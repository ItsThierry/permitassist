#!/usr/bin/env python3
"""BG-POLISH-01 frontend contract tests."""

from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "index.html"


def _html() -> str:
    return FRONTEND.read_text(encoding="utf-8")


def _function_slice(html: str, name: str, next_name: str) -> str:
    start = html.index(f"function {name}")
    end = html.index(f"function {next_name}", start)
    return html[start:end]


def test_task2_fallback_jurisdiction_copy_disambiguates_registry_vs_live_sources_without_verified_claim():
    html = _html()
    body = _function_slice(html, "getJurisdictionDisplay", "getNextSteps")

    assert "hasOfficialLocalResearchSource" in body
    assert "isn't yet in PermitAssist's registry" in body
    assert "official ${city} sources found via live web research" in body
    assert "verified registry" not in body.lower()
    assert "verified" not in body.lower()
    assert "City match for ${city}, ${state}" in body  # gated AHJ city match branch preserved
    assert "State/fallback match for ${city}, ${state}" in body  # pure state fallback branch preserved


def test_task2_official_local_research_source_requires_city_specific_match():
    html = _html()
    body = _function_slice(html, "hasOfficialLocalResearchSource", "normalizeCustomerWarningItems")

    assert "cityMatched && officialSignal" in body
    assert "cityToken.length >= 4" in body
    assert "compactHost.includes(cityToken)" in body
    assert "compactTitle.includes(cityToken)" in body
    assert "url) ||\n      /official|building|permit" not in body


def test_task3_customer_copy_blacklist_and_footer_contracts():
    html = _html()
    lowered = html.lower()

    assert "verified via permitassist" not in lowered
    assert "verify in before" not in lowered
    assert "queue.." not in html
    assert "Prepared with PermitAssist" in html
    assert "paid to the applicable permitting authority" in html
    assert "paid to the city, not to your contractor" not in html


def test_task3_watch_out_composer_excludes_permit_card_rationale_fields():
    html = _html()
    body = html[html.index("// Watch Out / Common mistakes"):html.index("// Permit list", html.index("// Watch Out / Common mistakes"))]

    assert "normalizeCustomerWarningItems" in html
    assert "normalizeCustomerWarningItems(mistakes, d.watch_out || [])" in body
    assert "conditionalNotes" not in body
    assert ".filter(p => p.notes" not in body
    assert "Required\\s+(?:for|because)" in html
    assert "companion permits are suppressed" in html


def test_task3_companion_filter_does_not_hide_missing_certainty_companions():
    html = _html()

    assert "function visibleCompanionPermits" in html
    assert "if (!certainty) return true" in html
    assert ".filter(cp => cp.certainty === 'almost_certain' || cp.certainty === 'likely')" not in html
    assert html.count("visibleCompanionPermits(d)") >= 3


def test_task3_frontend_keeps_raw_escaper_separate_from_customer_copy_normalizer():
    html = _html()
    esc_body = _function_slice(html, "esc", "sourceUrl")

    assert "function normalizeCustomerVisibleCopy" in html
    assert "(?<!" not in html
    assert "function escCopy" in esc_body
    assert "normalizeCustomerVisibleCopy(s)" in esc_body
    assert "function esc(s){return String(s ?? '')" in esc_body
    assert "verify with the building department before quoting" in html
