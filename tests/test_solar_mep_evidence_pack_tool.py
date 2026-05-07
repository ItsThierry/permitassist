import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.evidence_pack_tool import (  # noqa: E402
    build_evidence_pack,
    evidence_pack_tool_readiness,
    raw_file_sha256,
    write_evidence_pack_outputs,
)
from api.evidence_pack_runtime import _validate_metadata_contract  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[1]
SOLAR_MEP_INPUT = REPO_ROOT / "data" / "evidence_pack_inputs" / "solar_mep" / "permitassist-step6a-solar-commercial-mep-seed-2026-05-07.json"
SOLAR_MEP_REVIEW = REPO_ROOT / "data" / "evidence_pack_inputs" / "solar_mep" / "permitassist-step6a-solar-commercial-mep-seed-independent-review-2026-05-07.json"
CANONICAL_PACK_JSON = REPO_ROOT / "data" / "evidence_packs" / "offline" / "solar_mep" / "permitassist-step7b-solar-commercial-mep-offline-evidence-pack-20260507.json"
MIRROR_PACK_JSON = REPO_ROOT / "evidence_packs" / "offline" / "solar_mep" / "permitassist-step7b-solar-commercial-mep-offline-evidence-pack-20260507.json"


def _records_by_id(pack: dict) -> dict[str, dict]:
    return {record["record_id"]: record for record in pack["records"]}


def _seed_payload() -> dict:
    return json.loads(SOLAR_MEP_INPUT.read_text(encoding="utf-8"))


def _copy_seed_without_review(tmp_path: Path) -> Path:
    target_dir = tmp_path / "isolated_seed"
    target_dir.mkdir()
    target = target_dir / SOLAR_MEP_INPUT.name
    shutil.copyfile(SOLAR_MEP_INPUT, target)
    return target


def test_solar_mep_seed_artifact_builds_full_offline_fail_closed_pack(tmp_path):
    assert SOLAR_MEP_INPUT.exists(), "solar/MEP seed artifact must be committed as an offline source input"
    assert SOLAR_MEP_REVIEW.exists(), "solar/MEP seed requires a separate independent-review artifact"

    pack = build_evidence_pack([SOLAR_MEP_INPUT], generated_at_utc="2026-05-07T02:00:00Z")
    json_path, md_path = write_evidence_pack_outputs(pack, tmp_path, stem="solar-mep-pack")
    readiness = evidence_pack_tool_readiness(json_path, report_path=md_path)

    assert pack["metadata"]["production_wiring_allowed"] is False
    assert pack["validation"]["verdict"] == "PASS_OFFLINE_READY"
    assert pack["validation"]["fail_closed_records"] == 0
    assert readiness["verdict"] == "FULLY_READY_OFFLINE_FAIL_CLOSED"
    assert readiness["readiness_score"] == 100
    assert readiness["safe_for_env_activation"] is False
    assert json_path.exists()
    assert json_path.with_suffix(json_path.suffix + ".sha256").exists()
    assert md_path.exists()

    records = _records_by_id(pack)
    assert set(records) == {
        "nyc-solar-pv-required-permits",
        "nyc-solar-pv-review-timeline",
        "nyc-solar-pv-structural-electrical-companions",
        "dallas-commercial-building-permit",
        "dallas-commercial-mep-online-trade-permit-path",
        "dallas-commercial-mechanical-trigger",
        "dallas-commercial-electrical-trigger",
        "dallas-commercial-plumbing-trigger",
        "dallas-commercial-mep-trade-inspections",
    }
    assert {record["vertical"] for record in records.values()} == {
        "solar_pv_battery",
        "commercial_mep_ti",
        "commercial_mechanical",
        "commercial_electrical",
        "commercial_plumbing",
    }


def test_solar_records_keep_false_positive_guards_and_do_not_leak_into_roofing_or_panel_only_scopes():
    pack = build_evidence_pack([SOLAR_MEP_INPUT], generated_at_utc="2026-05-07T02:00:00Z")
    records = _records_by_id(pack)

    for record_id in (
        "nyc-solar-pv-required-permits",
        "nyc-solar-pv-review-timeline",
        "nyc-solar-pv-structural-electrical-companions",
    ):
        record = records[record_id]
        assert record["vertical"] == "solar_pv_battery"
        assert record["negative_scope_guards"] == [
            "reroof_no_solar",
            "roof_repair_only",
            "electrical_panel_work_without_pv_or_battery",
        ]
        assert "solar" in record["claim_value"].lower() or "pv" in record["claim_value"].lower()
        scope = record["source_scope_limit"].lower()
        assert "reroof without solar" in scope
        assert "roof repair only" in scope


