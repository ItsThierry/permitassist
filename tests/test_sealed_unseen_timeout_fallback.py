from __future__ import annotations

import hashlib
import json
from pathlib import Path

from api.server import _build_degraded_lookup_fallback


FIXTURE = Path(__file__).parent / "fixtures" / "sealed_unseen_timeout_scope_cases_v1.json"


def _family(row: dict) -> str:
    return str(row.get("family") or row.get("filing_family") or row.get("permit_family") or "").lower().strip()


def test_sealed_unseen_timeout_fixture_hash_and_scope_are_stable():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    expected = payload.pop("fixture_sha256")
    actual = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert actual == expected
    assert payload["seed"] == 20260730
    assert len(payload["cases"]) == 12
    assert not any(case["id"].startswith("F150-") for case in payload["cases"])


def test_sealed_unseen_timeout_replay_is_family_bearing_nonbinary_and_monotonic():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for case in payload["cases"]:
        first = _build_degraded_lookup_fallback(case["scope"], "Unseen", "ZZ")
        second = _build_degraded_lookup_fallback(case["scope"], "Unseen", "ZZ")
        assert first == second, case["id"]
        assert first["permit_decision"] == "NEEDS_INPUT", case["id"]
        assert first["permit_required"] is None, case["id"]
        assert first["permit_verdict"] == "NEEDS_INPUT", case["id"]
        assert first["decision_source"] == "lookup_timeout_needs_input", case["id"]
        rows = [row for row in first.get("family_decisions", []) if isinstance(row, dict)]
        families = {_family(row) for row in rows}
        assert set(case["must_include"]).issubset(families), {"case": case["id"], "families": sorted(families)}
        assert not (set(case["must_exclude"]) & families), {"case": case["id"], "families": sorted(families)}
        assert rows, case["id"]
        assert all(str(row.get("status") or row.get("required_status") or "").upper() == "NEEDS_INPUT" for row in rows), case["id"]
        assert all(row.get("required") is None for row in rows), case["id"]
        assert not any(str(row.get("status") or "").upper() in {"REQUIRED", "NOT_REQUIRED"} for row in rows), case["id"]
