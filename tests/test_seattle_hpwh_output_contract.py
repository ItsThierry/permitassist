#!/usr/bin/env python3
"""Seattle HPWH/water-heater customer-output contract regressions."""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.scope_contract import build_scope_contract


PHSKC_URL = "https://kingcounty.gov/en/dept/dph/health-safety/environmental-health/plumbing-gas-piping/applications-and-permits"


HPWH_JOB = (
    "Existing single-family home in Seattle WA: replace gas storage water heater "
    "with heat pump water heater in garage, contractor install, existing 200A panel available."
)


def _blob(value) -> str:
    return json.dumps(value, sort_keys=True, default=str).lower()


def _run_python_json(code: str):
    proc = subprocess.run([sys.executable, "-c", code], check=True, text=True, capture_output=True)
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _classify_scope_in_subprocess(job: str, city: str = "Seattle", state: str = "WA", job_category: str = "residential"):
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    code = f"""
import json, os, sys
sys.path.insert(0, {repo_root!r})
os.environ.setdefault('PERMITASSIST_NO_BACKGROUND_WORKERS', '1')
import api.research_engine as engine
from api.scope_contract import build_scope_contract
job = {job!r}
contract = build_scope_contract(job, {city!r}, {state!r}, job_category={job_category!r})
classified = engine.classify_scope_required_permits(job, contract)
print(json.dumps({{'contract': contract, 'classified': classified}}, sort_keys=True, default=str))
"""
    return _run_python_json(code)


def _customer_blob(value) -> str:
    if isinstance(value, dict):
        value = {k: v for k, v in value.items() if not str(k).startswith("_")}
    return _blob(value)


def test_scope_contract_classifies_heat_pump_water_heater_as_water_heater_not_hvac_or_panel():
    contract = build_scope_contract(HPWH_JOB, "Seattle", "WA", job_category="residential")

    assert contract["category"] == "residential"
    assert contract["vertical"] == "water_heater"
    assert contract["family"] == "residential_single_trade"


def test_scope_classifier_hpwh_is_plumbing_only_with_existing_200a_context():
    contract = build_scope_contract(HPWH_JOB, "Seattle", "WA", job_category="residential")
    result = _classify_scope_in_subprocess(HPWH_JOB)
    classified = result["classified"]
    assert classified is not None
    text = _blob(classified)

    assert "residential plumbing permit" in text
    assert "water heater replacement" in text
    assert "mechanical permit" not in text
    assert "refrigeration permit" not in text
    assert "panel / service upgrade" not in text
    assert "panel upgrade" not in text


def test_scope_classifier_combined_real_hvac_and_water_heater_keeps_both_trades():
    job = "Seattle single-family home: install ductless mini-split heat pump with exterior condenser and line set, and replace water heater."
    result = _classify_scope_in_subprocess(job)
    contract = result["contract"]
    classified = result["classified"]
    text = _blob(classified)

    assert contract["vertical"] == "hvac_changeout"
    assert "mechanical permit" in text
    assert "refrigeration permit" in text
    assert "electrical permit" in text
    assert "plumbing permit" in text
    assert "water heater replacement" in text


def test_customer_view_model_final_gate_makes_contaminated_hpwh_output_a_grade_safe():
    contaminated = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_kind": "Electrical",
        "permit_name": "Electrical Permit — Panel Upgrade / Service Change",
        "applying_office": "Seattle Department of Construction and Inspections",
        "apply_url": "https://cosaccela.seattle.gov/",
        "permits_required": [
            {"permit_type": "Electrical Permit — Panel Upgrade / Service Change", "required": True},
            {"permit_type": "Mechanical Permit — HVAC Equipment Changeout", "required": True},
            {"permit_type": "Refrigeration Permit — Split-System Heat Pump / Mini-Split", "required": True},
        ],
        "sources": [
            {"url": "https://www.seattle.gov/DPD/Publications/CAM/Tip424.pdf", "title": "Tip 424 commercial and multifamily heat pump water heating"},
            {"url": "https://www.seattle.gov/sdci/permits/permits-we-issue-(a-z)/refrigeration-permit", "title": "Refrigeration Permit"},
        ],
    }

    code = f"""
import json, os, sys
sys.path.insert(0, {os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))!r})
os.environ.setdefault('PERMITASSIST_NO_BACKGROUND_WORKERS', '1')
os.environ.setdefault('OPENAI_API_KEY', 'test-not-real-key-for-import-only')
from api.server import build_customer_permit_view_model
contaminated = {contaminated!r}
job = {HPWH_JOB!r}
public = build_customer_permit_view_model(contaminated, job, 'Seattle', 'WA', job_category='residential')
print(json.dumps(public, sort_keys=True, default=str))
"""
    public = _run_python_json(code)
    text = _blob(public)

    assert public["permit_decision"] == "REQUIRED"
    assert public["permit_required"] is True
    assert public["permit_kind"] == "Plumbing"
    assert public["permit_name"] == "Residential Plumbing Permit — Water Heater Replacement"
    assert public["permits_required"] == [
        {
            "permit_type": "Residential Plumbing Permit — Water Heater Replacement",
            "filing_family": "plumbing",
            "required": True,
            "decision": "REQUIRED",
            "status": "REQUIRED",
            "scope_trigger": "water_heater_replacement",
            "ahj_name": "Public Health — Seattle & King County Plumbing and Gas Piping Program",
            "source_url": PHSKC_URL,
        }
    ]
    assert public["apply_url"] == PHSKC_URL
    assert public["source_urls"] == [
        PHSKC_URL,
        "https://www.seattle.gov/sdci/permits/permits-we-issue-(a-z)/electrical-permit",
    ]
    assert "public health" in text
    assert "kingcounty.gov" in text
    assert "electrical circuit" in text
    assert "conditional" in text
    assert "panel upgrade" not in text
    assert "service change" not in text
    assert "mechanical permit" not in text
    assert "refrigeration permit" not in text
    assert "tip424" not in text
    assert "commercial and multifamily" not in text
