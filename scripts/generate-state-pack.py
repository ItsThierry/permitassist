#!/usr/bin/env python3
"""Generate draft state expert packs from Serper + Claude CLI.

Drafts are intentionally written outside api/state_packs.py so a human can
review them before promotion into STATE_PACKS. LLM grounding must go through
Boban's subscription Claude Max CLI auth; this script does not call model APIs
directly.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DRAFT_DIR = ROOT / "api" / "state_packs" / "drafts"
STATE_PACKS_PY = ROOT / "api" / "state_packs.py"
TOP9 = ["TX", "FL", "NY", "IL", "GA", "AZ", "NC", "WA", "CO"]
MAX_SERPER_CREDITS_PER_STATE = 10
MAX_TOTAL_SERPER_CREDITS = 350
MIN_RULES = 5
MAX_RULES = 8
CLAUDE_TIMEOUT_SECONDS = int(os.environ.get("STATE_PACK_CLAUDE_TIMEOUT", "420"))

STATE_NAMES = {
    "TX": "Texas",
    "FL": "Florida",
    "NY": "New York",
    "IL": "Illinois",
    "GA": "Georgia",
    "AZ": "Arizona",
    "NC": "North Carolina",
    "WA": "Washington",
    "CO": "Colorado",
    # Next 10 by 2024 population, added 2026-04-27 for Tier 2 state-pack expansion.
    "PA": "Pennsylvania",
    "OH": "Ohio",
    "MI": "Michigan",
    "NJ": "New Jersey",
    "VA": "Virginia",
    "TN": "Tennessee",
    "MA": "Massachusetts",
    "IN": "Indiana",
    "MD": "Maryland",
    "MO": "Missouri",
    # Final 30 (2026-04-27 evening) — finishing all 50 states.
    "WI": "Wisconsin", "MN": "Minnesota", "SC": "South Carolina", "AL": "Alabama",
    "LA": "Louisiana", "KY": "Kentucky", "OR": "Oregon", "OK": "Oklahoma",
    "CT": "Connecticut", "UT": "Utah",
    "IA": "Iowa", "NV": "Nevada", "AR": "Arkansas", "KS": "Kansas",
    "MS": "Mississippi", "NM": "New Mexico", "NE": "Nebraska", "ID": "Idaho",
    "WV": "West Virginia", "HI": "Hawaii",
    "NH": "New Hampshire", "ME": "Maine", "RI": "Rhode Island", "MT": "Montana",
    "DE": "Delaware", "SD": "South Dakota", "ND": "North Dakota", "AK": "Alaska",
    "VT": "Vermont", "WY": "Wyoming",
}

QUERY_TEMPLATES = [
    "{name} state contractor license board HVAC electrical plumbing residential permit",
    "{name} building code adoption residential code energy code amendments",
    "{name} permit review timeline state law residential construction ADU",
    "{name} municipal versus county building department permit jurisdiction split",
    "{name} overlay permit risk flood hurricane wildfire wetland historic district",
    "{name} municipal utilities electric utility interconnection building permits",
    "{name} seller disclosure permit energy disclosure real estate law",
    "{name} common contractor licensing permit mistakes state construction",
]

# State-specific search hints keep the 8-query budget high-signal.
STATE_QUERY_HINTS = {
    "TX": [
        "Texas TDLR electrical HVAC TSBPE plumbing contractor license",
        "Texas Department of Insurance windstorm certificate coastal counties permits",
        "Texas municipal utility districts electric cooperatives building permits",
    ],
    "FL": [
        "Florida DBPR contractor license certified registered electrical HVAC plumbing",
        "Florida Building Code hurricane high velocity hurricane zone permits",
        "Florida notice of commencement construction permit threshold",
    ],
    "NY": [
        "New York State Uniform Code Energy Code residential permits",
        "New York contractor licensing local NYC DOB home improvement license",
        "New York wetlands historic district floodplain permit construction",
    ],
    "IL": [
        "Illinois contractor license roofing plumbing electrical local permits",
        "Illinois Energy Conservation Code residential building code adoption",
        "Chicago building permits Department of Buildings contractor requirements",
    ],
    "GA": [
        "Georgia construction industry licensing board residential general contractor conditioned air electrical plumbing",
        "Georgia state minimum standard codes building energy code amendments",
        "Georgia coastal marshlands floodplain historic preservation permits",
    ],
    "AZ": [
        "Arizona Registrar of Contractors license residential commercial contractor permits",
        "Arizona building code local adoption energy code Phoenix Tucson",
        "Arizona water conservation assured water supply building permits solar utility",
    ],
    "NC": [
        "North Carolina Licensing Board general contractors electrical plumbing HVAC permits",
        "North Carolina Residential Code building code adoption coastal wind flood permits",
        "North Carolina permit choice statute development approval vesting timelines",
    ],
    "WA": [
        "Washington State L&I contractor registration electrical plumbing permits",
        "Washington State Energy Code residential building code adoption",
        "Washington critical areas wetlands shoreline permit residential construction",
    ],
    "CO": [
        "Colorado contractor license local building permits electrical plumbing state board",
        "Colorado model electric ready solar ready building code energy code",
        "Colorado wildfire wildland urban interface building code permit requirements",
    ],
    "PA": [
        "Pennsylvania Uniform Construction Code UCC residential building electrical plumbing inspector",
        "Pennsylvania Home Improvement Consumer Protection Act HIC contractor registration attorney general",
        "Pennsylvania Act 167 stormwater Act 537 sewage municipal floodplain historic permits",
    ],
    "OH": [
        "Ohio Construction Industry Licensing Board OCILB residential electrical HVAC plumbing license",
        "Ohio Residential Code RCO building energy code adoption local amendments",
        "Ohio floodplain Lake Erie shoreline historic district permit construction",
    ],
    "MI": [
        "Michigan LARA contractor license residential builder electrical plumbing mechanical maintenance and alteration",
        "Michigan Building Code Residential Code Stille-DeRossett-Hale energy code adoption",
        "Michigan critical dunes wetlands shoreline Part 31 floodplain permit construction",
    ],
    "NJ": [
        "New Jersey Uniform Construction Code building subcode HIC home improvement contractor registration",
        "New Jersey DEP wetlands flood hazard waterfront development residential construction permit",
        "New Jersey Pinelands historic preservation coastal area facility CAFRA permit",
    ],
    "VA": [
        "Virginia DPOR contractor license Class A B C residential building electrical plumbing tradesman",
        "Virginia Uniform Statewide Building Code USBC residential energy code amendments",
        "Virginia Chesapeake Bay Preservation Act erosion sediment control permit construction",
    ],
    "TN": [
        "Tennessee Department of Commerce Insurance contractor license HVAC electrical plumbing residential limited",
        "Tennessee Building Construction Safety Act state codes adoption local amendment",
        "Tennessee TDEC ARAP floodplain wetland water quality permit residential construction",
    ],
    "MA": [
        "Massachusetts Construction Supervisor License CSL HIC home improvement contractor registration",
        "Massachusetts State Building Code 780 CMR stretch energy code IECC residential",
        "Massachusetts Wetlands Protection Act Conservation Commission Order of Conditions permit",
    ],
    "IN": [
        "Indiana plumbing electrical HVAC contractor license local certification residential builder",
        "Indiana Residential Code 675 IAC building code energy code amendments",
        "Indiana floodplain wetland DNR Department of Natural Resources permit construction",
    ],
    "MD": [
        "Maryland Home Improvement Commission MHIC license general contractor electrical plumbing residential",
        "Maryland Building Performance Standards IBC IRC IECC energy code adoption",
        "Maryland Critical Area Chesapeake Bay Conservation Act stormwater permit construction",
    ],
    "MO": [
        "Missouri local contractor licensing electrical plumbing HVAC residential no statewide license",
        "Missouri local building code adoption residential energy code municipal variation",
        "Missouri floodplain levee historic preservation permit construction municipal",
    ],
    "WI": [
        "Wisconsin Department of Safety Professional Services dwelling contractor electrical plumbing HVAC license",
        "Wisconsin Uniform Dwelling Code UDC residential building energy code adoption",
        "Wisconsin shoreland zoning floodplain wetland navigable waters permit",
    ],
    "MN": [
        "Minnesota Department of Labor Industry residential contractor license electrical plumbing HVAC",
        "Minnesota State Building Code Residential Code 1309 energy code adoption",
        "Minnesota DNR shoreland floodplain wetland conservation permit construction",
    ],
    "SC": [
        "South Carolina LLR Contractor Licensing Board residential builder mechanical electrical plumbing",
        "South Carolina Modular Buildings Construction Standards Act IRC IBC IECC adoption",
        "South Carolina coastal CRC OCRM critical area floodplain permit residential",
    ],
    "AL": [
        "Alabama Home Builders Licensure Board AHBLB residential contractor electrical HVAC plumbing",
        "Alabama Energy and Residential Codes Board IRC IECC adoption local",
        "Alabama coastal floodplain wetland ADEM Mobile Bay permit construction",
    ],
    "LA": [
        "Louisiana State Licensing Board for Contractors residential mold electrical mechanical plumbing",
        "Louisiana State Uniform Construction Code LSUCC IRC IECC adoption parish",
        "Louisiana CPRA coastal use permit floodplain wetland construction parish",
    ],
    "KY": [
        "Kentucky HBC Board of Housing Buildings Construction electrical plumbing HVAC contractor license",
        "Kentucky Residential Code KRC building code energy code amendments",
        "Kentucky DOW floodplain construction permit historic district residential",
    ],
    "OR": [
        "Oregon CCB Construction Contractors Board residential general contractor BCD electrical plumbing",
        "Oregon Residential Specialty Code ORSC OEESC energy code adoption",
        "Oregon DSL DEQ wetland floodplain coastal zone Goal 18 permit construction",
    ],
    "OK": [
        "Oklahoma CIB Construction Industries Board electrical plumbing mechanical residential builder",
        "Oklahoma Uniform Building Code Commission IRC IECC energy code adoption",
        "Oklahoma OWRB floodplain construction tribal jurisdiction permit residential",
    ],
    "CT": [
        "Connecticut DCP Department of Consumer Protection HIC home improvement contractor major contractor",
        "Connecticut State Building Code IRC IBC IECC stretch energy code adoption",
        "Connecticut DEEP inland wetlands flood management coastal permit construction",
    ],
    "UT": [
        "Utah DOPL Division of Occupational Professional Licensing contractor electrical plumbing mechanical",
        "Utah State Construction Code IRC IBC IECC energy code adoption local amendments",
        "Utah DWR floodplain critical lands wildland urban interface permit construction",
    ],
    "IA": [
        "Iowa contractor registration Workforce Development electrical plumbing HVAC license",
        "Iowa State Building Code residential energy code adoption local jurisdiction",
        "Iowa DNR floodplain sovereign land wetland permit construction municipal",
    ],
    "NV": [
        "Nevada State Contractors Board NSCB residential A B C license classifications",
        "Nevada IECC energy code Southern Nevada adoption Clark County jurisdiction",
        "Nevada Division of Water Resources floodplain BLM federal lands permit construction",
    ],
    "AR": [
        "Arkansas Contractors Licensing Board residential builder HVACR electrical plumbing license",
        "Arkansas Fire Prevention Code IBC IRC IECC adoption local amendments",
        "Arkansas Game Fish Commission ADEQ floodplain wetland permit construction",
    ],
    "KS": [
        "Kansas no statewide general contractor license local plumbing electrical HVAC certification",
        "Kansas IRC IECC adoption local jurisdiction energy code amendments",
        "Kansas KDHE floodplain wetland Department of Agriculture water permit construction",
    ],
    "MS": [
        "Mississippi State Board of Contractors residential builder electrical plumbing HVAC remodeler",
        "Mississippi Building Code Council IRC IBC IECC adoption local jurisdiction",
        "Mississippi MDEQ Coastal Program wetland floodplain permit construction",
    ],
    "NM": [
        "New Mexico CID Construction Industries Division GB GA EE ME MM license residential",
        "New Mexico Residential Building Code Energy Conservation Code adoption",
        "New Mexico OSE acequia traditional water rights floodplain pueblo permit construction",
    ],
    "NE": [
        "Nebraska Contractor Registration Department of Labor electrical plumbing HVAC license",
        "Nebraska State Energy Code IECC adoption local building code jurisdiction",
        "Nebraska DNR floodplain Game Parks historic preservation permit construction",
    ],
    "ID": [
        "Idaho DBS Division of Building Safety contractor electrical plumbing HVAC license",
        "Idaho Residential Code IRC IECC adoption local jurisdiction amendments",
        "Idaho IDWR floodplain wetland Department of Lands permit construction",
    ],
    "WV": [
        "West Virginia Contractor Licensing Board electrical plumbing HVAC residential general contractor",
        "West Virginia State Building Code IRC IBC IECC adoption local jurisdiction",
        "West Virginia DEP floodplain National Coal Heritage permit construction",
    ],
    "HI": [
        "Hawaii DCCA Contractor Licensing Board RME C residential contractor electrical plumbing",
        "Hawaii State Building Code IBC IRC IECC adoption county Honolulu Maui Kauai",
        "Hawaii DLNR Special Management Area shoreline conservation district permit construction",
    ],
    "NH": [
        "New Hampshire no statewide general contractor license local electrical plumbing license",
        "New Hampshire State Building Code IRC IBC IECC adoption local amendments",
        "New Hampshire DES wetlands shoreland alteration of terrain permit construction",
    ],
    "ME": [
        "Maine no statewide general contractor license local electrical plumbing HVAC trades",
        "Maine Uniform Building Energy Code MUBEC IRC IECC adoption municipal",
        "Maine DEP NRPA wetlands shoreland zoning permit construction municipal",
    ],
    "RI": [
        "Rhode Island Contractors Registration Licensing Board CRLB electrical plumbing HVAC builder",
        "Rhode Island State Building Code IRC IBC IECC adoption local jurisdiction",
        "Rhode Island CRMC coastal zone DEM wetlands permit construction",
    ],
    "MT": [
        "Montana DLI Department of Labor Industry contractor registration electrical plumbing HVAC",
        "Montana State Building Code IRC IBC IECC adoption local jurisdiction amendments",
        "Montana DNRC floodplain stream protection 310 permit construction",
    ],
    "DE": [
        "Delaware contractor business license electrical plumbing HVAC trade license county",
        "Delaware State Building Code IRC IBC IECC adoption Sussex Kent New Castle",
        "Delaware DNREC wetlands floodplain coastal zone permit construction",
    ],
    "SD": [
        "South Dakota no statewide general contractor license local electrical plumbing HVAC",
        "South Dakota State Building Code IRC IECC adoption local jurisdiction amendments",
        "South Dakota DENR floodplain Game Fish Parks permit construction",
    ],
    "ND": [
        "North Dakota Secretary of State contractor license electrical plumbing HVAC",
        "North Dakota State Building Code IRC IECC adoption local jurisdiction amendments",
        "North Dakota DWR floodplain Game Fish Parks permit construction",
    ],
    "AK": [
        "Alaska DCCED Construction Contractor Endorsement electrical plumbing license",
        "Alaska Building Code Council IRC IECC adoption Anchorage Fairbanks Juneau borough",
        "Alaska DNR coastal zone wetlands ADF&G habitat permit construction",
    ],
    "VT": [
        "Vermont Office of Professional Regulation electrical plumbing trade license no GC license",
        "Vermont Residential Building Energy Code RBES IRC adoption municipal",
        "Vermont ANR Act 250 wetlands river corridor permit construction",
    ],
    "WY": [
        "Wyoming no statewide general contractor license local electrical plumbing HVAC",
        "Wyoming State Building Code IRC IECC adoption local jurisdiction amendments",
        "Wyoming WEQC floodplain wetland Game Fish permit construction",
    ],
}


@dataclass
class SearchResult:
    query: str
    title: str
    link: str
    snippet: str


@dataclass
class StateSummary:
    state: str
    path: str
    rule_count: int
    sources_count: int
    seconds: float
    credits: int
    valid: bool
    claude_calls: int = 0
    error: str = ""


def load_dotenv(path: Path) -> None:
    """Small .env loader to avoid adding python-dotenv just for this script."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def ca_template() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    from api.state_packs import STATE_PACKS  # type: ignore

    return STATE_PACKS["CA"]


