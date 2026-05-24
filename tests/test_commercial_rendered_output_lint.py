#!/usr/bin/env python3
"""Customer-visible commercial rendered-output guardrails.

These tests intentionally exercise the rendered/report text contract, not only
backend JSON shape. A report is not customer-ready if these invariants fail.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine
from api.rendered_output_lint import (
    build_rendering_context,
    classify_source_for_jurisdiction,
    lint_rendered_output,
    sanitize_permit_display_name,
    select_inspection_profile,
)


FALLBACK_TITLE = "Permit required -- exact permit type needs AHJ verification"


def _base_result(scope="commercial_restaurant", *, city="Chicago", state="IL"):
    return {
        "permit_verdict": "YES",
        "confidence": "high",
        "confidence_reason": "Exact permit category needs AHJ verification",
        "needs_review": True,
        "data_source": "city",
        "_primary_scope": scope,
        "job_category": "commercial",
        "city": city,
        "state": state,
        "permit_name": FALLBACK_TITLE,
        "permit_type": FALLBACK_TITLE,
        "permits_required": [
            {
                "permit_type": FALLBACK_TITLE,
                "portal_selection": FALLBACK_TITLE,
                "required": True,
                "notes": "needs AHJ verification",
            }
        ],
        "inspections": ["Mechanical rough inspection for split-system HVAC", "Final mechanical inspection"],
        "sources": [
            "https://www.chicago.gov/city/en/depts/bldgs.html",
            "https://www.phila.gov/departments/department-of-licenses-and-inspections/",
        ],
    }


def _bad_restaurant_rendered_text():
    return "\n".join(
        [
            "🏠 HOMEOWNER SUMMARY — WAS A PERMIT REQUIRED?",
            "Work without a permit can create problems when you sell the home and may not pass final inspection.",
            "🟢 Verified · official sources",
            "Exact permit category needs AHJ verification",
            f"📋 PERMIT DETAILS\n{FALLBACK_TITLE}",
            f"🎯 EXACT PERMIT NAME TO ASK FOR\n{FALLBACK_TITLE}",
            "🔍 WHAT GETS INSPECTED",
            "Mechanical rough inspection for split-system HVAC",
            "Final mechanical inspection",
            "🔗 OFFICIAL SOURCES",
            "https://www.chicago.gov/city/en/depts/bldgs.html",
            "https://www.phila.gov/departments/department-of-licenses-and-inspections/",
        ]
    )


def test_bad_restaurant_fixture_fails_all_required_customer_visible_lints():
    result = _base_result("commercial_restaurant")
    report = lint_rendered_output(
        result=result,
        rendered_text=_bad_restaurant_rendered_text(),
        job_type="Chicago restaurant TI with Type I hood, grease duct, Ansul suppression, grease interceptor, 38 apartments upstairs",
        city="Chicago",
        state="IL",
    )
    codes = {v["code"] for v in report["violations"]}
    assert report["passed"] is False
    assert "commercial_homeowner_language" in codes
    assert "wrong_local_source_jurisdiction" in codes
    assert "verified_badge_with_unverified_core_claim" in codes
    assert "fallback_text_in_permit_title" in codes
    assert "restaurant_inspection_profile_missing" in codes


def test_good_restaurant_fixture_passes_commercial_lint():
    result = _base_result("commercial_restaurant")
    result.update(
        {
            "needs_review": False,
            "confidence_reason": "Chicago AHJ source supports commercial building permit path; hood and fire items remain separate trade reviews.",
            "permit_name": "Commercial Alteration Permit",
            "permit_type": "Commercial Alteration Permit",
            "permits_required": [
                {"permit_type": "Commercial Alteration Permit", "portal_selection": "Building Permit / Alteration", "required": True}
            ],
            "inspections": [
                "Building rough inspection",
                "Building final inspection",
                "Hood and grease duct inspection",
                "Fire suppression acceptance test",
                "Fire alarm inspection",
                "Health department inspection",
                "Egress and occupancy final",
            ],
            "sources": ["https://www.chicago.gov/city/en/depts/bldgs.html"],
        }
    )
    text = """
