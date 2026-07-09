from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from customer_boundary_validator import validate_customer_boundary  # noqa: E402
from live100_fable5_final_gate import apply_fable5_final_customer_gate  # noqa: E402
from public_packet import apply_public_packet_projection  # noqa: E402
from scope_contract import build_scope_facts_v4, safety_critical_required_families  # noqa: E402


def _not_required_base() -> dict:
    return {
        "permit_required": False,
        "permit_decision": "NOT_REQUIRED",
        "permit_verdict": "NO",
        "permit_name": "No permit required",
        "permit_type": "No permit required",
        "permit_kind": "Not Required",
        "permits_required": [],
        "required_permit_families": [],
        "required_permit_names": [],
        "summary": "No permit required for the described scope.",
    }


def _families(public: dict) -> set[str]:
    rows = list(public.get("permits_required") or [])
    packet = public.get("public_packet") if isinstance(public.get("public_packet"), dict) else {}
    rows += [r for r in packet.get("rows") or [] if isinstance(r, dict) and r.get("decision") == "REQUIRED"]
    return {str(r.get("family") or r.get("filing_family") or "") for r in rows if str(r.get("family") or r.get("filing_family") or "")}


def test_safety_critical_scope_cannot_remain_clean_not_required_in_final_gate_and_packet() -> None:
    job = "commercial dental suite: add x-ray electrical circuit, new sink drain, and relocate fire sprinkler head"
    facts = build_scope_facts_v4(job, "Testville", "TX", job_category="commercial")
    safety = safety_critical_required_families(facts)
    assert {"electrical", "plumbing", "fire_suppression"}.issubset(safety)

    gated = apply_fable5_final_customer_gate(_not_required_base(), job, "Testville", "TX", {"category": "commercial"}, facts)
    projected = apply_public_packet_projection(gated, facts)

    assert projected["permit_decision"] == "REQUIRED"
    assert {"electrical", "plumbing", "fire_suppression"}.issubset(_families(projected))
    findings = validate_customer_boundary(projected, visible_text=projected.get("summary", ""), facts=facts)
    assert "safety_trigger_not_required" not in {f.code for f in findings}
    assert "status_contradiction_not_required_with_required_artifacts" not in {f.code for f in findings}


def test_safety_floor_validator_flags_unrepaired_not_required_payload() -> None:
    job = "residential replace gas water heater with new gas branch connection and drain pan"
    facts = build_scope_facts_v4(job, "Testville", "TX", job_category="residential")
    findings = validate_customer_boundary(_not_required_base(), visible_text="No permit required.", facts=facts)
    assert any(f.code == "safety_trigger_not_required" and "plumbing" in f.detail for f in findings)


def test_site_accessibility_or_curb_cut_scope_gets_site_safety_floor() -> None:
    job = "commercial mill and overlay parking lot and restripe ADA stalls, no drainage changes"
    facts = build_scope_facts_v4(job, "Testville", "NJ", job_category="commercial")
    safety = safety_critical_required_families(facts)
    assert {"grading", "planning_zoning"}.issubset(safety)
    projected = apply_public_packet_projection(
        apply_fable5_final_customer_gate(_not_required_base(), job, "Testville", "NJ", {"category": "commercial"}, facts),
        facts,
    )
    assert projected["permit_decision"] == "REQUIRED"
    assert "grading" in _families(projected)


def test_cosmetic_no_trade_scope_stays_not_required_and_has_no_safety_floor() -> None:
    job = "residential interior painting and carpet replacement, no electrical, no plumbing, no structural changes"
    facts = build_scope_facts_v4(job, "Testville", "TX", job_category="residential")
    assert safety_critical_required_families(facts) == set()

    gated = apply_fable5_final_customer_gate(_not_required_base(), job, "Testville", "TX", {"category": "residential"}, facts)
    projected = apply_public_packet_projection(gated, facts)

    assert projected["permit_decision"] == "NOT_REQUIRED"
    assert _families(projected) == set()


def test_no_change_ti_negative_ceilings_remove_stale_mep_and_co_rows() -> None:
    job = "commercial office tenant improvement: non-load-bearing partitions, no plumbing, no HVAC, no electrical, no change of use"
    facts = build_scope_facts_v4(job, "Testville", "ID", job_category="commercial")
    stale = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permits_required": [
            {"permit_name": "Commercial Building / Tenant Improvement Permit", "family": "building_ti", "required": True, "decision": "REQUIRED"},
            {"permit_name": "Electrical Permit", "family": "electrical", "required": True, "decision": "REQUIRED"},
            {"permit_name": "Plumbing Permit", "family": "plumbing", "required": True, "decision": "REQUIRED"},
            {"permit_name": "Certificate of Occupancy / Change-of-Occupancy Approval", "family": "co_change_of_occupancy", "required": True, "decision": "REQUIRED"},
        ],
    }
    projected = apply_public_packet_projection(apply_fable5_final_customer_gate(stale, job, "Testville", "ID", {"category": "commercial"}, facts), facts)
    assert _families(projected) == {"building_ti"}