def ca_schema_keys() -> tuple[set[str], set[str]]:
    ca = ca_template()
    note = ca["expert_notes"][0]
    return set(ca.keys()), set(note.keys())


def queries_for_state(state: str, limit: int = 8) -> list[str]:
    name = STATE_NAMES[state]
    queries: list[str] = []
    seen: set[str] = set()
    for q in STATE_QUERY_HINTS.get(state, []):
        if q not in seen:
            queries.append(q)
            seen.add(q)
    for tmpl in QUERY_TEMPLATES:
        q = tmpl.format(name=name)
        if q not in seen:
            queries.append(q)
            seen.add(q)
    return queries[:limit]


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None, *, timeout: int = 30) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc


def serper_search(query: str, api_key: str, *, timeout: int = 12) -> list[SearchResult]:
    data = post_json(
        "https://google.serper.dev/search",
        {"q": query, "num": 5},
        {"X-API-KEY": api_key},
        timeout=timeout,
    )
    results: list[SearchResult] = []
    for item in data.get("organic", [])[:5]:
        link = str(item.get("link") or "").strip()
        if not link.startswith("http"):
            continue
        results.append(
            SearchResult(
                query=query,
                title=str(item.get("title") or "").strip(),
                link=link,
                snippet=str(item.get("snippet") or "").strip(),
            )
        )
    return results


