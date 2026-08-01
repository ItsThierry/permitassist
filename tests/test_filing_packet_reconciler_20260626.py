import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from filing_packet_reconciler import (  # noqa: E402
    CACHE_SCHEMA_VERSION,
    FILING_PACKET_RECONCILER_VERSION,
    detect_filing_scope_signals,
    ensure_required_filing_rows,
)


def _families(result: dict) -> set[str]:
    return {
        str(row.get("filing_family") or "")
        for row in result.get("permits_required") or []
        if isinstance(row, dict) and row.get("filing_family")
    }


def _row(result: dict, family: str) -> dict:
    for row in result.get("permits_required") or []:
        if isinstance(row, dict) and row.get("filing_family") == family:
            return row
    raise AssertionError(f"missing family {family}; got {_families(result)}")


def _surface(result: dict) -> str:
    return json.dumps(result, sort_keys=True).lower()


def test_sf_adu_garage_conversion_subpanel_injects_electrical_required_row():
    result = {
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permits_required": [
            {"permit_type": "ADU Building Permit", "required": True, "filing_family": "building_adu"},
        ],
        "checklist": ["Show new subpanel load calculation and electrical single-line diagram."],
        "pro_tips": ["DBI may route electrical trade review separately from the ADU building permit."],
    }
    job = "San Francisco ADU garage conversion with kitchenette, bathroom, and a new subpanel"

    out = ensure_required_filing_rows(result, job, "San Francisco", "CA")

    assert "electrical" in _families(out)
    electrical = _row(out, "electrical")
    # Scope establishes a candidate filing lane, not jurisdiction authority.
    assert electrical["required"] is False
    assert electrical["decision"] == "VERIFY"
    assert "new_subpanel_or_service_upgrade" in electrical["trigger_signal_ids"]
    assert electrical["apply_url_status"] in {"needs_verification", "needs_reverification", "verified"}
    assert out["_filing_packet_reconciler_version"] == FILING_PACKET_RECONCILER_VERSION


def test_phoenix_taqueria_bar_ti_requires_full_filing_packet_and_bans_bad_anchors():
    result = {
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "apply_url": "https://aca-prod.accela.com/PHOENIX",
        "fee_range": "$558 combined mechanical/electrical/building permit (Phoenix single permit system)",
        "permits_required": [
            {"permit_type": "Commercial Building Permit", "required": True},
            {"permit_type": "Electrical Permit", "required": True},
        ],
        "checklist": [
            "Coordinate Maricopa County food-establishment plan review.",
            "Start Arizona liquor license / local governing body routing early.",
            "Show grease interceptor / FOG and wastewater pretreatment details.",
            "Confirm Certificate of Occupancy / change-of-occupancy and zoning use compatibility.",
        ],
        "hidden_triggers": [
            {"id": "restaurant_type_i_hood_mechanical_exhaust"},
            {"id": "phoenix_restaurant_hood_fire_suppression"},
            {"id": "restaurant_grease_interceptor_fog_review"},
            {"id": "restaurant_alcohol_service_liquor_routing"},
            {"id": "phoenix_restaurant_b_to_a2_sprinkler_change_of_occupancy"},
        ],
    }
    job = (
        "Phoenix AZ 2,400 sf taqueria/bar tenant improvement from retail to restaurant: "
        "commercial kitchen, Type I hood with ANSUL, new subpanel, HVAC/MAU, restrooms, "
        "grease interceptor/FOG, alcohol bar service, B/M to A-2 change of occupancy"
    )

    out = ensure_required_filing_rows(result, job, "Phoenix", "AZ")

    assert {
        "building_ti",
        "electrical",
        "plumbing",
        "mechanical",
        "fire_suppression",
        "health_food_establishment",
        "liquor_license",
        "wastewater_pretreatment_fog",
        "co_change_of_occupancy",
        "planning_zoning",
    }.issubset(_families(out))
    assert _row(out, "health_food_establishment")["ahj_name"] == "Maricopa County Environmental Services"
    assert "liquor" in _row(out, "liquor_license")["permit_type"].lower()
    assert _row(out, "wastewater_pretreatment_fog")["decision"] == "VERIFY"
    text = _surface(out)
    assert "aca-prod.accela.com/phoenix" not in text
    assert "$558" not in text
    assert "558 combined" not in text


