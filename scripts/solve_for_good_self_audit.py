#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

from closed_world_decision import apply_closed_world_customer_contract, canonical_family, check_render_fidelity  # noqa: E402
from family_reconciliation_gate import family_from_row  # noqa: E402

ARTIFACT = ROOT / "artifacts" / "live_customer_100_fable5_customer_pov_20260702T121009Z" / "cases.jsonl"
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "live100_full_customer"
BLOCKED_PUBLIC = re.compile(r"(_family_gate_audit|gate_audit|CONDITIONAL_FALLBACK|\bVETO\b|\bDEMOTE\b|positive scope fact|deterministic implication|source_status|_debug)", re.I)


def load_cases():
    for line in ARTIFACT.read_text().splitlines():
        if line.strip():
            yield json.loads(line)


def public_text(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def row_family(row):
    if not isinstance(row, dict):
        return ""
    return canonical_family(row.get("family") or row.get("filing_family"), row.get("permit_name") or row.get("permit_type"))


def audit_live100():
    issues = []
    for rec in load_cases():
        case = rec.get("case") or {}
        cid = rec.get("case_id") or rec.get("id") or case.get("id") or case.get("case_id")
        old = rec.get("response_body") or rec.get("public") or {}
        public = apply_closed_world_customer_contract(old, case.get("job_type", ""), case.get("city", ""), case.get("state", ""), job_category=case.get("segment"))
        if not public.get("decision_object"):
            issues.append((cid, "missing_decision_object"))
        if not public.get("render_fidelity", {}).get("pass"):
            issues.append((cid, "render_fidelity", public.get("render_fidelity")))
        if check_render_fidelity(public):
            issues.append((cid, "check_render_fidelity", check_render_fidelity(public)))
        required = public.get("permits_required") or []
        families = [row.get("family") for row in required if isinstance(row, dict)]
        if len(families) != len(set(families)):
            issues.append((cid, "duplicate_required_family", families))
        if required:
            lead_name = public.get("permit_name")
            if not any(isinstance(r, dict) and r.get("permit_name") == lead_name for r in required):
                issues.append((cid, "lead_name_not_in_required_rows", lead_name, required))
            packet_leads = [r for r in public.get("public_packet_rows") or [] if isinstance(r, dict) and r.get("lead")]
            if len(packet_leads) != 1:
                issues.append((cid, "packet_lead_count", len(packet_leads), public.get("permit_name"), public.get("public_packet_rows")))
        if BLOCKED_PUBLIC.search(public_text(public)):
            issues.append((cid, "public_internal_leak"))
    return issues


def audit_fixture_no_neuter_demotions():
    issues = []
    for raw_path in sorted(FIXTURE_ROOT.glob("*/raw_lookup.json")):
        cid = raw_path.parent.name
        expected_path = raw_path.parent / "expected_contract.json"
        if not expected_path.exists():
            continue
        contract = json.loads(expected_path.read_text())
        if not contract.get("protection"):
            continue
        raw = json.loads(raw_path.read_text())
        case = raw["case"]
        before = raw["response_body"]
        after = apply_closed_world_customer_contract(before, case.get("job_type", ""), case.get("city", ""), case.get("state", ""), job_category=case.get("segment"))
        before_fams = {canonical_family(r.get("family") or r.get("filing_family"), r.get("permit_name") or r.get("permit_type")) for r in before.get("permits_required") or [] if isinstance(r, dict)}
        after_fams = {canonical_family(r.get("family") or r.get("filing_family"), r.get("permit_name") or r.get("permit_type")) for r in after.get("permits_required") or [] if isinstance(r, dict)}
        cond_fams = {canonical_family(r.get("family") or r.get("filing_family"), r.get("permit_name") or r.get("permit_type")) for r in after.get("conditional_permits") or [] if isinstance(r, dict)}
        lost = before_fams - after_fams
        unaccounted = []
        for fam in lost:
            if fam not in cond_fams:
                unaccounted.append(fam)
        if unaccounted:
            issues.append((cid, sorted(unaccounted), "before", sorted(before_fams), "after", sorted(after_fams), "cond", sorted(cond_fams)))
    return issues


if __name__ == "__main__":
    live_issues = audit_live100()
    demotion_issues = audit_fixture_no_neuter_demotions()
    print(json.dumps({
        "live100_issue_count": len(live_issues),
        "live100_issues": live_issues[:30],
        "demotion_issue_count": len(demotion_issues),
        "demotion_issues": demotion_issues[:30],
    }, indent=2))
    raise SystemExit(1 if live_issues or demotion_issues else 0)
