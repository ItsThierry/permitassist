"""Typed evidence-pack adapters for PermitAssist state/vertical overlays.

Sprint 3 deliberately keeps this small: it wraps the existing Phase 4 state
schema rules into a typed, repeatable interface instead of creating a parallel
JSON tree or refactoring state_schema.py wholesale. Local AHJ apply portals stay
out of this module; these packs represent state/vertical guidance only.
"""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

try:  # package import in tests/app
    from .state_schema import STATE_RULE_SCHEMAS, get_state_rule_schema
except ImportError:  # direct script import
    from state_schema import STATE_RULE_SCHEMAS, get_state_rule_schema


CORE_EVIDENCE_FIELDS = (
    "permit_type",
    "apply_url",
    "fee_range",
    "approval_timeline",
    "inspections",
)

READINESS_LEVELS = ("verified", "partial", "needs_verification")


@dataclass(frozen=True)
class FieldEvidenceDefault:
    field: str
    readiness: str
    confidence: str
    source_type: str
    supports_field: bool
    note: str


@dataclass(frozen=True)
class EvidenceRule:
    id: str
    title: str
    overlay: str
    summary: str
    source_url: str
    source_title: str
    source_quote: str
    confidence: str
    applies: str


@dataclass(frozen=True)
class EvidencePack:
    state: str
    state_name: str
    verticals: tuple[str, ...]
    active_vertical: str
    overlay_rules: tuple[EvidenceRule, ...]
    field_defaults: tuple[FieldEvidenceDefault, ...]
    last_verified: str
    readiness: str
    source_system: str
    coverage_level: str
    population_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "state_name": self.state_name,
            "verticals": list(self.verticals),
            "active_vertical": self.active_vertical,
            "overlay_rules": [rule.__dict__.copy() for rule in self.overlay_rules],
            "field_defaults": [default.__dict__.copy() for default in self.field_defaults],
            "last_verified": self.last_verified,
            "readiness": self.readiness,
            "source_system": self.source_system,
            "coverage_level": self.coverage_level,
            "population_status": self.population_status,
        }


def _default_field_evidence(readiness: str = "needs_verification") -> tuple[FieldEvidenceDefault, ...]:
    if readiness not in READINESS_LEVELS:
        raise ValueError(f"invalid readiness default: {readiness}")
    confidence = "high" if readiness == "verified" else "medium" if readiness == "partial" else "low"
    supports_field = readiness == "verified"
    return tuple(
        FieldEvidenceDefault(
            field=field,
            readiness=readiness,
            confidence=confidence,
            source_type="state_vertical_overlay",
            supports_field=supports_field,
            note=(
                "State/vertical evidence is planning support only; local AHJ field proof is still required "
                "before marking this customer-visible field high confidence."
            ),
        )
        for field in CORE_EVIDENCE_FIELDS
    )


def _schema_group_for_vertical(vertical: str) -> str:
    return "healthcare_overlays" if vertical == "medical_clinic_ti" else "general_overlays"


def _rule_matches_vertical(raw_rule: dict[str, Any], vertical: str) -> bool:
    """Avoid cross-vertical leakage if general overlays later hold multiple verticals."""
    declared_scope = str(raw_rule.get("vertical_scope") or "").strip().lower()
    if declared_scope:
        return declared_scope == vertical
    declared_verticals = {str(v).strip().lower() for v in raw_rule.get("verticals") or [] if str(v).strip()}
    if declared_verticals:
        return vertical in declared_verticals
    if vertical == "medical_clinic_ti":
        return True
    # General overlays can serve multiple verticals. Require explicit tagging so
    # generic words like "office" in a source quote cannot leak a rule to office TI.
    return False


