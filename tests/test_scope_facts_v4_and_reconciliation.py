from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from family_reconciliation_gate import reconcile_rows  # noqa: E402
from public_packet import apply_public_packet_projection  # noqa: E402
from scope_contract import TriFact, build_scope_facts_v4  # noqa: E402


def test_scope_facts_v4_commercial_office_ti_axes_and_explicit_negatives():
    facts = build_scope_facts_v4(
        "commercial office tenant improvement with non load bearing partitions lighting receptacles and HVAC diffuser relocation no change of use, job value 95000",
        "Phoenix",
        "AZ",
        job_category="commercial",
    )
    assert facts.segment == "commercial"
    assert facts.occupancy_class == "commercial"
    assert facts.use_change is False
    assert "building_ti" in facts.request_positive_families
    assert "electrical" in facts.request_positive_families
    assert "mechanical" in facts.request_positive_families
    assert facts.structural_work.value == TriFact.FALSE
    assert "no_use_change" in facts.negative_facts


def test_scope_facts_v4_madison_state_split_has_no_mechanical_positive_when_scope_has_only_electrical_plumbing():
    facts = build_scope_facts_v4(
        "commercial tenant improvement replacing electrical service and adding plumbing fixtures only no mechanical or HVAC work",
        "Madison",
        "WI",
        job_category="commercial",
    )
    assert facts.occupancy_class == "commercial"
    assert {"building_ti", "electrical", "plumbing"} <= set(facts.request_positive_families)
    assert "mechanical" not in facts.request_positive_families
    assert facts.mechanical_work.value == TriFact.FALSE


def test_scope_facts_v4_ev_and_same_size_windows_do_not_positive_support_building_overpermits():
    ev = build_scope_facts_v4("residential level 2 EV charger branch circuit on existing panel", "Tempe", "AZ", job_category="residential")
    assert "electrical" in ev.request_positive_families
    assert "building" not in ev.request_positive_families
    windows = build_scope_facts_v4("replace same-size windows no structural changes no wall framing", "Kansas City", "MO", job_category="residential")
    assert windows.structural_work.value == TriFact.FALSE
    assert "structural" in windows.request_negative_families


def test_reconciliation_precedence_veto_beats_keep_and_demote_for_explicit_negative():
    facts = build_scope_facts_v4(
        "commercial tenant improvement replacing electrical service and adding plumbing fixtures only no mechanical or HVAC work",
        "Madison",
        "WI",
        job_category="commercial",
    )
    rows = [
        {"permit_type": "Mechanical Permit", "family": "mechanical", "required": True, "source_url": "https://www.cityofmadison.com/development-services-center"},
        {"permit_type": "Electrical Permit", "family": "electrical", "required": True, "source_url": "https://www.cityofmadison.com/development-services-center"},
    ]
    kept, conditional, rulings = reconcile_rows(rows, facts)
    assert "mechanical" not in {r.get("family") for r in kept}
    assert "mechanical" not in {r.get("family") for r in conditional}
    actions = {(r.family, r.action) for r in rulings}
    assert ("mechanical", "VETO") in actions
    assert ("electrical", "KEEP") in actions


def test_hard_required_without_positive_fact_or_source_is_demoted_not_kept():
    facts = build_scope_facts_v4("residential level 2 EV charger branch circuit on existing panel", "Tempe", "AZ", job_category="residential")
    rows = [{"permit_type": "Building Permit", "family": "building", "required": True}]
    kept, conditional, rulings = reconcile_rows(rows, facts)
    assert "building" not in {r.get("family") for r in kept}
    assert "building" in {r.get("family") for r in conditional}
    assert ("building", "DEMOTE") in {(r.family, r.action) for r in rulings}


def test_projection_does_not_convert_required_to_not_required_when_scope_support_misses_all_rows():
    facts = build_scope_facts_v4("residential level 2 EV charger branch circuit on existing panel", "Tempe", "AZ", job_category="residential")
    result = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "segment": "residential",
        "permits_required": [
            {
                "permit_type": "Building Permit",
                "permit_name": "Building Permit",
                "family": "building",
                "required": True,
                "source_url": "https://www.tempe.gov/government/community-development/building-safety/permits",
            }
        ],
        "apply_url": "https://www.tempe.gov/government/community-development/building-safety/permits",
        "source_urls": ["https://www.tempe.gov/government/community-development/building-safety/permits"],
    }
    public = apply_public_packet_projection(result, facts)
    assert public["permit_decision"] == "REQUIRED"
    assert public["permit_required"] is True
    required_rows = [r for r in (public.get("public_packet") or {}).get("rows") or [] if r.get("decision") == "REQUIRED"]
    assert required_rows, public


def test_fee_cleaner_removes_project_value_from_fee_only_fields():
    facts = build_scope_facts_v4("commercial tenant improvement", "Orlando", "FL", job_category="commercial")
    result = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "segment": "commercial",
        "fee_range": "Fee estimate: plan review permit fees may apply plus project value around $300,000.",
        "permits_required": [{"permit_type": "Commercial Building Permit", "family": "building_ti", "required": True}],
    }
    public = apply_public_packet_projection(result, facts)
    fee_text = str(public.get("fee_range") or "").lower()
    assert "project value" not in fee_text
    assert "$300,000" not in fee_text
