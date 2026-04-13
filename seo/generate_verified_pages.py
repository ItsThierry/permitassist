#!/usr/bin/env python3
"""
PermitAssist - Generate SEO pages from verified_cities.json
Fills in missing city×trade pages (especially "general" trade + newer cities)
and updates sitemap.xml in frontend/

Run: python3 generate_verified_pages.py
"""

import json
import os
import re
import html as html_module
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent.parent
VERIFIED_JSON = BASE / "data" / "verified_cities.json"
SEO_PAGES_DIR = Path(__file__).parent / "seo_pages"
OUT_DIR = SEO_PAGES_DIR / "permits"
FRONTEND_DIR = BASE / "frontend"
SITEMAP_PATH = FRONTEND_DIR / "sitemap.xml"

# ── Config ────────────────────────────────────────────────────────────────────
SITE_URL = "https://permitassist.io"
TODAY = datetime.now().strftime("%Y-%m-%d")
YEAR = datetime.now().year

TRADE_META = {
    "hvac": {"display": "HVAC", "icon": "🌡️", "permit_type": "Mechanical Permit",
              "desc": "heating, ventilation & air conditioning"},
    "electrical": {"display": "Electrical", "icon": "⚡", "permit_type": "Electrical Permit",
                   "desc": "electrical wiring & panel work"},
    "roofing": {"display": "Roofing", "icon": "🏠", "permit_type": "Building Permit",
                "desc": "roof replacement & repair"},
    "plumbing": {"display": "Plumbing", "icon": "🔧", "permit_type": "Plumbing Permit",
                 "desc": "plumbing & water heater work"},
    "general": {"display": "General Contractor", "icon": "🏗️", "permit_type": "Building Permit",
                "desc": "general construction & remodeling"},
}

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}


def esc(text: str) -> str:
    return html_module.escape(str(text), quote=True)


def city_slug(city: str, state: str) -> str:
    city_slug = re.sub(r"[^a-z0-9-]", "", city.lower().replace(" ", "-").replace("_", "-"))
    return f"{city_slug}-{state.lower()}"


def clean_summary(raw: str, city: str, state: str, trade: str) -> tuple[str, str]:
    """
    Extract clean 2-3 sentence description from raw scraped text.
    Returns (short_desc, detail_para)
    """
    if not raw:
        tm = TRADE_META.get(trade, TRADE_META["general"])
        short = (f"A {tm['display'].lower()} permit is required in {city}, {state} for most "
                 f"{tm['desc']} projects. Contact the local permit office to confirm requirements "
                 f"and fees for your specific project.")
        return short, ""

    # Clean up raw scraped text
    text = re.sub(r'\s+', ' ', raw).strip()
    # Remove common boilerplate artifacts
    text = re.sub(r'#\s+', '', text)
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'bit\.\s*ly/\S+', '', text)

    # Truncate to a reasonable length and split into sentences
    sentences = re.split(r'(?<=[.!?])\s+', text)
    good_sentences = []
    for s in sentences:
        s = s.strip()
        if len(s) < 20:
            continue
        # Skip sentences that look like raw addresses/headers
        if s.isupper():
            continue
        if re.match(r'^[A-Z0-9\s,\.]+$', s) and len(s) < 60:
            continue
        good_sentences.append(s)
        if len(good_sentences) >= 3:
            break

    if not good_sentences:
        tm = TRADE_META.get(trade, TRADE_META["general"])
        short = (f"A {tm['display'].lower()} permit is required in {city}, {state} for most "
                 f"{tm['desc']} projects. Contact the local permit office to confirm current requirements.")
        return short, ""

    short = good_sentences[0]
    if len(good_sentences) > 1:
        detail = " ".join(good_sentences[1:])
    else:
        detail = ""
    return short, detail


