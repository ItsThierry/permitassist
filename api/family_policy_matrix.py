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


def _canonical_family(family: Any) -> str:
    raw = str(family or "").strip().lower().replace("-", "_").replace("/", "_").replace(" ", "_")
    aliases = {
        "structural": "building",
        "building_structural": "building",
        "site_civil": "grading",
        "right_of_way": "grading",
        "row": "grading",
        "health": "health_food",
        "food": "health_food",
        "pool": "health_food",
        "fire": "fire_suppression",
        "fire_life_safety": "fire_suppression",
        "zoning": "planning_zoning",
        "planning": "planning_zoning",
        "co": "co_change_of_occupancy",
        "certificate_of_occupancy": "co_change_of_occupancy",
    }
    return aliases.get(raw, raw)


def _request_positive_floors(facts: ScopeFactsV3 | Any) -> dict[str, str]:
    """ScopeFactsV4 request-derived floor matrix.

    These floors are deliberately derived from request-positive family facts, not
    rendered/model/cache text.  They provide the deterministic lower bound that
    caught the Live100 core-truth missing-family class.
    """
    positive = {_canonical_family(f) for f in (getattr(facts, "request_positive_families", ()) or ())}
    positive_facts = set(getattr(facts, "positive_facts", ()) or ())
    negative = set(getattr(facts, "negative_facts", ()) or ())
    segment = str(getattr(facts, "segment", "") or "").lower()
    out: dict[str, str] = {}
    reason = "verified request-derived ScopeFactsV4 family floor"
    contradictory_negative = {
        "electrical": {"no_mep", "no_electrical", "cosmetic_only"},
        "plumbing": {"no_mep", "no_plumbing", "cosmetic_only"},
        "mechanical": {"no_mep", "no_mechanical", "cosmetic_only"},
        "building": {"no_structural", "no_walls", "cosmetic_only"},
        "building_ti": {"no_structural", "no_walls", "cosmetic_only", "no_use_change"},
        "co_change_of_occupancy": {"no_use_change", "cosmetic_only"},
        "planning_zoning": {"no_use_change", "cosmetic_only"},
        "sign": {"no_signage", "cosmetic_only"},
    }
    for family in sorted(positive):
        if family in {"", "planning_zoning"}:
            continue
        # Planning/zoning can be address/use dependent; only floor it for explicit
        # use-change/site/sign facts, not as a generic companion.
        if family in {_canonical_family(f) for f in (getattr(facts, "request_negative_families", ()) or ())}:
            # Do not turn a phrase like "no plumbing electrical" into a hard
            # floor.  Explicit implications below (e.g. fuel-gas branch ⇒
            # plumbing/gas path) can still add a floor with a better basis.
            continue
        if contradictory_negative.get(family, set()) & negative:
            continue
        out[family] = reason
    if {"building", "building_ti"} & positive:
        out.setdefault("building_ti" if segment == "commercial" and ("building_ti" in positive or "change_of_use_ti" in positive_facts or "commercial_ti" in positive_facts) else "building", "building/structural work floor from request facts")
    if "gas" in positive:
        out.setdefault("plumbing", "fuel-gas scope requires plumbing/gas permit-family floor")
    if positive & {"fire_suppression", "fire_alarm"}:
        out.setdefault("fire_suppression", "fire/life-safety scope requires fire-family floor")
    if "health_food" in positive:
        out.setdefault("health_food", "health/food/pool scope requires health-family floor")
    if "use_change" in positive_facts or "co_change_of_occupancy" in positive or "building_ti" in positive:
        if "no_use_change" not in negative:
            out.setdefault("co_change_of_occupancy", "change-of-use/occupancy scope requires occupancy-review floor")
            if segment == "commercial":
                out.setdefault("building_ti", "commercial change-of-use/TI scope requires building/TI floor")
    if "sign" in positive:
        out.setdefault("sign", "signage scope requires sign-family floor")
    return {k: v for k, v in out.items() if k}


