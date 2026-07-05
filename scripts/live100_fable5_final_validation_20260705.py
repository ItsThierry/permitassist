#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "live100_fable5_phase0_phase1_20260705"
PASS1 = ART / __import__("os").environ.get("FABLE5_VALIDATION_PASS1", "fable5_final_verified_pass1")
PASS2 = ART / __import__("os").environ.get("FABLE5_VALIDATION_PASS2", "fable5_final_verified_pass2")
GREEN = ROOT / "tests" / "fixtures" / "live100_fable5_final_green_freeze_68_20260705.json"
REPORT_DIR = ART / "final_validation"

BUILDING_BUCKET = {"building", "building_ti", "building_adu", "demolition", "racking"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def bucket(family: str) -> str:
    return "building" if family in BUILDING_BUCKET else family


def load_manifest(root: Path) -> dict[str, Any]:
    return load_json(root / "manifest.json")


def row_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["case_id"]): row for row in manifest.get("rows", [])}


def public_case(root: Path, case_id: str) -> dict[str, Any]:
    matches = list((root / "public_json").glob(f"*{case_id}.json"))
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one public_json file for {case_id}, found {len(matches)}")
    return load_json(matches[0])


def required(obj: dict[str, Any]) -> set[str]:
    return {str(row.get("family") or row.get("filing_family") or "") for row in obj.get("permits_required") or [] if isinstance(row, dict)}


def conditional(obj: dict[str, Any]) -> set[str]:
    return {str(row.get("family") or row.get("filing_family") or "") for row in obj.get("related_permits") or [] if isinstance(row, dict)}


def assert_required(case_id: str, *families: str) -> None:
    obj = public_case(PASS2, case_id)
    got = {bucket(f) for f in required(obj)}
    need = {bucket(f) for f in families}
    missing = sorted(need - got)
    if missing:
        raise AssertionError(f"{case_id} missing required families {missing}; got={sorted(got)}")
    if str(obj.get("permit_decision") or "").upper() != "REQUIRED":
        raise AssertionError(f"{case_id} expected REQUIRED, got {obj.get('permit_decision')}")


def assert_not_required(case_id: str) -> None:
    obj = public_case(PASS2, case_id)
    if str(obj.get("permit_decision") or "").upper() != "NOT_REQUIRED":
        raise AssertionError(f"{case_id} expected NOT_REQUIRED, got {obj.get('permit_decision')}")
    if required(obj):
        raise AssertionError(f"{case_id} NOT_REQUIRED has required rows: {sorted(required(obj))}")


def assert_conditional(case_id: str, *families: str) -> None:
    obj = public_case(PASS2, case_id)
    got = {bucket(f) for f in conditional(obj)}
    need = {bucket(f) for f in families}
    missing = sorted(need - got)
    if missing:
        raise AssertionError(f"{case_id} missing conditional families {missing}; got={sorted(got)}")


def assert_not_required_family(case_id: str, *families: str) -> None:
    obj = public_case(PASS2, case_id)
    got = {bucket(f) for f in required(obj)}
    bad = sorted({bucket(f) for f in families} & got)
    if bad:
        raise AssertionError(f"{case_id} families should not be REQUIRED: {bad}; got={sorted(got)}")


