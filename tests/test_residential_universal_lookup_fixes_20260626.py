import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API) not in sys.path:
    sys.path.insert(0, str(API))


def _load_server(tmp_path, monkeypatch):
    """Import server with real engine modules, not the debug-header minimal stub."""
    monkeypatch.setenv("FREE_LOOKUP_DB", str(tmp_path / "ip_lookups.db"))
    monkeypatch.setenv("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    for name in ["api.server", "server"]:
        sys.modules.pop(name, None)
    sys.modules.pop("research_engine", None)
    from api import server

    server.CACHE_DB = str(tmp_path / "cache.db")
    server.DATA_DIR = str(tmp_path)
    server.init_db()
    return server


def _public(server, result, job, city, state):
    finalized = server.finalize_permit_lookup_result(
        result,
        job,
        city,
        state,
        evidence_allowed=False,
        job_category="residential",
    )
    public = server.build_customer_permit_view_model(
        finalized,
        job,
        city,
        state,
        job_category="residential",
    )
    assert not server.lint_customer_visible_result(public, city, state)
    return public


def _text(value) -> str:
    return json.dumps(value, sort_keys=True, default=str).lower()


def _permit_texts(public):
    return [json.dumps(p, sort_keys=True, default=str).lower() for p in (public.get("permits_required") or [])]


def _has_row(public, *terms):
    for row_text in _permit_texts(public):
        if all(term.lower() in row_text for term in terms):
            return True
    return False


def _forbid(text, *terms):
    for term in terms:
        assert term.lower() not in text, term


def test_sacramento_water_heater_required_plumbing_no_metadata_or_job_cost_fee_badge(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    job = "Sacramento CA residential like-for-like gas water heater replacement in same location"
    public = _public(server, {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_kind": "Plumbing",
        "permit_name": "Residential Plumbing Permit — Water Heater Replacement",
        "applying_office": "Sacramento Building Division",
        "fee_range": "$2,800 total job cost estimate",
        "confidence_reason": "permitassist_v231_decision_cell source metadata exact-local fallback",
        "permits_required": [{"permit_type": "Residential Plumbing Permit — Water Heater Replacement", "required": True}],
        "sources": [{"url": "https://www.cityofsacramento.gov/community-development/building/permits", "title": "Sacramento Building Permits"}],
        "checklist": ["Document water-heater model, location, gas/venting, seismic strapping if locally required, and inspection access."],
    }, job, "Sacramento", "CA")
    text = _text(public)

    assert public["permit_decision"] == "REQUIRED"
    assert _has_row(public, "plumbing", "water heater")
    assert "sacramento building division" in text
    assert "water heater" in text
    _forbid(text, "permitassist_v231", "decision_cell", "source metadata", "exact-local fallback")
    assert "total job cost" not in str(public.get("fee_range", "")).lower()
    assert "job cost" not in str(public.get("customer_result_summary", {}).get("fee_cost_caveat", "")).lower()


def test_denver_small_shed_exemption_first_no_untriggered_trade_or_safety_rows(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    job = "Denver CO residential 10x10 detached storage shed, 100 square feet, no foundation, no electrical, no plumbing, no heat"
    public = _public(server, {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_kind": "Building",
        "permit_name": "Building Permit",
        "permits_required": [
            {"permit_type": "Building Permit — Foundation Repair", "required": True},
            {"permit_type": "Electrical Permit", "required": True},
            {"permit_type": "Fire Department Review", "required": True},
            {"permit_type": "Certificate of Occupancy", "required": True},
        ],
        "sources": [{"url": "https://www.denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Community-Planning-and-Development", "title": "Denver CPD permits"}],
        "checklist": ["Submit engineered foundation drawings.", "Electrical load calculation.", "Fire review and CO paperwork."],
    }, job, "Denver", "CO")
    text = _text(public)

    assert public["permit_decision"] in {"NOT_REQUIRED", "EXEMPT"}
    assert public.get("permit_required") is False
    assert "shed" in text
    assert "zoning" in text and ("verify" in text or "conditional" in text)
    _forbid(text, "foundation repair", "electrical permit", "fire department", "fire review", "certificate of occupancy", "co paperwork")


def test_non_denver_shed_does_not_leak_denver_or_silently_claim_exemption(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    job = "Sacramento CA residential 12x12 detached storage shed, 144 square feet, no foundation, no electrical, no plumbing, no heat"
    public = _public(server, {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_kind": "Building",
        "permit_name": "Building Permit",
        "permits_required": [{"permit_type": "Building Permit — Accessory Structure", "required": True}],
        "sources": [{"url": "https://www.cityofsacramento.gov/community-development/building/permits", "title": "Sacramento building permits"}],
    }, job, "Sacramento", "CA")
    text = _text(public)

    assert "denver" not in text
    assert public.get("permit_decision") != "NOT_REQUIRED"
    assert public.get("permit_required") is not False
    assert "verify" in text or "conditional" in text or "building" in text


def test_shed_with_electrical_scope_is_not_exempted_and_keeps_trade_routing(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    job = "Denver CO residential 10x10 detached storage shed, 100 square feet, add one electrical circuit and light; no foundation, no plumbing, no heat"
    public = _public(server, {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_kind": "Building",
        "permit_name": "Building Permit",
        "permits_required": [{"permit_type": "Electrical Permit — Shed Circuit", "required": True}],
        "sources": [{"url": "https://www.denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Community-Planning-and-Development", "title": "Denver CPD permits"}],
    }, job, "Denver", "CO")
    text = _text(public)

    assert public.get("permit_decision") == "REQUIRED"
    assert public.get("permit_required") is True
    assert "electrical" in text
    assert "no building-permit filing path is needed" not in text


def test_minneapolis_basement_finish_trade_packet_and_dli_source_without_hvac_changeout_contamination(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    job = "Minneapolis MN residential basement finish with new bathroom, bedroom, outlets, lights, bath fan and ductwork; no HVAC equipment replacement"
    public = _public(server, {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_kind": "Building",
        "permit_name": "Residential Building Permit — Basement Finish",
        "permits_required": [
            {"permit_type": "Building Permit — Basement Finish", "required": True},
            {"permit_type": "Fire Department Review", "required": True},
            {"permit_type": "Planning Permit", "required": True},
            {"permit_type": "Certificate of Occupancy", "required": True},
        ],
        "sources": [
            {"url": "https://www2.minneapolismn.gov/business-services/planning-zoning/building-permits/", "title": "Minneapolis building permits"},
            {"url": "https://www.dli.mn.gov/business/electrical-contractors/electrical-permits-contractors", "title": "Minnesota DLI electrical permits"},
        ],
        "checklist": ["Refrigerant line-set pressure test.", "Thermostat location and nameplate data.", "Condensate drain routing.", "Bath fan duct termination."],
    }, job, "Minneapolis", "MN")
    text = _text(public)

    assert public["permit_decision"] == "REQUIRED"
    for family in ("building", "plumbing", "electrical", "mechanical"):
        assert _has_row(public, family), (family, public.get("permits_required"))
    assert "dli" in text and "electrical" in text
    assert "bath fan" in text or "ductwork" in text
    _forbid(text, "fire department review", "planning permit", "certificate of occupancy", "refrigerant", "thermostat", "nameplate", "condensate")


def test_non_minnesota_basement_finish_does_not_leak_dli_guidance(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    job = "Chicago IL residential basement finish with bathroom, outlets, lights, bath fan and ductwork; no HVAC equipment replacement"
    public = _public(server, {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_kind": "Building",
        "permit_name": "Residential Building Permit — Basement Finish",
        "permits_required": [{"permit_type": "Building Permit — Basement Finish", "required": True}],
        "sources": [{"url": "https://www.chicago.gov/city/en/depts/bldgs.html", "title": "Chicago building permits"}],
    }, job, "Chicago", "IL")
    text = _text(public)

    assert public["permit_decision"] == "REQUIRED"
    assert "minnesota dli" not in text
    assert "dli electrical" not in text
    assert _has_row(public, "electrical")


def test_source_backed_fire_companion_survives_as_verify_not_template_contamination(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    job = "Portland OR residential garage-to-ADU conversion with bathroom, kitchenette, electrical circuits, plumbing, bath fan, and heating/ventilation"
    public = _public(server, {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_kind": "Building",
        "permit_name": "Building Permit — ADU Conversion",
        "permits_required": [
            {"permit_type": "Building Permit — ADU Conversion", "required": True},
            {"permit_type": "Fire Department Review", "required": True, "source_url": "https://www.portland.gov/fire-marshal/permits", "notes": "Source-backed fire/life-safety review may apply to this ADU conversion."},
        ],
        "sources": [
            {"url": "https://www.portland.gov/ppd/adu", "title": "Portland ADU permits"},
            {"url": "https://www.portland.gov/fire-marshal/permits", "title": "Portland Fire Marshal permits"},
        ],
    }, job, "Portland", "OR")
    text = _text(public)

    assert "fire department review" in text or "fire/life-safety" in text
    assert "portland.gov/fire-marshal" in text


def test_panel_missing_apply_path_contract_next_step_is_not_clobbered(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    job = "Cozad NE residential electrical service/panel upgrade to 200A; no plumbing, no HVAC, no building remodel"
    public = _public(server, {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_kind": "Electrical",
        "permit_name": "Electrical Permit — Service Upgrade",
        "permits_required": [{"permit_type": "Electrical Permit — Service Upgrade", "required": True}],
        "applying_office": "City of Cozad Building Department",
        "apply_path": {"state": "CONTACT_AHJ", "channel": "contact_ahj", "portal_url": None},
        "customer_next_step": "No verified online filing URL is attached; contact the AHJ before applying.",
        "sources": [{"url": "https://www.cityofcozad.com/", "title": "City of Cozad building department"}],
    }, job, "Cozad", "NE")
    next_step = str(public.get("customer_next_step") or "").lower()

    assert "no verified online filing url" in next_step or "no exact local filing portal" in next_step
    assert "utility" in next_step or "grounding" in next_step or "panel" in next_step
    assert next_step.count("coordinate utility meter release") == 1


def test_missing_apply_path_contract_next_step_survives_adu_basement_and_shed(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    cases = [
        (
            "Portland OR residential garage-to-ADU conversion with bathroom, kitchenette, electrical circuits, plumbing, bath fan, and heating/ventilation",
            "Portland",
            "OR",
            {"permit_name": "Building Permit — ADU Conversion", "permits_required": [{"permit_type": "Building Permit — ADU Conversion", "required": True}], "sources": []},
            "adu filing packet",
        ),
        (
            "Chicago IL residential basement finish with bathroom, outlets, lights, bath fan and ductwork; no HVAC equipment replacement",
            "Chicago",
            "IL",
            {"permit_name": "Residential Building Permit — Basement Finish", "permits_required": [{"permit_type": "Building Permit — Basement Finish", "required": True}], "sources": []},
            "basement-finish building packet",
        ),
        (
            "Sacramento CA residential 12x12 detached storage shed, 144 square feet, no foundation, no electrical, no plumbing, no heat",
            "Sacramento",
            "CA",
            {"permit_name": "Building Permit", "permits_required": [{"permit_type": "Building Permit — Accessory Structure", "required": True}], "sources": []},
            "shed thresholds",
        ),
    ]
    for job, city, state, result, guidance_term in cases:
        result.update({
            "permit_decision": "REQUIRED",
            "permit_required": True,
            "permit_verdict": "YES",
            "apply_path": {"state": "CONTACT_AHJ", "channel": "contact_ahj", "portal_url": None},
            "customer_next_step": "No verified online filing URL is attached; contact the AHJ before applying.",
        })
        public = _public(server, result, job, city, state)
        next_step = str(public.get("customer_next_step") or "").lower()
        assert "no verified online filing url" in next_step or "no exact local filing portal" in next_step
        assert guidance_term in next_step


def test_portland_garage_to_adu_primary_packet_no_pool_spa_template_content(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    job = "Portland OR residential garage-to-ADU conversion with bathroom, kitchenette, electrical circuits, plumbing, bath fan, and heating/ventilation"
    public = _public(server, {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_kind": "Building",
        "permit_name": "Building Permit — ADU Conversion",
        "permits_required": [{"permit_type": "Building Permit — ADU Conversion", "required": True}],
        "sources": [{"url": "https://www.portland.gov/ppd/adu", "title": "Portland ADU permits"}],
        "checklist": ["Pool/spa gunite inspection.", "Barrier fencing plan.", "Trenching layout for pool equipment."],
    }, job, "Portland", "OR")
    text = _text(public)

    for family in ("adu", "building", "electrical", "plumbing", "mechanical"):
        assert family in text, family
    _forbid(text, "pool", "spa", "gunite", "barrier fencing", "trenching")


def test_houston_panel_upgrade_electrical_packet_preserves_utility_guidance_and_obeys_negated_trades(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    job = "Houston TX residential electrical service/panel upgrade to 200A; no plumbing, no HVAC, no building remodel"
    public = _public(server, {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_kind": "Electrical",
        "permit_name": "Electrical Permit — Service Upgrade",
        "permits_required": [
            {"permit_type": "Electrical Permit — Service Upgrade", "required": True},
            {"permit_type": "Plumbing Permit", "required": True},
        ],
        "applying_office": "Houston Permitting Center",
        "sources": [{"url": "https://www.houstonpermittingcenter.org/hpwcode1002", "title": "Houston Permitting Center electrical permits"}],
        "checklist": ["HVAC condenser nameplate and refrigerant calculations.", "Pool/spa bonding and barrier fencing.", "Coordinate utility meter release, grounding electrode system, panel schedule, and load calculation."],
    }, job, "Houston", "TX")
    text = _text(public)

    assert public["permit_decision"] == "REQUIRED"
    assert _has_row(public, "electrical")
    assert "houston permitting center" in text
    assert "utility" in text and "grounding" in text and "panel" in text
    _forbid(text, "plumbing permit", "hvac", "refrigerant", "pool", "spa", "barrier fencing")
