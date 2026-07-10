"""Canonical request scope contract and customer-visible scope firebreaks.

The contract is computed from the original user request at lookup entry and is
safe to pass downstream. It exists to prevent later helpers from re-detecting
scope from already-contaminated model/cache text.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
import os
import re
from typing import Any

_NEGATION_RE = re.compile(
    r"(?:\bno\b|\bwithout\b|\bnot\b|\bnone of\b|\bexcludes?\b|\bexcluding\b|\bdoes not include\b|\bdoesn't include\b|\bno new\b|\bnon[- ]+)"
    r"(?:\s+(?:and|or|any|new|commercial|residential|type\s*i|type\s*1|solar|pv))*"
    r"(?:[\s,;/()-]+[a-z0-9]+){0,5}[\s,;/()-]*$",
    re.I,
)

_COMMERCIAL_TERMS = (
    "commercial", "tenant improvement", "tenant finish", "tenant buildout", "buildout", "build-out",
    "restaurant", "commercial kitchen", "commercial alteration", "commercial building", "type i hood", "type 1 hood", "grease interceptor",
    "medical clinic", "dental clinic", "office tenant improvement", "office ti", "retail tenant improvement",
    "change of occupancy", "change of use", "retail conversion", "retail converted", "retail space", "retail suite",
    "fitness studio", "storefront", "clinic tenant improvement", "commercial fit-out", "commercial fit out",
)
_STRONG_COMMERCIAL_TERMS = tuple(term for term in _COMMERCIAL_TERMS if term != "commercial")
_RESIDENTIAL_TERMS = (
    "residential", "single-family", "single family", "single family home", "single-family home",
    "dwelling", "home", "house", "townhouse", "duplex",
)
_PANEL_TERMS = ("panel upgrade", "service upgrade", "electrical panel", "main panel", "meter main", "200 amp", "200a", "400 amp", "400a", "subpanel", "sub-panel")
_PANEL_ACTION_TERMS = (
    "panel upgrade", "service upgrade", "upgrade panel", "upgrade service", "replace panel",
    "panel replacement", "new panel", "new electrical panel", "new main panel", "meter main",
    "subpanel", "sub-panel", "service change", "service size change",
)
_SOLAR_TERMS = ("solar", "pv", "photovoltaic", "solarapp")
_HVAC_TERMS = ("hvac", "air conditioner", "air conditioning", "a/c", "heat pump", "furnace", "mini split", "mini-split", "condenser")
_HVAC_SPECIFIC_TERMS = ("hvac", "air conditioner", "air conditioning", "a/c", "furnace", "mini split", "mini-split", "condenser", "ductwork", "ducting", "air handler")
_WATER_HEATER_TERMS = ("water heater",)
_ROOF_TERMS = ("reroof", "re-roof", "roof replacement", "tear-off", "tear off", "shingle roof")
_EV_CHARGER_TERMS = ("ev charger", "electric vehicle charger", "level 2 charger", "level ii charger", "car charger")
_WINDOW_TERMS = ("window replacement", "replace window", "replace windows", "replace same-size windows", "replace same size windows", "same-size window", "same-size windows", "same size window", "same size windows")
_FENCE_TERMS = ("fence", "privacy fence", "backyard fence")
_PATIO_TERMS = ("covered patio", "patio cover", "attached patio", "attached covered patio")
_FOUNDATION_TERMS = ("foundation repair", "concrete pier", "concrete piers", "helical pier", "foundation piers")
_ADU_TERMS = ("adu", "accessory dwelling", "garage conversion", "in-law suite", "granny flat", "jadu")
_REMODEL_TERMS = ("remodel", "renovation", "alteration", "addition", "new bathroom", "new kitchen", "load bearing", "wall removal")

_TAG_TERMS: dict[str, tuple[str, ...]] = {
    # Do not use bare "commercial" as a scope tag for deterministic notes: state
    # packs often say "residential and commercial" or "commercial refrigeration"
    # as broad applicability/licensing context. Contamination blocking should key
    # on actual TI/change-of-use language, not the standalone word.
    "commercial_ti": (
        "tenant improvement", "tenant finish", "tenant buildout", "buildout", "build-out",
        "commercial alteration", "commercial building", "change of occupancy", "change of use", "change in use",
        "conversion", "converted into", "convert into", "converting into", "fit-out", "fit out",
    ),
    "restaurant_ti": ("restaurant", "commercial kitchen", "food service", "type i hood", "type 1 hood", "grease interceptor", "ansul", "commercial dishwasher"),
    "medical_clinic_ti": ("medical clinic", "dental clinic", "clinic tenant", "exam room", "patient care", "medical gas", "x-ray", "radiology"),
    "office_ti": ("office tenant improvement", "office ti", "professional office", "office buildout", "law office"),
    "retail_ti": ("retail tenant improvement", "retail ti", "storefront", "retail buildout", "retail store"),
    "residential_only": (
        "homeowner", "homeowners", "owner-builder", "owner builder", "owner-occupied", "home improvement", "home-improvement",
        "home improvement contractor", "home-improvement contractor", "home-improvement contracts",
        "residential home-improvement", "residential remodeling", "residential alteration",
        "residential alterations", "residential land or buildings", "residential code", "residential remodel",
        "residential building permit", "residential building permits", "residential permit", "residential permits",
        "residential subdivision", "residential subdivisions", "residential developer", "residential developers",
        "single-family", "single family", "dwelling",
    ),
    "residential_adu": ("adu", "accessory dwelling", "jadu", "garage conversion", "in-law suite", "granny flat"),
    "residential_solar": ("residential solar", "solar pv", "photovoltaic", "solarapp", "net metering", "interconnection"),
    "panel_upgrade": _PANEL_TERMS,
    "solar_pv": _SOLAR_TERMS,
    "coastal_windstorm": ("twia", "windstorm", "wpi-8", "catastrophe area", "coastal"),
    "floodplain": ("floodplain", "nfip", "fema", "sfha", "base-flood", "elevation certificate"),
    "utility_interconnection": ("interconnection", "net metering", "utility", "meter set", "new service"),
}

_GENERAL_TAGS = {"general", "contractor_license", "code_adoption", "energy", "accessibility", "timeline", "fee", "inspection", "local_utility", "utility_service", "general_state_local"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


@dataclass(frozen=True)
class ScopeFacts:
    """Deterministic request-only scope facts used by invariant gates.

    This object is intentionally derived from the original lookup request, not
    from resolver/model/cache prose, so later contaminated rows cannot override
    segment, construction class, trade signals, or special review signals.
    """

    segment: str = "unknown"
    construction_class: str = "none"
    trade_signals: frozenset[str] = field(default_factory=frozenset)
    special_signals: frozenset[str] = field(default_factory=frozenset)
    negative_scope_facts: frozenset[str] = field(default_factory=frozenset)
    dominant_family: str = ""
    vertical: str = "generic"
    request_scope_text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "segment": self.segment,
            "construction_class": self.construction_class,
            "trade_signals": sorted(self.trade_signals),
            "special_signals": sorted(self.special_signals),
            "negative_scope_facts": sorted(self.negative_scope_facts),
            "dominant_family": self.dominant_family,
            "vertical": self.vertical,
            "request_scope_text": self.request_scope_text,
            "source": "request_scope_facts_v1",
        }


@dataclass(frozen=True)
class ScopeFactsV2(ScopeFacts):
    """Additive V2 request facts for full-customer family reconciliation.

    V2 keeps the V1 fields stable while adding positive/negative facts,
    occupancy-change markers, and quantitative values used by deterministic ADD
    implications.  It is still request-only: absence of a word never becomes a
    negative fact unless the user explicitly says "no/without/non-..." or the
    phrase is an unambiguous same-scope replacement marker.
    """

    positive_facts: frozenset[str] = field(default_factory=frozenset)
    negative_facts: frozenset[str] = field(default_factory=frozenset)
    occupancy_change: bool = False
    service_amperage: int | None = None
    valuation_usd: int | None = None
    rack_height_ft: float | None = None
    mandatory_family_floors: dict[str, str] = field(default_factory=dict)
    forbidden_families: dict[str, str] = field(default_factory=dict)
    required_documents_floor: dict[str, str] = field(default_factory=dict)
    repair_exemption_candidate: bool = False

    def as_dict(self) -> dict[str, Any]:
        data = super().as_dict()
        data.update({
            "positive_facts": sorted(self.positive_facts),
            "negative_facts": sorted(self.negative_facts),
            "occupancy_change": self.occupancy_change,
            "service_amperage": self.service_amperage,
            "valuation_usd": self.valuation_usd,
            "rack_height_ft": self.rack_height_ft,
            "mandatory_family_floors": dict(self.mandatory_family_floors),
            "forbidden_families": dict(self.forbidden_families),
            "required_documents_floor": dict(self.required_documents_floor),
            "repair_exemption_candidate": self.repair_exemption_candidate,
            "source": "request_scope_facts_v2",
        })
        return data


class TriFact(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Fact:
    value: TriFact = TriFact.UNKNOWN
    evidence: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value.value, "evidence": list(self.evidence)}


@dataclass(frozen=True)
class ChangeOfUse:
    from_use: str = ""
    to_use: str = ""
    to_occupancy_group: str = ""
    evidence: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "from_use": self.from_use,
            "to_use": self.to_use,
            "to_occupancy_group": self.to_occupancy_group,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class ScopeFactsV3(ScopeFactsV2):
    """Evidence-bearing tri-state request facts for the Fable5 packet seal."""

    food_establishment: Fact = field(default_factory=Fact)
    grease_discharge: Fact = field(default_factory=Fact)
    co2_enrichment: Fact = field(default_factory=Fact)
    hazardous_materials: Fact = field(default_factory=Fact)
    hazmat_kinds: tuple[str, ...] = field(default_factory=tuple)
    structural_work: Fact = field(default_factory=Fact)
    structural_kinds: tuple[str, ...] = field(default_factory=tuple)
    facade_scope: str = "none"
    change_of_use: ChangeOfUse | None = None
    assembly_occupancy: Fact = field(default_factory=Fact)
    electrical_new_circuits: Fact = field(default_factory=Fact)
    residential_outdoor_cooking: Fact = field(default_factory=Fact)
    gas_fuel_work: Fact = field(default_factory=Fact)

    def as_dict(self) -> dict[str, Any]:
        data = super().as_dict()
        data.update({
            "source": "request_scope_facts_v3",
            "food_establishment": self.food_establishment.as_dict(),
            "grease_discharge": self.grease_discharge.as_dict(),
            "co2_enrichment": self.co2_enrichment.as_dict(),
            "hazardous_materials": self.hazardous_materials.as_dict(),
            "hazmat_kinds": list(self.hazmat_kinds),
            "structural_work": self.structural_work.as_dict(),
            "structural_kinds": list(self.structural_kinds),
            "facade_scope": self.facade_scope,
            "change_of_use": self.change_of_use.as_dict() if self.change_of_use else None,
            "assembly_occupancy": self.assembly_occupancy.as_dict(),
            "electrical_new_circuits": self.electrical_new_circuits.as_dict(),
            "residential_outdoor_cooking": self.residential_outdoor_cooking.as_dict(),
            "gas_fuel_work": self.gas_fuel_work.as_dict(),
        })
        return data


@dataclass(frozen=True)
class ScopeFactsV4(ScopeFactsV3):
    """V4 request facts: scope-aware packet axes and family support sets."""

    occupancy_class: str = "unknown"
    use_change: bool = False
    request_positive_families: frozenset[str] = field(default_factory=frozenset)
    request_negative_families: frozenset[str] = field(default_factory=frozenset)
    electrical_work: Fact = field(default_factory=Fact)
    mechanical_work: Fact = field(default_factory=Fact)
    plumbing_work: Fact = field(default_factory=Fact)
    building_work: Fact = field(default_factory=Fact)

    def as_dict(self) -> dict[str, Any]:
        data = super().as_dict()
        data.update({
            "source": "request_scope_facts_v4",
            "occupancy_class": self.occupancy_class,
            "use_change": self.use_change,
            "request_positive_families": sorted(self.request_positive_families),
            "request_negative_families": sorted(self.request_negative_families),
            "electrical_work": self.electrical_work.as_dict(),
            "mechanical_work": self.mechanical_work.as_dict(),
            "plumbing_work": self.plumbing_work.as_dict(),
            "building_work": self.building_work.as_dict(),
        })
        return data


def _fact_value(facts: Any, name: str) -> str:
    value = getattr(facts, name, None)
    if isinstance(value, Fact):
        return value.value.value
    if isinstance(facts, dict):
        raw = facts.get(name)
        if isinstance(raw, dict):
            return str(raw.get("value") or "").upper()
    return ""


def safety_critical_required_families(facts: Any | None) -> set[str]:
    """Return request-evidence safety families that cannot be clean NOT_REQUIRED.

    This is a safety backstop, not the broad family matrix.  It only uses
    explicit request-positive safety work classes (structural, electrical, gas,
    plumbing supply/drain, and fire/life-safety) and respects explicit negative
    facts so cosmetic/no-trade scopes remain NOT_REQUIRED.
    """
    if facts is None:
        return set()
    positive_families = {str(f or "").strip() for f in (getattr(facts, "request_positive_families", ()) or ()) if str(f or "").strip()}
    positive_facts = {str(f or "").strip() for f in (getattr(facts, "positive_facts", ()) or ()) if str(f or "").strip()}
    negative_families = {str(f or "").strip() for f in (getattr(facts, "request_negative_families", ()) or ()) if str(f or "").strip()}
    negative_facts = {str(f or "").strip() for f in (getattr(facts, "negative_facts", ()) or ()) if str(f or "").strip()}
    floors = set((getattr(facts, "mandatory_family_floors", {}) or {}).keys())
    if isinstance(facts, dict):
        positive_families |= {str(f or "").strip() for f in (facts.get("request_positive_families") or []) if str(f or "").strip()}
        positive_facts |= {str(f or "").strip() for f in (facts.get("positive_facts") or []) if str(f or "").strip()}
        negative_families |= {str(f or "").strip() for f in (facts.get("request_negative_families") or []) if str(f or "").strip()}
        negative_facts |= {str(f or "").strip() for f in (facts.get("negative_facts") or []) if str(f or "").strip()}
        floors |= set((facts.get("mandatory_family_floors") or {}).keys()) if isinstance(facts.get("mandatory_family_floors"), dict) else set()
    positives = positive_families | floors
    request_text = str(getattr(facts, "request_scope_text", "") or (facts.get("request_scope_text") if isinstance(facts, dict) else "") or "").lower()
    out: set[str] = set()
    cosmetic = "cosmetic_only" in negative_facts
    if not cosmetic and (
        "electrical" in positives
        or "electrical" in positive_facts
        or _fact_value(facts, "electrical_work") == "TRUE"
        or _fact_value(facts, "electrical_new_circuits") == "TRUE"
    ) and "electrical" not in negative_families and "no_electrical" not in negative_facts and "no_mep" not in negative_facts:
        out.add("electrical")
    if not cosmetic and (
        "plumbing" in positives
        or "plumbing" in positive_facts
        or _fact_value(facts, "plumbing_work") == "TRUE"
    ) and "plumbing" not in negative_families and "no_plumbing" not in negative_facts and "no_mep" not in negative_facts:
        out.add("plumbing")
    if not cosmetic and (
        "gas" in positives or "gas" in positive_facts or _fact_value(facts, "gas_fuel_work") == "TRUE"
    ) and "gas" not in negative_families and "no_gas" not in negative_facts:
        out.add("gas")
        if "no_plumbing" not in negative_facts and "plumbing" not in negative_families:
            out.add("plumbing")
    if not cosmetic and (
        positive_families & {"fire_suppression", "fire_alarm", "fire_life_safety_assembly"}
        or positive_facts & {"fire_suppression", "fire_alarm", "hood_wet_chemical"}
    ):
        out.add("fire_suppression")
    if not cosmetic and (
        positive_families & {"building", "building_structural"}
        or positive_facts & {"structural", "addition", "demolition", "racking", "exterior"}
        or _fact_value(facts, "structural_work") == "TRUE"
        or _fact_value(facts, "building_work") == "TRUE"
    ) and "building" not in negative_families and "structural" not in negative_families and "no_structural" not in negative_facts:
        out.add("building")
    if not cosmetic and re.search(r"\b(?:curb\s+cut|driveway\s+apron|widen\s+(?:the\s+)?driveway|parking\s+lot|ada\s+stalls?|accessible\s+parking|restripe|striping|mill\s+and\s+overlay|grading|stormwater|right[- ]of[- ]way|\brow\b|site\s*(?:work|civil))\b", request_text, re.I):
        out.add("grading")
        if "no_use_change" not in negative_facts:
            out.add("planning_zoning")
    return {fam for fam in out if fam}


_CONSTRUCTION_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("change_of_use", ("change of use", "change in use", "change of occupancy", "occupancy change", "retail to", "office to", "warehouse to", "gallery change to", "convert from", "conversion from")),
    ("conversion", ("convert garage", "garage into", "garage to", "basement adu", "basement apartment", "laundromat conversion", "conversion", "converted into", "convert into", "convert vacant", "convert office", "convert retail", "convert warehouse", "convert space", "convert suite")),
    ("addition", ("addition", "add classroom", "classroom addition", "bedroom addition", "building addition", "porch addition", "attached patio cover", "metal building addition", "new detached", "backyard cottage", "detached backyard cottage")),
    ("TI", ("tenant improvement", "tenant finish", "tenant buildout", "buildout", "build-out", "first generation upfit", "first-generation upfit", "upfit", "demising wall", "conference rooms", "non load bearing partitions", "partition walls", "build new shared laundry room", "shared laundry room")),
    ("alteration", ("alteration", "remodel", "renovation", "structural", "load bearing", "foundation repair", "foundation replacement", "new foundation", "new window", "new wall", "demising")),
)
_TRADE_SIGNAL_PATTERNS: dict[str, tuple[str, ...]] = {
    "electrical": ("electrical", "new lighting", "lighting", "lights", "light fixtures", "outlets", "receptacles", "gfci", "240 volt", "240v", "220 volt", "220v", "dryer outlet", "electrical panel", "service panel", "breaker panel", "load center", "600 amp", "600a", "electrical service", "service upgrade", "amp service", "x ray", "x-ray", "grow lights", "disconnect", "fire alarm wiring", "new circuit"),
    "mechanical_fuelgas": ("mechanical", "hvac", "mini split", "mini-split", "ductless", "heat pump", "rtu", "rooftop unit", "rooftop package", "ventilation", "exhaust", "exhaust fan", "bath fan", "fume hood", "lab hood", "makeup air", "gas-fired", "gas fired", "gas dryer", "gas dryers", "fuel gas", "gas line", "gas piping", "radiant heat", "furnace"),
    "plumbing_fog": ("plumbing", "grease interceptor", "fog", "floor drain", "floor drains", "water heater", "heat pump water heater", "hpwh", "shampoo bowls", "sink", "sinks", "toilets", "restroom", "shower drain", "gas line", "fixture", "medical gas", "med gas", "nitrous"),
    "fire": ("fire alarm", "sprinkler", "hood", "commercial hood", "type i hood", "type 1 hood", "hood suppression", "fire suppression", "ansul", "wet-chemical", "wet chemical", "high pile", "hazardous", "chemical storage", "daycare", "child care", "fuel dispenser"),
    "building_structural": ("foundation repair", "foundation replacement", "new foundation", "structural", "canopy", "demising wall", "partition", "partitions", "partition walls", "addition", "load bearing", "framing", "new window", "exterior door", "pool", "spa", "retaining wall", "storm shelter", "garage conversion"),
    "wastewater_fog": ("grease interceptor", "fog", "pretreatment", "floor drain", "floor drains"),
    "pool_spa": ("pool", "spa", "swimming pool", "in ground pool", "in-ground pool"),
    "medical_gas": ("medical gas", "med gas", "nitrous", "oxygen line", "dental gas"),
    "lab_hazmat": ("lab", "wet lab", "biotech", "fume hood", "chemical storage", "hazmat", "hazardous materials"),
    "daycare_life_safety": ("daycare", "child care", "classrooms", "fenced play yard"),
    "retaining_wall": ("retaining wall",),
    "dryer_outlet": ("dryer outlet", "240 volt outlet", "240v outlet", "220 volt outlet", "heat pump dryer"),
    "rtu_same_capacity": ("rtu", "rooftop unit", "same capacity", "same tonnage"),
    "hpwh": ("heat pump water heater", "hpwh", "hybrid heat pump water heater"),
    "grease_interceptor_only": ("grease interceptor only", "interceptor only"),
}
_SPECIAL_SIGNAL_PATTERNS: dict[str, tuple[str, ...]] = {
    "historic": ("historic", "historic district", "historic overlay", "local historic district", "landmark", "french quarter", "vieux carre", "vieux carré", "vcc", "hdlc", "certificate of appropriateness", "coa", "architectural review", "board of architectural review"),
    "exterior_alteration": ("exterior facade", "front facade", "facade", "façade", "shutter", "shutters", "shutter replacement", "exterior sign", "sign replacement", "awning", "storefront", "exterior window", "exterior windows", "front window", "front windows", "window replacement", "windows", "exterior door", "door replacement"),
    "coastal": ("coastal", "shoreline", "windstorm", "twia"),
    "flood": ("flood", "floodplain", "fema", "sfha"),
    "hazardous": ("hazardous", "fuel dispenser", "gas station", "service station", "fuel system", "cannabis", "co2 system", "compressed air"),
    "health": ("restaurant", "food service", "food establishment", "commercial kitchen", "commissary", "brewery", "grease interceptor"),
    "row": ("right of way", "right-of-way", "sidewalk", "curb cut", "driveway", "encroachment"),
    "environmental": ("fuel dispenser", "gas station", "service station", "underground tank", "ust", "environmental"),
}

_NEGATIVE_SCOPE_PATTERNS: dict[str, tuple[str, ...]] = {
    "same_capacity": ("same capacity", "same tonnage", "same size", "like for like", "like-for-like"),
    "existing_circuits": ("existing circuit", "existing circuits", "using existing circuits", "no new circuit", "no new circuits", "no panel work", "no service upgrade"),
    "no_walls": ("no walls", "no wall changes", "no wall work", "no partitions", "no wall or partition work"),
    "no_mep": ("no mep", "no mechanical", "no electrical", "no plumbing", "no mechanical electrical plumbing"),
    "no_occupancy_change": ("no occupancy change", "no change of occupancy", "no change of use", "without occupancy change"),
    "no_electrical": ("no electrical", "no electric", "no wiring", "no new electrical"),
    "no_plumbing": ("no plumbing", "no pipe", "no drain", "no water line"),
    "no_mechanical": ("no mechanical", "no hvac", "no ventilation"),
    "no_structural": ("no structural", "no structural changes", "no framing", "no rough opening", "no rough-opening"),
    "only_scope": ("only", "interceptor only", "fixture only", "cosmetic only", "repaint and carpet replacement only"),
}


def _first_matching_class(job: str) -> str:
    for construction_class, terms in _CONSTRUCTION_PATTERNS:
        if has_any_unnegated(job, terms):
            return construction_class
    return "none"


def _signals_from_patterns(job: str, patterns: dict[str, tuple[str, ...]]) -> frozenset[str]:
    return frozenset(signal for signal, terms in patterns.items() if has_any_unnegated(job, terms))


def _negative_scope_facts(job: str) -> frozenset[str]:
    facts = {name for name, terms in _NEGATIVE_SCOPE_PATTERNS.items() if has_any_unnegated(job, terms)}
    # Handle comma/list negation such as: "no walls, electrical, plumbing, mechanical, or occupancy change".
    if re.search(r"\bno\s+[^.;]{0,120}\bwalls?\b", job, re.I):
        facts.add("no_walls")
    if re.search(r"\bno\s+[^.;]{0,120}\belectrical\b", job, re.I):
        facts.add("no_electrical")
    if re.search(r"\bno\s+[^.;]{0,120}\bplumbing\b", job, re.I):
        facts.add("no_plumbing")
    if re.search(r"\bno\s+[^.;]{0,120}\bmechanical\b", job, re.I):
        facts.add("no_mechanical")
    if re.search(r"\bno\s+[^.;]{0,120}\bmep\b", job, re.I):
        facts.add("no_mep")
    if re.search(r"\bno\s+[^.;]{0,120}\boccupancy\s+change\b|\bno\s+change\s+of\s+(?:use|occupancy)\b", job, re.I):
        facts.add("no_occupancy_change")
    if {"no_electrical", "no_plumbing", "no_mechanical"}.issubset(facts):
        facts.add("no_mep")
    if re.search(r"\b(?:no|without)\s+(?:walls?|mep|mechanical|electrical|plumbing|occupancy(?:\s+change)?)(?:\s*/\s*|\s+or\s+|\s+and\s+)?", job, re.I):
        if "no walls" in job or "no wall" in job:
            facts.add("no_walls")
        if "no mep" in job:
            facts.add("no_mep")
        if "no occupancy" in job or "no change of use" in job:
            facts.add("no_occupancy_change")
    if re.search(r"\b(?:repaint|paint|carpet|flooring|cosmetic|refresh)\b", job, re.I) and ("only" in job or {"no_walls", "no_mep", "no_occupancy_change"} & facts):
        facts.add("cosmetic_only")
    if re.search(r"\b(?:grease\s+)?interceptor\s+only\b", job, re.I):
        facts.add("grease_interceptor_only")
        facts.add("only_scope")
    return frozenset(facts)


def _dominant_family(job: str, trade_signals: frozenset[str], special_signals: frozenset[str]) -> str:
    if has_any_unnegated(job, ("heat pump water heater", "hpwh", "hybrid heat pump water heater")):
        return "plumbing"
    if has_any_unnegated(job, ("grease interceptor", "fog", "pretreatment")):
        return "plumbing"
    if has_any_unnegated(job, ("rtu", "rooftop unit", "hvac", "mechanical", "furnace", "air conditioner", "heat pump")):
        return "mechanical"
    if has_any_unnegated(job, ("gas station", "service station", "canopy", "fuel dispenser")):
        return "building"
    if "building_structural" in trade_signals:
        return "building"
    if "plumbing_fog" in trade_signals or "wastewater_fog" in trade_signals:
        return "plumbing"
    if "mechanical_fuelgas" in trade_signals:
        return "mechanical"
    if "electrical" in trade_signals:
        return "electrical"
    if "fire" in trade_signals:
        return "fire"
    if "historic" in special_signals:
        return "historic"
    return ""


def build_scope_facts(job_type: str, city: str = "", state: str = "", *, job_category: str | None = None, vertical: str | None = None, scope_contract: dict[str, Any] | None = None) -> ScopeFacts:
    contract = scope_contract if isinstance(scope_contract, dict) else build_scope_contract(job_type, city, state, job_category=job_category, vertical=vertical)
    job = _norm(job_type)
    segment = str(contract.get("category") or _explicit_category(job, job_category) or "unknown").lower().strip()
    if segment not in {"residential", "commercial"}:
        segment = "unknown"
    construction_class = _first_matching_class(job)
    trade_signals = _signals_from_patterns(job, _TRADE_SIGNAL_PATTERNS)
    special_signals = _signals_from_patterns(job, _SPECIAL_SIGNAL_PATTERNS)
    negative_scope_facts = _negative_scope_facts(job)
    dominant_family = "building" if construction_class != "none" else _dominant_family(job, trade_signals, special_signals)
    return ScopeFacts(
        segment=segment,
        construction_class=construction_class,
        trade_signals=trade_signals,
        special_signals=special_signals,
        negative_scope_facts=negative_scope_facts,
        dominant_family=dominant_family,
        vertical=str(contract.get("vertical") or vertical or "generic"),
        request_scope_text=job,
    )


def _extract_int_money_or_plain(text: str, *, value_words: tuple[str, ...]) -> int | None:
    pattern = r"(?:" + "|".join(re.escape(word) for word in value_words) + r")\D{0,24}(\$?\s*\d{1,3}(?:,\d{3})+|\$?\s*\d{2,7})(\s*[kK])?"
    match = re.search(pattern, text, re.I)
    if not match:
        return None
    value = int(re.sub(r"\D", "", match.group(1) or "0") or "0")
    if match.group(2):
        value *= 1000
    return value


def _extract_service_amperage(text: str) -> int | None:
    match = re.search(r"\b(\d{2,4})\s*(?:amps?\b|a\b(?=\s+(?:service|panel|electrical|upgrade)))", text, re.I)
    return int(match.group(1)) if match else None


def _extract_rack_height(text: str) -> float | None:
    match = re.search(r"\b(\d{1,2}(?:\.\d+)?)\s*(?:ft|feet|foot)\b.{0,40}\b(?:rack|racking|storage)\b|\b(?:rack|racking|storage)\b.{0,40}\b(\d{1,2}(?:\.\d+)?)\s*(?:ft|feet|foot)\b", text, re.I)
    if not match:
        return None
    return float(match.group(1) or match.group(2))


def is_vehicle_lift_scope(text: str) -> bool:
    """Return True only for vehicle/service-lift equipment scope.

    This shared classifier prevents generic lift/elevator wording from creating
    building, fire, or trade-family noise in unrelated requests.
    """
    return bool(
        re.search(
            r"\b(?:automotive|vehicle|car|service)\s+lifts?\b"
            r"|\blifts?\b.{0,50}\b(?:repair\s+shop|garage|slab|anchor)\b"
            r"|\b(?:repair\s+shop|garage)\b.{0,50}\blifts?\b",
            str(text or ""),
            re.I,
        )
    )


def filter_scope_contradicted_companion_warnings(
    warnings: list[Any] | tuple[Any, ...] | None,
    job_type: str,
) -> list[str]:
    """Remove the legacy generic trade warning when the scope classifier rejects it."""
    values = warnings if isinstance(warnings, (list, tuple)) else ([warnings] if warnings else [])
    cleaned = [str(item).strip() for item in values if str(item or "").strip()]
    if not is_vehicle_lift_scope(job_type):
        return cleaned
    marker = "commercial scope may require companion reviews/permits not fully proven here:"
    return [item for item in cleaned if marker not in item.lower()]


def _scope_facts_v2_positive(job: str, v1: ScopeFacts) -> set[str]:
    facts: set[str] = set()
    trade_map = {
        "electrical": "electrical",
        "mechanical_fuelgas": "mechanical",
        "plumbing_fog": "plumbing",
        "fire": "fire_suppression",
        "building_structural": "building",
        "wastewater_fog": "grease_generating",
    }
    for signal in v1.trade_signals:
        if signal in trade_map:
            facts.add(trade_map[signal])
    if "historic" in v1.special_signals:
        facts.add("historic_district")
    if re.search(r"\b(?:roof|reroof|re-roof|shingles?|porch|deck|garage|foundation|helical\s+piers?|structural\s+repair|interior\s+wall\s+relocation|wall\s+relocation|relocat(?:e|ing|ion)\s+(?:an?\s+)?(?:interior\s+)?wall)\b", job, re.I):
        facts.add("building")
    if "exterior_alteration" in v1.special_signals or re.search(r"\b(?:exterior|front\s+door|storefront|facade|fa[cç]ade|masonry|lintel|window|door)\b", job, re.I):
        facts.add("exterior")
        facts.add("building")
    if "health" in v1.special_signals:
        facts.add("food_service")
    use_change_request = bool(
        re.search(r"\b(?:change\s+of\s+(?:use|occupancy)|occupancy\s+change|retail\s+to|office\s+to|warehouse\s+to)\b", job, re.I)
        or re.search(r"\b(?:convert|converting|converted|conversion)\b.{0,80}\b(?:garage|adu|accessory\s+dwelling|bedroom|habitable|office|retail|warehouse|restaurant|bar|gym|fitness|assembly|dwelling\s+unit|living\s+space)\b", job, re.I)
        or re.search(r"\b(?:garage|office|retail|warehouse|storefront|suite)\b.{0,80}\b(?:convert|converting|converted|conversion)\b", job, re.I)
    )
    if use_change_request:
        facts.add("use_change")
        if v1.segment == "commercial":
            facts.add("commercial_ti")
            facts.add("building")
    if re.search(r"\b(?:tenant improvement|tenant buildout|tenant finish|commercial interior|white box|demolition|demo)\b", job, re.I):
        facts.add("commercial_ti")
        facts.add("building")
    office_light_ti = bool(re.search(r"\boffice\s+tenant\s+improvement\b|\boffice\s+ti\b", job, re.I) and re.search(r"\b(?:non\s+load\s+bearing|partitions?|diffuser\s+relocation|receptacles?)\b", job, re.I))
    if v1.segment == "commercial" and re.search(r"\b(?:tenant improvement|tenant buildout|tenant finish|upfit|buildout|build-out)\b", job, re.I) and not office_light_ti:
        facts.update({"commercial_ti", "building", "electrical", "mechanical", "plumbing", "planning_zoning"})
    rtu_like_for_like_food_unchanged = bool(re.search(r"\b(?:rtu|rooftop\s+unit)\b", job, re.I) and re.search(r"\b(?:like[- ]for[- ]like|same\s+(?:curb|location|capacity|tonnage))\b", job, re.I) and not re.search(r"\b(?:hood|commercial\s+kitchen|grease|fog|floor\s+drain|interceptor)\b", job, re.I))
    if v1.segment == "commercial" and re.search(r"\b(?:restaurant|bar|brewery|food\s+service|commercial kitchen)\b", job, re.I) and not rtu_like_for_like_food_unchanged:
        facts.update({"food_service", "grease_generating", "fire_suppression", "health_food", "co_change_of_occupancy", "planning_zoning", "mechanical", "plumbing", "electrical"})
    if v1.segment == "commercial" and re.search(r"\b(?:dental|medical|clinic|x[- ]?ray|exam room)\b", job, re.I):
        facts.update({"electrical", "mechanical", "plumbing"})
    vehicle_lift_scope = is_vehicle_lift_scope(job)
    if v1.segment == "commercial" and vehicle_lift_scope:
        # Vehicle/service lifts anchored to the slab are structural equipment.
        # Do not infer fire-suppression or zoning review from the business type;
        # those families require explicit system/hazardous/use-change scope.
        facts.update({"building", "structural"})
        if re.search(r"\b(?:electrical|wiring|circuits?|receptacles?|disconnects?|power)\b", job, re.I):
            facts.add("electrical")
        if "use_change" in facts:
            facts.add("planning_zoning")
    if re.search(r"\b(?:solar|pv|photovoltaic)\b", job, re.I):
        facts.update({"electrical", "building"})
    if re.search(r"\b(?:adu|accessory dwelling|detached dwelling|garage conversion|kitchen\s+bath\s+and\s+utilities)\b", job, re.I):
        facts.add("new_dwelling_unit")
    if re.search(r"\b(?:utilities|kitchen|bath|bathroom|water|sewer|electrical|panel|gas)\b", job, re.I) and "new_dwelling_unit" in facts:
        facts.add("utilities_connected")
    if re.search(r"\b(?:sign|signage|awning)\b", job, re.I) and not re.search(r"\bexit\s+signs?\b", job, re.I):
        facts.add("sign")
    if re.search(r"\b(?:illuminated|lit|lighting|electric sign)\b", job, re.I) and not re.search(r"\b(?:exit\s+signs?|exit\s+signage|non[- ]?electric|no\s+electrical|no\s+illumination|not\s+illuminated)\b", job, re.I):
        facts.add("sign_illuminated")
        facts.add("electrical")
    if re.search(r"\b(?:rack|racking|high[- ]pile|storage rack)\b", job, re.I):
        facts.add("racking")
        facts.add("building")
    if re.search(r"\b(?:demo|demolition|white box)\b", job, re.I):
        facts.add("demolition")
        facts.add("building")
    if re.search(r"\b(?:fire\s+alarm|alarm\s+panel|sprinkler\s+monitoring)\b", job, re.I):
        facts.add("fire_alarm")
        facts.add("electrical")
    if re.search(r"\b(?:type\s*i\s+hood|type\s*1\s+hood|wet[- ]chemical|ansul|hood\s+suppression)\b", job, re.I):
        facts.add("hood_wet_chemical")
        facts.add("fire_suppression")
        facts.add("food_service")
    if re.search(r"\b(?:restaurant|food\s+service|commercial kitchen|grease|fog|floor drains?)\b", job, re.I) and not rtu_like_for_like_food_unchanged:
        facts.add("food_service")
        if re.search(r"\b(?:restaurant|grease|fog|floor drains?|interceptor)\b", job, re.I):
            facts.add("grease_generating")
    if re.search(r"\b(?:ev\s+charger|electric\s+vehicle\s+charger|level\s*(?:2|ii)\s+charger|car\s+charger|panel\s+upgrade|service\s+upgrade|upgrade\s+(?:the\s+)?(?:electrical\s+)?panel|replace\s+(?:the\s+)?(?:electrical\s+)?panel|service\s+panel\s+replacement)\b", job, re.I):
        facts.add("electrical")
    if re.search(r"\b(?:central\s+air|air\s+conditioner|condenser|coil|furnace|heat\s+pump|mini[- ]split|hvac|rtu|rooftop\s+unit|bath(?:room)?\s+fan|duct\s+replacement|ductwork\s+replacement|replace\s+ducts?|ducting)\b", job, re.I):
        facts.add("mechanical")
    if re.search(r"\b(?:gas\s+(?:line|piping|reconnection|connection|dryer)|fuel\s+gas|radiant\s+heat|compressed\s+air)\b", job, re.I):
        facts.add("plumbing")
        facts.add("gas")
    if re.search(r"\b(?:plumbing(?:\s+and\s+electrical)?\s+upgrades?|electrical(?:\s+and\s+plumbing)?\s+upgrades?)\b", job, re.I):
        facts.update({"plumbing", "electrical"})
    if re.search(r"\b(?:standby\s+generator|automatic\s+transfer\s+switch|transfer\s+switch|generator\s+(?:install|installation))\b", job, re.I):
        facts.add("electrical")
    if re.search(r"\b(?:tub\s+valve|shower\s+valve|bathroom\s+renovation|bathroom\s+remodel|replace\s+(?:tub|toilet|sink|valve)|gas\s+water\s+heater|new\s+gas\s+line)\b", job, re.I):
        facts.add("plumbing")
    if re.search(r"\b(?:addition|new\s+building|metal\s+building|structural|load[- ]bearing)\b", job, re.I):
        facts.add("addition" if "addition" in job else "structural")
        facts.add("building")
    return facts


def _scope_facts_v2_negative(job: str, v1: ScopeFacts) -> set[str]:
    facts = set(v1.negative_scope_facts)
    # Normalize older V1 names to the plan's names.
    if "no_occupancy_change" in facts:
        facts.add("no_use_change")
    if "same_capacity" in facts:
        facts.add("like_for_like_replacement")
    if re.search(r"\b(?:no|without|non[- ]?)\s*(?:new\s*)?(?:electric(?:al)?|wiring|illumination|illuminated)\b", job, re.I):
        facts.add("no_electrical")
    if re.search(r"\bno\s+(?:problems?|issues?)\s+with\s+(?:the\s+)?electrical\b", job, re.I):
        facts.discard("no_electrical")
    if re.search(r"\b(?:non[- ]?electric|not\s+illuminated|no\s+illumination|no\s+lighting)\b", job, re.I):
        facts.add("no_illumination")
        facts.add("no_electrical")
    if re.search(r"\b(?:no|without)\s+(?:plumbing|pipe|pipes|drain|drains|fixtures?|water|sewer)\b", job, re.I):
        facts.add("no_plumbing")
    if re.search(r"\b(?:no|without)\s+(?:mechanical|hvac|ventilation|duct|ductwork)\b", job, re.I):
        facts.add("no_mechanical")
    if re.search(r"\b(?:no|without)\s+(?:mep|mechanical\s*/\s*electrical\s*/\s*plumbing)\b", job, re.I):
        facts.update({"no_mep", "no_mechanical", "no_electrical", "no_plumbing"})
    if re.search(r"\b(?:no|without)\s+(?:change\s+of\s+use|use\s+change|occupancy\s+change|change\s+of\s+occupancy)\b", job, re.I):
        facts.add("no_use_change")
    if re.search(r"\boffice\s+tenant\s+improvement\b|\boffice\s+ti\b", job, re.I) and re.search(r"\b(?:non\s+load\s+bearing|partitions?|diffuser\s+relocation|receptacles?)\b", job, re.I) and not re.search(r"\b(?:change\s+of\s+(?:use|occupancy)|conversion|convert|occupancy\s+change|retail\s+to|office\s+to|warehouse\s+to)\b", job, re.I):
        facts.add("no_use_change")
    if re.search(r"\b(?:no|without)\s+(?:sprinklers?\s+(?:altered|modified|changed)|sprinkler\s+alteration|fire\s+sprinkler\s+work)\b", job, re.I):
        facts.add("no_sprinkler_alteration")
    if re.search(r"\b(?:like[- ]for[- ]like|same\s+(?:curb|location|capacity|tonnage|size))\b", job, re.I):
        facts.add("like_for_like_replacement")
    if re.search(r"\b(?:rtu|rooftop\s+unit)\b", job, re.I) and re.search(r"\b(?:like[- ]for[- ]like|same\s+(?:curb|location|capacity|tonnage))\b", job, re.I) and not re.search(r"\b(?:hood|kitchen|grease|food\s+service|floor\s+drain)\b", job, re.I):
        facts.add("no_food_service_change")
    if v1.segment == "residential" and not re.search(r"\b(?:grease|fog|interceptor|commercial\s+kitchen|food\s+service|restaurant)\b", job, re.I):
        facts.add("no_food_service_change")
    if re.search(r"\b(?:new|upgrade|upgrades?|alter|altered|adds?|install|installation|relocat(?:e|ion))\s+(?:electrical|wiring|circuits?|receptacles?|lighting|panel|transfer\s+switch|generator)\b|\b(?:electrical|wiring|circuits?|receptacles?|lighting|panel|transfer\s+switch|generator)\s+(?:upgrade|upgrades?|alteration|installation)\b|\b(?:automatic\s+transfer\s+switch|standby\s+generator|new\s+gas\s+line)\b", job, re.I):
        facts.discard("no_electrical")
        facts.discard("no_mep")
    if re.search(r"\b(?:new|upgrade|upgrades?|alter|altered|adds?|install|installation|relocat(?:e|ion))\s+(?:plumbing|pipes?|drains?|fixtures?|water|sewer)\b|\b(?:plumbing(?:\s+and\s+electrical)?|pipes?|drains?|fixtures?|water|sewer)\s+(?:upgrade|upgrades?|work|alteration|installation)\b", job, re.I):
        facts.discard("no_plumbing")
        facts.discard("no_mep")
    if re.search(r"\b(?:restaurant|bar|brewery|food\s+service|commercial kitchen|small restaurant)\b", job, re.I) and not (re.search(r"\b(?:rtu|rooftop\s+unit)\b", job, re.I) and re.search(r"\b(?:like[- ]for[- ]like|same\s+(?:curb|location|capacity|tonnage))\b", job, re.I)):
        facts.discard("no_food_service_change")
    if re.search(r"\bwater\s+heater\b", job, re.I) and not re.search(r"\b(?:hvac|rtu|rooftop|air\s+handler|condenser|mini[- ]split|duct)\b", job, re.I):
        facts.add("water_heater_only")
    return facts


def _scope_facts_v2_family_floors(job: str, v1: ScopeFacts, positives: set[str]) -> dict[str, str]:
    floors: dict[str, str] = {}
    if v1.segment == "residential" and re.search(r"\blaundry\b", job, re.I) and re.search(r"\brelocat(?:e|ing|ion)\b", job, re.I) and re.search(r"\b(?:second\s+floor|upstairs|upper\s+floor)\b", job, re.I) and re.search(r"\b(?:drain|vent|water\s+lines?|plumbing)\b", job, re.I):
        floors["building"] = "residential laundry relocation between floors with new drain/vent/water lines requires building alteration floor"
    if v1.segment == "residential" and re.search(r"\b(?:bath(?:room)?|tub|shower|toilet)\b", job, re.I) and (
        re.search(r"\b(?:convert|conversion)\b.{0,40}\b(?:tub|shower)\b|\b(?:tub|shower)\b.{0,40}\b(?:convert|conversion)\b", job, re.I)
        or re.search(r"\brelocat(?:e|ing|ion)\b.{0,40}\b(?:toilet|shower|tub|fixture)\b|\b(?:toilet|shower|tub|fixture)\b.{0,40}\brelocat(?:e|ing|ion)\b", job, re.I)
    ):
        floors["building"] = "residential bathroom fixture conversion/relocation requires building alteration floor"
    if re.search(r"\bbasement\b", job, re.I) and re.search(r"\b(?:finish|finished|finishing|bedroom|bath(?:room)?)\b", job, re.I):
        floors["building"] = "basement finish with habitable room/bathroom requires residential building alteration floor"
        if re.search(r"\b(?:bath|bathroom|toilet|sink|shower|plumbing)\b", job, re.I):
            floors["plumbing"] = "basement finish with bathroom/plumbing scope requires plumbing floor"
    if re.search(r"\b(?:change\s+of\s+(?:use|occupancy)|occupancy\s+change|retail\s+store\s+to|retail\s+to|warehouse\s+to)\b", job, re.I) and re.search(r"\b(?:fitness|gym|showers?|locker)\b", job, re.I):
        floors["plumbing"] = "fitness/change-of-use scope with showers/locker rooms requires plumbing floor"
    if re.search(r"\b(?:school|classroom)\b", job, re.I) and re.search(r"\baddition\b", job, re.I):
        floors["building"] = "school/classroom addition requires commercial building addition floor"
        if re.search(r"\b(?:fire\s+alarm|hvac|rtu|mechanical|circuits?)\b", job, re.I):
            floors["electrical"] = "school/classroom addition with fire alarm/HVAC requires electrical floor"
    commercial_structural_addition = bool(
        re.search(r"\b(?:metal\s+building|structural\s+steel|new\s+foundation|foundation\s+work|foundations?\s+for|building\s+addition)\b", job, re.I)
        or re.search(r"\baddition\s+(?:to|of|for)\s+(?:the\s+)?(?:building|structure|warehouse|shop|school|classroom|auto\s+repair)\b", job, re.I)
    )
    if v1.segment == "commercial" and commercial_structural_addition:
        floors["building"] = "commercial structural/addition scope requires commercial building floor"
    vehicle_lift_scope = is_vehicle_lift_scope(job)
    if v1.segment == "commercial" and vehicle_lift_scope:
        floors["building"] = "commercial vehicle/service lift anchored to the slab requires structural building-equipment filing floor"
        if re.search(r"\b(?:electrical|wiring|circuits?|receptacles?|disconnects?|power)\b", job, re.I):
            floors["electrical"] = "commercial vehicle/service lift scope with new electrical work requires electrical filing floor"
    if v1.segment == "commercial" and re.search(r"\bfloor\s+drains?\b", job, re.I):
        floors["plumbing"] = "commercial TI with floor drains requires plumbing floor"
    if v1.segment == "commercial" and re.search(r"\b(?:warehouse\s+to|assembly|pickleball|occupant\s+load)\b", job, re.I) and "use_change" in positives:
        floors["building_ti"] = "commercial change-of-use/assembly conversion requires building/TI filing floor"
        floors["fire_suppression"] = "assembly/change-of-use occupant-load increase requires fire/life-safety floor"
        floors["planning_zoning"] = "commercial change-of-use requires zoning/use clearance floor"
        floors["co_change_of_occupancy"] = "commercial change-of-use requires certificate/change-of-occupancy floor"
    if v1.segment == "commercial" and re.search(r"\b(?:split\s+existing\s+retail\s+suite|demising\s+wall)\b", job, re.I) and re.search(r"\bexit\s+signs?\b", job, re.I):
        floors["fire_suppression"] = "commercial suite split with demising/exit-sign life-safety scope requires fire/life-safety floor"
    if v1.segment == "commercial" and "use_change" in positives:
        floors.setdefault("building_ti", "commercial change-of-use requires building/TI filing floor")
        floors.setdefault("planning_zoning", "commercial change-of-use requires zoning/use clearance floor")
        floors.setdefault("co_change_of_occupancy", "commercial change-of-use requires certificate/change-of-occupancy floor")
    if v1.segment == "commercial" and re.search(r"\b(?:restaurant|commercial kitchen|cooking equipment)\b", job, re.I) and re.search(r"\b(?:gas\s+(?:line|piping|equipment)|fuel\s+gas|gas)\b", job, re.I):
        if re.search(r"\b(?:alteration|alter|cooking equipment|hood|ductwork|fire alarm|tenant improvement|buildout)\b", job, re.I) and not re.search(r"\b(?:rtu|rooftop unit)\b", job, re.I):
            floors.setdefault("building_ti", "commercial restaurant alteration requires DOB/building alteration filing floor")
        floors["gas"] = "restaurant/commercial cooking scope with explicit gas requires fuel-gas/plumbing-gas permit floor"
    return floors


def _scope_facts_v2_forbidden_families(job: str, v1: ScopeFacts, negatives: set[str], positives: set[str]) -> dict[str, str]:
    forbidden: dict[str, str] = {}
    commercial_food_operation = bool(re.search(r"\b(?:restaurant|food\s+service|food\s+establishment|commercial\s+kitchen|commissary|brewery|bar|cafe|catering|food\s+truck|daycare|child\s*care)\b", job, re.I))
    if v1.segment == "residential" and not commercial_food_operation:
        forbidden["health_food"] = "residential non-food scope must not include commercial health/food review"
        forbidden["wastewater_pretreatment_fog"] = "residential non-food scope must not include commercial FOG/pretreatment review"
    if ("no_plumbing" in negatives or "no_mep" in negatives) and "plumbing" not in positives:
        forbidden["plumbing"] = "explicit no-plumbing/no-MEP request fact forbids plumbing family/conditional"
        forbidden["wastewater_pretreatment_fog"] = "explicit no-plumbing/no-MEP request fact forbids FOG/pretreatment family"
    if "no_electrical" in negatives and "electrical" not in positives:
        forbidden["electrical"] = "explicit no-electrical request fact forbids electrical family"
    if "no_mechanical" in negatives and "mechanical" not in positives:
        forbidden["mechanical"] = "explicit no-mechanical request fact forbids mechanical family"
    return forbidden


def _scope_facts_v2_required_document_floors(job: str, v1: ScopeFacts) -> dict[str, str]:
    docs: dict[str, str] = {}
    if v1.segment == "residential" and re.search(r"\b(?:addition|new\s+foundation|foundation)\b", job, re.I):
        docs["structural"] = "residential addition/foundation scope requires structural/foundation drawings in the packet"
    return docs


def _scope_facts_v2_repair_exemption_candidate(job: str) -> bool:
    has_repair = bool(re.search(r"\b(?:drywall|plaster)\b", job, re.I) and re.search(r"\b(?:repair|replace|replacement|patch)\b", job, re.I))
    guarded_out = bool(
        re.search(r"\b(?:fire[- ]rated|rated\s+assembly|framing|rewire|new\s+circuit)\b", job, re.I)
        or (re.search(r"\bstructural\b", job, re.I) and not re.search(r"\bno\s+structural\b", job, re.I))
        or (re.search(r"\belectrical\b", job, re.I) and not re.search(r"\bno\s+electrical\b", job, re.I))
        or (re.search(r"\bplumbing\b", job, re.I) and not re.search(r"\bno\s+plumbing\b", job, re.I))
        or re.search(r"\b(?:mechanical|mep)\b", job, re.I)
    )
    explicit_negatives = bool(re.search(r"\bno\s+structural\b", job, re.I) and re.search(r"\bno\s+electrical\b", job, re.I) and re.search(r"\bno\s+plumbing\b", job, re.I))
    return has_repair and explicit_negatives and not guarded_out


def build_scope_facts_v2(job_type: str, city: str = "", state: str = "", *, job_category: str | None = None, vertical: str | None = None, scope_contract: dict[str, Any] | None = None) -> ScopeFactsV2:
    v1 = build_scope_facts(job_type, city, state, job_category=job_category, vertical=vertical, scope_contract=scope_contract if isinstance(scope_contract, dict) else None)
    job = _norm(job_type)
    positives = _scope_facts_v2_positive(job, v1)
    negatives = _scope_facts_v2_negative(job, v1)
    floors = _scope_facts_v2_family_floors(job, v1, positives)
    forbidden_families = _scope_facts_v2_forbidden_families(job, v1, negatives, positives)
    document_floors = _scope_facts_v2_required_document_floors(job, v1)
    repair_exemption_candidate = _scope_facts_v2_repair_exemption_candidate(job)
    service_amperage = _extract_service_amperage(job)
    valuation = _extract_int_money_or_plain(job, value_words=("job value", "valuation", "project value", "value"))
    rack_height = _extract_rack_height(job)
    return ScopeFactsV2(
        segment=v1.segment,
        construction_class=v1.construction_class,
        trade_signals=v1.trade_signals,
        special_signals=v1.special_signals,
        negative_scope_facts=v1.negative_scope_facts,
        dominant_family=v1.dominant_family,
        vertical=v1.vertical,
        request_scope_text=v1.request_scope_text,
        positive_facts=frozenset(positives),
        negative_facts=frozenset(negatives),
        occupancy_change="use_change" in positives,
        service_amperage=service_amperage,
        valuation_usd=valuation,
        rack_height_ft=rack_height,
        mandatory_family_floors=floors,
        forbidden_families=forbidden_families,
        required_documents_floor=document_floors,
        repair_exemption_candidate=repair_exemption_candidate,
    )


def _evidence_spans(job: str, pattern: str) -> tuple[str, ...]:
    out: list[str] = []
    for match in re.finditer(pattern, job, re.I):
        text = re.sub(r"\s+", " ", match.group(0)).strip()
        if text and text.lower() not in {x.lower() for x in out}:
            out.append(text)
    return tuple(out)


def _fact(value: TriFact, evidence: tuple[str, ...] = ()) -> Fact:
    return Fact(value, evidence if value in {TriFact.TRUE, TriFact.FALSE} else ())


def _detect_change_of_use(job: str) -> ChangeOfUse | None:
    patterns = (
        (r"\bwarehouse\s+to\s+(?:indoor\s+)?(pickleball|assembly|gym|fitness|church|event|recreation)", "warehouse", "pickleball", "A-3"),
        (r"\bchange\s+(?:use|of\s+use|of\s+occupancy)\s+from\s+([a-z\s]+?)\s+to\s+([a-z\s]+?)(?:\s+with|,|$)", "", "", ""),
        (r"\b(retail|storefront|office|warehouse)\s+(?:to|into)\s+(restaurant|bar|brewery|fitness|gym|assembly|pickleball)", "", "", ""),
    )
    for pattern, default_from, default_to, default_group in patterns:
        match = re.search(pattern, job, re.I)
        if not match:
            continue
        from_use = default_from or (match.group(1).strip().split()[0] if match.lastindex and match.lastindex >= 1 else "")
        to_use = default_to or (match.group(2).strip().split()[0] if match.lastindex and match.lastindex >= 2 else "")
        if to_use == "pickleball":
            group = "A-3"
        elif to_use in {"assembly", "gym", "fitness", "church", "event", "recreation"}:
            group = "A"
        elif to_use in {"restaurant", "bar", "brewery"}:
            group = "A-2"
        else:
            group = default_group
        return ChangeOfUse(from_use=from_use, to_use=to_use, to_occupancy_group=group, evidence=(match.group(0),))
    return None


def build_scope_facts_v3(job_type: str, city: str = "", state: str = "", *, job_category: str | None = None, vertical: str | None = None, scope_contract: dict[str, Any] | None = None) -> ScopeFactsV3:
    v2 = build_scope_facts_v2(job_type, city, state, job_category=job_category, vertical=vertical, scope_contract=scope_contract)
    job = _norm(job_type)
    segment = v2.segment

    residential_outdoor = _evidence_spans(job, r"\b(?:outdoor\s+kitchen|grill|barbecue|bbq)\b") if segment == "residential" else ()
    food_true = _evidence_spans(job, r"\b(?:restaurant|commercial\s+kitchen|food\s+service|food\s+establishment|commissary|brewery|bar|cafe|catering)\b") if segment == "commercial" else ()
    no_food = _evidence_spans(job, r"\b(?:no\s+food\s+service|residential\s+(?:outdoor\s+)?kitchen|homeowner|single[- ]family|grill)\b")
    grease_true = _evidence_spans(job, r"\b(?:grease\s+interceptor|fog|commercial\s+cooking|type\s*i\s+hood|type\s*1\s+hood|hood\s+suppression)\b") if segment == "commercial" else ()
    co2 = _evidence_spans(job, r"\b(?:co2|co₂|carbon\s+dioxide)\s+(?:enrichment|system|tank|generator|piping)?\b")
    haz = list(co2)
    haz.extend(_evidence_spans(job, r"\b(?:hazmat|hazardous\s+materials?|compressed\s+gas|flammable|pesticide|cannabis\s+cultivation)\b"))
    structural_true = _evidence_spans(job, r"\b(?:structural|masonry|lintel|load[- ]bearing|foundation|facade\s+repair|fa[cç]ade\s+repair|scaffolding|structural\s+repair)\b")
    structural_false = _evidence_spans(job, r"\b(?:no\s+structural(?:\s+changes?)?|non[- ]structural|cosmetic\s+only)\b")
    facade_ctx = bool(re.search(r"\b(?:facade|fa[cç]ade|storefront|exterior)\b", job, re.I))
    structural_kinds = tuple(k for k, pat in (
        ("masonry", r"\bmasonry\b"), ("lintel", r"\blintel\b"), ("load_bearing", r"\bload[- ]bearing\b"),
        ("foundation", r"\bfoundation\b"), ("facade", r"\bfa[cç]ade|facade\b"),
    ) if re.search(pat, job, re.I))
    change = _detect_change_of_use(job)
    assembly_evidence = tuple(change.evidence) if change and (change.to_occupancy_group or "").startswith("A") else _evidence_spans(job, r"\b(?:assembly|occupant\s+load|pickleball|fitness\s+studio|gym)\b")
    new_circuit_evidence = _evidence_spans(job, r"\b(?:new\s+circuit|add\s+(?:a\s+)?circuit|panel\s+upgrade|service\s+upgrade|new\s+equipment\s+feed|600\s*(?:amp|a))\b")
    existing_circuit_evidence = _evidence_spans(job, r"\b(?:existing\s+boxes|existing\s+circuits?|no\s+new\s+circuits?|no\s+panel\s+work|replace\s+\d*\s*(?:kitchen\s+)?(?:outlets?|receptacles?)|gfci)\b")
    gas_evidence = _evidence_spans(job, r"\b(?:gas\s+(?:line|piping|permit|pressure|appliance)|fuel\s+gas|propane)\b")

    haz_kinds: list[str] = []
    if co2:
        haz_kinds.append("co2")
    if re.search(r"\b(?:compressed\s+gas)\b", job, re.I):
        haz_kinds.append("compressed_gas")
    if re.search(r"\b(?:cannabis|pesticide)\b", job, re.I):
        haz_kinds.append("pesticide")

    floors = dict(v2.mandatory_family_floors)
    docs = dict(v2.required_documents_floor)
    if co2:
        floors["fire_hazmat_co2"] = "CO2 enrichment / hazardous gas system requires fire-prevention review"
        docs["co2_system"] = "CO2 enrichment/hazardous gas system requires design, detection, and disclosure documents"
    if assembly_evidence:
        floors["fire_life_safety_assembly"] = "assembly/change-of-use occupant-load scope requires fire/life-safety review"
        floors.setdefault("co_change_of_occupancy", "change of use/occupancy requires certificate-of-occupancy review")
        docs["assembly_life_safety"] = "assembly/change-of-use scope requires life-safety and occupant-load documents"
    if structural_true and not structural_false:
        docs["structural_engineering"] = "structural masonry/lintel/facade scope requires engineering details"
    if gas_evidence:
        docs["gas_pressure_test"] = "fuel-gas scope requires pressure-test documentation"

    forbidden = dict(v2.forbidden_families)
    if segment == "residential" and not food_true:
        forbidden["health_food"] = "residential non-food scope has FALSE food-establishment fact"
        forbidden["wastewater_pretreatment_fog"] = "residential non-food scope has FALSE commercial grease/FOG fact"

    return ScopeFactsV3(
        segment=v2.segment,
        construction_class=v2.construction_class,
        trade_signals=v2.trade_signals,
        special_signals=v2.special_signals,
        negative_scope_facts=v2.negative_scope_facts,
        dominant_family=v2.dominant_family,
        vertical=v2.vertical,
        request_scope_text=v2.request_scope_text,
        positive_facts=v2.positive_facts,
        negative_facts=v2.negative_facts,
        occupancy_change=v2.occupancy_change,
        service_amperage=v2.service_amperage,
        valuation_usd=v2.valuation_usd,
        rack_height_ft=v2.rack_height_ft,
        mandatory_family_floors=floors,
        forbidden_families=forbidden,
        required_documents_floor=docs,
        repair_exemption_candidate=v2.repair_exemption_candidate,
        food_establishment=_fact(TriFact.TRUE, food_true) if food_true else (_fact(TriFact.FALSE, no_food or residential_outdoor) if no_food or residential_outdoor or segment == "residential" else _fact(TriFact.UNKNOWN)),
        grease_discharge=_fact(TriFact.TRUE, grease_true) if grease_true else (_fact(TriFact.FALSE, no_food or residential_outdoor) if no_food or residential_outdoor or segment == "residential" else _fact(TriFact.UNKNOWN)),
        co2_enrichment=_fact(TriFact.TRUE, co2) if co2 else _fact(TriFact.UNKNOWN),
        hazardous_materials=_fact(TriFact.TRUE, tuple(haz)) if haz else _fact(TriFact.UNKNOWN),
        hazmat_kinds=tuple(dict.fromkeys(haz_kinds)),
        structural_work=_fact(TriFact.TRUE, structural_true) if structural_true and not structural_false else (_fact(TriFact.FALSE, structural_false) if structural_false else _fact(TriFact.UNKNOWN)),
        structural_kinds=structural_kinds,
        facade_scope="structural_facade" if facade_ctx and structural_true and not structural_false else ("storefront_glazing_only" if facade_ctx else "none"),
        change_of_use=change,
        assembly_occupancy=_fact(TriFact.TRUE, assembly_evidence) if assembly_evidence else _fact(TriFact.UNKNOWN),
        electrical_new_circuits=_fact(TriFact.TRUE, new_circuit_evidence) if new_circuit_evidence else (_fact(TriFact.FALSE, existing_circuit_evidence) if existing_circuit_evidence else _fact(TriFact.UNKNOWN)),
        residential_outdoor_cooking=_fact(TriFact.TRUE, residential_outdoor) if residential_outdoor else _fact(TriFact.UNKNOWN),
        gas_fuel_work=_fact(TriFact.TRUE, gas_evidence) if gas_evidence else _fact(TriFact.UNKNOWN),
    )


def _family_support_sets_v4(job: str, v3: ScopeFactsV3) -> tuple[set[str], set[str], set[str], dict[str, str], set[str]]:
    positives = set(v3.positive_facts)
    negatives = set(v3.negative_facts)
    positive_families: set[str] = set()
    negative_families: set[str] = set()
    forbidden = dict(v3.forbidden_families)

    if v3.segment == "commercial" and ("commercial_ti" in positives or "use_change" in positives):
        positive_families.add("building_ti")
    if positives & {"building", "addition", "structural", "demolition", "racking", "new_dwelling_unit", "exterior"}:
        positive_families.add("building")
    if "racking" in positives:
        positive_families.add("racking")
    if "electrical" in positives:
        positive_families.add("electrical")
    if "mechanical" in positives:
        positive_families.add("mechanical")
    if "plumbing" in positives:
        positive_families.add("plumbing")
    if "gas" in positives:
        positive_families.add("gas")
    if positives & {"fire_alarm"}:
        positive_families.add("fire_alarm")
    if positives & {"fire_suppression", "hood_wet_chemical"}:
        positive_families.add("fire_suppression")
    if positives & {"food_service", "health_food"}:
        positive_families.add("health_food")
    if positives & {"grease_generating"}:
        positive_families.add("wastewater_pretreatment_fog")
    if positives & {"planning_zoning", "sign", "historic_district"}:
        positive_families.add("planning_zoning")
    if positives & {"historic_district"}:
        positive_families.add("historic_review")
    if positives & {"co_change_of_occupancy", "use_change"}:
        positive_families.add("co_change_of_occupancy")
    if "sign" in positives:
        positive_families.add("sign")
    if "sign_illuminated" in positives:
        positive_families.add("electrical")
    if re.search(r"\b(?:solar|pv|photovoltaic)\b", job, re.I):
        positives.add("solar_pv")
        positive_families.add("solar_pv")
    if re.search(r"\b(?:battery|ess|energy\s+storage|backup)\b", job, re.I):
        positives.add("battery_storage")
        positives.add("electrical")
        positive_families.add("battery_storage")
        positive_families.add("electrical")
    if re.search(r"\b(?:gas\s+(?:line|piping|equipment)|gas[- ]fired|gas\s+dryers?|fuel\s+gas)\b", job, re.I):
        positives.add("gas")
        positives.add("plumbing")
        positive_families.add("gas")
        positive_families.add("plumbing")
    if re.search(r"\b(?:medical|dental|clinic|veterinary|x[- ]?ray|exam\s+rooms?|kennels?)\b", job, re.I):
        positives.add("electrical")
        positives.add("mechanical")
        positive_families.add("electrical")
        positive_families.add("mechanical")
    if re.search(r"\b(?:ductless|mini[- ]?split|split[- ]system|heat\s+pump|refrigerant|condenser)\b", job, re.I):
        positives.add("refrigeration")
        positive_families.add("refrigeration")
    if re.search(r"\b(?:change\s+(?:to|from)|convert(?:ing|ed)?\s+(?:to|from)|conversion\s+(?:to|from)|occupant\s+load)\b", job, re.I) and v3.segment == "commercial":
        positives.update({"use_change", "commercial_ti", "building"})
        positive_families.update({"building_ti", "co_change_of_occupancy", "planning_zoning"})
    if re.search(r"\b(?:wine\s+bar|bar|restaurant|food\s+prep|hoodless\s+food)\b", job, re.I) and v3.segment == "commercial" and "use_change" in positives:
        positives.update({"electrical", "mechanical"})
        positive_families.update({"electrical", "mechanical"})
    if re.search(r"\b(?:laundromat|laundry|gas\s+dryers?)\b", job, re.I):
        for fam in ("food_service", "health_food"):
            positives.discard(fam)
        positive_families.discard("health_food")
    if re.search(r"\bgrease\s+interceptor\s+replacement\b", job, re.I) and re.search(r"\bsame\s+location\b", job, re.I):
        for fam in ("fire_suppression", "hood_wet_chemical"):
            positives.discard(fam)
            positive_families.discard(fam)
    if re.search(r"\b(?:brewery|taproom|wine\s+bar|cocktail\s+bar|liquor|alcohol\s+service)\b", job, re.I):
        positives.add("liquor")
        positive_families.add("liquor")
    positive_families.update(v3.mandatory_family_floors.keys())

    negative_map = {
        "no_electrical": "electrical",
        "no_mechanical": "mechanical",
        "no_plumbing": "plumbing",
        "no_use_change": "co_change_of_occupancy",
        "no_food_service_change": "health_food",
        "no_sprinkler_alteration": "fire_suppression",
    }
    for neg, fam in negative_map.items():
        if neg in negatives:
            negative_families.add(fam)
            if fam not in positive_families:
                forbidden[fam] = f"explicit request negative fact {neg} forbids {fam} hard-required row"
    if "no_use_change" in negatives:
        positives.discard("use_change")
        positives.discard("co_change_of_occupancy")
        positives.discard("change_of_use_ti")
        positives.discard("commercial_ti")
        positive_families.discard("co_change_of_occupancy")
        if "building" not in positives:
            positive_families.discard("building_ti")
    if "no_mechanical" in negatives:
        positives.discard("mechanical")
        positive_families.discard("mechanical")
    if "no_electrical" in negatives:
        positives.discard("electrical")
        positive_families.discard("electrical")
    if "no_plumbing" in negatives:
        positives.discard("plumbing")
        positive_families.discard("plumbing")
    # V3 may add a broad no_mep sentinel for "only/no mechanical" wording.
    # Preserve explicit positive trade facts instead of letting that broad
    # sentinel veto electrical/plumbing rows that the user actually asked for.
    if positives & {"electrical", "plumbing", "mechanical"}:
        negatives.discard("no_mep")
    if re.search(r"\b(?:ev\s+charger|electric\s+vehicle\s+charger|level\s*(?:2|ii)\s+charger)\b", job, re.I) and not re.search(r"\b(?:structural|framing|trench|new\s+building|addition|wall|foundation)\b", job, re.I):
        positive_families.discard("building")
        positives.discard("building")
    # V4 is emission-time scope-aware: generic commercial TI/building rows do
    # not hard-require every MEP/planning/CO family. Preserve only families with
    # explicit request evidence or a true use-change/sign/historic trigger.
    explicit_electrical = bool(_evidence_spans(job, r"\b(?:electrical|wiring|circuits?|service|panel|lighting|lights?|grow\s+lights?|x[- ]?ray|receptacles?|outlets?|power|generator|ev\s+charger|emergency\s+power|solar|pv|photovoltaic|battery|ess|illuminated|lit|wine\s+bar|bar|food\s+prep)\b"))
    explicit_mechanical = bool(_evidence_spans(job, r"\b(?:mechanical|hvac|ventilation|diffuser|duct(?:work)?|rtu|rooftop|fume\s+hoods?|hood|exhaust|odor\s+control|fermentation\s+tanks?|gas[- ]fired\s+equipment|cooking\s+equipment|mini[- ]?split|ductless|heat\s+pump|condenser|air\s+conditioner|medical|dental|clinic|veterinary|x[- ]?ray|exam\s+rooms?|kennels?|wine\s+bar|bar|food\s+prep)\b"))
    explicit_plumbing = bool(_evidence_spans(job, r"\b(?:plumbing|fixtures?|sinks?|drains?|floor\s+drains?|water|sewer|kitchen|bath(?:room)?|showers?|gas\s+piping|gas\s+line|gas|grease\s+interceptor|nitrous\s+lines?|utilities?)\b"))
    if not explicit_electrical:
        positives.discard("electrical")
        positive_families.discard("electrical")
    if not explicit_mechanical:
        positives.discard("mechanical")
        positive_families.discard("mechanical")
    if not explicit_plumbing:
        positives.discard("plumbing")
        positives.discard("gas")
        positive_families.discard("plumbing")
        positive_families.discard("gas")
    if "use_change" not in positives:
        positive_families.discard("co_change_of_occupancy")
        positives.discard("co_change_of_occupancy")
        if not (positives & {"sign", "historic_district"}):
            positive_families.discard("planning_zoning")
            positives.discard("planning_zoning")
    if v3.segment != "commercial":
        positive_families.discard("co_change_of_occupancy")
        positives.discard("co_change_of_occupancy")
    if {"new_dwelling_unit", "utilities_connected"}.issubset(positives):
        positives.update({"electrical", "plumbing", "mechanical"})
        positive_families.update({"electrical", "plumbing", "mechanical"})
    if re.search(r"\b(?:same[- ]size\s+windows?|replace\s+same[- ]size\s+windows?)\b", job, re.I) and re.search(r"\b(?:no\s+structural|no\s+wall\s+framing|same[- ]size)\b", job, re.I):
        negative_families.add("structural")
    return positives, positive_families, negative_families, forbidden, negatives


def _apply_phase0_scope_axis_closure(
    job: str,
    segment: str,
    positives: set[str],
    positive_families: set[str],
    negatives: set[str],
    negative_families: set[str],
    forbidden: dict[str, str],
) -> None:
    """Add request-derived axes for the Phase 0 scope-fact contract.

    This is intentionally archetype/phrase based, not case-ID based.  It fills
    the gaps Fable flagged: deterministic facts must capture explicit work axes
    (gas, elevator, pool/health, refrigeration, site/structural, etc.) before
    any scope→family matrix can safely run.  Negatives are retained as facts even
    when a different same-request positive remains; conflict handling belongs to
    the validator/matrix layer, not silent extraction.
    """

    def has(pattern: str) -> bool:
        return bool(re.search(pattern, job, re.I))

    def add_positive(axis: str) -> None:
        positives.add(axis)
        family_map = {
            "electrical": "electrical",
            "plumbing": "plumbing",
            "mechanical": "mechanical",
            "gas": "gas",
            "refrigeration": "refrigeration",
            "sign": "sign",
            "elevator": "elevator",
            "environmental_fuel": "environmental",
            "structural": "building",
            "exterior_site_roof_support": "building",
            "fire_life_safety": "fire_suppression",
            "health_food_pool": "health_food",
            "change_of_use_ti": "building_ti" if segment == "commercial" else "building",
        }
        fam = family_map.get(axis)
        if fam:
            positive_families.add(fam)
        if axis == "change_of_use_ti" and segment == "commercial":
            positive_families.update({"building_ti", "co_change_of_occupancy"})
        if axis == "health_food_pool" and has(r"\b(?:pool|pool\s+deck|ada\s+lift)\b"):
            positive_families.add("pool")

    def add_negative(axis: str, family: str | None = None) -> None:
        negatives.add(axis)
        if family:
            negative_families.add(family)

    # Positive axes: explicit work requested by the customer.
    if has(r"\b(?:electrical|electric|circuits?|receptacles?|outlets?|gfci|subpanel|sub-panel|panel|service|disconnect|feeder|lighting|lights?|ups|low[- ]voltage|data|x[- ]?ray|charger|bonding|solar|pv|photovoltaic|transformer|emergency\s+power|battery|ess|automatic\s+transfer\s+switch|ats|generator|mini[- ]?split|spray\s+booth|exhaust\s+fan|sump\s+pump|ejector\s+pump|sewage\s+ejector|lift\s+station\s+pump)\b"):
        add_positive("electrical")
    if has(r"\b(?:plumbing|sinks?|toilets?|bath(?:room)?|restrooms?|showers?|drains?|floor\s+drains?|water\s+lines?|water\b|sewer|condensate|grease\s+interceptor|mop\s+sink|domestic\s+water|repipe|piping|backflow|cistern|irrigation|pool\s+equipment|gas\s+(?:line|branch|piping|range|reconnection|connection))\b"):
        add_positive("plumbing")
    if has(r"\b(?:mechanical|hvac|heat\s+pump|mini[- ]?split|ductless|furnace|air\s+condition(?:er|ing)|\bac\b|a/c|fans?|bath\s+fan|exhaust|ducts?|ductwork|ventilation|rtu|rooftop\s+unit|makeup\s+air|hood|heater|cooling|supplemental\s+cooling|dryer|condenser|refrigeration\s+equipment|cold\s+storage|refrigerated\s+(?:room|space|storage)|pool\s+heater)\b"):
        add_positive("mechanical")
    if has(r"\b(?:gas\s+(?:range|branch|line|piping|reconnection|connection|pool\s+heater|heaters?|makeup\s+air)|fuel[- ]gas|gas[- ]fired|fuel\s+tank|diesel|propane|gas\s+furnace|gas\s+reconnect|existing\s+gas)\b"):
        add_positive("gas")
        add_positive("plumbing")
    if has(r"\b(?:refrigeration|refrigerated|walk[- ]in|walk\s+in|cooler|freezer|refrigerant|line[- ]set|line\s+set|mini[- ]?split|cold\s+storage)\b"):
        add_positive("refrigeration")
    if has(r"\b(?:fire\s+alarm|fire\s+suppression|sprinklers?|wet\s+chemical|ansul|fire\s+detection|battery\s+energy\s+storage|lithium|bess|hazardous|spray\s+booth|daycare|childcare|church\s+assembly|assembly|occupant\s+load|classrooms?|high[- ]pile|racking|mezzanine|type\s*i\s+hood|type\s*1\s+hood|clean\s+agent)\b"):
        add_positive("fire_life_safety")
    clinical_radiation_scope = has(r"\b(?:dental|x[- ]?ray|radiology)\b")
    if has(r"\b(?:restaurant|food\s+truck|commissary|coffee\s+kiosk|coffee\s+shop|cocktail\s+bar|brewery|brewpub|bakery|grocery|salon|medical|clinic|pharmacy|daycare|childcare|public\s+pool|hotel\s+outdoor\s+pool|pool\s+deck|grease|type\s*i\s+hood|type\s*1\s+hood)\b") and not clinical_radiation_scope:
        add_positive("health_food_pool")
    if clinical_radiation_scope:
        # Non-food clinical/radiation regulation is a separate, potentially
        # conditional review path. Do not project it into the food/pool family.
        add_positive("medical_radiation_review")
    if has(r"\b(?:change\s+of\s+use|change-of-use|change\s+of\s+occupancy|former\s+(?:retail|office)|tenant\s+improvement|\bti\b|vanilla\s+shell|buildout|build-out|fit[- ]out|retail\s+bay\s+to|office\s+to|split\s+existing\s+retail\s+suite|convert\s+(?:former|retail|vacant\s+retail|single-story\s+office|attached\s+garage|detached\s+garage)|garage\s+conversion|accessory\s+dwelling|adu\b|drive[- ]through\s+coffee\s+kiosk|coffee\s+kiosk)\b"):
        add_positive("change_of_use_ti")
    if has(r"\b(?:sign|signage|menu\s+boards?|window\s+vinyl)\b") and not has(r"\bexit\s+signs?\b"):
        add_positive("sign")
    if has(r"\b(?:structural|load[- ]bearing|header|beam|lvl|foundation|footings?|mezzanine|racking|guardrails?|stairs?|masonry\s+opening|lintel|retaining\s+wall|helical\s+piers?|anchored|anchors?|rooftop\s+cellular|antenna|equipment\s+cabinets?|canopy|steel|two[- ]story|elevated|carport|deck|porch|fence|shed|skylights?|exterior\s+stairs?|storm\s+shelter|slab|bollards?|modular\s+classrooms?|temporary\s+modular|cold\s+storage\s+room|insulated\s+panels?|data\s+room|accessible\s+restroom|single\s+accessible\s+restroom|restroom\s+inside\s+warehouse)\b"):
        add_positive("structural")
    if has(r"\b(?:build|construct|place|add|install)\b.{0,80}\b(?:modular\s+classrooms?|temporary\s+modular|cold\s+storage\s+room|data\s+room|accessible\s+restroom|single\s+accessible\s+restroom|restroom\s+inside\s+warehouse|warehouse\s+restroom)\b"):
        add_positive("structural")
    if has(r"\belevator\b"):
        add_positive("elevator")
    if has(r"\b(?:oil\s+tank|fuel\s+tank|diesel|environmental)\b"):
        add_positive("environmental_fuel")

    # Negative axes: explicit ceilings/absence statements.
    if has(r"\b(?:no\s+electrical|without\s+electrical|no\s+new\s+electrical|existing\s+electrical\s+switch|existing\s+dedicated\s+circuit|no\s+illumination|non[- ]illuminated)\b"):
        add_negative("no_electrical", "electrical")
    if has(r"\b(?:no\s+plumbing|without\s+plumbing|no\s+bath(?:room)?|no\s+sewer|no\s+water|no\s+drains?)\b"):
        add_negative("no_plumbing", "plumbing")
    if has(r"\b(?:no\s+mechanical|no\s+hvac|without\s+mechanical|no\s+kitchen\s+work)\b"):
        add_negative("no_mechanical", "mechanical")
    if has(r"\b(?:no\s+mep|no\s+utilities)\b"):
        add_negative("no_mep")
        add_negative("no_electrical", "electrical")
        add_negative("no_plumbing", "plumbing")
        add_negative("no_mechanical", "mechanical")
    if has(r"\b(?:no\s+structural(?:\s+changes?)?|no\s+header\s+changes|no\s+wall\s+layout\s+changes|no\s+walls?|non[- ]load[- ]bearing|non\s+load\s+bearing|no\s+truss\s+cutting|no\s+structural\s+demolition|same[- ]size|same\s+size|same\s+footprint)\b"):
        add_negative("no_structural", "structural")
    if has(r"\b(?:no\s+signage|no\s+sign\s+(?:work|copy)|no\s+lighting)\b"):
        add_negative("no_signage", "sign")
    if has(r"\b(?:no\s+food\s+service|same\s+cooking\s+line|like[- ]for[- ]like|same[- ]for[- ]same|no\s+kitchen\s+work)\b"):
        add_negative("no_food_service_change", "health_food")
    if has(r"\b(?:same\s+cooking\s+line|same\s+use|same\s+occupancy|operating\s+restaurant)\b"):
        add_negative("no_use_change", "co_change_of_occupancy")
    if has(r"\b(?:no\s+change\s+of\s+use|no\s+occupancy\s+change|no\s+change\s+of\s+occupancy|same\s+footprint|same\s+size|same[- ]size|like[- ]for[- ]like)\b"):
        add_negative("no_use_change", "co_change_of_occupancy")
    if segment == "residential" and has(r"\b(?:convert|conversion)\b.{0,40}\b(?:tub|shower)\b|\b(?:tub|shower)\b.{0,40}\b(?:convert|conversion)\b") and not has(r"\b(?:change\s+of\s+(?:use|occupancy)|occupancy\s+change|garage\s+conversion|adu|accessory\s+dwelling|bedroom|habitable|dwelling\s+unit|living\s+space)\b"):
        add_negative("no_use_change", "co_change_of_occupancy")
    if segment == "commercial" and has(r"\bsplit\s+existing\s+retail\s+suite\b") and not has(r"\b(?:change\s+of\s+(?:use|occupancy)|occupancy\s+change|retail\s+to|office\s+to|warehouse\s+to|assembly|fitness|restaurant|bar)\b"):
        add_negative("no_use_change", "co_change_of_occupancy")
    if has(r"\b(?:paint,\s*carpet\s+tile\s+replacement\s+and\s+furniture\s+only|cabinets\s+and\s+countertops\s+like\s+for\s+like|cosmetic\s+only|furniture\s+only)\b"):
        add_negative("cosmetic_only")

    # Explicit negative ceilings remove unsupported positives for the same axis,
    # except when another strong positive in the exact same family is present.
    if "no_signage" in negatives and not has(r"\b(?:new\s+sign|install\s+sign|menu\s+board|window\s+vinyl)\b"):
        positives.discard("sign")
        positive_families.discard("sign")
    if "no_mechanical" in negatives and not has(r"\b(?:install|replace|add|new|relocate|swap|connect|reconnect)\b.{0,60}\b(?:mechanical|hvac|heat\s+pump|mini[- ]?split|furnace|air\s+condition(?:er|ing)|\bac\b|a/c|fans?|bath\s+fan|exhaust|ducts?|ductwork|ventilation|rtu|rooftop\s+unit|makeup\s+air|hood|heater|cooling|dryer|condenser|pool\s+heater)\b|\b(?:mechanical|hvac|heat\s+pump|mini[- ]?split|furnace|air\s+condition(?:er|ing)|\bac\b|a/c|fans?|bath\s+fan|exhaust|ducts?|ductwork|ventilation|rtu|rooftop\s+unit|makeup\s+air|hood|heater|cooling|dryer|condenser|pool\s+heater)\b.{0,60}\b(?:install|replace|add|new|relocate|swap|connect|reconnect)\b"):
        positives.discard("mechanical")
        positive_families.discard("mechanical")
        forbidden["mechanical"] = "explicit no-mechanical request fact forbids mechanical hard-required row absent same-family positive work"
    if "cosmetic_only" in negatives:
        for fam in ("building_ti", "co_change_of_occupancy", "planning_zoning"):
            positive_families.discard(fam)
        positives.discard("commercial_ti")
        positives.discard("use_change")
        positives.discard("co_change_of_occupancy")


def build_scope_facts_v4(job_type: str, city: str = "", state: str = "", *, job_category: str | None = None, vertical: str | None = None, scope_contract: dict[str, Any] | None = None) -> ScopeFactsV4:
    v3 = build_scope_facts_v3(job_type, city, state, job_category=job_category, vertical=vertical, scope_contract=scope_contract)
    job = _norm(job_type)
    positives, positive_families, negative_families, forbidden, negatives = _family_support_sets_v4(job, v3)
    explicit_no_food_service = bool(re.search(r"\b(?:no|without)\s+(?:food\s+service|food\s+prep|restaurant|commercial\s+kitchen|cooking|grease|fog)\b", job, re.I))
    if explicit_no_food_service:
        negatives.add("no_food_service_change")
        negative_families.update({"health_food", "wastewater_pretreatment_fog"})
        for token in ("food_service", "health_food", "grease_generating"):
            positives.discard(token)
        for fam in ("health_food", "wastewater_pretreatment_fog"):
            positive_families.discard(fam)
            forbidden[fam] = "explicit request says no food service/grease scope; do not hard-require food or FOG permits"
    hpwh_scope = (v3.segment == "residential") and bool(re.search(r"\b(?:heat\s+pump\s+water\s+heater|hpwh)\b", job, re.I))
    minisplit_scope = (v3.segment == "residential") and bool(re.search(r"\b(?:ductless|mini[- ]?split)\b", job, re.I))
    residential_equipment_scope = hpwh_scope or minisplit_scope
    explicit_field_refrigerant = bool(re.search(r"\b(?:refrigerant|line\s*set|field\s*charg|evacuat(?:e|ion)|braze|new\s+lines?)\b", job, re.I))
    sealed_precharged_minisplit = bool(re.search(r"\b(?:pre[- ]?charged|factory[- ]sealed|sealed\s+system|no\s+(?:new\s+)?line\s*set|existing\s+line\s*set|no\s+field\s+refrigerant)\b", job, re.I))
    seattle_source_backed_exception = (city or "").strip().lower() == "seattle" and (state or "").strip().upper() == "WA"
    refrigeration_demote_scope = hpwh_scope or (minisplit_scope and sealed_precharged_minisplit)
    if refrigeration_demote_scope and not explicit_field_refrigerant and not seattle_source_backed_exception:
        positives.discard("refrigeration")
        positive_families.discard("refrigeration")
        negative_families.add("refrigeration")
        forbidden.setdefault("refrigeration", "residential sealed/precharged HPWH or mini-split scope does not hard-require standalone refrigeration unless field refrigerant work is explicit")
    explicit_building_equipment_work = bool(re.search(r"\b(?:structural|framing|roof\s+penetration|new\s+opening|new\s+wall|foundation|curb\s+cut|concrete\s+pad|equipment\s+platform)\b", job, re.I))
    if residential_equipment_scope and not explicit_building_equipment_work:
        positives.discard("building")
        positive_families.discard("building")
        negative_families.add("building")
        forbidden.setdefault("building", "residential HPWH/mini-split equipment scope does not hard-require standalone building permit absent structural/framing/envelope work")
    _apply_phase0_scope_axis_closure(job, v3.segment, positives, positive_families, negatives, negative_families, forbidden)
    # Phase-0 keyword closure is intentionally broad. Re-apply explicit
    # fire-system negatives so phrases such as "no sprinkler or fire alarm
    # changes" cannot create permit families from the words in the negative.
    affirmative_job = re.sub(r"\b(?:no|without)\b[^;,.]*", "", job)
    explicit_no_sprinkler = bool(re.search(r"\b(?:no|without)\b[^;,.]*\b(?:fire\s+)?sprinklers?\b", job, re.I))
    explicit_no_fire_alarm = bool(re.search(r"\b(?:no|without)\b[^;,.]*\bfire\s+alarms?\b", job, re.I))
    affirmative_sprinkler = bool(re.search(r"\b(?:install|add|alter|modify|relocate|replace|extend)\b[^;,.]*\b(?:fire\s+)?sprinklers?\b", affirmative_job, re.I))
    affirmative_fire_alarm = bool(re.search(r"\b(?:install|add|alter|modify|relocate|replace|extend)\b[^;,.]*\bfire\s+alarms?\b", affirmative_job, re.I))
    if explicit_no_sprinkler and not affirmative_sprinkler:
        negatives.add("no_sprinkler_alteration")
        negative_families.add("fire_suppression")
        positives.discard("fire_suppression")
        positive_families.discard("fire_suppression")
        forbidden["fire_suppression"] = "explicit request says no sprinkler/suppression changes"
    if explicit_no_fire_alarm and not affirmative_fire_alarm:
        negatives.add("no_fire_alarm_work")
        negative_families.add("fire_alarm")
        positives.discard("fire_alarm")
        positive_families.discard("fire_alarm")
        forbidden["fire_alarm"] = "explicit request says no fire-alarm changes"
    if explicit_no_sprinkler and explicit_no_fire_alarm and not (affirmative_sprinkler or affirmative_fire_alarm):
        positives.discard("fire_life_safety")
    # Re-apply explicit negative fuel-gas/plumbing ceilings after closure.
    explicit_no_gas_piping = bool(
        re.search(
            r"\b(?:no|without)\s+(?:new\s+)?(?:fuel[- ]?gas|gas)(?:\s+(?:line|lines|piping|pipe|work|connection|connections))?\b",
            job,
            re.I,
        )
    )
    affirmative_gas_piping = bool(
        re.search(
            r"\b(?:new|install|extend|replace|repair|reroute|run|add)\b.{0,48}\b(?:fuel[- ]?gas|gas)\s+(?:line|lines|piping|pipe|branch|connection|connections)\b"
            r"|\b(?:fuel[- ]?gas|gas)\s+(?:line|lines|piping|pipe|branch)\b",
            affirmative_job,
            re.I,
        )
    )
    affirmative_ordinary_plumbing = bool(
        re.search(
            r"\b(?:water\s+(?:line|lines|supply|service|piping|pipe)|sewer|sanitary|waste\s+(?:line|piping)|"
            r"drain|drainage|plumbing\s+fixture|sink|faucet|toilet|urinal|shower|bathtub|water\s+heater|"
            r"backflow|irrigation|sump\s+pump|grease\s+(?:trap|interceptor)|condensate\s+drain)\b",
            affirmative_job,
            re.I,
        )
    )
    if explicit_no_gas_piping and not affirmative_gas_piping:
        negatives.add("no_gas")
        negative_families.add("gas")
        positives.discard("gas")
        positive_families.discard("gas")
        forbidden["gas"] = "explicit no-gas/no-new-gas-piping scope forbids a standalone fuel-gas filing row"
        if not affirmative_ordinary_plumbing:
            negative_families.add("plumbing")
            positives.discard("plumbing")
            positive_families.discard("plumbing")
            forbidden["plumbing"] = "appliance-only/no-new-gas-piping scope has no independent plumbing filing trigger"
    if "no_plumbing" in negatives and not affirmative_ordinary_plumbing and not affirmative_gas_piping:
        negative_families.add("plumbing")
        positives.discard("plumbing")
        positive_families.discard("plumbing")
    if "no_use_change" in negatives:
        for token in ("use_change", "co_change_of_occupancy", "change_of_use_ti"):
            positives.discard(token)
        for fam in ("co_change_of_occupancy", "planning_zoning"):
            positive_families.discard(fam)
            negative_families.add(fam)
        forbidden.setdefault("co_change_of_occupancy", "request facts indicate no occupancy/use change; do not hard-require certificate/change-of-occupancy review")
    if hpwh_scope or (v3.segment == "residential" and "water_heater_only" in negatives):
        # A heat-pump/electric/gas water heater is still a water-heater/plumbing
        # scope unless the customer separately describes HVAC/refrigerant or
        # building/envelope work. The phase-0 keyword closure sees "heat pump" /
        # "heater" and can otherwise re-add mechanical/refrigeration/building
        # after the earlier ceiling above.
        for token in ("mechanical", "refrigeration", "building", "building_ti"):
            positives.discard(token)
            positive_families.discard(token)
        negative_families.update({"mechanical", "refrigeration", "building"})
        forbidden.setdefault("mechanical", "water-heater-only replacement does not hard-require standalone HVAC/mechanical permit absent separate HVAC scope")
        forbidden.setdefault("refrigeration", "water-heater-only replacement does not hard-require standalone refrigeration absent field refrigerant work")
        forbidden.setdefault("building", "water-heater-only replacement does not hard-require standalone building permit absent structural/framing/envelope work")
        if re.search(r"\b(?:existing\s+dedicated\s+circuit|existing\s+circuit|no\s+new\s+circuit|no\s+panel\s+work)\b", job, re.I) and not re.search(r"\b(?:new\s+circuit|add\s+(?:a\s+)?circuit|panel\s+upgrade|service\s+upgrade|new\s+wiring)\b", job, re.I):
            positives.discard("electrical")
            positive_families.discard("electrical")
            negative_families.add("electrical")
            forbidden.setdefault("electrical", "water-heater scope uses existing dedicated circuit; do not hard-require electrical absent new wiring/panel/circuit work")
    electric_dryer_circuit_only = bool(
        v3.segment == "residential"
        and re.search(r"\b(?:240\s*v|240\s*volt|220\s*v|220\s*volt|dryer\s+(?:circuit|outlet))\b", job, re.I)
        and re.search(r"\bdryer\b", job, re.I)
        and not re.search(r"\b(?:gas\s+dryers?|dryer\s+(?:vent|exhaust|duct)|mechanical|ventilation|exhaust|bath\s+fan|exhaust\s+fan)\b", job, re.I)
    )
    if electric_dryer_circuit_only:
        positives.discard("mechanical")
        positive_families.discard("mechanical")
        negative_families.add("mechanical")
        forbidden.setdefault("mechanical", "electric dryer circuit/outlet scope does not hard-require standalone mechanical absent dryer vent, gas dryer, exhaust, or ventilation work")
    bath_fan_only_scope = bool(
        v3.segment == "residential"
        and re.search(r"\b(?:bath(?:room)?\s+)?(?:exhaust\s+fan|bath\s+fan)\b", job, re.I)
        and not re.search(r"\b(?:plumbing|sinks?|toilets?|showers?|drains?|floor\s+drains?|water\s+lines?|sewer|repipe|piping|backflow|gas\s+(?:line|branch|piping|range|reconnection|connection))\b", job, re.I)
    )
    if bath_fan_only_scope:
        positives.discard("plumbing")
        positive_families.discard("plumbing")
        negative_families.add("plumbing")
        forbidden.setdefault("plumbing", "bathroom exhaust fan/duct scope does not hard-require plumbing absent fixture, drain, water, sewer, or gas work")
    if "no_electrical" in negatives and not re.search(r"\b(?:new\s+circuit|add\s+(?:a\s+)?circuit|panel\s+upgrade|service\s+upgrade|new\s+wiring|new\s+lighting|ups\s+circuits?|feeder|subpanel|electrical\s+panel\s+separation)\b", job, re.I):
        positives.discard("electrical")
        positive_families.discard("electrical")
        negative_families.add("electrical")
        forbidden.setdefault("electrical", "explicit no-electrical/existing-circuit request fact forbids hard-required electrical absent same-family new work")
    mechanical_false = _evidence_spans(job, r"\b(?:no|without)\s+(?:mechanical|hvac|ventilation|duct|ductwork)\b")
    electrical_false = _evidence_spans(job, r"\b(?:no|without)\s+(?:electric(?:al)?|wiring|circuits?|panel|illumination)\b")
    plumbing_false = _evidence_spans(job, r"\b(?:no|without)\s+(?:plumbing|pipes?|drains?|fixtures?|water|sewer)\b")
    gas_false = _evidence_spans(job, r"\b(?:no|without)\s+(?:new\s+)?(?:fuel[- ]?gas|gas)(?:\s+(?:line|lines|piping|pipe|work|connection|connections))?\b")
    building_false = _evidence_spans(job, r"\b(?:no\s+structural(?:\s+changes?)?|non[- ]structural|non\s+load\s+bearing|no\s+wall\s+framing)\b")
    mandatory_floors = dict(v3.mandatory_family_floors)
    if "no_gas" in negatives:
        mandatory_floors.pop("gas", None)
    if "plumbing" in negative_families and "plumbing" not in positive_families:
        mandatory_floors.pop("plumbing", None)
    if "cosmetic_only" in negatives:
        for fam in ("building_ti", "co_change_of_occupancy", "planning_zoning"):
            mandatory_floors.pop(fam, None)
    if "no_use_change" in negatives:
        for fam in ("co_change_of_occupancy", "planning_zoning"):
            mandatory_floors.pop(fam, None)
    occupancy_class = "commercial" if v3.segment == "commercial" else ("residential" if v3.segment == "residential" else "unknown")
    if v3.change_of_use and v3.change_of_use.to_occupancy_group:
        occupancy_class = f"commercial:{v3.change_of_use.to_occupancy_group}" if v3.segment == "commercial" else occupancy_class
    return ScopeFactsV4(
        segment=v3.segment,
        construction_class=v3.construction_class,
        trade_signals=v3.trade_signals,
        special_signals=v3.special_signals,
        negative_scope_facts=v3.negative_scope_facts,
        dominant_family=v3.dominant_family,
        vertical=v3.vertical,
        request_scope_text=v3.request_scope_text,
        positive_facts=frozenset(positives),
        negative_facts=frozenset(negatives),
        occupancy_change=v3.occupancy_change,
        service_amperage=v3.service_amperage,
        valuation_usd=v3.valuation_usd,
        rack_height_ft=v3.rack_height_ft,
        mandatory_family_floors=mandatory_floors,
        forbidden_families=forbidden,
        required_documents_floor=v3.required_documents_floor,
        repair_exemption_candidate=v3.repair_exemption_candidate,
        food_establishment=v3.food_establishment,
        grease_discharge=v3.grease_discharge,
        co2_enrichment=v3.co2_enrichment,
        hazardous_materials=v3.hazardous_materials,
        hazmat_kinds=v3.hazmat_kinds,
        structural_work=_fact(TriFact.FALSE, building_false) if building_false else v3.structural_work,
        structural_kinds=v3.structural_kinds,
        facade_scope=v3.facade_scope,
        change_of_use=v3.change_of_use,
        assembly_occupancy=v3.assembly_occupancy,
        electrical_new_circuits=v3.electrical_new_circuits,
        residential_outdoor_cooking=v3.residential_outdoor_cooking,
        gas_fuel_work=_fact(TriFact.FALSE, gas_false) if gas_false and "gas" not in positive_families else v3.gas_fuel_work,
        occupancy_class=occupancy_class,
        use_change=bool(v3.change_of_use or "use_change" in positives) and "no_use_change" not in negatives,
        request_positive_families=frozenset(positive_families),
        request_negative_families=frozenset(negative_families),
        electrical_work=_fact(TriFact.FALSE, electrical_false) if electrical_false and "electrical" not in positive_families else (_fact(TriFact.TRUE, _evidence_spans(job, r"\b(?:electrical|wiring|circuits?|panel|service|lighting|receptacles?|ev\s+charger|sump\s+pump|ejector\s+pump|sewage\s+ejector|lift\s+station\s+pump)\b")) if "electrical" in positive_families else _fact(TriFact.UNKNOWN)),
        mechanical_work=_fact(TriFact.FALSE, mechanical_false) if mechanical_false and "mechanical" not in positive_families else (_fact(TriFact.TRUE, _evidence_spans(job, r"\b(?:mechanical|hvac|ventilation|diffuser|duct|rtu|rooftop|fume\s+hood|exhaust)\b")) if "mechanical" in positive_families else _fact(TriFact.UNKNOWN)),
        plumbing_work=_fact(TriFact.FALSE, plumbing_false) if plumbing_false and "plumbing" not in positive_families else (_fact(TriFact.TRUE, _evidence_spans(job, r"\b(?:plumbing|fixtures?|sinks?|drains?|water|sewer|gas\s+line)\b")) if "plumbing" in positive_families else _fact(TriFact.UNKNOWN)),
        building_work=_fact(TriFact.FALSE, building_false) if building_false and "building" not in positive_families else (_fact(TriFact.TRUE, _evidence_spans(job, r"\b(?:tenant\s+improvement|building|structural|foundation|framing|facade|window|garage\s+conversion)\b")) if positive_families & {"building", "building_ti"} else _fact(TriFact.UNKNOWN)),
    )


def scope_facts_from_contract(scope_contract: dict[str, Any] | None, job_type: str = "", city: str = "", state: str = "") -> ScopeFacts:
    return build_scope_facts(job_type, city, state, scope_contract=scope_contract if isinstance(scope_contract, dict) else None)


def _term_is_locally_negated(text: str, term_start: int) -> bool:
    prefix = text[max(0, term_start - 96):term_start]
    if re.search(r"\b(?:remove|remove old|remove existing|removed|demolish|demo|cap|cap existing|abandon)\b(?:\s+(?:old|existing|unused|prior))*[\s,;/()-]*$", prefix, flags=re.I):
        return True
    if _NEGATION_RE.search(prefix):
        return True
    suffix = text[term_start:term_start + 96]
    return bool(re.search(r"^[a-z0-9\s,;/()'\"-]{0,48}\b(?:not included|excluded|not in scope|outside(?: the)? scope|not part|not proposed)\b", suffix, flags=re.I))


def contains_unnegated_phrase(text: str, phrase: str) -> bool:
    phrase_lc = (phrase or "").lower().strip()
    if not phrase_lc:
        return False
    haystack = _norm(text)
    pattern = re.compile(r"(?<![a-z0-9])" + re.escape(phrase_lc) + r"(?![a-z0-9])", flags=re.I)
    for match in pattern.finditer(haystack):
        if not _term_is_locally_negated(haystack, match.start()):
            return True
    return False


def has_any_unnegated(text: str, phrases: tuple[str, ...]) -> bool:
    return any(contains_unnegated_phrase(text, phrase) for phrase in phrases)


def _has_panel_scope(job_text: str) -> bool:
    """Return True for actual panel/service work, not existing-capacity facts."""
    job = _norm(job_text)
    if has_any_unnegated(job, _PANEL_ACTION_TERMS):
        return True
    # Bare "existing 200A panel available" is context for a water-heater/HVAC
    # lookup, not an electrical service-change trigger. Require an action verb.
    return bool(re.search(r"\b(?:upgrade|replace|new|modify|change)\b.{0,40}\b(?:200\s*(?:amp|a)|400\s*(?:amp|a)|panel|service)\b", job, flags=re.I))


def _explicit_category(job_text: str, job_category: str | None) -> str:
    category = (job_category or "").strip().lower()
    has_commercial = has_any_unnegated(job_text, _COMMERCIAL_TERMS)
    has_strong_commercial = has_any_unnegated(job_text, _STRONG_COMMERCIAL_TERMS)
    has_residential = has_any_unnegated(job_text, _RESIDENTIAL_TERMS)
    if category == "commercial":
        return category
    if category == "residential":
        # Some public/client callers historically defaulted an omitted category
        # to "residential". Do not let that implicit default strip a clearly
        # commercial TI/restaurant/clinic/office answer downstream.
        if has_strong_commercial:
            return "commercial"
        return category
    if has_commercial:
        return "commercial"
    if has_residential or has_any_unnegated(job_text, _PANEL_TERMS + _HVAC_TERMS + _WATER_HEATER_TERMS + _ROOF_TERMS):
        return "residential"
    return "unknown"


def build_scope_contract(job_type: str, city: str = "", state: str = "", *, job_category: str | None = None, vertical: str | None = None) -> dict[str, Any]:
    """Return the canonical scope contract for a request.

    The contract is based only on request fields, not model/cache output.
    """
    job = _norm(job_type)
    category = _explicit_category(job, job_category)
    explicit_vertical = _norm(vertical or "").replace(" ", "_").replace("-", "_")

    has_panel = _has_panel_scope(job)
    has_solar = has_any_unnegated(job, _SOLAR_TERMS)
    has_hvac = has_any_unnegated(job, _HVAC_TERMS)
    has_hvac_specific = has_any_unnegated(job, _HVAC_SPECIFIC_TERMS)
    has_water_heater = has_any_unnegated(job, _WATER_HEATER_TERMS)
    has_roof = has_any_unnegated(job, _ROOF_TERMS)
    has_ev_charger = has_any_unnegated(job, _EV_CHARGER_TERMS)
    has_window = has_any_unnegated(job, _WINDOW_TERMS)
    has_fence = has_any_unnegated(job, _FENCE_TERMS)
    has_patio = has_any_unnegated(job, _PATIO_TERMS)
    has_foundation = has_any_unnegated(job, _FOUNDATION_TERMS)
    has_adu = has_any_unnegated(job, _ADU_TERMS)
    has_remodel = has_any_unnegated(job, _REMODEL_TERMS)

    commercial_vertical = None
    if has_any_unnegated(job, _TAG_TERMS["restaurant_ti"]):
        commercial_vertical = "restaurant_ti"
    elif has_any_unnegated(job, _TAG_TERMS["medical_clinic_ti"]):
        commercial_vertical = "medical_clinic_ti"
    elif has_any_unnegated(job, _TAG_TERMS["office_ti"]):
        commercial_vertical = "office_ti"
    elif has_any_unnegated(job, _TAG_TERMS["retail_ti"]):
        commercial_vertical = "retail_ti"
    elif category == "commercial" and has_any_unnegated(job, _TAG_TERMS["commercial_ti"]):
        commercial_vertical = "commercial_ti"

    if explicit_vertical:
        request_vertical = explicit_vertical
    elif has_solar:
        request_vertical = "solar_pv"
    elif category == "commercial" and commercial_vertical:
        request_vertical = commercial_vertical
    elif has_water_heater and not has_hvac_specific:
        # "Heat pump water heater" is a plumbing/water-heater vertical, not a
        # mini-split/HVAC vertical.  If a separate HVAC system is also explicitly
        # present, let the HVAC branch handle the combined-scope case.
        request_vertical = "water_heater"
    elif has_panel and category != "commercial":
        request_vertical = "panel_upgrade"
    elif has_ev_charger and category != "commercial":
        request_vertical = "ev_charger"
    elif has_hvac:
        request_vertical = "hvac_changeout"
    elif has_roof:
        request_vertical = "reroof"
    elif has_window:
        request_vertical = "window_replacement"
    elif has_fence:
        request_vertical = "fence"
    elif has_patio:
        request_vertical = "patio_cover"
    elif has_foundation:
        request_vertical = "foundation_repair"
    elif has_adu:
        request_vertical = "adu"
    elif has_remodel:
        request_vertical = "residential_remodel" if category != "commercial" else "commercial_ti"
    else:
        request_vertical = "generic"

    if category == "commercial":
        family = "commercial_ti" if (request_vertical.endswith("_ti") or request_vertical in {"commercial_ti", "restaurant_ti", "medical_clinic_ti", "office_ti", "retail_ti"}) else "commercial_other"
    elif request_vertical in {"panel_upgrade", "ev_charger", "hvac_changeout", "water_heater", "reroof", "window_replacement", "fence", "patio_cover", "foundation_repair", "solar_pv"}:
        family = "residential_single_trade"
    elif request_vertical == "adu":
        family = "residential_adu"
    elif request_vertical == "residential_remodel":
        family = "residential_remodel"
    else:
        family = "residential_other" if category == "residential" else "unknown"

    if has_any_unnegated(job, ("single-family", "single family", "single family home", "single-family home", "house", "home")):
        occupancy = "single_family"
    elif has_any_unnegated(job, ("multifamily", "multi-family", "apartment", "condo")):
        occupancy = "multifamily"
    elif category == "commercial":
        occupancy = "commercial"
    else:
        occupancy = "unknown"

    forbidden = set()
    if category == "commercial":
        forbidden.update({"residential_only", "residential_adu", "homeowner_only"})
        if request_vertical != "solar_pv" and not has_solar:
            forbidden.update({"solar_pv", "residential_solar"})
    elif category == "residential":
        forbidden.update({"commercial_ti", "restaurant_ti", "medical_clinic_ti", "office_ti", "retail_ti", "commercial_food", "commercial_health"})
        if request_vertical not in {"solar_pv", "panel_upgrade"} and not has_solar:
            forbidden.update({"solar_pv", "residential_solar", "utility_interconnection"})
        elif request_vertical == "panel_upgrade" and not has_solar:
            forbidden.update({"solar_pv", "residential_solar"})
        if request_vertical != "adu":
            forbidden.add("residential_adu")
        if request_vertical != "panel_upgrade":
            forbidden.discard("utility_interconnection")
    if request_vertical == "panel_upgrade":
        forbidden.update({"solar_pv", "residential_solar", "commercial_ti", "restaurant_ti", "medical_clinic_ti", "office_ti", "retail_ti"})

    allowed = set(_GENERAL_TAGS)
    allowed.add(category)
    allowed.add(family)
    allowed.add(request_vertical)
    if category == "commercial":
        allowed.update({"commercial_ti", "contractor_license", "accessibility", "code_adoption", "energy", "inspection", "timeline"})
        if request_vertical == "restaurant_ti":
            allowed.update({"restaurant_ti", "commercial_food", "fire_life_safety", "health"})
        if request_vertical == "medical_clinic_ti":
            allowed.update({"medical_clinic_ti", "health", "accessibility"})
        if request_vertical == "office_ti":
            allowed.update({"office_ti", "accessibility"})
        if request_vertical == "solar_pv":
            allowed.update({"solar_pv", "residential_solar", "utility_interconnection", "electrical_service"})
    elif category == "residential":
        allowed.update({"residential_only", "contractor_license", "code_adoption", "inspection", "timeline"})
        if request_vertical == "panel_upgrade":
            allowed.update({"panel_upgrade", "electrical_service", "utility_service"})
        if request_vertical == "ev_charger":
            allowed.update({"electrical_service", "utility_service"})
        if request_vertical == "solar_pv":
            allowed.update({"solar_pv", "residential_solar", "utility_interconnection"})
        if request_vertical == "adu":
            allowed.update({"residential_adu", "energy", "zoning"})

    allowed.difference_update(forbidden)
    return {
        "category": category,
        "family": family,
        "vertical": request_vertical,
        "occupancy_class": occupancy,
        "forbidden_scope_tags": sorted(forbidden),
        "allowed_scope_tags": sorted(allowed),
        "city": city or "",
        "state": (state or "").upper().strip(),
        "source": "request_entry",
    }


def scope_tag_text(text: str) -> set[str]:
    tags: set[str] = set()
    value = text or ""
    for tag, terms in _TAG_TERMS.items():
        if has_any_unnegated(value, terms):
            tags.add(tag)
    if not tags:
        tags.add("general")
    return tags


def note_scope_tags(note: dict[str, Any]) -> list[str]:
    text = " ".join(str(note.get(k) or "") for k in ("title", "note", "applies_to"))
    tags = scope_tag_text(text)
    lower = _norm(text)
    residential_only_signals = (
        "homeowner", "homeowners", "owner-builder", "owner builder", "home improvement", "home-improvement",
        "residential remodeling", "residential alteration", "residential alterations",
        "residential land or buildings", "residential home-improvement", "residential code",
        "building, residential, plumbing",
    )
    if any(signal in lower for signal in residential_only_signals) or re.search(r"\bresidential\b.{0,40}\bcodes?\b|\bcodes?\b.{0,40}\bresidential\b", lower):
        tags.add("residential_only")
    if "license" in lower or "contractor" in lower or "tdlr" in lower or "tsbpe" in lower:
        tags.add("contractor_license")
    if "energy code" in lower or "energy compliance" in lower or "energy conservation" in lower or "iecc" in lower or "title 24" in lower or "cf1r" in lower or "calgreen" in lower:
        tags.add("energy")
        # Title 24/CF1R/CALGreen notes may mention solar-ready/solar-mandate
        # context without being a Solar PV permit note. Keep them as energy/code
        # compliance unless they include actual PV/interconnection terms.
        if not any(term in lower for term in ("solar pv", "photovoltaic", "net metering", "interconnection", "battery", "ess")):
            tags.discard("solar_pv")
            tags.discard("residential_solar")
            tags.discard("utility_interconnection")
    if "building standards code" in lower or "code edition" in lower or "code adoption" in lower or "adopted" in lower or "effective date" in lower or "irc" in lower or "ibc" in lower or "imc" in lower or "ifgc" in lower:
        tags.add("code_adoption")
    if "fee" in lower or "$" in lower:
        tags.add("fee")
    if "municipal utility" in lower or "local utility" in lower or "ladwp" in lower or "pasadena water and power" in lower or "pwp" in lower:
        tags.add("local_utility")
        tags.add("utility_service")
        tags.discard("solar_pv")
        tags.discard("residential_solar")
        tags.discard("utility_interconnection")
    if "historic district" in lower or "historic preservation" in lower or "historic parcels" in lower:
        tags.add("general_state_local")
        tags.discard("solar_pv")
        tags.discard("residential_solar")
    if "accessibility" in lower or "ada" in lower or "553.502" in lower:
        tags.add("accessibility")
    if "inspection" in lower:
        tags.add("inspection")
    if "shot clock" in lower or "timeline" in lower or "business days" in lower:
        tags.add("timeline")
    return sorted(tags)


def note_allowed_for_contract(note: dict[str, Any], scope_contract: dict[str, Any] | None) -> bool:
    if not scope_contract:
        return True
    tags = set(note.get("scope_tags") or note_scope_tags(note))
    forbidden = set(scope_contract.get("forbidden_scope_tags") or [])
    if tags & forbidden:
        return False
    allowed = set(scope_contract.get("allowed_scope_tags") or [])
    scoped_tags = tags - _GENERAL_TAGS
    if scoped_tags and not scoped_tags.issubset(allowed):
        return False
    return bool(tags & allowed) or tags == {"general"}


def _firebreak_forbidden_terms(scope_contract: dict[str, Any]) -> tuple[str, ...]:
    tags = set(scope_contract.get("forbidden_scope_tags") or [])
    terms: list[str] = []
    for tag in tags:
        for term in _TAG_TERMS.get(tag, ()):
            # ADU/garage-conversion is a legitimate residential scope; do not let
            # the broad commercial-TI word "conversion" firebreak strip it.
            if tag == "commercial_ti" and scope_contract.get("vertical") == "adu" and term == "conversion":
                continue
            terms.append(term)
    if "commercial_ti" in tags:
        terms.extend(("tenant improvement", "tenant finish", "tenant buildout", "commercial building", "commercial alteration"))
    if "residential_solar" in tags or "solar_pv" in tags:
        terms.extend(("solar pv", "photovoltaic", "solarapp", "roof-mounted racking", "structural racking", "net metering", "utility interconnection"))
    if "residential_adu" in tags:
        terms.extend(("adu", "accessory dwelling", "jadu"))
    if "residential_only" in tags:
        terms.extend(("homeowner", "homeowners", "owner-builder", "owner builder", "owner-occupied", "home improvement", "home-improvement", "home improvement contractor", "home-improvement contractor"))
    return tuple(dict.fromkeys(t for t in terms if t))


def customer_text_has_forbidden_scope(value: Any, scope_contract: dict[str, Any]) -> bool:
    text = value if isinstance(value, str) else str(value)
    return has_any_unnegated(text, _firebreak_forbidden_terms(scope_contract))


def customer_text_mentions_forbidden_scope(value: Any, scope_contract: dict[str, Any]) -> bool:
    """Return True when customer-visible text mentions a forbidden scope at all.

    The final firebreak uses unnegated matching so source/user phrases like "no
    solar" do not misclassify the request. Customer-facing helper copy is stricter:
    even exclusionary wording such as "do not use homeowner ADU forms" is still a
    confusing residential/solar leak in a commercial TI answer and should be
    removed upstream.
    """
    text = _norm(value if isinstance(value, str) else str(value))
    for phrase in _firebreak_forbidden_terms(scope_contract):
        phrase_lc = _norm(phrase)
        if phrase_lc and re.search(r"(?<![a-z0-9])" + re.escape(phrase_lc) + r"(?![a-z0-9])", text, flags=re.I):
            return True
    return False


def sanitize_result_for_scope_contract(result: dict[str, Any], scope_contract: dict[str, Any], *, fail_on_removal_in_tests: bool = True) -> dict[str, Any]:
    """Final customer-visible tripwire for leaked scope text.

    Production removes individual leaked items and marks metadata. Pytest raises if
    anything had to be removed so regressions get fixed upstream.
    """
    if not isinstance(result, dict) or not scope_contract:
        return result
    forbidden_terms = _firebreak_forbidden_terms(scope_contract)
    if not forbidden_terms:
        result.setdefault("_scope_contract", scope_contract)
        return result

    removals: list[dict[str, str]] = []
    removed = object()

    def is_forbidden(value: Any) -> bool:
        if not isinstance(value, str):
            value = str(value)
        return has_any_unnegated(value, forbidden_terms)

    def clean(value: Any, path: str) -> Any:
        if isinstance(value, str):
            if is_forbidden(value):
                removals.append({"path": path, "kind": "forbidden_scope_text"})
                return removed
            return value
        if isinstance(value, list):
            out = []
            for idx, item in enumerate(value):
                cleaned = clean(item, f"{path}[{idx}]")
                if cleaned is not removed:
                    out.append(cleaned)
            return out
        if isinstance(value, dict):
            if any(isinstance(v, str) and is_forbidden(v) for v in value.values()):
                # Drop whole structured items when their classifier/title fields leak scope.
                title_fields = {"permit_type", "portal_selection", "title", "name", "summary", "reason", "required_if", "claim", "value"}
                if any(k in title_fields and isinstance(v, str) and is_forbidden(v) for k, v in value.items()):
                    removals.append({"path": path, "kind": "forbidden_scope_structured_item"})
                    return removed
            out = {}
            for key, item in value.items():
                if str(key).startswith("_"):
                    out[key] = item
                    continue
                cleaned = clean(item, f"{path}.{key}" if path else str(key))
                if cleaned is not removed:
                    out[key] = cleaned
            return out
        return value

    cleaned = clean(copy.deepcopy(result), "")
    if not isinstance(cleaned, dict):
        cleaned = {}
    cleaned["_scope_contract"] = scope_contract
    if removals:
        cleaned["_scope_firebreak_removed"] = removals
        if fail_on_removal_in_tests:
            raise AssertionError(f"Scope firebreak removed leaked customer-visible text; fix upstream: {removals[:5]}")
    return cleaned


def customer_text_has_server_error_signal(text: str) -> bool:
    """Detect real server-error text without treating legal/code sections as 5xx errors."""
    value = str(text or "").lower()
    if not value:
        return False
    if "server_error" in value or "server error" in value or "internal server error" in value:
        return True
    if "bad gateway" in value or "service unavailable" in value or "gateway timeout" in value:
        return True
    return bool(re.search(r"\b(?:http\s*)?(?:500|501|502|503|504)\b(?:\s+(?:error|bad gateway|service unavailable|gateway timeout))", value))
