#!/usr/bin/env python3
"""A5 ADU hidden-trigger depth expansion tests."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.hidden_trigger_detector import detect_hidden_triggers


def ids(job, city="Phoenix", state="AZ", primary_scope="residential_adu"):
    return {t["id"] for t in detect_hidden_triggers(job, city, state, primary_scope, {})}


def test_la_hillside_adu_fires_geotech_wui_tree():
    got = ids("Los Angeles hillside detached ADU in VHFHSZ with oak tree near foundation", "Los Angeles", "CA", "residential_hillside_adu")
    assert "adu_geotech_haul_route" in got
    assert "adu_vhfhsz_wui_compliance" in got
    assert "adu_tree_protection" in got


def test_la_garage_to_jadu_legalization_fires_parking_and_code_enforcement():
    got = ids("Legalization of existing unpermitted garage conversion to JADU, removing covered parking", "Los Angeles", "CA", "residential_jadu")
    assert "adu_parking_replacement" in got
    assert "adu_legalization_co_codeenforcement" in got


def test_phoenix_detached_adu_fires_sewer_and_impact_fees():
    got = ids("New detached ADU with kitchen and bath, new water and sewer connection", "Phoenix", "AZ")
    assert "adu_sewer_capacity" in got
    assert "adu_traffic_park_water_impact_fees" in got


def test_seattle_dadu_fires_sewer_and_tree_protection():
    got = ids("Seattle DADU in rear yard with kitchen bath and site work", "Seattle", "WA")
    assert "adu_sewer_capacity" in got
    assert "adu_tree_protection" in got


def test_vegas_adu_fires_sewer_and_impact_fees():
    got = ids("New detached ADU with separate meter and new sewer connection", "Las Vegas", "NV")
    assert "adu_sewer_capacity" in got
    assert "adu_traffic_park_water_impact_fees" in got


def test_dallas_adu_fires_sewer_only_not_ca_specific_or_impact_fee_guarded():
    got = ids("New detached ADU with kitchen and bath", "Dallas", "TX")
    assert "adu_sewer_capacity" in got
    assert "adu_traffic_park_water_impact_fees" not in got
    assert "adu_vhfhsz_wui_compliance" not in got
    assert "adu_school_impact_fees" not in got


def test_ca_adu_over_750_sf_fires_school_impact_fees():
    got = ids("New detached ADU 820 sf with kitchen and bath", "Los Angeles", "CA")
    assert "adu_school_impact_fees" in got


def test_ca_adu_750_sf_or_less_does_not_fire_school_impact_fees():
    got = ids("New detached ADU 750 sf with kitchen and bath", "Los Angeles", "CA")
    assert "adu_school_impact_fees" not in got


def test_hillside_geotech_not_fired_on_flat_lot_adu():
    got = ids("New detached ADU on flat lot with kitchen and bath", "Los Angeles", "CA")
    assert "adu_geotech_haul_route" not in got


def test_tree_protection_not_fired_in_phoenix_without_explicit_tree_context():
    got = ids("New detached ADU with kitchen and bath in rear yard", "Phoenix", "AZ")
    assert "adu_tree_protection" not in got


def test_residential_single_trade_scopes_do_not_fire_adu_triggers():
    non_adu_jobs = [
        "Replace residential water heater like for like",
        "HVAC changeout split system condenser and furnace",
        "Reroof asphalt shingles like for like",
        "Replace 12 windows same size",
    ]
    for job in non_adu_jobs:
        got = ids(job, "Phoenix", "AZ", "residential_single_trade")
        assert not {x for x in got if x.startswith("adu_")}, (job, got)


def test_commercial_scope_does_not_fire_adu_triggers_even_if_adu_text_appears():
    got = ids("Commercial office TI next to an ADU showroom display", "Los Angeles", "CA", "commercial_office_ti")
    assert not {x for x in got if x.startswith("adu_")}


def test_explicit_protected_tree_context_can_fire_outside_known_city():
    got = ids("New detached ADU with oak protected tree removal", "Portland", "OR")
    assert "adu_tree_protection" in got
