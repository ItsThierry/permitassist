"""PermitAssist 2.0 contractor-usefulness contract.

This module is intentionally deterministic and local: it scores whether a
finalized lookup gives a contractor an operating packet, not just whether the
response avoided banned trust strings.
"""

from __future__ import annotations

import json
import re
from typing import Any

SAFE_INTERIM_PERMIT_LABEL = "Manual filing path confirmation in progress"

RENDER_ORDER = [
    "ruling",
    "exact_filing_path",
    "portal_selection_or_ask",
    "office_or_portal",
    "docs_checklist",
    "companions",
    "inspections_sequence",
    "sources",
    "caveat",
]

_SLOT_LABELS = {
    "ruling": "permit ruling",
    "exact_filing_path": "exact filing name/path or safe interim",
    "portal_selection_or_ask": "portal selection or what to ask",
    "office_or_portal": "office/portal",
    "docs_checklist": "documents/checklist",
    "companions": "companion permits/reviews",
    "inspections_sequence": "inspections/sequence",
    "sources": "official sources",
    "caveat": "caveat/verification note",
}

_ACTIONABLE_TERMS = {
    "permit",
    "required",
    "apply",
    "portal",
    "office",
    "inspection",
    "document",
    "plans",
    "review",
    "source",
    "filing",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, sort_keys=True)
    except Exception:
        return str(value)


def _has_text(value: Any) -> bool:
    text = _text(value)
    return bool(text and text.lower() not in {"none", "null", "n/a", "[]", "{}"})


def _is_safe_interim(value: Any) -> bool:
    return SAFE_INTERIM_PERMIT_LABEL.lower() in _text(value).lower()


def _first_present(*values: Any) -> str:
    for value in values:
        if _has_text(value):
            return _text(value)
    return ""


def _list_has_items(value: Any) -> bool:
    if isinstance(value, list):
        return any(_has_text(item) for item in value)
    return _has_text(value)


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _permit_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    raw = result.get("permits_required")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _status(status: str, evidence: Any = "") -> dict[str, Any]:
    return {"status": status, "evidence": evidence}


def _ruling_slot(result: dict[str, Any]) -> dict[str, Any]:
    verdict = _first_present(result.get("permit_verdict"), result.get("verdict"))
    if result.get("permit_required") is True or verdict.upper() == "YES":
        return _status("present_exact", "Permit required")
    if result.get("permit_required") is False or verdict.upper() == "NO":
        return _status("present_exact", "Permit not required")
    return _status("missing")


def _apply_path_is_supported(apply_path: dict[str, Any]) -> bool:
    support = _text(apply_path.get("support_level")).lower()
    return support == "verified path"


def _permit_name_verified(result: dict[str, Any]) -> bool:
    return result.get("permit_type_verified") is not False


def _exact_filing_path_slot(result: dict[str, Any]) -> dict[str, Any]:
    apply_path = _dict_value(result.get("apply_path"))
    names = [result.get("permit_name"), result.get("permit_type")]
    if _permit_name_verified(result):
        names.extend([result.get("_permit_display_name"), apply_path.get("permit_type")])
    elif _apply_path_is_supported(apply_path):
        names.append(apply_path.get("permit_type"))
    for permit in _permit_items(result):
        if _permit_name_verified(result) or _apply_path_is_supported(apply_path):
            names.append(permit.get("permit_type"))
            names.append(permit.get("portal_selection"))
        else:
            names.extend(
                item for item in (permit.get("permit_type"), permit.get("portal_selection")) if _is_safe_interim(item)
            )
    exact_names = [name for name in names if _has_text(name) and not _is_safe_interim(name)]
    if exact_names:
        return _status("present_exact", exact_names[0])
    if any(_is_safe_interim(name) for name in names) or _has_text(apply_path.get("verification_note")):
        return _status("present_safe_interim", SAFE_INTERIM_PERMIT_LABEL)
    return _status("missing")


def _portal_selection_slot(result: dict[str, Any]) -> dict[str, Any]:
    apply_path = _dict_value(result.get("apply_path"))
    candidates = [apply_path.get("portal"), apply_path.get("verification_note")]
    if _permit_name_verified(result) or _apply_path_is_supported(apply_path):
        candidates.append(apply_path.get("permit_type"))
        steps = apply_path.get("application_steps")
        if isinstance(steps, list):
            candidates.extend(steps)
        for permit in _permit_items(result):
            candidates.append(permit.get("portal_selection"))
    manual_confirmation = any(
        "confirm" in _text(item).lower() or "manual filing path check" in _text(item).lower()
        for item in candidates
    )
    exact = [
        item
        for item in candidates
        if _has_text(item)
        and not _is_safe_interim(item)
        and "manual filing path check" not in _text(item).lower()
        and "confirm the final filing category" not in _text(item).lower()
    ]
    if exact:
        return _status("present_exact", exact[0])
    if any(_is_safe_interim(item) for item in candidates) or manual_confirmation:
        return _status("present_safe_interim", "confirm final filing category")
    return _status("missing")


def _office_slot(result: dict[str, Any]) -> dict[str, Any]:
    apply_path = _dict_value(result.get("apply_path"))
    value = _first_present(result.get("applying_office"), result.get("office_name"), result.get("apply_url"), apply_path.get("portal"))
    return _status("present_exact", value) if value else _status("missing")


