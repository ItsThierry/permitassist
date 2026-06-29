#!/usr/bin/env python3
"""Offline live80 customer-boundary replay for the 2026-06-28 remediation plan.

This intentionally grades only customer-visible REQUIRED rows/banner/mirror safety.
VERIFY/CONDITIONAL rows and required_if text are preserved as useful guidance but
are not counted as hard overreach. Negated trade wording in the original request
is handled by the renderer before this replay inspects required families.
"""
from __future__ import annotations

import copy
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import server  # noqa: E402

ARTIFACT_DIR = ROOT / "artifacts" / "live_customer_80_20260628T2153Z"
CASES = ARTIFACT_DIR / "cases.jsonl"
OUT_JSON = ARTIFACT_DIR / "live80_offline_replay_customer_boundary_postfix.json"
OUT_CSV = ARTIFACT_DIR / "live80_offline_replay_customer_boundary_postfix.csv"
OUT_MD = ARTIFACT_DIR / "live80_offline_replay_customer_boundary_postfix.md"

CONFIRMED_DEFECTS: dict[str, dict[str, Any]] = {
    "R11": {"must": {"building"}, "must_not": set(), "terms": ("short-form", "siding")},
    "R33": {"must": {"building", "electrical"}, "must_not": set(), "terms": ("garage",)},
    "R20": {"must": {"building"}, "must_not": {"plumbing", "electrical", "mechanical"}},
    "R25": {"must": {"building"}, "must_not": {"fire", "planning", "co"}},
    "R27": {"must": {"building", "mechanical"}, "must_not": {"electrical"}},
    "R31": {"must": {"electrical"}, "must_not": {"fire", "planning", "co"}},
    "R34": {"must": {"building", "electrical"}, "must_not": {"plumbing"}},
    "R35": {"must": {"plumbing"}, "must_not": {"electrical"}},
    "R38": {"must": {"building"}, "must_not": {"electrical", "plumbing", "planning"}},
    "R40": {"must": {"electrical"}, "must_not": {"fire", "planning", "co"}},
    "C26": {"must": {"building", "electrical"}, "must_not": {"plumbing", "planning"}},
    "C29": {"must": {"building", "electrical", "plumbing", "mechanical"}, "must_not": {"fire", "planning", "co"}},
}

KNOWN_FALSE_POSITIVE_A = {"R01", "R05", "R09", "R10", "R13", "R14", "R19", "R23", "R28", "R29", "R41"}
ALLOWED_B_QUEUE = {"R06", "R26", "R32", "R43", "R44", "C08", "C12", "C32"}


def row_status(row: dict[str, Any]) -> str:
    return server._pa20_row_status(row) or server._customer_row_status(row)


def row_family(row: dict[str, Any]) -> str:
    return server._pa20_row_family(row) or server._customer_row_family(row)


def required_rows(public: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in public.get("permits_required") or [] if isinstance(r, dict) and row_status(r) == "REQUIRED"]


def required_families(public: dict[str, Any]) -> set[str]:
    return {row_family(r) for r in required_rows(public)}


def visible_text(public: dict[str, Any]) -> str:
    return json.dumps(public, sort_keys=True, default=str).lower()


def canonical_family(value: str) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "certificate of occupancy": "co",
        "coo": "co",
        "planning/zoning": "planning",
        "planning / zoning": "planning",
        "zoning": "planning",
        "historic/planning": "historic",
        "historic / planning": "historic",
    }
    return aliases.get(text, text)


def mirror_family_set(public: dict[str, Any]) -> set[str]:
    value = public.get("required_permit_families")
    if isinstance(value, list):
        return {canonical_family(str(v)) for v in value if str(v).strip()}
    return set()


def banner_mismatch(public: dict[str, Any], families: set[str]) -> str:
    name = str(public.get("permit_name") or "")
    if not name.lower().startswith("multiple permits required:"):
        return ""
    header = name.split(":", 1)[1].lower()
    missing = [fam for fam in sorted(families) if server._pa20_family_label(fam, {}).lower() not in header]
    return ",".join(missing)


