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

