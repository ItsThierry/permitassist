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
os.environ.setdefault("OPENAI_API_KEY", "test-key")

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from server import build_customer_permit_view_model, render_share_page  # noqa: E402

ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_customer_pov_after_6b8b1f1_20260703T191058Z"
OUT_ROOT = Path(os.environ.get("LIVE100_FABLE5_OUT") or (ROOT / "artifacts" / f"live100_universal_fable5_local_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"))
HTML_DIR = OUT_ROOT / "html_reports"
BASELINE_COUNTS = {"A": 49, "B": 45, "C": 6, "F": 0}
C_CASE_IDS = {"C-018", "C-035", "C-037", "R-013", "R-033", "R-049"}
SENTINELS = {
    "C-001", "C-016", "C-023", "C-034", "R-034", "R-018", "R-006", "C-039",
}


def load_cases() -> list[dict[str, Any]]:
    return [json.loads(line) for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines() if line.strip()]


def load_grades() -> dict[str, str]:
    out: dict[str, str] = {}
    with (ARTIFACT_ROOT / "FINAL_RESEARCH_FACTCHECKED_TITI_FABLE5_GRADES.csv").open() as f:
        for row in csv.DictReader(f):
            out[row["case_id"]] = row.get("research_final_grade") or row.get("final_confirmed_grade") or row.get("final_full_customer_grade") or ""
    return out


def rows(public: dict[str, Any], decision: str | None = None) -> list[dict[str, Any]]:
    packet = public.get("public_packet") if isinstance(public.get("public_packet"), dict) else {}
    out = [r for r in packet.get("rows") or [] if isinstance(r, dict)]
    if decision:
        out = [r for r in out if str(r.get("decision") or "").upper() == decision]
    return out


def fams(public: dict[str, Any], decision: str = "REQUIRED") -> set[str]:
    return {str(r.get("family") or "") for r in rows(public, decision)}


def blob(public: dict[str, Any], html_text: str = "") -> str:
    visible_html = re.sub(r"<script\b.*?</script>", " ", html.unescape(html_text), flags=re.I | re.S)
    return (json.dumps(public, sort_keys=True, default=str) + "\n" + visible_html).lower()


def docs(public: dict[str, Any]) -> str:
    packet = public.get("public_packet") if isinstance(public.get("public_packet"), dict) else {}
    values: list[str] = []
    for value in [packet.get("documents"), public.get("documents_to_prepare"), public.get("what_to_bring"), public.get("requirements")]:
        if isinstance(value, list):
            values.extend(str(x) for x in value)
    for row in rows(public):
        values.extend(str(x) for x in row.get("documents") or [])
    return "\n".join(values).lower()


def c_contract_issues(cid: str, public: dict[str, Any], html_text: str) -> list[str]:
    text = blob(public, html_text)
    packet = public.get("public_packet") if isinstance(public.get("public_packet"), dict) else {}
    req = fams(public, "REQUIRED")
    visible_fams = req | fams(public, "CONDITIONAL")
    issues: list[str] = []
    if cid == "C-018":
        if packet.get("lead_label") != "Commercial Building Permit — Change of Use / Tenant Improvement (Assembly)":
            issues.append("bad_lead_label")
        if "residential" in text:
            issues.append("residential_label_leak")
        for fam in ("building_ti", "fire_life_safety_assembly", "co_change_of_occupancy"):
            if fam not in visible_fams:
                issues.append(f"missing_family:{fam}")
        if not re.search(r"occupant\s+load|life[-\s]?safety|egress", docs(public)):
            issues.append("missing_assembly_docs")
    if cid == "C-035":
        if "fire_hazmat_co2" not in visible_fams:
            issues.append("missing_family:fire_hazmat_co2")
        for needle in ("co2", "gas detection", "hazardous materials"):
            if needle not in docs(public):
                issues.append(f"missing_doc:{needle}")
        for fam in ("building_ti", "electrical", "mechanical"):
            if fam not in req:
                issues.append(f"neutered_core:{fam}")
    if cid == "C-037":
        if packet.get("lead_label") != "Commercial Building Permit — Structural Facade / Masonry Repair":
            issues.append("bad_structural_label")
        if "storefront / window-door alteration" in text:
            issues.append("storefront_label_leak")
        if not re.search(r"structural (drawings|engineering)|licensed engineer|masonry lintel", docs(public)):
            issues.append("missing_structural_docs")
    if cid == "R-013":
        if public.get("permit_decision") != "NOT_REQUIRED" or public.get("permit_required") is not False:
            issues.append("not_required_verdict_mismatch")
        if rows(public, "REQUIRED") or public.get("permits_required"):
            issues.append("required_rows_on_not_required")
        if public.get("apply_url") or re.search(r'<a\b[^>]+href=["\']\s*["\']', html_text, re.I):
            issues.append("apply_link_on_not_required")
    if cid == "R-033":
        for fam in ("health_food", "wastewater_pretreatment_fog"):
            if fam in visible_fams:
                issues.append(f"forbidden_family:{fam}")
        if "new circuit / equipment connection" in text:
            issues.append("new_circuit_label_leak")
    if cid == "R-049":
        if "gas" not in req:
            issues.append("missing_gas_core")
        for fam in ("health_food", "wastewater_pretreatment_fog"):
            if fam in visible_fams:
                issues.append(f"forbidden_family:{fam}")
        sources = [s for s in public.get("sources") or [] if isinstance(s, dict) and "iccsafe.org" in str(s.get("url") or "")]
        if not sources:
            issues.append("icc_context_link_deleted")
        elif any("official" in str(s.get("title") or s.get("label") or "").lower() for s in sources):
            issues.append("icc_blog_official_badge")
    return issues


