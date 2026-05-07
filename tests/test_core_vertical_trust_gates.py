#!/usr/bin/env python3
"""Core vertical trust-gate regression tests before 60-AHJ expansion.

These are deterministic: no live model/API calls. They lock the failure modes
from the 2026-05-07 QA/handoff: office TI must not inherit restaurant hood /
grease / health adders through negated words, ordinary professional office must
not become medical clinic, and residential work must not leak commercial warnings.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

import api.research_engine as engine  # noqa: E402
import api.server as server  # noqa: E402
from api.evidence_pack_runtime import _vertical_for_job  # noqa: E402
from api.fee_realism_guardrail import apply_fee_realism_guardrail  # noqa: E402
from api.hidden_trigger_detector import detect_hidden_triggers  # noqa: E402


def _trigger_blob(triggers):
    return " | ".join(
        " ".join(str(t.get(k, "")) for k in ("id", "title", "why_it_matters"))
        for t in triggers
        if isinstance(t, dict)
    ).lower()


def _companion_blob(result):
    return " | ".join(
        " ".join(str(c.get(k, "")) for k in ("permit_type", "reason", "required_if"))
        for c in result.get("companion_permits", [])
        if isinstance(c, dict)
    ).lower()


def _checklist_blob(job, city="Dallas", state="TX"):
    result = {"_primary_scope": engine.detect_primary_scope(job), "license_required": "", "applying_office": ""}
    return " | ".join(engine.generate_permit_checklist(job, city, state, result)).lower()


def _apply_core_layers(job, city="Dallas", state="TX"):
    result = {
        "permit_verdict": "YES",
        "confidence": "high",
        "fee_range": "$800-$1,500",
        "permits_required": [],
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
    engine.apply_office_ti_rulebook(result, job, city, state)
    engine.apply_retail_ti_rulebook(result, job, city, state)
    engine.apply_medical_clinic_ti_rulebook(result, job, city, state)
    engine.enforce_ti_min_permits_floor(result, job, city, state)
    result = apply_fee_realism_guardrail(result, job, city, state, result["_primary_scope"])
    return result


def test_office_ti_strong_negation_has_no_restaurant_hood_grease_or_health_adders():
    job = (
        "3,000 sf commercial office TI for accounting suite with partitions, conference rooms, "
        "lighting controls and HVAC diffuser relocation; no restaurant, no food service, "
        "no commercial kitchen, no Type I hood, no fryer, no griddle, no ANSUL, "
        "and no grease interceptor."
    )

    out = _apply_core_layers(job, "Dallas", "TX")

    assert out["_primary_scope"] == "commercial_office_ti"
    assert out["_fee_floor_components"]["scope"] == "commercial_office_ti"
    assert {a["key"] for a in out["_fee_floor_components"]["trigger_adders"]}.isdisjoint({"hood_fire_suppression", "grease_interceptor"})
    restaurant_blob = _trigger_blob(out["hidden_triggers"]) + " | " + _companion_blob(out) + " | " + out["fee_range"].lower()
    assert "grease" not in restaurant_blob
    assert "hood" not in restaurant_blob
    assert "ansul" not in restaurant_blob
    assert "food establishment" not in restaurant_blob
    assert "health department" not in restaurant_blob


def test_restaurant_cosmetic_no_kitchen_does_not_create_hood_or_grease_adders():
    job = (
        "1,800 sf existing restaurant dining-room cosmetic refresh: paint, flooring, seating, "
        "decor and lighting only; no kitchen work, no Type I hood, no grease interceptor, "
        "no fryer, no griddle, no ANSUL and no food-prep equipment changes."
    )

    out = _apply_core_layers(job, "Chicago", "IL")

    assert out["_primary_scope"] == "commercial_restaurant"
    adder_keys = {a["key"] for a in out["_fee_floor_components"]["trigger_adders"]}
    assert "hood_fire_suppression" not in adder_keys
    assert "grease_interceptor" not in adder_keys
    assert "hood" not in out["fee_range"].lower()
    assert "grease" not in out["fee_range"].lower()


def test_full_restaurant_kitchen_still_triggers_health_hood_and_grease():
    job = (
        "3,200 sf restaurant TI converting retail to full commercial kitchen with Type I hood, "
        "ANSUL fire suppression, fryer, griddle, dishwashing, walk-in cooler, grease interceptor, "
        "new kitchen plumbing and health department food-establishment review."
    )

    out = _apply_core_layers(job, "Chicago", "IL")

    assert out["_primary_scope"] == "commercial_restaurant"
    blob = _trigger_blob(out["hidden_triggers"]) + " | " + _companion_blob(out) + " | " + out["fee_range"].lower()
    assert "hood" in blob or "suppression" in blob or "ansul" in blob
    assert "grease" in blob
    assert "health" in blob or "food-establishment" in blob or "food establishment" in blob
    adder_keys = {a["key"] for a in out["_fee_floor_components"]["trigger_adders"]}
    assert {"hood_fire_suppression", "grease_interceptor"}.issubset(adder_keys)


def test_professional_office_for_medical_billing_company_is_not_medical_clinic():
    job = (
        "2,400 sf professional office TI for medical billing and insurance administration company; "
        "ordinary office desks and conference rooms only, no clinic, no exam rooms, no patient care, "
        "no x-ray, no medical gas, no treatment rooms."
    )

    assert engine.detect_primary_scope(job) == "commercial_office_ti"
    assert _vertical_for_job(job) == "office_ti"
    out = _apply_core_layers(job, "Boston", "MA")
    assert out["_primary_scope"] == "commercial_office_ti"
    assert out.get("occupancy_analysis") is None
    medical_blob = _companion_blob(out) + " | " + _trigger_blob(out["hidden_triggers"])
    assert "clinic" not in medical_blob
    assert "exam room" not in medical_blob
    assert "medical gas" not in medical_blob
    assert "x-ray" not in medical_blob


def test_medical_billing_admin_office_does_not_get_clinic_checklist_or_warning():
    job = (
        "2,400 sf professional office TI for medical billing and insurance administration company; "
        "ordinary office desks and conference rooms only, no clinic, no exam rooms, no patient care, "
        "no x-ray, no medical gas, no treatment rooms."
    )

    checklist = _checklist_blob(job, "Boston", "MA")
    for forbidden in ("commercial clinic", "exam-room", "medical gas", "x-ray", "radiology"):
        assert forbidden not in checklist

    gated = server.apply_permitiq_quality_gate(
        {
            "_primary_scope": "commercial_office_ti",
            "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Office Interior Alteration"}],
            "companion_permits": [
                {"permit_type": "Electrical Permit — Commercial Tenant Improvement"},
                {"permit_type": "Mechanical Permit — Commercial Tenant Improvement"},
                {"permit_type": "Plumbing Permit — Commercial Tenant Improvement"},
            ],
            "quality_warnings": [],
            "pro_tips": [
                "Keep the scope clearly non-clinical in the permit narrative: 'office administration only, no patient care, no exam rooms, no medical gas' — that helps avoid healthcare review confusion",
                "Use the city's commercial TI permit category for partitions and lighting.",
            ],
            "sources": [{"url": "https://example.com/permit", "title": "Official source"}],
            "confidence": "medium",
        },
        job,
        "Boston",
        "MA",
    )
    warning_blob = " | ".join(gated.get("quality_warnings", [])).lower()
    tip_blob = " | ".join(gated.get("pro_tips", [])).lower()
    assert "medical gas" not in warning_blob
    assert "exam room" not in tip_blob
    assert "medical gas" not in tip_blob
    assert "commercial ti permit" in tip_blob


def test_clinic_with_exam_rooms_but_no_medgas_or_xray_filters_item_specific_checklist_and_warnings():
    job = (
        "3,000 sf medical clinic TI with exam rooms, patient check-in, accessible restroom, "
        "and HVAC ventilation updates; no x-ray, no radiology and no medical gas."
    )

    checklist = _checklist_blob(job, "Boston", "MA")
    assert "commercial clinic" in checklist
    assert "medical gas" not in checklist
    assert "x-ray" not in checklist
    assert "radiology" not in checklist

    gated = server.apply_permitiq_quality_gate(
        {
            "_primary_scope": "commercial_medical_clinic_ti",
            "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Medical Clinic Interior Alteration"}],
            "companion_permits": [
                {"permit_type": "Electrical Permit — Commercial Tenant Improvement"},
                {"permit_type": "Mechanical Permit — Commercial Tenant Improvement"},
                {"permit_type": "Plumbing Permit — Commercial Tenant Improvement"},
                {"permit_type": "Fire Alarm / Fire Sprinkler Permit — Commercial Tenant Improvement"},
                {"permit_type": "Accessibility review"},
            ],
            "quality_warnings": [],
            "sources": [{"url": "https://example.com/permit", "title": "Official source"}],
            "confidence": "medium",
        },
        job,
        "Boston",
        "MA",
    )
    assert "medical gas" not in " | ".join(gated.get("quality_warnings", [])).lower()


def test_real_medical_clinic_still_triggers_clinic_rulebook():
    job = (
        "4,000 sf medical clinic TI with exam rooms, hand sinks, patient check-in, x-ray room, "
        "medical gas outlets, HVAC ventilation updates, ADA restroom and fire alarm changes."
    )

    assert engine.detect_primary_scope(job) == "commercial_medical_clinic_ti"
    assert _vertical_for_job(job) == "medical_clinic_ti"
    out = _apply_core_layers(job, "Cambridge", "MA")
    assert out["_primary_scope"] == "commercial_medical_clinic_ti"
    assert out.get("occupancy_analysis", {}).get("applies") is True
    blob = _companion_blob(out) + " | " + " | ".join(out.get("watch_out", [])).lower()
    assert "health" in blob or "clinic" in blob
    assert "medical gas" in blob or "x-ray" in blob


def test_residential_bathroom_remodel_has_no_commercial_warning_leakage():
    job = "residential bathroom remodel replacing vanity, tub and tile in single-family home"
    out = _apply_core_layers(job, "Orlando", "FL")

    assert out["_primary_scope"] == "residential"
    assert out["_fee_floor_check"] == "residential_no_override"
    blob = " | ".join(
        str(x) for key in ("watch_out", "common_mistakes", "pro_tips", "inspections", "companion_permits") for x in out.get(key, [])
    ).lower()
    for forbidden in ("commercial", "tenant improvement", "restaurant", "food establishment", "medical clinic", "health-care", "low-voltage"):
        assert forbidden not in blob


def test_short_restaurant_tokens_do_not_match_inside_unrelated_words():
    cases = [
        "2,000 sf barber shop tenant improvement with reception, stations, retail shelving and lighting; no food service or kitchen work.",
        "4,500 sf indoor shooting range tenant improvement with office, classroom and acoustic partitions; no kitchen, hood or grease work.",
        "1,200 sf neighborhood market office buildout with back-office partitions and lighting only; no kitchen equipment.",
        "2,800 sf theater tenant improvement with stage fog machine and lighting controls; no food service, no kitchen, no grease interceptor.",
    ]

    for job in cases:
        out = _apply_core_layers(job, "Dallas", "TX")
        blob = _trigger_blob(out["hidden_triggers"]) + " | " + _companion_blob(out) + " | " + out["fee_range"].lower()
        adder_keys = {a["key"] for a in (out.get("_fee_floor_components") or {}).get("trigger_adders", [])}
        assert out["_primary_scope"] != "commercial_restaurant"
        assert {"hood_fire_suppression", "grease_interceptor"}.isdisjoint(adder_keys)
        assert "food establishment" not in blob
        assert "health department" not in blob


def test_healthcare_adjacent_admin_office_stays_office_without_clinical_scope():
    job = (
        "2,200 sf healthcare-adjacent corporate office TI for insurance administration, "
        "desks and conference rooms only; no clinic, no exam rooms, no patient care, "
        "no x-ray and no medical gas."
    )

    assert engine.detect_primary_scope(job) == "commercial_office_ti"
    assert _vertical_for_job(job) == "office_ti"
    out = _apply_core_layers(job, "Boston", "MA")
    assert out["_primary_scope"] == "commercial_office_ti"
    assert out.get("occupancy_analysis") is None
    medical_blob = _companion_blob(out) + " | " + _trigger_blob(out["hidden_triggers"])
    assert "clinic" not in medical_blob
    assert "exam room" not in medical_blob
    assert "medical gas" not in medical_blob
    assert "x-ray" not in medical_blob


def test_post_position_negation_suppresses_restaurant_fee_adders():
    job = (
        "1,900 sf existing restaurant front-of-house refresh with fryer not included in scope, "
        "hood outside the scope, and grease interceptor excluded; paint, flooring and seating only."
    )

    out = _apply_core_layers(job, "Chicago", "IL")
    adder_keys = {a["key"] for a in (out.get("_fee_floor_components") or {}).get("trigger_adders", [])}
    assert "hood_fire_suppression" not in adder_keys
    assert "grease_interceptor" not in adder_keys


def test_fog_as_stage_or_glass_word_does_not_trigger_grease_interceptor():
    cases = [
        "2,800 sf theater tenant improvement with stage fog machine and lighting controls; no food service or kitchen.",
        "3,000 sf office TI with fog-resistant glass partitions; no food service, no kitchen, no hood and no grease interceptor.",
    ]

    for job in cases:
        out = _apply_core_layers(job, "Dallas", "TX")
        adder_keys = {a["key"] for a in (out.get("_fee_floor_components") or {}).get("trigger_adders", [])}
        blob = _trigger_blob(out["hidden_triggers"]) + " | " + _companion_blob(out) + " | " + out["fee_range"].lower()
        assert "grease_interceptor" not in adder_keys
        assert "grease" not in blob


def test_commercial_kitchen_with_negated_hood_and_grease_does_not_create_hidden_triggers():
    job = (
        "restaurant TI with commercial kitchen equipment storage and prep tables only; "
        "no Type I hood, no fryer, no griddle, no ANSUL, no grease interceptor, "
        "and no grease duct work."
    )

    out = _apply_core_layers(job, "Chicago", "IL")
    assert out["_primary_scope"] == "commercial_restaurant"
    blob = _trigger_blob(out["hidden_triggers"]) + " | " + _companion_blob(out) + " | " + out["fee_range"].lower()
    adder_keys = {a["key"] for a in (out.get("_fee_floor_components") or {}).get("trigger_adders", [])}
    assert {"hood_fire_suppression", "grease_interceptor"}.isdisjoint(adder_keys)
    assert "hood" not in blob
    assert "ansul" not in blob
    assert "grease" not in blob


def test_commercial_kitchen_with_negated_hood_and_grease_does_not_get_kitchen_checklist():
    job = (
        "restaurant TI with commercial kitchen equipment storage and prep tables only; "
        "no Type I hood, no fryer, no griddle, no ANSUL, no grease interceptor, "
        "and no grease duct work."
    )

    checklist = _checklist_blob(job, "Chicago", "IL")
    for forbidden in ("type i commercial exhaust hood", "ansul", "grease interceptor", "hood exhaust"):
        assert forbidden not in checklist

    out = _apply_core_layers(job, "Chicago", "IL")
    permit_blob = " | ".join(str(p) for p in out.get("permits_required", []) + out.get("permits_required_logic", [])).lower()
    assert "grease-interceptor" not in permit_blob

    gated = server.apply_permitiq_quality_gate(
        {
            "_primary_scope": "commercial_restaurant",
            "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Restaurant Interior Alteration"}],
            "companion_permits": [
                {"permit_type": "Electrical Permit — Commercial Tenant Improvement"},
                {"permit_type": "Mechanical Permit — Commercial Tenant Improvement"},
                {"permit_type": "Plumbing Permit — Commercial Tenant Improvement"},
                {"permit_type": "Health Department / Food Establishment Review"},
            ],
            "common_mistakes": ["Assuming no hood means no permit — the interior alteration still needs a building permit"],
            "pro_tips": [
                "Keep the scope explicit: no Type I hood, no grease duct, no grease interceptor, no ANSUL. That helps avoid being bounced into fire suppression or plumbing review unnecessarily",
                "Confirm whether prep tables and storage affect health review.",
            ],
            "quality_warnings": [],
            "sources": [{"url": "https://example.com/permit", "title": "Official source"}],
            "confidence": "medium",
        },
        job,
        "Chicago",
        "IL",
    )
    surface_blob = " | ".join(gated.get("common_mistakes", []) + gated.get("pro_tips", [])).lower()
    assert "hood" not in surface_blob
    assert "ansul" not in surface_blob
    assert "grease" not in surface_blob
    assert "prep tables" in surface_blob


def test_restaurant_dishwasher_without_hood_or_grease_filters_item_specific_checklist():
    job = (
        "restaurant TI with commercial dishwasher and prep sink plumbing; "
        "no Type I hood, no fryer, no griddle, no ANSUL, and no grease interceptor."
    )

    checklist = _checklist_blob(job, "Chicago", "IL")
    assert "dishwasher" in checklist or "indirect waste" in checklist
    for forbidden in ("type i commercial exhaust hood", "ansul", "grease interceptor", "hood exhaust"):
        assert forbidden not in checklist

    out = _apply_core_layers(job, "Chicago", "IL")
    permit_blob = " | ".join(str(p) for p in out.get("permits_required", []) + out.get("permits_required_logic", [])).lower()
    assert "grease-interceptor" not in permit_blob
