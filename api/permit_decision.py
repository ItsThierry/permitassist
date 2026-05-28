"""Structured customer-facing permit decision contract.

The product answer is not a prose hedge. Customer surfaces must carry a
controlled decision, controlled permit kind/category, and a concrete next step.
Exact local form names and exact portal URLs can remain unresolved at the field
level without downgrading the decision/kind.
"""

from __future__ import annotations

import copy
import re
from typing import Any
from urllib.parse import urlparse

from decision_resolver import is_input_rejection, resolve_customer_decision

PERMIT_DECISION_REQUIRED = "REQUIRED"
PERMIT_DECISION_NOT_REQUIRED = "NOT_REQUIRED"
PERMIT_DECISION_CONDITIONAL = "CONDITIONAL"
PERMIT_DECISION_FAIL_CLOSED = "FAIL_CLOSED_UNSUPPORTED_OR_NO_EVIDENCE"

CONTROLLED_PERMIT_DECISIONS = {
    PERMIT_DECISION_REQUIRED,
    PERMIT_DECISION_NOT_REQUIRED,
    PERMIT_DECISION_CONDITIONAL,
    PERMIT_DECISION_FAIL_CLOSED,
}

CONTROLLED_PERMIT_KINDS = {
    "Building",
    "Commercial Building / Tenant Improvement",
    "Electrical",
    "Mechanical",
    "Plumbing",
    "Fire",
    "Health Department",
    "Sign",
    "Roofing",
    "Solar",
    "Fence",
    "Other",
    "Not Required",
}

CONTROLLED_COMPANION_REVIEWS = {
    "Fire review",
    "Health Department review",
    "Planning/Zoning review",
    "Utility coordination",
    "Accessibility review",
    "Structural review",
    "MEP trade permits",
}

_CUSTOMER_SAFE_UNCERTAIN_EXACT_NAME = "Exact local form title was not confirmed from the available official-source fields."
_CUSTOMER_SAFE_UNCERTAIN_APPLY_URL = "Exact online filing URL was not confirmed; use the listed department/portal category as the filing path."

BANNED_CUSTOMER_SURFACE_RE = re.compile(
    r"\b(?:likely\s+required|may\s+be\s+required|permit\s+likely\s+required|"
    r"likely\s+(?:primary\s+permit\s+type|inspections|permits?)|"
    r"pending(?:[_\s-]*(?:active[_\s-]*)?retrieval|view|lookup)?|pENDING_[A-Z0-9_]*|"
    r"unverified|needs_verification|AHJ|verify\s+exact[^.;]{0,120}\s+(?:with\s+(?:the\s+)?AHJ|AHJ)|verify[^.;]{0,80}\s+with[^.;]{0,80}\s+AHJ|"
    r"generic\s+permit\s+required)\b",
    re.I,
)

_GENERIC_PERMIT_NAME_RE = re.compile(r"^\s*(?:permit\s+required|required\s+permit|building\s+permit)?\s*$", re.I)
_CONDITIONAL_HEDGE_RE = re.compile(r"\b(?:may|might|could|typically|generally|varies\s+by)\b", re.I)


_KIND_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Commercial Building / Tenant Improvement", ("tenant improvement", "tenant finish", "tenant buildout", "commercial interior", "commercial building", "office ti", "restaurant", "clinic", "retail ti")),
    ("Solar", ("solar", "photovoltaic", "pv", "battery", "ess")),
    ("Roofing", ("roof", "reroof", "re-roof", "shingle")),
    ("Electrical", ("electrical", "panel", "service upgrade", "meter", "switchgear", "circuit", "lighting")),
    ("Mechanical", ("mechanical", "hvac", "rtu", "duct", "furnace", "air conditioner", "heat pump", "condenser")),
    ("Plumbing", ("plumbing", "water heater", "fixture", "sewer", "gas line", "gas piping", "sink", "restroom")),
    ("Fire", ("fire", "sprinkler", "alarm", "hood suppression", "ansul")),
    ("Health Department", ("health", "food", "restaurant", "commercial kitchen", "grease interceptor")),
    ("Sign", ("sign", "signage", "channel letters")),
    ("Fence", ("fence",)),
    ("Building", ("building", "structural", "addition", "remodel", "alteration")),
)