def collect_sources(state: str, api_key: str, *, query_limit: int = 8) -> tuple[list[SearchResult], int]:
    queries = queries_for_state(state, limit=query_limit)
    if len(queries) > MAX_SERPER_CREDITS_PER_STATE:
        raise RuntimeError(f"{state}: query plan exceeds per-state cap")
    results: list[SearchResult] = []
    with futures.ThreadPoolExecutor(max_workers=min(6, len(queries))) as pool:
        future_map = {pool.submit(serper_search, q, api_key): q for q in queries}
        for fut in futures.as_completed(future_map):
            results.extend(fut.result())
    deduped: list[SearchResult] = []
    seen_links: set[str] = set()
    for item in results:
        key = item.link.rstrip("/")
        if key in seen_links:
            continue
        deduped.append(item)
        seen_links.add(key)
    return deduped[:35], len(queries)


def compact_sources(results: list[SearchResult]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(
            f"[{i}] query={r.query}\n"
            f"title={r.title}\n"
            f"url={r.link}\n"
            f"snippet={r.snippet}"
        )
    return "\n\n".join(lines)


def build_prompt(state: str, results: list[SearchResult]) -> str:
    name = STATE_NAMES[state]
    ca = json.dumps(ca_template(), indent=2)
    source_block = compact_sources(results)
    return f"""
You are generating a state expert pack for {state} ({name}).

Match this California template JSON schema exactly (same top-level keys and expert_notes item keys):
{ca}

Web research from Serper for {state} ({name}); cite only these URLs:
{source_block}

Output ONLY valid JSON matching the California schema.
Requirements:
- Top-level keys must be exactly: name, expert_notes.
- expert_notes must contain {MIN_RULES}-{MAX_RULES} rules.
- Each rule keys must be exactly: title, note, applies_to, source.
- source must be one exact URL from the Serper results above.
- Use real {name}-specific content only; no generic templates.
- Prioritize contractor-facing permit gotchas: trade licensing, code adoption/amendments, permit timing/vesting, AHJ split, overlays, utility coordination, disclosure/notice requirements, and common mistakes.
- Notes must be factual, concise, and grounded by the cited Serper title/snippet/URL.
- Do not mention Serper, Claude, California, or this prompt in the output.
""".strip()


def extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    if not stripped.startswith("{"):
        match = re.search(r"\{.*\}", stripped, flags=re.S)
        if not match:
            raise ValueError("Claude response did not contain a JSON object")
        stripped = match.group(0)
    return json.loads(stripped)


def parse_claude_output(stdout: str) -> dict[str, Any]:
    """Parse Claude Code --print --output-format json output into the requested pack."""
    text = stdout.strip()
    if not text:
        raise RuntimeError("Claude CLI produced no stdout")
    try:
        outer = json.loads(text)
    except json.JSONDecodeError:
        return extract_json(text)

    if isinstance(outer, dict) and set(outer.keys()) >= {"name", "expert_notes"}:
        return outer
    if isinstance(outer, dict):
        if outer.get("is_error"):
            raise RuntimeError(f"Claude CLI returned an error result: {outer}")
        for key in ("result", "message", "text", "content", "output"):
            value = outer.get(key)
            if isinstance(value, str) and value.strip():
                return extract_json(value)
            if isinstance(value, dict) and set(value.keys()) >= {"name", "expert_notes"}:
                return value
            if isinstance(value, list):
                parts = []
                for part in value:
                    if isinstance(part, str):
                        parts.append(part)
                    elif isinstance(part, dict):
                        part_text = part.get("text") or part.get("content")
                        if isinstance(part_text, str):
                            parts.append(part_text)
                if parts:
                    return extract_json("\n".join(parts))
    return extract_json(text)


def generate_with_claude(state: str, results: list[SearchResult]) -> tuple[dict[str, Any], list[str]]:
    prompt = build_prompt(state, results)
    cmd = [
        "claude",
        "--print",
        "--output-format",
        "json",
        "--no-session-persistence",
        "--tools",
        "",
    ]
    completed = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=ROOT,
        timeout=CLAUDE_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Claude CLI failed with exit {completed.returncode}; stderr={completed.stderr[-1200:]} stdout={completed.stdout[-1200:]}"
        )
    return parse_claude_output(completed.stdout), cmd


