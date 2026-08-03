#!/usr/bin/env python3
"""Seattle HPWH/water-heater customer-output contract regressions."""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.scope_contract import build_scope_contract
from api.residential_universal_gate import apply_residential_universal_gate


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


def test_residential_gate_routes_seattle_water_heater_plumbing_to_phskc_and_filters_wrong_scope_sources():
    contract = build_scope_contract(HPWH_JOB, "Seattle", "WA", job_category="residential")
    contaminated = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_kind": "Electrical",
        "permit_name": "Electrical Permit — Panel Upgrade / Service Change",
        "applying_office": "Seattle Department of Construction and Inspections",
        "apply_url": "https://cosaccela.seattle.gov/",
        "permits_required": [
            {"permit_type": "Electrical Permit — Panel Upgrade / Service Change", "required": True, "source_url": "https://www.seattle.gov/sdci/permits/permits-we-issue-(a-z)/electrical-permit"},
            {"permit_type": "Mechanical Permit — HVAC Equipment Changeout", "required": True, "source_url": "https://www.seattle.gov/sdci/permits/permits-we-issue-(a-z)/mechanical-permit"},
            {"permit_type": "Refrigeration Permit — Split-System Heat Pump / Mini-Split", "required": True, "source_url": "https://www.seattle.gov/sdci/permits/permits-we-issue-(a-z)/refrigeration-permit"},
        ],
        "sources": [
            {"url": "https://www.seattle.gov/DPD/Publications/CAM/Tip424.pdf", "title": "Tip 424 commercial and multifamily heat pump water heating"},
            {"url": "https://www.seattle.gov/sdci/permits/permits-we-issue-(a-z)/mechanical-permit", "title": "Mechanical Permit"},
        ],
        "source_urls": [
            "https://www.seattle.gov/DPD/Publications/CAM/Tip424.pdf",
            "https://www.seattle.gov/sdci/permits/permits-we-issue-(a-z)/mechanical-permit",
        ],
        "customer_next_step": "Apply for Electrical Permit — Panel Upgrade / Service Change in the Seattle Services Portal.",
    }

    out = apply_residential_universal_gate(contaminated, HPWH_JOB, "Seattle", "WA", scope_contract=contract)
    text = _customer_blob(out)

    assert out["permit_kind"] == "Plumbing"
    assert out["permit_name"] == "Residential Plumbing Permit — Water Heater Replacement"
    assert "public health" in out["applying_office"].lower()
    assert out["apply_url"] == PHSKC_URL
    assert len(out["permits_required"]) == 1
    assert out["permits_required"][0]["filing_family"] == "plumbing"
    assert "panel upgrade" not in text
    assert "service change" not in text
    assert "mechanical permit" not in text
    assert "refrigeration permit" not in text
    assert "tip424" not in text
    assert "mechanical-permit" not in text
    assert "electrical circuit" in text
    assert "conditional" in text


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

    assert public["permit_decision"] == "VERIFY"
    assert public["permit_required"] is None
    assert public["permit_kind"] == "Plumbing"
    assert public["permit_name"] == "Residential Plumbing Permit — Water Heater Replacement"
    assert public["permits_required"] == []
    assert any(
        row.get("filing_family") == "plumbing" and row.get("status") == "VERIFY"
        for row in public.get("family_decisions", [])
    )
    row = next(row for row in public["family_decisions"] if row.get("filing_family") == "plumbing")
    assert row["permit_type"] == "Residential Plumbing Permit — Water Heater Replacement"
    assert row["decision"] == "VERIFY"
    assert row["status"] == "VERIFY"
    assert public["apply_url"] == PHSKC_URL
    assert str((public.get("apply_path") or {}).get("support_level") or "").lower() in {"verification required", "verify", "verified path"}
    assert PHSKC_URL in public["source_urls"]
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