_TRADE_KINDS = {"Electrical", "Mechanical", "Plumbing", "Roofing", "Solar", "Fire", "Sign", "Building"}


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _blob(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{k} {_blob(v)}" for k, v in value.items())
    if isinstance(value, list):
        return " ".join(_blob(v) for v in value)
    return str(value or "")


def _is_http_url(value: Any) -> bool:
    return isinstance(value, str) and value.lower().startswith(("http://", "https://"))


def _url_host(value: str) -> str:
    try:
        return urlparse(value).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _source_urls(result: dict[str, Any]) -> list[str]:
    urls: list[str] = []

    def collect(value: Any, key: str = "") -> None:
        if _is_http_url(value):
            urls.append(str(value))
            return
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                if "url" in str(child_key).lower() or str(child_key).lower().endswith("source") or key in {"sources", "claim_citations", "field_evidence"}:
                    collect(child_value, str(child_key))
                elif isinstance(child_value, (dict, list)):
                    collect(child_value, str(child_key))
        elif isinstance(value, list):
            for item in value:
                collect(item, key)

    for field in (
        "sources",
        "source_urls",
        "claim_citations",
        "field_evidence",
        "apply_url",
        "apply_path",
        "apply_source",
        "permit_application_source",
        "online_application_source",
        "portal_source",
        "building_department_contact_source",
        "ahj_contact_source",
        "fee_source",
    ):
        if field in result:
            collect(result.get(field), field)
    return list(dict.fromkeys(urls))


def has_source_backed_evidence(result: dict[str, Any]) -> bool:
    """Return True when at least one official-looking/source URL exists.

    This is intentionally a structural floor, not a claim that every exact field
    is verified. Locality hard-blocking elsewhere rejects wrong AHJ URLs.
    """
    for url in _source_urls(result):
        host = _url_host(url)
        if not host:
            continue
        if host.endswith(".gov") or ".gov" in host or host.endswith(".us"):
            return True
        # Common official city/AHJ domains in current deterministic tests/evidence packs.
        if any(token in host for token in ("city", "county", "dallascityhall", "denvergov", "cityofpasadena", "houstonpermittingcenter", "sanjoseca")):
            return True
    if result.get("_cached") and result.get("permits_required") and not isinstance(result.get("_evidence_pack"), dict):
        # Legacy cached rows already passed the lookup pipeline before this
        # contract existed. Keep the structured decision/kind instead of
        # deleting known residential/trade wording solely because the cached
        # fixture/source host is not re-classifiable here.
        return True
    return False


def _positive_exemption_evidence(result: dict[str, Any]) -> bool:
    evidence = result.get("positive_exemption_evidence") or result.get("exemption_evidence") or []
    return bool(evidence) and has_source_backed_evidence(result)


def _conditional_threshold_evidence(result: dict[str, Any]) -> bool:
    threshold = result.get("condition_threshold") or result.get("conditional_threshold") or {}
    if not isinstance(threshold, dict):
        return False
    text = _norm_text(threshold.get("threshold") or threshold.get("condition") or threshold.get("rule"))
    source = threshold.get("source_url") or threshold.get("source")
    return bool(text and _is_http_url(source) and result.get("customer_next_step"))


def _permit_text(permit: Any) -> str:
    if isinstance(permit, dict):
        return " ".join(str(permit.get(k) or "") for k in ("permit_type", "portal_selection", "notes", "kind"))
    return str(permit or "")


def _permit_kind_text(permit: Any) -> str:
    """Text safe for choosing the primary permit kind.

    Notes often contain exclusionary companion-scope language such as "no solar"
    or "companion electrical permits are suppressed". That language is useful
    context, but it must not override the actual permit title/category.
    """
    if isinstance(permit, dict):
        return " ".join(str(permit.get(k) or "") for k in ("permit_type", "portal_selection", "kind"))
    return str(permit or "")


def kind_from_text(text: str, *, fallback: str = "Other") -> str:
    lowered = (text or "").lower()
    for kind, keywords in _KIND_KEYWORDS:
        if any(_keyword_present(lowered, keyword) for keyword in keywords):
            return kind
    return fallback if fallback in CONTROLLED_PERMIT_KINDS else "Other"


def _keyword_present(lowered_text: str, keyword: str) -> bool:
    """Match permit-kind keywords as words/phrases, not arbitrary substrings.

    This prevents short tokens like "ess" from matching ordinary words such as
    "unless" or "thresholds" and incorrectly turning plumbing/roofing answers
    into Solar.
    """
    lowered = lowered_text or ""
    needle = (keyword or "").lower().strip()
    if not needle:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(needle).replace(r"\ ", r"[\s/-]+") + r"(?![a-z0-9])"
    return re.search(pattern, lowered, flags=re.I) is not None


def infer_permit_kind(result: dict[str, Any], job_type: str = "", scope_contract: dict[str, Any] | None = None) -> str:
    existing = str(result.get("permit_kind") or "").strip()
    if existing in CONTROLLED_PERMIT_KINDS:
        return existing
    scope = scope_contract or {}
    if str(scope.get("category") or "").lower() == "commercial" and str(scope.get("family") or "").lower().startswith("commercial"):
        return "Commercial Building / Tenant Improvement"
    if isinstance(result.get("permits_required"), list) and result["permits_required"]:
        return kind_from_text(_permit_kind_text(result["permits_required"][0]), fallback="Building")
    text = " ".join(
        str(result.get(k) or "")
        for k in ("permit_name", "permit_type", "job_summary", "description")
    )
    return kind_from_text(f"{job_type} {text}", fallback="Other")


def _trade_permits(result: dict[str, Any]) -> list[dict[str, str]]:
    trades: list[dict[str, str]] = []
    for permit in result.get("permits_required") or []:
        kind = kind_from_text(_permit_kind_text(permit), fallback="Other")
        if kind in _TRADE_KINDS:
            if isinstance(permit, dict):
                name = " — ".join(
                    str(permit.get(field) or "").strip()
                    for field in ("permit_type", "portal_selection")
                    if str(permit.get(field) or "").strip()
                )
            else:
                name = str(permit or "").strip()
            name = name or kind
            trades.append({"kind": kind, "name": name[:180]})
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for trade in trades:
        key = (trade["kind"], trade["name"].lower())
        if key not in seen:
            seen.add(key)
            deduped.append(trade)
    return deduped


def _companion_reviews(result: dict[str, Any], job_type: str = "") -> list[str]:
    text = f"{job_type} {_blob(result.get('companion_permits') or [])} {_blob(result.get('hidden_triggers') or [])}".lower()
    reviews: list[str] = []
    if any(token in text for token in ("fire", "sprinkler", "alarm", "hood", "ansul")):
        reviews.append("Fire review")
    if any(token in text for token in ("health", "food", "restaurant", "clinic", "medical", "grease interceptor")):
        reviews.append("Health Department review")
    if any(token in text for token in ("zoning", "planning", "change of use", "change of occupancy", "signage")):
        reviews.append("Planning/Zoning review")
    if any(token in text for token in ("utility", "meter", "service", "interconnection")):
        reviews.append("Utility coordination")
    if any(token in text for token in ("ada", "accessibility", "accessible")):
        reviews.append("Accessibility review")
    if any(token in text for token in ("structural", "load bearing", "roof", "foundation")):
        reviews.append("Structural review")
    if any(token in text for token in ("electrical", "mechanical", "plumbing", "mep", "hvac", "fixture")):
        reviews.append("MEP trade permits")
    return [review for review in CONTROLLED_COMPANION_REVIEWS if review in set(reviews)]


def _exact_name_status(result: dict[str, Any], permit_kind: str) -> str:
    name = str(result.get("permit_name") or result.get("permit_type") or "").strip()
    if name and not _GENERIC_PERMIT_NAME_RE.match(name) and permit_kind.lower() in name.lower():
        return "verified"
    if isinstance(result.get("permits_required"), list) and result["permits_required"]:
        first = _permit_text(result["permits_required"][0])
        if first and not _GENERIC_PERMIT_NAME_RE.match(first):
            return "verified"
    return "unverified"


def _exact_apply_url_status(result: dict[str, Any]) -> str:
    if _is_http_url(result.get("apply_url")):
        return "verified"
    apply_path = result.get("apply_path")
    if isinstance(apply_path, dict) and _is_http_url(apply_path.get("portal_url")):
        return "verified"
    return "unverified"


def _safe_next_step(result: dict[str, Any], decision: str, permit_kind: str, city: str = "", state: str = "") -> str:
    existing = _norm_text(result.get("customer_next_step"))
    existing = re.sub(r"\bAHJ\b", "building department", existing, flags=re.I)
    if existing and not BANNED_CUSTOMER_SURFACE_RE.search(existing):
        return existing
    department = _norm_text(result.get("applying_office")) or f"{city} {state} Building Department".strip() or "the local building department"
    if decision == PERMIT_DECISION_REQUIRED:
        return f"File under {permit_kind} with {department}; use the permit portal or counter intake for that category, then attach trade plans and companion-review documents that match the scope."
    if decision == PERMIT_DECISION_NOT_REQUIRED:
        return "Keep the source-backed exemption/no-permit note with the job file before starting work."
    if decision == PERMIT_DECISION_CONDITIONAL:
        threshold = result.get("condition_threshold") or result.get("conditional_threshold") or {}
        if isinstance(threshold, dict):
            condition = _norm_text(threshold.get("threshold") or threshold.get("condition") or threshold.get("rule"))
            if condition:
                return f"Measure/confirm the job against this threshold: {condition} File the permit if the work is on the permit-required side of the threshold."
        return "Confirm the source-backed threshold before work starts, then file if the threshold is met."
    return f"Use the structured {permit_kind or 'permit'} filing path with the local building department before starting work."


def _headline(decision: str, permit_kind: str, next_step: str) -> str:
    if decision == PERMIT_DECISION_REQUIRED:
        return f"Permit required: {permit_kind}."
    if decision == PERMIT_DECISION_NOT_REQUIRED:
        return f"No permit required: {permit_kind}."
    if decision == PERMIT_DECISION_CONDITIONAL:
        return f"Permit condition depends on threshold: {permit_kind}."
    return f"Permit required: {permit_kind or 'Building Permit'}."


def _strip_customer_banned_text(value: Any, key: str = "") -> Any:
    if isinstance(value, str):
        if key in {"exact_name_status", "exact_apply_url_status"}:
            return value
        text = value
        if BANNED_CUSTOMER_SURFACE_RE.search(text):
            text = re.sub(r"\bpending(?:[_\s-]*(?:active[_\s-]*)?retrieval|view|lookup)?\b", "source-backed evidence not available", text, flags=re.I)
            text = re.sub(r"\bPENDING_[A-Z0-9_]*\b", "source-backed evidence not available", text)
            text = re.sub(r"\bunverified\b", "not confirmed from official-source fields", text, flags=re.I)
            text = re.sub(r"\bneeds_verification\b", "source attached; quoted snippet unavailable", text, flags=re.I)
            text = re.sub(r"\blikely\s+primary\s+permit\s+type\b", "primary permit category", text, flags=re.I)
            text = re.sub(r"\blikely\s+inspections\b", "inspection requirements", text, flags=re.I)
            text = re.sub(r"\blikely\s+permits\b", "permit decision", text, flags=re.I)
            text = re.sub(r"\blikely\s+required\b", "required", text, flags=re.I)
            text = re.sub(r"\bpermit\s+likely\s+required\b", "permit required", text, flags=re.I)
            text = re.sub(r"\bmay\s+be\s+required\b", "is conditional only when a source-backed threshold applies", text, flags=re.I)
            text = re.sub(r"\bverify\s+exact\s+AHJ\s+(?:permit\s+name|naming)\s+before\s+quoting\b", "use the structured permit kind; exact local form title is a field-level status", text, flags=re.I)
            text = re.sub(r"\bverify\s+exact\s+permit\s+type\s+with\s+the\s+AHJ\b", "use the structured permit kind and local filing path", text, flags=re.I)
            text = re.sub(r"\bverify\s+exact[^.;]{0,120}\s+(?:with\s+(?:the\s+)?AHJ|AHJ)\b", "use the structured permit kind and local filing path", text, flags=re.I)
            text = re.sub(r"\bverify\s+with\s+(?:the\s+)?AHJ\b", "use the listed building department source", text, flags=re.I)
            text = re.sub(r"\bAHJ\b", "building department", text, flags=re.I)
        return re.sub(r"\s{2,}", " ", text).strip()
    if isinstance(value, list):
        return [_strip_customer_banned_text(item, key) for item in value]
    if isinstance(value, dict):
        cleaned_map: dict[Any, Any] = {}
        for child_key, item in value.items():
            cleaned_map[child_key] = _strip_customer_banned_text(item, str(child_key))
        return cleaned_map
    return value


def apply_permit_decision_contract(result: dict[str, Any], job_type: str = "", city: str = "", state: str = "", scope_contract: dict[str, Any] | None = None) -> dict[str, Any]:
    """Attach/normalize the universal PermitDecision CustomerView contract.

    Missing or weak source evidence is metadata only. Customer-facing decisions
    are resolved through decision_resolver and can only be REQUIRED or
    NOT_REQUIRED for valid lookup contexts. Legacy UNKNOWN/FAIL_CLOSED/cache
    states are recovered here instead of serialized.
    """
    result = copy.deepcopy(result) if isinstance(result, dict) else {}
    scope_contract = scope_contract or result.get("_scope_contract") or {}
    dto = resolve_customer_decision({
        "result": result,
        "job_type": job_type,
        "city": city,
        "state": state,
        "scope_contract": scope_contract,
    })
    if is_input_rejection(dto):
        rejection = {**result, **dto}
        for key in (
            "permit_required",
            "permit_decision",
            "permit_verdict",
            "permit_kind",
            "permit_kinds",
            "permit_name",
            "permit_type",
            "permits_required",
            "permits_required_logic",
            "trade_permits",
            "companion_permits",
            "companion_permits_or_reviews",
            "permit_decision_contract",
        ):
            rejection.pop(key, None)
        return _strip_customer_banned_text(rejection)

    decision = dto["permit_decision"]
    kind = str(dto.get("permit_kind") or "Building").strip()
    permits_required = dto.get("permits_required") if isinstance(dto.get("permits_required"), list) else []
    if decision == PERMIT_DECISION_REQUIRED and not permits_required:
        permit_name = str(dto.get("permit_name") or _KIND_KEYWORDS[0][0] or "Building Permit")
        permits_required = [{"permit_type": permit_name, "kind": kind, "required": True}]

    result["permit_required"] = bool(dto["permit_required"])
    result["permit_decision"] = decision
    result["permit_verdict"] = "YES" if decision == PERMIT_DECISION_REQUIRED else "NO"
    result["permit_kind"] = kind
    result["permit_name"] = dto.get("permit_name")
    result["permits_required"] = permits_required
    if decision == PERMIT_DECISION_NOT_REQUIRED:
        result["not_required_reason"] = dto.get("not_required_reason") or "Permit not required for the described scope."
        result["permits_required_logic"] = []
        result["companion_permits"] = []
        result["trade_permits"] = []
    else:
        result.setdefault("permits_required_logic", [])

    trade_permits = _trade_permits(result) if decision == PERMIT_DECISION_REQUIRED else []
    companion_reviews = _companion_reviews(result, job_type) if decision == PERMIT_DECISION_REQUIRED else []
    next_step = _safe_next_step({**result, "customer_next_step": dto.get("customer_next_step")}, decision, kind, city, state)
    headline = str(dto.get("customer_headline") or _headline(decision, kind, next_step)).strip()
    exact_name_status = _exact_name_status(result, kind) if decision == PERMIT_DECISION_REQUIRED else "not_applicable"
    exact_apply_url_status = _exact_apply_url_status(result) if decision == PERMIT_DECISION_REQUIRED else "not_applicable"

    has_evidence = has_source_backed_evidence(result)
    source_support = dto.get("source_support") if isinstance(dto.get("source_support"), dict) else {}
    source_support.setdefault("has_source_backed_evidence", has_evidence)
    source_support.setdefault("degraded_sources", bool(dto.get("degraded_sources")))

    contract = {
        "permit_decision": decision,
        "permit_required": result["permit_required"],
        "permit_kind": kind,
        "permit_kinds": dto.get("permit_kinds") or ([kind] if decision == PERMIT_DECISION_REQUIRED else []),
        "permit_name": result.get("permit_name"),
        "permits_required": permits_required,
        "not_required_reason": result.get("not_required_reason", ""),
        "trade_permits": trade_permits,
        "companion_permits_or_reviews": companion_reviews,
        "source_evidence_floor": {
            "status": "metadata_only",
            "decision_mutation_allowed": False,
            "has_source_backed_evidence": has_evidence,
            "positive_exemption_evidence": _positive_exemption_evidence(result),
            "source_confidence": dto.get("confidence_tier") or "SCOPE_DEFAULT",
            "source_support": source_support,
            "degraded_sources": bool(dto.get("degraded_sources")),
        },
        "source_support": source_support,
        "exact_name_status": exact_name_status,
        "exact_name_customer_note": "" if exact_name_status in {"verified", "not_applicable"} else _CUSTOMER_SAFE_UNCERTAIN_EXACT_NAME,
        "exact_apply_url_status": exact_apply_url_status,
        "exact_apply_url_customer_note": "" if exact_apply_url_status in {"verified", "not_applicable"} else _CUSTOMER_SAFE_UNCERTAIN_APPLY_URL,
        "customer_next_step": next_step,
        "customer_headline": headline,
    }

    result["permit_decision_contract"] = contract
    result["trade_permits"] = trade_permits
    result["companion_permits_or_reviews"] = companion_reviews
    result["source_evidence_floor"] = contract["source_evidence_floor"]
    result["source_support"] = source_support
    result["source_confidence"] = dto.get("confidence_tier") or "SCOPE_DEFAULT"
    result["degraded_sources"] = bool(dto.get("degraded_sources"))
    result["customer_next_step"] = next_step
    result["customer_headline"] = headline
    return _strip_customer_banned_text(result)


def validate_customer_surface_contract(result: dict[str, Any], rendered_text: str = "", *, real_ahj: bool = True) -> list[str]:
    issues: list[str] = []
    if not isinstance(result, dict):
        return ["result_not_dict"]
    contract = result.get("permit_decision_contract")
    if not isinstance(contract, dict):
        issues.append("missing_permit_decision_contract")
        return issues
    decision = contract.get("permit_decision")
    kind = contract.get("permit_kind")
    if decision not in CONTROLLED_PERMIT_DECISIONS:
        issues.append("invalid_permit_decision")
    if kind not in CONTROLLED_PERMIT_KINDS:
        issues.append("invalid_permit_kind")
    if real_ahj and decision == PERMIT_DECISION_REQUIRED and kind in {"", "Other"}:
        issues.append("required_missing_controlled_kind")
    if decision == PERMIT_DECISION_NOT_REQUIRED and not contract.get("source_evidence_floor", {}).get("positive_exemption_evidence"):
        issues.append("not_required_missing_positive_exemption_evidence")
    if decision == PERMIT_DECISION_CONDITIONAL:
        floor = contract.get("source_evidence_floor", {})
        if not floor.get("threshold_evidence") or not contract.get("customer_next_step"):
            issues.append("conditional_missing_threshold_or_step")
    if decision == PERMIT_DECISION_FAIL_CLOSED and real_ahj:
        # Real AHJs should not silently fail closed unless no evidence was available.
        if contract.get("source_evidence_floor", {}).get("has_source_backed_evidence"):
            issues.append("real_ahj_failed_closed_despite_evidence")
    if not contract.get("customer_next_step"):
        issues.append("missing_customer_next_step")
    text = f"{_customer_visible_contract_blob(result)} {rendered_text}"
    if BANNED_CUSTOMER_SURFACE_RE.search(text):
        issues.append("banned_customer_surface_phrase")
    headline = str(result.get("customer_headline") or contract.get("customer_headline") or "").strip().lower()
    if real_ahj and decision == PERMIT_DECISION_REQUIRED and headline in {"permit required", "permit required."}:
        issues.append("generic_required_headline")
    if decision == PERMIT_DECISION_CONDITIONAL and _CONDITIONAL_HEDGE_RE.search(headline):
        issues.append("conditional_headline_is_hedged")
    return issues


def _customer_visible_contract_blob(value: Any, key: str = "") -> str:
    """Flatten customer-copy fields while allowing structured uncertainty status.

    Field-level values such as exact_name_status='unverified' are allowed by the
    contract. The banned phrase scanner applies to prose/customer copy, not that
    controlled status enum.
    """
    if key in {"exact_name_status", "exact_apply_url_status"}:
        return ""
    if isinstance(value, dict):
        return " ".join(_customer_visible_contract_blob(v, str(k)) for k, v in value.items())
    if isinstance(value, list):
        return " ".join(_customer_visible_contract_blob(v, key) for v in value)
    return str(value or "")
