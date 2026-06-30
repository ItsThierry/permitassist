import copy
import csv
import json
import os
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
ARTIFACT_ROOT = ROOT / "artifacts" / "live100_customer_opus_20260630T082609Z"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

pytestmark = pytest.mark.skipif(
    not (ARTIFACT_ROOT / "cases.jsonl").exists(),
    reason="Live100 local artifact bundle is absent; artifact-backed fix-for-good contracts run in artifact-rich worktrees.",
)


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

    def classify_scope_required_permits(job_type, city="", state="", scope_contract=None):
        return []

    research_stub.research_permit = lambda *a, **k: {"permit_verdict": "MAYBE"}
    research_stub.build_google_maps_url = lambda *a, **k: ""
    research_stub.strip_pdf_from_result = lambda result: result
    research_stub.get_cache_hit_rate = lambda: 0
    research_stub.detect_primary_scope = lambda job_type: {"primary_scope": "generic", "signals": []}
    research_stub.classify_scope_required_permits = classify_scope_required_permits
    research_stub.classify_source_tier = lambda url, city="", state="", result=None: "local"
    research_stub.classify_source_authority = lambda url, city="", state="", result=None: {"category": "local_ahj", "tier": "local_ahj", "display_allowed": True}
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


def _records() -> list[dict]:
    return [json.loads(line) for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines() if line.strip()]


def _record(case_id: str) -> dict:
    for rec in _records():
        if rec["case"]["id"] == case_id:
            return rec
    raise KeyError(case_id)


def _grades() -> dict[str, dict]:
    with (ARTIFACT_ROOT / "FINAL_TITI_OPUS_GRADES.csv").open(newline="") as f:
        return {row["id"]: row for row in csv.DictReader(f)}


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


def _row_family(server, row: dict) -> str:
    return str(server._customer_row_family(row) or row.get("filing_family") or row.get("family") or "").lower()


def _row_status(server, row: dict) -> str:
    return str(server._customer_row_status(row) or row.get("status") or row.get("decision") or "").upper()


def _visible_rows(public: dict) -> list[dict]:
    rows = []
    for key in ("permits_required", "related_permits", "companion_permits", "trade_permits"):
        rows.extend([row for row in public.get(key) or [] if isinstance(row, dict)])
    return rows


def _families_by_status(server, public: dict) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for row in _visible_rows(public):
        out.setdefault(_row_family(server, row), set()).add(_row_status(server, row))
    return out


def _required_families(server, public: dict) -> set[str]:
    return {
        _row_family(server, row)
        for row in public.get("permits_required") or []
        if isinstance(row, dict) and (_row_status(server, row) == "REQUIRED" or row.get("required") is True)
    }


def _public_text(public: dict) -> str:
    return json.dumps(public, sort_keys=True, default=str).lower()


# ---------------------------------------------------------------------------
# RED: grader/scanner must be clean before product validation.
# ---------------------------------------------------------------------------


def test_clean_grader_url_aware_leak_scanner():
    from scripts.live100_fix_for_good_offline_replay_20260630 import debug_leaks

    payload = {
        "official_home": "https://www.honolulu.gov/dpp/home/permits/index.html",
        "official_app_hash": "https://aca-prod.accela.com/ATLANTA/app/#/permit/start",
        "real_home_path": "debug artifact /home/boban/projects/permitassist/private.json",
        "real_app_path": "runtime path /app/secrets/config.json",
        "secret": "PERMITASSIST_ADMIN_TOKEN and RAILWAY_TOKEN",
    }
    leaks = debug_leaks(payload)
    assert "https://www.honolulu.gov/dpp/home/permits/index.html" not in leaks
    assert "https://aca-prod.accela.com/ATLANTA/app/#/permit/start" not in leaks
    assert any("/home/boban" in leak for leak in leaks)
    assert any("/app/secrets" in leak for leak in leaks)
    assert any("PERMITASSIST_ADMIN_TOKEN" in leak or "RAILWAY_TOKEN" in leak for leak in leaks)


def test_clean_grader_segment_contamination_scans_customer_rows_only():
    from scripts.live100_fix_for_good_offline_replay_20260630 import segment_contamination_issues

    safe = {
        "permit_decision": "REQUIRED",
        "permit_name": "Commercial Building / Tenant Improvement Permit",
        "permit_kind": "Commercial Building / Tenant Improvement",
        "customer_headline": "Permit required: commercial TI.",
        "customer_next_step": "File with the commercial permit office.",
        "permits_required": [{"permit_type": "Commercial Building / Tenant Improvement Permit", "filing_family": "building", "status": "REQUIRED"}],
        "sources": [{"title": "Residential and commercial permit source boilerplate", "url": "https://city.example.gov/residential-and-commercial/permits"}],
    }
    contaminated = copy.deepcopy(safe)
    contaminated["permits_required"].append({"permit_type": "Residential Plumbing Permit — Water Heater Replacement", "filing_family": "plumbing", "status": "REQUIRED"})

    assert segment_contamination_issues({"segment": "commercial"}, safe) == []
    assert segment_contamination_issues({"segment": "commercial"}, contaminated)


