import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
for path in (ROOT, API):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from api.v24_decision_cells import (  # noqa: E402
    assemble_customer_view,
    audit_v231_index_for_v24,
    convert_v231_cell_to_v24_spine,
    validate_field_registry,
    validate_v24_cell,
)


def _write_snapshot(tmp_path: Path, text: str) -> tuple[str, str]:
    name = hashlib.sha256(text.encode()).hexdigest()[:12]
    p = tmp_path / f"snapshot_{name}.txt"
    p.write_text(text)
    return str(p), hashlib.sha256(p.read_bytes()).hexdigest()


def _prov(tmp_path: Path, quote: str = "Building permits are required for alterations.") -> dict:
    snapshot_path, snapshot_hash = _write_snapshot(tmp_path, f"Official page. {quote} Apply online.")
    return {
        "source_url": "https://city.example.gov/building",
        "source_quote": quote,
        "retrieved_at": "2026-06-13T00:00:00Z",
        "snapshot_hash": snapshot_hash,
        "snapshot_path": snapshot_path,
        "effective_date": None,
        "freshness_class": "current_live_verified",
        "last_verified_at": "2026-06-13T00:00:00Z",
        "publishable": True,
    }


def _valid_cell(tmp_path: Path) -> dict:
    prov = _prov(tmp_path)
    negative_prov = _prov(tmp_path, "PLI does not regulate plumbing permits.")
    return {
        "schema_version": "v2.4",
        "cell_id": "us-pa-pittsburgh__commercial__commercial_tenant_improvement__building",
        "jurisdiction_id": "us-pa-pittsburgh",
        "ahj": "Pittsburgh",
        "state": "PA",
        "county": "Allegheny",
        "project_family": "commercial_tenant_improvement",
        "scope": "Commercial tenant improvement",
        "status": "PUBLISHABLE",
        "serving_status": "TIER1_COMPLETE",
        "tier1": {
            "main_decision": {"value": "REQUIRED", "provenance": copy.deepcopy(prov)},
            "permits_required": [
                {
                    "permit_name": "Building Permit",
                    "permit_kind": "building",
                    "trigger": "Commercial alteration/tenant improvement",
                    "required_status": "required",
                    "provenance": copy.deepcopy(prov),
                },
                {
                    "permit_name": "Plumbing Permit",
                    "permit_kind": "plumbing",
                    "trigger": "Plumbing work or fixtures included",
                    "required_status": "conditional",
                    "provenance": copy.deepcopy(prov),
                },
            ],
            "trade_authority": [
                {
                    "trade": "building",
                    "handled_by_local_ahj": True,
                    "issuing_authority": {"name": "Pittsburgh PLI", "tier": "local"},
                    "application_authority": {"name": "Pittsburgh PLI"},
                    "negative_routing": [],
                    "provenance": copy.deepcopy(prov),
                },
                {
                    "trade": "plumbing",
                    "handled_by_local_ahj": False,
                    "issuing_authority": {"name": "Allegheny County Health Department", "tier": "county"},
                    "application_authority": {"name": "Allegheny County Health Department"},
                    "negative_routing": [
                        {
                            "authority": "Pittsburgh PLI",
                            "does_not_handle": "plumbing permits",
                            "provenance": copy.deepcopy(negative_prov),
                        }
                    ],
                    "provenance": copy.deepcopy(prov),
                },
            ],
            "apply": [
                {
                    "permit_name": "Building Permit",
                    "office_name": "Pittsburgh PLI",
                    "apply_url": "https://city.example.gov/building",
                    "url_status": "live",
                    "last_url_check": "2026-06-13T00:00:00Z",
                    "channel": "online",
                    "phone": "412-555-0100",
                    "address": "200 Ross St",
                    "provenance": copy.deepcopy(prov),
                }
            ],
            "fail_closed": {"active": False, "reason": None, "contact": {}},
        },
        "tier2": {"apply_path_detail": [], "fee_basis": [], "inspections": []},
        "change_watch": {
            "tier1_snapshot_hashes": [prov["snapshot_hash"]],
            "diff_cadence": "weekly",
            "last_diff": None,
            "stale": False,
        },
    }


def _issue_codes(result):
    return {issue.code for issue in result.issues}


