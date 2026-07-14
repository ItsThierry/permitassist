"""R9 RED contracts for Part 4 blocker remediation.

These tests are intentionally added before implementation.  They encode the
security/trust boundary work required by the R8 audit closeout.
"""

from __future__ import annotations

import json

import pytest

from api import permit_rule_engine as pre
from api import research_engine
from api import server


def _buckeye_envelope():
    resolution = pre.resolve_v24_cell(
        "Buckeye", "AZ", "residential reroof", "residential", force=True
    )
    return pre.build_core_decision_envelope(
        resolution,
        job_type="residential reroof",
        city="Buckeye",
        state="AZ",
        job_category="residential",
    )


def test_r9_shared_exemption_polarity_classifier_handles_adversarial_negation() -> None:
    classifier = getattr(pre, "classify_exemption_polarity", None)
    enum_type = getattr(pre, "ExemptionPolarity", None)
    assert callable(classifier), "shared classifier must exist"
    assert enum_type is not None, "typed polarity enum must exist"

    cases = {
        "No permit is required for ordinary painting.": "POSITIVE_EXEMPTION",
        "The work may be performed without obtaining a permit.": "POSITIVE_EXEMPTION",
        "A permit is required; this work is not exempt from a permit.": "POSITIVE_REQUIREMENT",
        "Reroof work is not exempt from permit requirements.": "POSITIVE_REQUIREMENT",
        "A permit must be obtained before work starts.": "POSITIVE_REQUIREMENT",
        "Permit exemptions may apply; contact the office.": "AMBIGUOUS",
    }
    for text, expected in cases.items():
        actual = classifier(text)
        assert actual.value == expected, (text, actual)


def test_r9_server_not_required_citation_uses_shared_polarity_without_neutering_positive_exemption() -> None:
    base = {
        "applying_office": "City of Buckeye Development Services",
        "sources": [
            {
                "url": "https://www.buckeyeaz.gov/business/development-services/permit-center"
            }
        ],
    }

    adversarial = {
        **base,
        "claim_citations": [
            {
                "field": "permit_decision",
                "value": "NOT_REQUIRED",
                "source_url": "https://www.buckeyeaz.gov/business/development-services/permit-center",
                "quoted_snippet": "A permit is required; this reroof work is not exempt from a permit.",
                "confidence": "high",
            }
        ],
    }
    assert (
        server._not_required_claim_citation_is_source_backed(
            adversarial, "Buckeye", "AZ"
        )
        is False
    )

    positive = {
        **base,
        "claim_citations": [
            {
                "field": "permit_decision",
                "value": "NOT_REQUIRED",
                "source_url": "https://www.buckeyeaz.gov/business/development-services/permit-center",
                "quoted_snippet": "No permit is required for ordinary interior painting.",
                "confidence": "high",
            }
        ],
    }
    assert (
        server._not_required_claim_citation_is_source_backed(
            positive, "Buckeye", "AZ"
        )
        is True
    )


def test_r9_unsealed_public_core_dto_cannot_bypass_server_seal(monkeypatch) -> None:
    monkeypatch.setenv(pre.CORE_SETTING, "active")
    monkeypatch.setenv(pre.CORE_ALLOWLIST_SETTING, "us-az-buckeye")
    sealed_payload = json.loads(_buckeye_envelope().sealed_projection.payload_json)

    # Shape-valid public fields are not proof that the server produced the DTO.
    projected = pre.project_core_customer_boundary(
        sealed_payload,
        job_type="residential reroof",
        city="Buckeye",
        state="AZ",
        job_category="residential",
    )
    assert projected is not None
    assert projected["permit_decision"] == "UNKNOWN"
    assert projected["permit_required"] is None
    assert projected["coverage_status"] == "integrity_fail_closed"
    assert projected["permit_name"] != sealed_payload["permit_name"]


def test_r9_exact_complete_active_core_short_circuits_legacy_research(monkeypatch) -> None:
    monkeypatch.setenv(pre.CORE_SETTING, "active")
    monkeypatch.setenv(pre.CORE_ALLOWLIST_SETTING, "us-az-buckeye")

    def forbidden_legacy(*_args, **_kwargs):
        raise AssertionError("exact-complete active core must not run search/model synthesis")

    monkeypatch.setattr(server, "research_permit", forbidden_legacy)
    result = server._research_permit_with_budget(
        "residential reroof",
        "Buckeye",
        "AZ",
        job_category="residential",
    )
    projection = pre.extract_sealed_public_projection(
        result, city="Buckeye", state="AZ"
    )
    assert projection is not None
    assert projection["permit_decision"] == "REQUIRED"


def test_r9_unallowlisted_request_does_not_short_circuit_legacy_research(monkeypatch) -> None:
    monkeypatch.setenv(pre.CORE_SETTING, "active")
    monkeypatch.setenv(pre.CORE_ALLOWLIST_SETTING, "us-az-buckeye")
    marker = {"legacy_path": True}
    monkeypatch.setattr(server, "research_permit", lambda *_a, **_k: marker)

    assert (
        server._research_permit_with_budget(
            "commercial tenant improvement",
            "Anchorage",
            "AK",
            job_category="commercial",
        )
        is marker
    )


