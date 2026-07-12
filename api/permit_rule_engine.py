"""Typed Permit Rule Engine contracts and a side-effect-isolated shadow adapter.

Part 1 intentionally does not replace the legacy PermitAssist decision path.  It
turns the shipped v2.4 Decision Cells into deterministic, versioned envelopes
that can be measured beside the legacy result.  The adapter is disabled by
default and its return value is never merged into customer or cache payloads.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from .v24_decision_cells import (
        DEFAULT_V24_MANIFEST_PATH,
        V24Resolution,
        V24ResolutionStatus,
        load_v24_index,
        resolve_v24_cell,
        validate_v24_cell,
    )
except ImportError:  # server.py imports research_engine as a top-level module
    from v24_decision_cells import (
        DEFAULT_V24_MANIFEST_PATH,
        V24Resolution,
        V24ResolutionStatus,
        load_v24_index,
        resolve_v24_cell,
        validate_v24_cell,
    )

RULE_ENGINE_SCHEMA_VERSION = "permitassist.rule-engine.v1"
AUTHORITY_CONTEXT_VERSION = "permitassist.authority-context.v1"
WORK_ATOM_VERSION = "permitassist.work-atom.v1"
FACT_PROFILE_VERSION = "permitassist.fact-profile.v1"
DECISION_ENVELOPE_VERSION = "permitassist.decision-envelope.v1"
SHADOW_EVENT_VERSION = "permitassist.rule-engine-shadow-event.v1"
ADAPTER_VERSION = "permitassist.rule-engine-shadow-adapter.v1"
DIVERGENCE_TAXONOMY_VERSION = "permitassist.divergence-taxonomy.v1"
SHADOW_SETTING = "PERMITASSIST_RULE_ENGINE_SHADOW"
SHADOW_LOG_SETTING = "PERMITASSIST_RULE_ENGINE_SHADOW_LOG"

# A generic scope can only be exact-complete when the cell closes every family
# that the scope can activate. Commercial TI uses the locked ten-lane W4 filing
# packet boundary (including explicit NOT_REQUIRED closure where a lane does not
# apply). This is deliberately stricter than the legacy v2.4 TIER1_COMPLETE label,
# which often means only a source-backed Building row.
FAMILY_CLOSURE_REQUIREMENTS: dict[str, frozenset[str]] = {
    "commercial_tenant_improvement": frozenset(
        {
            "building",
            "electrical",
            "plumbing",
            "mechanical",
            "fire",
            "health",
            "liquor",
            "wastewater",
            "occupancy",
            "zoning",
        }
    ),
    "residential_remodel": frozenset({"building", "electrical", "mechanical", "plumbing"}),
    "reroof": frozenset({"building"}),
}

UNKNOWN_FACT_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "commercial_tenant_improvement": (
        "change_of_use_or_occupancy",
        "electrical_scope",
        "fire_life_safety_scope",
        "mechanical_or_gas_scope",
        "plumbing_scope",
        "zoning_or_planning_trigger",
    ),
    "residential_remodel": (
        "electrical_scope",
        "mechanical_or_gas_scope",
        "plumbing_scope",
        "structural_scope",
    ),
    "reroof": ("structural_deck_change",),
}

_KIND_ALIASES = {
    "building_construction": "building",
    "construction": "building",
    "hvac": "mechanical",
    "gas": "mechanical",
    "fire_alarm": "fire",
    "fire_sprinkler": "fire",
    "planning": "zoning",
    "co": "occupancy",
}


class CoverageStatus(str, Enum):
    EXACT_COMPLETE = "EXACT_COMPLETE"
    EXACT_PARTIAL = "EXACT_PARTIAL"
    FAIL_CLOSED = "FAIL_CLOSED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    AHJ_COVERED_SCOPE_UNSUPPORTED = "AHJ_COVERED_SCOPE_UNSUPPORTED"
    AHJ_UNCOVERED = "AHJ_UNCOVERED"
    JURISDICTION_AMBIGUOUS = "JURISDICTION_AMBIGUOUS"
    INDEX_UNAVAILABLE = "INDEX_UNAVAILABLE"


class FamilyVerdict(str, Enum):
    REQUIRED = "REQUIRED"
    NOT_REQUIRED = "NOT_REQUIRED"
    CONDITIONAL = "CONDITIONAL"
    VERIFY = "VERIFY"
    ABSTAIN = "ABSTAIN"


class DivergenceCode(str, Enum):
    # Locked Part 1 taxonomy.  These names are stable external evidence keys.
    AHJ_BOUNDARY_MISMATCH = "AHJ_BOUNDARY_MISMATCH"
    SCOPE_TAXONOMY_UNSUPPORTED = "SCOPE_TAXONOMY_UNSUPPORTED"
    PROJECT_FAMILY_NOT_COVERED = "PROJECT_FAMILY_NOT_COVERED"
    RULE_OR_EXEMPTION_MISSING = "RULE_OR_EXEMPTION_MISSING"
    COMPANION_CLOSURE_INCOMPLETE = "COMPANION_CLOSURE_INCOMPLETE"
    AUTHORITATIVE_CELL_NOT_INJECTED = "AUTHORITATIVE_CELL_NOT_INJECTED"
    MODEL_OR_GENERIC_FALLBACK_GUESS = "MODEL_OR_GENERIC_FALLBACK_GUESS"
    POST_RECONCILIATION_MUTATION = "POST_RECONCILIATION_MUTATION"
    PUBLIC_RENDER_DIVERGENCE = "PUBLIC_RENDER_DIVERGENCE"
    STALE_OR_CONFLICTING_RULE = "STALE_OR_CONFLICTING_RULE"

    # Deterministic lower-level diagnostics retained beneath the locked classes.
    MATCH = "MATCH"
    COVERAGE_ONLY = "COVERAGE_ONLY"
    LEGACY_RESULT_MISSING = "LEGACY_RESULT_MISSING"
    DECISION_MISMATCH = "DECISION_MISMATCH"
    FAIL_CLOSED_VS_LEGACY_BINARY = "FAIL_CLOSED_VS_LEGACY_BINARY"
    FAMILY_MISSING_IN_LEGACY = "FAMILY_MISSING_IN_LEGACY"
    FAMILY_EXTRA_IN_LEGACY = "FAMILY_EXTRA_IN_LEGACY"
    FAMILY_VERDICT_MISMATCH = "FAMILY_VERDICT_MISMATCH"
    AUTHORITY_MISMATCH = "AUTHORITY_MISMATCH"
    APPLY_ROUTE_MISMATCH = "APPLY_ROUTE_MISMATCH"
    LEGACY_PROVENANCE_GAP = "LEGACY_PROVENANCE_GAP"


@dataclass(frozen=True)
class ProvenanceRecord:
    source_url: str
    source_quote: str
    retrieved_at: str
    snapshot_hash: str
    snapshot_path: str
    effective_date: str | None
    freshness_class: str
    last_verified_at: str
    publishable: bool


@dataclass(frozen=True)
class AuthorityRef:
    family: str
    issuing_authority: str
    application_authority: str
    authority_tier: str
    handled_by_local_ahj: bool | None


@dataclass(frozen=True)
class ApplicationRoute:
    permit_name: str
    office_name: str
    apply_url: str
    channel: str
    provenance: tuple[ProvenanceRecord, ...]


@dataclass(frozen=True)
class AuthorityContext:
    schema_version: str
    jurisdiction_id: str
    ahj_name: str
    state: str
    county: str | None
    jurisdiction_match: str
    family_authorities: tuple[AuthorityRef, ...]


@dataclass(frozen=True)
class WorkAtom:
    schema_version: str
    atom_id: str
    raw_scope: str
    project_family: str
    subtype: str
    applicability: str
    fact_dependencies: tuple[str, ...]


@dataclass(frozen=True)
class FactProfile:
    schema_version: str
    job_category: str
    known_facts: tuple[str, ...]
    unknown_dimensions: tuple[str, ...]
    fingerprint_sha256: str


@dataclass(frozen=True)
class FamilyDecision:
    family: str
    verdict: FamilyVerdict
    trigger: str
    authority: AuthorityRef | None
    provenance: tuple[ProvenanceRecord, ...]


@dataclass(frozen=True)
class DecisionEnvelope:
    schema_version: str
    adapter_version: str
    request_fingerprint_sha256: str
    source_cell_id: str | None
    source_index_key: str | None
    source_package_manifest_sha256: str | None
    coverage_status: CoverageStatus
    coverage_reason: str
    main_verdict: FamilyVerdict
    authority_context: AuthorityContext
    work_atoms: tuple[WorkAtom, ...]
    fact_profile: FactProfile
    family_decisions: tuple[FamilyDecision, ...]
    application_routes: tuple[ApplicationRoute, ...]
    validation_ok: bool | None
    validation_issue_codes: tuple[str, ...]
    source_set_sha256: str


@dataclass(frozen=True)
class Divergence:
    code: DivergenceCode
    family: str | None
    legacy_value: str | None
    envelope_value: str | None
    detail: str


@dataclass(frozen=True)
class ShadowObservation:
    schema_version: str
    taxonomy_version: str
    adapter_version: str
    request_fingerprint_sha256: str
    envelope_sha256: str
    source_cell_id: str | None
    coverage_status: CoverageStatus
    divergences: tuple[Divergence, ...]


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _normalize_text(value).lower()).strip("_")


def _v24_has_exact_ahj(city: str, state: str) -> bool:
    """Distinguish a covered AHJ with unsupported scope from an uncovered AHJ."""
    index = load_v24_index()
    if not index:
        return False
    state_key = _normalize_text(state).upper()
    city_key = _slug(city)
    return any(
        _normalize_text(cell.get("state")).upper() == state_key
        and _slug(cell.get("ahj")) == city_key
        for cell in index.values()
    )


def normalize_family(value: Any) -> str:
    family = _slug(value)
    return _KIND_ALIASES.get(family, family)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def response_json_bytes(value: Any) -> bytes:
    """Serialize in insertion order so key-order drift fails parity checks."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def stable_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _source_package_manifest_sha256(resolution: V24Resolution) -> str | None:
    """Pin the source package even though the legacy resolver omits the field."""
    if resolution.package_manifest_sha256:
        return resolution.package_manifest_sha256
    manifest_path = Path(
        os.environ.get("PERMITASSIST_V24_MANIFEST_PATH") or DEFAULT_V24_MANIFEST_PATH
    )
    try:
        return hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    except OSError:
        return None


