import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from server import build_customer_permit_view_model  # noqa: E402
from scope_contract import build_scope_contract  # noqa: E402

FROZEN_EVIDENCE = ROOT / "artifacts" / "customer_e2e_20260612T231212Z" / "evidence.jsonl"


def _record(case_id: str) -> dict:
    if not FROZEN_EVIDENCE.exists():
        pytest.skip(f"frozen customer E2E artifact is not present in this checkout: {FROZEN_EVIDENCE}")
    for line in FROZEN_EVIDENCE.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record["case"]["id"] == case_id:
            return record
    raise AssertionError(f"missing frozen E2E case {case_id}")


def _public_for(case_id: str) -> dict:
    record = _record(case_id)
    case = record["case"]
    return build_customer_permit_view_model(
        copy.deepcopy(record["response_body"]),
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case["segment"],
    )


def _underscore_paths(value, path=""):
    hits = []
    if isinstance(value, dict):
        for key, child in value.items():
            next_path = f"{path}.{key}" if path else str(key)
            if str(key).startswith("_"):
                hits.append(next_path)
            hits.extend(_underscore_paths(child, next_path))
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            hits.extend(_underscore_paths(child, f"{path}[{idx}]"))
    return hits


def _customer_scope_text(result: dict) -> str:
    return " ".join(str(result.get(field) or "") for field in ("permit_name", "customer_headline", "customer_next_step", "permit_kind")).lower()


def test_public_boundary_drops_contact_safe_note_underscore_keys_after_contact_sanitization():
    public = _public_for("R-AZ-001")
    assert _underscore_paths(public) == []


def test_residential_pool_does_not_inherit_commercial_ti_anchor():
    public = _public_for("R-AZ-009")
    text = _customer_scope_text(public)
    assert public["permit_decision"] == "REQUIRED"
    assert "commercial" not in text
    assert "tenant improvement" not in text
    assert "Residential In-Ground Pool" in public["permit_name"]


def test_commercial_ti_keeps_trade_rows_but_normalizes_residential_hvac_label():
    public = _public_for("C-US-056")
    text = json.dumps(public, sort_keys=True).lower()
    assert public["permit_decision"] == "REQUIRED"
    assert "commercial building / tenant improvement" in text
    assert "plumbing permit" in text
    assert "mechanical permit" in text
    assert "residential" not in _customer_scope_text(public)
    assert "hvac system replacement (residential)" not in text


def test_required_missing_apply_url_falls_back_to_local_official_source_url():
    public = _public_for("R-US-012")
    assert public["permit_decision"] == "REQUIRED"
    assert public["apply_url"].startswith("https://")
    assert "dallas" in public["apply_url"].lower()


def test_source_adjudicated_not_required_controls_for_fixture_and_drywall_repairs():
    assert _public_for("R-AZ-010")["permit_decision"] == "NOT_REQUIRED"
    assert _public_for("R-US-019")["permit_decision"] == "NOT_REQUIRED"
    assert _public_for("R-US-023")["permit_decision"] == "NOT_REQUIRED"


def test_minneapolis_sign_face_change_expectation_is_required_from_official_sign_source():
    record = _record("C-US-059")
    public = _public_for("C-US-059")
    assert public["permit_decision"] == "REQUIRED"
    assert public["apply_url"].startswith("https://")
    assert any(
        "planning-zoning/applications-handouts/land-use-applications/sign-permit" in str(url)
        for url in (record["response_body"].get("source_urls") or [])
    )


def test_build_scope_contract_accepts_explicit_vertical_kwarg_used_by_view_model():
    contract = build_scope_contract(
        "commercial office tenant improvement with relocated lighting",
        "Dallas",
        "TX",
        job_category="commercial",
        vertical="commercial",
    )
    assert contract["category"] == "commercial"


def test_source_adjudicated_not_required_rules_do_not_downgrade_commercial_scope():
    response = {"permit_decision": "REQUIRED", "permit_required": True, "permit_verdict": "YES"}
    cases = [
        ("Flagstaff", "AZ", "commercial restaurant tenant improvement replacing faucet and garbage disposal, job value 25000"),
        ("Las Vegas", "NV", "commercial office tenant improvement replacing toilet and vanity no plumbing relocation, job value 25000"),
        ("Chicago", "IL", "commercial office tenant improvement drywall repair no structural no electrical work, job value 25000"),
    ]
    for city, state, job in cases:
        public = build_customer_permit_view_model(copy.deepcopy(response), job, city, state, job_category="commercial")
        assert public["permit_decision"] == "REQUIRED"
        assert "Commercial Building / Tenant Improvement" in json.dumps(public)


def test_residential_change_of_use_stays_required_without_commercial_ti_anchor():
    public = _public_for("R-US-012")
    text = _customer_scope_text(public)
    assert public["permit_decision"] == "REQUIRED"
    assert "commercial" not in text
    assert "tenant improvement" not in text
