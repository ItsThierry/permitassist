"""Flag-gated canonical Permit Manifest projection.

Session 2 consolidates existing typed customer truth at the final egress boundary.
This module is deliberately pure and IO-free: it never resolves a jurisdiction,
changes a permit decision, invents a permit family, or adds a companion. It only
canonicalizes fields already present in the customer DTO. Unsourced heuristic
companions remain visible but are demoted to VERIFY.
"""
from __future__ import annotations

import builtins
import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
from typing import Any, cast
from urllib.parse import urlsplit

MANIFEST_FLAG = "PERMITASSIST_PERMIT_MANIFEST_MODE"
MANIFEST_SCHEMA = "permit_manifest_v1"
ONTOLOGY_SOURCE_SHA256 = "9b5e6748786c890ea6138871a49180c9531283c127c56bf036f23c48741601c8"
_MANIFEST_AUTH_FIELD = "authority_tag"
_PROCESS_KEY_ATTR = "_permitassist_process_manifest_auth_key"
_configured_manifest_key = str(
    os.environ.get("PERMITASSIST_MANIFEST_AUTH_SECRET")
    or os.environ.get("SESSION_SECRET")
    or ""
).encode("utf-8")
if _configured_manifest_key:
    _MANIFEST_AUTH_KEY = _configured_manifest_key
else:
    # `api.permit_manifest` and legacy `permit_manifest` import paths can coexist
    # in one worker. Share one process-private key across those module aliases.
    if not hasattr(builtins, _PROCESS_KEY_ATTR):
        setattr(builtins, _PROCESS_KEY_ATTR, secrets.token_bytes(32))
    _MANIFEST_AUTH_KEY = getattr(builtins, _PROCESS_KEY_ATTR)


def _manifest_auth_bytes(manifest: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in manifest.items() if key != _MANIFEST_AUTH_FIELD}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def _manifest_auth_tag(manifest: dict[str, Any]) -> str:
    """Return a visibly public, versioned MAC rather than a secret-like token.

    Generic secret redaction correctly treats a bare 64-character digest as
    suspicious.  Grouping and versioning the non-secret Manifest MAC keeps it
    stable across repeated public redaction while remaining strict to parse.
    """
    digest = hmac.new(
        _MANIFEST_AUTH_KEY, _manifest_auth_bytes(manifest), hashlib.sha256
    ).hexdigest()
    return "pm1-" + "-".join(digest[index : index + 8] for index in range(0, 64, 8))


def _sign_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    signed = copy.deepcopy(manifest)
    signed[_MANIFEST_AUTH_FIELD] = _manifest_auth_tag(signed)
    return signed


def is_authenticated_permit_manifest(manifest: Any) -> bool:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != MANIFEST_SCHEMA:
        return False
    supplied = str(manifest.get(_MANIFEST_AUTH_FIELD) or "")
    if not re.fullmatch(r"pm1-(?:[0-9a-f]{8}-){7}[0-9a-f]{8}", supplied):
        return False
    expected = _manifest_auth_tag(manifest)
    return hmac.compare_digest(supplied, expected)

FAMILIES = frozenset({
    "NO_PRIMARY_PERMIT", "BUILDING", "ROOFING", "ELECTRICAL", "PLUMBING",
    "MECHANICAL", "REFRIGERATION", "GAS", "FIRE_LIFE_SAFETY",
    "ZONING_PLANNING", "OCCUPANCY_CO", "DEMOLITION", "SIGN", "POOL_SPA",
    "MOVING", "LANDMARKS_HISTORIC", "HEALTH", "GRADING_SITE_CIVIL_ROW",
    "WASTEWATER_FOG", "ENVIRONMENTAL", "LIQUOR", "MANUFACTURED_STRUCTURE",
    "TRADE_OR_SUBPERMIT_REVIEW", "ACCESSIBILITY", "UTILITY", "OTHER", "VERIFY",
})
STATUSES = frozenset({"REQUIRED", "CONDITIONAL", "NOT_REQUIRED", "NEEDS_INPUT", "VERIFY"})

# Historical public `family_decisions[].family` values are a compatibility
# adapter only. Canonical truth remains the uppercase manifest family ID.
_LEGACY_FAMILY_IDS = {
    "FIRE_LIFE_SAFETY": "fire",
    "ZONING_PLANNING": "planning",
    "OCCUPANCY_CO": "co",
    "WASTEWATER_FOG": "wastewater",
    "GRADING_SITE_CIVIL_ROW": "grading",
    "LANDMARKS_HISTORIC": "historic",
    "NO_PRIMARY_PERMIT": "no_primary_permit",
}

