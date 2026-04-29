#!/usr/bin/env python3
"""A3 companion-permit floor tests for commercial office/retail TI."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine


def _base_result(primary_scope, permits):
    return {
        "permit_verdict": "YES",
        "_primary_scope": primary_scope,
        "permits_required": [
            {"permit_type": p, "portal_selection": p, "required": True, "notes": "seed"}
            for p in permits
        ],
        "permits_required_logic": [],
        "companion_permits": [],
        "sources": [],
    }


def _types(result):
    return " | ".join(p.get("permit_type", "") for p in result.get("permits_required", [])).lower()


def _families(result):
    return {engine._permit_family(p) for p in result.get("permits_required", [])}


def test_phoenix_office_ti_returns_floor_and_mep_families():
    r = _base_result("commercial_office_ti", [
        "Building Permit — Commercial Tenant Improvement / Interior Alteration",
        "Electrical Permit — Commercial Interior Alteration",
        "Fire Sprinkler Permit — Head Relocation / Modification",
    ])
    out = engine.enforce_ti_min_permits_floor(r, "office tenant improvement with restroom and HVAC", "Phoenix", "AZ")
    assert len(out["permits_required"]) >= 4
    assert {"mechanical", "plumbing", "electrical"}.issubset(_families(out))


def test_clark_county_office_ti_returns_floor():
    r = _base_result("commercial_office_ti", ["Building Permit", "Electrical Permit", "Mechanical Permit"])
    out = engine.enforce_ti_min_permits_floor(r, "office TI", "Las Vegas", "NV")
    assert len(out["permits_required"]) >= 4
    assert "plumbing" in _families(out)


def test_seattle_office_ti_returns_floor():
    r = _base_result("commercial_office_ti", ["Building Permit", "Mechanical Permit", "Electrical Permit"])
    out = engine.enforce_ti_min_permits_floor(r, "office tenant improvement", "Seattle", "WA")
    assert len(out["permits_required"]) >= 4
    assert "plumbing" in _families(out)


def test_los_angeles_office_ti_returns_floor():
    r = _base_result("commercial_office_ti", ["Building Permit", "Plumbing Permit", "Electrical Permit"])
    out = engine.enforce_ti_min_permits_floor(r, "professional office tenant improvement", "Los Angeles", "CA")
    assert len(out["permits_required"]) >= 4
    assert "mechanical" in _families(out)


def test_dallas_retail_ti_returns_floor_including_sign_permit():
    r = _base_result("commercial_retail_ti", [
        "Building Permit — Commercial Tenant Improvement / Interior Alteration",
        "Electrical Permit — Commercial Lighting and Branch Circuit Work",
        "Sign Permit — Commercial Wall Sign / Storefront Signage",
    ])
    out = engine.enforce_ti_min_permits_floor(r, "retail tenant improvement with new storefront signage", "Dallas", "TX")
    assert len(out["permits_required"]) >= 4
    assert "sign" in _families(out)
    assert "mechanical" in _families(out)


def test_phoenix_retail_ti_returns_floor():
    r = _base_result("commercial_retail_ti", ["Building Permit", "Electrical Permit"])
    out = engine.enforce_ti_min_permits_floor(r, "retail TI boutique with sign changes", "Phoenix", "AZ")
    assert len(out["permits_required"]) >= 4
    assert {"building", "mechanical", "electrical", "sign"}.issubset(_families(out))


def test_restaurant_ti_regression_untouched_at_five_permits():
    r = _base_result("commercial_restaurant", ["Building Permit", "Mechanical Permit", "Plumbing Permit", "Electrical Permit", "Health Department Approval"])
    out = engine.enforce_ti_min_permits_floor(r, "restaurant TI with hood and grease interceptor", "Phoenix", "AZ")
    assert len(out["permits_required"]) == 5
    assert "_a3_min_permits" not in out


def test_residential_adu_regression_no_ti_floor():
    r = _base_result("residential_adu", ["Building Permit", "Electrical Permit", "Plumbing Permit", "Mechanical Permit"])
    out = engine.enforce_ti_min_permits_floor(r, "garage ADU conversion", "Los Angeles", "CA")
    assert len(out["permits_required"]) == 4
    assert "_a3_min_permits" not in out


def test_simple_trade_regression_water_heater_hvac_reroof_stay_one_permit():
    cases = [
        ("residential", "water heater replacement", "Plumbing Permit — Water Heater Replacement"),
        ("residential", "HVAC condenser changeout", "Mechanical Permit — HVAC Equipment Changeout"),
        ("residential", "roof tear-off and reroof", "Roofing Permit — Tear-Off / Re-Roof"),
    ]
    for scope, job, permit in cases:
        r = _base_result(scope, [permit])
        out = engine.enforce_ti_min_permits_floor(r, job, "Phoenix", "AZ")
        assert len(out["permits_required"]) == 1
        assert "_a3_min_permits" not in out


def test_office_ti_fire_added_when_triggered():
    r = _base_result("commercial_office_ti", ["Building Permit", "Mechanical Permit", "Plumbing Permit", "Electrical Permit"])
    out = engine.enforce_ti_min_permits_floor(r, "office TI with change of occupancy and sprinkler relocation", "Seattle", "WA")
    assert len(out["permits_required"]) >= 5
    assert "fire" in _families(out)
