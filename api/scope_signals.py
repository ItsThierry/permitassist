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
        "any": (r"quick\s+service\s+restaurant", r"cafe", r"kitchen\s+equipment", r"grease", r"food[-\s]?prep"),
        "suppress_if_any": (r"no\s+kitchen\s+hood", r"existing\s+type\s+i\s+hood", r"existing\s+kitchen\s+hood", r"\bnon[-\s]?restaurant\b", r"\bno\s+restaurant\b", r"\bno\s+food\s+service\b", r"\bno\s+commercial\s+kitchen\b"),
        "trade_implications": ("building", "electrical", "mechanical", "plumbing", "health", "fire", "wastewater", "planning", "co"),
        "archetypes": ("COMMERCIAL_TENANT_IMPROVEMENT", "FOOD_SERVICE", "CHANGE_OF_OCCUPANCY"),
        "family_floor": {"building": "REQUIRED", "electrical": "REQUIRED", "mechanical": "REQUIRED", "plumbing": "REQUIRED", "health": "REQUIRED", "fire": "REQUIRED", "wastewater": "REQUIRED", "planning": "VERIFY", "co": "VERIFY"},
        "trigger_condition": {"when": "food-service conversion, hood, grease/FOG, kitchen, or cafe scope is present"},
        "primary_family": "building",
        "specificity": 96,
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
        "any": (r"outpatient\s+medical\s+clinic", r"medical\s+clinic", r"convert[^.]{0,100}\boutpatient\s+clinic", r"exam[-\s]+rooms?", r"medical\s+gas"),
        "trade_implications": ("building", "electrical", "mechanical", "plumbing", "fire", "planning", "co"),
        "archetypes": ("COMMERCIAL_TENANT_IMPROVEMENT", "MEDICAL_CLINIC", "CHANGE_OF_OCCUPANCY"),
        "family_floor": {"building": "REQUIRED", "electrical": "REQUIRED", "mechanical": "REQUIRED", "plumbing": "REQUIRED", "fire": "REQUIRED", "planning": "VERIFY", "co": "VERIFY"},
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
        "family_floor": {"building": "REQUIRED", "electrical": "REQUIRED", "plumbing": "REQUIRED", "mechanical": "REQUIRED", "planning": "VERIFY"},
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
        "signal_id": "roof_covering_replacement",
        "any": (r"roof\s+(?:covering\s+)?replacement", r"replace[^.]{0,60}\broof", r"re[-\s]?roof", r"rer[o0]of"),
        "suppress_if_any": (r"\brooftop\s+(?:evaporative\s+)?(?:cooler|unit|equipment|hvac|rtu)\b",),
        "trade_implications": ("roofing",),
        "archetypes": ("ROOF_COVERING_REPLACEMENT",),
        "family_floor": {"roofing": "REQUIRED"},
        "trigger_condition": {"when": "roof covering, underlayment, sheathing, or reroof work is included"},
        "primary_family": "roofing",
        "specificity": 88,
    },
    {
        "signal_id": "commercial_tenant_improvement",
        "any": (r"tenant\s+improvement", r"commercial\s+interior\s+alteration", r"office\s+tenant\s+improvement"),
        "trade_implications": ("building",),
        "archetypes": ("COMMERCIAL_TENANT_IMPROVEMENT",),
        "family_floor": {"building": "REQUIRED"},
        "trigger_condition": {"when": "tenant-improvement or commercial interior-alteration construction is included"},
        "primary_family": "building",
        "specificity": 87,
    },
    {
        "signal_id": "commercial_sink_or_plumbing_fixture_work",
        "any": (r"break[-\s]?room\s+sink", r"hand\s+sinks?", r"commercial[^.]{0,100}\bsinks?"),
        "trade_implications": ("plumbing",),
        "archetypes": ("PLUMBING_ALTERATION",),
        "family_floor": {"plumbing": "REQUIRED"},
        "trigger_condition": {"when": "commercial sink, fixture, supply, or drain work is included"},
        "primary_family": "plumbing",
        "specificity": 83,
    },
    {
        "signal_id": "powered_hvac_equipment",
        "any": (r"heat\s+pump", r"mini[-\s]?split", r"condenser", r"hvac", r"air\s+handler", r"evaporative\s+cooler", r"condensate\s+pump", r"central\s+air", r"air\s+conditioning", r"ductwork", r"furnace", r"exhaust\s+fan"),
        "suppress_if_any": (r"heat\s+pump\s+water\s+heater", r"\bhpwh\b", r"hybrid\s+heat\s+pump\s+water\s+heater"),
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
        "any": (r"relocat(?:e|ing|ion)\s+(?:drain|water|supply|fixture|toilet|sink|tub|shower)", r"(?:drain|water|supply|fixture|toilet|sink|tub|shower)\s+relocation", r"relocate\s+drain", r"move\s+(?:a\s+)?drain", r"new\s+(?:drain|water|supply|sewer)", r"sewer\s+line", r"sump\s+pump", r"discharge\s+line", r"walk[-\s]?in\s+shower"),
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
        "suppress_if_any": (r"no\s+(?:new\s+)?electrical", r"no\s+new\s+(?:electrical\s+)?circuit", r"no\s+panel", r"existing\s+circuit"),
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
        "signal_id": "generic_electrical_scope",
        "any": (r"\belectrical\b", r"\bwiring\b", r"\bcircuits?\b", r"\boutlets?\b", r"\breceptacles?\b", r"\blighting\b", r"\blight\s+fixtures?\b"),
        "suppress_if_any": (r"\b(?:no|without|excluding)\s+(?:any\s+)?electrical(?:\s+work)?\b", r"\b(?:no|without|excluding)\s+(?:new\s+)?lighting(?:\s+work)?\b"),
        "trade_implications": ("electrical",),
        "archetypes": ("ELECTRICAL_DISTRIBUTION",),
        "family_floor": {"electrical": "VERIFY"},
        "trigger_condition": {"when": "electrical work is explicitly included but the exact local filing trigger is not yet sourced"},
        "primary_family": "electrical",
        "specificity": 35,
    },
    {
        "signal_id": "generic_plumbing_scope",
        "any": (r"\bplumbing\b", r"\bwater\s+lines?\b", r"\bdrain\s+lines?\b", r"\brough[-\s]?in\b"),
        "suppress_if_any": (r"\b(?:no|without|excluding)\s+(?:any\s+)?plumbing(?:\s+work)?\b",),
        "trade_implications": ("plumbing",),
        "archetypes": ("PLUMBING_DISTRIBUTION",),
        "family_floor": {"plumbing": "VERIFY"},
        "trigger_condition": {"when": "plumbing work is explicitly included but the exact local filing trigger is not yet sourced"},
        "primary_family": "plumbing",
        "specificity": 36,
    },
    {
        "signal_id": "water_heater_replacement",
        "any": (r"water\s+heater", r"tankless\s+heater"),
        "trade_implications": ("plumbing", "mechanical"),
        "archetypes": ("EQUIPMENT_SWAP_POWERED", "PLUMBING_ALTERATION"),
        "family_floor": {"plumbing": "VERIFY", "mechanical": "VERIFY"},
        "trigger_condition": {"when": "water-heater plumbing, venting, gas, or powered-equipment scope is included"},
        "primary_family": "plumbing",
        "specificity": 76,
    },
    {
        "signal_id": "sign_installation",
        "any": (r"wall\s+sign", r"storefront\s+sign", r"monument\s+sign", r"illuminated\s+sign", r"new\s+sign"),
        "trade_implications": ("sign",),
        "archetypes": ("SIGN_INSTALLATION",),
        "family_floor": {"sign": "VERIFY"},
        "trigger_condition": {"when": "new exterior, wall, monument, storefront, or illuminated sign scope is included"},
        "primary_family": "sign",
        "specificity": 74,
    },
    {
        "signal_id": "demolition_scope",
        "any": (r"interior\s+demolition", r"selective\s+demolition", r"demolish", r"demolition"),
        "trade_implications": ("demolition",),
        "archetypes": ("DEMOLITION",),
        "family_floor": {"demolition": "VERIFY"},
        "trigger_condition": {"when": "interior, selective, or complete demolition scope is included"},
        "primary_family": "demolition",
        "specificity": 73,
    },
    {
        "signal_id": "de_minimis_fixture_swap",
        "any": (r"fixture\s+swap", r"replace\s+(?:bathtub|tub|toilet|sink|faucet|shower)", r"prehung\s+door", r"same\s+size", r"cabinets?", r"countertops?", r"drywall[^.]{0,50}\b(?:repair|replace)", r"replace[^.]{0,50}\bdrywall"),
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


def _family_is_negated(text: str, family: str) -> bool:
    """Recognize a family excluded anywhere in the same short scope clause."""
    token = re.escape(str(family or "").lower().strip())
    if not token:
        return False
    patterns = (
        rf"\b(?:no|without|excluding)\b[^.;]{{0,70}}\b{token}\b",
        rf"\b{token}\b[^.;]{{0,35}}\b(?:not\s+included|excluded|unchanged)\b",
    )
    return _matches_any(text, patterns)


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
        family_floor = _as_str_dict(entry.get("family_floor", {}))
        family_floor = {
            family: status
            for family, status in family_floor.items()
            if not _family_is_negated(text, family)
        }
        if entry.get("family_floor") and not family_floor:
            continue
        seen.add(signal_id)
        signals.append(ScopeSignal(
            signal_id=signal_id,
            trade_implications=frozenset(_as_patterns(entry.get("trade_implications", ()))),
            archetypes=frozenset(_as_patterns(entry.get("archetypes", ()))),
            family_floor=family_floor,
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
            # Scope text is an advisory family lead, never permit-law authority.
            # It may preserve what to investigate but cannot manufacture a hard
            # REQUIRED decision without an exact sourced rule/cell downstream.
            if stat in {"REQUIRED", "VERIFY_OR_REQUIRED"}:
                stat = "VERIFY"
            if _STATUS_RANK.get(stat, 1) > _STATUS_RANK.get(floor.get(fam, ""), 0):
                floor[fam] = stat
    return floor


def resolve_primary_family(signals: Iterable[ScopeSignal | dict], archetypes: Iterable[str] | None = None, floor: dict[str, str] | None = None, request_text: str = "") -> str:
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
