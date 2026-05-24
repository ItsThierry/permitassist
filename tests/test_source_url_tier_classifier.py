"""Source URL tier classifier and customer-visible source labels."""

from pathlib import Path
import os
import sys

os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from api.research_engine import (  # noqa: E402
    classify_source_tier,
    filter_sources_by_locality,
    is_url_allowed_for_locality,
)
from api.server import _source_dicts, build_claim_citations  # noqa: E402


def test_dallas_source_tiers_are_truthful():
    cases = [
        ("https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/Pages/default.aspx", "ahj", True),
        ("https://www.dallascounty.org/departments/fire-marshal/", "ahj", True),
        ("https://www.tdlr.texas.gov/TABS/Search/Project/TABS2024000000", "state", True),
        ("https://www.texas.gov/", "state", True),
        ("https://www.tx.gov/", "state", True),
        ("https://www.ada.gov/resources/2010-standards/", "universal", True),
        ("https://www.icc-safe.org/products-and-services/i-codes/", "universal", True),
        ("https://www.nfpa.org/codes-and-standards", "universal", True),
        ("https://www.cityofsouthlake.com/123/Building-Inspections", "wrong", False),
        ("https://www.celina-tx.gov/123/Building-Permits", "wrong", False),
        ("https://www.plano.gov/123/Building-Inspections", "wrong", False),
        ("https://www.lacity.org/", "wrong", False),
        ("https://www.phoenix.gov/pdd", "wrong", False),
    ]
    for url, tier, allowed in cases:
        assert classify_source_tier(url, "Dallas", "TX") == tier
        assert is_url_allowed_for_locality(url, "Dallas", "TX") is allowed


def test_state_sources_do_not_cross_state_boundaries():
    assert classify_source_tier("https://www.tx.gov/", "Phoenix", "AZ") == "wrong"
    assert not is_url_allowed_for_locality("https://www.tx.gov/", "Phoenix", "AZ")
    assert classify_source_tier("https://www.az.gov/", "Phoenix", "AZ") == "state"
    assert is_url_allowed_for_locality("https://www.az.gov/", "Phoenix", "AZ")
    assert classify_source_tier("https://www.az.gov/", "Phoenix", "Arizona") == "state"
    assert is_url_allowed_for_locality("https://www.az.gov/", "Phoenix", "Arizona")


def test_vendor_portal_tier_requires_locality_token():
    assert classify_source_tier("https://dallas.aca-prod.accela.com/Default.aspx", "Dallas", "TX") == "ahj"
    assert is_url_allowed_for_locality("https://dallas.aca-prod.accela.com/Default.aspx", "Dallas", "TX")
    assert classify_source_tier("https://plano.aca-prod.accela.com/Default.aspx", "Dallas", "TX") == "wrong"
    assert not is_url_allowed_for_locality("https://plano.aca-prod.accela.com/Default.aspx", "Dallas", "TX")
    assert classify_source_tier("https://aca-prod.accela.com/account123/Default.aspx", "Dallas", "TX") == "wrong"
    assert not is_url_allowed_for_locality("https://aca-prod.accela.com/account123/Default.aspx", "Dallas", "TX")
    assert classify_source_tier("https://aca-prod.accela.com/texas/Default.aspx", "Dallas", "TX") == "wrong"
    assert not is_url_allowed_for_locality("https://aca-prod.accela.com/texas/Default.aspx", "Dallas", "TX")


def test_ahj_allowlist_accepts_normalized_state_name():
    url = "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/Pages/default.aspx"
    assert classify_source_tier(url, "Dallas", "Texas") == "ahj"
    assert is_url_allowed_for_locality(url, "Dallas", "Texas")


def test_existing_filter_keeps_state_and_universal_but_drops_wrong_city():
    urls = [
        "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/Pages/default.aspx",
        "https://www.tdlr.texas.gov/TABS/Search/Project/TABS2024000000",
        "https://www.ada.gov/resources/2010-standards/",
        "https://www.cityofsouthlake.com/123/Building-Inspections",
    ]
    assert filter_sources_by_locality(urls, "Dallas", "TX") == urls[:3]


