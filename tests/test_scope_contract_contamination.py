import copy
import json
import sys
from importlib import util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_DIR = REPO_ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))


def _load_server_helpers():
    helper_spec = util.spec_from_file_location(
        "debug_headers_helper_scope_contract",
        Path(__file__).with_name("test_debug_headers_endpoint.py"),
    )
    helper = util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)
    return helper._import_server


def _customer_text(value) -> str:
    return json.dumps(value, sort_keys=True).lower()


def test_scope_contract_classifies_panel_upgrade_as_residential_single_trade():
    from scope_contract import build_scope_contract

    contract = build_scope_contract(
        "Residential single-family home electrical panel upgrade to 200 amp service; no solar or PV work",
        "Tarrant County",
        "TX",
        job_category="residential",
    )

    assert contract["category"] == "residential"
    assert contract["family"] == "residential_single_trade"
    assert contract["vertical"] == "panel_upgrade"
    assert contract["occupancy_class"] == "single_family"
    assert "commercial_ti" in contract["forbidden_scope_tags"]
    assert "residential_solar" in contract["forbidden_scope_tags"]


def test_scope_contract_overrides_implicit_residential_default_for_clear_commercial_ti():
    from scope_contract import build_scope_contract, sanitize_result_for_scope_contract

    job = "Commercial restaurant tenant improvement with Type I hood and grease interceptor"
    contract = build_scope_contract(job, "Phoenix", "AZ", job_category="residential")
    alteration_contract = build_scope_contract(
        "Commercial alteration for existing office suite with lighting and HVAC changes",
        "Dallas",
        "TX",
        job_category="residential",
    )

    assert contract["category"] == "commercial"
    assert contract["family"] == "commercial_ti"
    assert contract["vertical"] == "restaurant_ti"
    assert alteration_contract["category"] == "commercial"
    assert alteration_contract["family"] == "commercial_ti"
    assert "restaurant_ti" not in contract["forbidden_scope_tags"]
    assert "commercial_ti" not in contract["forbidden_scope_tags"]

    result = sanitize_result_for_scope_contract({
        "permit_name": "Commercial Tenant Improvement Building Permit — Restaurant TI",
        "permits_required": [
            {"permit_type": "Commercial Tenant Improvement Building Permit — Restaurant TI"},
            {"permit_type": "Mechanical Permit — Type I Hood"},
        ],
    }, contract, fail_on_removal_in_tests=False)

    assert result["permit_name"].startswith("Commercial Tenant Improvement")
    assert len(result["permits_required"]) == 2
    assert "_scope_firebreak_removed" not in result


def test_scope_contract_commercial_conversion_beats_hvac_single_trade():
    from scope_contract import build_scope_contract

    contract = build_scope_contract(
        "4,000 sq ft retail converted into a fitness studio with restrooms, showers, HVAC rooftop units and electrical lighting",
        "Austin",
        "TX",
        job_category="commercial",
    )

    assert contract["category"] == "commercial"
    assert contract["family"] == "commercial_ti"
    assert contract["vertical"] == "commercial_ti"


def test_scope_contract_simple_commercial_hvac_stays_single_trade():
    from scope_contract import build_scope_contract

    contract = build_scope_contract(
        "commercial rooftop HVAC unit like-for-like replacement only",
        "Austin",
        "TX",
        job_category="commercial",
    )

    assert contract["category"] == "commercial"
    assert contract["family"] == "commercial_other"
    assert contract["vertical"] == "hvac_changeout"


def test_batch_and_v1_permit_pass_request_job_category_to_research_permit():
    server_source = (API_DIR / "server.py").read_text()
    batch_block = server_source[server_source.index('elif path == "/api/batch-permit":'):server_source.index('elif path == "/api/beta-event":')]
    v1_block = server_source[server_source.index('elif path == "/api/v1/permit":'):server_source.index('else:', server_source.index('elif path == "/api/v1/permit":'))]

    assert 'job_category = item.get("job_category", "")' in batch_block
    assert "job_category=job_category" in batch_block
    assert 'job_category = (data.get("job_category") or "").strip()' in v1_block
    assert "job_category=job_category" in v1_block


