import json
import os
import sys
import threading
import types
import urllib.error
import urllib.request
from http.server import HTTPServer


def _install_server_import_stubs():
    requests_stub = types.ModuleType("requests")
    requests_stub.post = lambda *a, **k: None
    requests_stub.get = lambda *a, **k: None
    sys.modules.setdefault("requests", requests_stub)

    openai_stub = types.ModuleType("openai")
    openai_stub.OpenAI = lambda *a, **k: object()
    sys.modules.setdefault("openai", openai_stub)

    google_stub = types.ModuleType("google")
    genai_stub = types.ModuleType("google.generativeai")
    genai_stub.configure = lambda *a, **k: None
    sys.modules.setdefault("google", google_stub)
    sys.modules.setdefault("google.generativeai", genai_stub)

    research_stub = types.ModuleType("research_engine")
    research_stub.research_permit = lambda *a, **k: {"permit_verdict": "MAYBE"}
    research_stub.build_google_maps_url = lambda *a, **k: ""
    research_stub.strip_pdf_from_result = lambda result: result
    research_stub.get_cache_hit_rate = lambda: 0
    sys.modules.setdefault("research_engine", research_stub)


def _import_server(tmp_path, monkeypatch):
    _install_server_import_stubs()
    monkeypatch.setenv("FREE_LOOKUP_DB", str(tmp_path / "ip_lookups.db"))
    monkeypatch.setenv("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from api import server
    server.CACHE_DB = str(tmp_path / "cache.db")
    server.DATA_DIR = str(tmp_path)
    server.init_db()
    return server


class _LiveServer:
    def __init__(self, handler):
        self.httpd = HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.thread.join(timeout=5)


def _post_json(url, body=None, headers=None):
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def test_background_admin_endpoints_reject_when_admin_token_not_configured(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    server.ADMIN_TOKEN = ""
    server.process_onboarding_emails = lambda: 7
    server.process_permit_issued_reminders = lambda: 9

    with _LiveServer(server.Handler) as live:
        for path in ("/api/process-onboarding-emails", "/api/check-permit-reminders"):
            status, body = _post_json(f"{live.base}{path}", headers={"X-Admin-Token": "anything"})
            assert status == 401, (path, status, body)
            assert "Admin token required" in body


def test_background_admin_endpoints_require_matching_admin_token(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    server.ADMIN_TOKEN = "configured-test-admin-token"
    server.process_onboarding_emails = lambda: 7
    server.process_permit_issued_reminders = lambda: 9

    with _LiveServer(server.Handler) as live:
        for path in ("/api/process-onboarding-emails", "/api/check-permit-reminders"):
            wrong_status, wrong_body = _post_json(f"{live.base}{path}", headers={"X-Admin-Token": "wrong"})
            assert wrong_status == 401, (path, wrong_status, wrong_body)

        ok_status, ok_body = _post_json(
            f"{live.base}/api/process-onboarding-emails",
            headers={"X-Admin-Token": "configured-test-admin-token"},
        )
        assert ok_status == 200
        assert json.loads(ok_body)["sent"] == 7

        ok_status, ok_body = _post_json(
            f"{live.base}/api/check-permit-reminders",
            headers={"X-Admin-Token": "configured-test-admin-token"},
        )
        assert ok_status == 200
        assert json.loads(ok_body)["sent"] == 9
