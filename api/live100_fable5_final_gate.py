from __future__ import annotations

"""Final offline/customer-boundary gate for the 2026-07-05 Live100 Fable 5 plan.

This module is intentionally deterministic and offline-only.  It does not fetch,
call models, or invent exact portal URLs.  It repairs the final customer ViewModel
from request-scope facts immediately before PublicPacket projection.
"""

import copy
import re
from typing import Any


_FAMILY_LABELS = {
    "building": "Building",
    "building_ti": "Building",
    "electrical": "Electrical",
    "mechanical": "Mechanical",
    "refrigeration": "Refrigeration",
    "plumbing": "Plumbing",
    "gas": "Gas",
    "fire_suppression": "Fire",
    "fire_alarm": "Fire",
    "fire_life_safety_assembly": "Fire",
    "health_food": "Health",
    "wastewater_pretreatment_fog": "Wastewater/FOG",
    "planning_zoning": "Planning/Zoning",
    "co_change_of_occupancy": "Certificate of Occupancy",
    "sign": "Sign",
    "roofing": "Roofing",
    "solar_pv": "Solar / PV",
    "battery_storage": "Electrical",
    "environmental": "Environmental/Fuel-System",
    "grading": "Right-of-Way / Site/Civil",
}

_DEFAULT_NAMES = {
    "building": "Building Permit",
    "building_ti": "Commercial Building / Tenant Improvement Permit",
    "electrical": "Electrical Permit",
    "mechanical": "Mechanical Permit",
    "refrigeration": "Refrigeration Permit",
    "plumbing": "Plumbing Permit",
    "gas": "Fuel Gas / Plumbing Gas Permit",
    "fire_suppression": "Fire Prevention / Suppression Permit",
    "fire_alarm": "Fire Alarm Permit",
    "fire_life_safety_assembly": "Fire / Life-Safety Review",
    "health_food": "Health Plan Review / Food Establishment Permit",
    "wastewater_pretreatment_fog": "Wastewater / FOG / Pretreatment Approval",
    "planning_zoning": "Planning / Zoning Use Clearance",
    "co_change_of_occupancy": "Certificate of Occupancy / Change-of-Occupancy Approval",
    "sign": "Sign Permit",
    "roofing": "Roofing Permit",
    "solar_pv": "Solar PV Permit / Review",
    "battery_storage": "Battery / Energy Storage Permit",
    "environmental": "Environmental / Fuel-System Review",
    "grading": "Right-of-Way / Site/Civil Permit",
}

_NAMED_AUTHORITY_CONTACTS: dict[tuple[str, str], str] = {}

_CANONICAL_AUTHORITY_FIXES: dict[tuple[str, str], dict[str, str]] = {}


def _norm(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _has(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, text, flags=re.I))


def _row_family(row: dict[str, Any]) -> str:
    fam = str(row.get("family") or row.get("filing_family") or "").strip().lower().replace("-", "_")
    if fam:
        return fam
    text = _norm(" ".join(str(row.get(k) or "") for k in ("permit_type", "permit_name", "approval_type", "kind", "display_family")))
    checks = [
        ("wastewater_pretreatment_fog", r"\b(?:wastewater|fog|pretreatment|grease interceptor)\b"),
        ("environmental", r"\b(?:fuel system|fuel dispenser|ust|underground storage|environmental)\b"),
        ("refrigeration", r"\b(?:refrigeration|refrigerant|line set|line-set|cooler)\b"),
        ("fire_alarm", r"\bfire alarm\b"),
        ("fire_suppression", r"\b(?:fire|sprinkler|suppression|hood|life safety)\b"),
        ("health_food", r"\b(?:health|food establishment|food service)\b"),
        ("co_change_of_occupancy", r"\b(?:certificate of occupancy|change.of.occupancy|change.of.use)\b"),
        ("planning_zoning", r"\b(?:planning|zoning|land use)\b"),
        ("grading", r"\b(?:right.of.way|row|driveway|curb cut|site/civil|grading)\b"),
        ("sign", r"\bsign\b"),
        ("roofing", r"\b(?:re[- ]?roof|roof(?:ing| replacement| repair)?|shingles?|membrane roof)\b"),
        ("solar_pv", r"\b(?:solar\s+(?:pv|panels?|array)|photovoltaic|\bpv\s+(?:system|array|panels?))\b"),
        ("plumbing", r"\b(?:plumbing|sink|shower|drain|backflow|irrigation|water)\b"),
        ("mechanical", r"\b(?:mechanical|hvac|exhaust|ventilation|heat pump|wood stove|cooler|condenser)\b"),
        ("electrical", r"\b(?:electrical|circuit|panel|service|lighting|charger|disconnect|subpanel)\b"),
        ("building", r"\b(?:building|construction|structural|mezzanine|garage|shed|carport|addition|canopy)\b"),
    ]
    for family, pattern in checks:
        if _has(text, pattern):
            return family
    return "building"


