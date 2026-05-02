#!/usr/bin/env python3
"""Regression tests for 2026-05-02 deep production QA customer-trust bugs."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import api.research_engine as engine
from api.hidden_trigger_detector import detect_hidden_triggers


def _seed_bad_residential_primary():
    return {
        "permit_verdict": "YES",
        "confidence": "high",
        "permits_required": [
            {
                "permit_type": "Mechanical Permit — HVAC System Replacement (Residential)",
                "portal_selection": "Mechanical - HVAC Changeout / Replacement",
                "required": True,
                "notes": "Seeded bad model output that must be repaired for commercial alteration scopes.",
            }
        ],
        "permits_required_logic": [],
        "companion_permits": [],
        "hidden_triggers": [],
    }


def _run_final_reconciliation(job, city="Austin", state="TX", seed=None):
    result = seed or _seed_bad_residential_primary()
    result["_primary_scope"] = engine.detect_primary_scope(job)
    engine.apply_scope_aware_permit_classification(result, job)
    result["hidden_triggers"] = detect_hidden_triggers(job, city, state, result["_primary_scope"], result)
    engine.apply_retail_ti_rulebook(result, job, city, state)
    engine.apply_office_ti_rulebook(result, job, city, state)
    engine.apply_medical_clinic_ti_rulebook(result, job, city, state)
    engine.enforce_ti_min_permits_floor(result, job, city, state)
    engine.enforce_commercial_primary_permit_guardrail(result, job, city, state)
    engine.validate_and_sanitize_permit_result(result, job, city, state)
    return result


def _primary_text(result):
    permits = result.get("permits_required") or []
    assert permits, "expected at least one permit card"
    return " ".join(str(permits[0].get(k, "")) for k in ("permit_type", "portal_selection", "notes")).lower()


def _all_output_text(result):
    return str(result).lower()


def test_office_employee_kitchenette_negation_stays_office_not_restaurant():
    job = (
        "Commercial office tenant improvement for a 4,000 sf suite with demising partitions, "
        "conference rooms, lighting controls, HVAC diffuser relocation, data cabling, and a small "
        "employee kitchenette with sink, refrigerator, and microwave only. No restaurant, no public "
        "food service, no Type I hood, no grease interceptor, and no cooking equipment."
    )

    result = _run_final_reconciliation(job, "Austin", "TX")

    assert result["_primary_scope"] == "commercial_office_ti"
    primary = _primary_text(result)
    assert "office interior alteration" in primary
    assert "restaurant" not in primary
    hidden_ids = {t.get("id") for t in result.get("hidden_triggers", []) if isinstance(t, dict)}
    assert not any(str(tid).startswith("restaurant_") for tid in hidden_ids)
    assert "grease interceptor / fog" not in _all_output_text(result)


def test_retail_dental_products_negation_stays_retail_not_medical_clinic():
    job = (
        "Commercial retail tenant improvement for a store selling dental hygiene products, toothbrushes, "
        "and whitening kits. New shelving, checkout counter, lighting, ADA restroom refresh, and storefront sign. "
        "No dental services, no exam rooms, no x-ray, no nitrous, no sterilization room, and no medical clinic use."
    )

    result = _run_final_reconciliation(job, "Austin", "TX")

    assert result["_primary_scope"] == "commercial_retail_ti"
    primary = _primary_text(result)
    assert "retail interior alteration" in primary or "commercial interior alteration" in primary
    assert "medical clinic" not in primary
    hidden_ids = {t.get("id") for t in result.get("hidden_triggers", []) if isinstance(t, dict)}
    assert not any(str(tid).startswith("medical_clinic_") for tid in hidden_ids)


def test_church_classroom_commercial_alteration_repairs_residential_hvac_primary():
    job = (
        "Commercial church classroom renovation in an existing assembly building: new non-bearing partitions, "
        "doors, ceiling grid, LED lighting, HVAC diffuser relocation, fire alarm notification changes, "
        "accessible route and signage updates. No residential work."
    )

    result = _run_final_reconciliation(job, "Dothan", "AL")

    assert result["_primary_scope"] == "commercial"
    primary = _primary_text(result)
    assert "commercial interior alteration" in primary or "commercial building" in primary
    assert "residential" not in primary
    assert "hvac system replacement" not in primary
    assert result.get("_commercial_primary_permit_guardrail", {}).get("scope") == "commercial"


def test_san_diego_simple_residential_reroof_no_structural_work_not_forced_to_generic_permit():
    # Verified against City of San Diego IB-123: roof covering renewal can be permit-exempt
    # when the existing roof structure/diaphragm is not altered. This was a manual/source
    # review item from deep QA, not a customer-trust bug to flip blindly.
    job = "Residential asphalt shingle roof tear-off and replacement in San Diego, same framing, no structural changes."
    result = {
        "permit_verdict": "NO",
        "confidence": "medium",
        "permits_required": [],
        "permits_required_logic": [],
        "companion_permits": [],
    }

    engine.apply_scope_aware_permit_classification(result, job)

    assert result["permit_verdict"] == "NO"
    assert result.get("permits_required") == []


def test_positive_restaurant_scope_survives_negated_expansion_phrase():
    job = "Commercial restaurant tenant improvement with dining remodel and electrical work; no restaurant expansion and no change to occupant load."
    assert engine.detect_primary_scope(job) == "commercial_restaurant"


def test_true_restaurant_optional_features_negated_do_not_fire_hood_or_grease_triggers():
    job = "Commercial restaurant TI for dining room refresh with lighting and finishes. No Type I hood, no grease interceptor, no cooking equipment."
    triggers = detect_hidden_triggers(job, "Austin", "TX", "commercial_restaurant", {"_primary_scope": "commercial_restaurant"})
    ids = {t.get("id") for t in triggers}
    assert "restaurant_type_i_hood_mechanical_exhaust" not in ids
    assert "restaurant_grease_interceptor_fog_review" not in ids
    assert "restaurant_grease_duct_access_and_cleaning" not in ids


def test_true_dental_clinic_negated_xray_does_not_fire_radiology_trigger():
    job = "Commercial dental clinic TI with operatories, hand sinks, sterilization, and nitrous coordination. No x-ray or radiology equipment."
    triggers = detect_hidden_triggers(job, "Austin", "TX", "commercial_medical_clinic_ti", {"_primary_scope": "commercial_medical_clinic_ti"})
    ids = {t.get("id") for t in triggers}
    assert "medical_clinic_xray_or_radiology_shielding" not in ids


def test_residential_homeschool_classroom_no_commercial_use_stays_residential():
    job = "Residential homeschool classroom renovation with partitions and lighting. No commercial use."
    assert engine.detect_primary_scope(job) == "residential"


def test_new_commercial_warehouse_shell_stays_commercial():
    job = "New commercial warehouse building shell with slab, structural steel, fire sprinklers, and site utilities."
    assert engine.detect_primary_scope(job) == "commercial"
