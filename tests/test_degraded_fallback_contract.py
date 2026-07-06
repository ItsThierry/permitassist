from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import pytest

import server

TRADE_VERTICAL_SCOPES = [
    "tear-off reroof, replace shingles",
    "install rooftop solar panels",
    "install ductless mini split",
    "replace electrical panel 200A",
    "repipe bathroom plumbing",
    "replace water heater",
]


def _assert_degraded_safe(payload: dict) -> None:
    serialized = json.dumps(payload, sort_keys=True, default=str)
    assert payload.get("degraded_sources") is True
    assert payload.get("_runtime_degraded_fallback"), payload
    assert str(payload.get("permit_decision") or "").upper() != "NOT_REQUIRED"
    assert payload.get("permit_required") is not False
    assert "No permit indicated" not in serialized
    assert "No permit required" not in serialized
    rows = payload.get("permits_required") or []
    assert rows and all(str(row.get("decision") or row.get("status") or "").upper() == "REQUIRED" for row in rows)


@pytest.mark.parametrize("scope", TRADE_VERTICAL_SCOPES)
def test_degraded_fallback_never_confident_not_required(scope: str) -> None:
    _assert_degraded_safe(server._build_degraded_lookup_fallback(scope, "Phoenix", "AZ", reason="lookup_timeout"))


@pytest.mark.parametrize("scope", TRADE_VERTICAL_SCOPES)
def test_exception_budget_path_never_confident_not_required(monkeypatch: pytest.MonkeyPatch, scope: str) -> None:
    def boom(*args, **kwargs):
        raise ValueError("synthetic engine crash")

    monkeypatch.setattr(server, "research_permit", boom)
    _assert_degraded_safe(server._research_permit_with_budget(scope, "Phoenix", "AZ"))


@pytest.mark.parametrize("scope", TRADE_VERTICAL_SCOPES)
def test_timeout_budget_path_never_confident_not_required(monkeypatch: pytest.MonkeyPatch, scope: str) -> None:
    def slow(*args, **kwargs):
        time.sleep(0.05)
        return {"permit_decision": "NOT_REQUIRED"}

    monkeypatch.setattr(server, "PERMIT_LOOKUP_TOTAL_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(server, "research_permit", slow)
    _assert_degraded_safe(server._research_permit_with_budget(scope, "Phoenix", "AZ"))


def test_degraded_fallback_shape_passes_strict_invariants() -> None:
    payload = server._build_degraded_lookup_fallback("install rooftop solar panels", "Phoenix", "AZ", reason="lookup_timeout")
    projected = server.finalize_customer_public_projection(payload, "install rooftop solar panels", "Phoenix", "AZ", {})
    server.assert_customer_view_invariants(projected, soft=False)
