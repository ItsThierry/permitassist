#!/usr/bin/env python3
"""PermitAssist v24 Phase 6/7 deeper local E2E runner.

Local-only: assumes the v24 active test server is already running at localhost:18766
with CACHE_DIR=artifacts/v24_phase6_e2e/tmp-cache. Persists a timestamped evidence
bundle and exits non-zero on any regression.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "artifacts" / "v24_phase6_deep_e2e"
CACHE_DB = ROOT / "artifacts" / "v24_phase6_e2e" / "tmp-cache" / "cache.db"
BASE_URL = "http://127.0.0.1:18766"
LOCAL_API_KEY = "pa_live_phase6_deep_local_only_key"
LOCAL_EMAIL = "phase6-deep-paid@example.test"
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
]

NORMAL_HEADERS = {"Content-Type": "application/json", "X-Sample-Demo": "1"}
BYPASS_HEADERS = {
    "Content-Type": "application/json",
    "X-PermitIQ-Benchmark-Secret": "phase6-local-benchmark-secret-7890",
    "X-PermitAssist-Cache-Mode": "bypass",
}
V1_HEADERS = {"Content-Type": "application/json", "Authorization": f"Bearer {LOCAL_API_KEY}"}

CASES = [
    {
        "id": "permit_w4_anchorage_commercial_ti_cold_bypass",
        "surface": "permit",
        "payload": {"city": "Anchorage", "state": "AK", "job_type": "commercial tenant improvement", "job_category": "commercial"},
        "headers": BYPASS_HEADERS,
        "expect": {"status": 200, "permit_verdict": "YES", "permit_required": True, "permit_decision": "REQUIRED", "apply_url": True, "no_internal_markers": True},
    },
    {
        "id": "permit_w3_albertville_residential_remodel",
        "surface": "permit",
        "payload": {"city": "Albertville", "state": "AL", "job_type": "residential remodel", "job_category": "residential"},
        "headers": NORMAL_HEADERS,
        "expect": {"status": 200, "permit_verdict": "YES", "permit_required": True, "permit_decision": "REQUIRED", "apply_url": True, "no_internal_markers": True},
    },
    {
        "id": "permit_w2_buckeye_reroof_warm",
        "surface": "permit",
        "payload": {"city": "Buckeye", "state": "AZ", "job_type": "reroof", "job_category": "residential"},
        "headers": NORMAL_HEADERS,
        "expect": {"status": 200, "permit_verdict": "YES", "permit_required": True, "permit_decision": "REQUIRED", "apply_url": True, "no_internal_markers": True},
    },
    {
        "id": "permit_v24_fail_closed_yuma_contact_only",
        "surface": "permit",
        "payload": {"city": "Yuma", "state": "AZ", "job_type": "residential remodel", "job_category": "residential"},
        "headers": NORMAL_HEADERS,
        "expect": {"status": 200, "permit_verdict": "CONTACT_AHJ", "permit_required": None, "permit_decision": "UNKNOWN", "not_binary_yes_no": True, "no_internal_markers": True},
    },
    {
        "id": "permit_v24_uncovered_v231_fallback_helena",
        "surface": "permit",
        "payload": {"city": "Helena", "state": "AL", "job_type": "residential remodel", "job_category": "residential"},
        "headers": NORMAL_HEADERS,
        "expect": {"status": 200, "permit_verdict": "YES", "permit_required": True, "permit_decision": "REQUIRED", "apply_url": True, "no_internal_markers": True},
    },
]

SURFACE_CASES = [
    {
        "id": "batch_surface_mixed_v24_publishable_and_fail_closed",
        "surface": "batch",
        "payload": {"lookups": [CASES[2]["payload"], CASES[3]["payload"]]},
        "headers": {"Content-Type": "application/json"},
        "expect": {"status": 200, "results": [CASES[2]["expect"], CASES[3]["expect"]]},
    },
    {
        "id": "v1_surface_paid_api_key_buckeye_cached",
        "surface": "v1",
        "payload": CASES[2]["payload"],
        "headers": V1_HEADERS,
        "expect": {"status": 200, "permit_verdict": "YES", "permit_required": True, "permit_decision": "REQUIRED", "apply_url": True, "no_internal_markers": False},
    },
]


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
            (LOCAL_EMAIL, LOCAL_API_KEY, "Phase 6 deep local key", now, None, LOCAL_API_KEY),
        )
        conn.commit()
    finally:
        conn.close()


def post_json(path: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 240) -> dict[str, Any]:
    started = time.time()
    try:
        resp = requests.post(f"{BASE_URL}{path}", json=payload, headers=headers, timeout=timeout)
        text = resp.text
        try:
            body: Any = resp.json()
        except Exception:
            body = None
        return {"status_code": resp.status_code, "elapsed_s": round(time.time() - started, 3), "body": body, "response_text": text[:4000]}
    except Exception as exc:
        return {"status_code": 0, "elapsed_s": round(time.time() - started, 3), "body": None, "response_text": repr(exc)}


def forbidden_hits(body: Any) -> list[str]:
    rendered = json.dumps(body, sort_keys=True, ensure_ascii=False).lower()
    return [marker for marker in FORBIDDEN_CUSTOMER_MARKERS if marker in rendered]


def check_body(expect: dict[str, Any], body: Any) -> list[str]:
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
    if expect.get("no_internal_markers"):
        hits = forbidden_hits(body)
        if hits:
            errors.append(f"customer-visible forbidden markers leaked: {hits}")
    if body.get("permit_required") is True and not body.get("permits_required"):
        errors.append("permit_required true without permits_required")
    return errors


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    if case["surface"] == "permit":
        path = "/api/permit"
    elif case["surface"] == "batch":
        path = "/api/batch-permit"
    elif case["surface"] == "v1":
        path = "/api/v1/permit"
    else:
        raise AssertionError(case["surface"])
    result = post_json(path, case["payload"], case["headers"])
    errors: list[str] = []
    if result["status_code"] != case["expect"]["status"]:
        errors.append(f"status expected {case['expect']['status']} got {result['status_code']}")
    elif case["surface"] == "batch":
        body = result.get("body")
        if not isinstance(body, dict):
            errors.append("batch body is not an object")
        else:
            rows = body.get("results")
            if not isinstance(rows, list) or len(rows) != len(case["expect"]["results"]):
                errors.append(f"batch results length mismatch: {len(rows) if isinstance(rows, list) else 'not-list'}")
            else:
                for idx, (row, expect) in enumerate(zip(rows, case["expect"]["results"])):
                    for err in check_body(expect, row):
                        errors.append(f"batch[{idx}]: {err}")
            if forbidden_hits(body):
                errors.append(f"batch response forbidden markers leaked: {forbidden_hits(body)}")
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


def hash_artifacts() -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in [
        "knowledge/v24/permitassist_decision_cell_index_v24.json",
        "knowledge/v24/permitassist_decision_cells_v24.json",
        "knowledge/v24/permitassist_v24_manifest.json",
        "knowledge/v24/permitassist_v24_deferred_manifest.json",
    ]:
        path = ROOT / rel
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "MISSING"
    return out


def main() -> int:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    out_dir = ARTIFACT_ROOT / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    ensure_local_paid_api_key()

    results: list[dict[str, Any]] = []
    errors: dict[str, list[str]] = {}

    for case in CASES:
        result = run_case(case)
        results.append(result)
        if result["errors"]:
            errors[result["id"]] = result["errors"]
        print(json.dumps({"id": result["id"], "surface": result["surface"], "status": result["status_code"], "elapsed_s": result["elapsed_s"], "errors": result["errors"]}, sort_keys=True))

    cache_before_repeat = cache_rows()
    repeat = run_case({**CASES[2], "id": "permit_w2_buckeye_cached_repeat"})
    cache_after_repeat = cache_rows()
    buckeye_rows = [r for r in cache_after_repeat if r["city"] == "Buckeye" and r["state"] == "AZ"]
    if not buckeye_rows or max(int(r.get("hits") or 0) for r in buckeye_rows) < 1:
        repeat["errors"].append("cached repeat did not increment Buckeye permit_cache hits")
    results.append(repeat)
    if repeat["errors"]:
        errors[repeat["id"]] = repeat["errors"]
    print(json.dumps({"id": repeat["id"], "surface": repeat["surface"], "status": repeat["status_code"], "elapsed_s": repeat["elapsed_s"], "errors": repeat["errors"]}, sort_keys=True))

    for case in SURFACE_CASES:
        result = run_case(case)
        results.append(result)
        if result["errors"]:
            errors[result["id"]] = result["errors"]
        print(json.dumps({"id": result["id"], "surface": result["surface"], "status": result["status_code"], "elapsed_s": result["elapsed_s"], "errors": result["errors"]}, sort_keys=True))

    summary = {
        "ok": not errors,
        "run_id": run_id,
        "case_count": len(results),
        "errors": errors,
        "cache_before_repeat": cache_before_repeat,
        "cache_after_repeat": cache_after_repeat,
        "artifact_hashes": hash_artifacts(),
        "artifact_dir": str(out_dir),
    }
    (out_dir / "results.json").write_text(json.dumps({"summary": summary, "results": results}, indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("SUMMARY", json.dumps(summary, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