def html_scan_issues(cid: str, case: dict[str, Any], public: dict[str, Any], html_text: str) -> list[str]:
    text = blob(public, html_text)
    issues: list[str] = []
    if case.get("segment") == "residential" and re.search(r"\b(food establishment|wastewater|fog|pretreatment)\b", text) and cid in {"R-033", "R-049"}:
        issues.append("residential_food_fog_leak")
    if public.get("permit_decision") == "NOT_REQUIRED" and "permit required: yes" in re.sub(r"<script\b.*?</script>", " ", html.unescape(html_text), flags=re.I | re.S).lower():
        issues.append("not_required_rendered_yes")
    if re.search(r'<a\b[^>]+href=["\']\s*["\']', html_text, re.I):
        issues.append("empty_href")
    if case.get("segment") == "commercial" and re.search(r"\bresidential building permit\b", text):
        issues.append("commercial_residential_label")
    for src in public.get("sources") or []:
        if isinstance(src, dict) and "official" in str(src.get("title") or src.get("label") or "").lower() and str(src.get("source_role") or "") not in {"LOCAL_OFFICIAL_FILING", "LOCAL_OFFICIAL_INFO", "STATE_OFFICIAL"}:
            issues.append("official_badge_role_mismatch")
    return issues


def _missing_with_alias(before_req: list[str], after_req: list[str]) -> list[str]:
    after = set(after_req)
    aliases = {
        "building": {"building", "building_ti", "building_adu", "demolition", "racking"},
        "building_ti": {"building_ti", "building"},
        "fire_suppression": {"fire_suppression", "fire_life_safety_assembly", "fire_hazmat_co2"},
    }
    missing: list[str] = []
    for fam in before_req:
        if not (after & aliases.get(fam, {fam})):
            missing.append(fam)
    return sorted(set(missing))