def test_valid_seattle_hpwh_lookup_returns_binary_required_through_server_pipeline():
    """A valid paid lookup may not terminate at VERIFY/NEEDS_INPUT.

    Direct untrusted ViewModel input above remains fail-closed.  This regression
    exercises the trusted server finalizer + customer-egress path used by
    ``/api/permit`` and requires the source-backed Seattle water-heater lane to
    produce the binary answer promised to customers.
    """
    raw = {
        "permit_required": None,
        "permit_decision": "VERIFY",
        "permit_verdict": "VERIFY",
        "permit_kind": "Plumbing",
        "permit_name": "Residential Plumbing Permit — Water Heater Replacement",
        "applying_office": "Public Health — Seattle & King County Plumbing and Gas Piping Program",
        "apply_url": PHSKC_URL,
        "sources": [{
            "url": PHSKC_URL,
            "title": "Public Health — Seattle & King County plumbing and gas piping permits",
        }],
        "source_urls": [PHSKC_URL],
        "family_decisions": [{
            "family": "plumbing",
            "status": "VERIFY",
            "decision": "VERIFY",
            "required": None,
            "permit_type": "Residential Plumbing Permit — Water Heater Replacement",
        }],
    }

    code = f"""
import json, os, sys
sys.path.insert(0, {os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))!r})
os.environ.setdefault('PERMITASSIST_NO_BACKGROUND_WORKERS', '1')
os.environ.setdefault('OPENAI_API_KEY', 'test-not-real-key-for-import-only')
from api.server import _mark_server_owned_result, finalize_permit_lookup_result, build_customer_response_egress
raw = {raw!r}
job = {HPWH_JOB!r}
finalized = finalize_permit_lookup_result(
    _mark_server_owned_result(raw),
    job,
    'Seattle',
    'WA',
    job_category='residential',
    evidence_allowed=False,
)
public = build_customer_response_egress(
    finalized, job, 'Seattle', 'WA', job_category='residential',
)
print(json.dumps(public, sort_keys=True, default=str))
"""
    public = _run_python_json(code)
    text = _blob(public)

    assert public["permit_decision"] == "REQUIRED"
    assert public["permit_required"] is True
    assert public["permit_verdict"] == "YES"
    assert public["permit_kind"] == "Plumbing"
    assert "water heater replacement" in str(public.get("permit_name") or "").lower()
    assert any(
        row.get("filing_family") == "plumbing"
        and (row.get("status") == "REQUIRED" or row.get("required") is True)
        for row in public.get("family_decisions", [])
    )
    assert public["apply_url"] == PHSKC_URL
    assert PHSKC_URL in public["source_urls"]
    assert "electrical circuit" in text
    assert "conditional" in text
    assert "panel upgrade" not in text
    assert "mechanical permit" not in text
    assert "refrigeration permit" not in text


def test_seattle_hpwh_binary_continuity_requires_retained_exact_ahj_rule():
    """Server ownership alone cannot promote VERIFY without retained authority."""
    raw = {
        "permit_required": None,
        "permit_decision": "VERIFY",
        "permit_verdict": "VERIFY",
        "permit_kind": "Plumbing",
        "permit_name": "Residential Plumbing Permit — Water Heater Replacement",
        "applying_office": "Public Health — Seattle & King County Plumbing and Gas Piping Program",
        "apply_url": PHSKC_URL,
        "sources": [{"url": PHSKC_URL, "title": "Official plumbing permit page"}],
        "source_urls": [PHSKC_URL],
    }

    code = f"""
import json, os, sys
sys.path.insert(0, {os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))!r})
os.environ.setdefault('PERMITASSIST_NO_BACKGROUND_WORKERS', '1')
os.environ.setdefault('OPENAI_API_KEY', 'test-not-real-key-for-import-only')
from api import ahj_rule_packs
ahj_rule_packs.AHJ_RULES = ()
sys.modules['ahj_rule_packs'] = ahj_rule_packs
from api.server import _mark_server_owned_result, finalize_permit_lookup_result, build_customer_response_egress
raw = {raw!r}
job = {HPWH_JOB!r}
finalized = finalize_permit_lookup_result(
    _mark_server_owned_result(raw), job, 'Seattle', 'WA',
    job_category='residential', evidence_allowed=False,
)
public = build_customer_response_egress(
    finalized, job, 'Seattle', 'WA', job_category='residential',
)
print(json.dumps(public, sort_keys=True, default=str))
"""
    public = _run_python_json(code)

    assert public["permit_decision"] in {"VERIFY", "NEEDS_INPUT"}
    assert public["permit_required"] is None
    assert public["permit_verdict"] in {"VERIFY", "NEEDS_INPUT"}
    manifest = public.get("permit_manifest")
    if isinstance(manifest, dict):
        assert manifest.get("permit_decision") in {"VERIFY", "NEEDS_INPUT"}
        assert "authority_tag" not in manifest


