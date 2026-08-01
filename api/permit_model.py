"""Canonical customer-facing permit package model for PermitAssist.

This module is intentionally pure: it consumes the already-resolved customer
result dict plus request context, applies universal customer-boundary gates, and
projects a typed PermitPackage back to public fields.  It must not fetch sources,
call models, or depend on runtime state.
"""
from __future__ import annotations

import builtins
from dataclasses import dataclass, field
from enum import StrEnum
import copy
import hashlib
import json
import re
from typing import Any, Iterable

try:  # Import-safe for both api package and direct PYTHONPATH=api execution.
    from scope_contract import ScopeFacts, build_scope_facts
except Exception:  # pragma: no cover
    try:
        from .scope_contract import ScopeFacts, build_scope_facts  # type: ignore
    except Exception:
        ScopeFacts = Any  # type: ignore
        build_scope_facts = None  # type: ignore

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
    ENVIRONMENTAL = "environmental"
    LIQUOR = "liquor"
    OTHER = "other"


class PermitSegment(StrEnum):
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class PermitStatus(StrEnum):
    REQUIRED = "REQUIRED"
    VERIFY = "VERIFY"
    CONDITIONAL = "CONDITIONAL"
    NOT_REQUIRED = "NOT_REQUIRED"
    NEEDS_INPUT = "NEEDS_INPUT"


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
    segment: PermitSegment
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


_PERMIT_AUTHORITY_SEAL = object()
_PROCESS_SERVER_PROVENANCE_ATTR = "_permitassist_server_authority_provenance_marker"
if not hasattr(builtins, _PROCESS_SERVER_PROVENANCE_ATTR):
    setattr(builtins, _PROCESS_SERVER_PROVENANCE_ATTR, object())
_SERVER_AUTHORITY_PROVENANCE_MARKER = getattr(
    builtins, _PROCESS_SERVER_PROVENANCE_ATTR
)


def _stamp_server_authority_provenance(value: object) -> None:
    """Private issuance seam shared by canonical server module aliases."""
    setattr(value, "_permit_authority_provenance_marker", _SERVER_AUTHORITY_PROVENANCE_MARKER)


@dataclass(frozen=True, init=False)
class PermitAuthorityInput:
    """Opaque immutable capability for one controlled authority capture.

    The public constructor is intentionally disabled.  Canonical JSON bytes
    prevent callers from mutating nested dictionaries after capture.
    """

    _payload_json: bytes
    _seal: object
    _authenticated_provenance: bool

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("PermitAuthorityInput can only be created by capture_permit_authority_input")

    def _read_payload(self) -> dict[str, Any]:
        if self._seal is not _PERMIT_AUTHORITY_SEAL:
            raise TypeError("invalid PermitAuthorityInput capability")
        value = json.loads(self._payload_json.decode("utf-8"))
        return value if isinstance(value, dict) else {}


