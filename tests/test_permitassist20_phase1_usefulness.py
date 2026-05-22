import json
from importlib import util
from pathlib import Path

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper_permitassist20_phase1",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_import_server = _debug_helper._import_server

SAFE_INTERIM_PERMIT_LABEL = "Manual filing path confirmation in progress"


def _base_yes_result(**overrides):
    result = {
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_type": "Commercial Alteration Permit",
        "permit_name": "Commercial Alteration Permit",
        "permits_required": [
            {
                "permit_type": "Commercial Alteration Permit",
                "portal_selection": "Commercial Alteration Permit",
                "required": True,
            }
        ],
        "applying_office": "Phoenix Planning and Development Department",
        "apply_url": "https://www.phoenix.gov/pdd/permits",
        "apply_path": {
            "portal": "ShapePHX",
            "permit_type": "Commercial Alteration Permit",
            "application_steps": ["Open ShapePHX.", "Choose the commercial alteration filing path."],
        },
        "docs_required": ["Construction plans", "Electrical one-line diagram"],
        "companion_reviews_triggers": "Electrical service upgrade review and battery/ESS fire review may be required for this scope.",
        "inspections": ["Rough electrical inspection", "Final electrical inspection"],
        "sources": [
            {
                "url": "https://www.phoenix.gov/pdd/permits",
                "title": "Phoenix Planning and Development permits",
                "snippet": "Permits and inspections are managed through Planning and Development.",
            }
        ],
        "warnings": ["Confirm final filing category before submitting."],
    }
    result.update(overrides)
    return result


def _server(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda url, timeout=4: True)
    return server


def test_phase1_usefulness_contract_sets_order_with_caveat_last(tmp_path, monkeypatch):
    server = _server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(
        _base_yes_result(),
        "commercial solar PV battery energy storage 400 amp service upgrade",
        "Phoenix",
        "AZ",
        evidence_allowed=False,
    )

    assert result["_render_order"][:3] == ["ruling", "exact_filing_path", "portal_selection_or_ask"]
    assert result["_render_order"][-1] == "caveat"
    ordered_slots = [slot["key"] for slot in result["_usefulness"]["ordered_slots"]]
    assert ordered_slots == result["_render_order"]


def test_phase1_usefulness_scores_neutralized_yes_as_partial_not_zero(tmp_path, monkeypatch):
    server = _server(tmp_path, monkeypatch)
    result = _base_yes_result(
        permit_type=None,
        permit_name="",
        permits_required=[],
        _evidence_pack={
            "enabled": True,
            "matched_fields": ["apply_url", "inspections", "companion_reviews_triggers"],
            "failed_closed_fields": ["permit_type"],
        },
    )

    result = server.finalize_permit_lookup_result(
        result,
        "commercial solar PV battery energy storage 400 amp service upgrade",
        "Phoenix",
        "AZ",
        evidence_allowed=False,
    )

    assert result["permit_type"] == SAFE_INTERIM_PERMIT_LABEL
    assert result["permit_type_verified"] is False
    usefulness = result["_usefulness"]
    assert usefulness["score"] >= 5
    slot_status = {slot["key"]: slot["status"] for slot in usefulness["ordered_slots"]}
    assert slot_status["ruling"] == "present_exact"
    assert slot_status["exact_filing_path"] in {"present_exact", "present_safe_interim"}
    assert slot_status["office_or_portal"] == "present_exact"
    assert slot_status["caveat"] == "caveat_last"


def test_phase1_source_backed_category_keeps_filing_path_visible_without_claiming_exact_name(tmp_path, monkeypatch):
    server = _server(tmp_path, monkeypatch)
    result = _base_yes_result(
        permit_type="Commercial Alteration Permit",
        permit_name="Commercial Alteration Permit",
        _permit_display_name="Commercial Alteration Permit",
        _evidence_pack={
            "enabled": True,
            "matched_fields": ["official_application_category", "apply_url"],
            "failed_closed_fields": [],
            "permit_name_source_field": "official_application_category",
        },
    )

    server.ensure_customer_visible_permit_trust_statement(result, "Chicago", "IL", "restaurant tenant improvement")

    assert result["permit_type"] == "Commercial Alteration Permit"
    assert result["permit_name"] == "Commercial Alteration Permit"
    assert result["permit_type_verified"] is False
    assert result["apply_path"]["permit_type"] == "Commercial Alteration Permit"
    assert result["permits_required"][0]["portal_selection"] == "Commercial Alteration Permit"
    assert "Manual filing path check is in progress" in result["apply_path"]["verification_note"]


