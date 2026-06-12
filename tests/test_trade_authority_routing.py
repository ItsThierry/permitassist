import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "api"
for path in (str(ROOT), str(API_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from trade_authority_routing import apply_trade_authority_routing, build_trade_authority_routing
from hidden_trigger_detector import detect_hidden_triggers


BATTLE_GROUND_JOB = (
    "Convert a 2400 sq ft former retail space into a laundromat with 20 washers, "
    "20 gas dryers, new 600A three-phase electrical service, gas line/manifold, "
    "dryer exhaust/makeup air, plumbing/floor drains, water heater, ADA restroom, larger RTU."
)


def _base_result():
    return {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_kind": "Commercial Building / Tenant Improvement",
        "permit_name": "Commercial Building / Tenant Improvement Permit",
        "applying_office": "Battle Ground Community Development Department / Building Division",
        "permits_required": [
            {
                "permit_type": "Commercial Building / Tenant Improvement Permit",
                "required": True,
                "notes": "Covers change of use, buildout, plumbing, gas, mechanical, electrical, accessibility, and final occupancy coordination.",
            }
        ],
        "inspections": [
            {"stage": "Building / accessibility rough-in", "description": "Framing, restroom, path of travel."},
            {"stage": "Electrical Rough-In / Service Equipment", "description": "600A service, switchgear, panels."},
            {"stage": "Gas pressure test", "description": "Dryer gas manifold."},
        ],
        "sources": [
            {
                "url": "https://www.cityofbg.org/958/Commercial-Building-Information",
                "title": "Commercial Building Information | Battle Ground, WA",
                "snippet": (
                    "Electrical permits and electrical inspections shall be obtained at Washington State Department "
                    "of Labor and Industries (L&I). Contractors working within city limits must have a Battle Ground "
                    "Business License or Endorsement."
                ),
            },
            {
                "url": "https://lni.wa.gov/licensing-permits/electrical/electrical-permits-fees-and-inspections/",
                "title": "Electrical Permits, Fees & Inspections",
                "snippet": "Electrical work in Washington requires a permit and inspection from the correct jurisdiction.",
            },
        ],
    }


def test_battle_ground_routes_electrical_to_lni_and_filters_city_inspection():
    out = apply_trade_authority_routing(_base_result(), BATTLE_GROUND_JOB, "Battle Ground", "WA")

    electrical = out["permit_routing_map"]["electrical"]
    assert electrical["authority"] == "Washington State Department of Labor & Industries (L&I)"
    assert electrical["authority_level"] == "state"
    assert electrical["source_ref"]["binding"] == "source_text"

    permits_blob = " ".join(str(p) for p in out["permits_required"])
    assert "Electrical Permit / Electrical Inspections" in permits_blob
    assert "Washington State Department of Labor & Industries" in permits_blob

    # Top-level inspection list is the city-facing list; state-routed electrical
    # inspections must move to the state authority card instead of implying city inspection.
    city_inspections_blob = " ".join(str(item) for item in out.get("inspections", []))
    assert "Electrical Rough-In" not in city_inspections_blob
    assert "600A service" not in city_inspections_blob

    lni_cards = [card for card in out["permit_authority_cards"] if "Labor & Industries" in card["authority"]]
    assert len(lni_cards) == 1
    lni_inspections_blob = " ".join(str(item) for item in lni_cards[0]["inspections"])
    assert "Electrical Rough-In" in lni_inspections_blob
    assert "600A service" in lni_inspections_blob

    assert "business license" in out["city_contractor_registration"].lower()


def test_source_text_binding_without_lni_url_still_creates_electrical_routing_edge():
    result = _base_result()
    result["sources"] = [result["sources"][0]]  # city page text only, no separate L&I URL
    routing = build_trade_authority_routing(result, BATTLE_GROUND_JOB, "Battle Ground", "WA")
    electrical = routing["routing_map"]["electrical"]
    assert electrical["authority_short"] == "WA L&I"
    assert electrical["source_ref"]["binding"] == "source_text"
    assert "cityofbg.org" in electrical["source_ref"]["url"]


def test_wisconsin_dsps_class_routes_commercial_electrical_by_same_overlay():
    result = {
        "permit_required": True,
        "permit_kind": "Commercial Building / Tenant Improvement",
        "permit_name": "Commercial Alteration Permit",
        "applying_office": "Madison Building Inspection Division",
        "permits_required": [{"permit_type": "Commercial Alteration Permit", "required": True}],
        "sources": [
            {
                "url": "https://dsps.wi.gov/Pages/Programs/DelegatedAgents.aspx",
                "title": "DSPS Division of Industry Services Delegated Agents",
                "snippet": "DSPS may delegate commercial electrical permitting and inspection responsibilities to municipalities.",
            }
        ],
    }
    out = apply_trade_authority_routing(result, "Commercial TI with new electrical service and panels", "Madison", "WI")
    electrical = out["permit_routing_map"]["electrical"]
    assert electrical["authority_short"] == "WI DSPS"
    assert electrical["authority_level"] == "state"
    assert any("DSPS" in p.get("authority", "") for p in out["permits_required"])


def test_delegated_city_exception_does_not_overroute_to_lni():
    result = {
        "permit_required": True,
        "permit_kind": "Electrical",
        "permit_name": "Electrical Permit",
        "applying_office": "Seattle Department of Construction and Inspections",
        "permits_required": [{"permit_type": "Electrical Permit", "required": True}],
        "sources": [{"url": "https://www.seattle.gov/sdci/permits/permits-we-issue-(a-z)/electrical-permit", "title": "Seattle electrical permits", "snippet": "Seattle electrical permit information."}],
    }
    out = apply_trade_authority_routing(result, "Commercial panel upgrade", "Seattle", "WA")
    assert "permit_routing_map" not in out
    assert all("Labor & Industries" not in p.get("authority", "") for p in out["permits_required"])


def test_no_overlay_default_does_not_rewrite_existing_permit_cards():
    result = {
        "permit_required": True,
        "permit_kind": "Electrical",
        "permit_name": "Electrical Permit",
        "applying_office": "Phoenix Planning & Development Department",
        "permits_required": [{"permit_type": "Electrical Permit", "required": True, "notes": "Existing behavior"}],
    }
    out = apply_trade_authority_routing(result, "Commercial panel upgrade", "Phoenix", "AZ")
    assert out["permits_required"] == result["permits_required"]
    assert "permit_routing_map" not in out


def test_larger_rtu_structural_trigger_fires_when_rtu_preceded_by_larger():
    fired = detect_hidden_triggers(
        "Commercial laundromat TI with larger RTU replacing existing unit",
        "Battle Ground",
        "WA",
        "commercial_ti",
        {},
    )
    ids = {item["id"] for item in fired}
    assert "rtu_replacement_structural_check" in ids
