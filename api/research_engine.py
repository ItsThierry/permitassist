#!/usr/bin/env python3
"""
PermitAssist — AI Research Engine v2
Live permit requirement research for any trade job + US location.
Uses Tavily web search + GPT-4o for accurate, sourced, current data.
"""

import os
import json
import time
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
        return  # already loaded
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
    """
    Find the best-matching trade in the knowledge base and return its data as context.
    """
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
                score += len(name)  # longer match = more specific
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
    """
    Return state-specific permit/code context for the given 2-letter state code.
    """
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


def _get_city_context(city: str, state: str) -> str:
    """
    Return city-specific permit office, fees, and notes from the cities KB.
    Fuzzy match on city name. Also checks county data for unincorporated areas.
    """
    _load_knowledge()
    cities = _CITIES_KB.get("cities", {})
    if not cities:
        return ""

    city_lower = city.lower().strip()
    state_upper = state.upper().strip()

    # Exact match first
    for key, data in cities.items():
        if data.get("city", "").lower() == city_lower and data.get("state", "").upper() == state_upper:
            return _format_city_context(data)

    # Partial match (city name contained in key)
    for key, data in cities.items():
        if city_lower in key.lower() and data.get("state", "").upper() == state_upper:
            return _format_city_context(data)

    # Check county data if city not found
    county_ctx = _get_county_context(city, state)
    if county_ctx:
        return county_ctx

    # State-only match (city not in KB but state matches)
    return ""


def _get_county_context(city: str, state: str) -> str:
    """
    Return county-level permit info if the city is in an unincorporated area.
    Checks counties KB for known county-level jurisdictions.
    """
    _load_knowledge()
    counties = _CITIES_KB.get("counties", {})
    if not counties:
        return ""

    state_upper = state.upper().strip()
    city_lower = city.lower().strip()

    # Map known unincorporated suburban areas to their county
    county_hints = {
        # TX — unincorporated areas use county (or have no permit requirement)
        "katy": "harris_county_tx", "spring": "harris_county_tx",
        "humble": "harris_county_tx", "cypress": "harris_county_tx",
        # AZ — Maricopa County for areas not incorporated
        "cave creek": "maricopa_county_az", "paradise valley": "maricopa_county_az",
        # IL — unincorporated Cook County
        "unincorporated cook": "cook_county_il",
        # WA — unincorporated King County
        "unincorporated king": "king_county_wa",
        # FL — Broward County
        "unincorporated broward": "broward_county_fl",
    }

    county_key = county_hints.get(city_lower)
    if county_key and county_key in counties:
        data = counties[county_key]
        lines = [
            f"=== COUNTY PERMIT INFO: {data.get('county','').upper()}, {data.get('state','').upper()} ===",
            f"Note: {data.get('note', '')}",
            f"Permit office: {data.get('permit_office', 'County Building Dept')}",
        ]
        fees = data.get("fees", {})
        if fees:
            for k, v in fees.items():
                if k != "fee_note" and k != "note":
                    lines.append(f"  {k.replace('_',' ').title()}: {v}")
            if fees.get("fee_note"):
                lines.append(f"  Note: {fees['fee_note']}")
            if fees.get("note"):
                lines.append(f"  Note: {fees['note']}")
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
    """
    Get state-specific notes for a specific trade (e.g. CA notes for HVAC).
    """
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
    """
    Search for real permit info via Tavily.
    Returns list of {title, url, content} dicts.
    Falls back to empty list on any error — engine still works without it.
    """
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
                "include_domains": [],          # allow all
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
                "content": r.get("content", "")[:600],  # keep context tight
            })
        return results
    except Exception as e:
        print(f"[tavily] Search failed (non-fatal): {e}")
        return []

def build_search_context(job_type: str, city: str, state: str, zip_code: str = "") -> str:
    """
    Run 2 targeted Tavily searches and return a compact context string for GPT.
    Searches:
      1. City-specific official permit page
      2. General trade job permit requirements for that state/city
    Prepends local knowledge base context.
    """
    location = f"{city}, {state}"
    if zip_code:
        location += f" {zip_code}"

    # Search 1: official city permit page
    q1 = f"{city} {state} building permit {job_type} official requirements 2024 2025"
    # Search 2: specific department / fee info
    q2 = f"\"{city}\" \"{state}\" permit fee schedule {job_type} site:.gov OR site:.org"

    results1 = tavily_search(q1, max_results=3)
    results2 = tavily_search(q2, max_results=2)
    all_results = results1 + results2

    if not all_results:
        return ""  # GPT will rely on training data only

    lines = ["=== REAL-TIME WEB SEARCH RESULTS (use these to verify/improve your answer) ==="]
    for r in all_results:
        lines.append(f"\nSource: {r['url']}")
        lines.append(f"Title: {r['title']}")
        lines.append(f"Excerpt: {r['content']}")
        lines.append("---")

    return "\n".join(lines)

