import pytest

from universal_customer_phase0_helpers import (
    assert_basic_public_invariants,
    assert_no_collapsed_package,
    assert_contract_satisfied,
    build_public,
    final_grades,
    load_case,
    load_contracts,
    load_no_neuter_anchors,
    non_a_case_ids,
    required_rows,
    visible_rows,
)

ROOT_IDS = ["prior_50_50_120408", "real_customer_50_50_160333"]


@pytest.mark.parametrize("root_id", ROOT_IDS)
def test_universal_fixture_covers_every_non_a_case_from_both_artifact_roots(root_id):
    fixture_ids = {c["case_id"] for c in load_contracts() if c["root_id"] == root_id}
    fixture_ids |= {a["case_id"] for a in load_no_neuter_anchors() if a["root_id"] == root_id}
    assert non_a_case_ids(root_id).issubset(fixture_ids), {
        "root_id": root_id,
        "missing_non_a_ids": sorted(non_a_case_ids(root_id) - fixture_ids),
        "all_non_a_ids": sorted(non_a_case_ids(root_id)),
    }


@pytest.mark.parametrize("contract", load_contracts(), ids=lambda c: f"{c['root_id']}::{c['case_id']}")
def test_phase0_confirmed_non_a_universal_customer_contracts(contract):
    case = load_case(contract["root_id"], contract["case_id"])
    public = build_public(case)
    assert_basic_public_invariants(case, public)
    assert_contract_satisfied(contract, public)


@pytest.mark.parametrize("anchor", load_no_neuter_anchors(), ids=lambda a: f"{a['root_id']}::{a['case_id']}")
def test_phase0_no_neuter_a_anchors_keep_decision_and_packet_depth(anchor):
    case = load_case(anchor["root_id"], anchor["case_id"])
    public = build_public(case)
    # No-neuter anchors are a packet-richness guard.  They must not be forced to
    # satisfy the new no-collapse architecture before product code exists; the
    # contract tests above carry that RED invariant separately.
    expected = next(
        (
            contract.get("expected_decision")
            for contract in load_contracts()
            if contract.get("root_id") == anchor["root_id"] and contract.get("case_id") == anchor["case_id"]
        ),
        None,
    )
    if expected in {"CONDITIONAL", "VERIFY", "NEEDS_INPUT"}:
        assert public.get("permit_decision") == expected
        assert public.get("permit_required") is None
        assert not required_rows(public)
    elif expected in {"REQUIRED", "NOT_REQUIRED"}:
        assert public.get("permit_decision") == expected
        assert public.get("permit_required") is (expected == "REQUIRED")
    else:
        decision = public.get("permit_decision")
        assert decision in {"REQUIRED", "NOT_REQUIRED", "CONDITIONAL", "VERIFY", "NEEDS_INPUT"}
        assert public.get("permit_required") is ({"REQUIRED": True, "NOT_REQUIRED": False}.get(decision))
    grade = final_grades(anchor["root_id"])[anchor["case_id"]]
    assert grade == "A", {"anchor": anchor, "grade": grade}
    assert public.get("permit_decision") == expected if expected else public.get("permit_decision") in {"REQUIRED", "NOT_REQUIRED", "CONDITIONAL", "VERIFY", "NEEDS_INPUT"}
    min_visible_rows = int(anchor.get("min_visible_rows", 0))
    assert len(visible_rows(public)) >= min_visible_rows, {"anchor": anchor, "visible_rows": visible_rows(public)}
    assert_no_collapsed_package(public, anchor["case_id"])
    if public.get("permit_decision") == "REQUIRED":
        assert required_rows(public), {"anchor": anchor, "permit_name": public.get("permit_name")}


def test_phase0_contracts_include_latest_sign_cosmetic_jurisdiction_undercall_classes():
    latest = {c["case_id"]: c for c in load_contracts() if c["root_id"] == "real_customer_50_50_160333"}
    assert {"C100-013", "C100-042", "C100-043"}.issubset(latest)
    assert {"C100-022", "C100-023", "C100-024", "C100-041"}.issubset(latest)
    assert {"R100-011", "R100-035"}.issubset(latest)
    assert "R100-032" in latest
    assert all(c.get("must_not_collapse") for c in latest.values() if c.get("expected_decision") == "REQUIRED")