def to_primitive(value: Any) -> Any:
    """Convert nested dataclasses/enums/tuples to canonical JSON primitives."""
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_primitive(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): to_primitive(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_primitive(item) for item in value]
    return value


def get_rule_engine_shadow_mode() -> str:
    value = str(os.environ.get(SHADOW_SETTING, "off") or "off").strip().lower()
    return "shadow" if value in {"1", "true", "on", "shadow"} else "off"


def _provenance_from_dict(value: Any) -> ProvenanceRecord | None:
    if not isinstance(value, Mapping):
        return None
    return ProvenanceRecord(
        source_url=_normalize_text(value.get("source_url")),
        source_quote=_normalize_text(value.get("source_quote")),
        retrieved_at=_normalize_text(value.get("retrieved_at")),
        snapshot_hash=_normalize_text(value.get("snapshot_hash")).lower(),
        snapshot_path=_normalize_text(value.get("snapshot_path")),
        effective_date=_normalize_text(value.get("effective_date")) or None,
        freshness_class=_normalize_text(value.get("freshness_class")),
        last_verified_at=_normalize_text(value.get("last_verified_at")),
        publishable=value.get("publishable") is True,
    )


def _dedupe_sorted_provenance(values: Iterable[ProvenanceRecord | None]) -> tuple[ProvenanceRecord, ...]:
    unique: dict[str, ProvenanceRecord] = {}
    for value in values:
        if value is not None:
            unique[stable_sha256(to_primitive(value))] = value
    return tuple(unique[key] for key in sorted(unique))