def normalize_source(value: Any, source_urls: set[str]) -> str:
    text = " ".join(str(x) for x in value) if isinstance(value, list) else str(value or "")
    urls = re.findall(r"https?://[^\s,;\])}>'\"]+", text)
    source_urls_by_stripped = {u.rstrip("/"): u for u in source_urls}
    for url in urls:
        candidate = url.rstrip("/.,")
        if candidate in source_urls:
            return candidate
        if candidate.rstrip("/") in source_urls_by_stripped:
            return source_urls_by_stripped[candidate.rstrip("/")]
    clean = text.strip().rstrip("/")
    if clean in source_urls_by_stripped:
        return source_urls_by_stripped[clean]
    return ""


def clean_pack(state: str, pack: dict[str, Any], results: list[SearchResult]) -> dict[str, Any]:
    source_urls = {r.link for r in results}
    cleaned_notes: list[dict[str, str]] = []
    for raw_note in pack.get("expert_notes", []):
        if not isinstance(raw_note, dict):
            continue
        note = {
            "title": str(raw_note.get("title") or "").strip(),
            "note": str(raw_note.get("note") or raw_note.get("body") or "").strip(),
            "applies_to": str(raw_note.get("applies_to") or raw_note.get("category") or "").strip(),
            "source": normalize_source(raw_note.get("source") or raw_note.get("sources") or "", source_urls),
        }
        if all(note.values()):
            cleaned_notes.append(note)
    return {"name": f"{STATE_NAMES[state]} expert pack", "expert_notes": cleaned_notes}


