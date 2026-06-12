#!/usr/bin/env python3
"""
PermitAssist Regression Harness — Phase 4
=========================================
Runs three calibration prompts end-to-end against production (or local),
captures rendered output text, enforces banned patterns, and diffs against
golden snapshots.

Usage:
    python scripts/regression_check.py          # run all checks, compare to goldens
    python scripts/regression_check.py --refresh # capture new goldens (human approval)
    python scripts/regression_check.py --local   # run against localhost:8000
    python scripts/regression_check.py --prod    # run against permitassist.io (default)

Exit codes:
    0  = all checks passed, zero diffs from goldens
    1  = banned pattern found OR output changed from golden OR runtime error
"""

import argparse
import difflib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

# ── Configuration ──────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO_ROOT / "tests" / "golden"
GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

PROD_BASE = "https://permitassist.io"
LOCAL_BASE = "http://localhost:8000"

# Prompts (verbatim from specification)
PROMPTS: dict[str, dict[str, Any]] = {
    "savannah_restaurant": {
        "label": "Prompt B — Savannah restaurant TI",
        "payload": {
            "job_type": "I'm a GC converting a 2,200 sq ft former retail space into a full-service restaurant in Savannah, GA. Scope: grease interceptor and three-compartment sink with new floor drains, restroom plumbing build-out, Type I kitchen hood with exhaust and makeup air, electrical panel upgrade with new circuits for the cooking line, non-structural demo and new kitchen/dining partition layout. What permits do I need to pull?",
            "city": "Savannah",
            "state": "GA",
            "zip_code": "31401",
            "job_category": "general_contractor",
            "job_value": 200000,
        },
        "critical_asserts": [
            "anchor = Building",
            "single Building entry",
            "fee card computed from fee_formula",
            'no "$12,000"',
            'no "$17,500"',
            "W&S signed-form gate present",
            "FOG card mentions ClearForms",
            "fire protection appears as required trade",
            "phone renders 912-651-6530",
            "no address for Savannah",
            "no structural-RTU line",
        ],
    },
    "richmond_dental": {
        "label": "Prompt C — Richmond dental office TI",
        "payload": {
            "job_type": "I'm a GC converting a 1,600 sq ft former retail space into a dental office in Richmond, VA. Scope: plumbing rough-in for four operatories including vacuum and compressed-air lines plus a nitrous oxide system, ADA restroom upgrade, electrical panel upgrade with dedicated circuits for chairs and X-ray, replacing the existing rooftop HVAC unit with a larger RTU and ductwork rework, non-structural demo and new partitions. Budget is around $190,000. What permits do I need to pull?",
            "city": "Richmond",
            "state": "VA",
            "zip_code": "23219",
            "job_category": "general_contractor",
            "job_value": 190000,
        },
        "critical_asserts": [
            "anchor = Building",
            "full verified contact card",
            "804-646-4169",
            "900 E. Broad St",
            "provenance line present",
            "structural roof-load line present",
            "med-gas/nitrous content present",
            "labeled national benchmark fee",
            "no fee formula on record",
        ],
    },
    # Third prompt: replicate the Phase 2 Richmond verification prompt
    # ("the Richmond prompt in 2.2 above" = same scope, different framing)
    "richmond_dental_gc": {
        "label": "Prompt A — Richmond GC dental (Phase 2 check)",
        "payload": {
            "job_type": "General contractor converting a 1,600 sq ft former retail space into a dental office in Richmond, VA. Scope: plumbing rough-in for four operatories including vacuum and compressed-air lines plus a nitrous oxide system, ADA restroom upgrade, electrical panel upgrade with dedicated circuits for chairs and X-ray, replacing the existing rooftop HVAC unit with a larger RTU and ductwork rework, non-structural demo and new partitions. Budget is around $190,000. What permits do I need to pull?",
            "city": "Richmond",
            "state": "VA",
            "zip_code": "23219",
            "job_category": "general_contractor",
        },
        "critical_asserts": [
            "$63 in fee surface",
            "$1,200–$2,500 in fee range",
            "NO word-smashing (no tradepermit, acrossbuilding, etc.)",
            'proper spaced words ("trade permit", "license number")',
        ],
    },
}

# ── Banned patterns (spec 4.2) ─────────────────────────────────────────────

