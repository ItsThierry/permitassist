#!/usr/bin/env python3
"""Phase 7D buyer-readiness scorer.

This scorer separates customer usefulness from binary boundary guards. The 30-case
inspection showed the previous manual scoring template mechanically capped normal
passing cases at 4.0, making a commercial >=4.2 threshold impossible even for a
complete answer. This module gives launch-core cases a real 5-point rubric while
excluding guard-only cases from buyer-usefulness averages.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import mean
from urllib.parse import urlparse

BINARY_GUARD_GROUPS = {"route_browser_guard", "auth_boundary_guard", "security_boundary_guard"}
SUPPORTED_SCENARIO_TYPES = {
    "positive_supported",
    "positive_core_state",
    "negative_trap",
    "random_fallback",
    "unsupported_boundary",
}


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "pass", "ok"}


def _valid_http_url(value) -> bool:
    parsed = urlparse(str(value or ""))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _present(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return bool(str(value).strip())


def _status_ok(observed: dict) -> bool:
    for key in ("http_status_or_browser_status", "status"):
        value = observed.get(key)
        if value in (None, ""):
            continue
        try:
            return int(value) < 500
        except (TypeError, ValueError):
            text = str(value).strip().upper()
            if text in {"EXECUTED", "OK", "PASS", "PASSED", "SUCCESS"}:
                return True
            if text in {"FAIL", "FAILED", "ERROR", "BLOCKED"}:
                return False
    return True


def _forbidden_markers_present(observed: dict) -> list[str]:
    markers = observed.get("forbidden_internal_markers_present") or observed.get("review_marker_terms") or []
    if isinstance(markers, str):
        return [m.strip() for m in markers.split(";") if m.strip()]
    return [str(m) for m in markers if str(m).strip()]


def _coverage_truth_ok(observed: dict) -> bool:
    if "coverage_truth_ok" in observed:
        return _truthy(observed.get("coverage_truth_ok"))
    if "coverage_truth_ok_yes_no" in observed:
        return _truthy(observed.get("coverage_truth_ok_yes_no"))
    return isinstance(observed.get("coverage_truth"), dict) and bool(observed.get("coverage_truth"))


def _apply_path_ok(observed: dict) -> bool:
    if "apply_path_ok" in observed:
        return _truthy(observed.get("apply_path_ok"))
    if "apply_path_ok_yes_no" in observed:
        return _truthy(observed.get("apply_path_ok_yes_no"))
    return _valid_http_url(observed.get("apply_url")) or _present(observed.get("apply_phone")) or _present(observed.get("apply_address"))


def _permits_present(observed: dict) -> bool:
    if "permit_type_or_permits_required_present" in observed:
        return _truthy(observed.get("permit_type_or_permits_required_present"))
    return _present(observed.get("permits_required")) or _present(observed.get("permit_type"))


def score_case(case: dict, observed: dict) -> dict:
    """Score a Phase 7D case without capping useful cases at 4.0.

    Scale:
    - 5: complete, official apply path/contact, coverage truth, permits/scope, no leaks.
    - 4: beta-useful but one non-blocking gap (often missing apply URL or permit label detail).
    - 3: understandable but requires contractor verification before usefulness.
    - 2: materially weak or confusing.
    - 1: wrong AHJ/permit/vertical/internal leak or unusable.
    """
    group = str(case.get("group") or "")
    scenario_type = str(case.get("scenario_type") or "")
    is_guard = group in BINARY_GUARD_GROUPS or scenario_type.startswith("guard")

    status_ok = _status_ok(observed)
    forbidden = _forbidden_markers_present(observed)
    wrong_ahj = _truthy(observed.get("wrong_ahj_yes_no"))
    wrong_permit = _truthy(observed.get("wrong_permit_yes_no"))
    wrong_vertical = _truthy(observed.get("wrong_vertical_leak_yes_no"))
    internal_leak = _truthy(observed.get("internal_leak_yes_no")) or bool(forbidden)
    hard_fail_reasons = []
    if not status_ok:
        hard_fail_reasons.append("non_2xx_or_5xx_status")
    if wrong_ahj:
        hard_fail_reasons.append("wrong_ahj")
    if wrong_permit:
        hard_fail_reasons.append("wrong_permit")
    if wrong_vertical:
        hard_fail_reasons.append("wrong_vertical_leak")
    if internal_leak:
        hard_fail_reasons.append("internal_marker_leak")

    binary_guard_pass = not hard_fail_reasons
    if is_guard:
        return {
            "include_in_buyer_usefulness_average": False,
            "binary_guard_pass": binary_guard_pass,
            "usefulness_1_5": None,
            "trust_1_5": None,
            "clarity_1_5": None,
            "overall_case_score": None,
            "hard_fail_yes_no": "YES" if hard_fail_reasons else "NO",
            "hard_fail_reasons": ";".join(hard_fail_reasons),
        }

    apply_ok = _apply_path_ok(observed)
    coverage_ok = _coverage_truth_ok(observed)
    permits_ok = _permits_present(observed)
    snippet = str(observed.get("snippet") or observed.get("response_snippet") or "").strip()
    clarity_ok = bool(snippet) or _present(observed.get("checklist")) or _present(observed.get("next_steps"))

    usefulness = 5
    trust = 5
    clarity = 5
    missing = []

    if hard_fail_reasons:
        usefulness = trust = clarity = 1
    else:
        if not permits_ok:
            usefulness -= 2
            trust -= 1
            missing.append("permit_type_or_permits_required")
        if not apply_ok:
            usefulness -= 1
            trust -= 1
            missing.append("official_apply_path_or_contact")
        if not coverage_ok:
            trust -= 1
            missing.append("coverage_truth")
        if not clarity_ok:
            clarity -= 1
            missing.append("contractor_readable_summary")
        if scenario_type not in SUPPORTED_SCENARIO_TYPES:
            trust -= 1
            missing.append("rubric_scenario_type_unknown")

    usefulness = max(1, min(5, usefulness))
    trust = max(1, min(5, trust))
    clarity = max(1, min(5, clarity))
    overall = round(mean([usefulness, trust, clarity]), 2)

    return {
        "include_in_buyer_usefulness_average": True,
        "binary_guard_pass": None,
        "usefulness_1_5": usefulness,
        "trust_1_5": trust,
        "clarity_1_5": clarity,
        "overall_case_score": overall,
        "hard_fail_yes_no": "YES" if hard_fail_reasons else "NO",
        "hard_fail_reasons": ";".join(hard_fail_reasons),
        "apply_path_ok_yes_no": "YES" if apply_ok else "NO",
        "coverage_truth_ok_yes_no": "YES" if coverage_ok else "NO",
        "permit_type_or_permits_required_present": permits_ok,
        "what_was_missing": ";".join(missing),
        "would_use_yes_no": "YES" if overall >= 4 else "MAYBE" if overall >= 3 else "NO",
        "would_pay_yes_no": "YES" if overall >= 4.2 else "MAYBE" if overall >= 3.5 else "NO",
        "would_recommend_yes_no": "YES" if overall >= 4.2 else "MAYBE" if overall >= 3.5 else "NO",
    }


def _load_json(path: Path):
    return json.loads(path.read_text())


def _iter_cases(matrix_json: Path):
    data = _load_json(matrix_json)
    return data.get("cases") or data.get("rows") or data


def observed_payload_for_scoring(row: dict) -> dict:
    """Normalize observed rows from authenticated redacted artifacts.

    The rerun artifact stores reviewer booleans under `score` and customer-safe
    response fields under `redacted_result`. Score using both; otherwise the
    scorer treats complete cases as missing every field.
    """
    if not isinstance(row, dict):
        return {}
    observed: dict = {}
    redacted = row.get("redacted_result")
    if isinstance(redacted, dict):
        observed.update(redacted)
    review_score = row.get("score")
    if isinstance(review_score, dict):
        observed.update(review_score)
    for key, value in row.items():
        if key not in {"case", "redacted_result", "score"}:
            observed.setdefault(key, value)
    return observed


def _main() -> int:
    parser = argparse.ArgumentParser(description="Score Phase 7D buyer-readiness cases.")
    parser.add_argument("--matrix-json", type=Path, required=True)
    parser.add_argument("--observed-json", type=Path, required=True)
    parser.add_argument("--out-csv", type=Path, required=True)
    args = parser.parse_args()

    cases = list(_iter_cases(args.matrix_json))
    observed_data = _load_json(args.observed_json)
    observed_rows = observed_data.get("results") if isinstance(observed_data, dict) else observed_data
    observed_by_id = {}
    for row in observed_rows or []:
        case_id = row.get("id") or row.get("case_id") or (row.get("case") or {}).get("id")
        if case_id:
            observed_by_id[case_id] = row

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "id", "group", "vertical", "scenario_type", "include_in_buyer_usefulness_average",
        "binary_guard_pass", "usefulness_1_5", "trust_1_5", "clarity_1_5", "overall_case_score",
        "hard_fail_yes_no", "hard_fail_reasons", "apply_path_ok_yes_no", "coverage_truth_ok_yes_no",
        "permit_type_or_permits_required_present", "what_was_missing", "would_use_yes_no", "would_pay_yes_no", "would_recommend_yes_no",
    ]
    with args.out_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for case in cases:
            case_id = case.get("id")
            observed = observed_payload_for_scoring(observed_by_id.get(case_id, {}))
            score = score_case(case, observed)
            writer.writerow({k: case.get(k, score.get(k, "")) for k in fieldnames} | {"id": case_id, **score})
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
