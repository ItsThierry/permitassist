from __future__ import annotations

"""Universal filing-packet reconciliation for PermitAssist customer rows.

Contract enforced here:
    scope signal -> required filing family -> AHJ/apply routing -> structured row
    -> advisory/structured reconciliation gate.

This module is deterministic and jurisdiction-agnostic. Jurisdiction names and
apply provenance are metadata only; a missing/stale exact apply URL must never
hide a triggered required filing row.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import copy
import re
from typing import Any

FILING_PACKET_RECONCILER_VERSION = "filing_packet_reconciler_v1_20260626"
CACHE_SCHEMA_VERSION = "permit_cache_v4_filing_packet_reconciler_20260626"


@dataclass(frozen=True)
class FilingFamily:
    family_id: str
    label: str
    row_category: str
    default_ahj_type: str
    aliases: tuple[str, ...]
    advisory_terms: tuple[str, ...]


@dataclass(frozen=True)
class FilingSignal:
    signal_id: str
    filing_families: tuple[str, ...]
    evidence_text: str
    source: str = "scope"
    strength: str = "strong"


FILING_FAMILIES: dict[str, FilingFamily] = {
    "building_adu": FilingFamily(
        "building_adu",
        "ADU / Garage Conversion Building Permit",
        "construction_permit",
        "city_building_department",
        ("adu", "accessory dwelling", "garage conversion", "jadu"),
        ("adu permit", "garage conversion permit", "accessory dwelling"),
    ),
    "building_ti": FilingFamily(
        "building_ti",
        "Commercial Building / Tenant Improvement Permit",
        "construction_permit",
        "city_building_department",
        ("commercial building", "tenant improvement", "interior alteration", "finish-out", "fit-out", "change of use", "change of occupancy"),
        ("building permit", "tenant improvement", "commercial alteration", "finish-out", "fit-out"),
    ),
    "electrical": FilingFamily(
        "electrical",
        "Electrical Permit",
        "construction_permit",
        "city_building_department",
        ("electrical", "electric", "panel", "subpanel", "service upgrade", "lighting", "circuit", "switchgear"),
        ("electrical permit", "subpanel", "service upgrade", "panel upgrade", "electrical inspection"),
    ),
    "plumbing": FilingFamily(
        "plumbing",
        "Plumbing Permit",
        "construction_permit",
        "city_building_department",
        ("plumbing", "fixture", "sink", "restroom", "toilet", "sewer", "drain", "medical gas", "floor drain"),
        ("plumbing permit", "fixture", "floor drain", "medical gas", "sewer"),
    ),
    "mechanical": FilingFamily(
        "mechanical",
        "Mechanical Permit",
        "construction_permit",
        "city_building_department",
        ("mechanical", "hvac", "rtu", "makeup air", "make-up air", "exhaust", "hood", "dryer exhaust", "ventilation"),
        ("mechanical permit", "type i hood", "hvac", "makeup air", "exhaust", "ventilation"),
    ),
    "fire_suppression": FilingFamily(
        "fire_suppression",
        "Fire Suppression / Fire Prevention Review",
        "construction_permit",
        "fire_department",
        ("fire suppression", "ansul", "sprinkler", "fire alarm", "fire inspection", "fire prevention", "hood suppression"),
        ("fire suppression", "ansul", "fire sprinkler", "fire alarm", "fire inspection", "fire prevention"),
    ),
    "health_food_establishment": FilingFamily(
        "health_food_establishment",
        "Food Establishment Health Plan Review / Permit",
        "opening_approval",
        "county_health_department",
        ("health department", "food establishment", "food service", "commercial kitchen", "restaurant", "taqueria", "cafe", "walk-in cooler"),
        ("food establishment", "health department", "health plan review", "food permit", "commercial kitchen"),
    ),
    "liquor_license": FilingFamily(
        "liquor_license",
        "Liquor License / Local Alcohol Routing",
        "opening_approval",
        "state_or_local_licensing",
        ("liquor", "alcohol", "bar", "beer", "wine", "cocktail", "tavern"),
        ("liquor license", "alcohol license", "bar license", "local governing body"),
    ),
    "wastewater_pretreatment_fog": FilingFamily(
        "wastewater_pretreatment_fog",
        "Wastewater / FOG / Pretreatment Approval",
        "opening_approval",
        "sewer_or_water_authority",
        ("grease interceptor", "grease trap", "fog", "f.o.g", "wastewater", "pretreatment", "industrial waste", "floor drain"),
        ("grease interceptor", "fog", "wastewater", "pretreatment", "industrial waste"),
    ),
    "co_change_of_occupancy": FilingFamily(
        "co_change_of_occupancy",
        "Certificate of Occupancy / Change-of-Occupancy Approval",
        "opening_approval",
        "city_building_department",
        ("certificate of occupancy", " co ", "new co", "change of occupancy", "change-of-occupancy", "change of use", "retail to restaurant", "b to a-2", "b/m to a-2"),
        ("certificate of occupancy", "change of occupancy", "change of use", "new co"),
    ),
    "planning_zoning": FilingFamily(
        "planning_zoning",
        "Planning / Zoning Use Clearance",
        "conditional_review",
        "planning_department",
        ("planning", "zoning", "land use", "use compatibility", "zoning clearance", "conditional use", "special use"),
        ("planning", "zoning", "land use", "use clearance", "use compatibility"),
    ),
    "historic_review": FilingFamily(
        "historic_review",
        "Historic Preservation Review",
        "conditional_review",
        "planning_department",
        ("historic", "landmark", "preservation district", "historic district"),
        ("historic review", "historic preservation", "landmark"),
    ),
}

FILING_FAMILY_ORDER = tuple(FILING_FAMILIES.keys())

# Metadata-only routing registry. Entries intentionally avoid exact portal URLs
# unless the source path is explicitly verified and still inside TTL.
APPLY_PROVENANCE_REGISTRY: dict[tuple[str, str, str], dict[str, Any]] = {
    ("phoenix", "AZ", "building_ti"): {
        "ahj_name": "City of Phoenix Planning & Development Department",
        "ahj_type": "city_building_department",
        "department_url": "https://www.phoenix.gov/pdd",
        "source_url": "https://www.phoenix.gov/pdd",
        "verified_date": "2026-06-26",
        "ttl_days": 30,
        "status": "needs_verification",
    },
    ("phoenix", "AZ", "electrical"): {
        "ahj_name": "City of Phoenix Planning & Development Department",
        "ahj_type": "city_building_department",
        "department_url": "https://www.phoenix.gov/pdd",
        "source_url": "https://www.phoenix.gov/pdd",
        "verified_date": "2026-06-26",
        "ttl_days": 30,
        "status": "needs_verification",
    },
    ("phoenix", "AZ", "plumbing"): {
        "ahj_name": "City of Phoenix Planning & Development Department",
        "ahj_type": "city_building_department",
        "department_url": "https://www.phoenix.gov/pdd",
        "source_url": "https://www.phoenix.gov/pdd",
        "verified_date": "2026-06-26",
        "ttl_days": 30,
        "status": "needs_verification",
    },
    ("phoenix", "AZ", "mechanical"): {
        "ahj_name": "City of Phoenix Planning & Development Department",
        "ahj_type": "city_building_department",
        "department_url": "https://www.phoenix.gov/pdd",
        "source_url": "https://www.phoenix.gov/pdd",
        "verified_date": "2026-06-26",
        "ttl_days": 30,
        "status": "needs_verification",
    },
    ("phoenix", "AZ", "fire_suppression"): {
        "ahj_name": "Phoenix Fire Department / Fire Prevention",
        "ahj_type": "fire_department",
        "department_url": "https://www.phoenix.gov/fire",
        "source_url": "https://www.phoenix.gov/fire",
        "verified_date": "2026-06-26",
        "ttl_days": 30,
        "status": "needs_verification",
    },
    ("phoenix", "AZ", "health_food_establishment"): {
        "ahj_name": "Maricopa County Environmental Services",
        "ahj_type": "county_health_department",
        "department_url": "https://www.maricopa.gov/EnvSvc",
        "source_url": "https://www.maricopa.gov/EnvSvc",
        "verified_date": "2026-06-26",
        "ttl_days": 30,
        "status": "needs_verification",
    },
    ("phoenix", "AZ", "liquor_license"): {
        "ahj_name": "Arizona Department of Liquor Licenses and Control + City local governing-body routing",
        "ahj_type": "state_or_local_licensing",
        "department_url": "https://www.azliquor.gov/",
        "source_url": "https://www.azliquor.gov/",
        "verified_date": "2026-06-26",
        "ttl_days": 30,
        "status": "needs_verification",
    },
    ("phoenix", "AZ", "wastewater_pretreatment_fog"): {
        "ahj_name": "City of Phoenix Water Services / Wastewater Pretreatment",
        "ahj_type": "sewer_or_water_authority",
        "department_url": "https://www.phoenix.gov/waterservices",
        "source_url": "https://www.phoenix.gov/waterservices",
        "verified_date": "2026-06-26",
        "ttl_days": 30,
        "status": "needs_verification",
    },
    ("phoenix", "AZ", "co_change_of_occupancy"): {
        "ahj_name": "City of Phoenix Planning & Development Department",
        "ahj_type": "city_building_department",
        "department_url": "https://www.phoenix.gov/pdd",
        "source_url": "https://www.phoenix.gov/pdd",
        "verified_date": "2026-06-26",
        "ttl_days": 30,
        "status": "needs_verification",
    },
    ("phoenix", "AZ", "planning_zoning"): {
        "ahj_name": "City of Phoenix Planning & Development Department / Planning & Zoning",
        "ahj_type": "planning_department",
        "department_url": "https://www.phoenix.gov/pdd",
        "source_url": "https://www.phoenix.gov/pdd",
        "verified_date": "2026-06-26",
        "ttl_days": 30,
        "status": "needs_verification",
    },
    ("san francisco", "CA", "building_adu"): {
        "ahj_name": "San Francisco Department of Building Inspection",
        "ahj_type": "city_building_department",
        "department_url": "https://www.sf.gov/departments/department-building-inspection",
        "source_url": "https://www.sf.gov/departments/department-building-inspection",
        "verified_date": "2026-06-26",
        "ttl_days": 30,
        "status": "needs_verification",
    },
    ("san francisco", "CA", "electrical"): {
        "ahj_name": "San Francisco Department of Building Inspection",
        "ahj_type": "city_building_department",
        "department_url": "https://www.sf.gov/departments/department-building-inspection",
        "source_url": "https://www.sf.gov/departments/department-building-inspection",
        "verified_date": "2026-06-26",
        "ttl_days": 30,
        "status": "needs_verification",
    },
}

BANNED_TEXT_PATTERNS = (
    re.compile(r"https?://aca-prod\.accela\.com/PHOENIX\b", re.I),
    re.compile(r"\$\s*558\b[^.;\n]*(?:combined|phoenix|mechanical|electrical|building|hvac)[^.;\n]*", re.I),
)
BANNED_PHOENIX_APPLY_HOST_RE = re.compile(r"aca-prod\.accela\.com/phoenix", re.I)

_NEGATION_RE = re.compile(
    r"(?:\bno\b|\bwithout\b|\bnot\b|\bnon[-\s]*(?:restaurant|food|commercial\s+kitchen)\b|\bnone of\b|\bexcludes?\b|\bexcluding\b|\bdoes not include\b|\bdoesn't include\b)"
    r"(?:\s+(?:new|any|commercial|type\s*i|type\s*1|and|or))*"
    r"(?:[\s,;/()-]+[a-z0-9]+){0,5}[\s,;/()-]*$",
    re.I,
)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _city_key(city: str) -> str:
    return _norm(city).replace("city of ", "")


def _state_key(state: str) -> str:
    return str(state or "").strip().upper()


def _term_is_negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 120):start]
    if re.search(r"\bnot\s+just\s*$", prefix, flags=re.I):
        return False
    if re.search(r"\bnon[-\s]*$", prefix, flags=re.I):
        return True
    if re.search(r"\b(?:instead\s+of\s+splitting|wrong\s+permit\s+workflow|force\s+a\s+separate|trying\s+to\s+force\s+a\s+separate)\b", prefix, flags=re.I):
        return True
    return bool(_NEGATION_RE.search(prefix))


def _contains(text: str, phrase: str) -> bool:
    phrase = _norm(phrase)
    if not phrase:
        return False
    hay = _norm(text)
    if phrase.startswith(" ") or phrase.endswith(" "):
        pattern = re.compile(re.escape(phrase.strip()), re.I)
    else:
        pattern = re.compile(r"(?<![a-z0-9])" + re.escape(phrase).replace(r"\ ", r"[\s/-]+") + r"(?![a-z0-9])", re.I)
    for match in pattern.finditer(hay):
        if not _term_is_negated(hay, match.start()):
            return True
    return False


def _contains_any(text: str, terms: tuple[str, ...] | list[str]) -> bool:
    return any(_contains(text, term) for term in terms)


def _flatten_public_text(value: Any, *, include_private: bool = False) -> str:
    parts: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if not include_private and str(key).startswith("_"):
                continue
            parts.append(_flatten_public_text(child, include_private=include_private))
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            parts.append(_flatten_public_text(child, include_private=include_private))
    elif value is not None:
        parts.append(str(value))
    return " ".join(p for p in parts if p)


def _append_signal(signals: list[FilingSignal], signal_id: str, families: tuple[str, ...], evidence: str, source: str = "scope", strength: str = "strong") -> None:
    if not any(s.signal_id == signal_id and s.filing_families == families for s in signals):
        signals.append(FilingSignal(signal_id, families, evidence[:240], source, strength))


def detect_filing_scope_signals(job_type: str, result: dict[str, Any] | None = None, city: str = "", state: str = "") -> list[FilingSignal]:
    """Extract universal filing signals from request scope + advisory layers."""
    result = result if isinstance(result, dict) else {}
    advisory_fields = {
        key: result.get(key)
        for key in (
            "checklist",
            "what_to_bring",
            "pro_tips",
            "common_mistakes",
            "watch_out",
            "inspection_notes",
            "inspections",
            "companion_permits",
            "related_permits",
            "hidden_triggers",
            "procedural_gates",
            "_procedural_gates",
            "requirements",
        )
    }
    scope_text = _norm(job_type)
    advisory_text = _norm(_flatten_public_text(advisory_fields, include_private=True))
    all_text = f"{scope_text} {advisory_text}"
    signals: list[FilingSignal] = []

    is_commercial = _contains_any(scope_text, ("commercial", "tenant improvement", " ti", "restaurant", "retail", "office", "clinic", "salon", "daycare", "laundromat", "finish-out", "fit-out", "bar"))
    is_adu = _contains_any(scope_text, ("adu", "accessory dwelling", "garage conversion", "jadu", "in-law suite", "granny flat")) and not is_commercial
    if is_adu:
        _append_signal(signals, "adu_or_garage_conversion", ("building_adu",), "ADU / garage conversion scope")
    if is_commercial or _contains_any(scope_text, ("change of occupancy", "change of use", "certificate of occupancy")):
        _append_signal(signals, "commercial_ti_or_change_of_use", ("building_ti",), "Commercial TI/change-of-use scope")

    if _contains_any(all_text, ("subpanel", "sub-panel", "service upgrade", "service change", "new electrical service", "panel upgrade", "meter main", "switchgear", "200 amp", "200a", "400 amp", "400a")):
        _append_signal(signals, "new_subpanel_or_service_upgrade", ("electrical",), "Panel/subpanel/service-upgrade signal")
    elif _contains_any(scope_text, ("electrical", "electric", "lighting", "branch circuit", "circuit", "wiring", "receptacle", "low voltage", "data cabling")) or _contains_any(advisory_text, ("electrical permit", "electrical inspection")):
        _append_signal(signals, "electrical_work", ("electrical",), "Electrical/lighting/circuit signal")

    if _contains_any(all_text, ("plumbing", "restroom", "restrooms", "toilet", "toilets", "sink", "sinks", "lavatory", "lavatories", "fixture", "fixtures", "floor drain", "floor drains", "water line", "sewer", "dwv", "medical gas", "shampoo sink", "shampoo sinks", "exam sink", "exam sinks")):
        _append_signal(signals, "plumbing_fixtures", ("plumbing",), "Plumbing/fixture/sink/restroom signal")

    if _contains_any(all_text, ("commercial kitchen", "food service", "food establishment", "food prep", "food preparation", "restaurant", "taqueria", "cafe", "bar kitchen", "prep kitchen", "walk-in cooler", "dishwasher", "3-compartment", "three-compartment")):
        _append_signal(signals, "commercial_kitchen_food_service", ("health_food_establishment", "plumbing"), "Commercial kitchen/food service signal")

    hood_terms = ("type i hood", "type 1 hood", "ansul", "wet chemical", "hood suppression", "commercial hood", "grease duct", "makeup air", "make-up air")
    if _contains_any(all_text, hood_terms) or ("restaurant" in all_text and _contains(all_text, "hood")):
        _append_signal(signals, "type_i_hood_fire_suppression", ("mechanical", "fire_suppression"), "Type I hood / ANSUL / suppression signal")

    if _contains_any(scope_text, ("hvac", "mechanical", "rtu", "rooftop unit", "makeup air", "make-up air", "mau", "exhaust", "ventilation", "dryer exhaust", "gas dryer", "medical gas")) or _contains_any(advisory_text, ("mechanical permit", "mechanical inspection")):
        _append_signal(signals, "mechanical_hvac_or_exhaust", ("mechanical",), "Mechanical/HVAC/exhaust signal")

    has_process_fog = _contains_any(all_text, ("grease interceptor", "grease trap", "f.o.g", "wastewater", "pretreatment", "industrial waste", "laundromat discharge")) or (_contains(all_text, "fog") and not _contains_any(scope_text, ("stage fog", "fog machine", "special effects fog")))
    if has_process_fog:
        _append_signal(signals, "grease_fog_wastewater", ("plumbing", "wastewater_pretreatment_fog"), "Grease/FOG/wastewater signal")

    if _contains_any(all_text, ("alcohol", "liquor", "bar", "beer", "wine", "cocktail", "tavern")):
        _append_signal(signals, "alcohol_service", ("liquor_license",), "Alcohol/bar service signal")

    if _contains_any(all_text, ("change of occupancy", "change-of-occupancy", "change of use", "change in use", "retail to restaurant", "retail into restaurant", "b to a-2", "b/m to a-2", "a-2", "certificate of occupancy", "new co", " co ", "new classroom occupancy")):
        _append_signal(signals, "occupancy_change_or_new_co", ("co_change_of_occupancy", "planning_zoning", "fire_suppression"), "Change-of-use/CO/fire-life-safety signal", strength="strong")

    if _contains_any(all_text, ("zoning", "land use", "use compatibility", "zoning clearance", "planning review", "planning approval", "conditional use", "special use", "daycare", "salon")):
        _append_signal(signals, "planning_zoning_clearance", ("planning_zoning",), "Planning/zoning/use-clearance signal")

    if _contains_any(all_text, ("fire inspection", "fire prevention", "fire alarm", "sprinkler", "fire sprinkler", "daycare", "salon chemical", "chemical storage")):
        _append_signal(signals, "fire_life_safety_review", ("fire_suppression",), "Fire/life-safety review signal")

    if _contains_any(all_text, ("historic", "landmark", "historic district", "preservation")):
        _append_signal(signals, "historic_or_planning_overlay", ("historic_review", "planning_zoning"), "Historic/planning overlay signal", strength="conditional")

    return signals


def _family_from_existing_row(row: dict[str, Any], target_families: set[str]) -> str:
    explicit = str(row.get("filing_family") or "").strip()
    if explicit in FILING_FAMILIES:
        return explicit
    text = _norm(" ".join(str(row.get(k) or "") for k in ("permit_type", "approval_type", "portal_selection", "kind", "name", "notes", "reason")))
    if not text:
        return ""
    # Specific families before broad building/planning aliases.
    ordered = (
        "health_food_establishment",
        "wastewater_pretreatment_fog",
        "liquor_license",
        "co_change_of_occupancy",
        "fire_suppression",
        "electrical",
        "mechanical",
        "plumbing",
        "historic_review",
        "planning_zoning",
        "building_adu",
        "building_ti",
    )
    for family_id in ordered:
        family = FILING_FAMILIES[family_id]
        if _contains_any(text, family.aliases + family.advisory_terms):
            if family_id == "building_ti" and "building_adu" in target_families and "commercial" not in text and "tenant" not in text:
                continue
            return family_id
    if "building" in text or "permit" in text:
        if "building_adu" in target_families:
            return "building_adu"
        if "building_ti" in target_families:
            return "building_ti"
    return ""


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except Exception:
        try:
            return date.fromisoformat(str(value)[:10])
        except Exception:
            return None


def _provenance_for(family_id: str, city: str, state: str, as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or datetime.now(timezone.utc).date()
    entry = APPLY_PROVENANCE_REGISTRY.get((_city_key(city), _state_key(state), family_id), {})
    family = FILING_FAMILIES[family_id]
    if not entry:
        entry = {
            "ahj_name": f"{city} permit office".strip() or "Local permit office",
            "ahj_type": family.default_ahj_type,
            "department_url": "",
            "source_url": "",
            "verified_date": "",
            "ttl_days": 0,
            "status": "source_needed",
        }
    status = str(entry.get("status") or "source_needed")
    verified_date = _parse_date(entry.get("verified_date"))
    ttl_days = int(entry.get("ttl_days") or 0)
    if status == "verified" and verified_date and ttl_days > 0 and verified_date + timedelta(days=ttl_days) < as_of:
        status = "needs_reverification"
    apply_url = str(entry.get("apply_url") or "").strip() if status == "verified" else ""
    if apply_url and BANNED_PHOENIX_APPLY_HOST_RE.search(apply_url):
        apply_url = ""
        status = "needs_verification"
    if apply_url:
        apply_url_status = "verified" if status == "verified" else status
    elif status == "needs_reverification":
        apply_url_status = "needs_reverification"
    else:
        apply_url_status = "needs_verification"
    return {
        "ahj_name": entry.get("ahj_name") or f"{city} permit office".strip() or "Local permit office",
        "ahj_type": entry.get("ahj_type") or family.default_ahj_type,
        "application_authority_name": entry.get("application_authority_name") or entry.get("ahj_name") or f"{city} permit office".strip() or "Local permit office",
        "apply_url": apply_url,
        "department_url": entry.get("department_url") or "",
        "apply_url_status": apply_url_status,
        "source_status": status,
        "source_url": entry.get("source_url") or "",
        "verified_date": entry.get("verified_date") or "",
        "ttl_days": ttl_days,
    }


def _strip_banned_string(value: str, *, city: str, state: str) -> str:
    text = str(value)
    if _city_key(city) == "phoenix" and _state_key(state) == "AZ":
        for pattern in BANNED_TEXT_PATTERNS:
            text = pattern.sub("", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def strip_banned_filing_packet_artifacts(value: Any, city: str, state: str) -> Any:
    """Remove known stale Phoenix portal/fee anchors from runtime output."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, child in value.items():
            if key in {"apply_url", "online_application_url", "portal_url"} and isinstance(child, str) and BANNED_PHOENIX_APPLY_HOST_RE.search(child):
                out[key] = None
                out.setdefault("apply_url_status", "needs_verification")
                continue
            cleaned = strip_banned_filing_packet_artifacts(child, city, state)
            if key in {"url", "source_url"} and isinstance(cleaned, str) and BANNED_PHOENIX_APPLY_HOST_RE.search(cleaned):
                continue
            out[key] = cleaned
        return out
    if isinstance(value, list):
        cleaned_list = []
        for item in value:
            cleaned = strip_banned_filing_packet_artifacts(item, city, state)
            if isinstance(cleaned, str) and BANNED_PHOENIX_APPLY_HOST_RE.search(cleaned):
                continue
            if isinstance(cleaned, dict) and BANNED_PHOENIX_APPLY_HOST_RE.search(_flatten_public_text(cleaned, include_private=True)):
                # Drop source rows whose entire purpose is the banned portal.
                if set(cleaned.keys()) <= {"url", "title", "source_type", "publisher", "jurisdiction", "source_url"}:
                    continue
            cleaned_list.append(cleaned)
        return cleaned_list
    if isinstance(value, str):
        return _strip_banned_string(value, city=city, state=state)
    return value


