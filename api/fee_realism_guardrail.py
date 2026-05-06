from __future__ import annotations

"""Fee Realism Guardrail V1 — deterministic per-scope/per-jurisdiction fee floor.

Drafted by Forge subagent 2026-04-28 from Pass-2 deep-think analysis. Closes
the systematic 3-10x under-quote bug Opus 4.7 grading caught across all 4
cities of restaurant TI tests ($219 elec + $558 HVAC for a $8K-25K real
restaurant TI fee). Per-scope sqft floors + 25 jurisdiction multipliers +
trigger adders. Pure deterministic logic, zero LLM calls.

Called from research_permit() after detect_hidden_triggers() and before
sanitize_free_text_urls() so trigger-driven fee adders work with the
already-detected triggers.
"""

"""
Fee Realism Guardrail V1

Placement:
    research_permit() -> base LLM result -> detect_hidden_triggers(result, ...)
    -> apply_fee_realism_guardrail(result, job_type, city, state, primary_scope)
    -> sanitize_free_text_urls(result) / validate_and_sanitize_permit_result(result)

Design notes:
    - This is a credibility guardrail, not a final AHJ fee calculator.
    - Floors are intentionally conservative for commercial / complex scopes.
    - Jurisdiction multipliers are heuristic calibration factors. Where a city-specific
      calibration has not been validated against a current official fee schedule, the
      rationale is marked [verify]. Do not present these as official multipliers.
    - Residential scope is deliberately no-op unless primary_scope resolves to
      residential_adu, because the original bug is commercial / complex-scope
      under-quotation caused by the LLM selecting single-trade residential rows.
"""


import copy
import math
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


SCOPE_FEE_FLOORS: Dict[str, Dict[str, Any]] = {
    "commercial_restaurant": {
        "per_sf": 4.0,
        "min_floor": 12000,
        "note": "plan review + permit + fire + utility + health",
    },
    "commercial_office_ti": {
        "per_sf": 2.5,
        "min_floor": 5000,
        "note": "plan review + permit + sprinkler hydraulic recalc",
    },
    "commercial_medical_clinic_ti": {
        "per_sf": 3.25,
        "min_floor": 9000,
        "note": "commercial clinic TI + MEP + accessibility + fire/life-safety + health-care specialty reviews",
    },
    "commercial_retail_ti": {
        "per_sf": 2.0,
        "min_floor": 4000,
        "note": "plan review + permit + storefront + sign",
    },
    "multifamily": {
        "per_sf": 5.0,
        "min_floor": 25000,
        "note": "building + MEP + fire alarm/sprinkler + accessibility",
    },
    "commercial": {
        "per_sf": 2.5,
        "min_floor": 5000,
        "note": "generic commercial — verify with AHJ",
    },
    "residential_adu": {
        "per_sf": 12.0,
        "min_floor": 8000,
        "note": "building + MEP + accessibility + plan check",
    },
    "residential": {
        "per_sf": 0,
        "min_floor": 0,
        "note": "use existing residential fee logic",
    },  # do not override ordinary residential
}


