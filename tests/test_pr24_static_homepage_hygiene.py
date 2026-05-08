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


def test_static_customer_pages_do_not_ship_private_evidence_pack_terms():
    for rel in ["frontend/index.html"]:
        html = (ROOT / rel).read_text(encoding="utf-8").lower()
        leaked = [term for term in PRIVATE_TERMS if term.lower() in html]
        assert leaked == []
