import copy
import csv
import json
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_50_50_20260629T210943Z"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API) not in sys.path:
    sys.path.insert(0, str(API))


def _install_server_stubs() -> None:
    requests_stub = types.ModuleType("requests")
    requests_stub.post = lambda *a, **k: None
    requests_stub.get = lambda *a, **k: None
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
    research_stub.classify_source_authority = lambda url, city="", state="", result=None: {"category": "local_ahj", "display_allowed": True}
    sys.modules["research_engine"] = research_stub


def _server(tmp_path, monkeypatch):
    _install_server_stubs()
    monkeypatch.setenv("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
    monkeypatch.setenv("FREE_LOOKUP_DB", str(tmp_path / "ip_lookups.db"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    for name in ("server", "api.server"):
        sys.modules.pop(name, None)
    from api import server

    server.CACHE_DB = str(tmp_path / "cache.db")
    server.DATA_DIR = str(tmp_path)
    server.init_db()
    return server


def _public_from_model(raw, job, city="Testville", state="TX", category="commercial"):
    from api.permit_model import build_permit_package, capture_permit_authority_input, project_permit_package

    model_input, package = build_permit_package(capture_permit_authority_input(copy.deepcopy(raw)), job, city, state, {"category": category})
    return project_permit_package(model_input, package, job, city, state)


def _families(rows):
    return [str(row.get("filing_family") or row.get("family") or "") for row in rows]


def _visible_rows(public):
    return [
        row
        for key in ("permits_required", "family_decisions", "related_permits", "companion_permits")
        for row in (public.get(key) or [])
        if isinstance(row, dict)
    ]


def test_scopefacts_parser_extracts_segment_class_trades_and_specials():
    from api.scope_contract import build_scope_facts

    facts = build_scope_facts(
        "Commercial tenant improvement in a historic district with partitions, sink, new lighting, and fire alarm",
        "Miami",
        "FL",
        job_category="commercial",
    )

    assert facts.segment == "commercial"
    assert facts.construction_class == "TI"
    assert {"electrical", "plumbing_fog", "fire", "building_structural"}.issubset(facts.trade_signals)
    assert "historic" in facts.special_signals
    assert facts.dominant_family == "building"


def test_inv1_synthesized_governing_building_row_survives_and_leads_over_trade_rows():
    public = _public_from_model(
        {
            "permit_decision": "REQUIRED",
            "permits_required": [{"permit_type": "Electrical Permit", "filing_family": "electrical", "status": "REQUIRED"}],
            "sources": [{"url": "https://city.example.gov/permits"}],
        },
        "commercial tenant improvement with partition walls, electrical outlets, and sink",
        category="commercial",
    )

    rows = _visible_rows(public)
    assert public["permit_decision"] == "VERIFY"
    assert public["permit_required"] is None
    assert _families(rows)[0] == "building"
    assert {"building", "electrical", "plumbing"}.issubset(set(_families(rows)))
    assert rows[0].get("segment") == "commercial"
    assert all(row.get("status") in {"VERIFY", "CONDITIONAL"} for row in rows)


def test_standalone_primary_and_special_review_rules():
    grease = _public_from_model(
        {"permit_decision": "REQUIRED", "permits_required": [], "sources": [{"url": "https://city.example.gov/permits"}]},
        "replace grease interceptor only for restaurant",
        category="commercial",
    )
    assert _families(grease["permits_required"])[0] == "plumbing"
    assert "building" not in _families(grease["permits_required"])

    rtu = _public_from_model(
        {"permit_decision": "REQUIRED", "permits_required": [], "sources": [{"url": "https://city.example.gov/permits"}]},
        "replace RTU rooftop unit like for like on commercial building",
        category="commercial",
    )
    assert _families(rtu["permits_required"])[0] == "mechanical"

    historic = _public_from_model(
        {"permit_decision": "NOT_REQUIRED", "permits_required": [], "sources": [{"url": "https://city.example.gov/home/permits"}]},
        "paint storefront in historic district, no construction no electrical no plumbing",
        category="commercial",
    )
    assert historic["permit_decision"] == "VERIFY"
    assert historic["permit_required"] is None

    assert any(row.get("filing_family") == "historic" and row.get("status") in {"VERIFY", "CONDITIONAL"} for row in _visible_rows(historic))


def test_scope_to_trade_injection_preserves_source_backed_not_required_exemption():
    public = _public_from_model(
        {
            "permit_decision": "NOT_REQUIRED",
            "not_required_reason": "Official source-backed exemption: like-for-like sink fixture replacement with no rough-in is not required.",
            "exemption_evidence": True,
            "permits_required": [],
            "sources": [{"url": "https://city.example.gov/permit-exemptions"}],
        },
        "replace like-for-like sink fixture, no rough-in, no plumbing relocation",
        category="commercial",
    )

    assert public["permit_decision"] == "VERIFY"
    assert public["permit_required"] is None
    assert any(row.get("filing_family") == "plumbing" and row.get("status") in {"VERIFY", "CONDITIONAL"} for row in _visible_rows(public))


def test_hvac_equipment_install_not_demoted_by_generic_no_permit_note():
    public = _public_from_model(
        {
            "permit_decision": "NOT_REQUIRED",
            "not_required_reason": "official no-permit note preserved",
            "permits_required": [],
            "sources": [{"url": "https://www.houstonpermittingcenter.org"}],
        },
        "install mini split in detached garage home office, no plumbing and no structural work",
        city="Houston",
        state="TX",
        category="residential",
    )

    assert public["permit_decision"] == "VERIFY"
    assert public["permit_required"] is None
    assert "mechanical" in _families(_visible_rows(public))


def test_inv1_preserves_source_backed_construction_exemption_as_conditional_governing_review():
    public = _public_from_model(
        {
            "permit_decision": "NOT_REQUIRED",
            "not_required_reason": "Official source-backed no permit exemption for limited tenant finish if no walls, MEP, occupancy, or structural work changes.",
            "exemption_evidence": True,
            "permits_required": [],
            "sources": [{"url": "https://city.example.gov/exempt-work"}],
        },
        "commercial tenant improvement refresh with no walls no electrical no plumbing no occupancy change",
        category="commercial",
    )

    assert public["permit_decision"] == "VERIFY"
    assert public["permit_required"] is None
    assert any(
        row.get("filing_family") == "building"
        and row.get("status") in {"VERIFY", "CONDITIONAL"}
        for row in _visible_rows(public)
    )


def test_scopefacts_avoid_service_station_electrical_false_positive_and_hpwh_misrank():
    from api.scope_contract import build_scope_facts

    service_station = build_scope_facts("gas station service station canopy and fuel dispenser replacement", job_category="commercial")
    assert service_station.dominant_family == "building"
    assert "electrical" not in service_station.trade_signals
    assert {"hazardous", "environmental"}.issubset(service_station.special_signals)

    hpwh = build_scope_facts("replace gas water heater with heat pump water heater", job_category="residential")
    assert hpwh.dominant_family == "plumbing"

    home_office = build_scope_facts("install mini split in detached garage home office, no plumbing and no structural work", job_category="residential")
    assert home_office.segment == "residential"
    assert home_office.dominant_family == "mechanical"
    assert "mechanical_fuelgas" in home_office.trade_signals


def test_interior_door_safe_downgrade_does_not_match_exterior_entry_doors():
    interior = _public_from_model(
        {"permit_decision": "REQUIRED", "permits_required": [{"permit_type": "Building Permit", "filing_family": "building", "required": True, "status": "REQUIRED"}], "sources": [{"url": "https://city.example.gov/building"}]},
        "replace interior prehung door same size, no wall framing or header changes",
        category="residential",
    )
    exterior = _public_from_model(
        {"permit_decision": "REQUIRED", "permits_required": [{"permit_type": "Building Permit", "filing_family": "building", "required": True, "status": "REQUIRED"}], "sources": [{"url": "https://city.example.gov/building"}]},
        "replace exterior front door same size, no wall framing or header changes",
        category="residential",
    )

    assert interior["permit_decision"] == "VERIFY"
    assert interior["permit_required"] is None

    assert exterior["permit_decision"] == "VERIFY"
    assert exterior["permit_required"] is None
    assert any(row.get("filing_family") == "building" for row in _visible_rows(exterior))


def test_fuel_canopy_promotes_fire_and_environmental_reviews():
    public = _public_from_model(
        {"permit_decision": "REQUIRED", "permits_required": [], "sources": [{"url": "https://city.example.gov/fuel-permits"}]},
        "gas station service station canopy and fuel dispenser replacement",
        category="commercial",
    )
    families = set(_families(public["permits_required"]))
    assert {"building", "fire", "environmental"}.issubset(families)


def test_secret_redaction_keeps_token_classes_while_preserving_home_urls(tmp_path, monkeypatch):
    server = _server(tmp_path, monkeypatch)
    secret_blob = {
        "env": "PERMITASSIST_ADMIN_TOKEN and RAILWAY_TOKEN",
        "openai": "sk-" + "1234567890abcdef",
        "webhook": "whsec_" + "1234567890abcdef",
        "sha": "a" * 64,
        "url": "https://www.honolulu.gov/dpp/home/permits/index.html",
    }
    redacted = server.redact_public_output(secret_blob)
    serialized = json.dumps(redacted)
    assert "PERMITASSIST_ADMIN_TOKEN" not in serialized
    assert "RAILWAY_TOKEN" not in serialized
    assert secret_blob["openai"] not in serialized
    assert secret_blob["webhook"] not in serialized
    assert "a" * 64 not in serialized
    assert redacted["url"] == secret_blob["url"]


def test_resolver_candidate_selection_is_segment_constrained():
    from api.v231_decision_cells import classify_project_candidates

    assert classify_project_candidates("commercial tenant improvement with partitions", "residential") == []
    assert classify_project_candidates("residential kitchen remodel", "commercial") == []
    assert classify_project_candidates("commercial tenant improvement with partitions", "commercial") == ["commercial_tenant_improvement"]


def test_url_aware_leak_scanner_preserves_official_home_url_and_redacts_real_paths(tmp_path, monkeypatch):
    server = _server(tmp_path, monkeypatch)

    redacted = server.redact_public_output({
        "url": "https://www.honolulu.gov/dpp/home/permits/index.html",
        "path": "debug file /home/boban/projects/permitassist/private.json",
        "app": "runtime file /app/secrets/config.json",
    })

    assert redacted["url"] == "https://www.honolulu.gov/dpp/home/permits/index.html"
    assert "/home/boban" not in redacted["path"]
    assert "/app/secrets" not in redacted["app"]


def test_customer_api_share_report_projection_carries_canonical_rows_and_mirror_fields(tmp_path, monkeypatch):
    server = _server(tmp_path, monkeypatch)
    monkeypatch.setattr(
        server,
        "load_report_template",
        lambda: "<html><body><script>window.REPORT_DATA = __REPORT_DATA__;</script></body></html>",
    )
    raw = {
        "permit_decision": "REQUIRED",
        "permits_required": [{"permit_type": "Electrical Permit", "filing_family": "electrical", "status": "REQUIRED"}],
        "sources": [{"url": "https://city.example.gov/permits"}],
    }
    job = "commercial tenant improvement with partition walls, electrical outlets, and sink"

    public = server.build_customer_permit_view_model(copy.deepcopy(raw), job, "Miami", "FL", job_category="commercial")
    assert public["permit_decision"] == "VERIFY"
    assert public["permit_required"] is None
    rows = _visible_rows(public)
    assert rows[0]["filing_family"] == "building"
    assert rows[0]["segment"] == "commercial"

    handle = server.create_customer_snapshot_handoff(
        public,
        job,
        "Miami",
        "FL",
        job_category="commercial",
    )
    verified = server.consume_customer_snapshot_handoff(
        handle,
        public,
        job,
        "Miami",
        "FL",
        job_category="commercial",
    )
    assert verified is not None
    slug = server.create_share(job, "Miami", "FL", verified)
    share_json = server.get_share(slug)
    html = server.render_share_page(share_json)
    assert "permit_decision_contract" not in html
    assert "source_evidence_floor" not in html
    assert "\"segment\":\"commercial\"" in html
    assert "Commercial Building / Tenant Improvement Permit" in html


def test_live100_final_a_canary_replay_has_zero_three_sided_regressions(tmp_path, monkeypatch):
    server = _server(tmp_path, monkeypatch)
    final_a = {
        row["case_id"]
        for row in csv.DictReader((ARTIFACT_ROOT / "FINAL_TITI_OPUS_GRADES.csv").open())
        if row["final_grade"] == "A"
    }
    assert len(final_a) == 58
    cases = []
    for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines():
        record = json.loads(line)
        if record["case"]["id"] in final_a:
            cases.append(record)
    assert len(cases) == 58

    metrics = {
        "invalid_required_transition": [],
        "unsupported_binary_migrations": [],
        "fabricated_hard_requirements": [],
        "wrong_upgrades_of_source_backed_not_required": [],
    }
    for record in cases:
        case = record["case"]
        public = server.build_customer_permit_view_model(
            copy.deepcopy(record["response_body"]),
            case["job_type"],
            case["city"],
            case["state"],
            job_category=case.get("segment"),
        )
        decision = str(public.get("permit_decision") or "").upper()
        required_rows = [row for row in public.get("permits_required") or [] if isinstance(row, dict)]
        visible_rows = _visible_rows(public)
        expected = case.get("expected_decision_pre_registered")
        if expected == "REQUIRED" and (decision != "REQUIRED" or not required_rows):
            if decision == "VERIFY" and not required_rows and visible_rows:
                metrics["unsupported_binary_migrations"].append(case["id"])
            else:
                metrics["invalid_required_transition"].append(case["id"])
        if expected == "NOT_REQUIRED" and decision == "REQUIRED":
            metrics["fabricated_hard_requirements"].append(case["id"])
            metrics["wrong_upgrades_of_source_backed_not_required"].append(case["id"])

    assert metrics["invalid_required_transition"] == []
    assert metrics["fabricated_hard_requirements"] == []
    assert metrics["wrong_upgrades_of_source_backed_not_required"] == []
    assert metrics["unsupported_binary_migrations"]
