"""Jurisdiction-bound, sourced state permit rules."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class AuthorityModel(str, Enum):
    STATEWIDE_MANDATORY = "statewide_mandatory"
    HOME_RULE = "home_rule"
    DELEGATED_LOCAL = "delegated_local"


@dataclass(frozen=True)
class StateRule:
    rule_id: str
    state: str
    authority_model: AuthorityModel
    family: str
    status: str
    predicate: Callable[[dict[str, Any]], bool]
    source_url: str
    citation: str
    effective_date: str
    trigger: str
    effective_to: str | None = None
    source_section: str = ""
    source_quote: str = ""
    source_snapshot_path: str = ""
    source_snapshot_sha256: str = ""
    required_facts: tuple[str, ...] = ()
    disqualifying_facts: tuple[str, ...] = ()
    inspection_required: bool | None = None
    notice_required: bool | None = None
    code_compliance_required: bool = True
    conflict_status: str = "active"

    def __post_init__(self) -> None:
        if self.status not in {"REQUIRED", "CONDITIONAL", "NOT_REQUIRED", "NEEDS_INPUT", "VERIFY"}:
            raise ValueError(f"invalid state-rule status: {self.status!r}")


def _nj_detached_roof_covering(scope: dict[str, Any]) -> bool:
    text = str(scope.get("job_type") or scope.get("scope") or "").lower()
    detached = bool(re.search(r"\bdetached\b", text))
    one_two_family = bool(re.search(
        r"\b(single[- ]family|one[- ]family|two[- ]family|1[- /]2 family|one\s+or\s+two[- ]family|detached[^.;]{0,60}(?:home|house|dwelling))\b",
        text,
    ))
    roof_covering = bool(re.search(r"\b(reroof|re-roof|roof replacement|roof-covering|shingle|tear[- ]off)\b", text))
    affirmative = re.sub(
        r"\b(?:no|without|excluding)\b[^.;]{0,180}\bwork\b",
        "",
        text,
    )
    affirmative = re.sub(
        r"\b(?:no|without|excluding)\s+(?:new\s+)?(?:structural(?:\s+framing)?|framing|solar|photovoltaic|skylight|change\s+of\s+use)(?:\s+work)?\b",
        "",
        affirmative,
    )
    disqualifier = bool(re.search(r"\b(structur|framing|rafter|truss|solar|photovoltaic|skylight|roof deck replacement|change.*use)\b", affirmative))
    return detached and one_two_family and roof_covering and not disqualifier


def _nc_like_kind_door_under_threshold(scope: dict[str, Any]) -> bool:
    text = str(scope.get("job_type") or scope.get("scope") or "").lower()
    door_replacement = bool(re.search(r"\b(?:replace|replacement|replacing)\b[^.;]{0,80}\bdoor\b", text))
    like_kind = bool(re.search(r"\b(?:same size|same opening|like[- ]kind|no (?:wall )?framing|no header changes?)\b", text))
    disqualifier = bool(re.search(r"\b(?:load[- ]bearing|structural|new opening|widen|enlarge|header (?:change|replace)|fire[- ]rated)\b", text))
    raw_cost = scope.get("project_value") or scope.get("estimated_cost") or scope.get("cost")
    cost_under_threshold = False
    if isinstance(raw_cost, (int, float)):
        cost_under_threshold = 0 <= float(raw_cost) <= 40000
    elif raw_cost:
        match = re.search(r"\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)", str(raw_cost))
        if match:
            cost_under_threshold = float(match.group(1).replace(",", "")) <= 40000
    cost_under_threshold = cost_under_threshold or bool(re.search(r"\b(?:under|below|less than|not more than|at most)\s+\$?40,?000\b", text))
    return door_replacement and like_kind and cost_under_threshold and not disqualifier


STATE_RULES = (
    StateRule(
        rule_id="nc_gs_160d_1110_c_like_kind_door_under_40000",
        state="NC",
        authority_model=AuthorityModel.STATEWIDE_MANDATORY,
        family="BUILDING",
        status="NOT_REQUIRED",
        predicate=_nc_like_kind_door_under_threshold,
        source_url="https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/BySection/Chapter_160D/GS_160D-1110.html",
        citation="N.C.G.S. 160D-1110(c)(1): under the subsection's $40,000 ceiling, no permit is required for qualifying replacement of doors.",
        effective_date="2026-07-30",
        trigger="Like-kind door replacement costing $40,000 or less, with no load-bearing, structural, opening, header, or fire-rated-door change.",
        source_section="N.C.G.S. 160D-1110(c)(1)",
        source_quote="No permit issued under Article 9 of Chapter 143 of the General Statutes is required for any construction, installation, repair, replacement, or alteration costing forty thousand dollars ($40,000) or less ... However, no permit is required for replacement of windows, doors, exterior siding, or the pickets, railings, stair treads, and decking of porches and exterior decks that otherwise meet the requirements of this subsection.",
        source_snapshot_path="knowledge/state_rules/NC/gs_160d_1110_20260730.html",
        source_snapshot_sha256="4ed645c818b1577e1a92f4d5d077cb9c0b81f8619951597be36d8d8abe1c32e8",
        required_facts=("door replacement", "project cost at or below $40,000", "same opening or like-kind work"),
        disqualifying_facts=("load-bearing or structural work", "new or enlarged opening", "header work", "fire-rated assembly change"),
        inspection_required=False,
        notice_required=False,
        code_compliance_required=True,
    ),
    StateRule(
        rule_id="nj_ucc_detached_one_two_family_roof_covering_ordinary_maintenance",
        state="NJ",
        authority_model=AuthorityModel.STATEWIDE_MANDATORY,
        family="ROOFING",
        status="NOT_REQUIRED",
        predicate=_nj_detached_roof_covering,
        source_url="https://www.nj.gov/dca/codes/codreg/pdf_regs/njac_5_23_2.pdf",
        citation="N.J.A.C. 5:23-2.7(c): replacement of roof covering on detached one- or two-family dwellings is ordinary maintenance.",
        effective_date="2026-06-15",
        trigger="Detached one- or two-family roof-covering replacement with no structural, solar, skylight, or use-change work.",
        source_section="N.J.A.C. 5:23-2.7(c)1x",
        source_quote="The repair or replacement of existing roof covering on detached one- and two-family dwellings.",
        source_snapshot_path="knowledge/state_rules/NJ/njac_5_23_2_20260615.pdf",
        source_snapshot_sha256="6653a61ff58e45e7660b07d5d82ea669c81be50db5db5bdf5f9afb0fcad71e25",
        required_facts=("existing roof covering", "detached building", "one or two dwelling units"),
        disqualifying_facts=("structural or fire-safety work", "roof framing/deck replacement", "new roof construction", "commercial/mixed use", "three or more dwelling units", "new/replaced PV system"),
        inspection_required=False,
        notice_required=False,
        code_compliance_required=True,
    ),
)


def resolve_state_rule(state: str, scope: dict[str, Any]) -> StateRule | None:
    state_code = str(state or "").strip().upper()
    matches = [rule for rule in STATE_RULES if rule.state == state_code and rule.predicate(scope)]
    if not matches:
        return None
    # At an equal authority tier, a matched exemption beats a generic rule.
    matches.sort(key=lambda rule: (rule.status != "NOT_REQUIRED", rule.rule_id))
    return matches[0]
