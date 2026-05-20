"""Phase 1 M8 end-to-end fixture runner tests.

M8 proves the M1-M7 lead pipeline can run as one deterministic, local,
internal-review-only flow. It must remain fixture-only: no network, no paid APIs,
no browser/subprocess, no outreach, and no send authorization.
"""

from __future__ import annotations

import ast
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from lead_pipeline.contracts import ExportEligibility, GateStatus, PromotionTier
from lead_pipeline.event_writer import initialize_sqlite_schema
from lead_pipeline.phase1_runner import (
    PHASE1_M8_RUNNER_VERSION,
    Phase1RunnerSafetyError,
    run_phase1_fixture_pipeline,
)
from lead_pipeline.schema import PHASE1_SCHEMA_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]
M8_BATCH_ID = "batch_fixture_m8_golden"

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
    "subprocess",
}


def test_m8_version_and_module_have_no_network_or_outreach_imports():
    assert PHASE1_M8_RUNNER_VERSION == "lead_pipeline_phase1_m8_fixture_runner_v1"
    path = REPO_ROOT / "lead_pipeline" / "phase1_runner.py"
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
    assert not offenders, "M8 runner must stay fixture-only/no-network: " + ", ".join(offenders)


def test_m8_golden_fixture_runs_m1_through_m7_and_reports_internal_review_export():
    result = run_phase1_fixture_pipeline(fixture_id="golden")
    summary = result.to_dict()

    assert summary["runner_version"] == PHASE1_M8_RUNNER_VERSION
    assert summary["fixture_id"] == "golden"
    assert summary["batch_id"] == M8_BATCH_ID
    assert summary["safety"] == {
        "fixture_only": True,
        "network_used": False,
        "paid_api_used": False,
        "outreach_attempted": False,
        "send_authorized": False,
    }
    assert summary["table_counts"]["batches"] == 1
    assert summary["table_counts"]["sources"] == 1
    assert summary["table_counts"]["source_observations"] == 3
    assert summary["table_counts"]["entities"] == 3
    assert summary["table_counts"]["export_events"] == 1
    assert summary["exported_lead_count"] == 1
    assert summary["blocked_lead_count"] == 2
    assert len(summary["leads"]) == 3

    exported = [lead for lead in summary["leads"] if lead["export_event_id"]]
    assert len(exported) == 1
    ready = exported[0]
    assert ready["canonical_label"] == "Fixture Build Group"
    assert ready["promotion_tier"] == PromotionTier.OUTREACH_READY_INTERNAL_ONLY.value
    assert ready["gate_status"] == GateStatus.PASS_.value
    assert ready["export_eligibility"] == ExportEligibility.INTERNAL_REVIEW_ONLY.value
    assert ready["reason_codes"] == ["outreach_ready_internal_review_only_no_send"]
    assert ready["send_authorized"] is False
    assert ready["export_event_id"].startswith("exp_m7_internal_")
    assert ready["eligible_fact_ids"]
    assert ready["source_observation_ids"]
    assert ready["verification_event_ids"]
    assert ready["suppression_event_id"]
    assert ready["enrichment_event_id"]

    blocked_reasons = {
        lead["canonical_label"]: lead["reason_codes"] for lead in summary["leads"] if not lead["export_event_id"]
    }
    assert blocked_reasons["Quiet Clinic Contractors"] == ["missing_contact_candidate"]
    assert blocked_reasons["Suppressed TI Contractors"] == ["suppression_blocks_promotion"]

    rows = result.conn.execute(
        "SELECT export_target, status, send_authorized, included_fact_ids, included_source_observation_ids, "
        "included_verification_event_ids, suppression_event_id, enrichment_event_id FROM export_events"
    ).fetchall()
    assert len(rows) == 1
    export_target, status, send_authorized, fact_ids, observation_ids, verification_ids, suppression_id, enrichment_id = rows[0]
    assert export_target == "internal_review_queue"
    assert status == ExportEligibility.INTERNAL_REVIEW_ONLY.value
    assert send_authorized == 0
    assert json.loads(fact_ids) == ready["eligible_fact_ids"]
    assert json.loads(observation_ids) == ready["source_observation_ids"]
    assert set(json.loads(verification_ids)) == set(ready["verification_event_ids"])
    assert suppression_id == ready["suppression_event_id"]
    assert enrichment_id == ready["enrichment_event_id"]