SIGNAL_CASES = [
    ("service upgrade and new subpanel", {"electrical"}),
    ("commercial kitchen for food service", {"health_food_establishment", "plumbing"}),
    ("new bar serving alcohol and cocktails", {"liquor_license"}),
    ("grease interceptor and FOG wastewater worksheet", {"wastewater_pretreatment_fog", "plumbing"}),
    ("Type I hood with ANSUL wet-chemical suppression", {"mechanical", "fire_suppression"}),
    ("retail to restaurant change of use / occupancy", {"co_change_of_occupancy", "planning_zoning", "fire_suppression"}),
]


def test_universal_scope_signal_mapper_properties():
    for phrase, expected_families in SIGNAL_CASES:
        signals = detect_filing_scope_signals(f"Project includes {phrase}.", {}, "Dallas", "TX")
        mapped = {family for signal in signals for family in signal.filing_families}
        assert expected_families.issubset(mapped), phrase


def test_not_just_phrase_does_not_negate_required_trade_families():
    signals = detect_filing_scope_signals(
        "Restaurant TI includes not just electrical but also plumbing, Type I hood, and ANSUL suppression.",
        {},
        "Los Angeles",
        "CA",
    )
    mapped = {family for signal in signals for family in signal.filing_families}
    assert {"electrical", "plumbing", "mechanical", "fire_suppression"}.issubset(mapped)


def test_advisory_mentions_do_not_author_regulated_rows_and_text_is_preserved():
    result = {
        "permits_required": [{"permit_type": "Building Permit", "required": True}],
        "checklist": ["Before opening, obtain the health department food establishment permit."],
        "common_mistakes": ["Forgetting the liquor license can delay bar opening."],
    }
    out = ensure_required_filing_rows(result, "restaurant tenant improvement", "Seattle", "WA")
    # Restaurant scope exposes the health lane, but warning prose that merely
    # mentions liquor cannot create a regulated liquor-license row.
    assert "health_food_establishment" in _families(out)
    assert "liquor_license" not in _families(out)
    assert "health department food establishment permit" in _surface(out)
    assert "forgetting the liquor license" in _surface(out)


def test_model_emitted_phoenix_accela_url_is_never_authoritative():
    result = {
        "apply_url": "https://aca-prod.accela.com/PHOENIX",
        "permits_required": [
            {"permit_type": "Commercial Building Permit", "required": True, "apply_url": "https://aca-prod.accela.com/PHOENIX"},
        ],
    }
    out = ensure_required_filing_rows(result, "commercial tenant improvement", "Phoenix", "AZ")
    text = _surface(out)
    assert "aca-prod.accela.com/phoenix" not in text
    assert out.get("apply_url") in (None, "")
    assert _row(out, "building_ti")["apply_url_status"] == "needs_verification"


def test_reconciler_stamps_cache_schema_version_to_block_stale_cached_outputs():
    out = ensure_required_filing_rows(
        {"_cache_schema_version": "permit_cache_v3_pre_reconciler", "permits_required": []},
        "Seattle restaurant TI with food service and grease interceptor",
        "Seattle",
        "WA",
    )
    assert out["_cache_schema_version"] == CACHE_SCHEMA_VERSION
    assert out["_filing_packet_reconciler_version"] == FILING_PACKET_RECONCILER_VERSION
    assert "health_food_establishment" in _families(out)


def test_reconciler_preserves_source_adjudicated_not_required_even_with_trade_words():
    out = ensure_required_filing_rows(
        {
            "permit_decision": "NOT_REQUIRED",
            "permit_required": False,
            "permit_verdict": "NO",
            "permits_required": [],
        },
        "like-for-like low voltage thermostat fixture swap with electrical wiring words",
        "Phoenix",
        "AZ",
    )
    assert out["permit_decision"] == "NOT_REQUIRED"
    assert out["permit_required"] is False
    assert out.get("permits_required") == []
    assert out["_cache_schema_version"] == CACHE_SCHEMA_VERSION
    assert out["_filing_packet_reconciler"]["skipped"] == "preserved_not_required_decision"


