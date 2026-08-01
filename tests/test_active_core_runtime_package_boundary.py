"""Regression contracts for active-core runtime package compatibility.

An active, allowlisted request must never silently enter legacy research when
its v2.4 package cannot be authenticated.  Flag-off and unallowlisted traffic
keep their pre-core behavior.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from importlib import util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "api"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from api import permit_rule_engine as pre  # noqa: E402
from api import server  # noqa: E402
from api import v24_decision_cells as v24  # noqa: E402

_server_pre = sys.modules[server.build_active_core_first_result.__module__]

MANIFEST = ROOT / "knowledge" / "v24" / "permitassist_v24_manifest.json"
MANIFEST_SHA256 = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
STALE_MANIFEST_SHA256 = "0" * 64

_HELPER_SPEC = util.spec_from_file_location(
    "active_core_http_helper",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
_http_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_http_helper)
_LiveServer = _http_helper._LiveServer
_post_json = _http_helper._post_json


def _reset_v24_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = {
        "_V24_INDEX_CACHE": {},
        "_V24_INDEX_CACHE_SOURCE": None,
        "_V24_INDEX_CACHE_MTIME_NS": None,
        "_V24_INDEX_CACHE_MANIFEST_SOURCE": None,
        "_V24_INDEX_CACHE_MANIFEST_MTIME_NS": None,
        "_V24_INDEX_CACHE_EXPECTED_MANIFEST_SHA256": None,
        "_V24_INDEX_CACHE_LOAD_FAILED": False,
    }
    for name, value in defaults.items():
        monkeypatch.setattr(v24, name, value, raising=False)


def _activate_buckeye(monkeypatch: pytest.MonkeyPatch, manifest_sha256: str) -> None:
    monkeypatch.setenv(pre.CORE_SETTING, "active")
    monkeypatch.setenv(pre.CORE_ALLOWLIST_SETTING, "us-az-buckeye")
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    monkeypatch.setenv("PERMITASSIST_V24_MANIFEST_SHA256", manifest_sha256)
    _reset_v24_cache(monkeypatch)


def test_v24_manifest_pin_participates_in_cache_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    _activate_buckeye(monkeypatch, MANIFEST_SHA256)
    assert v24.load_v24_index() is not None

    # A runtime pin change must invalidate a previously successful cache entry.
    monkeypatch.setenv("PERMITASSIST_V24_MANIFEST_SHA256", STALE_MANIFEST_SHA256)
    assert v24.load_v24_index() is None

    # A corrected pin must recover without a process restart.
    monkeypatch.setenv("PERMITASSIST_V24_MANIFEST_SHA256", MANIFEST_SHA256)
    assert v24.load_v24_index() is not None


def test_active_allowlisted_pin_mismatch_never_calls_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    _activate_buckeye(monkeypatch, STALE_MANIFEST_SHA256)
    legacy_calls = 0

    def forbidden_legacy(*_args, **_kwargs):
        nonlocal legacy_calls
        legacy_calls += 1
        raise AssertionError("active-core package failure must not enter legacy research")

    monkeypatch.setattr(server, "research_permit", forbidden_legacy)
    error_type = getattr(server, "ActiveCorePackageUnavailableError", None)
    assert error_type is not None, "active-core package failure needs a typed fail-closed error"

    with pytest.raises(error_type) as raised:
        server._research_permit_with_budget(
            "residential reroof",
            "Buckeye",
            "AZ",
            job_category="residential",
        )

    assert getattr(raised.value, "code", "") == "active_core_package_unavailable"
    assert legacy_calls == 0


def test_unallowlisted_request_preserves_legacy_path_when_package_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _activate_buckeye(monkeypatch, STALE_MANIFEST_SHA256)
    marker = {"legacy_path": True}
    monkeypatch.setattr(server, "research_permit", lambda *_args, **_kwargs: marker)

    assert (
        server._research_permit_with_budget(
            "commercial tenant improvement",
            "Denver",
            "CO",
            job_category="commercial",
        )
        is marker
    )


def test_allowlisted_uncovered_project_returns_sealed_fail_closed_not_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_v24_cache(monkeypatch)
    _activate_buckeye(monkeypatch, MANIFEST_SHA256)
    legacy_calls = 0

    def forbidden_legacy(*_args, **_kwargs):
        nonlocal legacy_calls
        legacy_calls += 1
        raise AssertionError("allowlisted core traffic must not enter legacy research")

    monkeypatch.setattr(server, "research_permit", forbidden_legacy)
    result = server._research_permit_with_budget(
        "paint an exterior fence",
        "Buckeye",
        "AZ",
        job_category="residential",
    )
    public = server.build_customer_response_egress(
        server._mark_server_owned_result(result),
        "paint an exterior fence",
        "Buckeye",
        "AZ",
        job_category="residential",
    )

    assert legacy_calls == 0
    assert public["jurisdiction_id"] == "us-az-buckeye"
    assert public["permit_decision"] == "NEEDS_INPUT"
    assert public["permit_required"] is None


def test_exact_ahj_display_name_activates_stable_allowlisted_jurisdiction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not reject an exact indexed AHJ because its display name is not its ID slug."""
    monkeypatch.setenv(pre.CORE_SETTING, "active")
    monkeypatch.setenv(pre.CORE_ALLOWLIST_SETTING, "us-fl-pasco_county")
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    monkeypatch.setenv("PERMITASSIST_V24_MANIFEST_SHA256", MANIFEST_SHA256)
    _reset_v24_cache(monkeypatch)

    result = pre.build_active_core_first_result(
        job_type="Residential reroof. Replace the existing roof covering.",
        city="Pasco County Building Construction Services",
        state="FL",
        job_category="residential",
    )

    assert result is not None
    projected = pre.extract_sealed_public_projection(
        result,
        city="Pasco County Building Construction Services",
        state="FL",
    )
    assert projected is not None
    assert projected["jurisdiction_id"] == "us-fl-pasco-county"


