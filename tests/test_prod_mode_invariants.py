from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import server


def test_prod_mode_catastrophic_invariant_does_not_pass_through(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("PERMITASSIST_STRICT_CUSTOMER_INVARIANTS", raising=False)
    bad = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_name": "Building Permit",
        "permit_type": "Building Permit",
        "permits_required": [],
        "customer_headline": "Permit required: Building Permit.",
        "customer_next_step": "File the required permit package with the permit office.",
    }
    out = server.assert_customer_view_invariants(bad)
    assert not (out.get("permit_decision") == "REQUIRED" and not out.get("permits_required")), json.dumps(out, sort_keys=True, default=str)
    assert out != bad


def test_cosmetic_invariant_warning_is_internal_not_customer_visible(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("PERMITASSIST_STRICT_CUSTOMER_INVARIANTS", raising=False)
    cosmetic = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_name": "Multiple permits required: Building + Electrical",
        "permit_type": "Building Permit",
        "permits_required": [
            {"permit_type": "Building Permit", "permit_name": "Building Permit", "family": "building", "filing_family": "building", "status": "REQUIRED", "decision": "REQUIRED", "required": True}
        ],
        "customer_headline": "Permit required: multiple permits.",
        "customer_next_step": "File the required permit package with the permit office.",
    }
    out = server.assert_customer_view_invariants(cosmetic)
    assert not any("Customer-view invariant warning" in str(w) for w in out.get("warnings") or [])
    assert out.get("_customer_view_invariant_warnings")


def test_prod_mode_render_seal_fail_hard_uses_safe_fallback(monkeypatch) -> None:
    monkeypatch.setenv("PERMITASSIST_PROD_MODE", "1")

    def raise_on_prod_seal(payload, *, facts=None, fail_hard=None):
        if fail_hard:
            raise server.PacketInvariantError("synthetic prod seal failure")
        return payload

    monkeypatch.setattr(server, "apply_render_parity_seal", raise_on_prod_seal)
    bad = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "segment": "residential",
        "permit_kind": "Building",
        "permit_name": "Building Permit",
        "permit_type": "Building Permit",
        "permits_required": [{"permit_type": "Building Permit", "permit_name": "Building Permit", "family": "building", "filing_family": "building", "kind": "Building", "status": "REQUIRED", "decision": "REQUIRED", "required": True}],
        "related_permits": [],
        "customer_headline": "Permit required: Building Permit.",
        "customer_next_step": "File the required permit package with the permit office.",
        "_scope_contract": {"category": "residential"},
    }
    out = server.build_customer_permit_view_model(bad, "build small deck", "Phoenix", "AZ", "residential")
    assert out.get("permit_decision") == "REQUIRED"
    assert out.get("degraded_sources") is True
    assert out.get("_runtime_degraded_fallback")
