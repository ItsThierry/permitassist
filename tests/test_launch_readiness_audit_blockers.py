#!/usr/bin/env python3
"""Regression tests for the 2026-05-03 launch-readiness audit blockers."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

# api.server initializes an OpenAI chat client at import time; use a dummy key
# so these deterministic unit tests never need real credentials or paid calls.
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")

import api.research_engine as engine
import api.server as server
from api.hidden_trigger_detector import detect_hidden_triggers


def _citation_conf(result, field):
    citations = result.get("claim_citations") or server.build_claim_citations(result)
    for citation in citations:
        if citation.get("field") == field:
            return citation.get("confidence")
    return None


def test_apply_path_support_level_follows_apply_url_citation_confidence_for_fee_page():
    result = {
        "permit_verdict": "YES",
        "confidence": "medium",
        "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Restaurant Interior Alteration"}],
        "apply_url": "https://cohweb.houstontx.gov/fin_feeschedule/default.aspx",
        "sources": [{"url": "https://cohweb.houstontx.gov/fin_feeschedule/default.aspx", "title": "City-Wide Fee Schedule"}],
        "field_evidence": {
            "apply_url": [{
                "source_url": "https://cohweb.houstontx.gov/fin_feeschedule/default.aspx",
                "source_title": "City-Wide Fee Schedule",
                "quoted_snippet": "City-Wide Fee Schedule",
                "source_type": "official_ahj",
                "supports_field": True,
                "confidence_signal": "high",
            }]
        },
    }

    assert _citation_conf(result, "apply_url") == "needs_verification"
    apply_path = server.build_apply_path(result, "Commercial restaurant tenant improvement", "Houston", "TX")

    assert apply_path["support_level"] != "verified path"
    assert result["field_confidence"]["apply_url"] == "needs_verification"
    assert result.get("needs_review") is True


def test_disallowed_apply_urls_are_never_high_confidence_application_paths():
    bad_urls = [
        "https://cohweb.houstontx.gov/fin_feeschedule/default.aspx",
        "https://av-info.faa.gov/data/AID/tab/e1975_79.txt",
        "https://www.boston.gov/departments/inspectional-services/apply-permit-online",
        "https://aca.sandiego.gov",
        "https://aca-prod.accela.com/PHOENIX",
    ]
    for url in bad_urls:
        strict = server.enforce_strict_field_confidence("apply_url", url, [{
            "source_url": url,
            "source_title": "Apply online",
            "quoted_snippet": "Apply online through the permit portal and submit the application.",
            "source_type": "official_ahj",
            "supports_field": True,
            "confidence_signal": "high",
        }])
        assert strict["confidence"] == "needs_verification", url
        assert strict["needs_review"] is True, url


def test_home_office_no_commercial_scope_has_residential_apply_path_and_no_commercial_warnings():
    job = "Private residential home office in a spare bedroom, no customers, no employees, no signage, no structural work, no commercial use."
    result = {
        "permit_verdict": "YES",
        "confidence": "medium",
        "permits_required": [{"permit_type": "Building Permit — Commercial Tenant Improvement"}],
        "companion_permits": [{"permit_type": "Electrical Permit — Commercial Tenant Improvement"}],
        "quality_warnings": ["Commercial scope may require companion reviews/permits not fully proven here: electrical, mechanical, plumbing."],
    }

    engine.enforce_commercial_primary_permit_guardrail(result, job, "Plano", "TX")
    gated = server.apply_permitiq_quality_gate(result, job, "Plano", "TX")
    apply_path = server.build_apply_path(gated, job, "Plano", "TX")
    checklist = engine.generate_permit_checklist(job, "Plano", "TX", gated)

    text = " ".join([str(apply_path), str(gated.get("quality_warnings") or []), " ".join(checklist)]).lower()
    assert apply_path["permit_category"] == "Residential / Trade Permit"
    assert "commercial tenant improvement" not in text
    assert "commercial scope may require companion" not in text
    assert "solar" not in text
    assert "pool" not in text


def test_chicago_gym_commercial_alteration_suppresses_residential_hvac_hidden_trigger():
    job = "Commercial fitness gym tenant improvement in Chicago with partitions, lighting, HVAC diffuser relocation, accessibility upgrades, and no residential work."
    result = {"_primary_scope": engine.detect_primary_scope(job), "hidden_triggers": []}
    triggers = detect_hidden_triggers(job, "Chicago", "IL", result["_primary_scope"], result)
    trigger_ids = {t.get("id") for t in triggers if isinstance(t, dict)}

    assert result["_primary_scope"] == "commercial"
    assert not any(str(tid).startswith("residential_") for tid in trigger_ids)


def test_san_diego_reroof_no_solar_no_structural_does_not_leak_solar_or_racking():
    job = "Residential asphalt shingle reroof, same material and framing, no structural or diaphragm changes, no solar work."
    result = {
        "permit_verdict": "YES",
        "confidence": "medium",
        "permits_required": [{
            "permit_type": "Roofing Permit — Reroof",
            "portal_selection": "Solar PV / roof-mounted racking",
            "notes": "Structural engineering letter for roof-mounted solar racking and utility interconnection required.",
        }],
        "checklist": ["Structural engineering letter confirming roof load capacity", "Utility interconnection application"],
        "companion_permits": [{"permit_type": "Solar PV / roof-mounted racking review"}],
        "hidden_triggers": [{"id": "solar_roof_racking_structural_review", "title": "Solar roof racking structural review"}],
    }

    engine.apply_scope_aware_permit_classification(result, job)
    engine.purge_solar_ess_residue(result, job)
    engine.validate_and_sanitize_permit_result(result, job, "San Diego", "CA")
    checklist = engine.generate_permit_checklist(job, "San Diego", "CA", result)

    all_text = " ".join([str(result), " ".join(checklist)]).lower()
    assert "solar" not in all_text
    assert "racking" not in all_text
    assert "utility interconnection" not in all_text
    assert "structural engineering letter confirming roof load capacity" not in all_text
