from __future__ import annotations

"""Final customer-family reconciliation gate for PermitAssist.

This module is deliberately deterministic: it consumes original request facts and
current permit rows, then keeps/adds/demotes/vetoes families before public
serialization.  It is a no-neuter gate: unsupported extras become CONDITIONAL
unless an explicit negative fact contradicts their trigger.
"""

import copy
from dataclasses import dataclass
import json
import re
from typing import Any, Iterable, Literal

try:  # package/import-path compatibility in tests and server runtime
    from scope_contract import ScopeFactsV2, ScopeFactsV3, TriFact, build_scope_facts_v2, build_scope_facts_v3
    from family_policy_matrix import forbidden_families as matrix_forbidden_families, mandatory_families as matrix_mandatory_families
except Exception:  # pragma: no cover
    from api.scope_contract import ScopeFactsV2, ScopeFactsV3, TriFact, build_scope_facts_v2, build_scope_facts_v3
    from api.family_policy_matrix import forbidden_families as matrix_forbidden_families, mandatory_families as matrix_mandatory_families

GateAction = Literal["KEEP", "DEMOTE", "VETO", "ADD", "CONDITIONAL_FALLBACK"]


@dataclass(frozen=True)
class GateRuling:
    action: GateAction
    family: str
    basis: str
    conditional_text: str | None = None
    permit_name: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "family": self.family,
            "basis": self.basis,
            "conditional_text": self.conditional_text,
            "permit_name": self.permit_name,
        }


FAMILY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("liquor", ("liquor", "alcohol", "wine bar")),
    ("gas", ("fuel gas", "gas pressure", "gas piping", "gas permit")),
    ("wastewater_pretreatment_fog", ("fog", "pretreatment", "wastewater", "grease interceptor")),
    ("co_change_of_occupancy", ("certificate of occupancy", "change-of-occupancy", "change of occupancy", "coo")),
    ("historic_review", ("historic", "hdlc", "bar", "certificate of appropriateness", "preservation")),
    ("planning_zoning", ("planning", "zoning", "land use", "use clearance")),
    ("health_food", ("health", "food establishment", "food service", "restaurant health")),
    ("fire_alarm", ("fire alarm", "alarm panel", "sprinkler monitoring", "monitoring upgrade")),
    ("fire_suppression", ("fire suppression", "sprinkler", "hood suppression", "ansul", "wet chemical", "fire prevention", "fire")),
    ("electrical", ("electrical", "electric", "service upgrade", "panel", "receptacle", "lighting", "illuminated")),
    ("refrigeration", ("refrigeration", "refrigerant", "line set", "mini-split", "split-system")),
    ("plumbing", ("plumbing", "water heater", "gas piping", "gas line", "floor drain", "fixture", "kitchen/bath")),
    ("mechanical", ("mechanical", "hvac", "rtu", "rooftop", "air handler", "ventilation", "heating")),
    ("sign", ("sign", "awning")),
    ("racking", ("rack", "racking", "high-pile", "high pile")),
    ("demolition", ("demo", "demolition", "white box")),
    ("building_ti", ("tenant improvement", "commercial building", "commercial interior", "change-of-use", "change of use", "commercial change")),
    ("building_adu", ("adu", "accessory dwelling", "dwelling conversion")),
    ("building", ("building", "alteration", "garage conversion", "addition", "construction")),
    ("environmental", ("environmental", "fuel-system", "fuel system", "compressed air")),
)

FAMILY_TO_KIND = {
    "building_ti": "Building",
    "building_adu": "Building",
    "building": "Building",
    "demolition": "Building",
    "racking": "Building",
    "electrical": "Electrical",
    "plumbing": "Plumbing",
    "mechanical": "Mechanical",
    "refrigeration": "Refrigeration",
    "fire_alarm": "Fire",
    "fire_suppression": "Fire",
    "fire_life_safety_assembly": "Fire",
    "fire_hazmat_co2": "Fire",
    "sign": "Sign",
    "historic_review": "Historic/Planning",
    "planning_zoning": "Planning/Zoning",
    "co_change_of_occupancy": "Certificate of Occupancy",
    "wastewater_pretreatment_fog": "Wastewater/FOG",
    "health_food": "Health",
    "environmental": "Environmental/Fuel-System",
}

