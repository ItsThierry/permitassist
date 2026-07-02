from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from scope_contract import build_scope_facts_v2


def test_anchored_negation_extracts_no_electrical_and_illumination():
    facts = build_scope_facts_v2("install non-electric sign face, no electrical work", "Minneapolis", "MN")
    assert "no_electrical" in facts.negative_facts
    assert "no_illumination" in facts.negative_facts


def test_absence_is_not_negation():
    facts = build_scope_facts_v2("replace plumbing fixture in same location", "Dallas", "TX")
    assert "no_electrical" not in facts.negative_facts


def test_negation_of_unrelated_noun_not_captured():
    facts = build_scope_facts_v2("no problems with the electrical inspection last year; replace sink now", "Dallas", "TX")
    assert "no_electrical" not in facts.negative_facts


def test_amperage_extraction():
    facts = build_scope_facts_v2("commercial laundromat conversion with 600A service", "Kansas City", "MO")
    assert facts.service_amperage == 600


def test_service_amperage_does_not_parse_bare_suite_letter():
    facts = build_scope_facts_v2("Suite 300 A interior paint only", "Phoenix", "AZ")
    assert facts.service_amperage is None


def test_generator_transfer_switch_triggers_electrical():
    facts = build_scope_facts_v2("install standby generator with automatic transfer switch and new gas line", "Austin", "TX")
    assert "electrical" in facts.positive_facts
    assert "plumbing" in facts.positive_facts


def test_bathroom_tub_valve_triggers_plumbing():
    facts = build_scope_facts_v2("apartment bathroom renovation replacing tub valve lighting and exhaust fan", "Austin", "TX")
    assert "plumbing" in facts.positive_facts


def test_explicit_electrical_work_overrides_non_electric_negation():
    facts = build_scope_facts_v2("install non-electric sign face plus automatic transfer switch and generator electrical work", "Austin", "TX")
    assert "electrical" in facts.positive_facts
    assert "no_electrical" not in facts.negative_facts


def test_like_for_like():
    facts = build_scope_facts_v2("like-for-like RTU swap on same curb", "Boise", "ID")
    assert "like_for_like_replacement" in facts.negative_facts
    assert "no_food_service_change" in facts.negative_facts
