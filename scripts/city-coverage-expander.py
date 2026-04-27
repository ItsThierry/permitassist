#!/usr/bin/env python3
"""Expand PermitAssist city coverage with Serper-grounded municipal data.

Tier semantics for this script:
  --tier 2: top 1,000 incorporated US places by 2024 Census population.
  --tier 3: cities 1,001-5,000, reserved for future scale-out.
  --tier 4: top US counties (≥15k population), the AHJ for unincorporated areas.

The run is intentionally Serper-only: no Gemini SDK and no direct OpenAI calls.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import shutil
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
DB_PATH = DATA_DIR / "verified_cities.db"
CENSUS_URL = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/cities/totals/sub-est2024.csv"
CENSUS_CACHE = DATA_DIR / "sub-est2024.csv"
COUNTIES_CENSUS_URL = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/counties/totals/co-est2024-alldata.csv"
COUNTIES_CACHE = DATA_DIR / "co-est2024-alldata.csv"
COUNTY_MIN_POP = 15_000
COUNTY_MAX_ROWS = 2_500
SERPER_URL = "https://google.serper.dev/search"

# Mirrored from api/research_engine.py source policy. We use this as a hard
# blocklist: never store competitor/directories/social URLs in verified_cities.
EXCLUDED_SOURCE_DOMAINS = {
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
    # Additional common scrape noise/directories. They are not source citations
    # PermitAssist should store for AHJ coverage rows.
    "countyoffice.org",
    "mapquest.com",
    "yellowpages.com",
    "manta.com",
    "wikipedia.org",
}

OFFICIAL_SOURCE_DOMAINS = {
    "nfpa.org",
    "iapmo.org",
    "ncqa.org",
    "ashrae.org",
    "iccsafe.org",
    "nationalbuildingcodes.com",
    "cityofpasadena.net",
    "houstonpermittingcenter.org",
    "harrispermits.org",
}

TRUSTED_PERMIT_PORTAL_DOMAINS = {
    "accela.com",
    "aca-prod.accela.com",
    "tylertech.com",
    "tylerhost.net",
    "permitportal.com",
    "viewpointcloud.com",
    "energovweb.com",
    "mygovernmentonline.org",
    "citizenserve.com",
    "municity.com",
    "civicaccess.com",
    "opengov.com",
    "laserfiche.com",
    "etrakit.net",
    "permitworks.com",
}

STATE_ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL",
    "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
    "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
    "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA",
    "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}

STATE_NAME_BY_ABBR = {v: k for k, v in STATE_ABBR.items()}

PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
VANITY_PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?(?:\(?([2-9]\d{2})\)?)[\s.-]?([2-9]\d{2})[\s.-]?([A-Z]{3,7})(?:\s*\((\d{4})\))?", re.I)
PHONE_LETTERS = str.maketrans({
    **dict.fromkeys(list("ABCabc"), "2"),
    **dict.fromkeys(list("DEFdef"), "3"),
    **dict.fromkeys(list("GHIghi"), "4"),
    **dict.fromkeys(list("JKLjkl"), "5"),
    **dict.fromkeys(list("MNOmno"), "6"),
    **dict.fromkeys(list("PQRSpqrs"), "7"),
    **dict.fromkeys(list("TUVtuv"), "8"),
    **dict.fromkeys(list("WXYZwxyz"), "9"),
})
ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+"
    r"[A-Za-z0-9][A-Za-z0-9 .,'#&/\-]{2,90}?\s+"
    r"(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|Drive|Dr\.?|Lane|Ln\.?|Way|Court|Ct\.?|Place|Pl\.?|Plaza|Parkway|Pkwy\.?|Highway|Hwy\.?|Circle|Cir\.?|Square|Trail|Trl\.?|Loop|Center|Centre)"
    r"(?:[,\s]+(?:Suite|Ste\.?|Room|Rm\.?|Floor|Fl\.?|Unit|#)\s*[A-Za-z0-9\-]+)?"
    r"(?:[,\s]+[A-Z][A-Za-z .'-]{2,40},?\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?)?",
    re.I,
)

PLACE_SUFFIX_RE = re.compile(r"\s+(city|town|village|borough|municipality|corporation)$", re.I)


@dataclass(frozen=True)
class CityRow:
    city: str
    state: str
    state_name: str
    population: int
    rank: int
    entity_type: str = "city"


@dataclass
class FieldValue:
    value: str | None = None
    source_url: str | None = None
    source_class: str = ""


class BudgetExceeded(RuntimeError):
    pass


class CreditMeter:
    def __init__(self, cap: int):
        self.cap = cap
        self.used = 0
        self.lock = threading.Lock()

    def reserve_ok(self) -> bool:
        with self.lock:
            return self.used < self.cap

    def add(self, credits: int) -> None:
        with self.lock:
            self.used += max(int(credits or 0), 0)
            if self.used > self.cap:
                raise BudgetExceeded(f"Serper credit cap exceeded: {self.used}>{self.cap}")

    def value(self) -> int:
        with self.lock:
            return self.used


def load_dotenv() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = val.strip().strip('"').strip("'")


def norm_domain(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw if "://" in raw else f"https://{raw}")
    domain = (parsed.hostname or parsed.netloc or "").lower().strip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def domain_matches(domain: str, candidate: str) -> bool:
    return domain == candidate or domain.endswith(f".{candidate}")


def source_class(url: str) -> str:
    domain = norm_domain(url)
    if not domain:
        return "excluded"
    if any(domain_matches(domain, blocked) for blocked in EXCLUDED_SOURCE_DOMAINS):
        return "excluded"
    if domain.endswith((".gov", ".us", ".mil")):
        return "official"
    if any(domain_matches(domain, allowed) for allowed in OFFICIAL_SOURCE_DOMAINS):
        return "official"
    first_label = domain.split(".")[0]
    if first_label.startswith(("cityof", "townof", "villageof", "countyof")):
        return "official"
    if any(token in domain for token in ("county", "borough", "parish")) and any(
        token in domain for token in ("permit", "building", "planning", "development")
    ):
        return "official"
    if any(domain_matches(domain, portal) for portal in TRUSTED_PERMIT_PORTAL_DOMAINS):
        return "portal"
    if domain.endswith(".org"):
        return "supplementary"
    return "other"


def is_excluded_url(url: str) -> bool:
    return source_class(url) == "excluded"


def is_officialish(url: str) -> bool:
    return source_class(url) == "official"


def clean_city_name(name: str) -> str:
    return PLACE_SUFFIX_RE.sub("", (name or "").strip())


def clean_text(value: str, max_len: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" \t\n;,-")
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
    return text


def download_census(force: bool = False) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if CENSUS_CACHE.exists() and not force:
        return CENSUS_CACHE
    print(f"Downloading Census city population CSV: {CENSUS_URL}", flush=True)
    req = urllib.request.Request(CENSUS_URL, headers={"User-Agent": "PermitAssist city coverage expander"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        CENSUS_CACHE.write_bytes(resp.read())
    return CENSUS_CACHE


def load_cities(force_census_refresh: bool = False) -> list[CityRow]:
    csv_path = download_census(force_census_refresh)
    rows: list[CityRow] = []
    with csv_path.open(newline="", encoding="latin1") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("SUMLEV") != "162":
                continue
            if row.get("FUNCSTAT") not in {"A", "S"}:
                continue
            state_name = row.get("STNAME", "")
            state = STATE_ABBR.get(state_name)
            if not state:
                continue
            try:
                pop = int(row.get("POPESTIMATE2024") or 0)
            except ValueError:
                continue
            if pop <= 10_000:
                continue
            city = clean_city_name(row.get("NAME", ""))
            if not city:
                continue
            rows.append(CityRow(city=city, state=state, state_name=state_name, population=pop, rank=0))
    rows.sort(key=lambda c: c.population, reverse=True)
    return [CityRow(c.city, c.state, c.state_name, c.population, idx + 1) for idx, c in enumerate(rows[:5000])]


def download_counties(force: bool = False) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if COUNTIES_CACHE.exists() and not force:
        return COUNTIES_CACHE
    print(f"Downloading Census counties CSV: {COUNTIES_CENSUS_URL}", flush=True)
    req = urllib.request.Request(COUNTIES_CENSUS_URL, headers={"User-Agent": "PermitAssist county coverage expander"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        COUNTIES_CACHE.write_bytes(resp.read())
    return COUNTIES_CACHE


def load_counties(force_census_refresh: bool = False) -> list[CityRow]:
    csv_path = download_counties(force_census_refresh)
    rows: list[CityRow] = []
    with csv_path.open(newline="", encoding="latin1") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("SUMLEV") != "050":
                continue
            state_name = row.get("STNAME", "")
            state = STATE_ABBR.get(state_name)
            if not state or state == "DC":
                continue
            ctyname = (row.get("CTYNAME") or "").strip()
            if not ctyname:
                continue
            # Skip Virginia/Missouri/Maryland/Nevada independent cities. Census
            # files them under SUMLEV 050 (county-equivalent) but they are real
            # cities with their own AHJ — already in tier 2/3 coverage.
            if ctyname.lower().endswith(" city"):
                continue
            try:
                pop = int(row.get("POPESTIMATE2024") or 0)
            except ValueError:
                continue
            if pop < COUNTY_MIN_POP:
                continue
            rows.append(CityRow(
                city=ctyname,
                state=state,
                state_name=state_name,
                population=pop,
                rank=0,
                entity_type="county",
            ))
    rows.sort(key=lambda c: c.population, reverse=True)
    return [
        CityRow(c.city, c.state, c.state_name, c.population, idx + 1, c.entity_type)
        for idx, c in enumerate(rows[:COUNTY_MAX_ROWS])
    ]


def init_db(db_path: Path) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS verified_cities (
              city TEXT NOT NULL,
              state TEXT NOT NULL,
              population INTEGER,
              tier INTEGER NOT NULL,
              badge_state TEXT NOT NULL,
              portal_url TEXT,
              building_dept_phone TEXT,
              building_dept_address TEXT,
              fee_schedule_url TEXT,
              application_url TEXT,
              serper_credits_used INTEGER,
              entity_type TEXT NOT NULL DEFAULT 'city',
              generated_at TEXT NOT NULL,
              PRIMARY KEY (city, state)
            )
            """
        )
        cols = {row[1] for row in conn.execute("PRAGMA table_info(verified_cities)").fetchall()}
        if "entity_type" not in cols:
            conn.execute("ALTER TABLE verified_cities ADD COLUMN entity_type TEXT NOT NULL DEFAULT 'city'")
        conn.commit()


