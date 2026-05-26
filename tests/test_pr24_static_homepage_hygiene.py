#!/usr/bin/env python3
"""PR24 static homepage hygiene regression tests.

The production 25-case smoke treats private evidence-pack implementation terms as
customer-facing leaks if they appear in static HTML. The homepage can still read
runtime fields from the API; it just must not ship the literal private key names
in source text.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_TERMS = [
    "preview_token",
    "controlled_preview_token",
    "solar_mep_controlled_preview",
    "fingerprint_valid",
    "contract_status",
    "PERMITASSIST_EVIDENCE_PACK_PREVIEW_TOKEN",
    "_evidence_pack",
]
CUSTOMER_FORBIDDEN_COPY = [
    "Engine flagged this answer",
    "Verified · official sources",
    "Needs review</span>",
    "needs_review",
    "Planning estimate only",
]
CUSTOMER_REPORT_FORBIDDEN_COPY = [
    "${verdict} permit status",
    "No source links stored in this share.",
    "word-break:break-all",
]


def test_static_customer_pages_do_not_ship_private_evidence_pack_terms():
    for rel in ["frontend/index.html"]:
        html = (ROOT / rel).read_text(encoding="utf-8").lower()
        leaked = [term for term in PRIVATE_TERMS if term.lower() in html]
        assert leaked == []


def test_customer_result_static_copy_uses_soft_public_trust_labels():
    html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")

    leaked = [term for term in CUSTOMER_FORBIDDEN_COPY if term in html]
    assert leaked == []

    assert "Verify before filing" in html
    assert "Official path found" in html
    assert "Planning estimate" in html


def test_customer_static_pages_do_not_ship_internal_engine_labels():
    pages = [ROOT / "frontend/index.html", ROOT / "frontend/report.html"]
    pages.extend(sorted((ROOT / "frontend/trades").glob("*.html")))

    leaks = {}
    for page in pages:
        html = page.read_text(encoding="utf-8")
        page_leaks = [term for term in CUSTOMER_FORBIDDEN_COPY if term in html]
        if page_leaks:
            leaks[str(page.relative_to(ROOT))] = page_leaks

    assert leaks == {}


def test_shared_report_static_copy_uses_clean_labels_and_friendly_sources():
    html = (ROOT / "frontend/report.html").read_text(encoding="utf-8")

    leaked = [term for term in CUSTOMER_REPORT_FORBIDDEN_COPY if term in html]
    assert leaked == []
    assert "Permit required: Yes" in html
    assert "sourceHost" in html


def test_homepage_disambiguation_does_not_append_raw_choice_parentheses():
    html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")

    assert "isBroadProjectScope" in html
    assert "query + ' (' + choice + ')'" not in html
    assert "disambiguation_hint" in html
