"""Pure CustomerDecisionDTO resolver for PermitAssist customer surfaces.

This module is intentionally IO-free. It converts any valid lookup context — even
legacy UNKNOWN/FAIL_CLOSED/cache rows or source-starved fresh results — into a
typed customer decision: REQUIRED, NOT_REQUIRED, CONDITIONAL, or VERIFY.
"""

from __future__ import annotations

import re
from typing import Any

PERMIT_DECISION_REQUIRED = "REQUIRED"
PERMIT_DECISION_NOT_REQUIRED = "NOT_REQUIRED"
PERMIT_DECISION_CONDITIONAL = "CONDITIONAL"
PERMIT_DECISION_VERIFY = "VERIFY"
PERMIT_DECISION_NEEDS_INPUT = "NEEDS_INPUT"

_REQUIRED_KIND_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ADU / Accessory Dwelling Unit", ("adu", "accessory dwelling", "garage conversion", "in-law suite", "granny flat")),
    ("Zoning / Land Use", ("zoning", "land use", "land-use", "variance", "conditional use", "rezoning")),
    ("Commercial Building / Tenant Improvement", ("tenant improvement", "tenant finish", "tenant buildout", "build-out", "buildout", "commercial interior", "office ti", "retail ti", "medical clinic", "dental clinic", "daycare", "restaurant", "change of occupancy", "change of use", "commercial remodel")),
    ("Fire", ("hood suppression", "ansul", "fire alarm", "fire sprinkler", "sprinkler", "suppression")),
    ("Solar", ("solar", "photovoltaic", " pv ", "battery storage", "energy storage", "ess")),
    ("Electrical", ("electrical", "electric", "panel", "service upgrade", "service change", "meter", "switchgear", "ev charger", "charger", "circuit", "rewire", "generator", "lighting", "lighting controls")),
    ("Mechanical", ("mechanical", "hvac", "rtu", "furnace", "heat pump", "condenser", "air conditioner", "mini split", "ductwork", "makeup air", "exhaust", "fog machine", "stage fog", "hood")),
    ("Plumbing", ("plumbing", "water heater", "tankless", "sewer", "water line", "gas line", "gas piping", "fixture", "fixtures", "sink", "sinks", "toilet", "toilets", "restroom", "restrooms", "bathroom", "bathrooms", "shower", "showers", "lavatory", "lavatories")),
    ("Roofing", ("reroof", "re-roof", "roof replacement", "roofing", "roof repair", "roof patch", "shingle", "membrane roof")),
    ("Sign", ("sign", "signage", "channel letters", "monument sign")),
    ("Fence", ("fence", "retaining wall")),
    ("Building", ("addition", "adu", "deck", "pool", "egress", "structural", "load bearing", "foundation", "framing", "window", "door", "kitchen remodel", "bath remodel", "bathroom remodel", "remodel", "alteration", "demolition", "garage conversion", "racking", "warehouse racking")),
)

# P1A — Change-of-use anchor guard (2026-06-09).
# When a job explicitly describes a use-type change (dental → fitness, B → A-2,
# etc.), the anchor classifier must select Commercial Building / Tenant Improvement,
# NOT a trade-first category that happens to be mentioned as equipment.
_CHANGE_OF_USE_TERMS = (
    "change of occupancy", "change of use", "change in use",
    "convert", "converted into", "converting", "conversion",
    "reclassify", "reclassification", "use group", "occupancy group",
    "b-occupancy", "a-2 occupancy", "m-occupancy", "i-2 occupancy",
    "business group", "mercantile", "assembly", "institutional",
)

_USE_TYPE_LEXICON = (
    "fitness studio", "gym", "crossfit", "yoga studio", "pilates",
    "physical therapy", "pt clinic", "chiropractor", "dental clinic",
    "medical clinic", "veterinary clinic", "veterinary hospital",
    "restaurant", "cafe", "coffee shop", "bakery", "bar", "brewery",
    "food truck commissary", "ghost kitchen", "retail store", "boutique",
    "salon", "nail salon", "spa", "tanning salon", "tattoo parlor",
    "office", "law office", "accounting office", "insurance office",
    "real estate office", "start up", "coworking", "call center",
    "warehouse", "distribution center", "light industrial", "manufacturing",
    "print shop", "auto repair", "body shop", "car wash",
    "self-storage", "mini storage", "data center", "server room",
)

