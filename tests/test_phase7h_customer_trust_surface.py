import json
from importlib import util
from pathlib import Path

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper_phase7h_customer_trust",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_import_server = _debug_helper._import_server

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_INDEX = REPO_ROOT / "frontend" / "index.html"


def test_phase7h_verified_permit_type_populates_visible_required_permits(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Commercial Alteration Permit",
        "permit_name": "Commercial Alteration Permit",
        "permits_required": [],
        "_evidence_pack": {
            "enabled": True,
            "matched_fields": ["permit_type", "apply_url"],
            "failed_closed_fields": ["fee_range", "inspections"],
        },
        "coverage_truth": {
            "heading": "Coverage scope",
            "official_source_backed": ["permit type", "apply URL / route"],
            "not_confirmed_from_official_source": ["fees", "timelines"],
        },
    }

    server.ensure_customer_visible_permit_trust_statement(result, "Chicago", "IL")

    assert result["permit_type"] == "Commercial Alteration Permit"
    assert result["permit_name"] == "Commercial Alteration Permit"
    assert result["permits_required"] == [
        {
            "permit_type": "Commercial Alteration Permit",
            "required": True,
            "notes": "Official-source backed permit type. Verify final filing path with the AHJ before submitting.",
        }
    ]
    assert result.get("permit_type_verified") is True


def test_phase7h_yes_verdict_without_verified_permit_type_gets_safe_customer_statement(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": None,
        "permit_name": "",
        "permits_required": [],
        "apply_phone": "(312) 744-3449",
        "applying_office": "Chicago Department of Buildings",
        "apply_path": {"verification_note": "Use the AHJ portal/contact to confirm exact filing category."},
        "_evidence_pack": {
            "enabled": True,
            "matched_fields": ["apply_url"],
            "failed_closed_fields": ["permit_type", "fee_range", "inspections"],
        },
        "coverage_truth": {
            "heading": "Coverage scope",
            "official_source_backed": ["permit type", "apply URL / route"],
            "not_confirmed_from_official_source": ["fees", "timelines"],
        },
    }

    server.ensure_customer_visible_permit_trust_statement(result, "Chicago", "IL")

    statement = "Permit required — exact permit type needs AHJ verification"
    assert result["permit_type"] == statement
    assert result["permit_name"] == statement
    assert result["permit_type_verified"] is False
    assert result["permits_required"] == [
        {
            "permit_type": statement,
            "required": True,
            "notes": "Permit is required, but the exact permit category was not official-source verified for this lookup. Confirm the exact filing type with Chicago Department of Buildings or call (312) 744-3449 before filing.",
        }
    ]
    assert "permit type" not in result["coverage_truth"]["official_source_backed"]
    assert "exact permit type" in result["coverage_truth"]["not_confirmed_from_official_source"]
    text = json.dumps(result, sort_keys=True)
    assert "exact permit type needs AHJ verification" in text
    assert "not official-source verified" in text
    assert "Chicago Department of Buildings" in text


def test_phase7h_public_redaction_keeps_trust_statement_but_drops_internal_evidence_fields(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": None,
        "permits_required": [],
        "apply_phone": "(832) 394-8880",
        "applying_office": "Houston Permitting Center",
        "field_evidence_confidence": "high",
        "raw_path": "/home/boban/private/raw-response.json",
        "_evidence_pack": {
            "enabled": True,
            "mode": "future_registry_pack_preview",
            "public_redaction": "drop_evidence_pack",
            "matched_fields": ["apply_url"],
            "failed_closed_fields": ["permit_type"],
            "fingerprint_sha256": "a" * 64,
        },
    }

    server.ensure_customer_visible_permit_trust_statement(result, "Houston", "TX")
    public = server.redact_public_output(result)
    text = json.dumps(public, sort_keys=True)

    assert public["permit_type"] == "Permit required — exact permit type needs AHJ verification"
    assert public["permits_required"][0]["required"] is True
    for forbidden in (
        "_evidence_pack",
        "field_evidence_confidence",
        "evidence_pack",
        "fingerprint",
        "/home/boban",
        "raw-response",
    ):
        assert forbidden not in text


def test_phase7h_frontend_does_not_call_unverified_placeholder_an_exact_permit():
    source = FRONTEND_INDEX.read_text(encoding="utf-8")

    assert "permitTypeNeedsAhjVerify" in source
    assert "Confirm exact permit category with AHJ" in source
    assert "Ask the AHJ which permit/category to file for this scope" in source


