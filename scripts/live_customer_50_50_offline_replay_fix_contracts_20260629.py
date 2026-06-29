#!/usr/bin/env python3
"""Offline replay gate for the 2026-06-29 50/50 live customer artifact root.

Replays every frozen response through build_customer_permit_view_model, enforces
baseline public-boundary invariants for all 100 rows, and enforces the focused
50/50 fix contracts for the non-A + no-neuter anchors.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
TESTS = ROOT / "tests"
ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_50_50_20260629T120408Z"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
os.environ.setdefault("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

import server  # noqa: E402
from live_customer_50_50_phase0_helpers import assert_contract_satisfied, load_contracts, required_rows  # noqa: E402


def _body(path: Path) -> dict:
    raw = json.loads(path.read_text())
    body = raw.get("response_body") or raw.get("body") or raw
    assert isinstance(body, dict), path
    return body


def _replay_case(case: dict) -> dict:
    return server.build_customer_permit_view_model(
        _body(Path(case["artifact_json_path"])),
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case.get("segment"),
    )


def _all_cases() -> list[dict]:
    out = []
    for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines():
        if line.strip():
            out.append(json.loads(line)["case"])
    return out


def _public_text(public: dict) -> str:
    return json.dumps(public, sort_keys=True, default=str).lower()


def _assert_all_case_boundary(case: dict, public: dict) -> None:
    decision = str(public.get("permit_decision") or "").upper()
    assert decision in {"REQUIRED", "NOT_REQUIRED"}, {"case": case["id"], "decision": decision}
    assert public.get("permit_required") in {True, False}, {"case": case["id"], "permit_required": public.get("permit_required")}
    text = _public_text(public)
    assert "source-backed" not in text and "decision_cell" not in text and "customerdecisiondto" not in text, case["id"]
    if decision == "REQUIRED":
        assert public.get("permit_required") is True, case["id"]
        assert required_rows(public), {"case": case["id"], "permit_name": public.get("permit_name")}
        assert not re.search(r"\bno permit required\b|\bno permit submission needed\b", text), {"case": case["id"], "permit_name": public.get("permit_name")}
        apply_path = public.get("apply_path") if isinstance(public.get("apply_path"), dict) else {}
        assert apply_path.get("channel") != "no_permit_required", case["id"]
    else:
        assert public.get("permit_required") is False, case["id"]
        assert not required_rows(public), {"case": case["id"], "rows": required_rows(public)}
        assert "file the required permit" not in text, case["id"]


def main() -> int:
    cases = _all_cases()
    assert len(cases) == 100, len(cases)
    by_id = {case["id"]: case for case in cases}
    failures = []
    for case in cases:
        try:
            public = _replay_case(case)
            _assert_all_case_boundary(case, public)
        except Exception as exc:  # noqa: BLE001
            failures.append({"case_id": case["id"], "error": repr(exc)})
    for contract in load_contracts():
        try:
            public = _replay_case(by_id[contract["case_id"]])
            assert_contract_satisfied(contract, public)
        except Exception as exc:  # noqa: BLE001
            failures.append({"case_id": contract["case_id"], "contract_error": repr(exc)})
    if failures:
        print("FAIL live_customer_100_50_50 artifact replay")
        for failure in failures[:50]:
            print(json.dumps(failure, sort_keys=True))
        print(f"failure_count={len(failures)}")
        return 1
    print(f"PASS live_customer_100_50_50 artifact replay: {len(cases)} cases; {len(load_contracts())} focused contracts")
    print(f"artifact_root={ARTIFACT_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
