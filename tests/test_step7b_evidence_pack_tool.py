import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.evidence_pack_tool import (
    build_evidence_pack,
    discover_step6a_artifacts,
    fail_closed_decision,
    write_evidence_pack_outputs,
)


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _artifact(batch_id="batch-test", *, row_status="verified", scope_limit="Statewide trigger only; no local AHJ routing, fees, timelines, apply URL, permit type, or inspections verified."):
    return {
        "title": "fixture",
        "batch_id": batch_id,
        "created_utc": "2026-05-05T12:00:00Z",
        "status": "PASS_ACCEPTED_AFTER_INDEPENDENT_REVIEW",
        "accepted_upgrades": [
            {
                "row_id": "fixture-ca-irvine-medical-companion",
                "state": "CA",
                "ahj_name": "City of Irvine",
                "vertical": "medical_clinic_ti",
                "field": "companion_reviews_triggers",
                "old_status": "partial",
                "proposed_status": row_status,
                "claim_value_after": "Conditional statewide healthcare review warning only.",
                "source_scope_limit": scope_limit,
                "checked_at_utc": "2026-05-05T12:01:00Z",
                "evidence": [
                    {
                        "source_url": "https://hcai.ca.gov/facilities/building-safety/building-and-construction-projects/plan-review-programs/",
                        "source_title": "HCAI Plan Review Programs",
                        "source_type": "official_state_healthcare",
                        "exact_quote": "Prior to requesting OTC review, all construction documents and completed project applications must be submitted to OSHPD for triage.",
                        "quote_found_current_run": True,
                        "normalized_source_text_sha256": "a" * 64,
                        "normalized_source_text_length": 12345,
                        "validation_method": "fixture normalized text",
                        "fetch_status_code": 200,
                    }
                ],
            }
        ],
    }


def test_build_evidence_pack_preserves_field_level_evidence_and_scope_limit(tmp_path):
    artifact_path = _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch99-evidence-upgrades-2026-05-05.json", _artifact())

    pack = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")

    assert pack["metadata"]["evidence_pack_version"] == "step7b_offline_v1"
    assert pack["metadata"]["fingerprint_sha256"]
    assert pack["metadata"]["production_wiring_allowed"] is False
    assert pack["metadata"]["source_freshness_policy"]["default_reverify_after_days"] == 30
    assert pack["metadata"]["fail_closed_policy"]["403"] == "downgrade_to_needs_verification_until_revalidated"

    assert len(pack["records"]) == 1
    record = pack["records"][0]
    assert record["source_scope_limit"] == _artifact()["accepted_upgrades"][0]["source_scope_limit"]
    assert record["display_contract"]["must_display_scope_limit"] is True
    assert record["display_contract"]["forbids_local_ahj_certainty"] is True
    assert record["field_status"] == "verified"
    assert record["confidence"] == "high"

    item = record["field_evidence"][0]
    assert item["source_url"].startswith("https://hcai.ca.gov/")
    assert item["source_title"] == "HCAI Plan Review Programs"
    assert item["exact_quote_or_snippet"].startswith("Prior to requesting OTC review")
    assert item["quote_found"] is True
    assert item["normalized_source_text_sha256"] == "a" * 64
    assert item["normalized_source_text_length"] == 12345
    assert item["last_verified_utc"] == "2026-05-05T12:01:00Z"

    assert pack["validation"]["verdict"] == "PASS_OFFLINE_READY"
    required_record_fields = pack["validation"]["ingestion_regression_contract"]["required_record_fields"]
    assert required_record_fields
    assert "source_scope_limit_generated" in required_record_fields
    assert record["source_scope_limit_generated"] is False


def test_missing_scope_limit_or_quote_fails_closed_before_ingestion(tmp_path):
    payload = _artifact(scope_limit="")
    payload["accepted_upgrades"][0]["evidence"][0]["exact_quote"] = ""
    payload["accepted_upgrades"][0]["evidence"][0]["quote_found_current_run"] = False
    artifact_path = _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch98-evidence-upgrades-2026-05-05.json", payload)

    pack = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")

    assert pack["validation"]["verdict"] == "FAIL_CLOSED_NOT_INGESTION_READY"
    record = pack["records"][0]
    assert record["ingestion_ready"] is False
    assert record["field_status"] == "needs_verification"
    assert record["confidence"] == "needs_verification"
    assert "missing_source_scope_limit" not in record["validation_errors"]
    assert record["source_scope_limit_generated"] is True
    assert "Does not verify" in record["source_scope_limit"]
    assert "missing_field_level_quote_or_snippet" in record["validation_errors"]