def test_clean_grader_family_coverage_recognizes_panel_service_synonyms():
    from scripts.live100_fix_for_good_offline_replay_20260630 import family_present

    text = "Electrical Permit — Service Upgrade (200A). Coordinate meter/main inspection."
    assert family_present("panel", text)
    assert family_present("service panel", text)
    assert family_present("electrical", text)


# ---------------------------------------------------------------------------
# RED: request-scope facts and product contracts.
# ---------------------------------------------------------------------------


def test_scopefacts_extracts_special_review_exterior_and_negative_scope_model():
    from api.scope_contract import build_scope_facts

    facts = build_scope_facts(
        "French Quarter restaurant replaces exterior sign and paints facade; no walls no MEP no occupancy change",
        "New Orleans",
        "LA",
        job_category="commercial",
    )
    data = facts.as_dict()
    assert "historic" in facts.special_signals
    assert "exterior_alteration" in facts.special_signals
    assert "no_walls" in data["negative_scope_facts"]
    assert "no_mep" in data["negative_scope_facts"]
    assert "no_occupancy_change" in data["negative_scope_facts"]


@pytest.mark.parametrize("case_id,expected_decision,required_families", [
    ("R010", "NOT_REQUIRED", set()),
    ("R036", "REQUIRED", {"historic"}),
])
def test_r010_r036_boundary_pair(server, case_id, expected_decision, required_families):
    public = _build_public(server, case_id)
    assert public.get("permit_decision") == expected_decision, {"case": case_id, "public": public}
    assert _required_families(server, public) >= required_families
    if case_id == "R036":
        text = _public_text(public)
        assert re.search(r"\b(hdlc|certificate of appropriateness|coa|historic|design review)\b", text)
    if case_id == "R010":
        assert public.get("permits_required") == []
        assert "historic" not in _required_families(server, public)


@pytest.mark.parametrize("case_id,visible", [
    ("R009", {"electrical": {"REQUIRED"}}),
    ("R011", {"plumbing": {"REQUIRED"}, "electrical": {"REQUIRED"}}),
    ("R035", {"building": {"REQUIRED"}, "electrical": {"REQUIRED", "VERIFY", "CONDITIONAL"}}),
    ("R042", {"historic": {"REQUIRED", "VERIFY", "CONDITIONAL"}, "building": {"REQUIRED", "VERIFY", "CONDITIONAL"}}),
    ("C014", {"mechanical": {"REQUIRED", "VERIFY", "CONDITIONAL"}}),
    ("C023", {"mechanical": {"REQUIRED", "VERIFY", "CONDITIONAL"}}),
    ("C032", {"fire": {"REQUIRED", "VERIFY", "CONDITIONAL"}, "mechanical": {"REQUIRED"}}),
    ("C033", {"fire": {"REQUIRED", "VERIFY", "CONDITIONAL"}, "mechanical": {"REQUIRED"}}),
    ("C034", {"mechanical": {"REQUIRED"}, "health": {"REQUIRED", "VERIFY", "CONDITIONAL"}, "plumbing": {"REQUIRED", "VERIFY", "CONDITIONAL"}}),
    ("C036", {"fire": {"REQUIRED"}}),
    ("C048", {"historic": {"REQUIRED"}}),
])
def test_scope_noun_family_union_and_special_review_visibility(server, case_id, visible):
    public = _build_public(server, case_id)
    statuses = _families_by_status(server, public)
    for family, allowed in visible.items():
        assert statuses.get(family, set()) & allowed, {"case": case_id, "family": family, "allowed": allowed, "statuses": statuses, "public": public}


@pytest.mark.parametrize("case_id,expected_decision,expected_primary,forbidden_required", [
    ("R007", "REQUIRED", "building", set()),
    ("R039", "REQUIRED", "building", {"grading"}),
    ("R045", "REQUIRED", "electrical", {"building", "plumbing", "mechanical"}),
    ("C022", "NOT_REQUIRED", "", {"building"}),
    ("C035", "REQUIRED", "electrical", {"plumbing", "mechanical"}),
    ("C046", "REQUIRED", "plumbing", {"building", "mechanical", "electrical"}),
    ("C049", "REQUIRED", "mechanical", {"building"}),
])
def test_primary_resolver_and_negative_scope_suppression(server, case_id, expected_decision, expected_primary, forbidden_required):
    public = _build_public(server, case_id)
    required = _required_families(server, public)
    assert public.get("permit_decision") == expected_decision, {"case": case_id, "public": public}
    if expected_primary:
        first = public.get("permits_required", [{}])[0]
        assert _row_family(server, first) == expected_primary, {"case": case_id, "required": public.get("permits_required")}
    assert not (required & forbidden_required), {"case": case_id, "forbidden": required & forbidden_required, "required": required, "public": public}


