import copy
import json
import os
import sys
from importlib import util
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.rendered_output_lint import (  # noqa: E402
    FALLBACK_NAME_RE,
    lint_rendered_output,
    sanitize_permit_display_name,
)

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper_asap_trust",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_import_server = _debug_helper._import_server


FORBIDDEN_CUSTOMER_TEXT = (
    "Permit -- verify exact AHJ title",
    "Permit — verify exact AHJ title",
    "Needs review for: fee_range",
    "fee_range",
    "Needs AHJ verification",
    "verify before merging",
)


def _base_yes_result():
    return {
        "permit_verdict": "YES",
        "permit_required": True,
        "confidence": "high",
        "confidence_reason": "Permit required from current lookup; exact local name was not source-confirmed.",
        "permits_required": [],
        "fee_range": "$500-$1,000",
        "approval_timeline": "2-4 weeks",
        "inspections": ["final building"],
        "apply_url": "",
        "apply_phone": "",
        "sources": [],
        "checklist": [],
        "rejection_patterns": [],
    }


def _record(field, value, *, vertical="residential", state="TX", ahj="City of Dallas"):
    return {
        "record_id": f"dallas-{vertical}-{field}",
        "state": state,
        "ahj_name": ahj,
        "vertical": vertical,
        "field": field,
        "claim_value": value,
        "field_status": "verified",
        "confidence": "high",
        "ingestion_ready": True,
        "source_scope_limit": "Source-backed field only; separate fees and filing steps may still need confirmation.",
        "record_fingerprint_sha256": "a" * 64,
        "field_evidence": [
            {
                "field": field,
                "source_url": "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/Pages/permits.aspx",
                "source_title": "Dallas Building Inspection Permits",
                "exact_quote_or_snippet": f"Official source lists {value}.",
                "quote_found": True,
                "last_verified_utc": "2026-05-05T12:00:00Z",
                "stale_after_utc": "2027-05-05T12:00:00Z",
                "reverify_after_utc": "2027-04-05T12:00:00Z",
            }
        ],
    }


def _write_exact_name_pack(path: Path, records: list[dict]) -> Path:
    pack = {
        "metadata": {
            "evidence_pack_version": "step7b_offline_v1_test",
            "fingerprint_sha256": "f" * 64,
            "production_wiring_allowed": False,
        },
        "validation": {"verdict": "FAIL_CLOSED_NOT_INGESTION_READY"},
        "records": records,
    }
    path.write_text(json.dumps(pack, indent=2), encoding="utf-8")
    return path


def _enable_pack(monkeypatch, pack_path: Path):
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_ENABLED", "true")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_MODE", "dallas_step7h_preview")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_EXPECTED_FINGERPRINT", "f" * 64)
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PATH", str(pack_path))


def _customer_surface_blob(result, html=""):
    # Customer-visible contract check: rendered/report copy only. Backend JSON may
    # retain stable internal keys with None values for API compatibility.
    return html or json.dumps({
        "permit_name": result.get("permit_name"),
        "permit_type": result.get("permit_type"),
        "_permit_display_name": result.get("_permit_display_name"),
        "warnings": result.get("customer_warnings") or [],
    }, sort_keys=True, default=str)


def test_placeholder_title_and_internal_fee_field_are_linted_from_customer_text():
    result = {
        "permit_verdict": "YES",
        "needs_review": True,
        "_primary_scope": "residential",
        "permit_name": "Permit -- verify exact AHJ title",
        "permit_type": "Permit -- verify exact AHJ title",
        "permits_required": [{"permit_type": "Permit -- verify exact AHJ title", "required": True}],
        "sources": [],
    }
    rendered = "Permit -- verify exact AHJ title\nNeeds review for: fee_range\nverify before merging"

    report = lint_rendered_output(result, rendered, "residential bathroom remodel", "Dallas", "TX")
    codes = {v["code"] for v in report["violations"]}

    assert "fallback_text_in_permit_title" in codes
    assert "internal_debug_language_visible" in codes


def test_enforce_rendered_output_contract_hides_lint_codes_from_customer_warnings(monkeypatch, tmp_path):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_required": True,
        "confidence": "high",
        "confidence_reason": "Needs review for: fee_range; verify before merging.",
        "_primary_scope": "commercial_office_ti",
        "permit_name": "Building Permit — Tenant Improvement / Office Interior Alteration",
        "permit_type": "Building Permit — Tenant Improvement / Office Interior Alteration",
        "permits_required": [
            {
                "permit_type": "Building Permit — Tenant Improvement / Office Interior Alteration",
                "required": True,
            }
        ],
        "sources": [],
    }
    warnings = []

    server._enforce_rendered_output_contract(
        result,
        "office tenant improvement with interior alteration",
        "Dallas",
        "TX",
        warnings,
    )
    public_blob = json.dumps(
        server.redact_public_output({"result": result, "warnings": warnings, "quality_warnings": warnings}),
        sort_keys=True,
        default=str,
    )

    assert result["_rendered_lint"]["violations"]
    assert warnings == ["Some output details need source confirmation before customer use."]
    assert "internal_debug_language_visible" not in public_blob
    assert "fee_range" not in public_blob
    assert "verify before merging" not in public_blob.lower()
    assert "_rendered_lint" not in public_blob
    assert "_rendering_context" not in public_blob


def test_sanitize_missing_residential_permit_name_returns_customer_safe_fail_closed_copy():
    display = sanitize_permit_display_name("", scope="residential", job_type="residential bathroom remodel")

    assert display == "Residential Building Permit"
    for forbidden in FORBIDDEN_CUSTOMER_TEXT:
        assert forbidden not in display
    assert not FALLBACK_NAME_RE.search(display)