def _rules_from_schema(schema: dict[str, Any], vertical: str) -> tuple[EvidenceRule, ...]:
    group_name = _schema_group_for_vertical(vertical)
    rules: list[EvidenceRule] = []
    for slot in (schema.get(group_name) or {}).values():
        for raw_rule in slot.get("populated_rules") or []:
            if not _rule_matches_vertical(raw_rule, vertical):
                continue
            rules.append(
                EvidenceRule(
                    id=str(raw_rule.get("id") or ""),
                    title=str(raw_rule.get("title") or ""),
                    overlay=str(raw_rule.get("overlay") or ""),
                    summary=str(raw_rule.get("summary") or ""),
                    source_url=str(raw_rule.get("source_url") or ""),
                    source_title=str(raw_rule.get("source_title") or ""),
                    source_quote=str(raw_rule.get("source_quote") or ""),
                    confidence=str(raw_rule.get("confidence") or "medium"),
                    applies=str(raw_rule.get("applies") or ""),
                )
            )
    return tuple(rules)


def _last_verified_from_schema(schema: dict[str, Any], vertical: str) -> str:
    group_name = _schema_group_for_vertical(vertical)
    verified = []
    for slot in (schema.get(group_name) or {}).values():
        for hook in slot.get("citation_hooks") or []:
            declared_scope = str(hook.get("vertical_scope") or "").strip().lower()
            if declared_scope and declared_scope != vertical:
                continue
            if hook.get("citation_status") == "verified" and hook.get("verified_on"):
                verified.append(str(hook["verified_on"]))
    return max(verified) if verified else ""


def build_evidence_pack_from_state_schema(schema: dict[str, Any], *, active_vertical: str = "medical_clinic_ti") -> EvidencePack:
    """Wrap an existing state schema into the Sprint 3 typed pack interface."""
    if not isinstance(schema, dict):
        raise ValueError("schema must be a dict")
    active_vertical = (active_vertical or "").strip().lower()
    populated_verticals = tuple(str(v) for v in (schema.get("populated_verticals") or []))
    if active_vertical not in populated_verticals:
        raise ValueError(f"{schema.get('state')} has no populated evidence for {active_vertical}")
    rules = _rules_from_schema(schema, active_vertical)
    if not rules:
        raise ValueError(f"{schema.get('state')} {active_vertical} has no populated rules")
    coverage_level = str(
        (schema.get("vertical_coverage_levels") or {}).get(active_vertical)
        or schema.get("coverage_level")
        or ""
    )
    pack = EvidencePack(
        state=str(schema.get("state") or ""),
        state_name=str(schema.get("state_name") or ""),
        verticals=populated_verticals,
        active_vertical=active_vertical,
        overlay_rules=rules,
        field_defaults=_default_field_evidence("needs_verification"),
        last_verified=_last_verified_from_schema(schema, active_vertical),
        readiness="partial",
        source_system="state_schema_adapter",
        coverage_level=coverage_level,
        population_status=str(schema.get("population_status") or ""),
    )
    errors = validate_evidence_pack(pack)
    if errors:
        raise ValueError(f"invalid evidence pack for {pack.state} {active_vertical}: {errors}")
    return pack


_TX_RESTAURANT_TI_EXAMPLE = EvidencePack(
    state="TX",
    state_name="Texas",
    verticals=("restaurant_ti",),
    active_vertical="restaurant_ti",
    overlay_rules=(
        EvidenceRule(
            id="tx_restaurant_dshs_retail_food_permit_local_jurisdiction",
            title="Texas retail food establishment permit and local health-department split",
            overlay="food_health_department",
            summary=(
                "Texas restaurant TI should verify whether the city/county health department or DSHS permits "
                "the retail food establishment, while building/plumbing/electrical/fire/zoning remain local AHJ items."
            ),
            source_url="https://www.dshs.texas.gov/retail-food-establishments/permitting-information-retail-food-establishments/starting-a-new-retail",
            source_title="Texas DSHS — Starting a New Retail Food Establishment Under DSHS Jurisdiction",
            source_quote=(
                "There are many local health departments in the State of Texas. You should contact your city or county "
                "office to determine if they permit facilities in your area."
            ),
            confidence="medium",
            applies="tx_restaurant_ti_health_permit_coordination",
        ),
        EvidenceRule(
            id="tx_restaurant_tas_dining_surfaces_accessibility",
            title="Texas Accessibility Standards dining and work surface check",
            overlay="accessibility",
            summary=(
                "Restaurant dining, counter, and service layouts should keep TAS dining/work-surface accessibility in the plan-check checklist."
            ),
            source_url="https://www.tdlr.texas.gov/ab/2012abtas9.htm",
            source_title="TDLR — Texas Accessibility Standards Chapter 9: Built-In Elements",
            source_quote=(
                "The tops of dining surfaces and work surfaces shall be 28 inches (710 mm) minimum and 34 inches (865 mm) maximum above the finish floor or ground."
            ),
            confidence="medium",
            applies="tx_restaurant_ti_accessibility_dining_surfaces",
        ),
    ),
    field_defaults=_default_field_evidence("needs_verification"),
    last_verified="2026-05-02",
    readiness="partial",
    source_system="manual_state_vertical_example",
    coverage_level="sprint3_tx_restaurant_ti_example",
    population_status="partially_populated",
)