_EXACT_ALIASES = {
    "": "VERIFY", "VERIFY": "VERIFY", "UNKNOWN": "VERIFY", "OTHER": "OTHER",
    "BUILDING": "BUILDING", "BUILDING_TRADE": "BUILDING", "BUILDING_TI": "BUILDING",
    "BUILDING_CONSTRUCTION": "BUILDING", "COMMERCIAL_BUILDING": "BUILDING",
    "RESIDENTIAL_BUILDING": "BUILDING", "BUILDING_PERMIT": "BUILDING",
    "CONSTRUCTION_PERMIT": "BUILDING", "ALL_BUILDING_PERMITS": "BUILDING",
    "ROOFING": "ROOFING", "REROOF": "ROOFING", "ELECTRICAL": "ELECTRICAL",
    "PLUMBING": "PLUMBING", "MECHANICAL": "MECHANICAL", "HVAC": "MECHANICAL",
    "REFRIGERATION": "REFRIGERATION", "GAS": "GAS", "FIRE": "FIRE_LIFE_SAFETY",
    "FIRE_LIFE_SAFETY": "FIRE_LIFE_SAFETY", "ZONING": "ZONING_PLANNING",
    "PLANNING": "ZONING_PLANNING", "PLANNING_ZONING": "ZONING_PLANNING",
    "ZONING_PLANNING": "ZONING_PLANNING", "CO": "OCCUPANCY_CO",
    "OCCUPANCY": "OCCUPANCY_CO", "OCCUPANCY_CO": "OCCUPANCY_CO",
    "CERTIFICATE_OF_OCCUPANCY": "OCCUPANCY_CO", "DEMOLITION": "DEMOLITION",
    "SIGN": "SIGN", "POOL": "POOL_SPA", "POOL_SPA": "POOL_SPA",
    "MOVING": "MOVING", "LANDMARKS": "LANDMARKS_HISTORIC",
    "HISTORIC": "LANDMARKS_HISTORIC", "LANDMARKS_HISTORIC": "LANDMARKS_HISTORIC",
    "HEALTH": "HEALTH", "SEPTIC_OSS_HEALTH": "HEALTH", "GRADING": "GRADING_SITE_CIVIL_ROW",
    "SITE_CIVIL_ROW": "GRADING_SITE_CIVIL_ROW", "WASTEWATER": "WASTEWATER_FOG",
    "WASTEWATER_FOG": "WASTEWATER_FOG", "ENVIRONMENTAL": "ENVIRONMENTAL",
    "LIQUOR": "LIQUOR", "MANUFACTURED_STRUCTURE_INSTALLATION": "MANUFACTURED_STRUCTURE",
    "TRADE_OR_SUBPERMIT_REVIEW": "TRADE_OR_SUBPERMIT_REVIEW",
    "ACCESSIBILITY": "ACCESSIBILITY", "ADA": "ACCESSIBILITY", "UTILITY": "UTILITY",
    "NO_PRIMARY_PERMIT": "NO_PRIMARY_PERMIT", "NOT_REQUIRED": "NO_PRIMARY_PERMIT",
}

_ORDERED_DISPLAY_RULES = (
    (("NO PERMIT REQUIRED", "NOT REQUIRED"), "NO_PRIMARY_PERMIT"),
    (("ACCESSIBILITY", "ADA", "PATH OF TRAVEL"), "ACCESSIBILITY"),
    (("REFRIGERATION",), "REFRIGERATION"),
    (("FIRE", "SPRINKLER", "ALARM", "LIFE SAFETY", "SUPPRESSION"), "FIRE_LIFE_SAFETY"),
    (("CERTIFICATE OF OCCUPANCY", "CHANGE OF OCCUPANCY", "OCCUPANCY PERMIT"), "OCCUPANCY_CO"),
    (("ZONING", "PLANNING", "LAND USE", "SETBACK"), "ZONING_PLANNING"),
    (("LANDMARK", "HISTORIC"), "LANDMARKS_HISTORIC"),
    (("DEMOLITION", "DEMOLISH"), "DEMOLITION"),
    (("ROOF", "RE-ROOF", "REROOF"), "ROOFING"),
    (("ELECTRICAL",), "ELECTRICAL"), (("PLUMBING",), "PLUMBING"),
    (("MECHANICAL", "HVAC"), "MECHANICAL"), (("GAS", "FUEL GAS"), "GAS"),
    (("WASTEWATER", "FOG", "SEWER"), "WASTEWATER_FOG"),
    (("HEALTH", "SEPTIC", "OSS"), "HEALTH"), (("LIQUOR", "ALCOHOL"), "LIQUOR"),
    (("POOL", "SPA"), "POOL_SPA"), (("SIGN",), "SIGN"),
    (("MOVING", "MOVE PERMIT"), "MOVING"),
    (("MANUFACTURED STRUCTURE", "MANUFACTURED HOME"), "MANUFACTURED_STRUCTURE"),
    (("GRADING", "RIGHT OF WAY", "RIGHT-OF-WAY", "ENCROACHMENT", "SITE/CIVIL"), "GRADING_SITE_CIVIL_ROW"),
    (("UTILITY", "INTERCONNECTION"), "UTILITY"),
    (("BUILDING", "CONSTRUCTION", "REMODEL", "ALTERATION", "TENANT IMPROVEMENT"), "BUILDING"),
    (("PERMIT CATEGORY VERIFICATION", "PERMIT REQUIREMENT VERIFICATION"), "VERIFY"),
    (("PERMIT", "PLAN REVIEW", "REVIEW"), "OTHER"),
)


def _token(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(value or "").upper()).strip("_")


def canonical_family(value: Any) -> str:
    token = _token(value)
    if token in _EXACT_ALIASES:
        return _EXACT_ALIASES[token]
    display_tokens = tuple(part for part in token.split("_") if part)
    for terms, family in _ORDERED_DISPLAY_RULES:
        for term in terms:
            term_tokens = tuple(part for part in _token(term).split("_") if part)
            if not term_tokens or len(term_tokens) > len(display_tokens):
                continue
            if any(
                display_tokens[index : index + len(term_tokens)] == term_tokens
                for index in range(len(display_tokens) - len(term_tokens) + 1)
            ):
                return family
    return "VERIFY"


