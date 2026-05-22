import json
from importlib import util
from pathlib import Path

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper_phase7l_unsupported_ahj",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_import_server = _debug_helper._import_server
_LiveServer = _debug_helper._LiveServer
_post_json = _debug_helper._post_json

FORBIDDEN_UNSUPPORTED_KEYS = {
    "apply_path",
    "apply_url",
    "apply_phone",
    "apply_google_maps",
    "permits_required",
    "permit_type",
    "permit_name",
    "inspections",
    "approval_timeline",
    "fee_range",
    "companion_reviews_triggers",
    "_evidence_pack",
    "field_evidence_confidence",
    "claim_citations",
}


def _assert_forbidden_keys_absent(value):
    if isinstance(value, dict):
        assert not (FORBIDDEN_UNSUPPORTED_KEYS & set(value)), value
        for child in value.values():
            _assert_forbidden_keys_absent(child)
    elif isinstance(value, list):
        for child in value:
            _assert_forbidden_keys_absent(child)


def _assert_unsupported_contract(body, expected_status):
    payload = json.loads(body)
    assert payload["ahj_status"] == expected_status
    assert payload["coverage_truth"]["status"] == "ahj_not_supported"
    message = payload["message"].lower()
    assert "not supported" in message or "cannot verify" in message
    _assert_forbidden_keys_absent(payload)
    return payload