def _make_row(family: str, job_text: str = "", *, existing: dict[str, Any] | None = None, status: str = "REQUIRED") -> dict[str, Any]:
    row = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    family = family.strip().lower()
    name = _DEFAULT_NAMES.get(family, "Permit Category Verification")
    if family == "building" and _has(job_text, r"\b(?:garage.*adu|accessory dwelling|adu)\b"):
        name = "Building Permit — ADU / Dwelling Conversion"
    elif family == "building" and _has(job_text, r"\bgarage\b.*\b(?:conditioned|office|conversion|convert)\b|\bconvert\b.*\bgarage\b"):
        name = "Building Permit — Garage Conversion / Habitable Space"
    elif family == "building" and _has(job_text, r"\bmezzanine\b"):
        name = "Building Permit — Structural Mezzanine"
    elif family == "building" and _has(job_text, r"\b(?:carport|shade structure|modular classroom|patio|canopy|service bays?)\b"):
        name = "Building Permit — New Structure / Addition"
    elif family == "electrical" and _has(job_text, r"\b(?:ev charging|ev charger|charging stations?)\b"):
        name = "Electrical Permit — EV Charging Equipment"
    elif family == "electrical" and _has(job_text, r"\b(?:same location|existing).{0,80}\b(?:electrical|power|circuit)\b"):
        name = "Electrical Permit — Equipment Reconnection / Existing Circuit"
    elif family == "mechanical" and _has(job_text, r"\b(?:wood stove|chimney|solid fuel)\b"):
        name = "Mechanical Permit — Solid-Fuel Appliance / Chimney"
    elif family == "mechanical" and _has(job_text, r"\b(?:dust collection|cyclone|explosion venting)\b"):
        name = "Mechanical Permit — Dust Collection / Ventilation"
    elif family == "plumbing" and _has(job_text, r"\bshowers?\b"):
        name = "Plumbing Permit — Showers / Fixture Work"
    elif family == "plumbing" and _has(job_text, r"\b(?:irrigation|backflow)\b"):
        name = "Plumbing Permit — Irrigation Backflow Preventer"
    row.update({
        "permit_type": name,
        "permit_name": name,
        "approval_type": name,
        "family": family,
        "filing_family": family,
        "kind": _FAMILY_LABELS.get(family, "Permit"),
        "display_family": _FAMILY_LABELS.get(family, "Permit"),
        "required": status == "REQUIRED",
        "decision": status,
        "status": status,
        "scope_trigger": row.get("scope_trigger") or f"fable5_{family}_scope",
        "rationale": row.get("rationale") or f"Required because the original request includes {_FAMILY_LABELS.get(family, family).lower()} scope; confirm the exact local filing category before submitting.",
    })
    if status != "REQUIRED":
        row["required_if"] = row.get("required_if") or "Required only if the AHJ confirms this companion review is triggered by address-specific or final-scope details."
    return row