def test_r9_private_cache_telemetry_records_hit_without_customer_leak(
    monkeypatch, tmp_path
) -> None:
    reset = getattr(research_engine, "reset_private_cache_telemetry", None)
    snapshot = getattr(research_engine, "private_cache_telemetry_snapshot", None)
    assert callable(reset) and callable(snapshot)

    monkeypatch.setattr(research_engine, "CACHE_DB", str(tmp_path / "cache.db"))
    research_engine.init_cache()
    key = research_engine.cache_key(
        "residential reroof", "Buckeye", "AZ", "residential"
    )
    payload = {"permit_decision": "REQUIRED", "permit_required": True}
    research_engine.save_cache(
        key,
        "residential reroof",
        "residential",
        "Buckeye",
        "AZ",
        "",
        payload,
    )

    reset()
    assert research_engine.get_cached(key) == payload
    events = snapshot()
    assert len(events) == 1
    event = events[0]
    assert event["cache_decision"] == "hit"
    assert event["cache_key_hash"] != key
    assert len(event["cache_key_hash"]) == 64
    assert event["reason"] == "fresh_valid_payload"
    assert event["request_id"]
    assert not any(str(k).startswith("_permit_cache") for k in payload)


def test_r9_cache_decision_classifier_distinguishes_bypass_and_preview_suppression() -> None:
    classify = getattr(research_engine, "classify_private_cache_decision", None)
    assert callable(classify)
    assert classify(use_cache=False, suppress_cache_write=False) == (
        "bypass",
        "caller_disabled_cache",
    )
    assert classify(use_cache=False, suppress_cache_write=True) == (
        "suppressed",
        "suppressed_preview",
    )


def test_r9_bentonville_commercial_route_does_not_publish_residential_provenance() -> None:
    resolution = pre.resolve_v24_cell(
        "Bentonville",
        "AR",
        "commercial tenant improvement",
        "commercial",
        force=True,
    )
    envelope = pre.build_core_decision_envelope(
        resolution,
        job_type="commercial tenant improvement",
        city="Bentonville",
        state="AR",
        job_category="commercial",
    )
    projection = json.loads(envelope.sealed_projection.payload_json)
    route = projection["family_authority_routes"][0]["application_route"]
    provenance_urls = {
        str(item.get("source_url") or item.get("url") or "")
        for item in route.get("provenance", [])
    }
    assert not any("/183/Residential-Applications" in url for url in provenance_urls)
    assert route["channel"] == "verify"
    assert "route_provenance_scope_mismatch" in route["validation_issue_codes"]
    assert route["apply_url"].startswith("https://www.bentonville.ar.gov/")


def test_r9_antibot_or_challenge_page_is_unknown_not_unreachable() -> None:
    classify = getattr(pre, "classify_route_reachability", None)
    enum_type = getattr(pre, "RouteReachability", None)
    assert callable(classify) and enum_type is not None
    assert (
        classify(
            http_status=403,
            body_sample="Access denied. Verify you are human to continue.",
        ).value
        == "UNKNOWN"
    )
    assert classify(http_status=200, body_sample="Permit Center").value == "REACHABLE"
    assert classify(http_status=404, body_sample="Not found").value == "UNREACHABLE"


def test_r9_official_query_evidence_is_typed_hash_bound_and_disabled_by_default(
    monkeypatch,
) -> None:
    evidence_type = getattr(pre, "OfficialQueryEvidence", None)
    guard = getattr(pre, "official_query_evidence_guard", None)
    setting = getattr(
        pre,
        "OFFICIAL_QUERY_EVIDENCE_SETTING",
        "PERMITASSIST_RULE_ENGINE_OFFICIAL_QUERY_EVIDENCE",
    )
    assert evidence_type is not None and callable(guard)

    evidence = evidence_type(
        query="site:example.gov reroof permit exemption",
        jurisdiction_id="us-az-buckeye",
        source_url="https://www.buckeyeaz.gov/business/development-services/permit-center",
        source_quote="No permit is required for the stated scope.",
        snapshot_hash="a" * 64,
        checked_at="2026-07-14T00:00:00Z",
        publishable=True,
    )
    monkeypatch.delenv(setting, raising=False)
    disabled = guard(evidence)
    assert disabled["enabled"] is False
    assert disabled["exercised"] is True
    assert disabled["reason"] == "feature_disabled"

    monkeypatch.setenv(setting, "active")
    enabled = guard(evidence)
    assert enabled["enabled"] is True
    assert enabled["valid"] is True
    assert enabled["reason"] == "valid_hash_bound_official_query_evidence"

    invalid = evidence_type(
        query=evidence.query,
        jurisdiction_id=evidence.jurisdiction_id,
        source_url=evidence.source_url,
        source_quote=evidence.source_quote,
        snapshot_hash="0" * 64,
        checked_at=evidence.checked_at,
        publishable=True,
    )
    rejected = guard(invalid)
    assert rejected["enabled"] is True
    assert rejected["valid"] is False
    assert rejected["reason"] == "invalid_or_unbound_evidence"
