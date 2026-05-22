#!/usr/bin/env python3
"""PermitAssist 3.0 Phase 0/1 eval harness.

Runs the frozen 100-case manifest against the exact-name engine, verifies the
final/non-final customer-output contract, and writes a reproducible report.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
sys.path.insert(0, str(API))

from permitassist3_exact_name_engine import (  # noqa: E402
    FINAL_VERIFIED,
    NON_FINAL,
    PermitAssist3ExactNameEngine,
    WriteBackCorpus,
    contains_forbidden_final_string,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_manifest(corpus_path: Path) -> tuple[dict, list[dict]]:
    data = json.loads(corpus_path.read_text(encoding="utf-8"))
    return data, list(data.get("eval_manifest") or [])


def grade_case(case: dict, result: dict) -> dict:
    expected = case.get("expected_status")
    state = result.get("final_answer_state")
    names = result.get("permit_names_or_categories") or []
    expected_names = case.get("expected_exact_names") or []
    forbidden = contains_forbidden_final_string(result)
    passed = False
    reason = []
    if expected == "final_verified":
        if state != FINAL_VERIFIED:
            reason.append(f"expected_final_got_{state}")
        missing = [name for name in expected_names if name not in names]
        if missing:
            reason.append("missing_expected_names:" + ";".join(missing))
        if not result.get("source_evidence"):
            reason.append("missing_source_evidence")
        passed = not reason
    elif expected == "non_final_completion_required":
        ticket = result.get("completion_ticket") or {}
        if state != NON_FINAL:
            reason.append(f"expected_non_final_got_{state}")
        if result.get("permit_type") or result.get("permit_name") or result.get("permits_required"):
            reason.append("non_final_has_final_fields")
        if not ticket.get("ticket_id"):
            reason.append("missing_ticket_id")
        passed = not reason
    else:
        reason.append("unknown_expected_status")
    if forbidden:
        passed = False
        reason.append("forbidden_customer_output_string")
    return {
        "case_id": case["case_id"],
        "ahj_id": case.get("ahj_id"),
        "vertical": case.get("vertical"),
        "expected_status": expected,
        "actual_state": state,
        "passed": passed,
        "reason": reason,
        "names": names,
        "ticket_id": ((result.get("completion_ticket") or {}).get("ticket_id")),
    }


def run_eval(corpus_path: Path, report_path: Path, *, eval_mode: bool = True) -> dict:
    corpus, manifest = load_manifest(corpus_path)
    writeback = WriteBackCorpus(ROOT / "data" / "permitassist3" / "eval_writeback_guard.jsonl")
    before_hash = writeback.hash()
    engine = PermitAssist3ExactNameEngine(corpus_path=corpus_path, writeback_path=writeback.path)
    rows = []
    outputs = []
    for case in manifest:
        result = engine.lookup(
            case["job_type"],
            case["city"],
            case["state"],
            explicit_vertical=case.get("vertical"),
            eval_mode=eval_mode,
        )
        outputs.append({"case": case, "result": result})
        rows.append(grade_case(case, result))
    after_hash = writeback.hash()

    counts = Counter(row["actual_state"] for row in rows)
    split_counts = Counter((case.get("split"), row["passed"]) for case, row in zip(manifest, rows))
    cell = defaultdict(lambda: {"total": 0, "passed": 0})
    for row in rows:
        key = f"{row['ahj_id']}::{row['vertical']}"
        cell[key]["total"] += 1
        cell[key]["passed"] += int(row["passed"])
    failures = [row for row in rows if not row["passed"]]
    exact_rows = [row for row in rows if row["expected_status"] == "final_verified"]
    non_final_rows = [row for row in rows if row["expected_status"] == "non_final_completion_required"]
    holdout_rows = [row for case, row in zip(manifest, rows) if case.get("split") == "holdout"]
    exact_pass = sum(row["passed"] for row in exact_rows)
    non_final_pass = sum(row["passed"] for row in non_final_rows)
    holdout_pass = sum(row["passed"] for row in holdout_rows)
    multi_permit_like = [out for out in outputs if len((out["result"].get("permit_families") or [])) >= 2]
    summary = {
        "schema": "permitassist3.phase0_1_eval_report.v1",
        "generated_at_utc": utc_now_iso(),
        "corpus_path": str(corpus_path),
        "corpus_metadata": corpus.get("metadata") or {},
        "case_count": len(rows),
        "passed_count": sum(row["passed"] for row in rows),
        "failed_count": len(failures),
        "pass_rate": round(sum(row["passed"] for row in rows) / max(len(rows), 1), 4),
        "exact_final_case_count": len(exact_rows),
        "exact_final_pass_count": exact_pass,
        "exact_final_pass_rate": round(exact_pass / max(len(exact_rows), 1), 4),
        "non_final_case_count": len(non_final_rows),
        "non_final_pass_count": non_final_pass,
        "non_final_pass_rate": round(non_final_pass / max(len(non_final_rows), 1), 4),
        "holdout_case_count": len(holdout_rows),
        "holdout_pass_count": holdout_pass,
        "holdout_pass_rate": round(holdout_pass / max(len(holdout_rows), 1), 4),
        "actual_state_counts": dict(counts),
        "split_counts": {f"{k[0]}::{k[1]}": v for k, v in split_counts.items()},
        "multi_permit_like_case_count": len(multi_permit_like),
        "forbidden_output_count": sum(contains_forbidden_final_string(out) for out in outputs),
        "writeback_guard_before_sha256": before_hash,
        "writeback_guard_after_sha256": after_hash,
        "writeback_unchanged_in_eval_mode": before_hash == after_hash,
        "cell_results": dict(sorted(cell.items())),
        "failures": failures[:50],
        "acceptance": {
            "overall_pass_rate_at_least_0_80": (sum(row["passed"] for row in rows) / max(len(rows), 1)) >= 0.80,
            "exact_final_pass_rate_at_least_0_80": (exact_pass / max(len(exact_rows), 1)) >= 0.80,
            "holdout_pass_rate_at_least_0_80": (holdout_pass / max(len(holdout_rows), 1)) >= 0.80,
            "non_final_controls_all_pass": non_final_pass == len(non_final_rows),
            "zero_forbidden_output_strings": sum(contains_forbidden_final_string(out) for out in outputs) == 0,
            "writeback_disabled_during_eval": before_hash == after_hash,
            "at_least_20_multi_permit_like_cases": len(multi_permit_like) >= 20,
        },
        "rows": rows,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", default=str(ROOT / "data" / "permitassist3" / "launch_corpus.json"))
    parser.add_argument("--report", default=str(ROOT / "artifacts" / "permitassist3_phase0_1_eval_report.json"))
    parser.add_argument("--no-eval-mode", action="store_true")
    args = parser.parse_args()
    summary = run_eval(Path(args.corpus), Path(args.report), eval_mode=not args.no_eval_mode)
    print(json.dumps({k: v for k, v in summary.items() if k not in {"rows", "cell_results", "failures"}}, indent=2, sort_keys=True))
    return 0 if all(summary["acceptance"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
