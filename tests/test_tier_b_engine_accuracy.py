#!/usr/bin/env python3
"""Tier B engine accuracy tests: CA state packs + scope-aware permits."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import api.research_engine as engine
from permit_decision import apply_permit_decision_contract


RESIDENTIAL_THIN_CASES = [
    ("Knoxville", "TN", "residential room addition with new bedroom and bathroom", "addition"),
    ("Sedona", "AZ", "residential swimming pool and spa", "pool"),
    ("Spokane", "WA", "residential roof tear-off and reroof shingles", "roof"),
]


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

    assert len(result.get("expert_notes", [])) >= 8
    assert "tdlr" in text
    assert "tsbpe" in text
    assert "title 24" not in text


# ── Scope-aware permit classification (5 tests) ──────────────────────────────

def test_scope_hvac_condenser_changeout_is_one_mechanical_permit():
    result = engine.apply_scope_aware_permit_classification(_blank_result(), "like-for-like HVAC condenser changeout")
    permits = _permit_types(result)

    assert len(permits) == 1
    assert "Mechanical Permit" in permits[0]
    assert result["companion_permits"] == []
    assert result["permits_required_logic"][0]["included_because"]


def test_scope_rooftop_hvac_unit_is_mechanical_not_roofing():
    result = engine.apply_scope_aware_permit_classification(_blank_result(), "replace a rooftop HVAC unit, no other work")
    permits = _permit_types(result)

    assert len(permits) == 1
    assert "Mechanical Permit" in permits[0]
    assert "Roofing" not in permits[0]
    assert result["companion_permits"] == []


def test_permit_family_prefers_label_over_suppression_notes():
    assert engine._permit_family({
        "permit_type": "Mechanical Permit — HVAC Equipment Changeout (Residential)",
        "portal_selection": "Mechanical - HVAC Changeout / Replacement",
        "notes": "Companion permits are suppressed unless panel work, gas-line modification, or new fixtures are explicit.",
    }) == "mechanical"
    assert engine._permit_family({
        "permit_type": "Roofing Permit — Tear-Off / Re-Roof",
        "portal_selection": "Roofing - Residential Re-Roof",
        "notes": "No companion permit unless skylights, solar, or structural work are added.",
    }) == "roofing"


def test_scope_commercial_conversion_trade_list_leads_with_building_and_named_trades():
    for city, state in (("Austin", "TX"), ("Boston", "MA")):
        job = f"4,000 sq ft retail converted into a fitness studio, {city} {state} — restrooms, showers, HVAC, electrical"
        scope_contract = engine.build_scope_contract(job, city, state, job_category="commercial")
        result = engine.apply_scope_aware_permit_classification(_blank_result(), job, scope_contract)
        engine.enforce_ti_min_permits_floor(result, job, city, state)
        engine.apply_residential_permit_name_specificity(result, job, city, state)
        engine.enforce_commercial_primary_permit_guardrail(result, job, city, state)
        result = apply_permit_decision_contract(result, job, city, state, scope_contract)
        permits = _permit_types(result)
        permits_l = " | ".join(permits).lower()

        assert scope_contract["family"] == "commercial_ti"
        assert result["_primary_scope"] in {"commercial", "commercial_retail_ti"}
        assert permits[0].startswith("Building Permit — Commercial") or "Tenant Improvement" in permits[0]
        assert "residential" not in permits_l
        assert "building" in permits_l
        assert "plumbing" in permits_l
        assert "mechanical" in permits_l
        assert "electrical" in permits_l
        logic_l = " | ".join(str(item.get("permit_type", "")) for item in result.get("permits_required_logic", []) if isinstance(item, dict)).lower()
        for family in ("building", "plumbing", "mechanical", "electrical"):
            assert family in logic_l


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


def test_residential_stress_quality_floor_fills_thin_addition_pool_roof_cases():
    for city, state, job_type, expected_scope in RESIDENTIAL_THIN_CASES:
        result = engine.apply_residential_stress_quality_floor({
            "permit_verdict": "YES",
            "approval_timeline": {"simple": "varies"},
            "inspections": ["Final inspection"],
        }, job_type, city, state)
        assert result["approval_timeline"].get("complex")
        assert len(result["inspections"]) >= 3
        assert result["what_to_bring"]
        assert f"residential_{expected_scope}_stress_quality_floor" in result["_quality_floor_notes"]


def test_residential_stress_quality_floor_does_not_touch_commercial_ti():
    result = {"approval_timeline": {"simple": "varies"}, "inspections": ["Final inspection"]}
    unchanged = engine.apply_residential_stress_quality_floor(result.copy(), "commercial office tenant improvement", "Dallas", "TX")
    assert unchanged == result


def test_residential_stress_quality_floor_replaces_non_list_note_metadata():
    result = engine.apply_residential_stress_quality_floor({
        "approval_timeline": {},
        "inspections": [],
        "_quality_floor_notes": "bad-shape",
    }, "residential swimming pool", "Sedona", "AZ")
    assert result["_quality_floor_notes"] == ["residential_pool_stress_quality_floor"]


def test_residential_stress_quality_floor_skips_no_permit_results():
    for verdict in ("NO", "NOT REQUIRED", "NONE", "EXEMPT", "NOT NEEDED"):
        result = {"permit_verdict": verdict, "approval_timeline": {}, "inspections": []}
        unchanged = engine.apply_residential_stress_quality_floor(result.copy(), "residential roof repair", "Spokane", "WA")
        assert unchanged == result


def test_residential_stress_quality_floor_is_idempotent():
    result = {
        "permit_verdict": "YES",
        "approval_timeline": {},
        "inspections": [],
        "_primary_scope": "residential",
    }
    once = engine.apply_residential_stress_quality_floor(result, "residential room addition", "Knoxville", "TN")
    twice = engine.apply_residential_stress_quality_floor(once, "residential room addition", "Knoxville", "TN")
    assert twice["_quality_floor_notes"] == ["residential_addition_stress_quality_floor"]
    assert len(twice["inspections"]) == len(set(twice["inspections"]))


def test_residential_stress_quality_floor_does_not_touch_commercial_mixed_pool_scope():
    result = {"permit_verdict": "YES", "approval_timeline": {}, "inspections": []}
    unchanged = engine.apply_residential_stress_quality_floor(result.copy(), "commercial pool deck at residential complex", "Dallas", "TX")
    assert unchanged == result


def test_residential_stress_quality_floor_normalizes_malformed_primary_scope():
    result = engine.apply_residential_stress_quality_floor({
        "permit_verdict": "YES",
        "approval_timeline": {},
        "inspections": [],
        "_primary_scope": ["bad-shape"],
    }, "residential swimming pool", "Sedona", "AZ")
    assert result["_primary_scope"] == "residential"
    assert result["_quality_floor_notes"] == ["residential_pool_stress_quality_floor"]