def _explicit_change_of_use(job_text: str) -> bool:
    """Return True when the request explicitly describes a use-type change."""
    lowered = (job_text or "").lower()
    return any(term in lowered for term in _CHANGE_OF_USE_TERMS) or            any(term in lowered for term in _USE_TYPE_LEXICON)


_SOURCE_ADJUDICATED_NOT_REQUIRED_RULES: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...], str], ...] = (
    (
        "flagstaff",
        "AZ",
        ("faucet", "garbage disposal"),
        ("pipe relocation", "new pipe", "new plumbing", "new wiring", "new circuit"),
        "City of Flagstaff permit-exemption guidance covers same-location faucet/fixture replacement when plumbing is not rearranged; no permit is required for the described simple replacement scope.",
    ),
    (
        "las vegas",
        "NV",
        ("toilet", "vanity", "no plumbing relocation"),
        ("new pipe", "new plumbing", "new drain", "new supply"),
        "City of Las Vegas administrative-code exemption covers replacement of plumbing fixtures in the same location with similar fixtures; no permit is required for the described no-relocation scope.",
    ),
    (
        "chicago",
        "IL",
        ("drywall", "no structural", "no electrical"),
        ("more than 1000", "1,000", "electrical", "plumbing", "mechanical", "structural"),
        "City of Chicago permit-not-required guidance covers removing/replacing up to 1,000 square feet of drywall or plaster without MEP alteration; no permit is required for the described one-room repair.",
    ),
)


def _source_adjudicated_not_required_reason(city: str, state: str, job_text: str) -> str:
    lowered = (job_text or "").lower()
    city_lc = (city or "").lower().strip()
    state_uc = (state or "").upper().strip()
    for rule_city, rule_state, required_terms, disqualifiers, reason in _SOURCE_ADJUDICATED_NOT_REQUIRED_RULES:
        if city_lc != rule_city or state_uc != rule_state:
            continue
        if not all(term in lowered for term in required_terms):
            continue
        if rule_city == "chicago":
            # The Chicago source permits the drywall exemption only when no MEP
            # systems are altered. Keep negated "no electrical/plumbing" clauses
            # from disqualifying, but reject affirmative trade/structural work.
            if _has_affirmative_structural_or_trade(lowered.replace("drywall", "")):
                continue
        elif any(term in lowered and f"no {term}" not in lowered for term in disqualifiers):
            continue
        return reason
    return ""


_KIND_TO_PERMIT_NAME = {
    "ADU / Accessory Dwelling Unit": "ADU / Accessory Dwelling Unit Permit",
    "Zoning / Land Use": "Zoning / Land Use Permit",
    "Building": "Building Permit",
    "Commercial Building / Tenant Improvement": "Commercial Building / Tenant Improvement Permit",
    "Electrical": "Electrical Permit",
    "Mechanical": "Mechanical Permit",
    "Plumbing": "Plumbing Permit",
    "Fire": "Fire / Hood Suppression Permit",
    "Health Department": "Health Department Review",
    "Sign": "Sign Permit",
    "Roofing": "Roofing / Building Permit",
    "Solar": "Solar / Electrical Permit",
    "Fence": "Fence / Building Permit",
    "Other": "Building Permit",
}

_TRIVIAL_NOT_REQUIRED_RE = re.compile(
    r"\b(?:paint|painting|repaint|cosmetic|cosmetic\s+repair|"
    r"patch\s+drywall|drywall\s+patch|drywall\s+repair|"
    r"carpet|flooring|finish\s+flooring|floating\s+laminate|replace\s+flooring|"
    r"(?:replace|replacing|replacement\s+of)\s+(?:kitchen\s+)?cabinets?|"
    r"(?:kitchen\s+)?cabinets?\s+(?:replacement|like[-\s]?for[-\s]?like)|"
    r"cabinet\s+hardware|like[-\s]?for[-\s]?like\s+(?:cabinet|fixture|trim)|"
    r"non[-\s]?structural\s+cosmetic|"
    r"no\s+(?:(?:subfloor|floor|flooring|wall|walls|building|mep|trade|trades|layout)\s+){0,3}(?:structural|electrical|plumbing|mechanical|mep|layout|occupancy|exterior)\s+(?:work|changes?))\b",
    re.I,
)

_STRUCTURAL_OR_TRADE_RE = re.compile(
    r"\b(?:structural|electrical|electric|plumbing|mechanical|hvac|mep|gas|roof|reroof|solar|"
    r"panel|service|water\s+heater|furnace|condenser|rtu|addition|adu|deck|pool|egress|"
    r"occupancy|change\s+of\s+use|layout|wall|framing|tenant\s+improvement|restaurant|hood|fire)\b",
    re.I,
)