def test_official_source_runtime_failures_are_defined_as_fail_closed():
    for status in (403, 404, 500, "blocked", "browser_rendered_200_direct_requests_403"):
        decision = fail_closed_decision(status)
        assert decision["runtime_action"] in {
            "downgrade_to_needs_verification_until_revalidated",
            "use_existing_evidence_only_if_not_expired_and_display_stale_warning",
        }
        assert decision["must_not_promote"] is True


def test_discovery_prefers_corrected_artifact_and_skips_reviews(tmp_path):
    _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch18-evidence-upgrades-2026-05-05.json", _artifact("old"))
    corrected = _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch18-evidence-upgrades-corrected-v2-2026-05-05.json", _artifact("corrected"))
    _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch18-independent-review-2026-05-05.json", {"status": "PASS"})

    discovered = discover_step6a_artifacts(tmp_path)

    assert discovered == [corrected]


def test_artifact_without_independent_acceptance_fails_closed(tmp_path):
    payload = _artifact()
    payload["status"] = "primary_complete_pending_independent_review"
    artifact_path = _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch97-evidence-upgrades-2026-05-05.json", payload)

    pack = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")

    record = pack["records"][0]
    assert record["ingestion_ready"] is False
    assert record["field_status"] == "needs_verification"
    assert "artifact_not_independently_accepted" in record["validation_errors"]



def test_companion_independent_review_pass_accepts_pending_artifact(tmp_path):
    payload = _artifact()
    payload["status"] = "primary_complete_pending_independent_review"
    artifact_path = _write_json(
        tmp_path / "permitassist-road-to-perfection-step6a-batch89-evidence-upgrades-2026-05-05.json",
        payload,
    )
    _write_json(
        tmp_path / "permitassist-road-to-perfection-step6a-batch89-independent-review-2026-05-05.json",
        {"verdict": "PASS", "blockers": []},
    )

    pack = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")

    record = pack["records"][0]
    assert record["ingestion_ready"] is True
    assert record["field_status"] == "verified"
    assert "artifact_not_independently_accepted" not in record["validation_errors"]


def test_companion_independent_review_with_blockers_still_fails_closed(tmp_path):
    payload = _artifact()
    payload["status"] = "primary_complete_pending_independent_review"
    artifact_path = _write_json(
        tmp_path / "permitassist-road-to-perfection-step6a-batch88-evidence-upgrades-2026-05-05.json",
        payload,
    )
    _write_json(
        tmp_path / "permitassist-road-to-perfection-step6a-batch88-independent-review-2026-05-05.json",
        {"verdict": "PASS_WITH_BLOCKERS", "blockers": ["source validation unavailable"]},
    )

    pack = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")

    record = pack["records"][0]
    assert record["ingestion_ready"] is False
    assert record["field_status"] == "needs_verification"
    assert "artifact_not_independently_accepted" in record["validation_errors"]


def test_companion_independent_review_pass_with_blockers_status_fails_closed_even_without_blocker_list(tmp_path):
    payload = _artifact()
    payload["status"] = "primary_complete_pending_independent_review"
    artifact_path = _write_json(
        tmp_path / "permitassist-road-to-perfection-step6a-batch83-evidence-upgrades-2026-05-05.json",
        payload,
    )
    _write_json(
        tmp_path / "permitassist-road-to-perfection-step6a-batch83-independent-review-2026-05-05.json",
        {"verdict": "PASS_WITH_BLOCKERS", "blockers": []},
    )

    record = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")["records"][0]

    assert record["ingestion_ready"] is False
    assert "artifact_not_independently_accepted" in record["validation_errors"]


def test_bypass_like_review_status_does_not_accidentally_pass_substring_match(tmp_path):
    payload = _artifact()
    payload["status"] = "primary_complete_pending_independent_review"
    artifact_path = _write_json(
        tmp_path / "permitassist-road-to-perfection-step6a-batch82-evidence-upgrades-2026-05-05.json",
        payload,
    )
    _write_json(
        tmp_path / "permitassist-road-to-perfection-step6a-batch82-independent-review-2026-05-05.json",
        {"verdict": "BYPASS", "blockers": []},
    )

    record = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")["records"][0]

    assert record["ingestion_ready"] is False
    assert "artifact_not_independently_accepted" in record["validation_errors"]


