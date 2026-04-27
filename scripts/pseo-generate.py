#!/usr/bin/env python3
from dotenv import load_dotenv
try:
    load_dotenv()  # reads .env from CWD or parent
except AssertionError:
    # python-dotenv can assert when invoked through stdin/test harnesses; direct script runs use the line above.
    from pathlib import Path as _Path
    load_dotenv(_Path(__file__).resolve().parents[1] / ".env")

import argparse
import html
import importlib
import json
import os
import re
import sys
import time
import types
from datetime import date
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE_URL = "https://permitassist.io"
TEMPLATE_PATH = ROOT / "seo" / "template-trade-city.html"
OUTPUT_ROOT = ROOT / "frontend" / "permits"
MISSING_LOG = ROOT / "seo" / "missing-data.log"

CITIES = [
    {"city": "Houston", "state": "TX", "state_name": "Texas", "slug": "houston-tx", "lat": "29.7604", "lng": "-95.3698"},
    {"city": "Dallas", "state": "TX", "state_name": "Texas", "slug": "dallas-tx", "lat": "32.7767", "lng": "-96.7970"},
    {"city": "Phoenix", "state": "AZ", "state_name": "Arizona", "slug": "phoenix-az", "lat": "33.4484", "lng": "-112.0740"},
    {"city": "Atlanta", "state": "GA", "state_name": "Georgia", "slug": "atlanta-ga", "lat": "33.7490", "lng": "-84.3880"},
]

TRADES = [
    {"slug": "hvac-system", "display": "HVAC System Replacement", "job_type": "residential HVAC system replacement"},
    {"slug": "water-heater", "display": "Water Heater Replacement", "job_type": "plumbing water heater replacement"},
    {"slug": "panel-upgrade", "display": "Electrical Panel Upgrade", "job_type": "electrical panel upgrade"},
    {"slug": "solar-pv", "display": "Solar PV System", "job_type": "residential solar PV system"},
    {"slug": "roof-replacement", "display": "Roof Replacement", "job_type": "roof replacement"},
]

REQUIRED_FIELDS = ["permits_required", "applying_office", "fee_range", "approval_timeline", "inspections"]


def clean_text(value, default=""):
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        return "; ".join(clean_text(v) for v in value if clean_text(v)) or default
    if isinstance(value, dict):
        return "; ".join(f"{k}: {clean_text(v)}" for k, v in value.items() if clean_text(v)) or default
    return re.sub(r"\s+", " ", str(value)).strip() or default


def esc(value):
    return html.escape(clean_text(value), quote=True)


def list_html(items):
    safe = [clean_text(i) for i in items if clean_text(i)]
    if not safe:
        return "<p>PermitIQ did not return a separate list for this item; verify it directly with the permit office before submitting.</p>"
    return "<ul>" + "".join(f"<li>{esc(i)}</li>" for i in safe) + "</ul>"


def render_template(template, ctx):
    def repl(match):
        key = match.group(1).strip()
        return str(ctx.get(key, ""))
    return re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", repl, template)


def exec_with_future(module_name, path, package=""):
    source = Path(path).read_text(encoding="utf-8")
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    module.__name__ = module_name
    module.__package__ = package
    sys.modules[module_name] = module
    code = compile("from __future__ import annotations\n" + source, str(path), "exec")
    exec(code, module.__dict__)
    return module


def load_research_permit():
    try:
        from api.research_engine import research_permit
        return research_permit
    except TypeError as exc:
        # macOS system Python is 3.9 here; research_engine and auto_verify use PEP 604 annotations.
        # Execute with postponed annotations without editing existing tracked project files.
        if "unsupported operand type" not in str(exc):
            raise
        scripts_dir = ROOT / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        auto_verify_path = scripts_dir / "auto_verify.py"
        if auto_verify_path.exists():
            exec_with_future("auto_verify", auto_verify_path, "")
        engine_path = ROOT / "api" / "research_engine.py"
        module = exec_with_future("api.research_engine_compat", engine_path, "api")
        return module.research_permit