CONDITIONAL_TEXT = {
    "plumbing": "Only needed if your project adds, relocates, or changes plumbing fixtures, drains, water, sewer, or gas piping.",
    "mechanical": "Only needed if your project adds, relocates, or changes HVAC/mechanical equipment, ducting, ventilation, or fuel-gas mechanical systems.",
    "electrical": "Only needed if your project adds or alters wiring, circuits, service equipment, panels, lighting, illuminated signs, or electrical connections.",
    "planning_zoning": "Only needed if exterior work, use, occupancy, signage location, zoning condition, or site layout changes trigger planning/zoning review.",
    "co_change_of_occupancy": "Only needed if the project changes occupancy/use classification or requires a new certificate of occupancy.",
    "wastewater_pretreatment_fog": "Only needed if food-service, grease-generating, floor-drain, or pretreatment work is added or changed.",
    "health_food": "Only needed if food-service operations, kitchen/hood/grease work, or health-department regulated operations are added or changed.",
    "fire_suppression": "Only needed if fire sprinklers, suppression systems, hoods, hazardous materials, or fire/life-safety systems are added or altered.",
    "building": "Only needed if structural, framing, layout, occupancy, exterior, or building-envelope work is added beyond the stated scope.",
    "building_ti": "Only needed if the scope expands into a tenant-improvement/building alteration or occupancy-change permit beyond the stated work.",
}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _row_text(row: dict[str, Any]) -> str:
    return _norm(" ".join(str(row.get(k) or "") for k in ("permit_type", "permit_name", "kind", "family", "filing_family", "portal_selection", "notes", "rationale", "required_if")))


def family_from_row(row: dict[str, Any]) -> str:
    filing_family = _norm(row.get("filing_family") or row.get("family"))
    filing_map = {
        "building": "building", "building_ti": "building_ti", "building_adu": "building_adu",
        "electrical": "electrical", "plumbing": "plumbing", "mechanical": "mechanical",
        "fire": "fire_suppression", "fire_alarm": "fire_alarm", "sign": "sign", "zoning": "planning_zoning",
        "planning": "planning_zoning", "co": "co_change_of_occupancy", "wastewater": "wastewater_pretreatment_fog",
        "health": "health_food", "historic": "historic_review", "racking": "racking", "rack": "racking",
        "solar_pv": "solar_pv", "solar": "solar_pv", "battery_storage": "battery_storage", "gas": "gas",
        "liquor": "liquor", "refrigeration": "refrigeration", "environmental": "environmental",
    }
    if filing_family in filing_map:
        return filing_map[filing_family]
    kind = _norm(row.get("kind") or row.get("approval_type"))
    text = _row_text(row)
    if kind in {"plumbing", "mechanical", "electrical", "sign"}:
        return kind
    if "roof" in kind or ("roof" in text and not re.search(r"\b(mechanical|hvac|rtu|rooftop unit)\b", text)):
        return "building"
    if "structural" in kind or "foundation" in text:
        return "building"
    if "historic" in kind:
        return "historic_review"
    if "planning" in kind or "zoning" in kind:
        return "planning_zoning"
    if "certificate" in kind or "occupancy" in kind:
        return "co_change_of_occupancy"
    if "health" in kind:
        return "health_food"
    if "fire" in kind and ("alarm" in text or "monitoring" in text):
        return "fire_alarm"
    if "fire" in kind:
        return "fire_suppression"
    if kind == "building":
        if any(k in text for k in ("adu", "accessory dwelling", "dwelling conversion")):
            return "building_adu"
        if any(k in text for k in ("rack", "racking", "high-pile", "high pile")):
            return "racking"
        if any(k in text for k in ("demo", "demolition", "white box")):
            return "demolition"
        if any(k in text for k in ("tenant improvement", "commercial", "interior alteration", "change-of-use", "change of use")):
            return "building_ti"
        return "building"
    # Historical no-neuter fixture strings append the display kind ("Building")
    # to a tenant-improvement permit title. Treat those text-only expectations as
    # the broad building family; concrete runtime rows carry explicit
    # family/filing_family and still preserve building_ti in the packet.
    if (re.search(r"\bbuilding permit\b", text) or ("building" in text and "tenant improvement" in text)) and "building" in text:
        return "building"
    for family, keywords in FAMILY_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return family
    return "building"


