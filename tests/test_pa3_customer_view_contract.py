import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
sys.path.insert(0, str(API))

from customer_view import (  # noqa: E402
    EXACT_FINAL,
    PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE,
    OUT_OF_SCOPE,
    CustomerOutputScanner,
    CustomerView,
    build_customer_view,
    normalize_approval_timeline_for_customer,
)


def _official_source():
    return {
        "source_url": "https://www.austintexas.gov/department/building-permits",
        "source_title": "City of Austin Building Permits",
        "exact_quote_or_snippet": "Commercial Building Permit applications are filed through the Austin Build + Connect portal.",
        "retrieved_at_utc": "2026-05-22T00:00:00Z",
        "source_content_hash_sha256": "a" * 64,
        "source_snapshot_ref": "internal-snapshot-should-not-leak",
    }


def test_exact_source_backed_portal_path_builds_allowlisted_customer_view():
    raw = {
        "permit_required": True,
        "permit_type": "Commercial Building Permit — Tenant Improvement / Restaurant Interior Alteration",
        "apply_url": "https://www.austintexas.gov/abc",
        "approval_timeline": "Plan review route shown in official portal",
        "permitassist3_revised": {"completion_ticket": {"tracker_id": "internal"}},
        "source_content_hash_sha256": "b" * 64,
        "source_backed_official_portal_category_path": "Commercial Building Permit > Tenant Improvement / Restaurant Interior Alteration",
        "official_source_provenance": [_official_source()],
    }

    view = build_customer_view(raw, job_type="restaurant tenant improvement", city="Austin", state="TX")

    assert isinstance(view, CustomerView)
    public = view.to_dict()
    assert public["final_answer_state"] == EXACT_FINAL
    assert public["customer_final"] is True
    assert public["official_portal_category_path"] == "Commercial Building Permit > Tenant Improvement / Restaurant Interior Alteration"
    assert public["approval_timeline"] == {"simple": "Plan review route shown in official portal"}
    assert set(public) == CustomerView.PUBLIC_FIELDS
    assert CustomerOutputScanner().scan(public)["findings"] == []


def test_missing_exact_name_or_portal_path_returns_customer_safe_required_guidance_not_pending():
    raw = {
        "permit_required": True,
        "permit_type": "Permit Required",
        "permit_name": "Building Permit",
        "permits_required": [{"permit_type": "Building Permit", "required": True}],
        "warnings": ["Verify with AHJ before filing."],
        "official_source_provenance": [_official_source()],
    }

    view = build_customer_view(raw, job_type="garage remodel", city="Austin", state="TX")

    assert isinstance(view, CustomerView)
    public = view.to_dict()
    assert public["view_type"] == "CustomerView"
    assert public["final_answer_state"] == PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE
    assert public["customer_final"] is True
    assert public["permit_required"] is True
    assert public["permit_name"] == "Residential Building Permit"
    assert public["official_portal_category_path"] is None
    assert public["filing_path"] != "Permit Required"
    assert public["filing_path"].startswith("Permit Required — Residential Building Permit")
    assert "application/form path" in public["filing_path"]
    assert "lookup_id" not in public
    assert "pending_reason" not in public
    assert "missing_fields" not in public
    assert set(public) == CustomerView.PUBLIC_FIELDS
    assert CustomerOutputScanner().scan(public)["findings"] == []


def test_required_missing_exact_path_without_engine_name_uses_specific_job_based_permit_type():
    view = build_customer_view(
        {"permit_required": True, "permit_type": "Permit Required", "permit_name": "Permit Required"},
        job_type="restaurant tenant improvement with kitchen",
        city="Miami",
        state="FL",
    )

    public = view.to_dict()
    assert public["final_answer_state"] == PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE
    assert public["permit_required"] is True
    assert public["vertical"] == "restaurant_ti"
    assert public["permit_name"] == "Commercial Tenant Improvement / Alteration Building Permit"
    assert public["filing_path"].startswith("Permit Required — Commercial Tenant Improvement / Alteration Building Permit")
    assert public["filing_path"] != "Permit Required"
    assert CustomerOutputScanner().scan(public)["findings"] == []


def test_residential_kitchen_remodel_does_not_get_restaurant_ti_permit_name():
    view = build_customer_view(
        {"permit_required": True, "permit_type": "Permit Required", "permit_name": "Permit Required"},
        job_type="residential kitchen remodel",
        city="Denver",
        state="CO",
    )

    public = view.to_dict()
    assert public["vertical"] == "residential"
    assert public["permit_name"] == "Residential Building Permit"
    assert public["filing_path"].startswith("Permit Required — Residential Building Permit")
    assert "Commercial Tenant Improvement" not in public["filing_path"]


def test_banned_phrase_in_would_be_final_returns_sanitized_customer_guidance_not_pending():
    raw = {
        "permit_required": True,
        "source_backed_exact_permit_name": "Commercial Building Permit — Tenant Improvement",
        "official_source_provenance": [_official_source()],
        "customer_summary": "This is likely required and varies by AHJ.",
    }

    view = build_customer_view(raw, job_type="office tenant improvement", city="Austin", state="TX")

    assert isinstance(view, CustomerView)
    public = view.to_dict()
    assert public["view_type"] == "CustomerView"
    assert public["final_answer_state"] == PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE
    assert public["customer_final"] is True
    assert "pending_reason" not in public
    assert "lookup_id" not in public
    assert CustomerOutputScanner().scan(public)["findings"] == []


