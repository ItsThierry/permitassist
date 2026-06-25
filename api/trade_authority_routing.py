"""Per-scope permit authority routing for state-administered trades.

This module fixes the class of failures where the local building department issues
Building/TI permits but a state agency administers a trade permit/inspection
(e.g. WA L&I electrical, WI DSPS commercial electrical where not delegated).

The public contract is additive: existing permit reasoning and prose remain, but
split-authority scopes are represented as separate authority cards and top-level
city inspection lists are filtered so they do not imply the city inspects a
state-administered trade.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse


@dataclass(frozen=True)
class TradeAuthorityRule:
    state: str
    scope: str
    authority: str
    authority_short: str
    authority_level: str
    permit_type: str
    source_url: str
    source_title: str
    note: str
    exception_city_patterns: tuple[str, ...] = ()
    exception_source_patterns: tuple[str, ...] = ()


STATE_TRADE_AUTHORITY_RULES: tuple[TradeAuthorityRule, ...] = (
    TradeAuthorityRule(
        state="WA",
        scope="electrical",
        authority="Washington State Department of Labor & Industries (L&I)",
        authority_short="WA L&I",
        authority_level="state",
        permit_type="Electrical Permit / Electrical Inspections",
        source_url="https://lni.wa.gov/licensing-permits/electrical/electrical-permits-fees-and-inspections/",
        source_title="Washington L&I — Electrical Permits, Fees & Inspections",
        note=(
            "Electrical work in Washington requires the correct electrical permit/inspection authority. "
            "L&I handles most jobsites unless an official city/delegated program or Tacoma Power service-area source says otherwise."
        ),
        exception_city_patterns=(r"\bseattle\b",),
        exception_source_patterns=(
            r"\bcity\s+(?:that\s+)?does\s+(?:its\s+)?own\s+(?:electrical\s+)?permits?\s+and\s+inspections?\b",
            r"\btacoma\s+power\b",
            r"\bseattle\b.{0,80}\belectrical\s+permit",
        ),
    ),
    TradeAuthorityRule(
        state="WI",
        scope="electrical",
        authority="Wisconsin Department of Safety and Professional Services (DSPS)",
        authority_short="WI DSPS",
        authority_level="state",
        permit_type="Commercial Electrical Permit / Inspection Routing",
        source_url="https://dsps.wi.gov/Pages/Programs/DelegatedAgents.aspx",
        source_title="Wisconsin DSPS — Division of Industry Services Delegated Agents",
        note=(
            "Wisconsin DSPS may administer or delegate commercial electrical permitting/inspection. "
            "Use DSPS/delegated-agent routing unless a sourced municipal delegation says the city is the commercial electrical authority."
        ),
        exception_source_patterns=(
            r"\bdelegated\b.{0,80}\bcommercial\s+electrical\b",
            r"\bmunicipalit(?:y|ies)\b.{0,100}\bcommercial\s+electrical\s+permitting\s+and\s+inspect",
        ),
    ),
)

SCOPE_LABELS = {
    "building": "Building / Tenant Improvement",
    "electrical": "Electrical",
    "mechanical": "Mechanical / HVAC",
    "plumbing": "Plumbing",
    "gas": "Gas piping / fuel gas",
    "fire": "Fire / life safety",
    "health": "Health Department",
    "utility": "Utility coordination",
    "structural": "Structural review",
    "accessibility": "Accessibility review",
}

INSPECTION_TEMPLATES = {
    "electrical": [
        {
            "stage": "Electrical rough-in / service equipment",
            "description": "Schedule with the routed electrical authority for service, feeder, panel/switchgear, grounding, and rough-in inspection.",
            "authority_scope": "electrical",
        },
        {
            "stage": "Final electrical",
            "description": "Final electrical inspection/approval must come from the routed electrical authority before energizing or closing the trade scope.",
            "authority_scope": "electrical",
        },
    ],
    "mechanical": [
        {"stage": "Mechanical rough-in", "description": "Ducting, RTU/curb, dryer exhaust, combustion air, and makeup-air work before cover.", "authority_scope": "mechanical"},
        {"stage": "Final mechanical", "description": "Final equipment, controls, exhaust/makeup air, and startup inspection.", "authority_scope": "mechanical"},
    ],
    "plumbing": [
        {"stage": "Plumbing rough-in", "description": "Water, waste, floor drains, fixtures, and backflow protection before cover.", "authority_scope": "plumbing"},
        {"stage": "Final plumbing", "description": "Final fixture, water heater, drainage, and backflow inspection.", "authority_scope": "plumbing"},
    ],
    "gas": [
        {"stage": "Gas pressure test", "description": "Fuel-gas piping/manifold pressure test before service activation.", "authority_scope": "gas"},
    ],
    "building": [
        {"stage": "Building / accessibility inspection", "description": "Tenant-improvement framing, accessibility, restroom, egress, and final building inspection.", "authority_scope": "building"},
    ],
}

_SCOPE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("electrical", (r"\belectrical\b", r"\b600\s*a\b", r"\bthree[-\s]?phase\b", r"\bservice\s+(?:upgrade|equipment|panel)\b", r"\bswitchgear\b", r"\bpanel\b")),
    ("mechanical", (r"\bmechanical\b", r"\bhvac\b", r"\brtu\b", r"\brooftop\s+unit\b", r"\bdryer\s+exhaust\b", r"\bmake[-\s]?up\s+air\b", r"\bventilation\b")),
    ("plumbing", (r"\bplumbing\b", r"\bfloor\s+drains?\b", r"\bwater\s+heater\b", r"\brestroom\b", r"\bfixtures?\b", r"\bwasher\b", r"\bwashers\b")),
    ("gas", (r"\bgas\s+(?:line|piping|manifold|dryer|service)\b", r"\bfuel\s+gas\b")),
    ("fire", (r"\bfire\b", r"\bsprinkler\b", r"\balarm\b", r"\bhood\s+suppression\b", r"\bansul\b")),
    ("health", (r"\bhealth\s+department\b", r"\bfood\s+service\b", r"\bcommercial\s+kitchen\b", r"\bgrease\s+interceptor\b")),
    ("utility", (r"\butility\b", r"\bmeter\b", r"\benergiz", r"\bpto\b", r"\binterconnection\b")),
    ("structural", (r"\bstructural\b", r"\bstructural\s+calc", r"\broof\s+load", r"\bcurb\s+anchorage\b")),
    ("accessibility", (r"\bada\b", r"\baccessib", r"\bpath[-\s]?of[-\s]?travel\b")),
    ("building", (r"\btenant\s+improvement\b", r"\bcommercial\b", r"\bchange\s+of\s+(?:use|occupancy)\b", r"\bbuild[-\s]?out\b", r"\bremodel\b", r"\balteration\b", r"\blaundromat\b")),
)

_INSPECTION_SCOPE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("electrical", (r"\belectrical\b", r"\bservice\s+equipment\b", r"\bswitchgear\b", r"\bpanel\b", r"\bfeeder\b", r"\bmeter\b")),
    ("mechanical", (r"\bmechanical\b", r"\bhvac\b", r"\brtu\b", r"\bdryer\s+exhaust\b", r"\bmake[-\s]?up\s+air\b", r"\bventilation\b")),
    ("plumbing", (r"\bplumbing\b", r"\bwater\b", r"\bwaste\b", r"\bfloor\s+drain", r"\bfixtures?\b")),
    ("gas", (r"\bgas\b", r"\bfuel\b", r"\bpressure\s+test\b")),
    ("fire", (r"\bfire\b", r"\bsprinkler\b", r"\balarm\b", r"\bsuppression\b")),
    ("building", (r"\bbuilding\b", r"\bframing\b", r"\bfinal\b", r"\baccessib", r"\begress\b")),
)


def _norm_state(state: str) -> str:
    return str(state or "").strip().upper()


def _norm_city(city: str) -> str:
    return re.sub(r"\s+", " ", str(city or "").strip().lower())


def _blob(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{k} {_blob(v)}" for k, v in value.items())
    if isinstance(value, list):
        return " ".join(_blob(v) for v in value)
    return str(value or "")


def _source_dicts(result: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for source in result.get("sources") or []:
        if isinstance(source, dict):
            out.append(source)
        elif isinstance(source, str):
            out.append({"url": source, "title": source, "snippet": ""})
    for url in result.get("source_urls") or []:
        if isinstance(url, str) and not any(s.get("url") == url for s in out):
            out.append({"url": url, "title": url, "snippet": ""})
    return out


def _source_text(source: dict[str, Any]) -> str:
    return " ".join(str(source.get(k) or "") for k in ("title", "snippet", "description", "content", "url", "source_title", "source_url"))


def _host(url: str) -> str:
    try:
        return urlparse(str(url or "")).hostname or ""
    except Exception:
        return ""


def infer_work_scopes(job_type: str, result: dict[str, Any]) -> list[str]:
    text = _blob({
        "job_type": job_type,
        "permit_kind": result.get("permit_kind"),
        "permit_name": result.get("permit_name"),
        "permit_type": result.get("permit_type"),
        "job_summary": result.get("job_summary"),
        "summary": result.get("summary"),
        "permits_required": result.get("permits_required"),
        "companion_permits": result.get("companion_permits"),
        "requirements": result.get("requirements"),
        "what_to_bring": result.get("what_to_bring"),
        "inspections": result.get("inspections"),
    }).lower()
    scopes: list[str] = []
    for scope, patterns in _SCOPE_PATTERNS:
        if any(re.search(pattern, text, flags=re.I) for pattern in patterns):
            scopes.append(scope)
    if result.get("permit_required") is True and not scopes:
        scopes.append("building")
    if any(scope in scopes for scope in ("electrical", "mechanical", "plumbing", "gas", "fire")) and "building" not in scopes:
        # For multi-trade commercial/TI work, keep the parent Building/TI card if
        # the input/result looks commercial or change-of-use. Do not add it for
        # isolated residential single-trade controls.
        if re.search(r"\b(commercial|tenant|change of use|change of occupancy|laundromat|retail|restaurant|clinic|build[-\s]?out)\b", text, flags=re.I):
            scopes.insert(0, "building")
    ordered = [scope for scope, _ in _SCOPE_PATTERNS if scope in set(scopes)]
    return ordered


def _base_authority(result: dict[str, Any], city: str, state: str) -> dict[str, Any]:
    city_name = str(city or result.get("city") or "Local").strip() or "Local"
    authority = (
        result.get("applying_office")
        or result.get("building_dept_name")
        or result.get("office_name")
        or f"City of {city_name} Building Department"
    )
    return {
        "authority": str(authority),
        "authority_short": str(authority),
        "authority_level": "city",
        "source_url": "",
        "source_title": "Local building department / primary permit source",
        "confidence": "base_jurisdiction",
        "note": "Primary local permitting authority for building/TI scopes unless a scope-specific routing source overrides a trade.",
    }


def _rule_for(state: str, scope: str) -> TradeAuthorityRule | None:
    state = _norm_state(state)
    for rule in STATE_TRADE_AUTHORITY_RULES:
        if rule.state == state and rule.scope == scope:
            return rule
    return None


def _source_supports_rule(rule: TradeAuthorityRule, sources: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    for source in sources:
        text = _source_text(source).lower()
        url = str(source.get("url") or source.get("source_url") or "")
        host = _host(url).lower()
        if rule.state == "WA" and rule.scope == "electrical":
            if (
                ("lni.wa.gov" in host and ("electrical" in text or "permit" in text or not text.strip(url).strip()))
                or re.search(r"electrical\s+permits?.{0,80}(?:l\s*&\s*i|labor\s+and\s+industries)", text, flags=re.I)
                or re.search(r"electrical\s+inspections?.{0,80}(?:l\s*&\s*i|labor\s+and\s+industries)", text, flags=re.I)
            ):
                return {
                    "url": url or rule.source_url,
                    "title": source.get("title") or source.get("source_title") or rule.source_title,
                    "snippet": source.get("snippet") or source.get("description") or "Electrical permits/inspections route through Washington L&I unless a delegated city/source applies.",
                    "binding": "source_text",
                }
        if rule.state == "WI" and rule.scope == "electrical":
            if "dsps.wi.gov" in host or re.search(r"\bdsps\b.{0,120}\b(?:delegated|commercial electrical|inspection|permit)", text, flags=re.I):
                return {
                    "url": url or rule.source_url,
                    "title": source.get("title") or source.get("source_title") or rule.source_title,
                    "snippet": source.get("snippet") or source.get("description") or "DSPS/delegated-agent routing controls commercial electrical permitting/inspection unless a municipality is delegated.",
                    "binding": "source_text",
                }
    return None


def _source_indicates_city_exception(rule: TradeAuthorityRule, city: str, sources: Iterable[dict[str, Any]]) -> bool:
    city_lc = _norm_city(city)
    if city_lc and any(re.search(pattern, city_lc, flags=re.I) for pattern in rule.exception_city_patterns):
        return True
    for source in sources:
        text = _source_text(source).lower()
        # Only treat exception patterns as city-owned if the source is not merely
        # the generic state page describing possible exceptions.
        host = _host(str(source.get("url") or source.get("source_url") or "")).lower()
        is_generic_state_page = (rule.state == "WA" and "lni.wa.gov" in host) or (rule.state == "WI" and "dsps.wi.gov" in host)
        if is_generic_state_page:
            continue
        if any(re.search(pattern, text, flags=re.I) for pattern in rule.exception_source_patterns):
            return True
    return False


def _overlay_source_ref(rule: TradeAuthorityRule) -> dict[str, str]:
    return {
        "url": rule.source_url,
        "title": rule.source_title,
        "snippet": rule.note,
        "binding": "state_overlay",
    }


def build_trade_authority_routing(result: dict[str, Any], job_type: str = "", city: str = "", state: str = "") -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"routing_map": {}, "authority_cards": [], "warnings": []}
    state_norm = _norm_state(state or result.get("state") or "")
    scopes = infer_work_scopes(job_type, result)
    base = _base_authority(result, city, state_norm)
    sources = _source_dicts(result)
    routing_map: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    for scope in scopes:
        rule = _rule_for(state_norm, scope)
        if rule and not _source_indicates_city_exception(rule, city, sources):
            source_ref = _source_supports_rule(rule, sources) or _overlay_source_ref(rule)
            routing_map[scope] = {
                "scope": scope,
                "scope_label": SCOPE_LABELS.get(scope, scope.title()),
                "authority": rule.authority,
                "authority_short": rule.authority_short,
                "authority_level": rule.authority_level,
                "permit_type": rule.permit_type,
                "source_ref": source_ref,
                "confidence": "source_bound" if source_ref.get("binding") == "source_text" else "state_overlay",
                "routing_note": rule.note,
            }
        else:
            routing_map[scope] = {
                "scope": scope,
                "scope_label": SCOPE_LABELS.get(scope, scope.title()),
                "authority": base["authority"],
                "authority_short": base["authority_short"],
                "authority_level": base["authority_level"],
                "permit_type": _default_permit_type(scope, result),
                "source_ref": {
                    "url": base.get("source_url", ""),
                    "title": base.get("source_title", "Local building department / primary permit source"),
                    "snippet": base.get("note", ""),
                    "binding": "base_jurisdiction",
                },
                "confidence": base["confidence"],
                "routing_note": base["note"],
            }

    cards = _authority_cards(routing_map, result, base_authority=base["authority"])
    conflicts = detect_routing_conflicts(result, routing_map, base_authority=base["authority"])
    warnings.extend(conflicts)
    return {"routing_map": routing_map, "authority_cards": cards, "warnings": warnings}


def _default_permit_type(scope: str, result: dict[str, Any]) -> str:
    if scope == "building":
        return str(result.get("permit_name") or result.get("permit_type") or "Commercial Building / Tenant Improvement Permit")
    return f"{SCOPE_LABELS.get(scope, scope.title())} permit/review"


def _inspection_scope(item: Any) -> str:
    text = _blob(item).lower()
    for scope, patterns in _INSPECTION_SCOPE_PATTERNS:
        if any(re.search(pattern, text, flags=re.I) for pattern in patterns):
            return scope
    return "building" if text else ""


def _normalize_inspection(item: Any, scope_hint: str = "") -> dict[str, Any]:
    if isinstance(item, dict):
        out = copy.deepcopy(item)
    else:
        out = {"stage": str(item or ""), "description": ""}
    out.setdefault("authority_scope", scope_hint or _inspection_scope(out))
    return out


def _inspections_for_scope(scope: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    explicit: list[dict[str, Any]] = []
    for key in ("inspections", "inspect_checklist", "inspection_requirements"):
        for item in result.get(key) or []:
            item_scope = _inspection_scope(item)
            if item_scope == scope:
                explicit.append(_normalize_inspection(item, scope))
    if explicit:
        return _dedupe_inspections(explicit)
    return [copy.deepcopy(item) for item in INSPECTION_TEMPLATES.get(scope, [])]


def _dedupe_inspections(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = re.sub(r"\s+", " ", _blob(item).lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _authority_cards(routing_map: dict[str, dict[str, Any]], result: dict[str, Any], *, base_authority: str) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for scope, route in routing_map.items():
        authority = route.get("authority") or base_authority
        card = grouped.setdefault(authority, {
            "authority": authority,
            "authority_short": route.get("authority_short") or authority,
            "authority_level": route.get("authority_level") or "city",
            "scopes": [],
            "permit_types": [],
            "inspections": [],
            "source_refs": [],
            "notes": [],
        })
        card["scopes"].append(scope)
        permit_type = route.get("permit_type") or _default_permit_type(scope, result)
        if permit_type not in card["permit_types"]:
            card["permit_types"].append(permit_type)
        for inspection in _inspections_for_scope(scope, result):
            card["inspections"].append(inspection)
        source_ref = route.get("source_ref") or {}
        if source_ref and source_ref not in card["source_refs"]:
            card["source_refs"].append(source_ref)
        note = route.get("routing_note")
        if note and note not in card["notes"]:
            card["notes"].append(note)
    for card in grouped.values():
        card["inspections"] = _dedupe_inspections(card["inspections"])
    return list(grouped.values())


def _external_scopes(routing_map: dict[str, dict[str, Any]], base_authority: str) -> set[str]:
    return {
        scope for scope, route in routing_map.items()
        if str(route.get("authority") or "") != str(base_authority or "")
    }


def _filter_top_level_inspections(result: dict[str, Any], external_scopes: set[str]) -> None:
    if not external_scopes:
        return
    for key in ("inspections", "inspect_checklist", "inspection_requirements"):
        value = result.get(key)
        if not isinstance(value, list):
            continue
        kept = [item for item in value if _inspection_scope(item) not in external_scopes]
        if kept:
            result[key] = kept
        else:
            result.pop(key, None)


def _permit_scope(item: Any) -> str:
    if isinstance(item, dict):
        title_text = _blob({
            "permit_type": item.get("permit_type"),
            "portal_selection": item.get("portal_selection"),
            "name": item.get("name"),
        }).lower()
        kind_text = _blob({
            "kind": item.get("kind"),
            "permit_kind": item.get("permit_kind"),
        }).lower()
    else:
        title_text = _blob(item).lower()
        kind_text = ""
    checks = (
        ("gas", ("gas line", "gas piping", "gas permit", "fuel gas")),
        ("plumbing", ("plumbing", "fixture", "water heater", "floor drain", "sink", "restroom", "shower")),
        ("mechanical", ("mechanical", "hvac", "rtu", "furnace", "condenser", "exhaust", "dryer exhaust")),
        ("electrical", ("electrical", "electric", "service", "panel", "meter", "switchgear")),
        ("fire", ("fire", "sprinkler", "alarm", "ansul", "suppression")),
        ("building", ("building", "tenant improvement", "construction", "alteration", "change of use", "occupancy")),
    )
    for scope, terms in checks:
        if any(term in title_text for term in terms):
            return scope
    for scope, terms in checks:
        if any(term in kind_text for term in terms):
            return scope
    return ""


def _upsert_external_permit_cards(result: dict[str, Any], routing_map: dict[str, dict[str, Any]], base_authority: str) -> None:
    external_scopes = _external_scopes(routing_map, base_authority)
    if not external_scopes:
        return
    raw_permits = result.get("permits_required")
    permits = [copy.deepcopy(p) for p in raw_permits if isinstance(p, dict)] if isinstance(raw_permits, list) else []
    existing_external_scopes: set[str] = set()
    for permit in permits:
        scope = _permit_scope(permit)
        if scope not in external_scopes:
            continue
        route = routing_map.get(scope) or {}
        authority = route.get("authority") or "State trade authority"
        permit["authority"] = authority
        permit["authority_level"] = route.get("authority_level") or "state"
        permit.setdefault("source_url", (route.get("source_ref") or {}).get("url"))
        permit.setdefault(
            "notes",
            f"Apply/schedule this {SCOPE_LABELS.get(scope, scope)} scope with {authority}, not the city building counter, unless an official delegated-local source says otherwise.",
        )
        existing_external_scopes.add(scope)
    existing_blob = _blob(permits).lower()
    for scope in sorted(external_scopes - existing_external_scopes):
        route = routing_map.get(scope) or {}
        authority = route.get("authority") or "State trade authority"
        permit_type = route.get("permit_type") or _default_permit_type(scope, result)
        if route.get("authority_short", "").lower() in existing_blob and scope in existing_blob:
            continue
        permits.append({
            "permit_type": permit_type,
            "required": True,
            "authority": authority,
            "authority_level": route.get("authority_level") or "state",
            "portal_selection": permit_type,
            "notes": f"Apply/schedule this {SCOPE_LABELS.get(scope, scope)} scope with {authority}, not the city building counter, unless an official delegated-local source says otherwise.",
            "source_url": (route.get("source_ref") or {}).get("url"),
        })
    if permits:
        result["permits_required"] = permits


def detect_routing_conflicts(result: dict[str, Any], routing_map: dict[str, dict[str, Any]], *, base_authority: str) -> list[str]:
    warnings: list[str] = []
    external_scopes = _external_scopes(routing_map, base_authority)
    if not external_scopes:
        return warnings
    for key in ("inspections", "inspect_checklist", "inspection_requirements"):
        for item in result.get(key) or []:
            scope = _inspection_scope(item)
            if scope in external_scopes:
                route = routing_map.get(scope) or {}
                warnings.append(
                    f"{SCOPE_LABELS.get(scope, scope.title())} inspection moved to {route.get('authority_short') or route.get('authority')} authority card."
                )
    return warnings


def _business_license_note(result: dict[str, Any], city: str) -> str:
    text_parts = []
    for source in _source_dicts(result):
        text_parts.append(_source_text(source))
    text_parts.append(_blob({
        "requirements": result.get("requirements"),
        "what_to_bring": result.get("what_to_bring"),
        "pro_tips": result.get("pro_tips"),
        "common_mistakes": result.get("common_mistakes"),
        "summary": result.get("summary"),
    }))
    text = " ".join(text_parts)
    city_name = str(city or result.get("city") or "the city").strip() or "the city"
    if re.search(r"\bbusiness\s+license\s+or\s+endorsement\b", text, flags=re.I) or re.search(r"\bcontractors?\s+working\s+within\s+city\s+limits\b.{0,120}\bbusiness\s+license", text, flags=re.I):
        return f"Contractors working within {city_name} city limits must confirm the city business license/endorsement requirement before permit submittal."
    return ""


def _jurisdiction_routing_summary(cards: list[dict[str, Any]]) -> str:
    if not cards:
        return ""
    parts: list[str] = []
    for card in cards:
        raw_value = card.get("scopes")
        raw_scopes: list[Any] = raw_value if isinstance(raw_value, list) else []
        scope_labels = [SCOPE_LABELS.get(str(scope)) or str(scope).title() for scope in raw_scopes if scope]
        scopes = ", ".join(scope_labels)
        authority = card.get("authority_short") or card.get("authority")
        if scopes and authority:
            parts.append(f"{scopes}: {authority}")
    return " · ".join(parts[:5])


def apply_trade_authority_routing(result: dict[str, Any], job_type: str = "", city: str = "", state: str = "") -> dict[str, Any]:
    """Attach/repair per-scope authority routing on a customer-visible result.

    Additive behavior:
    - Always attaches `permit_routing_map` and `permit_authority_cards` when scopes are detected.
    - Only modifies `permits_required` and top-level inspections when a scope is routed away from the base city authority.
    - Does not remove fee, timeline, scope-trigger, or existing permit prose.
    """
    if not isinstance(result, dict):
        return result
    out = copy.deepcopy(result)
    routing = build_trade_authority_routing(out, job_type, city, state)
    routing_map = routing.get("routing_map") or {}
    cards = routing.get("authority_cards") or []
    if not routing_map:
        return out

    base = _base_authority(out, city, state)
    external_scopes = _external_scopes(routing_map, base["authority"])
    if not external_scopes:
        # Preserve existing single-authority behavior byte-for-byte on unaffected
        # lookups. The new customer-visible cards/map are a split-authority UX,
        # not a reason to bloat or reshape every result.
        return out

    _upsert_external_permit_cards(out, routing_map, base["authority"])
    _filter_top_level_inspections(out, external_scopes)

    out["permit_routing_map"] = routing_map
    out["permit_authority_cards"] = cards
    summary = _jurisdiction_routing_summary(cards)
    if summary:
        out["jurisdiction_routing_summary"] = summary
    warnings = list(routing.get("warnings") or [])
    if warnings:
        existing = out.get("warnings") if isinstance(out.get("warnings"), list) else []
        out["warnings"] = list(dict.fromkeys([*existing, *warnings]))

    business_note = _business_license_note(out, city)
    if business_note and not out.get("city_contractor_registration"):
        out["city_contractor_registration"] = business_note

    return out