def test_phase1_caveat_only_answer_is_marked_low_usefulness(tmp_path, monkeypatch):
    server = _server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_type": None,
        "permit_name": "",
        "permits_required": [],
        "warnings": ["Manual verification required."],
        "_evidence_pack": {"enabled": True, "matched_fields": [], "failed_closed_fields": ["permit_type", "apply_url", "inspections"]},
    }

    result = server.finalize_permit_lookup_result(
        result,
        "commercial tenant improvement",
        "Long Tail City",
        "CA",
        evidence_allowed=False,
    )

    usefulness = result["_usefulness"]
    assert usefulness["score"] <= 2, json.dumps(usefulness, sort_keys=True)
    assert usefulness["release_gate"] == "fail"
    usefulness_warnings = [w for w in result.get("warnings", []) if "contractor operating packet is incomplete" in str(w).lower()]
    assert len(usefulness_warnings) == 1


def test_phase1_loose_apply_path_support_level_does_not_count_as_exact_filing_grade(tmp_path, monkeypatch):
    server = _server(tmp_path, monkeypatch)

    result = {
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_type_verified": False,
        "permit_type": SAFE_INTERIM_PERMIT_LABEL,
        "permit_name": SAFE_INTERIM_PERMIT_LABEL,
        "apply_path": {
            "permit_type": "Commercial Alteration Permit",
            "support_level": "see notes",
            "verification_note": "Manual filing path check is in progress for this lookup.",
        },
        "permits_required": [
            {
                "permit_type": "Commercial Alteration Permit",
                "portal_selection": "Commercial Alteration Permit",
                "required": True,
            }
        ],
    }

    from usefulness_contract import score_result

    usefulness = score_result(result)
    slots = {slot["key"]: slot for slot in usefulness["ordered_slots"]}
    assert slots["exact_filing_path"]["status"] == "present_safe_interim"
    assert slots["exact_filing_path"]["points"] == 0


def test_phase1_no_permit_required_result_does_not_get_manual_completion_warning(tmp_path, monkeypatch):
    server = _server(tmp_path, monkeypatch)
    result = server.finalize_permit_lookup_result(
        {
            "permit_verdict": "NO",
            "permit_required": False,
            "permit_type": "No permit required",
            "permit_name": "No permit required",
            "warnings": [],
            "sources": [
                {
                    "url": "https://example.gov/minor-work",
                    "title": "Minor work exemptions",
                    "snippet": "Minor cosmetic work does not require a building permit.",
                }
            ],
        },
        "paint existing office walls with no electrical plumbing or structural work",
        "Phoenix",
        "AZ",
        evidence_allowed=False,
    )

    warnings_text = "\n".join(str(w) for w in result.get("warnings", []))
    assert "Contractor operating packet is incomplete" not in warnings_text
    assert result["_usefulness"]["ordered_slots"][0]["evidence"] == "Permit not required"


def test_phase1_static_benchmark_rubric_phoenix_solar_beats_generic_chatbot_baseline(tmp_path, monkeypatch):
    server = _server(tmp_path, monkeypatch)
    from api.usefulness_contract import score_against_static_baseline

    permitassist = server.finalize_permit_lookup_result(
        _base_yes_result(),
        "commercial solar PV battery energy storage 400 amp service upgrade",
        "Phoenix",
        "AZ",
        evidence_allowed=False,
    )
    generic_baseline = "You likely need a permit for solar and electrical work. Check with Phoenix before starting."

    comparison = score_against_static_baseline(
        permitassist,
        generic_baseline,
        job_type="commercial solar PV battery energy storage 400 amp service upgrade",
        city="Phoenix",
        state="AZ",
    )

    assert comparison["permitassist_score"] >= comparison["baseline_score"] + 2
    assert comparison["winner"] == "permitassist"
    assert not comparison["losses"]
