from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "test-not-real-openai-key")
os.environ.setdefault("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
os.environ.setdefault("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD", "1")

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import server  # noqa: E402


def _stale_not_required_payload() -> dict:
    return {
        "permit_required": False,
        "permit_decision": "NOT_REQUIRED",
        "permit_verdict": "NO",
        "permit_name": "No permit required",
        "permit_kind": "Not Required",
        "permits_required": [],
        "not_required_reason": "Recorded artifact said no permit required, but no positive exemption source supports that conclusion.",
        "sources": [{"url": "https://example.gov/permits", "title": "Permit office", "source_type": "official_local"}],
        "source_urls": ["https://example.gov/permits"],
        "public_packet": {
            "permit_required_verdict": "NOT_REQUIRED",
            "decision": "NOT_REQUIRED",
            "rows": [{"family": "not_required", "permit_name": "No permit required", "decision": "NOT_REQUIRED"}],
            "sealed_public_packet_hash": "stale-not-required-packet",
        },
        "sealed_public_packet_hash": "stale-not-required-packet",
    }


def _families(public: dict, key: str = "permits_required") -> set[str]:
    return {
        str(row.get("family") or row.get("filing_family") or "")
        for row in public.get(key) or []
        if isinstance(row, dict)
    }


def _packet_families(public: dict) -> set[str]:
    packet = public.get("public_packet") if isinstance(public.get("public_packet"), dict) else {}
    return {
        str(row.get("family") or row.get("filing_family") or "")
        for row in packet.get("rows") or []
        if isinstance(row, dict) and str(row.get("decision") or "").upper() == "REQUIRED"
    }


def test_s3_c040_commercial_refrigerated_cooler_rooms_flip_stale_no_to_required_packet() -> None:
    job = "install refrigerated produce cooler rooms in warehouse with ammonia-free condensing units and drains"

    public = server.build_customer_permit_view_model(_stale_not_required_payload(), job, "Salinas", "CA", job_category="commercial")

    assert public["permit_decision"] == "REQUIRED", public
    assert public["permit_required"] is True
    assert {"building", "mechanical", "refrigeration", "plumbing"}.issubset(_families(public))
    assert {"building_ti", "mechanical", "refrigeration", "plumbing"}.issubset(_packet_families(public))
    assert (public.get("public_packet") or {}).get("permit_required_verdict") == "REQUIRED"
    assert public.get("sealed_public_packet_hash") != "stale-not-required-packet"


def test_s3_c048_commercial_ev_transformer_trenching_flips_stale_no_to_required_packet() -> None:
    job = "install six commercial EV charging stations in parking lot with new transformer pad trenching and bollards"

    public = server.build_customer_permit_view_model(_stale_not_required_payload(), job, "Sparks", "NV", job_category="commercial")

    assert public["permit_decision"] == "REQUIRED", public
    assert public["permit_required"] is True
    assert {"building", "grading", "electrical"}.issubset(_families(public))
    assert {"building_ti", "grading", "electrical"}.issubset(_packet_families(public))
    assert (public.get("public_packet") or {}).get("permit_required_verdict") == "REQUIRED"
    assert public.get("sealed_public_packet_hash") != "stale-not-required-packet"


def test_s3_r027_wood_stove_chimney_flips_stale_no_to_required_packet() -> None:
    job = "install new freestanding wood stove with chimney penetration in single family living room"

    public = server.build_customer_permit_view_model(_stale_not_required_payload(), job, "Olympia", "WA", job_category="residential")

    assert public["permit_decision"] == "REQUIRED", public
    assert public["permit_required"] is True
    assert {"mechanical", "fire_suppression"}.issubset(_families(public))
    assert {"mechanical", "fire_suppression"}.issubset(_packet_families(public))
    assert (public.get("public_packet") or {}).get("permit_required_verdict") == "REQUIRED"
    assert public.get("render_seal_status") != "UNSEALED_NOT_REQUIRED_CONTRACT"


def test_s3_r004_like_for_like_backyard_fence_is_not_flat_required() -> None:
    job = "replace backyard wood fence same height 6 feet along rear property line no retaining wall no pool barrier"
    stale_required = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_name": "Building Permit",
        "permit_kind": "Fence",
        "permits_required": [{"permit_type": "Building Permit", "family": "building", "required": True, "decision": "REQUIRED"}],
    }

    public = server.build_customer_permit_view_model(stale_required, job, "Bend", "OR", job_category="residential")

    assert public["permit_decision"] == "NOT_REQUIRED", public
    assert public["permit_required"] is False
    assert not public.get("permits_required")
    reason = str(public.get("not_required_reason") or "").lower()
    assert "same-height" in reason or "same height" in reason
    assert "retaining wall" in reason
    assert "pool-barrier" in reason or "pool barrier" in reason
    assert "verify local fence/zoning thresholds" in reason


def test_s3_r004_risky_fence_variants_do_not_use_like_for_like_no_permit_gate() -> None:
    risky_jobs = [
        "replace backyard wood fence same height 6 feet with taller section near front yard no retaining wall no pool barrier",
        "replace backyard wood fence same height on corner lot no retaining wall no pool barrier",
        "replace backyard wood fence same height in historic overlay no retaining wall no pool barrier",
        "replace backyard wood fence same height with new subpanel and gate operator circuit no retaining wall no pool barrier",
    ]
    stale_required = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_name": "Building Permit",
        "permit_kind": "Fence",
        "permits_required": [{"permit_type": "Building Permit", "family": "building", "required": True, "decision": "REQUIRED"}],
    }

    for job in risky_jobs:
        public = server.build_customer_permit_view_model(stale_required, job, "Bend", "OR", job_category="residential")
        assert public["permit_decision"] == "REQUIRED", (job, public)
        assert public["permit_required"] is True
        assert public.get("permits_required")


def test_s3_runtime_sanitizer_scrubs_scope_firebreak_leak_instead_of_500(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    scope_contract = {"category": "commercial", "family": "commercial_ti", "forbidden_scope_tags": ["residential_solar"]}
    result = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "expert_notes": [{"note": "Solar PV interconnection package applies to this job"}],
        "_scope_contract": scope_contract,
    }

    cleaned = server.sanitize_customer_visible_result(result, strip_internal_keys=False)

    assert cleaned["permit_decision"] == "REQUIRED"
    assert cleaned.get("_scope_firebreak_removed")
    assert "residential solar" not in str(cleaned).lower()
