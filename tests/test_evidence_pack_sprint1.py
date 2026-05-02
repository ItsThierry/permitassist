from pathlib import Path
from importlib import util

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_import_server = _debug_helper._import_server


def test_verified_tier_matrix_skeleton_has_required_fields_and_target_verticals():
    matrix_spec = util.spec_from_file_location(
        "verified_tier_matrix",
        Path(__file__).resolve().parents[1] / "api" / "verified_tier_matrix.py",
    )
    matrix = util.module_from_spec(matrix_spec)
    matrix_spec.loader.exec_module(matrix)

    matrix.validate_verified_tier_matrix()
    summary = matrix.matrix_summary()

    assert summary["case_count"] >= 12
    assert set(summary["fields"]) == {
        "permit_type",
        "apply_url",
        "fee_range",
        "approval_timeline",
        "inspections",
    }
    assert summary["by_vertical"]["restaurant_ti"] >= 3
    assert summary["by_vertical"]["medical_clinic_ti"] >= 4
    assert summary["by_vertical"]["office_ti"] >= 3
    assert set(matrix.CORE_EVIDENCE_FIELDS) == set(summary["fields"])


def test_strict_validator_rejects_high_without_field_specific_quote(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    strict = server.enforce_strict_field_confidence(
        "fee_range",
        "$1,000-$2,000",
        [{
            "source_url": "https://example.gov/permits",
            "source_title": "Permit Portal",
            "quoted_snippet": "Apply online for building permits.",
            "source_type": "official_ahj",
            "supports_field": True,
            "confidence_signal": "high",
            "last_verified": "2026-05-02",
        }],
    )

    assert strict["confidence"] == "needs_verification"
    assert strict["needs_review"] is True
    assert "fee" in strict["warning"]


def test_official_source_check_rejects_substring_fake_gov_and_us_hosts(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    for fake_url in (
        "https://agovernor.com/permit-portal",
        "https://abus.com/permit-portal",
        "https://status.com/permit-portal",
        "https://permits.fakeus.us/permit-portal",
    ):
        strict = server.enforce_strict_field_confidence(
            "apply_url",
            fake_url,
            [{
                "source_url": fake_url,
                "source_title": "Fake Permit Portal",
                "quoted_snippet": "Start a building permit application and submit online through the portal.",
                "source_type": "webpage",
                "supports_field": True,
                "confidence_signal": "high",
                "last_verified": "2026-05-02",
            }],
        )

        assert strict["confidence"] == "needs_verification"
        assert strict["needs_review"] is True

    unverified_source_type = server.enforce_strict_field_confidence(
        "apply_url",
        "https://example.com/permit-portal",
        [{
            "source_url": "https://example.com/permit-portal",
            "source_title": "Unverified Permit Portal",
            "quoted_snippet": "Start a building permit application and submit online through the portal.",
            "source_type": "official_ahj_unverified",
            "supports_field": True,
            "confidence_signal": "high",
            "last_verified": "2026-05-02",
        }],
    )
    assert unverified_source_type["confidence"] == "needs_verification"


def test_claim_citations_use_field_specific_evidence_not_one_generic_snippet(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "confidence": "high",
        "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Medical Clinic Interior Alteration"}],
        "apply_url": "https://www.miamidade.gov/Apps/RER/EPSPortal",
        "fee_range": "$12,500-$12,500+ — verify against current fee schedule before quoting",
        "approval_timeline": "4-12 weeks; verify with AHJ",
        "inspections": ["Building rough/final", "MEP rough/final"],
        "sources": [{
            "url": "https://www.miamidade.gov/global/economy/building/online-services.page",
            "title": "Miami-Dade Building Online Services",
            "snippet": "Building Online Services lists the Permit Submission Portal for building permit applications.",
        }],
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
    assert "Permit Submission Portal" in by_field["apply_url"]["quoted_snippet"]
    assert by_field["fee_range"]["confidence"] == "needs_verification"
    assert by_field["fee_range"]["quoted_snippet"] == ""
    assert by_field["approval_timeline"]["confidence"] == "needs_verification"
    assert by_field["inspections"]["confidence"] == "needs_verification"
    assert any("quoted source snippets for their specific fields" in w for w in result["quality_warnings"])
    assert result["needs_review"] is True


def test_miami_dade_gold_backfills_apply_url_field_evidence_only(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Medical Clinic Interior Alteration"}],
        "apply_url": "https://www.miamidade.gov/global/economy/building/building-permit-fees.page",
        "sources": ["https://www.miamidade.gov/global/economy/building/building-permit-fees.page"],
        "fee_range": "$12,500-$12,500+ — verify against current fee schedule before quoting",
        "approval_timeline": {"complex": "4-12 weeks; verify with AHJ"},
        "inspections": ["Building rough/final", "MEP rough/final"],
    }

    server.build_apply_path(result, "dental clinic tenant improvement with x-ray and nitrous", "Miami", "FL")
    citations = server.build_claim_citations(result)
    by_field = {c["field"]: c for c in citations}

    assert result["apply_url"] == "https://www.miamidade.gov/Apps/RER/EPSPortal"
    assert result["field_evidence"]["apply_url"][0]["source_type"] == "official_ahj"
    assert result["field_evidence"]["apply_url"][0]["supports_field"] is True
    assert by_field["apply_url"]["confidence"] == "high"
    assert by_field["fee_range"]["confidence"] == "needs_verification"
    assert by_field["approval_timeline"]["confidence"] == "needs_verification"
    assert by_field["inspections"]["confidence"] == "needs_verification"
    assert any("fee/info page was not treated" in w for w in result["quality_warnings"])
