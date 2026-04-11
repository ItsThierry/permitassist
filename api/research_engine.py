#!/usr/bin/env python3
"""
PermitAssist — AI Research Engine v3
Improvements over v2:
  - Much more detailed, trade-specific advice (not generic "mechanical permit")
  - Smart small-city fallback: city → county → state level
  - Server-side PDF URL stripping (apply_url vs apply_pdf)
  - Google Maps fallback for office location
  - Better phone number retrieval from web search
  - Job type disambiguation hints
  - portal_selection few-shot examples in system prompt
"""

import os
import json
import time
import re
import sqlite3
import hashlib
import requests
from datetime import datetime, timedelta
from openai import OpenAI

client = OpenAI()

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
CACHE_DB       = os.path.join(os.path.dirname(__file__), "..", "data", "cache.db")
KNOWLEDGE_DIR  = os.path.join(os.path.dirname(__file__), "..", "knowledge")

# ─── Knowledge Base ───────────────────────────────────────────────────────────

_TRADES_KB: dict = {}
_STATES_KB: dict = {}
_CITIES_KB: dict = {}

def _load_knowledge():
    global _TRADES_KB, _STATES_KB, _CITIES_KB
    if _TRADES_KB and _STATES_KB and _CITIES_KB:
        return
    try:
        trades_path = os.path.join(KNOWLEDGE_DIR, "trades.json")
        states_path = os.path.join(KNOWLEDGE_DIR, "states.json")
        cities_path = os.path.join(KNOWLEDGE_DIR, "cities.json")
        if os.path.exists(trades_path):
            with open(trades_path) as f:
                _TRADES_KB = json.load(f)
            print(f"[kb] Loaded trades KB: {len(_TRADES_KB.get('trades', {}))} trades")
        if os.path.exists(states_path):
            with open(states_path) as f:
                _STATES_KB = json.load(f)
            print(f"[kb] Loaded states KB: {len(_STATES_KB.get('states', {}))} states")
        if os.path.exists(cities_path):
            with open(cities_path) as f:
                _CITIES_KB = json.load(f)
            print(f"[kb] Loaded cities KB: {len(_CITIES_KB.get('cities', {}))} cities")
    except Exception as e:
        print(f"[kb] Load error (non-fatal): {e}")

def _get_trade_context(job_type: str) -> str:
    _load_knowledge()
    trades = _TRADES_KB.get("trades", {})
    if not trades:
        return ""

    job_lower = job_type.lower()
    best_match = None
    best_score = 0

    for trade_key, trade_data in trades.items():
        names = trade_data.get("names", [])
        score = 0
        for name in names:
            if name in job_lower:
                score += len(name)
        if score > best_score:
            best_score = score
            best_match = (trade_key, trade_data)

    if not best_match or best_score == 0:
        return ""

    trade_key, trade_data = best_match
    lines = [
        f"=== KNOWLEDGE BASE: {trade_key.upper()} PERMITS ===",
        f"Permit type: {trade_data.get('permit_type', 'varies')}",
        f"Governing code: {trade_data.get('governing_code', 'varies')}",
        f"Who can pull: {trade_data.get('who_can_pull', 'varies by state')}",
    ]

    pr = trade_data.get("permit_required", {})
    if pr:
        lines.append(f"General rule: {pr.get('rule', '')}")
        always = pr.get("always_required", [])
        if always:
            lines.append("Always requires permit: " + "; ".join(always[:4]))
        exempt = pr.get("sometimes_exempt", [])
        if exempt:
            lines.append("Sometimes exempt: " + "; ".join(exempt[:3]))

    fees = trade_data.get("fee_range", {})
    if isinstance(fees, dict):
        lines.append(f"Typical fee range: {fees.get('typical', 'varies')}")
    elif isinstance(fees, str):
        lines.append(f"Typical fee range: {fees}")

    tl = trade_data.get("approval_timeline", {})
    if tl:
        lines.append(f"Timeline: OTC: {tl.get('over_the_counter','varies')} | Plan review: {tl.get('plan_review','varies')}")

    mistakes = trade_data.get("common_mistakes", [])[:3]
    if mistakes:
        lines.append("Common mistakes: " + " | ".join(mistakes))

    tips = trade_data.get("pro_tips", [])[:3]
    if tips:
        lines.append("Pro tips: " + " | ".join(tips))

    return "\n".join(lines)


def _get_state_context(state: str) -> str:
    _load_knowledge()
    states = _STATES_KB.get("states", {})
    state_data = states.get(state.upper(), {})
    if not state_data:
        return ""

    lines = [
        f"=== STATE CONTEXT: {state_data.get('name', state).upper()} ===",
        f"NEC edition: {state_data.get('nec_edition', 'varies')}",
        f"IRC edition: {state_data.get('irc_edition', 'varies')}",
        f"Statewide code: {state_data.get('statewide_code', 'varies')}",
        f"Permit authority: {state_data.get('permit_authority', 'varies')}",
    ]
    if state_data.get("mechanical_code"):
        lines.append(f"Mechanical code: {state_data['mechanical_code']}")
    if state_data.get("licensing_board_url"):
        lines.append(f"Licensing board: {state_data['licensing_board_url']}")

    quirks = state_data.get("key_quirks", [])[:5]
    if quirks:
        lines.append("Key facts: " + " | ".join(quirks))

    return "\n".join(lines)