def _row_decision_for_family(family_id: str, signals: list[FilingSignal]) -> str:
    if family_id == "historic_review":
        return "CONDITIONAL_REQUIRED"
    family_signals = [s for s in signals if family_id in s.filing_families]
    if family_signals and all(s.strength == "conditional" for s in family_signals):
        return "CONDITIONAL_REQUIRED"
    return "REQUIRED"


def _new_row(family_id: str, signals: list[FilingSignal], city: str, state: str, as_of: date | None = None) -> dict[str, Any]:
    family = FILING_FAMILIES[family_id]
    prov = _provenance_for(family_id, city, state, as_of=as_of)
    trigger_ids = [s.signal_id for s in signals if family_id in s.filing_families]
    decision = _row_decision_for_family(family_id, signals)
    row: dict[str, Any] = {
        "filing_family": family_id,
        "permit_type": family.label,
        "approval_type": family.label,
        "row_category": family.row_category,
        "required": True,
        "decision": decision,
        "trigger_signal_ids": list(dict.fromkeys(trigger_ids)),
        "ahj_name": prov["ahj_name"],
        "ahj_type": prov["ahj_type"],
        "application_authority_name": prov["application_authority_name"],
        "apply_url": prov["apply_url"] or None,
        "department_url": prov["department_url"] or None,
        "apply_url_status": prov["apply_url_status"],
        "source_status": prov["source_status"],
        "source_url": prov["source_url"] or None,
        "notes": (
            f"{decision.replace('_', ' ').title()} because the described scope triggers "
            f"{family.label}. Exact online apply path is metadata; if not verified, keep this row visible and verify routing before filing."
        ),
        "provenance": "universal_filing_packet_reconciler",
    }
    return {k: v for k, v in row.items() if v not in (None, "", [], {})}


