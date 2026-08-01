"""Status-aware PermitAssist family scoring over the canonical manifest ontology."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from api.permit_manifest import FAMILIES, STATUSES, canonical_family


class UnadjudicatedGoldError(RuntimeError):
    """Raised when a publishable score is requested against disputed gold."""


_STATUS_ALIASES = {
    "YES": "REQUIRED",
    "NO": "NOT_REQUIRED",
    "MAYBE": "CONDITIONAL",
    "CONDITIONAL_REQUIRED": "CONDITIONAL",
    "UNKNOWN": "VERIFY",
    "CONTACT_AHJ": "VERIFY",
    "ABSTAIN": "VERIFY",
    "NO_EVIDENCE": "VERIFY",
}
_STATUS_STRENGTH = {
    "NOT_REQUIRED": 0,
    "VERIFY": 1,
    "NEEDS_INPUT": 1,
    "CONDITIONAL": 2,
    "REQUIRED": 3,
}


def normalize_family_id(value: Any) -> str:
    family = canonical_family(value)
    if family not in FAMILIES:
        raise ValueError(f"unknown canonical permit family: {value!r}")
    return family


def normalize_status(value: Any) -> str:
    status = str(value or "").upper().strip()
    status = _STATUS_ALIASES.get(status, status)
    if status not in STATUSES:
        raise ValueError(f"unknown permit status: {value!r}")
    return status


def _pairs(rows: Iterable[tuple[Any, Any]]) -> set[tuple[str, str]]:
    return {(normalize_family_id(family), normalize_status(status)) for family, status in rows}


def collapse_strongest(rows: Iterable[tuple[Any, Any]]) -> dict[str, str]:
    """Collapse duplicate family rows to the strongest customer-visible status."""
    collapsed: dict[str, str] = {}
    for family, status in _pairs(rows):
        if _STATUS_STRENGTH[status] > _STATUS_STRENGTH.get(collapsed.get(family, ""), -1):
            collapsed[family] = status
    return collapsed


def extract_customer_family_statuses(response: dict[str, Any]) -> list[tuple[str, str]]:
    """Extract structured rows; never infer a family from display prose when rows exist."""
    manifest = response.get("permit_manifest") if isinstance(response, dict) else None
    rows: list[dict[str, Any]] = []
    if isinstance(manifest, dict):
        primary = manifest.get("primary")
        if isinstance(primary, dict):
            rows.append(primary)
        rows.extend(row for row in (manifest.get("companions") or []) if isinstance(row, dict))
    if not rows:
        for key in ("family_decisions", "permits_required", "related_permits", "companion_permits"):
            rows.extend(row for row in (response.get(key) or []) if isinstance(row, dict))
    extracted: list[tuple[str, str]] = []
    for row in rows:
        family_value = row.get("canonical_family") or row.get("family") or row.get("filing_family") or row.get("category")
        status_value = row.get("status") or row.get("required_status") or row.get("decision")
        if status_value is None:
            if row.get("required") is True:
                status_value = "REQUIRED"
            elif row.get("required") is False:
                status_value = "NOT_REQUIRED"
            else:
                status_value = "VERIFY"
        extracted.append((normalize_family_id(family_value), normalize_status(status_value)))
    if not extracted and str(response.get("permit_decision") or "").upper() == "NOT_REQUIRED":
        extracted.append(("NO_PRIMARY_PERMIT", "NOT_REQUIRED"))
    return sorted(collapse_strongest(extracted).items())


def score_family_status_pairs(*, truth, predicted, gold_adjudicated: bool):
    """Score canonical family/status pairs without treating conditional as hard truth."""
    if not gold_adjudicated:
        raise UnadjudicatedGoldError(
            "family/status score is unavailable until the gold envelope is adjudicated"
        )
    expected = collapse_strongest(truth)
    actual = collapse_strongest(predicted)
    families = sorted(set(expected) | set(actual))
    diagnostics: list[dict[str, str | None]] = []
    counts = defaultdict(int)
    for family in families:
        gold = expected.get(family)
        observed = actual.get(family)
        if gold == observed:
            counts["exact_status_tp"] += 1
            if gold == "REQUIRED":
                counts["required_tp"] += 1
            if gold == "CONDITIONAL":
                counts["conditional_tp"] += 1
            continue
        diagnostics.append({"family_id": family, "truth_status": gold, "predicted_status": observed})
        if gold == "REQUIRED":
            counts["required_fn"] += 1
        if observed == "REQUIRED":
            counts["hard_false_positives"] += 1
            if gold == "CONDITIONAL":
                counts["conditional_as_hard_overclaims"] += 1
            if gold == "NOT_REQUIRED" or gold is None:
                counts["dangerous_hard_false_positives"] += 1
        if gold is None:
            counts["unsupported_family_mentions"] += 1
        elif observed is None:
            counts["missing_family_mentions"] += 1
        else:
            counts["status_mismatches"] += 1
    required_precision_den = counts["required_tp"] + counts["hard_false_positives"]
    required_recall_den = counts["required_tp"] + counts["required_fn"]
    return {
        "true_positives": counts["exact_status_tp"],
        "false_positives": counts["hard_false_positives"],
        "false_negatives": counts["required_fn"],
        "required_true_positives": counts["required_tp"],
        "required_false_negatives": counts["required_fn"],
        "hard_false_positives": counts["hard_false_positives"],
        "conditional_true_positives": counts["conditional_tp"],
        "conditional_as_hard_overclaims": counts["conditional_as_hard_overclaims"],
        "dangerous_hard_false_positives": counts["dangerous_hard_false_positives"],
        "unsupported_family_mentions": counts["unsupported_family_mentions"],
        "missing_family_mentions": counts["missing_family_mentions"],
        "status_mismatches": counts["status_mismatches"],
        "required_precision": counts["required_tp"] / required_precision_den if required_precision_den else 0.0,
        "required_recall": counts["required_tp"] / required_recall_den if required_recall_den else 0.0,
        "truth_pairs": sorted(expected.items()),
        "predicted_pairs": sorted(actual.items()),
        "diagnostics": diagnostics,
    }
