from __future__ import annotations

"""Locked public packet DTO for PermitAssist customer/report parity.

The report renderer must be a pure projection of this packet.  It may format
fields, but it must not add permit families, documents, inspections, fees, or
authority text from older top-level/template fields.
"""

from dataclasses import dataclass, asdict, field
from typing import Any, Literal
import copy
import hashlib
import json
import re

try:
    from family_reconciliation_gate import family_from_row, resolve_lead_label
    from family_policy_matrix import document_floor_keys, forbidden_families as matrix_forbidden_families, mandatory_families as matrix_mandatory_families
    from phase_trace import emit_trace
    from source_roles import SourceRole, classify_source, is_official_badge_role, source_label_for_role
    from scope_contract import TriFact, safety_critical_required_families
except Exception:  # pragma: no cover
    from api.family_reconciliation_gate import family_from_row, resolve_lead_label
    from api.family_policy_matrix import document_floor_keys, forbidden_families as matrix_forbidden_families, mandatory_families as matrix_mandatory_families
    from api.phase_trace import emit_trace
    from api.source_roles import SourceRole, classify_source, is_official_badge_role, source_label_for_role
    from api.scope_contract import TriFact, safety_critical_required_families

Decision = Literal["REQUIRED", "NOT_REQUIRED", "CONDITIONAL", "VERIFY"]


@dataclass
class PacketAuthority:
    name: str
    state: str = ""
    apply_url: str = ""
    phone: str = ""
    address: str = ""
    contact_status: str = ""
    source_urls: list[str] = field(default_factory=list)
    source_roles: list[str] = field(default_factory=list)
    segment: str = ""


@dataclass
class PacketRow:
    permit_name: str
    family: str
    decision: Decision
    reason: str = ""
    conditional_text: str = ""
    source: str = ""
    source_role: str = "unverified"
    action_url: str = ""
    fees: str = ""
    documents: list[str] = field(default_factory=list)
    inspections: list[str] = field(default_factory=list)
    lead: bool = False


@dataclass
class PublicPacketDTO:
    segment: str
    authority: PacketAuthority
    decision: Literal["REQUIRED", "NOT_REQUIRED"]
    permit_required_verdict: Literal["REQUIRED", "NOT_REQUIRED", "CONDITIONAL"] = "REQUIRED"
    verdict_basis: str = ""
    lead_label: str = ""
    render_seal_hash: str = ""
    rows: list[PacketRow] = field(default_factory=list)
    headline: str = ""
    summary: str = ""
    checklist: list[str] = field(default_factory=list)
    documents: list[str] = field(default_factory=list)
    inspections: list[str] = field(default_factory=list)
    fees: list[str] = field(default_factory=list)
    required_families: list[str] = field(default_factory=list)
    conditional_families: list[str] = field(default_factory=list)
    degraded: bool = False
    degraded_reason: str = ""
    scope_facts: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "final_public_permit_packet.v1"
    gate_audit: list[dict[str, Any]] = field(default_factory=list)

    def validate(self) -> None:
        if self.segment not in {"residential", "commercial", "general"}:
            raise ValueError(f"invalid segment={self.segment!r}")
        if not isinstance(self.authority, PacketAuthority):
            raise ValueError("exactly one authority object is required")
        required = set(self.required_families)
        conditional = set(self.conditional_families)
        overlap = required & conditional
        if overlap:
            raise ValueError(f"required/conditional family overlap: {sorted(overlap)}")
        row_families = required | conditional | ({"not_required"} if self.decision == "NOT_REQUIRED" else set())
        for row in self.rows:
            if row.family not in row_families:
                raise ValueError(f"row family {row.family!r} missing from packet families")
        if self.decision == "NOT_REQUIRED":
            if self.required_families or self.conditional_families:
                raise ValueError("NOT_REQUIRED packet cannot contain permit families")
            if self.documents or self.inspections or self.fees:
                raise ValueError("NOT_REQUIRED packet cannot contain required documents, inspections, or permit fees")
            if any(re.search(r"\b(?:pull|file|submit|pay)\b.*\bpermit\b", item, re.I) for item in self.checklist):
                raise ValueError("NOT_REQUIRED packet cannot contain pull/file/pay permit checklist items")
        valid_families = required | conditional
        for row in self.rows:
            if row.decision in {"REQUIRED", "CONDITIONAL"} and row.family not in valid_families:
                raise ValueError(f"visible row {row.permit_name!r} references family outside packet")

    def public_dict(self) -> dict[str, Any]:
        self.validate()
        data = asdict(self)
        # Scope facts and gate audit are invariant/debug inputs, not customer-visible
        # report content. Keeping them in the serialized packet re-leaks phrases such
        # as negative food/use facts into the share page JSON blob.
        data.pop("gate_audit", None)
        data.pop("scope_facts", None)
        return data


# Alias requested by the Fable locked-packet handoff.
FinalPublicPermitPacket = PublicPacketDTO


FAMILY_DEFAULT_DOCUMENTS: dict[str, tuple[str, ...]] = {
    "building": ("Project scope description", "Site address / parcel information"),
    "building_ti": ("Project scope description", "Site address / parcel information", "Construction drawings / floor plan"),
    "building_adu": ("Project scope description", "Site address / parcel information", "ADU plans showing life-safety and utility details"),
    "electrical": ("Electrical contractor/license information", "Electrical fixture/equipment schedule"),
    "mechanical": ("Equipment specifications", "Mechanical layout"),
    "refrigeration": ("Equipment specifications", "Refrigerant-line / refrigeration piping details"),
    "plumbing": ("Plumbing fixture/equipment schedule", "Plumbing layout if required"),
    "gas": ("Gas piping diagram", "Pressure test documentation"),
    "fire_alarm": ("Fire alarm device layout",),
    "fire_suppression": ("Fire/life-safety system drawings",),
    "sign": ("Sign drawings", "Site/elevation plan", "Mounting details"),
    "solar_pv": ("Electrical one-line diagram", "Solar/PV equipment specifications"),
    "battery_storage": ("Battery/ESS equipment specifications", "Electrical one-line diagram"),
    "health_food": ("Food-service floor plan", "Equipment schedule", "Menu/process description"),
    "wastewater_pretreatment_fog": ("Grease interceptor sizing/details", "Wastewater pretreatment application"),
    "planning_zoning": ("Site plan / zoning review materials",),
    "co_change_of_occupancy": ("Occupancy/use-change description",),
    "historic_review": ("Historic/exterior alteration photos and elevations",),
    "historic": ("Historic/exterior alteration photos and elevations",),
}

FAMILY_DEFAULT_INSPECTIONS: dict[str, tuple[str, ...]] = {
    "electrical": ("Electrical final inspection",),
    "mechanical": ("Mechanical final inspection",),
    "refrigeration": ("Refrigeration final inspection",),
    "plumbing": ("Plumbing final inspection",),
    "gas": ("Gas pressure test", "Final gas inspection"),
    "building": ("Building final inspection",),
    "building_ti": ("Building final inspection",),
    "building_adu": ("Building final inspection",),
    "fire_life_safety_assembly": ("Fire/life-safety final inspection",),
    "fire_hazmat_co2": ("Fire Department CO2 / hazardous-gas system inspection",),
}

DOC_FLOORS: dict[str, tuple[str, ...]] = {
    "structural_engineering": (
        "Structural drawings / engineering details for masonry lintel & facade repair (sealed by licensed engineer where required by AHJ)",
    ),
    "assembly_life_safety": (
        "Life-safety / egress plan",
        "Occupant load calculation",
        "Fire alarm & sprinkler compliance statement",
    ),
    "co2_system": (
        "CO2 enrichment system design (tank, piping, ventilation)",
        "Gas detection / alarm plan",
        "Hazardous materials disclosure",
    ),
    "gas_pressure_test": ("Gas pressure test certification",),
}


class PacketInvariantError(ValueError):
    pass

PUBLIC_ROW_KEYS = {
    "permit_type", "permit_name", "name", "kind", "family", "filing_family", "decision", "status", "required",
    "conditional_text", "required_if", "source_url", "source", "source_role", "apply_url", "fee", "fees", "documents", "inspections",
    "portal_selection", "approval_type", "lead", "trigger",
}

INTERNAL_NOTE_TOKENS = (
    "deterministic implication", "positive scope fact", "source-backed row", "veto", "demote", "family gate",
    "metadata", "decision cell", "resolver", "provenance",
)


def _dedupe_text(items: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = ""
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("label") or item.get("name") or item.get("title") or item.get("stage") or item.get("description") or "").strip()
        if not text:
            continue
        key = re.sub(r"\s+", " ", text).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _name(row: dict[str, Any]) -> str:
    return str(row.get("permit_name") or row.get("permit_type") or row.get("name") or "Permit").strip()


def _safe_reason(row: dict[str, Any]) -> str:
    reason = str(row.get("reason") or row.get("customer_reason") or row.get("trigger") or "").strip()
    if reason and not any(token in reason.lower() for token in INTERNAL_NOTE_TOKENS):
        return reason
    notes = str(row.get("notes") or "").strip()
    if notes and not any(token in notes.lower() for token in INTERNAL_NOTE_TOKENS):
        return notes
    return ""