def normalize_result(result):
    if not isinstance(result, dict):
        return {"error": "PermitIQ returned a non-dict result"}
    return result


def missing_reasons(result):
    reasons = []
    if result.get("error"):
        reasons.append(f"error={result.get('error')}")
    for field in REQUIRED_FIELDS:
        value = result.get(field)
        if value in (None, "", [], {}):
            reasons.append(field)
    if result.get("missing_fields"):
        reasons.extend(f"missing_fields.{f}" for f in result.get("missing_fields") or [])
    if result.get("needs_review") and result.get("missing_fields"):
        reasons.append("needs_review")
    return sorted(set(reasons))


def verdict_data(result):
    verdict = clean_text(result.get("permit_verdict") or "YES").upper()
    permits = result.get("permits_required") or []
    required_flags = [p.get("required") for p in permits if isinstance(p, dict)]
    if verdict.startswith("NO") or (required_flags and all(v is False for v in required_flags)):
        return "No", "PermitIQ indicates no permit is required", "no", "No permit indicated"
    if "MAY" in verdict or "CONDIT" in verdict or any(str(v).lower() == "maybe" for v in required_flags):
        return "Conditional", "Permit may be required depending on scope", "maybe", "Conditional"
    return "Yes", "Permit likely required", "", "Permit likely required"


def permit_items_html(result):
    permits = result.get("permits_required") or []
    blocks = []
    for p in permits:
        if not isinstance(p, dict):
            continue
        name = esc(p.get("permit_type") or "Permit")
        required = p.get("required")
        req_text = "Required" if required is True else ("May be required" if str(required).lower() == "maybe" else "Check applicability")
        notes = esc(p.get("notes") or p.get("description") or "Verify this permit type in the official portal before submitting.")
        selection = esc(p.get("portal_selection") or "")
        extra = f"<br><span>Portal selection: {selection}</span>" if selection else ""
        blocks.append(f"<div class='permit-item'><strong>{name}</strong><span>{esc(req_text)} — {notes}</span>{extra}</div>")
    return "".join(blocks) or "<div class='permit-item'><strong>Permit details</strong><span>PermitIQ did not return individual permit names beyond the overall permit answer.</span></div>"


def need_permit_html(result, city, state, trade_display, department_name):
    short, _, _, _ = verdict_data(result)
    permits = result.get("permits_required") or []
    permit_summary = clean_text(permits[0].get("notes") if permits and isinstance(permits[0], dict) else "")
    conf = clean_text(result.get("confidence_reason"), "PermitIQ combined city, trade, and official-source research for this result.")
    if short == "No":
        answer = f"PermitIQ did not identify a required permit for a standard {trade_display.lower()} scope in {city}, {state}."
    elif short == "Conditional":
        answer = f"PermitIQ returned a conditional answer for {trade_display.lower()} in {city}, {state}; the exact requirement depends on scope details."
    else:
        answer = f"PermitIQ indicates that a permit is likely required for {trade_display.lower()} in {city}, {state}."
    if permit_summary:
        answer += f" The key local note returned was: {permit_summary}"
    return (
        f"<p>{esc(answer)}</p>"
        f"<p>{esc(conf)} The safest contractor workflow is to confirm the permit category with {esc(department_name)}, attach the required documents, and keep proof of approval available before work starts.</p>"
    )


def document_items(result):
    items = []
    for item in result.get("checklist") or []:
        items.append(clean_text(item))
    license_required = clean_text(result.get("license_required"))
    if license_required:
        items.insert(0, f"License or registration requirement: {license_required}")
    for p in result.get("permits_required") or []:
        if isinstance(p, dict) and p.get("portal_selection"):
            items.append(f"Select the permit category shown in the portal as: {p.get('portal_selection')}")
    # Deduplicate without inventing new requirements.
    out, seen = [], set()
    for item in items:
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out[:12]


