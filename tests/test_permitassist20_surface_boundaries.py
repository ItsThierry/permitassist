import json
import os
import sys
from importlib import util
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper_phase20_surfaces",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_import_server = _debug_helper._import_server
_LiveServer = _debug_helper._LiveServer
_post_json = _debug_helper._post_json

PLACEHOLDER = "Permit -- verify exact AHJ title"
SAFE_INTERIM = "Manual filing path confirmation in progress"
FORBIDDEN_SURFACE_TEXT = (
    PLACEHOLDER,
    "Permit — verify exact AHJ title",
    "Needs review for: fee_range",
    "verify before merging",
)


def _placeholder_result():
    return {
        "permit_verdict": "YES",
        "permit_required": True,
        "confidence": "high",
        "permit_name": PLACEHOLDER,
        "permit_type": PLACEHOLDER,
        "permits_required": [
            {
                "permit_type": PLACEHOLDER,
                "permit_name": "Permit — verify exact AHJ title",
                "required": True,
                "notes": "Needs review for: fee_range; verify before merging.",
            }
        ],
        "fee_range": "$500-$1,000",
        "approval_timeline": {"simple": "Needs review for: fee_range"},
        "what_to_bring": ["Plans", "fee_range worksheet"],
        "pro_tips": ["verify before merging"],
        "sources": [],
    }


def _assert_surface_sanitized(surface: str):
    assert SAFE_INTERIM in surface
    for forbidden in FORBIDDEN_SURFACE_TEXT:
        assert forbidden not in surface


def test_share_round_trip_sanitizes_placeholder_names_and_internal_copy(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    slug = server.create_share("bathroom remodel", "Dallas", "TX", _placeholder_result())
    shared = server.get_share(slug)

    surface = json.dumps(shared, sort_keys=True, default=str)
    _assert_surface_sanitized(surface)


def test_checklist_fallback_sanitizes_placeholder_permit_title(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    checklist = server.build_checklist_fallback(_placeholder_result(), "bathroom remodel", "Dallas", "TX")

    surface = json.dumps(checklist, sort_keys=True, default=str)
    _assert_surface_sanitized(surface)


def test_report_pdf_text_lines_sanitize_placeholder_and_internal_copy(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    lines = server._pdf_text_lines_for_report("bathroom remodel", "Dallas", "TX", _placeholder_result())

    surface = "\n".join(lines)
    _assert_surface_sanitized(surface)


def test_email_report_sanitizes_html_and_text_bodies(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    sent = {}

    def fake_send(to_email, subject, text, html):
        sent.update({"to_email": to_email, "subject": subject, "text": text, "html": html})
        return True

    monkeypatch.setattr(server, "resend_send", fake_send)

    assert server.send_email_report("contractor@example.com", "bathroom remodel", "Dallas", "TX", _placeholder_result()) is True
    surface = json.dumps(sent, sort_keys=True, default=str)
    _assert_surface_sanitized(surface)


def test_batch_permit_non_evidence_path_sanitizes_placeholder_names(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "research_permit", lambda *args, **kwargs: _placeholder_result())

    with _LiveServer(server.Handler) as live:
        status, body = _post_json(
            f"{live.base}/api/batch-permit",
            {"lookups": [{"job_type": "bathroom remodel", "city": "Dallas", "state": "TX"}]},
        )

    assert status == 200
    _assert_surface_sanitized(body)


def test_surface_sanitizer_is_idempotent_and_preserves_non_name_strings(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = _placeholder_result()
    result["apply_url"] = "https://example.gov/permit-path?id=ABC-123."
    result["office_address"] = "123 Main St., Suite 200."
    result["fee_range"] = "$500-$1,000."

    once = server.sanitize_customer_visible_result(result, "bathroom remodel", "Dallas", "TX")
    twice = server.sanitize_customer_visible_result(once, "bathroom remodel", "Dallas", "TX")

    assert once == twice
    assert once["apply_url"] == "https://example.gov/permit-path?id=ABC-123."
    assert once["office_address"] == "123 Main St., Suite 200."
    assert once["fee_range"] == "$500-$1,000."
    assert once["permit_name"] == SAFE_INTERIM
    _assert_surface_sanitized(json.dumps(once, sort_keys=True, default=str))


def test_surface_sanitizer_rewrites_embedded_fallback_without_mangling_sentence(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_name": "Permit -- verify exact AHJ title before starting work",
        "description": "Select Permit -- verify exact AHJ title in the portal before filing. Keep this final sentence.",
    }

    sanitized = server.sanitize_customer_visible_result(result, "bathroom remodel", "Dallas", "TX")

    assert sanitized["permit_name"] == SAFE_INTERIM
    assert sanitized["description"] == f"Select {SAFE_INTERIM} in the portal before filing. Keep this final sentence."
    _assert_surface_sanitized(json.dumps(sanitized, sort_keys=True, default=str))


def test_checklist_fee_precedence_prefers_fee_range_then_fees_then_fee(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    base = _placeholder_result()
    base["fee_range"] = "$500-$900"
    base["fees"] = "$400-$800"
    base["fee"] = "$300"
    checklist = server.build_checklist_fallback(base, "bathroom remodel", "Dallas", "TX")
    assert any(item.get("detail") == "$500-$900" for item in checklist["items"])

    base.pop("fee_range")
    checklist = server.build_checklist_fallback(base, "bathroom remodel", "Dallas", "TX")
    assert any(item.get("detail") == "$400-$800" for item in checklist["items"])

    base.pop("fees")
    checklist = server.build_checklist_fallback(base, "bathroom remodel", "Dallas", "TX")
    assert any(item.get("detail") == "$300" for item in checklist["items"])
