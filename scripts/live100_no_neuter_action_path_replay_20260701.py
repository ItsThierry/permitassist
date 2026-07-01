#!/usr/bin/env python3
"""Frozen Live100 no-neuter action-path replay/render artifact generator.

Replays the 2026-07-01 customer-POV Live100 artifact through local customer
boundary code. No network/model calls. Produces no-neuter, URL-status, and
static report HTML artifacts for Chromium rendering.
"""
from __future__ import annotations

import copy
import csv
import json
import os
import re
import sys
import types
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_customer_pov_20260701T123737Z"
BASELINE_PATH = ROOT / "tests" / "fixtures" / "live100_no_neuter_baseline_20260701.json"
OUT_ROOT = Path(os.environ.get("LIVE100_NO_NEUTER_OUT") or (ROOT / "artifacts" / "live100_no_neuter_action_path_fix_20260701_local"))
HTML_DIR = OUT_ROOT / "html_reports"
SCREENSHOT_DIR = OUT_ROOT / "screenshots_all_100"
COMMERCIAL_TIMELINE = "Commercial TI/addition/remodel scopes usually require plan review"
RESIDENTIAL_TIMELINE_FALSE_POSITIVES = {"R-007", "R-018"}
KNOWN_C_ACTION_PATH_CASES = {"R-015", "C-025", "R-031", "R-048"}
BROKEN_APPLY_BY_CASE = {
    "R-003": "https://aca-prod.accela.com/PORTLAND",
    "R-015": "https://aca-prod.accela.com/NASHVILLE",
    "R-020": "https://www.boston.gov/departments/inspectional-services/apply-permit-online",
    "R-027": "https://www.houstonpermittingcenter.org/online-permitting",
    "R-030": "https://www.cityofmadison.com/building-inspection/permits",
    "R-040": "https://detroitmi.gov/departments/buildings-safety-engineering-and-environmental-department/bseed-online-services",
    "R-041": "https://www.burlingtonvt.gov/DPZ",
    "R-042": "https://www.santafenm.gov/community_development/permits",
    "R-043": "https://www.charleston-sc.gov/1075/Inspections",
    "R-048": "https://www.littlerock.gov/city-administration/departments/planning-and-development/building-codes",
    "C-018": "https://aca-prod.accela.com/PORTLAND",
    "C-019": "https://www.clarkcountynv.gov/business/building/permits",
    "C-025": "https://aca-prod.accela.com/NASHVILLE",
    "C-031": "https://www.cityofmadison.com/building-inspection/permits",
    "C-040": "https://detroitmi.gov/departments/buildings-safety-engineering-and-environmental-department/bseed-online-services",
    "C-042": "https://www.santafenm.gov/community_development/permits",
    "C-043": "https://www.charleston-sc.gov/1075/Inspections",
    "C-048": "https://www.littlerock.gov/city-administration/departments/planning-and-development/building-codes",
}
SECRET_RE = re.compile(r"(?i)(PERMITASSIST_[A-Z0-9_]+|RAILWAY_[A-Z0-9_]+|OPENAI_API_KEY|ANTHROPIC_API_KEY|sk-[A-Za-z0-9_-]{16,}|pa_session[=:][A-Za-z0-9._-]+|authorization\s*:\s*bearer)")
INTERNAL_RE = re.compile(r"\b(decision cell|resolver|traceback|fail[_ -]?closed|before merging|keep this row visible|internal evidence)\b", re.I)


def _install_server_stubs() -> None:
    requests_stub = types.ModuleType("requests")
    requests_stub.post = lambda *a, **k: None
    requests_stub.get = lambda *a, **k: None
    requests_stub.head = lambda *a, **k: types.SimpleNamespace(status_code=200)
    requests_stub.exceptions = types.SimpleNamespace(Timeout=TimeoutError)
    sys.modules["requests"] = requests_stub
    openai_stub = types.ModuleType("openai")
    openai_stub.OpenAI = lambda *a, **k: object()
    sys.modules["openai"] = openai_stub
    google_stub = types.ModuleType("google")
    genai_stub = types.ModuleType("google.generativeai")
    genai_stub.configure = lambda *a, **k: None
    sys.modules["google"] = google_stub
    sys.modules["google.generativeai"] = genai_stub
    research_stub = types.ModuleType("research_engine")
    research_stub.research_permit = lambda *a, **k: {"permit_verdict": "MAYBE"}
    research_stub.build_google_maps_url = lambda *a, **k: ""
    research_stub.strip_pdf_from_result = lambda result: result
    research_stub.get_cache_hit_rate = lambda: 0
    research_stub.detect_primary_scope = lambda job_type: {"primary_scope": "generic", "signals": []}
    research_stub.classify_scope_required_permits = lambda job_type, city="", state="", scope_contract=None: []
    research_stub.classify_source_tier = lambda url, city="", state="", result=None: "local"
    research_stub.classify_source_authority = lambda url, city="", state="", result=None: {"category": "local_ahj", "tier": "local_ahj", "display_allowed": True, "local_decision_evidence": True}
    sys.modules["research_engine"] = research_stub


