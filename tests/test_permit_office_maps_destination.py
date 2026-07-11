from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SURFACES = [
    ROOT / "frontend" / "index.html",
    ROOT / "frontend" / "preview-modern-reskinned-index.html",
    ROOT / "frontend" / "trades" / "electrical.html",
    ROOT / "frontend" / "trades" / "hvac.html",
    ROOT / "frontend" / "trades" / "plumbing.html",
    ROOT / "frontend" / "trades" / "roofing.html",
    ROOT / "frontend" / "trades" / "solar.html",
]


def test_all_customer_surfaces_build_a_destination_only_permit_office_map() -> None:
    for path in SURFACES:
        text = path.read_text(encoding="utf-8")
        assert "function buildPermitOfficeMapsUrl(" in text, path
        assert "function safePermitOfficeMapsUrl(" in text, path
        assert "function openPermitOfficeMaps(" in text, path
        assert re.search(
            r"const maps = safePermitOfficeMapsUrl\(d\.apply_google_maps \|\| d\.maps_url \|\| '', city, state, office, officeAddress\);",
            text,
        ), path
        assert "maps || 'https://www.google.com/maps'" not in text, path
        assert 'data-maps-url="${esc(maps)}" onclick="openPermitOfficeMaps(this.dataset.mapsUrl)"' in text, path
        safe_assignment = "const maps = safePermitOfficeMapsUrl(d.apply_google_maps || d.maps_url || '', city, state, office, officeAddress);"
        assert text.count(safe_assignment) >= 2, path  # result rendering + no-phone contact fallback


def test_contact_department_name_and_address_link_to_the_same_office_destination() -> None:
    for path in SURFACES:
        text = path.read_text(encoding="utf-8")
        assert 'class="contact-office-link" href="${esc(maps)}"' in text, path
        assert 'class="contact-address-link" href="${esc(maps)}"' in text, path
        assert 'target="_blank" rel="noopener"' in text, path
