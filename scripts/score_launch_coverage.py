#!/usr/bin/env python3
"""Deterministic absolute scorer for frozen launch-coverage contracts."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from launch_coverage import load_coverage_registry, resolve_precharge_support
import server

LEAK_PATTERNS = (
    "decision cell", "scope_signal_only", "server-held", "integrity_fail_closed",
    "permit_rule_engine", "_internal", "debug_trace", "prompt",
)


def norm(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    return re.sub(r"\bahj\b", "issuing authority", text)


def score_contract(contract: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    resolution = resolve_precharge_support(
        job_type=contract["job_type"], city=contract["city"], state=contract["state"],
        zip_code=contract.get("zip_code", ""), segment=contract["segment"], supplied_facts=None,
    )
    if resolution.outcome.value != "SUPPORTED" or resolution.contract is None:
        return {"contract_id": contract["contract_id"], "grade": "F", "failures": ["not_supported"]}
    public = server.build_launch_coverage_customer_result(resolution.contract)
    if public.get("permit_decision") != contract["decision"]:
        failures.append("hard_decision_error")
    if norm(public.get("applying_office")) != norm(contract["authority"]):
        failures.append("wrong_ahj")
    rows = [row for row in public.get("permits_required") or [] if isinstance(row, dict)]
    names = {norm(row.get("permit_name") or row.get("permit_family") or row.get("kind")) for row in rows}
    for family in contract.get("required_families") or []:
        if norm(family) not in names:
            failures.append(f"dangerous_omission:{family}")
    for family in contract.get("prohibited_hard_required_families") or []:
        target = norm(family)
        if any(target in name or name in target for name in names if name):
            failures.append(f"prohibited_hard_required:{family}")
    action = public.get("apply_path") or public.get("application_route") or {}
    if not str(action.get("url") or "").startswith("https://"):
        failures.append("non_actionable_filing_path")
    if norm(action.get("office")) != norm(contract["maps_destination"]):
        failures.append("wrong_filing_office")
    if not str(action.get("maps_url") or "").startswith("https://www.google.com/maps/search/?api=1&query="):
        failures.append("invalid_maps_route")
    if not isinstance(public.get("permit_manifest"), dict):
        failures.append("missing_manifest")
    if server.project_customer_response_egress(public) != public:
        failures.append("payload_projection_divergence")
    blob = json.dumps(public, sort_keys=True).lower()
    if any(pattern in blob for pattern in LEAK_PATTERNS):
        failures.append("internal_leak")
    # Registry loading has already recomputed and authenticated every contract
    # digest before this projection is reachable.
    grade = "F" if failures else ("B" if contract["decision"] == "CONDITIONAL" else "A")
    return {
        "contract_id": contract["contract_id"], "grade": grade,
        "decision": public.get("permit_decision"), "failures": failures,
        "response": public,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("registry", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    registry = load_coverage_registry(args.registry)
    raw = json.loads(args.registry.read_text(encoding="utf-8"))
    rows = [score_contract(dict(contract)) for contract in registry.contracts]
    counts = {grade: sum(row["grade"] == grade for row in rows) for grade in "ABCDF"}
    summary = {
        "registry_sha256": registry.registry_sha256,
        "contract_count": len(rows),
        "grades": counts,
        "hard_decision_errors": sum("hard_decision_error" in row["failures"] for row in rows),
        "dangerous_omissions": sum(any(x.startswith("dangerous_omission") for x in row["failures"]) for row in rows),
        "wrong_ahjs": sum("wrong_ahj" in row["failures"] for row in rows),
        "non_actionable_filing_paths": sum("non_actionable_filing_path" in row["failures"] for row in rows),
        "internal_leaks": sum("internal_leak" in row["failures"] for row in rows),
        "payload_projection_divergence": sum("payload_projection_divergence" in row["failures"] for row in rows),
        "ab_demotions": counts["C"] + counts["D"] + counts["F"],
        "passed": counts["A"] + counts["B"] == len(rows),
        "source_plan_sha256": raw.get("source_plan_sha256"),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "results.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
