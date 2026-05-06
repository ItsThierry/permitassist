import copy
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from importlib import util
from pathlib import Path

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper_step7c",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_LiveServer = _debug_helper._LiveServer
_import_server = _debug_helper._import_server
_post_json = _debug_helper._post_json


def _post_json_response(url, body, headers=None):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _base_engine_result():
    return {
        "permit_verdict": "YES",
        "confidence": "high",
        "permits_required": [{"permit_type": "Building Permit — Tenant Improvement"}],
        "fee_range": "$500-$1,000",
        "approval_timeline": "2-4 weeks",
        "inspections": ["final building"],
        "apply_url": "",
        "apply_phone": "",
        "sources": ["https://denvergov.org/generic-permits"],
        "checklist": [],
        "rejection_patterns": [],
    }


def _write_pack(path: Path) -> Path:
    pack = {
        "metadata": {
            "evidence_pack_version": "step7b_offline_v1_test",
            "fingerprint_sha256": "f" * 64,
            "production_wiring_allowed": False,
        },
        "validation": {"verdict": "FAIL_CLOSED_NOT_INGESTION_READY"},
        "records": [
            {
                "record_id": "denver-office-companion-ready",
                "state": "CO",
                "ahj_name": "City of Denver",
                "vertical": "office_ti",
                "field": "companion_reviews_triggers",
                "claim_value": "Office TI may require zoning, fire/life-safety, accessibility, and MEP companion reviews depending on scope.",
                "field_status": "verified",
                "confidence": "high",
                "ingestion_ready": True,
                "source_scope_limit": "Verifies companion-review trigger warning only; does not verify local fees, timeline, apply URL, permit type, or inspections.",
                "record_fingerprint_sha256": "a" * 64,
                "field_evidence": [
                    {
                        "source_url": "https://denvergov.org/official-office-ti-review",
                        "source_title": "Denver Official Office TI Review",
                        "exact_quote_or_snippet": "Tenant finish projects may require zoning, building, fire, accessibility, and trade review.",
                        "quote_found": True,
                        "last_verified_utc": "2026-05-05T12:00:00Z",
                        "stale_after_utc": "2027-05-05T12:00:00Z",
                        "reverify_after_utc": "2027-04-05T12:00:00Z",
                    }
                ],
            },
            {
                "record_id": "denver-office-apply-not-ready",
                "state": "CO",
                "ahj_name": "City of Denver",
                "vertical": "office_ti",
                "field": "apply_url",
                "claim_value": "https://denvergov.org/not-ready",
                "ingestion_ready": False,
                "field_evidence": [],
            },
            {
                "record_id": "denver-ahj-level-timeline-ready",
                "state": "CO",
                "ahj_name": "City of Denver",
                "vertical": "ahj_level",
                "field": "approval_timeline",
                "claim_value": "Denver building-permit review has an official AHJ-level target timeline; verify scope-specific queues before relying on it.",
                "field_status": "verified",
                "confidence": "high",
                "ingestion_ready": True,
                "source_scope_limit": "AHJ-level approval timeline only; not vertical-specific and not proof of fees, permit type, apply URL, inspections, or companion reviews.",
                "record_fingerprint_sha256": "c" * 64,
                "field_evidence": [
                    {
                        "source_url": "https://denvergov.org/official-timeline",
                        "source_title": "Denver Official Review Timeline",
                        "exact_quote_or_snippet": "Most building permit reviews receive initial comments within a published target timeframe.",
                        "quote_found": True,
                        "last_verified_utc": "2026-05-05T12:00:00Z",
                        "stale_after_utc": "2027-05-05T12:00:00Z",
                        "reverify_after_utc": "2027-04-05T12:00:00Z",
                    }
                ],
            },
        ],
    }
    path.write_text(json.dumps(pack, indent=2), encoding="utf-8")
    return path


def _enable_pack(monkeypatch, pack_path: Path, expected_fingerprint: str | None = None, mode: str = "dallas_step7h_preview"):
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    fingerprint = expected_fingerprint or (data.get("metadata") or {}).get("fingerprint_sha256") or ("f" * 64)
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_ENABLED", "true")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_MODE", mode)
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_EXPECTED_FINGERPRINT", fingerprint)
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PATH", str(pack_path))


def test_step7m_dallas_only_staging_pack_is_supported_by_runtime_contract(monkeypatch, tmp_path):
    pack_path = Path(__file__).resolve().parents[1] / "evidence_packs" / "dallas" / "step7h" / "permitassist-step7h-dallas-only-staging-evidence-pack-20260505.json"
    assert pack_path.exists()
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(
        _base_engine_result(),
        "office tenant improvement with demising partitions, lighting controls, HVAC diffuser relocation, and accessibility review",
        "Dallas",
        "TX",
        explicit_vertical="office_ti",
    )

    meta = result["_evidence_pack"]
    assert meta["contract_status"] == "valid"
    assert meta["evidence_pack_version"] == "step7h_dallas_only_staging_v1"
    assert meta["fingerprint_valid"] is True
    assert meta["request_vertical"] == "office_ti"
    assert set(meta["matched_fields"]) == {"apply_url", "approval_timeline", "companion_reviews_triggers", "permit_type"}
    assert set(meta["failed_closed_fields"]) == {"fee_range", "inspections"}


