from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GREEN = ROOT / "tests" / "fixtures" / "live100_fable5_final_green_freeze_68_20260705.json"
RED = ROOT / "tests" / "fixtures" / "live100_fable5_final_red_structural_32_20260705.json"
IDENTITY = ROOT / "artifacts" / "live100_fable5_phase0_phase1_20260705" / "identity_diff_clean_fd39220_deterministic" / "IDENTITY_DIFF_REPORT.json"
DETERMINISM = ROOT / "artifacts" / "live100_fable5_phase0_phase1_20260705" / "determinism" / "IDENTITY_DIFF_REPORT.json"
ATTRIBUTION = ROOT / "artifacts" / "live100_fable5_phase0_phase1_20260705" / "attribution" / "PHASE0_ATTRIBUTION_DOC.json"


def test_final_fable5_green_freeze_and_red_fixture_counts():
    green = json.loads(GREEN.read_text())
    red = json.loads(RED.read_text())
    assert green["count"] == 68
    assert len(green["cases"]) == 68
    assert red["count"] == 32
    assert len(red["cases"]) == 32
    assert {case["grade"] for case in green["cases"].values()} <= {"A", "B"}
    assert {case["grade"] for case in red["cases"].values()} <= {"C", "F"}


def test_phase0_identity_diff_and_determinism_reports_are_green():
    identity = json.loads(IDENTITY.read_text())
    determinism = json.loads(DETERMINISM.read_text())
    assert identity["records_before"] == identity["records_after"] == 100
    assert identity["diff_count"] == 0
    assert identity["identity_diff_pass"] is True
    assert determinism["records_before"] == determinism["records_after"] == 100
    assert determinism["diff_count"] == 0
    assert determinism["determinism_pass"] is True


def test_phase0_hypotheses_are_explicitly_confirmed_or_refuted():
    doc = json.loads(ATTRIBUTION.read_text())
    assert doc["c_f_count"] == 32
    hypotheses = doc["hypotheses"]
    assert set(hypotheses) == {
        "stale_summary_mirror",
        "shared_cache_template_leak",
        "existing_verification_or_conditional_state",
        "apply_url_loss_point",
    }
    for info in hypotheses.values():
        assert info["status"]
        assert info["status"] != "UNKNOWN"
