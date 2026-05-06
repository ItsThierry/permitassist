#!/usr/bin/env python3
"""A7 state/local amendment citations."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine


def _result(scope, section="IBC 105.1", text="Permit required", permits=None):
    return {
        "_primary_scope": scope,
        "code_citation": {"section": section, "text": text},
        "permits_required": permits or [{"permit_type": "Building Permit"}],
        "companion_permits": [],
        "inspections": [],
        "what_to_bring": [],
        "common_mistakes": [],
        "pro_tips": [],
    }


def _codes(out):
    return " | ".join(str(c.get("code") or c.get("section")) for c in out["code_citation"])


def _amendments(out):
    return [c for c in out["code_citation"] if c.get("type") == "state_amendment"]


def test_la_hillside_adu_includes_cbc_and_title24():
    out = engine.apply_state_amendment_citations(_result("residential_adu"), "hillside ADU with energy forms", "Los Angeles", "CA")
    codes = _codes(out)
    assert "California Building Code (CBC)" in codes
    assert "California Energy Code Title 24 Part 6" in codes


def test_la_garage_jadu_includes_cbc_and_calgreen():
    out = engine.apply_state_amendment_citations(_result("residential_adu"), "garage conversion to JADU", "Los Angeles", "CA")
    codes = _codes(out)
    assert "California Building Code (CBC)" in codes
    assert "CALGreen Title 24 Part 11" in codes


def test_phoenix_hvac_includes_pbcc():
    out = engine.apply_state_amendment_citations(_result("residential", "IMC 106.1", permits=[{"permit_type": "Mechanical Permit — HVAC"}]), "HVAC replacement", "Phoenix", "AZ")
    assert "Phoenix Building Construction Code (PBCC)" in _codes(out)


def test_phoenix_restaurant_ti_includes_pbcc_and_phoenix_amendments():
    out = engine.apply_state_amendment_citations(_result("commercial_restaurant"), "restaurant TI with hood", "Phoenix", "AZ")
    codes = _codes(out)
    assert "PBCC" in codes
    assert "Phoenix amendments" in codes


def test_vegas_clark_restaurant_ti_includes_clark_county_building_code():
    out = engine.apply_state_amendment_citations(_result("commercial_restaurant"), "restaurant TI", "Clark County / Las Vegas", "NV")
    assert "Clark County Building Code" in _codes(out)


def test_seattle_dadu_includes_wsec_r():
    out = engine.apply_state_amendment_citations(_result("residential_adu"), "DADU with energy compliance", "Seattle", "WA")
    assert "Washington State Energy Code Residential (WSEC-R)" in _codes(out)


def test_seattle_restaurant_ti_includes_wsec_c_and_sbc():
    out = engine.apply_state_amendment_citations(_result("commercial_restaurant"), "restaurant TI energy mechanical", "Seattle", "WA")
    codes = _codes(out)
    assert "Washington State Energy Code Commercial (WSEC-C)" in codes
    assert "Seattle Building Code (SBC)" in codes


def test_dallas_restaurant_ti_includes_tas_tdlr_tsbpe():
    out = engine.apply_state_amendment_citations(_result("commercial_restaurant", permits=[{"permit_type": "Mechanical Permit"}, {"permit_type": "Plumbing Permit"}]), "restaurant TI with hood grease interceptor ADA restroom", "Dallas", "TX")
    codes = _codes(out)
    assert "Texas Accessibility Standards (TAS)" in codes
    assert "TDLR" in codes
    assert "Texas Plumbing License Law (TSBPE)" in codes
    assert "Texas State Board of Plumbing Examiners" in codes


def test_dallas_residential_window_replacement_does_not_include_tas():
    out = engine.apply_state_amendment_citations(_result("residential"), "residential window replacement", "Dallas", "TX")
    assert "Texas Accessibility Standards (TAS)" not in _codes(out)


def test_cross_state_regression_no_ca_in_tx_and_no_tx_in_ca():
    tx = engine.apply_state_amendment_citations(_result("commercial_restaurant"), "restaurant TI ADA plumbing mechanical", "Dallas", "TX")
    ca = engine.apply_state_amendment_citations(_result("residential_adu"), "ADU", "Los Angeles", "CA")
    assert "California" not in _codes(tx) and "CALGreen" not in _codes(tx)
    assert "Texas" not in _codes(ca) and "TDLR" not in _codes(ca)
    assert all(c["state"] == "TX" for c in _amendments(tx))
    assert all(c["state"] == "CA" for c in _amendments(ca))


def test_preserves_universal_model_code_entry():
    out = engine.apply_state_amendment_citations(_result("residential_adu", "IBC 105.1"), "ADU", "Los Angeles", "CA")
    assert out["code_citation"][0]["section"] == "IBC 105.1"
    assert any(c.get("type") == "state_amendment" for c in out["code_citation"])