def _request_negative_ceilings(facts: ScopeFactsV3 | Any, floors: dict[str, str]) -> dict[str, str]:
    negative_families = {_canonical_family(f) for f in (getattr(facts, "request_negative_families", ()) or ())}
    negative_facts = set(getattr(facts, "negative_facts", ()) or ())
    segment = str(getattr(facts, "segment", "") or "").lower()
    out: dict[str, str] = {}
    def forbid(family: str, reason: str) -> None:
        family = _canonical_family(family)
        # Fable precedence: explicit floor beats ceiling for the same family;
        # conflict is reported by the validator, not silently forbidden here.
        if family and family not in floors:
            out[family] = reason
    if "no_mep" in negative_facts:
        for family in ("electrical", "plumbing", "mechanical"):
            forbid(family, "explicit no-MEP request ceiling")
    if "no_electrical" in negative_facts or "electrical" in negative_families:
        forbid("electrical", "explicit no-electrical request ceiling")
    if "no_plumbing" in negative_facts or "plumbing" in negative_families:
        forbid("plumbing", "explicit no-plumbing request ceiling")
        forbid("wastewater_pretreatment_fog", "explicit no-plumbing request ceiling")
    if "no_mechanical" in negative_facts or "mechanical" in negative_families:
        forbid("mechanical", "explicit no-mechanical/HVAC request ceiling")
    if "no_structural" in negative_facts or "structural" in negative_families:
        forbid("building", "explicit no-structural request ceiling")
        forbid("building_ti", "explicit no-structural request ceiling")
    if "no_signage" in negative_facts or "sign" in negative_families:
        forbid("sign", "explicit no-signage request ceiling")
    if "no_food_service_change" in negative_facts or (segment == "residential" and "health_food" not in floors):
        for family in ("health_food", "wastewater_pretreatment_fog", "liquor"):
            forbid(family, "no food-service/public health trigger request ceiling")
    if "no_use_change" in negative_facts:
        forbid("co_change_of_occupancy", "explicit no-use-change request ceiling")
        forbid("planning_zoning", "explicit no-use-change request ceiling")
    if "cosmetic_only" in negative_facts:
        for family in ("building", "building_ti", "co_change_of_occupancy", "planning_zoning", "electrical", "plumbing", "mechanical", "fire_suppression"):
            forbid(family, "cosmetic-only/no-trade request ceiling")
    return out


def mandatory_families(facts: ScopeFactsV3 | Any) -> dict[str, str]:
    out = {_canonical_family(k): str(v) for k, v in dict(getattr(facts, "mandatory_family_floors", {}) or {}).items() if _canonical_family(k)}
    negative_facts = set(getattr(facts, "negative_facts", ()) or ())
    negative_families = {_canonical_family(f) for f in (getattr(facts, "request_negative_families", ()) or ())}
    if "no_use_change" in negative_facts:
        out.pop("co_change_of_occupancy", None)
        # Keep zoning only when the request has an independent site/sign/zoning
        # trigger; do not inherit it from stale change-of-use floors.
        positive = {_canonical_family(f) for f in (getattr(facts, "request_positive_families", ()) or ())}
        if not (positive & {"sign", "grading", "planning_zoning"}):
            out.pop("planning_zoning", None)
    if "no_electrical" in negative_facts or "electrical" in negative_families:
        out.pop("electrical", None)
    if "no_plumbing" in negative_facts or "plumbing" in negative_families:
        out.pop("plumbing", None)
        out.pop("wastewater_pretreatment_fog", None)
    if "no_mechanical" in negative_facts or "mechanical" in negative_families:
        out.pop("mechanical", None)
    if not isinstance(facts, ScopeFactsV3):
        return out
    out.update(_request_positive_floors(facts))
    for rule in FLOOR_RULES:
        if rule.predicate(facts):
            for family in rule.families:
                out[_canonical_family(family)] = rule.reason
    return out


def forbidden_families(facts: ScopeFactsV3 | Any) -> dict[str, str]:
    floors = mandatory_families(facts)
    out = {_canonical_family(k): str(v) for k, v in dict(getattr(facts, "forbidden_families", {}) or {}).items() if _canonical_family(k) and _canonical_family(k) not in floors}
    if not isinstance(facts, ScopeFactsV3):
        return out
    out.update(_request_negative_ceilings(facts, floors))
    for rule in FORBID_RULES:
        if rule.predicate(facts):
            for family in rule.families:
                fam = _canonical_family(family)
                if fam not in floors:
                    out[fam] = rule.reason
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