_EXPLICIT_NO_TRADE_OR_STRUCTURAL_RE = re.compile(
    r"\bno\s+(?:structural|electrical|plumbing|mechanical|mep|layout|occupancy|exterior|wall|trade)"
    r"(?:\s+(?:work|changes?|scope))?\b",
    re.I,
)

_NEGATED_WORK_CLAUSE_RE = re.compile(
    r"\b(?:no|without|not)\s+(?:new\s+)?"
    r"(?:(?:subfloor|floor|flooring|wall|walls|building|mep|trade|trades|layout)\s+){0,3}"
    r"(?:(?:and\s+|or\s+)?(?:structural|electrical|electric|plumbing|mechanical|hvac|mep|layout|occupancy|exterior|wall|walls|trade|trades|fire(?:\s+alarm|\s+sprinkler)?|signage|sign)"
    r"\s*(?:,|/|\band\b|\bor\b)?\s*)+"
    r"(?:work|changes?|scope|included|modifications?|relocation|repair)?\b",
    re.I,
)


def _has_affirmative_structural_or_trade(text: str) -> bool:
    """Return True for positive trade/structural scope, ignoring local negations.

    Phrases such as "no structural work" should not by themselves make a
    cosmetic result required, but they also must not let a panel/service upgrade
    inherit a stale NOT_REQUIRED decision.
    """
    without_negated_clauses = _NEGATED_WORK_CLAUSE_RE.sub(" ", text or "")
    return bool(_STRUCTURAL_OR_TRADE_RE.search(without_negated_clauses))

_BAD_DECISION_VALUES = {
    "",
    "UNKNOWN",
    "FAIL_CLOSED",
    "FAIL_CLOSED_UNSUPPORTED_OR_NO_EVIDENCE",
    "NONE",
    "NULL",
}

_US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
    "VA", "WA", "WV", "WI", "WY", "DC",
}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _jurisdiction_validation_errors(city: str, state: str) -> list[str]:
    """Return class-level request validation errors before any permit decision.

    The never-UNKNOWN contract applies to valid U.S. AHJ lookups. Invalid or
    unsupported jurisdiction inputs must be rejected as input errors instead of
    being converted into fabricated REQUIRED/NOT_REQUIRED customer decisions.
    """
    errors: list[str] = []
    state_norm = _norm(state).upper()
    if not state_norm:
        errors.append("missing_state")
    elif not re.fullmatch(r"[A-Z]{2}", state_norm) or state_norm not in _US_STATES:
        errors.append("unsupported_state")
    if errors:
        errors.insert(0, "unsupported_jurisdiction")
    return list(dict.fromkeys(errors))


def _structured_input_rejection(city: str, state: str, validation_errors: list[str]) -> dict[str, Any]:
    city = _norm(city)
    state = _norm(state).upper()
    return {
        "input_status": "rejected",
        "error_code": "unsupported_jurisdiction",
        "validation_errors": validation_errors or ["unsupported_jurisdiction"],
        "message": (
            "PermitAssist supports customer permit decisions for U.S. jurisdictions with a valid "
            "two-letter state code. Enter a valid city and U.S. state code before requesting a permit decision."
        ),
        "customer_headline": "Unsupported jurisdiction input.",
        "customer_next_step": "Enter a valid city and two-letter U.S. state code, then run the lookup again.",
        "city": city,
        "state": state,
    }


def is_input_rejection(value: Any) -> bool:
    return isinstance(value, dict) and value.get("input_status") == "rejected" and value.get("error_code") == "unsupported_jurisdiction"


