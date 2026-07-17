"""Regressions for outer lookup-budget versus authoritative Decision Cell truth."""
from __future__ import annotations

import json
import time


CROOK_ARGS = (
    "commercial tenant improvement",
    "Crook County",
    "WY",
    "",
)


def _import_server():
    from api import server

    return server


def _force_legacy_budget_path(monkeypatch, server) -> None:
    monkeypatch.setenv("PERMITASSIST_RULE_ENGINE_CORE", "off")
    monkeypatch.delenv("PERMITASSIST_RULE_ENGINE_CORE_ALLOWLIST", raising=False)
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    monkeypatch.setattr(server, "build_active_core_first_result", lambda **_kwargs: None)


def _assert_crook_authority_lock(result: dict) -> None:
    assert result["permit_decision"] == "NOT_REQUIRED"
    assert result["permit_required"] is False
    assert result["permit_verdict"] == "NO"
    assert result["permit_name"] == "No permit required"
    assert result["permits_required"] == []
    lock = result.get("_decision_cell_primary_lock")
    assert isinstance(lock, dict)
    assert lock["source"] == "permitassist_v24_decision_cell"
    assert lock["permit_decision"] == "NOT_REQUIRED"
    assert lock["permit_required"] is False
    assert lock["permit_name"] == "No permit required"
    assert lock["source_urls"]
    assert lock["sources"]


def test_timeout_recovers_locked_crook_authority_before_heuristic(monkeypatch):
    server = _import_server()
    _force_legacy_budget_path(monkeypatch, server)
    monkeypatch.setattr(server, "PERMIT_LOOKUP_TOTAL_TIMEOUT_SECONDS", 0.01)

    def slow_conflicting_model(*_args, **_kwargs):
        time.sleep(0.10)
        return {
            "permit_decision": "REQUIRED",
            "permit_required": True,
            "permit_verdict": "YES",
            "permit_name": "Conflicting AI filing",
            "permits_required": [{"permit_type": "Conflicting AI filing", "required": True}],
        }

    monkeypatch.setattr(server, "research_permit", slow_conflicting_model)
    result = server._research_permit_with_budget(
        *CROOK_ARGS,
        job_category="commercial",
        use_cache=False,
    )

    _assert_crook_authority_lock(result)
    assert result["_runtime_authority_recovery"]["reason"] == "lookup_timeout"
    assert "_runtime_degraded_fallback" not in result


def test_worker_exception_recovers_locked_crook_authority(monkeypatch):
    server = _import_server()
    _force_legacy_budget_path(monkeypatch, server)

    def failing_model(*_args, **_kwargs):
        raise RuntimeError("synthetic model/search failure")

    monkeypatch.setattr(server, "research_permit", failing_model)
    result = server._research_permit_with_budget(
        *CROOK_ARGS,
        job_category="commercial",
        use_cache=False,
    )

    _assert_crook_authority_lock(result)
    assert result["_runtime_authority_recovery"]["reason"] == "RuntimeError"
    assert "_runtime_degraded_fallback" not in result


