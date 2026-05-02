#!/usr/bin/env python3
"""Phase 4A Texas medical/dental clinic state-overlay tests."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine
from api.state_schema import compact_state_schema_context, get_state_rule_schema, validate_state_rule_schema


def _base_result(scope="commercial_medical_clinic_ti"):
    return {
        "_primary_scope": scope,
        "code_citation": {"section": "IBC 105.1", "text": "Permit required"},
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


def test_tx_medical_schema_is_phase4a_populated_with_real_official_sources():
    schema = get_state_rule_schema("TX")

    assert schema["phase"] == 4
    assert schema["coverage_level"] == "phase4a_tx_medical_clinic_ti"
    assert schema["population_status"] == "partially_populated"
    assert schema["requires_population_before_state_specific_claims"] is False
    assert validate_state_rule_schema(schema) == []

    overlays = schema["healthcare_overlays"]
    assert overlays["accessibility"]["status"] == "populated"
    assert overlays["radiology_xray"]["status"] == "populated"
    assert overlays["ambulatory_care_thresholds"]["status"] == "populated"

    source_blob = str(schema)
    assert "tdlr.texas.gov/ab" in source_blob
    assert "hhs.texas.gov/providers/health-care-facilities-regulation/ambulatory-surgical-centers" in source_blob
    assert "statutes.capitol.texas.gov/GetStatute.aspx?Code=HS&Value=243" in source_blob
    assert "dshs.texas.gov/texas-radiation-control/x-ray-machines-x-ray-services/dental-x-ray-machine" in source_blob
    assert "statutes.capitol.texas.gov/Docs/LG/htm/LG.214.htm#214.216" in source_blob


def test_tx_ordinary_medical_clinic_gets_accessibility_and_local_ahj_guidance_without_asc_overwarning():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Austin TX medical clinic tenant improvement with exam rooms, no surgery, no anesthesia, no PACU, no overnight stay, no x-ray, no nitrous",
        "Austin",
        "TX",
    )

    context = result["state_schema_context"]
    assert context["phase"] == 4
    triggered = {rule["id"] for rule in context["triggered_rules"]}
    assert "tx_accessibility_tdlr_tas" in triggered
    assert "tx_municipal_ibc_local_ahj" in triggered
    assert "tx_asc_license_required_when_primary_surgical_services" not in triggered
    assert "tx_dental_xray_registration" not in triggered
    assert "tx_medical_gas_verify_local_nfp99" not in triggered
    assert result["occupancy_analysis"]["requires_i2_review"] is False

    text = _combined_text(result).lower()
    assert "texas accessibility" in text or "tdlr" in text
    assert "texas ambulatory surgical center license" not in text


def test_tx_dental_xray_triggers_radiation_registration_guidance_only_when_in_scope():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Dallas TX dental clinic TI adding two operatories, panoramic x-ray, cone beam CT, sterilization, no surgery or anesthesia",
        "Dallas",
        "TX",
    )

    triggered = {rule["id"] for rule in result["state_schema_context"]["triggered_rules"]}
    assert "tx_dental_xray_registration" in triggered
    assert result["code_citation"] != []
    text = _combined_text(result).lower()
    assert "dshs" in text
    assert "x-ray" in text or "radiation" in text
    assert "shielding" in text or "registration" in text


def test_tx_nitrous_oxygen_vacuum_triggers_medical_gas_guidance():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Houston TX dental office tenant improvement with nitrous, oxygen, vacuum lines, zone valves, and alarms, no surgery",
        "Houston",
        "TX",
    )

    triggered = {rule["id"] for rule in result["state_schema_context"]["triggered_rules"]}
    assert "tx_medical_gas_verify_local_nfp99" in triggered
    text = _combined_text(result).lower()
    assert "medical gas" in text or "nitrous" in text
    assert "local ahj" in text


def test_tx_surgical_asc_scope_triggers_asc_licensing_and_higher_risk_review():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Plano TX outpatient surgery center TI with operating rooms, anesthesia, PACU recovery bays, oxygen and medical gas, no overnight stay",
        "Plano",
        "TX",
    )

    triggered = {rule["id"] for rule in result["state_schema_context"]["triggered_rules"]}
    assert "tx_asc_license_required_when_primary_surgical_services" in triggered
    assert "tx_medical_gas_verify_local_nfp99" in triggered
    assert result["occupancy_analysis"]["requires_i2_review"] is True
    assert result["needs_review"] is True
    text = _combined_text(result).lower()
    assert "ambulatory surgical center" in text
    assert "chapter 243" in text or "hhsc" in text


def test_tx_phase4a_healthcare_rules_do_not_leak_into_office_ti_or_other_states():
    office = engine.apply_state_schema_context(
        _base_result("commercial_office_ti"),
        "Dallas TX office tenant improvement with conference rooms and data cabling",
        "Dallas",
        "TX",
    )
    assert office["state_schema_context"]["vertical"] == "office_ti"
    assert "triggered_rules" not in office["state_schema_context"] or office["state_schema_context"]["triggered_rules"] == []

    ma = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Boston MA dental clinic TI with x-ray and nitrous",
        "Boston",
        "MA",
    )
    assert ma["state_schema_context"]["state"] == "MA"
    assert ma["state_schema_context"]["population_status"] == "not_populated"
    assert "triggered_rules" not in ma["state_schema_context"] or ma["state_schema_context"]["triggered_rules"] == []


def test_phase4a_false_positive_terms_do_not_overwarn_ordinary_clinics():
    false_positive_scopes = [
        "Dallas TX medical office or suite 200 tenant improvement with exam rooms, no surgery or anesthesia",
        "Austin TX clinic TI replacing fire alarm panel and low voltage devices, no surgery or anesthesia",
        "Houston TX clinic TI with ultrasound imaging displays only, no x-ray, no surgery or anesthesia",
        "Fort Worth TX dental clinic TI with oral sedation only, no surgery or anesthesia or PACU",
        "Plano TX clinic TI with central vacuum cleaner closet and oxygen sensor for HVAC monitoring, no surgery or anesthesia",
    ]

    for scope in false_positive_scopes:
        result = engine.apply_medical_clinic_ti_rulebook(_base_result(), scope, "Dallas", "TX")
        triggered = {rule["id"] for rule in result["state_schema_context"]["triggered_rules"]}
        assert "tx_asc_license_required_when_primary_surgical_services" not in triggered, scope
        assert "tx_dental_xray_registration" not in triggered, scope
        assert "tx_medical_gas_verify_local_nfp99" not in triggered, scope


def test_phase4a_negation_does_not_suppress_later_real_xray_or_medgas_scope():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Dallas TX dental clinic TI, no overnight stay, panoramic x-ray and nitrous with gas manifold, no surgery or anesthesia",
        "Dallas",
        "TX",
    )

    triggered = {rule["id"] for rule in result["state_schema_context"]["triggered_rules"]}
    assert "tx_asc_license_required_when_primary_surgical_services" not in triggered
    assert "tx_dental_xray_registration" in triggered
    assert "tx_medical_gas_verify_local_nfp99" in triggered


def test_phase4a_rule_injection_is_idempotent_for_customer_fields():
    result = _base_result()
    scope = "Dallas TX dental clinic TI with panoramic x-ray, nitrous, medical gas outlets, no surgery or anesthesia"
    first = engine.apply_medical_clinic_ti_rulebook(result, scope, "Dallas", "TX")
    second = engine.apply_medical_clinic_ti_rulebook(first, scope, "Dallas", "TX")

    for key in ("pro_tips", "what_to_bring", "watch_out"):
        values = second.get(key, [])
        assert len(values) == len({str(value).strip().lower() for value in values}), key

    permit_types = [str(item.get("permit_type") or "").strip().lower() for item in second.get("companion_permits", []) if isinstance(item, dict)]
    assert len(permit_types) == len(set(permit_types))


def test_phase4a_source_quotes_are_snapshot_verified():
    # These source_quote snapshots were manually verified against the saved Phase 4A
    # research file on 2026-05-02. If the text changes, re-check the source and update
    # the research artifact instead of silently weakening citation quality.
    schema = get_state_rule_schema("TX")
    source_quotes = str(schema)
    assert "applies to all commercial buildings in a municipality" in source_quotes
    assert "less than $50,000.00" in source_quotes
    assert "Chapter 243 establishes the state licensing requirements for ASCs" in source_quotes
    assert "certificate of registration for dental radiation machines" in source_quotes


def test_phase4a_real_state_sources_stay_out_of_code_citation_until_renderers_are_ready():
    result = _base_result()
    before = result["code_citation"].copy()
    result = engine.apply_state_schema_context(
        result,
        "Austin TX dental clinic tenant improvement with x-ray and nitrous",
        "Austin",
        "TX",
    )

    assert result["code_citation"] == before
    assert "state_schema_context" in result
    citation_blob = str(result.get("code_citation", ""))
    assert "tdlr.texas.gov" not in citation_blob
    assert "dshs.texas.gov" not in citation_blob
