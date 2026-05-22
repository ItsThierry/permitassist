import copy
import hashlib
import json
import sys
from pathlib import Path
from importlib import util

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper_solar_mep_activation",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_LiveServer = _debug_helper._LiveServer
_import_server = _debug_helper._import_server
_post_json = _debug_helper._post_json

_STEP7C_SPEC = util.spec_from_file_location(
    "step7c_helpers_solar_mep_activation",
    Path(__file__).with_name("test_step7c_evidence_pack_local_gates.py"),
)
_step7c = util.module_from_spec(_STEP7C_SPEC)
_STEP7C_SPEC.loader.exec_module(_step7c)
_post_json_response = _step7c._post_json_response

REPO_ROOT = Path(__file__).resolve().parents[1]
SOLAR_MEP_PACK = REPO_ROOT / "evidence_packs" / "offline" / "solar_mep" / "permitassist-step7b-solar-commercial-mep-offline-evidence-pack-20260507.json"
DALLAS_STEP7U_PACK = REPO_ROOT / "evidence_packs" / "dallas" / "step7u" / "permitassist-step7u-dallas-only-production-preview-evidence-pack-20260506.json"
DALLAS_STEP7H_PACK = REPO_ROOT / "evidence_packs" / "dallas" / "step7h" / "permitassist-step7h-dallas-only-staging-evidence-pack-20260505.json"


def _base_engine_result():
    return {
        "permit_verdict": "YES",
        "confidence": "high",
        "permit_required": True,
        "permits_required": [{"permit_type": "Generic Building Permit"}],
        "fee_range": "$500-$1,000",
        "approval_timeline": "2-4 weeks",
        "inspections": ["final"],
        "apply_url": "",
        "sources": ["https://example.gov/generic-permits"],
        "checklist": [],
        "rejection_patterns": [],
    }


def _enable_pack(monkeypatch, pack_path: Path = SOLAR_MEP_PACK, *, mode: str = "solar_mep_controlled_preview", expected_fingerprint: str | None = None):
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    fingerprint = expected_fingerprint or (data.get("metadata") or {}).get("fingerprint_sha256")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_ENABLED", "true")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_MODE", mode)
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_EXPECTED_FINGERPRINT", fingerprint)
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PATH", str(pack_path))


def _enable_controlled_preview_token(monkeypatch, token: str = "test-controlled-preview-token"):
    monkeypatch.setenv("PERMITASSIST_SOLAR_MEP_CONTROLLED_PREVIEW_TOKEN", token)
    monkeypatch.setenv("PERMITASSIST_SOLAR_MEP_CONTROLLED_PREVIEW_HEADER", "X-Evidence-Pack-Preview-Token")
    return token


def _finalize(tmp_path, monkeypatch, job_type: str, city: str, state: str, *, explicit_vertical: str | None = None, base: dict | None = None):
    _enable_pack(monkeypatch)
    server = _import_server(tmp_path, monkeypatch)
    return server.finalize_permit_lookup_result(
        copy.deepcopy(base or _base_engine_result()),
        job_type,
        city,
        state,
        explicit_vertical=explicit_vertical,
    )


def _assert_valid_meta(result: dict, expected_vertical: str):
    meta = result["_evidence_pack"]
    assert meta["enabled"] is True
    assert meta["mode"] == "solar_mep_controlled_preview"
    assert meta["contract_status"] == "valid"
    assert meta["evidence_pack_version"] == "step7b_offline_v1"
    assert meta["fingerprint_valid"] is True
    assert meta["request_vertical"] == expected_vertical
    assert meta["matched_fields"]
    assert meta["cache_bypassed"] is True
    assert "path" not in meta