def test_segment_row_title_invariant_canonicalizes_commercial_package_without_neutering(server):
    public = _build_public(server, "C006")
    rows_text = json.dumps(public.get("permits_required") or [], sort_keys=True).lower()
    assert public.get("permit_decision") == "REQUIRED"
    assert "plumbing" in _required_families(server, public)
    assert "residential plumbing permit" not in rows_text
    assert "single-family" not in rows_text


def test_source_backed_fire_health_not_neutered_by_cosmetic_negative_scope():
    from api import permit_model as pm
    from api.scope_contract import build_scope_facts

    facts = build_scope_facts(
        "commercial restaurant repaint only no wall MEP or occupancy change",
        "Dallas",
        "TX",
        job_category="commercial",
    )
    source_support = pm.SourceSupport(urls=("https://dallas.gov/fire-health",), jurisdiction=("Dallas", "TX"), official_count=1)
    fire_row = pm.make_row(pm.PermitFamily.FIRE, "Fire Department Source-Backed Review", pm.PermitStatus.REQUIRED, "Official source requires this safety review.", pm.PermitSegment.COMMERCIAL)
    health_row = pm.make_row(pm.PermitFamily.HEALTH, "Health Department Source-Backed Review", pm.PermitStatus.REQUIRED, "Official source requires this health review.", pm.PermitSegment.COMMERCIAL)
    fire_row["source_url"] = "https://dallas.gov/fire-health"
    health_row["source_url"] = "https://dallas.gov/fire-health"
    building_item = pm._row_with_status(pm.PermitFamily.BUILDING, "Commercial Building / Tenant Improvement Permit", pm.PermitStatus.REQUIRED, source_support, "Synthesized generic building row.", pm.PermitSegment.COMMERCIAL, synthesized_governing=True)
    required = [
        building_item,
        pm.item_from_row(fire_row, source_support, scope_segment="commercial"),
        pm.item_from_row(health_row, source_support, scope_segment="commercial"),
    ]

    next_required, related, decision, _primary = pm._apply_universal_invariant_gates(required, [], "REQUIRED", source_support, facts, {})

    required_families = {item.family for item in next_required if item.required}
    related_families = {item.family for item in related}
    assert pm.PermitFamily.FIRE in required_families
    assert pm.PermitFamily.HEALTH in required_families
    assert pm.PermitFamily.BUILDING not in required_families
    assert pm.PermitFamily.BUILDING in related_families
    assert decision == "REQUIRED"


def test_full_100_final_a_b_no_regression_and_decision_taxonomy_guard(server):
    grades = _grades()
    final_ab = {case_id for case_id, row in grades.items() if row["final_grade"] in {"A", "B"}}
    assert len(final_ab) == 85
    failures = []
    taxonomy = {"REQUIRED": 0, "NOT_REQUIRED": 0, "VERIFY_ONLY": 0}
    for rec in _records():
        case = rec["case"]
        public = _build_public(server, case["id"])
        decision = str(public.get("permit_decision") or "").upper()
        required_rows = public.get("permits_required") or []
        if decision in taxonomy:
            taxonomy[decision] += 1
        if decision not in {"REQUIRED", "NOT_REQUIRED"}:
            taxonomy["VERIFY_ONLY"] += 1
        if case["id"] in final_ab:
            expected = str(case.get("expected_decision") or "").upper()
            # Source-researched final baseline overrides the pre-registered manifest for R010.
            if case["id"] == "R010":
                expected = "NOT_REQUIRED"
            if expected == "REQUIRED" and (decision != "REQUIRED" or not required_rows):
                failures.append({"case": case["id"], "problem": "AB_required_regressed", "decision": decision})
            if expected == "NOT_REQUIRED" and (decision != "NOT_REQUIRED" or required_rows):
                failures.append({"case": case["id"], "problem": "AB_not_required_regressed", "decision": decision, "rows": required_rows})
    assert failures == []
    assert taxonomy["VERIFY_ONLY"] == 0
    # Anti-neuter: the corpus should still contain a healthy mix of binary yes/no answers.
    assert taxonomy["REQUIRED"] >= 70
    assert taxonomy["NOT_REQUIRED"] >= 10
