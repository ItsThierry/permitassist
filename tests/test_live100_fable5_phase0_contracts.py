from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from family_reconciliation_gate import family_from_row
from server import build_customer_permit_view_model, render_share_page

FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "live100_fable5_phase0"
FINAL_CF_CASE_IDS = [
    "R-010",
    "R-013",
    "R-028",
    "R-033",
    "R-049",
    "C-009",
    "C-016",
    "C-018",
    "C-028",
    "C-033",
    "C-036",
    "C-048",
    "C-050",
]
SENTINEL_CASE_IDS = sorted(
    p.name
    for p in FIXTURE_ROOT.iterdir()
    if (p / "expected_contract.json").exists()
    and json.loads((p / "expected_contract.json").read_text()).get("protection")
)
HARD_DEAD_STATUSES = {"HARD_404", "IRRELEVANT_REDIRECT", "MISSING", "BROKEN", "DEAD"}


def _load(case_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, dict[str, Any]]:
    raw = json.loads((FIXTURE_ROOT / case_id / "raw_lookup.json").read_text())
    contract = json.loads((FIXTURE_ROOT / case_id / "expected_contract.json").read_text())
    actual_packet = json.loads((FIXTURE_ROOT / case_id / "actual_packet_505b7c8.json").read_text())
    case = raw["case"]
    old_gate = os.environ.get("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD")
    os.environ["PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD"] = "1"
    try:
        public = build_customer_permit_view_model(
            raw["response_body"],
            case["job_type"],
            case["city"],
            case["state"],
            job_category=case.get("segment"),
        )
    finally:
        if old_gate is None:
            os.environ.pop("PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD", None)
        else:
            os.environ["PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD"] = old_gate
    html = render_share_page({"data": public, "job_type": case["job_type"], "city": case["city"], "state": case["state"]})
    return case, public, contract, html, actual_packet


def _rows(public: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [r for r in public.get(key) or [] if isinstance(r, dict)]


def _row_name(row: dict[str, Any]) -> str:
    return str(row.get("permit_type") or row.get("permit_name") or row.get("name") or row.get("kind") or "").strip()


def _family_multiset(rows: list[dict[str, Any]]) -> Counter[str]:
    return Counter(family_from_row(r) for r in rows)


def _family_set(rows: list[dict[str, Any]]) -> set[str]:
    return set(_family_multiset(rows))


def _visible_blob(public: dict[str, Any], html: str) -> str:
    return (json.dumps(public, sort_keys=True, default=str) + "\n" + html).lower()


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, dict):
                out.extend(str(v) for v in item.values() if isinstance(v, (str, int, float)))
            else:
                out.append(str(item))
        return out
    return [str(value)]


def _packet_texts(public: dict[str, Any]) -> dict[str, str]:
    docs = []
    inspections = []
    for key in ("documents_to_prepare", "what_to_bring", "required_documents", "documents"):
        docs.extend(_text_list(public.get(key)))
    for key in ("inspections", "inspection_checklist", "inspections_required"):
        inspections.extend(_text_list(public.get(key)))
    for row in _rows(public, "public_packet_rows"):
        docs.extend(_text_list(row.get("documents")))
        inspections.extend(_text_list(row.get("inspections")))
    return {"docs": "\n".join(docs).lower(), "inspections": "\n".join(inspections).lower()}


def _apply_status(public: dict[str, Any]) -> str | None:
    apply_path = public.get("apply_path") if isinstance(public.get("apply_path"), dict) else {}
    for key in ("apply_path_status", "status", "link_status", "url_status", "typed_status"):
        value = public.get(key) if key in public else apply_path.get(key)
        if value:
            return str(value).upper()
    return None


