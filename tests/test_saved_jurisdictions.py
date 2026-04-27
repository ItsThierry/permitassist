import json
import os
import sys
import threading
import types
import urllib.error
import urllib.parse
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
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
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
        self.httpd.server_close()


def _request(method, url, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode("utf-8"))


def test_saved_jurisdictions_crud_lookup_and_cross_user_isolation(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    owner = "owner@example.com"
    other = "other@example.com"

    with _LiveServer(server.Handler) as live:
        saved = []
        for city, state, trade in [
            ("Houston", "tx", "HVAC"),
            ("Austin", "TX", "Electrical"),
            ("Phoenix", "AZ", "Roofing"),
        ]:
            status, body = _request("POST", live.base + "/api/jurisdictions/save", {
                "email": owner,
                "city": city,
                "state": state,
                "trade": trade,
                "display_name": f"{city} {trade} route",
            })
            assert status == 200
            assert body["jurisdiction"]["email"] == owner
            assert body["jurisdiction"]["state"] == state.upper()
            saved.append(body["jurisdiction"])

        status, body = _request("GET", live.base + "/api/jurisdictions/list?" + urllib.parse.urlencode({"email": owner}))
        assert status == 200
        assert len(body["jurisdictions"]) == 3
        assert {j["city"] for j in body["jurisdictions"]} == {"Houston", "Austin", "Phoenix"}

        lookup_id = saved[0]["id"]
        status, body = _request("POST", f"{live.base}/api/jurisdictions/{lookup_id}/lookup", {"email": owner})
        assert status == 200
        assert body["jurisdiction"]["lookup_count"] == 1
        assert body["jurisdiction"]["last_lookup_at"]

        status, body = _request("POST", f"{live.base}/api/jurisdictions/{lookup_id}/lookup", {"email": other})
        assert status == 403
        assert body["error"] == "Forbidden"

        status, body = _request("DELETE", f"{live.base}/api/jurisdictions/{lookup_id}?" + urllib.parse.urlencode({"email": other}))
        assert status == 403
        assert body["error"] == "Forbidden"

        delete_id = saved[1]["id"]
        status, body = _request("DELETE", f"{live.base}/api/jurisdictions/{delete_id}?" + urllib.parse.urlencode({"email": owner}))
        assert status == 200
        assert body["deleted"] is True

        status, body = _request("GET", live.base + "/api/jurisdictions/list?" + urllib.parse.urlencode({"email": owner}))
        assert status == 200
        assert len(body["jurisdictions"]) == 2

        status, body = _request("GET", live.base + "/api/jurisdictions/list?" + urllib.parse.urlencode({"email": other}))
        assert status == 200
        assert body["jurisdictions"] == []
