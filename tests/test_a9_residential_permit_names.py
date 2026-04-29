#!/usr/bin/env python3
"""A9 residential permit-name specificity tests."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine


def _base_result(primary_scope="residential", permits=None):
    permits = permits or ["Residential Alteration"]
    return {
        "permit_verdict": "YES",
        "_primary_scope": primary_scope,
        "permits_required": [
            {"permit_type": p, "portal_selection": p, "required": True, "notes": "seed"}
            for p in permits
        ],
        "permits_required_logic": [{"permit_type": permits[0], "included_because": "seed", "scope_trigger": "seed"}],
        "companion_permits": [],
        "sources": [],
    }


def _names(result):
    return [p.get("permit_type", "") for p in result.get("permits_required", [])]


def test_phoenix_detached_adu_specific_name():
    out = engine.apply_residential_permit_name_specificity(
        _base_result("residential_adu", ["Residential Alteration"]),
        "new detached ADU in backyard", "Phoenix", "AZ"
    )
    assert _names(out)[0] == "Detached ADU Building Permit"
    assert "Residential Alteration" not in _names(out)[0]


def test_seattle_dadu_uses_dadu_name():
    out = engine.apply_residential_permit_name_specificity(
        _base_result("residential_adu", ["ADU Conversion"]),
        "new detached accessory dwelling unit / DADU", "Seattle", "WA"
    )
    assert "DADU" in _names(out)[0]


def test_los_angeles_hillside_adu_name_contains_hillside_and_adu():
    out = engine.apply_residential_permit_name_specificity(
        _base_result("residential_adu", ["Hillside"]),
        "hillside detached ADU with slope/geotech", "Los Angeles", "CA"
    )
    assert "Hillside" in _names(out)[0]
    assert "ADU" in _names(out)[0]


def test_los_angeles_garage_to_jadu_name():
    out = engine.apply_residential_permit_name_specificity(
        _base_result("residential_adu", ["Garage Conversion"]),
        "convert attached garage to JADU / junior accessory dwelling", "Los Angeles", "CA"
    )
    assert _names(out)[0] == "JADU Conversion Permit"


def test_los_angeles_kitchen_remodel_with_panel_specific_names():
    out = engine.apply_residential_permit_name_specificity(
        _base_result("residential", ["Residential Alteration", "Electrical"]),
        "kitchen remodel plus 200 amp panel service upgrade", "Los Angeles", "CA"
    )
    assert "Residential Alteration — Kitchen Remodel" in _names(out)
    assert "Electrical Permit — Service Upgrade (200A)" in _names(out)


def test_phoenix_water_heater_specific_name_and_one_permit():
    out = engine.apply_residential_permit_name_specificity(
        _base_result("residential", ["Plumbing"]),
        "replace residential water heater like for like", "Phoenix", "AZ"
    )
    assert len(out["permits_required"]) == 1
    assert "Water Heater" in _names(out)[0]


def test_phoenix_hvac_changeout_specific_name_and_one_permit():
    out = engine.apply_residential_permit_name_specificity(
        _base_result("residential", ["Mechanical"]),
        "HVAC condenser changeout replacement", "Phoenix", "AZ"
    )
    assert len(out["permits_required"]) == 1
    assert "HVAC" in _names(out)[0]


def test_vegas_reroof_specific_name():
    out = engine.apply_residential_permit_name_specificity(
        _base_result("residential", ["Building Permit"]),
        "residential reroof tear-off shingles", "Las Vegas", "NV"
    )
    assert "Reroof" in _names(out)[0] or "Roofing" in _names(out)[0]


def test_dallas_window_replacement_specific_name():
    out = engine.apply_residential_permit_name_specificity(
        _base_result("residential", ["Building Permit"]),
        "replace 12 windows and exterior doors", "Dallas", "TX"
    )
    assert "Window" in _names(out)[0]


def test_dallas_foundation_repair_specific_name():
    out = engine.apply_residential_permit_name_specificity(
        _base_result("residential", ["Building Permit"]),
        "pier and beam foundation repair", "Dallas", "TX"
    )
    assert "Foundation" in _names(out)[0]


def test_seattle_second_floor_deck_specific_name():
    out = engine.apply_residential_permit_name_specificity(
        _base_result("residential", ["Building Permit"]),
        "new 240 sf second floor deck", "Seattle", "WA"
    )
    assert "Deck" in _names(out)[0]


def test_commercial_office_ti_naming_unchanged():
    before = _base_result("commercial_office_ti", ["Building Permit — Commercial Tenant Improvement"])
    out = engine.apply_residential_permit_name_specificity(
        before, "office tenant improvement with restroom", "Phoenix", "AZ"
    )
    assert _names(out) == ["Building Permit — Commercial Tenant Improvement"]
    assert "_a9_residential_permit_names" not in out


def test_specific_simple_trade_name_not_renamed_or_duplicated():
    cases = [
        ("Plumbing Permit — Water Heater Replacement", "replace water heater"),
        ("Mechanical Permit — HVAC Equipment Replacement", "HVAC changeout"),
    ]
    for permit_name, job in cases:
        out = engine.apply_residential_permit_name_specificity(
            _base_result("residential", [permit_name]), job, "Phoenix", "AZ"
        )
        assert _names(out) == [permit_name]
        assert len(out["permits_required"]) == 1
