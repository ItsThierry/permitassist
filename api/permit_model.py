"""Canonical customer-facing permit package model for PermitAssist.

This module is intentionally pure: it consumes the already-resolved customer
result dict plus request context, applies universal customer-boundary gates, and
projects a typed PermitPackage back to public fields.  It must not fetch sources,
call models, or depend on runtime state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import copy
import json
import re
from typing import Any, Iterable


class PermitFamily(StrEnum):
    BUILDING = "building"
    ELECTRICAL = "electrical"
    PLUMBING = "plumbing"
    MECHANICAL = "mechanical"
    REFRIGERATION = "refrigeration"
    SIGN = "sign"
    FIRE = "fire"
    ZONING = "zoning"
    PLANNING = "planning"
    HISTORIC = "historic"
    OCCUPANCY = "co"
    HEALTH = "health"
    GRADING = "grading"
    WASTEWATER = "wastewater"
    LIQUOR = "liquor"
    OTHER = "other"


class PermitStatus(StrEnum):
    REQUIRED = "REQUIRED"
    VERIFY = "VERIFY"
    CONDITIONAL = "CONDITIONAL"
    NOT_REQUIRED = "NOT_REQUIRED"
    RELATED = "RELATED"


@dataclass(frozen=True)
class SourceSupport:
    urls: tuple[str, ...] = ()
    jurisdiction: tuple[str, str] = ("", "")
    official_count: int = 0
    degraded: bool = False


@dataclass(frozen=True)
class PermitItem:
    family: PermitFamily
    status: PermitStatus
    name: str
    row: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    source_support: SourceSupport = field(default_factory=SourceSupport)

    @property
    def required(self) -> bool:
        return self.status == PermitStatus.REQUIRED


@dataclass(frozen=True)
class PermitPackage:
    decision: str
    required: bool
    primary_family: PermitFamily | None
    required_items: tuple[PermitItem, ...] = ()
    related_items: tuple[PermitItem, ...] = ()
    source_support: SourceSupport = field(default_factory=SourceSupport)


_FAMILY_LABELS: dict[PermitFamily, str] = {
    PermitFamily.BUILDING: "Building",
    PermitFamily.ELECTRICAL: "Electrical",
    PermitFamily.PLUMBING: "Plumbing",
    PermitFamily.MECHANICAL: "Mechanical",
    PermitFamily.REFRIGERATION: "Refrigeration",
    PermitFamily.SIGN: "Sign",
    PermitFamily.FIRE: "Fire",
    PermitFamily.ZONING: "Planning/Zoning",
    PermitFamily.PLANNING: "Planning/Zoning",
    PermitFamily.HISTORIC: "Historic/Planning",
    PermitFamily.OCCUPANCY: "Certificate of Occupancy",
    PermitFamily.HEALTH: "Health",
    PermitFamily.GRADING: "Site/Civil / ROW",
    PermitFamily.WASTEWATER: "Wastewater/FOG",
    PermitFamily.LIQUOR: "Liquor",
    PermitFamily.OTHER: "Other",
}

_FAMILY_DEFAULT_NAMES: dict[PermitFamily, str] = {
    PermitFamily.BUILDING: "Building Permit",
    PermitFamily.ELECTRICAL: "Electrical Permit",
    PermitFamily.PLUMBING: "Plumbing Permit",
    PermitFamily.MECHANICAL: "Mechanical Permit",
    PermitFamily.REFRIGERATION: "Refrigeration Permit",
    PermitFamily.SIGN: "Sign Permit",
    PermitFamily.FIRE: "Fire Permit / Fire Prevention Review",
    PermitFamily.ZONING: "Planning/Zoning Verification",
    PermitFamily.PLANNING: "Planning/Zoning Verification",
    PermitFamily.HISTORIC: "Historic Preservation Verification",
    PermitFamily.OCCUPANCY: "Certificate of Occupancy / Change-of-Occupancy Review",
    PermitFamily.HEALTH: "Health Permit / Health Plan Review",
    PermitFamily.GRADING: "Right-of-Way / Site/Civil Permit",
    PermitFamily.WASTEWATER: "Wastewater / FOG / Pretreatment Approval",
    PermitFamily.LIQUOR: "Liquor License / Alcohol Approval",
    PermitFamily.OTHER: "Permit Category Verification",
}

_FAMILY_ORDER = [
    PermitFamily.SIGN,
    PermitFamily.BUILDING,
    PermitFamily.PLUMBING,
    PermitFamily.MECHANICAL,
    PermitFamily.REFRIGERATION,
    PermitFamily.ELECTRICAL,
    PermitFamily.GRADING,
    PermitFamily.WASTEWATER,
    PermitFamily.FIRE,
    PermitFamily.HEALTH,
    PermitFamily.ZONING,
    PermitFamily.PLANNING,
    PermitFamily.HISTORIC,
    PermitFamily.OCCUPANCY,
    PermitFamily.LIQUOR,
    PermitFamily.OTHER,
]
_ORDER_INDEX = {family: idx for idx, family in enumerate(_FAMILY_ORDER)}

_SIGN_RE = re.compile(r"\b(?:sign|signage|storefront\s+sign|monument\s+sign|blade\s+sign|cabinet\s+sign|projecting\s+sign|sign\s+face|illuminated\s+cabinet)\b", re.I)
_NON_SIGN_RE = re.compile(r"\b(?:signature|signal|significant)\b", re.I)
_COSMETIC_RE = re.compile(r"\b(?:paint|repaint|carpet|carpet\s+squares?|flooring|like[- ]for[- ]like\s+flooring|ceiling\s+tiles?|wallcovering|shelving|display\s+fixtures?|furniture|cosmetic(?:-only)?|refresh)\b", re.I)
_NO_WORK_RE = re.compile(r"\bno\s+(?:walls?|construction|utility\s+changes?|mep|mechanical|electrical|plumbing|fire|life[- ]safety|occupancy(?:\s+change)?|structural|exterior|accessibility|tenant\s+improvement|ti)\b", re.I)
_HARD_TRIGGER_RE = re.compile(r"\b(?:new\s+wall|partition|structural|framing|tenant\s+improvement|\bti\b|occupancy\s+change|change\s+of\s+(?:use|occupancy)|hood|grease|sink|restroom|sprinkler|fire\s+alarm|exterior\s+(?!sign)|accessibility|utility\s+change|new\s+circuit|panel|service\s+upgrade|electrical|outlets?|receptacles?|plumbing|mechanical|hvac)\b", re.I)
_NEGATED_TRIGGER_PHRASE_RE = re.compile(r"\b(?:no\s+construction\s+or\s+utility\s+changes?|no\s+life[- ]safety\s+work|no\s+(?:construction|utility\s+changes?|walls?|mep|mechanical|electrical|plumbing|occupancy(?:\s+change)?|fire\s+work|accessibility\s+work|structural(?:\s+work)?))\b", re.I)


def _text(*values: Any) -> str:
    return " ".join(str(v or "") for v in values).strip()


def family_label(family: PermitFamily | str | None) -> str:
    fam = normalize_family(family)
    return _FAMILY_LABELS.get(fam, "Other")


def default_name(family: PermitFamily | str | None) -> str:
    fam = normalize_family(family)
    return _FAMILY_DEFAULT_NAMES.get(fam, "Permit Category Verification")


def normalize_family(value: PermitFamily | str | None, row: dict[str, Any] | None = None) -> PermitFamily:
    if isinstance(value, PermitFamily):
        return value
    raw = str(value or "").lower().strip()
    exact_aliases = {
        "building": PermitFamily.BUILDING,
        "electrical": PermitFamily.ELECTRICAL,
        "plumbing": PermitFamily.PLUMBING,
        "mechanical": PermitFamily.MECHANICAL,
        "refrigeration": PermitFamily.REFRIGERATION,
        "sign": PermitFamily.SIGN,
        "fire": PermitFamily.FIRE,
        "planning": PermitFamily.PLANNING,
        "zoning": PermitFamily.ZONING,
        "historic": PermitFamily.HISTORIC,
        "co": PermitFamily.OCCUPANCY,
        "occupancy": PermitFamily.OCCUPANCY,
        "grading": PermitFamily.GRADING,
        "wastewater": PermitFamily.WASTEWATER,
        "wastewater_pretreatment_fog": PermitFamily.WASTEWATER,
        "liquor": PermitFamily.LIQUOR,
        "health": PermitFamily.HEALTH,
    }
    if raw in exact_aliases:
        return exact_aliases[raw]
    row = row or {}
    text = _text(raw, row.get("filing_family"), row.get("family"), row.get("display_family"), row.get("kind"), row.get("category"), row.get("permit_kind"), row.get("permit_type"), row.get("permit_name"), row.get("approval_type"), row.get("portal_selection")).lower()
    checks: list[tuple[PermitFamily, tuple[str, ...]]] = [
        (PermitFamily.WASTEWATER, ("wastewater", "pretreatment", "fog", "grease interceptor")),
        (PermitFamily.REFRIGERATION, ("refrigeration",)),
        (PermitFamily.SIGN, ("sign permit", "signage", " sign", "monument sign", "blade sign", "cabinet sign", "sign face")),
        (PermitFamily.GRADING, ("right-of-way", "right of way", "row", "encroachment", "driveway", "sidewalk", "site/civil", "site civil", "grading", "drainage")),
        (PermitFamily.OCCUPANCY, ("certificate of occupancy", "change-of-occupancy", "co_change", "occupancy")),
        (PermitFamily.HISTORIC, ("historic",)),
        (PermitFamily.PLANNING, ("planning", "zoning", "land use")),
        (PermitFamily.HEALTH, ("health", "food establishment")),
        (PermitFamily.LIQUOR, ("liquor", "alcohol")),
        (PermitFamily.FIRE, ("fire", "sprinkler", "alarm")),
        (PermitFamily.PLUMBING, ("plumbing", "water heater", "sewer", "water service", "water line", "repipe", "pex")),
        (PermitFamily.MECHANICAL, ("mechanical", "hvac", "mini split", "mini-split", "ductless", "wood stove", "solid-fuel", "furnace", "heat pump")),
        (PermitFamily.ELECTRICAL, ("electrical", "circuit", "panel", "disconnect", "service upgrade", "switch", "fixture")),
        (PermitFamily.BUILDING, ("building", "tenant improvement", "garage", "deck", "window", "roof", "structural", "construction", "porch", "stairs")),
    ]
    for family, needles in checks:
        if any(needle in text for needle in needles):
            return family
    return PermitFamily.OTHER


def normalize_status(row: dict[str, Any]) -> PermitStatus:
    raw = str(row.get("status") or row.get("decision") or row.get("requirement") or "").upper().strip()
    if raw in {"CONDITIONAL_REQUIRED", "MAY_NEED", "MAY NEED"}:
        return PermitStatus.CONDITIONAL
    if raw in {"REQUIRED", "VERIFY", "CONDITIONAL", "NOT_REQUIRED", "RELATED"}:
        return PermitStatus(raw if raw != "RELATED" else "RELATED")
    return PermitStatus.REQUIRED if row.get("required") is True else PermitStatus.VERIFY


def row_name(row: dict[str, Any], family: PermitFamily | None = None) -> str:
    name = str(row.get("permit_type") or row.get("permit_name") or row.get("approval_type") or row.get("portal_selection") or "").strip()
    if not name or re.match(r"^\s*multiple permits required\s*:", name, re.I):
        return default_name(family or normalize_family(None, row))
    return name


def make_row(family: PermitFamily, name: str | None = None, status: PermitStatus = PermitStatus.REQUIRED, rationale: str = "") -> dict[str, Any]:
    label = family_label(family)
    permit_name = name or default_name(family)
    row: dict[str, Any] = {
        "permit_type": permit_name,
        "permit_name": permit_name,
        "approval_type": permit_name,
        "kind": label,
        "display_family": label,
        "filing_family": family.value,
        "status": status.value,
        "decision": status.value,
        "required": status == PermitStatus.REQUIRED,
        "rationale": rationale or f"{label} review is triggered by the described scope; confirm exact filing category with the permit office.",
    }
    if status != PermitStatus.REQUIRED:
        row["required_if"] = row["rationale"]
        row["condition_text"] = row["rationale"]
    return row


def item_from_row(row: dict[str, Any], source_support: SourceSupport) -> PermitItem:
    family = normalize_family(row.get("filing_family") or row.get("family") or row.get("kind"), row)
    status = normalize_status(row)
    name = row_name(row, family)
    normalized = copy.deepcopy(row)
    normalized["permit_type"] = name
    normalized["permit_name"] = name
    normalized.setdefault("approval_type", name)
    normalized["filing_family"] = family.value
    normalized["kind"] = family_label(family)
    normalized["display_family"] = family_label(family)
    normalized["status"] = status.value
    normalized["decision"] = status.value
    normalized["required"] = status == PermitStatus.REQUIRED
    return PermitItem(family=family, status=status, name=name, row=normalized, rationale=str(normalized.get("rationale") or ""), source_support=source_support)


def _source_urls(public: dict[str, Any]) -> tuple[str, ...]:
    urls: list[str] = []
    for key in ("source_urls",):
        for url in public.get(key) or []:
            if isinstance(url, str) and url and url not in urls:
                urls.append(url)
    for src in public.get("sources") or []:
        if isinstance(src, dict):
            url = str(src.get("url") or src.get("source_url") or "")
            if url and url not in urls:
                urls.append(url)
    for cit in public.get("claim_citations") or []:
        if isinstance(cit, dict):
            url = str(cit.get("source_url") or "")
            if url and url not in urls:
                urls.append(url)
    return tuple(urls)


def source_support_from_public(public: dict[str, Any], city: str, state: str) -> SourceSupport:
    urls = _source_urls(public)
    official_count = 0
    for url in urls:
        u = url.lower()
        if ".gov" in u or ".us" in u or "accela" in u or "city" in u or "county" in u:
            official_count += 1
    support = public.get("source_support") if isinstance(public.get("source_support"), dict) else {}
    degraded = bool(public.get("degraded_sources") or support.get("degraded_sources") or support.get("display_source_count") == 0 and support.get("primary_requirement_source_tier") == "none")
    return SourceSupport(urls=urls, jurisdiction=(str(city or ""), str(state or "").upper()), official_count=official_count, degraded=degraded)


def _is_sign_scope(scope: str) -> bool:
    return bool(_SIGN_RE.search(scope or "")) and not bool(_NON_SIGN_RE.search(scope or ""))


def _is_commercial_cosmetic_no_work(scope: str, segment: str = "") -> bool:
    scope_lc = scope or ""
    if re.search(r"\bno\s+cosmetic(?:-only)?\s+exemption\b|\bno\s+cosmetic\b", scope_lc, re.I):
        return False
    commercial = "commercial" in segment.lower() or re.search(r"\b(?:commercial|office|retail|tenant|store|lobby|shop)\b", scope_lc, re.I)
    trigger_text = _NEGATED_TRIGGER_PHRASE_RE.sub(" ", scope_lc)
    return bool(commercial and _COSMETIC_RE.search(scope_lc) and (_NO_WORK_RE.search(scope_lc) or "only" in scope_lc.lower()) and not _HARD_TRIGGER_RE.search(trigger_text))


def _has_illumination(scope: str) -> bool:
    text = scope or ""
    negated = _NEGATED_TRIGGER_PHRASE_RE.sub(" ", text)
    return bool(re.search(r"\b(?:illuminated|illumination|internal\s+illumination|led|disconnect|electrical|new\s+circuit|wired|lighting)\b", negated, re.I))


def _expected_primary_from_scope(scope: str, current: PermitFamily | None = None) -> PermitFamily | None:
    s = scope or ""
    if _is_sign_scope(s):
        return PermitFamily.SIGN
    if re.search(r"\b(?:driveway|sidewalk|apron|curb|right[- ]of[- ]way|row|encroachment)\b", s, re.I):
        return PermitFamily.GRADING
    if re.search(r"\b(?:tenant\s+improvement|\bti\b|nonbearing\s+partitions?|partition|change\s+of\s+(?:use|occupancy)|retail\s+bay\s+to|basement\s+apartment|finish\s+basement|shared\s+laundry\s+room|apartment\s+basement)\b", s, re.I):
        return PermitFamily.BUILDING
    if re.search(r"\b(?:mini[- ]?split|ductless|evaporative\s+cooler|rooftop\s+cooler)\b", s, re.I) or (re.search(r"\bhvac\b", s, re.I) and not re.search(r"\bno\s+hvac\b", s, re.I)):
        return PermitFamily.MECHANICAL
    if re.search(r"\b(?:water\s+service|water\s+line|repipe|pex|sewer|drain|water\s+heater)\b", s, re.I):
        return PermitFamily.PLUMBING
    if re.search(r"\b(?:wood\s+stove|solid[- ]fuel|fireplace\s+insert)\b", s, re.I):
        return PermitFamily.MECHANICAL
    if re.search(r"\b(?:detached\s+(?:one-car\s+)?garage|build\s+detached|porch|stairs|deck|window|roof|change\s+of\s+(?:use|occupancy)|tenant\s+improvement|retail\s+bay\s+to)\b", s, re.I):
        return PermitFamily.BUILDING
    if re.search(r"\b(?:fixture|switch|panel|circuit|ev\s+chargers?|ev\s+charger|level\s*2|level\s*ii|electrical)\b", s, re.I):
        return PermitFamily.ELECTRICAL
    return current


def _demote_item(item: PermitItem, reason: str) -> PermitItem:
    row = copy.deepcopy(item.row)
    row.update({"status": PermitStatus.VERIFY.value, "decision": PermitStatus.VERIFY.value, "required": False})
    row["required_if"] = reason
    row["condition_text"] = reason
    row["rationale"] = reason
    return PermitItem(family=item.family, status=PermitStatus.VERIFY, name=item.name, row=row, rationale=reason, source_support=item.source_support)


def _add_or_replace_required(items: list[PermitItem], family: PermitFamily, name: str | None, source_support: SourceSupport, rationale: str = "", *, first: bool = False) -> None:
    if any(item.family == family and item.required for item in items):
        return
    item = item_from_row(make_row(family, name, PermitStatus.REQUIRED, rationale), source_support)
    if first:
        items.insert(0, item)
    else:
        items.append(item)


def _force_required(items: list[PermitItem], family: PermitFamily, name: str | None, source_support: SourceSupport, rationale: str = "", *, first: bool = False) -> None:
    items[:] = [item for item in items if not (item.family == family and item.required)]
    _add_or_replace_required(items, family, name, source_support, rationale, first=first)


def _unique_items(items: Iterable[PermitItem]) -> tuple[PermitItem, ...]:
    seen: set[tuple[PermitFamily, PermitStatus, str]] = set()
    out: list[PermitItem] = []
    for item in items:
        key = (item.family, item.status, item.name.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return tuple(sorted(out, key=lambda item: (_ORDER_INDEX.get(item.family, 999), item.name.lower())))


def _wrong_jurisdiction_url(url: str, city: str, state: str) -> bool:
    u = (url or "").lower()
    state_up = (state or "").upper()
    city_lc = (city or "").lower()
    if state_up == "KS" and "kansas city" in city_lc and ("kcmo" in u or "missouri" in u or "mo.gov" in u):
        return True
    if state_up and re.search(r"\b(?:ca|tx|fl|wa|or|pa|ny|nc|mt|ks|mo)\.gov\b", u):
        # Keep only the specific known false-positive hard block for now; broad
        # state inference from domains is noisy and can reject regional portals.
        return False
    return False


def _filter_wrong_jurisdiction_sources(public: dict[str, Any], city: str, state: str) -> dict[str, Any]:
    out = copy.deepcopy(public)
    sources = []
    for src in out.get("sources") or []:
        if not isinstance(src, dict):
            continue
        url = str(src.get("url") or src.get("source_url") or "")
        if _wrong_jurisdiction_url(url, city, state):
            continue
        sources.append(src)
    if isinstance(out.get("sources"), list):
        out["sources"] = sources
    urls = [url for url in (out.get("source_urls") or []) if isinstance(url, str) and not _wrong_jurisdiction_url(url, city, state)]
    if isinstance(out.get("source_urls"), list):
        out["source_urls"] = urls
    for key in ("apply_url", "online_application_url"):
        if _wrong_jurisdiction_url(str(out.get(key) or ""), city, state):
            out[key] = ""
    if isinstance(out.get("apply_path"), dict):
        ap = copy.deepcopy(out["apply_path"])
        if _wrong_jurisdiction_url(str(ap.get("portal_url") or ap.get("url") or ""), city, state):
            ap["portal_url"] = None
            ap["state"] = "HONEST_FALLBACK"
            ap["channel"] = "contact_ahj"
            ap["support_level"] = "not available"
            ap["verification_note"] = "Wrong-jurisdiction portal was suppressed; contact the resolved local permit office for the correct filing path."
        out["apply_path"] = ap
    return out


def build_permit_package(public: dict[str, Any], job_type: str, city: str, state: str, scope_contract: dict[str, Any] | None = None) -> tuple[dict[str, Any], PermitPackage]:
    out = _filter_wrong_jurisdiction_sources(public if isinstance(public, dict) else {}, city, state)
    scope = job_type or ""
    segment = str((scope_contract or {}).get("category") or out.get("job_category") or "")
    source_support = source_support_from_public(out, city, state)
    required_items = [item_from_row(row, source_support) for row in (out.get("permits_required") or []) if isinstance(row, dict) and normalize_status(row) == PermitStatus.REQUIRED]
    related_items = [item_from_row(row, source_support) for rows_key in ("related_permits", "companion_permits", "trade_permits") for row in (out.get(rows_key) or []) if isinstance(row, dict) and normalize_status(row) != PermitStatus.REQUIRED]

    decision = str(out.get("permit_decision") or "").upper().strip()
    if decision not in {"REQUIRED", "NOT_REQUIRED"}:
        # Ambiguous/verify/unknown customer-boundary states must never become a
        # confident no-permit answer.  The undercall floor is REQUIRED with an
        # honest filing/verification row unless the canonical gates below prove
        # a no-work/cosmetic exemption.
        decision = "REQUIRED"

    cosmetic_no_work = _is_commercial_cosmetic_no_work(scope, segment) and not _is_sign_scope(scope)
    if decision == "NOT_REQUIRED" and not cosmetic_no_work:
        ambiguity_text = _text(scope, out.get("permit_name"), out.get("permit_type"), out.get("permit_kind"))
        if re.search(r"\b(?:ambiguous|verify|verification|conditional|ahj\s+verification|permit applicability)\b", ambiguity_text, re.I):
            decision = "REQUIRED"

    if cosmetic_no_work:
        reason = "Cosmetic finish-only commercial scope has no walls, MEP, fire/life-safety, occupancy, exterior, signage, or accessibility trigger in the request."
        cosmetic_demote_families = {PermitFamily.BUILDING, PermitFamily.FIRE, PermitFamily.PLUMBING, PermitFamily.OCCUPANCY, PermitFamily.ZONING, PermitFamily.PLANNING, PermitFamily.HISTORIC, PermitFamily.HEALTH}
        related_items.extend(_demote_item(item, reason) for item in required_items if item.family in cosmetic_demote_families)
        required_items = [item for item in required_items if item.family not in cosmetic_demote_families]
        if not required_items:
            package = PermitPackage("NOT_REQUIRED", False, None, (), _unique_items(related_items), source_support)
            return out, package

    primary_hint = _expected_primary_from_scope(scope, required_items[0].family if required_items else None)

    if _is_sign_scope(scope):
        reason = "The described sign scope requires a visible sign-permit filing family unless the AHJ source explicitly routes signs under another filing category."
        demote_reason = "Verify building/TI only if the sign work also changes structure, walls, façade, occupancy, or tenant-improvement scope."
        related_items.extend(_demote_item(item, demote_reason) for item in required_items if item.family in {PermitFamily.BUILDING, PermitFamily.FIRE, PermitFamily.OCCUPANCY})
        required_items = [item for item in required_items if item.family not in {PermitFamily.BUILDING, PermitFamily.FIRE, PermitFamily.OCCUPANCY}]
        _add_or_replace_required(required_items, PermitFamily.SIGN, "Sign Permit", source_support, reason, first=True)
        if _has_illumination(scope):
            _add_or_replace_required(required_items, PermitFamily.ELECTRICAL, "Electrical Permit — Illumination / Disconnect", source_support, "Electrical coordination may be required for illuminated sign/disconnect work.")
        if re.search(r"\b(?:awning|projecting|sidewalk|historic\s+district)\b", scope, re.I):
            related_items.append(item_from_row(make_row(PermitFamily.PLANNING, "Planning/Zoning Projection or Historic-District Verification", PermitStatus.VERIFY, "Verify planning, historic, encroachment, or zoning approval only if the sign projection/location triggers it."), source_support))
        if re.search(r"\b(?:awning|projecting\s+over\s+sidewalk)\b", scope, re.I):
            related_items.append(item_from_row(make_row(PermitFamily.BUILDING, "Building / Awning Attachment Verification", PermitStatus.VERIFY, "Verify building review only if awning attachment or structural support details require it."), source_support))
        primary_hint = PermitFamily.SIGN

    if primary_hint == PermitFamily.PLUMBING:
        required_items = [item for item in required_items if item.family not in {PermitFamily.BUILDING, PermitFamily.ELECTRICAL}]
        _add_or_replace_required(required_items, PermitFamily.PLUMBING, "Plumbing Permit", source_support, "The described water/plumbing scope belongs in plumbing permitting.", first=True)
    elif primary_hint == PermitFamily.GRADING:
        required_items = [item for item in required_items if item.family not in {PermitFamily.ELECTRICAL, PermitFamily.BUILDING, PermitFamily.FIRE, PermitFamily.PLUMBING}]
        _add_or_replace_required(required_items, PermitFamily.GRADING, "Right-of-Way / Driveway-Sidewalk Permit", source_support, "Driveway apron, sidewalk, curb, or right-of-way work belongs in site/civil or ROW permitting.", first=True)
    elif primary_hint == PermitFamily.MECHANICAL:
        if re.search(r"\b(?:wood\s+stove|solid[- ]fuel|fireplace\s+insert)\b", scope, re.I):
            if decision == "NOT_REQUIRED" or not required_items:
                decision = "REQUIRED"
            _add_or_replace_required(required_items, PermitFamily.MECHANICAL, "Mechanical / Solid-Fuel Appliance Permit Verification", source_support, "Solid-fuel appliance installation cannot ship as an unqualified no-permit result without source-backed exemption qualifiers.", first=True)
        elif re.search(r"\b(?:mini[- ]?split|ductless|evaporative\s+cooler|rooftop\s+cooler|hvac)\b", scope, re.I):
            related_items.extend(_demote_item(item, "Verify building/accessory-structure review only if HVAC equipment work also changes structure, occupancy, walls, or building use.") for item in required_items if item.family == PermitFamily.BUILDING)
            required_items = [item for item in required_items if item.family != PermitFamily.BUILDING]
            _add_or_replace_required(required_items, PermitFamily.MECHANICAL, "Mechanical Permit", source_support, "Mechanical/HVAC equipment installation or replacement is the primary triggered permit family.", first=True)
    elif primary_hint == PermitFamily.BUILDING:
        if re.search(r"\b(?:detached\s+(?:one-car\s+)?garage|build\s+detached)\b", scope, re.I):
            required_items = [item for item in required_items if item.family != PermitFamily.ELECTRICAL]
            _add_or_replace_required(required_items, PermitFamily.BUILDING, "Building Permit — Detached Garage", source_support, "Detached garage construction is a building/construction filing; electrical may be a companion for outlets/circuits.", first=True)
            if re.search(r"\b(?:outlets?|electrical|circuit)\b", scope, re.I):
                _add_or_replace_required(required_items, PermitFamily.ELECTRICAL, "Electrical Permit / Outlet Verification", source_support, "Electrical filing is required/verified for outlets, circuits, lighting, or service work included with the garage.")
        elif re.search(r"\b(?:basement\s+apartment|finish\s+basement|shared\s+laundry\s+room|apartment\s+basement)\b", scope, re.I):
            _add_or_replace_required(required_items, PermitFamily.BUILDING, "Building Permit", source_support, "Basement apartment/laundry-room buildout requires building review for occupancy, egress, fire/life-safety, and construction scope.", first=True)
            if re.search(r"\b(?:kitchen|bath|floor\s+drain|laundry|plumbing)\b", scope, re.I):
                _add_or_replace_required(required_items, PermitFamily.PLUMBING, "Plumbing Permit", source_support, "Kitchen, bath, floor-drain, laundry, or plumbing fixture work requires plumbing review.")
            if re.search(r"\b(?:electrical|panel|dryer|lighting|receptacle)\b", scope, re.I):
                _add_or_replace_required(required_items, PermitFamily.ELECTRICAL, "Electrical Permit", source_support, "Panel, lighting, dryer, receptacle, or circuit work requires electrical review.")
            if re.search(r"\b(?:dryer|vent|mechanical|hvac)\b", scope, re.I):
                _add_or_replace_required(required_items, PermitFamily.MECHANICAL, "Mechanical Permit / Dryer Ventilation Verification", source_support, "Dryer venting, mechanical ventilation, or HVAC work may require mechanical review.")
        elif re.search(r"\b(?:change\s+of\s+(?:use|occupancy)|retail\s+bay\s+to|tenant\s+improvement|hood|occupant\s+load)\b", scope, re.I):
            required_items = [item for item in required_items if item.family != PermitFamily.LIQUOR]
            _add_or_replace_required(required_items, PermitFamily.BUILDING, "Commercial Building / Tenant Improvement Permit", source_support, "Change-of-use, occupant-load, hood, or tenant-improvement work requires building/TI review.", first=True)
            if re.search(r"\bhood\b", scope, re.I):
                _add_or_replace_required(required_items, PermitFamily.MECHANICAL, "Mechanical Permit — Hood / Ventilation", source_support, "Commercial hood/ventilation work may require a mechanical filing.")
            if re.search(r"\bbar|cooking|occupant\s+load\b", scope, re.I):
                _add_or_replace_required(required_items, PermitFamily.FIRE, "Fire / Life Safety Review", source_support, "Occupant load, bar, or cooking changes may require fire/life-safety review.")

    if str(city or "").lower().strip() == "san francisco" and str(state or "").upper().strip() == "CA" and re.search(r"\b(?:replace|replacement).*\bwindows?\b|\bwindows?\b.*\breplace", scope, re.I):
        decision = "REQUIRED"
        _add_or_replace_required(required_items, PermitFamily.BUILDING, "Building Permit — Window Replacement", source_support, "San Francisco window replacement requires building-permit verification; planning/historic review may also apply by property or district.", first=True)
        related_items.append(item_from_row(make_row(PermitFamily.PLANNING, "Planning/Historic Verification for Window Work", PermitStatus.VERIFY, "Verify planning or historic review only if the property/district triggers it."), source_support))

    if re.search(r"\b(?:restroom|breakroom\s+sink)\b", scope, re.I):
        _add_or_replace_required(required_items, PermitFamily.PLUMBING, "Plumbing Permit", source_support, "Restroom, sink, drain, or fixture work requires plumbing review.")
        related_items.append(item_from_row(make_row(PermitFamily.ELECTRICAL, "Electrical Permit / GFCI-Fan-Lighting Verification", PermitStatus.VERIFY, "Verify electrical filing if fan, GFCI, lighting, receptacle, or circuit work is included with the restroom/breakroom scope."), source_support))
    if re.search(r"\bfire\s+alarm\b", scope, re.I):
        _add_or_replace_required(required_items, PermitFamily.FIRE, "Fire Alarm / Fire Prevention Permit", source_support, "Fire alarm scope requires fire prevention review.")
        related_items.append(item_from_row(make_row(PermitFamily.ELECTRICAL, "Electrical Permit — Alarm Circuit / Device Work", PermitStatus.VERIFY, "Verify electrical filing if alarm circuit/device wiring work is included."), source_support))
    if re.search(r"\b(?:ev\s+chargers?|level\s*2|level\s*ii)\b", scope, re.I):
        related_items.extend(_demote_item(item, "Verify building/structural review only if equipment pads, bollards, canopies, or structural supports trigger it.") for item in required_items if item.family == PermitFamily.BUILDING)
        required_items = [item for item in required_items if item.family != PermitFamily.BUILDING]
        _add_or_replace_required(required_items, PermitFamily.ELECTRICAL, "Electrical Permit — EV Charger", source_support, "EV charger installation requires electrical permitting/utility coordination.", first=True)
        if re.search(r"\b(?:parking\s+lot|transformer\s+pad|trench|pavement|site|civil)\b", scope, re.I):
            related_items.append(item_from_row(make_row(PermitFamily.GRADING, "Site/Civil / Transformer Pad Verification", PermitStatus.VERIFY, "Verify whether site/civil, pavement, drainage, transformer-pad, or utility coordination requirements apply."), source_support))
            related_items.append(item_from_row(make_row(PermitFamily.BUILDING, "Building / Structural Verification for EV Equipment", PermitStatus.VERIFY, "Verify building/structural review only if equipment pads, bollards, canopies, or structural supports trigger it."), source_support))
    if re.search(r"\b(?:replace\s+light\s+fixtures?|switches?|same\s+locations?)\b", scope, re.I):
        decision = "REQUIRED"
        required_items = [item for item in required_items if item.family not in {PermitFamily.PLUMBING, PermitFamily.BUILDING}]
        _add_or_replace_required(required_items, PermitFamily.ELECTRICAL, "Electrical Permit / Fixture-Switch Replacement Verification", source_support, "Fixture/switch work must not be mislabeled as plumbing/building; verify local electrical permit exemption before treating as no-permit.", first=True)

    if re.search(r"\b(?:alcohol\s+bar\s+service|liquor|beer\s*/?\s*wine|wine\s+shop|bar\s+service)\b", scope, re.I):
        _add_or_replace_required(required_items, PermitFamily.LIQUOR, "Liquor License / Alcohol Approval", source_support, "Alcohol or bar service requires liquor-license/local governing body routing.")

    if decision == "NOT_REQUIRED" and re.search(r"\b(?:water\s+heater|wood\s+stove|solid[- ]fuel|repipe|water\s+line|service\s+line)\b", scope, re.I):
        family = primary_hint or PermitFamily.PLUMBING
        name = "Plumbing Permit / Water Heater Verification" if family == PermitFamily.PLUMBING else default_name(family)
        _add_or_replace_required(required_items, family, name, source_support, "Permit-heavy trade scope cannot be published as NOT_REQUIRED unless a source-backed exemption applies and all qualifiers are met.", first=True)
        decision = "REQUIRED"

    if re.search(r"\b(?:water\s+heater|hpwh|heat\s+pump\s+water\s+heater)\b", scope, re.I):
        decision = "REQUIRED"
        existing_plumbing = next((item for item in required_items if item.family == PermitFamily.PLUMBING and item.required), None)
        required_items = [item for item in required_items if not (item.family == PermitFamily.PLUMBING and item.required)]
        if existing_plumbing:
            row = copy.deepcopy(existing_plumbing.row)
            row.update({
                "permit_type": "Residential Plumbing Permit — Water Heater Replacement",
                "permit_name": "Residential Plumbing Permit — Water Heater Replacement",
                "approval_type": "Residential Plumbing Permit — Water Heater Replacement",
                "filing_family": PermitFamily.PLUMBING.value,
                "kind": family_label(PermitFamily.PLUMBING),
                "display_family": family_label(PermitFamily.PLUMBING),
                "status": PermitStatus.REQUIRED.value,
                "decision": PermitStatus.REQUIRED.value,
                "required": True,
                "scope_trigger": row.get("scope_trigger") or "water_heater_replacement",
            })
            row.setdefault("rationale", "Water-heater replacement is a plumbing permit family; verify any electrical/mechanical companion filing separately.")
            required_items.insert(0, item_from_row(row, source_support))
        else:
            _add_or_replace_required(required_items, PermitFamily.PLUMBING, "Residential Plumbing Permit — Water Heater Replacement", source_support, "Water-heater replacement is a plumbing permit family; verify any electrical/mechanical companion filing separately.", first=True)
        if re.search(r"\b(?:new\s+240|240\s*volt|dedicated\s+(?:electrical\s+)?circuit|new\s+circuit|new\s+disconnect)\b", scope, re.I):
            _add_or_replace_required(required_items, PermitFamily.ELECTRICAL, "Electrical Permit — New Circuit / Equipment Connection", source_support, "Required because the request explicitly includes new electrical circuit/disconnect work for the water heater.")
        elif re.search(r"\b(?:electric|hpwh|heat\s+pump|circuit|disconnect)\b", scope, re.I):
            related_items.append(item_from_row(make_row(PermitFamily.ELECTRICAL, "Electrical Permit / Circuit-Disconnect Verification", PermitStatus.CONDITIONAL, "Conditional electrical filing: verify if circuit, disconnect, or heat-pump water-heater electrical work is included."), source_support))

    if decision == "REQUIRED" and not required_items:
        family = primary_hint or PermitFamily.BUILDING
        _add_or_replace_required(required_items, family, default_name(family), source_support, "Requirement could not be safely resolved to no-permit; verify the exact filing category with the local permit office.", first=True)

    required_items = list(_unique_items(required_items))
    related_items = list(_unique_items(related_items))
    if required_items:
        decision = "REQUIRED"
    required = decision == "REQUIRED"
    if not required:
        required_items = []
    if primary_hint and any(item.family == primary_hint and item.required for item in required_items):
        required_items.sort(key=lambda item: (0 if item.family == primary_hint else 1, _ORDER_INDEX.get(item.family, 999), item.name.lower()))
    primary_family = required_items[0].family if required_items else None
    package = PermitPackage(decision=decision, required=required, primary_family=primary_family, required_items=tuple(required_items), related_items=tuple(related_items), source_support=source_support)
    return out, package


def _project_rows(items: Iterable[PermitItem]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        row = copy.deepcopy(item.row)
        row.pop("certainty", None)
        rows.append(row)
    return rows


def _safe_existing_kind(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.search(r"\b(?:no permit|not required|multiple permits|required review|needs review|verify permit applicability)\b", text, re.I):
        return ""
    return text


def _scrub_required_no_permit_copy(value: Any, replacement: str) -> Any:
    pattern = re.compile(r"\b(?:no permit required|no permit submission needed|no permit fee expected|resolved no-permit|no-permit scope|no permit is needed|permit is not required|permit not required|a permit is not required|no permit needed)\b", re.I)
    if isinstance(value, str):
        return pattern.sub(replacement, value)
    if isinstance(value, list):
        return [_scrub_required_no_permit_copy(item, replacement) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_required_no_permit_copy(child, replacement) for key, child in value.items()}
    return value


def _package_title(package: PermitPackage) -> str:
    if not package.required_items:
        return "No permit required"
    if len(package.required_items) == 1:
        return package.required_items[0].name
    return f"Required permit package — {package.required_items[0].name}"


def project_permit_package(public: dict[str, Any], package: PermitPackage, job_type: str, city: str, state: str) -> dict[str, Any]:
    out = copy.deepcopy(public if isinstance(public, dict) else {})
    office = out.get("applying_office") or out.get("building_dept_name") or f"{city} permit office".strip() or "the local permit office"
    if not package.required:
        cosmetic_no_work = _is_commercial_cosmetic_no_work(job_type or "", str(out.get("job_category") or ""))
        headline = "No permit required for cosmetic finish work only." if cosmetic_no_work else "No permit required for the described scope."
        out.update({
            "permit_required": False,
            "permit_decision": "NOT_REQUIRED",
            "permit_verdict": "NO",
            "permit_kind": "Not Required",
            "permit_name": "No permit required",
            "permit_type": "No permit required",
            "permits_required": [],
            "permits_required_logic": [],
            "required_permit_names": [],
            "required_permit_families": [],
            "required_permit_summary": headline,
            "customer_headline": headline,
            "customer_next_step": f"Keep the scope limited to the described no-permit work; verify with {office} if the scope changes.",
            "apply_url": "",
            "online_application_url": "",
            "companion_permits": [],
        })
        out["related_permits"] = _project_rows(package.related_items)
        out["apply_path"] = {"state": "NOT_APPLICABLE", "channel": "no_permit_required", "support_level": "not applicable", "portal_url": None, "platform": None, "login_required": None, "permit_type": "No permit required", "verification_note": "No permit filing path is needed for the resolved NOT_REQUIRED scope unless the scope changes."}
        return out

    rows = _project_rows(package.required_items)
    related = _project_rows(package.related_items)
    names = [item.name for item in package.required_items]
    labels = [family_label(item.family) for item in package.required_items]
    unique_labels = list(dict.fromkeys(labels))
    title = _package_title(package)
    original_next_step = str(out.get("customer_next_step") or "")
    if len(package.required_items) == 1:
        kind = _safe_existing_kind(out.get("permit_kind")) or family_label(package.primary_family)
    elif _safe_existing_kind(out.get("permit_kind")):
        kind = _safe_existing_kind(out.get("permit_kind"))
    elif package.primary_family == PermitFamily.BUILDING and re.search(r"\b(?:tenant\s+improvement|\bti\b|office|retail|clinic|interior alteration)\b", job_type or "", re.I):
        kind = "Commercial Building / Tenant Improvement"
    else:
        primary_label = family_label(package.primary_family) if package.primary_family else "Required"
        kind = f"{primary_label} permit package"
    summary = f"Permit required: {names[0]}." if len(names) == 1 else "Required permit package: " + "; ".join(names) + "."
    out.update({
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_kind": kind,
        "permit_name": title,
        "permit_type": title,
        "permits_required": rows,
        "related_permits": related,
        "companion_permits": [row for row in out.get("companion_permits") or [] if isinstance(row, dict) and normalize_status(row) != PermitStatus.REQUIRED],
        "required_permit_names": names,
        "required_permit_families": unique_labels,
        "required_permit_summary": summary,
        "customer_headline": f"Permit required: {title}.",
        "job_summary": summary,
        "summary": summary,
        "customer_next_step": f"File the required permit categories with {office}: {', '.join(names)}. Confirm exact portal subcategories before final submission.",
    })
    out["permits_required_logic"] = [
        {
            "filing_family": item.family.value,
            "permit_type": item.name,
            "included_because": item.rationale if str(item.rationale or "").lower().startswith("official permit rule") else "Official permit rule: " + (item.rationale or f"Required because the described scope triggers {family_label(item.family)} review."),
            "scope_trigger": str(item.row.get("scope_trigger") or f"{item.family.value}_scope"),
        }
        for item in package.required_items
    ]
    ap = copy.deepcopy(out.get("apply_path") if isinstance(out.get("apply_path"), dict) else {})
    ap["permit_type"] = title
    ap["permit_category"] = kind if len(package.required_items) == 1 else "Permit package"
    no_portal = not (out.get("apply_url") or out.get("online_application_url") or ap.get("portal_url"))
    if no_portal:
        ap["state"] = ap.get("state") if ap.get("state") in {"HONEST_FALLBACK", "CONTACT_AHJ"} else "HONEST_FALLBACK"
        ap["channel"] = "contact_ahj"
        ap["support_level"] = "not available"
        ap["portal_url"] = None
        ap["verification_note"] = ap.get("verification_note") or "Exact online filing portal is unresolved; contact the listed permit office before filing."
        existing_next = original_next_step
        if re.search(r"\b(?:no exact local filing portal|no verified online filing url)\b", existing_next, re.I):
            guidance = ""
            scope_lc = (job_type or "").lower()
            if ("panel" in scope_lc or "service" in scope_lc) and "coordinate utility meter release" not in existing_next.lower():
                guidance = " Coordinate utility meter release and grounding/panel inspection requirements."
            elif "adu" in scope_lc and "adu filing packet" not in existing_next.lower():
                guidance = " Prepare the ADU filing packet before contacting the permit office."
            elif "basement" in scope_lc and "basement-finish building packet" not in existing_next.lower():
                guidance = " Prepare the basement-finish building packet before contacting the permit office."
            elif "shed" in scope_lc and "shed thresholds" not in existing_next.lower():
                guidance = " Verify shed thresholds before starting work."
            out["customer_next_step"] = existing_next + guidance
        else:
            scope_lc = (job_type or "").lower()
            guidance_bits: list[str] = []
            if "panel" in scope_lc or "service" in scope_lc:
                guidance_bits.append("Coordinate utility meter release and grounding/panel inspection requirements.")
            if "adu" in scope_lc:
                guidance_bits.append("Prepare the ADU filing packet.")
            if "basement" in scope_lc:
                guidance_bits.append("Prepare the basement-finish building packet.")
            if "shed" in scope_lc:
                guidance_bits.append("Verify shed thresholds.")
            guidance = " " + " ".join(guidance_bits) if guidance_bits else ""
            out["customer_next_step"] = f"No exact local filing portal is attached; contact {office} and file the required permit categories: {', '.join(names)}. Confirm exact portal subcategories before final submission.{guidance}"
    steps = ap.get("steps") if isinstance(ap.get("steps"), list) else []
    if steps:
        ap["steps"] = [re.sub(r"Multiple permits required:?\s*[^.;\n]+", title, str(step), flags=re.I) for step in steps]
    out["apply_path"] = ap
    replacement = f"Permit required for the resolved scope; confirm exact filing details with {office} before starting work."
    scrubbed = _scrub_required_no_permit_copy(out, replacement)
    out = scrubbed if isinstance(scrubbed, dict) else out
    out["permit_required"] = True
    out["permit_decision"] = "REQUIRED"
    out["permit_verdict"] = "YES"
    out["permit_name"] = title
    out["permit_type"] = title
    return out


def validate_customer_view(public: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    decision = str(public.get("permit_decision") or "").upper()
    rows = [row for row in public.get("permits_required") or [] if isinstance(row, dict)]
    if decision == "REQUIRED" and not rows:
        issues.append("required_without_rows")
    if decision == "NOT_REQUIRED" and rows:
        issues.append("not_required_with_required_rows")
    surface = json.dumps(public, sort_keys=True, default=str).lower()
    required_no_permit_re = re.compile(r"\b(?:no permit required|no permit submission needed|no permit fee expected|resolved no-permit|no-permit scope|no permit is needed|permit is not required|permit not required|a permit is not required|no permit needed)\b", re.I)
    if decision == "REQUIRED" and required_no_permit_re.search(surface):
        issues.append("required_no_permit_text_contradiction")
    if decision == "NOT_REQUIRED" and re.search(r"\b(?:file|submit|apply for)\b.{0,60}\b(?:required permit|permit package)\b", surface, re.I):
        issues.append("not_required_required_package_text_contradiction")
    ap = public.get("apply_path") if isinstance(public.get("apply_path"), dict) else {}
    if decision == "REQUIRED" and not (public.get("apply_url") or public.get("online_application_url") or ap.get("portal_url")):
        next_step = str(public.get("customer_next_step") or "").lower()
        if "contact" not in next_step and "no exact local filing portal" not in next_step and "no verified online filing url" not in next_step:
            issues.append("required_missing_honest_no_portal_next_step")
    collapse_re = re.compile(r"^\s*multiple permits required\s*:", re.I)
    for field in ("permit_name", "permit_type"):
        value = public.get(field)
        if isinstance(value, str) and collapse_re.search(value):
            issues.append(f"collapsed_package_{field}")
    ap = public.get("apply_path") if isinstance(public.get("apply_path"), dict) else {}
    if isinstance(ap.get("permit_type"), str) and collapse_re.search(ap["permit_type"]):
        issues.append("collapsed_package_apply_path")
    for idx, row in enumerate(rows):
        fam = normalize_family(row.get("filing_family") or row.get("family") or row.get("kind"), row)
        if normalize_status(row) != PermitStatus.REQUIRED:
            issues.append(f"required_row_not_required_{idx}")
        if not row.get("filing_family"):
            issues.append(f"row_missing_family_{idx}")
        if re.match(r"^\s*multiple permits required\s*:", str(row.get("permit_type") or row.get("permit_name") or ""), re.I):
            issues.append(f"collapsed_package_row_{idx}")
        if fam == PermitFamily.LIQUOR and "liquor" not in str(row.get("permit_type") or row.get("permit_name") or "").lower() and "alcohol" not in str(row.get("permit_type") or row.get("permit_name") or "").lower():
            issues.append(f"liquor_row_name_mismatch_{idx}")
    return issues