def _reconcile_existing_row(row: dict[str, Any], family_id: str, signals: list[FilingSignal], city: str, state: str, as_of: date | None = None) -> dict[str, Any]:
    merged = dict(row)
    base = _new_row(family_id, signals, city, state, as_of=as_of)
    # Preserve useful user/model labels, but enforce the contract fields.
    for key, value in base.items():
        if key in {"permit_type", "approval_type"} and merged.get(key):
            continue
        if key == "notes" and merged.get("notes"):
            existing_notes = str(merged.get("notes") or "")
            base_notes = str(base.get("notes") or "")
            merged["notes"] = existing_notes if base_notes in existing_notes else f"{existing_notes} {base_notes}"
            continue
        merged[key] = value
    merged["filing_family"] = family_id
    merged["required"] = True
    merged["decision"] = _row_decision_for_family(family_id, signals)
    merged["trigger_signal_ids"] = list(dict.fromkeys(list(merged.get("trigger_signal_ids") or []) + list(base.get("trigger_signal_ids") or [])))
    if merged.get("apply_url") and BANNED_PHOENIX_APPLY_HOST_RE.search(str(merged.get("apply_url"))):
        merged["apply_url"] = None
        merged["apply_url_status"] = "needs_verification"
        merged["source_status"] = "needs_verification"
    return strip_banned_filing_packet_artifacts(merged, city, state)


