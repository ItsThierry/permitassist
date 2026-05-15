"""Commercial customer-visible rendered-output guardrails for PermitAssist.

This module is intentionally deterministic. It does not decide whether a permit
is legally required; it verifies that customer-facing output does not overclaim,
leak residential copy into commercial work, mix jurisdictions, or hide
uncertainty inside permit display names.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import urlparse


FALLBACK_NAME_RE = re.compile(
    r"permit\s+required\s*[-–—]+\s*exact\s+permit\s+type\s+needs\s+ahj\s+verification|"
    r"exact\s+permit\s+(?:type|category).*needs\s+ahj\s+verification|"
    r"needs\s+ahj\s+verification",
    re.I,
)

UNCERTAINTY_RE = re.compile(r"\b(unknown|unverified|needs?\s+AHJ\s+verification|verify\s+with\s+(?:the\s+)?AHJ|human\s+review)\b", re.I)
VERIFIED_RE = re.compile(r"\b(Verified\s*[·-]?\s*(?:official\s+sources?|source|data)|City-verified|✓\s*Verified)\b", re.I)
HOMEOWNER_RE = re.compile(r"\b(homeowner|home\s*sale|selling\s+your\s+home|sell\s+the\s+home|homeowner\s+insurance|your\s+contractor\s+should\s+have)\b", re.I)
STRING_ARTIFACT_RE = re.compile(r"\b(undefined|null|NaN)\b|\$\s*,|,\s*,|\(\s*\)|\b1\s+inspections\b", re.I)

LOCAL_HOST_BY_CITY = {
    "chicago": ("chicago.gov", "cityofchicago.org"),
    "philadelphia": ("phila.gov", "permits.phila.gov"),
    "new york": ("nyc.gov",),
    "los angeles": ("lacity.org", "ladbs.org", "ladbsservices2.lacity.org"),
    "houston": ("houstontx.gov", "houstonpermittingcenter.org"),
    "phoenix": ("phoenix.gov",),
    "austin": ("austintexas.gov",),
    "seattle": ("seattle.gov",),
    "denver": ("denvergov.org",),
    "boston": ("boston.gov", "bostonpermits.com"),
}

STATE_HOST_BY_STATE = {
    "IL": ("illinois.gov", "idfpr.illinois.gov"),
    "PA": ("pa.gov", "dli.pa.gov"),
    "NY": ("ny.gov",),
    "CA": ("ca.gov",),
    "TX": ("texas.gov", "tdlr.texas.gov"),
    "AZ": ("az.gov",),
    "WA": ("wa.gov",),
    "CO": ("colorado.gov",),
    "MA": ("mass.gov",),
}
STATE_ABBR_BY_NAME = {
    "ILLINOIS": "IL",
    "PENNSYLVANIA": "PA",
    "NEW YORK": "NY",
    "CALIFORNIA": "CA",
    "TEXAS": "TX",
    "ARIZONA": "AZ",
    "WASHINGTON": "WA",
    "COLORADO": "CO",
    "MASSACHUSETTS": "MA",
}

MODEL_CODE_HOSTS = (
    "codes.iccsafe.org",
    "iccsafe.org",
    "up.codes",
    "nfpa.org",
    "osha.gov",
)

WRONG_LOCAL_HOSTS = {
    "phila.gov": "Philadelphia",
    "permits.phila.gov": "Philadelphia",
    "nyc.gov": "New York",
    "chicago.gov": "Chicago",
    "cityofchicago.org": "Chicago",
    "lacity.org": "Los Angeles",
    "ladbs.org": "Los Angeles",
    "houstontx.gov": "Houston",
    "phoenix.gov": "Phoenix",
    "austintexas.gov": "Austin",
    "seattle.gov": "Seattle",
    "denvergov.org": "Denver",
    "boston.gov": "Boston",
}


@dataclass(frozen=True)
class InspectionProfile:
    name: str
    display_name: str
    required_concepts: tuple[str, ...]
    default_items: tuple[str, ...]
    forbidden_concepts: tuple[str, ...] = ()


PROFILES = {
    "commercial_restaurant": InspectionProfile(
        name="commercial_restaurant",
        display_name="Commercial restaurant tenant improvement",
        required_concepts=("building rough", "building final", "hood", "grease duct", "fire suppression", "fire alarm", "health", "egress", "occupancy final"),
        default_items=(
            "Building rough inspection for framing, rated assemblies, exits, and accessibility path-of-travel",
            "Building final inspection before opening or Certificate of Occupancy / occupancy final approval",
            "Mechanical rough/final for commercial HVAC, kitchen exhaust, Type I hood, and grease duct where applicable",
            "Fire suppression acceptance test for hood/Ansul or sprinkler modifications where applicable",
            "Fire alarm inspection where alarm devices, monitoring, or occupant notification are affected",
            "Plumbing rough/final for grease interceptor, floor sinks, hand sinks, and restrooms where applicable",
            "Health department / food establishment inspection before opening where applicable",
            "Egress and occupancy final inspection before Certificate of Occupancy / opening",
        ),
        forbidden_concepts=("homeowner", "home sale"),
    ),
    "medical_clinic_ti": InspectionProfile(
        name="medical_clinic_ti",
        display_name="Medical clinic tenant improvement",
        required_concepts=("clinic", "health", "life safety", "accessibility", "equipment", "fire", "egress"),
        default_items=(
            "Building rough/final inspection for clinic layout, rated corridors/rooms, and tenant-improvement work",
            "Accessibility inspection for accessible route, restrooms, exam rooms, reception, and parking/path-of-travel where applicable",
            "Fire/life-safety inspection for alarm, sprinkler, egress, and occupancy features",
            "Mechanical/electrical/plumbing inspections for exam rooms, hand sinks, ventilation, equipment loads, and dedicated circuits",
            "Medical gas, imaging/shielding, or special-equipment inspection/review where oxygen, vacuum, x-ray, CT, or similar equipment is included",
            "Health/licensing or clinic operational inspection where the AHJ or state program requires it",
        ),
        forbidden_concepts=("hood", "grease duct", "homeowner", "home sale"),
    ),
    "office_ti": InspectionProfile(
        name="office_ti",
        display_name="Commercial office tenant improvement",
        required_concepts=("commercial alteration", "egress", "accessibility", "building rough", "building final"),
        default_items=(
            "Commercial alteration / tenant-improvement building rough inspection for partitions, rated assemblies, and ceiling grid",
            "Egress and life-safety inspection for exits, travel distance, exit signs, emergency lighting, and occupant load",
            "Accessibility inspection for path-of-travel, doors/hardware, restrooms, reception counters, and accessible routes where applicable",
            "Trade rough/final inspections for electrical, mechanical, plumbing, and low-voltage work included in the TI",
            "Final building inspection and occupancy/tenant authorization before use of the space",
        ),
        forbidden_concepts=("medical gas", "x-ray", "hood", "grease duct", "homeowner", "home sale"),
    ),
    "commercial_trade_only": InspectionProfile(
        name="commercial_trade_only",
        display_name="Commercial trade-only work",
        required_concepts=("trade rough", "trade final"),
        default_items=(
            "Trade rough inspection where walls/ceilings are open or equipment is replaced",
            "Trade final inspection before the system is placed into service",
        ),
        forbidden_concepts=("restaurant", "clinic", "office tenant improvement", "homeowner"),
    ),
    "residential": InspectionProfile(
        name="residential",
        display_name="Residential permit workflow",
        required_concepts=("residential",),
        default_items=("Residential rough/final inspection sequence appropriate to the trade scope",),
        forbidden_concepts=("commercial restaurant", "medical clinic", "office tenant improvement"),
    ),
}


def _text(*parts: Any) -> str:
    return " ".join(str(p or "") for p in parts)


def _host(url: str) -> str:
    try:
        return urlparse(str(url)).hostname or ""
    except Exception:
        return ""


def _host_matches(host: str, allowed: tuple[str, ...]) -> bool:
    host = host.lower().removeprefix("www.")
    return any(host == h or host.endswith("." + h) for h in allowed)


def _contains_unnegated_phrase(text: str, phrase: str) -> bool:
    """Return True when phrase appears without a nearby negation prefix."""
    text_l = str(text or "").lower()
    phrase_l = str(phrase or "").lower().strip()
    if not text_l or not phrase_l:
        return False
    for match in re.finditer(re.escape(phrase_l), text_l):
        prefix = text_l[max(0, match.start() - 45):match.start()]
        if re.search(r"\b(no|not|without|exclude(?:s|d|ing)?|excluding|non[-\s]?|not\s+a|not\s+an|none)\s*$", prefix):
            continue
        return True
    return False


def _has_unnegated_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(_contains_unnegated_phrase(text, phrase) for phrase in phrases)


def canonical_vertical(scope: str | None, job_type: str | None = "") -> str:
    scope_text = str(scope or "").lower().strip()
    scope_key = re.sub(r"[^a-z0-9]+", "_", scope_text).strip("_")
    explicit_scope_map = {
        "commercial_restaurant": "commercial_restaurant",
        "restaurant_ti": "commercial_restaurant",
        "restaurant_tenant_improvement": "commercial_restaurant",
        "commercial_medical_clinic_ti": "medical_clinic_ti",
        "medical_clinic_ti": "medical_clinic_ti",
        "clinic_ti": "medical_clinic_ti",
        "commercial_office_ti": "office_ti",
        "office_ti": "office_ti",
        "office_tenant_improvement": "office_ti",
        "commercial_trade_only": "commercial_trade_only",
        "trade_only": "commercial_trade_only",
        "commercial_unknown": "commercial_unknown",
        "commercial": "commercial_unknown",
        "residential": "residential",
    }
    if scope_key in explicit_scope_map:
        return explicit_scope_map[scope_key]
    if scope_key.startswith("residential"):
        return "residential"

    s = str(job_type or "").lower()
    if _has_unnegated_any(s, ("restaurant", "food service", "commercial kitchen", "type i hood", "type 1 hood", "grease duct", "grease interceptor")):
        return "commercial_restaurant"
    if _has_unnegated_any(s, ("medical", "clinic", "dental", "exam room", "imaging", "x-ray", "medical gas", "office-to-clinic")):
        return "medical_clinic_ti"
    if _has_unnegated_any(s, ("office ti", "office tenant", "office buildout", "office build-out", "demising", "multi-tenant office")):
        return "office_ti"
    if _contains_unnegated_phrase(s, "commercial") and _has_unnegated_any(s, ("hvac", "rtu", "rooftop unit", "electrical", "plumbing", "trade-only", "trade only")) and not _has_unnegated_any(s, ("tenant improvement", "buildout", "build-out", "change of use", "change of occupancy")):
        return "commercial_trade_only"
    if _has_unnegated_any(s, ("commercial", "tenant improvement", "buildout", "build-out")):
        return "commercial_unknown"
    if _has_unnegated_any(s, ("residential", "single family", "single-family", "homeowner", "dwelling")):
        return "residential"
    return "unknown"


def is_commercial_vertical(vertical: str) -> bool:
    return str(vertical or "").startswith("commercial_") or vertical in {"medical_clinic_ti", "office_ti"}


def build_rendering_context(result: dict[str, Any] | None, job_type: str, city: str, state: str, requested_mode: str = "contractor") -> dict[str, Any]:
    result = result or {}
    scope = result.get("_primary_scope") or result.get("primary_scope") or result.get("project_scope") or result.get("job_category")
    vertical = canonical_vertical(scope, job_type)
    commercial = is_commercial_vertical(vertical)
    if vertical == "unknown" and str(result.get("job_category") or "").lower() == "commercial":
        commercial = True
        vertical = "commercial_unknown"
    template_family = "commercial" if commercial else ("residential" if vertical == "residential" or requested_mode == "homeowner" else "contractor")
    profile_name = {
        "commercial_restaurant": "commercial_restaurant",
        "medical_clinic_ti": "medical_clinic_ti",
        "office_ti": "office_ti",
        "commercial_trade_only": "commercial_trade_only",
        "residential": "residential",
    }.get(vertical, "commercial_trade_only" if commercial else "residential")
    return {
        "city": city or result.get("city") or "",
        "state": state or result.get("state") or "",
        "requested_mode": requested_mode or "contractor",
        "is_commercial": commercial,
        "vertical": vertical,
        "template_family": template_family,
        "allow_homeowner_template": not commercial and requested_mode == "homeowner",
        "inspection_profile": profile_name,
    }


def sanitize_permit_display_name(name: str | None, scope: str | None = None, job_type: str | None = "") -> str:
    raw = str(name or "").strip()
    vertical = canonical_vertical(scope, job_type)
    if not raw or FALLBACK_NAME_RE.search(raw):
        if vertical == "commercial_restaurant":
            return "Commercial Alteration / Tenant Improvement Permit"
        if vertical == "medical_clinic_ti":
            return "Medical Clinic Tenant Improvement Permit"
        if vertical == "office_ti":
            return "Commercial Office Tenant Improvement Permit"
        if vertical in {"commercial_unknown", "commercial_trade_only"}:
            return "Commercial Permit / Trade Permit"
        return "Permit — verify exact AHJ title"
    return re.sub(r"\s+", " ", raw)


def select_inspection_profile(scope: str | None, job_type: str | None = "") -> dict[str, Any]:
    vertical = canonical_vertical(scope, job_type)
    name = {
        "commercial_restaurant": "commercial_restaurant",
        "medical_clinic_ti": "medical_clinic_ti",
        "office_ti": "office_ti",
        "commercial_trade_only": "commercial_trade_only",
        "residential": "residential",
    }.get(vertical, "commercial_trade_only" if is_commercial_vertical(vertical) else "residential")
    p = PROFILES[name]
    return {
        "profile": p.name,
        "display_name": p.display_name,
        "required_concepts": list(p.required_concepts),
        "default_items": list(p.default_items),
        "forbidden_concepts": list(p.forbidden_concepts),
    }


def classify_source_for_jurisdiction(url: str, city: str, state: str) -> dict[str, Any]:
    host = _host(url).lower().removeprefix("www.")
    city_key = str(city or "").lower().strip()
    state_raw = str(state or "").upper().strip()
    state_key = STATE_ABBR_BY_NAME.get(state_raw, state_raw)
    local_hosts = LOCAL_HOST_BY_CITY.get(city_key, ())
    state_hosts = STATE_HOST_BY_STATE.get(state_key, ())
    if local_hosts and _host_matches(host, local_hosts):
        return {"url": url, "host": host, "source_class": "local_ahj", "allowed_as_official_support": True, "allowed_as_local_ahj_support": True}
    wrong_city = None
    for known_host, known_city in WRONG_LOCAL_HOSTS.items():
        if _host_matches(host, (known_host,)) and known_city.lower() != city_key:
            wrong_city = known_city
            break
    if wrong_city:
        return {"url": url, "host": host, "source_class": "wrong_local_ahj", "jurisdiction": wrong_city, "allowed_as_official_support": False, "allowed_as_local_ahj_support": False}
    if state_hosts and _host_matches(host, state_hosts):
        return {"url": url, "host": host, "source_class": "state", "allowed_as_official_support": True, "allowed_as_local_ahj_support": False}
    if _host_matches(host, MODEL_CODE_HOSTS):
        return {"url": url, "host": host, "source_class": "model_code", "allowed_as_official_support": True, "allowed_as_local_ahj_support": False}
    if host.endswith(".gov") or host.endswith(".gov.us") or host.endswith(".us"):
        return {"url": url, "host": host, "source_class": "government_other", "allowed_as_official_support": False, "allowed_as_local_ahj_support": False}
    return {"url": url, "host": host, "source_class": "other", "allowed_as_official_support": False, "allowed_as_local_ahj_support": False}


def _source_urls(result: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for item in result.get("sources") or []:
        if isinstance(item, str):
            urls.append(item)
        elif isinstance(item, dict):
            u = item.get("url") or item.get("link")
            if u:
                urls.append(str(u))
    return urls


def _current_names(result: dict[str, Any]) -> list[str]:
    names = [result.get("permit_name"), result.get("permit_type")]
    for p in result.get("permits_required") or []:
        if isinstance(p, dict):
            names.extend([p.get("permit_type"), p.get("portal_selection"), p.get("name"), p.get("title")])
    return [str(n) for n in names if n]


def _inspection_text(result: dict[str, Any], rendered_text: str) -> str:
    bits: list[str] = [rendered_text or ""]
    for key in ("inspections", "inspect_checklist"):
        for item in result.get(key) or []:
            if isinstance(item, dict):
                bits.append(_text(item.get("title"), item.get("stage"), item.get("description"), item.get("notes")))
            else:
                bits.append(str(item))
    return "\n".join(bits).lower()


def _has_concepts(text: str, concepts: list[str]) -> bool:
    return all(c.lower() in text for c in concepts)


def lint_rendered_output(result: dict[str, Any] | None, rendered_text: str, job_type: str, city: str, state: str) -> dict[str, Any]:
    result = result or {}
    text = rendered_text or ""
    context = build_rendering_context(result, job_type, city, state)
    violations: list[dict[str, str]] = []

    def add(code: str, message: str) -> None:
        violations.append({"code": code, "message": message})

    if context["is_commercial"] and HOMEOWNER_RE.search(text):
        add("commercial_homeowner_language", "Commercial rendered output contains homeowner/home-sale/homeowner-insurance language.")

    for url in _source_urls(result):
        cls = classify_source_for_jurisdiction(url, city, state)
        if cls["source_class"] == "wrong_local_ahj" and re.search(re.escape(cls["host"]), text, re.I):
            add("wrong_local_source_jurisdiction", f"Wrong-jurisdiction local source appears as customer-visible support: {cls['host']}.")

    has_verified = bool(VERIFIED_RE.search(text))
    has_uncertain_core = bool(result.get("needs_review")) or any(UNCERTAINTY_RE.search(n) for n in _current_names(result)) or UNCERTAINTY_RE.search(_text(result.get("confidence_reason"), text))
    if has_verified and has_uncertain_core:
        add("verified_badge_with_unverified_core_claim", "Verified badge/claim appears while core permit type/category is unknown, unverified, or needs AHJ verification.")
    if has_verified and not (result.get("permits_required") or result.get("permit_name") or result.get("permit_type")):
        add("verified_badge_with_empty_permit_set", "Verified badge/claim appears with no concrete permit set.")

    names = _current_names(result)
    if any(FALLBACK_NAME_RE.search(n) for n in names):
        add("fallback_text_in_permit_title", "Fallback/uncertainty text appears inside permit title/name slot.")

    profile = select_inspection_profile(context["vertical"], job_type)
    inspection_text = _inspection_text(result, text)
    if profile["profile"] == "commercial_restaurant":
        wanted_groups = [
            ("building rough/final", ("building rough", "building final")),
            ("hood/grease duct", ("hood", "grease duct")),
            ("fire suppression/alarm", ("fire suppression", "fire alarm")),
            ("health inspection", ("health",)),
            ("egress/occupancy final", ("egress", "occupancy final")),
        ]
        if any(not all(t in inspection_text for t in terms) for _label, terms in wanted_groups):
            add("restaurant_inspection_profile_missing", "Restaurant TI inspection profile misses commercial restaurant building/hood/fire/health/egress concepts.")
    elif profile["profile"] == "medical_clinic_ti":
        if any(t not in inspection_text for t in ("clinic", "health", "life", "accessibility", "equipment")):
            add("medical_clinic_inspection_profile_missing", "Medical clinic TI output misses clinic/health/fire/life-safety/accessibility/equipment logic.")
    elif profile["profile"] == "office_ti":
        if any(t not in inspection_text for t in ("commercial alteration", "egress", "accessibility")):
            add("office_inspection_profile_missing", "Office TI output misses commercial alteration/egress/accessibility logic.")

    if STRING_ARTIFACT_RE.search(text):
        add("string_assembly_artifact", "Rendered output contains obvious string assembly/run-on formatting artifact.")

    if has_verified:
        local_supported = any(classify_source_for_jurisdiction(u, city, state).get("source_class") == "local_ahj" for u in _source_urls(result))
        if not local_supported and context["is_commercial"]:
            add("official_claim_without_claim_specific_evidence", "Report claims official/verified support without local AHJ evidence for the core permit claim.")

    return {"passed": not violations, "violations": violations, "context": context}
