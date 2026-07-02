from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import copy
import re
from typing import Any
from urllib.parse import urlparse

from project_scope_attributes import (
    AttributeValue,
    Occupancy,
    ProjectScopeAttributes,
    SignIllumination,
    WorkNature,
    extract_project_scope_attributes,
)


class DecisionStatus(str, Enum):
    REQUIRED = "REQUIRED"
    CONDITIONAL = "CONDITIONAL"
    NOT_REQUIRED = "NOT_REQUIRED"


class FeeType(str, Enum):
    PERMIT_FEE = "permit_fee"
    PROJECT_COST_ESTIMATE = "project_cost_estimate"
    BENCHMARK_ESTIMATE = "benchmark_estimate"


@dataclass(frozen=True)
class TypedFee:
    text: str
    fee_type: FeeType
    family: str | None = None
    source_url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "fee_type": self.fee_type.value,
            "family": self.family,
            "source_url": self.source_url,
        }


@dataclass(frozen=True)
class DecisionItem:
    family: str
    status: DecisionStatus
    permit_name: str
    lead_eligible: bool = True
    trigger: str | None = None
    fees: tuple[TypedFee, ...] = ()
    source_urls: tuple[str, ...] = ()
    documents: tuple[str, ...] = ()
    inspections: tuple[str, ...] = ()
    provenance: str = "project_scope_attributes.v1"

    def as_public_row(self) -> dict[str, Any]:
        public_family = "building_ti" if self.family == "building" and "tenant improvement" in self.permit_name.lower() else self.family
        row = {
            "family": public_family,
            "filing_family": public_family,
            "decision": self.status.value,
            "status": self.status.value,
            "permit_name": self.permit_name,
            "permit_type": self.permit_name,
            "kind": _kind_for_family(self.family),
            "reason": self.trigger or "Required because the described project scope includes this permit family.",
            "trigger": self.trigger,
            "required_if": self.trigger if self.status == DecisionStatus.CONDITIONAL else None,
            "fees": [fee.as_dict() for fee in self.fees if fee.fee_type == FeeType.PERMIT_FEE],
            "source_urls": list(self.source_urls),
            "documents": list(self.documents),
            "inspections": list(self.inspections),
            "lead_eligible": self.lead_eligible,
        }
        return {k: v for k, v in row.items() if v not in (None, [], {})}


@dataclass(frozen=True)
class DecisionObject:
    schema_version: str
    attrs: ProjectScopeAttributes
    items: tuple[DecisionItem, ...]
    lead_family: str | None
    lead_permit_name: str | None
    invariants: tuple[str, ...] = (
        "I1_closed_world_required",
        "I2_occupancy_gate",
        "I3_co_only_change_of_use",
        "I4_bidirectional_trade_rule",
        "I5_food_fog_only_food_service",
        "I6_typed_fees_only",
        "I7_renderable_links_only",
        "I8_render_fidelity",
        "I9_conditional_concrete_trigger",
        "I10_deterministic_lead_rule",
        "I11_dedup_one_entry_per_family",
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scope_attributes": self.attrs.as_dict(),
            "lead_family": self.lead_family,
            "lead_permit_name": self.lead_permit_name,
            "items": [item.as_public_row() for item in self.items],
            "invariants": list(self.invariants),
        }


@dataclass(frozen=True)
class LinkLiveness:
    url: str
    renderable: bool
    reason: str
    status: str = "offline_static_check"

    def as_dict(self) -> dict[str, Any]:
        return {"url": self.url, "renderable": self.renderable, "reason": self.reason, "status": self.status}


