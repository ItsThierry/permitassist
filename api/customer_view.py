"""Typed customer-visible projection for PermitAssist 3.0 CustomerView.

This module is intentionally pure: no network, no database, and no endpoint
wiring.  It creates the only shapes customer surfaces are allowed to consume.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import uuid
from typing import Any, ClassVar, Iterable
from urllib.parse import urlparse

EXACT_FINAL = "EXACT_FINAL"
PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE = "PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE"
NO_PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE = "NO_PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE"
PENDING_ACTIVE_RETRIEVAL = "PENDING_ACTIVE_RETRIEVAL"
PENDING_MANUAL_COMPLETION = "PENDING_MANUAL_COMPLETION"
OUT_OF_SCOPE = "OUT_OF_SCOPE"
SCHEMA_VERSION = "permitassist.customer_view.v1"

_INTERNAL_KEY_PATTERNS = (
    r"^permitassist3_revised$",
    r"^completion_ticket$",
    r"^live_retrieval$",
    r"^source_content_hash_sha256$",
    r"^source_snapshot_ref$",
    r"^lookup_id$",
    r"^pending_reason$",
    r"^missing_fields$",
    r"^raw(?:_|$)",
    r"(?:^|_)trace(?:s)?$",
    r"provider",
    r"api[_-]?key",
    r"secret",
    r"token",
    r"_evidence_pack",
    r"evidence_metadata",
)

_BANNED_VALUE_PATTERNS = (
    r"^\s*Permit Required\s*$",
    r"^\s*Building Permit\s*$",
    r"^\s*Residential Remodel Permit\s*$",
    r"\blikely\b",
    r"\btypically\b",
    r"\bgenerally\b",
    r"\bvaries by\b",
    r"\bcontact\s+(?:the\s+)?AHJ\b",
    r"\bcheck\s+with\s+(?:the\s+)?AHJ\b",
    r"\bverify\s+with\s+(?:the\s+)?AHJ\b",
    r"\bPendingView\b",
    r"\bPENDING_MANUAL_COMPLETION\b",
    r"\bPENDING_ACTIVE_RETRIEVAL\b",
    r"No customer-final answer yet",
    r"manual completion pending",
    r"Missing source-backed fields",
)

_GENERIC_FINAL_PATTERNS = (
    r"^\s*Permit Required\s*$",
    r"^\s*Building Permit\s*$",
    r"^\s*Residential Remodel Permit\s*$",
    r"\bverify exact\b",
    r"\bexact permit type needs AHJ verification\b",
    r"\bneeds AHJ verification\b",
    r"\bunknown\b",
)

_PROSE_FIELDS_THAT_MUST_BE_SAFE = (
    "customer_summary",
    "summary",
    "headline",
    "next_step",
    "verification_note",
    "warning",
    "warnings",
    "quality_warnings",
    "permit_notes",
    "notes",
)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _is_generic_final(value: Any) -> bool:
    text = _clean(value)
    return (not text) or any(re.search(pattern, text, flags=re.I) for pattern in _GENERIC_FINAL_PATTERNS)


def _official_url(value: Any) -> bool:
    text = _clean(value)
    parsed = urlparse(text)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _infer_vertical(job_type: str, explicit_vertical: str | None = None) -> str:
    if explicit_vertical:
        return _clean(explicit_vertical).lower()
    text = _clean(job_type).lower()
    if any(token in text for token in ("residential", "adu", "garage", "single family", "home", "house")):
        return "residential"
    if any(token in text for token in ("restaurant", "food", "commercial kitchen", "hood", "grease")):
        return "restaurant_ti"
    if any(token in text for token in ("medical", "clinic", "dental", "healthcare")):
        return "medical_clinic_ti"
    if "solar" in text or "photovoltaic" in text or " pv" in f" {text}":
        return "solar"
    if any(token in text for token in ("electrical", "mechanical", "plumbing", "mep")):
        return "mep"
    if "office" in text:
        return "office_ti"
    if any(token in text for token in ("tenant improvement", "tenant", "commercial", "buildout", "build out")):
        return "office_ti"
    if any(token in text for token in ("remodel", "kitchen", "bathroom")):
        return "residential"
    return "general"


def normalize_approval_timeline_for_customer(value: Any) -> dict[str, str] | None:
    """Return the safe customer timeline shape: dict[str, str] or None."""
    if value is None:
        return None
    if isinstance(value, str):
        text = _clean(value)
        return {"simple": text} if text else None
    if isinstance(value, dict):
        out: dict[str, str] = {}
        for key, item in value.items():
            key_text = _clean(key)
            value_text = _clean(item)
            if key_text and value_text:
                out[key_text] = value_text
        return out or None
    return None


@dataclass(frozen=True)
class CustomerOutputScanner:
    """Recursive scanner over keys and values for customer-output violations."""

    key_patterns: tuple[re.Pattern[str], ...] = field(
        default_factory=lambda: tuple(re.compile(pattern, re.I) for pattern in _INTERNAL_KEY_PATTERNS)
    )
    value_patterns: tuple[re.Pattern[str], ...] = field(
        default_factory=lambda: tuple(re.compile(pattern, re.I) for pattern in _BANNED_VALUE_PATTERNS)
    )

    def _walk(self, value: Any, path: str = "$") -> Iterable[tuple[str, str, str]]:
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key)
                yield (f"{path}.{key_text}", "key", key_text)
                yield from self._walk(item, f"{path}.{key_text}")
        elif isinstance(value, (list, tuple, set)):
            for index, item in enumerate(value):
                yield from self._walk(item, f"{path}[{index}]")
        elif isinstance(value, str):
            yield (path, "value", value)

    def scan(self, value: Any) -> dict[str, Any]:
        findings: list[dict[str, str]] = []
        if isinstance(value, dict) and value.get("customer_final") is False:
            findings.append({"path": "$.customer_final", "kind": "value", "pattern": "customer_final_false", "text": "False"})
        for path, kind, text in self._walk(value):
            patterns = self.key_patterns if kind == "key" else self.value_patterns
            for pattern in patterns:
                if pattern.search(text):
                    findings.append({"path": path, "kind": kind, "pattern": pattern.pattern, "text": text})
        return {"pass": not findings, "findings": findings}


@dataclass(frozen=True)
class CustomerView:
    final_answer_state: str
    customer_final: bool
    permit_required: bool | None
    permit_name: str | None
    official_portal_category_path: str | None
    filing_path: str
    job_type: str
    city: str
    state: str
    vertical: str
    ahj_name: str
    ahj_contact: dict[str, Any]
    apply_url: str | None
    submission_options: list[dict[str, str]]
    approval_timeline: dict[str, str] | None
    official_source_provenance: list[dict[str, str]]
    next_steps: list[str]
    companion_permits_reviews: list[dict[str, Any]]
    schema_version: str = SCHEMA_VERSION
    view_type: str = "CustomerView"

    PUBLIC_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema_version",
            "view_type",
            "final_answer_state",
            "customer_final",
            "permit_required",
            "permit_name",
            "official_portal_category_path",
            "filing_path",
            "job_type",
            "city",
            "state",
            "vertical",
            "ahj_name",
            "ahj_contact",
            "apply_url",
            "submission_options",
            "approval_timeline",
            "official_source_provenance",
            "next_steps",
            "companion_permits_reviews",
        }
    )

    def to_dict(self) -> dict[str, Any]:
        return {field_name: getattr(self, field_name) for field_name in self.PUBLIC_FIELDS}


@dataclass(frozen=True)
class PendingView:
    final_answer_state: str
    customer_final: bool
    permit_required: bool | None
    job_type: str
    city: str
    state: str
    vertical: str
    ahj_name: str
    lookup_id: str
    pending_reason: str
    missing_fields: list[str]
    next_steps: list[str]
    schema_version: str = SCHEMA_VERSION
    view_type: str = "PendingView"

    PUBLIC_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema_version",
            "view_type",
            "final_answer_state",
            "customer_final",
            "permit_required",
            "job_type",
            "city",
            "state",
            "vertical",
            "ahj_name",
            "lookup_id",
            "pending_reason",
            "missing_fields",
            "next_steps",
        }
    )

    def to_dict(self) -> dict[str, Any]:
        return {field_name: getattr(self, field_name) for field_name in self.PUBLIC_FIELDS}


def _pending(
    *,
    state: str = PENDING_ACTIVE_RETRIEVAL,
    reason: str,
    missing_fields: list[str] | None = None,
    job_type: str,
    city: str,
    state_code: str,
    vertical: str,
    ahj_name: str | None = None,
    permit_required: bool | None = None,
) -> PendingView:
    lookup_seed = "|".join([job_type, city, state_code, vertical, reason])
    lookup_id = f"pa3-cv-{uuid.uuid5(uuid.NAMESPACE_URL, lookup_seed).hex[:16]}"
    if state == OUT_OF_SCOPE:
        steps = ["This request is outside the supported PermitAssist filing workflow."]
    elif state == PENDING_MANUAL_COMPLETION:
        steps = ["PermitAssist will complete a manual source-backed filing-path review before a final answer is issued."]
    else:
        steps = ["PermitAssist is completing official-source retrieval before a final answer is issued."]
    return PendingView(
        final_answer_state=state,
        customer_final=False,
        permit_required=permit_required,
        job_type=_clean(job_type),
        city=_clean(city),
        state=_clean(state_code).upper(),
        vertical=vertical,
        ahj_name=_clean(ahj_name) or f"{_clean(city)} {_clean(state_code).upper()}".strip(),
        lookup_id=lookup_id,
        pending_reason=reason,
        missing_fields=missing_fields or [],
        next_steps=steps,
    )


def _guidance_filing_path(
    permit_required: bool | None,
    *,
    unsupported: bool = False,
    permit_name: str | None = None,
) -> str:
    if unsupported:
        return "Invalid/Unsupported Jurisdiction — PermitAssist cannot issue a permit answer for this location."
    if permit_required is False:
        return "No Permit Required — source-backed guidance; keep the cited source with the job record."
    clean_name = _clean(permit_name)
    if clean_name and not _is_generic_final(clean_name):
        return f"Permit Required — {clean_name}. Verify the exact local application/form path with the permitting office before filing."
    return "Permit Required — Building Permit. Verify the exact local application/form path with the permitting office before filing."


def _first_clean(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        text = _clean(raw.get(key))
        if text:
            return text
    return ""


def _customer_ahj_contact(raw: dict[str, Any], *, ahj_name: str, apply_url: str | None = None) -> dict[str, Any]:
    office = _first_clean(raw, "applying_office", "office_name", "department", "department_name", "ahj_name") or _clean(ahj_name)
    phone = _first_clean(raw, "apply_phone", "office_phone", "department_phone", "phone")
    address = _first_clean(raw, "apply_address", "office_address", "department_address", "address")
    portal = _clean(apply_url or raw.get("apply_url") or raw.get("portal_url"))
    return {
        "department": office,
        "phone": phone or None,
        "address": address or None,
        "portal_url": portal if _official_url(portal) else None,
        "verification_note": "Confirm final intake details with the permitting office before filing.",
    }


def _customer_submission_options(raw: dict[str, Any], *, apply_url: str | None = None) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    portal = _clean(apply_url or raw.get("apply_url") or raw.get("portal_url"))
    if _official_url(portal):
        options.append({
            "type": "online_portal",
            "label": "Online portal link",
            "url": portal,
            "instructions": "Start from the official portal and choose the permit/application path shown in this result when available.",
        })
    pdf = _first_clean(raw, "apply_pdf", "application_pdf", "pdf_form_url", "form_url")
    if _official_url(pdf):
        options.append({
            "type": "pdf_form",
            "label": "PDF form link",
            "url": pdf,
            "instructions": "Download the official form, complete the required fields, and follow the listed submittal instructions.",
        })
    office = _first_clean(raw, "applying_office", "office_name", "department", "department_name", "ahj_name")
    phone = _first_clean(raw, "apply_phone", "office_phone", "department_phone", "phone")
    address = _first_clean(raw, "apply_address", "office_address", "department_address", "address")
    if office or phone or address or not options:
        instructions = "Use the AHJ contact block as the fallback when the exact portal path is unclear."
        if address:
            instructions = f"Paper/in-person intake may be available at {address}. Use the AHJ contact block before filing."
        elif phone:
            instructions = f"Call {phone} and ask for the correct permit/application intake path for this scope."
        elif office:
            instructions = f"Contact {office} for paper, in-person, or email submittal instructions when the online path is unclear."
        options.append({
            "type": "paper_in_person_email_fallback",
            "label": "Paper / in-person / email fallback",
            "url": "",
            "instructions": instructions,
        })
    return options[:4]


def _candidate_text(value: Any) -> str:
    if isinstance(value, str):
        return _clean(value)
    return ""


def _first_required_permit_candidate(raw: dict[str, Any]) -> str | None:
    for list_key in ("permits_required", "required_permits", "permits", "permit_types"):
        items = raw.get(list_key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, str):
                candidate = _clean(item)
            elif isinstance(item, dict):
                required_value = item.get("required")
                if required_value is False:
                    continue
                candidate = _clean(
                    item.get("permit_name")
                    or item.get("permit_type")
                    or item.get("name")
                    or item.get("type")
                    or item.get("portal_selection")
                    or item.get("category")
                )
            else:
                continue
            if candidate and not _is_generic_final(candidate):
                return candidate
    return None


def _derived_permit_name_from_job(job_type: str, vertical: str) -> str:
    text = f"{job_type} {vertical}".lower()
    if any(term in text for term in ("restaurant", "medical", "clinic", "office", "tenant improvement", " ti", "interior alteration", "buildout", "build-out")):
        return "Commercial Tenant Improvement / Alteration Building Permit"
    if any(term in text for term in ("water heater", "plumbing")):
        return "Plumbing Permit"
    if any(term in text for term in ("electrical", "panel", "wiring")):
        return "Electrical Permit"
    if any(term in text for term in ("hvac", "mechanical", "hood")):
        return "Mechanical Permit"
    if any(term in text for term in ("demolition", "demo")):
        return "Demolition Permit"
    if any(term in text for term in ("residential", "home", "house", "garage", "kitchen", "bathroom", "remodel")):
        return "Residential Building Permit"
    return "Building Permit"


def _best_available_permit_name(raw: dict[str, Any], *, job_type: str, vertical: str, permit_required: bool | None) -> str | None:
    if permit_required is not True:
        return None
    packet = _nested_packet(raw)
    direct_candidates = (
        raw.get("display_permit_name"),
        raw.get("official_permit_name"),
        raw.get("official_application_category"),
        raw.get("permit_name"),
        raw.get("permit_type"),
        raw.get("_permit_display_name"),
        packet.get("display_permit_name"),
        packet.get("permit_name"),
        packet.get("permit_type"),
    )
    for value in direct_candidates:
        candidate = _candidate_text(value)
        if candidate and not _is_generic_final(candidate):
            return candidate
    candidate = _first_required_permit_candidate(raw)
    if candidate:
        return candidate
    return _derived_permit_name_from_job(job_type, vertical)


def _guidance_next_steps(permit_required: bool | None, *, unsupported: bool = False) -> list[str]:
    if unsupported:
        return ["Check the city/state spelling or choose a supported jurisdiction before relying on this lookup."]
    if permit_required is False:
        return ["Keep the cited official source with the project file before starting work."]
    return ["Use the official source links below and confirm the exact filing category with the permitting office before filing."]


def _customer_guidance_view(
    *,
    final_answer_state: str,
    permit_required: bool | None,
    job_type: str,
    city: str,
    state_code: str,
    vertical: str,
    ahj_name: str | None = None,
    provenance: list[dict[str, str]] | None = None,
    unsupported: bool = False,
    permit_name: str | None = None,
    apply_url: str | None = None,
    raw: dict[str, Any] | None = None,
) -> CustomerView:
    raw = raw if isinstance(raw, dict) else {}
    safe_apply_url = _clean(apply_url) if _official_url(apply_url) else None
    resolved_ahj_name = _clean(ahj_name) or f"{_clean(city)} {_clean(state_code).upper()}".strip()
    return CustomerView(
        final_answer_state=final_answer_state,
        customer_final=True,
        permit_required=permit_required,
        permit_name=_clean(permit_name) or None,
        official_portal_category_path=None,
        filing_path=_guidance_filing_path(permit_required, unsupported=unsupported, permit_name=permit_name),
        job_type=_clean(job_type),
        city=_clean(city),
        state=_clean(state_code).upper(),
        vertical=vertical,
        ahj_name=resolved_ahj_name,
        ahj_contact=_customer_ahj_contact(raw, ahj_name=resolved_ahj_name, apply_url=safe_apply_url),
        apply_url=safe_apply_url,
        submission_options=_customer_submission_options(raw, apply_url=safe_apply_url),
        approval_timeline=None,
        official_source_provenance=provenance or [],
        next_steps=_guidance_next_steps(permit_required, unsupported=unsupported),
        companion_permits_reviews=[],
    )


def _first_dict_list(*values: Any) -> list[dict[str, Any]]:
    for value in values:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [value]
    return []


def _sanitize_provenance(raw_sources: list[dict[str, Any]]) -> list[dict[str, str]]:
    sanitized: list[dict[str, str]] = []
    for source in raw_sources:
        url = _clean(source.get("source_url") or source.get("url"))
        title = _clean(source.get("source_title") or source.get("title"))
        snippet = _clean(source.get("exact_quote_or_snippet") or source.get("quoted_snippet") or source.get("snippet") or source.get("quote"))
        retrieved = _clean(source.get("retrieved_at_utc") or source.get("last_verified_utc") or source.get("checked_at"))
        classification = _clean(source.get("official_source_classification") or source.get("source_type") or "official_source")
        if not _official_url(url) or not snippet:
            continue
        sanitized.append(
            {
                "source_url": url,
                "source_title": title or "Official permitting source",
                "exact_quote_or_snippet": snippet,
                "retrieved_at_utc": retrieved if retrieved.endswith("Z") else retrieved,
                "official_source_classification": classification,
            }
        )
    return sanitized


def _nested_packet(raw: dict[str, Any]) -> dict[str, Any]:
    packet = raw.get("permitassist3_revised")
    return packet if isinstance(packet, dict) else {}


_UNSUPPORTED_AHJ_STATUSES = {"unsupported", "invalid", "invalid_city", "invalid_state"}
_UNSUPPORTED_AHJ_ERRORS = {"unsupported_ahj", "invalid_ahj"}


def _is_unsupported_ahj_payload(raw: dict[str, Any]) -> bool:
    coverage_raw = raw.get("coverage_truth")
    coverage: dict[str, Any] = coverage_raw if isinstance(coverage_raw, dict) else {}
    status = _clean(raw.get("ahj_status") or raw.get("status")).lower()
    coverage_status = _clean(coverage.get("status")).lower()
    error = _clean(raw.get("error")).lower()
    return bool(
        raw.get("unsupported")
        or raw.get("final_answer_state") == OUT_OF_SCOPE
        or status in _UNSUPPORTED_AHJ_STATUSES
        or coverage_status == "ahj_not_supported"
        or error in _UNSUPPORTED_AHJ_ERRORS
    )


def _extract_exact_support(raw: dict[str, Any]) -> tuple[str | None, str | None, list[dict[str, str]]]:
    packet = _nested_packet(raw)
    permit_name = _clean(
        raw.get("source_backed_exact_permit_name")
        or raw.get("exact_permit_name")
        or raw.get("official_permit_name")
        or packet.get("source_backed_exact_permit_name")
    ) or None
    portal_path = _clean(
        raw.get("source_backed_official_portal_category_path")
        or raw.get("official_portal_category_path")
        or raw.get("official_application_category")
        or packet.get("source_backed_official_portal_category_path")
    ) or None

    if not permit_name and raw.get("permit_name_status") in {
        "exact_official_name_confirmed",
        "source_backed_official_path_confirmed",
    }:
        candidate = _clean(raw.get("permit_name") or raw.get("permit_type"))
        if not _is_generic_final(candidate):
            permit_name = candidate

    if not permit_name and raw.get("permit_type_verified") is True:
        candidate = _clean(raw.get("permit_name") or raw.get("permit_type") or raw.get("_permit_display_name"))
        if candidate and not _is_generic_final(candidate):
            permit_name = candidate

    raw_apply_path = raw.get("apply_path")
    apply_path: dict[str, Any] = raw_apply_path if isinstance(raw_apply_path, dict) else {}
    if not portal_path and apply_path.get("support_level") == "source_backed":
        candidate = _clean(apply_path.get("permit_category") or " > ".join(apply_path.get("portal_selection_path") or []))
        if candidate and not _is_generic_final(candidate):
            portal_path = candidate

    if (
        not portal_path
        and raw.get("permit_name_status") == "official_category_confirmed_exact_label_missing"
        and raw.get("permit_name_source_field") == "official_application_category"
    ):
        candidate = _clean(raw.get("permit_name") or raw.get("permit_type") or raw.get("official_application_category"))
        if candidate and not _is_generic_final(candidate):
            portal_path = candidate
            if permit_name == candidate:
                permit_name = None

    if permit_name and _is_generic_final(permit_name):
        permit_name = None
    if portal_path and _is_generic_final(portal_path):
        portal_path = None

    provenance = _sanitize_provenance(
        _first_dict_list(
            raw.get("official_source_provenance"),
            packet.get("official_source_provenance"),
            raw.get("claim_citations"),
            raw.get("sources"),
        )
    )
    return permit_name, portal_path, provenance


def _selected_customer_prose(raw: dict[str, Any]) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for field_name in _PROSE_FIELDS_THAT_MUST_BE_SAFE:
        if field_name in raw:
            selected[field_name] = raw[field_name]
    return selected


def build_customer_view(
    internal_result: Any,
    *,
    job_type: str,
    city: str,
    state: str,
    explicit_vertical: str | None = None,
) -> CustomerView | PendingView:
    """Project raw/internal lookup output into a customer-safe CustomerView.

    PendingView remains available for internal retry/manual-resolution queues,
    but this public projection must not expose PendingView or internal pending
    fields to customer surfaces.
    """
    vertical = _infer_vertical(job_type, explicit_vertical)
    ahj_name = f"{_clean(city)} {_clean(state).upper()}".strip()
    if not isinstance(internal_result, dict):
        return _customer_guidance_view(
            final_answer_state=PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE,
            permit_required=True,
            job_type=job_type,
            city=city,
            state_code=state,
            vertical=vertical,
            ahj_name=ahj_name,
        )

    if _is_unsupported_ahj_payload(internal_result):
        return _customer_guidance_view(
            final_answer_state=OUT_OF_SCOPE,
            permit_required=None,
            job_type=job_type,
            city=city,
            state_code=state,
            vertical=vertical,
            ahj_name=ahj_name,
            unsupported=True,
        )

    permit_name, portal_path, provenance = _extract_exact_support(internal_result)
    raw_required = internal_result.get("permit_required")
    permit_required = raw_required if isinstance(raw_required, bool) else True
    fallback_permit_name = _best_available_permit_name(
        internal_result,
        job_type=job_type,
        vertical=vertical,
        permit_required=permit_required,
    )
    if not (permit_name or portal_path) or not provenance:
        return _customer_guidance_view(
            final_answer_state=NO_PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE if permit_required is False else PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE,
            permit_required=permit_required,
            job_type=job_type,
            city=city,
            state_code=state,
            vertical=vertical,
            ahj_name=ahj_name,
            provenance=provenance,
            permit_name=fallback_permit_name,
            apply_url=_clean(internal_result.get("apply_url")),
            raw=internal_result,
        )

    prose_scan = CustomerOutputScanner(key_patterns=()).scan(_selected_customer_prose(internal_result))
    if prose_scan["findings"]:
        return _customer_guidance_view(
            final_answer_state=NO_PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE if permit_required is False else PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE,
            permit_required=permit_required,
            job_type=job_type,
            city=city,
            state_code=state,
            vertical=vertical,
            ahj_name=ahj_name,
            provenance=provenance,
            permit_name=permit_name or fallback_permit_name,
            apply_url=_clean(internal_result.get("apply_url")),
            raw=internal_result,
        )

    filing_path = permit_name or portal_path or ""
    raw_apply_path = internal_result.get("apply_path")
    apply_path = raw_apply_path if isinstance(raw_apply_path, dict) else {}
    raw_apply_url = internal_result.get("apply_url") or apply_path.get("portal_url")
    safe_apply_url = _clean(raw_apply_url) if _official_url(raw_apply_url) else None
    resolved_ahj_name = _clean(internal_result.get("ahj_name")) or ahj_name
    view = CustomerView(
        final_answer_state=EXACT_FINAL,
        customer_final=True,
        permit_required=True if internal_result.get("permit_required") is not False else False,
        permit_name=permit_name,
        official_portal_category_path=portal_path,
        filing_path=filing_path,
        job_type=_clean(job_type),
        city=_clean(city),
        state=_clean(state).upper(),
        vertical=vertical,
        ahj_name=resolved_ahj_name,
        ahj_contact=_customer_ahj_contact(internal_result, ahj_name=resolved_ahj_name, apply_url=safe_apply_url),
        apply_url=safe_apply_url,
        submission_options=_customer_submission_options(internal_result, apply_url=safe_apply_url),
        approval_timeline=normalize_approval_timeline_for_customer(internal_result.get("approval_timeline")),
        official_source_provenance=provenance,
        next_steps=["Use the source-backed filing path listed here before preparing the submittal."],
        companion_permits_reviews=[item for item in (internal_result.get("companion_permits_reviews") or internal_result.get("companion_permits") or []) if isinstance(item, dict)],
    )
    scan = CustomerOutputScanner().scan(view.to_dict())
    if scan["findings"]:
        return _customer_guidance_view(
            final_answer_state=NO_PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE if permit_required is False else PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE,
            permit_required=permit_required,
            job_type=job_type,
            city=city,
            state_code=state,
            vertical=vertical,
            ahj_name=ahj_name,
            provenance=provenance,
            permit_name=permit_name or fallback_permit_name,
            apply_url=_clean(internal_result.get("apply_url")),
            raw=internal_result,
        )
    return view


__all__ = [
    "EXACT_FINAL",
    "PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE",
    "NO_PERMIT_REQUIRED_SOURCE_BACKED_GUIDANCE",
    "PENDING_ACTIVE_RETRIEVAL",
    "PENDING_MANUAL_COMPLETION",
    "OUT_OF_SCOPE",
    "CustomerOutputScanner",
    "CustomerView",
    "PendingView",
    "build_customer_view",
    "normalize_approval_timeline_for_customer",
]