def fees_rows(result):
    rows = []
    fee = result.get("fee_range")
    fee_text = clean_text(fee, "Verify fee schedule with the department")
    rows.append(("Permit fee / fee range", fee_text, "Use this as the planning signal, then confirm the current fee schedule before payment."))
    calc = result.get("fee_calculator") or {}
    if isinstance(calc, dict) and calc.get("fee") is not None:
        note = clean_text(calc.get("note") or calc.get("formula"), "Calculated from available fee formula.")
        rows.append(("Estimated calculated fee", f"${float(calc.get('fee')):,.2f}", note))
    for p in result.get("permits_required") or []:
        if isinstance(p, dict) and p.get("fee"):
            rows.append((p.get("permit_type") or "Permit-specific fee", p.get("fee"), p.get("notes") or "Permit-specific fee returned by PermitIQ."))
    return "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td><td>{esc(c)}</td></tr>" for a, b, c in rows)


def timeline_html(result):
    tl = result.get("approval_timeline") or {}
    bits = []
    if isinstance(tl, dict):
        for label in ("simple", "complex"):
            if tl.get(label):
                bits.append(f"<li><strong>{esc(label.title())} jobs:</strong> {esc(tl.get(label))}</li>")
        for k, v in tl.items():
            if k not in ("simple", "complex") and clean_text(v):
                bits.append(f"<li><strong>{esc(str(k).replace('_', ' ').title())}:</strong> {esc(v)}</li>")
    elif clean_text(tl):
        bits.append(f"<li>{esc(tl)}</li>")
    if not bits:
        bits.append("<li>Verify current review timing with the official permit office before scheduling work.</li>")
    return "<ul>" + "".join(bits) + "</ul>"


def portal_html(result):
    office = clean_text(result.get("applying_office"), "Local building department")
    url = clean_text(result.get("apply_url"))
    phone = clean_text(result.get("apply_phone"), "Verify phone on official portal")
    address = clean_text(result.get("apply_address"), "Verify address on official portal")
    pdf = clean_text(result.get("apply_pdf"))
    parts = [f"<div><strong>Department:</strong> {esc(office)}</div>", f"<div><strong>Phone:</strong> {esc(phone)}</div>", f"<div><strong>Address:</strong> {esc(address)}</div>"]
    if url:
        parts.append(f"<div><strong>Website / portal:</strong> <a href='{html.escape(url, quote=True)}'>{esc(url)}</a></div>")
    if pdf:
        parts.append(f"<div><strong>Application / fee PDF:</strong> <a href='{html.escape(pdf, quote=True)}'>{esc(pdf)}</a></div>")
    parts.append("<div><strong>Hours:</strong> Verify current counter hours on the official portal before visiting.</div>")
    return "".join(parts)


def sources_html(result):
    sources = []
    for key in ("apply_url", "apply_pdf"):
        if clean_text(result.get(key)):
            sources.append(clean_text(result.get(key)))
    for src in result.get("sources") or []:
        if clean_text(src):
            sources.append(clean_text(src))
    deduped = []
    seen = set()
    for src in sources:
        if src not in seen:
            seen.add(src)
            deduped.append(src)
    if not deduped:
        return "<li>PermitIQ did not return public source URLs; verify directly with the office.</li>"
    return "".join(f"<li><a href='{html.escape(src, quote=True)}'>{esc(src)}</a></li>" for src in deduped[:6])


def inspections_html(result):
    inspections = result.get("inspections") or []
    blocks = []
    for i, insp in enumerate(inspections, 1):
        if isinstance(insp, dict):
            title = clean_text(insp.get("stage") or insp.get("name") or f"Inspection {i}")
            desc = clean_text(insp.get("description") or insp.get("notes") or "Inspection required before the permit can be closed.")
            timing = clean_text(insp.get("timing"))
            timing_html = f"<p><strong>Timing:</strong> {esc(timing)}</p>" if timing else ""
        else:
            title, desc, timing_html = f"Inspection {i}", clean_text(insp), ""
        blocks.append(f"<div class='inspection-step'><div class='step-num'>{i}</div><div><h3>{esc(title)}</h3><p>{esc(desc)}</p>{timing_html}</div></div>")
    return "".join(blocks) or "<p>Verify inspection stages with the official permit office before starting work.</p>"


