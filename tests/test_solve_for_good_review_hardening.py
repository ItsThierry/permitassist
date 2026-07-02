from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from closed_world_decision import apply_closed_world_customer_contract  # noqa: E402
from server import build_customer_permit_view_model  # noqa: E402


def test_server_path_runs_closed_world_gate_when_forced_on(monkeypatch):
    monkeypatch.setenv("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD", "1")
    public = build_customer_permit_view_model({}, "install illuminated wall sign", "Gilbert", "AZ", job_category="commercial")
    assert public.get("decision_object", {}).get("schema_version") == "decision_object.v1"
    assert public.get("permit_decision") == "REQUIRED"
    assert {row.get("family") for row in public.get("permits_required") or []} == {"sign", "electrical"}
    assert public.get("render_fidelity", {}).get("pass") is True


def test_server_path_honors_closed_world_gate_off(monkeypatch):
    monkeypatch.setenv("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD", "0")
    public = build_customer_permit_view_model({}, "install illuminated wall sign", "Gilbert", "AZ", job_category="commercial")
    assert "decision_object" not in public


def test_fuel_dispenser_scope_keeps_fire_environmental_review():
    public = apply_closed_world_customer_contract({}, "commercial service station canopy replacement with fuel dispenser electrical and structural foundations", "Jackson", "MS", job_category="commercial")
    families = {row.get("family") for row in public.get("permits_required") or []}
    assert "fire_suppression" in families
    assert "electrical" in families


def test_change_to_wine_bar_keeps_change_of_occupancy_review():
    public = apply_closed_world_customer_contract({}, "commercial art gallery change to wine bar with occupant load increase plumbing fixtures and hoodless food prep", "Santa Fe", "NM", job_category="commercial")
    families = {row.get("family") for row in public.get("permits_required") or []}
    assert "co_change_of_occupancy" in families
    assert "building_ti" in families


def test_solar_plus_battery_is_not_collapsed_to_battery_only():
    public = apply_closed_world_customer_contract({}, "install rooftop solar PV with battery and main panel upgrade on single family home", "Los Angeles", "CA", job_category="residential")
    families = {row.get("family") for row in public.get("permits_required") or []}
    assert {"battery_storage", "solar_pv", "electrical", "building"}.issubset(families)


def test_existing_solar_battery_does_not_invent_new_pv():
    public = apply_closed_world_customer_contract({}, "install residential battery backup tied to existing solar system", "Boise", "ID", job_category="residential")
    families = {row.get("family") for row in public.get("permits_required") or []}
    assert "battery_storage" in families
    assert "solar_pv" not in families


def test_public_packet_marks_ti_lead_row():
    public = apply_closed_world_customer_contract({}, "commercial office tenant improvement with new partitions lighting and HVAC", "Phoenix", "AZ", job_category="commercial")
    assert public["render_fidelity"]["pass"] is True
    lead_rows = [row for row in public["public_packet_rows"] if row.get("lead")]
    assert len(lead_rows) == 1
    assert lead_rows[0]["family"] == "building_ti"
    assert lead_rows[0]["permit_name"] == public["permit_name"]


def test_segment_mismatched_links_are_quarantined():
    source = {
        "source_urls": [
            "https://www.phoenix.gov/residents/residential-building-permits.html",
            "https://www.phoenix.gov/pdd/development/permits/commercial",
        ]
    }
    commercial = apply_closed_world_customer_contract(source, "commercial office tenant improvement with new partitions", "Phoenix", "AZ", job_category="commercial")
    assert "https://www.phoenix.gov/residents/residential-building-permits.html" not in commercial["source_urls"]
    assert any(s["reason"] == "segment_mismatch_residential_url" for s in commercial["link_liveness"])
    assert "https://www.phoenix.gov/pdd/development/permits/commercial" in commercial["source_urls"]


def test_public_rows_do_not_expose_internal_gate_language():
    public = apply_closed_world_customer_contract({}, "install illuminated wall sign", "Gilbert", "AZ", job_category="commercial")
    text = str(public.get("permits_required")) + str(public.get("permits_required_logic"))
    assert "project_scope_attributes.v1" not in text
    assert "Deterministic closed-world" not in text
    assert "provenance" not in text