def test_matrix_mandated_food_and_fog_floors_are_not_suppressed() -> None:
    job = "build 3000 square foot refrigerated cold storage room inside warehouse with insulated panels, refrigeration and floor drains"
    facts = build_scope_facts_v4(job, "Green Bay", "WI", job_category="commercial")
    projected = apply_public_packet_projection(
        {
            "permit_required": True,
            "permit_decision": "REQUIRED",
            "permit_verdict": "YES",
            "summary": "Permit required for the described work.",
            "permits_required": [
                {"permit_name": "Building Permit", "family": "building", "required": True, "decision": "REQUIRED"},
                {"permit_name": "Mechanical Permit", "family": "mechanical", "required": True, "decision": "REQUIRED"},
                {"permit_name": "Plumbing Permit", "family": "plumbing", "required": True, "decision": "REQUIRED"},
                {"permit_name": "Refrigeration Permit", "family": "refrigeration", "required": True, "decision": "REQUIRED"},
            ],
        },
        facts,
    )
    families = _families(projected)
    assert "health_food" in families
    assert "wastewater_pretreatment_fog" in families


def test_explicit_no_food_service_still_blocks_food_and_fog_floors() -> None:
    job = "convert former office suite to fitness gym with showers and locker rooms, no food service"
    facts = build_scope_facts_v4(job, "Knoxville", "TN", job_category="commercial")
    projected = apply_public_packet_projection(
        {
            "permit_required": True,
            "permit_decision": "REQUIRED",
            "permit_verdict": "YES",
            "permits_required": [
                {"permit_name": "Health Food Permit", "family": "health_food", "required": True, "decision": "REQUIRED"},
                {"permit_name": "FOG Permit", "family": "wastewater_pretreatment_fog", "required": True, "decision": "REQUIRED"},
                {"permit_name": "Building Permit", "family": "building", "required": True, "decision": "REQUIRED"},
            ],
        },
        facts,
    )
    families = _families(projected)
    assert "building" in families
    assert "health_food" not in families
    assert "wastewater_pretreatment_fog" not in families


def test_known_bad_apply_urls_are_repaired_to_landing_pages() -> None:
    facts = build_scope_facts_v4("commercial food facility permit", "Sonoma", "CA", job_category="commercial")
    stale = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "apply_url": "https://www.sonomacounty.gov/Main%20County%20Site/Health%20and%20Human%20Services/Health%20Services/Documents/_Documents/Retail-Food-Facility-Permit-Application.pdf",
        "permits_required": [{"permit_name": "Health Permit", "family": "health_food", "required": True, "decision": "REQUIRED"}],
    }
    projected = apply_public_packet_projection(stale, facts)
    assert "Retail-Food-Facility-Permit-Application.pdf" not in projected.get("apply_url", "")
    assert projected.get("apply_url", "").startswith("https://sonomacounty.ca.gov/")


def test_hpwh_water_heater_only_ceiling_blocks_building_mechanical_and_existing_circuit_electrical() -> None:
    job = "replace electric tank water heater with heat pump water heater in basement, new condensate line, existing dedicated circuit"
    facts = build_scope_facts_v4(job, "Ithaca", "NY", job_category="residential")
    assert "plumbing" in facts.request_positive_families
    assert "building" not in facts.request_positive_families
    assert "mechanical" not in facts.request_positive_families
    assert "electrical" not in facts.request_positive_families

    stale = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permits_required": [
            {"permit_name": "Building Permit", "family": "building", "required": True, "decision": "REQUIRED"},
            {"permit_name": "Mechanical Permit", "family": "mechanical", "required": True, "decision": "REQUIRED"},
            {"permit_name": "Electrical Permit", "family": "electrical", "required": True, "decision": "REQUIRED"},
            {"permit_name": "Plumbing Permit", "family": "plumbing", "required": True, "decision": "REQUIRED"},
        ],
    }
    projected = apply_public_packet_projection(stale, facts)
    assert _families(projected) == {"plumbing"}


