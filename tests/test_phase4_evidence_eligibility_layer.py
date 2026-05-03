#!/usr/bin/env python3
"""Phase 4 Evidence Eligibility Layer regression tests.

These tests protect commercial TI reports from broad residential/state expert
notes before the evidence reaches report composition/rendering.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine
from api.evidence_eligibility import filter_state_expert_notes, infer_vertical, is_commercial_ti_scope
from api.state_packs import get_state_expert_notes


def _blank_result(primary_scope="commercial_office_ti"):
    return {
        "permit_verdict": "YES",
        "_primary_scope": primary_scope,
        "expert_notes": [],
        "sources": [],
    }


def _notes_text(notes_or_result):
    notes = notes_or_result.get("expert_notes", []) if isinstance(notes_or_result, dict) else notes_or_result
    return json.dumps(notes, sort_keys=True).lower()


def _assert_no_residential_ca_leakage(text: str):
    forbidden = [
        "adu",
        "accessory dwelling",
        "sb 9",
        "sfr",
        "single-family",
        "duplex",
        "solar-ready",
        "ev-ready",
        "garage",
        "lot split",
        "owner-occupancy",
    ]
    assert not any(term in text for term in forbidden), text


def test_ca_office_ti_excludes_residential_adu_sfr_solar_state_notes():
    notes = get_state_expert_notes(
        "CA",
        "Los Angeles",
        "Commercial office tenant improvement with conference rooms, data cabling, lighting and HVAC; no residential, no SFR, no ADU, no solar",
    )
    text = _notes_text(notes)

    assert notes
    assert "cslb" in text or "contractors state license" in text
    assert "building standards code" in text
    _assert_no_residential_ca_leakage(text)


def test_ca_restaurant_ti_excludes_residential_notes_but_keeps_commercial_safe_notes():
    result = engine.apply_state_expert_pack(
        _blank_result("commercial_restaurant"),
        "Los Angeles",
        "CA",
        "Restaurant tenant improvement with Type I hood, grease interceptor and health department review; no residential, no SFR, no ADU, no solar",
    )
    text = _notes_text(result)

    assert result.get("expert_notes")
    assert "cslb" in text or "contractors state license" in text
    _assert_no_residential_ca_leakage(text)


def test_ca_medical_clinic_ti_excludes_broad_residential_state_pack_notes():
    result = engine.apply_state_expert_pack(
        _blank_result("commercial_medical_clinic_ti"),
        "Los Angeles",
        "CA",
        "Medical clinic tenant improvement with exam rooms, reception, lighting and HVAC; no surgery, no x-ray, no residential, no SFR, no ADU, no solar",
    )
    text = _notes_text(result)

    assert result.get("expert_notes")
    _assert_no_residential_ca_leakage(text)


def test_ca_adu_scope_still_gets_adu_historic_utility_and_wildfire_notes():
    result = engine.apply_state_expert_pack(
        _blank_result("residential_adu"),
        "Pasadena",
        "CA",
        "garage ADU conversion with exterior work and new utility coordination",
    )
    text = _notes_text(result)

    assert "60 days" in text
    assert "65852.2" in text
    assert "impact fees waived" in text
    assert "pasadena water and power" in text or "pwp" in text
    assert "bungalow heaven" in text
    assert "vhfhsz" in text


def test_ca_solar_scope_still_gets_utility_and_solar_related_notes():
    notes = get_state_expert_notes(
        "CA",
        "Los Angeles",
        "Residential solar PV with battery energy storage and utility interconnection",
    )
    text = _notes_text(notes)

    assert "ladwp" in text
    assert "solar" in text
    assert "utility" in text


def test_ca_commercial_ti_preserves_statewide_commercial_safe_guidance():
    notes = get_state_expert_notes(
        "CA",
        "Los Angeles",
        "Office tenant improvement with lighting controls, mechanical ventilation, accessibility path of travel and certificate of occupancy review",
        primary_scope="commercial_office_ti",
    )
    text = _notes_text(notes)

    assert "cslb" in text or "contractors state license" in text
    assert "building standards code" in text
    assert "2026 enforcement" in text
    _assert_no_residential_ca_leakage(text)


def test_residential_scope_with_negated_commercial_words_is_not_reclassified_as_ti():
    job = "garage ADU conversion, no medical clinic, no office, no restaurant, no commercial tenant improvement"

    assert infer_vertical(job, "residential_adu") is None
    assert not is_commercial_ti_scope(job, "residential_adu")

    result = engine.apply_state_expert_pack(_blank_result("residential_adu"), "Pasadena", "CA", job)
    text = _notes_text(result)
    assert "65852.2" in text
    assert "adu" in text


def test_negated_solar_terms_do_not_unlock_solar_notes_for_commercial_ti():
    notes = get_state_expert_notes(
        "CA",
        "Los Angeles",
        "Commercial office tenant improvement; no solar, no PV, no battery energy storage, no residential work",
        primary_scope="commercial_office_ti",
    )
    text = _notes_text(notes)

    assert "cslb" in text or "contractors state license" in text
    assert "solar-ready" not in text
    assert "solar pv" not in text
    assert "battery" not in text


def test_commercial_ti_filter_keeps_core_commercial_guidance_categories():
    legacy_notes = [
        {"title": "ADA accessibility path of travel", "note": "Accessibility and path of travel review may apply."},
        {"title": "Occupant load and egress", "note": "Confirm occupant load, exits, and egress width."},
        {"title": "Mechanical ventilation", "note": "Commercial TI may need outdoor air ventilation calculations."},
        {"title": "Certificate of occupancy", "note": "A CO or TCO may be needed before opening."},
        {"title": "Residential ADU shot clock", "note": "ADU and SFR residential rule."},
    ]

    filtered = filter_state_expert_notes(
        legacy_notes,
        state="CA",
        city="Los Angeles",
        job_description="Commercial office tenant improvement",
        primary_scope="commercial_office_ti",
    )
    text = _notes_text(filtered)

    assert "accessibility" in text
    assert "occupant load" in text
    assert "egress" in text
    assert "mechanical ventilation" in text
    assert "certificate of occupancy" in text
    assert "residential adu" not in text


def test_residential_utility_note_does_not_leak_via_generic_utility_terms():
    legacy_notes = [
        {"title": "Residential utility coordination", "note": "Homeowner dwelling utility service rules."},
        {"title": "Commercial utility service", "note": "Commercial electrical service coordination."},
    ]

    filtered = filter_state_expert_notes(
        legacy_notes,
        state="CA",
        city="Los Angeles",
        job_description="Commercial office tenant improvement with electrical service coordination",
        primary_scope="commercial_office_ti",
    )
    text = _notes_text(filtered)

    assert "commercial utility service" in text
    assert "residential utility coordination" not in text
    assert "homeowner dwelling" not in text


def test_commercial_parking_garage_note_is_not_dropped_by_residential_garage_word():
    legacy_notes = [
        {"title": "Commercial parking garage ventilation", "note": "Mechanical ventilation and exhaust for a parking garage."},
        {"title": "Garage ADU conversion", "note": "Residential garage conversion rule."},
    ]

    filtered = filter_state_expert_notes(
        legacy_notes,
        state="CA",
        city="Los Angeles",
        job_description="Commercial office TI with parking garage exhaust ventilation",
        primary_scope="commercial_office_ti",
    )
    text = _notes_text(filtered)

    assert "parking garage ventilation" in text
    assert "garage adu conversion" not in text


def test_untagged_legacy_note_fails_closed_for_commercial_ti():
    filtered = filter_state_expert_notes(
        [{"title": "Legacy advisory", "note": "Confirm local process before submittal."}],
        state="CA",
        city="Los Angeles",
        job_description="Commercial office tenant improvement",
        primary_scope="commercial_office_ti",
    )

    assert filtered == []
