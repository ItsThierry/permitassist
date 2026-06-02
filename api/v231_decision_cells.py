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
    )
    commercial_ti_generic_terms = (
        "interior alteration",
        "interior remodel",
    )
    if _has_any(combined, commercial_ti_explicit_terms) or (
        _has_any(text, commercial_ti_generic_terms) and (category == "commercial" or _has_any(combined, commercial_context_terms))
    ):
        candidates.append("commercial_tenant_improvement")

    residential_remodel_terms = (
        "residential remodel",
        "home remodel",
        "kitchen remodel",
        "bathroom remodel",
        "bath remodel",
        "residential renovation",
        "home renovation",
        "residential alteration",
        "kitchen renovation",
        "bathroom renovation",
    )
    if _has_any(combined, residential_remodel_terms):
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
        key = f"{state_key}|{city_key}|{project_slug}"
        cell = index.get(key)
        if isinstance(cell, dict):
            if cell.get("publish_status") == "PUBLISHABLE" and cell.get("main_decision") in {"REQUIRED", "NOT_REQUIRED"}:
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


def _cell_authority_fields(cell: dict[str, Any]) -> tuple[str, str, str, str, str]:
    authority_obj = cell.get("authority_model")
    authority: dict[str, Any] = authority_obj if isinstance(authority_obj, dict) else {}
    source = _primary_source(cell)
    apply_url = authority.get("application_url") or source.get("final_url") or source.get("url") or ""
    office = authority.get("application_authority") or authority.get("issuing_authority") or cell.get("ahj_name") or ""
    permit_name = cell.get("permit_name") or "Building Permit"
    source_url = source.get("final_url") or source.get("url") or apply_url
    quote = source.get("quote") or ""
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

    v2.3.1 NOT_REQUIRED cells are allowed to make a covered exact no-permit
    decision only when the normal engine has not already found a required
    permit, trade-scope permit, or hidden trigger. This prevents contradictory
    customer payloads such as ``permit_required=False`` beside required
    electrical/plumbing/mechanical permits.
    """
    if result.get("permit_required") is True:
        return True
    if str(result.get("permit_verdict") or "").upper() == "YES":
        return True
    if str(result.get("permit_decision") or "").upper() == "REQUIRED":
        return True
    permits = result.get("permits_required")
    if isinstance(permits, list):
        for permit in permits:
            if isinstance(permit, dict) and permit.get("required") is True:
                return True
    hidden = result.get("hidden_triggers")
    if isinstance(hidden, list) and any(isinstance(trigger, dict) for trigger in hidden):
        return True
    return False


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
    if not isinstance(resolution, V231Resolution) or resolution.status != ResolutionStatus.EXACT_CELL_COVERED:
        return result

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
    }

    sources_obj = result.get("sources")
    sources: list[Any] = list(sources_obj) if isinstance(sources_obj, list) else []
    if source_url:
        sources.insert(0, {"url": source_url, "title": _primary_source(cell).get("title") or "Official AHJ source"})
    result["sources"] = _dedupe_sources(sources)

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