def test_m8_runner_is_idempotent_on_existing_connection_and_does_not_duplicate_events():
    conn = sqlite3.connect(":memory:")
    initialize_sqlite_schema(conn)

    first = run_phase1_fixture_pipeline(fixture_id="golden", conn=conn).to_dict()
    second = run_phase1_fixture_pipeline(fixture_id="golden", conn=conn).to_dict()

    assert first["table_counts"] == second["table_counts"]
    assert first["table_counts"]["verification_events"] == second["table_counts"]["verification_events"]
    assert first["table_counts"]["suppression_events"] == second["table_counts"]["suppression_events"]
    assert first["table_counts"]["enrichment_events"] == second["table_counts"]["enrichment_events"]
    assert first["internal_review_export_event_ids"] == second["internal_review_export_event_ids"]
    assert first["leads"] == second["leads"]
    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 1
    assert conn.execute("SELECT COALESCE(SUM(send_authorized), 0) FROM export_events").fetchone()[0] == 0


def test_m8_runner_rejects_tampered_persisted_m6_event_on_replay():
    conn = sqlite3.connect(":memory:")
    initialize_sqlite_schema(conn)
    run_phase1_fixture_pipeline(fixture_id="golden", conn=conn)
    m6_row = conn.execute(
        "SELECT verification_event_id FROM verification_events WHERE gate_name = ? "
        "AND target_entity_id = (SELECT entity_id FROM entities WHERE canonical_label = ?)" ,
        ("phase1_m6_promotion_eligibility_gate", "Fixture Build Group"),
    ).fetchone()
    assert m6_row is not None
    conn.execute("DROP TRIGGER verification_events_append_only_no_update")
    conn.execute(
        "UPDATE verification_events SET reason_codes = ?, raw_result_ref = ? WHERE verification_event_id = ?",
        (
            json.dumps(["tampered_reason"]),
            json.dumps(
                {
                    "promotion_tier": PromotionTier.OUTREACH_READY_INTERNAL_ONLY.value,
                    "export_eligibility": ExportEligibility.INTERNAL_REVIEW_ONLY.value,
                    "reason_codes": ["tampered_reason"],
                    "eligible_fact_ids": [],
                    "source_observation_ids": [],
                    "verification_event_ids": [],
                    "suppression_event_id": None,
                    "enrichment_event_id": None,
                    "identity_edge_ids": [],
                    "send_authorized": False,
                    "schema_version": PHASE1_SCHEMA_VERSION,
                },
                sort_keys=True,
            ),
            m6_row[0],
        ),
    )
    conn.commit()

    with pytest.raises(Phase1RunnerSafetyError, match="persisted M6 decision does not match fresh evaluation"):
        run_phase1_fixture_pipeline(fixture_id="golden", conn=conn)

    assert conn.execute("SELECT COALESCE(SUM(send_authorized), 0) FROM export_events").fetchone()[0] == 0


def test_m8_runner_rejects_malformed_persisted_m6_event_on_replay():
    conn = sqlite3.connect(":memory:")
    initialize_sqlite_schema(conn)
    run_phase1_fixture_pipeline(fixture_id="golden", conn=conn)
    m6_row = conn.execute(
        "SELECT verification_event_id FROM verification_events WHERE gate_name = ? LIMIT 1",
        ("phase1_m6_promotion_eligibility_gate",),
    ).fetchone()
    assert m6_row is not None
    conn.execute("DROP TRIGGER verification_events_append_only_no_update")
    conn.execute(
        "UPDATE verification_events SET raw_result_ref = ? WHERE verification_event_id = ?",
        ("not-json", m6_row[0]),
    )
    conn.commit()

    with pytest.raises(Phase1RunnerSafetyError, match="persisted M6 event raw_result_ref is malformed"):
        run_phase1_fixture_pipeline(fixture_id="golden", conn=conn)


