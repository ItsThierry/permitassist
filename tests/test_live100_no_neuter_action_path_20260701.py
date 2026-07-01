import copy
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_customer_pov_20260701T123737Z"
BASELINE_PATH = ROOT / "tests" / "fixtures" / "live100_no_neuter_baseline_20260701.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

pytestmark = pytest.mark.skipif(
    not (ARTIFACT_ROOT / "cases.jsonl").exists() or not BASELINE_PATH.exists(),
    reason="Live100 customer POV artifact/baseline is absent; action-path contracts run only in artifact-rich worktrees.",
)

COMMERCIAL_TIMELINE = "Commercial TI/addition/remodel scopes usually require plan review"
RESIDENTIAL_TIMELINE_FALSE_POSITIVES = {"R-007", "R-018"}
KNOWN_C_ACTION_PATH_CASES = {"R-015", "C-025", "R-031", "R-048"}

# Deterministic 404/410/NXDOMAIN semantics are tested separately from request-time behavior.
BROKEN_APPLY_BY_CASE = {
    "R-003": "https://aca-prod.accela.com/PORTLAND",
    "R-015": "https://aca-prod.accela.com/NASHVILLE",
    "R-020": "https://www.boston.gov/departments/inspectional-services/apply-permit-online",
    "R-027": "https://www.houstonpermittingcenter.org/online-permitting",
    "R-030": "https://www.cityofmadison.com/building-inspection/permits",
    "R-040": "https://detroitmi.gov/departments/buildings-safety-engineering-and-environmental-department/bseed-online-services",
    "R-041": "https://www.burlingtonvt.gov/DPZ",
    "R-042": "https://www.santafenm.gov/community_development/permits",
    "R-043": "https://www.charleston-sc.gov/1075/Inspections",
    "R-048": "https://www.littlerock.gov/city-administration/departments/planning-and-development/building-codes",
    "C-018": "https://aca-prod.accela.com/PORTLAND",
    "C-019": "https://www.clarkcountynv.gov/business/building/permits",
    "C-025": "https://aca-prod.accela.com/NASHVILLE",
    "C-031": "https://www.cityofmadison.com/building-inspection/permits",
    "C-040": "https://detroitmi.gov/departments/buildings-safety-engineering-and-environmental-department/bseed-online-services",
    "C-042": "https://www.santafenm.gov/community_development/permits",
    "C-043": "https://www.charleston-sc.gov/1075/Inspections",
    "C-048": "https://www.littlerock.gov/city-administration/departments/planning-and-development/building-codes",
}

EXPECTED_OFFICIAL_URL_SNIPPETS = {
    "R-015": ("nashville.gov/departments/codes/construction-and-permits/e-permits-system", "epermits.nashville.gov"),
    "C-025": ("nashville.gov/departments/codes/construction-and-permits/e-permits-system", "epermits.nashville.gov"),
    "R-031": ("wycokck.org/Departments/Neighborhood-Resource-Center/Building-Inspection",),
    "R-048": ("littlerock.gov/government/city-departments/planning-and-development",),
}


def _install_server_stubs() -> None:
    requests_stub = types.ModuleType("requests")
    requests_stub.post = lambda *a, **k: None
    requests_stub.get = lambda *a, **k: None
    requests_stub.head = lambda *a, **k: types.SimpleNamespace(status_code=200)
    requests_stub.exceptions = types.SimpleNamespace(Timeout=TimeoutError)
    sys.modules["requests"] = requests_stub

    openai_stub = types.ModuleType("openai")
    openai_stub.OpenAI = lambda *a, **k: object()
    sys.modules["openai"] = openai_stub

    google_stub = types.ModuleType("google")
    genai_stub = types.ModuleType("google.generativeai")
    genai_stub.configure = lambda *a, **k: None
    sys.modules["google"] = google_stub
    sys.modules["google.generativeai"] = genai_stub

    research_stub = types.ModuleType("research_engine")
    research_stub.research_permit = lambda *a, **k: {"permit_verdict": "MAYBE"}
    research_stub.build_google_maps_url = lambda *a, **k: ""
    research_stub.strip_pdf_from_result = lambda result: result
    research_stub.get_cache_hit_rate = lambda: 0
    research_stub.detect_primary_scope = lambda job_type: {"primary_scope": "generic", "signals": []}
    research_stub.classify_scope_required_permits = lambda job_type, city="", state="", scope_contract=None: []
    research_stub.classify_source_tier = lambda url, city="", state="", result=None: "local"
    research_stub.classify_source_authority = lambda url, city="", state="", result=None: {"category": "local_ahj", "tier": "local_ahj", "display_allowed": True, "local_decision_evidence": True}
    sys.modules["research_engine"] = research_stub