def test_seattle_mini_split_reconciler_keeps_refrigeration_and_repairs_stale_only_prose():
    out = ensure_required_filing_rows(
        {
            "permit_decision": "REQUIRED",
            "permit_required": True,
            "permit_verdict": "YES",
            "job_summary": (
                "Install a residential ductless mini-split heat pump with an exterior condenser and refrigerant line set in Seattle. "
                "This scope triggers a Mechanical Permit — Mini Split System (Ductless) only. No separate electrical permit is required from the stated scope unless the installer is adding a new circuit, breaker, disconnect, or panel/service work."
            ),
            "permits_required": [
                {"permit_type": "Mechanical Permit — HVAC Equipment Changeout (Residential)", "required": True},
                {
                    "permit_type": "Refrigeration Permit — Split-System Heat Pump / Mini-Split",
                    "required": True,
                    "source_url": "https://services.seattle.gov/Portal/Customization/pages/recordindex.aspx",
                    "source_type": "official",
                },
            ],
        },
        "Install residential ductless mini-split heat pump with exterior condenser and refrigerant line set",
        "Seattle",
        "WA",
    )
    families = _families(out)
    assert {"mechanical", "refrigeration", "electrical"}.issubset(families)
    assert _row(out, "refrigeration")["decision"] == "REQUIRED"
    surface = _surface(out)
    assert "mechanical permit" in surface
    assert "refrigeration permit" in surface
    assert "electrical permit" in surface
    assert "mechanical permit — mini split system (ductless) only" not in surface
    assert "no separate electrical permit is required" not in surface


def test_residential_water_heater_advisory_does_not_inject_untriggered_filing_families():
    out = ensure_required_filing_rows(
        {
            "permit_decision": "REQUIRED",
            "permit_required": True,
            "permit_verdict": "YES",
            "permits_required": [
                {"permit_type": "Residential Plumbing Permit — Water Heater Replacement", "required": True},
            ],
            "pro_tips": [
                "Confirm whether your job should stay under the combined permit instead of splitting plumbing, mechanical, and electrical filings."
            ],
            "common_mistakes": [
                "Using the wrong permit workflow and trying to force a separate mechanical permit when Phoenix expects a combined permit for this scope."
            ],
        },
        "replace gas water heater like for like",
        "Phoenix",
        "AZ",
    )
    families = _families(out)
    assert families == set()


ADVERSARIAL_GOLDENS = [
    ("Seattle restaurant TI with commercial kitchen, Type I hood, grease interceptor, and bar", "Seattle", "WA", {"building_ti", "health_food_establishment", "mechanical", "fire_suppression", "wastewater_pretreatment_fog", "liquor_license"}),
    ("Los Angeles restaurant tenant improvement with food prep, walk-in cooler, hood, grease interceptor", "Los Angeles", "CA", {"building_ti", "health_food_establishment", "mechanical", "fire_suppression", "wastewater_pretreatment_fog"}),
    ("Dallas restaurant finish-out with commercial kitchen, grease trap, alcohol bar, and CO", "Dallas", "TX", {"building_ti", "health_food_establishment", "wastewater_pretreatment_fog", "liquor_license", "co_change_of_occupancy"}),
    ("Laundromat tenant improvement with gas dryers, dryer exhaust, floor drains, and wastewater discharge", "Chicago", "IL", {"building_ti", "mechanical", "plumbing", "wastewater_pretreatment_fog"}),
    ("Urgent care tenant improvement with x-ray room, medical gas, exam sinks, and change of occupancy", "Austin", "TX", {"building_ti", "plumbing", "mechanical", "co_change_of_occupancy", "planning_zoning"}),
    ("Hair salon tenant improvement with shampoo sinks, chemical storage, fire inspection, and zoning clearance", "Portland", "OR", {"building_ti", "plumbing", "fire_suppression", "planning_zoning"}),
    ("Daycare tenant improvement with new classroom occupancy, fire inspection, zoning clearance, and certificate of occupancy", "Denver", "CO", {"building_ti", "fire_suppression", "planning_zoning", "co_change_of_occupancy"}),
]


def test_adversarial_non_phoenix_sf_golden_cases_keep_scope_families_visible_without_heuristic_hard_requirements():
    for job, city, state, expected in ADVERSARIAL_GOLDENS:
        out = ensure_required_filing_rows({"permits_required": []}, job, city, state)
        assert expected.issubset(_families(out)), (job, _families(out))
        for family in expected:
            row = _row(out, family)
            if row["decision"] == "REQUIRED":
                assert row.get("source_status") == "verified"
                assert row.get("source_url")
            else:
                assert row["decision"] == "VERIFY"