def test_allowlisted_missing_core_envelope_raises_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_v24_cache(monkeypatch)
    _activate_buckeye(monkeypatch, MANIFEST_SHA256)

    def broken_envelope(*_args, **_kwargs):
        raise RuntimeError("simulated envelope construction failure")

    monkeypatch.setattr(pre, "build_core_decision_envelope", broken_envelope)
    with pytest.raises(pre.ActiveCorePackageUnavailableError) as caught:
        pre.build_active_core_first_result(
            job_type="residential reroof",
            city="Buckeye",
            state="AZ",
            job_category="residential",
        )
    assert caught.value.package_code == "core_envelope_unavailable"


def test_active_allowlisted_nonexact_identity_never_calls_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _activate_buckeye(monkeypatch, MANIFEST_SHA256)
    legacy_calls = 0

    def forbidden_legacy(*_args, **_kwargs):
        nonlocal legacy_calls
        legacy_calls += 1
        raise AssertionError("active allowlisted identity failure must not enter legacy research")

    monkeypatch.setattr(server, "research_permit", forbidden_legacy)
    monkeypatch.setattr(
        _server_pre,
        "resolve_jurisdiction_identity",
        lambda *_args, **_kwargs: _server_pre.JurisdictionIdentityResolution(
            _server_pre.JurisdictionResolutionStatus.UNCOVERED,
            (),
            None,
            "simulated non-exact active identity",
        ),
    )

    with pytest.raises(server.ActiveCorePackageUnavailableError) as caught:
        server._research_permit_with_budget(
            "residential reroof",
            "Buckeye",
            "AZ",
            job_category="residential",
        )

    assert caught.value.package_code == "jurisdiction_identity_uncovered"
    assert legacy_calls == 0


@pytest.mark.parametrize(
    ("broken_symbol", "expected_package_code"),
    (
        ("_request_targets_active_core", "active_core_runtime_unavailable"),
        ("resolve_jurisdiction_identity", "jurisdiction_identity_unavailable"),
        ("core_activation_allowed", "active_core_runtime_unavailable"),
        ("resolve_v24_cell", "v24_resolution_unavailable"),
        ("extract_sealed_public_projection", "core_envelope_unavailable"),
    ),
)
def test_active_allowlisted_unexpected_core_exception_never_calls_legacy(
    monkeypatch: pytest.MonkeyPatch,
    broken_symbol: str,
    expected_package_code: str,
) -> None:
    _activate_buckeye(monkeypatch, MANIFEST_SHA256)
    legacy_calls = 0

    def broken_stage(*_args, **_kwargs):
        raise RuntimeError(f"simulated {broken_symbol} failure")

    def forbidden_legacy(*_args, **_kwargs):
        nonlocal legacy_calls
        legacy_calls += 1
        raise AssertionError("active allowlisted core exception must not enter legacy research")

    monkeypatch.setattr(_server_pre, broken_symbol, broken_stage)
    monkeypatch.setattr(server, "research_permit", forbidden_legacy)

    with pytest.raises(server.ActiveCorePackageUnavailableError) as caught:
        server._research_permit_with_budget(
            "residential reroof",
            "Buckeye",
            "AZ",
            job_category="residential",
        )

    assert caught.value.package_code == expected_package_code
    assert legacy_calls == 0


def test_unallowlisted_unexpected_identity_exception_preserves_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _activate_buckeye(monkeypatch, MANIFEST_SHA256)
    marker = {"legacy_path": True}

    def broken_identity(*_args, **_kwargs):
        raise RuntimeError("simulated unallowlisted identity failure")

    monkeypatch.setattr(_server_pre, "resolve_jurisdiction_identity", broken_identity)
    monkeypatch.setattr(server, "research_permit", lambda *_args, **_kwargs: marker)

    assert (
        server._research_permit_with_budget(
            "commercial tenant improvement",
            "Denver",
            "CO",
            job_category="commercial",
        )
        is marker
    )


def test_core_off_preserves_legacy_path_when_package_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _activate_buckeye(monkeypatch, STALE_MANIFEST_SHA256)
    monkeypatch.setenv(pre.CORE_SETTING, "off")
    marker = {"legacy_path": True}
    monkeypatch.setattr(server, "research_permit", lambda *_args, **_kwargs: marker)

    assert (
        server._research_permit_with_budget(
            "residential reroof",
            "Buckeye",
            "AZ",
            job_category="residential",
        )
        is marker
    )