def build_page(entry: dict) -> str:
    """Generate HTML for one city×trade page using verified data."""
    city_name = entry["city"]
    state_abbr = entry["state"]
    trade = entry["trade"]
    data = entry.get("data", {})

    tm = TRADE_META.get(trade, TRADE_META["general"])
    state_name = STATE_NAMES.get(state_abbr, state_abbr)
    slug = city_slug(city_name, state_abbr)

    phone = esc(data.get("phone", ""))
    fee_range = esc(data.get("fee_range", ""))
    permit_office = esc(data.get("permit_office", f"{city_name} Building Department"))
    sources = data.get("sources", [])
    raw_summary = data.get("summary", "")

    canonical = f"{SITE_URL}/permits/{trade}/{slug}"
    title = f"{tm['display']} Permit Requirements in {city_name}, {state_abbr} | PermitAssist"
    meta_desc = (f"Do you need a {tm['display'].lower()} permit in {city_name}, {state_abbr}? "
                 f"Fees{', ' + fee_range if fee_range else ''}, requirements, and how to apply — "
                 f"verified {YEAR} data from official sources.")

    short_desc, detail_para = clean_summary(raw_summary, city_name, state_abbr, trade)

    # FAQ schema
    fee_answer = (f"Permit fees in {city_name} typically range {fee_range}." if fee_range
                  else f"Fees vary by project scope. Contact {permit_office} for the current fee schedule.")
    schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": f"Do I need a {tm['display'].lower()} permit in {city_name}, {state_abbr}?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": f"Yes — a {tm['permit_type'].lower()} is required in {city_name} for most {tm['desc']} projects. Contact {permit_office} to confirm requirements for your specific scope of work."
                }
            },
            {
                "@type": "Question",
                "name": f"How much does a {tm['display'].lower()} permit cost in {city_name}, {state_abbr}?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": fee_answer + " Always verify current fees before applying."
                }
            },
            {
                "@type": "Question",
                "name": f"Who do I contact for a {tm['display'].lower()} permit in {city_name}?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": f"Contact {permit_office}" + (f" at {phone}" if phone else "") + " for permit applications, requirements, and inspections."
                }
            },
        ]
    }

    schema_json = json.dumps(schema, ensure_ascii=False)

    # Sources HTML
    sources_html = ""
    if sources:
        source_links = "\n".join(
            f'          <li><a href="{esc(s)}" target="_blank" rel="noopener nofollow">{esc(s[:70])}{"..." if len(s) > 70 else ""}</a></li>'
            for s in sources[:3]
        )
        sources_html = f"""
    <div class="card">
      <h2>📎 Official Sources</h2>
      <p>This information was compiled from official government and verified third-party sources:</p>
      <ul>
{source_links}
      </ul>
      <p style="margin-top:12px; font-size:13px; color:var(--gray-400);">
        Always verify current requirements directly with {permit_office} before starting work.
      </p>
    </div>"""

    # Contact info card
    contact_rows = ""
    if permit_office:
        contact_rows += f"<tr><td><strong>Permit Office</strong></td><td>{permit_office}</td></tr>"
    if phone:
        contact_rows += f"<tr><td><strong>Phone</strong></td><td><a href='tel:{esc(phone)}'>{phone}</a></td></tr>"
    if fee_range:
        contact_rows += f"<tr><td><strong>Typical Fee Range</strong></td><td>{fee_range}</td></tr>"
    contact_rows += f"<tr><td><strong>Permit Type</strong></td><td>{tm['permit_type']}</td></tr>"
    contact_rows += f"<tr><td><strong>Data Verified</strong></td><td>{TODAY}</td></tr>"

    detail_html = f"<p>{esc(detail_para)}</p>" if detail_para else ""

    body = f"""
<div class="hero">
  <div class="container">
    <div class="breadcrumb">
      <a href="{SITE_URL}">PermitAssist</a>
      <span>›</span>
      <a href="{SITE_URL}/permits/">Permits</a>
      <span>›</span>
      <a href="{SITE_URL}/permits/{trade}/">{esc(tm['display'])}</a>
      <span>›</span>
      {esc(city_name)}, {esc(state_abbr)}
    </div>
    <h1>{tm['icon']} {esc(tm['display'])} Permit in {esc(city_name)}, {esc(state_abbr)}</h1>
    <p class="hero-sub">
      Requirements, fees, and contacts for {esc(city_name)} — verified {YEAR} data from official sources.
    </p>
    <a class="hero-cta" href="{SITE_URL}/#tool">Look Up Your Exact Permit Requirements →</a>
  </div>
</div>

<div class="content">
  <div class="container">

    <!-- Summary -->
    <div class="card">
      <h2>📋 {esc(tm['display'])} Permit Requirements — {esc(city_name)}, {esc(state_abbr)}</h2>
      <p>{esc(short_desc)}</p>
      {detail_html}
      <div class="info-box">
        💡 <strong>Need the exact permit for your project?</strong>
        Use our free AI tool to get a tailored permit checklist in under 5 seconds.
      </div>
    </div>

    <!-- Quick Facts -->
    <div class="card">
      <h2>⚡ Quick Reference</h2>
      <table class="fee-table">
        <thead><tr><th>Detail</th><th>Information</th></tr></thead>
        <tbody>
          {contact_rows}
        </tbody>
      </table>
    </div>

    <!-- What requires a permit -->
    <div class="card">
      <h2>🔍 What Requires a {esc(tm['display'])} Permit in {esc(city_name)}?</h2>
      <p>In {esc(city_name)}, {esc(state_name)}, a {esc(tm['permit_type'].lower())} is typically required for:</p>
      <ul>
        {_what_requires_list(trade, city_name)}
      </ul>
      <div class="warn-box">
        ⚠️ <strong>Always verify</strong> — permit requirements vary by project scope, zoning, and property type.
        When in doubt, call {permit_office + (" at " + phone if phone else "") }.
      </div>
    </div>

    <!-- CTA -->
    <div class="cta-block">
      <h2>Get Your {esc(city_name)} Permit Checklist Free</h2>
      <p>
        Enter your trade, zip code, and project scope — our AI returns exact permit requirements,
        fees, and contacts from {permit_office} in seconds.
      </p>
      <a href="{SITE_URL}/#tool">Look Up Permit Requirements for {esc(city_name)} →</a>
    </div>

    {sources_html}

    <!-- Related links -->
    <div class="card">
      <h2>🔗 Related Permit Guides</h2>
      <p><strong>Other trades in {esc(city_name)}:</strong></p>
      <div class="related-links">
        {_other_trade_links(trade, city_name, state_abbr)}
      </div>
      <p style="margin-top:16px;"><strong>Nearby cities in {esc(state_name)}:</strong></p>
      <p style="font-size:14px; color:var(--gray-600);">
        <a href="{SITE_URL}/permits/state/{esc(state_name.lower().replace(' ', '-'))}">
          Browse all {esc(tm['display'])} permit guides in {esc(state_name)} →
        </a>
      </p>
    </div>

  </div>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{esc(title)}</title>
  <meta name="description" content="{esc(meta_desc)}" />
  <meta name="robots" content="index, follow" />
  <link rel="canonical" href="{canonical}" />

  <meta property="og:title" content="{esc(title)}" />
  <meta property="og:description" content="{esc(meta_desc)}" />
  <meta property="og:url" content="{canonical}" />
  <meta property="og:type" content="website" />
  <meta property="og:site_name" content="PermitAssist" />
  <meta property="og:image" content="{SITE_URL}/logo.png" />

  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="{esc(title)}" />
  <meta name="twitter:description" content="{esc(meta_desc)}" />

  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet" />

  <script type="application/ld+json">{schema_json}</script>

  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --navy: #0f2044;
      --blue: #1a56db;
      --blue-light: #e8f0fe;
      --green: #0d9f6e;
      --green-light: #d1fae5;
      --yellow: #fbbf24;
      --yellow-light: #fffbeb;
      --gray-50: #f9fafb;
      --gray-100: #f3f4f6;
      --gray-200: #e5e7eb;
      --gray-400: #9ca3af;
      --gray-600: #4b5563;
      --gray-800: #1f2937;
      --radius: 10px;
      --shadow: 0 4px 24px rgba(15,32,68,0.10);
    }}
    body {{ font-family: 'Inter', system-ui, sans-serif; background: var(--gray-50); color: var(--gray-800); line-height: 1.6; }}
    a {{ color: var(--blue); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .nav {{ background: var(--navy); padding: 0 24px; display: flex; align-items: center; justify-content: space-between; height: 60px; position: sticky; top: 0; z-index: 100; }}
    .nav-logo {{ display: flex; align-items: center; gap: 10px; color: #fff; font-weight: 700; font-size: 18px; text-decoration: none; }}
    .nav-logo img {{ height: 32px; width: auto; }}
    .nav-cta {{ background: var(--blue); color: #fff !important; padding: 8px 18px; border-radius: 6px; font-size: 14px; font-weight: 600; text-decoration: none !important; }}
    .nav-cta:hover {{ background: #1648c0; }}
    .container {{ max-width: 900px; margin: 0 auto; padding: 0 20px; }}
    .hero {{ background: linear-gradient(135deg, var(--navy) 0%, #1a3a6e 100%); color: #fff; padding: 48px 24px 40px; }}
    .breadcrumb {{ font-size: 13px; color: rgba(255,255,255,0.6); margin-bottom: 16px; }}
    .breadcrumb a {{ color: rgba(255,255,255,0.7); }}
    .breadcrumb span {{ color: rgba(255,255,255,0.5); margin: 0 6px; }}
    h1 {{ font-size: clamp(22px, 4vw, 34px); font-weight: 800; line-height: 1.2; margin-bottom: 12px; }}
    .hero-sub {{ font-size: 16px; color: rgba(255,255,255,0.80); max-width: 640px; margin-bottom: 24px; }}
    .hero-cta {{ display: inline-block; background: var(--green); color: #fff; padding: 12px 28px; border-radius: 8px; font-weight: 700; font-size: 16px; }}
    .hero-cta:hover {{ background: #0b8a5e; text-decoration: none; }}
    .content {{ padding: 36px 0 60px; }}
    .card {{ background: #fff; border-radius: var(--radius); box-shadow: var(--shadow); padding: 28px; margin-bottom: 24px; }}
    .card h2 {{ font-size: 20px; font-weight: 700; color: var(--navy); margin-bottom: 16px; padding-bottom: 12px; border-bottom: 2px solid var(--gray-100); }}
    p {{ margin-bottom: 12px; font-size: 15px; color: var(--gray-600); }}
    ul {{ padding-left: 20px; margin-bottom: 12px; }}
    li {{ font-size: 15px; color: var(--gray-600); margin-bottom: 6px; }}
    .fee-table {{ width: 100%; border-collapse: collapse; font-size: 15px; }}
    .fee-table th {{ background: var(--navy); color: #fff; padding: 10px 14px; text-align: left; font-weight: 600; }}
    .fee-table td {{ padding: 10px 14px; border-bottom: 1px solid var(--gray-100); }}
    .fee-table tr:nth-child(even) td {{ background: var(--gray-50); }}
    .fee-table tr:last-child td {{ border-bottom: none; }}
    .info-box {{ border-left: 4px solid var(--blue); background: var(--blue-light); padding: 14px 16px; border-radius: 0 8px 8px 0; margin: 16px 0; font-size: 14px; color: var(--navy); }}
    .warn-box {{ border-left: 4px solid var(--yellow); background: var(--yellow-light); padding: 14px 16px; border-radius: 0 8px 8px 0; margin: 16px 0; font-size: 14px; color: #78350f; }}
    .cta-block {{ background: linear-gradient(135deg, var(--navy), #1a3a6e); border-radius: var(--radius); padding: 36px 28px; text-align: center; color: #fff; margin: 32px 0; }}
    .cta-block h2 {{ color: #fff; border: none; font-size: 22px; margin-bottom: 10px; padding: 0; }}
    .cta-block p {{ color: rgba(255,255,255,0.8); margin-bottom: 20px; }}
    .cta-block a {{ display: inline-block; background: var(--green); color: #fff; padding: 12px 28px; border-radius: 8px; font-weight: 700; font-size: 16px; }}
    .cta-block a:hover {{ background: #0b8a5e; text-decoration: none; }}
    .related-links {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }}
    .related-links a {{ padding: 6px 14px; background: var(--blue-light); border-radius: 20px; font-size: 13px; font-weight: 500; color: var(--blue); }}
    footer {{ background: var(--navy); color: rgba(255,255,255,0.6); text-align: center; padding: 28px 20px; font-size: 13px; }}
    footer a {{ color: rgba(255,255,255,0.7); }}
    .footer-links {{ display: flex; justify-content: center; gap: 20px; margin-bottom: 12px; flex-wrap: wrap; }}
    @media (max-width: 600px) {{
      .hero {{ padding: 32px 16px 28px; }}
      .card {{ padding: 20px 16px; }}
      .fee-table th, .fee-table td {{ padding: 8px 10px; font-size: 13px; }}
    }}
  </style>
</head>
<body>

<nav class="nav">
  <a class="nav-logo" href="{SITE_URL}">
    <img src="{SITE_URL}/logo.png" alt="PermitAssist Logo" onerror="this.style.display='none'" />
    PermitAssist
  </a>
  <a class="nav-cta" href="{SITE_URL}/#tool">Check a Permit Free →</a>
</nav>

{body}

<footer>
  <div class="footer-links">
    <a href="{SITE_URL}">Home</a>
    <a href="{SITE_URL}/permits/">All Permits</a>
    <a href="{SITE_URL}/permits/hvac/index.html">HVAC Guides</a>
    <a href="{SITE_URL}/permits/electrical/index.html">Electrical Guides</a>
    <a href="{SITE_URL}/permits/roofing/index.html">Roofing Guides</a>
    <a href="{SITE_URL}/permits/plumbing/index.html">Plumbing Guides</a>
  </div>
  <p>© {YEAR} PermitAssist · AI-powered permit research for contractors · Data updated {TODAY}</p>
  <p style="margin-top:6px; font-size:11px; opacity:0.5;">
    Information is for reference only. Always verify with your local AHJ before starting work.
  </p>
</footer>

</body>
</html>"""