def test_cache_bypass_pre_resolves_authoritative_not_required_without_model_race(monkeypatch):
    """Exact NOT_REQUIRED cells must not race a non-cached AI lookup."""
    server = _import_server()
    _force_legacy_budget_path(monkeypatch, server)
    calls = []

    def model_must_not_run(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("authoritative NOT_REQUIRED bypass must pre-resolve")

    monkeypatch.setattr(server, "research_permit", model_must_not_run)
    result = server._research_permit_with_budget(
        *CROOK_ARGS,
        job_category="commercial",
        use_cache=False,
        suppress_cache_write=True,
    )
    repeated = server._research_permit_with_budget(
        *CROOK_ARGS,
        job_category="commercial",
        use_cache=False,
        suppress_cache_write=True,
    )

    _assert_crook_authority_lock(result)
    assert repeated == result
    assert calls == []
    assert result["_runtime_authority_recovery"]["reason"] == (
        "cache_bypass_authoritative_not_required"
    )


def test_cache_bypass_does_not_preempt_required_cell_rich_worker_result(monkeypatch):
    """The deterministic shortcut must not neuter REQUIRED filing packets."""
    server = _import_server()
    _force_legacy_budget_path(monkeypatch, server)
    sentinel = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_name": "Rich successful required result",
        "permits_required": [
            {"permit_type": "Rich successful required result", "required": True}
        ],
        "documents_needed": ["Complete construction plans"],
    }
    calls = []

    def successful_model(*args, **kwargs):
        calls.append((args, kwargs))
        return sentinel

    monkeypatch.setattr(server, "research_permit", successful_model)
    result = server._research_permit_with_budget(
        "commercial office tenant improvement",
        "Anchorage",
        "AK",
        "",
        job_category="commercial",
        use_cache=False,
        suppress_cache_write=True,
    )

    assert len(calls) == 1
    assert result is sentinel
    assert result["documents_needed"] == ["Complete construction plans"]


def test_required_exact_cell_stays_locked_on_timeout(monkeypatch):
    server = _import_server()
    _force_legacy_budget_path(monkeypatch, server)
    monkeypatch.setattr(server, "PERMIT_LOOKUP_TOTAL_TIMEOUT_SECONDS", 0.01)

    def slow_conflicting_model(*_args, **_kwargs):
        time.sleep(0.10)
        return {"permit_decision": "NOT_REQUIRED", "permit_required": False}

    monkeypatch.setattr(server, "research_permit", slow_conflicting_model)
    result = server._research_permit_with_budget(
        "commercial office tenant improvement",
        "Anchorage",
        "AK",
        "",
        job_category="commercial",
        use_cache=False,
    )

    assert result["permit_decision"] == "REQUIRED"
    assert result["permit_required"] is True
    assert result["permit_verdict"] == "YES"
    assert result["permits_required"]
    lock = result["_decision_cell_primary_lock"]
    assert lock["source"] == "permitassist_v24_decision_cell"
    assert lock["permit_decision"] == "REQUIRED"
    assert lock["permit_required"] is True
    assert result["_runtime_authority_recovery"]["reason"] == "lookup_timeout"
    assert "_runtime_degraded_fallback" not in result


def test_crook_timeout_customer_surface_preserves_not_required_without_internal_leaks(monkeypatch):
    server = _import_server()
    _force_legacy_budget_path(monkeypatch, server)
    monkeypatch.setattr(server, "PERMIT_LOOKUP_TOTAL_TIMEOUT_SECONDS", 0.01)

    def slow_conflicting_model(*_args, **_kwargs):
        time.sleep(0.10)
        return {
            "permit_decision": "REQUIRED",
            "permit_required": True,
            "permit_verdict": "YES",
            "permit_name": "Commercial Building / Tenant Improvement Permit",
            "permits_required": [
                {"permit_type": "Commercial Building / Tenant Improvement Permit", "required": True}
            ],
        }

    monkeypatch.setattr(server, "research_permit", slow_conflicting_model)
    monkeypatch.setattr(server, "sanitize_result_urls", lambda result: result)
    raw = server._research_permit_with_budget(
        *CROOK_ARGS,
        job_category="commercial",
        use_cache=False,
    )
    finalized = server.finalize_permit_lookup_result(
        server._mark_server_owned_result(raw),
        CROOK_ARGS[0],
        CROOK_ARGS[1],
        CROOK_ARGS[2],
        job_category="commercial",
        evidence_allowed=False,
    )
    public = server.build_customer_response_egress(
        finalized,
        CROOK_ARGS[0],
        CROOK_ARGS[1],
        CROOK_ARGS[2],
        job_category="commercial",
    )

    assert public["permit_decision"] == "NOT_REQUIRED"
    assert public["permit_required"] is False
    assert public["permit_verdict"] == "NO"
    assert public["permit_name"] == "No permit required"
    assert public["permits_required"] == []
    assert public["permits_required_logic"] == []
    assert public["customer_result_summary"]["permit_decision"] == "NOT_REQUIRED"
    assert public["customer_first_screen_summary"]["decision"] == "NOT_REQUIRED"
    related = {
        str(row.get("family") or ""): row
        for row in public.get("related_permits", [])
        if isinstance(row, dict)
    }
    assert related["planning"]["decision"] == "CONDITIONAL"
    assert related["co"]["decision"] == "CONDITIONAL"
    assert public["source_support"]["has_official_source"] is True
    assert public["source_support"]["decision_mutation_allowed"] is False
    assert public["apply_path"].get("documents_to_prepare", []) == []
    assert public["apply_path"].get("portal_selection_path", []) == []
    assert public["apply_path"].get("steps", []) == []

    rendered = json.dumps(public, sort_keys=True).lower()
    assert "commercial building / tenant improvement permit" not in rendered
    assert "file the required permit" not in rendered
    assert "timeout fallback" not in rendered
    assert "_runtime_authority_recovery" not in rendered
    assert "_decision_cell_primary_lock" not in rendered
    assert "permitassist_v24" not in rendered
    assert "/home/boban" not in rendered
    assert "crookcounty.wy.gov" in rendered


