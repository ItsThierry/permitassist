#!/usr/bin/env python3
"""A6 retail TI rulebook expansion tests."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine
from api.hidden_trigger_detector import detect_hidden_triggers

RETAIL_IDS = {
    "retail_storefront_facade_alteration",
    "retail_signage_permit",
    "retail_ada_path_of_travel_20pct",
    "retail_egress_occupant_load_recalc",
    "retail_fire_alarm_sprinkler_modifications",
    "retail_energy_code_lighting_hvac",
    "retail_row_encroachment_outdoor_display",
    "retail_health_food_handling",
    "retail_change_of_use_or_occupancy",
    "retail_cannabis_alcohol_special_use",
}


def ids(job, city="Phoenix", state="AZ", primary_scope="commercial_retail_ti"):
    return {t["id"] for t in detect_hidden_triggers(job, city, state, primary_scope, {})}


def enriched(job="retail tenant improvement with new storefront sign, lighting, HVAC, and restroom", city="Phoenix", state="AZ"):
    result = {
        "_primary_scope": "commercial_retail_ti",
        "permits_required": [{"permit_type": "Building Permit", "portal_selection": "Building Permit", "required": True, "notes": "seed"}],
        "companion_permits": [],
        "pro_tips": [],
        "watch_out": [],
        "common_mistakes": [],
        "inspections": [],
    }
    result["hidden_triggers"] = detect_hidden_triggers(job, city, state, "commercial_retail_ti", result)
    engine.apply_retail_ti_rulebook(result, job, city, state)
    engine.enforce_ti_min_permits_floor(result, job, city, state)
    return result


def test_phoenix_retail_ti_fires_signage_and_ada():
    got = ids("Phoenix retail TI boutique $180,000 buildout with new sign and cash wrap", "Phoenix", "AZ")
    assert "retail_signage_permit" in got
    assert "retail_ada_path_of_travel_20pct" in got


def test_vegas_retail_ti_fires_signage_and_storefront_facade():
    got = ids("Las Vegas retail tenant improvement with storefront facade, awning and window changes", "Las Vegas", "NV")
    assert "retail_signage_permit" in got
    assert "retail_storefront_facade_alteration" in got


def test_seattle_retail_ti_fires_energy_code_lighting_hvac_wsec_c():
    got = ids("Seattle retail TI with new lighting, HVAC controls and storefront glazing WSEC-C", "Seattle", "WA")
    assert "retail_energy_code_lighting_hvac" in got


def test_la_retail_ti_fires_ada_and_storefront_facade_specific_plan():
    got = ids("Los Angeles retail TI in Specific Plan area with storefront facade windows and $250,000 alteration", "Los Angeles", "CA")
    assert "retail_ada_path_of_travel_20pct" in got
    assert "retail_storefront_facade_alteration" in got


def test_dallas_retail_ti_fires_signage_and_ada_tas():
    got = ids("Dallas retail tenant improvement under TAS with new sign, sales floor and $120,000 remodel", "Dallas", "TX")
    assert "retail_signage_permit" in got
    assert "retail_ada_path_of_travel_20pct" in got


def test_cannabis_retail_fires_special_use():
    assert "retail_cannabis_alcohol_special_use" in ids("cannabis dispensary retail TI with security plan", "Los Angeles", "CA")


def test_food_retail_fires_health_food_handling():
    assert "retail_health_food_handling" in ids("grocery convenience store retail TI with cafe coffee and walk-in cooler", "Phoenix", "AZ")


def test_warehouse_to_retail_fires_change_of_use_or_occupancy():
    assert "retail_change_of_use_or_occupancy" in ids("warehouse to retail change of use tenant improvement with new CO", "Dallas", "TX")


def test_sidewalk_display_fires_row_encroachment():
    assert "retail_row_encroachment_outdoor_display" in ids("retail TI with sidewalk display and sandwich-board sign", "Seattle", "WA")


def test_retail_pro_tips_has_at_least_three_specific_items():
    out = enriched()
    text = " | ".join(out["pro_tips"]).lower()
    assert len(out["pro_tips"]) >= 3
    assert "master sign" in text and "parking" in text and "health" in text


def test_retail_watch_out_has_at_least_three_specific_items():
    out = enriched()
    text = " | ".join(out["watch_out"]).lower()
    assert len(out["watch_out"]) >= 3
    assert "master sign" in text and "parking" in text and "landlord" in text


def test_retail_common_mistakes_has_at_least_three_specific_items():
    out = enriched()
    text = " | ".join(out["common_mistakes"]).lower()
    assert len(out["common_mistakes"]) >= 3
    assert "facade" in text and "ada path" in text and "energy" in text


def test_retail_inspections_include_storefront_sign_fire_energy():
    out = enriched("retail TI with storefront sign, ceiling sprinkler relocation, fire alarm, lighting and HVAC")
    text = " | ".join(out["inspections"]).lower()
    assert "storefront" in text and "sign" in text and "fire alarm" in text and "energy" in text


def test_restaurant_ti_does_not_include_retail_specific_triggers():
    got = ids("restaurant TI with hood, grease interceptor, dining and patio", "Phoenix", "AZ", "commercial_restaurant")
    assert not (got & RETAIL_IDS)


def test_residential_adu_does_not_include_retail_triggers():
    got = ids("new detached ADU with kitchen and bath", "Los Angeles", "CA", "residential_adu")
    assert not (got & RETAIL_IDS)


def test_a3_floor_still_applies_retail_returns_at_least_four_permits():
    out = enriched("retail TI boutique with new storefront sign and lighting", "Phoenix", "AZ")
    assert len(out["permits_required"]) >= 4
    families = {engine._permit_family(p) for p in out["permits_required"]}
    assert {"building", "mechanical", "electrical", "sign"}.issubset(families)


def test_retail_companion_permits_surface_sign_and_facade_from_triggers():
    out = enriched("retail tenant improvement with storefront facade, awning, window changes and illuminated sign")
    text = " | ".join(c.get("permit_type", "") for c in out["companion_permits"]).lower()
    assert "sign permit" in text
    assert "facade alteration permit" in text
