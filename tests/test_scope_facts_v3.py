from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from scope_contract import TriFact, build_scope_facts_v3


def test_co2_hazmat_fact_carries_evidence():
    facts = build_scope_facts_v3(
        "commercial cannabis cultivation tenant improvement adding grow lights ventilation odor control and CO2 system, job value 900000",
        "Boulder",
        "CO",
        job_category="commercial",
    )
    assert facts.co2_enrichment.value == TriFact.TRUE
    assert facts.co2_enrichment.evidence
    assert facts.hazardous_materials.value == TriFact.TRUE
    assert "co2" in facts.hazmat_kinds


def test_structural_facade_lintel_fact_beats_storefront_labeling():
    facts = build_scope_facts_v3(
        "commercial exterior facade repair with scaffolding and masonry lintel replacement, job value 140000",
        "Philadelphia",
        "PA",
        job_category="commercial",
    )
    assert facts.structural_work.value == TriFact.TRUE
    assert facts.structural_work.evidence
    assert "masonry" in facts.structural_kinds or "lintel" in facts.structural_kinds
    assert facts.facade_scope == "structural_facade"


def test_existing_boxes_no_new_circuits_is_false_with_evidence():
    facts = build_scope_facts_v3(
        "replace 12 kitchen outlets with GFCI in existing boxes no new circuits, job value 1200",
        "St. Louis",
        "MO",
        job_category="residential",
    )
    assert facts.electrical_new_circuits.value == TriFact.FALSE
    assert facts.electrical_new_circuits.evidence


def test_residential_grill_is_not_food_establishment_and_is_gas_scope():
    facts = build_scope_facts_v3(
        "install new gas line to outdoor kitchen and grill, job value 6000",
        "Jackson",
        "MS",
        job_category="residential",
    )
    assert facts.residential_outdoor_cooking.value == TriFact.TRUE
    assert facts.food_establishment.value == TriFact.FALSE
    assert facts.grease_discharge.value == TriFact.FALSE
    assert facts.gas_fuel_work.value == TriFact.TRUE


def test_warehouse_to_pickleball_is_assembly_change_of_use():
    facts = build_scope_facts_v3(
        "change use from warehouse to indoor pickleball facility with occupant load increase, job value 300000",
        "Portland",
        "OR",
        job_category="commercial",
    )
    assert facts.change_of_use is not None
    assert facts.change_of_use.from_use == "warehouse"
    assert facts.change_of_use.to_use in {"pickleball", "assembly"}
    assert facts.assembly_occupancy.value == TriFact.TRUE


def test_chicago_small_nonstructural_drywall_repair_exemption_fact():
    facts = build_scope_facts_v3(
        "replace drywall in one bedroom after leak no structural no electrical no plumbing, job value 3500",
        "Chicago",
        "IL",
        job_category="residential",
    )
    assert facts.repair_exemption_candidate is True
    assert facts.structural_work.value == TriFact.FALSE
    assert facts.structural_work.evidence
