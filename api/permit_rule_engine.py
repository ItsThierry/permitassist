"""Typed Permit Rule Engine contracts and a side-effect-isolated shadow adapter.

Part 1 intentionally does not replace the legacy PermitAssist decision path.  It
turns the shipped v2.4 Decision Cells into deterministic, versioned envelopes
that can be measured beside the legacy result.  The adapter is disabled by
default and its return value is never merged into customer or cache payloads.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
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
OFFICIAL_QUERY_EVIDENCE_SETTING = "PERMITASSIST_RULE_ENGINE_OFFICIAL_QUERY_EVIDENCE"

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


class ExemptionPolarity(str, Enum):
    """Semantic polarity of an official exemption/permit statement."""

    POSITIVE_EXEMPTION = "POSITIVE_EXEMPTION"
    POSITIVE_REQUIREMENT = "POSITIVE_REQUIREMENT"
    AMBIGUOUS = "AMBIGUOUS"


class RouteReachability(str, Enum):
    REACHABLE = "REACHABLE"
    UNREACHABLE = "UNREACHABLE"
    UNKNOWN = "UNKNOWN"


_REQUIREMENT_POLARITY_PATTERNS = (
    re.compile(r"\bnot\s+(?:permit[- ]?)?exempt\b", re.I),
    re.compile(r"\bnot\s+exempt\s+from\s+(?:a\s+)?permits?\b", re.I),
    re.compile(r"(?<!no )\bpermits?\s+(?:is|are)\s+required\b", re.I),
    re.compile(r"\b(?:must|shall)\s+(?:first\s+)?(?:obtain|secure|have)\s+(?:a\s+)?permits?\b", re.I),
    re.compile(r"\bpermits?\s+(?:must|shall)\s+be\s+(?:obtained|secured)\b", re.I),
    re.compile(r"(?<!not )\brequires?\s+(?:a\s+)?permits?\b", re.I),
)
_EXEMPTION_POLARITY_PATTERNS = (
    re.compile(r"\bno\s+permits?\s+(?:is|are)\s+required\b", re.I),
    re.compile(r"\bpermits?\s+(?:is|are)\s+not\s+required\b", re.I),
    re.compile(r"\b(?:does|do|shall)\s+not\s+require\b.{0,80}\bpermits?\b", re.I),
    re.compile(r"\bwithout\s+(?:first\s+)?(?:obtaining|securing|having)\s+(?:a\s+)?permits?\b", re.I),
    re.compile(r"\bpermit[- ]?exempt\b", re.I),
    re.compile(r"\bexempt\s+from\s+(?:the\s+)?permit(?:ting)?\s+requirements?\b", re.I),
)
_AMBIGUOUS_POLARITY_PATTERNS = (
    re.compile(r"\b(?:may|might|could)\s+(?:be\s+)?exempt\b", re.I),
    re.compile(r"\bexemptions?\s+(?:may|might|could)\s+apply\b", re.I),
    re.compile(r"\bverify\b.*\bexempt", re.I),
)


def classify_exemption_polarity(text: object) -> ExemptionPolarity:
    """Return a conservative, deterministic exemption/requirement polarity."""

    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return ExemptionPolarity.AMBIGUOUS
    # Requirement/negated-exemption wording must win before any exemption token.
    if any(pattern.search(normalized) for pattern in _REQUIREMENT_POLARITY_PATTERNS):
        return ExemptionPolarity.POSITIVE_REQUIREMENT
    if any(pattern.search(normalized) for pattern in _AMBIGUOUS_POLARITY_PATTERNS):
        return ExemptionPolarity.AMBIGUOUS
    if any(pattern.search(normalized) for pattern in _EXEMPTION_POLARITY_PATTERNS):
        return ExemptionPolarity.POSITIVE_EXEMPTION
    return ExemptionPolarity.AMBIGUOUS


_ANTI_BOT_MARKERS = (
    "access denied",
    "verify you are human",
    "checking your browser",
    "captcha",
    "cloudflare ray id",
    "automated access",
    "bot detection",
)


def classify_route_reachability(*, http_status: int | None, body_sample: object = "") -> RouteReachability:
    """Classify route checks without mislabeling anti-bot pages reachable."""

    body = str(body_sample or "").lower()
    if any(marker in body for marker in _ANTI_BOT_MARKERS):
        return RouteReachability.UNKNOWN
    if http_status is None or http_status in {401, 403, 407, 429}:
        return RouteReachability.UNKNOWN
    if 200 <= http_status < 400:
        return RouteReachability.REACHABLE
    if http_status in {404, 410}:
        return RouteReachability.UNREACHABLE
    return RouteReachability.UNKNOWN


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
class OfficialQueryEvidence:
    """Hash-bound official-query evidence; execution remains off by default."""

    query: str
    jurisdiction_id: str
    source_url: str
    source_quote: str
    snapshot_hash: str
    checked_at: str
    publishable: bool
    family: str = "building"
    verdict: FamilyVerdict = FamilyVerdict.ABSTAIN
    source_authority: str = "official_ahj"
    snapshot_path: str = ""
    effective_date: str | None = None


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
    validation_issue_codes: tuple[str, ...] = ()


def official_query_evidence_guard(evidence: object) -> dict[str, object]:
    """Report whether the dormant query-evidence precedence path may run."""

    enabled = os.environ.get(OFFICIAL_QUERY_EVIDENCE_SETTING, "").strip().lower() == "active"
    valid = bool(
        isinstance(evidence, OfficialQueryEvidence)
        and evidence.query.strip()
        and evidence.jurisdiction_id.strip()
        and evidence.source_url.startswith(("https://", "http://"))
        and evidence.source_quote.strip()
        and re.fullmatch(r"[0-9a-fA-F]{64}", evidence.snapshot_hash or "")
        and evidence.snapshot_hash.lower() != "0" * 64
        and evidence.checked_at.strip()
        and evidence.publishable is True
        and _slug(evidence.source_authority) in {"official_ahj", "official_state", "official_county"}
    )
    if not enabled:
        reason = "feature_disabled"
    elif valid:
        reason = "valid_hash_bound_official_query_evidence"
    else:
        reason = "invalid_or_unbound_evidence"
    return {
        "enabled": enabled,
        "valid": valid,
        "exercised": True,
        "reason": reason,
    }


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


def _canonical_jurisdiction_id(value: Any) -> str:
    """Normalize only separator spelling in a stable jurisdiction identifier.

    Jurisdiction ids are opaque apart from the repository's established
    lowercase separator convention.  Folding runs of whitespace, underscores,
    and hyphens prevents source-index aliases such as ``us-wi-eau_claire`` and
    ``us-wi-eau-claire`` from creating a false ambiguous-AHJ boundary, while
    preserving token distinctions such as ``springfield`` versus
    ``spring-field`` and every other punctuation character.
    """

    return re.sub(r"[\s_-]+", "-", _normalize_text(value).lower()).strip("-")


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


# ─── Part 2: guarded core, closed ontology, and sealed customer projection ────
#
# These contracts are deliberately additive.  The Part 1 envelope and shadow
# adapter above remain byte-for-byte stable when the independent core setting is
# off.  Core activation requires both an explicit active mode and an exact
# jurisdiction-id allowlist match.

ENVELOPE_SCHEMA_VERSION = DECISION_ENVELOPE_VERSION
CORE_ENVELOPE_SCHEMA_VERSION = "permitassist.decision-envelope.v2"
CORE_PROJECTION_SCHEMA_VERSION = "permitassist.customer-decision-projection.v1"
CORE_CACHE_SCHEMA_VERSION = "permitassist.rule-engine-cache.v1"
CORE_SETTING = "PERMITASSIST_RULE_ENGINE_CORE"
CORE_ALLOWLIST_SETTING = "PERMITASSIST_RULE_ENGINE_CORE_ALLOWLIST"

DecisionVerdict = FamilyVerdict


def normalize_exact_source_family(value: Any) -> str:
    """Preserve an exact v2.4 permit-family label for Part 3 customer truth.

    Part 1 intentionally used coarse aliases for comparison. Part 3 must not
    collapse separately sourced lanes such as ``gas`` or
    ``building_construction`` into another family.
    """
    return _slug(value)


class JurisdictionResolutionStatus(str, Enum):
    EXACT = "exact"
    AMBIGUOUS = "ambiguous"
    UNCOVERED = "uncovered"
    INDEX_UNAVAILABLE = "index_unavailable"


class WorkPolarity(str, Enum):
    POSITIVE = "positive"
    NEGATED = "negated"


class PrecedenceStage(str, Enum):
    VALIDATED_EXACT_COMPLETE = "validated_exact_complete"
    VALIDATED_EXACT_PARTIAL = "validated_exact_partial"
    EXACT_FAIL_CLOSED = "exact_fail_closed"
    QUERY_OFFICIAL_EVIDENCE = "query_official_evidence"
    INTERNAL_ABSTAIN = "internal_abstain"


@dataclass(frozen=True)
class JurisdictionCandidate:
    jurisdiction_id: str
    ahj_name: str
    state: str
    county: str | None
    cell_ids: tuple[str, ...]


@dataclass(frozen=True)
class JurisdictionIdentityResolution:
    status: JurisdictionResolutionStatus
    candidates: tuple[JurisdictionCandidate, ...]
    selected: JurisdictionCandidate | None
    reason: str


@dataclass(frozen=True)
class WorkThreshold:
    name: str
    operator: str
    value: int | float
    unit: str


@dataclass(frozen=True)
class ClosedWorkAtom:
    ontology_node: str
    project_family: str
    polarity: WorkPolarity
    raw_match: str
    thresholds: tuple[WorkThreshold, ...]


@dataclass(frozen=True)
class NormalizedWorkAtoms:
    atoms: tuple[ClosedWorkAtom, ...]
    positive_atoms: tuple[ClosedWorkAtom, ...]
    known_facts: tuple[tuple[str, Any], ...]
    issue_codes: tuple[str, ...]
    valid: bool


@dataclass(frozen=True)
class FamilyAuthorityRoute:
    family: str
    authority: AuthorityRef
    application_route: ApplicationRoute


@dataclass(frozen=True)
class CoreFamilyDecision:
    family: str
    verdict: FamilyVerdict
    trigger: str
    provenance: tuple[ProvenanceRecord, ...]
    validation_issue_codes: tuple[str, ...]


@dataclass(frozen=True)
class SealedDecisionProjection:
    schema_version: str
    payload_json: str
    payload_sha256: str


@dataclass(frozen=True)
class CoreDecisionEnvelope:
    schema_version: str
    request_fingerprint_sha256: str
    source_cell_id: str | None
    source_index_key: str | None
    source_package_manifest_sha256: str | None
    precedence_stage: PrecedenceStage
    coverage_status: str
    coverage_reason: str
    jurisdiction: JurisdictionIdentityResolution
    work_atoms: NormalizedWorkAtoms
    main_decision: CoreFamilyDecision
    family_decisions: tuple[CoreFamilyDecision, ...]
    family_routes: tuple[FamilyAuthorityRoute, ...]
    sealed_projection: SealedDecisionProjection
    envelope_sha256: str


_CORE_FAMILIES = frozenset(
    {
        # Locked W4 ten-lane filing-packet boundary.
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
        # Exact additional families present in the immutable v2.4 seed corpus.
        # Part 3 preserves these labels rather than collapsing or dropping them.
        "building_construction",
        "building_trade",
        "demolition",
        "gas",
        "manufactured_structure_installation",
        "moving",
        "pool",
        "septic_oss_health",
        "sign",
    }
)

_CORE_FACT_KEYS = frozenset(
    {
        "occupancy_change",
        "structural_change",
        "electrical_scope",
        "plumbing_scope",
        "mechanical_scope",
        "fire_life_safety_scope",
        "food_service_scope",
        "liquor_service_scope",
        "wastewater_scope",
        "zoning_trigger",
        "gas_scope",
        "demolition_scope",
        "pool_scope",
        "sign_scope",
        "structure_moving_scope",
        "manufactured_structure_scope",
        "septic_oss_scope",
        "area_sq_ft",
        "valuation_usd",
        "stories",
        "system_capacity_kw",
    }
)

# Order is part of the closed ontology contract.  Specific work atoms precede
# broad project-family phrases so normalization never depends on dict order.
_WORK_ONTOLOGY: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("roof_covering_replacement", "reroof", (r"\breroof\b", r"\bre-?roof\b", r"\broof replacement\b", r"\breplace (?:asphalt )?shingles?\b")),
    ("structural_alteration", "residential_remodel", (r"\bstructural (?:alteration|change|framing)\b", r"\bload[- ]bearing\b")),
    ("electrical_work", "residential_remodel", (r"\belectrical\b", r"\bpanel (?:replacement|upgrade|change)\b", r"\bwiring\b")),
    ("plumbing_work", "residential_remodel", (r"\bplumbing\b", r"\brepipe\b", r"\bwater heater\b")),
    ("mechanical_work", "residential_remodel", (r"\bmechanical\b", r"\bhvac\b", r"\bfurnace\b", r"\bair conditioner\b")),
    ("gas_work", "residential_remodel", (r"\bgas (?:line|piping|work|permit)\b", r"\bfuel gas\b")),
    ("demolition_work", "residential_remodel", (r"\bdemolition\b", r"\bdemolish\b")),
    ("pool_work", "residential_remodel", (r"\bswimming pool\b", r"\bpool (?:installation|work|permit)\b")),
    ("sign_work", "residential_remodel", (r"\bsign (?:installation|work|permit)\b",)),
    ("structure_moving", "residential_remodel", (r"\bmov(?:e|ing) (?:a )?(?:building|structure)\b",)),
    ("manufactured_structure_installation", "residential_remodel", (r"\bmanufactured (?:home|housing|structure)\b",)),
    ("septic_oss_work", "residential_remodel", (r"\bseptic\b", r"\bonsite sew(?:age|er)\b", r"\boss\b")),
    ("fire_life_safety_work", "commercial_tenant_improvement", (r"\bfire (?:alarm|sprinkler|suppression|life safety)\b",)),
    ("food_health_work", "commercial_tenant_improvement", (r"\bcommercial kitchen\b", r"\bfood service\b", r"\bhealth permit\b")),
    ("liquor_service_work", "commercial_tenant_improvement", (r"\bliquor\b", r"\balcohol service\b")),
    ("wastewater_work", "commercial_tenant_improvement", (r"\bwastewater\b", r"\bgrease interceptor\b")),
    ("occupancy_change", "commercial_tenant_improvement", (r"\bchange of (?:use|occupancy)\b", r"\boccupancy change\b", r"\boccupancy\b")),
    ("zoning_review", "commercial_tenant_improvement", (r"\bzoning\b", r"\bplanning review\b")),
    ("commercial_tenant_improvement", "commercial_tenant_improvement", (r"\bcommercial tenant improvement\b", r"\btenant improvement\b", r"\bcommercial build[- ]?out\b")),
    ("residential_remodel", "residential_remodel", (r"\bresidential remodel\b", r"\bkitchen remodel\b", r"\bbath(?:room)? remodel\b")),
)


def get_rule_engine_core_mode() -> str:
    value = str(os.environ.get(CORE_SETTING, "off") or "off").strip().lower()
    return "active" if value == "active" else "off"


def _core_allowlist() -> frozenset[str]:
    return frozenset(
        _canonical_jurisdiction_id(item)
        for item in str(os.environ.get(CORE_ALLOWLIST_SETTING, "") or "").split(",")
        if _canonical_jurisdiction_id(item)
    )


def core_activation_allowed(jurisdiction_id: str) -> bool:
    normalized = _canonical_jurisdiction_id(jurisdiction_id)
    return bool(
        normalized
        and get_rule_engine_core_mode() == "active"
        and normalized in _core_allowlist()
    )


def resolve_jurisdiction_identity(
    city: str,
    state: str,
    *,
    index: Mapping[str, Mapping[str, Any]] | None = None,
    canonicalize_id_aliases: bool = True,
) -> JurisdictionIdentityResolution:
    state_key = _normalize_text(state).upper()
    city_key = _slug(city)
    source_index = index if index is not None else load_v24_index()
    if source_index is None:
        return JurisdictionIdentityResolution(
            JurisdictionResolutionStatus.INDEX_UNAVAILABLE,
            (),
            None,
            "v2.4 jurisdiction index unavailable",
        )
    grouped: dict[str, dict[str, Any]] = {}
    for key in sorted(source_index):
        cell = source_index.get(key)
        if not isinstance(cell, Mapping):
            continue
        if _normalize_text(cell.get("state")).upper() != state_key or _slug(cell.get("ahj")) != city_key:
            continue
        raw_jurisdiction_id = _normalize_text(cell.get("jurisdiction_id")).lower()
        jurisdiction_id = (
            _canonical_jurisdiction_id(raw_jurisdiction_id)
            if canonicalize_id_aliases
            else raw_jurisdiction_id
        )
        if not jurisdiction_id:
            continue
        row = grouped.setdefault(
            jurisdiction_id,
            {
                "ahj_name": _normalize_text(cell.get("ahj")) or _normalize_text(city),
                "state": _normalize_text(cell.get("state") or state).upper(),
                "county": _normalize_text(cell.get("county")) or None,
                "cell_ids": set(),
            },
        )
        cell_id = _normalize_text(cell.get("cell_id"))
        if cell_id:
            row["cell_ids"].add(cell_id)
    candidates = tuple(
        JurisdictionCandidate(
            jurisdiction_id=jurisdiction_id,
            ahj_name=grouped[jurisdiction_id]["ahj_name"],
            state=grouped[jurisdiction_id]["state"],
            county=grouped[jurisdiction_id]["county"],
            cell_ids=tuple(sorted(grouped[jurisdiction_id]["cell_ids"])),
        )
        for jurisdiction_id in sorted(grouped)
    )
    if len(candidates) == 1:
        return JurisdictionIdentityResolution(
            JurisdictionResolutionStatus.EXACT,
            candidates,
            candidates[0],
            "one stable jurisdiction id matched the exact AHJ/state boundary",
        )
    if len(candidates) > 1:
        return JurisdictionIdentityResolution(
            JurisdictionResolutionStatus.AMBIGUOUS,
            candidates,
            None,
            "multiple stable jurisdiction ids matched the AHJ/state boundary",
        )
    return JurisdictionIdentityResolution(
        JurisdictionResolutionStatus.UNCOVERED,
        (),
        None,
        "no stable jurisdiction id matched the exact AHJ/state boundary",
    )


def _match_is_negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 32):start]
    return bool(re.search(r"(?:\bno\b|\bwithout\b|\bnot\b|does not include)\s+(?:\w+\s+){0,3}$", prefix))


def _scope_thresholds(text: str) -> tuple[WorkThreshold, ...]:
    values: list[WorkThreshold] = []
    number_pattern = r"([0-9][0-9,]*(?:\.[0-9]+)?)"
    area_pattern = re.compile(
        rf"(?:(under|less than|over|more than|at least|up to)\s+)?{number_pattern}\s*(?:sq\.?\s*ft\.?|square feet|square foot)\b"
    )
    operator_map = {
        "under": "lt",
        "less than": "lt",
        "over": "gt",
        "more than": "gt",
        "at least": "gte",
        "up to": "lte",
    }
    for match in area_pattern.finditer(text):
        number = float(match.group(2).replace(",", ""))
        values.append(
            WorkThreshold(
                "area_sq_ft",
                operator_map.get(match.group(1) or "", "eq"),
                int(number) if number.is_integer() else number,
                "sq_ft",
            )
        )
    for match in re.finditer(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)", text):
        number = float(match.group(1).replace(",", ""))
        values.append(WorkThreshold("valuation_usd", "eq", int(number) if number.is_integer() else number, "usd"))
    return tuple(sorted(values, key=lambda item: (item.name, item.value)))


def normalize_work_atoms(
    job_type: str,
    job_category: str = "",
    *,
    facts: Mapping[str, Any] | None = None,
) -> NormalizedWorkAtoms:
    text = _normalize_text(f"{job_type} {job_category}").lower()
    thresholds = _scope_thresholds(text)
    atoms: list[ClosedWorkAtom] = []
    for node, project_family, patterns in _WORK_ONTOLOGY:
        found: re.Match[str] | None = None
        for pattern in patterns:
            candidate = re.search(pattern, text)
            if candidate is not None and (found is None or candidate.start() < found.start()):
                found = candidate
        if found is None:
            continue
        polarity = WorkPolarity.NEGATED if _match_is_negated(text, found.start()) else WorkPolarity.POSITIVE
        fact_gate = {
            "structural_alteration": "structural_change",
            "electrical_work": "electrical_scope",
            "plumbing_work": "plumbing_scope",
            "mechanical_work": "mechanical_scope",
            "gas_work": "gas_scope",
            "demolition_work": "demolition_scope",
            "pool_work": "pool_scope",
            "sign_work": "sign_scope",
            "structure_moving": "structure_moving_scope",
            "manufactured_structure_installation": "manufactured_structure_scope",
            "septic_oss_work": "septic_oss_scope",
            "fire_life_safety_work": "fire_life_safety_scope",
            "food_health_work": "food_service_scope",
            "liquor_service_work": "liquor_service_scope",
            "wastewater_work": "wastewater_scope",
            "occupancy_change": "occupancy_change",
            "zoning_review": "zoning_trigger",
        }.get(node)
        if polarity is WorkPolarity.NEGATED and fact_gate and (facts or {}).get(fact_gate) is False:
            # A supplied, closed-world false fact is the canonical representation;
            # do not duplicate it as a negated atom.
            continue
        atoms.append(
            ClosedWorkAtom(
                ontology_node=node,
                project_family=project_family,
                polarity=polarity,
                raw_match=found.group(0),
                thresholds=thresholds,
            )
        )
    atoms.sort(key=lambda atom: tuple(item[0] for item in _WORK_ONTOLOGY).index(atom.ontology_node))
    declared_family_nodes = {
        "commercial_tenant_improvement": "commercial_tenant_improvement",
        "residential_remodel": "residential_remodel",
        "roof_covering_replacement": "reroof",
    }
    declared_families = {
        declared_family_nodes[atom.ontology_node]
        for atom in atoms
        if atom.polarity is WorkPolarity.POSITIVE and atom.ontology_node in declared_family_nodes
    }
    if len(declared_families) == 1:
        declared_family = next(iter(declared_families))
        atoms = [
            ClosedWorkAtom(
                ontology_node=atom.ontology_node,
                project_family=declared_family,
                polarity=atom.polarity,
                raw_match=atom.raw_match,
                thresholds=atom.thresholds,
            )
            for atom in atoms
        ]
    positive = tuple(atom for atom in atoms if atom.polarity is WorkPolarity.POSITIVE)
    known_facts: list[tuple[str, Any]] = []
    issues: set[str] = set()
    if len(declared_families) > 1:
        issues.add("ambiguous_project_family")
    for key, value in sorted((facts or {}).items()):
        normalized_key = _slug(key)
        if normalized_key not in _CORE_FACT_KEYS:
            issues.add("unknown_fact_key")
            continue
        if not isinstance(value, (bool, int, float, str)) or isinstance(value, str) and not _normalize_text(value):
            issues.add("invalid_fact_value")
            continue
        known_facts.append((normalized_key, value))
    if not atoms:
        issues.add("unknown_work_atom")
    if not positive:
        issues.add("no_positive_work_atom")
    return NormalizedWorkAtoms(
        atoms=tuple(atoms),
        positive_atoms=positive,
        known_facts=tuple(known_facts),
        issue_codes=tuple(sorted(issues)),
        valid=not issues,
    )


def _provenance_records(value: Any) -> tuple[ProvenanceRecord, ...]:
    values = value if isinstance(value, list) else [value]
    return _dedupe_sorted_provenance(_provenance_from_dict(item) for item in values)


def _publishable_provenance(record: ProvenanceRecord) -> bool:
    return bool(
        record.publishable
        and record.source_url
        and record.source_quote
        and re.fullmatch(r"[0-9a-f]{64}", record.snapshot_hash or "")
        and record.snapshot_hash.lower() != "0" * 64
        and record.snapshot_path
    )


def normalize_family_decision(value: Mapping[str, Any]) -> CoreFamilyDecision:
    family = normalize_exact_source_family(value.get("family") or value.get("permit_kind") or value.get("trade"))
    raw_verdict = _slug(value.get("verdict") or value.get("required_status") or value.get("value"))
    verdict_map = {
        "required": FamilyVerdict.REQUIRED,
        "not_required": FamilyVerdict.NOT_REQUIRED,
        "conditional": FamilyVerdict.CONDITIONAL,
        "verify": FamilyVerdict.VERIFY,
        "abstain": FamilyVerdict.ABSTAIN,
    }
    verdict = verdict_map.get(raw_verdict, FamilyVerdict.ABSTAIN)
    provenance = _provenance_records(value.get("provenance"))
    issues: set[str] = set()
    if family not in _CORE_FAMILIES:
        issues.add("unknown_family")
        verdict = FamilyVerdict.ABSTAIN
    publishable = tuple(record for record in provenance if _publishable_provenance(record))
    if verdict in {FamilyVerdict.REQUIRED, FamilyVerdict.NOT_REQUIRED} and not publishable:
        issues.add("binary_without_publishable_provenance")
        verdict = FamilyVerdict.VERIFY
    if verdict is FamilyVerdict.NOT_REQUIRED and publishable:
        polarities = tuple(classify_exemption_polarity(record.source_quote) for record in publishable)
        has_positive_exemption = ExemptionPolarity.POSITIVE_EXEMPTION in polarities
        has_contradictory_requirement = ExemptionPolarity.POSITIVE_REQUIREMENT in polarities
        if not has_positive_exemption or has_contradictory_requirement:
            issues.add("exemption_polarity_not_positively_supported")
            verdict = FamilyVerdict.VERIFY
    return CoreFamilyDecision(
        family=family or "unknown",
        verdict=verdict,
        trigger=_normalize_text(value.get("trigger")),
        provenance=provenance,
        validation_issue_codes=tuple(sorted(issues)),
    )


def _coerce_authority(row: Mapping[str, Any]) -> AuthorityRef | None:
    family = normalize_exact_source_family(row.get("permit_family") or row.get("trade"))
    if family not in _CORE_FAMILIES:
        return None
    issuing_raw = row.get("issuing_authority")
    applying_raw = row.get("application_authority")
    issuing = _mapping(issuing_raw)
    applying = _mapping(applying_raw)
    issuing_name = _normalize_text(issuing.get("name") if issuing else issuing_raw)
    applying_name = _normalize_text(applying.get("name") if applying else applying_raw)
    return AuthorityRef(
        family=family,
        issuing_authority=issuing_name,
        application_authority=applying_name,
        authority_tier=_slug(issuing.get("tier")) if issuing else "unknown",
        handled_by_local_ahj=(
            bool(row.get("handled_by_local_ahj"))
            if isinstance(row.get("handled_by_local_ahj"), bool)
            else None
        ),
    )


def _coerce_route(row: Mapping[str, Any]) -> ApplicationRoute:
    return ApplicationRoute(
        permit_name=_normalize_text(row.get("permit_name")),
        office_name=_normalize_text(row.get("office_name")),
        apply_url=_normalize_text(row.get("apply_url")),
        channel=_slug(row.get("channel")) or "unknown",
        provenance=_provenance_records(row.get("provenance")),
    )


def _route_matches_family(route: ApplicationRoute, family: str, authority: AuthorityRef) -> bool:
    permit_slug = _slug(route.permit_name)
    family_aliases = {family}
    if family == "mechanical":
        family_aliases |= {"hvac", "gas"}
    return any(alias in permit_slug for alias in family_aliases) or (
        bool(authority.application_authority)
        and authority.application_authority.lower() == route.office_name.lower()
    )


def _route_scope_mismatch(route: ApplicationRoute, project_family: str) -> bool:
    """Fail closed only when route provenance is visibly residential-only."""

    if not project_family.startswith("commercial"):
        return False
    provenance_text = " ".join(
        f"{record.source_url} {record.source_quote}" for record in route.provenance
    ).lower()
    residential_scope = bool(
        re.search(r"(?:/|\b)residential(?:/|\b)", provenance_text)
    )
    commercial_scope = bool(
        re.search(
            r"(?:/|\b)(?:commercial|non[-\s]?residential|business)(?:/|\b)"
            r"|\btenant (?:improvement|build[- ]?out)",
            provenance_text,
        )
    )
    residential_only_scope = bool(
        re.search(
            r"\bresidential(?:\s+(?:building\s+)?"
            r"(?:permit|permits|project|projects|work|application|applications|alteration|alterations))"
            r"{0,3}\s+only\b"
            r"|\bonly\s+(?:for\s+)?residential\b",
            provenance_text,
        )
    )
    commercial_exclusion_scope = bool(
        re.search(
            r"\b(?:not|is\s+not|isn't)\s+(?:intended\s+)?for\s+commercial\b",
            provenance_text,
        )
    )
    return (
        residential_only_scope
        or commercial_exclusion_scope
        or (residential_scope and not commercial_scope)
    )


def build_family_authority_routes(cell: Mapping[str, Any]) -> tuple[FamilyAuthorityRoute, ...]:
    tier1 = _mapping(cell.get("tier1"))
    project_family = _slug(cell.get("project_family"))
    authorities = tuple(
        authority
        for authority in (_coerce_authority(row) for row in _list(tier1.get("trade_authority")) if isinstance(row, Mapping))
        if authority is not None
    )
    routes = tuple(_coerce_route(row) for row in _list(tier1.get("apply")) if isinstance(row, Mapping))
    output: list[FamilyAuthorityRoute] = []
    for authority in sorted(authorities, key=lambda item: item.family):
        matches = sorted(
            (route for route in routes if _route_matches_family(route, authority.family, authority)),
            key=lambda item: (item.permit_name, item.office_name, item.apply_url),
        )
        route = matches[0] if matches else ApplicationRoute(
            permit_name="",
            office_name=authority.application_authority,
            apply_url="",
            channel="verify",
            provenance=(),
        )
        if _route_scope_mismatch(route, project_family):
            # Keep the official destination actionable, but do not let a
            # residential evidence record prove a commercial route dimension.
            route = ApplicationRoute(
                permit_name=route.permit_name,
                office_name=route.office_name,
                apply_url=route.apply_url,
                channel="verify",
                provenance=(),
                validation_issue_codes=("route_provenance_scope_mismatch",),
            )
        output.append(FamilyAuthorityRoute(authority.family, authority, route))
    return tuple(output)


def select_precedence_stage(
    resolution_status: V24ResolutionStatus,
    coverage: str,
    official_query_evidence_valid: bool,
) -> PrecedenceStage:
    normalized_coverage = _slug(coverage)
    if resolution_status is V24ResolutionStatus.EXACT_CELL_PUBLISHABLE and normalized_coverage == "complete":
        return PrecedenceStage.VALIDATED_EXACT_COMPLETE
    if resolution_status is V24ResolutionStatus.EXACT_CELL_PUBLISHABLE and normalized_coverage == "partial":
        return PrecedenceStage.VALIDATED_EXACT_PARTIAL
    if resolution_status is V24ResolutionStatus.EXACT_CELL_FAIL_CLOSED or normalized_coverage == "fail_closed":
        return PrecedenceStage.EXACT_FAIL_CLOSED
    if official_query_evidence_valid:
        return PrecedenceStage.QUERY_OFFICIAL_EVIDENCE
    return PrecedenceStage.INTERNAL_ABSTAIN


def _projection_sources(decisions: Iterable[CoreFamilyDecision]) -> list[dict[str, Any]]:
    records = _dedupe_sorted_provenance(
        record for decision in decisions for record in decision.provenance if _publishable_provenance(record)
    )
    return [
        {
            "url": record.source_url,
            "source_url": record.source_url,
            "title": "Official jurisdiction source",
            "quote": record.source_quote,
            "source_quote": record.source_quote,
            "snapshot_hash": record.snapshot_hash,
            "effective_date": record.effective_date,
            "last_verified_at": record.last_verified_at,
        }
        for record in records
    ]


def _public_family_authority_route(route: FamilyAuthorityRoute) -> dict[str, Any]:
    public_provenance = _projection_sources(
        (
            CoreFamilyDecision(
                family=route.family,
                verdict=FamilyVerdict.ABSTAIN,
                trigger="",
                provenance=route.application_route.provenance,
                validation_issue_codes=(),
            ),
        )
    )
    return {
        "family": route.family,
        "authority": {
            "family": route.authority.family,
            "issuing_authority": route.authority.issuing_authority,
            "application_authority": route.authority.application_authority,
        },
        "application_route": {
            "permit_name": route.application_route.permit_name,
            "office_name": route.application_route.office_name,
            "apply_url": route.application_route.apply_url,
            "channel": route.application_route.channel,
            "provenance": public_provenance,
            "validation_issue_codes": list(route.application_route.validation_issue_codes),
        },
    }


def build_sealed_projection_payload(
    *,
    jurisdiction_id: str,
    jurisdiction_name: str,
    state: str,
    project_family: str,
    main_decision: CoreFamilyDecision,
    family_decisions: tuple[CoreFamilyDecision, ...],
    family_routes: tuple[FamilyAuthorityRoute, ...],
    coverage_status: str,
    coverage_reason: str,
    source_cell_id: str | None,
    seed_classification: SeedClassification | str | None = None,
) -> dict[str, Any]:
    seed_classification_value = (
        seed_classification.value
        if isinstance(seed_classification, SeedClassification)
        else _normalize_text(seed_classification)
    )
    fail_closed_projection = seed_classification_value in {
        SeedClassification.FAIL_CLOSED.value,
        SeedClassification.JURISDICTION_HOLD.value,
        SeedClassification.UNSUPPORTED_SCOPE.value,
    }
    if fail_closed_projection:
        main_decision = CoreFamilyDecision(
            family=main_decision.family,
            verdict=FamilyVerdict.ABSTAIN,
            trigger=main_decision.trigger,
            provenance=(),
            validation_issue_codes=tuple(sorted(set(main_decision.validation_issue_codes) | {"part3_fail_closed"})),
        )
        family_decisions = tuple(
            CoreFamilyDecision(
                family=decision.family,
                verdict=FamilyVerdict.ABSTAIN,
                trigger=decision.trigger,
                provenance=(),
                validation_issue_codes=tuple(sorted(set(decision.validation_issue_codes) | {"part3_fail_closed"})),
            )
            for decision in family_decisions
        )
        family_routes = ()
    main_binary = main_decision.verdict in {FamilyVerdict.REQUIRED, FamilyVerdict.NOT_REQUIRED}
    permit_required = True if main_decision.verdict is FamilyVerdict.REQUIRED else False if main_decision.verdict is FamilyVerdict.NOT_REQUIRED else None
    route_by_family = {route.family: route for route in family_routes}
    main_route = route_by_family.get(main_decision.family)
    if main_route is None and family_routes:
        main_route = family_routes[0]
    family_rows = [
        {
            "family": decision.family,
            "verdict": decision.verdict.value,
            "trigger": decision.trigger,
            "authority": (
                route_by_family[decision.family].authority.application_authority
                if decision.family in route_by_family
                else ""
            ),
            "apply_url": (
                route_by_family[decision.family].application_route.apply_url
                if decision.family in route_by_family
                else ""
            ),
            "validation_issue_codes": list(decision.validation_issue_codes),
        }
        for decision in family_decisions
    ]
    verification_tasks = []
    for decision in family_decisions:
        route = route_by_family.get(decision.family)
        authority = route.authority.application_authority if route else ""
        apply_url = route.application_route.apply_url if route else ""
        sourced_binary = (
            decision.verdict in {FamilyVerdict.REQUIRED, FamilyVerdict.NOT_REQUIRED}
            and any(_publishable_provenance(record) for record in decision.provenance)
        )
        if sourced_binary:
            continue
        unresolved = []
        if decision.verdict not in {FamilyVerdict.REQUIRED, FamilyVerdict.NOT_REQUIRED}:
            unresolved.append("applicability")
        if not any(_publishable_provenance(record) for record in decision.provenance):
            unresolved.append("source evidence")
        if not authority:
            unresolved.append("filing authority")
        if not apply_url:
            unresolved.append("application route")
        verification_tasks.append(
            {
                "family": decision.family,
                "unresolved_dimensions": unresolved,
                "authority": authority,
                "apply_url": apply_url,
                "action": (
                    f"Verify {decision.family.replace('_', ' ')} "
                    f"{', '.join(unresolved)} with {authority or 'the official permit authority'} before filing."
                ),
            }
        )
    permits_required = [
        {
            "permit_type": decision.family.replace("_", " ").title() + " Permit",
            "permit_kind": decision.family,
            "required": True if decision.verdict is FamilyVerdict.REQUIRED else False if decision.verdict is FamilyVerdict.NOT_REQUIRED else "maybe",
            "required_status": decision.verdict.value,
            "trigger": decision.trigger,
            "applying_office": route_by_family[decision.family].authority.application_authority if decision.family in route_by_family else "",
            "apply_url": route_by_family[decision.family].application_route.apply_url if decision.family in route_by_family else "",
        }
        for decision in family_decisions
    ]
    related = [
        {
            "permit_type": decision.family.replace("_", " ").title() + " Permit",
            "permit_kind": decision.family,
            "required": False if decision.verdict is FamilyVerdict.NOT_REQUIRED else "maybe",
            "required_status": decision.verdict.value,
            "trigger": decision.trigger,
        }
        for decision in family_decisions
        if decision.verdict is not FamilyVerdict.REQUIRED
    ]
    sources = _projection_sources((main_decision, *family_decisions))
    required_name = next(
        (row["permit_type"] for row in permits_required if row.get("required") is True),
        "",
    )
    permit_name = required_name or (
        permits_required[0]["permit_type"]
        if permits_required
        else main_decision.family.replace("_", " ").title() + " Permit"
    )
    verdict_text = "YES" if permit_required is True else "NO" if permit_required is False else "VERIFY"
    decision_text = main_decision.verdict.value if main_binary else "UNKNOWN"
    unresolved_family_rows = [
        row for row in family_rows if row["verdict"] in {"CONDITIONAL", "VERIFY", "ABSTAIN"}
    ]
    if seed_classification_value == SeedClassification.EXACT_PARTIAL.value and unresolved_family_rows:
        next_step = (
            f"Verify unresolved permit families, then apply with {main_route.authority.application_authority}."
            if main_route and main_route.authority.application_authority
            else "Verify unresolved permit families with the official permit authority before filing."
        )
    else:
        next_step = (
            f"Apply with {main_route.authority.application_authority}."
            if main_route and main_route.authority.application_authority
            else "Verify the listed family decisions with the official permit authority before filing."
        )
    return {
        "projection_schema_version": CORE_PROJECTION_SCHEMA_VERSION,
        "decision_source": "sealed_permit_rule_engine_envelope",
        "source_cell_id": source_cell_id,
        "seed_classification": seed_classification_value,
        "jurisdiction_id": jurisdiction_id,
        "jurisdiction_name": jurisdiction_name,
        "city": jurisdiction_name,
        "state": state,
        "project_family": project_family,
        "coverage_status": coverage_status,
        "coverage_reason": coverage_reason,
        "permit_decision": decision_text,
        "permit_verdict": verdict_text,
        "permit_required": permit_required,
        "permit_kind": main_decision.family,
        "permit_name": permit_name,
        "permits_required": permits_required,
        "companion_permits": related,
        "related_permits": copy.deepcopy(related),
        "family_decisions": family_rows,
        "verification_tasks": verification_tasks,
        "family_authority_routes": [_public_family_authority_route(route) for route in family_routes],
        "applying_office": main_route.authority.application_authority if main_route else "",
        "apply_url": main_route.application_route.apply_url if main_route else "",
        "online_application_url": main_route.application_route.apply_url if main_route else "",
        "sources": sources,
        "claim_citations": copy.deepcopy(sources),
        "customer_headline": f"Permit decision: {verdict_text}",
        "customer_next_step": next_step,
        "summary": coverage_reason,
        "warnings": [
            f"{row['family'].replace('_', ' ').title()}: {row['verdict']} — verify before filing."
            for row in family_rows
            if row["verdict"] in {"CONDITIONAL", "VERIFY", "ABSTAIN"}
        ],
    }


def _identity_from_resolution(resolution: V24Resolution, city: str, state: str) -> JurisdictionIdentityResolution:
    # A carried cell must never override a known ambiguous AHJ identity. This
    # makes the duplicate-name boundary fail closed before any family truth is
    # projected. Uncovered synthetic/query cells may still carry an explicit
    # stable jurisdiction id for deterministic tests and official-query flows.
    indexed_identity = resolve_jurisdiction_identity(city, state)
    if indexed_identity.status is JurisdictionResolutionStatus.AMBIGUOUS:
        return indexed_identity
    cell = resolution.cell if isinstance(resolution.cell, Mapping) else None
    if cell and _canonical_jurisdiction_id(cell.get("jurisdiction_id")):
        candidate = JurisdictionCandidate(
            jurisdiction_id=_canonical_jurisdiction_id(cell.get("jurisdiction_id")),
            ahj_name=_normalize_text(cell.get("ahj")) or _normalize_text(city),
            state=_normalize_text(cell.get("state") or state).upper(),
            county=_normalize_text(cell.get("county")) or None,
            cell_ids=tuple(item for item in (_normalize_text(cell.get("cell_id")),) if item),
        )
        return JurisdictionIdentityResolution(
            JurisdictionResolutionStatus.EXACT,
            (candidate,),
            candidate,
            "exact resolved v2.4 cell carries one stable jurisdiction id",
        )
    return resolve_jurisdiction_identity(city, state)


def _seal_projection(payload: Mapping[str, Any]) -> SealedDecisionProjection:
    payload_json = canonical_json_bytes(payload).decode("utf-8")
    return SealedDecisionProjection(
        schema_version=CORE_PROJECTION_SCHEMA_VERSION,
        payload_json=payload_json,
        payload_sha256=hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
    )


def _core_envelope_hash_payload(envelope: Mapping[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(dict(envelope))
    payload.pop("envelope_sha256", None)
    return payload


def build_core_decision_envelope(
    resolution: V24Resolution,
    *,
    job_type: str,
    city: str,
    state: str,
    job_category: str = "",
    facts: Mapping[str, Any] | None = None,
    official_query_evidence: OfficialQueryEvidence | None = None,
) -> CoreDecisionEnvelope:
    if not isinstance(resolution, V24Resolution):
        raise TypeError("resolution must be V24Resolution")
    identity = _identity_from_resolution(resolution, city, state)
    seed = _classification_for_resolution(resolution, identity)
    work = normalize_work_atoms(job_type, job_category, facts=facts)
    cell = _mapping(resolution.cell)
    legacy_envelope = build_decision_envelope(
        resolution,
        job_type=job_type,
        city=city,
        state=state,
        job_category=job_category,
    )
    coverage_map = {
        CoverageStatus.EXACT_COMPLETE: "complete",
        CoverageStatus.EXACT_PARTIAL: "partial",
        CoverageStatus.FAIL_CLOSED: "fail_closed",
    }
    coverage = coverage_map.get(legacy_envelope.coverage_status, "none")
    query_guard = official_query_evidence_guard(official_query_evidence)
    query_decision = CoreFamilyDecision(
        family="building",
        verdict=FamilyVerdict.ABSTAIN,
        trigger="",
        provenance=(),
        validation_issue_codes=("official_query_evidence_disabled_or_invalid",),
    )
    if isinstance(official_query_evidence, OfficialQueryEvidence):
        query_decision = normalize_family_decision(
            {
                "family": official_query_evidence.family,
                "verdict": official_query_evidence.verdict.value,
                "trigger": official_query_evidence.query,
                "provenance": [
                    {
                        "source_url": official_query_evidence.source_url,
                        "source_quote": official_query_evidence.source_quote,
                        "retrieved_at": official_query_evidence.checked_at,
                        "snapshot_hash": official_query_evidence.snapshot_hash,
                        "snapshot_path": official_query_evidence.snapshot_path
                        or f"official-query://{official_query_evidence.snapshot_hash}",
                        "effective_date": official_query_evidence.effective_date,
                        "freshness_class": "fresh",
                        "last_verified_at": official_query_evidence.checked_at,
                        "publishable": official_query_evidence.publishable,
                    }
                ],
            }
        )
    selected_jurisdiction_id = identity.selected.jurisdiction_id if identity.selected else ""
    query_valid = bool(
        query_guard["enabled"]
        and query_guard["valid"]
        and isinstance(official_query_evidence, OfficialQueryEvidence)
        and _canonical_jurisdiction_id(official_query_evidence.jurisdiction_id)
        == _canonical_jurisdiction_id(selected_jurisdiction_id)
        and query_decision.verdict in {FamilyVerdict.REQUIRED, FamilyVerdict.NOT_REQUIRED}
        and identity.status is JurisdictionResolutionStatus.EXACT
        and work.valid
    )
    stage = select_precedence_stage(resolution.status, coverage, query_valid)
    if seed.classification is SeedClassification.FAIL_CLOSED:
        stage = PrecedenceStage.EXACT_FAIL_CLOSED
    elif seed.classification in {
        SeedClassification.JURISDICTION_HOLD,
        SeedClassification.UNSUPPORTED_SCOPE,
    }:
        stage = PrecedenceStage.INTERNAL_ABSTAIN
    if not work.valid or identity.status is not JurisdictionResolutionStatus.EXACT:
        stage = PrecedenceStage.INTERNAL_ABSTAIN

    tier1 = _mapping(cell.get("tier1"))
    decisions: list[CoreFamilyDecision] = []
    for row in _list(tier1.get("permits_required")):
        if isinstance(row, Mapping):
            decisions.append(
                normalize_family_decision(
                    {
                        "family": row.get("permit_kind"),
                        "verdict": row.get("required_status"),
                        "trigger": row.get("trigger"),
                        "provenance": row.get("provenance"),
                    }
                )
            )
    decisions.sort(key=lambda item: (item.family, item.verdict.value, item.trigger))
    main_row = _mapping(tier1.get("main_decision"))
    primary_family = decisions[0].family if decisions else "building"
    main = normalize_family_decision(
        {
            "family": primary_family,
            "verdict": main_row.get("value"),
            "trigger": _normalize_text(cell.get("scope")) or _normalize_text(cell.get("project_family")),
            "provenance": main_row.get("provenance"),
        }
    )
    if stage is PrecedenceStage.QUERY_OFFICIAL_EVIDENCE:
        main = query_decision
        decisions = [query_decision]
    elif stage in {PrecedenceStage.EXACT_FAIL_CLOSED, PrecedenceStage.INTERNAL_ABSTAIN}:
        main = CoreFamilyDecision(
            family=primary_family,
            verdict=FamilyVerdict.ABSTAIN,
            trigger=legacy_envelope.coverage_reason,
            provenance=(),
            validation_issue_codes=("precedence_abstain",),
        )
        abstain_issue = (
            "exact_cell_fail_closed"
            if stage is PrecedenceStage.EXACT_FAIL_CLOSED
            else "request_scope_not_executable"
        )
        decisions = [
            CoreFamilyDecision(
                family=decision.family,
                verdict=FamilyVerdict.ABSTAIN,
                trigger=decision.trigger,
                provenance=(),
                validation_issue_codes=tuple(sorted(set(decision.validation_issue_codes) | {abstain_issue})),
            )
            for decision in decisions
        ]
    project_family = _slug(cell.get("project_family")) or (
        work.positive_atoms[0].project_family if work.positive_atoms else "unsupported"
    )
    decisions = list(
        _complete_part3_family_decisions(
            decisions,
            project_family,
            seed.classification,
        )
    )
    routes = (
        build_family_authority_routes(cell)
        if cell
        and seed.classification in {
            SeedClassification.EXACT_COMPLETE,
            SeedClassification.EXACT_PARTIAL,
        }
        and identity.status is JurisdictionResolutionStatus.EXACT
        else ()
    )
    selected = identity.selected
    jurisdiction_id = selected.jurisdiction_id if selected else ""
    jurisdiction_name = selected.ahj_name if selected else _normalize_text(city)
    jurisdiction_state = selected.state if selected else _normalize_text(state).upper()
    coverage_reason = legacy_envelope.coverage_reason
    payload = build_sealed_projection_payload(
        jurisdiction_id=jurisdiction_id,
        jurisdiction_name=jurisdiction_name,
        state=jurisdiction_state,
        project_family=project_family,
        main_decision=main,
        family_decisions=tuple(decisions),
        family_routes=routes,
        coverage_status=stage.value,
        coverage_reason=coverage_reason,
        source_cell_id=legacy_envelope.source_cell_id,
        seed_classification=seed.classification,
    )
    sealed = _seal_projection(payload)
    base = {
        "schema_version": CORE_ENVELOPE_SCHEMA_VERSION,
        "request_fingerprint_sha256": _request_fingerprint(job_type, city, state, job_category),
        "source_cell_id": legacy_envelope.source_cell_id,
        "source_index_key": legacy_envelope.source_index_key,
        "source_package_manifest_sha256": legacy_envelope.source_package_manifest_sha256,
        "precedence_stage": stage,
        "coverage_status": coverage,
        "coverage_reason": coverage_reason,
        "jurisdiction": identity,
        "work_atoms": work,
        "main_decision": main,
        "family_decisions": tuple(decisions),
        "family_routes": routes,
        "sealed_projection": sealed,
    }
    envelope_hash = stable_sha256(to_primitive(base))
    return CoreDecisionEnvelope(**base, envelope_sha256=envelope_hash)


class _ServerOwnedCoreEnvelope(dict):
    """Private in-process marker for a freshly attached core envelope.

    The marker is deliberately carried by the Python type and an out-of-band
    digest rather than by a JSON field.  Serializing and decoding the mapping
    produces an ordinary ``dict`` that cannot assert server ownership.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._server_owned_sha256 = stable_sha256(dict(self))

    def has_intact_server_owned_payload(self) -> bool:
        return hmac.compare_digest(
            str(getattr(self, "_server_owned_sha256", "") or ""),
            stable_sha256(dict(self)),
        )