FAIL_PATTERNS: list[re.Pattern] = [
    # Word glued to currency
    re.compile(r"[a-zA-Z]\$[0-9]"),
    # HTML entities
    re.compile(r"&amp;|&lt;|&gt;|&quot;|&#\d+;"),
    # Double period (not ellipsis)
    re.compile(r"\.\.(?!\.)"),
    # BG-POLISH-01 customer-copy blacklists
    re.compile(r"verify\s+in\s+before", re.I),
    re.compile(r"verified\s+via\s+PermitAssist", re.I),
    re.compile(r"companion\s+permits\s+are\s+suppressed", re.I),
    # Multiplier missing integer
    re.compile(r"^\s*\.?[0-9]*×", re.MULTILINE),
    # Known fragment lines from splitter bug
    re.compile(r"^\s*certificate-of-occupancy conditions\.\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*fixture counts\.\s*$", re.IGNORECASE | re.MULTILINE),
    # "Permit Permit" stutter
    re.compile(r"\bPermit\s+Permit\b"),
    # "× 1" should not render
    re.compile(r"×\s*1\b"),
    # Broken multiplier like ".0×" or ") .0×"
    re.compile(r"\)\.\s*\d+×|\b\.\d+×"),
]

# Literal string banlist (from spec + known bad artifacts)
LITERAL_BLACKLIST: list[str] = [
    # Phase 2 known smeared words
    "tradepermit",
    "licensenumber",
    "acrossbuilding",
    "showingdemolition",
    "installedmodified",
    "dentaloccupancy",
    "RTUsubmittal",
    "rtusubmittal",
    "ifoxygen",
    # Bad phone number
    "(912) 651-6790",
    # Other known bad compounds from past runs
    "use the structured permit kind",  # template leak
    "110 W.State",
    "110 W. State",
]

# Header repeated-word check
_HEADER_REPEATED_WORD_RE = re.compile(
    r"(?im)^(?:#+\s*|\*\*?)?\s*([A-Za-z]+)\s+\1\b"
)

# Short numbered list items (< 4 words)
_SHORT_LIST_ITEM_RE = re.compile(
    r"(?m)^\s*(?:\d+\.|\-\s|\*\s)\s*(\S+(?:\s+\S+){0,2})\s*$"
)


# ── Text extraction ────────────────────────────────────────────────────────

def flatten_json_to_text(obj: Any, _lines: list[str] | None = None) -> str:
    """Recursively flatten a JSON dict/list into lines of text for scanning."""
    if _lines is None:
        _lines = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            _lines.append(f"KEY:{k}")
            flatten_json_to_text(v, _lines)
    elif isinstance(obj, list):
        for item in obj:
            flatten_json_to_text(item, _lines)
    elif isinstance(obj, str):
        _lines.append(obj)
    else:
        _lines.append(str(obj))
    return "\n".join(_lines)


def extract_rendered_text(response_json: dict) -> str:
    """Convert a permit lookup JSON response into human-readable text."""
    lines: list[str] = []

    # Top-level fields that are customer-visible strings
    for key in ("job_summary", "customer_headline", "customer_next_step",
                "fee_range", "disclaimer", "inspection_booking",
                "zoning_hoa_flag", "approval_timeline", "not_required_reason",
                "confidence_reason", "data_source"):
        val = response_json.get(key)
        if isinstance(val, str):
            lines.append(f"## {key}\n{val}")
        elif isinstance(val, dict):
            lines.append(f"## {key}")
            for sub_k, sub_v in val.items():
                if isinstance(sub_v, str):
                    lines.append(f"  {sub_k}: {sub_v}")

    # Permits
    permits = response_json.get("permits_required") or []
    if permits:
        lines.append("## Permits Required")
        for p in permits:
            if isinstance(p, dict):
                lines.append(f"  - {p.get('permit_type', '')} (kind={p.get('kind','')}, required={p.get('required','')})")

    # Companion permits
    companions = response_json.get("companion_permits") or []
    if companions:
        lines.append("## Companion Permits")
        for c in companions:
            if isinstance(c, dict):
                lines.append(f"  - {c.get('permit_type', '')}: {c.get('reason', '')}")

    # Pro tips / common mistakes
    for key in ("pro_tips", "common_mistakes"):
        vals = response_json.get(key) or []
        if vals:
            lines.append(f"## {key}")
            for v in vals:
                if isinstance(v, str):
                    lines.append(f"  - {v}")

    # Inspections
    inspections = response_json.get("inspections") or []
    if inspections:
        lines.append("## Inspections")
        for insp in inspections:
            if isinstance(insp, dict):
                lines.append(f"  [{insp.get('stage', '')}] {insp.get('description', '')}")
                if insp.get("timing"):
                    lines.append(f"     Timing: {insp['timing']}")

    # Watch outs / gates
    for key in ("watch_outs", "gates", "notes"):
        vals = response_json.get(key) or []
        if vals:
            lines.append(f"## {key}")
            for v in vals:
                if isinstance(v, str):
                    lines.append(f"  ⚠ {v}")
                elif isinstance(v, dict):
                    lines.append(f"  ⚠ {v.get('title', '')}: {v.get('body', '')}")

    # Contact / applying office
    office = response_json.get("applying_office")
    if office:
        lines.append(f"## Contact\n{office}")
    phone = response_json.get("apply_phone")
    if phone:
        lines.append(f"  Phone: {phone}")
    addr = response_json.get("apply_address")
    if addr:
        lines.append(f"  Address: {addr}")

    # Sources
    sources = response_json.get("sources") or []
    if sources:
        lines.append("## Sources")
        for s in sources:
            if isinstance(s, dict):
                lines.append(f"  - {s.get('name', '')}: {s.get('url', '')}")

    return "\n\n".join(lines)


