#!/usr/bin/env python3
"""Medical clinic tenant-improvement coverage tests."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine
from api.fee_realism_guardrail import apply_fee_realism_guardrail
from api.hidden_trigger_detector import detect_hidden_triggers

MEDICAL_IDS = {
    "medical_clinic_exam_room_plumbing",
    "medical_clinic_medical_gas_review",
    "medical_clinic_infection_control_pressure_relationships",
    "medical_clinic_accessibility_path_of_travel",
    "medical_clinic_xray_or_radiology_shielding",
    "medical_clinic_fire_life_safety_and_alarm",
}


def ids(job, city="Austin", state="TX", primary_scope="commercial_medical_clinic_ti"):
    return {t["id"] for t in detect_hidden_triggers(job, city, state, primary_scope, {})}


def enriched(job="medical clinic tenant improvement with exam rooms, hand sinks, med gas, ADA restroom, fire alarm and x-ray room", city="Austin", state="TX"):
    result = {
        "_primary_scope": "commercial_medical_clinic_ti",
        "fee_range": "$450 electrical + $750 plumbing",
        "permits_required": [{"permit_type": "Building Permit", "portal_selection": "Building Permit", "required": True, "notes": "seed"}],
        "companion_permits": [],
        "pro_tips": [],
        "watch_out": [],
        "common_mistakes": [],
        "inspections": [],
    }
    result["hidden_triggers"] = detect_hidden_triggers(job, city, state, "commercial_medical_clinic_ti", result)
    engine.apply_medical_clinic_ti_rulebook(result, job, city, state)
    engine.enforce_ti_min_permits_floor(result, job, city, state)
    return result


def test_medical_clinic_scope_is_not_flattened_to_office_ti():
    assert engine.detect_primary_scope("medical clinic TI with exam rooms, hand sinks, med gas and x-ray room") == "commercial_medical_clinic_ti"
    assert engine.detect_primary_scope("dental clinic tenant improvement with nitrous oxide and sterilization room") == "commercial_medical_clinic_ti"
    assert engine.detect_primary_scope("ambulatory surgical center ASC tenant improvement with operating room, PACU and medical gas") == "commercial_medical_clinic_ti"
    assert engine.detect_primary_scope("ASC tenant improvement with sterile processing and recovery bays") == "commercial_medical_clinic_ti"
    assert engine.detect_primary_scope("outpatient surgery buildout with pre-op and recovery bays") == "commercial_medical_clinic_ti"


def test_medical_clinic_ti_fires_clinic_specific_triggers():
    got = ids("medical clinic tenant improvement with exam rooms, hand sinks, med gas, fire alarm, ADA restroom, and x-ray room")
    assert "medical_clinic_exam_room_plumbing" in got
    assert "medical_clinic_medical_gas_review" in got
    assert "medical_clinic_xray_or_radiology_shielding" in got
    assert "medical_clinic_accessibility_path_of_travel" in got


def test_medical_clinic_ti_suppresses_multifamily_trigger_pool_even_with_surrounding_r2_context():
    job = (
        "medical clinic tenant improvement with two procedure rooms, exam sinks, med gas, "
        "sprinkler relocation, and x-ray in a mixed-use building with apartments/R-2 above"
    )
    got = ids(job, "Cambridge", "MA", "commercial_medical_clinic_ti")
    assert "medical_clinic_exam_room_plumbing" in got
    assert "medical_clinic_medical_gas_review" in got
    assert "multifamily_type_b_accessible_unit_ratio" not in got
    assert "multifamily_nfpa_13r_sprinkler_coverage_limits" not in got


def test_legitimate_multifamily_scope_still_fires_multifamily_trigger_pool():
    job = "multifamily apartment building renovation with Type B accessible units, NFPA 13R sprinkler work, and R-2 corridors"
    got = ids(job, "Austin", "TX", "multifamily")
    assert "multifamily_type_b_accessible_unit_ratio" in got
    assert "multifamily_nfpa_13r_sprinkler_coverage_limits" in got


def test_medical_clinic_rulebook_adds_contractor_grade_guidance():
    out = enriched()
    combined = " | ".join(out["pro_tips"] + out["watch_out"] + out["common_mistakes"] + out["inspections"]).lower()
    assert "exam room" in combined
    assert "medical gas" in combined
    assert "infection" in combined or "pressure" in combined
    assert "ada" in combined or "path-of-travel" in combined


def test_medical_clinic_min_permit_floor_includes_mep_fire_and_health_review():
    out = enriched()
    families = {engine._permit_family(p) for p in out["permits_required"]}
    assert {"building", "mechanical", "electrical", "plumbing", "fire"}.issubset(families)
    companion_text = " | ".join(c.get("permit_type", "") for c in out["companion_permits"]).lower()
    assert "medical gas" in companion_text
    assert "health" in companion_text or "licensing" in companion_text


def test_medical_clinic_fee_floor_overrides_residential_trade_anchors():
    result = {
        "fee_range": "$450 electrical + $750 plumbing",
        "hidden_triggers": [{"id": trigger_id} for trigger_id in MEDICAL_IDS],
    }
    guarded = apply_fee_realism_guardrail(
        result,
        "4200 sqft medical clinic tenant improvement with exam sinks, med gas, fire alarm and x-ray room",
        "Austin",
        "TX",
        "commercial_medical_clinic_ti",
    )
    assert guarded["_fee_adjusted"] is True
    assert guarded["_fee_floor_components"]["scope"] == "commercial_medical_clinic_ti"
    assert guarded["_fee_floor_components"]["structured_low"] >= 10000
    assert "$450" not in guarded["fee_range"]


def test_restaurant_and_residential_do_not_include_medical_clinic_triggers():
    restaurant = ids("restaurant TI with hood, grease interceptor and dining", "Phoenix", "AZ", "commercial_restaurant")
    residential = ids("bathroom remodel with new sink and electrical", "Austin", "TX", "residential")
    assert not (restaurant & MEDICAL_IDS)
    assert not (residential & MEDICAL_IDS)


def test_phase2_ordinary_medical_clinic_defaults_to_business_occupancy_basis():
    analysis = engine.classify_healthcare_occupancy(
        "medical clinic tenant improvement with exam rooms, check-in, sinks, x-ray, no sedation or overnight stay"
    )
    assert analysis["classification"] == "likely_business_group_b"
    assert analysis["risk_level"] == "medium"
    assert analysis["requires_i2_review"] is False
    assert "Business Group B" in analysis["summary"]
    assert any("outpatient" in reason.lower() for reason in analysis["reasons"])
    assert any("sedation" in item.lower() for item in analysis["verify_before_quote"])


def test_phase2_non_healthcare_office_does_not_get_healthcare_occupancy_analysis():
    analysis = engine.classify_healthcare_occupancy("office tenant improvement with conference rooms, lighting, data cabling and finishes")
    assert analysis["applies"] is False
    assert analysis["classification"] == "not_healthcare_occupancy_scope"
    assert analysis["requires_i2_review"] is False


def test_phase2_dental_sterilization_room_stays_business_group_b_without_surgical_scope():
    analysis = engine.classify_healthcare_occupancy(
        "dental clinic tenant improvement with exam rooms, nitrous oxide, sterilization room, and no surgery or overnight stays"
    )
    assert analysis["applies"] is True
    assert analysis["classification"] == "likely_business_group_b"
    assert analysis["requires_i2_review"] is False
    assert analysis["risk_level"] == "medium"


def test_phase2_explicit_no_surgery_no_sedation_no_overnight_stays_medium_risk():
    analysis = engine.classify_healthcare_occupancy(
        "outpatient medical clinic TI with exam rooms, no surgery, no operating room, no anesthesia, and no overnight stays"
    )
    assert analysis["classification"] == "likely_business_group_b"
    assert analysis["requires_i2_review"] is False
    assert not any("Surgical center" in reason for reason in analysis["reasons"])


def test_phase2_unrelated_negation_does_not_hide_real_operating_room_signal():
    analysis = engine.classify_healthcare_occupancy(
        "medical clinic TI with no exterior signage, operating room with anesthesia and PACU recovery"
    )
    assert analysis["classification"] == "possible_i2_ambulatory_care_review"
    assert analysis["requires_i2_review"] is True


def test_phase2_negated_self_preservation_phrase_does_not_force_high_risk():
    analysis = engine.classify_healthcare_occupancy(
        "outpatient medical clinic TI with exam rooms; patients are not incapable of self-preservation"
    )
    assert analysis["classification"] == "likely_business_group_b"
    assert analysis["requires_i2_review"] is False


def test_phase2_asc_without_clinic_wording_still_enters_high_risk_path():
    analysis = engine.classify_healthcare_occupancy("ASC operating rooms with PACU recovery bays")
    assert analysis["applies"] is True
    assert analysis["classification"] == "possible_i2_ambulatory_care_review"
    assert analysis["requires_i2_review"] is True


def test_phase2_surgical_center_flags_i2_ambulatory_care_review_without_overclaiming():
    job = (
        "commercial tenant improvement for ambulatory surgical center / ASC with two operating rooms, "
        "pre-op and PACU recovery bays, oxygen, medical gas, nurse call, sterile processing, "
        "moderate sedation and patients may be incapable of self-preservation during procedures"
    )
    analysis = engine.classify_healthcare_occupancy(job)
    assert analysis["classification"] == "possible_i2_ambulatory_care_review"
    assert analysis["risk_level"] == "high"
    assert analysis["requires_i2_review"] is True
    assert "not a normal office/clinic TI" in analysis["summary"]
    assert any("ASC" in permit["permit_type"] or "surgical" in permit["permit_type"].lower() for permit in analysis["companion_permits"])
    assert any("I-2" in cite or "422" in cite for cite in analysis["citations"])


def test_phase2_medical_rulebook_surfaces_occupancy_analysis_in_customer_output():
    job = (
        "Austin TX surgical center tenant improvement with operating room, PACU recovery, "
        "sterile processing, oxygen medical gas, nurse call and moderate sedation"
    )
    out = enriched(job, city="Austin", state="TX")
    analysis = out["occupancy_analysis"]
    assert analysis["classification"] == "possible_i2_ambulatory_care_review"
    assert out["needs_review"] is True
    combined = " | ".join(
        out["pro_tips"] + out["watch_out"] + out["common_mistakes"] + out["what_to_bring"] + out["inspections"]
    )
    assert "B vs I-2" in combined
    assert "not a normal office/clinic TI" in combined
    companion_text = " | ".join(c.get("permit_type", "") for c in out["companion_permits"])
    assert "Surgical center" in companion_text or "ASC" in companion_text


def test_phase2_medium_risk_clinic_rulebook_surfaces_business_group_b_guidance():
    out = enriched(
        "Cambridge MA medical clinic tenant improvement with exam rooms, sinks, x-ray, no surgery and no overnight stays",
        city="Cambridge",
        state="MA",
    )
    analysis = out["occupancy_analysis"]
    assert analysis["classification"] == "likely_business_group_b"
    assert analysis["requires_i2_review"] is False
    combined = " | ".join(out["pro_tips"] + out["what_to_bring"] + out["watch_out"] + out["common_mistakes"])
    assert "Business Group B" in combined
    assert "verify B vs I-2" in combined
    assert "not a normal office/clinic TI" not in combined