_MANUAL_EXAMPLE_PACKS: dict[tuple[str, str], EvidencePack] = {
    ("TX", "restaurant_ti"): _TX_RESTAURANT_TI_EXAMPLE,
}


def get_evidence_pack(state: str, vertical: str = "medical_clinic_ti") -> EvidencePack | None:
    """Return a typed state/vertical pack, without local AHJ portal claims."""
    state_upper = (state or "").strip().upper()
    vertical_key = (vertical or "").strip().lower()
    manual = _MANUAL_EXAMPLE_PACKS.get((state_upper, vertical_key))
    if manual:
        return deepcopy(manual)
    schema = get_state_rule_schema(state_upper)
    if not schema:
        return None
    try:
        return build_evidence_pack_from_state_schema(schema, active_vertical=vertical_key)
    except ValueError:
        return None


def list_evidence_packs() -> tuple[EvidencePack, ...]:
    """List currently populated typed evidence packs for scoring/audit scripts."""
    packs: list[EvidencePack] = []
    for state in sorted(STATE_RULE_SCHEMAS):
        pack = get_evidence_pack(state, "medical_clinic_ti")
        if pack:
            packs.append(pack)
    packs.extend(deepcopy(pack) for pack in _MANUAL_EXAMPLE_PACKS.values())
    return tuple(packs)


def validate_evidence_pack(pack: EvidencePack) -> list[str]:
    """Deterministic safety checks for Sprint 3 typed packs."""
    errors: list[str] = []
    if pack.active_vertical not in pack.verticals:
        errors.append("active_vertical must be included in verticals")
    if pack.readiness not in READINESS_LEVELS:
        errors.append("readiness must be verified, partial, or needs_verification")
    if pack.readiness == "verified":
        errors.append("state/vertical evidence packs must not be marked verified until local AHJ field proof exists")
    if not pack.last_verified:
        errors.append("last_verified is required")
    if not pack.overlay_rules:
        errors.append("at least one overlay rule is required")
    fields = {default.field for default in pack.field_defaults}
    missing = set(CORE_EVIDENCE_FIELDS) - fields
    if missing:
        errors.append(f"field_defaults missing core fields: {sorted(missing)}")
    for default in pack.field_defaults:
        if default.readiness not in READINESS_LEVELS:
            errors.append(f"{default.field} has invalid readiness {default.readiness}")
        if default.readiness == "verified" and (not default.supports_field or default.source_type == "state_vertical_overlay"):
            errors.append(f"{default.field} cannot be verified by state/vertical overlay defaults alone")
    for rule in pack.overlay_rules:
        if not rule.id or not rule.title:
            errors.append("every rule needs id and title")
        if not rule.source_url or not rule.source_title or not rule.source_quote:
            errors.append(f"{rule.id or rule.title} needs source_url, source_title, and source_quote")
        source_url = str(rule.source_url or "")
        parsed_url = urlparse(source_url)
        if source_url and parsed_url.scheme not in {"http", "https"}:
            errors.append(f"{rule.id or rule.title} source_url must use http or https")
        if source_url and not parsed_url.netloc:
            errors.append(f"{rule.id or rule.title} source_url must include a hostname")
        if rule.confidence == "high" and not rule.source_quote:
            errors.append(f"{rule.id} high confidence requires direct source_quote")
    return errors