# ── Checks ─────────────────────────────────────────────────────────────────

def check_banned_patterns(text: str) -> list[dict]:
    """Return list of banned-pattern hits. Each hit = {pattern, line, snippet}."""
    failures: list[dict] = []
    lines = text.splitlines()
    text_flat = flatten_json_to_text(json.loads(text) if text.startswith("{") else {})

    for i, line in enumerate(lines, 1):
        # Regex patterns
        for pat in FAIL_PATTERNS:
            for match in pat.finditer(line):
                failures.append({
                    "check": "regex",
                    "pattern": pat.pattern[:60],
                    "line": i,
                    "snippet": line.strip()[:120],
                })

        # Literal blacklist
        for bad in LITERAL_BLACKLIST:
            if bad.lower() in line.lower():
                failures.append({
                    "check": "literal",
                    "pattern": bad,
                    "line": i,
                    "snippet": line.strip()[:120],
                })

    # Header repeated-word check
    for match in _HEADER_REPEATED_WORD_RE.finditer(text):
        failures.append({
            "check": "header_repeated_word",
            "pattern": f"word '{match.group(1)}' repeated",
            "line": text[:match.start()].count("\n") + 1,
            "snippet": match.group(0)[:120],
        })

    # Short numbered list items
    for match in _SHORT_LIST_ITEM_RE.finditer(text):
        failures.append({
            "check": "short_list_item",
            "pattern": f"item < 4 words: '{match.group(1)}'",
            "line": text[:match.start()].count("\n") + 1,
            "snippet": match.group(0)[:120],
        })

    return failures


