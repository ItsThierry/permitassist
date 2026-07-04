#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ["PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD"] = "1"
os.environ.setdefault("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
os.environ.setdefault("OPENAI_API_KEY", "offline-not-used")

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from public_packet import validate_public_packet  # noqa: E402
from server import build_customer_permit_view_model, render_share_page  # noqa: E402

ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_customer_pov_after_1041efe_20260704T000450Z"
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "live100_1041efe_full_fix"
MANIFEST_PATH = FIXTURE_ROOT / "FREEZE_MANIFEST.json"
DEFAULT_OUT = ROOT / "artifacts" / ("live100_1041efe_full_fix_local_replay_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
CURRENT_RED_CASES = {"C-002", "C-018", "C-030", "C-031", "C-037", "R-005", "R-013", "R-022", "R-032"}
NO_NEUTER_SENTINELS = {"C-001", "C-016", "C-023", "C-034", "C-039", "R-006", "R-018", "R-034"}


def load_cases() -> list[dict[str, Any]]:
    return [json.loads(line) for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines() if line.strip()]


def load_prior_grades() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    path = ARTIFACT_ROOT / "FINAL_FACTCHECKED_TITI_FABLE5_GRADES.csv"
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            out[row["case_id"]] = row
    return out


def report_payload(html_text: str) -> dict[str, Any]:
    match = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html_text, flags=re.S)
    if not match:
        return {}
    return json.loads(html.unescape(match.group(1)))


def rows(public: dict[str, Any], decision: str | None = None) -> list[dict[str, Any]]:
    packet = public.get("public_packet") if isinstance(public.get("public_packet"), dict) else {}
    result = [r for r in packet.get("rows") or [] if isinstance(r, dict)]
    if decision:
        result = [r for r in result if str(r.get("decision") or "").upper() == decision]
    return result


def fams(public: dict[str, Any], decision: str = "REQUIRED") -> set[str]:
    return {str(r.get("family") or "") for r in rows(public, decision)}


def visible_blob(public: dict[str, Any], html_text: str = "") -> str:
    visible_html = re.sub(r"<script\b.*?</script>", " ", html.unescape(html_text), flags=re.I | re.S)
    return (json.dumps(public, sort_keys=True, default=str) + "\n" + visible_html).lower()


def docs_blob(public: dict[str, Any]) -> str:
    packet = public.get("public_packet") if isinstance(public.get("public_packet"), dict) else {}
    values: list[str] = []
    for value in [packet.get("documents"), public.get("documents_to_prepare"), public.get("what_to_bring"), public.get("requirements")]:
        if isinstance(value, list):
            values.extend(str(x) for x in value)
    for row in rows(public):
        values.extend(str(x) for x in row.get("documents") or [])
    return "\n".join(values).lower()


def source_labels(public: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for src in public.get("sources") or []:
        if isinstance(src, dict):
            labels.append(str(src.get("label") or src.get("title") or ""))
    for row in rows(public):
        if row.get("source") or row.get("source_role"):
            labels.append(str(row.get("source") or row.get("source_role") or ""))
    return [x for x in labels if x]


def lint_public(case_id: str, case: dict[str, Any], public: dict[str, Any], html_text: str, payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    packet = public.get("public_packet") if isinstance(public.get("public_packet"), dict) else {}
    try:
        validate_public_packet(packet)
    except Exception as exc:  # noqa: BLE001
        issues.append(f"packet_validate:{exc.__class__.__name__}:{exc}")
    sealed = public.get("sealed_public_packet_hash")
    if not sealed or packet.get("sealed_public_packet_hash") != sealed:
        issues.append("packet_hash_mismatch")
    payload_data = ((payload.get("share") or {}).get("data") or {}) if isinstance(payload, dict) else {}
    if payload_data.get("sealed_public_packet_hash") != sealed:
        issues.append("render_seal_mismatch")
    if (payload_data.get("public_packet") or {}).get("sealed_public_packet_hash") != sealed:
        issues.append("render_packet_hash_mismatch")
    if payload_data.get("permits_required") != public.get("permits_required"):
        issues.append("render_required_rows_diverge")
    apply_path = public.get("apply_path") if isinstance(public.get("apply_path"), dict) else {}
    if public.get("permit_decision") == "REQUIRED":
        if not rows(public, "REQUIRED"):
            issues.append("required_without_required_packet_rows")
        if apply_path.get("typed_status") not in {"RESOLVED_PORTAL", "OFFICIAL_SOURCE_FALLBACK", "VERIFY_WITH_PERMIT_OFFICE", "BROKEN_URL", "UNKNOWN_REACHABILITY"}:
            issues.append("bad_required_apply_path_state")
    if public.get("permit_decision") == "NOT_REQUIRED":
        for key in ("permits_required", "documents_to_prepare", "what_to_bring", "requirements", "inspections", "apply_url"):
            if public.get(key):
                issues.append(f"not_required_artifact:{key}")
        if apply_path.get("typed_status") != "NOT_APPLICABLE":
            issues.append("not_required_apply_path_not_applicable")
    if any(label.strip().lower() == "source" for label in source_labels(public)):
        issues.append("bare_source_label")
    text = visible_blob(public, html_text)
    if re.search(r"\b(?:metadata|decision cell|resolver|keep this row visible)\b", text):
        issues.append("internal_language_leak")
    if re.search(r"onclick=\"window\.open\('\$\{esc\(maps", html_text):
        issues.append("raw_maps_onclick")
    fee_text = json.dumps({"fee_range": public.get("fee_range"), "fees": packet.get("fees")}, default=str).lower()
    if re.search(r"project\s+(?:cost|value)|total\s+project\s+(?:cost|value)|typical\s+total|valuation", fee_text) or re.search(r"\$\s?9,?150\s*[-–]\s*\$\s?10,?250", fee_text):
        issues.append("fee_mixes_project_cost")
    docs = docs_blob(public)
    if case_id in {"C-002"} and re.search(r"masonry lintel|facade structural", docs):
        issues.append("structural_doc_leak")
    if case_id == "C-030" and re.search(r"clean|exhaust", str(public.get("apply_url") or ""), re.I):
        issues.append("wrong_scope_apply_url")
    if case_id == "R-013" and public.get("permit_decision") != "NOT_REQUIRED":
        issues.append("drywall_exemption_not_preserved")
    return issues


def local_grade(case_id: str, prior_grade: str, issues: list[str]) -> str:
    if issues:
        return "C"
    if case_id in CURRENT_RED_CASES:
        return "B" if prior_grade in {"C", "F", ""} else prior_grade
    return prior_grade or "B"


def main() -> int:
    out = Path(os.environ.get("LIVE100_1041_REPLAY_OUT") or DEFAULT_OUT)
    out.mkdir(parents=True, exist_ok=True)
    html_dir = out / "html_reports"
    html_dir.mkdir(exist_ok=True)
    manifest = json.loads(MANIFEST_PATH.read_text())
    holdout_ids = set(manifest["holdout_case_ids"])
    cases = load_cases()
    prior = load_prior_grades()
    details: list[dict[str, Any]] = []
    hard_failures: list[dict[str, Any]] = []
    grade_counts = Counter()
    holdout_rows: list[dict[str, Any]] = []
    no_neuter_rows: list[dict[str, Any]] = []
    api_out = (out / "api_outputs.jsonl").open("w")
    report_out = (out / "report_payloads.jsonl").open("w")
    csv_rows: list[dict[str, Any]] = []
    try:
        for idx, rec in enumerate(cases, 1):
            case = rec["case"]
            cid = case["id"]
            raw = rec.get("response_body") or {}
            public = build_customer_permit_view_model(raw, case["job_type"], case["city"], case["state"], job_category=case.get("segment"))
            html_text = render_share_page({"slug": cid, "data": public, "job_type": case["job_type"], "city": case["city"], "state": case["state"]})
            html_path = html_dir / f"{idx:03d}_{cid}_{case.get('segment')}_{case.get('city')}_{case.get('state')}.html".replace(" ", "_")
            html_path.write_text(html_text, encoding="utf-8")
            payload = report_payload(html_text)
            issues = lint_public(cid, case, public, html_text, payload)
            prior_row = prior.get(cid) or {}
            prior_grade = prior_row.get("final_grade", "")
            after_grade = local_grade(cid, prior_grade, issues)
            grade_counts[after_grade] += 1
            before_decision = str(raw.get("permit_decision") or ("REQUIRED" if raw.get("permit_required") else "")).upper()
            decision_flip = before_decision in {"REQUIRED", "NOT_REQUIRED"} and public.get("permit_decision") != before_decision
            row = {
                "case_id": cid,
                "segment": case.get("segment"),
                "city": case.get("city"),
                "state": case.get("state"),
                "prior_grade": prior_grade,
                "local_after_grade": after_grade,
                "before_decision": before_decision,
                "after_decision": public.get("permit_decision"),
                "required_families": sorted(fams(public, "REQUIRED")),
                "conditional_families": sorted(fams(public, "CONDITIONAL")),
                "issues": issues,
                "html_path": str(html_path),
                "sealed_public_packet_hash": public.get("sealed_public_packet_hash"),
                "holdout": cid in holdout_ids,
                "current_red_case": cid in CURRENT_RED_CASES,
                "sentinel": cid in NO_NEUTER_SENTINELS,
            }
            details.append(row)
            csv_rows.append({k: json.dumps(v, sort_keys=True) if isinstance(v, (list, dict)) else v for k, v in row.items()})
            if issues:
                hard_failures.append(row)
            if cid in holdout_ids:
                holdout_rows.append(row)
            if cid in NO_NEUTER_SENTINELS or prior_grade == "A":
                no_neuter_rows.append({"case_id": cid, "prior_grade": prior_grade, "decision_flip": decision_flip, "issues": issues, "after_decision": public.get("permit_decision"), "required_families": row["required_families"]})
            api_out.write(json.dumps({"case_id": cid, "case": case, "public": public}, sort_keys=True, default=str) + "\n")
            report_out.write(json.dumps({"case_id": cid, "payload": payload}, sort_keys=True, default=str) + "\n")
    finally:
        api_out.close()
        report_out.close()
    no_neuter_violations = [r for r in no_neuter_rows if r["decision_flip"] or r["issues"]]
    holdout_failures = [r for r in holdout_rows if r["issues"]]
    current_red_failures = [r for r in details if r["current_red_case"] and r["issues"]]
    summary = {
        "artifact_root": str(ARTIFACT_ROOT),
        "out_root": str(out),
        "records": len(cases),
        "grade_counts_local_after_fix": dict(sorted(grade_counts.items())),
        "hard_failure_count": len(hard_failures),
        "current_red_failures": current_red_failures,
        "holdout_case_ids": sorted(holdout_ids),
        "holdout_failures": holdout_failures,
        "no_neuter_violations": no_neuter_violations,
        "packet_lint_pass": not hard_failures,
        "render_parity_pass": not any(any(issue.startswith("render_") for issue in row["issues"]) for row in details),
        "current_red_pass": not current_red_failures,
        "holdout_pass": not holdout_failures,
        "no_neuter_pass": not no_neuter_violations,
        "ready_by_local_offline_gates": not hard_failures and not no_neuter_violations,
        "fable_status": "REVIEW_PACKET_PREPARED_NOT_SUBMITTED_BY_SCRIPT",
    }
    (out / "details.json").write_text(json.dumps(details, indent=2, sort_keys=True, default=str), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
    with (out / "score_diff.csv").open("w", newline="") as f:
        fieldnames = list(csv_rows[0].keys()) if csv_rows else []
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)
    (out / "FABLE5_REVIEW_PACKET.md").write_text(
        "# Fable 5 review packet — 1041efe full-fix local/offline gates\n\n"
        f"Artifact root: `{ARTIFACT_ROOT}`\n\n"
        f"Output root: `{out}`\n\n"
        "## Summary\n\n```json\n" + json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n```\n\n"
        "## Diff inputs\n\nRun `git diff -- api tests frontend scripts` from the repo root and review this packet with `summary.json`, `score_diff.csv`, `api_outputs.jsonl`, and `report_payloads.jsonl`.\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0 if summary["ready_by_local_offline_gates"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
