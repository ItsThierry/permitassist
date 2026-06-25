#!/usr/bin/env python3
"""PermitAssist v24 Phase 6 local E2E runner.

Local-only: hits a localhost server started with PERMITASSIST_V24_MODE=active and
writes timestamped evidence under artifacts/v24_phase6_e2e/.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "artifacts" / "v24_phase6_e2e"
BASE_URL = "http://127.0.0.1:18766"
CACHE_DB = ARTIFACT_ROOT / "tmp-cache" / "cache.db"
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
    "X-PermitIQ-Benchmark-Secret": "***",
    "X-PermitAssist-Cache-Mode": "bypass",
}

CASES = [
    {
        "id": "w4_publishable_anchorage_commercial_ti_cold",
        "payload": {"city": "Anchorage", "state": "AK", "job_type": "commercial tenant improvement", "job_category": "commercial"},
        "headers": BYPASS_HEADERS,
        "expect": {"status": 200, "permit_verdict": "YES", "permit_required": True, "permit_decision": "REQUIRED", "apply_url": True},
    },
    {
        "id": "w3_publishable_albertville_residential_remodel",
        "payload": {"city": "Albertville", "state": "AL", "job_type": "residential remodel", "job_category": "residential"},
        "headers": NORMAL_HEADERS,
        "expect": {"status": 200, "permit_verdict": "YES", "permit_required": True, "permit_decision": "REQUIRED", "apply_url": True},
    },
    {
        "id": "w2_publishable_buckeye_reroof_warm",
        "payload": {"city": "Buckeye", "state": "AZ", "job_type": "reroof", "job_category": "residential"},
        "headers": NORMAL_HEADERS,
        "expect": {"status": 200, "permit_verdict": "YES", "permit_required": True, "permit_decision": "REQUIRED", "apply_url": True},
    },
    {
        "id": "v24_fail_closed_yuma_residential_remodel",
        "payload": {"city": "Yuma", "state": "AZ", "job_type": "residential remodel", "job_category": "residential"},
        "headers": NORMAL_HEADERS,
        "expect": {"status": 200, "permit_verdict": "CONTACT_AHJ", "permit_required": None, "not_binary_yes_no": True},
    },
    {
        "id": "v24_uncovered_falls_back_v231_helena_residential_remodel",
        "payload": {"city": "Helena", "state": "AL", "job_type": "residential remodel", "job_category": "residential"},
        "headers": NORMAL_HEADERS,
        "expect": {"status": 200, "permit_verdict": "YES", "permit_required": True, "permit_decision": "REQUIRED", "apply_url": True},
    },
]


def post_case(case: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    response_text = ""
    data: Any = None
    try:
        resp = requests.post(f"{BASE_URL}/api/permit", json=case["payload"], headers=case["headers"], timeout=240)
        response_text = resp.text
        try:
            data = resp.json()
        except Exception:
            data = None
        status_code = resp.status_code
    except Exception as exc:  # pragma: no cover - evidence path
        status_code = 0
        response_text = repr(exc)
    elapsed = round(time.time() - started, 3)
    return {"id": case["id"], "payload": case["payload"], "status_code": status_code, "elapsed_s": elapsed, "body": data, "response_text": response_text[:2000]}


def forbidden_hits(body: Any) -> list[str]:
    rendered = json.dumps(body, sort_keys=True, ensure_ascii=False).lower()
    return [marker for marker in FORBIDDEN_CUSTOMER_MARKERS if marker in rendered]


def check_case(case: dict[str, Any], result: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expect = case["expect"]
    body_value = result.get("body")
    body = body_value if isinstance(body_value, dict) else {}
    if result["status_code"] != expect["status"]:
        errors.append(f"status expected {expect['status']} got {result['status_code']}")
        return errors
    hits = forbidden_hits(body)
    if hits:
        errors.append(f"customer-visible forbidden markers leaked: {hits}")
    for key in ("permit_verdict", "permit_required", "permit_decision"):
        if key in expect and body.get(key) != expect[key]:
            errors.append(f"{key} expected {expect[key]!r} got {body.get(key)!r}")
    if expect.get("apply_url") and not body.get("apply_url"):
        errors.append("missing apply_url")
    if expect.get("not_binary_yes_no") and body.get("permit_verdict") in {"YES", "NO"}:
        errors.append(f"fail-closed emitted binary verdict {body.get('permit_verdict')}")
    return errors


def cache_summary() -> dict[str, Any]:
    if not CACHE_DB.exists():
        return {"cache_db_exists": False}
    conn = sqlite3.connect(CACHE_DB)
    try:
        rows = conn.execute("SELECT job_type, city, state, hits FROM permit_cache ORDER BY city, job_type").fetchall()
        return {"cache_db_exists": True, "rows": [{"job_type": r[0], "city": r[1], "state": r[2], "hits": r[3]} for r in rows]}
    finally:
        conn.close()


def main() -> int:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    out_dir = ARTIFACT_ROOT / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    errors: dict[str, list[str]] = {}

    for case in CASES:
        result = post_case(case)
        errs = check_case(case, result)
        result["errors"] = errs
        results.append(result)
        if errs:
            errors[case["id"]] = errs
        print(json.dumps({"id": case["id"], "status": result["status_code"], "elapsed_s": result["elapsed_s"], "errors": errs}, sort_keys=True))

    # Cached-path check: repeat Buckeye after warm request; expect DB hit increment and same regulated fields.
    warm_case = CASES[2]
    before = cache_summary()
    repeat = post_case({**warm_case, "id": "w2_publishable_buckeye_reroof_cached_repeat"})
    repeat_errs = check_case(warm_case, repeat)
    after = cache_summary()
    repeat["errors"] = repeat_errs
    results.append(repeat)
    buckeye_rows = [r for r in after.get("rows", []) if r.get("city") == "Buckeye" and r.get("state") == "AZ"]
    if not buckeye_rows or max(int(r.get("hits") or 0) for r in buckeye_rows) < 1:
        repeat_errs.append("cached repeat did not increment Buckeye permit_cache hits")
    if repeat_errs:
        errors[repeat["id"]] = repeat_errs
    print(json.dumps({"id": repeat["id"], "status": repeat["status_code"], "elapsed_s": repeat["elapsed_s"], "errors": repeat_errs}, sort_keys=True))

    summary = {
        "ok": not errors,
        "run_id": run_id,
        "case_count": len(results),
        "errors": errors,
        "cache_before_repeat": before,
        "cache_after_repeat": after,
        "artifact_dir": str(out_dir),
    }
    (out_dir / "results.json").write_text(json.dumps({"summary": summary, "results": results}, indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("SUMMARY", json.dumps(summary, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
