#!/usr/bin/env python3
# pyright: reportMissingImports=false
"""Non-tautological revised Phase 2/3 eval and customer-output scan.

The held-out manifest below is hand-written and intentionally separate from the
Phase 7B corpus loader.  It checks customer/eval parity, verified-final invariant,
pending-active retrieval invariants, overlay wiring, and forbidden phrase hygiene.
"""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
sys.path.insert(0, str(API))

from permitassist3_revised import (  # noqa: E402
    PENDING_ACTIVE_RETRIEVAL,
    VERIFIED_FINAL,
    CustomerOutputScanner,
    PermitAssist3RevisedEngine,
    PermitAssist3RevisedFinalGate,
    load_verified_corpus_slice,
)

HELD_OUT_CASES = [
    {
        "case_id": "verified-austin-restaurant-ti",
        "job_type": "restaurant tenant improvement with hood and grease interceptor",
        "city": "Austin",
        "state": "TX",
        "expected_state": VERIFIED_FINAL,
        "expected_vertical": "restaurant_ti",
    },
    {
        "case_id": "verified-tampa-medical-clinic-ti",
        "job_type": "medical clinic tenant improvement exam rooms",
        "city": "Tampa",
        "state": "FL",
        "expected_state": VERIFIED_FINAL,
        "expected_vertical": "medical_clinic_ti",
    },
    {
        "case_id": "verified-los-angeles-office-ti",
        "job_type": "office tenant improvement buildout",
        "city": "Los Angeles",
        "state": "CA",
        "expected_state": VERIFIED_FINAL,
        "expected_vertical": "office_ti",
    },
    {
        "case_id": "pending-plano-restaurant-ti",
        "job_type": "restaurant tenant improvement",
        "city": "Plano",
        "state": "TX",
        "expected_state": PENDING_ACTIVE_RETRIEVAL,
        "expected_vertical": "restaurant_ti",
    },
    {
        "case_id": "pending-real-city-ma-office-ti",
        "job_type": "office tenant improvement",
        "city": "Real City",
        "state": "MA",
        "expected_state": PENDING_ACTIVE_RETRIEVAL,
        "expected_vertical": "office_ti",
    },
]


def _valid_pending(packet: dict[str, Any]) -> bool:
    ticket = packet.get("completion_ticket") or {}
    return bool(
        packet.get("public_state") == PENDING_ACTIVE_RETRIEVAL
        and packet.get("customer_final") is False
        and not packet.get("source_backed_exact_permit_name")
        and not packet.get("source_backed_official_portal_category_path")
        and ticket.get("tracker_id", "").startswith("pa3-")
        and ticket.get("owner")
        and 0 < int(ticket.get("sla_hours") or 0) <= 24
        and ticket.get("alarm", {}).get("on_sla_breach")
        and ticket.get("writeback", {}).get("required") is True
        and packet.get("missing_fields")
    )


def run_eval(report_path: Path) -> dict[str, Any]:
    engine = PermitAssist3RevisedEngine(
        ticket_path=report_path.parent / "permitassist3_revised_phase2_3_eval_tickets.jsonl",
        writeback_path=report_path.parent / "permitassist3_revised_phase2_3_eval_writeback.jsonl",
    )
    gate = PermitAssist3RevisedFinalGate()
    scanner = CustomerOutputScanner()
    corpus = load_verified_corpus_slice()
    rows = []
    failures = []
    for case in HELD_OUT_CASES:
        packet = engine.lookup(case["job_type"], case["city"], case["state"])
        scan = scanner.scan(packet.get("customer_output") or {})
        ok = packet.get("public_state") == case["expected_state"] and packet.get("vertical") == case["expected_vertical"] and scan["pass"]
        gate_reasons: list[str] = []
        if case["expected_state"] == VERIFIED_FINAL:
            gate_ok, gate_reasons = gate.validate_verified_final(packet)
            ok = ok and gate_ok and bool(packet.get("official_source_provenance"))
        else:
            ok = ok and _valid_pending(packet)
        row = {
            "case_id": case["case_id"],
            "expected_state": case["expected_state"],
            "actual_state": packet.get("public_state"),
            "expected_vertical": case["expected_vertical"],
            "actual_vertical": packet.get("vertical"),
            "customer_scan_pass": scan["pass"],
            "gate_reasons": gate_reasons,
            "ok": bool(ok),
        }
        rows.append(row)
        if not ok:
            failures.append(row)
    report = {
        "schema": "permitassist3.revised_phase2_3_eval.v1",
        "held_out_manifest_source": "tools/permitassist3_revised_phase2_3_eval.py::HELD_OUT_CASES",
        "corpus": {
            "record_count": len(corpus.records),
            "source_artifacts": list(corpus.source_artifacts),
            "generated_from_round_robin": corpus.generated_from_round_robin,
        },
        "eval": {
            "case_count": len(rows),
            "pass_count": sum(1 for row in rows if row["ok"]),
            "failure_count": len(failures),
            "rows": rows,
            "failures": failures,
        },
        "acceptance": {
            "static_verified_corpus_slice_25_to_50": 25 <= len(corpus.records) <= 50,
            "static_verified_corpus_not_round_robin": corpus.generated_from_round_robin is False,
            "held_out_cases_pass": not failures,
            "customer_rendered_generic_final_rate_zero": not any(not row["customer_scan_pass"] for row in rows),
            "pending_cases_have_owner_tracker_sla_alarm_writeback": all(
                row["ok"] for row in rows if row["expected_state"] == PENDING_ACTIVE_RETRIEVAL
            ),
            "verified_cases_pass_final_gate": all(row["ok"] for row in rows if row["expected_state"] == VERIFIED_FINAL),
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default=str(ROOT / "artifacts" / "permitassist3_revised_phase2_3_eval_report.json"))
    args = parser.parse_args()
    report = run_eval(Path(args.report))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if all(report["acceptance"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
