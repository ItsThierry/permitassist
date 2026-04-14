#!/usr/bin/env python3
"""
PermitAssist SEO Page Generator
Generates 3 types of SEO pages from the knowledge base:
  1. City × Trade pages  (e.g. /permits/hvac/houston-tx)
  2. State hub pages     (e.g. /permits/state/texas)
  3. Trade guide pages   (e.g. /permits/guide/hvac)
  + sitemap.xml
  + robots.txt
  + seo_index.html (links page for internal linking)

Run: python3 generate_seo.py
Output: ./seo_pages/
"""

import json
import os
import re
from pathlib import Path
from datetime import datetime

# ── Paths ────────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent.parent
KB_DIR = BASE / "knowledge"
OUT_DIR = Path(__file__).parent / "seo_pages"

# ── Load Knowledge Base ───────────────────────────────────────────────────────
with open(KB_DIR / "cities.json") as f:
    _cities_raw = json.load(f)
CITIES = _cities_raw["cities"]

with open(KB_DIR / "trades.json") as f:
    _trades_raw = json.load(f)
TRADES = _trades_raw["trades"]

with open(KB_DIR / "states.json") as f:
    _states_raw = json.load(f)
STATES = _states_raw["states"]

# ── Config ────────────────────────────────────────────────────────────────────
SITE_URL = "https://permitassist.io"
SITE_NAME = "PermitAssist"
SITE_TAGLINE = "AI-Powered Permit Research for Contractors"
TOOL_URL = f"{SITE_URL}/#tool"
UPGRADE_URL = f"{SITE_URL}/#upgrade"
TODAY = datetime.now().strftime("%Y-%m-%d")

# Trade display names and slugs
TRADE_META = {
    "hvac": {
        "display": "HVAC",
        "full": "HVAC (Heating, Ventilation & Air Conditioning)",
        "keyword": "HVAC permit",
        "permit_type": "Mechanical Permit",
        "icon": "🌡️",
        "searches": "do I need a permit for HVAC",
    },
    "electrical": {
        "display": "Electrical",
        "full": "Electrical Panel & Wiring",
        "keyword": "electrical permit",
        "permit_type": "Electrical Permit",
        "icon": "⚡",
        "searches": "electrical permit requirements",
    },
    "roofing": {
        "display": "Roofing",
        "full": "Roof Replacement & Repair",
        "keyword": "roofing permit",
        "permit_type": "Building Permit",
        "icon": "🏠",
        "searches": "roofing permit requirements",
    },
    "plumbing": {
        "display": "Plumbing",
        "full": "Plumbing & Water Heater",
        "keyword": "plumbing permit",
        "permit_type": "Plumbing Permit",
        "icon": "🔧",
        "searches": "plumbing permit requirements",
    },
    "mini_split": {
        "display": "Mini Split",
        "full": "Mini Split / Ductless AC",
        "keyword": "mini split permit",
        "permit_type": "Mechanical Permit",
        "icon": "❄️",
        "searches": "do I need a permit for mini split",
    },
    "ev_charger": {
        "display": "EV Charger",
        "full": "EV Charger Installation",
        "keyword": "EV charger permit",
        "permit_type": "Electrical Permit",
        "icon": "🔌",
        "searches": "EV charger permit requirements",
    },
    "generator": {
        "display": "Generator",
        "full": "Standby Generator Installation",
        "keyword": "generator permit",
        "permit_type": "Electrical + Mechanical Permit",
        "icon": "⚙️",
        "searches": "do I need a permit for generator",
    },
    "deck": {
        "display": "Deck",
        "full": "Deck Addition & Construction",
        "keyword": "deck permit",
        "permit_type": "Building Permit",
        "icon": "🪵",
        "searches": "deck permit requirements",
    },
    "solar": {
        "display": "Solar",
        "full": "Solar Panel Installation",
        "keyword": "solar permit",
        "permit_type": "Electrical + Building Permit",
        "icon": "☀️",
        "searches": "solar panel permit requirements",
    },
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9-]", "", text.lower().replace(" ", "-").replace("_", "-"))


def state_name_from_abbr(abbr: str) -> str:
    """Find state full name from abbreviation."""
    for key, data in STATES.items():
        if key.upper() == abbr.upper() or data.get("name", "").upper() == abbr.upper():
            return data.get("name", abbr)
    return abbr


def get_fee_for_trade(city_data: dict, trade_key: str) -> str:
    """Extract city fee for a specific trade."""
    fees = city_data.get("fees", {})
    # Map trade key to likely fee key names
    mapping = {
        "hvac": ["hvac_mechanical", "hvac", "mechanical"],
        "electrical": ["electrical", "electric"],
        "roofing": ["roofing", "roof"],
        "plumbing": ["plumbing", "plumb"],
        "mini_split": ["hvac_mechanical", "hvac", "mechanical"],
        "ev_charger": ["ev_charger", "electrical"],
        "generator": ["electrical", "hvac_mechanical"],
        "deck": ["deck", "building"],
        "solar": ["solar", "electrical"],
    }
    for candidate in mapping.get(trade_key, []):
        for key, val in fees.items():
            if candidate in key.lower():
                return val
    # fallback
    return fees.get("fee_note", "Varies — use our tool for exact current fees")


