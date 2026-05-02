#!/usr/bin/env python3
"""Phase 3 state schema design tests.

Phase 3 is architecture, not full state-rule population. These tests make sure
we have a stable, citation-ready schema for state overlays without pretending
that MA/TX/CA/FL healthcare rules are already fully researched.
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


def test_phase3_target_state_schemas_exist_and_validate():
    assert PHASE3_TARGET_STATES == ("CA", "TX", "FL", "MA")
    for state in PHASE3_TARGET_STATES:
        schema = get_state_rule_schema(state)
        errors = validate_state_rule_schema(schema)
        assert errors == []
        assert schema["state"] == state
        assert schema["phase"] == 3
        assert schema["coverage_level"] == "schema_only"
        assert schema["population_status"] == "not_populated"
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
        assert slot["status"] == "needs_population"
        assert slot["citation_hooks"]
        assert all(hook["citation_status"] == "needs_population" for hook in slot["citation_hooks"])
        assert all(hook["source_url"] == "" for hook in slot["citation_hooks"])


def test_phase3_unknown_state_returns_none_instead_of_generic_fake_schema():
    assert get_state_rule_schema("ZZ") is None
    result = engine.apply_state_schema_context(_base_result(), "medical clinic TI", "Atlantis", "ZZ")
    assert "state_schema_context" not in result


def test_phase3_state_schema_context_is_attached_to_medical_clinic_results_without_claiming_population():
    result = engine.apply_medical_clinic_ti_rulebook(
        _base_result(),
        "medical clinic tenant improvement with exam rooms and x-ray, no surgery or overnight stay",
        "Cambridge",
        "MA",
    )

    context = result["state_schema_context"]
    assert context["state"] == "MA"
    assert context["phase"] == 3
    assert context["coverage_level"] == "schema_only"
    assert context["population_status"] == "not_populated"
    assert context["requires_population_before_state_specific_claims"] is True
    assert "verify with ahj" in context["contractor_warning"].lower()
    assert context["overlay_slots"]

    citation_blob = str(result.get("code_citation", ""))
    assert "Massachusetts healthcare overlay" not in citation_blob
    assert "needs population" not in citation_blob.lower()
    assert "not a verified rule" not in citation_blob.lower()


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
        assert context["coverage_level"] == "schema_only"
        assert context["population_status"] == "not_populated"
        assert "http" not in str(context).lower()
        assert "§" not in str(context)
        assert "not populated" in context["contractor_warning"].lower()


def test_phase3_state_schema_context_is_idempotent_and_preserves_real_citations():
    result = _base_result()
    original_citation = result["code_citation"]
    first = engine.apply_state_schema_context(result, "medical clinic tenant improvement", "Austin", "TX")
    second = engine.apply_state_schema_context(first, "medical clinic tenant improvement", "Austin", "TX")

    assert second["code_citation"] == original_citation
    assert second["state_schema_context"]["state"] == "TX"
    assert str(second).count("Texas state overlay schema is ready") == 1


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
    schema = get_state_rule_schema("CA")
    schema["population_status"] = "populated"
    schema["coverage_level"] = "full"
    schema["citation_policy"] = {"no_fake_citations": False}
    first_slot = next(iter(schema["healthcare_overlays"].values()))
    first_slot["status"] = "populated"
    first_slot["citation_hooks"][0]["source_url"] = "https://example.com/not-verified"
    first_slot["citation_hooks"][0]["citation_status"] = "verified"

    errors = validate_state_rule_schema(schema)

    assert any("population_status" in error for error in errors)
    assert any("coverage_level" in error for error in errors)
    assert any("no_fake_citations" in error for error in errors)
    assert any("status must be needs_population" in error for error in errors)
    assert any("source_url must stay blank" in error for error in errors)
    assert any("citation_status must be needs_population" in error for error in errors)


def test_phase3_get_state_rule_schema_returns_deep_copy():
    first = get_state_rule_schema("FL")
    first["healthcare_overlays"]["medical_gas"]["status"] = "mutated"
    first["healthcare_overlays"]["medical_gas"]["citation_hooks"][0]["source_url"] = "https://example.com/mutation"

    second = get_state_rule_schema("FL")

    assert second["healthcare_overlays"]["medical_gas"]["status"] == "needs_population"
    assert second["healthcare_overlays"]["medical_gas"]["citation_hooks"][0]["source_url"] == ""


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
    assert str(result).count("Texas state overlay schema is ready") == 1


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
