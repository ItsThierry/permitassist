#!/usr/bin/env python3
"""
PermitAssist Auto-Verification System
Searches Tavily for permit requirements for top 20 cities x 5 trades
and stores results in /data/permitassist/data/verified_cities.json

Usage:
    python scripts/auto_verify.py

Requires TAVILY_API_KEY environment variable.
"""

import json
import os
import re
import time
import requests
from datetime import datetime, timedelta, timezone

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
_default_data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
DATA_DIR = os.environ.get("CACHE_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or _default_data_dir
OUTPUT_FILE = os.path.join(DATA_DIR, "verified_cities.json")

CITIES = [
    # Texas (high volume, extreme city-to-city variation)
    ("Houston",         "TX"),
    ("Dallas",          "TX"),
    ("Austin",          "TX"),
    ("San Antonio",     "TX"),
    ("Fort Worth",      "TX"),
    ("El Paso",         "TX"),
    ("Arlington",       "TX"),
    ("Corpus Christi",  "TX"),
    ("Plano",           "TX"),
    ("Lubbock",         "TX"),
    # Florida (complex building code, FBC)
    ("Jacksonville",    "FL"),
    ("Miami",           "FL"),
    ("Tampa",           "FL"),
    ("Orlando",         "FL"),
    ("St Petersburg",   "FL"),
    ("Hialeah",         "FL"),
    ("Fort Lauderdale", "FL"),
    ("Tallahassee",     "FL"),
    ("Cape Coral",      "FL"),
    ("Pembroke Pines",  "FL"),
    # California (own code cycle, heavy amendments)
    ("Los Angeles",     "CA"),
    ("San Diego",       "CA"),
    ("San Jose",        "CA"),
    ("San Francisco",   "CA"),
    ("Fresno",          "CA"),
    ("Sacramento",      "CA"),
    ("Long Beach",      "CA"),
    ("Oakland",         "CA"),
    ("Bakersfield",     "CA"),
    ("Anaheim",         "CA"),
    # Arizona
    ("Phoenix",         "AZ"),
    ("Tucson",          "AZ"),
    ("Mesa",            "AZ"),
    ("Chandler",        "AZ"),
    ("Scottsdale",      "AZ"),
    # Georgia
    ("Atlanta",         "GA"),
    ("Columbus",        "GA"),
    ("Augusta",         "GA"),
    ("Savannah",        "GA"),
    # North Carolina
    ("Charlotte",       "NC"),
    ("Raleigh",         "NC"),
    ("Greensboro",      "NC"),
    ("Durham",          "NC"),
    # Tennessee
    ("Nashville",       "TN"),
    ("Memphis",         "TN"),
    ("Knoxville",       "TN"),
    # Colorado
    ("Denver",          "CO"),
    ("Colorado Springs","CO"),
    ("Aurora",          "CO"),
    # Nevada
    ("Las Vegas",       "NV"),
    ("Henderson",       "NV"),
    ("Reno",            "NV"),
    # Ohio
    ("Columbus",        "OH"),
    ("Cleveland",       "OH"),
    ("Cincinnati",      "OH"),
    # Indiana
    ("Indianapolis",    "IN"),
    # Washington
    ("Seattle",         "WA"),
    ("Spokane",         "WA"),
    ("Tacoma",          "WA"),
    # Oregon
    ("Portland",        "OR"),
    ("Eugene",          "OR"),
    # Michigan
    ("Detroit",         "MI"),
    ("Grand Rapids",    "MI"),
    # Illinois
    ("Chicago",         "IL"),
    # Pennsylvania
    ("Philadelphia",    "PA"),
    ("Pittsburgh",      "PA"),
    # New York
    ("New York City",   "NY"),
    ("Buffalo",         "NY"),
    # Virginia
    ("Virginia Beach",  "VA"),
    ("Norfolk",         "VA"),
    ("Richmond",        "VA"),
    ("Chesapeake",      "VA"),
    # Missouri
    ("Kansas City",     "MO"),
    ("St Louis",        "MO"),
    # Minnesota
    ("Minneapolis",     "MN"),
    ("St Paul",         "MN"),
    # Wisconsin
    ("Milwaukee",       "WI"),
    ("Madison",         "WI"),
    # Louisiana
    ("New Orleans",     "LA"),
    ("Baton Rouge",     "LA"),
    # Alabama
    ("Birmingham",      "AL"),
    ("Montgomery",      "AL"),
    # South Carolina
    ("Charleston",      "SC"),
    ("Columbia",        "SC"),
    # Oklahoma
    ("Oklahoma City",   "OK"),
    ("Tulsa",           "OK"),
    # Kentucky
    ("Louisville",      "KY"),
    ("Lexington",       "KY"),
    # Maryland
    ("Baltimore",       "MD"),
    # Massachusetts
    ("Boston",          "MA"),
    # Utah
    ("Salt Lake City",  "UT"),
    # New Mexico
    ("Albuquerque",     "NM"),
    # Nebraska
    ("Omaha",           "NE"),
    # Kansas
    ("Wichita",         "KS"),
]

TRADES = ["electrical", "plumbing", "hvac", "roofing", "general"]


def make_key(city: str, state: str, trade: str) -> str:
    return f"{city.lower().replace(' ', '_')}_{state.lower()}_{trade}"


def tavily_search(query: str, max_results: int = 4) -> list:
    if not TAVILY_API_KEY:
        print("[auto_verify] No TAVILY_API_KEY — set env var to enable real searches")
        return []
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "basic",
                "max_results": max_results,
            },
            timeout=12,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        print(f"[auto_verify] Tavily error: {e}")
        return []