def html_base(title: str, desc: str, canonical: str, body: str,
              og_type: str = "website", schema_json: str = "") -> str:
    """Shared HTML shell with full SEO head."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <meta name="description" content="{desc}" />
  <meta name="robots" content="index, follow" />
  <link rel="canonical" href="{canonical}" />

  <!-- Open Graph -->
  <meta property="og:title" content="{title}" />
  <meta property="og:description" content="{desc}" />
  <meta property="og:url" content="{canonical}" />
  <meta property="og:type" content="{og_type}" />
  <meta property="og:site_name" content="{SITE_NAME}" />
  <meta property="og:image" content="{SITE_URL}/logo.png" />

  <!-- Twitter Card -->
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="{title}" />
  <meta name="twitter:description" content="{desc}" />

  <!-- Preconnect -->
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet" />

  <!-- Organization schema (brand signal) -->
  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "Organization",
    "@id": "https://permitassist.io/#organization",
    "name": "PermitAssist",
    "url": "https://permitassist.io",
    "logo": {{ "@type": "ImageObject", "url": "https://permitassist.io/icons/icon-512.png", "width": 512, "height": 512 }},
    "description": "AI-powered permit research for contractors. Get exact permit requirements, fees, and contacts in 30 seconds."
  }}
  </script>
  {f'<script type="application/ld+json">{schema_json}</script>' if schema_json else ''}

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
      --red: #ef4444;
      --gray-50: #f9fafb;
      --gray-100: #f3f4f6;
      --gray-200: #e5e7eb;
      --gray-400: #9ca3af;
      --gray-600: #4b5563;
      --gray-800: #1f2937;
      --radius: 10px;
      --shadow: 0 4px 24px rgba(15,32,68,0.10);
    }}
    body {{
      font-family: 'Inter', system-ui, sans-serif;
      background: var(--gray-50);
      color: var(--gray-800);
      line-height: 1.6;
    }}
    a {{ color: var(--blue); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}

    /* ── Nav ── */
    .nav {{
      background: var(--navy);
      padding: 0 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      height: 60px;
      position: sticky;
      top: 0;
      z-index: 100;
    }}
    .nav-logo {{
      display: flex;
      align-items: center;
      gap: 10px;
      color: #fff;
      font-weight: 700;
      font-size: 18px;
      text-decoration: none;
    }}
    .nav-logo img {{ height: 32px; width: auto; }}
    .nav-cta {{
      background: var(--blue);
      color: #fff !important;
      padding: 8px 18px;
      border-radius: 6px;
      font-size: 14px;
      font-weight: 600;
      text-decoration: none !important;
    }}
    .nav-cta:hover {{ background: #1648c0; text-decoration: none !important; }}

    /* ── Layout ── */
    .container {{ max-width: 900px; margin: 0 auto; padding: 0 20px; }}
    .hero {{
      background: linear-gradient(135deg, var(--navy) 0%, #1a3a6e 100%);
      color: #fff;
      padding: 48px 24px 40px;
    }}
    .hero .container {{ max-width: 900px; margin: 0 auto; }}
    .breadcrumb {{
      font-size: 13px;
      color: rgba(255,255,255,0.6);
      margin-bottom: 16px;
    }}
    .breadcrumb a {{ color: rgba(255,255,255,0.7); }}
    .breadcrumb span {{ color: rgba(255,255,255,0.5); margin: 0 6px; }}
    h1 {{ font-size: clamp(24px, 4vw, 36px); font-weight: 800; line-height: 1.2; margin-bottom: 12px; }}
    .hero-sub {{ font-size: 16px; color: rgba(255,255,255,0.80); max-width: 640px; margin-bottom: 24px; }}
    .hero-cta {{
      display: inline-block;
      background: var(--green);
      color: #fff;
      padding: 12px 28px;
      border-radius: 8px;
      font-weight: 700;
      font-size: 16px;
    }}
    .hero-cta:hover {{ background: #0b8a5e; text-decoration: none; }}

    /* ── Content ── */
    .content {{ padding: 36px 0 60px; }}
    .card {{
      background: #fff;
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      padding: 28px;
      margin-bottom: 24px;
    }}
    .card h2 {{
      font-size: 20px;
      font-weight: 700;
      color: var(--navy);
      margin-bottom: 16px;
      padding-bottom: 12px;
      border-bottom: 2px solid var(--gray-100);
    }}
    .card h3 {{
      font-size: 16px;
      font-weight: 600;
      color: var(--gray-800);
      margin: 16px 0 8px;
    }}
    p {{ margin-bottom: 12px; font-size: 15px; color: var(--gray-600); }}
    ul {{ padding-left: 20px; margin-bottom: 12px; }}
    li {{ font-size: 15px; color: var(--gray-600); margin-bottom: 6px; }}

    /* ── Fee Table ── */
    .fee-table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 15px; }}
    .fee-table th {{
      background: var(--navy);
      color: #fff;
      padding: 10px 14px;
      text-align: left;
      font-weight: 600;
    }}
    .fee-table td {{ padding: 10px 14px; border-bottom: 1px solid var(--gray-100); }}
    .fee-table tr:nth-child(even) td {{ background: var(--gray-50); }}
    .fee-table tr:last-child td {{ border-bottom: none; }}

    /* ── Badges ── */
    .badge {{
      display: inline-block;
      padding: 3px 10px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 600;
    }}
    .badge-green {{ background: var(--green-light); color: var(--green); }}
    .badge-yellow {{ background: var(--yellow-light); color: #92400e; }}
    .badge-blue {{ background: var(--blue-light); color: var(--blue); }}

    /* ── Info boxes ── */
    .info-box {{
      border-left: 4px solid var(--blue);
      background: var(--blue-light);
      padding: 14px 16px;
      border-radius: 0 8px 8px 0;
      margin: 16px 0;
      font-size: 14px;
      color: var(--navy);
    }}
    .warn-box {{
      border-left: 4px solid var(--yellow);
      background: var(--yellow-light);
      padding: 14px 16px;
      border-radius: 0 8px 8px 0;
      margin: 16px 0;
      font-size: 14px;
      color: #78350f;
    }}

    /* ── CTA Block ── */
    .cta-block {{
      background: linear-gradient(135deg, var(--navy), #1a3a6e);
      border-radius: var(--radius);
      padding: 36px 28px;
      text-align: center;
      color: #fff;
      margin: 32px 0;
    }}
    .cta-block h2 {{ color: #fff; border: none; font-size: 22px; margin-bottom: 10px; }}
    .cta-block p {{ color: rgba(255,255,255,0.8); margin-bottom: 20px; }}
    .cta-block a {{
      display: inline-block;
      background: var(--green);
      color: #fff;
      padding: 12px 28px;
      border-radius: 8px;
      font-weight: 700;
      font-size: 16px;
    }}
    .cta-block a:hover {{ background: #0b8a5e; text-decoration: none; }}

    /* ── Grid ── */
    .grid-2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; }}
    .grid-3 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }}
    .mini-card {{
      background: #fff;
      border: 1px solid var(--gray-200);
      border-radius: 8px;
      padding: 16px;
      font-size: 14px;
    }}
    .mini-card strong {{ display: block; margin-bottom: 4px; color: var(--navy); }}

    /* ── Related links ── */
    .related-links {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }}
    .related-links a {{
      padding: 6px 14px;
      background: var(--blue-light);
      border-radius: 20px;
      font-size: 13px;
      font-weight: 500;
      color: var(--blue);
    }}

    /* ── Footer ── */
    footer {{
      background: var(--navy);
      color: rgba(255,255,255,0.6);
      text-align: center;
      padding: 28px 20px;
      font-size: 13px;
    }}
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
  <a class="nav-cta" href="{TOOL_URL}">Try PermitAssist Free →</a>
</nav>

{body}

<footer>
  <div class="footer-links">
    <a href="{SITE_URL}">PermitAssist Home</a>
    <a href="{SITE_URL}/permits/">All PermitAssist Permits</a>
    <a href="{SITE_URL}/pricing">PermitAssist Pricing</a>
    <a href="{SITE_URL}/cities">Verified Cities</a>
    <a href="{SITE_URL}/permits/guide/hvac">HVAC Permit Guide</a>
    <a href="{SITE_URL}/permits/guide/electrical">Electrical Permit Guide</a>
    <a href="{SITE_URL}/permits/guide/plumbing">Plumbing Permit Guide</a>
    <a href="{SITE_URL}/permits/guide/roofing">Roofing Permit Guide</a>
    <a href="{UPGRADE_URL}">PermitAssist Pro Plan</a>
  </div>
  <p><a href="{SITE_URL}" style="color:rgba(255,255,255,0.85);font-weight:600;">PermitAssist</a> · AI-powered permit research for contractors · © {datetime.now().year} · Data updated {TODAY}</p>
  <p style="margin-top:6px; font-size:11px; opacity:0.5;">
    Information is for reference only. Always verify with your local AHJ before starting work.
  </p>
</footer>

</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
# 1. CITY × TRADE PAGES
# ══════════════════════════════════════════════════════════════════════════════

def build_city_trade_page(city_key: str, trade_key: str) -> str:
    """Generate a city × trade SEO page."""
    city = CITIES[city_key]
    trade = TRADES[trade_key]
    tmeta = TRADE_META[trade_key]

    city_name = city["city"]
    state_abbr = city["state"]
    state_name = state_name_from_abbr(state_abbr)
    state_data = next(
        (v for k, v in STATES.items() if v.get("name", "").lower() == state_name.lower()),
        {}
    )

    fee_text = get_fee_for_trade(city, trade_key)
    timeline = city.get("timeline", {})
    timeline_str = timeline.get("over_the_counter", timeline.get("plan_review", "Varies by project type"))

    canonical = f"{SITE_URL}/permits/{slug(trade_key.replace('_','-'))}/{city_key.replace('_','-')}"
    title = f"{tmeta['display']} Permit {city_name}, {state_abbr} — Cost, Requirements & How to Apply ({TODAY[:4]})"
    desc = (
        f"Do you need a {tmeta['display'].lower()} permit in {city_name}, {state_abbr}? "
        f"Exact fees, permit requirements, timelines, and how to apply — verified {TODAY[:4]} data."
    )

    # City hours (used in comparison table)
    city_hours = city.get("hours", "Mon–Fri 8am–4pm (call to confirm)")

    # FAQ schema for Google rich results
    faqs = [
        {
            "q": f"Do I need a {tmeta['display'].lower()} permit in {city_name}, {state_abbr}?",
            "a": f"Yes, in most cases. {trade['permit_required']['rule']}. In {city_name}, contact {city.get('permit_office', 'the local permitting office')} at {city.get('permit_url', '#')}."
        },
        {
            "q": f"How much does a {tmeta['display'].lower()} permit cost in {city_name}?",
            "a": f"In {city_name}: {fee_text}. Fees may change — always confirm with {city.get('permit_office', 'the permit office')} before applying."
        },
        {
            "q": f"How long does a {tmeta['display'].lower()} permit take in {city_name}?",
            "a": f"{timeline_str}. Simple residential trade work is often issued same-day or within 1–3 business days."
        },
        {
            "q": f"Who can pull a {tmeta['display'].lower()} permit in {state_name}?",
            "a": trade.get("who_can_pull", f"A licensed {tmeta['display']} contractor in most cases.")
        },
    ]

    faq_schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": faq["q"],
                "acceptedAnswer": {"@type": "Answer", "text": faq["a"]}
            }
            for faq in faqs
        ]
    }

    service_schema = {
        "@context": "https://schema.org",
        "@type": "Service",
        "name": f"{tmeta['display']} Permit Research \u2014 {city_name}, {state_abbr}",
        "description": desc,
        "url": canonical,
        "provider": {
            "@type": "Organization",
            "name": "PermitAssist",
            "url": SITE_URL,
            "logo": f"{SITE_URL}/logo.png"
        },
        "areaServed": {
            "@type": "City",
            "name": city_name,
            "containedInPlace": {"@type": "State", "name": state_name}
        },
        "serviceType": f"{tmeta['display']} Permit Research",
        "offers": {
            "@type": "Offer",
            "price": "0",
            "priceCurrency": "USD",
            "description": "3 free permit lookups, then $19/mo unlimited"
        }
    }

    # Combined structured data for rich results
    schema = [faq_schema, service_schema]

    # Build always/exempt lists
    always_list = "".join(f"<li>{item}</li>" for item in trade.get("permit_required", {}).get("always_required", []))
    exempt_list = "".join(f"<li>{item}</li>" for item in trade.get("permit_required", {}).get("sometimes_exempt", []))
    inspections = trade.get("inspections", [])
    insp_list = ""
    if isinstance(inspections, list):
        for item in inspections:
            if isinstance(item, dict):
                stage = item.get("stage", "")
                desc_text = item.get("description", "")
                insp_list += f"<li><strong>{stage}:</strong> {desc_text}</li>"
    elif isinstance(inspections, dict):
        for stage, desc_text in inspections.items():
            if isinstance(desc_text, str):
                insp_list += f"<li><strong>{stage.replace('_',' ').title()}:</strong> {desc_text}</li>"

    mistakes = "".join(f"<li>{m}</li>" for m in trade.get("common_mistakes", [])[:4])
    pro_tips = "".join(f"<li>{t}</li>" for t in trade.get("pro_tips", [])[:4])
    key_notes = "".join(f"<li>{n}</li>" for n in city.get("key_notes", [])[:4])
    state_quirks = "".join(f"<li>{q}</li>" for q in state_data.get("key_quirks", [])[:4])

    # Related city + trade links
    other_trades = [t for t in TRADE_META if t != trade_key]
    other_trade_links = " ".join(
        f'<a href="{SITE_URL}/permits/{slug(t.replace("_","-"))}/{city_key.replace("_","-")}">'
        f'{TRADE_META[t]["display"]}</a>'
        for t in other_trades[:6]
    )

    other_cities_same_state = [
        k for k, v in CITIES.items()
        if v["state"] == state_abbr and k != city_key
    ][:6]
    other_city_links = " ".join(
        f'<a href="{SITE_URL}/permits/{slug(trade_key.replace("_","-"))}/{k.replace("_","-")}">'
        f'{CITIES[k]["city"]}</a>'
        for k in other_cities_same_state
    )

    # Enriched trade links (with icons)
    other_trades_enriched = " ".join(
        f'<a href="{SITE_URL}/permits/{slug(t.replace("_","-"))}/{city_key.replace("_","-")}">{TRADE_META[t]["icon"]} {TRADE_META[t]["display"]}</a>'
        for t in [x for x in TRADE_META if x != trade_key]
    )
    # Cross-state city links for same trade
    cross_state_city_links = " ".join(
        f'<a href="{SITE_URL}/permits/{slug(trade_key.replace("_","-"))}/{k.replace("_","-")}">{CITIES[k]["city"]}, {CITIES[k]["state"]}</a>'
        for k in [x for x in list(CITIES.keys()) if x != city_key][:12]
    )

    faq_html = ""
    for faq in faqs:
        faq_html += f"""
      <div style="border-bottom:1px solid var(--gray-100); padding: 16px 0;">
        <h3 style="font-size:16px; color:var(--navy); margin-bottom:8px;">{faq["q"]}</h3>
        <p style="margin:0;">{faq["a"]}</p>
      </div>"""

    body = f"""