def _family_from_row(row: dict[str, Any]) -> str:
    for key in (
        "family", "filing_family", "primary_permit_family", "permit_family",
        "permit_kind", "permit_type", "approval_type", "display_family", "kind",
        "permit_name", "name",
    ):
        if row.get(key) not in (None, ""):
            family = canonical_family(row.get(key))
            if family != "VERIFY":
                return family
    return "VERIFY"


def _status_from_row(row: dict[str, Any], *, default: str = "VERIFY") -> str:
    for key in ("status", "decision", "companion_decision", "required_status", "requirement"):
        token = _token(row.get(key))
        if token in STATUSES:
            return token
        if token in {"CONDITIONAL_REQUIRED", "LIKELY", "LIKELY_REQUIRED", "MAY_BE_REQUIRED", "MAY_NEED"}:
            return "CONDITIONAL"
        if token in {"EXEMPT", "NO"}:
            return "NOT_REQUIRED"
    if row.get("required") is True:
        return "REQUIRED"
    if row.get("required") is False and _token(row.get("certainty")) not in {"LIKELY", "CONDITIONAL"}:
        return default
    certainty = _token(row.get("certainty"))
    if certainty in {"CONDITIONAL", "LIKELY", "LIKELY_REQUIRED", "MAY_BE_REQUIRED"}:
        return "CONDITIONAL"
    if certainty == "NEEDS_INPUT":
        return "NEEDS_INPUT"
    return default


