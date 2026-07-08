from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from customer_boundary_validator import (  # noqa: E402
    CanonicalDecisionObject,
    canonical_render_diffs,
    project_canonical_decision,
    validate_customer_boundary,
)
from family_policy_matrix import forbidden_families, mandatory_families  # noqa: E402
from live100_fable5_final_gate import apply_fable5_final_customer_gate  # noqa: E402
from scope_contract import build_scope_facts_v4  # noqa: E402

ARTIFACT_ROOT = ROOT / "artifacts" / "customer_pov_100_50_50_b1a2bb1_20260708T012930Z"
PHASE0 = ARTIFACT_ROOT / "phase0_core_truth_remediation"
RED_FIXTURES = PHASE0 / "PHASE0_RED_CORE_TRUTH_FIXTURES.json"
CASES_JSONL = ARTIFACT_ROOT / "cases.jsonl"
RENDERED_JSONL = ARTIFACT_ROOT / "rendered_customer_reports_all_100.jsonl"


def _load_json(path: Path):
    if not path.exists():
        pytest.skip(f"artifact missing: {path}")
    return json.loads(path.read_text())


def _jsonl_by_case(path: Path) -> dict[str, dict]:
    if not path.exists():
        pytest.skip(f"artifact missing: {path}")
    out: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        case_id = rec.get("case_id") or (rec.get("case") or {}).get("id")
        if case_id:
            out[str(case_id)] = rec
    return out


def _response_for_case(case_id: str) -> dict:
    cases = _jsonl_by_case(CASES_JSONL)
    rec = cases.get(case_id)
    assert rec, case_id
    body = rec.get("response_body")
    assert isinstance(body, dict), case_id
    return body


def _rendered_text_for_case(case_id: str) -> str:
    rendered = _jsonl_by_case(RENDERED_JSONL)
    rec = rendered.get(case_id) or {}
    return str(rec.get("body_text") or rec.get("visible_text") or rec.get("visible_report_excerpt") or "")


def test_part1_post_render_validator_flags_at_least_30_old_core_failures() -> None:
    fixtures = _load_json(RED_FIXTURES)
    flagged: dict[str, list[str]] = {}
    for fixture in fixtures:
        case_id = fixture["case_id"]
        findings = validate_customer_boundary(
            _response_for_case(case_id),
            visible_text=_rendered_text_for_case(case_id),
            expected=fixture,
            include_matrix=False,
        )
        if findings:
            flagged[case_id] = [f.code for f in findings]
    assert len(flagged) >= 30, flagged


def test_part1_validator_plus_scope_matrix_dry_run_flags_at_least_40_old_core_failures() -> None:
    fixtures = _load_json(RED_FIXTURES)
    flagged: dict[str, list[str]] = {}
    for fixture in fixtures:
        case_id = fixture["case_id"]
        facts = build_scope_facts_v4(
            fixture["job_type"],
            fixture["city"],
            fixture["state"],
            job_category=fixture.get("segment"),
        )
        findings = validate_customer_boundary(
            _response_for_case(case_id),
            visible_text=_rendered_text_for_case(case_id),
            expected=fixture,
            facts=facts,
            include_matrix=True,
        )
        if findings:
            flagged[case_id] = [f.code for f in findings]
    assert len(flagged) >= 40, flagged


def test_canonical_decision_object_pure_projection_repairs_stale_mirrors_without_changing_truth() -> None:
    public = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "required_permit_families": ["plumbing"],  # stale mirror
        "required_permit_names": ["Plumbing Permit"],
        "permits_required": [{"permit_type": "Plumbing Permit", "family": "plumbing", "decision": "REQUIRED", "required": True}],
        "public_packet": {
            "schema_version": "final_public_permit_packet.v1",
            "decision": "REQUIRED",
            "segment": "commercial",
            "authority": {"name": "Test Building Department", "apply_url": "", "source_urls": []},
            "required_families": ["building_ti", "electrical"],
            "conditional_families": [],
            "rows": [
                {"permit_name": "Commercial Building / Tenant Improvement Permit", "family": "building_ti", "decision": "REQUIRED"},
                {"permit_name": "Electrical Permit", "family": "electrical", "decision": "REQUIRED"},
            ],
        },
    }
    assert canonical_render_diffs(public)
    canonical = CanonicalDecisionObject.from_public(public)
    projected = project_canonical_decision(public, canonical)
    assert projected["permit_decision"] == "REQUIRED"
    assert projected["permit_required"] is True
    assert projected["required_permit_families"] == ["building_ti", "electrical"]
    assert {r["family"] for r in projected["permits_required"]} == {"building_ti", "electrical"}


def test_scopefactsv4_floor_ceiling_matrix_uses_request_facts_only_and_floor_beats_same_family_ceiling() -> None:
    facts = build_scope_facts_v4(
        "commercial install standby generator with automatic transfer switch, new electrical feeder, gas branch line, no plumbing fixtures",
        "Testville",
        "TX",
        job_category="commercial",
    )
    floors = mandatory_families(facts)
    ceilings = forbidden_families(facts)
    assert "electrical" in floors
    assert "gas" in floors
    assert "plumbing" in floors  # gas branch line floors plumbing/gas path
    assert "electrical" not in ceilings
    # Explicit no-plumbing is preserved as a negative fact, but same-family gas
    # branch floor wins for enforcement; conflict is left for validator review.
    assert "plumbing" not in ceilings


def test_fable5_final_gate_consumes_scopefactsv4_matrix_for_false_not_required_floor() -> None:
    job = "commercial tenant improvement for dental clinic with new x-ray electrical circuit, sinks, compressor, and fire alarm tie-in"
    facts = build_scope_facts_v4(job, "Testville", "TX", job_category="commercial")
    base = {"permit_required": False, "permit_decision": "NOT_REQUIRED", "permit_verdict": "NO", "permits_required": [], "_core_truth_matrix_enforce": True}
    out = apply_fable5_final_customer_gate(base, job, "Testville", "TX", {"category": "commercial"}, facts)
    families = {r.get("family") or r.get("filing_family") for r in out.get("permits_required") or [] if isinstance(r, dict)}
    assert out["permit_decision"] == "REQUIRED"
    assert {"building_ti", "electrical", "plumbing"}.issubset(families)