def backup_db_if_needed(db_path: Path) -> Path | None:
    if not db_path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db_path.with_suffix(f".db.bak-{stamp}")
    shutil.copy2(db_path, backup)
    return backup


def already_processed(db_path: Path, cities: list[CityRow]) -> set[tuple[str, str]]:
    if not db_path.exists():
        return set()
    wanted = {(c.city, c.state) for c in cities}
    if not wanted:
        return set()
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT city, state FROM verified_cities").fetchall()
    return {(city, state) for city, state in rows if (city, state) in wanted}


def upsert_result(db_path: Path, result: dict[str, Any]) -> None:
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO verified_cities
            (city, state, population, tier, badge_state, portal_url, building_dept_phone,
             building_dept_address, fee_schedule_url, application_url, serper_credits_used,
             entity_type, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result["city"], result["state"], result["population"], result["tier"], result["badge_state"],
                result.get("portal_url"), result.get("building_dept_phone"), result.get("building_dept_address"),
                result.get("fee_schedule_url"), result.get("application_url"), result.get("serper_credits_used", 0),
                result.get("entity_type", "city"),
                result["generated_at"],
            ),
        )
        conn.commit()


def serper_search(query: str, key: str, meter: CreditMeter, num: int = 10, timeout: int = 20) -> dict[str, Any]:
    if not meter.reserve_ok():
        raise BudgetExceeded("Serper credit cap reached")
    payload = json.dumps({"q": query, "num": num, "gl": "us", "hl": "en"}).encode("utf-8")
    headers = {"X-API-KEY": key, "Content-Type": "application/json", "User-Agent": "PermitAssist city coverage expander"}
    delay = 1.0
    last_err = ""
    charged_total = 0
    for attempt in range(3):
        req = urllib.request.Request(SERPER_URL, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                data = json.loads(body)
                charged = int(data.get("credits") or 1)
                meter.add(charged)
                charged_total += charged
                data["_credits_charged"] = charged_total
                return data
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]
            last_err = f"HTTP {e.code}: {body}"
            # Count HTTP responses conservatively as one attempted credit.
            meter.add(1)
            charged_total += 1
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                retry_after = e.headers.get("Retry-After")
                sleep_for = float(retry_after) if retry_after and retry_after.isdigit() else delay
                time.sleep(sleep_for)
                delay *= 2
                continue
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = str(e)
            if attempt < 2:
                time.sleep(delay)
                delay *= 2
                continue
            break
    return {"organic": [], "_error": last_err, "credits": 0, "_credits_charged": charged_total}