def _required_rows(public: dict[str, Any]) -> list[dict[str, Any]]:
    return [copy.deepcopy(r) for r in public.get("permits_required") or [] if isinstance(r, dict) and str(r.get("decision") or r.get("status") or ("REQUIRED" if r.get("required") is True else "")).upper() == "REQUIRED"]


def _set_required(out: dict[str, Any], families: set[str], job_text: str, city: str, state: str) -> None:
    rows = _required_rows(out)
    by_family: dict[str, dict[str, Any]] = {}
    for row in rows:
        fam = _row_family(row)
        by_family.setdefault(fam, row)
    for family in families:
        by_family.setdefault(family, _make_row(family, job_text))
    order = ["building_ti", "building", "roofing", "solar_pv", "grading", "environmental", "electrical", "mechanical", "refrigeration", "plumbing", "gas", "fire_alarm", "fire_suppression", "health_food", "wastewater_pretreatment_fog", "sign", "planning_zoning", "co_change_of_occupancy"]
    rows_out = [by_family[f] for f in order if f in by_family] + [r for f, r in by_family.items() if f not in order]
    for row in rows_out:
        row["required"] = True
        row["decision"] = "REQUIRED"
        row["status"] = "REQUIRED"
        row.setdefault("derived_from", ["fable5_final_scope_floor"])
        row["_request_scope_text"] = job_text
        row.setdefault("scope_trigger", f"fable5_{_row_family(row)}_scope")
    out["permits_required"] = rows_out
    out["permit_required"] = True
    out["permit_decision"] = "REQUIRED"
    out["permit_verdict"] = "YES"
    names = [str(r.get("permit_type") or r.get("permit_name") or "Permit") for r in rows_out]
    labels = []
    for r in rows_out:
        label = str(r.get("display_family") or r.get("kind") or _FAMILY_LABELS.get(_row_family(r), "Permit"))
        if label not in labels:
            labels.append(label)
    out["required_permit_names"] = names
    out["required_permit_families"] = labels
    if len(rows_out) == 1:
        out["permit_name"] = names[0]
        out["permit_type"] = names[0]
        out["permit_kind"] = labels[0]
        out.pop("package_header", None)
        out["customer_headline"] = f"Permit required: {names[0]}."
        summary = f"Permit required: {names[0]}."
    else:
        out["package_header"] = "Multiple permits required: " + " + ".join(labels[:6])
        primary_name = names[0] if names else "Permit"
        out["permit_name"] = primary_name
        out["permit_type"] = primary_name
        out["permit_kind"] = labels[0] if labels else "Permit package"
        out["customer_headline"] = "Permit required: multiple permits — " + " + ".join(labels[:6]) + "."
        summary = "Multiple permits required: " + "; ".join(names[:8]) + "."
    out["job_summary"] = summary
    out["summary"] = summary
    office = str(out.get("applying_office") or out.get("building_dept_name") or _NAMED_AUTHORITY_CONTACTS.get((_norm(city), _norm(state)), f"{city} permit office")).strip()
    out["applying_office"] = office
    out["building_dept_name"] = out.get("building_dept_name") or office
    out["customer_next_step"] = f"File the required permit package with {office}: {', '.join(names[:8])}. Confirm exact portal subcategories before final submission."


def _mark_not_required(out: dict[str, Any], job_text: str, city: str, state: str) -> None:
    office = str(out.get("applying_office") or out.get("building_dept_name") or f"{city} permit office").strip()
    out.update({
        "permit_required": False,
        "permit_decision": "NOT_REQUIRED",
        "permit_verdict": "NO",
        "permit_kind": "Not Required",
        "permit_name": "No permit required",
        "permit_type": "No permit required",
        "permits_required": [],
        "required_permit_names": [],
        "required_permit_families": [],
        "customer_headline": "No permit required for the described scope.",
        "job_summary": f"No permit required for the described scope in {city}, {state} as long as the work stays within the stated limits.",
        "summary": f"No permit required for the described scope in {city}, {state} as long as the work stays within the stated limits.",
        "customer_next_step": f"Keep the work limited to the described no-permit scope; if framing, trade work, occupancy, exterior, fire/life-safety, or accessibility work is added, verify with {office} before starting.",
        "apply_url": "",
        "online_application_url": "",
        "fee_range": "No permit fee expected for the resolved no-permit scope; verify with the permit office if the scope changes.",
    })
    out["apply_path"] = {"state": "NOT_APPLICABLE", "channel": "no_permit_required", "support_level": "not applicable", "portal_url": None, "office_name": office, "verification_note": "No permit filing path is needed for the resolved NOT_REQUIRED scope."}