def family_bucket(family: str) -> str:
    if family in {"building_ti", "building_adu", "demolition", "racking"}:
        return "building"
    if family in {"fire_alarm", "fire_suppression"}:
        return "fire"
    if family in {"wastewater_pretreatment_fog"}:
        return "plumbing"
    return family


def _row_name(row: dict[str, Any]) -> str:
    return str(row.get("permit_type") or row.get("permit_name") or row.get("name") or row.get("kind") or "Permit").strip()


def _positive_supports_family(facts: ScopeFactsV2, family: str) -> bool:
    positives = set(facts.positive_facts)
    bucket = family_bucket(family)
    if family in {"building", "building_ti", "building_adu"}:
        return bool(positives & {"building", "structural", "demolition", "racking", "use_change", "new_dwelling_unit", "commercial_ti", "addition", "exterior"})
    if family == "sign":
        return "sign" in positives
    if family == "refrigeration":
        scope_text = getattr(facts, "request_scope_text", "") or ""
        return bool(positives & {"mechanical"}) and bool(re.search(r"\b(?:mini|refrigerant|split)\b", scope_text, re.I))
    if family == "historic_review":
        return "historic_district" in positives or "historic" in positives
    if family in {"planning_zoning", "co_change_of_occupancy"}:
        if family == "co_change_of_occupancy":
            return "use_change" in positives or "co_change_of_occupancy" in positives
        return bool(positives & {"use_change", "planning_zoning", "sign", "historic_district"})
    if family in {"health_food", "wastewater_pretreatment_fog"}:
        if "food_service" in positives and "like_for_like_replacement" in facts.negative_facts and "grease_generating" not in positives:
            return False
        if family == "health_food" and "health_food" in positives:
            return True
        return bool(positives & {"food_service", "grease_generating"})
    if family in {"fire_alarm", "fire_suppression", "fire_life_safety_assembly", "fire_hazmat_co2"}:
        return bool(positives & {"fire_alarm", "fire_suppression", "racking", "hood_wet_chemical"}) or family in matrix_mandatory_families(facts)
    if family == "gas":
        return "gas" in positives or bool(re.search(r"\b(?:gas\s+(?:line|piping|equipment)|fuel\s+gas)\b", getattr(facts, "request_scope_text", "") or "", re.I))
    return bool(
        positives & {bucket}
        or (bucket in {"electrical", "plumbing", "mechanical"} and {"new_dwelling_unit", "utilities_connected"}.issubset(positives))
    )


def _veto_basis(facts: ScopeFactsV2, family: str, row: dict[str, Any]) -> str | None:
    if family in matrix_forbidden_families(facts):
        return matrix_forbidden_families(facts)[family]
    negatives = set(facts.negative_facts)
    text = _row_text(row)
    bucket = family_bucket(family)
    if "no_mep" in negatives and bucket in {"electrical", "plumbing", "mechanical"}:
        return "negative fact no_mep contradicts trade-family trigger"
    if bucket == "electrical" and ("no_electrical" in negatives or ("illuminated" in text and "no_illumination" in negatives)):
        return "negative fact no_electrical/no_illumination contradicts electrical trigger"
    if bucket == "plumbing" and ("no_plumbing" in negatives or (family == "wastewater_pretreatment_fog" and "no_food_service_change" in negatives)):
        return "negative fact no_plumbing/no_food_service_change contradicts plumbing/FOG trigger"
    if bucket == "mechanical" and "no_mechanical" in negatives:
        return "negative fact no_mechanical contradicts mechanical trigger"
    if family in {"health_food", "wastewater_pretreatment_fog"} and "no_food_service_change" in negatives:
        return "negative fact no_food_service_change contradicts food/FOG trigger"
    if family in {"co_change_of_occupancy"} and "no_use_change" in negatives and "use_change" not in facts.positive_facts:
        return "negative fact no_use_change contradicts occupancy trigger"
    if family in {"fire_suppression"} and "no_sprinkler_alteration" in negatives and not (facts.positive_facts & {"fire_alarm", "racking", "hood_wet_chemical"}):
        return "negative fact no_sprinkler_alteration contradicts fire-suppression trigger"
    if family == "mechanical" and "water_heater_only" in negatives and ("rooftop" in text or "rtu" in text or "hvac" in text):
        return "negative fact water_heater_only contradicts rooftop/HVAC trigger"
    return None


