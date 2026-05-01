#!/usr/bin/env python3
"""PermitAssist deterministic eval suite — v1.0.0 (2026-04-28).

Runs the locked test cases at eval/permit_eval_cases.json against the live
production engine and grades each response against a deterministic rubric
(regex + structural checks). Aggregate score is the engine's grade.

Usage:
    # Run all 10 cases against prod, write scorecard to memory/evals/
    python3 scripts/run_eval.py

    # Run a single case
    python3 scripts/run_eval.py --case phoenix_restaurant_ti

    # Run with cache bust (new query string each run)
    python3 scripts/run_eval.py --bust

    # Custom prod URL (e.g. local dev)
    python3 scripts/run_eval.py --base-url http://localhost:8000

The grading is deterministic — no LLM calls. Each rubric check is pass/fail.
Score per case = (passed_checks / total_checks) * 100. Aggregate = mean of
case scores.

Use this as the fast-feedback loop. For deeper qualitative grading, dispatch
a subscription Opus 4.7 subagent (separate workflow).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

# Repo root resolution
REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_CASES_PATH = REPO_ROOT / "eval" / "permit_eval_cases.json"
DEFAULT_BASE_URL = "https://permitassist.io"
ADMIN_TOKEN_ENV = "PERMITASSIST_ADMIN_TOKEN"
ADMIN_TOKEN_FALLBACK_FILES = [
    Path.home() / ".openclaw" / "private" / "api-status.private.md",
]


def load_admin_token() -> str:
    """Resolve admin bypass token from env or local private file."""
    token = os.environ.get(ADMIN_TOKEN_ENV, "").strip()
    if token:
        return token
    for path in ADMIN_TOKEN_FALLBACK_FILES:
        if path.exists():
            try:
                content = path.read_text()
                m = re.search(r"Admin Token[:\s]+([A-Za-z0-9_\-]+)", content)
                if m:
                    return m.group(1).strip()
            except Exception:
                pass
    return ""


def call_engine(base_url: str, job_type: str, city: str, state: str, admin_token: str, timeout: int = 180) -> tuple[int, dict]:
    """POST to /api/permit and return (http_status, json_body or {'error': ...})."""
    url = base_url.rstrip("/") + "/api/permit"
    payload = json.dumps({"job_type": job_type, "city": city, "state": state}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Admin-Token": admin_token,
            "User-Agent": "PermitAssistEvalSuite/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
            return e.code, json.loads(body) if body else {"error": str(e)}
        except Exception:
            return e.code, {"error": str(e)}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def _get_fee_high_end(fee_range: Any) -> float | None:
    """Extract the high-end dollar number from a fee_range string.

    Handles formats: '$8K-25K', '$2,500 - $12,000', '$558', 'Fee Estimate: **$19,000-$30,000+**'.
    Returns the highest USD number found, or None if unparseable.
    """
    if not isinstance(fee_range, str) or not fee_range:
        return None
    # Find all $X or $X.YY tokens; allow K/M suffix and commas
    matches = re.findall(r"\$\s?([\d,]+(?:\.\d+)?)\s?([KkMm])?", fee_range)
    if not matches:
        return None
    values = []
    for num_str, suffix in matches:
        try:
            n = float(num_str.replace(",", ""))
            if suffix.lower() == "k":
                n *= 1000
            elif suffix.lower() == "m":
                n *= 1_000_000
            values.append(n)
        except ValueError:
            continue
    return max(values) if values else None


def _checklist_text(response: dict) -> str:
    """Concatenate checklist + inspect_checklist + common_mistakes + pro_tips into one searchable string."""
    blocks = []
    for fld in ("checklist", "inspect_checklist", "common_mistakes", "pro_tips", "what_to_bring"):
        v = response.get(fld) or []
        if isinstance(v, list):
            blocks.append(" | ".join(str(x) for x in v))
        elif isinstance(v, str):
            blocks.append(v)
    return " | ".join(blocks).lower()


def _sources_list(response: dict) -> list[str]:
    """Return source URLs as a list of strings."""
    raw = response.get("sources") or []
    if not isinstance(raw, list):
        return []
    out = []
    for s in raw:
        if isinstance(s, str):
            out.append(s.lower())
        elif isinstance(s, dict):
            url = s.get("url") or ""
            if url:
                out.append(url.lower())
    return out


def evaluate(case: dict, response: dict) -> dict:
    """Grade a single response against the case rubric. Returns dict with checks + score."""
    rubric = case["rubric"]
    checks: list[dict] = []

    apply_url = (response.get("apply_url") or "").lower()
    primary_scope = response.get("_primary_scope") or ""
    permits = response.get("permits_required") or []
    primary_permit = ""
    if permits and isinstance(permits[0], dict):
        primary_permit = str(permits[0].get("permit_type") or permits[0].get("name") or "")
    sources = _sources_list(response)
    checklist_text = _checklist_text(response)
    hidden_triggers = response.get("hidden_triggers") or []
    fee_range = response.get("fee_range") or ""
    fee_high = _get_fee_high_end(fee_range)
    validation_issues = response.get("_validation_issues") or []
    confidence = str(response.get("confidence") or "").lower()

    def _check(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    # apply_url match
    if "must_apply_url_match" in rubric:
        pat = rubric["must_apply_url_match"]
        passed = bool(re.search(pat, apply_url, re.IGNORECASE)) if apply_url else False
        _check("apply_url_match", passed, f"pat={pat!r} got={apply_url!r}")

    # apply_url NOT match (state-license / hallucinated domains)
    if "must_apply_url_not_match" in rubric:
        pat = rubric["must_apply_url_not_match"]
        passed = not bool(re.search(pat, apply_url, re.IGNORECASE)) if apply_url else True
        _check("apply_url_not_match", passed, f"pat={pat!r} got={apply_url!r}")

    # primary_scope correctness
    if "must_primary_scope" in rubric:
        expected = rubric["must_primary_scope"]
        _check("primary_scope", primary_scope == expected, f"expected={expected!r} got={primary_scope!r}")

    # primary permit text correctness
    if "must_primary_permit_match" in rubric:
        pat = rubric["must_primary_permit_match"]
        passed = bool(re.search(pat, primary_permit, re.IGNORECASE)) if primary_permit else False
        _check("primary_permit_match", passed, f"pat={pat!r} got={primary_permit!r}")

    if "must_primary_permit_not_match" in rubric:
        pat = rubric["must_primary_permit_not_match"]
        passed = not bool(re.search(pat, primary_permit, re.IGNORECASE)) if primary_permit else True
        _check("primary_permit_not_match", passed, f"pat={pat!r} got={primary_permit!r}")

    # min permits count
    if "permits_min" in rubric:
        n = len(permits) if isinstance(permits, list) else 0
        _check("permits_min", n >= rubric["permits_min"], f"min={rubric['permits_min']} got={n}")

    # at least one source matches
    if "must_sources_match_any" in rubric:
        needles = [n.lower() for n in rubric["must_sources_match_any"]]
        joined = " ".join(sources)
        passed = any(n in joined for n in needles) if sources else False
        _check("sources_match_any", passed, f"needles={needles} sources_count={len(sources)}")

    # no source matches blocked list
    if "must_sources_not_match_any" in rubric:
        bad = [b.lower() for b in rubric["must_sources_not_match_any"]]
        joined = " ".join(sources)
        hits = [b for b in bad if b in joined]
        _check("sources_not_match_any", len(hits) == 0, f"hits={hits}" if hits else "clean")

    # checklist contains at least one expected item
    if "must_checklist_contain_any" in rubric:
        needles = [n.lower() for n in rubric["must_checklist_contain_any"]]
        passed = any(n in checklist_text for n in needles)
        _check("checklist_contain_any", passed, f"needles_hit={[n for n in needles if n in checklist_text]}")

    # checklist contains NONE of forbidden items
    if "must_checklist_not_contain" in rubric:
        forbidden = [f.lower() for f in rubric["must_checklist_not_contain"]]
        hits = [f for f in forbidden if f in checklist_text]
        _check("checklist_not_contain", len(hits) == 0, f"hits={hits}" if hits else "clean")

    # min hidden_triggers count
    if "must_hidden_triggers_min" in rubric:
        n = len(hidden_triggers) if isinstance(hidden_triggers, list) else 0
        _check("hidden_triggers_min", n >= rubric["must_hidden_triggers_min"], f"min={rubric['must_hidden_triggers_min']} got={n}")

    # specific trigger ids must include at least one
    if "must_hidden_trigger_ids_any" in rubric:
        expected_ids = set(rubric["must_hidden_trigger_ids_any"])
        actual_ids = {t.get("id") for t in hidden_triggers if isinstance(t, dict)}
        passed = bool(expected_ids & actual_ids)
        _check("hidden_trigger_ids_any", passed, f"expected_any={list(expected_ids)} got={list(actual_ids)[:5]}")

    # fee_adjusted flag (commercial/ADU should override; residential should not)
    if "must_fee_adjusted" in rubric:
        expected = bool(rubric["must_fee_adjusted"])
        actual = bool(response.get("_fee_adjusted"))
        _check("fee_adjusted", expected == actual, f"expected={expected} got={actual}")

    # fee high-end >= floor
    if "must_fee_min" in rubric:
        floor = rubric["must_fee_min"]
        passed = fee_high is not None and fee_high >= floor
        _check("fee_min", passed, f"floor=${floor} got={'$' + str(int(fee_high)) if fee_high else 'unparseable'}")

    # fee high-end <= ceiling (sanity check)
    if "must_fee_max" in rubric:
        ceiling = rubric["must_fee_max"]
        passed = fee_high is None or fee_high <= ceiling
        _check("fee_max", passed, f"ceiling=${ceiling} got={'$' + str(int(fee_high)) if fee_high else 'unparseable'}")

    # high confidence needs at least one source; otherwise force calibration failure
    if rubric.get("must_confidence_not_high_without_sources"):
        _check(
            "confidence_not_high_without_sources",
            not (confidence == "high" and len(sources) == 0),
            f"confidence={confidence!r} sources_count={len(sources)}",
        )

    # validation gate didn't catch placeholder leaks
    if rubric.get("must_validation_no_placeholder"):
        placeholder_issues = [i for i in validation_issues if i.get("kind") == "placeholder"]
        _check("no_placeholder_leak", len(placeholder_issues) == 0, f"hits={len(placeholder_issues)}")

    passed_n = sum(1 for c in checks if c["passed"])
    total_n = len(checks)
    score = round(passed_n / total_n * 100, 1) if total_n else 0.0

    return {
        "case_id": case["id"],
        "category": case["category"],
        "scope": case["scope"],
        "city": case["city"],
        "state": case["state"],
        "score": score,
        "passed": passed_n,
        "total": total_n,
        "checks": checks,
        "engine_primary_scope": primary_scope,
        "engine_primary_permit": primary_permit,
        "engine_apply_url": response.get("apply_url"),
        "engine_permits_count": len(permits),
        "engine_sources_count": len(sources),
        "engine_hidden_triggers_count": len(hidden_triggers),
        "engine_fee_range": (fee_range[:160] + "...") if isinstance(fee_range, str) and len(fee_range) > 160 else fee_range,
        "engine_fee_adjusted": response.get("_fee_adjusted"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Engine base URL (default: prod)")
    ap.add_argument("--case", help="Run only a single case by id")
    ap.add_argument("--bust", action="store_true", help="Append cache-bust suffix to job_type")
    ap.add_argument("--output-dir", default=str(Path.home() / ".openclaw" / "workspace" / "memory" / "evals"), help="Where to write the scorecard")
    ap.add_argument("--no-write", action="store_true", help="Don't write scorecard, just print")
    args = ap.parse_args()

    cases_data = json.loads(EVAL_CASES_PATH.read_text())
    cases = cases_data["cases"]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            print(f"Case {args.case!r} not found", file=sys.stderr)
            return 2

    admin_token = load_admin_token()
    if not admin_token:
        print(f"WARNING: no admin token found (env {ADMIN_TOKEN_ENV} or {ADMIN_TOKEN_FALLBACK_FILES[0]})", file=sys.stderr)
        print("Calls will hit the 3-lookup free tier limit.", file=sys.stderr)

    bust_suffix = f" (eval-{datetime.utcnow().strftime('%Y%m%dT%H%M')})" if args.bust else ""

    print(f"Running {len(cases)} eval case(s) against {args.base_url}")
    print(f"Cache bust: {'on' if args.bust else 'off'}")
    print()

    results = []
    started_at = datetime.utcnow().isoformat() + "Z"
    t_start = time.time()

    for i, case in enumerate(cases, 1):
        job_type = case["job_type"] + bust_suffix
        city = case["city"]
        state = case["state"]
        print(f"[{i}/{len(cases)}] {case['id']:30s} | {city}, {state}")
        t0 = time.time()
        status, body = call_engine(args.base_url, job_type, city, state, admin_token)
        elapsed = time.time() - t0
        if status != 200:
            print(f"    FAIL HTTP {status}: {body.get('error', body)[:200]}")
            results.append({
                "case_id": case["id"],
                "score": 0.0,
                "error": f"HTTP {status}: {body.get('error', '')[:120]}",
                "elapsed_s": round(elapsed, 1),
            })
            continue
        result = evaluate(case, body)
        result["elapsed_s"] = round(elapsed, 1)
        results.append(result)
        print(f"    score={result['score']:5.1f} ({result['passed']}/{result['total']})  scope={result['engine_primary_scope']}  permits={result['engine_permits_count']}  triggers={result['engine_hidden_triggers_count']}  {elapsed:.1f}s")
        # surface failures inline
        failures = [c for c in result["checks"] if not c["passed"]]
        for f in failures[:3]:
            print(f"        FAIL  {f['name']}  {f['detail'][:120]}")
        if len(failures) > 3:
            print(f"        ... +{len(failures) - 3} more failures")

    total_elapsed = round(time.time() - t_start, 1)
    scored = [r for r in results if "score" in r and r.get("score") is not None and "error" not in r]
    if scored:
        residential = [r for r in scored if r.get("category") == "residential"]
        commercial = [r for r in scored if r.get("category") == "commercial"]
        scopes: dict[str, list[dict]] = {}
        for r in scored:
            scopes.setdefault(r.get("scope") or "unknown", []).append(r)
        verticals = {
            scope: round(sum(item["score"] for item in rows) / len(rows), 1)
            for scope, rows in sorted(scopes.items())
        }
        agg = {
            "overall": round(sum(r["score"] for r in scored) / len(scored), 1),
            "residential": round(sum(r["score"] for r in residential) / len(residential), 1) if residential else None,
            "commercial": round(sum(r["score"] for r in commercial) / len(commercial), 1) if commercial else None,
            "verticals": verticals,
        }
    else:
        agg = {"overall": None, "residential": None, "commercial": None, "verticals": {}}

    print()
    print("=" * 80)
    print(f"AGGREGATE — {len(scored)}/{len(results)} cases scored, {total_elapsed}s total")
    print(f"  Overall:     {agg['overall']}")
    print(f"  Residential: {agg['residential']}")
    print(f"  Commercial:  {agg['commercial']}")
    if agg.get("verticals"):
        print("  Verticals:")
        for scope, score in agg["verticals"].items():
            print(f"    {scope:32s} {score}")
    print("=" * 80)

    if not args.no_write:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        scorecard_path = out_dir / f"eval-{datetime.utcnow().strftime('%Y-%m-%dT%H%M')}.json"
        scorecard = {
            "started_at": started_at,
            "elapsed_s": total_elapsed,
            "base_url": args.base_url,
            "cache_bust": args.bust,
            "case_count": len(results),
            "aggregate": agg,
            "cases": results,
        }
        scorecard_path.write_text(json.dumps(scorecard, indent=2))
        print(f"\nScorecard written to: {scorecard_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
