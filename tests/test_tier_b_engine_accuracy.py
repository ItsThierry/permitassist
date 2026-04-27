#!/usr/bin/env python3
"""Tier B engine accuracy tests: CA state packs + scope-aware permits."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine


def _blank_result():
    return {
        "permit_verdict": "YES",
        "permits_required": [
            {"permit_type": "Mechanical Permit — Generic", "required": True, "notes": "model output"}
        ],
        "companion_permits": [
            {"permit_type": "Electrical Permit", "reason": "Required for disconnect", "certainty": "almost_certain"}
        ],
        "sources": [],
    }


def _notes_text(result):
    return json.dumps(result.get("expert_notes", []), sort_keys=True).lower()


def _permit_types(result):
    return [p.get("permit_type", "") for p in result.get("permits_required", [])]


# ── California expert pack (3 tests) ─────────────────────────────────────────

def test_ca_state_pack_pasadena_adu_contains_required_rules():
    result = engine.apply_state_expert_pack(_blank_result(), "Pasadena", "CA", "garage ADU conversion")
    text = _notes_text(result)

    assert "60 days" in text
    assert "ab 881" in text
    assert "65852.2" in text
    assert "impact fees waived" in text
    assert "title 24" in text
    assert "cf1r" in text
    assert "cal fire" in text
    assert "vhfhsz" in text
    assert "pasadena water and power" in text or "pwp" in text
    assert "bungalow heaven" in text


def test_ca_state_pack_is_appended_for_non_adu_ca_jobs():
    result = engine.apply_state_expert_pack(_blank_result(), "Los Angeles", "CA", "HVAC condenser changeout")
    text = _notes_text(result)

    assert result.get("expert_notes")
    assert "title 24" in text
    assert "cal fire" in text
    assert "ladwp" in text


def test_tx_state_pack_added_without_california_rules():
    result = engine.apply_state_expert_pack(_blank_result(), "Houston", "TX", "HVAC condenser changeout")
    text = _notes_text(result)

    assert len(result.get("expert_notes", [])) == 8
    assert "tdlr" in text
    assert "tsbpe" in text
    assert "california" not in text
    assert "title 24" not in text


# ── Scope-aware permit classification (5 tests) ──────────────────────────────

def test_scope_hvac_condenser_changeout_is_one_mechanical_permit():
    result = engine.apply_scope_aware_permit_classification(_blank_result(), "like-for-like HVAC condenser changeout")
    permits = _permit_types(result)

    assert len(permits) == 1
    assert "Mechanical Permit" in permits[0]
    assert result["companion_permits"] == []
    assert result["permits_required_logic"][0]["included_because"]


def test_scope_hvac_with_panel_upgrade_is_mechanical_plus_electrical():
    result = engine.apply_scope_aware_permit_classification(_blank_result(), "HVAC system replacement with 200 amp panel upgrade")
    permits = " | ".join(_permit_types(result)).lower()

    assert len(result["permits_required"]) == 2
    assert "mechanical" in permits
    assert "electrical" in permits
    assert "panel" in permits
    assert len(result["permits_required_logic"]) == 2


def test_scope_adu_conversion_has_building_mep_permits():
    result = engine.apply_scope_aware_permit_classification(_blank_result(), "garage ADU conversion with kitchen and bath")
    permits = " | ".join(_permit_types(result)).lower()

    assert 4 <= len(result["permits_required"]) <= 5
    assert "building" in permits
    assert "mechanical" in permits
    assert "electrical" in permits
    assert "plumbing" in permits
    assert len(result["permits_required_logic"]) == len(result["permits_required"])


def test_scope_roof_tearoff_reroof_is_one_roofing_permit():
    result = engine.apply_scope_aware_permit_classification(_blank_result(), "roof tear-off and reroof shingles")
    permits = _permit_types(result)

    assert len(permits) == 1
    assert "Roofing Permit" in permits[0]
    assert result["companion_permits"] == []


def test_scope_solar_battery_is_two_city_permits_with_utility_companion():
    result = engine.apply_scope_aware_permit_classification(_blank_result(), "rooftop solar PV with battery backup")
    permits = " | ".join(_permit_types(result)).lower()
    companions = json.dumps(result.get("companion_permits", [])).lower()

    assert len(result["permits_required"]) == 2
    assert "building" in permits
    assert "electrical" in permits
    assert "battery" in permits or "ess" in permits
    assert "utility interconnection" in companions
    assert len(result["permits_required_logic"]) == 2