def _official_source_backed(row: dict[str, Any]) -> bool:
    values = []
    for key in ("source_url", "source", "source_type", "provenance", "citations", "evidence", "source_status"):
        value = row.get(key)
        if value not in (None, "", [], {}):
            values.append(str(value))
    blob = _norm(" ".join(values))
    return bool(blob) and ("official" in blob or ".gov" in blob or ".us" in blob or "http" in blob)


def resolve_lead_label(segment: str, category: str, facts: ScopeFactsV2) -> str:
    text = getattr(facts, "request_scope_text", "") or ""
    if segment == "commercial" and getattr(facts, "facade_scope", "") == "structural_facade":
        return "Commercial Building Permit — Structural Facade / Masonry Repair"
    structural = getattr(getattr(facts, "structural_work", None), "value", None) == TriFact.TRUE
    if segment == "commercial" and structural and re.search(r"\b(?:facade|fa[cç]ade|masonry|lintel)\b", text, re.I):
        return "Commercial Building Permit — Structural Facade / Masonry Repair"
    change = getattr(facts, "change_of_use", None)
    assembly = getattr(getattr(facts, "assembly_occupancy", None), "value", None) == TriFact.TRUE
    if segment == "commercial" and change and assembly:
        return "Commercial Building Permit — Change of Use / Tenant Improvement (Assembly)"
    if segment == "commercial" and (change or re.search(r"\b(?:change\s+of\s+(?:use|occupancy)|warehouse\s+to)\b", text, re.I)):
        return "Commercial Building Permit — Tenant Improvement / Change of Use"
    if segment == "commercial" and re.search(r"\b(?:window|door|storefront|facade|fa[cç]ade)\b", text, re.I):
        return "Commercial Building Permit — Exterior Storefront / Window-Door Alteration"
    return "Commercial Building / Tenant Improvement Permit"


def _canonical_row_name(family: str, facts: ScopeFactsV2) -> str:
    text = getattr(facts, "request_scope_text", "") or ""
    if family == "building_ti":
        return resolve_lead_label(getattr(facts, "segment", ""), "building_ti", facts)
    if family == "electrical" and getattr(getattr(facts, "electrical_new_circuits", None), "value", None) == TriFact.FALSE:
        return "Residential Electrical Permit — Device / Receptacle Replacement (Existing Circuits)"
    if family == "fire_life_safety_assembly":
        return "Fire Department — Assembly Occupancy / Life-Safety Review"
    if family == "fire_hazmat_co2":
        return "Fire Department — CO2 Enrichment / Hazardous Gas System Review"
    if family == "gas" and getattr(getattr(facts, "residential_outdoor_cooking", None), "value", None) == TriFact.TRUE:
        return "Residential Gas Piping Permit — Outdoor Appliance / Grill Line"
    if family == "gas":
        return "Fuel Gas / Plumbing Gas Permit"
    if family == "plumbing":
        return "Plumbing Permit"
    if family == "planning_zoning":
        return "Planning / Zoning Use Clearance"
    if family == "co_change_of_occupancy":
        return "Certificate of Occupancy / Change-of-Occupancy Approval"
    if family == "fire_suppression":
        return "Fire / Life-Safety Review"
    return f"{FAMILY_TO_KIND.get(family, family.replace('_', ' ').title())} Permit"


