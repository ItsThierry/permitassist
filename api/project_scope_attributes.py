from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any


class Occupancy(str, Enum):
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class AttributeValue(str, Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class SignIllumination(str, Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"
    NA = "n/a"


class WorkNature(str, Enum):
    LIKE_FOR_LIKE_REPLACEMENT = "like_for_like_replacement"
    REPLACEMENT = "replacement"
    ALTERATION = "alteration"
    ADDITION = "addition"
    NEW_CONSTRUCTION = "new_construction"
    CHANGE_OF_USE = "change_of_use"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProjectScopeAttributes:
    """Closed-enum project-scope contract for deterministic permit composition.

    The extractor is intentionally conservative: absence of a fact is UNKNOWN unless
    the request text or segment makes a safe negative assertion. Downstream composers
    may render UNKNOWN as a concrete CONDITIONAL trigger, but must not silently drop
    a family due to unknown evidence.
    """

    occupancy: Occupancy = Occupancy.UNKNOWN
    trades: frozenset[str] = field(default_factory=frozenset)
    work_nature: WorkNature = WorkNature.UNKNOWN
    change_of_use: AttributeValue = AttributeValue.UNKNOWN
    food_service: AttributeValue = AttributeValue.UNKNOWN
    sign_illuminated: SignIllumination = SignIllumination.NA
    exterior_envelope: AttributeValue = AttributeValue.UNKNOWN
    existing_solar_context: AttributeValue = AttributeValue.UNKNOWN
    new_solar_panels: AttributeValue = AttributeValue.UNKNOWN
    roof_penetrations: AttributeValue = AttributeValue.UNKNOWN
    structural_mounting: AttributeValue = AttributeValue.UNKNOWN
    project_features: frozenset[str] = field(default_factory=frozenset)
    positive_facts: frozenset[str] = field(default_factory=frozenset)
    negative_facts: frozenset[str] = field(default_factory=frozenset)
    unknowns: tuple[str, ...] = field(default_factory=tuple)
    confidence: str = "medium"
    evidence: dict[str, list[str]] = field(default_factory=dict)
    schema_version: str = "project_scope_attributes.v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "occupancy": self.occupancy.value,
            "trades": sorted(self.trades),
            "work_nature": self.work_nature.value,
            "change_of_use": self.change_of_use.value,
            "food_service": self.food_service.value,
            "sign_illuminated": self.sign_illuminated.value,
            "exterior_envelope": self.exterior_envelope.value,
            "existing_solar_context": self.existing_solar_context.value,
            "new_solar_panels": self.new_solar_panels.value,
            "roof_penetrations": self.roof_penetrations.value,
            "structural_mounting": self.structural_mounting.value,
            "project_features": sorted(self.project_features),
            "positive_facts": sorted(self.positive_facts),
            "negative_facts": sorted(self.negative_facts),
            "unknowns": list(self.unknowns),
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _has(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, text, re.I))


def _evidence_add(evidence: dict[str, list[str]], key: str, snippet: str) -> None:
    if snippet and snippet not in evidence.setdefault(key, []):
        evidence[key].append(snippet[:160])


def _explicit_occupancy(job: str, job_category: str | None) -> Occupancy:
    category = _norm(job_category)
    if category == "residential":
        return Occupancy.RESIDENTIAL
    if category == "commercial":
        return Occupancy.COMMERCIAL
    has_res = _has(job, r"\b(residential|single[- ]family|homeowner|house|dwelling|adu|apartment|condo|townhome|rowhouse)\b")
    has_com = _has(job, r"\b(commercial|tenant|retail|office|restaurant|clinic|storefront|warehouse|laundromat|hotel|bar|brewery|daycare|salon|sign for retail)\b")
    if has_res and has_com:
        return Occupancy.MIXED
    if has_com:
        return Occupancy.COMMERCIAL
    if has_res:
        return Occupancy.RESIDENTIAL
    return Occupancy.UNKNOWN


def extract_project_scope_attributes(
    job_type: str,
    city: str = "",
    state: str = "",
    *,
    job_category: str | None = None,
    structured_fields: dict[str, Any] | None = None,
) -> ProjectScopeAttributes:
    job = _norm(job_type)
    structured_fields = structured_fields if isinstance(structured_fields, dict) else {}
    evidence: dict[str, list[str]] = {}
    trades: set[str] = set()
    features: set[str] = set()
    positives: set[str] = set()
    negatives: set[str] = set()
    unknowns: set[str] = set()

    occupancy = _explicit_occupancy(job, job_category or structured_fields.get("segment") or structured_fields.get("occupancy"))
    _evidence_add(evidence, "occupancy", job_category or structured_fields.get("segment") or "request_text")

    if _has(job, r"\b(no new circuits?|existing circuits?|existing boxes|no panel work|no service upgrade)\b"):
        negatives.add("existing_circuits")
    if _has(job, r"\bno\b[^.;,]{0,80}\b(electrical|electric|wiring|illumination|illuminated|lighting)\b|\bwithout\b[^.;,]{0,80}\b(electrical|electric|wiring|illumination|lighting)\b|\bnon[- ]electric\b|\bnot illuminated\b|\bno lighting\b"):
        negatives.add("no_electrical")
        negatives.add("no_illumination")
    if _has(job, r"\bno\b[^.;,]{0,80}\b(plumbing|pipes?|drains?|fixtures?|water|sewer)\b|\bwithout\b[^.;,]{0,80}\b(plumbing|pipes?|drains?|fixtures?|water|sewer)\b"):
        negatives.add("no_plumbing")
    if _has(job, r"\bbut\b[^.;,]{0,60}\bplumbing\b"):
        negatives.discard("no_plumbing")
    if _has(job, r"\bbut\b[^.;,]{0,60}\b(electrical|electric|wiring)\b"):
        negatives.discard("no_electrical")
        negatives.discard("no_illumination")
    if _has(job, r"\bno\b[^.;,]{0,80}\b(mechanical|hvac|ventilation|ductwork|ducts?)\b|\bwithout\b[^.;,]{0,80}\b(mechanical|hvac|ventilation|ductwork|ducts?)\b"):
        negatives.add("no_mechanical")
    if _has(job, r"\b(no|without)\s+(change of use|change of occupancy|occupancy change|use change)\b"):
        negatives.add("no_use_change")
    if _has(job, r"\b(no|without)\s+(battery|ess|energy storage)\b"):
        negatives.add("no_battery")

    change_of_use = AttributeValue.UNKNOWN
    if "no_use_change" in negatives:
        change_of_use = AttributeValue.FALSE
    elif _has(job, r"\b(change of use|change in use|change of occupancy|occupancy change|change\b.{0,40}\bto\b.{0,40}\b(?:restaurant|bar|brewery|wine bar|assembly|clinic|daycare|school)|convert(?:ing|ed)?\b|conversion\b|retail to|office to|warehouse to|b to a-2|m to a-2)\b"):
        change_of_use = AttributeValue.TRUE
        positives.add("change_of_use")
    elif not _has(job, r"\b(change|convert|conversion|occupancy)\b"):
        change_of_use = AttributeValue.FALSE
    else:
        unknowns.add("change_of_use")

    if _has(job, r"\b(like[- ]for[- ]like|same size|same opening|same location|same curb|same capacity|replace\b|replacement\b|swap\b|existing boxes|no new circuits?)\b"):
        if _has(job, r"\b(like[- ]for[- ]like|same size|same opening|same location|same curb|same capacity|no new circuits?|existing boxes)\b"):
            work_nature = WorkNature.LIKE_FOR_LIKE_REPLACEMENT
            positives.add("like_for_like_replacement")
        else:
            work_nature = WorkNature.REPLACEMENT
    elif change_of_use == AttributeValue.TRUE:
        work_nature = WorkNature.CHANGE_OF_USE
    elif _has(job, r"\b(addition|new detached|new building|new construction)\b"):
        work_nature = WorkNature.ADDITION if "addition" in job else WorkNature.NEW_CONSTRUCTION
    elif _has(job, r"\b(install|alteration|tenant improvement|buildout|renovation|remodel|relocat(?:e|ion)|demolition|demo)\b"):
        work_nature = WorkNature.ALTERATION
    else:
        work_nature = WorkNature.UNKNOWN
        unknowns.add("work_nature")

    sign_illuminated = SignIllumination.NA
    if _has(job, r"\b(sign|signage|awning|channel letters|monument)\b"):
        features.add("sign")
        positives.add("sign")
        if _has(job, r"\b(non[- ]?illuminated|non[- ]?electric|not illuminated|no illumination|no lighting|no electrical)\b"):
            sign_illuminated = SignIllumination.FALSE
        elif _has(job, r"\b(illuminated|lit|electric sign|lighting|lighted|channel letters)\b"):
            sign_illuminated = SignIllumination.TRUE
            trades.add("electrical")
            positives.add("electrical")
            positives.add("sign_illuminated")
        else:
            sign_illuminated = SignIllumination.UNKNOWN
            unknowns.add("sign_illuminated")

    # Food-service is a regulated commercial-operation attribute. A residential
    # "kitchen" word alone is not food service.
    if occupancy == Occupancy.COMMERCIAL and _has(job, r"\b(restaurant|food service|food establishment|commercial kitchen|commissary kitchen|kitchen buildout|grease|fog|grease interceptor|prep kitchen|bar kitchen|wine bar|brewery|bar|cafe|commissary|cooking equipment)\b") and not _has(job, r"\b(rooftop unit|rtu|hvac|furnace|air conditioner|condenser)\b.{0,50}\b(replacement|same curb|same tonnage)\b|\b(replacement|same curb|same tonnage)\b.{0,50}\b(rooftop unit|rtu|hvac|furnace|air conditioner|condenser)\b"):
        food_service = AttributeValue.TRUE
        positives.add("food_service")
        if _has(job, r"\b(grease|fog|interceptor|floor drains?)\b"):
            positives.add("grease_generating")
    elif _has(job, r"\b(no food service|non[- ]food|no grease|no fog|no commercial kitchen)\b") or occupancy == Occupancy.RESIDENTIAL or not _has(job, r"\b(food|restaurant|kitchen|grease|fog|interceptor|cafe|bar)\b"):
        food_service = AttributeValue.FALSE
        negatives.add("no_food_service_change")
    else:
        food_service = AttributeValue.UNKNOWN
        unknowns.add("food_service")

    if _has(job, r"\b(gfci|outlets?|receptacles?|electrical|electric|wiring|circuits?|lighting|panel|service upgrade|ev charger|generator|transfer switch|disconnect|solar|photovoltaic|pv|\d+\s*(?:a|amp)\s+service|utilities)\b") and "no_electrical" not in negatives:
        trades.add("electrical")
        positives.add("electrical")
        if _has(job, r"\b(panel|service upgrade|main panel|service panel|\d+\s*(?:a|amp)\s+service)\b"):
            positives.add("service_panel")
    if _has(job, r"\b(battery|ess|energy storage)\b") and "no_battery" not in negatives and "no_electrical" not in negatives:
        trades.add("electrical")
        positives.add("electrical")
    if _has(job, r"\b(plumbing|water heater|fixture|fixtures|sink|sinks|toilet|toilets|bath|bathroom|restroom|restrooms|shower|shower drain|tub valve|shampoo bowls?|sewer|water line|floor drains?|grease interceptor|laundry|washer|disposal|faucet|utilities)\b") and "no_plumbing" not in negatives:
        trades.add("plumbing")
        positives.add("plumbing")
    if _has(job, r"\b(gas\s+(?:line|piping|reconnection|connection|dryer|fired|water heater|furnace)|fuel\s+gas|radiant\s+heat|compressed\s+air|gas dryers?|gas equipment|gas piping|pressure test)\b"):
        trades.add("gas")
        trades.add("plumbing")
        positives.add("gas")
        positives.add("plumbing")
    if _has(job, r"\b(mechanical|hvac|rtu|rooftop unit|furnace|heat pump|mini[- ]split|air conditioner|condenser|air handler|coil|ductwork|ducts?|ventilation|exhaust|hood|makeup air|fume hood|fireplace|chimney liner|compressed air|auto repair|vehicle repair|lifts?|utilities)\b") and "no_mechanical" not in negatives:
        trades.add("mechanical")
        positives.add("mechanical")
    if _has(job, r"\b(fire alarm|alarm panel)\b"):
        trades.add("fire_alarm")
        positives.add("fire_alarm")
    if _has(job, r"\b(sprinkler|sprinklers|hood suppression|ansul|wet[- ]chemical|fire suppression|type i hood|type 1 hood)\b"):
        trades.add("fire_suppression")
        positives.add("fire_suppression")

    if _has(job, r"\b(roof|reroof|re-roof|shingles?|porch|deck|patio cover|garage|foundation|helical\s+piers?|structural\s+repair|interior\s+wall\s+relocation|wall\s+relocation|relocat(?:e|ing|ion)\s+(?:an?\s+)?(?:interior\s+)?wall|load[- ]bearing|beam|sill plate|floor joists?|retaining wall|shed|accessory structure|adu|addition|bedroom addition|basement finish|egress window|pool|spa|fence|storefront|facade|fa[cç]ade|masonry|lintel|windows?|doors?|siding|partitions?|demising wall|occupant load|tenant improvement|buildout|upfit|remodel|renovation|white box|demolition|demo|canopy|metal building|classroom addition|equipment platform|racking|anchored to slab|concrete pad|equipment slab)\b"):
        if not (_has(job, r"\b(carpet|paint|countertops?|cabinets?)\b") and _has(job, r"\b(no walls?|no wall changes|paint only|carpet only)\b")):
            trades.add("structural")
            positives.add("structural")
            features.add("building_envelope" if _has(job, r"\b(roof|window|door|siding|facade|storefront|exterior)\b") else "building_work")

    if _has(job, r"\b(historic district|historic preservation|landmark|arb|hdlc)\b"):
        features.add("historic")
        positives.add("historic")

    existing_solar_context = AttributeValue.UNKNOWN
    new_solar_panels = AttributeValue.UNKNOWN
    roof_penetrations = AttributeValue.UNKNOWN
    structural_mounting = AttributeValue.UNKNOWN
    if _has(job, r"\b(battery|battery backup|ess|energy storage)\b") and "no_battery" not in negatives:
        features.add("battery_storage")
        positives.add("battery_storage")
        trades.add("electrical")
        positives.add("electrical")
        if _has(job, r"\b(existing solar|tied to existing solar|existing pv)\b"):
            existing_solar_context = AttributeValue.TRUE
            new_solar_panels = AttributeValue.FALSE
            roof_penetrations = AttributeValue.FALSE
        if _has(job, r"\b(wall[- ]mounted|floor[- ]mounted|anchored|mounting|racking)\b"):
            structural_mounting = AttributeValue.TRUE
        else:
            structural_mounting = AttributeValue.UNKNOWN
            unknowns.add("structural_mounting")
    if _has(job, r"\b(solar|pv|photovoltaic)\b") and existing_solar_context != AttributeValue.TRUE:
        features.add("solar_pv")
        trades.add("electrical")
        positives.add("electrical")
        if _has(job, r"\b(install|new|add|system)\b.{0,60}\b(solar|pv|photovoltaic|panels?)\b|\b(solar|pv|photovoltaic)\b.{0,60}\b(install|new|add|system|panels?)\b"):
            new_solar_panels = AttributeValue.TRUE
            positives.add("solar_pv")
        else:
            new_solar_panels = AttributeValue.UNKNOWN
            unknowns.add("new_solar_panels")
        if _has(job, r"\b(roof|rooftop|racking|penetration|structural|load)\b"):
            roof_penetrations = AttributeValue.TRUE
            structural_mounting = AttributeValue.TRUE
            positives.add("structural")

    exterior_envelope = AttributeValue.UNKNOWN
    if _has(job, r"\b(exterior|facade|façade|front door|window|siding|roof|reroof|awning|sign|storefront|wall sign|monument sign)\b"):
        exterior_envelope = AttributeValue.TRUE
        positives.add("exterior")
        features.add("exterior_envelope")
    elif _has(job, r"\b(interior|inside|no exterior)\b"):
        exterior_envelope = AttributeValue.FALSE
    else:
        unknowns.add("exterior_envelope")

    if _has(job, r"\b(structural|load[- ]bearing|foundation|racking|roof penetrations?|roof load|framing|new opening|enlarged opening|beam)\b"):
        trades.add("structural")
        positives.add("structural")
    if _has(job, r"\b(roof|reroof|re-roof|shingles?|siding|windows?|doors?)\b"):
        features.add("building_envelope")

    confidence = "high" if occupancy != Occupancy.UNKNOWN and work_nature != WorkNature.UNKNOWN else "medium"
    return ProjectScopeAttributes(
        occupancy=occupancy,
        trades=frozenset(sorted(trades)),
        work_nature=work_nature,
        change_of_use=change_of_use,
        food_service=food_service,
        sign_illuminated=sign_illuminated,
        exterior_envelope=exterior_envelope,
        existing_solar_context=existing_solar_context,
        new_solar_panels=new_solar_panels,
        roof_penetrations=roof_penetrations,
        structural_mounting=structural_mounting,
        project_features=frozenset(sorted(features)),
        positive_facts=frozenset(sorted(positives)),
        negative_facts=frozenset(sorted(negatives)),
        unknowns=tuple(sorted(unknowns)),
        confidence=confidence,
        evidence=evidence,
    )
