import copy
import hashlib
import inspect
import json
from importlib import util
from pathlib import Path

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper_phase7c_golden",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_import_server = _debug_helper._import_server

_BUILDER_SPEC = util.spec_from_file_location(
    "phase7b_golden_local_pack_builder",
    Path(__file__).resolve().parents[1] / "scripts" / "build_phase7b_golden_local_pack.py",
)
_pack_builder = util.module_from_spec(_BUILDER_SPEC)
_BUILDER_SPEC.loader.exec_module(_pack_builder)

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ARTIFACT = Path("/home/boban/.hermes/project-briefs/artifacts/permitassist-phase7/PA7B-HYBRID10-20260511/golden-evidence-pack-third-pass.json")
SOURCE_SHA256 = "91e0589edbf17aae15f2dc21f3734c333e9d4f02debe0e1575f7117f884753f9"
BATCH_ID = "PA7B-HYBRID10-20260511"
GOLDEN_MODE = "phase7b_golden_local_preview"
GOLDEN_BETA_MODE = "phase7b_golden_beta_guarded"
GOLDEN_VERSION = "phase7b_golden_local_preview_v1"
GOLDEN_PACK = REPO_ROOT / "data" / "evidence_packs" / "local_golden" / "phase7b" / "permitassist-phase7b-golden-local-preview-pack-PA7B-HYBRID10-20260511.json"
TOKEN = "test"


def _base_engine_result():
    return {
        "permit_verdict": "YES",
        "confidence": "high",
        "permits_required": [{"permit_type": "Generic cached engine value"}],
        "fee_range": "$500-$1,000",
        "approval_timeline": "2-4 weeks",
        "inspections": ["final building"],
        "companion_reviews_triggers": "generic companion review copy",
        "apply_url": "",
        "apply_phone": "",
        "sources": [],
        "checklist": [],
        "rejection_patterns": [],
    }


def _timeline_simple(value):
    if isinstance(value, dict):
        return value.get("simple", "")
    return value or ""


def _source_rows():
    assert SOURCE_ARTIFACT.exists()
    assert hashlib.sha256(SOURCE_ARTIFACT.read_bytes()).hexdigest() == SOURCE_SHA256
    data = json.loads(SOURCE_ARTIFACT.read_text(encoding="utf-8"))
    return data["records"]


def _enable_golden(monkeypatch, pack_path: Path = GOLDEN_PACK, *, mode: str = GOLDEN_MODE, fingerprint: str | None = None):
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_ENABLED", "true")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_MODE", mode)
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PATH", str(pack_path))
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_EXPECTED_FINGERPRINT", fingerprint or data["metadata"]["fingerprint_sha256"])
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PREVIEW_ONLY", "true")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PREVIEW_HEADER", "X-Sample-Demo")
    monkeypatch.setenv("PERMITASSIST_PHASE7B_GOLDEN_LOCAL_PREVIEW_TOKEN", TOKEN)
    monkeypatch.setenv("PERMITASSIST_PHASE7B_GOLDEN_LOCAL_PREVIEW_HEADER", "X-Phase7B-Golden-Preview-Token")


def _promoted_field_caveats(source_row):
    caveats = []
    for field in source_row["fields"].values():
        if field.get("status") == "promoted_caveated_source_backed" and field.get("customer_facing_caveat"):
            caveats.append(field["customer_facing_caveat"])
    return caveats


def _assert_no_customer_private_markers(body):
    body_text = json.dumps(body, sort_keys=True, default=str)
    for forbidden in (
        "_evidence_pack",
        "Evidence Pack",
        "Evidence pack",
        "Evidence-pack",
        "evidence pack",
        "evidence_pack",
        "fingerprint",
        "local_golden",
        "golden_row_id",
        "matched_row_ids",
        BATCH_ID,
        GOLDEN_MODE,
        GOLDEN_BETA_MODE,
        "phase7b-golden::",
    ):
        assert forbidden not in body_text