def _blob(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{k} {_blob(v)}" for k, v in value.items() if not str(k).startswith("_"))
    if isinstance(value, list):
        return " ".join(_blob(v) for v in value)
    return str(value or "")


def _keyword_present(text: str, keyword: str) -> bool:
    return _keyword_position(text, keyword) is not None


def _keyword_negated(hay: str, start: int) -> bool:
    """True when a scope keyword appears inside a local negation clause.

    This keeps phrases such as "no tenant improvement", "no solar or PV",
    and "without electrical work" from creating customer-visible permit kinds.
    """
    prior = hay[max(0, start - 80):start]
    return bool(re.search(
        r"(?:^|[.;,]|\b)(?:no|without|not|excluding|except|minus)\b[\w\s,/&-]{0,60}$",
        prior,
        flags=re.I,
    ))


def _keyword_position(text: str, keyword: str) -> int | None:
    needle = keyword.strip().lower()
    if not needle:
        return None
    # Pad text so rules such as " pv " can intentionally require standalone tokens.
    hay = f" {text.lower()} "
    pattern = r"(?<![a-z0-9])" + re.escape(needle.strip()).replace(r"\ ", r"[\s/-]+") + r"(?![a-z0-9])"
    for match in re.finditer(pattern, hay, flags=re.I):
        if not _keyword_negated(hay, match.start()):
            return match.start()
    return None


def _existing_required_permit_names(result: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for permit in result.get("permits_required") or []:
        if isinstance(permit, dict):
            text = _norm(permit.get("permit_type") or permit.get("portal_selection") or permit.get("kind") or permit.get("name"))
        else:
            text = _norm(permit)
        if text:
            names.append(text)
    return list(dict.fromkeys(names))


def _existing_required_permit_records(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for permit in result.get("permits_required") or []:
        if not isinstance(permit, dict):
            continue
        text = _norm(permit.get("permit_type") or permit.get("portal_selection") or permit.get("kind") or permit.get("name"))
        if text and text not in records:
            records[text] = dict(permit)
    return records


def _permit_payloads_for_names(permit_names: list[str], kinds: list[str], result: dict[str, Any]) -> list[dict[str, Any]]:
    existing_records = _existing_required_permit_records(result)
    primary_kind = kinds[0] if kinds else "Building"
    rows: list[dict[str, Any]] = []
    for idx, name in enumerate(permit_names):
        kind = kinds[idx] if idx < len(kinds) else primary_kind
        row = {"permit_type": name, "kind": kind, "required": True}
        existing = existing_records.get(name)
        if isinstance(existing, dict):
            for field in (
                "portal_selection",
                "notes",
                "required_if",
                "source_url",
                "source_title",
                "filing_family",
                "approval_type",
                "row_category",
                "decision",
                "trigger_signal_ids",
                "ahj_name",
                "ahj_type",
                "application_authority_name",
                "apply_url",
                "department_url",
                "apply_url_status",
                "source_status",
                "provenance",
            ):
                value = existing.get(field)
                if value not in (None, "", [], {}):
                    row[field] = value
        rows.append(row)
    return rows


def _display_permit_kind(primary_kind: str, kinds: list[str], result: dict[str, Any]) -> str:
    supplied = _norm(result.get("permit_kind"))
    if supplied and not contains_legacy_unknown_state(supplied):
        supplied_kinds = set(_kinds_from_text(supplied))
        current_kinds = set(kinds or [primary_kind])
        supplied_lc = supplied.lower()
        if supplied_kinds & current_kinds:
            return supplied
        if "mep" in supplied_lc and current_kinds & {"Electrical", "Mechanical", "Plumbing"}:
            return supplied
        if ("zoning" in supplied_lc or "land use" in supplied_lc or "land-use" in supplied_lc) and "Zoning / Land Use" in current_kinds:
            return supplied
    return primary_kind


def _kinds_from_text(text: str, *, commercial_default: bool = False) -> list[str]:
    ranked: list[tuple[int, int, str]] = []
    for rule_idx, (kind, keywords) in enumerate(_REQUIRED_KIND_RULES):
        positions = [pos for keyword in keywords if (pos := _keyword_position(text, keyword)) is not None]
        if positions:
            ranked.append((min(positions), rule_idx, kind))
    kinds = [kind for _, _, kind in sorted(ranked)]
    if commercial_default and "Commercial Building / Tenant Improvement" not in kinds:
        kinds.insert(0, "Commercial Building / Tenant Improvement")
    if not kinds and _STRUCTURAL_OR_TRADE_RE.search(text):
        kinds.append("Building")
    return list(dict.fromkeys(kinds))


def _permit_names_for_kinds(kinds: list[str], existing_names: list[str]) -> list[str]:
    names: list[str] = []
    used_existing: set[str] = set()
    current_kinds = set(kinds)
    generic_names = {"permit", "required permit", "generic building permit", "permit required"}

    def aligned_existing(name: str, kind: str = "") -> bool:
        name_lc = name.lower()
        if not name or name_lc in generic_names:
            return False
        name_kinds = set(_kinds_from_text(name))
        if kind and kind not in name_kinds and kind.lower() not in name_lc:
            return False
        # Avoid stealing a stale cross-scope row such as Solar PV for an
        # electrical-panel lookup just because it also contains "Electrical".
        extra_specific = name_kinds - current_kinds - {"Building"}
        return not extra_specific

    for kind in kinds:
        matching_existing = ""
        for existing in existing_names:
            if existing in used_existing:
                continue
            if aligned_existing(existing, kind):
                matching_existing = existing
                used_existing.add(existing)
                break
        names.append(matching_existing or _KIND_TO_PERMIT_NAME.get(kind, "Building Permit"))

    # Preserve additional upstream rows only when their inferred permit kind is
    # inside the request-scope kind set. This keeps multi-permit answers such as
    # stage fog + lighting controls while excluding stale unrelated cache rows.
    for existing in existing_names:
        if existing in used_existing:
            continue
        if aligned_existing(existing):
            names.append(existing)
            used_existing.add(existing)
    return list(dict.fromkeys(name for name in names if name))


def _has_official_source(result: dict[str, Any]) -> bool:
    text = _blob({k: result.get(k) for k in ("sources", "source_urls", "claim_citations", "apply_url", "apply_path", "online_application_url")})
    lowered = text.lower()
    return any(token in lowered for token in (".gov", ".us", "city", "county", "department", "permit")) and "http" in lowered


def _source_backed_required_kinds(result: dict[str, Any]) -> list[str]:
    """Preserve official source-backed required trade rows even when the job text's primary kind is narrower."""
    kinds: list[str] = []
    for permit in result.get("permits_required") or []:
        if not isinstance(permit, dict) or permit.get("required") is False:
            continue
        source_blob = _blob({
            "source_url": permit.get("source_url"),
            "source_type": permit.get("source_type"),
            "source": permit.get("source"),
            "provenance": permit.get("provenance"),
            "citations": permit.get("citations"),
            "evidence": permit.get("evidence"),
        }).lower()
        if not ("http" in source_blob or "official" in source_blob or ".gov" in source_blob or ".us" in source_blob):
            continue
        text = _norm(permit.get("permit_type") or permit.get("portal_selection") or permit.get("kind") or permit.get("name"))
        for kind in _kinds_from_text(text):
            if kind not in kinds:
                kinds.append(kind)
    return kinds


def _source_failure_seen(result: dict[str, Any]) -> bool:
    text = _blob({k: result.get(k) for k in ("retrieval_diagnostics", "warnings", "quality_warnings", "source_support", "sources", "source_urls")}).lower()
    return any(token in text for token in ("429", "502", "timeout", "timed out", "rate limit", "bad gateway", "temporarily unavailable", "retrieval failed"))


def _not_required_reason(job_type: str, result: dict[str, Any]) -> str:
    existing = _norm(result.get("not_required_reason") or result.get("exemption_reason") or result.get("reason"))
    if existing:
        return existing
    return (
        "Permit not required because the described scope is cosmetic/non-structural and does not include "
        "electrical, plumbing, mechanical, structural, exterior, layout, or occupancy work."
    )


def resolve_customer_decision(ctx: dict[str, Any] | None) -> dict[str, Any]:
    """Return a CustomerDecisionDTO dict with REQUIRED or NOT_REQUIRED only.

    Expected ctx keys: result, job_type, city, state, scope_contract. For
    convenience, callers may pass the raw result dict directly.
    """
    ctx = ctx if isinstance(ctx, dict) else {}
    if "result" in ctx and isinstance(ctx.get("result"), dict):
        result = dict(ctx.get("result") or {})
        job_type = _norm(ctx.get("job_type") or result.get("job_type") or result.get("job_summary") or result.get("description"))
        city = _norm(ctx.get("city") or result.get("city"))
        state = _norm(ctx.get("state") or result.get("state")).upper()
        scope_contract = ctx.get("scope_contract") if isinstance(ctx.get("scope_contract"), dict) else (result.get("_scope_contract") if isinstance(result.get("_scope_contract"), dict) else {})
    else:
        result = dict(ctx)
        job_type = _norm(result.get("job_type") or result.get("job_summary") or result.get("description"))
        city = _norm(result.get("city"))
        state = _norm(result.get("state")).upper()
        scope_contract = result.get("_scope_contract") if isinstance(result.get("_scope_contract"), dict) else {}

    validation_errors = _jurisdiction_validation_errors(city, state)
    if validation_errors:
        return _structured_input_rejection(city, state, validation_errors)

    job_scope_text = " ".join([
        job_type,
        _norm(result.get("permit_kind")),
        _norm(result.get("permit_name")),
        _blob(result.get("permits_required") or []),
    ]).lower()
    scope_contract_for_kind_text = dict(scope_contract) if isinstance(scope_contract, dict) else {}
    # The contract's forbidden_scope_tags intentionally name disallowed verticals
    # (e.g. commercial_ti for a residential pool). Those guardrail tokens are not
    # candidate permit kinds and must not seed the resolver back into a forbidden
    # segment.
    scope_contract_for_kind_text.pop("forbidden_scope_tags", None)
    scope_contract_text = _blob(scope_contract_for_kind_text).lower()
    scope_text = " ".join([job_scope_text, scope_contract_text]).lower()
    category = _norm(scope_contract.get("category") if isinstance(scope_contract, dict) else "").lower()
    family = _norm(scope_contract.get("family") if isinstance(scope_contract, dict) else "").lower()
    commercial_default = category == "commercial" or family.startswith("commercial")

    supplied_decision = _norm(result.get("permit_decision")).upper()
    supplied_required = result.get("permit_required")
    supplied_verdict = _norm(result.get("permit_verdict")).upper()

    job_text = job_type.lower()
    explicit_no_trade_or_structural = bool(_EXPLICIT_NO_TRADE_OR_STRUCTURAL_RE.search(job_text))
    has_affirmative_trade_or_structural = _has_affirmative_structural_or_trade(job_text)
    trivial_not_required = (
        bool(_TRIVIAL_NOT_REQUIRED_RE.search(job_text or scope_text))
        and (explicit_no_trade_or_structural or not has_affirmative_trade_or_structural)
        and not has_affirmative_trade_or_structural
    )
    source_backed_exemption_evidence = bool(result.get("positive_exemption_evidence") or result.get("exemption_evidence")) and _has_official_source(result)
    not_required_allowed_for_segment = (not commercial_default) or source_backed_exemption_evidence
    source_adjudicated_not_required_reason = _source_adjudicated_not_required_reason(city, state, job_text)
    explicit_not_required = supplied_decision == PERMIT_DECISION_NOT_REQUIRED or supplied_required is False or supplied_verdict in {"NO", "NOT_REQUIRED"}
    explicit_nonbinary = supplied_decision if supplied_decision in {
        PERMIT_DECISION_CONDITIONAL,
        PERMIT_DECISION_VERIFY,
        PERMIT_DECISION_NEEDS_INPUT,
    } else ""

    # Preserve an explicit non-binary answer when no authenticated Decision Cell
    # has replaced it upstream. Generic scope classification may enrich the
    # permit family and next action, but it must never invent REQUIRED from
    # CONDITIONAL/VERIFY input.
    if explicit_nonbinary:
        decision = explicit_nonbinary
        existing_names = _existing_required_permit_names(result)
        kinds = _kinds_from_text(job_type, commercial_default=commercial_default)
        if not kinds:
            kinds = _kinds_from_text(" ".join([job_scope_text, " ".join(existing_names)]), commercial_default=commercial_default)
        if not kinds:
            kinds = ["Commercial Building / Tenant Improvement" if commercial_default else "Building"]
        permit_names = _permit_names_for_kinds(kinds, existing_names)
        reason = ""
        department = _norm(result.get("applying_office") or result.get("building_dept_name")) or f"{city} {state} Building Department".strip() or "the local building department"
        if decision == PERMIT_DECISION_CONDITIONAL:
            headline = f"Permit requirement depends on a verified threshold: {permit_names[0]}."
            next_step = _norm(result.get("customer_next_step")) or f"Confirm the source-backed trigger or threshold with {department}; file under {permit_names[0]} if it applies."
        elif decision == PERMIT_DECISION_NEEDS_INPUT:
            headline = "Permit requirement needs project details."
            next_step = _norm(result.get("customer_next_step")) or f"Provide the missing scope details, then verify the listed filing lanes with {department} before work starts."
        else:
            headline = "Permit requirement needs verification."
            next_step = f"Verify the permit requirement and filing category with {department} before work starts."
        confidence_tier = "AHJ_DIRECT" if _has_official_source(result) else "SCOPE_DEFAULT"
    # Only honor NOT_REQUIRED when the current scope is genuinely trivial/cosmetic
    # or an upstream source/rule explicitly classified it as NOT_REQUIRED. Legacy
    # UNKNOWN/FAIL_CLOSED/null never survive this boundary. Commercial scopes need
    # source-backed exemption evidence; cosmetic defaults alone are residential-only.
    elif (source_adjudicated_not_required_reason and not_required_allowed_for_segment) or (trivial_not_required and not_required_allowed_for_segment) or (explicit_not_required and not _has_affirmative_structural_or_trade(scope_text) and not_required_allowed_for_segment):
        decision = PERMIT_DECISION_NOT_REQUIRED
        kinds: list[str] = []
        permit_names: list[str] = []
        reason = source_adjudicated_not_required_reason or _not_required_reason(job_type, result)
        headline = "Permit not required for the described scope."
        next_step = "Keep this no-permit rationale with the job file before starting work."
        confidence_tier = "AHJ_DIRECT" if _has_official_source(result) else "SCOPE_DEFAULT"
    else:
        decision = PERMIT_DECISION_REQUIRED
        existing_names = _existing_required_permit_names(result)
        # P1A — Change-of-use anchor guard (2026-06-09).
        # Force Building/TI anchor for commercial use-type changes, but do not
        # misclassify residential ADU/garage conversions as commercial TI. Preserve
        # explicit trade rows already identified upstream under the TI anchor.
        is_residential_adu = family == "residential_adu" or "adu" in scope_text or "accessory dwelling" in scope_text
        if commercial_default and _explicit_change_of_use(job_text) and not is_residential_adu:
            inferred_kinds = _kinds_from_text(job_type, commercial_default=True)
            extra_trade_kinds = [
                kind for kind in _kinds_from_text(" ".join(existing_names), commercial_default=False)
                if kind not in {"Commercial Building / Tenant Improvement", "Building"}
            ]
            kinds = list(dict.fromkeys(["Commercial Building / Tenant Improvement", *inferred_kinds, *extra_trade_kinds]))
            permit_names = _permit_names_for_kinds(kinds, existing_names) or ["Commercial Building / Tenant Improvement Permit"]
            reason = "Permit required because the scope involves a change of use or occupancy classification."
            headline = "Permit required: Commercial Building / Tenant Improvement."
            department = _norm(result.get("applying_office") or result.get("building_dept_name")) or f"{city} {state} Building Department".strip() or "the local building department"
            next_step = f"File under Commercial Building / Tenant Improvement with {department}; confirm the exact occupancy classification and required plan sets before starting work."
            confidence_tier = "AHJ_DIRECT" if _has_official_source(result) else "SCOPE_DEFAULT"
            degraded_sources = bool(result.get("degraded_sources") or _source_failure_seen(result))
            source_support = result.get("source_support") if isinstance(result.get("source_support"), dict) else {}
            if not source_support:
                source_support = {
                    "confidence_tier": confidence_tier,
                    "has_official_source": _has_official_source(result),
                    "source_count": len(result.get("sources") or []),
                    "has_live_source": bool(result.get("sources") or result.get("source_urls")),
                    "degraded_sources": degraded_sources,
                }
            else:
                source_support.setdefault("confidence_tier", confidence_tier)
            return {
                "permit_decision": decision,
                "permit_required": True,
                "permit_verdict": "YES",
                "permit_kind": kinds[0],
                "permit_kinds": kinds,
                "permit_name": permit_names[0],
                "permits_required": _permit_payloads_for_names(permit_names, kinds, result),
                "not_required_reason": "",
                "trade_permits": [],
                "companion_permits_or_reviews": [],
                "customer_next_step": next_step,
                "customer_headline": headline,
                "confidence_tier": confidence_tier,
                "source_support": source_support,
                "degraded_sources": degraded_sources,
            }
        kinds = _kinds_from_text(job_type, commercial_default=commercial_default)
        if not kinds:
            kinds = _kinds_from_text(" ".join([job_scope_text, " ".join(existing_names)]), commercial_default=commercial_default)
        if not kinds:
            kinds = _kinds_from_text(" ".join([scope_contract_text, " ".join(existing_names)]), commercial_default=commercial_default)
        if not kinds:
            kinds = ["Building"]
        for source_kind in _source_backed_required_kinds(result):
            if source_kind not in kinds:
                kinds.append(source_kind)
        permit_names = _permit_names_for_kinds(kinds, existing_names)
        reason = "Permit required based on the work scope classification."
        headline = f"Permit required: {', '.join(permit_names[:3])}."
        department = _norm(result.get("applying_office") or result.get("building_dept_name")) or f"{city} {state} Building Department".strip() or "the local building department"
        next_step = f"File under {permit_names[0]} with {department}; use the local permit portal or counter intake for that permit category before starting work."
        if _has_official_source(result):
            confidence_tier = "AHJ_DIRECT"
        elif state in _US_STATES:
            confidence_tier = "SCOPE_DEFAULT"
        else:
            confidence_tier = "CLASS_MINIMUM"

    degraded_sources = bool(result.get("degraded_sources") or _source_failure_seen(result))
    source_support = result.get("source_support") if isinstance(result.get("source_support"), dict) else {}
    if not source_support:
        source_support = {
            "confidence_tier": confidence_tier,
            "has_official_source": _has_official_source(result),
            "degraded_sources": degraded_sources,
            "exact_form_known": bool(_norm(result.get("permit_name")) or _existing_required_permit_names(result)),
            "apply_url_known": bool(_norm(result.get("apply_url") or result.get("online_application_url"))),
        }

    primary_kind = kinds[0] if kinds else "Building"
    primary_name = permit_names[0] if permit_names else ""
    display_kind = _display_permit_kind(primary_kind, kinds, result) if decision in {PERMIT_DECISION_REQUIRED, PERMIT_DECISION_CONDITIONAL} else "Other"
    permits_required = _permit_payloads_for_names(permit_names, kinds, result)
    if decision in {PERMIT_DECISION_CONDITIONAL, PERMIT_DECISION_VERIFY, PERMIT_DECISION_NEEDS_INPUT}:
        for row in permits_required:
            row["required"] = None
            row["status"] = decision
            row["decision"] = decision
            row["required_status"] = decision

    permit_required_value = (
        True if decision == PERMIT_DECISION_REQUIRED
        else False if decision == PERMIT_DECISION_NOT_REQUIRED
        else None
    )
    permit_name_value = (
        primary_name if decision in {PERMIT_DECISION_REQUIRED, PERMIT_DECISION_CONDITIONAL}
        else "No permit required" if decision == PERMIT_DECISION_NOT_REQUIRED
        else "Permit requirement verification"
    )

    return {
        "permit_required": permit_required_value,
        "permit_decision": decision,
        "permit_verdict": (
            "YES" if decision == PERMIT_DECISION_REQUIRED
            else "NO" if decision == PERMIT_DECISION_NOT_REQUIRED
            else decision
        ),
        "permit_kind": display_kind,
        "permit_kinds": kinds,
        "permit_name": permit_name_value,
        "permits_required": permits_required if decision != PERMIT_DECISION_NOT_REQUIRED else [],
        "not_required_reason": reason if decision == PERMIT_DECISION_NOT_REQUIRED else "",
        "customer_headline": headline,
        "customer_next_step": next_step,
        "confidence_tier": confidence_tier,
        "source_support": source_support,
        "apply_path": result.get("apply_path"),
        "degraded_sources": degraded_sources,
    }


def contains_legacy_unknown_state(value: Any, *, _root: bool = True) -> bool:
    """Return True for cached/public data that must be re-resolved or ignored."""
    if isinstance(value, dict):
        if _root and not any(not str(key).startswith("_") for key in value):
            return True
        decision = _norm(value.get("permit_decision")).upper()
        verdict = _norm(value.get("permit_verdict")).upper()
        nonbinary_states = {
            _norm(value.get(field)).upper()
            for field in ("permit_decision", "permit_verdict", "decision", "status", "required_status")
        }
        valid_nonbinary = bool(nonbinary_states & {PERMIT_DECISION_CONDITIONAL, PERMIT_DECISION_VERIFY})
        if (
            ("permit_decision" in value and decision in _BAD_DECISION_VALUES)
            or verdict == "UNKNOWN"
            or (
                "permit_required" in value
                and value.get("permit_required") is None
                and not valid_nonbinary
            )
        ):
            return True
        return any(
            contains_legacy_unknown_state(v, _root=False)
            for k, v in value.items()
            if not str(k).startswith("_")
        )
    if isinstance(value, list):
        return any(contains_legacy_unknown_state(item, _root=False) for item in value)
    if isinstance(value, str):
        text = value.upper()
        return "FAIL_CLOSED_UNSUPPORTED_OR_NO_EVIDENCE" in text or text.strip() == "UNKNOWN"
    return False
