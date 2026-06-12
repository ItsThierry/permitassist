#!/usr/bin/env python3
"""BG-POLISH-01 engine regressions for commercial/residential scope separation."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import api.research_engine as engine
from api.decision_resolver import resolve_customer_decision

# Several legacy server-helper tests import/patch the engine through the top-level
# `research_engine` name. Keep both import paths pointed at the same module when
# this regression file is imported first in a pytest process.
sys.modules["research_engine"] = engine


def _permit_blob(result):
    return " | ".join(
        " ".join(str(p.get(k, "")) for k in ("permit_type", "portal_selection", "notes"))
        for p in result.get("permits_required", [])
        if isinstance(p, dict)
    ).lower()


def test_task0_commercial_rtu_swap_does_not_suppress_implied_trade_companions():
    job = (
        "Replacing the rooftop HVAC unit on a small commercial office building in Battle Ground, WA, "
        "like-for-like swap, existing gas connection to the unit, existing electrical disconnect. "
        "What permits do I need?"
    )

    out = engine.classify_scope_required_permits(job)

    assert out is not None
    assert out["scope_classification"] == "commercial_mechanical_hvac"
    assert "residential" not in _permit_blob(out)
    companions = " | ".join(
        f"{cp.get('permit_type', '')} {cp.get('reason', '')}"
        for cp in out.get("companion_permits", [])
        if isinstance(cp, dict)
    ).lower()
    assert "gas" in companions
    assert "electrical" in companions
    assert "suppressed" not in companions


def test_bg_laundromat_fallback_lookup_classifies_as_commercial_ti_not_residential_hvac():
    job = (
        "Converting former retail space into self-service laundromat in Battle Ground WA, 2,400 sq ft; "
        "20 washers and 20 gas dryers; new 600A three-phase electrical service; new gas line/manifold; "
        "dryer exhaust and makeup air; extensive plumbing, floor drains, commercial water heater; "
        "ADA restroom upgrade; larger replacement RTU; budget around $240k."
    )

    out = engine.classify_scope_required_permits(job)

    assert out is not None
    assert out["scope_classification"] == "commercial"
    blob = _permit_blob(out)
    assert "commercial" in blob
    assert "tenant improvement" in blob or "interior alteration" in blob or "change of use" in blob
    assert "residential" not in blob
    assert "hvac system replacement" not in blob
    assert "suppressed" not in blob
    families = {engine._permit_family(p) for p in out["permits_required"]}
    assert {"building", "mechanical", "electrical", "plumbing", "gas"}.issubset(families)


def test_task1_residential_hvac_label_still_renders_for_residential_lookup():
    job = "Replace residential HVAC condenser at single-family home, like-for-like swap, existing electrical disconnect."

    out = engine.classify_scope_required_permits(job)

    assert out is not None
    blob = _permit_blob(out)
    assert "residential" in blob
    assert "hvac" in blob
    assert out["scope_classification"] == "hvac_or_water_heater_single_trade"


def test_residential_converting_language_does_not_promote_to_commercial_ti():
    job = "Residential single-family home converting old gas water heater to electric in the same garage location."

    out = engine.classify_scope_required_permits(job)

    assert out is not None
    assert out["scope_classification"] == "hvac_or_water_heater_single_trade"
    assert "commercial" not in _permit_blob(out)


def test_gas_fired_mechanical_permit_stays_mechanical_family():
    permit = {
        "permit_type": "Mechanical Permit — Gas-fired RTU Changeout",
        "portal_selection": "Mechanical - Commercial HVAC",
    }

    assert engine._permit_family(permit) == "mechanical"


def test_fee_verify_caveat_is_idempotent():
    result = {"fee_range": "Fee Estimate: $500-$700"}

    once = engine.apply_fee_verify_caveat(result.copy())
    twice = engine.apply_fee_verify_caveat(once.copy())

    caveat = "verify with the building department before quoting"
    assert twice["fee_range"].lower().count(caveat) == 1


def test_change_of_use_resolver_preserves_existing_trade_rows():
    job = "Commercial restaurant tenant improvement with Type I hood and grease interceptor"
    result = {
        "permit_verdict": "YES",
        "permit_name": "Commercial Tenant Improvement Building Permit — Restaurant TI",
        "permits_required": [
            {"permit_type": "Commercial Tenant Improvement Building Permit — Restaurant TI", "required": True},
            {"permit_type": "Mechanical Permit — Type I Hood", "required": True},
        ],
        "sources": ["https://www.phoenix.gov/pdd/development/permits"],
    }

    dto = resolve_customer_decision({"result": result, "job_type": job, "city": "Phoenix", "state": "AZ"})
    permit_names = [p.get("permit_type") for p in dto.get("permits_required", [])]

    assert dto["permit_decision"] == "REQUIRED"
    assert any("Tenant Improvement" in name for name in permit_names)
    assert any("Mechanical Permit" in name for name in permit_names)