def mistakes_items(result):
    items = []
    for item in result.get("common_mistakes") or []:
        items.append(clean_text(item))
    for item in result.get("pro_tips") or []:
        items.append(clean_text(item))
    for item in result.get("rejection_patterns") or []:
        if isinstance(item, dict):
            pattern = clean_text(item.get("pattern"))
            fix = clean_text(item.get("fix"))
            if pattern and fix:
                items.append(f"{pattern} — fix: {fix}")
            elif pattern:
                items.append(pattern)
        else:
            items.append(clean_text(item))
    out, seen = [], set()
    for item in items:
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out[:8]


def faq_items(result, city, state, trade_display, department_name):
    verdict_short, _, _, _ = verdict_data(result)
    fee = clean_text(result.get("fee_range"), "PermitIQ did not return a precise fee; verify the current fee schedule.")
    timeline = clean_text(result.get("approval_timeline"), "Verify current review timing with the permit office.")
    inspections = result.get("inspections") or []
    insp_answer = "; ".join(clean_text(i.get("stage") if isinstance(i, dict) else i) for i in inspections[:4]) or "Confirm required inspection stages with the city before work starts."
    portal = clean_text(result.get("apply_url") or result.get("apply_phone") or department_name)
    permit_answer = {
        "Yes": f"PermitIQ indicates a permit is likely required for {trade_display.lower()} work in {city}, {state}. Confirm the exact permit category with {department_name} before submitting.",
        "No": f"PermitIQ did not identify a required permit for a standard {trade_display.lower()} scope in {city}, {state}, but you should still confirm scope-specific exceptions with {department_name}.",
        "Conditional": f"PermitIQ returned a conditional answer. The permit requirement depends on the exact {trade_display.lower()} scope, so confirm the details with {department_name}."
    }.get(verdict_short, f"Confirm the exact requirement with {department_name}.")
    return [
        (f"Do I need a permit for {trade_display.lower()} in {city}, {state}?", permit_answer),
        (f"How much does a {trade_display.lower()} permit cost in {city}?", f"PermitIQ returned this fee signal: {fee} Always verify the current fee schedule before quoting or paying."),
        (f"How long does approval take in {city}?", f"PermitIQ returned this turnaround signal: {timeline} Incomplete applications or plan review comments can add time."),
        (f"Where do I apply for the permit?", f"Apply or confirm requirements through {department_name}. The best contact or portal returned was: {portal}"),
        (f"What inspections are required for this permit?", f"PermitIQ surfaced these inspection stages or notes: {insp_answer}. Schedule inspections through the official city process."),
    ]


def faq_html(items):
    return "".join(f"<div class='faq-item'><h3>{esc(q)}</h3><p>{esc(a)}</p></div>" for q, a in items)


def meta_description(trade, city, state, fee_short, timeline_short):
    raw = f"{trade} permit requirements for {city}, {state}: fees, documents, inspections, portal contacts, and turnaround times for contractors."
    if len(raw) > 160:
        raw = raw[:157].rsplit(" ", 1)[0] + "..."
    return raw


def local_rows(ctx):
    rows = [
        ("Department", ctx["department_name"]),
        ("Address", ctx["department_address"]),
        ("Phone", ctx["department_phone"]),
        ("Website", ctx["department_url"]),
        ("Coordinates", f"{ctx['latitude']}, {ctx['longitude']}"),
        ("Hours", ctx["department_hours"]),
        ("Last reviewed", ctx["last_reviewed"]),
    ]
    return "".join(f"<tr><th>{esc(a)}</th><td>{esc(b)}</td></tr>" for a, b in rows)


