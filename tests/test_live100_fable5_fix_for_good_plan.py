from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from family_reconciliation_gate import family_from_row
from server import build_customer_permit_view_model, render_share_page

ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_customer_pov_after_3fdb563_20260703T133901Z"
C_CASE_IDS = ["R-011", "R-013", "C-018", "C-021", "C-024"]


def _records() -> list[dict[str, Any]]:
    return [json.loads(line) for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines() if line.strip()]


def _record(case_id: str) -> dict[str, Any]:
    for rec in _records():
        if rec.get("case", {}).get("id") == case_id:
            return rec
    raise AssertionError(f"missing case {case_id}")


def _load(case_id: str) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]:
    rec = _record(case_id)
    case = rec["case"]
    old_gate = os.environ.get("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD")
    os.environ["PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD"] = "1"
    try:
        public = build_customer_permit_view_model(
            rec.get("response_body") or {},
            case["job_type"],
            case["city"],
            case["state"],
            job_category=case.get("segment"),
        )
        html_text = render_share_page({"data": public, "job_type": case["job_type"], "city": case["city"], "state": case["state"]})
    finally:
        if old_gate is None:
            os.environ.pop("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD", None)
        else:
            os.environ["PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD"] = old_gate
    return case, public, html_text, rec.get("response_body") or {}


def _report_payload(html_text: str) -> dict[str, Any]:
    match = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html_text, flags=re.S)
    assert match, "report-data payload missing"
    return json.loads(html.unescape(match.group(1)))


def _rows(public: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in public.get("permits_required") or [] if isinstance(r, dict)]


def _families(public: dict[str, Any]) -> set[str]:
    packet = public.get("public_packet") if isinstance(public.get("public_packet"), dict) else {}
    packet_rows = [r for r in packet.get("rows") or [] if isinstance(r, dict) and str(r.get("decision") or "").upper() == "REQUIRED"]
    if packet_rows:
        return {str(r.get("family") or family_from_row(r)) for r in packet_rows}
    return {family_from_row(r) for r in _rows(public)}


def _packet(public: dict[str, Any]) -> dict[str, Any]:
    packet = public.get("public_packet")
    assert isinstance(packet, dict), "missing public_packet"
    return packet


