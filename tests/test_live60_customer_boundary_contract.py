import json
import os
import sys
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import server  # noqa: E402


def _apply(raw, job, city="Testville", state="TS", category="residential"):
    scope = {"category": category, "city": city, "state": state}
    return server.apply_live60_customer_boundary_contract(raw, job, city, state, scope_contract=scope)


def _blob(value):
    return json.dumps(value, sort_keys=True, default=str).lower()


def _families(public):
    return {server._pa20_row_family(row) for row in public.get("permits_required") or [] if isinstance(row, dict)}


def test_r27_residential_fixture_swap_no_relocation_blocks_health_electrical_plumbing_overreach():
    job = "replace kitchen faucet and garbage disposal only, no pipe relocation"
    raw = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_name": "Multiple permits required: Electrical + Health + Plumbing",
        "permits_required": [
            {"permit_type": "Electrical Permit", "required": True},
            {"permit_type": "Food Establishment Health Plan Review / Permit", "required": True},
            {"permit_type": "Plumbing Permit — Garbage Disposal Replacement", "required": True},
        ],
    }

    public = _apply(raw, job, "Charlotte", "NC")
    text = _blob(public)

    assert "food establishment" not in text
    assert "health plan" not in text
    assert "electrical permit" not in text
    assert "plumbing permit" not in text


def test_r28_basement_adu_forces_complete_bepm_packet_without_removing_features():
    job = "basement ADU conversion with new kitchen, bathroom, egress windows and subpanel"
    raw = {"permit_decision": "REQUIRED", "permit_required": True, "permits_required": [{"permit_type": "Electrical Permit", "required": True}]}

    public = _apply(raw, job, "Salt Lake City", "UT")

    assert {"building", "electrical", "plumbing", "mechanical"}.issubset(_families(public))
    assert "adu" in _blob(public)


def test_r03_solar_battery_and_r13_hpwh_240v_promote_electrical_rows():
    solar = _apply(
        {"permit_decision": "REQUIRED", "permit_required": True, "permits_required": [{"permit_type": "Building Permit — Solar PV", "required": True}]},
        "install rooftop solar panels with battery backup on a single family house",
        "Mesa",
        "AZ",
    )
    assert "electrical" in _families(solar)
    assert "electrical" in str(solar.get("permit_name", "")).lower()

    hpwh = _apply(
        {"permit_decision": "REQUIRED", "permit_required": True, "permits_required": [{"permit_type": "Residential Plumbing Permit — Water Heater Replacement", "required": True}]},
        "install heat pump water heater in garage with new 240 volt circuit",
        "San Jose",
        "CA",
    )
    assert {"plumbing", "electrical"}.issubset(_families(hpwh))


def test_not_required_contract_removes_required_docs_filing_timeline_and_stale_summary():
    raw = {
        "permit_decision": "NOT_REQUIRED",
        "permit_required": False,
        "permit_name": "No permit required",
        "summary": "Multiple permits required: Building Permit; Plumbing Permit; Fire Review; Certificate of Occupancy.",
        "timeline": "Permit filing/review is required for the resolved permit category.",
        "requirements": ["Panel schedule showing existing and proposed circuits", "Wire gauge and breaker size"],
        "documents_needed": ["Electrical diagram or one-line drawing", "Utility disconnect location marked on plans"],
        "apply_url": "https://example.gov/apply",
    }

    public = _apply(raw, "interior repaint and replace carpet only, no walls, no electrical, no plumbing", "Scottsdale", "AZ")
    text = _blob(public)

    assert public["permit_decision"] == "NOT_REQUIRED"
    assert public["permits_required"] == []
    assert public.get("required_permit_families") == []
    assert public.get("required_permit_names") == []
    assert public["apply_url"] == ""
    for forbidden in ["panel schedule", "wire gauge", "electrical diagram", "utility disconnect", "multiple permits required", "permit filing/review is required"]:
        assert forbidden not in text
    assert "if walls" in text or "if the work expands" in text


def test_companion_overreach_removed_but_explicit_trade_rows_preserved():
    r22 = _apply(
        {
            "permit_decision": "REQUIRED",
            "permit_required": True,
            "permits_required": [
                {"permit_type": "Electrical Permit", "required": True},
                {"permit_type": "Mechanical Permit", "required": True},
                {"permit_type": "Plumbing Permit", "required": True},
            ],
            "summary": "Multiple permits required: Electrical; Mechanical; Plumbing; Fire; Planning; Certificate of Occupancy.",
        },
        "apartment bathroom renovation replacing tub, valve, lighting and exhaust fan",
        "New York",
        "NY",
    )
    text = _blob(r22)
    assert {"electrical", "mechanical", "plumbing"}.issubset(_families(r22))
    assert r22.get("required_permit_families") == ["Electrical", "Mechanical", "Plumbing"]
    assert "fire" not in str(r22.get("permit_name", "")).lower()
    assert "certificate of occupancy" not in text
    assert "planning / zoning" not in text

    siding = _apply(
        {"permit_decision": "REQUIRED", "permit_required": True, "permits_required": [{"permit_type": "Building Permit — Exterior Siding", "required": True}, {"permit_type": "Plumbing Permit", "required": True}]},
        "replace existing exterior siding on two family house",
        "Boston",
        "MA",
    )
    assert "building" in _families(siding)
    assert "plumbing" not in _families(siding)


def test_c09_illuminated_sign_adds_electrical_and_suppresses_plumbing():
    public = _apply(
        {"permit_decision": "REQUIRED", "permit_required": True, "permits_required": [{"permit_type": "Building Permit — Commercial Sign Installation", "required": True}, {"permit_type": "Plumbing Permit", "required": True}]},
        "install monument sign and illuminated wall sign for retail tenant",
        "Gilbert",
        "AZ",
        category="commercial",
    )

    assert "electrical" in _families(public)
    assert "plumbing" not in _families(public)
    assert "illuminated" in _blob(public)


def test_no_neuter_negation_and_conditional_language_does_not_create_or_delete_useful_scope():
    public = _apply(
        {"permit_decision": "REQUIRED", "permit_required": True, "permits_required": [{"permit_type": "Building Permit — Patio Cover", "required": True}], "summary": "Building permit only; no electrical or plumbing permit is triggered."},
        "build attached patio cover with no electrical or plumbing",
        "Gilbert",
        "AZ",
    )
    text = _blob(public)
    assert _families(public) == {"building"}
    assert "electrical permit" not in str(public.get("permit_name", "")).lower()
    assert "plumbing permit" not in str(public.get("permit_name", "")).lower()
    assert "building" in text

    relocated = _apply(
        {"permit_decision": "REQUIRED", "permit_required": True, "permits_required": [{"permit_type": "Plumbing Permit", "required": True}]},
        "replace kitchen faucet and garbage disposal with drain relocation and new water line",
        "Charlotte",
        "NC",
    )
    assert "plumbing" in _families(relocated)

    food = _apply(
        {"permit_decision": "REQUIRED", "permit_required": True, "permits_required": [{"permit_type": "Food Establishment Health Plan Review / Permit", "required": True}]},
        "commercial restaurant kitchen remodel with food service equipment",
        "Phoenix",
        "AZ",
        category="commercial",
    )
    assert "health" in _families(food)
