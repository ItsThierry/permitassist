import json
import sqlite3
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
_post_json = _debug_helper._post_json


def _post_json_response(url, body, headers=None):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, dict(resp.headers.items()), resp.read().decode("utf-8")


def test_permitiq_quality_gate_repairs_commercial_residential_primary(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "confidence": "high",
        "permits_required": [{"permit_type": "Residential HVAC Permit"}],
        "sources": [],
        "companion_permits": [],
    }

    gated = server.apply_permitiq_quality_gate(
        result,
        "medical clinic tenant improvement with exam rooms and x-ray",
        "Cambridge",
        "MA",
    )

    primary = gated["permits_required"][0]["permit_type"]
    assert "Medical Clinic" in primary
    assert "Residential HVAC" not in primary
    assert gated["needs_review"] is True
    assert gated["confidence"] == "medium"
    assert gated["quality_warnings"]


def test_claim_citations_never_invent_missing_quotes(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "confidence": "high",
        "apply_url": "https://example.gov/permits",
        "fee_range": "$1,000-$2,000",
        "permits_required": [{"permit_type": "Building Permit — Commercial TI"}],
        "sources": ["https://example.gov/permits"],
    }

    citations = server.build_claim_citations(result)

    assert citations
    assert citations[0]["source_url"] == "https://example.gov/permits"
    assert citations[0]["quoted_snippet"] == ""
    assert citations[0]["confidence"] == "needs_verification"
    assert any("quoted source snippets" in w for w in result["quality_warnings"])


def test_apply_path_adds_stop_before_final_submit(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "apply_url": "https://aca-prod.accela.com/example",
        "permits_required": [{"permit_type": "Building Permit — Tenant Improvement"}],
    }

    apply_path = server.build_apply_path(result, "restaurant tenant improvement", "Austin", "TX")

    assert apply_path["platform"] == "Accela / Citizen Access"
    assert apply_path["stop_before"] == "final submit, payment, signature, or legal attestation"
    assert "Commercial Building" in apply_path["permit_category"]


