#!/usr/bin/env python3
"""
PermitAssist — AI Research Engine v5
Improvements over v4:
  - Added Gemini 3 Pro as fallback if OpenAI is unavailable
  - Fallback uses Gemini JSON mode (response_mime_type: application/json)
  - Fallback is transparent — same result structure, same post-processing
"""

import os
import json
import time
import re
import sqlite3
import hashlib
import io
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from copy import deepcopy
from urllib.parse import urljoin, urlparse
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from openai import OpenAI
import google.generativeai as genai
import pdfplumber

client = OpenAI()

# Gemini fallback client (used if OpenAI is unavailable)
_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if _GEMINI_API_KEY:
    genai.configure(api_key=_GEMINI_API_KEY)
_gemini_fallback_model = "gemini-2.5-pro"

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")
BRAVE_SEARCH_API_KEY = os.environ.get("BRAVE_SEARCH_API_KEY", "BSAdf-HMvCIiwJw7UlYFXghzqwfv_Pp")
ACCELA_APP_ID = os.environ.get("ACCELA_APP_ID", "639125015399507099")
ACCELA_APP_SECRET = os.environ.get("ACCELA_APP_SECRET", "a516edb01cab4261baf14a478ee3c9ac")
ACCELA_BASE_URL = "https://apis.accela.com"
ACCELA_DOCS_BASE_URL = "https://developer.accela.com/docs/api_reference"
_accela_token = ""
_accela_token_expiry = 0.0
_accela_agencies_cache: dict[str, object] = {"expires": 0.0, "result": []}
# Support RAILWAY_VOLUME_MOUNT_PATH or CACHE_DIR env var for persistent volumes
_default_data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
_data_dir = os.environ.get("CACHE_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or _default_data_dir
CACHE_DB       = os.path.join(_data_dir, "cache.db")
KNOWLEDGE_DIR  = os.path.join(os.path.dirname(__file__), "..", "knowledge")

SUMMARY_JUNK_PATTERNS = [
    r'\*\s*Email;\s*"Click to submit an email[^\n]*',
    r'\*\s*Facebook\s*"Click to share with Facebook[^\n]*',
    r'\*\s*LinkedIn\s*"Click to share with LinkedIn[^\n]*',
    r'\*\s*Twitter\s*"Click to share with Twitter[^\n]*',
    r'\*\s*Reddit\s*"Click to share with Reddit[^\n]*',
    r'Feedback;\s*"Click to submit an email to feedback[^\n]*',
    r'Print;\s*"Click to print this page[^\n]*',
    r'Helpful Links\.',
]

POSITIVE_URL_HINTS = ("building", "permits", "codes", "planning", "development", "pds", "dsd", "bds")
NEGATIVE_DOMAIN_HINTS = ("municode", "permits.com", "countyoffice.org", "lawserver", "justia", "findlaw", "contractor", "homeadvisor", "thumbtack", "angi", "yelp", "houzz")
SOCIAL_DOMAIN_HINTS = ("reddit", "quora", "youtube", "facebook")
PDF_URL_HINTS = ("fee", "schedule", "permit")
DIRECT_FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

JURISDICTION_PATTERNS = {
    "AK": {"authority": "borough", "examples": ["kenai peninsula borough", "matanuska-susitna borough", "fairbanks north star borough"]},
    "LA": {"authority": "parish", "examples": ["east baton rouge parish", "jefferson parish", "orleans parish"]},
    "VA": {"authority": "city", "note": "Virginia cities are independent of counties"},
}

UNINCORPORATED_KEYWORDS = ["unincorporated", "county area", "rural", "township"]
FEE_LINK_KEYWORDS = ["fee schedule", "fee table", "permit fees", "building fees", "inspection fee", "permit checklist", "inspection checklist", "apply online", "online portal"]
PHONE_CONTEXT_KEYWORDS = ["permit", "building", "codes", "office", "main", "department", "division", "contact", "call", "phone"]
STATE_NAME_MAP = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california",
    "CO": "colorado", "CT": "connecticut", "DE": "delaware", "FL": "florida", "GA": "georgia",
    "HI": "hawaii", "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa",
    "KS": "kansas", "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi", "MO": "missouri",
    "MT": "montana", "NE": "nebraska", "NV": "nevada", "NH": "new hampshire", "NJ": "new jersey",
    "NM": "new mexico", "NY": "new york", "NC": "north carolina", "ND": "north dakota", "OH": "ohio",
    "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina",
    "SD": "south dakota", "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont",
    "VA": "virginia", "WA": "washington", "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming",
    "DC": "district of columbia", "PR": "puerto rico",
}


def clean_summary_text(text: str, max_len: int = 700) -> str:
    if not text:
        return ""
    cleaned = str(text)
    for pat in SUMMARY_JUNK_PATTERNS:
        cleaned = re.sub(pat, " ", cleaned, flags=re.I)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(" \n\t;,-")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rsplit(' ', 1)[0].rstrip(' ,;:-') + '…'
    return cleaned


def clean_verified_entry(entry: dict | None) -> dict | None:
    if not entry:
        return entry
    clean = deepcopy(entry)
    data = clean.get("data") or {}
    if isinstance(data, dict):
        data["summary"] = clean_summary_text(data.get("summary", ""))
        srcs = data.get("sources") or []
        data["sources"] = [s for s in srcs if isinstance(s, str) and s.startswith("http")]
        clean["data"] = data
    return clean


def normalize_sources(*groups) -> list[str]:
    seen = set()
    out = []
    for group in groups:
        if not group:
            continue
        if isinstance(group, str):
            group = [group]
        for src in group:
            if not isinstance(src, str):
                continue
            src = src.strip()
            if not src.startswith("http"):
                continue
            if src in seen:
                continue
            seen.add(src)
            out.append(src)
    return out[:8]


def compute_missing_fields(result: dict) -> list[str]:
    missing = []
    permits = result.get("permits_required")
    verdict = str(result.get("permit_verdict") or "").upper()
    if not isinstance(permits, list):
        missing.append("permits_required")
        permits = []
    if not permits and verdict != "NO":
        missing.append("permit_details")
    if not result.get("applying_office"):
        missing.append("applying_office")
    if not result.get("fee_range"):
        missing.append("fee_range")
    tl = result.get("approval_timeline") or {}
    if not isinstance(tl, dict) or not (tl.get("simple") or tl.get("complex")):
        missing.append("approval_timeline")
    insps = result.get("inspections") or []
    if not isinstance(insps, list) or not insps:
        missing.append("inspections")
    return missing


def downgrade_confidence(confidence: str, steps: int = 1) -> str:
    levels = ["low", "medium", "high"]
    conf = str(confidence or "medium").lower()
    if conf not in levels:
        conf = "medium"
    idx = levels.index(conf)
    return levels[max(0, idx - steps)]


def derive_confidence_reason(result: dict, city_match_level: str, auto_verified: bool, missing_fields: list[str], web_sources: int) -> str:
    if auto_verified:
        reason = "Verified city/trade data found from official sources"
    elif city_match_level == "city" and web_sources > 0:
        reason = "City-specific match supported by live web research"
    elif city_match_level == "county":
        reason = "County-level fallback used because exact city data was limited"
    elif web_sources > 0:
        reason = "Live web research found partial local guidance"
    else:
        reason = "Limited local data, answer relies on fallback rules"
    if missing_fields:
        reason += f". Needs review for: {', '.join(missing_fields[:3])}"
    return reason


def get_accela_token() -> str:
    """Get or refresh an Accela OAuth2 access token."""
    global _accela_token, _accela_token_expiry
    if _accela_token and time.time() < _accela_token_expiry - 60:
        return _accela_token
    if not (ACCELA_APP_ID and ACCELA_APP_SECRET):
        return ""
    try:
        resp = requests.post(
            f"{ACCELA_BASE_URL}/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": ACCELA_APP_ID,
                "client_secret": ACCELA_APP_SECRET,
                "scope": "records",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=12,
        )
        if resp.ok:
            data = resp.json()
            _accela_token = data.get("access_token", "")
            expires_in = int(data.get("expires_in", 3600) or 3600)
            _accela_token_expiry = time.time() + expires_in
            print(f"[accela] Token obtained, expires in {expires_in}s")
            return _accela_token
        print(f"[accela] Token fetch failed: {resp.status_code} {resp.text[:300]}")
    except Exception as e:
        print(f"[accela] Token fetch failed: {e}")
    return ""


def _accela_get(path: str, *, params: dict | None = None, agency_name: str = "", require_token: bool = False) -> dict | None:
    if not ACCELA_APP_ID:
        return None
    headers = {}
    token = ""
    if require_token:
        token = get_accela_token()
        if not token:
            return None
        headers["Authorization"] = f"Bearer {token}"
    else:
        headers["x-accela-appid"] = ACCELA_APP_ID
    if agency_name:
        headers["x-accela-agency"] = agency_name
    try:
        resp = requests.get(f"{ACCELA_BASE_URL}{path}", headers=headers, params=params or {}, timeout=15)
        if resp.ok:
            return resp.json()
        print(f"[accela] GET {path} failed: {resp.status_code} {resp.text[:300]}")
    except Exception as e:
        print(f"[accela] GET {path} failed: {e}")
    return None


def _accela_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 3}


def accela_list_agencies(limit: int = 200) -> list[dict]:
    now = time.time()
    cached = _accela_agencies_cache.get("result")
    if cached and now < float(_accela_agencies_cache.get("expires", 0) or 0):
        return list(cached)
    data = _accela_get("/v4/agencies", params={"limit": limit, "offset": 0}) or {}
    agencies = data.get("result") or []
    _accela_agencies_cache["result"] = agencies
    _accela_agencies_cache["expires"] = now + 3600
    return agencies


def accela_find_agency(city: str, state: str) -> dict | None:
    state_upper = (state or "").upper()
    city_lower = (city or "").strip().lower()
    if not city_lower or not state_upper:
        return None
    candidates = []
    direct = _accela_get("/v4/agencies", params={"name": city, "limit": 25}) or {}
    candidates.extend(direct.get("result") or [])
    if not candidates:
        candidates.extend(accela_list_agencies())
    best = None
    best_score = -1
    for agency in candidates:
        agency_state = str(agency.get("state") or "").upper()
        if state_upper and agency_state and agency_state != state_upper:
            continue
        haystacks = [str(agency.get("name") or ""), str(agency.get("displayName") or "")]
        score = 0
        for hay in haystacks:
            hay_l = hay.lower()
            if hay_l == city_lower:
                score = max(score, 100)
            elif city_lower in hay_l:
                score = max(score, 80)
            elif any(part and part in hay_l for part in city_lower.split()):
                score = max(score, 40)
        if score > best_score:
            best = agency
            best_score = score
    return best if best_score >= 40 else None


def _accela_build_citizen_portal_url(agency: dict) -> str:
    """Build the citizen-facing Accela portal URL for an agency."""
    spc = str(agency.get("serviceProviderCode") or "").upper()
    hosted = agency.get("hostedACA", False)
    if hosted and spc:
        return f"https://aca-prod.accela.com/{spc}/"
    return ""


def _accela_get_record_types(agency_name: str, job_type: str) -> list[dict]:
    """Record types require user auth — not available with app-level token. Return empty."""
    return []


def _accela_get_fee_schedules(agency_name: str, record_type_id: str) -> list[dict]:
    """Fee schedules require user auth — not available with app-level token. Return empty."""
    return []


def get_accela_processing_time(agency_id: str, permit_type: str) -> str:
    """Accela v4 docs do not expose processing time metadata on the public settings endpoints used here."""
    return ""


def accela_get_permit_info(city: str, state: str, job_type: str) -> dict | None:
    agency = accela_find_agency(city, state)
    if not agency:
        print(f"[accela] No agency found for {city}, {state}")
        return None

    agency_name = str(agency.get("name") or "")
    display = str(agency.get("display") or agency_name)
    portal_url = _accela_build_citizen_portal_url(agency)

    print(f"[accela] Matched agency: {agency_name} | Portal: {portal_url or 'none (not hostedACA)'}")

    summary = f"Accela agency matched: {display} ({agency.get('state') or state})."
    if portal_url:
        summary += f" Citizen portal: {portal_url}"

    structured = {
        "fees": [],
        "portal_url": portal_url,
        "raw_text": summary,
        "source": "layer0_5_accela",
        "field_sources": {"portal_url": "accela_api"} if portal_url else {},
        "field_confidence": {"portal_url": "high"} if portal_url else {},
        "freshness": "live_accela_api",
    }
    return {
        "agency": agency,
        "portal_url": portal_url,
        "structured": structured,
        "summary": summary,
    }

# ─── Hardcoded County Fallback Data ───────────────────────────────────────────

COUNTY_DATA = {
    "cook_il": {
        "name": "Cook County, IL",
        "phone": "312-603-0500",
        "url": "https://www.cookcountyil.gov/service/building-permits",
        "office": "Cook County Building & Zoning Department"
    },
    "harris_tx": {
        "name": "Harris County, TX",
        "phone": "713-274-3880",
        "url": "https://permits.harriscountytx.gov",
        "office": "Harris County Permits Office"
    },
    "maricopa_az": {
        "name": "Maricopa County, AZ",
        "phone": "602-506-3301",
        "url": "https://mcassessor.maricopa.gov/permits",
        "office": "Maricopa County Planning & Development"
    },
    "los_angeles_ca": {
        "name": "Los Angeles County, CA",
        "phone": "626-458-3173",
        "url": "https://dpw.lacounty.gov/building-and-safety",
        "office": "LA County Dept of Public Works — Building & Safety"
    },
    "king_wa": {
        "name": "King County, WA",
        "phone": "206-296-6600",
        "url": "https://kingcounty.gov/depts/permitting",
        "office": "King County Permitting Division"
    },
    "miami_dade_fl": {
        "name": "Miami-Dade County, FL",
        "phone": "786-315-2000",
        "url": "https://www.miamidade.gov/permits",
        "office": "Miami-Dade County Building Department"
    },
    "dallas_tx": {
        "name": "Dallas County, TX",
        "phone": "214-653-7399",
        "url": "https://www.dallascounty.org/departments/perm_imp/index.php",
        "office": "Dallas County Permits & Improvements"
    },
    "orange_ca": {
        "name": "Orange County, CA",
        "phone": "714-667-8888",
        "url": "https://ocds.ocpublicworks.com",
        "office": "Orange County Development Services"
    },
    "san_diego_ca": {
        "name": "San Diego County, CA",
        "phone": "858-694-3816",
        "url": "https://www.sandiegocounty.gov/pds",
        "office": "San Diego County Planning & Development Services"
    },
    "clark_nv": {
        "name": "Clark County, NV",
        "phone": "702-455-3000",
        "url": "https://www.clarkcountynv.gov/government/departments/building_department",
        "office": "Clark County Building Department"
    },
    "tarrant_tx": {
        "name": "Tarrant County, TX",
        "phone": "817-884-1111",
        "url": "https://www.tarrantcounty.com/en/community-development.html",
        "office": "Tarrant County Community Development"
    },
    "bexar_tx": {
        "name": "Bexar County, TX",
        "phone": "210-335-2700",
        "url": "https://www.bexar.org/1730/Development-Services",
        "office": "Bexar County Development Services"
    },
    "broward_fl": {
        "name": "Broward County, FL",
        "phone": "954-765-4400",
        "url": "https://www.broward.org/PermitsLicensing",
        "office": "Broward County Permits & Licensing"
    },
    "santa_clara_ca": {
        "name": "Santa Clara County, CA",
        "phone": "408-299-5700",
        "url": "https://www.sccgov.org/sites/opa",
        "office": "Santa Clara County Planning & Development"
    },
    "travis_tx": {
        "name": "Travis County, TX",
        "phone": "512-854-9188",
        "url": "https://www.traviscountytx.gov/tnr/development-services",
        "office": "Travis County Development Services"
    },
    "twin_falls_id": {
        "name": "Twin Falls County Building Department",
        "office": "Twin Falls County Building Department",
        "phone": "(208) 736-4011",
        "url": "https://www.tfcounty.org/building",
        "state": "ID"
    },
    "coconino_az": {
        "name": "Coconino County Community Development",
        "office": "Coconino County Community Development",
        "phone": "(928) 679-8850",
        "url": "https://www.coconino.az.gov/188/Community-Development",
        "state": "AZ"
    },
    "missoula_mt": {
        "name": "Missoula County Building Services",
        "office": "Missoula County Building Services",
        "phone": "(406) 258-4657",
        "url": "https://www.missoulacounty.us/government/community-planning/building-services",
        "state": "MT"
    },
    "yellowstone_mt": {
        "name": "Yellowstone County Building Department",
        "office": "Yellowstone County Building Department",
        "phone": "(406) 256-2701",
        "url": "https://www.yellowstonecountymt.gov/building",
        "state": "MT"
    },
    "chittenden_vt": {
        "name": "Chittenden County Regional Planning Commission",
        "office": "Burlington Department of Planning & Zoning",
        "phone": "(802) 865-7188",
        "url": "https://www.burlingtonvt.gov/PZ",
        "state": "VT"
    },
}

CITY_TO_COUNTY = {
    # Cook County, IL
    "alsip": "cook_il", "evanston": "cook_il", "cicero": "cook_il", "oak_park": "cook_il",
    "oak park": "cook_il",
    # Harris County, TX
    "sugar_land": "harris_tx", "sugar land": "harris_tx",
    "pasadena": "harris_tx", "pearland": "harris_tx",
    # Maricopa County, AZ
    "scottsdale": "maricopa_az", "tempe": "maricopa_az", "mesa": "maricopa_az",
    "chandler": "maricopa_az", "gilbert": "maricopa_az", "peoria": "maricopa_az",
    # King County, WA
    "bellevue": "king_wa", "redmond": "king_wa", "kirkland": "king_wa", "renton": "king_wa",
    # Miami-Dade County, FL
    "hialeah": "miami_dade_fl", "coral_gables": "miami_dade_fl", "coral gables": "miami_dade_fl",
    "miami_beach": "miami_dade_fl", "miami beach": "miami_dade_fl",
    # Dallas County, TX
    "irving": "dallas_tx", "garland": "dallas_tx", "mesquite": "dallas_tx",
    # Clark County, NV
    "henderson": "clark_nv", "north_las_vegas": "clark_nv", "north las vegas": "clark_nv",
    # Tarrant County, TX
    "fort_worth": "tarrant_tx", "fort worth": "tarrant_tx", "arlington": "tarrant_tx",
    # Twin Falls County, ID
    "twin_falls": "twin_falls_id", "twin falls": "twin_falls_id",
    # Flagstaff / Coconino County, AZ
    "flagstaff": "coconino_az",
    # Missoula County, MT
    "missoula": "missoula_mt",
    # Billings / Yellowstone County, MT
    "billings": "yellowstone_mt",
    # Burlington / Chittenden County, VT
    "burlington": "chittenden_vt",
}