JURISDICTION_FEE_MULTIPLIERS: Dict[Tuple[str, str], Dict[str, Any]] = {
    # Provided calibrations from Opus 4.7 grading / known high-overhead AHJ behavior.
    ("los angeles", "ca"): {
        "mult": 1.5,
        "rationale": "LADBS + BOE/LAFD/Planning overlays; high cost-of-government metro [verify current LADBS fee estimator]",
    },
    ("los angeles hillside", "ca"): {
        "mult": 1.8,
        "rationale": "LADBS hillside review + grading + arborist + soils/geology overhead [verify current LADBS + BOE + Planning fees]",
    },
    ("san francisco", "ca"): {
        "mult": 2.0,
        "rationale": "SFDBI valuation/plan review stack plus fire/planning health overlays; among highest-cost permit metros [verify current SFDBI fee tables]",
    },
    ("new york", "ny"): {
        "mult": 1.7,
        "rationale": "NYC DOB filing/inspection ecosystem plus landmarks / multi-agency review potential [verify current NYC DOB fees]",
    },
    ("seattle", "wa"): {
        "mult": 1.3,
        "rationale": "SDCI valuation-based scaling and separate trade/fire review common [verify current SDCI fee subtitle]",
    },
    ("boston", "ma"): {
        "mult": 1.4,
        "rationale": "ISD + landmarks/historical-district review common in core neighborhoods [verify current Boston ISD schedule]",
    },
    ("washington", "dc"): {
        "mult": 1.4,
        "rationale": "DOB/DCRA legacy + zoning + DDOT/public-space coordination [verify current DC DOB fee schedule]",
    },
    ("chicago", "il"): {
        "mult": 1.4,
        "rationale": "Chicago DOB + zoning/DPD + licensing interactions for commercial occupancies [verify current Chicago fee ordinance]",
    },
    ("phoenix", "az"): {
        "mult": 1.0,
        "rationale": "baseline calibration from Phoenix restaurant TI test case [verify current Phoenix Planning & Development fees]",
    },

    # Additional metro calibrations. Values are conservative heuristic buckets, not official multipliers.
    ("austin", "tx"): {
        "mult": 1.2,
        "rationale": "Development Services review plus Austin Energy / fire / health coordination common [verify current Austin DSD fees]",
    },
    ("houston", "tx"): {
        "mult": 1.0,
        "rationale": "large-market baseline with separate trade and occupancy review; no zoning but commercial reviews still stack [verify Houston Permitting Center fees]",
    },
    ("dallas", "tx"): {
        "mult": 1.1,
        "rationale": "commercial plan review / fire review stack in major Texas metro [verify Dallas Development Services fees]",
    },
    ("miami", "fl"): {
        "mult": 1.4,
        "rationale": "building + fire + public works plus Florida product approval / flood / coastal issues common [verify City of Miami fees]",
    },
    ("denver", "co"): {
        "mult": 1.2,
        "rationale": "Community Planning & Development valuation fees plus fire/trade review [verify Denver CPD fees]",
    },
    ("las vegas", "nv"): {
        "mult": 1.2,
        "rationale": "Clark County / Las Vegas commercial valuation and fire review on TI scopes [verify applicable AHJ fee schedule]",
    },
    ("clark county", "nv"): {
        "mult": 1.2,
        "rationale": "Clark County commercial building/trade/fire review; used for unincorporated Las Vegas-area projects [verify current Clark County fees]",
    },
    ("portland", "or"): {
        "mult": 1.3,
        "rationale": "Portland BDS valuation, plan review, systems development / trade review stack [verify current Portland BDS fees]",
    },
    ("atlanta", "ga"): {
        "mult": 1.1,
        "rationale": "commercial building/fire review baseline in major southeast metro [verify Atlanta Office of Buildings fees]",
    },
    ("philadelphia", "pa"): {
        "mult": 1.2,
        "rationale": "L&I commercial permits plus zoning/historic district potential [verify current Philadelphia L&I fees]",
    },
    ("san diego", "ca"): {
        "mult": 1.5,
        "rationale": "California coastal / high-cost city review stack similar to LA without hillside premium [verify current San Diego DSD fees]",
    },
    ("san jose", "ca"): {
        "mult": 1.5,
        "rationale": "Bay Area cost environment and valuation-based development services fees [verify current San Jose fees]",
    },
    ("sacramento", "ca"): {
        "mult": 1.3,
        "rationale": "California capital-region plan check / impact / fire coordination [verify current Sacramento fees]",
    },
    ("minneapolis", "mn"): {
        "mult": 1.2,
        "rationale": "commercial plan review + fire + zoning in major Midwest metro [verify current Minneapolis CPED fees]",
    },
    ("pittsburgh", "pa"): {
        "mult": 1.1,
        "rationale": "PLI commercial review plus zoning/fire coordination [verify current Pittsburgh PLI fees]",
    },
    ("charlotte", "nc"): {
        "mult": 1.1,
        "rationale": "Mecklenburg County / Charlotte commercial plan review baseline [verify current Mecklenburg fee schedule]",
    },
}