def _what_requires_list(trade: str, city: str) -> str:
    items = {
        "hvac": [
            "New HVAC system installation",
            "Central AC or furnace replacement",
            "Ductwork modifications or new ductwork",
            "Mini-split or heat pump installation",
            "Commercial HVAC equipment",
        ],
        "electrical": [
            "Electrical panel upgrades or replacements",
            "New circuit installation",
            "Adding outlets or rewiring rooms",
            "Service entrance work",
            "EV charger installation",
        ],
        "roofing": [
            "Complete roof replacement (all or most shingles)",
            "Structural roof repairs",
            "Adding skylights or roof penetrations",
            "Re-roofing over existing materials",
            "Commercial roofing projects",
        ],
        "plumbing": [
            "Water heater replacement or new installation",
            "Rerouting supply or drain lines",
            "Adding new plumbing fixtures",
            "Sewer line repairs or replacements",
            "Irrigation system installation",
        ],
        "general": [
            "New construction or additions",
            "Interior remodels affecting structure or systems",
            "Garage conversions or ADU construction",
            "Deck or porch additions over 200 sq ft",
            "Commercial tenant improvements",
        ],
    }
    bullets = items.get(trade, items["general"])
    return "".join(f"<li>{b}</li>" for b in bullets)


def _other_trade_links(current_trade: str, city: str, state: str) -> str:
    slug = city_slug(city, state)
    links = []
    for trade, tm in TRADE_META.items():
        if trade == current_trade:
            continue
        links.append(
            f'<a href="{SITE_URL}/permits/{trade}/{slug}">'
            f'{tm["icon"]} {tm["display"]}</a>'
        )
    return "\n".join(links)