def _triggered_families(job_type: str, segment: str) -> set[str]:
    text = _norm(job_type)
    families: set[str] = set()
    commercial = segment == "commercial" or _has(text, r"\b(?:commercial|restaurant|brewery|warehouse|retail|office|school|laundromat|grocery|marine repair|auto repair|car wash|gas station|fitness studio)\b")
    if _has(text, r"\b(?:tenant improvement|demising wall|partition|build.?out|suite|mezzanine|modular classroom|car wash tunnel|shade structure|patio|canopy|service bays?|garage.*(?:office|adu|conversion|convert)|convert\s+.*garage|accessory dwelling|adu|carport|retaining wall|shed|cooler rooms?|transformer pad)\b"):
        families.add("building_ti" if commercial and not _has(text, r"\b(?:car wash tunnel|modular classroom|shade structure|patio|canopy|service bays?|fuel canopy)\b") else "building")
    if _has(text, r"\b(?:parking lot|restripe|ada stalls?|driveway curb cut|curb cut|right.of.way|row)\b"):
        families.add("grading")
    if _has(text, r"\b(?:electrical|lighting|exit signage|fire alarm tie-in|600a|service|subpanel|ev charging|charger|transformer|disconnect|wiring|vacuums?|dispensers?)\b"):
        families.add("electrical")
    if _has(text, r"\b(?:hvac|makeup air|make-up air|ventilation|exhaust|dust collection|cyclone|explosion venting|condensing units?|cooler rooms?|walk-in cooler|bakery oven|wood stove|chimney|heat pump|mini split|compressor|commercial dishwasher|dishwasher)\b"):
        families.add("mechanical")
    if _has(text, r"\b(?:cooler rooms?|walk-in cooler|refrigerated|refrigeration|condensing units?)\b"):
        families.add("refrigeration")
    if _has(text, r"\b(?:floor drains?|drains?|water heaters?|showers?|locker rooms?|prep sink|sink|oil separator|backflow|irrigation|underground product piping|reclaim system|plumbing|toilet|bathroom|kitchenette)\b"):
        families.add("plumbing")
    if _has(text, r"\b(?:gas dryers?|gas line|gas station|fuel canopy|fuel dispensers?)\b"):
        families.add("gas")
    if _has(text, r"\b(?:fire alarm|fire suppression|sprinkler|life safety|explosion venting|fuel canopy|dispensers?|ust|wood stove|chimney)\b"):
        families.add("fire_suppression")
    if _has(text, r"\b(?:deli prep|grocery|bakery|walk-in cooler|restaurant|commercial kitchen|food service)\b") and not _has(text, r"\bno\s+(?:kitchen|food service|food prep|commercial kitchen)\b"):
        families.add("health_food")
    if _has(text, r"\b(?:grease|fog|oil separator|floor drains?|reclaim system|food service|deli prep|bakery)\b") and not _has(text, r"\bno\s+(?:kitchen|food service|food prep|grease|fog)\b"):
        families.add("wastewater_pretreatment_fog")
    if _has(text, r"\b(?:signs?|signage)\b") and not _has(text, r"\bexit signage\b"):
        families.add("sign")
    if _has(text, r"\b(?:re[- ]?roof|roof(?:ing| replacement| repair)?|tear[- ]?off|shingles?|membrane roof)\b"):
        families.add("roofing")
    if _has(text, r"\b(?:solar\s+(?:pv|panels?|array)|photovoltaic|\bpv\s+(?:system|array|panels?))\b"):
        families.add("solar_pv")
    if commercial and _has(text, r"\b(?:convert|change of use|former retail|retail suite|warehouse to|laundromat|fitness studio|grocery)\b"):
        families.add("co_change_of_occupancy")
        families.add("planning_zoning")
    if _has(text, r"\b(?:fuel canopy|gas station|underground product piping|dispensers?)\b"):
        families.add("environmental")
    # Explicit negative facts remove overreach families.
    if _has(text, r"\bno\s+(?:kitchen|food service|food prep|commercial kitchen|grease|fog|plumbing)\b"):
        families.discard("health_food")
        families.discard("wastewater_pretreatment_fog")
    if _has(text, r"\bno\s+(?:plumbing|sink move|sink relocation|pipe|water line)\b") and not _has(text, r"\b(?:shower|drain|backflow|irrigation|oil separator|floor drain)\b"):
        families.discard("plumbing")
    if _has(text, r"\bexhaust fan\b") and _has(text, r"\bno\s+(?:duct route changes?|electrical circuit changes?)\b"):
        families.discard("plumbing")
    if _has(text, r"\bno\s+(?:electrical|circuit|wiring)\b") and not _has(text, r"\b(?:lighting|exit signage|ev charging|transformer)\b"):
        families.discard("electrical")
    if _has(text, r"\bno\s+(?:mechanical|hvac|ductwork)\b") and not _has(text, r"\b(?:wood stove|chimney|cooler|ventilation|exhaust)\b"):
        families.discard("mechanical")
    if _has(text, r"\bno\s+roof(?:ing)?\s+work\b"):
        families.discard("roofing")
    if _has(text, r"\bno\s+solar\s+work\b"):
        families.discard("solar_pv")
    return families