def validate_pack(pack: dict[str, Any], results: list[SearchResult]) -> None:
    allowed_top, allowed_note = ca_schema_keys()
    if set(pack.keys()) != allowed_top:
        raise ValueError(f"top-level keys {set(pack.keys())} do not match CA schema {allowed_top}")
    notes = pack.get("expert_notes")
    if not isinstance(notes, list):
        raise ValueError("expert_notes must be a list")
    if not MIN_RULES <= len(notes) <= MAX_RULES:
        raise ValueError(f"expected {MIN_RULES}-{MAX_RULES} expert_notes, got {len(notes)}")
    source_urls = {r.link.rstrip("/") for r in results}
    for idx, note in enumerate(notes, 1):
        if not isinstance(note, dict):
            raise ValueError(f"note {idx} is not an object")
        if set(note.keys()) != allowed_note:
            raise ValueError(f"note {idx} keys {set(note.keys())} do not match CA schema {allowed_note}")
        for key in allowed_note:
            if not str(note.get(key) or "").strip():
                raise ValueError(f"note {idx} missing {key}")
        source = str(note.get("source", "")).rstrip("/")
        if not source.startswith("http") or source not in source_urls:
            raise ValueError(f"note {idx} source is not a Serper result URL: {note.get('source')}")


def generate_state(state: str, *, review_mode: str = "draft", query_limit: int = 8) -> StateSummary:
    start = time.monotonic()
    state = state.upper()
    path = DRAFT_DIR / f"{state.lower()}.json"
    credits = 0
    claude_calls = 0
    try:
        if state not in STATE_NAMES:
            raise ValueError(f"unsupported state {state}; expected one of {', '.join(TOP9)}")
        if review_mode != "draft":
            raise ValueError("only --review-mode draft is supported; live promotion is intentionally disabled")
        api_key = os.environ.get("SERPER_API_KEY")
        if not api_key:
            raise RuntimeError("SERPER_API_KEY is not set")
        results, credits = collect_sources(state, api_key, query_limit=query_limit)
        if credits > MAX_SERPER_CREDITS_PER_STATE:
            raise RuntimeError(f"{state}: exceeded Serper credit cap ({credits})")
        if len(results) < 5:
            raise RuntimeError(f"{state}: too few Serper results ({len(results)})")
        claude_calls = 1
        raw_pack, _cmd = generate_with_claude(state, results)
        pack = clean_pack(state, raw_pack, results)
        validate_pack(pack, results)
        DRAFT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(pack, indent=2, ensure_ascii=False) + "\n")
        seconds = time.monotonic() - start
        return StateSummary(
            state=state,
            path=str(path),
            rule_count=len(pack["expert_notes"]),
            sources_count=len({n["source"] for n in pack["expert_notes"]}),
            seconds=seconds,
            credits=credits,
            valid=True,
            claude_calls=claude_calls,
        )
    except Exception as exc:
        return StateSummary(
            state=state,
            path=str(path),
            rule_count=0,
            sources_count=0,
            seconds=time.monotonic() - start,
            credits=credits,
            valid=False,
            claude_calls=claude_calls,
            error=str(exc),
        )


