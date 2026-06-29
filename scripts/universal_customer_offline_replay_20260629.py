#!/usr/bin/env python3
"""Universal offline replay for the 2026-06-29 PermitAssist customer-view roots.

Replays both frozen artifact roots through build_customer_permit_view_model and
checks the Phase 0 public-boundary contracts/invariants. This script is intended
as an artifact-level gate in addition to pytest.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
API = ROOT / "api"
for path in (str(TESTS), str(API)):
    if path not in sys.path:
        sys.path.insert(0, path)
os.environ.setdefault("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from universal_customer_phase0_helpers import (  # noqa: E402
    assert_basic_public_invariants,
    assert_contract_satisfied,
    build_public,
    load_all_cases,
    load_case,
    load_contracts,
    load_no_neuter_anchors,
    non_a_case_ids,
)

ROOT_IDS = ["prior_50_50_120408", "real_customer_50_50_160333"]


def main() -> int:
    failures: list[dict] = []
    counts: dict[str, int] = {}
    for root_id in ROOT_IDS:
        cases = load_all_cases(root_id)
        counts[root_id] = len(cases)
        try:
            assert len(cases) == 100, {"root_id": root_id, "count": len(cases)}
        except Exception as exc:  # noqa: BLE001
            failures.append({"root_id": root_id, "case_id": "__count__", "error": repr(exc)})
        for case in cases:
            if case["case_id"] not in non_a_case_ids(root_id):
                continue
            try:
                public = build_public(case)
                assert_basic_public_invariants(case, public)
            except Exception as exc:  # noqa: BLE001
                failures.append({"root_id": root_id, "case_id": case["case_id"], "invariant_error": repr(exc)})
    for contract in load_contracts():
        try:
            case = load_case(contract["root_id"], contract["case_id"])
            public = build_public(case)
            assert_basic_public_invariants(case, public)
            assert_contract_satisfied(contract, public)
        except Exception as exc:  # noqa: BLE001
            failures.append({"root_id": contract["root_id"], "case_id": contract["case_id"], "contract_error": repr(exc)})
    for anchor in load_no_neuter_anchors():
        try:
            case = load_case(anchor["root_id"], anchor["case_id"])
            public = build_public(case)
            assert public.get("permit_decision") in {"REQUIRED", "NOT_REQUIRED"}, {"case": case["case_id"], "decision": public.get("permit_decision")}
            assert public.get("permit_required") in {True, False}, {"case": case["case_id"], "permit_required": public.get("permit_required")}
        except Exception as exc:  # noqa: BLE001
            failures.append({"root_id": anchor["root_id"], "case_id": anchor["case_id"], "anchor_error": repr(exc)})
    if failures:
        print("FAIL universal customer-view artifact replay")
        print("counts=" + json.dumps(counts, sort_keys=True))
        for failure in failures[:80]:
            print(json.dumps(failure, sort_keys=True))
        print(f"failure_count={len(failures)}")
        return 1
    print("PASS universal customer-view artifact replay")
    print("counts=" + json.dumps(counts, sort_keys=True))
    print(f"contracts={len(load_contracts())} anchors={len(load_no_neuter_anchors())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