def _authority_rows(cell: Mapping[str, Any]) -> dict[str, AuthorityRef]:
    tier1 = _mapping(cell.get("tier1"))
    rows = _list(tier1.get("trade_authority"))
    authorities: dict[str, AuthorityRef] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        family = normalize_family(row.get("trade"))
        issuing = _mapping(row.get("issuing_authority"))
        applying = _mapping(row.get("application_authority"))
        if not family:
            continue
        authorities[family] = AuthorityRef(
            family=family,
            issuing_authority=_normalize_text(issuing.get("name")),
            application_authority=_normalize_text(applying.get("name")),
            authority_tier=_slug(issuing.get("tier")) or "unknown",
            handled_by_local_ahj=(
                bool(row.get("handled_by_local_ahj"))
                if isinstance(row.get("handled_by_local_ahj"), bool)
                else None
            ),
        )
    return authorities


def _request_fingerprint(job_type: str, city: str, state: str, job_category: str) -> str:
    return stable_sha256(
        {
            "job_type": _normalize_text(job_type).lower(),
            "city": _normalize_text(city).lower(),
            "state": _normalize_text(state).upper(),
            "job_category": _normalize_text(job_category).lower(),
        }
    )


def _fact_profile(job_type: str, job_category: str, project_family: str) -> FactProfile:
    known = tuple(
        sorted(
            item
            for item in (
                f"job_category={_slug(job_category)}" if _slug(job_category) else "",
                f"project_family={project_family}" if project_family else "",
                f"scope_slug={_slug(job_type)}" if _slug(job_type) else "",
            )
            if item
        )
    )
    unknown = UNKNOWN_FACT_DIMENSIONS.get(project_family, ("project_scope_not_canonicalized",))
    fingerprint = stable_sha256({"known_facts": known, "unknown_dimensions": unknown})
    return FactProfile(
        schema_version=FACT_PROFILE_VERSION,
        job_category=_slug(job_category) or "unknown",
        known_facts=known,
        unknown_dimensions=tuple(unknown),
        fingerprint_sha256=fingerprint,
    )


def _verdict_from_required_status(value: Any) -> FamilyVerdict:
    status = _slug(value)
    if status == "required":
        return FamilyVerdict.REQUIRED
    if status == "not_required":
        return FamilyVerdict.NOT_REQUIRED
    if status == "conditional":
        return FamilyVerdict.CONDITIONAL
    return FamilyVerdict.VERIFY


def _main_verdict(cell: Mapping[str, Any]) -> FamilyVerdict:
    tier1 = _mapping(cell.get("tier1"))
    main = _mapping(tier1.get("main_decision"))
    value = _slug(main.get("value"))
    if value == "required":
        return FamilyVerdict.REQUIRED
    if value == "not_required":
        return FamilyVerdict.NOT_REQUIRED
    return FamilyVerdict.ABSTAIN


def request_time_validation(cell: Mapping[str, Any]) -> tuple[bool, tuple[str, ...]]:
    """Run the same portable validation used by the request-time v2.4 resolver."""
    result = validate_v24_cell(
        dict(cell),
        strict_snapshots=False,
        require_live_url_check=False,
    )
    codes = tuple(sorted({issue.code for issue in result.issues}))
    return result.ok, codes


def _has_source_provenance(row: Mapping[str, Any]) -> bool:
    provenance = row.get("provenance")
    if isinstance(provenance, Mapping):
        return bool(_normalize_text(provenance.get("source_url")))
    if isinstance(provenance, list):
        return any(
            isinstance(item, Mapping) and bool(_normalize_text(item.get("source_url")))
            for item in provenance
        )
    return False


