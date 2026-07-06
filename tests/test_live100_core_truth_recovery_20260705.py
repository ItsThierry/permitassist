from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "artifacts" / "live100_random_50_50_customer_pov_5fe5c20_20260705T204831Z"
FACTCHECK_CSV = ARTIFACT_ROOT / "final_factcheck_confirmation_fable5" / "FINAL_FACTCHECK_TITI_FABLE5_CONFIRMED_GRADES.csv"
SCRIPT_PATH = ROOT / "scripts" / "live100_core_truth_recovery_20260705.py"
_spec = importlib.util.spec_from_file_location("live100_core_truth_recovery_20260705", SCRIPT_PATH)
assert _spec and _spec.loader
recovery = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recovery)

FALSE_NOT_REQUIRED_EXPECTED = recovery.FALSE_NOT_REQUIRED_EXPECTED
WRONG_AHJ_EXPECTATIONS = recovery.WRONG_AHJ_EXPECTATIONS
read_final_grades = recovery.read_final_grades
replay_all = recovery.replay_all
validate_rows = recovery.validate_rows

pytestmark = pytest.mark.skipif(
    not (ARTIFACT_ROOT / "cases.jsonl").exists() or not FACTCHECK_CSV.exists(),
    reason="Fresh random Live100 artifact is absent; core-truth recovery contracts run only in artifact-rich worktrees.",
)


@pytest.fixture(scope="module")
def recovery_report(tmp_path_factory: pytest.TempPathFactory) -> dict:
    out = tmp_path_factory.mktemp("live100_core_truth_recovery")
    rows = replay_all(ARTIFACT_ROOT, out)
    grades = read_final_grades(FACTCHECK_CSV)
    return validate_rows(rows, grades)


def _case_errors(report: dict, case_id: str) -> list[dict]:
    return list(report["per_case"][case_id]["errors"])


def _assert_no_gate_errors(report: dict, case_ids: list[str], gate_prefix: str) -> None:
    failures = {
        cid: [err for err in _case_errors(report, cid) if err["gate"].startswith(gate_prefix)]
        for cid in case_ids
    }
    failures = {cid: errs for cid, errs in failures.items() if errs}
    assert not failures, json.dumps(failures, indent=2, sort_keys=True)


def test_tier1_false_not_required_contracts_are_required_with_core_families(recovery_report: dict):
    """The eight highest-risk unsafe false-NOT_REQUIRED cases are non-negotiable RED contracts."""
    _assert_no_gate_errors(recovery_report, sorted(FALSE_NOT_REQUIRED_EXPECTED), "false_not_required")


def test_wrong_ahj_and_action_path_contracts_hold(recovery_report: dict):
    _assert_no_gate_errors(recovery_report, sorted(WRONG_AHJ_EXPECTATIONS), "wrong_ahj")


def test_no_change_use_sprinkler_overprescription_contracts_hold(recovery_report: dict):
    _assert_no_gate_errors(recovery_report, ["C-002", "C-013", "C-028", "C-030", "C-031"], "no_change_use")


def test_demo_is_not_recast_as_tenant_improvement(recovery_report: dict):
    _assert_no_gate_errors(recovery_report, ["C-016", "C-024"], "demo_as_ti")


def test_residential_kitchen_outputs_have_no_food_or_fog_contamination(recovery_report: dict):
    _assert_no_gate_errors(recovery_report, ["R-018", "R-024", "R-038", "R-039", "R-050"], "residential_commercial_contamination")


def test_overprescription_subtype_contracts_hold(recovery_report: dict):
    target_gate_prefixes = {
        "R-014": "wrong_egress_subtype",
        "R-029": "wrong_egress_subtype",
        "R-046": "unsupported_refrigeration",
        "C-025": "sign_overreach",
        "R-003": "water_heater_overprescription",
        "R-007": "ordinary_maintenance_overprescription_risk",
    }
    failures = {}
    for cid, gate_prefix in target_gate_prefixes.items():
        errs = [err for err in _case_errors(recovery_report, cid) if err["gate"].startswith(gate_prefix)]
        if errs:
            failures[cid] = errs
    assert not failures, json.dumps(failures, indent=2, sort_keys=True)


def test_green_freeze_has_no_core_truth_demotions_for_confirmed_ab_cases(recovery_report: dict):
    assert recovery_report["ab_green_freeze_count"] == 62
    green_errors = [err for err in recovery_report["errors"] if err["gate"].startswith("green_freeze")]
    assert not green_errors, json.dumps(green_errors[:50], indent=2, sort_keys=True)


def test_all_100_core_truth_gate_is_clean(recovery_report: dict):
    assert recovery_report["case_count"] == 100
    assert recovery_report["pass"] is True, json.dumps(recovery_report["errors"][:80], indent=2, sort_keys=True)


def test_kitchen_sink_move_wording_variant_stays_required(tmp_path: Path):
    server = recovery.import_server(tmp_path)
    public = server.build_customer_permit_view_model(
        {"permit_decision": "NOT_REQUIRED", "permit_required": False, "permit_verdict": "NO", "permits_required": []},
        "move kitchen sink and add island receptacles, residential kitchen remodel no structural changes",
        "South Bend",
        "IN",
        job_category="residential",
    )
    families = sorted({str(row.get("filing_family") or row.get("family") or "") for row in public.get("permits_required") or []})
    assert public.get("permit_decision") == "REQUIRED", public
    assert families == ["building", "electrical", "plumbing"], public