def build_permits_index(all_entries: dict) -> str:
    """Build a comprehensive index page listing all permit guides by state."""
    # Group by state → city → trades
    by_state: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for key, entry in all_entries.items():
        state = entry["state"]
        city = entry["city"]
        trade = entry["trade"]
        slug = city_slug(city, state)
        by_state[state][city].append((trade, slug))

    state_sections = ""
    total_pages = sum(len(cities) for cities in by_state.values())

    for state_abbr in sorted(by_state.keys()):
        state_full = STATE_NAMES.get(state_abbr, state_abbr)
        cities = by_state[state_abbr]
        city_rows = ""
        for city_name in sorted(cities.keys()):
            trades = sorted(cities[city_name], key=lambda x: x[0])
            trade_links = " ".join(
                f'<a href="{SITE_URL}/permits/{t}/{s}">{TRADE_META.get(t, {}).get("icon","🏗")} '
                f'{TRADE_META.get(t, {}).get("display", t.title())}</a>'
                for t, s in trades
            )
            city_rows += f"""
        <div class="city-row">
          <strong class="city-name">{esc(city_name)}</strong>
          <div class="trade-links">{trade_links}</div>
        </div>"""

        state_sections += f"""
    <div class="state-block">
      <h2 class="state-heading">
        <a href="{SITE_URL}/permits/state/{esc(state_full.lower().replace(' ','-'))}">
          {esc(state_full)}
        </a>
        <span class="state-abbr">{esc(state_abbr)}</span>
      </h2>
      <div class="city-grid">
        {city_rows}
      </div>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Building Permit Guides by City — HVAC, Electrical, Roofing & Plumbing | PermitAssist</title>
  <meta name="description" content="Browse verified permit requirement guides for HVAC, electrical, roofing, plumbing, and general contractor work by city and state. {total_pages}+ cities covered." />
  <meta name="robots" content="index, follow" />
  <link rel="canonical" href="{SITE_URL}/permits/" />

  <meta property="og:title" content="Building Permit Guides by City | PermitAssist" />
  <meta property="og:description" content="Permit requirements, fees, and contacts for {total_pages}+ city+trade combinations across the US." />
  <meta property="og:url" content="{SITE_URL}/permits/" />
  <meta property="og:type" content="website" />
  <meta property="og:site_name" content="PermitAssist" />
  <meta property="og:image" content="{SITE_URL}/logo.png" />

  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet" />

  <script type="application/ld+json">{{
    "@context": "https://schema.org",
    "@type": "WebSite",
    "name": "PermitAssist",
    "url": "{SITE_URL}",
    "description": "AI-powered permit research for contractors — {total_pages}+ verified city guides"
  }}</script>

  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --navy: #0f2044; --blue: #1a56db; --blue-light: #e8f0fe;
      --green: #0d9f6e; --gray-50: #f9fafb; --gray-100: #f3f4f6;
      --gray-200: #e5e7eb; --gray-400: #9ca3af; --gray-600: #4b5563;
      --gray-800: #1f2937; --radius: 10px; --shadow: 0 4px 24px rgba(15,32,68,0.10);
    }}
    body {{ font-family: 'Inter', system-ui, sans-serif; background: var(--gray-50); color: var(--gray-800); line-height: 1.6; }}
    a {{ color: var(--blue); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .nav {{ background: var(--navy); padding: 0 24px; display: flex; align-items: center; justify-content: space-between; height: 60px; position: sticky; top: 0; z-index: 100; }}
    .nav-logo {{ display: flex; align-items: center; gap: 10px; color: #fff; font-weight: 700; font-size: 18px; text-decoration: none; }}
    .nav-logo img {{ height: 32px; width: auto; }}
    .nav-cta {{ background: var(--blue); color: #fff !important; padding: 8px 18px; border-radius: 6px; font-size: 14px; font-weight: 600; text-decoration: none !important; }}
    .container {{ max-width: 1100px; margin: 0 auto; padding: 0 20px; }}
    .hero {{ background: linear-gradient(135deg, var(--navy) 0%, #1a3a6e 100%); color: #fff; padding: 48px 24px 40px; text-align: center; }}
    h1 {{ font-size: clamp(24px, 4vw, 38px); font-weight: 800; line-height: 1.2; margin-bottom: 12px; }}
    .hero-sub {{ font-size: 16px; color: rgba(255,255,255,0.80); max-width: 640px; margin: 0 auto 24px; }}
    .hero-cta {{ display: inline-block; background: var(--green); color: #fff; padding: 12px 28px; border-radius: 8px; font-weight: 700; font-size: 16px; }}
    .content {{ padding: 40px 0 60px; }}
    .state-block {{ background: #fff; border-radius: var(--radius); box-shadow: var(--shadow); padding: 24px; margin-bottom: 20px; }}
    .state-heading {{ font-size: 20px; font-weight: 700; color: var(--navy); margin-bottom: 16px; padding-bottom: 12px; border-bottom: 2px solid var(--gray-100); display: flex; align-items: center; gap: 10px; }}
    .state-abbr {{ background: var(--blue-light); color: var(--blue); padding: 2px 8px; border-radius: 4px; font-size: 13px; font-weight: 600; }}
    .city-grid {{ display: grid; gap: 10px; }}
    .city-row {{ display: flex; align-items: center; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--gray-100); flex-wrap: wrap; }}
    .city-row:last-child {{ border-bottom: none; }}
    .city-name {{ min-width: 160px; font-size: 14px; color: var(--gray-800); }}
    .trade-links {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .trade-links a {{ padding: 4px 12px; background: var(--blue-light); border-radius: 20px; font-size: 13px; color: var(--blue); font-weight: 500; }}
    .trade-links a:hover {{ background: var(--blue); color: #fff; text-decoration: none; }}
    footer {{ background: var(--navy); color: rgba(255,255,255,0.6); text-align: center; padding: 28px 20px; font-size: 13px; }}
    footer a {{ color: rgba(255,255,255,0.7); }}
    .footer-links {{ display: flex; justify-content: center; gap: 20px; margin-bottom: 12px; flex-wrap: wrap; }}
    @media (max-width: 600px) {{
      .city-row {{ flex-direction: column; align-items: flex-start; }}
      .city-name {{ min-width: unset; }}
    }}
  </style>
</head>
<body>

<nav class="nav">
  <a class="nav-logo" href="{SITE_URL}">
    <img src="{SITE_URL}/logo.png" alt="PermitAssist Logo" onerror="this.style.display='none'" />
    PermitAssist
  </a>
  <a class="nav-cta" href="{SITE_URL}/#tool">Check a Permit Free →</a>
</nav>

<div class="hero">
  <div class="container">
    <h1>🏗️ Building Permit Guides by City</h1>
    <p class="hero-sub">
      Verified permit requirements, fees, and contacts for HVAC, electrical, roofing, plumbing,
      and general contractor work across {len(by_state)} states.
    </p>
    <a class="hero-cta" href="{SITE_URL}/#tool">Look Up Permit Requirements Free →</a>
  </div>
</div>

<div class="content">
  <div class="container">
    {state_sections}
  </div>
</div>

<footer>
  <div class="footer-links">
    <a href="{SITE_URL}">Home</a>
    <a href="{SITE_URL}/permits/">All Permits</a>
    <a href="{SITE_URL}/#tool">Free Permit Tool</a>
  </div>
  <p>© {YEAR} PermitAssist · AI-powered permit research for contractors · Data updated {TODAY}</p>
  <p style="margin-top:6px; font-size:11px; opacity:0.5;">
    Information is for reference only. Always verify with your local AHJ before starting work.
  </p>
</footer>

</body>
</html>"""


