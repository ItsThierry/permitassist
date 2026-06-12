"""PermitAssist v2.3.1 Decision Cell resolver.

This module is the single source of truth for runtime v2.3.1 Decision Cell
resolution and late reconciliation. It is intentionally deterministic and
fail-safe: if the index is missing/corrupt, if AHJ/project matching is not exact,
or if project-family classification is ambiguous, callers get no authoritative
cell and the normal PermitAssist pipeline continues unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import copy
import json
import re
from pathlib import Path
from typing import Any


RESOLVER_VERSION = "v231-runtime-resolver-2026-06-02"
INDEX_VERSION = "v2.3.1-national-baseline-2026-06-02"

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INDEX_PATH = ROOT_DIR / "knowledge" / "permitassist_decision_cell_index_v231.json"


class ResolutionStatus(str, Enum):
    EXACT_CELL_COVERED = "exact_cell_covered"
    AHJ_COVERED_PROJECT_NOT_COVERED = "ahj_covered_project_not_covered"
    AHJ_NOT_COVERED = "ahj_not_covered"
    AMBIGUOUS_ABSTAIN = "ambiguous_abstain"
    INDEX_UNAVAILABLE = "index_unavailable"


@dataclass(frozen=True)
class V231Resolution:
    status: ResolutionStatus
    cell: dict[str, Any] | None = None
    key: str | None = None
    project_candidates: tuple[str, ...] = ()
    reason: str = ""
    index_version: str = INDEX_VERSION
    resolver_version: str = RESOLVER_VERSION


_INDEX_CACHE: dict[str, Any] = {}
_INDEX_CACHE_SOURCE: Path | None = None
_INDEX_CACHE_MTIME_NS: int | None = None
_INDEX_CACHE_LOAD_FAILED: bool = False


def normalize_ahj_key(city: str) -> str:
    """Return the exact runtime AHJ city slug used in the v2.3.1 index."""
    return re.sub(r"[^a-z0-9]+", "_", (city or "").lower()).strip("_")


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").lower()).strip()


def _term_pattern(term: str) -> str:
    """Return a safe phrase pattern with alphanumeric word boundaries.

    The resolver must abstain rather than route substring accidents such as
    ``waterproofing`` -> ``roofing`` or ``anti corrosion`` -> ``ti``.
    Spaces in project-family phrases intentionally accept spaces or hyphens so
    customer input like ``tenant-improvement`` still resolves to TI.
    """
    pieces = [re.escape(piece) for piece in re.split(r"[\s-]+", term.strip()) if piece]
    body = r"[\s-]+".join(pieces) if pieces else re.escape(term.strip())
    return rf"(?<![a-z0-9]){body}(?![a-z0-9])"


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(re.search(_term_pattern(term), text) for term in terms)


def classify_project_candidates(job_type: str, job_category: str = "") -> list[str]:
    """Classify request text to safe v2.3.1 project-family candidates.

    The function abstains rather than guessing from broad category labels alone.
    It also abstains on mixed project-family signals (for example TI + reroof)
    because an automatic resolver must not choose one authoritative cell for a
    multi-scope request.
    """
    text = _normalize_text(job_type)
    category = _normalize_text(job_category)
    combined = f"{text} {category}".strip()
    if not text:
        return []

    candidates: list[str] = []
    commercial_context_terms = (
        "commercial",
        "office",
        "retail",
        "restaurant",
        "clinic",
        "tenant",
        "business",
    )

    reroof_terms = (
        "reroof",
        "re-roof",
        "roof replacement",
        "replace roof",
        "roof tear-off",
        "tear off roof",
        "tear-off roof",
        "new roof shingles",
        "roofing",
    )
    if _has_any(combined, reroof_terms):
        candidates.append("reroof")

    commercial_construction_explicit_terms = (
        "new commercial construction",
        "commercial new construction",
        "new commercial building",
        "commercial building construction",
        "commercial plan review",
        "building plan review",
        "retail shell",
        "commercial retail shell",
        "shell building",
        "commercial shell",
        "core and shell",
        "core-and-shell",
        "commercial construction",
        "new construction retail",
    )
    commercial_construction_broad_terms = (
        "ground-up",
        "ground up",
        "new building",
    )
    if _has_any(combined, commercial_construction_explicit_terms) or (
        _has_any(text, commercial_construction_broad_terms)
        and (category == "commercial" or _has_any(combined, commercial_context_terms))
    ):
        candidates.append("commercial_construction")

    commercial_ti_explicit_terms = (
        "tenant improvement",
        "tenant improvements",
        "ti",
        "t.i.",
        "buildout",
        "build-out",
        "fit out",
        "fit-out",
        "office tenant improvement",
        "office ti",
        "retail tenant improvement",
        "retail ti",
        "restaurant tenant improvement",
        "restaurant ti",
        "clinic tenant improvement",
        "clinic ti",
        "commercial building permit",
        "commercial building project",
    )
    commercial_ti_generic_terms = (
        "interior alteration",
        "interior remodel",
        "commercial interior remodel",
        "interior commercial remodel",
        "commercial interior alteration",
        "interior commercial alteration",
    )
    construction_project_review_terms = (
        "commercial construction plan review",
        "building plan review",
        "plan review",
        "change of occupancy",
        "change of use",
    )
    construction_project_review = "commercial_construction" in candidates and _has_any(combined, construction_project_review_terms)
    if _has_any(combined, commercial_ti_explicit_terms) or (
        not construction_project_review
        and _has_any(text, commercial_ti_generic_terms)
        and (category == "commercial" or _has_any(combined, commercial_context_terms))
    ):
        candidates.append("commercial_tenant_improvement")

    residential_remodel_terms = (
        "residential remodel",
        "residential interior remodel",
        "residential interior remodeling",
        "residential interior renovation",
        "residential interior alteration",
        "home remodel",
        "kitchen remodel",
        "bathroom remodel",
        "bath remodel",
        "interior remodel",
        "interior remodeling",
        "interior renovation",
        "residential renovation",
        "home renovation",
        "residential alteration",
        "kitchen renovation",
        "bathroom renovation",
    )
    if _has_any(combined, residential_remodel_terms) and (category == "residential" or "residential" in combined or "home" in combined or "kitchen" in combined or "bath" in combined):
        candidates.append("residential_remodel")

    unique_candidates = list(dict.fromkeys(candidates))
    if len(unique_candidates) == 1:
        return unique_candidates
    # Broad category labels, vague words, or mixed project-family signals are not safe enough.
    return []


def load_v231_index(index_path: str | Path | None = None) -> dict[str, dict[str, Any]] | None:
    """Load the runtime index, returning None for any unavailable/corrupt shape."""
    global _INDEX_CACHE, _INDEX_CACHE_SOURCE, _INDEX_CACHE_MTIME_NS, _INDEX_CACHE_LOAD_FAILED

    path = Path(index_path) if index_path is not None else DEFAULT_INDEX_PATH
    try:
        stat = path.stat()
    except OSError:
        if index_path is None:
            _INDEX_CACHE = {}
            _INDEX_CACHE_SOURCE = path
            _INDEX_CACHE_MTIME_NS = None
            _INDEX_CACHE_LOAD_FAILED = True
        return None

    if (
        index_path is None
        and _INDEX_CACHE_SOURCE == path
        and _INDEX_CACHE_MTIME_NS == stat.st_mtime_ns
        and not _INDEX_CACHE_LOAD_FAILED
    ):
        return _INDEX_CACHE

    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        if index_path is None:
            _INDEX_CACHE = {}
            _INDEX_CACHE_SOURCE = path
            _INDEX_CACHE_MTIME_NS = stat.st_mtime_ns
            _INDEX_CACHE_LOAD_FAILED = True
        return None

    entries = obj.get("index") if isinstance(obj, dict) else None
    if not isinstance(entries, dict):
        if index_path is None:
            _INDEX_CACHE = {}
            _INDEX_CACHE_SOURCE = path
            _INDEX_CACHE_MTIME_NS = stat.st_mtime_ns
            _INDEX_CACHE_LOAD_FAILED = True
        return None

    normalized: dict[str, dict[str, Any]] = {}
    for key, cell in entries.items():
        if not isinstance(key, str) or not isinstance(cell, dict):
            return None
        normalized[key] = cell

    if index_path is None:
        _INDEX_CACHE = normalized
        _INDEX_CACHE_SOURCE = path
        _INDEX_CACHE_MTIME_NS = stat.st_mtime_ns
        _INDEX_CACHE_LOAD_FAILED = False
    return normalized


def _ahj_has_any_project(index: dict[str, dict[str, Any]], state_key: str, city_key: str) -> bool:
    prefix = f"{state_key}|{city_key}|"
    return any(key.startswith(prefix) for key in index)


def _publishable_customer_cell(cell: Any) -> bool:
    return isinstance(cell, dict) and cell.get("publish_status") == "PUBLISHABLE" and cell.get("main_decision") in {"REQUIRED", "NOT_REQUIRED"}


def _commercial_construction_alias_cell(cell: dict[str, Any]) -> bool:
    """Return True when a compressed TI runtime key is truly construction/building scope.

    The Run3 import intentionally compressed many richer commercial building/
    construction slugs into the broad runtime bucket
    ``commercial_tenant_improvement``.  The resolver may use that compressed key
    for explicit commercial-construction customer phrasing only when the row's
    own metadata shows construction/building/plan-review scope.  Pure TI rows
    stay TI-only; ambiguous "commercial work" still abstains.
    """
    blob = " ".join(str(cell.get(field) or "") for field in (
        "project_type_slug",
        "project_type_label",
        "decision_subject",
        "permit_name",
        "customer_action",
    )).lower().replace("_", " ").replace("-", " ")
    construction_markers = (
        "construction",
        "building/construction",
        "building plan review",
        "commercial building",
        "state plan review",
        "change of occupancy",
        "change of use",
        "core and shell",
        "shell building",
    )
    return any(marker in blob for marker in construction_markers)


def _resolve_publishable_cell_from_index(
    index: dict[str, dict[str, Any]],
    state_key: str,
    city_key: str,
    project_slug: str,
    candidates: tuple[str, ...],
) -> V231Resolution | None:
    key = f"{state_key}|{city_key}|{project_slug}"
    cell = index.get(key)
    if isinstance(cell, dict):
        if _publishable_customer_cell(cell):
            return V231Resolution(
                ResolutionStatus.EXACT_CELL_COVERED,
                cell=copy.deepcopy(cell),
                key=key,
                project_candidates=candidates,
                reason="exact publishable AHJ/project cell",
            )
        return V231Resolution(
            ResolutionStatus.AHJ_COVERED_PROJECT_NOT_COVERED,
            key=key,
            project_candidates=candidates,
            reason="cell exists but is not publishable/customer-concrete",
        )
    return None


def resolve_v231_cell(
    city: str,
    state: str,
    job_type: str,
    job_category: str = "",
    *,
    index_path: str | Path | None = None,
) -> V231Resolution:
    """Resolve a publishable v2.3.1 cell by exact AHJ + safe project family."""
    state_key = (state or "").strip().upper()
    city_key = normalize_ahj_key(city)
    if not city_key or not state_key:
        return V231Resolution(ResolutionStatus.AMBIGUOUS_ABSTAIN, reason="missing city or state")

    index = load_v231_index(index_path=index_path)
    if not index:
        return V231Resolution(ResolutionStatus.INDEX_UNAVAILABLE, reason="v2.3.1 index unavailable")

    candidates = tuple(classify_project_candidates(job_type, job_category))
    if not candidates:
        status = ResolutionStatus.AMBIGUOUS_ABSTAIN if _ahj_has_any_project(index, state_key, city_key) else ResolutionStatus.AHJ_NOT_COVERED
        return V231Resolution(status, project_candidates=(), reason="project family ambiguous or unsupported")

    for project_slug in candidates:
        resolved = _resolve_publishable_cell_from_index(index, state_key, city_key, project_slug, candidates)
        if resolved is not None:
            return resolved

    if "commercial_construction" in candidates:
        alias_key = f"{state_key}|{city_key}|commercial_tenant_improvement"
        alias_cell = index.get(alias_key)
        if isinstance(alias_cell, dict) and _publishable_customer_cell(alias_cell) and _commercial_construction_alias_cell(alias_cell):
            return V231Resolution(
                ResolutionStatus.EXACT_CELL_COVERED,
                cell=copy.deepcopy(alias_cell),
                key=alias_key,
                project_candidates=candidates,
                reason="commercial construction resolved through compressed commercial_tenant_improvement runtime bucket",
            )

    first_key = f"{state_key}|{city_key}|{candidates[0]}"
    if _ahj_has_any_project(index, state_key, city_key):
        return V231Resolution(
            ResolutionStatus.AHJ_COVERED_PROJECT_NOT_COVERED,
            key=first_key,
            project_candidates=candidates,
            reason="AHJ covered but project family not covered",
        )
    return V231Resolution(
        ResolutionStatus.AHJ_NOT_COVERED,
        key=first_key,
        project_candidates=candidates,
        reason="AHJ not covered by v2.3.1 index",
    )


def get_v231_coverage_status(city: str, state: str, job_type: str, job_category: str = "") -> ResolutionStatus:
    return resolve_v231_cell(city, state, job_type, job_category).status


def _primary_source(cell: dict[str, Any]) -> dict[str, Any]:
    evidence = cell.get("source_evidence") or []
    return evidence[0] if evidence and isinstance(evidence[0], dict) else {}


def _cell_source_urls(cell: dict[str, Any], apply_url: str = "") -> list[dict[str, str]]:
    """Return trusted v2.3.1 Decision Cell source URLs for public provenance.

    Decision Cells have already passed the import/source gates; runtime locality
    filters can be too strict for small-city domains/abbreviated hosts. Preserve
    this vetted provenance structurally so a customer-visible exact decision never
    degrades into a bare REQUIRED/NOT_REQUIRED answer with no source path.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(url: Any, title: Any = "", quote: Any = "") -> None:
        value = str(url or "").strip()
        if not value.startswith("http") or value in seen:
            return
        seen.add(value)
        out.append({
            "url": value,
            "title": str(title or "Official AHJ source").strip() or "Official AHJ source",
            "quote": str(quote or "").strip(),
            "source": "permitassist_v231_decision_cell",
            "trusted_decision_cell_source": "true",
        })

    for source in cell.get("source_evidence") or []:
        if not isinstance(source, dict):
            continue
        add(
            source.get("final_url") or source.get("source_url") or source.get("url"),
            source.get("title") or source.get("source_title") or source.get("final_title"),
            source.get("quote") or source.get("exact_quote_or_snippet") or source.get("snippet"),
        )
    if apply_url:
        add(apply_url, "Official AHJ application/start page", "")
    return out