def _packet_hash(packet: dict[str, Any]) -> str:
    clone = {k: v for k, v in packet.items() if k not in {"sealed_public_packet_hash", "sealed_at_stage", "render_seal_hash"}}
    digest = hashlib.sha256(json.dumps(clone, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return f"sha256:{digest}"


def _all_visible_text(public: dict[str, Any], html_text: str = "") -> str:
    return (json.dumps(public, sort_keys=True, default=str) + "\n" + html_text).lower()


# Universal invariant tests required by the closeout handoff.

def test_seal_not_required_has_no_permit_artifacts():
    _case, public, html_text, _raw = _load("R-013")
    payload_data = _report_payload(html_text)["share"]["data"]
    for surface in (public, payload_data):
        assert surface.get("permit_decision") == "NOT_REQUIRED"
        assert surface.get("permit_required") is False
        assert not surface.get("permits_required")
        assert not surface.get("conditional_permits")
        assert not surface.get("documents_to_prepare")
        assert not surface.get("what_to_bring")
        assert not surface.get("requirements")
        assert not surface.get("inspections")
        assert not surface.get("apply_url")
        assert (surface.get("apply_path") or {}).get("status") == "NOT_APPLICABLE"
    forbidden = r"\b(?:pull|file|submit|apply\s+for|pay)\b(?![^.]{0,35}\b(?:not|no|do not|don't|does not)\b)[^.]{0,80}\bpermit\b"
    assert not re.search(forbidden, _all_visible_text(payload_data), flags=re.I)


@pytest.mark.parametrize("case_id", C_CASE_IDS)
def test_all_surfaces_render_from_sealed_dto(case_id: str):
    _case, public, html_text, _raw = _load(case_id)
    packet = _packet(public)
    assert public.get("sealed_schema") == "final_public_permit_packet.v1"
    assert public.get("sealed_public_packet_hash") == _packet_hash(packet)
    assert packet.get("sealed_public_packet_hash") == public.get("sealed_public_packet_hash")
    payload_data = _report_payload(html_text)["share"]["data"]
    assert payload_data.get("sealed_public_packet_hash") == public.get("sealed_public_packet_hash")
    assert (payload_data.get("public_packet") or {}).get("sealed_public_packet_hash") == public.get("sealed_public_packet_hash")
    assert payload_data.get("permits_required") == public.get("permits_required")


@pytest.mark.parametrize("case_id", C_CASE_IDS)
def test_no_orphan_docs_or_inspections(case_id: str):
    _case, public, _html_text, _raw = _load(case_id)
    packet_rows = [r for r in _packet(public).get("rows") or [] if isinstance(r, dict)]
    families = {str(r.get("family") or "") for r in packet_rows}
    assert families or public.get("permit_decision") == "NOT_REQUIRED"
    for row in packet_rows:
        for item in list(row.get("documents") or []) + list(row.get("inspections") or []):
            text = str(item).lower()
            if "gas" in text:
                assert families & {"gas", "plumbing"}, (case_id, item, families)
            if "noa" in text or "product approval" in text or "hvhz" in text:
                assert families & {"building", "building_ti"}, (case_id, item, families)


@pytest.mark.parametrize("case_id", C_CASE_IDS)
def test_no_duplicate_canonical_family_rows(case_id: str):
    _case, public, _html_text, _raw = _load(case_id)
    rows = [r for r in _packet(public).get("rows") or [] if isinstance(r, dict) and r.get("decision") in {"REQUIRED", "CONDITIONAL"}]
    families = [str(r.get("family") or "") for r in rows]
    assert len(families) == len(set(families)), (case_id, families, rows)


@pytest.mark.parametrize("case_id", ["C-018", "C-021", "C-024"])
def test_segment_lock_row_names(case_id: str):
    _case, public, _html_text, _raw = _load(case_id)
    assert public.get("segment") == "commercial"
    names = "\n".join(str(r.get("permit_name") or r.get("permit_type") or "") for r in _rows(public)).lower()
    assert "residential" not in names
    assert not re.search(r"\bhomeowner\b|\bsingle[- ]family\b", names)


def test_resolved_ahj_source_coherence():
    for case_id in ("R-011", "C-021"):
        _case, public, _html_text, _raw = _load(case_id)
        resolution = public.get("ahj_resolution") or {}
        assert resolution.get("resolved_ahj_key") == "miami_fl_city", (case_id, resolution)
        assert "city of miami" in (resolution.get("resolved_ahj_name") or "").lower()
        assert "miami.gov" in str(public.get("apply_url") or "").lower()
        assert "ecobuilt" not in str(public.get("apply_url") or "").lower()
        assert "miamidade.gov/buildingpermit" not in str(public.get("apply_url") or "").lower()
        visible = _all_visible_text(public)
        assert "procurement" not in visible and "solicitationdetails" not in visible
        for url in public.get("source_urls") or []:
            if "miamidade.gov" in str(url).lower():
                assert re.search(r"hvhz|noa|product|windows|shutters|doors", str(url), flags=re.I), (case_id, url)


@pytest.mark.parametrize("case_id", C_CASE_IDS)
def test_scope_floor_monotonicity(case_id: str):
    _case, public, _html_text, _raw = _load(case_id)
    fams = _families(public)
    if case_id == "C-018":
        assert {"building_ti", "fire_suppression", "planning_zoning", "co_change_of_occupancy"} <= fams
    if case_id == "C-021":
        assert {"building_ti", "electrical"} <= fams
        assert re.search(r"NOA|product approval|HVHZ", json.dumps(public.get("documents_to_prepare") or []), flags=re.I)
    if case_id == "C-024":
        assert {"building_ti", "gas", "mechanical", "health_food", "fire_alarm"} <= fams


def test_no_decision_flips_from_customer_boundary_gates():
    old_gate = os.environ.get("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD")
    os.environ["PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD"] = "1"
    try:
        for rec in _records():
            case = rec["case"]
            raw = rec.get("response_body") or {}
            before = str(raw.get("permit_decision") or ("REQUIRED" if raw.get("permit_required") else "")).upper()
            if before not in {"REQUIRED", "NOT_REQUIRED"}:
                continue
            public = build_customer_permit_view_model(raw, case["job_type"], case["city"], case["state"], job_category=case.get("segment"))
            assert public.get("permit_decision") == before, case["id"]
    finally:
        if old_gate is None:
            os.environ.pop("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD", None)
        else:
            os.environ["PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD"] = old_gate


@pytest.mark.parametrize("case_id", ["R-011", "C-018", "C-021", "C-024"])
def test_required_packets_have_fee_or_official_fee_schedule_pointer(case_id: str):
    _case, public, _html_text, _raw = _load(case_id)
    assert public.get("permit_decision") == "REQUIRED"
    blob = _all_visible_text(public)
    assert public.get("fee_range") or "fee schedule" in blob or "fee" in blob
    if case_id in {"R-011", "C-021"}:
        assert "city of miami" in blob and "fee schedule" in blob
        assert "miami-dade residential building permit base" not in str(public.get("fee_range") or "").lower()


# Focused five C-case sentinels.

def test_live100_r011_miami_windows_city_ahj_hvhz_noa():
    _case, public, _html_text, _raw = _load("R-011")
    assert public.get("permit_decision") == "REQUIRED"
    assert (public.get("ahj_resolution") or {}).get("resolved_ahj_key") == "miami_fl_city"
    assert "city of miami" in str(public.get("applying_office") or "").lower()
    assert "miami.gov" in str(public.get("apply_url") or "").lower()
    assert re.search(r"NOA|product approval|HVHZ", json.dumps(public.get("documents_to_prepare") or []), flags=re.I)


def test_live100_r013_chicago_drywall_not_required_render_parity():
    _case, public, html_text, _raw = _load("R-013")
    payload_data = _report_payload(html_text)["share"]["data"]
    assert public.get("permit_decision") == payload_data.get("permit_decision") == "NOT_REQUIRED"
    assert public.get("permit_required") is payload_data.get("permit_required") is False
    assert not public.get("permits_required") and not payload_data.get("permits_required")
    assert "building permit" not in json.dumps(payload_data.get("public_packet") or {}).lower()


def test_live100_c018_portland_commercial_change_of_use_segment_lock():
    _case, public, _html_text, _raw = _load("C-018")
    names = [str(r.get("permit_name") or r.get("permit_type") or "") for r in _rows(public)]
    assert any("Commercial Building" in name and ("Change" in name or "Tenant Improvement" in name) for name in names), names
    assert not any("Residential" in name for name in names), names
    assert {"building_ti", "fire_suppression", "planning_zoning", "co_change_of_occupancy"} <= _families(public)


def test_live100_c021_miami_storefront_city_path_hvhz_noa():
    _case, public, _html_text, _raw = _load("C-021")
    assert public.get("permit_decision") == "REQUIRED"
    assert (public.get("ahj_resolution") or {}).get("resolved_ahj_key") == "miami_fl_city"
    assert "miami.gov" in str(public.get("apply_url") or "").lower()
    assert "ecobuilt" not in str(public.get("apply_url") or "").lower()
    assert "solicitationdetails" not in _all_visible_text(public)
    assert re.search(r"NOA|product approval|HVHZ", json.dumps(public.get("documents_to_prepare") or []), flags=re.I)


def test_live100_c024_nyc_restaurant_gas_family_closure():
    _case, public, _html_text, _raw = _load("C-024")
    fams = _families(public)
    assert "gas" in fams, (fams, public.get("permits_required"))
    assert "building_ti" in fams
    packet_rows = [r for r in _packet(public).get("rows") or [] if isinstance(r, dict) and str(r.get("decision") or "").upper() == "REQUIRED"]
    names_by_family = {str(r.get("family") or family_from_row(r)): str(r.get("permit_name") or r.get("permit_type") or "") for r in packet_rows}
    assert "gas" in names_by_family["gas"].lower(), names_by_family
    assert "tenant improvement" in names_by_family["building_ti"].lower() or "alteration" in names_by_family["building_ti"].lower()
    docs_and_inspections = json.dumps({"docs": public.get("documents_to_prepare"), "inspections": public.get("inspections")}, default=str).lower()
    assert "gas" in docs_and_inspections and ("pressure" in docs_and_inspections or "piping" in docs_and_inspections)
