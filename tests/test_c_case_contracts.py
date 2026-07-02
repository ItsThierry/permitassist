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
from server import build_customer_permit_view_model, render_share_page

FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "live100_full_customer"
C_CASE_IDS = [
    "C-004", "C-010", "C-013", "C-018", "C-022", "C-023", "C-033", "C-036", "C-037", "C-042", "C-045", "C-046",
    "R-002", "R-004", "R-011", "R-012", "R-013", "R-018", "R-020", "R-024", "R-032", "R-033", "R-036", "R-038", "R-043", "R-044", "R-049",
]


def load_case(case_id: str):
    raw = json.loads((FIXTURE_ROOT / case_id / "raw_lookup.json").read_text())
    contract = json.loads((FIXTURE_ROOT / case_id / "expected_contract.json").read_text())
    case = raw["case"]
    result = raw["response_body"]
    old_gate = os.environ.get("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD")
    os.environ["PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD"] = "1"
    try:
        public = build_customer_permit_view_model(result, case["job_type"], case["city"], case["state"], job_category=case.get("segment"))
    finally:
        if old_gate is None:
            os.environ.pop("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD", None)
        else:
            os.environ["PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD"] = old_gate
    return case, public, contract


def families(public: dict) -> set[str]:
    return {family_from_row(r) for r in public.get("permits_required") or [] if isinstance(r, dict) and r.get("required") is not False and str(r.get("decision") or "REQUIRED").upper() != "CONDITIONAL"}


def conditional_families(public: dict) -> set[str]:
    return {family_from_row(r) for r in public.get("conditional_permits") or [] if isinstance(r, dict)}


def blob(public: dict) -> str:
    return json.dumps(public, sort_keys=True, default=str).lower()


def family_ok(actual: set[str], expected: str) -> bool:
    if expected in actual:
        return True
    aliases = {
        "building_ti": {"building_ti", "building"},
        "building_adu": {"building_adu", "building"},
        "demolition": {"demolition", "building"},
        "racking": {"racking", "building"},
        "fire_alarm": {"fire_alarm", "fire_suppression", "electrical"},
        "historic_review": {"historic_review", "historic"},
        "gas": {"gas", "plumbing"},
    }
    return bool(actual & aliases.get(expected, {expected}))


@pytest.mark.parametrize("case_id", C_CASE_IDS)
def test_c_case_contracts(case_id: str):
    case, public, contract = load_case(case_id)
    fams = families(public)
    conds = conditional_families(public)
    expected_decision = contract.get("expected_decision", "REQUIRED")
    assert public.get("permit_decision") == expected_decision, case_id
    for expected in contract.get("must_keep_required", []):
        assert family_ok(fams, expected), (case_id, expected, fams, public.get("permits_required"))
    for forbidden in contract.get("must_not_required", []):
        assert not family_ok(fams, forbidden), (case_id, forbidden, fams, public.get("permits_required"))
    for expected_cond in contract.get("must_demote", []):
        assert family_ok(conds, expected_cond), (case_id, expected_cond, conds, public.get("conditional_permits"))
    text = blob(public)
    if contract.get("apply_url_must_contain"):
        assert contract["apply_url_must_contain"].lower() in str(public.get("apply_url") or "").lower()
    if contract.get("source_must_contain"):
        assert contract["source_must_contain"].lower() in text
    if contract.get("source_must_not_contain"):
        assert contract["source_must_not_contain"].lower() not in text
    if contract.get("fee_must_not_contain"):
        assert contract["fee_must_not_contain"].lower() not in str(public.get("fee_range") or "").lower()
    if contract.get("summary_must_contain"):
        assert contract["summary_must_contain"].lower() in text
    for forbidden_text in contract.get("forbidden_visible", []):
        assert forbidden_text.lower() not in text, (case_id, forbidden_text)
    assert "verify in before quoting" not in text


@pytest.mark.parametrize("case_id", C_CASE_IDS)
def test_c_case_report_html_uses_canonical_packet(case_id: str):
    case, public, contract = load_case(case_id)
    html = render_share_page({"data": public, "job_type": case["job_type"], "city": case["city"], "state": case["state"]})
    html_lc = html.lower()
    assert "public_packet_rows" in html
    assert "Pull No permit required" not in html
    assert "verify in before quoting" not in html
    for forbidden_text in contract.get("forbidden_visible", []):
        assert forbidden_text.lower() not in html_lc, (case_id, forbidden_text)
