#!/usr/bin/env python3
from __future__ import annotations

"""Local micro-benchmark seam for the Session 3 customer pipeline refactor."""

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from server import build_customer_permit_view_model  # noqa: E402

PAYLOAD = {
    "permit_required": True,
    "permit_decision": "REQUIRED",
    "permit_verdict": "YES",
    "permit_name": "Building Permit",
    "permit_kind": "Building",
    "applying_office": "Phoenix Planning and Development Department",
    "apply_url": "https://www.phoenix.gov/pdd",
    "source_urls": ["https://www.phoenix.gov/pdd"],
    "sources": [{"url": "https://www.phoenix.gov/pdd", "title": "Phoenix PDD"}],
    "permits_required": [
        {"permit_type": "Building Permit", "family": "building", "required": True, "decision": "REQUIRED"}
    ],
}


def digest(payload: dict) -> str:
    public = build_customer_permit_view_model(payload, "single-family kitchen remodel, no exterior work", "Phoenix", "AZ", job_category="residential")
    stable = json.dumps(public, sort_keys=True, default=str)
    return hashlib.sha256(stable.encode()).hexdigest()


def main() -> int:
    loops = 25
    start = time.perf_counter()
    hashes = [digest(PAYLOAD) for _ in range(loops)]
    elapsed = time.perf_counter() - start
    result = {
        "loops": loops,
        "elapsed_seconds": round(elapsed, 6),
        "per_lookup_ms": round((elapsed / loops) * 1000, 3),
        "stable_hash": len(set(hashes)) == 1,
        "sha256": hashes[0],
    }
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["stable_hash"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