FAMILY_APPLICABILITY_METADATA: dict[str, dict[str, Any]] = {
    "building": {"applies_when": ["structural", "change_of_use", "building_envelope", "new_construction", "addition"], "occupancy_scope": "any", "lead_eligible_for": ["structural", "tenant_improvement", "addition", "new_construction"], "provenance": "family_level_bulk_backfill.v1"},
    "electrical": {"applies_when": ["electrical_trade", "illuminated_sign", "battery_storage", "solar_pv", "service_panel"], "occupancy_scope": "any", "lead_eligible_for": ["electrical", "battery_storage", "minor_electrical"], "provenance": "family_level_bulk_backfill.v1"},
    "mechanical": {"applies_when": ["mechanical_trade", "hvac", "rtu", "hood_ventilation"], "occupancy_scope": "any", "lead_eligible_for": ["mechanical", "hvac"], "provenance": "family_level_bulk_backfill.v1"},
    "plumbing": {"applies_when": ["plumbing_trade", "fixtures", "water_heater", "drains"], "occupancy_scope": "any", "lead_eligible_for": ["plumbing"], "provenance": "family_level_bulk_backfill.v1"},
    "gas": {"applies_when": ["gas_piping", "gas_equipment", "pressure_test"], "occupancy_scope": "any", "lead_eligible_for": ["gas"], "provenance": "family_level_bulk_backfill.v1"},
    "fire_alarm": {"applies_when": ["fire_alarm_devices", "alarm_panel", "life_safety_system"], "occupancy_scope": "any", "lead_eligible_for": ["fire_alarm"], "provenance": "family_level_bulk_backfill.v1"},
    "fire_suppression": {"applies_when": ["sprinkler", "hood_suppression", "wet_chemical", "life_safety_system"], "occupancy_scope": "any", "lead_eligible_for": ["fire_suppression", "restaurant_ti"], "provenance": "family_level_bulk_backfill.v1"},
    "health_food": {"applies_when": ["commercial_food_service", "food_establishment", "commercial_kitchen"], "occupancy_scope": "commercial", "lead_eligible_for": ["restaurant", "food_service"], "provenance": "family_level_bulk_backfill.v1"},
    "wastewater_pretreatment_fog": {"applies_when": ["grease_generating", "fog", "grease_interceptor", "commercial_food_service"], "occupancy_scope": "commercial", "lead_eligible_for": ["restaurant", "grease_interceptor"], "provenance": "family_level_bulk_backfill.v1"},
    "planning_zoning": {"applies_when": ["zoning_clearance", "sign", "exterior", "use_change", "setback"], "occupancy_scope": "any", "lead_eligible_for": ["zoning", "sign"], "provenance": "family_level_bulk_backfill.v1"},
    "co_change_of_occupancy": {"applies_when": ["change_of_use_true", "occupancy_classification_change"], "occupancy_scope": "commercial", "lead_eligible_for": ["change_of_use"], "provenance": "family_level_bulk_backfill.v1"},
    "sign": {"applies_when": ["sign", "wall_sign", "monument_sign", "sign_face", "illuminated_sign"], "occupancy_scope": "commercial", "lead_eligible_for": ["sign"], "provenance": "family_level_bulk_backfill.v1"},
    "battery_storage": {"applies_when": ["battery_storage", "ess", "energy_storage"], "occupancy_scope": "any", "lead_eligible_for": ["battery_storage", "ess"], "provenance": "family_level_bulk_backfill.v1"},
    "solar_pv": {"applies_when": ["new_solar_panels", "pv", "photovoltaic"], "occupancy_scope": "any", "lead_eligible_for": ["solar_pv"], "provenance": "family_level_bulk_backfill.v1"},
    "historic": {"applies_when": ["historic_district", "landmark", "exterior_historic"], "occupancy_scope": "any", "lead_eligible_for": ["historic"], "provenance": "family_level_bulk_backfill.v1"},
    "roofing": {"applies_when": ["roof", "reroof", "roofing"], "occupancy_scope": "any", "lead_eligible_for": ["roofing"], "provenance": "family_level_bulk_backfill.v1"},
    "refrigeration": {"applies_when": ["refrigerant_lines", "split_system", "mini_split"], "occupancy_scope": "any", "lead_eligible_for": ["refrigeration"], "provenance": "family_level_bulk_backfill.v1"},
    "grading": {"applies_when": ["right_of_way", "site_civil", "grading", "street_cut"], "occupancy_scope": "any", "lead_eligible_for": ["site_civil"], "provenance": "family_level_bulk_backfill.v1"},
    "utility": {"applies_when": ["utility_interconnection", "pto", "service_connection"], "occupancy_scope": "any", "lead_eligible_for": ["utility"], "provenance": "family_level_bulk_backfill.v1"},
    "liquor": {"applies_when": ["alcohol_service", "bar", "brewery", "liquor_license"], "occupancy_scope": "commercial", "lead_eligible_for": ["liquor"], "provenance": "family_level_bulk_backfill.v1"},
    "environmental": {"applies_when": ["fuel_system", "environmental_review", "hazardous_materials"], "occupancy_scope": "any", "lead_eligible_for": ["environmental"], "provenance": "family_level_bulk_backfill.v1"},
    "other": {"applies_when": ["specialty_review"], "occupancy_scope": "any", "lead_eligible_for": [], "provenance": "family_level_bulk_backfill.v1"},
}

_FAMILY_ALIASES = {
    "co": "co_change_of_occupancy",
    "building_ti": "building",
    "fire": "fire_suppression",
    "health": "health_food",
    "planning": "planning_zoning",
    "wastewater": "wastewater_pretreatment_fog",
    "solar": "solar_pv",
    "structural": "building",
}


def canonical_family(family: str | None, permit_name: str | None = None) -> str:
    raw = _norm(family)
    if raw in FAMILY_APPLICABILITY_METADATA:
        return raw
    if raw in _FAMILY_ALIASES:
        return _FAMILY_ALIASES[raw]
    name = _norm(permit_name)
    if "certificate of occupancy" in name or "change-of-occupancy" in name or "change of occupancy" in name:
        return "co_change_of_occupancy"
    if "food" in name or "health" in name:
        return "health_food"
    if "fog" in name or "grease" in name or "wastewater" in name:
        return "wastewater_pretreatment_fog"
    if "fire alarm" in name:
        return "fire_alarm"
    if "fire" in name or "suppression" in name:
        return "fire_suppression"
    if "zoning" in name or "planning" in name:
        return "planning_zoning"
    if "solar" in name or "pv" in name:
        return "solar_pv"
    if "roof" in name:
        return "roofing"
    if "sign" in name:
        return "sign"
    if "gas" in name:
        return "gas"
    for token in ("building", "electrical", "mechanical", "plumbing", "historic", "refrigeration", "grading", "utility"):
        if token in name:
            return token
    return raw or "other"


