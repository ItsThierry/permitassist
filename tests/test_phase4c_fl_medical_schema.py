#!/usr/bin/env python3
"""Phase 4C Florida medical/dental clinic state-overlay tests."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine
from api.state_schema import compact_state_schema_context, get_state_rule_schema, validate_state_rule_schema


def _base_result(scope="commercial_medical_clinic_ti"):
    return {
        "_primary_scope": scope,
        "code_citation": {"section": "FBC 105.1", "text": "Permit required"},
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


def test_fl_medical_schema_is_phase4c_populated_with_real_sources():
    schema = get_state_rule_schema("FL")

    assert schema["phase"] == 4
    assert schema["coverage_level"] == "phase4c_fl_medical_clinic_ti"
    assert schema["population_status"] == "partially_populated"
    assert schema["requires_population_before_state_specific_claims"] is False
    assert validate_state_rule_schema(schema) == []

    source_blob = str(schema)
    assert "leg.state.fl.us/Statutes/index.cfm?App_mode=Display_Statute&URL=0500-0599/0553/Sections/0553.73.html" in source_blob
    assert "leg.state.fl.us/statutes/index.cfm?App_mode=Display_Statute&Search_String=&URL=0500-0599/0553/Sections/0553.502.html" in source_blob
    assert "ahca.myflorida.com/health-quality-assurance/bureau-of-health-facility-regulation/hospital-outpatient-services-unit/health-care-clinics" in source_blob
    assert "ahca.myflorida.com/health-quality-assurance/bureau-of-health-facility-regulation/hospital-outpatient-services-unit/ambulatory-surgical-center" in source_blob
    assert "floridahealth.gov/licensing-regulations/radiation-control/ionizing-radiation-machines-x-ray" in source_blob
    assert "leg.state.fl.us/statutes/index.cfm?App_mode=Display_Statute&Search_String=&URL=0400-0499/0489/Sections/0489.1136.html" in source_blob
    assert "flrules.org/gateway/ruleNo.asp?id=61G4-15.031" in source_blob
    assert "floridabuilding.org" in source_blob


def test_fl_private_medical_office_gets_fbc_accessibility_energy_without_ahca_overwarning():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Miami FL private medical office tenant improvement with exam rooms, restroom accessibility upgrades, lighting and HVAC alterations, no surgery or anesthesia, no x-ray, no nitrous",
        "Miami",
        "FL",
    )

    triggered = _triggered_ids(result)
    assert "fl_fbc_local_ahj_accessibility_baseline" in triggered
    assert "fl_energy_code_commercial_alteration_scope" in triggered
    assert "fl_ahca_health_care_clinic_license_check" not in triggered
    assert "fl_ahca_asc_opc_review_when_surgical" not in triggered
    assert "fl_doh_xray_registration_dental_medical" not in triggered
    assert "fl_medical_gas_certified_contractor" not in triggered
    assert result["occupancy_analysis"]["requires_i2_review"] is False

    text = _combined_text(result).lower()
    assert "florida state overlay" in text
    assert "texas state overlay" not in text
    assert "california state overlay" not in text
    assert "florida building code" in text
    assert "accessibility" in text


def test_fl_ahca_health_care_clinic_terms_trigger_license_check_without_asc():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Tampa FL AHCA licensed health care clinic tenant improvement with MRI services and portable equipment provider billing, no surgery or anesthesia",
        "Tampa",
        "FL",
    )

    triggered = _triggered_ids(result)
    assert "fl_ahca_health_care_clinic_license_check" in triggered
    assert "fl_ahca_asc_opc_review_when_surgical" not in triggered
    text = _combined_text(result).lower()
    assert "ahca" in text
    assert "licensure inspection" in text or "license" in text


def test_fl_dental_xray_triggers_doh_registration_only_when_in_scope():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Orlando FL dental clinic TI with panoramic x-ray, CBCT, sterilization, no surgery or anesthesia",
        "Orlando",
        "FL",
    )

    triggered = _triggered_ids(result)
    assert "fl_doh_xray_registration_dental_medical" in triggered
    assert "fl_ahca_asc_opc_review_when_surgical" not in triggered
    text = _combined_text(result).lower()
    assert "florida department of health" in text or "doh" in text
    assert "radiation" in text or "x-ray" in text


def test_fl_nitrous_medical_gas_triggers_certified_contractor_guidance():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Jacksonville FL dental clinic TI with nitrous, medical oxygen outlets, dental vacuum lines, zone valves, gas manifold, no surgery",
        "Jacksonville",
        "FL",
    )

    triggered = _triggered_ids(result)
    assert "fl_medical_gas_certified_contractor" in triggered
    text = _combined_text(result).lower()
    assert "medical gas" in text
    assert "32-hour" in text or "certified" in text


def test_fl_ambulatory_surgery_scope_triggers_ahca_asc_and_medgas_review():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Fort Lauderdale FL ambulatory surgery center tenant improvement with operating rooms, general anesthesia, PACU recovery bays, medical gas, no overnight stay",
        "Fort Lauderdale",
        "FL",
    )

    triggered = _triggered_ids(result)
    assert "fl_ahca_asc_opc_review_when_surgical" in triggered
    assert "fl_medical_gas_certified_contractor" in triggered
    assert result["occupancy_analysis"]["requires_i2_review"] is True
    assert result["needs_review"] is True
    text = _combined_text(result).lower()
    assert "ambulatory surgery center" in text or "asc" in text
    assert "office of plans and construction" in text or "ahca" in text


def test_fl_false_positive_terms_do_not_overwarn_ordinary_clinics():
    false_positive_scopes = [
        "Miami FL medical office or suite 200 tenant improvement with exam rooms, no surgery or anesthesia",
        "Tampa FL clinic TI replacing fire alarm panel, access controls, and low voltage devices, no surgery or anesthesia",
        "Orlando FL clinic TI with ultrasound imaging displays only, no x-ray, no surgery or anesthesia",
        "Naples FL dental clinic TI with oral sedation only, no surgery or anesthesia or PACU",
        "Boca Raton FL dermatology clinic with IV sedation room for minor procedures, no surgery or operating room or PACU",
        "St Petersburg FL clinic TI with central vacuum cleaner closet and oxygen sensor for HVAC monitoring, no surgery or anesthesia",
        "Sarasota FL clinic TI with natural gas outlet for water heater, mechanical room labels, and kitchenette, no medical gas, no nitrous, no surgery",
        "Miami FL private physician office tenant improvement, not an AHCA licensed health care clinic, no surgery",
    ]

    for scope in false_positive_scopes:
        result = engine.apply_medical_clinic_ti_rulebook(_base_result(), scope, "Miami", "FL")
        triggered = _triggered_ids(result)
        assert "fl_ahca_asc_opc_review_when_surgical" not in triggered, scope
        assert "fl_doh_xray_registration_dental_medical" not in triggered, scope
        assert "fl_medical_gas_certified_contractor" not in triggered, scope
        assert "fl_ahca_health_care_clinic_license_check" not in triggered, scope


def test_fl_layout_finish_only_scope_does_not_get_energy_forms_warning():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Miami FL medical clinic tenant improvement with paint, flooring, cabinets, and exam-room furniture only, no lighting, no HVAC, no mechanical, no water heating",
        "Miami",
        "FL",
    )

    triggered = _triggered_ids(result)
    assert "fl_fbc_local_ahj_accessibility_baseline" in triggered
    assert "fl_energy_code_commercial_alteration_scope" not in triggered


def test_fl_negation_does_not_suppress_later_real_xray_or_medgas_scope():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Miami FL dental clinic TI, no overnight stay, panoramic x-ray and nitrous with gas manifold, no surgery or anesthesia",
        "Miami",
        "FL",
    )

    triggered = _triggered_ids(result)
    assert "fl_ahca_asc_opc_review_when_surgical" not in triggered
    assert "fl_doh_xray_registration_dental_medical" in triggered
    assert "fl_medical_gas_certified_contractor" in triggered


def test_fl_rule_injection_is_idempotent_for_customer_fields():
    result = _base_result()
    scope = "Miami FL dental clinic TI with panoramic x-ray, nitrous, medical gas outlets, no surgery or anesthesia"
    first = engine.apply_medical_clinic_ti_rulebook(result, scope, "Miami", "FL")
    second = engine.apply_medical_clinic_ti_rulebook(first, scope, "Miami", "FL")

    for key in ("pro_tips", "what_to_bring", "watch_out"):
        values = second.get(key, [])
        assert len(values) == len({str(value).strip().lower() for value in values}), key

    permit_types = [str(item.get("permit_type") or "").strip().lower() for item in second.get("companion_permits", []) if isinstance(item, dict)]
    assert len(permit_types) == len(set(permit_types))


def test_phase4c_healthcare_rules_do_not_leak_into_office_or_ma_unpopulated_state():
    office = engine.apply_state_schema_context(
        _base_result("commercial_office_ti"),
        "Miami FL office tenant improvement with conference rooms and data cabling",
        "Miami",
        "FL",
    )
    assert office["state_schema_context"]["vertical"] == "office_ti"
    assert office["state_schema_context"]["triggered_rules"] == []

    ma = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Boston MA dental clinic TI with x-ray and nitrous",
        "Boston",
        "MA",
    )
    assert ma["state_schema_context"]["state"] == "MA"
    assert ma["state_schema_context"]["population_status"] == "not_populated"
    assert ma["state_schema_context"]["triggered_rules"] == []


def test_phase4c_sources_stay_out_of_code_citation_until_renderers_are_ready():
    result = _base_result()
    before = result["code_citation"].copy()
    result = engine.apply_state_schema_context(
        result,
        "Miami FL dental clinic tenant improvement with x-ray and nitrous",
        "Miami",
        "FL",
    )

    assert result["code_citation"] == before
    citation_blob = str(result.get("code_citation", ""))
    assert "floridahealth.gov" not in citation_blob
    assert "ahca.myflorida.com" not in citation_blob
    assert "leg.state.fl.us" not in citation_blob
    assert "flrules.org" not in citation_blob
    assert "floridabuilding.org" not in citation_blob
