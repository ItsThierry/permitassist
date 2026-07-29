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
PERMIT_DECISION_VERIFY = "VERIFY"
PERMIT_DECISION_FAIL_CLOSED = "FAIL_CLOSED_UNSUPPORTED_OR_NO_EVIDENCE"

CONTROLLED_PERMIT_DECISIONS = {
    PERMIT_DECISION_REQUIRED,
    PERMIT_DECISION_NOT_REQUIRED,
    PERMIT_DECISION_CONDITIONAL,
    PERMIT_DECISION_VERIFY,
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
    text_tokens = tuple(re.findall(r"[a-z0-9]+", text))

    def has(*phrases: str) -> bool:
        for phrase in phrases:
            phrase_tokens = tuple(re.findall(r"[a-z0-9]+", phrase.lower()))
            if phrase_tokens and len(phrase_tokens) <= len(text_tokens) and any(
                text_tokens[index : index + len(phrase_tokens)] == phrase_tokens
                for index in range(len(text_tokens) - len(phrase_tokens) + 1)
            ):
                return True
        return False

    reviews: list[str] = []
    if has("fire", "sprinkler", "alarm", "hood", "ansul"):
        reviews.append("Fire review")
    if has("health", "food", "restaurant", "clinic", "medical", "grease interceptor"):
        reviews.append("Health Department review")
    if has("zoning", "planning", "change of use", "change of occupancy", "signage"):
        reviews.append("Planning/Zoning review")
    if has("utility", "meter", "service", "interconnection"):
        reviews.append("Utility coordination")
    if has("ada", "accessibility", "accessible"):
        reviews.append("Accessibility review")
    if has("structural", "load bearing", "roof", "roofing", "reroof", "foundation"):
        reviews.append("Structural review")
    if has("electrical", "mechanical", "plumbing", "mep", "hvac", "fixture"):
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


_REQUIRED_NO_PERMIT_PROSE_RE = re.compile(
    r"\b(?:no\s+permit\s+(?:needed|required|review\s+needed|submission\s+needed)|"
    r"permit\s+not\s+required|work\s+can\s+proceed\s+without\s+(?:a\s+)?permit)\b|"
    r"\$0\s+permit\s+fee\s+for\s+(?:this\s+exact\s+scope|the\s+stated\s+scope|like[-\s]?for[-\s]?like|ordinary|finish|cabinet|flooring)",
    re.I,
)
_APPLY_ONLINE_RE = re.compile(r"\bapply\s+online\b", re.I)
_NOT_REQUIRED_AFFIRMATIVE_PRIMARY_RE = re.compile(
    r"\b(?:permit\s+required|required\s+permit\s+package|"
    r"file\s+the\s+required\s+permit|apply\s+for\s+(?:the\s+)?"
    r"(?:required\s+)?(?:building\s+)?permit)\b",
    re.I,
)


def _visible_decision_text(result: dict[str, Any]) -> str:
    return _blob({
        key: result.get(key)
        for key in (
            "customer_headline", "customer_next_step", "approval_timeline", "timeline",
            "fee_range", "fee_estimate", "total_cost_estimate", "requirements",
            "next_steps", "watch_out", "customer_result_summary", "customer_first_screen_summary",
        )
    })


def _repair_customer_decision_prose_coherence(result: dict[str, Any], decision: str = "") -> dict[str, Any]:
    """Keep customer prose coherent with the resolved binary decision.

    This boundary guard repairs copied/stale prose only; it never downgrades a
    source-backed REQUIRED decision to CONTACT_AHJ/UNKNOWN and never removes the
    binary REQUIRED/NOT_REQUIRED answer to make a test pass.
    """
    if not isinstance(result, dict):
        return {}
    decision = (decision or result.get("permit_decision") or "").upper().strip()
    if decision == PERMIT_DECISION_REQUIRED:
        if isinstance(result.get("approval_timeline"), dict):
            timeline = dict(result.get("approval_timeline") or {})
            simple = str(timeline.get("simple") or "")
            if _REQUIRED_NO_PERMIT_PROSE_RE.search(simple):
                timeline["simple"] = "Permit filing/review is required for the resolved permit category; timeline depends on portal intake completeness and local review queue."
            result["approval_timeline"] = timeline
        for fee_key in ("fee_range", "fee_estimate", "total_cost_estimate"):
            fee_text = str(result.get(fee_key) or "")
            if fee_text and _REQUIRED_NO_PERMIT_PROSE_RE.search(fee_text):
                result[fee_key] = "Fee depends on declared valuation, trade scope, plan-review fees, and the current local fee schedule; verify in the city portal before quoting."
        for key in ("customer_headline", "customer_next_step"):
            text = str(result.get(key) or "")
            if text and _REQUIRED_NO_PERMIT_PROSE_RE.search(text):
                permit_kind = _norm_text(result.get("permit_kind") or result.get("permit_name") or "Building Permit")
                if key == "customer_headline":
                    result[key] = _headline(PERMIT_DECISION_REQUIRED, permit_kind, "")
                else:
                    dept = _norm_text(result.get("applying_office") or result.get("building_dept_name")) or "the local building department"
                    result[key] = f"File under {permit_kind} with {dept}; use the verified local filing path or counter/contact intake before starting work."
    elif decision == PERMIT_DECISION_NOT_REQUIRED:
        result["apply_url"] = ""
        result["online_application_url"] = ""
        if isinstance(result.get("apply_path"), dict):
            apply_path = dict(result.get("apply_path") or {})
            apply_path.update({
                "state": "NOT_APPLICABLE",
                "channel": "no_permit_required",
                "support_level": "not applicable",
                "portal_url": None,
                "platform": None,
                "login_required": None,
                "verification_note": "No permit filing path is needed for the resolved NOT_REQUIRED scope.",
            })
            result["apply_path"] = apply_path
        for key in ("customer_next_step", "summary", "job_summary"):
            text = str(result.get(key) or "")
            if text and _APPLY_ONLINE_RE.search(text):
                result[key] = "Keep the official no-permit note with the job file before starting work."
        if isinstance(result.get("customer_result_summary"), dict):
            summary = dict(result.get("customer_result_summary") or {})
            if _APPLY_ONLINE_RE.search(str(summary.get("next_step") or "")):
                summary["next_step"] = "Keep the official no-permit note with the job file before starting work."
            result["customer_result_summary"] = summary
        if isinstance(result.get("customer_first_screen_summary"), dict):
            first = dict(result.get("customer_first_screen_summary") or {})
            if _APPLY_ONLINE_RE.search(str(first.get("next_action") or "")):
                first["next_action"] = "Keep the official no-permit note with the job file before starting work."
            result["customer_first_screen_summary"] = first

        # Late customer-shape normalizers can synthesize affirmative primary-
        # permit copy before an exact NOT_REQUIRED lock is re-applied. Repair the
        # affected primary-filing fields without deleting separately listed
        # CONDITIONAL/VERIFY companion permits or their trigger guidance.
        safe_timeline = (
            "No primary permit review timeline applies to the resolved scope; "
            "separately triggered companion permits may have their own timelines."
        )
        safe_fee = (
            "No primary permit fee applies to the resolved scope; separately "
            "triggered companion permits may have their own fees."
        )
        safe_inspection = (
            "No primary permit inspection is scheduled for the resolved scope; "
            "separately triggered companion permits may have their own inspections."
        )
        safe_summary = "No primary permit is required for the resolved scope."
        for key in ("summary", "job_summary"):
            value = result.get(key)
            if isinstance(value, str) and _NOT_REQUIRED_AFFIRMATIVE_PRIMARY_RE.search(value):
                result[key] = safe_summary
        for key in ("timeline", "approval_timeline"):
            value = result.get(key)
            if isinstance(value, str) and _NOT_REQUIRED_AFFIRMATIVE_PRIMARY_RE.search(value):
                result[key] = safe_timeline
            elif isinstance(value, dict):
                repaired = dict(value)
                for child_key, child in repaired.items():
                    if isinstance(child, str) and _NOT_REQUIRED_AFFIRMATIVE_PRIMARY_RE.search(child):
                        repaired[child_key] = safe_timeline
                result[key] = repaired
        for key in ("fee_range", "fee_estimate", "total_cost_estimate"):
            value = result.get(key)
            if isinstance(value, str) and _NOT_REQUIRED_AFFIRMATIVE_PRIMARY_RE.search(value):
                result[key] = safe_fee
        if _NOT_REQUIRED_AFFIRMATIVE_PRIMARY_RE.search(str(result.get("inspection_booking") or "")):
            result["inspection_booking"] = safe_inspection
        if _NOT_REQUIRED_AFFIRMATIVE_PRIMARY_RE.search(str(result.get("required_permit_summary") or "")):
            result["required_permit_summary"] = safe_summary

        safe_list_copy = {
            "checklist": (
                "Confirm the work remains within the exact no-permit scope; check "
                "separately listed conditional companion permits if the scope changes."
            ),
            "what_to_bring": (
                "Keep photos/invoices documenting that the work remained within "
                "the exact no-permit scope."
            ),
            "documents_needed": (
                "Keep photos/invoices documenting that the work remained within "
                "the exact no-permit scope."
            ),
            "requirements": (
                "Keep the primary work within the exact no-permit scope and review "
                "any separately listed companion-permit triggers if the scope changes."
            ),
        }
        for key, replacement in safe_list_copy.items():
            values = result.get(key)
            if not isinstance(values, list):
                continue
            repaired_values = []
            for value in values:
                repaired_value = (
                    replacement
                    if isinstance(value, str) and _NOT_REQUIRED_AFFIRMATIVE_PRIMARY_RE.search(value)
                    else value
                )
                if repaired_value not in repaired_values:
                    repaired_values.append(repaired_value)
            result[key] = repaired_values

        if isinstance(result.get("customer_result_summary"), dict):
            summary = dict(result.get("customer_result_summary") or {})
            if _NOT_REQUIRED_AFFIRMATIVE_PRIMARY_RE.search(str(summary.get("timeline") or "")):
                summary["timeline"] = safe_timeline
            if _NOT_REQUIRED_AFFIRMATIVE_PRIMARY_RE.search(str(summary.get("fee_cost_caveat") or "")):
                summary["fee_cost_caveat"] = safe_fee
            result["customer_result_summary"] = summary
    return result


def _strip_customer_banned_text(value: Any, key: str = "") -> Any:
    if isinstance(value, str):
        if key in {"exact_name_status", "exact_apply_url_status", "apply_url_status", "exact_source_status", "source_status"}:
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
    if lock.get("source") not in {"permitassist_v231_decision_cell", "permitassist_v24_decision_cell"} or lock.get("exact_match") is not True:
        return None
    decision = str(lock.get("permit_decision") or "").upper().strip()
    if decision not in {PERMIT_DECISION_REQUIRED, PERMIT_DECISION_NOT_REQUIRED}:
        return None
    return copy.deepcopy(lock)




def _is_placeholder_generic_permit_row(permit: dict[str, Any]) -> bool:
    """True only for rows that have no specific title, portal, or trade kind."""
    if not isinstance(permit, dict):
        return False
    title_fields = [
        _norm_text(permit.get("permit_type")),
        _norm_text(permit.get("portal_selection")),
        _norm_text(permit.get("name")),
        _norm_text(permit.get("title")),
    ]
    if any(value and not _GENERIC_PERMIT_NAME_RE.match(value) for value in title_fields):
        return False
    kind_text = " ".join(
        value for value in [
            _norm_text(permit.get("kind")),
            _norm_text(permit.get("permit_kind")),
        ] if value
    )
    inferred_kind = kind_from_text(kind_text, fallback="Other") if kind_text else "Other"
    if inferred_kind not in {"Other", "Building"}:
        return False
    return any(value for value in title_fields) or bool(kind_text)


def _collapse_parent_child_permits(permits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """P1C: Collapse generic parent permits when a specific child permit exists.

    E.g. 'Building Permit' + 'Commercial Building / Tenant Improvement Permit'
    → keep only the specific one. Added 2026-06-09.
    """
    if not permits:
        return permits

    # Drop only true placeholders (e.g. {permit_type: "Permit"}), not rows whose
    # portal_selection/kind carries specific trade scope.
    if any(not _is_placeholder_generic_permit_row(p) for p in permits):
        permits = [p for p in permits if not _is_placeholder_generic_permit_row(p)]

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
    if name and (not _lock_permit_name_is_generic(name) or (lock.get("source") in {"permitassist_v231_decision_cell", "permitassist_v24_decision_cell"} and lock.get("exact_match") is True)):
        # Exact publishable Decision Cells own the primary permit framing, even
        # when an AHJ calls the filing category simply "permit".  Generic legacy
        # locks without Decision Cell provenance still recover to the safer
        # customer-facing heuristic below.
        return name

    blob = _blob(lock).lower().replace("_", " ").replace("-", " ")
    blob_tokens = tuple(re.findall(r"[a-z0-9]+", blob))

    def has(*phrases: str) -> bool:
        for phrase in phrases:
            phrase_tokens = tuple(re.findall(r"[a-z0-9]+", phrase.lower()))
            if phrase_tokens and len(phrase_tokens) <= len(blob_tokens) and any(
                blob_tokens[index : index + len(phrase_tokens)] == phrase_tokens
                for index in range(len(blob_tokens) - len(phrase_tokens) + 1)
            ):
                return True
        return False

    if has("tenant improvement", "tenant build", "commercial interior"):
        return "Commercial Building / Tenant Improvement Permit"
    if has("construction permit"):
        return "Construction Permit"
    if has("water heater"):
        return "Residential Plumbing Permit — Water Heater Replacement"
    if has("roof", "roofing", "reroof", "re roof"):
        return "Building Permit"
    if has("building permit") or str(permit_kind).lower() == "building":
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
        if _lock_permit_name_is_generic(permit.get("permit_type") or permit.get("portal_selection") or permit.get("kind")):
            continue
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        if public:
            permit.pop("source", None)
        merged.append(permit)
    return merged


def _restore_locked_primary_first(permits: list[dict[str, Any]], lock: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep the exact Decision Cell primary as the first permit after collapse.

    Generic parent/child cleanup can otherwise re-sort the list or drop a
    source-backed primary such as "Construction Permit" in favor of a generic
    commercial/TI heuristic.  The cell primary is authoritative; companion/trade
    rows remain after it.
    """
    if not permits:
        return permits
    kind = _controlled_kind_from_lock(lock)
    primary_name = _recovered_lock_permit_name(lock, kind)
    primary_key = _normalize_permit_name_for_dedupe(primary_name)
    raw_primary = lock.get("primary_permit")
    primary: dict[str, Any] = copy.deepcopy(raw_primary) if isinstance(raw_primary, dict) else {}
    primary.update({
        "permit_type": primary_name,
        "required": True,
        "kind": kind,
        "permit_kind": lock.get("permit_kind") or "building",
        "portal_selection": primary_name,
    })
    if lock.get("customer_action") and not primary.get("notes"):
        primary["notes"] = lock.get("customer_action")
    primary["source"] = "permitassist_v231_decision_cell"

    rest: list[dict[str, Any]] = []
    for permit in permits:
        key = _normalize_permit_name_for_dedupe(permit.get("permit_type") or permit.get("portal_selection") or permit.get("kind"))
        source = permit.get("source")
        if key == primary_key or source == "permitassist_v231_decision_cell":
            continue
        rest.append(permit)
    return [primary, *rest]


def _merge_locked_decision_cell_sources(result: dict[str, Any], lock: dict[str, Any], *, public: bool = False) -> None:
    """Preserve trusted Decision Cell provenance through public serialization.

    Source-backed v2.3.1 cells are the authority for the main decision. If the
    normal source locality filter drops an abbreviated/small-city official host,
    the customer response must still carry the vetted Decision Cell source path
    rather than a bare REQUIRED/NOT_REQUIRED answer.
    """
    raw_sources_obj = lock.get("sources")
    raw_sources = raw_sources_obj if isinstance(raw_sources_obj, list) else []
    raw_urls_obj = lock.get("source_urls")
    raw_urls = raw_urls_obj if isinstance(raw_urls_obj, list) else []
    merged_sources: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_source(url: Any, title: Any = "", snippet: Any = "") -> None:
        value = str(url or "").strip()
        if not value.startswith("http") or value in seen:
            return
        seen.add(value)
        source = {
            "url": value,
            "title": str(title or "Official AHJ source").strip() or "Official AHJ source",
            "publisher": str(lock.get("applying_office") or "Official AHJ source").strip() or "Official AHJ source",
            "snippet": str(snippet or "").strip(),
        }
        if not public:
            source["source"] = "permitassist_v231_decision_cell"
        merged_sources.append(source)

    for source in raw_sources:
        if isinstance(source, dict):
            add_source(source.get("url") or source.get("source_url"), source.get("title") or source.get("source_title"), source.get("quote") or source.get("snippet"))
        elif isinstance(source, str):
            add_source(source)
    for url in raw_urls:
        add_source(url)

    existing = result.get("sources") if isinstance(result.get("sources"), list) else []
    for source in existing:
        if isinstance(source, dict):
            add_source(source.get("url") or source.get("source_url"), source.get("title") or source.get("source_title"), source.get("snippet") or source.get("quote"))
        elif isinstance(source, str):
            add_source(source)
    if merged_sources:
        result["sources"] = merged_sources
        result["source_urls"] = [source["url"] for source in merged_sources]


_LOCKED_NOT_REQUIRED_STALE_WARNING_RE = re.compile(
    r"\b(?:before\s+filing|demoted\s+to\s+verify|primary\s+permit\s+was\s+repaired|"
    r"forced\s+away\s+from\s+residential|claim[-\s]?linked\s+official\s+decision\s+evidence|"
    r"exact\s+permit\s+(?:name|category)|form\s+title|requires?\s+building\s*/\s*ti\s+review)\b",
    re.I,
)


def _repair_locked_not_required_customer_mirrors(result: dict[str, Any], lock: dict[str, Any]) -> None:
    """Scrub stale primary-filing mirrors for a validated exact exemption lock.

    This helper is intentionally lock-gated. It does not infer trust from a
    submitted/public DTO and it preserves separately triggered companion rows,
    companion warnings, and their customer guidance.
    """
    result["permits_required_logic"] = []
    result["required_permit_names"] = []
    result["required_permit_families"] = []
    result["required_permit_segments"] = []

    apply_path = dict(result.get("apply_path") or {}) if isinstance(result.get("apply_path"), dict) else {}
    apply_path.update({
        "state": "NOT_APPLICABLE",
        "channel": "no_permit_required",
        "support_level": "not applicable",
        "permit_category": None,
        "permit_type": None,
        "portal_url": None,
        "platform": None,
        "login_required": None,
        "documents_to_prepare": [],
        "portal_selection_path": [],
        "steps": [],
        "stop_before": None,
        "verification_note": "No permit filing path is needed for the resolved NOT_REQUIRED scope.",
    })
    result["apply_path"] = apply_path

    raw_warnings = (result.get("warnings") if isinstance(result.get("warnings"), list) else []) or []
    safe_warnings: list[Any] = []
    seen_warnings: set[str] = set()
    for warning in raw_warnings:
        text = str(warning or "").strip()
        if not text or _LOCKED_NOT_REQUIRED_STALE_WARNING_RE.search(text):
            continue
        key = text.casefold()
        if key not in seen_warnings:
            seen_warnings.add(key)
            safe_warnings.append(warning)
    result["warnings"] = safe_warnings

    locked_urls = [
        str(value or "").strip()
        for value in ((lock.get("source_urls") if isinstance(lock.get("source_urls"), list) else []) or [])
        if str(value or "").strip().startswith("http")
    ]
    locked_sources = (lock.get("sources") if isinstance(lock.get("sources"), list) else []) or []
    for source in locked_sources:
        if isinstance(source, dict):
            url = str(source.get("url") or source.get("source_url") or "").strip()
            if url.startswith("http"):
                locked_urls.append(url)
        elif str(source or "").strip().startswith("http"):
            locked_urls.append(str(source).strip())
    has_locked_source = bool(locked_urls)

    source_support = dict(result.get("source_support") or {}) if isinstance(result.get("source_support"), dict) else {}
    if has_locked_source:
        source_support["has_source_backed_evidence"] = True
        source_support["has_official_source"] = True
    source_support["decision_mutation_allowed"] = False
    result["source_support"] = source_support

    source_floor = dict(result.get("source_evidence_floor") or {}) if isinstance(result.get("source_evidence_floor"), dict) else {}
    if source_floor:
        if has_locked_source:
            source_floor["has_source_backed_evidence"] = True
        source_floor["decision_mutation_allowed"] = False
        source_floor["source_support"] = copy.deepcopy(source_support)
        result["source_evidence_floor"] = source_floor

    contract = dict(result.get("permit_decision_contract") or {}) if isinstance(result.get("permit_decision_contract"), dict) else {}
    if contract:
        contract["source_support"] = copy.deepcopy(source_support)
        contract_floor = dict(contract.get("source_evidence_floor") or {}) if isinstance(contract.get("source_evidence_floor"), dict) else {}
        if contract_floor:
            if has_locked_source:
                contract_floor["has_source_backed_evidence"] = True
            contract_floor["decision_mutation_allowed"] = False
            contract_floor["source_support"] = copy.deepcopy(source_support)
            contract["source_evidence_floor"] = contract_floor
        result["permit_decision_contract"] = contract

    customer_summary = dict(result.get("customer_result_summary") or {}) if isinstance(result.get("customer_result_summary"), dict) else {}
    freshness = str(customer_summary.get("freshness_label") or "")
    if re.search(r"\bbefore\s+filing\b", freshness, re.I):
        customer_summary["freshness_label"] = "Official source date not published; verify current requirements before starting work"
        result["customer_result_summary"] = customer_summary


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
        _merge_locked_decision_cell_sources(result, lock, public=public)
    elif decision == PERMIT_DECISION_REQUIRED:
        kind = _controlled_kind_from_lock(lock)
        permit_name = _recovered_lock_permit_name(lock, kind)
        preserve_existing_primary = bool(lock.get("preserve_existing_primary"))
        if preserve_existing_primary:
            existing_name = _norm_text(result.get("permit_name"))
            raw_primary = lock.get("primary_permit") if isinstance(lock.get("primary_permit"), dict) else {}
            if existing_name and not _lock_permit_name_is_generic(existing_name):
                permit_name = existing_name
            elif raw_primary:
                permit_name = _norm_text(raw_primary.get("permit_type") or raw_primary.get("portal_selection") or permit_name)
            existing_kind = _norm_text(result.get("permit_kind"))
            if existing_kind:
                kind = existing_kind
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
        if preserve_existing_primary:
            result["permits_required"] = _collapse_parent_child_permits([
                copy.deepcopy(item) for item in (result.get("permits_required") or []) if isinstance(item, dict)
            ])
        else:
            merged_permits = _merge_locked_primary_permits(lock, result.get("permits_required"), public=False)
            result["permits_required"] = _restore_locked_primary_first(_collapse_parent_child_permits(merged_permits), lock)
        if public:
            for permit in result["permits_required"]:
                permit.pop("source", None)
        result["trade_permits"] = _trade_permits(result)
        result["companion_permits_or_reviews"] = _companion_reviews(result)
        result["customer_next_step"] = _locked_primary_next_step(lock, PERMIT_DECISION_REQUIRED, kind, city, state)
        result["customer_headline"] = _headline(PERMIT_DECISION_REQUIRED, kind, result["customer_next_step"])
        _merge_locked_decision_cell_sources(result, lock, public=public)
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
    if decision == PERMIT_DECISION_NOT_REQUIRED:
        _repair_locked_not_required_customer_mirrors(result, lock)
    if public:
        result.pop("_decision_cell_primary_lock", None)
    return _repair_customer_decision_prose_coherence(result, str(result.get("permit_decision") or ""))


def apply_permit_decision_contract(result: dict[str, Any], job_type: str = "", city: str = "", state: str = "", scope_contract: dict[str, Any] | None = None) -> dict[str, Any]:
    """Attach/normalize the universal PermitDecision CustomerView contract.

    Customer-facing decisions are resolved through decision_resolver into the
    typed REQUIRED/NOT_REQUIRED/CONDITIONAL/VERIFY contract. Legacy
    UNKNOWN/FAIL_CLOSED/cache states are recovered instead of serialized.
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

    result["permit_required"] = dto["permit_required"]
    result["permit_decision"] = decision
    result["permit_verdict"] = (
        "YES" if decision == PERMIT_DECISION_REQUIRED
        else "NO" if decision == PERMIT_DECISION_NOT_REQUIRED
        else decision
    )
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
            "threshold_evidence": bool(
                result.get("condition_threshold")
                or result.get("conditional_threshold")
            ) if decision == PERMIT_DECISION_CONDITIONAL else False,
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
    result = _repair_customer_decision_prose_coherence(result, decision)
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
    visible_text = _visible_decision_text(result)
    if decision == PERMIT_DECISION_REQUIRED and _REQUIRED_NO_PERMIT_PROSE_RE.search(visible_text):
        issues.append("required_no_permit_prose_contradiction")
    if decision == PERMIT_DECISION_NOT_REQUIRED and _APPLY_ONLINE_RE.search(visible_text):
        issues.append("not_required_apply_online_contradiction")
    return issues


def _customer_visible_contract_blob(value: Any, key: str = "") -> str:
    """Flatten customer-copy fields while allowing structured uncertainty status.

    Field-level values such as exact_name_status='unverified' are allowed by the
    contract. The banned phrase scanner applies to prose/customer copy, not that
    controlled status enum.
    """
    if key in {"exact_name_status", "exact_apply_url_status", "apply_url_status", "exact_source_status", "source_status"}:
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
