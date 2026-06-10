"""AHJ (Authority Having Jurisdiction) record management.

Stores jurisdiction-level metadata:
  - fee_formula (jurisdiction-specific fee schedules)
  - timezone (IANA timezone for AHJ)
  - contact verification fields
  - gates and notes (jurisdiction-specific warnings/requirements)

Lookup order:
  1. data/ahj_records.json (authoritative, human-verified)
  2. data/verified_cities.db (runtime auto-verified per-trade data)
  3. data/verified_cities.json (legacy import data)
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

_default_data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
DATA_DIR = os.environ.get("CACHE_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or _default_data_dir
AHJ_RECORDS_FILE = os.path.join(DATA_DIR, "ahj_records.json")


# ── In-memory cache ─────────────────────────────────────────────────────────
_ahj_cache: dict[str, dict[str, Any]] | None = None


def _norm_city(city: str) -> str:
    return re.sub(r"\s+", "_", str(city or "").strip().lower())


def _norm_state(state: str) -> str:
    return str(state or "").strip().upper()


def _ahj_key(city: str, state: str) -> str:
    return f"{_norm_city(city)}__{_norm_state(state)}"


def load_ahj_records() -> dict[str, dict[str, Any]]:
    """Load AHJ records from JSON.  Returns dict keyed by normalized city__state."""
    global _ahj_cache
    if _ahj_cache is not None:
        return _ahj_cache
    if os.path.exists(AHJ_RECORDS_FILE):
        try:
            with open(AHJ_RECORDS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                _ahj_cache = raw
                return _ahj_cache
        except Exception:
            pass
    _ahj_cache = {}
    return _ahj_cache


def get_ahj(city: str, state: str) -> dict[str, Any] | None:
    """Return the AHJ record for a city/state, or None."""
    records = load_ahj_records()
    key = _ahj_key(city, state)
    # Try exact key
    rec = records.get(key)
    if rec:
        return rec
    # Try without underscores (legacy compatibility)
    alt_key = f"{_norm_city(city).replace('_', '')}__{_norm_state(state)}"
    rec = records.get(alt_key)
    return rec


def save_ahj_records(records: dict[str, dict[str, Any]]) -> None:
    """Persist AHJ records to disk and bust cache."""
    global _ahj_cache
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(AHJ_RECORDS_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    _ahj_cache = records


def set_ahj(city: str, state: str, data: dict[str, Any]) -> None:
    """Upsert a single AHJ record."""
    records = load_ahj_records()
    key = _ahj_key(city, state)
    records[key] = data
    save_ahj_records(records)


# ── Fee formula computation ─────────────────────────────────────────────────

def compute_fee_from_formula(
    fee_formula: dict[str, Any],
    job_valuation_low: float,
    job_valuation_high: float,
) -> dict[str, Any]:
    """
    Compute permit fee from a tiered valuation formula.

    Returns dict with:
      permit_fee_low, permit_fee_high, plan_review_fee, tech_fee,
      total_low, total_high, breakdown, notes.
    """
    tiers = fee_formula.get("tiers", [])
    plan_review_brackets = fee_formula.get("plan_review_brackets", [])
    min_fee = float(fee_formula.get("min_fee", 0))
    tech_fee = float(fee_formula.get("tech_fee", 0))

    def _permit_fee(val: float) -> float:
        fee = 0.0
        processed = 0.0
        for tier in tiers:
            rate = float(tier.get("rate_per_1000", 0))
            up_to = tier.get("up_to")
            above = tier.get("above")
            if up_to is not None:
                up_to = float(up_to)
                band = min(max(val - processed, 0), up_to - processed)
                fee += band * rate / 1000.0
                processed = up_to
            elif above is not None:
                above = float(above)
                if val > above:
                    fee += (val - max(processed, above)) * rate / 1000.0
                    processed = val
        return max(fee, min_fee)

    def _plan_review_fee(val: float) -> float:
        for bracket in plan_review_brackets:
            bmax = bracket.get("max")
            b_above = bracket.get("above")
            if bmax is not None and val <= float(bmax):
                return float(bracket.get("fee", 0))
            if b_above is not None and val > float(b_above):
                return float(bracket.get("fee", 0))
        return 0.0

    pr_low = _plan_review_fee(job_valuation_low)
    pr_high = _plan_review_fee(job_valuation_high)

    permit_low = _permit_fee(job_valuation_low)
    permit_high = _permit_fee(job_valuation_high)

    total_low = permit_low + pr_low + tech_fee
    total_high = permit_high + pr_high + tech_fee

    return {
        "permit_fee_low": round(permit_low, 2),
        "permit_fee_high": round(permit_high, 2),
        "plan_review_fee_low": pr_low,
        "plan_review_fee_high": pr_high,
        "tech_fee": tech_fee,
        "total_low": round(total_low, 2),
        "total_high": round(total_high, 2),
        "all_inclusive": fee_formula.get("all_inclusive", False),
        "excluded_note": fee_formula.get("excluded_note", ""),
        "source_url": fee_formula.get("source_url", ""),
        "effective_date": fee_formula.get("effective_date", ""),
        "applies_to": fee_formula.get("applies_to", ""),
    }


def format_fee_formula_text(
    fee_result: dict[str, Any],
    city: str,
    state: str,
) -> str:
    """Format computed fee as customer-facing text."""
    def _fmt(n: float) -> str:
        return f"${n:,.0f}"

    parts = [
        f"Fee estimate computed from {city}, {state} published fee schedule",
        f"(effective {fee_result.get('effective_date', 'N/A')}):",
        f"  • Permit fee: {_fmt(fee_result['permit_fee_low'])}–{_fmt(fee_result['permit_fee_high'])}",
        f"  • Plan review: {_fmt(fee_result['plan_review_fee_low'])}–{_fmt(fee_result['plan_review_fee_high'])}",
    ]
    if fee_result.get("tech_fee"):
        parts.append(f"  • Technology fee: {_fmt(fee_result['tech_fee'])}")
    parts.append(f"  • Total: {_fmt(fee_result['total_low'])}–{_fmt(fee_result['total_high'])}")
    if fee_result.get("excluded_note"):
        parts.append(f"  • Note: {fee_result['excluded_note']}")
    parts.append(f"  • Source: {fee_result.get('source_url', '')}")
    parts.append("Verify this estimate with the building department before bidding — valuation bands are approximate.")
    return "\n".join(parts)


# ── Timezone support ────────────────────────────────────────────────────────

def get_ahj_timezone(city: str, state: str) -> str | None:
    """Return IANA timezone for AHJ if known, else None."""
    rec = get_ahj(city, state)
    if rec and rec.get("timezone"):
        return str(rec["timezone"])
    # Fallback: derive from state
    _state_to_tz = {
        "AL": "America/Chicago", "AK": "America/Anchorage", "AZ": "America/Phoenix",
        "AR": "America/Chicago", "CA": "America/Los_Angeles", "CO": "America/Denver",
        "CT": "America/New_York", "DE": "America/New_York", "DC": "America/New_York",
        "FL": "America/New_York", "GA": "America/New_York", "HI": "Pacific/Honolulu",
        "ID": "America/Boise", "IL": "America/Chicago", "IN": "America/Indiana/Indianapolis",
        "IA": "America/Chicago", "KS": "America/Chicago", "KY": "America/New_York",
        "LA": "America/Chicago", "ME": "America/New_York", "MD": "America/New_York",
        "MA": "America/New_York", "MI": "America/Detroit", "MN": "America/Chicago",
        "MS": "America/Chicago", "MO": "America/Chicago", "MT": "America/Denver",
        "NE": "America/Chicago", "NV": "America/Los_Angeles", "NH": "America/New_York",
        "NJ": "America/New_York", "NM": "America/Denver", "NY": "America/New_York",
        "NC": "America/New_York", "ND": "America/Chicago", "OH": "America/New_York",
        "OK": "America/Chicago", "OR": "America/Los_Angeles", "PA": "America/New_York",
        "RI": "America/New_York", "SC": "America/New_York", "SD": "America/Chicago",
        "TN": "America/Chicago", "TX": "America/Chicago", "UT": "America/Denver",
        "VT": "America/New_York", "VA": "America/New_York", "WA": "America/Los_Angeles",
        "WV": "America/New_York", "WI": "America/Chicago", "WY": "America/Denver",
    }
    return _state_to_tz.get(_norm_state(state))


# ── Contact verification helpers ────────────────────────────────────────────

def get_ahj_contact(city: str, state: str) -> dict[str, Any] | None:
    """Return contact block from AHJ record if present."""
    rec = get_ahj(city, state)
    if not rec:
        return None
    contact = rec.get("contact")
    if isinstance(contact, dict) and contact:
        return contact
    return None


def get_ahj_gates(city: str, state: str) -> list[dict[str, Any]]:
    """Return hard-gate list from AHJ record if present."""
    rec = get_ahj(city, state)
    if not rec:
        return []
    gates = rec.get("gates", [])
    if isinstance(gates, list):
        return gates
    return []


def get_ahj_notes(city: str, state: str) -> list[dict[str, Any]]:
    """Return note list from AHJ record if present."""
    rec = get_ahj(city, state)
    if not rec:
        return []
    notes = rec.get("notes", [])
    if isinstance(notes, list):
        return notes
    return []
