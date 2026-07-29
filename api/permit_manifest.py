"""Flag-gated canonical Permit Manifest projection.

Session 2 consolidates existing typed customer truth at the final egress boundary.
This module is deliberately pure and IO-free: it never resolves a jurisdiction,
changes a permit decision, invents a permit family, or adds a companion. It only
canonicalizes fields already present in the customer DTO. Unsourced heuristic
companions remain visible but are demoted to VERIFY.
"""
from __future__ import annotations

import copy
import os
import re
from typing import Any

MANIFEST_FLAG = "PERMITASSIST_PERMIT_MANIFEST_MODE"
MANIFEST_SCHEMA = "permit_manifest_v1"
ONTOLOGY_SOURCE_SHA256 = "9b5e6748786c890ea6138871a49180c9531283c127c56bf036f23c48741601c8"

FAMILIES = frozenset({
    "NO_PRIMARY_PERMIT", "BUILDING", "ROOFING", "ELECTRICAL", "PLUMBING",
    "MECHANICAL", "REFRIGERATION", "GAS", "FIRE_LIFE_SAFETY",
    "ZONING_PLANNING", "OCCUPANCY_CO", "DEMOLITION", "SIGN", "POOL_SPA",
    "MOVING", "LANDMARKS_HISTORIC", "HEALTH", "GRADING_SITE_CIVIL_ROW",
    "WASTEWATER_FOG", "ENVIRONMENTAL", "LIQUOR", "MANUFACTURED_STRUCTURE",
    "TRADE_OR_SUBPERMIT_REVIEW", "ACCESSIBILITY", "UTILITY", "OTHER", "VERIFY",
})
STATUSES = frozenset({"REQUIRED", "CONDITIONAL", "NOT_REQUIRED", "NEEDS_INPUT", "VERIFY"})

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


def _source_refs(row: dict[str, Any]) -> list[str]:
    """Collect all explicit public source references without copying opaque row state."""
    refs: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            candidate = value.strip()
            if candidate not in refs:
                refs.append(candidate)

    existing_refs = row.get("source_refs")
    if isinstance(existing_refs, list):
        for ref in existing_refs:
            add(ref)
    for key in ("source_ref", "source_url", "evidence_ref", "provenance_ref"):
        add(row.get(key))
    for key in ("source", "evidence", "sources", "provenance", "application_route"):
        nested = row.get(key)
        items = nested if isinstance(nested, list) else [nested]
        for item in items:
            if isinstance(item, str):
                add(item)
            elif isinstance(item, dict):
                for nested_key in ("source_ref", "url", "source_url", "evidence_ref", "provenance_ref", "id"):
                    add(item.get(nested_key))
                provenance = item.get("provenance")
                if isinstance(provenance, list):
                    for provenance_item in provenance:
                        if isinstance(provenance_item, dict):
                            for nested_key in ("source_ref", "url", "source_url", "evidence_ref"):
                                add(provenance_item.get(nested_key))
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


def _canonical_row(row: dict[str, Any], *, primary: bool = False) -> dict[str, Any]:
    source_refs = _source_refs(row)
    source_ref = source_refs[0] if source_refs else _source_ref(row)
    status = _status_from_row(row, default="REQUIRED" if primary and row.get("required") is True else "VERIFY")
    if not primary and status in {"REQUIRED", "CONDITIONAL"} and not source_ref:
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
    if status == "VERIFY" and not guidance:
        label = (local_name or _family_from_row(row).replace("_", " ").title()).strip()
        guidance = f"Confirm the {label} requirement with the issuing authority before filing."
    result = {
        "family": _family_from_row(row),
        "local_name": local_name,
        "status": status,
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
        "required": True if status == "REQUIRED" else False if status == "NOT_REQUIRED" else None,
    }
    if source_ref and source_ref.lower().startswith(("http://", "https://")):
        result["source_url"] = source_ref
    return result


