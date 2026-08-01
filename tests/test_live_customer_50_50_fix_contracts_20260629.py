import copy
import pytest

from live_customer_50_50_phase0_helpers import (
    assert_contract_satisfied,
    assert_mirrors_consistent,
    build_public,
    family_from_row,
    load_contract,
    load_contracts,
    public_text,
    required_families,
    required_rows,
    server_module,
    visible_rows,
)


NON_A_CASES = {
    "R100-023", "R100-050", "C100-050", "R100-007", "R100-010", "R100-022", "R100-040",
    "C100-037", "C100-048", "C100-006", "C100-011", "C100-021", "C100-040",
}
NO_NEUTER_CASES = {f"R100-{i:03d}" for i in range(44, 50)} | {"C100-005", "C100-008", "C100-010", "C100-012", "C100-016", "C100-032", "C100-041"}


def test_fixture_contains_required_50_50_cases_and_anchors():
    ids = {contract["case_id"] for contract in load_contracts()}
    assert NON_A_CASES.issubset(ids)
    assert {f"R100-{i:03d}" for i in range(44, 50)}.issubset(ids)
    assert {"C100-005", "C100-008", "C100-010", "C100-012", "C100-016", "C100-032", "C100-041"}.issubset(ids)


@pytest.mark.parametrize("contract", load_contracts(), ids=lambda c: c["case_id"])
def test_live_customer_50_50_fix_contracts_are_satisfied(contract):
    public = build_public(contract)
    assert_contract_satisfied(contract, public)


@pytest.mark.parametrize("case_id", sorted(NON_A_CASES))
def test_non_a_rows_have_boundary_safe_required_not_required_prose(case_id):
    contract = load_contract(case_id)
    public = build_public(contract)
    text = public_text(public)
    if public.get("permit_decision") == "REQUIRED":
        assert "no permit required" not in text
        assert "no permit submission needed" not in text
        assert required_rows(public)
    elif public.get("permit_decision") == "NOT_REQUIRED":
        assert not required_rows(public)
        assert "file the required permit" not in text


@pytest.mark.parametrize("case_id", sorted(NO_NEUTER_CASES))
def test_no_neuter_anchors_keep_expected_decision_and_packet_depth(case_id):
    contract = load_contract(case_id)
    public = build_public(contract)
    assert_contract_satisfied(contract, public)


def test_finalizer_idempotency_for_required_and_not_required_mirrors():
    for case_id in ["R100-023", "R100-050", "C100-050", "R100-044", "C100-010"]:
        contract = load_contract(case_id)
        first = build_public(contract)
        second = build_public(contract, source=copy.deepcopy(first))
        keys = [
            "permit_decision", "permit_required", "permit_name", "permit_type", "customer_headline",
            "customer_next_step", "required_permit_names", "required_permit_families",
        ]
        assert {key: first.get(key) for key in keys} == {key: second.get(key) for key in keys}, {"case": case_id, "first": {key: first.get(key) for key in keys}, "second": {key: second.get(key) for key in keys}}
        assert_mirrors_consistent(second)


def test_suppressed_wrong_families_are_not_source_backed_hard_rows():
    expectations = {
        "R100-007": {"building"},
        "R100-010": {"roofing"},
        "R100-022": {"foundation"},
        "C100-037": {"building"},
        "C100-048": {"building"},
        "C100-050": {"building", "fire"},
    }
    for case_id, forbidden in expectations.items():
        public = build_public(load_contract(case_id))
        assert not (forbidden & required_families(public)), {"case": case_id, "forbidden": sorted(forbidden), "required": sorted(required_families(public))}


def test_commercial_cosmetic_historical_artifact_fails_closed_and_clears_hard_rows():
    contract = load_contract("C100-050")
    public = build_public(contract)
    assert public["permit_decision"] == "VERIFY"
    assert public["permit_required"] is None
    assert public.get("permits_required") == []
    assert not public.get("companion_permits")
    assert "cosmetic" in public_text(public).lower()