def test_active_core_runtime_health_is_identity_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    health_fn = getattr(pre, "active_core_runtime_health", None)
    assert callable(health_fn), "active-core deployments need a package-aware health gate"

    _activate_buckeye(monkeypatch, STALE_MANIFEST_SHA256)
    stale = health_fn()
    assert stale["ready"] is False
    assert stale["code"] == "manifest_sha256_mismatch"
    assert stale["expected_manifest_sha256"] == STALE_MANIFEST_SHA256
    assert stale["actual_manifest_sha256"] == MANIFEST_SHA256

    _activate_buckeye(monkeypatch, MANIFEST_SHA256)
    ready = health_fn()
    assert isinstance(ready, dict)
    assert ready["ready"] is True
    assert ready["code"] == "ready"
    assert ready["index_entries"] == 2162
    assert ready["allowlist"] == ["us-az-buckeye"]
    source_hashes = {
        name: hashlib.sha256((ROOT / "api" / name).read_bytes()).hexdigest()
        for name in ("permit_rule_engine.py", "server.py", "v24_decision_cells.py")
    }
    expected_source_identity = hashlib.sha256(
        json.dumps(source_hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert ready["runtime_source_sha256"] == expected_source_identity


def test_active_core_runtime_health_rejects_missing_pin_but_preserves_forced_v24_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _activate_buckeye(monkeypatch, MANIFEST_SHA256)
    monkeypatch.delenv("PERMITASSIST_V24_MANIFEST_SHA256")
    missing_pin = pre.active_core_runtime_health()
    assert missing_pin["ready"] is False
    assert missing_pin["code"] == "manifest_pin_missing"

    _activate_buckeye(monkeypatch, MANIFEST_SHA256)
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "shadow")
    forced_v24 = pre.active_core_runtime_health()
    assert forced_v24["ready"] is True
    assert forced_v24["code"] == "ready"
    assert forced_v24["v24_mode"] == "shadow"
    assert pre.assert_active_core_runtime_ready()["ready"] is True


def test_active_core_runtime_health_rejects_index_digest_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index_source = ROOT / "knowledge" / "v24" / "permitassist_decision_cell_index_v24.json"
    manifest_copy = tmp_path / MANIFEST.name
    index_copy = tmp_path / "renamed-runtime-index.json"
    manifest_copy.write_bytes(MANIFEST.read_bytes())
    index_copy.write_bytes(index_source.read_bytes() + b"\n")

    _activate_buckeye(monkeypatch, MANIFEST_SHA256)
    monkeypatch.setenv("PERMITASSIST_V24_MANIFEST_PATH", str(manifest_copy))
    monkeypatch.setenv("PERMITASSIST_V24_INDEX_PATH", str(index_copy))
    drift = pre.active_core_runtime_health()

    assert drift["ready"] is False
    assert drift["code"] == "index_sha256_mismatch"
    assert drift["expected_index_sha256"] != drift["actual_index_sha256"]
    assert str(tmp_path) not in json.dumps(drift, sort_keys=True)
    assert v24.load_v24_index() is None


def test_health_endpoint_returns_503_for_active_core_package_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _activate_buckeye(monkeypatch, STALE_MANIFEST_SHA256)
    with _LiveServer(server.Handler) as live:
        try:
            urllib.request.urlopen(f"{live.base}/health", timeout=5)
        except urllib.error.HTTPError as exc:
            status = exc.code
            body = json.loads(exc.read().decode("utf-8"))
        else:  # pragma: no cover - makes the failure message explicit on RED baseline
            pytest.fail("active-core package mismatch returned a healthy status")

    assert status == 503
    assert body["status"] == "degraded"
    assert body["error"] == "active_core_package_unavailable"
    assert body["rule_engine"]["code"] == "manifest_sha256_mismatch"


def test_permit_endpoint_returns_503_not_legacy_200_for_active_core_package_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _activate_buckeye(monkeypatch, STALE_MANIFEST_SHA256)
    monkeypatch.setattr(server, "ADMIN_TOKEN", "runtime-package-test-admin")
    legacy_calls = 0

    def forbidden_legacy(*_args, **_kwargs):
        nonlocal legacy_calls
        legacy_calls += 1
        raise AssertionError("legacy research must remain unreachable")

    monkeypatch.setattr(server, "research_permit", forbidden_legacy)
    with _LiveServer(server.Handler) as live:
        status, raw = _post_json(
            f"{live.base}/api/permit",
            {
                "job_type": "residential reroof",
                "city": "Buckeye",
                "state": "AZ",
                "job_category": "residential",
            },
            {
                "X-Admin-Token": "runtime-package-test-admin",
                "X-Client-Fingerprint": "active-core-package-boundary",
            },
        )

    body = json.loads(raw)
    assert status == 503
    assert body == {
        "error": "active_core_package_unavailable",
        "message": "PermitAssist rule-engine data is temporarily unavailable. Please retry shortly.",
    }
    assert legacy_calls == 0