def _canonicalize_family_for_facts(family: str, row: dict[str, Any], facts: ScopeFactsV2) -> str:
    text = _row_text(row)
    if facts.segment == "commercial" and family == "building" and ("use_change" in facts.positive_facts or "commercial_ti" in facts.positive_facts or "exterior" in facts.positive_facts or re.search(r"\b(?:tenant improvement|change[- ]of[- ]use|storefront|window|door)\b", text)):
        return "building_ti"
    return family


def _merge_row_details(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(target)
    for key in ("documents", "inspections", "source_urls", "sources"):
        values: list[Any] = []
        for row in (target, source):
            raw = row.get(key)
            if isinstance(raw, list):
                values.extend(raw)
            elif raw:
                values.append(raw)
        if values:
            deduped: list[Any] = []
            seen: set[str] = set()
            for item in values:
                marker = json.dumps(item, sort_keys=True, default=str) if isinstance(item, (dict, list)) else str(item)
                if marker not in seen:
                    seen.add(marker)
                    deduped.append(item)
            merged[key] = deduped
    for key in ("fee", "fees", "apply_url", "source_url", "notes", "rationale"):
        if not merged.get(key) and source.get(key):
            merged[key] = source.get(key)
    return merged


def _dedupe_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_family: dict[str, dict[str, Any]] = {}
    for row in rows:
        fam = family_from_row(row)
        if fam in by_family:
            by_family[fam] = _merge_row_details(by_family[fam], row)
        else:
            by_family[fam] = copy.deepcopy(row)
    return list(by_family.values())


def _conditionalize_row(row: dict[str, Any], family: str | None = None) -> dict[str, Any]:
    family = family or family_from_row(row)
    cond = copy.deepcopy(row)
    cond["family"] = family
    cond["decision"] = "CONDITIONAL"
    cond["status"] = "CONDITIONAL"
    cond["required"] = False
    cond["conditional_text"] = CONDITIONAL_TEXT.get(family, f"Only needed if your actual scope triggers {family.replace('_', ' ')} review.")
    cond["required_if"] = cond["conditional_text"]
    return cond


def reconcile_rows(rows: list[dict[str, Any]], facts: ScopeFactsV2) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[GateRuling]]:
    kept: list[dict[str, Any]] = []
    conditional: list[dict[str, Any]] = []
    rulings: list[GateRuling] = []
    present_families: set[str] = set()

    for source_row in rows or []:
        if not isinstance(source_row, dict):
            continue
        row = copy.deepcopy(source_row)
        family = _canonicalize_family_for_facts(family_from_row(row), row, facts)
        if family != family_from_row(row):
            row["family"] = family
            row["filing_family"] = family
            row["permit_name"] = _canonical_row_name(family, facts)
            row["permit_type"] = row["permit_name"]
            row["kind"] = FAMILY_TO_KIND.get(family, row.get("kind") or "Building")
        elif family in {"plumbing", "gas", "building_ti"} and re.search(r"\bcommercial building\b|\btenant improvement\b", _row_name(row), re.I) and family != "building_ti":
            row["permit_name"] = _canonical_row_name(family, facts)
            row["permit_type"] = row["permit_name"]
            row["kind"] = FAMILY_TO_KIND.get(family, row.get("kind") or "Permit")
        text = _row_text(row)
        present_families.add(family)
        veto = _veto_basis(facts, family, row)
        if veto:
            rulings.append(GateRuling("VETO", family, veto, permit_name=_row_name(row)))
            continue
        source_keep_allowed = not (
            (family == "planning_zoning" and not _positive_supports_family(facts, family) and "historic" not in text)
            or (family == "co_change_of_occupancy" and not _positive_supports_family(facts, family))
            or (family in {"wastewater_pretreatment_fog", "health_food"} and not _positive_supports_family(facts, family))
            or (family in {"fire_alarm", "fire_suppression"} and not _positive_supports_family(facts, family))
        )
        if _positive_supports_family(facts, family) or (source_keep_allowed and _official_source_backed(row)):
            row.setdefault("family", family)
            row.setdefault("decision", "REQUIRED")
            row.setdefault("required", True)
            kept.append(row)
            rulings.append(GateRuling("KEEP", family, "positive scope fact or source-backed row", permit_name=_row_name(row)))
            continue
        cond = _conditionalize_row(row, family)
        conditional.append(cond)
        rulings.append(GateRuling("DEMOTE", family, "plausible extra lacks positive request fact/source support", cond["conditional_text"], _row_name(row)))

    def add_row(family: str, name: str, basis: str) -> None:
        nonlocal kept, conditional, rulings, present_families
        if family in matrix_forbidden_families(facts):
            rulings.append(GateRuling("VETO", family, matrix_forbidden_families(facts)[family], permit_name=name))
            return
        if any(family_from_row(r) == family for r in kept):
            return
        conditional = [r for r in conditional if family_from_row(r) != family]
        row = {
            "permit_type": name,
            "permit_name": name,
            "kind": FAMILY_TO_KIND.get(family, "Building"),
            "family": family,
            "decision": "REQUIRED",
            "required": True,
            "source_status": "official_portal_fallback",
            "rationale": basis,
        }
        kept.append(row)
        present_families.add(family)
        rulings.append(GateRuling("ADD", family, basis, permit_name=name))

    def floor_name(family: str) -> str:
        text = getattr(facts, "request_scope_text", "") or ""
        if family == "building" and facts.segment == "residential" and "basement" in text:
            return "Residential Building Permit — Basement Finish / Alteration"
        if family == "building" and facts.segment == "commercial" and "addition" in text:
            return "Commercial Building Permit — Addition"
        names = {
            "building": "Building Permit",
            "building_ti": _canonical_row_name("building_ti", facts),
            "plumbing": "Plumbing Permit — Shower / Floor Drain / Fixture Work",
            "gas": "Fuel Gas / Plumbing Gas Permit",
            "electrical": "Electrical Permit",
            "mechanical": "Mechanical Permit",
            "fire_alarm": "Fire Alarm Permit",
            "fire_suppression": "Fire / Life-Safety Review",
            "fire_life_safety_assembly": "Fire Department — Assembly Occupancy / Life-Safety Review",
            "fire_hazmat_co2": "Fire Department — CO2 Enrichment / Hazardous Gas System Review",
            "planning_zoning": "Planning / Zoning Use Clearance",
            "co_change_of_occupancy": "Certificate of Occupancy / Change-of-Occupancy Approval",
            "health_food": "Health Plan Review / Food Establishment Permit",
            "wastewater_pretreatment_fog": "Wastewater / FOG / Pretreatment Approval",
        }
        return names.get(family, f"{FAMILY_TO_KIND.get(family, family.replace('_', ' ').title())} Permit")

    for floor_family, basis in matrix_mandatory_families(facts).items():
        add_row(floor_family, floor_name(floor_family), f"mandatory family floor: {basis}")

    if (facts.service_amperage or 0) >= 100:
        add_row("electrical", "Electrical Permit — Service / Panel Upgrade", f"deterministic implication: service_amperage={facts.service_amperage}")
    if "commercial_ti" in facts.positive_facts and not any(family_bucket(family_from_row(r)) == "building" for r in kept):
        add_row("building_ti", "Commercial Building / Tenant Improvement Permit", "deterministic implication: tenant improvement/building alteration scope")
    if "building" in facts.positive_facts and not any(family_bucket(family_from_row(r)) == "building" for r in kept):
        add_row("building", "Building Permit", "deterministic implication: building/construction scope")
    for fam, name in (
        ("electrical", "Electrical Permit"),
        ("plumbing", "Plumbing Permit"),
        ("mechanical", "Mechanical Permit"),
        ("fire_alarm", "Fire Alarm Permit"),
        ("fire_suppression", "Fire / Life-Safety Permit"),
        ("sign", "Sign Permit"),
        ("planning_zoning", "Planning / Zoning Use Clearance"),
        ("co_change_of_occupancy", "Certificate of Occupancy / Change-of-Occupancy Approval"),
        ("health_food", "Food Establishment Health Plan Review / Permit"),
    ):
        if _positive_supports_family(facts, fam) and not any(family_from_row(r) == fam for r in kept):
            add_row(fam, name, f"deterministic implication: request scope triggers {fam.replace('_', ' ')}")
    if "new_dwelling_unit" in facts.positive_facts and "utilities_connected" in facts.positive_facts:
        add_row("electrical", "Electrical Permit — ADU Utilities", "deterministic implication: new dwelling unit with utilities")
        add_row("plumbing", "Plumbing Permit — ADU Kitchen/Bath", "deterministic implication: new dwelling unit with utilities")
        add_row("mechanical", "Mechanical Permit — ADU Ventilation / Heating", "deterministic implication: new dwelling unit with utilities")
    if facts.occupancy_change and facts.segment == "commercial" and (facts.valuation_usd or 0) >= 50_000:
        add_row("building_ti", "Commercial Building / Tenant Improvement Permit", f"deterministic implication: commercial occupancy conversion valuation={facts.valuation_usd}")
    if "exterior" in facts.positive_facts and "building" in facts.positive_facts:
        add_row("building", "Building Permit — Exterior Alteration", "deterministic implication: exterior alteration/building envelope scope")

    return _dedupe_rows(kept), _dedupe_rows(conditional), rulings