def extract_phone(text: str) -> str:
    match = re.search(r'\(?\d{3}\)?[\s\-\.]\d{3}[\s\-\.]\d{4}', text)
    return match.group(0).strip() if match else ""


def extract_fee(text: str) -> str:
    match = re.search(r'\$[\d,]+(?:[\s\-–]+\$[\d,]+)?', text)
    return match.group(0).strip() if match else ""


def verify_city_trade(city: str, state: str, trade: str) -> dict | None:
    q1 = f"{city} {state} {trade} permit requirements fees phone number building department 2025"
    q2 = f'"{city}" "{state}" {trade} permit official building department site:.gov OR "city of" OR "cityof"'

    results1 = tavily_search(q1, max_results=3)
    results2 = tavily_search(q2, max_results=2)
    all_results = results1 + results2

    if not all_results:
        return None

    # Use best result for metadata
    best = all_results[0]
    source_url = best.get("url", "")

    # Aggregate content from all results for better extraction
    combined_content = " ".join(r.get("content", "") for r in all_results)

    phone = extract_phone(combined_content)
    fee   = extract_fee(combined_content)

    # Find a .gov URL if available
    gov_urls = [r.get("url", "") for r in all_results if ".gov" in r.get("url", "")]
    source_url = gov_urls[0] if gov_urls else source_url

    data = {
        "city":    city,
        "state":   state,
        "trade":   trade,
        "summary": combined_content[:600],
        "phone":   phone,
        "fee_range": fee,
        "permit_office": f"{city} Building Department",
        "sources": [r.get("url", "") for r in all_results if r.get("url")][:3],
    }

    return {
        "city":        city,
        "state":       state,
        "trade":       trade,
        "data":        data,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "source_url":  source_url,
        "confidence":  "verified",
    }


def load_existing() -> dict:
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def is_fresh(entry: dict, max_days: int = 90) -> bool:
    try:
        verified_at = datetime.fromisoformat(entry.get("verified_at", ""))
        if verified_at.tzinfo is None:
            verified_at = verified_at.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - verified_at) < timedelta(days=max_days)
    except Exception:
        return False


def run():
    print(f"[auto_verify] Starting verification: {len(CITIES)} cities × {len(TRADES)} trades = {len(CITIES)*len(TRADES)} checks")
    os.makedirs(DATA_DIR, exist_ok=True)

    existing   = load_existing()
    updated    = dict(existing)
    new_count  = 0
    skip_count = 0
    err_count  = 0

    for city, state in CITIES:
        for trade in TRADES:
            key = make_key(city, state, trade)

            if key in existing and is_fresh(existing[key]):
                skip_count += 1
                print(f"[auto_verify] SKIP (fresh < 90d): {key}")
                continue

            print(f"[auto_verify] Verifying: {city}, {state} — {trade}")
            try:
                result = verify_city_trade(city, state, trade)
                if result:
                    updated[key] = result
                    new_count += 1
                    print(f"  ✓ {key} — {result['source_url'][:70]}")
                else:
                    err_count += 1
                    print(f"  ✗ No data returned for {key}")
            except Exception as e:
                err_count += 1
                print(f"  ✗ Error for {key}: {e}")

            # Write incrementally after every entry so progress is never lost
            with open(OUTPUT_FILE, "w") as f:
                json.dump(updated, f, indent=2)

            # Respect Tavily rate limits
            time.sleep(0.6)

    print(f"\n[auto_verify] Done.")
    print(f"  New verified: {new_count}")
    print(f"  Skipped (fresh): {skip_count}")
    print(f"  Errors: {err_count}")
    print(f"  Total stored: {len(updated)}")
    print(f"  Output: {OUTPUT_FILE}")


def get_verified_for_city_trade(city: str, state: str, trade: str) -> dict | None:
    """
    Called by research_engine to check verified data before hitting OpenAI.
    Returns entry if < 90 days old, else None.
    """
    if not os.path.exists(OUTPUT_FILE):
        return None
    try:
        with open(OUTPUT_FILE) as f:
            data = json.load(f)
        key = make_key(city, state, trade)
        entry = data.get(key)
        if entry and is_fresh(entry):
            return entry
    except Exception:
        pass
    return None


def get_verified_cities() -> list:
    """
    Returns list of unique {city, state} combos that have verified data.
    Used by GET /api/verified-cities endpoint.
    """
    if not os.path.exists(OUTPUT_FILE):
        return []
    try:
        with open(OUTPUT_FILE) as f:
            data = json.load(f)
        seen = set()
        result = []
        for entry in data.values():
            key = f"{entry.get('city', '')}|{entry.get('state', '')}"
            if key not in seen:
                seen.add(key)
                result.append({"city": entry["city"], "state": entry["state"]})
        return sorted(result, key=lambda x: (x["state"], x["city"]))
    except Exception:
        return []


if __name__ == "__main__":
    run()
