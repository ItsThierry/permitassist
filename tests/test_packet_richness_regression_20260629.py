import pytest

from live_customer_100_phase0_helpers import build_public, family_from_row, load_contract, required_rows, row_status, visible_rows


SENTINELS = {
    "R100-002": {"electrical", "mechanical", "refrigeration"},
    "R100-038": {"building", "electrical", "plumbing", "mechanical"},
    "C100-010": {"building", "electrical", "mechanical", "plumbing", "health", "fire", "wastewater"},
    "C100-034": {"building", "plumbing", "electrical", "mechanical"},
}


def _artifact_contract_for(case_id):
    # A/B sentinels not all in the 20-contract fixture: synthesize from grades row paths when needed.
    from pathlib import Path
    import csv
    root = Path(__file__).resolve().parents[1] / "artifacts" / "live_customer_100_20260629T0032Z"
    grades_path = root / "FINAL_TITI_OPUS_GRADES.csv"
    cases_path = root / "cases.jsonl"
    if not grades_path.exists() or not cases_path.exists():
        pytest.skip("live_customer_100 artifact corpus is local-only and absent in clean checkout")
    for row in csv.DictReader(open(grades_path, newline="")):
        if row["case_id"] == case_id:
            import json
            by_id = {}
            for line in open(root / "cases.jsonl"):
                if line.strip():
                    rec = json.loads(line); by_id[rec["case"]["id"]] = rec
            rec = by_id[case_id]
            return {"case_id": case_id, "segment": row["segment"], "city": row["city"], "state": row["state"], "input_scope": rec["case"]["job_type"], "artifact_response_json_path": row["response_json_path"]}
    raise KeyError(case_id)


@pytest.mark.parametrize("case_id,expected", sorted(SENTINELS.items()))
def test_packet_richness_sentinels_retain_expected_family_depth(case_id, expected):
    contract = load_contract(case_id) if case_id in {c["case_id"] for c in __import__('live_customer_100_phase0_helpers').load_contracts()} else _artifact_contract_for(case_id)
    public = build_public(contract)
    families = {family_from_row(row) for row in visible_rows(public) if row_status(row) in {"REQUIRED", "CONDITIONAL", "VERIFY"}}
    assert expected.issubset(families), {"case": case_id, "expected_missing": sorted(expected - families), "families": sorted(families), "permit_name": public.get("permit_name")}


def test_packet_richness_regression_floor_keeps_companions_not_just_single_primary():
    total_rows = 0
    for case_id in SENTINELS:
        public = build_public(_artifact_contract_for(case_id))
        total_rows += len(visible_rows(public))
    assert total_rows >= 18, "Sentinel packets collapsed; companion/family rows were stripped."
