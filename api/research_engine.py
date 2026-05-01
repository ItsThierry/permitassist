#!/usr/bin/env python3
"""
PermitAssist — AI Research Engine v5
Improvements over v4:
  - Added Gemini 3 Pro as fallback if OpenAI is unavailable
  - Fallback uses Gemini JSON mode (response_mime_type: application/json)
  - Fallback is transparent — same result structure, same post-processing
"""

from __future__ import annotations

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

try:
    from .state_packs import get_state_expert_notes
except ImportError:  # server.py imports research_engine as a top-level module
    from state_packs import get_state_expert_notes

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except Exception:
    # python-dotenv is optional in production; environment variables may already
    # be injected by the hosting layer. Never block a lookup because .env loading
    # is unavailable.
    pass

client = None

def _get_openai_client() -> OpenAI:
    """Create the OpenAI fallback client lazily so importing tests never needs a key."""
    global client
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set — OpenAI fallback unavailable")
    if client is None or getattr(client, "api_key", None) != api_key:
        client = OpenAI(api_key=api_key)
    return client

# ─── Cache stats (in-memory, resets on restart) ───────────────────────────────
_cache_stats = {"hits": 0, "misses": 0}

def get_cache_hit_rate() -> dict:
    h, m = _cache_stats["hits"], _cache_stats["misses"]
    total = h + m
    return {
        "hits": h, "misses": m, "total": total,
        "hit_rate_pct": round(h / total * 100, 1) if total else 0
    }

# 2026-04-26: Engine swap based on 100-city × 5-trade benchmark (1,000 reqs):
#   gemini-3-flash-preview: 9.53/10  ($69/mo @ 50k lookups)
#   gpt-5.4-mini:            8.01/10  ($123/mo @ 50k lookups)
# Gemini won the synthetic benchmark with a +1.52 delta and was 44% cheaper.
#
# 2026-04-28 OVERRIDE: real-world Opus 4.7 reviews graded the engine 75%
# (residential ADU) and 30% (commercial restaurant TI) while running on
# Gemini-3-Flash-Preview as primary. The 9.53/10 internal benchmark didn't
# include commercial restaurant / office TI scenarios — exactly where
# Gemini-3-Flash falls apart. Swap: OpenAI gpt-5.4-mini becomes PRIMARY,
# Gemini-3-Flash drops to fallback. (gpt-5.5 / gpt-5.5-mini are not yet
# available via the OpenAI API as of 2026-04-28; bump when they ship.)
# Variable names retained for backwards-compat across the file; the
# *_fallback_model name now refers to the PRIMARY model. Rename pending.
_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if _GEMINI_API_KEY:
    genai.configure(api_key=_GEMINI_API_KEY)
_gemini_primary_model = "gemini-3-flash-preview"
_openai_fallback_model = "gpt-5.4-mini"

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")
# No hardcoded fallback for BRAVE_SEARCH_API_KEY — fail-fast if env var is missing
# rather than silently using a key checked into git history (was a security gap
# fixed 2026-04-26).
BRAVE_SEARCH_API_KEY = os.environ.get("BRAVE_SEARCH_API_KEY", "")
ACCELA_APP_ID = os.environ.get("ACCELA_APP_ID")
ACCELA_APP_SECRET = os.environ.get("ACCELA_APP_SECRET")

# Cache TTL ceiling (days). Permit rules and fees update quarterly in many
# cities; 30 days was too long. Combined with ETag polling (added 2026-04-26)
# for early invalidation when AHJ source pages change.
_PERMIT_CACHE_TTL_DEFAULT_DAYS = 14
_PERMIT_CACHE_TTL_HIGH_HITS_DAYS = 30
_PERMIT_CACHE_TTL_FRESH_REVERIFY_DAYS = 7
# How often to revalidate cached entries via HEAD/If-None-Match
_PERMIT_CACHE_ETAG_CHECK_FRACTION = 0.50  # at 50% of TTL, check ETag
# OpenAI / Gemini per-request timeouts (seconds). Was unset → defaulted to
# requests' 5-minute timeout, which would freeze the whole pipeline.
_OPENAI_REQUEST_TIMEOUT_S = 30
_GEMINI_REQUEST_TIMEOUT_S = 30
ACCELA_BASE_URL = "https://apis.accela.com"
ACCELA_DOCS_BASE_URL = "https://developer.accela.com/docs/api_reference"
_accela_token = ""
_accela_token_expiry = 0.0
_accela_agencies_cache: dict[str, object] = {"expires": 0.0, "result": []}
# Support RAILWAY_VOLUME_MOUNT_PATH or CACHE_DIR env var for persistent volumes
_default_data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
_data_dir = os.environ.get("CACHE_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or _default_data_dir
CACHE_DB       = os.path.join(_data_dir, "cache.db")
SERPER_CACHE_DB = os.path.join(_data_dir, "serper_cache.db")
KNOWLEDGE_DIR  = os.path.join(os.path.dirname(__file__), "..", "knowledge")
SERPER_TRUST_TTL_SECONDS = 7 * 24 * 60 * 60
SERPER_TRUST_MAX_QUERIES = 5
SERPER_TRUST_MAX_CONCURRENCY = 5
SERPER_TRUST_TOTAL_TIMEOUT_SECONDS = 30
SERPER_TRUST_REQUEST_TIMEOUT_SECONDS = 12

SOURCE_CLASS_OFFICIAL = "OFFICIAL"
SOURCE_CLASS_SUPPLEMENTARY = "SUPPLEMENTARY"
SOURCE_CLASS_EXCLUDED = "EXCLUDED"

# Serper source-domain policy. Keep these constants near the top of the file so
# competitor/source trust updates are easy to audit during hotfixes.
EXCLUDED_SOURCE_DOMAINS = {
    # Competitor / aggregator domains — never surface as our citations.
    "permitmint.com",
    "doineedapermit.org",
    "permitai.us",
    "permitflow.com",
    "permitlabs.ai",
    "withpulley.com",
    "oneclickcode.com",
    "buildoraiq.com",
    "shovels.ai",
    "upcodes.com",
    "codes.iccsafe.org",
    "madcad.com",
    "greenlancer.com",
    "lyra.solar",
    "gosolarapp.org",
    "archistar.ai",
    "planifly.co.uk",
    "planningpermission.ai",
    "planx.uk",
    "planningai.com.au",
    "servicetitan.com",
    "housecallpro.com",
    "buildertrend.com",
    "jobnimbus.com",
    "fieldedge.com",
    "houzz.com",
    "zillow.com",
    "realtor.com",
    "yelp.com",
    "angi.com",
    "thumbtack.com",
    "nextdoor.com",
    "facebook.com",
    "reddit.com",
    "medium.com",
    "substack.com",
    "quora.com",
    "youtube.com",
    # 2026-04-28: hallucinated / mis-cited data sources flagged in the live
    # grading run (Opus 4.7 grading pass against prod). These domains are real
    # .gov / .com but unrelated to building permits in the contexts where the
    # LLM cited them.
    "opendata.utah.gov",          # appeared as a Denver multifamily fee citation
    "developer.accela.com",       # Accela API docs page, not a permit source
    "huduser.gov",                # federal HUD research, not a permit source
    "coloradocoalition.org",      # appeared in a Denver permit context (RFP doc)
    # 2026-04-28: federal .gov domains unrelated to building/permitting.
    # Without this list, classify_source_url() auto-classifies any .gov as
    # OFFICIAL — and an Opus 4.7 review on a Phoenix restaurant TI caught the
    # engine citing ojp.gov (US DOJ Office of Justice Programs digitization
    # archive) as a fee source SIX times. These are LLM hallucinations the
    # source-grounding layer has no business surfacing for a contractor.
    "ojp.gov",
    "ncjrs.gov",
    "doj.gov",
    "justice.gov",
    "fbi.gov",
    "ice.gov",
    "dhs.gov",
    "uscis.gov",
    "nih.gov",
    "cdc.gov",
    "irs.gov",
    "ssa.gov",
    "va.gov",
    "uspto.gov",
    "treasury.gov",
    "state.gov",
    "fcc.gov",
    "ftc.gov",
    "sec.gov",
    "fda.gov",
    "noaa.gov",
    "usgs.gov",
    "nasa.gov",
    "ed.gov",
    "loc.gov",
    "nps.gov",
    # 2026-04-28: LLM-hallucinated academic / archival / research domains
    # caught across 4-city Opus 4.7 grading + 10-scenario re-grade. None has
    # any business in contractor-facing permit citations.
    "kauffman.org",                     # Seattle restaurant TI + Seattle ADU regression
    "huduser.gov",                      # federal HUD research
    "opendata.utah.gov",                # Denver multifamily — wrong state CSV
    "coloradocoalition.org",            # Denver multifamily — housing RFP, not code
    "rand.org",
    "brookings.edu",
    "urban.org",
    "pewresearch.org",
    "archive.org",                      # archive.org/details/dailycolonist1978 (Vegas)
    "web.archive.org",
    "scholar.google.com",
    "ncbi.nlm.nih.gov",
    "jstor.org",
    "cstx.gov",                         # College Station TX — only valid for College Station queries
    "dublin.ca.gov",                    # Dublin CA — only valid for Dublin queries
}

OFFICIAL_SOURCE_DOMAINS = {
    "nfpa.org",
    "iapmo.org",
    "ncqa.org",
    "ashrae.org",
    "iccsafe.org",
    "nationalbuildingcodes.com",
    # Recognized AHJ/authority domains that are not on .gov/.us.
    "cityofpasadena.net",
    "houstonpermittingcenter.org",
    "harrispermits.org",
    # 2026-04-28: major-city building-dept portals on .org/.com TLDs that ARE
    # the AHJ. Without this, classify_source_url() drops them to SUPPLEMENTARY
    # and they lose to LLM-emitted .gov junk in source ranking.
    "ladbs.org",                      # City of LA — Department of Building & Safety
    "ladbsservices2.lacity.org",      # LADBS online portal
    "ladbsservices.lacity.org",
    "ssps.lacity.org",                # LA online permitting
    "buildingla.lacity.org",
    "permits.lacounty.gov",           # LA County (unincorporated only)
    "epermits.miamidade.gov",
    "selfservice.miamidade.gov",
    "pdox.bouldercolorado.gov",
    "permits.charlottenc.gov",
    "permits.austintexas.gov",
    "abc.austintexas.gov",
    "abuilding.dallascityhall.com",
    "buildingeplans.austintexas.gov",
    "ipermits.minneapolismn.gov",
    "permits.sandiego.gov",
    "permits.santamonica.gov",
    "permits.berkeleyca.gov",
    "permits.cityofchicago.org",
    "etrakit.cityofchicago.org",
    "epermits.cityofchicago.org",
    # Common AHJ vendors (treat as official if AHJ uses them)
    "accela.com",
    "mygovernmentonline.org",
    "viewpointcloud.com",
    "etrakit.com",
    "openforms.com",
}

SUPPLEMENTARY_SOURCE_DOMAINS = {
    "roofingcontractor.com",
    "ecmweb.com",
    "contractingbusiness.com",
    "contractor.com",
    "plumbingmag.com",
    "ehcopumps.com",
    "statefarm.com",
    "municode.com",
}

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
    """Deduplicate + classify source URLs before they reach the contractor.

    2026-04-28: previously this only filtered non-http strings, which let
    LLM-hallucinated junk URLs (e.g. ojp.gov DOJ archive cited as a Phoenix
    building permit source) flow straight through to the result. Now applies
    classify_source_url() and rejects EXCLUDED domains.
    """
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
            try:
                if classify_source_url(src) == SOURCE_CLASS_EXCLUDED:
                    continue
            except Exception:
                continue
            seen.add(src)
            out.append(src)
    return out[:8]


# ─── Cross-jurisdiction source-locality filter ──────────────────────────────
# A1 (2026-04-28): hard-block source URLs that do not belong to the active AHJ
# tree. This is stricter than classify_source_url(): a real .gov can still be
# the wrong .gov (lebanon.in.gov on a Dallas query, ldh.la.gov on LA City, etc.).

_US_STATE_NAMES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "connecticut", "DE": "delaware",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho",
    "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi",
    "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "newhampshire", "NJ": "newjersey", "NM": "newmexico", "NY": "newyork",
    "NC": "northcarolina", "ND": "northdakota", "OH": "ohio", "OK": "oklahoma",
    "OR": "oregon", "PA": "pennsylvania", "RI": "rhodeisland", "SC": "southcarolina",
    "SD": "southdakota", "TN": "tennessee", "TX": "texas", "UT": "utah",
    "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "westvirginia",
    "WI": "wisconsin", "WY": "wyoming", "DC": "districtofcolumbia",
}

_UNIVERSAL_LOCALITY_DOMAINS = frozenset({
    "ada.gov", "access-board.gov", "energy.gov", "energystar.gov", "fema.gov", "epa.gov",
    "icc-safe.org", "iccsafe.org", "icc-es.org", "nfpa.org", "iapmo.org", "ashrae.org",
})

_PLATFORM_VENDOR_DOMAINS = frozenset({"accela.com", "publicstuff.com", "citizenserve.com"})

_AHJ_DOMAIN_ALLOWLIST: dict[tuple[str, str], set[str]] = {
    ("phoenix", "az"): {"phoenix.gov", "az.gov", "azdeq.gov", "azroc.gov", "roc.az.gov"},
    ("las vegas", "nv"): {"clarkcountynv.gov", "lasvegasnevada.gov", "nv.gov", "nvcontractorsboard.com"},
    ("clark county", "nv"): {"clarkcountynv.gov", "nv.gov", "nvcontractorsboard.com"},
    ("seattle", "wa"): {"seattle.gov", "kingcounty.gov", "wa.gov", "lni.wa.gov", "ecology.wa.gov"},
    ("los angeles", "ca"): {"lacity.org", "ladbs.org", "ca.gov", "cslb.ca.gov"},
    ("dallas", "tx"): {"dallascityhall.com", "dallascounty.org", "tx.gov", "tdlr.texas.gov", "texas.gov", "txdmv.gov"},
}

# Explicit wrong-AHJ regressions from cda4106/four-city review. Keep these ahead
# of generic state-domain rules (e.g. pw.lacounty.gov is a CA .gov, but not LA City).
_CITY_LOCALITY_EXCLUSIONS = {
    ("los angeles", "ca"): {"pw.lacounty.gov", "dpw.lacounty.gov", "lacounty.gov", "ldh.la.gov"},
    ("dallas", "tx"): {"lebanon.in.gov", "govinfo.gov"},
}


def _city_match_tokens(city: str, state: str) -> set[str]:
    tokens: set[str] = set()
    c = (city or "").lower().strip()
    if not c:
        return tokens
    tokens.update({c, c.replace(" ", ""), c.replace(" ", "_"), c.replace(" ", "-")})
    aliases = {
        ("los angeles", "CA"): {"lacity", "ladbs", "ladwp"},
        ("new york", "NY"): {"nyc", "newyork", "nycgov"},
        ("san francisco", "CA"): {"sfgov", "sfdbi"},
        ("phoenix", "AZ"): {"phoenix", "shapephx"},
    }
    tokens.update(aliases.get((c, (state or "").upper().strip()), set()))
    return tokens


def _state_match_tokens(state: str) -> set[str]:
    s = (state or "").upper().strip()
    if not s or s not in _US_STATE_NAMES:
        return set()
    return {s.lower(), _US_STATE_NAMES[s], _US_STATE_NAMES[s].replace(" ", "")}


def _host_matches_any(host: str, domains: set[str] | frozenset[str] | tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains if d)


def _verified_city_domains(city: str, state: str) -> set[str]:
    """Best-effort domains from knowledge/verified_cities.db for all verified cities."""
    out: set[str] = set()
    try:
        db_path = os.path.join(KNOWLEDGE_DIR, "verified_cities.db")
        if not os.path.exists(db_path):
            return out
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT portal_url, fee_schedule_url, application_url FROM verified_cities WHERE lower(city)=? AND upper(state)=? LIMIT 1",
            ((city or "").lower().strip(), (state or "").upper().strip()),
        ).fetchone()
        conn.close()
        if not row:
            return out
        for key in ("portal_url", "fee_schedule_url", "application_url"):
            domain = _normalized_source_domain(row[key] or "")
            if domain:
                out.add(domain)
    except Exception:
        return out
    return out


def locality_allowed_domains(city: str, state: str) -> set[str]:
    city_key = (city or "").lower().strip()
    state_key = (state or "").lower().strip()
    domains = set(_AHJ_DOMAIN_ALLOWLIST.get((city_key, state_key), set()))
    domains |= _verified_city_domains(city, state)
    if state_key:
        domains.add(f"{state_key}.gov")
        domains.add(f"{state_key}.us")
    return domains


def is_url_allowed_for_locality(url: str, city: str, state: str, result: dict | None = None) -> bool:
    domain = _normalized_source_domain(url)
    if not domain:
        return False
    city_key = (city or "").lower().strip()
    state_key = (state or "").lower().strip()
    if _host_matches_any(domain, _CITY_LOCALITY_EXCLUSIONS.get((city_key, state_key), set())):
        return False
    if _host_matches_any(domain, _UNIVERSAL_LOCALITY_DOMAINS):
        return True
    allowed = locality_allowed_domains(city, state)
    if _host_matches_any(domain, allowed):
        return True
    if classify_source_url(url) == SOURCE_CLASS_EXCLUDED:
        return False
    if _host_matches_any(domain, _PLATFORM_VENDOR_DOMAINS):
        hay = " ".join(str((result or {}).get(k, "")) for k in ("apply_url", "applying_office", "portal_name", "permit_summary")).lower()
        return any(vendor in hay for vendor in _PLATFORM_VENDOR_DOMAINS if _domain_matches(domain, vendor))
    # Conservative fallback for non-seeded cities: official host/path with city token AND same-state TLD.
    parsed = urlparse(url)
    full = f"{domain} {(parsed.path or '').lower()}"
    city_tokens = {t for t in _city_match_tokens(city, state) if len(t) >= 3}
    if city_tokens and any(t in full for t in city_tokens):
        if state_key and (domain.endswith(f".{state_key}.gov") or domain.endswith(f".{state_key}.us")):
            return True
        if domain.endswith((".gov", ".us")) and any(tok in full for tok in _state_match_tokens(state) if len(tok) >= 3):
            return True
    return False


def filter_sources_by_locality(sources: list[str], city: str, state: str, result: dict | None = None) -> list[str]:
    if not isinstance(sources, list) or not sources:
        return sources or []
    kept: list[str] = []
    for src in sources:
        if not isinstance(src, str) or not src.startswith("http"):
            continue
        if is_url_allowed_for_locality(src, city, state, result=result):
            kept.append(src)
    return kept


def _locality_placeholder(result: dict, city: str) -> str:
    ahj = result.get("applying_office") or (f"{city} building department" if city else "the building department")
    phone = result.get("building_dept_phone") or result.get("phone") or result.get("office_phone") or ""
    return f"[verify with {ahj}{(' ' + phone) if phone else ''}]"


def _strip_nonlocal_urls_from_text(text: str, city: str, state: str, result: dict, block: bool = False) -> str:
    if not isinstance(text, str) or not text:
        return text
    placeholder = _locality_placeholder(result, city)
    def _replace(match):
        raw = match.group(0)
        url = raw.rstrip('.,;:!?')
        suffix = raw[len(url):]
        if is_url_allowed_for_locality(url, city, state, result=result):
            return raw
        return ("" if block else placeholder) + suffix
    return _URL_REGEX.sub(_replace, text)


def apply_source_locality_hard_block(result: dict, city: str, state: str) -> dict:
    """Apply A1 locality filtering to citations, *_source URL fields, and prose."""
    if not isinstance(result, dict):
        return result
    dropped: list[dict] = []

    pre_sources = list(result.get("sources") or []) if isinstance(result.get("sources"), list) else []
    result["sources"] = filter_sources_by_locality(pre_sources, city, state, result=result)
    for src in pre_sources:
        if src not in result["sources"]:
            dropped.append({"field": "sources", "url": src})

    for key, value in list(result.items()):
        if key.endswith("_source") and isinstance(value, str) and value.startswith("http"):
            if not is_url_allowed_for_locality(value, city, state, result=result):
                dropped.append({"field": key, "url": value})
                result[key] = None

    text_policy = {
        "fee_range": False,
        "confidence_reason": True,
        "permit_summary": False,
        "pro_tip": False,
        "watch_out": False,
        "job_summary": False,
    }
    for field, block in text_policy.items():
        if isinstance(result.get(field), str):
            cleaned = _strip_nonlocal_urls_from_text(result[field], city, state, result, block=block)
            if cleaned != result[field]:
                result[field] = re.sub(r"\s+", " ", cleaned).strip()
                dropped.append({"field": field, "url": "free_text"})

    for field in ("pro_tips", "common_mistakes", "what_to_bring", "requirements", "documents_needed"):
        if isinstance(result.get(field), list):
            result[field] = [
                _strip_nonlocal_urls_from_text(item, city, state, result, block=False) if isinstance(item, str) else item
                for item in result[field]
            ]

    if dropped:
        result["_sources_locality_dropped"] = (result.get("_sources_locality_dropped") or []) + dropped[:10]
    return result


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


# 2026-04-28: free-text URL sanitizer. The LLM occasionally emits hallucinated
# URLs INSIDE free-text fields (fee_range, confidence_reason, pro_tips notes)
# that survive the source-grounding filter on result["sources"] because that
# filter only operates on the structured sources list, not on prose. Real
# four-city Opus 4.7 review caught these leaks:
#   • Phoenix restaurant TI: fee_range said "verify in https://www.ojp.gov/..."
#     × 6 places (US DOJ digitization archive)
#   • Vegas restaurant TI: "verify in archive.org/dailycolonist1978" × 6 places
#   • Seattle restaurant TI: "verify in kauffman.org/.../NETS_US_PublicFirms.xlsx" × 6 places
# Strategy: find every URL in the text via regex and pass it through
# classify_source_url(). If EXCLUDED, replace the URL token with the AHJ name
# placeholder so the surrounding sentence still reads sensibly.
_URL_IN_TEXT_RE = re.compile(r'https?://[^\s\)\]\}\,]+', re.IGNORECASE)


def strip_junk_urls_from_text(text: str, ahj_name: str = "city building dept") -> str:
    """Remove URLs from a free-text field if their host is EXCLUDED.

    Replaces matched URLs with `[verify with ahj_name]` so the sentence stays
    readable. Returns the text unchanged if no junk URLs are present.
    """
    if not isinstance(text, str) or not text:
        return text
    placeholder = f"[verify with {ahj_name}]"

    def _replace(match):
        url = match.group(0).rstrip('.,;:')
        try:
            cls = classify_source_url(url)
            if cls == SOURCE_CLASS_EXCLUDED:
                return placeholder
        except Exception:
            return placeholder
        return match.group(0)

    return _URL_IN_TEXT_RE.sub(_replace, text)


def sanitize_free_text_url_leaks(result: dict, city: str = "", state: str = "") -> dict:
    """Strip hallucinated URLs from prose fields. Mutates result in place.

    Called near the end of research_permit() after the LLM has populated
    text fields and before the validation gate runs.
    """
    ahj_name = result.get("applying_office") or (
        f"{city} building department" if city else "your local building department"
    )
    for fld in ("fee_range", "confidence_reason", "permit_summary", "job_summary"):
        v = result.get(fld)
        if isinstance(v, str) and v:
            result[fld] = strip_junk_urls_from_text(v, ahj_name=ahj_name)
    # List-of-strings fields too.
    for fld in ("pro_tips", "common_mistakes", "what_to_bring", "requirements", "documents_needed"):
        v = result.get(fld)
        if isinstance(v, list):
            result[fld] = [
                strip_junk_urls_from_text(item, ahj_name=ahj_name) if isinstance(item, str) else item
                for item in v
            ]
    return result


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


# 2026-04-26: Validation cache for Accela portal URLs. We've never end-to-end
# tested whether the URLs we return actually let a contractor submit an
# application — Boban explicitly flagged this. As a first defense, do a HEAD
# check at portal-build time and cache 4xx/5xx results so we don't re-hit.
# In-process dict (process-lifetime cache, ~1 hour TTL).
_ACCELA_URL_VALIDATION_CACHE: dict = {}
_ACCELA_URL_VALIDATION_TTL_S = 3600  # 1 hour


def _validate_accela_portal_url(url: str, *, timeout: float = 4.0) -> dict:
    """HEAD-check an Accela portal URL. Returns dict with:
        - alive: bool (True if 2xx/3xx)
        - status: int or None
        - reason: short string for logging/UI
        - checked_at: epoch
    Cached in-process for _ACCELA_URL_VALIDATION_TTL_S seconds.
    Never raises.
    """
    if not url:
        return {"alive": False, "status": None, "reason": "empty_url", "checked_at": time.time()}
    cached = _ACCELA_URL_VALIDATION_CACHE.get(url)
    now = time.time()
    if cached and now - cached.get("checked_at", 0) < _ACCELA_URL_VALIDATION_TTL_S:
        return cached
    info: dict = {"alive": False, "status": None, "reason": "unknown", "checked_at": now}
    try:
        # Some Accela portals 405 on HEAD; fall back to GET if so.
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
        if resp.status_code == 405:
            resp = requests.get(url, timeout=timeout, allow_redirects=True, stream=True)
            try:
                resp.close()
            except Exception:
                pass
        info["status"] = resp.status_code
        if 200 <= resp.status_code < 400:
            info["alive"] = True
            info["reason"] = "ok"
        elif resp.status_code in (401, 403):
            # Auth-walled but the page exists — treat as alive for our
            # purposes (contractor will see a login form, that's expected).
            info["alive"] = True
            info["reason"] = f"auth_required_{resp.status_code}"
        else:
            info["reason"] = f"http_{resp.status_code}"
    except requests.RequestException as e:
        info["reason"] = f"net_error: {type(e).__name__}"
    _ACCELA_URL_VALIDATION_CACHE[url] = info
    return info


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

    # 2026-04-26: HEAD-check the portal URL before claiming it as a source.
    # Cached in-process. If dead, downgrade portal claim so we don't hand
    # contractors broken links.
    portal_alive = True
    portal_status_reason = "not_checked"
    if portal_url:
        v = _validate_accela_portal_url(portal_url)
        portal_alive = bool(v.get("alive"))
        portal_status_reason = str(v.get("reason") or "unknown")
        if not portal_alive:
            print(f"[accela] Portal URL FAILED validation for {agency_name}: {portal_url} → {portal_status_reason}")
            # Don't return the URL but do return the agency match so downstream
            # can still benefit from agency name / display.
            portal_url = ""

    print(f"[accela] Matched agency: {agency_name} | Portal: {portal_url or 'none (not hostedACA or dead)'} | reason: {portal_status_reason}")

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
        "portal_validation_reason": portal_status_reason,
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
        "Rapid shutdown compliance documentation per NEC 690.12",
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