def test_phase7c_golden_pack_is_generated_from_locked_artifact_contract():
    rows = _source_rows()
    assert len(rows) == 30
    assert {r["vertical_slug"] for r in rows} == {"restaurant_ti", "medical_clinic_ti", "office_ti"}
    assert {r["row_level_classification"] for r in rows} == {"customer-ready", "safe-with-caveat"}

    assert GOLDEN_PACK.exists()
    pack = json.loads(GOLDEN_PACK.read_text(encoding="utf-8"))
    metadata = pack["metadata"]
    assert metadata["mode"] == GOLDEN_MODE
    assert metadata["evidence_pack_version"] == GOLDEN_VERSION
    assert metadata["batch_id"] == BATCH_ID
    assert metadata["source_artifact"] == "golden-evidence-pack-third-pass.json"
    assert metadata["source_artifact_sha256"] == SOURCE_SHA256
    assert metadata["source_schema_name"] == "permitassist_golden_evidence_record_v1"
    assert metadata["golden_row_count"] == 30
    assert metadata["row_level_classification_counts"] == {"customer-ready": 12, "safe-with-caveat": 18}
    assert set(metadata["locked_verticals"]) == {"restaurant_ti", "medical_clinic_ti", "office_ti"}
    assert metadata["production_wiring_allowed"] is False
    assert metadata["eligibility"] == "local_internal_preview_only"
    assert metadata["row_readiness_enum"] == ["customer_ready", "customer_ready_with_minor_caveat", "safe_with_caveat", "internal_only"]
    assert metadata["field_certainty_enum"] == ["promoted_clean", "promoted_caveated", "not_published_on_official_sources", "not_researched_enough", "rejected"]
    assert metadata["customer_surface_policy_enum"] == ["show_as_fact", "show_with_caveat", "suppress_but_log_internal", "never_show"]
    assert len(metadata["internal_unpromoted_field_reasons"]) == 30 * 7

    records = pack["records"]
    assert len(records) == 60
    assert len({r["golden_row_id"] for r in records}) == 30
    assert {r["field"] for r in records} == {"permit_type", "apply_url"}
    pairs = [(r["golden_row_id"], r["field"]) for r in records]
    assert len(set(pairs)) == 60
    for row_id in {r["golden_row_id"] for r in records}:
        assert sorted(r["field"] for r in records if r["golden_row_id"] == row_id) == ["apply_url", "permit_type"]
    assert all(str(r["claim_value"]).strip() for r in records if r["field"] == "permit_type")
    assert all(str(r["claim_value"]).startswith(("http://", "https://")) for r in records if r["field"] == "apply_url")
    assert all(r["row_readiness"] in metadata["row_readiness_enum"] for r in records)
    assert all(r["field_certainty"] in metadata["field_certainty_enum"] for r in records)
    assert all(r["customer_surface_policy"] in metadata["customer_surface_policy_enum"] for r in records)
    assert all(r["last_verified_date"] and r["next_reverification_due"] and r["stale_after_date"] for r in records)
    assert all(r["source_retrieval_method"] and r["source_snapshot_ref"] for r in records)
    assert all(r["ingestion_ready"] is True for r in records)
    assert not any(r["source_golden_field_status"] == "verified_not_published_or_not_found_on_official_sources" for r in records)
    assert not any(r["field"] in {"fee_range", "approval_timeline", "inspections", "companion_reviews_triggers"} for r in records)


