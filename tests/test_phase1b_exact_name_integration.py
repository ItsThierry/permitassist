import copy
import json
import sys
from importlib import util
from pathlib import Path

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper_phase1b_exact_name",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_import_server = _debug_helper._import_server

REPO_ROOT = Path(__file__).resolve().parents[1]
PHASE1B_PACK = REPO_ROOT / "api" / "data" / "evidence_packs" / "phase1b" / "permitassist-phase1b-commercial-ti-exact-names-20260516.json"
PHASE1B_MODE = "phase1b_commercial_ti_exact_names_preview"
FORBIDDEN_CUSTOMER_STRINGS = (
    "fee_range",
    "Permit — verify exact AHJ title",
    "Permit -- verify exact AHJ title",
    "failed-closed",
    "ingestion-ready",
    "pack-controlled",
    "local-pack",
)


def _base_engine_result():
    return {
        "permit_verdict": "YES",
        "confidence": "medium",
        "permit_required": True,
        "permit_name": "Generic model fallback permit",
        "permit_type": "Generic model fallback permit",
        "permits_required": [{"permit_type": "Generic model fallback permit"}],
        "fee_range": "$500-$1,000",
        "approval_timeline": "2-4 weeks",
        "inspections": ["final"],
        "apply_url": None,
        "sources": ["https://example.gov/generic-permits"],
        "checklist": [],
        "rejection_patterns": [],
    }


def _enable_phase1b_pack(monkeypatch):
    data = json.loads(PHASE1B_PACK.read_text(encoding="utf-8"))
    fingerprint = data["metadata"]["fingerprint_sha256"]
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_ENABLED", "true")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_MODE", PHASE1B_MODE)
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_EXPECTED_FINGERPRINT", fingerprint)
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PATH", str(PHASE1B_PACK))
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PREVIEW_ONLY", "true")
    monkeypatch.setenv("PERMITASSIST_PHASE1B_EXACT_NAME_PREVIEW_TOKEN", "phase1b-test-token")
    monkeypatch.setenv("PERMITASSIST_PHASE1B_EXACT_NAME_PREVIEW_HEADER", "X-Phase1B-Exact-Name-Preview-Token")


def _finalize(tmp_path, monkeypatch, city, state, vertical, *, job_type=None, base=None):
    _enable_phase1b_pack(monkeypatch)
    server = _import_server(tmp_path, monkeypatch)
    return server.finalize_permit_lookup_result(
        copy.deepcopy(base or _base_engine_result()),
        job_type or f"{vertical.replace('_', ' ')} commercial tenant improvement",
        city,
        state,
        explicit_vertical=vertical,
    )


def _customer_text(result):
    publicish = {
        key: value
        for key, value in result.items()
        if key not in {"_evidence_pack", "needs_review_reasons", "quality_warnings"}
        and value is not None
    }
    return json.dumps(publicish, sort_keys=True)


def test_phase1b_pack_identity_and_scope_are_pinned():
    data = json.loads(PHASE1B_PACK.read_text(encoding="utf-8"))
    metadata = data["metadata"]
    records = data["records"]
    assert metadata["evidence_pack_version"] == "phase1b_commercial_ti_exact_names_v1"
    assert metadata["source_data_repo_main_sha"] == "ed3166d3784607bb50861e75c3220482374df9fb"
    assert metadata["row_level_classification_counts"] == {"exact_strong": 57}
    assert metadata["ahj_count"] == 19
    assert metadata["record_count"] == 57
    assert len(records) == 57
    assert {record["state"] for record in records} == {"CA", "TX", "FL", "MA"}
    assert {record["vertical"] for record in records} == {"restaurant_ti", "medical_clinic_ti", "office_ti"}
    assert {record["field"] for record in records} == {"display_permit_name", "official_permit_name"}
    assert {record["row_level_classification"] for record in records} == {"exact_strong"}
    assert all(record["ingestion_ready"] is True for record in records)
    assert not any("category_only" in json.dumps(record) or "needs_human" in json.dumps(record) for record in records)


