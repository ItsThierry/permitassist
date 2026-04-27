#!/usr/bin/env python3
"""Daily AHJ freshness watchdog.

Checks the top verified cities with one Serper query per city, compares the
best current fee-schedule / permit-portal URL against data/serper_cache.db,
and sends one batched Telegram alert through skills/alert/notify.sh.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
VERIFIED_CITIES_PATH = DATA_DIR / "verified_cities.json"
DEFAULT_CACHE_PATH = DATA_DIR / "serper_cache.db"
DEFAULT_CHANGE_LOG = DATA_DIR / "ahj-changes.log"
DEFAULT_NOTIFY = Path.home() / ".openclaw" / "workspace" / "skills" / "alert" / "notify.sh"
CLAIM_TYPE = "ahj_freshness"
SERPER_ENDPOINT = "https://google.serper.dev/search"
DAILY_CREDIT_CAP = 100
AUTO_REPLENISH_THRESHOLD_CREDITS = 10_000


@dataclass(frozen=True)
class City:
    city: str
    state: str


@dataclass
class SearchResult:
    query: str
    title: str
    url: str
    snippet: str


@dataclass
class Change:
    city: str
    state: str
    old_url: str
    new_url: str
    title: str


def utc_now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def make_query(city: str, state: str) -> str:
    return f'{city} {state} building permit fee schedule permit portal official city fees'


def load_top_cities(path: Path, limit: int) -> list[City]:
    data = json.loads(path.read_text())
    seen: set[tuple[str, str]] = set()
    cities: list[City] = []
    for entry in data.values():
        city = (entry.get("city") or "").strip()
        state = (entry.get("state") or "").strip().upper()
        if not city or not state:
            continue
        key = (city.lower(), state)
        if key in seen:
            continue
        seen.add(key)
        cities.append(City(city, state))
        if len(cities) >= limit:
            break
    return cities


def ensure_cache_schema(conn: sqlite3.Connection) -> None:
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
    cols = {row[1] for row in conn.execute("PRAGMA table_info(serper_claim_cache)")}
    if "verified_at" not in cols:
        conn.execute("ALTER TABLE serper_claim_cache ADD COLUMN verified_at REAL")
    conn.commit()


def cached_result(conn: sqlite3.Connection, city: str, state: str) -> SearchResult | None:
    try:
        row = conn.execute(
            """
            SELECT query, COALESCE(title,''), COALESCE(url,''), COALESCE(snippet,'')
            FROM serper_claim_cache
            WHERE city = ? AND state = ? AND claim_type = ?
            """,
            (city, state, CLAIM_TYPE),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    return SearchResult(*row)


def normalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower() or "https"
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/") or "/"
    return f"{scheme}://{host}{path}"


def score_result(item: dict[str, Any], city: str) -> int:
    title = (item.get("title") or "").lower()
    url = (item.get("link") or item.get("url") or "").lower()
    snippet = (item.get("snippet") or "").lower()
    haystack = f"{title} {url} {snippet}"
    score = 0
    if ".gov" in url:
        score += 8
    if "fee" in haystack or "fees" in haystack:
        score += 6
    if "permit" in haystack or "permitting" in haystack:
        score += 5
    if "schedule" in haystack:
        score += 3
    if "portal" in haystack:
        score += 2
    if city.lower() in haystack:
        score += 2
    if any(bad in url for bad in ("facebook.com", "permitplace.com", "yelp.com", "wikipedia.org")):
        score -= 8
    return score


def best_result(payload: dict[str, Any], query: str, city: str) -> SearchResult:
    organic = payload.get("organic") or []
    if not organic:
        return SearchResult(query=query, title="", url="", snippet="")
    chosen = max(organic, key=lambda item: score_result(item, city))
    return SearchResult(
        query=query,
        title=chosen.get("title") or "",
        url=chosen.get("link") or chosen.get("url") or "",
        snippet=chosen.get("snippet") or "",
    )


def serper_search(query: str, city: str, *, timeout: int = 15) -> SearchResult:
    api_key = os.environ.get("SERPER_API_KEY")
    if not api_key:
        raise RuntimeError("SERPER_API_KEY is not set")
    body = json.dumps({"q": query, "num": 5}).encode("utf-8")
    request = Request(
        SERPER_ENDPOINT,
        data=body,
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Serper HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Serper request failed: {exc.reason}") from exc
    return best_result(payload, query, city)


def mock_result(city: City, query: str, mock_change: str | None) -> SearchResult:
    slug = f"{city.city.lower().replace(' ', '-')}-{city.state.lower()}"
    url = f"https://www.{slug}.gov/building/permit-fee-schedule"
    if mock_change and mock_change.lower() in {city.city.lower(), f"{city.city}, {city.state}".lower(), f"{city.city} {city.state}".lower()}:
        url = f"https://www.{slug}.gov/building/updated-permit-fee-schedule"
    return SearchResult(
        query=query,
        title=f"{city.city} {city.state} Permit Fee Schedule",
        url=url,
        snippet="Official building permit fee schedule and permitting portal.",
    )


def upsert_result(conn: sqlite3.Connection, city: City, result: SearchResult, now: float) -> None:
    conn.execute(
        """
        INSERT INTO serper_claim_cache
            (city, state, claim_type, query, title, url, snippet, created_at, verified_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(city, state, claim_type) DO UPDATE SET
            query=excluded.query,
            title=excluded.title,
            url=excluded.url,
            snippet=excluded.snippet,
            verified_at=excluded.verified_at
        """,
        (city.city, city.state, CLAIM_TYPE, result.query, result.title, result.url, result.snippet, now, now),
    )


def append_change_log(path: Path, changes: list[Change]) -> None:
    if not changes:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for ch in changes:
            f.write(json.dumps({"ts": iso_now(), **ch.__dict__}, sort_keys=True) + "\n")


def notify(level: str, text: str, notify_cmd: Path, *, dry_run: bool) -> None:
    if dry_run:
        print(f"[notify:dry-run] {level}: {text}")
        return
    subprocess.run([str(notify_cmd), "--level", level, "--text", text], check=True)


def digest_text(total: int, changes: list[Change]) -> str:
    if changes:
        sample = "; ".join(f"{c.city} {c.state} fee schedule URL changed" for c in changes[:10])
        more = f" (+{len(changes) - 10} more)" if len(changes) > 10 else ""
        return f"AHJ Freshness Watchdog — Daily Report\nChecked {total} cities. {len(changes)} changes detected: {sample}{more}."
    return f"AHJ Freshness Watchdog — Daily Report\nChecked {total} cities. 0 changes detected."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily Serper AHJ freshness watchdog")
    parser.add_argument("--dry-run", action="store_true", help="print sample queries/results; no cache writes or real alerts")
    parser.add_argument("--limit", type=int, default=None, help="number of cities to check (hard-capped at 100)")
    parser.add_argument("--cache-path", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--verified-cities", type=Path, default=VERIFIED_CITIES_PATH)
    parser.add_argument("--change-log", type=Path, default=DEFAULT_CHANGE_LOG)
    parser.add_argument("--notify-command", type=Path, default=DEFAULT_NOTIFY)
    parser.add_argument("--mock-serper", action="store_true", help="use deterministic mock Serper results (no credits)")
    parser.add_argument("--mock-change", default=None, help="city or 'City ST' to force as changed with --mock-serper")
    parser.add_argument("--skip-alert", action="store_true", help="do not call notify command")
    return parser.parse_args()


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()

    requested_limit = args.limit if args.limit is not None else (5 if args.dry_run else DAILY_CREDIT_CAP)
    limit = min(requested_limit, DAILY_CREDIT_CAP)
    if requested_limit > DAILY_CREDIT_CAP:
        print(f"[watchdog] requested limit {requested_limit} capped to {DAILY_CREDIT_CAP} Serper credits/day")
    if limit <= 0:
        raise SystemExit("--limit must be positive")

    cities = load_top_cities(args.verified_cities, limit)
    projected_monthly = len(cities) * 30
    print(f"[watchdog] Script: {Path(__file__).resolve()}")
    print(f"[watchdog] Cities to check: {len(cities)} (daily Serper credits: {len(cities)}/{DAILY_CREDIT_CAP})")
    print(f"[watchdog] Cost projection: {len(cities)} credits/day ≈ {projected_monthly}/month; threshold {AUTO_REPLENISH_THRESHOLD_CREDITS}/month")
    print(f"[watchdog] Cost check: {'PASS' if projected_monthly < AUTO_REPLENISH_THRESHOLD_CREDITS else 'FAIL'}")

    if args.dry_run:
        print("[watchdog] DRY RUN: no cache writes, no real Telegram alerts, no Serper credits")

    if args.dry_run:
        if args.cache_path.exists():
            conn = sqlite3.connect(f"file:{args.cache_path}?mode=ro", uri=True)
        else:
            conn = sqlite3.connect(":memory:")
            ensure_cache_schema(conn)
    else:
        args.cache_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(args.cache_path)
        ensure_cache_schema(conn)

    changes: list[Change] = []
    updates: list[tuple[City, SearchResult]] = []
    errors = 0

    for idx, city in enumerate(cities, start=1):
        query = make_query(city.city, city.state)
        print(f"[{idx:03d}] QUERY: {query}")
        try:
            result = mock_result(city, query, args.mock_change) if args.mock_serper or args.dry_run else serper_search(query, city.city)
            old = cached_result(conn, city.city, city.state)
            old_norm = normalize_url(old.url) if old else ""
            new_norm = normalize_url(result.url)
            if old and old_norm and new_norm and old_norm != new_norm:
                changes.append(Change(city.city, city.state, old.url, result.url, result.title))
                print(f"      CHANGE: {old.url} -> {result.url}")
            else:
                status = "NEW" if not old else "same"
                print(f"      {status}: {result.url}")
            updates.append((city, result))
        except Exception as exc:
            errors += 1
            print(f"      ERROR: {exc}", file=sys.stderr)
        if not args.mock_serper and not args.dry_run:
            time.sleep(0.2)

    level = "P2"
    exit_code = 0
    if len(changes) >= 30:
        level = "P0"
        exit_code = 2
    elif len(changes) >= 10:
        level = "P1"

    if len(changes) >= 30:
        text = f"AHJ Freshness Watchdog HALTED — {len(changes)} changes across {len(cities)} cities; likely Serper/API problem. Cache was not updated."
        if not args.skip_alert:
            notify(level, text, args.notify_command, dry_run=args.dry_run)
        print(f"[watchdog] P0 halt: {len(changes)} changes; cache updates skipped")
        return exit_code

    if not args.dry_run:
        now = utc_now_ts()
        with conn:
            for city, result in updates:
                upsert_result(conn, city, result, now)
        append_change_log(args.change_log, changes)
        if not args.skip_alert:
            notify(level, digest_text(len(cities), changes), args.notify_command, dry_run=False)
    else:
        print("[watchdog] DRY RUN complete: skipped cache writes and alerts")

    print(f"[watchdog] Checked: {len(cities)}")
    print(f"[watchdog] Changes: {len(changes)}")
    print(f"[watchdog] Errors: {errors}")
    print(f"[watchdog] Alert level: {level}")
    return 1 if errors else exit_code


if __name__ == "__main__":
    raise SystemExit(main())