def test_solar_mep_promotion_identity_is_pinned_to_checked_in_pack(tmp_path, monkeypatch):
    _enable_pack(monkeypatch)
    server = _import_server(tmp_path, monkeypatch)
    promotion = sys.modules["evidence_pack_runtime"].SOLAR_MEP_CONTROLLED_PROMOTION
    data = json.loads(SOLAR_MEP_PACK.read_text(encoding="utf-8"))
    metadata = data["metadata"]
    assert str(SOLAR_MEP_PACK).replace("\\", "/").endswith(promotion["path_suffix"])
    assert hashlib.sha256(SOLAR_MEP_PACK.read_bytes()).hexdigest() == promotion["raw_sha256"]
    assert metadata["fingerprint_sha256"] == promotion["fingerprint_sha256"]
    assert metadata["evidence_pack_version"] == promotion["evidence_pack_version"] == "step7b_offline_v1"
    assert len(data["records"]) == promotion["record_count"] == 9
    assert {record["pack_family"] for record in data["records"]} == {promotion["pack_family"]}
    pack = sys.modules["evidence_pack_runtime"].get_local_evidence_pack()
    assert pack.contract_status == "valid"
    assert pack.fingerprint_valid is True
    assert pack.production_wiring_allowed is False


def test_solar_mep_copied_same_bytes_path_mismatch_fails_closed(tmp_path, monkeypatch):
    copied_pack = tmp_path / "permitassist-step7b-solar-commercial-mep-offline-evidence-pack-20260507.json"
    copied_pack.write_bytes(SOLAR_MEP_PACK.read_bytes())
    _enable_pack(monkeypatch, copied_pack)
    server = _import_server(tmp_path, monkeypatch)
    result = server.finalize_permit_lookup_result(_base_engine_result(), "NYC solar PV", "New York", "NY")
    meta = result["_evidence_pack"]
    assert meta["contract_status"] == "invalid_contract"
    assert meta["matched_fields"] == []
    assert meta["failed_closed_fields"]
    assert meta["fingerprint_valid"] is False
    assert "path" not in meta
    assert str(tmp_path) not in json.dumps(result)


