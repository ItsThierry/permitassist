#!/usr/bin/env python3
"""Phase 4D restaurant TI state-overlay tests for CA/TX/FL/MA."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine
from api.state_schema import compact_state_schema_context, get_state_rule_schema, validate_state_rule_schema


STATES = ("CA", "TX", "FL", "MA")


def _restaurant_result():
    return {
        "_primary_scope": "commercial_restaurant",
        "code_citation": {"section": "IBC 105.1", "text": "Permit required"},
        "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Restaurant Interior Alteration"}],
        "companion_permits": [],
        "inspections": [],
        "what_to_bring": [],
        "common_mistakes": [],
        "pro_tips": [],
        "watch_out": [],
    }


def _ids(ctx):
    return {rule["id"] for rule in ctx["triggered_rules"]}


def _blob(value) -> str:
    return str(value).lower()


def test_restaurant_ti_overlay_is_populated_for_four_core_states_with_sources():
    expected_source_tokens = {
        "CA": ["dgs.ca.gov/bsc/codes", "leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?sectionNum=114380"],
        "TX": ["statutes.capitol.texas.gov", "dshs.texas.gov/retail-food-establishments"],
        "FL": ["leg.state.fl.us/Statutes", "myfloridalicense.com/hotels-restaurants/licensing/plan-review"],
        "MA": ["mass.gov/handbook/tenth-edition-of-the-ma-state-building-code-780", "mass.gov/doc/2013-food-code"],
    }

    for state in STATES:
        schema = get_state_rule_schema(state)
        assert "restaurant_ti" in schema["populated_for_verticals"]
        assert validate_state_rule_schema(schema) == []
        source_blob = str(schema)
        for token in expected_source_tokens[state]:
            assert token in source_blob, (state, token)

        ctx = compact_state_schema_context(
            state,
            "restaurant_ti",
            f"{state} restaurant tenant improvement with Type I hood, grease interceptor, kitchen equipment, dining, restroom, and fire suppression scope",
        )
        assert ctx["active_vertical"] == "restaurant_ti"
        assert ctx["active_vertical_populated"] is True
        assert ctx["population_status"] == "partially_populated"
        assert ctx["coverage_level"] == f"phase4d_{state.lower()}_restaurant_ti"
        assert ctx["populated_phase"] == "phase4d_restaurant_ti"
        assert ctx["requires_population_before_state_specific_claims"] is False
        assert len(ctx["triggered_rules"]) >= 2
        assert all(rule["id"].startswith(f"{state.lower()}_restaurant_") for rule in ctx["triggered_rules"])


def test_restaurant_finish_lite_scope_keeps_baseline_but_avoids_health_overwarning():
    expected_baseline = {
        "CA": "ca_restaurant_title24_local_ahj_baseline",
        "TX": "tx_restaurant_ibc_local_ahj_baseline",
        "FL": "fl_restaurant_fbc_local_ahj_baseline",
        "MA": "ma_restaurant_780cmr_local_ahj_baseline",
    }
    for state in STATES:
        ctx = compact_state_schema_context(
            state,
            "restaurant_ti",
            f"{state} restaurant dining room refresh with paint, loose furniture, and signage only; no hood, no grease interceptor, no kitchen equipment, no food prep, no plumbing",
        )
        ids = _ids(ctx)
        assert expected_baseline[state] in ids, (state, ids)
        assert not any(rule_id.endswith(("health_department_split", "plan_review_remodel", "plan_review_and_grease_waste")) for rule_id in ids), (state, ids)


def test_restaurant_rules_surface_customer_guidance_without_office_medical_or_residential_leakage():
    result = engine.apply_state_schema_context(
        _restaurant_result(),
        "Los Angeles CA restaurant tenant improvement with Type I hood, grease interceptor, kitchen equipment, dining, restroom, and fire suppression scope; no medical clinic, no office TI, no ADU, no solar",
        "Los Angeles",
        "CA",
    )

    ctx = result["state_schema_context"]
    ids = _ids(ctx)
    assert "ca_restaurant_title24_local_ahj_baseline" in ids
    assert "ca_restaurant_calcode_food_facility_plan_review" in ids
    assert result["code_citation"] == {"section": "IBC 105.1", "text": "Permit required"}

    text = _blob(result)
    assert "california state overlay" in text
    assert "restaurant ti" in text or "restaurant" in text
    assert "title 24" in text
    assert "environmental health" in text or "food-facility" in text
    assert "medical gas" not in text
    assert "x-ray" not in text
    assert "office state overlay" not in text
    assert "adu" not in text
    assert "single-family" not in text
    assert "sfr" not in text

    citation_blob = _blob(result.get("code_citation"))
    assert "dgs.ca.gov" not in citation_blob
    assert "california-retail-food-code" not in citation_blob


def test_restaurant_active_vertical_does_not_surface_office_secondary_sources():
    ctx = compact_state_schema_context(
        "MA",
        "restaurant_ti",
        "Boston MA restaurant TI with Type I hood, grease trap, dining, restroom, and fire suppression scope",
    )
    source_urls = {
        source["url"]
        for slot in ctx["overlay_slots"]
        for source in slot["verified_sources"]
    }
    assert "https://www.mass.gov/lists/521-cmr-2006-edition" not in source_urls
    assert "https://www.mass.gov/doc/10th-edition-chapter-13-energy-efficiency/download" not in source_urls
    assert "https://www.mass.gov/doc/2013-food-code-merged-with-105-cmr-590-0/download" in source_urls


def test_non_core_states_have_no_restaurant_state_overlay_context():
    for state in ("NY", "WA", "IL", "GA"):
        assert compact_state_schema_context(
            state,
            "restaurant_ti",
            f"{state} restaurant TI with hood, grease interceptor, and food prep scope",
        ) is None


def test_office_active_vertical_does_not_trigger_restaurant_rules_or_sources():
    ctx = compact_state_schema_context(
        "CA",
        "office_ti",
        "Los Angeles CA office TI with open office layout, lighting, HVAC diffusers, conference rooms, and restroom accessibility updates",
    )
    ids = _ids(ctx)
    assert ids
    assert all("restaurant" not in rule_id for rule_id in ids), ids
    text = _blob(ctx)
    assert "food facility" not in text
    assert "food-facility" not in text
    assert "environmental health" not in text
    assert "leginfo.legislature.ca.gov/faces/codes_displaysection.xhtml?sectionnum=114380" not in text


def test_medical_active_vertical_does_not_trigger_restaurant_rules_or_sources():
    ctx = compact_state_schema_context(
        "TX",
        "medical_clinic_ti",
        "Austin TX outpatient medical clinic TI with exam rooms, hand sinks, sterilization room, x-ray, and medical gas coordination",
    )
    ids = _ids(ctx)
    assert ids
    assert all("restaurant" not in rule_id for rule_id in ids), ids
    triggered_text = _blob(ctx["triggered_rules"])
    source_urls = _blob([
        source["url"]
        for slot in ctx["overlay_slots"]
        for source in slot["verified_sources"]
    ])
    assert "retail food" not in triggered_text
    assert "restaurant" not in triggered_text
    assert "dshs.texas.gov/retail-food-establishments" not in source_urls


def test_restaurant_finish_only_scope_does_not_trigger_companion_food_life_safety_permits():
    finish_only_prompts = [
        "{state} restaurant dining room finish refresh: paint, decorative wall panels, loose tables and chairs only; no kitchen, no hood, no grease, no food prep, no dishwashing, no plumbing, no fire suppression",
        "{state} restaurant kitchen flooring replacement only with no kitchen equipment, no hood, no grease interceptor, no food prep, no plumbing, and no fire suppression",
    ]
    for state in STATES:
        for prompt in finish_only_prompts:
            ctx = compact_state_schema_context(
                state,
                "restaurant_ti",
                prompt.format(state=state),
            )
            companion_permits = [
                permit
                for rule in ctx["triggered_rules"]
                for permit in rule.get("companion_permits") or []
            ]
            assert companion_permits == [], (state, companion_permits, _ids(ctx))
            assert len(ctx["triggered_rules"]) == 1, (state, _ids(ctx))
