"""Verified-tier readiness matrix skeleton for PermitAssist evidence packs.

This is intentionally a small typed skeleton first: it defines the target fields
and launch-critical vertical/city cases without running API lookups or scraping
AHJ portals. Sprint 2+ can attach live evaluators and more city-specific proof.
"""

from dataclasses import dataclass

CORE_EVIDENCE_FIELDS = (
    "permit_type",
    "apply_url",
    "fee_range",
    "approval_timeline",
    "inspections",
)

READINESS_LEVELS = ("verified", "partial", "needs_verification")


@dataclass(frozen=True)
class VerifiedTierCase:
    case_id: str
    state: str
    city: str
    vertical: str
    prompt: str
    expected_primary_family: str
    field_expectations: dict[str, str]
    warnings_visible: bool
    commercial_primary_correct: bool
    state_overlay_visible: bool

    def validate(self) -> None:
        missing = set(CORE_EVIDENCE_FIELDS) - set(self.field_expectations)
        if missing:
            raise ValueError(f"{self.case_id} missing field expectations: {sorted(missing)}")
        invalid = {
            field: value
            for field, value in self.field_expectations.items()
            if field not in CORE_EVIDENCE_FIELDS or value not in READINESS_LEVELS
        }
        if invalid:
            raise ValueError(f"{self.case_id} invalid field expectations: {invalid}")


VERIFIED_TIER_MATRIX: tuple[VerifiedTierCase, ...] = (
    VerifiedTierCase(
        case_id="fl_miami_dade_dental_clinic_ti_gold",
        state="FL",
        city="Miami-Dade County",
        vertical="medical_clinic_ti",
        prompt="Dental clinic tenant improvement in Miami-Dade with x-ray and nitrous; no surgery or anesthesia.",
        expected_primary_family="Building Permit — Tenant Improvement / Medical Clinic Interior Alteration",
        field_expectations={
            "permit_type": "partial",
            "apply_url": "verified",
            "fee_range": "needs_verification",
            "approval_timeline": "needs_verification",
            "inspections": "needs_verification",
        },
        warnings_visible=True,
        commercial_primary_correct=True,
        state_overlay_visible=True,
    ),
    VerifiedTierCase(
        case_id="tx_dallas_restaurant_ti",
        state="TX",
        city="Dallas",
        vertical="restaurant_ti",
        prompt="Restaurant tenant improvement in Dallas with Type I hood, grease interceptor, dining area, and restroom changes.",
        expected_primary_family="Building Permit — Tenant Improvement / Restaurant Interior Alteration",
        field_expectations={field: "partial" for field in CORE_EVIDENCE_FIELDS},
        warnings_visible=True,
        commercial_primary_correct=True,
        state_overlay_visible=True,
    ),
    VerifiedTierCase(
        case_id="tx_houston_restaurant_ti",
        state="TX",
        city="Houston",
        vertical="restaurant_ti",
        prompt="Restaurant tenant improvement in Houston with hood, grease waste, MEP, and fire review.",
        expected_primary_family="Building Permit — Tenant Improvement / Restaurant Interior Alteration",
        field_expectations={field: "partial" for field in CORE_EVIDENCE_FIELDS},
        warnings_visible=True,
        commercial_primary_correct=True,
        state_overlay_visible=True,
    ),
    VerifiedTierCase(
        case_id="tx_austin_office_ti",
        state="TX",
        city="Austin",
        vertical="office_ti",
        prompt="Office tenant improvement in Austin with demising walls, lighting, HVAC diffusers, and data cabling.",
        expected_primary_family="Building Permit — Tenant Improvement / Office Interior Alteration",
        field_expectations={field: "partial" for field in CORE_EVIDENCE_FIELDS},
        warnings_visible=True,
        commercial_primary_correct=True,
        state_overlay_visible=True,
    ),
    VerifiedTierCase(
        case_id="tx_austin_dental_clinic_ti",
        state="TX",
        city="Austin",
        vertical="medical_clinic_ti",
        prompt="Dental clinic tenant improvement in Austin with x-ray and nitrous; no surgery or overnight stay.",
        expected_primary_family="Building Permit — Tenant Improvement / Medical Clinic Interior Alteration",
        field_expectations={field: "partial" for field in CORE_EVIDENCE_FIELDS},
        warnings_visible=True,
        commercial_primary_correct=True,
        state_overlay_visible=True,
    ),
    VerifiedTierCase(
        case_id="ca_los_angeles_medical_clinic_ti",
        state="CA",
        city="Los Angeles",
        vertical="medical_clinic_ti",
        prompt="Medical clinic tenant improvement in Los Angeles with exam rooms, x-ray, sinks, and HVAC changes.",
        expected_primary_family="Building Permit — Tenant Improvement / Medical Clinic Interior Alteration",
        field_expectations={field: "partial" for field in CORE_EVIDENCE_FIELDS},
        warnings_visible=True,
        commercial_primary_correct=True,
        state_overlay_visible=True,
    ),
    VerifiedTierCase(
        case_id="ca_los_angeles_office_ti",
        state="CA",
        city="Los Angeles",
        vertical="office_ti",
        prompt="Office tenant improvement in Los Angeles with partitions, suspended ceiling, lighting controls, and HVAC diffuser relocation.",
        expected_primary_family="Building Permit — Tenant Improvement / Office Interior Alteration",
        field_expectations={field: "partial" for field in CORE_EVIDENCE_FIELDS},
        warnings_visible=True,
        commercial_primary_correct=True,
        state_overlay_visible=True,
    ),
    VerifiedTierCase(
        case_id="fl_miami_restaurant_ti",
        state="FL",
        city="Miami",
        vertical="restaurant_ti",
        prompt="Restaurant tenant improvement in Miami with cooking hood, grease interceptor, fire suppression, and accessibility work.",
        expected_primary_family="Building Permit — Tenant Improvement / Restaurant Interior Alteration",
        field_expectations={field: "partial" for field in CORE_EVIDENCE_FIELDS},
        warnings_visible=True,
        commercial_primary_correct=True,
        state_overlay_visible=True,
    ),
    VerifiedTierCase(
        case_id="ma_boston_medical_clinic_ti",
        state="MA",
        city="Boston",
        vertical="medical_clinic_ti",
        prompt="Dental clinic tenant improvement in Boston with x-ray, nitrous, exam sinks, and no surgery or anesthesia.",
        expected_primary_family="Building Permit — Tenant Improvement / Medical Clinic Interior Alteration",
        field_expectations={field: "partial" for field in CORE_EVIDENCE_FIELDS},
        warnings_visible=True,
        commercial_primary_correct=True,
        state_overlay_visible=True,
    ),
    VerifiedTierCase(
        case_id="ma_boston_office_ti",
        state="MA",
        city="Boston",
        vertical="office_ti",
        prompt="Office tenant improvement in Boston with new partitions, lighting, HVAC balancing, data cabling, and exit signs.",
        expected_primary_family="Building Permit — Tenant Improvement / Office Interior Alteration",
        field_expectations={field: "partial" for field in CORE_EVIDENCE_FIELDS},
        warnings_visible=True,
        commercial_primary_correct=True,
        state_overlay_visible=True,
    ),
    VerifiedTierCase(
        case_id="residential_roof_control",
        state="CA",
        city="San Diego",
        vertical="residential_roof",
        prompt="Residential roof replacement in San Diego.",
        expected_primary_family="Residential Roofing Permit",
        field_expectations={field: "partial" for field in CORE_EVIDENCE_FIELDS},
        warnings_visible=True,
        commercial_primary_correct=False,
        state_overlay_visible=False,
    ),
    VerifiedTierCase(
        case_id="residential_water_heater_control",
        state="FL",
        city="Orlando",
        vertical="residential_water_heater",
        prompt="Residential water heater replacement in Orlando.",
        expected_primary_family="Residential Plumbing Permit",
        field_expectations={field: "partial" for field in CORE_EVIDENCE_FIELDS},
        warnings_visible=True,
        commercial_primary_correct=False,
        state_overlay_visible=False,
    ),
)