def test_any_conflicting_companion_review_blocker_fails_closed(tmp_path):
    payload = _artifact()
    payload["status"] = "primary_complete_pending_independent_review"
    artifact_path = _write_json(
        tmp_path / "permitassist-road-to-perfection-step6a-batch79-evidence-upgrades-2026-05-05.json",
        payload,
    )
    _write_json(
        tmp_path / "permitassist-road-to-perfection-step6a-batch79-independent-review-2026-05-05.json",
        {"verdict": "PASS", "blockers": []},
    )
    _write_json(
        tmp_path / "permitassist-road-to-perfection-step6a-batch79-later-independent-review-2026-05-05.json",
        {"verdict": "PASS_WITH_BLOCKERS", "blockers": ["later blocker"]},
    )

    record = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")["records"][0]

    assert record["ingestion_ready"] is False
    assert "artifact_not_independently_accepted" in record["validation_errors"]


def test_explicit_accepted_false_review_does_not_pass_on_pass_verdict(tmp_path):
    payload = _artifact()
    payload["status"] = "primary_complete_pending_independent_review"
    artifact_path = _write_json(
        tmp_path / "permitassist-road-to-perfection-step6a-batch78-evidence-upgrades-2026-05-05.json",
        payload,
    )
    _write_json(
        tmp_path / "permitassist-road-to-perfection-step6a-batch78-independent-review-2026-05-05.json",
        {"verdict": "PASS", "accepted": False, "blockers": []},
    )

    record = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")["records"][0]

    assert record["ingestion_ready"] is False
    assert "artifact_not_independently_accepted" in record["validation_errors"]


def test_missing_fetch_status_fails_closed():
    decision = fail_closed_decision(None)

    assert decision["runtime_action"] == "downgrade_to_needs_verification_until_revalidated"
    assert decision["must_not_promote"] is True


def test_scope_limit_display_contract_does_not_forbid_own_field(tmp_path):
    payload = _artifact()
    row = payload["accepted_upgrades"][0]
    row["field"] = "fee_range"
    row["source_scope_limit"] = "Verifies fee_range only for captured official fee schedule components. Does not verify approval_timeline, inspections sequence, apply_url, permit_type, companion reviews, or routing."
    artifact_path = _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch96-evidence-upgrades-2026-05-05.json", payload)

    pack = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")

    record = pack["records"][0]
    assert "fee_range" not in record["display_contract"]["must_not_use_for_fields"]
    assert "approval_timeline" in record["display_contract"]["must_not_use_for_fields"]
    assert "inspections" in record["display_contract"]["must_not_use_for_fields"]


def test_legacy_flat_row_shape_is_adapted_with_clear_remaining_errors(tmp_path):
    payload = {
        "title": "legacy fixture",
        "batch_id": "legacy-batch",
        "created_utc": "2026-05-05T12:00:00Z",
        "status": "PASS_ACCEPTED_AFTER_INDEPENDENT_REVIEW",
        "accepted_upgrades": [
            {
                "row_id": "legacy-row",
                "state": "CA",
                "ahj_name": "City of Oakland",
                "vertical": "restaurant_ti",
                "field": "companion_reviews_triggers",
                "proposed_status": "partial",
                "claim_value_after": "Plan review may be required for remodels.",
                "limits_and_caveats": "Statewide food-facility plan review trigger only; does not verify local AHJ routing, fees, timelines, apply URL, permit type, or inspections.",
                "checked_at_utc": "2026-05-05T12:01:00Z",
                "source_url": "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?sectionNum=114380&lawCode=HSC",
                "source_title": "California Health and Safety Code Section 114380",
                "source_type": "official_state",
                "exact_official_quote": "A person proposing to build or remodel a food facility shall submit complete, easily readable plans.",
                "quote_found_current_run": True,
                "source_text_sha256": "b" * 64,
            }
        ],
    }
    artifact_path = _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch95-evidence-upgrades-2026-05-05.json", payload)

    pack = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")

    record = pack["records"][0]
    assert record["source_scope_limit"] == payload["accepted_upgrades"][0]["limits_and_caveats"]
    assert record["field_evidence"][0]["exact_quote_or_snippet"].startswith("A person proposing")
    assert record["field_evidence"][0]["normalized_source_text_sha256"] == "b" * 64
    assert "missing_field_level_evidence" not in record["validation_errors"]
    assert "missing_source_scope_limit" not in record["validation_errors"]
    assert "missing_normalized_source_text_length" in record["validation_errors"]