def test_phase7c_all_30_golden_rows_match_and_suppress_unsupported_fields(monkeypatch, tmp_path):
    _enable_golden(monkeypatch)
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda *_args, **_kwargs: True)

    for source_row in _source_rows():
        result = server.finalize_permit_lookup_result(
            copy.deepcopy(_base_engine_result()),
            f"{source_row['vertical_slug']} tenant improvement local golden preview",
            source_row["city"],
            source_row["state"],
            explicit_vertical=source_row["vertical_slug"],
        )
        meta = result["_evidence_pack"]
        golden_meta = meta["local_golden"]
        assert meta["contract_status"] == "valid"
        assert meta["mode"] == GOLDEN_MODE
        assert meta["request_vertical"] == source_row["vertical_slug"]
        assert meta["cache_bypassed"] is True
        assert golden_meta["row_id"] == source_row["row_id"]
        assert golden_meta["row_level_classification"] == source_row["row_level_classification"]
        assert golden_meta["batch_id"] == BATCH_ID
        assert set(meta["matched_fields"]) == {"permit_type", "apply_url"}
        assert {c["field"] for c in result["claim_citations"]} == {"permit_type", "apply_url"}
        assert not any(not str(c.get("value") or "").strip() for c in result["claim_citations"])
        assert result["fee_range"] is None
        assert result["approval_timeline"] is None
        assert result["inspections"] is None
        assert result["companion_reviews_triggers"] is None
        assert not any(c["field"] in {"fee_range", "approval_timeline", "inspections", "companion_reviews_triggers"} for c in result["claim_citations"])

        warning_text = "\n".join(result.get("warnings") or result.get("quality_warnings") or [])
        for caveat in _promoted_field_caveats(source_row):
            assert caveat in warning_text
        if source_row["row_level_classification"] == "safe-with-caveat":
            assert "caveated_row" in result.get("needs_review_reasons", [])
            assert "Coverage caveat" in warning_text
            html = server.render_white_label_report_html({
                "result": result,
                "job_type": "tenant improvement",
                "city": source_row["city"],
                "state": source_row["state"],
            })
            assert "Coverage scope" in html
            assert "Customer-visible result" in html
            assert ("Permit Required" in html) or ("No Permit Required" in html) or ("Invalid/Unsupported Jurisdiction" in html)
            assert "No customer-final answer yet" not in html
            assert "PendingView" not in html
            assert "pending_reason" not in html
            assert "lookup_id" not in html
            assert "Official sources" in html
            assert "Likely permits" not in html
            assert "How to apply" not in html
            assert "_evidence_pack" not in html
            assert "Coverage caveat" not in html


def test_phase7c_golden_http_gate_requires_preview_and_non_public_token(monkeypatch, tmp_path):
    _enable_golden(monkeypatch)
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda *_args, **_kwargs: True)
    headers = {"X-Sample-Demo": "1", "X-Phase7B-Golden-Preview-Token": TOKEN}
    assert server.evidence_pack_allowed_for_request("/api/permit", headers, is_sample_demo=True) is True
    assert "hmac.compare_digest" in inspect.getsource(server.evidence_pack_allowed_for_request)
    assert server.evidence_pack_allowed_for_request("/api/permit", {"X-Sample-Demo": "1"}, is_sample_demo=True) is False
    assert server.evidence_pack_allowed_for_request("/api/permit", {"X-Sample-Demo": "1", "X-Phase7B-Golden-Preview-Token": "wrong"}, is_sample_demo=True) is False
    assert server.evidence_pack_allowed_for_request("/api/permit", {"X-Phase7B-Golden-Preview-Token": TOKEN}, is_sample_demo=False) is False
    assert server.evidence_pack_allowed_for_request("/api/batch-permit", headers, is_sample_demo=True) is False

    result = server.finalize_permit_lookup_result(
        copy.deepcopy(_base_engine_result()),
        "retail tenant improvement",
        "Los Angeles",
        "CA",
        explicit_vertical="retail_ti",
        evidence_allowed=False,
    )
    assert "_evidence_pack" not in result


def test_phase7c_golden_negative_scope_controls_fail_closed_without_golden_leak(monkeypatch, tmp_path):
    _enable_golden(monkeypatch)
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda *_args, **_kwargs: True)
    for city, state, vertical in (("Imaginary City", "CA", "office_ti"), ("Los Angeles", "CA", "retail_ti")):
        result = server.finalize_permit_lookup_result(
            copy.deepcopy(_base_engine_result()),
            f"{vertical} tenant improvement",
            city,
            state,
            explicit_vertical=vertical,
        )
        assert "_evidence_pack" not in result
        assert result["fee_range"] is None
        assert result["approval_timeline"] is None
        assert result["inspections"] is None
        assert result["companion_reviews_triggers"] is None
        assert result.get("claim_citations") == []
        expected = "unsupported_ahj" if vertical == "office_ti" else "unsupported_vertical"
        assert expected in result.get("needs_review_reasons", [])
        assert "unsupported_fields_failed_closed" in result.get("needs_review_reasons", [])


