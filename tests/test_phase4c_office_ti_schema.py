#!/usr/bin/env python3
"""Phase 4C Office TI state-overlay tests for CA/TX/FL/MA."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine
from api.state_schema import compact_state_schema_context, get_state_rule_schema, validate_state_rule_schema


STATES = ("CA", "TX", "FL", "MA")


def _base_result():
    return {
        "_primary_scope": "commercial_office_ti",
        "code_citation": {"section": "IBC 105.1", "text": "Permit required"},
        "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Office Interior Alteration"}],
        "companion_permits": [],
        "inspections": [],
        "what_to_bring": [],
        "common_mistakes": [],
        "pro_tips": [],
        "watch_out": [],
    }


def _blob(value) -> str:
    return str(value).lower()


def _ids(ctx):
    return {rule["id"] for rule in ctx["triggered_rules"]}


def test_office_ti_overlay_is_populated_for_four_core_states_with_sources():
    expected_source_tokens = {
        "CA": ["dgs.ca.gov/bsc/codes", "energycodeace.com/content/get-forms"],
        "TX": ["statutes.capitol.texas.gov", "tdlr.texas.gov/ab/abfaq"],
        "FL": ["leg.state.fl.us/Statutes", "floridabuilding.org"],
        "MA": ["mass.gov/aab-rules-and-regulations", "mass.gov/regulations/780-CMR-tenth-edition-chapter-13-energy-efficiency-amendments"],
    }

    for state in STATES:
        schema = get_state_rule_schema(state)
        assert "office_ti" in schema["populated_for_verticals"]
        assert validate_state_rule_schema(schema) == []
        source_blob = str(schema)
        for token in expected_source_tokens[state]:
            assert token in source_blob, (state, token)

        ctx = compact_state_schema_context(
            state,
            "office_ti",
            f"{state} office tenant improvement with partitions, conference rooms, lighting, HVAC, low-voltage cabling, fire alarm, exit signs, and ADA restroom work",
        )
        assert ctx["active_vertical"] == "office_ti"
        assert ctx["active_vertical_populated"] is True
        assert ctx["population_status"] == "partially_populated"
        assert ctx["coverage_level"] == f"phase4c_{state.lower()}_office_ti"
        assert ctx["populated_phase"] == "phase4c_office_ti"
        assert ctx["requires_population_before_state_specific_claims"] is False
        assert len(ctx["triggered_rules"]) >= 2
        assert all(rule["id"].startswith(f"{state.lower()}_office_") for rule in ctx["triggered_rules"])


def test_office_ti_secondary_sources_surface_only_for_active_office_vertical():
    ctx = compact_state_schema_context(
        "MA",
        "office_ti",
        "Boston MA office TI with lighting, HVAC, accessible route, restroom, and exit sign scope",
    )
    source_urls = {
        source["url"]
        for slot in ctx["overlay_slots"]
        for source in slot["verified_sources"]
    }
    assert "https://www.mass.gov/lists/521-cmr-2006-edition" in source_urls
    assert "https://www.mass.gov/doc/10th-edition-chapter-13-energy-efficiency/download" in source_urls

    restaurant = compact_state_schema_context("MA", "restaurant_ti", "Boston restaurant TI with hood")
    restaurant_urls = {
        source["url"]
        for slot in restaurant["overlay_slots"]
        for source in slot["verified_sources"]
    }
    assert "https://www.mass.gov/lists/521-cmr-2006-edition" not in restaurant_urls
    assert "https://www.mass.gov/doc/10th-edition-chapter-13-energy-efficiency/download" not in restaurant_urls
    assert "https://www.mass.gov/handbook/tenth-edition-of-the-ma-state-building-code-780" in restaurant_urls


def test_state_overlay_tip_includes_rule_confidence_label():
    result = engine.apply_state_schema_context(
        _base_result(),
        "Orlando FL office tenant improvement with lighting, HVAC diffuser relocation, exit signs, and accessible restroom upgrades",
        "Orlando",
        "FL",
    )

    tips = "\n".join(result.get("pro_tips") or []).lower()
    assert "florida state overlay (medium confidence)" in tips
    assert "florida state overlay (low confidence)" in tips


def test_office_ti_rules_surface_customer_guidance_without_fake_code_citations():
    result = _base_result()
    before = dict(result["code_citation"])
    result = engine.apply_state_schema_context(
        result,
        "Los Angeles CA office tenant improvement with demising partitions, conference rooms, lighting controls, HVAC diffuser relocation, data cabling, fire alarm notification appliances, exit signs, and accessible restroom upgrades",
        "Los Angeles",
        "CA",
    )

    assert result["code_citation"] == before
    ctx = result["state_schema_context"]
    triggered = _ids(ctx)
    assert "ca_office_title24_local_ahj_baseline" in triggered
    assert "ca_office_title24_part6_nonresidential_energy_forms" in triggered
    assert "ca_office_accessibility_path_of_travel" in triggered

    text = _blob(result)
    assert "california state overlay" in text
    assert "office ti" in text or "office" in text
    assert "title 24" in text
    assert "energy" in text
    assert "medical gas" not in text
    assert "x-ray" not in text
    assert "grease" not in text
    assert "type i hood" not in text
    assert "adu" not in text
    assert "single-family" not in text
    assert "sfr" not in text

    citation_blob = _blob(result.get("code_citation"))
    assert "dgs.ca.gov" not in citation_blob
    assert "energycodeace.com" not in citation_blob


def test_office_finish_only_scope_avoids_energy_overwarning_but_keeps_baseline_rules():
    for state in STATES:
        ctx = compact_state_schema_context(
            state,
            "office_ti",
            f"{state} office refresh with paint, carpet, movable furniture, and signage only; no lighting, no HVAC, no mechanical, no electrical, no water heating",
        )
        ids = _ids(ctx)
        expected_baseline = {
            "CA": "ca_office_title24_local_ahj_baseline",
            "TX": "tx_office_ibc_local_ahj_ti_baseline",
            "FL": "fl_office_fbc_local_ahj_baseline",
            "MA": "ma_office_780cmr_local_ahj_baseline",
        }[state]
        assert expected_baseline in ids, (state, ids)
        assert not any("energy" in rule_id for rule_id in ids), (state, ids)


def test_office_energy_triggers_use_word_boundaries_not_substrings():
    ctx = compact_state_schema_context(
        "CA",
        "office_ti",
        "California office TI with delighting signage mockups and slighting punch-list language only; no light fixture, no HVAC, no mechanical work",
    )
    ids = _ids(ctx)
    assert "ca_office_title24_part6_nonresidential_energy_forms" not in ids


def test_office_ti_does_not_receive_medical_restaurant_residential_or_wrong_state_rules():
    cases = [
        ("TX", "Austin TX office TI with kitchenette/break room sink, data cabling, conference rooms, no restaurant, no hood, no grease interceptor, no food service"),
        ("FL", "Miami FL office tenant buildout with wellness room and first-aid cabinet, no clinic, no x-ray, no medical gas, no treatment rooms"),
        ("MA", "Boston MA office suite alteration with reception desk and conference rooms, no surgery, no dental, no medical clinic"),
        ("CA", "San Diego CA commercial office TI with partitions and low-voltage cabling, no ADU, no SFR, no solar, no residential work"),
    ]
    banned = (
        "x-ray", "radiation", "medical gas", "nitrous", "clinic licensure", "ambulatory surgical",
        "grease interceptor", "type i hood", "food establishment", "adu", "sfr", "single-family",
        "phoenix", "maricopa", "los angeles county solar",
    )

    for state, scope in cases:
        result = engine.apply_state_schema_context(_base_result(), scope, scope.split()[0], state)
        text = _blob(result)
        assert result["state_schema_context"]["active_vertical"] == "office_ti"
        assert result["state_schema_context"]["active_vertical_populated"] is True
        assert all(rule["id"].startswith(f"{state.lower()}_office_") for rule in result["state_schema_context"]["triggered_rules"])
        forbidden_rule_tokens = ("xray", "x_ray", "clinic", "medical_gas", "nitrous", "surgical", "asc", "dental", "restaurant", "hood", "grease")
        assert not any(
            token in rule["id"]
            for rule in result["state_schema_context"]["triggered_rules"]
            for token in forbidden_rule_tokens
        )
        for token in banned:
            assert token not in text, (state, token, text)


def test_tx_tdlr_threshold_stays_warning_not_auto_companion_permit():
    result = engine.apply_state_schema_context(
        _base_result(),
        "Austin TX small office refresh with paint, carpet, movable furniture, and signage only",
        "Austin",
        "TX",
    )

    companion_blob = _blob(result.get("companion_permits"))
    warning_blob = _blob(result.get("watch_out"))
    assert "tdlr" not in companion_blob
    assert "tabs" not in companion_blob
    assert "tdlr" in warning_blob or "texas accessibility standards" in warning_blob
    assert "cost-threshold" in warning_blob


def test_restaurant_ti_now_has_active_vertical_evidence_without_office_or_medical_leakage():
    ctx = compact_state_schema_context("TX", "restaurant_ti", "Dallas TX restaurant TI with hood and grease interceptor")
    assert ctx["active_vertical_populated"] is True
    assert ctx["population_status"] == "partially_populated"
    assert ctx["coverage_level"] == "phase4d_tx_restaurant_ti"
    assert ctx["populated_phase"] == "phase4d_restaurant_ti"
    ids = _ids(ctx)
    assert "tx_restaurant_ibc_local_ahj_baseline" in ids
    assert "tx_restaurant_dshs_local_health_department_split" in ids
    assert all(rule_id.startswith("tx_restaurant_") for rule_id in ids)
    assert "medical" not in ctx["contractor_warning"].lower()
    assert not any("office_" in rule_id for rule_id in ids)
    assert "tdlr.texas.gov/ab/abfaq" not in _blob(ctx["overlay_slots"])