# Scope-specific inspection / submittal items that fire when matching tokens
# appear in the job description. Layered ON TOP of CHECKLIST_TRADE so a
# "solar+ESS" job gets both solar items AND battery-specific items.
# Added 2026-04-27 after Opus 4.7 review of Montpelier VT 12kW PV + 2 Powerwall
# job flagged the inspection checklist as generic NEC items rather than
# solar/ESS-specific (no rapid-shutdown labeling, no NFPA 855 clearances).
CHECKLIST_SCOPE = {
    "battery_ess": {
        "tokens": ["battery", "ess", "energy storage", "powerwall", "encharge", "lg chem", "sonnen", "fortress"],
        "items": [
            "ESS clearances per NFPA 855 (≥3 ft from openings, doors, windows; verify state/AHJ override)",
            "Battery commissioning report from manufacturer (required for inspection sign-off)",
            "ESS hazardous-voltage labeling per UL 9540 / 9540A on each enclosure",
            "AC disconnect labeling 'Warning: Dual Power Source' per NEC 705.10 at the main service",
            "AC disconnect within sight of the inverter (NEC 690.13 / 705.20)",
            "Working clearance at ESS enclosure: 30 in wide × 36 in depth × 6.5 ft height (NEC 110.26)",
            "ESS marking at the main service identifying battery feed-in source per NEC 706.10",
        ],
    },
    "ground_mount_solar": {
        "tokens": ["ground-mount", "ground mount", "ground-mounted", "ground mounted", "ground array", "pole-mount", "pole mount"],
        "items": [
            "Foundation depth ≥ {state} frost line (e.g. 42 in central VT, 48 in interior MN, 30 in coastal NC)",
            "Helical pile installation report (torque values, depth, manufacturer letter) if helical foundation used",
            "Trench depth per NEC 300.5 — minimum 24 in direct burial / 18 in PVC Schedule 40 conduit",
            "Equipment grounding electrode at the array per NEC 250.32 (auxiliary GEC)",
            "Array-to-house conduit transition box accessible for inspection",
            "Setback from property lines verified against zoning (often 5–10 ft)",
        ],
    },
    "rapid_shutdown": {
        "tokens": ["solar", "pv", "photovoltaic"],
        "items": [
            "Rapid shutdown initiator at service disconnect labeled per NEC 690.56(C) (red label, 1 in lettering)",
            "DC conductors inside the array boundary terminated within 30 sec of shutdown initiation",
            "Module-level rapid shutdown devices (MLPE) if voltage outside the array reduced to ≤80V",
        ],
    },
    "ev_charger": {
        "tokens": ["ev charger", "ev charging", "level 2", "240v charger", "240 v charger", "evse", "wallbox", "tesla wall connector"],
        "items": [
            "Continuous-load sized at 125% per NEC 625.41 (e.g. 40A charger needs 50A breaker + 6 AWG)",
            "GFCI protection per NEC 625.54 for receptacle-fed installations",
            "Disconnect within sight or capable of being locked OPEN (NEC 625.43) for ≥60A chargers",
            "Load calculation showing service can carry the new circuit (or EVEMS if load-managed)",
            "Charger circuit dedicated — no shared neutrals per NEC 625.40",
        ],
    },
    "panel_upgrade": {
        "tokens": ["panel upgrade", "service upgrade", "200 amp", "200amp", "400 amp", "400amp", "subpanel", "sub-panel", "main panel"],
        "items": [
            "Working space at panel: 30 in wide × 36 in depth × 6.5 ft height (NEC 110.26)",
            "Neutral-ground bond at service only (not at subpanels) per NEC 250.142",
            "AFCI protection on all 120V branch circuits per NEC 210.12 (current code cycle)",
            "Equipment grounding conductor sized to NEC 250.122 table",
            "Service-entrance labeling: amperage rating, AIC rating, and short-circuit current per NEC 110.16/110.24",
        ],
    },
    "tankless_water_heater": {
        "tokens": ["tankless", "tankless water heater", "tankless wh", "instantaneous water heater", "navien", "rinnai"],
        "items": [
            "Gas line sized for tankless BTU/hr (typically 150,000–199,000 BTU vs ~40,000 for tank — verify pipe size)",
            "Concentric vent or category III/IV vent per manufacturer; clearance to property lines",
            "Condensate drain to approved location (some need neutralizer for condensing units)",
            "Dedicated 120V outlet within 6 ft if electric ignition / control board",
            "Isolation valves on hot AND cold for descaling (manufacturer requirement, AHJ commonly verifies)",
        ],
    },
    "gas_water_heater": {
        "tokens": ["gas water heater", "gas wh", "natural gas water heater", "lp water heater"],
        "items": [
            "T&P relief valve discharge piped to floor / drain pan per IPC 504",
            "Drain pan with ≥1 in side height for water heaters above finished space",
            "Garage installation: 18 in elevation OR FVIR-listed unit (IPC 504.6)",
            "Seismic strapping (CA/OR/WA): two straps, upper third + lower third",
            "Combustion air provisions or direct-vent unit — verify per IFGC 304",
        ],
    },
    "adu_specific": {
        "tokens": ["adu", "accessory dwelling", "granny flat", "in-law suite", "garage conversion", "junior accessory", "jadu"],
        "items": [
            "Smoke + CO alarms in every sleeping room AND each level (interconnected if hardwired)",
            "Egress window in every sleeping room: 5.7 sq ft net (5.0 if grade), 24 in min height, 20 in min width, sill ≤44 in",
            "Fire-rated assembly between dwelling units (1-hour wall + 1-hour ceiling typical)",
            "Independent or accessory-metered electrical service (varies by state — confirm with utility)",
            "Energy code compliance for the new conditioned space (Title 24 in CA; IECC adopted version elsewhere)",
        ],
    },
    "pool_spa": {
        "tokens": ["swimming pool", "in-ground pool", "above-ground pool", "spa", "hot tub", "jacuzzi"],
        "items": [
            "Barrier / fence at least 48 in (60 in some jurisdictions) per IRC AG105",
            "Self-closing self-latching gate latch ≥54 in above ground",
            "Equipotential bonding grid per NEC 680.26 (8 AWG copper, ≤6 ft from water)",
            "GFCI protection on all pool/spa equipment circuits per NEC 680.6",
            "Anti-entrapment drain cover per Virginia Graeme Baker Act + manufacturer cert ≤5 yrs",
            "Depth markers visible on deck and inside the pool per IRC AG107.4 / IBC 3109",
            "Ladder, handrail, or steps within first 24 in of depth per IRC AG107.5",
            "Slip-resistant decking surface within 4 ft of pool perimeter per CPSC guidance",
            "Perimeter overflow gutter or skimmer system for commercial / public pools per IBC 3109",
            "Water-chemistry log: free chlorine + pH recorded ≥3×/day for commercial / public pools per local health code",
            "Underwater luminaires GFCI-protected and rated for wet locations per NEC 680.23",
            "Pool heater + pump combustion-air clearance per manufacturer; gas-line sized per IFGC 402",
        ],
    },
    "commercial_restaurant": {
        # 2026-04-28: tightened tokens. Removed generic "tenant improvement"
        # and " ti " — they match ANY commercial TI (office, retail, industrial)
        # and were causing the 41-item commercial-kitchen pack (UL 710 / NFPA 96
        # / ANSUL / grease) to leak into Chicago office TI and Houston retail TI
        # responses (graded 64 and 58 respectively). Restaurant-specific tokens
        # only — primary-scope router handles broader commercial routing.
        "tokens": [
            "restaurant", "cafe", "fast-casual", "fast casual", "food service",
            "commercial kitchen", "type i hood", "kitchen hood", "grease trap",
            "grease interceptor", "ansul", "fryer", "charbroiler", "griddle",
            "walk-in cooler", "walk in cooler", "walk-in freezer",
            "bar buildout", "tavern", "brewery", "kitchen build-out",
            "dine-in", "dine in", "food establishment",
        ],
        "items": [
            "Type I commercial exhaust hood listed/labeled per UL 710 with NFPA 96 compliance and AHJ-approved capture velocity per IMC 507",
            "ANSUL R-102 (or equivalent UL 300) hood suppression: post-install discharge test certificate + every-6-month cleaning + annual inspection per NFPA 96 §11",
            "ANSUL discharge auto-shutoffs all hood gas + electric fuel sources per IMC 507.7 — verify with AHJ during witness test",
            "Grease interceptor sized per IPC 1003.3.4 by drainage fixture units (4-comp + mop typically 50+ GPM hydromechanical OR 750–1,500 gal exterior gravity); located ≤25 ft developed length from fixtures",
            "Makeup air system balanced to ±10% of hood exhaust per IMC 506.3.4; tempered if > 35°F differential to prevent backdraft",
            "Indirect waste with air gap on dishwasher, ice maker, prep sink, and steam kettle per IPC 802; commercial dishwasher backflow prevention (RPZ) per IPC 608.13.2",
            "Dedicated mop sink with hose bib in janitor closet (IBC 902.3 / local health code) — required before health dept sign-off",
            "ADA fixture counts per IBC Table 2902.1 + 60 in turning radius + lavatory pipe wrap insulation per ADA §606.5 + mirror reflective edge ≤40 in AFF per ADA §603.3",
            "Local health department food establishment plan review + final inspection sign-off REQUIRED before Certificate of Occupancy (parallel filing — file early)",
            "TABC / state liquor authority clearance if alcohol service — separate filing from building permit",
            "Walk-in cooler envelope per IECC 403.10.1 (≥R-25 medium-temp panels, ≥R-32 freezer panels); door gasket seal verification at final",
            "Existing sprinkler system reuse: hydraulic recalc for new layout + current 5-year cert per NFPA 25; new heads in cooking equipment area must be K-25+ per NFPA 13",
            "Floor drains within 5 ft of kitchen equipment per IPC 411 with backwater valves where required",
            "Fire alarm + emergency lighting per NFPA 72/101 with current battery capacity test on file",
            # 2026-04-28: additions sourced from Forge / commercial-state-pack-draft.
            "Change of occupancy memo (B/M → A-2): include occupant load + exits + restroom count + sprinkler/fire-alarm impacts per IEBC §1001.2/1001.3 + IBC §303.3 (Group A-2)",
            "Maricopa County (or local equivalent) Environmental Services Department food-establishment plan review + final inspection sign-off REQUIRED before opening — coordinate with City CO schedule (MCESD: maricopa.gov)",
            "Backflow prevention on EVERY food-service water connection: carbonated beverage (ASSE 1022 dual check), chemical dispenser at mop/service sinks, hose bibbs, dish-machine/ice/RO/espresso, pre-rinse — testable assemblies certified annually",
            "Outdoor patio / sidewalk dining: verify city zoning (use permit may apply, e.g. Phoenix ZO §1207); include patio in occupant load + exiting + accessible route + sprinkler/awning review",
            "Phoenix-specific (or equivalent local rule): contractor must be AZ ROC licensed AND associated with the Phoenix SHAPE PHX permit account before submittal (A.R.S. Title 32 Ch. 10) — frequently-missed pre-submittal blocker",
            "Realistic fee warning: a 3,200 sq ft restaurant TI in Phoenix typically lands $8K–$25K+ across plan review + permit + fire + utility + health + zoning. Reject token min-fee outputs; calculate from the live fee schedule before quoting",
        ],
    },
    "change_of_occupancy": {
        "tokens": [
            "change of occupancy", "change of use", "change occupancy",
            "b to a-2", "office to restaurant", "retail to restaurant",
            "new occupancy classification",
        ],
        "items": [
            "Occupancy reclassification: identify existing approved occupancy + proposed group; require code-official approval + new/updated Certificate of Occupancy (IEBC §§1001.1, 1001.2, 1001.3; IBC Ch. 3)",
            "Height/area limit recheck: compare new occupancy hazard category to existing construction type, allowable area, frontage/sprinkler increases, fire-wall strategy (IEBC §1011.5; IBC Ch. 5)",
            "Fire-resistance + separation reassessment: re-verify fire barriers, horizontal assemblies, exterior wall ratings/openings, shaft enclosures, mixed-occupancy separations for the new hazard category (IEBC §§1011.1, 1011.5.3, 1011.6/7; IBC Ch. 7)",
            "Fire protection re-run: sprinkler, fire alarm, hood suppression, and monitoring requirements at the new occupancy + IBC/IFC Ch. 9 thresholds (IEBC §§1004.1, 1011.2; IFC Ch. 9)",
            "Means of egress recalculation: occupant load + number of exits + egress capacity + travel distance + common path + dead ends + door hardware updated to new occupancy (IEBC §§1005.1, 1011.4; IBC Ch. 10)",
            "Accessibility / path-of-travel trigger: alterations to primary-function areas trigger ADA upgrades to altered area + serving amenities (2010 ADA §202.4 + 28 CFR §36.403)",
            "Mechanical ventilation/exhaust recheck against the new occupancy: IMC ventilation, commercial-kitchen exhaust + makeup air, hazardous exhaust, energy compliance (IEBC §1008.1; IMC Ch. 4-6 + §507)",
            "Plumbing demand + fixture-count recheck: water supply, sanitary load, fixture count, food-handling waste, grease/oil interceptors, chemical-waste approval (IEBC §§1009.1-1009.4; IPC Ch. 4 + 10)",
        ],
    },
    "ada_path_of_travel": {
        "tokens": [
            "ada path-of-travel", "path of travel", "path-of-travel",
            "20% rule", "20 percent rule", "primary function area",
            "alteration", "renovation",
        ],
        "items": [
            "Identify altered primary-function area: if scope alters an area where major activity occurs, an accessible path of travel to the altered area is required unless technically infeasible / disproportionate (2010 ADA §202.4; 28 CFR §36.403(a))",
            "20% disproportionality cap: ADA path-of-travel work exceeding 20% of alteration cost is disproportionate at the federal level, but lower-cost items must still be prioritized (28 CFR §36.403(f)(1))",
            "Accessible exterior route: site arrival points + accessible parking/passenger loading + curb ramps + walking surfaces + ramps/landings + slope/cross-slope + door clearances to altered area (2010 ADA §§206.2.1, 206.4, 403, 404, 405, 406)",
            "Restroom path + restroom upgrades: if restrooms serve the altered area, accessible route + door + turning space + lavatory + water closet + grab bars + clearances + signage (2010 ADA §§213, 603-606, 609, 703)",
            "Parking + passenger loading: recalculate accessible-stall count + van space + access aisle + signage for affected parking serving the altered area (2010 ADA §§208, 502, 503; 28 CFR §36.403(e)(1))",
            "Drinking fountains, phones, and signage along the path: tactile/visual signs, room ID, directional, fountain units, public phones if any (2010 ADA §§211, 216, 602, 703, 704)",
            "Doors, thresholds, and hardware on the route: clear width + maneuvering clearance + opening force + thresholds + lever/operable hardware + protruding objects (2010 ADA §§303, 307, 309, 404)",
        ],
    },
    "commercial_office_ti": {
        "tokens": [
            "office tenant improvement", "office ti", "office buildout", "executive suite",
            "professional office", "law office", "medical office tenant", "dental office tenant",
            "co-working", "coworking", "shared workspace office",
        ],
        "items": [
            "ADA fixture counts per IBC Table 2902.1 (1 toilet + 1 lavatory per 1–25 occupants per sex; verify current edition)",
            "Demising wall fire-rated assembly between tenants per IBC 706.3 (1-hour typical; 2-hour for higher-occupancy adjacencies)",
            "Accessible-parking ratio per IBC 1106 + 1106.4 (1 per 25 spaces; 1 of every 6 must be van-accessible)",
            "Exit access travel distance ≤200 ft (sprinklered) / ≤75 ft (non-sprinklered) per IBC 1017",
            "Sprinkler head additions or relocations: hydraulic recalc + permit + 5-year cert update per NFPA 13/25",
            "Lighting power density per IECC C405 (e.g. ≤0.79 W/sf for open-plan office)",
            "Mechanical zoning per IMC 403 with tenant-controlled VAV / ductless on each occupied space",
            "Fire alarm initiating-device coordination with base building (smoke detectors at HVAC return per NFPA 72 §17.7)",
            "Accessible route from accessible entrance to all tenant areas per ADA §206 — no thresholds >½ in unbeveled",
            "Means of egress: 2 exits required if occupant load >49 OR travel distance exceeds limits per IBC 1006",
            "Energy code envelope + windows + insulation comply with IECC C402 if exterior wall is touched",
            "Asbestos / lead survey for buildings pre-1980 if disturbing partitions, ceilings, or flooring",
            "Glass + glazing: safety glazing per IBC 2406 in any pane within 24 in of doors or 60 in of floor",
        ],
    },
    "commercial_medical_clinic_ti": {
        "tokens": [
            "medical clinic", "medical office tenant", "clinic tenant improvement", "clinic ti",
            "dental clinic", "dental office tenant", "health clinic", "exam room", "exam rooms",
            "treatment room", "procedure room", "medical gas", "med gas", "x-ray", "radiology",
        ],
        "items": [
            "Commercial clinic TI building permit: identify B / ambulatory-care / outpatient occupancy basis, suite size, occupant load, egress, rated corridors, and certificate-of-occupancy conditions before submittal.",
            "Exam-room plumbing: show hand sinks, accessible restroom fixture count, backflow protection, indirect waste, sterilization-room fixtures, and dental/medical equipment utility connections.",
            "Medical gas / nitrous / oxygen: submit NFPA 99-style outlet schedule, alarms, zone valves, source equipment, pressure test, and verifier documentation when gas systems are in scope.",
            "Clinic HVAC / infection-control: provide room-by-room ventilation schedule, exhaust, pressure relationships, filtration, and construction infection-control notes where procedure/sterilization/lab spaces exist.",
            "ADA path-of-travel: verify accessible route, reception/check-in counter, exam-room door/turning clearances, toilet rooms, parking/passenger loading, signage, and 20% disproportionality cap documentation.",
            "X-ray / radiology: obtain shielding design or state radiation-control registration where equipment is installed; coordinate lead-lined assemblies, warning lights/signage, and electrical requirements.",
            "Fire/life-safety: coordinate fire alarm notification appliances, sprinkler head layout, emergency lighting/exit signs, suite separation, and storage/oxygen hazards with Fire Prevention.",
            "Health-care licensing / local health review: confirm whether the clinic type needs state or local health-care licensing approval separate from the building permit before opening.",
        ],
    },
    "commercial_retail_ti": {
        "tokens": [
            "retail tenant improvement", "retail ti", "retail buildout", "store buildout",
            "showroom", "boutique", "retail space", "shop tenant",
            "commercial retail", "mall tenant", "strip mall tenant",
        ],
        "items": [
            "Occupancy load calc per IBC 1004 (mercantile = 60 sf gross/occupant for sales floor; 300 sf for stockroom)",
            "Egress capacity sized to occupant load: 0.2 in/occupant for stairs, 0.15 in/occupant for level egress per IBC 1005",
            "Accessible route from accessible entrance to all customer-accessible areas per ADA §206",
            "ADA fixture counts per IBC Table 2902.1 (mercantile uses lower ratios — verify current edition)",
            "Exterior signage permit (often a SEPARATE permit from the building TI — confirm with city sign code)",
            "Fire alarm strobe coverage per NFPA 72 §18.5 (visible from any point, candela-rated to room area)",
            "Sprinkler hydraulic recalc required if shelving / racking exceeds 12 ft (NFPA 13 §3.3.4 storage occupancy)",
            "Storefront glazing: safety glazing per IBC 2406 for any pane within 24 in of door or 60 in of floor",
            "Demising wall fire-rated per IBC 706.3 + STC ≥40 between tenants if not factory-built",
            "Means of egress: ≥2 exits if occupant load >49 OR floor area >3,000 sf in M occupancy per IBC 1006",
            "Public restroom availability per IBC 2902.3 (required for retail >150 occupants OR >300 ft from public restroom)",
            "Energy code lighting + envelope per IECC C405 / C402 if HVAC or exterior wall is touched",
            "Health department clearance if any food/beverage prep beyond pre-packaged sales",
        ],
    },
    "multifamily": {
        "tokens": [
            "apartment building", "apartment complex", "multifamily", "multi-family", "multi family",
            "condominium", "condo building",
            "triplex", "fourplex", "duplex addition", "townhouse",
            "mixed-use", "mixed use", "5-over-1", "five over one", "podium construction",
            "r-2 occupancy", "r2 occupancy",
        ],
        "items": [
            "Fire-rated assembly between dwelling units: 1-hr (Type V) / 2-hr (Type III/IV) per IBC 706; STC ≥50 + IIC ≥50 per IBC 1206",
            "Type IIIA (5-over-1): 1-hr exterior + 1-hr corridors + 2-hr separation between R-2 above and podium below per IBC Table 601",
            "Mixed-occupancy separation per IBC 508 (separated vs non-separated; 2-hr typical between R-2 over M/B podium)",
            "Means of egress: each dwelling unit accesses ≥2 separate exits if travel distance >125 ft (sprinklered) per IBC 1006",
            "Fair Housing Act + ADA Title III accessibility for common areas; ≥1 accessible route to each accessible building entrance",
            "Type B accessible-unit ratio per IBC 1107.6.2 (typically all ground-floor units in non-elevator buildings)",
            "Fire alarm system per NFPA 72: detection in common spaces + sleeping rooms; smoke alarms hardwired/interconnected per IBC 907.2.10",
            "NFPA 13R sprinkler minimum (NFPA 13 if >4 stories or >60 ft) — full coverage including dwelling units, attic spaces per local",
            "Standpipe required for buildings >4 stories or >30 ft above lowest fire-dept access per IBC 905.3",
            "Fire pump if hydraulic calc requires — annual flow + churn test per NFPA 25",
            "Common-area + emergency egress lighting per NFPA 101 §7.9 (90-min battery, monthly + annual tests)",
            "Trash chute fire-rated 1-hr enclosure + Type B sprinkler in chute terminal room per IBC 713.13",
            "Accessible-parking + EV-ready conduit per local code (CA T24 requires 10% EV-ready in new multifamily)",
            "Pre-1980 buildings: asbestos + lead survey before disturbance of common-area finishes / unit interiors",
            "Mail compartment standards: USPS STD-4C for new buildings + 4+ units per 39 CFR 111",
        ],
    },
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

# 2026-04-28: lazy in-process cache of the canonical verified_cities.db row
# set, keyed by ("city_lower", "state_upper") → portal_url / phone / address.
# Used as a wider-coverage fallback (5,260 AHJs) when _CITIES_KB (curated
# JSON, ~263 cities) misses. Loaded once on first lookup.
_VERIFIED_CITIES_ROWS: dict[tuple[str, str], dict] = {}
_VERIFIED_CITIES_LOADED: bool = False

def _load_verified_cities_rows() -> None:
    global _VERIFIED_CITIES_ROWS, _VERIFIED_CITIES_LOADED
    if _VERIFIED_CITIES_LOADED:
        return
    _VERIFIED_CITIES_LOADED = True  # set first so a fail-once doesn't retry every request
    try:
        import sqlite3
        knowledge_db = os.path.join(KNOWLEDGE_DIR, "verified_cities.db")
        data_db = os.path.join(os.path.dirname(__file__), "..", "data", "verified_cities.db")
        db_path = knowledge_db if os.path.exists(knowledge_db) else data_db
        if not os.path.exists(db_path):
            print(f"[verified_cities_kb] DB not found at {knowledge_db} or {data_db}")
            return
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT city, state, portal_url, application_url, building_dept_phone, "
                "building_dept_address, entity_type FROM verified_cities"
            ).fetchall()
            for r in rows:
                key = (str(r["city"] or "").strip().lower(), str(r["state"] or "").strip().upper())
                # Prefer city rows over county rows on collision (cities are
                # the more specific match for an apply_url fallback).
                existing = _VERIFIED_CITIES_ROWS.get(key)
                if existing and existing.get("entity_type") == "city" and r["entity_type"] == "county":
                    continue
                _VERIFIED_CITIES_ROWS[key] = {
                    "city": r["city"],
                    "state": r["state"],
                    "portal_url": r["portal_url"] or "",
                    "application_url": r["application_url"] or "",
                    "phone": r["building_dept_phone"] or "",
                    "address": r["building_dept_address"] or "",
                    "entity_type": r["entity_type"] or "city",
                }
        print(f"[verified_cities_kb] Loaded {len(_VERIFIED_CITIES_ROWS)} rows from {db_path}")
    except Exception as e:
        print(f"[verified_cities_kb] Load failed (non-fatal): {e}")

def _get_verified_city_row(city: str, state: str) -> dict | None:
    """Look up a single AHJ row from verified_cities.db (5,260 rows).

    Returns dict with keys: city, state, portal_url, phone, address,
    entity_type. None if no match. Used as the apply_url / phone / address
    fallback when the curated _CITIES_KB JSON doesn't have the city.
    """
    if not city or not state:
        return None
    _load_verified_cities_rows()
    return _VERIFIED_CITIES_ROWS.get((city.strip().lower(), state.strip().upper()))



# B4 (2026-04-28): rulebook depth is deliberately separate from the existing
# confidence score. Confidence describes output completeness/source quality;
# rulebook_depth tells the user how deep our jurisdiction-specific rulebook is
# for this city + scope. This is the integrity layer for 5,260 verified AHJs:
# only the GTM cities below have stress-tested deep rulebooks today.
DEEP_RULEBOOK_CITIES = frozenset({
    ("phoenix", "AZ"),
    ("las vegas", "NV"),
    ("clark county", "NV"),
    ("seattle", "WA"),
    ("los angeles", "CA"),
    ("dallas", "TX"),
})

TESTED_RULEBOOK_SCOPES = frozenset({
    "restaurant_ti", "office_ti", "retail_ti",
    "detached_adu", "jadu", "hillside_adu", "garage_conversion",
    "kitchen_remodel", "panel_upgrade", "water_heater", "hvac_changeout",
    "reroof", "foundation", "window_replacement", "deck", "patio_cover",
})

COMMON_RULEBOOK_SCOPES = frozenset({
    "restaurant_ti", "office_ti", "retail_ti",
    "detached_adu", "jadu", "hillside_adu", "garage_conversion", "adu",
    "kitchen_remodel", "panel_upgrade", "water_heater", "hvac_changeout",
    "reroof", "window_replacement", "deck", "patio_cover", "simple_trade",
})

RULEBOOK_STRESS_TEST_VERIFIED_AT = "2026-04-28"
RULEBOOK_ENGINE_COMMIT_VERIFIED_AT = "2026-04-29"


def _display_scope_label(scope: str) -> str:
    return (scope or "scope").replace("_", " ")


def classify_rulebook_scope(job_type: str) -> str:
    """Map free-text job_type into B4's rulebook-depth scope enum."""
    job = re.sub(r"\s+", " ", (job_type or "").lower()).strip()
    if not job:
        return "unknown"
    if any(t in job for t in ("tribal", "reservation", "sovereign land", "federal enclave")):
        return "edge_case"
    if any(t in job for t in ("restaurant", "commercial kitchen", "food service", "type i hood", "grease interceptor")):
        return "restaurant_ti"
    if any(t in job for t in ("office ti", "office tenant improvement", "office buildout")):
        return "office_ti"
    if any(t in job for t in ("retail ti", "retail tenant improvement", "retail buildout", "store buildout", "boutique")):
        return "retail_ti"
    if any(t in job for t in ("hillside adu", "hillside accessory dwelling")):
        return "hillside_adu"
    if any(t in job for t in ("jadu", "junior accessory")):
        return "jadu"
    if any(t in job for t in ("garage conversion", "convert garage")):
        return "garage_conversion"
    if any(t in job for t in ("dadu", "detached adu", "detached accessory dwelling")):
        return "detached_adu"
    if "adu" in job or "accessory dwelling" in job:
        return "adu"
    if "kitchen remodel" in job or "kitchen renovation" in job:
        return "kitchen_remodel"
    if any(t in job for t in ("water heater", "tankless")):
        return "water_heater"
    if any(t in job for t in ("hvac", "furnace", "heat pump", "condenser", "air conditioner", "a/c", "ac changeout")):
        return "hvac_changeout"
    if any(t in job for t in ("reroof", "re-roof", "roof replacement", "roofing")):
        return "reroof"
    if "foundation" in job:
        return "foundation"
    if any(t in job for t in ("window replacement", "replace windows", "windows")):
        return "window_replacement"
    if "panel upgrade" in job or "service upgrade" in job:
        return "panel_upgrade"
    if "patio cover" in job or "pergola" in job:
        return "patio_cover"
    if "deck" in job:
        return "deck"
    if any(t in job for t in ("electrical", "plumbing", "mechanical", "trade permit")):
        return "simple_trade"
    return "unknown"


