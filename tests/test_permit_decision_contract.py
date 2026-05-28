import copy
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "api") not in sys.path:
    sys.path.insert(0, str(ROOT / "api"))

import api.server as server  # noqa: E402
from api.permit_decision import (  # noqa: E402
    BANNED_CUSTOMER_SURFACE_RE,
    validate_customer_surface_contract,
)


def _official_result(city="Dallas", state="TX"):
    city_key = str(city or "").strip().lower()
    if state == "TX" and city_key == "austin":
        host = "www.austintexas.gov"
    elif state == "TX":
        host = "dallascityhall.com"
    else:
        host = "cityofpasadena.net"
    return {
        "permit_verdict": "YES",
        "confidence": "high",
        "permit_name": "Permit Required",
        "permit_type": "Permit Required",
        "permits_required": [],
        "sources": [f"https://{host}/building/permits"],
        "applying_office": f"{city} Building Department",
        "apply_url": "",
        "quality_warnings": ["exact portal subcategory not verified"],
    }


def _surface_text(value) -> str:
    return json.dumps(value, sort_keys=True).lower()


def _assert_clean_customer_contract(result: dict, *, decision: str, expected_kind_fragment: str):
    contract = result["permit_decision_contract"]
    assert contract["permit_decision"] == decision
    assert expected_kind_fragment.lower() in contract["permit_kind"].lower()
    assert contract["customer_next_step"]
    assert contract["exact_name_status"] in {"verified", "unverified", "not_applicable"}
    assert contract["exact_apply_url_status"] in {"verified", "unverified", "not_applicable"}
    assert validate_customer_surface_contract(result) == []
    text = _surface_text({
        "customer_headline": result.get("customer_headline"),
        "customer_next_step": result.get("customer_next_step"),
        "warnings": result.get("warnings"),
        "quality_warnings": result.get("quality_warnings"),
        "exact_name_customer_note": contract.get("exact_name_customer_note"),
        "exact_apply_url_customer_note": contract.get("exact_apply_url_customer_note"),
    })
    assert not BANNED_CUSTOMER_SURFACE_RE.search(text)
    assert "pending_active_retrieval" not in text
    assert "pendingview" not in text
    assert "pENDING_".lower() not in text
    # Raw internal uncertainty label must not be leaked into customer copy.
    assert "unverified" not in str(result.get("customer_next_step", "")).lower()


def test_required_contract_is_structural_not_likely_for_restaurant_medical_and_office_ti():
    cases = [
        (
            "Dallas restaurant TI with Type I hood, grease interceptor, plumbing, fire alarm, and health review",
            "Dallas",
            "TX",
            "Commercial Building / Tenant Improvement",
        ),
        (
            "Pasadena medical clinic tenant improvement with exam rooms, accessible restroom, plumbing, and fire alarm work",
            "Pasadena",
            "CA",
            "Commercial Building / Tenant Improvement",
        ),
        (
            "Dallas office tenant improvement with demising partitions, lighting controls, RTU diffuser relocation, and ADA restroom work",
            "Dallas",
            "TX",
            "Commercial Building / Tenant Improvement",
        ),
    ]

    for job, city, state, expected_kind in cases:
        result = server.finalize_permit_lookup_result(copy.deepcopy(_official_result(city, state)), job, city, state)

        _assert_clean_customer_contract(result, decision="REQUIRED", expected_kind_fragment=expected_kind)
        assert result["permit_decision"] == "REQUIRED"
        assert result["permit_kind"] == expected_kind
        assert result["customer_headline"].startswith("Permit required:")
        assert "likely required" not in _surface_text(result)
        assert "permit required" != result["customer_headline"].strip().lower()


def test_required_contract_covers_residential_trades_and_mixed_hvac_panel():
    cases = [
        ("Austin residential electrical panel upgrade to 200A", "Electrical"),
        ("Austin residential water heater replacement with gas reconnect", "Plumbing"),
        ("Austin residential HVAC condenser and furnace replacement", "Mechanical"),
        ("Austin residential shingle roof replacement", "Roofing"),
        ("Austin residential rooftop solar PV with battery backup", "Solar"),
        ("Austin residential HVAC heat pump replacement with 200A panel upgrade", "Mechanical"),
    ]

    for job, kind in cases:
        result = server.finalize_permit_lookup_result(copy.deepcopy(_official_result("Austin", "TX")), job, "Austin", "TX")

        _assert_clean_customer_contract(result, decision="REQUIRED", expected_kind_fragment=kind)
        if "panel upgrade" in job and "HVAC" in job:
            trade_kinds = {item["kind"] for item in result["trade_permits"]}
            assert {"Mechanical", "Electrical"}.issubset(trade_kinds)


def test_not_required_requires_positive_exemption_evidence_and_customer_step():
    result = server.finalize_permit_lookup_result(
        {
            "permit_decision": "NOT_REQUIRED",
            "permit_kind": "Other",
            "customer_next_step": "Keep the AHJ exemption note with the job file before starting work.",
            "positive_exemption_evidence": [
                {
                    "source_url": "https://dallascityhall.com/building/no-permit-needed",
                    "quote": "Painting, movable cases, and cosmetic finish work do not require a building permit.",
                }
            ],
            "sources": ["https://dallascityhall.com/building/no-permit-needed"],
            "permits_required": [],
        },
        "Dallas cosmetic repainting and movable display shelving only, no electrical, plumbing, structural, occupancy, or wall work",
        "Dallas",
        "TX",
    )

    _assert_clean_customer_contract(result, decision="NOT_REQUIRED", expected_kind_fragment="Other")
    assert result["customer_headline"].startswith(("Permit not required", "No permit required"))


