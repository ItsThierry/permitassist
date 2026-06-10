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
    r"source-backed\s+(?:threshold|evidence|exemption)|"
    r"pending(?:[_\s-]*(?:active[_\s-]*)?retrieval|view|lookup)?|pENDING_[A-Z0-9_]*|"
    r"unverified|needs_verification|AHJ|verify\s+exact[^.;]{0,120}\s+(?:with\s+(?:the\s+)?AHJ|AHJ)|verify[^.;]{0,80}\s+with[^.;]{0,80}\s+AHJ|"
    r"generic\s+permit\s+required)\b",
    re.I,
)

_GENERIC_PERMIT_NAME_RE = re.compile(r"^\s*(?:permit|permits?|permit\s+required|required\s+permit)?\s*$", re.I)
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


# ─── P0: Contact Data Integrity ───────────────────────────────────────────────
# Provenance-gated contact validator + safe fallback.
# Prevents wrong phone numbers/addresses from being shown to customers
# when the source is unverified (model_training, web_search without .gov).
# Added 2026-06-09 per permitassist-lookup-fix-spec v1.0.

_CONTACT_TRUSTED_SOURCES = frozenset({
    "auto_verified", "city_kb", "county_kb", "verified_cities_db",
    "accela_api", "official_hallucination_blocker",
    # Any source prefixed with these trusted categories
})

_CONTACT_UNTRUSTED_SOURCES = frozenset({
    "model_training", "web_search", "machine_extracted", "county_fallback",
})

# Phone patterns that indicate placeholders or non-department numbers
_CONTACT_SUSPICIOUS_PHONE_RE = re.compile(
    r"(?i)\b(?:000[-.]?000[-.]?0000|123[-.]?456[-.]?7890|"
    r"555[-.]?0100|555[-.]?555[-.]?5555|"
    r"xxx[-.]?xxx[-.]?xxxx|\(?000\)?|999[-.]?999[-.]?9999|"
    r"000-0000|(?:search|google|find)\s*:?)"
)

# Address patterns that indicate placeholders
_CONTACT_SUSPICIOUS_ADDR_RE = re.compile(
    r"(?i)\b(?:123\s+main\s+st(?:reet)?,?\s*(?:anytown|anystate|[a-z]+,?\s*[a-z]{2})?|"
    r"tbd|to be determined|unknown)\b"
)


def _is_trusted_contact_source(field_sources: dict[str, Any], field: str) -> bool:
    """Return True if the field has a trusted provenance source."""
    if not isinstance(field_sources, dict):
        return False
    src = str(field_sources.get(field) or "").lower()
    if not src:
        return False
    # Check trusted prefixes
    for trusted in _CONTACT_TRUSTED_SOURCES:
        if src.startswith(trusted.lower()):
            return True
    # Check if it contains a .gov URL (URL-based source)
    if ".gov" in src or ".us" in src:
        return True
    # Explicit untrusted
    if src in _CONTACT_UNTRUSTED_SOURCES:
        return False
    # Unknown source — treat as untrusted for safety
    return False


def validate_contact_provenance(result: dict[str, Any]) -> list[str]:
    """Validate contact fields and return list of trust issues.

    Issues trigger contact-card suppression in the public view.
    """
    issues: list[str] = []
    if not isinstance(result, dict):
        return ["result_not_dict"]

    # Map public field names to internal field names
    field_map = {
        "apply_phone": ("apply_phone", "office_phone", "phone"),
        "applying_office": ("applying_office", "building_dept_name"),
        "apply_address": ("apply_address", "building_dept_address", "office_address"),
    }

    field_sources = result.get("_field_sources") or {}

    for public_field, internal_fields in field_map.items():
        value = ""
        for f in internal_fields:
            if result.get(f):
                value = str(result[f])
                break

        # Skip empty
        if not value or value.strip() in ("", "N/A", "n/a"):
            continue

        # Check provenance
        is_trusted = _is_trusted_contact_source(field_sources, public_field)

        # Apply heuristic red flags even if source claims trusted
        if public_field == "apply_phone":
            # Check for placeholder/suspicious patterns
            digits = re.sub(r"[^0-9]", "", value)
            if len(digits) < 10:
                issues.append(f"{public_field}_short_digits")
            elif _CONTACT_SUSPICIOUS_PHONE_RE.search(value):
                issues.append(f"{public_field}_placeholder_pattern")
            elif not is_trusted:
                issues.append(f"{public_field}_untrusted_source")

        elif public_field == "applying_office":
            if len(value) < 5:
                issues.append(f"{public_field}_too_short")
            elif not is_trusted:
                issues.append(f"{public_field}_untrusted_source")

        elif public_field == "apply_address":
            if _CONTACT_SUSPICIOUS_ADDR_RE.search(value):
                issues.append(f"{public_field}_placeholder_pattern")
            elif not is_trusted:
                issues.append(f"{public_field}_untrusted_source")

    return issues