def family_metadata_for(family: str | None, permit_name: str | None = None) -> dict[str, Any] | None:
    return FAMILY_APPLICABILITY_METADATA.get(canonical_family(family, permit_name))


_FAMILY_NAMES = {
    "building": "Building Permit",
    "electrical": "Electrical Permit",
    "mechanical": "Mechanical Permit",
    "plumbing": "Plumbing Permit",
    "gas": "Gas Pressure Test / Fuel Gas Permit",
    "fire_alarm": "Fire Alarm Permit",
    "fire_suppression": "Fire Suppression / Fire Prevention Review",
    "health_food": "Health Plan Review / Food Establishment Permit",
    "wastewater_pretreatment_fog": "Wastewater / FOG / Pretreatment Approval",
    "planning_zoning": "Planning / Zoning Use Clearance",
    "co_change_of_occupancy": "Certificate of Occupancy / Change-of-Occupancy Approval",
    "sign": "Sign Permit",
    "battery_storage": "Electrical Permit — Battery Energy Storage System (ESS)",
    "solar_pv": "Solar / PV",
    "refrigeration": "Refrigeration Permit",
}

_LEAD_PRIORITY = [
    "building",
    "sign",
    "battery_storage",
    "electrical",
    "mechanical",
    "plumbing",
    "gas",
    "health_food",
    "fire_suppression",
    "fire_alarm",
    "planning_zoning",
]

