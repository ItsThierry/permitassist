import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_full_customer_pov_clean_5946c7c_20260704T234342Z"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from public_packet import apply_public_packet_projection, build_public_packet
from scope_contract import build_scope_facts_v4


def _records() -> dict[str, dict]:
    return {
        json.loads(line)["case"]["id"]: json.loads(line)
        for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines()
        if line.strip()
    }


def _project(case_id: str) -> dict:
    rec = _records()[case_id]
    case = rec["case"]
    facts = build_scope_facts_v4(case["job_type"], case["city"], case["state"], job_category=case.get("segment"))
    result = copy.deepcopy(rec["response_body"])
    result["_request_city"] = case["city"]
    result["_request_state"] = case["state"]
    result["_request_job_type"] = case["job_type"]
    return apply_public_packet_projection(result, facts)


def _families(public: dict) -> set[str]:
    return {
        str(row.get("filing_family") or row.get("family") or "")
        for row in public.get("permits_required") or []
        if isinstance(row, dict)
    }


def test_false_not_required_kitchen_rows_promote_from_scope_without_using_citations_as_signal():
    for case_id in ["R-010", "R-012", "R-047"]:
        public = _project(case_id)
        assert public["permit_decision"] == "REQUIRED", {"case": case_id, "public": public}
        fams = _families(public)
        assert {"building", "electrical", "plumbing"} <= fams, {"case": case_id, "families": fams, "public": public}
        text = json.dumps(public, sort_keys=True).lower()
        assert "no permit required" not in text, {"case": case_id, "public": public}


def test_parking_lot_accessibility_contradictions_become_required_site_civil_packet():
    for case_id in ["C-006", "C-018"]:
        public = _project(case_id)
        assert public["permit_decision"] == "REQUIRED", {"case": case_id, "public": public}
        names = "\n".join(str(row.get("permit_name") or row.get("permit_type") or "") for row in public.get("permits_required") or [])
        assert "Right-of-Way / Site/Civil Permit" in names, {"case": case_id, "public": public}
        assert public.get("apply_url") or public.get("source_urls"), {"case": case_id, "public": public}


def test_source_backed_no_permit_with_citations_stays_not_required():
    result = {
        "permit_decision": "NOT_REQUIRED",
        "permit_required": False,
        "permit_verdict": "NO",
        "permit_name": "No permit required",
        "not_required_reason": "Official source says no permit is required for like-for-like cabinet hardware replacement.",
        "claim_citations": [{"source_url": "https://www.example.gov/permits", "quote": "No permit required"}],
        "source_urls": ["https://www.example.gov/permits"],
    }
    facts = build_scope_facts_v4("replace cabinet hardware only no plumbing electrical or structural work", "Testville", "TS", job_category="residential")
    public = apply_public_packet_projection(result, facts)
    assert public["permit_decision"] == "NOT_REQUIRED", public
    assert not public.get("permits_required"), public


def test_no_food_service_yoga_rows_drop_food_and_fog_but_keep_real_change_of_use_package():
    for case_id in ["C-028", "C-050"]:
        public = _project(case_id)
        fams = _families(public)
        assert public["permit_decision"] == "REQUIRED", {"case": case_id, "public": public}
        assert "health_food" not in fams, {"case": case_id, "families": fams, "public": public}
        assert "wastewater_pretreatment_fog" not in fams, {"case": case_id, "families": fams, "public": public}
        assert {"building_ti", "co_change_of_occupancy", "planning_zoning", "fire_life_safety_assembly"} & fams, {"case": case_id, "families": fams, "public": public}


def test_residential_hpwh_does_not_hard_require_standalone_refrigeration_or_building():
    for case_id in ["R-017", "R-023", "R-034", "R-044"]:
        public = _project(case_id)
        fams = _families(public)
        assert public["permit_decision"] == "REQUIRED", {"case": case_id, "public": public}
        assert "refrigeration" not in fams, {"case": case_id, "families": fams, "public": public}
        assert "building" not in fams, {"case": case_id, "families": fams, "public": public}
        assert fams & {"mechanical", "plumbing", "electrical"}, {"case": case_id, "families": fams, "public": public}


def test_standard_minisplit_keeps_refrigeration_margin_but_drops_standalone_building():
    public = _project("R-041")
    fams = _families(public)
    assert public["permit_decision"] == "REQUIRED", public
    assert "building" not in fams, public
    assert {"mechanical", "electrical"} <= fams, public


def test_seattle_residential_minisplit_refrigeration_allowlist_preserved():
    facts = build_scope_facts_v4(
        "install ductless mini split heat pump with exterior condenser and new electrical disconnect",
        "Seattle",
        "WA",
        job_category="residential",
    )
    assert "refrigeration" in set(facts.request_positive_families)


def test_claim_citations_can_supply_authority_source_but_do_not_force_required_by_themselves():
    result = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_name": "Electrical Permit",
        "permit_type": "Electrical Permit",
        "permits_required": [{"permit_name": "Electrical Permit", "family": "electrical", "decision": "REQUIRED", "required": True}],
        "claim_citations": [{"source_url": "https://www.augustaga.gov/2101/Building-Permits"}],
    }
    facts = build_scope_facts_v4("install level 2 EV charger on new 50 amp circuit", "Augusta", "GA", job_category="residential")
    packet = build_public_packet(result, facts)
    assert "https://www.augustaga.gov/2101/Building-Permits" in packet.authority.source_urls


def test_promoted_not_required_without_concrete_rows_falls_back_to_original_not_required():
    class UnsupportedFamilyFacts:
        segment = "residential"
        request_scope_text = "roofing permit required"
        request_positive_families = frozenset({"roofing"})
        forbidden_families = {}
        mandatory_family_floors = {}

        def as_dict(self):
            return {
                "segment": self.segment,
                "request_scope_text": self.request_scope_text,
                "request_positive_families": sorted(self.request_positive_families),
                "forbidden_families": {},
                "mandatory_family_floors": {},
            }

    result = {
        "permit_decision": "NOT_REQUIRED",
        "permit_required": False,
        "permit_verdict": "NO",
        "summary": "Permit required: Roofing Permit.",
        "permit_name": "No permit required",
    }
    public = apply_public_packet_projection(result, UnsupportedFamilyFacts())
    assert public["permit_decision"] == "NOT_REQUIRED", public
    assert not public.get("permits_required"), public