def test_source_title_falls_back_to_source_host(tmp_path):
    payload = _artifact()
    evidence = payload["accepted_upgrades"][0]["evidence"][0]
    evidence.pop("source_title")
    evidence["source_host"] = "hcai.ca.gov"
    artifact_path = _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch94-evidence-upgrades-2026-05-05.json", payload)

    pack = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")

    record = pack["records"][0]
    assert record["field_evidence"][0]["source_title"] == "hcai.ca.gov"
    assert "missing_source_title" not in record["validation_errors"]


def test_discovery_skips_glob_matching_review_files_and_prefers_highest_corrected_version(tmp_path):
    _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch18-evidence-upgrades-2026-05-05.json", _artifact("old"))
    _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch18-evidence-upgrades-corrected-v2-2026-05-05.json", _artifact("v2"))
    corrected_v3 = _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch18-evidence-upgrades-corrected-v3-2026-05-05.json", _artifact("v3"))
    _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch18-evidence-upgrades-independent-review-2026-05-05.json", {"status": "PASS"})

    discovered = discover_step6a_artifacts(tmp_path)

    assert discovered == [corrected_v3]


def test_write_outputs_creates_json_and_markdown(tmp_path):
    artifact_path = _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch99-evidence-upgrades-2026-05-05.json", _artifact())
    pack = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")

    json_path, md_path = write_evidence_pack_outputs(pack, tmp_path, stem="step7b-fixture")

    assert json.loads(json_path.read_text(encoding="utf-8"))["metadata"]["fingerprint_sha256"] == pack["metadata"]["fingerprint_sha256"]
    md = md_path.read_text(encoding="utf-8")
    assert "# PermitAssist Step 7B Offline Evidence Pack" in md
    assert "PASS_OFFLINE_READY" in md
    assert "Production wiring allowed: false" in md


def test_scope_limit_comma_list_marks_excluded_fields_but_not_current_field(tmp_path):
    payload = _artifact()
    row = payload["accepted_upgrades"][0]
    row["field"] = "companion_reviews_triggers"
    row["source_scope_limit"] = "Statewide trigger only; no local AHJ routing, fees, timelines, apply URL, permit type, or inspections verified."
    artifact_path = _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch93-evidence-upgrades-2026-05-05.json", payload)

    pack = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")

    forbidden = pack["records"][0]["display_contract"]["must_not_use_for_fields"]
    assert "companion_reviews_triggers" not in forbidden
    assert {"fee_range", "approval_timeline", "apply_url", "permit_type", "inspections"}.issubset(set(forbidden))


def test_fingerprint_is_stable_across_different_artifact_parent_paths(tmp_path):
    left = tmp_path / "left" / "permitassist-road-to-perfection-step6a-batch92-evidence-upgrades-2026-05-05.json"
    right = tmp_path / "right" / "permitassist-road-to-perfection-step6a-batch92-evidence-upgrades-2026-05-05.json"
    left.parent.mkdir()
    right.parent.mkdir()
    _write_json(left, _artifact("stable-batch"))
    _write_json(right, _artifact("stable-batch"))

    left_pack = build_evidence_pack([left], generated_at_utc="2026-05-05T15:15:00Z")
    right_pack = build_evidence_pack([right], generated_at_utc="2026-05-05T15:15:00Z")

    assert left_pack["metadata"]["fingerprint_sha256"] == right_pack["metadata"]["fingerprint_sha256"]
    assert left_pack["records"][0]["record_fingerprint_sha256"] == right_pack["records"][0]["record_fingerprint_sha256"]


def test_ingestion_contract_includes_fetch_status_code(tmp_path):
    artifact_path = _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch91-evidence-upgrades-2026-05-05.json", _artifact())
    pack = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")

    required = pack["validation"]["ingestion_regression_contract"]["required_evidence_fields"]
    assert "fetch_status_code" in required