def test_public_json_redaction_drops_internal_fee_floor_components(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    response = server.redact_public_output({
        "fee_range": "$18,000 - $65,000",
        "_fee_floor_components": {
            "jurisdiction_rationale": "baseline calibration from Phoenix restaurant TI test case",
            "scope": "commercial_restaurant",
        },
        "_rulebook_depth_disclaimer": "Confidence: HIGH — verified rulebook depth for Phoenix on restaurant ti",
        "_residential_home_office_leak_repaired": True,
        "nested": {
            "_fee_floor_components": {"jurisdiction_rationale": "restaurant"},
            "_rulebook_depth_disclaimer": "restaurant ti",
            "_residential_home_office_leak_repaired": {"city": "Birmingham", "state": "AL"},
            "ok": True,
        },
    })
    assert "_fee_floor_components" not in response
    assert "_rulebook_depth_disclaimer" not in response
    assert "_residential_home_office_leak_repaired" not in response
    assert "_fee_floor_components" not in response["nested"]
    assert "_rulebook_depth_disclaimer" not in response["nested"]
    assert "_residential_home_office_leak_repaired" not in response["nested"]
    assert "restaurant" not in json.dumps(response).lower()


def test_public_json_redaction_drops_evidence_pack_metadata_key(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    response = server.redact_public_output({
        "permit_verdict": "YES",
        "coverage_truth": {"official_source_backed": ["apply URL / route"]},
        "_evidence_pack": {
            "enabled": True,
            "mode": "local_golden",
            "matched_fields": ["permit_type"],
        },
        "nested": {
            "field_evidence_confidence": {"permit_type": "verified"},
            "evidence_pack_record_id": "internal-row",
        },
    })
    text = json.dumps(response).lower()
    assert response["permit_verdict"] == "YES"
    assert response["coverage_truth"]["official_source_backed"] == ["apply URL / route"]
    assert "_evidence_pack" not in text
    assert "evidence_pack" not in text
    assert "field_evidence_confidence" not in text
    assert "evidence_pack_record_id" not in text


def test_negated_former_restaurant_license_required_is_scrubbed(tmp_path, monkeypatch):
    job = (
        "Retail tenant improvement in former restaurant space: convert to dry retail store, "
        "remove old cooking equipment, no food service, no hood, no grease interceptor."
    )
    result = _finalize(
        tmp_path,
        monkeypatch,
        job,
        "Nashville",
        "TN",
        base={
            **_base_engine_result(),
            "license_required": (
                "Licensed commercial contractor pulls the permit on behalf of the tenant or owner, "
                "and the contractor's license/registration information must appear on the application. "
                "In Nashville, contractors must also have current Metro Codes contractor registration before they can pull permits. "
                "The permit is still required even though the space is being converted away from food service."
            ),
        },
    )
    license_text = result.get("license_required", "")
    assert "Licensed commercial contractor pulls the permit" in license_text
    assert "Metro Codes contractor registration" in license_text
    for forbidden in ("food service", "restaurant", "hood", "grease"):
        assert forbidden not in license_text.lower()


def test_solar_mep_preview_mode_requires_preview_only_route_gate(tmp_path, monkeypatch):
    _enable_pack(monkeypatch)
    server = _import_server(tmp_path, monkeypatch)
    assert server.evidence_pack_allowed_for_request("/api/permit", {"X-Sample-Demo": "1"}, is_sample_demo=True) is False
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PREVIEW_ONLY", "true")
    assert server.evidence_pack_allowed_for_request("/api/permit", {"X-Sample-Demo": "1"}, is_sample_demo=True) is False
    token = _enable_controlled_preview_token(monkeypatch)
    assert server.evidence_pack_allowed_for_request(
        "/api/permit",
        {"X-Sample-Demo": "1", "X-Evidence-Pack-Preview-Token": token},
        is_sample_demo=True,
    ) is True
    assert server.evidence_pack_allowed_for_request(
        "/api/permit",
        {"X-Sample-Demo": "1", "X-Evidence-Pack-Preview-Token": "wrong-token"},
        is_sample_demo=True,
    ) is False
    assert server.evidence_pack_allowed_for_request(
        "/api/v1/permit",
        {"X-Sample-Demo": "1", "X-Evidence-Pack-Preview-Token": token},
        is_sample_demo=True,
    ) is False
    assert server.evidence_pack_allowed_for_request(
        "/api/batch-permit",
        {"X-Sample-Demo": "1", "X-Evidence-Pack-Preview-Token": token},
        is_sample_demo=True,
    ) is False


def test_solar_mep_positive_solar_pv_match(tmp_path, monkeypatch):
    result = _finalize(tmp_path, monkeypatch, "NYC roof-mounted solar PV installation", "New York", "NY")
    _assert_valid_meta(result, "solar_pv_battery")
    assert {"approval_timeline", "companion_reviews_triggers", "permit_type"}.issubset(result["_evidence_pack"]["matched_fields"])
    assert "solar" in result["permit_type"].lower() or "pv" in result["permit_type"].lower()


def test_solar_mep_positive_battery_storage_match(tmp_path, monkeypatch):
    result = _finalize(tmp_path, monkeypatch, "NYC rooftop solar PV with battery storage ESS", "New York", "NY")
    _assert_valid_meta(result, "solar_pv_battery")
    assert "permit_type" in result["_evidence_pack"]["matched_fields"]
    assert "battery" in json.dumps(result).lower() or "storage" in json.dumps(result).lower()


def test_solar_mep_positive_commercial_mechanical_match(tmp_path, monkeypatch):
    result = _finalize(tmp_path, monkeypatch, "Dallas commercial mechanical RTU replacement and duct alterations", "Dallas", "TX")
    _assert_valid_meta(result, "commercial_mechanical")
    assert result["_evidence_pack"]["matched_fields"] == ["companion_reviews_triggers"]
    assert "mechanical" in result["companion_reviews_triggers"].lower()


def test_solar_mep_positive_commercial_electrical_match(tmp_path, monkeypatch):
    result = _finalize(tmp_path, monkeypatch, "Dallas commercial electrical panel and branch circuit alteration", "Dallas", "TX")
    _assert_valid_meta(result, "commercial_electrical")
    assert result["_evidence_pack"]["matched_fields"] == ["companion_reviews_triggers"]
    assert "electrical" in result["companion_reviews_triggers"].lower()


def test_solar_mep_positive_commercial_plumbing_match(tmp_path, monkeypatch):
    result = _finalize(tmp_path, monkeypatch, "Dallas commercial plumbing fixture relocation and DWV work", "Dallas", "TX")
    _assert_valid_meta(result, "commercial_plumbing")
    assert result["_evidence_pack"]["matched_fields"] == ["companion_reviews_triggers"]
    assert "plumbing" in result["companion_reviews_triggers"].lower()


def test_solar_mep_positive_cross_trade_commercial_mep_match(tmp_path, monkeypatch):
    result = _finalize(
        tmp_path,
        monkeypatch,
        "Dallas commercial MEP tenant improvement with mechanical electrical and plumbing scope",
        "Dallas",
        "TX",
    )
    _assert_valid_meta(result, "commercial_mep_ti")
    assert {"apply_url", "inspections", "permit_type"}.issubset(result["_evidence_pack"]["matched_fields"])
    assert result["apply_url"] == "https://aca-prod.accela.com/DALLASTX/Default.aspx"


def test_solar_mep_unsupported_city_state_fail_closed(tmp_path, monkeypatch):
    result = _finalize(tmp_path, monkeypatch, "solar PV with battery storage", "Boston", "MA")
    meta = result["_evidence_pack"]
    assert meta["contract_status"] == "valid"
    assert meta["matched_fields"] == []
    assert set(meta["failed_closed_fields"]) == {"permit_type", "apply_url", "fee_range", "approval_timeline", "inspections", "companion_reviews_triggers"}
    assert result["claim_citations"] == []
    assert result["permit_type"] == "Manual filing path confirmation in progress"
    assert result["permits_required"] == [
        {
            "permit_type": "Manual filing path confirmation in progress",
            "portal_selection": "Manual filing path confirmation in progress",
            "required": True,
            "notes": result["permits_required"][0]["notes"],
        }
    ]
    assert "Generic Building Permit" not in json.dumps(result["permits_required"], sort_keys=True)


def test_solar_mep_bad_fingerprint_fail_closed(tmp_path, monkeypatch):
    _enable_pack(monkeypatch, expected_fingerprint="0" * 64)
    server = _import_server(tmp_path, monkeypatch)
    result = server.finalize_permit_lookup_result(_base_engine_result(), "NYC solar PV", "New York", "NY")
    assert result["_evidence_pack"]["contract_status"] == "invalid_fingerprint"
    assert result["_evidence_pack"]["matched_fields"] == []
    assert result["claim_citations"] == []


def test_solar_mep_copied_or_modified_pack_fails_closed(tmp_path, monkeypatch):
    data = json.loads(SOLAR_MEP_PACK.read_text(encoding="utf-8"))
    for record in data["records"]:
        record["stale_after_utc"] = "2020-01-01T00:00:00Z"
        for evidence in record.get("field_evidence") or []:
            evidence["stale_after_utc"] = "2020-01-01T00:00:00Z"
    pack_path = tmp_path / "stale-solar-mep.json"
    pack_path.write_text(json.dumps(data), encoding="utf-8")
    _enable_pack(monkeypatch, pack_path)
    server = _import_server(tmp_path, monkeypatch)
    result = server.finalize_permit_lookup_result(_base_engine_result(), "NYC solar PV", "New York", "NY")
    meta = result["_evidence_pack"]
    assert meta["contract_status"] == "invalid_contract"
    assert meta["matched_fields"] == []
    assert meta["fingerprint_valid"] is False
    assert result["claim_citations"] == []
    assert result["permit_type"] == "Manual filing path confirmation in progress"
    assert result["permits_required"] == [
        {
            "permit_type": "Manual filing path confirmation in progress",
            "portal_selection": "Manual filing path confirmation in progress",
            "required": True,
            "notes": result["permits_required"][0]["notes"],
        }
    ]
    assert "Generic Building Permit" not in json.dumps(result["permits_required"], sort_keys=True)


def test_solar_mep_public_no_header_no_evidence_leak(tmp_path, monkeypatch):
    _enable_pack(monkeypatch)
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PREVIEW_ONLY", "true")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PREVIEW_HEADER", "X-Sample-Demo")
    server = _import_server(tmp_path, monkeypatch)
    calls = []

    def fake_research(*args, **kwargs):
        calls.append(kwargs)
        return copy.deepcopy(_base_engine_result())

    server.research_permit = fake_research
    server.is_paid_user = lambda email: True
    token = server.create_session_token("paid@example.com")
    with _LiveServer(server.Handler) as live:
        status, body = _post_json_response(
            f"{live.base}/api/permit",
            {"job_type": "NYC solar PV with battery storage", "city": "New York", "state": "NY", "job_category": "commercial"},
            {"X-Session-Token": token},
        )
    assert status == 200
    assert calls and calls[0]["use_cache"] is True
    assert calls[0]["suppress_cache_write"] is False
    assert "_evidence_pack" not in body
    assert "02fea6" not in json.dumps(body)


def test_solar_mep_preview_header_route_gating_works(tmp_path, monkeypatch):
    _enable_pack(monkeypatch)
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PREVIEW_ONLY", "true")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PREVIEW_HEADER", "X-Sample-Demo")
    controlled_token = _enable_controlled_preview_token(monkeypatch)
    server = _import_server(tmp_path, monkeypatch)
    calls = []

    def fake_research(*args, **kwargs):
        calls.append(kwargs)
        return copy.deepcopy(_base_engine_result())

    server.research_permit = fake_research
    server.is_paid_user = lambda email: True
    token = server.create_session_token("paid@example.com")
    body = {"job_type": "NYC solar PV with battery storage", "city": "New York", "state": "NY", "job_category": "commercial"}
    with _LiveServer(server.Handler) as live:
        status, preview_body = _post_json_response(
            f"{live.base}/api/permit",
            body,
            {"X-Session-Token": token, "X-Sample-Demo": "1", "X-Evidence-Pack-Preview-Token": controlled_token},
        )
        batch_status, batch_body = _post_json_response(
            f"{live.base}/api/batch-permit",
            {"lookups": [body]},
            {"X-Sample-Demo": "1"},
        )
    assert status == batch_status == 200
    assert calls[0]["use_cache"] is False
    assert calls[0]["suppress_cache_write"] is True
    assert preview_body["_evidence_pack"]["mode"] == "solar_mep_controlled_preview"
    assert "permit_type" in preview_body["_evidence_pack"]["matched_fields"]
    assert "_evidence_pack" not in json.dumps(batch_body)