@pytest.fixture()
def server(tmp_path, monkeypatch):
    _install_server_stubs()
    monkeypatch.setenv("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
    monkeypatch.setenv("FREE_LOOKUP_DB", str(tmp_path / "ip_lookups.db"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    for name in ("server", "api.server"):
        sys.modules.pop(name, None)
    from api import server as server_mod

    server_mod.CACHE_DB = str(tmp_path / "cache.db")
    server_mod.DATA_DIR = str(tmp_path)
    server_mod.init_db()
    return server_mod


@pytest.fixture(scope="session")
def baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text())


def _records() -> list[dict]:
    return [json.loads(line) for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines() if line.strip()]


def _record(case_id: str) -> dict:
    for rec in _records():
        if rec["case"]["id"] == case_id:
            return rec
    raise KeyError(case_id)


def _build_public(server, case_id: str) -> dict:
    rec = _record(case_id)
    case = rec["case"]
    return server.build_customer_permit_view_model(
        copy.deepcopy(rec["response_body"]),
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case.get("segment"),
    )


def _canonical_family(value: str) -> str:
    value = str(value or "").lower().strip().replace("-", "_")
    return {"zoning": "planning", "land_use": "planning", "occupancy": "co", "right_of_way": "grading", "row": "grading", "wastewater/fog": "wastewater", "food/health": "health"}.get(value, value)


def _row_family(server, row: dict) -> str:
    return _canonical_family(server._customer_row_family(row) or row.get("filing_family") or row.get("family") or "")


def _row_status(server, row: dict) -> str:
    return str(server._customer_row_status(row) or row.get("status") or row.get("decision") or "").upper()


def _visible_rows(public: dict) -> list[dict]:
    rows: list[dict] = []
    for key in ("permits_required", "related_permits", "companion_permits", "trade_permits"):
        rows.extend(row for row in public.get(key) or [] if isinstance(row, dict))
    return rows


def _required_families(server, public: dict) -> set[str]:
    return {
        _row_family(server, row)
        for row in public.get("permits_required") or []
        if isinstance(row, dict) and (_row_status(server, row) == "REQUIRED" or row.get("required") is True)
    }


def _visible_families(server, public: dict) -> set[str]:
    return {_row_family(server, row) for row in _visible_rows(public)}


def _required_names(public: dict) -> set[str]:
    return {
        str(row.get("permit_name") or row.get("permit_type") or "").strip()
        for row in public.get("permits_required") or []
        if isinstance(row, dict) and str(row.get("permit_name") or row.get("permit_type") or "").strip()
    }


def _url_set(public: dict) -> set[str]:
    urls: set[str] = set()
    for key in ("apply_url", "online_application_url"):
        if public.get(key):
            urls.add(str(public[key]))
    apply_path = public.get("apply_path") if isinstance(public.get("apply_path"), dict) else {}
    for key in ("portal_url", "url", "source_url"):
        if apply_path.get(key):
            urls.add(str(apply_path[key]))
    for url in public.get("source_urls") or []:
        if isinstance(url, str) and url:
            urls.add(url)
    for src in public.get("sources") or []:
        if isinstance(src, dict) and src.get("url"):
            urls.add(str(src["url"]))
    return urls


def _primary_apply_urls(public: dict) -> set[str]:
    urls = {str(public.get(key) or "") for key in ("apply_url", "online_application_url") if public.get(key)}
    apply_path = public.get("apply_path") if isinstance(public.get("apply_path"), dict) else {}
    for key in ("portal_url", "url", "source_url"):
        if apply_path.get(key):
            urls.add(str(apply_path[key]))
    return urls


def test_phase0_no_neuter_decisions_and_family_sets_frozen(server, baseline):
    """T1/T2: compare decision + family/package sets, not full rendered-text hashes."""
    for rec in _records():
        case_id = rec["case"]["id"]
        public = _build_public(server, case_id)
        expected = baseline["cases"][case_id]
        decision = str(public.get("permit_decision") or "").upper().strip()
        assert decision in {"REQUIRED", "NOT_REQUIRED"}, {"case": case_id, "public": public}
        assert decision == expected["decision"], {"case": case_id, "expected": expected["decision"], "public": public}
        assert sorted(_required_families(server, public)) == expected["required_families"], {"case": case_id, "public": public}
        assert sorted(_visible_families(server, public)) == expected["visible_families"], {"case": case_id, "public": public}
        if decision == "REQUIRED":
            assert _required_names(public), {"case": case_id, "public": public}


@pytest.mark.parametrize("case_id", sorted(KNOWN_C_ACTION_PATH_CASES))
def test_known_c_action_path_cases_have_official_non_broken_filing_path(server, case_id):
    """T3/T4: fixed cases keep decision but gain official evidence-backed action path/source fallback."""
    public = _build_public(server, case_id)
    assert public.get("permit_decision") == "REQUIRED", {"case": case_id, "public": public}
    urls = _url_set(public)
    assert urls, {"case": case_id, "public": public}
    lowered = "\n".join(sorted(urls)).lower()
    assert any(snippet.lower() in lowered for snippet in EXPECTED_OFFICIAL_URL_SNIPPETS[case_id]), {"case": case_id, "urls": sorted(urls), "public": public}
    broken = BROKEN_APPLY_BY_CASE.get(case_id, "").lower()
    if broken:
        assert broken not in {url.lower() for url in _primary_apply_urls(public)}, {"case": case_id, "broken_primary": broken, "urls": sorted(_primary_apply_urls(public)), "public": public}


@pytest.mark.parametrize("case_id,broken_url", sorted(BROKEN_APPLY_BY_CASE.items()))
def test_broken_apply_urls_are_not_sole_customer_action_path(server, case_id, broken_url):
    """T3: deterministic 404 URLs must not remain the sole/primary filing path for REQUIRED results."""
    public = _build_public(server, case_id)
    if public.get("permit_decision") != "REQUIRED":
        return
    primary = {url.lower() for url in _primary_apply_urls(public)}
    all_urls = {url.lower() for url in _url_set(public)}
    assert broken_url.lower() not in primary, {"case": case_id, "broken_url": broken_url, "public": public}
    assert all_urls - {broken_url.lower()}, {"case": case_id, "broken_url": broken_url, "public": public}


def test_commercial_timeline_classifier_residential_veto_and_all_positive_retention(server, baseline):
    """T5: residential false positives are removed; every enumerated true commercial positive retains rich timeline copy."""
    baseline_positive = set(baseline["commercial_timeline_positive_case_ids"])
    expected_positive = baseline_positive - RESIDENTIAL_TIMELINE_FALSE_POSITIVES
    observed_positive = set()
    for rec in _records():
        case_id = rec["case"]["id"]
        public = _build_public(server, case_id)
        text = json.dumps(public.get("approval_timeline") or {}, sort_keys=True)
        if COMMERCIAL_TIMELINE.lower() in text.lower():
            observed_positive.add(case_id)
    assert not (RESIDENTIAL_TIMELINE_FALSE_POSITIVES & observed_positive), sorted(RESIDENTIAL_TIMELINE_FALSE_POSITIVES & observed_positive)
    assert expected_positive <= observed_positive, {"missing_true_commercial": sorted(expected_positive - observed_positive), "observed": sorted(observed_positive)}


def test_mixed_use_commercial_residential_words_do_not_veto_ti_timeline(server):
    """Residential-veto-first must be anchored so mixed-use commercial text with dwelling words keeps commercial TI behavior."""
    result = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_name": "Commercial Building / Tenant Improvement Permit",
        "permit_kind": "Commercial Building / Tenant Improvement",
        "permit_type": "Commercial Building / Tenant Improvement Permit",
        "job_summary": "Commercial mixed-use tenant improvement in ground-floor retail below dwelling units.",
        "applying_office": "Test Building Department",
        "apply_url": "https://www.nyc.gov/site/buildings/index.page",
        "source_urls": ["https://www.nyc.gov/site/buildings/index.page"],
        "sources": [{"url": "https://www.nyc.gov/site/buildings/index.page", "title": "NYC Department of Buildings", "source_type": "official_local"}],
        "approval_timeline": {"simple": "same day / OTC", "complex": ""},
        "permits_required": [{"permit_type": "Commercial Building / Tenant Improvement Permit", "permit_name": "Commercial Building / Tenant Improvement Permit", "family": "building", "filing_family": "building", "status": "REQUIRED", "decision": "REQUIRED", "required": True}],
    }
    public = server.build_customer_permit_view_model(
        result,
        "commercial tenant improvement in ground-floor retail below dwelling units; no residential unit work",
        "New York",
        "NY",
        job_category="commercial",
    )
    text = json.dumps(public.get("approval_timeline") or {}, sort_keys=True)
    assert COMMERCIAL_TIMELINE.lower() in text.lower(), public


def test_url_status_semantics_units(server):
    classify = getattr(server, "classify_customer_url_status", None)
    assert callable(classify), "classify_customer_url_status must expose offline URL status semantics"
    assert classify(http_status=200, error=None, body="Permit Center") == "ok"
    assert classify(http_status=302, error=None, body="") == "ok"
    assert classify(http_status=404, error=None, body="not found") == "broken"
    assert classify(http_status=410, error=None, body="gone") == "broken"
    assert classify(http_status=None, error="NXDOMAIN", body="") == "broken"
    assert classify(http_status=403, error=None, body="forbidden") == "unknown"
    assert classify(http_status=429, error=None, body="rate limited") == "unknown"
    assert classify(http_status=500, error=None, body="server error") == "unknown"
    assert classify(http_status=None, error="timeout", body="") == "unknown"
    assert classify(http_status=200, error=None, body="404 - File or directory not found") == "broken"
