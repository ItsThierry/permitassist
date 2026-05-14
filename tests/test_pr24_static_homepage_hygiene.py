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

UNSCOPED_LAUNCH_MODAL_TERMS = [
    'id="upgrade-modal-sub">Unlimited permit lookups,',
    ">Unlimited lookups<",
]

UNSCOPED_PUBLIC_PLAN_TERMS = [
    "Unlimited permit lookups",
    "Unlimited lookups",
]


def test_static_customer_pages_do_not_ship_private_evidence_pack_terms():
    for rel in ["frontend/index.html"]:
        html = (ROOT / rel).read_text(encoding="utf-8").lower()
        leaked = [term for term in PRIVATE_TERMS if term.lower() in html]
        assert leaked == []


def test_upgrade_modal_scopes_unlimited_lookup_copy_to_launch_scope():
    html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    assert "Unlimited launch-scope permit lookups" in html
    assert "Unlimited launch-scope lookups" in html
    leaked = [term for term in UNSCOPED_LAUNCH_MODAL_TERMS if term in html]
    assert leaked == []


def test_public_launch_plan_copy_scopes_unlimited_language():
    for rel in ["frontend/index.html", "frontend/pricing.html", "frontend/help.html"]:
        html = (ROOT / rel).read_text(encoding="utf-8")
        leaked = [term for term in UNSCOPED_PUBLIC_PLAN_TERMS if term in html]
        assert leaked == [], rel
