"""PermitAssist 2.0 national filing-grade workflow helpers.

This module is intentionally deterministic and local-only. It does not fetch
sources, mutate production data, or promote evidence without quote-backed field
support. It gives the runtime/tests a single contract for:
- AHJ × trade × scope evidence-cell promotion gates;
- private manual-completion research tickets;
- static benchmark release gates; and
- verified-auto / safe-interim / invalid-AHJ routing.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from usefulness_contract import SAFE_INTERIM_PERMIT_LABEL, score_against_static_baseline

OFFICIAL_SOURCE_TYPES = {"official_ahj", "official_state", "delegated_official"}
DIRECT_FIELD_SUPPORT = {"direct_quote", "quoted_field", "official_quote"}
MAX_FRESHNESS_DAYS = 365
PRIVATE_TICKET_KEYS = {"candidate_sources", "tried_urls", "suggested_queries", "missing_fields"}


@dataclass(frozen=True)
class EvidenceCellEvaluation:
    """Result of evaluating one AHJ×trade×scope evidence cell."""

    status: str
    can_drive_auto_answer: bool
    reasons: tuple[str, ...]
    cell: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "can_drive_auto_answer": self.can_drive_auto_answer,
            "reasons": list(self.reasons),
            "cell": dict(self.cell),
        }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else ([] if value in (None, "") else [value])


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "permitassist20"


def evaluate_evidence_cell(cell: dict[str, Any]) -> EvidenceCellEvaluation:
    """Fail-closed promotion gate for one evidence graph cell.

    A promoted cell must have an official/delegated source, a non-empty exact
    quote/snippet, direct field support, trade+scope identity, and non-stale
    freshness metadata. Anything weaker remains usable as a research clue but
    cannot drive verified automatic customer answers.
    """

    reasons: list[str] = []
    source_quote = _text(cell.get("source_quote") or cell.get("quote") or cell.get("snippet"))
    if not source_quote:
        reasons.append("missing_source_quote")
    if _text(cell.get("source_type")).lower() not in OFFICIAL_SOURCE_TYPES:
        reasons.append("non_official_source")
    if _text(cell.get("field_support")).lower() not in DIRECT_FIELD_SUPPORT:
        reasons.append("insufficient_field_support")
    if not _text(cell.get("trade")) or not _text(cell.get("scope")):
        reasons.append("missing_trade_scope")
    freshness_days = cell.get("freshness_days")
    expires_in = cell.get("freshness_expires_in_days")
    try:
        if freshness_days is not None and int(freshness_days) > MAX_FRESHNESS_DAYS:
            reasons.append("stale_or_expired_source")
    except (TypeError, ValueError):
        reasons.append("stale_or_expired_source")
    try:
        if expires_in is not None and int(expires_in) < 0:
            reasons.append("stale_or_expired_source")
    except (TypeError, ValueError):
        reasons.append("stale_or_expired_source")

    reasons = sorted(set(reasons))
    promoted = not reasons
    return EvidenceCellEvaluation(
        status="promoted" if promoted else "needs_verification",
        can_drive_auto_answer=promoted,
        reasons=tuple(reasons),
        cell=dict(cell),
    )


def create_research_ticket(
    *,
    scenario: str,
    ahj_stack: list[str],
    detected_scopes: list[str],
    candidate_sources: list[dict[str, Any]],
    missing_fields: list[str],
    tried_urls: list[str],
    suggested_queries: list[str] | None = None,
    owner: str = "research",
    sla_hours: int = 24,
) -> dict[str, Any]:
    """Create a private manual-completion ticket for long-tail gaps."""

    ticket = {
        "scenario": _text(scenario),
        "ahj_stack": [_text(v) for v in _list(ahj_stack) if _text(v)],
        "detected_scopes": [_text(v) for v in _list(detected_scopes) if _text(v)],
        "candidate_sources": [dict(v) for v in candidate_sources if isinstance(v, dict)],
        "missing_fields": [_text(v) for v in _list(missing_fields) if _text(v)],
        "tried_urls": [_text(v) for v in _list(tried_urls) if _text(v)],
        "suggested_queries": [_text(v) for v in _list(suggested_queries or []) if _text(v)],
        "owner": _text(owner) or "research",
        "sla_hours": int(sla_hours),
        "customer_visible_status": SAFE_INTERIM_PERMIT_LABEL,
        "status": "manual_completion_required",
    }
    required = [
        "scenario",
        "ahj_stack",
        "detected_scopes",
        "candidate_sources",
        "missing_fields",
        "tried_urls",
        "suggested_queries",
        "owner",
        "sla_hours",
    ]
    missing = [key for key in required if not ticket.get(key)]
    if missing:
        raise ValueError(f"research ticket missing required fields: {', '.join(missing)}")
    ticket["ticket_id"] = _stable_id("pa20_ticket", ticket)
    return ticket


def write_research_ticket(ticket: dict[str, Any], ticket_dir: str | Path) -> Path:
    """Write a private ticket artifact to a caller-supplied directory."""

    output_dir = Path(ticket_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ticket_id = _text(ticket.get("ticket_id")) or _stable_id("pa20_ticket", ticket)
    path = output_dir / f"{_slug(ticket_id)}.json"
    path.write_text(json.dumps(ticket, indent=2, sort_keys=True) + "\n")
    return path


def _promoted_cells(cells: Iterable[dict[str, Any]]) -> list[EvidenceCellEvaluation]:
    return [evaluation for cell in cells if (evaluation := evaluate_evidence_cell(cell)).can_drive_auto_answer]


def _fields_from_cells(cells: list[EvidenceCellEvaluation]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for evaluation in cells:
        cell = evaluation.cell
        field = _text(cell.get("field"))
        if field and field not in fields:
            fields[field] = cell.get("value")
    return fields


def _candidate_missing_fields(cells: Iterable[dict[str, Any]]) -> list[str]:
    wanted = {"portal_category", "official_application_title", "apply_url", "inspection_sequence"}
    present = {_text(cell.get("field")) for cell in cells if _text(cell.get("field"))}
    return sorted(wanted - present) or ["official_application_title"]


def customer_result_for_manual(*, scenario: str, ahj_stack: list[str], route: str) -> dict[str, Any]:
    """Return the safe customer-visible result for manual/interim states."""

    return {
        "permit_verdict": "YES" if route != "invalid_ahj_fail_closed" else "UNKNOWN",
        "permit_required": True if route != "invalid_ahj_fail_closed" else None,
        "permit_type": SAFE_INTERIM_PERMIT_LABEL,
        "permit_name": SAFE_INTERIM_PERMIT_LABEL,
        "permit_type_verified": False,
        "permit_name_status": "needs_manual_filing_path_confirmation",
        "permit_name_confidence": "low",
        "customer_visible_status": SAFE_INTERIM_PERMIT_LABEL,
        "warnings": [
            "Manual filing path check is in progress for this lookup; confirm the final filing category with the AHJ before submitting."
        ],
        "scenario_summary": _text(scenario),
        "ahj_summary": " / ".join(_text(v) for v in ahj_stack if _text(v)),
    }


def route_lookup_outcome(
    *,
    scenario: str,
    ahj_stack: list[str],
    detected_scopes: list[str],
    evidence_cells: list[dict[str, Any]],
    candidate_sources: list[dict[str, Any]],
    tried_urls: list[str],
    ticket_dir: str | Path,
    suggested_queries: list[str] | None = None,
    ahj_valid: bool = True,
) -> dict[str, Any]:
    """Route a long-tail lookup into verified auto, safe interim, or fail-closed."""

    if not ahj_valid:
        customer_result = customer_result_for_manual(scenario=scenario, ahj_stack=ahj_stack, route="invalid_ahj_fail_closed")
        return {
            "route": "invalid_ahj_fail_closed",
            "permit_type": SAFE_INTERIM_PERMIT_LABEL,
            "permit_name": SAFE_INTERIM_PERMIT_LABEL,
            "permit_type_verified": False,
            "manual_completion_ticket": None,
            "customer_result": customer_result,
        }

    promoted = _promoted_cells(evidence_cells)
    if promoted:
        fields = _fields_from_cells(promoted)
        permit_type = _text(fields.get("official_application_title") or fields.get("portal_category") or promoted[0].cell.get("value"))
        source_cell = promoted[0].cell
        return {
            "route": "verified_auto",
            "permit_type": permit_type,
            "permit_name": permit_type,
            "permit_type_verified": True,
            "source_support": [item.as_dict() for item in promoted],
            "customer_result": {
                "permit_verdict": "YES",
                "permit_required": True,
                "permit_type": permit_type,
                "permit_name": permit_type,
                "permit_type_verified": True,
                "apply_path": {
                    "permit_type": permit_type,
                    "portal_url": source_cell.get("source_url"),
                    "support_level": "verified path",
                },
                "sources": [
                    {
                        "url": source_cell.get("source_url"),
                        "title": source_cell.get("source_title"),
                        "snippet": source_cell.get("source_quote") or source_cell.get("snippet"),
                    }
                ],
            },
        }

    missing_fields = _candidate_missing_fields(evidence_cells)
    ticket = create_research_ticket(
        scenario=scenario,
        ahj_stack=ahj_stack,
        detected_scopes=detected_scopes,
        candidate_sources=candidate_sources,
        missing_fields=missing_fields,
        tried_urls=tried_urls,
        suggested_queries=suggested_queries or [f"official permit application {' '.join(ahj_stack)} {' '.join(detected_scopes)}"],
    )
    ticket_path = write_research_ticket(ticket, ticket_dir)
    customer_result = customer_result_for_manual(scenario=scenario, ahj_stack=ahj_stack, route="safe_interim_manual")
    return {
        "route": "safe_interim_manual",
        "permit_type": SAFE_INTERIM_PERMIT_LABEL,
        "permit_name": SAFE_INTERIM_PERMIT_LABEL,
        "permit_type_verified": False,
        "manual_completion_ticket": str(ticket_path),
        "customer_result": customer_result,
    }


def run_benchmark_gate(cases: list[dict[str, Any]], *, ticket_dir: str | Path) -> dict[str, Any]:
    """Deterministic static benchmark release gate.

    A case fails when PermitAssist does not beat the static baseline. Every loss
    emits a private research ticket artifact with owner/root-cause/dimension.
    """

    case_results: list[dict[str, Any]] = []
    losses: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        case_id = _text(case.get("case_id")) or f"case-{index + 1}"
        comparison = score_against_static_baseline(
            case.get("permitassist") or {},
            _text(case.get("baseline_text")),
            job_type=_text(case.get("job_type") or case.get("scenario")),
            city=_text(case.get("city")),
            state=_text(case.get("state")),
        )
        result = {
            "case_id": case_id,
            "permitassist_score": comparison["permitassist_score"],
            "baseline_score": comparison["baseline_score"],
            "winner": comparison["winner"],
        }
        case_results.append(result)
        if comparison["winner"] != "permitassist":
            root_cause = comparison["losses"][0] if comparison.get("losses") else "PermitAssist lost to static baseline."
            ticket = create_research_ticket(
                scenario=_text(case.get("scenario") or case_id),
                ahj_stack=[_text(case.get("city") or "unknown AHJ")],
                detected_scopes=[_text(case.get("job_type") or "benchmark_loss")],
                candidate_sources=[{"title": "benchmark loss", "url": "local://benchmark"}],
                missing_fields=["benchmark_actionability", "exact_filing_path"],
                tried_urls=["local://benchmark"],
                suggested_queries=["review PermitAssist benchmark loss root cause"],
                owner=_text(case.get("owner")) or "research",
                sla_hours=int(case.get("sla_hours") or 24),
            )
            artifact = write_research_ticket(ticket, ticket_dir)
            losses.append(
                {
                    "case_id": case_id,
                    "owner": ticket["owner"],
                    "root_cause": root_cause,
                    "rubric_dimension": "contractor_actionability",
                    "ticket_artifact": str(artifact),
                }
            )
    return {
        "release_gate": "fail" if losses else "pass",
        "case_results": case_results,
        "losses": losses,
    }