def readiness_from_claim_citations(claim_citations: list[dict]) -> dict[str, str]:
    """Summarize strict field confidence into verified-tier readiness labels."""
    readiness = {field: "needs_verification" for field in CORE_EVIDENCE_FIELDS}
    for citation in claim_citations or []:
        field = str(citation.get("field") or "")
        if field not in readiness:
            continue
        confidence = str(citation.get("confidence") or "needs_verification").lower()
        if confidence == "high":
            readiness[field] = "verified"
        elif confidence in {"medium", "partial"}:
            readiness[field] = "partial"
        else:
            readiness[field] = "needs_verification"
    return readiness


def evaluate_case_result(case: VerifiedTierCase, result: dict) -> dict:
    """Compare a result's strict citations/warnings against a matrix case."""
    case.validate()
    actual = readiness_from_claim_citations(result.get("claim_citations") or [])
    mismatches = {
        field: {"expected": expected, "actual": actual.get(field)}
        for field, expected in case.field_expectations.items()
        if actual.get(field) != expected
    }
    warnings = result.get("quality_warnings") or []
    if case.warnings_visible and not warnings:
        mismatches["warnings_visible"] = {"expected": True, "actual": False}
    return {
        "case_id": case.case_id,
        "passed": not mismatches,
        "actual_field_readiness": actual,
        "mismatches": mismatches,
    }


def validate_verified_tier_matrix() -> None:
    seen = set()
    for case in VERIFIED_TIER_MATRIX:
        if case.case_id in seen:
            raise ValueError(f"duplicate matrix case_id: {case.case_id}")
        seen.add(case.case_id)
        case.validate()


def matrix_summary() -> dict:
    validate_verified_tier_matrix()
    by_vertical: dict[str, int] = {}
    by_state: dict[str, int] = {}
    for case in VERIFIED_TIER_MATRIX:
        by_vertical[case.vertical] = by_vertical.get(case.vertical, 0) + 1
        by_state[case.state] = by_state.get(case.state, 0) + 1
    return {
        "case_count": len(VERIFIED_TIER_MATRIX),
        "fields": CORE_EVIDENCE_FIELDS,
        "by_vertical": by_vertical,
        "by_state": by_state,
    }