def test_phase7d_golden_beta_guard_requires_session_allowlist_and_supported_route(monkeypatch, tmp_path):
    _enable_golden(monkeypatch, mode=GOLDEN_BETA_MODE)
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PREVIEW_ONLY", "false")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_BETA_EMAIL_ALLOWLIST", "beta@example.com, friend@example.com")
    monkeypatch.delenv("PERMITASSIST_PHASE7B_GOLDEN_LOCAL_PREVIEW_TOKEN", raising=False)
    server = _import_server(tmp_path, monkeypatch)

    assert server.evidence_pack_allowed_for_request(
        "/api/permit",
        {"X-Session-Token": "session"},
        is_sample_demo=False,
        user_email="beta@example.com",
        paid=False,
    ) is True
    assert server.evidence_pack_allowed_for_request(
        "/api/permit",
        {"X-Session-Token": "session"},
        is_sample_demo=False,
        user_email="paid-not-allowlisted@example.com",
        paid=True,
    ) is False
    assert server.evidence_pack_allowed_for_request(
        "/api/permit",
        {},
        is_sample_demo=False,
        user_email="",
        paid=False,
    ) is False
    assert server.evidence_pack_allowed_for_request(
        "/api/batch-permit",
        {"X-Session-Token": "session"},
        is_sample_demo=False,
        user_email="beta@example.com",
        paid=True,
    ) is False
    assert server.evidence_pack_allowed_for_request(
        "/api/permit",
        {"X-Sample-Demo": "1", "X-Phase7B-Golden-Preview-Token": TOKEN},
        is_sample_demo=True,
        user_email="",
        paid=False,
    ) is False


def test_phase7d_golden_beta_paid_http_path_bypasses_cache_and_redacts_pack_metadata(monkeypatch, tmp_path):
    from test_debug_headers_endpoint import _LiveServer
    from test_step7c_evidence_pack_local_gates import _post_json_response

    _enable_golden(monkeypatch, mode=GOLDEN_BETA_MODE)
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PREVIEW_ONLY", "false")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_BETA_EMAIL_ALLOWLIST", "beta@example.com")
    monkeypatch.delenv("PERMITASSIST_PHASE7B_GOLDEN_LOCAL_PREVIEW_TOKEN", raising=False)
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda *_args, **_kwargs: True)
    calls = []

    def fake_research(*args, **kwargs):
        calls.append(kwargs)
        return copy.deepcopy(_base_engine_result())

    server.research_permit = fake_research
    server.is_paid_user = lambda email: email == "beta@example.com"
    token = server.create_session_token("beta@example.com")
    with _LiveServer(server.Handler) as live:
        status, body = _post_json_response(
            f"{live.base}/api/permit",
            {
                "job_type": "office tenant improvement",
                "city": "Los Angeles",
                "state": "CA",
                "job_category": "commercial",
                "vertical": "office_ti",
            },
            {"X-Session-Token": token},
        )

    assert status == 200
    assert calls and calls[0]["use_cache"] is False
    assert calls[0]["suppress_cache_write"] is True
    assert body["apply_url"].startswith("http")
    assert body["coverage_truth"]["official_source_backed"] == ["permit type", "apply URL / route"]
    assert body["permit_type"]
    assert body["permits_required"] and body["permits_required"][0]["permit_type"] == body["permit_type"]
    assert body["permit_required"] is True
    assert body["remaining_lookups"] == -1
    _assert_no_customer_private_markers(body)