def validate() -> dict[str, Any]:
    m1 = load_manifest(PASS1)
    m2 = load_manifest(PASS2)
    errors: list[str] = []

    for label, m in (("pass1", m1), ("pass2", m2)):
        if m.get("records") != 100:
            errors.append(f"{label}: expected 100 records, got {m.get('records')}")
        if m.get("parity_failure_count") != 0:
            errors.append(f"{label}: parity failures {m.get('parity_failure_count')}")
        if m.get("tripwire_failure_count") != 0:
            errors.append(f"{label}: tripwire failures {m.get('tripwire_failure_count')}")
    if m1.get("public_hash_all") != m2.get("public_hash_all"):
        errors.append("double-run public_hash_all mismatch")
    if m1.get("html_hash_all") != m2.get("html_hash_all"):
        errors.append("double-run html_hash_all mismatch")

    # Green-freeze: decision unchanged, bucketed required-family set is a superset,
    # and nonblank apply URLs never regress to blank.
    green = load_json(GREEN)["cases"]
    rm = row_map(m2)
    green_errors = []
    for cid, frozen in green.items():
        row = rm[cid]
        frozen_req = {bucket(f) for f in frozen.get("canonical_required_family_keys") or []}
        current_req = {bucket(f) for f in row.get("required_families") or []}
        if row.get("decision") != frozen.get("decision"):
            green_errors.append(f"{cid}: decision {frozen.get('decision')} -> {row.get('decision')}")
        if not frozen_req.issubset(current_req):
            green_errors.append(f"{cid}: required families missing {sorted(frozen_req - current_req)}")
        if frozen.get("apply_url") and not row.get("apply_url"):
            green_errors.append(f"{cid}: apply_url regressed to blank")
    if green_errors:
        errors.extend("green-freeze: " + e for e in green_errors)

    # Phase 2 chunk target structural assertions.
    try:
        # B decision floors / false NR fixes.
        assert_required("C-009", "grading")
        assert_required("C-048", "building_ti", "electrical", "grading")
        assert_not_required("R-024")
        assert_required("R-027", "mechanical", "fire_suppression")
        assert_required("R-034", "grading")
        assert_required("R-040", "plumbing")
        assert_required("R-050", "building")
        assert_required("C-040", "building_ti", "mechanical", "plumbing", "refrigeration")
        # C action/source/authority completeness targets.
        assert_required("C-020", "building_ti", "electrical", "plumbing", "gas", "mechanical", "planning_zoning", "co_change_of_occupancy")
        assert_required("R-020", "building")
        assert_required("R-038", "refrigeration", "electrical")
        assert_required("R-042", "building")
        # D1/D2 family floors and companion consistency.
        assert_required("C-016", "building", "fire_suppression", "health_food", "wastewater_pretreatment_fog")
        assert_required("C-017", "building", "electrical", "fire_alarm", "fire_suppression")
        assert_required("C-023", "building", "electrical", "plumbing", "sign", "wastewater_pretreatment_fog")
        assert_required("C-026", "building_ti", "electrical", "mechanical")
        assert_required("C-035", "building", "mechanical", "fire_suppression")
        assert_required("C-042", "building", "electrical", "environmental", "fire_suppression", "gas", "plumbing")
        assert_required("C-043", "building_ti", "co_change_of_occupancy", "fire_life_safety_assembly", "mechanical", "planning_zoning", "plumbing")
        assert_required("C-049", "building", "electrical", "mechanical", "fire_suppression", "plumbing")
        assert_required("C-050", "building_ti", "co_change_of_occupancy", "health_food", "mechanical", "planning_zoning", "plumbing", "refrigeration", "wastewater_pretreatment_fog")
        assert_required("R-016", "building_ti", "electrical", "mechanical", "refrigeration")
        assert_required("R-019", "co_change_of_occupancy", "electrical", "mechanical", "plumbing", "refrigeration")
        # E1/E2 demotions/labels/flips.
        assert_required("C-031", "electrical", "mechanical", "plumbing")
        assert_required("C-032", "building_ti", "electrical")
        assert_conditional("C-032", "sign")
        assert_not_required_family("C-037", "health_food", "liquor")
        assert_conditional("C-037", "health_food", "liquor")
        assert_not_required_family("C-046", "health_food", "wastewater_pretreatment_fog")
        assert_conditional("C-046", "wastewater_pretreatment_fog")
        assert_required("R-012", "mechanical")
        assert_not_required_family("R-012", "plumbing")
        assert_conditional("R-012", "plumbing")
        assert_not_required("R-022")
        assert_not_required("R-036")
    except AssertionError as exc:
        errors.append(str(exc))

    # REQUIRED completeness: exact URL OR named-authority degraded path.
    for cid, row in rm.items():
        if row.get("decision") != "REQUIRED":
            continue
        obj = public_case(PASS2, cid)
        ap_raw = obj.get("apply_path")
        ap: dict[str, Any] = ap_raw if isinstance(ap_raw, dict) else {}
        path_state = str(ap.get("status") or ap.get("typed_status") or ap.get("state") or "").upper()
        allowed_states = {"VERIFY_WITH_PERMIT_OFFICE", "OFFICIAL_SOURCE_FALLBACK", "RESOLVED_PORTAL", "CONTACT_AHJ"}
        if not (row.get("apply_url") or (ap.get("office_name") and path_state in allowed_states)):
            errors.append(f"{cid}: REQUIRED missing exact apply URL or permit-office fallback")

    report = {
        "pass": not errors,
        "errors": errors,
        "pass1": {"public_hash_all": m1.get("public_hash_all"), "html_hash_all": m1.get("html_hash_all"), "tripwire_failure_count": m1.get("tripwire_failure_count"), "parity_failure_count": m1.get("parity_failure_count")},
        "pass2": {"public_hash_all": m2.get("public_hash_all"), "html_hash_all": m2.get("html_hash_all"), "tripwire_failure_count": m2.get("tripwire_failure_count"), "parity_failure_count": m2.get("parity_failure_count")},
        "green_freeze_count": len(green),
        "red_target_count": 32,
    }
    return report


if __name__ == "__main__":
    report = validate()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "FABLE5_FINAL_VALIDATION_REPORT.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["pass"] else 1)