def build_context(city_info, trade_info, result):
    city, state = city_info["city"], city_info["state"]
    trade_display = trade_info["display"]
    trade_lower = trade_display.lower()
    canonical_url = f"{BASE_URL}/permits/{city_info['slug']}/{trade_info['slug']}/"
    lookup_url = html.escape(f"/?city={quote_plus(city)}&state={quote_plus(state)}&trade={quote_plus(trade_info['slug'])}", quote=True)
    verdict_short, verdict_badge, verdict_class, hero_verdict = verdict_data(result)
    office = clean_text(result.get("applying_office"), f"{city} building department")
    address = clean_text(result.get("apply_address"), "Verify address on official portal")
    phone = clean_text(result.get("apply_phone"), "Verify phone on official portal")
    url = clean_text(result.get("apply_url"), "Verify website through the city portal")
    fee_short = clean_text(result.get("fee_range"), "Verify fee schedule")
    if len(fee_short) > 42:
        fee_short = fee_short[:39].rsplit(" ", 1)[0] + "…"
    tl = result.get("approval_timeline") or {}
    if isinstance(tl, dict):
        timeline_short = clean_text(tl.get("simple") or tl.get("complex") or tl, "Verify timing")
    else:
        timeline_short = clean_text(tl, "Verify timing")
    if len(timeline_short) > 42:
        timeline_short = timeline_short[:39].rsplit(" ", 1)[0] + "…"
    inspections = result.get("inspections") or []
    faq = faq_items(result, city, state, trade_display, office)
    json_ld = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "FAQPage",
                "@id": canonical_url + "#faq",
                "mainEntity": [
                    {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
                    for q, a in faq
                ],
            },
            {
                "@type": "LocalBusiness",
                "@id": canonical_url + "#building-department",
                "name": office,
                "url": url if url.startswith("http") else canonical_url,
                "telephone": phone,
                "address": {"@type": "PostalAddress", "streetAddress": address, "addressLocality": city, "addressRegion": state, "addressCountry": "US"},
                "geo": {"@type": "GeoCoordinates", "latitude": city_info["lat"], "longitude": city_info["lng"]},
                "areaServed": {"@type": "City", "name": f"{city}, {state}"},
            },
        ],
    }
    ctx = {
        "title": f"{trade_display} Permit in {city}, {state} — Requirements, Fees, Inspections | PermitAssist",
        "meta_description": meta_description(trade_display, city, state, fee_short, timeline_short),
        "canonical_url": canonical_url,
        "json_ld": json.dumps(json_ld, ensure_ascii=False, separators=(",", ":")),
        "city": city,
        "state": state,
        "state_name": city_info["state_name"],
        "trade_display": trade_display,
        "trade_lower": trade_lower,
        "lookup_url": lookup_url,
        "confidence_label": esc(clean_text(result.get("confidence"), "PermitIQ")),
        "verdict_short": hero_verdict,
        "verdict_badge": verdict_badge,
        "verdict_class": verdict_class,
        "fee_short": fee_short,
        "timeline_short": timeline_short,
        "inspection_count": str(len(inspections)),
        "inspection_plural": "" if len(inspections) == 1 else "s",
        "need_permit_html": need_permit_html(result, city, state, trade_display, office),
        "permits_html": permit_items_html(result),
        "documents_html": list_html(document_items(result)),
        "fees_rows_html": fees_rows(result),
        "timeline_html": timeline_html(result),
        "department_name": esc(office),
        "portal_html": portal_html(result),
        "sources_html": sources_html(result),
        "inspections_html": inspections_html(result),
        "mistakes_html": list_html(mistakes_items(result)),
        "faq_html": faq_html(faq),
        "department_address": address,
        "department_phone": phone,
        "department_url": url,
        "department_hours": "Verify current counter hours on the official portal before visiting.",
        "latitude": city_info["lat"],
        "longitude": city_info["lng"],
        "last_reviewed": date.today().isoformat(),
    }
    ctx["local_rows_html"] = local_rows(ctx)
    return ctx