<div class="hero">
  <div class="container">
    <div class="breadcrumb">
      <a href="{SITE_URL}">PermitAssist</a>
      <span>›</span>
      <a href="{SITE_URL}/permits/">Permits</a>
      <span>›</span>
      <a href="{SITE_URL}/permits/guide/{slug(trade_key.replace('_','-'))}">{tmeta['display']}</a>
      <span>›</span>
      {city_name}, {state_abbr}
    </div>
    <h1>{tmeta["icon"]} {tmeta["display"]} Permit in {city_name}, {state_abbr}</h1>
    <p class="hero-sub">
      Exact fees, requirements, and timelines for {city_name} — verified from official sources.
      Use our free AI tool to get a permit report in 5 seconds.
    </p>
    <a class="hero-cta" href="{TOOL_URL}">Check My Permit on PermitAssist — Free →</a>
  </div>
</div>

<div class="content">
  <div class="container">

    <!-- Quick Facts -->
    <div class="card">
      <h2>📋 Quick Facts — {tmeta["display"]} Permit in {city_name}</h2>
      <div class="grid-2">
        <div>
          <table class="fee-table">
            <thead><tr><th>Detail</th><th>Info</th></tr></thead>
            <tbody>
              <tr><td><strong>Permit Required?</strong></td><td><span class="badge badge-green">Yes — in most cases</span></td></tr>
              <tr><td><strong>Permit Type</strong></td><td>{tmeta["permit_type"]}</td></tr>
              <tr><td><strong>Permit Office</strong></td><td><a href="{city.get('permit_url','#')}" target="_blank" rel="noopener">{city.get("permit_office","Local AHJ")}</a></td></tr>
              <tr><td><strong>Phone</strong></td><td>{city.get("phone","Call to confirm")}</td></tr>
              <tr><td><strong>Hours</strong></td><td>{city.get("hours","Mon–Fri business hours")}</td></tr>
              <tr><td><strong>Online Portal</strong></td><td>{"<a href='" + city["online_portal"] + "' target='_blank' rel='noopener'>Apply Online</a>" if city.get("online_portal") else "In-person required"}</td></tr>
            </tbody>
          </table>
        </div>
        <div>
          <table class="fee-table">
            <thead><tr><th>Fee / Timeline</th><th>Current Data</th></tr></thead>
            <tbody>
              <tr><td><strong>{tmeta["display"]} Permit Fee</strong></td><td>{fee_text}</td></tr>
              <tr><td><strong>Approval Time</strong></td><td>{timeline_str}</td></tr>
              <tr><td><strong>NEC Edition ({state_abbr})</strong></td><td>{state_data.get("nec_edition","Varies")}</td></tr>
              <tr><td><strong>Permit Authority</strong></td><td>{state_data.get("permit_authority","Local AHJ")[:80]}</td></tr>
            </tbody>
          </table>
        </div>
      </div>
      <div class="info-box" style="margin-top:16px;">
        📍 <strong>Office Address:</strong> {city.get("address","Contact office for address")}
      </div>
    </div>

    <!-- Do I Need a Permit -->
    <div class="card">
      <h2>Do I Need a {tmeta["display"]} Permit in {city_name}?</h2>
      <p>
        <strong>The short answer: almost always yes.</strong>
        {trade["permit_required"]["rule"]} — and {city_name} is no exception.
        The {city.get("permit_office","local building department")} enforces {tmeta["permit_type"].lower()} requirements
        for residential and commercial work.
      </p>
      <h3>✅ Work That Always Requires a Permit</h3>
      <ul>{always_list}</ul>
      {"<h3>⚠️ Work That May Be Exempt</h3><ul>" + exempt_list + "</ul>" if exempt_list else ""}
      <div class="warn-box">
        <strong>⚠️ Don't skip the permit.</strong> Working without a permit in {city_name} can result in
        stop-work orders, fines, failed home sales, and liability if the work causes damage or injury.
      </div>
    </div>

    <!-- Fee Breakdown -->
    <div class="card">
      <h2>💰 {tmeta["display"]} Permit Fees in {city_name} ({TODAY[:4]})</h2>
      <p>
        Permit fees in {city_name} are set by {city.get("permit_office","the local permit office")}.
        Here's the current fee structure for {tmeta["display"].lower()} work:
      </p>
      <table class="fee-table">
        <thead><tr><th>Fee Item</th><th>Amount</th></tr></thead>
        <tbody>
          <tr><td><strong>{tmeta["display"]} Permit</strong></td><td>{fee_text}</td></tr>
          {"".join(f'<tr><td>{k.replace("_"," ").title()}</td><td>{v}</td></tr>' for k, v in city.get("fees", {}).items() if k != "fee_note" and k != slug(trade_key) and k not in ["hvac_mechanical"] and len(str(v)) > 3)[:3]}
        </tbody>
      </table>
      {"<p class='info-box'>" + city['fees'].get('fee_note','') + "</p>" if city.get('fees',{}).get('fee_note') else ""}
      <p style="font-size:13px; color:var(--gray-400); margin-top:8px;">
        * Fees may change. Always verify current rates with {city.get("permit_office","the permit office")} at
        <a href="{city.get('permit_url','#')}" target="_blank" rel="noopener">{city.get("permit_url","the official portal")}</a>.
      </p>
    </div>

    <!-- Inspections -->
    {f"""<div class="card">
      <h2>🔍 Inspection Requirements</h2>
      <p>After the permit is issued, {city_name} requires inspections at key stages. Do not cover work before it's inspected.</p>
      <ul>{insp_list}</ul>
    </div>""" if insp_list else ""}

    <!-- How to Apply -->
    <div class="card">
      <h2>📝 How to Apply for a {tmeta["display"]} Permit in {city_name}</h2>
      <ol style="padding-left:20px;">
        <li style="margin-bottom:10px;"><strong>Verify your contractor is licensed</strong> — {trade.get("who_can_pull", f"A licensed {tmeta['display'].lower()} contractor must pull the permit.")}.</li>
        <li style="margin-bottom:10px;"><strong>Gather required documents</strong> — Equipment specs, site plan, load calculations where applicable.</li>
        <li style="margin-bottom:10px;"><strong>Submit the application</strong> — {"Online at <a href='" + city['online_portal'] + "' target='_blank' rel='noopener'>" + city.get('permit_office','the portal') + "</a> or in person." if city.get('online_portal') else f"In person at {city.get('address','the permit office')}."}</li>
        <li style="margin-bottom:10px;"><strong>Pay the permit fee</strong> — {fee_text}.</li>
        <li style="margin-bottom:10px;"><strong>Post the permit</strong> — Keep a copy on-site until all inspections pass.</li>
        <li style="margin-bottom:10px;"><strong>Schedule inspections</strong> — Do not cover work until the inspector signs off.</li>
      </ol>
    </div>

    <!-- {state_name} State Notes -->
    {f"""<div class="card">
      <h2>📌 {state_name} State Requirements</h2>
      <p>In addition to {city_name}'s local rules, these state-level requirements apply:</p>
      <ul>{state_quirks}</ul>
      <p style="margin-top:8px;"><a href="{state_data.get('licensing_board_url','#')}" target="_blank" rel="noopener">→ {state_name} Licensing Board</a></p>
    </div>""" if state_quirks else ""}

    <!-- Why Contractors in [City] Use PermitAssist -->
    <div class="card">
      <h2>🏗️ Why Contractors in {city_name} Use PermitAssist</h2>
      <ul>
        <li><strong>Skip the hold music:</strong> {city_name}'s {city.get("permit_office", "building department")} is busy —
          AI research takes 30 seconds vs. 30–45 minutes on hold or waiting for a callback.</li>
        <li><strong>Know before you bid:</strong> Include exact {tmeta["display"].lower()} permit costs in estimates
          before the job is won — no surprise fees eating into your margin.</li>
        <li><strong>Multi-city {state_name} coverage:</strong> If you work across {state_name}, get permit
          requirements for every AHJ in one tool — no per-city learning curve.</li>
        <li><strong>Avoid the $2K–$10K mistake:</strong> One stop-work order costs more than years of PermitAssist.
          Know the {tmeta["display"].lower()} permit rules before you start — every time.</li>
      </ul>
    </div>

    <!-- Common Permit Mistakes -->
    {f"""<div class="card">
      <h2>🚫 Common {tmeta["display"]} Permit Mistakes in {city_name}</h2>
      <p style="font-size:14px;color:#4b5563;margin-bottom:12px;">These are the most frequent errors contractors
        make when pulling {tmeta["display"].lower()} permits in {city_name} and the surrounding {state_name} area:</p>
      <ul>{mistakes}</ul>
    </div>""" if mistakes else ""}

    <!-- Compare: PermitAssist vs. Calling [City] Building Department -->
    <div class="card">
      <h2>⚡ PermitAssist vs. Calling {city_name} Building Department</h2>
      <table class="fee-table">
        <thead><tr><th></th><th>PermitAssist</th><th>Calling {city_name}</th></tr></thead>
        <tbody>
          <tr><td><strong>Time to answer</strong></td><td style="color:#0d9f6e;font-weight:700;">30 seconds</td><td>30–60 min (hold + callback)</td></tr>
          <tr><td><strong>Cost</strong></td><td style="color:#0d9f6e;font-weight:700;">$19/mo unlimited</td><td>Free + $50–$150/hr of your time</td></tr>
          <tr><td><strong>Hours available</strong></td><td style="color:#0d9f6e;font-weight:700;">24/7</td><td>{city_hours}</td></tr>
          <tr><td><strong>Answer consistency</strong></td><td style="color:#0d9f6e;font-weight:700;">Consistent, structured</td><td>Varies by who answers</td></tr>
          <tr><td><strong>Inspector checklist</strong></td><td style="color:#0d9f6e;font-weight:700;">✓ Included</td><td>✗ Must ask the right questions</td></tr>
          <tr><td><strong>Exact permit name</strong></td><td style="color:#0d9f6e;font-weight:700;">✓ Every time</td><td>Sometimes (depends on staff)</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Pro Tips -->
    {f"""<div class="card">
      <h2>💡 Pro Tips from Contractors</h2>
      <ul>{pro_tips}</ul>
      {f"<h3>Notes specific to {city_name}:</h3><ul>{key_notes}</ul>" if key_notes else ""}
    </div>""" if pro_tips or key_notes else ""}

    <!-- CTA -->
    <div class="cta-block">
      <h2>Get Your Exact {tmeta["display"]} Permit Requirements in 5 Seconds</h2>
      <p>
        Our AI searches live building department data for {city_name} and all 50 states —
        returning the exact permit requirements, fees, and application links for your job.
        No hold music. No guessing.
      </p>
      <a href="{TOOL_URL}">Run a Free Permit Check on PermitAssist →</a>
    </div>

    <!-- Brand trust bar -->
    <div style="background:#e8f0fe;border:1px solid rgba(26,86,219,0.2);border-radius:10px;padding:16px 20px;margin-bottom:16px;text-align:center;">
      <p style="font-size:14px;color:#0f2044;font-weight:700;margin-bottom:4px;">Powered by <a href="{SITE_URL}" style="color:#1a56db;">PermitAssist</a> — AI Permit Research for Contractors</p>
      <p style="font-size:12px;color:#4b5563;">Trusted by contractors in {city_name} and 90+ US cities · <a href="{SITE_URL}/pricing" style="color:#1a56db;">$19/mo unlimited lookups</a> · <a href="{SITE_URL}/cities" style="color:#1a56db;">See all PermitAssist cities</a></p>
    </div>

    <!-- FAQ -->
    <div class="card">
      <h2>❓ Frequently Asked Questions</h2>
      {faq_html}
    </div>

    <!-- Related — enriched internal linking -->
    <div class="card">
      <h2>🔗 Related Permit Pages</h2>

      <h3>Other permit types in {city_name}:</h3>
      <div class="related-links">{other_trades_enriched}</div>

      {f'<h3 style="margin-top:16px;">{tmeta["display"]} permits in other {state_name} cities:</h3><div class="related-links">{other_city_links}</div>' if other_city_links else ""}

      <div style="margin-top:16px;">
        <a href="{SITE_URL}/permits/state/{slug(state_name)}" class="badge badge-blue">→ Full {state_name} Permit Guide</a>
        &nbsp;
        <a href="{SITE_URL}/permits/guide/{slug(trade_key.replace('_','-'))}" class="badge badge-blue">→ Full {tmeta["display"]} Permit Guide</a>
      </div>

      <h3 style="margin-top:16px;">More cities — {tmeta["display"]} permits nationwide:</h3>
      <div class="related-links">{cross_state_city_links}</div>
    </div>

  </div>
