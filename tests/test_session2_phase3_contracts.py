from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from live100_fable5_final_gate import apply_fable5_final_customer_gate
from public_packet import PacketInvariantError, apply_public_packet_projection, seal_packet
from scope_contract import build_scope_facts_v2
from server import build_customer_permit_view_model


def test_degraded_flag_survives_public_packet_projection_and_packet() -> None:
    facts = build_scope_facts_v2("install rooftop solar panels", "Phoenix", "AZ")
    projected = apply_public_packet_projection(
        {
            "permit_decision": "REQUIRED",
            "permit_required": True,
            "degraded_sources": True,
            "_runtime_degraded_fallback": {"reason": "TimeoutError"},
            "warnings": ["source support is degraded"],
            "permits_required": [
                {"permit_type": "Permit category requires AHJ verification", "family": "building", "required": True, "decision": "REQUIRED"}
            ],
            "applying_office": "Phoenix permit office",
            "source_urls": ["https://www.phoenix.gov/pdd"],
        },
        facts,
    )
    assert projected["degraded_sources"] is True
    assert projected["public_packet"]["degraded"] is True
    assert "TimeoutError" in projected["public_packet"]["degraded_reason"]
    assert projected.get("source_support", {}).get("decision_mutation_allowed") is False


def test_apply_phone_never_serializes_raw_url() -> None:
    public = build_customer_permit_view_model(
        {
            "permit_decision": "REQUIRED",
            "permit_required": True,
            "permit_verdict": "YES",
            "permit_name": "Electrical Permit",
            "permit_kind": "Electrical",
            "permits_required": [{"permit_type": "Electrical Permit", "family": "electrical", "required": True, "source_url": "https://www.phoenix.gov/pdd"}],
            "applying_office": "Phoenix Planning & Development",
            "apply_phone": "https://www.google.com/maps/search/Phoenix+permit+office",
            "apply_google_maps": "https://www.google.com/maps/search/Phoenix+permit+office",
            "apply_url": "https://www.phoenix.gov/pdd",
            "source_urls": ["https://www.phoenix.gov/pdd"],
        },
        "200 amp electrical panel replacement",
        "Phoenix",
        "AZ",
        job_category="residential",
    )
    assert not re.match(r"^https?://", str(public.get("apply_phone") or ""), re.I)
    assert public.get("apply_google_maps", "").startswith("https://www.google.com/maps")


def test_multi_permit_package_header_is_separate_from_primary_row_name() -> None:
    out = apply_fable5_final_customer_gate(
        {"permit_decision": "REQUIRED", "permit_required": True, "permits_required": []},
        "commercial restaurant tenant improvement with hood, plumbing, electrical and fire alarm",
        "Austin",
        "TX",
        {"category": "commercial"},
    )
    assert out.get("package_header", "").startswith("Multiple permits required:")
    assert not str(out.get("permit_name") or "").startswith("Multiple permits required:")
    assert not str(out.get("permit_type") or "").startswith("Multiple permits required:")


def test_required_packet_name_only_authority_fails_without_action_path_or_verified_contact() -> None:
    packet = {
        "schema_version": "final_public_permit_packet.v1",
        "segment": "commercial",
        "authority": {"name": "Local permit office", "source_urls": []},
        "decision": "REQUIRED",
        "permit_required_verdict": "REQUIRED",
        "rows": [{"permit_name": "Building Permit", "family": "building", "decision": "REQUIRED"}],
        "required_families": ["building"],
        "conditional_families": [],
        "documents": ["Scope of work"],
        "inspections": [],
        "fees": [],
        "checklist": ["Pull Building Permit before starting work"],
    }
    with pytest.raises(PacketInvariantError):
        seal_packet(packet, fail_hard=True)
    packet["authority"] = {"name": "Local permit office", "phone": "(602) 262-7811", "contact_status": "verified", "source_urls": []}
    assert seal_packet(packet, fail_hard=True)["sealed_public_packet_hash"].startswith("sha256:")


def test_frontend_tel_renderers_have_raw_url_guard() -> None:
    html_files = [ROOT / "frontend" / "index.html", *sorted((ROOT / "frontend" / "trades").glob("*.html"))]
    for path in html_files:
        text = path.read_text(encoding="utf-8")
        assert "looksLikeRawUrl" in text, path
        assert "safePhoneForDisplay" in text, path
        assert not re.search(r"const phone\s*=\s*d\.apply_phone \|\| d\.office_phone", text), path
        assert not re.search(r"const phone\s*=\s*d\.apply_phone \|\| ''", text), path


def test_system_prompt_does_not_pressure_model_to_invent_contact_or_fee() -> None:
    prompt = (API / "research_engine.py").read_text(encoding="utf-8")
    forbidden = [
        "ALWAYS include the phone number in apply_phone",
        "NEVER return null for apply_phone",
        "The ACTUAL fee in dollars, not \"varies\"",
    ]
    for phrase in forbidden:
        assert phrase not in prompt
    assert "return null" in prompt.lower()
    assert "provided sources" in prompt.lower()


def test_public_packet_source_role_unknown_mirror_contract() -> None:
    facts = build_scope_facts_v2("electrical panel replacement", "Phoenix", "AZ")
    projected = apply_public_packet_projection(
        {
            "permit_decision": "REQUIRED",
            "permit_required": True,
            "permits_required": [
                {"permit_type": "Electrical Permit", "family": "electrical", "required": True, "source_role": "UNKNOWN", "source_url": "https://www.phoenix.gov/pdd"}
            ],
            "sources": [{"url": "https://www.phoenix.gov/pdd", "title": "Phoenix PDD", "source_role": "UNKNOWN"}],
            "source_urls": ["https://www.phoenix.gov/pdd"],
        },
        facts,
    )
    blob = json.dumps({
        "public_packet": projected.get("public_packet"),
        "canonical_public_packet": projected.get("canonical_public_packet"),
        "public_packet_rows": projected.get("public_packet_rows"),
        "sources": projected.get("sources"),
    })
    assert "UNKNOWN" not in blob
