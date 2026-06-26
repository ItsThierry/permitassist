#!/usr/bin/env python3
"""Offline replay gate for the v24 live-341 FilingPath contract fixture.

Runs the customer ViewModel finalizer over the compact frozen live fixture and
checks the universal FilingPath invariants without calling production or network.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
for path in (str(ROOT), str(API)):
    if path not in sys.path:
        sys.path.insert(0, path)

# Import server without live API credentials; this replay never calls LLM/network paths.
os.environ.setdefault("OPENAI_API_KEY", "offline-replay-not-used")
os.environ.setdefault("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")

from api import server  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "v24_live_341_filing_path_replay.json"
VALID_STATES = {"RESOLVED_PORTAL", "RESOLVED_COUNTER", "HONEST_FALLBACK"}
GENERIC_TOKENS = ("iccsafe.org", "icc-safe.org", "nfpa.org", "energy.gov", "codes.iccsafe.org", "upcodes.com")


def is_required(body: dict) -> bool:
    return body.get("permit_required") is True or str(body.get("permit_decision") or "").upper() == "REQUIRED"


def public_from_row(row: dict) -> dict:
    payload = row.get("request_payload") or {}
    body = row.get("response_body") or {}
    return server.build_customer_permit_view_model(
        body,
        payload.get("job_type", ""),
        payload.get("city", ""),
        payload.get("state", ""),
        job_category=payload.get("job_category"),
        explicit_vertical=payload.get("vertical"),
    )


def check_row(row: dict) -> list[str]:
    body = row.get("response_body") or {}
    if not is_required(body):
        return []
    public = public_from_row(row)
    failures: list[str] = []
    if public.get("permit_decision") != "REQUIRED" or public.get("permit_required") is not True:
        failures.append("decision_not_preserved")
    apply_path = public.get("apply_path") if isinstance(public.get("apply_path"), dict) else {}
    state = apply_path.get("state")
    if state not in VALID_STATES:
        failures.append(f"invalid_filing_state:{state}")
    apply_url = public.get("apply_url") or public.get("online_application_url") or apply_path.get("portal_url")
    if state == "RESOLVED_PORTAL" and not apply_url:
        failures.append("resolved_portal_without_url")
    if state in {"RESOLVED_COUNTER", "HONEST_FALLBACK"} and apply_url:
        failures.append("non_portal_state_has_url")
    next_step = str(public.get("customer_next_step") or "").lower()
    if not apply_url and "use the local permit portal" in next_step:
        failures.append("generic_portal_copy_without_url")
    apply_url_lc = str(apply_url or "").lower()
    if any(token in apply_url_lc for token in GENERIC_TOKENS):
        failures.append("generic_model_code_apply_url")
    return failures


def main() -> int:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = fixture["rows"]
    required = 0
    failures = []
    issue_required_counts = {}
    for row in rows:
        if is_required(row.get("response_body") or {}):
            required += 1
            for code in row.get("issue_codes") or []:
                issue_required_counts[code] = issue_required_counts.get(code, 0) + 1
        row_failures = check_row(row)
        if row_failures:
            failures.append({"id": row.get("id"), "issues": row.get("issue_codes", []), "failures": row_failures})
    summary = {
        "fixture": str(FIXTURE),
        "total_rows": len(rows),
        "required_rows_checked": required,
        "issue_required_counts": dict(sorted(issue_required_counts.items())),
        "contract_failures": len(failures),
        "failures": failures[:20],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
