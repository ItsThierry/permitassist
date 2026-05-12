import json
import os
import sys
from importlib import util
from pathlib import Path

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper_phase7d_safe_registry_startup",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_import_server = _debug_helper._import_server

REPO_ROOT = Path(__file__).resolve().parents[1]
PACK_CONTROLLED_FIELDS = {
    "apply_url",
    "approval_timeline",
    "companion_reviews_triggers",
    "fee_range",
    "inspections",
    "permit_type",
}


def _purge_evidence_pack_modules():
    for name in (
        "api.server",
        "server",
        "api.evidence_pack_runtime",
        "evidence_pack_runtime",
    ):
        sys.modules.pop(name, None)
    api_pkg = sys.modules.get("api")
    if api_pkg is not None:
        for attr in ("server", "evidence_pack_runtime"):
            if hasattr(api_pkg, attr):
                delattr(api_pkg, attr)


def _import_server_fresh(tmp_path, monkeypatch):
    _purge_evidence_pack_modules()
    try:
        return _import_server(tmp_path, monkeypatch)
    finally:
        # Avoid leaking import-time registry constants into later tests after
        # monkeypatch restores env vars.
        _purge_evidence_pack_modules()


def _enable_phase7b_env(monkeypatch, registry_path: Path):
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_REGISTRY_PATH", str(registry_path))
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_ENABLED", "true")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_MODE", "phase7b_golden_local_preview")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PREVIEW_ONLY", "false")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PATH", str(registry_path.parent / "missing-pack.json"))
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_EXPECTED_FINGERPRINT", "0" * 64)


def test_phase7d_registry_is_code_owned_and_survives_runtime_data_shadow(tmp_path, monkeypatch):
    """Startup-critical registry config must not live under Railway's mutable data volume."""
    server = _import_server(tmp_path, monkeypatch)
    from api import evidence_pack_runtime as runtime

    registry_path = runtime.DEFAULT_EVIDENCE_PACK_REGISTRY_PATH
    relative_parts = registry_path.relative_to(REPO_ROOT).parts

    assert server.evidence_pack_enabled() is False
    assert registry_path.exists()
    assert "data" not in relative_parts
    assert "phase7b_golden_local_preview" in runtime.ALLOWED_EVIDENCE_PACK_MODES


def test_phase7d_missing_registry_imports_but_request_gate_fails_closed(tmp_path, monkeypatch):
    missing_registry = tmp_path / "missing-registry.json"
    _enable_phase7b_env(monkeypatch, missing_registry)

    server = _import_server_fresh(tmp_path, monkeypatch)

    assert server.evidence_pack_enabled() is True
    assert server.evidence_pack_allowed_for_request(
        "/api/permit",
        {"X-Sample-Demo": "1", "X-Phase7B-Golden-Preview-Token": "token"},
        is_sample_demo=True,
    ) is False


def _assert_pack_controlled_fields_suppressed(result: dict):
    evidence = result["_evidence_pack"]
    assert evidence["contract_status"] == "invalid_contract"
    assert set(evidence["failed_closed_fields"]) == PACK_CONTROLLED_FIELDS
    assert result["permits_required"] == []
    assert result["apply_url"] is None
    assert result["fee_range"] is None
    assert result["approval_timeline"] is None
    assert result["inspections"] is None
    assert result["companion_reviews_triggers"] is None
    assert result["claim_citations"] == []


def _pack_controlled_engine_result() -> dict:
    return {
        "permit_type": "Commercial Building Permit",
        "permits_required": ["Commercial Building Permit"],
        "apply_url": "https://example.gov/apply",
        "fee_range": "$100-$200",
        "approval_timeline": "2 weeks",
        "inspections": ["building"],
        "companion_reviews_triggers": ["fire"],
        "claim_citations": [{"field": "apply_url"}],
    }


def test_phase7d_missing_registry_suppresses_pack_controlled_fields(tmp_path, monkeypatch):
    missing_registry = tmp_path / "missing-registry.json"
    _enable_phase7b_env(monkeypatch, missing_registry)

    server = _import_server_fresh(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda *_args, **_kwargs: True)
    result = server.finalize_permit_lookup_result(
        _pack_controlled_engine_result(),
        "restaurant tenant improvement",
        "Los Angeles",
        "CA",
    )

    _assert_pack_controlled_fields_suppressed(result)


