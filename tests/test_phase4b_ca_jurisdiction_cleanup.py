#!/usr/bin/env python3
"""Phase 4B-1 California jurisdiction/source-scope cleanup regressions."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.state_packs import _california_utility_scope_requested, get_state_expert_notes


def _notes_text(notes):
    return json.dumps(notes, sort_keys=True).lower()


def _assert_no_terms(text: str, forbidden: list[str]):
    leaked = [term for term in forbidden if term in text]
    assert not leaked, f"leaked {leaked}: {text}"


def test_san_diego_medical_ti_gets_ca_state_fallback_not_la_or_residential_notes():
    notes = get_state_expert_notes(
        "CA",
        "San Diego",
        "Medical clinic tenant improvement with exam rooms, sinks, lighting, and HVAC; no residential, no ADU, no solar",
        primary_scope="commercial_medical_clinic_ti",
    )
    text = _notes_text(notes)

    assert "california state-level guidance applies" in text
    assert "verify san diego city-specific portal" in text
    assert "building standards code" in text
    _assert_no_terms(
        text,
        [
            "ladwp",
            "pasadena",
            "la county",
            "los angeles county",
            "los angeles department",
            "adu",
            "sb 9",
            "sfr",
            "solar-ready",
        ],
    )


def test_san_jose_office_ti_gets_ca_state_fallback_not_borrowed_la_pasadena_notes():
    notes = get_state_expert_notes(
        "CA",
        "San Jose",
        "Office tenant improvement with partitions, lighting controls, data cabling, and HVAC diffuser relocation; no residential or solar work",
        primary_scope="commercial_office_ti",
    )
    text = _notes_text(notes)

    assert "california state-level guidance applies" in text
    assert "verify san jose city-specific portal" in text
    _assert_no_terms(text, ["ladwp", "pasadena", "los angeles county", "la county", "bungalow heaven"])


def test_los_angeles_office_ti_keeps_city_utility_out_unless_utility_scope_is_explicit():
    notes = get_state_expert_notes(
        "CA",
        "Los Angeles",
        "Office tenant improvement with partitions, lighting controls, data cabling, and HVAC; no solar, no service upgrade, no utility interconnection, no residential work",
        primary_scope="commercial_office_ti",
    )
    text = _notes_text(notes)

    assert "building standards code" in text
    assert "cslb" in text or "contractors state license" in text
    _assert_no_terms(text, ["ladwp", "la county", "los angeles county", "adu", "sb 9", "solar-ready", "ev-ready"])


def test_los_angeles_explicit_commercial_service_scope_keeps_ladwp_without_residential_leakage():
    notes = get_state_expert_notes(
        "CA",
        "Los Angeles",
        "Restaurant tenant improvement with electrical service upgrade and utility coordination for new kitchen equipment; no residential, no ADU, no solar",
        primary_scope="commercial_restaurant_ti",
    )
    text = _notes_text(notes)

    assert "ladwp" in text
    assert "electrical service and utility coordination" in text
    _assert_no_terms(text, ["adu 60-day", "sb 9", "solar-ready", "ev-ready", "single-family"])


def test_pasadena_adu_still_gets_residential_jurisdiction_notes():
    notes = get_state_expert_notes(
        "CA",
        "Pasadena",
        "Garage ADU conversion with exterior work, new utility coordination, and address-specific wildfire review",
        primary_scope="residential_adu",
    )
    text = _notes_text(notes)

    assert "65852.2" in text
    assert "pasadena water and power" in text or "pwp" in text
    assert "bungalow heaven" in text
    assert "vhfhsz" in text


def test_sacramento_utility_scope_uses_generic_iou_contrast_not_wrong_socal_pge_reference():
    notes = get_state_expert_notes(
        "CA",
        "Sacramento",
        "Restaurant tenant improvement with electrical service upgrade and utility coordination for new kitchen equipment; no solar or battery work",
        primary_scope="commercial_restaurant_ti",
    )
    text = _notes_text(notes)

    assert "smud" in text
    assert "surrounding investor-owned utility" in text
    _assert_no_terms(text, ["not socal edison", "not pg&e"])


def test_pasadena_office_ti_filters_historic_overlay_and_pwp_without_utility_scope():
    notes = get_state_expert_notes(
        "CA",
        "Pasadena",
        "Office tenant improvement with partitions, lighting controls, and HVAC; no exterior changes and no utility service upgrade",
        primary_scope="commercial_office_ti",
    )
    text = _notes_text(notes)

    assert "building standards code" in text
    _assert_no_terms(text, ["pasadena water and power", "pwp", "bungalow heaven", "historic district overlay"])


def test_pasadena_explicit_commercial_service_scope_keeps_pwp_without_historic_or_residential_leakage():
    notes = get_state_expert_notes(
        "CA",
        "Pasadena",
        "Office tenant improvement with new electrical service upgrade and utility coordination for tenant equipment; no exterior changes, no ADU, no solar",
        primary_scope="commercial_office_ti",
    )
    text = _notes_text(notes)

    assert "pasadena water and power" in text or "pwp" in text
    _assert_no_terms(text, ["bungalow heaven", "historic district overlay", "adu 60-day", "sb 9", "solar-ready"])


def test_california_utility_scope_detector_is_clause_negation_aware_and_qualified():
    assert _california_utility_scope_requested("Restaurant TI with electrical service upgrade", "commercial_restaurant_ti")
    assert _california_utility_scope_requested("Office TI with utility coordination for new tenant equipment", "commercial_office_ti")
    assert _california_utility_scope_requested("Restaurant TI", "commercial_restaurant_ti service panel")
    assert _california_utility_scope_requested("Install new electrical meter base for tenant service", "commercial_office_ti")

    assert not _california_utility_scope_requested(
        "Office tenant improvement; tenant does not currently anticipate any utility interconnection upgrades",
        "commercial_office_ti",
    )
    assert not _california_utility_scope_requested("Restaurant TI with gas meter relocation only", "commercial_restaurant_ti")
    assert not _california_utility_scope_requested("Office TI with submeter trim and no electrical service change", "commercial_office_ti")


def test_data_interconnection_does_not_trigger_ca_municipal_utility_note():
    assert not _california_utility_scope_requested(
        "Office TI with data interconnection between suites; no electrical service upgrade",
        "commercial_office_ti",
    )


def test_non_ca_municipal_utility_solar_der_notes_stay_filtered_from_commercial_ti():
    notes = get_state_expert_notes(
        "GA",
        "Atlanta",
        "Office tenant improvement with partitions, lighting controls, data cabling, and HVAC diffuser relocation; no solar, no battery, no generator, no utility interconnection",
        primary_scope="commercial_office_ti",
    )
    text = _notes_text(notes)

    _assert_no_terms(text, ["meag", "municipal utility interconnection", "solar/der", "dg interconnection"])


def test_ca_public_note_shape_remains_legacy_compatible_after_cleanup():
    notes = get_state_expert_notes(
        "CA",
        "San Diego",
        "Office tenant improvement",
        primary_scope="commercial_office_ti",
    )

    assert notes
    for note in notes:
        assert {"title", "note", "applies_to", "source"}.issubset(note)
        assert not {"vertical_scope", "jurisdiction_scope", "jurisdictions", "use_class"}.intersection(note)