def review_existing(states: list[str]) -> list[StateSummary]:
    summaries: list[StateSummary] = []
    for state in states:
        started = time.monotonic()
        path = DRAFT_DIR / f"{state.lower()}.json"
        try:
            data = json.loads(path.read_text())
            allowed_top, allowed_note = ca_schema_keys()
            if set(data.keys()) != allowed_top:
                raise ValueError("top-level schema mismatch")
            notes = data.get("expert_notes", [])
            if not MIN_RULES <= len(notes) <= MAX_RULES:
                raise ValueError(f"expected {MIN_RULES}-{MAX_RULES} expert_notes, got {len(notes)}")
            for idx, note in enumerate(notes, 1):
                if set(note.keys()) != allowed_note:
                    raise ValueError(f"note {idx} schema mismatch")
                if not str(note.get("source", "")).startswith("http"):
                    raise ValueError(f"note {idx} source is not a URL")
            summaries.append(
                StateSummary(
                    state=state,
                    path=str(path),
                    rule_count=len(notes),
                    sources_count=len({n.get("source") for n in notes}),
                    seconds=time.monotonic() - started,
                    credits=0,
                    valid=True,
                )
            )
        except Exception as exc:
            summaries.append(StateSummary(state, str(path), 0, 0, time.monotonic() - started, 0, False, 0, str(exc)))
    return summaries