def test_source_backed_official_application_category_can_be_exact_final_path():
    raw = {
        "permit_required": True,
        "permit_name": "Commercial Building > Tenant Improvement",
        "permit_type": "Commercial Building > Tenant Improvement",
        "permit_name_status": "official_category_confirmed_exact_label_missing",
        "permit_name_source_field": "official_application_category",
        "claim_citations": [{
            "source_url": "https://www.austintexas.gov/department/building-permits",
            "source_title": "City of Austin Building Permits",
            "quoted_snippet": "Use Commercial Building > Tenant Improvement for interior alteration applications.",
            "checked_at": "2026-05-22",
        }],
    }

    view = build_customer_view(raw, job_type="office tenant improvement", city="Austin", state="TX")

    public = view.to_dict()
    assert public["final_answer_state"] == EXACT_FINAL
    assert public["official_portal_category_path"] == "Commercial Building > Tenant Improvement"
    assert public["permit_name"] is None
    assert CustomerOutputScanner().scan(public)["findings"] == []


def test_malformed_shapes_become_customer_safe_guidance_without_crash():
    view = build_customer_view(["not", "a", "dict"], job_type="office TI", city="Austin", state="TX")
    public = view.to_dict()
    assert public["view_type"] == "CustomerView"
    assert public["final_answer_state"] == PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE
    assert public["customer_final"] is True
    assert "lookup_id" not in public
    assert "pending_reason" not in public
    assert "missing_fields" not in public
    assert normalize_approval_timeline_for_customer(["bad", "shape"]) is None
    assert normalize_approval_timeline_for_customer(None) is None
    assert normalize_approval_timeline_for_customer({"simple": "2 weeks"}) == {"simple": "2 weeks"}


def test_recursive_scanner_catches_internal_key_names_and_banned_values():
    payload = {
        "safe": "ok",
        "nested": [{"source_content_hash_sha256": "a" * 64}],
        "other": {"label": "contact AHJ before filing"},
    }

    findings = CustomerOutputScanner().scan(payload)["findings"]

    assert any(hit["path"].endswith("source_content_hash_sha256") and hit["kind"] == "key" for hit in findings)
    assert any("contact AHJ" in hit["text"] and hit["kind"] == "value" for hit in findings)


def test_recursive_scanner_catches_raw_pending_view_customer_forbidden_fields():
    payload = {
        "view_type": "PendingView",
        "final_answer_state": "PENDING_MANUAL_COMPLETION",
        "customer_final": False,
        "lookup_id": "pa3-cv-internal",
        "pending_reason": "manual completion pending",
        "missing_fields": ["Missing source-backed fields"],
    }

    findings = CustomerOutputScanner().scan(payload)["findings"]
    blob = "\n".join(f"{hit['path']} {hit['kind']} {hit['text']}" for hit in findings)

    assert "PendingView" in blob
    assert "PENDING_MANUAL_COMPLETION" in blob
    assert "customer_final" in blob
    assert "lookup_id" in blob
    assert "pending_reason" in blob
    assert "missing_fields" in blob
    assert "manual completion pending" in blob
    assert "Missing source-backed fields" in blob


def test_unsupported_out_of_scope_is_fail_closed_allowlisted_view():
    view = build_customer_view(
        {"unsupported": True, "error": "AHJ unsupported", "reason": "fake jurisdiction"},
        job_type="restaurant tenant improvement",
        city="Made Up City",
        state="ZZ",
    )

    public = view.to_dict()
    assert public["final_answer_state"] == OUT_OF_SCOPE
    assert public["view_type"] == "CustomerView"
    assert public["customer_final"] is True
    assert public["permit_required"] is None
    assert public["filing_path"].startswith("Invalid jurisdiction")
    assert "lookup_id" not in public
    assert "pending_reason" not in public
    assert "missing_fields" not in public
    assert CustomerOutputScanner().scan(public)["findings"] == []


def test_unsupported_ahj_status_and_coverage_truth_fail_closed_not_required_guidance():
    raw = {
        "error": "unsupported_ahj",
        "ahj_status": "unsupported",
        "message": "PermitAssist cannot verify this AHJ as real/supported.",
        "coverage_truth": {"status": "ahj_not_supported", "classification": "fake"},
    }

    view = build_customer_view(raw, job_type="office tenant improvement", city="Fakeville", state="ZZ")

    public = view.to_dict()
    assert public["view_type"] == "CustomerView"
    assert public["final_answer_state"] == OUT_OF_SCOPE
    assert public["customer_final"] is True
    assert public["permit_required"] is None
    assert public["filing_path"].startswith("Invalid jurisdiction")
    assert "Permit Required" not in public["filing_path"]
    assert "lookup_id" not in public
    assert "pending_reason" not in public
    assert CustomerOutputScanner().scan(public)["findings"] == []