</div>
"""
    return html_base(title, desc, canonical, body, schema_json=json.dumps(schema))


# ══════════════════════════════════════════════════════════════════════════════
# 2. STATE HUB PAGES
# ══════════════════════════════════════════════════════════════════════════════

def build_state_page(state_abbr: str, state_data: dict) -> str:
    """Generate a state-level hub page."""
    state_name = state_data.get("name", state_abbr)
    state_cities = {k: v for k, v in CITIES.items() if v["state"] == state_abbr}

    canonical = f"{SITE_URL}/permits/state/{slug(state_name)}"
    title = f"{state_name} Building Permit Guide — HVAC, Electrical, Roofing & Plumbing ({TODAY[:4]})"
    desc = (
        f"Complete permit guide for {state_name} contractors. HVAC, electrical, roofing, and plumbing permit "
        f"requirements, costs, and licensing rules for all major {state_name} cities — verified {TODAY[:4]}."
    )

    schema = {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": title,
        "description": desc,
        "url": canonical,
        "publisher": {"@type": "Organization", "name": SITE_NAME, "url": SITE_URL}
    }

    quirks = "".join(f"<li>{q}</li>" for q in state_data.get("key_quirks", []))

    city_cards = ""
    for city_key, city_val in state_cities.items():
        trade_links = " ".join(
            f'<a href="{SITE_URL}/permits/{slug(t.replace("_","-"))}/{city_key.replace("_","-")}">'
            f'{TRADE_META[t]["display"]}</a>'
            for t in list(TRADE_META.keys())[:5]
        )
        city_cards += f"""
      <div class="mini-card">
        <strong>📍 {city_val["city"]}</strong>
        <p style="margin:4px 0 8px; font-size:12px; color:var(--gray-400);">{city_val.get("permit_office","")}</p>
        <div class="related-links" style="gap:4px;">{trade_links}</div>
      </div>"""

    all_trade_links = ""
    for trade_key, tmeta in TRADE_META.items():
        all_trade_links += f"""
      <a href="{SITE_URL}/permits/guide/{slug(trade_key.replace('_','-'))}" style="display:flex; align-items:center; gap:8px; padding:12px; background:#fff; border:1px solid var(--gray-200); border-radius:8px; font-weight:600; color:var(--navy);">
        {tmeta["icon"]} {tmeta["display"]}
      </a>"""

    body = f"""
