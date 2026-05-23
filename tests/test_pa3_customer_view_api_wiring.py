import json
import sys
import urllib.request
from importlib import util
from pathlib import Path

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_LiveServer = _debug_helper._LiveServer
_import_server = _debug_helper._import_server

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
sys.path.insert(0, str(API))

from customer_view import (  # noqa: E402
    EXACT_FINAL,
    PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE,
    OUT_OF_SCOPE,
    CustomerOutputScanner,
    CustomerView,
)


def _post_json_response(url, body, headers=None):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, dict(resp.headers.items()), json.loads(resp.read().decode("utf-8"))


def _official_source():
    return {
        "source_url": "https://www.austintexas.gov/department/building-permits",
        "source_title": "City of Austin Building Permits",
        "exact_quote_or_snippet": "Commercial Building Permit applications are filed through Austin Build + Connect.",
        "retrieved_at_utc": "2026-05-22T00:00:00Z",
        "source_content_hash_sha256": "a" * 64,
        "source_snapshot_ref": "internal-snapshot-should-not-leak",
    }


def _exact_raw_result():
    return {
        "permit_required": True,
        "source_backed_exact_permit_name": "Commercial Building Permit — Tenant Improvement / Restaurant Interior Alteration",
        "source_backed_official_portal_category_path": "Commercial Building Permit > Tenant Improvement / Restaurant Interior Alteration",
        "official_source_provenance": [_official_source()],
        "apply_url": "https://www.austintexas.gov/abc",
        "approval_timeline": "Plan review route shown in official portal",
        "permitassist3_revised": {"completion_ticket": {"tracker_id": "internal"}},
        "source_content_hash_sha256": "b" * 64,
    }


def _generic_raw_result():
    return {
        "permit_required": True,
        "permit_type": "Permit Required",
        "permit_name": "Building Permit",
        "permits_required": [{"permit_type": "Building Permit", "required": True}],
        "warnings": ["Verify with AHJ before filing."],
        "official_source_provenance": [_official_source()],
        "permitassist3_revised": {"completion_ticket": {"tracker_id": "internal"}},
    }