def sample_rule(state: str) -> dict[str, Any]:
    path = DRAFT_DIR / f"{state.lower()}.json"
    data = json.loads(path.read_text())
    notes = data["expert_notes"]
    keyword_preferences = {
        "TX": ("Air Conditioning", "HVAC", "TDLR", "TACL", "TSBPE"),
        "FL": ("hurricane", "HVHZ", "Florida Building Code", "DBPR", "Notice of Commencement"),
    }
    for keyword in keyword_preferences.get(state.upper(), ()): 
        needle = keyword.lower()
        for note in notes:
            if needle in json.dumps(note).lower():
                return note
    return notes[0]


def print_table(summaries: list[StateSummary]) -> None:
    print("\nReview table")
    print("state | rule count | sources count | gen time | credits | claude calls | status")
    print("--- | ---: | ---: | ---: | ---: | ---: | ---")
    for s in summaries:
        status = "ok" if s.valid else f"ERROR: {s.error}"
        print(f"{s.state} | {s.rule_count} | {s.sources_count} | {s.seconds:.1f}s | {s.credits} | {s.claude_calls} | {status}")
    total_credits = sum(s.credits for s in summaries)
    total_claude_calls = sum(s.claude_calls for s in summaries)
    total_time = sum(s.seconds for s in summaries)
    all_valid = all(s.valid for s in summaries)
    print(f"\nGenerator script path: {Path(__file__).resolve()}")
    print("Draft JSON paths:")
    for s in summaries:
        print(f"- {s.path}")
    if all_valid and (DRAFT_DIR / "tx.json").exists() and (DRAFT_DIR / "fl.json").exists():
        print("\nSample TX rule:")
        print(json.dumps(sample_rule("TX"), indent=2, ensure_ascii=False))
        print("\nSample FL rule:")
        print(json.dumps(sample_rule("FL"), indent=2, ensure_ascii=False))
    print(f"\nClaude CLI invocation count: {total_claude_calls}")
    print(f"Serper credits used: {total_credits}")
    print(f"Total generation wall-clock summed time: {total_time:.1f}s")
    if all_valid and total_claude_calls >= len(summaries) and total_credits <= MAX_TOTAL_SERPER_CREDITS:
        print("Self-assessment: drafts ready for Opus review")
    else:
        print("Self-assessment: drafts NOT ready; see errors above")