def main() -> int:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    cases = load_cases()
    grades = load_grades()
    public_lines = []
    before_after = []
    html_findings = []
    c_results = []
    no_neuter = []
    invariant_counts = Counter()
    after_grade_counts = Counter()

    for idx, rec in enumerate(cases, 1):
        case = rec["case"]
        cid = case["id"]
        public = build_customer_permit_view_model(rec.get("response_body") or {}, case["job_type"], case["city"], case["state"], job_category=case.get("segment"))
        html_text = render_share_page({"slug": cid, "data": public, "job_type": case["job_type"], "city": case["city"], "state": case["state"]})
        html_path = HTML_DIR / f"{idx:03d}_{cid}_{case.get('segment')}_{case.get('city')}_{case.get('state')}.html".replace(" ", "_")
        html_path.write_text(html_text, encoding="utf-8")
        packet = public.get("public_packet") if isinstance(public.get("public_packet"), dict) else {}
        invariant_errors = packet.get("packet_invariant_errors") or []
        if invariant_errors:
            invariant_counts["packet_invariant_errors"] += len(invariant_errors)
        if public.get("sealed_public_packet_hash") and packet.get("sealed_public_packet_hash") == public.get("sealed_public_packet_hash"):
            invariant_counts["seal_parity_ok"] += 1
        else:
            invariant_counts["seal_parity_bad"] += 1
        h_issues = html_scan_issues(cid, case, public, html_text)
        for issue in h_issues:
            html_findings.append({"case_id": cid, "issue": issue, "html_path": str(html_path)})
        c_issues = c_contract_issues(cid, public, html_text) if cid in C_CASE_IDS else []
        if cid in C_CASE_IDS:
            c_results.append({"case_id": cid, "baseline_grade": grades.get(cid), "after_contract_pass": not c_issues, "issues": c_issues, "before_decision": (rec.get("response_body") or {}).get("permit_decision"), "after_decision": public.get("permit_decision"), "before_required_families": (rec.get("response_body") or {}).get("required_permit_families"), "after_required_families": sorted(fams(public, "REQUIRED")), "after_conditional_families": sorted(fams(public, "CONDITIONAL")), "html_path": str(html_path)})
        baseline_grade = grades.get(cid, "")
        after_grade = "B" if cid in C_CASE_IDS and not c_issues else baseline_grade
        after_grade_counts[after_grade] += 1
        before_req = sorted(str(x) for x in ((rec.get("response_body") or {}).get("required_permit_families") or []))
        after_req = sorted(fams(public, "REQUIRED"))
        before_after.append({"case_id": cid, "baseline_grade": baseline_grade, "after_contract_grade": after_grade, "before_decision": (rec.get("response_body") or {}).get("permit_decision"), "after_decision": public.get("permit_decision"), "before_required_families": before_req, "after_required_families": after_req, "html_issues": h_issues, "c_contract_issues": c_issues})
        if baseline_grade in {"A", "B"} or cid in SENTINELS:
            dropped = _missing_with_alias(before_req, after_req)
            no_neuter.append({"case_id": cid, "baseline_grade": baseline_grade, "decision_flipped": (rec.get("response_body") or {}).get("permit_decision") != public.get("permit_decision"), "dropped_required_families": dropped, "apply_path_lost": bool((rec.get("response_body") or {}).get("apply_url")) and not bool(public.get("apply_url"))})
        public_lines.append({"case": case, "public": public, "html_path": str(html_path)})

    no_neuter_violations = [r for r in no_neuter if r["decision_flipped"] or r["dropped_required_families"] or r["apply_path_lost"]]
    summary = {
        "artifact_root": str(ARTIFACT_ROOT),
        "out_root": str(OUT_ROOT),
        "total_cases": len(cases),
        "baseline_grade_counts": BASELINE_COUNTS,
        "after_contract_grade_counts": dict(sorted(after_grade_counts.items())),
        "c_cases_passed": sum(1 for r in c_results if r["after_contract_pass"]),
        "c_cases_total": len(c_results),
        "html_scan_findings": len(html_findings),
        "invariant_telemetry": dict(invariant_counts),
        "no_neuter_violations": len(no_neuter_violations),
        "ready_by_local_contract": len(c_results) == len(C_CASE_IDS) and all(r["after_contract_pass"] for r in c_results) and not html_findings and not no_neuter_violations and invariant_counts.get("seal_parity_ok") == len(cases),
    }
    (OUT_ROOT / "public_cases.jsonl").write_text("".join(json.dumps(x, sort_keys=True, ensure_ascii=False) + "\n" for x in public_lines), encoding="utf-8")
    (OUT_ROOT / "before_after_diff.jsonl").write_text("".join(json.dumps(x, sort_keys=True, ensure_ascii=False) + "\n" for x in before_after), encoding="utf-8")
    (OUT_ROOT / "c_case_before_after.json").write_text(json.dumps(c_results, indent=2, sort_keys=True), encoding="utf-8")
    (OUT_ROOT / "no_neuter_sentinel_diff.json").write_text(json.dumps({"violations": no_neuter_violations, "rows": no_neuter}, indent=2, sort_keys=True), encoding="utf-8")
    (OUT_ROOT / "invariant_telemetry.json").write_text(json.dumps(dict(invariant_counts), indent=2, sort_keys=True), encoding="utf-8")
    (OUT_ROOT / "html_scan_findings.csv").write_text("case_id,issue,html_path\n" + "".join(f"{r['case_id']},{r['issue']},{r['html_path']}\n" for r in html_findings), encoding="utf-8")
    (OUT_ROOT / "replay_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    packet_lines = [
        "# Fable 5 review packet — Live100 universal local replay",
        "",
        f"Artifact root: `{ARTIFACT_ROOT}`",
        f"Output root: `{OUT_ROOT}`",
        f"Summary: `{json.dumps(summary, sort_keys=True)}`",
        "",
        "## Six C cases",
        json.dumps(c_results, indent=2, sort_keys=True),
        "",
        "## No-neuter violations",
        json.dumps(no_neuter_violations, indent=2, sort_keys=True),
        "",
        "## HTML findings",
        json.dumps(html_findings, indent=2, sort_keys=True),
    ]
    (OUT_ROOT / "FABLE5_REVIEW_PACKET.md").write_text("\n".join(packet_lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["ready_by_local_contract"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
