#!/usr/bin/env python3
"""Phase 4B California medical/dental clinic state-overlay tests."""

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


def _triggered_ids(result):
    return {rule["id"] for rule in result["state_schema_context"]["triggered_rules"]}


def test_ca_medical_schema_is_phase4b_populated_with_real_sources():
    schema = get_state_rule_schema("CA")

    assert schema["phase"] == 4
    assert schema["coverage_level"] == "phase4b_ca_medical_clinic_ti"
    assert schema["population_status"] == "partially_populated"
    assert schema["requires_population_before_state_specific_claims"] is False
    assert validate_state_rule_schema(schema) == []

    source_blob = str(schema)
    assert "dgs.ca.gov/bsc/codes" in source_blob
    assert "hcai.ca.gov/facilities/building-safety/codes-and-regulations" in source_blob
    assert "cdph.ca.gov/Programs/CHCQ/LCP/Pages/Primary-Care-Clinic-FAQs.aspx" in source_blob
    assert "cdph.ca.gov/Programs/CHCQ/LCP/Pages/Ambulatory-Surgery-Center-FAQs.aspx" in source_blob
    assert "cdph.ca.gov/Programs/CEH/DRSEM/pages/rhb-x-ray/registration.aspx" in source_blob
    assert "energycodeace.com/content/get-forms" in source_blob


def test_ca_ordinary_private_medical_office_gets_title24_energy_without_cdph_overwarning():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Los Angeles CA private medical office tenant improvement with exam rooms, lighting and HVAC alterations, no surgery or anesthesia, no x-ray, no nitrous",
        "Los Angeles",
        "CA",
    )

    triggered = _triggered_ids(result)
    assert "ca_title24_local_ahj_oshpd3_awareness" in triggered
    assert "ca_title24_part6_energy_forms_nonresidential_ti" in triggered
    assert "ca_cdph_pcc_license_when_primary_care_clinic" not in triggered
    assert "ca_surgc_asc_license_certification_trigger" not in triggered
    assert "ca_rhb_xray_registration_dental_medical" not in triggered
    assert "ca_cpc_dental_medgas_vacuum" not in triggered
    assert result["occupancy_analysis"]["requires_i2_review"] is False

    text = _combined_text(result).lower()
    assert "california state overlay" in text
    assert "texas state overlay" not in text
    assert "title 24" in text
    assert "state license is required to operate a surgc" not in text


def test_ca_primary_care_clinic_terms_trigger_cdph_hcai_path_without_asc():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Oakland CA licensed primary care clinic HSC 1200 tenant improvement with exam rooms, no surgery or anesthesia",
        "Oakland",
        "CA",
    )

    triggered = _triggered_ids(result)
    assert "ca_cdph_pcc_license_when_primary_care_clinic" in triggered
    assert "ca_surgc_asc_license_certification_trigger" not in triggered
    text = _combined_text(result).lower()
    assert "cdph" in text
    assert "oshpd 3" in text or "hcai" in text


def test_ca_dental_xray_triggers_rhb_registration_only_when_in_scope():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "San Diego CA dental clinic TI with panoramic x-ray, CBCT, sterilization, no surgery or anesthesia",
        "San Diego",
        "CA",
    )

    triggered = _triggered_ids(result)
    assert "ca_rhb_xray_registration_dental_medical" in triggered
    assert "ca_surgc_asc_license_certification_trigger" not in triggered
    text = _combined_text(result).lower()
    assert "radiologic health branch" in text or "rhb" in text
    assert "within 30 days" in text or "registration" in text


def test_ca_nitrous_medical_gas_triggers_cpc_local_verifier_guidance():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Sacramento CA dental clinic TI with nitrous, medical oxygen outlets, dental vacuum lines, zone valves, gas manifold, no surgery",
        "Sacramento",
        "CA",
    )

    triggered = _triggered_ids(result)
    assert "ca_cpc_dental_medgas_vacuum" in triggered
    text = _combined_text(result).lower()
    assert "dental gas" in text or "medical gas" in text
    assert "local" in text


def test_ca_surgical_asc_scope_triggers_surgc_asc_warning_and_review():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Irvine CA ambulatory surgery center tenant improvement with operating rooms, general anesthesia, PACU recovery bays, medical gas, no overnight stay",
        "Irvine",
        "CA",
    )

    triggered = _triggered_ids(result)
    assert "ca_surgc_asc_license_certification_trigger" in triggered
    assert "ca_cpc_dental_medgas_vacuum" in triggered
    assert result["occupancy_analysis"]["requires_i2_review"] is True
    assert result["needs_review"] is True
    text = _combined_text(result).lower()
    assert "surgc" in text or "ambulatory surgery" in text
    assert "cdph" in text


