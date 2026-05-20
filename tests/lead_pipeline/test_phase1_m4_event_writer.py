"""Phase 1 M4 SQLite event writer / persistence layer tests.

These tests assert that the M4 writer takes an M3 ConnectorRunResult and
persists it into an in-memory SQLite database initialized from the M1
schema, with foreign keys ON, append-only semantics, and a second safety
boundary on top of the connector-time policy: paid/login/scrape sources,
non-fixture URLs, schema-version drift, blocked connector ids, network
or send_authorized flags must all be rejected at write time.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path
from types import MappingProxyType

import pytest

from lead_pipeline.connectors import (
    ConnectorRunResult,
    FetchMode,
    FixtureDocument,
    run_fixture_connector,
)
from lead_pipeline.schema import PHASE1_SCHEMA_VERSION, REQUIRED_TABLES

from lead_pipeline.event_writer import (
    PERSISTENCE_VERSION,
    PersistenceSafetyError,
    WriteSummary,
    initialize_sqlite_schema,
    write_connector_run_result,
)


REPO_ROOT = Path(__file__).resolve().parents[2]

NETWORK_IMPORTS = {
    "requests",
    "httpx",
    "urllib",
    "socket",
    "smtplib",
    "dns",
    "aiohttp",
    "selenium",
    "playwright",
    "firecrawl",
    "brave",
}


M4_BATCH_ID = "batch_fixture_m4"


def _make_doc(url: str, snippet: str, payload: str) -> FixtureDocument:
    return FixtureDocument(
        url_or_path=url,
        fetched_at_utc="2026-05-20T00:00:00Z",
        payload_text=payload,
        snippet=snippet,
        content_type="text/plain",
    )


def _clean_documents() -> list[FixtureDocument]:
    return [
        _make_doc(
            "fixture://contractor-first-party/fixturebuild/services/commercial-ti",
            "We perform commercial tenant improvement build-outs.",
            "Fixture Build Group commercial TI page payload synthetic body.",
        ),
        _make_doc(
            "fixture://contractor-first-party/fixturebuild/about",
            "Fixture Build Group is a licensed commercial general contractor.",
            "Fixture Build Group about page payload synthetic body.",
        ),
    ]


def _clean_run() -> ConnectorRunResult:
    return run_fixture_connector(
        "contractor_first_party_website",
        documents=_clean_documents(),
        batch_id=M4_BATCH_ID,
        mode=FetchMode.FIXTURE_ONLY,
    )


def _clean_batch() -> dict[str, object]:
    return {
        "batch_id": M4_BATCH_ID,
        "approved_scope_ref": "phase1_fixture_only_lead_pipeline_m4",
        "adapter_id": "permitassist",
        "started_at_utc": "2026-05-20T00:00:00Z",
        "status": "open",
        "schema_version": PHASE1_SCHEMA_VERSION,
    }


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    initialize_sqlite_schema(conn)
    return conn


def _replace_observations(
    result: ConnectorRunResult, observations: tuple,
) -> ConnectorRunResult:
    return ConnectorRunResult(
        connector_run_id=result.connector_run_id,
        connector_id=result.connector_id,
        mode=result.mode,
        network_used=result.network_used,
        send_authorized=result.send_authorized,
        source=result.source,
        observations=observations,
        cost_events=result.cost_events,
    )


def _replace_source(result: ConnectorRunResult, source) -> ConnectorRunResult:
    return ConnectorRunResult(
        connector_run_id=result.connector_run_id,
        connector_id=result.connector_id,
        mode=result.mode,
        network_used=result.network_used,
        send_authorized=result.send_authorized,
        source=source,
        observations=result.observations,
        cost_events=result.cost_events,
    )


def _replace_cost_events(result: ConnectorRunResult, cost_events) -> ConnectorRunResult:
    return ConnectorRunResult(
        connector_run_id=result.connector_run_id,
        connector_id=result.connector_id,
        mode=result.mode,
        network_used=result.network_used,
        send_authorized=result.send_authorized,
        source=result.source,
        observations=result.observations,
        cost_events=cost_events,
    )


def test_persistence_version_is_phase1_m4_event_writer_v1():
    assert PERSISTENCE_VERSION == "lead_pipeline_phase1_m4_event_writer_v1"


def test_initialize_sqlite_schema_enables_foreign_keys_and_creates_required_tables():
    conn = sqlite3.connect(":memory:")
    initialize_sqlite_schema(conn)

    (fk_on,) = conn.execute("PRAGMA foreign_keys").fetchone()
    assert fk_on == 1

    table_rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    table_names = {row[0] for row in table_rows}
    for table_name in REQUIRED_TABLES:
        assert table_name in table_names, table_name


def test_write_connector_run_inserts_batch_source_cost_events_and_observations():
    conn = _connection()
    result = _clean_run()
    batch = _clean_batch()

    summary = write_connector_run_result(conn, result, batch=batch)

    assert isinstance(summary, WriteSummary)

    batch_rows = conn.execute(
        "SELECT batch_id, approved_scope_ref, status, schema_version FROM batches"
    ).fetchall()
    assert batch_rows == [
        (M4_BATCH_ID, "phase1_fixture_only_lead_pipeline_m4", "open", PHASE1_SCHEMA_VERSION)
    ]

    source_rows = conn.execute(
        "SELECT source_id, source_class, base_url_or_path, allowed_phase, paid_flag, requires_login "
        "FROM sources"
    ).fetchall()
    assert len(source_rows) == 1
    (source_id, source_class, base_url, allowed_phase, paid_flag, requires_login) = source_rows[0]
    assert source_id == result.source["source_id"]
    assert source_class == "first_party_website"
    assert base_url.startswith("fixture://")
    assert allowed_phase == "phase1_fixture_only"
    assert paid_flag == 0
    assert requires_login == 0

    cost_rows = conn.execute(
        "SELECT cost_event_id, batch_id, connector_run_id, stage, cost_usd FROM cost_events"
    ).fetchall()
    assert len(cost_rows) == len(result.cost_events)
    for cost_row, expected in zip(cost_rows, result.cost_events):
        assert cost_row[0] == expected["cost_event_id"]
        assert cost_row[1] == M4_BATCH_ID
        assert cost_row[2] == result.connector_run_id
        assert cost_row[3] == "raw_collection"
        assert cost_row[4] == 0.0

    obs_rows = conn.execute(
        "SELECT observation_id, source_id, batch_id, connector_run_id, "
        "url_or_path, payload_hash_sha256, snippet_or_excerpt, cost_event_id "
        "FROM source_observations ORDER BY observation_id"
    ).fetchall()
    assert len(obs_rows) == len(result.observations)
    expected_by_id = {obs["observation_id"]: obs for obs in result.observations}
    for row in obs_rows:
        expected = expected_by_id[row[0]]
        assert row[1] == result.source["source_id"]
        assert row[2] == M4_BATCH_ID
        assert row[3] == result.connector_run_id
        assert row[4] == expected["url_or_path"]
        assert row[5] == expected["payload_hash_sha256"]
        assert row[6] == expected["snippet_or_excerpt"]
        assert row[7] == expected["cost_event_id"]


def test_write_connector_run_returns_deterministic_summary_with_row_counts():
    conn = _connection()
    result = _clean_run()
    batch = _clean_batch()

    summary = write_connector_run_result(conn, result, batch=batch)

    assert summary.batch_id == M4_BATCH_ID
    assert summary.connector_run_id == result.connector_run_id
    assert summary.batches_inserted == 1
    assert summary.sources_inserted == 1
    assert summary.cost_events_inserted == len(result.cost_events)
    assert summary.observations_inserted == len(result.observations)
    assert summary.batches_duplicate == 0
    assert summary.sources_duplicate == 0
    assert summary.cost_events_duplicate == 0
    assert summary.observations_duplicate == 0


def test_write_duplicate_run_reports_duplicates_without_mutation():
    conn = _connection()
    result = _clean_run()
    batch = _clean_batch()

    first = write_connector_run_result(conn, result, batch=batch)
    snapshot_before = conn.execute(
        "SELECT observation_id, payload_hash_sha256, snippet_or_excerpt "
        "FROM source_observations ORDER BY observation_id"
    ).fetchall()

    second = write_connector_run_result(conn, result, batch=batch)
    snapshot_after = conn.execute(
        "SELECT observation_id, payload_hash_sha256, snippet_or_excerpt "
        "FROM source_observations ORDER BY observation_id"
    ).fetchall()

    assert snapshot_before == snapshot_after
    assert second.batches_inserted == 0
    assert second.sources_inserted == 0
    assert second.cost_events_inserted == 0
    assert second.observations_inserted == 0
    assert second.batches_duplicate == 1
    assert second.sources_duplicate == 1
    assert second.cost_events_duplicate == len(result.cost_events)
    assert second.observations_duplicate == len(result.observations)

    # The append-only DB triggers must still be in place after the writer ran.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE source_observations SET snippet_or_excerpt = 'mutated' "
            "WHERE observation_id = ?",
            (snapshot_after[0][0],),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "DELETE FROM source_observations WHERE observation_id = ?",
            (snapshot_after[0][0],),
        )

    # And the writer itself never tries to mutate rows: a hand-crafted UPDATE
    # outside the writer fails. The writer never issues UPDATE/DELETE either.
    assert first.observations_inserted == len(result.observations)


def test_write_rejects_network_used_true():
    conn = _connection()
    good = _clean_run()
    bad = ConnectorRunResult(
        connector_run_id=good.connector_run_id,
        connector_id=good.connector_id,
        mode=good.mode,
        network_used=True,
        send_authorized=False,
        source=good.source,
        observations=good.observations,
        cost_events=good.cost_events,
    )
    with pytest.raises(PersistenceSafetyError) as excinfo:
        write_connector_run_result(conn, bad, batch=_clean_batch())
    assert "network_used" in str(excinfo.value)
    assert conn.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0] == 0


def test_write_rejects_send_authorized_true():
    conn = _connection()
    good = _clean_run()
    bad = ConnectorRunResult(
        connector_run_id=good.connector_run_id,
        connector_id=good.connector_id,
        mode=good.mode,
        network_used=False,
        send_authorized=True,
        source=good.source,
        observations=good.observations,
        cost_events=good.cost_events,
    )
    with pytest.raises(PersistenceSafetyError) as excinfo:
        write_connector_run_result(conn, bad, batch=_clean_batch())
    assert "send_authorized" in str(excinfo.value)
    assert conn.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0] == 0


def test_write_rejects_mode_other_than_fixture_only():
    conn = _connection()
    good = _clean_run()
    bad = ConnectorRunResult(
        connector_run_id=good.connector_run_id,
        connector_id=good.connector_id,
        mode="live",
        network_used=False,
        send_authorized=False,
        source=good.source,
        observations=good.observations,
        cost_events=good.cost_events,
    )
    with pytest.raises(PersistenceSafetyError) as excinfo:
        write_connector_run_result(conn, bad, batch=_clean_batch())
    assert "fixture_only" in str(excinfo.value)


def test_write_rejects_blocked_connector_id_at_write_time():
    conn = _connection()
    good = _clean_run()
    bad = ConnectorRunResult(
        connector_run_id=good.connector_run_id,
        connector_id="apollo_io_b2b_database",
        mode=good.mode,
        network_used=False,
        send_authorized=False,
        source=good.source,
        observations=good.observations,
        cost_events=good.cost_events,
    )
    with pytest.raises(PersistenceSafetyError) as excinfo:
        write_connector_run_result(conn, bad, batch=_clean_batch())
    msg = str(excinfo.value)
    assert "apollo_io_b2b_database" in msg
    assert "blocked_paid_phase1" in msg


def test_write_rejects_unknown_connector_id_at_write_time():
    conn = _connection()
    good = _clean_run()
    bad = ConnectorRunResult(
        connector_run_id=good.connector_run_id,
        connector_id="not_a_real_connector",
        mode=good.mode,
        network_used=False,
        send_authorized=False,
        source=good.source,
        observations=good.observations,
        cost_events=good.cost_events,
    )
    with pytest.raises(PersistenceSafetyError) as excinfo:
        write_connector_run_result(conn, bad, batch=_clean_batch())
    assert "not_a_real_connector" in str(excinfo.value)


def test_write_rejects_non_fixture_source_base_url():
    conn = _connection()
    good = _clean_run()
    bad_source = dict(good.source)
    bad_source["base_url_or_path"] = "https://contractor.example.com/"
    bad = _replace_source(good, MappingProxyType(bad_source))
    with pytest.raises(PersistenceSafetyError) as excinfo:
        write_connector_run_result(conn, bad, batch=_clean_batch())
    assert "fixture://" in str(excinfo.value)


def test_write_rejects_non_fixture_observation_url_or_path():
    conn = _connection()
    good = _clean_run()
    first = dict(good.observations[0])
    first["url_or_path"] = "https://example.com/page"
    new_observations = (MappingProxyType(first),) + good.observations[1:]
    bad = _replace_observations(good, new_observations)
    with pytest.raises(PersistenceSafetyError) as excinfo:
        write_connector_run_result(conn, bad, batch=_clean_batch())
    assert "fixture://" in str(excinfo.value)


def test_write_rejects_schema_version_mismatch_on_source():
    conn = _connection()
    good = _clean_run()
    bad_source = dict(good.source)
    bad_source["schema_version"] = "lead_pipeline_phase1_m1_v0_drift"
    bad = _replace_source(good, MappingProxyType(bad_source))
    with pytest.raises(PersistenceSafetyError) as excinfo:
        write_connector_run_result(conn, bad, batch=_clean_batch())
    assert "schema_version" in str(excinfo.value)


def test_write_rejects_schema_version_mismatch_on_observation():
    conn = _connection()
    good = _clean_run()
    first = dict(good.observations[0])
    first["schema_version"] = "lead_pipeline_phase1_m1_v0_drift"
    new_observations = (MappingProxyType(first),) + good.observations[1:]
    bad = _replace_observations(good, new_observations)
    with pytest.raises(PersistenceSafetyError) as excinfo:
        write_connector_run_result(conn, bad, batch=_clean_batch())
    assert "schema_version" in str(excinfo.value)


def test_write_rejects_schema_version_mismatch_on_cost_event():
    conn = _connection()
    good = _clean_run()
    first = dict(good.cost_events[0])
    first["schema_version"] = "lead_pipeline_phase1_m1_v0_drift"
    new_costs = (MappingProxyType(first),) + good.cost_events[1:]
    bad = _replace_cost_events(good, new_costs)
    with pytest.raises(PersistenceSafetyError) as excinfo:
        write_connector_run_result(conn, bad, batch=_clean_batch())
    assert "schema_version" in str(excinfo.value)


def test_write_rejects_non_phase1_source_allowed_phase():
    conn = _connection()
    good = _clean_run()
    bad_source = dict(good.source)
    bad_source["allowed_phase"] = "blocked_future_phase_requires_separate_approval"
    bad = _replace_source(good, MappingProxyType(bad_source))
    with pytest.raises(PersistenceSafetyError) as excinfo:
        write_connector_run_result(conn, bad, batch=_clean_batch())
    assert "allowed_phase" in str(excinfo.value)


def test_write_rejects_paid_source_for_writable_sources():
    conn = _connection()
    good = _clean_run()
    bad_source = dict(good.source)
    bad_source["paid_flag"] = 1
    bad = _replace_source(good, MappingProxyType(bad_source))
    with pytest.raises(PersistenceSafetyError) as excinfo:
        write_connector_run_result(conn, bad, batch=_clean_batch())
    assert "paid" in str(excinfo.value).lower()


def test_write_rejects_login_source_for_writable_sources():
    conn = _connection()
    good = _clean_run()
    bad_source = dict(good.source)
    bad_source["requires_login"] = 1
    bad = _replace_source(good, MappingProxyType(bad_source))
    with pytest.raises(PersistenceSafetyError) as excinfo:
        write_connector_run_result(conn, bad, batch=_clean_batch())
    assert "login" in str(excinfo.value).lower()


def test_write_rejects_batch_id_mismatch_between_result_and_batch():
    conn = _connection()
    result = _clean_run()
    bad_batch = _clean_batch()
    bad_batch["batch_id"] = "batch_some_other_id"
    with pytest.raises(PersistenceSafetyError) as excinfo:
        write_connector_run_result(conn, result, batch=bad_batch)
    assert "batch_id" in str(excinfo.value)


def test_write_rejects_batch_with_drifted_schema_version():
    conn = _connection()
    result = _clean_run()
    bad_batch = _clean_batch()
    bad_batch["schema_version"] = "lead_pipeline_phase1_m1_v0_drift"
    with pytest.raises(PersistenceSafetyError) as excinfo:
        write_connector_run_result(conn, result, batch=bad_batch)
    assert "schema_version" in str(excinfo.value)


def test_event_writer_module_has_no_network_or_paid_imports():
    path = REPO_ROOT / "lead_pipeline" / "event_writer.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        imported: list[str] = []
        if isinstance(node, ast.Import):
            imported = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported = [node.module.split(".")[0]]
        for module_name in imported:
            if module_name in NETWORK_IMPORTS:
                offenders.append(f"{path.name} imports {module_name}")
    assert not offenders, "Phase 1 M4 writer must stay fixture-only/no-network:\n" + "\n".join(offenders)