def _jurisdiction_identity(value: dict[str, Any]) -> dict[str, Any]:
    summary = value.get("customer_result_summary")
    summary = summary if isinstance(summary, dict) else {}
    nested = summary.get("jurisdiction")
    nested_dict = nested if isinstance(nested, dict) else {}
    nested_name = nested if isinstance(nested, str) and nested.strip() else None
    return {
        "jurisdiction_id": _first_text(nested_dict, ("jurisdiction_id", "id", "ahj_id")) or _first_text(value, ("jurisdiction_id", "ahj_id")),
        "name": _first_text(nested_dict, ("name", "jurisdiction_name", "ahj_name")) or nested_name or _first_text(value, ("jurisdiction_name", "ahj_name")),
        "type": _first_text(nested_dict, ("type", "jurisdiction_type", "ahj_type")) or _first_text(value, ("jurisdiction_type", "ahj_type")),
        "city": _first_text(nested_dict, ("city",)) or _first_text(value, ("city",)),
        "state": _first_text(nested_dict, ("state",)) or _first_text(value, ("state",)),
        "authority_model": _first_text(nested_dict, ("authority_model",)) or _first_text(value, ("authority_model",)),
        "issuing_authority": _first_text(value, ("issuing_authority", "applying_office", "building_dept_name")),
        "application_authority": _first_text(value, ("application_authority", "applying_office", "building_dept_name")),
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


def _project_one(value: dict[str, Any]) -> dict[str, Any]:
    # A valid manifest is the immutable source for re-entry. Do not derive a
    # second copy from its backward-compatible mirrors.
    manifest = value.get("permit_manifest")
    if isinstance(manifest, dict) and manifest.get("schema_version") == MANIFEST_SCHEMA:
        without_manifest = copy.deepcopy(value)
        without_manifest.pop("permit_manifest", None)
        rebuilt = _project_one(without_manifest)
        regulated_mirrors = (
            "permit_manifest", "primary_permit_family", "jurisdiction_identity",
            "companion_permits", "permits_required", "family_decisions",
            "permit_kind", "permit_name",
        )
        if all(value.get(key) == rebuilt.get(key) for key in regulated_mirrors):
            return value
        # The projection is forbidden to change permit_decision. Fail closed by
        # rejecting the stale derived manifest and deterministically rebuilding
        # it from the current trusted typed DTO rather than trusting contradictions.
        return rebuilt
    decision = str(value.get("permit_decision") or "").upper().strip()
    required_rows = [row for row in (value.get("permits_required") or []) if isinstance(row, dict)]
    primary_source = required_rows[0] if required_rows else {
        "primary_permit_family": value.get("primary_permit_family"),
        "permit_kind": value.get("permit_kind"),
        "permit_name": value.get("permit_name"),
        "status": decision,
        "required": value.get("permit_required"),
    }
    typed_truth = _typed_family_truth(value)
    primary = _canonical_row(_merge_typed_family_truth(primary_source, typed_truth), primary=True)
    if decision == "NOT_REQUIRED":
        primary["family"] = "NO_PRIMARY_PERMIT"
        primary["status"] = "NOT_REQUIRED"
    companion_sources = required_rows[1:]
    companion_sources += [row for row in (value.get("related_permits") or []) if isinstance(row, dict)]
    companion_sources += [row for row in (value.get("companion_permits") or []) if isinstance(row, dict)]
    companions: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None, str | None]] = set()
    for source in companion_sources:
        row = _canonical_row(_merge_typed_family_truth(source, typed_truth))
        key = (row["family"], row["status"], row.get("source_ref"), row.get("local_name"))
        if key not in seen:
            seen.add(key)
            companions.append(row)
    jurisdiction = _jurisdiction_identity(value)
    out = copy.deepcopy(value)
    out["primary_permit_family"] = primary["family"]
    out["permit_kind"] = primary["family"]
    out["permit_name"] = primary.get("local_name") or primary["family"].replace("_", " ").title()
    out["jurisdiction_identity"] = jurisdiction
    out["companion_permits"] = copy.deepcopy(companions)
    out["permits_required"] = copy.deepcopy([primary, *companions]) if decision != "NOT_REQUIRED" else []
    out["family_decisions"] = copy.deepcopy([primary, *companions])
    out["permit_manifest"] = {
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
    }
    return out


def permit_manifest_mode_enabled() -> bool:
    return str(os.environ.get(MANIFEST_FLAG, "")).strip().lower() in {
        "1", "true", "yes", "on", "shadow", "active",
    }


def build_permit_manifest_projection(value: dict[str, Any]) -> dict[str, Any]:
    """Return the flag-gated, recursive, idempotent egress projection."""
    original = copy.deepcopy(value) if isinstance(value, dict) else {}
    if not permit_manifest_mode_enabled():
        return original

    def walk(item: Any) -> Any:
        if isinstance(item, dict):
            mapped = {key: walk(child) for key, child in item.items()}
            return _project_one(mapped) if _looks_like_permit_result(mapped) else mapped
        if isinstance(item, list):
            return [walk(child) for child in item]
        if isinstance(item, tuple):
            return [walk(child) for child in item]
        return copy.deepcopy(item)

    projected = walk(original)
    return projected if isinstance(projected, dict) else {}