def test_solar_mep_cache_interaction_fails_closed(tmp_path, monkeypatch):
    result = _finalize(
        tmp_path,
        monkeypatch,
        "NYC solar PV with battery storage",
        "New York",
        "NY",
        base={**_base_engine_result(), "_cached": True},
    )
    assert result["_evidence_pack"]["contract_status"] == "invalid_contract"
    assert result["_evidence_pack"]["matched_fields"] == []
    assert result["claim_citations"] == []


def test_solar_mep_dallas_step7u_preview_still_works(tmp_path, monkeypatch):
    _enable_pack(monkeypatch, DALLAS_STEP7U_PACK, mode="dallas_step7u_production_preview")
    server = _import_server(tmp_path, monkeypatch)
    result = server.finalize_permit_lookup_result(
        _base_engine_result(),
        "medical clinic tenant improvement with exam rooms and accessibility review",
        "Dallas",
        "TX",
        explicit_vertical="medical_clinic_ti",
    )
    assert result["_evidence_pack"]["mode"] == "dallas_step7u_production_preview"
    assert result["_evidence_pack"]["contract_status"] == "valid"
    assert result["_evidence_pack"]["request_vertical"] == "medical_clinic_ti"
    assert "apply_url" in result["_evidence_pack"]["matched_fields"]


