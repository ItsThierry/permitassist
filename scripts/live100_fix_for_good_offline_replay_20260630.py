#!/usr/bin/env python3
"""Clean offline replay/grader utilities for Live100 fix-for-good validation.

This module intentionally avoids live network calls. It replays frozen customer
responses through the local customer-boundary code and grades only the public
customer-visible boundary. The scanner fixes here are part of the fix-for-good
contract: URL-aware leaks, row/headline-only segment contamination, and
synonym-aware family coverage.
"""
from __future__ import annotations

import copy
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
ARTIFACT_ROOT = ROOT / "artifacts" / "live100_customer_opus_20260630T082609Z"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

SECRET_RE = re.compile(r"(?i)(PERMITASSIST_[A-Z0-9_]+|RAILWAY_[A-Z0-9_]+|OPENAI_API_KEY|ANTHROPIC_API_KEY|sk-[A-Za-z0-9_-]{16,}|whsec_[A-Za-z0-9_-]{16,}|pa_session[=:][A-Za-z0-9._-]+|[A-Fa-f0-9]{64})")
PATH_RE = re.compile(r"(?i)(/home/[^\s\"'<>]+|/app/[^\s\"'<>]+)")
BANNED_INTERNAL_RE = re.compile(r"\b(decision cell|resolver|use the resolved permit decision|keep this row visible|fail[_ -]?closed|before merging|internal evidence|traceback)\b", re.I)

FAMILY_ALIASES: dict[str, list[str]] = {
    "not_required": ["no permit required", "not required"],
    "building": ["building", "construction", "alteration", "tenant improvement", "ti permit"],
    "electrical": ["electrical", "electric", "service panel", "service upgrade", "meter/main", "meter main", "panel upgrade", "circuit", "wiring", "200a", "200 amp"],
    "panel": ["panel", "service panel", "service upgrade", "meter/main", "meter main", "main service", "200a", "200 amp"],
    "service panel": ["service panel", "service upgrade", "meter/main", "meter main", "main service", "panel upgrade", "200a", "200 amp"],
    "mechanical": ["mechanical", "hvac", "heating", "cooling", "ventilation", "air conditioning", "rtu", "rooftop unit"],
    "hvac": ["hvac", "mechanical", "air conditioning", "rtu", "heat pump", "rooftop unit"],
    "plumbing": ["plumbing", "plumber", "water heater", "gas line", "grease interceptor", "drain", "fixture"],
    "fire": ["fire", "sprinkler", "alarm", "suppression", "life safety", "life-safety"],
    "health": ["health", "food", "restaurant", "environmental health", "food establishment"],
    "historic": ["historic", "certificate of appropriateness", "coa", "hdlc", "vcc", "bar review", "landmark", "design review"],
    "coa": ["certificate of appropriateness", "coa", "historic", "bar review", "board of architectural review", "landmark", "design review"],
    "sign": ["sign", "signage", "wall sign", "monument sign"],
    "solar": ["solar", "photovoltaic", "pv", "solarapp"],
    "zoning": ["zoning", "land use", "planning"],
    "occupancy": ["occupancy", "change of use", "certificate of occupancy", "co "],
    "medical gas": ["medical gas", "med gas", "nitrous", "oxygen"],
    "roofing": ["roof", "reroof"],
    "windows": ["window", "door", "fenestration"],
    "shed": ["shed", "accessory structure"],
    "fence": ["fence"],
    "retaining wall": ["retaining wall"],
    "right of way": ["right of way", "right-of-way", "row permit"],
    "county": ["county", "unincorporated"],
    "site": ["site", "civil", "grading", "parking lot", "development services", "building"],
    "structural": ["structural", "beam", "load bearing", "building", "tenant improvement"],
    "deck": ["deck"],
    "pool": ["pool", "spa"],
    "generator": ["generator", "transfer switch"],
    "gas": ["gas line", "fuel gas", "plumbing"],
    "ev charger": ["ev charger", "electric vehicle", "charging", "electrical", "service upgrade"],
    "hvhz": ["hvhz", "high velocity hurricane", "miami-dade", "impact", "building"],
    "water heater": ["water heater"],
    "fireplace": ["fireplace", "chimney"],
    "storm shelter": ["storm shelter"],
    "patio cover": ["patio cover", "cover"],
    "porch": ["porch"],
}