<div class="hero">
  <div class="container">
    <div class="breadcrumb">
      <a href="{SITE_URL}">PermitAssist</a>
      <span>›</span>
      <a href="{SITE_URL}/permits/">Permits</a>
      <span>›</span>
      {state_name}
    </div>
    <h1>🏛️ {state_name} Permit Guide for Contractors</h1>
    <p class="hero-sub">
      Everything you need to know about pulling permits in {state_name} —
      HVAC, electrical, roofing, and plumbing. Fees, requirements, and licensing rules for all major cities.
    </p>
    <a class="hero-cta" href="{TOOL_URL}">Check My {state_name} Permit Requirements Free →</a>
  </div>
</div>

<div class="content">
  <div class="container">

    <!-- State Overview -->
    <div class="card">
      <h2>📋 {state_name} Permit Overview</h2>
      <table class="fee-table">
        <thead><tr><th>Detail</th><th>Info</th></tr></thead>
        <tbody>
          <tr><td><strong>NEC Edition</strong></td><td>{state_data.get("nec_edition","Varies by jurisdiction")}</td></tr>
          <tr><td><strong>IRC Edition</strong></td><td>{state_data.get("irc_edition","Varies by jurisdiction")}</td></tr>
          <tr><td><strong>Permit Authority</strong></td><td>{state_data.get("permit_authority","Local jurisdictions")}</td></tr>
          <tr><td><strong>Statewide Code</strong></td><td>{"Yes" if state_data.get("statewide_code") == True else "Partial" if state_data.get("statewide_code") == "partial" else "No — local jurisdictions control"}</td></tr>
          <tr><td><strong>Licensing Board</strong></td><td><a href="{state_data.get('licensing_board_url','#')}" target="_blank" rel="noopener">{state_data.get("licensing_board_url","See state website")}</a></td></tr>
        </tbody>
      </table>
    </div>

    <!-- Key Quirks -->
    {f"""<div class="card">
      <h2>⚠️ Key Things to Know About Permitting in {state_name}</h2>
      <ul>{quirks}</ul>
    </div>""" if quirks else ""}

    <!-- Cities -->
    {f"""<div class="card">
      <h2>📍 Cities in {state_name} — Permit Guides</h2>
      <div class="grid-2">{city_cards}</div>
    </div>""" if city_cards else f"""<div class="info-box">We're adding more {state_name} cities soon. Use our AI tool for any city.</div>"""}

    <!-- Trade Guides -->
    <div class="card">
      <h2>🔨 Permit Guides by Trade — {state_name}</h2>
      <div class="grid-3">{all_trade_links}</div>
    </div>

    <!-- CTA -->
    <div class="cta-block">
      <h2>Check Any {state_name} City Permit in 5 Seconds</h2>
      <p>Our AI searches live building department data for every city and county in {state_name}.</p>
      <a href="{TOOL_URL}">Run a Free Permit Check →</a>
    </div>

  </div>