TRIGGER_FEE_ADDERS: Dict[str, Dict[str, Any]] = {
    "change_of_occupancy": {
        "add_min": 2000,
        "add_max": 5000,
        "rationale": "IEBC §1001-1011 review + new CO",
    },
    "hood_fire_suppression": {
        "add_min": 3000,
        "add_max": 8000,
        "rationale": "separate fire-prevention permit + acceptance test",
    },
    "grease_interceptor": {
        "add_min": 1000,
        "add_max": 4000,
        "rationale": "sized + reviewed + utility coordination",
    },
    "hillside_grading": {
        "add_min": 5000,
        "add_max": 10000,
        "rationale": "soils/geology + arborist + haul route review",
    },
    "demising_wall": {
        "add_min": 1000,
        "add_max": 3000,
        "rationale": "fire-rated assembly + STC field test",
    },
    "fire_sprinkler_modify": {
        "add_min": 2000,
        "add_max": 6000,
        "rationale": "hydraulic recalc + permit",
    },
    "ada_path_of_travel": {
        "add_min": 1500,
        "add_max": 5000,
        "rationale": "20% rule alterations",
    },
    "multifamily_accessibility": {
        "add_min": 3000,
        "add_max": 8000,
        "rationale": "Type B accessible-unit detailing",
    },
}


_SQFT_RE = re.compile(
    r"(?P<sqft>\d{1,3}(?:,\d{3})+|\d{1,6})\s*(?:sq\.?\s*ft\.?|sqft|s\.f\.|sf)\b",
    re.IGNORECASE,
)

_MONEY_RANGE_RE = re.compile(
    r"\$\s*(?P<low>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<low_k>[kK])?"
    r"\s*(?:-|–|—|to)\s*\$?\s*"
    r"(?P<high>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<high_k>[kK])?",
    re.IGNORECASE,
)

_MONEY_SINGLE_RE = re.compile(
    r"\$\s*(?P<amount>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<k>[kK])?",
    re.IGNORECASE,
)


def parse_sqft_from_job_type(job_type: str) -> Optional[int]:
    """Return the first square-foot quantity found in job_type, or None."""
    if not job_type:
        return None
    match = _SQFT_RE.search(job_type)
    if not match:
        return None
    return int(match.group("sqft").replace(",", ""))


def _money_to_number(raw: str, k_suffix: Optional[str] = None) -> float:
    value = float(str(raw).replace(",", ""))
    if k_suffix:
        value *= 1000
    return value


