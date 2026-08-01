"""Persistent fenced coordination for expensive permit lookups."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class IdempotencyConflictError(ValueError):
    pass


@dataclass(frozen=True)
class Claim:
    action: str
    request_id: str
    execution_token: str | None = None
    response: dict[str, Any] | None = None
    response_digest: str | None = None
    http_status: int | None = None


class LookupExecutionLedger:
    """SQLite-backed claim/complete/replay ledger with per-attempt fencing.

    The client request ID is correlation metadata only. Every executable claim
    receives a server-generated execution token; a stale worker cannot publish
    after a replacement has acquired a newer token.
    """

    def __init__(self, path: str | Path, *, claim_ttl_seconds: int = 180):
        self.path = str(path)
        self.claim_ttl_seconds = max(1, int(claim_ttl_seconds))
        self._local = threading.local()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @staticmethod
    def request_fingerprint(body: dict[str, Any]) -> str:
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def response_digest(response: dict[str, Any]) -> str:
        raw = json.dumps(response, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS lookup_executions (
                    owner_scope TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    execution_token TEXT,
                    state TEXT NOT NULL CHECK(state IN ('PROCESSING','COMPLETED','FAILED')),
                    claimed_at REAL NOT NULL,
                    completed_at REAL,
                    response_json TEXT,
                    response_digest TEXT,
                    http_status INTEGER,
                    attempt_count INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(owner_scope, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS served_decisions (
                    owner_scope TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    execution_token TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    response_digest TEXT NOT NULL,
                    http_status INTEGER NOT NULL,
                    completed_at REAL NOT NULL,
                    PRIMARY KEY(owner_scope, idempotency_key),
                    UNIQUE(execution_token)
                );
                """
            )
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(lookup_executions)")}
            if "execution_token" not in columns:
                conn.execute("ALTER TABLE lookup_executions ADD COLUMN execution_token TEXT")
            served_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(served_decisions)")}
            if "execution_token" not in served_columns:
                # Historical rows used a globally unique caller request ID. The
                # new column is populated only for fenced completions.
                conn.execute("ALTER TABLE served_decisions ADD COLUMN execution_token TEXT")

    def claim(
        self,
        idempotency_key: str,
        request_fingerprint: str,
        *,
        owner_scope: str = "anonymous",
        request_id: str,
        now: float | None = None,
    ) -> Claim:
        now = time.time() if now is None else float(now)
        key = str(idempotency_key or "").strip()
        if not key or len(key) > 200:
            raise ValueError("idempotency key must contain 1-200 characters")
        execution_token = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM lookup_executions WHERE owner_scope=? AND idempotency_key=?",
                (owner_scope, key),
            ).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO lookup_executions
                       (owner_scope,idempotency_key,request_fingerprint,request_id,execution_token,state,claimed_at)
                       VALUES (?,?,?,?,?,'PROCESSING',?)""",
                    (owner_scope, key, request_fingerprint, request_id, execution_token, now),
                )
                conn.commit()
                return Claim("execute", request_id, execution_token)
            if row["request_fingerprint"] != request_fingerprint:
                conn.rollback()
                raise IdempotencyConflictError("idempotency key was already used with a different request")
            if row["state"] == "COMPLETED":
                response = json.loads(row["response_json"])
                conn.commit()
                return Claim(
                    "replay", str(row["request_id"]), None, response,
                    str(row["response_digest"]), int(row["http_status"] or 200),
                )
            if row["state"] == "PROCESSING":
                age = now - float(row["claimed_at"])
                conn.commit()
                if age < self.claim_ttl_seconds:
                    return Claim("in_progress", str(row["request_id"]))
                # Never auto-reexecute an attempt whose external side effects are
                # unknown. A TTL lease cannot prove the research call did not run.
                return Claim("indeterminate", str(row["request_id"]))
            conn.execute(
                """UPDATE lookup_executions
                   SET request_id=?, execution_token=?, state='PROCESSING', claimed_at=?, completed_at=NULL,
                       response_json=NULL, response_digest=NULL, http_status=NULL,
                       attempt_count=attempt_count+1
                   WHERE owner_scope=? AND idempotency_key=?""",
                (request_id, execution_token, now, owner_scope, key),
            )
            conn.commit()
            return Claim("execute", request_id, execution_token)

    def complete(
        self,
        idempotency_key: str,
        request_fingerprint: str,
        response: dict[str, Any],
        *,
        owner_scope: str = "anonymous",
        request_id: str,
        execution_token: str,
        http_status: int = 200,
        now: float | None = None,
    ) -> str:
        now = time.time() if now is None else float(now)
        response_json = json.dumps(response, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(response_json.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM lookup_executions WHERE owner_scope=? AND idempotency_key=?",
                (owner_scope, idempotency_key),
            ).fetchone()
            if row is None or row["request_fingerprint"] != request_fingerprint:
                conn.rollback()
                raise IdempotencyConflictError("claim does not match request")
            if row["state"] == "COMPLETED":
                conn.commit()
                return str(row["response_digest"])
            if row["request_id"] != request_id or row["execution_token"] != execution_token:
                conn.rollback()
                raise IdempotencyConflictError("claim is owned by another execution")
            conn.execute(
                """UPDATE lookup_executions SET state='COMPLETED', completed_at=?,
                   response_json=?, response_digest=?, http_status=?
                   WHERE owner_scope=? AND idempotency_key=?""",
                (now, response_json, digest, int(http_status), owner_scope, idempotency_key),
            )
            served_columns = {str(item[1]) for item in conn.execute("PRAGMA table_info(served_decisions)")}
            if "request_id" in served_columns and "execution_token" in served_columns:
                conn.execute(
                    """INSERT OR REPLACE INTO served_decisions
                       (owner_scope,idempotency_key,request_id,execution_token,request_fingerprint,response_digest,http_status,completed_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (owner_scope, idempotency_key, execution_token, execution_token, request_fingerprint, digest, int(http_status), now),
                )
            else:
                conn.execute(
                    """INSERT OR REPLACE INTO served_decisions
                       (owner_scope,idempotency_key,execution_token,request_fingerprint,response_digest,http_status,completed_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (owner_scope, idempotency_key, execution_token, request_fingerprint, digest, int(http_status), now),
                )
            conn.commit()
        return digest

    def fail(
        self,
        idempotency_key: str,
        request_fingerprint: str,
        *,
        owner_scope: str = "anonymous",
        request_id: str,
        execution_token: str,
        now: float | None = None,
    ) -> None:
        now = time.time() if now is None else float(now)
        with self._connect() as conn:
            conn.execute(
                """UPDATE lookup_executions SET state='FAILED', completed_at=?
                   WHERE owner_scope=? AND idempotency_key=? AND request_fingerprint=?
                     AND request_id=? AND execution_token=? AND state='PROCESSING'""",
                (now, owner_scope, idempotency_key, request_fingerprint, request_id, execution_token),
            )

    def replay(self, idempotency_key: str, *, owner_scope: str = "anonymous") -> Claim | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT request_id,response_json,response_digest,http_status FROM lookup_executions
                   WHERE owner_scope=? AND idempotency_key=? AND state='COMPLETED'""",
                (owner_scope, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        return Claim(
            "replay", str(row["request_id"]), None, json.loads(row["response_json"]),
            str(row["response_digest"]), int(row["http_status"] or 200),
        )
