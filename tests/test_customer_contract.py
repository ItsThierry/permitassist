import json
import re
from importlib import util
from pathlib import Path

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
assert _HELPER_SPEC is not None and _HELPER_SPEC.loader is not None
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)

ROOT = Path(__file__).resolve().parents[1]

BANNED_RE = re.compile(
    r"UNKNOWN|FAIL_CLOSED|fail closed|permit likely required|likely required|likely not required|"
    r"may be required|may require|might require|probably|maybe|possibly|unable to determine|"
    r"could not verify|not available from source-backed evidence|Permit answer not available|"
    r"verify with (?:the )?AHJ|permit_required[\"']?\s*:\s*(?:null|None)",
    re.I,
)

PR72_UNKNOWN_CASES = [
    ("panel upgrade", "Boston", "MA"),
    ("EV charger installation", "Denver", "CO"),
    ("solar PV with battery storage", "Seattle", "WA"),
    ("water heater replacement", "Phoenix", "AZ"),
    ("replace gas furnace", "Columbus", "OH"),
    ("commercial office tenant improvement", "Austin", "TX"),
    ("restaurant hood suppression retrofit", "Portland", "OR"),
    ("warehouse pallet racking", "Raleigh", "NC"),
    ("bathroom remodel with plumbing and electrical", "Boise", "ID"),
    ("kitchen remodel moving sink and circuits", "Atlanta", "GA"),
    ("roof replacement", "Charlotte", "NC"),
    ("deck addition", "Nashville", "TN"),
    ("pool installation", "Orlando", "FL"),
    ("egress window installation", "Minneapolis", "MN"),
    ("commercial medical clinic buildout", "Miami", "FL"),
    ("retail storefront sign", "Las Vegas", "NV"),
    ("fire alarm tenant improvement", "Dallas", "TX"),
    ("mechanical RTU replacement", "Chicago", "IL"),
    ("plumbing sewer line replacement", "Newark", "NJ"),
    ("interior wall relocation", "Sacramento", "CA"),
]

PRIOR_UNKNOWN_CASES = [
    ("commercial dental office tenant improvement", "Houston", "TX"),
    ("service panel replacement", "Omaha", "NE"),
    ("bathroom fan and duct replacement", "Madison", "WI"),
    ("ADU garage conversion", "Los Angeles", "CA"),
    ("tenant buildout for daycare", "Tampa", "FL"),
    ("new furnace and AC condenser", "St Louis", "MO"),
    ("paint only, no structural electrical plumbing mechanical layout occupancy or exterior work", "Akron", "OH"),
]


def _import_server(tmp_path, monkeypatch):
    _debug_helper._install_server_import_stubs()
    import sys

    sys.modules.pop("research_engine", None)
    sys.modules.pop("api.server", None)
    repo_root = Path(__file__).resolve().parents[1]
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
    return server


def _legacy_unknown_result():
    return {
        "permit_decision": "FAIL_CLOSED_UNSUPPORTED_OR_NO_EVIDENCE",
        "permit_verdict": "UNKNOWN",
        "permit_required": None,
        "permit_kind": "Local AHJ verification required",
        "permit_name": "",
        "permits_required": [],
        "customer_headline": "Permit answer not available from source-backed evidence.",
        "customer_next_step": "Verify with AHJ before relying on the answer.",
        "sources": [],
        "source_urls": [],
    }


def _assert_concrete_public(public):
    serialized = json.dumps(public, sort_keys=True, default=str)
    assert not BANNED_RE.search(serialized), serialized
    assert public["permit_decision"] in {"REQUIRED", "NOT_REQUIRED"}
    assert public["permit_required"] in {True, False}
    if public["permit_required"] is True:
        assert public.get("permit_kind")
        assert public.get("permits_required")
    else:
        assert public.get("not_required_reason") or "not required" in serialized.lower()
    assert public.get("customer_headline")
    assert public.get("customer_next_step")


def _assert_structured_input_rejection(public, *, expected_code="unsupported_jurisdiction"):
    serialized = json.dumps(public, sort_keys=True, default=str)
    assert public.get("input_status") == "rejected", serialized
    assert public.get("error_code") == expected_code, serialized
    assert expected_code in public.get("validation_errors", []), serialized
    assert public.get("permit_decision") not in {"REQUIRED", "NOT_REQUIRED"}, serialized
    assert public.get("permit_required") not in {True, False}, serialized
    assert "Madeupville ZZ Building Department" not in serialized
    assert "permit likely" not in serialized.lower()
    assert "maybe" not in serialized.lower()
    assert "probably" not in serialized.lower()


