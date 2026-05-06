import hashlib
import hmac
import importlib
import os
import sys


def _load_server(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from api import server
    return importlib.reload(server)


def _stripe_signature(payload: bytes, secret: str, timestamp: str = "12345") -> str:
    signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
    digest = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_stripe_signature_rejects_when_webhook_secret_missing(monkeypatch):
    server = _load_server(monkeypatch)

    assert server.verify_stripe_signature(b'{"id":"evt_test"}', "t=12345,v1=fake", "") is False
    assert server.verify_stripe_signature(b'{"id":"evt_test"}', "t=12345,v1=fake", None) is False


def test_stripe_signature_accepts_valid_signature_and_rejects_invalid(monkeypatch):
    server = _load_server(monkeypatch)
    payload = b'{"id":"evt_test"}'
    secret = "whsec_test_secret_not_real"

    assert server.verify_stripe_signature(payload, _stripe_signature(payload, secret), secret) is True
    assert server.verify_stripe_signature(payload, "t=12345,v1=bad", secret) is False