def test_commercial_mep_records_are_trade_aware_and_conditional_only():
    pack = build_evidence_pack([SOLAR_MEP_INPUT], generated_at_utc="2026-05-07T02:00:00Z")
    records = _records_by_id(pack)

    expected_trade_scopes = {
        "dallas-commercial-mechanical-trigger": "mechanical",
        "dallas-commercial-electrical-trigger": "electrical",
        "dallas-commercial-plumbing-trigger": "plumbing",
    }
    for record_id, trade in expected_trade_scopes.items():
        record = records[record_id]
        assert record["trade_scope"] == trade
        assert record["companion_review_policy"] == "conditional_only"
        assert "does not verify unrelated trades" in record["source_scope_limit"].lower()

    assert records["dallas-commercial-mep-trade-inspections"]["trade_scope"] == "cross_trade_mep"
    assert records["dallas-commercial-mep-trade-inspections"]["companion_review_policy"] == "conditional_only"


def test_solar_mep_seed_cannot_self_certify_independent_acceptance(tmp_path):
    isolated_seed = _copy_seed_without_review(tmp_path)

    pack = build_evidence_pack([isolated_seed], generated_at_utc="2026-05-07T02:00:00Z")

    assert pack["validation"]["verdict"] == "FAIL_CLOSED_NOT_INGESTION_READY"
    assert pack["validation"]["fail_closed_records"] == 9
    for record in pack["records"]:
        assert record["ingestion_ready"] is False
        assert "artifact_not_independently_accepted" in record["validation_errors"]


def test_offline_snapshots_do_not_emit_live_fetch_status_200():
    pack = build_evidence_pack([SOLAR_MEP_INPUT], generated_at_utc="2026-05-07T02:00:00Z")

    for record in pack["records"]:
        for evidence in record["field_evidence"]:
            method = evidence["validation_method"].lower()
            if "no runtime fetch" in method:
                assert evidence.get("runtime_fetch_status") == "not_attempted_offline_snapshot"
                assert evidence.get("runtime_fetch_status_code") is None
                assert evidence.get("source_snapshot_status_code_at_capture") == 200
                assert evidence.get("fetch_status_code") != 200


def test_dallas_mep_apply_url_uses_routine_dallasnow_source_not_tornado_faq():
    pack = build_evidence_pack([SOLAR_MEP_INPUT], generated_at_utc="2026-05-07T02:00:00Z")
    record = _records_by_id(pack)["dallas-commercial-mep-online-trade-permit-path"]
    evidence = record["field_evidence"][0]

    assert "tornado" not in evidence["source_url"].lower()
    assert evidence["source_id"] == "dallas_dallasnow"
    assert evidence["source_url"] in {
        "https://dallascityhall.com/departments/sustainabledevelopment/Pages/DallasNow.aspx",
        "https://aca-prod.accela.com/DALLASTX/Default.aspx",
    }
    assert "DallasNow" in evidence["exact_quote_or_snippet"]
    assert "Permitting" in evidence["exact_quote_or_snippet"]


def test_dallas_mep_inspection_claim_is_fully_supported_or_narrowed():
    pack = build_evidence_pack([SOLAR_MEP_INPUT], generated_at_utc="2026-05-07T02:00:00Z")
    record = _records_by_id(pack)["dallas-commercial-mep-trade-inspections"]
    claim = record["claim_value"].lower()
    quotes = "\n".join(item["exact_quote_or_snippet"] for item in record["field_evidence"]).lower()

    if "final" in claim or "completion" in claim:
        assert "final" in quotes or "completion" in quotes
    assert "prior to framing" in quotes or "framing inspection" in quotes


