import json
import sqlite3
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


def test_feedback_endpoint_works_on_fresh_volume_before_any_lookup(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    db_path = tmp_path / "cache.db"

    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "permit_cache" in tables
    assert "feedback" in tables

    with _LiveServer(server.Handler) as live:
        status, body = _post_json(
            f"{live.base}/api/feedback",
            {
                "job_type": "office tenant improvement",
                "city": "Denver",
                "state": "CO",
                "issue": "batch4 smoke",
            },
        )

    assert status == 200
    assert json.loads(body)["received"] is True

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
    assert count == 1


def test_health_and_homepage_smoke_from_local_handler(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    with _LiveServer(server.Handler) as live:
        with urllib.request.urlopen(f"{live.base}/health", timeout=5) as resp:
            assert resp.status == 200
            assert json.loads(resp.read().decode())["status"] == "ok"
        with urllib.request.urlopen(f"{live.base}/", timeout=5) as resp:
            html = resp.read().decode("utf-8", "replace")
            assert resp.status == 200
            assert "PermitAssist" in html


def test_paid_permit_lookup_does_not_emit_free_lookup_headers(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    user_email = "paid@example.com"
    now = server.utc_now().isoformat()
    with sqlite3.connect(server.CACHE_DB) as conn:
        conn.execute(
            "INSERT INTO users (email, plan, created_at, last_login) VALUES (?,?,?,?)",
            (user_email, "solo", now, now),
        )
        conn.commit()
    token = server.create_session_token(user_email)
    server.research_permit = lambda *a, **k: {
        "permit_verdict": "YES",
        "confidence": "high",
        "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Office Interior Alteration"}],
        "sources": [],
    }

    with _LiveServer(server.Handler) as live:
        status, headers, body = _post_json_response(
            f"{live.base}/api/permit",
            {"job_type": "commercial office TI", "city": "Denver", "state": "CO", "job_category": "commercial"},
            {"X-Session-Token": token, "X-Client-Fingerprint": "paid-fp"},
        )

    assert status == 200
    assert "X-Free-Lookups-Used" not in headers
    assert "X-Free-Lookups-Remaining" not in headers
    assert json.loads(body)["remaining_lookups"] == -1


def test_feedback_telegram_message_escapes_user_supplied_html(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    messages = []
    monkeypatch.setattr(server, "notify_telegram", messages.append)

    with _LiveServer(server.Handler) as live:
        status, body = _post_json(
            f"{live.base}/api/feedback",
            {
                "job_type": "<b>RTU replacement</b>",
                "city": "Denver <script>",
                "state": "CO",
                "issue": "Wrong & unsafe <permit>",
            },
        )

    assert status == 200
    assert json.loads(body)["received"] is True
    assert messages
    msg = messages[0]
    assert "&lt;b&gt;RTU replacement&lt;/b&gt;" in msg
    assert "Denver &lt;script&gt;" in msg
    assert "Wrong &amp; unsafe &lt;permit&gt;" in msg
    assert "<script>" not in msg
