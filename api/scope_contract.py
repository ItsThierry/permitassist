"""Canonical request scope contract and customer-visible scope firebreaks.

The contract is computed from the original user request at lookup entry and is
safe to pass downstream. It exists to prevent later helpers from re-detecting
scope from already-contaminated model/cache text.
"""

from __future__ import annotations

import copy
import os
import re
from typing import Any

_NEGATION_RE = re.compile(
    r"(?:\bno\b|\bwithout\b|\bnot\b|\bnone of\b|\bexcludes?\b|\bexcluding\b|\bdoes not include\b|\bdoesn't include\b|\bno new\b|\bnon[- ]+)"
    r"(?:\s+(?:and|or|any|new|commercial|residential|type\s*i|type\s*1|solar|pv))*"
    r"(?:[\s,;/()-]+[a-z0-9]+){0,5}[\s,;/()-]*$",
    re.I,
)

_COMMERCIAL_TERMS = (
    "commercial", "tenant improvement", "tenant finish", "tenant buildout", "buildout", "build-out",
    "restaurant", "commercial kitchen", "commercial alteration", "commercial building", "type i hood", "type 1 hood", "grease interceptor",
    "medical clinic", "dental clinic", "office tenant improvement", "office ti", "retail tenant improvement",
    "change of occupancy", "change of use", "retail conversion", "retail converted", "retail space", "retail suite",
    "fitness studio", "storefront", "clinic tenant improvement", "commercial fit-out", "commercial fit out",
)
_STRONG_COMMERCIAL_TERMS = tuple(term for term in _COMMERCIAL_TERMS if term != "commercial")
_RESIDENTIAL_TERMS = (
    "residential", "single-family", "single family", "single family home", "single-family home",
    "dwelling", "home", "house", "townhouse", "duplex",
)
_PANEL_TERMS = ("panel upgrade", "service upgrade", "electrical panel", "main panel", "meter main", "200 amp", "200a", "400 amp", "400a", "subpanel", "sub-panel")
_PANEL_ACTION_TERMS = (
    "panel upgrade", "service upgrade", "upgrade panel", "upgrade service", "replace panel",
    "panel replacement", "new panel", "new electrical panel", "new main panel", "meter main",
    "subpanel", "sub-panel", "service change", "service size change",
)
_SOLAR_TERMS = ("solar", "pv", "photovoltaic", "solarapp")
_HVAC_TERMS = ("hvac", "air conditioner", "air conditioning", "a/c", "heat pump", "furnace", "mini split", "mini-split", "condenser")
_HVAC_SPECIFIC_TERMS = ("hvac", "air conditioner", "air conditioning", "a/c", "furnace", "mini split", "mini-split", "condenser", "ductwork", "ducting", "air handler")
_WATER_HEATER_TERMS = ("water heater",)
_ROOF_TERMS = ("reroof", "re-roof", "roof replacement", "tear-off", "tear off", "shingle roof")
_ADU_TERMS = ("adu", "accessory dwelling", "garage conversion", "in-law suite", "granny flat", "jadu")
_REMODEL_TERMS = ("remodel", "renovation", "alteration", "addition", "new bathroom", "new kitchen", "load bearing", "wall removal")