def test_source_threshold_scope_resolves_to_required_not_conditional_customer_state():
    result = server.finalize_permit_lookup_result(
        {
            "permit_decision": "CONDITIONAL",
            "permit_kind": "Roofing",
            "customer_next_step": "Measure the exact repaired roof area; file a roofing permit if the repair exceeds 100 square feet.",
            "condition_threshold": {
                "threshold": "Roof repairs over 100 square feet require a roofing permit.",
                "source_url": "https://cityofpasadena.net/building/roofing-thresholds",
            },
            "sources": ["https://cityofpasadena.net/building/roofing-thresholds"],
            "permits_required": [],
        },
        "Pasadena residential roof patch repair, exact square footage unknown",
        "Pasadena",
        "CA",
    )

    _assert_clean_customer_contract(result, decision="REQUIRED", expected_kind_fragment="Roofing")
    assert result["customer_headline"].startswith("Permit required:")
    assert result["permit_decision"] in {"REQUIRED", "NOT_REQUIRED"}


def test_missing_evidence_or_fake_ahj_rejects_invalid_jurisdiction_without_pending_or_likely_language():
    result = server.finalize_permit_lookup_result(
        {
            "permit_verdict": "YES",
            "permit_name": "Permit Required",
            "sources": [],
            "permits_required": [],
            "quality_warnings": ["pending_active_retrieval", "PENDING_LOOKUP", "unverified"],
        },
        "Imaginaryville restaurant tenant improvement",
        "Imaginaryville",
        "ZZ",
    )

    assert result.get("input_status") == "rejected"
    assert result.get("error_code") == "unsupported_jurisdiction"
    assert "unsupported_jurisdiction" in result.get("validation_errors", [])
    assert result.get("permit_decision") not in {"REQUIRED", "NOT_REQUIRED"}
    assert result.get("permit_required") not in {True, False}
    text = _surface_text(server.sanitize_customer_visible_result(result))
    assert "likely required" not in text
    assert "pending" not in text
    assert "unverified" not in text
    assert "fail_closed" not in text


def test_white_label_report_renders_decision_contract_not_likely_or_ahj_surrender():
    result = server.finalize_permit_lookup_result(
        copy.deepcopy(_official_result("Dallas", "TX")),
        "Dallas office tenant improvement with demising partitions, lighting, and accessibility review",
        "Dallas",
        "TX",
    )

    rendered = server.render_white_label_report_html({"contractor_name": "ACME", "result": result, "city": "Dallas", "state": "TX"})
    text = html.unescape(rendered).lower()
    assert "permit required:" in text
    assert "commercial building / tenant improvement" in text
    assert "likely permits" not in text
    assert "likely required" not in text
    assert "verify exact permit type with the ahj" not in text
    assert "verify exact ahj" not in text
    assert "verify with ahj" not in text
    assert "verify with the ahj" not in text
    assert "likely primary permit type" not in text
    assert "needs_verification" not in text
    assert validate_customer_surface_contract(result, rendered_text=rendered) == []


def test_stale_fail_closed_recovers_when_official_evidence_and_scope_remain():
    result = server.finalize_permit_lookup_result(
        {
            "permit_decision": "FAIL_CLOSED_UNSUPPORTED_OR_NO_EVIDENCE",
            "permit_verdict": "UNKNOWN",
            "confidence": "low",
            "data_source": "state_rules",
            "confidence_reason": "County-level fallback used because exact city data was limited",
            "permit_name": "Electrical Permit",
            "permit_type": "Electrical Permit",
            "permits_required": [{"permit_type": "Electrical Permit"}],
            "sources": ["https://www.naperville.il.us/services/permits--licenses/"],
            "claim_citations": [
                {
                    "field": "permit_name",
                    "claim": "Electrical permits are handled by the City of Naperville.",
                    "source_url": "https://www.naperville.il.us/services/permits--licenses/",
                    "quoted_snippet": "Permits & Licenses",
                }
            ],
            "applying_office": "City of Naperville",
            "apply_url": "https://www.naperville.il.us/services/permits--licenses/",
        },
        "residential electrical panel upgrade to 200A",
        "Naperville",
        "IL",
        evidence_allowed=False,
    )

    assert result["permit_decision"] == "REQUIRED"
    assert result["permit_kind"] == "Electrical"
    assert result["trade_permits"]
    assert validate_customer_surface_contract(result) == []


def test_pessimistic_metadata_does_not_override_actual_official_evidence():
    base = _official_result("Dallas", "TX")
    base.update({
        "data_source": "state_rules",
        "confidence_reason": "County-level fallback used because exact city data was limited",
        "permits_required": [{"permit_type": "Building Permit — Tenant Improvement"}],
    })

    result = server.finalize_permit_lookup_result(
        base,
        "Dallas office tenant improvement with electrical and accessibility work",
        "Dallas",
        "TX",
        evidence_allowed=False,
    )

    assert result["permit_decision"] == "REQUIRED"
    assert result["permit_kind"] == "Commercial Building / Tenant Improvement"