def extract_llm_fee_high_end(fee_text: Any) -> Optional[float]:
    """
    Extract the highest plausible dollar amount from an LLM fee_range string.

    Handles examples:
        "$8K-25K"                  -> 25000
        "$558"                     -> 558
        "$2,500-12,000"            -> 12000
        "$219 elec + $558 HVAC = $777 total" -> 777
        "$8.5K-14K"                -> 14000

    The function is intentionally permissive because fee_range is free text. It looks
    first for dollar-prefixed ranges, then dollar-prefixed singles, and returns max().
    If the low side has a K suffix and the high side omits it, infer K for compact
    forms like "$8K-25". For comma-style forms like "$2,500-12,000", no inference is
    needed because the high token is already 12000.
    """
    if fee_text is None:
        return None
    text = str(fee_text)
    candidates: List[float] = []

    for match in _MONEY_RANGE_RE.finditer(text):
        low_raw = match.group("low")
        high_raw = match.group("high")
        low_k = match.group("low_k")
        high_k = match.group("high_k")
        inferred_high_k = high_k or (low_k if low_k and "," not in high_raw else None)
        candidates.append(_money_to_number(low_raw, low_k))
        candidates.append(_money_to_number(high_raw, inferred_high_k))

    for match in _MONEY_SINGLE_RE.finditer(text):
        candidates.append(_money_to_number(match.group("amount"), match.group("k")))

    if not candidates:
        return None
    return max(candidates)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _normalize_scope(primary_scope: str, job_type: str) -> str:
    """Map caller scope + job description onto SCOPE_FEE_FLOORS keys."""
    scope = _norm(primary_scope).replace("-", "_").replace(" ", "_")
    text = f"{scope} {_norm(job_type)}"

    if scope in SCOPE_FEE_FLOORS:
        return scope

    if "adu" in text or "accessory dwelling" in text:
        return "residential_adu"
    if "multifamily" in text or "multi_family" in text or "apartment" in text:
        return "multifamily"
    if "restaurant" in text or "food service" in text or "commercial kitchen" in text:
        return "commercial_restaurant"
    if any(token in text for token in ("medical clinic", "medical office", "dental clinic", "health clinic", "clinic tenant", "exam room", "med gas", "medical gas", "x ray", "x-ray", "radiology")):
        return "commercial_medical_clinic_ti"
    if "office" in text and ("ti" in text or "tenant improvement" in text or "buildout" in text):
        return "commercial_office_ti"
    if ("retail" in text or "storefront" in text) and (
        "ti" in text or "tenant improvement" in text or "buildout" in text
    ):
        return "commercial_retail_ti"
    if "commercial" in text or "tenant improvement" in text or "ti" in text:
        return "commercial"
    if "residential" in text or "single family" in text or "sfr" in text:
        return "residential"
    return "commercial" if not scope else scope


def _select_jurisdiction_multiplier(city: str, state: str, job_type: str, triggers: Iterable[str]) -> Tuple[float, str, str]:
    """Return (multiplier, matched_key_label, rationale)."""
    city_norm = _norm(city)
    state_norm = _norm(state)
    trigger_set = set(triggers)
    text = f"{city_norm} {_norm(job_type)}"

    if state_norm == "ca" and city_norm in {"los angeles", "la"} and (
        "hillside_grading" in trigger_set or "hillside" in text
    ):
        data = JURISDICTION_FEE_MULTIPLIERS[("los angeles hillside", "ca")]
        return float(data["mult"]), "Los Angeles hillside, CA", data["rationale"]

    aliases = {
        "la": "los angeles",
        "nyc": "new york",
        "new york city": "new york",
        "washington dc": "washington",
        "district of columbia": "washington",
        "vegas": "las vegas",
    }
    city_key = aliases.get(city_norm, city_norm)
    data = JURISDICTION_FEE_MULTIPLIERS.get((city_key, state_norm))
    if data:
        return float(data["mult"]), f"{city_key.title()}, {state_norm.upper()}", data["rationale"]

    return 1.0, f"{city_norm.title() or 'Unknown'}, {state_norm.upper() or 'Unknown'}", "default 1.0×; jurisdiction multiplier not calibrated [verify]"


def _hidden_trigger_names(hidden_triggers: Any) -> List[str]:
    """Normalize result['hidden_triggers'] into trigger keys."""
    names: List[str] = []
    if not hidden_triggers:
        return names

    if isinstance(hidden_triggers, dict):
        iterable = hidden_triggers.keys()
    elif isinstance(hidden_triggers, (list, tuple, set)):
        iterable = hidden_triggers
    else:
        iterable = [hidden_triggers]

    for item in iterable:
        if isinstance(item, dict):
            # 2026-04-28: Hidden Trigger Detector V1 emits `id` field
            # (e.g. "phoenix_restaurant_hood_fire_suppression"). Earlier
            # callers used `key`/`name`/`trigger`/`type`. Check id last so
            # explicit key fields win.
            raw = item.get("key") or item.get("name") or item.get("trigger") or item.get("type") or item.get("id") or ""
        else:
            raw = str(item)
        key = _norm(raw).replace("-", "_").replace(" ", "_")
        # Direct match (older callers passing exact adder key)
        if key in TRIGGER_FEE_ADDERS:
            names.append(key)
            continue
        # 2026-04-28: Hidden Trigger Detector IDs follow pattern
        # <jurisdiction>_<scope>_<adder_key_suffix>, so check suffix match
        # against each adder key. e.g. "phoenix_restaurant_hood_fire_suppression"
        # matches adder key "hood_fire_suppression".
        for adder_key in TRIGGER_FEE_ADDERS.keys():
            if key.endswith(adder_key) or f"_{adder_key}" in key or adder_key in key:
                names.append(adder_key)
                break
    return names


