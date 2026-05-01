#!/usr/bin/env python3
"""Batch 1 commercial primary-permit guardrail regression tests."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import api.research_engine as engine
from api.hidden_trigger_detector import detect_hidden_triggers


def _run_batch1_layers(job, city, state, seed=None):
    result = seed or {
        "permit_verdict": "YES",
        "confidence": "high",
        "job_summary": f"Commercial tenant improvement: {job}",
        "permits_required": [
            {
                "permit_type": "Mechanical Permit — HVAC System Replacement (Residential)",
                "portal_selection": "Mechanical - HVAC Changeout / Replacement",
                "required": True,
                "notes": "Seeded bad model output that used to leak into the UI primary card.",
            }
        ],
        "permits_required_logic": [],
        "companion_permits": [],
        "pro_tips": [],
        "watch_out": [],
        "common_mistakes": [],
        "inspections": [],
    }
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


def _permit_blob(result):
    return " | ".join(
        " ".join(str(p.get(k, "")) for k in ("permit_type", "portal_selection", "notes"))
        for p in result.get("permits_required", [])
        if isinstance(p, dict)
    ).lower()


def _families(result):
    return [engine._permit_family(p) for p in result.get("permits_required", [])]


def _assert_commercial_ti_primary(result):
    assert result.get("permits_required"), "commercial result must have structured required permits"
    primary = result["permits_required"][0]
    primary_text = " ".join(str(primary.get(k, "")) for k in ("permit_type", "portal_selection", "notes")).lower()
    assert engine._permit_family(primary) == "building"
    assert "tenant improvement" in primary_text or "interior alteration" in primary_text or "change of use" in primary_text
    assert "residential" not in primary_text
    assert "hvac changeout" not in primary_text
    assert "hvac system replacement" not in primary_text


def test_cambridge_medical_clinic_ti_blocks_residential_hvac_primary():
    job = "commercial tenant improvement converting retail to medical clinic with exam rooms, hand sinks, HVAC ventilation, ADA restroom, fire alarm and x-ray room"
    out = _run_batch1_layers(job, "Cambridge", "MA")

    _assert_commercial_ti_primary(out)
    assert out["_primary_scope"] == "commercial_medical_clinic_ti"
    assert {"building", "mechanical", "electrical", "plumbing", "fire"}.issubset(set(_families(out)))
    assert "residential" not in _permit_blob(out)
    companion_text = " | ".join(c.get("permit_type", "") for c in out.get("companion_permits", [])).lower()
    assert "health" in companion_text or "licensing" in companion_text


def test_denver_office_ti_blocks_residential_hvac_primary():
    job = "commercial office TI with demising partitions, conference rooms, ceiling grid, lighting controls, data cabling, HVAC diffuser relocation, ADA restroom, fire alarm devices and sprinkler relocation"
    out = _run_batch1_layers(job, "Denver", "CO")

    _assert_commercial_ti_primary(out)
    assert out["_primary_scope"] == "commercial_office_ti"
    assert {"building", "mechanical", "electrical", "plumbing", "fire"}.issubset(set(_families(out)))
    assert "residential" not in _permit_blob(out)
    companion_text = " | ".join(c.get("permit_type", "") for c in out.get("companion_permits", [])).lower()
    assert "low-voltage" in companion_text or "low voltage" in companion_text or "data" in companion_text


def test_chicago_restaurant_ti_keeps_commercial_building_primary_and_food_service_companions():
    job = "commercial restaurant TI converting retail to restaurant with Type I hood, grease interceptor, kitchen plumbing, electrical panels, fire suppression and health department review"
    out = _run_batch1_layers(job, "Chicago", "IL")

    _assert_commercial_ti_primary(out)
    assert out["_primary_scope"] == "commercial_restaurant"
    families = set(_families(out))
    assert {"building", "mechanical", "electrical", "plumbing", "fire"}.issubset(families)
    text = _permit_blob(out) + " | " + " | ".join(c.get("permit_type", "") for c in out.get("companion_permits", [])).lower()
    assert "grease" in text
    assert "hood" in text or "suppression" in text
    assert "health" in text
    assert "residential" not in text


def test_commercial_primary_reconciliation_repairs_stale_cached_residential_trade_card():
    job = "Denver commercial office tenant improvement with partitions and HVAC diffuser relocation"
    result = {
        "confidence": "high",
        "job_summary": "This is a commercial tenant improvement for an office suite.",
        "permits_required": [
            {"permit_type": "Mechanical Permit — HVAC System Replacement (Residential)", "portal_selection": "Mechanical - HVAC Changeout / Replacement", "required": True, "notes": "wrong cached primary"}
        ],
        "permits_required_logic": [],
        "companion_permits": [],
    }

    engine.enforce_commercial_primary_permit_guardrail(result, job, "Denver", "CO")
    engine.validate_and_sanitize_permit_result(result, job, "Denver", "CO")

    _assert_commercial_ti_primary(result)
    assert result.get("needs_review") is True
    assert result.get("_commercial_primary_permit_guardrail", {}).get("repaired") is True


def test_guardrail_directly_repairs_each_batch1_commercial_vertical():
    cases = [
        ("Cambridge", "MA", "commercial tenant improvement converting retail to medical clinic with exam rooms, sinks, HVAC and fire alarm", "commercial_medical_clinic_ti"),
        ("Denver", "CO", "commercial office TI with partitions, data cabling, HVAC diffuser relocation and sprinkler relocation", "commercial_office_ti"),
        ("Chicago", "IL", "commercial restaurant TI with Type I hood, grease interceptor, kitchen plumbing and fire suppression", "commercial_restaurant"),
        ("Austin", "TX", "commercial retail TI buildout with storefront signage, lighting and ADA restroom", "commercial_retail_ti"),
    ]
    for city, state, job, expected_scope in cases:
        result = {
            "confidence": "high",
            "permits_required": [
                {"permit_type": "Mechanical Permit — HVAC System Replacement (Residential)", "portal_selection": "Mechanical - HVAC Changeout / Replacement", "required": True, "notes": "wrong cached primary"}
            ],
            "permits_required_logic": [],
            "companion_permits": [],
        }
        engine.enforce_commercial_primary_permit_guardrail(result, job, city, state)

        _assert_commercial_ti_primary(result)
        assert result["_primary_scope"] == expected_scope
        assert result["_commercial_primary_permit_guardrail"]["repaired"] is True
        assert "residential" not in _permit_blob(result)


def test_guardrail_does_not_rewrite_plain_residential_hvac_scope():
    result = {
        "confidence": "high",
        "permits_required": [
            {"permit_type": "Mechanical Permit — HVAC System Replacement (Residential)", "portal_selection": "Mechanical - HVAC Changeout / Replacement", "required": True, "notes": "single-family condenser replacement"}
        ],
        "permits_required_logic": [],
        "companion_permits": [],
    }
    original = [dict(p) for p in result["permits_required"]]

    engine.enforce_commercial_primary_permit_guardrail(result, "replace residential HVAC condenser at single-family home", "Houston", "TX")

    assert result["permits_required"] == original
    assert "_commercial_primary_permit_guardrail" not in result


def test_generic_commercial_new_construction_is_not_forced_to_ti_primary():
    result = {
        "confidence": "high",
        "permits_required": [
            {"permit_type": "Commercial New Building Permit", "portal_selection": "Commercial Building Permit", "required": True, "notes": "ground-up warehouse"}
        ],
        "permits_required_logic": [],
        "companion_permits": [],
    }

    engine.enforce_commercial_primary_permit_guardrail(result, "ground-up new commercial warehouse building", "Dallas", "TX")

    assert result["permits_required"][0]["permit_type"] == "Commercial New Building Permit"
    assert "_commercial_primary_permit_guardrail" not in result


def test_guardrail_does_not_force_commercial_trade_only_hvac_into_ti_primary():
    result = {
        "confidence": "high",
        "permits_required": [
            {"permit_type": "Mechanical Permit — Commercial RTU Replacement", "portal_selection": "Mechanical Permit", "required": True, "notes": "like-for-like RTU replacement only"}
        ],
        "permits_required_logic": [],
        "companion_permits": [],
    }
    original = [dict(p) for p in result["permits_required"]]

    engine.enforce_commercial_primary_permit_guardrail(result, "commercial building HVAC changeout in suite 200, like-for-like RTU replacement only", "Phoenix", "AZ")

    assert result["permits_required"] == original
    assert "_commercial_primary_permit_guardrail" not in result


def test_cache_hit_path_repairs_stale_commercial_residential_primary(monkeypatch):
    cached = {
        "confidence": "high",
        "job_summary": "Commercial office tenant improvement for a suite buildout.",
        "permits_required": [
            {"permit_type": "Mechanical Permit — HVAC System Replacement (Residential)", "portal_selection": "Mechanical - HVAC Changeout / Replacement", "required": True, "notes": "stale cached primary"}
        ],
        "permits_required_logic": [],
        "companion_permits": [],
        "sources": [],
        "missing_fields": [],
    }

    monkeypatch.setattr(engine, "get_cached", lambda key, _refresh_callback=None: cached)
    monkeypatch.setattr(engine, "init_cache", lambda: None)
    monkeypatch.setattr(engine, "enrich_result_with_serper_sources", lambda result, job_type, city, state: result)
    monkeypatch.setattr(engine, "apply_scope_aware_permit_classification", lambda result, job_type: result)

    out = engine.research_permit("commercial office TI suite buildout with HVAC diffuser relocation", "Denver", "CO", use_cache=True, job_category="commercial")

    _assert_commercial_ti_primary(out)
    assert out["_cached"] is True
    assert out["_commercial_primary_permit_guardrail"]["repaired"] is True
    assert "residential" not in _permit_blob(out)


def test_guardrail_is_idempotent_after_repair():
    result = {
        "confidence": "high",
        "confidence_reason": "Initial model confidence.",
        "permits_required": [
            {"permit_type": "Mechanical Permit — HVAC System Replacement (Residential)", "portal_selection": "Mechanical - HVAC Changeout / Replacement", "required": True, "notes": "wrong cached primary"}
        ],
        "permits_required_logic": [],
        "companion_permits": [],
    }
    job = "commercial office TI with partitions and HVAC diffuser relocation"

    engine.enforce_commercial_primary_permit_guardrail(result, job, "Denver", "CO")
    first_confidence = result["confidence"]
    first_reason = result["confidence_reason"]
    first_logic_count = len(result["permits_required_logic"])
    engine.enforce_commercial_primary_permit_guardrail(result, job, "Denver", "CO")

    assert first_confidence == "medium"
    assert result["confidence"] == first_confidence
    assert result["confidence_reason"] == first_reason
    assert len(result["permits_required_logic"]) == first_logic_count
    _assert_commercial_ti_primary(result)


def test_ti_min_floor_does_not_add_sign_for_office_or_medical_without_signage():
    cases = [
        ("commercial office TI with partitions, ceiling grid, HVAC diffuser relocation and ADA restroom", "commercial_office_ti"),
        ("commercial tenant improvement converting office to medical clinic with exam rooms, hand sinks and HVAC ventilation", "commercial_medical_clinic_ti"),
    ]
    for job, expected_scope in cases:
        result = {"permits_required": [], "permits_required_logic": []}
        result["_primary_scope"] = engine.detect_primary_scope(job)

        engine.enforce_ti_min_permits_floor(result, job, "Denver", "CO")

        assert result["_primary_scope"] == expected_scope
        assert "sign" not in {engine._permit_family(p) for p in result["permits_required"]}


def test_ti_min_floor_keeps_sign_for_retail_signage_scope():
    job = "commercial retail TI buildout with storefront signage, lighting and ADA restroom"
    result = {"permits_required": [], "permits_required_logic": []}
    result["_primary_scope"] = engine.detect_primary_scope(job)

    engine.enforce_ti_min_permits_floor(result, job, "Austin", "TX")

    assert result["_primary_scope"] == "commercial_retail_ti"
    assert "sign" in {engine._permit_family(p) for p in result["permits_required"]}