def test_step7u_production_preview_pack_requires_dedicated_mode(monkeypatch, tmp_path):
    pack_path = Path(__file__).resolve().parents[1] / "evidence_packs" / "dallas" / "step7u" / "permitassist-step7u-dallas-only-production-preview-evidence-pack-20260506.json"
    assert pack_path.exists()

    _enable_pack(monkeypatch, pack_path, mode="dallas_step7u_production_preview")
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(
        _base_engine_result(),
        "medical clinic tenant improvement with exam rooms and accessibility review",
        "Dallas",
        "TX",
        explicit_vertical="medical_clinic_ti",
    )

    meta = result["_evidence_pack"]
    assert meta["contract_status"] == "valid"
    assert meta["evidence_pack_version"] == "step7u_dallas_only_production_preview_v1"
    assert meta["fingerprint_valid"] is True
    assert meta["request_vertical"] == "medical_clinic_ti"
    assert set(meta["matched_fields"]) == {"apply_url", "approval_timeline", "companion_reviews_triggers", "permit_type"}
    assert set(meta["failed_closed_fields"]) == {"fee_range", "inspections"}


def test_step7u_production_preview_pack_fails_closed_in_staging_mode(monkeypatch, tmp_path):
    pack_path = Path(__file__).resolve().parents[1] / "evidence_packs" / "dallas" / "step7u" / "permitassist-step7u-dallas-only-production-preview-evidence-pack-20260506.json"
    _enable_pack(monkeypatch, pack_path, mode="dallas_step7h_preview")
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Dallas", "TX", explicit_vertical="office_ti")

    meta = result["_evidence_pack"]
    assert meta["contract_status"] == "invalid_version"
    assert meta["matched_fields"] == []
    assert result["claim_citations"] == []


def test_step7h_staging_pack_fails_closed_in_production_preview_mode(monkeypatch, tmp_path):
    pack_path = Path(__file__).resolve().parents[1] / "evidence_packs" / "dallas" / "step7h" / "permitassist-step7h-dallas-only-staging-evidence-pack-20260505.json"
    _enable_pack(monkeypatch, pack_path, mode="dallas_step7u_production_preview")
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Dallas", "TX", explicit_vertical="office_ti")

    meta = result["_evidence_pack"]
    assert meta["contract_status"] == "invalid_version"
    assert meta["matched_fields"] == []
    assert result["claim_citations"] == []


def test_step7u_production_preview_rejects_supported_but_wrong_pack_version(monkeypatch, tmp_path):
    source_path = Path(__file__).resolve().parents[1] / "evidence_packs" / "dallas" / "step7u" / "permitassist-step7u-dallas-only-production-preview-evidence-pack-20260506.json"
    pack_path = tmp_path / "step7u-with-step7b-version.json"
    data = json.loads(source_path.read_text(encoding="utf-8"))
    data["metadata"]["evidence_pack_version"] = "step7b_offline_v1"
    data["metadata"]["production_wiring_allowed"] = False
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _enable_pack(monkeypatch, pack_path, mode="dallas_step7u_production_preview")
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Dallas", "TX", explicit_vertical="office_ti")

    meta = result["_evidence_pack"]
    assert meta["contract_status"] == "invalid_version"
    assert meta["matched_fields"] == []
    assert result["claim_citations"] == []


def test_step7u_production_preview_pack_requires_production_wiring_flag(monkeypatch, tmp_path):
    source_path = Path(__file__).resolve().parents[1] / "evidence_packs" / "dallas" / "step7u" / "permitassist-step7u-dallas-only-production-preview-evidence-pack-20260506.json"
    pack_path = tmp_path / "step7u-without-production-wiring.json"
    data = json.loads(source_path.read_text(encoding="utf-8"))
    data["metadata"]["production_wiring_allowed"] = False
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _enable_pack(monkeypatch, pack_path, mode="dallas_step7u_production_preview")
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Dallas", "TX", explicit_vertical="office_ti")

    meta = result["_evidence_pack"]
    assert meta["contract_status"] == "invalid_production_wiring"
    assert meta["matched_fields"] == []