@pytest.mark.parametrize("scope", [
    "replace exterior storefront signage with new cabinet and disconnect, no tenant improvement or wall work",
    "install projecting sign over sidewalk for retail storefront, no tenant improvement or building alteration",
    "Sign cabinet replacement with electrical disconnect only, no tenant improvement or building alteration",
])
def test_sign_trigger_variants_share_sign_row_and_demote_building_ti(scope):
    contract = load_contract("C100-048")
    source = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_name": "Multiple permits required: Building + Electrical",
        "permits_required": [
            {"permit_type": "Commercial Building / Tenant Improvement Permit", "filing_family": "building", "required": True},
            {"permit_type": "Electrical Permit — Illuminated Sign", "filing_family": "electrical", "required": True},
        ],
        "apply_path": {"state": "RESOLVED_PORTAL", "channel": "online_portal", "portal_url": "https://example.gov/permits"},
        "sources": [{"title": "Official sign permits", "url": "https://example.gov/signs"}],
        "applying_office": "Test permit office",
        "_scope_contract": {"category": "commercial", "city": contract["city"], "state": contract["state"]},
    }
    variant = dict(contract)
    variant["input_scope"] = scope
    public = build_public(variant, source=source)
    families = {family_from_row(row) for row in visible_rows(public)}
    assert public["permit_decision"] == "VERIFY"
    assert public["permit_required"] is None
    assert "sign" in families
    assert "sign" not in required_families(public)
    assert "building" not in required_families(public)


@pytest.mark.parametrize("scope", [
    "significant electrical service upgrade with new panels and branch circuits, no signage work",
    "signal and low-voltage controls upgrade for parking garage, no sign or sign cabinet work",
    "signature lighting fixture replacement inside lobby, no exterior sign work",
])
def test_non_sign_words_do_not_trigger_sign_permit_false_positive(scope):
    contract = load_contract("C100-048")
    source = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_name": "Electrical Permit",
        "permits_required": [
            {"permit_type": "Electrical Permit", "filing_family": "electrical", "required": True},
        ],
        "apply_path": {"state": "RESOLVED_PORTAL", "channel": "online_portal", "portal_url": "https://example.gov/permits"},
        "sources": [{"title": "Official electrical permits", "url": "https://example.gov/electrical"}],
        "applying_office": "Test permit office",
        "_scope_contract": {"category": "commercial", "city": contract["city"], "state": contract["state"]},
    }
    variant = dict(contract)
    variant["input_scope"] = scope
    public = build_public(variant, source=source)
    families = {family_from_row(row) for row in visible_rows(public)}
    assert "sign" not in families
    assert "sign" not in required_families(public)


def test_ambiguous_decision_without_hard_rows_does_not_project_as_no_permit_required():
    contract = load_contract("C100-050")
    source = {
        "permit_required": False,
        "permit_decision": "CONDITIONAL",
        "permit_name": "Verify permit applicability",
        "permits_required": [],
        "apply_path": {"state": "CONTACT_AHJ", "channel": "contact_ahj", "portal_url": None},
        "sources": [{"title": "Official permit office", "url": "https://example.gov/permits"}],
        "applying_office": "Test permit office",
        "_scope_contract": {"category": "commercial", "city": contract["city"], "state": contract["state"]},
    }
    variant = dict(contract)
    variant["input_scope"] = "commercial ambiguous office work requiring AHJ verification, no cosmetic-only exemption facts"
    public = build_public(variant, source=source)
    assert public["permit_decision"] == "CONDITIONAL"
    assert public["permit_required"] is None
    assert public.get("permits_required") == []
    assert public.get("family_decisions")
    assert all(row.get("status") == "CONDITIONAL" for row in public["family_decisions"])
    assert "no permit required" not in public_text(public)


def test_plain_public_projection_cannot_reestablish_required_authority():
    contract = load_contract("R100-050")
    first = build_public(contract)
    server = server_module()
    second = server.finalize_customer_public_projection(first, contract["input_scope"], contract["city"], contract["state"], first.get("_scope_contract") or {})
    assert first["permit_decision"] == "VERIFY"
    assert first["permit_required"] is None
    assert second["permit_decision"] == "VERIFY"
    assert second["permit_required"] is None
    assert not required_rows(first)
    assert not required_rows(second)
