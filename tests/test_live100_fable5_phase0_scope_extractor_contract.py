from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from scope_contract import build_scope_facts_v4  # noqa: E402

ARTIFACT_ROOT = ROOT / "artifacts" / "customer_pov_100_50_50_b1a2bb1_20260708T012930Z"
LABELS_PATH = ARTIFACT_ROOT / "phase0_core_truth_remediation" / "PHASE0_SCOPE_FACT_LABELS.json"

CRITICAL_POSITIVE_AXES = {
    "electrical",
    "plumbing",
    "mechanical",
    "gas",
    "fire_life_safety",
    "health_food_pool",
    "change_of_use_ti",
    "signage",
    "structural",
    "elevator",
    "refrigeration",
    "environmental_fuel",
}
CRITICAL_NEGATIVE_AXES = {
    "no_electrical",
    "no_plumbing",
    "no_mechanical",
    "no_mep",
    "no_structural",
    "no_signage",
    "no_food_service_change",
    "no_use_change",
    "cosmetic_only",
}


def _normalize_extractor_facts(facts):
    d = facts.as_dict()
    pos = set(d.get("request_positive_families") or []) | set(d.get("positive_facts") or [])
    neg = set(d.get("request_negative_families") or []) | set(d.get("negative_facts") or []) | set(d.get("negative_scope_facts") or [])
    mapping = {
        "building": "structural",
        "building_ti": "change_of_use_ti",
        "electrical": "electrical",
        "plumbing": "plumbing",
        "mechanical": "mechanical",
        "fire_suppression": "fire_life_safety",
        "fire_alarm": "fire_life_safety",
        "health_food": "health_food_pool",
        "sign": "signage",
        "planning_zoning": "zoning",
        "co_change_of_occupancy": "change_of_use_ti",
        "refrigeration": "refrigeration",
        "gas": "gas",
        "environmental": "environmental_fuel",
        "pool": "health_food_pool",
    }
    pos_axes = {mapping.get(x, x) for x in pos}
    neg_axes = set(neg)
    if d.get("electrical_work", {}).get("value") == "FALSE":
        neg_axes.add("no_electrical")
    if d.get("plumbing_work", {}).get("value") == "FALSE":
        neg_axes.add("no_plumbing")
    if d.get("mechanical_work", {}).get("value") == "FALSE":
        neg_axes.add("no_mechanical")
    if d.get("structural_work", {}).get("value") == "FALSE":
        neg_axes.add("no_structural")
    if d.get("food_establishment", {}).get("value") == "FALSE":
        neg_axes.add("no_food_service_change")
    return str(d.get("segment") or ""), pos_axes, neg_axes


def test_live100_fable5_phase0_scope_extractor_contract_reaches_98_of_100():
    if not LABELS_PATH.exists():
        pytest.skip(f"Phase 0 labels artifact not present: {LABELS_PATH}")
    labels = json.loads(LABELS_PATH.read_text())
    failures = []
    for label in labels:
        facts = build_scope_facts_v4(label["job_type"], label["city"], label["state"], job_category=label["segment"])
        segment, pos_axes, neg_axes = _normalize_extractor_facts(facts)
        exp_pos = set(label["positive_scope_facts"]) & CRITICAL_POSITIVE_AXES
        exp_neg = set(label["negative_scope_facts"]) & CRITICAL_NEGATIVE_AXES
        missing_pos = sorted(exp_pos - pos_axes)
        missing_neg = sorted(exp_neg - neg_axes)
        if segment != label["segment"] or missing_pos or missing_neg:
            failures.append({
                "case_id": label["case_id"],
                "segment": segment,
                "expected_segment": label["segment"],
                "missing_positive_axes": missing_pos,
                "missing_negative_axes": missing_neg,
            })
    assert len(labels) - len(failures) >= 98, failures[:20]