def classify_cell_coverage(
    cell: Mapping[str, Any],
) -> tuple[CoverageStatus, str, bool, tuple[str, ...]]:
    """Classify a shipped cell without inflating legacy TIER1_COMPLETE claims."""
    if cell.get("status") == "FAIL_CLOSED" or cell.get("serving_status") == "FAIL_CLOSED":
        ok, issue_codes = request_time_validation(cell)
        return CoverageStatus.FAIL_CLOSED, "cell explicitly fail-closed", ok, issue_codes

    ok, issue_codes = request_time_validation(cell)
    if not ok:
        return CoverageStatus.VALIDATION_FAILED, "portable request-time validation failed", ok, issue_codes

    project_family = normalize_family(cell.get("project_family"))
    tier1 = _mapping(cell.get("tier1"))
    permits_value = tier1.get("permits_required")
    authority_value = tier1.get("trade_authority")
    apply_value = tier1.get("apply")
    permits: list[Any] = permits_value if isinstance(permits_value, list) else []
    trade_authority: list[Any] = authority_value if isinstance(authority_value, list) else []
    apply_rows: list[Any] = apply_value if isinstance(apply_value, list) else []

    permit_by_family: dict[str, list[Mapping[str, Any]]] = {}
    for row in permits:
        if isinstance(row, Mapping):
            family = normalize_family(row.get("permit_kind"))
            if family:
                permit_by_family.setdefault(family, []).append(row)
    authority_by_family: dict[str, list[Mapping[str, Any]]] = {}
    for row in trade_authority:
        if isinstance(row, Mapping):
            family = normalize_family(row.get("trade"))
            if family:
                authority_by_family.setdefault(family, []).append(row)

    required = FAMILY_CLOSURE_REQUIREMENTS.get(project_family, frozenset({"building"}))
    missing_families = sorted(required - set(permit_by_family))
    missing_authorities = sorted(required - set(authority_by_family))
    missing_provenance = sorted(
        family
        for family in required
        if not any(_has_source_provenance(row) for row in permit_by_family.get(family, []))
        and not any(_has_source_provenance(row) for row in authority_by_family.get(family, []))
    )
    route_complete = any(
        isinstance(row, Mapping)
        and bool(_normalize_text(row.get("office_name")))
        and bool(_normalize_text(row.get("apply_url")))
        and _has_source_provenance(row)
        for row in apply_rows
    )

    gaps: list[str] = []
    if missing_families:
        gaps.append("family closure=" + ",".join(missing_families))
    if missing_authorities:
        gaps.append("authority closure=" + ",".join(missing_authorities))
    if missing_provenance:
        gaps.append("provenance closure=" + ",".join(missing_provenance))
    if not route_complete:
        gaps.append("source-backed application route")
    if gaps:
        return (
            CoverageStatus.EXACT_PARTIAL,
            "source-backed exact AHJ/project row is incomplete: " + "; ".join(gaps),
            ok,
            issue_codes,
        )
    return (
        CoverageStatus.EXACT_COMPLETE,
        "source-backed exact AHJ/project row closes family, authority, provenance, and routing requirements",
        ok,
        issue_codes,
    )


def _empty_authority_context(city: str, state: str, match: str) -> AuthorityContext:
    return AuthorityContext(
        schema_version=AUTHORITY_CONTEXT_VERSION,
        jurisdiction_id="",
        ahj_name=_normalize_text(city),
        state=_normalize_text(state).upper(),
        county=None,
        jurisdiction_match=match,
        family_authorities=(),
    )


def _empty_envelope(
    *,
    job_type: str,
    city: str,
    state: str,
    job_category: str,
    resolution: V24Resolution,
    coverage: CoverageStatus,
) -> DecisionEnvelope:
    project_family = resolution.project_candidates[0] if resolution.project_candidates else "unsupported"
    fact_profile = _fact_profile(job_type, job_category, project_family)
    atom = WorkAtom(
        schema_version=WORK_ATOM_VERSION,
        atom_id=stable_sha256({"scope": _slug(job_type), "family": project_family})[:20],
        raw_scope=_normalize_text(job_type),
        project_family=project_family,
        subtype=_slug(job_type) or "unknown",
        applicability="unresolved",
        fact_dependencies=fact_profile.unknown_dimensions,
    )
    return DecisionEnvelope(
        schema_version=DECISION_ENVELOPE_VERSION,
        adapter_version=ADAPTER_VERSION,
        request_fingerprint_sha256=_request_fingerprint(job_type, city, state, job_category),
        source_cell_id=None,
        source_index_key=resolution.key,
        source_package_manifest_sha256=_source_package_manifest_sha256(resolution),
        coverage_status=coverage,
        coverage_reason=resolution.reason,
        main_verdict=FamilyVerdict.ABSTAIN,
        authority_context=_empty_authority_context(city, state, coverage.value.lower()),
        work_atoms=(atom,),
        fact_profile=fact_profile,
        family_decisions=(),
        application_routes=(),
        validation_ok=None,
        validation_issue_codes=(),
        source_set_sha256=stable_sha256([]),
    )


