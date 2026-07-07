from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "test-key-for-import-only")
os.environ.setdefault("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import decision_resolver


def test_omission_derived_no_verdict_is_required_floor() -> None:
    dto = decision_resolver.resolve_customer_decision(
        {
            "result": {
                "permit_verdict": "NO",
                "permits_required": [],
                "sources": [{"url": "https://example.com/permit-tips", "title": "City permit office"}],
                "applying_office": "Phoenix Permit Department",
            },
            "job_type": "install freestanding pergola in back yard",
            "city": "Phoenix",
            "state": "AZ",
            "scope_contract": {"category": "residential"},
        }
    )
    assert dto["permit_decision"] == "REQUIRED", dto
    assert dto["permit_required"] is True
    assert dto["decision_basis"] == "scope_required_floor"


def test_repaired_json_cannot_create_not_required_without_positive_evidence() -> None:
    dto = decision_resolver.resolve_customer_decision(
        {
            "result": {
                "permit_decision": "NOT_REQUIRED",
                "permit_required": False,
                "permit_verdict": "NO",
                "permits_required": [],
                "_json_repaired": True,
                "sources": [{"url": "https://example.com/permit-tips"}],
            },
            "job_type": "install freestanding pergola in back yard",
            "city": "Phoenix",
            "state": "AZ",
            "scope_contract": {"category": "residential"},
        }
    )
    assert dto["permit_decision"] == "REQUIRED", dto
    assert dto["permit_required"] is True


def test_empty_permits_verdict_derivation_requires_positive_no_permit_evidence() -> None:
    import research_engine

    assert research_engine._derive_permit_verdict_from_result({"permits_required": []}) == "MAYBE"
    assert research_engine._derive_permit_verdict_from_result({"permits_required": [], "not_required_reason": "City exemption says no permit is required."}) == "NO"
    assert research_engine._derive_permit_verdict_from_result({"permits_required": [], "not_required_reason": "No permit required.", "_json_repaired": True}) == "MAYBE"


def test_ahj_direct_rejects_boilerplate_plus_random_permit_url() -> None:
    dto = decision_resolver.resolve_customer_decision(
        {
            "result": {
                "sources": [{"url": "https://example.com/permit-tips", "title": "City permit office"}],
                "apply_path": {"permit_category": "building permit", "office_name": "Phoenix permit office"},
                "applying_office": "Phoenix Permit Department",
            },
            "job_type": "install freestanding pergola in back yard",
            "city": "Phoenix",
            "state": "AZ",
            "scope_contract": {"category": "residential"},
        }
    )
    assert dto["permit_decision"] == "REQUIRED", dto
    assert dto["confidence_tier"] != "AHJ_DIRECT", dto
    assert dto["source_support"]["has_official_source"] is False


def test_ahj_direct_requires_actual_local_official_url_value() -> None:
    dto = decision_resolver.resolve_customer_decision(
        {
            "result": {"sources": [{"url": "https://www.phoenix.gov/pdd", "title": "Phoenix Planning and Development"}]},
            "job_type": "install freestanding pergola in back yard",
            "city": "Phoenix",
            "state": "AZ",
            "scope_contract": {"category": "residential"},
        }
    )
    assert dto["permit_decision"] == "REQUIRED", dto
    assert dto["confidence_tier"] == "AHJ_DIRECT", dto
    assert dto["source_support"]["has_official_source"] is True


def test_source_failure_seen_reads_structured_diagnostics_not_source_text() -> None:
    assert decision_resolver._source_failure_seen(
        {"sources": [{"url": "https://www.phoenix.gov/pdd", "title": "Permit timeout rules for contractors"}]}
    ) is False
    assert decision_resolver._source_failure_seen({"retrieval_diagnostics": {"error": "timeout while fetching official source"}}) is True