def test_admin_can_create_paid_test_session_for_smoke(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "ADMIN_TOKEN", "admin.test")
    server.research_permit = lambda *a, **k: {
        "permit_verdict": "YES",
        "confidence": "high",
        "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Office Interior Alteration"}],
        "sources": [{"url": "https://denvergov.org/permits", "snippet": "Tenant finish permits are required."}],
    }

    with _LiveServer(server.Handler) as live:
        email = urllib.parse.quote("paid-smoke@example.com")

        no_token_req = urllib.request.Request(
            f"{live.base}/api/admin/create-session?email={email}&plan=solo",
        )
        try:
            urllib.request.urlopen(no_token_req, timeout=5)
            assert False, "admin create-session must reject requests without X-Admin-Token"
        except urllib.error.HTTPError as exc:
            assert exc.code == 401

        invalid_plan_req = urllib.request.Request(
            f"{live.base}/api/admin/create-session?email={email}&plan=enterprise",
            headers={"X-Admin-Token": "admin.test"},
        )
        try:
            urllib.request.urlopen(invalid_plan_req, timeout=5)
            assert False, "admin create-session must reject unsupported plans"
        except urllib.error.HTTPError as exc:
            assert exc.code == 400

        team_req = urllib.request.Request(
            f"{live.base}/api/admin/create-session?email=team-smoke%40example.com&plan=team",
            headers={"X-Admin-Token": "admin.test"},
        )
        with urllib.request.urlopen(team_req, timeout=5) as resp:
            team_session = json.loads(resp.read().decode("utf-8"))
        assert team_session["plan"] == "team"
        assert team_session["paid"] is True

        req = urllib.request.Request(
            f"{live.base}/api/admin/create-session?email={email}&plan=solo",
            headers={"X-Admin-Token": "admin.test"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            session = json.loads(resp.read().decode("utf-8"))

        assert session["email"] == "paid-smoke@example.com"
        assert session["plan"] == "solo"
        assert session["paid"] is True
        assert session["token"]
        assert server.get_user("paid-smoke@example.com")["plan"] == "solo"
        assert server.get_user("team-smoke@example.com")["plan"] == "team"

        status, headers, body = _post_json_response(
            f"{live.base}/api/permit",
            {"job_type": "commercial office TI", "city": "Denver", "state": "CO", "job_category": "commercial"},
            {"X-Session-Token": session["token"], "X-Client-Fingerprint": "paid-smoke-fp"},
        )

    assert status == 200
    assert "X-Free-Lookups-Used" not in headers
    assert "X-Free-Lookups-Remaining" not in headers
    payload = json.loads(body)
    assert payload["remaining_lookups"] == -1


def test_beta_feedback_and_white_label_report_endpoints(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_session_token", lambda token: "beta@example.com" if token == "valid.test" else None)
    with _LiveServer(server.Handler) as live:
        unauthorized, body = _post_json(
            f"{live.base}/api/beta-feedback",
            {"email": "spoof@example.com", "useful": "yes"},
        )
        assert unauthorized == 401

        status, body = _post_json(
            f"{live.base}/api/beta-feedback",
            {
                "email": "spoof@example.com",
                "job_type": "office TI",
                "city": "Denver",
                "state": "CO",
                "useful": "yes",
                "knew_next_step": "yes",
                "missing": "fee detail",
                "ahj_confirmed": "not_yet",
                "use_again": "yes",
            },
            {"X-Session-Token": "valid.test"},
        )
        assert status == 200
        assert json.loads(body)["received"] is True

        status, headers, html = _post_json_response(
            f"{live.base}/api/white-label-report",
            {
                "contractor_name": "Boban Build Co",
                "client_name": "Test Client",
                "job_type": "office TI",
                "city": "Denver",
                "state": "CO",
                "result": {
                    "apply_url": "https://denvergov.org/permits",
                    "permits_required": [{"permit_type": "Building Permit — Tenant Improvement"}],
                    "claim_citations": [{
                        "id": "C1",
                        "claim": "Permit type",
                        "quoted_snippet": "Tenant finish permits are required.",
                        "source_url": "https://denvergov.org/permits",
                        "checked_at": "2026-05-01",
                        "confidence": "high",
                    }],
                },
            },
            {"X-Session-Token": "valid.test"},
        )
        assert status == 200
        assert headers["Content-Type"].startswith("text/html")
        assert "Boban Build Co" in html
        assert "Print / Save PDF" in html
        assert "Tenant finish permits are required." in html

    with sqlite3.connect(server.CACHE_DB) as conn:
        feedback_count = conn.execute("SELECT COUNT(*) FROM beta_feedback").fetchone()[0]
        email = conn.execute("SELECT email FROM beta_feedback").fetchone()[0]
        event_count = conn.execute("SELECT COUNT(*) FROM beta_events").fetchone()[0]
    assert feedback_count == 1
    assert email == "beta@example.com"
    assert event_count >= 1


def test_white_label_report_blocks_unsafe_urls(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_session_token", lambda token: "beta@example.com" if token == "valid.test" else None)
    with _LiveServer(server.Handler) as live:
        status, headers, html = _post_json_response(
            f"{live.base}/api/white-label-report",
            {
                "contractor_name": "<script>alert(1)</script>",
                "job_type": "office TI",
                "result": {
                    "apply_url": "javascript:alert(1)",
                    "permits_required": [{"permit_type": "Building Permit"}],
                    "claim_citations": [{
                        "id": "C1",
                        "claim": "Permit type",
                        "quoted_snippet": "Office TI permits are required.",
                        "source_url": "javascript:alert(2)",
                        "checked_at": "2026-05-01",
                        "confidence": "medium",
                    }],
                },
            },
            {"X-Session-Token": "valid.test"},
        )
    assert status == 200
    assert "javascript:" not in html
    assert "<script>" not in html
    assert "No safe source URL attached" in html


def test_quality_gate_does_not_clobber_ahj_specific_commercial_primary(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "confidence": "medium",
        "permits_required": [{"permit_type": "Tenant Finish Review"}],
        "sources": [{"url": "https://city.gov/tenant-finish", "snippet": "Tenant finish review is required."}],
    }
    gated = server.apply_permitiq_quality_gate(result, "office tenant finish", "Denver", "CO")
    assert gated["permits_required"][0]["permit_type"] == "Tenant Finish Review"
    assert any("AHJ-specific" in w for w in gated["quality_warnings"])


def test_city_watch_change_digest_contains_action_fields(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    responses = [
        {"permits_required": [{"permit_type": "Old Permit"}], "fee_range": "$100", "apply_url": "https://city.gov/old"},
        {"permits_required": [{"permit_type": "New Permit"}], "fee_range": "$200", "apply_url": "https://city.gov/new"},
    ]
    monkeypatch.setattr(server, "research_permit", lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(server, "resend_send", lambda *a, **k: True)

    server.create_city_watch("beta@example.com", "Austin", "TX", "restaurant TI")
    changed = server.check_city_changes("beta@example.com", "Austin", "TX", "restaurant TI")

    assert changed["watched"] is True
    assert changed["changed"] is True
    assert changed["digest"]["fee_range"] == "$200"
    assert changed["digest"]["required_permits"] == ["New Permit"]