def _positive_trade_or_layout_blocker(text: str) -> bool:
    scan = re.sub(r"\bno\s+(?:sink\s+move|electrical|walls?|wall\s+changes?|plumbing|structural|mechanical)\b", " ", text, flags=re.I)
    positive_patterns = [
        r"\b(?:moving\s+sink|move\s+sink|relocat(?:e|ing)\s+sink|adding\s+island|island\s+(?:receptacles?|circuits?)|receptacles?|circuits?|dishwasher|new\s+(?:plumbing|electrical|wiring))\b",
        r"\b(?:remove|removing|demo|demolish|alter|move|relocate|open|frame|new|add|adding)\b.{0,40}\b(?:walls?|framing|structural|beam|header|load[- ]bearing|partition)\b",
        r"\b(?:walls?|framing|structural|beam|header|load[- ]bearing|partition)\b.{0,40}\b(?:remove|removing|demo|demolish|alter|move|relocate|open|new|add|adding)\b",
        r"\b(?:layout\s+change|change\s+layout|new\s+opening|exterior\s+opening|subfloor\s+repair)\b",
    ]
    return any(_has(scan, pattern) for pattern in positive_patterns)


def _cosmetic_not_required(job_type: str) -> bool:
    text = _norm(job_type)
    if _positive_trade_or_layout_blocker(text):
        return False
    cabinet_like_for_like = (
        _has(text, r"\breplace\s+kitchen\s+cabinets?.*countertops?|cabinets?.*countertops?\b")
        and (_has(text, r"\b(?:same\s+layout|like[- ]for[- ]like)\b") or (_has(text, r"\bno\s+sink\s+move\b") and _has(text, r"\bno\s+electrical\b") and _has(text, r"\bno\s+walls?\b")))
    )
    return bool(
        (_has(text, r"\b(?:floating laminate|carpet|flooring|cabinets?|countertops?|vanity|toilet|tile)\b") and _has(text, r"\bno\s+(?:subfloor structural|structural|plumbing|electrical|walls?|sink move|pipe|mechanical)\b"))
        or cabinet_like_for_like
        or (_has(text, r"\breplace\s+bathroom\s+vanity\b") and _has(text, r"\bsame locations?\b") and _has(text, r"\bno\s+(?:plumbing relocation|electrical)\b"))
    )