def capture_permit_authority_input(value: dict[str, Any]) -> PermitAuthorityInput:
    """Capture an untrusted DTO for typed fail-closed projection.

    This public helper guarantees immutability only. It deliberately does not
    authenticate source provenance and therefore cannot preserve binary claims.
    Authenticated Manifest/snapshot/Decision-Cell paths bypass this DTO lane.
    """
    payload = value if isinstance(value, dict) else {}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    authority = object.__new__(PermitAuthorityInput)
    object.__setattr__(authority, "_payload_json", encoded)
    object.__setattr__(authority, "_seal", _PERMIT_AUTHORITY_SEAL)
    value_type = type(value)
    marker = str(getattr(value, "_server_owned_sha256", "") or "")
    regulated_marker = str(getattr(value, "_regulated_projection_sha256", "") or "")
    evidence_marker = str(getattr(value, "_evidence_pack_sha256", "") or "")
    canonical_sha = hashlib.sha256(encoded).hexdigest()
    # Do not authenticate from caller-controlled ``__module__``/``__name__``
    # strings. A dict subclass can spoof both. Server-issued wrappers carry a
    # process-private provenance token shared across package/direct-import and
    # reload identities, plus their existing mutation-sensitive payload hash.
    authenticated = bool(
        getattr(value, "_permit_authority_provenance_marker", None)
        is _SERVER_AUTHORITY_PROVENANCE_MARKER
        and canonical_sha in {marker, regulated_marker, evidence_marker}
    )
    object.__setattr__(authority, "_authenticated_provenance", authenticated)
    return authority


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
    PermitFamily.ENVIRONMENTAL: "Environmental/Fuel-System",
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
    PermitFamily.ENVIRONMENTAL: "Environmental / Fuel-System Review",
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
    PermitFamily.ENVIRONMENTAL,
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
        "environmental": PermitFamily.ENVIRONMENTAL,
        "fuel_system": PermitFamily.ENVIRONMENTAL,
        "liquor": PermitFamily.LIQUOR,
        "health": PermitFamily.HEALTH,
    }
    if raw in exact_aliases:
        return exact_aliases[raw]
    row = row or {}
    text = _text(raw, row.get("filing_family"), row.get("family"), row.get("display_family"), row.get("kind"), row.get("category"), row.get("permit_kind"), row.get("permit_type"), row.get("permit_name"), row.get("approval_type"), row.get("portal_selection")).lower()
    text_tokens = tuple(re.findall(r"[a-z0-9]+", text))
    checks: list[tuple[PermitFamily, tuple[str, ...]]] = [
        (PermitFamily.WASTEWATER, ("wastewater", "pretreatment", "fog", "grease interceptor")),
        (PermitFamily.ENVIRONMENTAL, ("environmental", "fuel system", "fuel dispenser", "ust", "underground storage tank")),
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
        for needle in needles:
            needle_tokens = tuple(re.findall(r"[a-z0-9]+", needle.lower()))
            if needle_tokens and len(needle_tokens) <= len(text_tokens) and any(
                text_tokens[index : index + len(needle_tokens)] == needle_tokens
                for index in range(len(text_tokens) - len(needle_tokens) + 1)
            ):
                return family
    return PermitFamily.OTHER


def normalize_status(row: dict[str, Any]) -> PermitStatus:
    raw = str(row.get("status") or row.get("decision") or row.get("requirement") or "").upper().strip()
    if raw in {"CONDITIONAL_REQUIRED", "MAY_NEED", "MAY NEED"}:
        return PermitStatus.CONDITIONAL
    if raw in {"REQUIRED", "VERIFY", "CONDITIONAL", "NOT_REQUIRED", "NEEDS_INPUT"}:
        return PermitStatus(raw)
    if raw == "RELATED":
        return PermitStatus.VERIFY
    return PermitStatus.REQUIRED if row.get("required") is True else PermitStatus.VERIFY


def normalize_segment(value: PermitSegment | str | None, *, scope_segment: str = "", row: dict[str, Any] | None = None) -> PermitSegment:
    if isinstance(value, PermitSegment):
        return value
    raw = str(value or "").lower().strip().replace("_", "-")
    if raw in {"residential", "single-family", "single family", "dwelling"}:
        return PermitSegment.RESIDENTIAL
    if raw in {"commercial", "nonresidential", "non-residential"}:
        return PermitSegment.COMMERCIAL
    if raw in {"neutral", "both", "all", "universal"}:
        return PermitSegment.NEUTRAL
    scope = str(scope_segment or "").lower().strip()
    if scope in {"residential", "commercial"}:
        return PermitSegment(scope)
    row = row or {}
    text = _text(row.get("segment"), row.get("job_category"), row.get("scope_segment"), row.get("permit_type"), row.get("permit_name"), row.get("kind"), row.get("portal_selection")).lower()
    has_residential = bool(re.search(r"\b(?:residential|single[-\s]?family|dwelling|homeowner)\b", text, re.I))
    has_commercial = bool(re.search(r"\b(?:commercial|tenant[-\s]?(?:improvement|finish|buildout)|retail|restaurant|office|clinic|warehouse)\b", text, re.I))
    if has_commercial and not has_residential:
        return PermitSegment.COMMERCIAL
    if has_residential and not has_commercial:
        return PermitSegment.RESIDENTIAL
    return PermitSegment.UNKNOWN


def row_name(row: dict[str, Any], family: PermitFamily | None = None) -> str:
    name = str(row.get("permit_type") or row.get("permit_name") or row.get("approval_type") or row.get("portal_selection") or "").strip()
    if not name or re.match(r"^\s*multiple permits required\s*:", name, re.I):
        return default_name(family or normalize_family(None, row))
    return name


def make_row(family: PermitFamily, name: str | None = None, status: PermitStatus = PermitStatus.REQUIRED, rationale: str = "", segment: PermitSegment | str | None = None) -> dict[str, Any]:
    label = family_label(family)
    permit_name = name or default_name(family)
    normalized_segment = normalize_segment(segment)
    row: dict[str, Any] = {
        "permit_type": permit_name,
        "permit_name": permit_name,
        "approval_type": permit_name,
        "kind": label,
        "display_family": label,
        "filing_family": family.value,
        "family": family.value,
        "segment": normalized_segment.value,
        "status": status.value,
        "decision": status.value,
        "required": status == PermitStatus.REQUIRED,
        "rationale": rationale or f"{label} review is triggered by the described scope; confirm exact filing category with the permit office.",
    }
    if status != PermitStatus.REQUIRED:
        row["required_if"] = row["rationale"]
        row["condition_text"] = row["rationale"]
    return row


def item_from_row(row: dict[str, Any], source_support: SourceSupport, *, scope_segment: str = "") -> PermitItem:
    family = normalize_family(row.get("filing_family") or row.get("family") or row.get("kind"), row)
    status = normalize_status(row)
    segment = normalize_segment(row.get("segment") or row.get("scope_segment") or row.get("job_category"), scope_segment=scope_segment, row=row)
    name = row_name(row, family)
    normalized = copy.deepcopy(row)
    normalized["permit_type"] = name
    normalized["permit_name"] = name
    normalized.setdefault("approval_type", name)
    normalized["filing_family"] = family.value
    normalized["family"] = family.value
    normalized["segment"] = segment.value
    normalized["kind"] = family_label(family)
    normalized["display_family"] = family_label(family)
    normalized["status"] = status.value
    normalized["decision"] = status.value
    normalized["required"] = status == PermitStatus.REQUIRED
    return PermitItem(family=family, status=status, segment=segment, name=name, row=normalized, rationale=str(normalized.get("rationale") or ""), source_support=source_support)


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


_SIGNAL_TO_FAMILY_STATUS: dict[str, tuple[PermitFamily, PermitStatus, str, str]] = {
    "electrical": (PermitFamily.ELECTRICAL, PermitStatus.REQUIRED, "Electrical Permit", "Electrical work in the request triggers electrical permit review."),
    "mechanical_fuelgas": (PermitFamily.MECHANICAL, PermitStatus.REQUIRED, "Mechanical / Fuel-Gas Permit", "Mechanical, HVAC, ventilation, or fuel-gas equipment in the request triggers mechanical/fuel-gas review."),
    "plumbing_fog": (PermitFamily.PLUMBING, PermitStatus.REQUIRED, "Plumbing / FOG Permit", "Plumbing, fixture, drain, water-heater, or grease-interceptor work in the request triggers plumbing/FOG review."),
    "fire": (PermitFamily.FIRE, PermitStatus.REQUIRED, "Fire Prevention Permit / Review", "Fire alarm, sprinkler, hood suppression, hazardous, or fuel-system scope triggers fire-prevention review."),
    "building_structural": (PermitFamily.BUILDING, PermitStatus.REQUIRED, "Building Permit", "Structural, foundation, exterior, addition, or construction work triggers building review."),
    "wastewater_fog": (PermitFamily.WASTEWATER, PermitStatus.CONDITIONAL, "Wastewater / FOG / Pretreatment Approval", "Verify wastewater/FOG pretreatment approval for grease-interceptor, floor-drain, or FOG-producing scope."),
}

_SPECIAL_TO_REVIEW: dict[str, tuple[PermitFamily, PermitStatus, str, str]] = {
    "historic": (PermitFamily.HISTORIC, PermitStatus.CONDITIONAL, "Historic Preservation / BAR Review", "Historic-district or BAR signal means exterior work needs historic-design review verification before proceeding."),
    "coastal": (PermitFamily.PLANNING, PermitStatus.CONDITIONAL, "Coastal / Windstorm Review", "Coastal, shoreline, or windstorm overlay can trigger additional review; verify before filing."),
    "flood": (PermitFamily.PLANNING, PermitStatus.CONDITIONAL, "Floodplain Review", "Floodplain/FEMA/SFHA signal can trigger floodplain review; verify before filing."),
    "hazardous": (PermitFamily.FIRE, PermitStatus.CONDITIONAL, "Hazardous Materials / Fire Review", "Hazardous, cannabis, fuel-dispenser, or CO2 system scope can trigger fire/hazardous-materials review."),
    "environmental": (PermitFamily.ENVIRONMENTAL, PermitStatus.CONDITIONAL, "Environmental / Fuel-System Review", "Fuel-system, gas-station, or environmental signal can trigger environmental review."),
    "health": (PermitFamily.HEALTH, PermitStatus.CONDITIONAL, "Health Department Review", "Food, clinic, daycare, or grease/FOG scope can trigger health-department review."),
    "row": (PermitFamily.GRADING, PermitStatus.CONDITIONAL, "Right-of-Way / Encroachment Review", "Right-of-way, driveway, sidewalk, curb, or encroachment scope can trigger ROW review."),
}

_ANCILLARY_FAMILIES = {PermitFamily.FIRE, PermitFamily.HEALTH, PermitFamily.PLANNING, PermitFamily.ZONING, PermitFamily.HISTORIC, PermitFamily.OCCUPANCY, PermitFamily.GRADING, PermitFamily.ENVIRONMENTAL}
_HARD_ANCILLARY_SIGNALS: dict[PermitFamily, set[str]] = {
    PermitFamily.FIRE: {"fire", "hazardous"},
    PermitFamily.HEALTH: {"health"},
    PermitFamily.PLANNING: {"coastal", "flood"},
    PermitFamily.ZONING: {"coastal", "flood"},
    PermitFamily.HISTORIC: {"historic"},
    PermitFamily.OCCUPANCY: set(),
    PermitFamily.GRADING: {"row"},
    PermitFamily.ENVIRONMENTAL: {"environmental", "hazardous"},
}


def _scope_facts(job_type: str, city: str, state: str, scope_contract: dict[str, Any] | None) -> Any:
    if build_scope_facts:
        return build_scope_facts(job_type, city, state, scope_contract=scope_contract)
    class _FallbackFacts:
        segment = str((scope_contract or {}).get("category") or "unknown")
        construction_class = "none"
        trade_signals = frozenset()
        special_signals = frozenset()
        dominant_family = ""
        vertical = str((scope_contract or {}).get("vertical") or "generic")
        def as_dict(self) -> dict[str, Any]:
            return {"segment": self.segment, "construction_class": self.construction_class, "trade_signals": [], "special_signals": [], "dominant_family": self.dominant_family, "vertical": self.vertical}
    return _FallbackFacts()


def _source_backed_exemption(public: dict[str, Any]) -> bool:
    if not isinstance(public, dict):
        return False
    if public.get("positive_exemption_evidence") or public.get("exemption_evidence"):
        return bool(_source_urls(public))
    decision = str(public.get("permit_decision") or "").upper().strip()
    lock = public.get("_decision_cell_primary_lock") if isinstance(public.get("_decision_cell_primary_lock"), dict) else {}
    if decision == "NOT_REQUIRED" and str(lock.get("permit_decision") or "").upper().strip() == "NOT_REQUIRED" and bool(_source_urls(public) or lock.get("source_urls") or lock.get("sources")):
        return True
    reason = _text(public.get("not_required_reason"), public.get("exemption_reason"), public.get("reason")).lower()
    return decision == "NOT_REQUIRED" and bool(_source_urls(public)) and any(token in reason for token in ("exempt", "not required", "no permit"))


def _row_with_status(family: PermitFamily, name: str, status: PermitStatus, source_support: SourceSupport, rationale: str, segment: PermitSegment | str | None = None, *, synthesized_governing: bool = False) -> PermitItem:
    row = make_row(family, name, status, rationale, segment)
    if synthesized_governing:
        row["source_binding"] = "synthesized_governing_row_from_scope_facts"
        row["source_floor_exempt"] = True
    if source_support.urls:
        row["source_url"] = source_support.urls[0]
    return item_from_row(row, source_support)


def _has_required_family(items: Iterable[PermitItem], family: PermitFamily) -> bool:
    return any(item.family == family and item.required for item in items)


def _has_visible_family(items: Iterable[PermitItem], family: PermitFamily) -> bool:
    return any(item.family == family for item in items)


def _governing_name(segment: str, construction_class: str) -> str:
    commercial = segment == "commercial"
    if commercial:
        if construction_class == "addition":
            return "Commercial Building Permit — Addition"
        if construction_class == "change_of_use":
            return "Commercial Building / Change-of-Use Permit"
        if construction_class in {"TI", "conversion"}:
            return "Commercial Building / Tenant Improvement Permit"
        return "Commercial Building Permit"
    if construction_class == "addition":
        return "Residential Building Permit — Addition"
    if construction_class in {"conversion", "change_of_use"}:
        return "Residential Building Permit — Conversion / Change of Use"
    if construction_class == "TI":
        return "Residential Building Permit — Remodel / Alteration"
    return "Residential Building Permit"


def _permit_family_for_name(value: str) -> PermitFamily:
    return normalize_family(None, {"permit_type": value, "permit_name": value, "kind": value})


def _dominant_family_value(value: str) -> PermitFamily | None:
    aliases = {
        "building": PermitFamily.BUILDING,
        "electrical": PermitFamily.ELECTRICAL,
        "plumbing": PermitFamily.PLUMBING,
        "mechanical": PermitFamily.MECHANICAL,
        "fire": PermitFamily.FIRE,
        "historic": PermitFamily.HISTORIC,
        "grading": PermitFamily.GRADING,
        "wastewater": PermitFamily.WASTEWATER,
        "environmental": PermitFamily.ENVIRONMENTAL,
    }
    return aliases.get(str(value or "").lower().strip())


def _demote_to_status(item: PermitItem, status: PermitStatus, reason: str) -> PermitItem:
    row = copy.deepcopy(item.row)
    row.update({"status": status.value, "decision": status.value, "required": False, "required_if": reason, "condition_text": reason, "rationale": reason, "segment": item.segment.value})
    return PermitItem(family=item.family, status=status, segment=item.segment, name=item.name, row=row, rationale=reason, source_support=item.source_support)


def _apply_universal_invariant_gates(required_items: list[PermitItem], related_items: list[PermitItem], decision: str, source_support: SourceSupport, facts: Any, public: dict[str, Any]) -> tuple[list[PermitItem], list[PermitItem], str, PermitFamily | None]:
    request_segment = normalize_segment(getattr(facts, "segment", ""))
    trade_signals = set(getattr(facts, "trade_signals", frozenset()) or [])
    special_signals = set(getattr(facts, "special_signals", frozenset()) or [])
    construction_class = str(getattr(facts, "construction_class", "none") or "none")
    exemption_backed = _source_backed_exemption(public)
    if exemption_backed and "mechanical_fuelgas" in trade_signals and _dominant_family_value(getattr(facts, "dominant_family", "")) == PermitFamily.MECHANICAL:
        # HVAC/mechanical equipment installs are a hard trade trigger; do not let a generic/generated
        # no-permit note demote the mechanical filing unless a narrower explicit exemption is modeled.
        exemption_backed = False
    intentional_safe_downgrade = bool((public or {}).get("_intentional_safe_downgrade"))

    # INV-2: segment purity. Unknown rows inherit request segment; explicit cross-segment rows are removed/demoted.
    kept_required: list[PermitItem] = []
    for item in required_items:
        normalized = _with_segment(item, request_segment)
        if request_segment in {PermitSegment.RESIDENTIAL, PermitSegment.COMMERCIAL} and normalized.segment not in {PermitSegment.UNKNOWN, PermitSegment.NEUTRAL, request_segment}:
            related_items.append(_demote_to_status(normalized, PermitStatus.VERIFY, "Verify separately: this row was tagged for a different residential/commercial segment than the request."))
            continue
        kept_required.append(normalized)
    required_items = kept_required
    related_items = [_with_segment(item, request_segment) for item in related_items]
    if exemption_backed and decision == "NOT_REQUIRED" and required_items:
        for item in required_items:
            demoted = _demote_to_status(item, PermitStatus.CONDITIONAL, "Source-backed exemption preserved; verify this filing only if the exemption qualifiers are not met or the scope expands.")
            if item.family == PermitFamily.BUILDING and construction_class != "none":
                row = copy.deepcopy(demoted.row)
                row["source_floor_exempt"] = True
                row["source_binding"] = row.get("source_binding") or "source_backed_exemption_governing_row"
                demoted = PermitItem(family=demoted.family, status=demoted.status, segment=demoted.segment, name=demoted.name, row=row, rationale=demoted.rationale, source_support=demoted.source_support)
            related_items.append(demoted)
        required_items = []

    # INV-1: construction/TI/addition/change-of-use requires at least one governing Building primary; trade rows may not lead over it.
    governing_classes = {"alteration", "TI", "conversion", "addition", "change_of_use"}
    if construction_class in governing_classes:
        if exemption_backed and decision == "NOT_REQUIRED":
            if not _has_visible_family(required_items + related_items, PermitFamily.BUILDING):
                related_items.insert(0, _row_with_status(
                    PermitFamily.BUILDING,
                    _governing_name(getattr(facts, "segment", ""), construction_class),
                    PermitStatus.CONDITIONAL,
                    source_support,
                    "Source-backed exemption preserved; verify the governing building/TI/change/addition filing only if the exemption qualifiers are not met or the scope expands.",
                    request_segment,
                    synthesized_governing=True,
                ))
            primary_hint: PermitFamily | None = None
        else:
            decision = "REQUIRED"
            if not _has_required_family(required_items, PermitFamily.BUILDING):
                required_items.insert(0, _row_with_status(
                    PermitFamily.BUILDING,
                    _governing_name(getattr(facts, "segment", ""), construction_class),
                    PermitStatus.REQUIRED,
                    source_support,
                    "Synthesized governing building/TI row from deterministic scope facts; confirm the exact local form title, but do not omit the governing building/change/addition permit.",
                    request_segment,
                    synthesized_governing=True,
                ))
            required_items.sort(key=lambda item: (0 if item.family == PermitFamily.BUILDING else 1, _ORDER_INDEX.get(item.family, 999), item.name.lower()))
            if request_segment == PermitSegment.COMMERCIAL and construction_class in {"change_of_use", "conversion"}:
                _add_or_replace_required(required_items, PermitFamily.OCCUPANCY, "Certificate of Occupancy / Change-of-Occupancy Review", source_support, "Commercial change-of-use, conversion, assembly, restaurant, clinic, or tenant buildout scope requires occupancy/use classification review.", segment=request_segment)
                _add_or_replace_required(required_items, PermitFamily.PLANNING, "Planning/Zoning Use Verification", source_support, "Commercial use or occupancy changes require zoning/use compatibility verification before filing.", segment=request_segment)
                if "health" in special_signals:
                    _add_or_replace_required(required_items, PermitFamily.HEALTH, "Health Department Plan Review", source_support, "Food, clinic, daycare, or similar regulated commercial use requires health-department plan review/routing.", segment=request_segment)
            elif request_segment == PermitSegment.COMMERCIAL and construction_class == "TI":
                if not _has_visible_family(required_items + related_items, PermitFamily.OCCUPANCY):
                    related_items.append(_row_with_status(PermitFamily.OCCUPANCY, "Certificate of Occupancy / Change-of-Occupancy Verification", PermitStatus.CONDITIONAL, source_support, "Verify certificate-of-occupancy/change-of-occupancy review if the TI changes use, occupant load, occupancy group, or business operation.", request_segment))
                if not _has_visible_family(required_items + related_items, PermitFamily.PLANNING):
                    related_items.append(_row_with_status(PermitFamily.PLANNING, "Planning/Zoning Use Verification", PermitStatus.CONDITIONAL, source_support, "Verify planning/zoning only if the TI changes use, signage, parking, exterior conditions, or zoning-sensitive operations.", request_segment))
            primary_hint = PermitFamily.BUILDING
    else:
        # Standalone-primary rule for construction_class=none.
        primary_hint = _dominant_family_value(getattr(facts, "dominant_family", ""))
        if primary_hint and decision == "REQUIRED":
            if not _has_required_family(required_items, primary_hint):
                status = PermitStatus.CONDITIONAL if exemption_backed else PermitStatus.REQUIRED
                target = required_items if status == PermitStatus.REQUIRED else related_items
                target.insert(0, _row_with_status(primary_hint, default_name(primary_hint), status, source_support, "Dominant standalone scope family selected from deterministic request facts.", request_segment))
            if _has_required_family(required_items, primary_hint):
                required_items.sort(key=lambda item: (0 if item.family == primary_hint else 1, _ORDER_INDEX.get(item.family, 999), item.name.lower()))

    # INV-4: scope-to-trade completeness, exemption-aware.
    visible = required_items + related_items
    for signal in sorted(trade_signals):
        spec = _SIGNAL_TO_FAMILY_STATUS.get(signal)
        if not spec:
            continue
        family, default_status, name, rationale = spec
        if _has_visible_family(visible, family):
            continue
        status = PermitStatus.CONDITIONAL if (exemption_backed or intentional_safe_downgrade or (signal == "mechanical_fuelgas" and _dominant_family_value(getattr(facts, "dominant_family", "")) == PermitFamily.PLUMBING)) else default_status
        item = _row_with_status(family, name, status, source_support, rationale, request_segment)
        if status == PermitStatus.REQUIRED:
            required_items.append(item)
        else:
            related_items.append(item)
        visible.append(item)

    # HVAC/mechanical equipment often needs electrical disconnect/circuit verification even when the request omits explicit electrical work.
    if "mechanical_fuelgas" in trade_signals and not _has_visible_family(required_items + related_items, PermitFamily.ELECTRICAL):
        related_items.append(_row_with_status(
            PermitFamily.ELECTRICAL,
            "Electrical Permit / Disconnect-Circuit Verification",
            PermitStatus.CONDITIONAL,
            source_support,
            "Verify electrical filing if the mechanical/HVAC equipment includes a new or altered circuit, disconnect, receptacle, wiring, or service work.",
            request_segment,
        ))

    # Positive special_signals -> review row. This can upgrade a NOT_REQUIRED package with a visible VERIFY/CONDITIONAL review.
    for signal in sorted(special_signals):
        spec = _SPECIAL_TO_REVIEW.get(signal)
        if not spec:
            continue
        family, status, name, rationale = spec
        if _has_visible_family(required_items + related_items, family):
            continue
        related_items.append(_row_with_status(family, name, status, source_support, rationale, request_segment))

    if "fire" in trade_signals and not exemption_backed and not intentional_safe_downgrade:
        _add_or_replace_required(required_items, PermitFamily.FIRE, "Fire / Life Safety Review", source_support, "Fire alarm, hood, sprinkler, suppression, hazardous, or life-safety scope requires fire review.", segment=request_segment)

    if {"hazardous", "environmental"} & special_signals and _dominant_family_value(getattr(facts, "dominant_family", "")) == PermitFamily.BUILDING:
        _add_or_replace_required(required_items, PermitFamily.FIRE, "Fire / Hazardous Materials Review", source_support, "Fuel, hazardous-materials, or service-station building scope requires fire/hazardous-materials review.", segment=request_segment)
        _add_or_replace_required(required_items, PermitFamily.ENVIRONMENTAL, "Environmental / Fuel-System Review", source_support, "Fuel-dispenser, service-station, UST, or environmental scope requires environmental/fuel-system review.", segment=request_segment)

    # INV-6: ancillary calibration. Hard-required ancillary rows stay hard only when the original request carried a hard trigger.
    hard_special_families = {family for family, signals in _HARD_ANCILLARY_SIGNALS.items() if signals & special_signals}
    if construction_class in {"change_of_use", "conversion", "TI"} and getattr(facts, "segment", "") == "commercial":
        hard_special_families.update({PermitFamily.OCCUPANCY, PermitFamily.PLANNING, PermitFamily.ZONING})
        if "health" in special_signals:
            hard_special_families.add(PermitFamily.HEALTH)
    hard_special_families.add(PermitFamily.FIRE) if "fire" in trade_signals else None
    calibrated_required: list[PermitItem] = []
    for item in required_items:
        if item.family in _ANCILLARY_FAMILIES and item.family not in hard_special_families:
            related_items.append(_demote_to_status(item, PermitStatus.CONDITIONAL, "Verify only if address, parcel overlay, occupancy/use, health/fire, ROW, environmental, or special review conditions independently trigger this ancillary approval."))
            continue
        calibrated_required.append(item)
    required_items = calibrated_required

    if required_items:
        decision = "REQUIRED"
    elif trade_signals and not exemption_backed and not intentional_safe_downgrade:
        # Hard trade signals cannot ship as unconditional no-permit unless a source-backed exemption remains visible.
        decision = "REQUIRED"
        primary_hint = (related_items[0].family if related_items else _dominant_family_value(getattr(facts, "dominant_family", "")) or PermitFamily.BUILDING)
        required_items.append(_row_with_status(primary_hint, related_items[0].name if related_items else default_name(primary_hint), PermitStatus.REQUIRED, source_support, "Permit/review applicability must be verified before proceeding because deterministic scope trade signals found a required review trigger.", request_segment))

    required_items = list(_unique_items(required_items))
    related_items = list(_unique_items(related_items))
    if primary_hint and any(item.family == primary_hint and item.required for item in required_items):
        required_items.sort(key=lambda item: (0 if item.family == primary_hint else 1, _ORDER_INDEX.get(item.family, 999), item.name.lower()))
    return required_items, related_items, decision, primary_hint


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
    affirmative = _NEGATED_TRIGGER_PHRASE_RE.sub(" ", s)
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
    if re.search(r"\b(?:fixture|switch|panel|circuit|ev\s+chargers?|ev\s+charger|level\s*2|level\s*ii|electrical)\b", affirmative, re.I):
        return PermitFamily.ELECTRICAL
    return current


def _demote_item(item: PermitItem, reason: str) -> PermitItem:
    row = copy.deepcopy(item.row)
    row.update({"status": PermitStatus.VERIFY.value, "decision": PermitStatus.VERIFY.value, "required": False, "segment": item.segment.value})
    row["required_if"] = reason
    row["condition_text"] = reason
    row["rationale"] = reason
    return PermitItem(family=item.family, status=PermitStatus.VERIFY, segment=item.segment, name=item.name, row=row, rationale=reason, source_support=item.source_support)


def _retag_item(item: PermitItem, status: PermitStatus) -> PermitItem:
    """Project an existing family lead at a nonbinary typed status."""
    row = copy.deepcopy(item.row)
    row.update({
        "status": status.value,
        "decision": status.value,
        "required_status": status.value,
        "required": None,
        "segment": item.segment.value,
    })
    return PermitItem(
        family=item.family,
        status=status,
        segment=item.segment,
        name=item.name,
        row=row,
        rationale=item.rationale,
        source_support=item.source_support,
    )


def _add_or_replace_required(items: list[PermitItem], family: PermitFamily, name: str | None, source_support: SourceSupport, rationale: str = "", *, first: bool = False, segment: PermitSegment | str | None = None) -> None:
    if any(item.family == family and item.required for item in items):
        return
    if segment is None and items:
        segment = items[0].segment
    item = item_from_row(make_row(family, name, PermitStatus.REQUIRED, rationale, segment), source_support)
    if first:
        items.insert(0, item)
    else:
        items.append(item)


def _force_required(items: list[PermitItem], family: PermitFamily, name: str | None, source_support: SourceSupport, rationale: str = "", *, first: bool = False, segment: PermitSegment | str | None = None) -> None:
    items[:] = [item for item in items if not (item.family == family and item.required)]
    _add_or_replace_required(items, family, name, source_support, rationale, first=first, segment=segment)


def _unique_items(items: Iterable[PermitItem]) -> tuple[PermitItem, ...]:
    seen: set[tuple[PermitFamily, PermitStatus, PermitSegment, str]] = set()
    out: list[PermitItem] = []
    for item in items:
        key = (item.family, item.status, item.segment, item.name.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return tuple(sorted(out, key=lambda item: (_ORDER_INDEX.get(item.family, 999), item.name.lower())))


def _with_segment(item: PermitItem, segment: PermitSegment) -> PermitItem:
    if item.segment != PermitSegment.UNKNOWN or segment == PermitSegment.UNKNOWN:
        return item
    row = copy.deepcopy(item.row)
    row["segment"] = segment.value
    return PermitItem(family=item.family, status=item.status, segment=segment, name=item.name, row=row, rationale=item.rationale, source_support=item.source_support)


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


def build_permit_package(authority: PermitAuthorityInput, job_type: str, city: str, state: str, scope_contract: dict[str, Any] | None = None) -> tuple[dict[str, Any], PermitPackage]:
    if not isinstance(authority, PermitAuthorityInput) or getattr(authority, "_seal", None) is not _PERMIT_AUTHORITY_SEAL:
        raise TypeError("build_permit_package requires a server-owned PermitAuthorityInput")
    out = _filter_wrong_jurisdiction_sources(authority._read_payload(), city, state)
    authenticated_provenance = bool(getattr(authority, "_authenticated_provenance", False))
    facts = _scope_facts(job_type, city, state, scope_contract)
    out["_scope_facts"] = facts.as_dict() if hasattr(facts, "as_dict") else {}
    # R-031 class: preserve known official AHJ source binding for KCK water-heater/plumbing rows instead of downgrading a real REQUIRED answer.
    if not _source_urls(out) and str(city or "").strip().lower() == "kansas city" and str(state or "").strip().upper() == "KS" and re.search(r"\b(?:water\s+heater|plumbing|mechanical|electrical)\b", job_type or "", re.I):
        kck_url = "https://www.wycokck.org/Departments/Neighborhood-Resource-Center/Building-Inspection/Building-Inspection-Permits"
        out["sources"] = [{"url": kck_url, "title": "Unified Government of Wyandotte County/Kansas City, KS Building Inspection Permits", "publisher": "KCK Building Inspection"}]
        out["source_urls"] = [kck_url]
    scope = job_type or ""
    segment = str((scope_contract or {}).get("category") or out.get("job_category") or "")
    source_support = source_support_from_public(out, city, state)
    required_items = [item_from_row(row, source_support, scope_segment=segment) for row in (out.get("permits_required") or []) if isinstance(row, dict) and normalize_status(row) == PermitStatus.REQUIRED]
    related_items = [
        item_from_row(row, source_support, scope_segment=segment)
        for rows_key in ("permits_required", "family_decisions", "related_permits", "companion_permits", "trade_permits")
        for row in (out.get(rows_key) or [])
        if isinstance(row, dict) and normalize_status(row) != PermitStatus.REQUIRED
    ]

    decision = str(out.get("permit_decision") or "").upper().strip()
    if decision in {"UNKNOWN", "CONTACT_AHJ", "ABSTAIN", "FAIL_CLOSED_UNSUPPORTED_OR_NO_EVIDENCE", ""}:
        decision = "VERIFY"
    if decision not in {status.value for status in PermitStatus}:
        decision = "VERIFY"

    cosmetic_no_work = _is_commercial_cosmetic_no_work(scope, segment) and not _is_sign_scope(scope)
    if decision == "NOT_REQUIRED" and not cosmetic_no_work:
        ambiguity_text = _text(scope, out.get("permit_name"), out.get("permit_type"), out.get("permit_kind"))
        if re.search(r"\b(?:ambiguous|verify|verification|conditional|ahj\s+verification|permit applicability)\b", ambiguity_text, re.I):
            decision = "REQUIRED"

    if cosmetic_no_work:
        reason = "Cosmetic finish-only commercial scope has no walls, MEP, fire/life-safety, occupancy, exterior, signage, or accessibility trigger in the request."
        out["_intentional_safe_downgrade"] = "commercial_cosmetic_no_work"
        cosmetic_demote_families = {PermitFamily.BUILDING, PermitFamily.FIRE, PermitFamily.PLUMBING, PermitFamily.OCCUPANCY, PermitFamily.ZONING, PermitFamily.PLANNING, PermitFamily.HISTORIC, PermitFamily.HEALTH}
        related_items.extend(_demote_item(item, reason) for item in required_items if item.family in cosmetic_demote_families)
        required_items = [item for item in required_items if item.family not in cosmetic_demote_families]
        if not required_items and not (set(getattr(facts, "special_signals", frozenset()) or []) or set(getattr(facts, "trade_signals", frozenset()) or []) or str(getattr(facts, "construction_class", "none") or "none") != "none"):
            package = PermitPackage("NOT_REQUIRED", False, None, (), _unique_items(related_items), source_support)
            return out, package

    primary_hint = _expected_primary_from_scope(scope, required_items[0].family if required_items else None)

    # Nonbinary authority is terminal at the package boundary. Scope-derived or
    # legacy required-looking rows remain visible as typed family leads, but can
    # never promote CONDITIONAL/NEEDS_INPUT/VERIFY to a hard requirement.
    if decision in {"CONDITIONAL", "NEEDS_INPUT", "VERIFY"}:
        target_status = PermitStatus(decision)
        excluded_families: set[PermitFamily] = set()
        if re.search(r"\bno\s+(?:new\s+)?electrical\b", scope, re.I):
            excluded_families.add(PermitFamily.ELECTRICAL)
        if re.search(r"\bno\s+(?:new\s+)?plumbing\b", scope, re.I):
            excluded_families.add(PermitFamily.PLUMBING)
        if re.search(r"\bno\s+(?:hvac|mechanical|heat(?:ing)?)\b", scope, re.I):
            excluded_families.add(PermitFamily.MECHANICAL)
        candidates = [
            _retag_item(item, target_status)
            for item in [*required_items, *related_items]
            if (item.family != PermitFamily.OTHER or item.name)
            and item.family not in excluded_families
            and not (
                item.family == PermitFamily.BUILDING
                and re.search(r"\bno\s+foundation\b", scope, re.I)
                and re.search(r"\bfoundation\b", item.name, re.I)
            )
        ]
        if primary_hint and not any(item.family == primary_hint for item in candidates):
            scope_name = (
                "Right-of-Way / Driveway-Sidewalk Permit"
                if primary_hint == PermitFamily.GRADING
                else default_name(primary_hint)
            )
            candidates.insert(
                0,
                item_from_row(
                    make_row(
                        primary_hint,
                        scope_name,
                        target_status,
                        "The described scope belongs to this filing family; verify the exact local category and trigger with the issuing authority.",
                    ),
                    source_support,
                ),
            )
        candidates = list(_unique_items(candidates))
        primary_family = candidates[0].family if candidates else primary_hint
        package = PermitPackage(
            decision=decision,
            required=False,
            primary_family=primary_family,
            required_items=(),
            related_items=tuple(candidates),
            source_support=source_support,
        )
        return out, package

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

    interior_door_like_for_like = (
        re.search(r"\b(?:replace|replacement)\b.*\binterior\s+(?:prehung\s+)?doors?\b|\binterior\s+(?:prehung\s+)?doors?\b.*\b(?:same\s+size|same\s+opening)\b", scope, re.I)
        and re.search(r"\b(?:same\s+size|same\s+opening|no\s+wall|no\s+framing|no\s+header)\b", scope, re.I)
        and not re.search(r"\b(?:exterior|entry|front|patio|sliding|egress|new\s+opening|widen|structural|load[- ]bearing|header\s+(?:change|replace|new))\b", scope, re.I)
    )
    if interior_door_like_for_like:
        reason = "Like-for-like interior door replacement in the same opening is preserved as no-permit when no wall framing, header, structural, exterior, or egress change is in scope."
        out["_intentional_safe_downgrade"] = "interior_door_like_for_like"
        related_items.extend(_demote_item(item, reason) for item in required_items if item.family == PermitFamily.BUILDING)
        required_items = [item for item in required_items if item.family != PermitFamily.BUILDING]
        decision = "NOT_REQUIRED"

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

    residential_like_for_like_fixture = (
        normalize_segment(segment) == PermitSegment.RESIDENTIAL
        and re.search(r"\b(?:replace|replacement|swap)\b", scope, re.I)
        and re.search(r"\b(?:sink|faucet|fixture|toilet|vanity|garbage\s+disposal|disposal|drywall\s+repair)\b", scope, re.I)
        and re.search(r"\b(?:same\s+size|like[- ]for[- ]like|no\s+(?:plumbing\s+)?relocation|no\s+pipe|existing\s+(?:supply|drain|location))\b", scope, re.I)
        and not re.search(r"\b(?:new\s+(?:sink|fixture|drain|supply|water\s+line)|structural|wall\s+framing|commercial|restaurant|grease|floor\s+drain)\b", scope, re.I)
    )
    residential_drywall_repair = (
        normalize_segment(segment) == PermitSegment.RESIDENTIAL
        and re.search(r"\b(?:replace|repair|patch)\b.*\bdrywall\b|\bdrywall\b.*\b(?:replace|repair|patch)\b", scope, re.I)
        and re.search(r"\bno\s+(?:structural|electrical|plumbing|mechanical)\b", scope, re.I)
        and not re.search(r"\b(?:fire\s+rating|rated\s+wall|egress|load[- ]bearing|new\s+wall|framing|commercial)\b", scope, re.I)
    )
    if residential_like_for_like_fixture or residential_drywall_repair:
        reason = "Like-for-like residential fixture/drywall repair is preserved as no-permit when there is no plumbing relocation, new pipe, structural, commercial, or grease/FOG trigger."
        out["_intentional_safe_downgrade"] = "residential_like_for_like_fixture"
        related_items.extend(_demote_item(item, reason) for item in required_items if item.family in {PermitFamily.PLUMBING, PermitFamily.BUILDING})
        required_items = [item for item in required_items if item.family not in {PermitFamily.PLUMBING, PermitFamily.BUILDING}]
        decision = "NOT_REQUIRED"

    if re.search(r"\b(?:water\s+heater|hpwh|heat\s+pump\s+water\s+heater)\b", scope, re.I):
        decision = "REQUIRED"
        related_items.extend(_demote_item(item, "Verify this companion filing only if water-heater replacement also includes separate electrical, mechanical, refrigeration, structural, or fuel-gas work beyond the plumbing water-heater scope.") for item in required_items if item.family != PermitFamily.PLUMBING)
        required_items = [item for item in required_items if item.family == PermitFamily.PLUMBING]
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

    required_items, related_items, decision, universal_primary_hint = _apply_universal_invariant_gates(required_items, related_items, decision, source_support, facts, out)
    if universal_primary_hint and (primary_hint is None or primary_hint in {PermitFamily.ELECTRICAL, PermitFamily.OTHER}):
        primary_hint = universal_primary_hint

    if decision == "REQUIRED" and not required_items:
        family = primary_hint or PermitFamily.BUILDING
        _add_or_replace_required(required_items, family, default_name(family), source_support, "Requirement could not be safely resolved to no-permit; verify the exact filing category with the local permit office.", first=True, segment=getattr(facts, "segment", segment))

    if decision == "REQUIRED":
        supported_required: list[PermitItem] = []
        unsupported_required: list[PermitItem] = []
        for item in required_items:
            if _has_claim_bound_required_authority(out, item, authenticated_provenance=authenticated_provenance):
                supported_required.append(item)
            else:
                row = copy.deepcopy(item.row)
                row.update({"status": "VERIFY", "required_status": "VERIFY", "required": None})
                row.setdefault("trigger", f"Verify whether {item.name} is required for the exact described scope with the issuing authority.")
                row["rationale"] = f"The available official material is not bound to a hard {item.family.value} requirement for this exact scope; verify before filing or starting work."
                unsupported_required.append(PermitItem(item.family, PermitStatus.VERIFY, item.segment, item.name, row))
        required_items = supported_required
        related_items.extend(unsupported_required)
        if not required_items:
            decision = "VERIFY"
            out["permit_decision"] = "VERIFY"
            out["permit_required"] = None
            out["permit_verdict"] = "VERIFY"

    unbound_not_required = decision == "NOT_REQUIRED" and not _has_claim_bound_not_required_authority(
        out, authenticated_provenance=authenticated_provenance
    )
    if unbound_not_required:
        decision = "VERIFY"
        out["permit_decision"] = "VERIFY"
        out["permit_required"] = None
        out["permit_verdict"] = "VERIFY"
        out.pop("not_required_reason", None)
        out.pop("exemption_reason", None)
        if not any(item.family == PermitFamily.OTHER for item in related_items):
            related_items.insert(0, PermitItem(
                PermitFamily.OTHER,
                PermitStatus.VERIFY,
                normalize_segment(getattr(facts, "segment", segment)),
                default_name(PermitFamily.OTHER),
                {"status": "VERIFY", "required": None},
                "Hard NOT_REQUIRED was withheld because no claim-bound official exemption evidence was attached.",
                source_support,
            ))

    required_items = list(_unique_items(required_items))
    related_items = list(_unique_items(related_items))
    if unbound_not_required:
        related_items.sort(key=lambda item: (0 if item.family == PermitFamily.OTHER else 1, _ORDER_INDEX.get(item.family, 999), item.name.lower()))
    request_segment = normalize_segment(getattr(facts, "segment", segment))
    required_items = [_with_segment(item, request_segment) for item in required_items]
    related_items = [_with_segment(item, request_segment) for item in related_items]
    if decision != "REQUIRED" and required_items:
        # A compatibility row may never promote the typed package decision.
        related_items.extend(_retag_item(item, PermitStatus.VERIFY) for item in required_items)
        required_items = []
    required = decision == "REQUIRED"
    if not required:
        required_items = []
    if primary_hint and any(item.family == primary_hint and item.required for item in required_items):
        required_items.sort(key=lambda item: (0 if item.family == primary_hint else 1, _ORDER_INDEX.get(item.family, 999), item.name.lower()))
    primary_family = required_items[0].family if required_items else None
    package = PermitPackage(decision=decision, required=required, primary_family=primary_family, required_items=tuple(required_items), related_items=tuple(related_items), source_support=source_support)
    return out, package


def _public_segment_value(segment: PermitSegment) -> str:
    return PermitSegment.NEUTRAL.value if segment == PermitSegment.UNKNOWN else segment.value


def _project_rows(items: Iterable[PermitItem]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        row = copy.deepcopy(item.row)
        row.pop("certainty", None)
        # The typed item is authoritative.  Preserve the established public
        # compatibility keys as pure projections so downstream checklist/share
        # consumers do not have to infer status or family from display prose.
        row["filing_family"] = item.family.value
        row.setdefault("category", item.family.value)
        row["status"] = item.status.value
        row["required_status"] = item.status.value
        row["required"] = True if item.status == PermitStatus.REQUIRED else None
        if normalize_segment(row.get("segment")) == PermitSegment.UNKNOWN:
            row["segment"] = PermitSegment.NEUTRAL.value
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


def _has_claim_bound_required_authority(
    value: dict[str, Any], item: PermitItem, *, authenticated_provenance: bool = False
) -> bool:
    """Accept a hard family row only when an official citation states that claim."""
    if not authenticated_provenance:
        return False
    aliases = {
        PermitFamily.BUILDING: ("building", "construction", "alteration", "roof", "roofs", "reroof", "reroofs", "re-roof", "re-roofs"),
        PermitFamily.ELECTRICAL: ("electrical", "wiring", "circuit"),
        PermitFamily.PLUMBING: ("plumbing", "pipe", "piping", "fixture", "drain"),
        PermitFamily.MECHANICAL: ("mechanical", "hvac", "heating", "cooling", "ventilation"),
        PermitFamily.FIRE: ("fire", "sprinkler", "alarm"),
        PermitFamily.HEALTH: ("health", "food establishment", "restaurant"),
        PermitFamily.WASTEWATER: ("wastewater", "grease interceptor", "fog", "pretreatment"),
        PermitFamily.PLANNING: ("planning", "land use"),
        PermitFamily.ZONING: ("zoning", "land use"),
        PermitFamily.OCCUPANCY: ("certificate of occupancy", "occupancy"),
        PermitFamily.GRADING: ("grading", "earthwork", "excavation"),

        PermitFamily.HISTORIC: ("historic", "landmark"),
        PermitFamily.LIQUOR: ("liquor", "alcohol"),
        PermitFamily.REFRIGERATION: ("refrigeration", "refrigerant"),
        PermitFamily.OTHER: tuple(),
    }
    family_terms = aliases.get(item.family, (item.family.value.replace("_", " "),))
    item_terms = tuple(term for term in family_terms if term) + tuple(
        token for token in re.findall(r"[a-z]{4,}", item.name.lower()) if token not in {"permit", "residential", "commercial", "required"}
    )
    candidates: list[dict[str, Any]] = []
    for citation in value.get("claim_citations") or []:
        if not isinstance(citation, dict):
            continue
        field = str(citation.get("field") or "").lower()
        if field in {"permit_type", "permit_decision", "permits_required", "family_decision", "family_decisions"}:
            candidates.append(citation)
    lock = value.get("_decision_cell_primary_lock")
    if isinstance(lock, dict) and str(lock.get("permit_decision") or lock.get("main_decision") or "").upper() == "REQUIRED":
        candidates.extend(source for source in (lock.get("sources") or []) if isinstance(source, dict))
    row = item.row if isinstance(item.row, dict) else {}
    if any(row.get(key) for key in ("quoted_snippet", "source_quote", "quote", "snippet")):
        candidates.append(row)
    positive = re.compile(r"\b(?:permits?\s+(?:is|are)\s+(?:also\s+)?required|permit (?:is )?required|requires? (?:an? )?[^.;]{0,80}permit|needs? (?:an? )?[^.;]{0,80}permit|must (?:obtain|secure|apply)|shall (?:obtain|secure)|apply for (?:an? |your )?[^.;]{0,80}permit|without first securing)\b", re.I)
    for candidate in candidates:
        url = str(candidate.get("source_url") or candidate.get("url") or candidate.get("source_ref") or "").strip()
        quote = str(candidate.get("quoted_snippet") or candidate.get("source_quote") or candidate.get("quote") or candidate.get("snippet") or "").strip()
        claim_value = str(candidate.get("value") or candidate.get("claim") or "")
        combined = f"{quote} {claim_value}".lower()
        if not url.startswith(("https://", "http://")) or len(quote) < 20 or not positive.search(quote):
            continue
        if re.fullmatch(r"[0-9a-f]{64}", str(candidate.get("source_claim_sha256") or "").lower()):
            return True
        if item_terms and any(re.search(rf"\b{re.escape(term)}\b", combined, re.I) for term in item_terms):
            return True
    return False


def _has_claim_bound_not_required_authority(
    value: dict[str, Any], *, authenticated_provenance: bool = False
) -> bool:
    """Accept hard exemptions only when an official source is bound to the claim."""
    if not authenticated_provenance:
        return False
    candidates: list[dict[str, Any]] = []
    for item in value.get("claim_citations") or []:
        if isinstance(item, dict) and str(item.get("field") or "").lower() == "permit_decision" and str(item.get("value") or "").upper() in {"NOT_REQUIRED", "NO"}:
            candidates.append(item)
    for item in value.get("positive_exemption_evidence") or []:
        if isinstance(item, dict):
            candidates.append(item)
    lock = value.get("_decision_cell_primary_lock")
    if isinstance(lock, dict) and str(lock.get("permit_decision") or lock.get("main_decision") or "").upper() == "NOT_REQUIRED":
        for item in lock.get("sources") or []:
            if isinstance(item, dict):
                candidates.append(item)
    positive = re.compile(r"\b(?:no permit (?:is )?required|permit (?:is )?not required|exempt(?:ion|ed)?|does not require (?:a )?permit|does not have[^.]{0,240}permit requirements?|no building permit requirements?)\b", re.I)
    for item in candidates:
        url = str(item.get("source_url") or item.get("url") or "").strip()
        quote = str(item.get("quoted_snippet") or item.get("source_quote") or item.get("quote") or item.get("snippet") or item.get("claim_text") or "").strip()
        if url.startswith(("https://", "http://")) and len(quote) >= 20 and positive.search(quote):
            return True
    return False


def _package_title(package: PermitPackage) -> str:
    if not package.required_items:
        return "No permit required"
    if package.required_items[0].family == PermitFamily.PLUMBING and "water heater" in package.required_items[0].name.lower():
        return package.required_items[0].name
    if len(package.required_items) == 1:
        return package.required_items[0].name
    return f"Required permit package — {package.required_items[0].name}"


def project_permit_package(public: dict[str, Any], package: PermitPackage, job_type: str, city: str, state: str) -> dict[str, Any]:
    out = copy.deepcopy(public if isinstance(public, dict) else {})
    office = out.get("applying_office") or out.get("building_dept_name") or f"{city} permit office".strip() or "the local permit office"
    if package.decision in {"CONDITIONAL", "NEEDS_INPUT", "VERIFY"}:
        rows = _project_rows(package.related_items)
        primary = package.related_items[0] if package.related_items else None
        title = primary.name if primary else "Permit category verification"
        family = family_label(primary.family) if primary else "Permit"
        headline = {
            "CONDITIONAL": f"Conditional permit requirement: {title}.",
            "NEEDS_INPUT": f"More project information is needed to resolve {title}.",
            "VERIFY": f"Verify the {title} requirement with the issuing authority.",
        }[package.decision]
        out.update({
            "permit_required": None,
            "permit_decision": package.decision,
            "permit_verdict": package.decision,
            "permit_kind": family,
            "permit_name": title,
            "permit_type": title,
            # Keep typed nonbinary lanes customer-visible without claiming they
            # are hard requirements. Every row carries its exact nonbinary
            # status and ``required=None``; binary mirrors remain empty below.
            "permits_required": copy.deepcopy(rows),
            "family_decisions": copy.deepcopy(rows),
            "related_permits": copy.deepcopy(rows),
            "companion_permits": [],
            "required_permit_names": [],
            "required_permit_families": [],
            "required_permit_segments": [],
            "related_permit_names": [item.name for item in package.related_items],
            "related_permit_families": list(dict.fromkeys(family_label(item.family) for item in package.related_items)),
            "related_permit_segments": list(dict.fromkeys(_public_segment_value(item.segment) for item in package.related_items)),
            "required_permit_summary": headline,
            "customer_headline": headline,
            "job_summary": headline,
            "summary": headline,
            "customer_next_step": f"Contact {office} to resolve the listed conditions or missing inputs before filing or starting work.",
            "apply_url": "",
            "online_application_url": "",
            "apply_path": {"state": "CONTACT_AHJ", "channel": "contact_ahj", "support_level": "verification required", "portal_url": None, "platform": None, "login_required": None, "permit_type": title, "verification_note": "The hard filing claim is unresolved; confirm the exact permit family and filing route with the issuing authority."},
        })
        return out
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
            "required_permit_segments": [],
            "related_permit_names": [item.name for item in package.related_items],
            "related_permit_families": list(dict.fromkeys(family_label(item.family) for item in package.related_items)),
            "related_permit_segments": list(dict.fromkeys(_public_segment_value(item.segment) for item in package.related_items)),
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
    segments = list(dict.fromkeys(_public_segment_value(item.segment) for item in package.required_items))
    related_names = [item.name for item in package.related_items]
    related_labels = list(dict.fromkeys(family_label(item.family) for item in package.related_items))
    related_segments = list(dict.fromkeys(_public_segment_value(item.segment) for item in package.related_items))
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
        "required_permit_segments": segments,
        "related_permit_names": related_names,
        "related_permit_families": related_labels,
        "related_permit_segments": related_segments,
        "required_permit_summary": summary,
        "customer_headline": f"Permit required: {title}.",
        "job_summary": summary,
        "summary": summary,
        "customer_next_step": f"File the required permit categories with {office}: {', '.join(names)}. Confirm exact portal subcategories before final submission.",
    })
    out["permits_required_logic"] = [
        {
            "filing_family": item.family.value,
            "segment": _public_segment_value(item.segment),
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
        row_status = normalize_status(row)
        if decision == "REQUIRED" and row_status != PermitStatus.REQUIRED:
            issues.append(f"required_row_not_required_{idx}")
        if decision in {"CONDITIONAL", "VERIFY", "NEEDS_INPUT"}:
            if row_status.value != decision or row.get("required") is not None:
                issues.append(f"nonbinary_row_status_mismatch_{idx}")
        if not row.get("filing_family"):
            issues.append(f"row_missing_family_{idx}")
        if re.match(r"^\s*multiple permits required\s*:", str(row.get("permit_type") or row.get("permit_name") or ""), re.I):
            issues.append(f"collapsed_package_row_{idx}")
        if fam == PermitFamily.LIQUOR and "liquor" not in str(row.get("permit_type") or row.get("permit_name") or "").lower() and "alcohol" not in str(row.get("permit_type") or row.get("permit_name") or "").lower():
            issues.append(f"liquor_row_name_mismatch_{idx}")
    return issues
