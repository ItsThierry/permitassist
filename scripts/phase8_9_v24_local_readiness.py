#!/usr/bin/env python3
"""PermitAssist v24 Phase 8/9 local-only readiness runner.

This does not deploy, import, push, mutate Railway, or touch production. It treats
Phase 8 (staging-active readiness) and Phase 9 (production canary/ramp readiness)
as local gates: package/hash invariants, mode/rollback/kill-switch semantics,
canary sample validation, and localhost API evidence from an already-running
PERMITASSIST_V24_MODE=active server on port 18766.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
for path in (ROOT, API):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from api.v24_decision_cells import (  # noqa: E402
    V24ResolutionStatus,
    load_v24_index,
    reconcile_authoritative_result,
    resolve_v24_cell,
    validate_v24_cell,
)

ARTIFACT_ROOT = ROOT / "artifacts" / "v24_phase8_9_local_readiness"
CACHE_DB = ARTIFACT_ROOT / "tmp-cache" / "cache.db"
BASE_URL = "http://127.0.0.1:18766"
LOCAL_API_KEY = os.environ.get("PERMITASSIST_PHASE8_9_LOCAL_API_KEY", "")
LOCAL_EMAIL = "phase8-9-paid@example.test"
PKG = ROOT / "knowledge" / "v24"
INDEX = PKG / "permitassist_decision_cell_index_v24.json"
MANIFEST = PKG / "permitassist_v24_manifest.json"
DEFERRED = PKG / "permitassist_v24_deferred_manifest.json"

NORMAL_HEADERS = {"Content-Type": "application/json", "X-Sample-Demo": "1"}
BYPASS_HEADERS = {
    "Content-Type": "application/json",
    "X-PermitIQ-Benchmark-Secret": "phase8-9-local-benchmark-secret-7890",
    "X-PermitAssist-Cache-Mode": "bypass",
}
V1_HEADERS = {"Content-Type": "application/json", "Authorization": f"Bearer {LOCAL_API_KEY}"}
FORBIDDEN_CUSTOMER_MARKERS = [
    "_v24",
    "permitassist_v24",
    "v2.4",
    "decision_cell",
    "decision cell",
    "cell_id",
    "resolver",
    "permitassist_v231",
    "_v231",
    "snapshot_hash",
    "source_snapshot_path",
    "raw_snapshot_path",
    "terminal_status",
    "spine_only",
    "tier1_complete",
    "validator_agent_id",
    "builder_run_id",
]

API_CASES = [
    {
        "id": "phase8_active_w4_anchorage_commercial_ti",
        "surface": "permit",
        "payload": {"city": "Anchorage", "state": "AK", "job_type": "commercial tenant improvement", "job_category": "commercial"},
        "headers": BYPASS_HEADERS,
        "expect": {"status": 200, "permit_verdict": "YES", "permit_required": True, "permit_decision": "REQUIRED", "apply_url": True},
    },
    {
        "id": "phase8_active_w3_albertville_residential_remodel",
        "surface": "permit",
        "payload": {"city": "Albertville", "state": "AL", "job_type": "residential remodel", "job_category": "residential"},
        "headers": NORMAL_HEADERS,
        "expect": {"status": 200, "permit_verdict": "YES", "permit_required": True, "permit_decision": "REQUIRED", "apply_url": True},
    },
    {
        "id": "phase8_active_w2_buckeye_reroof",
        "surface": "permit",
        "payload": {"city": "Buckeye", "state": "AZ", "job_type": "reroof", "job_category": "residential"},
        "headers": NORMAL_HEADERS,
        "expect": {"status": 200, "permit_verdict": "YES", "permit_required": True, "permit_decision": "REQUIRED", "apply_url": True},
    },
    {
        "id": "phase8_active_fail_closed_yuma_contact_only",
        "surface": "permit",
        "payload": {"city": "Yuma", "state": "AZ", "job_type": "residential remodel", "job_category": "residential"},
        "headers": NORMAL_HEADERS,
        "expect": {"status": 200, "permit_verdict": "CONTACT_AHJ", "permit_required": None, "permit_decision": "UNKNOWN", "not_binary_yes_no": True},
    },
    {
        "id": "phase9_local_canary_w4_juneau_commercial_ti",
        "surface": "permit",
        "payload": {"city": "Juneau", "state": "AK", "job_type": "commercial tenant improvement", "job_category": "commercial"},
        "headers": NORMAL_HEADERS,
        "expect": {"status": 200, "permit_verdict": "YES", "permit_required": True, "permit_decision": "REQUIRED", "apply_url": True},
    },
    {
        "id": "phase9_local_canary_batch_publishable_and_fail_closed",
        "surface": "batch",
        "payload": {"lookups": [
            {"city": "Buckeye", "state": "AZ", "job_type": "reroof", "job_category": "residential"},
            {"city": "Yuma", "state": "AZ", "job_type": "residential remodel", "job_category": "residential"},
        ]},
        "headers": {"Content-Type": "application/json"},
        "expect": {"status": 200, "results": [
            {"permit_verdict": "YES", "permit_required": True, "permit_decision": "REQUIRED", "apply_url": True},
            {"permit_verdict": "CONTACT_AHJ", "permit_required": None, "permit_decision": "UNKNOWN", "not_binary_yes_no": True},
        ]},
    },
    {
        "id": "phase9_local_canary_paid_v1_buckeye",
        "surface": "v1",
        "payload": {"city": "Buckeye", "state": "AZ", "job_type": "reroof", "job_category": "residential"},
        "headers": V1_HEADERS,
        "expect": {"status": 200, "permit_verdict": "YES", "permit_required": True, "permit_decision": "REQUIRED", "apply_url": True},
    },
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def forbidden_hits(body: Any) -> list[str]:
    rendered = json.dumps(body, sort_keys=True, ensure_ascii=False).lower()
    return [marker for marker in FORBIDDEN_CUSTOMER_MARKERS if marker in rendered]


def healthcheck() -> dict[str, Any]:
    started = time.time()
    try:
        response = requests.get(f"{BASE_URL}/api/stats", timeout=15)
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text[:500]
        return {"status_code": response.status_code, "elapsed_s": round(time.time() - started, 3), "body": body}
    except Exception as exc:
        return {"status_code": 0, "elapsed_s": round(time.time() - started, 3), "body": repr(exc)}


def ensure_local_paid_api_key() -> None:
    if not CACHE_DB.exists():
        raise RuntimeError(f"cache DB not found: {CACHE_DB}")
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(CACHE_DB)
    try:
        conn.execute("INSERT OR IGNORE INTO users (email, plan, created_at, last_login) VALUES (?,?,?,?)", (LOCAL_EMAIL, "solo", now, now))
        conn.execute("UPDATE users SET plan='solo', last_login=? WHERE email=?", (now, LOCAL_EMAIL))
        conn.execute(
            "INSERT OR REPLACE INTO api_keys (email, key, name, created_at, last_used_at, lookup_count) VALUES (?,?,?,?,?,COALESCE((SELECT lookup_count FROM api_keys WHERE key=?),0))",
            (LOCAL_EMAIL, LOCAL_API_KEY, "Phase 8/9 local key", now, None, LOCAL_API_KEY),
        )
        conn.commit()
    finally:
        conn.close()


def post_json(path: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 240) -> dict[str, Any]:
    started = time.time()
    try:
        response = requests.post(f"{BASE_URL}{path}", json=payload, headers=headers, timeout=timeout)
        try:
            body: Any = response.json()
        except Exception:
            body = None
        return {"status_code": response.status_code, "elapsed_s": round(time.time() - started, 3), "body": body, "response_text": response.text[:4000]}
    except Exception as exc:
        return {"status_code": 0, "elapsed_s": round(time.time() - started, 3), "body": None, "response_text": repr(exc)}


def check_body(expect: dict[str, Any], body: Any, *, leak_scan: bool = True) -> list[str]:
    errors: list[str] = []
    if not isinstance(body, dict):
        return ["response body is not an object"]
    for key in ("permit_verdict", "permit_required", "permit_decision"):
        if key in expect and body.get(key) != expect[key]:
            errors.append(f"{key} expected {expect[key]!r} got {body.get(key)!r}")
    if expect.get("apply_url") and not body.get("apply_url"):
        errors.append("missing apply_url")
    if expect.get("not_binary_yes_no") and body.get("permit_verdict") in {"YES", "NO"}:
        errors.append(f"fail-closed emitted binary verdict {body.get('permit_verdict')}")
    if body.get("permit_required") is True and not body.get("permits_required"):
        errors.append("permit_required true without permits_required")
    if leak_scan:
        hits = forbidden_hits(body)
        if hits:
            errors.append(f"customer-visible forbidden markers leaked: {hits}")
    return errors


def run_api_case(case: dict[str, Any]) -> dict[str, Any]:
    path = {"permit": "/api/permit", "batch": "/api/batch-permit", "v1": "/api/v1/permit"}[case["surface"]]
    result = post_json(path, case["payload"], case["headers"])
    errors: list[str] = []
    if result["status_code"] != case["expect"]["status"]:
        errors.append(f"status expected {case['expect']['status']} got {result['status_code']}")
    elif case["surface"] == "batch":
        body = result.get("body")
        rows = body.get("results") if isinstance(body, dict) else None
        expects = case["expect"]["results"]
        if not isinstance(rows, list) or len(rows) != len(expects):
            errors.append(f"batch results length mismatch: {len(rows) if isinstance(rows, list) else 'not-list'}")
        else:
            for idx, (row, expect) in enumerate(zip(rows, expects)):
                for err in check_body(expect, row):
                    errors.append(f"batch[{idx}]: {err}")
        hits = forbidden_hits(body)
        if hits:
            errors.append(f"batch response forbidden markers leaked: {hits}")
    else:
        errors.extend(check_body(case["expect"], result.get("body")))
    result.update({"id": case["id"], "surface": case["surface"], "payload": case["payload"], "errors": errors})
    return result


def cache_rows() -> list[dict[str, Any]]:
    if not CACHE_DB.exists():
        return []
    conn = sqlite3.connect(CACHE_DB)
    try:
        rows = conn.execute("SELECT job_type, city, state, hits FROM permit_cache ORDER BY city, job_type").fetchall()
        return [{"job_type": r[0], "city": r[1], "state": r[2], "hits": r[3]} for r in rows]
    finally:
        conn.close()


def run_mode_and_package_checks() -> tuple[list[dict[str, Any]], dict[str, list[str]], dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    errors: dict[str, list[str]] = {}
    old_mode = os.environ.get("PERMITASSIST_V24_MODE")
    try:
        os.environ["PERMITASSIST_V24_MODE"] = "off"
        off = resolve_v24_cell("Anchorage", "AK", "commercial tenant improvement", "commercial")
        checks.append({"id": "phase8_kill_switch_off", "status": off.status.value, "ok": off.status == V24ResolutionStatus.INDEX_UNAVAILABLE})

        os.environ["PERMITASSIST_V24_MODE"] = "shadow"
        shadow = resolve_v24_cell("Anchorage", "AK", "commercial tenant improvement", "commercial")
        served = {"permit_required": False, "permit_decision": "NOT_REQUIRED", "permit_verdict": "NO"}
        reconcile_authoritative_result(served, v24_resolution=shadow, v231_resolution=None)
        shadow_ok = shadow.status == V24ResolutionStatus.EXACT_CELL_PUBLISHABLE and served.get("permit_required") is False and "_v24_cell_id" not in served
        checks.append({"id": "phase8_shadow_does_not_publish", "status": shadow.status.value, "served": served, "ok": shadow_ok})

        os.environ["PERMITASSIST_V24_MODE"] = "active"
        active = resolve_v24_cell("Anchorage", "AK", "commercial tenant improvement", "commercial")
        served = {"permit_required": False, "permit_decision": "NOT_REQUIRED", "permit_verdict": "NO"}
        reconcile_authoritative_result(served, v24_resolution=active, v231_resolution=None)
        active_ok = active.status == V24ResolutionStatus.EXACT_CELL_PUBLISHABLE and served.get("permit_required") is True and served.get("_decision_cell_primary_lock", {}).get("source") == "permitassist_v24_decision_cell"
        checks.append({"id": "phase8_active_publishes_exact_cell", "status": active.status.value, "served": served, "ok": active_ok})

        mismatch = load_v24_index(index_path=INDEX, manifest_path=MANIFEST, expected_manifest_sha256="0" * 64)
        checks.append({"id": "phase9_manifest_hash_mismatch_refuses_load", "ok": mismatch is None})

        fail_closed = resolve_v24_cell("Yuma", "AZ", "residential remodel", "residential")
        served = {"permit_required": True, "permit_decision": "REQUIRED", "permit_verdict": "YES"}
        reconcile_authoritative_result(served, v24_resolution=fail_closed, v231_resolution={"publish_status": "PUBLISHABLE"})
        fc_ok = fail_closed.status == V24ResolutionStatus.EXACT_CELL_FAIL_CLOSED and served.get("permit_required") is None and served.get("permit_verdict") == "CONTACT_AHJ"
        checks.append({"id": "phase9_fail_closed_blocks_binary_answer", "status": fail_closed.status.value, "served": served, "ok": fc_ok})
    finally:
        if old_mode is None:
            os.environ.pop("PERMITASSIST_V24_MODE", None)
        else:
            os.environ["PERMITASSIST_V24_MODE"] = old_mode

    for check in checks:
        if not check.get("ok"):
            errors[check["id"]] = ["check returned not ok"]

    manifest = load_json(MANIFEST)
    index = load_json(INDEX)["index"]
    cells = load_json(PKG / manifest["decision_cells_file"])["cells"]
    deferred_doc = load_json(DEFERRED)
    deferred_count = int(deferred_doc.get("counts", {}).get("total") or 0)
    package_summary = {
        "manifest_mode": manifest.get("mode"),
        "counts": manifest.get("counts"),
        "publishable_count": sum(1 for cell in cells if cell.get("status") == "PUBLISHABLE"),
        "fail_closed_count": sum(1 for cell in cells if cell.get("status") == "FAIL_CLOSED"),
        "index_count": len(index),
        "deferred_count": deferred_count,
        "hashes": {str(path.relative_to(ROOT)): sha256(path) for path in [MANIFEST, INDEX, PKG / manifest["decision_cells_file"], DEFERRED]},
    }
    invariants = {
        "counts_ready_2162": manifest.get("counts", {}).get("ready_total") == 2162,
        "counts_deferred_327": manifest.get("counts", {}).get("deferred_total") == 327 and deferred_count == 327,
        "index_2162": len(index) == 2162,
        "cells_2162": len(cells) == 2162,
        "publishable_2127_failclosed_35": package_summary["publishable_count"] == 2127 and package_summary["fail_closed_count"] == 35,
        "manifest_local_only_mode": manifest.get("mode") == "staged_app_runtime_candidate_not_deployed",
    }
    package_summary["invariants"] = invariants
    if not all(invariants.values()):
        errors["package_invariants"] = [k for k, ok in invariants.items() if not ok]

    selected_keys = [
        "AK|anchorage|commercial_tenant_improvement",
        "AL|albertville|residential_remodel",
        "AZ|buckeye|reroof",
        "AZ|yuma|residential_remodel",
    ]
    keys = sorted(index)
    stride = max(1, len(keys) // 50)
    for key in keys[::stride]:
        if key not in selected_keys:
            selected_keys.append(key)
        if len(selected_keys) >= 50:
            break
    canary_rows = []
    canary_errors: list[str] = []
    for key in selected_keys:
        cell = index[key]
        validation = validate_v24_cell(cell, strict_snapshots=False, require_live_url_check=False)
        canary_rows.append({"key": key, "status": cell.get("status"), "project_family": cell.get("project_family"), "validation_ok": validation.ok})
        if not validation.ok:
            canary_errors.append(f"{key}: {validation.to_dict()}")
    package_summary["local_canary_validation"] = {
        "selected_count": len(selected_keys),
        "rows": canary_rows,
        "ok": not canary_errors,
        "description": "Local C1/C2 canary-ramp sample; no prod traffic or deployment touched.",
    }
    if canary_errors:
        errors["local_canary_validation"] = canary_errors
    return checks, errors, package_summary


def main() -> int:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    out_dir = ARTIFACT_ROOT / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    mode_checks, errors, package_summary = run_mode_and_package_checks()

    health = healthcheck()
    if health["status_code"] != 200:
        errors["localhost_server_health"] = [f"/api/stats expected 200 got {health['status_code']}: {health['body']}"]
    else:
        ensure_local_paid_api_key()

    api_results: list[dict[str, Any]] = []
    if health["status_code"] == 200:
        for case in API_CASES:
            result = run_api_case(case)
            api_results.append(result)
            if result["errors"]:
                errors[result["id"]] = result["errors"]
            print(json.dumps({"id": result["id"], "surface": result["surface"], "status": result["status_code"], "elapsed_s": result["elapsed_s"], "errors": result["errors"]}, sort_keys=True))

        before = cache_rows()
        repeat_case = {**API_CASES[2], "id": "phase9_local_canary_cache_repeat_buckeye"}
        repeat = run_api_case(repeat_case)
        after = cache_rows()
        buckeye_rows = [row for row in after if row.get("city") == "Buckeye" and row.get("state") == "AZ"]
        if not buckeye_rows or max(int(row.get("hits") or 0) for row in buckeye_rows) < 1:
            repeat["errors"].append("cached repeat did not increment Buckeye permit_cache hits")
        repeat["cache_before_repeat"] = before
        repeat["cache_after_repeat"] = after
        api_results.append(repeat)
        if repeat["errors"]:
            errors[repeat["id"]] = repeat["errors"]
        print(json.dumps({"id": repeat["id"], "surface": repeat["surface"], "status": repeat["status_code"], "elapsed_s": repeat["elapsed_s"], "errors": repeat["errors"]}, sort_keys=True))

    summary = {
        "ok": not errors,
        "run_id": run_id,
        "artifact_dir": str(out_dir),
        "phase8": "local staging-active readiness simulation",
        "phase9": "local production canary/ramp readiness simulation; no prod traffic/deploy/import/push",
        "server": {"base_url": BASE_URL, "health": health, "cache_db": str(CACHE_DB)},
        "mode_checks": mode_checks,
        "package_summary": package_summary,
        "api_case_count": len(api_results),
        "errors": errors,
        "boundary": {
            "prod_deploy_git_push_railway_import_touched": False,
            "requires_boban_approval_for_real_phase9_prod_canary": True,
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "results.json").write_text(json.dumps({"summary": summary, "api_results": api_results}, indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "local_canary_manifest.json").write_text(json.dumps(package_summary.get("local_canary_validation", {}), indent=2, sort_keys=True), encoding="utf-8")
    print("SUMMARY", json.dumps(summary, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