def build_decision_envelope(
    resolution: V24Resolution,
    *,
    job_type: str,
    city: str,
    state: str,
    job_category: str = "",
) -> DecisionEnvelope:
    """Build a deterministic envelope from a v2.4 resolution."""
    if not isinstance(resolution, V24Resolution):
        raise TypeError("resolution must be V24Resolution")
    if not isinstance(resolution.cell, dict):
        status_map = {
            V24ResolutionStatus.AHJ_COVERED_PROJECT_NOT_COVERED: CoverageStatus.AHJ_COVERED_SCOPE_UNSUPPORTED,
            V24ResolutionStatus.AHJ_NOT_COVERED: (
                CoverageStatus.AHJ_COVERED_SCOPE_UNSUPPORTED
                if _v24_has_exact_ahj(city, state)
                else CoverageStatus.AHJ_UNCOVERED
            ),
            V24ResolutionStatus.AMBIGUOUS_ABSTAIN: (
                CoverageStatus.AHJ_COVERED_SCOPE_UNSUPPORTED
                if _v24_has_exact_ahj(city, state)
                else CoverageStatus.JURISDICTION_AMBIGUOUS
            ),
            V24ResolutionStatus.INDEX_UNAVAILABLE: CoverageStatus.INDEX_UNAVAILABLE,
            V24ResolutionStatus.VALIDATION_FAILED_DEMOTED: CoverageStatus.VALIDATION_FAILED,
        }
        return _empty_envelope(
            job_type=job_type,
            city=city,
            state=state,
            job_category=job_category,
            resolution=resolution,
            coverage=status_map.get(resolution.status, CoverageStatus.INDEX_UNAVAILABLE),
        )

    cell = copy.deepcopy(resolution.cell)
    coverage, coverage_reason, validation_ok, issue_codes = classify_cell_coverage(cell)
    project_family = normalize_family(cell.get("project_family"))
    authorities = _authority_rows(cell)
    tier1 = _mapping(cell.get("tier1"))
    permits = _list(tier1.get("permits_required"))
    trade_rows = _list(tier1.get("trade_authority"))
    trade_provenance: dict[str, list[ProvenanceRecord | None]] = {}
    for row in trade_rows:
        if isinstance(row, Mapping):
            family = normalize_family(row.get("trade"))
            trade_provenance.setdefault(family, []).append(_provenance_from_dict(row.get("provenance")))

    family_decisions: list[FamilyDecision] = []
    for row in permits:
        if not isinstance(row, Mapping):
            continue
        family = normalize_family(row.get("permit_kind")) or "unknown"
        provenance = _dedupe_sorted_provenance(
            [_provenance_from_dict(row.get("provenance")), *trade_provenance.get(family, [])]
        )
        family_decisions.append(
            FamilyDecision(
                family=family,
                verdict=_verdict_from_required_status(row.get("required_status")),
                trigger=_normalize_text(row.get("trigger")),
                authority=authorities.get(family),
                provenance=provenance,
            )
        )
    family_decisions.sort(key=lambda item: (item.family, item.verdict.value, item.trigger))

    all_provenance: list[ProvenanceRecord | None] = []
    main = _mapping(tier1.get("main_decision"))
    all_provenance.append(_provenance_from_dict(main.get("provenance")))
    for decision in family_decisions:
        all_provenance.extend(decision.provenance)
    apply_rows = _list(tier1.get("apply"))
    application_routes: list[ApplicationRoute] = []
    for row in apply_rows:
        if isinstance(row, Mapping):
            route_provenance = _dedupe_sorted_provenance([_provenance_from_dict(row.get("provenance"))])
            all_provenance.extend(route_provenance)
            application_routes.append(
                ApplicationRoute(
                    permit_name=_normalize_text(row.get("permit_name")),
                    office_name=_normalize_text(row.get("office_name")),
                    apply_url=_normalize_text(row.get("apply_url")),
                    channel=_slug(row.get("channel")) or "unknown",
                    provenance=route_provenance,
                )
            )
    application_routes.sort(key=lambda item: (item.permit_name, item.office_name, item.apply_url))
    source_set = _dedupe_sorted_provenance(all_provenance)

    authority_context = AuthorityContext(
        schema_version=AUTHORITY_CONTEXT_VERSION,
        jurisdiction_id=_normalize_text(cell.get("jurisdiction_id")),
        ahj_name=_normalize_text(cell.get("ahj")) or _normalize_text(city),
        state=_normalize_text(cell.get("state") or state).upper(),
        county=_normalize_text(cell.get("county")) or None,
        jurisdiction_match="exact_v24_ahj_project_key",
        family_authorities=tuple(authorities[key] for key in sorted(authorities)),
    )
    fact_profile = _fact_profile(job_type, job_category, project_family)
    atom = WorkAtom(
        schema_version=WORK_ATOM_VERSION,
        atom_id=stable_sha256(
            {"scope": _slug(job_type), "family": project_family, "facts": fact_profile.fingerprint_sha256}
        )[:20],
        raw_scope=_normalize_text(job_type),
        project_family=project_family,
        subtype=_slug(job_type) or project_family,
        applicability="request_candidate",
        fact_dependencies=fact_profile.unknown_dimensions,
    )
    return DecisionEnvelope(
        schema_version=DECISION_ENVELOPE_VERSION,
        adapter_version=ADAPTER_VERSION,
        request_fingerprint_sha256=_request_fingerprint(job_type, city, state, job_category),
        source_cell_id=_normalize_text(cell.get("cell_id")) or None,
        source_index_key=resolution.key,
        source_package_manifest_sha256=_source_package_manifest_sha256(resolution),
        coverage_status=coverage,
        coverage_reason=coverage_reason,
        main_verdict=_main_verdict(cell),
        authority_context=authority_context,
        work_atoms=(atom,),
        fact_profile=fact_profile,
        family_decisions=tuple(family_decisions),
        application_routes=tuple(application_routes),
        validation_ok=validation_ok,
        validation_issue_codes=issue_codes,
        source_set_sha256=stable_sha256([to_primitive(item) for item in source_set]),
    )


