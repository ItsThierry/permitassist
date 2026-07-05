from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "artifacts" / "live100_fable5_phase0_phase1_20260705" / "final_validation" / "FABLE5_FINAL_VALIDATION_REPORT.json"
PASS2 = ROOT / "artifacts" / "live100_fable5_phase0_phase1_20260705" / "fable5_final_verified_pass2"


def _public(case_id: str) -> dict:
    matches = list((PASS2 / "public_json").glob(f"*{case_id}.json"))
    assert len(matches) == 1
    return json.loads(matches[0].read_text())


def _families(obj: dict, key: str) -> set[str]:
    return {str(row.get("family") or row.get("filing_family") or "") for row in obj.get(key) or [] if isinstance(row, dict)}


def test_fable5_final_validation_report_is_green():
    report = json.loads(REPORT.read_text())
    assert report["pass"] is True
    assert report["errors"] == []
    assert report["green_freeze_count"] == 68
    assert report["red_target_count"] == 32
    assert report["pass1"]["tripwire_failure_count"] == 0
    assert report["pass2"]["tripwire_failure_count"] == 0
    assert report["pass1"]["parity_failure_count"] == 0
    assert report["pass2"]["parity_failure_count"] == 0
    assert report["pass1"]["public_hash_all"] == report["pass2"]["public_hash_all"]
    assert report["pass1"]["html_hash_all"] == report["pass2"]["html_hash_all"]


def test_fable5_e1_e2_sentinels_hold():
    c037 = _public("C-037")
    assert {"health_food", "liquor"}.isdisjoint(_families(c037, "permits_required"))
    assert {"health_food", "liquor"} <= _families(c037, "related_permits")

    c046 = _public("C-046")
    assert {"health_food", "wastewater_pretreatment_fog"}.isdisjoint(_families(c046, "permits_required"))
    assert "wastewater_pretreatment_fog" in _families(c046, "related_permits")

    r012 = _public("R-012")
    assert "mechanical" in _families(r012, "permits_required")
    assert "plumbing" not in _families(r012, "permits_required")
    assert "plumbing" in _families(r012, "related_permits")

    for case_id in ("R-022", "R-036"):
        obj = _public(case_id)
        assert obj["permit_decision"] == "NOT_REQUIRED"
        assert obj.get("permits_required") == []
