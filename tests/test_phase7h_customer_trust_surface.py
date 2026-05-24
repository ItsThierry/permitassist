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