</div>
"""
    return html_base(title, desc, canonical, body, schema_json=json.dumps(schema))


# ══════════════════════════════════════════════════════════════════════════════
# 3. TRADE GUIDE PAGES
# ══════════════════════════════════════════════════════════════════════════════

def build_trade_page(trade_key: str) -> str:
    """Generate a national trade guide page."""
    trade = TRADES[trade_key]
    tmeta = TRADE_META[trade_key]

    canonical = f"{SITE_URL}/permits/guide/{slug(trade_key.replace('_','-'))}"
    title = f"{tmeta['display']} Permit Guide — Requirements, Costs & How to Apply (All 50 States, {TODAY[:4]})"
    desc = (
        f"Complete {tmeta['display'].lower()} permit guide for US contractors. When is a permit required, "
        f"how much does it cost, who can pull it, and what inspections are needed — all 50 states, {TODAY[:4]}."
    )

    schema = {
        "@context": "https://schema.org",
        "@type": "HowTo",
        "name": f"How to Get a {tmeta['display']} Permit",
        "description": desc,
        "url": canonical,
        "step": [
            {"@type": "HowToStep", "name": "Verify permit is required", "text": trade["permit_required"]["rule"]},
            {"@type": "HowToStep", "name": "Hire a licensed contractor", "text": trade.get("who_can_pull", "")},
            {"@type": "HowToStep", "name": "Submit application and pay fee",
             "text": f"Typical fee: see guide"},
            {"@type": "HowToStep", "name": "Schedule inspections",
             "text": "Do not cover work until all required inspections pass."},
        ]
    }

    always_list = "".join(f"<li>{i}</li>" for i in trade.get("permit_required", {}).get("always_required", []))
    exempt_list = "".join(f"<li>{i}</li>" for i in trade.get("permit_required", {}).get("sometimes_exempt", []))
    insp_html = ""
    inspections_trade = trade.get("inspections", [])
    if isinstance(inspections_trade, list):
        for item in inspections_trade:
            if isinstance(item, dict):
                stage = item.get("stage", "")
                text = item.get("description", "")
                insp_html += f"<li><strong>{stage}:</strong> {text}</li>"
    elif isinstance(inspections_trade, dict):
        for stage, text in inspections_trade.items():
            if isinstance(text, str):
                insp_html += f"<li><strong>{stage.replace('_',' ').title()}:</strong> {text}</li>"
    mistakes = "".join(f"<li>{m}</li>" for m in trade.get("common_mistakes", []))
    pro_tips = "".join(f"<li>{t}</li>" for t in trade.get("pro_tips", []))

    _fee_range_raw = trade.get("fee_range", {})
    if isinstance(_fee_range_raw, dict):
        fee_range = _fee_range_raw
    else:
        fee_range = {"typical": str(_fee_range_raw)}
    fee_html = ""
    for tier, val in fee_range.items():
        if tier != "note" and isinstance(val, str):
            fee_html += f"<tr><td>{tier.replace('_',' ').title()}</td><td>{val}</td></tr>"

    # State notes
    state_notes = trade.get("state_notes", {})
    state_notes_html = ""
    for state_key, note in list(state_notes.items())[:10]:
        state_notes_html += f"<tr><td>{state_key}</td><td>{note}</td></tr>"

    # City links
    city_links_html = ""
    for city_key, city_val in list(CITIES.items())[:18]:
        city_links_html += (
            f'<a href="{SITE_URL}/permits/{slug(trade_key.replace("_","-"))}/{city_key.replace("_","-")}">'
            f'{city_val["city"]}, {city_val["state"]}</a> '
        )

    body = f"""