def test_field_registry_enforces_tier_boundary():
    result = validate_field_registry()
    assert result.ok, result.to_dict()
    registry = json.loads((ROOT / "schema" / "permitassist_v24" / "fields.json").read_text())
    for name, spec in registry["fields"].items():
        if spec["tier"] == 2:
            assert spec["gate"] is False, name
        if spec["tier"] == 1:
            assert spec["gate"] is True, name


def test_valid_cell_passes_merge_gate(tmp_path):
    cell = _valid_cell(tmp_path)
    result = validate_v24_cell(cell, live_url_checker=lambda url: True)
    assert result.ok, result.to_dict()


def test_publishable_cell_requires_live_apply_url_and_checker(tmp_path):
    cell = _valid_cell(tmp_path)
    no_checker = validate_v24_cell(cell)
    assert not no_checker.ok
    assert "live_url_checker_required" in _issue_codes(no_checker)

    broken = validate_v24_cell(cell, live_url_checker=lambda url: False)
    assert not broken.ok
    assert "apply_url_not_live" in _issue_codes(broken)

    cell["tier1"]["apply"][0]["url_status"] = "stale"
    stale = validate_v24_cell(cell, live_url_checker=lambda url: True)
    assert not stale.ok
    assert "publishable_missing_live_apply_url" in _issue_codes(stale)


def test_not_required_cell_does_not_fabricate_permits_or_apply_routes(tmp_path):
    prov = _prov(tmp_path, "No permit is required for this exempt work.")
    cell = _valid_cell(tmp_path)
    cell["cell_id"] = "us-test__residential__exempt_work__not_required"
    cell["tier1"]["main_decision"] = {"value": "NOT_REQUIRED", "provenance": prov}
    cell["tier1"]["permits_required"] = []
    cell["tier1"]["trade_authority"] = []
    cell["tier1"]["apply"] = []
    result = validate_v24_cell(cell, live_url_checker=lambda url: False)
    assert result.ok, result.to_dict()
    public = assemble_customer_view(cell)
    assert public["permit_name"] == "No permit required"
    assert public["permits_required"] == []


def test_not_required_cell_rejects_fabricated_required_permits_and_routes(tmp_path):
    prov = _prov(tmp_path, "No permit is required for this exempt work.")
    cell = _valid_cell(tmp_path)
    cell["tier1"]["main_decision"] = {"value": "NOT_REQUIRED", "provenance": prov}
    result = validate_v24_cell(cell, live_url_checker=lambda url: True)
    assert not result.ok
    codes = _issue_codes(result)
    assert "not_required_contains_required_permit" in codes
    assert "not_required_contains_routing_rows" in codes


def test_merge_gate_rejects_missing_tier1_provenance(tmp_path):
    cell = _valid_cell(tmp_path)
    del cell["tier1"]["permits_required"][0]["provenance"]
    result = validate_v24_cell(cell, live_url_checker=lambda url: True)
    assert not result.ok
    assert "missing_provenance" in _issue_codes(result)


def test_merge_gate_rejects_quote_not_in_snapshot_and_hash_mismatch(tmp_path):
    cell = _valid_cell(tmp_path)
    cell["tier1"]["main_decision"]["provenance"]["source_quote"] = "This quote does not exist"
    cell["tier1"]["main_decision"]["provenance"]["snapshot_hash"] = "0" * 64
    result = validate_v24_cell(cell, live_url_checker=lambda url: True)
    codes = _issue_codes(result)
    assert "quote_not_in_snapshot" in codes
    assert "snapshot_hash_mismatch" in codes


def test_tier2_absent_is_non_blocking_but_present_must_be_sourced(tmp_path):
    cell = _valid_cell(tmp_path)
    assert validate_v24_cell(cell, live_url_checker=lambda url: True).ok
    cell["tier2"]["fee_basis"].append({"permit_name": "Building Permit", "formula_or_amount": "$12 per $1,000"})
    result = validate_v24_cell(cell, live_url_checker=lambda url: True)
    assert not result.ok
    assert "missing_provenance" in _issue_codes(result)


def test_negative_routing_requires_explicit_sourced_negative_knowledge(tmp_path):
    cell = _valid_cell(tmp_path)
    cell["tier1"]["trade_authority"][1]["negative_routing"] = [
        {"authority": "Pittsburgh PLI", "does_not_handle": "plumbing permits"}
    ]
    result = validate_v24_cell(cell, live_url_checker=lambda url: True)
    assert not result.ok
    assert "negative_routing_missing_full_provenance" in _issue_codes(result)


