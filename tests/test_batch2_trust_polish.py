from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
INDEX = FRONTEND / "index.html"
PRICING = FRONTEND / "pricing.html"
HELP = FRONTEND / "help.html"
ACCOUNT = FRONTEND / "account.html"
TRADE_PAGES = sorted((FRONTEND / "trades").glob("*.html"))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_team_price_is_consistent_on_customer_facing_pages():
    pages = [PRICING, HELP, ACCOUNT, *TRADE_PAGES]
    combined = "\n".join(_read(path) for path in pages if path.exists())

    assert "Team — $79.99/mo" in combined
    assert "Team ($79.99/mo)" in combined
    assert "Team — $49/mo" not in combined
    assert "Team ($49/mo)" not in combined
    assert "$49/mo" not in combined


def test_plan_names_are_solo_and_team_not_mixed_with_pro_plan_copy():
    pages = [INDEX, PRICING, *TRADE_PAGES]
    combined = "\n".join(_read(path) for path in pages if path.exists())

    assert "Everything included with Solo" in combined
    assert "Everything in Solo" in combined
    assert "Upgrade to Solo" in combined
    assert "Everything included with Pro" not in combined
    assert "Everything in Pro" not in combined
    assert "Upgrade to Pro" not in combined


def test_homepage_uses_customer_friendly_real_lookup_cta_and_no_launch_focus_block():
    source = _read(INDEX)
    pricing = _read(PRICING)
    help_page = _read(HELP)
    cities = _read(ROOT / "frontend" / "cities.html")
    login = _read(ROOT / "frontend" / "login.html")
    public_pages = "\n".join([source, pricing, help_page, cities, login])

    assert "See a real permit lookup" in source
    assert "See a restaurant TI sample" not in source
    assert "restaurant TI sample" not in source
    assert "launch-scope-card" not in source
    assert "Launch focus:" not in source
    assert "Strongest today for restaurant TI" not in source
    assert "Launch focus:" not in public_pages
    assert "Current Launch Scope" not in public_pages
    assert "Strongest today" not in public_pages
    assert "strongest today" not in public_pages
    assert "Launch-scope support" not in public_pages
    assert "launch-scope" not in public_pages
    assert "Launch-scope" not in public_pages
    assert "restaurant, medical clinic" not in public_pages
    assert "restaurant tenant improvements, medical clinic tenant improvements, and office tenant improvements" not in public_pages


def test_result_and_report_views_show_beta_safe_guidance_warning():
    source = _read(INDEX)

    warning = "PermitAssist is guidance only."
    verify = "Verify final permit type with the AHJ before quoting or starting work."
    assert source.count(warning) >= 2
    assert verify in source
    assert "PERMITASSIST GUIDANCE ONLY: Verify final permit type with the AHJ before quoting or starting work." in source

    standard_warning_pos = source.index('class="disclaimer-box"')
    hero_pos = source.index('html += `<div class="result-hero">')
    assert standard_warning_pos < hero_pos

    report_header_pos = source.index("PermitAssist Report v1.0")
    report_warning_pos = source.index("PermitAssist is guidance only.", report_header_pos)
    first_field_pos = source.index("// The 18 fields — locked order")
    assert report_header_pos < report_warning_pos < first_field_pos


def test_license_requirement_copy_normalizes_bad_plural_grammar():
    source = _read(INDEX)

    assert "function normalizeLicenseRequirementCopy" in source
    assert "Massachusetts-Licensed" not in source
    assert ")s pull" not in source
    assert "contractor)s" not in source
    assert "plumber)s" not in source
    assert "licenseDisplayForReport = normalizeLicenseRequirementCopy(license);" in source