def test_step7l_enabled_pack_exposes_schema_version_contract_metadata(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

    meta = result["_evidence_pack"]
    assert meta["contract_schema"] == "permitassist.evidence_pack.runtime.v1"
    assert meta["runtime_contract_version"] == "step7l_runtime_contract_v1"
    assert meta["contract_status"] == "valid"
    assert meta["evidence_pack_version"] == "step7b_offline_v1_test"
    assert meta["fingerprint_valid"] is True



def test_step7l_enabled_pack_fails_closed_for_unsupported_pack_version(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    data["metadata"]["evidence_pack_version"] = "future_unreviewed_schema_v99"
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

    meta = result["_evidence_pack"]
    assert meta["contract_status"] == "invalid_version"
    assert meta["matched_fields"] == []
    assert set(meta["failed_closed_fields"]) == {"permit_type", "apply_url", "fee_range", "approval_timeline", "inspections", "companion_reviews_triggers"}
    assert result["approval_timeline"] is None
    assert result["companion_reviews_triggers"] is None
    assert result["claim_citations"] == []



def test_step7l_enabled_pack_fails_closed_when_metadata_fingerprint_missing_or_invalid(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    data["metadata"].pop("fingerprint_sha256", None)
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

    meta = result["_evidence_pack"]
    assert meta["contract_status"] == "invalid_fingerprint"
    assert meta["fingerprint_valid"] is False
    assert meta["matched_fields"] == []
    assert result["claim_citations"] == []



def test_step7l_enabled_pack_fails_closed_when_production_wiring_flag_is_true(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    data["metadata"]["production_wiring_allowed"] = True
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

    meta = result["_evidence_pack"]
    assert meta["contract_status"] == "invalid_production_wiring"
    assert meta["matched_fields"] == []
    assert result["claim_citations"] == []



def test_step7l_enabled_pack_fails_closed_for_truthy_production_wiring_values(tmp_path, monkeypatch):
    for idx, truthy_value in enumerate((1, "true", "yes"), 1):
        pack_path = _write_pack(tmp_path / f"pack-truthy-{idx}.json")
        data = json.loads(pack_path.read_text(encoding="utf-8"))
        data["metadata"]["production_wiring_allowed"] = truthy_value
        pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _enable_pack(monkeypatch, pack_path)
        server = _import_server(tmp_path / f"server-truthy-{idx}", monkeypatch)

        result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

        meta = result["_evidence_pack"]
        assert meta["contract_status"] == "invalid_production_wiring"
        assert meta["matched_fields"] == []



def test_step7l_enabled_pack_accumulates_combined_contract_warnings_deterministically(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    data["metadata"]["evidence_pack_version"] = "future_unreviewed_schema_v99"
    data["metadata"].pop("fingerprint_sha256", None)
    data["metadata"]["production_wiring_allowed"] = "true"
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

    assert result["_evidence_pack"]["contract_status"] == "invalid_version"
    assert result["_evidence_pack"]["matched_fields"] == []
    assert result["claim_citations"] == []



def test_disabled_default_behavior_preserves_legacy_source_apply_url_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("PERMITASSIST_EVIDENCE_PACK_PATH", raising=False)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

    assert result["apply_url"] == "https://denvergov.org/generic-permits"
    assert "_evidence_pack" not in result
    assert result["claim_citations"]
    assert result["claim_citations"][0]["source_url"] == "https://denvergov.org/generic-permits"
    assert result["apply_path"]


def test_enabled_local_pack_fails_closed_missing_field_evidence_and_blocks_sources0_fallback(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    engine_result = _base_engine_result()
    engine_result["apply_url"] = "https://aca-prod.accela.com/DENVER/stale-portal"
    engine_result["inspection_booking"] = "Schedule online at https://aca-prod.accela.com/DENVER/stale-portal with 24 hours notice."
    result = server.finalize_permit_lookup_result(engine_result, "office tenant improvement", "Denver", "CO")

    meta = result["_evidence_pack"]
    assert meta["enabled"] is True
    assert meta["matched_fields"] == ["approval_timeline", "companion_reviews_triggers"]
    assert "apply_url" in meta["failed_closed_fields"]
    assert result["apply_url"] is None
    assert result["inspection_booking"] is None
    assert result["fee_range"] is None
    assert result["approval_timeline"].startswith("Denver building-permit review")
    assert result["inspections"] is None
    assert result["companion_reviews_triggers"].startswith("Office TI may require")
    assert [c["field"] for c in result["claim_citations"]] == ["companion_reviews_triggers", "approval_timeline"]
    assert {c["source_url"] for c in result["claim_citations"]} == {
        "https://denvergov.org/official-office-ti-review",
        "https://denvergov.org/official-timeline",
    }
    assert all("generic-permits" not in json.dumps(c) for c in result["claim_citations"])
    assert result["apply_path"]["support_level"] == "not available"
    assert result["apply_path"]["portal_url"] in (None, "")
    assert "Open the unknown start URL" not in json.dumps(result["apply_path"])
    assert "No verified online filing path" in result["apply_path"]["steps"][0]


def test_permit_endpoint_bypasses_cache_when_local_pack_enabled(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)
    calls = []

    def fake_research(*args, **kwargs):
        calls.append(kwargs)
        return copy.deepcopy(_base_engine_result())

    server.research_permit = fake_research
    token = server.create_session_token("paid@example.com")
    server.is_paid_user = lambda email: True

    with _LiveServer(server.Handler) as live:
        status, body = _post_json_response(
            f"{live.base}/api/permit",
            {"job_type": "office tenant improvement", "city": "Denver", "state": "CO", "job_category": "commercial"},
            {"X-Session-Token": token},
        )

    assert status == 200
    assert calls and calls[0]["use_cache"] is False
    assert calls[0]["suppress_cache_write"] is True
    assert body["_evidence_pack"]["cache_bypassed"] is True
    assert body["apply_url"] is None


def test_enabled_local_pack_endpoint_parity_for_permit_batch_and_v1(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)
    calls = []

    def fake_research(*args, **kwargs):
        calls.append(kwargs)
        return copy.deepcopy(_base_engine_result())

    server.research_permit = fake_research
    server.is_paid_user = lambda email: True
    server.validate_api_key = lambda auth: ("paid@example.com", {"name": "test"})
    token = server.create_session_token("paid@example.com")
    request_body = {"job_type": "office tenant improvement", "city": "Denver", "state": "CO", "job_category": "commercial"}

    with _LiveServer(server.Handler) as live:
        permit_status, permit_body = _post_json_response(
            f"{live.base}/api/permit",
            request_body,
            {"X-Session-Token": token},
        )
        batch_status, batch_body = _post_json_response(
            f"{live.base}/api/batch-permit",
            {"lookups": [request_body]},
        )
        v1_status, v1_body = _post_json_response(
            f"{live.base}/api/v1/permit",
            request_body,
            {"Authorization": "Bearer test-key"},
        )

    batch_result = batch_body["results"][0]
    assert permit_status == batch_status == v1_status == 200
    for body in (permit_body, batch_result, v1_body):
        assert body["apply_url"] is None
        assert body["fee_range"] is None
        assert body["approval_timeline"].startswith("Denver building-permit review")
        assert body["companion_reviews_triggers"].startswith("Office TI may require")
        assert [c["field"] for c in body["claim_citations"]] == ["companion_reviews_triggers", "approval_timeline"]
        assert body["_evidence_pack"]["matched_fields"] == ["approval_timeline", "companion_reviews_triggers"]
        assert "apply_url" in body["_evidence_pack"]["failed_closed_fields"]
        assert body["apply_path"]["support_level"] == "not available"
    assert all(call.get("use_cache") is False for call in calls)
    assert all(call.get("suppress_cache_write") is True for call in calls)


def test_enabled_pack_rejects_malformed_evidence_records(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    data["records"][0]["field_evidence"] = [{}]
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

    assert result["_evidence_pack"]["matched_fields"] == ["approval_timeline"]
    assert result["claim_citations"] == [
        c for c in result["claim_citations"] if c["field"] == "approval_timeline"
    ]
    assert "companion_reviews_triggers" in result["_evidence_pack"]["failed_closed_fields"]


def test_enabled_pack_apply_url_is_sanitized_after_overlay(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    data["records"].append({
        "record_id": "denver-office-apply-ready-unsafe",
        "state": "CO",
        "ahj_name": "City of Denver",
        "vertical": "office_ti",
        "field": "apply_url",
        "claim_value": "javascript:alert(1)",
        "field_status": "verified",
        "confidence": "high",
        "ingestion_ready": True,
        "source_scope_limit": "Verifies apply URL only.",
        "record_fingerprint_sha256": "b" * 64,
        "field_evidence": [{
            "source_url": "https://denvergov.org/official-office-ti-portal",
            "source_title": "Denver Official Portal",
            "exact_quote_or_snippet": "Apply through the official permitting portal.",
            "quote_found": True,
            "stale_after_utc": "2027-05-05T12:00:00Z",
            "reverify_after_utc": "2027-04-05T12:00:00Z",
        }],
    })
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

    assert "apply_url" in result["_evidence_pack"]["matched_fields"]
    assert result["apply_url"] is None
    assert result["apply_path"]["portal_url"] in (None, "")
    assert "javascript:" not in json.dumps(result)



def test_enabled_pack_enforces_stale_after_and_reverify_runtime_freshness(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    data["records"][0]["stale_after_utc"] = "2020-01-01T00:00:00Z"
    data["records"][2]["field_evidence"][0]["reverify_after_utc"] = "2020-01-01T00:00:00Z"
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

    meta = result["_evidence_pack"]
    assert meta["contract_status"] == "stale"
    assert meta["matched_fields"] == []
    assert "companion_reviews_triggers" in meta["failed_closed_fields"]
    assert "approval_timeline" in meta["failed_closed_fields"]
    assert result["companion_reviews_triggers"] is None
    assert result["approval_timeline"] is None
    assert result["claim_citations"] == []


def test_enabled_pack_fails_closed_on_malformed_freshness_timestamp(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    data["records"][0]["field_evidence"][0]["stale_after_utc"] = "not-a-date"
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

    assert result["_evidence_pack"]["matched_fields"] == ["approval_timeline"]
    assert result["companion_reviews_triggers"] is None


def test_enabled_pack_fails_closed_when_freshness_cutoff_missing(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    evidence = data["records"][0]["field_evidence"][0]
    evidence.pop("stale_after_utc", None)
    evidence.pop("reverify_after_utc", None)
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

    assert result["_evidence_pack"]["matched_fields"] == ["approval_timeline"]
    assert result["companion_reviews_triggers"] is None


def test_enabled_pack_fails_closed_when_cutoff_equals_now_boundary(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    data["records"][0]["field_evidence"][0]["stale_after_utc"] = now.isoformat().replace("+00:00", "Z")
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

    assert result["_evidence_pack"]["matched_fields"] == ["approval_timeline"]
    assert result["companion_reviews_triggers"] is None


def test_enabled_pack_rechecks_freshness_after_cached_pack_cutoff_passes(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    cutoff = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    data["records"][0]["stale_after_utc"] = cutoff
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    first = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")
    assert "companion_reviews_triggers" in first["_evidence_pack"]["matched_fields"]

    time.sleep(1.2)
    second = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")
    assert second["_evidence_pack"]["matched_fields"] == ["approval_timeline"]
    assert second["companion_reviews_triggers"] is None


def test_enabled_pack_rejects_wrong_ahj_substring_match(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    data["records"].append({
        "record_id": "south-dallas-office-apply-ready",
        "state": "CO",
        "ahj_name": "City of South Denver",
        "vertical": "office_ti",
        "field": "apply_url",
        "claim_value": "https://southdenver.example.gov/portal",
        "field_status": "verified",
        "confidence": "high",
        "ingestion_ready": True,
        "record_fingerprint_sha256": "d" * 64,
        "field_evidence": [{
            "source_url": "https://southdenver.example.gov/permits",
            "source_title": "South Denver Permits",
            "exact_quote_or_snippet": "Apply for South Denver permits through the online portal.",
            "quote_found": True,
            "last_verified_utc": "2026-05-05T12:00:00Z",
            "stale_after_utc": "2027-05-05T12:00:00Z",
            "reverify_after_utc": "2027-04-05T12:00:00Z",
        }],
    })
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

    assert "apply_url" in result["_evidence_pack"]["failed_closed_fields"]
    assert "apply_url" not in result["_evidence_pack"]["matched_fields"]
    assert result["apply_url"] is None
    assert "southdenver" not in json.dumps(result)


def test_enabled_pack_accepts_official_ahj_suffix_variants_without_substring_leakage(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    data["records"][0]["ahj_name"] = "Denver County"
    data["records"][2]["ahj_name"] = "County of Denver"
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

    assert result["_evidence_pack"]["matched_fields"] == ["approval_timeline", "companion_reviews_triggers"]
    assert result["companion_reviews_triggers"].startswith("Office TI may require")
    assert result["approval_timeline"].startswith("Denver building-permit review")


def test_enabled_pack_accepts_city_county_borough_parish_township_municipality_variants(tmp_path, monkeypatch):
    ahj_pairs = [
        ("City and County of Denver", "Denver"),
        ("Denver Borough", "Denver"),
        ("Parish of Denver", "Denver"),
        ("Denver Township", "Denver"),
        ("Municipality of Denver", "Denver"),
    ]
    for idx, (record_ahj, request_city) in enumerate(ahj_pairs):
        pack_path = _write_pack(tmp_path / f"pack-{idx}.json")
        data = json.loads(pack_path.read_text(encoding="utf-8"))
        data["records"][0]["ahj_name"] = record_ahj
        data["records"][2]["ahj_name"] = record_ahj
        pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _enable_pack(monkeypatch, pack_path)
        server = _import_server(tmp_path / f"server-{idx}", monkeypatch)

        result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", request_city, "CO")

        assert result["_evidence_pack"]["matched_fields"] == ["approval_timeline", "companion_reviews_triggers"]
        assert result["companion_reviews_triggers"].startswith("Office TI may require")


def test_enabled_pack_ignores_ahj_level_as_explicit_vertical_override(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(
        _base_engine_result(),
        "tenant improvement buildout with partitions and lighting",
        "Denver",
        "CO",
        explicit_vertical="ahj_level",
    )

    meta = result["_evidence_pack"]
    assert meta["request_vertical"] == "unknown"
    assert meta["matched_fields"] == []
    assert result["approval_timeline"] is None
    assert result["companion_reviews_triggers"] is None


def test_enabled_pack_ignores_result_supplied_ahj_level_vertical(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)
    engine_result = _base_engine_result()
    engine_result["evidence_vertical"] = "AHJ_LEVEL"

    result = server.finalize_permit_lookup_result(engine_result, "office tenant improvement", "Denver", "CO")

    assert result["_evidence_pack"]["request_vertical"] == "office_ti"
    assert result["_evidence_pack"]["matched_fields"] == ["approval_timeline", "companion_reviews_triggers"]


def test_enabled_pack_uses_explicit_vertical_when_job_type_has_no_vertical_keywords(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(
        _base_engine_result(),
        "tenant improvement buildout with partitions and lighting",
        "Denver",
        "CO",
        explicit_vertical="office_ti",
    )

    meta = result["_evidence_pack"]
    assert meta["request_vertical"] == "office_ti"
    assert meta["matched_fields"] == ["approval_timeline", "companion_reviews_triggers"]
    assert result["companion_reviews_triggers"].startswith("Office TI may require")


def test_enabled_pack_accepts_mixed_case_explicit_vertical_alias(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(
        _base_engine_result(),
        "tenant improvement buildout with partitions and lighting",
        "Denver",
        "CO",
        explicit_vertical="OfFiCe TI",
    )

    assert result["_evidence_pack"]["request_vertical"] == "office_ti"
    assert result["_evidence_pack"]["matched_fields"] == ["approval_timeline", "companion_reviews_triggers"]
    assert result["companion_reviews_triggers"].startswith("Office TI may require")


def test_enabled_pack_rechecks_all_field_evidence_freshness_not_only_first_item(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    data["records"][0]["field_evidence"].append({
        "source_url": "https://denvergov.org/secondary-office-ti-review",
        "source_title": "Denver Secondary Office TI Review",
        "exact_quote_or_snippet": "Secondary evidence must also remain fresh.",
        "quote_found": True,
        "reverify_after_utc": "2020-01-01T00:00:00Z",
    })
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

    assert result["_evidence_pack"]["matched_fields"] == ["approval_timeline"]
    assert result["companion_reviews_triggers"] is None


def test_enabled_pack_rejects_prefix_and_same_state_ahj_collisions(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    data["records"].append({
        "record_id": "denver-north-office-apply-ready",
        "state": "CO",
        "ahj_name": "Denver North",
        "vertical": "office_ti",
        "field": "apply_url",
        "claim_value": "https://denvernorth.example.gov/portal",
        "field_status": "verified",
        "confidence": "high",
        "ingestion_ready": True,
        "record_fingerprint_sha256": "e" * 64,
        "field_evidence": [{
            "source_url": "https://denvernorth.example.gov/permits",
            "source_title": "Denver North Permits",
            "exact_quote_or_snippet": "Apply for Denver North permits through the online portal.",
            "quote_found": True,
            "last_verified_utc": "2026-05-05T12:00:00Z",
            "stale_after_utc": "2027-05-05T12:00:00Z",
            "reverify_after_utc": "2027-04-05T12:00:00Z",
        }],
    })
    data["records"].append({
        "record_id": "north-denver-office-fee-ready",
        "state": "CO",
        "ahj_name": "North Denver",
        "vertical": "office_ti",
        "field": "fee_range",
        "claim_value": "$2,000 minimum for North Denver office TI",
        "field_status": "verified",
        "confidence": "high",
        "ingestion_ready": True,
        "record_fingerprint_sha256": "f" * 64,
        "field_evidence": [{
            "source_url": "https://northdenver.example.gov/fees",
            "source_title": "North Denver Fees",
            "exact_quote_or_snippet": "North Denver office tenant improvement fees start at $2,000.",
            "quote_found": True,
            "last_verified_utc": "2026-05-05T12:00:00Z",
            "stale_after_utc": "2027-05-05T12:00:00Z",
            "reverify_after_utc": "2027-04-05T12:00:00Z",
        }],
    })
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

    assert "apply_url" in result["_evidence_pack"]["failed_closed_fields"]
    assert "fee_range" in result["_evidence_pack"]["failed_closed_fields"]
    assert "apply_url" not in result["_evidence_pack"]["matched_fields"]
    assert "fee_range" not in result["_evidence_pack"]["matched_fields"]
    assert "denvernorth" not in json.dumps(result)
    assert "northdenver" not in json.dumps(result)


def test_permit_endpoint_whitelists_client_supplied_vertical(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    def fake_research(*args, **kwargs):
        return copy.deepcopy(_base_engine_result())

    server.research_permit = fake_research
    token = server.create_session_token("paid@example.com")
    server.is_paid_user = lambda email: True
    server.validate_api_key = lambda auth: ("paid@example.com", {"name": "test"})
    request_body = {
        "job_type": "tenant improvement buildout with partitions and lighting",
        "city": "Denver",
        "state": "CO",
        "job_category": "commercial",
        "vertical": "AHJ_LEVEL",
        "evidence_vertical": "OfFiCe TI",
    }

    with _LiveServer(server.Handler) as live:
        status, body = _post_json_response(
            f"{live.base}/api/permit",
            request_body,
            {"X-Session-Token": token},
        )
        batch_status, batch_body = _post_json_response(
            f"{live.base}/api/batch-permit",
            {"lookups": [request_body]},
        )
        v1_status, v1_body = _post_json_response(
            f"{live.base}/api/v1/permit",
            request_body,
            {"Authorization": "Bearer test-key"},
        )

    assert status == batch_status == v1_status == 200
    for response in (body, batch_body["results"][0], v1_body):
        assert response["_evidence_pack"]["request_vertical"] != "ahj_level"
        assert response["_evidence_pack"]["request_vertical"] == "office_ti"
        assert response["_evidence_pack"]["matched_fields"] == ["approval_timeline", "companion_reviews_triggers"]

def test_ui_exposure_gate_adds_warnings_and_blocks_unverified_inspection_booking(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    data["records"].append(
        {
            "record_id": "denver-office-apply-ready",
            "state": "CO",
            "ahj_name": "City of Denver",
            "vertical": "office_ti",
            "field": "apply_url",
            "claim_value": "https://aca-prod.accela.com/DENVER/Default.aspx",
            "field_status": "verified",
            "confidence": "high",
            "ingestion_ready": True,
            "source_scope_limit": "Verifies portal start URL only; exact subcategory and filing path still require AHJ verification.",
            "record_fingerprint_sha256": "d" * 64,
            "field_evidence": [
                {
                    "source_url": "https://denvergov.org/official-portal",
                    "source_title": "Denver Official Portal",
                    "exact_quote_or_snippet": "Start online permit applications in the official portal.",
                    "quote_found": True,
                    "last_verified_utc": "2026-05-05T12:00:00Z",
                    "stale_after_utc": "2027-05-05T12:00:00Z",
                    "reverify_after_utc": "2027-04-05T12:00:00Z",
                }
            ],
        }
    )
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    engine_result = _base_engine_result()
    engine_result["inspection_booking"] = "Schedule online at stale upstream portal."
    result = server.finalize_permit_lookup_result(engine_result, "office tenant improvement", "Denver", "CO")

    warnings = "\n".join(result.get("quality_warnings") or [])
    assert result["apply_url"] == "https://aca-prod.accela.com/DENVER/Default.aspx"
    assert result["inspection_booking"] is None
    assert "inspections" in result["_evidence_pack"]["failed_closed_fields"]
    assert "fee_range" in result["_evidence_pack"]["failed_closed_fields"]
    assert "Inspection booking steps are not shown" in warnings
    assert "fee range is not verified" in warnings
    assert "outer-deadline evidence only" in warnings
    assert "queue estimate" in warnings
    assert "exact portal subcategory/filing path" in warnings
    assert "not a complete local health" in warnings
    assert "exact portal subcategory" in result["apply_path"]["verification_note"]


def test_white_label_report_surfaces_evidence_pack_limits(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    html = server.render_white_label_report_html(
        {
            "contractor_name": "ACME",
            "client_name": "Client",
            "job_type": "restaurant tenant improvement",
            "city": "Dallas",
            "state": "TX",
            "result": {
                "permits_required": [{"permit_type": "Commercial Building Permit"}],
                "apply_url": "https://aca-prod.accela.com/DALLASTX/Default.aspx",
                "apply_path": {"verification_note": "DallasNow/DALLASTX start portal only; exact portal subcategory requires AHJ verification."},
                "quality_warnings": [],
                "claim_citations": [],
                "_evidence_pack": {
                    "enabled": True,
                    "matched_fields": ["apply_url", "approval_timeline", "companion_reviews_triggers"],
                    "failed_closed_fields": ["fee_range", "inspections"],
                },
            },
        }
    )

    assert "Local evidence pack failed closed for: fee_range, inspections" in html
    assert "Timeline is statutory/AHJ outer-deadline evidence only, not a local queue estimate" in html
    assert "not a complete local health" in html
    assert "exact portal subcategory requires AHJ verification" in html



def test_step7p_path_alone_does_not_activate_evidence_pack(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    monkeypatch.delenv("PERMITASSIST_EVIDENCE_PACK_ENABLED", raising=False)
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PATH", str(pack_path))
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

    assert "_evidence_pack" not in result
    assert result["apply_url"] == "https://denvergov.org/generic-permits"


def test_step7p_invalid_mode_fails_closed_without_paths_or_full_hash(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_ENABLED", "true")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_MODE", "prod_or_unknown")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_EXPECTED_FINGERPRINT", "f" * 64)
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PATH", str(pack_path))
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")
    meta = result["_evidence_pack"]

    assert meta["contract_status"] == "invalid_contract"
    assert meta["fingerprint_valid"] is False
    assert meta["fingerprint_prefix"] == ""
    assert meta["matched_fields"] == []
    dumped = json.dumps(result, sort_keys=True)
    assert str(pack_path) not in dumped
    assert "f" * 64 not in dumped
    assert "PERMITASSIST_EVIDENCE_PACK_PATH" not in dumped


def test_step7p_expected_fingerprint_mismatch_fails_closed(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    _enable_pack(monkeypatch, pack_path, expected_fingerprint="e" * 64)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")
    meta = result["_evidence_pack"]

    assert meta["contract_status"] == "invalid_fingerprint"
    assert meta["fingerprint_valid"] is False
    assert meta["fingerprint_prefix"] == ""
    assert result["claim_citations"] == []
    assert result["approval_timeline"] is None


def test_step7p_safe_diagnostics_have_no_path_full_fingerprint_or_contract_internals(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")
    meta = result["_evidence_pack"]

    assert meta["enabled"] is True
    assert meta["fingerprint_valid"] is True
    assert meta["fingerprint_prefix"] == "f" * 12
    assert "path" not in meta
    assert "fingerprint_sha256" not in meta
    assert "evidence_pack_fingerprint" not in meta
    assert "contract_warnings" not in meta
    assert "production_wiring_allowed" not in meta
    assert "f" * 64 not in json.dumps(meta, sort_keys=True)


def test_step7p_unexpected_cached_result_fails_closed_invalid_contract(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)
    engine_result = _base_engine_result()
    engine_result["_cached"] = True

    result = server.finalize_permit_lookup_result(engine_result, "office tenant improvement", "Denver", "CO", is_cached=True)
    meta = result["_evidence_pack"]

    assert meta["contract_status"] == "invalid_contract"
    assert meta["matched_fields"] == []
    assert result["claim_citations"] == []
    assert result["approval_timeline"] is None
    assert result["companion_reviews_triggers"] is None


def test_step7p_public_redaction_removes_paths_env_railway_tokens_and_hashes(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    sensitive_home = "/home/" + "boban/projects/permitassist/private-pack.json"
    token_like = "sk-" + "testtokenvalue1234567890"
    payload = {
        "_evidence_pack": {
            "path": sensitive_home,
            "fingerprint_sha256": "a" * 64,
            "nested": f"PERMITASSIST_EVIDENCE_PACK_PATH={sensitive_home} {token_like} RAILWAY_PRIVATE_DOMAIN",
        },
        "railway": "RAILWAY_SERVICE_ID",
        "app_path": "/app/.railway/private/file.json",
        "citation": {"evidence_pack_fingerprint": "b" * 64},
    }

    redacted = server.redact_public_output(payload)
    dumped = json.dumps(redacted, sort_keys=True)

    assert "/home/" not in dumped
    assert "/app/" not in dumped
    assert "PERMITASSIST_EVIDENCE_PACK_PATH" not in dumped
    assert "RAILWAY_SERVICE_ID" not in dumped
    assert "RAILWAY_PRIVATE_DOMAIN" not in dumped
    assert token_like not in dumped
    assert "a" * 64 not in dumped
    assert "b" * 64 not in dumped
    assert dumped.count("[REDACTED]") >= 5


def test_step7p_preview_indexing_guards_for_robots_sitemap_and_json(tmp_path, monkeypatch):
    pack_path = _write_pack(tmp_path / "pack.json")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)

    with _LiveServer(server.Handler) as live:
        root_req = urllib.request.Request(f"{live.base}/")
        with urllib.request.urlopen(root_req, timeout=5) as resp:
            resp.read()
            assert resp.headers.get("X-Robots-Tag") == "noindex, nofollow"
        robots_req = urllib.request.Request(f"{live.base}/robots.txt")
        with urllib.request.urlopen(robots_req, timeout=5) as resp:
            robots_body = resp.read().decode("utf-8")
            assert resp.headers.get("X-Robots-Tag") == "noindex, nofollow"
            assert resp.headers.get("Cache-Control") == "no-store"
        sitemap_req = urllib.request.Request(f"{live.base}/sitemap.xml")
        with urllib.request.urlopen(sitemap_req, timeout=5) as resp:
            sitemap_body = resp.read().decode("utf-8")
            assert resp.headers.get("X-Robots-Tag") == "noindex, nofollow"
            assert resp.headers.get("Cache-Control") == "no-store"
        status, body = _post_json_response(
            f"{live.base}/api/batch-permit",
            {"lookups": [{"job_type": "office tenant improvement", "city": "Denver", "state": "CO", "job_category": "commercial"}]},
        )

    assert "User-agent: *" in robots_body
    assert "Disallow: /" in robots_body
    assert "<urlset" in sitemap_body
    assert "<url>" not in sitemap_body
    assert status == 200
    assert body["results"][0]["_evidence_pack"]["cache_bypassed"] is True
