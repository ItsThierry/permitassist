from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

try:
    from scope_contract import ScopeFactsV3, TriFact
except Exception:  # pragma: no cover
    from api.scope_contract import ScopeFactsV3, TriFact


@dataclass(frozen=True)
class FloorRule:
    rule_id: str
    predicate: Callable[[ScopeFactsV3], bool]
    families: tuple[str, ...]
    doc_floor_keys: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""


@dataclass(frozen=True)
class ForbidRule:
    rule_id: str
    predicate: Callable[[ScopeFactsV3], bool]
    families: tuple[str, ...]
    reason: str = ""


def _is_true(value: Any) -> bool:
    return getattr(value, "value", None) == TriFact.TRUE


def _is_false(value: Any) -> bool:
    return getattr(value, "value", None) == TriFact.FALSE


FLOOR_RULES: tuple[FloorRule, ...] = (
    FloorRule(
        "FLOOR_CO2",
        lambda facts: _is_true(facts.co2_enrichment) or "co2" in set(getattr(facts, "hazmat_kinds", ()) or ()),
        ("fire_hazmat_co2",),
        ("co2_system",),
        "CO2 enrichment / hazardous gas system requires fire-prevention review",
    ),
    FloorRule(
        "FLOOR_ASSEMBLY_COU",
        lambda facts: bool(getattr(facts, "change_of_use", None)) and _is_true(facts.assembly_occupancy),
        ("fire_life_safety_assembly", "co_change_of_occupancy"),
        ("assembly_life_safety",),
        "Assembly change-of-use/occupant-load scope requires fire/life-safety and occupancy review",
    ),
    FloorRule(
        "FLOOR_STRUCTURAL",
        lambda facts: _is_true(facts.structural_work),
        tuple(),
        ("structural_engineering",),
        "Structural masonry/lintel/facade scope requires engineering details",
    ),
    FloorRule(
        "FLOOR_GAS_TEST",
        lambda facts: _is_true(facts.gas_fuel_work),
        tuple(),
        ("gas_pressure_test",),
        "Fuel-gas scope requires pressure-test documentation",
    ),
)

FORBID_RULES: tuple[ForbidRule, ...] = (
    ForbidRule(
        "FORBID_RES_FOOD",
        lambda facts: getattr(facts, "segment", "") == "residential" and not _is_true(facts.food_establishment),
        ("health_food", "wastewater_pretreatment_fog"),
        "Residential non-food scope has no commercial food/FOG trigger; homeowner outdoor cooking is not a food establishment",
    ),
    ForbidRule(
        "FORBID_NO_GAS",
        lambda facts: "no_gas" in set(getattr(facts, "negative_facts", ()) or ()),
        ("gas",),
        "Explicit no-gas request fact forbids gas family",
    ),
)


def mandatory_families(facts: ScopeFactsV3 | Any) -> dict[str, str]:
    out = dict(getattr(facts, "mandatory_family_floors", {}) or {})
    if not isinstance(facts, ScopeFactsV3):
        return out
    for rule in FLOOR_RULES:
        if rule.predicate(facts):
            for family in rule.families:
                out[family] = rule.reason
    return out


def forbidden_families(facts: ScopeFactsV3 | Any) -> dict[str, str]:
    out = dict(getattr(facts, "forbidden_families", {}) or {})
    if not isinstance(facts, ScopeFactsV3):
        return out
    for rule in FORBID_RULES:
        if rule.predicate(facts):
            for family in rule.families:
                out[family] = rule.reason
    return out


def document_floor_keys(facts: ScopeFactsV3 | Any) -> dict[str, str]:
    out = dict(getattr(facts, "required_documents_floor", {}) or {})
    if not isinstance(facts, ScopeFactsV3):
        return out
    for rule in FLOOR_RULES:
        if rule.predicate(facts):
            for key in rule.doc_floor_keys:
                out[key] = rule.reason
    return out
