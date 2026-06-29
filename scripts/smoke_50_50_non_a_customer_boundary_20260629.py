#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
TESTS = ROOT / "tests"
ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_50_50_20260629T120408Z"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
os.environ.setdefault("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

import server  # noqa: E402
from live_customer_50_50_phase0_helpers import family_from_row, required_rows, row_status, visible_rows  # noqa: E402

NON_A = ["R100-023", "R100-050", "C100-050", "R100-007", "R100-010", "R100-022", "R100-040", "C100-037", "C100-048", "C100-006", "C100-011", "C100-021", "C100-040"]

cases = {}
for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines():
    if line.strip():
        rec = json.loads(line)["case"]
        cases[rec["id"]] = rec

failures = []
for cid in NON_A:
    case = cases[cid]
    raw = json.loads(Path(case["artifact_json_path"]).read_text())
    body = raw.get("response_body") or raw.get("body") or raw
    public = server.build_customer_permit_view_model(body, case["job_type"], case["city"], case["state"], job_category=case.get("segment"))
    text = json.dumps(public, sort_keys=True, default=str).lower()
    req_rows = required_rows(public)
    fams = []
    for row in visible_rows(public):
        fams.append(f"{family_from_row(row)}:{row_status(row)}")
    bad_required_no_permit = public.get("permit_decision") == "REQUIRED" and bool(re.search(r"\bno permit required\b|\bno permit submission needed\b", text))
    bad_not_required_rows = public.get("permit_decision") == "NOT_REQUIRED" and bool(req_rows)
    if bad_required_no_permit or bad_not_required_rows:
        failures.append(cid)
    print(json.dumps({
        "case_id": cid,
        "decision": public.get("permit_decision"),
        "permit_required": public.get("permit_required"),
        "permit_name": public.get("permit_name"),
        "required_families": [family_from_row(row) for row in req_rows],
        "visible_families_statuses": fams,
        "bad_required_no_permit": bad_required_no_permit,
        "bad_not_required_rows": bad_not_required_rows,
    }, sort_keys=True))
if failures:
    print("FAIL non-A smoke", failures)
    raise SystemExit(1)
print(f"PASS non-A customer-boundary smoke: {len(NON_A)} cases")
