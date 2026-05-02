#!/usr/bin/env python3
"""Phase 4D Massachusetts medical/dental clinic state-overlay tests."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine
from api.state_schema import compact_state_schema_context, get_state_rule_schema, validate_state_rule_schema


def _base_result(scope="commercial_medical_clinic_ti"):
    return {
        "_primary_scope": scope,
        "code_citation": {"section": "Existing verified citation", "text": "Permit required"},
        "permits_required": [{"permit_type": "Building Permit — Tenant Improvement"}],
        "companion_permits": [],
        "inspections": [],
        "what_to_bring": [],
        "common_mistakes": [],
        "pro_tips": [],
        "watch_out": [],
    }


def _combined_text(result):
    return "\n".join(str(result.get(key, "")) for key in ("watch_out", "what_to_bring", "pro_tips", "companion_permits", "state_schema_context"))


def _triggered_ids(result):
    return {rule["id"] for rule in result["state_schema_context"]["triggered_rules"]}


def test_ma_medical_schema_is_phase4d_populated_with_real_sources():
    schema = get_state_rule_schema("MA")

    assert schema["phase"] == 4
    assert schema["coverage_level"] == "phase4d_ma_medical_clinic_ti"
    assert schema["population_status"] == "partially_populated"
    assert schema["requires_population_before_state_specific_claims"] is False
    assert validate_state_rule_schema(schema) == []

    source_blob = str(schema)
    assert "mass.gov/doc/bbrs-10th-edition-building-code/download" in source_blob
    assert "mass.gov/info-details/physical-accessibility-requirements" in source_blob
    assert "mass.gov/doc/105-cmr-140-licensure-of-clinics/download" in source_blob
    assert "mass.gov/regulations/105-CMR-12000-the-control-of-radiation" in source_blob
    assert "mass.gov/regulations/248-CMR-1000-uniform-state-plumbing-code" in source_blob
    assert "mass.gov/regulations/248-CMR-300-general-provisions" in source_blob
    assert "mass.gov/doc/7th-edition-780-cmr-massachusetts-building-code-780-cmr-1300-energy-conservation/download" in source_blob


def test_ma_private_medical_office_gets_code_accessibility_energy_without_dph_overwarning():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Boston MA private medical office tenant improvement with exam rooms, restroom accessibility upgrades, lighting and HVAC alterations, no DPH clinic license, no surgery or anesthesia, no x-ray, no nitrous",
        "Boston",
        "MA",
    )

    triggered = _triggered_ids(result)
    assert "ma_780cmr_local_ahj_accessibility_baseline" in triggered
    assert "ma_energy_code_commercial_alteration_scope" in triggered
    assert "ma_dph_clinic_license_plan_review" not in triggered
    assert "ma_dph_asc_surgery_license_review" not in triggered
    assert "ma_dph_radiation_control_xray" not in triggered
    assert "ma_plumbing_gas_medical_gas_coordination" not in triggered
    assert result["occupancy_analysis"]["requires_i2_review"] is False

    text = _combined_text(result).lower()
    assert "massachusetts state overlay" in text
    assert "texas state overlay" not in text
    assert "california state overlay" not in text
    assert "florida state overlay" not in text
    assert "massachusetts state building code" in text or "780 cmr" in text
    assert "accessibility" in text


def test_ma_dph_clinic_license_terms_trigger_plan_review_without_asc():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Cambridge MA DPH licensed clinic tenant improvement with urgent care clinic services and architectural plans for alterations, no surgery or anesthesia",
        "Cambridge",
        "MA",
    )

    triggered = _triggered_ids(result)
    assert "ma_dph_clinic_license_plan_review" in triggered
    assert "ma_dph_asc_surgery_license_review" not in triggered
    text = _combined_text(result).lower()
    assert "dph" in text
    assert "clinic license" in text or "licensure" in text
    assert "architectural plans" in text or "prior written" in text


def test_ma_dental_xray_triggers_radiation_control_only_when_in_scope():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Worcester MA dental clinic TI with panoramic x-ray, CBCT, sterilization, no surgery or anesthesia",
        "Worcester",
        "MA",
    )

    triggered = _triggered_ids(result)
    assert "ma_dph_radiation_control_xray" in triggered
    assert "ma_dph_asc_surgery_license_review" not in triggered
    text = _combined_text(result).lower()
    assert "radiation" in text or "x-ray" in text
    assert "105 cmr 120" in text or "department of public health" in text


def test_ma_nitrous_medical_gas_triggers_plumbing_and_gas_coordination():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Springfield MA dental clinic TI with nitrous, medical oxygen outlets, dental vacuum lines, zone valves, gas manifold, no surgery",
        "Springfield",
        "MA",
    )

    triggered = _triggered_ids(result)
    assert "ma_plumbing_gas_medical_gas_coordination" in triggered
    text = _combined_text(result).lower()
    assert "248 cmr" in text
    assert "medical gas" in text or "nitrous" in text
    assert "plumbing" in text or "gas fitting" in text


def test_ma_ambulatory_surgery_scope_triggers_dph_asc_and_medgas_review():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Boston MA ambulatory surgery center tenant improvement with operating rooms, general anesthesia, PACU recovery bays, medical gas, no overnight stay",
        "Boston",
        "MA",
    )

    triggered = _triggered_ids(result)
    assert "ma_dph_asc_surgery_license_review" in triggered
    assert "ma_plumbing_gas_medical_gas_coordination" in triggered
    assert result["occupancy_analysis"]["requires_i2_review"] is True
    assert result["needs_review"] is True
    text = _combined_text(result).lower()
    assert "ambulatory surgery" in text or "asc" in text
    assert "dph" in text


def test_ma_false_positive_terms_do_not_overwarn_ordinary_clinics():
    false_positive_scopes = [
        "Boston MA medical office or suite 200 tenant improvement with exam rooms, no surgery or anesthesia",
        "Cambridge MA clinic TI replacing fire alarm panel, access controls, and low voltage devices, no surgery or anesthesia",
        "Worcester MA clinic TI with ultrasound imaging displays only, no x-ray, no surgery or anesthesia",
        "Newton MA dental clinic TI with oral sedation only, no surgery or anesthesia or PACU",
        "Lowell MA dermatology clinic with IV sedation room for minor procedures, no surgery or operating room or PACU",
        "Quincy MA clinic TI with central vacuum cleaner closet and oxygen sensor for HVAC monitoring, no surgery or anesthesia",
        "Somerville MA clinic TI with natural gas outlet for water heater, mechanical room labels, and kitchenette, no medical gas, no nitrous, no surgery",
        "Boston MA private physician office tenant improvement, not a DPH licensed clinic, no surgery",
    ]

    for scope in false_positive_scopes:
        result = engine.apply_medical_clinic_ti_rulebook(_base_result(), scope, "Boston", "MA")
        triggered = _triggered_ids(result)
        assert "ma_dph_asc_surgery_license_review" not in triggered, scope
        assert "ma_dph_radiation_control_xray" not in triggered, scope
        assert "ma_plumbing_gas_medical_gas_coordination" not in triggered, scope
        assert "ma_dph_clinic_license_plan_review" not in triggered, scope


def test_ma_layout_finish_only_scope_does_not_get_energy_forms_warning():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Boston MA medical clinic tenant improvement with paint, flooring, cabinets, and exam-room furniture only, no lighting, no HVAC, no mechanical, no water heating",
        "Boston",
        "MA",
    )

    triggered = _triggered_ids(result)
    assert "ma_780cmr_local_ahj_accessibility_baseline" in triggered
    assert "ma_energy_code_commercial_alteration_scope" not in triggered


def test_ma_negation_does_not_suppress_later_real_xray_or_medgas_scope():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Boston MA dental clinic TI, no overnight stay, panoramic x-ray and nitrous with gas manifold, no surgery or anesthesia",
        "Boston",
        "MA",
    )

    triggered = _triggered_ids(result)
    assert "ma_dph_asc_surgery_license_review" not in triggered
    assert "ma_dph_radiation_control_xray" in triggered
    assert "ma_plumbing_gas_medical_gas_coordination" in triggered


def test_ma_rule_injection_is_idempotent_for_customer_fields():
    result = _base_result()
    scope = "Boston MA dental clinic TI with panoramic x-ray, nitrous, medical gas outlets, no surgery or anesthesia"
    first = engine.apply_medical_clinic_ti_rulebook(result, scope, "Boston", "MA")
    second = engine.apply_medical_clinic_ti_rulebook(first, scope, "Boston", "MA")

    for key in ("pro_tips", "what_to_bring", "watch_out"):
        values = second.get(key, [])
        assert len(values) == len({str(value).strip().lower() for value in values}), key

    permit_types = [str(item.get("permit_type") or "").strip().lower() for item in second.get("companion_permits", []) if isinstance(item, dict)]
    assert len(permit_types) == len(set(permit_types))


def test_phase4d_healthcare_rules_do_not_leak_into_office_ti():
    office = engine.apply_state_schema_context(
        _base_result("commercial_office_ti"),
        "Boston MA office tenant improvement with conference rooms and data cabling",
        "Boston",
        "MA",
    )
    assert office["state_schema_context"]["vertical"] == "office_ti"
    assert office["state_schema_context"]["state"] == "MA"
    assert office["state_schema_context"]["triggered_rules"] == []


def test_phase4d_sources_stay_out_of_code_citation_until_renderers_are_ready():
    result = _base_result()
    before = result["code_citation"].copy()
    result = engine.apply_state_schema_context(
        result,
        "Boston MA dental clinic tenant improvement with x-ray and nitrous",
        "Boston",
        "MA",
    )

    assert result["code_citation"] == before
    citation_blob = str(result.get("code_citation", ""))
    assert "mass.gov" not in citation_blob
    assert "105 CMR" not in citation_blob
    assert "248 CMR" not in citation_blob
    assert "780 CMR" not in citation_blob


def test_ma_compact_context_invariants_are_clean_after_phase4d():
    schema = get_state_rule_schema("MA")
    assert validate_state_rule_schema(schema) == []
    context = compact_state_schema_context(
        "MA",
        "medical_clinic_ti",
        "Boston MA dental clinic TI with x-ray and nitrous medical gas, lighting and HVAC controls, no surgery or anesthesia",
    )
    assert context["phase"] == 4
    assert context["coverage_level"] == "phase4d_ma_medical_clinic_ti"
    assert context["population_status"] == "partially_populated"
    assert context["triggered_rules"]
    assert "code_citation" not in str(context).lower()
