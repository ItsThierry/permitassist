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
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "live100_1041efe_full_fix"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from server import build_customer_permit_view_model, render_share_page  # noqa: E402

CURRENT_C_CASES = ["C-002", "C-018", "C-030", "C-031", "C-037", "R-005", "R-013", "R-022", "R-032"]
NO_NEUTER_SENTINELS = ["C-001", "C-016", "C-023", "C-034", "C-039", "R-006", "R-018", "R-034"]


def load_case(case_id: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    raw = json.loads((FIXTURE_ROOT / case_id / "raw_lookup.json").read_text())
    case = raw["case"]
    old = os.environ.get("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD")
    os.environ["PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD"] = "1"
    try:
        public = build_customer_permit_view_model(
            raw.get("response_body") or {},
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


def packet(public: dict[str, Any]) -> dict[str, Any]:
    pkt = public.get("public_packet")
    assert isinstance(pkt, dict), "missing canonical public_packet"
    assert str(pkt.get("schema_version") or "").startswith("final_public_permit_packet")
    return pkt


def packet_rows(public: dict[str, Any], decision: str | None = None) -> list[dict[str, Any]]:
    rows = [r for r in packet(public).get("rows") or [] if isinstance(r, dict)]
    if decision:
        rows = [r for r in rows if str(r.get("decision") or "").upper() == decision]
    return rows


def families(public: dict[str, Any], decision: str = "REQUIRED") -> set[str]:
    return {str(r.get("family") or "") for r in packet_rows(public, decision)}


def visible_blob(public: dict[str, Any], html_text: str = "") -> str:
    rendered = re.sub(r"<script\b.*?</script>", " ", html.unescape(html_text), flags=re.I | re.S)
    return (json.dumps(public, sort_keys=True, default=str) + "\n" + rendered).lower()


def docs_blob(public: dict[str, Any]) -> str:
    values: list[str] = []
    for row in packet_rows(public):
        values.extend(str(x) for x in row.get("documents") or [])
    values.extend(str(x) for x in packet(public).get("documents") or [])
    return "\n".join(values).lower()


def source_labels(public: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for src in public.get("sources") or []:
        if isinstance(src, dict):
            labels.append(str(src.get("label") or src.get("title") or ""))
    for row in packet_rows(public):
        if row.get("source") or row.get("source_role"):
            labels.append(str(row.get("source") or row.get("source_role") or ""))
    return [x for x in labels if x]


@pytest.mark.parametrize("case_id", CURRENT_C_CASES)
def test_latest_red_cases_have_canonical_packet_and_api_render_parity(case_id: str):
    _case, public, html_text = load_case(case_id)
    pkt = packet(public)
    assert public.get("sealed_public_packet_hash") == pkt.get("sealed_public_packet_hash")
    payload_match = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html_text, flags=re.S)
    assert payload_match, "rendered report-data JSON missing"
    payload = json.loads(html.unescape(payload_match.group(1)))["share"]["data"]
    assert payload.get("permit_decision") == public.get("permit_decision")
    assert (payload.get("public_packet") or {}).get("sealed_public_packet_hash") == public.get("sealed_public_packet_hash")
    assert payload.get("permits_required") == public.get("permits_required")


def test_c002_commercial_office_ti_has_no_residential_electrical_or_masonry_lintel_leak():
    _case, public, html_text = load_case("C-002")
    blob = visible_blob(public, html_text)
    assert public.get("segment") == "commercial"
    assert "residential electrical" not in blob
    assert "masonry lintel" not in docs_blob(public)
    assert "facade structural" not in docs_blob(public)


def test_c018_commercial_change_of_use_not_residential_and_keeps_use_change_families():
    _case, public, html_text = load_case("C-018")
    blob = visible_blob(public, html_text)
    assert public.get("permit_decision") == "REQUIRED"
    assert "residential building permit" not in blob
    assert {"building_ti", "fire_life_safety_assembly", "planning_zoning", "co_change_of_occupancy"} <= families(public)


def test_c030_boston_lab_ti_no_fire_cleaning_apply_url_no_duplicate_building_and_has_mechanical():
    _case, public, _html_text = load_case("C-030")
    apply_url = str(public.get("apply_url") or "").lower()
    assert "clean" not in apply_url and "exhaust" not in apply_url
    assert "fire-prevention" not in apply_url
    req_fams = [str(r.get("family") or "") for r in packet_rows(public, "REQUIRED")]
    assert req_fams.count("building_ti") + req_fams.count("building") <= 1
    assert "mechanical" in set(req_fams)


def test_c031_madison_no_mechanical_hard_required_without_mechanical_scope_and_no_bare_source_labels():
    _case, public, _html_text = load_case("C-031")
    assert "mechanical" not in families(public)
    assert all(label.strip().lower() != "source" for label in source_labels(public))


def test_c037_facade_structural_keeps_building_but_does_not_hard_require_zoning_without_use_change():
    _case, public, _html_text = load_case("C-037")
    assert families(public) & {"building", "building_ti"}
    assert "planning_zoning" not in families(public)
    assert re.search(r"structural|masonry|lintel", docs_blob(public), flags=re.I)


def test_r005_garage_conversion_labels_and_co_are_not_unsupported_hard_required():
    _case, public, html_text = load_case("R-005")
    blob = visible_blob(public, html_text)
    assert "detached garage / accessory structure" not in blob
    assert "co_change_of_occupancy" not in families(public)
    assert all(label.strip().lower() != "source" for label in source_labels(public))


def test_r013_chicago_drywall_not_required_renders_no_required_artifacts():
    _case, public, html_text = load_case("R-013")
    blob = visible_blob(public, html_text)
    assert public.get("permit_decision") == "NOT_REQUIRED"
    assert public.get("permit_required") is False
    assert not packet_rows(public, "REQUIRED")
    assert "permit required: yes" not in blob
    assert "structural/foundation" not in blob
    assert not public.get("apply_url")


def test_r022_ev_charger_keeps_electrical_but_not_hard_required_building():
    _case, public, _html_text = load_case("R-022")
    assert "electrical" in families(public)
    assert "building" not in families(public)
    assert "building_ti" not in families(public)


def test_r032_same_size_windows_no_structural_docs_and_fee_does_not_mix_project_cost():
    _case, public, _html_text = load_case("R-032")
    docs = docs_blob(public)
    assert "structural/foundation" not in docs
    assert "foundation drawings" not in docs
    fee_text = json.dumps({"fee_range": public.get("fee_range"), "public_packet": packet(public).get("fees")}, default=str).lower()
    assert "project cost" not in fee_text
    assert not re.search(r"\$\s?9,?150\s*[-–]\s*\$\s?10,?250", fee_text)


@pytest.mark.parametrize("case_id", NO_NEUTER_SENTINELS)
def test_a_no_neuter_sentinels_keep_decisive_binary_and_primary_families(case_id: str):
    _case, public, _html_text = load_case(case_id)
    assert public.get("permit_decision") in {"REQUIRED", "NOT_REQUIRED"}
    assert public.get("permit_required") in {True, False}
    if public.get("permit_decision") == "REQUIRED":
        assert packet_rows(public, "REQUIRED"), case_id
        assert public.get("apply_url") or (public.get("apply_path") or {}).get("portal_url")


def test_holdout_manifest_is_frozen_and_not_part_of_development_red_cases():
    manifest = json.loads((FIXTURE_ROOT / "FREEZE_MANIFEST.json").read_text())
    holdout = set(manifest["holdout_case_ids"])
    assert len(holdout) == 20
    assert holdout.isdisjoint(CURRENT_C_CASES)
    assert holdout.isdisjoint(NO_NEUTER_SENTINELS)