def result_items(data: dict[str, Any], query_kind: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    answer = data.get("answerBox") or {}
    if answer.get("link"):
        # Answer boxes can point to broad state portals rather than the AHJ.
        # Keep them as fallback evidence, but rank below organic municipal hits.
        out.append({
            "title": answer.get("title") or "Answer",
            "url": answer.get("link"),
            "snippet": answer.get("snippet") or " ".join(answer.get("snippetHighlighted") or []),
            "position": 50,
            "query_kind": query_kind,
        })
    kg = data.get("knowledgeGraph") or {}
    if kg.get("website") or kg.get("link"):
        attrs = kg.get("attributes") or {}
        snippet_bits = [kg.get("description") or "", kg.get("phone") or attrs.get("Phone") or "", kg.get("address") or attrs.get("Address") or ""]
        out.append({
            "title": kg.get("title") or "Knowledge graph",
            "url": kg.get("website") or kg.get("link"),
            "snippet": " ".join(str(x) for x in snippet_bits if x),
            "position": 0,
            "query_kind": query_kind,
        })
    for r in data.get("organic") or []:
        url = r.get("link") or r.get("url") or ""
        if not url or is_excluded_url(url):
            continue
        out.append({
            "title": clean_text(r.get("title") or "", 200),
            "url": url,
            "snippet": clean_text(r.get("snippet") or r.get("content") or "", 700),
            "position": int(r.get("position") or 99),
            "query_kind": query_kind,
        })
    return out


def city_aliases(city: CityRow) -> set[str]:
    city_l = city.city.lower()
    aliases = {city_l, city_l.replace(" ", "-"), city_l.replace(" ", "")}
    aliases.add(re.sub(r"[^a-z0-9]", "", city_l))
    if city.city == "New York" and city.state == "NY":
        # Avoid treating every New York State result as NYC. NYC municipal pages
        # reliably include nyc/nyc.gov or the explicit "New York City" phrase.
        return {"nyc", "new york city", "new-york-city", "newyorkcity"}
    return {a for a in aliases if a}


def city_signal(item: dict[str, Any], city: CityRow) -> bool:
    hay = " ".join([item.get("url", ""), item.get("title", ""), item.get("snippet", "")]).lower()
    city_l = city.city.lower()
    # Avoid common false positives for neighboring directional municipalities:
    # South San Francisco != San Francisco, North Miami != Miami, etc.
    if not any(city_l.startswith(p + " ") for p in ("north", "south", "east", "west")):
        for prefix in ("north", "south", "east", "west"):
            if re.search(rf"\b{prefix}\s+{re.escape(city_l)}\b", hay):
                return False
    hay_alnum = re.sub(r"[^a-z0-9]", "", hay)
    for alias in city_aliases(city):
        if alias in hay:
            return True
        alias_alnum = re.sub(r"[^a-z0-9]", "", alias)
        if alias_alnum and alias_alnum in hay_alnum:
            return True
    if f"city of {city_l}" in hay or f"town of {city_l}" in hay or f"village of {city_l}" in hay:
        return True
    return False


def state_mismatch(item: dict[str, Any], city: CityRow) -> bool:
    hay = " ".join([item.get("url", ""), item.get("title", ""), item.get("snippet", "")]).lower()
    domain = norm_domain(item.get("url", ""))
    compact_domain = re.sub(r"[^a-z0-9]", "", domain)
    city_compact = re.sub(r"[^a-z0-9]", "", city.city.lower())
    for abbr, name in STATE_NAME_BY_ABBR.items():
        if abbr == city.state:
            continue
        abbr_l = abbr.lower()
        if name.lower() in hay:
            return True
        if re.search(rf"(^|[,\s/-]){re.escape(abbr_l)}($|[,\s/.-]|\d)", hay):
            return True
        # Domains like portlandtx.gov or cityoflongbeachms.info.
        if city_compact and f"{city_compact}{abbr_l}" in compact_domain:
            return True
    return False


def is_state_agency_domain(url: str, city: CityRow) -> bool:
    domain = norm_domain(url)
    state_l = city.state.lower()
    return domain == f"{state_l}.gov" or domain.endswith(f".{state_l}.gov")


def field_source_class(item: dict[str, Any] | None, city: CityRow) -> str:
    if not item:
        return ""
    cls = source_class(item.get("url", ""))
    # Tier-2 verification is stricter than storage: an official-looking domain
    # only counts for a field when the result itself has a city/AHJ signal and
    # is not merely a broad state-agency page.
    if cls == "official" and (not city_signal(item, city) or state_mismatch(item, city) or is_state_agency_domain(item.get("url", ""), city)):
        return "other"
    return cls


def location_score(item: dict[str, Any], city: CityRow) -> int:
    hay = " ".join([item.get("url", ""), item.get("title", ""), item.get("snippet", "")]).lower()
    state_l = city.state.lower()
    state_name_l = city.state_name.lower()
    score = 0
    if city_signal(item, city):
        score += 20
    elif len(city.city) >= 5:
        score -= 12
    if state_name_l in hay or re.search(rf"(^|[^a-z]){re.escape(state_l)}([^a-z]|$)", hay):
        score += 10
    if state_mismatch(item, city):
        score -= 60
    return score


def score_item(item: dict[str, Any], city: CityRow, want: str) -> int:
    url = item.get("url", "")
    hay = " ".join([url, item.get("title", ""), item.get("snippet", "")]).lower()
    cls = source_class(url)
    score = location_score(item, city)
    score += {"official": 100, "portal": 70, "supplementary": 30, "other": 5}.get(cls, -100)
    if item.get("position") is not None:
        score += max(0, 12 - int(item.get("position") or 99))
    parsed = urllib.parse.urlparse(url)
    domain = norm_domain(url)
    if (parsed.path or "/") == "/" and domain == f"{city.state.lower()}.gov":
        score -= 60
    if is_state_agency_domain(url, city):
        score -= 80
    common_positive = ("building", "permit", "permits", "planning", "development", "inspection", "code")
    score += sum(4 for token in common_positive if token in hay)
    if want == "portal":
        for token in ("online", "portal", "apply", "aca", "accela", "etrakit", "energov", "citizenserve", "civicaccess", "permitcenter"):
            if token in hay:
                score += 8
    elif want == "contact":
        for token in ("contact", "phone", "address", "office", "department", "division"):
            if token in hay:
                score += 7
    elif want == "fee":
        for token in ("fee", "fees", "schedule", "valuation", "pdf"):
            if token in hay:
                score += 10
    elif want == "application":
        for token in ("application", "apply", "requirements", "forms", "submittal", "checklist"):
            if token in hay:
                score += 9
    # This batch is incorporated-place coverage. County portals often outrank
    # city AHJs in Google for large metros (e.g. Los Angeles County vs City of
    # Los Angeles), so prefer city-owned results when both are present.
    if "county" in hay and "city of" not in hay and not city.city.lower().endswith(" county"):
        score -= 35
    for bad in ("jobs", "bid", "procurement", "agenda", "minutes", "facebook", "calendar"):
        if bad in hay:
            score -= 15
    return score


def best_item(items: list[dict[str, Any]], city: CityRow, want: str) -> dict[str, Any] | None:
    if not items:
        return None
    ranked = sorted(items, key=lambda item: score_item(item, city, want), reverse=True)
    return ranked[0] if ranked else None


def clean_phone(raw: str) -> str:
    phone = re.sub(r"[^0-9]", "", raw or "")
    if len(phone) == 11 and phone.startswith("1"):
        phone = phone[1:]
    if len(phone) != 10:
        return raw.strip()
    return f"({phone[:3]}) {phone[3:6]}-{phone[6:]}"


def phones_from_text(text: str) -> list[str]:
    phones: list[str] = []
    for area, prefix, letters, parenthetic in VANITY_PHONE_RE.findall(text or ""):
        suffix = parenthetic or (letters.translate(PHONE_LETTERS)[:4])
        if len(suffix) == 4:
            phones.append(f"{area}{prefix}{suffix}")
    for match in PHONE_RE.findall(text or ""):
        digits = re.sub(r"\D", "", match)
        if len(digits) in (10, 11):
            phones.append(digits)
    return phones


def extract_phone(items: list[dict[str, Any]], city: CityRow) -> FieldValue:
    ranked = sorted(items, key=lambda item: score_item(item, city, "contact"), reverse=True)
    for item in ranked:
        if not city_signal(item, city) or state_mismatch(item, city):
            continue
        hay = " ".join([item.get("title", ""), item.get("snippet", "")])
        for phone in phones_from_text(hay):
            digits = re.sub(r"\D", "", phone)
            if len(digits) in (10, 11):
                return FieldValue(clean_phone(phone), item.get("url"), field_source_class(item, city))
    return FieldValue()


def extract_address(items: list[dict[str, Any]], city: CityRow) -> FieldValue:
    ranked = sorted(items, key=lambda item: score_item(item, city, "contact"), reverse=True)
    for item in ranked:
        if not city_signal(item, city) or state_mismatch(item, city):
            continue
        hay = " ".join([item.get("title", ""), item.get("snippet", "")])
        for match in ADDRESS_RE.findall(hay):
            val = clean_text(match, 180)
            if len(val) >= 12 and not PHONE_RE.search(val):
                return FieldValue(val, item.get("url"), field_source_class(item, city))
    return FieldValue()


def queries_for(city: CityRow) -> list[tuple[str, str]]:
    loc = f"{city.city} {city.state}"
    state_name = city.state_name
    if city.entity_type == "county":
        return [
            ("portal", f"{loc} unincorporated building permit portal online application"),
            ("contact", f"{loc} building department permits phone address official"),
            ("fee", f"{loc} building permit fee schedule {datetime.now().year} pdf"),
            ("application", f"{loc} unincorporated building permit application requirements"),
            ("requirements", f"site:.gov OR site:.us {city.city} {state_name} building permits unincorporated"),
        ]
    return [
        ("portal", f"{loc} official building permit portal online application"),
        ("contact", f"{loc} building department permits phone address official"),
        ("fee", f"{loc} building permit fee schedule {datetime.now().year} pdf"),
        ("application", f"{loc} building permit application requirements forms"),
        ("requirements", f"site:.gov OR site:.us {city.city} {state_name} building permits requirements"),
    ]


def process_city(city: CityRow, key: str, meter: CreditMeter) -> dict[str, Any]:
    started = time.time()
    all_items: list[dict[str, Any]] = []
    items_by_kind: dict[str, list[dict[str, Any]]] = {}
    local_credits = 0
    errors = []
    for kind, query in queries_for(city):
        data = serper_search(query, key, meter, num=10)
        local_credits += int(data.get("_credits_charged") or data.get("credits") or 0)
        if data.get("_error"):
            errors.append(f"{kind}:{data.get('_error')}")
        items = result_items(data, kind)
        items_by_kind[kind] = items
        all_items.extend(items)

    portal_candidates = items_by_kind.get("portal", []) or (items_by_kind.get("application", []) + items_by_kind.get("requirements", []))
    fee_candidates = items_by_kind.get("fee", []) or all_items
    app_candidates = (items_by_kind.get("application", []) + items_by_kind.get("requirements", [])) or all_items
    portal_item = best_item(portal_candidates, city, "portal")
    fee_item = best_item(fee_candidates, city, "fee")
    app_item = best_item(app_candidates, city, "application")
    # Contact fields are intentionally extracted only from the contact query.
    # Falling back to all result snippets raised false positives for ambiguous
    # city names (Portland ME/TX, Long Beach MS, South San Francisco, etc.).
    contact_items = items_by_kind.get("contact", [])
    phone = extract_phone(contact_items, city)
    address = extract_address(contact_items, city)

    portal = FieldValue(portal_item.get("url"), portal_item.get("url"), field_source_class(portal_item, city)) if portal_item else FieldValue()
    fee = FieldValue(fee_item.get("url"), fee_item.get("url"), field_source_class(fee_item, city)) if fee_item else FieldValue()
    application = FieldValue(app_item.get("url"), app_item.get("url"), field_source_class(app_item, city)) if app_item else FieldValue()

    # Final safety: never store excluded URLs even if scoring changes later.
    for fv in (portal, fee, application):
        if fv.value and is_excluded_url(fv.value):
            fv.value = None
            fv.source_url = None
            fv.source_class = "excluded"

    field_sources = [portal, fee, application, phone, address]
    populated_fields = sum(1 for fv in field_sources if fv.value)
    official_fields = sum(1 for fv in field_sources if fv.value and fv.source_class == "official")

    if populated_fields == 0:
        tier = 4
        badge = "limited"
    elif official_fields >= 3:
        tier = 2
        badge = "verified"
    else:
        tier = 3
        badge = "ai_researched"

    return {
        "city": city.city,
        "state": city.state,
        "population": city.population,
        "tier": tier,
        "badge_state": badge,
        "portal_url": portal.value,
        "building_dept_phone": phone.value,
        "building_dept_address": address.value,
        "fee_schedule_url": fee.value,
        "application_url": application.value,
        "serper_credits_used": local_credits,
        "entity_type": city.entity_type,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_seconds": time.time() - started,
        "official_fields": official_fields,
        "populated_fields": populated_fields,
        "errors": "; ".join(errors)[:500],
    }


def target_slice(cities: list[CityRow], counties: list[CityRow], tier: int) -> list[CityRow]:
    if tier == 2:
        return cities[:1000]
    if tier == 3:
        return cities[1000:5000]
    if tier == 4:
        return counties
    raise ValueError("Only --tier 2, 3, or 4 are supported")


def db_counts(db_path: Path) -> dict[int, int]:
    if not db_path.exists():
        return {}
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT tier, count(*) FROM verified_cities GROUP BY tier ORDER BY tier").fetchall()
    return {int(tier): int(count) for tier, count in rows}


def tier2_sample(db_path: Path, n: int = 20) -> list[tuple[str, str, str, str]]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT city, state, COALESCE(portal_url, ''), COALESCE(building_dept_phone, '')
            FROM verified_cities
            WHERE tier = 2
            ORDER BY RANDOM()
            LIMIT ?
            """,
            (n,),
        ).fetchall()
    return [(str(a), str(b), str(c), str(d)) for a, b, c, d in rows]


def total_db_credits_for_targets(db_path: Path, targets: list[CityRow]) -> int:
    if not targets or not db_path.exists():
        return 0
    wanted = {(c.city, c.state) for c in targets}
    total = 0
    with sqlite3.connect(db_path) as conn:
        for city, state, credits in conn.execute("SELECT city, state, COALESCE(serper_credits_used, 0) FROM verified_cities"):
            if (city, state) in wanted:
                total += int(credits or 0)
    return total


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build PermitAssist verified city coverage via Serper.")
    parser.add_argument("--tier", type=int, choices=[2, 3, 4], required=True, help="Coverage tier batch to run: 2=top 1000 cities, 3=cities 1001-5000, 4=top counties.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--workers", type=int, default=20, help="ThreadPoolExecutor workers; capped at 20.")
    parser.add_argument("--credit-cap", type=int, default=8000)
    parser.add_argument("--max-cities", type=int, default=None, help="Optional smaller cap for smoke tests/resume slices.")
    parser.add_argument("--force-census-refresh", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Load targets and print plan without Serper calls or DB writes.")
    parser.add_argument("--sample", type=int, default=20)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.workers > 20:
        print("--workers capped at 20 to respect Serper rate limits", flush=True)
    workers = max(1, min(args.workers, 20))
    load_dotenv()
    key = os.environ.get("SERPER_API_KEY", "").strip()
    if not key and not args.dry_run:
        print("ERROR: SERPER_API_KEY missing from environment/.env", file=sys.stderr)
        return 2

    all_cities = load_cities(args.force_census_refresh) if args.tier in (2, 3) else []
    all_counties = load_counties(args.force_census_refresh) if args.tier == 4 else []
    targets = target_slice(all_cities, all_counties, args.tier)
    if args.max_cities is not None:
        targets = targets[: max(0, args.max_cities)]
    if args.tier == 4:
        print(f"Loaded {len(all_counties)} Census counties >={COUNTY_MIN_POP} population; target tier {args.tier}: {len(targets)} counties", flush=True)
    else:
        print(f"Loaded {len(all_cities)} Census incorporated places >10k population; target tier {args.tier}: {len(targets)} cities", flush=True)

    if args.dry_run:
        for c in targets[:10]:
            print(f"#{c.rank}: {c.city}, {c.state} pop={c.population}")
        return 0

    backup = backup_db_if_needed(args.db)
    if backup:
        print(f"Backed up existing DB: {backup}", flush=True)
    init_db(args.db)
    done = already_processed(args.db, targets)
    pending = [c for c in targets if (c.city, c.state) not in done]
    print(f"Idempotency: {len(done)} already processed, {len(pending)} pending", flush=True)

    meter = CreditMeter(args.credit_cap)
    completed = 0
    elapsed_sum = 0.0
    tier_counts_run: dict[int, int] = {2: 0, 3: 0, 4: 0}
    started = time.time()

    if pending:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process_city, city, key, meter): city for city in pending}
            for future in as_completed(futures):
                city = futures[future]
                try:
                    result = future.result()
                except BudgetExceeded as e:
                    print(f"CREDIT CAP STOP: {e}", file=sys.stderr, flush=True)
                    for f in futures:
                        f.cancel()
                    break
                except Exception as e:
                    result = {
                        "city": city.city,
                        "state": city.state,
                        "population": city.population,
                        "tier": 4,
                        "badge_state": "limited",
                        "portal_url": None,
                        "building_dept_phone": None,
                        "building_dept_address": None,
                        "fee_schedule_url": None,
                        "application_url": None,
                        "serper_credits_used": 0,
                        "entity_type": city.entity_type,
                        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "elapsed_seconds": 0,
                        "official_fields": 0,
                        "populated_fields": 0,
                        "errors": str(e)[:500],
                    }
                upsert_result(args.db, result)
                completed += 1
                elapsed_sum += float(result.get("elapsed_seconds") or 0)
                tier_counts_run[int(result["tier"])] = tier_counts_run.get(int(result["tier"]), 0) + 1
                if completed % 25 == 0 or completed == len(pending):
                    avg = elapsed_sum / completed if completed else 0
                    print(
                        f"Progress: {completed}/{len(pending)} pending complete | run credits={meter.value()} | "
                        f"run tier counts={tier_counts_run} | avg={avg:.2f}s/city",
                        flush=True,
                    )

    counts = db_counts(args.db)
    sample = tier2_sample(args.db, args.sample)
    avg_time = elapsed_sum / completed if completed else 0.0
    db_target_credits = total_db_credits_for_targets(args.db, targets)

    print("\n=== City Coverage Expander Final stdout ===")
    print(f"Script file path: {Path(__file__).resolve()}")
    print(f"DB path: {args.db.resolve()}")
    print("DB row counts by tier:")
    for tier in (2, 3, 4):
        print(f"  tier {tier}: {counts.get(tier, 0)}")
    print(f"Total Serper credits used this run: {meter.value()}")
    print(f"Total Serper credits recorded for target batch: {db_target_credits}")
    print(f"Generation time per city avg: {avg_time:.2f}s")
    print(f"20 random Tier 2 sample rows for human review (city, state, portal_url, phone):")
    for row in sample:
        print(f"  {row[0]}, {row[1]} | {row[2]} | {row[3]}")
    tier2, tier3, tier4 = counts.get(2, 0), counts.get(3, 0), counts.get(4, 0)
    if args.tier == 2 and len(targets) == 1000 and 600 <= tier2 <= 800 and 200 <= tier3 <= 400 and tier4 <= 50 and db_target_credits <= args.credit_cap:
        print('Self-assessment: "Tier 2 batch ready for Boban + Opus quality review"')
    else:
        print("Self-assessment: Tier counts/cost need review before Boban + Opus quality review")
    print(f"Wall time: {(time.time() - started) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
