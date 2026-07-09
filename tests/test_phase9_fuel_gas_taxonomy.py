from __future__ import annotations

from dataclasses import asdict
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from customer_boundary_validator import validate_customer_boundary
from family_policy_matrix import covered_families, mandatory_families
from public_packet import build_public_packet, seal_packet
from scope_contract import build_scope_facts_v4


OFFICIAL_APPLY = "https://www.topeka.gov/how_do_i/permit-license/skilled_trades/index.php"


def _required_result(*rows: dict[str, Any]) -> dict[str, Any]:
    return {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permits_required": list(rows),
        "apply_url": OFFICIAL_APPLY,
        "applying_office": "Local trades permit office",
        "source_urls": [OFFICIAL_APPLY],
    }


def _row(name: str, family: str) -> dict[str, Any]:
    return {
        "permit_name": name,
        "family": family,
        "filing_family": family,
        "decision": "REQUIRED",
        "required": True,
    }


def _packet(job: str, segment: str, *rows: dict[str, Any]) -> dict[str, Any]:
    facts = build_scope_facts_v4(job, "Topeka", "KS", job_category=segment)
    packet = asdict(build_public_packet(_required_result(*rows), facts))
    return seal_packet(packet, facts=facts, fail_hard=True)


def test_r032_generator_new_gas_line_is_one_plumbing_gas_subtype_not_mechanical() -> None:
    job = "install 22 kW standby generator with automatic transfer switch and new gas line for single-family home"
    facts = build_scope_facts_v4(job, "Topeka", "KS", job_category="residential")
    assert set(mandatory_families(facts)) == {"electrical", "gas", "plumbing"}

    packet = _packet(job, "residential", _row("Electrical Permit", "electrical"))
    assert packet["required_families"] == ["electrical", "gas"]
    assert covered_families(packet["required_families"]) == {"electrical", "gas", "plumbing"}
    assert [(row["family"], row["permit_name"]) for row in packet["rows"]] == [
        ("electrical", "Electrical Permit"),
        ("gas", "Fuel Gas / Plumbing Gas Permit"),
    ]
    assert "mechanical" not in packet["required_families"]
    assert "plumbing" not in packet["required_families"]
    assert not packet.get("packet_invariant_errors")


def test_pure_grill_gas_line_uses_same_one_way_plumbing_parent_coverage() -> None:
    packet = _packet(
        "install new gas branch line to an outdoor grill; no water or drain work",
        "residential",
        _row("Fuel Gas / Plumbing Gas Permit", "gas"),
    )
    assert packet["required_families"] == ["gas"]
    assert covered_families(packet["required_families"]) == {"gas", "plumbing"}
    assert not packet.get("packet_invariant_errors")


def test_gas_appliance_replacement_without_new_piping_does_not_invent_gas_or_plumbing() -> None:
    packet = _packet(
        "replace gas furnace at same capacity and reconnect existing gas; no new gas piping",
        "residential",
        _row("Mechanical Permit", "mechanical"),
    )
    assert packet["required_families"] == ["mechanical"]


def test_ordinary_mechanical_scope_does_not_invent_fuel_gas_or_plumbing() -> None:
    packet = _packet(
        "replace electric heat pump system; no gas line and no plumbing work",
        "residential",
        _row("Mechanical Permit", "mechanical"),
    )
    assert "mechanical" in packet["required_families"]
    assert "gas" not in packet["required_families"]
    assert "plumbing" not in packet["required_families"]


def test_ordinary_plumbing_scope_keeps_generic_plumbing_without_gas_alias() -> None:
    packet = _packet(
        "replace kitchen sink, faucet, and water supply line; no gas work",
        "residential",
        _row("Plumbing Permit", "plumbing"),
    )
    assert packet["required_families"] == ["plumbing"]
    assert covered_families(packet["required_families"]) == {"plumbing"}


def test_generic_plumbing_row_cannot_satisfy_explicit_fuel_gas_floor() -> None:
    assert covered_families({"plumbing"}) == {"plumbing"}
    assert "gas" not in covered_families({"plumbing"})


def test_customer_boundary_validator_uses_same_one_way_gas_plumbing_coverage() -> None:
    job = "install new gas branch line and 120V receptacle; no water or drain work"
    facts = build_scope_facts_v4(job, "Topeka", "KS", job_category="residential")
    packet = _packet(job, "residential", _row("Electrical Permit", "electrical"))
    public = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permits_required": [r for r in packet["rows"] if r["decision"] == "REQUIRED"],
        "public_packet": packet,
    }
    findings = validate_customer_boundary(
        public,
        expected={"expected_decision": "REQUIRED", "required_families_must_include": ["electrical", "gas", "plumbing"]},
        facts=facts,
        include_matrix=True,
    )
    codes = [finding.code for finding in findings]
    assert "missing_or_demoted_required_family" not in codes
    assert "matrix_expected_family_floor_dry_run" not in codes