✅ PERMIT REQUIRED
Commercial Alteration Permit
Building Permit / Alteration
Building rough inspection
Building final inspection
Hood and grease duct inspection
Fire suppression acceptance test
Fire alarm inspection
Health department inspection
Egress and occupancy final
Official local AHJ source: chicago.gov
"""
    report = lint_rendered_output(result, text, "restaurant tenant improvement with Type I hood", "Chicago", "IL")
    assert report["violations"] == []
    assert report["passed"] is True


def test_commercial_rendering_context_beats_homeowner_mode_and_frontend_derivation():
    result = _base_result("commercial_medical_clinic_ti")
    ctx = build_rendering_context(
        result,
        job_type="office-to-clinic conversion with exam rooms, hand sinks, x-ray, medical gas",
        city="Chicago",
        state="IL",
        requested_mode="homeowner",
    )
    assert ctx["is_commercial"] is True
    assert ctx["vertical"] == "medical_clinic_ti"
    assert ctx["template_family"] == "commercial"
    assert ctx["allow_homeowner_template"] is False
    assert ctx["inspection_profile"] == "medical_clinic_ti"


def test_permit_display_name_is_separate_from_uncertainty_notes():
    display = sanitize_permit_display_name(FALLBACK_TITLE, scope="commercial_restaurant")
    assert display == "Commercial Alteration / Tenant Improvement Permit"
    assert "AHJ verification" not in display
    assert "Permit required" not in display


def test_source_classification_distinguishes_local_state_model_and_wrong_local():
    local = classify_source_for_jurisdiction("https://www.chicago.gov/city/en/depts/bldgs.html", "Chicago", "IL")
    local_full_state = classify_source_for_jurisdiction("https://www.chicago.gov/city/en/depts/bldgs.html", "Chicago", "Illinois")
    wrong_city = classify_source_for_jurisdiction("https://permits.phila.gov/", "Chicago", "IL")
    state = classify_source_for_jurisdiction("https://idfpr.illinois.gov/", "Chicago", "Illinois")
    model = classify_source_for_jurisdiction("https://codes.iccsafe.org/content/IBC2024P1", "Chicago", "IL")
    assert local["source_class"] == "local_ahj"
    assert local_full_state["source_class"] == "local_ahj"
    assert local["allowed_as_official_support"] is True
    assert wrong_city["source_class"] == "wrong_local_ahj"
    assert wrong_city["allowed_as_official_support"] is False
    assert state["source_class"] == "state"
    assert model["source_class"] == "model_code"
    assert model["allowed_as_local_ahj_support"] is False


def test_vertical_specific_inspection_profiles_and_foreign_absence():
    restaurant = select_inspection_profile("commercial_restaurant", "restaurant TI with hood grease duct fire suppression")
    clinic = select_inspection_profile("commercial_medical_clinic_ti", "medical clinic TI with x-ray and med gas")
    office = select_inspection_profile("commercial_office_ti", "office TI with demising partitions and HVAC balancing")
    assert {"building rough", "hood", "grease duct", "fire suppression", "health", "egress", "occupancy final"}.issubset(
        set(restaurant["required_concepts"])
    )
    assert {"clinic", "health", "life safety", "accessibility", "equipment"}.issubset(set(clinic["required_concepts"]))
    assert {"commercial alteration", "egress", "accessibility"}.issubset(set(office["required_concepts"]))
    assert not any("medical gas" in item.lower() for item in office["default_items"] + office["required_concepts"])


def test_controls_residential_and_trade_only_scope_do_not_overclassify():
    assert engine.detect_primary_scope("residential HVAC replacement and bathroom remodel at a single family home") == "residential"
    trade_only = engine.detect_primary_scope("commercial rooftop unit replacement only, like-for-like HVAC, no tenant buildout")
    assert trade_only not in {"commercial_restaurant", "commercial_medical_clinic_ti", "commercial_office_ti"}


def test_empty_permit_set_cannot_be_verified():
    result = _base_result("commercial_office_ti")
    result["permits_required"] = []
    result["permit_name"] = ""
    result["permit_type"] = ""
    text = "🟢 Verified · official sources\nNo permits required"
    report = lint_rendered_output(result, text, "office TI", "Chicago", "IL")
    codes = {v["code"] for v in report["violations"]}
    assert "verified_badge_with_empty_permit_set" in codes


def test_string_assembly_artifacts_fail_lint():
    result = _base_result("commercial_office_ti")
    text = "Commercial Alteration Permit undefined $  , , () 1 inspections"
    report = lint_rendered_output(result, text, "office TI", "Chicago", "IL")
    codes = {v["code"] for v in report["violations"]}
    assert "string_assembly_artifact" in codes
