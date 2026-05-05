import copy
import json
import os
import sys
import urllib.request
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
                    }
                ],
            },
        ],
    }
    path.write_text(json.dumps(pack, indent=2), encoding="utf-8")
    return path


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
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PATH", str(pack_path))
    server = _import_server(tmp_path, monkeypatch)

    engine_result = _base_engine_result()
    engine_result["apply_url"] = "https://aca-prod.accela.com/DENVER/stale-portal"
    engine_result["inspection_booking"] = "Schedule online at https://aca-prod.accela.com/DENVER/stale-portal with 24 hours notice."
    result = server.finalize_permit_lookup_result(engine_result, "office tenant improvement", "Denver", "CO")

    meta = result["_evidence_pack"]
    assert meta["enabled"] is True
    assert meta["ingestion_ready_records_loaded"] == 2
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
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PATH", str(pack_path))
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
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PATH", str(pack_path))
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
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PATH", str(pack_path))
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

    assert result["_evidence_pack"]["ingestion_ready_records_loaded"] == 1
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
        }],
    })
    pack_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PATH", str(pack_path))
    server = _import_server(tmp_path, monkeypatch)

    result = server.finalize_permit_lookup_result(_base_engine_result(), "office tenant improvement", "Denver", "CO")

    assert "apply_url" in result["_evidence_pack"]["matched_fields"]
    assert result["apply_url"] is None
    assert result["apply_path"]["portal_url"] in (None, "")
    assert "javascript:" not in json.dumps(result)