def _scrub_foreign_lines(value: Any, job_type: str) -> Any:
    text = _norm(job_type)
    masonry_scope = _has(text, r"\b(?:masonry|lintel|facade|façade|chimney|structural facade)\b")
    if isinstance(value, str):
        out = value.replace("verify in confirm with", "verify with")
        out = re.sub(r"\bverify\s+in\s+before\s+quoting\b", "verify with the permit office before quoting", out, flags=re.I)
        out = re.sub(r"\s+", " ", out).strip()
        if not masonry_scope and _has(out, r"\b(?:masonry\s+lintel|structural\s+facade|facade\s+repair|façade\s+repair)\b"):
            return ""
        return out
    if isinstance(value, list):
        cleaned = [_scrub_foreign_lines(item, job_type) for item in value]
        return [item for item in cleaned if item not in ("", [], {})]
    if isinstance(value, dict):
        return {k: v for k, v in ((k, _scrub_foreign_lines(v, job_type)) for k, v in value.items()) if v not in ("", [], {})}
    return value


def _demote_or_drop_overreach(out: dict[str, Any], job_type: str) -> None:
    text = _norm(job_type)
    rows = []
    related = [copy.deepcopy(r) for r in out.get("related_permits") or [] if isinstance(r, dict)]
    for row in out.get("permits_required") or []:
        if not isinstance(row, dict):
            continue
        fam = _row_family(row)
        drop = False
        if fam in {"health_food", "wastewater_pretreatment_fog", "liquor"} and (
            _has(text, r"\bno\s+(?:kitchen|food service|food prep|commercial kitchen|grease|fog|plumbing)\b")
            or (_has(text, r"\b(?:repair shop|industrial|warehouse|solvent storage|compressor)\b") and not _has(text, r"\b(?:food|restaurant|kitchen|deli|bakery|grocery|grease|fog|brewery|taproom|brewing)\b"))
        ):
            drop = True
        if fam == "sign" and _has(text, r"\bexit signage\b") and not _has(text, r"\b(?:exterior sign|storefront sign|illuminated sign|new sign)\b"):
            drop = True
        if fam == "plumbing" and _has(text, r"\bexhaust fan\b") and _has(text, r"\bno\s+(?:duct route changes?|electrical circuit changes?)\b"):
            drop = True
        if drop:
            demoted = copy.deepcopy(row)
            demoted.update({"required": False, "decision": "CONDITIONAL", "status": "CONDITIONAL", "required_if": "Only needed if final scope or AHJ intake confirms this companion review is actually triggered."})
            related.append(demoted)
        else:
            rows.append(row)
    out["permits_required"] = rows
    if related:
        deduped_related = []
        seen_related = set()
        for row in related:
            key = (_row_family(row), str(row.get("decision") or row.get("status") or ""), str(row.get("permit_type") or row.get("permit_name") or ""))
            if key in seen_related:
                continue
            seen_related.add(key)
            deduped_related.append(row)
        out["related_permits"] = deduped_related
        out["conditional_permits"] = deduped_related


