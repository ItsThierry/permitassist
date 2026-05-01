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
