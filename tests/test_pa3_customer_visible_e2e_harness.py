import hashlib
import json
import urllib.error
import urllib.parse
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

INTERNAL_MARKERS = (
    "permitassist3_revised",
    "completion_ticket",
    "live_retrieval",
    "source_content_hash_sha256",
    "source_snapshot_ref",
    "PendingView",
    "pending_reason",
    "lookup_id",
    "customer_final\": false",
    "No customer-final answer yet",
    "manual completion pending",
    "Missing source-backed fields",
    "[REDACTED]",
    "Traceback",
)

WEAK_FINAL_MARKERS = (
    "Likely permits",
    "Verify exact filing path with the AHJ",
    "Verify final permit type",
    "PermitAssist is guidance only",
)


def _request_json(url, body, headers=None, *, decode="text"):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
            payload = raw if decode == "bytes" else raw.decode("utf-8", "replace")
            return resp.status, dict(resp.headers.items()), payload
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        payload = raw if decode == "bytes" else raw.decode("utf-8", "replace")
        return exc.code, dict(exc.headers.items()), payload


def _get_text(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, dict(resp.headers.items()), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read().decode("utf-8", "replace")


def _fixture_result(permit_name, *, marker):
    return {
        "permit_verdict": "YES",
        "confidence": "high",
        "permit_type_verified": True,
        "permit_name": permit_name,
        "permit_type": permit_name,
        "_permit_display_name": permit_name,
        "permits_required": [{"permit_type": permit_name, "required": True}],
        "apply_url": "https://abc.austintexas.gov/citizenportal/app/landing",
        "apply_path": {"verification_note": f"Portal category: {permit_name} — {marker}"},
        "claim_citations": [{
            "id": "C1",
            "claim": "Permit type",
            "quoted_snippet": f"Apply using {permit_name}.",
            "source_url": "https://abc.austintexas.gov/citizenportal/app/landing",
            "checked_at": "2026-05-22",
            "confidence": "high",
        }],
        "approval_timeline": {"simple": "Plan review route shown in portal", "complex": "Plan review route shown in portal"},
        "job_summary": f"{permit_name} lookup {marker}",
    }


def _api_response_for(result, *, job_type="office TI", city="Denver", state="CO"):
    raw = dict(result)
    raw.update({"job_type": job_type, "city": city, "state": state, "remaining_lookups": -1})
    return raw


def _surface_findings(name, content):
    text = content.decode("latin-1", "replace") if isinstance(content, bytes) else str(content)
    return [
        {"surface": name, "marker": marker}
        for marker in (*INTERNAL_MARKERS, *WEAK_FINAL_MARKERS)
        if marker.lower() in text.lower()
    ]


def test_customer_visible_redaction_preserves_public_portal_app_urls(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    payload = {
        "apply_url": "https://abc.austintexas.gov/citizenportal/app/landing",
        "sources": [{"url": "https://abc.austintexas.gov/citizenportal/app/landing", "title": "Austin portal"}],
        "debug_path": "/app/data/internal-cache.json",
    }

    redacted = server.redact_public_output(payload)
    serialized = json.dumps(redacted)

    assert "https://abc.austintexas.gov/citizenportal/app/landing" in serialized
    assert "[REDACTED]" not in redacted["apply_url"]
    assert redacted["debug_path"] == "[REDACTED]"


def test_quality_gate_preserves_commercial_no_permit_consistency(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "NO",
        "permit_required": False,
        "job_category": "commercial",
        "permits_required": [],
        "confidence": "medium",
        "fee_range": "No permit fee expected for cosmetic painting only.",
    }

    out = server.apply_permitiq_quality_gate(
        result,
        "cosmetic painting only, no electrical, no plumbing, no structural, no occupancy change",
        "Denver",
        "CO",
    )

    assert out["permit_verdict"] == "NO"
    assert out.get("permit_required") is False
    assert out.get("permits_required") == []


def test_stage0_white_label_uses_real_payload_shape_and_rejects_raw_api_response(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_session_token", lambda token: "beta@example.com" if token == "valid.test" else None)
    auth = {"X-Session-Token": "valid.test"}
    result_a = _fixture_result("Commercial Tenant Finish Permit", marker="case-a")
    result_b = _fixture_result("Commercial Interior Remodel Permit", marker="case-b")

    with _LiveServer(server.Handler) as live:
        status_a, _, html_a = _request_json(
            f"{live.base}/api/white-label-report",
            {"result": result_a, "job_type": "office TI", "city": "Denver", "state": "CO"},
            auth,
        )
        status_b, _, html_b = _request_json(
            f"{live.base}/api/white-label-report",
            {"result": result_b, "job_type": "medical clinic TI", "city": "Austin", "state": "TX"},
            auth,
        )
        malformed_status, _, malformed_body = _request_json(
            f"{live.base}/api/white-label-report",
            _api_response_for(result_a),
            auth,
        )

    assert status_a == 200
    assert status_b == 200
    assert "Commercial Tenant Finish Permit" in html_a
    assert "Commercial Interior Remodel Permit" in html_b
    assert hashlib.sha256(html_a.encode()).hexdigest() != hashlib.sha256(html_b.encode()).hexdigest()
    assert malformed_status == 422
    assert "result, job_type, city, state" in malformed_body


def test_stage0_white_label_malformed_payloads_fail_closed_422(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_session_token", lambda token: "beta@example.com" if token == "valid.test" else None)
    auth = {"X-Session-Token": "valid.test"}
    malformed_payloads = [
        {},
        {"result": {}, "job_type": "office TI", "city": "Denver", "state": "CO"},
        {"result": [], "job_type": "office TI", "city": "Denver", "state": "CO"},
        {"result": _fixture_result("Commercial Tenant Finish Permit", marker="x"), "city": "Denver", "state": "CO"},
        {"result": _fixture_result("Commercial Tenant Finish Permit", marker="x"), "job_type": "office TI", "state": "CO"},
        {"result": _fixture_result("Commercial Tenant Finish Permit", marker="x"), "job_type": "office TI", "city": "Denver"},
    ]

    with _LiveServer(server.Handler) as live:
        responses = [
            _request_json(f"{live.base}/api/white-label-report", payload, auth)
            for payload in malformed_payloads
        ]

    assert [status for status, _, _ in responses] == [422] * len(malformed_payloads)


def test_stage1_report_pdf_rejects_malformed_result_shape(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    auth = {"X-Session-Token": "valid.test"}
    malformed_payload = {"result": [], "job_type": "office TI", "city": "Denver", "state": "CO"}

    with _LiveServer(server.Handler) as live:
        status, _, body = _request_json(f"{live.base}/api/report-pdf", malformed_payload, auth, decode="bytes")

    assert status == 400
    assert isinstance(body, bytes)
    assert b"job_type, city, state, result required" in body


def test_stage1_batch_permit_normalizes_timeline_in_customer_api_response(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = _fixture_result("Commercial Tenant Finish Permit", marker="batch")
    result["approval_timeline"] = "2-4 weeks"
    monkeypatch.setattr(server, "research_permit", lambda *args, **kwargs: dict(result))

    with _LiveServer(server.Handler) as live:
        status, _, body = _request_json(
            f"{live.base}/api/batch-permit",
            {"lookups": [{"job_type": "office TI", "city": "Denver", "state": "CO"}]},
        )

    payload = json.loads(body)
    assert status == 200
    assert payload["results"][0]["approval_timeline"] == {"simple": "2-4 weeks"}


def test_stage1_v1_permit_normalizes_timeline_without_evidence_flag(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = _fixture_result("Commercial Tenant Finish Permit", marker="v1")
    result["approval_timeline"] = "2-4 weeks"
    monkeypatch.setattr(server, "research_permit", lambda *args, **kwargs: dict(result))
    monkeypatch.setattr(server, "validate_api_key", lambda header: ("beta@example.com", "test-key") if header == "Bearer valid.test" else (None, None))
    monkeypatch.setattr(server, "is_paid_user", lambda email: True)

    with _LiveServer(server.Handler) as live:
        status, _, body = _request_json(
            f"{live.base}/api/v1/permit",
            {"job_type": "office TI", "city": "Denver", "state": "CO"},
            {"Authorization": "Bearer valid.test"},
        )

    payload = json.loads(body)
    assert status == 200
    assert payload["approval_timeline"] == {"simple": "2-4 weeks"}


def test_static_report_template_does_not_ship_customer_forbidden_pending_copy(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    with _LiveServer(server.Handler) as live:
        status, _, html = _get_text(f"{live.base}/report.html")

    assert status == 200
    assert _surface_findings("static_report_template", html) == []


def test_stage0_stage1_customer_visible_e2e_surfaces_execute_and_scan(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = _fixture_result("Commercial Tenant Finish Permit", marker="e2e-surface-marker")
    monkeypatch.setattr(server, "research_permit", lambda *args, **kwargs: dict(result))
    monkeypatch.setattr(server, "validate_url", lambda *args, **kwargs: True)
    monkeypatch.setattr(server, "validate_session_token", lambda token: "beta@example.com" if token == "valid.test" else None)
    monkeypatch.setattr(server, "is_paid_user", lambda email: True)
    auth = {"X-Session-Token": "valid.test", "X-Client-Fingerprint": "pa3-e2e-test"}

    with _LiveServer(server.Handler) as live:
        api_status, _, api_body = _request_json(
            f"{live.base}/api/permit",
            {"job_type": "office TI", "city": "Denver", "state": "CO", "job_category": "commercial"},
            auth,
        )
        assert api_status == 200
        api_payload = json.loads(api_body)

        share_status, _, share_body = _request_json(
            f"{live.base}/api/share",
            {"result": api_payload, "job_type": "office TI", "city": "Denver", "state": "CO"},
            auth,
        )
        assert share_status == 200
        slug = json.loads(share_body)["slug"]
        report_status, _, report_html = _get_text(f"{live.base}/report/{urllib.parse.quote(slug)}")

        pdf_status, pdf_headers, pdf_body = _request_json(
            f"{live.base}/api/report-pdf",
            {"result": api_payload, "job_type": "office TI", "city": "Denver", "state": "CO"},
            auth,
            decode="bytes",
        )
        wl_status, _, wl_html = _request_json(
            f"{live.base}/api/white-label-report",
            {"result": api_payload, "job_type": "office TI", "city": "Denver", "state": "CO"},
            auth,
        )

    assert report_status == 200
    assert pdf_status == 200
    assert pdf_headers["Content-Type"].startswith("application/pdf")
    assert wl_status == 200
    # Customer surfaces must not replay the raw legacy API fallback wording or
    # expose PendingView/internal retry state. Under the Stage 3 flag, exact
    # source-backed fixtures may now return CustomerView/EXACT_FINAL directly;
    # legacy/no-flag paths still render customer-safe required guidance instead.
    if api_payload.get("view_type") == "CustomerView":
        assert api_payload.get("customer_final") is True
        assert api_payload.get("final_answer_state") in {"EXACT_FINAL", "PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE"}
        assert "exact permit type needs AHJ verification" not in json.dumps(api_payload)
    else:
        assert "exact permit type needs AHJ verification" in json.dumps(api_payload)
    report_text = str(report_html)
    wl_text = str(wl_html)
    assert isinstance(pdf_body, bytes)
    expected_rendered_signal = "Permit Required"
    assert expected_rendered_signal in report_text
    assert expected_rendered_signal.encode() in pdf_body
    assert expected_rendered_signal in wl_text
    for forbidden in ("No customer-final answer yet", "PendingView", "pending_reason", "lookup_id"):
        assert forbidden not in report_text
        assert forbidden.encode() not in pdf_body
        assert forbidden not in wl_text

    scan_findings = []
    for name, content in [
        ("api", api_body),
        ("report", report_html),
        ("pdf", pdf_body),
        ("white_label", wl_html),
    ]:
        scan_findings.extend(_surface_findings(name, content))

    # Stage 0/1 customer-final release gate: rendered-surface scanning must be
    # clean for internal leaks and weak generic final-answer copy.
    assert scan_findings == []


def test_stage1_approval_timeline_shapes_are_normalized_before_checklist_render(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    for raw_timeline in (
        {"simple": "same day", "complex": "2 weeks"},
        "2-4 weeks",
        None,
        ["2 weeks", "4 weeks"],
    ):
        result = {
            "permit_name": "Commercial Tenant Finish Permit",
            "approval_timeline": raw_timeline,
            "permits_required": [{"permit_type": "Commercial Tenant Finish Permit"}],
        }
        normalized = server.normalize_approval_timeline(result.get("approval_timeline"))
        result["approval_timeline"] = normalized
        checklist = server.build_checklist_fallback(result, "office TI", "Denver", "CO")
        assert isinstance(normalized, (dict, type(None)))
        assert checklist["items"]
        assert all(isinstance(item["label"], str) for item in checklist["items"])


def test_stage1_evidence_pack_timeline_constructor_returns_safe_shape():
    from api import evidence_pack_runtime as runtime

    pack = runtime.EvidencePackRuntime(
        path="",
        version="test",
        fingerprint="test",
        records=(),
        mode="test_mode",
    )

    timeline = runtime._customer_facing_timeline("2-4 weeks", city="Denver", state="CO", pack=pack)

    assert isinstance(timeline, dict)
    assert timeline == {"simple": "2-4 weeks"}


def test_stage1_report_share_pdf_white_label_paths_do_not_crash_on_timeline_shape_drift(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_session_token", lambda token: "beta@example.com" if token == "valid.test" else None)
    auth = {"X-Session-Token": "valid.test"}
    cases = [
        ("residential kitchen remodel", "Denver", "CO", "2-4 weeks"),
        ("rooftop solar installation", "Phoenix", "AZ", ["same-day intake", "plan review after intake"]),
        ("commercial MEP tenant improvement", "Miami", "FL", None),
        ("office tenant improvement", "Middletown", "OH", {"simple": "AHJ unverified — manual completion needed"}),
    ]

    with _LiveServer(server.Handler) as live:
        for job_type, city, state, timeline in cases:
            result = _fixture_result("Permit required — exact permit type needs AHJ verification", marker=f"{job_type}-{city}")
            result["permit_type_verified"] = False
            result["approval_timeline"] = timeline
            result["ahj_status"] = "AHJ unverified" if city == "Middletown" else "supported_fixture"
            payload = {"result": result, "job_type": job_type, "city": city, "state": state}

            share_status, _, share_body = _request_json(f"{live.base}/api/share", payload, auth)
            assert share_status == 200
            slug = json.loads(share_body)["slug"]
            report_status, _, report_html = _get_text(f"{live.base}/report/{urllib.parse.quote(slug)}")
            pdf_status, pdf_headers, pdf_body = _request_json(f"{live.base}/api/report-pdf", payload, auth, decode="bytes")
            wl_status, _, wl_html = _request_json(f"{live.base}/api/white-label-report", payload, auth)

            assert report_status == 200
            assert pdf_status == 200
            assert pdf_headers["Content-Type"].startswith("application/pdf")
            assert wl_status == 200
            for surface, content in (("report", report_html), ("pdf", pdf_body), ("white_label", wl_html)):
                findings = _surface_findings(surface, content)
                assert not any(f["marker"] in INTERNAL_MARKERS for f in findings)
