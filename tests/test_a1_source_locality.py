"""A1 source locality hard-block regressions.

These are deterministic unit tests: no live LLM/API calls.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from api.research_engine import (  # noqa: E402
    apply_source_locality_hard_block,
    filter_sources_by_locality,
    is_url_allowed_for_locality,
    sanitize_free_text_urls,
)


def _base_result(**overrides):
    result = {
        "applying_office": "Dallas Building Inspection",
        "building_dept_phone": "214-948-4480",
        "sources": [],
    }
    result.update(overrides)
    return result


def test_dallas_restaurant_ti_rejects_lebanon_indiana_source():
    sources = ["https://lebanon.in.gov/egov/documents/permit-fees.pdf"]
    assert filter_sources_by_locality(sources, "Dallas", "TX") == []


def test_dallas_restaurant_ti_rejects_govinfo_source_and_confidence_reason_url():
    result = _base_result(
        sources=["https://www.govinfo.gov/content/pkg/CFR-2024-title24/pdf/CFR-2024-title24.pdf"],
        confidence_reason="Fee confidence cited https://www.govinfo.gov/content/pkg/foo.pdf",
    )
    apply_source_locality_hard_block(result, "Dallas", "TX")
    assert result["sources"] == []
    assert "govinfo.gov" not in result["confidence_reason"]


def test_la_hillside_adu_rejects_louisiana_ldh_source():
    assert not is_url_allowed_for_locality(
        "https://ldh.la.gov/assets/oph/Center-EH/sanitarian/food-safety.pdf",
        "Los Angeles",
        "CA",
    )


def test_la_hillside_adu_rejects_la_county_public_works_regression():
    result = {
        "applying_office": "Los Angeles Department of Building and Safety",
        "sources": ["https://pw.lacounty.gov/building-and-safety/fee-schedule.pdf"],
        "fee_source": "https://pw.lacounty.gov/building-and-safety/fee-schedule.pdf",
        "fee_range": "Verify in https://pw.lacounty.gov/building-and-safety/fee-schedule.pdf",
    }
    apply_source_locality_hard_block(result, "Los Angeles", "CA")
    assert result["sources"] == []
    assert result["fee_source"] is None
    assert "pw.lacounty.gov" not in result["fee_range"]


def test_bend_adu_rejects_la_county_public_works_apply_url_regression():
    result = {
        "applying_office": "City of Bend Building Safety Division",
        "apply_url": "https://pw.lacounty.gov/building-and-safety/permits",
        "sources": ["https://bendoregon.gov/services/permits-licenses/adu-resources-hub/"],
    }
    apply_source_locality_hard_block(result, "Bend", "OR")
    assert result["apply_url"] is None
    assert result["sources"] == ["https://bendoregon.gov/services/permits-licenses/adu-resources-hub/"]
    assert result["_apply_url_locality_warning"].startswith("The online application URL did not match Bend, OR")


def test_valid_apply_url_clears_stale_locality_warning():
    result = {
        "applying_office": "City of Bend Building Safety Division",
        "apply_url": "https://bendoregon.gov/services/permits-licenses/adu-resources-hub/",
        "sources": [],
        "_apply_url_locality_warning": "stale warning",
    }
    apply_source_locality_hard_block(result, "Bend", "OR")
    assert result["apply_url"].startswith("https://bendoregon.gov")
    assert "_apply_url_locality_warning" not in result


def test_non_http_apply_urls_are_ignored_without_warning_or_crash():
    for url in ("", "mailto:permits@example.gov", "tel:5551212"):
        result = {"apply_url": url, "sources": []}
        apply_source_locality_hard_block(result, "Bend", "OR")
        assert result["apply_url"] == url
        assert "_apply_url_locality_warning" not in result


def test_fredericksburg_tx_official_abbreviation_domain_is_allowed():
    url = "https://www.fbgtx.org/890/Permit-Applications"
    assert is_url_allowed_for_locality(url, "Fredericksburg", "TX")
    assert filter_sources_by_locality([url], "Fredericksburg", "TX") == [url]


def test_fredericksburg_tx_abbreviation_domain_not_allowed_for_virginia_city():
    url = "https://www.fbgtx.org/890/Permit-Applications"
    assert not is_url_allowed_for_locality(url, "Fredericksburg", "VA")
    assert filter_sources_by_locality([url], "Fredericksburg", "VA") == []


def test_unseeded_state_level_source_without_city_token_is_not_ahj_proof():
    assert not is_url_allowed_for_locality("https://www.oregon.gov/bcd/Pages/permits.aspx", "Bend", "OR")


def test_known_vendor_apply_url_is_not_dropped_without_city_host_token():
    result = {
        "applying_office": "Example Building Department",
        "apply_url": "https://aca-prod.accela.com/example/Default.aspx",
        "sources": ["https://example.gov/building/permits"],
    }
    apply_source_locality_hard_block(result, "Example", "EX")
    assert result["apply_url"] == "https://aca-prod.accela.com/example/Default.aspx"


def test_vendor_tenant_subdomain_with_city_token_is_allowed():
    assert is_url_allowed_for_locality("https://bend.viewpointcloud.com/categories/1082", "Bend", "OR")


def test_vendor_added_after_source_classifier_still_reaches_token_check():
    assert is_url_allowed_for_locality("https://bend.tylerhost.net/energovprod/selfservice", "Bend", "OR")


def test_vendor_specificity_prefers_longest_matching_suffix():
    assert is_url_allowed_for_locality("https://bend.aca-prod.accela.com/Default.aspx", "Bend", "OR")
    assert not is_url_allowed_for_locality("https://southbend.aca-prod.accela.com/Default.aspx", "Bend", "OR")


def test_known_vendor_apply_url_without_city_token_is_dropped_even_if_prose_matches():
    result = {
        "applying_office": "City of Bend Accela Building Portal",
        "portal_name": "Accela Bend online permits",
        "apply_url": "https://aca-prod.accela.com/account123/Default.aspx",
        "sources": ["https://bendoregon.gov/services/permits-licenses/adu-resources-hub/"],
    }
    apply_source_locality_hard_block(result, "Bend", "OR")
    assert result["apply_url"] is None


def test_known_vendor_apply_url_is_dropped_when_path_points_to_wrong_jurisdiction():
    result = {
        "applying_office": "City of Bend Building Safety Division",
        "apply_url": "https://aca-prod.accela.com/lacounty/Default.aspx",
        "sources": ["https://bendoregon.gov/services/permits-licenses/adu-resources-hub/"],
    }
    apply_source_locality_hard_block(result, "Bend", "OR")
    assert result["apply_url"] is None


def test_known_vendor_apply_url_does_not_substring_match_wrong_city():
    result = {
        "applying_office": "City of Bend Building Safety Division",
        "apply_url": "https://aca-prod.accela.com/southbend/Default.aspx",
        "sources": ["https://bendoregon.gov/services/permits-licenses/adu-resources-hub/"],
    }
    apply_source_locality_hard_block(result, "Bend", "OR")
    assert result["apply_url"] is None


def test_known_vendor_apply_url_does_not_directional_match_wrong_city():
    result = {
        "applying_office": "City of Bend Building Safety Division",
        "apply_url": "https://north-bend.viewpointcloud.com/categories/permits",
        "sources": ["https://bendoregon.gov/services/permits-licenses/adu-resources-hub/"],
    }
    apply_source_locality_hard_block(result, "Bend", "OR")
    assert result["apply_url"] is None


def test_non_vendor_official_fallback_does_not_substring_match_wrong_city():
    assert not is_url_allowed_for_locality("https://southbend.or.gov/permits", "Bend", "OR")
    assert not is_url_allowed_for_locality("https://north-bend.or.gov/permits", "Bend", "OR")


def test_phoenix_commercial_rejects_non_ahj_residential_solar_domain():
    result = _base_result(
        sources=["https://www.gosolarapp.org/solarapp/residential-solar-permit"],
        fee_range="Solar fee at https://www.gosolarapp.org/solarapp/residential-solar-permit",
    )
    apply_source_locality_hard_block(result, "Phoenix", "AZ")
    assert result["sources"] == []
    assert "gosolarapp.org" not in result["fee_range"]


def test_universal_allow_ada_gov_survives_dallas_commercial_scope():
    sources = ["https://www.ada.gov/resources/2010-standards/"]
    assert filter_sources_by_locality(sources, "Dallas", "TX") == sources


def test_universal_allow_icc_safe_survives_any_scope():
    sources = ["https://www.icc-safe.org/products-and-services/i-codes/2021-i-codes/ibc/"]
    assert filter_sources_by_locality(sources, "Phoenix", "AZ") == sources


def test_ladbs_survives_la_but_rejects_on_dallas():
    url = "https://www.ladbs.org/services/core-services/plan-check-permit/plan-check-permit-special-assistance/permit-fees"
    assert filter_sources_by_locality([url], "Los Angeles", "CA") == [url]
    assert filter_sources_by_locality([url], "Dallas", "TX") == []


def test_source_fields_and_watch_out_are_cleaned_before_render():
    result = _base_result(
        apply_url_source="https://lebanon.in.gov/apply",
        code_source="https://www.icc-safe.org/products-and-services/i-codes/",
        watch_out="Do not verify Dallas fees at https://lebanon.in.gov/fees.pdf",
        pro_tip="ADA path citation https://ada.gov/resources/2010-standards/ is valid.",
    )
    apply_source_locality_hard_block(result, "Dallas", "TX")
    assert result["apply_url_source"] is None
    assert result["code_source"].startswith("https://www.icc-safe.org")
    assert "lebanon.in.gov" not in result["watch_out"]
    assert "ada.gov" in result["pro_tip"]


def test_sanitize_free_text_urls_strips_official_but_wrong_state_urls():
    result = _base_result(
        fee_range="Verify Dallas TI fees at https://lebanon.in.gov/fees.pdf",
        confidence_reason="Bad source https://ldh.la.gov/foo.pdf",
    )
    sanitize_free_text_urls(result, "Dallas", "TX")
    assert "lebanon.in.gov" not in result["fee_range"]
    assert "ldh.la.gov" not in result["confidence_reason"]