def test_seattle_hpwh_negative_mentions_cannot_trigger_binary_promotion():
    """Incidental or negated water-heater mentions stay nonbinary."""
    jobs = [
        "replace a sink; water heater remains unchanged",
        "replace plumbing fixtures adjacent to the existing water heater",
        "inspect the water heater before replacing a kitchen faucet",
        "no water heater replacement; replace the bathroom faucet only",
        "replace water heater supply line only",
        "replace water heater-supply line only",
        "replace the water heater's supply line only",
        "replace water heater insulation blanket only",
        "replace water heater expansion tank only",
        "replace water heater anode rod only",
        "water heater replacement by others; our scope is faucet only",
        "water heater replacement is excluded from our scope",
        "water heater replacement not included; replace faucet only",
        "future water heater replacement; current scope is faucet only",
        "quoted alternate water heater replacement; base scope is faucet only",
        "replace water heater in the future; today replace faucet only",
        "option to replace water heater; base scope is faucet only",
        "quote to replace water heater; authorized scope is faucet only",
        "estimate to replace water heater; no work authorized",
        "Owner declined to replace water heater; repair faucet only",
        "Replace water heater: valve only",
        "Replace water heater, valve only",
        "Replace faucet (water heater replacement by owner)",
        "Water heater replacement completed by others; our scope is faucet only",
        "Water heater replacement performed by others; our scope is faucet only",
        "Water heater replacement to be completed by others; our scope is faucet only",
        "Water heater replacement under a separate contract; our scope is faucet only",
        "Future owner will replace water heater; current scope is faucet only",
        "Phase 2 will replace water heater; current scope is faucet only",
        "Water heater replacement is a future phase; current scope is faucet only",
        "Allowance only to replace water heater; no work is authorized",
    ]
    code = f"""
import json, os, sys
sys.path.insert(0, {os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))!r})
os.environ.setdefault('PERMITASSIST_NO_BACKGROUND_WORKERS', '1')
os.environ.setdefault('OPENAI_API_KEY', 'test-not-real-key-for-import-only')
from api.server import _mark_server_owned_result, finalize_permit_lookup_result, build_customer_response_egress
jobs = {jobs!r}
raw = {{
    'permit_required': None,
    'permit_decision': 'VERIFY',
    'permit_verdict': 'VERIFY',
    'permit_kind': 'Plumbing',
    'permit_name': 'Verify plumbing scope',
}}
results = []
for job in jobs:
    finalized = finalize_permit_lookup_result(
        _mark_server_owned_result(raw), job, 'Seattle', 'WA',
        job_category='residential', evidence_allowed=False,
    )
    public = build_customer_response_egress(
        finalized, job, 'Seattle', 'WA', job_category='residential',
    )
    results.append({{
        'job': job,
        'permit_decision': public.get('permit_decision'),
        'permit_required': public.get('permit_required'),
        'permit_verdict': public.get('permit_verdict'),
    }})
print(json.dumps(results, sort_keys=True))
"""
    results = _run_python_json(code)

    assert len(results) == len(jobs)
    for result in results:
        assert result["permit_decision"] in {"VERIFY", "NEEDS_INPUT"}, result
        assert result["permit_required"] is None, result
        assert result["permit_verdict"] in {"VERIFY", "NEEDS_INPUT"}, result


def test_public_family_compatibility_prefers_explicit_plumbing_kind():
    """HPWH descriptive text must not override an explicit typed Plumbing kind."""
    code = f"""
import json, os, sys
sys.path.insert(0, {os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))!r})
os.environ.setdefault('PERMITASSIST_NO_BACKGROUND_WORKERS', '1')
os.environ.setdefault('OPENAI_API_KEY', 'test-not-real-key-for-import-only')
from api.server import project_customer_response_egress
with_kind = project_customer_response_egress({{
    'permit_decision': 'REQUIRED',
    'permits_required': [{{
        'permit_kind': 'Plumbing',
        'permit_type': 'Heat Pump Water Heater Replacement Permit',
        'required': True,
    }}],
}})
without_kind = project_customer_response_egress({{
    'permit_decision': 'REQUIRED',
    'permits_required': [{{
        'permit_type': 'Heat Pump Water Heater Replacement Permit',
        'required': True,
    }}],
}})
print(json.dumps({{'with_kind': with_kind, 'without_kind': without_kind}}, sort_keys=True))
"""
    payload = _run_python_json(code)

    assert payload["with_kind"]["permits_required"][0]["filing_family"] == "plumbing"
    assert "filing_family" not in payload["without_kind"]["permits_required"][0]


def test_seattle_hpwh_evidence_pack_fail_closed_fields_cannot_be_resurrected():
    """The deterministic HPWH lane must not bypass evidence-pack controls."""
    raw = {
        "permit_required": None,
        "permit_decision": "VERIFY",
        "permit_verdict": "VERIFY",
        "permit_kind": "Plumbing",
        "permit_name": "Residential Plumbing Permit — Water Heater Replacement",
        "applying_office": "Public Health — Seattle & King County Plumbing and Gas Piping Program",
        "apply_url": PHSKC_URL,
        "sources": [{
            "url": PHSKC_URL,
            "title": "Public Health — Seattle & King County plumbing and gas piping permits",
        }],
        "source_urls": [PHSKC_URL],
    }

    code = f"""
import json, os, sys
sys.path.insert(0, {os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))!r})
os.environ.setdefault('PERMITASSIST_NO_BACKGROUND_WORKERS', '1')
os.environ.setdefault('OPENAI_API_KEY', 'test-not-real-key-for-import-only')
from api.server import _issue_evidence_pack_authorized_result, finalize_permit_lookup_result, build_customer_response_egress
raw = {raw!r}
job = {HPWH_JOB!r}
finalized = finalize_permit_lookup_result(
    raw,
    job,
    'Seattle',
    'WA',
    job_category='residential',
    evidence_allowed=False,
)
controlled = dict(finalized)
controlled['_evidence_pack'] = {{
    'enabled': True,
    'matched_fields': [],
    'failed_closed_fields': ['permit_type', 'apply_url'],
}}
authorized = _issue_evidence_pack_authorized_result(controlled)
public = build_customer_response_egress(
    authorized, job, 'Seattle', 'WA', job_category='residential',
)
print(json.dumps(public, sort_keys=True, default=str))
"""
    public = _run_python_json(code)

    assert public["permit_decision"] in {"VERIFY", "NEEDS_INPUT"}
    assert public["permit_required"] is None
    assert public["permit_verdict"] in {"VERIFY", "NEEDS_INPUT"}
    assert public.get("apply_url") in {None, ""}
    assert public.get("online_application_url") in {None, ""}