def apply_rulebook_depth(result: dict, job_type: str, city: str, state: str) -> dict:
    """Add B4 rulebook_depth metadata to a permit result without touching confidence."""
    if not isinstance(result, dict):
        return result
    city_name = (city or result.get("location", "").split(",")[0] or "").strip()
    state_code = (state or "").strip().upper()
    scope = classify_rulebook_scope(job_type)
    city_key = (city_name.lower(), state_code)
    ahj = result.get("applying_office") or f"{city_name} building department" or "the AHJ"

    if city_key in DEEP_RULEBOOK_CITIES:
        if scope in TESTED_RULEBOOK_SCOPES:
            depth = "DEEP"
            reason = "GTM-test: Top 5 GTM city + tested scope: depth-graded by 4-city Opus review and 30-scenario stress test on 2026-04-28"
            disclaimer = f"Confidence: HIGH — verified rulebook depth for {city_name} on {_display_scope_label(scope)}"
        else:
            depth = "STATE_DEFAULT" if scope == "edge_case" else "MEDIUM"
            reason = "fallback: GTM city, but scope is outside the 2026-04-28 tested rulebook matrix"
            disclaimer = "Confidence: MEDIUM — verified jurisdiction + state code, scope-specific rulebook is general" if depth == "MEDIUM" else f"Confidence: BASELINE — verified jurisdiction, but scope-specific rulebook is generic; verify with {ahj}"
    else:
        vrow = _get_verified_city_row(city_name, state_code)
        has_apply_url = bool(vrow and (vrow.get("portal_url") or vrow.get("application_url")))
        has_state_amendment = state_code in STATE_AMENDMENT_CITATIONS
        if has_apply_url and has_state_amendment and scope in COMMON_RULEBOOK_SCOPES:
            depth = "MEDIUM"
            reason = f"state-amendment: verified_cities.db has jurisdiction apply URL and {state_code} has A7 state amendments; scope uses general rulebook"
            disclaimer = "Confidence: MEDIUM — verified jurisdiction + state code, scope-specific rulebook is general"
        else:
            depth = "STATE_DEFAULT"
            reason = "fallback: minimal verified jurisdiction data or uncommon/edge-case scope; using generic state/default rulebook"
            disclaimer = f"Confidence: BASELINE — verified jurisdiction, but scope-specific rulebook is generic; verify with {ahj}"

    result["rulebook_depth"] = depth
    result["_rulebook_depth_reason"] = reason
    result["_rulebook_depth_disclaimer"] = disclaimer
    if depth == "DEEP":
        result["_last_verified_at"] = RULEBOOK_STRESS_TEST_VERIFIED_AT if scope in TESTED_RULEBOOK_SCOPES else RULEBOOK_ENGINE_COMMIT_VERIFIED_AT
    elif depth == "MEDIUM":
        result["_last_verified_at"] = RULEBOOK_ENGINE_COMMIT_VERIFIED_AT
    else:
        result.pop("_last_verified_at", None)
    return result

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

    # Mini split — scope-aware permit handling
    if "mini split" in job_lower or "mini-split" in job_lower or "ductless" in job_lower:
        hints.append(
            "IMPORTANT: Treat mini-split scope carefully. If the job only states mini-split equipment installation/changeout "
            "and does NOT explicitly mention panel/service work, gas-line work, or new fixtures, list the Mechanical/HVAC permit only. "
            "Add an Electrical permit only when the scope explicitly includes panel/service upgrade, new circuit, breaker, or disconnect work."
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


def _looks_like_commercial_trade_only_scope(job_type: str) -> bool:
    """Detect commercial-location single-trade work that should not become TI.

    A phrase like "RTU replacement at strip mall" has commercial/retail words,
    but the project is a trade-only swap unless it also says TI/buildout/change
    of use/interior alteration. Keep those out of named TI scopes so the Batch 1
    guardrail does not replace a correct mechanical primary with building/TI.
    """
    job = (job_type or "").lower()
    if not job:
        return False
    ti_markers = (
        "tenant improvement", " t.i.", " ti ", " ti,", " ti.", " ti-", "buildout", "build-out",
        "interior alteration", "interior remodel", "change of use", "change of occupancy",
        "occupancy change", "convert", "converting", "demising", "partition", "partitions",
        "new restaurant", "new clinic", "new office", "new retail",
    )
    if any(t in job for t in ti_markers):
        return False
    commercial_location_markers = (
        "commercial", "strip mall", "mall", "grocery store", "restaurant", "office",
        "professional office", "retail", "store", "tenant space", "suite", "warehouse",
    )
    trade_markers = (
        "rtu", "rooftop unit", "hvac", "condenser", "compressor", "furnace", "mini split",
        "walk-in cooler", "walk in cooler", "walk-in freezer", "walk in freezer",
        "panel", "electrical", "plumbing", "water heater", "hood", "exhaust fan",
    )
    swap_markers = (
        "replace", "replacement", "changeout", "change-out", "swap", "like-for-like",
        "like for like", "repair", "service", "install", "installation", "add", "upgrade",
    )
    return (
        any(t in job for t in commercial_location_markers)
        and any(t in job for t in trade_markers)
        and any(t in job for t in swap_markers)
    )


def detect_primary_scope(job_type: str) -> str:
    """Identify the primary occupancy/project class for a permit query.

    2026-04-28: introduced after Opus 4.7 graded a Phoenix commercial restaurant
    TI at 30% — the engine returned residential-deck checklist items because
    fuzzy-matched residential trades and residential single-trade scopes (HVAC,
    panel_upgrade, etc.) ran alongside the commercial scope. With a primary
    scope detected up front, generate_permit_checklist() suppresses competing
    residential trades + single-trade scopes for commercial queries.

    Returns one of:
      commercial_restaurant | commercial_office_ti | commercial_retail_ti |
      multifamily | commercial (generic) | residential_adu | residential (default)
    """
    job_lc = (job_type or "").lower()
    if _looks_like_commercial_trade_only_scope(job_lc):
        return 'commercial'

    # Commercial restaurant — strongest signal
    if any(t in job_lc for t in (
        'restaurant', 'commercial kitchen', 'food service', ' cafe', 'fast-casual',
        'fast casual', 'tavern', 'brewery', 'type i hood', 'grease interceptor',
        'ansul', 'walk-in cooler', 'walk-in freezer', 'kitchen build-out',
    )):
        return 'commercial_restaurant'
    if any(t in job_lc for t in (
        'medical clinic', 'medical office tenant', 'dental clinic', 'dental office tenant',
        'health clinic', 'clinic tenant improvement', 'clinic ti', 'exam room',
        'exam rooms', 'med gas', 'medical gas', 'nitrous oxide', 'x-ray', 'x ray',
        'radiology', 'sterilization room',
    )):
        return 'commercial_medical_clinic_ti'
    if any(t in job_lc for t in (
        'office tenant improvement', 'office ti', 'office buildout',
        'co-working', 'coworking', 'professional office',
        'law office',
    )):
        return 'commercial_office_ti'
    if any(t in job_lc for t in (
        'retail tenant improvement', 'retail ti', 'retail buildout',
        'showroom', 'boutique', 'mall tenant', 'strip mall', 'store buildout',
        'commercial retail',
    )):
        return 'commercial_retail_ti'
    if any(t in job_lc for t in (
        'multifamily', '5-over-1', '5 over 1', 'podium', '4-over-1',
        'mixed-use', 'mixed use', 'apartment complex', 'apartment building',
    )):
        return 'multifamily'
    # Generic commercial — no specific subtype detected but commercial markers present
    if any(t in job_lc for t in (
        'commercial tenant improvement', 'commercial buildout', 'commercial building',
        'industrial ', 'warehouse', 'change of occupancy', 'a-2 occupancy',
        'b-occupancy', 'mercantile', 'assembly occupancy', 'change of use',
    )):
        return 'commercial'

    # ADU detection — important enough to be its own primary scope
    if any(t in job_lc for t in (
        'adu', 'accessory dwelling', 'granny flat', 'in-law suite',
        'in law suite', 'garage conversion', 'junior accessory', 'jadu',
        'secondary dwelling', 'secondary unit', 'convert into a residence',
    )):
        return 'residential_adu'

    return 'residential'


# Residential single-trade scopes that pollute commercial checklists with
# items like residential gas-water-heater drain pans on a restaurant TI.
# Suppressed when primary scope is commercial.
_RESIDENTIAL_TRADE_SCOPES = frozenset({
    'gas_water_heater', 'tankless_water_heater', 'panel_upgrade', 'ev_charger',
    'ground_mount_solar', 'rapid_shutdown', 'battery_ess', 'pool_spa',
    'adu_specific',
})

_COMMERCIAL_PRIMARY_SCOPES = frozenset({
    'commercial_restaurant', 'commercial_office_ti', 'commercial_retail_ti',
    'commercial_medical_clinic_ti', 'multifamily', 'commercial',
})


# A4 (2026-04-28): downstream cleanup for solar/ESS advisory residue leaking
# into non-solar residential scopes after permit + content assembly. Keep this
# conservative and late in the pipeline: it must not alter trigger detection,
# and it intentionally leaves commercial output alone.
_A4_SOLAR_ESS_SCOPE_RE = re.compile(r"(?<![a-z0-9])(?:solar|pv|photovoltaic|battery|batteries|ess|bess|energy\s+storage|panel_with_storage)(?![a-z0-9])", re.I)
_A4_SOLAR_ESS_TEXT_RE = re.compile(
    r"\b(?:"
    r"ESS|Energy\s+Storage\s+System|energy\s+storage|solar|PV|photovoltaic|"
    r"battery|batteries|Powerwall|LiFePO4|lithium|NFPA\s*855|NEC\s*706|"
    r"IRC\s*324\.10|inverter|microinverter|string\s+inverter|ESS\s+NEC|"
    r"ESS\s+NFPA|BESS"
    r")\b",
    re.I,
)


def _a4_solar_ess_in_scope(result: dict, job_type: str) -> bool:
    """Return True only when solar/PV/battery/ESS is actually in scope."""
    parts = [job_type or "", str((result or {}).get("_primary_scope") or "")]
    if any(_A4_SOLAR_ESS_SCOPE_RE.search(p) for p in parts):
        return True
    for trig in (result or {}).get("hidden_triggers") or []:
        trig_id = ""
        if isinstance(trig, dict):
            trig_id = str(trig.get("id") or trig.get("trigger_id") or "")
        else:
            trig_id = str(trig or "")
        if _A4_SOLAR_ESS_SCOPE_RE.search(trig_id):
            return True
    return False


def _a4_strip_solar_ess_sentences(text: str) -> str:
    """Remove only sentences/clauses containing solar/ESS keywords."""
    if not isinstance(text, str) or not _A4_SOLAR_ESS_TEXT_RE.search(text):
        return text
    pieces = re.split(r"(?<=[.!?])\s+|\n+", text)
    kept = [p.strip() for p in pieces if p.strip() and not _A4_SOLAR_ESS_TEXT_RE.search(p)]
    if kept:
        return " ".join(kept)
    clauses = re.split(r"\s*(?:;|\s+—\s+|\s+-\s+)\s*", text)
    kept = [c.strip() for c in clauses if c.strip() and not _A4_SOLAR_ESS_TEXT_RE.search(c)]
    return "; ".join(kept).strip()


def purge_solar_ess_residue(result: dict, job_type: str) -> dict:
    """Suppress ESS/solar/battery advisory residue for non-solar residential scopes."""
    if not isinstance(result, dict):
        return result
    primary_scope = result.get("_primary_scope") or detect_primary_scope(job_type or "")
    result.setdefault("_primary_scope", primary_scope)
    if primary_scope in _COMMERCIAL_PRIMARY_SCOPES:
        return result
    if _a4_solar_ess_in_scope(result, job_type):
        return result

    removed = 0

    def clean_string(value: str) -> str:
        nonlocal removed
        cleaned = _a4_strip_solar_ess_sentences(value)
        if cleaned != value:
            removed += 1
        return cleaned

    def clean_list(values, drop_keyword_dicts: bool = False):
        nonlocal removed
        if not isinstance(values, list):
            return values
        out = []
        for item in values:
            if isinstance(item, str):
                if _A4_SOLAR_ESS_TEXT_RE.search(item):
                    removed += 1
                    continue
                out.append(item)
            elif isinstance(item, dict):
                if drop_keyword_dicts and _A4_SOLAR_ESS_TEXT_RE.search(str(item)):
                    removed += 1
                    continue
                out.append(clean_dict(item))
            else:
                out.append(item)
        return out

    def clean_dict(obj: dict) -> dict:
        cleaned = dict(obj)
        for key, value in list(cleaned.items()):
            if isinstance(value, str) and key in {"notes", "note", "description", "applies_to", "title"}:
                cleaned[key] = clean_string(value)
            elif isinstance(value, list) and key in {"notes", "fail_points", "common_mistakes", "pro_tips", "watch_out"}:
                cleaned[key] = clean_list(value)
        return cleaned

    for key in ("job_summary", "zoning_hoa_flag", "confidence_reason", "disclaimer"):
        if isinstance(result.get(key), str):
            result[key] = clean_string(result[key])
    for key in ("pro_tips", "common_mistakes", "watch_out", "what_to_bring", "requirements", "checklist"):
        if key in result:
            result[key] = clean_list(result.get(key))
    if "expert_notes" in result:
        result["expert_notes"] = clean_list(result.get("expert_notes"), drop_keyword_dicts=True)
    for key in ("inspections", "permits_required"):
        if isinstance(result.get(key), list):
            result[key] = [clean_dict(item) if isinstance(item, dict) else item for item in result[key]]
    for key in ("sources", "state_expert_notes"):
        if isinstance(result.get(key), list):
            result[key] = clean_list(result[key], drop_keyword_dicts=True)
    for key in ("fee_source", "ahj_contact_source", "code_section_source", "required_documents_source", "inspection_process_source"):
        if isinstance(result.get(key), dict) and _A4_SOLAR_ESS_TEXT_RE.search(str(result[key])):
            result[key] = {}
            removed += 1

    if removed:
        result["_a4_residue_removed"] = removed
    return result


# A7 (2026-04-28): model-code citations are useful, but contractors in the
# launch cities also expect the controlling state/local amendment regime to sit
# next to the IBC/IMC/IPC/NEC citation. This layer only augments code_citation;
# it intentionally does not touch tier_b/apply_state_expert_pack.
STATE_AMENDMENT_CITATIONS = {
    "CA": [
        {"code": "California Building Code (CBC)", "section_pattern": "CBC Chapter <X>", "applies_to": ["all"], "version": "2022 with 2025 supplement"},
        {"code": "California Mechanical Code (CMC)", "applies_to": ["mechanical"], "version": "2022"},
        {"code": "California Plumbing Code (CPC)", "applies_to": ["plumbing"], "version": "2022"},
        {"code": "California Electrical Code (CEC)", "applies_to": ["electrical"], "version": "2022"},
        {"code": "California Energy Code Title 24 Part 6", "applies_to": ["energy", "all"], "version": "2022"},
        {"code": "California Fire Code (CFC)", "applies_to": ["fire", "all_commercial"], "version": "2022"},
        {"code": "CALGreen Title 24 Part 11", "applies_to": ["all"], "version": "2022"},
    ],
    "WA": [
        {"code": "Washington State Energy Code Commercial (WSEC-C)", "applies_to": ["energy", "all_commercial"], "version": "2021"},
        {"code": "Washington State Energy Code Residential (WSEC-R)", "applies_to": ["energy", "all_residential"], "version": "2021"},
        {"code": "Seattle Building Code (SBC) Chapter <X> amendments", "applies_to": ["all"], "scope": "Seattle only"},
    ],
    "AZ": [
        {"code": "Arizona Roofing Standards (ARS)", "applies_to": ["roofing"]},
        {"code": "Phoenix Building Construction Code (PBCC) — IBC 2018 + Phoenix amendments", "applies_to": ["all"], "scope": "Phoenix only"},
    ],
    "NV": [
        {"code": "Clark County Building Code — IBC 2018 + Clark amendments", "applies_to": ["all"], "scope": "Clark County / unincorporated Las Vegas"},
        {"code": "City of Las Vegas Municipal Code Title 15", "applies_to": ["all"], "scope": "incorporated Las Vegas only"},
    ],
    "TX": [
        {"code": "Texas Accessibility Standards (TAS)", "applies_to": ["accessibility", "all_commercial"], "version": "2012 with 2025 erratta"},
        {"code": "Texas Plumbing License Law (TSBPE)", "applies_to": ["plumbing"]},
        {"code": "Texas State Board of Plumbing Examiners", "applies_to": ["plumbing"]},
        {"code": "TDLR — Texas Department of Licensing and Regulation (Air Conditioning and Refrigeration)", "applies_to": ["mechanical"]},
        {"code": "Dallas Building Code Chapter — local amendments", "applies_to": ["all"], "scope": "Dallas only"},
    ],
}


def _code_citation_items(code_citation):
    if isinstance(code_citation, list):
        return [c for c in code_citation if c]
    if isinstance(code_citation, dict):
        return [code_citation]
    if isinstance(code_citation, str) and len(code_citation) > 3:
        return [{"section": code_citation, "text": ""}]
    return []


def _state_amendment_trade_tags(result: dict, job_type: str) -> set[str]:
    haystack_parts = [job_type or "", result.get("_primary_scope") or ""]
    for key in ("code_citation", "permits_required", "companion_permits", "inspections", "what_to_bring", "common_mistakes", "pro_tips"):
        haystack_parts.append(str(result.get(key, "")))
    text = " ".join(haystack_parts).lower()
    tags = set()
    tag_terms = {
        "mechanical": ("mechanical", "hvac", "rtu", "furnace", "heat pump", "imc", "air conditioning", "tdlr"),
        "plumbing": ("plumbing", "fixture", "grease interceptor", "ipc", "water heater", "dwv", "tsbpe"),
        "electrical": ("electrical", "panel", "wiring", "circuit", "nec", "service upgrade", "lighting"),
        "fire": ("fire", "sprinkler", "alarm", "ansul", "hood suppression", "ifc", "cfc"),
        "accessibility": ("accessib", " ada", "tas", "disabled", "barrier"),
        "energy": ("energy", "title 24", "wsec", "calgreen", "envelope", "insulation"),
        "roofing": ("roof", "reroof", "re-roof", "roofing"),
    }
    for tag, terms in tag_terms.items():
        if any(term in text for term in terms):
            tags.add(tag)
    primary_scope = result.get("_primary_scope") or detect_primary_scope(job_type or "")
    tags.add("all_commercial" if primary_scope in _COMMERCIAL_PRIMARY_SCOPES else "all_residential")
    tags.add("all")
    return tags


def _format_amendment_code(amendment: dict, existing_items: list[dict]) -> str:
    code = amendment.get("code", "")
    pattern = amendment.get("section_pattern")
    if pattern and "<X>" in pattern:
        for item in existing_items:
            section = str(item.get("section") or item.get("code") or "")
            m = re.search(r"\b(?:IBC|IRC|IEBC)\s*(?:§+\s*)?(\d+)", section, re.I)
            if m:
                chapter = m.group(1)[:2] if len(m.group(1)) >= 4 else m.group(1)[:1]
                return f"{code} {pattern.replace('<X>', chapter).replace('CBC ', '')}"
    return code.replace("Chapter <X>", "Chapter INCOMPLETE — verify AHJ-specific chapter")


def apply_state_amendment_citations(result: dict, job_type: str, city: str, state: str) -> dict:
    """Append A7 state/local amendment citations to result['code_citation']."""
    state_code = (state or "").strip().upper()
    amendments = STATE_AMENDMENT_CITATIONS.get(state_code)
    if not amendments:
        return result

    city_lc = (city or "").lower()
    tags = _state_amendment_trade_tags(result, job_type)
    existing_items = _code_citation_items(result.get("code_citation"))
    out = list(existing_items)
    seen = {str(i.get("code") or i.get("section") or "").lower() for i in out if isinstance(i, dict)}

    for amendment in amendments:
        scope = amendment.get("scope", "")
        if "Seattle only" in scope and "seattle" not in city_lc:
            continue
        if "Phoenix only" in scope and "phoenix" not in city_lc:
            continue
        if "Dallas only" in scope and "dallas" not in city_lc:
            continue
        if "Clark County" in scope and not any(t in city_lc for t in ("las vegas", "clark")):
            continue
        if "incorporated Las Vegas" in scope and "las vegas" not in city_lc:
            continue

        applies_to = amendment.get("applies_to", [])
        matched_scope = next((a for a in applies_to if a in tags), None)
        if not matched_scope:
            continue

        code = _format_amendment_code(amendment, existing_items)
        if code.lower() in seen:
            continue
        entry = {
            "code": code,
            "type": "state_amendment",
            "state": state_code,
            "applies_to_scope": matched_scope,
        }
        if amendment.get("version"):
            entry["version"] = amendment["version"]
        if amendment.get("scope"):
            entry["scope"] = amendment["scope"]
        out.append(entry)
        seen.add(code.lower())

    if out:
        result["code_citation"] = out
    return result


def generate_permit_checklist(job_type: str, city: str, state: str, result: dict) -> list[str]:
    try:
        primary_scope = detect_primary_scope(job_type)
        is_commercial = primary_scope in _COMMERCIAL_PRIMARY_SCOPES
        # Surface the routing decision on the result so the UI / validator can
        # see why scope items were included or suppressed.
        if isinstance(result, dict):
            result.setdefault('_primary_scope', primary_scope)

        items = list(CHECKLIST_BASE.get('always', []))

        # Fuzzy-matched residential trade items pollute commercial output
        # (e.g. "tenant improvement" fuzzy-matched to a residential trade
        # supplied residential deck items on a Phoenix restaurant TI).
        # Skip the trade-fuzzy step for commercial queries — commercial
        # routes exclusively through the matching commercial scope below.
        if not is_commercial:
            matched = _fuzzy_match_key(job_type, list(CHECKLIST_TRADE.keys()))
            if matched:
                items.extend(CHECKLIST_TRADE.get(matched, []))

        # Layer scope-specific inspection items on top with primary-scope-aware
        # suppression.
        # 1) Residential single-trade scopes (gas_water_heater, panel_upgrade,
        #    etc.) are suppressed for commercial queries.
        # 2) Commercial primary scopes COMPETE with each other — when primary
        #    is commercial_office_ti / commercial_retail_ti / multifamily, we
        #    SUPPRESS the commercial_restaurant scope group even if its tokens
        #    match (e.g. Chicago office TI mentioning "tenant improvement" or
        #    " ti " was loading the full hood/grease/Type-I-hood pack —
        #    Opus 4.7 graded Chicago office TI 64 and Houston retail 58
        #    specifically because of this leak). Cross-cutting scopes
        #    (change_of_occupancy, ada_path_of_travel) are NOT competing — they
        #    fire alongside any primary because they're orthogonal reviews.
        # 3) Multiple non-competing scopes can still fire on one job (e.g.
        #    solar + battery on a residential PV install pulls rapid_shutdown
        #    + battery_ess together).
        _COMMERCIAL_COMPETING_SCOPES = frozenset({
            'commercial_restaurant', 'commercial_office_ti',
            'commercial_retail_ti', 'multifamily',
        })
        job_lc = (job_type or "").lower()
        for scope_key, scope in CHECKLIST_SCOPE.items():
            tokens = scope.get("tokens") or []
            if not any(t in job_lc for t in tokens):
                continue
            if is_commercial and scope_key in _RESIDENTIAL_TRADE_SCOPES:
                continue
            if (
                is_commercial
                and scope_key in _COMMERCIAL_COMPETING_SCOPES
                and scope_key != primary_scope
            ):
                continue
            items.extend(scope.get("items", []))
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
    # 2026-04-26: ETag/Last-Modified columns for change-detection-driven
    # cache invalidation. When set, get_cached() can revalidate against
    # the source URL via HEAD If-None-Match before serving stale data.
    if "source_url" not in cols:
        conn.execute("ALTER TABLE permit_cache ADD COLUMN source_url TEXT")
    if "etag" not in cols:
        conn.execute("ALTER TABLE permit_cache ADD COLUMN etag TEXT")
    if "last_modified" not in cols:
        conn.execute("ALTER TABLE permit_cache ADD COLUMN last_modified TEXT")
    if "last_checked_at" not in cols:
        conn.execute("ALTER TABLE permit_cache ADD COLUMN last_checked_at TEXT")
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
    # v2 (2026-04-27): bumped after the Pasadena CA → Harris County TX cross-state
    # leak. Old v1 entries cached buggy county_fallback data; bumping the prefix
    # invalidates every pre-fix row at once so users never see stale results.
    raw = f"v2|{job_type.lower().strip()}|{city.lower().strip()}|{state.upper().strip()}|{(job_category or 'residential').lower().strip()}"
    return hashlib.md5(raw.encode()).hexdigest()

def _smart_ttl(hits: int, confidence: str, fee_unverified: bool) -> int:
    """Tiered TTL based on popularity and data quality.
    Reduced 2026-04-26: max 30d (was 60), default 14d (was 30). Permit rules
    and fee schedules update too fast for a 60-day blanket cache. Coupled with
    ETag polling that triggers earlier refresh for entries with a known
    source_url.
    """
    if fee_unverified or confidence == "low":
        return _PERMIT_CACHE_TTL_FRESH_REVERIFY_DAYS  # 7
    if hits >= 10 and confidence == "high":
        return _PERMIT_CACHE_TTL_HIGH_HITS_DAYS  # 30
    if hits >= 3 and confidence in ("high", "medium"):
        return 21
    return _PERMIT_CACHE_TTL_DEFAULT_DAYS  # 14

def _etag_changed(source_url: str, etag: str = "", last_modified: str = "", *, timeout: float = 4.0) -> str:
    """Check whether source_url has changed since the last cached version.

    Returns one of: "changed" | "same" | "unknown".

    Uses If-None-Match (ETag) and If-Modified-Since (Last-Modified) per RFC 7232.
    A 304 = "same", a 200 with different ETag/Last-Modified = "changed".
    Network/HTTP errors → "unknown" (defer to TTL).

    Never raises. Designed to add at most ~200ms to a cache hit.
    """
    if not source_url:
        return "unknown"
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        # HEAD first; some servers return 405 → fall back to GET
        resp = requests.head(source_url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code == 405:
            resp = requests.get(source_url, headers=headers, timeout=timeout, allow_redirects=True, stream=True)
            try:
                resp.close()
            except Exception:
                pass
        if resp.status_code == 304:
            return "same"
        if resp.status_code >= 400:
            return "unknown"
        new_etag = (resp.headers.get("ETag") or "").strip()
        new_last_mod = (resp.headers.get("Last-Modified") or "").strip()
        if etag and new_etag and new_etag != etag:
            return "changed"
        if last_modified and new_last_mod and new_last_mod != last_modified:
            return "changed"
        if not etag and not last_modified:
            # We don't have validators saved yet — can't compare.
            return "unknown"
        return "same"
    except requests.RequestException:
        return "unknown"


def _capture_validators(source_url: str, *, timeout: float = 4.0) -> tuple[str, str]:
    """Fetch ETag + Last-Modified for a URL we're about to cache. Returns ("","") on failure."""
    if not source_url:
        return ("", "")
    try:
        resp = requests.head(source_url, timeout=timeout, allow_redirects=True)
        if resp.status_code == 405:
            resp = requests.get(source_url, timeout=timeout, allow_redirects=True, stream=True)
            try:
                resp.close()
            except Exception:
                pass
        return (
            (resp.headers.get("ETag") or "").strip(),
            (resp.headers.get("Last-Modified") or "").strip(),
        )
    except requests.RequestException:
        return ("", "")


def _pick_primary_source_url(result: dict) -> str:
    """Pick the most-authoritative URL associated with a cached answer.

    Order: apply_url (from KB/Accela) > first .gov source > first source.
    Used as the ETag-check target.
    """
    apply_url = (result.get("apply_url") or "").strip()
    if apply_url and apply_url.startswith("http"):
        return apply_url
    sources = result.get("sources") or []
    for s in sources:
        url = (s.get("url") if isinstance(s, dict) else str(s)).strip()
        if url and (".gov" in url.lower() or ".us" in url.lower()):
            return url
    for s in sources:
        url = (s.get("url") if isinstance(s, dict) else str(s)).strip()
        if url and url.startswith("http"):
            return url
    return ""


def get_cached(key: str, max_age_days: int = None, _refresh_callback=None):
    """Smart cache read with tiered TTL, stale-while-revalidate, and
    optional ETag-based invalidation (added 2026-04-26).

    - max_age_days: override TTL (used internally; pass None for smart TTL)
    - _refresh_callback: callable(key) to trigger background refresh when stale

    ETag flow: at >= _PERMIT_CACHE_ETAG_CHECK_FRACTION of TTL, if we have a
    source_url + (etag OR last_modified), do a HEAD revalidation. If the
    source has changed → treat as cache miss + clear the row. Else update
    last_checked_at. Network/HTTP errors fall back to TTL (status quo).
    """
    try:
        conn = sqlite3.connect(CACHE_DB)
        # Read all the columns we care about
        cols_avail = {row[1] for row in conn.execute("PRAGMA table_info(permit_cache)").fetchall()}
        select_cols = ["result_json", "created_at", "hits"]
        if "source_url" in cols_avail: select_cols.append("source_url")
        if "etag" in cols_avail: select_cols.append("etag")
        if "last_modified" in cols_avail: select_cols.append("last_modified")
        if "last_checked_at" in cols_avail: select_cols.append("last_checked_at")
        sel_sql = f"SELECT {', '.join(select_cols)} FROM permit_cache WHERE cache_key = ?"
        row = conn.execute(sel_sql, [key]).fetchone()
        if row:
            result = json.loads(row[0])
            created = datetime.fromisoformat(row[1])
            hits = row[2] or 0
            row_dict = dict(zip(select_cols, row))
            source_url = row_dict.get("source_url") or ""
            etag = row_dict.get("etag") or ""
            last_modified = row_dict.get("last_modified") or ""
            confidence = result.get("confidence", "medium")
            fee_unverified = bool(result.get("_fee_unverified"))
            ttl = max_age_days if max_age_days is not None else _smart_ttl(hits, confidence, fee_unverified)
            age = datetime.now() - created
            if age < timedelta(days=ttl):
                # ETag check: at >= 50% of TTL, revalidate against source if we have validators
                etag_threshold = timedelta(days=ttl * _PERMIT_CACHE_ETAG_CHECK_FRACTION)
                if source_url and (etag or last_modified) and age >= etag_threshold:
                    status = _etag_changed(source_url, etag=etag, last_modified=last_modified)
                    if status == "changed":
                        # Source has updated since we cached — invalidate
                        print(f"[cache] ETag CHANGED for {source_url[:80]} → invalidating cache key {key[:8]}…")
                        conn.execute("DELETE FROM permit_cache WHERE cache_key = ?", [key])
                        conn.commit()
                        conn.close()
                        _cache_stats["misses"] += 1
                        _cache_stats["etag_invalidations"] = _cache_stats.get("etag_invalidations", 0) + 1
                        return None
                    elif status == "same":
                        # Confirmed unchanged — bump last_checked_at so we don't re-check next read
                        conn.execute(
                            "UPDATE permit_cache SET last_checked_at = ? WHERE cache_key = ?",
                            [datetime.now().isoformat(), key],
                        )
                        _cache_stats["etag_revalidations_same"] = _cache_stats.get("etag_revalidations_same", 0) + 1
                conn.execute("UPDATE permit_cache SET hits = hits + 1 WHERE cache_key = ?", [key])
                conn.commit()
                conn.close()
                _cache_stats["hits"] += 1
                # Stale-while-revalidate: if past 75% of TTL, trigger background refresh
                if _refresh_callback and age > timedelta(days=ttl * 0.75):
                    print(f"[cache] Stale-while-revalidate triggered for key {key[:8]}… (age={age.days}d, ttl={ttl}d)")
                    import threading
                    threading.Thread(target=_refresh_callback, args=(key,), daemon=True).start()
                return result
        conn.close()
    except Exception as e:
        print(f"[cache] Read error (non-fatal): {e}")
    _cache_stats["misses"] += 1
    return None

def save_cache(key: str, job_type: str, job_category: str, city: str, state: str, zip_code: str, result: dict):
    try:
        # 2026-04-26: capture source URL + ETag/Last-Modified for change detection.
        source_url = _pick_primary_source_url(result)
        etag, last_modified = _capture_validators(source_url) if source_url else ("", "")
        conn = sqlite3.connect(CACHE_DB)
        cols_avail = {row[1] for row in conn.execute("PRAGMA table_info(permit_cache)").fetchall()}
        if {"source_url", "etag", "last_modified", "last_checked_at"}.issubset(cols_avail):
            conn.execute("""
                INSERT OR REPLACE INTO permit_cache
                (cache_key, job_type, job_category, city, state, zip_code,
                 result_json, created_at, hits,
                 source_url, etag, last_modified, last_checked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
            """, [key, job_type, job_category, city, state, zip_code,
                  json.dumps(result), datetime.now().isoformat(),
                  source_url, etag, last_modified, datetime.now().isoformat()])
        else:
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



def _http_post_with_backoff(url: str, *, json: dict | None = None, headers: dict | None = None, timeout: int = 10, max_retries: int = 3) -> "requests.Response | None":
    """POST with exponential backoff on 429/503. Returns Response or None on terminal failure."""
    delay = 1.0
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, json=json, headers=headers or {}, timeout=timeout)
            if resp.status_code in (429, 503) and attempt < max_retries - 1:
                # Respect Retry-After if provided; else exponential
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if (retry_after and retry_after.isdigit()) else delay
                print(f"[search] {urlparse(url).netloc} {resp.status_code} — retrying in {wait:.1f}s (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
                delay *= 2
                continue
            return resp
        except requests.RequestException as e:
            if attempt < max_retries - 1:
                print(f"[search] {urlparse(url).netloc} transient error: {e} — retrying in {delay:.1f}s")
                time.sleep(delay)
                delay *= 2
                continue
            print(f"[search] {urlparse(url).netloc} failed after {max_retries} attempts: {e}")
            return None
    return None


def _http_get_with_backoff(url: str, *, params: dict | None = None, headers: dict | None = None, timeout: int = 10, max_retries: int = 3) -> "requests.Response | None":
    """GET with exponential backoff on 429/503. Returns Response or None on terminal failure."""
    delay = 1.0
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, headers=headers or {}, timeout=timeout)
            if resp.status_code in (429, 503) and attempt < max_retries - 1:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if (retry_after and retry_after.isdigit()) else delay
                print(f"[search] {urlparse(url).netloc} {resp.status_code} — retrying in {wait:.1f}s (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
                delay *= 2
                continue
            return resp
        except requests.RequestException as e:
            if attempt < max_retries - 1:
                print(f"[search] {urlparse(url).netloc} transient error: {e} — retrying in {delay:.1f}s")
                time.sleep(delay)
                delay *= 2
                continue
            print(f"[search] {urlparse(url).netloc} failed after {max_retries} attempts: {e}")
            return None
    return None


def serper_search(query: str, num: int = 5, city: str = "", state: str = "") -> list[dict]:
    if not SERPER_API_KEY:
        return []
    try:
        resp = _http_post_with_backoff(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": num, "gl": "us"},
            timeout=15,
        )
        if not resp or resp.status_code >= 400:
            return []
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
        allowed_results = _filter_allowed_source_results(results)
        excluded = len(results) - len(allowed_results)
        trimmed = _rank_search_results(allowed_results, limit=min(num, 4), city=city, state=state)
        print(f"[search] Layer 1: serper found {len(trimmed)} urls (excluded: {excluded})")
        return trimmed
    except Exception as e:
        print(f"[search] Serper search failed (non-fatal): {e}")
        return []


# ─── Tier A Trust Layer: Serper claim grounding + conditional companion permits ──

def _today_iso_date() -> str:
    return datetime.now().date().isoformat()


def _serper_cache_conn():
    os.makedirs(os.path.dirname(SERPER_CACHE_DB), exist_ok=True)
    conn = sqlite3.connect(SERPER_CACHE_DB)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS serper_claim_cache (
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            claim_type TEXT NOT NULL,
            query TEXT NOT NULL,
            title TEXT,
            url TEXT,
            snippet TEXT,
            created_at REAL NOT NULL,
            PRIMARY KEY (city, state, claim_type)
        )
        """
    )
    conn.commit()
    return conn


def _cache_norm(value: str) -> str:
    return (value or "").strip().lower()


def _get_cached_serper_source(city: str, state: str, claim_type: str) -> dict | None:
    try:
        conn = _serper_cache_conn()
        try:
            row = conn.execute(
                "SELECT title, url, snippet, created_at FROM serper_claim_cache WHERE city=? AND state=? AND claim_type=?",
                (_cache_norm(city), _cache_norm(state), _cache_norm(claim_type)),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        title, url, snippet, created_at = row
        if not url or (time.time() - float(created_at or 0)) > SERPER_TRUST_TTL_SECONDS:
            return None
        source_class = classify_source_url(url)
        if source_class == SOURCE_CLASS_EXCLUDED:
            print(f"[trust] Serper cache ignored excluded source: {url}")
            return None
        cached = {
            "url": url,
            "title": title or url,
            "verified_at": datetime.fromtimestamp(float(created_at)).date().isoformat(),
            "snippet": snippet or "",
            "cache": "hit",
            "source_class": source_class,
            "source_type": source_class.lower(),
        }
        if source_class == SOURCE_CLASS_SUPPLEMENTARY:
            cached["supplementary"] = True
            cached["source_label"] = "supplementary reference"
        return cached
    except Exception as e:
        print(f"[trust] Serper cache read failed (non-fatal): {e}")
        return None


def _set_cached_serper_source(city: str, state: str, claim_type: str, query: str, source: dict) -> None:
    if not source or not source.get("url"):
        return
    if classify_source_url(source.get("url") or "") == SOURCE_CLASS_EXCLUDED:
        return
    try:
        conn = _serper_cache_conn()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO serper_claim_cache
                (city, state, claim_type, query, title, url, snippet, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _cache_norm(city),
                    _cache_norm(state),
                    _cache_norm(claim_type),
                    query,
                    source.get("title") or source.get("url"),
                    source.get("url"),
                    source.get("snippet") or source.get("content") or "",
                    time.time(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"[trust] Serper cache write failed (non-fatal): {e}")


def _normalized_source_domain(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    domain = (parsed.hostname or parsed.netloc or "").lower().strip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _domain_matches(domain: str, candidate: str) -> bool:
    return domain == candidate or domain.endswith(f".{candidate}")


def classify_source_url(url: str) -> str:
    """Classify a source URL before it is allowed into public citations."""
    domain = _normalized_source_domain(url)
    if not domain:
        return SOURCE_CLASS_EXCLUDED

    # Blocklist wins over every allow rule, including .us and iccsafe.org.
    if any(_domain_matches(domain, blocked) for blocked in EXCLUDED_SOURCE_DOMAINS):
        return SOURCE_CLASS_EXCLUDED

    if domain.endswith((".gov", ".us", ".mil")):
        return SOURCE_CLASS_OFFICIAL

    if any(_domain_matches(domain, allowed) for allowed in OFFICIAL_SOURCE_DOMAINS):
        return SOURCE_CLASS_OFFICIAL

    labels = domain.split(".")
    first_label = labels[0] if labels else ""
    if first_label.startswith(("cityof", "townof", "villageof", "countyof")):
        return SOURCE_CLASS_OFFICIAL
    if any(token in domain for token in ("county", "borough", "parish")) and any(token in domain for token in ("permit", "building", "planning", "development")):
        return SOURCE_CLASS_OFFICIAL

    if any(_domain_matches(domain, supplemental) for supplemental in SUPPLEMENTARY_SOURCE_DOMAINS):
        return SOURCE_CLASS_SUPPLEMENTARY

    if domain.endswith(".org"):
        return SOURCE_CLASS_SUPPLEMENTARY

    # Unknown .com/.net/etc. sources are not authoritative enough for PermitIQ
    # citations. Excluding by default prevents accidental competitor referrals.
    return SOURCE_CLASS_EXCLUDED


def _is_official_permit_source_url(url: str) -> bool:
    return classify_source_url(url) == SOURCE_CLASS_OFFICIAL


def _source_trust_rank(source_class: str) -> int:
    if source_class == SOURCE_CLASS_OFFICIAL:
        return 0
    if source_class == SOURCE_CLASS_SUPPLEMENTARY:
        return 1
    return 2


def _with_source_class(item: dict, source_class: str) -> dict:
    annotated = {**item, "source_class": source_class, "source_type": source_class.lower()}
    if source_class == SOURCE_CLASS_SUPPLEMENTARY:
        annotated["supplementary"] = True
        annotated["source_label"] = "supplementary reference"
    return annotated


def _filter_allowed_source_results(results: list[dict]) -> list[dict]:
    allowed = []
    for item in results or []:
        url = item.get("url") or item.get("link") or ""
        source_class = classify_source_url(url)
        if source_class == SOURCE_CLASS_EXCLUDED:
            continue
        allowed.append(_with_source_class(item, source_class))
    return allowed


def _best_serper_source_from_results(results: list[dict], city: str, state: str) -> dict | None:
    if not results:
        return None
    allowed = _filter_allowed_source_results(results)
    if not allowed:
        return None
    ranked = _rank_search_results(allowed, limit=min(len(allowed), 5), city=city, state=state)
    ranked = ranked or allowed
    ranked.sort(key=lambda item: (_source_trust_rank(item.get("source_class", SOURCE_CLASS_EXCLUDED)),))
    best = ranked[0]
    url = best.get("url") or best.get("link") or ""
    if not url:
        return None
    source_class = best.get("source_class") or classify_source_url(url)
    source = {
        "url": url,
        "title": best.get("title") or url,
        "verified_at": _today_iso_date(),
        "snippet": clean_summary_text(best.get("content") or best.get("snippet") or "", max_len=280),
        "source_class": source_class,
        "source_type": source_class.lower(),
    }
    if source_class == SOURCE_CLASS_SUPPLEMENTARY:
        source["supplementary"] = True
        source["source_label"] = "supplementary reference"
    return source


def _serper_claim_source(query: str, claim_type: str, city: str, state: str, stats: dict, request_timeout: float = 15) -> dict | None:
    cached = _get_cached_serper_source(city, state, claim_type)
    if cached:
        stats["cache_hits"] = stats.get("cache_hits", 0) + 1
        print(f"[trust] Serper {claim_type}: cache hit {cached.get('url')}")
        return cached

    if stats.get("queries", 0) >= SERPER_TRUST_MAX_QUERIES:
        stats["capped"] = True
        return None
    if not SERPER_API_KEY:
        stats["provider_failed"] = True
        stats["failure_reason"] = "missing_api_key"
        return None

    stats["queries"] = stats.get("queries", 0) + 1
    try:
        resp = _http_post_with_backoff(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": 5, "gl": "us"},
            timeout=request_timeout,
            max_retries=2,
        )
        if not resp or resp.status_code >= 400:
            stats["provider_failed"] = True
            stats["failure_reason"] = f"http_{getattr(resp, 'status_code', 'none')}"
            return None
        data = resp.json()
        results = []
        for r in data.get("organic", []) or []:
            url = r.get("link", "")
            if not url:
                continue
            results.append({
                "title": r.get("title", ""),
                "url": url,
                "content": clean_summary_text(r.get("snippet", ""), max_len=500),
            })
        source = _best_serper_source_from_results(results, city, state)
        if source:
            _set_cached_serper_source(city, state, claim_type, query, source)
            label = source.get("source_label") or source.get("source_type") or "source"
            print(f"[trust] Serper {claim_type}: {source.get('url')} ({label})")
            return source
        if results and all(classify_source_url(r.get("url") or r.get("link") or "") == SOURCE_CLASS_EXCLUDED for r in results):
            stats["excluded_results"] = stats.get("excluded_results", 0) + len(results)
            stats["failure_reason"] = stats.get("failure_reason") or "excluded_sources_only"
            print(f"[trust] Serper {claim_type}: synthesized from {len(results)} excluded sources, no official URL available")
        else:
            stats["empty_results"] = stats.get("empty_results", 0) + 1
        return None
    except Exception as e:
        stats["provider_failed"] = True
        stats["failure_reason"] = type(e).__name__
        print(f"[trust] Serper {claim_type} failed (non-fatal): {e}")
        return None


def _primary_permit_label(result: dict, job_type: str) -> str:
    for p in result.get("permits_required") or []:
        if isinstance(p, dict) and p.get("permit_type"):
            return str(p.get("permit_type"))
    return job_type or "building permit"


def _serper_claim_queries(job_type: str, city: str, state: str, result: dict) -> list[tuple[str, str, str]]:
    # Use the contractor's plain job description rather than the model's often
    # long portal label. Exact quoted permit labels over-constrain Google and
    # caused empty Serper results for real cases like ADUs and solar+battery.
    permit_type = (job_type or _primary_permit_label(result, job_type) or "building permit").strip()
    return [
        ("fee", "fee_source", f'{city} {state} {permit_type} permit fee schedule 2026'),
        ("ahj_contact", "ahj_contact_source", f'{city} {state} Building Department phone address'),
        ("code_section", "code_section_source", f'{city} {state} {permit_type} code section permit'),
        ("required_documents", "required_documents_source", f'{city} {state} {permit_type} permit application requirements'),
        ("inspection_process", "inspection_process_source", f'{city} {state} {permit_type} inspection process'),
    ]


def _append_source_url(result: dict, source: dict) -> None:
    url = (source or {}).get("url")
    if not url:
        return
    existing = result.get("sources") or []
    if not isinstance(existing, list):
        existing = []
    if url not in [s for s in existing if isinstance(s, str)]:
        existing.append(url)
    result["sources"] = normalize_sources(existing)




def _merge_serper_stats(total: dict, partial: dict | None) -> None:
    if not partial:
        return
    total["queries"] = total.get("queries", 0) + int(partial.get("queries", 0) or 0)
    total["cache_hits"] = total.get("cache_hits", 0) + int(partial.get("cache_hits", 0) or 0)
    for key in ("capped", "provider_failed", "empty_results", "timed_out"):
        if partial.get(key):
            total[key] = partial.get(key)
    if partial.get("excluded_results"):
        total["excluded_results"] = total.get("excluded_results", 0) + int(partial.get("excluded_results", 0) or 0)
    if partial.get("failure_reason") and not total.get("failure_reason"):
        total["failure_reason"] = partial.get("failure_reason")


def _run_serper_claim_task(index: int, claim_type: str, field_name: str, query: str, city: str, state: str) -> dict:
    local_stats = {"queries": 0, "cache_hits": 0}
    source = _serper_claim_source(
        query,
        claim_type,
        city,
        state,
        local_stats,
        request_timeout=SERPER_TRUST_REQUEST_TIMEOUT_SECONDS,
    )
    return {
        "index": index,
        "claim_type": claim_type,
        "field_name": field_name,
        "query": query,
        "source": source,
        "stats": local_stats,
    }


def _serper_claim_sources_parallel(job_type: str, city: str, state: str, result: dict) -> tuple[list[dict], dict]:
    """Resolve claim-level Serper sources concurrently with a bounded budget.

    The caller receives partial successes if individual searches fail or the
    overall 30s budget expires. At most five Serper-crediting requests are
    submitted per lookup.
    """
    claims = _serper_claim_queries(job_type, city, state, result)[:SERPER_TRUST_MAX_QUERIES]
    stats = {"queries": 0, "cache_hits": 0}
    if not claims:
        return [], stats

    max_workers = min(SERPER_TRUST_MAX_CONCURRENCY, len(claims))
    completed: dict[int, dict] = {}
    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures = {
        executor.submit(_run_serper_claim_task, idx, claim_type, field_name, query, city, state): idx
        for idx, (claim_type, field_name, query) in enumerate(claims)
    }
    try:
        for future in as_completed(futures, timeout=SERPER_TRUST_TOTAL_TIMEOUT_SECONDS):
            idx = futures[future]
            try:
                item = future.result()
            except Exception as e:
                item = {
                    "index": idx,
                    "claim_type": claims[idx][0],
                    "field_name": claims[idx][1],
                    "query": claims[idx][2],
                    "source": None,
                    "stats": {"provider_failed": True, "failure_reason": type(e).__name__},
                }
                print(f"[trust] Serper {claims[idx][0]} failed in worker (non-fatal): {e}")
            _merge_serper_stats(stats, item.get("stats"))
            completed[idx] = item
    except FuturesTimeout:
        stats["timed_out"] = True
        stats["failure_reason"] = stats.get("failure_reason") or "total_timeout"
        print("[trust] Serper parallel enrichment total timeout reached; using partial sources")
    finally:
        for future in futures:
            if not future.done():
                future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

    return [completed[i] for i in sorted(completed)], stats


def _serper_claim_sources_sequential(job_type: str, city: str, state: str, result: dict) -> tuple[list[dict], dict]:
    """Legacy serial Serper claim resolution retained for benchmarks/tests."""
    claims = _serper_claim_queries(job_type, city, state, result)[:SERPER_TRUST_MAX_QUERIES]
    stats = {"queries": 0, "cache_hits": 0}
    completed = []
    deadline = time.monotonic() + SERPER_TRUST_TOTAL_TIMEOUT_SECONDS
    for idx, (claim_type, field_name, query) in enumerate(claims):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            stats["timed_out"] = True
            stats["failure_reason"] = stats.get("failure_reason") or "total_timeout"
            break
        local_stats = {"queries": 0, "cache_hits": 0}
        source = _serper_claim_source(
            query,
            claim_type,
            city,
            state,
            local_stats,
            request_timeout=min(SERPER_TRUST_REQUEST_TIMEOUT_SECONDS, max(1, remaining)),
        )
        _merge_serper_stats(stats, local_stats)
        completed.append({
            "index": idx,
            "claim_type": claim_type,
            "field_name": field_name,
            "query": query,
            "source": source,
            "stats": local_stats,
        })
    return completed, stats


def _attach_serper_claim_results(result: dict, claim_results: list[dict], stats: dict) -> int:
    attached = 0
    for item in sorted(claim_results, key=lambda x: x.get("index", 0)):
        source = item.get("source")
        if source and source.get("url"):
            source_class = source.get("source_class") or classify_source_url(source.get("url") or "")
            if source_class == SOURCE_CLASS_EXCLUDED:
                continue
            public_source = {
                "url": source.get("url"),
                "title": source.get("title") or source.get("url"),
                "verified_at": source.get("verified_at") or _today_iso_date(),
                "source_type": source_class.lower(),
            }
            if source_class == SOURCE_CLASS_SUPPLEMENTARY:
                public_source["supplementary"] = True
                public_source["source_label"] = "supplementary reference"
            result[item.get("field_name")] = public_source
            _append_source_url(result, public_source)
            attached += 1
    result["serper_credits_used"] = min(int(stats.get("queries", 0) or 0), SERPER_TRUST_MAX_QUERIES)
    result["serper_cache_hits"] = int(stats.get("cache_hits", 0) or 0)
    if attached >= 3:
        result["sources_status"] = "serper_verified"
    elif attached > 0:
        result["sources_status"] = "serper_partial"
    else:
        result["sources_status"] = "serper_unavailable"
    if stats.get("failure_reason"):
        result["sources_failure_reason"] = stats.get("failure_reason")
    if not result.get("badge_state"):
        meta = result.get("_meta") if isinstance(result.get("_meta"), dict) else {}
        data_source = str(result.get("data_source") or meta.get("city_match_level") or "").lower()
        if attached >= 3 and (data_source in {"city_database", "city", "verified"} or meta.get("auto_verified")):
            result["badge_state"] = "verified"
        elif attached > 0:
            result["badge_state"] = "ai_researched"
        else:
            result["badge_state"] = "limited"
    return attached


def _enrich_result_with_serper_sources_sequential(result: dict, job_type: str, city: str, state: str) -> dict:
    """Legacy serial enrichment path used by the benchmark script."""
    if not isinstance(result, dict):
        return result
    stats = {"queries": 0, "cache_hits": 0}
    try:
        claim_results, stats = _serper_claim_sources_sequential(job_type, city, state, result)
        attached = _attach_serper_claim_results(result, claim_results, stats)
        print(
            f"[trust] Serper serial credits used: {result['serper_credits_used']} "
            f"(cache hits: {result['serper_cache_hits']}, attached: {attached}, status: {result['sources_status']})"
        )
    except Exception as e:
        result["serper_credits_used"] = int(stats.get("queries", 0))
        result["serper_cache_hits"] = int(stats.get("cache_hits", 0))
        result["sources_status"] = "serper_unavailable"
        result["sources_failure_reason"] = type(e).__name__
        print(f"[trust] Serper serial enrichment failed (non-fatal): {e}")
    return result

def enrich_result_with_serper_sources(result: dict, job_type: str, city: str, state: str) -> dict:
    """Attach claim-level Google/Serper source URLs without changing existing fields.

    Adds fee_source, ahj_contact_source, code_section_source,
    required_documents_source, inspection_process_source, sources_status,
    serper_credits_used, and serper_cache_hits. Never raises.
    """
    if not isinstance(result, dict):
        return result
    stats = {"queries": 0, "cache_hits": 0}
    try:
        claim_results, stats = _serper_claim_sources_parallel(job_type, city, state, result)
        attached = _attach_serper_claim_results(result, claim_results, stats)
        print(
            f"[trust] Serper credits used: {result['serper_credits_used']} "
            f"(cache hits: {result['serper_cache_hits']}, attached: {attached}, status: {result['sources_status']})"
        )
    except Exception as e:
        result["serper_credits_used"] = int(stats.get("queries", 0))
        result["serper_cache_hits"] = int(stats.get("cache_hits", 0))
        result["sources_status"] = "serper_unavailable"
        result["sources_failure_reason"] = type(e).__name__
        print(f"[trust] Serper enrichment failed (non-fatal): {e}")
    return result


def _companion_trigger(permit_type: str, job_type: str) -> str:
    p = (permit_type or "").lower()
    job = (job_type or "").lower()
    like_for_like = any(x in job for x in ["like for like", "like-for-like", "swap", "condenser", "replacement", "replace"])

    if "elect" in p:
        if any(x in job for x in ["solar", "pv", "battery"]):
            return "inverter, battery, disconnect, panel tie-in, service equipment, or new/modified wiring is included"
        if any(x in job for x in ["hvac", "ac", "air condition", "condenser", "heat pump", "furnace"]):
            return "new wiring, disconnect replacement, breaker/panel modification, or a new circuit is included"
        if "ev" in job or "charger" in job:
            return "a new 240V circuit, breaker, load calculation, panel work, or hardwired EVSE is included"
        return "new circuits, panel work, outlet relocation, disconnect replacement, or wiring modifications are included"
    if "gas" in p:
        if like_for_like:
            return "gas piping modification, gas appliance connection change, regulator change, venting change, or a new gas line is included"
        return "gas piping modification, gas appliance connection, regulator/venting change, or a new gas line is included"
    if "plumb" in p:
        return "pipe relocation, fixture move, water/sanitary line work, or DWV modification is included"
    if "mechanical" in p or "hvac" in p:
        return "new HVAC equipment, duct routing, exhaust/ventilation, combustion air, or condensate routing is added or modified"
    if "struct" in p or "building" in p:
        if any(x in job for x in ["solar", "pv", "battery"]):
            return "roof racking, attachments, penetrations, battery mounting, or structural load review is required"
        return "framing, load-bearing elements, structural openings, roof/decking, or exterior envelope changes are included"
    if "utility" in p or "interconnection" in p or "coordination" in p:
        if any(x in job for x in ["solar", "pv", "battery"]):
            return "grid-tied PV export, net metering, battery backup, meter changes, or utility interconnection is included"
        return "service disconnect/reconnect, meter pull, utility-side service change, or grid interconnection is required"
    if "fire" in p:
        return "fire-rated assemblies, alarms, sprinklers, battery/fire review, or egress/fire separation changes are included"
    return "that trade's scope is added, relocated, upgraded, or modified beyond the primary permit scope"


def hedge_companion_permits(result: dict, job_type: str) -> dict:
    """Convert companion permits from unconditional Required to scoped conditions.

    Keeps primary permits in permits_required untouched; companion_permits are
    secondary and should read as conditional triggers for contractor trust.
    """
    if not isinstance(result, dict):
        return result
    companions = result.get("companion_permits") or []
    if not isinstance(companions, list):
        result["companion_permits"] = []
        return result
    hedged = []
    for cp in companions:
        if not isinstance(cp, dict):
            continue
        item = dict(cp)
        ptype = item.get("permit_type") or item.get("name") or "Companion Permit"
        trigger = item.get("required_if")
        existing_reason = str(item.get("reason") or "").strip()
        if not trigger and existing_reason.lower().startswith("may be required if"):
            trigger = re.sub(r"^may be required if\s*:?\s*", "", existing_reason, flags=re.I)
        if not trigger:
            trigger = _companion_trigger(ptype, job_type)
        trigger = str(trigger).strip().rstrip(".")
        # Solar/battery utility coordination is not an electrical permit; label
        # it as interconnection/PTO when the model blends those concepts.
        if any(x in (job_type or "").lower() for x in ["solar", "pv", "battery"]) and any(x in str(ptype).lower() for x in ["utility", "interconnection", "meter"]):
            ptype = "Utility Interconnection / Permission to Operate"
        item["permit_type"] = ptype
        item["required_if"] = trigger
        item["requirement_label"] = "May be required based on scope"
        item["reason"] = f"May be required if: {trigger}."
        if str(item.get("certainty", "")).lower() in ("almost_certain", "required", "mandatory"):
            item["certainty"] = "conditional"
        elif not item.get("certainty"):
            item["certainty"] = "possible"
        hedged.append(item)
    result["companion_permits"] = hedged
    if hedged:
        print("[trust] Companion permit hedging applied: " + "; ".join(f"{h.get('permit_type')} — {h.get('reason')}" for h in hedged[:5]))
    return result


def _scope_has_any(job: str, phrases: list[str]) -> bool:
    return any(p in job for p in phrases)


def _scope_permit(permit_type: str, portal_selection: str, notes: str, required: bool | str = True) -> dict:
    return {
        "permit_type": permit_type,
        "portal_selection": portal_selection,
        "required": required,
        "notes": notes,
    }


def classify_scope_required_permits(job_type: str) -> dict | None:
    """Deterministically classify permits for high-confidence job scopes.

    This is intentionally narrow: it only overrides the model when the job text
    clearly matches a scope in the Tier B matrix. Other jobs keep model output
    and receive derived logic only.
    """
    job = (job_type or "").lower()
    job = re.sub(r"\s+", " ", job).strip()
    if not job:
        return None

    has_panel = _scope_has_any(job, ["panel upgrade", "service upgrade", "new panel", "subpanel", "sub-panel", "200 amp", "200amp", "400 amp", "400amp"])
    has_gas_line = _scope_has_any(job, ["gas line", "gas piping", "new gas", "relocate gas", "gas modification"])
    has_new_fixtures = _scope_has_any(job, ["new fixture", "new fixtures", "add fixture", "add fixtures", "fixture relocation", "new bathroom", "new kitchen"])
    has_solar = _scope_has_any(job, ["solar", " pv", "photovoltaic"])
    has_battery = _scope_has_any(job, ["battery", "ess", "energy storage", "powerwall"])

    logic: list[dict] = []

    def add_logic(permit_type: str, because: str, trigger: str) -> None:
        logic.append({"permit_type": permit_type, "included_because": because, "scope_trigger": trigger})

    primary_scope = detect_primary_scope(job)
    if _is_commercial_ti_scope(primary_scope, job):
        permits, commercial_logic = _commercial_ti_required_permit_set(primary_scope, job)
        logic.extend(commercial_logic)
        return {
            "scope_classification": primary_scope,
            "permits_required": permits,
            "permits_required_logic": logic,
            "companion_permits": _commercial_ti_secondary_companions(primary_scope),
        }

    # Solar first so "roof solar" scopes don't collapse into a reroof permit.
    if has_solar:
        # Mount-type detection: prevents the "ground-mount job labeled as Roof-Mounted
        # Racking" bug Opus flagged on the Montpelier VT 12kW PV review (2026-04-27).
        is_ground_mount = _scope_has_any(job, [
            "ground-mount", "ground mount", "ground-mounted", "ground mounted",
            "ground array", "pole-mount", "pole mount", "pole-mounted", "pole mounted",
        ])
        is_carport = _scope_has_any(job, ["carport", "solar canopy", "solar carport", "patio cover solar"])
        is_bipv = _scope_has_any(job, ["bipv", "building-integrated", "building integrated pv", "solar shingle", "solar tile", "tesla solar roof"])

        if is_ground_mount:
            building_permit_name = "Building Permit — Solar PV (Ground-Mount Foundation & Racking)"
            portal_label = "Building - Solar PV / Ground-Mount Foundation"
            building_notes = (
                "Required for ground-mount foundation engineering (concrete piers, helical piles, "
                "or driven posts), frost-depth compliance, racking structural design, lateral wind/snow "
                "loads, and trench/conduit routing from the array to the main service."
            )
            mount_logic_phrase = "Solar PV ground-mount work needs foundation + structural review under the building permit (no roof penetrations)."
            mount_trigger = "ground-mount/pole-mount in job description"
        elif is_carport:
            building_permit_name = "Building Permit — Solar PV (Carport / Canopy Structure)"
            portal_label = "Building - Solar PV / Carport Canopy"
            building_notes = (
                "Required for solar carport canopy structural design (columns, beams, footings), "
                "lateral bracing, drainage routing, racking attachment, and clearance review."
            )
            mount_logic_phrase = "Solar PV carport canopy needs full structural review (foundations + lateral) under the building permit."
            mount_trigger = "carport/solar canopy in job description"
        elif is_bipv:
            building_permit_name = "Building Permit — Solar PV (BIPV Building-Integrated)"
            portal_label = "Building - Solar PV / BIPV"
            building_notes = (
                "Required for building-integrated PV review covering envelope penetrations, "
                "weatherproofing, fire-rating, and integrated structural/electrical scope."
            )
            mount_logic_phrase = "BIPV scope needs envelope + structural + integrated electrical review under the building permit."
            mount_trigger = "BIPV/solar shingle in job description"
        else:
            # Default: roof-mount. Only label this way when no other mount type is detected.
            building_permit_name = "Building Permit — Solar PV (Structural Racking & Roof Penetrations)"
            portal_label = "Building - Solar PV / Roof-Mounted Racking"
            building_notes = "Required for rooftop racking, roof penetrations, and structural load review."
            mount_logic_phrase = "Solar PV roof work needs structural/racking review, but Building and Structural are one permit."
            mount_trigger = "solar/pv (roof-mount default) in job description"

        permits = [
            _scope_permit(building_permit_name, portal_label, building_notes),
            _scope_permit(
                "Electrical Permit — Solar PV" + (" + Battery ESS" if has_battery else ""),
                "Electrical - Solar PV" + (" and Battery Energy Storage" if has_battery else " System"),
                "Required for inverter, rapid shutdown, DC/AC disconnects, panel tie-in" + (", and ESS wiring/listing per NEC 706 + NFPA 855." if has_battery else "."),
            ),
        ]
        add_logic(permits[0]["permit_type"], mount_logic_phrase, mount_trigger)
        add_logic(permits[1]["permit_type"], "Solar PV electrical work falls under NEC Article 690; battery scope adds ESS review under NEC 706 + NFPA 855.", "battery/ESS present" if has_battery else "solar/pv electrical scope")
        return {
            "scope_classification": "solar_pv_battery" if has_battery else "solar_pv",
            "permits_required": permits,
            "permits_required_logic": logic,
            "companion_permits": [{
                "permit_type": "Utility Interconnection / Permission to Operate",
                "reason": "May be required if: grid-tied PV export, net metering, battery backup, meter changes, or utility PTO is included.",
                "required_if": "grid-tied PV export, net metering, battery backup, meter changes, or utility PTO is included",
                "certainty": "conditional",
                "requirement_label": "May be required based on scope",
            }],
        }

    is_adu = _scope_has_any(job, ["adu", "accessory dwelling", "garage conversion", "in-law suite", "granny flat", "junior accessory dwelling", "jadu"])
    if is_adu:
        permits = [
            _scope_permit("Building Permit — ADU Conversion / Residential Alteration", "Building - Accessory Dwelling Unit (ADU)", "Master building permit for occupancy conversion, life-safety, framing, fire separation, and egress."),
            _scope_permit("Electrical Permit — ADU Circuits / Service Load", "Electrical - ADU / Residential Alteration", "Required for new circuits, smoke/CO alarms, load calculations, subpanel, or service changes."),
            _scope_permit("Plumbing Permit — ADU Kitchen/Bath/Laundry", "Plumbing - ADU Fixtures / DWV", "Required for kitchen, bath, laundry, water supply, sewer, and DWV work."),
            _scope_permit("Mechanical Permit — ADU HVAC / Ventilation", "Mechanical - ADU HVAC / Ventilation", "Required for HVAC, bath/kitchen exhaust, ventilation, combustion air, or ducts."),
        ]
        add_logic(permits[0]["permit_type"], "ADU conversion changes occupancy/use and needs a master building permit.", "ADU/conversion scope")
        add_logic(permits[1]["permit_type"], "ADUs require dedicated electrical scope for circuits, alarms, load calculations, and possible subpanel/service work.", "ADU electrical systems")
        add_logic(permits[2]["permit_type"], "ADUs normally add or legalize kitchen/bath/laundry plumbing and DWV work.", "ADU plumbing fixtures")
        add_logic(permits[3]["permit_type"], "ADUs need conditioned-space HVAC/ventilation review under mechanical code and Title 24 in California.", "ADU mechanical/ventilation systems")
        return {
            "scope_classification": "adu_conversion",
            "permits_required": permits,
            "permits_required_logic": logic,
            "companion_permits": [],
        }

    is_roof = _scope_has_any(job, ["roof", "reroof", "re-roof", "tear-off", "tear off", "shingle"])
    roof_simple = is_roof and not _scope_has_any(job, ["skylight", "solar", "structural", "rafter", "truss", "decking replacement", "new opening"])
    if roof_simple:
        permits = [_scope_permit("Roofing Permit — Tear-Off / Re-Roof", "Roofing - Residential Re-Roof", "Required for roof tear-off and reroof; no companion permit unless skylights, solar, or structural work are added.")]
        add_logic(permits[0]["permit_type"], "Roof tear-off/re-roof scope is isolated roofing work with no skylight, solar, or structural trigger stated.", "roof/reroof keywords without companion triggers")
        return {
            "scope_classification": "roof_reroof_only",
            "permits_required": permits,
            "permits_required_logic": logic,
            "companion_permits": [],
        }

    is_hvac = _scope_has_any(job, ["hvac", "condenser", "air conditioner", "air conditioning", " ac ", "a/c", "heat pump", "furnace", "mini split", "mini-split", "ductless", "air handler"])
    is_water_heater = "water heater" in job
    hvac_like_for_like = _scope_has_any(job, ["like for like", "like-for-like", "swap", "changeout", "change out", "condenser", "replacement", "replace", "mini split", "mini-split", "ductless"])
    if is_hvac or is_water_heater:
        if is_water_heater and not is_hvac:
            ptype = "Plumbing Permit — Water Heater Replacement"
            portal = "Plumbing - Water Heater Replacement"
            notes = "Required for water heater replacement; companion electrical/gas permits are suppressed unless gas piping, venting conversion, or new circuit work is explicit."
            base_reason = "Water heater swap is one trade permit when same location/capacity and no new gas/electrical scope is stated."
        else:
            ptype = "Mechanical Permit — HVAC Equipment Changeout (Residential)" if hvac_like_for_like else "Mechanical Permit — HVAC System Replacement (Residential)"
            portal = "Mechanical - HVAC Changeout / Replacement"
            notes = "Required for HVAC equipment replacement/changeout; companion permits are suppressed unless panel work, gas-line modification, or new fixtures are explicit."
            base_reason = "HVAC equipment swap/changeout triggers the mechanical permit; no companion trade permit is included without explicit extra scope."
        permits = [_scope_permit(ptype, portal, notes)]
        add_logic(permits[0]["permit_type"], base_reason, "HVAC/water-heater replacement scope")
        companions: list[dict] = []
        if has_panel:
            ep = _scope_permit("Electrical Permit — Panel / Service Upgrade", "Electrical - Panel Upgrade / Service Change", "Required because panel or service upgrade work is explicitly included.")
            permits.append(ep)
            add_logic(ep["permit_type"], "Electrical permit required because the job explicitly includes panel/service upgrade work.", "panel/service upgrade stated")
        if has_gas_line:
            gp = _scope_permit("Gas Permit — Gas Line Modification", "Mechanical/Gas - Gas Line Modification", "Required because gas piping modification is explicitly included.")
            permits.append(gp)
            add_logic(gp["permit_type"], "Gas permit required because gas line or gas piping modification is explicitly stated.", "gas-line modification stated")
        if has_new_fixtures:
            pp = _scope_permit("Plumbing Permit — New Fixture Work", "Plumbing - Fixture Addition / Relocation", "Required because new fixture work is explicitly included.")
            permits.append(pp)
            add_logic(pp["permit_type"], "Plumbing permit required because new fixtures or fixture relocation are explicitly stated.", "new fixture work stated")
        return {
            "scope_classification": "hvac_or_water_heater_with_explicit_companions" if len(permits) > 1 else "hvac_or_water_heater_single_trade",
            "permits_required": permits,
            "permits_required_logic": logic,
            "companion_permits": companions,
        }

    return None



_RESIDENTIAL_PRIMARY_SCOPE_ALIASES = {"residential", "residential_adu", "residential_detached_adu", "residential_jadu", "residential_hillside_adu", "residential_garage_conversion", "residential_kitchen_remodel", "residential_addition", "residential_water_heater", "residential_hvac_changeout", "residential_panel_upgrade", "residential_reroof", "residential_window_replacement", "residential_foundation", "residential_deck"}


def infer_residential_scope(job_type: str, primary_scope: str = "") -> str:
    """A9: infer specific residential scope for permit-name normalization only."""
    scope = (primary_scope or "").strip().lower().replace("-", "_").replace(" ", "_")
    if scope in _RESIDENTIAL_PRIMARY_SCOPE_ALIASES and scope not in ("residential", "residential_adu"):
        return scope
    job = re.sub(r"\s+", " ", (job_type or "").lower()).strip()
    if not job:
        return scope or "residential"

    is_adu = _scope_has_any(job, ["adu", "accessory dwelling", "detached accessory dwelling", "dadu", "granny flat", "in-law suite", "in law suite", "secondary dwelling", "secondary unit"])
    if is_adu and _scope_has_any(job, ["hillside", "slope", "steep", "grading", "geotech", "geology"]):
        return "residential_hillside_adu"
    if _scope_has_any(job, ["jadu", "junior accessory dwelling", "junior adu"]):
        return "residential_jadu"
    if is_adu and _scope_has_any(job, ["garage conversion", "convert garage", "garage into", "attached garage"]):
        return "residential_garage_conversion"
    if is_adu and _scope_has_any(job, ["detached", "new build", "new construction", "dadu", "backyard cottage", "standalone", "stand-alone"]):
        return "residential_detached_adu"
    if is_adu:
        return "residential_detached_adu"
    if _scope_has_any(job, ["kitchen remodel", "kitchen renovation"]):
        return "residential_kitchen_remodel"
    if _scope_has_any(job, ["addition", "add bedroom", "add bathroom", "room addition", "sf addition", "sq ft addition", "square foot addition"]):
        return "residential_addition"
    if "water heater" in job:
        return "residential_water_heater"
    if _scope_has_any(job, ["hvac", "condenser", "air conditioner", "air conditioning", "a/c", "heat pump", "furnace", "mini split", "mini-split", "changeout", "change out"]):
        return "residential_hvac_changeout"
    if _scope_has_any(job, ["panel upgrade", "service upgrade", "200 amp", "200amp", "400 amp", "400amp", "electrical panel"]):
        return "residential_panel_upgrade"
    if _scope_has_any(job, ["reroof", "re-roof", "roof tear", "tear-off", "tear off", "shingle roof", "replace roof"]):
        return "residential_reroof"
    if _scope_has_any(job, ["window replacement", "replace window", "windows", "window/door", "door replacement"]):
        return "residential_window_replacement"
    if _scope_has_any(job, ["foundation", "pier and beam", "pier-and-beam", "slab repair", "structural foundation"]):
        return "residential_foundation"
    if _scope_has_any(job, ["deck", "patio cover", "balcony"]):
        return "residential_deck"
    return scope or "residential"


def _extract_square_footage(job_type: str) -> str:
    text = job_type or ""
    match = re.search(r"(\d[\d,]*)\s*(?:sq\.?\s*ft|sf|square\s*feet|square\s*foot)", text, re.I)
    return match.group(1).replace(",", "") if match else ""


def _residential_specific_permit_name(scope: str, job_type: str, city: str, state: str) -> str | None:
    city_l = (city or "").strip().lower()
    state_u = (state or "").strip().upper()
    sf = _extract_square_footage(job_type)
    if scope == "residential_detached_adu":
        if city_l == "seattle" and state_u == "WA":
            return "DADU Building Permit"
        return "Detached ADU Building Permit"
    if scope == "residential_jadu":
        return "JADU Conversion Permit"
    if scope == "residential_hillside_adu":
        if city_l in ("los angeles", "la") and state_u == "CA":
            return "Hillside ADU Residential Building Permit (LADBS)"
        return "Hillside ADU Residential Building Permit"
    if scope == "residential_garage_conversion":
        return "Garage Conversion Building Permit + Change of Occupancy (per scope)"
    if scope == "residential_kitchen_remodel":
        return "Residential Alteration — Kitchen Remodel"
    if scope == "residential_addition":
        return f"Residential Building Permit — Addition ({sf} sf)" if sf else "Residential Building Permit — Addition"
    if scope == "residential_water_heater":
        return "Plumbing Permit — Water Heater Replacement"
    if scope == "residential_hvac_changeout":
        return "Mechanical Permit — HVAC Equipment Replacement"
    if scope == "residential_panel_upgrade":
        return "Electrical Permit — Service Upgrade (200A)" if re.search(r"200\s*amp|200a", job_type or "", re.I) else "Electrical Permit — Service Upgrade"
    if scope == "residential_reroof":
        return "Roofing Permit — Reroof"
    if scope == "residential_window_replacement":
        if city_l in ("los angeles", "la") and state_u == "CA":
            return "Express Permit — Window/Door Replacement"
        return "Building Permit — Window/Door Replacement"
    if scope == "residential_foundation":
        return "Building Permit — Foundation Repair (Pier and Beam)" if re.search(r"pier", job_type or "", re.I) else "Building Permit — Foundation Repair"
    if scope == "residential_deck":
        return f"Building Permit — Deck ({sf} sf)" if sf else "Building Permit — Deck"
    return None


def _is_already_specific_residential_name(name: str, scope: str) -> bool:
    text = (name or "").lower()
    if scope == "residential_hillside_adu":
        return "hillside" in text and "adu" in text
    wanted = {
        "residential_detached_adu": ("detached adu", "dadu", "accessory dwelling"),
        "residential_jadu": ("jadu", "junior accessory"),
        "residential_garage_conversion": ("garage conversion", "change of occupancy"),
        "residential_kitchen_remodel": ("kitchen",),
        "residential_addition": ("addition",),
        "residential_water_heater": ("water heater",),
        "residential_hvac_changeout": ("hvac", "equipment", "changeout", "replacement"),
        "residential_panel_upgrade": ("service upgrade", "panel upgrade", "200a"),
        "residential_reroof": ("reroof", "re-roof", "roofing"),
        "residential_window_replacement": ("window", "door"),
        "residential_foundation": ("foundation", "structural"),
        "residential_deck": ("deck",),
    }.get(scope, ())
    return bool(wanted and any(w in text for w in wanted))


def apply_residential_permit_name_specificity(result: dict, job_type: str, city: str, state: str) -> dict:
    """A9: replace vague residential permit labels with scope-specific names.

    Runs after permit assembly. It never adds permits and never touches commercial
    scopes, preserving A3 office/retail TI behavior and simple-trade one-permit
    residential outputs.
    """
    if not isinstance(result, dict):
        return result
    primary_scope = result.get("_primary_scope") or detect_primary_scope(job_type or "")
    result.setdefault("_primary_scope", primary_scope)
    if primary_scope in _COMMERCIAL_PRIMARY_SCOPES:
        return result

    scope = infer_residential_scope(job_type, primary_scope)
    specific = _residential_specific_permit_name(scope, job_type or "", city, state)
    permits = result.get("permits_required")
    if not specific or not isinstance(permits, list) or not permits:
        return result

    generic_names = {"residential alteration", "adu conversion", "building permit", "garage conversion", "hillside", "plumbing", "mechanical", "electrical"}
    renamed = []
    for idx, permit in enumerate(permits):
        if not isinstance(permit, dict):
            continue
        name = str(permit.get("permit_type") or permit.get("name") or "").strip()
        family = _permit_family(permit)
        is_primary_slot = idx == 0 or (scope in ("residential_water_heater",) and family == "plumbing") or (scope in ("residential_hvac_changeout",) and family == "mechanical") or (scope in ("residential_panel_upgrade",) and family == "electrical") or (scope not in ("residential_water_heater", "residential_hvac_changeout", "residential_panel_upgrade") and family in ("building", ""))
        if not is_primary_slot:
            # Secondary trade permit names get their own specificity from their family/scope below.
            if scope == "residential_kitchen_remodel" and family == "electrical" and not _is_already_specific_residential_name(name, "residential_panel_upgrade"):
                if re.search(r"panel|service|200\s*amp|200a", job_type or "", re.I):
                    permit["permit_type"] = _residential_specific_permit_name("residential_panel_upgrade", job_type or "", city, state)
                    renamed.append({"index": idx, "from": name, "to": permit["permit_type"]})
            continue
        if _is_already_specific_residential_name(name, scope):
            continue
        if name.lower() in generic_names or len(name) < 28 or any(g in name.lower() for g in generic_names):
            permit["permit_type"] = specific
            if not permit.get("portal_selection") or str(permit.get("portal_selection", "")).strip().lower() in generic_names:
                permit["portal_selection"] = specific
            renamed.append({"index": idx, "from": name, "to": specific})
            continue

    if renamed:
        result["_a9_residential_permit_names"] = {"scope": scope, "renamed": renamed}
        logic = result.get("permits_required_logic")
        if isinstance(logic, list):
            for item in logic:
                if isinstance(item, dict):
                    for change in renamed:
                        if item.get("permit_type") == change["from"]:
                            item["permit_type"] = change["to"]
    return result

def _derive_permit_logic(result: dict) -> list[dict]:
    logic = []
    for permit in result.get("permits_required") or []:
        if not isinstance(permit, dict):
            continue
        ptype = permit.get("permit_type") or "Permit"
        because = permit.get("notes") or permit.get("portal_selection") or "Included by jurisdiction-specific permit research for this job scope."
        logic.append({"permit_type": ptype, "included_because": str(because), "scope_trigger": "model/jurisdiction research"})
    return logic


def apply_scope_aware_permit_classification(result: dict, job_type: str) -> dict:
    """Apply Tier B scope-aware permits and always add permits_required_logic."""
    if not isinstance(result, dict):
        return result
    classified = classify_scope_required_permits(job_type)
    if classified:
        result["permits_required"] = classified["permits_required"]
        result["permits_required_logic"] = classified["permits_required_logic"]
        result["companion_permits"] = classified.get("companion_permits", result.get("companion_permits", []))
        if classified["permits_required"]:
            result["permit_verdict"] = "YES"
    elif not isinstance(result.get("permits_required_logic"), list) or not result.get("permits_required_logic"):
        result["permits_required_logic"] = _derive_permit_logic(result)
    return result




def _permit_family(permit: dict) -> str:
    """Best-effort family classifier for required permit de-duping."""
    if not isinstance(permit, dict):
        return ""
    text = " ".join(str(permit.get(k) or "") for k in ("permit_type", "portal_selection", "notes")).lower()
    if any(t in text for t in ("fire alarm", "sprinkler", "fire sprinkler", "fire suppression")):
        return "fire"
    if "sign" in text or "signage" in text:
        return "sign"
    if "plumb" in text or "fixture" in text or "restroom" in text or "sewer" in text:
        return "plumbing"
    if "mechanical" in text or "hvac" in text or "duct" in text or "ventilation" in text or "rtu" in text:
        return "mechanical"
    if "electrical" in text or "lighting" in text or "branch circuit" in text or "panel" in text:
        return "electrical"
    if "building" in text or "tenant improvement" in text or "alteration" in text or "interior" in text:
        return "building"
    return ""


def _ahj_companion_permit_name(family: str, primary_scope: str, city: str, state: str, verified: bool) -> tuple[str, str]:
    """Return AHJ-safe permit labels.

    verified_cities.db currently verifies AHJ existence/contact/portal, not a
    per-family permit-name catalog. When the AHJ row exists, use conservative
    portal-style commercial labels; otherwise use plain generic family names.
    Both avoid fabricated city-specific titles.
    """
    if primary_scope == "commercial_medical_clinic_ti":
        scope_label = "Commercial Medical Clinic TI"
    elif primary_scope == "commercial_office_ti":
        scope_label = "Commercial Office TI"
    elif primary_scope == "commercial_restaurant":
        scope_label = "Commercial Restaurant TI"
    elif primary_scope == "commercial":
        scope_label = "Commercial Tenant Improvement"
    else:
        scope_label = "Commercial Retail TI"
    generic = {
        "building": (f"Building Permit — Tenant Improvement ({scope_label})", "Building - Tenant Improvement / Alteration"),
        "mechanical": ("Mechanical Permit", "Mechanical Permit"),
        "plumbing": ("Plumbing Permit", "Plumbing Permit"),
        "electrical": ("Electrical Permit", "Electrical Permit"),
        "sign": ("Sign Permit", "Sign Permit"),
        "fire": ("Fire Alarm / Fire Sprinkler Permit", "Fire Alarm / Fire Sprinkler Permit"),
    }
    verified_names = {
        "building": (f"Building Permit — Tenant Improvement / Commercial Alteration ({scope_label})", "Commercial Building Permit - Tenant Improvement"),
        "mechanical": ("Mechanical Permit — Commercial Tenant Improvement", "Mechanical Permit - Commercial Interior Alteration"),
        "plumbing": ("Plumbing Permit — Commercial Tenant Improvement", "Plumbing Permit - Commercial Interior Alteration"),
        "electrical": ("Electrical Permit — Commercial Tenant Improvement", "Electrical Permit - Commercial Interior Alteration"),
        "sign": ("Sign Permit — Commercial Storefront / Wall Sign", "Sign Permit - Commercial"),
        "fire": ("Fire Alarm / Fire Sprinkler Permit — Commercial Tenant Improvement", "Fire Alarm / Fire Sprinkler Permit - Commercial"),
    }
    return (verified_names if verified else generic).get(family, generic[family])


# A6 (2026-04-28): deterministic retail TI rulebook enrichment. Complements
# hidden-trigger detection with retail-specific content so retail TI no longer
# gets thin office/restaurant leftovers.
def apply_retail_ti_rulebook(result: dict, job_type: str, city: str, state: str) -> dict:
    if not isinstance(result, dict):
        return result
    primary_scope = result.get("_primary_scope") or detect_primary_scope(job_type or "")
    result.setdefault("_primary_scope", primary_scope)
    if primary_scope != "commercial_retail_ti":
        return result

    def ensure_list(key: str) -> list:
        if not isinstance(result.get(key), list):
            result[key] = []
        return result[key]

    def add_unique(key: str, items: list[str]) -> None:
        arr = ensure_list(key)
        seen = {str(x).strip().lower() for x in arr if isinstance(x, str)}
        for item in items:
            if item.lower() not in seen:
                arr.append(item)
                seen.add(item.lower())

    city_state = f"{city}, {state}".strip(", ")
    add_unique("pro_tips", [
        "Coordinate the sign permit with the landlord's master sign program before ordering storefront signage.",
        "Confirm retail parking ratio and accessible parking/path-of-travel scope before lease execution or final layout.",
        "If the tenant sells food, groceries, coffee, alcohol, or cannabis, start health/licensing/zoning review in parallel with the building TI.",
        "Package storefront elevations, glazing specs, awning details, and sign locations together so facade/design review does not lag the TI.",
    ])
    add_unique("watch_out", [
        "Many cities require a Master Sign Program or landlord sign criteria approval before individual retail sign permits.",
        "Parking, loading, and zoning variances commonly surface on change-of-use retail TIs and can block the certificate of occupancy.",
        "Tenant leases often restrict facade, storefront, awning, penetrations, and signage even when code allows them — get landlord signoff early.",
        "Illuminated signage may need both a sign permit and an electrical sign permit/inspection.",
    ])
    add_unique("common_mistakes", [
        "Skipping storefront/facade design review when windows, awnings, signs, or exterior finishes change.",
        "Underestimating ADA path-of-travel upgrades for a primary-function retail alteration and missing the 20% cost allocation.",
        "Missing storefront glazing and lighting/HVAC energy-code documentation (COMcheck/IECC, Title 24, WSEC-C, or local equivalent).",
        "Submitting retail fixture plans without occupant-load, egress, aisle-width, and exit-sign/emergency-lighting coordination.",
    ])
    add_unique("inspections", [
        "Building rough/final — verify sales-floor layout, accessible route, exits, doors/hardware, and certificate-of-occupancy conditions.",
        "Storefront/facade final — verify glazing safety labels, awning attachment, exterior finishes, and approved elevations.",
        "Sign final / electrical sign inspection — verify sign location, mounting, illumination disconnect, and landlord/master-sign-program compliance.",
        "Fire alarm / sprinkler acceptance test if devices, ceilings, racking, demising walls, or coverage areas changed.",
        "Energy compliance verification — confirm lighting controls, lighting power density, HVAC controls, economizer/ventilation, and storefront glazing documentation.",
    ])

    # Surface companion permits from A6 triggers as first-class companions.
    companions = ensure_list("companion_permits")
    existing_companions = {str(c.get("permit_type") if isinstance(c, dict) else c).strip().lower() for c in companions}
    for trig in result.get("hidden_triggers") or []:
        if not isinstance(trig, dict) or not str(trig.get("id", "")).startswith("retail_"):
            continue
        for permit in trig.get("companion_permits") or []:
            key = str(permit).strip().lower()
            if key and key not in existing_companions:
                companions.append({
                    "permit_type": permit,
                    "reason": trig.get("why_it_matters") or f"Retail TI rulebook trigger {trig.get('id')}",
                    "certainty": "likely" if trig.get("severity") == "medium" else "almost_certain",
                })
                existing_companions.add(key)

    # Make sure critical retail companions can appear even when a thin prompt
    # only says "retail TI" and hidden trigger companion text is sparse.
    baseline = [
        ("Sign Permit", "Retail tenant changes usually include new wall/window/monument signage; most AHJs review signs separately."),
        ("Electrical Sign Permit if illuminated", "Illuminated retail signage generally requires electrical review/inspection under NEC Article 600."),
    ]
    for permit, reason in baseline:
        key = permit.lower()
        if key not in existing_companions:
            companions.append({"permit_type": permit, "reason": reason, "certainty": "almost_certain"})
            existing_companions.add(key)

    return result


# Launch blocker #8 (2026-04-30): deterministic office TI enrichment.
# Keeps ordinary office buildouts contractor-grade instead of thin generic commercial TI.
def apply_office_ti_rulebook(result: dict, job_type: str, city: str, state: str) -> dict:
    if not isinstance(result, dict):
        return result
    primary_scope = result.get("_primary_scope") or detect_primary_scope(job_type or "")
    result.setdefault("_primary_scope", primary_scope)
    if primary_scope != "commercial_office_ti":
        return result

    def ensure_list(key: str) -> list:
        if not isinstance(result.get(key), list):
            result[key] = []
        return result[key]

    def add_unique(key: str, items: list[str]) -> None:
        arr = ensure_list(key)
        seen = {str(x).strip().lower() for x in arr if isinstance(x, str)}
        for item in items:
            if item.lower() not in seen:
                arr.append(item)
                seen.add(item.lower())

    add_unique("pro_tips", [
        "Treat office TI as a building/MEP/fire/accessibility coordination job: demising partitions, ceiling/lighting, HVAC zoning, low-voltage/data, ADA path-of-travel, and fire alarm/sprinkler drawings can each drive separate reviews.",
        "Get existing reflected ceiling, sprinkler, fire alarm, HVAC diffuser/return, and low-voltage pathways before pricing — moving walls without these backgrounds creates change orders.",
        "Confirm landlord/building engineer requirements early for after-hours work, fire-life-safety shutdowns, low-voltage pathways, air-balance reports, and certificate-of-occupancy signoff.",
    ])
    add_unique("watch_out", [
        "Generic office TI answers often miss low-voltage/data permits, fire alarm/sprinkler deferred submittals, and HVAC balancing; those can delay tenant move-in even when the building permit is issued.",
        "New demising walls or conference rooms can break existing egress, strobe visibility, sprinkler spacing, return-air paths, and ventilation assumptions.",
        "ADA path-of-travel and restroom work can be required even when the office remodel feels mostly cosmetic; document the 20% disproportionality analysis if applicable.",
    ])
    add_unique("common_mistakes", [
        "Submitting partition plans without reflected ceiling, sprinkler, alarm/strobe, diffuser/return, and emergency-lighting coordination.",
        "Forgetting commercial energy-code forms for lighting controls, occupancy sensors, daylight zones, HVAC controls, or envelope/glazing changes.",
        "Leaving low-voltage/data/security/access-control out of the permit set or ignoring plenum-rated cable, firestopping, and controlled-door egress release requirements.",
        "Assuming existing restrooms and reception counters are acceptable without verifying accessible route, door clearances, hardware, signage, and path-of-travel obligations.",
    ])
    add_unique("inspections", [
        "Building rough/final — verify demising partitions, rated assemblies, firestopping, doors/hardware, egress, accessibility, and CO/suite conditions.",
        "Electrical rough/final — verify lighting layout, controls, emergency lighting, exit signs, panels, receptacles, and equipment schedules.",
        "Mechanical rough/final or air-balance verification — confirm HVAC zoning, ventilation, diffuser/return layout, thermostat locations, transfer air, and TAB report if required.",
        "Low-voltage / data / access-control inspection if required — verify cable routing, plenum ratings, firestopping, card-reader egress releases, and fire-alarm interface.",
        "Fire alarm / sprinkler acceptance test if partitions, ceilings, notification appliances, duct detectors, or sprinkler heads changed.",
        "Energy compliance final — verify lighting power density, controls, commissioning/checklists, and any COMcheck/Title 24/WSEC documentation.",
    ])

    companions = ensure_list("companion_permits")
    existing_companions = {str(c.get("permit_type") if isinstance(c, dict) else c).strip().lower() for c in companions}
    for trig in result.get("hidden_triggers") or []:
        if not isinstance(trig, dict) or not str(trig.get("id", "")).startswith("office_"):
            continue
        for permit in trig.get("companion_permits") or []:
            key = str(permit).strip().lower()
            if key and key not in existing_companions:
                companions.append({
                    "permit_type": permit,
                    "reason": trig.get("why_it_matters") or f"Office TI rulebook trigger {trig.get('id')}",
                    "certainty": "likely" if trig.get("severity") == "medium" else "almost_certain",
                })
                existing_companions.add(key)

    baseline = [
        ("Low-voltage / data cabling permit if cabling, access control, AV, or security systems are installed", "Office TIs commonly include telecom/data/access-control work that may be permitted separately or inspected by electrical/fire reviewers."),
        ("Fire alarm / fire sprinkler permit if devices or heads change", "Office partitions, ceiling grids, storage, and conference rooms commonly affect alarm notification and sprinkler coverage."),
    ]
    for permit, reason in baseline:
        key = permit.lower()
        if key not in existing_companions:
            companions.append({"permit_type": permit, "reason": reason, "certainty": "likely"})
            existing_companions.add(key)

    return result


# Launch blocker #7 (2026-04-30): deterministic medical clinic TI enrichment.
# Keeps clinic/dental outpatient buildouts from being treated like ordinary office TI.
def apply_medical_clinic_ti_rulebook(result: dict, job_type: str, city: str, state: str) -> dict:
    if not isinstance(result, dict):
        return result
    primary_scope = result.get("_primary_scope") or detect_primary_scope(job_type or "")
    result.setdefault("_primary_scope", primary_scope)
    if primary_scope != "commercial_medical_clinic_ti":
        return result

    def ensure_list(key: str) -> list:
        if not isinstance(result.get(key), list):
            result[key] = []
        return result[key]

    def add_unique(key: str, items: list[str]) -> None:
        arr = ensure_list(key)
        seen = {str(x).strip().lower() for x in arr if isinstance(x, str)}
        for item in items:
            if item.lower() not in seen:
                arr.append(item)
                seen.add(item.lower())

    add_unique("pro_tips", [
        "Treat medical clinic TI as a specialty commercial buildout, not a plain office: confirm exam-room plumbing, medical gas, infection-control/HVAC, accessibility, fire/life-safety, and health-care licensing paths before pricing.",
        "Ask the owner early whether the clinic includes x-ray/radiology, oxygen/nitrous/medical gas, sterilization, lab work, procedure rooms, or state-licensed health-care services — each can add separate reviews.",
        "Coordinate architect/MEP, medical-gas verifier, equipment vendor, and health/licensing reviewer before submitting so room names, fixture schedules, ventilation, and equipment utility loads match.",
    ])
    add_unique("watch_out", [
        "A clinic with exam rooms, treatment rooms, medical gas, or x-ray can be rejected if submitted as generic office TI with only building/electrical scope.",
        "Health-care licensing or state/local health review may control opening even after the building permit is final; verify this path before promising an opening date.",
        "Medical gas and x-ray shielding often need specialty documentation and third-party verification; missing it can block final inspection or equipment operation.",
    ])
    add_unique("common_mistakes", [
        "Forgetting exam-room sinks, fixture counts, backflow/indirect waste, or accessible restroom upgrades in the plumbing scope.",
        "Leaving medical gas, nitrous/oxygen, alarms, zone valves, and verifier paperwork out of the permit package.",
        "Using ordinary office HVAC assumptions instead of confirming ventilation, exhaust, pressure relationships, filtration, and infection-control needs for procedure/sterilization/lab rooms.",
        "Missing ADA path-of-travel documentation for reception, exam rooms, restrooms, route, parking/passenger loading, and check-in counters.",
    ])
    add_unique("inspections", [
        "Building rough/final — verify clinic layout, occupancy basis, egress, corridors, accessibility, fire-rated assemblies, and certificate-of-occupancy conditions.",
        "Plumbing rough/final — verify exam-room hand sinks, accessible restrooms, backflow protection, indirect waste, dental/medical equipment connections, and fixture counts.",
        "Mechanical balance / infection-control verification — confirm ventilation, exhaust, pressure relationships, filtration, and room-use assumptions where clinic functions require them.",
        "Medical gas pressure test / verifier final if oxygen, nitrous, vacuum, alarms, zone valves, or gas outlets are installed or modified.",
        "Fire alarm / sprinkler / life-safety final — verify notification appliance coverage, sprinkler head layout, emergency lighting, exit signs, and any oxygen/medical-gas hazard coordination.",
        "Radiology/x-ray shielding or state radiation registration verification if radiation-producing equipment is installed.",
    ])

    companions = ensure_list("companion_permits")
    existing_companions = {str(c.get("permit_type") if isinstance(c, dict) else c).strip().lower() for c in companions}
    for trig in result.get("hidden_triggers") or []:
        if not isinstance(trig, dict) or not str(trig.get("id", "")).startswith("medical_clinic_"):
            continue
        for permit in trig.get("companion_permits") or []:
            key = str(permit).strip().lower()
            if key and key not in existing_companions:
                companions.append({
                    "permit_type": permit,
                    "reason": trig.get("why_it_matters") or f"Medical clinic TI rulebook trigger {trig.get('id')}",
                    "certainty": "likely" if trig.get("severity") == "medium" else "almost_certain",
                })
                existing_companions.add(key)

    baseline = [
        ("Health-care licensing / local health review", "Clinic opening may require state or local health-care approval separate from the building permit; verify by clinic type."),
        ("Fire alarm / fire sprinkler permit if devices or heads change", "Clinic layouts commonly affect notification coverage, sprinkler spacing, emergency lighting, and egress."),
    ]
    for permit, reason in baseline:
        key = permit.lower()
        if key not in existing_companions:
            companions.append({"permit_type": permit, "reason": reason, "certainty": "likely"})
            existing_companions.add(key)

    return result


def enforce_ti_min_permits_floor(result: dict, job_type: str, city: str, state: str) -> dict:
    """A3: ensure office/retail TI required permits include core MEP families.

    Runs after model/scope assembly and before final render/cache. Restaurant,
    residential, and simple-trade scopes are intentionally untouched.
    """
    if not isinstance(result, dict):
        return result
    primary_scope = result.get("_primary_scope") or detect_primary_scope(job_type or "")
    result.setdefault("_primary_scope", primary_scope)
    if primary_scope not in ("commercial_office_ti", "commercial_retail_ti", "commercial_medical_clinic_ti"):
        return result
    permits = result.get("permits_required")
    if not isinstance(permits, list):
        permits = []
        result["permits_required"] = permits

    job = (job_type or "").lower()
    required = ["building", "mechanical", "electrical"]
    if primary_scope == "commercial_office_ti":
        required.append("plumbing")
    else:
        if any(t in job for t in ("restroom", "bathroom", "toilet", "lavatory", "sink", "kitchen", "plumbing")):
            required.append("plumbing")
        if primary_scope == "commercial_retail_ti" or any(t in job for t in ("sign", "signage", "storefront")):
            required.append("sign")
    if any(t in job for t in ("change of occupancy", "change of use", "sprinkler", "fire alarm", "relocate sprinkler", "sprinkler relocation", ">50% sprinkler", "more than 50% sprinkler")):
        required.append("fire")

    existing = {_permit_family(p) for p in permits if isinstance(p, dict)}
    verified_row = _get_verified_city_row(city, state)
    verified = bool(verified_row)
    added = []
    for family in required:
        if family in existing:
            continue
        permit_type, portal = _ahj_companion_permit_name(family, primary_scope, city, state, verified)
        notes = f"Added by A3 TI permit-floor guardrail: {family} review is a core companion permit family for {primary_scope.replace('_', ' ')} scope. Verify exact AHJ portal label before submittal."
        permits.append(_scope_permit(permit_type, portal, notes))
        existing.add(family)
        added.append(family)

    if added:
        result["_a3_min_permits"] = {"scope": primary_scope, "floor": 4, "added_families": added, "verified_city_row": verified}
        logic = result.get("permits_required_logic") if isinstance(result.get("permits_required_logic"), list) else []
        for p in permits[-len(added):]:
            logic.append({"permit_type": p.get("permit_type"), "included_because": p.get("notes"), "scope_trigger": "A3 commercial TI min_permits floor"})
        result["permits_required_logic"] = logic
        result["permit_verdict"] = "YES"
    return result


def _is_commercial_ti_scope(primary_scope: str, job_type: str) -> bool:
    if primary_scope in {"commercial_restaurant", "commercial_office_ti", "commercial_retail_ti", "commercial_medical_clinic_ti"}:
        return True
    if primary_scope != "commercial":
        return False
    job = (job_type or "").lower()
    return _scope_has_any(job, [
        "tenant improvement", " ti", "t.i.", "interior alteration", "interior remodel",
        "buildout", "build-out", "change of use", "change of occupancy", "occupancy change",
        "fit out", "fit-out",
    ])


def _commercial_ti_building_permit(primary_scope: str) -> dict:
    building_name = {
        "commercial_restaurant": "Building Permit — Tenant Improvement / Restaurant Interior Alteration",
        "commercial_office_ti": "Building Permit — Tenant Improvement / Office Interior Alteration",
        "commercial_retail_ti": "Building Permit — Tenant Improvement / Retail Interior Alteration",
        "commercial_medical_clinic_ti": "Building Permit — Tenant Improvement / Medical Clinic Interior Alteration",
        "commercial": "Building Permit — Commercial Interior Alteration / Change of Use",
    }.get(primary_scope, "Building Permit — Commercial Interior Alteration / Change of Use")
    return _scope_permit(
        building_name,
        "Commercial Building Permit - Tenant Improvement / Interior Alteration",
        "Primary commercial building/TI permit for occupancy, life-safety, accessibility, plan review, and interior alteration scope. Verify exact AHJ portal label before submittal.",
    )


def _commercial_ti_companion_permits(primary_scope: str, job_type: str) -> list[dict]:
    job = (job_type or "").lower()
    companions = [
        _scope_permit("Mechanical Permit — Commercial Tenant Improvement", "Mechanical Permit - Commercial Interior Alteration", "Commercial HVAC, ventilation, exhaust, diffuser/RTU, or air-balance scope commonly requires mechanical review."),
        _scope_permit("Electrical Permit — Commercial Tenant Improvement", "Electrical Permit - Commercial Interior Alteration", "Commercial lighting, panels, branch circuits, equipment, emergency lighting, and controls commonly require electrical review."),
    ]
    if primary_scope in {"commercial_restaurant", "commercial_office_ti", "commercial_medical_clinic_ti"} or _scope_has_any(job, ["sink", "restroom", "bathroom", "plumbing", "grease", "interceptor", "kitchen", "fixture"]):
        companions.append(_scope_permit("Plumbing Permit — Commercial Tenant Improvement", "Plumbing Permit - Commercial Interior Alteration", "Commercial fixtures, restrooms, sinks, grease/dental/medical equipment, or DWV changes commonly require plumbing review."))
    if primary_scope in {"commercial_restaurant", "commercial_medical_clinic_ti"} or _scope_has_any(job, ["fire alarm", "sprinkler", "hood", "suppression", "ansul", "fire suppression"]):
        companions.append(_scope_permit("Fire Alarm / Fire Sprinkler Permit — Commercial Tenant Improvement", "Fire Alarm / Fire Sprinkler Permit - Commercial", "Commercial TI frequently affects fire alarm, sprinkler, hood suppression, egress, emergency lighting, or life-safety review."))
    if primary_scope == "commercial_retail_ti" or _scope_has_any(job, ["sign", "signage", "storefront"]):
        companions.append(_scope_permit("Sign Permit — Commercial Storefront / Wall Sign", "Sign Permit - Commercial", "Retail/storefront changes commonly require separate sign review if signage is included."))
    return companions


def _commercial_ti_required_permit_set(primary_scope: str, job_type: str) -> tuple[list[dict], list[dict]]:
    permits = [_commercial_ti_building_permit(primary_scope)]
    logic = [{
        "permit_type": permits[0]["permit_type"],
        "included_because": "Commercial TI/change-of-use scopes must lead with a building/interior-alteration permit; trade permits are companion permits unless the job is only a trade changeout.",
        "scope_trigger": f"detected primary scope {primary_scope}",
    }]
    for permit in _commercial_ti_companion_permits(primary_scope, job_type):
        permits.append(permit)
        logic.append({
            "permit_type": permit.get("permit_type"),
            "included_because": permit.get("notes"),
            "scope_trigger": f"commercial TI {_permit_family(permit)} companion family",
        })
    return permits, logic


def _commercial_ti_secondary_companions(primary_scope: str) -> list[dict]:
    companions: list[dict] = []
    if primary_scope == "commercial_restaurant":
        companions.extend([
            {"permit_type": "Health Department / Food Establishment Review", "reason": "Restaurant buildouts commonly require health review before opening.", "certainty": "almost_certain"},
            {"permit_type": "Grease Interceptor / FOG Approval", "reason": "Commercial kitchen plumbing often requires grease interceptor/FOG approval.", "certainty": "likely"},
        ])
    elif primary_scope == "commercial_medical_clinic_ti":
        companions.extend([
            {"permit_type": "Health-care licensing / local health review", "reason": "Clinic opening may require licensing or health-care approval separate from building permit final.", "certainty": "likely"},
            {"permit_type": "Medical gas / x-ray specialty review if included", "reason": "Medical gas, nitrous/oxygen/vacuum, or radiology equipment can require specialty documentation and verification.", "certainty": "conditional"},
        ])
    elif primary_scope == "commercial_office_ti":
        companions.append({"permit_type": "Low-voltage / data cabling permit if cabling, access control, AV, or security systems are installed", "reason": "Office TIs commonly include telecom/data/access-control work that may be permitted separately or inspected by electrical/fire reviewers.", "certainty": "likely"})
    companions.append({"permit_type": "Accessibility / ADA path-of-travel verification", "reason": "Commercial tenant improvements commonly trigger accessible route, restroom, door/hardware, counter, parking, or 20% disproportionality review.", "certainty": "likely"})
    return companions


def _is_residential_or_trade_only_primary(permit: dict) -> bool:
    if not isinstance(permit, dict):
        return True
    text = " ".join(str(permit.get(k, "")) for k in ("permit_type", "portal_selection", "notes", "description")).lower()
    if "residential" in text:
        return True
    if any(bad in text for bad in ("hvac changeout", "hvac system replacement", "changeout / replacement")):
        return True
    return _permit_family(permit) != "building"


def enforce_commercial_primary_permit_guardrail(result: dict, job_type: str, city: str, state: str) -> dict:
    """Ensure commercial TI scopes never ship a residential/trade primary card.

    The model/narrative can understand commercial TI while stale structured JSON still
    puts residential HVAC first. This reconciles the structured card against the
    detected scope and repairs to a commercial building/TI primary, while marking the
    result needs_review so a contractor sees verification caution instead of fake certainty.
    """
    if not isinstance(result, dict):
        return result
    primary_scope = result.get("_primary_scope") or detect_primary_scope(job_type or "")
    result["_primary_scope"] = primary_scope
    if not _is_commercial_ti_scope(primary_scope, job_type or ""):
        return result

    permits = result.get("permits_required")
    if not isinstance(permits, list):
        permits = []
    permits = [p for p in permits if isinstance(p, dict)]
    primary = permits[0] if permits else {}
    repaired = _is_residential_or_trade_only_primary(primary)

    building_primary = _commercial_ti_building_permit(primary_scope)
    ordered: list[dict] = []
    if repaired:
        ordered.append(building_primary)
    else:
        # Keep a valid AHJ-specific building/TI primary, but normalize any accidental residential wording.
        primary_text = " ".join(str(primary.get(k, "")) for k in ("permit_type", "portal_selection", "notes")).lower()
        if "residential" in primary_text:
            primary = building_primary
            repaired = True
        ordered.append(primary)

    existing_families = {_permit_family(ordered[0])}
    for p in _commercial_ti_companion_permits(primary_scope, job_type):
        fam = _permit_family(p)
        if fam not in existing_families:
            ordered.append(p)
            existing_families.add(fam)
    for p in permits[1:]:
        text = " ".join(str(p.get(k, "")) for k in ("permit_type", "portal_selection", "notes", "description")).lower()
        if "residential" in text or "hvac changeout" in text or "hvac system replacement" in text:
            continue
        fam = _permit_family(p)
        if fam and fam not in existing_families:
            ordered.append(p)
            existing_families.add(fam)

    already_marked = isinstance(result.get("_commercial_primary_permit_guardrail"), dict)
    result["permits_required"] = ordered
    result["permit_verdict"] = "YES"
    result["needs_review"] = bool(result.get("needs_review")) or repaired
    logic = result.get("permits_required_logic") if isinstance(result.get("permits_required_logic"), list) else []
    if not already_marked:
        logic.append({
            "permit_type": ordered[0].get("permit_type"),
            "included_because": "Commercial TI/change-of-use scopes must lead with a commercial building/interior alteration permit; residential/trade-only primary cards are blocked.",
            "scope_trigger": "Batch 1 commercial primary-permit guardrail",
        })
    result["permits_required_logic"] = logic
    result["_commercial_primary_permit_guardrail"] = {
        "scope": primary_scope,
        "repaired": repaired,
        "city": city,
        "state": state,
    }
    if repaired and not already_marked:
        result["confidence"] = downgrade_confidence(str(result.get("confidence") or "medium").lower(), 1)
        reason = (result.get("confidence_reason") or "").strip()
        append = "Structured primary permit was reconciled to commercial TI; verify exact AHJ portal label before quoting or starting work."
        result["confidence_reason"] = f"{reason} {append}".strip()
    return result


def apply_state_expert_pack(result: dict, city: str, state: str, job_type: str) -> dict:
    """Append deterministic state expert notes (currently California)."""
    if not isinstance(result, dict):
        return result
    notes = get_state_expert_notes(state, city, job_type)
    if not notes:
        if "expert_notes" not in result:
            result["expert_notes"] = []
        return result
    existing = result.get("expert_notes") if isinstance(result.get("expert_notes"), list) else []
    seen = {str(n.get("title") if isinstance(n, dict) else n).lower() for n in existing}
    for note in notes:
        title = str(note.get("title") if isinstance(note, dict) else note).lower()
        if title not in seen:
            existing.append(note)
            seen.add(title)
    result["expert_notes"] = existing
    return result


def apply_fee_verify_caveat(result: dict) -> dict:
    """Preserve fee numbers and add verify language + source URL when available."""
    if not isinstance(result, dict):
        return result
    fee = result.get("fee_range")
    if isinstance(fee, list):
        fee_text = ", ".join(str(x) for x in fee if x)
    else:
        fee_text = str(fee or "").strip()
    if not fee_text:
        return result
    fee_source = result.get("fee_source") if isinstance(result.get("fee_source"), dict) else {}
    source_url = fee_source.get("url") or ""
    if "verify in" in fee_text.lower() or "verify at" in fee_text.lower():
        # Cached results may have an older verify URL. Keep the fee number but
        # align inline caveat with the current fee_source when available.
        if source_url and source_url not in fee_text:
            result["fee_range"] = re.sub(
                r"\s+—\s+verify\s+(?:in|at)\s+.+?(?:\s+before quoting)?$",
                f" — verify in {source_url} before quoting",
                fee_text,
                flags=re.I,
            )
        return result
    if fee_text.lower().startswith("fee estimate:"):
        base = fee_text
    else:
        base = f"Fee Estimate: {fee_text}"
    if source_url:
        result["fee_range"] = f"{base} — verify in {source_url} before quoting"
    else:
        result["fee_range"] = f"{base} — verify in city portal before quoting"
    return result


def brave_search(query: str, num: int = 5, max_results: int | None = None, city: str = "", state: str = "") -> list[dict]:
    limit = max_results or num
    if not BRAVE_SEARCH_API_KEY:
        return []
    try:
        resp = _http_get_with_backoff(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": limit, "text_decorations": False, "search_lang": "en"},
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
            },
            timeout=15,
        )
        if not resp or resp.status_code >= 400:
            return []
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


def _search_with_fallback(query: str, num: int, city: str, state: str) -> list[dict]:
    """Explicit fallback chain: Serper first if available, else Brave.

    Replaces the implicit `serper_search(...) or brave_search(...)` pattern,
    which would (a) skip Brave even when Serper returned an empty list for a
    valid reason, and (b) hide which provider answered. This version logs the
    decision and lets us add per-provider metadata to results in future.
    """
    if SERPER_API_KEY:
        primary = serper_search(query, num=num, city=city, state=state)
        if primary:
            return primary
        # Serper available but empty → try Brave as second opinion
        if BRAVE_SEARCH_API_KEY:
            print("[search] Serper returned 0 results — falling back to Brave")
            return brave_search(query, num=num, city=city, state=state)
        return []
    if BRAVE_SEARCH_API_KEY:
        return brave_search(query, num=num, city=city, state=state)
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
            model="gpt-5.4-mini",
            messages=[{
                "role": "user",
                "content": f"List 3 alternative official permit names for '{job_type}' used by US building departments. Return only a JSON array of strings, no explanation. Example: [\"mechanical permit\", \"HVAC permit\", \"cooling system permit\"]"
            }],
            max_completion_tokens=80,
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
            else:
                # 2026-04-26: Visibility on Accela silent failures.
                # Before, this branch was empty — meaning we couldn't tell
                # "city not in Accela's coverage" from "Accela failed for a
                # city it should cover." Now:
                #   (a) always emit a structured warning + flag downstream,
                #   (b) if the state has heavy Accela coverage, emit a
                #       louder per-state WARN so ops can spot data-coverage
                #       regressions (e.g. CA suddenly stops matching).
                print(f"[search][WARN] accela_miss city={city!r} state={state!r} job_type={job_type!r} — fell through to web search; quality may be lower")
                _ACCELA_LIKELY_COVERED_STATES = {
                    "CA", "TX", "FL", "NY", "PA", "IL", "OH", "GA", "NC", "MI",
                    "NJ", "VA", "WA", "AZ", "MA", "TN", "IN", "MO", "MD", "WI",
                    "CO", "MN", "SC", "AL", "LA", "KY", "OR", "OK",
                }
                if (state or "").upper() in _ACCELA_LIKELY_COVERED_STATES:
                    print(f"[accela][WARN] {city}, {state} miss in heavy-coverage state — data gap or normalization miss")
                if "accela_miss" not in [c.get("source") for c in structured_candidates if isinstance(c, dict)]:
                    structured_candidates.append({
                        "fees": [], "portal_url": "", "raw_text": "",
                        "source": "accela_miss",
                        "field_sources": {}, "field_confidence": {},
                        "freshness": "accela_miss",
                        "_warn": f"Accela returned no agency match for {city}, {state}",
                    })

        if total_chars < 200:
            alt_queries = expand_permit_query(job_type, search_city, search_state)
            primary_query = f'"{search_city}" "{search_state}" {job_type} permit requirements fee site:.gov'
            relaxed_query = f'{search_city} {search_state} {job_type} permit requirements fees building department'
            merged_results = []
            # Use explicit Serper→Brave fallback (was implicit `or` chain that
            # could skip Brave even when Serper had a transient empty result).
            merged_results.extend(_search_with_fallback(primary_query, num=5, city=search_city, state=search_state))
            if alt_queries:
                alt_query = f'{search_city} {search_state} {alt_queries[0]} permit building department site:.gov'
                merged_results.extend(_search_with_fallback(alt_query, num=4, city=search_city, state=search_state))
            if not merged_results:
                merged_results.extend(_search_with_fallback(relaxed_query, num=5, city=search_city, state=search_state))
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

# 2026-04-28: state contractor-license / regulator domains. These are NOT
# building-permit portals — they're licensing authorities for verifying
# trade credentials. Opus 4.7 grading caught the LLM emitting these as
# `apply_url` for Pasadena solar (cslb.ca.gov, CA contractor license board)
# and Houston retail TI (tdlr.texas.gov, TX licensing). A contractor lands
# on the wrong site, looks lost, bounces. Block these from apply_url.
STATE_LICENSE_DOMAINS = frozenset({
    "cslb.ca.gov",                   # CA Contractors State License Board
    "dca.ca.gov",                    # CA Department of Consumer Affairs
    "tdlr.texas.gov",                # TX Department of Licensing & Regulation
    "dbpr.myfloridalicense.com",     # FL Dept of Business and Professional Regulation
    "myfloridalicense.com",          # FL DBPR alias
    "roc.az.gov",                    # AZ Registrar of Contractors (verify-only)
    "azroc.gov",                     # AZ ROC alias
    "lni.wa.gov",                    # WA L&I (licensing portal)
    "ccb.oregon.gov",                # OR Construction Contractors Board
    "dpor.virginia.gov",             # VA Dept of Professional/Occupational Regulation
    "llr.sc.gov",                    # SC Labor Licensing Regulation
    "ncbceblc.org",                  # NC Building Code Council
    "tn.gov/commerce",               # TN Dept of Commerce & Insurance (licensing)
    "in.gov/pla",                    # IN Professional Licensing Agency
    "michigan.gov/lara",             # MI LARA (licensing)
    "dpr.delaware.gov",              # DE Division of Professional Regulation
    "dol.colorado.gov",              # CO Dept of Labor (licensing)
    "dpor.virginia.gov",             # VA DPOR
    "dca.ny.gov",                    # NY Dept of Consumer Affairs
    "doppl.indiana.gov",             # IN PLA alias
    "dol.maryland.gov",              # MD licensing
})


def is_state_license_url(url: str) -> bool:
    """True if url points at a state contractor-license / regulator domain.

    These belong in `license_required` context, NEVER in `apply_url` — a
    contractor clicking "apply for permit" should land on a building-dept
    portal, not a license-verification page.
    """
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
        # Strip explicit "www." prefix (lstrip("www.") is a CHARACTER class
        # strip — it eats any leading w/. chars, breaking www2.dbpr.* etc).
        if host.startswith("www."):
            host = host[4:]
        if not host:
            return False
        path = (urlparse(url).path or "")
        for pat in STATE_LICENSE_DOMAINS:
            if "/" in pat:
                pat_host, pat_path = pat.split("/", 1)
                # Match host (exact or subdomain suffix) + path prefix
                if (host == pat_host or host.endswith("." + pat_host)) and \
                   path.lstrip("/").lower().startswith(pat_path.lower()):
                    return True
            else:
                # Exact OR subdomain suffix match (catches www2.dbpr.* etc)
                if host == pat or host.endswith("." + pat):
                    return True
        return False
    except Exception:
        return False


def strip_pdf_from_result(result: dict) -> dict:
    """
    Move PDF URLs from apply_url to apply_pdf, and strip state-license
    URLs entirely (they're never the right apply_url target).

    Apply_url should only ever be a real building-department permit portal
    or PDF application, never a PDF and never a state license verification
    page.
    """
    apply_url = result.get("apply_url", "")
    # 2026-04-28: state license domains never belong in apply_url. The LLM
    # occasionally emits cslb.ca.gov / tdlr.texas.gov as apply_url because
    # they're authoritative-looking .gov links it found while researching
    # contractor licensing — but the contractor following that URL lands
    # on a license lookup page, not a permit portal.
    if apply_url and is_state_license_url(apply_url):
        print(f"[apply_url_strip] Removed state-license URL from apply_url: {apply_url}")
        result["apply_url"] = None
        # Surface the license URL elsewhere so it's not lost — append to
        # license_required text if not already there.
        existing_license = result.get("license_required", "") or ""
        if apply_url not in existing_license:
            result["license_required"] = (existing_license + (" " if existing_license else "") + f"(license verification: {apply_url})").strip()
        apply_url = ""

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

13. JOB-SCOPE-AWARE PERMIT SELECTION — CRITICAL: Use the job_description + trade scope. Output ONLY the permits actually triggered by the stated scope, not a fixed companion list by trade.
   - HVAC like-for-like/condenser changeout/water heater swap/mini-split with no panel/gas-line/new-fixture scope → ONE primary trade permit only.
   - HVAC replacement WITH panel/service upgrade → Mechanical + Electrical panel/service permit.
   - ADU/garage conversion → Building master + Electrical + Plumbing + Mechanical; Solar only if new detached ADU or solar mandate is clearly triggered.
   - Roof tear-off/re-roof → Roofing permit only unless skylights, solar, or structural work is stated.
   - Solar PV → Building/Structural Solar permit + Electrical Solar permit; battery is included in the electrical/ESS permit. Utility interconnection/PTO is separate coordination, not a third city permit unless the AHJ treats it as one.
   For borderline items, use companion_permits with "May be required if: [specific trigger]" rather than listing them as required.

14. COMMERCIAL vs RESIDENTIAL PERMIT NAMING — CRITICAL FOR COMMERCIAL ACCURACY:
   If the job_description contains commercial markers (restaurant, tenant improvement / TI, change of occupancy, A-2 / B / M / I / F occupancy, commercial kitchen, food service, multifamily, 5-over-1, mixed-use, warehouse, industrial, office TI, retail TI, ≥3-story, occupancy classification change, public assembly, hotel, school, hospital), permit names MUST NOT contain residential framing.
   - DO NOT output "Residential Re-Roof" / "Residential Roofing" / "Residential Deck" / "Residential Furnace" / "Residential System" / "(Residential)" suffix on a commercial scope.
   - For roofing on a commercial building: use "Roofing Permit — Commercial Re-Roof" or "Roofing Permit — Built-Up / Modified-Bitumen / TPO" (match the system).
   - For HVAC on a commercial building: use "Mechanical Permit — Commercial RTU / VAV / Make-Up Air Unit" (match the equipment).
   - For B → A-2 / B → M / B → I / use-change scopes: lead permit_name with "Building Permit — Interior Alteration / Change of Use (B → A-2)" or the specific occupancy pair, NOT a trade-specific residential template.
   - For multifamily / 5-over-1 / podium / mixed-use: use "Building Permit — Multifamily Construction" or "Building Permit — Multifamily Alteration".
   The few-shot examples in CRITICAL RULE 2 above are RESIDENTIAL EXAMPLES. They illustrate the format, not the framing. When the job is commercial, replace "Residential" with the appropriate commercial qualifier or drop the residential qualifier entirely.

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
  "permits_required_logic": [
    {
      "permit_type": "Mechanical Permit — HVAC Replacement (Residential)",
      "included_because": "Mechanical permit required because the stated scope is a like-for-like HVAC equipment changeout; no panel, gas-line, or fixture work was described.",
      "scope_trigger": "HVAC changeout in job description"
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
      "reason": "May be required if: specific scope trigger for this trade/job (new wiring, gas piping modification, fixture relocation, structural change, utility interconnection, etc.)",
      "required_if": "specific trigger conditions; do NOT mark companion permits as unconditionally Required",
      "certainty": "likely | possible | conditional"
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
✓ permits_required_logic explains WHY each required permit was included and names the scope trigger.
✓ companion_permits is MANDATORY as a field, but [] is correct for isolated single-trade scopes (HVAC condenser swap, water heater swap, simple re-roof, etc.). Do NOT populate a fixed companion list just because of the trade.
✓ Companion permit wording rule: NEVER say "Companion Permit Required" or imply unconditional requirement for secondary permits. Use "May be required if: [specific trigger]". Keep the primary permit in permits_required as required when applicable; do not downgrade the primary permit.

COMPANION PERMIT TRADE MATRIX (use ONLY when the stated scope includes or plausibly borders these triggers):
- HVAC/AC/furnace/heat pump replacement → [Electrical Permit — May be required if: new wiring, disconnect replacement, breaker/panel modification, or new circuit; Gas Permit — May be required if: gas piping modification, gas appliance connection, or new gas line]
- Electrical panel upgrade/service change → [Utility Coordination — May be required if: service disconnect/reconnect, meter pull, or utility-side service change]
- Bathroom remodel → [Plumbing Permit — May be required if: pipe relocation, fixture move, or DWV modification; Electrical Permit — May be required if: new circuits, GFCI/outlet relocation, fan/light wiring, or panel work; Building Permit — May be required if: walls moved or structural/framing changes]
- Kitchen remodel → [Plumbing Permit — May be required if: sink/dishwasher/ice-maker line relocation or gas line work; Electrical Permit — May be required if: new dedicated circuits, outlet relocation, appliance circuits, or panel work; Mechanical Permit — May be required if: new/rerouted range hood exhaust]
- Roof replacement → [Electrical Permit — May be required if: solar panels/electrical equipment must be removed/reinstalled; Structural Permit — May be required if: decking, rafters, trusses, or load path changes]
- Water heater replacement → [Gas Permit — May be required if: gas piping, venting, combustion air, or gas appliance connection changes; Electrical Permit — May be required if: converting to electric or adding a circuit/disconnect]
- Deck/patio addition → [Electrical Permit — May be required if: outlets, lighting, fans, heaters, or exterior circuits are added]
- Garage conversion/ADU → [Electrical Permit — May be required if: new circuits, subpanel, service load changes, or rewiring; Plumbing Permit — May be required if: bathroom/kitchen/laundry fixtures or drains are added; Mechanical Permit — May be required if: new HVAC, ducts, exhaust, or ventilation]
- Solar panel installation → [Electrical Permit — May be required if: inverter, disconnect, panel tie-in, battery, or service equipment is installed/modified; Building/Structural Permit — May be required if: roof racking, attachments, penetrations, or structural review are required; Utility Interconnection — May be required if: grid-tied PV export, net metering, or battery backup interconnection is included]
- EV charger installation → [Electrical Permit — May be required if: new 240V circuit, breaker, load calculation, panel work, or hardwired EVSE]
- Generator installation → [Electrical Permit — May be required if: transfer switch/interlock, feeder, or panel connection is installed; Gas/Mechanical Permit — May be required if: gas piping, regulator, venting, or fuel connection changes]
- Basement finish → [Electrical Permit — May be required if: new outlets, lighting, smoke/CO alarms, or subpanel work; Plumbing Permit — May be required if: bathroom, wet bar, laundry, or floor drain added; Mechanical Permit — May be required if: ducts, returns, bathroom exhaust, or heating/cooling zones are modified]
- Window/door replacement → [Building Permit — May be required if: structural opening/header changes, egress changes, or exterior envelope alterations]
- Plumbing repiping → [Building Permit — May be required if: structural framing, fire-rated assemblies, or major access openings are altered]
✓ apply_url: ALWAYS provide the direct online permit portal URL if one exists (e.g. "https://abc.austintexas.gov"). Do not leave null if you found a portal in your research.
✓ total_cost_estimate: Provide a realistic total project cost range for this job in this city (including labor, materials, and permit fees). Example: "$2,500 - $4,500". NEVER leave this field null — use your training knowledge to provide a best-estimate range for the contractor.
✓ approval_timeline: Always provide a 'simple' (over-the-counter) and 'complex' (plan review) estimate.
✓ code_citation: ALWAYS include the specific code section (IRC/IPC/NEC/state code) that applies. Format: {"section": "IRC R105.2.2", "text": "first 120 chars of the relevant rule or exemption text"}. For NO verdicts: cite the exemption clause. For YES/MAYBE verdicts: cite the primary code section that REQUIRES the permit (e.g. "IRC R105.1", "NEC 210.12", "IPC 106.1"). Never set code_citation to null — always provide a relevant code reference."""

# ─── JSON repair helpers (used after a JSONDecodeError on AI output) ──────────

def _try_repair_truncated_json(text: str):
    """Best-effort recovery of structurally broken JSON from LLM output.

    Production hits this when gpt-5.4-mini occasionally produces a JSON body
    with a missing comma or unterminated string mid-document on long prompts
    (ADU conversions, multi-permit scopes). Returns parsed dict on success,
    None if no repair strategy worked. Never raises.
    """
    if not text:
        return None
    text = text.strip()

    # Strategy 1: json5 / demjson3 (lenient parsers, optional deps).
    for parser_name in ("json5", "demjson3"):
        try:
            mod = __import__(parser_name)
            return mod.loads(text)
        except ImportError:
            continue
        except Exception:
            continue

    # Strategy 2: progressively trim from the right until the prefix parses.
    # Handles "valid JSON cut off mid-field" by recovering everything before
    # the broken element. Bound the search to avoid O(n^2) on huge bodies.
    last_brace = text.rfind("}")
    attempts = 0
    while last_brace > 0 and attempts < 50:
        candidate = text[: last_brace + 1]
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            last_brace = text.rfind("}", 0, last_brace)
            attempts += 1

    # Strategy 3: naive bracket-balance repair. Cut off after the last comma
    # to drop the partial element, then append matching closers.
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")
    if open_braces > 0 or open_brackets > 0:
        last_comma = text.rfind(",")
        if last_comma > 0:
            candidate = text[:last_comma] + ("]" * max(0, open_brackets)) + ("}" * max(0, open_braces))
            try:
                return json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                pass

    return None


def _retry_with_minimal_prompt(user_prompt: str, _openai_call_fn=None):
    """Last-resort retry with a stripped-down system prompt asking for a
    minimal JSON shape. Used when the full prompt repeatedly produces
    malformed JSON. The `_openai_call_fn` arg is a closure passed for
    signature compatibility with the call site; we re-issue the request
    against the fallback model directly so we control the system prompt.
    """
    minimal_system = (
        "You are a permit research assistant. Return ONLY a valid, parseable "
        "JSON object — nothing else, no markdown, no commentary. Required fields: "
        "applying_office (string), apply_phone (string), apply_url (string), "
        "apply_address (string), permit_verdict (one of: YES, NO, MAYBE), "
        "permits_required (array of objects with type, required:bool, fee_estimate:string). "
        "Optional fields: fee_range (string), approval_timeline (object with simple, complex), "
        "code_citation (object with section, text), confidence (low/medium/high). "
        "Keep the response under 3000 tokens. Be concise and accurate."
    )
    openai_client = _get_openai_client()
    response = openai_client.with_options(timeout=_OPENAI_REQUEST_TIMEOUT_S).chat.completions.create(
        model=_openai_fallback_model,
        messages=[
            {"role": "system", "content": minimal_system},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        max_completion_tokens=4000,
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content
    return json.loads(raw)


# ─── Output schema validation gate ───────────────────────────────────────────
# 2026-04-28: Catches semantic defects that pass structural normalization.
# Real production cases that triggered this:
#   • Phoenix restaurant TI returned "the required permit" as the permit
#     name (LLM placeholder when it couldn't determine the real name).
#   • Same Phoenix TI returned residential deck-building checklist items
#     (joist hangers, ledger flashing) for a commercial restaurant scope.
#   • LA Hillside ADU showed HVAC-only "Don't fail this inspection" items
#     (drain pan, AHRI cert, duct mastic) — wrong scope for an ADU.
# When the gate flags issues it redacts placeholder fields, downgrades
# confidence, and sets needs_review=True so the UI surfaces the warning.

_PLACEHOLDER_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r'\bthe\s+required\s+permit\b',
        r'^\s*the\s+permit\s*$',
        r'^\s*permit\s*$',
        r'^\s*the\s+building\s+permit\s*$',
        r'\[\s*trade\s*\]\s*permit',
        r'\[\s*permit[_\s]*name\s*\]',
        r'<\s*permit[_\s]*name\s*>',
        r'\{\s*permit[_\s]*name\s*\}',
        r'^\s*tbd\s*$',
        r'^\s*see\s+notes\s*$',
        r'\bINSERT_\w+',
        r'^\s*N/?A\s*$',
    ]
]

# Tokens that signal residential single-trade work; if these are the ONLY
# scope-relevant tokens in the checklist for a commercial query, that's a
# scope/checklist mismatch.
_RESIDENTIAL_DECK_PATTERNS = re.compile(
    r'(joist\s+hanger|ledger\s+flash|guardrail\s+balust|frost\s+line|deck\s+footing|stair\s+stringer)',
    re.IGNORECASE,
)
_RESIDENTIAL_HVAC_RES_PATTERNS = re.compile(
    r'(condensate\s+drain\s+pan|AHRI\s+cert|R-?410|duct\s+mastic|return-air\s+plenum|residential\s+furnace)',
    re.IGNORECASE,
)
_COMMERCIAL_TOKENS = re.compile(
    r'(restaurant|tenant\s+improvement|\bTI\b|\bhood\b|grease\s+interceptor|change\s+of\s+occupancy|'
    r'commercial\s+kitchen|office\s+TI|retail\s+TI|multifamily|5-?over-?1|mixed-?use|'
    r'food\s+service|commercial\s+building|warehouse|\bindustrial\b|\bAnsul\b|Type\s+I\s+hood|'
    r'\b[AB]-?\d\b\s*occupancy)',
    re.IGNORECASE,
)

# 2026-04-28: residential-marker patterns. Opus 4.7 commercial review caught
# the Milwaukee 411 E Wisconsin Ave 85-seat restaurant TI (B → A-2) returning
# "Roofing – Residential Re-Roof" as exact_permit_name. Cause: the prompt
# few-shot examples (lines ~5208-5218) are residential-biased — when any
# trade keyword fires on a commercial scope (rooftop RTU, exterior wall
# penetration, deck demo, mechanical work) the LLM grabs the residential
# few-shot pattern. The validator below is the safety net on top of the
# prompt rule we added on the same day. Strip + flag + downgrade confidence.
_RESIDENTIAL_MARKER_PATTERNS = [
    re.compile(r'\s*\(\s*Residential\s*\)', re.IGNORECASE),
    re.compile(r'\s*[—–-]\s*Residential\s+Re-?Roof\b', re.IGNORECASE),
    re.compile(r'\s*[—–-]\s*Residential\s+Roofing\b', re.IGNORECASE),
    re.compile(r'\s*[—–-]\s*Residential\s+Deck\b', re.IGNORECASE),
    re.compile(r'\s*[—–-]\s*Residential\s+(System|Unit|Furnace|Service)\b', re.IGNORECASE),
    re.compile(r'\s*[—–-]\s*Residential\b(?!\s+\w)', re.IGNORECASE),
    re.compile(r'\bResidential\s+Re-?Roof\b', re.IGNORECASE),
    re.compile(r'\bResidential\s+Roofing\b', re.IGNORECASE),
    re.compile(r'\bResidential\s+Deck\b', re.IGNORECASE),
    re.compile(r'\bResidential\s+(System|Unit|Furnace|Service)\b', re.IGNORECASE),
    # Trailing "Residential" / "Residential System" at end of string —
    # "Mechanical - HVAC Replacement Residential" → "Mechanical - HVAC Replacement"
    re.compile(r'\s+Residential\s*$', re.IGNORECASE),
    re.compile(r'\s+Residential\s+(System|Unit)\s*$', re.IGNORECASE),
    # Single-family detached / SFR markers
    re.compile(r'\bsingle-?family\s+detached\b', re.IGNORECASE),
    re.compile(r'\bSFR\b'),
]

# Primary-scope → fallback permit name when the residential-marker strip
# leaves too short a stub to ship.
_COMMERCIAL_FALLBACK_PERMIT_NAMES = {
    'commercial_restaurant': 'Building Permit — Tenant Improvement (Commercial Restaurant)',
    'commercial_office_ti':  'Building Permit — Tenant Improvement (Commercial Office)',
    'commercial_retail_ti':  'Building Permit — Tenant Improvement (Commercial Retail)',
    'multifamily':           'Building Permit — Multifamily Tenant Improvement',
    'commercial':            'Building Permit — Commercial Alteration',
}


def _strip_residential_markers(name: str) -> tuple:
    """Strip residential markers from a permit name. Returns (cleaned, changed)."""
    if not isinstance(name, str) or not name:
        return name, False
    cleaned = name
    for pat in _RESIDENTIAL_MARKER_PATTERNS:
        cleaned = pat.sub('', cleaned)
    cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip(' -—–')
    return cleaned, cleaned != name


def _has_placeholder(value) -> bool:
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s:
        return False
    return any(p.search(s) for p in _PLACEHOLDER_PATTERNS)


# 2026-04-28: free-text URL sanitizer. The earlier source-grounding fix in
# normalize_sources() filtered the structured `result["sources"]` list, but
# LLM-emitted free text (fee_range, confidence_reason, notes) still leaks
# fabricated plausible-sounding archival URLs that don't survive domain
# classification. Real triangulated leaks across 4 cities of Opus 4.7 review:
#   - Phoenix:  ojp.gov/pdffiles1/Digitization/10429NCJRS.pdf  (DOJ archive)
#   - Vegas:    archive.org/details/dailycolonist1978          (1978 Victoria BC newspaper)
#   - Seattle:  kauffman.org/wp-content/.../NETS_US_PublicFirms2013.xlsx  (academic firms data)
#   - LA:       pw.lacounty.gov/...                            (wrong jurisdiction for City of LA)
# All four shipped to contractors via fee_range "verify in <URL>" text.
# This sanitizer runs after the engine builds free-text fields and strips any
# URL whose host classifies as EXCLUDED, replacing with the AHJ name.
_URL_REGEX = re.compile(r'https?://[^\s)\]\}>"\'`]+', re.IGNORECASE)

# Exact domains/host patterns observed as hallucinated fee-source leaks in the
# 2026-04-28 four-city review. Some (notably pw.lacounty.gov and ojp.gov) can
# classify as OFFICIAL by broad .gov rules, but they are still wrong for
# contractor-facing permit-fee prose in these scenarios.
_FREE_TEXT_FEE_URL_DENYLIST = (
    "pw.lacounty.gov",
    "ojp.gov",
    "archive.org",
    "kauffman.org",
)


def _is_denied_free_text_fee_url(url: str) -> bool:
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return True
    if host.startswith("www."):
        host = host[4:]
    return any(host == denied or host.endswith(f".{denied}") for denied in _FREE_TEXT_FEE_URL_DENYLIST)


def _strip_excluded_urls_from_text(text: str, ahj_name: str = "the building department") -> str:
    """Strip URLs that don't belong in contractor-facing free text.

    Stricter than `normalize_sources` because free-text fields are read by
    the contractor as authoritative ("verify in URL"). For free text we only
    allow URLs that classify_source_url() rates as OFFICIAL — that means
    .gov / .us / .mil / municipal / explicit-AHJ-allowlist domains. Anything
    that's SUPPLEMENTARY (.org bucket including kauffman.org, archive.org,
    research foundations, academic) or EXCLUDED gets replaced with
    `[verify with {ahj_name}]`.

    Real triangulated leaks this catches:
      - kauffman.org public-firms research dataset (Seattle review)
      - archive.org/details/dailycolonist1978 1978 newspaper (Vegas review)
      - ojp.gov DOJ digitization archive (Phoenix review)
      - any .org/.com domain the LLM hallucinates as a fee-source URL
    """
    if not text or not isinstance(text, str):
        return text
    def _replace(match):
        url = match.group(0).rstrip('.,;:!?')
        try:
            cls = classify_source_url(url)
            # Free-text URLs must be OFFICIAL (.gov / .us / .mil / municipal /
            # vetted AHJ allowlist). SUPPLEMENTARY and EXCLUDED both get
            # stripped — supplementary research sources don't belong in
            # "verify in <URL>" contractor-facing text. The explicit denylist
            # catches official-looking but wrong-jurisdiction/archive hosts from
            # the four-city review (e.g., pw.lacounty.gov for City of LA fees).
            if cls != SOURCE_CLASS_OFFICIAL or _is_denied_free_text_fee_url(url):
                return f"[verify with {ahj_name}]"
        except Exception:
            return f"[verify with {ahj_name}]"
        return url
    return _URL_REGEX.sub(_replace, text)


def sanitize_free_text_urls(result: dict, city: str, state: str) -> dict:
    """Apply _strip_excluded_urls_from_text to known free-text fields.

    Mutates result in place. Reports any strips on result['_url_strips'] for
    telemetry. Runs after the engine builds the response, before the
    semantic validation gate.
    """
    ahj = result.get("applying_office") or f"the {city} building department"
    fields_to_clean = [
        "fee_range", "confidence_reason", "permit_summary",
        "permit_type", "permit_name", "disclaimer",
    ]
    strips: list[dict] = []
    for fld in fields_to_clean:
        original = result.get(fld)
        if not isinstance(original, str) or not original:
            continue
        # Quick check: any URLs at all?
        urls = _URL_REGEX.findall(original)
        if not urls:
            continue
        # Any non-OFFICIAL URLs? Free-text fields require OFFICIAL only —
        # SUPPLEMENTARY (.org research / archive sources) and EXCLUDED both
        # get stripped because both lead to "wait, why am I verifying my
        # Phoenix permit fee at kauffman.org?" credibility hits.
        bad = [
            u.rstrip('.,;:!?')
            for u in urls
            if classify_source_url(u.rstrip('.,;:!?')) != SOURCE_CLASS_OFFICIAL
            or _is_denied_free_text_fee_url(u.rstrip('.,;:!?'))
            or not is_url_allowed_for_locality(u.rstrip('.,;:!?'), city, state, result=result)
        ]
        if not bad:
            continue
        cleaned = _strip_excluded_urls_from_text(original, ahj_name=ahj)
        cleaned = _strip_nonlocal_urls_from_text(cleaned, city, state, result, block=(fld == "confidence_reason"))
        if cleaned != original:
            result[fld] = cleaned
            strips.append({"field": fld, "removed": bad[:3]})
    if strips:
        result["_url_strips"] = strips
    return result


def validate_and_sanitize_permit_result(result: dict, job_type: str, city: str, state: str) -> dict:
    """Hard gate against placeholder leaks + scope/checklist mismatch.

    Mutates result in place. Adds `_validation_issues` for telemetry,
    downgrades confidence, sets needs_review=True, and redacts placeholder
    fields so the UI can suppress them or show a "verify with building
    department" tag.
    """
    issues: list[dict] = []

    # 1. Placeholder check on top-level critical fields
    for fld in ('permit_name', 'permit_type', 'permit_summary'):
        v = result.get(fld)
        if _has_placeholder(v):
            issues.append({"field": fld, "kind": "placeholder", "value": v})
            result[fld] = None

    # 2. Placeholder check inside permits_required[]
    permits = result.get('permits_required') or []
    if isinstance(permits, list):
        for permit in permits:
            if not isinstance(permit, dict):
                continue
            for sub in ('permit_type', 'portal_selection'):
                pv = permit.get(sub) or ''
                if _has_placeholder(pv):
                    issues.append({"field": f"permits_required.{sub}", "kind": "placeholder", "value": pv})
                    permit[sub] = None

    # 3. Scope/checklist mismatch — commercial query, residential-only checklist
    is_commercial = bool(_COMMERCIAL_TOKENS.search(job_type or ""))
    # Cross-reference with primary scope detection — catches commercial signals
    # the regex misses (e.g. "85-person seating", "B-occupancy", "A-2 occupancy
    # change") that detect_primary_scope picks up via stronger lexicon.
    primary_scope_for_validation = 'residential'
    try:
        primary_scope_for_validation = detect_primary_scope(job_type or "")
        if not is_commercial:
            is_commercial = primary_scope_for_validation in _COMMERCIAL_PRIMARY_SCOPES
    except Exception:
        pass
    if is_commercial:
        checklist_blob = []
        for fld in ('inspect_checklist', 'common_mistakes', 'pro_tips', 'requirements', 'documents_needed'):
            v = result.get(fld) or []
            if isinstance(v, list):
                checklist_blob.extend(str(i) for i in v if i)
        text = " | ".join(checklist_blob)
        residential_hits = (
            len(_RESIDENTIAL_DECK_PATTERNS.findall(text)) +
            len(_RESIDENTIAL_HVAC_RES_PATTERNS.findall(text))
        )
        commercial_hits = len(_COMMERCIAL_TOKENS.findall(text))
        if residential_hits >= 2 and commercial_hits == 0:
            issues.append({
                "field": "inspect_checklist",
                "kind": "scope_mismatch_commercial_query_residential_checklist",
                "detail": f"residential_token_hits={residential_hits} commercial_token_hits={commercial_hits}",
            })

    # 4. Residential permit name on commercial scope — repair + flag.
    #    Catches the LLM-hallucinated "Roofing — Residential Re-Roof",
    #    "Building Permit - Residential Deck", "(Residential)" suffix, etc.
    #    on commercial queries. Surfaced by the Milwaukee 411 E Wisconsin
    #    restaurant TI review (2026-04-28): single-screenshot kills commercial
    #    credibility. Defense in depth on top of the prompt rule — strips the
    #    residential framing and falls back to a scope-aware neutral name when
    #    the strip leaves an empty / too-short stub.
    if is_commercial:
        fallback_name = _COMMERCIAL_FALLBACK_PERMIT_NAMES.get(
            primary_scope_for_validation,
            _COMMERCIAL_FALLBACK_PERMIT_NAMES['commercial'],
        )

        def _repair_permit_field(container: dict, key: str, label: str) -> None:
            v = container.get(key)
            if not isinstance(v, str) or not v:
                return
            cleaned, changed = _strip_residential_markers(v)
            # If after strip the stub is too short to ship (< 6 chars or just
            # "Permit" / "Building Permit"), fall back to the scope-default.
            stub_too_short = len(cleaned) < 6 or cleaned.strip().lower() in {
                "permit", "building permit", "building permit -", "building permit —",
            }
            if changed:
                final_value = fallback_name if stub_too_short else cleaned
                issues.append({
                    "field": label,
                    "kind": "residential_permit_name_on_commercial_scope",
                    "value": v,
                    "repaired_to": final_value,
                })
                container[key] = final_value

        _repair_permit_field(result, 'permit_name', 'permit_name')
        _repair_permit_field(result, 'permit_type', 'permit_type')

        permits_list = result.get('permits_required') or []
        if isinstance(permits_list, list):
            for idx, p in enumerate(permits_list):
                if isinstance(p, dict):
                    _repair_permit_field(p, 'permit_type', f'permits_required[{idx}].permit_type')
                    _repair_permit_field(p, 'portal_selection', f'permits_required[{idx}].portal_selection')

        companions = result.get('companion_permits') or []
        if isinstance(companions, list):
            for idx, p in enumerate(companions):
                if isinstance(p, dict):
                    _repair_permit_field(p, 'permit_type', f'companion_permits[{idx}].permit_type')
                    _repair_permit_field(p, 'name', f'companion_permits[{idx}].name')

    if issues:
        result['_validation_issues'] = issues
        result['needs_review'] = True
        if str(result.get('confidence') or '').lower() in ('high', 'medium'):
            result['confidence'] = 'low'
        existing_reason = (result.get('confidence_reason') or '').strip()
        kinds = sorted({i['kind'] for i in issues})
        validation_msg = (
            f"⚠️ Output validation flagged {len(issues)} issue(s): {', '.join(kinds)}. "
            "Confirm permit names + scope with the building department before relying on this."
        )
        result['confidence_reason'] = (existing_reason + " " + validation_msg).strip() if existing_reason else validation_msg

    return result


# ─── Main Research Function ───────────────────────────────────────────────────

def research_permit(job_type: str, city: str, state: str, zip_code: str = "", use_cache: bool = True, job_category: str = "residential", job_value: float | None = None, force_model: str | None = None) -> dict:
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
            apply_scope_aware_permit_classification(cached, job_type)
            apply_office_ti_rulebook(cached, job_type, city, state)
            apply_medical_clinic_ti_rulebook(cached, job_type, city, state)
            enforce_ti_min_permits_floor(cached, job_type, city, state)
            enforce_commercial_primary_permit_guardrail(cached, job_type, city, state)
            validate_and_sanitize_permit_result(cached, job_type, city, state)
            apply_state_expert_pack(cached, city, state, job_type)
            hedge_companion_permits(cached, job_type)
            enrich_result_with_serper_sources(cached, job_type, city, state)
            apply_fee_verify_caveat(cached)
            apply_rulebook_depth(cached, job_type, city, state)
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
    _model_used = _gemini_primary_model  # set by whichever engine answered

    # 2026-04-26: force_model override for benchmarking.
    # If set, only that engine is called (no fallback). Used by the
    # /api/permit benchmark bypass header so we can A/B both engines
    # through the FULL PermitIQ pipeline. None (default) = production
    # behavior (Gemini primary, gpt fallback).
    _gemini_aliases = {"gemini", "gemini-3-flash", _gemini_primary_model}
    _openai_aliases = {"openai", "gpt", "gpt-5.4-mini", _openai_fallback_model}
    _force = (force_model or "").strip().lower() if force_model else None

    def _call_gemini():
        if not _GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY not set — Gemini is now the primary engine; configure it in env")
        gemini_model = genai.GenerativeModel(
            model_name=_gemini_primary_model,
            generation_config=genai.GenerationConfig(
                temperature=0.1,
                max_output_tokens=3000,
                response_mime_type="application/json",
            )
        )
        gemini_prompt = f"{SYSTEM_PROMPT}\n\n{user_prompt}"
        gemini_resp = gemini_model.generate_content(
            gemini_prompt,
            request_options={"timeout": _GEMINI_REQUEST_TIMEOUT_S},
        )
        return gemini_resp.text

    def _call_openai():
        openai_client = _get_openai_client()
        response = openai_client.with_options(timeout=_OPENAI_REQUEST_TIMEOUT_S).chat.completions.create(
            model=_openai_fallback_model,
            messages=[
                {"role": "system",  "content": SYSTEM_PROMPT},
                {"role": "user",    "content": user_prompt},
            ],
            temperature=0.1,
            # 2026-04-27 evening: bumped 8000→16000 after Boban hit "Lookup failed"
            # on complex ADU/basement-conversion payloads. The model was producing
            # valid-but-truncated JSON when output approached the cap.
            max_completion_tokens=16000,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content

    if _force in _gemini_aliases:
        # Forced Gemini — no fallback.
        raw = _call_gemini()
        _model_used = _gemini_primary_model
        print(f"[engine] FORCED Gemini ({_gemini_primary_model}) responded in {round((time.time()-start)*1000)}ms")
    elif _force in _openai_aliases:
        # Forced OpenAI — no fallback.
        raw = _call_openai()
        _model_used = _openai_fallback_model
        print(f"[engine] FORCED OpenAI ({_openai_fallback_model}) responded in {round((time.time()-start)*1000)}ms")
    else:
        # Default production path: OpenAI primary, Gemini fallback.
        # 2026-04-28: swapped from Gemini primary after Opus 4.7 reviews
        # graded the engine 30% on commercial restaurant TI scenarios.
        try:
            raw = _call_openai()
            _model_used = _openai_fallback_model
            print(f"[engine] OpenAI primary ({_openai_fallback_model}) responded in {round((time.time()-start)*1000)}ms")
        except Exception as openai_err:
            print(f"[engine] OpenAI failed ({openai_err}), trying Gemini fallback ({_gemini_primary_model})...")
            try:
                raw = _call_gemini()
                _model_used = _gemini_primary_model
                print(f"[engine] Gemini fallback ({_gemini_primary_model}) responded in {round((time.time()-start)*1000)}ms")
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
            # 2026-04-27: Aggressive repair pass for the intermittent
            # "Expecting ',' delimiter" / "Unterminated string" failures from
            # gpt-5.4-mini on long prompts. We progressively trim the response
            # until it parses, recovering whatever fields fit. Better partial
            # data than a 500.
            result = _try_repair_truncated_json(cleaned)
            if result is not None:
                print(f"[engine] Repaired truncated/malformed JSON after model failure ({e2})")
            else:
                # Last resort: ask the model to retry with a shorter, stricter prompt.
                try:
                    result = _retry_with_minimal_prompt(user_prompt, _call_openai)
                    print(f"[engine] Recovered via minimal-prompt retry after JSON failure")
                except Exception as retry_err:
                    print(f"[engine] AI returned non-JSON response: {repr((raw or '')[:300])}")
                    print(f"[engine] Minimal-prompt retry also failed: {retry_err}")
                    raise RuntimeError(f"AI returned non-JSON output: {e2}")

    # 2026-04-26: Gemini sometimes returns a top-level JSON ARRAY instead of
    # an OBJECT (e.g. `[ {...} ]`). All downstream code assumes `result` is a
    # dict. If we got a list, unwrap the first object element; if there's no
    # dict in it, that's a real model failure and should error explicitly.
    if isinstance(result, list):
        first_dict = next((x for x in result if isinstance(x, dict)), None)
        if first_dict is None:
            print(f"[engine] AI returned a list with no dict element: {repr((raw or '')[:200])}")
            raise RuntimeError(f"AI returned a list-shaped response with no usable object: {raw[:200] if raw else ''}")
        print(f"[engine] AI returned list-shaped response — unwrapping first dict element")
        result = first_dict
    if not isinstance(result, dict):
        print(f"[engine] AI returned non-dict, non-list response: {type(result).__name__} {repr((raw or '')[:200])}")
        raise RuntimeError(f"AI returned non-dict output: {type(result).__name__}")

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
    # Tightened 2026-04-26:
    #   - Catch ranges with >10x ratio (e.g. "$50–$500") — too wide to be useful
    #   - Catch ranges with >5x ratio AND no .gov source backing it up
    #   - Keep all original "varies/contact/call" phrase rejections
    _fee = str(result.get("fee_range") or "").lower().strip()
    _VAGUE_FEE_PHRASES = [
        "varies", "contact", "call", "check with", "depends on", "consult",
        "not available", "unknown", "n/a", "tbd", "to be determined",
        "see website", "visit website", "refer to", "estimate", "estimated"
    ]
    _is_vague_phrase = bool(_fee) and any(p in _fee for p in _VAGUE_FEE_PHRASES)
    _is_wide_range = False
    if _fee and not _is_vague_phrase:
        # Detect $X-$Y patterns and compute ratio
        try:
            _range_match = re.search(r"\$\s?(\d[\d,.]*)[\s\-–—]+(?:to[\s]+)?\$?\s?(\d[\d,.]*)", _fee)
            if _range_match:
                _lo = float(_range_match.group(1).replace(",", ""))
                _hi = float(_range_match.group(2).replace(",", ""))
                if _lo > 0:
                    _ratio = _hi / _lo
                    if _ratio >= 10:
                        _is_wide_range = True  # Always reject 10x+ ranges
                    elif _ratio >= 5:
                        # Reject 5x+ ranges only if no .gov web source confirms it
                        _has_gov_source = any(".gov" in (s.get("url") if isinstance(s, dict) else str(s)).lower()
                                             for s in (result.get("sources") or []))
                        if not _has_gov_source:
                            _is_wide_range = True
        except (ValueError, ZeroDivisionError, AttributeError):
            pass
    if not _fee or _is_vague_phrase or _is_wide_range:
        # Replace vague answers with a clear fallback
        _reason = "vague phrase" if _is_vague_phrase else ("wide range" if _is_wide_range else "missing")
        result["fee_range"] = (
            f"Fee not confirmed — call the {result.get('applying_office') or city + ' building dept'} "
            f"or check their online fee schedule before applying."
        )
        result["_fee_unverified"] = True
        print(f"[fee_guard] Fee replaced for {city}, {state} ({_reason}): '{_fee[:80]}'")

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
        # Refuse county fallback if it's in a different state than the user typed.
        # CITY_TO_COUNTY maps city name → "harris_tx" / "cook_il" etc. The suffix is the state.
        # Without this check, "Pasadena, CA" would map to Harris County, TX.
        if county_key:
            county_state = county_key.rsplit("_", 1)[-1].upper() if "_" in county_key else ""
            if county_state and county_state != state.upper().strip():
                county_key = None
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

    # 2026-04-28: Wider verified_cities.db fallback (5,260 AHJs, vs ~263 in
    # _CITIES_KB JSON). Catches cities like Pasadena/Houston/Portland whose
    # apply_url was landing empty because the LLM emitted null and the
    # curated JSON had no entry. Only fills gaps — never overwrites a real
    # apply_url / phone / address the LLM or earlier fallback already
    # produced. Skipped on state/none matches where city name isn't trusted.
    if city_match_level not in ("state", "none"):
        _vrow = _get_verified_city_row(city, state)
        if _vrow:
            if not result.get("apply_url") and _vrow.get("portal_url"):
                result["apply_url"] = _vrow["portal_url"]
                print(f"[apply_url_fallback] verified_cities.db → {city}, {state}: {_vrow['portal_url']}")
            if (not result.get("apply_phone") or str(result.get("apply_phone", "")).startswith("Search:")) and _vrow.get("phone"):
                result["apply_phone"] = _vrow["phone"]
            if not result.get("apply_address") and _vrow.get("address"):
                result["apply_address"] = _vrow["address"]

    # 2026-04-28: state-license URL strip can't run before the verified_cities
    # fallback (verified_cities.db has clean portal URLs), but we re-run it now
    # in case any fallback step accidentally landed a state-license URL.
    result = strip_pdf_from_result(result)

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

    result = apply_state_amendment_citations(result, job_type, city, state)

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
        (["solar", "solar panel", "pv", "battery backup"],
         "Utility Interconnection / Permission to Operate",
         "May be required if the PV or battery system is grid-tied, exports power, uses net metering, changes the meter, or needs utility PTO.",
         "likely"),
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
        (["adu", "garage conversion", "accessory dwelling", "in-law suite"],
         "Planning/Zoning Clearance",
         "May be required if the property is in a historic district, zoning overlay, parking-sensitive area, or needs ADU covenant/site-plan review.",
         "possible"),
        (["adu", "garage conversion", "accessory dwelling", "in-law suite"],
         "Electrical Permit",
         "May be required if new circuits, subpanel, service load changes, smoke/CO alarms, or rewiring are included.",
         "likely"),
        (["adu", "garage conversion", "accessory dwelling", "in-law suite"],
         "Plumbing Permit",
         "May be required if a bathroom, kitchen, laundry, water line, sewer line, or DWV work is included.",
         "likely"),
        (["adu", "garage conversion", "accessory dwelling", "in-law suite"],
         "Mechanical Permit",
         "May be required if new HVAC, ducts, exhaust, ventilation, or combustion-air work is included.",
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

    # 2026-04-28: cross-jurisdiction source-locality filter. Drops sources whose
    # hostname/path doesn't match the queried AHJ — catches saratoga.ca.us on
    # Denver queries, nyc.gov on Phoenix queries, pw.lacounty.gov on City of LA
    # queries. Conservative: keeps anything in the federal/code-reference
    # allowlist (NFPA, IAPMO, ICC, ada.gov, energy.gov, etc.) and anything with
    # a city/state/state-code locality match. Telemetry on `_sources_locality_dropped`.
    try:
        apply_source_locality_hard_block(result, city, state)
    except Exception as _e:
        # Defensive — never let the filter break the engine
        print(f"[locality_filter] failed: {_e}")

    if _verified_entry and _verified_entry.get("verified_at"):
        result["last_verified_at"] = _verified_entry.get("verified_at")

    # 2026-04-26: Per-field source attribution. The pipeline already tracks
    # `field_sources` on individual candidates (Accela / web / KB) and
    # `machine_structured` data; surface a consolidated map on the final
    # result so the frontend can show contractors WHERE each field came from.
    # Sources priority (high → low): accela_api > auto_verified > kb >
    # machine_extracted > web_search > model_training.
    _field_sources: dict = {}
    # Accela-backed fields (highest trust)
    for cand in structured_candidates if 'structured_candidates' in locals() else []:
        if not isinstance(cand, dict):
            continue
        if cand.get("source") == "layer0_5_accela":
            for fld, src in (cand.get("field_sources") or {}).items():
                _field_sources.setdefault(fld, src)
    # Auto-verified entry takes the next slot (only if not already Accela)
    if _verified_entry:
        verified_url = (_verified_entry.get("source_url") or "")
        for fld in ("apply_url", "applying_office", "apply_phone", "apply_address", "fee_range"):
            if result.get(fld) and fld not in _field_sources:
                _field_sources[fld] = "auto_verified" + (f":{verified_url}" if verified_url else "")
    # KB-backed fields
    if city_match_level == "city":
        try:
            _load_knowledge()
            _city_key = city.lower().strip().replace(" ", "_") + "_" + state.lower().strip()
            _city_data = _CITIES_KB.get("cities", {}).get(_city_key)
            if _city_data:
                for fld, src_key in [("apply_url", "online_portal"), ("apply_phone", "phone"), ("apply_address", "address")]:
                    if result.get(fld) and _city_data.get(src_key) and fld not in _field_sources:
                        _field_sources[fld] = "city_kb"
        except Exception:
            pass
    # Machine-extracted (regex from scraped HTML) fields
    if 'machine_structured' in locals() and isinstance(machine_structured, dict):
        for fld, src_key in [("apply_phone", "phone"), ("apply_url", "portal_url"), ("apply_address", "address"), ("fee_range", "fees")]:
            if machine_structured.get(src_key) and fld not in _field_sources:
                _field_sources[fld] = "machine_extracted"
    # Web search backing — at least one .gov source in result["sources"]
    _sources_list = result.get("sources") or []
    _has_gov = any(
        ".gov" in (s.get("url") if isinstance(s, dict) else str(s)).lower()
        or ".us" in (s.get("url") if isinstance(s, dict) else str(s)).lower()
        for s in _sources_list
    )
    # County-fallback marker
    if result.get("county_fallback"):
        for fld in ("apply_url", "applying_office", "apply_phone"):
            if result.get(fld) and fld not in _field_sources:
                _field_sources[fld] = "county_fallback"
    # Anything still unattributed AND we have web sources → web_search
    # Anything still unattributed AND we have NO web sources → model_training
    for fld in ("permits_required", "permit_summary", "fee_range", "approval_timeline",
                "apply_url", "apply_phone", "apply_address", "applying_office",
                "inspect_checklist", "common_mistakes", "pro_tips", "total_cost_estimate"):
        if result.get(fld) and fld not in _field_sources:
            _field_sources[fld] = "web_search" if _has_gov else "model_training"
    result["_field_sources"] = _field_sources

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
    # 2026-04-26: Penalize web-only answers when no .gov source backed the AI.
    # Without web verification, a HIGH-confidence-looking answer is just
    # plausible LLM output. We require at least one .gov / .us source OR an
    # Accela hit OR an auto-verified entry to keep confidence at "high".
    _gov_source_present = any(
        ".gov" in (s.get("url") if isinstance(s, dict) else str(s)).lower()
        or ".us" in (s.get("url") if isinstance(s, dict) else str(s)).lower()
        for s in (result.get("sources") or [])
    )
    _accela_backed = bool((result.get("_field_sources") or {}).get("portal_url") == "accela_api") or \
                     bool(result.get("apply_url") and "accela.com" in str(result.get("apply_url", "")).lower())
    _verified_backed = bool(_verified_entry)
    if confidence == "high" and not (_gov_source_present or _accela_backed or _verified_backed):
        confidence = downgrade_confidence(confidence, 1)
        print(f"[confidence] Downgraded HIGH→{confidence} for {city}, {state}: no .gov/Accela/verified source backing")
    result["confidence"] = confidence
    result["confidence_reason"] = derive_confidence_reason(
        result, city_match_level, bool(_verified_entry), missing_fields, web_source_count
    )

    # Add metadata
    result["_meta"] = {
        "generated_at":    datetime.now().isoformat(),
        "response_ms":     elapsed,
        "cached":          False,
        # 2026-04-26: report which engine actually answered (primary or fallback)
        "model":           _model_used,
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

    # Tier A trust layer: conditional companion wording, claim-level Google
    # sources, and fee verify caveat. These only ADD fields / enrich text.
    hedge_companion_permits(result, job_type)
    enrich_result_with_serper_sources(result, job_type, city, state)
    apply_fee_verify_caveat(result)

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

    apply_scope_aware_permit_classification(result, job_type)
    enforce_ti_min_permits_floor(result, job_type, city, state)
    apply_residential_permit_name_specificity(result, job_type, city, state)
    apply_state_expert_pack(result, city, state, job_type)

    # 2026-04-28: Hidden Trigger Detector V1. Deterministic detection of
    # permit blockers the user didn't ask about (hood→fire suppression,
    # B→A-2 sprinkler retrofit, restroom→ADA path-of-travel 20% rule,
    # hillside→geotech/haul, oak→urban forestry, etc.). 44 triggers across
    # commercial restaurant TI, LA Hillside ADU, generic commercial,
    # multifamily, and residential single-trade scopes. Pure regex/token,
    # zero LLM calls, zero added latency. Both Opus + GPT-5.5 deep-thinks
    # named this as the single biggest moat differentiator.
    try:
        from hidden_trigger_detector import detect_hidden_triggers
        primary_scope_for_triggers = result.get("_primary_scope") or detect_primary_scope(job_type)
        result["hidden_triggers"] = detect_hidden_triggers(
            job_type=job_type, city=city, state=state,
            primary_scope=primary_scope_for_triggers, result=result,
        )
        apply_retail_ti_rulebook(result, job_type, city, state)
        apply_office_ti_rulebook(result, job_type, city, state)
        apply_medical_clinic_ti_rulebook(result, job_type, city, state)
    except Exception as e:
        print(f"[hidden_triggers] Failed: {e}")
        result["hidden_triggers"] = []

    # Final commercial structured-card reconciliation. This stays outside the
    # hidden-trigger try/except so a detector failure cannot let a residential
    # HVAC/changeout card leak as the primary permit for commercial TI.
    enforce_commercial_primary_permit_guardrail(result, job_type, city, state)

    # 2026-04-28: Fee Realism Guardrail V1. Closes the systematic 3-10x under-
    # quote bug Opus 4.7 grading caught across all 4 cities of restaurant TI
    # tests ($219 elec + $558 HVAC for an $8K-25K real fee). Per-scope sqft
    # floors + 25 jurisdiction multipliers + trigger adders. Pure deterministic
    # logic, zero LLM calls, zero added latency. Reads result['hidden_triggers']
    # so trigger adders compose with the just-detected triggers.
    try:
        from fee_realism_guardrail import apply_fee_realism_guardrail
        primary_scope_for_fee = result.get("_primary_scope") or detect_primary_scope(job_type)
        # 2026-04-28: the guardrail deep-copies and returns a new dict; we need
        # to merge it back into result so subsequent steps + save_cache see
        # the override. Earlier wiring discarded the return value, which is
        # why _fee_adjusted was null on every prod response despite local
        # tests passing — caught by Opus 4.7 re-grade run.
        guarded = apply_fee_realism_guardrail(result, job_type, city, state, primary_scope_for_fee)
        if isinstance(guarded, dict):
            result.update(guarded)
    except Exception as e:
        print(f"[fee_realism_guardrail] Failed: {e}")

    # 2026-04-28: Strip fabricated URLs from free-text fields BEFORE the
    # validation gate runs. The earlier source-grounding fix in
    # normalize_sources() filters the structured sources list, but LLM-emitted
    # URLs still leak inside fee_range / confidence_reason / permit_summary.
    # Real Opus 4.7 production leaks: ojp.gov (Phoenix), kauffman.org (Seattle),
    # archive.org/dailycolonist1978 (Vegas), pw.lacounty.gov (LA city query).
    sanitize_free_text_urls(result, city, state)

    # A4: purge ESS/solar/battery advisory residue from non-solar residential
    # scopes after permit/content assembly and before render/cache.
    purge_solar_ess_residue(result, job_type)

    # 2026-04-28: Final hard gate — catch placeholder leaks and scope mismatch
    # before the result reaches the contractor. Mutates result in place; if
    # issues are found, redacts the bad fields, downgrades confidence to
    # "low", and sets needs_review=True so the UI surfaces the warning.
    validate_and_sanitize_permit_result(result, job_type, city, state)
    apply_rulebook_depth(result, job_type, city, state)

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