# ─── Jurisdiction Quirks Database ─────────────────────────────────────────────

JURISDICTION_QUIRKS = {
    "austin_tx": [
        "Austin requires WET STAMPS on all structural drawings — digital signatures and PDFs are NOT accepted at the Development Services counter",
        "Austin DSD is appointment-only for permit submittals — walk-ins are rarely accommodated. Book online at austintexas.gov/dsd",
        "Austin requires a separate ROW (Right of Way) permit for any work affecting the sidewalk or driveway apron",
    ],
    "phoenix_az": [
        "Phoenix requires a separate City Contractor Registration on top of your Arizona ROC license — without city registration your permit will be rejected",
        "Phoenix uses a single-permit system for many trade combos — check if your project qualifies before pulling separate mechanical + electrical permits",
        "Phoenix inspection scheduling requires a minimum 24-hour advance notice through the city's online portal — same-day requests are not accepted",
    ],
    "los_angeles_ca": [
        "LA City requires a CSLB license AND an LA City Business Tax Registration Certificate — contractors often forget the BTRC which causes counter rejection",
        "LA Bureau of Engineering review is separate from Building & Safety — projects near public streets need both approvals",
        "LA has specific Title 24 energy compliance requirements for HVAC replacements — a CF1R form is required at permit application",
    ],
    "chicago_il": [
        "Chicago requires all contractors to be licensed at the CITY level — Illinois state license alone is not sufficient",
        "Chicago's permit portal (Chicago Permits) is separate from Cook County — always apply through the city portal for work within city limits",
        "Chicago requires asbestos testing documentation for any work in buildings built before 1980 before permit issuance",
    ],
    "houston_tx": [
        "Many Houston areas are unincorporated Harris County with NO permit requirements for residential HVAC, electrical, or plumbing — verify your specific address jurisdiction before applying",
        "Houston Permitting Center is walk-in friendly and often issues same-day permits for standard residential trade work",
        "Houston does not require a city contractor license — your state license (TECL, TACL, plumbing license) is sufficient",
    ],
    "dallas_tx": [
        "Dallas requires contractors to register with the city EACH YEAR — registration renewal is often overlooked and causes permit rejection",
        "Dallas uses a permit valuation system — undervaluing your project is flagged and can trigger an audit and penalty fees",
        "Dallas requires a separate grading/drainage permit for any work that changes surface drainage patterns",
    ],
    "san_francisco_ca": [
        "SF DBI requires licensed contractors for virtually all work — owner-builder exemptions are extremely limited in San Francisco",
        "SF has a mandatory 30-day neighbor notification period for many permit types — factor this into your project timeline",
        "SF has unique seismic retrofit requirements that may be triggered by HVAC or electrical work in older buildings (pre-1978)",
    ],
    "miami_fl": [
        "Miami-Dade uses the Florida Building Code with significant local amendments — product approval numbers are required for roofing and window/door products",
        "Miami-Dade requires a Notice of Commencement (NOC) recorded with the county clerk before work begins on projects over $2,500",
        "Miami-Dade HVAC permits require proof of NATE certification or equivalent for the installing technician in some municipalities",
    ],
    "seattle_wa": [
        "Seattle requires a Master Use Permit (MUP) for many projects in addition to the building permit — check for both before starting",
        "Seattle has mandatory energy benchmarking for commercial buildings — HVAC replacements may trigger an energy audit requirement",
        "Seattle inspectors enforce strict noise ordinances — work hours are limited to 7am-10pm on weekdays, 9am-10pm on weekends",
    ],
    "denver_co": [
        "Denver requires all electrical work to be done by a Denver-licensed electrician — Colorado state electrical license must be supplemented with Denver city license",
        "Denver has a Green Building Ordinance that requires energy compliance documentation for HVAC replacements in buildings over 25,000 sq ft",
        "Denver's online permit portal (Denver Community Planning) often has faster processing than in-person submissions",
    ],
    "atlanta_ga": [
        "Atlanta requires a separate permit from Fulton County for unincorporated areas — verify your address is within Atlanta city limits before applying",
        "Atlanta has a mandatory pre-application meeting requirement for commercial projects over $500,000 in value",
        "Atlanta requires asbestos survey documentation for demolition or renovation of buildings built before 1980",
    ],
    "las_vegas_nv": [
        "Las Vegas and Clark County have separate permit jurisdictions — strip properties use Clark County, not City of Las Vegas permits",
        "Nevada does not require a plumbing license for water heater replacements under certain conditions — verify with NSCB before applying",
        "Las Vegas inspections are typically scheduled online and have 2-3 day lead time — plan accordingly for rough-in milestone",
    ],
    "portland_or": [
        "Portland requires all residential electrical work to be done by an Oregon-licensed electrical contractor — no owner-builder electrical exemption",
        "Portland's Bureau of Development Services has notoriously long plan review times — budget 6-8 weeks for any commercial or complex residential project",
        "Portland's urban growth boundary affects permit requirements for properties near city limits — verify jurisdiction before applying",
    ],
    "nashville_tn": [
        "Nashville Metro requires contractors to register with Metro Codes before pulling permits — registration is annual and renewal is often missed",
        "Nashville has rapid growth-related permit backlogs — expedited review is available for an additional fee (typically 50% of permit fee)",
        "Tennessee does not have a state electrical license — Nashville uses its own Metro electrical license which must be obtained separately",
        "REGISTRATION RENEWAL CHECK: Before submitting any Metro Nashville permit application, verify your Metro Codes contractor registration is current at nashville.gov/departments/codes. An expired registration blocks application submission with no error message — you won't know why it failed.",
    ],
}

# Cities known to support SolarAPP+ (instant solar permit approval)
# Source: NREL SolarAPP+ adoption list (verify current status via web search)
SOLARAPP_CITIES = {
    "des_moines_ia", "aurora_co", "mesa_az", "tempe_az", "peoria_az",
    "chandler_az", "gilbert_az", "surprise_az", "goodyear_az",
    "fort_collins_co", "loveland_co", "boulder_co",
    "stockton_ca", "fresno_ca", "sacramento_ca", "san_jose_ca",
    "riverside_ca", "corona_ca", "fontana_ca", "moreno_valley_ca",
    "oxnard_ca", "ontario_ca", "rancho_cucamonga_ca",
    "las_vegas_nv", "henderson_nv", "north_las_vegas_nv",
    "portland_or", "salem_or", "gresham_or",
    "spokane_wa", "tacoma_wa", "bellevue_wa",
    "albuquerque_nm", "rio_rancho_nm",
    "el_paso_tx", "lubbock_tx", "amarillo_tx",
    "tulsa_ok", "oklahoma_city_ok",
    "wichita_ks", "topeka_ks",
    "omaha_ne", "lincoln_ne",
    "sioux_falls_sd",
    "fargo_nd",
    "minneapolis_mn", "saint_paul_mn",
    "madison_wi", "milwaukee_wi",
    "indianapolis_in", "fort_wayne_in",
    "columbus_oh", "cleveland_oh", "cincinnati_oh",
    "detroit_mi", "grand_rapids_mi",
    "louisville_ky",
    "nashville_tn", "memphis_tn",
    "birmingham_al", "montgomery_al",
    "jackson_ms",
    "little_rock_ar",
    "baton_rouge_la", "new_orleans_la",
    "charleston_sc", "columbia_sc",
    "charlotte_nc", "raleigh_nc", "greensboro_nc",
    "richmond_va", "virginia_beach_va",
    "baltimore_md",
    "philadelphia_pa", "pittsburgh_pa",
    "newark_nj", "jersey_city_nj",
    "hartford_ct", "bridgeport_ct",
    "providence_ri",
    "manchester_nh",
    "portland_me",
    "burlington_vt",
}


STATE_TYPICAL_CITIES = {
    "AL": "birmingham", "AK": "anchorage", "AZ": "phoenix", "AR": "little_rock",
    "CA": "los_angeles", "CO": "denver", "CT": "hartford", "DE": "wilmington",
    "FL": "miami", "GA": "atlanta", "HI": "honolulu", "ID": "boise",
    "IL": "chicago", "IN": "indianapolis", "IA": "des_moines", "KS": "wichita",
    "KY": "louisville", "LA": "new_orleans", "ME": "portland", "MD": "baltimore",
    "MA": "boston", "MI": "detroit", "MN": "minneapolis", "MS": "jackson",
    "MO": "kansas_city", "MT": "billings", "NE": "omaha", "NV": "las_vegas",
    "NH": "manchester", "NJ": "newark", "NM": "albuquerque", "NY": "new_york",
    "NC": "charlotte", "ND": "fargo", "OH": "columbus", "OK": "oklahoma_city",
    "OR": "portland", "PA": "philadelphia", "RI": "providence", "SC": "columbia",
    "SD": "sioux_falls", "TN": "nashville", "TX": "houston", "UT": "salt_lake_city",
    "VT": "burlington", "VA": "virginia_beach", "WA": "seattle", "WV": "charleston",
    "WI": "milwaukee", "WY": "cheyenne",
}

CHECKLIST_BASE = {
    "always": [
        "Verify your contractor license is current and valid in {state}",
        "Confirm your liability insurance is active (certificate may be required at permit office)",
        "Have the property address and legal parcel number ready",
        "Know the project value/cost — required on most permit applications",
    ]
}

CHECKLIST_TRADE = {
    "hvac": [
        "Equipment spec sheets / submittal documents for all new units",
        "Load calculation (Manual J) if replacing system >5 tons or changing capacity",
        "Refrigerant type and charge amount documentation",
        "Energy compliance form (required in many states — check {state} requirements)",
    ],
    "electrical": [
        "Panel schedule showing existing and proposed circuits",
        "Wire gauge and breaker size for each new circuit",
        "Electrical diagram or one-line drawing (required for panel upgrades)",
        "Utility disconnect location marked on plans",
    ],
    "plumbing": [
        "Isometric or riser diagram for new plumbing runs",
        "Fixture count and drain sizes",
        "Water heater BTU/gallon rating if replacing",
        "Backflow prevention documentation if required",
    ],
    "roofing": [
        "Manufacturer's product approval number (required in FL, TX coastal areas)",
        "Existing roof layer count (many jurisdictions limit to 2 layers max)",
        "Structural decking condition report if decking will be replaced",
        "Photos of existing roof condition recommended",
    ],
    "solar": [
        "Site plan showing panel layout with setback dimensions",
        "Structural engineering letter confirming roof load capacity",
        "Single-line electrical diagram (NEC Article 690 compliant)",
        "Rapid shutdown compliance documentation",
        "Utility interconnection application (separate from permit — file with utility)",
        "HOA approval letter if applicable",
    ],
    "generator": [
        "Generator spec sheet with wattage, fuel type, emissions data",
        "Transfer switch type (manual or automatic) and rating",
        "Gas line sizing calculations if natural gas",
        "Setback measurements from property lines and windows (typically 5-10 ft)",
    ],
    "fence": [
        "Survey or site plan showing fence location relative to property lines",
        "Fence height and material specifications",
        "HOA approval if in an HOA community",
        "Utility locates (call 811) completed before digging",
    ],
    "deck": [
        "Structural drawings showing joist size, span, and beam details",
        "Footing depth and diameter (frost line depth for {state})",
        "Ledger attachment method to house",
        "Guard rail height and baluster spacing specs",
    ],
}

PDF_SOURCES = {
    "nashville_tn": "https://www.nashville.gov/departments/codes/construction-and-permits/permit-fees",
    "austin_tx": "https://www.austintexas.gov/sites/default/files/files/Development_Services/Fee_Schedule.pdf",
    "indianapolis_in": "https://www.indy.gov/activity/building-permit-fees",
    "san_jose_ca": "https://www.sanjoseca.gov/your-government/departments-offices/planning-building-code-enforcement/building/fees",
}
PDF_CACHE_DIR = os.path.join(_data_dir, "pdf_cache")
_RAILWAY_ENV_PATH = "/data/.openclaw/private/railway.env"

FEE_FORMULAS = {
    "nashville_tn": {"base": 30, "per_thousand": 8.50, "min": 30, "threshold": 2858},
    "austin_tx": {"base": 200, "per_thousand": 9.00, "min": 200, "max": 25000},
    "chicago_il": {"base": 50, "per_thousand": 12.00, "min": 50},
    "houston_tx": {"base": 0, "per_thousand": 6.50, "min": 35},
    "phoenix_az": {"base": 25, "per_thousand": 3.80, "min": 25},
    "seattle_wa": {"base": 200, "per_thousand": 11.50, "min": 200},
    "denver_co": {"base": 35, "per_thousand": 8.00, "min": 35},
    "portland_or": {"base": 89, "per_thousand": 10.30, "min": 89},
    "las_vegas_nv": {"base": 40, "per_thousand": 5.50, "min": 40},
    "san_diego_ca": {"base": 75, "per_thousand": 15.00, "min": 75},
    "twin_falls_id": {"base": 30, "per_thousand": 5.00, "min": 30},
    "flagstaff_az": {"base": 45, "per_thousand": 7.50, "min": 45},
    "billings_mt": {"base": 35, "per_thousand": 5.50, "min": 35},
    "missoula_mt": {"base": 40, "per_thousand": 6.00, "min": 40},
    "burlington_vt": {"base": 50, "per_thousand": 8.00, "min": 50},
    "indianapolis_in": {"base": 50, "per_thousand": 7.00, "min": 50},
    "san_jose_ca": {"base": 200, "per_thousand": 18.00, "min": 200},
    "miami_fl": {"base": 60, "per_thousand": 9.00, "min": 60},
    "atlanta_ga": {"base": 50, "per_thousand": 7.00, "min": 50},
}

REJECTION_PATTERNS = {
    "nashville_tn": [
        {"pattern": "Expired Metro Codes contractor registration", "frequency": "very_common", "fix": "Renew at nashville.gov/departments/codes before applying — registration is annual"},
        {"pattern": "Missing load calculation for HVAC jobs", "frequency": "common", "fix": "Provide Manual J load calculation signed by licensed engineer"},
        {"pattern": "Incomplete site plan (missing north arrow, scale, setbacks)", "frequency": "common", "fix": "Include all required elements per Metro Codes checklist"},
    ],
    "chicago_il": [
        {"pattern": "Missing City of Chicago contractor license (state license not sufficient)", "frequency": "very_common", "fix": "Apply for Chicago city-level license at chicago.gov/city/en/depts/bldgs"},
        {"pattern": "No asbestos survey for pre-1980 buildings", "frequency": "common", "fix": "Hire licensed asbestos inspector before applying for any renovation permit"},
        {"pattern": "Missing BTRC (Business Tax Registration Certificate)", "frequency": "very_common", "fix": "Register at chicago.gov/city/en/depts/fin/supp_info/revenue/tax_registration"},
    ],
    "austin_tx": [
        {"pattern": "Digital signatures on structural drawings (wet stamps required)", "frequency": "very_common", "fix": "All structural drawings must have original wet stamps — PDFs not accepted"},
        {"pattern": "No appointment booked for submittal (walk-ins rarely accepted)", "frequency": "common", "fix": "Book appointment at austintexas.gov/dsd before going to counter"},
        {"pattern": "Missing ROW permit for work affecting sidewalk/driveway", "frequency": "common", "fix": "Apply for separate ROW permit through Austin Transportation Dept"},
    ],
    "los_angeles_ca": [
        {"pattern": "Missing CSLB license AND LA City Business Tax Registration Certificate", "frequency": "very_common", "fix": "Both required — get BTRC at finance.lacity.org before permit application"},
        {"pattern": "Missing CF1R energy compliance form for HVAC replacements", "frequency": "common", "fix": "Generate CF1R through a certified HERS rater before applying"},
        {"pattern": "Bureau of Engineering approval missing for projects near public streets", "frequency": "common", "fix": "Submit to Bureau of Engineering separately from Building & Safety"},
    ],
    "phoenix_az": [
        {"pattern": "Missing Phoenix City Contractor Registration (state ROC not sufficient)", "frequency": "very_common", "fix": "Register at phoenix.gov/pdd/contractor-registration before applying"},
        {"pattern": "Same-day inspection requests (24-hour notice required)", "frequency": "common", "fix": "Schedule inspections minimum 24 hours in advance through city portal"},
    ],
    "miami_fl": [
        {"pattern": "Missing Notice of Commencement (for projects over $2,500)", "frequency": "very_common", "fix": "Record NOC with Miami-Dade County Clerk before starting work"},
        {"pattern": "Missing product approval numbers for roofing/windows/doors", "frequency": "very_common", "fix": "Florida requires state product approval — verify at floridabuilding.org"},
    ],
    "dallas_tx": [
        {"pattern": "Expired annual city contractor registration", "frequency": "very_common", "fix": "Renew at dallas.gov/dsd annually — expiration causes silent rejection"},
        {"pattern": "Project value underestimation flagged by plan reviewer", "frequency": "common", "fix": "Use contractor cost estimating software for accurate value — audits can add penalty fees"},
    ],
    "seattle_wa": [
        {"pattern": "Missing Master Use Permit (MUP) in addition to building permit", "frequency": "common", "fix": "Check if project requires MUP at seattle.gov/sdci before applying"},
        {"pattern": "Noise ordinance violation during construction", "frequency": "common", "fix": "Work hours: 7am-10pm weekdays, 9am-10pm weekends — strictly enforced"},
    ],
}

