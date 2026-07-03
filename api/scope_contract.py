"""Canonical request scope contract and customer-visible scope firebreaks.

The contract is computed from the original user request at lookup entry and is
safe to pass downstream. It exists to prevent later helpers from re-detecting
scope from already-contaminated model/cache text.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
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
    "historic": ("historic", "historic district", "historic overlay", "local historic district", "landmark", "french quarter", "vieux carre", "vieux carré", "vcc", "hdlc", "certificate of appropriateness", "coa", "architectural review", "bar", "board of architectural review"),
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
    if re.search(r"\b(?:change\s+of\s+(?:use|occupancy)|occupancy\s+change|convert(?:ing|ed)?\b|conversion\b|retail\s+to|office\s+to|warehouse\s+to)\b", job, re.I):
        facts.add("use_change")
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
    if v1.segment == "commercial" and re.search(r"\b(?:auto\s+repair|repair\s+shop|garage|vehicle\s+repair|lifts?)\b", job, re.I):
        facts.update({"fire_suppression", "planning_zoning"})
    if re.search(r"\b(?:solar|pv|photovoltaic)\b", job, re.I):
        facts.update({"electrical", "building"})
    if re.search(r"\b(?:adu|accessory dwelling|detached dwelling|garage conversion|kitchen\s+bath\s+and\s+utilities)\b", job, re.I):
        facts.add("new_dwelling_unit")
    if re.search(r"\b(?:utilities|kitchen|bath|bathroom|water|sewer|electrical|panel|gas)\b", job, re.I) and "new_dwelling_unit" in facts:
        facts.add("utilities_connected")
    if re.search(r"\b(?:sign|awning)\b", job, re.I):
        facts.add("sign")
    if re.search(r"\b(?:illuminated|lit|lighting|electric sign)\b", job, re.I) and not re.search(r"\b(?:non[- ]?electric|no\s+electrical|no\s+illumination|not\s+illuminated)\b", job, re.I):
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
    if v1.segment == "commercial" and re.search(r"\bfloor\s+drains?\b", job, re.I):
        floors["plumbing"] = "commercial TI with floor drains requires plumbing floor"
    if v1.segment == "commercial" and re.search(r"\b(?:warehouse\s+to|assembly|pickleball|occupant\s+load)\b", job, re.I) and "use_change" in positives:
        floors["fire_suppression"] = "assembly/change-of-use occupant-load increase requires fire/life-safety floor"
        floors["planning_zoning"] = "commercial change-of-use requires zoning/use clearance floor"
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
        if fail_on_removal_in_tests and os.environ.get("PYTEST_CURRENT_TEST"):
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