def prepare_permit_rule_engine_shadow(
    job_type: str,
    city: str,
    state: str,
    job_category: str = "",
) -> DecisionEnvelope | None:
    """Return a shadow envelope only when the independent setting is enabled.

    `force=True` reads the v2.4 package without enabling v2.4 prompt injection or
    reconciliation.  This keeps the legacy path byte-stable even when shadowing.
    """
    if get_rule_engine_shadow_mode() != "shadow":
        return None
    resolution = resolve_v24_cell(city, state, job_type, job_category, force=True)
    return build_decision_envelope(
        resolution,
        job_type=job_type,
        city=city,
        state=state,
        job_category=job_category,
    )


def _legacy_main_verdict(result: Mapping[str, Any]) -> FamilyVerdict:
    decision = _slug(result.get("permit_decision"))
    verdict = _slug(result.get("permit_verdict"))
    required = result.get("permit_required")
    if decision == "required" or verdict in {"yes", "required"} or required is True:
        return FamilyVerdict.REQUIRED
    if decision == "not_required" or verdict in {"no", "not_required"} or required is False:
        return FamilyVerdict.NOT_REQUIRED
    return FamilyVerdict.ABSTAIN


def _legacy_family_map(result: Mapping[str, Any]) -> dict[str, FamilyVerdict]:
    rows = _list(result.get("permits_required"))
    families: dict[str, FamilyVerdict] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        raw_family = (
            row.get("permit_kind")
            or row.get("trade")
            or row.get("permit_type")
            or row.get("permit_name")
            or row.get("name")
        )
        family = normalize_family(raw_family)
        # Legacy labels such as "Commercial Building Permit" need a stable
        # coarse mapping rather than becoming incomparable free text.
        for token in ("electrical", "mechanical", "plumbing", "fire", "occupancy", "zoning", "building"):
            if token in family:
                family = token
                break
        if not family:
            continue
        if row.get("required") is True:
            verdict = FamilyVerdict.REQUIRED
        elif row.get("required") is False:
            verdict = FamilyVerdict.NOT_REQUIRED
        elif _slug(row.get("required")) in {"maybe", "conditional"}:
            verdict = FamilyVerdict.CONDITIONAL
        else:
            verdict = _verdict_from_required_status(row.get("required_status"))
        families[family] = verdict
    return families


def _regulated_projection(result: Mapping[str, Any]) -> dict[str, Any]:
    """Small deterministic regulated-field view for stage/render comparisons."""
    return {
        "main_verdict": _legacy_main_verdict(result).value,
        "families": {key: value.value for key, value in sorted(_legacy_family_map(result).items())},
        "applying_office": _normalize_text(result.get("applying_office")),
        "apply_url": _normalize_text(result.get("apply_url")),
    }


