
import json
import os
import sqlite3
import sys
import urllib.error
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

from customer_view import CustomerOutputScanner  # noqa: E402

BANNED = [
    '"permit_name": "Permit Required"',
    '"permit_name": "Building Permit"',
    '"permit_type": "Permit Required"',
    '"permit_type": "Building Permit"',
    "Verify with AHJ",
    "verify requirements",
    "Always verify",
    "Contact local building department",
    "Pending verification with AHJ",
    "Varies by jurisdiction",
    "completion_ticket",
    "permitassist3_revised",
    "source_content_hash_sha256",
    "source_snapshot_ref",
    "resolved_by",
    "resolution_json",
]


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
        "source_backed_exact_permit_name": "Commercial Building Permit - Tenant Improvement / Restaurant Interior Alteration",
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


def _post_json(url, body, headers=None, decode_json=True):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
            if decode_json:
                return resp.status, dict(resp.headers.items()), json.loads(raw.decode("utf-8"))
            return resp.status, dict(resp.headers.items()), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        if decode_json:
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:
                body = raw.decode("utf-8", "replace")
            return exc.code, dict(exc.headers.items()), body
        return exc.code, dict(exc.headers.items()), raw


def _get_text(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, dict(resp.headers.items()), resp.read().decode("utf-8")


def _assert_clean_public_blob(blob):
    for banned in BANNED:
        assert banned not in blob


def test_stage4_white_label_validates_422_and_projects_required_guidance_not_pending_view(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_session_token", lambda token: "beta@example.com")
    with _LiveServer(server.Handler) as live:
        status, _headers, body = _post_json(f"{live.base}/api/white-label-report", {"result": {}}, {"X-Session-Token": "ok"})
        assert status == 422
        status, headers, raw = _post_json(
            f"{live.base}/api/white-label-report",
            {"job_type": "restaurant tenant improvement", "city": "Austin", "state": "TX", "result": _generic_raw_result()},
            {"X-Session-Token": "ok"},
            decode_json=False,
        )
    assert status == 200
    html = raw.decode("utf-8")
    assert "Permit Required" in html
    assert "source-backed" in html
    assert "Manual source-backed completion pending" not in html
    assert "No customer-final answer yet" not in html
    assert "PendingView" not in html
    assert "pending_reason" not in html
    assert "lookup_id" not in html
    _assert_clean_public_blob(html)


def test_stage4_share_page_and_pdf_consume_customer_projection(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    with _LiveServer(server.Handler) as live:
        status, _headers, body = _post_json(
            f"{live.base}/api/share",
            {"job_type": "restaurant tenant improvement", "city": "Austin", "state": "TX", "result": _generic_raw_result()},
        )
        assert status == 200
        page_status, _page_headers, html = _get_text(f"{live.base}/report/{body['slug']}")
        assert page_status == 200
        pdf_status, pdf_headers, pdf = _post_json(
            f"{live.base}/api/report-pdf",
            {"job_type": "restaurant tenant improvement", "city": "Austin", "state": "TX", "result": _generic_raw_result()},
            decode_json=False,
        )
    assert "Permit Required" in html
    assert "Manual source-backed completion pending" not in html
    assert "PendingView" not in html
    assert "pending_reason" not in html
    assert "lookup_id" not in html
    _assert_clean_public_blob(html)
    assert pdf_status == 200
    assert "application/pdf" in pdf_headers.get("Content-Type", "")
    pdf_text = pdf.decode("latin-1", "ignore")
    assert "Permit Required" in pdf_text
    assert "No customer-final answer yet" not in pdf_text
    assert "PendingView" not in pdf_text
    assert "pending_reason" not in pdf_text
    assert "lookup_id" not in pdf_text
    _assert_clean_public_blob(pdf_text)


def test_stage4_email_report_uses_customer_projection_only(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    sent = {}
    def fake_send(to_email, subject, text_body, html_body=""):
        sent.update({"to": to_email, "subject": subject, "text": text_body, "html": html_body})
        return True
    monkeypatch.setattr(server, "resend_send", fake_send)
    assert server.send_email_report("client@example.com", "restaurant tenant improvement", "Austin", "TX", _generic_raw_result()) is True
    blob = json.dumps(sent, sort_keys=True)
    assert "Permit Required" in blob
    assert "No customer-final answer yet" not in blob
    assert "PendingView" not in blob
    assert "pending_reason" not in blob
    assert "lookup_id" not in blob
    _assert_clean_public_blob(blob)


def test_stage5_pending_queue_and_manual_resolve_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PA3_CUSTOMER_VIEW_VERTICALS", "all")
    monkeypatch.setenv("PERMITASSIST_ADMIN_TOKEN", "admin-test-token")
    server = _import_server(tmp_path, monkeypatch)
    setattr(server, "ADMIN_TOKEN", "admin-test-token")
    public = server.build_pa3_customer_view_api_response(_generic_raw_result(), "restaurant tenant improvement", "Austin", "TX", explicit_vertical="restaurant_ti")
    assert public["view_type"] == "CustomerView"
    assert public["customer_final"] is True
    assert "lookup_id" not in public
    assert "pending_reason" not in public
    assert CustomerOutputScanner().scan(public)["findings"] == []
    queue = server.list_pending_lookups(status="open")
    pending_row = next(row for row in queue if row["city"] == "Austin" and row["state"] == "TX")
    with _LiveServer(server.Handler) as live:
        q_status, _q_headers, q_body = _post_json(
            f"{live.base}/api/admin/pending-lookups/resolve",
            {"lookup_id": pending_row["lookup_id"], "result": _exact_raw_result(), "resolved_by": "ops@test"},
            {"X-Admin-Token": "admin-test-token"},
        )
        assert q_status == 200
        view = q_body["customer_view"]
        get_req = urllib.request.Request(f"{live.base}/api/admin/pending-lookups?status=all", headers={"X-Admin-Token": "admin-test-token"})
        with urllib.request.urlopen(get_req, timeout=5) as resp:
            admin_queue = json.loads(resp.read().decode("utf-8"))
    assert view["view_type"] == "CustomerView"
    assert view["customer_final"] is True
    assert CustomerOutputScanner().scan(view)["findings"] == []
    blob = json.dumps(view, sort_keys=True)
    _assert_clean_public_blob(blob)
    assert any(row["lookup_id"] == pending_row["lookup_id"] and row["status"] == "resolved" for row in admin_queue["pending_lookups"])
