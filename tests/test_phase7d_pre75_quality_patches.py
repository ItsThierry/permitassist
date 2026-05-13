#!/usr/bin/env python3
"""Phase 7D pre-75 quality hold regression tests."""

import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine


PA30_020_JOB = (
    "Single-family residential electrical panel upgrade from 100A to 200A "
    "with utility coordination, no solar, no remodel. QA marker PA30-020; "
    "marker is not permit scope."
)


def _combined_text(value) -> str:
    return str(value).lower()


def test_pa30_020_no_solar_panel_upgrade_does_not_classify_as_solar_scope():
    out = engine.classify_scope_required_permits(PA30_020_JOB)

    assert out is not None
    assert out["scope_classification"] == "residential_panel_upgrade"
    assert any("electrical" in p["permit_type"].lower() for p in out["permits_required"])

    combined = _combined_text(out)
    for forbidden in ("solar pv", "photovoltaic", "battery", "ess", "nec 706", "nfpa 855"):
        assert forbidden not in combined


def test_no_solar_negation_does_not_protect_solar_residue_from_purge():
    result = {
        "_primary_scope": "residential_panel_upgrade",
        "permits_required": [
            {"permit_type": "Electrical Permit — Service Upgrade", "notes": "Required for service/panel work."},
            {"permit_type": "Electrical Permit — Solar PV", "notes": "Solar PV electrical work falls under NEC Article 690."},
        ],
        "permits_required_logic": [
            {"permit_type": "Electrical Permit — Solar PV", "included_because": "Solar PV roof work needs structural/racking review.", "scope_trigger": "solar/pv"},
            {"permit_type": "Electrical Permit — Solar PV + Battery ESS", "included_because": "Battery scope adds ESS review under NEC 706 + NFPA 855.", "scope_trigger": "battery/ESS present"},
        ],
        "pro_tips": ["Coordinate utility disconnect/reconnect.", "Battery ESS labeling may be required."],
    }

    out = engine.purge_solar_ess_residue(result, PA30_020_JOB)
    combined = _combined_text(out)
    for forbidden in ("solar", "pv", "photovoltaic", "battery", "ess", "nec 706", "nfpa 855"):
        assert forbidden not in combined
    assert "utility disconnect" in combined


def test_openai_reasoning_effort_medium_is_passed_when_configured(monkeypatch):
    monkeypatch.setenv("PERMITASSIST_OPENAI_REASONING_EFFORT", "medium")

    assert engine._openai_reasoning_effort_kwargs() == {"reasoning_effort": "medium"}


def test_benchmark_reasoning_header_is_sanitized_for_ab_only(monkeypatch):
    monkeypatch.delenv("PERMITASSIST_OPENAI_REASONING_EFFORT", raising=False)

    assert engine._sanitize_openai_reasoning_effort("medium") == "medium"
    assert engine._sanitize_openai_reasoning_effort("none") is None
    assert engine._sanitize_openai_reasoning_effort("garbage") is None


def test_phase7d_scorer_allows_five_for_complete_launch_core_case():
    scorer_path = Path(__file__).resolve().parents[1] / "scripts" / "phase7d_buyer_readiness_scorer.py"
    spec = importlib.util.spec_from_file_location("phase7d_buyer_readiness_scorer", scorer_path)
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)

    case = {
        "id": "PA30_001_LA_office_ti_positive_supported",
        "group": "commercial_launch_core",
        "scenario_type": "positive_supported",
    }
    observed = {
        "status": 200,
        "coverage_truth": {"mode": "beta"},
        "apply_url": "https://permitla.lacitydbs.org/",
        "permits_required": [{"permit_type": "Building Permit — Commercial Tenant Improvement", "required": True}],
        "snippet": "Office tenant improvement with official PermitLA apply path and clear permits required.",
    }

    score = scorer.score_case(case, observed)

    assert score["include_in_buyer_usefulness_average"] is True
    assert score["usefulness_1_5"] == 5
    assert score["trust_1_5"] == 5
    assert score["clarity_1_5"] == 5
    assert score["overall_case_score"] == 5.0


def test_phase7d_scorer_excludes_binary_guard_from_buyer_average():
    scorer_path = Path(__file__).resolve().parents[1] / "scripts" / "phase7d_buyer_readiness_scorer.py"
    spec = importlib.util.spec_from_file_location("phase7d_buyer_readiness_scorer", scorer_path)
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)

    score = scorer.score_case(
        {"id": "PA30_030_public_guard", "group": "route_browser_guard", "scenario_type": "guard_no_leak"},
        {"status": 200, "forbidden_internal_markers_present": [], "snippet": "No internal markers."},
    )

    assert score["include_in_buyer_usefulness_average"] is False
    assert score["binary_guard_pass"] is True
    assert score["overall_case_score"] is None


def test_phase7d_scorer_unwraps_authenticated_redacted_artifact_rows():
    scorer_path = Path(__file__).resolve().parents[1] / "scripts" / "phase7d_buyer_readiness_scorer.py"
    spec = importlib.util.spec_from_file_location("phase7d_buyer_readiness_scorer", scorer_path)
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)

    row = {
        "case": {
            "id": "PA30_001_LA_office_ti_positive_supported",
            "group": "commercial_launch_core",
            "scenario_type": "positive_supported",
            "vertical": "office_ti",
        },
        "redacted_result": {
            "coverage_truth": {"mode": "beta"},
            "apply_url": "https://permitla.lacitydbs.org/",
            "permits_required": [{"permit_type": "Building Permit — Commercial Tenant Improvement"}],
            "snippet": "Official apply path and clear permit-required answer.",
        },
        "score": {
            "status": "EXECUTED",
            "http_status_or_browser_status": 200,
            "apply_path_ok_yes_no": "YES",
            "coverage_truth_ok_yes_no": "YES",
            "permit_type_or_permits_required_present": True,
            "wrong_ahj_yes_no": "NO",
            "wrong_permit_yes_no": "NO",
            "wrong_vertical_leak_yes_no": "NO",
            "internal_leak_yes_no": "NO",
            "review_marker_terms": [],
        },
    }

    observed = scorer.observed_payload_for_scoring(row)
    score = scorer.score_case(row["case"], observed)

    assert observed["apply_path_ok_yes_no"] == "YES"
    assert observed["coverage_truth"] == {"mode": "beta"}
    assert score["overall_case_score"] == 5.0
