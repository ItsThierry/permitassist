from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from family_reconciliation_gate import family_from_row
from no_neuter_scorer import score_packet
from server import build_customer_permit_view_model

FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "live100_full_customer"
A_CASE_IDS = [p.name for p in FIXTURE_ROOT.iterdir() if (p / "expected_contract.json").exists() and json.loads((p / "expected_contract.json").read_text()).get("protection")]


def _family_tokens(rows):
    out = set()
    for row in rows or []:
        if isinstance(row, dict):
            out.add(family_from_row(row))
            text = " ".join(str(row.get(k) or "") for k in ("permit_type", "permit_name", "kind")).lower()
            out.add(text)
    return out


@pytest.mark.parametrize("case_id", A_CASE_IDS)
def test_no_neuter_guard(case_id: str):
    raw = json.loads((FIXTURE_ROOT / case_id / "raw_lookup.json").read_text())
    contract = json.loads((FIXTURE_ROOT / case_id / "expected_contract.json").read_text())
    case = raw["case"]
    before = raw["response_body"]
    old_gate = os.environ.get("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD")
    os.environ["PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD"] = "1"
    try:
        after = build_customer_permit_view_model(before, case["job_type"], case["city"], case["state"], job_category=case.get("segment"))
    finally:
        if old_gate is None:
            os.environ.pop("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD", None)
        else:
            os.environ["PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD"] = old_gate
    after_tokens = _family_tokens(after.get("permits_required") or [])
    for required_text in contract.get("must_keep_required", []):
        assert any(required_text.lower() in token or family_from_row({"permit_type": required_text}) in after_tokens for token in after_tokens), (case_id, required_text, after.get("permits_required"))
    before_score = score_packet(before)
    after_score = score_packet(after)
    assert after_score["not_required"] >= before_score["not_required"]
    assert after_score["sources"] >= min(before_score["sources"], 1)
    assert after_score["fee_amounts"] >= min(before_score["fee_amounts"], contract.get("min_fee_amounts", 0))
    assert after_score["hedge_decision_phrases"] == 0