def test_m8_runner_rejects_duplicate_persisted_m6_events_on_replay():
    conn = sqlite3.connect(":memory:")
    initialize_sqlite_schema(conn)
    run_phase1_fixture_pipeline(fixture_id="golden", conn=conn)
    cursor = conn.execute(
        "SELECT * FROM verification_events WHERE gate_name = ? "
        "AND target_entity_id = (SELECT entity_id FROM entities WHERE canonical_label = ?) LIMIT 1",
        ("phase1_m6_promotion_eligibility_gate", "Fixture Build Group"),
    )
    row = cursor.fetchone()
    assert row is not None
    columns = [column[0] for column in cursor.description]
    payload = dict(zip(columns, row, strict=True))
    payload["verification_event_id"] = str(payload["verification_event_id"]) + "_duplicate"
    placeholders = ",".join("?" for _ in columns)
    column_list = ",".join(columns)
    conn.execute(
        f"INSERT INTO verification_events ({column_list}) VALUES ({placeholders})",
        [payload[column] for column in columns],
    )
    conn.commit()

    with pytest.raises(Phase1RunnerSafetyError, match="expected exactly one persisted M6 event per entity"):
        run_phase1_fixture_pipeline(fixture_id="golden", conn=conn)


def test_m8_runner_rejects_persisted_m6_event_missing_required_raw_fields():
    conn = sqlite3.connect(":memory:")
    initialize_sqlite_schema(conn)
    run_phase1_fixture_pipeline(fixture_id="golden", conn=conn)
    m6_row = conn.execute(
        "SELECT verification_event_id, raw_result_ref FROM verification_events WHERE gate_name = ? LIMIT 1",
        ("phase1_m6_promotion_eligibility_gate",),
    ).fetchone()
    assert m6_row is not None
    raw = json.loads(str(m6_row[1]))
    raw.pop("promotion_tier")
    conn.execute("DROP TRIGGER verification_events_append_only_no_update")
    conn.execute(
        "UPDATE verification_events SET raw_result_ref = ? WHERE verification_event_id = ?",
        (json.dumps(raw, sort_keys=True), m6_row[0]),
    )
    conn.commit()

    with pytest.raises(Phase1RunnerSafetyError, match="missing required decision fields"):
        run_phase1_fixture_pipeline(fixture_id="golden", conn=conn)


def test_m8_runner_rejects_network_tainted_persisted_m6_event_on_replay():
    conn = sqlite3.connect(":memory:")
    initialize_sqlite_schema(conn)
    run_phase1_fixture_pipeline(fixture_id="golden", conn=conn)
    m6_row = conn.execute(
        "SELECT verification_event_id FROM verification_events WHERE gate_name = ? LIMIT 1",
        ("phase1_m6_promotion_eligibility_gate",),
    ).fetchone()
    assert m6_row is not None
    conn.execute("DROP TRIGGER verification_events_append_only_no_update")
    conn.execute(
        "UPDATE verification_events SET network_used_flag = 1 WHERE verification_event_id = ?",
        (m6_row[0],),
    )
    conn.commit()

    with pytest.raises(Phase1RunnerSafetyError, match="used network"):
        run_phase1_fixture_pipeline(fixture_id="golden", conn=conn)


def test_m8_runner_rejects_connection_missing_phase1_schema():
    conn = sqlite3.connect(":memory:")

    with pytest.raises(Phase1RunnerSafetyError, match="missing the Phase 1 fixture schema"):
        run_phase1_fixture_pipeline(fixture_id="golden", conn=conn)


def test_m8_runner_persists_fixture_urls_only():
    result = run_phase1_fixture_pipeline(fixture_id="golden")
    urls = [row[0] for row in result.conn.execute("SELECT url_or_path FROM source_observations")]

    assert urls
    assert all(url.startswith("fixture://") for url in urls)


def test_m8_runner_rejects_unknown_fixture_without_writing_rows():
    conn = sqlite3.connect(":memory:")
    initialize_sqlite_schema(conn)

    with pytest.raises(Phase1RunnerSafetyError, match="unknown fixture_id"):
        run_phase1_fixture_pipeline(fixture_id="live_city_seed", conn=conn)

    assert conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 0


def test_m8_cli_outputs_deterministic_json_summary_no_send():
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "lead_pipeline.run_phase1_fixture_pipeline",
            "--fixture",
            "golden",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(proc.stdout)

    assert payload["fixture_id"] == "golden"
    assert payload["runner_version"] == PHASE1_M8_RUNNER_VERSION
    assert payload["exported_lead_count"] == 1
    assert payload["blocked_lead_count"] == 2
    assert payload["safety"]["send_authorized"] is False
    assert payload["table_counts"]["export_events"] == 1