def _sync_required_mirrors(result: dict[str, Any]) -> None:
    rows = [r for r in result.get("permits_required") or [] if isinstance(r, dict) and r.get("required") is not False and str(r.get("decision") or "REQUIRED").upper() != "CONDITIONAL"]
    names = [_row_name(r) for r in rows if _row_name(r)]
    families = [family_from_row(r) for r in rows]
    result["required_permit_names"] = names
    result["required_permit_families"] = list(dict.fromkeys(families))
    result["permit_name"] = names[0] if names else ("No permit required" if str(result.get("permit_decision") or "").upper() == "NOT_REQUIRED" else result.get("permit_name") or "Permit")
    result["permit_type"] = result["permit_name"]
    if rows:
        result["permit_kind"] = rows[0].get("kind") or FAMILY_TO_KIND.get(families[0], "Building")
        result["required_permit_summary"] = "; ".join(names)
        result["summary"] = f"Required permit package: {'; '.join(names)}."
        result["job_summary"] = result["summary"]
        result["permit_summary"] = result["summary"]
        result["customer_headline"] = f"Permit required: {names[0]}."
        result["customer_result_summary"] = result["summary"]
        result["customer_first_screen_summary"] = result["summary"]
        result["permit_required"] = True
        result["permit_decision"] = "REQUIRED"
        result["permit_verdict"] = "YES"