def test_solar_mep_verticals_do_not_leak_into_dallas_modes(tmp_path, monkeypatch):
    cases = [
        (DALLAS_STEP7U_PACK, "dallas_step7u_production_preview"),
        (DALLAS_STEP7H_PACK, "dallas_step7h_preview"),
    ]
    requests = [
        ("Dallas commercial solar PV with battery storage", None, "solar_pv_battery"),
        ("Dallas commercial electrical panel alteration", None, "commercial_electrical"),
        (
            "Dallas commercial MEP tenant improvement with mechanical electrical and plumbing scope",
            None,
            "commercial_mep_ti",
        ),
        ("generic Dallas tenant work", "solar_pv_battery", "solar_pv_battery"),
        ("generic Dallas tenant work", "commercial_electrical", "commercial_electrical"),
    ]
    for pack_path, mode in cases:
        monkeypatch.undo()
        _enable_pack(monkeypatch, pack_path, mode=mode)
        server = _import_server(tmp_path / mode, monkeypatch)
        for job_type, explicit_vertical, expected_vertical in requests:
            result = server.finalize_permit_lookup_result(
                copy.deepcopy(_base_engine_result()),
                job_type,
                "Dallas",
                "TX",
                explicit_vertical=explicit_vertical,
            )
            meta = result["_evidence_pack"]
            assert meta["mode"] == mode
            assert meta["request_vertical"] == expected_vertical
            assert meta["matched_fields"] == []
            assert meta["failed_closed_fields"]
            assert result["claim_citations"] == []


