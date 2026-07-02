from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))
ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_fable5_customer_pov_20260702T121009Z"

from closed_world_decision import apply_closed_world_customer_contract, check_render_fidelity  # noqa: E402


COUNTERFACTUALS = [
    ("residential vs commercial", "replace bathroom vanity only no plumbing electrical", "residential", set()),
    ("commercial TI", "commercial office tenant improvement with new partitions lighting and HVAC", "commercial", {"building_ti", "electrical", "mechanical"}),
    ("illuminated sign", "install illuminated wall sign", "commercial", {"sign", "electrical"}),
    ("non-illuminated sign", "install non-illuminated wall sign no electrical work", "commercial", {"sign"}),
    ("food service", "restaurant tenant improvement with commercial kitchen grease interceptor", "commercial", {"building_ti", "electrical", "mechanical", "plumbing", "health_food", "fire_suppression", "wastewater_pretreatment_fog", "planning_zoning", "co_change_of_occupancy"}),
    ("non food service", "commercial office carpet and paint only no walls no electrical no plumbing", "commercial", set()),
    ("change of use", "change occupancy from retail to restaurant with bar", "commercial", {"building_ti", "electrical", "mechanical", "plumbing", "health_food", "fire_suppression", "planning_zoning", "co_change_of_occupancy", "liquor"}),
    ("no change of use", "retail tenant install illuminated sign only no change of use", "commercial", {"sign", "electrical"}),
    ("trade present", "install new gas line to outdoor kitchen and grill", "residential", {"plumbing", "gas"}),
    ("trade absent", "replace carpet and paint only no electrical no plumbing no walls", "residential", set()),
]


def _apply(job: str, segment: str) -> dict:
    return apply_closed_world_customer_contract({}, job, "Testville", "TS", job_category=segment)


def _families(public: dict) -> set[str]:
    return {r.get("family") for r in public.get("permits_required") or [] if isinstance(r, dict)}


def test_counterfactual_decision_objects_are_stable():
    for label, job, segment, expected in COUNTERFACTUALS:
        public = _apply(job, segment)
        assert _families(public) == expected, (label, public.get("decision_object"))
        assert check_render_fidelity(public) == []


def test_all_live100_cases_have_versioned_decision_objects_and_render_fidelity():
    records = [json.loads(line) for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines() if line.strip()]
    assert len(records) == 100
    failures = []
    for rec in records:
        case = rec["case"]
        public = apply_closed_world_customer_contract(rec.get("response_body") or {}, case["job_type"], case["city"], case["state"], job_category=case.get("segment"))
        if public["decision_object"]["schema_version"] != "decision_object.v1":
            failures.append((case["id"], "schema"))
        issues = check_render_fidelity(public)
        if issues:
            failures.append((case["id"], issues))
    assert not failures
