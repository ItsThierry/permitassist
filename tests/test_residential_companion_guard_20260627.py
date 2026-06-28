import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from api.scope_contract import build_scope_contract
from api.server import _normalize_public_required_permit_package


def _row(name, kind=None):
    return {"permit_type": name, "kind": kind or name.split()[0], "required": True}


def _normalize(job, city, state, rows, fee="$70"):
    public = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_name": "Multiple permits required",
        "permit_kind": "Multiple permits",
        "permits_required": copy.deepcopy(rows),
        "fee_range": fee,
        "customer_next_step": "File the required permit before starting work.",
    }
    scope = build_scope_contract(job, city, state, job_category="residential")
    return _normalize_public_required_permit_package(public, job, city, state, scope)


def _families(out):
    return out.get("required_permit_families") or []


def _related_families(out):
    return [item.get("decision") for item in out.get("related_permits", [])]


def test_denver_ev_keeps_electrical_required_and_demotes_fire_zoning_co():
    out = _normalize(
        "install level 2 EV charger in attached garage at a single-family home",
        "Denver",
        "CO",
        [
            _row("Electrical Permit — EV Charger", "Electrical"),
            _row("Fire Department Review", "Fire"),
            _row("Planning / Zoning Review", "Planning/Zoning"),
            _row("Certificate of Occupancy", "Certificate of Occupancy"),
        ],
    )
    assert _families(out) == ["Electrical"]
    assert len(out.get("related_permits", [])) == 3
    assert set(_related_families(out)) == {"VERIFY"}


def test_la_same_size_windows_demotes_electrical_and_address_reviews():
    out = _normalize(
        "replace same-size windows in a single-family home",
        "Los Angeles",
        "CA",
        [
            _row("Residential Window / Building Permit", "Building"),
            _row("Electrical Permit", "Electrical"),
            _row("Historic Preservation Review", "Historic/Planning"),
        ],
    )
    assert _families(out) == ["Building"]
    assert {item.get("decision") for item in out.get("related_permits", [])} == {"CONDITIONAL", "VERIFY"}


def test_las_vegas_patio_adds_city_county_routing_caveat():
    out = _normalize(
        "build an attached covered patio on a single-family home",
        "Las Vegas",
        "NV",
        [_row("Residential Patio Cover Building Permit", "Building"), _row("Planning / Zoning Review", "Planning/Zoning")],
    )
    assert _families(out) == ["Building"]
    assert "City of Las Vegas or Clark County" in out.get("jurisdiction_routing_summary", "")


def test_portland_hpwh_keeps_plumbing_and_electrical_but_seattle_keeps_plumbing_only():
    rows = [_row("Plumbing Permit — Water Heater", "Plumbing"), _row("Electrical Permit", "Electrical"), _row("Mechanical Permit", "Mechanical")]
    portland = _normalize("install heat pump water heater in a single-family home", "Portland", "OR", rows)
    seattle = _normalize("install heat pump water heater in a single-family home", "Seattle", "WA", rows)
    assert _families(portland) == ["Electrical", "Plumbing"]
    assert _families(seattle) == ["Plumbing"]
    assert "additional trade" in seattle.get("fee_range", "")
