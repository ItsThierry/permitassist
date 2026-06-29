import pytest

from live_customer_100_phase0_helpers import build_public, family_from_row, load_contracts, row_has_condition, row_status, visible_rows


@pytest.mark.parametrize("contract", load_contracts(), ids=lambda c: c["case_id"])
def test_every_visible_verify_or_conditional_expected_floor_row_has_trigger_condition(contract):
    public = build_public(contract)
    expected_condition_families = {
        item["family"] for item in contract.get("expected_visible_families", [])
        if item.get("condition_required_when_not_required")
    }
    for row in visible_rows(public):
        if family_from_row(row) in expected_condition_families and row_status(row) in {"VERIFY", "CONDITIONAL"}:
            assert row_has_condition(row), {"case": contract["case_id"], "row": row}


def test_verify_condition_contract_rejects_bare_verify_everything_noise():
    bare = {"permit_type": "Electrical Permit", "filing_family": "electrical", "status": "VERIFY", "rationale": "Verify with AHJ."}
    assert not row_has_condition(bare), "Bare verify-with-AHJ noise is not a trigger condition."
    good = {"permit_type": "Electrical Permit", "filing_family": "electrical", "status": "VERIFY", "trigger_condition": "If a new circuit, disconnect, or equipment connection is part of scope."}
    assert row_has_condition(good)