def word_count(html_text):
    text = re.sub(r"<script[\s\S]*?</script>", " ", html_text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return len(re.findall(r"\b[\w’'-]+\b", text))


def generate_page(research_permit, city_info, trade_info, force_model=None):
    label = f"{city_info['city']}, {city_info['state']} / {trade_info['display']}"
    print(f"[pseo] researching {label}", flush=True)
    result = normalize_result(research_permit(
        trade_info["job_type"], city_info["city"], city_info["state"], zip_code="", use_cache=True,
        job_category="residential", job_value=None, force_model=force_model,
    ))
    reasons = missing_reasons(result)
    if reasons:
        return {"ok": False, "label": label, "reasons": reasons, "result": result}
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    ctx = build_context(city_info, trade_info, result)
    html_text = render_template(template, ctx)
    out_path = OUTPUT_ROOT / city_info["slug"] / trade_info["slug"] / "index.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_text, encoding="utf-8")
    wc = word_count(html_text)
    return {"ok": True, "label": label, "path": out_path, "url": ctx["canonical_url"].replace(BASE_URL, ""), "word_count": wc}


def write_sitemap(successes):
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    urls = []
    for item in successes:
        loc = f"{BASE_URL}{item['url']}"
        urls.append(f"  <url><loc>{html.escape(loc)}</loc><lastmod>{today}</lastmod><changefreq>weekly</changefreq></url>")
    xml = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n" + "\n".join(urls) + "\n</urlset>\n"
    path = OUTPUT_ROOT / "sitemap.xml"
    path.write_text(xml, encoding="utf-8")
    return path


def log_failures(failures):
    if not failures:
        return None
    lines = []
    stamp = date.today().isoformat()
    for f in failures:
        lines.append(f"{stamp}\t{f['label']}\t{', '.join(f['reasons'])}")
    MISSING_LOG.parent.mkdir(parents=True, exist_ok=True)
    with MISSING_LOG.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return MISSING_LOG


def select_targets(args):
    if args.all or not (args.city or args.state or args.trade):
        return [(c, t) for c in CITIES for t in TRADES]
    city_matches = [c for c in CITIES if c["city"].lower() == (args.city or "").lower() and c["state"].lower() == (args.state or "").lower()]
    trade_matches = [t for t in TRADES if t["slug"] == args.trade or t["display"].lower() == (args.trade or "").lower()]
    if not city_matches or not trade_matches:
        raise SystemExit("Unknown --city/--state/--trade. Use --all for the starter set.")
    return [(city_matches[0], trade_matches[0])]


def main():
    ap = argparse.ArgumentParser(description="Generate PermitAssist pSEO city × trade pages.")
    ap.add_argument("--city")
    ap.add_argument("--state")
    ap.add_argument("--trade", help="Trade slug, e.g. hvac-system")
    ap.add_argument("--all", action="store_true", help="Generate the 4×5 starter set")
    ap.add_argument("--force-model")
    ap.add_argument("--delay", type=float, default=1.0)
    args = ap.parse_args()

    research_permit = load_research_permit()
    targets = select_targets(args)
    successes, failures = [], []
    for idx, (city_info, trade_info) in enumerate(targets, 1):
        item = generate_page(research_permit, city_info, trade_info, force_model=args.force_model)
        if item["ok"]:
            successes.append(item)
            print(f"[pseo] wrote {item['path']} ({item['word_count']} words)", flush=True)
        else:
            failures.append(item)
            print(f"[pseo] FAILED {item['label']}: {', '.join(item['reasons'])}", flush=True)
        if idx < len(targets):
            time.sleep(max(0, args.delay))

    sitemap = write_sitemap(successes)
    missing_log = log_failures(failures)
    counts = [s["word_count"] for s in successes]
    print("\nPSEO GENERATION SUMMARY")
    print(f"generator={ROOT / 'scripts' / 'pseo-generate.py'}")
    print(f"template={TEMPLATE_PATH}")
    print(f"sitemap={sitemap}")
    if missing_log:
        print(f"missing_data_log={missing_log}")
    print(f"successes={len(successes)} failures={len(failures)}")
    if counts:
        print(f"word_range={min(counts)}-{max(counts)}")
    print("generated_pages:")
    for s in successes:
        print(f"- {s['url']} -> {s['path']}")
    if failures:
        print("failed_combos:")
        for f in failures:
            print(f"- {f['label']}: {', '.join(f['reasons'])}")
    if len(targets) == 20 and len(successes) < 15:
        raise SystemExit("Fewer than 15 pages generated successfully; stopping per spec.")


if __name__ == "__main__":
    main()
