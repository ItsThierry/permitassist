#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ["PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD"] = "1"

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from family_reconciliation_gate import family_from_row  # noqa: E402
from no_neuter_scorer import score_packet  # noqa: E402
from server import build_customer_permit_view_model  # noqa: E402

ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_fable5_customer_pov_20260701T234354Z"
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "live100_full_customer"
OUT_ROOT = ROOT / "artifacts" / f"full_customer_fix_for_good_replay_20260702_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
C_CASE_IDS = ["R-003","R-005","R-025","R-033","R-043","R-044","R-049","C-002","C-006","C-018","C-029","C-033","C-034","C-036","C-040","C-043","C-044","C-050"]


def strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)


def blob(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str).lower()


def families(packet: dict[str, Any]) -> set[str]:
    return {family_from_row(r) for r in packet.get("permits_required") or [] if isinstance(r, dict) and r.get("required") is not False and str(r.get("decision") or "REQUIRED").upper() != "CONDITIONAL"}


def cond_families(packet: dict[str, Any]) -> set[str]:
    return {family_from_row(r) for r in packet.get("conditional_permits") or [] if isinstance(r, dict)}


def family_ok(actual: set[str], expected: str) -> bool:
    aliases = {
        "building_ti": {"building_ti", "building"},
        "building_adu": {"building_adu", "building"},
        "demolition": {"demolition", "building"},
        "racking": {"racking", "building", "fire_suppression"},
        "fire_alarm": {"fire_alarm", "fire_suppression", "electrical"},
    }
    return bool(actual & aliases.get(expected, {expected}))


def load_cases() -> list[dict[str, Any]]:
    return [json.loads(line) for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines() if line.strip()]


def load_grades() -> dict[str, str]:
    path = ARTIFACT_ROOT / "FINAL_FULL_CUSTOMER_AUDIT_TITI_FABLE5_GRADES.csv"
    with path.open() as f:
        return {row["case_id"]: row["final_full_customer_grade"] for row in csv.DictReader(f)}


def check_c_contract(case_id: str, public: dict[str, Any]) -> list[str]:
    contract = json.loads((FIXTURE_ROOT / case_id / "expected_contract.json").read_text())
    fams = families(public)
    conds = cond_families(public)
    issues: list[str] = []
    for expected in contract.get("must_keep_required", []):
        if not family_ok(fams, expected):
            issues.append(f"missing_required_family:{expected}")
    for forbidden in contract.get("must_not_required", []):
        if family_ok(fams, forbidden):
            issues.append(f"forbidden_required_family:{forbidden}")
    for expected in contract.get("must_demote", []):
        if not family_ok(conds, expected):
            issues.append(f"missing_conditional_family:{expected}")
    text = blob(public)
    if contract.get("apply_url_must_contain") and contract["apply_url_must_contain"].lower() not in str(public.get("apply_url") or "").lower():
        issues.append("apply_url_mismatch")
    if contract.get("source_must_contain") and contract["source_must_contain"].lower() not in text:
        issues.append("source_missing")
    if contract.get("source_must_not_contain") and contract["source_must_not_contain"].lower() in text:
        issues.append("wrong_source_leak")
    if contract.get("fee_must_not_contain") and contract["fee_must_not_contain"].lower() in str(public.get("fee_range") or "").lower():
        issues.append("bad_fee_component")
    if "verify in before quoting" in text:
        issues.append("garbled_fee_caveat")
    return issues


