#!/usr/bin/env python3
"""B4 rulebook depth meter / per-jurisdiction confidence indicator."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine


def _out(job, city, state, office=None):
    result = {
        "confidence": "high",
        "applying_office": office or f"{city} Building Department",
        "apply_url": "https://example.gov/permits",
    }
    before_confidence = result["confidence"]
    out = engine.apply_rulebook_depth(result, job, city, state)
    assert out["confidence"] == before_confidence  # B4 must not mutate existing confidence
    return out


def test_phoenix_restaurant_ti_deep():
    out = _out("restaurant TI with hood and grease interceptor", "Phoenix", "AZ")
    assert out["rulebook_depth"] == "DEEP"
    assert "verified rulebook depth for Phoenix on restaurant ti" in out["_rulebook_depth_disclaimer"]


def test_la_hillside_adu_deep():
    assert _out("hillside ADU with geotech", "Los Angeles", "CA")["rulebook_depth"] == "DEEP"


def test_seattle_dadu_deep():
    assert _out("DADU detached accessory dwelling unit", "Seattle", "WA")["rulebook_depth"] == "DEEP"


def test_dallas_window_replacement_deep():
    assert _out("residential window replacement", "Dallas", "TX")["rulebook_depth"] == "DEEP"


def test_phoenix_kitchen_remodel_deep():
    assert _out("kitchen remodel", "Phoenix", "AZ")["rulebook_depth"] == "DEEP"


def test_bakersfield_restaurant_ti_medium():
    out = _out("restaurant TI", "Bakersfield", "CA")
    assert out["rulebook_depth"] == "MEDIUM"
    assert "state-amendment" in out["_rulebook_depth_reason"]


def test_honolulu_water_heater_state_default_no_hi_amendments():
    assert _out("water heater replacement", "Honolulu", "HI")["rulebook_depth"] == "STATE_DEFAULT"


def test_susanville_reroof_medium():
    assert _out("reroof existing house", "Susanville", "CA")["rulebook_depth"] == "MEDIUM"


def test_los_angeles_tribal_land_scope_state_default_edge_case():
    out = _out("tribal-land cultural resource permit coordination", "Los Angeles", "CA", office="Los Angeles Department of Building and Safety")
    assert out["rulebook_depth"] == "STATE_DEFAULT"
    assert "verify with Los Angeles Department of Building and Safety" in out["_rulebook_depth_disclaimer"]


def test_random_small_texas_city_restaurant_ti_medium():
    assert _out("restaurant tenant improvement", "Abilene", "TX")["rulebook_depth"] == "MEDIUM"


def test_tier_reasoning_populated_and_references_expected_basis():
    cases = [
        _out("restaurant TI", "Phoenix", "AZ"),
        _out("restaurant TI", "Bakersfield", "CA"),
        _out("tribal-land permit", "Honolulu", "HI"),
    ]
    for out in cases:
        reason = out.get("_rulebook_depth_reason") or ""
        assert reason
        assert any(token in reason for token in ("GTM-test", "state-amendment", "fallback"))


def test_disclaimer_copy_per_tier():
    deep = _out("restaurant TI", "Phoenix", "AZ")
    medium = _out("restaurant TI", "Bakersfield", "CA")
    default = _out("water heater", "Honolulu", "HI", office="Honolulu DPP")
    assert deep["_rulebook_depth_disclaimer"].startswith("Confidence: HIGH — verified rulebook depth")
    assert medium["_rulebook_depth_disclaimer"] == "Confidence: MEDIUM — verified jurisdiction + state code, scope-specific rulebook is general"
    assert default["_rulebook_depth_disclaimer"] == "Confidence: BASELINE — verified jurisdiction, but scope-specific rulebook is generic; verify with Honolulu DPP"


def test_deep_city_untested_non_edge_scope_is_medium():
    assert _out("commercial warehouse mezzanine expansion", "Dallas", "TX")["rulebook_depth"] == "MEDIUM"