def test_ca_false_positive_terms_do_not_overwarn_ordinary_clinics():
    false_positive_scopes = [
        "Los Angeles CA medical office or suite 200 tenant improvement with exam rooms, no surgery or anesthesia",
        "San Jose CA clinic TI replacing fire alarm panel and low voltage devices, no surgery or anesthesia",
        "Oakland CA clinic TI with ultrasound imaging displays only, no x-ray, no surgery or anesthesia",
        "Fresno CA dental clinic TI with oral sedation only, no surgery or anesthesia or PACU",
        "Beverly Hills CA dermatology clinic with IV sedation room for minor procedures, no surgery or operating room or PACU",
        "Anaheim CA clinic TI with central vacuum cleaner closet and oxygen sensor for HVAC monitoring, no surgery or anesthesia",
        "Pasadena CA clinic TI with natural gas outlet for water heater and kitchenette, no medical gas, no nitrous, no surgery",
        "Santa Monica CA primary care office tenant improvement, private physician practice, not HSC 1200 licensed clinic, no surgery",
    ]

    for scope in false_positive_scopes:
        result = engine.apply_medical_clinic_ti_rulebook(_base_result(), scope, "Los Angeles", "CA")
        triggered = _triggered_ids(result)
        assert "ca_surgc_asc_license_certification_trigger" not in triggered, scope
        assert "ca_rhb_xray_registration_dental_medical" not in triggered, scope
        assert "ca_cpc_dental_medgas_vacuum" not in triggered, scope
        assert "ca_cdph_pcc_license_when_primary_care_clinic" not in triggered, scope


def test_ca_layout_finish_only_scope_does_not_get_energy_forms_warning():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Los Angeles CA medical clinic tenant improvement with paint, flooring, cabinets, and exam-room furniture only, no lighting, no HVAC, no mechanical, no water heating",
        "Los Angeles",
        "CA",
    )

    triggered = _triggered_ids(result)
    assert "ca_title24_local_ahj_oshpd3_awareness" in triggered
    assert "ca_title24_part6_energy_forms_nonresidential_ti" not in triggered


def test_ca_populated_metadata_and_tx_refactor_regression():
    ca = get_state_rule_schema("CA")
    tx = get_state_rule_schema("TX")

    assert "medical_clinic_ti" in ca["populated_verticals"]
    assert "medical_clinic_ti" in tx["populated_verticals"]
    assert ca["citation_policy"]["phase4b_note"]
    assert tx["citation_policy"]["phase4a_note"]

    ca_hooks = [hook for slot in ca["healthcare_overlays"].values() for hook in slot["citation_hooks"] if hook.get("citation_status") == "verified"]
    tx_hooks = [hook for slot in tx["healthcare_overlays"].values() for hook in slot["citation_hooks"] if hook.get("citation_status") == "verified"]
    assert ca_hooks and all(hook["verified_on"] == "2026-05-02" for hook in ca_hooks)
    assert tx_hooks and all(hook["verified_on"] == "2026-05-02" for hook in tx_hooks)


def test_ca_negation_does_not_suppress_later_real_xray_or_medgas_scope():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "San Diego CA dental clinic TI, no overnight stay, panoramic x-ray and nitrous with gas manifold, no surgery or anesthesia",
        "San Diego",
        "CA",
    )

    triggered = _triggered_ids(result)
    assert "ca_surgc_asc_license_certification_trigger" not in triggered
    assert "ca_rhb_xray_registration_dental_medical" in triggered
    assert "ca_cpc_dental_medgas_vacuum" in triggered


def test_ca_rule_injection_is_idempotent_for_customer_fields():
    result = _base_result()
    scope = "Los Angeles CA dental clinic TI with panoramic x-ray, nitrous, medical gas outlets, no surgery or anesthesia"
    first = engine.apply_medical_clinic_ti_rulebook(result, scope, "Los Angeles", "CA")
    second = engine.apply_medical_clinic_ti_rulebook(first, scope, "Los Angeles", "CA")

    for key in ("pro_tips", "what_to_bring", "watch_out"):
        values = second.get(key, [])
        assert len(values) == len({str(value).strip().lower() for value in values}), key

    permit_types = [str(item.get("permit_type") or "").strip().lower() for item in second.get("companion_permits", []) if isinstance(item, dict)]
    assert len(permit_types) == len(set(permit_types))


def test_phase4b_healthcare_rules_do_not_leak_into_office_or_other_unpopulated_states():
    office = engine.apply_state_schema_context(
        _base_result("commercial_office_ti"),
        "Los Angeles CA office tenant improvement with conference rooms and data cabling",
        "Los Angeles",
        "CA",
    )
    assert office["state_schema_context"]["vertical"] == "office_ti"
    assert office["state_schema_context"]["triggered_rules"] == []

    fl = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "Miami FL dental clinic TI with x-ray and nitrous",
        "Miami",
        "FL",
    )
    assert fl["state_schema_context"]["state"] == "FL"
    assert fl["state_schema_context"]["population_status"] == "not_populated"
    assert fl["state_schema_context"]["triggered_rules"] == []


def test_phase4b_sources_stay_out_of_code_citation_until_renderers_are_ready():
    result = _base_result()
    before = result["code_citation"].copy()
    result = engine.apply_state_schema_context(
        result,
        "Los Angeles CA dental clinic tenant improvement with x-ray and nitrous",
        "Los Angeles",
        "CA",
    )

    assert result["code_citation"] == before
    citation_blob = str(result.get("code_citation", ""))
    assert "cdph.ca.gov" not in citation_blob
    assert "hcai.ca.gov" not in citation_blob
    assert "dgs.ca.gov" not in citation_blob