<div class="hero">
  <div class="container">
    <div class="breadcrumb">
      <a href="{SITE_URL}">PermitAssist</a>
      <span>›</span>
      <a href="{SITE_URL}/permits/">Permits</a>
      <span>›</span>
      {tmeta["display"]} Guide
    </div>
    <h1>{tmeta["icon"]} {tmeta["full"]} Permit Guide</h1>
    <p class="hero-sub">
      When do you need a permit, how much does it cost, who pulls it, and what inspections are required —
      complete guide for contractors across all 50 states.
    </p>
    <a class="hero-cta" href="{TOOL_URL}">Check My City's Requirements Free →</a>
  </div>
</div>

<div class="content">
  <div class="container">

    <!-- Overview -->
    <div class="card">
      <h2>📋 {tmeta["display"]} Permit Overview</h2>
      <table class="fee-table">
        <thead><tr><th>Detail</th><th>Info</th></tr></thead>
        <tbody>
          <tr><td><strong>Permit Type</strong></td><td>{tmeta["permit_type"]}</td></tr>
          <tr><td><strong>Governing Code</strong></td><td>{trade.get("governing_code","Varies by state")}</td></tr>
          <tr><td><strong>Who Can Pull</strong></td><td>{trade.get("who_can_pull","Licensed contractor in most states")}</td></tr>
          <tr><td><strong>Typical Fee</strong></td><td>{fee_range.get("typical","$75–$250")}</td></tr>
          <tr><td><strong>Approval Timeline</strong></td><td>{trade.get("approval_timeline",{}).get("over_the_counter","Same day in most suburban cities")}</td></tr>
        </tbody>
      </table>
    </div>

    <!-- When Required -->
    <div class="card">
      <h2>When Do You Need a {tmeta["display"]} Permit?</h2>
      <p><strong>{trade["permit_required"]["rule"]}</strong></p>
      <h3>✅ Always Requires a Permit</h3>
      <ul>{always_list}</ul>
      {"<h3>⚠️ May Be Exempt</h3><ul>" + exempt_list + "</ul>" if exempt_list else ""}
      <div class="warn-box">
        <strong>⚠️ When in doubt, pull the permit.</strong> The consequences of skipping a required permit —
        fines, stop-work orders, failed home sales, liability — far outweigh the cost of applying.
      </div>
    </div>

    <!-- Fees -->
    <div class="card">
      <h2>💰 {tmeta["display"]} Permit Costs by Market Type</h2>
      <table class="fee-table">
        <thead><tr><th>Market</th><th>Typical Fee</th></tr></thead>
        <tbody>{fee_html}</tbody>
      </table>
      <div class="info-box" style="margin-top:12px;">
        Fees vary significantly by jurisdiction. Use our free AI tool to get the exact fee for your specific city.
      </div>
    </div>

    <!-- Inspections -->
    {f"""<div class="card">
      <h2>🔍 Required Inspections</h2>
      <p>After the permit is issued, work must be inspected at these stages. Do not cover or close up work before inspection.</p>
      <ul>{insp_html}</ul>
    </div>""" if insp_html else ""}

    <!-- State Notes -->
    {f"""<div class="card">
      <h2>📌 State-by-State Notes</h2>
      <p>Key differences across states for {tmeta["display"].lower()} permits:</p>
      <table class="fee-table">
        <thead><tr><th>State</th><th>Key Note</th></tr></thead>
        <tbody>{state_notes_html}</tbody>
      </table>
      <p style="margin-top:8px; font-size:13px; color:var(--gray-400);">Use our tool for any state not listed above.</p>
    </div>""" if state_notes_html else ""}

    <!-- Common Mistakes -->
    {f"""<div class="card">
      <h2>🚫 Common Mistakes to Avoid</h2>
      <ul>{mistakes}</ul>
    </div>""" if mistakes else ""}

    <!-- Pro Tips -->
    {f"""<div class="card">
      <h2>💡 Pro Tips</h2>
      <ul>{pro_tips}</ul>
    </div>""" if pro_tips else ""}

    <!-- City Pages -->
    <div class="card">
      <h2>📍 {tmeta["display"]} Permit Guides by City</h2>
      <p>We have verified fee data for these cities:</p>
      <div class="related-links">{city_links_html}</div>
      <div class="info-box" style="margin-top:16px;">
        Don't see your city? Our AI tool covers all 50 states and thousands of jurisdictions.
      </div>
    </div>

    <!-- CTA -->
    <div class="cta-block">
      <h2>Get Your Exact {tmeta["display"]} Permit Requirements in 5 Seconds</h2>
      <p>Enter your trade, city, and state — our AI returns the permit requirements, fees, and application link instantly.</p>
      <a href="{TOOL_URL}">Run a Free Permit Check →</a>
    </div>

  </div>