def detect_fee_triggers_from_text(job_type: str) -> List[str]:
    """Fallback trigger detector for cases where hidden_triggers is unavailable."""
    text = _norm(job_type)
    triggers: List[str] = []

    def add_if(key: str, *needles: str) -> None:
        if any(n in text for n in needles):
            triggers.append(key)

    add_if("change_of_occupancy", "change of occupancy", "change-of-occupancy", "new co", "certificate of occupancy", "occupancy change")
    add_if("hood_fire_suppression", "hood", "type i", "type 1 hood", "ansul", "fire suppression", "kitchen suppression")
    add_if("grease_interceptor", "grease", "interceptor", "fats oils grease", "fog")
    add_if("hillside_grading", "hillside", "grading", "slope", "soils", "geology", "haul route")
    add_if("demising_wall", "demising", "tenant separation", "rated wall", "fire wall", "party wall")
    add_if("fire_sprinkler_modify", "sprinkler", "hydraulic", "fire sprinkler")
    add_if("ada_path_of_travel", "ada", "accessibility", "path of travel", "accessible route")
    add_if("multifamily_accessibility", "type b", "fair housing", "accessible unit", "multifamily accessibility")

    return triggers


def _trigger_names_for_fee(result: Dict[str, Any], job_type: str) -> List[str]:
    names = _hidden_trigger_names(result.get("hidden_triggers"))
    names.extend(detect_fee_triggers_from_text(job_type))
    # stable de-dupe preserving declaration order
    ordered = []
    for key in TRIGGER_FEE_ADDERS.keys():
        if key in names:
            ordered.append(key)
    return ordered


def _round_to_nearest(value: float, nearest: int = 500) -> int:
    if value <= 0:
        return 0
    return int(round(value / nearest) * nearest)


def _format_usd(value: float) -> str:
    return f"${int(round(value)):,}"


def _scope_label(scope_key: str) -> str:
    labels = {
        "commercial_restaurant": "commercial restaurant TI",
        "commercial_office_ti": "commercial office TI",
        "commercial_retail_ti": "commercial retail TI",
        "multifamily": "multifamily",
        "commercial": "commercial",
        "residential_adu": "residential ADU",
        "residential": "residential",
    }
    return labels.get(scope_key, scope_key.replace("_", " "))


def _build_fee_text(
    *,
    low_total: int,
    high_total: int,
    base_floor: int,
    scope_key: str,
    jurisdiction_label: str,
    jurisdiction_mult: float,
    adders: List[Tuple[str, int, int]],
) -> str:
    components = [
        f"~{_format_usd(base_floor)} base permit + plan review ({jurisdiction_label} {_scope_label(scope_key)} floor)",
        f"× {jurisdiction_mult:.1f}× jurisdiction multiplier",
    ]
    for key, add_min, add_max in adders:
        label = key.replace("_", "-")
        midpoint = _round_to_nearest((add_min + add_max) / 2, 500)
        components.append(f"+ {_format_usd(midpoint)} {label} adder")

    return (
        f"Fee Estimate: **{_format_usd(low_total)}-{_format_usd(high_total)}+** "
        f"(structured floor — see breakdown). Components: "
        f"{' '.join(components)}. "
        f"**Verify against current fee schedule before quoting.**"
    )


