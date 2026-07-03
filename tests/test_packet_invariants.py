from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from public_packet import PacketInvariantError, seal_packet
from scope_contract import build_scope_facts_v3


def test_i1_forbidden_family_rejected_at_packet_boundary():
    facts = build_scope_facts_v3("install new gas line to outdoor kitchen and grill", "Jackson", "MS", job_category="residential")
    packet = {
        "schema_version": "final_public_permit_packet.v1",
        "segment": "residential",
        "decision": "REQUIRED",
        "permit_required_verdict": "REQUIRED",
        "lead_label": "Residential Gas Piping Permit — Outdoor Appliance / Grill Line",
        "authority": {"name": "Jackson permit office", "apply_url": "https://www.jacksonms.gov/building-permits/", "source_urls": []},
        "rows": [
            {"permit_name": "Gas Pressure Test / Fuel Gas Permit", "family": "gas", "decision": "REQUIRED", "lead": True, "action_url": "https://www.jacksonms.gov/building-permits/"},
            {"permit_name": "Food Establishment Health Plan Review / Permit", "family": "health_food", "decision": "CONDITIONAL"},
        ],
        "required_families": ["gas"],
        "conditional_families": ["health_food"],
        "checklist": [],
        "documents": [],
        "inspections": [],
        "fees": [],
    }
    with pytest.raises(PacketInvariantError, match="forbidden"):
        seal_packet(packet, facts=facts, fail_hard=True)


def test_i2_not_required_packet_has_no_required_artifacts():
    facts = build_scope_facts_v3(
        "replace drywall in one bedroom after leak no structural no electrical no plumbing, job value 3500",
        "Chicago",
        "IL",
        job_category="residential",
    )
    packet = {
        "schema_version": "final_public_permit_packet.v1",
        "segment": "residential",
        "decision": "NOT_REQUIRED",
        "permit_required_verdict": "NOT_REQUIRED",
        "verdict_basis": "Chicago repair exemption for limited nonstructural drywall repair.",
        "lead_label": "No permit required",
        "authority": {"name": "Chicago DOB", "apply_url": "", "source_urls": []},
        "rows": [{"permit_name": "Building Permit", "family": "building", "decision": "REQUIRED"}],
        "required_families": ["building"],
        "conditional_families": [],
        "checklist": ["Pull Building Permit before starting work"],
        "documents": ["Structural/foundation drawings"],
        "inspections": [],
        "fees": [],
    }
    with pytest.raises(PacketInvariantError):
        seal_packet(packet, facts=facts, fail_hard=True)


def test_i3_required_packet_needs_apply_or_verified_contact_fallback():
    facts = build_scope_facts_v3("commercial exterior facade repair with masonry lintel replacement", "Philadelphia", "PA", job_category="commercial")
    packet = {
        "schema_version": "final_public_permit_packet.v1",
        "segment": "commercial",
        "decision": "REQUIRED",
        "permit_required_verdict": "REQUIRED",
        "lead_label": "Commercial Building Permit — Structural Facade / Masonry Repair",
        "authority": {"name": "Philadelphia L&I", "apply_url": "", "source_urls": []},
        "rows": [{"permit_name": "Commercial Building Permit — Structural Facade / Masonry Repair", "family": "building_ti", "decision": "REQUIRED", "lead": True}],
        "required_families": ["building_ti"],
        "conditional_families": [],
        "checklist": [],
        "documents": [],
        "inspections": [],
        "fees": [],
    }
    with pytest.raises(PacketInvariantError, match="apply"):
        seal_packet(packet, facts=facts, fail_hard=True)


def test_i6_non_official_source_cannot_carry_official_badge():
    facts = build_scope_facts_v3("install new gas line to outdoor kitchen and grill", "Jackson", "MS", job_category="residential")
    packet = {
        "schema_version": "final_public_permit_packet.v1",
        "segment": "residential",
        "decision": "REQUIRED",
        "permit_required_verdict": "REQUIRED",
        "lead_label": "Residential Gas Piping Permit — Outdoor Appliance / Grill Line",
        "authority": {"name": "Jackson permit office", "apply_url": "https://www.jacksonms.gov/building-permits/", "source_urls": []},
        "sources": [{"url": "https://www.iccsafe.org/building-safety-journal/bsj-technical/code-requirements-for-outdoor-kitchens/", "title": "Official permit source", "source_role": "PUBLISHER_CONTEXT"}],
        "rows": [{"permit_name": "Residential Gas Piping Permit — Outdoor Appliance / Grill Line", "family": "gas", "decision": "REQUIRED", "lead": True, "action_url": "https://www.jacksonms.gov/building-permits/"}],
        "required_families": ["gas"],
        "conditional_families": [],
        "checklist": [],
        "documents": [],
        "inspections": [],
        "fees": [],
    }
    with pytest.raises(PacketInvariantError, match="Official"):
        seal_packet(packet, facts=facts, fail_hard=True)