_TAG_TERMS: dict[str, tuple[str, ...]] = {
    # Do not use bare "commercial" as a scope tag for deterministic notes: state
    # packs often say "residential and commercial" or "commercial refrigeration"
    # as broad applicability/licensing context. Contamination blocking should key
    # on actual TI/change-of-use language, not the standalone word.
    "commercial_ti": (
        "tenant improvement", "tenant finish", "tenant buildout", "buildout", "build-out",
        "commercial alteration", "commercial building", "change of occupancy", "change of use", "change in use",
        "conversion", "converted into", "convert into", "converting into", "fit-out", "fit out",
    ),
    "restaurant_ti": ("restaurant", "commercial kitchen", "food service", "type i hood", "type 1 hood", "grease interceptor", "ansul", "commercial dishwasher"),
    "medical_clinic_ti": ("medical clinic", "dental clinic", "clinic tenant", "exam room", "patient care", "medical gas", "x-ray", "radiology"),
    "office_ti": ("office tenant improvement", "office ti", "professional office", "office buildout", "law office"),
    "retail_ti": ("retail tenant improvement", "retail ti", "storefront", "retail buildout", "retail store"),
    "residential_only": (
        "homeowner", "homeowners", "owner-builder", "owner builder", "owner-occupied", "home improvement", "home-improvement",
        "home improvement contractor", "home-improvement contractor", "home-improvement contracts",
        "residential home-improvement", "residential remodeling", "residential alteration",
        "residential alterations", "residential land or buildings", "residential code", "residential remodel",
        "residential building permit", "residential building permits", "residential permit", "residential permits",
        "residential subdivision", "residential subdivisions", "residential developer", "residential developers",
        "single-family", "single family", "dwelling",
    ),
    "residential_adu": ("adu", "accessory dwelling", "jadu", "garage conversion", "in-law suite", "granny flat"),
    "residential_solar": ("residential solar", "solar pv", "photovoltaic", "solarapp", "net metering", "interconnection"),
    "panel_upgrade": _PANEL_TERMS,
    "solar_pv": _SOLAR_TERMS,
    "coastal_windstorm": ("twia", "windstorm", "wpi-8", "catastrophe area", "coastal"),
    "floodplain": ("floodplain", "nfip", "fema", "sfha", "base-flood", "elevation certificate"),
    "utility_interconnection": ("interconnection", "net metering", "utility", "meter set", "new service"),
}

