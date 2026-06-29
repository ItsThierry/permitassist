import copy
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))
os.environ.setdefault("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

import server  # noqa: E402


def _families(public):
    rows = []
    for key in ("permits_required", "related_permits"):
        rows.extend(row for row in public.get(key, []) if isinstance(row, dict))
    return [(server._pa20_row_family(row) or server._customer_row_family(row), server._pa20_row_status(row) or server._customer_row_status(row) or ("VERIFY" if not row.get("required") else "REQUIRED")) for row in rows]


def test_scope_signal_floor_is_idempotent_and_does_not_duplicate_rows():
    base = {"permit_decision": "NOT_REQUIRED", "permit_required": False, "permit_verdict": "NO", "permits_required": [], "related_permits": []}
    job = "replace bathtub with walk in shower and relocate drain six inches, no electrical work"
    once = server._pa20_apply_scope_signal_family_floor(copy.deepcopy(base), job, "Queens", "NY")
    twice = server._pa20_apply_scope_signal_family_floor(copy.deepcopy(once), job, "Queens", "NY")
    assert once["permit_decision"] == "REQUIRED"
    assert _families(once) == _families(twice)
    assert _families(once).count(("plumbing", "REQUIRED")) == 1


def test_de_minimis_only_signal_keeps_not_required_contract():
    base = {"permit_decision": "REQUIRED", "permit_required": True, "permit_verdict": "YES", "permits_required": [{"permit_type": "Building Permit", "filing_family": "building", "required": True, "status": "REQUIRED"}]}
    job = "replace interior prehung door same size, no wall framing or header changes"
    out = server._pa20_apply_scope_signal_family_floor(copy.deepcopy(base), job, "Raleigh", "NC")
    assert out["permit_decision"] == "NOT_REQUIRED"
    assert out.get("permits_required") == []


def test_generator_no_building_expansion_demotes_building_without_deleting_guidance():
    base = {"permit_decision": "REQUIRED", "permit_required": True, "permit_verdict": "YES", "permits_required": [
        {"permit_type": "Building Permit", "filing_family": "building", "required": True, "status": "REQUIRED"},
        {"permit_type": "Electrical Permit", "filing_family": "electrical", "required": True, "status": "REQUIRED"},
    ], "related_permits": []}
    job = "install emergency generator and automatic transfer switch for outpatient clinic, no building expansion"
    out = server._pa20_apply_scope_signal_family_floor(copy.deepcopy(base), job, "Orlando", "FL")
    required = {fam for fam, status in _families(out) if status == "REQUIRED"}
    verify = {fam for fam, status in _families(out) if status == "VERIFY"}
    assert "electrical" in required
    assert "building" not in required
    assert "building" in verify


def test_synthesized_verify_rows_have_trigger_condition_text():
    base = {"permit_decision": "REQUIRED", "permit_required": True, "permit_verdict": "YES", "permits_required": [{"permit_type": "Electrical Permit", "filing_family": "electrical", "required": True, "status": "REQUIRED"}], "related_permits": []}
    job = "install emergency generator and automatic transfer switch for outpatient clinic, no building expansion"
    out = server._pa20_apply_scope_signal_family_floor(copy.deepcopy(base), job, "Orlando", "FL")
    out = server._pa20_add_trigger_conditions_to_visible_floor_rows(out, job)
    for row in out.get("related_permits", []):
        if (server._pa20_row_status(row) or server._customer_row_status(row)) in {"VERIFY", "CONDITIONAL"}:
            assert row.get("trigger_condition") or row.get("condition_text")
