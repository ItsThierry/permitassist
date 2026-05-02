#!/usr/bin/env python3
"""Phase 3 state schema design tests.

Phase 3 created the architecture. Phase 4A-D now populates the first
TX/CA/FL/MA medical-clinic slices while preserving schema safety and no fake
renderer citations.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine
from api.state_schema import (
    PHASE3_TARGET_STATES,
    compact_state_schema_context,
    get_state_rule_schema,
    validate_state_rule_schema,
)


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


def _synthetic_schema_only():
    schema = get_state_rule_schema("MA")
    schema["phase"] = 3
    schema["coverage_level"] = "schema_only"
    schema["population_status"] = "not_populated"
    schema["requires_population_before_state_specific_claims"] = True
    schema.pop("populated_verticals", None)
    for slot in schema["healthcare_overlays"].values():
        slot["status"] = "needs_population"
        slot["rule_summary"] = ""
        slot["contractor_guidance"] = []
        slot["risk_flags"] = []
        slot["populated_rules"] = []
        for hook in slot["citation_hooks"]:
            hook["source_url"] = ""
            hook["source_title"] = ""
            hook["citation_status"] = "needs_population"
            hook["verified_on"] = ""
    return schema


def test_phase3_target_state_schemas_exist_and_validate():
    assert PHASE3_TARGET_STATES == ("CA", "TX", "FL", "MA")
    for state in PHASE3_TARGET_STATES:
        schema = get_state_rule_schema(state)
        errors = validate_state_rule_schema(schema)
        assert errors == []
        assert schema["state"] == state
        if state == "TX":
            assert schema["phase"] == 4
            assert schema["coverage_level"] == "phase4a_tx_medical_clinic_ti"
            assert schema["population_status"] == "partially_populated"
        elif state == "CA":
            assert schema["phase"] == 4
            assert schema["coverage_level"] == "phase4b_ca_medical_clinic_ti"
            assert schema["population_status"] == "partially_populated"
        elif state == "FL":
            assert schema["phase"] == 4
            assert schema["coverage_level"] == "phase4c_fl_medical_clinic_ti"
            assert schema["population_status"] == "partially_populated"
        else:
            assert schema["phase"] == 4
            assert schema["coverage_level"] == "phase4d_ma_medical_clinic_ti"
            assert schema["population_status"] == "partially_populated"
        assert schema["citation_policy"]["no_fake_citations"] is True


def test_phase3_schema_has_required_healthcare_overlay_slots_with_citation_hooks():
    schema = get_state_rule_schema("TX")
    overlays = schema["healthcare_overlays"]

    required = {
        "occupancy_classification",
        "ambulatory_care_thresholds",
        "healthcare_licensing",
        "medical_gas",
        "radiology_xray",
        "infection_control_hvac",
        "accessibility",
    }
    assert required.issubset(overlays.keys())

    for key in required:
        slot = overlays[key]
        assert slot["status"] in {"needs_population", "populated"}
        assert slot["citation_hooks"]
        if slot["status"] == "needs_population":
            assert all(hook["citation_status"] == "needs_population" for hook in slot["citation_hooks"])
            assert all(hook["source_url"] == "" for hook in slot["citation_hooks"])
        else:
            assert all(hook["citation_status"] == "verified" for hook in slot["citation_hooks"])
            assert all(hook["source_url"].startswith("https://") for hook in slot["citation_hooks"])


def test_phase3_unknown_state_returns_none_instead_of_generic_fake_schema():
    assert get_state_rule_schema("ZZ") is None
    result = engine.apply_state_schema_context(_base_result(), "medical clinic TI", "Atlantis", "ZZ")
    assert "state_schema_context" not in result


def test_phase3_state_schema_context_is_attached_to_medical_clinic_results_without_fake_citations():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "medical clinic tenant improvement with exam rooms and x-ray, no surgery or overnight stay",
        "Cambridge",
        "MA",
    )

    context = result["state_schema_context"]
    assert context["state"] == "MA"
    assert context["phase"] == 4
    assert context["coverage_level"] == "phase4d_ma_medical_clinic_ti"
    assert context["population_status"] == "partially_populated"
    assert context["requires_population_before_state_specific_claims"] is False
    assert context["overlay_slots"]
    assert context["triggered_rules"]

    citation_blob = str(result.get("code_citation", ""))
    assert "Massachusetts healthcare overlay" not in citation_blob
    assert "needs population" not in citation_blob.lower()
    assert "mass.gov" not in citation_blob.lower()


def test_phase3_state_schema_context_attaches_to_all_target_states_without_fake_urls_or_rules():
    for state in PHASE3_TARGET_STATES:
        result = engine.apply_state_schema_context(
            _base_result(),
            "medical clinic tenant improvement with exam rooms",
            "Test City",
            state,
        )
        context = result["state_schema_context"]
        assert context["state"] == state
        if state == "TX":
            assert context["coverage_level"] == "phase4a_tx_medical_clinic_ti"
            assert context["population_status"] == "partially_populated"
            assert context["triggered_rules"]
        elif state == "CA":
            assert context["coverage_level"] == "phase4b_ca_medical_clinic_ti"
            assert context["population_status"] == "partially_populated"
            assert context["triggered_rules"]
        elif state == "FL":
            assert context["coverage_level"] == "phase4c_fl_medical_clinic_ti"
            assert context["population_status"] == "partially_populated"
            assert context["triggered_rules"]
        else:
            assert context["coverage_level"] == "phase4d_ma_medical_clinic_ti"
            assert context["population_status"] == "partially_populated"
            assert context["triggered_rules"]
        assert "§" not in str(context)


def test_phase3_state_schema_context_is_idempotent_and_preserves_real_citations():
    result = _base_result()
    original_citation = result["code_citation"]
    first = engine.apply_state_schema_context(result, "medical clinic tenant improvement", "Austin", "TX")
    second = engine.apply_state_schema_context(first, "medical clinic tenant improvement", "Austin", "TX")

    assert second["code_citation"] == original_citation
    assert second["state_schema_context"]["state"] == "TX"
    assert str(second).count("Texas medical/dental clinic TI overlay is populated") == 1


def test_phase3_general_overlay_context_is_available_for_office_and_restaurant_ti():
    office = engine.apply_state_schema_context(
        _base_result("commercial_office_ti"),
        "office tenant improvement with conference rooms and data cabling",
        "Dallas",
        "TX",
    )
    restaurant = engine.apply_state_schema_context(
        _base_result("commercial_restaurant_ti"),
        "restaurant tenant improvement with hood and grease interceptor",
        "Miami",
        "FL",
    )
    production_restaurant = engine.apply_state_schema_context(
        _base_result("commercial_restaurant"),
        "restaurant tenant improvement with hood and grease interceptor",
        "Miami",
        "FL",
    )

    assert office["state_schema_context"]["vertical"] == "office_ti"
    assert restaurant["state_schema_context"]["vertical"] == "restaurant_ti"
    assert production_restaurant["state_schema_context"]["vertical"] == "restaurant_ti"
    assert {slot["key"] for slot in office["state_schema_context"]["overlay_slots"]} >= {
        "adopted_code_editions",
        "energy_code",
        "local_amendments",
    }


def test_phase3_schema_context_does_not_attach_to_unsupported_scope():
    result = engine.apply_state_schema_context(
        _base_result("residential_roof"),
        "replace asphalt shingle roof on single family house",
        "Dallas",
        "TX",
    )
    assert "state_schema_context" not in result


def test_phase3_validator_rejects_populated_or_fake_citation_shapes():
    schema = _synthetic_schema_only()
    schema["population_status"] = "populated"
    schema["coverage_level"] = "full"
    schema["citation_policy"] = {"no_fake_citations": False}
    first_slot = next(iter(schema["healthcare_overlays"].values()))
    first_slot["status"] = "populated"
    first_slot["citation_hooks"][0]["source_url"] = "https://example.com/not-verified"
    first_slot["citation_hooks"][0]["citation_status"] = "verified"

    errors = validate_state_rule_schema(schema)

    assert any("populated_rules required" in error for error in errors)
    assert any("coverage_level" in error for error in errors)
    assert any("no_fake_citations" in error for error in errors)
    assert any("citation_status must be verified" in error for error in errors)
    assert any("needs source_url and source_title" in error for error in errors)
    assert any("phase must be 4" in error for error in errors)


def test_phase3_get_state_rule_schema_returns_deep_copy():
    first = get_state_rule_schema("MA")
    first["healthcare_overlays"]["medical_gas"]["status"] = "mutated"
    first["healthcare_overlays"]["medical_gas"]["citation_hooks"][0]["source_url"] = "https://example.com/mutation"

    second = get_state_rule_schema("MA")

    assert second["healthcare_overlays"]["medical_gas"]["status"] == "populated"
    assert second["healthcare_overlays"]["medical_gas"]["citation_hooks"][0]["source_url"] != "https://example.com/mutation"
    assert second["healthcare_overlays"]["medical_gas"]["citation_hooks"][0]["source_url"].startswith("https://www.mass.gov/")


def test_phase3_compact_context_rejects_unknown_vertical():
    assert compact_state_schema_context("TX", "unknown_vertical") is None


def test_phase3_medical_rulebook_plus_pipeline_call_is_idempotent_and_keeps_citations_real():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "medical clinic tenant improvement with exam rooms and no surgery",
        "Austin",
        "TX",
    )
    before_citation = result["code_citation"]

    result = engine.apply_state_schema_context(
        result,
        "medical clinic tenant improvement with exam rooms and no surgery",
        "Austin",
        "TX",
    )

    assert result["code_citation"] == before_citation
    assert result["state_schema_context"]["state"] == "TX"
    assert str(result).count("Texas medical/dental clinic TI overlay is populated") == 1


def test_phase3_state_schema_never_changes_code_citation_for_target_states():
    for state in PHASE3_TARGET_STATES:
        result = _base_result()
        before = result["code_citation"].copy()
        result = engine.apply_state_schema_context(
            result,
            "medical clinic tenant improvement with exam rooms",
            "Test City",
            state,
        )
        assert result["code_citation"] == before
        assert result["state_schema_context"]["state"] == state
