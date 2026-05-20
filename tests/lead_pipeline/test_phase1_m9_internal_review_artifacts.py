"""Phase 1 M9 internal review artifact tests.

M9 renders the M8 ``export_events`` ledger into local Boban/Titi review
artifacts only. It must stay fixture/local-only: no network, no paid APIs, no
browser/subprocess in implementation, no CRM/outreach, and no send authorization.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from lead_pipeline.contracts import ExportEligibility, GateStatus
from lead_pipeline.phase1_runner import run_phase1_fixture_pipeline
from lead_pipeline.review_artifacts import (
    INTERNAL_REVIEW_BANNER,
    PHASE1_M9_ARTIFACT_SCHEMA_VERSION,
    InternalReviewArtifactSafetyError,
    render_internal_review_artifacts,
    write_internal_review_artifacts,
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


def test_m9_version_and_module_have_no_network_or_outreach_imports():
    assert PHASE1_M9_ARTIFACT_SCHEMA_VERSION == "lead_pipeline_phase1_m9_internal_review_artifact_v1"
    assert "send_authorized=false" in INTERNAL_REVIEW_BANNER
    assert "INTERNAL REVIEW ONLY" in INTERNAL_REVIEW_BANNER
    for relative in (
        Path("lead_pipeline") / "review_artifacts.py",
        Path("lead_pipeline") / "run_internal_review_artifacts.py",
    ):
        path = REPO_ROOT / relative
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
        assert not offenders, "M9 renderer must stay local/no-network: " + ", ".join(offenders)


def test_m9_renders_json_and_markdown_from_m8_export_events_for_internal_review_only():
    run = run_phase1_fixture_pipeline(fixture_id="golden")

    artifact = render_internal_review_artifacts(run.conn, batch_id=M8_BATCH_ID)
    payload = artifact.to_dict()

    assert payload["artifact_schema_version"] == PHASE1_M9_ARTIFACT_SCHEMA_VERSION
    assert payload["banner"] == INTERNAL_REVIEW_BANNER
    assert payload["batch_id"] == M8_BATCH_ID
    assert payload["export_event_count"] == 1
    assert payload["safety"] == {
        "local_artifact_only": True,
        "internal_review_only": True,
        "network_used": False,
        "outreach_attempted": False,
        "crm_sync_attempted": False,
        "send_authorized": False,
    }

    [export] = payload["exports"]
    assert export["business_label"] == "Fixture Build Group"
    assert export["status"] == ExportEligibility.INTERNAL_REVIEW_ONLY.value
    assert export["internal_review_only"] is True
    assert export["send_authorized"] is False
    assert export["export_target"] == "internal_review_queue"
    assert export["artifact_use"] == "boban_titi_review_only"
    assert export["icp_reasons"]
    assert any("commercial tenant improvement" in reason for reason in export["icp_reasons"])
    assert any("general_contractor" in reason for reason in export["icp_reasons"])
    assert export["source_fixture_lineage"]
    assert all(item["url_or_path"].startswith("fixture://") for item in export["source_fixture_lineage"])
    assert export["suppression"]["status"] == "clear"
    assert export["suppression"]["suppression_event_id"].startswith("sup_m8_")
    assert export["enrichment"]["validator_status"] == GateStatus.PASS_.value
    assert export["enrichment"]["unsupported_claim_count"] == 0
    assert export["verification_event_ids"]
    assert any(event["gate_name"] == "phase1_m6_promotion_eligibility_gate" for event in export["verification_events"])
    assert all(event["network_used_flag"] == 0 for event in export["verification_events"])
    assert export["facts"]
    assert all(fact["source_observation_id"] for fact in export["facts"])

    markdown = artifact.markdown
    assert "# Lead Pipeline M9 Internal Review Artifact" in markdown
    assert INTERNAL_REVIEW_BANNER in markdown
    assert "Business label: Fixture Build Group" in markdown
    assert "ICP reasons" in markdown
    assert "Source / fixture lineage" in markdown
    assert "Suppression status: clear" in markdown
    assert "Enrichment status: pass" in markdown
    assert "Verification event IDs" in markdown
    assert "send_authorized=false" in markdown
    assert "internal_review_only" in markdown
    assert "CRM" in markdown and "outreach" in markdown

    reparsed = json.loads(artifact.json_text)
    assert reparsed == payload


def test_m9_writes_local_markdown_and_json_files_only(tmp_path: Path):
    run = run_phase1_fixture_pipeline(fixture_id="golden")

    result = write_internal_review_artifacts(run.conn, output_dir=tmp_path, batch_id=M8_BATCH_ID)

    assert result.json_path == tmp_path / "lead_pipeline_m9_internal_review_artifacts.json"
    assert result.markdown_path == tmp_path / "lead_pipeline_m9_internal_review_artifacts.md"
    assert result.json_path.exists()
    assert result.markdown_path.exists()
    assert json.loads(result.json_path.read_text(encoding="utf-8"))["safety"]["send_authorized"] is False
    assert INTERNAL_REVIEW_BANNER in result.markdown_path.read_text(encoding="utf-8")


def test_m9_cli_runs_m8_fixture_and_writes_local_artifacts(tmp_path: Path):
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "lead_pipeline.run_internal_review_artifacts",
            "--fixture",
            "golden",
            "--output-dir",
            str(tmp_path),
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    manifest = json.loads(proc.stdout)

    assert manifest["artifact_schema_version"] == PHASE1_M9_ARTIFACT_SCHEMA_VERSION
    assert manifest["json_path"].endswith("lead_pipeline_m9_internal_review_artifacts.json")
    assert manifest["markdown_path"].endswith("lead_pipeline_m9_internal_review_artifacts.md")
    assert Path(manifest["json_path"]).exists()
    assert Path(manifest["markdown_path"]).exists()
    assert manifest["safety"]["send_authorized"] is False
    assert manifest["safety"]["internal_review_only"] is True


def test_m9_fails_closed_on_send_authorized_export_row():
    run = run_phase1_fixture_pipeline(fixture_id="golden")
    export_event_id = run.conn.execute("SELECT export_event_id FROM export_events LIMIT 1").fetchone()[0]
    run.conn.execute("DROP TRIGGER export_events_append_only_no_update")
    run.conn.execute("PRAGMA ignore_check_constraints = ON")
    run.conn.execute("UPDATE export_events SET send_authorized = 1 WHERE export_event_id = ?", (export_event_id,))
    run.conn.commit()

    with pytest.raises(InternalReviewArtifactSafetyError, match="send_authorized"):
        render_internal_review_artifacts(run.conn, batch_id=M8_BATCH_ID)


def test_m9_fails_closed_on_network_tainted_verification_event():
    run = run_phase1_fixture_pipeline(fixture_id="golden")
    event_id = run.conn.execute(
        "SELECT verification_event_id FROM verification_events WHERE gate_name = ? LIMIT 1",
        ("phase1_m6_promotion_eligibility_gate",),
    ).fetchone()[0]
    run.conn.execute("DROP TRIGGER verification_events_append_only_no_update")
    run.conn.execute("UPDATE verification_events SET network_used_flag = 1 WHERE verification_event_id = ?", (event_id,))
    run.conn.commit()

    with pytest.raises(InternalReviewArtifactSafetyError, match="network"):
        render_internal_review_artifacts(run.conn, batch_id=M8_BATCH_ID)


def test_m9_fails_closed_on_missing_or_nonfixture_source_lineage():
    run = run_phase1_fixture_pipeline(fixture_id="golden")
    observation_id = run.conn.execute("SELECT observation_id FROM source_observations LIMIT 1").fetchone()[0]
    run.conn.execute("DROP TRIGGER source_observations_append_only_no_update")
    run.conn.execute("UPDATE source_observations SET url_or_path = ? WHERE observation_id = ?", ("https://example.test/live", observation_id))
    run.conn.commit()

    with pytest.raises(InternalReviewArtifactSafetyError, match="fixture"):
        render_internal_review_artifacts(run.conn, batch_id=M8_BATCH_ID)


def test_m9_fails_closed_on_schema_drift_in_export_or_lineage_rows():
    run = run_phase1_fixture_pipeline(fixture_id="golden")
    export_event_id = run.conn.execute("SELECT export_event_id FROM export_events LIMIT 1").fetchone()[0]
    run.conn.execute("DROP TRIGGER export_events_append_only_no_update")
    run.conn.execute("UPDATE export_events SET schema_version = ? WHERE export_event_id = ?", ("future_schema", export_event_id))
    run.conn.commit()

    with pytest.raises(InternalReviewArtifactSafetyError, match="schema_version"):
        render_internal_review_artifacts(run.conn, batch_id=M8_BATCH_ID)


def test_m9_fails_closed_on_duplicate_export_lineage_ids():
    run = run_phase1_fixture_pipeline(fixture_id="golden")
    export_event_id, fact_ids_json = run.conn.execute(
        "SELECT export_event_id, included_fact_ids FROM export_events LIMIT 1"
    ).fetchone()
    fact_ids = json.loads(fact_ids_json)
    fact_ids.append(fact_ids[0])
    run.conn.execute("DROP TRIGGER export_events_append_only_no_update")
    run.conn.execute("UPDATE export_events SET included_fact_ids = ? WHERE export_event_id = ?", (json.dumps(fact_ids), export_event_id))
    run.conn.commit()

    with pytest.raises(InternalReviewArtifactSafetyError, match="duplicate"):
        render_internal_review_artifacts(run.conn, batch_id=M8_BATCH_ID)


def test_m9_fails_closed_on_cross_entity_verification_event_lineage():
    run = run_phase1_fixture_pipeline(fixture_id="golden")
    event_id = run.conn.execute(
        "SELECT verification_event_id FROM verification_events WHERE target_fact_id IS NOT NULL LIMIT 1"
    ).fetchone()[0]
    run.conn.execute(
        "INSERT INTO entities(entity_id, entity_type, canonical_label, normalized_key, status, schema_version) "
        "VALUES (?, 'business', ?, ?, ?, ?)",
        ("ent_m9_other", "M9 Other", "m9 other", "raw_discovery", PHASE1_SCHEMA_VERSION),
    )
    run.conn.execute("DROP TRIGGER verification_events_append_only_no_update")
    run.conn.execute("UPDATE verification_events SET target_entity_id = ? WHERE verification_event_id = ?", ("ent_m9_other", event_id))
    run.conn.commit()

    with pytest.raises(InternalReviewArtifactSafetyError, match="verification event lineage"):
        render_internal_review_artifacts(run.conn, batch_id=M8_BATCH_ID)


def test_m9_fails_closed_on_secret_like_content_before_rendering():
    run = run_phase1_fixture_pipeline(fixture_id="golden")
    fact_id = run.conn.execute("SELECT fact_id FROM facts WHERE fact_type = 'business_name' LIMIT 1").fetchone()[0]
    secret_like = "sk_" + "liv" + "e_" + "abcdefghi"
    run.conn.execute("DROP TRIGGER facts_append_only_no_update")
    run.conn.execute("UPDATE facts SET fact_value = ? WHERE fact_id = ?", (secret_like, fact_id))
    run.conn.commit()

    with pytest.raises(InternalReviewArtifactSafetyError, match="secret-like"):
        render_internal_review_artifacts(run.conn, batch_id=M8_BATCH_ID)