def test_phase7d_golden_beta_public_and_invalid_session_do_not_expose_protected_evidence(monkeypatch, tmp_path):
    from test_debug_headers_endpoint import _LiveServer
    from test_step7c_evidence_pack_local_gates import _post_json_response

    _enable_golden(monkeypatch, mode=GOLDEN_BETA_MODE)
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PREVIEW_ONLY", "false")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_BETA_EMAIL_ALLOWLIST", "beta@example.com")
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda *_args, **_kwargs: True)
    calls = []

    def fake_research(*args, **kwargs):
        calls.append(kwargs)
        return copy.deepcopy(_base_engine_result())

    server.research_permit = fake_research
    server.is_paid_user = lambda email: email == "beta@example.com"
    payload = {"job_type": "office tenant improvement", "city": "Los Angeles", "state": "CA", "job_category": "commercial", "vertical": "office_ti"}
    with _LiveServer(server.Handler) as live:
        public_status, public_body = _post_json_response(f"{live.base}/api/permit", payload, {})
        invalid_status, invalid_body = _post_json_response(f"{live.base}/api/permit", payload, {"X-Session-Token": "not-a-valid-session"})

    assert public_status == 200
    assert invalid_status == 200
    assert len(calls) == 2
    assert all(call["use_cache"] is True and call["suppress_cache_write"] is False for call in calls)
    assert "coverage_truth" not in public_body
    assert "coverage_truth" not in invalid_body
    _assert_no_customer_private_markers(public_body)
    _assert_no_customer_private_markers(invalid_body)


def test_phase7d_golden_beta_allowlist_guard_prevents_cache_poisoning(monkeypatch, tmp_path):
    from test_debug_headers_endpoint import _LiveServer
    from test_step7c_evidence_pack_local_gates import _post_json_response

    _enable_golden(monkeypatch, mode=GOLDEN_BETA_MODE)
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PREVIEW_ONLY", "false")
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_BETA_EMAIL_ALLOWLIST", "beta@example.com")
    server = _import_server(tmp_path, monkeypatch)
    calls = []

    def fake_research(*args, **kwargs):
        calls.append(kwargs)
        return copy.deepcopy(_base_engine_result())

    server.research_permit = fake_research
    server.is_paid_user = lambda email: True
    token = server.create_session_token("paid-not-allowlisted@example.com")
    with _LiveServer(server.Handler) as live:
        status, body = _post_json_response(
            f"{live.base}/api/permit",
            {"job_type": "office tenant improvement", "city": "Los Angeles", "state": "CA", "job_category": "commercial", "vertical": "office_ti"},
            {"X-Session-Token": token},
        )

    assert status == 200
    assert calls and calls[0]["use_cache"] is True
    assert calls[0]["suppress_cache_write"] is False
    assert "_evidence_pack" not in json.dumps(body, sort_keys=True)
    assert "coverage_truth" not in body


def test_phase7c_golden_rejects_wrong_contract_pins(monkeypatch, tmp_path):
    source = json.loads(GOLDEN_PACK.read_text(encoding="utf-8"))
    mutations = [
        ("wrong path", lambda d: d, tmp_path / "wrong-name.json"),
        ("wrong source raw sha", lambda d: d["metadata"].__setitem__("source_artifact_sha256", "0" * 64), tmp_path / GOLDEN_PACK.name),
        ("wrong batch", lambda d: d["metadata"].__setitem__("batch_id", "WRONG"), tmp_path / GOLDEN_PACK.name),
        ("wrong record count", lambda d: d.__setitem__("records", d["records"][:-1]), tmp_path / GOLDEN_PACK.name),
        ("wrong version", lambda d: d["metadata"].__setitem__("evidence_pack_version", "step7b_offline_v1"), tmp_path / GOLDEN_PACK.name),
        ("wrong mode", lambda d: d["metadata"].__setitem__("mode", "dallas_step7u_production_preview"), tmp_path / GOLDEN_PACK.name),
        ("wrong fingerprint", lambda d: d["metadata"].__setitem__("fingerprint_sha256", "a" * 64), tmp_path / GOLDEN_PACK.name),
    ]
    for label, mutate, path in mutations:
        data = copy.deepcopy(source)
        mutate(data)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        _enable_golden(monkeypatch, path)
        server = _import_server(tmp_path, monkeypatch)
        monkeypatch.setattr(server, "validate_url", lambda *_args, **_kwargs: True)
        result = server.finalize_permit_lookup_result(
            copy.deepcopy(_base_engine_result()),
            "office tenant improvement",
            "Los Angeles",
            "CA",
            explicit_vertical="office_ti",
        )
        assert result["_evidence_pack"]["contract_status"] == "invalid_contract", label
        assert result["_evidence_pack"]["matched_fields"] == [], label
        assert "invalid_pack_contract" in result.get("needs_review_reasons", []), label


