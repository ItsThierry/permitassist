import os
import sys
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "test-key-for-import-only")
ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))


def _decision(job: str, *, city: str = "Phoenix", state: str = "AZ", result: dict | None = None) -> dict:
    from api.decision_resolver import resolve_customer_decision

    base = {"sources": [f"https://www.{city.lower().replace(' ', '')}.gov/permits"]}
    if result:
        base.update(result)
    return resolve_customer_decision({"result": base, "job_type": job, "city": city, "state": state})


def test_cosmetic_minor_scopes_resolve_not_required():
    cases = [
        "replace carpet with floating laminate flooring no subfloor structural changes",
        "replace kitchen cabinets like for like no plumbing electrical or wall changes",
        "interior painting only no structural electrical plumbing mechanical work",
        "patch drywall and repaint interior walls cosmetic repair only no structural electrical plumbing mechanical work",
    ]
    for job in cases:
        dto = _decision(job)
        assert dto["permit_decision"] == "NOT_REQUIRED", (job, dto)
        assert dto["permit_required"] is False


def test_real_structural_or_trade_scopes_stay_required():
    cases = [
        "replace structural beam, no electrical",
        "repair structural subfloor",
        "move load-bearing wall, no plumbing",
        "install cabinets with plumbing relocation",
        "replace kitchen cabinets and relocate sink plumbing",
        "new flooring plus structural subfloor replacement",
        "no flooring change but structural beam added",
        "no permit history on file but structural work required for a beam repair",
    ]
    for job in cases:
        dto = _decision(job)
        assert dto["permit_decision"] == "REQUIRED", (job, dto)
        assert dto["permit_required"] is True


def test_commercial_cosmetic_scope_does_not_default_to_not_required():
    from api.decision_resolver import resolve_customer_decision

    dto = resolve_customer_decision(
        {
            "result": {"sources": ["https://city.example.gov/permits"]},
            "job_type": "commercial office repaint and replace carpet with floating laminate flooring no structural electrical plumbing mechanical work",
            "city": "Example City",
            "state": "CA",
            "scope_contract": {"category": "commercial", "family": "commercial tenant improvement"},
        }
    )
    assert dto["permit_decision"] == "REQUIRED", dto
    assert dto["permit_required"] is True


def test_customer_surface_required_cannot_ship_no_permit_prose():
    from api.permit_decision import apply_permit_decision_contract, validate_customer_surface_contract

    result = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_name": "Building Permit",
        "permit_kind": "Building",
        "applying_office": "Test Building Department",
        "sources": ["https://phoenix.gov/pdd"],
        "approval_timeline": {"simple": "No permit review needed for this scope."},
        "fee_range": "$0 permit fee for this exact scope",
        "customer_next_step": "No permit needed; keep notes.",
    }
    public = apply_permit_decision_contract(result, "replace structural beam, no electrical", "Phoenix", "AZ")
    text = str(public).lower()
    assert public["permit_decision"] == "REQUIRED"
    assert "no permit needed" not in text
    assert "no permit review needed" not in text
    assert "$0 permit fee for this exact scope" not in text
    assert "required_no_permit_prose_contradiction" not in validate_customer_surface_contract(public, real_ahj=False)


def test_customer_surface_not_required_cannot_tell_customer_to_apply_online():
    from api.permit_decision import apply_permit_decision_contract, validate_customer_surface_contract

    result = {
        "permit_decision": "NOT_REQUIRED",
        "permit_required": False,
        "permit_name": "Building Permit",
        "permit_kind": "Building",
        "applying_office": "Test Building Department",
        "sources": ["https://phoenix.gov/pdd"],
        "customer_next_step": "Apply online at https://phoenix.gov/pdd under Building Permit.",
    }
    public = apply_permit_decision_contract(
        result,
        "replace kitchen cabinets like for like no plumbing electrical or wall changes",
        "Phoenix",
        "AZ",
    )
    text = str(public).lower()
    assert public["permit_decision"] == "NOT_REQUIRED"
    assert "apply online" not in text
    assert "not_required_apply_online_contradiction" not in validate_customer_surface_contract(public, real_ahj=False)


def test_residential_north_salt_lake_addition_does_not_emit_commercial_apply_url():
    from api.server import build_customer_permit_view_model

    result = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_kind": "Building",
        "permit_name": "Residential Building Permit — Addition / Remodel",
        "job_category": "residential",
        "applying_office": "North Salt Lake Building Division",
        "apply_url": "https://nslcity.org/1033/Commercial-Permits",
        "online_application_url": "https://nslcity.org/1033/Commercial-Permits",
        "apply_path": {
            "portal_url": "https://nslcity.org/1033/Commercial-Permits",
            "permit_type": "Residential Building Permit — Addition / Remodel",
            "permit_category": "Residential / Trade Permit",
            "support_level": "verified path",
        },
        "sources": [{"url": "https://nslcity.org/1033/Commercial-Permits", "title": "Commercial Permits"}],
        "source_urls": ["https://nslcity.org/1033/Commercial-Permits"],
    }
    public = build_customer_permit_view_model(
        result,
        "single-family 450 sq ft bedroom addition",
        "North Salt Lake",
        "UT",
        job_category="residential",
        explicit_vertical="residential",
    )
    assert public["permit_decision"] == "VERIFY"
    assert public["permit_required"] is None
    assert "commercial-permits" not in str(public.get("apply_url") or "").lower()
    assert "commercial-permits" not in str((public.get("apply_path") or {}).get("portal_url") or "").lower()
    assert "apply online" not in str(public.get("customer_next_step") or "").lower()
    assert "iccsafe" not in str(public).lower()
    assert "nfpa" not in str(public).lower()
    assert "energy.gov" not in str(public).lower()


def test_segment_guard_does_not_strip_generic_office_or_home_portal_words():
    from api.server import _apply_url_segment_mismatch

    residential_result = {"job_category": "residential", "sources": [{"url": "https://city.example.gov/office-of-permits", "title": "Office of Permits"}]}
    assert not _apply_url_segment_mismatch(
        "https://city.example.gov/office-of-permits",
        "Example City",
        "CA",
        residential_result,
        "single-family bathroom repair",
    )

    commercial_result = {"job_category": "commercial", "sources": [{"url": "https://city.example.gov/home/permits", "title": "Permit portal home"}]}
    assert not _apply_url_segment_mismatch(
        "https://city.example.gov/home/permits",
        "Example City",
        "CA",
        commercial_result,
        "commercial tenant improvement for retail suite",
    )
