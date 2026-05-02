from pathlib import Path
from importlib import util

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_import_server = _debug_helper._import_server


def _import_matrix():
    matrix_spec = util.spec_from_file_location(
        "verified_tier_matrix",
        Path(__file__).resolve().parents[1] / "api" / "verified_tier_matrix.py",
    )
    matrix = util.module_from_spec(matrix_spec)
    matrix_spec.loader.exec_module(matrix)
    return matrix


def test_medium_and_partial_confidence_require_field_specific_support(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    no_quote = server.enforce_strict_field_confidence(
        "fee_range",
        "$1,000-$2,000",
        [{
            "source_url": "https://example.gov/building-permits",
            "source_title": "Building Permits",
            "quoted_snippet": "Apply for building permits online.",
            "source_type": "official_ahj",
            "supports_field": True,
            "confidence_signal": "medium",
            "last_verified": "2026-05-02",
        }],
    )
    assert no_quote["confidence"] == "needs_verification"
    assert no_quote["needs_review"] is True

    partial_with_direct_quote = server.enforce_strict_field_confidence(
        "inspections",
        ["Rough building", "Final building"],
        [{
            "source_url": "https://example.gov/inspections",
            "source_title": "Inspections",
            "quoted_snippet": "Schedule required inspection and final certificate review through the building department.",
            "source_type": "official_ahj",
            "supports_field": True,
            "confidence_signal": "partial",
            "last_verified": "2026-05-02",
        }],
    )
    assert partial_with_direct_quote["confidence"] == "partial"
    assert partial_with_direct_quote["needs_review"] is False


def test_apply_url_disallow_rules_are_path_aware_not_raw_substring(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    valid_with_safe_fee_word = server.enforce_strict_field_confidence(
        "apply_url",
        "https://permits.example.gov/permit-applications/coffee-shop/start",
        [{
            "source_url": "https://permits.example.gov/permit-applications/coffee-shop/start",
            "source_title": "Permit Applications",
            "quoted_snippet": "Start and submit a building permit application through the online portal.",
            "source_type": "official_ahj",
            "supports_field": True,
            "confidence_signal": "high",
            "last_verified": "2026-05-02",
        }],
    )
    assert valid_with_safe_fee_word["confidence"] == "high"

    fee_page = server.enforce_strict_field_confidence(
        "apply_url",
        "https://permits.example.gov/building-permit-fees",
        [{
            "source_url": "https://permits.example.gov/building-permit-fees",
            "source_title": "Fees",
            "quoted_snippet": "Start and submit a building permit application through the online portal.",
            "source_type": "official_ahj",
            "supports_field": True,
            "confidence_signal": "high",
            "last_verified": "2026-05-02",
        }],
    )
    assert fee_page["confidence"] == "needs_verification"


def test_generic_gov_page_cannot_prove_external_apply_portal(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    for source_type in ("webpage", "official_ahj"):
        strict = server.enforce_strict_field_confidence(
            "apply_url",
            "https://vendor.example.com/start/dallas",
            [{
                "source_url": "https://dallas.gov/building/permits",
                "source_title": "Dallas Building Permits",
                "quoted_snippet": "Start and submit a building permit application through the online portal.",
                "source_type": source_type,
                "supports_field": True,
                "confidence_signal": "high",
                "last_verified": "2026-05-02",
            }],
        )
        assert strict["confidence"] == "needs_verification"


def test_partial_apply_url_and_permit_type_need_specific_action_language(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    vague_online = server.enforce_strict_field_confidence(
        "apply_url",
        "https://dallas.gov/building/permits/start",
        [{
            "source_url": "https://dallas.gov/building/permits/start",
            "source_title": "Dallas Building Permits",
            "quoted_snippet": "Visit our online resource center for permit information.",
            "source_type": "official_ahj",
            "supports_field": True,
            "confidence_signal": "partial",
            "last_verified": "2026-05-02",
        }],
    )
    assert vague_online["confidence"] == "needs_verification"

    direct_apply = server.enforce_strict_field_confidence(
        "apply_url",
        "https://dallas.gov/building/permits/start",
        [{
            "source_url": "https://dallas.gov/building/permits/start",
            "source_title": "Dallas Building Permits",
            "quoted_snippet": "Submit a permit application through the online portal.",
            "source_type": "official_ahj",
            "supports_field": True,
            "confidence_signal": "partial",
            "last_verified": "2026-05-02",
        }],
    )
    assert direct_apply["confidence"] == "partial"

    review_only = server.enforce_strict_field_confidence(
        "permit_type",
        "Building Permit — Tenant Improvement",
        [{
            "source_url": "https://dallas.gov/building/review",
            "source_title": "Dallas Plan Review",
            "quoted_snippet": "Plans go through staff review before issuance.",
            "source_type": "official_ahj",
            "supports_field": True,
            "confidence_signal": "partial",
            "last_verified": "2026-05-02",
        }],
    )
    assert review_only["confidence"] == "needs_verification"


def test_claim_citations_sync_legacy_field_confidence_and_aliases(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "confidence": "high",
        "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Medical Clinic Interior Alteration"}],
        "apply_url": "https://www.miamidade.gov/Apps/RER/EPSPortal",
        "fee_range": "$12,500+ — verify before quoting",
        "approval_timeline": "4-12 weeks; verify with AHJ",
        "inspections": ["Building rough/final"],
        "field_confidence": {
            "fees": "high",
            "fee_range": "high",
            "timeline": "high",
            "portal_url": "low",
        },
        "field_evidence": {
            "apply_url": [{
                "source_url": "https://www.miamidade.gov/global/economy/building/online-services.page",
                "source_title": "Miami-Dade Building Online Services",
                "quoted_snippet": "Permit Submission Portal for building permit applications: submit and check the status of permit applications.",
                "source_type": "official_ahj",
                "supports_field": True,
                "confidence_signal": "high",
                "last_verified": "2026-05-02",
            }]
        },
    }

    citations = server.build_claim_citations(result)
    by_field = {c["field"]: c for c in citations}

    assert by_field["apply_url"]["confidence"] == "high"
    assert by_field["fee_range"]["confidence"] == "needs_verification"
    assert result["field_confidence"]["apply_url"] == "high"
    assert result["field_confidence"]["portal_url"] == "high"
    assert result["field_confidence"]["fee_range"] == "needs_verification"
    assert result["field_confidence"]["fees"] == "needs_verification"
    assert result["field_confidence"]["approval_timeline"] == "needs_verification"
    assert result["field_confidence"]["timeline"] == "needs_verification"


def test_claim_citations_sync_clears_stale_legacy_highs_when_fields_absent(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "apply_url": "https://dallas.gov/building/permits/start",
        "field_confidence": {
            "permit_type": "high",
            "fee_range": "high",
            "fees": "high",
            "approval_timeline": "high",
            "timeline": "high",
            "inspections": "high",
        },
        "field_evidence": {
            "apply_url": [{
                "source_url": "https://dallas.gov/building/permits/start",
                "source_title": "Dallas Building Permits",
                "quoted_snippet": "Submit a permit application through the online portal.",
                "source_type": "official_ahj",
                "supports_field": True,
                "confidence_signal": "high",
                "last_verified": "2026-05-02",
            }]
        },
    }

    server.build_claim_citations(result)

    assert result["field_confidence"]["apply_url"] == "high"
    assert result["field_confidence"]["permit_type"] == "needs_verification"
    assert result["field_confidence"]["fee_range"] == "needs_verification"
    assert result["field_confidence"]["fees"] == "needs_verification"
    assert result["field_confidence"]["approval_timeline"] == "needs_verification"
    assert result["field_confidence"]["timeline"] == "needs_verification"
    assert result["field_confidence"]["inspections"] == "needs_verification"


def test_verified_tier_matrix_evaluates_claim_citation_readiness():
    matrix = _import_matrix()
    case = next(c for c in matrix.VERIFIED_TIER_MATRIX if c.case_id == "fl_miami_dade_dental_clinic_ti_gold")
    result = {
        "claim_citations": [
            {"field": "permit_type", "confidence": "partial"},
            {"field": "apply_url", "confidence": "high"},
            {"field": "fee_range", "confidence": "needs_verification"},
            {"field": "approval_timeline", "confidence": "needs_verification"},
            {"field": "inspections", "confidence": "needs_verification"},
        ],
        "quality_warnings": ["Fees, timelines, and inspections need AHJ verification."],
    }

    evaluation = matrix.evaluate_case_result(case, result)

    assert evaluation["passed"] is True
    assert evaluation["actual_field_readiness"]["apply_url"] == "verified"
    assert evaluation["actual_field_readiness"]["permit_type"] == "partial"
    assert evaluation["mismatches"] == {}


def test_verified_tier_matrix_reports_field_and_warning_mismatches():
    matrix = _import_matrix()
    case = next(c for c in matrix.VERIFIED_TIER_MATRIX if c.case_id == "fl_miami_dade_dental_clinic_ti_gold")
    result = {
        "claim_citations": [
            {"field": "permit_type", "confidence": "partial"},
            {"field": "apply_url", "confidence": "medium"},
            {"field": "fee_range", "confidence": "needs_verification"},
            {"field": "approval_timeline", "confidence": "needs_verification"},
            {"field": "inspections", "confidence": "needs_verification"},
        ],
        "quality_warnings": [],
    }

    evaluation = matrix.evaluate_case_result(case, result)

    assert evaluation["passed"] is False
    assert evaluation["mismatches"]["apply_url"] == {"expected": "verified", "actual": "partial"}
    assert evaluation["mismatches"]["warnings_visible"] == {"expected": True, "actual": False}