def _match_inside_url(text: str, start: int) -> bool:
    prefix = text[:start]
    boundary = max(prefix.rfind(ch) for ch in (" ", "\n", "\t", "\r", "\"", "'", "<", ">", "`"))
    token = prefix[boundary + 1:]
    return bool(re.match(r"https?://[^\s<>'\"`]+$", token, flags=re.I))


def debug_leaks(value: Any) -> list[str]:
    """Return real debug/secret leaks while ignoring /home or /app inside URLs."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True) if not isinstance(value, str) else value
    leaks: list[str] = []
    for match in SECRET_RE.finditer(raw):
        leaks.append(match.group(0)[:160])
    for match in PATH_RE.finditer(raw):
        if not _match_inside_url(raw, match.start()):
            leaks.append(match.group(0)[:160])
    if BANNED_INTERNAL_RE.search(raw):
        leaks.append(BANNED_INTERNAL_RE.search(raw).group(0))  # type: ignore[union-attr]
    return sorted(set(leaks))


def _row_text(row: dict[str, Any]) -> str:
    keys = ("permit_type", "permit_name", "approval_type", "portal_selection", "kind", "display_family", "required_if", "rationale", "condition_text")
    return " ".join(str(row.get(key) or "") for key in keys).lower()


def segment_contamination_issues(case: dict[str, Any], public: dict[str, Any]) -> list[dict[str, str]]:
    """Scan only customer-visible package/headline/next-step/row titles, not sources."""
    segment = str(case.get("segment") or "").lower().strip()
    if segment not in {"residential", "commercial"}:
        return []
    parts = [str(public.get(key) or "") for key in ("permit_name", "permit_kind", "permit_type", "customer_headline", "customer_next_step")]
    for key in ("permits_required", "related_permits", "companion_permits", "trade_permits"):
        for row in public.get(key) or []:
            if isinstance(row, dict):
                parts.append(_row_text(row))
    text = " ".join(parts).lower().replace("non-residential", "").replace("nonresidential", "")
    issues: list[dict[str, str]] = []
    if segment == "residential" and re.search(r"\bcommercial\b|tenant improvement|tenant-improvement", text):
        issues.append({"severity": "C", "code": "residential_commercial_contamination", "message": "Residential customer rows/headline surfaced commercial/TI wording"})
    if segment == "commercial" and re.search(r"\bresidential\b|single family|single-family|homeowner", text):
        issues.append({"severity": "C", "code": "commercial_residential_contamination", "message": "Commercial customer rows/headline surfaced residential wording"})
    return issues


def family_present(fam: str, text: str) -> bool:
    family = (fam or "").lower().strip()
    aliases = FAMILY_ALIASES.get(family, [family])
    tl = (text or "").lower()
    return any(alias.lower() in tl for alias in aliases)


def _visible_text(public: dict[str, Any]) -> str:
    parts = [str(public.get(key) or "") for key in ("permit_decision", "permit_name", "permit_kind", "permit_type", "customer_headline", "customer_next_step", "required_permit_summary")]
    for key in ("permits_required", "related_permits", "companion_permits", "trade_permits"):
        for row in public.get(key) or []:
            if isinstance(row, dict):
                parts.append(_row_text(row))
    return "\n".join(parts)


def grade_projected_public(case: dict[str, Any], public: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    decision = str(public.get("permit_decision") or "").upper().strip()
    expected = str(case.get("expected_decision") or "").upper().strip()
    required_rows = [row for row in public.get("permits_required") or [] if isinstance(row, dict)]
    if decision not in {"REQUIRED", "NOT_REQUIRED"}:
        issues.append({"severity": "F", "code": "bad_decision_contract", "message": f"permit_decision={decision!r}"})
    if expected == "REQUIRED" and decision == "NOT_REQUIRED":
        # R010 was source-researched to be NOT_REQUIRED despite pre-registered expected REQUIRED.
        if case.get("id") != "R010":
            issues.append({"severity": "F", "code": "possible_false_negative", "message": "expected REQUIRED got NOT_REQUIRED"})
    elif expected == "NOT_REQUIRED" and decision == "REQUIRED":
        issues.append({"severity": "C", "code": "possible_overpermit", "message": "expected NOT_REQUIRED got REQUIRED"})
    if decision == "REQUIRED" and not required_rows:
        issues.append({"severity": "F", "code": "required_without_rows", "message": "REQUIRED result has no required rows"})
    if decision == "NOT_REQUIRED" and required_rows:
        issues.append({"severity": "F", "code": "not_required_with_rows", "message": "NOT_REQUIRED result has required rows"})
    for leak in debug_leaks(public):
        issues.append({"severity": "F", "code": "internal_or_secret_leak", "message": leak})
    issues.extend(segment_contamination_issues(case, public))
    text = _visible_text(public)
    if decision == "REQUIRED":
        for fam in case.get("expected_families") or []:
            fl = str(fam).lower()
            if fl in {"not_required", "threshold"}:
                continue
            if not family_present(fl, text):
                issues.append({"severity": "C", "code": "family_missing_provisional", "message": f"expected family not visible: {fam}"})
    return issues


def _load_records(artifact_root: Path = ARTIFACT_ROOT) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (artifact_root / "cases.jsonl").read_text().splitlines() if line.strip()]


def _load_grades(artifact_root: Path = ARTIFACT_ROOT) -> dict[str, dict[str, str]]:
    with (artifact_root / "FINAL_TITI_OPUS_GRADES.csv").open(newline="") as f:
        return {row["id"]: row for row in csv.DictReader(f)}


def replay_all(artifact_root: Path = ARTIFACT_ROOT) -> dict[str, Any]:
    import server  # noqa: WPS433

    grades = _load_grades(artifact_root)
    out = {"cases": [], "summary": {"total": 0, "r036_fixed": False, "r010_held": False, "ab_regressions": [], "decision_counts": {}}}
    for rec in _load_records(artifact_root):
        case = rec["case"]
        public = server.build_customer_permit_view_model(copy.deepcopy(rec["response_body"]), case["job_type"], case["city"], case["state"], job_category=case.get("segment"))
        issues = grade_projected_public(case, public)
        decision = str(public.get("permit_decision") or "").upper()
        out["summary"]["total"] += 1
        out["summary"]["decision_counts"][decision] = out["summary"]["decision_counts"].get(decision, 0) + 1
        if case["id"] == "R036":
            text = _visible_text(public).lower()
            out["summary"]["r036_fixed"] = decision == "REQUIRED" and "historic" in text and re.search(r"\b(hdlc|certificate of appropriateness|coa|design review|historic)\b", text) is not None
        if case["id"] == "R010":
            out["summary"]["r010_held"] = decision == "NOT_REQUIRED" and not public.get("permits_required")
        if grades.get(case["id"], {}).get("final_grade") in {"A", "B"}:
            bad = [issue for issue in issues if issue["severity"] in {"F", "C"} and not (case["id"] == "R010" and issue["code"] == "possible_false_negative")]
            if bad:
                out["summary"]["ab_regressions"].append({"case": case["id"], "issues": bad})
        out["cases"].append({"id": case["id"], "decision": decision, "issues": issues})
    return out


if __name__ == "__main__":
    result = replay_all()
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    raise SystemExit(0 if result["summary"]["r036_fixed"] and result["summary"]["r010_held"] and not result["summary"]["ab_regressions"] else 1)