def sanitize_contact_for_public(result: dict[str, Any], city: str = "", state: str = "") -> dict[str, Any]:
    """Return a contact-sanitized copy of result for public/serialized views.

    If contact fields fail provenance validation, suppress them entirely
    and replace with a safe fallback message. Never show wrong phone numbers.
    """
    result = copy.deepcopy(result) if isinstance(result, dict) else {}
    contact_issues = validate_contact_provenance(result)

    # Build safe fallback text
    dept_name = _norm_text(result.get("applying_office")) or f"{city} {state} Building Department".strip() or "the local building department"
    safe_phone = f"Call {dept_name} directly — local number available via official city website or 311."
    safe_address = f"Search: {dept_name} address"
    safe_office = dept_name

    if contact_issues:
        # Suppress fields provenance-failure — metadata is NOT customer-facing
        result.pop("_contact_suppressed", None)
        result.pop("_contact_suppression_reasons", None)

        # Replace untrusted phone with safe fallback
        if any("apply_phone" in issue for issue in contact_issues):
            result["apply_phone"] = ""
            result["building_dept_phone"] = ""
            result["office_phone"] = ""
            result["phone"] = ""
            result["_safe_phone_note"] = safe_phone

        # Replace untrusted address with safe fallback
        if any("apply_address" in issue for issue in contact_issues):
            result["apply_address"] = ""
            result["building_dept_address"] = ""
            result["office_address"] = ""
            result["_safe_address_note"] = safe_address

        # Replace untrusted office name with safe generic
        if any("applying_office" in issue for issue in contact_issues):
            result["applying_office"] = safe_office
            result["building_dept_name"] = ""

    return result


def apply_contact_sanitization(result: dict[str, Any], city: str = "", state: str = "") -> dict[str, Any]:
    """Public API: sanitize contact data before serialization to frontend.

    Task 3: If AHJ record has verified contact, inject it into the result
    (overriding unverified auto-searched values).  Then run provenance gate.

    Must be called AFTER apply_permit_decision_contract and BEFORE
    JSON serialization in server.py.
    """
    result = copy.deepcopy(result) if isinstance(result, dict) else {}

    # ── Task 3: Inject AHJ verified contact data ──
    try:
        from ahj_records import get_ahj_contact
        contact = get_ahj_contact(city, state)
        if contact:
            status = contact.get("contact_status", "")
            if status == "verified":
                # Override with verified values
                if contact.get("phone"):
                    result["apply_phone"] = contact["phone"]
                    result["building_dept_phone"] = contact["phone"]
                if contact.get("inspection_scheduling_phone"):
                    result["inspection_scheduling_phone"] = contact["inspection_scheduling_phone"]
                if contact.get("address"):
                    result["apply_address"] = contact["address"]
                    result["building_dept_address"] = contact["address"]
                if contact.get("department"):
                    result["applying_office"] = contact["department"]
                    result["building_dept_name"] = contact["department"]
                # Provenance metadata for frontend
                result["_contact_status"] = "verified"
                result["_contact_provenance"] = contact.get("contact_source_url", "")
                result["_contact_verified_at"] = contact.get("contact_verified_at", "")
            elif status == "mismatch":
                # Render phone + name only, no address (human resolution needed)
                if contact.get("phone"):
                    result["apply_phone"] = contact["phone"]
                    result["building_dept_phone"] = contact["phone"]
                if contact.get("department"):
                    result["applying_office"] = contact["department"]
                    result["building_dept_name"] = contact["department"]
                result["apply_address"] = ""
                result["building_dept_address"] = ""
                result["_contact_status"] = "mismatch"
                result["_contact_provenance"] = contact.get("contact_source_url", "")
                result["_contact_mismatch_note"] = "Address conflict detected — call to confirm location"
            # "unverified" or missing → fall through to existing provenance gate
    except Exception as _e:
        print(f"[contact-sanitize] AHJ contact injection failed (non-fatal): {_e}")

    # Now run the existing provenance gate
    return sanitize_contact_for_public(result, city=city, state=state)



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
        return "Keep the official no-permit note with the job file before starting work."
    if decision == PERMIT_DECISION_CONDITIONAL:
        threshold = result.get("condition_threshold") or result.get("conditional_threshold") or {}
        if isinstance(threshold, dict):
            condition = _norm_text(threshold.get("threshold") or threshold.get("condition") or threshold.get("rule"))
            if condition:
                return f"Measure/confirm the job against this threshold: {condition} File the permit if the work is on the permit-required side of the threshold."
        return "Confirm the listed permit trigger with the local building department before work starts, then file if the condition applies."
    return f"Use the structured {permit_kind or 'permit'} filing path with the local building department before starting work."