def test_pure_panel_upgrade_classification_never_becomes_solar_pv():
    import research_engine

    classified = research_engine.classify_scope_required_permits(
        "Residential single-family home electrical panel upgrade to 200 amp service; no solar or PV work"
    )

    assert classified is not None
    assert classified["scope_classification"] == "residential_panel_upgrade"
    text = _customer_text(classified)
    assert "electrical permit" in text
    assert "panel" in text or "service upgrade" in text
    assert "solar" not in text
    assert "photovoltaic" not in text
    assert "tenant improvement" not in text


def test_state_expert_notes_filter_commercial_restaurant_ti_residential_leaks():
    from scope_contract import build_scope_contract
    from state_packs import get_state_expert_notes

    contract = build_scope_contract(
        "Commercial restaurant tenant improvement in Dallas: dining room remodel, Type I hood, grease interceptor",
        "Dallas",
        "TX",
        job_category="commercial",
    )
    notes = get_state_expert_notes("TX", "Dallas", "Commercial restaurant tenant improvement", scope_contract=contract)
    text = _customer_text(notes)

    assert notes
    assert "homeowner" not in text
    assert "owner-occupied" not in text
    assert "adu" not in text
    assert "solar pv" not in text
    assert "residential solar" not in text
    assert "twia" not in text


def test_finalize_panel_upgrade_repairs_stale_solar_and_keeps_residential_apply_path(tmp_path, monkeypatch):
    _import_server = _load_server_helpers()
    server = _import_server(tmp_path, monkeypatch)
    stale_result = {
        "permit_verdict": "YES",
        "confidence": "medium",
        "permits_required": [{"permit_type": "Building Permit — Solar PV (Structural Racking & Roof Penetrations)", "required": True}],
        "apply_url": "https://www.tarrantcountytx.gov/en/development-services/permits.html",
        "sources": ["https://www.tarrantcountytx.gov/en/development-services/permits.html"],
        "checklist": ["Solar PV plans"],
        "approval_timeline": "2-4 weeks",
    }

    result = server.finalize_permit_lookup_result(
        copy.deepcopy(stale_result),
        "Residential single-family home electrical panel upgrade to 200 amp service; no solar or PV work",
        "Tarrant County",
        "TX",
        evidence_allowed=False,
    )

    text = _customer_text(result)
    assert result["_scope_contract"]["vertical"] == "panel_upgrade"
    assert result["apply_path"]["permit_category"] == "Residential / Trade Permit"
    assert "electrical permit" in text
    assert "panel" in text or "service upgrade" in text
    assert "solar pv" not in text
    assert "structural racking" not in text
    assert "tenant improvement" not in text


def test_finalize_commercial_ti_removes_even_exclusionary_residential_scope_leaks(tmp_path, monkeypatch):
    _import_server = _load_server_helpers()
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(sys.modules["research_engine"], "classify_source_tier", lambda *_a, **_k: "official")
    monkeypatch.setattr(server, "classify_scope_required_permits", lambda *_a, **_k: None)
    contaminated_result = {
        "permit_verdict": "YES",
        "confidence": "high",
        "permit_name": "Commercial Tenant Improvement Building Permit — Restaurant TI",
        "permits_required": [
            {"permit_type": "Commercial Tenant Improvement Building Permit — Restaurant TI", "required": True},
            {"permit_type": "Mechanical Permit — Type I Hood", "required": True},
        ],
        "checklist": [
            "Submit commercial TI plans and hood drawings.",
            "Do not use homeowner ADU solar PV forms for this commercial job.",
        ],
        "what_to_bring": [
            "Commercial tenant improvement plan set.",
            "Homeowner affidavit is not the path for this commercial job.",
        ],
        "pro_tips": [
            "Use the commercial tenant improvement application path; ignore residential ADU solar forms.",
            "Ask the AHJ if residential owner-builder paperwork applies.",
        ],
        "sources": ["https://www.phoenix.gov/pdd/development/permits"],
    }

    result = server.finalize_permit_lookup_result(
        copy.deepcopy(contaminated_result),
        "Commercial restaurant tenant improvement with Type I hood and grease interceptor",
        "Phoenix",
        "AZ",
        explicit_vertical="restaurant_ti",
        evidence_allowed=False,
    )

    text = _customer_text(server.sanitize_customer_visible_result(result))
    assert "commercial tenant improvement" in text
    assert "mechanical permit" in text
    assert "homeowner" not in text
    assert "residential" not in text
    assert "adu" not in text
    assert "solar" not in text
    assert "photovoltaic" not in text


