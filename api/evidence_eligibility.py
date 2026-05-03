"""Hard eligibility gates for state evidence before report composition.

This module is intentionally conservative. It adapts older broad state expert
notes into explicit scope checks so residential/ADU/solar/roofing evidence does
not leak into commercial TI reports. It is not the future Evidence Pack Tool;
it is the first runtime eligibility layer that prevents unsafe evidence from
reaching model/report rendering.
"""

from __future__ import annotations

import re
from typing import Iterable


COMMERCIAL_TI_VERTICALS = {
    "restaurant_ti",
    "medical_clinic_ti",
    "office_ti",
}

_COMMERCIAL_TI_TERMS = (
    "commercial", "tenant improvement", "tenant buildout", "ti", "change of occupancy",
    "certificate of occupancy", "retail", "restaurant", "office", "clinic", "medical", "dental",
)
_MEDICAL_TERMS = (
    "medical", "clinic", "dental", "exam room", "x-ray", "xray", "nitrous",
    "medical gas", "surgery", "ambulatory", "patient", "treatment room",
)
_RESIDENTIAL_USE_TERMS = (
    "adu", "accessory dwelling", "jadu", "sb 9", "sfr", "single-family", "single family",
    "duplex", "lot split", "garage adu", "garage conversion", "dwelling unit", "dwelling",
    "homeowner", "residential", "owner-occupancy", "owner occupancy", "residential infill",
)
_SOLAR_USE_TERMS = (
    "solar", "pv", "photovoltaic", "battery", "ess", "energy storage", "interconnection",
)
_ROOF_USE_TERMS = ("roof", "reroof", "re-roof", "roofing")
_EXTERIOR_WILDFIRE_TERMS = (
    "wildfire", "vhfhsz", "fire hazard", "defensible", "exterior", "siding", "deck", "roof",
)
_COMMERCIAL_SAFE_TERMS = (
    "contractor", "license", "licensing", "cslb", "tdlr", "electrical", "plumbing",
    "municipal utility", "utility", "service", "code-change", "code change", "building standards code",
    "accessibility", "ada", "path of travel", "occupant load", "egress", "exit", "sprinkler",
    "fire alarm", "certificate of occupancy", "title 24", "energy", "mechanical ventilation",
)


def normalize_scope_token(value: str | None) -> str:
    text = (value or "").strip().lower()
    mappings = {
        "commercial_restaurant_ti": "restaurant_ti",
        "commercial_restaurant": "restaurant_ti",
        "commercial_medical_clinic_ti": "medical_clinic_ti",
        "commercial_office_ti": "office_ti",
    }
    for old, new in mappings.items():
        text = text.replace(old, new)
    return text


def infer_vertical(job_description: str = "", primary_scope: str | None = None) -> str | None:
    scope = normalize_scope_token(primary_scope)
    for vertical in COMMERCIAL_TI_VERTICALS:
        if vertical in scope:
            return vertical
    text = f"{scope} {job_description or ''}".lower()
    if _explicitly_triggers(text, ("restaurant", "food service", "commercial kitchen", "hood", "grease")):
        return "restaurant_ti"
    if _explicitly_triggers(text, _MEDICAL_TERMS):
        return "medical_clinic_ti"
    if _explicitly_triggers(text, ("office", "conference", "suite", "data", "low voltage")):
        return "office_ti"
    return None


def is_commercial_ti_scope(job_description: str = "", primary_scope: str | None = None) -> bool:
    scope = normalize_scope_token(primary_scope)
    text = f"{scope} {job_description or ''}".lower()
    if scope.startswith("residential") or scope in {"adu", "residential_adu", "solar", "roofing"}:
        return False
    if infer_vertical(job_description, primary_scope) in COMMERCIAL_TI_VERTICALS:
        return True
    return _explicitly_triggers(text, _COMMERCIAL_TI_TERMS) and _explicitly_triggers(text, ("tenant improvement", "buildout", "ti", "commercial"))


def filter_state_expert_notes(
    notes: Iterable[dict],
    *,
    state: str = "",
    city: str = "",
    job_description: str = "",
    primary_scope: str | None = None,
) -> list[dict]:
    """Return only notes eligible for the requested scope.

    Phase 4 first slice: commercial TI must fail closed for residential/ADU/SFR,
    solar/ESS, roof, and exterior/wildfire notes unless those concepts are
    explicitly in scope. Existing non-commercial behavior remains permissive so
    residential/ADU/trade-only regressions are avoided while we harden commercial
    launch verticals first.
    """
    note_list = [note for note in notes if isinstance(note, dict)]
    if not is_commercial_ti_scope(job_description, primary_scope):
        return list(note_list)

    job_text = (job_description or "").lower()
    eligible: list[dict] = []
    for note in note_list:
        if _note_allowed_for_commercial_ti(note, job_text):
            eligible.append(note)
    return eligible


def _note_allowed_for_commercial_ti(note: dict, job_text: str) -> bool:
    note_text = _note_text(note)

    if _has_any(note_text, ("building standards code", "code-change", "code change")):
        return True

    if _has_any(note_text, _RESIDENTIAL_USE_TERMS):
        return _explicitly_triggers(job_text, _RESIDENTIAL_USE_TERMS)

    if _has_any(note_text, _SOLAR_USE_TERMS):
        return _explicitly_triggers(job_text, _SOLAR_USE_TERMS)

    if _has_any(note_text, _ROOF_USE_TERMS):
        return _explicitly_triggers(job_text, _ROOF_USE_TERMS)

    if _has_any(note_text, ("vhfhsz", "wildfire", "fire hazard severity", "defensible-space", "defensible space")):
        return _explicitly_triggers(job_text, _EXTERIOR_WILDFIRE_TERMS)

    if _has_any(note_text, _COMMERCIAL_SAFE_TERMS):
        return True

    # Unknown legacy notes are not allowed into commercial TI until tagged.
    return False


def _note_text(note: dict) -> str:
    fields = [note.get("title"), note.get("note"), note.get("applies_to"), note.get("source")]
    return "\n".join(str(field or "") for field in fields).lower()


def _has_any(text: str, terms: Iterable[str]) -> bool:
    return any(_contains_term(text, term) for term in terms)


def _explicitly_triggers(text: str, terms: Iterable[str]) -> bool:
    # This layer intentionally treats negated prompts as non-triggers. The engine
    # has richer negation helpers elsewhere; here we only need deterministic
    # conservative protection for evidence eligibility.
    lowered = f" {text.lower()} "
    for term in terms:
        term_l = term.lower().strip()
        if not term_l:
            continue
        pattern = rf"(?<![a-z0-9]){re.escape(term_l)}(?![a-z0-9])"
        for match in re.finditer(pattern, lowered):
            before = lowered[max(0, match.start() - 32):match.start()]
            if re.search(r"\b(no|not|without|exclude|excluding|non)\b", before):
                continue
            return True
    return False


def _contains_term(text: str, term: str) -> bool:
    term_l = term.lower().strip()
    if not term_l:
        return False
    if re.search(r"[a-z0-9]", term_l):
        return re.search(rf"(?<![a-z0-9]){re.escape(term_l)}(?![a-z0-9])", text) is not None
    return term_l in text