@pytest.mark.parametrize("case_id", FINAL_CF_CASE_IDS)
def test_final_cf_contracts_are_enforced(case_id: str):
    _case, public, contract, html, actual_packet = _load(case_id)
    required = _rows(public, "permits_required")
    conditional = _rows(public, "conditional_permits")
    required_names = [_row_name(r) for r in required]
    conditional_names = [_row_name(r) for r in conditional]
    required_families = _family_set(required)
    conditional_families = _family_set(conditional)
    visible = _visible_blob(public, html)
    packet_texts = _packet_texts(public)

    assert public.get("permit_decision") == contract.get("expected_decision", "REQUIRED")

    for family in contract.get("required_families_must_include", []):
        assert family in required_families, (case_id, family, required_families, required_names)
    for family in contract.get("required_families_must_not_include", []):
        assert family not in required_families, (case_id, family, required_families, required_names)
    for family in contract.get("conditional_families_must_not_include", []):
        assert family not in conditional_families, (case_id, family, conditional_families, conditional_names)

    for needle in contract.get("required_name_must_include", []):
        assert any(needle.lower() in name.lower() for name in required_names), (case_id, needle, required_names)
    for needle in contract.get("required_name_must_not_include", []):
        assert not any(needle.lower() in name.lower() for name in required_names), (case_id, needle, required_names)
    for exact in contract.get("forbidden_exact_required_names", []):
        assert exact not in required_names, (case_id, exact, required_names)
    for needle in contract.get("first_required_name_must_not_include", []):
        assert required_names and needle.lower() not in required_names[0].lower(), (case_id, needle, required_names)

    for family, max_count in contract.get("dedupe_family_max", {}).items():
        assert _family_multiset(required)[family] <= max_count, (case_id, family, _family_multiset(required), required_names)
    for needle, max_count in contract.get("dedupe_name_contains_max", {}).items():
        count = sum(1 for name in required_names if needle.lower() in name.lower())
        assert count <= max_count, (case_id, needle, count, required_names)

    for needle in contract.get("visible_must_include", []):
        assert needle.lower() in visible, (case_id, needle)
    for alternatives in contract.get("visible_must_include_any", []):
        assert any(str(needle).lower() in visible for needle in alternatives), (case_id, alternatives)
    for forbidden in contract.get("forbidden_visible", []):
        assert forbidden.lower() not in visible, (case_id, forbidden)
    frozen_visible = json.dumps(actual_packet, sort_keys=True, default=str).lower()
    for forbidden in contract.get("frozen_forbidden_visible", []):
        assert forbidden.lower() not in frozen_visible, (case_id, forbidden)
    if contract.get("no_residential_label_on_commercial"):
        assert "residential" not in "\n".join(required_names).lower(), (case_id, required_names)

    for needle in contract.get("required_docs_must_include", []):
        assert needle.lower() in packet_texts["docs"], (case_id, needle, packet_texts["docs"])
    for needle in contract.get("inspections_must_include", []):
        assert needle.lower() in packet_texts["inspections"], (case_id, needle, packet_texts["inspections"])

    status = _apply_status(public)
    if contract.get("typed_apply_status_required"):
        assert status is not None, (case_id, public.get("apply_path"), public.get("apply_url"))
    if contract.get("action_path_must_not_be_hard_dead"):
        assert status not in HARD_DEAD_STATUSES, (case_id, status, public.get("apply_url"), public.get("apply_path"))
    if contract.get("if_required_apply_path_nonempty") and public.get("permit_decision") == "REQUIRED":
        assert public.get("apply_url") or (isinstance(public.get("apply_path"), dict) and public["apply_path"].get("portal_url")), case_id
    if contract.get("apply_path_nonempty"):
        assert public.get("apply_url") or (isinstance(public.get("apply_path"), dict) and public["apply_path"].get("portal_url")), case_id

    for needle in contract.get("fee_must_not_contain", []):
        assert needle.lower() not in str(public.get("fee_range") or "").lower(), (case_id, public.get("fee_range"))
    if contract.get("source_labels_no_blank"):
        labels = [str(s.get("label") or s.get("title") or "").strip() for s in _text_list([])]
        for source in public.get("sources_checked") or public.get("sources") or []:
            if isinstance(source, dict):
                label = str(source.get("label") or source.get("title") or source.get("source_label") or "").strip()
                labels.append(label)
        assert not labels or all(labels), (case_id, labels)


@pytest.mark.parametrize("case_id", SENTINEL_CASE_IDS)
def test_no_neuter_sentinel_family_sets_stay_green_on_baseline(case_id: str):
    _case, public, contract, _html, _actual_packet = _load(case_id)
    required = _rows(public, "permits_required")
    conditional = _rows(public, "conditional_permits")
    assert public.get("permit_decision") == contract["expected_decision"], case_id
    assert sorted(_family_multiset(required).elements()) == contract["baseline_required_family_multiset"], (case_id, _family_multiset(required), contract)
    assert sorted(_family_multiset(conditional).elements()) == contract["baseline_conditional_family_multiset"], (case_id, _family_multiset(conditional), contract)
    assert len(required) == contract["baseline_required_count"], case_id