def test_server_error_scanner_does_not_flag_florida_statute_553_502():
    from scope_contract import customer_text_has_server_error_signal

    assert customer_text_has_server_error_signal(
        "Florida Statute 553.502 accessibility requirements may apply to commercial alterations."
    ) is False
    assert customer_text_has_server_error_signal("upstream server_error from permit lookup") is True
    assert customer_text_has_server_error_signal("HTTP 502 Bad Gateway from backend") is True


def test_commercial_ny_state_pack_filters_residential_homeowner_notes():
    from scope_contract import build_scope_contract
    from state_packs import get_state_expert_notes

    contract = build_scope_contract(
        "Commercial office tenant improvement with partitions, lighting, and restroom accessibility updates",
        "Rochester",
        "NY",
        job_category="commercial",
    )

    notes = get_state_expert_notes("NY", "Rochester", "Commercial office tenant improvement", scope_contract=contract)
    text = _customer_text(notes)

    assert notes
    assert "homeowner" not in text
    assert "homeowners" not in text
    assert "home-improvement" not in text
    assert "home improvement" not in text
    assert "residential" not in text


def test_customer_sanitizer_remaps_raw_ahj_source_key_and_text(tmp_path, monkeypatch):
    _import_server = _load_server_helpers()
    server = _import_server(tmp_path, monkeypatch)

    cleaned = server.sanitize_customer_visible_result({
        "permit_verdict": "YES",
        "permit_name": "Commercial Tenant Improvement Building Permit",
        "ahj_contact_source": {
            "title": "Official AHJ portal",
            "url": "https://example.gov/building",
            "source_type": "official",
        },
    })
    text = _customer_text(cleaned)

    assert "ahj_contact_source" not in cleaned
    assert "building_department_contact_source" in cleaned
    assert "ahj" not in text
    assert "building department" in text


def test_checklist_endpoint_output_is_customer_sanitized_before_cache(tmp_path, monkeypatch):
    _import_server = _load_server_helpers()
    server = _import_server(tmp_path, monkeypatch)

    monkeypatch.setattr(server, "generate_checklist", lambda *_args, **_kwargs: {
        "title": "Pre-Construction Compliance Checklist",
        "summary": "Checklist for customer",
        "items": [
            {"label": "Provide occupancy basis and verify classification with the AHJ", "category": "code review", "required": True},
        ],
    })

    result = {"permit_name": "Commercial Tenant Improvement Building Permit", "permit_verdict": "YES"}
    first = server.get_or_create_checklist(result, "Commercial office tenant improvement", "Rochester", "NY")
    second = server.get_or_create_checklist(result, "Commercial office tenant improvement", "Rochester", "NY")

    assert first["cached"] is False
    assert second["cached"] is True
    assert "ahj" not in _customer_text(first)
    assert "ahj" not in _customer_text(second)
    assert "building department" in _customer_text(first)


def test_cached_checklist_uses_request_scope_firebreak_for_customer_output(tmp_path, monkeypatch):
    import sqlite3

    _import_server = _load_server_helpers()
    server = _import_server(tmp_path, monkeypatch)

    result = {"permit_name": "Commercial Tenant Improvement Building Permit", "permit_verdict": "YES"}
    result_hash = server.make_result_hash(result)
    dirty_checklist = {
        "title": "AHJ checklist",
        "summary": "Bring this to the AHJ with ahj_contact_source metadata.",
        "items": [
            {"label": "AHJ intake", "detail": "Ask AHJ about homeowner ADU solar PV forms."},
            {"label": "Commercial plans", "detail": "Submit tenant improvement drawings."},
        ],
        "ahj_contact_source": "AHJ checklist source",
    }
    with sqlite3.connect(server.CACHE_DB) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO checklist_cache (result_hash, checklist_json, created_at) VALUES (?,?,?)",
            (result_hash, json.dumps(dirty_checklist), "2026-05-26T00:00:00+00:00"),
        )
        conn.commit()

    cleaned = server.get_or_create_checklist(result, "Commercial office tenant improvement", "Rochester", "NY")
    text = _customer_text(cleaned)

    assert cleaned["cached"] is True
    assert "ahj_contact_source" not in text
    assert "ahj" not in text
    assert "building_department_contact_source" in cleaned
    assert "homeowner" not in text
    assert "adu" not in text
    assert "solar" not in text
    assert "residential" not in text
    assert "tenant improvement" in text
