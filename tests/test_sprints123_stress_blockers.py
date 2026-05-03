#!/usr/bin/env python3
"""Regression tests for Sprint 1/2/3 stress QA blockers."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import api.research_engine as engine
from api.hidden_trigger_detector import detect_hidden_triggers
from api.state_schema import compact_state_schema_context


def _blob(value) -> str:
    return json.dumps(value, sort_keys=True, default=str).lower()


def test_office_overlay_uses_active_vertical_metadata_after_office_population():
    ctx = compact_state_schema_context(
        "TX",
        "office_ti",
        "commercial office TI with partitions, conference rooms, lighting and ADA restroom",
    )

    assert ctx["active_vertical"] == "office_ti"
    assert ctx["active_vertical_populated"] is True
    assert ctx["population_status"] == "partially_populated"
    assert ctx["requires_population_before_state_specific_claims"] is False
    assert ctx["coverage_level"] == "phase4c_tx_office_ti"
    warning = ctx["contractor_warning"].lower()
    assert "office ti overlay is populated" in warning
    assert "medical" not in warning
    assert "dental" not in warning
    assert ctx["triggered_rules"]


def test_restaurant_overlay_fails_closed_when_active_vertical_not_populated():
    ctx = compact_state_schema_context(
        "TX",
        "restaurant_ti",
        "commercial restaurant TI with Type I hood, grease interceptor and health review",
    )

    assert ctx["active_vertical"] == "restaurant_ti"
    assert ctx["active_vertical_populated"] is False
    assert ctx["population_status"] == "needs_verification"
    assert "needs_verification" in ctx["coverage_level"]
    warning = ctx["contractor_warning"].lower()
    assert "restaurant ti" in warning
    assert "not populated" in warning
    assert "medical" not in warning
    assert "dental" not in warning


def test_medical_overlay_keeps_verified_medical_context_when_populated():
    ctx = compact_state_schema_context(
        "TX",
        "medical_clinic_ti",
        "commercial tenant improvement converting office to medical clinic with exam rooms and hand sinks",
    )

    assert ctx["active_vertical"] == "medical_clinic_ti"
    assert ctx["active_vertical_populated"] is True
    assert ctx["population_status"] != "needs_verification"
    assert "medical" in ctx["contractor_warning"].lower()


def test_residential_home_office_does_not_classify_as_commercial_office_ti():
    job = "job_category=residential convert spare bedroom to home office with desk, paint and shelving; no structural work, no customers, no public access"
    result = {
        "permit_verdict": "YES",
        "confidence": "high",
        "permits_required": [
            {"permit_type": "Building Permit — Tenant Improvement", "portal_selection": "Office Interior Alteration", "required": True, "notes": "bad stale commercial output"}
        ],
        "permits_required_logic": [],
        "companion_permits": [],
    }

    assert engine.detect_primary_scope(job) == "residential"
    engine.enforce_commercial_primary_permit_guardrail(result, job, "Dallas", "TX")

    out = _blob(result)
    assert result["_primary_scope"] == "residential"
    assert "tenant improvement" not in out
    assert "office interior alteration" not in out
    assert "commercial office" not in out
    assert "residential home office" in out


def test_tx_restaurant_ti_scope_pack_does_not_leak_phoenix_or_maricopa():
    job = "commercial restaurant TI in Dallas TX with Type I hood, grease interceptor, ADA restroom and health department food establishment review"
    result = {
        "permit_verdict": "YES",
        "confidence": "high",
        "permits_required": [],
        "permits_required_logic": [],
        "companion_permits": [],
        "what_to_bring": [],
        "common_mistakes": [],
        "pro_tips": [],
        "watch_out": [],
        "inspections": [],
    }
    result["_primary_scope"] = engine.detect_primary_scope(job)
    engine.apply_scope_aware_permit_classification(result, job)
    result["what_to_bring"] = engine.generate_permit_checklist(job, "Dallas", "TX", result)
    engine.enforce_commercial_primary_permit_guardrail(result, job, "Dallas", "TX")
    engine.validate_and_sanitize_permit_result(result, job, "Dallas", "TX")

    out = _blob(result)
    assert result["_primary_scope"] == "commercial_restaurant"
    assert "phoenix" not in out
    assert "maricopa" not in out


def test_non_az_restaurant_hidden_triggers_do_not_leak_arizona_liquor_or_patio_citations():
    triggers = detect_hidden_triggers(
        "restaurant TI in Dallas TX with outdoor patio, alcohol service, Type I hood and change of occupancy to restaurant",
        "Dallas",
        "TX",
        "commercial_restaurant",
        {},
    )

    out = _blob(triggers)
    for forbidden in ("phoenix", "maricopa", "arizona", "a.r.s.", "shape phx", "az roc"):
        assert forbidden not in out
    assert "liquor" in out
    assert "outdoor patio" not in out or "local" in out or "ibc" in out


def test_office_ti_output_does_not_contain_medical_or_dental_clinic_wording():
    job = "commercial office TI in Denver with demising partitions, conference rooms, ceiling grid, lighting controls, data cabling, HVAC diffuser relocation, ADA restroom and sprinkler relocation"
    result = {
        "permit_verdict": "YES",
        "confidence": "high",
        "permits_required": [],
        "permits_required_logic": [],
        "companion_permits": [],
        "what_to_bring": [],
        "common_mistakes": [],
        "pro_tips": [],
        "watch_out": [],
        "inspections": [],
    }
    result["_primary_scope"] = engine.detect_primary_scope(job)
    engine.apply_scope_aware_permit_classification(result, job)
    result["what_to_bring"] = engine.generate_permit_checklist(job, "Denver", "CO", result)
    engine.enforce_commercial_primary_permit_guardrail(result, job, "Denver", "CO")
    engine.validate_and_sanitize_permit_result(result, job, "Denver", "CO")

    out = _blob(result)
    assert result["_primary_scope"] == "commercial_office_ti"
    for forbidden in ("medical clinic", "dental clinic", "exam room", "exam rooms", "med gas", "medical gas", "x-ray", "x ray", "nitrous oxide"):
        assert forbidden not in out


def test_office_interior_alteration_does_not_fall_back_to_residential_pool_checklist():
    job = "commercial tenant space office interior alteration with demising partitions"
    result = {
        "permit_verdict": "YES",
        "confidence": "high",
        "permits_required": [],
        "permits_required_logic": [],
        "companion_permits": [],
        "what_to_bring": [],
        "common_mistakes": [],
        "pro_tips": [],
        "watch_out": [],
        "inspections": [],
    }
    result["_primary_scope"] = engine.detect_primary_scope(job)
    engine.apply_scope_aware_permit_classification(result, job)
    result["what_to_bring"] = engine.generate_permit_checklist(job, "Plano", "TX", result)
    engine.enforce_ti_min_permits_floor(result, job, "Plano", "TX")
    engine.enforce_commercial_primary_permit_guardrail(result, job, "Plano", "TX")
    engine.validate_and_sanitize_permit_result(result, job, "Plano", "TX")

    out = _blob(result)
    assert result["_primary_scope"] == "commercial_office_ti"
    assert "tenant improvement" in out
    assert "office interior alteration" in out
    assert "pool" not in out
    assert "barrier / fence" not in out
