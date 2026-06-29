import pytest

from live_customer_100_phase0_helpers import build_public, load_contract, load_contracts, public_text, required_rows


def test_required_result_cannot_have_not_applicable_apply_path_or_no_permit_channel():
    public = build_public(load_contract("R100-007"))
    apply_path = public.get("apply_path") or {}
    assert public.get("permit_decision") == "REQUIRED"
    assert apply_path.get("state") != "NOT_APPLICABLE"
    assert apply_path.get("channel") != "no_permit_required"
    assert public.get("apply_url") or apply_path.get("portal_url") or "contact" in str(public.get("customer_next_step") or "").lower()


def test_not_required_contract_has_no_required_rows_or_apply_task():
    public = build_public(load_contract("R100-048"))
    if public.get("permit_decision") == "NOT_REQUIRED":
        assert required_rows(public) == []
        assert "file the required permit" not in public_text(public)


def test_required_fee_copy_cannot_say_zero_if_no_permit_required():
    public = build_public(load_contract("R100-048"))
    if public.get("permit_decision") == "REQUIRED":
        fee = str(public.get("fee_range") or "").lower()
        assert "$0" not in fee and "if no permit required" not in fee


@pytest.mark.parametrize("contract", load_contracts(), ids=lambda c: c["case_id"])
def test_public_boundary_result_keeps_report_share_apply_and_source_surfaces(contract):
    public = build_public(contract)
    text = public_text(public)
    assert public.get("permit_name")
    assert public.get("customer_next_step") or public.get("customer_result_summary", {}).get("next_step")
    assert public.get("sources") or public.get("source_urls") or "source" in text