def test_companion_source_validation_backfills_missing_metadata_and_quote(tmp_path):
    payload = _artifact(scope_limit="")
    row = payload["accepted_upgrades"][0]
    row["field"] = "companion_reviews_triggers"
    row["evidence"] = [
        {
            "source_id": "official_source_one",
            "source_url": "https://example.gov/source",
            "source_type": "official_ahj",
        }
    ]
    artifact_path = _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch90-evidence-upgrades-2026-05-05.json", payload)
    _write_json(
        tmp_path / "permitassist-road-to-perfection-step6a-batch90-source-validation-2026-05-05.json",
        {
            "title": "source validation",
            "created_utc": "2026-05-05T12:00:00Z",
            "verdict": "PASS",
            "sources": [
                {
                    "source_id": "official_source_one",
                    "source_url": "https://example.gov/source",
                    "source_title": "Official source one",
                    "source_type": "official_ahj",
                    "fetch_status_code": 200,
                    "normalized_text_sha256": "c" * 64,
                    "normalized_text_length": 4567,
                    "exact_snippets": ["Official field-level quote from the current validation artifact."],
                }
            ],
        },
    )

    pack = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")

    record = pack["records"][0]
    item = record["field_evidence"][0]
    assert record["source_scope_limit_generated"] is True
    assert record["ingestion_ready"] is True
    assert item["source_title"] == "Official source one"
    assert item["exact_quote_or_snippet"] == "Official field-level quote from the current validation artifact."
    assert item["normalized_source_text_sha256"] == "c" * 64
    assert item["normalized_source_text_length"] == 4567
    assert item["fetch_status_code"] == 200
    assert item["fetch_status_inferred"] is False


def test_generated_scope_for_inspection_field_forbids_companion_review_reuse(tmp_path):
    payload = _artifact(scope_limit="")
    row = payload["accepted_upgrades"][0]
    row["field"] = "inspections"
    row["vertical"] = "office_ti"
    artifact_path = _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch81-evidence-upgrades-2026-05-05.json", payload)

    record = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")["records"][0]

    assert record["ingestion_ready"] is True
    assert record["source_scope_limit_generated"] is True
    assert "companion_reviews_triggers" in record["display_contract"]["must_not_use_for_fields"]
    assert "inspections" not in record["display_contract"]["must_not_use_for_fields"]


def test_generated_scope_for_companion_review_field_forbids_inspection_reuse(tmp_path):
    payload = _artifact(scope_limit="")
    artifact_path = _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch80-evidence-upgrades-2026-05-05.json", payload)

    record = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")["records"][0]

    assert record["ingestion_ready"] is True
    assert record["source_scope_limit_generated"] is True
    assert "inspections" in record["display_contract"]["must_not_use_for_fields"]
    assert "companion_reviews_triggers" not in record["display_contract"]["must_not_use_for_fields"]


@pytest.mark.parametrize(
    "field",
    ["permit_type", "apply_url", "fee_range", "approval_timeline", "inspections", "companion_reviews_triggers"],
)
def test_generated_scope_forbids_every_supported_field_except_current_field(tmp_path, field):
    payload = _artifact(scope_limit="")
    row = payload["accepted_upgrades"][0]
    row["field"] = field
    artifact_path = _write_json(tmp_path / f"permitassist-road-to-perfection-step6a-batch77-{field}-evidence-upgrades-2026-05-05.json", payload)

    record = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")["records"][0]

    expected = {"permit_type", "apply_url", "fee_range", "approval_timeline", "inspections", "companion_reviews_triggers"} - {field}
    assert record["source_scope_limit_generated"] is True
    assert set(record["display_contract"]["must_not_use_for_fields"]) == expected


def test_root_source_validation_shape_backfills_legacy_flat_row(tmp_path):
    payload = _artifact(scope_limit="Statewide trigger only; no local AHJ routing, fees, timelines, apply URL, permit type, or inspections verified.")
    row = payload["accepted_upgrades"][0]
    row.pop("evidence")
    row["source_url"] = "https://example.gov/root-source"
    row["source_title"] = ""
    row["exact_official_quote"] = "Root-level official quote."
    row["source_text_sha256"] = ""
    artifact_path = _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch87-evidence-upgrades-2026-05-05.json", payload)
    _write_json(
        tmp_path / "permitassist-road-to-perfection-step6a-batch87-source-validation-2026-05-05.json",
        {
            "source_url": "https://example.gov/root-source",
            "final_url": "https://example.gov/root-source",
            "source_title": "Root source title",
            "fetch_status_code": 200,
            "normalized_text_sha256": "d" * 64,
            "normalized_text_length": 9876,
            "quote": "Root-level official quote.",
            "quote_found_in_current_normalized_text": True,
            "verdict": "PASS",
        },
    )

    record = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")["records"][0]

    item = record["field_evidence"][0]
    assert record["ingestion_ready"] is True
    assert item["source_title"] == "Root source title"
    assert item["normalized_source_text_sha256"] == "d" * 64
    assert item["normalized_source_text_length"] == 9876
    assert item["fetch_status_inferred"] is False