def classify_divergences(
    envelope: DecisionEnvelope,
    legacy_result: Mapping[str, Any] | None,
    *,
    authoritative_result: Mapping[str, Any] | None = None,
    public_result: Mapping[str, Any] | None = None,
) -> tuple[Divergence, ...]:
    if not isinstance(legacy_result, Mapping):
        return (
            Divergence(
                code=DivergenceCode.LEGACY_RESULT_MISSING,
                family=None,
                legacy_value=None,
                envelope_value=envelope.main_verdict.value,
                detail="legacy result is missing or not an object",
            ),
        )

    divergences: list[Divergence] = []
    legacy_main = _legacy_main_verdict(legacy_result)

    if envelope.coverage_status == CoverageStatus.AHJ_COVERED_SCOPE_UNSUPPORTED:
        divergences.extend(
            [
                Divergence(
                    DivergenceCode.SCOPE_TAXONOMY_UNSUPPORTED,
                    None,
                    legacy_main.value,
                    envelope.main_verdict.value,
                    "request scope cannot be normalized to a supported v2.4 project family",
                ),
                Divergence(
                    DivergenceCode.PROJECT_FAMILY_NOT_COVERED,
                    None,
                    legacy_main.value,
                    envelope.work_atoms[0].project_family if envelope.work_atoms else "unsupported",
                    "exact AHJ exists but no matching project-family cell is executable",
                ),
            ]
        )
    elif envelope.coverage_status in {CoverageStatus.AHJ_UNCOVERED, CoverageStatus.JURISDICTION_AMBIGUOUS}:
        divergences.append(
            Divergence(
                DivergenceCode.AHJ_BOUNDARY_MISMATCH,
                None,
                _normalize_text(legacy_result.get("jurisdiction_id")) or None,
                envelope.authority_context.jurisdiction_id or None,
                "request does not resolve to one exact covered jurisdiction boundary",
            )
        )

    if envelope.coverage_status == CoverageStatus.EXACT_PARTIAL:
        divergences.append(
            Divergence(
                DivergenceCode.RULE_OR_EXEMPTION_MISSING,
                None,
                None,
                envelope.coverage_reason,
                "exact seed lacks required-family rule or exemption closure",
            )
        )
    if envelope.validation_ok is False:
        divergences.append(
            Divergence(
                DivergenceCode.STALE_OR_CONFLICTING_RULE,
                None,
                None,
                "|".join(envelope.validation_issue_codes),
                "source cell failed portable request-time validation",
            )
        )

    if isinstance(authoritative_result, Mapping) and _regulated_projection(authoritative_result) != _regulated_projection(legacy_result):
        divergences.append(
            Divergence(
                DivergenceCode.POST_RECONCILIATION_MUTATION,
                None,
                stable_sha256(_regulated_projection(authoritative_result)),
                stable_sha256(_regulated_projection(legacy_result)),
                "regulated fields differ after the authoritative reconciliation checkpoint",
            )
        )
    if isinstance(public_result, Mapping) and _regulated_projection(public_result) != _regulated_projection(legacy_result):
        divergences.append(
            Divergence(
                DivergenceCode.PUBLIC_RENDER_DIVERGENCE,
                None,
                stable_sha256(_regulated_projection(legacy_result)),
                stable_sha256(_regulated_projection(public_result)),
                "public/render projection differs from the current-path regulated fields",
            )
        )

    if envelope.coverage_status == CoverageStatus.FAIL_CLOSED and legacy_main in {
        FamilyVerdict.REQUIRED,
        FamilyVerdict.NOT_REQUIRED,
    }:
        divergences.extend(
            [
                Divergence(
                    DivergenceCode.FAIL_CLOSED_VS_LEGACY_BINARY,
                    None,
                    legacy_main.value,
                    FamilyVerdict.ABSTAIN.value,
                    "static cell is fail-closed while legacy emitted a binary decision",
                ),
                Divergence(
                    DivergenceCode.MODEL_OR_GENERIC_FALLBACK_GUESS,
                    None,
                    legacy_main.value,
                    FamilyVerdict.ABSTAIN.value,
                    "current path emitted binary regulated truth outside executable cell coverage",
                ),
            ]
        )
    elif envelope.main_verdict not in {FamilyVerdict.ABSTAIN, legacy_main} and legacy_main != FamilyVerdict.ABSTAIN:
        divergences.append(
            Divergence(
                DivergenceCode.DECISION_MISMATCH,
                None,
                legacy_main.value,
                envelope.main_verdict.value,
                "top-level permit decision differs",
            )
        )

    envelope_families = {item.family: item.verdict for item in envelope.family_decisions}
    legacy_families = _legacy_family_map(legacy_result)
    for family in sorted(set(envelope_families) - set(legacy_families)):
        divergences.extend(
            [
                Divergence(
                    DivergenceCode.COMPANION_CLOSURE_INCOMPLETE,
                    family,
                    None,
                    envelope_families[family].value,
                    "current path omits a source-cell companion family",
                ),
                Divergence(
                    DivergenceCode.FAMILY_MISSING_IN_LEGACY,
                    family,
                    None,
                    envelope_families[family].value,
                    "envelope has a family decision absent from legacy permits_required",
                ),
            ]
        )
    for family in sorted(set(legacy_families) - set(envelope_families)):
        divergences.append(
            Divergence(
                DivergenceCode.FAMILY_EXTRA_IN_LEGACY,
                family,
                legacy_families[family].value,
                None,
                "legacy has a family absent from the source cell",
            )
        )
    for family in sorted(set(envelope_families) & set(legacy_families)):
        if envelope_families[family] != legacy_families[family]:
            divergences.append(
                Divergence(
                    DivergenceCode.FAMILY_VERDICT_MISMATCH,
                    family,
                    legacy_families[family].value,
                    envelope_families[family].value,
                    "per-family verdict differs",
                )
            )

    legacy_jurisdiction_id = _normalize_text(legacy_result.get("jurisdiction_id"))
    envelope_jurisdiction_id = _normalize_text(envelope.authority_context.jurisdiction_id)
    if legacy_jurisdiction_id and envelope_jurisdiction_id and legacy_jurisdiction_id != envelope_jurisdiction_id:
        divergences.append(
            Divergence(
                DivergenceCode.AHJ_BOUNDARY_MISMATCH,
                None,
                legacy_jurisdiction_id,
                envelope_jurisdiction_id,
                "current-path jurisdiction identity differs from the exact source cell",
            )
        )

    envelope_offices = {
        _normalize_text(ref.application_authority).lower()
        for ref in envelope.authority_context.family_authorities
        if ref.application_authority
    }
    legacy_office = _normalize_text(legacy_result.get("applying_office")).lower()
    if envelope_offices and legacy_office and legacy_office not in envelope_offices:
        divergences.append(
            Divergence(
                DivergenceCode.AUTHORITY_MISMATCH,
                None,
                legacy_office,
                " | ".join(sorted(envelope_offices)),
                "legacy applying office does not equal a cell application authority",
            )
        )

    expected_apply_urls = {
        _normalize_text(route.apply_url)
        for route in envelope.application_routes
        if _normalize_text(route.apply_url)
    }
    legacy_apply_url = _normalize_text(legacy_result.get("apply_url"))
    if expected_apply_urls and legacy_apply_url and legacy_apply_url not in expected_apply_urls:
        divergences.append(
            Divergence(
                DivergenceCode.APPLY_ROUTE_MISMATCH,
                None,
                legacy_apply_url,
                " | ".join(sorted(expected_apply_urls)),
                "legacy apply URL differs from source-cell route",
            )
        )

    legacy_sources = legacy_result.get("sources") if isinstance(legacy_result.get("sources"), list) else []
    if envelope.source_set_sha256 != stable_sha256([]) and not legacy_sources:
        divergences.extend(
            [
                Divergence(
                    DivergenceCode.AUTHORITATIVE_CELL_NOT_INJECTED,
                    None,
                    "0",
                    envelope.source_cell_id,
                    "source-backed cell evidence is absent from the current-path result",
                ),
                Divergence(
                    DivergenceCode.LEGACY_PROVENANCE_GAP,
                    None,
                    "0",
                    envelope.source_set_sha256,
                    "source-backed envelope exists while legacy exposes no sources",
                ),
            ]
        )

    if not divergences:
        code = (
            DivergenceCode.MATCH
            if envelope.coverage_status in {CoverageStatus.EXACT_COMPLETE, CoverageStatus.EXACT_PARTIAL}
            else DivergenceCode.COVERAGE_ONLY
        )
        divergences.append(
            Divergence(
                code,
                None,
                legacy_main.value,
                envelope.main_verdict.value,
                "no regulated-value divergence detected",
            )
        )
    return tuple(sorted(divergences, key=lambda item: (item.code.value, item.family or "", item.detail)))


