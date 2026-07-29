"""Deterministic RED contracts for the PermitAssist trust-boundary successor.

The immutable old candidate is imported from ``SOURCE_REPO``.  These tests use
only temporary sqlite databases and in-memory Handler instances; no listening
socket, provider, SMTP, or other network operation is permitted.

The contract deliberately treats every JSON-decoded dict as untrusted.  Binary
permit truth may cross a request only through a short-lived, keyed, server-held
snapshot whose bearer handle is transported out of band.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import importlib
import inspect
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest


SOURCE_REPO_SETTING = str(os.environ.get("PERMITASSIST_SOURCE_REPO") or "").strip()
EXPECTED_SOURCE_SHA = str(os.environ.get("PERMITASSIST_EXPECTED_SOURCE_SHA") or "").strip()
SOURCE_REPO = (
    Path(SOURCE_REPO_SETTING).resolve()
    if SOURCE_REPO_SETTING
    else Path(__file__).resolve().parents[1]
)
pytestmark = pytest.mark.skipif(
    not SOURCE_REPO_SETTING or not EXPECTED_SOURCE_SHA,
    reason=(
        "historical trust-successor RED contract requires explicit "
        "PERMITASSIST_SOURCE_REPO and PERMITASSIST_EXPECTED_SOURCE_SHA"
    ),
)
FIXED_SESSION_SIGNING_MATERIAL = (
    "test-only-permitassist-trust-successor-session-signing-material-v1"
)
OFFICIAL_URL = "https://example.test/official-permit-authority"

W4_FAMILIES = [
    "building",
    "electrical",
    "plumbing",
    "mechanical",
    "fire",
    "health",
    "liquor",
    "wastewater",
    "occupancy",
    "zoning",
]

BINARY_DECISIONS = {"REQUIRED", "NOT_REQUIRED"}
BINARY_VERDICTS = {"YES", "NO", "REQUIRED", "NOT_REQUIRED"}
FORBIDDEN_KEY_PARTS = {
    "private_envelope",
    "raw_result",
    "sealed_projection",
    "projection_handle",
    "projection_handles",
    "handoff_handle",
    "handoff_handles",
    "row_hmac",
    "payload_sha256",
    "envelope_sha256",
    "request_fingerprint_sha256",
    "permit_rule_engine_core",
    "trusted_state",
    "verified_projection",
    "proof",
}


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _walk(value: object, path: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield child_path, str(key), child
            yield from _walk(child, child_path)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def _families(result: dict) -> list[str]:
    rows = result.get("permits_required")
    if not isinstance(rows, list):
        return []
    found = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        family = (
            row.get("filing_family")
            or row.get("permit_family")
            or row.get("permit_kind")
            or row.get("category")
        )
        found.append(str(family or "").strip().lower())
    return found


def _assert_fail_closed(result: dict, *, require_w4: bool = False) -> None:
    """Collect all fail-closed defects into one useful assertion message."""
    defects: list[str] = []
    decision = str(result.get("permit_decision") or "").upper()
    verdict = str(result.get("permit_verdict") or "").upper()
    if decision in BINARY_DECISIONS:
        defects.append(f"untrusted permit_decision remained authoritative: {decision}")
    if result.get("permit_required") is not None:
        defects.append(
            f"untrusted permit_required remained binary: {result.get('permit_required')!r}"
        )
    if verdict in BINARY_VERDICTS:
        defects.append(f"untrusted permit_verdict remained authoritative: {verdict}")
    if require_w4:
        families = _families(result)
        if families != W4_FAMILIES:
            defects.append(f"W4 lanes changed: expected {W4_FAMILIES!r}, got {families!r}")
        rows = result.get("permits_required") or []
        binary_rows = [
            (index, row.get("required_status"), row.get("required"))
            for index, row in enumerate(rows)
            if isinstance(row, dict)
            and (
                str(row.get("required_status") or "").upper() in BINARY_DECISIONS
                or row.get("required") is not None
            )
        ]
        if binary_rows:
            defects.append(f"untrusted W4 lanes retained binary truth: {binary_rows!r}")
        actionable = any(
            isinstance(row, dict)
            and any(
                str(row.get(key) or "").strip()
                for key in ("next_step", "verification_step", "trigger", "description")
            )
            for row in rows
        )
        if not actionable:
            defects.append("W4 fail-closed lanes lost all actionable verification/contact text")
    assert not defects, "\n".join(defects)


def _assert_recursive_no_leak(value: object, *secret_values: str) -> None:
    serialized = _canonical(value).lower()
    leaked_paths = []
    for path, key, child in _walk(value):
        normalized = key.lower().lstrip("_")
        if key.startswith("_") or any(part in normalized for part in FORBIDDEN_KEY_PARTS):
            leaked_paths.append(path)
        if normalized.endswith(("_sha256", "_hmac", "_digest", "_fingerprint")):
            leaked_paths.append(path)
        if isinstance(child, str) and re.search(r"vp_[A-Za-z0-9_-]{32,96}", child):
            leaked_paths.append(path + "<handle-value>")
    for secret in secret_values:
        if secret and secret.lower() in serialized:
            leaked_paths.append(f"<serialized secret {secret[:10]!r}>")
    assert leaked_paths == [], f"recursive trust material leaked: {sorted(set(leaked_paths))}"


def _w4_public(decision: str = "REQUIRED") -> dict:
    decision = decision.upper()
    required = decision == "REQUIRED"
    rows = []
    for index, family in enumerate(W4_FAMILIES):
        status = decision if index == 0 else "VERIFY"
        rows.append(
            {
                "filing_family": family,
                "permit_kind": family,
                "permit_type": f"{family.title()} permit/review",
                "required_status": status,
                "required": required if index == 0 else None,
                "trigger": f"Verify the {family} lane with the issuing authority.",
                "next_step": f"Contact the authority before relying on the {family} lane.",
                "source_urls": [OFFICIAL_URL],
                "provenance": {
                    "decision_source": "submitted-source-looking-dto",
                    "source_url": OFFICIAL_URL,
                },
            }
        )
    return {
        "permit_decision": decision,
        "permit_required": required,
        "permit_verdict": "YES" if required else "NO",
        "permit_kind": "Commercial Tenant Improvement",
        "permit_type": "Commercial Tenant Improvement Permit",
        "decision_source": "official_source_evidence",
        "coverage_status": "source_backed_complete",
        "city": "Matanuska-Susitna Borough",
        "state": "AK",
        "permits_required": rows,
        "applying_office": "Example Official Permit Authority",
        "apply_url": OFFICIAL_URL,
        "source_urls": [OFFICIAL_URL],
        "sources": [
            {
                "title": "Official permit authority",
                "url": OFFICIAL_URL,
                "snippet": "An affirmative official-looking permit statement.",
            }
        ],
        "requirements": ["Confirm the complete filing packet with the authority."],
        "documents_needed": ["Scope narrative and plans."],
        "inspections": ["Confirm inspection stages with the authority."],
        "verification_tasks": ["Contact the official permit authority."],
    }


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setenv("CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("FREE_LOOKUP_DB", str(cache_dir / "ip_lookups.db"))
    monkeypatch.setenv("SESSION_SECRET", FIXED_SESSION_SIGNING_MATERIAL)
    monkeypatch.setenv("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    for path in (str(SOURCE_REPO), str(SOURCE_REPO / "api")):
        if path not in sys.path:
            sys.path.insert(0, path)
    module = importlib.import_module("api.server")
    module.DATA_DIR = str(cache_dir)
    module.CACHE_DB = str(cache_dir / "cache.db")
    module.EMAILS_CSV = str(cache_dir / "captured_emails.csv")
    module.FREE_LOOKUP_DB = str(cache_dir / "ip_lookups.db")
    module.SESSION_SECRET = FIXED_SESSION_SIGNING_MATERIAL
    module.init_db()

    def _network_forbidden(*_args, **_kwargs):
        raise AssertionError("network access is forbidden by this RED contract")

    # Provider/SMTP paths must be explicitly stubbed by a test that needs them.
    monkeypatch.setattr(module.requests, "request", _network_forbidden, raising=False)
    monkeypatch.setattr(module.requests, "get", _network_forbidden, raising=False)
    monkeypatch.setattr(module.requests, "post", _network_forbidden, raising=False)
    monkeypatch.setattr(module, "notify_telegram", lambda *_a, **_k: None)
    return module


def _core_case(monkeypatch: pytest.MonkeyPatch) -> dict:
    from api import permit_rule_engine as pre
    from api.v24_decision_cells import V24ResolutionStatus, resolve_v24_cell

    job_type = "residential reroof"
    job_category = "residential"
    city = "Buckeye"
    state = "AZ"
    resolution = resolve_v24_cell(city, state, job_type, job_category, force=True)
    assert resolution.status is V24ResolutionStatus.EXACT_CELL_PUBLISHABLE
    jurisdiction_id = str((resolution.cell or {}).get("jurisdiction_id") or "")
    assert jurisdiction_id
    monkeypatch.setenv(pre.CORE_SETTING, "active")
    monkeypatch.setenv(pre.CORE_ALLOWLIST_SETTING, jurisdiction_id)
    envelope = pre.build_core_decision_envelope(
        resolution,
        job_type=job_type,
        city=city,
        state=state,
        job_category=job_category,
    )
    wrapped = pre.attach_core_decision_envelope(
        {"permit_decision": "UNKNOWN", "permit_required": None}, envelope
    )
    return {
        "job_type": job_type,
        "job_category": job_category,
        "city": city,
        "state": state,
        "wrapped": wrapped,
        "wire": json.loads(envelope.sealed_projection.payload_json),
    }


def _issue_snapshot(server, customer: dict, *, owner_scope: str = "") -> str:
    """Use the successor name when present, retaining the old API as the RED baseline."""
    if hasattr(server, "create_customer_snapshot_handoff"):
        return server.create_customer_snapshot_handoff(
            customer,
            "commercial tenant improvement",
            "Matanuska-Susitna Borough",
            "AK",
            job_category="commercial",
            owner_scope=owner_scope,
        )
    return server.create_verified_projection_handoff(
        customer,
        customer,
        "commercial tenant improvement",
        "Matanuska-Susitna Borough",
        "AK",
        job_category="commercial",
        owner_scope=owner_scope,
    )


def _consume_snapshot(
    server,
    handle: str,
    customer: dict,
    *,
    owner_scope: str = "",
    job_type: str = "commercial tenant improvement",
    city: str = "Matanuska-Susitna Borough",
    state: str = "AK",
):
    function = getattr(
        server,
        "consume_customer_snapshot_handoff",
        server.consume_verified_projection_handoff,
    )
    return function(
        handle,
        customer,
        job_type,
        city,
        state,
        owner_scope=owner_scope,
    )


def _handoff_table(server) -> tuple[str, list[str]]:
    with sqlite3.connect(server.CACHE_DB) as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        candidates = []
        for table in tables:
            columns = [
                row[1]
                for row in connection.execute(f'PRAGMA table_info("{table}")')
            ]
            if "handle_sha256" in columns and any("hmac" in col for col in columns):
                candidates.append((table, columns))
    assert len(candidates) == 1, f"expected one generic snapshot table, found {candidates!r}"
    return candidates[0]


def _row_for_handle(server, handle: str) -> tuple[str, list[str], dict[str, Any]]:
    table, columns = _handoff_table(server)
    digest = hashlib.sha256(handle.encode()).hexdigest()
    with sqlite3.connect(server.CACHE_DB) as connection:
        raw = connection.execute(
            f'SELECT * FROM "{table}" WHERE handle_sha256=?', [digest]
        ).fetchone()
    assert raw is not None, "issued snapshot row is missing"
    return table, columns, dict(zip(columns, raw))


def _invoke_post(server, path: str, payload: dict, headers: dict[str, str] | None = None):
    """Invoke the real request handler without opening a network socket."""
    body = json.dumps(payload).encode("utf-8")
    handler = object.__new__(server.Handler)
    handler.path = path
    handler.command = "POST"
    handler.request_version = "HTTP/1.1"
    handler.client_address = ("127.0.0.1", 10000)
    handler.headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "Host": "127.0.0.1",
        **(headers or {}),
    }
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    response: dict[str, Any] = {"status": None, "headers": []}
    handler.send_response = lambda status, *_a, **_k: response.__setitem__(
        "status", status
    )
    handler.send_header = lambda key, value: response["headers"].append((key, value))
    handler.end_headers = lambda: None
    handler.do_POST()
    raw = handler.wfile.getvalue()
    content_type = next(
        (value for key, value in response["headers"] if key.lower() == "content-type"),
        "",
    )
    if "application/json" in content_type:
        response["json"] = json.loads(raw.decode("utf-8"))
    else:
        response["text"] = raw.decode("utf-8")
    return response


def _stored_share_result(server, slug: str) -> dict:
    share = server.get_share(slug)
    assert share is not None
    result = share.get("data")
    assert isinstance(result, dict)
    return result


def _checklist_result_projection(checklist: dict) -> dict:
    """Normalize checklist rows into the decision shape used by fail-closed checks."""
    items = checklist.get("items") if isinstance(checklist, dict) else []
    rows = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        required = item.get("required")
        rows.append(
            {
                "filing_family": item.get("category") or item.get("filing_family"),
                "required": required,
                "required_status": (
                    "REQUIRED" if required is True else "NOT_REQUIRED" if required is False else "VERIFY"
                ),
                "description": item.get("description") or item.get("title") or item.get("task"),
            }
        )
    return {
        "permit_decision": checklist.get("permit_decision"),
        "permit_required": checklist.get("permit_required"),
        "permit_verdict": checklist.get("permit_verdict"),
        "permits_required": rows,
    }


# ---------------------------------------------------------------------------
# Untrusted JSON can never establish authority.


def test_source_repository_is_the_frozen_old_candidate():
    head = subprocess.run(
        ["git", "-C", str(SOURCE_REPO), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == EXPECTED_SOURCE_SHA


def test_submitted_self_consistent_internal_envelope_never_authenticates(
    server, monkeypatch
):
    case = _core_case(monkeypatch)
    submitted = json.loads(json.dumps(case["wrapped"]))
    assert submitted["_permit_rule_engine_core"]["envelope_sha256"]
    assert submitted["_permit_rule_engine_core"]["sealed_projection"]["payload_sha256"]

    public = server.build_customer_response_egress(
        submitted,
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    )
    _assert_fail_closed(public)


@pytest.mark.parametrize("forged_decision", ["REQUIRED", "NOT_REQUIRED"])
def test_complete_submitted_public_dto_never_authenticates(server, forged_decision):
    forged = _w4_public(forged_decision)
    external = json.loads(json.dumps(forged))
    public = server.build_customer_response_egress(
        external,
        "commercial tenant improvement",
        "Matanuska-Susitna Borough",
        "AK",
        job_category="commercial",
    )
    _assert_fail_closed(public, require_w4=True)


# ---------------------------------------------------------------------------
# One generic keyed server-held snapshot, not a core-only exception.


@pytest.mark.parametrize("decision", ["REQUIRED", "NOT_REQUIRED"])
def test_generic_snapshot_preserves_server_generated_legacy_binary_truth(server, decision):
    customer = _w4_public(decision)
    handle = _issue_snapshot(server, customer)
    assert handle, "generic snapshot issuance rejected a non-core server result"
    verified = _consume_snapshot(server, handle, json.loads(json.dumps(customer)))
    assert verified is not None, "valid generic snapshot did not authenticate"
    assert verified["permit_decision"] == decision
    assert verified["permit_required"] is (decision == "REQUIRED")
    assert _families(verified) == W4_FAMILIES


def test_snapshot_handle_is_256_bit_opaque_and_only_its_hash_is_stored(server):
    customer = _w4_public()
    handle = _issue_snapshot(server, customer)
    assert re.fullmatch(r"vp_[A-Za-z0-9_-]{43}", handle), (
        "handle must be a vp_ prefix plus a 256-bit base64url token"
    )
    decoded = base64.urlsafe_b64decode(handle[3:] + "=")
    assert len(decoded) == 32
    _table, _columns, row = _row_for_handle(server, handle)
    assert row["handle_sha256"] == hashlib.sha256(handle.encode()).hexdigest()
    assert handle not in _canonical(row)
    with sqlite3.connect(server.CACHE_DB) as connection:
        dump = "\n".join(connection.iterdump())
    assert handle not in dump


def test_snapshot_row_is_keyed_and_covers_output_request_owner_ttl_and_counter(server):
    customer = _w4_public()
    owner = server._projection_owner_scope("owner@example.test")
    handle = _issue_snapshot(server, customer, owner_scope=owner)
    _table, columns, row = _row_for_handle(server, handle)
    joined = " ".join(columns).lower()
    for required in (
        "handle_sha256",
        "customer",
        "request_identity",
        "owner_scope",
        "created_at",
        "expires_at",
        "max_uses",
        "use_count",
        "hmac",
    ):
        assert required in joined, f"snapshot row omits authenticated {required!r} state"
    customer_column = next(column for column in columns if "customer" in column and "json" in column)
    assert json.loads(row[customer_column]) == customer
    hmac_column = next(column for column in columns if "hmac" in column)
    assert re.fullmatch(r"[0-9a-f]{64}", str(row[hmac_column]))
    assert str(row[hmac_column]) != hashlib.sha256(_canonical(customer).encode()).hexdigest()


def test_snapshot_exact_dto_request_and_owner_binding(server):
    customer = _w4_public()
    owner_a = server._projection_owner_scope("owner-a@example.test")
    owner_b = server._projection_owner_scope("owner-b@example.test")
    handle = _issue_snapshot(server, customer, owner_scope=owner_a)
    substituted = copy.deepcopy(customer)
    substituted["requirements"].append("attacker substitution")
    assert _consume_snapshot(server, handle, substituted, owner_scope=owner_a) is None
    assert _consume_snapshot(server, handle, customer, owner_scope=owner_b) is None
    assert _consume_snapshot(server, handle, customer, owner_scope=owner_a, city="Anchorage") is None
    assert _consume_snapshot(server, handle, customer, owner_scope=owner_a) is not None


def test_snapshot_ttl_expiry_deletes_row(server, monkeypatch):
    clock = {"now": datetime(2035, 1, 1, tzinfo=timezone.utc)}
    monkeypatch.setattr(server, "utc_now", lambda: clock["now"])
    monkeypatch.setattr(server, "VERIFIED_PROJECTION_HANDOFF_TTL_SECONDS", 60)
    customer = _w4_public()
    handle = _issue_snapshot(server, customer)
    assert handle, "generic snapshot issuance rejected a server-generated result"
    clock["now"] += timedelta(seconds=61)
    assert _consume_snapshot(server, handle, customer) is None
    table, _columns = _handoff_table(server)
    digest = hashlib.sha256(handle.encode()).hexdigest()
    with sqlite3.connect(server.CACHE_DB) as connection:
        count = connection.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE handle_sha256=?', [digest]
        ).fetchone()[0]
    assert count == 0, "expired snapshot row was not cleaned up"


def test_snapshot_max_use_is_atomic_and_replay_safe(server, monkeypatch):
    monkeypatch.setattr(server, "VERIFIED_PROJECTION_HANDOFF_MAX_USES", 2)
    customer = _w4_public()
    handle = _issue_snapshot(server, customer)
    assert _consume_snapshot(server, handle, customer) is not None
    assert _consume_snapshot(server, handle, customer) is not None
    assert _consume_snapshot(server, handle, customer) is None
    _table, _columns, row = _row_for_handle(server, handle)
    assert int(row["use_count"]) == 2


def test_snapshot_row_hmac_detects_counter_and_payload_tamper(server):
    customer = _w4_public()
    handle = _issue_snapshot(server, customer)
    table, columns, row = _row_for_handle(server, handle)
    customer_column = next(column for column in columns if "customer" in column and "json" in column)
    with sqlite3.connect(server.CACHE_DB) as connection:
        connection.execute(
            f'UPDATE "{table}" SET use_count=use_count+1 WHERE handle_sha256=?',
            [row["handle_sha256"]],
        )
    assert _consume_snapshot(server, handle, customer) is None

    second = _issue_snapshot(server, customer)
    table, columns, row = _row_for_handle(server, second)
    corrupted = copy.deepcopy(customer)
    corrupted["permit_decision"] = "NOT_REQUIRED"
    with sqlite3.connect(server.CACHE_DB) as connection:
        connection.execute(
            f'UPDATE "{table}" SET "{customer_column}"=? WHERE handle_sha256=?',
            [_canonical(corrupted), row["handle_sha256"]],
        )
    assert _consume_snapshot(server, second, corrupted) is None


def test_recomputed_unkeyed_projection_hash_cannot_bless_tamper_and_no_private_state_persists(
    server, monkeypatch
):
    case = _core_case(monkeypatch)
    public = server.build_customer_response_egress(
        case["wrapped"],
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    )
    handle = server.create_verified_projection_handoff(
        case["wrapped"],
        public,
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["job_category"],
    )
    assert handle
    table, columns, row = _row_for_handle(server, handle)
    lowered_columns = " ".join(columns).lower()
    for forbidden in ("private", "raw_result", "core_envelope", "sealed_projection"):
        assert forbidden not in lowered_columns, (
            f"snapshot schema persisted forbidden private/core state: {forbidden}"
        )
    customer_column = next(
        column for column in columns if "customer" in column and "json" in column
    )
    customer_hash_column = next(
        column for column in columns if "customer" in column and "sha256" in column
    )
    stored_customer = json.loads(row[customer_column])
    _assert_recursive_no_leak(stored_customer)

    corrupted = copy.deepcopy(stored_customer)
    corrupted["permit_decision"] = (
        "NOT_REQUIRED"
        if str(stored_customer.get("permit_decision") or "").upper() == "REQUIRED"
        else "REQUIRED"
    )
    corrupted_json = _canonical(corrupted)
    recomputed_unkeyed_hash = hashlib.sha256(corrupted_json.encode()).hexdigest()
    with sqlite3.connect(server.CACHE_DB) as connection:
        connection.execute(
            f'UPDATE "{table}" SET "{customer_column}"=?, '
            f'"{customer_hash_column}"=? WHERE handle_sha256=?',
            [corrupted_json, recomputed_unkeyed_hash, row["handle_sha256"]],
        )
    assert server.consume_verified_projection_handoff(
        handle, corrupted, case["job_type"], case["city"], case["state"]
    ) is None


def test_snapshot_survives_restart_with_same_secret_and_database(server, monkeypatch):
    customer = _w4_public()
    handle = _issue_snapshot(server, customer)
    cache_db = server.CACHE_DB
    data_dir = server.DATA_DIR
    restarted = importlib.reload(server)
    restarted.CACHE_DB = cache_db
    restarted.DATA_DIR = data_dir
    restarted.SESSION_SECRET = FIXED_SESSION_SIGNING_MATERIAL
    restarted.init_db()
    assert _consume_snapshot(restarted, handle, customer) is not None


def test_snapshot_creation_cleans_unconsumed_expired_rows(server, monkeypatch):
    clock = {"now": datetime(2035, 1, 1, tzinfo=timezone.utc)}
    monkeypatch.setattr(server, "utc_now", lambda: clock["now"])
    monkeypatch.setattr(server, "VERIFIED_PROJECTION_HANDOFF_TTL_SECONDS", 60)
    customer = _w4_public()
    expired_handle = _issue_snapshot(server, customer)
    clock["now"] += timedelta(seconds=61)
    live_handle = _issue_snapshot(server, customer)
    table, _columns = _handoff_table(server)
    with sqlite3.connect(server.CACHE_DB) as connection:
        hashes = {
            row[0]
            for row in connection.execute(f'SELECT handle_sha256 FROM "{table}"')
        }
    assert hashlib.sha256(expired_handle.encode()).hexdigest() not in hashes
    assert hashlib.sha256(live_handle.encode()).hexdigest() in hashes


# ---------------------------------------------------------------------------
# Every actual submitted-result ingress fails closed without a valid snapshot.


def test_share_ingress_fails_closed_and_preserves_w4(server):
    forged = _w4_public("REQUIRED")
    response = _invoke_post(
        server,
        "/api/share",
        {
            "job_type": "commercial tenant improvement",
            "city": "Matanuska-Susitna Borough",
            "state": "AK",
            "result": forged,
        },
    )
    assert response["status"] == 200, response
    stored = _stored_share_result(server, response["json"]["slug"])
    _assert_fail_closed(stored, require_w4=True)
    _assert_recursive_no_leak(stored)


def test_checklist_ingress_fails_closed_and_preserves_w4(server):
    response = _invoke_post(
        server,
        "/api/checklist",
        {
            "job_type": "commercial tenant improvement",
            "job_category": "commercial",
            "city": "Matanuska-Susitna Borough",
            "state": "AK",
            "result": _w4_public("REQUIRED"),
        },
    )
    assert response["status"] == 200, response
    projected = _checklist_result_projection(response["json"])
    _assert_fail_closed(projected, require_w4=True)
    _assert_recursive_no_leak(response["json"])


def test_white_label_export_ingress_fails_closed(server, monkeypatch):
    monkeypatch.setattr(
        server,
        "validate_session_token",
        lambda token: "owner@example.test" if token == "session" else None,
    )
    response = _invoke_post(
        server,
        "/api/white-label-report",
        {
            "job_type": "commercial tenant improvement",
            "city": "Matanuska-Susitna Borough",
            "state": "AK",
            "result": _w4_public("REQUIRED"),
        },
        {"X-Session-Token": "session"},
    )
    assert response["status"] == 200, response
    text = response["text"]
    assert not re.search(r"permit decision.{0,80}(?:required|yes)", text, re.I | re.S)
    assert "verify" in text.lower() or "contact" in text.lower()
    for family in W4_FAMILIES:
        assert family in text.lower(), f"white-label fail-closed report lost {family} lane"
    _assert_recursive_no_leak({"html": text})


def test_email_export_ingress_fails_closed(server, monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(server, "save_email_capture", lambda *_a, **_k: None)

    def capture(_email, _job, _city, _state, result):
        captured.append(copy.deepcopy(result))
        return True

    monkeypatch.setattr(server, "send_email_report", capture)
    response = _invoke_post(
        server,
        "/api/email-report",
        {
            "email": "recipient@example.test",
            "job": "commercial tenant improvement",
            "city": "Matanuska-Susitna Borough",
            "state": "AK",
            "data": _w4_public("NOT_REQUIRED"),
        },
    )
    assert response["status"] == 200, response
    assert captured, "email path did not expose its final in-process report payload"
    _assert_fail_closed(captured[-1], require_w4=True)
    _assert_recursive_no_leak(captured[-1])


def test_saved_job_result_ingress_fails_closed_and_is_owner_scoped(server, monkeypatch):
    monkeypatch.setattr(
        server,
        "validate_session_token",
        lambda token: "owner@example.test" if token == "session" else None,
    )
    monkeypatch.setattr(server, "get_team_scope_emails", lambda email: [email])
    response = _invoke_post(
        server,
        "/api/jobs",
        {
            "job_name": "Untrusted saved result",
            "city": "Matanuska-Susitna Borough",
            "state": "AK",
            "job_type": "commercial tenant improvement",
            "result_json": _w4_public("REQUIRED"),
        },
        {"X-Session-Token": "session"},
    )
    assert response["status"] == 201, response
    jobs = server.list_jobs("owner@example.test")
    assert len(jobs) == 1
    stored = jobs[0]["result_json"]
    assert isinstance(stored, dict)
    _assert_fail_closed(stored, require_w4=True)
    _assert_recursive_no_leak(stored)


def test_result_ingress_inventory_is_complete_and_explicit():
    source = (SOURCE_REPO / "api" / "server.py").read_text(encoding="utf-8")
    expected = {
        "/api/share": 'data.get("result", {})',
        "/api/checklist": 'data.get("result") or {}',
        "/api/white-label-report": 'data.get("result")',
        "/api/email-report": 'data.get("data", {})',
        "/api/jobs": 'data.get("result_json")',
    }
    for route, expression in expected.items():
        assert route in source, f"expected real result ingress disappeared: {route}"
        assert expression in source, f"inventory expression drifted for {route}: {expression}"
    matches = re.findall(
        r'data\.get\(["\'](?:result|data|result_json)["\'][^\n]*', source
    )
    handler_matches = [match for match in matches if "line_items" not in match]
    assert len(handler_matches) == 6, (
        "new/removed result-bearing ingress requires an explicit trust contract: "
        f"{handler_matches!r}"
    )


# ---------------------------------------------------------------------------
# Valid generic continuity, result-producing classes, W4, and recursive leaks.


def test_valid_generic_snapshot_continues_through_share_report_and_checklist(server):
    customer = _w4_public("REQUIRED")
    handle = _issue_snapshot(server, customer)
    verified = _consume_snapshot(server, handle, customer)
    assert verified is not None
    slug = server.create_share(
        "commercial tenant improvement",
        "Matanuska-Susitna Borough",
        "AK",
        verified,
    )
    stored = _stored_share_result(server, slug)
    assert stored["permit_decision"] == "REQUIRED"
    assert _families(stored) == W4_FAMILIES
    report_html = server.render_share_page(server.get_share(slug))
    assert '"permit_decision":"REQUIRED"' in report_html
    checklist = server.get_or_create_checklist(
        verified,
        "commercial tenant improvement",
        "Matanuska-Susitna Borough",
        "AK",
    )
    projected = _checklist_result_projection(checklist)
    assert any(row.get("required") is True for row in projected["permits_required"])
    assert _families(projected) == W4_FAMILIES
    _assert_recursive_no_leak(
        {"stored": stored, "report_html": report_html, "checklist": checklist}, handle
    )


def test_ordinary_batch_and_versioned_result_classes_issue_out_of_band_handoffs():
    source = inspect.cleandoc(
        (SOURCE_REPO / "api" / "server.py").read_text(encoding="utf-8")
    )
    route_windows = {}
    for route, next_route in (
        ("/api/permit", "/api/batch-permit"),
        ("/api/batch-permit", "/api/beta-event"),
        ("/api/v1/permit", "/api/email-report"),
    ):
        start = source.index(f'path == "{route}"')
        end = source.index(f'path == "{next_route}"', start)
        route_windows[route] = source[start:end]
    for route, window in route_windows.items():
        assert "create_verified_projection_handoff(" in window or "create_customer_snapshot_handoff(" in window, (
            f"{route} does not issue the generic snapshot"
        )
        assert (
            "_projection_handoff_response_headers(" in window
            or "_customer_snapshot_response_headers(" in window
            or "VERIFIED_PROJECTION_HANDLES_HEADER" in window
        ), (
            f"{route} does not transport its handle out of band"
        )
        assert 'self.send_json(200, {"projection_handle"' not in window
        assert 'self.send_json(200, {"handoff_handle"' not in window


@pytest.mark.parametrize("length", [32, 43, 96])
def test_every_consumer_accepted_handle_length_is_recursively_scrubbed(server, length):
    handle = "vp_" + "A" * length
    nested = {
        "plain": handle,
        "embedded": f"before:{handle}:after",
        "list": [{"deep": handle}],
    }
    redacted = server.project_customer_response_egress(nested)
    redacted = server.redact_public_output(redacted)
    text = server._redact_sensitive_text(_canonical(redacted))
    assert handle not in _canonical(redacted)
    assert handle not in text


def test_private_proof_cannot_survive_json_round_trip(server):
    customer = _w4_public("REQUIRED")
    handle = _issue_snapshot(server, customer)
    proof = _consume_snapshot(server, handle, customer)
    assert proof is not None
    wire = json.loads(json.dumps(proof))
    assert type(wire) is dict
    _assert_recursive_no_leak(wire, handle)
    assert _consume_snapshot(server, handle, wire) is not None
    # The plain dict itself is never authority when presented without the handle.
    public = server.build_customer_response_egress(
        wire,
        "commercial tenant improvement",
        "Matanuska-Susitna Borough",
        "AK",
        job_category="commercial",
    )
    _assert_fail_closed(public, require_w4=True)


def test_outbound_webhook_uses_server_owned_egress_and_leaks_no_internal_state(
    server, monkeypatch
):
    case = _core_case(monkeypatch)
    delivered = server.threading.Event()
    posts = []
    job_type = "commercial tenant improvement"
    city = "Matanuska-Susitna Borough"
    state = "AK"
    expected = server.build_customer_response_egress(
        server._mark_server_owned_result(copy.deepcopy(case["wrapped"])),
        job_type,
        city,
        state,
    )
    observed = []
    real_egress = server.build_customer_response_egress

    def traced_egress(value, *args, **kwargs):
        observed.append(value)
        return real_egress(value, *args, **kwargs)

    monkeypatch.setattr(server, "build_customer_response_egress", traced_egress)

    monkeypatch.setattr(
        server,
        "research_permit",
        lambda *args, **kwargs: copy.deepcopy(case["wrapped"]),
    )
    monkeypatch.setattr(
        server, "validate_webhook_callback_url", lambda value: str(value)
    )

    def fake_post(url, **kwargs):
        posts.append((url, kwargs))
        delivered.set()
        return object()

    monkeypatch.setattr(server.requests, "post", fake_post)
    integration = {
        "integration_key": "wh_test_only_successor_contract",
        "name": "Contract webhook",
        "callback_url": "https://customer.example.test/hook",
        "field_mapping": {},
    }
    server.run_webhook_lookup_async(
        integration,
        {
            "job_type": job_type,
            "city": city,
            "state": state,
        },
    )
    assert delivered.wait(5), "webhook worker did not deliver"
    body = json.loads(posts[0][1]["data"])
    assert len(observed) == 1
    assert observed[0].has_intact_server_owned_payload()
    assert body["result"] == expected
    _assert_recursive_no_leak(body)


def test_city_watch_projects_each_server_owned_current_request(server, monkeypatch):
    responses = [
        {
            "permit_decision": "REQUIRED",
            "permit_required": True,
            "permits_required": [
                {"permit_type": "Old Permit", "required": True}
            ],
            "fee_range": "$100",
            "apply_url": "https://city.example.test/old",
            "debug_trace": "PRIVATE_OLD_TRACE",
        },
        {
            "permit_decision": "REQUIRED",
            "permit_required": True,
            "permits_required": [
                {"permit_type": "New Permit", "required": True}
            ],
            "fee_range": "$200",
            "apply_url": "https://city.example.test/new",
            "debug_trace": "PRIVATE_NEW_TRACE",
        },
    ]
    observed = []
    sent = []
    real_egress = server.build_customer_response_egress

    def traced_egress(value, *args, **kwargs):
        observed.append(value)
        return real_egress(value, *args, **kwargs)

    monkeypatch.setattr(server, "build_customer_response_egress", traced_egress)
    monkeypatch.setattr(
        server, "research_permit", lambda *args, **kwargs: responses.pop(0)
    )
    monkeypatch.setattr(
        server,
        "resend_send",
        lambda *args, **kwargs: sent.append((args, kwargs)) or True,
    )
    server.create_city_watch(
        "watch@example.test", "Austin", "TX", "restaurant tenant improvement"
    )
    changed = server.check_city_changes(
        "watch@example.test", "Austin", "TX", "restaurant tenant improvement"
    )
    assert len(observed) == 2
    assert all(isinstance(value, server._ServerOwnedLegacyResult) for value in observed)
    assert changed["digest"]["required_permits"] == ["New Permit"]
    assert changed["digest"]["fee_range"] == "$200"
    serialized = _canonical({"changed": changed, "sent": sent})
    assert "PRIVATE_OLD_TRACE" not in serialized
    assert "PRIVATE_NEW_TRACE" not in serialized
    _assert_recursive_no_leak({"changed": changed, "sent": sent})


def test_direct_renderers_default_deny_plain_result_dictionaries(server, monkeypatch):
    raw = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "customer_headline": "UNTRUSTED BINARY AUTHORITY",
        "permits_required": [
            {"permit_type": "Untrusted Building Permit", "required": True}
        ],
        "_permit_rule_engine_core": {"private": "PRIVATE_RENDERER_ENVELOPE"},
    }
    html = server.render_white_label_report_html(
        {
            "result": raw,
            "job_type": "commercial tenant improvement",
            "city": "Austin",
            "state": "TX",
        }
    )
    sent = []
    monkeypatch.setattr(
        server,
        "resend_send",
        lambda *args, **kwargs: sent.append((args, kwargs)) or True,
    )
    assert server.send_email_report(
        "customer@example.test",
        "commercial tenant improvement",
        "Austin",
        "TX",
        raw,
    )
    serialized = _canonical({"html": html, "email": sent})
    assert "UNTRUSTED BINARY AUTHORITY" not in serialized
    assert "PRIVATE_RENDERER_ENVELOPE" not in serialized
    assert "[YES]" not in serialized
    assert ">YES<" not in serialized
    _assert_recursive_no_leak({"html": html, "email": sent})


def test_https_forwarded_scheme_forces_secure_cookie_even_with_loopback_host(server):
    handle = "vp_" + "A" * 43
    secure = server._projection_handoff_response_headers(
        handle,
        {"Host": "localhost:8080", "X-Forwarded-Proto": "https"},
    )["Set-Cookie"]
    assert "HttpOnly" in secure
    assert "SameSite=Strict" in secure
    assert "Secure" in secure

    loopback_http = server._projection_handoff_response_headers(
        handle,
        {"Host": "127.0.0.1:8000"},
    )["Set-Cookie"]
    assert "Secure" not in loopback_http


def test_hmac_tampered_snapshot_row_is_deleted_immediately(server):
    customer = _w4_public("REQUIRED")
    handle = _issue_snapshot(server, customer)
    handle_sha256 = hashlib.sha256(handle.encode("utf-8")).hexdigest()
    with sqlite3.connect(server.CACHE_DB) as conn:
        conn.execute(
            "UPDATE verified_projection_handoffs SET row_hmac_sha256=? "
            "WHERE handle_sha256=?",
            ["0" * 64, handle_sha256],
        )
    assert _consume_snapshot(server, handle, customer) is None
    with sqlite3.connect(server.CACHE_DB) as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM verified_projection_handoffs "
            "WHERE handle_sha256=?",
            [handle_sha256],
        ).fetchone()[0]
    assert remaining == 0
