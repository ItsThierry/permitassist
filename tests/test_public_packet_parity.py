from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from family_reconciliation_gate import apply_family_reconciliation_gate
from public_packet import apply_public_packet_projection, build_public_packet
from scope_contract import build_scope_facts_v2
from server import build_checklist_fallback, render_share_page


def test_headline_segment_matches_occupancy():
    facts = build_scope_facts_v2("commercial warehouse to assembly change of use", "Portland", "OR")
    result = {"permit_decision": "REQUIRED", "permit_required": True, "permits_required": [{"permit_type": "Commercial Building / Change-of-Use Permit", "required": True}]}
    packet = build_public_packet(result, facts)
    assert packet.headline.startswith("Commercial")
    assert "Residential" not in packet.headline


def test_checklist_fallback_not_required():
    checklist = build_checklist_fallback({"permit_decision": "NOT_REQUIRED", "permit_required": False, "permit_name": "No permit required", "fee_range": "$500"}, "cosmetic work", "Phoenix", "AZ")
    text = "\n".join(item["label"] for item in checklist["items"])
    assert "Pull No permit required" not in text
    assert "Pay permit fee" not in text
    assert "No permit fee applies" in text
    assert "No permit is required" in text


def test_checklist_fallback_permit_required_false_without_decision_suppresses_fee_payment():
    checklist = build_checklist_fallback({"permit_required": False, "permit_name": "No permit required", "fee_range": "$500"}, "cosmetic work", "Phoenix", "AZ")
    text = "\n".join(item["label"] for item in checklist["items"])
    assert "Pay permit fee" not in text
    assert "No permit fee applies" in text


def test_conditional_rows_survive_public_sanitization():
    result = {"permit_decision": "REQUIRED", "permit_required": True, "permits_required": [{"permit_type": "Plumbing Permit", "required": True}]}
    gated = apply_family_reconciliation_gate(result, "commercial sign face replacement", "Phoenix", "AZ")
    projected = apply_public_packet_projection(gated, build_scope_facts_v2("commercial sign face replacement", "Phoenix", "AZ"))
    assert projected.get("conditional_permits")
    assert any(r["decision"] == "CONDITIONAL" for r in projected.get("public_packet_rows", []))


def test_public_projection_strips_gate_internal_row_fields():
    result = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permits_required": [{
            "permit_type": "Electrical Permit",
            "required": True,
            "family": "electrical",
            "source_status": "official_portal_fallback",
            "rationale": "deterministic implication: request scope triggers electrical",
            "_debug": "secret",
        }],
    }
    projected = apply_public_packet_projection(result, build_scope_facts_v2("electrical panel upgrade", "Phoenix", "AZ"))
    blob = str(projected)
    assert "source_status" not in blob
    assert "rationale" not in blob
    assert "deterministic implication" not in blob
    assert "_debug" not in blob


def test_share_page_and_api_render_same_rows():
    result = {"permit_decision": "REQUIRED", "permit_required": True, "permits_required": [{"permit_type": "Electrical Permit", "required": True}]}
    projected = apply_public_packet_projection(result, build_scope_facts_v2("200 amp electrical panel upgrade", "Gilbert", "AZ"))
    html = render_share_page({"data": projected, "job_type": "200 amp electrical panel upgrade", "city": "Gilbert", "state": "AZ"})
    assert "Electrical Permit" in html
    assert "public_packet_rows" in html
