from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_customer_pov_after_6b8b1f1_20260703T191058Z"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from server import build_customer_permit_view_model

# GREEN before the Fable5 change; these are the non-neuter tripwires.
SENTINELS: dict[str, dict[str, Any]] = {
    "C-001": {"label": "restaurant TI", "decision": "REQUIRED", "required": {"building_ti", "health_food", "wastewater_pretreatment_fog", "fire_suppression", "mechanical", "plumbing", "gas", "electrical"}, "apply": True},
    "C-016": {"label": "brewery / food production", "decision": "REQUIRED", "required": {"building_ti", "health_food", "wastewater_pretreatment_fog", "liquor", "gas", "plumbing", "mechanical", "electrical"}, "apply": True},
    "C-023": {"label": "medical office TI", "decision": "REQUIRED", "required": {"building_ti", "plumbing", "mechanical", "electrical"}, "apply": True},
    "C-034": {"label": "commercial kitchen gas/RTU line", "decision": "REQUIRED", "required": {"mechanical", "plumbing", "gas"}, "apply": True},
    "R-034": {"label": "ESS / battery install", "decision": "REQUIRED", "required": {"battery_storage"}, "conditional": {"building"}, "apply": True},
    "R-018": {"label": "ADU", "decision": "REQUIRED", "required": {"building", "electrical", "mechanical", "plumbing"}, "apply": True},
    "R-006": {"label": "residential solar with panel/battery", "decision": "REQUIRED", "required": {"building", "battery_storage", "electrical", "solar_pv"}, "apply": True},
    "C-039": {"label": "commercial change-of-use to restaurant", "decision": "REQUIRED", "required": {"building_ti", "health_food", "plumbing", "mechanical", "electrical", "co_change_of_occupancy"}, "apply": True},
}


def _record(case_id: str) -> dict[str, Any]:
    for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines():
        rec = json.loads(line)
        if rec.get("case", {}).get("id") == case_id:
            return rec
    raise AssertionError(f"missing Live100 sentinel {case_id}")


def _load(case_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    rec = _record(case_id)
    case = rec["case"]
    old = os.environ.get("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD")
    os.environ["PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD"] = "1"
    try:
        public = build_customer_permit_view_model(
            rec.get("response_body") or {},
            case["job_type"],
            case["city"],
            case["state"],
            job_category=case.get("segment"),
        )
    finally:
        if old is None:
            os.environ.pop("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD", None)
        else:
            os.environ["PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD"] = old
    return case, public


def _packet_rows(public: dict[str, Any], decision: str) -> list[dict[str, Any]]:
    packet = public.get("public_packet") if isinstance(public.get("public_packet"), dict) else {}
    return [r for r in packet.get("rows") or [] if isinstance(r, dict) and str(r.get("decision") or "").upper() == decision]


def _families(public: dict[str, Any], decision: str) -> set[str]:
    return {str(r.get("family") or "") for r in _packet_rows(public, decision)}


@pytest.mark.parametrize("case_id", sorted(SENTINELS), ids=lambda cid: f"{cid}:{SENTINELS[cid]['label']}")
def test_no_neuter_sentinel_keeps_required_families_decision_and_apply_path(case_id: str):
    _case, public = _load(case_id)
    spec = SENTINELS[case_id]
    assert public.get("permit_decision") == spec["decision"], (case_id, public.get("permit_decision"))
    required = _families(public, "REQUIRED")
    conditional = _families(public, "CONDITIONAL")
    assert spec["required"] <= required, (case_id, spec["required"], required, [r.get("permit_name") for r in _packet_rows(public, "REQUIRED")])
    assert set(spec.get("conditional", set())) <= (required | conditional), (case_id, conditional)
    if spec.get("apply"):
        apply_path = public.get("apply_path") if isinstance(public.get("apply_path"), dict) else {}
        assert public.get("apply_url") or apply_path.get("portal_url"), (case_id, public.get("apply_url"), apply_path)