def test_replay_20_pr72_unknown_cases_resolve_concrete(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    for job_type, city, state in PR72_UNKNOWN_CASES:
        public = server.build_customer_permit_view_model(_legacy_unknown_result(), job_type, city, state)
        _assert_concrete_public(public)


def test_replay_7_prior_unknown_cases_resolve_concrete(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    for job_type, city, state in PRIOR_UNKNOWN_CASES:
        public = server.build_customer_permit_view_model(_legacy_unknown_result(), job_type, city, state)
        _assert_concrete_public(public)


def test_source_starvation_degrades_source_not_decision(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    raw = _legacy_unknown_result()
    raw.update({"retrieval_diagnostics": {"source_url_count": 0}, "warnings": ["source retrieval failed"]})
    public = server.build_customer_permit_view_model(raw, "commercial office tenant improvement", "Austin", "TX")
    _assert_concrete_public(public)
    assert public["permit_decision"] == "REQUIRED"
    assert public.get("degraded_sources") is True
    assert public.get("source_support", {}).get("decision_mutation_allowed") is False


def test_http_429_502_still_returns_concrete_decision(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    raw = _legacy_unknown_result()
    raw.update({"retrieval_diagnostics": {"status_codes": [429, 502], "error": "bad gateway / rate limit"}})
    public = server.build_customer_permit_view_model(raw, "EV charger installation", "Denver", "CO")
    _assert_concrete_public(public)
    assert public["permit_decision"] == "REQUIRED"
    assert public.get("degraded_sources") is True


def test_madeupville_zz_is_structured_input_rejection_before_customer_decision(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    direct = server.resolve_customer_decision({"result": {}, "job_type": "commercial remodel", "city": "Madeupville", "state": "ZZ"})
    public = server.build_customer_permit_view_model(_legacy_unknown_result(), "commercial remodel", "Madeupville", "ZZ")

    _assert_structured_input_rejection(direct)
    _assert_structured_input_rejection(public)


def test_garbage_non_us_state_code_is_structured_input_rejection(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    public = server.build_customer_permit_view_model(_legacy_unknown_result(), "panel upgrade", "Springfield", "XX9")

    _assert_structured_input_rejection(public)


def test_missing_state_is_structured_input_rejection(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    public = server.build_customer_permit_view_model(_legacy_unknown_result(), "water heater replacement", "Phoenix", "")

    _assert_structured_input_rejection(public)


def test_valid_us_jurisdiction_still_returns_concrete_never_unknown(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    public = server.build_customer_permit_view_model(_legacy_unknown_result(), "water heater replacement", "Phoenix", "AZ")

    _assert_concrete_public(public)
    assert public["permit_decision"] in {"REQUIRED", "NOT_REQUIRED"}
    assert public["permit_required"] in {True, False}


def test_lookup_share_report_checklist_public_surfaces_banned_text_scan(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    public = server.build_customer_permit_view_model(_legacy_unknown_result(), "water heater replacement", "Phoenix", "AZ")
    slug = server.create_share("water heater replacement", "Phoenix", "AZ", public)
    share = server.get_share(slug)
    checklist = server.get_or_create_checklist(public, "water heater replacement", "Phoenix", "AZ")
    report = server.render_white_label_report_html({"result": public, "job_type": "water heater replacement", "city": "Phoenix", "state": "AZ"})
    for surface in (public, share, checklist, report):
        text = surface if isinstance(surface, str) else json.dumps(surface, sort_keys=True, default=str)
        assert not BANNED_RE.search(text), text


def test_static_customer_paths_do_not_assign_unknown_fail_closed_or_likely_maybe():
    customer_paths = [ROOT / "api" / "server.py", ROOT / "api" / "permit_decision.py"]
    banned = re.compile(
        r"permit_verdict\s*=\s*[\"']UNKNOWN|permit_required\s*=\s*None|"
        r"permit_decision\s*=\s*[\"']FAIL_CLOSED|_force_fail_closed_no_local_evidence|"
        r"apply_final_source_floor_gate|Permit answer not available|could not verify|"
        r"permit likely required|likely required|may be required|maybe|probably",
        re.I,
    )
    hits = []
    for path in customer_paths:
        text = path.read_text(encoding="utf-8")
        for idx, line in enumerate(text.splitlines(), 1):
            if "BANNED" in line or "banned" in line:
                continue
            if banned.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{idx}:{line.strip()}")
    assert hits == []
