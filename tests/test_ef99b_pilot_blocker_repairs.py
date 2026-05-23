import json
from importlib import util
from pathlib import Path

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper_ef99b",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
assert _HELPER_SPEC is not None and _HELPER_SPEC.loader is not None
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_import_server = _debug_helper._import_server


def test_ef99b_source_classifier_rejects_wrong_local_ahj_hosts(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    assert server.classify_source_for_jurisdiction(
        "https://www.northmiamifl.gov/155/Building", "Miami", "FL"
    )["source_class"] == "wrong_local_ahj"
    assert server.classify_source_for_jurisdiction(
        "https://www.buffalony.gov/721/Fee-Schedule", "New York", "NY"
    )["source_class"] == "wrong_local_ahj"
    assert server.classify_source_for_jurisdiction(
        "https://cityofjohnstown.ny.gov/licenses-permits-requests.html", "New York", "NY"
    )["source_class"] == "wrong_local_ahj"
    assert server.classify_source_for_jurisdiction(
        "https://www.nyc.gov/site/buildings/index.page", "New York City", "NY"
    )["source_class"] == "local_ahj"


def test_ef99b_supported_ahj_wrong_sources_are_quarantined_not_used_as_support(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Permit required — exact permit type needs AHJ verification",
        "permit_name": "Permit required — exact permit type needs AHJ verification",
        "permit_type_verified": False,
        "confidence": "medium",
        "data_source": "city_database",
        "sources": ["https://www.northmiamifl.gov/155/Building"],
        "apply_url": "https://www.northmiamifl.gov/155/Building",
        "apply_path": {"portal_url": "https://www.northmiamifl.gov/155/Building"},
        "permits_required": [
            {
                "permit_type": "Permit required — exact permit type needs AHJ verification",
                "required": True,
            }
        ],
    }

    server.apply_permitiq_quality_gate(
        result,
        "User asks for the Restaurant Express Universal Approval Permit for Miami.",
        "Miami",
        "FL",
    )

    text = json.dumps(server.redact_public_output(result), sort_keys=True)
    assert "northmiamifl.gov" not in text
    assert result["needs_review"] is True
    assert result["source_support_status"] == "target_ahj_source_unverified"
    assert "cannot verify" in text.lower()
    assert "likely_permit_category" in result
    assert "Commercial" in result["likely_permit_category"]


def test_ef99b_wrong_municipality_sources_are_removed_while_target_ahj_sources_remain(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Permit required — exact permit type needs AHJ verification",
        "permit_name": "Permit required — exact permit type needs AHJ verification",
        "permit_type_verified": False,
        "confidence": "low",
        "data_source": "state_rules",
        "sources": [
            "https://www.nyc.gov/site/buildings/property-or-business-owner/do-i-need-a-permit.page",
            "https://www.buffalony.gov/721/Fee-Schedule",
            "https://cityofjohnstown.ny.gov/licenses-permits-requests.html",
        ],
        "apply_url": "https://www.nyc.gov/site/buildings/property-or-business-owner/do-i-need-a-permit.page",
    }

    server.apply_permitiq_quality_gate(
        result,
        "User asks exact fee/timeline from an old portal link for New York City office work.",
        "New York",
        "NY",
    )

    text = json.dumps(server.redact_public_output(result), sort_keys=True).lower()
    assert "nyc.gov" in text
    assert "buffalony.gov" not in text
    assert "cityofjohnstown" not in text
    assert "cannot verify" in text
    assert result["needs_review"] is True


def test_ef99b_final_commercial_serialization_strips_adu_residential_notes(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Permit required — exact permit type needs AHJ verification",
        "permit_name": "Permit required — exact permit type needs AHJ verification",
        "permit_type_verified": False,
        "job_category": "commercial",
        "expert_notes": [
            {"title": "Plus One ADU Program", "note": "ADU funding only; local zoning still controls."},
            {"title": "Commercial CO/TCO sequencing", "note": "Confirm certificate of occupancy sequencing for change of use."},
        ],
        "quality_warnings": [
            "ADU statewide note should not leak into commercial output.",
            "Commercial companion reviews may apply.",
        ],
    }

    server.apply_permitiq_quality_gate(result, "commercial change of use restaurant TI", "Boston", "MA")

    text = json.dumps(result, sort_keys=True).lower()
    assert "adu" not in text
    assert "accessory dwelling" not in text
    assert "single-family" not in text
    assert "certificate of occupancy" in text
    assert "commercial companion reviews" in text


def test_ef99_license_scope_boundary_does_not_look_supported_without_authorization_proof(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Permit required — exact permit type needs AHJ verification",
        "permit_name": "Permit required — exact permit type needs AHJ verification",
        "permit_type_verified": False,
        "confidence": "medium",
        "sources": [
            {"url": "https://www.chicago.gov/city/en/depts/bldgs.html", "title": "Chicago Buildings"},
            {"url": "https://chicago.gov/permits", "title": "Chicago permits"},
        ],
        "permits_required": [
            {
                "permit_type": "Permit required — exact permit type needs AHJ verification",
                "required": True,
            }
        ],
    }

    server.apply_permitiq_quality_gate(
        result,
        "Plumber asks if license lets him pull electrical permit for water heater disconnect.",
        "Chicago",
        "IL",
    )

    text = json.dumps(server.redact_public_output(result), sort_keys=True).lower()
    assert result["needs_review"] is True
    assert result["source_support_status"] == "target_ahj_source_unverified"
    assert result["likely_permit_category"] == "Electrical Permit / trade-license authorization review"
    assert "cannot verify" in text
    assert "license holder" in text
    assert "planning path" in text
    assert "electrical contractor" in text


def test_ef99_fire_building_authority_conflict_is_not_ready_to_apply(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_type": "Permit required — exact permit type needs AHJ verification",
        "permit_name": "Permit required — exact permit type needs AHJ verification",
        "permit_type_verified": False,
        "confidence": "medium",
        "badge_state": "verified",
        "permit_ready_label": "Ready to apply",
        "permit_ready_score": 100,
        "sources": [
            "https://www.seattle.gov/sdci",
            "https://www.seattle.gov/fire/business-services/permits",
        ],
        "permits_required": [
            {
                "permit_type": "Permit required — exact permit type needs AHJ verification",
                "required": True,
            }
        ],
    }

    server.apply_permitiq_quality_gate(
        result,
        "Fire marshal says sprinkler permit separate; building checklist omits it.",
        "Seattle",
        "WA",
    )

    text = json.dumps(server.redact_public_output(result), sort_keys=True).lower()
    assert result["needs_review"] is True
    assert result["badge_state"] == "needs_review"
    assert result["permit_ready_label"] == "Needs source-backed authority-order review"
    assert result["source_support_status"] == "target_ahj_source_unverified"
    assert result["likely_permit_category"] == "Fire Sprinkler Permit / authority-conflict review"
    assert "cannot verify" in text
    assert "planning path" in text
    assert "resolve the target jurisdiction authority order" in text