def _prime_server_for_lookup(server, monkeypatch, raw_result):
    monkeypatch.setattr(server, "classify_ahj_coverage", lambda city, state: {"classification": "verified", "status": "verified"})
    monkeypatch.setattr(server, "record_beta_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "record_lookup_stat", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "research_permit", lambda *args, **kwargs: dict(raw_result))


def test_pa3_customer_view_api_replaces_enabled_restaurant_response_with_allowlisted_exact_view(tmp_path, monkeypatch):
    monkeypatch.setenv("PA3_CUSTOMER_VIEW_VERTICALS", "restaurant")
    server = _import_server(tmp_path, monkeypatch)
    _prime_server_for_lookup(server, monkeypatch, _exact_raw_result())

    with _LiveServer(server.Handler) as live:
        status, _headers, payload = _post_json_response(
            f"{live.base}/api/permit",
            {"job_type": "restaurant tenant improvement", "city": "Austin", "state": "TX", "vertical": "restaurant_ti"},
            {"X-Sample-Demo": "1", "X-Client-Fingerprint": "pa3-cv-exact"},
        )

    assert status == 200
    assert payload["view_type"] == "CustomerView"
    assert payload["final_answer_state"] == EXACT_FINAL
    assert payload["customer_final"] is True
    assert "Commercial Building Permit" in payload["filing_path"]
    assert set(payload) == CustomerView.PUBLIC_FIELDS
    assert CustomerOutputScanner().scan(payload)["findings"] == []
    blob = json.dumps(payload, sort_keys=True)
    assert "permitassist3_revised" not in blob
    assert "completion_ticket" not in blob
    assert "source_content_hash_sha256" not in blob
    assert "source_snapshot_ref" not in blob


def test_pa3_customer_view_api_returns_safe_required_guidance_for_enabled_generic_final(tmp_path, monkeypatch):
    monkeypatch.setenv("PA3_CUSTOMER_VIEW_VERTICALS", "restaurant")
    server = _import_server(tmp_path, monkeypatch)
    _prime_server_for_lookup(server, monkeypatch, _generic_raw_result())

    with _LiveServer(server.Handler) as live:
        status, _headers, payload = _post_json_response(
            f"{live.base}/api/permit",
            {"job_type": "restaurant tenant improvement", "city": "Austin", "state": "TX", "vertical": "restaurant_ti"},
            {"X-Sample-Demo": "1", "X-Client-Fingerprint": "pa3-cv-pending"},
        )

    assert status == 200
    assert payload["view_type"] == "CustomerView"
    assert payload["final_answer_state"] == PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE
    assert payload["customer_final"] is True
    assert payload["permit_required"] is True
    assert payload["permit_name"] == "Commercial Tenant Improvement / Alteration Building Permit"
    assert payload["official_portal_category_path"] is None
    assert payload["filing_path"].startswith("Permit Required — Commercial Tenant Improvement / Alteration Building Permit")
    assert set(payload) == CustomerView.PUBLIC_FIELDS
    assert CustomerOutputScanner().scan(payload)["findings"] == []
    blob = json.dumps(payload, sort_keys=True)
    assert "PendingView" not in blob
    assert "pending_reason" not in blob
    assert "lookup_id" not in blob
    assert "Verify with AHJ" not in blob
    assert "completion_ticket" not in blob


def test_pa3_customer_view_api_is_disabled_for_non_flagged_verticals(tmp_path, monkeypatch):
    monkeypatch.setenv("PA3_CUSTOMER_VIEW_VERTICALS", "restaurant")
    server = _import_server(tmp_path, monkeypatch)
    _prime_server_for_lookup(server, monkeypatch, _exact_raw_result())

    with _LiveServer(server.Handler) as live:
        status, _headers, payload = _post_json_response(
            f"{live.base}/api/permit",
            {"job_type": "residential bathroom remodel", "city": "Austin", "state": "TX", "vertical": "residential"},
            {"X-Sample-Demo": "1", "X-Client-Fingerprint": "pa3-cv-disabled"},
        )

    assert status == 200
    assert payload.get("view_type") not in {"CustomerView", "PendingView"}
    assert "source_backed_exact_permit_name" in payload


def test_pa3_customer_view_api_preserves_source_backed_exact_name_with_claim_citations_only(tmp_path, monkeypatch):
    monkeypatch.setenv("PA3_CUSTOMER_VIEW_VERTICALS", "all")
    raw = {
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_type_verified": True,
        "source_backed_exact_permit_name": "Commercial Tenant Finish Permit",
        "permit_name": "Commercial Tenant Finish Permit",
        "permit_type": "Commercial Tenant Finish Permit",
        "claim_citations": [
            {
                "source_url": "https://www.denvergov.gov/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Community-Planning-and-Development/Apply-for-Permits",
                "source_title": "Denver permit source",
                "quoted_snippet": "Commercial Tenant Finish Permit applications are required for tenant finish work.",
                "checked_at": "2026-05-22T00:00:00Z",
            }
        ],
    }
    server = _import_server(tmp_path, monkeypatch)
    _prime_server_for_lookup(server, monkeypatch, raw)

    with _LiveServer(server.Handler) as live:
        status, _headers, payload = _post_json_response(
            f"{live.base}/api/permit",
            {"job_type": "office tenant improvement", "city": "Denver", "state": "CO", "vertical": "office_ti"},
            {"X-Sample-Demo": "1", "X-Client-Fingerprint": "pa3-cv-exact-claim-citations"},
        )

    assert status == 200
    assert payload["view_type"] == "CustomerView"
    assert payload["final_answer_state"] in {EXACT_FINAL, PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE}
    assert payload["permit_name"] == "Commercial Tenant Finish Permit"
    assert "Commercial Tenant Finish Permit" in payload["filing_path"]
    assert "Commercial Tenant Improvement / Alteration Building Permit" not in payload["filing_path"]
    assert CustomerOutputScanner().scan(payload)["findings"] == []


def test_pa3_customer_view_builder_handles_malformed_api_shapes_when_flagged(tmp_path, monkeypatch):
    monkeypatch.setenv("PA3_CUSTOMER_VIEW_VERTICALS", "all")
    server = _import_server(tmp_path, monkeypatch)

    payload = server.build_pa3_customer_view_api_response(
        ["not", "a", "dict"],
        "office tenant improvement",
        "Austin",
        "TX",
        explicit_vertical="office_ti",
    )

    assert payload["view_type"] == "CustomerView"
    assert payload["customer_final"] is True
    assert payload["final_answer_state"] == PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE
    assert "pending_reason" not in payload
    assert "lookup_id" not in payload
    assert CustomerOutputScanner().scan(payload)["findings"] == []


def test_pa3_customer_view_builder_projects_unsupported_ahj_as_invalid_unsupported_when_flagged(tmp_path, monkeypatch):
    monkeypatch.setenv("PA3_CUSTOMER_VIEW_VERTICALS", "all")
    server = _import_server(tmp_path, monkeypatch)

    payload = server.build_pa3_customer_view_api_response(
        {
            "error": "unsupported_ahj",
            "ahj_status": "unsupported",
            "message": "PermitAssist cannot verify this AHJ as real/supported.",
            "coverage_truth": {"status": "ahj_not_supported", "classification": "fake"},
        },
        "office tenant improvement",
        "Fakeville",
        "ZZ",
        explicit_vertical="office_ti",
    )

    assert payload["view_type"] == "CustomerView"
    assert payload["customer_final"] is True
    assert payload["final_answer_state"] == OUT_OF_SCOPE
    assert payload["permit_required"] is None
    assert payload["filing_path"].startswith("Invalid jurisdiction")
    assert "Permit Required" not in payload["filing_path"]
    assert "pending_reason" not in payload
    assert "lookup_id" not in payload
    assert set(payload) == CustomerView.PUBLIC_FIELDS
    assert CustomerOutputScanner().scan(payload)["findings"] == []


def test_cold_real_ahj_resolver_runs_before_customer_output_and_preserves_exact_official_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PA3_CUSTOMER_VIEW_VERTICALS", "all")
    server = _import_server(tmp_path, monkeypatch)
    calls = []

    def resolver(*, result, job_type, city, state, vertical):
        calls.append((job_type, city, state, vertical, result.get("final_answer_state")))
        return {
            "resolution_status": "exact_official_path_found",
            "exact_permit_name": "Commercial Tenant Finish Permit",
            "official_portal_category_path": "Building > Commercial > Tenant Finish",
            "apply_url": "https://www.denvergov.org/Government/Apply-for-Permits",
            "official_source_provenance": [{
                "source_url": "https://www.denvergov.org/Government/Apply-for-Permits",
                "source_title": "Denver permit applications",
                "exact_quote_or_snippet": "Commercial tenant finish permit applications are filed through the online permit portal.",
                "retrieved_at_utc": "2026-05-22T00:00:00Z",
                "source_content_hash_sha256": "c" * 64,
                "source_snapshot_ref": "resolver-internal-snapshot",
            }],
            "provider": "brave_search",
            "lookup_id": "internal-cold-lookup",
            "source_content_hash_sha256": "d" * 64,
        }

    payload = server.project_customer_visible_view(
        {
            "permit_required": True,
            "final_answer_state": "pending_active_retrieval",
            "customer_final": False,
            "permit_name": "Permit Required",
            "permitassist3_revised": {"completion_ticket": {"tracker_id": "internal"}},
        },
        "office tenant improvement",
        "Denver",
        "CO",
        explicit_vertical="office_ti",
        cold_resolver=resolver,
    )

    assert calls == [("office tenant improvement", "Denver", "CO", "office_ti", "pending_active_retrieval")]
    assert payload["view_type"] == "CustomerView"
    assert payload["final_answer_state"] == EXACT_FINAL
    assert payload["permit_name"] == "Commercial Tenant Finish Permit"
    assert payload["official_portal_category_path"] == "Building > Commercial > Tenant Finish"
    assert payload["apply_url"] == "https://www.denvergov.org/Government/Apply-for-Permits"
    assert CustomerOutputScanner().scan(payload)["findings"] == []
    blob = json.dumps(payload, sort_keys=True)
    assert "provider" not in blob
    assert "lookup_id" not in blob
    assert "source_content_hash_sha256" not in blob
    assert "source_snapshot_ref" not in blob


def test_cold_resolver_partial_guidance_keeps_concrete_permit_type_and_contact_path_source_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("PA3_CUSTOMER_VIEW_VERTICALS", "all")
    server = _import_server(tmp_path, monkeypatch)

    def resolver(**_kwargs):
        return {
            "resolution_status": "partial_source_backed_contact_fallback",
            "permit_name": "Commercial Tenant Improvement / Alteration Building Permit",
            "apply_url": "https://www.cityofpasadena.net/planning/permits/",
            "applying_office": "Pasadena Permit Center",
            "official_source_provenance": [{
                "source_url": "https://www.cityofpasadena.net/planning/permits/",
                "source_title": "Pasadena Permit Center",
                "exact_quote_or_snippet": "The Permit Center provides assistance with building permit applications and submittal requirements.",
                "retrieved_at_utc": "2026-05-22T00:00:00Z",
                "source_content_hash_sha256": "e" * 64,
            }],
            "raw_snippets": ["internal provider excerpt must not leak"],
        }

    payload = server.project_customer_visible_view(
        {"permit_required": True, "permit_type": "Permit Required", "permit_name": "Building Permit"},
        "restaurant tenant improvement",
        "Pasadena",
        "CA",
        explicit_vertical="restaurant_ti",
        cold_resolver=resolver,
    )

    assert payload["final_answer_state"] == PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE
    assert payload["permit_required"] is True
    assert payload["permit_name"] == "Commercial Tenant Improvement / Alteration Building Permit"
    assert payload["official_portal_category_path"] is None
    assert payload["apply_url"] == "https://www.cityofpasadena.net/planning/permits/"
    assert "Commercial Tenant Improvement / Alteration Building Permit" in payload["filing_path"]
    assert "permitting office" in payload["filing_path"]
    assert payload["official_source_provenance"][0]["source_title"] == "Pasadena Permit Center"
    assert CustomerOutputScanner().scan(payload)["findings"] == []
    blob = json.dumps(payload, sort_keys=True)
    assert "raw_snippets" not in blob
    assert "PendingView" not in blob
    assert '"customer_final": false' not in blob.lower()


def test_cold_resolver_is_bypassed_for_invalid_ahj_and_provider_unavailable_stays_customer_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("PA3_CUSTOMER_VIEW_VERTICALS", "all")
    server = _import_server(tmp_path, monkeypatch)
    calls = []

    def resolver(**_kwargs):
        calls.append(_kwargs)
        return {"resolution_status": "provider_unavailable", "provider": "serper", "pending_reason": "missing credentials"}

    invalid_payload = server.project_customer_visible_view(
        {"unsupported": True, "error": "unsupported_ahj", "ahj_status": "unsupported"},
        "medical clinic tenant improvement",
        "Fakeville",
        "ZZ",
        explicit_vertical="medical_clinic_ti",
        cold_resolver=resolver,
    )
    safe_payload = server.project_customer_visible_view(
        {"permit_required": True, "permit_type": "Permit Required", "permit_name": "Building Permit"},
        "medical clinic tenant improvement",
        "Quincy",
        "MA",
        explicit_vertical="medical_clinic_ti",
        cold_resolver=resolver,
    )

    assert calls and calls[0]["city"] == "Quincy"
    assert invalid_payload["final_answer_state"] == OUT_OF_SCOPE
    assert safe_payload["final_answer_state"] == PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE
    assert safe_payload["permit_name"] == "Commercial Tenant Improvement / Alteration Building Permit"
    assert CustomerOutputScanner().scan(invalid_payload)["findings"] == []
    assert CustomerOutputScanner().scan(safe_payload)["findings"] == []
    blob = json.dumps(safe_payload, sort_keys=True).lower()
    assert "provider" not in blob
    assert "serper" not in blob
    assert "pending_reason" not in blob
