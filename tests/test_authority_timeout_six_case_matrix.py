"""Offline two-phase replay of the exact V10 six-case semantic contract.

The first Crook pass forces the failed outer-timeout path.  The second models the
successful/cached research return.  Customer semantic projections must be byte
stable while all four active-core controls and the Phoenix legacy control retain
their original contracts.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time


ALLOWLIST = (
    "us-az-buckeye",
    "us-al-albertville",
    "us-ak-matanuska_susitna_borough",
    "us-wi-eau-claire",
)
MANIFEST_SHA256 = "9916d097a5e5f9331caa7530d96d2f378144824b113fe2a8fab5df2c716316b1"
CASES = (
    {
        "id": "buckeye-required",
        "payload": {"job_type": "residential reroof", "city": "Buckeye", "state": "AZ", "zip_code": "", "job_category": "residential"},
        "core": True,
        "decision": "REQUIRED",
        "families": [["building", "REQUIRED"]],
    },
    {
        "id": "albertville-six-family",
        "payload": {"job_type": "residential remodel", "city": "Albertville", "state": "AL", "zip_code": "", "job_category": "residential"},
        "core": True,
        "decision": "REQUIRED",
        "families": [["building", "REQUIRED"], ["demolition", "VERIFY"], ["electrical", "VERIFY"], ["gas", "VERIFY"], ["mechanical", "VERIFY"], ["plumbing", "VERIFY"]],
    },
    {
        "id": "matanuska-w4-ten-lane",
        "payload": {"job_type": "commercial tenant improvement", "city": "Matanuska-Susitna Borough", "state": "AK", "zip_code": "", "job_category": "commercial"},
        "core": True,
        "decision": "REQUIRED",
        "families": [["building", "REQUIRED"], ["electrical", "VERIFY"], ["fire", "VERIFY"], ["health", "VERIFY"], ["liquor", "VERIFY"], ["mechanical", "VERIFY"], ["occupancy", "VERIFY"], ["plumbing", "VERIFY"], ["wastewater", "VERIFY"], ["zoning", "VERIFY"]],
    },
    {
        "id": "eau-claire-alias-route",
        "payload": {"job_type": "commercial tenant improvement", "city": "Eau Claire", "state": "WI", "zip_code": "", "job_category": "commercial"},
        "core": True,
        "decision": "REQUIRED",
        "families": [["building", "REQUIRED"], ["electrical", "VERIFY"], ["fire", "VERIFY"], ["health", "VERIFY"], ["liquor", "VERIFY"], ["mechanical", "VERIFY"], ["occupancy", "VERIFY"], ["plumbing", "VERIFY"], ["wastewater", "VERIFY"], ["zoning", "VERIFY"]],
    },
    {
        "id": "phoenix-unallowlisted-legacy-control",
        "payload": {"job_type": "commercial tenant improvement", "city": "Phoenix", "state": "AZ", "zip_code": "", "job_category": "commercial"},
        "core": False,
        "decision": "REQUIRED",
    },
    {
        "id": "crook-county-positive-not-required-sentinel",
        "payload": {"job_type": "commercial tenant improvement", "city": "Crook County", "state": "WY", "zip_code": "", "job_category": "commercial"},
        "core": False,
        "decision": "NOT_REQUIRED",
    },
)
VOLATILE_KEYS = {
    "generated_at", "updated_at", "created_at", "timestamp", "request_id",
    "lookup_id", "trace_id", "cached", "cache_hit", "processing_ms",
    "duration_ms", "latency_ms",
}


def _semantic_projection(value):
    if isinstance(value, dict):
        return {
            key: _semantic_projection(child)
            for key, child in value.items()
            if str(key).lower() not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_semantic_projection(child) for child in value]
    return value


def _canonical_hash(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _phoenix_result() -> dict:
    return {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_name": "Commercial Building Permit",
        "permits_required": [
            {"permit_type": "Commercial Building Permit", "required": True, "kind": "Building"}
        ],
        "sources": [
            {
                "url": "https://www.phoenix.gov/pdd/development/permits",
                "title": "City of Phoenix Planning and Development permits",
            }
        ],
        "apply_url": "https://www.phoenix.gov/pdd/development/permits",
        "applying_office": "City of Phoenix Planning and Development Department",
        "confidence": "high",
        "warnings": [],
    }


def _public_result(server, raw: dict, payload: dict) -> dict:
    owned = server._mark_server_owned_result(raw)
    finalized = server.finalize_permit_lookup_result(
        owned,
        payload["job_type"],
        payload["city"],
        payload["state"],
        job_category=payload["job_category"],
        evidence_allowed=False,
    )
    return server.build_customer_response_egress(
        finalized,
        payload["job_type"],
        payload["city"],
        payload["state"],
        job_category=payload["job_category"],
    )


def _assert_case_contract(case: dict, result: dict) -> None:
    decision = case["decision"]
    assert result["permit_decision"] == decision
    assert result["permit_required"] is (decision == "REQUIRED")
    assert result["permit_verdict"] == ("YES" if decision == "REQUIRED" else "NO")
    rendered = json.dumps(result, sort_keys=True).lower()
    for forbidden in (
        "_decision_cell_primary_lock",
        "_runtime_authority_recovery",
        "_runtime_degraded_fallback",
        "permitassist_v24",
        "/home/boban",
    ):
        assert forbidden not in rendered
    if case.get("core"):
        families = [
            [row.get("family"), row.get("verdict")]
            for row in result.get("family_decisions", [])
            if isinstance(row, dict)
        ]
        assert families == case["families"]
    if case["id"] == "crook-county-positive-not-required-sentinel":
        assert result["permit_name"] == "No permit required"
        assert result["permits_required"] == []
        assert result["permits_required_logic"] == []
        assert result["source_support"]["has_official_source"] is True
        assert result["source_support"]["decision_mutation_allowed"] is False
        assert len(result["related_permits"]) == 2
        assert {
            row.get("decision") or row.get("verdict") or row.get("status")
            for row in result["related_permits"]
        } == {"CONDITIONAL"}
        assert result["apply_path"].get("documents_to_prepare", []) == []
        assert result["apply_path"].get("portal_selection_path", []) == []
        assert result["apply_path"].get("steps", []) == []
        assert "crookcounty.wy.gov" in rendered
        assert "commercial building / tenant improvement permit" not in rendered


def test_exact_six_case_two_phase_semantic_parity_and_zero_demotion(monkeypatch):
    from api import server

    monkeypatch.setenv("PERMITASSIST_RULE_ENGINE_CORE", "active")
    monkeypatch.setenv("PERMITASSIST_RULE_ENGINE_CORE_ALLOWLIST", ",".join(ALLOWLIST))
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    monkeypatch.setenv("PERMITASSIST_V24_MANIFEST_SHA256", MANIFEST_SHA256)
    monkeypatch.setattr(server, "PERMIT_LOOKUP_TOTAL_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(server, "sanitize_result_urls", lambda result: result)

    phase_hashes: list[dict[str, str]] = []
    active_core_legacy_calls: list[str] = []
    for phase in (1, 2):
        def legacy_result(job_type, city, state, *_args, **kwargs):
            if city not in {"Phoenix", "Crook County"}:
                active_core_legacy_calls.append(city)
                raise AssertionError(f"active-core case entered legacy research: {city}")
            if city == "Phoenix":
                return _phoenix_result()
            if phase == 1:
                time.sleep(0.10)
                return {
                    "permit_decision": "REQUIRED",
                    "permit_required": True,
                    "permit_verdict": "YES",
                    "permit_name": "Conflicting AI filing",
                    "permits_required": [{"permit_type": "Conflicting AI filing", "required": True}],
                }
            authoritative = server.resolve_authoritative_decision_cell_fallback(
                job_type,
                city,
                state,
                job_category=kwargs.get("job_category"),
            )
            assert authoritative is not None
            return copy.deepcopy(authoritative)

        monkeypatch.setattr(server, "research_permit", legacy_result)
        by_case: dict[str, str] = {}
        for case in CASES:
            payload = case["payload"]
            raw = server._research_permit_with_budget(
                payload["job_type"],
                payload["city"],
                payload["state"],
                payload["zip_code"],
                job_category=payload["job_category"],
                use_cache=True,
            )
            result = _public_result(server, raw, payload)
            _assert_case_contract(case, result)
            by_case[case["id"]] = _canonical_hash(_semantic_projection(result))
        phase_hashes.append(by_case)

    assert active_core_legacy_calls == []
    assert phase_hashes[0] == phase_hashes[1]
    assert set(phase_hashes[0]) == {case["id"] for case in CASES}