def run_prompt(base_url: str, key: str, prompt_data: dict) -> tuple[dict, str, str]:
    """Run a single prompt against the API. Returns (json, rendered_text, raw_json_str)."""
    url = f"{base_url}/api/permit"
    headers = {
        "Content-Type": "application/json",
        "X-Sample-Demo": "1",  # Bypass rate limits / free tier
    }
    resp = requests.post(url, json=prompt_data["payload"], headers=headers, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    raw_str = json.dumps(data, indent=2, ensure_ascii=False)
    rendered = extract_rendered_text(data)
    return data, rendered, raw_str


# ── Golden snapshot management ─────────────────────────────────────────────

def golden_path(key: str) -> Path:
    return GOLDEN_DIR / f"{key}.golden.txt"


def golden_raw_path(key: str) -> Path:
    return GOLDEN_DIR / f"{key}.golden.json"


def load_golden(key: str) -> str | None:
    gp = golden_path(key)
    if gp.exists():
        return gp.read_text(encoding="utf-8")
    return None


def save_golden(key: str, rendered: str, raw: str) -> None:
    golden_path(key).write_text(rendered, encoding="utf-8")
    golden_raw_path(key).write_text(raw, encoding="utf-8")


def diff_golden(current: str, golden: str) -> str:
    """Return unified diff string (whitespace-sensitive)."""
    cur_lines = current.splitlines(keepends=True)
    gold_lines = golden.splitlines(keepends=True)
    diff = difflib.unified_diff(
        gold_lines, cur_lines,
        fromfile="golden", tofile="current",
        lineterm="",
    )
    return "".join(diff)


# ── Telegram notification ──────────────────────────────────────────────────

def notify_failure(message: str) -> None:
    """Fire-and-forget Telegram notification on failure."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": message[:4000], "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception:
        pass


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="PermitAssist Regression Harness")
    parser.add_argument("--refresh", action="store_true", help="Capture new golden snapshots (requires human approval)")
    parser.add_argument("--local", action="store_true", help="Run against localhost:8000")
    parser.add_argument("--prod", action="store_true", help="Run against permitassist.io (default)")
    parser.add_argument("--prompt", choices=list(PROMPTS.keys()), help="Run only one prompt")
    args = parser.parse_args()

    base_url = LOCAL_BASE if args.local else PROD_BASE
    prompts_to_run = {args.prompt: PROMPTS[args.prompt]} if args.prompt else PROMPTS

    print(f"═" * 70)
    print(f"PermitAssist Regression Harness")
    print(f"Target: {base_url}")
    print(f"Mode:   {'REFRESH goldens' if args.refresh else 'VERIFY against goldens'}")
    print(f"═" * 70)

    exit_code = 0
    failures_report: list[str] = []

    for key, pdata in prompts_to_run.items():
        label = pdata["label"]
        print(f"\n─── {label} ({key}) ───")

        # 1. Run the prompt
        try:
            data, rendered, raw = run_prompt(base_url, key, pdata)
        except requests.exceptions.RequestException as e:
            print(f"  ❌ API ERROR: {e}")
            failures_report.append(f"{key}: API error — {e}")
            exit_code = 1
            continue
        except Exception as e:
            print(f"  ❌ RUNTIME ERROR: {e}")
            failures_report.append(f"{key}: Runtime error — {e}")
            exit_code = 1
            continue

        # 2. Banned pattern check
        banned_hits = check_banned_patterns(raw)
        if banned_hits:
            print(f"  ❌ BANNED PATTERNS ({len(banned_hits)} hits):")
            for hit in banned_hits[:5]:
                print(f"     [{hit['check']}] L{hit['line']}: {hit['snippet'][:80]}")
            if len(banned_hits) > 5:
                print(f"     ... and {len(banned_hits) - 5} more")
            failures_report.append(f"{key}: {len(banned_hits)} banned pattern(s)")
            exit_code = 1
        else:
            print(f"  ✅ Banned patterns: clean")

        # 3. Golden snapshot
        if args.refresh:
            print(f"  💾 Saving golden snapshot...")
            save_golden(key, rendered, raw)
            print(f"  ✅ Golden saved to {golden_path(key)}")
            continue  # Skip diff on refresh

        golden = load_golden(key)
        if golden is None:
            print(f"  ⚠️  No golden snapshot found. Run with --refresh to capture.")
            # Not a failure on first run — but warn
            save_golden(key, rendered, raw)  # auto-seed if missing
            print(f"  💾 Auto-seeded golden for '{key}'")
            continue

        diff = diff_golden(rendered, golden)
        if diff:
            print(f"  ❌ GOLDEN DIFF:")
            # Print first ~30 lines of diff
            diff_lines = diff.splitlines()
            for dl in diff_lines[:30]:
                print(f"    {dl}")
            if len(diff_lines) > 30:
                print(f"    ... ({len(diff_lines) - 30} more lines)")
            failures_report.append(f"{key}: golden diff ({len(diff_lines)} line changes)")
            exit_code = 1
        else:
            print(f"  ✅ Golden diff: identical")

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{'═' * 70}")
    if exit_code == 0:
        print("RESULT: ✅ ALL CHECKS PASSED")
    else:
        print(f"RESULT: ❌ {len(failures_report)} FAILURE(S)")
        for f in failures_report:
            print(f"  • {f}")
    print(f"{'═' * 70}")

    # Notify on failure
    if exit_code != 0 and failures_report:
        msg = (
            f"🚨 <b>PermitAssist Regression FAILURE</b>\n"
            f"Target: {base_url}\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n\n"
            f"Failures:\n" + "\n".join(f"• {f}" for f in failures_report[:10])
        )
        notify_failure(msg)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