def _get_city_context(city: str, state: str) -> tuple[str, str]:
    """
    Return (city_context, match_level) where match_level is 'city', 'county', or 'state'.
    Never returns empty — always falls back to county then state.
    """
    _load_knowledge()
    cities = _CITIES_KB.get("cities", {})

    city_lower = city.lower().strip()
    state_upper = state.upper().strip()

    # Exact match
    for key, data in cities.items():
        if data.get("city", "").lower() == city_lower and data.get("state", "").upper() == state_upper:
            return _format_city_context(data), "city"

    # Partial match
    for key, data in cities.items():
        if city_lower in key.lower() and data.get("state", "").upper() == state_upper:
            return _format_city_context(data), "city"

    # County fallback
    county_ctx = _get_county_context(city, state)
    if county_ctx:
        return county_ctx, "county"

    # State fallback — build a useful fallback from state KB
    state_ctx = _get_state_context(state)
    if state_ctx:
        fallback = (
            f"=== FALLBACK: {city.upper()}, {state_upper} NOT IN CITY DATABASE ===\n"
            f"INSTRUCTION: {city} is not in our city-specific database. You MUST:\n"
            f"1. Use web search results to find the actual building permit office for {city}, {state}\n"
            f"2. Return the real phone number and address from web search\n"
            f"3. If web search finds nothing specific, suggest searching: "
            f"https://www.google.com/maps/search/{city.replace(' ','+')}+{state}+building+permit+office\n"
            f"4. As fallback, reference the county or state-level office\n"
            f"The state context below gives you licensing and code requirements:\n\n"
            + state_ctx
        )
        return fallback, "state"

    return "", "none"


def _get_county_context(city: str, state: str) -> str:
    _load_knowledge()
    counties = _CITIES_KB.get("counties", {})
    if not counties:
        return ""

    state_upper = state.upper().strip()
    city_lower = city.lower().strip()

    # Known unincorporated → county mappings
    county_hints = {
        # TX Harris County (no residential permit required for HVAC/electrical/plumbing)
        "katy": "harris_county_tx", "spring": "harris_county_tx",
        "humble": "harris_county_tx", "cypress": "harris_county_tx",
        "pearland": "harris_county_tx", "sugar land": "harris_county_tx",
        "friendswood": "harris_county_tx",
        # AZ Maricopa County
        "cave creek": "maricopa_county_az", "paradise valley": "maricopa_county_az",
        "fountain hills": "maricopa_county_az", "queen creek": "maricopa_county_az",
        # IL Cook County
        "unincorporated cook": "cook_county_il", "alsip": "cook_county_il",
        "oak lawn": "cook_county_il", "calumet city": "cook_county_il",
        "dolton": "cook_county_il", "harvey": "cook_county_il",
        # WA King County
        "unincorporated king": "king_county_wa", "shoreline": "king_county_wa",
        "burien": "king_county_wa", "kenmore": "king_county_wa",
        # FL Broward County
        "unincorporated broward": "broward_county_fl", "deerfield beach": "broward_county_fl",
        "coconut creek": "broward_county_fl", "tamarac": "broward_county_fl",
    }

    county_key = county_hints.get(city_lower)
    if not county_key:
        # Try state-based county lookup for common patterns
        if state_upper == "IL" and "chicago" not in city_lower:
            county_key = "cook_county_il"  # most IL suburbs are Cook County
        elif state_upper == "TX" and any(x in city_lower for x in ["suburb", "unincorporated"]):
            county_key = "harris_county_tx"

    if county_key and county_key in counties:
        data = counties[county_key]
        lines = [
            f"=== COUNTY PERMIT INFO: {data.get('county','').upper()}, {data.get('state','').upper()} ===",
            f"Note: {data.get('note', '')}",
            f"Permit office: {data.get('permit_office', 'County Building Dept')}",
        ]
        if data.get("phone"):
            lines.append(f"Phone: {data['phone']}")
        if data.get("online_portal"):
            lines.append(f"Online portal: {data['online_portal']}")
        fees = data.get("fees", {})
        if fees:
            for k, v in fees.items():
                if k not in ("fee_note", "note"):
                    lines.append(f"  {k.replace('_',' ').title()}: {v}")
            if fees.get("fee_note"):
                lines.append(f"  Note: {fees['fee_note']}")
        notes = data.get("key_notes", [])[:4]
        if notes:
            lines.append("Key notes: " + " | ".join(notes))
        return "\n".join(lines)

    return ""