def test_solar_mep_restaurant_medical_office_ti_unchanged_no_leakage(tmp_path, monkeypatch):
    _enable_pack(monkeypatch)
    server = _import_server(tmp_path, monkeypatch)
    cases = [
        ("restaurant tenant improvement with Type I hood", "Dallas", "TX", "restaurant_ti"),
        ("medical clinic TI with exam rooms", "Dallas", "TX", "medical_clinic_ti"),
        ("office tenant improvement with conference rooms", "Dallas", "TX", "office_ti"),
    ]
    for idx, (job_type, city, state, expected_vertical) in enumerate(cases):
        result = server.finalize_permit_lookup_result(_base_engine_result(), job_type, city, state)
        meta = result["_evidence_pack"]
        assert meta["request_vertical"] == expected_vertical
        assert meta["matched_fields"] == []
        evidence_text = json.dumps(result.get("claim_citations", [])) + json.dumps(result.get("companion_reviews_triggers", ""))
        assert "solar" not in evidence_text.lower()
        assert "battery" not in evidence_text.lower()
        assert "dallasnow" not in evidence_text.lower()


def test_solar_mep_negative_terms_do_not_cross_pollute_verticals(tmp_path, monkeypatch):
    solar = _finalize(
        tmp_path / "solar",
        monkeypatch,
        "NYC solar PV array, not an office TI, no tenant improvement restaurant or medical clinic scope",
        "New York",
        "NY",
    )
    assert solar["_evidence_pack"]["request_vertical"] == "solar_pv_battery"
    assert "restaurant" not in json.dumps(solar.get("claim_citations", [])).lower()
    assert "office" not in json.dumps(solar.get("claim_citations", [])).lower()

    monkeypatch.undo()
    _enable_pack(monkeypatch)
    office = _finalize(
        tmp_path / "office",
        monkeypatch,
        "Dallas office TI without solar PV, without battery storage, no restaurant or food service",
        "Dallas",
        "TX",
    )
    assert office["_evidence_pack"]["request_vertical"] == "office_ti"
    assert office["_evidence_pack"]["matched_fields"] == []
    companion_text = json.dumps(office.get("companion_permits", [])) + json.dumps(office.get("claim_citations", []))
    assert "solar" not in companion_text.lower()
    assert "battery" not in companion_text.lower()