def ensure_claude_logged_in() -> None:
    try:
        completed = subprocess.run(
            ["claude", "auth", "status"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Claude CLI is not installed or not on PATH") from exc
    detail = (completed.stdout + completed.stderr).strip() or f"exit {completed.returncode}"
    if completed.returncode != 0:
        raise RuntimeError(f"Claude CLI auth status failed ({detail})")
    try:
        status = json.loads(completed.stdout)
    except json.JSONDecodeError:
        status = {}
    if status.get("loggedIn") is not True:
        raise RuntimeError(f"Claude CLI is not logged in ({detail})")
    if status.get("subscriptionType") != "max":
        raise RuntimeError(f"Claude CLI auth is not a Max subscription ({detail})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate PermitAssist draft state expert packs")
    parser.add_argument("--state", action="append", help="Two-letter state; may be repeated")
    parser.add_argument("--all-top9", action="store_true", help="Generate TX, FL, NY, IL, GA, AZ, NC, WA, CO")
    parser.add_argument("--review-mode", default="draft", choices=["draft"], help="Only draft mode is supported")
    parser.add_argument("--validate-only", action="store_true", help="Validate existing drafts without API calls")
    parser.add_argument("--workers", type=int, default=1, help="Parallel states to generate; default serializes Claude CLI calls")
    parser.add_argument("--query-limit", type=int, default=8, help="Serper queries per state, max 10")
    return parser.parse_args()


def main() -> int:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    states = TOP9 if args.all_top9 else [s.upper() for s in (args.state or [])]
    if not states:
        raise SystemExit("Provide --state TX or --all-top9")
    unknown = [s for s in states if s not in STATE_NAMES]
    if unknown:
        raise SystemExit(f"Unsupported states: {', '.join(unknown)}")

    if args.validate_only:
        summaries = review_existing(states)
    else:
        try:
            ensure_claude_logged_in()
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        if not 5 <= args.query_limit <= MAX_SERPER_CREDITS_PER_STATE:
            raise SystemExit(f"--query-limit must be 5..{MAX_SERPER_CREDITS_PER_STATE}")
        max_possible = len(states) * args.query_limit
        if max_possible > MAX_TOTAL_SERPER_CREDITS:
            raise SystemExit(f"Refusing run: possible Serper credits {max_possible} exceeds {MAX_TOTAL_SERPER_CREDITS}")
        # Keep Claude calls serial by default. If --workers is raised manually, every
        # state still uses exactly one Claude CLI subprocess and its own Serper budget.
        with futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            summaries = list(
                pool.map(
                    lambda st: generate_state(
                        st,
                        review_mode=args.review_mode,
                        query_limit=args.query_limit,
                    ),
                    states,
                )
            )

    print_table(summaries)
    if sum(s.credits for s in summaries) > MAX_TOTAL_SERPER_CREDITS:
        print("ERROR: total Serper credits exceeded cap", file=sys.stderr)
        return 2
    if any(not s.valid for s in summaries):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