def _format_city_context(data: dict) -> str:
    lines = [
        f"=== CITY KNOWLEDGE BASE: {data.get('city','').upper()}, {data.get('state','').upper()} ===",
        f"Permit office: {data.get('permit_office', 'varies')}",
        f"Website: {data.get('permit_url', '')}",
    ]
    if data.get("online_portal"):
        lines.append(f"ONLINE PERMIT PORTAL (use this as apply_url): {data['online_portal']}")
    if data.get("phone"):    lines.append(f"Phone: {data['phone']}")
    if data.get("address"): lines.append(f"Address: {data['address']}")

    fees = data.get("fees", {})
    if fees:
        lines.append("Verified fees:")
        for k, v in fees.items():
            if k != "fee_note":
                lines.append(f"  {k.replace('_',' ').title()}: {v}")
        if fees.get("fee_note"):
            lines.append(f"  Note: {fees['fee_note']}")

    tl = data.get("timeline", {})
    if tl:
        for k, v in tl.items():
            lines.append(f"Timeline ({k}): {v}")

    notes = data.get("key_notes", [])[:5]
    if notes:
        lines.append("Key notes: " + " | ".join(notes))

    return "\n".join(lines)


def _get_trade_state_notes(job_type: str, state: str) -> str:
    _load_knowledge()
    trades = _TRADES_KB.get("trades", {})
    job_lower = job_type.lower()

    for trade_key, trade_data in trades.items():
        names = trade_data.get("names", [])
        if any(n in job_lower for n in names):
            state_notes = trade_data.get("state_notes", {})
            note = state_notes.get(state.upper(), "")
            if note:
                return f"=== {state.upper()} SPECIFIC ({trade_key.upper()}) ===\n{note}"
    return ""


def _detect_job_type_hints(job_type: str) -> str:
    """Return disambiguation hints for ambiguous job types."""
    job_lower = job_type.lower()
    hints = []

    # Repair vs replacement
    if any(w in job_lower for w in ["repair", "fix", "leak", "patch", "patch"]):
        if "roof" in job_lower or "roofing" in job_lower:
            hints.append(
                "JOB DISAMBIGUATION: 'repair' vs 'replacement' matters for roofing. "
                "Minor repairs (<25% of roof area, no structural) are often EXEMPT from permits. "
                "Full or partial replacement (>25% area) almost always requires a permit. "
                "Address BOTH scenarios in your response if unclear."
            )
        elif any(w in job_lower for w in ["hvac", "ac", "furnace", "heat"]):
            hints.append(
                "JOB DISAMBIGUATION: Minor HVAC repairs (replacing a capacitor, fan motor, belt) "
                "do NOT require a permit in most jurisdictions. "
                "Full system replacement or new refrigerant work DOES require a permit. "
                "Clarify which applies."
            )
        elif "plumbing" in job_lower or "pipe" in job_lower:
            hints.append(
                "JOB DISAMBIGUATION: Minor plumbing repairs (replacing faucets, fixing leaks at existing connections) "
                "often don't require a permit. New runs, water heater replacement, or re-piping DOES. "
                "Address both if unclear."
            )

    # Mini split — dual permit
    if "mini split" in job_lower or "mini-split" in job_lower or "ductless" in job_lower:
        hints.append(
            "IMPORTANT: Mini split installations require TWO permits in most jurisdictions: "
            "1) Mechanical/HVAC permit for the refrigerant system "
            "2) Electrical permit for the dedicated circuit "
            "List BOTH in permits_required."
        )

    # EV charger
    if "ev charger" in job_lower or "ev charging" in job_lower or "electric vehicle" in job_lower:
        hints.append(
            "EV CHARGER NOTE: Level 2 EVSE (240V) always requires an electrical permit. "
            "If the panel needs upgrade or new sub-panel, that's a separate permit. "
            "Level 1 (120V) plugging into existing outlet: NO permit typically required. "
            "Assume Level 2 unless stated otherwise."
        )

    # Solar
    if "solar" in job_lower or "pv" in job_lower or "photovoltaic" in job_lower:
        hints.append(
            "SOLAR NOTE: Solar installations require TWO permits: "
            "1) Building permit (structural — roof penetrations, racking) "
            "2) Electrical permit (NEC Article 690, utility interconnection) "
            "Many cities have SolarAPP+ for instant approval. "
            "Utility interconnection application is SEPARATE from permits."
        )

    # Generator
    if "generator" in job_lower or "standby" in job_lower:
        hints.append(
            "GENERATOR NOTE: Standby generators typically require THREE permits: "
            "1) Electrical permit (NEC 445/702, transfer switch) "
            "2) Gas/mechanical permit (if gas-powered) "
            "3) Building permit (if on a pad or adding structure) "
            "Plus: gas utility inspection, and sometimes HOA approval. "
            "Setback requirements (5-20ft from openings) are commonly violated."
        )

    return "\n".join(hints) if hints else ""


# ─── Cache ────────────────────────────────────────────────────────────────────