def attach_core_decision_envelope(
    result: Mapping[str, Any],
    envelope: CoreDecisionEnvelope,
) -> dict[str, Any]:
    if not isinstance(envelope, CoreDecisionEnvelope):
        raise TypeError("envelope must be CoreDecisionEnvelope")
    output = copy.deepcopy(dict(result))
    output["_permit_rule_engine_cache_schema_version"] = CORE_CACHE_SCHEMA_VERSION
    output["_permit_rule_engine_core"] = to_primitive(envelope)
    return _ServerOwnedCoreEnvelope(output)


def _validated_core_payload(result: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if result.get("_permit_rule_engine_cache_schema_version") != CORE_CACHE_SCHEMA_VERSION:
        return None
    envelope = result.get("_permit_rule_engine_core")
    if not isinstance(envelope, Mapping) or envelope.get("schema_version") != CORE_ENVELOPE_SCHEMA_VERSION:
        return None
    expected_envelope_hash = _normalize_text(envelope.get("envelope_sha256"))
    if not expected_envelope_hash or stable_sha256(_core_envelope_hash_payload(envelope)) != expected_envelope_hash:
        return None
    sealed = envelope.get("sealed_projection")
    if not isinstance(sealed, Mapping) or sealed.get("schema_version") != CORE_PROJECTION_SCHEMA_VERSION:
        return None
    payload_json = sealed.get("payload_json")
    expected_payload_hash = _normalize_text(sealed.get("payload_sha256"))
    if not isinstance(payload_json, str) or hashlib.sha256(payload_json.encode("utf-8")).hexdigest() != expected_payload_hash:
        return None
    try:
        payload = json.loads(payload_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("projection_schema_version") != CORE_PROJECTION_SCHEMA_VERSION:
        return None
    if canonical_json_bytes(payload).decode("utf-8") != payload_json:
        return None
    return dict(envelope), payload


def validate_rule_engine_cache_payload(result: Mapping[str, Any], *, required_version: str) -> bool:
    return bool(
        required_version == CORE_CACHE_SCHEMA_VERSION
        and isinstance(result, Mapping)
        and _validated_core_payload(result) is not None
    )


def extract_sealed_public_projection(
    result: Mapping[str, Any],
    *,
    city: str = "",
    state: str = "",
) -> dict[str, Any] | None:
    if not isinstance(result, Mapping):
        return None
    validated = _validated_core_payload(result)
    if validated is None:
        return None
    _envelope, payload = validated
    jurisdiction_id = _normalize_text(payload.get("jurisdiction_id")).lower()
    if not core_activation_allowed(jurisdiction_id):
        return None
    if city and _slug(payload.get("city")) != _slug(city):
        return None
    if state and _normalize_text(payload.get("state")).upper() != _normalize_text(state).upper():
        return None
    return copy.deepcopy(payload)


_CORE_PUBLIC_PROJECTION_FIELDS = frozenset(
    {
        "projection_schema_version",
        "decision_source",
        "jurisdiction_id",
        "jurisdiction_name",
        "city",
        "state",
        "project_family",
        "permit_required",
        "permit_decision",
        "permit_verdict",
        "permit_name",
        "permit_kind",
        "customer_headline",
        "customer_next_step",
        "summary",
        "coverage_status",
        "coverage_reason",
        "source_cell_id",
        "seed_classification",
        "family_decisions",
        "family_authority_routes",
        "permits_required",
        "companion_permits",
        "related_permits",
        "verification_tasks",
        "applying_office",
        "apply_url",
        "online_application_url",
        "sources",
        "claim_citations",
        "warnings",
    }
)


def project_core_customer_boundary(
    result: Mapping[str, Any],
    *,
    job_type: str,
    city: str,
    state: str,
    job_category: str = "",
) -> dict[str, Any] | None:
    """Return the one core customer DTO, failing closed on integrity errors.

    A valid envelope returns its byte-sealed public projection. For an active,
    allowlisted request, every unsealed or invalid payload fails closed; a
    shape-valid public DTO is never accepted as proof of server authenticity.
    The integrity fallback keeps every applicable permit-family lane visible as
    ABSTAIN with a concrete office-verification task.

    Flag-off and unallowlisted legacy traffic returns ``None`` so the pre-core
    path remains unchanged.
    """
    sealed = extract_sealed_public_projection(result, city=city, state=state)
    if sealed is not None:
        return sealed
    if not isinstance(result, Mapping) or get_rule_engine_core_mode() != "active":
        return None
    identity = resolve_jurisdiction_identity(city, state)
    if identity.status is not JurisdictionResolutionStatus.EXACT or identity.selected is None:
        return None
    if not core_activation_allowed(identity.selected.jurisdiction_id):
        return None

    work = normalize_work_atoms(job_type, job_category)
    project_family = next(
        (
            atom.project_family
            for atom in work.positive_atoms
            if atom.project_family in FAMILY_CLOSURE_REQUIREMENTS
        ),
        "unsupported",
    )
    families = tuple(
        sorted(FAMILY_CLOSURE_REQUIREMENTS.get(project_family, _CORE_FAMILIES))
    )
    has_unverified_core_artifact = any(
        key in result
        for key in (
            "_permit_rule_engine_core",
            "_permit_rule_engine_cache_schema_version",
        )
    )
    issue_code = "decision_integrity_validation_failed"
    office = f"{identity.selected.ahj_name} permit office"
    family_decisions = [
        {
            "family": family,
            "verdict": FamilyVerdict.ABSTAIN.value,
            "trigger": issue_code,
            "authority": "",
            "apply_url": "",
            "validation_issue_codes": [issue_code],
        }
        for family in families
    ]
    permits_required = [
        {
            "permit_kind": family,
            "permit_type": f"{family.replace('_', ' ').title()} Permit",
            "required": "maybe",
            "required_status": FamilyVerdict.ABSTAIN.value,
            "trigger": issue_code,
            "applying_office": "",
            "apply_url": "",
        }
        for family in families
    ]
    verification_tasks = [
        {
            "family": family,
            "action": (
                f"Verify {family.replace('_', ' ')} applicability, filing authority, "
                f"and application route with {office} before filing."
            ),
            "authority": "",
            "apply_url": "",
            "unresolved_dimensions": [
                "decision integrity",
                "applicability",
                "filing authority",
                "application route",
            ],
        }
        for family in families
    ]
    summary = (
        "The saved permit decision could not be validated. Contact the permit "
        "office before quoting, filing, or starting work."
    )
    projection = {
        "projection_schema_version": CORE_PROJECTION_SCHEMA_VERSION,
        "decision_source": "permit_rule_engine_integrity_fail_closed",
        "jurisdiction_id": identity.selected.jurisdiction_id,
        "jurisdiction_name": identity.selected.ahj_name,
        "city": identity.selected.ahj_name,
        "state": identity.selected.state,
        "project_family": project_family,
        "permit_required": None,
        "permit_decision": "UNKNOWN",
        "permit_verdict": "CONTACT_AHJ",
        "permit_name": None,
        "permit_kind": "Verification Required",
        "customer_headline": "Verify permit requirements with the permit office.",
        "customer_next_step": f"Contact {office} before filing or starting work.",
        "summary": summary,
        "coverage_status": "integrity_fail_closed",
        "coverage_reason": issue_code,
        "source_cell_id": "",
        "seed_classification": SeedClassification.FAIL_CLOSED.value,
        "family_decisions": family_decisions,
        "family_authority_routes": [],
        "permits_required": permits_required,
        "companion_permits": [],
        "related_permits": [],
        "verification_tasks": verification_tasks,
        "applying_office": office,
        "apply_url": "",
        "online_application_url": "",
        "sources": [],
        "claim_citations": [],
        "warnings": [summary],
    }
    if not has_unverified_core_artifact:
        projection["permit_verdict"] = "VERIFY"
    return projection


def core_cache_schema_for_request(city: str, state: str) -> str | None:
    """Return the active namespace only for one exact, allowlisted jurisdiction."""
    if get_rule_engine_core_mode() != "active":
        return None
    identity = resolve_jurisdiction_identity(city, state)
    if identity.status is not JurisdictionResolutionStatus.EXACT or identity.selected is None:
        return None
    return CORE_CACHE_SCHEMA_VERSION if core_activation_allowed(identity.selected.jurisdiction_id) else None


def maybe_attach_core_decision_envelope(
    result: dict[str, Any],
    *,
    job_type: str,
    city: str,
    state: str,
    job_category: str = "",
    facts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if get_rule_engine_core_mode() != "active":
        return result
    identity = resolve_jurisdiction_identity(city, state)
    if identity.status is not JurisdictionResolutionStatus.EXACT or identity.selected is None:
        return result
    if not core_activation_allowed(identity.selected.jurisdiction_id):
        return result
    resolution = resolve_v24_cell(city, state, job_type, job_category, force=True)
    envelope = build_core_decision_envelope(
        resolution,
        job_type=job_type,
        city=city,
        state=state,
        job_category=job_category,
        facts=facts,
    )
    return attach_core_decision_envelope(result, envelope)


def build_active_core_first_result(
    *,
    job_type: str,
    city: str,
    state: str,
    job_category: str = "",
    facts: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return a deterministic exact-complete core result before legacy research.

    Partial, unsupported, ambiguous, uncovered, unallowlisted, and flag-off
    requests return ``None`` and continue through the existing research path.
    """

    if get_rule_engine_core_mode() != "active":
        return None
    identity = resolve_jurisdiction_identity(city, state)
    if (
        identity.status is not JurisdictionResolutionStatus.EXACT
        or identity.selected is None
        or not core_activation_allowed(identity.selected.jurisdiction_id)
    ):
        return None
    resolution = resolve_v24_cell(city, state, job_type, job_category, force=True)
    envelope = build_core_decision_envelope(
        resolution,
        job_type=job_type,
        city=city,
        state=state,
        job_category=job_category,
        facts=facts,
    )
    if envelope.precedence_stage is not PrecedenceStage.VALIDATED_EXACT_COMPLETE:
        return None
    result = attach_core_decision_envelope({}, envelope)
    if extract_sealed_public_projection(result, city=city, state=state) is None:
        return None
    return result


# ─── Part 3: immutable seed migration and evidence-gated factory ─────────────

PART3_MIGRATION_SCHEMA_VERSION = "permitassist.rule-engine-seed-migration.v1"
PART3_PREDICATE_SCHEMA_VERSION = "permitassist.sourced-predicate.v1"
PART3_FACTORY_SCHEMA_VERSION = "permitassist.evidence-gated-factory.v1"
PART3_TEMPLATE_SCHEMA_VERSION = "permitassist.code-adoption-template.v1"
PART3_OVERLAY_SCHEMA_VERSION = "permitassist.ahj-overlay.v1"


class SeedClassification(str, Enum):
    EXACT_COMPLETE = "exact_complete"
    EXACT_PARTIAL = "exact_partial"
    FAIL_CLOSED = "fail_closed"
    JURISDICTION_HOLD = "jurisdiction_hold"
    UNSUPPORTED_SCOPE = "unsupported_scope"


@dataclass(frozen=True)
class MigratedSeed:
    schema_version: str
    source_index_key: str
    source_cell_id: str
    jurisdiction_id: str
    ahj_name: str
    state: str
    project_family: str
    classification: SeedClassification
    source_families: tuple[str, ...]
    binary_families: tuple[str, ...]
    source_cell_sha256: str
    issue_codes: tuple[str, ...]
    seed_sha256: str


@dataclass(frozen=True)
class SeedReverification:
    ok: bool
    issue_codes: tuple[str, ...]
    expected_seed_sha256: str
    actual_seed_sha256: str


@dataclass(frozen=True)
class SourcedPredicate:
    predicate_id: str
    ontology_node: str
    family: str
    operator: str
    fact_key: str
    expected_value: Any
    provenance: tuple[ProvenanceRecord, ...]
    template_id: str


@dataclass(frozen=True)
class CodeAdoptionTemplate:
    template_id: str
    code_family: str
    adoption_basis: str
    predicate_ids: tuple[str, ...]
    provenance: tuple[ProvenanceRecord, ...]


@dataclass(frozen=True)
class AHJOverlay:
    jurisdiction_id: str
    template_ids: tuple[str, ...]
    predicate_overrides: tuple[tuple[str, str], ...]
    provenance: tuple[ProvenanceRecord, ...]


_PART3_SCOPE_ONTOLOGY_ORDER = (
    "commercial_tenant_improvement",
    "residential_remodel",
    "roof_covering_replacement",
    "structural_alteration",
    "electrical_work",
    "plumbing_work",
    "mechanical_work",
    "gas_work",
    "fire_life_safety_work",
    "food_health_work",
    "liquor_service_work",
    "wastewater_work",
    "occupancy_change",
    "zoning_review",
    "demolition_work",
    "pool_work",
    "sign_work",
    "structure_moving",
    "manufactured_structure_installation",
    "septic_oss_work",
)

_PART3_PROJECT_FAMILIES = frozenset(
    {"commercial_tenant_improvement", "residential_remodel", "reroof"}
)

_PART3_TEMPLATE_BY_PROJECT = {
    "commercial_tenant_improvement": "commercial-ti-local-adoption-seed-v1",
    "residential_remodel": "residential-remodel-local-adoption-seed-v1",
    "reroof": "residential-reroof-local-adoption-seed-v1",
}

_PART3_PREDICATE_BY_PROJECT = {
    "commercial_tenant_improvement": "commercial-ti-building-seed-required-v1",
    "residential_remodel": "residential-remodel-building-seed-required-v1",
    "reroof": "reroof-building-seed-required-v1",
}

_PART3_REPRESENTATIVE_KEYS = {
    "commercial_tenant_improvement": "AK|anchorage|commercial_tenant_improvement",
    "residential_remodel": "AL|albertville|residential_remodel",
    "reroof": "AZ|buckeye|reroof",
}


def minimum_scope_ontology() -> dict[str, dict[str, Any]]:
    """Return the closed minimum production ontology in contract order."""
    patterns_by_node = {node: patterns for node, _family, patterns in _WORK_ONTOLOGY}
    family_by_node = {node: family for node, family, _patterns in _WORK_ONTOLOGY}
    broad = {
        "commercial_tenant_improvement": "commercial_tenant_improvement",
        "residential_remodel": "residential_remodel",
        "roof_covering_replacement": "reroof",
    }
    return {
        node: {
            "ontology_node": node,
            "project_family": family_by_node.get(node, broad.get(node, "unsupported")),
            "patterns": list(patterns_by_node.get(node, ())),
            "closed": True,
        }
        for node in _PART3_SCOPE_ONTOLOGY_ORDER
    }


def _cell_publishable_provenance(cell: Mapping[str, Any]) -> tuple[ProvenanceRecord, ...]:
    tier1 = _mapping(cell.get("tier1"))
    records: list[ProvenanceRecord | None] = []
    main = _mapping(tier1.get("main_decision"))
    records.extend(_provenance_from_dict(item) for item in _list(main.get("provenance")))
    if isinstance(main.get("provenance"), Mapping):
        records.append(_provenance_from_dict(main.get("provenance")))
    for collection in ("permits_required", "trade_authority", "apply"):
        for row in _list(tier1.get(collection)):
            if not isinstance(row, Mapping):
                continue
            provenance = row.get("provenance")
            if isinstance(provenance, list):
                records.extend(_provenance_from_dict(item) for item in provenance)
            else:
                records.append(_provenance_from_dict(provenance))
    return tuple(record for record in _dedupe_sorted_provenance(records) if _publishable_provenance(record))


def _main_provenance_hash_bound(cell: Mapping[str, Any]) -> bool:
    tier1 = _mapping(cell.get("tier1"))
    main = _mapping(tier1.get("main_decision"))
    raw_provenance = main.get("provenance")
    provenance = _provenance_records(raw_provenance)
    if not provenance or not all(_publishable_provenance(item) for item in provenance):
        return False
    # Legacy waves used several non-equivalent hash domains (live snapshots,
    # normalized snapshots, and aggregate source packs). Part 3 therefore
    # requires a non-placeholder SHA-256-shaped provenance hash here and binds
    # the complete immutable source cell through ``source_cell_sha256``. The
    # re-verification gate detects any subsequent change to any provenance
    # field without falsely demoting legacy seeds whose aggregate watch hash is
    # intentionally different from the main-decision snapshot hash.
    return all(
        bool(re.fullmatch(r"[0-9a-f]{64}", record.snapshot_hash))
        and len(set(record.snapshot_hash)) > 1
        for record in provenance
    )


def _binary_source_families(cell: Mapping[str, Any]) -> tuple[str, ...]:
    tier1 = _mapping(cell.get("tier1"))
    output: set[str] = set()
    for row in _list(tier1.get("permits_required")):
        if not isinstance(row, Mapping):
            continue
        family = normalize_exact_source_family(row.get("permit_kind"))
        verdict = _slug(row.get("required_status") or row.get("decision"))
        records = _provenance_records(row.get("provenance"))
        if (
            family
            and verdict in {"required", "not_required"}
            and records
            and all(_publishable_provenance(record) for record in records)
        ):
            output.add(family)
    return tuple(sorted(output))


def _source_families(cell: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                family
                for row in _list(_mapping(cell.get("tier1")).get("permits_required"))
                if isinstance(row, Mapping)
                for family in (normalize_exact_source_family(row.get("permit_kind")),)
                if family
            }
        )
    )


def seed_hash_payload(seed: MigratedSeed) -> dict[str, Any]:
    payload = to_primitive(seed)
    payload.pop("seed_sha256", None)
    return payload


def _make_seed(
    *,
    source_index_key: str,
    source_cell_id: str,
    jurisdiction_id: str,
    ahj_name: str,
    state: str,
    project_family: str,
    classification: SeedClassification,
    source_families: tuple[str, ...],
    binary_families: tuple[str, ...],
    source_cell_sha256: str,
    issue_codes: tuple[str, ...],
) -> MigratedSeed:
    base = {
        "schema_version": PART3_MIGRATION_SCHEMA_VERSION,
        "source_index_key": source_index_key,
        "source_cell_id": source_cell_id,
        "jurisdiction_id": jurisdiction_id,
        "ahj_name": ahj_name,
        "state": state,
        "project_family": project_family,
        "classification": classification,
        "source_families": source_families,
        "binary_families": binary_families,
        "source_cell_sha256": source_cell_sha256,
        "issue_codes": issue_codes,
    }
    return MigratedSeed(**base, seed_sha256=stable_sha256(to_primitive(base)))


def build_fail_closed_factory_seed(
    *,
    jurisdiction_id: str,
    ahj_name: str,
    state: str,
    project_family: str,
    source_index_key: str,
    issue_codes: Iterable[str] = ("factory_born_fail_closed",),
    source_cell: Mapping[str, Any] | None = None,
) -> MigratedSeed:
    source = dict(source_cell or {})
    return _make_seed(
        source_index_key=_normalize_text(source_index_key),
        source_cell_id=_normalize_text(source.get("cell_id")) or (
            f"factory::{_normalize_text(jurisdiction_id).lower()}::{_slug(project_family) or 'unsupported'}"
        ),
        jurisdiction_id=_normalize_text(jurisdiction_id).lower(),
        ahj_name=_normalize_text(ahj_name),
        state=_normalize_text(state).upper(),
        project_family=_slug(project_family) or "unsupported",
        classification=SeedClassification.FAIL_CLOSED,
        source_families=_source_families(source),
        binary_families=(),
        source_cell_sha256=stable_sha256(source),
        issue_codes=tuple(sorted(set(issue_codes))),
    )


def classify_v24_seed(
    source_index_key: str,
    cell: Mapping[str, Any],
    *,
    identity_ambiguous: bool = False,
) -> MigratedSeed:
    source = dict(cell)
    project_family = _slug(source.get("project_family")) or "unsupported"
    source_families = _source_families(source)
    common = {
        "source_index_key": _normalize_text(source_index_key),
        "source_cell_id": _normalize_text(source.get("cell_id")) or f"missing::{_normalize_text(source_index_key)}",
        "jurisdiction_id": _normalize_text(source.get("jurisdiction_id")).lower(),
        "ahj_name": _normalize_text(source.get("ahj")),
        "state": _normalize_text(source.get("state")).upper(),
        "project_family": project_family,
        "source_families": source_families,
        "source_cell_sha256": stable_sha256(source),
    }
    if identity_ambiguous:
        return _make_seed(
            **common,
            classification=SeedClassification.JURISDICTION_HOLD,
            binary_families=(),
            issue_codes=("jurisdiction_identity_ambiguous",),
        )
    if project_family not in _PART3_PROJECT_FAMILIES:
        return _make_seed(
            **common,
            classification=SeedClassification.UNSUPPORTED_SCOPE,
            binary_families=(),
            issue_codes=("project_family_not_in_closed_ontology",),
        )
    if source.get("status") == "FAIL_CLOSED" or source.get("serving_status") == "FAIL_CLOSED":
        return _make_seed(
            **common,
            classification=SeedClassification.FAIL_CLOSED,
            binary_families=(),
            issue_codes=("legacy_seed_explicit_fail_closed",),
        )
    validation_ok, validation_issues = request_time_validation(source)
    if not validation_ok or not _main_provenance_hash_bound(source):
        issue_codes = set(validation_issues)
        if not _main_provenance_hash_bound(source):
            issue_codes.add("main_provenance_not_hash_bound")
        return _make_seed(
            **common,
            classification=SeedClassification.FAIL_CLOSED,
            binary_families=(),
            issue_codes=tuple(sorted(issue_codes or {"legacy_seed_evidence_gate_failed"})),
        )
    coverage, _reason, _ok, coverage_issues = classify_cell_coverage(source)
    classification = (
        SeedClassification.EXACT_COMPLETE
        if coverage is CoverageStatus.EXACT_COMPLETE
        else SeedClassification.EXACT_PARTIAL
        if coverage is CoverageStatus.EXACT_PARTIAL
        else SeedClassification.FAIL_CLOSED
    )
    return _make_seed(
        **common,
        classification=classification,
        binary_families=(
            _binary_source_families(source)
            if classification in {SeedClassification.EXACT_COMPLETE, SeedClassification.EXACT_PARTIAL}
            else ()
        ),
        issue_codes=tuple(sorted(coverage_issues)),
    )


def safe_factory_migrate_seed(
    source_index_key: str,
    cell: Mapping[str, Any],
    *,
    identity_ambiguous: bool = False,
) -> MigratedSeed:
    try:
        return classify_v24_seed(
            source_index_key,
            cell,
            identity_ambiguous=identity_ambiguous,
        )
    except Exception:
        return build_fail_closed_factory_seed(
            jurisdiction_id=_normalize_text(cell.get("jurisdiction_id")),
            ahj_name=_normalize_text(cell.get("ahj")),
            state=_normalize_text(cell.get("state")),
            project_family=_normalize_text(cell.get("project_family")),
            source_index_key=source_index_key,
            issue_codes=("factory_exception",),
            source_cell=cell,
        )


def migrate_v24_seed_index(
    *,
    index: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, MigratedSeed]:
    source_index = index if index is not None else (load_v24_index() or {})
    identities: dict[tuple[str, str], set[str]] = {}
    for key in sorted(source_index):
        cell = source_index[key]
        identity_key = (_slug(cell.get("ahj")), _normalize_text(cell.get("state")).upper())
        identities.setdefault(identity_key, set()).add(_normalize_text(cell.get("jurisdiction_id")).lower())
    return {
        key: safe_factory_migrate_seed(
            key,
            source_index[key],
            identity_ambiguous=len(
                identities.get(
                    (_slug(source_index[key].get("ahj")), _normalize_text(source_index[key].get("state")).upper()),
                    set(),
                )
            )
            > 1,
        )
        for key in sorted(source_index)
    }


def reverify_migrated_seed(seed: MigratedSeed, source_cell: Mapping[str, Any]) -> SeedReverification:
    issues: set[str] = set()
    if seed.source_cell_sha256 != stable_sha256(source_cell):
        issues.add("source_cell_hash_mismatch")
    recomputed = safe_factory_migrate_seed(
        seed.source_index_key,
        source_cell,
        identity_ambiguous=seed.classification is SeedClassification.JURISDICTION_HOLD,
    )
    if seed.seed_sha256 != recomputed.seed_sha256:
        issues.add("seed_hash_mismatch")
    return SeedReverification(
        ok=not issues,
        issue_codes=tuple(sorted(issues)),
        expected_seed_sha256=seed.seed_sha256,
        actual_seed_sha256=recomputed.seed_sha256,
    )


def _overlay_promotion_gate(seed: MigratedSeed, cell: Mapping[str, Any]) -> tuple[bool, tuple[str, ...]]:
    issues: set[str] = set()
    overlay = build_ahj_overlay(cell)
    if overlay.jurisdiction_id != _normalize_text(cell.get("jurisdiction_id")).lower():
        issues.add("overlay_jurisdiction_mismatch")
    if not overlay.template_ids:
        issues.add("missing_local_adoption_template")
    if not overlay.provenance or not all(_publishable_provenance(item) for item in overlay.provenance):
        issues.add("missing_local_adoption_evidence")
    override_families = {family for family, _verdict in overlay.predicate_overrides}
    missing_overrides = set(seed.binary_families) - override_families
    if missing_overrides:
        issues.add("missing_local_administrative_override")
    route_families = {
        route.family
        for route in build_family_authority_routes(cell)
        if (
            route.authority.issuing_authority
            or route.authority.application_authority
        )
        and (
            route.application_route.apply_url
            or route.application_route.office_name
        )
    }
    if set(seed.binary_families) - route_families:
        issues.add("missing_local_authority_evidence")
    predicate = next(
        (
            item
            for item in minimum_sourced_predicates().values()
            if item.expected_value == seed.project_family
        ),
        None,
    )
    if predicate is None or evaluate_sourced_predicate(
        predicate,
        {"project_family": seed.project_family},
    ) is not True:
        issues.add("template_predicate_not_satisfied")
    return not issues, tuple(sorted(issues))


def promote_factory_seed(candidate: MigratedSeed, *, source_cell: Mapping[str, Any]) -> MigratedSeed:
    """Promote only after source, adoption, administration, and authority gates."""
    if not source_cell:
        return candidate
    identity = resolve_jurisdiction_identity(
        _normalize_text(source_cell.get("ahj")),
        _normalize_text(source_cell.get("state")),
        canonicalize_id_aliases=False,
    )
    if identity.status is not JurisdictionResolutionStatus.EXACT:
        return candidate
    migrated = safe_factory_migrate_seed(candidate.source_index_key, source_cell)
    if migrated.classification not in {
        SeedClassification.EXACT_COMPLETE,
        SeedClassification.EXACT_PARTIAL,
    }:
        return candidate
    promoted, _issues = _overlay_promotion_gate(migrated, source_cell)
    return migrated if promoted else candidate


def deterministic_seed_sample(
    index: Mapping[str, Any],
    *,
    seed: str,
    sample_size: int,
) -> tuple[str, ...]:
    if sample_size < 0:
        raise ValueError("sample_size must be non-negative")
    ranked = sorted(
        index,
        key=lambda key: (hashlib.sha256(f"{seed}\0{key}".encode("utf-8")).hexdigest(), key),
    )
    return tuple(ranked[: min(sample_size, len(ranked))])


def audit_pre_activation_family_preservation(
    index: Mapping[str, Any] | None = None,
    *,
    allowlisted_jurisdiction_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Prove exact-family preservation/routing before any jurisdiction activation."""

    source_index = dict(index or load_v24_index() or {})
    allowlist = {
        _normalize_text(item).lower()
        for item in (allowlisted_jurisdiction_ids or ())
        if _normalize_text(item)
    }
    migrated = migrate_v24_seed_index(index=source_index)
    violations: list[dict[str, Any]] = []
    checked = 0
    binary_family_count = 0
    for key, cell in sorted(source_index.items()):
        seed = migrated[key]
        if allowlist and seed.jurisdiction_id not in allowlist:
            continue
        if seed.classification not in {
            SeedClassification.EXACT_COMPLETE,
            SeedClassification.EXACT_PARTIAL,
        }:
            continue
        checked += 1
        binary_family_count += len(seed.binary_families)
        routes = {route.family for route in build_family_authority_routes(_mapping(cell))}
        missing_from_closed_ontology = sorted(set(seed.binary_families) - _CORE_FAMILIES)
        missing_from_preserved_seed = sorted(set(seed.binary_families) - set(seed.source_families))
        missing_routes = sorted(set(seed.binary_families) - routes)
        if missing_from_closed_ontology or missing_from_preserved_seed or missing_routes:
            violations.append(
                {
                    "source_index_key": key,
                    "jurisdiction_id": seed.jurisdiction_id,
                    "missing_from_closed_ontology": missing_from_closed_ontology,
                    "missing_from_preserved_seed": missing_from_preserved_seed,
                    "missing_routes": missing_routes,
                }
            )
    return {
        "schema_version": "permitassist.rule-engine-part3-preactivation-family-audit.v1",
        "checked_cells": checked,
        "binary_family_occurrences": binary_family_count,
        "violation_count": len(violations),
        "violations": violations,
        "passed": not violations,
    }


def classify_request_scope(job_type: str, job_category: str = "") -> SeedClassification:
    work = normalize_work_atoms(job_type, job_category)
    return (
        SeedClassification.EXACT_PARTIAL
        if work.valid
        else SeedClassification.UNSUPPORTED_SCOPE
    )


def _representative_provenance(project_family: str) -> tuple[ProvenanceRecord, ...]:
    index = load_v24_index() or {}
    cell = index.get(_PART3_REPRESENTATIVE_KEYS[project_family], {})
    records = _cell_publishable_provenance(cell)
    if not records:
        raise RuntimeError(f"no publishable representative provenance for {project_family}")
    return records


def minimum_sourced_predicates() -> dict[str, SourcedPredicate]:
    node_by_project = {
        "commercial_tenant_improvement": "commercial_tenant_improvement",
        "residential_remodel": "residential_remodel",
        "reroof": "roof_covering_replacement",
    }
    output: dict[str, SourcedPredicate] = {}
    for project_family in ("commercial_tenant_improvement", "residential_remodel", "reroof"):
        predicate_id = _PART3_PREDICATE_BY_PROJECT[project_family]
        output[predicate_id] = SourcedPredicate(
            predicate_id=predicate_id,
            ontology_node=node_by_project[project_family],
            family="building",
            operator="equals",
            fact_key="project_family",
            expected_value=project_family,
            provenance=_representative_provenance(project_family),
            template_id=_PART3_TEMPLATE_BY_PROJECT[project_family],
        )
    return output


def evaluate_sourced_predicate(predicate: SourcedPredicate, facts: Mapping[str, Any]) -> bool | None:
    """Evaluate a closed sourced predicate without manufacturing truth.

    Missing facts, unsupported operators, and unsourced predicates return
    ``None``. Factory promotion may proceed only on an explicit ``True``.
    """

    if predicate.operator != "equals" or predicate.fact_key not in facts:
        return None
    if not predicate.provenance or not all(_publishable_provenance(item) for item in predicate.provenance):
        return None
    return _slug(facts.get(predicate.fact_key)) == _slug(predicate.expected_value)


def minimum_code_adoption_templates() -> dict[str, CodeAdoptionTemplate]:
    code_family_by_project = {
        "commercial_tenant_improvement": "commercial_building_local_adoption",
        "residential_remodel": "residential_building_local_adoption",
        "reroof": "residential_roofing_local_adoption",
    }
    output: dict[str, CodeAdoptionTemplate] = {}
    for project_family in ("commercial_tenant_improvement", "residential_remodel", "reroof"):
        template_id = _PART3_TEMPLATE_BY_PROJECT[project_family]
        output[template_id] = CodeAdoptionTemplate(
            template_id=template_id,
            code_family=code_family_by_project[project_family],
            adoption_basis="reusable structure only; exact AHJ overlay and source evidence required",
            predicate_ids=(_PART3_PREDICATE_BY_PROJECT[project_family],),
            provenance=_representative_provenance(project_family),
        )
    return output


def build_ahj_overlay(cell: Mapping[str, Any]) -> AHJOverlay:
    project_family = _slug(cell.get("project_family"))
    template_id = _PART3_TEMPLATE_BY_PROJECT.get(project_family)
    overrides: list[tuple[str, str]] = []
    for row in _list(_mapping(cell.get("tier1")).get("permits_required")):
        if not isinstance(row, Mapping):
            continue
        family = normalize_exact_source_family(row.get("permit_kind"))
        verdict = _slug(row.get("required_status") or row.get("decision")).upper()
        if family:
            overrides.append((family, verdict or "VERIFY"))
    return AHJOverlay(
        jurisdiction_id=_normalize_text(cell.get("jurisdiction_id")).lower(),
        template_ids=(template_id,) if template_id else (),
        predicate_overrides=tuple(sorted(set(overrides))),
        provenance=_cell_publishable_provenance(cell),
    )


def _classification_for_resolution(
    resolution: V24Resolution,
    identity: JurisdictionIdentityResolution,
) -> MigratedSeed:
    cell = _mapping(resolution.cell)
    if not cell:
        classification = (
            SeedClassification.JURISDICTION_HOLD
            if identity.status is JurisdictionResolutionStatus.AMBIGUOUS
            else SeedClassification.UNSUPPORTED_SCOPE
        )
        return _make_seed(
            source_index_key=_normalize_text(resolution.key),
            source_cell_id="",
            jurisdiction_id="",
            ahj_name=identity.candidates[0].ahj_name if identity.candidates else "",
            state=identity.candidates[0].state if identity.candidates else "",
            project_family="unsupported",
            classification=classification,
            source_families=(),
            binary_families=(),
            source_cell_sha256=stable_sha256({}),
            issue_codes=(classification.value,),
        )
    return safe_factory_migrate_seed(
        _normalize_text(resolution.key),
        cell,
        identity_ambiguous=identity.status is JurisdictionResolutionStatus.AMBIGUOUS,
    )


def _complete_part3_family_decisions(
    decisions: Iterable[CoreFamilyDecision],
    project_family: str,
    classification: SeedClassification,
) -> tuple[CoreFamilyDecision, ...]:
    existing = {decision.family: decision for decision in decisions}
    if classification is SeedClassification.EXACT_PARTIAL:
        for family in sorted(FAMILY_CLOSURE_REQUIREMENTS.get(project_family, frozenset())):
            existing.setdefault(
                family,
                CoreFamilyDecision(
                    family=family,
                    verdict=FamilyVerdict.VERIFY,
                    trigger=f"{family} applicability is not closed by the exact seed",
                    provenance=(),
                    validation_issue_codes=("verified_partial_dimension_unclosed",),
                ),
            )
    elif classification in {
        SeedClassification.FAIL_CLOSED,
        SeedClassification.JURISDICTION_HOLD,
        SeedClassification.UNSUPPORTED_SCOPE,
    }:
        # A fail-closed seed must still preserve the complete customer-visible
        # family boundary. An empty family list is not a safe abstention: the
        # public projection validator rejects it, after which a later customer
        # ViewModel pass can fall through to an unrelated legacy binary answer.
        # Unsupported scopes do not have a narrower closed family ontology, so
        # keep every core family visible as an explicit abstention instead of
        # silently removing dimensions.
        issue_code = (
            "exact_cell_fail_closed"
            if classification is SeedClassification.FAIL_CLOSED
            else classification.value
        )
        for family in sorted(FAMILY_CLOSURE_REQUIREMENTS.get(project_family, _CORE_FAMILIES)):
            existing.setdefault(
                family,
                CoreFamilyDecision(
                    family=family,
                    verdict=FamilyVerdict.ABSTAIN,
                    trigger=f"{family} applicability requires official verification",
                    provenance=(),
                    validation_issue_codes=(issue_code,),
                ),
            )
    return tuple(existing[key] for key in sorted(existing))
