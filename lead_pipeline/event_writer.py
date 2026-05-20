"""Deterministic stdlib-only SQLite event writer for Phase 1 M4.

The M4 writer persists an ``M3`` :class:`ConnectorRunResult` into the
``M1`` fixture SQLite schema. It enforces Phase 1 safety again at the
*write* boundary — not just at the connector boundary — so that any
drifted, blocked, or non-fixture-shaped payload fails closed before any
row reaches the database.

The writer is intentionally limited to four tables:

  * ``batches``
  * ``sources``
  * ``cost_events``
  * ``source_observations``

It never performs UPDATE or DELETE; duplicate deterministic event IDs
are reported in the returned :class:`WriteSummary` without mutating
existing rows. The module performs no network, DNS, SMTP, scraping,
paid-provider, or outreach action.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .adapters import AdapterPolicyError, enforce_adapter_policy_for_connector
from .connectors import (
    FIXTURE_URL_PREFIX,
    ConnectorRunResult,
    FetchMode,
    UnknownConnectorError,
)
from .schema import PHASE1_SCHEMA_VERSION, create_sqlite_schema, get_table_contract

PERSISTENCE_VERSION = "lead_pipeline_phase1_m4_event_writer_v1"
PHASE1_ALLOWED_PHASE = "phase1_fixture_only"


class PersistenceSafetyError(RuntimeError):
    """Raised when an M4 writer call would violate Phase 1 safety guarantees."""


@dataclass(frozen=True)
class WriteSummary:
    """Deterministic per-call summary of inserted vs. duplicate rows."""

    batch_id: str
    connector_run_id: str
    batches_inserted: int
    sources_inserted: int
    cost_events_inserted: int
    observations_inserted: int
    batches_duplicate: int
    sources_duplicate: int
    cost_events_duplicate: int
    observations_duplicate: int


def initialize_sqlite_schema(conn: sqlite3.Connection) -> None:
    """Apply the M1 fixture DDL with foreign keys enabled on ``conn``.

    ``PRAGMA foreign_keys = ON`` is issued outside the schema script so
    SQLite enforces FK constraints at insert time (the DDL string itself
    contains a PRAGMA, but PRAGMA inside ``executescript`` does not
    reliably persist on every SQLite build).
    """

    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(create_sqlite_schema())


def _check_result_safety(result: ConnectorRunResult) -> None:
    if result.network_used:
        raise PersistenceSafetyError(
            f"refusing to persist connector run {result.connector_run_id!r}: "
            "network_used must be False in Phase 1"
        )
    if result.send_authorized:
        raise PersistenceSafetyError(
            f"refusing to persist connector run {result.connector_run_id!r}: "
            "send_authorized must be False in Phase 1"
        )
    if result.mode != FetchMode.FIXTURE_ONLY.value:
        raise PersistenceSafetyError(
            f"refusing to persist connector run {result.connector_run_id!r}: "
            f"mode must be {FetchMode.FIXTURE_ONLY.value!r}; got {result.mode!r}"
        )

    try:
        enforce_adapter_policy_for_connector(result.connector_id)
    except (AdapterPolicyError, UnknownConnectorError) as exc:
        raise PersistenceSafetyError(
            f"refusing to persist connector run {result.connector_run_id!r}: {exc}"
        ) from exc


def _check_source_safety(source: Mapping[str, Any]) -> None:
    if source.get("schema_version") != PHASE1_SCHEMA_VERSION:
        raise PersistenceSafetyError(
            f"source schema_version mismatch on {source.get('source_id')!r}: "
            f"got {source.get('schema_version')!r}, expected {PHASE1_SCHEMA_VERSION!r}"
        )
    base_url = str(source.get("base_url_or_path") or "")
    if not base_url.startswith(FIXTURE_URL_PREFIX):
        raise PersistenceSafetyError(
            f"source {source.get('source_id')!r} base_url_or_path must use "
            f"{FIXTURE_URL_PREFIX!r} prefix; got {base_url!r}"
        )
    if source.get("allowed_phase") != PHASE1_ALLOWED_PHASE:
        raise PersistenceSafetyError(
            f"source {source.get('source_id')!r} allowed_phase must be "
            f"{PHASE1_ALLOWED_PHASE!r}; got {source.get('allowed_phase')!r}"
        )
    if int(source.get("paid_flag") or 0) != 0:
        raise PersistenceSafetyError(
            f"refusing to persist paid source {source.get('source_id')!r} in Phase 1"
        )
    if int(source.get("requires_login") or 0) != 0:
        raise PersistenceSafetyError(
            f"refusing to persist login-required source {source.get('source_id')!r} in Phase 1"
        )


def _check_observation_safety(obs: Mapping[str, Any]) -> None:
    if obs.get("schema_version") != PHASE1_SCHEMA_VERSION:
        raise PersistenceSafetyError(
            f"observation {obs.get('observation_id')!r} schema_version mismatch: "
            f"got {obs.get('schema_version')!r}, expected {PHASE1_SCHEMA_VERSION!r}"
        )
    url = str(obs.get("url_or_path") or "")
    if not url.startswith(FIXTURE_URL_PREFIX):
        raise PersistenceSafetyError(
            f"observation {obs.get('observation_id')!r} url_or_path must use "
            f"{FIXTURE_URL_PREFIX!r} prefix; got {url!r}"
        )


def _check_cost_event_safety(cost: Mapping[str, Any]) -> None:
    if cost.get("schema_version") != PHASE1_SCHEMA_VERSION:
        raise PersistenceSafetyError(
            f"cost_event {cost.get('cost_event_id')!r} schema_version mismatch: "
            f"got {cost.get('schema_version')!r}, expected {PHASE1_SCHEMA_VERSION!r}"
        )


def _derive_result_batch_id(result: ConnectorRunResult) -> str:
    batch_ids = {obs.get("batch_id") for obs in result.observations}
    batch_ids.update(cost.get("batch_id") for cost in result.cost_events)
    if len(batch_ids) != 1 or None in batch_ids:
        raise PersistenceSafetyError(
            f"connector run {result.connector_run_id!r} has inconsistent batch_id "
            f"across observations/cost_events: {sorted(str(b) for b in batch_ids)}"
        )
    return next(iter(batch_ids))


def _check_batch_safety(batch: Mapping[str, Any], result_batch_id: str) -> None:
    if batch.get("batch_id") != result_batch_id:
        raise PersistenceSafetyError(
            f"batch_id mismatch: batch payload {batch.get('batch_id')!r} does not "
            f"match connector run batch_id {result_batch_id!r}"
        )
    schema_version = batch.get("schema_version", PHASE1_SCHEMA_VERSION)
    if schema_version != PHASE1_SCHEMA_VERSION:
        raise PersistenceSafetyError(
            f"batch {batch.get('batch_id')!r} schema_version mismatch: "
            f"got {schema_version!r}, expected {PHASE1_SCHEMA_VERSION!r}"
        )
    required = ("approved_scope_ref", "started_at_utc")
    missing = [name for name in required if not batch.get(name)]
    if missing:
        raise PersistenceSafetyError(
            f"batch {batch.get('batch_id')!r} missing required fields: {missing}"
        )


def _filter_columns(payload: Mapping[str, Any], allowed: Iterable[str]) -> dict[str, Any]:
    allowed_set = set(allowed)
    return {key: value for key, value in payload.items() if key in allowed_set}


def _insert_or_ignore(
    conn: sqlite3.Connection, table: str, payload: Mapping[str, Any]
) -> int:
    contract = get_table_contract(table)
    filtered = _filter_columns(payload, contract.columns.keys())
    columns = list(filtered.keys())
    placeholders = ",".join("?" for _ in columns)
    col_list = ",".join(columns)
    cursor = conn.execute(
        f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})",
        [filtered[col] for col in columns],
    )
    return 1 if cursor.rowcount and cursor.rowcount > 0 else 0


def write_connector_run_result(
    conn: sqlite3.Connection,
    result: ConnectorRunResult,
    *,
    batch: Mapping[str, Any],
) -> WriteSummary:
    """Persist a fixture connector run into the M1 SQLite schema.

    The writer enforces Phase 1 safety, respects FK ordering
    (``batches`` → ``sources`` → ``cost_events`` → ``source_observations``),
    and preserves append-only semantics. Duplicate deterministic
    primary keys are reported in the returned :class:`WriteSummary`
    without mutating existing rows.
    """

    _check_result_safety(result)
    _check_source_safety(result.source)
    for obs in result.observations:
        _check_observation_safety(obs)
    for cost in result.cost_events:
        _check_cost_event_safety(cost)
    result_batch_id = _derive_result_batch_id(result)
    _check_batch_safety(batch, result_batch_id)

    batches_inserted = _insert_or_ignore(conn, "batches", batch)
    sources_inserted = _insert_or_ignore(conn, "sources", result.source)

    cost_events_inserted = 0
    for cost in result.cost_events:
        cost_events_inserted += _insert_or_ignore(conn, "cost_events", cost)

    observations_inserted = 0
    for obs in result.observations:
        observations_inserted += _insert_or_ignore(conn, "source_observations", obs)

    conn.commit()

    total_cost_events = len(result.cost_events)
    total_observations = len(result.observations)
    return WriteSummary(
        batch_id=result_batch_id,
        connector_run_id=result.connector_run_id,
        batches_inserted=batches_inserted,
        sources_inserted=sources_inserted,
        cost_events_inserted=cost_events_inserted,
        observations_inserted=observations_inserted,
        batches_duplicate=1 - batches_inserted,
        sources_duplicate=1 - sources_inserted,
        cost_events_duplicate=total_cost_events - cost_events_inserted,
        observations_duplicate=total_observations - observations_inserted,
    )
