from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))
ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_fable5_customer_pov_20260702T121009Z"

from closed_world_decision import (  # noqa: E402
    FeeType,
    apply_closed_world_customer_contract,
    check_render_fidelity,
    classify_renderable_link,
)


def _record(case_id: str) -> dict:
    for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec["case"]["id"] == case_id:
            return rec
    raise AssertionError(f"case not found: {case_id}")


def _apply(case_id: str) -> dict:
    rec = _record(case_id)
    case = rec["case"]
    return apply_closed_world_customer_contract(
        rec["response_body"],
        case["job_type"],
        case["city"],
        case["state"],
        job_category=case.get("segment"),
    )


def test_c010_project_costs_are_not_rendered_as_permit_fees():
    public = _apply("C-010")
    fee_text = str(public.get("fee_range") or "")
    assert "$300" in fee_text or "$900" in fee_text
    assert "$4,200" not in fee_text
    assert "$11,600" not in fee_text
    typed = public.get("fees_typed") or []
    assert any(item.get("fee_type") == FeeType.PERMIT_FEE.value for item in typed)
    assert all(item.get("fee_type") in {ft.value for ft in FeeType} for item in typed)


def test_placeholder_or_irrelevant_links_are_not_renderable():
    bad = classify_renderable_link("https://www.portlandmaine.gov/DocumentCenter/View/12345/Current-Fee-Schedules", "Portland", "ME")
    assert not bad.renderable
    assert bad.reason == "placeholder_pattern"
    blog = classify_renderable_link("https://example.com/landscaping-equipment-blog", "Salt Lake City", "UT")
    assert not blog.renderable


def test_family_specific_fee_text_is_not_copied_to_unrelated_rows():
    source = {
        "fee_range": "Electrical permit fee: $125.",
        "source_urls": ["https://www.gilbertaz.gov/departments/development-services"],
    }
    public = apply_closed_world_customer_contract(source, "install illuminated wall sign", "Gilbert", "AZ", job_category="commercial")
    by_family = {row["family"]: row for row in public["permits_required"]}
    assert by_family["electrical"].get("fees")
    assert not by_family["sign"].get("fees")


def test_public_packet_matches_decision_rows_for_focus_cases():
    for case_id in ["R-033", "C-010", "R-034"]:
        public = _apply(case_id)
        issues = check_render_fidelity(public)
        assert issues == [], (case_id, issues, public.get("public_packet_rows"))


def test_link_liveness_metadata_quarantines_bad_links_without_generic_neutering():
    public = _apply("C-010")
    assert public.get("permit_decision") == "REQUIRED"
    assert public.get("permit_required") is True
    assert isinstance(public.get("link_liveness"), list)
    assert all("/12345" not in str(url) for url in public.get("source_urls") or [])