def test_phase7c_golden_loader_refuses_without_preview_only(monkeypatch, tmp_path):
    _enable_golden(monkeypatch)
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PREVIEW_ONLY", "false")
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda *_args, **_kwargs: True)
    result = server.finalize_permit_lookup_result(
        copy.deepcopy(_base_engine_result()),
        "office tenant improvement",
        "Los Angeles",
        "CA",
        explicit_vertical="office_ti",
    )
    pack = server.get_local_evidence_pack()
    assert pack.contract_status == "invalid_contract"
    assert "phase7b_golden_preview_only_required" in pack.contract_warnings
    assert result["_evidence_pack"]["contract_status"] == "invalid_contract"
    assert "invalid_pack_contract" in result.get("needs_review_reasons", [])


def test_phase7c_public_surfaces_do_not_leak_local_golden_metadata(monkeypatch, tmp_path):
    _enable_golden(monkeypatch)
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda *_args, **_kwargs: True)

    public_result = server.finalize_permit_lookup_result(
        copy.deepcopy(_base_engine_result()),
        "office tenant improvement",
        "Los Angeles",
        "CA",
        explicit_vertical="office_ti",
        evidence_allowed=False,
    )
    preview_internal_result = server.finalize_permit_lookup_result(
        copy.deepcopy(_base_engine_result()),
        "office tenant improvement",
        "Los Angeles",
        "CA",
        explicit_vertical="office_ti",
    )
    preview_public_result = server.redact_public_output(copy.deepcopy(preview_internal_result))
    assert "_evidence_pack" not in preview_public_result
    assert "coverage_truth" in preview_public_result
    assert "Coverage scope" in server.render_white_label_report_html({
        "result": preview_public_result,
        "job_type": "office tenant improvement",
        "city": "Los Angeles",
        "state": "CA",
    })
    public_report = server.render_white_label_report_html({
        "result": server.redact_public_output(copy.deepcopy(public_result)),
        "job_type": "office tenant improvement",
        "city": "Los Angeles",
        "state": "CA",
    })
    public_batch_response = {
        "job_type": "office tenant improvement",
        "city": "Los Angeles",
        "state": "CA",
        "permit_verdict": public_result.get("permit_verdict"),
        "permits_required": public_result.get("permits_required", []),
        "fee_range": public_result.get("fee_range"),
        "approval_timeline": public_result.get("approval_timeline"),
        "apply_url": public_result.get("apply_url"),
        "apply_phone": public_result.get("apply_phone"),
        "confidence": public_result.get("confidence"),
        "permit_ready_score": public_result.get("permit_ready_score"),
        "checklist": public_result.get("checklist", []),
        "rejection_patterns": public_result.get("rejection_patterns", []),
        "error": None,
    }

    combined = "\n".join(
        json.dumps(surface, sort_keys=True, default=str) if not isinstance(surface, str) else surface
        for surface in (public_result, public_batch_response, public_report, preview_public_result)
    )
    forbidden = (
        "_evidence_pack",
        "local_golden",
        "golden_row_id",
        "matched_row_ids",
        "PA7B-HYBRID10-20260511",
        "phase7b_golden_local_preview",
        "phase7b_golden_local_preview_internal_only_v1",
        "Golden local mode",
        "invalid_pack_contract",
        "unsupported_ahj",
        "unsupported_vertical",
        "unsupported_fields_failed_closed",
        "empty_promoted_claim",
        "caveated_row",
        "Phase 7B Golden local preview",
        "Local Golden Evidence",
        "Golden Evidence",
        "phase7b-golden::",
    )
    for needle in forbidden:
        assert needle not in combined