def _check_solarapp(city: str, state: str) -> bool:
    key = f"{city.lower().replace(' ', '_')}_{state.lower()}"
    return key in SOLARAPP_CITIES


def _get_jurisdiction_quirks(city: str, state: str) -> list[str]:
    """Look up local jurisdiction quirks/gotchas for a city+state combo."""
    key = f"{city.lower().replace(' ', '_')}_{state.lower()}"
    return JURISDICTION_QUIRKS.get(key, [])


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

    # Roofing threshold caveat — fires for all roofing/solar jobs
    if any(w in job_lower for w in ["roof", "roofing", "shingle", "solar panel", "solar"]):
        hints.append(
            "ROOFING THRESHOLD CAVEAT: The common '100 sq ft = roofing permit' rule is NOT universal. "
            "In reality: (a) Some jurisdictions trigger at different thresholds (50, 200 sq ft, or ANY replacement). "
            "(b) Some require a permit ANY TIME structural decking is replaced regardless of area. "
            "(c) For partial roof replacement combined with solar, both roofing AND building permits are triggered. "
            "ALWAYS state the specific threshold for this jurisdiction. If unknown, state: "
            "'Threshold varies by jurisdiction — verify with local building department before starting.'"
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
            "SOLAR NOTE: Solar installations require TWO permits (NOT three — building and structural are ONE permit): "
            "1) Building/Structural Permit (roof penetrations, panel racking, structural loading — one combined permit, NOT two separate ones) "
            "2) Electrical Permit (NEC Article 690, utility interconnection, DC/AC disconnects, rapid shutdown) "
            "CRITICAL: Do NOT list 'Building Permit' and 'Structural Permit' as separate items — they are the SAME permit. "
            "Many cities have SolarAPP+ for instant approval. Utility interconnection application is SEPARATE from permits."
        )
        hints.append(
            "SOLARAPP+ CHECK — IMPORTANT: Many cities have adopted SolarAPP+ for instant residential solar permits. "
            "If this city uses SolarAPP+, state it prominently: 'This city supports SolarAPP+ — qualifying standard residential solar installations may receive INSTANT permit approval online without plan review.' "
            "Always check and state whether SolarAPP+ is available in this jurisdiction. If uncertain, say 'Check with the permit office whether SolarAPP+ is available — it can eliminate plan review delays entirely.'"
        )
        hints.append(
            "SOLAR SETBACKS & PANEL PLACEMENT — MUST INCLUDE IN RESPONSE: "
            "IRC R324 and IFC Section 1204 require panels to maintain setback clearances. "
            "Typical residential requirement: 3-foot clear access pathways along roof ridges and perimeter. "
            "Exact dimensions vary by AHJ — some require 18-inch minimum, others 36 inches. "
            "Panels CANNOT cover the full roof — fire access pathways must remain clear. "
            "Include specific setback requirements for this jurisdiction in what_to_bring and inspections."
        )
        hints.append(
            "SOLAR FIRE ACCESS PATHWAYS — MUST INCLUDE IN RESPONSE: "
            "Most jurisdictions (per IFC/IRC R324) require: "
            "(a) A 3-foot wide unobstructed pathway from eave to ridge on each roof plane with panels. "
            "(b) Hip roofs: 3-foot clear area around the perimeter AND a 3-foot pathway to the ridge. "
            "(c) Structural loading: confirm roof can support panel dead load (typically 3-4 lb/sq ft additional). "
            "These are common inspection failure points — include in common_mistakes AND inspections."
        )
        hints.append(
            "ZONING / HOA / HISTORIC DISTRICT FLAG — MANDATORY FOR SOLAR: "
            "Always flag these potential blockers in your response: "
            "1) HOA RESTRICTIONS: HOA may restrict solar panel placement, visibility, or type — check CC&Rs. "
            "Note: Most states have solar access laws limiting HOA ability to ban solar outright, but placement restrictions are often still permitted. "
            "2) HISTORIC DISTRICT: If in a Historic District, Historic Preservation Commission approval may be required BEFORE permits are issued (adds 30-90 days). "
            "3) ZONING OVERLAY: Scenic corridors, shoreline districts, or other overlays may restrict visible rooftop equipment. "
            "Populate the 'zoning_hoa_flag' field in your JSON response with specifics for this location."
        )

    # HOA/zoning flag for roofing jobs
    if any(w in job_lower for w in ["roof", "roofing", "shingle"]) and "solar" not in job_lower:
        hints.append(
            "ZONING / HOA FLAG — ROOFING: "
            "1) HOA: Many HOAs restrict shingle color, material type, or require approval before re-roofing. "
            "2) HISTORIC DISTRICT: Homes in historic districts may need Historic Preservation Commission approval for visible roofing changes. "
            "3) WIND/HAIL ZONE: Some jurisdictions require impact-resistant (Class 4) shingles or specific wind ratings per local IRC Chapter 9 amendments. "
            "Include an HOA/historic district check note in common_mistakes or pro_tips."
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


def _safe_format_checklist_item(item: str, city: str, state: str) -> str:
    try:
        return str(item).format(city=city, state=state)
    except Exception:
        return str(item)


def _fuzzy_match_key(value: str, options: list[str]) -> str:
    try:
        value_lower = (value or '').lower()
        best = ''
        best_score = 0
        for option in options:
            score = 0
            for token in re.split(r'[^a-z0-9]+', option.lower()):
                if token and token in value_lower:
                    score += len(token)
            if option in value_lower:
                score += len(option) * 2
            if score > best_score:
                best_score = score
                best = option
        return best
    except Exception:
        return ''


def find_similar_city(city: str, state: str) -> tuple[str | None, dict | None]:
    try:
        _load_knowledge()
        cities = _CITIES_KB.get('cities', {})
        state_upper = state.upper()
        state_cities = [(k, v) for k, v in cities.items() if v.get('state', '').upper() == state_upper]
        if not state_cities:
            return None, None
        typical = STATE_TYPICAL_CITIES.get(state_upper, '')
        for key, data in state_cities:
            if typical and typical in key:
                return key, data
        return state_cities[0]
    except Exception as e:
        print(f"[similar-city] Failed to find similar city: {e}")
        return None, None


def ensure_pdf_cache_dir():
    try:
        os.makedirs(PDF_CACHE_DIR, exist_ok=True)
    except Exception as e:
        print(f"[pdf_kb] Failed to create cache dir: {e}")


def _get_env_value(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value not in (None, ""):
        return value
    try:
        with open(_RAILWAY_ENV_PATH) as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, raw_value = line.partition("=")
                if key.strip() != name:
                    continue
                value = raw_value.strip().strip('"').strip("'")
                if value:
                    os.environ[name] = value
                    return value
    except Exception:
        pass
    return default


def _pdf_cache_key(city: str, state: str) -> str:
    return f"{city.lower().replace(' ', '_')}_{state.lower()}"


def _meaningful_pdf_text_len(text: str) -> int:
    if not text:
        return 0
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    cleaned = re.sub(r"[^A-Za-z0-9$%.,:;()/#\- ]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return len(cleaned)


def get_cached_pdf_text(city: str, state: str, source_url: str = "") -> str:
    try:
        ensure_pdf_cache_dir()
        key = _pdf_cache_key(city, state)
        cache_file = os.path.join(PDF_CACHE_DIR, f"{key}.txt")
        meta_file = os.path.join(PDF_CACHE_DIR, f"{key}.meta.json")
        if os.path.exists(cache_file) and os.path.exists(meta_file):
            with open(meta_file) as f:
                meta = json.load(f)
            cached_source = str(meta.get('source_url') or '')
            if source_url and cached_source and cached_source != source_url:
                return ''
            age_days = (time.time() - meta.get('cached_at', 0)) / 86400
            if age_days < 30:
                with open(cache_file) as f:
                    return f.read()
    except Exception as e:
        print(f"[pdf_kb] Cache read failed: {e}")
    return ''


def cache_pdf_text(city: str, state: str, text: str, source_url: str, extraction_method: str = ""):
    try:
        ensure_pdf_cache_dir()
        key = _pdf_cache_key(city, state)
        with open(os.path.join(PDF_CACHE_DIR, f"{key}.txt"), 'w') as f:
            f.write(text or '')
        with open(os.path.join(PDF_CACHE_DIR, f"{key}.meta.json"), 'w') as f:
            json.dump({'cached_at': time.time(), 'source_url': source_url, 'extraction_method': extraction_method}, f)
    except Exception as e:
        print(f"[pdf_kb] Cache write failed: {e}")


def _extract_pdf_text_with_pdfplumber(pdf_bytes: bytes, url: str = "") -> str:
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = []
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                if page_text.strip():
                    pages.append(page_text)
        text = "\n\n".join(pages).strip()
        if text:
            print(f"[pdf_kb] pdfplumber extracted {len(text)} chars from {url or 'pdf bytes'}")
        return text
    except Exception as e:
        print(f"[pdf_kb] pdfplumber extraction failed for {url or 'pdf bytes'}: {e}")
    return ''


def _extract_pdf_text_with_firecrawl(url: str) -> str:
    api_key = _get_env_value("FIRECRAWL_API_KEY", "")
    if not api_key or not url:
        return ""
    try:
        resp = requests.post(
            "https://api.firecrawl.dev/v2/scrape",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"url": url, "formats": ["markdown"], "parsers": [{"type": "pdf", "mode": "auto"}]},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json() or {}
        payload = data.get("data") or {}
        markdown = payload.get("markdown") or data.get("markdown") or ""
        return (markdown or "").strip()
    except Exception as e:
        print(f"[pdf_kb] Firecrawl PDF OCR failed for {url}: {e}")
        return ""


def extract_pdf_text(url: str, city: str, state: str) -> str:
    try:
        if not is_pdf_url(url):
            return ''
        cached = get_cached_pdf_text(city, state, source_url=url)
        if cached:
            print(f"[pdf_kb] Cache hit for {city}, {state}: {len(cached)} chars")
            return cached

        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (PermitAssist PDF Bot)"}, timeout=45)
        resp.raise_for_status()
        pdf_bytes = resp.content or b''
        if not pdf_bytes:
            return ''

        pdfplumber_text = _extract_pdf_text_with_pdfplumber(pdf_bytes, url=url)
        if _meaningful_pdf_text_len(pdfplumber_text) >= 100:
            cache_pdf_text(city, state, pdfplumber_text, url, extraction_method="pdfplumber")
            print(f"[pdf_kb] Cached text PDF for {city}, {state}: {len(pdfplumber_text)} chars")
            return pdfplumber_text

        print(f"[pdf_kb] Low-text PDF detected for {url}, trying Firecrawl OCR")
        firecrawl_text = _extract_pdf_text_with_firecrawl(url)
        if _meaningful_pdf_text_len(firecrawl_text) >= 100:
            cache_pdf_text(city, state, firecrawl_text, url, extraction_method="firecrawl_pdf_ocr")
            print(f"[pdf_kb] Cached OCR PDF for {city}, {state}: {len(firecrawl_text)} chars")
            return firecrawl_text

        if pdfplumber_text:
            cache_pdf_text(city, state, pdfplumber_text, url, extraction_method="pdfplumber_low_text")
            return pdfplumber_text
    except Exception as e:
        print(f"[pdf_kb] Unified PDF extraction failed for {url}: {e}")
    return ''


def fetch_and_cache_pdf(url: str, city: str, state: str) -> str:
    return extract_pdf_text(url, city, state)


def calculate_permit_ready_score(context: str, structured: dict) -> tuple[int, str, list[str]]:
    try:
        score = 0
        reasons = []
        phone = str(structured.get('phone', '') or '')
        digits = re.sub(r'\D', '', phone)
        if len(digits) >= 10:
            score += 20
        else:
            reasons.append('permit office phone not found — call to verify')
        portal = str(structured.get('portal_url', '') or '')
        if portal.startswith('http'):
            score += 25
        else:
            reasons.append('online portal not confirmed — may need to apply in person')
        fees = structured.get('fees', []) or []
        if fees:
            score += 20
        else:
            reasons.append('exact fees not found — request fee schedule at permit office')
        address = str(structured.get('address', '') or '')
        if len(address) > 10:
            score += 15
        else:
            reasons.append('office address not confirmed')
        if len(context or '') > 1000:
            score += 10
        elif len(context or '') > 500:
            score += 5
        if '.gov' in (context or ''):
            score += 10
        if score >= 85:
            label = 'Ready to apply'
        elif score >= 65:
            label = 'Mostly ready — verify fees'
        elif score >= 45:
            label = 'Partial data — call permit office first'
        else:
            label = 'Limited data — in-person visit recommended'
        return score, label, reasons
    except Exception as e:
        print(f"[ready-score] Failed: {e}")
        return 0, 'Limited data — in-person visit recommended', ['unable to score permit readiness']


def calculate_exact_fee(job_type: str, city: str, state: str, job_value: float) -> dict:
    try:
        key = f"{city.lower().replace(' ', '_')}_{state.lower()}"
        formula = FEE_FORMULAS.get(key)
        if not formula:
            return {'fee': None, 'formula': None, 'confidence': 'none', 'note': f'Exact fee formula not available for {city}, {state}. Use fee range as estimate.'}
        base = float(formula.get('base', 0) or 0)
        rate = float(formula.get('per_thousand', 0) or 0)
        threshold = float(formula.get('threshold', 0) or 0)
        min_fee = float(formula.get('min', 0) or 0)
        max_fee = formula.get('max', None)
        if threshold and job_value <= threshold:
            calculated = base
        else:
            calculated = base + ((job_value / 1000) * rate)
        calculated = max(calculated, min_fee)
        if max_fee is not None:
            calculated = min(calculated, float(max_fee))
        formula_str = f"${base:g} base + ${rate:.2f}/thousand of job value"
        if threshold:
            formula_str += f" (flat ${base:g} for jobs under ${threshold:,.0f})"
        return {'fee': round(calculated, 2), 'formula': formula_str, 'confidence': 'high', 'note': 'Verify current fee schedule — rates may have changed'}
    except Exception as e:
        print(f"[fee-calc] Failed: {e}")
        return {'fee': None, 'formula': None, 'confidence': 'none', 'note': f'Unable to calculate exact fee for {city}, {state}.'}


def get_rejection_patterns(city: str, state: str, job_type: str) -> list[dict]:
    try:
        key = f"{city.lower().replace(' ', '_')}_{state.lower()}"
        patterns = REJECTION_PATTERNS.get(key, [])
        if not patterns:
            return []
        job_lower = (job_type or '').lower()
        relevant = []
        for p in patterns:
            pattern_text = str(p.get('pattern', '')).lower()
            if p.get('frequency') == 'very_common':
                relevant.append(p)
            elif any(word and word in pattern_text for word in re.split(r'[^a-z0-9]+', job_lower)):
                relevant.append(p)
        return relevant[:4]
    except Exception as e:
        print(f"[rejections] Failed: {e}")
        return []


def generate_permit_checklist(job_type: str, city: str, state: str, result: dict) -> list[str]:
    try:
        matched = _fuzzy_match_key(job_type, list(CHECKLIST_TRADE.keys()))
        items = list(CHECKLIST_BASE.get('always', []))
        if matched:
            items.extend(CHECKLIST_TRADE.get(matched, []))
        license_required = result.get('license_required') or ''
        applying_office = result.get('applying_office') or ''
        if license_required:
            items.append(f"Confirm the required license is active and matches the application: {license_required}")
        if applying_office:
            items.append(f"Bring 2 copies of all documents to {applying_office}")
        deduped = []
        seen = set()
        for item in items:
            formatted = _safe_format_checklist_item(item, city, state).strip()
            if formatted and formatted not in seen:
                seen.add(formatted)
                deduped.append(formatted)
        return deduped
    except Exception as e:
        print(f"[checklist] Failed: {e}")
        return []


def check_for_changes(city: str, state: str, new_content: str) -> dict:
    try:
        cached = get_search_cache(city, state)
        if not cached:
            return {'changed': False, 'change_summary': ''}
        old_hash = cached.get('content_hash', '')
        new_hash = content_hash(new_content)
        if old_hash and old_hash != new_hash:
            old_text = cached.get('raw_text', '') or ''
            changes = []
            old_fees = set(re.findall(r'\$[\d,]+(?:\.\d{2})?', old_text))
            new_fees = set(re.findall(r'\$[\d,]+(?:\.\d{2})?', new_content or ''))
            if old_fees != new_fees:
                added = new_fees - old_fees
                removed = old_fees - new_fees
                if added:
                    changes.append(f"New fee amounts detected: {', '.join(list(added)[:3])}")
                if removed:
                    changes.append(f"Fee amounts no longer found: {', '.join(list(removed)[:3])}")
            old_phones = set(re.findall(r'\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}', old_text))
            new_phones = set(re.findall(r'\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}', new_content or ''))
            if old_phones != new_phones:
                changes.append('Phone number may have changed — verify before calling')
            summary = ' | '.join(changes) if changes else 'Page content updated — review for changes'
            return {'changed': True, 'change_summary': summary}
        return {'changed': False, 'change_summary': ''}
    except Exception as e:
        print(f"[change-detect] Failed: {e}")
        return {'changed': False, 'change_summary': ''}


# ─── Cache ────────────────────────────────────────────────────────────────────

def init_search_cache_db():
    """Initialize search_cache and url_patterns tables."""
    os.makedirs(os.path.dirname(CACHE_DB), exist_ok=True)
    conn = sqlite3.connect(CACHE_DB)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS search_cache (
            cache_key TEXT PRIMARY KEY,
            city TEXT,
            state TEXT,
            payload_json TEXT,
            content_hash TEXT,
            created_at TEXT
        )
    """)
    cols = {row[1] for row in c.execute("PRAGMA table_info(search_cache)").fetchall()}
    if "content_hash" not in cols:
        c.execute("ALTER TABLE search_cache ADD COLUMN content_hash TEXT")
    if "city" not in cols:
        c.execute("ALTER TABLE search_cache ADD COLUMN city TEXT")
    if "state" not in cols:
        c.execute("ALTER TABLE search_cache ADD COLUMN state TEXT")
    if "payload_json" not in cols:
        c.execute("ALTER TABLE search_cache ADD COLUMN payload_json TEXT")
    if "created_at" not in cols:
        c.execute("ALTER TABLE search_cache ADD COLUMN created_at TEXT")
    c.execute("""
        CREATE TABLE IF NOT EXISTS url_patterns (
            domain TEXT PRIMARY KEY,
            success_count INTEGER DEFAULT 0,
            fail_count INTEGER DEFAULT 0,
            avg_content_len REAL DEFAULT 0,
            last_seen INTEGER
        )
    """)
    conn.commit()
    conn.close()


def content_hash(text: str) -> str:
    return hashlib.md5((text or "").encode()).hexdigest()[:12]


def _get_domain_success_bonus(url: str) -> int:
    try:
        domain = urlparse(url).netloc
        if not domain:
            return 0
        conn = sqlite3.connect(CACHE_DB)
        row = conn.execute("SELECT success_count FROM url_patterns WHERE domain = ?", (domain,)).fetchone()
        conn.close()
        if row and (row[0] or 0) > 5:
            return 2
    except Exception:
        pass
    return 0


def record_url_success(url: str, content_len: int):
    """Track which domains reliably produce good content."""
    try:
        domain = urlparse(url).netloc
        if not domain:
            return
        conn = sqlite3.connect(CACHE_DB)
        c = conn.cursor()
        now = int(time.time())
        c.execute("""
            INSERT INTO url_patterns (domain, success_count, avg_content_len, last_seen)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                success_count = success_count + 1,
                avg_content_len = (avg_content_len * success_count + ?) / (success_count + 1),
                last_seen = ?
        """, (domain, content_len, now, content_len, now))
        conn.commit()
        conn.close()
    except Exception:
        pass


def init_cache():
    os.makedirs(os.path.dirname(CACHE_DB), exist_ok=True)
    ensure_pdf_cache_dir()
    init_search_cache_db()
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS permit_cache (
            cache_key    TEXT PRIMARY KEY,
            job_type     TEXT,
            job_category TEXT,
            city         TEXT,
            state        TEXT,
            zip_code     TEXT,
            result_json  TEXT,
            created_at   TEXT,
            hits         INTEGER DEFAULT 0
        )
    """)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(permit_cache)").fetchall()}
    if "job_category" not in cols:
        conn.execute("ALTER TABLE permit_cache ADD COLUMN job_category TEXT")
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

def cache_key(job_type: str, city: str, state: str, job_category: str = "residential") -> str:
    raw = f"{job_type.lower().strip()}|{city.lower().strip()}|{state.upper().strip()}|{(job_category or 'residential').lower().strip()}"
    return hashlib.md5(raw.encode()).hexdigest()

def _smart_ttl(hits: int, confidence: str, fee_unverified: bool) -> int:
    """Tiered TTL based on popularity and data quality.
    - Popular + high confidence = 60 days (stable, well-tested data)
    - Unverified fee or low confidence = 10 days (refresh sooner)
    - Default = 30 days
    """
    if fee_unverified or confidence == "low":
        return 10
    if hits >= 10 and confidence == "high":
        return 60
    if hits >= 3 and confidence in ("high", "medium"):
        return 45
    return 30

def get_cached(key: str, max_age_days: int = None, _refresh_callback=None):
    """Smart cache read with tiered TTL and stale-while-revalidate.
    - max_age_days: override TTL (used internally; pass None for smart TTL)
    - _refresh_callback: callable(key) to trigger background refresh when stale
    """
    try:
        conn = sqlite3.connect(CACHE_DB)
        row = conn.execute(
            "SELECT result_json, created_at, hits FROM permit_cache WHERE cache_key = ?", [key]
        ).fetchone()
        if row:
            result = json.loads(row[0])
            created = datetime.fromisoformat(row[1])
            hits = row[2] or 0
            confidence = result.get("confidence", "medium")
            fee_unverified = bool(result.get("_fee_unverified"))
            ttl = max_age_days if max_age_days is not None else _smart_ttl(hits, confidence, fee_unverified)
            age = datetime.now() - created
            if age < timedelta(days=ttl):
                conn.execute("UPDATE permit_cache SET hits = hits + 1 WHERE cache_key = ?", [key])
                conn.commit()
                conn.close()
                # Stale-while-revalidate: if past 75% of TTL, trigger background refresh
                if _refresh_callback and age > timedelta(days=ttl * 0.75):
                    print(f"[cache] Stale-while-revalidate triggered for key {key[:8]}… (age={age.days}d, ttl={ttl}d)")
                    import threading
                    threading.Thread(target=_refresh_callback, args=(key,), daemon=True).start()
                return result
        conn.close()
    except Exception as e:
        print(f"[cache] Read error (non-fatal): {e}")
    return None

def save_cache(key: str, job_type: str, job_category: str, city: str, state: str, zip_code: str, result: dict):
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute("""
            INSERT OR REPLACE INTO permit_cache
            (cache_key, job_type, job_category, city, state, zip_code, result_json, created_at, hits)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, [key, job_type, job_category, city, state, zip_code, json.dumps(result), datetime.now().isoformat()])
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

_PHONE_RE = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}")
_FEE_RE = re.compile(r"\$[\d,]+(?:\.\d{2})?")
_HOURS_RE = re.compile(
    r"((?:mon|monday|tue|tues|tuesday|wed|wednesday|thu|thurs|thursday|fri|friday|sat|saturday|sun|sunday)[^\n]{0,80}?(?:\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)))",
    re.I,
)
_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.#'\- ]+\s(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|Highway|Hwy|Parkway|Pkwy|Circle|Cir|Place|Pl|Suite|Ste)\b[^\n,;]{0,80}",
    re.I,
)
_PORTAL_HINTS = ("permit", "apply", "portal", "eplan", "accela", "citizenserve", "viewpoint", "mylocalgov", "opengov", "tylertech")
_URL_RE = re.compile(r"https?://[^\s)\]>\"']+")


def _unique_keep_order(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        if not value:
            continue
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out



def normalize_jurisdiction(city: str, state: str) -> tuple[str, str, str]:
    """Returns (search_city, search_state, jurisdiction_note)."""
    city_lower = (city or "").lower()
    note = ""

    for kw in UNINCORPORATED_KEYWORDS:
        if kw in city_lower:
            clean = city_lower.replace(kw, "").replace("county", "").strip(" ,-_")
            clean = re.sub(r'\s+', ' ', clean).title()
            note = f"Unincorporated area - searching {clean} County"
            return f"{clean} County", state, note

    if state.upper() == "AK" and "borough" not in city_lower and "municipality" not in city_lower:
        note = "Alaska uses borough authority for permits"
    elif state.upper() == "LA":
        note = "Louisiana uses parish authority - may be parish permit office"
    elif state.upper() == "VA":
        note = JURISDICTION_PATTERNS["VA"].get("note", "")

    return city, state, note



def _find_city_kb_entry(city: str, state: str) -> dict | None:
    _load_knowledge()
    cities = _CITIES_KB.get("cities", {})
    city_lower = city.lower().strip()
    state_upper = state.upper().strip()
    for _, data in cities.items():
        if data.get("city", "").lower().strip() == city_lower and data.get("state", "").upper().strip() == state_upper:
            return data
    for key, data in cities.items():
        if city_lower in key.lower() and data.get("state", "").upper().strip() == state_upper:
            return data
    return None



def scrape_url(url: str) -> tuple[str, str]:
    if not url or not str(url).startswith(("http://", "https://")):
        return "", ""
    try:
        resp = requests.get(
            url,
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0 (PermitAssist Search Bot)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "text/html" not in ctype and "application/xhtml" not in ctype:
            return resp.url, ""
        return resp.url, resp.text
    except Exception as e:
        print(f"[search] Scrape failed for {url}: {e}")
        return url, ""



def _search_cache_key(city: str, state: str) -> str:
    return f"{city.lower().strip()}|{state.upper().strip()}"



def delete_search_cache(city: str, state: str):
    try:
        init_search_cache_db()
        conn = sqlite3.connect(CACHE_DB)
        conn.execute("DELETE FROM search_cache WHERE cache_key = ?", [_search_cache_key(city, state)])
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[search] Cache delete failed (non-fatal): {e}")



def _page_headers_signature(url: str) -> tuple[str, str]:
    try:
        resp = requests.head(url, allow_redirects=True, timeout=5, headers={"User-Agent": "Mozilla/5.0 (PermitAssist Search Bot)"})
        return resp.headers.get("ETag", "") or "", resp.headers.get("Last-Modified", "") or ""
    except Exception:
        return "", ""



def get_search_cache(city: str, state: str, city_match_level: str = "city", ttl_days: int = 30) -> dict | None:
    try:
        init_search_cache_db()
        conn = sqlite3.connect(CACHE_DB)
        row = conn.execute(
            "SELECT payload_json, content_hash, created_at FROM search_cache WHERE cache_key = ?",
            [_search_cache_key(city, state)],
        ).fetchone()
        conn.close()
        if not row:
            return None
        created_at = datetime.fromisoformat(row[2])
        if datetime.now() - created_at > timedelta(days=ttl_days):
            delete_search_cache(city, state)
            return None
        payload = json.loads(row[0])
        payload["content_hash"] = row[1] or payload.get("content_hash", "")
        if city_match_level and payload.get("city_match_level") and payload.get("city_match_level") != city_match_level:
            return None
        first_url = ((payload.get("results") or [{}])[0]).get("url", "")
        if first_url:
            etag, last_mod = _page_headers_signature(first_url)
            cached_meta = payload.get("cache_meta") or {}
            if (etag and cached_meta.get("etag") and etag != cached_meta.get("etag")) or (last_mod and cached_meta.get("last_modified") and last_mod != cached_meta.get("last_modified")):
                print("[search] Cache hit but page may have changed - serving cached data")
        return payload
    except Exception as e:
        print(f"[search] Cache read failed (non-fatal): {e}")
        return None



def set_search_cache(city: str, state: str, payload: dict):
    try:
        init_search_cache_db()
        first_url = ((payload.get("results") or [{}])[0]).get("url", "")
        etag, last_mod = _page_headers_signature(first_url) if first_url else ("", "")
        payload = dict(payload)
        payload["cache_meta"] = {
            "etag": etag,
            "last_modified": last_mod,
        }
        raw_text = str(payload.get("raw_text") or ((payload.get("structured") or {}).get("raw_text") or ''))[:500]
        payload["raw_text"] = raw_text
        payload["content_hash"] = content_hash(raw_text or json.dumps(payload.get("results") or []))
        serialized = json.dumps(payload)
        conn = sqlite3.connect(CACHE_DB)
        conn.execute(
            """
            INSERT OR REPLACE INTO search_cache
            (cache_key, city, state, payload_json, content_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                _search_cache_key(city, state),
                city,
                state,
                serialized,
                payload["content_hash"],
                datetime.now().isoformat(),
            ],
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[search] Cache write failed (non-fatal): {e}")



def _location_match_score(url: str, title: str = "", content: str = "", city: str = "", state: str = "") -> int:
    if not city or not state:
        return 0
    haystack = " ".join([url or "", title or "", content or ""]).lower()
    desired_city = re.sub(r"\s+", " ", (city or "").strip().lower())
    desired_state = (state or "").strip().upper()
    desired_state_name = STATE_NAME_MAP.get(desired_state, "")
    desired_city_slug = desired_city.replace(" ", "-")
    desired_city_compact = desired_city.replace(" ", "")

    score = 0
    city_match = False
    if desired_city and any(token in haystack for token in [desired_city, desired_city_slug, desired_city_compact]):
        score += 10
        city_match = True
    elif desired_city and len(desired_city) >= 4:
        score -= 10

    state_match = False
    if desired_state_name and desired_state_name in haystack:
        score += 6
        state_match = True
    if re.search(rf"(^|[^a-z]){re.escape(desired_state.lower())}([^a-z]|$)", haystack):
        score += 4
        state_match = True
    if not state_match:
        score -= 8

    for abbr, name in STATE_NAME_MAP.items():
        if abbr == desired_state:
            continue
        abbr_hit = re.search(rf"(^|[^a-z]){re.escape(abbr.lower())}([^a-z]|$)", haystack)
        if name in haystack or abbr_hit:
            if not state_match:
                score -= 14
            elif city_match:
                score -= 6
            break

    if city_match and state_match:
        score += 4
    return score



def _score_search_url(url: str, title: str = "", content: str = "", city: str = "", state: str = "") -> int:
    if not url:
        return -999
    parsed = urlparse(url)
    domain = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    lowered = url.lower()
    score = 0
    if domain.endswith(".gov"):
        score += 10
    if domain.endswith(".us"):
        score += 8
    if any(term in lowered for term in POSITIVE_URL_HINTS):
        score += 5
    if any(term in domain for term in NEGATIVE_DOMAIN_HINTS):
        score -= 5
    if any(term in domain for term in SOCIAL_DOMAIN_HINTS):
        score -= 3
    if path.endswith(".pdf") and any(term in path for term in PDF_URL_HINTS):
        score += 6
    score += _get_domain_success_bonus(url)
    score += _location_match_score(url, title=title, content=content, city=city, state=state)
    return score



def _rank_search_results(results: list[dict], limit: int = 4, city: str = "", state: str = "") -> list[dict]:
    ranked = []
    for item in results or []:
        url = item.get("url", "")
        score = _score_search_url(url, title=item.get("title", ""), content=item.get("content", ""), city=city, state=state)
        print(f"[search] URL ranking: score={score} url={url}")
        ranked.append({**item, "score": score})
    ranked.sort(key=lambda item: (item.get("score", -999), item.get("url", "")), reverse=True)
    deduped = []
    seen = set()
    for item in ranked:
        url = item.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append({k: v for k, v in item.items() if k != "score"})
        if len(deduped) >= limit:
            break
    return deduped



def serper_search(query: str, num: int = 5, city: str = "", state: str = "") -> list[dict]:
    if not SERPER_API_KEY:
        return []
    try:
        resp = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": num, "gl": "us"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for r in data.get("organic", []):
            url = r.get("link", "")
            if not url:
                continue
            results.append({
                "title": r.get("title", ""),
                "url": url,
                "content": clean_summary_text(r.get("snippet", ""), max_len=500),
            })
        trimmed = _rank_search_results(results, limit=min(num, 4), city=city, state=state)
        print(f"[search] Layer 1: serper found {len(trimmed)} urls")
        return trimmed
    except Exception as e:
        print(f"[search] Serper search failed (non-fatal): {e}")
        return []



def brave_search(query: str, num: int = 5, max_results: int | None = None, city: str = "", state: str = "") -> list[dict]:
    limit = max_results or num
    if not BRAVE_SEARCH_API_KEY:
        return []
    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": limit, "text_decorations": False, "search_lang": "en"},
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for r in data.get("web", {}).get("results", []):
            url = r.get("url", "")
            if not url:
                continue
            results.append({
                "title": r.get("title", ""),
                "url": url,
                "content": clean_summary_text(r.get("description", ""), max_len=500),
            })
        trimmed = _rank_search_results(results, limit=min(limit, 4), city=city, state=state)
        print(f"[search] Layer 1: brave found {len(trimmed)} urls")
        return trimmed
    except Exception as e:
        print(f"[search] Brave search failed (non-fatal): {e}")
        return []



_SPA_PORTAL_HINTS = (
    "accela.com",
    "aca-prod",
    "citizenserve",
    "viewpoint",
    "tylertech",
    "opengov",
    "mynewport",
    "energovweb",
)


def _is_spa_portal_url(url: str) -> bool:
    if not url:
        return False
    lowered = str(url).lower()
    return any(hint in lowered for hint in _SPA_PORTAL_HINTS)



def extract_tables_from_html(html: str) -> str:
    """Extract HTML tables as markdown. Fee schedules are almost always in tables."""
    try:
        soup = BeautifulSoup(html, "lxml")
        tables_md = []
        for table in soup.find_all("table")[:3]:
            rows = []
            for tr in table.find_all("tr"):
                cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if cells and any(c for c in cells):
                    rows.append(" | ".join(cells))
            if rows:
                tables_md.append("\n".join(rows))
        return "\n\n".join(tables_md) if tables_md else ""
    except Exception:
        return ""



def jina_fetch(url: str) -> tuple[str, int, bool]:
    if not url or not str(url).startswith(("http://", "https://")):
        return "", 0, False
    if _is_spa_portal_url(url):
        fire_text = firecrawl_fetch(url)
        if fire_text:
            print(f"[search] Layer 2: firecrawl extracted {len(fire_text)} chars from {urlparse(url).netloc or url} (spa portal)")
            return fire_text, 200, True
        print(f"[search] SPA portal detected for {url} but Firecrawl unavailable or empty, skipping Jina")
        return "", 0, True
    try:
        target = f"https://r.jina.ai/{url}"
        resp = requests.get(
            target,
            headers={"Accept": "text/plain", "User-Agent": "Mozilla/5.0 (PermitAssist Search Bot)"},
            timeout=20,
        )
        status = resp.status_code
        if status == 429:
            time.sleep(2)
            resp = requests.get(
                target,
                headers={"Accept": "text/plain", "User-Agent": "Mozilla/5.0 (PermitAssist Search Bot)"},
                timeout=20,
            )
            status = resp.status_code
            if status == 429:
                print(f"[search] Jina 429 - falling back to direct fetch for {url}")
                direct = requests.get(url, headers=DIRECT_FETCH_HEADERS, timeout=20)
                status = direct.status_code
                if status == 404:
                    return "", status, False
                direct.raise_for_status()
                soup = BeautifulSoup(direct.text or "", "html.parser")
                for tag in soup(["script", "style", "nav", "footer"]):
                    tag.decompose()
                text = soup.get_text("\n", strip=True)
                tables = extract_tables_from_html(direct.text or "")
                if tables:
                    text = f"{text}\n\n{tables}".strip()
                print(f"[search] Layer 2: direct fetch extracted {len(text)} chars from {urlparse(url).netloc or url}")
                return text, status, False
        if status == 404:
            return "", status, False
        resp.raise_for_status()
        text = resp.text or ""
        is_spa = "please enable javascript" in text.lower()
        print(f"[search] Layer 2: jina extracted {len(text)} chars from {urlparse(url).netloc or url}")
        return text, status, is_spa
    except Exception as e:
        print(f"[search] Jina fetch failed for {url}: {e}")
        return "", 0, False



def firecrawl_fetch(url: str) -> str:
    api_key = _get_env_value("FIRECRAWL_API_KEY", "")
    if not api_key or not url:
        return ""
    try:
        resp = requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"url": url, "formats": ["markdown"]},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        payload = data.get("data") or {}
        markdown = payload.get("markdown") or data.get("markdown") or ""
        return markdown or ""
    except Exception as e:
        print(f"[search] Firecrawl failed for {url}: {e}")
        return ""



def _preserve_text_tables(text: str) -> str:
    if not text:
        return ""
    lines = [line.rstrip() for line in str(text).splitlines()]
    kept = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        kept.append(stripped if "|" in stripped else re.sub(r"\s+", " ", stripped))
    return "\n".join(kept).strip()



def _clean_address_text(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', str(text))
    cleaned = re.sub(r'\]\(|\)\[|\]', ' ', cleaned)
    cleaned = re.sub(r'\(https?://[^\s)]*', ' ', cleaned)
    cleaned = re.sub(r'https?://[^\s)\]]+', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned.strip(" ,;)")



def _clean_address_candidate(address: str) -> str:
    cleaned = _clean_address_text(address)
    match = _ADDRESS_RE.search(cleaned)
    if match:
        cleaned = match.group(0)
    cleaned = re.sub(r'^\d{1,3}\s+(\d{3,6}\s+[A-Za-z])', r'\1', cleaned)
    cleaned = re.sub(r'\b(To apply for a permit|Permit Number|Legal Description|Parcel ID|Owner.?s Information|Contractor.?s Information)\b.*$', '', cleaned, flags=re.I)
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned.strip(" ,;)")



def _score_address_candidate(address: str, url: str = "", context: str = "") -> int:
    if not address:
        return -999
    lowered = address.lower()
    context_l = (context or "").lower()
    score = 0
    if re.search(r'\b\d{1,6}\s+', address):
        score += 3
    if re.search(r'\b(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|way|highway|hwy|parkway|pkwy|circle|cir|place|pl|suite|ste)\b', lowered):
        score += 3
    if re.search(r'\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b', address):
        score += 4
    if re.search(r'\b(?:city hall|building department|planning|zoning|permit|inspection|office|located at|visit us)\b', context_l):
        score += 3
    if any(term in lowered or term in context_l for term in ("chapter", "expand", "permit number", "legal description", "parcel id", "owner", "contractor", "rights of way", "regulations of", "kiddie pools", "october 31")):
        score -= 10
    if re.search(r'\b\d{1,2}\s+st\b', lowered):
        score -= 6
    if len(address) > 70:
        score -= 5
    if url:
        host = urlparse(url).netloc.lower()
        if any(term in host for term in ("municode", "countyoffice", "permits.com", "permitflow", "homeyou", "greenlancer", "solartechonline", "indoortemp", "momentumacpro")):
            score -= 3
    return score



def extract_best_phone(text: str) -> str:
    """Extract the phone number most contextually relevant to the permit office."""
    phone_pattern = re.compile(r'\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}')
    best_phone = ""
    best_score = -1
    for match in phone_pattern.finditer(text or ""):
        start = max(0, match.start() - 200)
        end = min(len(text), match.end() + 200)
        context = text[start:end].lower()
        score = 0
        for kw in PHONE_CONTEXT_KEYWORDS:
            if kw in context:
                score += 1
        local_start = max(0, match.start() - start - 30)
        local_end = min(len(context), match.start() - start + 30)
        if "fax" in context[local_start:local_end].lower():
            score -= 5
        if score > best_score:
            best_score = score
            best_phone = match.group()
    return best_phone



def score_field_confidence(value: str, source_url: str, field: str) -> str:
    """Return high, medium, low, or none confidence for a field."""
    if not value:
        return "none"
    is_gov = ".gov" in source_url or ".us" in source_url
    if field == "phone":
        return "high" if is_gov else "medium"
    if field == "portal_url":
        lower = str(value).lower()
        if any(kw in lower for kw in ["accela", "citizenserve", "viewpoint", "opengov", "tylertech"]):
            return "high"
        return "high" if is_gov else "medium"
    if field == "fees":
        return "high" if (is_gov and "$" in str(value)) else "medium"
    if field == "address":
        return "high" if is_gov else "low"
    return "medium"



def find_followup_links(url: str, content: str) -> list[str]:
    """Find high-value sub-links from a scraped page worth following."""
    links = []
    for match in re.finditer(r'\[([^\]]+)\]\((https?://[^)]+)\)', content or ""):
        text_label, href = match.group(1).lower(), match.group(2)
        if any(kw in text_label for kw in FEE_LINK_KEYWORDS):
            links.append(href)
    return _unique_keep_order(links)[:2]



def check_page_freshness(url: str) -> str:
    """Return freshness label based on Last-Modified header."""
    try:
        resp = requests.head(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        last_mod = resp.headers.get("Last-Modified", "")
        if last_mod:
            from email.utils import parsedate_to_datetime
            mod_date = parsedate_to_datetime(last_mod)
            days_old = (datetime.utcnow().replace(tzinfo=mod_date.tzinfo) - mod_date).days
            if days_old < 30:
                return f"recently updated ({days_old}d ago)"
            if days_old < 180:
                return f"verified ({days_old}d ago)"
            return f"older data ({days_old}d ago)"
    except Exception:
        pass
    return ""



def expand_permit_query(job_type: str, city: str, state: str) -> list[str]:
    """Generate 3 alternative search queries for the job type."""
    raw = ""
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": f"List 3 alternative official permit names for '{job_type}' used by US building departments. Return only a JSON array of strings, no explanation. Example: [\"mechanical permit\", \"HVAC permit\", \"cooling system permit\"]"
            }],
            max_tokens=80,
            temperature=0.2
        )
        raw = resp.choices[0].message.content or ""
        raw = re.sub(r'```json?\s*|\s*```', '', raw).strip()
        if not raw or not raw.startswith("["):
            return []
        import json as _json
        alts = _json.loads(raw)
        if isinstance(alts, list):
            return [str(a) for a in alts[:3]]
    except Exception as e:
        print(f"[search] Query expansion raw response: {repr(raw[:100])}")
        print(f"[search] Query expansion failed (non-fatal): {e}")
    return []



def detect_contradictions(results: list[dict]) -> list[str]:
    """Detect fields where multiple sources disagree."""
    conflicts = []
    phones = list(set(r["structured"].get("phone", "") for r in results if r.get("structured", {}).get("phone")))
    if len(phones) > 1:
        conflicts.append(f"PHONE CONFLICT: Multiple numbers found - {', '.join(phones)} - verify before using")
    portals = list(set(r["structured"].get("portal_url", "") for r in results if r.get("structured", {}).get("portal_url")))
    if len(portals) > 1:
        conflicts.append("PORTAL CONFLICT: Multiple URLs found - verify which is current")
    return conflicts



def auto_update_city_kb(city: str, state: str, phone: str, portal_url: str, source_url: str):
    """Write successfully discovered city data back to cities.json for future Layer 0 hits."""
    if not (phone and portal_url):
        return
    if ".gov" not in source_url and ".us" not in source_url:
        return
    if _location_match_score(source_url, city=city, state=state) < 8:
        print(f"[search] Skipping KB auto-update for {city}, {state}: weak location match")
        return
    try:
        global _CITIES_KB
        cities_path = os.path.join(KNOWLEDGE_DIR, "cities.json")
        with open(cities_path) as f:
            kb = json.load(f)
        cities = kb.get("cities", {})
        key = f"{city.lower().replace(' ', '_')}_{state.lower()}"
        if key not in cities:
            cities[key] = {
                "city": city,
                "state": state.upper(),
                "permit_office": f"{city} Building Department",
                "permit_url": source_url,
                "phone": phone,
                "online_portal": portal_url,
                "_auto_added": True,
                "_added_date": datetime.utcnow().isoformat()
            }
            kb["cities"] = cities
            with open(cities_path, "w") as f:
                json.dump(kb, f, indent=2)
            _CITIES_KB = kb
            print(f"[search] Auto-added {city}, {state} to cities KB")
    except Exception as e:
        print(f"[search] KB auto-update failed (non-fatal): {e}")



def extract_permit_content(url: str, text: str, city: str = "", state: str = "") -> dict:
    result = {
        "phone": "",
        "fees": [],
        "portal_url": "",
        "address": "",
        "hours": "",
        "raw_text": "",
        "field_sources": {},
        "field_confidence": {},
    }
    if not text:
        return result

    normalized = _preserve_text_tables(text)
    address_ready = _clean_address_text(normalized)
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    address_lines = [line.strip() for line in address_ready.splitlines() if line.strip()]

    best_phone = extract_best_phone(normalized)
    fees = _unique_keep_order([m.strip() for m in _FEE_RE.findall(normalized)])
    table_fees = []
    for line in lines:
        if "|" in line and any(word in line.lower() for word in ["fee", "permit", "inspection", "total"]):
            table_fees.extend(_FEE_RE.findall(line))
    fees = _unique_keep_order(table_fees + fees)
    hours_matches = _unique_keep_order([m.strip() for m in _HOURS_RE.findall(normalized)])
    address_candidates: list[tuple[str, str]] = []
    for match in _ADDRESS_RE.finditer(address_ready):
        candidate = _clean_address_candidate(match.group(0).strip(" ,;"))
        if candidate:
            address_candidates.append((candidate, ""))

    url_hits = []
    for match in _URL_RE.findall(normalized):
        cleaned = match.rstrip('.,);]')
        label = cleaned.lower()
        if any(hint in label for hint in _PORTAL_HINTS):
            url_hits.append(cleaned)

    address_markers = ("office", "located at", "visit us", "city hall", "building department", "planning and zoning", "planning department", "permit", "inspection", "development services")
    for idx, line in enumerate(address_lines):
        chunk = " ".join(address_lines[max(0, idx - 1):idx + 3])
        if any(marker in line.lower() for marker in address_markers) or any(marker in chunk.lower() for marker in address_markers):
            for match in _ADDRESS_RE.finditer(chunk):
                candidate = _clean_address_candidate(match.group(0).strip(" ,;"))
                if candidate:
                    address_candidates.append((candidate, chunk))

    city_l = (city or "").lower()
    state_l = (state or "").lower()
    false_positive_terms = ("copyright", "privacy", "terms", "all rights")
    deduped_addresses = []
    seen_addr = set()
    for addr, context in address_candidates:
        key = addr.lower()
        if key in seen_addr:
            continue
        seen_addr.add(key)
        if any(term in key for term in false_positive_terms):
            continue
        if re.fullmatch(r'\s*(suite|ste\.?)(?:\s+\w+)?\s*', key, flags=re.I):
            continue
        deduped_addresses.append((addr, context))

    scored_addresses = []
    for addr, context in deduped_addresses:
        score = _score_address_candidate(addr, url, context)
        addr_l = addr.lower()
        if city_l and city_l in addr_l:
            score += 4
        if state_l and re.search(rf'\b{re.escape(state_l)}\b', addr_l, flags=re.I):
            score += 2
        if 'suite' in addr_l and not re.search(r'\b\d+\b', addr):
            score -= 10
        if score >= 7:
            scored_addresses.append((addr, context, score))
    scored_addresses.sort(key=lambda item: item[2], reverse=True)
    addresses = [addr for addr, _, _ in scored_addresses]

    permit_lines = []
    for line in lines:
        ll = line.lower()
        if any(word in ll for word in ["permit", "inspection", "building", "codes", "application", "contractor", "fee", "hour", "office", "phone"]):
            permit_lines.append(line)
    if not permit_lines:
        permit_lines = lines[:24]

    raw_parts = []
    current_len = 0
    for line in permit_lines:
        candidate = ("\n" if raw_parts else "") + line
        if current_len + len(candidate) > 1100:
            remaining = 1100 - current_len
            if remaining > 0:
                raw_parts.append(candidate[:remaining].rstrip())
            break
        raw_parts.append(candidate if raw_parts else line)
        current_len += len(candidate)

    result["phone"] = best_phone
    result["fees"] = fees[:8]
    result["portal_url"] = url_hits[0] if url_hits else ""
    result["address"] = _clean_address_candidate(addresses[0]) if addresses else ""
    result["hours"] = hours_matches[0] if hours_matches else ""
    result["raw_text"] = "".join(raw_parts).strip() or normalized[:1100]
    for field in ("phone", "portal_url", "address"):
        if result.get(field):
            result["field_sources"][field] = url
            result["field_confidence"][field] = score_field_confidence(result[field], url, field)
    if result["fees"]:
        result["field_sources"]["fees"] = url
        result["field_confidence"]["fees"] = score_field_confidence(", ".join(result["fees"]), url, "fees")
    if result["hours"]:
        result["field_sources"]["hours"] = url
        result["field_confidence"]["hours"] = score_field_confidence(result["hours"], url, "hours")
    return result



def _merge_structured_candidates(candidates: list[dict], kb_entry: dict | None = None) -> dict:
    merged = {
        "phone": "", "fees": [], "portal_url": "", "address": "", "hours": "", "raw_text": "", "source": "",
        "field_sources": {}, "field_confidence": {}, "conflicts": [], "freshness": ""
    }
    candidates = sorted(candidates, key=lambda item: (item.get("location_score", 0), item.get("source_url", "")), reverse=True)
    for item in candidates:
        item_sources = item.get("field_sources") or {}
        item_conf = item.get("field_confidence") or {}
        loc_score = item.get("location_score", 0)
        if not merged["phone"] and item.get("phone") and loc_score >= 10:
            merged["phone"] = item["phone"]
            merged["field_sources"]["phone"] = item_sources.get("phone", item.get("source_url", item.get("source", "")))
            merged["field_confidence"]["phone"] = item_conf.get("phone", "medium")
        if not merged["portal_url"] and item.get("portal_url") and loc_score >= 4:
            merged["portal_url"] = item["portal_url"]
            merged["field_sources"]["portal_url"] = item_sources.get("portal_url", item.get("source_url", item.get("source", "")))
            merged["field_confidence"]["portal_url"] = item_conf.get("portal_url", "medium")
        if not merged["address"] and item.get("address") and loc_score >= 10:
            merged["address"] = item["address"]
            merged["field_sources"]["address"] = item_sources.get("address", item.get("source_url", item.get("source", "")))
            merged["field_confidence"]["address"] = item_conf.get("address", "low")
        if not merged["hours"] and item.get("hours") and loc_score >= 10:
            merged["hours"] = item["hours"]
            merged["field_sources"]["hours"] = item_sources.get("hours", item.get("source_url", item.get("source", "")))
            merged["field_confidence"]["hours"] = item_conf.get("hours", "medium")
        if loc_score >= 4:
            merged["fees"] = _unique_keep_order(merged["fees"] + (item.get("fees") or []))[:8]
        if merged["fees"] and "fees" not in merged["field_sources"]:
            merged["field_sources"]["fees"] = item_sources.get("fees", item.get("source_url", item.get("source", "")))
            merged["field_confidence"]["fees"] = item_conf.get("fees", "medium")
        if item.get("raw_text") and len(merged["raw_text"]) < 1800:
            merged_text = (merged["raw_text"] + "\n" + item["raw_text"]).strip() if merged["raw_text"] else item["raw_text"]
            merged["raw_text"] = merged_text[:1800]
        if not merged["source"] and item.get("source"):
            merged["source"] = item["source"]
        if not merged["freshness"] and item.get("freshness"):
            merged["freshness"] = item["freshness"]

    if kb_entry:
        if not merged["phone"] and kb_entry.get("phone"):
            merged["phone"] = kb_entry["phone"]
            merged["field_sources"]["phone"] = kb_entry.get("permit_url", "knowledge_base")
            merged["field_confidence"]["phone"] = score_field_confidence(merged["phone"], kb_entry.get("permit_url", ""), "phone")
        if not merged["address"] and kb_entry.get("address"):
            merged["address"] = kb_entry["address"]
            merged["field_sources"]["address"] = kb_entry.get("permit_url", "knowledge_base")
            merged["field_confidence"]["address"] = score_field_confidence(merged["address"], kb_entry.get("permit_url", ""), "address")
        portal = kb_entry.get("online_portal") or kb_entry.get("permit_url")
        if not merged["portal_url"] and str(portal or "").startswith(("http://", "https://")):
            merged["portal_url"] = portal
            merged["field_sources"]["portal_url"] = portal
            merged["field_confidence"]["portal_url"] = score_field_confidence(portal, portal, "portal_url")
        fees = kb_entry.get("fees") or {}
        merged["fees"] = _unique_keep_order(merged["fees"] + [str(v) for k, v in fees.items() if k != "fee_note" and v])[:8]
        if merged["fees"] and "fees" not in merged["field_sources"]:
            src = kb_entry.get("permit_url", "knowledge_base")
            merged["field_sources"]["fees"] = src
            merged["field_confidence"]["fees"] = score_field_confidence(", ".join(merged["fees"]), src, "fees")
        if not merged["source"] and kb_entry.get("permit_url"):
            merged["source"] = "layer0_jina"
    return merged



def _render_search_context(payload: dict) -> str:
    structured = payload.get("structured") or {}
    results = payload.get("results") or []
    field_sources = structured.get("field_sources") or {}
    field_conf = structured.get("field_confidence") or {}
    def fmt(field: str, value):
        if isinstance(value, list):
            value = ", ".join(value) if value else "None found"
        value = value or "Not found"
        extra = []
        if field_conf.get(field):
            extra.append(f"confidence: {field_conf[field]}")
        if field_sources.get(field):
            extra.append(f"source: {field_sources[field]}")
        return f"{value}" + (f" [{' | '.join(extra)}]" if extra else "")
    lines = [
        "=== STRUCTURED PERMIT DATA (machine-extracted, high confidence) ===",
        f"Phone: {fmt('phone', structured.get('phone'))}",
        f"Portal: {fmt('portal_url', structured.get('portal_url'))}",
        f"Fees found: {fmt('fees', structured.get('fees') or [])}",
        f"Address: {fmt('address', structured.get('address'))}",
        f"Hours: {fmt('hours', structured.get('hours'))}",
        f"Source: {structured.get('source') or 'unknown'}",
    ]
    if payload.get("jurisdiction_note"):
        lines.append(f"Jurisdiction note: {payload['jurisdiction_note']}")
    if payload.get("similar_city_note"):
        lines.append(payload['similar_city_note'])
    if structured.get("freshness"):
        lines.append(f"Data freshness: {structured['freshness']}")
    change_info = payload.get("change_info") or {}
    if change_info.get("changed") and change_info.get("change_summary"):
        lines.append(f"⚠️ CHANGE DETECTED: {change_info['change_summary']}")
    conflicts = structured.get("conflicts") or []
    if conflicts:
        lines.append("Conflicts:")
        lines.extend([f"- {c}" for c in conflicts])
    lines.extend([
        "---",
        "=== REAL-TIME WEB SEARCH RESULTS ===",
    ])
    for r in results:
        lines.append("")
        lines.append(f"Source: {r.get('url', '')}")
        lines.append(f"Title: {r.get('title', '')}")
        lines.append(f"Excerpt: {r.get('content', '')}")
        lines.append("---")
    return "\n".join(lines)



def _cache_still_valid(payload: dict) -> bool:
    try:
        results = payload.get("results") or []
        if not results:
            return True
        url = results[0].get("url", "")
        if not url:
            return True
        resp = requests.head(url, allow_redirects=True, timeout=8, headers={"User-Agent": "Mozilla/5.0 (PermitAssist Search Bot)"})
        return resp.status_code != 404
    except Exception:
        return True



def _parse_search_context_structured(search_context: str) -> dict:
    structured = {}
    if not search_context:
        return structured
    patterns = {
        "phone": r'^Phone:\s*(.+)$',
        "portal_url": r'^Portal:\s*(.+)$',
        "fees": r'^Fees found:\s*(.+)$',
        "address": r'^Address:\s*(.+)$',
        "hours": r'^Hours:\s*(.+)$',
    }
    for key, pat in patterns.items():
        match = re.search(pat, search_context, flags=re.M)
        if not match:
            continue
        value = match.group(1).strip()
        value = re.sub(r'\s*\[[^\]]*\]\s*$', '', value).strip()
        if value.lower() in ('not found', 'none found'):
            value = ''
        structured[key] = value
    return structured



def _make_result(url: str, title: str, content: str, source_layer: str) -> dict:
    return {
        "url": url,
        "title": title,
        "content": clean_summary_text(content, max_len=900),
        "source_layer": source_layer,
    }



def _fetch_and_structure_url(url: str, city: str, state: str, default_title: str = "") -> dict | None:
    if not url:
        return None
    if is_pdf_url(url):
        print("[search] PDF detected, routing to unified PDF extractor")
        pdf_text = extract_pdf_text(url, city, state)
        content = (pdf_text or "").strip()
        status = 200 if content else 0
        source_layer = "layer1_pdf"
        if content and len(content) > 400:
            record_url_success(url, len(content))
        structured = extract_permit_content(url, content, city=city, state=state) if content else {}
        if structured:
            structured["source_url"] = url
            structured["source"] = source_layer
            structured["freshness"] = check_page_freshness(url)
        return {
            "url": url,
            "title": default_title or url,
            "content": content[:900],
            "structured": structured or {},
            "status": status,
            "source_layer": source_layer,
        }
    text, status, is_spa = jina_fetch(url)
    if status == 404:
        return {"url": url, "title": default_title or url, "content": "", "structured": {}, "status": 404, "source_layer": "layer1_dead"}
    source_layer = "layer1_brave_jina"
    if is_spa or len((text or '').strip()) < 200:
        if is_spa:
            print(f"[search] SPA detected at {url} - trying firecrawl")
        fire_text = firecrawl_fetch(url)
        if fire_text:
            text = fire_text
            source_layer = "layer2_firecrawl"
    content = (text or "").strip()
    if content and len(content) > 400:
        record_url_success(url, len(content))
    structured = extract_permit_content(url, content, city=city, state=state) if content else {}
    if structured:
        structured["source_url"] = url
        structured["source"] = source_layer
        structured["freshness"] = check_page_freshness(url)
    follow_content = ""
    if content:
        for href in find_followup_links(url, content):
            child_text, child_status, child_is_spa = jina_fetch(href)
            if child_status == 404:
                continue
            if child_is_spa or len((child_text or '').strip()) < 120:
                child_fire = firecrawl_fetch(href)
                if child_fire:
                    child_text = child_fire
            child_text = (child_text or "").strip()
            if child_text:
                follow_content += "\n\n" + child_text[:600]
                if len(content) + len(follow_content) > 1800:
                    break
    merged_content = (content + follow_content)[:1800] if content else follow_content[:1800]
    if merged_content and structured:
        structured = extract_permit_content(url, merged_content, city=city, state=state)
        structured["source_url"] = url
        structured["source"] = source_layer
        structured["freshness"] = check_page_freshness(url)
    return {
        "url": url,
        "title": default_title or url,
        "content": merged_content[:900],
        "structured": structured or {},
        "status": status,
        "source_layer": source_layer,
    }



def scrape_urls_parallel(urls: list[str], city: str = "", state: str = "", max_workers: int = 3, timeout: int = 12) -> list[dict]:
    """Scrape multiple URLs in parallel. Returns list of dicts with structured extraction."""
    results = []
    unique_urls = _unique_keep_order(urls)[: max_workers + 1]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {executor.submit(_fetch_and_structure_url, url, city, state): url for url in unique_urls[:max_workers]}
        try:
            for future in as_completed(future_to_url, timeout=timeout):
                url = future_to_url[future]
                try:
                    item = future.result(timeout=10)
                    if item and item.get("content"):
                        results.append(item)
                except Exception as e:
                    print(f"[search] Parallel scrape failed for {url}: {e}")
        except FuturesTimeout:
            print("[search] Parallel scrape timeout reached")
    return results



def build_search_context(job_type: str, city: str, state: str, zip_code: str = "",
                          city_match_level: str = "city") -> str:
    try:
        search_city, search_state, jurisdiction_note = normalize_jurisdiction(city, state)
        cached = get_search_cache(search_city, search_state, city_match_level)
        if cached:
            if _cache_still_valid(cached):
                print(f"[search] Search cache hit for {search_city}, {search_state}")
                return _render_search_context(cached)
            delete_search_cache(search_city, search_state)

        kb_entry = _find_city_kb_entry(city, state) or _find_city_kb_entry(search_city, search_state)
        all_results = []
        structured_candidates = []
        detailed_results = []
        seen_urls = set()
        total_chars = 0
        auto_kb_updated = False

        def add_result(url: str, title: str, content: str, source_layer: str):
            nonlocal total_chars
            if not url or url in seen_urls:
                return
            seen_urls.add(url)
            content = content or ""
            all_results.append(_make_result(url, title, content, source_layer))
            total_chars += len(content.strip())

        def add_scraped_item(item: dict, fallback_title: str = ""):
            nonlocal auto_kb_updated
            if not item or item.get("status") == 404:
                return
            url = item.get("url", "")
            title = item.get("title") or fallback_title or url
            content = item.get("content", "")
            source_layer = item.get("source_layer", "")
            add_result(url, title, content, source_layer)
            structured = item.get("structured") or {}
            loc_score = _location_match_score(url, title=title, content=content, city=search_city, state=search_state)
            if any(structured.get(k) for k in ("phone", "fees", "portal_url", "address", "hours", "raw_text")):
                structured = dict(structured)
                structured["location_score"] = loc_score
                structured["source_url"] = url
                structured_candidates.append(structured)
                detailed_results.append({"url": url, "structured": structured})
                if (not auto_kb_updated and loc_score >= 10 and source_layer.startswith("layer1") and structured.get("phone") and structured.get("portal_url") and 
                    score_field_confidence(structured.get("phone"), url, "phone") == "high" and
                    score_field_confidence(structured.get("portal_url"), url, "portal_url") == "high"):
                    auto_update_city_kb(city, state, structured.get("phone"), structured.get("portal_url"), url)
                    auto_kb_updated = True

        if kb_entry and kb_entry.get("permit_url"):
            print("[search] Layer 0: direct permit source check")
            primary = _fetch_and_structure_url(kb_entry.get("permit_url"), city, state, kb_entry.get("permit_office", f"{city} permit office"))
            add_scraped_item(primary, kb_entry.get("permit_office", f"{city} permit office"))
            portal_url = kb_entry.get("online_portal")
            if portal_url and portal_url != kb_entry.get("permit_url"):
                add_scraped_item(_fetch_and_structure_url(portal_url, city, state, f"{city} online permit portal"), f"{city} online permit portal")

        if ACCELA_APP_ID:
            accela_data = accela_get_permit_info(city, state, job_type)
            if accela_data:
                print(f"[search] Accela data found for {city}, {state}")
                accela_structured = accela_data.get("structured") or {}
                accela_url = f"{ACCELA_DOCS_BASE_URL}/api-settings.html#operation/v4.get.settings.records.types"
                add_result(accela_url, f"Accela permit data for {city}, {state}", accela_data.get("summary", ""), "layer0_5_accela")
                if accela_structured:
                    structured_candidates.append(accela_structured)
                    detailed_results.append({"url": accela_url, "structured": accela_structured})

        if total_chars < 200:
            alt_queries = expand_permit_query(job_type, search_city, search_state)
            primary_query = f'"{search_city}" "{search_state}" {job_type} permit requirements fee site:.gov'
            relaxed_query = f'{search_city} {search_state} {job_type} permit requirements fees building department'
            merged_results = []
            merged_results.extend(serper_search(primary_query, num=5, city=search_city, state=search_state) or brave_search(primary_query, num=5, city=search_city, state=search_state))
            if alt_queries:
                alt_query = f'{search_city} {search_state} {alt_queries[0]} permit building department site:.gov'
                merged_results.extend(serper_search(alt_query, num=4, city=search_city, state=search_state) or brave_search(alt_query, num=4, city=search_city, state=search_state))
            if not merged_results:
                merged_results.extend(serper_search(relaxed_query, num=5, city=search_city, state=search_state) or brave_search(relaxed_query, num=5, city=search_city, state=search_state))
            ranked = _rank_search_results(merged_results, limit=4, city=search_city, state=search_state)
            scraped = scrape_urls_parallel([r.get("url", "") for r in ranked], city=city, state=state, max_workers=3, timeout=14)
            scraped_by_url = {item.get("url", ""): item for item in scraped}
            for r in ranked:
                item = scraped_by_url.get(r.get("url", ""))
                if item:
                    item["title"] = r.get("title", "") or item.get("title", "")
                    if SERPER_API_KEY and item.get("source_layer") == "layer1_brave_jina":
                        item["source_layer"] = "layer1_serper_jina"
                    add_scraped_item(item, r.get("title", ""))
                else:
                    add_result(r.get("url", ""), r.get("title", ""), r.get("content", ""), "layer1_search_only")

        if total_chars < 200:
            print(f"[search] Layer 4: tavily fallback")
            fallback_query = f"{search_city} {search_state} building permit {job_type} office phone address fees apply online"
            for r in tavily_search(fallback_query, max_results=4):
                add_result(r.get("url", ""), r.get("title", ""), r.get("content", ""), "layer3_tavily")

        merged = _merge_structured_candidates(structured_candidates, kb_entry=kb_entry)
        merged["conflicts"] = detect_contradictions(detailed_results)
        compact_results = []
        for r in all_results:
            compact_results.append({
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "content": r.get("content", "")[:900],
                "source_layer": r.get("source_layer", ""),
            })
            if len(compact_results) >= 8:
                break

        if not compact_results:
            return ""

        payload = {
            "city_match_level": city_match_level,
            "jurisdiction_note": jurisdiction_note,
            "structured": merged,
            "results": compact_results,
            "auto_kb_updated": auto_kb_updated,
        }
        set_search_cache(search_city, search_state, payload)
        return _render_search_context(payload)
    except Exception as e:
        print(f"[search] build_search_context failed (non-fatal): {e}")
        try:
            print(f"[search] Layer 4: tavily fallback")
            fallback_query = f"{city} {state} building permit {job_type}"
            fallback_results = tavily_search(fallback_query, max_results=4)
            if not fallback_results:
                return ""
            payload = {
                "city_match_level": city_match_level,
                "jurisdiction_note": "",
                "structured": {"phone": "", "fees": [], "portal_url": "", "address": "", "hours": "", "raw_text": "", "source": "layer3_tavily", "field_sources": {}, "field_confidence": {}, "conflicts": [], "freshness": ""},
                "results": [_make_result(r.get("url", ""), r.get("title", ""), r.get("content", ""), "layer3_tavily") for r in fallback_results],
            }
            return _render_search_context(payload)
        except Exception as inner_e:
            print(f"[search] Final fallback failed (non-fatal): {inner_e}")
            return ""
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


def build_google_maps_url(city: str, state: str, address: str = "", office: str = "") -> str:
    """Build the best possible Google Maps URL — pinned address if available, else office+city search."""
    # If we have a real street address, use maps?q= for a pin
    if address and address.strip():
        q = address.strip()
        # Append city/state if not already present
        if city.lower() not in q.lower():
            q = f"{q}, {city}, {state}"
        return f"https://www.google.com/maps?q={q.replace(' ', '+')}"
    # Otherwise use a more targeted search: office name + city
    if office and office.strip():
        query = f"{office}, {city}, {state}".replace(" ", "+")
    else:
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
• Roofing: IRC Chapter 9, wind zones (ASCE 7-22), fire ratings, ICC-600 for high-wind, ice & water shield requirements by climate zone, 25% rule for re-roofing vs tear-off. PERMIT THRESHOLD: The 100 sq ft rule is NOT universal — thresholds vary by jurisdiction (some require permit for ANY decking replacement regardless of area). Always state the specific local threshold or note it varies by jurisdiction.
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

2b. SOLAR PERMIT DEDUPLICATION: For solar jobs, 'Building permit' and 'Structural permit' are THE SAME PERMIT — never list both. Use 'Building Permit — Solar PV (Structural Racking & Roof Penetrations)' as the single permit type name.

2. PORTAL_SELECTION must be the exact dropdown/checkbox string from the permit portal. Examples:
   - For HVAC replacement: "HVAC Replacement - Residential" or "Mechanical - AC/Furnace Replacement"
   - For water heater: "Plumbing - Water Heater Replacement (Gas)" or "Gas Water Heater - Residential"
   - For panel upgrade: "Electrical - Panel Upgrade 200A" or "Service Upgrade - 200 Amp"
   - For roofing: "Roofing - Re-Roof (Shingles)" or "Roof Replacement - Residential"
   - For EV charger: "Electrical - EV Charger (Level 2, 240V)" or "EV Charging Station - Residential"
   - For solar: "Electrical - Solar PV System" AND "Building - Solar Panel Installation"
   - For deck: "Building - Deck Addition" or "Residential Deck/Patio Cover"
   Use the most specific version you can based on city/state context.

   FEW-SHOT EXAMPLES for portal_selection (use these as reference patterns):
   - HVAC replacement → "HVAC Replacement - Residential System"
   - HVAC repair → "HVAC Repair - Residential"
   - Electrical service upgrade → "Electrical Permit - Service Upgrade"
   - Electrical panel replacement → "Electrical Permit - Panel Replacement"
   - Plumbing water heater → "Plumbing Permit - Water Heater Replacement"
   - Roofing replacement → "Roofing Permit - Residential Re-Roof"
   - Deck construction → "Building Permit - Residential Deck"
   - Solar installation → "Building Permit - Solar Installation"
   - Generator installation → "Electrical Permit - Generator Installation"
   Always adapt these to the specific city's portal naming if you know it.

3. ALWAYS include the phone number in apply_phone. If not in KB:
   - Search web results for the actual number
   - If not found, provide the format: "Search Google Maps: [city] [state] building permit office"
   Never return null for apply_phone — always return something actionable.

4. APPLY_URL must be a real WEB PORTAL URL (not a PDF). Examples of valid apply_url:
   - "https://aca-prod.accela.com/PHOENIX" ✅
   - "https://abc.austintexas.gov" ✅
   - "https://houston.gov/onlinepermitting" ✅
   - "https://city.gov/docs/permit-application.pdf" ❌ (this is a PDF — put in apply_pdf instead)
   If only a PDF application exists, set apply_url = null and apply_pdf = [PDF URL]. If you are not certain of the exact application URL, return the city's main permit office website URL rather than null.

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

11. CONTRACTOR REGISTRATION WARNINGS: For any city that requires separate city/metro contractor registration (Nashville Metro Codes, Phoenix PDD, Dallas, Chicago, etc.), ALWAYS add to common_mistakes: "Submitting permit application without verifying current contractor registration status — an expired or lapsed city registration will block your application silently. Verify your registration is current BEFORE submitting." Also add to pro_tips: "Check your [city] contractor registration renewal date before starting any permit application — registration lapses are the #1 avoidable rejection reason."

12. INSPECTION BOOKING: Always populate 'inspection_booking' with specific instructions for how to schedule inspections. Contractors often don't know this and it causes project delays. Include advance notice requirements (very important — Phoenix requires 24hr minimum, some cities require 48hr).

Return ONLY a JSON object with these exact fields:
{
  "job_summary": "clear description of what the job involves and what permits it triggers",
  "total_cost_estimate": "MANDATORY: realistic total project cost range including labor/materials/permit, e.g. '$2,500 - $4,500'",
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
  "apply_url": "The DIRECT URL to apply online or start the permit application. IMPORTANT: For cities using Accela (aca-prod.accela.com/CITYNAME), return the exact Accela portal URL. For Tyler Technologies portals, return the exact URL. For city .gov permit pages, return the exact URL. Do NOT return homepage URLs — return the specific permit application page. If you are not certain of the exact URL, return the city's main permit office website URL rather than null.",
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
  "inspection_booking": "HOW to schedule inspections for this jurisdiction. Include: online portal URL if available, phone number, advance notice required (e.g. '24 hours minimum'), hours of operation if known. Example: 'Schedule online at permits.cityname.gov — minimum 24 hours advance notice required. Phone: (555) 555-1234, Mon-Fri 8am-4pm.' Return null if unknown.",
  "license_required": "Licensed HVAC contractor (TACL in TX) pulls the permit — their license # must appear on the mechanical permit application. Owner-builders cannot pull HVAC permits in TX.",
  "city_contractor_registration": "If this city requires a SEPARATE city-level contractor registration (on top of state license), describe it here with renewal frequency and how to get it. Return null if no city registration required.",
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
    "section": "IRC R105.2.2",
    "text": "Ordinary repairs to structures shall not include the cutting away of any wall, partition or portion thereof..."
  },
  "companion_permits": [
    {
      "permit_type": "Name of companion permit (e.g. Electrical Permit)",
      "reason": "One sentence explaining why this additional permit is needed for this job",
      "certainty": "almost_certain | likely | possible"
    }
  ],
  "zoning_hoa_flag": "For solar and roofing jobs: describe potential HOA restrictions, historic district overlay requirements, and zoning restrictions that could block or delay work. State-specific solar access laws if relevant. Return null for jobs where this is not applicable.",
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
✓ city_contractor_registration: populate ONLY when city requires separate city-level contractor registration beyond state license (e.g. Dallas annual registration, Phoenix city contractor registration, Chicago city license, Nashville Metro Codes registration). Set to null if only state license required.
✓ pro_tips save real time or money — not generic advice
✓ companion_permits is MANDATORY — always populate this field. Use the trade matrix below. Return [] ONLY if the job is truly isolated (e.g. painting, landscaping, minor repairs). For any mechanical, electrical, plumbing, or structural work, there are almost always companion permits.

COMPANION PERMIT TRADE MATRIX (use this to populate companion_permits):
- HVAC/AC/furnace/heat pump replacement → [almost_certain: Electrical Permit (disconnect/reconnect circuit), likely: Gas Permit if gas appliance]
- Electrical panel upgrade/service change → [almost_certain: Electrical Inspection Permit, likely: Utility Coordination Permit]
- Bathroom remodel → [almost_certain: Plumbing Permit, almost_certain: Electrical Permit (GFCI/outlets), possible: Building Permit if walls moved]
- Kitchen remodel → [almost_certain: Plumbing Permit, almost_certain: Electrical Permit, possible: Mechanical Permit for range hood]
- Roof replacement → [possible: Electrical Permit if solar panels present, possible: Structural Permit if decking replaced]
- Water heater replacement → [likely: Gas Permit if gas unit, possible: Electrical Permit if converting to electric]
- Deck/patio addition → [almost_certain: Building Permit, likely: Electrical Permit if adding outlets/lighting]
- Garage conversion/ADU → [almost_certain: Building Permit, almost_certain: Electrical Permit, almost_certain: Plumbing Permit, likely: Mechanical Permit]
- Solar panel installation → [almost_certain: Electrical Permit, almost_certain: Building/Structural Permit, likely: Utility Interconnection Permit]
- EV charger installation → [almost_certain: Electrical Permit]
- Generator installation → [almost_certain: Electrical Permit, likely: Gas/Mechanical Permit]
- Basement finish → [almost_certain: Building Permit, almost_certain: Electrical Permit, likely: Plumbing Permit, likely: Mechanical Permit]
- Window/door replacement → [possible: Building Permit if structural opening changes]
- Plumbing repiping → [almost_certain: Plumbing Permit, possible: Building Permit for access openings]
✓ apply_url: ALWAYS provide the direct online permit portal URL if one exists (e.g. "https://abc.austintexas.gov"). Do not leave null if you found a portal in your research.
✓ total_cost_estimate: Provide a realistic total project cost range for this job in this city (including labor, materials, and permit fees). Example: "$2,500 - $4,500". NEVER leave this field null — use your training knowledge to provide a best-estimate range for the contractor.
✓ approval_timeline: Always provide a 'simple' (over-the-counter) and 'complex' (plan review) estimate.
✓ code_citation: ALWAYS include the specific code section (IRC/IPC/NEC/state code) that applies. Format: {"section": "IRC R105.2.2", "text": "first 120 chars of the relevant rule or exemption text"}. For NO verdicts: cite the exemption clause. For YES/MAYBE verdicts: cite the primary code section that REQUIRES the permit (e.g. "IRC R105.1", "NEC 210.12", "IPC 106.1"). Never set code_citation to null — always provide a relevant code reference."""

# ─── Main Research Function ───────────────────────────────────────────────────

def research_permit(job_type: str, city: str, state: str, zip_code: str = "", use_cache: bool = True, job_category: str = "residential", job_value: float | None = None) -> dict:
    """
    Research permit requirements for a job + location.
    v3: Better advice depth, small city fallback, PDF stripping, Google Maps fallback.
    """
    init_cache()

    job_category = job_category.lower().strip() if job_category else "residential"
    if job_category not in ("residential", "commercial"):
        job_category = "residential"

    key = cache_key(job_type, city, state, job_category)

    if use_cache:
        def _background_refresh(k):
            """Re-run the lookup without cache and save fresh result."""
            try:
                print(f"[cache] Background refresh started for key {k[:8]}…")
                fresh = research_permit(job_type, city, state, zip_code, use_cache=False, job_category=job_category, job_value=job_value)
                if fresh and not fresh.get("error"):
                    save_cache(k, job_type, job_category, city, state, zip_code, fresh)
                    print(f"[cache] Background refresh complete for {city}, {state} / {job_type}")
            except Exception as e:
                print(f"[cache] Background refresh failed (non-fatal): {e}")

        cached = get_cached(key, _refresh_callback=_background_refresh)
        if cached:
            cached["_cached"] = True
            if not isinstance(cached.get("companion_permits"), list):
                cached["companion_permits"] = []
            if not isinstance(cached.get("sources"), list):
                cached["sources"] = []
            cached["sources"] = normalize_sources(cached.get("sources", []), cached.get("apply_url", ""), cached.get("apply_pdf", ""))
            if "missing_fields" not in cached:
                cached["missing_fields"] = compute_missing_fields(cached)
            cached["needs_review"] = bool(cached.get("missing_fields"))
            if not cached.get("confidence_reason"):
                meta = cached.get("_meta", {})
                cached["confidence_reason"] = derive_confidence_reason(
                    cached,
                    meta.get("city_match_level", cached.get("data_source", "general_knowledge")),
                    bool(meta.get("auto_verified")),
                    cached.get("missing_fields", []),
                    meta.get("web_sources", 0),
                )
            if 'checklist' not in cached:
                cached['checklist'] = generate_permit_checklist(job_type, city, state, cached)
            if 'rejection_patterns' not in cached:
                cached['rejection_patterns'] = get_rejection_patterns(city, state, job_type)
            if 'permit_ready_score' not in cached:
                score_structured = {
                    'phone': cached.get('apply_phone', ''),
                    'portal_url': cached.get('apply_url', ''),
                    'fees': cached.get('fee_range') if isinstance(cached.get('fee_range'), list) else ([cached.get('fee_range')] if cached.get('fee_range') else []),
                    'address': cached.get('apply_address', ''),
                }
                s, label, missing = calculate_permit_ready_score('', score_structured)
                cached['permit_ready_score'] = s
                cached['permit_ready_label'] = label
                cached['permit_ready_missing'] = missing
            if job_value is not None:
                cached['job_value'] = job_value
                cached['fee_calculator'] = calculate_exact_fee(job_type, city, state, float(job_value))
            elif 'fee_calculator' not in cached:
                cached['fee_calculator'] = {'fee': None, 'formula': None, 'confidence': 'none', 'note': 'Provide job_value to calculate an exact fee where formulas are available.'}
            return cached

    # ── Check auto-verified data first ──
    _verified_entry = None
    try:
        import sys
        import os as _os
        _scripts_dir = _os.path.join(_os.path.dirname(__file__), "..", "scripts")
        if _scripts_dir not in sys.path:
            sys.path.insert(0, _scripts_dir)
        from auto_verify import get_verified_for_city_trade
        # Detect trade from job_type for lookup key
        _job_lower = job_type.lower()
        _trade_guess = "general"
        if any(w in _job_lower for w in ["hvac", "ac unit", "air condition", "heat pump", "furnace", "mini split", "ductless"]):
            _trade_guess = "hvac"
        elif any(w in _job_lower for w in ["electric", "panel", "wiring", "ev charger", "solar", "generator"]):
            _trade_guess = "electrical"
        elif any(w in _job_lower for w in ["plumb", "water heater", "pipe", "drain", "sewer"]):
            _trade_guess = "plumbing"
        elif any(w in _job_lower for w in ["roof", "shingle", "gutter"]):
            _trade_guess = "roofing"
        _verified_entry = clean_verified_entry(get_verified_for_city_trade(city, state, _trade_guess))
    except Exception as _e:
        print(f"[research] auto_verify check failed (non-fatal): {_e}")

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
    machine_structured = _parse_search_context_structured(search_context)
    if search_context:
        print(f"[research] Got {search_context.count('Source:')} web sources")
    else:
        print("[research] No web results — using KB + GPT training data")

    extra_context = ""
    if "solar" in job_type.lower() or "pv" in job_type.lower():
        if _check_solarapp(city, state):
            extra_context = (
                f"\nSolarAPP+ STATUS: {city}, {state} HAS ADOPTED SolarAPP+. Mention this prominently — "
                "qualifying residential solar installations may receive instant permit approval. "
                "Include in pro_tips and approval_timeline."
            )
        else:
            extra_context = (
                f"\nSolarAPP+ STATUS: {city}, {state} is NOT confirmed in our SolarAPP+ database. "
                "Tell the contractor to ask the permit office if SolarAPP+ is available."
            )

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

    # ── Step 3b: Jurisdiction quirks ──
    quirks = _get_jurisdiction_quirks(city, state)
    quirks_context = ""
    if quirks:
        quirks_context = "\n\n=== LOCAL JURISDICTION QUIRKS — CRITICAL FOR THIS CITY ===\n"
        quirks_context += "These are verified local gotchas that catch contractors off guard. Include them in pro_tips or common_mistakes:\n"
        for q in quirks:
            quirks_context += f"• {q}\n"
        print(f"[research] Found {len(quirks)} jurisdiction quirks for {city}, {state}")

    # ── Step 4: GPT synthesis ──
    # Build verified data context if available
    _verified_context = ""
    if _verified_entry:
        vd = _verified_entry.get("data", {})
        _verified_context = (
            f"\n\n=== PRE-VERIFIED DATA (confidence: verified, source: {_verified_entry.get('source_url','')}) ===\n"
            f"Trade: {_verified_entry.get('trade','')}\n"
            f"Verified at: {_verified_entry.get('verified_at','')}\n"
            f"Phone: {vd.get('phone','(not found)')}\n"
            f"Fee range: {vd.get('fee_range','(not found)')}\n"
            f"Summary: {vd.get('summary','')}\n"
            "Use this verified data in your response — it's from official sources."
        )
        print(f"[research] Using auto-verified data for {city}, {state} ({_trade_guess})")

    user_prompt = f"""A contractor needs permit information for this job:

Job: {job_type}
Location: {location_str}
Job Category: {job_category.upper()} (important: tailor requirements and fees for {job_category} work — requirements differ between residential and commercial)
City data availability: {city_match_level}

{f"IMPORTANT HINTS FOR THIS JOB TYPE:{chr(10)}{job_hints}" if job_hints else ""}

{kb_context}

{_verified_context}

{search_context}{extra_context}

{quirks_context}

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
    raw = None

    try:
        response = client.chat.completions.create(
            model="gpt-5.4-mini",
            messages=[
                {"role": "system",  "content": SYSTEM_PROMPT},
                {"role": "user",    "content": user_prompt},
            ],
            temperature=0.1,
            max_completion_tokens=8000,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        print(f"[engine] OpenAI gpt-5.4-mini responded in {round((time.time()-start)*1000)}ms")
    except Exception as openai_err:
        print(f"[engine] OpenAI failed ({openai_err}), trying Gemini 3 Pro fallback...")
        if not _GEMINI_API_KEY:
            raise RuntimeError(f"OpenAI failed and no GEMINI_API_KEY set: {openai_err}")
        try:
            gemini_model = genai.GenerativeModel(
                model_name=_gemini_fallback_model,
                generation_config=genai.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=3000,
                    response_mime_type="application/json",
                )
            )
            gemini_prompt = f"{SYSTEM_PROMPT}\n\n{user_prompt}"
            gemini_resp = gemini_model.generate_content(gemini_prompt)
            raw = gemini_resp.text
            print(f"[engine] Gemini 3 Pro fallback responded in {round((time.time()-start)*1000)}ms")
        except Exception as gemini_err:
            raise RuntimeError(f"Both OpenAI and Gemini failed. OpenAI: {openai_err} | Gemini: {gemini_err}")

    elapsed = round((time.time() - start) * 1000)
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # Gemini sometimes wraps JSON in markdown code fences — strip and retry
        import re as _re
        cleaned = raw.strip() if raw else ""
        fence_match = _re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', cleaned)
        if fence_match:
            cleaned = fence_match.group(1).strip()
        # Also strip any leading/trailing non-JSON text
        brace_match = _re.search(r'(\{[\s\S]*\})', cleaned)
        if brace_match:
            cleaned = brace_match.group(1)
        try:
            result = json.loads(cleaned)
            print(f"[engine] Stripped markdown wrapper from AI response successfully")
        except (json.JSONDecodeError, TypeError) as e2:
            print(f"[engine] AI returned non-JSON response: {repr((raw or '')[:300])}")
            raise RuntimeError(f"AI returned non-JSON output: {e2}")

    # ── Post-processing ──

    # Prefer machine-extracted contact details when the model leaves gaps
    if machine_structured.get("phone") and (not result.get("apply_phone") or str(result.get("apply_phone", "")).startswith("Search:")):
        result["apply_phone"] = machine_structured["phone"]
    if machine_structured.get("portal_url") and not result.get("apply_url"):
        result["apply_url"] = machine_structured["portal_url"]
    if machine_structured.get("address") and not result.get("apply_address"):
        result["apply_address"] = machine_structured["address"]
    if machine_structured.get("fees") and (not result.get("fee_range") or any(p in str(result.get("fee_range", "")).lower() for p in ["varies", "not confirmed", "unknown", "call the"])):
        result["fee_range"] = machine_structured["fees"]

    # Strip PDF URLs from apply_url
    result = strip_pdf_from_result(result)

    # ── Hallucination guard: reject vague fee_range non-answers ──
    _fee = str(result.get("fee_range") or "").lower().strip()
    _VAGUE_FEE_PHRASES = [
        "varies", "contact", "call", "check with", "depends on", "consult",
        "not available", "unknown", "n/a", "tbd", "to be determined",
        "see website", "visit website", "refer to"
    ]
    if not _fee or any(p in _fee for p in _VAGUE_FEE_PHRASES):
        # Replace vague answers with a clear fallback
        result["fee_range"] = (
            f"Fee not confirmed — call the {result.get('applying_office') or city + ' building dept'} "
            f"or check their online fee schedule before applying."
        )
        result["_fee_unverified"] = True
        print(f"[fee_guard] Vague fee_range replaced for {city}, {state}: '{_fee}'") 

    # Ensure apply_google_maps is always set (use address + office for best pin)
    if not result.get("apply_google_maps"):
        result["apply_google_maps"] = build_google_maps_url(
            city, state,
            address=result.get("apply_address", ""),
            office=result.get("applying_office", "")
        )
    else:
        # Upgrade existing search URLs to pinned address URLs when we have address data
        existing = result["apply_google_maps"]
        addr = result.get("apply_address", "")
        office = result.get("applying_office", "")
        if "/maps/search/" in existing and (addr or office):
            result["apply_google_maps"] = build_google_maps_url(city, state, address=addr, office=office)
    # Always set maps_url as alias for frontend
    result["maps_url"] = result["apply_google_maps"]

    # Ensure apply_phone is never null
    if not result.get("apply_phone"):
        result["apply_phone"] = f"Search: {build_google_maps_url(city, state, office=result.get('applying_office',''))}"

    # ── County fallback for small/unknown cities + low-confidence known cities ──
    if city_match_level in ("state", "none") or (result.get("confidence") == "low" and not result.get("apply_url")):
        city_key = city.lower().strip().replace(" ", "_")
        city_key_spaces = city.lower().strip()
        county_key = CITY_TO_COUNTY.get(city_key) or CITY_TO_COUNTY.get(city_key_spaces)
        if county_key and county_key in COUNTY_DATA:
            county = COUNTY_DATA[county_key]
            # Fill in missing fields from county data
            if not result.get("apply_phone") or result["apply_phone"].startswith("Search:"):
                result["apply_phone"] = county["phone"]
            if not result.get("apply_url"):
                result["apply_url"] = county["url"]
            if not result.get("applying_office"):
                result["applying_office"] = county["office"]
            result["county_fallback"] = True
            result["county_fallback_note"] = (
                f"We don't have exact data for {city} — showing {county['name']} "
                f"data which covers this area."
            )

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

    # ── City Database Fallbacks for Missing Fields ──
    if city_match_level == "city":
        _load_knowledge()
        _city_key = city.lower().strip().replace(" ", "_") + "_" + state.lower().strip()
        _city_data = _CITIES_KB.get("cities", {}).get(_city_key)
        if _city_data:
            if not result.get("apply_url"):
                result["apply_url"] = _city_data.get("online_portal") or _city_data.get("permit_url")
            if not result.get("apply_phone") or result["apply_phone"].startswith("Search:"):
                result["apply_phone"] = _city_data.get("phone")
            if not result.get("apply_address"):
                result["apply_address"] = _city_data.get("address")

    # ── Total Cost Estimate fallback (hardcoded industry averages if AI skipped it) ──
    if not result.get("total_cost_estimate"):
        _job_lower = job_type.lower()
        _COST_TABLE = {
            "hvac":             "$4,000 – $12,000 (unit + labor + permit)",
            "mini split":       "$2,000 – $7,000 (unit + labor + permit)",
            "electrical panel": "$1,500 – $4,000 (labor + materials + permit)",
            "panel upgrade":    "$1,500 – $4,000 (labor + materials + permit)",
            "ev charger":       "$800 – $2,500 (charger + labor + permit)",
            "solar":            "$15,000 – $30,000 (system + install + permits)",
            "generator":        "$5,000 – $15,000 (unit + install + permits)",
            "water heater":     "$900 – $2,500 (unit + labor + permit)",
            "roof":             "$8,000 – $20,000 (materials + labor + permit)",
            "deck":             "$5,000 – $15,000 (materials + labor + permit)",
            "bathroom remodel": "$8,000 – $25,000 (labor + materials + permits)",
            "kitchen remodel":  "$15,000 – $50,000 (labor + materials + permits)",
            "window":           "$400 – $1,000 per window (unit + install + permit)",
            "fence":            "$2,000 – $8,000 (materials + labor + permit)",
            "pool":             "$35,000 – $75,000 (construction + permits)",
            "plumbing":         "$1,000 – $5,000 (labor + materials + permit)",
            "shed":             "$3,000 – $10,000 (structure + permit)",
        }
        for _keyword, _range in _COST_TABLE.items():
            if _keyword in _job_lower:
                result["total_cost_estimate"] = _range
                break

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

    # Ensure list fields are present and well-formed
    if not isinstance(result.get("permits_required"), list):
        result["permits_required"] = []
    if not isinstance(result.get("inspections"), list):
        result["inspections"] = []
    if not isinstance(result.get("what_to_bring"), list):
        result["what_to_bring"] = []
    if not isinstance(result.get("requirements"), list):
        result["requirements"] = []
    if not isinstance(result.get("common_mistakes"), list):
        result["common_mistakes"] = []
    if not isinstance(result.get("pro_tips"), list):
        result["pro_tips"] = []
    if not isinstance(result.get("sources"), list):
        result["sources"] = []
    if not isinstance(result.get("companion_permits"), list):
        result["companion_permits"] = []

    job_lower = job_type.lower()

    if not isinstance(result.get("inspection_booking"), str) or not result.get("inspection_booking", "").strip():
        booking_bits = []
        if result.get("apply_url"):
            booking_bits.append(f"Schedule online at {result['apply_url']}")
        if result.get("apply_phone"):
            booking_bits.append(f"Phone: {result['apply_phone']}")
        booking_context = " ".join(result.get("pro_tips", []) + quirks)
        booking_context_lower = booking_context.lower()
        if "48-hour" in booking_context_lower or "48 hour" in booking_context_lower:
            booking_bits.append("48 hours advance notice required")
        elif "24-hour" in booking_context_lower or "24 hour" in booking_context_lower:
            booking_bits.append("24 hours advance notice required")
        elif booking_bits:
            booking_bits.append("Advance notice may be required, verify when booking")
        if booking_bits:
            result["inspection_booking"] = ". ".join(booking_bits) + "."

    if ("solar" in job_lower or "pv" in job_lower or "roof" in job_lower or "roofing" in job_lower) and not result.get("zoning_hoa_flag"):
        result["zoning_hoa_flag"] = (
            "Check HOA rules, historic district overlays, and local zoning before applying. "
            "Solar jobs may also face placement and visibility restrictions, even where state solar access laws limit outright bans."
        )

    # Server-side companion permit injection — guarantees high-value companions
    # even when AI omits them. Only adds if not already present (deduped by permit_type).
    existing_types = {c.get("permit_type", "").lower() for c in result.get("companion_permits", [])}
    injected = []

    COMPANION_MATRIX = [
        # (job keywords, permit_type, reason, certainty)
        (["hvac", "air condition", "ac unit", "heat pump", "furnace", "air handler"],
         "Electrical Permit",
         "Required for the electrical disconnect/reconnect circuit serving the HVAC system.",
         "almost_certain"),
        (["hvac", "air condition", "ac unit", "heat pump", "furnace"],
         "Gas Permit",
         "Required if the new unit is gas-fired or uses a gas line.",
         "likely"),
        (["bathroom remodel", "bath remodel", "bathroom renovation"],
         "Plumbing Permit",
         "Required for any fixture changes, drain relocation, or supply line work in the bathroom.",
         "almost_certain"),
        (["bathroom remodel", "bath remodel", "bathroom renovation"],
         "Electrical Permit",
         "Required for GFCI outlets, exhaust fan, and lighting circuit updates in wet areas.",
         "almost_certain"),
        (["kitchen remodel", "kitchen renovation"],
         "Plumbing Permit",
         "Required for sink, dishwasher, or gas line modifications.",
         "almost_certain"),
        (["kitchen remodel", "kitchen renovation"],
         "Electrical Permit",
         "Required for dedicated circuits (refrigerator, dishwasher, microwave, range).",
         "almost_certain"),
        (["panel upgrade", "panel replacement", "service upgrade", "electrical service", "200 amp", "200amp"],
         "Utility Coordination",
         "Your utility company must disconnect and reconnect service — coordinate before permit inspection.",
         "almost_certain"),
        (["solar", "solar panel"],
         "Electrical Permit",
         "Required for the inverter, electrical interconnection, and utility tie-in.",
         "almost_certain"),
        (["solar", "solar panel"],
         "Building Permit (Structural/Racking)",
         "Required to verify roof load capacity and panel attachment method.",
         "almost_certain"),
        (["ev charger", "electric vehicle", "level 2 charger"],
         "Electrical Permit",
         "Required for the dedicated 240V circuit and panel breaker installation.",
         "almost_certain"),
        (["generator"],
         "Electrical Permit",
         "Required for the transfer switch and electrical connection to the panel.",
         "almost_certain"),
        (["basement finish", "basement remodel", "basement conversion"],
         "Electrical Permit",
         "Required for outlets, lighting, and any subpanel work in the finished space.",
         "almost_certain"),
        (["basement finish", "basement remodel", "basement conversion"],
         "Plumbing Permit",
         "Required if adding a bathroom, wet bar, or floor drain to the basement.",
         "likely"),
        (["water heater"],
         "Gas Permit",
         "Required if replacing with or converting to a gas water heater.",
         "likely"),
        (["deck", "patio cover", "pergola"],
         "Electrical Permit",
         "Required if adding outlets, lighting, or ceiling fans to the deck or patio.",
         "likely"),
    ]

    verdict = result.get("permit_verdict", "YES")
    if verdict != "NO":  # Don't inject companions on no-permit results
        for keywords, ptype, reason, certainty in COMPANION_MATRIX:
            if any(kw in job_lower for kw in keywords):
                if ptype.lower() not in existing_types:
                    injected.append({"permit_type": ptype, "reason": reason, "certainty": certainty})
                    existing_types.add(ptype.lower())

    if injected:
        result["companion_permits"] = result.get("companion_permits", []) + injected

    # Normalize sources / trust metadata
    verified_data = (_verified_entry or {}).get("data", {}) if _verified_entry else {}
    verified_sources = verified_data.get("sources", []) if isinstance(verified_data, dict) else []
    result["sources"] = normalize_sources(
        result.get("sources", []),
        verified_sources,
        (_verified_entry or {}).get("source_url", "") if _verified_entry else "",
        result.get("apply_url", ""),
        result.get("apply_pdf", ""),
    )
    if _verified_entry and _verified_entry.get("verified_at"):
        result["last_verified_at"] = _verified_entry.get("verified_at")

    # Build disclaimer with freshness note
    _generated_at = result.get("_meta", {}).get("generated_at") or datetime.now().isoformat()
    try:
        _gen_date = datetime.fromisoformat(_generated_at).strftime("%b %d, %Y")
    except Exception:
        _gen_date = datetime.now().strftime("%b %d, %Y")
    result["disclaimer"] = (
        f"Data sourced {_gen_date}. Always verify current requirements directly with your "
        "local building department before starting work. Permit fees and requirements change frequently."
    )

    web_source_count = search_context.count("Source:") if search_context else 0
    missing_fields = compute_missing_fields(result)
    result["needs_review"] = bool(missing_fields)
    result["missing_fields"] = missing_fields

    confidence = str(result.get("confidence") or "medium").lower()
    # Vague fee also counts as a quality gap for confidence scoring
    if result.get("_fee_unverified"):
        missing_fields = list(set(missing_fields + ["fee_range"]))
        result["missing_fields"] = missing_fields
        result["needs_review"] = True
    if len(missing_fields) >= 3:
        confidence = downgrade_confidence(confidence, 2)
    elif missing_fields:
        confidence = downgrade_confidence(confidence, 1)
    # Web-search-only results (city not in KB) must never be "high" confidence
    # regardless of missing fields — the data source itself is unverified
    if city_match_level == "state" and confidence == "high":
        confidence = "medium"
    result["confidence"] = confidence
    result["confidence_reason"] = derive_confidence_reason(
        result, city_match_level, bool(_verified_entry), missing_fields, web_source_count
    )

    # Add metadata
    result["_meta"] = {
        "generated_at":    datetime.now().isoformat(),
        "response_ms":     elapsed,
        "cached":          False,
        "model":           "gpt-5.4-mini",
        "web_sources":     web_source_count,
        "city_match_level": city_match_level,
        "job_type":        job_type,
        "city":            city,
        "state":           state,
        "zip_code":        zip_code,
        "job_category":    job_category,
        "auto_verified":   bool(_verified_entry),
        "missing_fields":  missing_fields,
    }

    # ── Deduplicate companion_permits ─────────────────────────────────────────
    def _normalize_permit_name(name: str) -> str:
        """Normalize permit type name for dedup comparison."""
        if not name:
            return ""
        n = name.lower()
        for strip in [" (residential)", " — residential", " - residential",
                      " (commercial)", "residential ", "commercial ",
                      " permit", "permit "]:
            n = n.replace(strip, "")
        n = n.replace("structural / building", "building")
        n = n.replace("structural/building", "building")
        n = n.replace("structural racking", "building")
        n = n.replace("building/structural", "building")
        n = n.replace("electrical permit", "electrical")
        n = n.replace("mechanical permit", "mechanical")
        n = n.replace("building permit", "building")
        n = n.replace("gas permit", "gas")
        n = n.replace("hvac", "mechanical")
        n = re.sub(r'[^a-z0-9 ]', ' ', n)
        n = re.sub(r'\s+', ' ', n).strip()

        if any(p in n for p in ["utility coordination", "utility interconnection", "interconnection"]):
            return "utility"
        if any(p in n for p in ["plumbing", "water heater", "repipe"]):
            return "plumbing"
        if "gas" in n:
            return "gas"
        if any(p in n for p in ["mechanical", "hvac", "furnace", "air handler", "mini split"]):
            return "mechanical"
        if any(p in n for p in ["electrical", "service upgrade", "disconnect", "reconnect", "temporary power", "panel replacement", "panel upgrade"]):
            return "electrical"
        if any(p in n for p in ["building", "structural", "racking", "roof penetration", "roof penetrations"]):
            return "building"
        return n

    existing_permit_names = set()
    for p in result.get("permits_required") or []:
        existing_permit_names.add(_normalize_permit_name(p.get("permit_type", "")))

    seen_companions = set()
    deduped_companions = []
    for cp in result.get("companion_permits") or []:
        norm = _normalize_permit_name(cp.get("permit_type", ""))
        if norm in existing_permit_names:
            continue
        if norm in seen_companions:
            continue
        seen_companions.add(norm)
        deduped_companions.append(cp)
    result["companion_permits"] = deduped_companions

    score_structured = {
        'phone': result.get('apply_phone') or machine_structured.get('phone', ''),
        'portal_url': result.get('apply_url') or machine_structured.get('portal_url', ''),
        'fees': result.get('fee_range') if isinstance(result.get('fee_range'), list) else (machine_structured.get('fees') or ([result.get('fee_range')] if result.get('fee_range') else [])),
        'address': result.get('apply_address') or machine_structured.get('address', ''),
    }
    ready_score, ready_label, ready_missing = calculate_permit_ready_score(search_context or '', score_structured)
    result['permit_ready_score'] = ready_score
    result['permit_ready_label'] = ready_label
    result['permit_ready_missing'] = ready_missing
    result['checklist'] = generate_permit_checklist(job_type, city, state, result)
    result['rejection_patterns'] = get_rejection_patterns(city, state, job_type)
    search_change = get_search_cache(normalize_jurisdiction(city, state)[0], normalize_jurisdiction(city, state)[1], city_match_level=city_match_level)
    if search_change:
        cinfo = search_change.get('change_info') or {}
        result['change_detected'] = bool(cinfo.get('changed'))
        result['change_summary'] = cinfo.get('change_summary', '')
        result['similar_city_reference'] = bool(search_change.get('similar_city_note'))
        result['similar_city_note'] = search_change.get('similar_city_note', '')
    else:
        result['change_summary'] = ''
        result['similar_city_reference'] = False
        result['similar_city_note'] = ''
    if job_value is not None:
        result['job_value'] = job_value
        result['fee_calculator'] = calculate_exact_fee(job_type, city, state, float(job_value))
    else:
        result['fee_calculator'] = {'fee': None, 'formula': None, 'confidence': 'none', 'note': 'Provide job_value to calculate an exact fee where formulas are available.'}

    save_cache(key, job_type, job_category, city, state, zip_code, result)
    return result


# ─── Display Helper ───────────────────────────────────────────────────────────

def format_for_display(result: dict) -> str:
    lines = []
    meta = result.get("_meta", {})
    loc = result.get("location") or ", ".join([p for p in [meta.get("city"), meta.get("state")] if p])
    job = meta.get("job_type", result.get("job_summary", ""))
    cached = result.get("_cached") or meta.get("cached", False)
    conf = result.get("confidence", "?").upper()
    sources = meta.get("web_sources", 0)
    match = meta.get("city_match_level", "?")

    lines.append("=" * 60)
    lines.append(f"📋 PERMIT RESEARCH: {job.upper()}")
    lines.append(f"📍 Location: {loc} [data: {match}]")
    lines.append(f"🎯 Confidence: {conf}  {'⚡ CACHED' if cached else f'🌐 {sources} web source(s)'}")
    lines.append("=" * 60)

    if result.get("similar_city_note"):
        lines.append(f"\n🏙️  {result['similar_city_note']}")

    permits = result.get("permits_required", [])
    if permits:
        lines.append("\n🔖 PERMITS REQUIRED:")
        for p in permits:
            req = p.get("required", "?")
            icon = "✅" if req is True else ("⚠️" if req == "maybe" else "❌")
            lines.append(f"  {icon} {p.get('permit_type', 'Unknown')}")
            if p.get("portal_selection"):
                lines.append(f"     Select in portal: '{p['portal_selection']}'")
            if p.get("notes"):
                lines.append(f"     → {p['notes']}")

    office = result.get("applying_office", "")
    url = result.get("apply_url", "")
    phone = result.get("apply_phone", "")
    addr = result.get("apply_address", "")
    maps = result.get("apply_google_maps", "")
    if office:
        lines.append(f"\n🏢 APPLY TO: {office}")
    if url:
        lines.append(f"   🌐 Online: {url}")
    if phone:
        lines.append(f"   📞 Phone: {phone}")
    if addr:
        lines.append(f"   📬 Address: {addr}")
    if maps and not url:
        lines.append(f"   🗺️  Maps: {maps}")

    fee = result.get("fee_range", "")
    if fee:
        lines.append(f"\n💰 FEES: {fee}")
    fee_calc = result.get("fee_calculator") or {}
    if fee_calc.get("fee") is not None:
        lines.append(f"   Exact fee estimate: ${fee_calc['fee']:.2f}")
        if fee_calc.get("formula"):
            lines.append(f"   Formula: {fee_calc['formula']}")
        if fee_calc.get("note"):
            lines.append(f"   Note: {fee_calc['note']}")

    tl = result.get("approval_timeline", {})
    if tl:
        lines.append("\n⏱️  TIMELINE:")
        if tl.get("simple"):
            lines.append(f"   Simple: {tl['simple']}")
        if tl.get("complex"):
            lines.append(f"   Complex: {tl['complex']}")

    lic = result.get("license_required", "")
    if lic:
        lines.append(f"\n📜 LICENSE: {lic}")

    if result.get("permit_ready_score") is not None:
        lines.append(f"\n🧭 PERMIT READY SCORE: {result.get('permit_ready_score')}/100 — {result.get('permit_ready_label', '')}")
        for miss in result.get("permit_ready_missing", [])[:3]:
            lines.append(f"   Missing: {miss}")
    if result.get("change_summary"):
        lines.append(f"\n⚠️ CHANGE DETECTED: {result.get('change_summary')}")

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
        for tip in tips[:3]:
            lines.append(f"   • {tip}")

    mistakes = result.get("common_mistakes", [])
    if mistakes:
        lines.append("\n⚠️  AVOID:")
        for m in mistakes[:3]:
            lines.append(f"   • {m}")

    checklist = result.get("checklist", [])
    if checklist:
        lines.append("\n📝 PRE-APPLICATION CHECKLIST:")
        for item in checklist[:8]:
            lines.append(f"   • {item}")

    rejections = result.get("rejection_patterns", [])
    if rejections:
        lines.append("\n🚫 KNOWN REJECTION CAUSES (avoid these):")
        for item in rejections[:4]:
            lines.append(f"   • {item.get('pattern', '')} [{str(item.get('frequency', '')).upper()}]")
            if item.get("fix"):
                lines.append(f"     Fix: {item['fix']}")

    disc = result.get("disclaimer", "")
    if disc:
        lines.append(f"\n📌 {disc}")

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
