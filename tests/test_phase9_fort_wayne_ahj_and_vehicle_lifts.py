from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from ahj_locality_resolver import (  # noqa: E402
    apply_ahj_locality_resolution,
    classify_source_for_resolution,
    resolve_ahj_locality,
)
from customer_pipeline import CustomerPipelineContext, run_pipeline_through_projection  # noqa: E402
from family_policy_matrix import mandatory_families  # noqa: E402
from public_packet import build_public_packet  # noqa: E402
from scope_contract import build_scope_facts_v4  # noqa: E402
from source_roles import SourceRole, classify_source  # noqa: E402


C035_SCOPE = (
    "install two automotive service lifts in an existing repair shop, "
    "anchor to slab, add dedicated electrical circuits"
)
PORTAL = "https://aca-prod.accela.com/ACFW/Default.aspx"
COUNTY_PAGE = "https://www.allencounty.in.gov/234/Building-Department"
COUNTY_DIRECTORY = "https://www.allencounty.in.gov/directory.aspx?did=18"
OLD_ROW_PAGE = "https://www.cityoffortwayne.in.gov/668/Permits-and-Bonds"


def _families(scope: str) -> set[str]:
    facts = build_scope_facts_v4(scope, "Fort Wayne", "IN", job_category="commercial")
    return set(mandatory_families(facts))


def test_c035_vehicle_lift_scope_requires_building_and_electrical_not_fire_or_zoning() -> None:
    facts = build_scope_facts_v4(C035_SCOPE, "Fort Wayne", "IN", job_category="commercial")
    families = set(mandatory_families(facts))
    assert families == {"building", "electrical"}
    assert {"building", "structural", "electrical"}.issubset(set(facts.positive_facts))
    assert not ({"fire_suppression", "fire_alarm", "planning_zoning"} & families)


def test_vehicle_lift_without_electrical_or_fire_system_changes_is_building_only() -> None:
    scope = (
        "install one automotive service lift in an existing repair shop, anchor to slab; "
        "no new electrical work and no sprinkler or fire alarm changes"
    )
    facts = build_scope_facts_v4(scope, "Fort Wayne", "IN", job_category="commercial")
    assert set(mandatory_families(facts)) == {"building"}
    assert {"no_electrical", "no_sprinkler_alteration", "no_fire_alarm_work"}.issubset(
        set(facts.negative_facts)
    )


def test_explicit_sprinkler_work_adds_suppression_but_not_alarm() -> None:
    families = _families(
        "install two automotive service lifts and alter fire sprinkler heads in an existing repair shop"
    )
    assert "fire_suppression" in families
    assert "fire_alarm" not in families


def test_explicit_alarm_work_adds_alarm_but_no_suppression_when_sprinklers_unchanged() -> None:
    families = _families(
        "install two automotive service lifts, add fire alarm devices, no sprinkler changes"
    )
    assert "fire_alarm" in families
    assert "fire_suppression" not in families


def test_change_of_use_adds_occupancy_routing_without_inventing_fire_system_work() -> None:
    families = _families(
        "convert retail suite to auto repair shop and install automotive lifts with dedicated circuits"
    )
    assert {"building", "building_ti", "electrical", "planning_zoning", "co_change_of_occupancy"}.issubset(
        families
    )
    assert not ({"fire_suppression", "fire_alarm"} & families)


def test_fort_wayne_resolves_to_joint_allen_county_filing_authority_and_office() -> None:
    resolution = resolve_ahj_locality("Fort Wayne", "IN")
    assert resolution is not None
    assert resolution["resolved_ahj_name"] == "Allen County Building Department"
    assert resolution["resolved_level"] == "county_joint_city_county"
    assert resolution["filing_authority_url"] == PORTAL
    assert resolution["office_address"] == "200 East Berry Street, Suite 180, Fort Wayne, IN 46802"
    assert classify_source_for_resolution({"url": COUNTY_PAGE}, resolution) == "filing_authority"
    assert classify_source_for_resolution({"url": OLD_ROW_PAGE}, resolution) == "irrelevant_or_procurement"


def test_fort_wayne_resolution_rewrites_all_customer_actions_and_maps_destination() -> None:
    original = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "applying_office": "City of Fort Wayne",
        "apply_url": OLD_ROW_PAGE,
        "permits_required": [
            {
                "permit_name": "Commercial Building Permit",
                "family": "building",
                "action_url": OLD_ROW_PAGE,
                "source_url": OLD_ROW_PAGE,
                "fee": "Budget roughly $300 - $1,500; verification needed.",
            },
            {
                "permit_name": "Electrical Permit",
                "family": "electrical",
                "action_url": OLD_ROW_PAGE,
                "source_url": OLD_ROW_PAGE,
                "fee": "Budget roughly $300 - $1,500; verification needed.",
            },
        ],
        "sources": [{"url": OLD_ROW_PAGE, "title": "Fort Wayne permits and bonds"}],
        "fee_range": "Budget roughly $300 - $1,500; verification needed.",
    }
    out = apply_ahj_locality_resolution(original, "Fort Wayne", "IN", C035_SCOPE)
    assert out["applying_office"] == "Allen County Building Department"
    assert out["apply_url"] == PORTAL
    assert out["apply_address"] == "200 East Berry Street, Suite 180, Fort Wayne, IN 46802"
    assert out["apply_google_maps"].startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "200+East+Berry+Street" in out["apply_google_maps"]
    assert out["apply_google_maps"] != "https://www.google.com/maps"
    assert all(row["action_url"] == PORTAL for row in out["permits_required"])
    assert all(row["source_url"] == PORTAL for row in out["permits_required"])
    assert OLD_ROW_PAGE not in out["source_urls"]
    assert {PORTAL, COUNTY_PAGE}.issubset(set(out["source_urls"]))
    assert "degraded_sources" not in out
    assert out["ahj_resolution"]["discarded_wrong_authority_sources"][0]["url"] == OLD_ROW_PAGE
    assert "$300" not in out["fee_range"]
    assert "Applications-Fees" in out["fee_range"]
    assert all("$300" not in str(row.get("fee") or "") for row in out["permits_required"])
    assert all("Applications-Fees" in str(row.get("fee") or "") for row in out["permits_required"])


