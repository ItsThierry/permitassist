#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

from closed_world_decision import (  # noqa: E402
    DecisionStatus,
    apply_closed_world_customer_contract,
    canonical_family,
    check_render_fidelity,
)

DEFAULT_ARTIFACT = ROOT / "artifacts" / "live_customer_100_fable5_customer_pov_20260702T121009Z"
DEFAULT_OUT = ROOT / "artifacts" / "live100_solve_for_good_offline"

FOCUS_BLOCKLIST = {
    "R-033": {"health_food", "wastewater_pretreatment_fog"},
    "C-010": {"co_change_of_occupancy"},
    "R-034": {"building"},
}
FOCUS_REQUIRED = {
    "R-033": {"electrical"},
    "C-010": {"sign", "electrical"},
    "R-034": {"battery_storage"},
}


def _old_required_families(body: dict) -> set[str]:
    out: set[str] = set()
    for row in body.get("permits_required") or []:
        if isinstance(row, dict):
            out.add(canonical_family(row.get("family") or row.get("filing_family"), row.get("permit_name") or row.get("permit_type")))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    cases_path = args.artifact_root / "cases.jsonl"
    records = [json.loads(line) for line in cases_path.read_text().splitlines() if line.strip()]
    decision_path = args.out / "decision_objects.jsonl"
    public_path = args.out / "public_outputs.jsonl"

    failures: list[dict] = []
    focus_summary: dict[str, dict] = {}
    total_fidelity_pass = 0
    renderable_bad_links = 0

    with decision_path.open("w") as d_out, public_path.open("w") as p_out:
        for rec in records:
            case = rec["case"]
            body = rec.get("response_body") or {}
            public = apply_closed_world_customer_contract(
                body,
                case["job_type"],
                case["city"],
                case["state"],
                job_category=case.get("segment"),
            )
            decision = public["decision_object"]
            d_out.write(json.dumps({"case_id": case["id"], "decision_object": decision}, sort_keys=True) + "\n")
            p_out.write(json.dumps({"case_id": case["id"], "public": public}, sort_keys=True) + "\n")
            required = {row.get("family") for row in public.get("permits_required") or [] if isinstance(row, dict)}
            issues = check_render_fidelity(public)
            if not issues:
                total_fidelity_pass += 1
            else:
                failures.append({"case_id": case["id"], "type": "render_fidelity", "issues": issues})
            for item in public.get("link_liveness") or []:
                if item.get("renderable") and item.get("reason") in {"placeholder_pattern", "irrelevant_domain", "irrelevant_content", "invalid_url"}:
                    renderable_bad_links += 1
                    failures.append({"case_id": case["id"], "type": "bad_renderable_link", "url": item.get("url"), "reason": item.get("reason")})
            if case["id"] in FOCUS_REQUIRED:
                missing = sorted(FOCUS_REQUIRED[case["id"]] - required)
                bad = sorted(FOCUS_BLOCKLIST.get(case["id"], set()) & required)
                focus_summary[case["id"]] = {"required": sorted(required), "missing": missing, "blocked_present": bad}
                if missing or bad:
                    failures.append({"case_id": case["id"], "type": "focus_contract", "missing": missing, "blocked_present": bad})
            # Side-by-side recall ledger: compare against old rows, but report as
            # ledger rather than auto-failing because C rows include known bad overreach.
            old_required = _old_required_families(body)
            lost = sorted(old_required - required)
            if lost:
                failures.append({"case_id": case["id"], "type": "side_by_side_lost_old_required_ledger", "lost": lost, "old": sorted(old_required), "new": sorted(required)})

    hard_failures = [f for f in failures if f["type"] not in {"side_by_side_lost_old_required_ledger"}]
    summary = {
        "artifact_root": str(args.artifact_root),
        "records": len(records),
        "decision_objects": str(decision_path),
        "public_outputs": str(public_path),
        "render_fidelity_pass": total_fidelity_pass,
        "render_fidelity_total": len(records),
        "renderable_bad_links": renderable_bad_links,
        "focus_summary": focus_summary,
        "hard_failure_count": len(hard_failures),
        "side_by_side_lost_old_required_ledger_count": len(failures) - len(hard_failures),
        "failures": failures[:200],
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    (args.out / "SUMMARY.md").write_text(
        "# Live100 solve-for-good offline replay\n\n"
        f"records: {len(records)}\n\n"
        f"render fidelity: {total_fidelity_pass}/{len(records)}\n\n"
        f"hard failures: {len(hard_failures)}\n\n"
        f"side-by-side lost-old-required ledger entries: {len(failures) - len(hard_failures)}\n\n"
        f"focus summary:\n```json\n{json.dumps(focus_summary, indent=2, sort_keys=True)}\n```\n"
    )
    print(json.dumps(summary, indent=2, sort_keys=True)[:12000])
    return 0 if not hard_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