def build_shadow_observation(
    envelope: DecisionEnvelope,
    legacy_result: Mapping[str, Any] | None,
    *,
    authoritative_result: Mapping[str, Any] | None = None,
    public_result: Mapping[str, Any] | None = None,
) -> ShadowObservation:
    envelope_primitive = to_primitive(envelope)
    return ShadowObservation(
        schema_version=SHADOW_EVENT_VERSION,
        taxonomy_version=DIVERGENCE_TAXONOMY_VERSION,
        adapter_version=ADAPTER_VERSION,
        request_fingerprint_sha256=envelope.request_fingerprint_sha256,
        envelope_sha256=stable_sha256(envelope_primitive),
        source_cell_id=envelope.source_cell_id,
        coverage_status=envelope.coverage_status,
        divergences=classify_divergences(
            envelope,
            legacy_result,
            authoritative_result=authoritative_result,
            public_result=public_result,
        ),
    )


def observe_permit_rule_engine_shadow(
    envelope: DecisionEnvelope | None,
    legacy_result: Mapping[str, Any] | None,
    *,
    authoritative_result: Mapping[str, Any] | None = None,
    public_result: Mapping[str, Any] | None = None,
    sink_path: str | Path | None = None,
) -> ShadowObservation | None:
    """Observe without mutating the result; telemetry failure is customer-safe."""
    if envelope is None:
        return None
    observation = build_shadow_observation(
        envelope,
        legacy_result,
        authoritative_result=authoritative_result,
        public_result=public_result,
    )
    target = sink_path or os.environ.get(SHADOW_LOG_SETTING)
    if target:
        try:
            path = Path(target)
            if not path.is_absolute():
                raise ValueError("shadow telemetry path must be absolute")
            path.parent.mkdir(parents=True, exist_ok=True)
            line = canonical_json_bytes(to_primitive(observation)) + b"\n"
            with path.open("ab") as handle:
                handle.write(line)
        except Exception:
            # Shadow telemetry is explicitly prohibited from changing customer
            # availability or response behavior.
            pass
    return observation


def envelope_from_cell_for_census(cell: Mapping[str, Any], index_key: str) -> DecisionEnvelope:
    """Deterministically build the canonical-family request used by the census."""
    family = normalize_family(cell.get("project_family"))
    category = "commercial" if family == "commercial_tenant_improvement" else "residential"
    job_type = {
        "commercial_tenant_improvement": "commercial tenant improvement",
        "residential_remodel": "residential remodel",
        "reroof": "residential reroof",
    }.get(family, family.replace("_", " "))
    status = (
        V24ResolutionStatus.EXACT_CELL_FAIL_CLOSED
        if cell.get("status") == "FAIL_CLOSED" or cell.get("serving_status") == "FAIL_CLOSED"
        else V24ResolutionStatus.EXACT_CELL_PUBLISHABLE
    )
    resolution = V24Resolution(status=status, cell=dict(cell), key=index_key, reason="Part 1 census canonical request")
    return build_decision_envelope(
        resolution,
        job_type=job_type,
        city=_normalize_text(cell.get("ahj")),
        state=_normalize_text(cell.get("state")),
        job_category=category,
    )