def test_commercial_interior_build_scopes_floor_building_without_case_ids() -> None:
    examples = [
        "place two temporary modular classrooms on private school campus with ramps, electrical feeder and fire alarm connection",
        "build 3000 square foot refrigerated cold storage room inside warehouse with insulated panels, ammonia-free refrigeration and floor drains",
        "build small data room in office suite with supplemental cooling, clean agent fire suppression and new UPS circuits",
        "add single accessible restroom inside warehouse with trench drain connection, exhaust fan and water heater",
    ]
    for job in examples:
        facts = build_scope_facts_v4(job, "Testville", "TX", job_category="commercial")
        assert "building" in facts.request_positive_families, job


def test_exit_signs_do_not_become_illuminated_sign_scope() -> None:
    job = "split existing retail suite into two tenant spaces with demising wall, exit signs and electrical panel separation, no plumbing"
    facts = build_scope_facts_v4(job, "Tallahassee", "FL", job_category="commercial")
    assert "electrical" in facts.request_positive_families
    assert "sign" not in facts.request_positive_families


def test_laundry_relocation_between_floors_adds_building_floor_and_does_not_create_mechanical_from_electric_dryer_circuit() -> None:
    job = "relocate laundry from basement to second floor closet with new drain, vent, water lines and 240V dryer circuit, customer asks for a customer-ready filing packet"
    facts = build_scope_facts_v4(job, "Athens", "GA", job_category="residential")
    assert {"building", "electrical", "plumbing"}.issubset(facts.request_positive_families)
    assert "mechanical" not in facts.request_positive_families

    stale = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permits_required": [
            {"permit_name": "Electrical Permit", "family": "electrical", "required": True, "decision": "REQUIRED"},
            {"permit_name": "Mechanical Permit", "family": "mechanical", "required": True, "decision": "REQUIRED"},
            {"permit_name": "Plumbing Permit", "family": "plumbing", "required": True, "decision": "REQUIRED"},
        ],
    }
    gated = apply_fable5_final_customer_gate(stale, job, "Athens", "GA", {"category": "residential"}, facts)
    projected = apply_public_packet_projection(gated, facts)
    assert {"building", "electrical", "plumbing"}.issubset(_families(projected))
    assert "mechanical" not in _families(projected)


def test_report_dump_fee_text_is_never_reused_as_customer_fee_field() -> None:
    job = "install 1200 gallon residential rainwater harvesting cistern connected to landscape irrigation only, customer asks for a customer-ready filing packet"
    facts = build_scope_facts_v4(job, "Tucson", "AZ", job_category="residential")
    stale = {
        "permit_required": False,
        "permit_decision": "NOT_REQUIRED",
        "permit_verdict": "NO",
        "permit_name": "No permit required",
        "permit_type": "No permit required",
        "summary": "No permit is required for this scope.",
        "fee_range": "Timeline Varies by jurisdiction Where to apply City of Tucson Development Services Center SUMMARY Permit required: Plumbing Permit — Irrigation Backflow Preventer. REQUIRED DOCUMENTS Scope of work PRE-CONSTRUCTION CHECKLIST No permit is required SOURCES CHECKED Get your own permits instantly at permitassist.io",
    }
    gated = apply_fable5_final_customer_gate(stale, job, "Tucson", "AZ", {"category": "residential"}, facts)
    projected = apply_public_packet_projection(gated, facts)
    fee = str(projected.get("fee_range") or "")
    assert "SUMMARY" not in fee
    assert "PRE-CONSTRUCTION" not in fee
    assert "Get your own permits" not in fee


def test_retail_suite_exit_signs_are_life_safety_electrical_not_illuminated_sign_or_co() -> None:
    job = "split existing retail suite into two tenant spaces with demising wall, exit signs and electrical panel separation, no plumbing, customer asks for a customer-ready filing packet"
    facts = build_scope_facts_v4(job, "Tallahassee", "FL", job_category="commercial")
    stale = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permits_required": [
            {"permit_name": "Commercial Building / Tenant Improvement Permit", "family": "building_ti", "required": True, "decision": "REQUIRED"},
            {"permit_name": "Electrical Permit — Illuminated Sign", "family": "electrical", "required": True, "decision": "REQUIRED"},
            {"permit_name": "Certificate of Occupancy / Change-of-Occupancy Approval", "family": "co_change_of_occupancy", "required": True, "decision": "REQUIRED"},
        ],
    }
    gated = apply_fable5_final_customer_gate(stale, job, "Tallahassee", "FL", {"category": "commercial"}, facts)
    projected = apply_public_packet_projection(gated, facts)
    families = _families(projected)
    assert {"building", "electrical", "fire_suppression"}.issubset(families)
    assert "co_change_of_occupancy" not in families
    visible = json.dumps(projected, default=str)
    assert "Illuminated Sign" not in visible
    assert "Certificate of Occupancy" not in visible
    assert "sign" not in facts.request_positive_families