def test_all_phase1b_exact_strong_rows_produce_source_backed_customer_safe_names(tmp_path, monkeypatch):
    data = json.loads(PHASE1B_PACK.read_text(encoding="utf-8"))
    for record in data["records"]:
        result = _finalize(
            tmp_path / record["source_row_id"].replace(":", "_"),
            monkeypatch,
            record["city"],
            record["state"],
            record["vertical"],
        )
        expected = record["claim_value"]
        assert result["permit_name"] == expected
        assert result["permit_type"] == expected
        assert result["_permit_display_name"] == expected
        assert result["permit_name_source_field"] == record["field"]
        assert result["permit_name_status"] == "exact_official_name_confirmed"
        assert result["permit_name_confidence"] == "high"
        assert result["_evidence_pack"]["contract_status"] == "valid"
        assert result["_evidence_pack"]["request_vertical"] == record["vertical"]
        assert record["field"] in result["_evidence_pack"]["matched_fields"]
        assert result["claim_citations"]
        text = _customer_text(result)
        for forbidden in FORBIDDEN_CUSTOMER_STRINGS:
            assert forbidden not in text


def test_phase1b_category_only_and_needs_human_rows_are_not_customer_promoted(tmp_path, monkeypatch):
    blocked_cases = [
        ("City of Tampa", "FL", "restaurant_ti", "Permit Application Guide - Commercial Alteration"),
        ("City of Tampa", "FL", "medical_clinic_ti", "Permit Application Guide - Commercial Alteration"),
        ("City of Tampa", "FL", "office_ti", "Permit Application Guide - Commercial Alteration"),
        ("City of Fort Lauderdale", "FL", "restaurant_ti", "Fort Lauderdale Portal / Permitting Services"),
        ("City of Fort Lauderdale", "FL", "medical_clinic_ti", "Fort Lauderdale Portal / Permitting Services"),
        ("City of Fort Lauderdale", "FL", "office_ti", "Fort Lauderdale Portal / Permitting Services"),
    ]
    for city, state, vertical, blocked_name in blocked_cases:
        result = _finalize(tmp_path / f"blocked-{city}-{vertical}", monkeypatch, city, state, vertical)
        assert result.get("permit_name_source_field") not in {"display_permit_name", "official_permit_name"}
        matched_fields = set((result.get("_evidence_pack") or {}).get("matched_fields", []))
        assert not ({"display_permit_name", "official_permit_name"} & matched_fields)
        assert result.get("permit_name") != blocked_name
        assert result.get("permit_type") != blocked_name


def test_phase1b_residential_no_regression_no_commercial_ti_names(tmp_path, monkeypatch):
    commercial_names = {
        record["claim_value"]
        for record in json.loads(PHASE1B_PACK.read_text(encoding="utf-8"))["records"]
    }
    cases = [
        ("San Diego", "CA", "residential kitchen remodel"),
        ("Dallas", "TX", "residential bathroom remodel"),
        ("City of Miami", "FL", "residential window replacement"),
        ("Boston", "MA", "residential deck repair"),
    ]
    for city, state, job_type in cases:
        base = _base_engine_result()
        base["permit_name"] = "Residential Building Permit"
        base["permit_type"] = "Residential Building Permit"
        base["permits_required"] = [{"permit_type": "Residential Building Permit"}]
        result = _finalize(
            tmp_path / f"residential-{state}-{city}".replace(" ", "_"),
            monkeypatch,
            city,
            state,
            "residential",
            job_type=job_type,
            base=base,
        )
        text = json.dumps(result, sort_keys=True)
        assert (result.get("_evidence_pack") or {}).get("request_vertical") == "residential"
        matched_fields = set((result.get("_evidence_pack") or {}).get("matched_fields", []))
        assert not ({"display_permit_name", "official_permit_name"} & matched_fields)
        assert result.get("final_answer_state") == "OFFICIAL_SOURCE_RETRIEVAL_REQUIRED"
        assert result.get("permit_name") is None
        assert result.get("permit_type") is None
        assert result.get("_permit_display_name") == "Residential Building Permit"
        assert result.get("permit_type_verified") is False
        assert "exact permit type needs AHJ verification" not in text
        assert not any(name in text for name in commercial_names)


def test_phase1b_preview_mode_requires_preview_token(tmp_path, monkeypatch):
    _enable_phase1b_pack(monkeypatch)
    server = _import_server(tmp_path, monkeypatch)
    assert server.evidence_pack_allowed_for_request("/api/permit", {"X-Sample-Demo": "1"}, is_sample_demo=True) is False
    assert server.evidence_pack_allowed_for_request(
        "/api/permit",
        {"X-Sample-Demo": "1", "X-Phase1B-Exact-Name-Preview-Token": "phase1b-test-token"},
        is_sample_demo=True,
    ) is True
    assert server.evidence_pack_allowed_for_request("/api/batch-permit", {"X-Phase1B-Exact-Name-Preview-Token": "phase1b-test-token"}) is False