_OFFICIAL_FALLBACKS = {
    ("st. louis", "mo"): "https://www.stlouis-mo.gov/government/departments/public-safety/building/permits",
    ("gilbert", "az"): "https://www.gilbertaz.gov/departments/development-services",
    ("boise", "id"): "https://www.cityofboise.org/departments/planning-and-development-services/",
    ("phoenix", "az"): "https://www.phoenix.gov/pdd",
    ("austin", "tx"): "https://www.austintexas.gov/department/development-services",
}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _source_urls(result: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("source_urls", "sources"):
        raw = result.get(key)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    urls.append(item)
                elif isinstance(item, dict):
                    u = item.get("url") or item.get("source_url") or item.get("link")
                    if u:
                        urls.append(str(u))
    apply_path = result.get("apply_path")
    if isinstance(apply_path, dict):
        for key in ("portal_url", "filing_sources", "requirement_sources"):
            raw = apply_path.get(key)
            if isinstance(raw, str):
                urls.append(raw)
            elif isinstance(raw, list):
                urls.extend(str(u) for u in raw if u)
    if result.get("apply_url"):
        urls.append(str(result.get("apply_url")))
    dedup: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url.startswith("http") and url not in seen:
            seen.add(url)
            dedup.append(url)
    return dedup


def classify_renderable_link(url: str, city: str = "", state: str = "", occupancy: Occupancy | None = None) -> LinkLiveness:
    text = str(url or "").strip()
    if not text:
        return LinkLiveness(text, False, "empty")
    parsed = urlparse(text)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if parsed.scheme not in {"http", "https"} or not host:
        return LinkLiveness(text, False, "invalid_url")
    if re.search(r"/(?:12345|0000|placeholder|tbd)(?:/|$|[-_.])", path) or re.search(r"(?:placeholder|tbd)", text, re.I):
        return LinkLiveness(text, False, "placeholder_pattern")
    if host in {"example.com", "www.example.com"}:
        return LinkLiveness(text, False, "irrelevant_domain")
    if re.search(r"\b(blog|news|press|archive|dailycolonist|ojp\.gov)\b", text, re.I):
        return LinkLiveness(text, False, "irrelevant_content")
    segment_text = f"{host} {path}"
    if occupancy == Occupancy.COMMERCIAL and re.search(r"\b(residential|homeowner|single[-_]?family)\b", segment_text, re.I):
        return LinkLiveness(text, False, "segment_mismatch_residential_url")
    if occupancy == Occupancy.RESIDENTIAL and re.search(r"\b(commercial|business|tenant[-_]?improvement|ti[-_/])\b", segment_text, re.I):
        return LinkLiveness(text, False, "segment_mismatch_commercial_url")
    return LinkLiveness(text, True, "static_ok")


def _official_fallback(city: str, state: str) -> str:
    return _OFFICIAL_FALLBACKS.get((_norm(city), _norm(state)), "")


def _renderable_sources(result: dict[str, Any], city: str, state: str, occupancy: Occupancy | None = None) -> tuple[tuple[str, ...], list[dict[str, Any]]]:
    statuses = [classify_renderable_link(url, city, state, occupancy) for url in _source_urls(result)]
    urls = [status.url for status in statuses if status.renderable]
    if not urls:
        fallback = _official_fallback(city, state)
        if fallback:
            urls = [fallback]
            statuses.append(LinkLiveness(fallback, True, "verified_ahj_landing_fallback"))
    return tuple(dict.fromkeys(urls)), [s.as_dict() for s in statuses]


def _docs_for_family(family: str, attrs: ProjectScopeAttributes) -> tuple[str, ...]:
    base = ["Project scope description", "Site address / parcel information"]
    if family == "electrical":
        docs = base + ["Electrical contractor/license information", "Electrical fixture/equipment schedule"]
        if "existing_circuits" in attrs.negative_facts:
            docs.append("Statement that work uses existing boxes/circuits with no new circuit")
        return tuple(docs)
    if family == "battery_storage":
        return tuple(base + ["Battery/ESS equipment specifications", "Electrical one-line diagram", "Manufacturer installation instructions"])
    if family == "sign":
        return tuple(base + ["Sign drawings", "Site/elevation plan", "Mounting details"])
    if family == "mechanical":
        return tuple(base + ["Equipment specifications", "Mechanical layout"])
    if family == "refrigeration":
        return tuple(base + ["Refrigerant-line / refrigeration piping details", "Equipment specifications"])
    if family == "plumbing":
        return tuple(base + ["Plumbing fixture/equipment schedule", "Plumbing layout if required"])
    if family == "gas":
        return tuple(base + ["Gas piping diagram", "Pressure test documentation"])
    if family == "health_food":
        return tuple(base + ["Food-service floor plan", "Equipment schedule", "Menu/process description"])
    if family == "wastewater_pretreatment_fog":
        return tuple(base + ["Grease interceptor sizing/details", "Wastewater pretreatment application"])
    return tuple(base)


def _inspections_for_family(family: str) -> tuple[str, ...]:
    if family == "gas":
        return ("Gas pressure test", "Final gas inspection")
    if family == "electrical":
        return ("Electrical final inspection",)
    if family == "battery_storage":
        return ("Electrical final inspection", "ESS equipment inspection if required")
    if family == "mechanical":
        return ("Mechanical final inspection",)
    if family == "refrigeration":
        return ("Refrigeration final inspection",)
    if family == "plumbing":
        return ("Plumbing final inspection",)
    return ()


def _extract_typed_fees(result: dict[str, Any], family: str | None = None) -> tuple[TypedFee, ...]:
    fee = result.get("fee_range")
    if not isinstance(fee, str) or not fee.strip():
        return ()
    text = re.sub(r"\s+", " ", fee).strip()
    typed: list[TypedFee] = []
    # Project/fabrication/install ranges are not permit fees and must not render
    # under the Fees field.
    for m in re.finditer(r"\$[\d,]+(?:\s*-\s*\$[\d,]+)?[^.;]*(?:fabrication|installation|project|construction|equipment|labor|electrical and installation)[^.;]*", text, re.I):
        typed.append(TypedFee(m.group(0).strip(), FeeType.PROJECT_COST_ESTIMATE, family))
    cleaned = re.sub(r"\s+plus\s+\$[\d,]+\s*-\s*\$[\d,]+\s+for\s+[^.;]+", "", text, flags=re.I)
    cleaned = re.sub(r"\s+plus\s+\$[\d,]+\s+for\s+[^.;]+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+—\s*verify\s+(?:in|at)\s+before quoting", "", cleaned, flags=re.I)
    cleaned = cleaned.strip(" ;,.-")
    if cleaned and re.search(r"fee|permit|plan review|\$", cleaned, re.I):
        kind = FeeType.PERMIT_FEE if re.search(r"permit|plan review|fee estimate|fee", cleaned, re.I) else FeeType.BENCHMARK_ESTIMATE
        typed.insert(0, TypedFee(cleaned, kind, family))
    return tuple(typed)


def _fee_mentions_family(text: str, family: str) -> bool:
    hay = _norm(text)
    patterns = {
        "building": r"\b(building|construction|tenant improvement|ti)\b",
        "electrical": r"\b(electrical|electric|wiring|service|panel)\b",
        "mechanical": r"\b(mechanical|hvac|rtu|furnace|air conditioner|ventilation)\b",
        "plumbing": r"\b(plumbing|fixture|water|sewer|drain)\b",
        "gas": r"\b(gas|fuel gas)\b",
        "sign": r"\b(sign|signage)\b",
        "solar_pv": r"\b(solar|pv|photovoltaic)\b",
        "battery_storage": r"\b(battery|ess|energy storage)\b",
        "health_food": r"\b(health|food)\b",
        "wastewater_pretreatment_fog": r"\b(wastewater|fog|grease|pretreatment)\b",
        "fire_suppression": r"\b(fire|suppression|sprinkler|hood)\b",
        "fire_alarm": r"\b(fire alarm|alarm)\b",
        "planning_zoning": r"\b(planning|zoning)\b",
        "co_change_of_occupancy": r"\b(certificate of occupancy|change[- ]of[- ]occupancy|\bco\b)\b",
    }
    mentioned = {fam for fam, pat in patterns.items() if re.search(pat, hay, re.I)}
    canonical = canonical_family(family)
    return len(mentioned) == 1 and canonical in mentioned


def _fees_for_family(fees: tuple[TypedFee, ...], family: str) -> tuple[TypedFee, ...]:
    return tuple(TypedFee(f.text, f.fee_type, family, f.source_url) for f in fees if _fee_mentions_family(f.text, family))


def _permit_fee_text(fees: tuple[TypedFee, ...]) -> str | None:
    parts = [f.text for f in fees if f.fee_type == FeeType.PERMIT_FEE and f.text]
    if not parts:
        return None
    return "; ".join(dict.fromkeys(parts))


def _add_item(items: dict[str, DecisionItem], item: DecisionItem) -> None:
    current = items.get(item.family)
    if current is None:
        items[item.family] = item
        return
    if current.status != DecisionStatus.REQUIRED and item.status == DecisionStatus.REQUIRED:
        items[item.family] = item


def _existing_required_names(result: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for row in result.get("permits_required") or []:
        if not isinstance(row, dict):
            continue
        fam = canonical_family(row.get("family") or row.get("filing_family"), row.get("permit_name") or row.get("permit_type"))
        nm = str(row.get("permit_name") or row.get("permit_type") or "").strip()
        if fam and nm and fam not in names:
            names[fam] = nm
    return names


def compose_decision_object(
    result: dict[str, Any],
    job_type: str,
    city: str = "",
    state: str = "",
    *,
    job_category: str | None = None,
    structured_fields: dict[str, Any] | None = None,
) -> DecisionObject:
    source_result = result if isinstance(result, dict) else {}
    attrs = extract_project_scope_attributes(job_type, city, state, job_category=job_category, structured_fields=structured_fields)
    sources, _ = _renderable_sources(source_result, city, state, attrs.occupancy)
    fees = _extract_typed_fees(source_result)
    existing_names = _existing_required_names(source_result)
    items: dict[str, DecisionItem] = {}

    def required(family: str, name: str | None = None) -> None:
        fam_fees = _fees_for_family(fees, family)
        chosen_name = name
        if not chosen_name and family == "building" and attrs.occupancy == Occupancy.COMMERCIAL and re.search(r"\b(white box|demolition|demo)\b", _norm(job_type)):
            chosen_name = "Demolition / Building Permit"
        if not chosen_name and family == "building" and attrs.occupancy == Occupancy.COMMERCIAL and re.search(r"\b(tenant improvement|buildout|upfit|first generation|shell building interior|clinic|salon|bar|brewery|lab|veterinary|vet clinic|restaurant)\b", _norm(job_type)):
            chosen_name = "Commercial Building / Tenant Improvement Permit"
        _add_item(items, DecisionItem(family=family, status=DecisionStatus.REQUIRED, permit_name=chosen_name or existing_names.get(family) or _FAMILY_NAMES.get(family, family.replace("_", " ").title()), fees=fam_fees, source_urls=sources, documents=_docs_for_family(family, attrs), inspections=_inspections_for_family(family)))

    def conditional(family: str, trigger: str, name: str | None = None) -> None:
        if family not in items:
            _add_item(items, DecisionItem(family=family, status=DecisionStatus.CONDITIONAL, permit_name=name or _FAMILY_NAMES.get(family, family.replace("_", " ").title()), trigger=trigger, source_urls=sources, documents=_docs_for_family(family, attrs), inspections=_inspections_for_family(family)))

    if "sign" in attrs.project_features:
        required("sign")
        if attrs.sign_illuminated == SignIllumination.TRUE:
            required("electrical", "Electrical Permit — Illuminated Sign")
        elif attrs.sign_illuminated == SignIllumination.UNKNOWN:
            conditional("electrical", "Only needed if the sign is illuminated/electrical or includes new wiring.", "Electrical Permit — Illuminated Sign")

    if "battery_storage" in attrs.project_features:
        required("battery_storage")
        # Battery/ESS is the family-level electrical lead; avoid a duplicate
        # generic electrical card unless additional electrical/service/PV work exists.
        if attrs.new_solar_panels == AttributeValue.TRUE or "service_panel" in attrs.positive_facts:
            required("electrical", "Electrical Permit — Solar PV / Battery System")
        if attrs.structural_mounting == AttributeValue.TRUE or attrs.roof_penetrations == AttributeValue.TRUE:
            required("building", "Building/Structural Permit — Battery/ESS Mounting")
        elif attrs.structural_mounting == AttributeValue.UNKNOWN:
            conditional("building", "Only needed if the battery installation adds structural anchorage, roof penetrations, or other building-structural work.")
    elif "electrical" in attrs.trades:
        required("electrical")

    if attrs.new_solar_panels == AttributeValue.TRUE:
        required("solar_pv")
        if attrs.roof_penetrations == AttributeValue.TRUE or attrs.structural_mounting == AttributeValue.TRUE or attrs.occupancy == Occupancy.COMMERCIAL:
            required("building", "Commercial Building / Tenant Improvement Permit" if attrs.occupancy == Occupancy.COMMERCIAL else "Building Permit — Solar PV Structural/Roof Work")

    for trade in ("mechanical", "plumbing", "fire_alarm", "fire_suppression"):
        if trade in attrs.trades:
            required(trade)
    if (
        city.strip().lower() == "seattle"
        and state.strip().upper() == "WA"
        and re.search(r"\b(?:mini[- ]split|ductless|split[- ]system|heat\s+pump|air\s+conditioner|condenser|refrigerant|line[- ]set)\b", _norm(job_type))
    ):
        required("refrigeration", "Refrigeration Permit — Split-System Heat Pump / Mini-Split")
    if "gas" in attrs.trades or "gas" in attrs.positive_facts:
        required("gas")

    if "structural" in attrs.trades and "battery_storage" not in attrs.project_features:
        required("building")

    if attrs.food_service == AttributeValue.TRUE:
        required("health_food")
        if "grease_generating" in attrs.positive_facts:
            required("wastewater_pretreatment_fog")
    elif attrs.food_service == AttributeValue.UNKNOWN:
        conditional("health_food", "Only needed if the project creates or modifies a regulated commercial food-service operation.")
        conditional("wastewater_pretreatment_fog", "Only needed if the project includes grease-generating fixtures, floor drains, or a grease interceptor.")

    if "historic" in attrs.project_features:
        required("historic", "Historic Preservation / Certificate of Appropriateness Review")

    if attrs.change_of_use == AttributeValue.TRUE:
        required("co_change_of_occupancy")
        if attrs.occupancy == Occupancy.COMMERCIAL:
            required("building", "Commercial Building / Tenant Improvement Permit")
    elif attrs.change_of_use == AttributeValue.UNKNOWN:
        conditional("co_change_of_occupancy", "Only needed when the tenant/use or occupancy classification changes.")

    job_l = _norm(job_type)
    if attrs.occupancy == Occupancy.COMMERCIAL and "building" in items and re.search(r"\b(tenant improvement|buildout|upfit|first generation|shell building interior|dental|medical|clinic|salon|bar|brewery|lab|veterinary|vet clinic)\b", job_l):
        if "electrical" not in items and "no_electrical" not in attrs.negative_facts:
            required("electrical")
        if "mechanical" not in items and "no_mechanical" not in attrs.negative_facts:
            required("mechanical")
    if attrs.food_service == AttributeValue.TRUE and re.search(r"\b(tenant improvement|buildout|restaurant|bar|brewery|commissary)\b", job_l):
        same_location_replacement = attrs.work_nature == WorkNature.LIKE_FOR_LIKE_REPLACEMENT or bool(re.search(r"\b(same location|replace(?:ment)?\b.*\bsame location|same curb|same capacity)\b", job_l))
        if "plumbing" not in items and "no_plumbing" not in attrs.negative_facts:
            required("plumbing")
        if not same_location_replacement:
            if "planning_zoning" not in items:
                required("planning_zoning")
            if "co_change_of_occupancy" not in items and not re.search(r"\b(no change of use|no change of occupancy)\b", job_l):
                required("co_change_of_occupancy")
            if "fire_suppression" not in items and re.search(r"\b(hood|suppression|bar|brewery|restaurant|cooking)\b", job_l):
                required("fire_suppression")
    if "planning_zoning" not in items and attrs.occupancy == Occupancy.COMMERCIAL and "building" in items and re.search(r"\b(storefront|exterior|facade|fa[cç]ade|sign|awning)\b", job_l):
        required("planning_zoning")
    if "co_change_of_occupancy" not in items and attrs.occupancy == Occupancy.COMMERCIAL and "building" in items and re.search(r"\b(retail\s+to|office\s+to|warehouse\s+to|change\s+of\s+(?:use|occupancy)|occupancy\s+change|convert(?:ing|ed)?\b|conversion\b|retail\s+tenant\s+improvement)\b", job_l):
        required("co_change_of_occupancy")
    # Medical/dental/veterinary tenant improvements can require building/trade
    # permits, but they are not food-establishment or generic fire-suppression
    # permits unless the request has food, hood, sprinkler, alarm, or use-change
    # facts.  Keep those families gated by the closed-world attributes above.
    if attrs.occupancy == Occupancy.COMMERCIAL and re.search(r"\b(bar|wine bar|brewery|alcohol|liquor)\b", job_l):
        required("liquor", "Liquor License / Local Alcohol Routing")
    if attrs.occupancy == Occupancy.COMMERCIAL and re.search(r"\b(daycare|child care|childcare)\b", job_l):
        required("health_food", "Health / Childcare Licensing Review")
        required("fire_suppression", "Fire / Life Safety Review")
        required("planning_zoning")
        if "co_change_of_occupancy" not in items:
            required("co_change_of_occupancy")
    if attrs.occupancy == Occupancy.COMMERCIAL and re.search(r"\b(service station|fuel dispenser|fuel system|gas station)\b", job_l):
        required("fire_suppression", "Fire / Environmental Review — Fuel System Work")

    if "plumbing" not in items and any(fam in items for fam in ("building", "electrical", "mechanical")):
        if re.search(r"\b(bedroom|tenant improvement|office|clinic|salon|laundromat|bar|restaurant|adu|bath|kitchen|restrooms?)\b", job_l):
            conditional("plumbing", "Only needed if the final scope adds, relocates, or reconnects plumbing fixtures, drains, water, sewer, or gas piping.")
    if "planning_zoning" not in items and attrs.occupancy == Occupancy.COMMERCIAL and "building" in items:
        conditional("planning_zoning", "Only needed if zoning/use clearance, address-specific planning review, exterior work, parking, signage, or use approval applies.")
    if "building" not in items and any(fam in items for fam in ("electrical", "fire_alarm", "fire_suppression", "mechanical")):
        if re.search(r"\b(panel upgrade|service upgrade|fire alarm|sprinkler|apartment building|equipment mounting|roof|rtu)\b", job_l):
            conditional("building", "Only needed if equipment mounting, penetration, fire/life-safety review, or building work is required by the AHJ.")
    if attrs.occupancy == Occupancy.COMMERCIAL and "auto repair" in job_l and "fire_suppression" not in items:
        required("fire_suppression", "Fire / Environmental Review — Auto Repair Use")

    # A plain building/TI row is only allowed from explicit structural/building
    # or change-of-use facts; there is no association-based REQUIRED fallback.
    if not items:
        conditional("building", "Only needed if the final scope includes structural, trade, exterior, occupancy, or life-safety work.")

    ordered = tuple(sorted(items.values(), key=lambda item: (_LEAD_PRIORITY.index(item.family) if item.family in _LEAD_PRIORITY else 99, item.family)))
    required_items = [item for item in ordered if item.status == DecisionStatus.REQUIRED and item.lead_eligible]
    lead = required_items[0] if required_items else None
    return DecisionObject(
        schema_version="decision_object.v1",
        attrs=attrs,
        items=ordered,
        lead_family=lead.family if lead else None,
        lead_permit_name=lead.permit_name if lead else None,
    )


def _status_rows(decision: DecisionObject, status: DecisionStatus) -> list[dict[str, Any]]:
    return [item.as_public_row() for item in decision.items if item.status == status]


def _kind_for_family(family: str | None) -> str:
    return {
        "building": "Building",
        "sign": "Sign",
        "battery_storage": "Electrical",
        "electrical": "Electrical",
        "mechanical": "Mechanical",
        "refrigeration": "Refrigeration",
        "plumbing": "Plumbing",
        "gas": "Gas",
        "solar_pv": "Solar / PV",
        "health_food": "Health",
        "wastewater_pretreatment_fog": "Wastewater/FOG",
        "fire_suppression": "Fire",
        "fire_alarm": "Fire",
    }.get(str(family or ""), "Permit")


def apply_closed_world_customer_contract(
    result: dict[str, Any],
    job_type: str,
    city: str = "",
    state: str = "",
    *,
    job_category: str | None = None,
    structured_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    public = copy.deepcopy(result) if isinstance(result, dict) else {}
    decision = compose_decision_object(public, job_type, city, state, job_category=job_category, structured_fields=structured_fields)
    required_rows = _status_rows(decision, DecisionStatus.REQUIRED)
    conditional_rows = _status_rows(decision, DecisionStatus.CONDITIONAL)
    sources, link_statuses = _renderable_sources(public, city, state, decision.attrs.occupancy)
    fees = _extract_typed_fees(public, decision.lead_family)
    permit_fee = _permit_fee_text(fees)

    public["decision_object"] = decision.as_dict()
    public["project_scope_attributes"] = decision.attrs.as_dict()
    public["permits_required"] = required_rows
    public["conditional_permits"] = conditional_rows
    public["related_permits"] = conditional_rows
    public["required_permit_names"] = [row["permit_name"] for row in required_rows]
    public["required_permit_families"] = [row["family"] for row in required_rows]
    public["source_urls"] = list(sources)
    public["sources"] = [{"url": url, "title": "Official AHJ source", "source_tier": "local_ahj"} for url in sources]
    public["link_liveness"] = link_statuses
    public["fees_typed"] = [fee.as_dict() for fee in fees]
    public["fee_range"] = permit_fee or "Permit fee not confirmed; verify the current AHJ fee schedule before quoting."

    if required_rows:
        lead_name = decision.lead_permit_name or required_rows[0]["permit_name"]
        public["permit_required"] = True
        public["permit_decision"] = "REQUIRED"
        public["permit_verdict"] = "YES"
        public["permit_name"] = lead_name
        public["permit_type"] = lead_name
        public["permit_kind"] = _kind_for_family(decision.lead_family)
        public["customer_headline"] = f"Permit required: {', '.join(public['required_permit_names'])}."
        office = public.get("applying_office") or public.get("office_name") or "the permitting office"
        public["customer_next_step"] = f"File the required permit categories with {office}: {', '.join(public['required_permit_names'])}. Confirm exact portal subcategories before final submission."
    else:
        public["permit_required"] = False
        public["permit_decision"] = "NOT_REQUIRED"
        public["permit_verdict"] = "NO"
        public["permit_name"] = "No permit required"
        public["permit_type"] = "No permit required"
        public["permit_kind"] = "Not Required"
        public["customer_headline"] = "No permit required for the described scope."

    packet_rows = []
    for row in required_rows + conditional_rows:
        packet_rows.append({
            "family": row["family"],
            "decision": row["decision"],
            "permit_name": row["permit_name"],
            "lead": row["decision"] == "REQUIRED" and canonical_family(row.get("family"), row.get("permit_name")) == decision.lead_family,
            "trigger": row.get("trigger"),
            "fees": row.get("fees", []),
            "source_urls": row.get("source_urls", []),
        })
    public["public_packet_rows"] = packet_rows
    public["public_packet"] = {"schema_version": "public_packet.v2", "rows": packet_rows}
    public["canonical_public_packet"] = public["public_packet"]

    docs = []
    inspections = []
    for row in required_rows + conditional_rows:
        docs.extend(row.get("documents") or [])
        inspections.extend(row.get("inspections") or [])
    docs = list(dict.fromkeys(str(d) for d in docs if d))
    inspections = list(dict.fromkeys(str(i) for i in inspections if i))
    public["checklist"] = docs
    public["what_to_bring"] = docs
    public["documents_to_prepare"] = docs
    public["requirements"] = docs
    public["inspections"] = inspections
    if isinstance(public.get("apply_path"), dict):
        public["apply_path"]["documents_to_prepare"] = docs
        public["apply_path"]["permit_type"] = public.get("permit_name")
        public["apply_path"]["permit_category"] = public.get("permit_kind")
        public["apply_path"]["portal_selection_path"] = [
            "Open the verified AHJ portal or department start URL",
            f"Choose the closest permit category to: {public.get('permit_name')}",
            "Prepare the listed documents before final submission",
        ]
    public["permits_required_logic"] = [
        {
            "filing_family": row.get("family"),
            "permit_type": row.get("permit_name"),
            "scope_trigger": "Project scope",
            "included_because": "The described scope includes this required permit family.",
        }
        for row in required_rows
    ]

    issues = check_render_fidelity(public)
    public["render_fidelity"] = {"pass": not issues, "issues": issues}
    return public


def check_render_fidelity(public: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    required = public.get("permits_required") or []
    conditionals = public.get("conditional_permits") or []
    packet = public.get("public_packet_rows") or []
    req_pairs = {(r.get("family"), r.get("decision") or r.get("status") or "REQUIRED") for r in required if isinstance(r, dict)}
    cond_pairs = {(r.get("family"), r.get("decision") or r.get("status") or "CONDITIONAL") for r in conditionals if isinstance(r, dict)}
    packet_pairs = {(r.get("family"), r.get("decision") or r.get("status")) for r in packet if isinstance(r, dict)}
    if req_pairs | cond_pairs != packet_pairs:
        issues.append(f"public_packet_rows mismatch expected={sorted(req_pairs|cond_pairs)} actual={sorted(packet_pairs)}")
    if required:
        lead_name = public.get("permit_name") or public.get("permit_type")
        if lead_name and not any(isinstance(r, dict) and (r.get("permit_name") or r.get("permit_type")) == lead_name for r in required):
            issues.append("lead permit_name is not mirrored by a required permit row")
        lead_rows = [r for r in packet if isinstance(r, dict) and r.get("lead")]
        if len(lead_rows) != 1:
            issues.append(f"public_packet lead row count expected=1 actual={len(lead_rows)}")
    names = [r.get("permit_name") for r in required if isinstance(r, dict)]
    if list(public.get("required_permit_names") or []) != names:
        issues.append("required_permit_names mismatch")
    families = [r.get("family") for r in required if isinstance(r, dict)]
    if list(public.get("required_permit_families") or []) != families:
        issues.append("required_permit_families mismatch")
    if len(families) != len(set(families)):
        issues.append("duplicate required family")
    for row in conditionals:
        if not isinstance(row, dict):
            continue
        trigger = str(row.get("trigger") or row.get("required_if") or "")
        if not trigger or "verify with ahj" in trigger.lower():
            issues.append(f"generic/empty conditional trigger for {row.get('family')}")
    return issues