def apply_fee_realism_guardrail(result: dict, job_type: str, city: str, state: str, primary_scope: str) -> dict:
    """If the LLM-emitted fee is below the computed structured floor, override.

    Logic:
      1. Parse sqft from job_type.
      2. Look up scope floor: max(per_sf * sqft, min_floor).
      3. Apply jurisdiction multiplier.
      4. Apply trigger adders from result['hidden_triggers'] when available, OR re-detect from job_type.
      5. Parse the LLM-emitted fee_range to extract the high-end number.
      6. If LLM number < structured floor, override fee_range with structured floor + breakdown and set _fee_adjusted.
      7. If LLM number >= floor, keep LLM text but set _fee_floor_check = 'llm_above_floor'.

    Returns a shallow-deep copied result dict, so callers can safely assign it back without
    mutating the original object they may have logged earlier.
    """
    guarded = copy.deepcopy(result or {})
    scope_key = _normalize_scope(primary_scope, job_type)
    floor_data = SCOPE_FEE_FLOORS.get(scope_key, SCOPE_FEE_FLOORS["commercial"])

    # Explicit no-op for ordinary residential. ADUs still use the ADU floor.
    if scope_key == "residential":
        guarded["_fee_floor_check"] = "residential_no_override"
        return guarded

    sqft = parse_sqft_from_job_type(job_type)
    per_sf = float(floor_data["per_sf"])
    min_floor = float(floor_data["min_floor"])
    base_floor_raw = max(per_sf * sqft, min_floor) if sqft else min_floor
    base_floor = _round_to_nearest(base_floor_raw, 500)

    trigger_keys = _trigger_names_for_fee(guarded, job_type)
    jurisdiction_mult, jurisdiction_label, jurisdiction_rationale = _select_jurisdiction_multiplier(
        city, state, job_type, trigger_keys
    )

    multiplied_base = _round_to_nearest(base_floor * jurisdiction_mult, 500)

    adders: List[Tuple[str, int, int]] = []
    add_min_total = 0
    add_max_total = 0
    for key in trigger_keys:
        adder = TRIGGER_FEE_ADDERS[key]
        add_min = int(adder["add_min"])
        add_max = int(adder["add_max"])
        adders.append((key, add_min, add_max))
        add_min_total += add_min
        add_max_total += add_max

    structured_low = _round_to_nearest(multiplied_base + add_min_total, 500)
    structured_high = _round_to_nearest(multiplied_base + add_max_total, 500)
    if structured_high < structured_low:
        structured_high = structured_low

    llm_high = extract_llm_fee_high_end(guarded.get("fee_range"))
    guarded["_fee_floor_components"] = {
        "scope": scope_key,
        "scope_note": floor_data["note"],
        "sqft": sqft,
        "base_floor": base_floor,
        "jurisdiction_multiplier": jurisdiction_mult,
        "jurisdiction_label": jurisdiction_label,
        "jurisdiction_rationale": jurisdiction_rationale,
        "trigger_adders": [
            {
                "key": key,
                "add_min": add_min,
                "add_max": add_max,
                "rationale": TRIGGER_FEE_ADDERS[key]["rationale"],
            }
            for key, add_min, add_max in adders
        ],
        "structured_low": structured_low,
        "structured_high": structured_high,
        "llm_fee_high_end": llm_high,
    }

    # If the LLM did not provide a parseable fee, treat that as below-floor for any
    # commercial/ADU/multifamily scope covered here.
    if llm_high is None or llm_high < structured_low:
        guarded["fee_range"] = _build_fee_text(
            low_total=structured_low,
            high_total=structured_high,
            base_floor=base_floor,
            scope_key=scope_key,
            jurisdiction_label=jurisdiction_label,
            jurisdiction_mult=jurisdiction_mult,
            adders=adders,
        )
        guarded["_fee_adjusted"] = True
        guarded["_fee_floor_check"] = "llm_below_floor_adjusted"
    else:
        guarded["_fee_adjusted"] = False
        guarded["_fee_floor_check"] = "llm_above_floor"

    return guarded