def test_solar_mep_metadata_fields_are_required_and_validated(tmp_path):
    payload = _seed_payload()
    row = payload["accepted_upgrades"][0]
    row.pop("trade_scope", None)
    row["companion_review_policy"] = "always_required"
    row["negative_scope_guards"] = "reroof_no_solar"
    row["positive_scope_triggers"] = []
    bad_path = tmp_path / SOLAR_MEP_INPUT.name
    bad_path.write_text(json.dumps(payload), encoding="utf-8")
    shutil.copyfile(SOLAR_MEP_REVIEW, tmp_path / SOLAR_MEP_REVIEW.name)

    pack = build_evidence_pack([bad_path], generated_at_utc="2026-05-07T02:00:00Z")
    record = _records_by_id(pack)["nyc-solar-pv-required-permits"]

    assert pack["validation"]["verdict"] == "FAIL_CLOSED_NOT_INGESTION_READY"
    assert record["ingestion_ready"] is False
    assert "invalid_or_missing_trade_scope" in record["validation_errors"]
    assert "invalid_companion_review_policy" in record["validation_errors"]
    assert "invalid_negative_scope_guards" in record["validation_errors"]
    assert "invalid_or_missing_positive_scope_triggers" in record["validation_errors"]


def test_solar_mep_fingerprint_stays_stable_across_equivalent_paths(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    left_seed = left / SOLAR_MEP_INPUT.name
    right_seed = right / SOLAR_MEP_INPUT.name
    shutil.copyfile(SOLAR_MEP_INPUT, left_seed)
    shutil.copyfile(SOLAR_MEP_REVIEW, left / SOLAR_MEP_REVIEW.name)
    shutil.copyfile(SOLAR_MEP_INPUT, right_seed)
    shutil.copyfile(SOLAR_MEP_REVIEW, right / SOLAR_MEP_REVIEW.name)

    left_pack = build_evidence_pack([left_seed], generated_at_utc="2026-05-07T02:00:00Z")
    right_pack = build_evidence_pack([right_seed], generated_at_utc="2026-05-07T02:00:00Z")

    assert left_pack["metadata"]["fingerprint_sha256"] == right_pack["metadata"]["fingerprint_sha256"]
    assert [r["record_fingerprint_sha256"] for r in left_pack["records"]] == [r["record_fingerprint_sha256"] for r in right_pack["records"]]


def test_solar_mep_mirror_artifacts_and_sha_sidecars_do_not_drift():
    assert CANONICAL_PACK_JSON.exists()
    assert MIRROR_PACK_JSON.exists()
    assert CANONICAL_PACK_JSON.read_bytes() == MIRROR_PACK_JSON.read_bytes()
    for path in (CANONICAL_PACK_JSON, MIRROR_PACK_JSON):
        sha_path = path.with_suffix(path.suffix + ".sha256")
        assert sha_path.exists()
        assert sha_path.read_text(encoding="utf-8").strip() == raw_file_sha256(path)


def test_step7b_offline_solar_mep_pack_is_rejected_for_production_preview_activation():
    pack = build_evidence_pack([SOLAR_MEP_INPUT], generated_at_utc="2026-05-07T02:00:00Z")

    version, _fingerprint, status, warnings, production_wiring_allowed, fingerprint_valid = _validate_metadata_contract(
        pack["metadata"],
        expected_fingerprint=pack["metadata"]["fingerprint_sha256"],
        mode="dallas_step7u_production_preview",
    )

    assert version == "step7b_offline_v1"
    assert production_wiring_allowed is False
    assert fingerprint_valid is True
    assert status == "invalid_version"
    assert "evidence_pack_version_not_allowed_for_mode" in warnings


def test_missing_or_unsupported_solar_mep_source_records_fail_closed(tmp_path):
    payload = json.loads(SOLAR_MEP_INPUT.read_text(encoding="utf-8"))
    row = payload["accepted_upgrades"][0]
    row["field"] = "utility_interconnection"
    row["evidence"][0]["source_url"] = ""
    row["evidence"][0]["exact_quote"] = ""
    row["evidence"][0]["quote_found_current_run"] = False
    artifact_path = tmp_path / "permitassist-step6a-solar-commercial-mep-bad-seed-2026-05-07.json"
    artifact_path.write_text(json.dumps(payload), encoding="utf-8")

    pack = build_evidence_pack([artifact_path], generated_at_utc="2026-05-07T02:00:00Z")
    record = _records_by_id(pack)["nyc-solar-pv-required-permits"]

    assert pack["validation"]["verdict"] == "FAIL_CLOSED_NOT_INGESTION_READY"
    assert record["ingestion_ready"] is False
    assert record["field_status"] == "needs_verification"
    assert "unsupported_or_missing_field" in record["validation_errors"]
    assert "invalid_or_missing_source_url" in record["validation_errors"]
    assert "missing_field_level_quote_or_snippet" in record["validation_errors"]