def _cell_authority_fields(cell: dict[str, Any]) -> tuple[str, str, str, str, str]:
    authority_obj = cell.get("authority_model")
    authority: dict[str, Any] = authority_obj if isinstance(authority_obj, dict) else {}
    source = _primary_source(cell)
    apply_url = authority.get("application_url") or source.get("final_url") or source.get("url") or ""
    office = authority.get("application_authority") or authority.get("issuing_authority") or cell.get("ahj_name") or ""
    permit_name = cell.get("permit_name") or "Building Permit"
    source_url = source.get("final_url") or source.get("source_url") or source.get("url") or apply_url
    quote = source.get("quote") or source.get("exact_quote_or_snippet") or source.get("snippet") or ""
    return permit_name, office, apply_url, source_url, quote


def _normalize_permit_type(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    text = text.replace(" permit", "").replace("permit ", "")
    if "building" in text or "construction" in text:
        return "building"
    return text


def _dedupe_sources(sources: list[Any]) -> list[Any]:
    seen: set[str] = set()
    deduped: list[Any] = []
    for source in sources:
        url = source.get("url") if isinstance(source, dict) else str(source)
        key = (url or "").strip()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        deduped.append(source)
    return deduped


def _pipeline_has_required_safety_signal(result: dict[str, Any]) -> bool:
    """Detect existing PermitAssist signals that must not be suppressed by NO.

    A source-backed v2.3.1 NOT_REQUIRED cell should beat a generic building/TI
    default from the normal pipeline. It must *not* beat concrete safety signals:
    hidden triggers, companion trade/life-safety permits, or a non-generic
    required permit already found by the engine. This keeps the product capable
    while preventing generic REQUIRED fallbacks from overruling exact no-permit
    decision cells everywhere.
    """
    hidden = result.get("hidden_triggers")
    if isinstance(hidden, list) and any(isinstance(trigger, dict) for trigger in hidden):
        return True

    trade_permits = result.get("trade_permits")
    if isinstance(trade_permits, list):
        for permit in trade_permits:
            if isinstance(permit, dict) and permit.get("required") is True:
                return True
            if isinstance(permit, str) and permit.strip():
                return True

    generic_required_keys = {
        "building",
        "construction",
        "commercial_tenant_improvement",
        "commercial_building_tenant_improvement",
        "commercial_building_interior_alteration",
    }
    permits = result.get("permits_required")
    if isinstance(permits, list):
        for permit in permits:
            if not (isinstance(permit, dict) and permit.get("required") is True):
                continue
            name = permit.get("permit_type") or permit.get("portal_selection") or permit.get("kind")
            key = _normalize_permit_type(name)
            dedupe_key = _normalize_permit_type(str(name or "").replace("/", " "))
            family_key = _normalize_permit_name_for_safety(name)
            if key in {"electrical", "plumbing", "mechanical", "fire", "sprinkler", "health", "zoning"}:
                return True
            if family_key not in generic_required_keys and dedupe_key not in {"building", "construction"}:
                return True
    return False


def _normalize_permit_name_for_safety(name: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()
    tokens = [token for token in text.split() if token not in {"permit", "permits", "required"}]
    token_set = set(tokens)
    if {"commercial", "building", "tenant", "improvement"} & token_set and ("tenant" in token_set or "improvement" in token_set):
        return "commercial_building_tenant_improvement"
    if "tenant" in token_set and ("improvement" in token_set or "buildout" in token_set):
        return "commercial_tenant_improvement"
    if "interior" in token_set and "alteration" in token_set and "commercial" in token_set:
        return "commercial_building_interior_alteration"
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
    if "fire" in token_set or "sprinkler" in token_set:
        return "fire"
    if "health" in token_set:
        return "health"
    if "zoning" in token_set:
        return "zoning"
    return "_".join(tokens)


RI_STATEWIDE_BUILDING_PERMIT_SOURCES: tuple[dict[str, str], ...] = (
    {
        "url": "https://webserver.rilegislature.gov/Statutes/TITLE23/23-27.3/23-2/23-27.3-113.1.htm",
        "title": "R.I. Gen. Laws § 23-27.3-113.1 official permit-required statute",
        "publisher": "Rhode Island General Assembly",
        "snippet": "It shall be unlawful to construct, enlarge, alter, remove, or demolish a building, or change the occupancy of a building ... without first filing an application with the building official in writing and obtaining the required permit therefor; except that ordinary repairs ... shall be exempt.",
    },
    {
        "url": "https://webserver.rilegislature.gov/Statutes/TITLE23/23-27.3/23-2/23-27.3-115.6.htm",
        "title": "R.I. Gen. Laws § 23-27.3-115.6 official electronic construction permitting statute",
        "publisher": "Rhode Island General Assembly",
        "snippet": "Every municipality in the state ... shall adopt and implement electronic construction permitting.",
    },
    {
        "url": "https://rhodeisland.portal.opengov.com/",
        "title": "Rhode Island official electronic construction permitting portal",
        "publisher": "Rhode Island electronic construction permitting",
        "snippet": "Official statewide e-permitting intake portal used by Rhode Island municipalities.",
    },
)


def _has_public_source_urls(result: dict[str, Any]) -> bool:
    urls = result.get("source_urls")
    if isinstance(urls, list) and any(isinstance(url, str) and url.startswith("http") for url in urls):
        return True
    sources = result.get("sources")
    if isinstance(sources, list):
        return any(isinstance(source, dict) and str(source.get("url") or "").startswith("http") for source in sources)
    return False


def _resolution_state_from_key(resolution: V231Resolution) -> str:
    key = str(resolution.key or "")
    return key.split("|", 1)[0].upper() if "|" in key else ""


def _apply_nonpublishable_boundary_source_floor(result: dict[str, Any], resolution: V231Resolution) -> dict[str, Any]:
    """Attach only genuinely statewide sources for non-customer v2.3.1 boundaries.

    Boundary rows are not promoted to exact Decision Cell answers. If the normal
    engine independently returns REQUIRED for a source-less Rhode Island building
    alteration/construction boundary, the statewide code/e-permitting sources
    substantiate the generic REQUIRED claim without faking local cell coverage or
    leaking internal publishability tokens.
    """
    if resolution.status != ResolutionStatus.AHJ_COVERED_PROJECT_NOT_COVERED:
        return result
    if _resolution_state_from_key(resolution) != "RI":
        return result
    if str(result.get("permit_decision") or "").upper() != "REQUIRED" and str(result.get("permit_verdict") or "").upper() != "YES":
        return result
    if _has_public_source_urls(result):
        return result
    candidates = {str(candidate or "") for candidate in resolution.project_candidates}
    if not candidates.intersection({"commercial_tenant_improvement", "commercial_construction", "residential_remodel", "residential_addition", "residential_alteration"}):
        return result

    existing_sources_obj = result.get("sources")
    existing_sources = existing_sources_obj if isinstance(existing_sources_obj, list) else []
    sources = [copy.deepcopy(source) for source in RI_STATEWIDE_BUILDING_PERMIT_SOURCES]
    seen = {source["url"] for source in sources}
    for source in existing_sources:
        if isinstance(source, dict):
            url = str(source.get("url") or "")
            if url.startswith("http") and url not in seen:
                seen.add(url)
                sources.append(source)
    result["sources"] = sources
    result["source_urls"] = [source["url"] for source in sources]
    result["source_floor"] = "statewide_public_code_source_floor"
    result["source_confidence"] = result.get("source_confidence") or "STATEWIDE_CODE"
    return result


def reconcile_v231_result(result: dict[str, Any], resolution: V231Resolution | dict[str, Any] | None) -> dict[str, Any]:
    """Late authoritative merge for an exact v2.3.1 cell.

    Exact publishable cells win only on regulated/main-decision fields. Existing
    PermitAssist enrichments (hidden triggers, rulebooks, fees, checklists,
    rejection patterns, source enrichment, UI gates) are preserved.
    """
    if not isinstance(result, dict):
        return result

    if isinstance(resolution, dict):
        resolution = V231Resolution(ResolutionStatus.EXACT_CELL_COVERED, cell=resolution)
    if not isinstance(resolution, V231Resolution):
        return result
    if resolution.status != ResolutionStatus.EXACT_CELL_COVERED:
        return _apply_nonpublishable_boundary_source_floor(result, resolution)

    cell = resolution.cell or {}
    if cell.get("publish_status") != "PUBLISHABLE" or cell.get("main_decision") not in {"REQUIRED", "NOT_REQUIRED"}:
        return result

    main_decision = cell.get("main_decision")
    permit_required = main_decision == "REQUIRED"
    permit_name, office, apply_url, source_url, quote = _cell_authority_fields(cell)

    if main_decision == "NOT_REQUIRED" and _pipeline_has_required_safety_signal(result):
        field_sources = dict(result.get("_field_sources") or {}) if isinstance(result.get("_field_sources"), dict) else {}
        field_sources["permit_required"] = "pipeline_safety_signal"
        field_sources["permit_verdict"] = "pipeline_safety_signal"
        field_sources["permit_decision"] = "pipeline_safety_signal"
        result["_field_sources"] = field_sources
        result["_v231_resolution_status"] = "not_required_safety_conflict"
        result["_v231_cell_id"] = cell.get("cell_id")
        result["_v231_conflict_reconciliation"] = [{
            "field": "permit_required",
            "pipeline_value": result.get("permit_required"),
            "cell_value": False,
            "reason": "v2.3.1 NOT_REQUIRED cell refused because pipeline required-permit or hidden-trigger safety signal is present",
        }]
        return result

    # Authoritative regulated fields.
    old_values = {
        "permit_verdict": result.get("permit_verdict"),
        "permit_required": result.get("permit_required"),
        "permit_decision": result.get("permit_decision"),
        "permit_name": result.get("permit_name"),
        "applying_office": result.get("applying_office"),
        "apply_url": result.get("apply_url"),
    }
    result["permit_verdict"] = "YES" if permit_required else "NO"
    result["permit_required"] = permit_required
    result["permit_decision"] = main_decision
    if permit_required:
        result["permit_name"] = permit_name
    if office:
        result["applying_office"] = office
    if apply_url:
        result["apply_url"] = apply_url
    if permit_required:
        result.pop("not_required_reason", None)
        result.pop("no_permit_required_reason", None)
    result["confidence"] = result.get("confidence") or "high"
    result["confidence_reason"] = "Official AHJ source-backed permit decision for this exact jurisdiction and project type."

    decision_cell_sources = _cell_source_urls(cell, apply_url)
    authority = cell.get("authority_model") if isinstance(cell.get("authority_model"), dict) else {}
    if authority:
        if authority.get("authority_type"):
            result["authority_model_type"] = authority.get("authority_type")
        if authority.get("issuing_authority"):
            result["issuing_authority_name"] = authority.get("issuing_authority")
        if authority.get("application_authority"):
            result["application_authority_name"] = authority.get("application_authority")
        if authority.get("application_url"):
            result["application_authority_url"] = authority.get("application_url")
        if authority.get("delegation_note"):
            result["delegation_note"] = authority.get("delegation_note")

    result["_v231_decision_cell"] = {
        "cell_id": cell.get("cell_id"),
        "jurisdiction_id": cell.get("jurisdiction_id"),
        "project_type_slug": cell.get("project_type_slug"),
        "main_decision": cell.get("main_decision"),
        "publish_status": cell.get("publish_status"),
        "evidence_level": cell.get("evidence_level"),
        "source_url": source_url,
        "source_quote": quote[:500] if isinstance(quote, str) else "",
        "resolver_version": RESOLVER_VERSION,
        "index_version": INDEX_VERSION,
    }
    result["_v231_resolver_version"] = RESOLVER_VERSION
    result["_v231_index_version"] = INDEX_VERSION
    result["_v231_cell_id"] = cell.get("cell_id")
    result["_v231_resolution_status"] = resolution.status.value

    backlog = cell.get("enrichment_backlog")
    if isinstance(backlog, list):
        existing_obj = result.get("_v231_enrichment_backlog")
        existing_backlog: list[Any] = existing_obj if isinstance(existing_obj, list) else []
        merged_backlog: list[Any] = []
        for item in [*existing_backlog, *backlog]:
            if item not in merged_backlog:
                merged_backlog.append(item)
        result["_v231_enrichment_backlog"] = merged_backlog

    permits_obj = result.get("permits_required")
    permits: list[dict[str, Any]] = [copy.deepcopy(p) for p in permits_obj if isinstance(p, dict)] if isinstance(permits_obj, list) else []
    primary_permit = None
    if permit_required:
        primary_notes = cell.get("customer_action") or (f"Apply with {office} before starting work." if office else "Apply before starting work.")
        if isinstance(primary_notes, str):
            primary_notes = primary_notes.replace("tenant-improvement", "tenant improvement")

        primary_permit = {
            "permit_type": permit_name,
            "required": True,
            "permit_kind": cell.get("permit_kind") or "building",
            "portal_selection": permit_name,
            "notes": primary_notes,
            "source": "permitassist_v231_decision_cell",
        }
        primary_norm = _normalize_permit_type(permit_name)
        upgraded = False
        for permit in permits:
            if _normalize_permit_type(permit.get("permit_type")) == primary_norm:
                permit.update(primary_permit)
                upgraded = True
                break
        if not upgraded:
            permits.insert(0, primary_permit)
    result["permits_required"] = permits
    result["_decision_cell_primary_lock"] = {
        "source": "permitassist_v231_decision_cell",
        "exact_match": True,
        "cell_id": cell.get("cell_id"),
        "permit_decision": main_decision,
        "permit_required": permit_required,
        "permit_name": permit_name if permit_required else "No permit required",
        "permit_kind": cell.get("permit_kind") or ("building" if permit_required else "not_required"),
        "apply_url": apply_url,
        "applying_office": office,
        "primary_permit": copy.deepcopy(primary_permit) if primary_permit else None,
        "customer_action": cell.get("customer_action") or (primary_permit or {}).get("notes") or "",
        "source_urls": [src.get("url") for src in decision_cell_sources if src.get("url")],
        "sources": copy.deepcopy(decision_cell_sources),
    }

    sources_obj = result.get("sources")
    sources: list[Any] = list(sources_obj) if isinstance(sources_obj, list) else []
    if decision_cell_sources:
        sources = [*copy.deepcopy(decision_cell_sources), *sources]
    elif source_url:
        sources.insert(0, {"url": source_url, "title": _primary_source(cell).get("title") or "Official AHJ source", "source": "permitassist_v231_decision_cell", "trusted_decision_cell_source": "true"})
    result["sources"] = _dedupe_sources(sources)
    if decision_cell_sources:
        existing_source_urls = [url for url in result.get("source_urls") or [] if isinstance(url, str)] if isinstance(result.get("source_urls"), list) else []
        merged_source_urls: list[str] = []
        for url in [*[src.get("url") for src in decision_cell_sources], *existing_source_urls]:
            if isinstance(url, str) and url and url not in merged_source_urls:
                merged_source_urls.append(url)
        result["source_urls"] = merged_source_urls

    field_sources = dict(result.get("_field_sources") or {}) if isinstance(result.get("_field_sources"), dict) else {}
    for field in ("permit_verdict", "permit_required", "permit_decision", "permit_name", "permits_required", "applying_office", "apply_url"):
        if field == "permit_name" and not permit_required:
            continue
        if result.get(field) is not None:
            field_sources[field] = "permitassist_v231_decision_cell"
    result["_field_sources"] = field_sources

    conflicts = []
    for field, old_value in old_values.items():
        new_value = result.get(field)
        if old_value not in (None, "") and old_value != new_value:
            conflicts.append({"field": field, "pipeline_value": old_value, "cell_value": new_value, "reason": "v2.3.1 exact Decision Cell authoritative field precedence"})
    if conflicts:
        result["_v231_conflict_reconciliation"] = conflicts

    return result


def apply_v231_decision_cell_overlay(result: dict[str, Any], job_type: str, city: str, state: str, job_category: str = "") -> dict[str, Any]:
    """Compatibility wrapper for older callers; delegates to the resolver."""
    return reconcile_v231_result(result, resolve_v231_cell(city, state, job_type, job_category))


def build_v231_prompt_context(resolution: V231Resolution | None) -> str:
    """Build compact grounding context for the model prompt.

    Late reconciliation remains the final authority; this text is only to reduce
    model drift for covered exact cells.
    """
    if not isinstance(resolution, V231Resolution) or resolution.status != ResolutionStatus.EXACT_CELL_COVERED or not resolution.cell:
        return ""
    cell = resolution.cell
    if cell.get("main_decision") != "REQUIRED":
        return ""
    permit_name, office, apply_url, source_url, quote = _cell_authority_fields(cell)
    quote_line = f"Official quote: {quote[:700]}" if quote else "Official source quote is stored in the Decision Cell evidence."
    return (
        "=== AUTHORITATIVE v2.3.1 DECISION CELL CONTEXT (GROUNDING ONLY) ===\n"
        f"Cell: {cell.get('cell_id')}\n"
        f"Exact AHJ/project: {cell.get('city')}, {cell.get('state')} / {cell.get('project_type_label') or cell.get('project_type_slug')}\n"
        f"Main decision: {cell.get('main_decision')} for {permit_name}.\n"
        f"Apply through: {office}.\n"
        f"Apply URL/source: {apply_url or source_url}.\n"
        f"{quote_line}\n"
        "Run the normal PermitAssist pipeline; do not short-circuit hidden triggers, rulebooks, fees, checklists, rejection patterns, sources, or UI gates. "
        "Late reconciliation, not this prompt text, is the final authority for regulated fields. Keep internal resolver/version labels out of the customer response.\n"
        "=== END DECISION CELL CONTEXT ==="
    )
