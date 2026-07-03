from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from family_reconciliation_gate import apply_family_reconciliation_gate, family_from_row, reconcile_rows
from scope_contract import build_scope_facts_v2


def test_fable5_phase2_floor_adds_after_conditional_alias_and_forbidden_veto():
    facts = build_scope_facts_v2(
        "change of occupancy from retail store to fitness studio with showers and new mechanical ventilation, job value 175000",
        "Glendale",
        "AZ",
        job_category="commercial",
    )
    rows = [
        {"permit_type": "Commercial Building / Tenant Improvement Permit", "required": True, "source_url": "https://example.gov"},
        {"permit_type": "Mechanical Permit — Commercial Tenant Improvement", "required": True, "source_url": "https://example.gov"},
        {"permit_type": "Plumbing Permit", "required": False, "decision": "CONDITIONAL"},
    ]
    kept, _conditional, rulings = reconcile_rows(rows, facts)
    assert any(family_from_row(r) == "plumbing" and r.get("required") is True for r in kept)
    assert all(family_from_row(r) != "plumbing" for r in _conditional)
    assert any(r.action == "ADD" and r.family == "plumbing" for r in rulings)

    residential = build_scope_facts_v2("install new gas line to outdoor kitchen and grill, job value 6000", "Jackson", "MS", job_category="residential")
    rows = [
        {"permit_type": "Plumbing Permit — Gas Piping Installation (Residential)", "required": True},
        {"permit_type": "Wastewater / FOG / Pretreatment Approval", "required": False, "decision": "CONDITIONAL"},
        {"permit_type": "Health Plan Review / Food Establishment Permit", "required": False, "decision": "CONDITIONAL"},
    ]
    kept, conditional, rulings = reconcile_rows(rows, residential)
    assert all(family_from_row(r) not in {"health_food", "wastewater_pretreatment_fog"} for r in kept + conditional)
    assert any(r.action == "VETO" and r.family in {"health_food", "wastewater_pretreatment_fog"} for r in rulings)


def test_precedence_negative_fact_beats_source_backed_trigger():
    facts = build_scope_facts_v2("commercial exterior wall sign face change only no electrical work", "Minneapolis", "MN")
    rows = [{"permit_type": "Electrical Permit — Illuminated Sign", "source_url": "https://www.minneapolismn.gov/permits", "required": True}]
    kept, conditional, rulings = reconcile_rows(rows, facts)
    assert not any(family_from_row(r) == "electrical" for r in kept)
    assert any(r.action == "VETO" and r.family == "electrical" for r in rulings)


def test_source_backed_quirk_kept_over_heuristic_demote():
    facts = build_scope_facts_v2("replace exterior front door same size in historic district", "Charleston", "SC")
    rows = [{"permit_type": "Historic / HDLC Certificate of Appropriateness Review", "source_url": "https://www.charleston-sc.gov", "required": True}]
    kept, conditional, _ = reconcile_rows(rows, facts)
    assert "historic_review" in [family_from_row(r) for r in kept]
    assert all(family_from_row(r) != "historic_review" for r in conditional)


def test_plausible_extra_demoted_not_dropped():
    facts = build_scope_facts_v2("commercial office tenant improvement with partitions lighting receptacles and diffuser relocation", "Phoenix", "AZ")
    result = {"permit_decision": "REQUIRED", "permit_required": True, "permits_required": [{"permit_type": "Plumbing Permit", "required": True}]}
    out = apply_family_reconciliation_gate(result, "commercial office tenant improvement with partitions lighting receptacles and diffuser relocation", "Phoenix", "AZ")
    assert out.get("conditional_permits")
    assert any(r.get("family") == "plumbing" and r.get("decision") == "CONDITIONAL" for r in out.get("conditional_permits") or [])
    assert any(r.get("family") in {"building_ti", "electrical", "mechanical"} for r in out.get("permits_required") or [])


def test_all_vetoed_rows_preserved_only_as_conditional_guidance():
    out = apply_family_reconciliation_gate(
        {"permit_decision": "REQUIRED", "permit_required": True, "permits_required": [{"permit_type": "Electrical Permit", "required": True, "filing_family": "electrical"}]},
        "interior paint only, no electrical work, no plumbing, no mechanical",
        "Austin",
        "TX",
    )
    assert not out.get("permits_required")
    assert any(r.get("family") == "electrical" and r.get("decision") == "CONDITIONAL" and r.get("required") is False for r in out.get("conditional_permits") or [])
    assert any(r.get("action") == "CONDITIONAL_FALLBACK" for r in out.get("_family_gate_audit") or [])


def test_add_from_amperage():
    facts = build_scope_facts_v2("upgrade to 600A service", "Kansas City", "MO")
    kept, _, rulings = reconcile_rows([], facts)
    assert any(family_from_row(r) == "electrical" for r in kept)
    assert any(r.action == "ADD" and r.family == "electrical" for r in rulings)


def test_add_never_fires_without_structured_implication():
    facts = build_scope_facts_v2("commercial laundry concept study, possible future power needs", "Kansas City", "MO")
    kept, _, rulings = reconcile_rows([], facts)
    assert kept == []
    assert not any(r.action == "ADD" for r in rulings)


def test_veto_records_audit():
    out = apply_family_reconciliation_gate(
        {"permit_decision": "REQUIRED", "permit_required": True, "permits_required": [{"permit_type": "Mechanical Permit", "required": True}]},
        "commercial white box demolition partitions only no MEP",
        "Detroit",
        "MI",
    )
    assert any(r["action"] == "VETO" and r["basis"] for r in out.get("_family_gate_audit", []))