def test_filter_sources_by_locality_preserves_labeled_source_objects():
    sources = [
        {"url": "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/Pages/default.aspx", "title": "Official Dallas source"},
        {"url": "https://www.tdlr.texas.gov/TABS/Search/Project/TABS2024000000", "title": "Texas state reference"},
        {"url": "https://www.ada.gov/resources/2010-standards/", "title": "National code reference"},
        {"url": "https://www.cityofsouthlake.com/123/Building-Inspections", "title": "Official Southlake source"},
    ]
    kept = filter_sources_by_locality(sources, "Dallas", "TX")
    assert kept == sources[:3]
    assert [s["title"] for s in kept] == [
        "Official Dallas source",
        "Texas state reference",
        "National code reference",
    ]


def test_source_dicts_labels_each_tier_and_drops_wrong_city():
    result = {
        "sources": [
            "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/Pages/default.aspx",
            "https://www.tdlr.texas.gov/TABS/Search/Project/TABS2024000000",
            "https://www.ada.gov/resources/2010-standards/",
            "https://www.cityofsouthlake.com/123/Building-Inspections",
        ]
    }
    sources = _source_dicts(result, city="Dallas", state="TX")
    assert [s["title"] for s in sources] == [
        "Official Dallas source",
        "Texas state reference",
        "National code reference",
    ]
    assert all("southlake" not in s["url"].lower() for s in sources)


def test_source_dicts_replaces_generic_upstream_titles_but_preserves_specific_titles():
    result = {
        "sources": [
            {"url": "https://www.tdlr.texas.gov/TABS/Search/Project/TABS2024000000", "title": "Official source"},
            {"url": "https://www.ada.gov/resources/2010-standards/", "title": "2010 ADA Standards"},
            {"url": "https://www.tdlr.texas.gov/TABS/Search/Project/TABS2024000000", "title": "Official Dallas Building Department"},
        ]
    }
    sources = _source_dicts(result, city="Dallas", state="TX")
    assert sources[0]["title"] == "Texas state reference"
    assert sources[1]["title"] == "2010 ADA Standards"
    assert sources[2]["title"] == "Texas state reference"


def test_source_dicts_cached_dict_shape_rerenders_without_title_corruption():
    result = {
        "sources": [
            {"url": "https://www.tdlr.texas.gov/TABS/Search/Project/TABS2024000000", "title": "Texas state reference"},
            {"url": "https://www.ada.gov/resources/2010-standards/", "title": "National code reference"},
        ]
    }
    sources = _source_dicts(result, city="Dallas", state="TX")
    assert [s["title"] for s in sources] == ["Texas state reference", "National code reference"]


def test_build_claim_citations_with_locality_drops_wrong_city_source_url():
    result = {
        "city": "Dallas",
        "state": "TX",
        "confidence": "high",
        "apply_url": "https://www.cityofsouthlake.com/123/Building-Inspections",
        "fee_range": "$1,000-$2,000",
        "permits_required": [{"permit_type": "Building Permit — Commercial TI"}],
        "sources": ["https://www.cityofsouthlake.com/123/Building-Inspections"],
    }
    citations = build_claim_citations(result, city="Dallas", state="TX")
    assert citations
    assert all("southlake" not in c["source_url"].lower() for c in citations)
    assert all(c["confidence"] == "needs_verification" for c in citations)


def test_source_dicts_without_location_does_not_guess_official_local_source():
    result = {"sources": [
        "https://www.tdlr.texas.gov/TABS/Search/Project/TABS2024000000",
        "https://www.ada.gov/resources/2010-standards/",
    ]}
    sources = _source_dicts(result)
    assert [s["title"] for s in sources] == ["National code reference"]
