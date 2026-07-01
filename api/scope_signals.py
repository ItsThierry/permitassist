"""Universal scope-signal architecture primitives for PermitAssist.

Pure, deterministic architecture layer:
request text -> ScopeSignal[] -> ProjectArchetype set -> union family floor
-> specificity-ranked primary. No I/O and no product resolver dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable


@dataclass(frozen=True)
class ScopeSignal:
    signal_id: str
    trade_implications: frozenset[str] = field(default_factory=frozenset)
    archetypes: frozenset[str] = field(default_factory=frozenset)
    family_floor: dict[str, str] = field(default_factory=dict)
    trigger_condition: dict[str, str] = field(default_factory=dict)
    primary_family: str = ""
    specificity: int = 0


# Single lexicon table. Phase 4 expansion is corpus-backed by the 20 confirmed
# live_customer_100 C/F contracts; no secondary keyword list is used elsewhere.
_SCOPE_SIGNAL_LEXICON: tuple[dict[str, object], ...] = (
    {
        "signal_id": "commercial_food_service_conversion",
        "any": (r"quick\s+service\s+restaurant", r"restaurant", r"coffee\s+shop", r"cafe", r"espresso\s+bar", r"bar\b", r"brewery", r"kitchen\s+equipment", r"warming\s+kitchen", r"hand\s+sink", r"grease", r"food[-\s]?prep"),
        "suppress_if_any": (r"(?:grading|drainage)[^.]{0,80}outdoor\s+patio|outdoor\s+patio[^.]{0,80}(?:grading|drainage)", r"(?:awning|storefront|projecting|illuminated|monument|cabinet|wall)\s+sign|sign\s+(?:awning|projecting|cabinet)"),
        "suppress_if_all": (r"finishes?\s+only|interior\s+refresh", r"no\s+(?:mep|mechanical|electrical|plumbing)", r"no\s+seating(?:\s+count)?\s+change", r"no\s+occupancy\s+change"),
        "trade_implications": ("building", "electrical", "mechanical", "plumbing", "health", "fire", "wastewater", "planning", "co"),
        "archetypes": ("COMMERCIAL_TENANT_IMPROVEMENT", "FOOD_SERVICE", "CHANGE_OF_OCCUPANCY"),
        "family_floor": {"building": "REQUIRED", "electrical": "REQUIRED", "mechanical": "REQUIRED", "plumbing": "REQUIRED", "health": "REQUIRED", "fire": "REQUIRED", "wastewater": "VERIFY", "planning": "VERIFY", "co": "VERIFY"},
        "trigger_condition": {"when": "food-service, bar/brewery, hand-sink, kitchen, grease/FOG, or cafe/coffee scope is present"},
        "primary_family": "building",
        "specificity": 96,
    },
    {
        "signal_id": "personal_care_health_ti",
        "any": (r"\bsalon\b", r"\bbarber\b", r"nail\s+salon", r"\bspa\b", r"\btattoo\b", r"personal\s+care", r"shampoo\s+bowls?"),
        "trade_implications": ("building", "plumbing", "mechanical", "electrical", "health", "planning", "co"),
        "archetypes": ("COMMERCIAL_TENANT_IMPROVEMENT", "PERSONAL_CARE_SERVICE"),
        "family_floor": {"building": "REQUIRED", "plumbing": "REQUIRED", "mechanical": "REQUIRED", "electrical": "REQUIRED", "health": "REQUIRED", "planning": "VERIFY", "co": "VERIFY"},
        "trigger_condition": {"when": "salon/barber/spa/tattoo/personal-care scope with wet stations, dryers, ventilation, or electrical work is present"},
        "primary_family": "plumbing",
        "specificity": 95,
    },
    {
        "signal_id": "daycare_childcare_ti",
        "any": (r"daycare", r"child\s*care", r"childcare", r"preschool", r"classrooms?", r"play\s*yard"),
        "trade_implications": ("building", "plumbing", "electrical", "mechanical", "health", "fire", "planning", "co"),
        "archetypes": ("COMMERCIAL_TENANT_IMPROVEMENT", "DAYCARE_CHILDCARE", "CHANGE_OF_OCCUPANCY"),
        "family_floor": {"building": "REQUIRED", "plumbing": "REQUIRED", "electrical": "REQUIRED", "mechanical": "REQUIRED", "health": "REQUIRED", "fire": "REQUIRED", "planning": "REQUIRED", "co": "VERIFY"},
        "trigger_condition": {"when": "daycare/childcare/preschool, classroom, toilet, warming kitchen, or play-yard scope is present"},
        "primary_family": "building",
        "specificity": 95,
    },
    {
        "signal_id": "solar_pv_storage",
        "any": (r"solar\s+panels?", r"solar\s+pv", r"photovoltaic", r"battery\s+backup", r"battery\s+storage"),
        "trade_implications": ("solar", "electrical"),
        "archetypes": ("SOLAR_PV_STORAGE", "ELECTRICAL_DISTRIBUTION"),
        "family_floor": {"solar": "REQUIRED", "electrical": "REQUIRED"},
        "trigger_condition": {"when": "solar photovoltaic panels, inverter, battery storage, or electrical interconnection work is included"},
        "primary_family": "solar",
        "specificity": 89,
    },
    {
        "signal_id": "sign_planning_electrical",
        "any": (r"illuminated\s+sign", r"exterior\s+sign", r"storefront\s+sign", r"wall\s+sign", r"monument\s+sign", r"projecting\s+sign", r"sign\s+projecting", r"awning\s+sign", r"sign\s+awning", r"blade\s+sign", r"cabinet\s+sign", r"sign\s+cabinet", r"signage"),
        "suppress_if_any": (r"\bno\s+(?:exterior\s+)?sign(?:age)?\b", r"\bno\s+sign\s+or\s+sign\s+cabinet\s+work\b"),
        "trade_implications": ("sign", "electrical", "planning"),
        "archetypes": ("SIGNAGE",),
        "family_floor": {"sign": "REQUIRED", "electrical": "REQUIRED", "planning": "VERIFY"},
        "trigger_condition": {"when": "sign/signage work is included; electrical is REQUIRED for illuminated/sign-lighting scope"},
        "primary_family": "sign",
        "specificity": 88,
    },
    {
        "signal_id": "whole_house_repipe",
        "any": (r"whole\s+house\s+repipe", r"repipe\s+from", r"galvanized\s+to\s+pex", r"pex\s+repipe"),
        "trade_implications": ("plumbing",),
        "archetypes": ("PLUMBING_DISTRIBUTION",),
        "family_floor": {"plumbing": "REQUIRED"},
        "trigger_condition": {"when": "water distribution piping is replaced or repiped"},
        "primary_family": "plumbing",
        "specificity": 84,
    },
    {
        "signal_id": "fuel_gas_connection",
        "any": (r"natural\s+gas\s+connection", r"new\s+gas\s+connection", r"extend\s+gas\s+line", r"gas\s+line\s+from", r"fuel\s+gas\s+piping"),
        "trade_implications": ("plumbing",),
        "archetypes": ("FUEL_GAS_PIPING",),
        "family_floor": {"plumbing": "REQUIRED"},
        "trigger_condition": {"when": "new, extended, or modified fuel-gas piping/connection is included"},
        "primary_family": "plumbing",
        "specificity": 86,
    },
    {
        "signal_id": "medical_clinic_conversion",
        "any": (r"outpatient\s+medical\s+clinic", r"medical\s+clinic", r"medical\s+office", r"dental\s+office", r"procedure\s+rooms?", r"convert[^.]{0,100}\boutpatient\s+clinic", r"exam\s+rooms?", r"medical\s+gas"),
        "trade_implications": ("building", "electrical", "mechanical", "plumbing", "fire", "health", "planning", "co"),
        "archetypes": ("COMMERCIAL_TENANT_IMPROVEMENT", "MEDICAL_CLINIC", "CHANGE_OF_OCCUPANCY"),
        "family_floor": {"building": "REQUIRED", "electrical": "REQUIRED", "mechanical": "REQUIRED", "plumbing": "REQUIRED", "fire": "REQUIRED", "health": "REQUIRED", "planning": "VERIFY", "co": "VERIFY"},
        "trigger_condition": {"when": "medical clinic conversion, exam room, sink, medical gas, or healthcare occupancy scope is present"},
        "primary_family": "building",
        "specificity": 95,
    },
    {
        "signal_id": "brewery_taproom_conversion",
        "any": (r"brewery\s+taproom", r"taproom", r"tasting\s+room", r"small\s+production"),
        "trade_implications": ("building", "electrical", "mechanical", "plumbing", "health", "fire", "planning", "co"),
        "archetypes": ("COMMERCIAL_TENANT_IMPROVEMENT", "FOOD_BEVERAGE", "CHANGE_OF_OCCUPANCY"),
        "family_floor": {"building": "REQUIRED", "electrical": "REQUIRED", "mechanical": "REQUIRED", "plumbing": "REQUIRED", "health": "CONDITIONAL", "fire": "REQUIRED", "planning": "VERIFY", "co": "VERIFY"},
        "trigger_condition": {"when": "brewery/taproom, tasting room, restroom, or production occupancy scope is present"},
        "primary_family": "building",
        "specificity": 94,
    },
    {
        "signal_id": "assembly_studio_conversion",
        "any": (r"yoga\s+studio", r"assembly\s+space", r"exit\s+lighting"),
        "trade_implications": ("building", "electrical", "plumbing", "fire", "planning", "co"),
        "archetypes": ("COMMERCIAL_TENANT_IMPROVEMENT", "ASSEMBLY_OCCUPANCY", "CHANGE_OF_OCCUPANCY"),
        "family_floor": {"building": "REQUIRED", "electrical": "REQUIRED", "plumbing": "REQUIRED", "fire": "REQUIRED", "planning": "VERIFY", "co": "VERIFY"},
        "trigger_condition": {"when": "assembly/studio conversion, showers, or exit-lighting scope is present"},
        "primary_family": "building",
        "specificity": 93,
    },
    {
        "signal_id": "commercial_laundry_room",
        "any": (r"shared\s+laundry", r"laundry\s+room", r"floor\s+drain"),
        "trade_implications": ("building", "plumbing", "electrical", "mechanical"),
        "archetypes": ("MULTIFAMILY_COMMON_AREA", "EQUIPMENT_SWAP_POWERED"),
        "family_floor": {"building": "REQUIRED", "plumbing": "REQUIRED", "electrical": "VERIFY", "mechanical": "VERIFY"},
        "trigger_condition": {"when": "laundry room, dryer, exhaust/make-up air, electrical, or floor drain scope is present"},
        "primary_family": "building",
        "specificity": 92,
    },
    {
        "signal_id": "new_dwelling_unit",
        "any": (r"\badu\b", r"accessory dwelling", r"new dwelling unit", r"backyard cottage", r"basement apartment", r"kitchenette", r"sleeping room"),
        "trade_implications": ("building", "electrical", "plumbing", "mechanical", "planning"),
        "archetypes": ("NEW_DWELLING_UNIT",),
        "family_floor": {"building": "REQUIRED", "electrical": "REQUIRED", "plumbing": "REQUIRED", "mechanical": "REQUIRED", "planning": "REQUIRED"},
        "trigger_condition": {"when": "new dwelling unit, kitchen/kitchenette, sleeping room, or apartment/cottage scope is present"},
        "primary_family": "building",
        "specificity": 90,
    },
    {
        "signal_id": "habitable_conversion",
        "any": (r"garage\s+to\s+adu", r"convert[^.]{0,80}\b(?:detached|attached)?\s*garage", r"finish\s+basement", r"bedroom\s+and\s+bathroom", r"bathroom\s+suite"),
        "trade_implications": ("building", "electrical", "plumbing", "mechanical"),
        "archetypes": ("HABITABLE_CONVERSION",),
        "family_floor": {"building": "REQUIRED", "electrical": "REQUIRED", "plumbing": "REQUIRED", "mechanical": "REQUIRED"},
        "trigger_condition": {"when": "existing non-habitable/accessory/basement/garage space is converted to habitable or dwelling use"},
        "primary_family": "building",
        "specificity": 85,
    },
    {
        "signal_id": "powered_hvac_equipment",
        "any": (r"heat\s+pump", r"mini[-\s]?split", r"condenser", r"hvac", r"air\s+handler", r"evaporative\s+cooler", r"condensate\s+pump", r"central\s+air", r"air\s+conditioning", r"ductwork", r"furnace"),
        "suppress_if_any": (r"heat\s+pump\s+water\s+heater", r"\bhpwh\b", r"hybrid\s+heat\s+pump\s+water\s+heater", r"heat\s+pump\s+dryer"),
        "trade_implications": ("mechanical", "electrical"),
        "archetypes": ("EQUIPMENT_SWAP_POWERED",),
        "family_floor": {"mechanical": "REQUIRED", "electrical": "VERIFY"},
        "trigger_condition": {"electrical": "new/reworked circuit, disconnect, receptacle, condenser, pump, or panel/subpanel work is included"},
        "primary_family": "mechanical",
        "specificity": 80,
    },
    {
        "signal_id": "emergency_generator_transfer_switch",
        "any": (r"emergency\s+generator", r"automatic\s+transfer\s+switch", r"\bats\b", r"generator"),
        "trade_implications": ("electrical", "mechanical"),
        "archetypes": ("BACKUP_POWER_EQUIPMENT",),
        "family_floor": {"electrical": "REQUIRED", "mechanical": "VERIFY"},
        "trigger_condition": {"when": "generator, transfer switch, fuel/exhaust, anchorage, or equipment connection scope is present"},
        "primary_family": "electrical",
        "specificity": 79,
    },
    {
        "signal_id": "plumbing_relocation",
        "any": (r"relocat(?:e|ing|ion)\s+(?:drain|water|supply|fixture|toilet|sink|tub|shower)", r"relocate\s+drain", r"move\s+(?:a\s+)?drain", r"new\s+(?:drain|water|supply|sewer)", r"sewer\s+line", r"sump\s+pump", r"discharge\s+line", r"walk[-\s]?in\s+shower"),
        "trade_implications": ("plumbing",),
        "archetypes": ("PLUMBING_ALTERATION",),
        "family_floor": {"plumbing": "REQUIRED", "building": "VERIFY"},
        "trigger_condition": {"when": "drain, supply, sewer, pump, or fixture location/rough-in is added or relocated"},
        "primary_family": "plumbing",
        "specificity": 78,
    },
    {
        "signal_id": "electrical_distribution",
        "any": (r"subpanel", r"panel", r"circuit", r"disconnect", r"receptacle", r"outlet", r"service\s+upgrade", r"exit\s+lighting"),
        "suppress_if_any": (r"no\s+(?:new\s+)?electrical", r"no\s+new\s+(?:electrical\s+)?circuit", r"no\s+panel", r"existing\s+circuit", r"sidewalk\s+panel", r"driveway\s+apron"),
        "trade_implications": ("electrical",),
        "archetypes": ("ELECTRICAL_DISTRIBUTION",),
        "family_floor": {"electrical": "REQUIRED"},
        "trigger_condition": {"when": "panel/subpanel/circuit/disconnect/receptacle/exit-lighting work is included"},
        "primary_family": "electrical",
        "specificity": 82,
    },
    {
        "signal_id": "grading_drainage_sitework",
        "any": (r"grading", r"drainage", r"site\s+work", r"stormwater", r"outdoor\s+patio", r"land\s+disturb"),
        "trade_implications": ("grading", "planning"),
        "archetypes": ("SITE_GRADING",),
        "family_floor": {"grading": "REQUIRED", "planning": "VERIFY"},
        "trigger_condition": {"when": "grading, drainage, stormwater, or sitework scope changes site conditions"},
        "primary_family": "grading",
        "specificity": 70,
    },
    {
        "signal_id": "fence_wall_planning",
        "any": (r"\bfence\b", r"\bfencing\b", r"privacy\s+fence", r"fence\s*/\s*wall"),
        "trade_implications": ("fence", "planning"),
        "archetypes": ("FENCE_WALL",),
        "family_floor": {"fence": "REQUIRED", "planning": "VERIFY"},
        "trigger_condition": {"when": "fence/wall height, placement, or setback scope is present"},
        "primary_family": "fence",
        "specificity": 88,
    },
    {
        "signal_id": "roofing_sheathing",
        "any": (r"reroof", r"re[-\s]?roof", r"roof\s+shingles?", r"cool\s+roof", r"sheathing"),
        "trade_implications": ("roofing", "building"),
        "archetypes": ("ROOFING",),
        "family_floor": {"roofing": "REQUIRED", "building": "REQUIRED"},
        "trigger_condition": {"when": "roof covering replacement, reroof, cool roof, or sheathing repair is included"},
        "primary_family": "roofing",
        "specificity": 87,
    },
    {
        "signal_id": "historic_exterior_overlay",
        "any": (r"historic\s+district", r"landmark", r"certificate\s+of\s+appropriateness"),
        "trade_implications": ("building", "planning", "historic"),
        "archetypes": ("HISTORIC_EXTERIOR_OVERLAY",),
        "family_floor": {"building": "VERIFY", "planning": "REQUIRED", "historic": "VERIFY"},
        "trigger_condition": {"when": "historic/landmark overlay is paired with exterior work such as windows, doors, signs, paint, or facade changes"},
        "primary_family": "building",
        "specificity": 83,
    },
    {
        "signal_id": "detached_shed_garage_patio",
        "any": (r"detached\s+\d+\s+square\s+foot\s+storage\s+shed", r"storage\s+shed", r"detached\s+(?:one[-\s]?car|two[-\s]?car|\d+[-\s]?car)?\s*garage", r"patio\s+cover", r"ceiling\s+fan", r"carport\s+to\s+enclosed\s+garage"),
        "suppress_if_any": (r"mini[-\s]?split[^.]{0,80}detached\s+garage|detached\s+garage[^.]{0,80}mini[-\s]?split",),
        "trade_implications": ("building", "planning", "electrical"),
        "archetypes": ("ACCESSORY_STRUCTURE",),
        "family_floor": {"building": "REQUIRED", "planning": "VERIFY", "electrical": "VERIFY"},
        "trigger_condition": {"when": "shed, detached garage, patio cover, or accessory structure scope is included; electrical is REQUIRED when fan/subpanel/lighting is present"},
        "primary_family": "building",
        "specificity": 86,
    },
    {
        "signal_id": "sewer_backflow_irrigation",
        "any": (r"sewer\s+(?:line|lateral)", r"backflow", r"irrigation\s+system", r"open\s+trench"),
        "trade_implications": ("plumbing", "grading"),
        "archetypes": ("PLUMBING_SITE_CONNECTION",),
        "family_floor": {"plumbing": "REQUIRED", "grading": "VERIFY"},
        "trigger_condition": {"when": "sewer lateral, irrigation, backflow, or trenching scope is present"},
        "primary_family": "plumbing",
        "specificity": 87,
    },
    {
        "signal_id": "gas_water_heater",
        "any": (r"gas\s+water\s+heater", r"water\s+heater.*\bgas\b", r"gas\s+appliance"),
        "trade_implications": ("plumbing", "mechanical"),
        "archetypes": ("GAS_APPLIANCE",),
        "family_floor": {"plumbing": "REQUIRED", "mechanical": "REQUIRED"},
        "trigger_condition": {"when": "gas water-heater or gas-appliance replacement/installation is included"},
        "primary_family": "plumbing",
        "specificity": 86,
    },
    {
        "signal_id": "structural_platform_steel",
        "any": (r"structural\s+steel", r"mezzanine", r"equipment\s+platform", r"platform", r"stairs\s+guardrails?", r"load[-\s]?bearing"),
        "suppress_if_any": (r"non[-\s]?load[-\s]?bearing", r"not\s+load[-\s]?bearing"),
        "trade_implications": ("structural", "building", "electrical"),
        "archetypes": ("STRUCTURAL_PLATFORM",),
        "family_floor": {"structural": "REQUIRED", "building": "REQUIRED", "electrical": "VERIFY"},
        "trigger_condition": {"when": "equipment platform, mezzanine, structural steel, stairs, guardrails, or load-bearing work is included"},
        "primary_family": "structural",
        "specificity": 88,
    },
    {
        "signal_id": "switchgear_service_utility",
        "any": (r"switchgear", r"service\s+equipment", r"service\s+upgrade", r"meter\s+main", r"meter/main", r"transformer"),
        "trade_implications": ("electrical", "utility"),
        "archetypes": ("ELECTRICAL_SERVICE_UTILITY",),
        "family_floor": {"electrical": "REQUIRED", "utility": "VERIFY"},
        "trigger_condition": {"when": "service equipment, switchgear, meter-main, transformer, or service upgrade scope is present"},
        "primary_family": "electrical",
        "specificity": 87,
    },
    {
        "signal_id": "front_stoop_steps_handrail",
        "any": (r"front\s+stoop", r"stoop", r"front\s+steps", r"handrail", r"porch"),
        "trade_implications": ("building",),
        "archetypes": ("EXTERIOR_EGRESS_STRUCTURE",),
        "family_floor": {"building": "REQUIRED"},
        "trigger_condition": {"when": "stoop, front steps, handrail, porch, or exterior egress component work is included"},
        "primary_family": "building",
        "specificity": 85,
    },
    {
        "signal_id": "commercial_ti_mep_baseline",
        "any": (r"white\s+box", r"tenant\s+(?:improvement|finish|buildout)", r"demising\s+wall", r"restroom", r"electrical\s+panel", r"commercial[^.]{0,80}ventilation"),
        "suppress_if_any": (r"led\s+lighting\s+retrofit[^.]{0,80}existing\s+circuits|existing\s+circuits[^.]{0,80}led\s+lighting\s+retrofit", r"no\s+tenant\s+improvement", r"no\s+building\s+alteration", r"no\s+wall\s+work"),
        "suppress_if_all": (r"finishes?\s+only|interior\s+refresh", r"no\s+(?:mep|mechanical|electrical|plumbing)", r"no\s+occupancy\s+change"),
        "trade_implications": ("building", "plumbing", "electrical", "mechanical"),
        "archetypes": ("COMMERCIAL_TENANT_IMPROVEMENT",),
        "family_floor": {"building": "REQUIRED", "plumbing": "REQUIRED", "electrical": "REQUIRED", "mechanical": "REQUIRED"},
        "trigger_condition": {"when": "commercial TI/white-box/demising-wall/restroom/lighting/ventilation scope is present"},
        "primary_family": "building",
        "specificity": 84,
    },
    {
        "signal_id": "auto_repair_fire_floor",
        "any": (r"auto\s+repair", r"vehicle\s+lifts?", r"compressor", r"sprinkler", r"fire\s+alarm"),
        "trade_implications": ("building", "plumbing", "mechanical", "electrical", "fire"),
        "archetypes": ("AUTO_REPAIR_TENANT_IMPROVEMENT",),
        "family_floor": {"building": "REQUIRED", "plumbing": "REQUIRED", "mechanical": "REQUIRED", "electrical": "REQUIRED", "fire": "REQUIRED"},
        "trigger_condition": {"when": "auto repair, vehicle lift, compressor, floor drain, sprinkler, alarm, or hazardous/life-safety scope is present"},
        "primary_family": "building",
        "specificity": 85,
    },
    {
        "signal_id": "battery_charging_fire_safety",
        "any": (r"battery\s+charging", r"spill\s+containment", r"charging\s+area"),
        "trade_implications": ("building", "electrical", "mechanical", "fire"),
        "archetypes": ("HAZARDOUS_OR_BATTERY_CHARGING_AREA",),
        "family_floor": {"building": "REQUIRED", "electrical": "REQUIRED", "mechanical": "REQUIRED", "fire": "REQUIRED"},
        "trigger_condition": {"when": "battery charging area, ventilation, electrical, spill containment, or hazardous/life-safety scope is present"},
        "primary_family": "mechanical",
        "specificity": 86,
    },
    {
        "signal_id": "de_minimis_fixture_swap",
        "any": (r"fixture\s+swap", r"replace\s+(?:bathtub|tub|toilet|sink|faucet|shower)", r"prehung\s+door", r"cabinets?", r"countertops?", r"drywall[^.]{0,50}\b(?:repair|replace)", r"replace[^.]{0,50}\bdrywall"),
        "suppress_if_any": (r"relocate\s+drain", r"move\s+(?:a\s+)?drain", r"new\s+(?:drain|supply|water|sewer)", r"rough[-\s]?in"),
        "trade_implications": (),
        "archetypes": ("DE_MINIMIS_FIXTURE_SWAP",),
        "family_floor": {},
        "trigger_condition": {"when": "like-for-like fixture/door swap only with no relocation, wall framing, or rough-in"},
        "primary_family": "",
        "specificity": 20,
    },
)

_STATUS_RANK = {"VERIFY": 1, "CONDITIONAL": 2, "VERIFY_OR_REQUIRED": 3, "REQUIRED": 4}


def _as_patterns(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(str(item) for item in value)
    if value:
        return (str(value),)
    return ()


def _as_str_dict(value: object) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return {}


def _matches_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def detect_scope_signals(request_text: str) -> list[ScopeSignal]:
    text = f" {request_text or ''} ".lower()
    signals: list[ScopeSignal] = []
    seen: set[str] = set()
    for entry in _SCOPE_SIGNAL_LEXICON:
        signal_id = str(entry["signal_id"])
        if signal_id in seen:
            continue
        if not _matches_any(text, _as_patterns(entry.get("any", ()))):
            continue
        if _matches_any(text, _as_patterns(entry.get("suppress_if_any", ()))):
            continue
        suppress_all = _as_patterns(entry.get("suppress_if_all", ()))
        if suppress_all and all(re.search(pattern, text, flags=re.I) for pattern in suppress_all):
            continue
        seen.add(signal_id)
        signals.append(ScopeSignal(
            signal_id=signal_id,
            trade_implications=frozenset(_as_patterns(entry.get("trade_implications", ()))),
            archetypes=frozenset(_as_patterns(entry.get("archetypes", ()))),
            family_floor=_as_str_dict(entry.get("family_floor", {})),
            trigger_condition=_as_str_dict(entry.get("trigger_condition", {})),
            primary_family=str(entry.get("primary_family", "")),
            specificity=int(str(entry.get("specificity", 0))),
        ))
    return sorted(signals, key=lambda signal: (-signal.specificity, signal.signal_id))


def derive_project_archetypes(signals: Iterable[ScopeSignal | dict]) -> set[str]:
    archetypes: set[str] = set()
    for signal in signals or []:
        values = signal.get("archetypes", ()) if isinstance(signal, dict) else signal.archetypes
        archetypes.update(str(value) for value in values)
    return archetypes


def derive_family_floor(signals: Iterable[ScopeSignal | dict], archetypes: Iterable[str] | None = None) -> dict[str, str]:
    floor: dict[str, str] = {}
    for signal in signals or []:
        values = signal.get("family_floor", {}) if isinstance(signal, dict) else signal.family_floor
        items = values.items() if isinstance(values, dict) else ((str(value), "VERIFY") for value in values)
        for family, status in items:
            fam = str(family).lower().strip()
            stat = str(status or "VERIFY").upper().strip()
            if _STATUS_RANK.get(stat, 1) > _STATUS_RANK.get(floor.get(fam, ""), 0):
                floor[fam] = stat
    return floor


def resolve_primary_family(signals: Iterable[ScopeSignal | dict], archetypes: Iterable[str] | None = None, floor: dict[str, str] | None = None, request_text: str = "") -> str:
    request_lc = (request_text or "").lower()
    floor_keys_pre = {str(key).lower().strip() for key in (floor or {}).keys()}
    sign_text = re.sub(r"\bno\s+(?:tenant\s+improvement|building\s+alteration|wall\s+work)\b", "", request_lc)
    if "sign" in floor_keys_pre and re.search(r"\b(?:signage|sign\s+cabinet|cabinet\s+sign|projecting\s+sign|awning\s+sign|storefront\s+sign|illuminated\s+sign|monument\s+sign|exterior\s+sign)\b", sign_text) and not re.search(r"\b(?:tenant\s+improvement|building\s+alteration|structural)\b", sign_text):
        return "sign"
    if re.search(r"\b(?:grading|drainage)\b", request_lc) and re.search(r"\boutdoor\s+patio\b", request_lc) and not re.search(r"\b(?:kitchen|hood|grease|sink|food[-\s]?prep|hand\s+sink)\b", request_lc):
        return "grading"
    ranked: list[tuple[int, int, str]] = []
    floor_keys = {str(key).lower().strip() for key in (floor or {}).keys()}
    family_tiebreak = {"mechanical": 50, "plumbing": 50, "electrical": 45, "grading": 45, "building": 20, "roofing": 10, "foundation": 5}
    for signal in signals or []:
        if isinstance(signal, dict):
            primary = str(signal.get("primary_family") or "").lower().strip()
            specificity = int(signal.get("specificity") or 0)
            implications = {str(v).lower().strip() for v in signal.get("trade_implications", ())}
        else:
            primary = str(signal.primary_family or "").lower().strip()
            specificity = int(signal.specificity or 0)
            implications = {str(v).lower().strip() for v in signal.trade_implications}
        if not primary:
            candidates = sorted(implications & floor_keys, key=lambda fam: (-family_tiebreak.get(fam, 0), fam))
            primary = candidates[0] if candidates else ""
        if primary:
            ranked.append((specificity, family_tiebreak.get(primary, 0), primary))
    if ranked:
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return ranked[0][2]
    if floor_keys:
        return sorted(floor_keys, key=lambda fam: (-family_tiebreak.get(fam, 0), fam))[0]
    return ""


__all__ = ["ScopeSignal", "detect_scope_signals", "derive_project_archetypes", "derive_family_floor", "resolve_primary_family"]
