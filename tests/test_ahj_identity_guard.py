from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from ahj_identity_guard import apply_ahj_identity_guard, check_source_identity


def test_cross_state_same_name_city_rejected():
    verdict = check_source_identity("https://www.portland.gov/ppd/get-permit/apply-permits", "Portland", "ME")
    assert verdict.status == "WRONG_STATE"


def test_county_source_for_city_address_needs_delegation():
    verdict = check_source_identity("https://www.laramiecountywy.gov/County-Government/County-Departments/Planning-Development/Laramie-County-Building", "Cheyenne", "WY")
    assert verdict.status == "NEEDS_DELEGATION_EVIDENCE"


def test_delegated_county_accepted_with_evidence():
    source = {"url": "https://county.example.gov/permits", "snippet": "County delegation serves the city of Example for building permits."}
    assert check_source_identity(source, "Example", "EX").status == "OK"


def test_dead_action_path_falls_back_to_official_portal_not_generic():
    out = apply_ahj_identity_guard({"permit_decision": "REQUIRED", "permit_required": True, "apply_url": "https://developdallas.dallascityhall.com/PermitDallas/", "sources": []}, "Dallas", "TX")
    assert "dallascityhall.com" in out["apply_url"]
    assert "contact" not in (out.get("customer_next_step") or "").lower()