def test_phase7l_classifies_supported_unsupported_invalid_and_normalized_city_tokens(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    assert server.classify_ahj_coverage(" Los Angeles ", " ca ")["status"] == "supported"
    assert server.classify_ahj_coverage("Nowhereville", "CA")["status"] == "unsupported"
    assert server.classify_ahj_coverage("Springfield", "ZZ")["status"] == "invalid_state"
    assert server.classify_ahj_coverage("", "CA")["status"] == "invalid_city"
    assert server.classify_ahj_coverage("!!!@@@", "CA")["status"] == "invalid_city"
    assert server.classify_ahj_coverage("A" * 121, "CA")["status"] == "invalid_city"
    assert server.classify_ahj_coverage("  sPrInGfIeLd ", " mo ")["status"] == "supported"
    assert server.classify_ahj_coverage("Springfield", "IL")["status"] == "unsupported"


def test_phase7m_new_york_alias_classifies_as_supported_without_weakening_fake_ahj_gate(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    new_york = server.classify_ahj_coverage("New York", "NY")
    assert new_york["status"] == "supported"
    assert new_york["classification"] == "supported"
    assert new_york["city"] == "New York City"
    assert new_york["reason"] == "city_state_supported"

    new_york_city = server.classify_ahj_coverage(" new york city ", " ny ")
    assert new_york_city["status"] == "supported"
    assert new_york_city["city"] == "New York City"

    assert server.classify_ahj_coverage("Faketown", "NY")["status"] == "unsupported"
    assert server.classify_ahj_coverage("Nowhereville", "CA")["status"] == "unsupported"
    assert server.classify_ahj_coverage("Springfield", "ZZ")["status"] == "invalid_state"


def test_phase7m_api_permit_allows_new_york_alias_to_existing_lookup_path(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    called = {"research": False, "city": None, "state": None}

    def fake_research(job_type, city, state, zip_code, **kwargs):
        called.update({"research": True, "city": city, "state": state})
        return {
            "permit_verdict": "YES",
            "permit_type": "Building Permit — Tenant Improvement / Office Interior Alteration",
            "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Office Interior Alteration", "required": True}],
            "apply_url": "https://www.nyc.gov/site/buildings/property-or-business-owner/project-requirements-owner.page",
            "sources": ["https://www.nyc.gov/site/buildings/property-or-business-owner/project-requirements-owner.page"],
            "confidence": "medium",
        }

    monkeypatch.setattr(server, "research_permit", fake_research)
    monkeypatch.setattr(server, "validate_url", lambda *args, **kwargs: True)

    with _LiveServer(server.Handler) as live:
        status, body = _post_json(
            f"{live.base}/api/permit",
            {"job_type": "office tenant improvement", "city": "New York", "state": "NY", "job_category": "commercial"},
            {"X-Sample-Demo": "1"},
        )

    payload = json.loads(body)
    assert status == 200
    assert called == {"research": True, "city": "New York", "state": "NY"}
    assert payload.get("ahj_status") != "unsupported"
    assert "apply_path" in payload
    assert payload["permits_required"]


def test_ef02_api_permit_blocks_fake_unsupported_ahj_before_research(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    called = {"research": False}

    def fail_if_called(job_type, city, state, zip_code, **kwargs):
        called["research"] = True
        raise AssertionError("research_permit must not run for unsupported AHJs")

    monkeypatch.setattr(server, "research_permit", fail_if_called)
    monkeypatch.setattr(server, "validate_url", lambda *args, **kwargs: True)

    with _LiveServer(server.Handler) as live:
        status, body = _post_json(
            f"{live.base}/api/permit",
            {"job_type": "office tenant improvement", "city": "Faketown", "state": "NY", "job_category": "commercial"},
            {"X-Sample-Demo": "1"},
        )

    payload = _assert_unsupported_contract(body, "unsupported")
    assert status == 422
    assert called["research"] is False
    assert payload.get("permit_verdict") != "YES"


def test_phase7l_unsupported_response_builder_is_canonical_and_has_no_supported_fields(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    payload = server.build_unsupported_ahj_response(
        "Nowhereville",
        "CA",
        {"status": "unsupported", "city": "Nowhereville", "state": "CA", "reason": "city_not_in_supported_coverage"},
    )

    assert payload["ahj_status"] == "unsupported"
    assert payload["coverage_truth"]["status"] == "ahj_not_supported"
    assert payload["location"] == {"city": "Nowhereville", "state": "CA"}
    _assert_forbidden_keys_absent(payload)


def test_phase7l_finalize_and_apply_path_defensively_preserve_unsupported_fail_closed(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    polluted = {
        "ahj_status": "unsupported",
        "coverage_truth": {"status": "ahj_not_supported"},
        "message": "PermitAssist does not support this AHJ yet.",
        "apply_url": "https://example.gov/apply",
        "apply_phone": "555-111-2222",
        "apply_google_maps": "https://maps.example.test",
        "permits_required": [{"permit_type": "Commercial Building Permit"}],
        "permit_type": "Commercial Building Permit",
        "permit_name": "Commercial Building Permit",
        "inspections": ["final"],
        "approval_timeline": {"simple": "2 weeks"},
        "fee_range": "$1,000",
        "companion_reviews_triggers": ["fire"],
        "_evidence_pack": {"enabled": True},
        "claim_citations": [{"field": "apply_url"}],
    }

    finalized = server.finalize_permit_lookup_result(polluted, "medical clinic TI", "Nowhereville", "CA")
    server.build_apply_path(finalized, "medical clinic TI", "Nowhereville", "CA")

    assert finalized["ahj_status"] == "unsupported"
    assert finalized["coverage_truth"]["status"] == "ahj_not_supported"
    _assert_forbidden_keys_absent(finalized)


def test_phase7l_api_permit_blocks_faketown_zz_before_research_and_without_supported_fields(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    called = {"research": False}

    def fail_if_called(*args, **kwargs):
        called["research"] = True
        raise AssertionError("research_permit must not run for invalid AHJ")

    monkeypatch.setattr(server, "research_permit", fail_if_called)

    with _LiveServer(server.Handler) as live:
        status, body = _post_json(
            f"{live.base}/api/permit",
            {"job_type": "restaurant TI with Type I hood", "city": "Faketown", "state": "ZZ", "job_category": "commercial"},
            {"X-Sample-Demo": "1"},
        )

    assert status == 400
    _assert_unsupported_contract(body, "invalid_state")
    assert called["research"] is False


def test_phase7l_api_permit_blocks_springfield_zz_with_structured_response_not_request_error(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "research_permit", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no research")))

    with _LiveServer(server.Handler) as live:
        status, body = _post_json(
            f"{live.base}/api/permit",
            {"job_type": "office tenant improvement", "city": "Springfield", "state": "ZZ", "job_category": "commercial"},
            {"X-Sample-Demo": "1"},
        )

    assert status == 400
    _assert_unsupported_contract(body, "invalid_state")


def test_ef02_api_permit_blocks_unsupported_ahj_before_queue_and_lookup(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    called = {"acquire": False, "research": False}

    class _SemaphoreProbe:
        def acquire(self, *args, **kwargs):
            called["acquire"] = True
            return True

        def release(self):
            called["released"] = True

    monkeypatch.setattr(server, "PERMIT_LOOKUP_SEMAPHORE", _SemaphoreProbe())

    def fail_if_called(job_type, city, state, zip_code, **kwargs):
        called["research"] = True
        raise AssertionError("research_permit must not run for unsupported AHJs")

    monkeypatch.setattr(server, "research_permit", fail_if_called)
    monkeypatch.setattr(server, "validate_url", lambda *args, **kwargs: True)

    with _LiveServer(server.Handler) as live:
        status, body = _post_json(
            f"{live.base}/api/permit",
            {"job_type": "medical clinic tenant improvement", "city": "Nowhereville", "state": "CA", "job_category": "commercial"},
            {"X-Sample-Demo": "1"},
        )

    payload = _assert_unsupported_contract(body, "unsupported")
    assert status == 422
    assert called["acquire"] is False
    assert called["research"] is False
    assert called.get("released") is None
    assert payload.get("permit_verdict") != "YES"


def test_ef02_real_unverified_us_city_still_gets_lookup_with_warning_not_not_supported(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    called = {"acquire": False, "research": False}

    class _SemaphoreProbe:
        def acquire(self, *args, **kwargs):
            called["acquire"] = True
            return True

        def release(self):
            called["released"] = True

    def fake_research(job_type, city, state, zip_code, **kwargs):
        called["research"] = True
        return {
            "permit_verdict": "YES",
            "permit_type": "Building Permit — Tenant Improvement / Office Interior Alteration",
            "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Office Interior Alteration", "required": True}],
            "apply_url": "https://www.springfield.il.us/permits",
            "sources": ["https://www.springfield.il.us/permits"],
            "confidence": "medium",
        }

    monkeypatch.setattr(server, "PERMIT_LOOKUP_SEMAPHORE", _SemaphoreProbe())
    monkeypatch.setattr(server, "research_permit", fake_research)
    monkeypatch.setattr(server, "validate_url", lambda *args, **kwargs: True)

    with _LiveServer(server.Handler) as live:
        status, body = _post_json(
            f"{live.base}/api/permit",
            {"job_type": "office tenant improvement", "city": "Springfield", "state": "IL", "job_category": "commercial"},
            {"X-Sample-Demo": "1"},
        )

    payload = json.loads(body)
    assert status == 200
    assert called["acquire"] is True
    assert called["research"] is True
    assert called["released"] is True
    assert payload["ahj_status"] == "unverified"
    assert payload["coverage_truth"]["status"] == "jurisdiction_unverified"
    assert payload["jurisdiction_verification"] == "unverified_us_jurisdiction"
    assert payload["permits_required"]
    assert "not supported" not in json.dumps(payload).lower()


def test_phase7l_supported_city_still_follows_existing_lookup_path(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    called = {"research": False}

    def fake_research(job_type, city, state, zip_code, **kwargs):
        called["research"] = True
        return {
            "permit_verdict": "YES",
            "permit_type": "Building Permit — Tenant Improvement / Office Interior Alteration",
            "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Office Interior Alteration", "required": True}],
            "apply_url": "https://www.lacity.org/permits",
            "sources": ["https://www.lacity.org/permits"],
            "confidence": "medium",
        }

    monkeypatch.setattr(server, "research_permit", fake_research)
    monkeypatch.setattr(server, "validate_url", lambda *args, **kwargs: True)

    with _LiveServer(server.Handler) as live:
        status, body = _post_json(
            f"{live.base}/api/permit",
            {"job_type": "office tenant improvement", "city": "Los Angeles", "state": "CA", "job_category": "commercial"},
            {"X-Sample-Demo": "1"},
        )

    payload = json.loads(body)
    assert status == 200
    assert called["research"] is True
    assert payload.get("ahj_status") != "unsupported"
    assert "apply_path" in payload
    assert payload["permits_required"]


def test_ef02_batch_permit_item_blocks_unsupported_ahj_before_research(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    called = {"research": False}

    def fail_if_called(job_type, city, state, zip_code, **kwargs):
        called["research"] = True
        raise AssertionError("research_permit must not run for unsupported AHJs")

    monkeypatch.setattr(server, "research_permit", fail_if_called)

    with _LiveServer(server.Handler) as live:
        status, body = _post_json(
            f"{live.base}/api/batch-permit",
            {"lookups": [{"job_type": "restaurant TI with Type I hood", "city": "Nowhereville", "state": "CA", "job_category": "commercial"}]},
            {"X-Sample-Demo": "1"},
        )

    batch = json.loads(body)
    assert status == 200
    assert batch["summary"]["permits_required"] == 0
    assert called["research"] is False
    assert len(batch["results"]) == 1
    payload = batch["results"][0]
    assert payload["ahj_status"] == "unsupported"
    assert payload["coverage_truth"]["status"] == "ahj_not_supported"
    assert payload.get("permit_verdict") != "YES"
    _assert_forbidden_keys_absent(payload)


def test_ef02_api_v1_blocks_unsupported_ahj_for_paid_api_user_before_research(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_api_key", lambda header: ("paid@example.test", {"id": "k"}))
    monkeypatch.setattr(server, "is_paid_user", lambda email: True)
    monkeypatch.setattr(server, "validate_url", lambda *args, **kwargs: True)
    called = {"research": False}

    def fail_if_called(job_type, city, state, zip_code, **kwargs):
        called["research"] = True
        raise AssertionError("research_permit must not run for unsupported AHJs")

    monkeypatch.setattr(server, "research_permit", fail_if_called)

    with _LiveServer(server.Handler) as live:
        status, body = _post_json(
            f"{live.base}/api/v1/permit",
            {"job_type": "medical clinic tenant improvement", "city": "Nowhereville", "state": "CA", "job_category": "commercial"},
            {"Authorization": "Bearer test-api-key"},
        )

    payload = _assert_unsupported_contract(body, "unsupported")
    assert status == 422
    assert called["research"] is False
    assert payload.get("permit_verdict") != "YES"


def test_phase7l_api_v1_internal_errors_do_not_leak_exception_text(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_api_key", lambda header: ("paid@example.test", {"id": "k"}))
    monkeypatch.setattr(server, "is_paid_user", lambda email: True)

    def boom(*args, **kwargs):
        raise RuntimeError("raw protected production stack detail should not leak")

    monkeypatch.setattr(server, "research_permit", boom)

    with _LiveServer(server.Handler) as live:
        status, body = _post_json(
            f"{live.base}/api/v1/permit",
            {"job_type": "office tenant improvement", "city": "Los Angeles", "state": "CA", "job_category": "commercial"},
            {"Authorization": "Bearer test-api-key"},
        )

    payload = json.loads(body)
    assert status == 500
    assert payload["error"] == "internal_error"
    assert "raw protected production stack detail" not in body
    assert "RuntimeError" not in body