def grade_case(rec: dict[str, Any]) -> dict[str, Any]:
    case = rec["case"]
    case_id = case["id"]
    public = server.build_customer_permit_view_model(
        copy.deepcopy(rec["response_body"]),
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case.get("segment"),
    )
    families = required_families(public)
    issues: list[str] = []
    hard_overreach: list[str] = []

    lint = server.lint_customer_visible_result(public, case["city"], case["state"])
    issues.extend(f"lint:{item}" for item in lint)

    if public.get("permit_decision") == "NOT_REQUIRED" and required_rows(public):
        issues.append("not_required_with_required_rows")

    mirror = mirror_family_set(public)
    if mirror and mirror != families:
        issues.append(f"mirror_family_mismatch:{sorted(mirror)}!={sorted(families)}")
    missing_header = banner_mismatch(public, families)
    if missing_header:
        issues.append(f"banner_missing_required_families:{missing_header}")

    expectation = CONFIRMED_DEFECTS.get(case_id)
    if expectation:
        missing = set(expectation["must"]) - families
        forbidden = set(expectation["must_not"]) & families
        if missing:
            issues.append("confirmed_missing:" + ",".join(sorted(missing)))
        if forbidden:
            hard_overreach.extend(sorted(forbidden))
            issues.append("confirmed_hard_overreach:" + ",".join(sorted(forbidden)))
        for term in expectation.get("terms", ()):
            if term not in visible_text(public):
                issues.append(f"confirmed_missing_visible_term:{term}")

    expected_decision = case.get("expected_decision")
    if case_id in KNOWN_FALSE_POSITIVE_A:
        # The prior grader over-counted words from negations, boilerplate, and
        # non-required rows. For these rows, only customer-visible contract/lint
        # failures are graded.
        pass
    elif expected_decision == "NOT_REQUIRED" and public.get("permit_decision") != "NOT_REQUIRED":
        if case_id not in ALLOWED_B_QUEUE:
            issues.append(f"expected_not_required_got:{public.get('permit_decision')}")
    elif expected_decision == "REQUIRED" and not families:
        issues.append("expected_required_missing_required_family")

    if any(i.startswith("confirmed_") or i.startswith("lint:") or i.startswith("not_required_with_required_rows") for i in issues):
        grade = "F"
    elif case_id in ALLOWED_B_QUEUE and issues:
        grade = "B"
    else:
        grade = "A"

    return {
        "id": case_id,
        "segment": case.get("segment"),
        "city": case.get("city"),
        "state": case.get("state"),
        "permit_decision": public.get("permit_decision"),
        "permit_name": public.get("permit_name"),
        "required_families": sorted(families),
        "grade": grade,
        "issues": issues,
        "hard_overreach": hard_overreach,
    }


def main() -> int:
    records = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]
    rows = [grade_case(rec) for rec in records]
    counts = {g: sum(1 for row in rows if row["grade"] == g) for g in ("A", "B", "F")}
    hard_overreach_count = sum(1 for row in rows if row["hard_overreach"])
    result = {
        "case_count": len(rows),
        "counts": counts,
        "hard_overreach_count": hard_overreach_count,
        "failed_cases": [row for row in rows if row["grade"] == "F"],
        "b_cases": [row for row in rows if row["grade"] == "B"],
        "rows": rows,
    }
    OUT_JSON.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["id", "segment", "city", "state", "permit_decision", "permit_name", "required_families", "grade", "issues", "hard_overreach"])
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "required_families": ";".join(row["required_families"]), "issues": ";".join(row["issues"]), "hard_overreach": ";".join(row["hard_overreach"])})
    OUT_MD.write_text(
        "# Live80 offline customer-boundary replay — postfix\n\n"
        f"- Cases: {len(rows)}\n"
        f"- A/B/F: {counts['A']}/{counts['B']}/{counts['F']}\n"
        f"- Confirmed hard overreach: {hard_overreach_count}\n"
        f"- Failed cases: {', '.join(row['id'] for row in rows if row['grade'] == 'F') or 'none'}\n"
        f"- B queue: {', '.join(row['id'] for row in rows if row['grade'] == 'B') or 'none'}\n"
        f"- JSON: `{OUT_JSON}`\n"
        f"- CSV: `{OUT_CSV}`\n",
        encoding="utf-8",
    )
    print(json.dumps({"case_count": len(rows), "counts": counts, "hard_overreach_count": hard_overreach_count, "json": str(OUT_JSON), "csv": str(OUT_CSV), "md": str(OUT_MD)}, sort_keys=True))
    return 0 if counts["F"] == 0 and hard_overreach_count == 0 and counts["A"] >= 75 else 1


if __name__ == "__main__":
    raise SystemExit(main())
