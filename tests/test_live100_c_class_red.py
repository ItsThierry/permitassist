from __future__ import annotations

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
ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_customer_pov_after_6b8b1f1_20260703T191058Z"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from server import build_customer_permit_view_model, render_share_page

C_CASE_IDS = ["C-018", "C-035", "C-037", "R-013", "R-033", "R-049"]


def _records() -> list[dict[str, Any]]:
    return [json.loads(line) for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines() if line.strip()]


def _record(case_id: str) -> dict[str, Any]:
    for rec in _records():
        if rec.get("case", {}).get("id") == case_id:
            return rec
    raise AssertionError(f"missing Live100 case {case_id}")


def _load(case_id: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    rec = _record(case_id)
    case = rec["case"]
    old = os.environ.get("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD")
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
        if old is None:
            os.environ.pop("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD", None)
        else:
            os.environ["PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD"] = old
    return case, public, html_text


def _blob(public: dict[str, Any], html_text: str) -> str:
    rendered = re.sub(r"<script\b.*?</script>", " ", html.unescape(html_text), flags=re.I | re.S)
    return (json.dumps(public, sort_keys=True, default=str) + "\n" + rendered).lower()


def _packet(public: dict[str, Any]) -> dict[str, Any]:
    packet = public.get("public_packet")
    assert isinstance(packet, dict), "missing sealed public_packet"
    return packet


def _packet_rows(public: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in (_packet(public).get("rows") or []) if isinstance(r, dict)]


def _required_packet_rows(public: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in _packet_rows(public) if str(r.get("decision") or "").upper() == "REQUIRED"]


def _conditional_packet_rows(public: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in _packet_rows(public) if str(r.get("decision") or "").upper() == "CONDITIONAL"]


def _required_families(public: dict[str, Any]) -> set[str]:
    return {str(r.get("family") or "") for r in _required_packet_rows(public)}


def _conditional_families(public: dict[str, Any]) -> set[str]:
    return {str(r.get("family") or "") for r in _conditional_packet_rows(public)}


def _names(public: dict[str, Any]) -> list[str]:
    return [str(r.get("permit_name") or r.get("permit_type") or "") for r in _required_packet_rows(public)]


def _docs(public: dict[str, Any]) -> str:
    docs: list[str] = []
    for key in ("documents", "documents_to_prepare", "what_to_bring", "requirements", "documents_needed"):
        value = public.get(key) if key != "documents" else _packet(public).get("documents")
        if isinstance(value, list):
            docs.extend(str(x) for x in value)
    for row in _packet_rows(public):
        docs.extend(str(x) for x in row.get("documents") or [])
    return "\n".join(docs).lower()


def _inspections(public: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("inspections", "inspection_checklist", "inspections_required"):
        raw = public.get(key) if key != "inspections" else _packet(public).get("inspections") or public.get(key)
        if isinstance(raw, list):
            values.extend(str(x) for x in raw)
    for row in _packet_rows(public):
        values.extend(str(x) for x in row.get("inspections") or [])
    return "\n".join(values).lower()


def _assert_no_empty_apply_link(html_text: str):
    assert not re.search(r'<a\b[^>]+href=["\']\s*["\']', html_text, flags=re.I)


@pytest.mark.parametrize("case_id", C_CASE_IDS)
def test_sealed_packet_and_html_are_present_for_all_fable5_c_cases(case_id: str):
    _case, public, html_text = _load(case_id)
    packet = _packet(public)
    assert str(packet.get("schema_version") or "").startswith("final_public_permit_packet")
    assert public.get("sealed_public_packet_hash") == packet.get("sealed_public_packet_hash")
    assert html_text and "PermitAssist" in html_text


def test_c018_commercial_warehouse_to_assembly_packet_contract():
    _case, public, html_text = _load("C-018")
    visible = _blob(public, html_text)
    assert public.get("permit_decision") == "REQUIRED"
    assert _packet(public).get("lead_label") == "Commercial Building Permit — Change of Use / Tenant Improvement (Assembly)"
    assert "residential" not in visible
    assert {"building_ti", "fire_life_safety_assembly", "co_change_of_occupancy"} <= (_required_families(public) | _conditional_families(public))
    assert re.search(r"occupant\s+load|life[-\s]?safety|egress", _docs(public))


def test_c035_cannabis_co2_hazard_packet_contract():
    _case, public, html_text = _load("C-035")
    visible = _blob(public, html_text)
    assert public.get("permit_decision") == "REQUIRED"
    assert "fire_hazmat_co2" in (_required_families(public) | _conditional_families(public))
    assert re.search(r"co2|carbon dioxide|hazard", visible)
    docs = _docs(public)
    for needle in ("co2", "gas detection", "hazardous materials"):
        assert needle in docs
    assert {"building_ti", "electrical", "mechanical"} <= _required_families(public)


def test_c037_structural_facade_lintel_packet_contract():
    _case, public, html_text = _load("C-037")
    visible = _blob(public, html_text)
    assert public.get("permit_decision") == "REQUIRED"
    assert _packet(public).get("lead_label") == "Commercial Building Permit — Structural Facade / Masonry Repair"
    assert any("Structural Facade / Masonry Repair" in name for name in _names(public))
    assert "storefront / window-door alteration" not in visible
    assert re.search(r"structural (drawings|engineering)|licensed engineer|masonry lintel", _docs(public))
    assert "structural" in _inspections(public)


def test_r013_chicago_drywall_not_required_packet_and_render_contract():
    _case, public, html_text = _load("R-013")
    visible = _blob(public, html_text)
    assert public.get("permit_decision") == "NOT_REQUIRED"
    assert _packet(public).get("permit_required_verdict") == "NOT_REQUIRED"
    assert public.get("permit_required") is False
    assert not _required_packet_rows(public)
    assert not public.get("permits_required")
    assert "building permit" not in "\n".join(_names(public)).lower()
    assert "structural/foundation" not in visible
    assert "permit required: yes" not in visible
    assert re.search(r"permit required:\s*no|no permit required|not required", visible)
    assert not public.get("apply_url")
    _assert_no_empty_apply_link(html_text)


def test_r033_gfci_existing_boxes_no_food_fog_and_no_new_circuit_label():
    _case, public, html_text = _load("R-033")
    visible = _blob(public, html_text)
    assert public.get("permit_decision") == "REQUIRED"
    assert "health_food" not in (_required_families(public) | _conditional_families(public))
    assert "wastewater_pretreatment_fog" not in (_required_families(public) | _conditional_families(public))
    assert "food establishment" not in visible
    assert "fog" not in visible
    assert "new circuit / equipment connection" not in visible
    assert any("Device / Receptacle Replacement (Existing Circuits)" in name for name in _names(public))


def test_r049_homeowner_grill_gas_keeps_core_and_relabels_icc_blog():
    _case, public, html_text = _load("R-049")
    visible = _blob(public, html_text)
    assert public.get("permit_decision") == "REQUIRED"
    assert {"gas"} <= _required_families(public)
    assert re.search(r"pressure test|final gas", visible)
    assert "health_food" not in (_required_families(public) | _conditional_families(public))
    assert "wastewater_pretreatment_fog" not in (_required_families(public) | _conditional_families(public))
    assert "food establishment" not in visible
    assert "fog" not in visible
    sources = [s for s in public.get("sources") or [] if isinstance(s, dict) and "iccsafe.org" in str(s.get("url") or "")]
    assert sources, "ICC context link should be retained, not deleted"
    assert all("official" not in str(s.get("title") or s.get("label") or "").lower() for s in sources)
    assert all(str(s.get("source_role") or "") == "PUBLISHER_CONTEXT" for s in sources)