def init_cache():
    os.makedirs(os.path.dirname(CACHE_DB), exist_ok=True)
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS permit_cache (
            cache_key   TEXT PRIMARY KEY,
            job_type    TEXT,
            city        TEXT,
            state       TEXT,
            zip_code    TEXT,
            result_json TEXT,
            created_at  TEXT,
            hits        INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_captures (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT NOT NULL,
            source      TEXT DEFAULT 'gate',
            captured_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def cache_key(job_type: str, city: str, state: str) -> str:
    raw = f"{job_type.lower().strip()}|{city.lower().strip()}|{state.upper().strip()}"
    return hashlib.md5(raw.encode()).hexdigest()

def get_cached(key: str, max_age_days: int = 30):
    try:
        conn = sqlite3.connect(CACHE_DB)
        row = conn.execute(
            "SELECT result_json, created_at FROM permit_cache WHERE cache_key = ?", [key]
        ).fetchone()
        if row:
            created = datetime.fromisoformat(row[1])
            if datetime.now() - created < timedelta(days=max_age_days):
                conn.execute("UPDATE permit_cache SET hits = hits + 1 WHERE cache_key = ?", [key])
                conn.commit()
                conn.close()
                return json.loads(row[0])
        conn.close()
    except Exception as e:
        print(f"[cache] Read error (non-fatal): {e}")
    return None

def save_cache(key: str, job_type: str, city: str, state: str, zip_code: str, result: dict):
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute("""
            INSERT OR REPLACE INTO permit_cache
            (cache_key, job_type, city, state, zip_code, result_json, created_at, hits)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """, [key, job_type, city, state, zip_code, json.dumps(result), datetime.now().isoformat()])
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[cache] Write error (non-fatal): {e}")

# ─── Tavily Web Search ────────────────────────────────────────────────────────

def tavily_search(query: str, max_results: int = 4) -> list[dict]:
    if not TAVILY_API_KEY:
        return []
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "basic",
                "max_results": max_results,
                "include_domains": [],
                "exclude_domains": [],
            },
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for r in data.get("results", []):
            results.append({
                "title":   r.get("title", ""),
                "url":     r.get("url", ""),
                "content": r.get("content", "")[:700],
            })
        return results
    except Exception as e:
        print(f"[tavily] Search failed (non-fatal): {e}")
        return []

def build_search_context(job_type: str, city: str, state: str, zip_code: str = "",
                          city_match_level: str = "city") -> str:
    """
    Run targeted Tavily searches. Adjusts search based on whether we have a city KB hit.
    For small cities, explicitly searches for phone number and office address.
    """
    location = f"{city}, {state}"
    if zip_code:
        location += f" {zip_code}"

    if city_match_level == "city":
        # City in KB — search for current fees + portal-specific info
        q1 = f"{city} {state} building permit {job_type} requirements fee 2025 2026"
        q2 = f'"{city}" "{state}" permit portal apply online {job_type}'
        results1 = tavily_search(q1, max_results=3)
        results2 = tavily_search(q2, max_results=2)
        all_results = results1 + results2
    else:
        # City NOT in KB — need to find the actual permit office
        q1 = f"{city} {state} building permit office phone number address {job_type}"
        q2 = f'"{city}" "{state}" building department permit how to apply online portal 2025'
        q3 = f"{city} {state} permit fee schedule {job_type} site:.gov"
        results1 = tavily_search(q1, max_results=3)
        results2 = tavily_search(q2, max_results=2)
        results3 = tavily_search(q3, max_results=2)
        all_results = results1 + results2 + results3

    if not all_results:
        return ""

    lines = ["=== REAL-TIME WEB SEARCH RESULTS (use these to verify/improve your answer) ==="]
    for r in all_results:
        lines.append(f"\nSource: {r['url']}")
        lines.append(f"Title: {r['title']}")
        lines.append(f"Excerpt: {r['content']}")
        lines.append("---")

    return "\n".join(lines)

# ─── URL Utilities ────────────────────────────────────────────────────────────

def is_pdf_url(url: str) -> bool:
    """Detect if a URL is a PDF file."""
    if not url:
        return False
    url_lower = url.lower()
    return (
        url_lower.endswith(".pdf") or
        ".pdf?" in url_lower or
        "/pdf/" in url_lower or
        "type=pdf" in url_lower or
        "format=pdf" in url_lower
    )

def strip_pdf_from_result(result: dict) -> dict:
    """
    Move PDF URLs from apply_url to apply_pdf.
    Apply_url should only ever be a real web portal, never a PDF.
    """
    apply_url = result.get("apply_url", "")
    if apply_url and is_pdf_url(apply_url):
        print(f"[pdf_strip] Moved PDF to apply_pdf: {apply_url}")
        result["apply_pdf"] = apply_url
        result["apply_url"] = None

    # Also check if apply_pdf is already set and apply_url is None
    if not result.get("apply_pdf") and result.get("apply_url") == result.get("apply_pdf"):
        result["apply_pdf"] = None

    return result


def build_google_maps_url(city: str, state: str) -> str:
    """Build a Google Maps search URL for the building permit office."""
    query = f"{city}+{state}+building+permit+office".replace(" ", "+")
    return f"https://www.google.com/maps/search/{query}"

# ─── System Prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are PermitAssist, an expert AI that helps contractors understand permit requirements for residential and commercial trade work.

You have deep, SPECIFIC expertise in building codes and permits across all 50 US states. You talk like an experienced contractor who's pulled hundreds of permits — direct, practical, no fluff.

CORE MISSION — BE MORE USEFUL THAN GOOGLE OR CHATGPT:
Google gives links. ChatGPT gives generic answers. PermitAssist gives contractor-ready specifics:
- The EXACT permit type name used in that city's portal dropdown
- The REAL phone number for the building department
- The ACTUAL fee in dollars, not "varies"
- The SPECIFIC things an inspector will look for (not "rough-in inspection")
- What to bring to the permit counter, item by item
- The local gotchas nobody else mentions (license number format, required plan set size, etc.)
If your answer could apply to any city in America, it's not specific enough. Make it specific to THAT city.

LICENSE REQUIRED FIELD — CRITICAL WORDING RULES:
This field answers: "who pulls the permit?" NEVER imply that having a licensed contractor means no permit is needed.
- WRONG: "Licensed plumber required"
- WRONG: "Must use licensed contractor"
- RIGHT: "Licensed plumber pulls the permit — contractor's license number goes on the application"
- RIGHT: "Owner-builder allowed in TX — you can pull your own permit but must pass inspection"
- RIGHT: "TACL license (TX HVAC contractor) — license number required on mechanical permit application"
If a licensed contractor pulls the permit FOR the homeowner, say: "[Trade] contractor pulls the permit on your behalf — their license # appears on the application. The permit is still required."

EXPERTISE BY TRADE:
• HVAC: IMC/UMC Chapters 3-9, EPA 608 refrigerant certification, Manual J load calcs, SEER2 minimums (2023), contractor licensing (TACL in TX, C-20 in CA, etc.)
• Electrical: NEC 2020/2023, Article 210 (branch circuits), Article 230 (services), Article 250 (grounding), Article 625 (EV), Article 690 (solar), Article 702 (generators)
• Roofing: IRC Chapter 9, wind zones (ASCE 7-22), fire ratings, ICC-600 for high-wind, ice & water shield requirements by climate zone, 25% rule for re-roofing vs tear-off
• Plumbing: IPC/UPC, ASSE 1016 (anti-scald), T&P valve relief piping, seismic strapping (CA/OR/WA), water heater venting (Type B, direct vent, power vent)
• Mini splits: dual permits (mechanical + electrical), refrigerant recovery, line set routing, condensate drainage
• Solar: NEC Article 690, DC disconnects, rapid shutdown, utility interconnection (separate from building permit), SolarAPP+
• Generators: NEC 445/702, transfer switch types (ATS vs manual interlock), setbacks from openings (5-20ft), gas permit, pad/footing requirements
• Decks: IRC R507 (2018+), ledger attachment (lag bolt pattern), footing depth (below frost line), guardrail height (36" residential, 42" commercial), deck board span tables

PRIORITY ORDER for answers:
1. Web search results in context (most current, city-specific)
2. Knowledge base context in this message (verified trade/state/city rules)
3. Your training knowledge (fill gaps only)

CRITICAL RULES:
1. NEVER say "Mechanical Permit" alone — always specify the exact sub-type:
   - "Mechanical Permit — HVAC Replacement (Residential)" or
   - "Mechanical Permit — Gas Furnace Installation" or  
   - "Mechanical Permit — Mini Split System (Ductless)"
   This is what the contractor actually selects in the permit portal.

2. PORTAL_SELECTION must be the exact dropdown/checkbox string from the permit portal. Examples:
   - For HVAC replacement: "HVAC Replacement - Residential" or "Mechanical - AC/Furnace Replacement"
   - For water heater: "Plumbing - Water Heater Replacement (Gas)" or "Gas Water Heater - Residential"
   - For panel upgrade: "Electrical - Panel Upgrade 200A" or "Service Upgrade - 200 Amp"
   - For roofing: "Roofing - Re-Roof (Shingles)" or "Roof Replacement - Residential"
   - For EV charger: "Electrical - EV Charger (Level 2, 240V)" or "EV Charging Station - Residential"
   - For solar: "Electrical - Solar PV System" AND "Building - Solar Panel Installation"
   - For deck: "Building - Deck Addition" or "Residential Deck/Patio Cover"
   Use the most specific version you can based on city/state context.

3. ALWAYS include the phone number in apply_phone. If not in KB:
   - Search web results for the actual number
   - If not found, provide the format: "Search Google Maps: [city] [state] building permit office"
   Never return null for apply_phone — always return something actionable.

4. APPLY_URL must be a real WEB PORTAL URL (not a PDF). Examples of valid apply_url:
   - "https://aca-prod.accela.com/PHOENIX" ✅
   - "https://abc.austintexas.gov" ✅
   - "https://houston.gov/onlinepermitting" ✅
   - "https://city.gov/docs/permit-application.pdf" ❌ (this is a PDF — put in apply_pdf instead)
   If only a PDF application exists, set apply_url = null and apply_pdf = [PDF URL].

5. SMALL CITY HANDLING: If the city is not in your database, you MUST:
   a. Use web search results to find the actual building department
   b. Return whatever phone/address you found from web search
   c. Note in the result that it's from web search and should be verified
   d. If no web results, return the county or state-level fallback office
   e. NEVER return null for apply_phone — return Google Maps search link if nothing else

6. ADVICE DEPTH — for each inspection, be specific:
   - Not: "Rough-in inspection"
   - But: "Rough-in mechanical inspection — inspector checks: refrigerant line routing, electrical disconnect within sight of equipment, proper clearances (18\" above floor for gas furnace in garage), condensate line slope (1/8\" per foot minimum)"

7. COMMON_MISTAKES must be trade-specific and action-preventing:
   - Not: "Don't forget to get a permit"
   - But: "Starting HVAC work before permit is posted at job site — inspector can stop work and require permit fee double-up"

8. PRO_TIPS must be practical time/money savers:
   - Not: "Research requirements"
   - But: "In TX, TACL license number goes on the mechanical permit application — have your license card in hand before opening the portal"

9. CONFIDENCE levels:
   - "high": city-specific data from KB + web search confirms it
   - "medium": state-level data, city not in KB but web search found something
   - "low": general knowledge only, city not found anywhere — tell contractor to call

10. FEE PRECISION: Always give specific numbers when known. Instead of "$75-$250" say:
    "$68 for first HVAC system, $19 for each additional (Austin 2025)" or
    "$558 combined mechanical/electrical/building permit (Phoenix — single permit system)"

Return ONLY a JSON object with these exact fields:
{
  "job_summary": "clear description of what the job involves and what permits it triggers",
  "location": "city, state",
  "data_source": "city_database" | "web_search" | "state_rules" | "general_knowledge",
  "permits_required": [
    {
      "permit_type": "Mechanical Permit — HVAC Replacement (Residential)",
      "portal_selection": "HVAC Replacement - Residential System",
      "required": true,
      "notes": "Required for any HVAC system replacement involving refrigerant or new ductwork"
    }
  ],
  "applying_office": "exact department name, e.g. Houston Permitting Center or Cook County Building & Zoning Dept",
  "apply_url": "https://real-portal-url.gov (or null if PDF-only or unknown — never a PDF link)",
  "apply_pdf": "URL to paper application PDF form, or null if online portal exists",
  "apply_phone": "(555) 555-5555 — always return something, even Google Maps search link",
  "apply_address": "123 Main St, City, ST 00000",
  "apply_google_maps": "https://www.google.com/maps/search/City+State+building+permit+office",
  "fee_range": "specific dollar amounts when known, e.g. '$68 first system, $19 each additional (Austin 2025)'",
  "approval_timeline": {
    "simple": "e.g. Same day OTC for residential trade work",
    "complex": "e.g. 5-10 business days if plan review required"
  },
  "inspections": [
    {
      "stage": "Rough-In Mechanical",
      "description": "Inspector verifies: refrigerant line routing and support, electrical disconnect in sight of equipment, proper clearances, condensate drain slope (1/8\" per foot min)",
      "timing": "Before insulating lines or covering wall penetrations"
    }
  ],
  "license_required": "Licensed HVAC contractor (TACL in TX) pulls the permit — their license # must appear on the mechanical permit application. Owner-builders cannot pull HVAC permits in TX.",
  "what_to_bring": [
    "Completed permit application (available online or at counter)",
    "TACL license number and expiration date",
    "Equipment spec sheets: make/model/BTU/SEER2 rating for new system",
    "Property address and legal description",
    "Homeowner authorization letter (if contractor is submitting on behalf)"
  ],
  "common_mistakes": [
    "Starting work before permit is posted at job site — can trigger stop-work order and double permit fee",
    "Using wrong permit type — HVAC replacement needs Mechanical permit, not Building permit",
    "Not pulling separate electrical permit for the new disconnect/circuit"
  ],
  "pro_tips": [
    "In TX, you can submit the mechanical permit online via city portal and get approval same day for residential replacements",
    "Take photos of existing equipment nameplate before demo — inspector may ask for it",
    "Schedule rough-in inspection before the 48-hour window closes or pay re-inspection fee"
  ],
  "code_citation": {
    "section": "IRC R105.2.2" ,
    "text": "Ordinary repairs to structures shall not include the cutting away of any wall, partition or portion thereof..." 
  },
  "what_to_bring": [
    "Item 1 contractor needs at the permit counter or to submit online",
    "Item 2 — be specific: 'Equipment spec sheet with make/model/BTU/SEER2'",
    "Item 3 — e.g. 'TACL license number (not just the card — the actual number)'",
    "Item 4 — e.g. 'Site plan showing equipment location and clearances'",
    "Item 5 — e.g. 'Homeowner signature on application if contractor is pulling'"
  ],
  "sources": ["official source URLs cited in your answer"],
  "confidence": "high|medium|low",
  "disclaimer": "Always verify current requirements directly with your local building department before starting work. Permit fees and requirements change frequently."
}

DEPTH CHECKLIST — before returning your answer, verify:
✓ permit_type is specific, not generic (not just 'Mechanical Permit')
✓ portal_selection is the exact string the contractor picks in a dropdown
✓ apply_phone is a real phone number, not null
✓ fee_range has specific dollar amounts, not 'varies'
✓ each inspection.description names the actual things the inspector checks
✓ what_to_bring has 4-6 specific items for THIS job type and location
✓ common_mistakes are job-specific, not generic reminders
✓ license_required explains WHO pulls the permit and HOW, never implies 'no permit needed'
✓ pro_tips save real time or money — not generic advice
✓ code_citation: for NO verdicts, ALWAYS include the specific code section (IRC/IPC/NEC/state code) that creates the exemption. Format: {"section": "IRC R105.2.2", "text": "first 120 chars of the relevant exemption text"}. For YES/MAYBE verdicts, set code_citation to null."""

# ─── Main Research Function ───────────────────────────────────────────────────

def research_permit(job_type: str, city: str, state: str, zip_code: str = "", use_cache: bool = True) -> dict:
    """
    Research permit requirements for a job + location.
    v3: Better advice depth, small city fallback, PDF stripping, Google Maps fallback.
    """
    init_cache()

    key = cache_key(job_type, city, state)

    if use_cache:
        cached = get_cached(key)
        if cached:
            cached["_cached"] = True
            return cached

    location_str = f"{city}, {state}"
    if zip_code:
        location_str += f" {zip_code}"

    # ── Step 1: Knowledge base context ──
    trade_context      = _get_trade_context(job_type)
    trade_state_notes  = _get_trade_state_notes(job_type, state)
    city_context, city_match_level = _get_city_context(city, state)
    job_hints          = _detect_job_type_hints(job_type)

    print(f"[research] City match level: {city_match_level} for {city}, {state}")
    if trade_context:  print(f"[research] KB trade match found")
    if city_context:   print(f"[research] KB context: {city_match_level}")

    # ── Step 2: Live web search (adaptive based on city match) ──
    print(f"[research] Searching web for: {job_type} in {location_str}")
    search_context = build_search_context(job_type, city, state, zip_code, city_match_level)
    if search_context:
        print(f"[research] Got {search_context.count('Source:')} web sources")
    else:
        print("[research] No web results — using KB + GPT training data")

    # ── Step 3: Build combined context ──
    kb_context_parts = []
    if city_context:       kb_context_parts.append(city_context)
    if trade_state_notes:  kb_context_parts.append(trade_state_notes)
    if trade_context:      kb_context_parts.append(trade_context)
    # State context already included in fallback if needed
    if city_match_level == "city":
        state_ctx = _get_state_context(state)
        if state_ctx: kb_context_parts.append(state_ctx)

    kb_context = "\n\n".join(kb_context_parts)

    # ── Step 4: GPT synthesis ──
    user_prompt = f"""A contractor needs permit information for this job:

Job: {job_type}
Location: {location_str}
City data availability: {city_match_level}

{f"IMPORTANT HINTS FOR THIS JOB TYPE:{chr(10)}{job_hints}" if job_hints else ""}

{kb_context}

{search_context}

Using all context above, research the specific permit requirements for this exact job in {city}, {state}.

{"IMPORTANT: This city is NOT in our database. Use web search results above to find the actual permit office, phone number, and address. If web search found nothing, return the county or state building department as fallback." if city_match_level in ("state", "none") else ""}

Priority:
1. Web search results (most current, city-specific)
2. Knowledge base context
3. Training knowledge (fill gaps)

Be as specific as possible for {city}, {state}.
Give EXACT permit type names, EXACT portal selection strings, SPECIFIC fee amounts, SPECIFIC inspection requirements.
Include the apply_google_maps URL even if you have other contact info.

Return ONLY the JSON object."""

    start = time.time()

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system",  "content": SYSTEM_PROMPT},
            {"role": "user",    "content": user_prompt},
        ],
        temperature=0.1,
        max_tokens=2500,
        response_format={"type": "json_object"},
    )

    elapsed = round((time.time() - start) * 1000)
    raw = response.choices[0].message.content
    result = json.loads(raw)

    # ── Post-processing ──

    # Strip PDF URLs from apply_url
    result = strip_pdf_from_result(result)

    # Ensure apply_google_maps is always set
    if not result.get("apply_google_maps"):
        result["apply_google_maps"] = build_google_maps_url(city, state)

    # Ensure apply_phone is never null
    if not result.get("apply_phone"):
        result["apply_phone"] = f"Search: {build_google_maps_url(city, state)}"

    # Derive top-level permit_verdict from permits_required array
    # Frontend verdictState() reads this field
    if not result.get("permit_verdict"):
        permits = result.get("permits_required", [])
        if not permits:
            # Empty array = GPT said no permit needed
            result["permit_verdict"] = "NO"
        else:
            first_req = permits[0].get("required")
            if first_req is True:
                result["permit_verdict"] = "YES"
            elif first_req is False:
                result["permit_verdict"] = "NO"
            elif first_req == "maybe":
                result["permit_verdict"] = "MAYBE"
            else:
                # required field missing or unknown value — check fee/summary for hints
                fee = str(result.get("fee_range", "")).lower()
                summary = str(result.get("job_summary", "") + result.get("permit_summary", "")).lower()
                if "no permit" in fee or "no permit" in summary or "not required" in summary:
                    result["permit_verdict"] = "NO"
                else:
                    result["permit_verdict"] = "MAYBE"

    # Add data_source if not set
    if not result.get("data_source"):
        result["data_source"] = city_match_level if city_match_level != "none" else "general_knowledge"

    # Ensure code_citation is well-formed or null
    cc = result.get("code_citation")
    if cc and isinstance(cc, dict) and not cc.get("section"):
        result["code_citation"] = None
    elif cc and isinstance(cc, str) and len(cc) > 3:
        # GPT returned a string instead of object — wrap it
        result["code_citation"] = {"section": cc, "text": ""}
    elif not cc:
        result["code_citation"] = None

    # Add metadata
    result["_meta"] = {
        "generated_at":    datetime.now().isoformat(),
        "response_ms":     elapsed,
        "cached":          False,
        "model":           "gpt-4o",
        "web_sources":     search_context.count("Source:") if search_context else 0,
        "city_match_level": city_match_level,
        "job_type":        job_type,
        "city":            city,
        "state":           state,
        "zip_code":        zip_code,
    }

    save_cache(key, job_type, city, state, zip_code, result)
    return result


# ─── Display Helper ───────────────────────────────────────────────────────────

def format_for_display(result: dict) -> str:
    lines = []
    loc    = result.get("location", "")
    job    = result.get("_meta", {}).get("job_type", result.get("job_summary", ""))
    cached = result.get("_cached") or result.get("_meta", {}).get("cached", False)
    conf   = result.get("confidence", "?").upper()
    sources = result.get("_meta", {}).get("web_sources", 0)
    match   = result.get("_meta", {}).get("city_match_level", "?")

    lines.append("="*60)
    lines.append(f"📋 PERMIT RESEARCH: {job.upper()}")
    lines.append(f"📍 Location: {loc} [data: {match}]")
    lines.append(f"🎯 Confidence: {conf}  {'⚡ CACHED' if cached else f'🌐 {sources} web source(s)'}")
    lines.append("="*60)

    permits = result.get("permits_required", [])
    if permits:
        lines.append("\n🔖 PERMITS REQUIRED:")
        for p in permits:
            req  = p.get("required", "?")
            icon = "✅" if req is True else ("⚠️" if req == "maybe" else "❌")
            lines.append(f"  {icon} {p.get('permit_type', 'Unknown')}")
            if p.get("portal_selection"):
                lines.append(f"     Select in portal: '{p['portal_selection']}'")
            if p.get("notes"):
                lines.append(f"     → {p['notes']}")

    office = result.get("applying_office", "")
    url    = result.get("apply_url", "")
    phone  = result.get("apply_phone", "")
    addr   = result.get("apply_address", "")
    maps   = result.get("apply_google_maps", "")
    if office: lines.append(f"\n🏢 APPLY TO: {office}")
    if url:    lines.append(f"   🌐 Online: {url}")
    if phone:  lines.append(f"   📞 Phone: {phone}")
    if addr:   lines.append(f"   📬 Address: {addr}")
    if maps and not url: lines.append(f"   🗺️  Maps: {maps}")

    fee = result.get("fee_range", "")
    if fee: lines.append(f"\n💰 FEES: {fee}")

    tl = result.get("approval_timeline", {})
    if tl:
        lines.append("\n⏱️  TIMELINE:")
        if tl.get("simple"):  lines.append(f"   Simple: {tl['simple']}")
        if tl.get("complex"): lines.append(f"   Complex: {tl['complex']}")

    lic = result.get("license_required", "")
    if lic: lines.append(f"\n📜 LICENSE: {lic}")

    inspections = result.get("inspections", [])
    if inspections:
        lines.append(f"\n🔍 INSPECTIONS ({len(inspections)} required):")
        for i, insp in enumerate(inspections, 1):
            lines.append(f"   {i}. {insp.get('stage','')} — {insp.get('description','')}")
            if insp.get("timing"):
                lines.append(f"      When: {insp['timing']}")

    tips = result.get("pro_tips", [])
    if tips:
        lines.append("\n💡 PRO TIPS:")
        for tip in tips[:3]: lines.append(f"   • {tip}")

    mistakes = result.get("common_mistakes", [])
    if mistakes:
        lines.append("\n⚠️  AVOID:")
        for m in mistakes[:3]: lines.append(f"   • {m}")

    disc = result.get("disclaimer", "")
    if disc: lines.append(f"\n📌 {disc}")

    return "\n".join(lines)


if __name__ == "__main__":
    print("Testing research engine v3...")
    # Test 1: Known city
    print("\n=== Test 1: Houston HVAC ===")
    result = research_permit("HVAC system replacement", "Austin", "TX", "78701", use_cache=False)
    print(format_for_display(result))
    print(f"\nApply URL: {result.get('apply_url')}")
    print(f"Apply PDF: {result.get('apply_pdf')}")
    print(f"Apply Phone: {result.get('apply_phone')}")
    print(f"Maps: {result.get('apply_google_maps')}")
