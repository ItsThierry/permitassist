from __future__ import annotations

"""Residential universal lookup gate.

This is the deterministic customer-result assembly gate for the 2026-06-26
residential universal-fix plan. It is deliberately anti-neuter: it preserves
source-backed guidance and triggered filing rows, downgrades uncertain zoning /
threshold items to VERIFY/CONDITIONAL, and removes only contradicted,
wrong-template, or explicit-negation-violating content.
"""

import copy
import re
from typing import Any

try:
    from scope_contract import has_any_unnegated
except ImportError:  # pragma: no cover - package import path
    from .scope_contract import has_any_unnegated

RESIDENTIAL_UNIVERSAL_GATE_VERSION = "residential_universal_gate_v1_20260626"
DECISION_STATES = {"REQUIRED", "NOT_REQUIRED", "EXEMPT", "CONDITIONAL", "VERIFY"}

PHSKC_PLUMBING_PERMIT_URL = "https://kingcounty.gov/en/dept/dph/health-safety/environmental-health/plumbing-gas-piping/applications-and-permits"
PHSKC_PLUMBING_AUTHORITY = "Public Health — Seattle & King County Plumbing and Gas Piping Program"
SEATTLE_SDCI_ELECTRICAL_URL = "https://www.seattle.gov/sdci/permits/permits-we-issue-(a-z)/electrical-permit"
PANEL_ACTION_TERMS = (
    "panel upgrade", "service upgrade", "upgrade panel", "upgrade service", "replace panel",
    "panel replacement", "new panel", "new electrical panel", "new main panel", "meter main",
    "subpanel", "sub-panel", "service change", "service size change",
)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def _blob(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{k} {_blob(v)}" for k, v in value.items() if not str(k).startswith("_"))
    if isinstance(value, list):
        return " ".join(_blob(v) for v in value)
    return str(value or "")


def _has(text: str, terms: tuple[str, ...]) -> bool:
    return has_any_unnegated(text, terms)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _has_panel_scope(text: str) -> bool:
    value = _norm(text)
    if _has(value, PANEL_ACTION_TERMS):
        return True
    return bool(re.search(r"\b(?:upgrade|replace|new|modify|change)\b.{0,40}\b(?:200\s*(?:amp|a)|400\s*(?:amp|a)|panel|service)\b", value, flags=re.I))


def _is_seattle_wa(city: str, state: str) -> bool:
    return str(city or "").strip().lower() == "seattle" and str(state or "").strip().upper() == "WA"


def _explicitly_negated(text: str, terms: tuple[str, ...]) -> bool:
    value = _norm(text)
    for term in terms:
        t = re.escape(term).replace(r"\ ", r"[\s/-]+")
        patterns = (
            rf"\b(?:no|without|not|no new|does not include|doesn't include|excluding|excludes)\s+(?:any\s+|new\s+)?{t}\b",
            rf"\b{t}\s+(?:not included|excluded|not in scope|outside(?: the)? scope|not proposed)\b",
        )
        if any(re.search(pattern, value, flags=re.I) for pattern in patterns):
            return True
    return False


def _scope_model(job_type: str, city: str, state: str, scope_contract: dict[str, Any] | None) -> dict[str, Any]:
    job = _norm(job_type)
    contract = scope_contract if isinstance(scope_contract, dict) else {}
    vertical = str(contract.get("vertical") or "").lower().strip()
    tokens: set[str] = set()
    negated: set[str] = set()

    is_water_heater = _has(job, ("water heater",)) or vertical == "water_heater"
    if is_water_heater:
        tokens.update({"water_heater", "plumbing"})
    if _has_panel_scope(job) or vertical == "panel_upgrade":
        tokens.update({"panel_upgrade", "electrical", "utility_coordination", "grounding"})
    if _has(job, ("shed", "storage shed", "accessory structure", "detached storage")):
        tokens.update({"shed", "accessory_structure"})
    if _has(job, ("basement finish", "finish basement", "basement remodel", "finished basement", "basement finishing")):
        tokens.update({"basement_finish", "building"})
    if _has(job, ("adu", "accessory dwelling", "garage-to-adu", "garage to adu", "garage conversion", "garage-to-accessory dwelling")) or vertical == "adu":
        tokens.update({"adu", "building", "electrical", "plumbing", "mechanical"})

    if _has(job, ("new bathroom", "bathroom", "toilet", "sink", "plumbing", "dwv", "water line", "fixture", "kitchenette", "kitchen")):
        tokens.add("plumbing")
    if _has(job, ("electrical", "outlet", "outlets", "light", "lights", "lighting", "circuit", "circuits", "wiring", "receptacle", "receptacles")):
        tokens.add("electrical")
    if _has(job, ("bath fan", "exhaust fan", "ductwork", "duct", "ducts", "ventilation", "mechanical", "heating")):
        tokens.add("mechanical")
    hvac_specific = _has(job, ("hvac", "furnace", "air conditioner", "condenser", "equipment replacement", "changeout", "change-out", "mini split", "mini-split", "ductless", "air handler", "ductwork", "ducting"))
    if (not is_water_heater or hvac_specific) and _has(job, ("hvac", "furnace", "air conditioner", "condenser", "heat pump", "equipment replacement", "changeout", "change-out", "mini split", "mini-split", "ductless", "air handler", "ductwork", "ducting")):
        tokens.add("hvac_equipment")
        tokens.add("mechanical")
    if _has(job, ("zoning", "setback", "setbacks", "lot coverage", "historic district", "overlay")):
        tokens.add("zoning")
    if _has(job, ("fire alarm", "sprinkler", "fire sprinkler", "fire suppression")):
        tokens.add("fire")
    if _has(job, ("change of occupancy", "certificate of occupancy", "new certificate of occupancy")):
        tokens.add("co_change")
    if _has(job, ("pool", "spa", "gunite")):
        tokens.add("pool_spa")

    negation_terms = {
        "plumbing": ("plumbing", "plumbing work", "fixture", "fixtures"),
        "electrical": ("electrical", "electric", "wiring", "outlets", "circuits"),
        "mechanical": ("mechanical", "ductwork", "ventilation", "bath fan", "heat", "heating", "utilities"),
        "hvac_equipment": ("hvac", "hvac equipment", "equipment replacement", "condenser", "furnace", "air conditioner"),
        "building": ("building remodel", "building work", "structural", "foundation"),
        "foundation": ("foundation", "foundation work"),
        "fire": ("fire", "fire review", "fire alarm", "sprinkler"),
        "co_change": ("certificate of occupancy", "co", "occupancy change"),
        "planning": ("planning", "planning review"),
        "pool_spa": ("pool", "spa"),
    }
    for token, terms in negation_terms.items():
        if _explicitly_negated(job, terms):
            negated.add(token)
            if token != "hvac_equipment":
                tokens.discard(token)
    # Bath-fan/ductwork jobs may explicitly negate HVAC equipment while keeping
    # mechanical ventilation active.
    if "hvac_equipment" in negated:
        tokens.discard("hvac_equipment")

    shed_area = None
    area_match = re.search(r"\b(\d{1,4})\s*(?:sq\.?\s*ft|sf|square\s+feet)\b", job)
    dim_match = re.search(r"\b(\d{1,2})\s*x\s*(\d{1,2})\b", job)
    if area_match:
        shed_area = int(area_match.group(1))
    elif dim_match:
        shed_area = int(dim_match.group(1)) * int(dim_match.group(2))

    return {
        "version": RESIDENTIAL_UNIVERSAL_GATE_VERSION,
        "category": contract.get("category") or "residential",
        "vertical": vertical or ("shed" if "shed" in tokens else "generic"),
        "city": city,
        "state": str(state or "").upper().strip(),
        "active_scope_tokens": sorted(tokens),
        "explicit_negations": sorted(negated),
        "shed_area_sqft": shed_area,
        "decision_states": sorted(DECISION_STATES),
    }


def _row(permit_type: str, family: str, trigger: str, *, decision: str = "REQUIRED", required: bool = True, ahj_name: str = "", source_url: str = "") -> dict[str, Any]:
    return {
        "permit_type": permit_type,
        "filing_family": family,
        "required": required,
        "decision": decision,
        "trigger_signal_ids": [trigger],
        "derived_from": [trigger],
        "scope_trigger": trigger,
        **({"ahj_name": ahj_name} if ahj_name else {}),
        **({"source_url": source_url} if source_url else {}),
    }


def _first_source_url(result: dict[str, Any], contains: str = "") -> str:
    contains_lc = contains.lower()
    for source in result.get("sources") or []:
        if isinstance(source, dict):
            text = _norm(" ".join(str(source.get(k) or "") for k in ("url", "title", "publisher")))
            if not contains_lc or contains_lc in text:
                return str(source.get("url") or "")
        elif isinstance(source, str) and (not contains_lc or contains_lc in source.lower()):
            return source
    for url in result.get("source_urls") or []:
        if not contains_lc or contains_lc in str(url).lower():
            return str(url)
    return ""


def _append_source(result: dict[str, Any], url: str, title: str, city: str, state: str) -> None:
    if not url:
        return
    sources = result.setdefault("sources", [])
    if not isinstance(sources, list):
        sources = []
        result["sources"] = sources
    urls = {str(src.get("url") or src.get("source_url") or "") for src in sources if isinstance(src, dict)} | {str(src) for src in sources if isinstance(src, str)}
    if url not in urls:
        sources.append({"url": url, "title": title, "publisher": title.split(" permits")[0], "source_type": "official", "jurisdiction": f"{city}, {state}".strip(", ")})
    source_urls = result.setdefault("source_urls", [])
    if isinstance(source_urls, list) and url not in source_urls:
        source_urls.append(url)


def _filter_water_heater_sources(result: dict[str, Any], ledger: list[dict[str, Any]]) -> None:
    """Drop wrong-scope source contamination for residential water-heater output."""
    bad_fragments = (
        "tip424", "tip 424", "cam/tip424", "mechanical-permit", "refrigeration-permit",
        "commercial and multifamily", "commercial/multifamily", "multifamily buildings",
    )
    removed = 0
    if isinstance(result.get("sources"), list):
        kept_sources = []
        for source in result.get("sources") or []:
            text = _norm(_blob(source))
            if any(fragment in text for fragment in bad_fragments):
                removed += 1
                continue
            kept_sources.append(source)
        result["sources"] = kept_sources
    if isinstance(result.get("source_urls"), list):
        kept_urls = []
        for url in result.get("source_urls") or []:
            url_lc = str(url or "").lower()
            if any(fragment in url_lc for fragment in bad_fragments):
                removed += 1
                continue
            kept_urls.append(url)
        result["source_urls"] = kept_urls
    if removed:
        ledger.append({"unit": "sources", "reason": "water_heater_wrong_scope_source_filter", "decision": "drop_commercial_mechanical_refrigeration_sources", "count": removed})


def _family_for_row(row: dict[str, Any]) -> str:
    value = _norm(" ".join(str(row.get(k) or "") for k in ("filing_family", "permit_type", "approval_type", "portal_selection", "title", "name")))
    if "fire" in value:
        return "fire"
    if "certificate of occupancy" in value or re.search(r"\bco\b", value):
        return "co_change"
    if "planning" in value or "zoning" in value or "setback" in value:
        return "planning"
    if "adu" in value or "accessory dwelling" in value:
        return "building_adu"
    if "electrical" in value or "electric" in value:
        return "electrical"
    if "plumbing" in value or "water heater" in value:
        return "plumbing"
    if "mechanical" in value or "hvac" in value or "bath fan" in value or "ventilation" in value:
        return "mechanical"
    if "building" in value or "shed" in value or "basement" in value:
        return "building"
    return str(row.get("filing_family") or row.get("permit_type") or "").lower()


def _row_is_source_backed(row: dict[str, Any]) -> bool:
    source_keys = ("source_url", "url", "source", "source_ref", "source_refs", "sources", "citations", "claim_citations", "evidence")
    for key in source_keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, (list, dict)) and value:
            return True
    return False


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = _family_for_row(row) or str(row.get("permit_type") or "").lower()
        if key and key not in seen:
            seen.add(key)
            out.append(row)
    return out


