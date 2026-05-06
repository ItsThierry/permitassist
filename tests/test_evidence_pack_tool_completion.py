import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.evidence_pack_tool import (
    build_evidence_pack,
    evidence_pack_tool_readiness,
    raw_file_sha256,
    write_evidence_pack_outputs,
)


def _fixture_artifact(tmp_path: Path) -> Path:
    artifact = {
        "title": "completion fixture",
        "batch_id": "completion-fixture",
        "created_utc": "2026-05-05T12:00:00Z",
        "status": "PASS_ACCEPTED_AFTER_INDEPENDENT_REVIEW",
        "accepted_upgrades": [
            {
                "row_id": "completion-row",
                "state": "TX",
                "ahj_name": "City of Dallas",
                "vertical": "restaurant_ti",
                "field": "apply_url",
                "proposed_status": "verified",
                "claim_value_after": "https://developdallas.dallascityhall.com/",
                "source_scope_limit": "Verifies apply URL only; does not verify permit type, fee range, approval timeline, inspections, or companion reviews.",
                "checked_at_utc": "2026-05-05T12:01:00Z",
                "evidence": [
                    {
                        "source_url": "https://developdallas.dallascityhall.com/",
                        "source_title": "Develop Dallas",
                        "source_type": "official_ahj_portal",
                        "exact_quote": "Welcome to the City of Dallas online permit portal.",
                        "quote_found_current_run": True,
                        "normalized_source_text_sha256": "a" * 64,
                        "normalized_source_text_length": 2222,
                        "fetch_status_code": 200,
                    }
                ],
            }
        ],
    }
    path = tmp_path / "permitassist-road-to-perfection-step6a-batch99-evidence-upgrades-2026-05-05.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    return path


def test_packaging_writes_raw_sha256_sidecar(tmp_path):
    pack = build_evidence_pack([_fixture_artifact(tmp_path)], generated_at_utc="2026-05-05T15:15:00Z")

    json_path, md_path = write_evidence_pack_outputs(pack, tmp_path, stem="completion-pack")

    sha_path = json_path.with_suffix(json_path.suffix + ".sha256")
    assert json_path.exists()
    assert md_path.exists()
    assert sha_path.exists()
    assert sha_path.read_text(encoding="utf-8").strip() == raw_file_sha256(json_path)


def test_tool_readiness_covers_scaffold_validate_score_report_promote_package_preview(tmp_path):
    pack = build_evidence_pack([_fixture_artifact(tmp_path)], generated_at_utc="2026-05-05T15:15:00Z")
    json_path, md_path = write_evidence_pack_outputs(pack, tmp_path, stem="completion-pack")

    readiness = evidence_pack_tool_readiness(json_path, report_path=md_path)

    assert readiness["verdict"] == "FULLY_READY_OFFLINE_FAIL_CLOSED"
    assert readiness["readiness_score"] == 100
    assert readiness["stages"] == {
        "scaffold": "pass",
        "validate": "pass",
        "score": "pass",
        "report": "pass",
        "promote_or_fail_closed": "pass",
        "package_fingerprint": "pass",
        "preview_contract": "pass",
    }
    assert readiness["safe_for_env_activation"] is False
    assert readiness["evidence_pack_vars_required_before_activation"] == []


def test_tool_readiness_fails_closed_when_sha_sidecar_mismatches(tmp_path):
    pack = build_evidence_pack([_fixture_artifact(tmp_path)], generated_at_utc="2026-05-05T15:15:00Z")
    json_path, md_path = write_evidence_pack_outputs(pack, tmp_path, stem="completion-pack")
    json_path.with_suffix(json_path.suffix + ".sha256").write_text("0" * 64 + "\n", encoding="utf-8")

    readiness = evidence_pack_tool_readiness(json_path, report_path=md_path)

    assert readiness["verdict"] == "FAIL_CLOSED_NOT_READY"
    assert readiness["stages"]["package_fingerprint"] == "fail"
    assert "raw_sha256_sidecar_mismatch" in readiness["blockers"]


def test_build_script_validate_existing_is_read_only_and_prints_readiness(tmp_path):
    pack = build_evidence_pack([_fixture_artifact(tmp_path)], generated_at_utc="2026-05-05T15:15:00Z")
    json_path, md_path = write_evidence_pack_outputs(pack, tmp_path, stem="completion-pack")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_step7b_evidence_pack.py",
            "--validate-existing-pack",
            str(json_path),
            "--report-path",
            str(md_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "Readiness verdict: FULLY_READY_OFFLINE_FAIL_CLOSED" in result.stdout
    assert "Safe for env activation: false" in result.stdout
