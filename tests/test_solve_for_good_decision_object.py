from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))
ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_fable5_customer_pov_20260702T121009Z"

from closed_world_decision import (  # noqa: E402
    DecisionStatus,
    apply_closed_world_customer_contract,
    compose_decision_object,
)
from scope_contract import build_scope_facts_v2  # noqa: E402
from public_packet import apply_public_packet_projection  # noqa: E402


def _record(case_id: str) -> dict:
    for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec["case"]["id"] == case_id:
            return rec
    raise AssertionError(f"case not found: {case_id}")


def _families(public: dict, status: str = "REQUIRED") -> set[str]:
    rows = public.get("permits_required") if status == "REQUIRED" else public.get("conditional_permits")
    return {str(r.get("family") or r.get("filing_family") or "") for r in rows or [] if isinstance(r, dict)}


def _names(public: dict, status: str = "REQUIRED") -> list[str]:
    rows = public.get("permits_required") if status == "REQUIRED" else public.get("conditional_permits")
    return [str(r.get("permit_name") or r.get("permit_type") or "") for r in rows or [] if isinstance(r, dict)]


def _apply(case_id: str) -> dict:
    rec = _record(case_id)
    case = rec["case"]
    return apply_closed_world_customer_contract(
        rec["response_body"],
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case.get("segment"),
    )


def test_fable5_public_packet_apply_path_fallback_status_is_honest_and_generic_sources_not_promoted():
    official = apply_public_packet_projection(
        {
            "permit_decision": "REQUIRED",
            "permit_required": True,
            "permits_required": [{"permit_name": "Building Permit", "family": "building"}],
            "source_urls": ["https://www.examplecity.gov/building-permits"],
            "applying_office": "Example City Building Department",
        },
        {"segment": "commercial"},
    )
    assert official.get("apply_url") == "https://www.examplecity.gov/building-permits"
    assert official["apply_path"]["typed_status"] == "OFFICIAL_SOURCE_FALLBACK"
    assert official["apply_path"]["channel"] == "official_source"

    generic = apply_public_packet_projection(
        {
            "permit_decision": "REQUIRED",
            "permit_required": True,
            "permits_required": [{"permit_name": "Building Permit", "family": "building"}],
            "source_urls": ["https://codes.iccsafe.org/content/IBC2024P1"],
            "applying_office": "Local Building Department",
        },
        {"segment": "commercial"},
    )
    assert not generic.get("apply_url")
    assert not generic["apply_path"].get("portal_url")
    assert generic["apply_path"]["typed_status"] == "VERIFY_WITH_PERMIT_OFFICE"


def test_fable5_closed_world_consumes_scope_floor_and_forbid_contracts():
    job = "change of occupancy from retail store to fitness studio with showers and new mechanical ventilation, job value 175000"
    facts = build_scope_facts_v2(job, "Glendale", "AZ", job_category="commercial")
    public = apply_closed_world_customer_contract(
        {"permit_decision": "REQUIRED", "permit_required": True, "permits_required": [{"permit_name": "Commercial Building / Tenant Improvement Permit", "family": "building"}]},
        job,
        "Glendale",
        "AZ",
        job_category="commercial",
        scope_facts_v2=facts,
    )
    assert "plumbing" in _families(public)
    assert "plumbing" not in _families(public, "CONDITIONAL")

    residential_job = "install new gas line to outdoor kitchen and grill, job value 6000"
    residential_facts = build_scope_facts_v2(residential_job, "Jackson", "MS", job_category="residential")
    public = apply_closed_world_customer_contract(
        {
            "permit_decision": "REQUIRED",
            "permit_required": True,
            "permits_required": [
                {"permit_name": "Plumbing Permit — Gas Piping Installation (Residential)", "family": "plumbing"},
                {"permit_name": "Health Plan Review / Food Establishment Permit", "family": "health_food"},
                {"permit_name": "Wastewater / FOG / Pretreatment Approval", "family": "wastewater_pretreatment_fog"},
            ],
        },
        residential_job,
        "Jackson",
        "MS",
        job_category="residential",
        scope_facts_v2=residential_facts,
    )
    assert "health_food" not in _families(public)
    assert "wastewater_pretreatment_fog" not in _families(public)
    assert "health_food" not in _families(public, "CONDITIONAL")
    assert "wastewater_pretreatment_fog" not in _families(public, "CONDITIONAL")


def test_decision_object_r033_keeps_electrical_and_blocks_food_fog_required():
    rec = _record("R-033")
    case = rec["case"]
    obj = compose_decision_object(rec["response_body"], case["job_type"], case["city"], case["state"], job_category=case.get("segment"))
    req = {item.family: item for item in obj.items if item.status == DecisionStatus.REQUIRED}
    assert "electrical" in req
    assert "health_food" not in req
    assert "wastewater_pretreatment_fog" not in req
    assert all("new circuit" not in item.permit_name.lower() for item in obj.items)

    public = _apply("R-033")
    assert "electrical" in _families(public)
    assert "health_food" not in _families(public)
    assert "wastewater_pretreatment_fog" not in _families(public)
    rendered = json.dumps(public).lower()
    assert "new circuit permit" not in rendered
    assert "new circuits required" not in rendered


def test_decision_object_c010_promotes_electrical_and_blocks_co_without_change_of_use():
    public = _apply("C-010")
    required = _families(public)
    assert "sign" in required
    assert "electrical" in required
    assert "co_change_of_occupancy" not in required
    assert "Certificate of Occupancy" not in "; ".join(_names(public))
    assert public.get("permit_name") == "Sign Permit"


def test_decision_object_r034_ess_electrical_lead_not_solar_structural():
    public = _apply("R-034")
    assert public.get("permit_kind") == "Electrical"
    assert "Battery" in str(public.get("permit_name")) or "ESS" in str(public.get("permit_name"))
    assert "Structural Racking" not in str(public.get("permit_name"))
    assert "roof load" not in json.dumps(public).lower()
    assert "structural engineering letter" not in json.dumps(public).lower()


def test_every_conditional_has_concrete_machine_trigger_not_generic_verify():
    public = _apply("C-010")
    for row in public.get("conditional_permits") or []:
        trigger = str(row.get("trigger") or row.get("required_if") or row.get("conditional_text") or "").lower()
        assert trigger
        assert "verify with ahj" not in trigger
        assert any(token in trigger for token in ["if", "when", "only needed"])


def test_decision_object_dedupes_one_required_row_per_family():
    for case_id in ["R-033", "C-010", "R-034"]:
        public = _apply(case_id)
        families = [r.get("family") for r in public.get("permits_required") or [] if isinstance(r, dict)]
        assert len(families) == len(set(families)), (case_id, families)