def test_bathroom_tub_to_shower_fixture_relocation_blocks_stale_co_and_uses_positive_electrical_label() -> None:
    job = "convert hall bathroom tub to shower, relocate toilet eighteen inches, add bath fan and GFCI outlet in single-family home, customer asks for a customer-ready filing packet"
    stale = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permits_required": [
            {"permit_name": "Certificate of Occupancy / Change-of-Occupancy Approval", "family": "co_change_of_occupancy", "required": True, "decision": "REQUIRED"},
            {"permit_name": "Residential Electrical Permit — Device / Receptacle Replacement (Existing Circuits)", "family": "electrical", "required": True, "decision": "REQUIRED"},
            {"permit_name": "Plumbing Permit", "family": "plumbing", "required": True, "decision": "REQUIRED"},
        ],
    }
    facts = build_scope_facts_v4(job, "Pittsburgh", "PA", job_category="residential")
    gated = apply_fable5_final_customer_gate(stale, job, "Pittsburgh", "PA", {"category": "residential"}, facts)
    projected = apply_public_packet_projection(gated, facts)
    families = _families(projected)
    assert {"building", "electrical", "mechanical", "plumbing"}.issubset(families)
    assert "co_change_of_occupancy" not in families
    visible = json.dumps(projected, default=str)
    assert "Certificate of Occupancy" not in visible
    assert "Device / Receptacle Replacement" not in visible


def test_same_size_garage_doors_use_verify_with_conditional_building_and_electrical() -> None:
    job = "replace two residential garage doors same size with new openers, no header changes"
    facts = build_scope_facts_v4(job, "Rockford", "IL", job_category="residential")
    stale = {
        **_not_required_base(),
        "applying_office": "City of Rockford Building Permits Division",
        "apply_url": "https://www.rockfordil.gov/438/Permits",
        "source_urls": ["https://www.rockfordil.gov/438/Permits"],
    }

    projected = apply_public_packet_projection(stale, facts)

    assert projected["permit_decision"] == "VERIFY"
    assert projected["permit_required"] is None
    assert _families(projected) == set()
    conditional = {
        str(row.get("filing_family") or row.get("family") or "")
        for row in projected.get("conditional_permits") or []
        if isinstance(row, dict)
    }
    assert conditional == {"building", "electrical"}
    assert "no permit required" not in json.dumps(projected, default=str).lower()
    assert projected["apply_path"]["typed_status"] == "VERIFY_WITH_PERMIT_OFFICE"


def test_garage_door_structural_scope_does_not_use_like_for_like_verify_rule() -> None:
    job = "replace residential garage door and enlarge opening with new structural header framing"
    facts = build_scope_facts_v4(job, "Rockford", "IL", job_category="residential")
    stale = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permits_required": [
            {"permit_name": "Building Permit", "family": "building", "required": True, "decision": "REQUIRED"},
        ],
    }

    projected = apply_public_packet_projection(stale, facts)

    assert projected["permit_decision"] == "REQUIRED"
    assert "building" in _families(projected)


def test_forbidden_subtype_alias_does_not_forbid_required_parent_family() -> None:
    public = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "required_permit_families": ["building"],
        "permits_required": [
            {"permit_name": "Residential Building Permit", "family": "building", "required": True, "decision": "REQUIRED"},
        ],
    }
    expected = {
        "expected_decision": "REQUIRED",
        "required_families_must_include": ["building"],
        "forbidden_hard_required_families": ["detached_garage_building"],
    }

    findings = validate_customer_boundary(public, visible_text="Residential Building Permit required.", expected=expected)

    assert "unsupported_extra_hard_required_family" not in {finding.code for finding in findings}


def test_forbidden_subtype_alias_still_flags_exact_required_subtype() -> None:
    public = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "required_permit_families": ["detached_garage_building"],
        "permits_required": [
            {
                "permit_name": "Building Permit — Detached Garage",
                "family": "detached_garage_building",
                "required": True,
                "decision": "REQUIRED",
            },
        ],
    }
    expected = {
        "expected_decision": "REQUIRED",
        "required_families_must_include": ["building"],
        "forbidden_hard_required_families": ["detached_garage_building"],
    }

    findings = validate_customer_boundary(public, visible_text="Building Permit — Detached Garage required.", expected=expected)

    assert any(
        finding.code == "unsupported_extra_hard_required_family"
        and "detached_garage_building" in finding.detail
        for finding in findings
    )
