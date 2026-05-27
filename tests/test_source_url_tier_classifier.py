"""Source URL tier classifier and customer-visible source labels."""

from pathlib import Path
import os
import sys

os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from api.research_engine import (  # noqa: E402
    apply_source_locality_hard_block,
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


def test_live_e2e_apply_url_rejects_fee_schedule_and_state_licensing_pages():
    cases = [
        ("Dallas", "TX", "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/Pages/permit-fees.aspx"),
        ("San Jose", "CA", "https://www.cslb.ca.gov/contractors/applicants/"),
        ("Dallas", "TX", "https://www.tdlr.texas.gov/TABS/Search/Project/TABS2024000000"),
    ]
    for city, state, apply_url in cases:
        result = {"apply_url": apply_url, "sources": [apply_url], "applying_office": f"{city} Building Department"}

        apply_source_locality_hard_block(result, city, state, job_type="commercial tenant improvement")

        assert result.get("apply_url") in (None, "")
        assert apply_url not in str(result.get("apply_path") or {})


def test_locality_hard_block_customer_fields_do_not_leak_ahj_surrender_language():
    wrong_url = "https://www.cityofsouthlake.com/123/Building-Inspections"
    result = {
        "apply_url": wrong_url,
        "apply_path": {"portal_url": wrong_url, "verification_note": "stale"},
        "claim_citations": [{
            "id": "C1",
            "claim": "Permit type",
            "quoted_snippet": "Office TI permits are required.",
            "source_url": wrong_url,
            "source_title": "Wrong city source",
            "confidence": "medium",
        }],
    }

    apply_source_locality_hard_block(result, "Dallas", "TX", job_type="office tenant improvement")

    customer_blob = str({
        "apply_path": result.get("apply_path"),
        "claim_citations": result.get("claim_citations"),
    }).lower()
    assert wrong_url.lower() not in customer_blob
    assert "ahj" not in customer_blob
    assert "verify directly" not in customer_blob
    assert "not verified" not in customer_blob


def test_live_e2e_wrong_locality_urls_do_not_survive_as_local_sources_or_apply_urls():
    cases = [
        ("Dallas", "TX", "https://www.fortworthtexas.gov/departments/development-services/permits"),
        ("Pasadena", "CA", "https://www.southpasadenaca.gov/government/departments/planning-building/building-permits"),
    ]
    for city, state, wrong_url in cases:
        result = {
            "apply_url": wrong_url,
            "sources": [wrong_url],
            "fee_source": wrong_url,
            "building_department_contact_source": wrong_url,
            "fee_range": f"Verify fees at {wrong_url}",
        }

        apply_source_locality_hard_block(result, city, state, job_type="office tenant improvement")

        assert result.get("apply_url") in (None, "")
        assert result.get("sources") == []
        assert result.get("fee_source") in (None, {})
        assert result.get("building_department_contact_source") in (None, {})
        assert wrong_url not in result.get("fee_range", "")


def test_live_e2e_state_licensing_board_sources_are_not_primary_claim_citations():
    cases = [
        ("Pasadena", "CA", "https://www.cslb.ca.gov/contractors/"),
        ("San Jose", "CA", "https://www.cslb.ca.gov/contractors/"),
        ("Los Angeles", "CA", "https://www.cslb.ca.gov/contractors/"),
        ("Dallas", "TX", "https://www.tdlr.texas.gov/TABS/Search/Project/TABS2024000000"),
    ]

    for city, state, licensing_url in cases:
        result = {
            "city": city,
            "state": state,
            "confidence": "high",
            "apply_url": licensing_url,
            "fee_range": "$500-$1,500",
            "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Interior Alteration"}],
            "sources": [licensing_url],
        }

        apply_source_locality_hard_block(result, city, state, job_type="commercial tenant improvement")
        citations = build_claim_citations(result, city=city, state=state)

        assert result.get("apply_url") in (None, "")
        assert all(licensing_url.lower() != c.get("source_url", "").lower() for c in citations)
        assert all(c["confidence"] == "needs_verification" for c in citations)