def test_fort_wayne_sources_are_labeled_as_local_official_not_state_or_unverified() -> None:
    identity = {
        "city": "Fort Wayne",
        "state": "IN",
        "authority_name": "Allen County Building Department",
        "resolved_ahj_key": "allen_county_building_department_joint",
    }
    assert classify_source(PORTAL, identity)[0] == SourceRole.LOCAL_OFFICIAL_FILING
    assert classify_source(COUNTY_PAGE, identity)[0] == SourceRole.LOCAL_OFFICIAL_INFO
    assert classify_source(COUNTY_DIRECTORY, identity)[0] == SourceRole.LOCAL_OFFICIAL_INFO


def test_wrong_county_government_source_is_not_labeled_local_official() -> None:
    identity = {
        "city": "Fort Wayne",
        "state": "IN",
        "authority_name": "Allen County Building Department",
        "resolved_ahj_key": "allen_county_building_department_joint",
    }
    role, evidence = classify_source(
        "https://www.marioncounty.in.gov/permits/building-permit",
        identity,
    )
    assert role == SourceRole.UNKNOWN
    assert "not confirmed" in evidence.lower() or "wrong" in evidence.lower()


def test_c035_terminal_pipeline_preserves_row_provenance_and_scrubs_stale_noise() -> None:
    facts = build_scope_facts_v4(C035_SCOPE, "Fort Wayne", "IN", job_category="commercial")
    raw = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "segment": "commercial",
        "applying_office": "City of Fort Wayne",
        "apply_url": OLD_ROW_PAGE,
        "source_urls": [OLD_ROW_PAGE],
        "sources": [{"url": OLD_ROW_PAGE, "title": "Fort Wayne permits and bonds"}],
        "fee_range": "Budget roughly $300 - $1,500; verification needed.",
        "warnings": [
            "Commercial scope may require companion reviews/permits not fully proven here: mechanical, plumbing."
        ],
        "permits_required": [
            {
                "permit_name": "Commercial Building Permit",
                "family": "building",
                "required": True,
                "fee": "Budget roughly $300 - $1,500; verification needed.",
            },
            {
                "permit_name": "Electrical Permit",
                "family": "electrical",
                "required": True,
                "fee": "Budget roughly $300 - $1,500; verification needed.",
            },
        ],
    }
    out = run_pipeline_through_projection(
        raw,
        CustomerPipelineContext(
            job_type=C035_SCOPE,
            city="Fort Wayne",
            state="IN",
            job_category="commercial",
            scope_contract={},
            scope_facts=facts,
        ),
    )

    packet_rows = [row for row in (out.get("public_packet") or {}).get("rows") or [] if row.get("decision") == "REQUIRED"]
    legacy_rows = [row for row in out.get("permits_required") or [] if row.get("decision") == "REQUIRED"]
    assert {row["family"] for row in packet_rows} == {"building", "electrical"}
    assert packet_rows and all(row.get("source_url") == PORTAL for row in packet_rows)
    assert all(row.get("source") == PORTAL for row in packet_rows)
    assert legacy_rows and all(row.get("source_url") == PORTAL for row in legacy_rows)
    assert all(row.get("source_role") == SourceRole.LOCAL_OFFICIAL_FILING.value for row in packet_rows)
    assert all("$300" not in str(row.get("fees") or "") for row in packet_rows)
    assert all("Applications-Fees" in str(row.get("fees") or "") for row in packet_rows)
    assert "mechanical, plumbing" not in " ".join(str(item).lower() for item in out.get("warnings") or [])


def test_production_python_sources_contain_no_embedded_control_characters() -> None:
    offenders: list[tuple[str, int, int]] = []
    for path in sorted(API.glob("*.py")):
        for offset, byte in enumerate(path.read_bytes()):
            if byte < 32 and byte not in {9, 10, 13}:
                offenders.append((path.name, offset, byte))
    assert offenders == []


def test_vehicle_lift_packet_drops_untriggered_mechanical_conditional_noise() -> None:
    facts = build_scope_facts_v4(C035_SCOPE, "Fort Wayne", "IN", job_category="commercial")
    result = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "job_type": C035_SCOPE,
        "segment": "commercial",
        "apply_url": PORTAL,
        "permits_required": [
            {"permit_name": "Commercial Building Permit", "family": "building", "required": True},
            {"permit_name": "Electrical Permit", "family": "electrical", "required": True},
            {"permit_name": "Mechanical Permit", "family": "mechanical", "required": True},
        ],
    }
    packet = build_public_packet(result, facts=facts)
    assert set(packet.required_families) == {"building", "electrical"}
    assert "mechanical" not in set(packet.conditional_families)


def test_unmapped_locality_remains_unchanged() -> None:
    original = {"applying_office": "Local Building Department", "apply_url": "https://example.gov/apply"}
    assert apply_ahj_locality_resolution(original, "Topeka", "KS") == original