def test_negative_routing_quote_must_match_snapshot(tmp_path):
    cell = _valid_cell(tmp_path)
    cell["tier1"]["trade_authority"][1]["negative_routing"][0]["provenance"]["source_quote"] = "fabricated negative routing quote"
    result = validate_v24_cell(cell, live_url_checker=lambda url: True)
    assert not result.ok
    assert "quote_not_in_snapshot" in _issue_codes(result)


def test_absence_of_trade_authority_does_not_become_negative_knowledge(tmp_path):
    cell = _valid_cell(tmp_path)
    # Plumbing is triggered, but route is absent. This must fail closed; it must
    # never be interpreted as "not handled/not required" by absence.
    cell["tier1"]["trade_authority"] = [cell["tier1"]["trade_authority"][0]]
    result = validate_v24_cell(cell, live_url_checker=lambda url: True)
    assert not result.ok
    assert "triggered_trade_missing_authority" in _issue_codes(result)


def test_fail_closed_cell_requires_real_contact_and_can_bypass_missing_tier1(tmp_path):
    cell = _valid_cell(tmp_path)
    cell["status"] = "FAIL_CLOSED"
    cell["serving_status"] = "FAIL_CLOSED"
    cell["tier1"]["permits_required"] = []
    cell["tier1"]["trade_authority"] = []
    cell["tier1"]["apply"] = []
    cell["tier1"]["fail_closed"] = {
        "active": True,
        "reason": "Trade authority split could not be verified",
        "contact": {"office_name": "Pittsburgh PLI", "phone": "412-555-0100"},
    }
    result = validate_v24_cell(cell, live_url_checker=lambda url: True)
    assert result.ok, result.to_dict()


def test_assembler_accepts_narrative_only_and_rejects_regulated_field_tamper(tmp_path):
    cell = _valid_cell(tmp_path)
    safe = assemble_customer_view(cell, narrative_rewriter=lambda payload: "Submit through the verified permit office before starting.")
    assert safe["narrative"] == "Submit through the verified permit office before starting."
    assert safe["permit_required"] is True
    assert safe["narrative_rewrite_status"] == "accepted_narrative_only"

    contradiction = assemble_customer_view(cell, narrative_rewriter=lambda payload: "No permit required for this project.")
    assert contradiction["permit_required"] is True
    assert contradiction["permit_decision"] == "REQUIRED"
    assert contradiction["narrative_rewrite_status"] == "rejected_narrative_contradicts_regulated_decision"
    assert "Permit required" in contradiction["narrative"]

    def tamper(payload):
        return {"narrative": "No permit needed", "permit_required": False, "permit_decision": "NOT_REQUIRED"}

    guarded = assemble_customer_view(cell, narrative_rewriter=tamper)
    assert guarded["permit_required"] is True
    assert guarded["permit_decision"] == "REQUIRED"
    assert guarded["narrative_rewrite_status"] == "rejected_regulated_field_tamper_deterministic_fallback"
    assert "Permit required" in guarded["narrative"]


def test_v231_to_v24_spine_converter_marks_draft_not_publishable():
    index = json.loads((ROOT / "knowledge" / "permitassist_decision_cell_index_v231.json").read_text())["index"]
    key = "MA|worcester|commercial_tenant_improvement"
    if key not in index:
        key = "AZ|gilbert|commercial_tenant_improvement"
    cell = convert_v231_cell_to_v24_spine(index[key])
    assert cell["schema_version"] == "v2.4"
    assert cell["status"] == "DRAFT"
    assert cell["serving_status"] == "SPINE_ONLY"
    assert cell["tier1"]["main_decision"]["value"] == "REQUIRED"
    assert cell["tier1"]["apply"]
    assert "Must enrich trade_authority" in " ".join(cell["factory_notes"])


def test_v231_audit_reports_enrichment_readiness_counts():
    report = audit_v231_index_for_v24()
    assert report["total_index_entries"] > 0
    assert report["publishable"] > 0
    assert report["needs_v24_live_url_check"] == report["total_index_entries"]
    assert "by_project_family" in report