def _merge_scope_rows(existing_rows: list[dict[str, Any]], generated_rows: list[dict[str, Any]], model: dict[str, Any], ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge generated scope rows without discarding source-backed AHJ specifics.

    Generated rows repair missing/wrong core families. Source-backed extra rows
    such as fire/CO/planning are preserved as VERIFY/CONDITIONAL companions unless
    they violate an explicit negation or wrong-template guard.
    """
    merged: list[dict[str, Any]] = []
    used_existing: set[int] = set()
    existing_by_family: dict[str, tuple[int, dict[str, Any]]] = {}
    for idx, row in enumerate(existing_rows):
        if isinstance(row, dict):
            existing_by_family.setdefault(_family_for_row(row), (idx, row))

    for generated in generated_rows:
        family = _family_for_row(generated)
        existing_pair = existing_by_family.get(family)
        if existing_pair:
            idx, existing = existing_pair
            forbidden, reason = _item_forbidden(" ".join(str(existing.get(k) or "") for k in ("permit_type", "approval_type", "portal_selection", "notes", "summary")), model)
            if not forbidden:
                row = dict(existing)
                # Preserve source-backed AHJ wording, add canonical contract fields.
                for key in ("filing_family", "required", "decision", "trigger_signal_ids", "derived_from", "scope_trigger"):
                    row[key] = generated.get(key)
                row.setdefault("ahj_name", generated.get("ahj_name"))
                if generated.get("source_url") and not row.get("source_url"):
                    row["source_url"] = generated.get("source_url")
                merged.append(row)
                used_existing.add(idx)
                continue
            ledger.append({"unit": "permits_required", "reason": reason, "decision": "replace_source_backed_row", "family": family})
        merged.append(generated)

    generated_families = {_family_for_row(row) for row in merged}
    for idx, existing in enumerate(existing_rows):
        if idx in used_existing or not isinstance(existing, dict) or not _row_is_source_backed(existing):
            continue
        family = _family_for_row(existing)
        if family in generated_families:
            continue
        text = " ".join(str(existing.get(k) or "") for k in ("permit_type", "approval_type", "portal_selection", "notes", "summary"))
        forbidden, reason = _item_forbidden(text, model)
        if forbidden and reason != "untriggered_fire_co_planning":
            ledger.append({"unit": "permits_required", "reason": reason, "decision": "drop_source_backed_extra", "family": family})
            continue
        if family in {"fire", "co_change", "planning"}:
            extra = dict(existing)
            extra.setdefault("decision", "VERIFY")
            extra.setdefault("required", False)
            extra.setdefault("scope_trigger", "source_backed_companion_requirement")
            extra.setdefault("derived_from", ["source_backed_companion_requirement"])
            merged.append(extra)
            ledger.append({"unit": "permits_required", "reason": "preserve_source_backed_companion", "decision": "keep_as_verify", "family": family})
    return _dedupe_rows(merged)


BAD_TEMPLATE_TERMS = ("pool", "spa", "gunite", "barrier fencing", "pool barrier", "pool equipment")
HVAC_EQUIPMENT_TERMS = ("refrigerant", "thermostat", "nameplate", "condensate", "condenser", "line-set", "line set", "hvac system replacement")
FOUNDATION_TERMS = ("foundation repair", "engineered foundation", "foundation drawings")
FIRE_CO_PLANNING_TERMS = ("fire department", "fire review", "certificate of occupancy", " co paperwork", "planning permit")
PLUMBING_TERMS = ("plumbing permit", "plumbing -", "plumbing /", "plumbing work")
ELECTRICAL_TERMS = ("electrical permit", "electrical -", "electrical /", "load calculation", "wiring")
MECHANICAL_TERMS = ("mechanical permit", "mechanical -", "hvac", "refrigerant", "condenser")


def _item_forbidden(text: str, model: dict[str, Any]) -> tuple[bool, str]:
    value = _norm(text)
    tokens = set(model.get("active_scope_tokens") or [])
    negated = set(model.get("explicit_negations") or [])
    vertical = str(model.get("vertical") or "")

    if "pool_spa" not in tokens and _contains_any(value, BAD_TEMPLATE_TERMS):
        return True, "wrong_template_pool_spa"
    if vertical == "water_heater":
        if _contains_any(value, ("panel upgrade", "service upgrade", "service change", "electrical panel upgrade")):
            return True, "water_heater_untriggered_panel_service"
        if _contains_any(value, ("refrigeration permit", "mechanical permit", "hvac equipment changeout", "hvac system replacement")):
            return True, "water_heater_untriggered_mechanical_refrigeration"
        if "electrical" not in tokens and _contains_any(value, ("electrical permit", "electrical -", "electrical /")):
            return True, "water_heater_untriggered_electrical_permit"
    if "hvac_equipment" not in tokens and _contains_any(value, HVAC_EQUIPMENT_TERMS):
        return True, "wrong_template_hvac_equipment"
    if ("shed" in tokens or "foundation" in negated) and _contains_any(value, FOUNDATION_TERMS):
        return True, "explicit_negation_foundation"
    if "plumbing" in negated and _contains_any(value, PLUMBING_TERMS):
        return True, "explicit_negation_plumbing"
    if "electrical" in negated and _contains_any(value, ELECTRICAL_TERMS):
        return True, "explicit_negation_electrical"
    if "mechanical" in negated and _contains_any(value, MECHANICAL_TERMS):
        return True, "explicit_negation_mechanical_hvac"
    if "hvac_equipment" in negated and _contains_any(value, HVAC_EQUIPMENT_TERMS):
        return True, "explicit_negation_hvac_equipment"
    if vertical == "panel_upgrade" and _contains_any(value, MECHANICAL_TERMS):
        return True, "explicit_negation_mechanical_hvac"
    if "fire" not in tokens and "co_change" not in tokens and vertical in {"water_heater", "panel_upgrade", "shed", "basement_finish", "adu", "residential_remodel", ""} and _contains_any(value, FIRE_CO_PLANNING_TERMS):
        if "zoning" in value and "shed" in tokens:
            return False, ""
        return True, "untriggered_fire_co_planning"
    if "shed" in tokens:
        if "electrical" not in tokens and _contains_any(value, ("electrical permit",)):
            return True, "shed_exemption_untriggered_row"
        if _contains_any(value, ("fire department", "certificate of occupancy", "foundation repair")):
            return True, "shed_exemption_untriggered_row"
    if vertical == "panel_upgrade" and _contains_any(value, ("plumbing permit", "pool", "spa", "barrier fencing")):
        return True, "panel_negated_trade_or_template"
    return False, ""


def _clean_value(value: Any, model: dict[str, Any], ledger: list[dict[str, Any]], path: str = "") -> Any:
    removed = object()

    def clean(child: Any, child_path: str) -> Any:
        if isinstance(child, str):
            forbidden, reason = _item_forbidden(child, model)
            if forbidden:
                ledger.append({"unit": child_path, "reason": reason, "decision": "drop"})
                return removed
            return child
        if isinstance(child, list):
            list_out: list[Any] = []
            for idx, item in enumerate(child):
                cleaned = clean(item, f"{child_path}[{idx}]")
                if cleaned is not removed and cleaned not in ("", [], {}):
                    list_out.append(cleaned)
            return list_out
        if isinstance(child, dict):
            title_text = " ".join(str(child.get(k) or "") for k in ("permit_type", "approval_type", "portal_selection", "title", "name", "summary", "reason", "required_if", "notes", "value"))
            forbidden, reason = _item_forbidden(title_text, model)
            if forbidden:
                if reason == "untriggered_fire_co_planning" and _row_is_source_backed(child):
                    out = dict(child)
                    out.setdefault("decision", "VERIFY")
                    out.setdefault("required", False)
                    ledger.append({"unit": child_path, "reason": "source_backed_fire_co_planning", "decision": "keep_as_verify"})
                    return out
                ledger.append({"unit": child_path, "reason": reason, "decision": "drop"})
                return removed
            out: dict[str, Any] = {}
            for key, item in child.items():
                if str(key).startswith("_"):
                    out[key] = item
                    continue
                cleaned = clean(item, f"{child_path}.{key}" if child_path else str(key))
                if cleaned is not removed and cleaned not in ("", [], {}):
                    out[key] = cleaned
            return out
        return child

    result = clean(value, path)
    return [] if result is removed else result


def _fee_contract(result: dict[str, Any], ledger: list[dict[str, Any]]) -> None:
    fee = str(result.get("fee_range") or "")
    if fee and re.search(r"\b(?:total\s+)?(?:job|project)\s+cost\b|\btotal\s+cost\s+estimate\b", fee, flags=re.I):
        if not result.get("total_cost_estimate"):
            result["total_cost_estimate"] = fee
        result["fee_range"] = "Permit fee is separate from total project cost; verify the current permit fee schedule before quoting."
        ledger.append({"unit": "fee_range", "reason": "job_cost_not_permit_fee", "decision": "downgrade_to_fee_caveat"})


def _set_permit_kind(result: dict[str, Any], new_kind: str, *, preserve_terms: tuple[str, ...] = ()) -> None:
    existing = str(result.get("permit_kind") or "").strip()
    existing_lc = existing.lower()
    if existing and any(term in existing_lc for term in preserve_terms):
        return
    if existing and existing_lc not in {"", "other", "permit required", "building", "not required", "unknown"}:
        return
    result["permit_kind"] = new_kind


def _merge_customer_next_step(result: dict[str, Any], fallback: str, guidance: str) -> str:
    """Preserve filing-path contract caveats while adding scope guidance once."""
    existing = str(result.get("customer_next_step") or "").strip()
    existing_lc = existing.lower()
    contract_phrase_present = "no verified online filing url" in existing_lc or "no exact local filing portal" in existing_lc
    if not existing or not contract_phrase_present:
        return fallback
    guidance = guidance.strip().rstrip(".")
    if guidance and guidance.lower() not in existing_lc:
        return f"{existing.rstrip()} Also {guidance[0].lower()}{guidance[1:]}."
    return existing


def _set_rows_for_scope(result: dict[str, Any], model: dict[str, Any], city: str, state: str, ledger: list[dict[str, Any]]) -> None:
    tokens = set(model.get("active_scope_tokens") or [])
    vertical = str(model.get("vertical") or "")
    state_uc = str(state or "").upper()
    city_name = city or "Local"
    source_url = _first_source_url(result)
    ahj = str(result.get("applying_office") or result.get("building_dept_name") or f"{city_name} permit office")
    raw_existing_rows = [row for row in (result.get("permits_required") or []) if isinstance(row, dict)]
    stored_companions = [row for row in (result.get("_residential_source_backed_companions") or []) if isinstance(row, dict)]
    source_backed_companions = [
        row for row in raw_existing_rows + stored_companions
        if _row_is_source_backed(row) and _family_for_row(row) in {"fire", "co_change", "planning"}
    ]
    if source_backed_companions:
        result["_residential_source_backed_companions"] = _dedupe_rows(source_backed_companions)
    existing_rows = raw_existing_rows + stored_companions

    shed_area = model.get("shed_area_sqft") if isinstance(model.get("shed_area_sqft"), int) else None
    negated = set(model.get("explicit_negations") or [])
    shed_trade_tokens = tokens.intersection({"electrical", "plumbing", "mechanical", "hvac_equipment", "foundation", "fire", "co_change", "pool_spa"})
    shed_no_trade_triggers = not shed_trade_tokens and {"foundation", "electrical", "plumbing"}.issubset(negated) and ("mechanical" in negated or "hvac_equipment" in negated)
    is_denver = city_name.lower().strip() == "denver" and state_uc == "CO"

    if "shed" in tokens and shed_area is not None and is_denver and shed_area <= 200 and shed_no_trade_triggers:
        result["permit_required"] = False
        result["permit_decision"] = "NOT_REQUIRED"
        result["permit_verdict"] = "NO"
        result["permit_kind"] = "Not Required"
        result["permit_name"] = "No building permit required for qualifying small detached shed"
        result["not_required_reason"] = f"Small detached shed/accessory-structure exemption evaluated before default building-permit routing for {city_name}, {state_uc}; verify zoning, setbacks, easements, and utility/HOA constraints before placement."
        result["permits_required"] = []
        result["permits_required_logic"] = [{
            "permit_type": "Building Permit — Small detached shed",
            "decision": "EXEMPT",
            "included_because": "Exemption-first rule for small detached accessory structures when no foundation, electrical, plumbing, heat, utility, or occupancy work is described.",
            "scope_trigger": "source/jurisdiction-specific small-shed exemption plus explicit trade/foundation negations",
        }]
        result["related_permits"] = [{
            "permit_type": "Zoning / setback verification",
            "decision": "VERIFY",
            "required": False,
            "reason": f"{city_name} zoning, setbacks, easements, floodplain, landmark, HOA, and utility placement constraints can still apply even when the building permit is exempt.",
            "derived_from": ["small_shed_exemption"],
        }]
        result["customer_next_step"] = f"Treat the {shed_area} sq ft shed as building-permit exempt only if {city_name}'s small-accessory-structure criteria are met; verify zoning/setbacks before placement."
        result["customer_headline"] = "No building permit for qualifying small shed; verify zoning/setbacks."
        result["checklist"] = ["Confirm shed size, detached/non-habitable use, no utilities, no permanent foundation, setbacks/easements, and HOA or landmark constraints."]
        ledger.append({"unit": "permit_decision", "reason": "exemption_first_small_shed", "decision": "downgrade_required_to_not_required"})
        return

    rows: list[dict[str, Any]] = []
    if vertical == "water_heater" or "water_heater" in tokens:
        if _is_seattle_wa(city_name, state_uc):
            ahj = PHSKC_PLUMBING_AUTHORITY
            result["applying_office"] = ahj
            result["building_dept_name"] = ahj
            result["apply_url"] = PHSKC_PLUMBING_PERMIT_URL
            result["online_application_url"] = PHSKC_PLUMBING_PERMIT_URL
            existing_apply_path = dict(result.get("apply_path") or {}) if isinstance(result.get("apply_path"), dict) else {}
            result["apply_path"] = {
                **existing_apply_path,
                "state": "RESOLVED_PORTAL",
                "channel": "online_portal",
                "support_level": "verified path",
                "portal_url": PHSKC_PLUMBING_PERMIT_URL,
                "platform": "Public Health Permit Center",
                "office_name": ahj,
                "permit_category": "Plumbing / Gas Piping Permit",
                "permit_type": "Residential Plumbing Permit — Water Heater Replacement",
                "verification_note": "Seattle plumbing and gas-piping permits route through Public Health — Seattle & King County; confirm the water-heater fixture/type before submitting.",
            }
            _append_source(result, PHSKC_PLUMBING_PERMIT_URL, "Public Health — Seattle & King County plumbing and gas piping permits", city, state)
            _append_source(result, SEATTLE_SDCI_ELECTRICAL_URL, "Seattle SDCI electrical permit conditional trigger", city, state)
        permit_source_url = PHSKC_PLUMBING_PERMIT_URL if _is_seattle_wa(city_name, state_uc) else source_url
        rows.append(_row("Residential Plumbing Permit — Water Heater Replacement", "plumbing", "water_heater_replacement", ahj_name=ahj, source_url=permit_source_url))
        result["permit_kind"] = "Plumbing"
        result["permit_name"] = "Residential Plumbing Permit — Water Heater Replacement"
        result["related_permits"] = [
            {
                "permit_type": "Fuel gas / gas piping permit or inspection",
                "decision": "CONDITIONAL",
                "required": False,
                "required_if": "gas piping is capped, abandoned, relocated, pressure-tested, or otherwise modified while removing the gas water heater",
                "authority": ahj,
            },
            {
                "permit_type": "Electrical Permit — Water-heater circuit / equipment connection",
                "decision": "CONDITIONAL",
                "required": False,
                "required_if": "new wiring, a new circuit, disconnect replacement, breaker/panel modification, or hardwired equipment connection is added or altered",
                "authority": "Seattle Department of Construction and Inspections" if _is_seattle_wa(city_name, state_uc) else ahj,
                "source_url": SEATTLE_SDCI_ELECTRICAL_URL if _is_seattle_wa(city_name, state_uc) else source_url,
            },
        ]
        result["customer_next_step"] = _merge_customer_next_step(
            result,
            f"File the water-heater plumbing permit with {ahj}; confirm any gas cap/abandonment and electrical-connection details before final submission.",
            "confirm any gas cap/abandonment and electrical-connection details before final submission",
        )
    elif vertical == "panel_upgrade" or "panel_upgrade" in tokens:
        rows.append(_row("Electrical Permit — Service / Panel Upgrade", "electrical", "panel_service_upgrade", ahj_name=ahj, source_url=source_url))
        if "hvac_equipment" in tokens:
            rows.append(_row("Mechanical Permit — HVAC Equipment Replacement", "mechanical", "hvac_equipment_replacement", ahj_name=ahj, source_url=source_url))
            _set_permit_kind(result, "Mechanical", preserve_terms=("mep", "trade"))
            result["permit_name"] = "Mechanical Permit"
        else:
            _set_permit_kind(result, "Electrical", preserve_terms=("mep", "trade"))
            result["permit_name"] = "Electrical Permit — Service / Panel Upgrade"
        utility_guidance = "Coordinate utility meter release, grounding/bonding, panel schedule, load calculation, and inspection timing before shutdown."
        result["customer_next_step"] = _merge_customer_next_step(
            result,
            f"File the electrical service/panel upgrade with {ahj}; {utility_guidance[0].lower()}{utility_guidance[1:]}",
            utility_guidance,
        )
        tips = result.setdefault("pro_tips", [])
        if isinstance(tips, list):
            guidance = "Coordinate utility meter release, grounding/bonding, panel schedule, service-load calculation, and inspection timing before any service shutdown."
            if guidance not in tips:
                tips.append(guidance)
    elif vertical == "adu" or "adu" in tokens:
        rows.extend([
            _row("Building Permit — ADU / Garage Conversion", "building_adu", "adu_conversion", ahj_name=ahj, source_url=source_url),
            _row("Electrical Permit — ADU Circuits / Load", "electrical", "adu_electrical", ahj_name=ahj, source_url=source_url),
            _row("Plumbing Permit — ADU Kitchen/Bath", "plumbing", "adu_plumbing", ahj_name=ahj, source_url=source_url),
            _row("Mechanical Permit — ADU Ventilation / Heating", "mechanical", "adu_mechanical", ahj_name=ahj, source_url=source_url),
        ])
        _set_permit_kind(result, "Building", preserve_terms=("adu", "accessory dwelling"))
        result["permit_name"] = "Building Permit — ADU / Garage Conversion"
        adu_guidance = f"Prepare one ADU filing packet with {ahj}: building/ADU plans plus electrical, plumbing, and mechanical trade sheets."
        result["customer_next_step"] = _merge_customer_next_step(result, adu_guidance, adu_guidance)
    elif "shed" in tokens:
        rows.append(_row("Building/Zoning Verification — Detached Shed", "building", "shed_accessory_structure", decision="VERIFY", required=True, ahj_name=ahj, source_url=source_url))
        if "electrical" in tokens:
            rows.append(_row("Electrical Permit — Shed Circuit / Lighting", "electrical", "shed_electrical", ahj_name=ahj, source_url=source_url))
        if "plumbing" in tokens:
            rows.append(_row("Plumbing Permit — Shed Fixtures / Utility Connection", "plumbing", "shed_plumbing", ahj_name=ahj, source_url=source_url))
        if "mechanical" in tokens or "hvac_equipment" in tokens:
            rows.append(_row("Mechanical Permit — Shed Heat / Ventilation", "mechanical", "shed_mechanical", ahj_name=ahj, source_url=source_url))
        _set_permit_kind(result, "Building")
        result["permit_name"] = "Detached Shed Permit / Zoning Verification"
        result["related_permits"] = [{
            "permit_type": "Zoning / setback verification",
            "decision": "VERIFY",
            "required": False,
            "reason": f"Verify {city_name} shed size threshold, zoning setbacks, easements, floodplain/landmark overlays, and utility/HOA constraints before placement.",
            "derived_from": ["shed_threshold_unconfirmed"],
        }]
        shed_guidance = f"Verify {city_name}, {state_uc} shed thresholds and zoning/setback rules before treating the shed as permit-exempt; file any triggered trade permit for utilities or equipment."
        result["customer_next_step"] = _merge_customer_next_step(result, shed_guidance, shed_guidance)
    elif "basement_finish" in tokens:
        rows.append(_row("Residential Building Permit — Basement Finish", "building", "basement_finish", ahj_name=ahj, source_url=source_url))
        if "plumbing" in tokens:
            rows.append(_row("Plumbing Permit — Basement Bathroom / Fixtures", "plumbing", "basement_plumbing", ahj_name=ahj, source_url=source_url))
        if "electrical" in tokens:
            dli_url = _first_source_url(result, "dli")
            rows.append(_row("Electrical Permit — Basement Finish Wiring / Lighting", "electrical", "basement_electrical", ahj_name="Minnesota Department of Labor and Industry" if state_uc == "MN" else ahj, source_url=dli_url or source_url))
            if state_uc == "MN" and dli_url:
                _append_source(result, dli_url, "Minnesota DLI electrical permits", city, state)
        if "mechanical" in tokens:
            rows.append(_row("Mechanical Permit — Bath Fan / Ductwork / Ventilation", "mechanical", "basement_bath_fan_ductwork", ahj_name=ahj, source_url=source_url))
        _set_permit_kind(result, "Building")
        result["permit_name"] = "Residential Building Permit — Basement Finish"
        if state_uc == "MN":
            basement_guidance = "Submit the basement-finish building packet with triggered plumbing/electrical/mechanical trade scope; route Minnesota electrical permitting through DLI where applicable."
        else:
            basement_guidance = "Submit the basement-finish building packet with triggered plumbing/electrical/mechanical trade scope; verify the local electrical permitting path before filing."
        result["customer_next_step"] = _merge_customer_next_step(result, basement_guidance, basement_guidance)

    if rows:
        result["permit_required"] = True
        result["permit_decision"] = "REQUIRED"
        result["permit_verdict"] = "YES"
        result["permits_required"] = _merge_scope_rows(existing_rows, rows, model, ledger)
        result["permits_required_logic"] = [
            {"permit_type": row.get("permit_type"), "included_because": row.get("scope_trigger"), "scope_trigger": row.get("scope_trigger"), "filing_family": row.get("filing_family")}
            for row in result["permits_required"]
        ]
        if not result.get("customer_headline"):
            headline_kind = result.get("permit_kind") or result.get("permit_name") or "permit"
            result["customer_headline"] = f"Permit required: {headline_kind}."
        ledger.append({"unit": "permits_required", "reason": "scope_grounded_reconciler", "decision": "replace_with_scope_bound_rows", "families": [r.get("filing_family") for r in rows]})


def _scope_derived_lists(result: dict[str, Any], model: dict[str, Any], ledger: list[dict[str, Any]]) -> None:
    tokens = set(model.get("active_scope_tokens") or [])
    vertical = str(model.get("vertical") or "")
    if vertical == "panel_upgrade" or "panel_upgrade" in tokens:
        result["checklist"] = [
            "Confirm service size, panel amperage, meter/main location, grounding/bonding, panel schedule, and load calculation.",
            "Coordinate utility meter release, inspection timing, and any planned outage before service work.",
            "Have licensed electrical contractor information and equipment specifications ready for the electrical permit.",
        ]
    elif "basement_finish" in tokens:
        electrical_item = "Electrical outlet, lighting, panel/circuit, and local electrical permit information."
        if str(model.get("state") or "").upper() == "MN":
            electrical_item = "Electrical outlet, lighting, panel/circuit, and Minnesota DLI electrical permit information."
        result["checklist"] = [
            "Basement finish floor plan with room labels, egress/rescue opening details, smoke/CO alarm locations if locally required, and energy/insulation notes.",
            "Plumbing fixture/DWV/water-supply scope for the basement bathroom.",
            electrical_item,
            "Bath fan duct termination, ductwork, and ventilation details; do not use HVAC equipment-changeout checklist unless equipment is being replaced.",
        ]
    elif vertical == "adu" or "adu" in tokens:
        result["documents_needed"] = [
            "ADU/garage-conversion plans showing occupancy conversion, life-safety, egress, fire separation, and energy/ventilation details.",
            "Electrical plan/load information, plumbing fixture/DWV plan, and mechanical ventilation/heating details.",
        ]
    elif vertical == "water_heater" or "water_heater" in tokens:
        result.setdefault("checklist", [
            "Water-heater model/capacity, location, gas/venting or electrical connection details, seismic/support details if locally required, and inspection access.",
        ])
        city_name = str(model.get("city") or "").strip()
        state_uc = str(model.get("state") or "").upper().strip()
        ahj = PHSKC_PLUMBING_AUTHORITY if _is_seattle_wa(city_name, state_uc) else str(result.get("applying_office") or result.get("building_dept_name") or "local permit office")
        result["related_permits"] = [
            {
                "permit_type": "Fuel gas / gas piping permit or inspection",
                "decision": "CONDITIONAL",
                "required": False,
                "required_if": "gas piping is capped, abandoned, relocated, pressure-tested, or otherwise modified while removing the gas water heater",
                "authority": ahj,
            },
            {
                "permit_type": "Electrical circuit / equipment-connection permit",
                "decision": "CONDITIONAL",
                "required": False,
                "required_if": "new wiring, a new circuit, disconnect replacement, breaker/panel modification, or hardwired equipment connection is added or altered",
                "authority": "Seattle Department of Construction and Inspections" if _is_seattle_wa(city_name, state_uc) else ahj,
                "source_url": SEATTLE_SDCI_ELECTRICAL_URL if _is_seattle_wa(city_name, state_uc) else "",
            },
        ]


def apply_residential_universal_gate(result: dict[str, Any], job_type: str, city: str, state: str, *, scope_contract: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a scope-grounded residential customer result.

    The gate is safe to run multiple times. It derives rows from request scope,
    strips contradictions/wrong-template content, fixes fee typing, and records a
    private suppression ledger.
    """
    out = copy.deepcopy(result) if isinstance(result, dict) else {}
    model = _scope_model(job_type, city, state, scope_contract or out.get("_scope_contract"))
    category = str(model.get("category") or "").lower()
    tokens = set(model.get("active_scope_tokens") or [])
    if category == "commercial" or not tokens.intersection({"water_heater", "panel_upgrade", "shed", "basement_finish", "adu"}):
        out.setdefault("_canonical_scope_model", model)
        return out

    ledger: list[dict[str, Any]] = []
    _fee_contract(out, ledger)
    _set_rows_for_scope(out, model, city, state, ledger)

    # Clean after rows are reconciled so stale/model rows do not re-enter lower sections.
    for key in ("permits_required", "permits_required_logic", "companion_permits", "related_permits", "checklist", "documents_needed", "what_to_bring", "requirements", "pro_tips", "common_mistakes", "watch_out", "inspections", "inspect_checklist", "inspection_requirements", "apply_path", "customer_next_step", "customer_headline", "summary", "job_summary"):
        if key in out:
            out[key] = _clean_value(out.get(key), model, ledger, key)

    _scope_derived_lists(out, model, ledger)
    if str(model.get("vertical") or "") == "water_heater":
        _filter_water_heater_sources(out, ledger)
        if _is_seattle_wa(city, state):
            note = "Public Health — Seattle & King County notes replacement water-heater permit requirements start July 1, 2026; confirm effective-date handling if the work starts before that date."
            tips = out.setdefault("pro_tips", [])
            if isinstance(tips, list) and note not in tips:
                tips.append(note)
    _fee_contract(out, ledger)

    # Final consistency: NOT_REQUIRED/EXEMPT scopes must not carry filing URLs or required rows.
    if out.get("permit_decision") == "NOT_REQUIRED" or out.get("permit_required") is False:
        out["permits_required"] = []
        out["apply_url"] = ""
        out["online_application_url"] = ""
        raw_apply_path = out.get("apply_path")
        apply_path: dict[str, Any] = dict(raw_apply_path) if isinstance(raw_apply_path, dict) else {}
        apply_path.update({
            "state": "NOT_APPLICABLE",
            "channel": "no_permit_required",
            "support_level": "not applicable",
            "portal_url": None,
            "platform": None,
            "login_required": None,
            "verification_note": "No building-permit filing path is needed if the small-shed exemption criteria are met; verify zoning/setbacks separately.",
        })
        out["apply_path"] = apply_path

    out["_canonical_scope_model"] = model
    out["_residential_universal_gate"] = {
        "version": RESIDENTIAL_UNIVERSAL_GATE_VERSION,
        "suppression_ledger": ledger,
        "active_scope_tokens": model.get("active_scope_tokens"),
        "explicit_negations": model.get("explicit_negations"),
    }
    return out
