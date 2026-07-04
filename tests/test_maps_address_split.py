from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from research_engine import build_google_maps_url  # noqa: E402
from server import build_customer_permit_view_model  # noqa: E402


def test_google_maps_url_is_encoded_and_google_prefix_enforced():
    url = build_google_maps_url("Tempe", "AZ", address="123 Main St #5 & <script>alert(1)</script>")
    assert url.startswith("https://www.google.com/maps")
    assert " " not in url
    assert "<" not in url and ">" not in url and "'" not in url and '"' not in url
    assert "%3Cscript%3E" in url or "%3cscript%3e" in url
    assert "123+Main+St" in url


def test_customer_view_model_splits_project_address_maps_from_ahj_office_maps():
    public = build_customer_permit_view_model(
        {
            "permit_decision": "REQUIRED",
            "permit_required": True,
            "permit_verdict": "YES",
            "permit_name": "Electrical Permit",
            "permit_kind": "Electrical",
            "permits_required": [{"permit_type": "Electrical Permit", "family": "electrical", "required": True, "source_url": "https://www.tempe.gov/government/community-development/building-safety"}],
            "applying_office": "City of Tempe Community Development",
            "apply_address": "31 E 5th St, Tempe, AZ",
            "apply_google_maps": "https://www.google.com/maps/search/City+of+Tempe+Community+Development%2C+Tempe%2C+AZ",
            "apply_url": "https://www.tempe.gov/government/community-development/building-safety",
            "source_urls": ["https://www.tempe.gov/government/community-development/building-safety"],
            "job_address": "123 Main St #5 & <x>, Tempe, AZ",
        },
        "residential level 2 EV charger on existing panel",
        "Tempe",
        "AZ",
        job_category="residential",
    )
    assert public["job_address"] == "123 Main St #5 & <x>, Tempe, AZ"
    assert public["job_maps_url"].startswith("https://www.google.com/maps")
    assert "%3Cx%3E" in public["job_maps_url"] or "%3cx%3e" in public["job_maps_url"]
    assert public["apply_address"] == "31 E 5th St, Tempe, AZ"
    assert public["apply_google_maps"] == "https://www.google.com/maps/search/City+of+Tempe+Community+Development%2C+Tempe%2C+AZ"
    contamination_blob = json.dumps({k: public.get(k) for k in ("applying_office", "apply_address", "apply_google_maps")}, sort_keys=True)
    assert "123 Main" not in contamination_blob


def test_frontend_posts_address_and_does_not_inline_raw_maps_onclick():
    html_files = [ROOT / "frontend" / "index.html", ROOT / "frontend" / "preview-modern-reskinned-index.html", *sorted((ROOT / "frontend" / "trades").glob("*.html"))]
    checked_payloads = 0
    for path in html_files:
        text = path.read_text(encoding="utf-8")
        if "/api/permit" in text:
            assert re.search(r"JSON\.stringify\(\{[^}]*\baddress\b", text, flags=re.S), path
            checked_payloads += 1
        assert "onclick=\"window.open('${esc(maps" not in text, path
        assert "openSafeMapsUrl(" in text or "openProjectMaps" in text, path
    assert checked_payloads >= 6