def _source_ref(row: dict[str, Any]) -> str | None:
    for key in ("source_ref", "source_url", "evidence_ref", "provenance_ref"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    source = row.get("source")
    if isinstance(source, str) and source.strip():
        return source.strip()
    if isinstance(source, dict):
        for key in ("source_ref", "url", "source_url", "id"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    evidence = row.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, dict):
                ref = _source_ref(item)
                if ref:
                    return ref
    return None


def _valid_public_ref(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or any(char.isspace() for char in candidate):
        return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


def _source_refs(row: dict[str, Any]) -> list[str]:
    """Collect all explicit public source references without copying opaque row state."""
    refs: list[str] = []

    def add(value: Any, *, url_only: bool = False) -> None:
        candidate = _valid_public_ref(value) if url_only else (value.strip() if isinstance(value, str) and value.strip() else None)
        if candidate:
            if candidate not in refs:
                refs.append(candidate)

    existing_refs = row.get("source_refs")
    if isinstance(existing_refs, list):
        for ref in existing_refs:
            add(ref)
    for key in ("source_ref", "evidence_ref", "provenance_ref"):
        add(row.get(key))
    add(row.get("source_url"), url_only=True)
    for key in ("source", "evidence", "sources", "provenance", "application_route"):
        nested = row.get(key)
        items = nested if isinstance(nested, list) else [nested]
        for item in items:
            if isinstance(item, str):
                add(item, url_only=True)
            elif isinstance(item, dict):
                for nested_key in ("source_ref", "evidence_ref", "provenance_ref", "id"):
                    add(item.get(nested_key))
                for nested_key in ("url", "source_url"):
                    add(item.get(nested_key), url_only=True)
                provenance = item.get("provenance")
                if isinstance(provenance, list):
                    for provenance_item in provenance:
                        if isinstance(provenance_item, dict):
                            for nested_key in ("source_ref", "url", "source_url", "evidence_ref"):
                                add(provenance_item.get(nested_key), url_only=nested_key in {"url", "source_url"})
    return refs


def _first_text(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _rationale(row: dict[str, Any]) -> str | None:
    rationale = _first_text(row, ("rationale", "notes", "included_because"))
    if rationale:
        return rationale
    # Canonical rows carry a backward-compatible `reason` mirror of customer
    # guidance. Do not reinterpret that mirror as source rationale on re-entry.
    if "customer_guidance" not in row:
        return _first_text(row, ("reason",))
    return None


_FAMILY_EVIDENCE_TERMS = {
    "BUILDING": ("building", "construction", "tenant improvement", "alteration"),
    "ROOFING": ("roof", "reroof", "re-roof"),
    "ELECTRICAL": ("electrical", "wiring", "circuit"),
    "PLUMBING": ("plumbing", "sewer", "water piping"),
    "MECHANICAL": ("mechanical", "hvac", "heating", "air conditioning"),
    "REFRIGERATION": ("refrigeration", "refrigerant"),
    "GAS": ("gas permit", "fuel gas"),
    "FIRE_LIFE_SAFETY": ("fire permit", "fire prevention", "suppression", "alarm permit"),
    "ZONING_PLANNING": ("zoning", "planning", "land use"),
    "OCCUPANCY_CO": ("certificate of occupancy", "change of occupancy"),
    "DEMOLITION": ("demolition", "demolish"),
    "SIGN": ("sign permit", "signage permit"),
    "HEALTH": ("health permit", "food establishment", "plan review"),
    "GRADING_SITE_CIVIL_ROW": ("grading", "encroachment", "right of way", "right-of-way"),
    "WASTEWATER_FOG": ("wastewater", "fog permit", "grease interceptor", "sewer permit"),
    "LIQUOR": ("liquor", "alcohol license"),
}


def _has_claim_bound_source(row: dict[str, Any], family: str) -> bool:
    """Require evidence tied to this family, not merely an official homepage."""
    terms = _FAMILY_EVIDENCE_TERMS.get(family, (family.lower().replace("_", " "),))
    positive_claim = re.compile(
        r"\b(?:permits?\s+(?:is|are)\s+(?:also\s+)?required|permit (?:is )?required|"
        r"requires? (?:an? )?[^.;]{0,80}permit|must (?:obtain|secure|apply)|"
        r"shall (?:obtain|secure)|apply for (?:an? |your )?[^.;]{0,80}permit)\b",
        re.I,
    )

    def family_text(value: Any) -> bool:
        text = str(value or "").lower()
        return any(term in text for term in terms)

    quote_fields = (
        "quoted_snippet", "source_quote", "quote", "snippet", "evidence_excerpt", "official_quote",
        "requirement_quote", "claim_text",
    )
    for key in quote_fields:
        quote = row.get(key)
        if (
            isinstance(quote, str)
            and len(quote.strip()) >= 20
            and family_text(quote)
            and positive_claim.search(quote)
            and any(_valid_public_ref(ref) for ref in _source_refs(row))
        ):
            return True
    for key in ("claim_citations", "citations", "evidence", "sources", "provenance"):
        nested = row.get(key)
        items = nested if isinstance(nested, list) else [nested]
        for item in items:
            if not isinstance(item, dict):
                continue
            quote_blob = " ".join(
                str(item.get(field) or "")
                for field in (*quote_fields, "claim", "field", "value", "title")
            )
            if len(quote_blob.strip()) >= 20 and family_text(quote_blob) and positive_claim.search(quote_blob) and any(
                isinstance(item.get(field), str) and len(str(item.get(field)).strip()) >= 20
                for field in quote_fields
            ) and _valid_public_ref(item.get("source_url") or item.get("url")):
                return True
    return False


def _canonical_row(
    row: dict[str, Any], *, primary: bool = False, authenticated: bool = False
) -> dict[str, Any]:
    source_refs = _source_refs(row)
    source_ref = source_refs[0] if source_refs else _valid_public_ref(_source_ref(row))
    public_source_url = next((_valid_public_ref(ref) for ref in source_refs if _valid_public_ref(ref)), None)
    status = _status_from_row(row, default="REQUIRED" if primary and row.get("required") is True else "VERIFY")
    family = _family_from_row(row)
    if status == "REQUIRED" and not authenticated and not _has_claim_bound_source(row, family):
        status = "VERIFY"
    local_name = _first_text(row, ("permit_type", "permit_name", "approval_type", "name", "portal_selection"))
    trigger = _first_text(row, ("trigger", "trigger_condition", "required_if", "condition_text", "scope_trigger", "included_because"))
    exemption = _first_text(row, ("exemption", "exemption_text", "not_required_reason"))
    authority = _first_text(row, ("authority", "application_authority", "issuing_authority", "ahj_name", "applying_office"))
    apply_url = _first_text(row, ("apply_url", "application_url", "online_application_url"))
    route_channel = _first_text(row, ("route_channel", "channel"))
    coverage_state = _first_text(row, ("coverage_state", "coverage_status", "serving_status"))
    missing_input = _first_text(row, ("missing_input", "missing_fact", "needs_input"))
    rationale = _rationale(row)
    guidance = _first_text(row, ("customer_guidance", "verification_step", "next_step"))
    segment = _first_text(row, ("segment", "job_segment", "request_segment"))
    certainty = _first_text(row, ("certainty",))
    if status == "VERIFY" and not guidance:
        label = (local_name or _family_from_row(row).replace("_", " ").title()).strip()
        guidance = f"Confirm the {label} requirement with the issuing authority before filing."
    legacy_family = {
        "FIRE_LIFE_SAFETY": "fire",
        "ZONING_PLANNING": "planning_zoning",
        "OCCUPANCY_CO": "certificate_of_occupancy",
        "GRADING_SITE_CIVIL_ROW": "grading",
        "WASTEWATER_FOG": "wastewater_fog",
        "LANDMARKS_HISTORIC": "historic",
    }.get(family, family.lower())
    result = {
        "family": family,
        "filing_family": legacy_family,
        "category": legacy_family,
        "kind": _first_text(row, ("permit_kind", "kind")),
        "local_name": local_name,
        "status": status,
        "required_status": status,
        "trigger": trigger,
        "exemption": exemption,
        "authority": authority,
        "apply_url": apply_url,
        "route_channel": route_channel,
        "source_ref": source_ref,
        "source_refs": source_refs,
        "rationale": rationale,
        "coverage_state": coverage_state,
        "missing_input": missing_input,
        "customer_guidance": guidance,
        # Backward-compatible row mirrors consumed by existing UI/report code.
        "permit_type": local_name,
        "reason": guidance,
        "decision": status,
        "verdict": status,
        "required": True if status == "REQUIRED" else False if status == "NOT_REQUIRED" else None,
    }
    if segment:
        result["segment"] = segment.lower()
    if certainty and certainty.lower() in {"likely", "confirmed", "conditional", "verify"}:
        result["certainty"] = certainty.lower()
    provenance = row.get("provenance")
    if isinstance(provenance, dict):
        result["provenance"] = copy.deepcopy(provenance)
    if public_source_url:
        result["source_url"] = public_source_url
    return result


def _jurisdiction_identity(value: dict[str, Any]) -> dict[str, Any]:
    explicit = value.get("jurisdiction_identity")
    explicit_dict = explicit if isinstance(explicit, dict) else {}
    summary = value.get("customer_result_summary")
    summary = summary if isinstance(summary, dict) else {}
    nested = summary.get("jurisdiction")
    nested_dict = nested if isinstance(nested, dict) else {}
    nested_name = nested if isinstance(nested, str) and nested.strip() else None
    return {
        "jurisdiction_id": _first_text(explicit_dict, ("jurisdiction_id", "id", "ahj_id")) or _first_text(nested_dict, ("jurisdiction_id", "id", "ahj_id")) or _first_text(value, ("jurisdiction_id", "ahj_id")),
        "name": _first_text(explicit_dict, ("name", "jurisdiction_name", "ahj_name")) or _first_text(nested_dict, ("name", "jurisdiction_name", "ahj_name")) or nested_name or _first_text(value, ("jurisdiction_name", "ahj_name")),
        "type": _first_text(explicit_dict, ("type", "jurisdiction_type", "ahj_type")) or _first_text(nested_dict, ("type", "jurisdiction_type", "ahj_type")) or _first_text(value, ("jurisdiction_type", "ahj_type")),
        "city": _first_text(explicit_dict, ("city",)) or _first_text(nested_dict, ("city",)) or _first_text(value, ("city",)),
        "state": _first_text(explicit_dict, ("state",)) or _first_text(nested_dict, ("state",)) or _first_text(value, ("state",)),
        "authority_model": _first_text(explicit_dict, ("authority_model",)) or _first_text(nested_dict, ("authority_model",)) or _first_text(value, ("authority_model",)),
        "issuing_authority": _first_text(explicit_dict, ("issuing_authority",)) or _first_text(value, ("issuing_authority", "applying_office", "building_dept_name")),
        "application_authority": _first_text(explicit_dict, ("application_authority",)) or _first_text(value, ("application_authority", "applying_office", "building_dept_name")),
    }


def _typed_family_truth(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index existing typed route/decision truth without creating new families."""
    indexed: dict[str, dict[str, Any]] = {}
    decisions = value.get("family_decisions") if isinstance(value.get("family_decisions"), list) else []
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        family = _family_from_row(decision)
        if family == "VERIFY":
            continue
        truth = indexed.setdefault(family, {})
        truth.update({
            key: copy.deepcopy(decision.get(key))
            for key in (
                "status", "decision", "verdict", "trigger", "authority",
                "apply_url", "route_channel", "channel", "source_ref", "source_url",
                "validation_issue_codes", "quoted_snippet", "source_quote", "quote", "snippet",
                "claim_citations", "citations",
            )
            if decision.get(key) not in (None, "")
        })
        refs = list(dict.fromkeys([*(truth.get("source_refs") or []), *_source_refs(decision)]))
        if refs:
            truth["source_refs"] = refs
            truth.setdefault("source_ref", refs[0])
        rationale = _rationale(decision)
        if rationale:
            truth.setdefault("rationale", rationale)
    routes = value.get("family_authority_routes") if isinstance(value.get("family_authority_routes"), list) else []
    for route in routes:
        if not isinstance(route, dict):
            continue
        family = _family_from_row(route)
        if family == "VERIFY":
            continue
        truth = indexed.setdefault(family, {})
        authority_obj = route.get("authority")
        application_obj = route.get("application_route")
        authority: dict[str, Any] = dict(authority_obj) if isinstance(authority_obj, dict) else {}
        application: dict[str, Any] = dict(application_obj) if isinstance(application_obj, dict) else {}
        for candidate in (
            authority.get("application_authority"), authority.get("issuing_authority"),
            application.get("office_name"), route.get("application_authority"),
        ):
            if isinstance(candidate, str) and candidate.strip():
                truth.setdefault("authority", candidate.strip())
                break
        apply_url = _first_text(application, ("apply_url", "application_url", "online_application_url"))
        if apply_url:
            truth.setdefault("apply_url", apply_url)
        channel = _first_text(application, ("route_channel", "channel"))
        if channel:
            truth.setdefault("route_channel", channel)
        refs = list(dict.fromkeys([
            *(truth.get("source_refs") or []),
            *_source_refs(application),
            *_source_refs(route),
        ]))
        if refs:
            truth["source_refs"] = refs
            truth.setdefault("source_ref", refs[0])
        rationale = _rationale(application) or _rationale(route)
        if rationale:
            truth.setdefault("rationale", rationale)
    return indexed


def _merge_typed_family_truth(row: dict[str, Any], indexed: dict[str, dict[str, Any]]) -> dict[str, Any]:
    merged = copy.deepcopy(row)
    typed = indexed.get(_family_from_row(row), {})
    if typed:
        refs = list(dict.fromkeys([*_source_refs(merged), *(typed.get("source_refs") or [])]))
        if refs:
            merged["source_refs"] = refs
            if not _source_ref(merged):
                merged["source_ref"] = refs[0]
        if not _first_text(merged, ("authority", "application_authority", "issuing_authority", "ahj_name", "applying_office")) and typed.get("authority"):
            merged["authority"] = typed["authority"]
        if not _first_text(merged, ("apply_url", "application_url", "online_application_url")) and typed.get("apply_url"):
            merged["apply_url"] = typed["apply_url"]
        if not _first_text(merged, ("route_channel", "channel")) and typed.get("route_channel"):
            merged["route_channel"] = typed["route_channel"]
        if "validation_issue_codes" not in merged and "validation_issue_codes" in typed:
            merged["validation_issue_codes"] = copy.deepcopy(typed["validation_issue_codes"])
        if _status_from_row(merged) == "VERIFY":
            for key in ("status", "decision", "verdict"):
                if typed.get(key):
                    merged["status"] = typed[key]
                    break
        if not _first_text(merged, ("trigger", "trigger_condition", "required_if", "condition_text", "scope_trigger", "included_because")) and typed.get("trigger"):
            merged["trigger"] = typed["trigger"]
        if not _rationale(merged) and typed.get("rationale"):
            merged["rationale"] = typed["rationale"]
    return merged


def _looks_like_permit_result(value: dict[str, Any]) -> bool:
    return bool(
        "permit_decision" in value
        and any(key in value for key in ("permits_required", "permit_kind", "permit_name", "permit_required"))
    )


def _project_one(
    value: dict[str, Any], *, allow_create: bool = False,
    authenticated_creation: bool = False,
) -> dict[str, Any]:
    # A valid authenticated manifest is the immutable source for re-entry. Do
    # not derive a second copy from backward-compatible mirrors.
    manifest = value.get("permit_manifest")
    if is_authenticated_permit_manifest(manifest):
        manifest_dict = copy.deepcopy(cast(dict[str, Any], manifest))
        raw_decision = _token(manifest_dict.get("permit_decision"))
        manifest_decision = raw_decision if raw_decision in STATUSES else "VERIFY"
        raw_primary_value = manifest_dict.get("primary")
        raw_primary: dict[str, Any] = dict(raw_primary_value) if isinstance(raw_primary_value, dict) else {}
        primary = _canonical_row(raw_primary, primary=True, authenticated=True)
        primary["status"] = manifest_decision
        primary["decision"] = manifest_decision
        primary["verdict"] = manifest_decision
        primary["required"] = True if manifest_decision == "REQUIRED" else False if manifest_decision == "NOT_REQUIRED" else None
        companions = [
            _canonical_row(row, authenticated=True)
            for row in (manifest_dict.get("companions") or [])
            if isinstance(row, dict)
        ]
        out = copy.deepcopy(value)
        all_rows = [primary, *companions]
        out["permit_manifest"] = copy.deepcopy(manifest)
        out["permit_decision"] = manifest_decision
        out["permit_required"] = True if manifest_decision == "REQUIRED" else False if manifest_decision == "NOT_REQUIRED" else None
        out["permit_verdict"] = "YES" if manifest_decision == "REQUIRED" else "NO" if manifest_decision == "NOT_REQUIRED" else manifest_decision
        out["primary_permit_family"] = primary["family"]
        out["permit_kind"] = primary.get("kind") or primary.get("local_name") or _LEGACY_FAMILY_IDS.get(primary["family"], primary["family"].lower())
        out["permit_name"] = primary.get("local_name") or primary["family"].replace("_", " ").title()
        out["permit_type"] = primary.get("permit_type") or primary.get("local_name") or out["permit_name"]
        out["jurisdiction_identity"] = copy.deepcopy(manifest_dict.get("jurisdiction") or {})
        out["companion_permits"] = copy.deepcopy(companions)
        out["related_permits"] = copy.deepcopy([
            row for row in companions if row.get("status") != "REQUIRED"
        ])
        # This historical list is a binary compatibility mirror. Nonbinary
        # rows remain visible through the Manifest/family/companion surfaces.
        out["permits_required"] = copy.deepcopy([
            row for row in all_rows if row.get("status") == "REQUIRED"
        ])
        if manifest_decision == "NOT_REQUIRED":
            out["permits_required_logic"] = []
            out["required_permit_names"] = []
            out["required_permit_families"] = []
            out["required_permit_segments"] = []
            out["not_required_reason"] = out.get("not_required_reason") or primary.get("exemption") or "Permit not required for the resolved scope."
        family_decisions = copy.deepcopy(all_rows)
        for row in family_decisions:
            row["canonical_family"] = row["family"]
            row["family"] = _LEGACY_FAMILY_IDS.get(str(row["canonical_family"]), str(row["canonical_family"]).lower())
            row["verdict"] = row["status"]
            if row.get("trigger") and not row.get("condition_text"):
                row["condition_text"] = row["trigger"]
        out["family_decisions"] = family_decisions
        out["related_permits"] = copy.deepcopy([
            row for row in family_decisions[1:] if row.get("status") != "REQUIRED"
        ])
        hard_rows = [row for row in family_decisions if row.get("status") == "REQUIRED"]
        related_rows = [row for row in family_decisions[1:] if row.get("status") != "REQUIRED"]
        out["required_permit_names"] = [row.get("local_name") or row.get("permit_type") for row in hard_rows]
        out["required_permit_families"] = [row.get("family") for row in hard_rows]
        out["required_permit_segments"] = list(dict.fromkeys(str(row.get("segment")).lower() for row in hard_rows if row.get("segment")))
        out["related_permit_names"] = [row.get("local_name") or row.get("permit_type") for row in related_rows]
        out["related_permit_families"] = [row.get("family") for row in related_rows]
        out["related_permit_segments"] = list(dict.fromkeys(str(row.get("segment")).lower() for row in related_rows if row.get("segment")))
        raw_filing = manifest_dict.get("filing_destination")
        filing: dict[str, Any] = dict(raw_filing) if isinstance(raw_filing, dict) else {}
        out["apply_url"] = filing.get("apply_url")
        out["online_application_url"] = filing.get("apply_url")
        if isinstance(filing.get("apply_path"), dict):
            out["apply_path"] = copy.deepcopy(filing.get("apply_path"))
        return out
    if isinstance(manifest, dict):
        # A schema literal is not authentication. Generic serialization drops
        # forged/tampered authority instead of using it to rewrite mirrors.
        value = copy.deepcopy(value)
        value.pop("permit_manifest", None)
        if not allow_create:
            return value
    elif not allow_create:
        return copy.deepcopy(value)
    raw_decision = _token(value.get("permit_decision"))
    decision = raw_decision if raw_decision in STATUSES else "VERIFY"
    # Quotation-shaped DTO fields remain caller data. Binary Manifest authority
    # is created only from an authenticated in-process PermitAuthorityInput.
    if not authenticated_creation and decision in {"REQUIRED", "NOT_REQUIRED"}:
        decision = "VERIFY"
    required_rows = [row for row in (value.get("permits_required") or []) if isinstance(row, dict)]
    primary_source = copy.deepcopy(required_rows[0]) if required_rows else {
        "primary_permit_family": value.get("primary_permit_family"),
        "permit_kind": value.get("permit_kind"),
        "permit_name": value.get("permit_name"),
        "status": decision,
        "required": value.get("permit_required"),
    }
    # `permit_kind` is the customer-facing category while the primary row's
    # local_name is the exact filing title. Preserve both in the canonical
    # Manifest instead of collapsing the category to the local title.
    if value.get("permit_kind") not in (None, ""):
        primary_source["permit_kind"] = value.get("permit_kind")
    if value.get("claim_citations") and not primary_source.get("claim_citations"):
        primary_source["claim_citations"] = copy.deepcopy(value.get("claim_citations"))
    typed_truth = _typed_family_truth(value)
    primary = _canonical_row(
        _merge_typed_family_truth(primary_source, typed_truth),
        primary=True,
        authenticated=authenticated_creation,
    )
    if not authenticated_creation and primary.get("status") in {"REQUIRED", "NOT_REQUIRED"}:
        primary["status"] = "VERIFY"
        primary["decision"] = "VERIFY"
        primary["verdict"] = "VERIFY"
        primary["required_status"] = "VERIFY"
        primary["required"] = None
        if not primary.get("customer_guidance"):
            label = primary.get("local_name") or str(primary.get("family") or "permit").replace("_", " ").title()
            primary["customer_guidance"] = f"Confirm the {label} requirement with the issuing authority before filing."
        primary["reason"] = primary.get("customer_guidance")
    if decision == "REQUIRED" and primary.get("status") != "REQUIRED":
        decision = "VERIFY"
    primary["status"] = decision
    primary["decision"] = decision
    primary["required_status"] = decision
    primary["required"] = True if decision == "REQUIRED" else False if decision == "NOT_REQUIRED" else None
    if decision == "NOT_REQUIRED":
        primary["family"] = "NO_PRIMARY_PERMIT"
        primary["status"] = "NOT_REQUIRED"
    companion_sources = required_rows[1:]
    companion_sources += [row for row in (value.get("related_permits") or []) if isinstance(row, dict)]
    companion_sources += [row for row in (value.get("companion_permits") or []) if isinstance(row, dict)]
    companions: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None, str | None]] = set()
    source_evidence = json.dumps(value.get("sources") or [], sort_keys=True, default=str).lower()
    explicit_no_building_requirement = bool(re.search(
        r"\b(?:does not have|no)\b[^.]{0,240}\bbuilding permit requirements?\b",
        source_evidence,
    ))
    for source in companion_sources:
        source = copy.deepcopy(source)
        if value.get("claim_citations") and not source.get("claim_citations"):
            source["claim_citations"] = copy.deepcopy(value.get("claim_citations"))
        row = _canonical_row(
            _merge_typed_family_truth(source, typed_truth),
            authenticated=authenticated_creation,
        )
        if not authenticated_creation and row.get("status") in {"REQUIRED", "NOT_REQUIRED"}:
            row["status"] = "VERIFY"
            row["decision"] = "VERIFY"
            row["verdict"] = "VERIFY"
            row["required_status"] = "VERIFY"
            row["required"] = None
            if not row.get("customer_guidance"):
                label = row.get("local_name") or str(row.get("family") or "permit").replace("_", " ").title()
                row["customer_guidance"] = f"Confirm the {label} requirement with the issuing authority before filing."
            row["reason"] = row.get("customer_guidance")
        if decision == "NOT_REQUIRED" and explicit_no_building_requirement and row.get("family") == "BUILDING":
            continue
        key = (row["family"], row["status"], row.get("source_ref"), row.get("local_name"))
        if key not in seen:
            seen.add(key)
            companions.append(row)
    jurisdiction = _jurisdiction_identity(value)
    out = copy.deepcopy(value)
    out["primary_permit_family"] = primary["family"]
    out["permit_kind"] = primary.get("kind") or primary.get("local_name") or _LEGACY_FAMILY_IDS.get(primary["family"], primary["family"].lower())
    out["permit_name"] = primary.get("local_name") or primary["family"].replace("_", " ").title()
    out["permit_type"] = primary.get("permit_type") or primary.get("local_name") or out["permit_name"]
    out["jurisdiction_identity"] = jurisdiction
    out["companion_permits"] = copy.deepcopy(companions)
    out["related_permits"] = copy.deepcopy([
        row for row in companions if row.get("status") != "REQUIRED"
    ])
    emit_required_rows = decision != "NOT_REQUIRED" and bool(
        required_rows
        or decision in {"REQUIRED", "CONDITIONAL", "NEEDS_INPUT"}
        or value.get("primary_permit_family")
        or value.get("permit_kind")
        or value.get("permit_name")
    )
    all_rows = [primary, *companions]
    # Historical customers read hard-required binary rows from this list.
    # Preserve nonbinary family-bearing rows only in typed/companion surfaces.
    out["permits_required"] = copy.deepcopy([
        row for row in all_rows if row.get("status") == "REQUIRED"
    ]) if emit_required_rows else []
    if decision == "NOT_REQUIRED":
        out["permits_required_logic"] = []
        out["required_permit_names"] = []
        out["required_permit_families"] = []
        out["required_permit_segments"] = []
        out["not_required_reason"] = out.get("not_required_reason") or primary.get("exemption") or "Permit not required for the resolved scope."
    # Keep one compatibility mirror for legacy consumers that historically used
    # lower-case `family` plus `verdict`; the manifest itself remains the only
    # canonical uppercase ontology authority.
    family_decisions = copy.deepcopy(all_rows)
    for row in family_decisions:
        row["canonical_family"] = row["family"]
        row["family"] = _LEGACY_FAMILY_IDS.get(
            str(row["canonical_family"]),
            str(row["canonical_family"]).lower(),
        )
        row["verdict"] = row["status"]
        if row.get("trigger") and not row.get("condition_text"):
            row["condition_text"] = row["trigger"]
    out["family_decisions"] = family_decisions
    out["related_permits"] = copy.deepcopy([
        row for row in family_decisions[1:] if row.get("status") != "REQUIRED"
    ])
    hard_rows = [row for row in family_decisions if row.get("status") == "REQUIRED"]
    related_rows = [row for row in family_decisions[1:] if row.get("status") != "REQUIRED"]
    out["required_permit_names"] = [row.get("local_name") or row.get("permit_type") for row in hard_rows]
    out["required_permit_families"] = [row.get("family") for row in hard_rows]
    out["required_permit_segments"] = list(dict.fromkeys(str(row.get("segment")).lower() for row in hard_rows if row.get("segment")))
    out["related_permit_names"] = [row.get("local_name") or row.get("permit_type") for row in related_rows]
    out["related_permit_families"] = [row.get("family") for row in related_rows]
    out["related_permit_segments"] = list(dict.fromkeys(str(row.get("segment")).lower() for row in related_rows if row.get("segment")))
    # Canonical Manifest status owns every binary compatibility mirror. This is
    # essential when caller-shaped REQUIRED/NOT_REQUIRED input is demoted.
    out["permit_decision"] = decision
    out["permit_required"] = True if decision == "REQUIRED" else False if decision == "NOT_REQUIRED" else None
    out["permit_verdict"] = "YES" if decision == "REQUIRED" else "NO" if decision == "NOT_REQUIRED" else decision
    out["permit_status"] = decision
    out["permit_manifest"] = _sign_manifest({
        "schema_version": MANIFEST_SCHEMA,
        "permit_decision": decision,
        "primary": primary,
        "companions": copy.deepcopy(companions),
        "jurisdiction": jurisdiction,
        "filing_destination": {
            "application_authority": primary.get("authority") or jurisdiction.get("application_authority"),
            "apply_url": primary.get("apply_url") or value.get("apply_url") or value.get("online_application_url"),
            "apply_path": copy.deepcopy(value.get("apply_path")) if isinstance(value.get("apply_path"), dict) else None,
        },
    })
    out["apply_url"] = out["permit_manifest"]["filing_destination"]["apply_url"]
    out["online_application_url"] = out["permit_manifest"]["filing_destination"]["apply_url"]
    return out


def permit_manifest_mode_enabled() -> bool:
    return str(os.environ.get(MANIFEST_FLAG, "")).strip().lower() in {
        "1", "true", "yes", "on", "shadow", "active",
    }


def _build_manifest_projection(
    value: dict[str, Any], *, force: bool, allow_create: bool,
    authority_input: object | None = None,
) -> dict[str, Any]:
    """Project authenticated Manifests, optionally sealing trusted authority.

    Permit-shaped dictionaries also occur inside summaries and row collections.
    Only the root and named result containers are authority boundaries.
    """
    original = copy.deepcopy(value) if isinstance(value, dict) else {}
    if not force and not permit_manifest_mode_enabled():
        return original

    result_container_keys = frozenset({
        "data", "result", "response", "customer", "customer_result",
        "permit_result", "share_data", "results",
    })

    authority_type = type(authority_input)
    trusted_authority_types = tuple(
        candidate
        for module_name in ("api.permit_model", "permit_model")
        for candidate in (getattr(sys.modules.get(module_name), "PermitAuthorityInput", None),)
        if isinstance(candidate, type)
    )
    authenticated_creation = bool(
        authority_type in trusted_authority_types
        and getattr(authority_input, "_authenticated_provenance", False) is True
    )

    def walk(item: Any, path: tuple[str, ...] = ()) -> Any:
        if isinstance(item, dict):
            mapped = {key: walk(child, (*path, str(key))) for key, child in item.items()}
            is_boundary = not path or path[-1] in result_container_keys
            if is_boundary and _looks_like_permit_result(mapped):
                return _project_one(
                    mapped,
                    allow_create=allow_create,
                    authenticated_creation=authenticated_creation,
                )
            return mapped
        if isinstance(item, list):
            return [walk(child, path) for child in item]
        if isinstance(item, tuple):
            return [walk(child, path) for child in item]
        return copy.deepcopy(item)

    projected = walk(original)
    return projected if isinstance(projected, dict) else {}


def build_permit_manifest_projection(value: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """Sanitize/re-project authenticated authority; never create authority."""
    return _build_manifest_projection(value, force=force, allow_create=False)


def seal_permit_manifest_projection(
    value: dict[str, Any], *, force: bool = False, authority_input: object | None = None
) -> dict[str, Any]:
    """Private server authority lane: create and HMAC-seal a canonical Manifest."""
    return _build_manifest_projection(
        value, force=force, allow_create=True, authority_input=authority_input
    )
