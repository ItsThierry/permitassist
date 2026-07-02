from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))
ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_fable5_customer_pov_20260702T121009Z"

from closed_world_decision import (  # noqa: E402
    DecisionStatus,
    apply_closed_world_customer_contract,
    compose_decision_object,
)


def _record(case_id: str) -> dict:
    for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec["case"]["id"] == case_id:
            return rec
    raise AssertionError(f"case not found: {case_id}")


def _families(public: dict, status: str = "REQUIRED") -> set[str]:
    rows = public.get("permits_required") if status == "REQUIRED" else public.get("conditional_permits")
    return {str(r.get("family") or r.get("filing_family") or "") for r in rows or [] if isinstance(r, dict)}


def _names(public: dict, status: str = "REQUIRED") -> list[str]:
    rows = public.get("permits_required") if status == "REQUIRED" else public.get("conditional_permits")
    return [str(r.get("permit_name") or r.get("permit_type") or "") for r in rows or [] if isinstance(r, dict)]


def _apply(case_id: str) -> dict:
    rec = _record(case_id)
    case = rec["case"]
    return apply_closed_world_customer_contract(
        rec["response_body"],
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case.get("segment"),
    )


def test_decision_object_r033_keeps_electrical_and_blocks_food_fog_required():
    rec = _record("R-033")
    case = rec["case"]
    obj = compose_decision_object(rec["response_body"], case["job_type"], case["city"], case["state"], job_category=case.get("segment"))
    req = {item.family: item for item in obj.items if item.status == DecisionStatus.REQUIRED}
    assert "electrical" in req
    assert "health_food" not in req
    assert "wastewater_pretreatment_fog" not in req
    assert all("new circuit" not in item.permit_name.lower() for item in obj.items)

    public = _apply("R-033")
    assert "electrical" in _families(public)
    assert "health_food" not in _families(public)
    assert "wastewater_pretreatment_fog" not in _families(public)
    rendered = json.dumps(public).lower()
    assert "new circuit permit" not in rendered
    assert "new circuits required" not in rendered


def test_decision_object_c010_promotes_electrical_and_blocks_co_without_change_of_use():
    public = _apply("C-010")
    required = _families(public)
    assert "sign" in required
    assert "electrical" in required
    assert "co_change_of_occupancy" not in required
    assert "Certificate of Occupancy" not in "; ".join(_names(public))
    assert public.get("permit_name") == "Sign Permit"


def test_decision_object_r034_ess_electrical_lead_not_solar_structural():
    public = _apply("R-034")
    assert public.get("permit_kind") == "Electrical"
    assert "Battery" in str(public.get("permit_name")) or "ESS" in str(public.get("permit_name"))
    assert "Structural Racking" not in str(public.get("permit_name"))
    assert "roof load" not in json.dumps(public).lower()
    assert "structural engineering letter" not in json.dumps(public).lower()


def test_every_conditional_has_concrete_machine_trigger_not_generic_verify():
    public = _apply("C-010")
    for row in public.get("conditional_permits") or []:
        trigger = str(row.get("trigger") or row.get("required_if") or row.get("conditional_text") or "").lower()
        assert trigger
        assert "verify with ahj" not in trigger
        assert any(token in trigger for token in ["if", "when", "only needed"])


def test_decision_object_dedupes_one_required_row_per_family():
    for case_id in ["R-033", "C-010", "R-034"]:
        public = _apply(case_id)
        families = [r.get("family") for r in public.get("permits_required") or [] if isinstance(r, dict)]
        assert len(families) == len(set(families)), (case_id, families)
