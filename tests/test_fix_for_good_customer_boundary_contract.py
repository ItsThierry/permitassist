import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import server  # noqa: E402
from permit_decision import _get_decision_cell_primary_lock  # noqa: E402

R30_CASES = ROOT / "artifacts" / "residential_customer_30_20260628T033535Z" / "cases.jsonl"
R30_SUMMARY = ROOT / "artifacts" / "residential_customer_30_20260628T033535Z" / "summary.json"
BAD_CONFIDENCE_COPY = "Use the resolved permit decision and current local filing category before filing"


def _r30_records():
    if not R30_CASES.exists():
        pytest.skip(f"Residential 30 frozen artifact not present: {R30_CASES}")
    return [json.loads(line) for line in R30_CASES.read_text().splitlines() if line.strip()]


def test_phase1_frozen_residential30_reproduces_current_customer_boundary_failures():
    if not R30_SUMMARY.exists():
        pytest.skip(f"Residential 30 summary not present: {R30_SUMMARY}")
    summary = json.loads(R30_SUMMARY.read_text())
    phrase_hit_cases = [rec["case"]["id"] for rec in _r30_records() if BAD_CONFIDENCE_COPY in json.dumps(rec, ensure_ascii=False)]

    assert summary["completed"] == 30
    assert summary["statuses"] == {"FAIL": 18, "PASS": 5, "PASS_WITH_NOTES": 7}
    assert summary["issue_counts"]["template_or_bad_copy"] == 15
    assert len(phrase_hit_cases) == 15


def test_final_customer_egress_scrubs_confidence_reason_template_from_frozen_r30_cases():
    phrase_records = [rec for rec in _r30_records() if BAD_CONFIDENCE_COPY in json.dumps(rec, ensure_ascii=False)]
    assert len(phrase_records) == 15

    failures = []
    for rec in phrase_records:
        case = rec["case"]
        public = server.build_customer_permit_view_model(
            copy.deepcopy(rec["response_body"]),
            case["job_type"],
            case["city"],
            case["state"],
            job_category="residential",
        )
        blob = json.dumps(public, sort_keys=True).lower()
        lint_codes = [hit["code"] for hit in server.lint_customer_visible_result(public, case["city"], case["state"])]
        if "use the resolved permit decision" in blob or "current local filing category" in blob or "internal_process_copy" in lint_codes:
            failures.append({"case_id": case["id"], "confidence_reason": public.get("confidence_reason"), "lint_codes": lint_codes})

    assert failures == []


def test_linter_flags_internal_process_copy_at_customer_boundary():
    public = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_kind": "Electrical",
        "permit_name": "Electrical Permit",
        "customer_next_step": "File the electrical permit with the listed permit office.",
        "customer_result_summary": {
            "permit_decision": "REQUIRED",
            "permit_kind": "Electrical",
            "next_step": "File the electrical permit with the listed permit office.",
            "ahj_department": "City Building Department",
            "source_cue": "Official source path found",
        },
        "confidence_reason": "Live web research found partial local guidance. Use the resolved permit decision and current local filing category before filing.",
        "sources": [{"url": "https://example.gov/permits", "title": "Permits"}],
    }

    codes = {hit["code"] for hit in server.lint_customer_visible_result(public, "Boulder", "CO")}
    assert "internal_process_copy" in codes


def test_plain_synthetic_v24_cell_lock_cannot_mint_binary_customer_authority():
    raw = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_kind": "Electrical",
        "permit_name": "Electrical Permit",
        "permits_required": [{"permit_type": "Electrical Permit", "required": True}],
        "applying_office": "Live Layer Office",
        "apply_url": "https://live.example.gov/electrical",
        "sources": [{"url": "https://live.example.gov/electrical", "title": "Live"}],
        "_decision_cell_primary_lock": {
            "source": "permitassist_v24_decision_cell",
            "exact_match": True,
            "cell_id": "v24-test-cell",
            "permit_decision": "REQUIRED",
            "permit_required": True,
            "permit_name": "Cell-Owned Building Permit",
            "permit_kind": "building",
            "apply_url": "https://cell.example.gov/building",
            "applying_office": "Cell-Owned Building Department",
            "sources": [{"url": "https://cell.example.gov/building", "title": "Cell source"}],
            "source_urls": ["https://cell.example.gov/building"],
        },
    }

    assert _get_decision_cell_primary_lock(raw)
    public = server.build_customer_permit_view_model(
        copy.deepcopy(raw),
        "residential window replacement requiring building permit",
        "Testville",
        "CO",
        job_category="residential",
    )

    assert public["permit_decision"] == "VERIFY"
    assert public["permit_required"] is None
    assert public["permit_verdict"] == "VERIFY"
    assert public.get("permits_required") == []
    assert "_decision_cell_primary_lock" not in public


def test_residential_address_dependent_companions_demote_to_verify_not_deleted():
    raw = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_kind": "Electrical",
        "permit_name": "Electrical Permit — EV Charger Installation",
        "applying_office": "City Electrical Permits",
        "apply_url": "https://example.gov/electrical",
        "sources": [{"url": "https://example.gov/electrical", "title": "Electrical permits"}],
        "source_urls": ["https://example.gov/electrical"],
        "permits_required": [
            {"permit_type": "Electrical Permit — EV Charger Installation", "required": True, "filing_family": "electrical"},
            {"permit_type": "Fire Marshal Review", "required": True, "filing_family": "fire"},
            {"permit_type": "Planning / Zoning Review", "required": True, "filing_family": "planning"},
        ],
    }

    public = server.build_customer_permit_view_model(
        copy.deepcopy(raw),
        "install a level 2 EV charger in an attached garage with a new 60 amp circuit, single family home",
        "Boulder",
        "CO",
        job_category="residential",
    )
    required_text = json.dumps(public.get("permits_required") or []).lower()
    related_text = json.dumps(public.get("related_permits") or []).lower()

    assert public["permit_decision"] == "VERIFY"
    assert public["permit_required"] is None
    assert not required_text or required_text == "[]"
    assert "electrical" in related_text and "verify" in related_text
    assert "fire marshal" in related_text and "verify" in related_text
    assert "planning" in related_text and "verify" in related_text