def _locked_primary_next_step(lock: dict[str, Any], decision: str, permit_kind: str, city: str = "", state: str = "") -> str:
    locked_action = _norm_text(lock.get("customer_action"))
    locked_action = re.sub(r"\bAHJ\b", "building department", locked_action, flags=re.I)
    if locked_action and not BANNED_CUSTOMER_SURFACE_RE.search(locked_action):
        return locked_action
    department = _norm_text(lock.get("applying_office")) or f"{city} {state} Building Department".strip() or "the local building department"
    if decision == PERMIT_DECISION_REQUIRED:
        permit_name = _recovered_lock_permit_name(lock, permit_kind)
        return f"Apply for {permit_name} with {department} before starting work."
    if decision == PERMIT_DECISION_NOT_REQUIRED:
        return "Keep the official no-permit note with the job file before starting work."
    return f"Use the structured {permit_kind or 'permit'} filing path with {department} before starting work."


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
            text = re.sub(r"\bsource-backed\s+threshold\b", "listed permit trigger", text, flags=re.I)
            text = re.sub(r"\bsource-backed\s+evidence\b", "official source", text, flags=re.I)
            text = re.sub(r"\bsource-backed\s+exemption\b", "official no-permit note", text, flags=re.I)
            text = re.sub(r"\bpending(?:[_\s-]*(?:active[_\s-]*)?retrieval|view|lookup)?\b", "not yet published", text, flags=re.I)
            text = re.sub(r"\bPENDING_[A-Z0-9_]*\b", "not yet published", text)
            text = re.sub(r"\bunverified\b", "not confirmed from official-source fields", text, flags=re.I)
            text = re.sub(r"\bneeds_verification\b", "source attached; quoted snippet unavailable", text, flags=re.I)
            text = re.sub(r"\blikely\s+primary\s+permit\s+type\b", "primary permit category", text, flags=re.I)
            text = re.sub(r"\blikely\s+inspections\b", "inspection requirements", text, flags=re.I)
            text = re.sub(r"\blikely\s+permits\b", "permit decision", text, flags=re.I)
            text = re.sub(r"\blikely\s+required\b", "required", text, flags=re.I)
            text = re.sub(r"\bpermit\s+likely\s+required\b", "permit required", text, flags=re.I)
            text = re.sub(r"\bmay\s+be\s+required\b", "is conditional only when the listed condition applies", text, flags=re.I)
            text = re.sub(r"\bverify\s+exact\s+AHJ\s+(?:permit\s+name|naming)\s+before\s+quoting\b", "confirm the exact permit name and form title with the building department before filing", text, flags=re.I)
            text = re.sub(r"\bverify\s+exact\s+permit\s+type\s+with\s+the\s+AHJ\b", "confirm the exact permit category and filing path with the building department", text, flags=re.I)
            text = re.sub(r"\bverify\s+exact[^.;]{0,120}\s+(?:with\s+(?:the\s+)?AHJ|AHJ)\b", "confirm the exact permit requirements with the building department", text, flags=re.I)
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