_GENERAL_TAGS = {"general", "contractor_license", "code_adoption", "energy", "accessibility", "timeline", "fee", "inspection", "local_utility", "utility_service", "general_state_local"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _term_is_locally_negated(text: str, term_start: int) -> bool:
    prefix = text[max(0, term_start - 96):term_start]
    if re.search(r"\b(?:remove|remove old|remove existing|removed|demolish|demo|cap|cap existing|abandon)\b(?:\s+(?:old|existing|unused|prior))*[\s,;/()-]*$", prefix, flags=re.I):
        return True
    if _NEGATION_RE.search(prefix):
        return True
    suffix = text[term_start:term_start + 96]
    return bool(re.search(r"^[a-z0-9\s,;/()'\"-]{0,48}\b(?:not included|excluded|not in scope|outside(?: the)? scope|not part|not proposed)\b", suffix, flags=re.I))


def contains_unnegated_phrase(text: str, phrase: str) -> bool:
    phrase_lc = (phrase or "").lower().strip()
    if not phrase_lc:
        return False
    haystack = _norm(text)
    pattern = re.compile(r"(?<![a-z0-9])" + re.escape(phrase_lc) + r"(?![a-z0-9])", flags=re.I)
    for match in pattern.finditer(haystack):
        if not _term_is_locally_negated(haystack, match.start()):
            return True
    return False


def has_any_unnegated(text: str, phrases: tuple[str, ...]) -> bool:
    return any(contains_unnegated_phrase(text, phrase) for phrase in phrases)


def _has_panel_scope(job_text: str) -> bool:
    """Return True for actual panel/service work, not existing-capacity facts."""
    job = _norm(job_text)
    if has_any_unnegated(job, _PANEL_ACTION_TERMS):
        return True
    # Bare "existing 200A panel available" is context for a water-heater/HVAC
    # lookup, not an electrical service-change trigger. Require an action verb.
    return bool(re.search(r"\b(?:upgrade|replace|new|modify|change)\b.{0,40}\b(?:200\s*(?:amp|a)|400\s*(?:amp|a)|panel|service)\b", job, flags=re.I))


def _explicit_category(job_text: str, job_category: str | None) -> str:
    category = (job_category or "").strip().lower()
    has_commercial = has_any_unnegated(job_text, _COMMERCIAL_TERMS)
    has_strong_commercial = has_any_unnegated(job_text, _STRONG_COMMERCIAL_TERMS)
    has_residential = has_any_unnegated(job_text, _RESIDENTIAL_TERMS)
    if category == "commercial":
        return category
    if category == "residential":
        # Some public/client callers historically defaulted an omitted category
        # to "residential". Do not let that implicit default strip a clearly
        # commercial TI/restaurant/clinic/office answer downstream.
        if has_strong_commercial:
            return "commercial"
        return category
    if has_commercial:
        return "commercial"
    if has_residential or has_any_unnegated(job_text, _PANEL_TERMS + _HVAC_TERMS + _WATER_HEATER_TERMS + _ROOF_TERMS):
        return "residential"
    return "unknown"


def build_scope_contract(job_type: str, city: str = "", state: str = "", *, job_category: str | None = None, vertical: str | None = None) -> dict[str, Any]:
    """Return the canonical scope contract for a request.

    The contract is based only on request fields, not model/cache output.
    """
    job = _norm(job_type)
    category = _explicit_category(job, job_category)
    explicit_vertical = _norm(vertical or "").replace(" ", "_").replace("-", "_")

    has_panel = _has_panel_scope(job)
    has_solar = has_any_unnegated(job, _SOLAR_TERMS)
    has_hvac = has_any_unnegated(job, _HVAC_TERMS)
    has_hvac_specific = has_any_unnegated(job, _HVAC_SPECIFIC_TERMS)
    has_water_heater = has_any_unnegated(job, _WATER_HEATER_TERMS)
    has_roof = has_any_unnegated(job, _ROOF_TERMS)
    has_adu = has_any_unnegated(job, _ADU_TERMS)
    has_remodel = has_any_unnegated(job, _REMODEL_TERMS)

    commercial_vertical = None
    if has_any_unnegated(job, _TAG_TERMS["restaurant_ti"]):
        commercial_vertical = "restaurant_ti"
    elif has_any_unnegated(job, _TAG_TERMS["medical_clinic_ti"]):
        commercial_vertical = "medical_clinic_ti"
    elif has_any_unnegated(job, _TAG_TERMS["office_ti"]):
        commercial_vertical = "office_ti"
    elif has_any_unnegated(job, _TAG_TERMS["retail_ti"]):
        commercial_vertical = "retail_ti"
    elif category == "commercial" and has_any_unnegated(job, _TAG_TERMS["commercial_ti"]):
        commercial_vertical = "commercial_ti"

    if explicit_vertical:
        request_vertical = explicit_vertical
    elif has_solar:
        request_vertical = "solar_pv"
    elif category == "commercial" and commercial_vertical:
        request_vertical = commercial_vertical
    elif has_water_heater and not has_hvac_specific:
        # "Heat pump water heater" is a plumbing/water-heater vertical, not a
        # mini-split/HVAC vertical.  If a separate HVAC system is also explicitly
        # present, let the HVAC branch handle the combined-scope case.
        request_vertical = "water_heater"
    elif has_panel and category != "commercial":
        request_vertical = "panel_upgrade"
    elif has_hvac:
        request_vertical = "hvac_changeout"
    elif has_roof:
        request_vertical = "reroof"
    elif has_adu:
        request_vertical = "adu"
    elif has_remodel:
        request_vertical = "residential_remodel" if category != "commercial" else "commercial_ti"
    else:
        request_vertical = "generic"

    if category == "commercial":
        family = "commercial_ti" if (request_vertical.endswith("_ti") or request_vertical in {"commercial_ti", "restaurant_ti", "medical_clinic_ti", "office_ti", "retail_ti"}) else "commercial_other"
    elif request_vertical in {"panel_upgrade", "hvac_changeout", "water_heater", "reroof", "solar_pv"}:
        family = "residential_single_trade"
    elif request_vertical == "adu":
        family = "residential_adu"
    elif request_vertical == "residential_remodel":
        family = "residential_remodel"
    else:
        family = "residential_other" if category == "residential" else "unknown"

    if has_any_unnegated(job, ("single-family", "single family", "single family home", "single-family home", "house", "home")):
        occupancy = "single_family"
    elif has_any_unnegated(job, ("multifamily", "multi-family", "apartment", "condo")):
        occupancy = "multifamily"
    elif category == "commercial":
        occupancy = "commercial"
    else:
        occupancy = "unknown"

    forbidden = set()
    if category == "commercial":
        forbidden.update({"residential_only", "residential_adu", "homeowner_only"})
        if request_vertical != "solar_pv" and not has_solar:
            forbidden.update({"solar_pv", "residential_solar"})
    elif category == "residential":
        forbidden.update({"commercial_ti", "restaurant_ti", "medical_clinic_ti", "office_ti", "retail_ti", "commercial_food", "commercial_health"})
        if request_vertical not in {"solar_pv", "panel_upgrade"} and not has_solar:
            forbidden.update({"solar_pv", "residential_solar", "utility_interconnection"})
        elif request_vertical == "panel_upgrade" and not has_solar:
            forbidden.update({"solar_pv", "residential_solar"})
        if request_vertical != "adu":
            forbidden.add("residential_adu")
        if request_vertical != "panel_upgrade":
            forbidden.discard("utility_interconnection")
    if request_vertical == "panel_upgrade":
        forbidden.update({"solar_pv", "residential_solar", "commercial_ti", "restaurant_ti", "medical_clinic_ti", "office_ti", "retail_ti"})

    allowed = set(_GENERAL_TAGS)
    allowed.add(category)
    allowed.add(family)
    allowed.add(request_vertical)
    if category == "commercial":
        allowed.update({"commercial_ti", "contractor_license", "accessibility", "code_adoption", "energy", "inspection", "timeline"})
        if request_vertical == "restaurant_ti":
            allowed.update({"restaurant_ti", "commercial_food", "fire_life_safety", "health"})
        if request_vertical == "medical_clinic_ti":
            allowed.update({"medical_clinic_ti", "health", "accessibility"})
        if request_vertical == "office_ti":
            allowed.update({"office_ti", "accessibility"})
        if request_vertical == "solar_pv":
            allowed.update({"solar_pv", "residential_solar", "utility_interconnection", "electrical_service"})
    elif category == "residential":
        allowed.update({"residential_only", "contractor_license", "code_adoption", "inspection", "timeline"})
        if request_vertical == "panel_upgrade":
            allowed.update({"panel_upgrade", "electrical_service", "utility_service"})
        if request_vertical == "solar_pv":
            allowed.update({"solar_pv", "residential_solar", "utility_interconnection"})
        if request_vertical == "adu":
            allowed.update({"residential_adu", "energy", "zoning"})

    allowed.difference_update(forbidden)
    return {
        "category": category,
        "family": family,
        "vertical": request_vertical,
        "occupancy_class": occupancy,
        "forbidden_scope_tags": sorted(forbidden),
        "allowed_scope_tags": sorted(allowed),
        "city": city or "",
        "state": (state or "").upper().strip(),
        "source": "request_entry",
    }


def scope_tag_text(text: str) -> set[str]:
    tags: set[str] = set()
    value = text or ""
    for tag, terms in _TAG_TERMS.items():
        if has_any_unnegated(value, terms):
            tags.add(tag)
    if not tags:
        tags.add("general")
    return tags


def note_scope_tags(note: dict[str, Any]) -> list[str]:
    text = " ".join(str(note.get(k) or "") for k in ("title", "note", "applies_to"))
    tags = scope_tag_text(text)
    lower = _norm(text)
    residential_only_signals = (
        "homeowner", "homeowners", "owner-builder", "owner builder", "home improvement", "home-improvement",
        "residential remodeling", "residential alteration", "residential alterations",
        "residential land or buildings", "residential home-improvement", "residential code",
        "building, residential, plumbing",
    )
    if any(signal in lower for signal in residential_only_signals) or re.search(r"\bresidential\b.{0,40}\bcodes?\b|\bcodes?\b.{0,40}\bresidential\b", lower):
        tags.add("residential_only")
    if "license" in lower or "contractor" in lower or "tdlr" in lower or "tsbpe" in lower:
        tags.add("contractor_license")
    if "energy code" in lower or "energy compliance" in lower or "energy conservation" in lower or "iecc" in lower or "title 24" in lower or "cf1r" in lower or "calgreen" in lower:
        tags.add("energy")
        # Title 24/CF1R/CALGreen notes may mention solar-ready/solar-mandate
        # context without being a Solar PV permit note. Keep them as energy/code
        # compliance unless they include actual PV/interconnection terms.
        if not any(term in lower for term in ("solar pv", "photovoltaic", "net metering", "interconnection", "battery", "ess")):
            tags.discard("solar_pv")
            tags.discard("residential_solar")
            tags.discard("utility_interconnection")
    if "building standards code" in lower or "code edition" in lower or "code adoption" in lower or "adopted" in lower or "effective date" in lower or "irc" in lower or "ibc" in lower or "imc" in lower or "ifgc" in lower:
        tags.add("code_adoption")
    if "fee" in lower or "$" in lower:
        tags.add("fee")
    if "municipal utility" in lower or "local utility" in lower or "ladwp" in lower or "pasadena water and power" in lower or "pwp" in lower:
        tags.add("local_utility")
        tags.add("utility_service")
        tags.discard("solar_pv")
        tags.discard("residential_solar")
        tags.discard("utility_interconnection")
    if "historic district" in lower or "historic preservation" in lower or "historic parcels" in lower:
        tags.add("general_state_local")
        tags.discard("solar_pv")
        tags.discard("residential_solar")
    if "accessibility" in lower or "ada" in lower or "553.502" in lower:
        tags.add("accessibility")
    if "inspection" in lower:
        tags.add("inspection")
    if "shot clock" in lower or "timeline" in lower or "business days" in lower:
        tags.add("timeline")
    return sorted(tags)


def note_allowed_for_contract(note: dict[str, Any], scope_contract: dict[str, Any] | None) -> bool:
    if not scope_contract:
        return True
    tags = set(note.get("scope_tags") or note_scope_tags(note))
    forbidden = set(scope_contract.get("forbidden_scope_tags") or [])
    if tags & forbidden:
        return False
    allowed = set(scope_contract.get("allowed_scope_tags") or [])
    scoped_tags = tags - _GENERAL_TAGS
    if scoped_tags and not scoped_tags.issubset(allowed):
        return False
    return bool(tags & allowed) or tags == {"general"}


def _firebreak_forbidden_terms(scope_contract: dict[str, Any]) -> tuple[str, ...]:
    tags = set(scope_contract.get("forbidden_scope_tags") or [])
    terms: list[str] = []
    for tag in tags:
        terms.extend(_TAG_TERMS.get(tag, ()))
    if "commercial_ti" in tags:
        terms.extend(("tenant improvement", "tenant finish", "tenant buildout", "commercial building", "commercial alteration"))
    if "residential_solar" in tags or "solar_pv" in tags:
        terms.extend(("solar pv", "photovoltaic", "solarapp", "roof-mounted racking", "structural racking", "net metering", "utility interconnection"))
    if "residential_adu" in tags:
        terms.extend(("adu", "accessory dwelling", "jadu"))
    if "residential_only" in tags:
        terms.extend(("homeowner", "homeowners", "owner-builder", "owner builder", "owner-occupied", "home improvement", "home-improvement", "home improvement contractor", "home-improvement contractor"))
    return tuple(dict.fromkeys(t for t in terms if t))


def customer_text_has_forbidden_scope(value: Any, scope_contract: dict[str, Any]) -> bool:
    text = value if isinstance(value, str) else str(value)
    return has_any_unnegated(text, _firebreak_forbidden_terms(scope_contract))


def customer_text_mentions_forbidden_scope(value: Any, scope_contract: dict[str, Any]) -> bool:
    """Return True when customer-visible text mentions a forbidden scope at all.

    The final firebreak uses unnegated matching so source/user phrases like "no
    solar" do not misclassify the request. Customer-facing helper copy is stricter:
    even exclusionary wording such as "do not use homeowner ADU forms" is still a
    confusing residential/solar leak in a commercial TI answer and should be
    removed upstream.
    """
    text = _norm(value if isinstance(value, str) else str(value))
    for phrase in _firebreak_forbidden_terms(scope_contract):
        phrase_lc = _norm(phrase)
        if phrase_lc and re.search(r"(?<![a-z0-9])" + re.escape(phrase_lc) + r"(?![a-z0-9])", text, flags=re.I):
            return True
    return False


def sanitize_result_for_scope_contract(result: dict[str, Any], scope_contract: dict[str, Any], *, fail_on_removal_in_tests: bool = True) -> dict[str, Any]:
    """Final customer-visible tripwire for leaked scope text.

    Production removes individual leaked items and marks metadata. Pytest raises if
    anything had to be removed so regressions get fixed upstream.
    """
    if not isinstance(result, dict) or not scope_contract:
        return result
    forbidden_terms = _firebreak_forbidden_terms(scope_contract)
    if not forbidden_terms:
        result.setdefault("_scope_contract", scope_contract)
        return result

    removals: list[dict[str, str]] = []
    removed = object()

    def is_forbidden(value: Any) -> bool:
        if not isinstance(value, str):
            value = str(value)
        return has_any_unnegated(value, forbidden_terms)

    def clean(value: Any, path: str) -> Any:
        if isinstance(value, str):
            if is_forbidden(value):
                removals.append({"path": path, "kind": "forbidden_scope_text"})
                return removed
            return value
        if isinstance(value, list):
            out = []
            for idx, item in enumerate(value):
                cleaned = clean(item, f"{path}[{idx}]")
                if cleaned is not removed:
                    out.append(cleaned)
            return out
        if isinstance(value, dict):
            if any(isinstance(v, str) and is_forbidden(v) for v in value.values()):
                # Drop whole structured items when their classifier/title fields leak scope.
                title_fields = {"permit_type", "portal_selection", "title", "name", "summary", "reason", "required_if", "claim", "value"}
                if any(k in title_fields and isinstance(v, str) and is_forbidden(v) for k, v in value.items()):
                    removals.append({"path": path, "kind": "forbidden_scope_structured_item"})
                    return removed
            out = {}
            for key, item in value.items():
                if str(key).startswith("_"):
                    out[key] = item
                    continue
                cleaned = clean(item, f"{path}.{key}" if path else str(key))
                if cleaned is not removed:
                    out[key] = cleaned
            return out
        return value

    cleaned = clean(copy.deepcopy(result), "")
    if not isinstance(cleaned, dict):
        cleaned = {}
    cleaned["_scope_contract"] = scope_contract
    if removals:
        cleaned["_scope_firebreak_removed"] = removals
        if fail_on_removal_in_tests and os.environ.get("PYTEST_CURRENT_TEST"):
            raise AssertionError(f"Scope firebreak removed leaked customer-visible text; fix upstream: {removals[:5]}")
    return cleaned


def customer_text_has_server_error_signal(text: str) -> bool:
    """Detect real server-error text without treating legal/code sections as 5xx errors."""
    value = str(text or "").lower()
    if not value:
        return False
    if "server_error" in value or "server error" in value or "internal server error" in value:
        return True
    if "bad gateway" in value or "service unavailable" in value or "gateway timeout" in value:
        return True
    return bool(re.search(r"\b(?:http\s*)?(?:500|501|502|503|504)\b(?:\s+(?:error|bad gateway|service unavailable|gateway timeout))", value))