def _canonical_family(value: str) -> str:
    value = str(value or "").lower().strip().replace("-", "_")
    return {"zoning": "planning", "land_use": "planning", "occupancy": "co", "right_of_way": "grading", "row": "grading", "wastewater/fog": "wastewater", "food/health": "health"}.get(value, value)


def _visible_rows(public: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("permits_required", "related_permits", "companion_permits", "trade_permits"):
        rows.extend(row for row in public.get(key) or [] if isinstance(row, dict))
    return rows


def _url_key(url: str) -> str:
    return (url or "").rstrip("/").lower()


def main() -> int:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if str(API) not in sys.path:
        sys.path.insert(0, str(API))
    _install_server_stubs()
    from api import server  # noqa: WPS433

    server.CACHE_DB = str(OUT_ROOT / "replay_cache.db")
    server.DATA_DIR = str(OUT_ROOT)
    server.init_db()

    baseline = json.loads(BASELINE_PATH.read_text())
    records = [json.loads(line) for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines() if line.strip()]
    baseline_positive = set(baseline["commercial_timeline_positive_case_ids"])
    expected_timeline_positive = baseline_positive - RESIDENTIAL_TIMELINE_FALSE_POSITIVES

    cases_out: list[dict[str, Any]] = []
    url_rows: list[dict[str, Any]] = []
    render_manifest: list[dict[str, Any]] = []
    issue_counts: Counter[str] = Counter()
    observed_timeline_positive: set[str] = set()

    for idx, rec in enumerate(records, start=1):
        case = rec["case"]
        cid = case["id"]
        public = server.build_customer_permit_view_model(copy.deepcopy(rec["response_body"]), case["job_type"], case["city"], case["state"], job_category=case.get("segment"))
        decision = str(public.get("permit_decision") or "").upper().strip()
        required_rows = [row for row in public.get("permits_required") or [] if isinstance(row, dict)]
        visible_rows = _visible_rows(public)
        required_families = sorted({
            _canonical_family(server._customer_row_family(row) or row.get("filing_family") or row.get("family") or "")
            for row in required_rows
            if str(server._customer_row_status(row) or row.get("status") or row.get("decision") or "").upper() == "REQUIRED" or row.get("required") is True
        })
        visible_families = sorted({_canonical_family(server._customer_row_family(row) or row.get("filing_family") or row.get("family") or "") for row in visible_rows})
        base = baseline["cases"][cid]
        issues: list[dict[str, str]] = []
        if decision not in {"REQUIRED", "NOT_REQUIRED"}:
            issues.append({"code": "bad_main_decision", "message": decision})
        if decision != base["decision"]:
            issues.append({"code": "decision_regression", "message": f"{base['decision']} -> {decision}"})
        if required_families != base["required_families"]:
            issues.append({"code": "required_family_regression", "message": f"baseline={base['required_families']} current={required_families}"})
        if visible_families != base["visible_families"]:
            issues.append({"code": "visible_family_regression", "message": f"baseline={base['visible_families']} current={visible_families}"})
        if decision == "REQUIRED" and not required_rows:
            issues.append({"code": "required_without_rows", "message": "REQUIRED with no rows"})
        timeline_text = json.dumps(public.get("approval_timeline") or {}, sort_keys=True)
        if COMMERCIAL_TIMELINE.lower() in timeline_text.lower():
            observed_timeline_positive.add(cid)
        if cid in RESIDENTIAL_TIMELINE_FALSE_POSITIVES and COMMERCIAL_TIMELINE.lower() in timeline_text.lower():
            issues.append({"code": "residential_commercial_timeline", "message": "Residential report still has commercial timeline"})
        public_text = json.dumps(public, ensure_ascii=False, sort_keys=True, default=str)
        if SECRET_RE.search(public_text) or INTERNAL_RE.search(public_text):
            issues.append({"code": "public_secret_or_internal_leak", "message": "Secret/internal token pattern found"})

        apply_path = public.get("apply_path") if isinstance(public.get("apply_path"), dict) else {}
        primary_urls = [u for u in [public.get("apply_url"), public.get("online_application_url"), apply_path.get("portal_url"), apply_path.get("url"), apply_path.get("source_url")] if u]
        broken_expected = BROKEN_APPLY_BY_CASE.get(cid, "")
        broken_primary = bool(broken_expected and any(_url_key(str(u)) == _url_key(broken_expected) for u in primary_urls))
        source_urls = [u for u in public.get("source_urls") or [] if isinstance(u, str)]
        source_support = public.get("source_support") if isinstance(public.get("source_support"), dict) else {}
        status = source_support.get("filing_path_reachability") or ("broken" if broken_primary else "unknown")
        if decision == "REQUIRED" and broken_primary:
            issues.append({"code": "broken_primary_action_path", "message": broken_expected})
        if decision == "REQUIRED" and cid in KNOWN_C_ACTION_PATH_CASES and not (primary_urls or source_urls):
            issues.append({"code": "known_c_still_missing_action_path", "message": "No primary/source URL"})
        for issue in issues:
            issue_counts[issue["code"]] += 1

        share = {
            "slug": cid,
            "job_type": case["job_type"],
            "city": case["city"],
            "state": case["state"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "data": public,
        }
        html = server.render_share_page(share)
        html_path = HTML_DIR / f"{idx:03d}_{cid}_{case.get('segment')}_{case.get('city')}_{case.get('state')}.html".replace(" ", "_").replace("/", "-")
        html_path.write_text(html, encoding="utf-8")
        screenshot_path = SCREENSHOT_DIR / (html_path.stem + ".png")
        render_manifest.append({"case_id": cid, "html_path": str(html_path), "screenshot_path": str(screenshot_path), "segment": case.get("segment"), "city": case.get("city"), "state": case.get("state"), "decision": decision})

        cases_out.append({
            "case_id": cid,
            "segment": case.get("segment"),
            "city": case.get("city"),
            "state": case.get("state"),
            "bucket": case.get("bucket"),
            "decision": decision,
            "baseline_decision": base["decision"],
            "required_families": required_families,
            "baseline_required_families": base["required_families"],
            "visible_families": visible_families,
            "baseline_visible_families": base["visible_families"],
            "apply_url": public.get("apply_url"),
            "source_urls": source_urls,
            "source_support": source_support,
            "has_commercial_timeline": cid in observed_timeline_positive,
            "issues": issues,
            "html_path": str(html_path),
            "screenshot_path": str(screenshot_path),
        })
        url_rows.append({
            "case_id": cid,
            "decision": decision,
            "apply_url": public.get("apply_url") or "",
            "source_url_count": len(source_urls),
            "broken_baseline_apply_url": broken_expected,
            "broken_primary_after_fix": broken_primary,
            "filing_path_status": status,
            "filing_path_evidence": source_support.get("filing_path_evidence", ""),
            "filing_path_verified_on": source_support.get("filing_path_verified_on", ""),
        })

    missing_timeline = sorted(expected_timeline_positive - observed_timeline_positive)
    for case in cases_out:
        if case["case_id"] in missing_timeline:
            case["issues"].append({"code": "commercial_timeline_regression", "message": "Was baseline commercial timeline positive but no longer is"})
            issue_counts["commercial_timeline_regression"] += 1

    failed = [case for case in cases_out if case["issues"]]
    summary = {
        "artifact_root": str(ARTIFACT_ROOT),
        "out_root": str(OUT_ROOT),
        "total": len(cases_out),
        "pass": len(cases_out) - len(failed),
        "fail": len(failed),
        "failed_case_ids": [case["case_id"] for case in failed],
        "issue_counts": dict(sorted(issue_counts.items())),
        "observed_commercial_timeline_positive_count": len(observed_timeline_positive),
        "observed_commercial_timeline_positive_case_ids": sorted(observed_timeline_positive),
        "expected_commercial_timeline_positive_case_ids": sorted(expected_timeline_positive),
        "residential_timeline_false_positives_remaining": sorted(RESIDENTIAL_TIMELINE_FALSE_POSITIVES & observed_timeline_positive),
        "missing_commercial_timeline_positives": missing_timeline,
        "known_c_action_path_cases_fixed": [row["case_id"] for row in url_rows if row["case_id"] in KNOWN_C_ACTION_PATH_CASES and row["decision"] == "REQUIRED" and row["apply_url"] and not row["broken_primary_after_fix"]],
        "required_cases_with_broken_primary_action_path": [row["case_id"] for row in url_rows if row["decision"] == "REQUIRED" and row["broken_primary_after_fix"]],
    }

    (OUT_ROOT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUT_ROOT / "public_cases.jsonl").write_text("".join(json.dumps(case, sort_keys=True, ensure_ascii=False) + "\n" for case in cases_out), encoding="utf-8")
    (OUT_ROOT / "url_status.json").write_text(json.dumps(url_rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (OUT_ROOT / "url_status.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(url_rows[0].keys()))
        writer.writeheader()
        writer.writerows(url_rows)
    (OUT_ROOT / "local_render_manifest.json").write_text(json.dumps(render_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md = [
        "# Live100 no-neuter action-path replay",
        "",
        f"- Artifact root: `{ARTIFACT_ROOT}`",
        f"- Output root: `{OUT_ROOT}`",
        f"- Pass/fail: **{summary['pass']}/{summary['fail']}** of {summary['total']}",
        f"- Issue counts: `{json.dumps(summary['issue_counts'], sort_keys=True)}`",
        f"- Required cases with broken primary action path: `{', '.join(summary['required_cases_with_broken_primary_action_path']) or 'none'}`",
        f"- Residential timeline false positives remaining: `{', '.join(summary['residential_timeline_false_positives_remaining']) or 'none'}`",
        f"- Missing commercial timeline positives: `{', '.join(summary['missing_commercial_timeline_positives']) or 'none'}`",
        f"- Known C action-path cases fixed: `{', '.join(summary['known_c_action_path_cases_fixed'])}`",
        "",
        "## Failed cases",
    ]
    if failed:
        for case in failed:
            md.append(f"- **{case['case_id']}**: " + "; ".join(issue["code"] for issue in case["issues"]))
    else:
        md.append("- none")
    (OUT_ROOT / "NO_NEUTER_DIFF_REPORT.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