def ensure_required_filing_rows(result: dict[str, Any], job_type: str, city: str, state: str, *, as_of: date | None = None) -> dict[str, Any]:
    """Inject/repair structured filing rows for every triggered family.

    The function is inject-only for advisory text: it adds structured rows and
    provenance metadata but does not delete useful checklist/gotcha/inspection
    prose. It may strip known-bad Phoenix portal/fee artifacts because those are
    stale anchors, not useful customer advice.
    """
    out = copy.deepcopy(result) if isinstance(result, dict) else {}
    out = strip_banned_filing_packet_artifacts(out, city, state)
    supplied_decision = str(out.get("permit_decision") or "").upper().strip()
    supplied_verdict = str(out.get("permit_verdict") or "").upper().strip()
    if supplied_decision == "NOT_REQUIRED" or out.get("permit_required") is False or supplied_verdict in {"NO", "NOT_REQUIRED"}:
        out["_filing_packet_reconciler_version"] = FILING_PACKET_RECONCILER_VERSION
        out["_cache_schema_version"] = CACHE_SCHEMA_VERSION
        out["_filing_packet_reconciler"] = {
            "version": FILING_PACKET_RECONCILER_VERSION,
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "signal_ids": [],
            "families": [],
            "injected_families": [],
            "repaired_families": [],
            "advisory_gate": "inject_only",
            "skipped": "preserved_not_required_decision",
        }
        return out
    signals = detect_filing_scope_signals(job_type, out, city, state)
    target_families: set[str] = {family for signal in signals for family in signal.filing_families if family in FILING_FAMILIES}

    # Reconcile existing rows only when the current scope/advisory signals require
    # that family. Existing unrelated rows pass through unchanged so simple
    # residential lookups do not gain filing-packet provenance/source URLs.
    raw_permits = out.get("permits_required") if isinstance(out.get("permits_required"), list) else []
    existing_rows = [row for row in raw_permits if isinstance(row, dict)]
    family_to_row: dict[str, dict[str, Any]] = {}
    passthrough: list[dict[str, Any]] = []
    for row in existing_rows:
        family = _family_from_existing_row(row, target_families)
        if family and family in target_families:
            family_to_row.setdefault(family, row)
        else:
            passthrough.append(strip_banned_filing_packet_artifacts(row, city, state))

    ordered_families = [family for family in FILING_FAMILY_ORDER if family in target_families]
    reconciled: list[dict[str, Any]] = []
    injected: list[str] = []
    repaired: list[str] = []
    for family in ordered_families:
        if family in family_to_row:
            before = family_to_row[family]
            after = _reconcile_existing_row(before, family, signals, city, state, as_of=as_of)
            if after != before:
                repaired.append(family)
            reconciled.append(after)
        else:
            reconciled.append(_new_row(family, signals, city, state, as_of=as_of))
            injected.append(family)

    # Preserve unrelated model rows after reconciled family rows unless they are
    # known-bad artifacts. Stable family rows are the customer-visible contract.
    reconciled.extend(passthrough)
    if reconciled:
        out["permits_required"] = reconciled
        out["permit_required"] = True
        out["permit_decision"] = "REQUIRED"
        out["permit_verdict"] = "YES"
        if not out.get("permit_name"):
            out["permit_name"] = reconciled[0].get("permit_type")
        if not out.get("permit_kind"):
            out["permit_kind"] = "Commercial Building / Tenant Improvement" if "building_ti" in target_families else ("ADU / Accessory Dwelling Unit" if "building_adu" in target_families else "Building")

    logic = out.get("permits_required_logic") if isinstance(out.get("permits_required_logic"), list) else []
    existing_logic_families = {_norm(item.get("filing_family")) for item in logic if isinstance(item, dict)}
    for row in reconciled:
        family = str(row.get("filing_family") or "")
        if family and family not in existing_logic_families:
            logic.append({
                "filing_family": family,
                "permit_type": row.get("permit_type"),
                "included_because": row.get("notes"),
                "scope_trigger": ", ".join(row.get("trigger_signal_ids") or []) or "filing-packet reconciliation",
            })
    if logic:
        out["permits_required_logic"] = logic

    out["_filing_packet_reconciler_version"] = FILING_PACKET_RECONCILER_VERSION
    out["_cache_schema_version"] = CACHE_SCHEMA_VERSION
    out["_filing_packet_reconciler"] = {
        "version": FILING_PACKET_RECONCILER_VERSION,
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "signal_ids": [s.signal_id for s in signals],
        "families": ordered_families,
        "injected_families": injected,
        "repaired_families": repaired,
        "advisory_gate": "inject_only",
    }
    return out
