import json
import os
import sys
import threading
import types

import pytest


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


def test_customer_webhook_callback_urls_must_be_https_public_hosts(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    monkeypatch.setattr(
        server.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(None, None, None, None, ("93.184.216.34", port))],
    )
    assert server.validate_webhook_callback_url("https://hooks.example.com/permit") == "https://hooks.example.com/permit"

    for unsafe_url in [
        "http://hooks.example.com/permit",
        "https://localhost/permit",
        "https://127.0.0.1/permit",
        "https://10.0.0.5/permit",
        "https://172.16.0.2/permit",
        "https://192.168.1.2/permit",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::1]/permit",
        "https://user:pass@hooks.example.com/permit",
    ]:
        with pytest.raises(ValueError):
            server.validate_webhook_callback_url(unsafe_url)


def test_customer_webhook_delivery_uses_configured_url_not_payload_override_and_signs(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    sent = []
    delivered = threading.Event()

    monkeypatch.setattr(server, "research_permit", lambda *a, **k: {"permit_verdict": "YES"})
    monkeypatch.setattr(
        server.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(None, None, None, None, ("93.184.216.34", port))],
    )

    def fake_post(url, **kwargs):
        sent.append((url, kwargs))
        delivered.set()
        return object()

    monkeypatch.setattr(server.requests, "post", fake_post)

    integration = {
        "integration_key": "wh_test_customer_webhook_key",
        "name": "Customer CRM",
        "callback_url": "https://customer.example.com/permitassist",
        "field_mapping": {},
    }
    payload = {
        "job_type": "office TI",
        "city": "Phoenix",
        "state": "AZ",
        "callback_url": "https://attacker.example.com/steal",
    }

    server.run_webhook_lookup_async(integration, payload)

    assert delivered.wait(5), "webhook delivery thread did not call requests.post"
    assert sent, "expected one outbound webhook call"
    url, kwargs = sent[0]
    assert url == "https://customer.example.com/permitassist"
    assert url != payload["callback_url"]

    body = json.loads(kwargs.get("data") or "{}")
    body_json = kwargs.get("data") or ""
    headers = kwargs.get("headers") or {}
    assert body["ok"] is True
    assert headers["X-PermitAssist-Webhook-Id"].startswith("evt_")
    assert headers["X-PermitAssist-Webhook-Timestamp"].isdigit()
    assert headers["X-PermitAssist-Webhook-Signature"].startswith("sha256=")
    assert server.verify_customer_webhook_signature(
        integration["integration_key"],
        body_json,
        headers["X-PermitAssist-Webhook-Timestamp"],
        headers["X-PermitAssist-Webhook-Signature"],
    )


def test_create_webhook_integration_rejects_unsafe_callback_url_before_storage(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    with pytest.raises(ValueError):
        server.create_webhook_integration("user@example.com", "Bad", "http://localhost:8080/hook")

    rows = server.list_webhook_integrations("user@example.com")
    assert rows == []
