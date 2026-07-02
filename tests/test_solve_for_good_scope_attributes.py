from __future__ import annotations

import json
from pathlib import Path

import pytest
import sys

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))
ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_fable5_customer_pov_20260702T121009Z"

from project_scope_attributes import (  # noqa: E402
    AttributeValue,
    Occupancy,
    ProjectScopeAttributes,
    SignIllumination,
    WorkNature,
    extract_project_scope_attributes,
)


def _case(case_id: str) -> dict:
    for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec["case"]["id"] == case_id:
            return rec["case"]
    raise AssertionError(f"case not found: {case_id}")


def _attrs(case_id: str) -> ProjectScopeAttributes:
    c = _case(case_id)
    return extract_project_scope_attributes(c["job_type"], c["city"], c["state"], job_category=c.get("segment"))


def test_r033_gfci_is_residential_electrical_not_food_service():
    attrs = _attrs("R-033")
    assert attrs.occupancy == Occupancy.RESIDENTIAL
    assert "electrical" in attrs.trades
    assert attrs.food_service == AttributeValue.FALSE
    assert attrs.change_of_use == AttributeValue.FALSE
    assert attrs.work_nature in {WorkNature.REPLACEMENT, WorkNature.ALTERATION, WorkNature.LIKE_FOR_LIKE_REPLACEMENT}
    assert "existing_circuits" in attrs.negative_facts


def test_c010_illuminated_sign_requires_sign_and_electrical_attributes():
    attrs = _attrs("C-010")
    assert attrs.occupancy == Occupancy.COMMERCIAL
    assert attrs.sign_illuminated == SignIllumination.TRUE
    assert "sign" in attrs.project_features
    assert "electrical" in attrs.trades
    assert attrs.change_of_use == AttributeValue.FALSE
    assert attrs.food_service == AttributeValue.FALSE


def test_r034_battery_storage_is_ess_not_new_roof_pv_structural():
    attrs = _attrs("R-034")
    assert attrs.occupancy == Occupancy.RESIDENTIAL
    assert "battery_storage" in attrs.project_features
    assert "electrical" in attrs.trades
    assert attrs.existing_solar_context == AttributeValue.TRUE
    assert attrs.new_solar_panels == AttributeValue.FALSE
    assert attrs.roof_penetrations == AttributeValue.FALSE
    assert attrs.structural_mounting == AttributeValue.UNKNOWN
    assert "solar_pv" not in attrs.project_features
    assert "solar_pv" not in attrs.positive_facts


@pytest.mark.parametrize(
    ("job", "expected_illum", "expected_electrical"),
    [
        ("install non-illuminated monument sign face only no electrical work", SignIllumination.FALSE, False),
        ("install illuminated wall sign for retail tenant", SignIllumination.TRUE, True),
    ],
)
def test_illuminated_vs_non_illuminated_sign_counterfactual(job, expected_illum, expected_electrical):
    attrs = extract_project_scope_attributes(job, "Gilbert", "AZ", job_category="commercial")
    assert attrs.sign_illuminated == expected_illum
    assert ("electrical" in attrs.trades) is expected_electrical


@pytest.mark.parametrize(
    ("job", "expected_occupancy", "food"),
    [
        ("replace 12 kitchen outlets with GFCI in existing boxes", Occupancy.RESIDENTIAL, AttributeValue.FALSE),
        ("convert retail suite to restaurant with commercial kitchen and grease interceptor", Occupancy.COMMERCIAL, AttributeValue.TRUE),
    ],
)
def test_residential_kitchen_word_does_not_equal_food_service(job, expected_occupancy, food):
    attrs = extract_project_scope_attributes(job, "St. Louis", "MO", job_category="residential" if expected_occupancy == Occupancy.RESIDENTIAL else "commercial")
    assert attrs.occupancy == expected_occupancy
    assert attrs.food_service == food


def test_all_live100_intakes_produce_closed_enum_schema_no_freeform_values():
    records = [json.loads(line)["case"] for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines() if line.strip()]
    assert len(records) == 100
    for case in records:
        attrs = extract_project_scope_attributes(case["job_type"], case["city"], case["state"], job_category=case.get("segment"))
        as_dict = attrs.as_dict()
        assert as_dict["schema_version"] == "project_scope_attributes.v1"
        assert attrs.occupancy in set(Occupancy)
        assert attrs.work_nature in set(WorkNature)
        assert attrs.change_of_use in set(AttributeValue)
        assert attrs.food_service in set(AttributeValue)
        assert attrs.confidence in {"high", "medium", "low"}
        # Unknowns are allowed; wrong positive free-form strings are not.
        assert all(isinstance(item, str) and item for item in attrs.unknowns)