def test_sources_checked_validation_shape_backfills_content_metadata(tmp_path):
    payload = _artifact()
    row = payload["accepted_upgrades"][0]
    row["evidence"] = [{"source_id": "checked_source", "source_url": "https://example.gov/checked"}]
    artifact_path = _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch86-evidence-upgrades-2026-05-05.json", payload)
    _write_json(
        tmp_path / "permitassist-road-to-perfection-step6a-batch86-source-validation-2026-05-05.json",
        {
            "sources_checked": [
                {
                    "source_id": "checked_source",
                    "source_url": "https://example.gov/checked",
                    "source_title": "Checked source",
                    "fetch_status_code": "browser_rendered_200",
                    "source_content_sha256": "e" * 64,
                    "source_content_length": 4321,
                    "exact_snippets": ["Checked exact snippet."],
                }
            ],
            "quote_validation_status": "pass",
        },
    )

    record = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")["records"][0]

    item = record["field_evidence"][0]
    assert record["ingestion_ready"] is True
    assert item["fetch_status_code"] == "browser_rendered_200"
    assert item["normalized_source_text_sha256"] == "e" * 64
    assert item["normalized_source_text_length"] == 4321

def test_legacy_flat_row_exact_snippets_and_browser_rendered_metadata_are_adapted(tmp_path):
    payload = _artifact()
    row = payload["accepted_upgrades"][0]
    row.pop("evidence")
    row.pop("source_scope_limit", None)
    row["source_url"] = "https://example.gov/browser-source"
    row["source_title"] = "Browser source"
    row["exact_snippets"] = ["Browser-rendered official snippet."]
    artifact_path = _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch85-evidence-upgrades-2026-05-05.json", payload)
    _write_json(
        tmp_path / "permitassist-road-to-perfection-step6a-batch85-source-validation-2026-05-05.json",
        {
            "sources": {
                "browser_source": {
                    "url": "https://example.gov/browser-source",
                    "source_title": "Browser source",
                    "browser_rendered_text_sha256": "f" * 64,
                    "browser_rendered_text_length": 2757,
                    "snippet_found_in_browser_rendered_text": True,
                }
            }
        },
    )

    record = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")["records"][0]

    item = record["field_evidence"][0]
    assert record["ingestion_ready"] is True
    assert item["exact_quote_or_snippet"] == "Browser-rendered official snippet."
    assert item["normalized_source_text_sha256"] == "f" * 64
    assert item["normalized_source_text_length"] == 2757
    assert item["fetch_status_code"] == "validated_current_run"
    assert item["fetch_status_inferred"] is True


def test_nested_source_object_legacy_row_is_adapted(tmp_path):
    payload = {
        "title": "nested source fixture",
        "batch_id": "nested-source-batch",
        "created_utc": "2026-05-05T12:00:00Z",
        "status": "PASS_ACCEPTED_AFTER_INDEPENDENT_REVIEW",
        "accepted_upgrades": [
            {
                "state": "MA",
                "ahj_name": "City of Waltham",
                "vertical": "medical_clinic_ti",
                "field": "inspections",
                "proposed_status": "partial",
                "claim_value": "Inspection trigger evidence.",
                "source_scope_limit": "State code inspection trigger only; does not verify local sequence, fees, timelines, apply URL, permit type, or companion reviews.",
                "checked_at_utc": "2026-05-05T12:01:00Z",
                "exact_official_quote": "Nested official quote.",
                "source": {
                    "source_id": "nested_source",
                    "source_url": "https://example.gov/nested-source",
                    "source_title": "Nested source title",
                    "source_type": "official_state",
                    "normalized_text_sha256": "1" * 64,
                    "normalized_text_length": 1234,
                    "fetched_via": "fixture fetch",
                },
            }
        ],
    }
    artifact_path = _write_json(tmp_path / "permitassist-road-to-perfection-step6a-batch84-evidence-upgrades-2026-05-05.json", payload)

    record = build_evidence_pack([artifact_path], generated_at_utc="2026-05-05T15:15:00Z")["records"][0]

    item = record["field_evidence"][0]
    assert record["ingestion_ready"] is True
    assert item["source_id"] == "nested_source"
    assert item["source_title"] == "Nested source title"
    assert item["normalized_source_text_sha256"] == "1" * 64
    assert item["normalized_source_text_length"] == 1234
    assert item["fetch_status_code"] == "validated_current_run"
    assert item["fetch_status_inferred"] is True