# ─── System Prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are PermitAssist, an expert AI that helps contractors understand permit requirements for residential and commercial trade work.

You have deep expertise in:
- HVAC (mechanical permits, IMC/UMC, EPA 608 refrigerant rules)
- Electrical (NEC, panel upgrades, EV chargers, NEC Article 625)
- Roofing (IRC Chapter 9, wind zones, fire ratings)
- Plumbing (IPC/UPC, water heaters, T&P valves, seismic strapping)
- Mini splits (refrigerant + electrical dual permits)
- Solar PV (NEC Article 690, utility interconnection)
- Standby generators (NEC 445/702, gas permits, setbacks)
- Decks (IRC R507, footing depths, frost lines, guardrails)

PRIORITY ORDER for your answers:
1. Real-time web search results in the user message — most current, city-specific
2. Knowledge base context in the user message — verified trade and state rules
3. Your training knowledge — fill remaining gaps only

RULES:
1. Identify EVERY permit type required — a single job can need multiple (e.g. solar = Electrical Permit + Building Permit). List them ALL.
2. For each permit, give the EXACT portal_selection string the contractor must choose inside the city portal (e.g. "HVAC Replacement - Residential", "Panel Upgrade 200A", "Gas Water Heater", "Roof Replacement - Shingles"). Be specific — not just "Mechanical Permit".
3. Name the EXACT department/office to apply to — full official name.
4. Provide the ONLINE PORTAL URL from the knowledge base context if provided — use it as apply_url.
5. Give SPECIFIC fee amounts when known (e.g. "$68 first system, $19 additional" not "$50-$200"). Use knowledge base city data first, then web search results.
6. Give realistic timelines — over-the-counter same day vs plan review weeks.
7. List ALL required inspections in order with exact timing (rough-in before covering, final after completion).
8. Flag common mistakes that cause delays or failed inspections.
9. State exactly WHO can pull this permit — license type required (e.g. "TACL license — Texas Air Conditioning Contractor").
10. If the job might NOT require a permit in some jurisdictions, say so clearly with the specific exemption rule.
11. ALWAYS include disclaimer to verify with local authority.
12. Set confidence = "high" only if you have solid city-specific info. Use "medium" for state-level rules only.

TONE: Talk like a knowledgeable friend in the trades — practical, direct, no fluff. The contractor is on a job site.

Return ONLY a JSON object with these exact fields:
{
  "job_summary": "brief description",
  "location": "city, state",
  "permits_required": [
    {"permit_type": "exact permit category name (e.g. Mechanical Permit, Electrical Permit)", "portal_selection": "exact sub-type to select in the city portal (e.g. HVAC Replacement, Panel Upgrade, Gas Water Heater)", "required": true/false/"maybe", "notes": "1-sentence context"}
  ],
  "applying_office": "exact department name",
  "apply_url": "official ONLINE PORTAL URL or null — NEVER a PDF link. If only a PDF exists, set this to null.",
  "apply_pdf": "URL if the only application method is a PDF form, otherwise null",
  "apply_phone": "phone number of the permit office e.g. (832) 394-8880 or null",
  "apply_address": "physical address if known",
  "fee_range": "e.g. $75–$250 based on project valuation",
  "approval_timeline": {
    "simple": "e.g. Same day / over the counter",
    "complex": "e.g. 5–10 business days for plan review"
  },
  "inspections": [
    {"stage": "name", "description": "what inspector checks", "timing": "when to schedule"}
  ],
  "license_required": "yes/no/varies — who can pull this permit",
  "common_mistakes": ["list of things that cause delays or failures"],
  "pro_tips": ["practical advice from experienced contractors"],
  "sources": ["official source URLs cited"],
  "confidence": "high/medium/low",
  "disclaimer": "Always verify current requirements with your local building department before starting work."
}"""

# ─── Main Research Function ───────────────────────────────────────────────────

def research_permit(job_type: str, city: str, state: str, zip_code: str = "", use_cache: bool = True) -> dict:
    """
    Research permit requirements for a job + location.
    Uses Tavily web search to ground GPT-4o with current info.
    Returns structured dict.
    """
    init_cache()

    key = cache_key(job_type, city, state)

    # Cache hit
    if use_cache:
        cached = get_cached(key)
        if cached:
            cached["_cached"] = True
            return cached

    location_str = f"{city}, {state}"
    if zip_code:
        location_str += f" {zip_code}"

    # ── Step 1: Knowledge base context (instant, no API call) ──
    trade_context      = _get_trade_context(job_type)
    state_context      = _get_state_context(state)
    trade_state_notes  = _get_trade_state_notes(job_type, state)
    city_context       = _get_city_context(city, state)
    if trade_context:  print(f"[research] KB match found for job type")
    if state_context:  print(f"[research] KB state context loaded for {state}")
    if city_context:   print(f"[research] KB city context found for {city}, {state}")

    # ── Step 2: Live web search ──
    print(f"[research] Searching web for: {job_type} in {location_str}")
    search_context = build_search_context(job_type, city, state, zip_code)
    if search_context:
        print(f"[research] Got {search_context.count('Source:')} web sources")
    else:
        print("[research] No web results — using KB + GPT training data")

    # ── Step 3: Build combined context ──
    kb_context_parts = []
    if city_context:       kb_context_parts.append(city_context)       # Most specific first
    if trade_state_notes:  kb_context_parts.append(trade_state_notes)
    if trade_context:      kb_context_parts.append(trade_context)
    if state_context:      kb_context_parts.append(state_context)
    kb_context = "\n\n".join(kb_context_parts)

    # ── Step 4: GPT synthesis ──
    user_prompt = f"""A contractor needs permit information for this job:

Job: {job_type}
Location: {location_str}

{kb_context}

{search_context}

Using all context above, research the specific permit requirements for this exact job in {city}, {state}.
Priority order:
1. Live web search results above (most current, city-specific)
2. Knowledge base context (reliable defaults for this trade + state)
3. Your training knowledge (fill remaining gaps)

Be as specific as possible to {city}, {state}.
Include the actual department name, actual website URL, actual fee schedule.
If web search found city-specific fee info, use that over the KB default.

Return ONLY the JSON object."""

    start = time.time()

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system",  "content": SYSTEM_PROMPT},
            {"role": "user",    "content": user_prompt},
        ],
        temperature=0.15,   # Very consistent — this is factual research
        max_tokens=1600,
        response_format={"type": "json_object"},
    )

    elapsed = round((time.time() - start) * 1000)
    raw = response.choices[0].message.content
    result = json.loads(raw)

    # Add metadata
    result["_meta"] = {
        "generated_at":   datetime.now().isoformat(),
        "response_ms":    elapsed,
        "cached":         False,
        "model":          "gpt-4o",
        "web_sources":    search_context.count("Source:") if search_context else 0,
        "job_type":       job_type,
        "city":           city,
        "state":          state,
        "zip_code":       zip_code,
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

    lines.append("="*60)
    lines.append(f"📋 PERMIT RESEARCH: {job.upper()}")
    lines.append(f"📍 Location: {loc}")
    lines.append(f"🎯 Confidence: {conf}  {'⚡ CACHED' if cached else f'🌐 {sources} web source(s)'}")
    lines.append("="*60)

    permits = result.get("permits_required", [])
    if permits:
        lines.append("\n🔖 PERMITS REQUIRED:")
        for p in permits:
            req  = p.get("required", "?")
            icon = "✅" if req is True else ("⚠️" if req == "maybe" else "❌")
            lines.append(f"  {icon} {p.get('permit_type', 'Unknown')}")
            if p.get("notes"):
                lines.append(f"     → {p['notes']}")

    office = result.get("applying_office", "")
    url    = result.get("apply_url", "")
    addr   = result.get("apply_address", "")
    if office: lines.append(f"\n🏢 APPLY TO: {office}")
    if url:    lines.append(f"   🌐 {url}")
    if addr:   lines.append(f"   📬 {addr}")

    fee = result.get("fee_range", "")
    if fee: lines.append(f"\n💰 FEES: {fee}")

    tl = result.get("approval_timeline", {})
    if tl:
        lines.append("\n⏱️  TIMELINE:")
        if tl.get("simple"):  lines.append(f"   Simple jobs: {tl['simple']}")
        if tl.get("complex"): lines.append(f"   Plan review: {tl['complex']}")

    lic = result.get("license_required", "")
    if lic: lines.append(f"\n📜 WHO CAN PULL: {lic}")

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
    print("Testing research engine v2 (with Tavily)...")
    result = research_permit("HVAC system replacement", "Austin", "TX", "78701", use_cache=False)
    print(format_for_display(result))
