#!/usr/bin/env python3
"""Evidence Pack Sprint 3 typed adapter and metadata tests."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine
from api.evidence_packs import (
    CORE_EVIDENCE_FIELDS,
    EvidencePack,
    EvidenceRule,
    FieldEvidenceDefault,
    build_evidence_pack_from_state_schema,
    get_evidence_pack,
    list_evidence_packs,
    validate_evidence_pack,
)
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


def test_typed_adapter_wraps_existing_phase4_medical_packs_without_new_source_tree():
    states = {pack.state: pack for pack in list_evidence_packs() if pack.active_vertical == "medical_clinic_ti"}

    assert set(states) == {"TX", "CA", "FL", "MA"}
    for state, pack in states.items():
        assert pack.source_system == "state_schema_adapter"
        assert pack.readiness == "partial"
        assert pack.active_vertical == "medical_clinic_ti"
        assert "medical_clinic_ti" in pack.verticals
        assert pack.overlay_rules, state
        assert validate_evidence_pack(pack) == []
        assert {default.field for default in pack.field_defaults} == set(CORE_EVIDENCE_FIELDS)
        assert all(default.readiness == "needs_verification" for default in pack.field_defaults)
        assert all(rule.source_url and rule.source_title and rule.source_quote for rule in pack.overlay_rules)


def test_state_schema_context_has_clear_active_vertical_metadata_for_qa():
    medical = compact_state_schema_context(
        "TX",
        "medical_clinic_ti",
        "Dallas TX dental clinic tenant improvement with x-ray and nitrous",
    )
    restaurant = compact_state_schema_context(
        "TX",
        "restaurant_ti",
        "Dallas TX restaurant tenant improvement with hood and grease interceptor",
    )

    assert medical["coverage_level"] == "phase4a_tx_medical_clinic_ti"
    assert medical["populated_phase"] == "phase4a"
    assert medical["populated_for_verticals"] == ["medical_clinic_ti"]
    assert medical["active_vertical"] == "medical_clinic_ti"
    assert medical["active_vertical_populated"] is True
    assert medical["triggered_rules"]

    # Unpopulated active verticals must fail closed instead of borrowing the
    # historical medical/dental coverage label and warning text.
    assert restaurant["coverage_level"] == "needs_verification_tx_restaurant_ti"
    assert restaurant["population_status"] == "needs_verification"
    assert restaurant["requires_population_before_state_specific_claims"] is True
    assert "restaurant ti" in restaurant["contractor_warning"].lower()
    assert "medical" not in restaurant["contractor_warning"].lower()
    assert restaurant["active_vertical"] == "restaurant_ti"
    assert restaurant["active_vertical_populated"] is False
    assert restaurant["triggered_rules"] == []


def test_get_evidence_pack_has_manual_tx_restaurant_example_without_apply_path_claims():
    pack = get_evidence_pack("TX", "restaurant_ti")

    assert pack is not None
    assert pack.source_system == "manual_state_vertical_example"
    assert pack.state == "TX"
    assert pack.active_vertical == "restaurant_ti"
    assert pack.readiness == "partial"
    assert validate_evidence_pack(pack) == []

    rule_ids = {rule.id for rule in pack.overlay_rules}
    assert "tx_restaurant_dshs_retail_food_permit_local_jurisdiction" in rule_ids
    assert "tx_restaurant_tas_dining_surfaces_accessibility" in rule_ids
    source_blob = str(pack.to_dict()).lower()
    assert "dshs.texas.gov" in source_blob
    assert "tdlr.texas.gov" in source_blob
    assert "contact your city or county office" in source_blob

    # State/restaurant pack is not a local AHJ portal registry; it must not mark
    # customer core fields verified or supply a verified apply URL by itself.
    defaults = {default.field: default for default in pack.field_defaults}
    assert defaults["apply_url"].readiness == "needs_verification"
    assert defaults["apply_url"].confidence == "low"
    assert defaults["apply_url"].supports_field is False
    assert all(default.source_type == "state_vertical_overlay" for default in defaults.values())


def test_adapter_rejects_unpopulated_verticals_in_existing_state_schema():
    schema = get_state_rule_schema("TX")

    assert validate_state_rule_schema(schema) == []
    try:
        build_evidence_pack_from_state_schema(schema, active_vertical="office_ti")
    except ValueError as exc:
        assert "office_ti" in str(exc)
    else:
        raise AssertionError("office_ti adapter should reject unpopulated TX schema")


def test_sprint3_metadata_does_not_change_customer_rule_injection_or_code_citation():
    result = _base_result("commercial_office_ti")
    before = result["code_citation"].copy()
    output = engine.apply_state_schema_context(
        result,
        "Dallas TX office tenant improvement with conference rooms and data cabling",
        "Dallas",
        "TX",
    )

    context = output["state_schema_context"]
    assert context["active_vertical"] == "office_ti"
    assert context["active_vertical_populated"] is False
    assert context["triggered_rules"] == []
    assert output["code_citation"] == before
    assert not output["pro_tips"]
    assert not output["watch_out"]


def test_validate_evidence_pack_blocks_false_verified_and_missing_proof():
    bad_pack = EvidencePack(
        state="TX",
        state_name="Texas",
        verticals=("restaurant_ti",),
        active_vertical="restaurant_ti",
        overlay_rules=(
            EvidenceRule(
                id="tx_restaurant_bad_rule",
                title="Bad high-confidence rule without direct quote",
                overlay="health",
                summary="Unsafe synthetic example for validator coverage.",
                source_url="javascript:alert(1)",
                source_title="Example source",
                source_quote="",
                confidence="high",
                applies="tx_restaurant_ti",
            ),
        ),
        field_defaults=(
            FieldEvidenceDefault(
                field="apply_url",
                readiness="verified",
                confidence="high",
                source_type="state_vertical_overlay",
                supports_field=True,
                note="Unsafe synthetic default for validator coverage.",
            ),
        ),
        last_verified="",
        readiness="verified",
        source_system="test",
        coverage_level="test",
        population_status="partially_populated",
    )

    errors = validate_evidence_pack(bad_pack)

    assert any("must not be marked verified" in error for error in errors)
    assert "last_verified is required" in errors
    assert any("field_defaults missing core fields" in error for error in errors)
    assert any("apply_url cannot be verified" in error for error in errors)
    assert any("needs source_url, source_title, and source_quote" in error for error in errors)
    assert any("source_url must use http or https" in error for error in errors)
    assert any("source_url must include a hostname" in error for error in errors)
    assert any("high confidence requires direct source_quote" in error for error in errors)


def test_public_pack_lookup_and_dict_surface_are_safe_for_unknowns_and_audits():
    assert get_evidence_pack("ZZ", "medical_clinic_ti") is None
    assert get_evidence_pack("TX", "office_ti") is None

    pack = get_evidence_pack("TX", "restaurant_ti")
    data = pack.to_dict()

    assert data["state"] == "TX"
    assert data["active_vertical"] == "restaurant_ti"
    assert data["readiness"] == "partial"
    assert data["field_defaults"]
    assert data["overlay_rules"]
    assert data["field_defaults"][0]["confidence"] == "low"


def test_validator_requires_active_vertical_inside_declared_verticals():
    pack = get_evidence_pack("TX", "restaurant_ti")
    bad_pack = EvidencePack(
        state=pack.state,
        state_name=pack.state_name,
        verticals=("office_ti",),
        active_vertical=pack.active_vertical,
        overlay_rules=pack.overlay_rules,
        field_defaults=pack.field_defaults,
        last_verified=pack.last_verified,
        readiness=pack.readiness,
        source_system=pack.source_system,
        coverage_level=pack.coverage_level,
        population_status=pack.population_status,
    )

    assert "active_vertical must be included in verticals" in validate_evidence_pack(bad_pack)


def test_schema_adapter_filters_general_overlay_rules_by_requested_vertical():
    schema = {
        "state": "ZZ",
        "state_name": "Test State",
        "populated_verticals": ["restaurant_ti", "office_ti"],
        "coverage_level": "test_general_overlays",
        "population_status": "partially_populated",
        "general_overlays": {
            "mixed_slot": {
                "citation_hooks": [{"citation_status": "verified", "verified_on": "2026-05-02"}],
                "populated_rules": [
                    {
                        "id": "zz_restaurant_food_rule",
                        "verticals": ["restaurant_ti"],
                        "title": "Restaurant food rule",
                        "overlay": "food_health",
                        "summary": "Restaurant tenant improvement food-health rule.",
                        "source_url": "https://example.invalid/restaurant",
                        "source_title": "Restaurant source",
                        "source_quote": "Restaurant quote.",
                        "confidence": "medium",
                        "applies": "zz_restaurant_ti",
                    },
                    {
                        "id": "zz_office_fixture_rule",
                        "verticals": ["office_ti"],
                        "title": "Office fixture rule",
                        "overlay": "office_planning",
                        "summary": "Office tenant improvement fixture rule.",
                        "source_url": "https://example.invalid/office",
                        "source_title": "Office source",
                        "source_quote": "Office quote.",
                        "confidence": "medium",
                        "applies": "zz_office_ti",
                    },
                ],
            }
        },
    }

    restaurant_pack = build_evidence_pack_from_state_schema(schema, active_vertical="restaurant_ti")
    office_pack = build_evidence_pack_from_state_schema(schema, active_vertical="office_ti")

    assert [rule.id for rule in restaurant_pack.overlay_rules] == ["zz_restaurant_food_rule"]
    assert [rule.id for rule in office_pack.overlay_rules] == ["zz_office_fixture_rule"]

def test_schema_adapter_fails_closed_when_schema_pack_fails_validation():
    schema = {
        "state": "ZZ",
        "state_name": "Test State",
        "populated_verticals": ["restaurant_ti"],
        "coverage_level": "test_general_overlays",
        "population_status": "partially_populated",
        "general_overlays": {
            "bad_slot": {
                "citation_hooks": [],
                "populated_rules": [
                    {
                        "id": "zz_restaurant_bad_url_rule",
                        "verticals": ["restaurant_ti"],
                        "title": "Restaurant bad URL rule",
                        "overlay": "food_health",
                        "summary": "Invalid source URL should fail closed.",
                        "source_url": "javascript:alert(1)",
                        "source_title": "Bad source",
                        "source_quote": "Bad quote.",
                        "confidence": "medium",
                        "applies": "zz_restaurant_ti",
                    },
                ],
            }
        },
    }

    try:
        build_evidence_pack_from_state_schema(schema, active_vertical="restaurant_ti")
    except ValueError as exc:
        assert "invalid evidence pack" in str(exc)
        assert "last_verified is required" in str(exc)
        assert "source_url must use http or https" in str(exc)
    else:
        raise AssertionError("invalid schema evidence pack should fail closed")
