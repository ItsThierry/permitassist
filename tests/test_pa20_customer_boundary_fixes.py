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
from server import build_customer_permit_view_model  # noqa: E402

PA20_EVIDENCE = ROOT / "artifacts" / "pa20_customer_lookup_20260627" / "evidence.jsonl"


def _pa20_public(case_id: str) -> dict:
    if not PA20_EVIDENCE.exists():
        pytest.skip(f"PA20 frozen lookup artifact is not present: {PA20_EVIDENCE}")
    for line in PA20_EVIDENCE.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record["case"]["id"] == case_id:
            case = record["case"]
            return build_customer_permit_view_model(
                copy.deepcopy(record["response_body"]),
                case["job_type"],
                case["city"],
                case["state"],
                job_category=case["segment"],
            )
    raise AssertionError(f"missing PA20 case {case_id}")


def _view(raw: dict, job: str, city: str = "Dallas", state: str = "TX", category: str = "commercial") -> dict:
    return build_customer_permit_view_model(copy.deepcopy(raw), job, city, state, job_category=category)


def _required_raw(rows=None, **extra):
    raw = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_kind": "Building",
        "permit_name": "Building Permit",
        "applying_office": "Dallas Development Services Department",
        "sources": [{"url": "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/", "title": "Dallas Building Inspection"}],
        "source_urls": ["https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/"],
        "permits_required": rows or [
            {"permit_type": "Building Permit", "required": True},
            {"permit_type": "Electrical Permit", "required": True},
            {"permit_type": "Plumbing Permit", "required": True},
        ],
    }
    raw.update(extra)
    return raw


def test_commercial_cosmetic_finish_only_converts_to_not_required_without_neutering_triggered_work():
    contaminated = _required_raw()

    cosmetic = _view(
        contaminated,
        "commercial office repaint and carpet only; no walls, no MEP, no structural, no occupancy change",
        category="commercial",
    )

    assert cosmetic["permit_decision"] == "NOT_REQUIRED"
    assert cosmetic["permit_required"] is False
    assert cosmetic.get("permits_required") == []
    assert "cosmetic finish work" in cosmetic["customer_headline"].lower()

    triggered = _view(
        contaminated,
        "commercial office repaint and carpet plus relocated lighting and electrical receptacles",
        category="commercial",
    )

    assert triggered["permit_decision"] == "REQUIRED"
    assert triggered.get("permits_required")
    assert "electrical" in json.dumps(triggered).lower()

    structural_ceiling = _view(
        contaminated,
        "commercial office ceiling finish only with rated ceiling assembly work; no walls and no MEP",
        category="commercial",
    )
    assert structural_ceiling["permit_decision"] == "REQUIRED"
    assert structural_ceiling.get("permits_required")


def test_residential_address_dependent_companions_are_related_not_required_headline_items():
    public = _pa20_public("PA20-014")

    required_text = json.dumps(public.get("permits_required") or []).lower()
    related_text = json.dumps(public.get("related_permits") or []).lower()

    assert public["permit_decision"] == "REQUIRED"
    assert "roofing" in required_text
    assert "zoning" not in required_text
    assert "historic" not in required_text
    assert "zoning" in related_text and "conditional" in related_text
    assert "historic" in related_text and "conditional" in related_text
    assert "zoning" not in public["customer_headline"].lower()
    assert "historic" not in public["customer_headline"].lower()


def test_specialty_rows_are_scope_gated_and_public_row_text_is_clean():
    public = _pa20_public("PA20-003")
    text = json.dumps(public).lower()
    row_types = [row.get("permit_type") for row in public.get("permits_required") or []]

    assert not any("Liquor" in str(row) for row in row_types)
    assert any("Health Plan Review" in str(row) for row in row_types)
    assert "copied here by mistake" not in text
    assert "metadata" not in text
    assert "keep this row visible" not in text
    assert "universal_filing_packet" not in text


def test_fee_renderer_suppresses_malformed_fee_strings_and_ev_label_is_precise():
    fee_public = _view(
        _required_raw(
            rows=[{"permit_type": "Mechanical Permit — HVAC Equipment Changeout (Residential)", "filing_family": "mechanical", "required": True}],
            fee_range="Fee Estimate:; verify with AHJ. commercial floor).5× local fee schedule $5,500-$5,500+",
        ),
        "residential AC condenser changeout",
        city="Phoenix",
        state="AZ",
        category="residential",
    )
    assert fee_public["fee_range"] == "Fee estimate not confirmed; verify the current AHJ fee schedule before quoting."

    ev_public = _view(
        _required_raw(
            rows=[{"permit_type": "Electrical Permit — Service / Panel Upgrade", "filing_family": "electrical", "required": True}],
            applying_office="Minnesota Department of Labor and Industry",
        ),
        "single-family Level 2 EV charger on existing panel, new 60A branch circuit",
        city="Minneapolis",
        state="MN",
        category="residential",
    )
    assert ev_public["permit_name"] == "Electrical Permit — EV Charger / New Branch Circuit"
    assert "panel upgrade" not in json.dumps(ev_public).lower()


def test_small_detached_shed_unbound_negative_fails_closed_with_zoning_verify_only():
    public = _pa20_public("PA20-005")
    assert public["permit_decision"] == "UNKNOWN"
    assert public.get("permit_required") is None
    assert public.get("permit_verdict") == "VERIFY"
    assert all((row.get("status") or row.get("decision")) == "VERIFY" for row in public.get("permits_required") or [])
    assert "zoning" not in public["customer_headline"].lower()
    assert "setback" in json.dumps(public.get("related_permits") or []).lower()


def test_unbound_negative_drywall_fails_closed_without_negated_trade_or_structural_templates():
    public = _pa20_public("PA20-016")
    text = json.dumps(public).lower()
    assert public["permit_decision"] == "UNKNOWN"
    assert public.get("permit_required") is None
    assert public.get("permit_verdict") == "VERIFY"
    assert all((row.get("status") or row.get("decision")) == "VERIFY" for row in public.get("permits_required") or [])
    assert "no permit required" not in text
    assert "structural" not in text
    assert "electrical permit" not in text
    assert "plumbing permit" not in text


def test_timeout_fallback_returns_customer_safe_structured_payload_not_502_shape(monkeypatch):
    monkeypatch.setattr(server, "PERMIT_LOOKUP_TOTAL_TIMEOUT_SECONDS", 30)
    fallback = server._build_degraded_lookup_fallback(
        "NYC bathroom remodel with plumbing and electrical",
        "New York",
        "NY",
        reason="lookup_timeout",
    )

    assert fallback["permit_decision"] == "REQUIRED"
    assert fallback["permit_verdict"] == "YES"
    assert fallback["customer_next_step"]
    assert fallback["warnings"]
    assert fallback.get("apply_url") == ""
    assert fallback.get("online_application_url") == ""