</div>
"""
    return html_base(title, desc, canonical, body, schema_json=json.dumps(schema))


# ══════════════════════════════════════════════════════════════════════════════
# 4. SITEMAP + ROBOTS
# ══════════════════════════════════════════════════════════════════════════════

def build_sitemap(all_urls: list[str]) -> str:
    urls_xml = ""
    for url in all_urls:
        urls_xml += f"""  <url>
    <loc>{url}</loc>
    <lastmod>{TODAY}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>\n"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls_xml}</urlset>"""


def build_robots() -> str:
    return f"""User-agent: *
Allow: /

Sitemap: {SITE_URL}/sitemap.xml
"""


# ══════════════════════════════════════════════════════════════════════════════
# 5. INDEX PAGE (links hub for internal linking)
# ══════════════════════════════════════════════════════════════════════════════

def build_seo_index() -> str:
    city_trade_links = ""
    for city_key, city_val in CITIES.items():
        for trade_key, tmeta in TRADE_META.items():
            url = f"{SITE_URL}/permits/{slug(trade_key.replace('_','-'))}/{city_key.replace('_','-')}"
            city_trade_links += f'<li><a href="{url}">{tmeta["display"]} Permit — {city_val["city"]}, {city_val["state"]}</a></li>\n'

    state_links = ""
    for state_key, state_val in STATES.items():
        name = state_val.get("name", state_key)
        url = f"{SITE_URL}/permits/state/{slug(name)}"
        state_links += f'<li><a href="{url}">{name}</a></li>\n'

    trade_links = ""
    for trade_key, tmeta in TRADE_META.items():
        url = f"{SITE_URL}/permits/guide/{slug(trade_key.replace('_','-'))}"
        trade_links += f'<li><a href="{url}">{tmeta["icon"]} {tmeta["display"]} Permit Guide</a></li>\n'

    canonical = f"{SITE_URL}/permits/"
    title = "Permit Guides — HVAC, Electrical, Roofing & Plumbing by City | PermitAssist"
    desc = "Browse permit guides for HVAC, electrical, roofing, and plumbing by city and state. Exact fees and requirements from official sources."

    body = f"""
<div class="hero">
  <div class="container">
    <h1>🗂️ Permit Guides — All Cities & Trades</h1>
    <p class="hero-sub">Browse our library of permit guides for contractors. Exact fees and requirements from official sources, updated {TODAY[:4]}.</p>
    <a class="hero-cta" href="{TOOL_URL}">Check Any City Free →</a>
  </div>
</div>
<div class="content">
  <div class="container">
    <div class="grid-2">
      <div class="card">
        <h2>By Trade</h2>
        <ul>{trade_links}</ul>
      </div>
      <div class="card">
        <h2>By State</h2>
        <ul>{state_links}</ul>
      </div>
    </div>
    <div class="card">
      <h2>City × Trade Pages ({len(CITIES) * len(TRADE_META)} total)</h2>
      <ul style="columns: 2; column-gap: 24px;">{city_trade_links}</ul>
    </div>
    <div class="cta-block">
      <h2>Don't See Your City?</h2>
      <p>Our AI covers all 50 states. Just enter your trade, city, and state.</p>
      <a href="{TOOL_URL}">Run a Free Permit Check →</a>
    </div>
  </div>
</div>
"""
    index_schema = json.dumps({
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": SITE_NAME,
        "url": SITE_URL,
        "description": desc,
        "potentialAction": {
            "@type": "SearchAction",
            "target": {"@type": "EntryPoint", "urlTemplate": f"{SITE_URL}/?q={{search_term_string}}"},
            "query-input": "required name=search_term_string"
        }
    })
    return html_base(title, desc, canonical, body, schema_json=index_schema)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN — Generate All Pages
# ══════════════════════════════════════════════════════════════════════════════

def main():
    all_urls = [SITE_URL, f"{SITE_URL}/permits/"]
    page_count = 0

    # 1. City × Trade pages
    ct_dir = OUT_DIR / "permits"
    for trade_key in TRADES:
        trade_slug = slug(trade_key.replace("_", "-"))
        trade_dir = ct_dir / trade_slug
        trade_dir.mkdir(parents=True, exist_ok=True)
        for city_key in CITIES:
            city_slug = city_key.replace("_", "-")
            out_path = trade_dir / city_slug / "index.html"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            html = build_city_trade_page(city_key, trade_key)
            out_path.write_text(html, encoding="utf-8")
            url = f"{SITE_URL}/permits/{trade_slug}/{city_slug}"
            all_urls.append(url)
            page_count += 1

    # 2. State hub pages
    state_dir = ct_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    for state_key, state_data in STATES.items():
        state_name = state_data.get("name", state_key)
        state_slug = slug(state_name)
        out_path = state_dir / state_slug / "index.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        html = build_state_page(state_key, state_data)
        out_path.write_text(html, encoding="utf-8")
        url = f"{SITE_URL}/permits/state/{state_slug}"
        all_urls.append(url)
        page_count += 1

    # 3. Trade guide pages
    guide_dir = ct_dir / "guide"
    guide_dir.mkdir(parents=True, exist_ok=True)
    for trade_key in TRADES:
        trade_slug = slug(trade_key.replace("_", "-"))
        out_path = guide_dir / trade_slug / "index.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        html = build_trade_page(trade_key)
        out_path.write_text(html, encoding="utf-8")
        url = f"{SITE_URL}/permits/guide/{trade_slug}"
        all_urls.append(url)
        page_count += 1

    # 4. Permits index
    index_out = ct_dir / "index.html"
    index_out.write_text(build_seo_index(), encoding="utf-8")

    # 5. Sitemap
    (OUT_DIR / "sitemap.xml").write_text(build_sitemap(all_urls), encoding="utf-8")

    # 6. Robots
    (OUT_DIR / "robots.txt").write_text(build_robots(), encoding="utf-8")

    print(f"✅ Generated {page_count} SEO pages + sitemap + robots.txt")
    print(f"   City×Trade:  {len(CITIES) * len(TRADES)}")
    print(f"   State hubs:  {len(STATES)}")
    print(f"   Trade guides:{len(TRADES)}")
    print(f"   Output dir:  {OUT_DIR}")
    print(f"   Total URLs:  {len(all_urls)}")


if __name__ == "__main__":
    main()