def _docs_slot(result: dict[str, Any]) -> dict[str, Any]:
    for key in ("docs_required", "required_documents", "checklist", "document_checklist"):
        if _list_has_items(result.get(key)):
            return _status("present_exact", result.get(key))
    return _status("missing")


def _companions_slot(result: dict[str, Any]) -> dict[str, Any]:
    for key in ("companion_reviews_triggers", "companion_permits", "companion_reviews", "additional_reviews"):
        if _list_has_items(result.get(key)):
            return _status("present_exact", result.get(key))
    return _status("missing")


def _generated_inspection_notice(value: Any) -> bool:
    text = _text(value).lower()
    return bool(
        text
        and (
            "google.com/maps/search" in text
            or "advance notice may be required" in text
            or "verify when booking" in text
        )
    )


def _inspections_slot(result: dict[str, Any]) -> dict[str, Any]:
    for key in ("inspections", "inspection_sequence", "inspection_booking"):
        value = result.get(key)
        if _list_has_items(value):
            items = value if isinstance(value, list) else [value]
            if all(_generated_inspection_notice(item) for item in items):
                return _status("present_safe_interim", value)
            return _status("present_exact", value)
    return _status("missing")


def _sources_slot(result: dict[str, Any]) -> dict[str, Any]:
    for key in ("sources", "claim_citations", "official_sources"):
        if _list_has_items(result.get(key)):
            return _status("present_exact", result.get(key))
    return _status("missing")


def _caveat_slot(result: dict[str, Any]) -> dict[str, Any]:
    warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    apply_path = _dict_value(result.get("apply_path"))
    evidence = warnings or apply_path.get("verification_note") or result.get("verification_note") or "caveat slot reserved last"
    return _status("caveat_last", evidence)


_SLOT_BUILDERS = {
    "ruling": _ruling_slot,
    "exact_filing_path": _exact_filing_path_slot,
    "portal_selection_or_ask": _portal_selection_slot,
    "office_or_portal": _office_slot,
    "docs_checklist": _docs_slot,
    "companions": _companions_slot,
    "inspections_sequence": _inspections_slot,
    "sources": _sources_slot,
    "caveat": _caveat_slot,
}


def score_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic contractor-usefulness score for a finalized result."""
    ordered_slots: list[dict[str, Any]] = []
    score = 0
    missing: list[str] = []
    for key in RENDER_ORDER:
        raw = _SLOT_BUILDERS[key](result)
        status = raw["status"]
        evidence = raw.get("evidence", "")
        points = 0 if key == "caveat" else (1 if status in {"present_exact", "present_safe_interim"} else 0)
        if status == "present_safe_interim" and key in {"exact_filing_path", "inspections_sequence"}:
            # Safe interim containment/generic booking hints are not filing-grade usefulness.
            # They should not make a caveat-only YES look actionable.
            points = 0
        score += points
        if key != "caveat" and points == 0:
            missing.append(key)
        ordered_slots.append(
            {
                "key": key,
                "label": _SLOT_LABELS[key],
                "status": status,
                "points": points,
                "evidence": evidence,
            }
        )
    release_gate = "pass" if score >= 5 else "fail"
    return {
        "score": score,
        "max_score": len(RENDER_ORDER) - 1,
        "release_gate": release_gate,
        "ordered_slots": ordered_slots,
        "missing_slots": missing,
    }


def attach_usefulness_contract(result: dict[str, Any]) -> dict[str, Any]:
    """Attach render order/usefulness metadata and a customer-safe low-score warning."""
    if not isinstance(result, dict):
        return result
    contract = score_result(result)
    result["_render_order"] = list(RENDER_ORDER)
    result["_usefulness"] = contract
    permit_required = result.get("permit_required") is True or _text(result.get("permit_verdict")).upper() == "YES"
    if permit_required and contract["release_gate"] == "fail":
        warning = "Contractor operating packet is incomplete; PermitAssist is preparing a manual completion pass before this should be used for filing."
        warnings = result.setdefault("warnings", [])
        if not isinstance(warnings, list):
            warnings = [str(warnings)] if warnings else []
            result["warnings"] = warnings
        if warning not in warnings:
            warnings.append(warning)
    return result


def _baseline_score(text: str) -> int:
    normalized = text.lower()
    score = 0
    for term in _ACTIONABLE_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", normalized):
            score += 1
    if "check with" in normalized or "confirm" in normalized:
        score += 1
    # Cap so a verbose but generic answer cannot beat a structured PermitAssist packet.
    return min(score, 5)


def score_against_static_baseline(
    permitassist_result: dict[str, Any],
    generic_baseline: str,
    *,
    job_type: str = "",
    city: str = "",
    state: str = "",
) -> dict[str, Any]:
    """Compare PermitAssist to a local/static generic-chatbot baseline."""
    pa_contract = score_result(permitassist_result)
    pa_score = int(pa_contract["score"])
    baseline_score = _baseline_score(generic_baseline)
    losses: list[str] = []
    if pa_score <= baseline_score:
        losses.append("PermitAssist did not beat the static generic baseline on contractor actionability.")
    winner = "permitassist" if pa_score > baseline_score else "baseline"
    return {
        "winner": winner,
        "permitassist_score": pa_score,
        "baseline_score": baseline_score,
        "losses": losses,
        "rubric": list(RENDER_ORDER),
        "scenario": {"job_type": job_type, "city": city, "state": state},
    }
