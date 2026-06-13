#!/usr/bin/env python3
"""PermitAssist live customer E2E sweep runner.

Writes one JSONL evidence row per case plus summary/failure/report artifacts.
Does not persist auth tokens in output artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://permitassist.io"
TOKEN_PATH = Path("/home/boban/.cache/permitassist_customer_e2e_session_token")
ARTIFACT_DIR = Path("/home/boban/projects/permitassist/artifacts/customer_e2e_20260612T231212Z")
EVIDENCE_PATH = ARTIFACT_DIR / "evidence.jsonl"
SUMMARY_PATH = ARTIFACT_DIR / "summary.json"
FAILURES_PATH = ARTIFACT_DIR / "failures.json"
BASELINE_PATH = ARTIFACT_DIR / "baseline.json"
REPORT_PATH = ARTIFACT_DIR / "report.md"

CLIENT_FINGERPRINT = "customer-e2e-20260612-boban-paid-session"
TIMEOUT_SECONDS = 160
SLEEP_BETWEEN_CASES_SECONDS = 1.0

SENSITIVE_KEYS = {"token", "authorization", "x-session-token", "session"}
INTERNAL_KEY_ALLOWLIST = set()  # Customer API should not expose _-prefixed fields.
DEBUG_TOKEN_RE = re.compile(
    r"(traceback|Traceback|/(?:home|app)/[A-Za-z0-9_.-]+/|RAILWAY_|railway_|PERMITASSIST_ADMIN_TOKEN|permitassist_admin_token|benchmark_secret|BENCHMARK_SECRET|sk-[A-Za-z0-9_-]{12,}|AIza[0-9A-Za-z_-]{20,})"
)

CASES: list[dict[str, Any]] = [
    # Residential — Arizona priority
    {"id":"R-AZ-001","segment":"residential","city":"Phoenix","state":"AZ","zip_code":"85004","job_type":"replace a 3 ton split system condenser and air handler like for like, job value 8500","job_value":8500,"expected_decision":"REQUIRED"},
    {"id":"R-AZ-002","segment":"residential","city":"Phoenix","state":"AZ","zip_code":"85016","job_type":"install rooftop solar PV with battery backup on a single family home, job value 32000","job_value":32000,"expected_decision":"REQUIRED"},
    {"id":"R-AZ-003","segment":"residential","city":"Phoenix","state":"AZ","zip_code":"85018","job_type":"interior repaint and replace carpet only, no electrical, no plumbing, no walls, job value 4500","job_value":4500,"expected_decision":"NOT_REQUIRED"},
    {"id":"R-AZ-004","segment":"residential","city":"Tucson","state":"AZ","zip_code":"85701","job_type":"replace gas water heater in same location, job value 2800","job_value":2800,"expected_decision":"REQUIRED"},
    {"id":"R-AZ-005","segment":"residential","city":"Mesa","state":"AZ","zip_code":"85201","job_type":"200 amp electrical panel upgrade for single family home, job value 6500","job_value":6500,"expected_decision":"REQUIRED"},
    {"id":"R-AZ-006","segment":"residential","city":"Scottsdale","state":"AZ","zip_code":"85251","job_type":"build a new 420 square foot attached patio cover, job value 18000","job_value":18000,"expected_decision":"REQUIRED"},
    {"id":"R-AZ-007","segment":"residential","city":"Tempe","state":"AZ","zip_code":"85281","job_type":"install level 2 EV charger with new 50 amp circuit in garage, job value 2200","job_value":2200,"expected_decision":"REQUIRED"},
    {"id":"R-AZ-008","segment":"residential","city":"Chandler","state":"AZ","zip_code":"85225","job_type":"tear off and reroof asphalt shingles on single family house, job value 14000","job_value":14000,"expected_decision":"REQUIRED"},
    {"id":"R-AZ-009","segment":"residential","city":"Gilbert","state":"AZ","zip_code":"85234","job_type":"install new in ground swimming pool and spa in backyard, job value 85000","job_value":85000,"expected_decision":"REQUIRED"},
    {"id":"R-AZ-010","segment":"residential","city":"Flagstaff","state":"AZ","zip_code":"86001","job_type":"replace kitchen faucet and garbage disposal, no pipe relocation, job value 900","job_value":900,"expected_decision":"NOT_REQUIRED"},
    # Residential — national mix
    {"id":"R-US-011","segment":"residential","city":"Houston","state":"TX","zip_code":"77002","job_type":"replace central air conditioner condenser and coil like for like, job value 7800","job_value":7800,"expected_decision":"REQUIRED"},
    {"id":"R-US-012","segment":"residential","city":"Dallas","state":"TX","zip_code":"75201","job_type":"convert garage into bedroom with new window, insulation, electrical outlets, job value 42000","job_value":42000,"expected_decision":"REQUIRED"},
    {"id":"R-US-013","segment":"residential","city":"Austin","state":"TX","zip_code":"78701","job_type":"replace 40 gallon electric water heater same size same location, job value 2400","job_value":2400,"expected_decision":"REQUIRED"},
    {"id":"R-US-014","segment":"residential","city":"Los Angeles","state":"CA","zip_code":"90012","job_type":"install residential solar PV and main panel upgrade, job value 38000","job_value":38000,"expected_decision":"REQUIRED"},
    {"id":"R-US-015","segment":"residential","city":"San Diego","state":"CA","zip_code":"92101","job_type":"bathroom remodel moving shower drain and adding exhaust fan, job value 24000","job_value":24000,"expected_decision":"REQUIRED"},
    {"id":"R-US-016","segment":"residential","city":"Denver","state":"CO","zip_code":"80202","job_type":"replace existing asphalt shingles after hail damage, job value 16000","job_value":16000,"expected_decision":"REQUIRED"},
    {"id":"R-US-017","segment":"residential","city":"Seattle","state":"WA","zip_code":"98104","job_type":"build detached backyard deck 24 inches above grade, job value 12000","job_value":12000,"expected_decision":"REQUIRED"},
    {"id":"R-US-018","segment":"residential","city":"Portland","state":"OR","zip_code":"97204","job_type":"install ductless mini split heat pump with exterior condenser, job value 9500","job_value":9500,"expected_decision":"REQUIRED"},
    {"id":"R-US-019","segment":"residential","city":"Las Vegas","state":"NV","zip_code":"89101","job_type":"replace toilet and vanity cabinet only, no plumbing relocation, job value 1800","job_value":1800,"expected_decision":"NOT_REQUIRED"},
    {"id":"R-US-020","segment":"residential","city":"Atlanta","state":"GA","zip_code":"30303","job_type":"finish basement with new bedroom bathroom electrical and HVAC, job value 65000","job_value":65000,"expected_decision":"REQUIRED"},
    {"id":"R-US-021","segment":"residential","city":"Miami","state":"FL","zip_code":"33130","job_type":"replace impact windows and exterior doors, job value 28000","job_value":28000,"expected_decision":"REQUIRED"},
    {"id":"R-US-022","segment":"residential","city":"Orlando","state":"FL","zip_code":"32801","job_type":"install standby generator with automatic transfer switch and gas line, job value 15000","job_value":15000,"expected_decision":"REQUIRED"},
    {"id":"R-US-023","segment":"residential","city":"Chicago","state":"IL","zip_code":"60602","job_type":"replace drywall in one bedroom after leak, no structural, no electrical, job value 3500","job_value":3500,"expected_decision":"NOT_REQUIRED"},
    {"id":"R-US-024","segment":"residential","city":"New York","state":"NY","zip_code":"10007","job_type":"apartment bathroom renovation replacing tub, valve, lighting and exhaust fan, job value 36000","job_value":36000,"expected_decision":"REQUIRED"},
    {"id":"R-US-025","segment":"residential","city":"Nashville","state":"TN","zip_code":"37219","job_type":"add 12 by 16 screened porch attached to house, job value 22000","job_value":22000,"expected_decision":"REQUIRED"},
    {"id":"R-US-026","segment":"residential","city":"Charlotte","state":"NC","zip_code":"28202","job_type":"replace kitchen cabinets and countertops only, no plumbing or electrical, job value 18000","job_value":18000,"expected_decision":"NOT_REQUIRED"},
    {"id":"R-US-027","segment":"residential","city":"Columbus","state":"OH","zip_code":"43215","job_type":"replace furnace and air conditioner like for like, job value 11000","job_value":11000,"expected_decision":"REQUIRED"},
    {"id":"R-US-028","segment":"residential","city":"Salt Lake City","state":"UT","zip_code":"84111","job_type":"basement ADU conversion with new kitchen bathroom egress windows, job value 95000","job_value":95000,"expected_decision":"REQUIRED"},
    {"id":"R-US-029","segment":"residential","city":"Minneapolis","state":"MN","zip_code":"55415","job_type":"install wood burning fireplace insert and chimney liner, job value 7200","job_value":7200,"expected_decision":"REQUIRED"},
    {"id":"R-US-030","segment":"residential","city":"Boston","state":"MA","zip_code":"02108","job_type":"replace existing exterior siding on two family house, job value 26000","job_value":26000,"expected_decision":"REQUIRED"},

    # Commercial — Arizona priority
    {"id":"C-AZ-031","segment":"commercial","city":"Phoenix","state":"AZ","zip_code":"85004","job_type":"commercial restaurant tenant improvement adding Type I hood, gas line, grease interceptor, electrical panel, job value 180000","job_value":180000,"expected_decision":"REQUIRED"},
    {"id":"C-AZ-032","segment":"commercial","city":"Phoenix","state":"AZ","zip_code":"85012","job_type":"commercial office tenant improvement with non load bearing partitions, lighting, receptacles and HVAC diffuser relocation, job value 95000","job_value":95000,"expected_decision":"REQUIRED"},
    {"id":"C-AZ-033","segment":"commercial","city":"Phoenix","state":"AZ","zip_code":"85016","job_type":"commercial retail repaint and carpet replacement only, no walls, no electrical, no plumbing, job value 12000","job_value":12000,"expected_decision":"NOT_REQUIRED"},
    {"id":"C-AZ-034","segment":"commercial","city":"Mesa","state":"AZ","zip_code":"85201","job_type":"commercial medical clinic tenant improvement with exam rooms, plumbing sinks, med gas coordination, job value 240000","job_value":240000,"expected_decision":"REQUIRED"},
    {"id":"C-AZ-035","segment":"commercial","city":"Scottsdale","state":"AZ","zip_code":"85251","job_type":"commercial salon buildout adding shampoo bowls, water heater, electrical and ventilation, job value 160000","job_value":160000,"expected_decision":"REQUIRED"},
    {"id":"C-AZ-036","segment":"commercial","city":"Tucson","state":"AZ","zip_code":"85701","job_type":"warehouse high pile storage racking over 12 feet with fire sprinkler evaluation, job value 130000","job_value":130000,"expected_decision":"REQUIRED"},
    {"id":"C-AZ-037","segment":"commercial","city":"Tempe","state":"AZ","zip_code":"85281","job_type":"commercial rooftop package HVAC unit replacement same tonnage, job value 28000","job_value":28000,"expected_decision":"REQUIRED"},
    {"id":"C-AZ-038","segment":"commercial","city":"Chandler","state":"AZ","zip_code":"85225","job_type":"commercial EV charging stations in parking lot with new electrical service equipment, job value 210000","job_value":210000,"expected_decision":"REQUIRED"},
    {"id":"C-AZ-039","segment":"commercial","city":"Glendale","state":"AZ","zip_code":"85301","job_type":"change of occupancy from retail store to fitness studio with showers and new mechanical ventilation, job value 175000","job_value":175000,"expected_decision":"REQUIRED"},
    {"id":"C-AZ-040","segment":"commercial","city":"Gilbert","state":"AZ","zip_code":"85234","job_type":"install monument sign and illuminated wall sign for retail tenant, job value 22000","job_value":22000,"expected_decision":"REQUIRED"},
    # Commercial — national mix
    {"id":"C-US-041","segment":"commercial","city":"Houston","state":"TX","zip_code":"77002","job_type":"commercial restaurant second generation buildout adding hood suppression grease interceptor and gas appliances, job value 260000","job_value":260000,"expected_decision":"REQUIRED"},
    {"id":"C-US-042","segment":"commercial","city":"Dallas","state":"TX","zip_code":"75201","job_type":"commercial office suite refresh repaint carpet only no wall changes no MEP, job value 22000","job_value":22000,"expected_decision":"NOT_REQUIRED"},
    {"id":"C-US-043","segment":"commercial","city":"Austin","state":"TX","zip_code":"78701","job_type":"commercial dental office tenant improvement with exam rooms, nitrous lines, plumbing sinks and X ray equipment, job value 310000","job_value":310000,"expected_decision":"REQUIRED"},
    {"id":"C-US-044","segment":"commercial","city":"Los Angeles","state":"CA","zip_code":"90012","job_type":"commercial retail tenant improvement adding partition walls, storefront door, electrical lighting and restroom upgrades, job value 190000","job_value":190000,"expected_decision":"REQUIRED"},
    {"id":"C-US-045","segment":"commercial","city":"San Diego","state":"CA","zip_code":"92101","job_type":"commercial kitchen hood replacement with fire suppression and makeup air, job value 125000","job_value":125000,"expected_decision":"REQUIRED"},
    {"id":"C-US-046","segment":"commercial","city":"Denver","state":"CO","zip_code":"80202","job_type":"brewery tenant improvement adding floor drains, gas fired equipment, fermentation tanks and electrical, job value 420000","job_value":420000,"expected_decision":"REQUIRED"},
    {"id":"C-US-047","segment":"commercial","city":"Seattle","state":"WA","zip_code":"98104","job_type":"commercial office demising wall and new conference rooms with lighting controls and HVAC balancing, job value 145000","job_value":145000,"expected_decision":"REQUIRED"},
    {"id":"C-US-048","segment":"commercial","city":"Portland","state":"OR","zip_code":"97204","job_type":"change use from warehouse to indoor pickleball facility with occupant load increase, job value 300000","job_value":300000,"expected_decision":"REQUIRED"},
    {"id":"C-US-049","segment":"commercial","city":"Las Vegas","state":"NV","zip_code":"89101","job_type":"commercial hotel lobby remodel with new bar sink, lighting, finishes and non structural partitions, job value 380000","job_value":380000,"expected_decision":"REQUIRED"},
    {"id":"C-US-050","segment":"commercial","city":"Atlanta","state":"GA","zip_code":"30303","job_type":"commercial daycare tenant improvement with classrooms, toilets, kitchen warming area and fenced play yard, job value 280000","job_value":280000,"expected_decision":"REQUIRED"},
    {"id":"C-US-051","segment":"commercial","city":"Miami","state":"FL","zip_code":"33130","job_type":"commercial condo lobby impact storefront replacement and electrical lighting upgrades, job value 210000","job_value":210000,"expected_decision":"REQUIRED"},
    {"id":"C-US-052","segment":"commercial","city":"Orlando","state":"FL","zip_code":"32801","job_type":"commercial rooftop solar PV system on warehouse with service upgrade, job value 520000","job_value":520000,"expected_decision":"REQUIRED"},
    {"id":"C-US-053","segment":"commercial","city":"Chicago","state":"IL","zip_code":"60602","job_type":"commercial medical office tenant improvement with procedure rooms, plumbing, electrical and ventilation, job value 360000","job_value":360000,"expected_decision":"REQUIRED"},
    {"id":"C-US-054","segment":"commercial","city":"New York","state":"NY","zip_code":"10007","job_type":"commercial restaurant alteration adding cooking equipment, hood, ductwork, gas and fire alarm tie in, job value 650000","job_value":650000,"expected_decision":"REQUIRED"},
    {"id":"C-US-055","segment":"commercial","city":"Nashville","state":"TN","zip_code":"37219","job_type":"commercial bar tenant improvement adding plumbing fixtures, electrical panel, hoodless warming equipment and occupancy change, job value 225000","job_value":225000,"expected_decision":"REQUIRED"},
    {"id":"C-US-056","segment":"commercial","city":"Charlotte","state":"NC","zip_code":"28202","job_type":"commercial shell building interior first generation upfit for retail with restroom and HVAC, job value 410000","job_value":410000,"expected_decision":"REQUIRED"},
    {"id":"C-US-057","segment":"commercial","city":"Columbus","state":"OH","zip_code":"43215","job_type":"commercial warehouse LED lighting retrofit only using existing circuits no new panels, job value 48000","job_value":48000,"expected_decision":"REQUIRED"},
    {"id":"C-US-058","segment":"commercial","city":"Salt Lake City","state":"UT","zip_code":"84111","job_type":"commercial industrial equipment platform with structural steel and electrical connections, job value 275000","job_value":275000,"expected_decision":"REQUIRED"},
    {"id":"C-US-059","segment":"commercial","city":"Minneapolis","state":"MN","zip_code":"55415","job_type":"commercial exterior wall sign face change only no electrical work, job value 4500","job_value":4500,"expected_decision":"REQUIRED"},
    {"id":"C-US-060","segment":"commercial","city":"Boston","state":"MA","zip_code":"02108","job_type":"commercial lab tenant improvement adding fume hoods, exhaust, gas piping, emergency power and sinks, job value 780000","job_value":780000,"expected_decision":"REQUIRED"},
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_token() -> str:
    token = TOKEN_PATH.read_text().strip()
    if not token or len(token) < 20:
        raise RuntimeError(f"Missing session token in {TOKEN_PATH}")
    return token


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            kl = str(k).lower()
            if kl in SENSITIVE_KEYS:
                out[k] = "[REDACTED]"
            else:
                out[k] = sanitize(v)
        return out
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"pa_session['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9._-]{20,}", "pa_session=[REDACTED]", value)
    return value


def find_internal_keys(value: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            key = str(k)
            new_path = f"{path}.{key}" if path else key
            if key.startswith("_") and key not in INTERNAL_KEY_ALLOWLIST:
                hits.append(new_path)
            hits.extend(find_internal_keys(v, new_path))
    elif isinstance(value, list):
        for i, item in enumerate(value[:20]):
            hits.extend(find_internal_keys(item, f"{path}[{i}]"))
    return hits


def debug_leaks(value: Any) -> list[str]:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return sorted(set(m.group(0)[:120] for m in DEBUG_TOKEN_RE.finditer(raw)))


def source_urls(data: dict[str, Any]) -> list[str]:
    urls = []
    for u in data.get("source_urls") or []:
        if isinstance(u, str) and u.startswith("http"):
            urls.append(u)
    for s in data.get("sources") or []:
        if isinstance(s, dict) and isinstance(s.get("url"), str) and s["url"].startswith("http"):
            urls.append(s["url"])
    return sorted(set(urls))


def classify(case: dict[str, Any], status: int, data: Any, elapsed: float) -> tuple[str, list[dict[str, str]]]:
    issues: list[dict[str, str]] = []
    if status != 200:
        issues.append({"severity":"P0","code":"http_non_200","message":f"HTTP {status}"})
        return "FAIL", issues
    if not isinstance(data, dict):
        issues.append({"severity":"P0","code":"non_json_object","message":"Response was not a JSON object"})
        return "FAIL", issues
    if data.get("error"):
        issues.append({"severity":"P0","code":"api_error_field","message":str(data.get("error"))[:200]})
    decision = str(data.get("permit_decision") or "").strip().upper()
    if decision not in {"REQUIRED", "NOT_REQUIRED"}:
        issues.append({"severity":"P0","code":"bad_decision_contract","message":f"permit_decision={decision!r}"})
    verdict = str(data.get("permit_verdict") or "").strip().upper()
    if verdict not in {"YES", "NO"}:
        issues.append({"severity":"P1","code":"bad_verdict_contract","message":f"permit_verdict={verdict!r}"})
    expected = case.get("expected_decision")
    if expected and decision in {"REQUIRED", "NOT_REQUIRED"} and decision != expected:
        issues.append({"severity":"P1","code":"expectation_mismatch","message":f"expected {expected}, got {decision}"})
    if isinstance(data.get("permit_required"), bool):
        if decision == "REQUIRED" and data["permit_required"] is not True:
            issues.append({"severity":"P1","code":"decision_bool_mismatch","message":"REQUIRED but permit_required is not true"})
        if decision == "NOT_REQUIRED" and data["permit_required"] is not False:
            issues.append({"severity":"P1","code":"decision_bool_mismatch","message":"NOT_REQUIRED but permit_required is not false"})
    else:
        issues.append({"severity":"P1","code":"missing_bool","message":"permit_required missing or non-boolean"})
    customer_scope_text = " ".join(str(data.get(field) or "") for field in ("permit_name", "customer_headline", "customer_next_step", "permit_kind")).lower()
    customer_scope_text_for_residential_check = customer_scope_text.replace("non-residential", "").replace("nonresidential", "")
    if case.get("segment") == "residential" and any(term in customer_scope_text for term in ("commercial", "tenant improvement", "tenant-improvement")):
        issues.append({"severity":"P1","code":"segment_scope_leak","message":"Residential case surfaced commercial/TI permit wording"})
    if case.get("segment") == "commercial" and "residential" in customer_scope_text_for_residential_check:
        issues.append({"severity":"P1","code":"segment_scope_leak","message":"Commercial case surfaced residential permit wording"})
    for field in ("permit_name", "customer_headline", "customer_next_step"):
        if not str(data.get(field) or "").strip():
            issues.append({"severity":"P1","code":"missing_customer_field","message":field})
    urls = source_urls(data)
    if decision == "REQUIRED":
        if not str(data.get("apply_url") or "").startswith("http"):
            issues.append({"severity":"P1","code":"missing_apply_url","message":"Required result missing apply_url"})
        if not urls:
            issues.append({"severity":"P1","code":"missing_sources","message":"Required result missing source URLs"})
    if str(data.get("apply_url") or "").startswith("http://"):
        issues.append({"severity":"P2","code":"insecure_apply_url","message":str(data.get("apply_url"))[:200]})
    internal = find_internal_keys(data)
    if internal:
        issues.append({"severity":"P1","code":"internal_key_leak","message":", ".join(internal[:12])})
    leaks = debug_leaks(data)
    if leaks:
        issues.append({"severity":"P0","code":"debug_or_secret_leak","message":", ".join(leaks[:5])})
    if elapsed > 75:
        issues.append({"severity":"P2","code":"slow_response","message":f"{elapsed:.1f}s"})
    status_label = "PASS" if not any(i["severity"] in {"P0", "P1"} for i in issues) else "FAIL"
    return status_label, issues


def save_summary(records: list[dict[str, Any]], final: bool = False) -> None:
    failures = [r for r in records if r.get("status_label") != "PASS"]
    issue_counts: dict[str, int] = {}
    segment_counts: dict[str, dict[str, int]] = {}
    decisions: dict[str, int] = {}
    for r in records:
        seg = r.get("case", {}).get("segment", "unknown")
        segment_counts.setdefault(seg, {"total": 0, "pass": 0, "fail": 0})
        segment_counts[seg]["total"] += 1
        segment_counts[seg]["pass" if r.get("status_label") == "PASS" else "fail"] += 1
        d = str(r.get("key_fields", {}).get("permit_decision") or "")
        decisions[d] = decisions.get(d, 0) + 1
        for issue in r.get("issues", []):
            issue_counts[issue["code"]] = issue_counts.get(issue["code"], 0) + 1
    elapsed_values = [r.get("elapsed_seconds", 0.0) for r in records if isinstance(r.get("elapsed_seconds"), (int, float))]
    summary = {
        "run_id": ARTIFACT_DIR.name,
        "base_url": BASE_URL,
        "started_at": records[0]["started_at"] if records else None,
        "updated_at": utc_now(),
        "final": final,
        "requested_range": "50-100 customer E2E cases",
        "planned_cases": len(CASES),
        "completed": len(records),
        "pass": len(records) - len(failures),
        "fail": len(failures),
        "segment_counts": segment_counts,
        "decision_counts": decisions,
        "issue_counts": dict(sorted(issue_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "elapsed_seconds_avg": round(sum(elapsed_values) / len(elapsed_values), 2) if elapsed_values else None,
        "elapsed_seconds_max": round(max(elapsed_values), 2) if elapsed_values else None,
        "artifacts": {
            "evidence_jsonl": str(EVIDENCE_PATH),
            "failures_json": str(FAILURES_PATH),
            "summary_json": str(SUMMARY_PATH),
            "report_md": str(REPORT_PATH),
            "baseline_json": str(BASELINE_PATH),
        },
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, sort_keys=True))
    FAILURES_PATH.write_text(json.dumps(failures, indent=2, sort_keys=True))
    write_report(summary, failures, records)


def write_report(summary: dict[str, Any], failures: list[dict[str, Any]], records: list[dict[str, Any]]) -> None:
    lines = []
    lines.append(f"# PermitAssist Customer E2E Sweep — {summary['run_id']}\n")
    lines.append(f"- Base URL: {BASE_URL}")
    lines.append(f"- Completed: {summary['completed']} / {summary['planned_cases']}")
    lines.append(f"- Pass: {summary['pass']}")
    lines.append(f"- Fail: {summary['fail']}")
    lines.append(f"- Segment counts: `{json.dumps(summary['segment_counts'], sort_keys=True)}`")
    lines.append(f"- Decision counts: `{json.dumps(summary['decision_counts'], sort_keys=True)}`")
    lines.append(f"- Issue counts: `{json.dumps(summary['issue_counts'], sort_keys=True)}`")
    lines.append(f"- Avg/max latency seconds: {summary['elapsed_seconds_avg']} / {summary['elapsed_seconds_max']}")
    lines.append("\n## Failure ledger\n")
    if not failures:
        lines.append("No P0/P1 failures detected.\n")
    else:
        for r in failures[:80]:
            c = r["case"]
            issues = "; ".join(f"{i['severity']} {i['code']}: {i['message']}" for i in r.get("issues", []))
            key = r.get("key_fields", {})
            lines.append(f"- {c['id']} {c['segment']} {c['city']}, {c['state']} — decision={key.get('permit_decision')} expected={c.get('expected_decision')} elapsed={r.get('elapsed_seconds')}s — {issues}")
    lines.append("\n## Sample successful customer outputs\n")
    for r in [x for x in records if x.get("status_label") == "PASS"][:8]:
        c = r["case"]
        k = r.get("key_fields", {})
        lines.append(f"- {c['id']} {c['city']}, {c['state']} — {k.get('permit_decision')} — {k.get('permit_name')} — apply={k.get('apply_url')}")
    REPORT_PATH.write_text("\n".join(lines) + "\n")


def do_baseline(session: requests.Session, token: str) -> dict[str, Any]:
    baseline: dict[str, Any] = {"started_at": utc_now(), "checks": {}}
    h = session.get(f"{BASE_URL}/health", timeout=30)
    baseline["checks"]["health"] = {"status_code": h.status_code, "body": sanitize(h.json() if h.text.startswith("{") else h.text[:500])}
    a = session.get(f"{BASE_URL}/api/account", headers={"X-Session-Token": token}, timeout=30)
    baseline["checks"]["account"] = {"status_code": a.status_code, "body": sanitize(a.json() if a.text.startswith("{") else a.text[:500])}
    BASELINE_PATH.write_text(json.dumps(baseline, indent=2, sort_keys=True))
    return baseline


def run_case(session: requests.Session, token: str, case: dict[str, Any], attempt: int) -> dict[str, Any]:
    payload = {
        "job_type": case["job_type"],
        "city": case["city"],
        "state": case["state"],
        "zip_code": case.get("zip_code", ""),
        "job_category": case["segment"],
        "vertical": case["segment"],
        "job_value": case.get("job_value"),
    }
    headers = {
        "Content-Type": "application/json",
        "X-Session-Token": token,
        "X-Client-Fingerprint": CLIENT_FINGERPRINT,
    }
    started_at = utc_now()
    t0 = time.time()
    err_text = None
    status_code = None
    data: Any = None
    response_text = ""
    try:
        resp = session.post(f"{BASE_URL}/api/permit", json=payload, headers=headers, timeout=TIMEOUT_SECONDS)
        elapsed = time.time() - t0
        status_code = resp.status_code
        response_text = resp.text
        try:
            data = resp.json()
        except Exception:
            data = {"_non_json_text": resp.text[:2000]}
    except Exception as e:
        elapsed = time.time() - t0
        err_text = f"{type(e).__name__}: {e}"
        data = {"error": err_text}
        status_code = 0
    status_label, issues = classify(case, int(status_code or 0), data, elapsed)
    full_body = sanitize(data)
    record = {
        "run_id": ARTIFACT_DIR.name,
        "started_at": started_at,
        "completed_at": utc_now(),
        "attempt": attempt,
        "case": case,
        "request_payload": payload,
        "http_status": status_code,
        "elapsed_seconds": round(elapsed, 2),
        "response_sha256": hashlib.sha256(response_text.encode("utf-8", errors="ignore")).hexdigest() if response_text else None,
        "status_label": status_label,
        "issues": issues,
        "key_fields": {
            "permit_decision": data.get("permit_decision") if isinstance(data, dict) else None,
            "permit_required": data.get("permit_required") if isinstance(data, dict) else None,
            "permit_verdict": data.get("permit_verdict") if isinstance(data, dict) else None,
            "confidence": data.get("confidence") if isinstance(data, dict) else None,
            "permit_name": data.get("permit_name") if isinstance(data, dict) else None,
            "fee_range": data.get("fee_range") if isinstance(data, dict) else None,
            "apply_url": data.get("apply_url") if isinstance(data, dict) else None,
            "applying_office": data.get("applying_office") if isinstance(data, dict) else None,
            "sources_count": len(source_urls(data)) if isinstance(data, dict) else 0,
            "remaining_lookups": data.get("remaining_lookups") if isinstance(data, dict) else None,
            "customer_headline": data.get("customer_headline") if isinstance(data, dict) else None,
            "customer_next_step": data.get("customer_next_step") if isinstance(data, dict) else None,
        },
        "response_body": full_body,
    }
    return record


def load_existing_records() -> list[dict[str, Any]]:
    records = []
    if EVIDENCE_PATH.exists():
        for line in EVIDENCE_PATH.read_text().splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int, default=len(CASES))
    args = parser.parse_args()

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    token = load_token()
    session = requests.Session()
    baseline = do_baseline(session, token)
    account_body = baseline.get("checks", {}).get("account", {}).get("body", {})
    if baseline["checks"]["health"]["status_code"] != 200:
        raise SystemExit("Health baseline failed")
    if baseline["checks"]["account"]["status_code"] != 200 or not account_body.get("paid"):
        raise SystemExit(f"Account baseline failed or not paid: {account_body}")

    records = load_existing_records() if args.resume else []
    done_ids = {r.get("case", {}).get("id") for r in records if r.get("case")}
    mode = "a" if args.resume else "w"
    total_to_run = min(args.limit, len(CASES))
    print(f"RUN {ARTIFACT_DIR.name}: baseline ok, plan={total_to_run}, already_done={len(done_ids)}", flush=True)
    with EVIDENCE_PATH.open(mode) as f:
        for case in CASES[:total_to_run]:
            if case["id"] in done_ids:
                continue
            record = None
            for attempt in range(1, 4):
                record = run_case(session, token, case, attempt)
                retryable = record["http_status"] in {0, 408, 409, 429, 500, 502, 503, 504}
                if not retryable or attempt == 3:
                    break
                wait = 6 * attempt
                print(f"{case['id']} retryable status={record['http_status']} attempt={attempt}; sleeping {wait}s", flush=True)
                time.sleep(wait)
            assert record is not None
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
            records.append(record)
            done = len(records)
            issues = ",".join(i["code"] for i in record.get("issues", [])) or "none"
            k = record["key_fields"]
            print(f"{done:03d}/{total_to_run} {case['id']} {record['status_label']} http={record['http_status']} {record['elapsed_seconds']}s decision={k.get('permit_decision')} expected={case.get('expected_decision')} issues={issues}", flush=True)
            if done % 5 == 0:
                save_summary(records, final=False)
            time.sleep(SLEEP_BETWEEN_CASES_SECONDS)
    save_summary(records, final=True)
    print(f"FINAL summary={SUMMARY_PATH} report={REPORT_PATH} failures={FAILURES_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