def main() -> int:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    cases = load_cases()
    grades = load_grades()
    c_resolutions = []
    score_rows = []
    diffs = []
    public_cases = []
    failures = []
    for rec in cases:
        case = rec["case"]
        cid = case["id"]
        before = rec["response_body"]
        try:
            after = build_customer_permit_view_model(before, case["job_type"], case["city"], case["state"], job_category=case.get("segment"))
        except Exception as exc:  # noqa: BLE001
            failures.append({"case_id": cid, "issue": "exception", "error": repr(exc)})
            continue
        public_cases.append({"case": case, "public": after})
        before_score = score_packet(before)
        after_score = score_packet(after)
        before_fams = sorted(families(before))
        after_fams = sorted(families(after))
        row = {
            "case_id": cid,
            "grade": grades.get(cid, ""),
            "before_decision": before.get("permit_decision"),
            "after_decision": after.get("permit_decision"),
            "before_families": before_fams,
            "after_families": after_fams,
            "conditional_families": sorted(cond_families(after)),
            "a_case_demoted_families": sorted((set(before_fams) - set(after_fams)) if grades.get(cid) == "A" else []),
            "before_score": before_score,
            "after_score": after_score,
            "public_packet_rows": len(after.get("public_packet_rows") or []),
            "issues": [],
        }
        text = blob(after)
        if cid in C_CASE_IDS:
            issues = check_c_contract(cid, after)
            c_resolutions.append({**row, "contract_issues": issues, "passed": not issues})
            row["issues"].extend(issues)
        if grades.get(cid) == "A":
            missing_a_families = set(before_fams) - set(after_fams)
            conditional_a_families = set(row["conditional_families"])
            if missing_a_families - conditional_a_families:
                row["issues"].append("a_case_required_families_dropped_not_conditional:" + ",".join(sorted(missing_a_families - conditional_a_families)))
            if after_score["required_rows"] < min(before_score["required_rows"], 1) and not after_score["not_required"]:
                row["issues"].append("a_case_required_rows_regressed")
            if after_score["fee_amounts"] < min(before_score["fee_amounts"], 1):
                row["issues"].append("a_case_fee_amounts_regressed")
            if after_score["sources"] < min(before_score["sources"], 1):
                row["issues"].append("a_case_sources_regressed")
        if "verify in before quoting" in text:
            row["issues"].append("garbled_fee_caveat")
        if after.get("permit_decision") not in {"REQUIRED", "NOT_REQUIRED", "UNKNOWN", None}:
            row["issues"].append("invalid_decision")
        score_rows.append(row)
        if before_fams != after_fams or before_score != after_score or row["issues"]:
            diffs.append({
                "case_id": cid,
                "job_type": case["job_type"],
                "city": case["city"],
                "state": case["state"],
                "grade": grades.get(cid, ""),
                "before_families": before_fams,
                "after_families": after_fams,
                "conditional_families": sorted(cond_families(after)),
                "before_score": before_score,
                "after_score": after_score,
                "issues": row["issues"],
            })
        if row["issues"]:
            failures.append({"case_id": cid, "issues": row["issues"]})
    scorecard = {
        "artifact_root": str(ARTIFACT_ROOT),
        "out_root": str(OUT_ROOT),
        "total_cases": len(cases),
        "replayed": len(public_cases),
        "failures": failures,
        "c_cases_total": len(C_CASE_IDS),
        "c_cases_passed": sum(1 for r in c_resolutions if r["passed"]),
        "a_cases_total": sum(1 for r in score_rows if r["grade"] == "A"),
        "a_cases_with_issues": sum(1 for r in score_rows if r["grade"] == "A" and r["issues"]),
        "passed": len(cases) == len(public_cases) and not failures and sum(1 for r in c_resolutions if r["passed"]) == len(C_CASE_IDS),
    }
    (OUT_ROOT / "public_cases.jsonl").write_text("".join(json.dumps(x, sort_keys=True, ensure_ascii=False) + "\n" for x in public_cases), encoding="utf-8")
    (OUT_ROOT / "no_neuter_scorecard.json").write_text(json.dumps({"summary": scorecard, "rows": score_rows}, indent=2, sort_keys=True), encoding="utf-8")
    (OUT_ROOT / "before_after_diff.jsonl").write_text("".join(json.dumps(x, sort_keys=True, ensure_ascii=False) + "\n" for x in diffs), encoding="utf-8")
    (OUT_ROOT / "c_case_resolution.json").write_text(json.dumps(c_resolutions, indent=2, sort_keys=True), encoding="utf-8")
    (OUT_ROOT / "replay_summary.json").write_text(json.dumps(scorecard, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(scorecard, indent=2, sort_keys=True))
    return 0 if scorecard["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
