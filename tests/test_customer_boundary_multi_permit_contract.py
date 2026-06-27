#!/usr/bin/env python3
"""Customer-boundary contract tests for multi-permit lookup packages."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_FILES = [
    ROOT / "frontend" / "index.html",
    ROOT / "frontend" / "preview-modern-reskinned-index.html",
    *(ROOT / "frontend" / "trades").glob("*.html"),
]


def _seattle_mini_split_public():
    import api.research_engine as engine
    from api.server import build_customer_permit_view_model

    job = (
        "Install one ductless mini-split heat pump system for an existing single-family residence, "
        "including one outdoor condenser and two indoor wall-mounted heads. New electrical circuit/disconnect as required."
    )
    scope_contract = engine.build_scope_contract(job, "Seattle", "WA", job_category="residential")
    raw = {
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_name": "Mechanical Permit — HVAC Equipment Changeout",
        "permit_type": "Mechanical Permit — HVAC Equipment Changeout",
        "job_category": "residential",
        "permits_required": [
            {"permit_type": "Mechanical Permit — HVAC Equipment Changeout", "required": True, "notes": "model output"}
        ],
        "companion_permits": [
            {"permit_type": "Electrical Permit", "reason": "Required for new circuit/disconnect", "certainty": "almost_certain"}
        ],
        "sources": [
            {"url": "https://www.seattle.gov/sdci/permits/permits-we-issue-(a-z)/mechanical-permit", "title": "Mechanical Permit - SDCI | seattle.gov"},
            {"url": "https://www.seattle.gov/sdci/permits/permits-we-issue-(a-z)/electrical-permit", "title": "Electrical Permit - SDCI | seattle.gov"},
            {"url": "https://www.seattle.gov/sdci/permits/permits-we-issue-(a-z)/refrigeration-permit", "title": "Refrigeration Permit - SDCI | seattle.gov"},
        ],
        "_scope_contract": scope_contract,
    }
    raw = engine.apply_scope_aware_permit_classification(raw, job, scope_contract)
    return build_customer_permit_view_model(raw, job, "Seattle", "WA", job_category="residential")


@pytest.mark.parametrize(
    "term",
    [
        "exact online apply path is metadata",
        "keep this row visible",
        "if not verified",
        "universal_filing_packet_reconciler",
        "provenance",
    ],
)
def test_linter_flags_internal_filing_reconciler_terms(term):
    from api.server import lint_customer_visible_result

    dirty = {
        "permit_decision": "REQUIRED",
        "permit_kind": "Mechanical",
        "customer_next_step": "Apply for the permit.",
        "applying_office": "Seattle SDCI",
        "customer_result_summary": {"source_cue": "Official source path found"},
        "permits_required": [{"permit_type": "Mechanical Permit", "notes": term}],
    }

    assert lint_customer_visible_result(dirty, "Seattle", "WA")


def test_seattle_mini_split_customer_boundary_lists_all_required_permits_and_scrubs_internal_notes():
    from api.server import lint_customer_visible_result

    public = _seattle_mini_split_public()
    blob = json.dumps(public, sort_keys=True).lower()

    assert public["permit_decision"] == "REQUIRED"
    assert set(public.get("required_permit_families") or []) >= {"Electrical", "Mechanical", "Refrigeration"}
    assert "multiple permits required" in (public.get("permit_name") or "").lower()
    assert "multiple permits" in (public.get("customer_result_summary") or {}).get("permit_kind", "").lower()
    assert "electrical" in public["required_permit_summary"].lower()
    assert "mechanical" in public["required_permit_summary"].lower()
    assert "refrigeration" in public["required_permit_summary"].lower()

    for row in public["permits_required"]:
        text = json.dumps(row).lower()
        if "electrical" in text:
            assert row.get("kind") == "Electrical"
        if "refrigeration" in text:
            assert row.get("kind") == "Refrigeration"

    forbidden = [
        "exact online apply path is metadata",
        "keep this row visible",
        "if not verified",
        "universal_filing_packet_reconciler",
        "provenance",
    ]
    assert [term for term in forbidden if term in blob] == []
    assert lint_customer_visible_result(public, "Seattle", "WA") == []


def test_frontend_summary_card_does_not_cross_alias_global_and_row_permit_names():
    for path in FRONTEND_FILES:
        html = path.read_text(encoding="utf-8")
        assert "Also known as:" not in html, path
        assert "d.permit_name || d.permit_type || permits[0]?.permit_type" not in html, path
        assert "sameRowAlias" in html, path
        assert "howToMultiPermit" in html, path
        assert "requiredPermitNames" in html, path
        assert "Permit categories to file" in html, path
