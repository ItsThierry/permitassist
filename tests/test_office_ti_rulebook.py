#!/usr/bin/env python3
"""Office tenant-improvement coverage tests."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine
from api.fee_realism_guardrail import apply_fee_realism_guardrail
from api.hidden_trigger_detector import detect_hidden_triggers

OFFICE_IDS = {
    "office_demising_partition_life_safety",
    "office_ceiling_lighting_energy_code",
    "office_hvac_zoning_ventilation_balance",
    "office_low_voltage_data_security",
    "office_ada_path_of_travel_restrooms",
    "office_fire_alarm_sprinkler_coordination",
    "office_change_of_use_or_co",
}


def ids(job, city="Phoenix", state="AZ", primary_scope="commercial_office_ti"):
    return {t["id"] for t in detect_hidden_triggers(job, city, state, primary_scope, {})}


def enriched(job="office tenant improvement with demising partitions, open ceiling, lighting, HVAC zoning, data cabling, ADA restroom, sprinkler relocation and fire alarm", city="Phoenix", state="AZ"):
    result = {
        "_primary_scope": "commercial_office_ti",
        "permits_required": [{"permit_type": "Building Permit", "portal_selection": "Building Permit", "required": True, "notes": "seed"}],
        "companion_permits": [],
        "pro_tips": [],
        "watch_out": [],
        "common_mistakes": [],
        "inspections": [],
    }
    result["hidden_triggers"] = detect_hidden_triggers(job, city, state, "commercial_office_ti", result)
    engine.apply_office_ti_rulebook(result, job, city, state)
    engine.enforce_ti_min_permits_floor(result, job, city, state)
    return result


def test_office_scope_stays_office_not_retail_or_medical():
    assert engine.detect_primary_scope("professional office tenant improvement with demising partitions and data cabling") == "commercial_office_ti"
    assert engine.detect_primary_scope("law office buildout with conference rooms, lighting and HVAC balancing") == "commercial_office_ti"


def test_office_ti_fires_office_specific_triggers():
    got = ids("office tenant improvement with demising partitions, new ceiling grid, lighting, HVAC zoning, low voltage data cabling, ADA restroom, sprinkler relocation and fire alarm")
    assert "office_demising_partition_life_safety" in got
    assert "office_ceiling_lighting_energy_code" in got
    assert "office_hvac_zoning_ventilation_balance" in got
    assert "office_low_voltage_data_security" in got
    assert "office_fire_alarm_sprinkler_coordination" in got
    assert "office_ada_path_of_travel_restrooms" in got


def test_office_rulebook_adds_contractor_grade_guidance():
    out = enriched()
    combined = " | ".join(out["pro_tips"] + out["watch_out"] + out["common_mistakes"] + out["inspections"]).lower()
    assert "demising" in combined or "partition" in combined
    assert "lighting" in combined and "energy" in combined
    assert "hvac" in combined and ("balance" in combined or "zoning" in combined)
    assert "low voltage" in combined or "data" in combined
    assert "ada" in combined or "path-of-travel" in combined


def test_office_min_permit_floor_includes_mep_fire_and_low_voltage_companions():
    out = enriched()
    families = {engine._permit_family(p) for p in out["permits_required"]}
    assert {"building", "mechanical", "electrical", "plumbing", "fire"}.issubset(families)
    companion_text = " | ".join(c.get("permit_type", "") for c in out["companion_permits"]).lower()
    assert "low voltage" in companion_text or "data" in companion_text
    assert "fire alarm" in companion_text or "fire sprinkler" in companion_text


def test_office_fee_floor_accounts_for_commercial_ti_complexity():
    result = {
        "fee_range": "$300 electrical only",
        "hidden_triggers": [{"id": trigger_id} for trigger_id in OFFICE_IDS],
    }
    guarded = apply_fee_realism_guardrail(
        result,
        "6500 sqft office tenant improvement with demising partitions, ceiling lighting, HVAC zoning, data cabling, ADA restroom and sprinkler relocation",
        "Phoenix",
        "AZ",
        "commercial_office_ti",
    )
    assert guarded["_fee_adjusted"] is True
    assert guarded["_fee_floor_components"]["scope"] == "commercial_office_ti"
    assert guarded["_fee_floor_components"]["structured_low"] >= 12000
    assert "$300" not in guarded["fee_range"]


def test_office_fee_floor_does_not_treat_incidental_exhaust_hood_as_restaurant_suppression():
    result = {
        "fee_range": "$500 permit fee",
        "hidden_triggers": [],
    }
    guarded = apply_fee_realism_guardrail(
        result,
        "Boston MA office tenant improvement adding break room kitchenette, copy-room exhaust hood, janitor vacuum closet, ceiling grid, lighting controls, HVAC diffuser relocation, data cabling, accessibility, and fire alarm coordination",
        "Boston",
        "MA",
        "commercial_office_ti",
    )
    assert guarded["_fee_adjusted"] is True
    adders = {item["key"] for item in guarded["_fee_floor_components"]["trigger_adders"]}
    assert "hood_fire_suppression" not in adders
    assert "ada_path_of_travel" in adders
    assert "hood-fire-suppression adder" not in guarded["fee_range"]


def test_office_fee_floor_does_not_treat_fire_suppression_or_fume_hood_as_kitchen_hood_adder():
    office_cases = [
        "Boston MA office TI with sprinkler fire suppression upgrade, ceiling grid, lighting controls, accessibility, and fire alarm coordination",
        "Boston MA lab support office TI with fume hood and chemical exhaust for a copy/sample room, ceiling grid, lighting controls, and accessibility",
    ]
    for job in office_cases:
        guarded = apply_fee_realism_guardrail(
            {"fee_range": "$500 permit fee", "hidden_triggers": []},
            job,
            "Boston",
            "MA",
            "commercial_office_ti",
        )
        adders = {item["key"] for item in guarded["_fee_floor_components"]["trigger_adders"]}
        assert "hood_fire_suppression" not in adders
        assert "hood-fire-suppression adder" not in guarded["fee_range"]


def test_clear_suppression_equipment_terms_still_trigger_hood_suppression_adder():
    for phrase in ("ANSUL system", "wet chemical suppression", "kitchen suppression system"):
        guarded = apply_fee_realism_guardrail(
            {"fee_range": "$500 permit fee", "hidden_triggers": []},
            f"Boston MA restaurant tenant improvement with {phrase}, accessibility, and health department review",
            "Boston",
            "MA",
            "commercial_restaurant",
        )
        adders = {item["key"] for item in guarded["_fee_floor_components"]["trigger_adders"]}
        assert "hood_fire_suppression" in adders
        assert "hood-fire-suppression adder" in guarded["fee_range"]


def test_office_fee_floor_respects_explicit_negated_restaurant_equipment_terms():
    negated_office_cases = [
        "Boston MA office tenant improvement with demising partitions, break room kitchenette, ceiling grid, lighting controls, HVAC diffuser relocation, data cabling, accessibility, fire alarm coordination, no Type I hood, no fryer, no griddle, no ANSUL, no wet chemical suppression, and no grease interceptor",
        "Boston MA office TI with break room kitchenette, fire alarm coordination, accessibility, without Type I hood, without fryer, excluding griddle, not adding ANSUL, no wet chemical suppression, and without grease interceptor",
    ]
    for job in negated_office_cases:
        guarded = apply_fee_realism_guardrail(
            {"fee_range": "$500 permit fee", "hidden_triggers": []},
            job,
            "Boston",
            "MA",
            "commercial_office_ti",
        )
        assert guarded["_fee_adjusted"] is True
        adders = {item["key"] for item in guarded["_fee_floor_components"]["trigger_adders"]}
        assert "hood_fire_suppression" not in adders
        assert "grease_interceptor" not in adders
        assert "hood-fire-suppression adder" not in guarded["fee_range"]
        assert "grease-interceptor adder" not in guarded["fee_range"]


def test_fee_floor_handles_mixed_positive_hood_and_negated_grease_scope():
    guarded = apply_fee_realism_guardrail(
        {"fee_range": "$500 permit fee", "hidden_triggers": []},
        "Boston MA restaurant tenant improvement with Type I hood, commercial cooking line, fryer, ANSUL wet chemical suppression, accessibility, health department review, and no grease interceptor",
        "Boston",
        "MA",
        "commercial_restaurant",
    )
    adders = {item["key"] for item in guarded["_fee_floor_components"]["trigger_adders"]}
    assert "hood_fire_suppression" in adders
    assert "grease_interceptor" not in adders


def test_restaurant_fee_floor_still_adds_hood_suppression_for_type_i_commercial_cooking_scope():
    result = {
        "fee_range": "$500 permit fee",
        "hidden_triggers": [],
    }
    guarded = apply_fee_realism_guardrail(
        result,
        "Boston MA restaurant tenant improvement with Type I hood, commercial cooking equipment, fryer, grease interceptor, fire suppression, accessibility, and health department review",
        "Boston",
        "MA",
        "commercial_restaurant",
    )
    assert guarded["_fee_adjusted"] is True
    adders = {item["key"] for item in guarded["_fee_floor_components"]["trigger_adders"]}
    assert "hood_fire_suppression" in adders
    assert "grease_interceptor" in adders
    assert "hood-fire-suppression adder" in guarded["fee_range"]


def test_restaurant_fee_floor_still_adds_hood_suppression_for_commercial_cooking_equipment_without_type_i_phrase():
    guarded = apply_fee_realism_guardrail(
        {"fee_range": "$500 permit fee", "hidden_triggers": []},
        "Boston MA restaurant tenant improvement with commercial cooking line, fryer, griddle, ANSUL wet chemical suppression, grease interceptor, accessibility, and health department review",
        "Boston",
        "MA",
        "commercial_restaurant",
    )
    adders = {item["key"] for item in guarded["_fee_floor_components"]["trigger_adders"]}
    assert "hood_fire_suppression" in adders
    assert "grease_interceptor" in adders


def test_retail_medical_and_residential_do_not_include_office_triggers():
    retail = ids("retail TI with storefront sign and sales floor", "Phoenix", "AZ", "commercial_retail_ti")
    medical = ids("medical clinic TI with exam rooms, med gas and x-ray", "Austin", "TX", "commercial_medical_clinic_ti")
    residential = ids("bathroom remodel with new sink and electrical", "Austin", "TX", "residential")
    assert not (retail & OFFICE_IDS)
    assert not (medical & OFFICE_IDS)
    assert not (residential & OFFICE_IDS)