def test_uncovered_timeout_retains_ordinary_degraded_control(monkeypatch):
    server = _import_server()
    _force_legacy_budget_path(monkeypatch, server)
    monkeypatch.setattr(server, "PERMIT_LOOKUP_TOTAL_TIMEOUT_SECONDS", 0.01)
    assert server.resolve_authoritative_decision_cell_fallback(
        "commercial tenant improvement",
        "Not A Real Covered City",
        "TX",
        job_category="commercial",
    ) is None

    def slow_model(*_args, **_kwargs):
        time.sleep(0.10)
        return {"permit_decision": "NOT_REQUIRED", "permit_required": False}

    monkeypatch.setattr(server, "research_permit", slow_model)
    result = server._research_permit_with_budget(
        "commercial tenant improvement",
        "Not A Real Covered City",
        "TX",
        "",
        job_category="commercial",
        use_cache=False,
    )

    assert result["permit_decision"] == "REQUIRED"
    assert result["permit_required"] is True
    assert result["_runtime_degraded_fallback"]["reason"] == "lookup_timeout"
    assert "_runtime_authority_recovery" not in result
    assert "_decision_cell_primary_lock" not in result


def test_authority_resolver_failure_falls_back_without_exception_detail_leak(monkeypatch, capsys):
    server = _import_server()

    def broken_resolver(*_args, **_kwargs):
        raise RuntimeError("secret-token-must-not-enter-logs")

    monkeypatch.setattr(server, "resolve_authoritative_decision_cell_fallback", broken_resolver)
    result = server._build_authority_preserving_lookup_fallback(
        "commercial tenant improvement",
        "Not A Real Covered City",
        "TX",
        job_category="commercial",
        reason="lookup_timeout",
    )

    assert result["permit_decision"] == "REQUIRED"
    assert result["_runtime_degraded_fallback"]["reason"] == "lookup_timeout"
    output = capsys.readouterr().out
    assert "authority-fallback-error" in output
    assert "RuntimeError" in output
    assert "secret-token-must-not-enter-logs" not in output


def test_successful_worker_result_is_unchanged(monkeypatch):
    server = _import_server()
    _force_legacy_budget_path(monkeypatch, server)
    sentinel = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_name": "Successful legacy result",
        "permits_required": [{"permit_type": "Successful legacy result", "required": True}],
    }
    monkeypatch.setattr(server, "research_permit", lambda *_args, **_kwargs: sentinel)

    result = server._research_permit_with_budget(
        "commercial tenant improvement",
        "Not A Real Covered City",
        "TX",
        "",
        job_category="commercial",
        use_cache=False,
    )

    assert result is sentinel
    assert "_runtime_authority_recovery" not in result
    assert "_runtime_degraded_fallback" not in result