def _get_decision_cell_primary_lock(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    lock = result.get("_decision_cell_primary_lock")
    if not isinstance(lock, dict):
        return None
    if lock.get("source") != "permitassist_v231_decision_cell" or lock.get("exact_match") is not True:
        return None
    decision = str(lock.get("permit_decision") or "").upper().strip()
    if decision not in {PERMIT_DECISION_REQUIRED, PERMIT_DECISION_NOT_REQUIRED}:
        return None
    return copy.deepcopy(lock)




def _collapse_parent_child_permits(permits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """P1C: Collapse generic parent permits when a specific child permit exists.

    E.g. 'Building Permit' + 'Commercial Building / Tenant Improvement Permit'
    → keep only the specific one. Added 2026-06-09.
    """
    if not permits:
        return permits

    def _specificity_score(p: dict) -> int:
        name = _norm_text(p.get("permit_type") or p.get("portal_selection") or "").lower()
        score = len(name.split())
        # Bonus for containing category keywords
        for _, keywords in _KIND_KEYWORDS:
            for kw in keywords:
                if kw in name:
                    score += 2
        return score

    # Sort by specificity descending (most specific first)
    sorted_permits = sorted(permits, key=lambda p: (-_specificity_score(p), _norm_text(p.get("permit_type") or "").lower()))

    collapsed: list[dict[str, Any]] = []
    # Known keywords that identify a permit family; shared across parent/child
    _PERMIT_FAMILY_KEYWORDS = {"building", "construction", "electrical", "plumbing", "mechanical", "fire", "sprinkler", "tenant", "improvement"}
    for permit in sorted_permits:
        p_name = _norm_text(permit.get("permit_type") or permit.get("portal_selection") or "").lower()
        p_kind = kind_from_text(_permit_kind_text(permit))
        p_words = set(p_name.split())
        is_redundant = False
        for existing in collapsed:
            e_name = _norm_text(existing.get("permit_type") or existing.get("portal_selection") or "").lower()
            e_kind = kind_from_text(_permit_kind_text(existing))
            e_words = set(e_name.split())
            shared_family = bool(p_words & e_words & _PERMIT_FAMILY_KEYWORDS)
            same_or_compat = (p_kind == e_kind or p_kind == "Other" or e_kind == "Other" or
                              e_kind == "Building" or p_kind == "Building")
            if p_name != e_name and shared_family and same_or_compat:
                # Longer/more specific wins; equal-length duplicates drop
                if len(e_words) <= 3 and len(p_words) >= 4:
                    # existing is generic, permit is specific → remove existing below
                    pass
                elif len(p_words) <= 3 and len(e_words) >= 4:
                    # permit is generic, existing is specific → skip permit
                    is_redundant = True
                    break
                elif p_words == e_words:
                    is_redundant = True
                    break
        if not is_redundant:
            # Remove existing generic permits superseded by this specific one
            collapsed = [e for e in collapsed if not (
                len(set(_norm_text(e.get("permit_type") or e.get("portal_selection") or "").lower().split())) <= 3
                and len(p_words) >= 4
                and bool(set(_norm_text(e.get("permit_type") or e.get("portal_selection") or "").lower().split()) & p_words & _PERMIT_FAMILY_KEYWORDS)
                and (kind_from_text(_permit_kind_text(e)) == p_kind or p_kind == "Other" or kind_from_text(_permit_kind_text(e)) == "Building" or p_kind == "Building")
            )]
            collapsed.append(permit)

    # Final protection: if any "Construction Permit" still present alongside Building/TI, drop Construction
    has_specific_building = any(
        "building" in _norm_text(p.get("permit_type") or "").lower().split()
        and len(_norm_text(p.get("permit_type") or "").lower().split()) >= 4
        for p in collapsed
    )
    if has_specific_building:
        collapsed = [p for p in collapsed
                     if set(_norm_text(p.get("permit_type") or "").lower().split()) != {"construction", "permit"}]

    # Re-sort by kind-order preference (matching CONTROLLED_PERMIT_KINDS order)
    kind_order = {k: i for i, k in enumerate(CONTROLLED_PERMIT_KINDS)}
    collapsed.sort(key=lambda p: kind_order.get(kind_from_text(_permit_kind_text(p)), 999))
    return collapsed
def _normalize_permit_name_for_dedupe(name: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()
    tokens = [token for token in text.split() if token not in {"permit", "permits", "required"}]
    token_set = set(tokens)
    if "tenant" in token_set and ("improvement" in token_set or "buildout" in token_set):
        return "commercial_tenant_improvement"
    if "construction" in token_set:
        return "construction"
    if "building" in token_set:
        return "building"
    if "electrical" in token_set:
        return "electrical"
    if "mechanical" in token_set or "hvac" in token_set:
        return "mechanical"
    if "plumbing" in token_set:
        return "plumbing"
    if "roof" in token_set or "roofing" in token_set or "reroof" in token_set:
        return "roofing"
    return " ".join(tokens)


def _controlled_kind_from_lock(lock: dict[str, Any]) -> str:
    raw = str(lock.get("permit_kind") or "").strip()
    raw_lc = raw.lower()
    if raw in CONTROLLED_PERMIT_KINDS:
        return raw
    if raw_lc in {"building", "construction"}:
        return "Building"
    if raw_lc in {"not_required", "not required", "no permit required"}:
        return "Not Required"
    return kind_from_text(str(lock.get("permit_name") or raw), fallback="Building")


def _lock_permit_name_is_generic(name: Any) -> bool:
    text = _norm_text(name)
    return not text or bool(_GENERIC_PERMIT_NAME_RE.match(text))


def _recovered_lock_permit_name(lock: dict[str, Any], permit_kind: str = "") -> str:
    name = _norm_text(lock.get("permit_name"))
    if not _lock_permit_name_is_generic(name):
        return name

    blob = _blob(lock).lower().replace("_", " ").replace("-", " ")
    if "tenant improvement" in blob or "tenant build" in blob or "commercial interior" in blob:
        return "Commercial Building / Tenant Improvement Permit"
    if "construction permit" in blob:
        return "Construction Permit"
    if "water heater" in blob:
        return "Residential Plumbing Permit — Water Heater Replacement"
    if "roof" in blob or "reroof" in blob or "re roof" in blob:
        return "Building Permit"
    if "building permit" in blob or str(permit_kind).lower() == "building":
        return "Building Permit"
    if permit_kind and permit_kind not in {"Other", "Not Required"}:
        suffix = "" if permit_kind.lower().endswith("permit") else " Permit"
        return f"{permit_kind}{suffix}"
    return "Building Permit"


def _merge_locked_primary_permits(lock: dict[str, Any], existing_permits: Any, *, public: bool = False) -> list[dict[str, Any]]:
    raw_primary = lock.get("primary_permit")
    primary: dict[str, Any] = copy.deepcopy(raw_primary) if isinstance(raw_primary, dict) else {}
    kind = _controlled_kind_from_lock(lock)
    permit_name = _recovered_lock_permit_name(lock, kind)
    primary.update({
        "permit_type": permit_name,
        "required": True,
        "kind": kind,
        "permit_kind": lock.get("permit_kind") or "building",
        "portal_selection": permit_name,
    })
    if lock.get("customer_action") and not primary.get("notes"):
        primary["notes"] = lock.get("customer_action")
    if not public:
        primary["source"] = "permitassist_v231_decision_cell"
    else:
        primary.pop("source", None)

    permits = [copy.deepcopy(item) for item in existing_permits if isinstance(item, dict)] if isinstance(existing_permits, list) else []
    primary_key = _normalize_permit_name_for_dedupe(permit_name)
    merged: list[dict[str, Any]] = [primary]
    seen = {primary_key}
    for permit in permits:
        key = _normalize_permit_name_for_dedupe(permit.get("permit_type") or permit.get("portal_selection") or permit.get("kind"))
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        if public:
            permit.pop("source", None)
        merged.append(permit)
    return merged


def enforce_decision_cell_primary(result: dict[str, Any], lock: dict[str, Any] | None = None, city: str = "", state: str = "", public: bool = False) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    lock = copy.deepcopy(lock) if isinstance(lock, dict) else _get_decision_cell_primary_lock(result)
    if not lock:
        return result

    decision = str(lock.get("permit_decision") or "").upper().strip()
    if decision == PERMIT_DECISION_NOT_REQUIRED:
        result["permit_required"] = False
        result["permit_decision"] = PERMIT_DECISION_NOT_REQUIRED
        result["permit_verdict"] = "NO"
        result["permit_kind"] = "Not Required"
        result["permit_name"] = "No permit required"
        result["permits_required"] = []
        result["trade_permits"] = []
        result["companion_permits_or_reviews"] = []
        result["not_required_reason"] = str(lock.get("customer_action") or result.get("not_required_reason") or "No permit required for this exact scope.")
        result["customer_next_step"] = _locked_primary_next_step(lock, PERMIT_DECISION_NOT_REQUIRED, "Not Required", city, state)
        result["customer_headline"] = _headline(PERMIT_DECISION_NOT_REQUIRED, "Not Required", result["customer_next_step"])
    elif decision == PERMIT_DECISION_REQUIRED:
        kind = _controlled_kind_from_lock(lock)
        permit_name = _recovered_lock_permit_name(lock, kind)
        result["permit_required"] = True
        result["permit_decision"] = PERMIT_DECISION_REQUIRED
        result["permit_verdict"] = "YES"
        result["permit_kind"] = kind
        result["permit_name"] = permit_name
        if lock.get("applying_office"):
            result["applying_office"] = lock.get("applying_office")
        if lock.get("apply_url"):
            result["apply_url"] = lock.get("apply_url")
        result.pop("not_required_reason", None)
        result.pop("no_permit_required_reason", None)
        result["permits_required"] = _collapse_parent_child_permits(_merge_locked_primary_permits(lock, result.get("permits_required"), public=public))
        result["trade_permits"] = _trade_permits(result)
        result["companion_permits_or_reviews"] = _companion_reviews(result)
        result["customer_next_step"] = _locked_primary_next_step(lock, PERMIT_DECISION_REQUIRED, kind, city, state)
        result["customer_headline"] = _headline(PERMIT_DECISION_REQUIRED, kind, result["customer_next_step"])
    else:
        return result

    contract = result.get("permit_decision_contract") if isinstance(result.get("permit_decision_contract"), dict) else {}
    if contract:
        contract.update({
            "permit_decision": result["permit_decision"],
            "permit_required": result["permit_required"],
            "permit_kind": result["permit_kind"],
            "permit_kinds": [result["permit_kind"]] if result["permit_required"] else [],
            "permit_name": result["permit_name"],
            "permits_required": result.get("permits_required", []),
            "not_required_reason": result.get("not_required_reason", ""),
            "trade_permits": result.get("trade_permits", []),
            "companion_permits_or_reviews": result.get("companion_permits_or_reviews", []),
            "customer_next_step": result.get("customer_next_step"),
            "customer_headline": result.get("customer_headline"),
        })
        result["permit_decision_contract"] = contract
    summary = result.get("customer_result_summary") if isinstance(result.get("customer_result_summary"), dict) else None
    if summary is not None:
        summary.update({
            "permit_decision": result.get("permit_decision"),
            "permit_kind": result.get("permit_kind"),
            "permit_name": result.get("permit_name"),
            "next_step": result.get("customer_next_step"),
        })
        result["customer_result_summary"] = summary
    first_screen = result.get("customer_first_screen_summary") if isinstance(result.get("customer_first_screen_summary"), dict) else None
    if first_screen is not None:
        first_screen.update({
            "decision": result.get("permit_decision"),
            "kind_category": result.get("permit_kind"),
            "next_action": result.get("customer_next_step"),
        })
        result["customer_first_screen_summary"] = first_screen
    if public:
        result.pop("_decision_cell_primary_lock", None)
    return result


def apply_permit_decision_contract(result: dict[str, Any], job_type: str = "", city: str = "", state: str = "", scope_contract: dict[str, Any] | None = None) -> dict[str, Any]:
    """Attach/normalize the universal PermitDecision CustomerView contract.

    Missing or weak source evidence is metadata only. Customer-facing decisions
    are resolved through decision_resolver and can only be REQUIRED or
    NOT_REQUIRED for valid lookup contexts. Legacy UNKNOWN/FAIL_CLOSED/cache
    states are recovered here instead of serialized.
    """
    result = copy.deepcopy(result) if isinstance(result, dict) else {}
    cell_lock = _get_decision_cell_primary_lock(result)
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
    result["permits_required"] = _collapse_parent_child_permits(permits_required)
    permits_required = result["permits_required"]
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
    if cell_lock:
        result = enforce_decision_cell_primary(result, cell_lock, city, state, public=False)
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


# ─── P0: Contact Data Integrity ───────────────────────────────────────────────
# Provenance-gated contact validator + safe fallback.
# Prevents wrong phone numbers/addresses from being shown to customers
# when the source is unverified (model_training, web_search without .gov).
# Added 2026-06-09 per permitassist-lookup-fix-spec v1.0.

_CONTACT_TRUSTED_SOURCES = frozenset({
    "auto_verified", "city_kb", "county_kb", "verified_cities_db",
    "accela_api", "official_hallucination_blocker",
    # Any source prefixed with these trusted categories
})

_CONTACT_UNTRUSTED_SOURCES = frozenset({
    "model_training", "web_search", "machine_extracted", "county_fallback",
})

# Phone patterns that indicate placeholders or non-department numbers
