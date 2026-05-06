#!/usr/bin/env python3
"""I3 last-verified badge metadata per city / rulebook tier."""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine


def _out(job, city, state):
    result = {
        "confidence": "high",
        "applying_office": f"{city} Building Department",
        "apply_url": "https://example.gov/permits",
    }
    return engine.apply_rulebook_depth(result, job, city, state)


def test_phoenix_deep_last_verified_stress_test_date():
    out = _out("restaurant TI with hood and grease interceptor", "Phoenix", "AZ")
    assert out["rulebook_depth"] == "DEEP"
    assert out["_last_verified_at"] == "2026-04-28"


def test_bakersfield_medium_last_verified_engine_commit_date():
    out = _out("restaurant TI", "Bakersfield", "CA")
    assert out["rulebook_depth"] == "MEDIUM"
    assert out["_last_verified_at"] == engine.RULEBOOK_ENGINE_COMMIT_VERIFIED_AT


def test_honolulu_state_default_has_no_last_verified_field():
    out = _out("water heater replacement", "Honolulu", "HI")
    assert out["rulebook_depth"] == "STATE_DEFAULT"
    assert "_last_verified_at" not in out


def test_la_hillside_adu_deep_last_verified_populated():
    out = _out("hillside ADU with geotech", "Los Angeles", "CA")
    assert out["rulebook_depth"] == "DEEP"
    assert out["_last_verified_at"]


def test_deep_and_medium_dates_do_not_collide():
    deep = _out("restaurant TI", "Phoenix", "AZ")
    medium = _out("restaurant TI", "Bakersfield", "CA")
    assert deep["rulebook_depth"] == "DEEP"
    assert medium["rulebook_depth"] == "MEDIUM"
    assert deep["_last_verified_at"] != medium["_last_verified_at"]


def test_last_verified_field_is_iso_date():
    for out in (
        _out("restaurant TI", "Phoenix", "AZ"),
        _out("restaurant TI", "Bakersfield", "CA"),
        _out("hillside ADU", "Los Angeles", "CA"),
    ):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", out["_last_verified_at"])
