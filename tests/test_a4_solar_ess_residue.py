#!/usr/bin/env python3
"""A4 ESS/solar/battery residue suppression tests."""

import os
import re
import sys
from copy import deepcopy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine

LEAK_TERMS = ("ESS", "solar", "battery")


def _leaky_result(primary_scope="residential"):
    return {
        "_primary_scope": primary_scope,
        "pro_tips": [
            "Book the rough inspection before closing walls.",
            "ESS NEC 706 disconnect requirements must be labeled.",
            "Keep manufacturer cut sheets with the application. Solar PV ground-mount setbacks per IRC 324.10 do not belong here.",
        ],
        "common_mistakes": [
            "Missing product approvals delays review.",
            "Battery storage system ventilation requirements are often missed.",
        ],
        "watch_out": [
            "Confirm utility shutoff window.",
            "Energy Storage System (ESS) inspection per NFPA 855 may be required.",
        ],
        "expert_notes": [
            "Use the AHJ portal checklist.",
            "PV interconnection is separate from the building permit.",
        ],
        "inspections": [
            {
                "stage": "Final",
                "notes": "Verify installation matches approved plans. ESS clearances per NFPA 855 are not relevant.",
                "fail_points": ["No access to work area", "ESS marking at the main service per NEC 706.10"],
            }
        ],
        "permits_required": [
            {"permit_type": "Building Permit", "notes": "Standard permit notes. Solar adders should be stripped."}
        ],
        "hidden_triggers": [],
    }


def _combined_checked_text(result):
    parts = []
    for key in ("pro_tips", "common_mistakes", "watch_out", "expert_notes"):
        parts.extend(str(x) for x in result.get(key, []))
    for insp in result.get("inspections", []):
        parts.append(str(insp.get("notes", "")))
        parts.extend(str(x) for x in insp.get("fail_points", []))
    for permit in result.get("permits_required", []):
        parts.append(str(permit.get("notes", "")))
    return "\n".join(parts)


def _assert_no_leaks(result, terms=LEAK_TERMS):
    text = _combined_checked_text(result)
    for term in terms:
        assert not re.search(r"\b" + re.escape(term) + r"\b", text, re.I)


def test_phoenix_water_heater_no_solar_residue():
    out = engine.purge_solar_ess_residue(_leaky_result(), "replace residential water heater like for like")
    _assert_no_leaks(out)
    assert "Book the rough inspection" in " ".join(out["pro_tips"])


def test_phoenix_detached_adu_no_solar_residue():
    out = engine.purge_solar_ess_residue(_leaky_result("residential_adu"), "new detached ADU in Phoenix")
    _assert_no_leaks(out)


def test_vegas_reroof_no_solar_text():
    out = engine.purge_solar_ess_residue(_leaky_result(), "Las Vegas reroof asphalt shingles like-for-like")
    _assert_no_leaks(out, ("solar", "PV", "IRC 324.10"))


def test_seattle_deck_no_ess_text():
    out = engine.purge_solar_ess_residue(_leaky_result(), "Seattle second floor deck replacement")
    _assert_no_leaks(out, ("ESS", "Energy Storage", "NFPA 855"))


def test_dallas_window_replacement_no_ess_solar_text():
    out = engine.purge_solar_ess_residue(_leaky_result(), "Dallas replace 12 windows")
    _assert_no_leaks(out)


def test_la_garage_to_jadu_no_ess_text():
    out = engine.purge_solar_ess_residue(_leaky_result("residential_adu"), "Los Angeles garage conversion to JADU")
    _assert_no_leaks(out, ("ESS", "Energy Storage", "NFPA 855"))


def test_panel_upgrade_with_battery_backup_keeps_ess_guidance():
    before = _leaky_result()
    out = engine.purge_solar_ess_residue(deepcopy(before), "200 amp panel upgrade with battery backup")
    assert out == before
    assert "Battery storage" in _combined_checked_text(out)


def test_solar_pv_install_with_battery_keeps_full_guidance():
    before = _leaky_result("residential")
    out = engine.purge_solar_ess_residue(deepcopy(before), "solar PV install + battery storage")
    assert out == before
    assert "Solar PV" in _combined_checked_text(out)


def test_hidden_trigger_solar_id_keeps_guidance():
    before = _leaky_result()
    before["hidden_triggers"] = [{"id": "battery_ess_clearance"}]
    out = engine.purge_solar_ess_residue(deepcopy(before), "residential panel work")
    assert out == before


def test_commercial_restaurant_ti_untouched():
    before = _leaky_result("commercial_restaurant")
    out = engine.purge_solar_ess_residue(deepcopy(before), "commercial restaurant TI with patio")
    assert out == before


def test_commercial_office_ti_untouched():
    before = _leaky_result("commercial_office_ti")
    out = engine.purge_solar_ess_residue(deepcopy(before), "office tenant improvement")
    assert out == before


def test_sentence_level_cleanup_preserves_non_solar_sentence():
    r = _leaky_result()
    r["inspections"][0]["notes"] = "Verify approved plans at final. Battery inspection is not in scope. Confirm smoke alarms where required."
    out = engine.purge_solar_ess_residue(r, "Phoenix water heater")
    assert out["inspections"][0]["notes"] == "Verify approved plans at final. Confirm smoke alarms where required."