def test_phase7h_official_category_alone_does_not_verify_exact_permit_name(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Commercial Alteration Permit",
        "permit_name": "Commercial Alteration Permit",
        "_permit_display_name": "Commercial Alteration Permit",
        "permits_required": [{"permit_type": "Commercial Alteration Permit", "required": True}],
        "apply_path": {
            "permit_type": "Commercial Alteration Permit",
            "application_steps": ["Choose Commercial Alteration Permit in the portal."],
        },
        "_evidence_pack": {
            "enabled": True,
            "matched_fields": ["official_application_category", "apply_url"],
            "failed_closed_fields": [],
            "permit_name_source_field": "official_application_category",
        },
    }

    server.ensure_customer_visible_permit_trust_statement(result, "Chicago", "IL", "restaurant tenant improvement")

    statement = "Permit required — exact permit type needs AHJ verification"
    assert result["permit_type"] == statement
    assert result["permit_name"] == statement
    assert result["_permit_display_name"] == statement
    assert result["permit_type_verified"] is False
    assert result["permits_required"][0]["permit_type"] == statement
    assert result["apply_path"]["permit_type"] == statement
    assert "Commercial Alteration Permit" not in json.dumps(result.get("apply_path"), sort_keys=True)


def test_phase7h_non_evidence_city_source_exact_name_is_neutralized(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Building Permit",
        "permit_name": "Building Permit",
        "data_source": "city",
        "_meta": {"city_match_level": "city"},
        "permits_required": [{"permit_type": "Building Permit", "portal_selection": "Building Permit", "required": True}],
        "apply_path": {
            "permit_type": "Building Permit",
            "application_steps": ["Select Building Permit, then upload drawings."],
        },
    }

    server.ensure_customer_visible_permit_trust_statement(result, "Austin", "TX", "office tenant improvement")

    statement = "Permit required — exact permit type needs AHJ verification"
    text = json.dumps(result, sort_keys=True)
    assert result["permit_type"] == statement
    assert result["permit_name"] == statement
    assert result["permit_type_verified"] is False
    assert result["permits_required"][0]["permit_type"] == statement
    assert result["permits_required"][0]["portal_selection"] == statement
    assert "Building Permit" not in text
    assert "not official-source verified" in text


def test_ef04_build_claim_citations_omits_claims_without_quoted_snippets(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Commercial Alteration Permit",
        "permits_required": [{"permit_type": "Commercial Alteration Permit", "required": True}],
        "apply_url": "https://chicago.gov/permits",
        "fee_range": "$500-$1,000",
        "confidence": "high",
        "sources": [{"url": "https://chicago.gov/permits", "title": "Chicago Permits", "snippet": ""}],
    }

    citations = server.build_claim_citations(result)

    assert citations == []
    assert result["claim_citations"] == []
    assert [claim["field"] for claim in result["unverified_claims"]] == ["permit_type", "apply_url", "fee_range"]
    assert all(claim["confidence"] == "needs_verification" for claim in result["unverified_claims"])
    assert result["confidence"] == "medium"
    assert result["needs_review"] is True
    assert "not shown as verified" in " ".join(result["quality_warnings"])


def test_ef04_build_claim_citations_keeps_only_nonempty_quoted_support(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Commercial Alteration Permit",
        "permits_required": [{"permit_type": "Commercial Alteration Permit", "required": True}],
        "confidence": "medium",
        "sources": [
            {"url": "https://chicago.gov/empty", "title": "Empty", "snippet": "   "},
            {"url": "https://chicago.gov/permit-type", "title": "Permit Type", "snippet": "Alteration work requires a building permit."},
        ],
    }

    citations = server.build_claim_citations(result)

    assert len(citations) == 1
    assert citations[0]["field"] == "permit_type"
    assert citations[0]["source_url"] == "https://chicago.gov/permit-type"
    assert citations[0]["quoted_snippet"] == "Alteration work requires a building permit."
    assert "unverified_claims" not in result


def test_ef04_report_renderer_does_not_recreate_unverified_claim_citations(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Permit required — exact permit type needs AHJ verification",
        "permit_name": "Permit required — exact permit type needs AHJ verification",
        "permit_type_verified": False,
        "permits_required": [{"permit_type": "Permit required — exact permit type needs AHJ verification", "required": True}],
        "claim_citations": [],
        "unverified_claims": [{"field": "permit_type", "confidence": "needs_verification"}],
        "sources": [{"url": "https://chicago.gov/permits", "title": "Chicago Permits", "snippet": ""}],
    }

    html = server.render_white_label_report_html({
        "result": result,
        "job_type": "restaurant tenant improvement",
        "city": "Chicago",
        "state": "IL",
    })

    assert result["claim_citations"] == []
    assert "Source quote not attached for this field yet" not in html
    assert "No citations attached yet" in html
