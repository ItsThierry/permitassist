import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

import server
from launch_coverage import SupportOutcome, load_coverage_registry, resolve_precharge_support
from lookup_execution_ledger import LookupExecutionLedger

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "api" / "launch_coverage_registry.json"


@pytest.mark.parametrize(
    "contract", load_coverage_registry(REGISTRY).contracts, ids=lambda c: c["contract_id"]
)
def test_terminal_manifest_is_binary_or_conditional_and_projection_idempotent(contract):
    customer = server.build_launch_coverage_customer_result(contract)
    assert customer["permit_decision"] == contract["decision"]
    assert customer["permit_decision"] != "VERIFY"
    assert isinstance(customer.get("permit_manifest"), dict)
    assert customer["permit_manifest"]["schema_version"] == "permit_manifest_v1"
    assert server.project_customer_response_egress(customer) == customer
    blob = json.dumps(customer, sort_keys=True).lower()
    for banned in ("decision cell", "scope_signal_only", "server-held", "integrity_fail_closed"):
        assert banned not in blob


def test_unsupported_resolution_has_no_customer_report_or_model_permission():
    result = resolve_precharge_support(
        job_type="Unknown unsupported scope",
        city="Nowhere",
        state="ZZ",
        segment="commercial",
        registry_path=REGISTRY,
    )
    assert result.outcome is SupportOutcome.UNSUPPORTED
    assert result.customer_report is None
    assert result.model_call_allowed is False
    assert result.retained_charge is False


def test_execution_ledger_persists_distinct_request_id_and_execution_token(tmp_path):
    db = tmp_path / "ledger.db"
    ledger = LookupExecutionLedger(db)
    body = {"job_type": "x", "city": "y", "state": "ZZ"}
    fingerprint = ledger.request_fingerprint(body)
    claim = ledger.claim("idem-1", fingerprint, owner_scope="user:test", request_id="request-123")
    assert claim.action == "execute"
    ledger.complete(
        "idem-1",
        fingerprint,
        {"coverage_outcome": "UNSUPPORTED", "report_created": False},
        owner_scope="user:test",
        request_id="request-123",
        execution_token=claim.execution_token,
        http_status=422,
    )
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT request_id, execution_token, http_status FROM served_decisions WHERE owner_scope=? AND idempotency_key=?",
            ("user:test", "idem-1"),
        ).fetchone()
    assert row == ("request-123", claim.execution_token, 422)
    replay = ledger.claim("idem-1", fingerprint, owner_scope="user:test", request_id="request-456")
    assert replay.action == "replay"
    assert replay.http_status == 422


def test_paid_route_requires_idempotency_and_support_gate_precedes_research_static():
    source = Path(server.__file__).read_text(encoding="utf-8")
    assert "launch_coverage_contracts_active()" in source
    assert '"error": "idempotency_key_required"' in source
    support_index = source.index("support_resolution = resolve_precharge_support(", source.index('if path == "/api/permit":'))
    research_index = source.index("result = _research_permit_with_budget(", support_index)
    assert support_index < research_index
    assert '"report_created": False' in source[support_index:research_index]
    assert '"retained_charge": False' in source[support_index:research_index]


def test_launch_gate_is_default_on_but_legacy_pytest_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("PERMITASSIST_LAUNCH_COVERAGE_MODE", "off")
    assert server.launch_coverage_contracts_active() is True
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "legacy::test")
    assert server.launch_coverage_contracts_active() is False
    monkeypatch.setenv("PERMITASSIST_LAUNCH_COVERAGE_MODE", "enforce")
    assert server.launch_coverage_contracts_active() is True


def test_paid_api_and_webhook_runtime_paths_are_sealed_and_idempotent_static():
    assert server.__file__ is not None
    source = Path(server.__file__).read_text(encoding="utf-8")
    api_v1 = source.split('elif path == "/api/v1/permit":', 1)[1].split('elif path == "/api/email-report":', 1)[0]
    assert 'self.headers.get("Idempotency-Key"' in api_v1
    assert "support_resolution = resolve_precharge_support(" in api_v1
    assert "build_launch_coverage_customer_result(" in api_v1
    assert api_v1.index("support_resolution = resolve_precharge_support(") < api_v1.index("research_permit(")
    webhook_route = source.split('elif path.startswith("/api/integrations/webhook/"):', 1)[1].split('elif path == "/api/v1/permit":', 1)[0]
    assert 'self.headers.get("Idempotency-Key"' in webhook_route
    assert "run_webhook_lookup_async(integration, data, idempotency_key)" in webhook_route
    active_webhook = source.split('    mapping = integration.get("field_mapping") or {}', 2)[2].split("def send_email_report", 1)[0]
    assert "resolve_precharge_support(" in active_webhook
    assert "build_launch_coverage_customer_result(" in active_webhook
    assert "research_permit(" not in active_webhook


def test_webhook_runtime_claims_once_and_replays_without_second_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("PERMITASSIST_LAUNCH_COVERAGE_MODE", "enforce")
    ledger = LookupExecutionLedger(tmp_path / "webhook-ledger.db")
    monkeypatch.setattr(server, "_lookup_execution_ledger", lambda: ledger)
    monkeypatch.setattr(server, "validate_webhook_callback_url", lambda value: value)
    monkeypatch.setattr(server, "mark_webhook_triggered", lambda key: None)
    delivered = threading.Event()
    posts = []

    class Response:
        def raise_for_status(self):
            return None

    def fake_post(url, **kwargs):
        posts.append((url, kwargs))
        delivered.set()
        return Response()

    monkeypatch.setattr(server.requests, "post", fake_post)
    contract = load_coverage_registry(REGISTRY).contracts[4]
    payload = {
        "job_type": contract["job_type"],
        "city": contract["city"],
        "state": contract["state"],
        "zip_code": contract["zip_code"],
        "job_category": contract["segment"],
    }
    integration = {
        "integration_key": "webhook-proof",
        "name": "Proof webhook",
        "callback_url": "https://customer.example.test/hook",
        "field_mapping": {},
    }
    first = server.run_webhook_lookup_async(integration, payload, "webhook-idem-1")
    assert first["http_status"] == 202
    assert delivered.wait(5)
    replay = None
    for _ in range(100):
        replay = server.run_webhook_lookup_async(integration, payload, "webhook-idem-1")
        if replay["http_status"] == 200:
            break
        time.sleep(0.01)
    assert replay and replay["http_status"] == 200 and replay["idempotent_replay"] is True
    assert len(posts) == 1
    callback = json.loads(posts[0][1]["data"])
    assert callback["result"]["permit_decision"] == contract["decision"]
    assert callback["result"]["permit_decision"] != "VERIFY"
    conflict = server.run_webhook_lookup_async(
        integration, {**payload, "zip_code": "99999"}, "webhook-idem-1"
    )
    assert conflict["http_status"] == 409


def test_all_customer_entry_pages_send_idempotency_key():
    pages = [ROOT / "frontend" / "index.html", ROOT / "frontend" / "preview-modern-reskinned-index.html"]
    pages.extend(sorted((ROOT / "frontend" / "trades").glob("*.html")))
    assert pages
    for page in pages:
        source = page.read_text(encoding="utf-8")
        assert "function getPermitLookupHeaders(payload)" in source, page
        assert "h['Idempotency-Key']=key" in source, page
        assert "headers: getPermitLookupHeaders(permitPayload)" in source, page