def test_phase7d_missing_registry_route_gate_denial_still_fails_closed(tmp_path, monkeypatch):
    missing_registry = tmp_path / "missing-registry.json"
    _enable_phase7b_env(monkeypatch, missing_registry)

    server = _import_server_fresh(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda *_args, **_kwargs: True)
    assert server.evidence_pack_allowed_for_request(
        "/api/permit",
        {"X-Sample-Demo": "1", "X-Phase7B-Golden-Preview-Token": "token"},
        is_sample_demo=True,
    ) is False

    result = server.finalize_permit_lookup_result(
        _pack_controlled_engine_result(),
        "restaurant tenant improvement",
        "Los Angeles",
        "CA",
        evidence_allowed=False,
    )

    _assert_pack_controlled_fields_suppressed(result)


def test_phase7d_malformed_registry_with_correct_schema_imports_fail_closed(tmp_path, monkeypatch):
    malformed_registry = tmp_path / "malformed-registry.json"
    malformed_registry.write_text(
        json.dumps(
            {
                "schema": "permitassist.evidence_pack.registry.v1",
                "supported_fields": "apply_url",
                "pack_versions": ["phase7b_golden_local_preview_v1"],
                "modes": ["phase7b_golden_local_preview"],
            }
        ),
        encoding="utf-8",
    )
    _enable_phase7b_env(monkeypatch, malformed_registry)

    server = _import_server_fresh(tmp_path, monkeypatch)

    assert server.evidence_pack_allowed_for_request(
        "/api/permit",
        {"X-Sample-Demo": "1", "X-Phase7B-Golden-Preview-Token": "token"},
        is_sample_demo=True,
    ) is False


def test_phase7d_protected_mode_with_malformed_activation_fails_request_gate(tmp_path, monkeypatch):
    malformed_registry = tmp_path / "malformed-activation-registry.json"
    malformed_registry.write_text(
        json.dumps(
            {
                "schema": "permitassist.evidence_pack.registry.v1",
                "supported_fields": ["apply_url", "permit_type"],
                "pack_versions": {"phase7b_golden_local_preview_v1": {"production_wiring_allowed": False}},
                "modes": {
                    "phase7b_golden_local_preview": {
                        "expected_version": "phase7b_golden_local_preview_v1",
                        "activation": ["not", "a", "dict"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    _enable_phase7b_env(monkeypatch, malformed_registry)

    server = _import_server_fresh(tmp_path, monkeypatch)

    assert server.evidence_pack_allowed_for_request(
        "/api/permit",
        {"X-Sample-Demo": "1", "X-Phase7B-Golden-Preview-Token": "token"},
        is_sample_demo=True,
    ) is False


def test_phase7d_malformed_nested_promotion_imports_fail_closed(tmp_path, monkeypatch):
    malformed_registry = tmp_path / "malformed-promotion-registry.json"
    malformed_registry.write_text(
        json.dumps(
            {
                "schema": "permitassist.evidence_pack.registry.v1",
                "supported_fields": ["apply_url", "permit_type"],
                "pack_versions": {"step7b_offline_v1": {"production_wiring_allowed": False}},
                "modes": {
                    "solar_mep_controlled_preview": {
                        "expected_version": "step7b_offline_v1",
                        "activation": ["not", "a", "dict"],
                        "promotion": {
                            "metadata_equals": ["fingerprint_sha256"],
                            "record_field_exact": ["pack_family"],
                            "record_count": "not-an-int",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_REGISTRY_PATH", str(malformed_registry))
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_ENABLED", "true")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_MODE", "solar_mep_controlled_preview")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PREVIEW_ONLY", "false")

    server = _import_server_fresh(tmp_path, monkeypatch)

    assert server.evidence_pack_allowed_for_request(
        "/api/permit",
        {"X-Sample-Demo": "1", "X-Evidence-Pack-Preview-Token": "token"},
        is_sample_demo=True,
    ) is False
