import pytest

from live_customer_100_phase0_helpers import build_public, load_contracts, assert_contract_satisfied


@pytest.mark.parametrize("contract", load_contracts(), ids=lambda c: c["case_id"])
def test_live_customer_100_confirmed_cf_cases_meet_source_backed_family_contracts(contract):
    """RED: frozen 20 C/F cases must satisfy source/scope-backed family contracts."""
    public = build_public(contract)
    assert_contract_satisfied(contract, public)
