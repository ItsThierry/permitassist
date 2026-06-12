import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from permit_decision import _get_decision_cell_primary_lock, enforce_decision_cell_primary
from v231_decision_cells import reconcile_v231_result, resolve_v231_cell


def _base_required_result(name="Commercial Building / Tenant Improvement Permit"):
    return {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_name": name,
        "permits_required": [{"permit_type": name, "required": True}],
        "sources": [],
        "source_urls": [],
    }


def _public_from_reconciled(result, city, state):
    lock = _get_decision_cell_primary_lock(result)
    assert lock, "expected exact Decision Cell primary lock"
    public = {
        "permit_decision": result.get("permit_decision"),
        "permit_required": result.get("permit_required"),
        "permit_verdict": result.get("permit_verdict"),
        "permit_name": result.get("permit_name"),
        "permits_required": result.get("permits_required"),
        "sources": [],
        "source_urls": [],
        "_decision_cell_primary_lock": lock,
    }
    return enforce_decision_cell_primary(public, lock, city, state, public=True)


def _assert_no_internal_tokens(payload):
    text = json.dumps(payload, sort_keys=True)
    for token in [
        "permitassist_v231_decision_cell",
        "_v231_",
        "decision_cell_primary_lock",
        "IMPORTED_",
        "INTERNAL_SCOPE",
    ]:
        assert token not in text


def test_exact_publishable_decision_cell_preserves_public_source_urls_for_small_city_domain():
    resolution = resolve_v231_cell(
        "Fond Du Lac",
        "WI",
        "Interior commercial remodel for a tenant space with lighting and layout changes",
        "commercial",
    )
    result = _base_required_result()
    reconcile_v231_result(result, resolution)

    assert result["permit_decision"] == "REQUIRED"
    assert result["permit_name"] == "permit"
    assert result["source_urls"] == ["https://www.fdl.wi.gov/inspection-services/permits-fees/"]

    public = _public_from_reconciled(result, "Fond Du Lac", "WI")
    assert public["permit_decision"] == "REQUIRED"
    assert public["permit_name"] == "permit"
    assert public["source_urls"] == ["https://www.fdl.wi.gov/inspection-services/permits-fees/"]
    assert public["sources"][0]["url"] == "https://www.fdl.wi.gov/inspection-services/permits-fees/"
    _assert_no_internal_tokens(public)


def test_exact_publishable_ri_state_and_portal_sources_survive_public_serialization():
    resolution = resolve_v231_cell(
        "Burrillville",
        "RI",
        "Interior commercial remodel for a tenant space with lighting and layout changes",
        "commercial",
    )
    result = _base_required_result()
    reconcile_v231_result(result, resolution)
    public = _public_from_reconciled(result, "Burrillville", "RI")

    assert public["permit_decision"] == "REQUIRED"
    assert public["permit_name"] == "Building Permit"
    assert len(public["source_urls"]) >= 3
    assert "https://rhodeisland.portal.opengov.com/" in public["source_urls"]
    _assert_no_internal_tokens(public)


def test_exact_not_required_cell_beats_generic_commercial_required_default_but_keeps_sources():
    resolution = resolve_v231_cell(
        "Washington",
        "MN",
        "Office/retail tenant buildout with non-structural interior alteration",
        "commercial",
    )
    result = _base_required_result()
    reconcile_v231_result(result, resolution)
    public = _public_from_reconciled(result, "Washington", "MN")

    assert public["permit_decision"] == "NOT_REQUIRED"
    assert public["permit_required"] is False
    assert public["permit_verdict"] == "NO"
    assert public["source_urls"] == ["https://washingtoncountymn.gov/486/Permits"]
    _assert_no_internal_tokens(public)


def test_exact_not_required_cell_does_not_override_concrete_trade_safety_signal():
    resolution = resolve_v231_cell(
        "Washington",
        "MN",
        "Office/retail tenant buildout with non-structural interior alteration",
        "commercial",
    )
    result = _base_required_result("Electrical Permit")
    result["trade_permits"] = [{"permit_type": "Electrical Permit", "required": True}]
    reconcile_v231_result(result, resolution)

    assert result["permit_decision"] == "REQUIRED"
    assert _get_decision_cell_primary_lock(result) is None
    assert result.get("_v231_resolution_status") == "not_required_safety_conflict"


def test_ri_noncustomer_boundary_required_answer_gets_statewide_public_sources_without_exact_cell_promotion():
    from api.server import build_customer_permit_view_model

    job = "Interior commercial remodel for a tenant space with lighting and layout changes"
    for city in ["Exeter", "Little Compton"]:
        resolution = resolve_v231_cell(city, "RI", job, "commercial")
        result = _base_required_result()
        reconcile_v231_result(result, resolution)

        assert result["permit_decision"] == "REQUIRED"
        assert result["permit_required"] is True
        assert result["source_urls"] == [
            "https://webserver.rilegislature.gov/Statutes/TITLE23/23-27.3/23-2/23-27.3-113.1.htm",
            "https://webserver.rilegislature.gov/Statutes/TITLE23/23-27.3/23-2/23-27.3-115.6.htm",
            "https://rhodeisland.portal.opengov.com/",
        ]
        assert _get_decision_cell_primary_lock(result) is None
        public = build_customer_permit_view_model(result, job, city, "RI")
        assert public["source_urls"] == result["source_urls"]
        _assert_no_internal_tokens(public)


def test_finalizer_reconciles_stale_cached_required_result_to_exact_not_required_cell():
    from api.server import finalize_permit_lookup_result, build_customer_permit_view_model

    job = "Office/retail tenant buildout with non-structural interior alteration"
    cached = {
        "_cached": True,
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "permit_name": "Commercial Building / Tenant Improvement Permit",
        "permits_required": [{"permit_type": "Commercial Building / Tenant Improvement Permit", "kind": "Commercial Building / Tenant Improvement", "required": True}],
        "sources": [
            {"url": "https://www.washingtoncountymn.gov/DocumentCenter/View/26", "title": "Washington County commercial development guide"},
            {"url": "https://www.washingtoncountymn.gov/486/Permits", "title": "Permits | Washington County, MN"},
        ],
        "source_urls": ["https://www.washingtoncountymn.gov/DocumentCenter/View/26", "https://www.washingtoncountymn.gov/486/Permits"],
    }

    finalized = finalize_permit_lookup_result(cached, job, "Washington", "MN", is_cached=True, explicit_vertical="commercial_ti", evidence_allowed=False)
    public = build_customer_permit_view_model(finalized, job, "Washington", "MN")

    assert public["permit_decision"] == "NOT_REQUIRED"
    assert public["permit_required"] is False
    assert public["permit_verdict"] == "NO"
    assert public["permit_name"] == "No permit required"
    assert "https://washingtoncountymn.gov/486/Permits" in public["source_urls"]
    _assert_no_internal_tokens(public)
