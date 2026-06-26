import json
from pathlib import Path

from importlib import util

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
assert _HELPER_SPEC is not None
assert _HELPER_SPEC.loader is not None
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "v24_live_341_filing_path_replay.json"
GENERIC_SOURCE_HOST_TOKENS = ("iccsafe.org", "nfpa.org", "energy.gov")
VALID_REQUIRED_FILING_STATES = {"RESOLVED_PORTAL", "RESOLVED_COUNTER", "HONEST_FALLBACK"}


def _import_server(tmp_path, monkeypatch):
    _debug_helper._install_server_import_stubs()
    import sys

    sys.modules.pop("research_engine", None)
    sys.modules.pop("api.server", None)
    repo_root = ROOT
    api_root = repo_root / "api"
    for path in (str(repo_root), str(api_root)):
        if path not in sys.path:
            sys.path.insert(0, path)
    monkeypatch.setenv("FREE_LOOKUP_DB", str(tmp_path / "ip_lookups.db"))
    monkeypatch.setenv("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
    from api import server

    server.CACHE_DB = str(tmp_path / "cache.db")
    server.DATA_DIR = str(tmp_path)
    server.init_db()
    monkeypatch.setattr(server, "validate_url", lambda url, timeout=5: True)
    return server


def _fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _rows(*codes):
    wanted = set(codes)
    return [row for row in _fixture()["rows"] if wanted.intersection(row.get("issue_codes") or [])]


def _required_rows(rows):
    out = []
    for row in rows:
        body = row.get("response_body") or {}
        if body.get("permit_required") is True or str(body.get("permit_decision") or "").upper() == "REQUIRED":
            out.append(row)
    return out


def _public_from_replay_row(server, row):
    payload = row.get("request_payload") or {}
    body = row.get("response_body") or {}
    return server.build_customer_permit_view_model(
        body,
        payload.get("job_type", ""),
        payload.get("city", ""),
        payload.get("state", ""),
        job_category=payload.get("job_category"),
        explicit_vertical=payload.get("vertical"),
    )


def _assert_required_filing_path_contract(public):
    assert public.get("permit_decision") == "REQUIRED"
    assert public.get("permit_required") is True
    apply_path = public.get("apply_path")
    assert isinstance(apply_path, dict), public
    assert apply_path.get("state") in VALID_REQUIRED_FILING_STATES, apply_path
    apply_url = public.get("apply_url") or public.get("online_application_url") or apply_path.get("portal_url")
    if apply_path.get("state") == "RESOLVED_PORTAL":
        assert apply_url, apply_path
    else:
        assert apply_url in (None, ""), public
        assert apply_path.get("channel") in {"in_person_counter", "contact_ahj"}, apply_path
    next_step = (public.get("customer_next_step") or "").lower()
    if not apply_url:
        assert "use the local permit portal" not in next_step
        assert "no exact local filing portal is attached" in next_step or "no verified online filing url" in next_step


def test_replay_fixture_contains_expected_live_failure_sets():
    fixture = _fixture()
    summary = fixture["summary"]
    assert summary["total_rows"] == 341
    assert len(summary["missing_apply_path_ids"]) == 82
    assert len(summary["generic_only_source_support_ids"]) == 3
    assert len(summary["slow_response_ids"]) == 11
    assert len(summary["auth_or_rate_limit_ids"]) == 1
    assert len(summary["representative_pass_ids"]) >= 20


def test_required_missing_apply_rows_get_typed_honest_filing_path_without_decision_neutering(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    rows = _required_rows(_rows("missing_apply_path"))
    assert len(rows) == 82
    for row in rows:
        public = _public_from_replay_row(server, row)
        _assert_required_filing_path_contract(public)


def test_generic_model_code_sources_never_become_primary_filing_support(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    rows = _required_rows(_rows("generic_only_source_support"))
    assert len(rows) == 3
    for row in rows:
        public = _public_from_replay_row(server, row)
        _assert_required_filing_path_contract(public)
        apply_url = str(public.get("apply_url") or public.get("online_application_url") or "").lower()
        assert not any(token in apply_url for token in GENERIC_SOURCE_HOST_TOKENS)
        primary_filing_tier = (public.get("apply_path") or {}).get("primary_filing_source_tier")
        assert primary_filing_tier in {"none", "local_ahj", "county", "delegated_state"}
        if not apply_url:
            assert primary_filing_tier == "none"


def test_representative_passes_preserve_required_decisions_and_valid_portal_states(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    fixture = _fixture()
    pass_ids = set(fixture["summary"]["representative_pass_ids"])
    rows = [row for row in fixture["rows"] if row.get("id") in pass_ids]
    assert len(rows) >= 20
    for row in _required_rows(rows):
        public = _public_from_replay_row(server, row)
        _assert_required_filing_path_contract(public)


def test_api_key_create_response_nested_key_validates_for_v1_usage(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    email = "paid@example.com"
    response = {"api_key": server.create_api_key(email, "pytest nested key")}
    assert isinstance(response["api_key"], dict)
    assert response["api_key"]["key"].startswith("pa_live_")

    user_email, used_key = server.validate_api_key(f"Bearer {response['api_key']['key']}")
    assert user_email == email
    assert used_key == response["api_key"]["key"]

    # Guard the prior harness bug: passing the api_key object/dict string must not authenticate.
    bad_email, bad_key = server.validate_api_key(f"Bearer {response['api_key']}")
    assert bad_email is None
    assert bad_key == str(response["api_key"])
