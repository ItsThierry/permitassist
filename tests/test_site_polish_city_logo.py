#!/usr/bin/env python3
"""Regression coverage for small launch-site polish routes."""

import os
import sys

os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-a-real-key")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api import server  # noqa: E402


def test_city_slug_to_display_formats_legacy_city_routes():
    assert server.city_slug_to_display("houston-tx") == ("Houston", "TX")
    assert server.city_slug_to_display("los-angeles-ca") == ("Los Angeles", "CA")


def test_city_slug_to_display_rejects_path_traversal_and_bad_slugs():
    assert server.city_slug_to_display("../secrets") is None
    assert server.city_slug_to_display("houston") is None
    assert server.city_slug_to_display("houston-texas") is None


def test_render_city_landing_page_links_to_canonical_permit_pages():
    body = server.render_city_landing_page("houston-tx")
    assert body is not None
    html = body.decode("utf-8")
    assert "Permit requirements in Houston, TX" in html
    assert "/permits/hvac/houston-tx" in html
    assert "/permits/electrical/houston-tx" in html
    assert "/logo.png" in html
    assert "Run a free lookup" in html


def test_logo_asset_exists_for_root_logo_route():
    logo_path = os.path.join(server.FRONTEND_DIR, "icons", "logo.png")
    assert os.path.isfile(logo_path)
    assert os.path.getsize(logo_path) > 0