def test_phase7c_activation_guard_matrix_fails_closed(monkeypatch, tmp_path):
    _enable_golden(monkeypatch)
    server = _import_server(tmp_path, monkeypatch)
    headers = {"X-Sample-Demo": "1", "X-Phase7B-Golden-Preview-Token": TOKEN}
    assert server.evidence_pack_allowed_for_request("/api/permit", headers, is_sample_demo=True) is True

    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PREVIEW_ONLY", "false")
    assert server.evidence_pack_allowed_for_request("/api/permit", headers, is_sample_demo=True) is False
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PREVIEW_ONLY", "true")
    monkeypatch.delenv("PERMITASSIST_PHASE7B_GOLDEN_LOCAL_PREVIEW_TOKEN")
    assert server.evidence_pack_allowed_for_request("/api/permit", headers, is_sample_demo=True) is False
    monkeypatch.setenv("PERMITASSIST_PHASE7B_GOLDEN_LOCAL_PREVIEW_TOKEN", TOKEN)
    assert server.evidence_pack_allowed_for_request("/api/permit", {"X-Sample-Demo": "1"}, is_sample_demo=True) is False
    assert server.evidence_pack_allowed_for_request("/api/permit", {"X-Sample-Demo": "1", "X-Phase7B-Golden-Preview-Token": "wrong"}, is_sample_demo=True) is False
    assert server.evidence_pack_allowed_for_request("/api/batch-permit", headers, is_sample_demo=True) is False

    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_EXPECTED_FINGERPRINT", "0" * 64)
    result = server.finalize_permit_lookup_result(
        copy.deepcopy(_base_engine_result()),
        "office tenant improvement",
        "Los Angeles",
        "CA",
        explicit_vertical="office_ti",
    )
    assert result["_evidence_pack"]["contract_status"] in {"invalid_fingerprint", "invalid_contract"}
    assert result["_evidence_pack"]["matched_fields"] == []
    assert result["fee_range"] is None
    assert result["approval_timeline"] is None

    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_EXPECTED_FINGERPRINT", json.loads(GOLDEN_PACK.read_text(encoding="utf-8"))["metadata"]["fingerprint_sha256"])
    monkeypatch.setenv("PERMITASSIST_EVIDENCE_PACK_PATH", str(tmp_path / "missing.json"))
    result = server.finalize_permit_lookup_result(
        copy.deepcopy(_base_engine_result()),
        "office tenant improvement",
        "Los Angeles",
        "CA",
        explicit_vertical="office_ti",
    )
    assert result["_evidence_pack"]["contract_status"] == "invalid_path"
    assert result["_evidence_pack"]["matched_fields"] == []


def test_phase7c_non_golden_regression_when_env_unset(monkeypatch, tmp_path):
    for name in (
        "PERMITASSIST_EVIDENCE_PACK_ENABLED",
        "PERMITASSIST_EVIDENCE_PACK_MODE",
        "PERMITASSIST_EVIDENCE_PACK_PATH",
        "PERMITASSIST_EVIDENCE_PACK_EXPECTED_FINGERPRINT",
        "PERMITASSIST_EVIDENCE_PACK_PREVIEW_ONLY",
        "PERMITASSIST_EVIDENCE_PACK_PREVIEW_HEADER",
        "PERMITASSIST_PHASE7B_GOLDEN_LOCAL_PREVIEW_TOKEN",
        "PERMITASSIST_PHASE7B_GOLDEN_LOCAL_PREVIEW_HEADER",
    ):
        monkeypatch.delenv(name, raising=False)
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda *_args, **_kwargs: True)

    result = server.finalize_permit_lookup_result(
        copy.deepcopy(_base_engine_result()),
        "office tenant improvement",
        "Los Angeles",
        "CA",
        explicit_vertical="office_ti",
    )

    assert "_evidence_pack" not in result
    assert result["fee_range"] == "$500-$1,000"
    assert result["approval_timeline"] == {"simple": "2-4 weeks"}
    assert result["inspections"] == ["final building"]
    assert result["companion_reviews_triggers"] == "generic companion review copy"


def test_phase7c_pack_builder_rejects_promoted_field_without_last_verified_date():
    row = copy.deepcopy(_source_rows()[0])
    row["fields"]["department_portal_application_method"].pop("last_verified_date", None)

    try:
        _pack_builder._transform_row(row)
    except ValueError as exc:
        assert "last_verified_date" in str(exc)
    else:
        raise AssertionError("builder accepted a promoted Golden field without last_verified_date")
