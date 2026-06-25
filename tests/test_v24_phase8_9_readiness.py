import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
for path in (ROOT, API):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from api.v24_decision_cells import (  # noqa: E402
    V24ResolutionStatus,
    get_v24_mode,
    load_v24_index,
    reconcile_authoritative_result,
    resolve_v24_cell,
    validate_v24_cell,
)

PKG = ROOT / "knowledge" / "v24"
INDEX = PKG / "permitassist_decision_cell_index_v24.json"
MANIFEST = PKG / "permitassist_v24_manifest.json"
DEFERRED = PKG / "permitassist_v24_deferred_manifest.json"


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_phase8_active_staging_mode_is_explicit_and_off_shadow_do_not_publish_v24(monkeypatch):
    monkeypatch.delenv("PERMITASSIST_V24_MODE", raising=False)
    assert get_v24_mode() == "off"
    off = resolve_v24_cell("Anchorage", "AK", "commercial tenant improvement", "commercial")
    assert off.status == V24ResolutionStatus.INDEX_UNAVAILABLE

    base = {"permit_required": False, "permit_decision": "NOT_REQUIRED", "permit_verdict": "NO"}
    reconcile_authoritative_result(base, v24_resolution=off, v231_resolution=None)
    assert base == {"permit_required": False, "permit_decision": "NOT_REQUIRED", "permit_verdict": "NO"}

    monkeypatch.setenv("PERMITASSIST_V24_MODE", "shadow")
    shadow = resolve_v24_cell("Anchorage", "AK", "commercial tenant improvement", "commercial")
    assert shadow.status == V24ResolutionStatus.EXACT_CELL_PUBLISHABLE
    base = {"permit_required": False, "permit_decision": "NOT_REQUIRED", "permit_verdict": "NO"}
    reconcile_authoritative_result(base, v24_resolution=shadow, v231_resolution=None)
    assert base["permit_required"] is False
    assert "_v24_cell_id" not in base

    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    active = resolve_v24_cell("Anchorage", "AK", "commercial tenant improvement", "commercial")
    assert active.status == V24ResolutionStatus.EXACT_CELL_PUBLISHABLE
    base = {"permit_required": False, "permit_decision": "NOT_REQUIRED", "permit_verdict": "NO"}
    reconcile_authoritative_result(base, v24_resolution=active, v231_resolution=None)
    assert base["permit_required"] is True
    assert base["permit_decision"] == "REQUIRED"
    assert base["permit_verdict"] == "YES"
    assert base["_decision_cell_primary_lock"]["source"] == "permitassist_v24_decision_cell"


def test_phase8_manifest_hash_pin_and_missing_index_are_safe(monkeypatch, tmp_path):
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    assert load_v24_index(index_path=INDEX, manifest_path=MANIFEST, expected_manifest_sha256=_sha256(MANIFEST)) is not None
    assert load_v24_index(index_path=INDEX, manifest_path=MANIFEST, expected_manifest_sha256="0" * 64) is None

    missing = tmp_path / "missing-index.json"
    unavailable = resolve_v24_cell("Anchorage", "AK", "commercial tenant improvement", "commercial", index_path=missing)
    assert unavailable.status == V24ResolutionStatus.INDEX_UNAVAILABLE


def test_phase8_wrong_project_and_fail_closed_preserve_live_binary_but_do_not_use_v231(monkeypatch):
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")

    wrong_project = resolve_v24_cell("Anchorage", "AK", "water heater replacement", "residential")
    assert wrong_project.status in {
        V24ResolutionStatus.AHJ_COVERED_PROJECT_NOT_COVERED,
        V24ResolutionStatus.AMBIGUOUS_ABSTAIN,
    }

    fail_closed = resolve_v24_cell("Yuma", "AZ", "residential remodel", "residential")
    assert fail_closed.status == V24ResolutionStatus.EXACT_CELL_FAIL_CLOSED
    result = {"permit_required": True, "permit_decision": "REQUIRED", "permit_verdict": "YES"}
    reconcile_authoritative_result(result, v24_resolution=fail_closed, v231_resolution={"publish_status": "PUBLISHABLE"})
    assert result["permit_required"] is True
    assert result["permit_verdict"] == "YES"
    assert result["_field_sources"]["fail_closed"] == "permitassist_v24_static_data_gap"
    assert "_v231_decision_cell" not in result


def test_phase9_local_canary_samples_validate_and_full_target_invariants_hold(monkeypatch):
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    manifest = _json(MANIFEST)
    index_doc = _json(INDEX)
    cells_doc = _json(PKG / manifest["decision_cells_file"])
    deferred = _json(DEFERRED)
    index = index_doc["index"]
    cells = cells_doc["cells"]

    assert manifest["mode"] == "staged_app_runtime_candidate_not_deployed"
    assert manifest["counts"] == {
        "cells": 2162,
        "deferred_total": 327,
        "ready_total": 2162,
        "v231_total": 2489,
        "w2_reroof_pass": 25,
        "w3_publishable": 200,
        "w4_tier1_complete": 1938,
    }
    assert len(index) == 2162
    assert len(cells) == 2162
    assert deferred.get("counts", {}).get("total") == 327
    assert sum(1 for cell in cells if cell.get("status") == "PUBLISHABLE") == 2128
    assert sum(1 for cell in cells if cell.get("status") == "FAIL_CLOSED") == 34

    # C1/C2 local canary-ramp proof: validate a deterministic 50-cell spread,
    # including all project families and fail-closed rows, without touching prod.
    keys = sorted(index)
    selected_keys = []
    must_include_prefixes = [
        "AK|anchorage|commercial_tenant_improvement",
        "AL|albertville|residential_remodel",
        "AZ|buckeye|reroof",
        "AZ|yuma|residential_remodel",
    ]
    for key in must_include_prefixes:
        assert key in index
        selected_keys.append(key)
    stride = max(1, len(keys) // 50)
    for key in keys[::stride]:
        if key not in selected_keys:
            selected_keys.append(key)
        if len(selected_keys) >= 50:
            break

    statuses = {"PUBLISHABLE": 0, "FAIL_CLOSED": 0}
    families = set()
    for key in selected_keys:
        cell = copy.deepcopy(index[key])
        families.add(cell.get("project_family"))
        statuses[cell.get("status")] = statuses.get(cell.get("status"), 0) + 1
        validation = validate_v24_cell(cell, strict_snapshots=False, require_live_url_check=False)
        assert validation.ok, (key, validation.to_dict())

    assert statuses["PUBLISHABLE"] > 0
    assert statuses["FAIL_CLOSED"] > 0
    assert {"commercial_tenant_improvement", "residential_remodel", "reroof"}.issubset(families)