def test_display_permit_name_wins_main_permit_slot_when_source_backed(monkeypatch, tmp_path):
    pack_path = _write_exact_name_pack(
        tmp_path / "exact-name-pack.json",
        [
            _record("display_permit_name", "Residential Remodel Building Permit"),
            _record("official_permit_name", "Residential Building Permit"),
            _record("official_application_category", "Building / Residential Remodel"),
            _record("apply_url", "https://dallascityhall.com/permits"),
        ],
    )
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda *_args, **_kwargs: True)

    result = server.finalize_permit_lookup_result(
        copy.deepcopy(_base_yes_result()),
        "residential bathroom remodel with plumbing and electrical",
        "Dallas",
        "TX",
        explicit_vertical="residential",
    )

    assert result["_permit_display_name"] == "Residential Remodel Building Permit"
    assert result["permit_name"] == "Residential Remodel Building Permit"
    assert result["permit_type"] == "Residential Remodel Building Permit"
    assert result["permit_name_confidence"] == "high"
    assert result["permit_required_confidence"] == "high"
    assert "display_permit_name" in result["_evidence_pack"]["matched_fields"]
    assert result["_evidence_pack"]["permit_name_source_field"] == "display_permit_name"
    assert result["fee_range"] is None


def test_missing_exact_name_fails_closed_without_placeholder_or_debug_language(monkeypatch, tmp_path):
    pack_path = _write_exact_name_pack(
        tmp_path / "category-only-pack.json",
        [
            _record("official_application_category", "Building / Residential Remodel"),
            _record("apply_url", "https://dallascityhall.com/permits"),
        ],
    )
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda *_args, **_kwargs: True)

    result = server.finalize_permit_lookup_result(
        copy.deepcopy(_base_yes_result()),
        "residential bathroom remodel with plumbing and electrical",
        "Dallas",
        "TX",
        explicit_vertical="residential",
    )
    html = server.render_white_label_report_html({"result": result, "job_type": "residential bathroom remodel", "city": "Dallas", "state": "TX"})
    surface = _customer_surface_blob(result, html)

    assert result["_permit_display_name"] == "Building / Residential Remodel"
    assert result["permit_name"] == "Building / Residential Remodel"
    assert result["permit_type"] == "Building / Residential Remodel"
    assert result["permit_type_verified"] is False
    assert result["permit_name_status"] == "official_category_confirmed_exact_label_missing"
    assert result["permit_name_confidence"] == "medium"
    assert "Building / Residential Remodel" in surface
    assert "https://dallascityhall.com/permits" in surface
    assert "Permit / application / form</strong><span>-</span>" in surface
    assert "Needs AHJ verification" not in surface
    assert "Needs review for:" not in surface
    assert "verify before merging" not in surface
    assert "Permit -- verify exact AHJ title" not in surface
    assert "Permit — verify exact AHJ title" not in surface


def test_raw_fee_range_field_name_and_global_ahj_badge_do_not_render_for_field_specific_fee_uncertainty(monkeypatch, tmp_path):
    pack_path = _write_exact_name_pack(
        tmp_path / "fee-failed-pack.json",
        [
            _record("display_permit_name", "Residential Remodel Building Permit"),
            _record("apply_url", "https://dallascityhall.com/permits"),
        ],
    )
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda *_args, **_kwargs: True)

    result = server.finalize_permit_lookup_result(
        copy.deepcopy(_base_yes_result()),
        "residential bathroom remodel with plumbing and electrical",
        "Dallas",
        "TX",
        explicit_vertical="residential",
    )
    html = server.render_white_label_report_html({"result": result, "job_type": "residential bathroom remodel", "city": "Dallas", "state": "TX"})
    surface = _customer_surface_blob(result, html)

    assert "fee_range" not in surface
    assert "Needs AHJ verification" not in surface
    assert "Fee information is not source-confirmed for this lookup" in surface


def test_white_label_report_sanitizes_placeholder_permits_required_items(monkeypatch, tmp_path):
    server = _import_server(tmp_path, monkeypatch)
    result = copy.deepcopy(_base_yes_result())
    result["_primary_scope"] = "residential"
    result["permits_required"] = [
        {"permit_type": "Permit -- verify exact AHJ title", "required": True},
        {"permit_name": "Permit — verify exact AHJ title", "required": True},
    ]

    html = server.render_white_label_report_html({"result": result, "job_type": "residential bathroom remodel", "city": "Dallas", "state": "TX"})

    assert "Residential Building Permit" in html
    assert "No safe source URL attached; official-source retrieval is still in progress." in html
    assert "verify exact" not in html.lower()
    assert "PendingView" not in html
    assert "Pending reason" not in html
    assert "Missing source-backed fields" not in html
    assert "Permit -- verify exact AHJ title" not in html
    assert "Permit — verify exact AHJ title" not in html


def test_frontend_core_permit_badge_does_not_use_needs_review_as_global_name_uncertainty():
    frontend = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(encoding="utf-8")
    start = frontend.index("function corePermitUnverified")
    end = frontend.index("\nfunction ", start + 1)
    body = frontend[start:end]

    assert "!!d?.needs_review" not in body
    assert "d?.permit_name_confidence === 'low'" in body
    assert "d?.permit_name_confidence === 'medium'" in body


def test_hidden_trigger_public_payload_strips_internal_verify_before_merging_notes():
    from api.hidden_trigger_detector import detect_hidden_triggers

    triggers = detect_hidden_triggers(
        "restaurant tenant improvement with food service and grease interceptor",
        "Phoenix",
        "AZ",
        "commercial_restaurant",
        {},
    )
    payload = json.dumps(triggers, sort_keys=True)

    assert triggers
    assert "verify before merging" not in payload
    assert "[verify" not in payload.lower()