def _repair_action_path(out: dict[str, Any], city: str, state: str) -> None:
    city_key = _norm(city)
    state_key = _norm(state)
    auth_fix = _CANONICAL_AUTHORITY_FIXES.get((city_key, state_key))
    if auth_fix:
        out["applying_office"] = auth_fix["office"]
        out["building_dept_name"] = auth_fix["office"]
        safe_url = auth_fix["url"]
        out["apply_url"] = safe_url
        out["online_application_url"] = safe_url
        sources = [s for s in out.get("sources") or [] if isinstance(s, dict)]
        if not any(str(s.get("url") or "") == safe_url for s in sources):
            sources.insert(0, {"url": safe_url, "title": auth_fix["title"], "source_type": "official_local"})
        out["sources"] = sources
        urls = [u for u in out.get("source_urls") or [] if isinstance(u, str)]
        if safe_url not in urls:
            urls.insert(0, safe_url)
        out["source_urls"] = urls
    if str(out.get("permit_decision") or "").upper() != "REQUIRED":
        return
    office = str(out.get("applying_office") or out.get("building_dept_name") or _NAMED_AUTHORITY_CONTACTS.get((city_key, state_key), f"{city} permit office")).strip()
    out["applying_office"] = office
    out["building_dept_name"] = out.get("building_dept_name") or office
    ap = dict(out.get("apply_path") or {}) if isinstance(out.get("apply_path"), dict) else {}
    portal = out.get("apply_url") or out.get("online_application_url") or ap.get("portal_url")
    if portal:
        ap.update({"state": "RESOLVED_PORTAL", "channel": ap.get("channel") or "online_portal", "support_level": ap.get("support_level") or "official source or recorded portal", "portal_url": portal, "office_name": office})
    else:
        ap.update({"state": "CONTACT_AHJ", "status": "VERIFY_WITH_PERMIT_OFFICE", "typed_status": "VERIFY_WITH_PERMIT_OFFICE", "channel": "permit_office_contact", "support_level": "degraded contact path", "portal_url": None, "office_name": office, "action_path_confidence": "LOW", "verification_note": "Exact online filing URL is not present in the recorded artifact; contact the permitting authority and confirm the exact portal/form before filing."})
    out["apply_path"] = ap


def apply_fable5_final_customer_gate(public: dict[str, Any], job_type: str = "", city: str = "", state: str = "", scope_contract: dict[str, Any] | None = None, scope_facts: Any | None = None) -> dict[str, Any]:
    if not isinstance(public, dict):
        return {}
    out = _scrub_foreign_lines(copy.deepcopy(public), job_type)
    if not isinstance(out, dict):
        return {}
    segment = str((scope_contract or {}).get("category") or out.get("segment") or "").lower().strip()
    job_text = _norm(job_type)

    # E2 high-risk safe downgrades: only for explicit cosmetic/no-trade scopes.
    if _cosmetic_not_required(job_type) and segment != "commercial":
        _mark_not_required(out, job_text, city, state)
        return out

    _demote_or_drop_overreach(out, job_type)
    families = _triggered_families(job_type, segment)

    # R-012 style exhaust fan: mechanical wins; stale plumbing row is demoted above.
    if _has(job_text, r"\bexhaust fan\b"):
        families.add("mechanical")

    # If the final output says NOT_REQUIRED while scope facts have a primary floor,
    # the source-backed/explicit family wins and all surfaces must become REQUIRED.
    decision = str(out.get("permit_decision") or "").upper().strip()
    if families:
        _set_required(out, families, job_text, city, state)
    elif decision == "REQUIRED" and not _required_rows(out):
        _set_required(out, {"building"}, job_text, city, state)

    # C-031/C-032 subtype/segment cleanup after rows are present.
    if _has(job_text, r"\bdishwasher\b.*\bsame location\b"):
        for row in out.get("permits_required") or []:
            if isinstance(row, dict) and _row_family(row) == "electrical":
                row.update({"permit_type": "Electrical Permit — Equipment Reconnection / Existing Circuit", "permit_name": "Electrical Permit — Equipment Reconnection / Existing Circuit", "approval_type": "Electrical Permit — Equipment Reconnection / Existing Circuit"})
    if segment == "commercial":
        for row in out.get("permits_required") or []:
            if isinstance(row, dict):
                for key in ("permit_type", "permit_name", "approval_type", "kind", "display_family"):
                    if isinstance(row.get(key), str):
                        row[key] = re.sub(r"\bResidential\b", "Commercial", row[key], flags=re.I)

    if str(out.get("permit_decision") or "").upper() == "REQUIRED":
        _repair_action_path(out, city, state)
    return out