def build_sitemap(all_entries: dict) -> str:
    """Build sitemap.xml for all verified city pages."""
    urls = [
        ("https://permitassist.io", "1.0", "monthly"),
        ("https://permitassist.io/permits/", "0.9", "monthly"),
    ]
    for key, entry in sorted(all_entries.items()):
        slug = city_slug(entry["city"], entry["state"])
        trade = entry["trade"]
        url = f"{SITE_URL}/permits/{trade}/{slug}"
        urls.append((url, "0.8", "monthly"))

    entries = "\n".join(
        f"""  <url>
    <loc>{u}</loc>
    <lastmod>{TODAY}</lastmod>
    <changefreq>{freq}</changefreq>
    <priority>{prio}</priority>
  </url>"""
        for u, prio, freq in urls
    )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{entries}
</urlset>"""


def main():
    print("Loading verified_cities.json...")
    with open(VERIFIED_JSON) as f:
        verified = json.load(f)

    print(f"  {len(verified)} entries found.")

    generated = 0
    skipped = 0

    for key, entry in verified.items():
        city_name = entry["city"]
        state_abbr = entry["state"]
        trade = entry["trade"]
        slug = city_slug(city_name, state_abbr)
        out_path = OUT_DIR / trade / slug / "index.html"

        if out_path.exists():
            skipped += 1
            continue

        out_path.parent.mkdir(parents=True, exist_ok=True)
        html = build_page(entry)
        out_path.write_text(html, encoding="utf-8")
        generated += 1
        print(f"  ✅ {trade}/{slug}")

    print(f"\nGenerated: {generated} new pages | Skipped (already exist): {skipped}")

    # ── Rebuild permits/index.html ────────────────────────────────────────────
    print("\nBuilding permits/index.html...")
    index_html = build_permits_index(verified)
    index_path = OUT_DIR / "index.html"
    index_path.write_text(index_html, encoding="utf-8")
    print(f"  ✅ {index_path}")

    # ── Write frontend/sitemap.xml ────────────────────────────────────────────
    print("\nWriting frontend/sitemap.xml...")
    FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
    sitemap = build_sitemap(verified)
    SITEMAP_PATH.write_text(sitemap, encoding="utf-8")
    print(f"  ✅ {SITEMAP_PATH} ({len(verified) + 2} URLs)")

    print("\n✅ All done!")


if __name__ == "__main__":
    main()
