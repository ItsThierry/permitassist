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


def test_retail_medical_and_residential_do_not_include_office_triggers():
    retail = ids("retail TI with storefront sign and sales floor", "Phoenix", "AZ", "commercial_retail_ti")
    medical = ids("medical clinic TI with exam rooms, med gas and x-ray", "Austin", "TX", "commercial_medical_clinic_ti")
    residential = ids("bathroom remodel with new sink and electrical", "Austin", "TX", "residential")
    assert not (retail & OFFICE_IDS)
    assert not (medical & OFFICE_IDS)
    assert not (residential & OFFICE_IDS)
