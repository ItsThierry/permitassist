#!/usr/bin/env python3
"""Offline customer-boundary replay for live_customer_real100_more_gpt54_20260630T233000Z.

No network/model calls. Replays frozen live customer responses through the local
customer ViewModel and scores the public contract after universal filing-packet
fixes.
"""
from __future__ import annotations

import copy
import json
import sys
import types
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_real100_more_gpt54_20260630T233000Z"
OUT_ROOT = ROOT / "artifacts" / "universal_fixes_20260701_offline_replay"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API) not in sys.path:
    sys.path.insert(0, str(API))


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
    research_stub.classify_source_authority = lambda url, city="", state="", result=None: {"category": "local_ahj", "tier": "local_ahj", "display_allowed": True}
    sys.modules["research_engine"] = research_stub


def _records() -> list[dict[str, Any]]:
    return [json.loads(line) for line in (ARTIFACT_ROOT / "evidence.jsonl").read_text().splitlines() if line.strip()]


def _canonical_family(value: str) -> str:
    value = str(value or "").lower().strip().replace("-", "_")
    return {
        "zoning": "planning",
        "land_use": "planning",
        "occupancy": "co",
        "right_of_way": "grading",
        "row": "grading",
    }.get(value, value)


def _visible_rows(public: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("permits_required", "related_permits", "companion_permits", "trade_permits"):
        rows.extend(row for row in public.get(key) or [] if isinstance(row, dict))
    return rows


def main() -> int:
    _install_server_stubs()
    from api import server  # noqa: WPS433
    from scripts.live100_fix_for_good_offline_replay_20260630 import debug_leaks  # noqa: WPS433

    server.CACHE_DB = str(OUT_ROOT / "replay_cache.db")
    server.DATA_DIR = str(OUT_ROOT)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    server.init_db()

    cases_out: list[dict[str, Any]] = []
    issue_counts: Counter[str] = Counter()
    decision_counts: Counter[str] = Counter()
    segment_counts: dict[str, Counter[str]] = {}

    for rec in _records():
        case = rec["case"]
        public = server.build_customer_permit_view_model(
            copy.deepcopy(rec["response_body"]),
            case["job_type"],
            case["city"],
            case["state"],
            job_category=case.get("segment"),
        )
        decision = str(public.get("permit_decision") or "").upper().strip()
        decision_counts[decision] += 1
        segment_counts.setdefault(case.get("segment", "unknown"), Counter())[decision] += 1
        required_rows = [row for row in public.get("permits_required") or [] if isinstance(row, dict)]
        visible_families = {_canonical_family(server._customer_row_family(row)) for row in _visible_rows(public)}
        required_families = {
            _canonical_family(server._customer_row_family(row))
            for row in required_rows
            if str(server._customer_row_status(row) or row.get("status") or row.get("decision") or "").upper() == "REQUIRED" or row.get("required") is True
        }
        expected_decision = str(case.get("expected_decision") or "").upper().strip()
        expected_families = {_canonical_family(family) for family in case.get("expected_families") or []}
        issues: list[dict[str, str]] = []
        if decision not in {"REQUIRED", "NOT_REQUIRED"}:
            issues.append({"code": "bad_main_decision", "message": decision})
        if decision != expected_decision:
            issues.append({"code": "decision_mismatch", "message": f"expected {expected_decision}, got {decision}"})
        if expected_decision == "REQUIRED":
            missing = sorted(expected_families - visible_families)
            if missing:
                issues.append({"code": "missing_expected_family", "message": ",".join(missing)})
            if not required_rows:
                issues.append({"code": "required_without_rows", "message": "REQUIRED result has no required rows"})
        if expected_decision == "NOT_REQUIRED" and required_rows:
            issues.append({"code": "false_required_family", "message": ",".join(sorted(required_families))})
        leaks = debug_leaks(public)
        if leaks:
            issues.append({"code": "debug_or_secret_leak", "message": ";".join(leaks[:5])})
        for issue in issues:
            issue_counts[issue["code"]] += 1
        cases_out.append({
            "id": case["id"],
            "segment": case.get("segment"),
            "decision": decision,
            "expected_decision": expected_decision,
            "visible_families": sorted(visible_families),
            "required_families": sorted(required_families),
            "expected_families": sorted(expected_families),
            "issues": issues,
        })

    failed = [case for case in cases_out if case["issues"]]
    summary = {
        "artifact_root": str(ARTIFACT_ROOT),
        "total": len(cases_out),
        "pass": len(cases_out) - len(failed),
        "fail": len(failed),
        "issue_counts": dict(sorted(issue_counts.items())),
        "decision_counts": dict(sorted(decision_counts.items())),
        "segment_decision_counts": {key: dict(value) for key, value in sorted(segment_counts.items())},
        "failed_case_ids": [case["id"] for case in failed],
    }
    (OUT_ROOT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUT_ROOT / "cases.json").write_text(json.dumps(cases_out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_lines = [
        "# Universal fixes offline replay — 2026-07-01",
        f"- Artifact root: `{ARTIFACT_ROOT}`",
        f"- Pass/fail: {summary['pass']}/{summary['fail']} of {summary['total']}",
        f"- Issue counts: `{json.dumps(summary['issue_counts'], sort_keys=True)}`",
        f"- Failed case ids: `{', '.join(summary['failed_case_ids']) or 'none'}`",
    ]
    (OUT_ROOT / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
