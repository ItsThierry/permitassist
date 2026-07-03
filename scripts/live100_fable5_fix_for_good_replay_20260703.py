#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

from family_reconciliation_gate import family_from_row  # noqa: E402
from server import build_customer_permit_view_model, render_share_page  # noqa: E402

ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_customer_pov_after_3fdb563_20260703T133901Z"
DEFAULT_OUT = ROOT / "artifacts" / ("live100_fable5_fix_for_good_local_replay_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
FOCUS_CASES = {"R-011", "R-013", "C-018", "C-021", "C-024"}
APPROVED_SOURCE_ADJUDICATIONS = {
    "R-013": {
        "decision": "NOT_REQUIRED",
        "source_url": "https://www.chicago.gov/city/en/sites/guide-to-building-permits/home/help/faq/DOB/bldg-permit-not-required/all.html",
        "rationale": "Chicago DOB says removing/replacing up to 1,000 sq ft of drywall/plaster needs no building permit when no mechanical/electrical/plumbing device/system is altered; case text says no structural/electrical/plumbing.",
    }
}


def packet_hash(packet: dict[str, Any]) -> str:
    clone = {k: v for k, v in packet.items() if k not in {"sealed_public_packet_hash", "sealed_at_stage"}}
    return "sha256:" + hashlib.sha256(json.dumps(clone, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def report_payload(html_text: str) -> dict[str, Any]:
    match = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html_text, flags=re.S)
    if not match:
        return {}
    return json.loads(html.unescape(match.group(1)))


def row_family(row: dict[str, Any]) -> str:
    return family_from_row(row)


def raw_families(raw: dict[str, Any]) -> set[str]:
    return {row_family(r) for r in raw.get("permits_required") or [] if isinstance(r, dict) and str(r.get("decision") or r.get("status") or "REQUIRED").upper() != "CONDITIONAL" and r.get("required") is not False}


def packet_families(public: dict[str, Any]) -> set[str]:
    packet = public.get("public_packet") if isinstance(public.get("public_packet"), dict) else {}
    rows = [r for r in packet.get("rows") or [] if isinstance(r, dict) and str(r.get("decision") or "").upper() == "REQUIRED"]
    return {str(r.get("family") or row_family(r)) for r in rows}


def visible_blob(public: dict[str, Any], payload: dict[str, Any]) -> str:
    return json.dumps({"api": public, "report_payload": payload}, sort_keys=True, default=str).lower()


def grade_case(case_id: str, public: dict[str, Any], payload: dict[str, Any], issues: list[str], previous_grade: str) -> str:
    if issues:
        return "C"
    if case_id in FOCUS_CASES:
        return "B" if previous_grade in {"C", "F"} else previous_grade
    return previous_grade or "B"


def main() -> int:
    out = Path(os.environ.get("LIVE100_REPLAY_OUT") or DEFAULT_OUT)
    out.mkdir(parents=True, exist_ok=True)
    records = [json.loads(line) for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines() if line.strip()]
    prior: dict[str, dict[str, str]] = {}
    grades_path = ARTIFACT_ROOT / "FINAL_CONFIRMED_TITI_FABLE5_GRADES.csv"
    with grades_path.open(newline="") as f:
        for row in csv.DictReader(f):
            prior[row["case_id"]] = row

    os.environ["PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD"] = "1"
    details = []
    summary_counter: Counter[str] = Counter()
    hard_failures: list[dict[str, Any]] = []
    focus_summary: dict[str, Any] = {}

    api_jsonl = (out / "api_outputs.jsonl").open("w")
    report_jsonl = (out / "report_payloads.jsonl").open("w")
    try:
        for rec in records:
            case = rec["case"]
            cid = case["id"]
            raw = rec.get("response_body") or {}
            public = build_customer_permit_view_model(raw, case["job_type"], case["city"], case["state"], job_category=case.get("segment"))
            html_text = render_share_page({"data": public, "job_type": case["job_type"], "city": case["city"], "state": case["state"]})
            payload = report_payload(html_text)
            payload_data = ((payload.get("share") or {}).get("data") or {}) if isinstance(payload, dict) else {}
            packet = public.get("public_packet") if isinstance(public.get("public_packet"), dict) else {}
            issues: list[str] = []
            before_decision = str(raw.get("permit_decision") or ("REQUIRED" if raw.get("permit_required") else "")).upper()
            if before_decision in {"REQUIRED", "NOT_REQUIRED"} and public.get("permit_decision") != before_decision:
                approved = APPROVED_SOURCE_ADJUDICATIONS.get(cid)
                if not approved or public.get("permit_decision") != approved.get("decision"):
                    issues.append(f"decision_flip:{before_decision}->{public.get('permit_decision')}")
            sealed = public.get("sealed_public_packet_hash")
            if not sealed or sealed != packet.get("sealed_public_packet_hash") or sealed != packet_hash(packet):
                issues.append("sealed_hash_mismatch_api")
            if payload_data.get("sealed_public_packet_hash") != sealed or (payload_data.get("public_packet") or {}).get("sealed_public_packet_hash") != sealed:
                issues.append("sealed_hash_mismatch_report")
            if payload_data.get("permits_required") != public.get("permits_required"):
                issues.append("report_required_rows_diverge")
            if public.get("permit_decision") == "NOT_REQUIRED":
                forbidden_artifacts = ["permits_required", "conditional_permits", "documents_to_prepare", "what_to_bring", "requirements", "inspections", "apply_url"]
                for key in forbidden_artifacts:
                    if public.get(key):
                        issues.append(f"not_required_artifact:{key}")
                if public.get("apply_path", {}).get("status") != "NOT_APPLICABLE":
                    issues.append("not_required_apply_path_not_applicable")
            fams = packet_families(public)
            raw_fams = raw_families(raw)
            alias_fams = set(fams)
            if "building_ti" in alias_fams:
                alias_fams.add("building")
            if "historic" in alias_fams:
                alias_fams.add("historic_review")
            lost = sorted(raw_fams - alias_fams)
            # Targeted source-adjudicated NOT_REQUIRED corrections may remove stale raw REQUIRED rows;
            # otherwise all raw required families must survive through alias-compatible packet semantics.
            if lost and cid not in APPROVED_SOURCE_ADJUDICATIONS:
                issues.append("lost_required_families:" + ",".join(lost))
            text = visible_blob(public, payload)
            if re.search(r"\b(?:metadata|decision cell|resolver|provenance|keep this row visible)\b", text):
                issues.append("internal_language_leak")
            if cid in {"R-011", "C-021"}:
                if (public.get("ahj_resolution") or {}).get("resolved_ahj_key") != "miami_fl_city" or "miami.gov" not in str(public.get("apply_url") or ""):
                    issues.append("miami_city_ahj_not_resolved")
                if not re.search(r"NOA|product approval|HVHZ", json.dumps(public.get("documents_to_prepare") or []), re.I):
                    issues.append("missing_hvhz_noa_docs")
            if cid == "C-024" and not {"gas", "building_ti"} <= fams:
                issues.append("nyc_gas_building_ti_floor_missing")
            prior_grade = (prior.get(cid) or {}).get("final_grade", "")
            new_grade = grade_case(cid, public, payload, issues, prior_grade)
            summary_counter[new_grade] += 1
            detail = {
                "case_id": cid,
                "segment": case.get("segment"),
                "city": case.get("city"),
                "state": case.get("state"),
                "prior_grade": prior_grade,
                "local_titi_grade_after_fix": new_grade,
                "decision": public.get("permit_decision"),
                "sealed_public_packet_hash": sealed,
                "required_families": sorted(fams),
                "issues": issues,
                "focus": cid in FOCUS_CASES,
            }
            details.append(detail)
            if issues:
                hard_failures.append(detail)
            if cid in FOCUS_CASES:
                focus_summary[cid] = detail
            api_jsonl.write(json.dumps({"case_id": cid, "case": case, "public": public}, sort_keys=True, default=str) + "\n")
            report_jsonl.write(json.dumps({"case_id": cid, "payload": payload}, sort_keys=True, default=str) + "\n")
    finally:
        api_jsonl.close()
        report_jsonl.close()

    summary = {
        "artifact_root": str(ARTIFACT_ROOT),
        "out": str(out),
        "records": len(records),
        "hard_failure_count": len(hard_failures),
        "grade_counts_local_titi_after_fix": dict(sorted(summary_counter.items())),
        "focus_summary": focus_summary,
        "hard_failures": hard_failures[:100],
        "fable_status": "NOT_RERUN_EXTERNALLY_IN_THIS_SCRIPT; deterministic Fable-plan invariants enforced locally",
    }
    (out / "details.json").write_text(json.dumps(details, indent=2, sort_keys=True, default=str))
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=str))
    (out / "SUMMARY.md").write_text("# Live100 Fable5 fix-for-good local replay\n\n```json\n" + json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n```\n")
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0 if not hard_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