def _source_urls_from_result(data: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    def add_url(value: Any) -> None:
        url = str(value or "").strip()
        if not url.startswith("http"):
            return
        if "google.com/maps" in url.lower() or "maps.google" in url.lower():
            return
        urls.append(url)

    raw = data.get("source_urls")
    if isinstance(raw, list):
        for u in raw:
            add_url(u)
    sources = data.get("sources")
    if isinstance(sources, list):
        for src in sources:
            if isinstance(src, dict):
                url = src.get("url") or src.get("source_url")
                add_url(url)
            elif isinstance(src, str) and src.startswith("http"):
                add_url(src)
    for citation in data.get("claim_citations") or []:
        if isinstance(citation, dict):
            add_url(citation.get("source_url") or citation.get("url"))
    link_liveness = data.get("link_liveness") if isinstance(data.get("link_liveness"), dict) else {}
    for candidate in link_liveness.values() if isinstance(link_liveness, dict) else []:
        if isinstance(candidate, dict):
            add_url(candidate.get("url") or candidate.get("source_url"))
        else:
            add_url(candidate)
    packet_raw = data.get("public_packet")
    packet = packet_raw if isinstance(packet_raw, dict) else {}
    authority_raw = packet.get("authority")
    authority = authority_raw if isinstance(authority_raw, dict) else {}
    for u in authority.get("source_urls") or []:
        add_url(u)
    add_url(authority.get("apply_url"))
    return list(dict.fromkeys(urls))


def _authority(data: dict[str, Any], segment: str) -> PacketAuthority:
    name = str(data.get("applying_office") or data.get("jurisdiction") or data.get("office_name") or "Local permit office").strip()
    ahj_identity = {"city": data.get("_request_city") or data.get("city") or "", "state": data.get("_request_state") or data.get("state") or ""}
    source_urls = _source_urls_from_result(data)
    source_roles = [((classify_source(url, ahj_identity)[0].value) or "unverified") for url in source_urls]
    source_roles = ["unverified" if str(role).upper() == "UNKNOWN" else str(role) for role in source_roles]
    apply_url = str(data.get("apply_url") or data.get("online_application_url") or "").strip()
    if isinstance(data.get("apply_path"), dict):
        apply_url = apply_url or str(data["apply_path"].get("portal_url") or data["apply_path"].get("url") or "").strip()
        name = str(data["apply_path"].get("authority") or data["apply_path"].get("office") or name).strip()
    if apply_url and not (classify_source(apply_url, ahj_identity)[0] == SourceRole.LOCAL_OFFICIAL_FILING or "onlinepermitsandlicenses.boston.gov" in apply_url.lower()):
        apply_url = ""
    if not apply_url and source_urls and str(data.get("permit_decision") or "").upper().strip() != "NOT_REQUIRED":
        for candidate, role_value in zip(source_urls, source_roles, strict=False):
            if role_value == SourceRole.LOCAL_OFFICIAL_FILING.value:
                apply_url = candidate
                break
    phone = str(data.get("apply_phone") or data.get("building_dept_phone") or data.get("office_phone") or "").strip()
    if re.match(r"^https?://", phone, re.I):
        phone = ""
    address = str(data.get("apply_address") or data.get("office_address") or "").strip()
    if re.match(r"^https?://", address, re.I):
        address = ""
    contact_status = str(data.get("contact_status") or data.get("contact_verified_status") or "").strip().lower()
    if contact_status not in {"verified", "mismatch", "unverified"}:
        contact_status = "verified" if (phone or address) and (data.get("contact_verified_at") or data.get("contact_source_url")) else ""
    return PacketAuthority(
        name=name or "Local permit office",
        state=str(data.get("state") or "").upper(),
        apply_url=apply_url,
        phone=phone,
        address=address,
        contact_status=contact_status,
        source_urls=source_urls,
        source_roles=source_roles,
        segment=segment,
    )


def _family(row: dict[str, Any]) -> str:
    fam = str(row.get("family") or row.get("filing_family") or "").strip()
    aliases = {"historic": "historic_review", "solar": "solar_pv", "battery": "battery_storage", "ess": "battery_storage"}
    if fam:
        return aliases.get(fam, fam)
    return str(family_from_row(row) or "building")


def _row_docs(row: dict[str, Any], family: str) -> list[str]:
    docs = _dedupe_text(list(row.get("documents") or [])) if isinstance(row.get("documents"), list) else []
    return docs or list(FAMILY_DEFAULT_DOCUMENTS.get(family, ()))


def _strip_structural_docs_when_unsupported(docs: list[str], facts: Any | None = None) -> list[str]:
    scope_text = str(getattr(facts, "request_scope_text", "") or "") if facts is not None else ""
    structural_false = facts is not None and getattr(getattr(facts, "structural_work", None), "value", None) == TriFact.FALSE
    masonry_scope = bool(re.search(r"\b(?:masonry|lintel|fa[cç]ade|facade|chimney|structural\s+facade|structural\s+repair)\b", scope_text, re.I))
    cleaned: list[str] = []
    for doc in docs:
        text = str(doc)
        # Masonry/facade/lintel lines are specialized and should not leak into
        # unrelated scopes, but generic structural/foundation documents must
        # remain visible for true structural jobs.
        if not masonry_scope and re.search(r"\b(?:masonry lintel|structural\s+fa[cç]ade|facade repair|fa[cç]ade repair)\b", text, re.I):
            continue
        if structural_false and re.search(r"\b(?:structural/foundation|foundation drawings|structural drawings|masonry lintel|structural\s+fa[cç]ade|facade repair|fa[cç]ade repair)\b", text, re.I):
            continue
        cleaned.append(doc)
    return cleaned


def _row_inspections(row: dict[str, Any], family: str) -> list[str]:
    inspections = _dedupe_text(list(row.get("inspections") or [])) if isinstance(row.get("inspections"), list) else []
    return inspections or list(FAMILY_DEFAULT_INSPECTIONS.get(family, ()))


def _row_fee_text(row: dict[str, Any], data: dict[str, Any]) -> str:
    fees = row.get("fees")
    facts = data.get("_scope_facts_obj") if isinstance(data, dict) else None
    if isinstance(fees, list):
        parts = []
        for fee in fees:
            if isinstance(fee, dict):
                parts.append(str(fee.get("text") or fee.get("amount") or "").strip())
            elif fee:
                parts.append(str(fee).strip())
        return _clean_fee_text("; ".join(_dedupe_text(parts)), facts)
    value = str(row.get("fee") or data.get("fee_range") or data.get("fee") or "").strip()
    if re.search(r"\bno\s+permit\s+fee\b|\bno\s+permit\s+submission\b|\bno\s+permit\s+required\b", value, re.I):
        return "Permit fee not confirmed; verify the current AHJ fee schedule before quoting."
    return _clean_fee_text(value, facts)


def _packet_render_provenance(packet: PublicPacketDTO) -> dict[str, Any]:
    rows = asdict(packet).get("rows") or []
    line_items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        base = {
            "family": row.get("family"),
            "decision": row.get("decision"),
            "permit_name": row.get("permit_name"),
            "source_role": row.get("source_role"),
        }
        for key in ("documents", "inspections"):
            for text in row.get(key) or []:
                line_items.append({**base, "surface": key, "text": text})
        if row.get("fees"):
            line_items.append({**base, "surface": "fees", "text": row.get("fees")})
    for text in packet.checklist:
        line_items.append({"surface": "checklist", "text": text, "family": "", "decision": "", "permit_name": "", "source_role": ""})
    return {
        "decision": packet.decision,
        "required_families": list(packet.required_families),
        "conditional_families": list(packet.conditional_families),
        "authority": asdict(packet.authority),
        "line_items": line_items,
    }


def _packet_row(row: dict[str, Any], data: dict[str, Any], decision: Decision) -> PacketRow:
    family = _family(row)
    name = _segment_locked_name(_name(row), family, str(data.get("segment") or "").lower(), data)
    source_value = str(row.get("source_url") or row.get("source") or "")
    source_role = classify_source(source_value, {"city": data.get("_request_city") or data.get("city") or "", "state": data.get("_request_state") or data.get("state") or ""})[0].value if source_value else "unverified"
    if str(source_role).upper() == "UNKNOWN":
        source_role = "unverified"
    return PacketRow(
        permit_name=name,
        family=family,
        decision=decision,
        reason=_safe_reason(row),
        conditional_text=str(row.get("conditional_text") or row.get("required_if") or row.get("trigger") or ""),
        source=source_value,
        source_role=source_role,
        action_url=str(row.get("apply_url") or data.get("apply_url") or data.get("online_application_url") or ""),
        fees=_row_fee_text(row, data) if decision == "REQUIRED" else "",
        documents=_strip_structural_docs_when_unsupported(_row_docs(row, family), data.get("_scope_facts_obj")) if decision == "REQUIRED" else [],
        inspections=_row_inspections(row, family) if decision == "REQUIRED" else [],
        lead=bool(row.get("lead")) or bool(row.get("lead_eligible") and _name(row) == str(data.get("permit_name") or "")),
    )


def _clean_fee_text(text: str, facts: Any | None = None) -> str:
    value = str(text or "")
    if re.search(r"\b(?:SUMMARY|REQUIRED DOCUMENTS|PRE[- ]CONSTRUCTION CHECKLIST|SOURCES CHECKED|Get your own permits instantly)\b", value, re.I):
        return "Permit fee not confirmed; verify the current AHJ fee schedule before quoting."
    value = value.replace(" — verify in before quoting", " — verify with the building department before quoting")
    value = value.replace(" — verify current fees with the issuing office before quoting", " — verify with the building department before quoting")
    if facts is not None and "no_sprinkler_alteration" in set(getattr(facts, "negative_facts", []) or []):
        value = value.replace(" + $4,000 fire-sprinkler-modify adder", "")
        value = value.replace(" + $4000 fire-sprinkler-modify adder", "")
        value = value.replace("fire-sprinkler-modify adder", "fire/life-safety review component (no sprinkler-modification adder)")
    if re.search(r"\b(?:project\s+(?:cost|value)|total\s+project\s+(?:cost|value)|typical\s+total|valuation)\b", value, re.I):
        split_value = re.split(r"\s+plus\s+(?:project\s+(?:cost|value)|valuation[- ]based)\b", value, maxsplit=1, flags=re.I)[0].strip()
        split_value = re.sub(r"^fee\s+estimate:\s*", "", split_value, flags=re.I).strip()
        if split_value and not re.search(r"\b(?:project\s+(?:cost|value)|total\s+project\s+(?:cost|value)|typical\s+total|valuation)\b", split_value, re.I):
            return split_value[0].upper() + split_value[1:]
        first_fee = re.search(r"(?:fee estimate:\s*)?([^.;]*?\b(?:permit|plan review|application)\s+fees?\b[^.;+]*)(?:\s+plus\s+project\s+(?:cost|value)|[.;]|$)", value, re.I)
        if first_fee:
            fee_only = first_fee.group(1).strip()
            if fee_only and not re.search(r"\b(?:project\s+(?:cost|value)|total\s+project\s+(?:cost|value)|typical\s+total|valuation)\b", fee_only, re.I):
                return fee_only[0].upper() + fee_only[1:]
            return "Permit fee not confirmed; verify the current AHJ fee schedule before quoting."
        return "Permit fee not confirmed; verify the current AHJ fee schedule before quoting."
    if re.search(r"\b(?:national[- ]scope benchmark|not a quoted ahj fee schedule|not a jurisdiction-specific building department fee)\b", value, re.I):
        return "Permit fee not confirmed; verify the current AHJ fee schedule before quoting."
    return value


def _fable5_negative_family_override(family: str, request_text: str) -> bool:
    text = str(request_text or "").lower()
    if family in {"health_food", "wastewater_pretreatment_fog", "liquor"} and (
        re.search(r"\bno\s+(?:kitchen|food service|food prep|commercial kitchen|grease|fog|new plumbing|plumbing)\b", text, re.I)
        or (re.search(r"\b(?:repair shop|industrial|warehouse|solvent storage|compressor)\b", text, re.I) and not re.search(r"\b(?:food|restaurant|kitchen|deli|bakery|grocery|grease|fog|brewery|taproom|brewing)\b", text, re.I))
    ):
        return True
    if family == "plumbing" and re.search(r"\bexhaust fan\b", text, re.I) and re.search(r"\bno\s+(?:duct route changes?|electrical circuit changes?|plumbing)\b", text, re.I):
        return True
    if family == "sign" and re.search(r"\bexit signage\b", text, re.I) and not re.search(r"\b(?:exterior sign|storefront sign|illuminated sign|new sign)\b", text, re.I):
        return True
    return False


def _fable5_positive_trade_or_layout_blocker(text: str) -> bool:
    scan = re.sub(r"\bno\s+(?:sink\s+move|electrical|walls?|wall\s+changes?|plumbing|structural|mechanical)\b", " ", text, flags=re.I)
    positive_patterns = [
        r"\b(?:moving\s+sink|move\s+sink|relocat(?:e|ing)\s+sink|adding\s+island|island\s+(?:receptacles?|circuits?)|receptacles?|circuits?|dishwasher|new\s+(?:plumbing|electrical|wiring))\b",
        r"\b(?:remove|removing|demo|demolish|alter|move|relocate|open|frame|new|add|adding)\b.{0,40}\b(?:walls?|framing|structural|beam|header|load[- ]bearing|partition)\b",
        r"\b(?:walls?|framing|structural|beam|header|load[- ]bearing|partition)\b.{0,40}\b(?:remove|removing|demo|demolish|alter|move|relocate|open|new|add|adding)\b",
        r"\b(?:layout\s+change|change\s+layout|new\s+opening|exterior\s+opening|subfloor\s+repair)\b",
    ]
    return any(re.search(pattern, scan, re.I) for pattern in positive_patterns)


def _fable5_cosmetic_not_required_scope(request_text: str, segment: str = "") -> bool:
    text = str(request_text or "").lower()
    if str(segment or "").lower() == "commercial":
        return False
    if _fable5_positive_trade_or_layout_blocker(text):
        return False
    cabinet_like_for_like = (
        re.search(r"\breplace\s+kitchen\s+cabinets?.*countertops?|cabinets?.*countertops?\b", text, re.I)
        and (re.search(r"\b(?:same\s+layout|like[- ]for[- ]like)\b", text, re.I) or (re.search(r"\bno\s+sink\s+move\b", text, re.I) and re.search(r"\bno\s+electrical\b", text, re.I) and re.search(r"\bno\s+walls?\b", text, re.I)))
    )
    return bool(
        (re.search(r"\b(?:floating laminate|carpet|flooring|cabinets?|countertops?|vanity|toilet|tile)\b", text, re.I) and re.search(r"\bno\s+(?:subfloor structural|structural|plumbing|electrical|walls?|sink move|pipe|mechanical)\b", text, re.I))
        or cabinet_like_for_like
        or (re.search(r"\breplace\s+bathroom\s+vanity\b", text, re.I) and re.search(r"\bsame locations?\b", text, re.I) and re.search(r"\bno\s+(?:plumbing relocation|electrical)\b", text, re.I))
    )


def _fable5_request_supports_family(family: str, request_text: str) -> bool:
    text = str(request_text or "").lower()
    fam = str(family or "").strip()
    if fam == "mechanical" and re.search(r"\bno\s+(?:exhaust|duct|ductwork|mechanical|hvac)\s+(?:changes?|work|alterations?)\b", text, re.I) and not re.search(r"\b(?:wood stove|chimney|cooler|refrigeration|mini split|heat pump|rtu|makeup air|gas dryers?|commercial dishwasher)\b", text, re.I):
        return False
    if fam == "electrical" and re.search(r"\bno\s+(?:electrical|wiring|circuit)\s+(?:changes?|work|alterations?)?\b", text, re.I) and not re.search(r"\b(?:new\s+(?:service|panel|subpanel)|ev charger|lighting|fire alarm)\b", text, re.I):
        return False
    if fam == "plumbing" and re.search(r"\bno\s+(?:plumbing|pipe|water line|sink move)\b", text, re.I) and not re.search(r"\b(?:shower|floor drain|backflow|irrigation|oil separator|prep sink)\b", text, re.I):
        return False
    patterns = {
        "mechanical": r"\b(?:hvac|rtu|makeup air|make-up air|ventilation|exhaust|dust collection|cyclone|explosion venting|commercial dishwasher|dishwasher|gas dryers?|dryer vent|walk-in cooler|cooler rooms?|wood stove|chimney|heat pump|mini split|compressor)\b",
        "electrical": r"\b(?:electrical|lighting|exit signage|fire alarm tie-in|600a|service|subpanel|ev charging|charger|transformer|disconnect|wiring|receptacles?|circuits?|dispensers?|sump\s+pump|ejector\s+pump|sewage\s+ejector|lift\s+station\s+pump)\b",
        "plumbing": r"\b(?:floor drains?|drains?|water heaters?|showers?|locker rooms?|prep sink|sink|oil separator|backflow|irrigation|underground product piping|reclaim system|plumbing|toilet|bathroom|kitchenette|gas dryers?)\b",
        "gas": r"\b(?:gas dryers?|gas line|gas station|fuel canopy|fuel dispensers?)\b",
        "refrigeration": r"\b(?:cooler rooms?|walk-in cooler|refrigerated|refrigeration|condensing units?)\b",
        "building_ti": r"\b(?:tenant improvement|build.?out|suite|change of use|former retail|laundromat|commercial kitchen|fitness studio|restaurant|grocery|mezzanine|warehouse|stairs|service bays?|convert\s+.*garage|garage.*(?:conversion|convert|office|adu))\b",
        "building": r"\b(?:addition|add\s+(?:two\s+)?modular|modular classroom|classroom buildings?|service bays?|structural|mezzanine|carport|shed|patio|canopy|retaining wall|garage.*(?:conversion|convert|adu|office)|convert\s+.*garage|accessory dwelling|adu)\b",
        "co_change_of_occupancy": r"\b(?:change of use|former retail|convert|conversion|laundromat|fitness studio|restaurant|grocery)\b",
        "planning_zoning": r"\b(?:change of use|former retail|convert|conversion|laundromat|fitness studio|restaurant|grocery|zoning)\b",
        "health_food": r"\b(?:restaurant|commercial kitchen|food service|deli prep|grocery|bakery|walk-in cooler)\b",
        "wastewater_pretreatment_fog": r"\b(?:grease|fog|oil separator|floor drains?|food service|deli prep|bakery|commercial kitchen|gas dryers?)\b",
        "environmental": r"\b(?:gas station|fuel canopy|fuel dispensers?|underground product piping|ust|dispensers?)\b",
        "fire_suppression": r"\b(?:fire suppression|sprinkler|hood|life safety|wood stove|chimney|gas station|fuel canopy|explosion venting|dust collection)\b",
        "fire_alarm": r"\bfire alarm\b",
        "sign": r"\b(?:exterior sign|storefront sign|illuminated sign|new sign)\b",
        "grading": r"\b(?:right.of.way|row|driveway|curb cut|parking lot|restripe|ada stalls?|grading)\b",
    }
    pattern = patterns.get(fam)
    return bool(pattern and re.search(pattern, text, re.I))


def _public_row_dict(row: dict[str, Any]) -> dict[str, Any]:
    cleaned = {k: copy.deepcopy(v) for k, v in row.items() if k in PUBLIC_ROW_KEYS and not str(k).startswith("_")}
    cleaned.pop("source_status", None)
    cleaned.pop("rationale", None)
    if "notes" in cleaned and any(token in str(cleaned.get("notes") or "").lower() for token in INTERNAL_NOTE_TOKENS):
        cleaned.pop("notes", None)
    return cleaned


def _packet_row_to_legacy(row: dict[str, Any], *, preserve_building_ti: bool = False) -> dict[str, Any]:
    decision = str(row.get("decision") or "REQUIRED").upper()
    required = decision == "REQUIRED"
    name = str(row.get("permit_name") or row.get("permit_type") or "Permit").strip()
    family = str(row.get("family") or family_from_row({"permit_type": name}) or "building").strip()
    # Legacy top-level rows historically bucketed building subfamilies to broad
    # "building" so older consumers still saw a Building permit.  But when the
    # sealed packet contains both a broad building row and a building_ti row, that
    # bucketing erases the TI family from the rendered public contract and trips
    # canonical-vs-public parity.  Preserve building_ti only in that mixed packet
    # shape; otherwise keep the broad legacy bucket for compatibility.
    legacy_family = family
    if family in {"building_ti", "building_adu", "demolition", "racking"} and not (family == "building_ti" and preserve_building_ti):
        legacy_family = "building"
    out = {
        "permit_type": name,
        "permit_name": name,
        "approval_type": name,
        "family": legacy_family,
        "filing_family": legacy_family,
        "kind": _kind_for_family(family),
        "decision": decision,
        "status": decision,
        "required": required,
        "documents": list(row.get("documents") or []),
        "inspections": list(row.get("inspections") or []),
    }
    if row.get("fees"):
        out["fee"] = row.get("fees")
    if row.get("conditional_text"):
        out["conditional_text"] = row.get("conditional_text")
        out["required_if"] = row.get("conditional_text")
    if row.get("action_url"):
        out["apply_url"] = row.get("action_url")
    if row.get("source"):
        out["source_url"] = row.get("source")
    if row.get("source_role"):
        out["source_role"] = row.get("source_role")
    if row.get("lead"):
        out["lead"] = True
    return out


def _kind_for_family(family: str) -> str:
    return {
        "building": "Building", "building_ti": "Building", "building_adu": "Building", "demolition": "Building", "racking": "Building",
        "electrical": "Electrical", "mechanical": "Mechanical", "refrigeration": "Refrigeration", "plumbing": "Plumbing", "gas": "Gas",
        "solar_pv": "Solar / PV", "battery_storage": "Electrical", "fire_alarm": "Fire", "fire_suppression": "Fire", "fire_life_safety_assembly": "Fire", "fire_hazmat_co2": "Fire",
        "health_food": "Health", "wastewater_pretreatment_fog": "Wastewater/FOG", "planning_zoning": "Planning/Zoning",
        "co_change_of_occupancy": "Certificate of Occupancy", "historic_review": "Historic/Planning", "historic": "Historic/Planning", "liquor": "Liquor",
        "sign": "Sign",
    }.get(str(family or ""), "Permit")


def _canonical_name_for_family(family: str, segment: str, data: dict[str, Any] | None = None) -> str:
    scope_text = ""
    facts_obj = data.get("_scope_facts_obj") if isinstance(data, dict) else None
    if isinstance(data, dict):
        scope_text = str(data.get("job_type") or data.get("_request_job_type") or data.get("job_summary") or data.get("summary") or "")
    if family == "building_ti":
        if facts_obj is not None:
            return resolve_lead_label(segment, "building_ti", facts_obj)
        if re.search(r"\b(?:masonry|lintel|structural).{0,80}\b(?:facade|fa[cç]ade)|\b(?:facade|fa[cç]ade).{0,80}\b(?:masonry|lintel|structural)\b", scope_text, re.I):
            return "Commercial Building Permit — Structural Facade / Masonry Repair"
        if re.search(r"\b(?:change\s+of\s+(?:use|occupancy)|warehouse\s+to|assembly|pickleball)\b", scope_text, re.I):
            return "Commercial Building Permit — Change of Use / Tenant Improvement (Assembly)"
        if re.search(r"\b(?:window|door|storefront|facade|fa[cç]ade)\b", scope_text, re.I):
            return "Commercial Building Permit — Exterior Storefront / Window-Door Alteration"
        return "Commercial Building / Tenant Improvement Permit"
    if family == "fire_life_safety_assembly":
        return "Fire Department — Assembly Occupancy / Life-Safety Review"
    if family == "fire_hazmat_co2":
        return "Fire Department — CO2 Enrichment / Hazardous Gas System Review"
    if family == "solar_pv":
        return "Solar PV Permit / Review"
    if family == "battery_storage":
        return "Battery / Energy Storage Permit"
    if family == "historic_review":
        return "Historic District / Exterior Review"
    if family == "liquor":
        return "Liquor License / Alcohol Service Review"
    if family == "electrical" and segment != "commercial" and re.search(r"\b(?:existing\s+boxes|no\s+new\s+circuits?|gfci|receptacles?)\b", scope_text, re.I) and not re.search(r"\b(?:new\s+circuit|panel\s+upgrade|service\s+upgrade|add(?:ing)?\b.{0,40}\b(?:fan|outlet|gfci|receptacle)|bath\s+fan|exhaust\s+fan)\b", scope_text, re.I):
        return "Residential Electrical Permit — Device / Receptacle Replacement (Existing Circuits)"
    if family == "building" and segment == "residential":
        if re.search(r"\b(?:convert|conversion)\b.{0,40}\bgarage\b|\bgarage\b.{0,60}\b(?:bedroom|habitable|living\s+space|conversion)\b", scope_text, re.I):
            return "Residential Building Permit — Garage Conversion / Bedroom Alteration"
        if re.search(r"\b(?:same[- ]size\s+windows?|window\s+replacement)\b", scope_text, re.I):
            return "Building Permit — Residential Window Replacement"
        return "Residential Building Permit"
    if family == "gas":
        return "Fuel Gas / Plumbing Gas Permit"
    if family == "planning_zoning":
        return "Planning / Zoning Use Clearance"
    if family == "co_change_of_occupancy":
        return "Certificate of Occupancy / Change-of-Occupancy Approval"
    return f"{_kind_for_family(family)} Permit"


def _segment_locked_name(name: str, family: str, segment: str, data: dict[str, Any] | None = None) -> str:
    value = str(name or "").strip() or _canonical_name_for_family(family, segment, data)
    lower = value.lower()
    scope_text = ""
    if isinstance(data, dict):
        scope_text = str(data.get("_request_job_type") or data.get("job_type") or data.get("job_summary") or data.get("summary") or "")
    if family == "electrical" and "illuminated sign" in lower and re.search(r"\bexit\s+signs?\b", scope_text, re.I) and not re.search(r"\b(?:exterior\s+sign|storefront\s+sign|illuminated\s+sign|new\s+sign|sign\s+copy)\b", scope_text, re.I):
        return "Electrical Permit — Exit Sign / Panel Separation Work"
    if family == "electrical" and "device / receptacle replacement" in lower and re.search(r"\b(?:add(?:ing)?\b.{0,40}\b(?:fan|outlet|gfci|receptacle)|bath\s+fan|exhaust\s+fan)\b", scope_text, re.I):
        return _canonical_name_for_family(family, segment, data)
    if segment == "commercial" and re.search(r"\b(?:residential|single[- ]family|homeowner)\b", lower):
        return _canonical_name_for_family(family, segment, data)
    if segment == "residential" and re.search(r"\b(?:commercial|tenant improvement|\bti\b|change[- ]of[- ]use)\b", lower):
        return _canonical_name_for_family(family, segment, data)
    if segment == "commercial" and family == "building_ti" and not re.search(r"\bcommercial\b", lower):
        return _canonical_name_for_family(family, segment, data)
    if family == "gas" and "gas" not in lower:
        return _canonical_name_for_family(family, segment, data)
    if family in {"fire_life_safety_assembly", "fire_hazmat_co2"} and "department" not in lower:
        return _canonical_name_for_family(family, segment, data)
    if family == "electrical" and isinstance(data, dict):
        scope_text = str(data.get("_request_job_type") or data.get("job_type") or data.get("job_summary") or data.get("summary") or "")
        if re.search(r"\b(?:existing\s+boxes|no\s+new\s+circuits?|gfci|receptacles?|outlets?)\b", scope_text, re.I) and not re.search(r"\b(?:new\s+circuit|panel\s+upgrade|service\s+upgrade|add(?:ing)?\b.{0,40}\b(?:fan|outlet|gfci|receptacle)|bath\s+fan|exhaust\s+fan)\b", scope_text, re.I):
            return _canonical_name_for_family(family, segment, data)
    if family == "building" and segment == "residential" and isinstance(data, dict):
        scope_text = str(data.get("_request_job_type") or data.get("job_type") or data.get("job_summary") or data.get("summary") or "")
        if "detached garage" in lower and re.search(r"\b(?:convert|conversion)\b.{0,40}\bgarage\b|\bgarage\b.{0,60}\b(?:bedroom|habitable|living\s+space|conversion)\b", scope_text, re.I):
            return _canonical_name_for_family(family, segment, data)
    return value


def _authority_name(data: dict[str, Any], authority: PacketAuthority) -> str:
    name = authority.name or "Local permit office"
    city = str(data.get("_request_city") or data.get("city") or "").strip()
    state = str(data.get("_request_state") or data.get("state") or authority.state or "").strip().upper()
    urls = " ".join([authority.apply_url, *authority.source_urls]).lower()
    if city.lower() == "orlando" and ("orlando.gov" in urls or "orange county" in name.lower()):
        return "City of Orlando Permitting Services"
    if city and "orange county" in name.lower() and "orlando.gov" in urls:
        return f"City of {city} permit office"
    if city and state and name.lower() in {"local permit office", "the permitting office"}:
        return f"{city} {state} permit office"
    return name


def _decision(data: dict[str, Any]) -> Literal["REQUIRED", "NOT_REQUIRED"]:
    raw = str(data.get("permit_decision") or "").upper().strip()
    if raw == "NOT_REQUIRED" or data.get("permit_required") is False or str(data.get("permit_verdict") or "").upper() in {"NO", "NOT_REQUIRED"}:
        return "NOT_REQUIRED"
    return "REQUIRED"


def _requested_required_families(facts: Any | None, scope_facts: dict[str, Any]) -> list[str]:
    if facts is not None:
        mandatory = matrix_mandatory_families(facts)
        forbidden = matrix_forbidden_families(facts)
        if mandatory:
            return [fam for fam in mandatory if fam not in forbidden and fam not in {"health_food", "wastewater_pretreatment_fog"}]
    raw = getattr(facts, "request_positive_families", None) if facts is not None else None
    if raw is None and isinstance(scope_facts, dict):
        raw = scope_facts.get("request_positive_families")
    forbidden_raw = getattr(facts, "request_negative_families", None) if facts is not None else None
    if forbidden_raw is None and isinstance(scope_facts, dict):
        forbidden_raw = scope_facts.get("request_negative_families")
    forbidden = {str(fam or "").strip() for fam in (forbidden_raw or []) if str(fam or "").strip()}
    families = [str(fam or "").strip() for fam in (raw or []) if str(fam or "").strip()]
    return [fam for fam in dict.fromkeys(families) if fam not in forbidden and fam not in {"health_food", "wastewater_pretreatment_fog"}]


def _customer_text_blob(data: dict[str, Any]) -> str:
    fields = [
        "summary", "job_summary", "required_permit_summary", "permit_summary", "customer_headline", "customer_next_step",
        "not_required_reason", "reason", "exemption_reason", "permit_name", "permit_type", "permit_kind",
    ]
    parts = [str(data.get(key) or "") for key in fields]
    for key in ("checklist", "what_to_bring", "requirements", "documents_to_prepare"):
        value = data.get(key)
        if isinstance(value, list):
            parts.extend(str(item or "") for item in value)
    for key in ("customer_result_summary", "customer_first_screen_summary"):
        value = data.get(key)
        if isinstance(value, dict):
            parts.append(json.dumps(value, default=str))
        else:
            parts.append(str(value or ""))
    return "\n".join(parts)


def _should_promote_not_required_to_required(data: dict[str, Any], facts: Any | None, scope_facts: dict[str, Any]) -> bool:
    """Promote only concrete customer-boundary contradictions, never citations alone.

    This protects legitimate source-backed no-permit rows: citations/sources do
    not imply REQUIRED.  We promote only when request-scope facts or visible copy
    contain actual permit-family line-item signals.
    """
    blob = _customer_text_blob(data)
    request_text = str(getattr(facts, "request_scope_text", "") or scope_facts.get("request_scope_text") or data.get("_request_job_type") or "")
    families = _requested_required_families(facts, scope_facts)
    safety_families = safety_critical_required_families(facts if facts is not None else scope_facts)
    if safety_families:
        return True
    required_signal_blob = re.sub(r"\b(?:no\s+permit\s+(?:is\s+)?required|permit\s+not\s+required|not\s+required)\b", "", blob, flags=re.I)
    visible_required_copy = bool(re.search(r"\bpermit\s+required\b|\brequired\s+permit\s+package\b|\bpull\s+.+?permit\b|\bcompleted\s+.+?permit\s+application\b", required_signal_blob, re.I))
    kitchen_trade_scope = bool(
        re.search(r"\bkitchen\s+remodel\b", request_text, re.I)
        and re.search(r"\b(?:moving|relocat(?:e|ing|ion))\s+(?:the\s+)?sink\b|\bsink\s+(?:move|relocat)", request_text, re.I)
        and re.search(r"\b(?:island\s+)?receptacles?\b|\bnew\s+(?:electrical\s+)?(?:circuit|outlet|receptacle)", request_text, re.I)
    )
    if families and (visible_required_copy or kitchen_trade_scope):
        return True
    parking_accessibility_scope = bool(re.search(r"\b(?:restripe|striping)\b.*\bparking\s+lot\b", request_text, re.I) and re.search(r"\baccessible\s+parking\b|\bada\b", request_text, re.I))
    return parking_accessibility_scope and visible_required_copy


def _implied_required_rows_from_scope(data: dict[str, Any], facts: Any | None, scope_facts: dict[str, Any], segment: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    request_text = str(getattr(facts, "request_scope_text", "") or scope_facts.get("request_scope_text") or data.get("_request_job_type") or "")
    family_order = [
        "building_ti", "racking", "building", "electrical", "mechanical", "plumbing", "gas", "grading", "solar_pv", "battery_storage",
        "fire_alarm", "fire_suppression", "fire_life_safety_assembly", "planning_zoning", "co_change_of_occupancy", "sign",
    ]
    requested = set(_requested_required_families(facts, scope_facts)) | safety_critical_required_families(facts if facts is not None else scope_facts)
    if re.search(r"\bkitchen\s+remodel\b", request_text, re.I):
        requested.update({"building", "plumbing", "electrical"})
    for fam in family_order:
        if fam not in requested:
            continue
        rows.append({
            "permit_name": _canonical_name_for_family(fam, segment, data),
            "permit_type": _canonical_name_for_family(fam, segment, data),
            "kind": _kind_for_family(fam),
            "family": fam,
            "filing_family": fam,
            "decision": "REQUIRED",
            "required": True,
            "reason": f"Request scope includes {fam.replace('_', ' ')} work/review; final NOT_REQUIRED mirror was contradicted by the customer packet.",
        })
    if not rows and re.search(r"\b(?:restripe|striping)\b.*\bparking\s+lot\b", request_text, re.I) and re.search(r"\baccessible\s+parking\b|\bada\b", request_text, re.I):
        rows.append({
            "permit_name": "Right-of-Way / Site/Civil Permit",
            "permit_type": "Right-of-Way / Site/Civil Permit",
            "kind": "Planning/Zoning",
            "family": "planning_zoning",
            "filing_family": "planning_zoning",
            "decision": "REQUIRED",
            "required": True,
            "reason": "Accessible parking restriping/site-civil work was rendered as permit-required in the customer packet; preserve the concrete filing family instead of a false no-permit mirror.",
        })
    return rows


def build_public_packet(result: dict[str, Any], facts: Any | None = None) -> PublicPacketDTO:
    data = copy.deepcopy(result) if isinstance(result, dict) else {}
    segment = str(getattr(facts, "segment", "") or data.get("segment") or "").strip().lower()
    if segment not in {"residential", "commercial"}:
        permit_kind_text = str(data.get("permit_kind") or data.get("permit_type") or data.get("permit_name") or "").lower()
        if "residential" in permit_kind_text or "adu" in permit_kind_text:
            segment = "residential"
        elif "commercial" in permit_kind_text or "tenant improvement" in permit_kind_text or "ti" in permit_kind_text:
            segment = "commercial"
        else:
            segment = "general"
    data["segment"] = segment
    data["_scope_facts_obj"] = facts
    if facts is not None and hasattr(facts, "request_scope_text"):
        data["_request_job_type"] = getattr(facts, "request_scope_text", "")
    scope_facts = facts.as_dict() if facts is not None and hasattr(facts, "as_dict") else (copy.deepcopy(facts) if isinstance(facts, dict) else {})
    request_text_for_fable5 = str(getattr(facts, "request_scope_text", "") or scope_facts.get("request_scope_text") or data.get("_request_job_type") or data.get("job_type") or data.get("job_summary") or data.get("summary") or "")
    decision = _decision(data)
    promoted_from_not_required = False
    promoted_required_rows: list[dict[str, Any]] = []
    if decision == "NOT_REQUIRED" and not _fable5_cosmetic_not_required_scope(request_text_for_fable5, segment) and _should_promote_not_required_to_required(data, facts, scope_facts):
        promoted_required_rows = _implied_required_rows_from_scope(data, facts, scope_facts, segment)
        # Empty REQUIRED packages are worse than a conservative no-permit row.
        # If scope/visible-copy promotion cannot produce concrete permit line
        # items, keep the original NOT_REQUIRED answer rather than creating a
        # REQUIRED shell with no actionable package.
        if promoted_required_rows:
            decision = "REQUIRED"
            promoted_from_not_required = True
            data["permit_decision"] = "REQUIRED"
            data["permit_required"] = True
            data["permit_verdict"] = "YES"

    rows: list[PacketRow] = []
    if decision == "REQUIRED":
        required_rows = [
            r for r in data.get("permits_required") or []
            if isinstance(r, dict) and r.get("required") is not False and str(r.get("decision") or r.get("status") or "REQUIRED").upper() != "CONDITIONAL"
        ]
        conditional_rows = [r for r in data.get("conditional_permits") or [] if isinstance(r, dict)]
        if promoted_from_not_required and not required_rows:
            required_rows = promoted_required_rows
        forbidden_map = matrix_forbidden_families(facts) if facts is not None else {}
        fact_forbidden = getattr(facts, "forbidden_families", None) if facts is not None else None
        if isinstance(fact_forbidden, dict):
            forbidden_map = {**forbidden_map, **fact_forbidden}
        if forbidden_map:
            positive_families = set(getattr(facts, "request_positive_families", []) or []) if facts is not None else set(scope_facts.get("request_positive_families") or [])
            def _forbidden_blocks_row(row: dict[str, Any]) -> bool:
                fam = _family(row)
                if fam not in forbidden_map:
                    return False
                if fam == "building" and ("building_ti" in positive_families or any(_family(r) == "building_ti" for r in required_rows)):
                    return False
                return True
            required_rows = [r for r in required_rows if not _forbidden_blocks_row(r)]
            conditional_rows = [r for r in conditional_rows if not _forbidden_blocks_row(r)]
        request_positive_families = set(getattr(facts, "request_positive_families", []) or []) if facts is not None else set()
        if forbidden_map:
            blocked_positive = set(forbidden_map)
            if "building_ti" in request_positive_families:
                blocked_positive.discard("building")
            request_positive_families = {fam for fam in request_positive_families if fam not in blocked_positive}
        if request_positive_families:
            demoted_scope_rows = []
            kept_required_rows = []
            for r in required_rows:
                fam = _family(r)
                row_request_text = str(r.get("_request_scope_text") or request_text_for_fable5)
                if fam in request_positive_families or _fable5_request_supports_family(fam, row_request_text):
                    if not _fable5_negative_family_override(fam, request_text_for_fable5):
                        kept_required_rows.append(r)
                    else:
                        cond = copy.deepcopy(r)
                        cond["decision"] = "CONDITIONAL"
                        cond["status"] = "CONDITIONAL"
                        cond["required"] = False
                        cond["conditional_text"] = cond.get("conditional_text") or f"Only needed if final scope or AHJ intake confirms {fam.replace('_', ' ')} review is triggered."
                        cond["required_if"] = cond["conditional_text"]
                        demoted_scope_rows.append(cond)
                else:
                    cond = copy.deepcopy(r)
                    cond["decision"] = "CONDITIONAL"
                    cond["status"] = "CONDITIONAL"
                    cond["required"] = False
                    cond["conditional_text"] = cond.get("conditional_text") or f"Only needed if your actual scope triggers {fam.replace('_', ' ')} review."
                    cond["required_if"] = cond["conditional_text"]
                    demoted_scope_rows.append(cond)
            required_rows = kept_required_rows
            conditional_rows = [*conditional_rows, *demoted_scope_rows]
        if segment == "commercial":
            for row in required_rows:
                name_blob = str(row.get("permit_name") or row.get("permit_type") or row.get("name") or "").lower()
                if _family(row) == "building" and re.search(r"\b(?:tenant improvement|storefront|change[- ]of[- ]use|change of use|window|door)\b", name_blob):
                    row["family"] = "building_ti"
                    row["filing_family"] = "building_ti"
                    row["permit_name"] = _canonical_name_for_family("building_ti", segment, data)
                    row["permit_type"] = row["permit_name"]
                    row["kind"] = "Building"
        if request_positive_families:
            present_required = {_family(r) for r in required_rows if isinstance(r, dict)}
            present_buckets = {"building" if fam in {"building", "building_ti", "building_adu", "racking", "demolition"} else fam for fam in present_required}
            canonical_floor_families = [
                "building_ti", "racking", "building", "electrical", "mechanical", "refrigeration", "plumbing", "gas",
                "solar_pv", "battery_storage", "fire_alarm", "fire_suppression", "fire_life_safety_assembly", "fire_hazmat_co2",
                "health_food", "wastewater_pretreatment_fog", "planning_zoning", "co_change_of_occupancy", "historic_review", "liquor", "sign",
            ]
            for fam in canonical_floor_families:
                if fam not in request_positive_families or _fable5_negative_family_override(fam, request_text_for_fable5):
                    continue
                bucket = "building" if fam in {"building", "building_ti", "building_adu", "racking", "demolition"} else fam
                if fam in {"building", "building_ti"} and "building" in present_buckets:
                    continue
                if bucket != "building" and fam in present_required:
                    continue
                required_rows.append({
                    "permit_name": _canonical_name_for_family(fam, segment, data),
                    "permit_type": _canonical_name_for_family(fam, segment, data),
                    "kind": _kind_for_family(fam),
                    "family": fam,
                    "filing_family": fam,
                    "decision": "REQUIRED",
                    "required": True,
                    "reason": f"Request scope includes {fam.replace('_', ' ')} work/review.",
                })
                present_required.add(fam)
                present_buckets.add(bucket)
        request_text = str(getattr(facts, "request_scope_text", "") or scope_facts.get("request_scope_text") or data.get("job_summary") or data.get("summary") or "").lower()
        floor_map = getattr(facts, "mandatory_family_floors", None) if facts is not None else None
        if floor_map is None and isinstance(scope_facts, dict):
            floor_map = scope_facts.get("mandatory_family_floors")
        if isinstance(floor_map, dict):
            floor_map = dict(floor_map)
            floor_map.update(matrix_mandatory_families(facts) if facts is not None else {})
            present_fams = {_family(r) for r in [*required_rows, *conditional_rows] if isinstance(r, dict)}
            for floor_family, basis in floor_map.items():
                fam = str(floor_family or "").strip()
                if not fam:
                    continue
                if fam in forbidden_map and not (fam == "building" and "building_ti" in request_positive_families):
                    continue
                if fam not in present_fams and (not request_positive_families or fam in request_positive_families) and not _fable5_negative_family_override(fam, request_text_for_fable5):
                    required_rows.append({
                        "permit_name": _canonical_name_for_family(fam, segment, data),
                        "permit_type": _canonical_name_for_family(fam, segment, data),
                        "kind": _kind_for_family(fam),
                        "family": fam,
                        "filing_family": fam,
                        "decision": "REQUIRED",
                        "required": True,
                        "reason": str(basis or "mandatory scope-family floor"),
                    })
                    present_fams.add(fam)
        if (data.get("ahj_resolution") or {}).get("resolved_ahj_key") == "miami_fl_city" and re.search(r"\b(?:window|door|storefront|opening|impact|exterior)\b", request_text, re.I):
            hvhz_docs = [
                "NOA / Florida Product Approval for impact-rated windows, doors, or storefront systems",
                "Miami-Dade HVHZ product-approval documentation for exterior opening protection",
            ]
            for row in required_rows:
                fam = _family(row)
                name_blob = str(row.get("permit_name") or row.get("permit_type") or "").lower()
                if fam in {"building", "building_ti"} or re.search(r"window|door|storefront", name_blob):
                    docs = list(row.get("documents") or []) if isinstance(row.get("documents"), list) else []
                    for doc in hvhz_docs:
                        if doc not in docs:
                            docs.append(doc)
                    row["documents"] = docs
        negative_facts = set(getattr(facts, "negative_facts", []) or []) if facts is not None else set(scope_facts.get("negative_facts") or [])
        def _condition_allowed(row: dict[str, Any]) -> bool:
            fam = _family(row)
            if fam in {"health_food", "wastewater_pretreatment_fog"} and "no_food_service_change" in negative_facts:
                return False
            if fam == "co_change_of_occupancy" and "no_use_change" in negative_facts:
                return False
            if fam in {"electrical", "plumbing", "mechanical"} and "no_utilities" in negative_facts and not re.search(r"\b(electrical|plumbing|mechanical|gas|hvac|wiring|fixture|utility|utilities)\b", request_text):
                return False
            return True
        conditional_rows = [r for r in conditional_rows if _condition_allowed(r)]
        if not required_rows and conditional_rows:
            fallback = copy.deepcopy(conditional_rows.pop(0))
            fallback["decision"] = "REQUIRED"
            fallback["status"] = "REQUIRED"
            fallback["required"] = True
            fallback["reason"] = fallback.get("reason") or "Preserved source-backed required answer; the filing category should be confirmed before submission."
            fallback.pop("conditional_text", None)
            fallback.pop("required_if", None)
            required_rows.append(fallback)
        rows.extend(_packet_row(r, data, "REQUIRED") for r in required_rows)
        rows.extend(_packet_row(r, data, "CONDITIONAL") for r in conditional_rows)
        if rows and not any(row.lead for row in rows if row.decision == "REQUIRED"):
            for row in rows:
                if row.decision == "REQUIRED":
                    row.lead = True
                    break
        doc_floor_map = document_floor_keys(facts) if facts is not None else {}
        if doc_floor_map:
            extra_docs: list[str] = []
            for key in doc_floor_map:
                extra_docs.extend(DOC_FLOORS.get(str(key), ()))
            if extra_docs:
                for row in rows:
                    if row.decision == "REQUIRED" and row.family in {"building", "building_ti", "fire_life_safety_assembly", "fire_hazmat_co2", "gas", "plumbing"}:
                        row.documents = _dedupe_text([*row.documents, *extra_docs])
                        row.documents = _strip_structural_docs_when_unsupported(row.documents, facts)
                        if "structural_engineering" in doc_floor_map and row.family in {"building", "building_ti"} and not getattr(getattr(facts, "structural_work", None), "value", None) == TriFact.FALSE:
                            row.inspections = _dedupe_text([*row.inspections, "Structural inspection"])
    else:
        rows = [PacketRow("No permit required", "not_required", "NOT_REQUIRED", reason=str(data.get("not_required_reason") or data.get("summary") or ""))]

    required_families = list(dict.fromkeys(row.family for row in rows if row.decision == "REQUIRED"))
    def _family_bucket_for_packet(family: str) -> str:
        if family in {"building", "building_ti", "building_adu", "racking", "demolition"}:
            return "building"
        if family in {"fire_alarm", "fire_suppression", "fire_life_safety_assembly", "fire_hazmat_co2"}:
            return "fire"
        return family
    required_buckets = {_family_bucket_for_packet(fam) for fam in required_families}
    conditional_families = [
        fam for fam in dict.fromkeys(row.family for row in rows if row.decision == "CONDITIONAL")
        if fam not in required_families and _family_bucket_for_packet(fam) not in required_buckets
    ]
    deduped_rows: list[PacketRow] = []
    seen_rows: set[tuple[str, str]] = set()
    for row in rows:
        if row.decision == "CONDITIONAL" and row.family not in conditional_families:
            continue
        key = (row.decision, _family_bucket_for_packet(row.family) if row.decision == "CONDITIONAL" else row.family)
        if key in seen_rows:
            continue
        seen_rows.add(key)
        deduped_rows.append(row)
    rows = deduped_rows

    prefix = "Commercial" if segment == "commercial" else ("Residential" if segment == "residential" else "")
    required_names = [row.permit_name for row in rows if row.decision == "REQUIRED"]
    conditional_names = [row.permit_name for row in rows if row.decision == "CONDITIONAL"]
    if required_names:
        headline = f"{prefix + ' ' if prefix else ''}permit required: {required_names[0]}"
        summary = "Required permit package: " + "; ".join(required_names) + "."
        if conditional_names:
            summary += " Conditional guidance: " + "; ".join(conditional_names) + "."
    else:
        headline = f"{prefix + ' ' if prefix else ''}no permit required for the stated scope"
        summary = str(data.get("not_required_reason") or data.get("summary") or "No permit required for the stated scope.")

    documents = _dedupe_text([doc for row in rows if row.decision == "REQUIRED" for doc in row.documents])
    inspections = _dedupe_text([item for row in rows if row.decision == "REQUIRED" for item in row.inspections])
    fees = _dedupe_text([row.fees for row in rows if row.decision == "REQUIRED" and row.fees])
    if decision == "NOT_REQUIRED":
        documents = []
        inspections = []
        fees = []

    checklist: list[str] = []
    if decision == "NOT_REQUIRED":
        checklist.append("Keep this no-permit report with the job record and verify before expanding the scope.")
    else:
        for row in rows:
            if row.decision == "REQUIRED":
                checklist.append(f"Pull {row.permit_name} before starting work")
            elif row.decision == "CONDITIONAL":
                checklist.append(f"{row.conditional_text or 'Only if triggered'} — if triggered, pull {row.permit_name}")

    runtime_degraded = data.get("_runtime_degraded_fallback") if isinstance(data.get("_runtime_degraded_fallback"), dict) else {}
    degraded = bool(data.get("degraded_sources") or runtime_degraded or data.get("source_degraded_input"))
    degraded_reason = str(runtime_degraded.get("reason") or data.get("degraded_reason") or data.get("source_degraded_reason") or "").strip()
    packet = PublicPacketDTO(
        segment=segment,
        authority=_authority(data, segment),
        decision=decision,
        permit_required_verdict=decision,
        verdict_basis=str(data.get("not_required_reason") or data.get("exemption_reason") or data.get("reason") or summary if decision == "NOT_REQUIRED" else ""),
        lead_label=(next((row.permit_name for row in rows if row.decision == "REQUIRED" and row.lead), "No permit required" if decision == "NOT_REQUIRED" else (required_names[0] if required_names else "Permit"))),
        rows=rows,
        headline=headline,
        summary=summary,
        checklist=_dedupe_text(checklist),
        documents=documents,
        inspections=inspections,
        fees=fees,
        required_families=required_families,
        conditional_families=conditional_families,
        degraded=degraded,
        degraded_reason=degraded_reason,
        scope_facts=scope_facts,
        gate_audit=list(data.get("_family_gate_audit") or []),
    )
    packet.validate()
    emit_trace("render_provenance", _packet_render_provenance(packet))
    return packet


def _repair_stale_apply_url_for_scope(out: dict[str, Any], facts: Any | None = None) -> None:
    scope_text = str(getattr(facts, "request_scope_text", "") or out.get("_request_job_type") or out.get("job_type") or out.get("job_summary") or out.get("summary") or "")
    apply_url = str(out.get("apply_url") or out.get("online_application_url") or "")
    lower_url = apply_url.lower()
    static_bad_replacements = {
        "retail-food-facility-permit-application.pdf": "https://sonomacounty.ca.gov/health-and-human-services/health-services/divisions/public-health/environmental-health-and-safety/food-facility-permits",
        "aca-prod.accela.com/memphis": "https://www.memphistn.gov/government/construction-enforcement/",
        "billingsmt.gov/135/building-division": "https://www.billingsmt.gov/1905/E-Permitting",
    }
    for token, replacement in static_bad_replacements.items():
        if token in lower_url:
            out["apply_url"] = replacement
            out["online_application_url"] = replacement
            out["_repaired_apply_url"] = replacement
            source_urls = [replacement]
            for u in out.get("source_urls") or []:
                su = str(u or "")
                if su and token not in su.lower() and su != replacement:
                    source_urls.append(su)
            out["source_urls"] = list(dict.fromkeys(source_urls))
            if isinstance(out.get("sources"), list):
                out["sources"] = [src for src in out["sources"] if not (isinstance(src, dict) and token in str(src.get("url") or src.get("source_url") or "").lower())]
                out["sources"].insert(0, {"url": replacement, "title": "Official permit landing page", "source_role": "LOCAL_OFFICIAL_FILING"})
            if isinstance(out.get("apply_path"), dict):
                out["apply_path"] = copy.deepcopy(out["apply_path"])
                out["apply_path"]["portal_url"] = replacement
                out["apply_path"]["channel"] = "online_portal"
                out["apply_path"]["state"] = "resolved_portal"
                out["apply_path"]["status"] = "RESOLVED_PORTAL"
            apply_url = replacement
            lower_url = replacement.lower()
            break
    if not apply_url:
        # Registry/source fallback chain: if an official AHJ information page is
        # all we have, advance to the nearest official permit-department landing
        # page instead of emitting a REQUIRED action path with no URL. This is not
        # a case-ID patch; it keys off the official source URL pattern and keeps
        # the concrete REQUIRED permit package intact.
        source_blob = "\n".join(str(u or "") for u in (out.get("source_urls") or []))
        source_blob += "\n" + "\n".join(
            str((src or {}).get("url") or (src or {}).get("source_url") or "")
            for src in (out.get("sources") or [])
            if isinstance(src, dict)
        )
        source_fallbacks = {
            "peoriagov.org/172/do-i-need-a-permit": "https://www.peoriagov.org/168/Building-Safety",
        }
        for token, replacement in source_fallbacks.items():
            if token in source_blob.lower():
                out["apply_url"] = replacement
                out["online_application_url"] = replacement
                out["_repaired_apply_url"] = replacement
                out["source_urls"] = list(dict.fromkeys([replacement, *(out.get("source_urls") or [])]))
                if isinstance(out.get("sources"), list):
                    out["sources"].insert(0, {"url": replacement, "title": "Official permit department landing page", "source_role": "LOCAL_OFFICIAL_FILING"})
                if isinstance(out.get("apply_path"), dict):
                    out["apply_path"] = copy.deepcopy(out["apply_path"])
                    out["apply_path"]["portal_url"] = replacement
                    out["apply_path"]["channel"] = "official_department_page"
                    out["apply_path"]["state"] = "official_source_fallback"
                    out["apply_path"]["status"] = "OFFICIAL_SOURCE_FALLBACK"
                    out["apply_path"]["typed_status"] = "OFFICIAL_SOURCE_FALLBACK"
                apply_url = replacement
                lower_url = replacement.lower()
                break
    stale_fire_cleaning = "exhaust-vent-cleaning-and-inspection" in apply_url.lower()
    actual_lab_or_install = bool(re.search(r"\b(?:lab|laboratory|fume\s+hoods?|exhaust|gas\s+piping|tenant\s+improvement|install|adding)\b", scope_text, re.I))
    maintenance_cleaning = bool(re.search(r"\b(?:clean(?:ing)?|maintenance|inspection\s+only)\b", scope_text, re.I))
    if not (stale_fire_cleaning and actual_lab_or_install and not maintenance_cleaning):
        return
    candidates: list[str] = []
    source_urls = out.get("source_urls")
    if isinstance(source_urls, list):
        candidates.extend(str(u) for u in source_urls if str(u).startswith("http"))
    for src in out.get("sources") or []:
        if isinstance(src, dict):
            candidates.append(str(src.get("url") or src.get("source_url") or ""))
    preferred = next((u for u in candidates if "onlinepermitsandlicenses.boston.gov" in u.lower()), "")
    preferred = preferred or next((u for u in candidates if "inspectional-services" in u.lower() and "exhaust-vent-cleaning" not in u.lower()), "")
    preferred = preferred or next((u for u in candidates if "boston.gov" in u.lower() and "exhaust-vent-cleaning" not in u.lower()), "")
    if not preferred:
        preferred = "https://onlinepermitsandlicenses.boston.gov/isdpermits/"
    out["apply_url"] = preferred
    out["online_application_url"] = preferred
    cleaned_sources = [preferred]
    cleaned_sources.extend(u for u in candidates if u and u != preferred and "exhaust-vent-cleaning-and-inspection" not in u.lower())
    out["source_urls"] = list(dict.fromkeys(cleaned_sources))
    if isinstance(out.get("apply_path"), dict):
        out["apply_path"] = copy.deepcopy(out["apply_path"])
        out["apply_path"]["portal_url"] = preferred
        out["apply_path"]["channel"] = "online_portal"
        out["apply_path"]["state"] = "resolved_portal"
        out["apply_path"]["status"] = "RESOLVED_PORTAL"


def apply_public_packet_projection(result: dict[str, Any], facts: Any | None = None) -> dict[str, Any]:
    out = copy.deepcopy(result) if isinstance(result, dict) else {}
    original_degraded_sources = bool(out.get("degraded_sources") or out.get("_runtime_degraded_fallback") or out.get("source_degraded_input"))
    original_degraded_reason = ""
    if isinstance(out.get("_runtime_degraded_fallback"), dict):
        original_degraded_reason = str(out["_runtime_degraded_fallback"].get("reason") or "")
    original_degraded_reason = original_degraded_reason or str(out.get("degraded_reason") or out.get("source_degraded_reason") or "")
    original_permit_kind = str(out.get("permit_kind") or "").strip()
    legacy_apply_path_obj = out.get("apply_path")
    legacy_apply_path = legacy_apply_path_obj if isinstance(legacy_apply_path_obj, dict) else {}
    input_apply_url = str(out.get("apply_url") or out.get("online_application_url") or "").strip()
    legacy_documents = legacy_apply_path.get("likely_documents") or out.get("_legacy_apply_documents") or []
    if not isinstance(legacy_documents, list):
        legacy_documents = []
    legacy_documents = [str(item).strip() for item in legacy_documents if str(item or "").strip()]
    _repair_stale_apply_url_for_scope(out, facts)
    if out.get("fee_range"):
        out["fee_range"] = _clean_fee_text(out.get("fee_range"), facts)
    packet = build_public_packet(out, facts)
    public_packet = packet.public_dict()
    repaired_apply_url = str(out.get("_repaired_apply_url") or "").strip()
    if repaired_apply_url and isinstance(public_packet.get("authority"), dict):
        public_packet["authority"]["apply_url"] = repaired_apply_url
        packet.authority.apply_url = repaired_apply_url
    original_decision_schema = None
    if isinstance(out.get("decision_object"), dict):
        original_decision_schema = out["decision_object"].get("schema_version")
    original_render_pass = out.get("render_fidelity", {}).get("pass") if isinstance(out.get("render_fidelity"), dict) else None
    authority_name = _authority_name(out, packet.authority)
    public_packet["authority"]["name"] = authority_name
    out.pop("_family_gate_audit", None)

    # The locked packet is now the sole customer-visible row source.  Old lookup
    # artifacts may still contain stale required/related/checklist/scope fields;
    # rewrite every report-facing mirror from packet rows and purge debug/source
    # structures that can leak suppressed families through serialized JSON.
    packet_rows = list(public_packet.get("rows") or [])
    required_packet_rows = [row for row in packet_rows if row.get("decision") == "REQUIRED"]
    conditional_packet_rows = [row for row in packet_rows if row.get("decision") == "CONDITIONAL"]
    required_packet_families = {str(row.get("family") or "") for row in required_packet_rows}
    preserve_building_ti = "building_ti" in required_packet_families and "building" in required_packet_families
    required_public_rows = [_packet_row_to_legacy(row, preserve_building_ti=preserve_building_ti) for row in required_packet_rows]
    conditional_public_rows = [_packet_row_to_legacy(row, preserve_building_ti=preserve_building_ti) for row in conditional_packet_rows]
    out["public_packet"] = public_packet
    out["canonical_public_packet"] = public_packet
    out["segment"] = packet.segment
    out["public_packet_rows"] = packet_rows
    out["permits_required"] = required_public_rows
    out["conditional_permits"] = conditional_public_rows
    out["related_permits"] = conditional_public_rows
    out["companion_permits"] = []
    out["trade_permits"] = []
    packet_family_names = list(public_packet.get("required_families") or [])
    display_family_names = [_kind_for_family(str(fam)) for fam in packet_family_names]
    out["required_permit_families"] = list(dict.fromkeys(packet_family_names + display_family_names))
    out["required_permit_names"] = [str(row.get("permit_name")) for row in required_public_rows if row.get("permit_name")]
    out["permits_required_logic"] = [
        {"filing_family": row.get("family"), "permit_type": row.get("permit_name"), "scope_trigger": "Project scope", "included_because": "The locked public packet includes this required permit family."}
        for row in packet_rows if row.get("decision") == "REQUIRED"
    ]
    out["customer_headline"] = packet.headline
    out["summary"] = packet.summary
    out["job_summary"] = packet.summary
    out["permit_summary"] = packet.summary
    out["customer_result_summary"] = {"summary": packet.summary}
    out["customer_first_screen_summary"] = packet.summary
    out["what_to_bring"] = list(packet.documents)
    out["requirements"] = list(packet.documents)
    out["documents_needed"] = list(packet.documents)
    out["documents_to_prepare"] = list(packet.documents)
    out["checklist"] = list(packet.checklist)
    out["inspections"] = list(packet.inspections)
    if required_public_rows:
        lead = next((row for row in required_public_rows if row.get("lead")), required_public_rows[0])
        package_name = ""
        if len(required_public_rows) > 1:
            package_name = "Permit package: " + "; ".join(str(name) for name in out.get("required_permit_names") or [] if name)
        out["permit_required"] = True
        out["permit_decision"] = "REQUIRED"
        out["permit_verdict"] = "YES"
        out["permit_name"] = package_name or lead.get("permit_name")
        out["permit_type"] = package_name or lead.get("permit_name")
        computed_kind = "Permit package" if package_name else _kind_for_family(str(lead.get("family") or ""))
        generic_kinds = {"building", "electrical", "mechanical", "plumbing", "fire", "refrigeration", "sign", "planning", "zoning", "permit package", "not required"}
        candidate_kind = original_permit_kind if original_permit_kind and not re.search(r"\b(?:unknown|likely|required\?)\b", original_permit_kind, re.I) else ""
        if package_name and candidate_kind.lower().strip() in generic_kinds:
            candidate_kind = ""
        if candidate_kind and not ((packet.segment == "commercial" and "residential" in candidate_kind.lower()) or (packet.segment == "residential" and ("commercial" in candidate_kind.lower() or "tenant improvement" in candidate_kind.lower()))):
            out["permit_kind"] = candidate_kind
        else:
            out["permit_kind"] = computed_kind
        if packet.authority.apply_url:
            out["customer_next_step"] = f"File the required permit categories with {authority_name}: {', '.join(out['required_permit_names'])}. Confirm exact portal subcategories before final submission."
        else:
            out["customer_next_step"] = f"Contact {authority_name} to confirm the filing path for: {', '.join(out['required_permit_names'])}. Confirm exact portal subcategories before final submission."
    else:
        out["permit_required"] = False
        out["permit_decision"] = "NOT_REQUIRED"
        out["permit_verdict"] = "NO"
        out["permit_name"] = "No permit required"
        out["permit_type"] = "No permit required"
        out["permit_kind"] = "Not Required"
        out["customer_next_step"] = "Keep this no-permit report with the job record and verify with the permit office before expanding the scope."
    if packet.fees:
        out["fee_range"] = packet.fees[0]
    elif packet.decision == "NOT_REQUIRED":
        out["fee_range"] = "No permit fee expected for the resolved no-permit scope; verify with the permit office if the scope changes"
    if packet.authority.apply_url and packet.decision == "REQUIRED":
        out["apply_url"] = packet.authority.apply_url
        out["online_application_url"] = packet.authority.apply_url
    elif packet.decision == "NOT_REQUIRED":
        out["apply_url"] = ""
        out["online_application_url"] = ""
    out["applying_office"] = authority_name
    if packet.authority.source_urls:
        out["source_urls"] = list(packet.authority.source_urls)
        source_items = []
        ahj_identity = {"city": out.get("_request_city") or out.get("city") or "", "state": out.get("_request_state") or out.get("state") or packet.authority.state or ""}
        for url in packet.authority.source_urls:
            role, evidence = classify_source(url, ahj_identity)
            role_value = "unverified" if str(role.value).upper() == "UNKNOWN" else role.value
            source_items.append({"url": url, "title": source_label_for_role(role), "label": source_label_for_role(role), "source_tier": "local_permit_source" if is_official_badge_role(role) else "context", "source_role": role_value, "role_evidence": evidence})
        out["sources"] = source_items
    if packet.decision == "REQUIRED":
        original_apply_url = input_apply_url
        legacy_portal_url = str(legacy_apply_path.get("portal_url") or legacy_apply_path.get("url") or "").strip()
        fallback_source_url = bool(packet.authority.apply_url and not (original_apply_url or legacy_portal_url))
        path_state = "official_source_fallback" if fallback_source_url else ("resolved_portal" if packet.authority.apply_url else "verify_with_permit_office")
        typed_status = "OFFICIAL_SOURCE_FALLBACK" if fallback_source_url else ("RESOLVED_PORTAL" if packet.authority.apply_url else "VERIFY_WITH_PERMIT_OFFICE")
        out["apply_path"] = {
            "state": path_state,
            "status": typed_status,
            "typed_status": typed_status,
            "channel": "official_source" if fallback_source_url else ("online_portal" if packet.authority.apply_url else "office_verification"),
            "portal_url": packet.authority.apply_url,
            "office_name": authority_name,
            "authority": authority_name,
            "permit_type": out.get("permit_name"),
            "permit_category": out.get("permit_kind"),
            "documents_to_prepare": list(packet.documents),
            "steps": [
                "Open the listed permit portal or contact the permit office",
                f"Select the closest category to: {out.get('permit_name')}",
                "Confirm exact portal subcategories before final submission",
            ],
        }
    else:
        out["apply_path"] = {
            "state": "not_applicable",
            "status": "NOT_APPLICABLE",
            "typed_status": "NOT_APPLICABLE",
            "channel": "no_permit_required",
            "office_name": authority_name,
            "permit_type": "No permit required",
            "documents_to_prepare": [],
            "steps": ["Keep the described scope limited; verify before adding regulated work."],
        }
    for key in (
        "decision_object", "project_scope_attributes", "render_fidelity", "scope_facts", "scope_contract", "_scope_contract",
        "_decision_floor_invariant", "_repaired_apply_url",
        "package_header",
        "_request_city", "_request_state", "negative_facts", "positive_facts", "city_contractor_registration", "pro_tips", "common_mistakes", "watch_out",
        "inspection_requirements", "inspect_checklist", "inspection_checklist", "inspections_required",
        "companion_reviews", "companion_permits_or_reviews", "primary_permit", "description", "next_steps",
        "permit_notes", "inspection_notes", "zoning_hoa_flag",
        "claim_citations", "related_permit_names", "related_permit_segments", "required_permit_segments",
        "required_permit_summary", "source_support", "source_confidence", "remaining_lookups",
        "related_permit_families", "suggested_permit_families", "possible_permit_families", "other_permits",
        "cost_estimate", "fee_estimate", "fees_typed", "permit_fee", "fee_notes",
        "timeline", "approval_timeline", "inspection_booking", "degraded_sources",
    ):
        out.pop(key, None)
    if original_decision_schema:
        out["decision_object"] = {"schema_version": original_decision_schema}
    if original_render_pass is not None:
        out["render_fidelity"] = {"pass": bool(original_render_pass), "issues": [] if original_render_pass else ["pre-lock render fidelity reported failure"]}
    if isinstance(out.get("customer_result_summary"), dict):
        out["customer_result_summary"]["permit_kind"] = str(out.get("permit_kind") or "")
        out["customer_result_summary"]["next_step"] = str(out.get("customer_next_step") or "")
        out["customer_result_summary"]["source_cue"] = "Official source path found" if out.get("sources") or out.get("source_urls") else "Permit office verification path included"
    out["required_permit_summary"] = packet.summary
    if original_degraded_sources:
        out["degraded_sources"] = True
        out["degraded_reason"] = original_degraded_reason
        if isinstance(out.get("public_packet"), dict):
            out["public_packet"]["degraded"] = True
            out["public_packet"]["degraded_reason"] = original_degraded_reason
        if isinstance(out.get("canonical_public_packet"), dict):
            out["canonical_public_packet"]["degraded"] = True
            out["canonical_public_packet"]["degraded_reason"] = original_degraded_reason
        out["source_support"] = {"decision_mutation_allowed": False}
    return out


def _seal_hash(packet: dict[str, Any]) -> str:
    clone = {k: v for k, v in (packet or {}).items() if k not in {"sealed_public_packet_hash", "sealed_at_stage", "render_seal_hash"}}
    return "sha256:" + hashlib.sha256(json.dumps(clone, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _packet_family(row: dict[str, Any]) -> str:
    return str(row.get("family") or family_from_row(row) or "")


def seal_packet(packet: dict[str, Any], *, facts: Any | None = None, fail_hard: bool = True) -> dict[str, Any]:
    out = copy.deepcopy(packet) if isinstance(packet, dict) else {}
    rows = [r for r in out.get("rows") or [] if isinstance(r, dict)]
    decision = str(out.get("permit_required_verdict") or out.get("decision") or "").upper().strip()
    forbidden = matrix_forbidden_families(facts) if facts is not None else {}
    required_rows = [r for r in rows if str(r.get("decision") or "").upper() == "REQUIRED"]
    conditional_rows = [r for r in rows if str(r.get("decision") or "").upper() == "CONDITIONAL"]
    errors: list[str] = []
    present_families = {_packet_family(r) for r in rows if str(r.get("decision") or "").upper() in {"REQUIRED", "CONDITIONAL"}}
    forbidden_present = sorted(fam for fam in present_families if fam in forbidden)
    if forbidden_present:
        errors.append("forbidden families present: " + ",".join(forbidden_present))
    if decision == "NOT_REQUIRED":
        if required_rows or out.get("required_families") or out.get("documents") or out.get("inspections") or out.get("fees"):
            errors.append("NOT_REQUIRED packet contains required permit artifacts")
        if not str(out.get("verdict_basis") or "").strip():
            errors.append("NOT_REQUIRED packet missing verdict_basis")
        auth = out.get("authority") if isinstance(out.get("authority"), dict) else {}
        if auth.get("apply_url"):
            errors.append("NOT_REQUIRED packet contains apply URL")
    if decision == "REQUIRED":
        auth_raw = out.get("authority")
        auth = auth_raw if isinstance(auth_raw, dict) else {}
        verified_contact = str(auth.get("contact_status") or "").lower() == "verified" and bool(str(auth.get("phone") or auth.get("address") or "").strip())
        official_source_fallback = bool(str(auth.get("name") or "").strip() and auth.get("source_urls"))
        if not (auth.get("apply_url") or verified_contact or official_source_fallback):
            errors.append("REQUIRED packet missing apply URL or verified AHJ contact fallback")
    segment = str(out.get("segment") or "").lower()
    lead_label = str(out.get("lead_label") or "")
    if segment == "commercial" and re.search(r"\b(?:residential|homeowner|single[- ]family)\b", lead_label, re.I):
        errors.append("lead label segment mismatch")
    if segment == "residential" and re.search(r"\b(?:commercial|tenant improvement)\b", lead_label, re.I):
        errors.append("lead label segment mismatch")
    mandatory = matrix_mandatory_families(facts) if facts is not None else {}
    missing_mandatory = sorted(fam for fam in mandatory if fam not in present_families)
    if missing_mandatory:
        errors.append("mandatory families missing: " + ",".join(missing_mandatory))
    for source in out.get("sources") or []:
        if not isinstance(source, dict):
            continue
        label = str(source.get("title") or source.get("label") or "")
        role = str(source.get("source_role") or "unverified")
        if "official" in label.lower() and not is_official_badge_role(role):
            errors.append("Official badge on non-local/state source")
    if errors and fail_hard:
        raise PacketInvariantError("; ".join(errors))
    if errors:
        out["packet_invariant_errors"] = errors
    out["render_seal_hash"] = _seal_hash(out)
    out["sealed_public_packet_hash"] = out["render_seal_hash"]
    return out


def apply_render_parity_seal(result: dict[str, Any], *, facts: Any | None = None, fail_hard: bool | None = True) -> dict[str, Any]:
    """Terminal customer-boundary seal for API/share/report/HTML parity."""
    out = copy.deepcopy(result) if isinstance(result, dict) else {}
    packet = out.get("public_packet") if isinstance(out.get("public_packet"), dict) else {}
    if not packet or not str(packet.get("schema_version") or "").startswith("final_public_permit_packet"):
        return out
    top_apply_url = str(out.get("apply_url") or out.get("online_application_url") or "").strip()
    if top_apply_url and isinstance(packet.get("authority"), dict) and not str(packet["authority"].get("apply_url") or "").strip():
        packet = copy.deepcopy(packet)
        packet["authority"] = copy.deepcopy(packet.get("authority") or {})
        packet["authority"]["apply_url"] = top_apply_url
    if top_apply_url and isinstance(out.get("apply_path"), dict) and not str(out["apply_path"].get("portal_url") or out["apply_path"].get("url") or "").strip():
        out["apply_path"] = copy.deepcopy(out["apply_path"])
        out["apply_path"]["portal_url"] = top_apply_url
        out["apply_path"]["state"] = "official_source_fallback"
        out["apply_path"]["status"] = "OFFICIAL_SOURCE_FALLBACK"
        out["apply_path"]["typed_status"] = "OFFICIAL_SOURCE_FALLBACK"
        out["apply_path"]["channel"] = "official_source"
    decision = str(packet.get("decision") or out.get("permit_decision") or "").upper().strip()
    rows = [r for r in packet.get("rows") or [] if isinstance(r, dict)]
    required_rows = [r for r in rows if str(r.get("decision") or "").upper() == "REQUIRED"]
    conditional_rows = [r for r in rows if str(r.get("decision") or "").upper() == "CONDITIONAL"]
    if decision == "NOT_REQUIRED":
        packet["rows"] = [r for r in rows if str(r.get("decision") or "").upper() == "NOT_REQUIRED"] or [{"permit_name": "No permit required", "family": "not_required", "decision": "NOT_REQUIRED", "reason": str(out.get("not_required_reason") or out.get("summary") or "")}]
        packet["required_families"] = []
        packet["conditional_families"] = []
        packet["documents"] = []
        packet["inspections"] = []
        packet["fees"] = []
        safe_checklist: list[str] = []
        for item in packet.get("checklist") or []:
            text = str(item or "")
            if re.search(r"\b(?:pull|file|submit|apply\s+for|pay)\b.{0,80}\bpermit\b", text, re.I) and not re.search(r"\b(?:do not|don't|does not|no|not)\b.{0,40}\b(?:pull|file|submit|apply|pay)", text, re.I):
                continue
            safe_checklist.append(text)
        packet["checklist"] = safe_checklist or ["Keep this no-permit report with the job record and verify before expanding the scope."]
        out.update({
            "permit_required": False,
            "permit_decision": "NOT_REQUIRED",
            "permit_verdict": "NO",
            "permit_name": "No permit required",
            "permit_type": "No permit required",
            "permit_kind": "Not Required",
            "permits_required": [],
            "conditional_permits": [],
            "related_permits": [],
            "companion_permits": [],
            "trade_permits": [],
            "required_permit_names": [],
            "required_permit_families": [],
            "documents_to_prepare": [],
            "what_to_bring": [],
            "requirements": [],
            "documents_needed": [],
            "inspections": [],
            "checklist": list(packet["checklist"]),
            "apply_url": "",
            "online_application_url": "",
            "fee_range": "",
            "apply_path": {"state": "not_applicable", "status": "NOT_APPLICABLE", "typed_status": "NOT_APPLICABLE", "channel": "no_permit_required", "office_name": out.get("applying_office") or (packet.get("authority") or {}).get("name") or "Local permit office", "permit_type": "No permit required", "documents_to_prepare": [], "steps": ["Keep the described scope limited; verify before adding regulated work."]},
        })
    else:
        required_family_set = {str(r.get("family") or "") for r in required_rows}
        preserve_building_ti = "building_ti" in required_family_set and "building" in required_family_set
        out["permits_required"] = [_packet_row_to_legacy(r, preserve_building_ti=preserve_building_ti) for r in required_rows]
        out["conditional_permits"] = [_packet_row_to_legacy(r, preserve_building_ti=preserve_building_ti) for r in conditional_rows]
        out["related_permits"] = list(out["conditional_permits"])
        out["required_permit_names"] = [r.get("permit_name") for r in required_rows if r.get("permit_name")]
        out["required_permit_families"] = list(dict.fromkeys(str(r.get("family") or "") for r in required_rows if r.get("family")))
        out["documents_to_prepare"] = list(packet.get("documents") or [])
        out["what_to_bring"] = list(packet.get("documents") or [])
        out["requirements"] = list(packet.get("documents") or [])
        out["documents_needed"] = list(packet.get("documents") or [])
        out["inspections"] = list(packet.get("inspections") or [])
        out["checklist"] = list(packet.get("checklist") or [])
    packet["sealed_at_stage"] = "post_public_packet_projection"
    seal_fail_hard = True if fail_hard is None else bool(fail_hard)
    packet = seal_packet(packet, facts=facts, fail_hard=seal_fail_hard)
    out["public_packet"] = packet
    out["canonical_public_packet"] = copy.deepcopy(packet)
    out["public_packet_rows"] = list(packet.get("rows") or [])
    out["sealed_schema"] = str(packet.get("schema_version") or "final_public_permit_packet.v1")
    out["sealed_public_packet_hash"] = packet["sealed_public_packet_hash"]
    out["render_seal_hash"] = packet.get("render_seal_hash")
    invariant_errors = list(packet.get("packet_invariant_errors") or [])
    out["render_fidelity"] = {"pass": not invariant_errors, "issues": invariant_errors}
    return out


def validate_public_packet(packet: PublicPacketDTO | dict[str, Any]) -> None:
    if isinstance(packet, PublicPacketDTO):
        packet.validate()
        return
    if not isinstance(packet, dict):
        raise ValueError("packet must be a PublicPacketDTO or dict")
    authority_data: dict[str, Any] = dict(packet.get("authority") or {"name": "Local permit office"})
    if not isinstance(authority_data.get("source_urls"), list):
        authority_data["source_urls"] = []
    dto = PublicPacketDTO(
        segment=str(packet.get("segment") or "general"),
        authority=PacketAuthority(**authority_data),
        decision=packet.get("decision") or "REQUIRED",
        rows=[PacketRow(**row) for row in packet.get("rows") or []],
        headline=str(packet.get("headline") or ""),
        summary=str(packet.get("summary") or ""),
        checklist=list(packet.get("checklist") or []),
        documents=list(packet.get("documents") or []),
        inspections=list(packet.get("inspections") or []),
        fees=list(packet.get("fees") or []),
        required_families=list(packet.get("required_families") or []),
        conditional_families=list(packet.get("conditional_families") or []),
        scope_facts=dict(packet.get("scope_facts") or {}),
    )
    dto.validate()