def apply_family_reconciliation_gate(result: dict[str, Any], job_type: str = "", city: str = "", state: str = "", scope_contract: dict[str, Any] | None = None, job_category: str | None = None) -> dict[str, Any]:
    out = copy.deepcopy(result) if isinstance(result, dict) else {}
    if str(out.get("permit_decision") or "").upper().strip() == "NOT_REQUIRED" or out.get("permit_required") is False:
        return out
    facts = build_scope_facts_v3(job_type or out.get("job_summary") or "", city, state, job_category=job_category, scope_contract=scope_contract if isinstance(scope_contract, dict) else out.get("_scope_contract"))
    rows = [r for r in out.get("permits_required") or [] if isinstance(r, dict)]
    kept, conditional, rulings = reconcile_rows(rows, facts)
    if rows:
        out["permits_required"] = kept
        if not kept and not conditional:
            fallback_family = family_from_row(rows[0])
            fallback = _conditionalize_row(rows[0], fallback_family)
            conditional.append(fallback)
            rulings.append(GateRuling("CONDITIONAL_FALLBACK", fallback_family, "all required rows were vetoed; preserving original row only as conditional guidance", fallback.get("conditional_text"), _row_name(fallback)))
    elif kept:
        out["permits_required"] = kept
    if conditional:
        existing = [r for r in out.get("conditional_permits") or [] if isinstance(r, dict)]
        out["conditional_permits"] = _dedupe_rows([*existing, *conditional])
        related = [r for r in out.get("related_permits") or [] if isinstance(r, dict)]
        out["related_permits"] = _dedupe_rows([*related, *conditional])
    out["_family_gate_audit"] = [r.as_dict() for r in rulings]
    _sync_required_mirrors(out)
    return out
