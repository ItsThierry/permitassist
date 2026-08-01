import copy
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_20260629T0032Z"
CONTRACT_PATH = ROOT / "tests" / "fixtures" / "live_customer_100_red_contracts_adjudicated_v2_20260730.json"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))
os.environ.setdefault("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
os.environ.setdefault("OPENAI_API_KEY", "test-key")


def load_contracts() -> list[dict[str, Any]]:
    data = json.loads(CONTRACT_PATH.read_text())
    return list(data["contracts"])


def load_contract(case_id: str) -> dict[str, Any]:
    for contract in load_contracts():
        if contract["case_id"] == case_id:
            return contract
    raise KeyError(case_id)


def _response_body(contract: dict[str, Any]) -> dict[str, Any]:
    path = Path(contract["artifact_response_json_path"])
    raw = json.loads(path.read_text())
    body = raw.get("response_body") or raw.get("body") or raw
    assert isinstance(body, dict), path
    return body


def server_module():
    from api import server  # noqa: WPS433
    return server


def build_public(contract: dict[str, Any]) -> dict[str, Any]:
    server = server_module()
    return server.build_customer_permit_view_model(
        copy.deepcopy(_response_body(contract)),
        contract["input_scope"],
        contract["city"],
        contract["state"],
        job_category=contract.get("segment"),
    )


def row_status(row: dict[str, Any]) -> str:
    server = server_module()
    for func_name in ("_pa20_row_status", "_customer_row_status"):
        func = getattr(server, func_name, None)
        if callable(func):
            status = str(func(row) or "").upper()
            if status:
                return "CONDITIONAL" if status == "CONDITIONAL_REQUIRED" else status
    raw = str(row.get("status") or row.get("decision") or row.get("requirement") or "").upper()
    if raw in {"REQUIRED", "VERIFY", "CONDITIONAL", "NOT_REQUIRED"}:
        return raw
    return "REQUIRED" if row.get("required") is True else "VERIFY"


def family_from_row(row: dict[str, Any]) -> str:
    text = " ".join(str(row.get(k) or "") for k in (
        "filing_family", "family", "display_family", "kind", "category", "permit_kind",
        "permit_type", "permit_name", "approval_type", "portal_selection",
    )).lower()
    if re.search(r"\b(grade|grading|sitework|site work|drainage|land disturbance|civil)\b", text):
        return "grading"
    if "foundation" in text:
        return "foundation"
    server = server_module()
    for func_name in ("_pa20_row_family", "_customer_row_family"):
        func = getattr(server, func_name, None)
        if callable(func):
            fam = str(func(row) or "").lower()
            if fam:
                if fam in {"building_ti", "building_adu"}:
                    return "building"
                if fam in {"fire_suppression"}:
                    return "fire"
                if fam in {"health_food_establishment"}:
                    return "health"
                if fam in {"wastewater_pretreatment_fog"}:
                    return "wastewater"
                if fam in {"co_change_of_occupancy"}:
                    return "co"
                if fam in {"planning_zoning", "historic_review"}:
                    return "planning" if fam == "planning_zoning" else "historic"
                return fam
    return "other"


def visible_rows(public: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for key in ("permits_required", "related_permits", "companion_permits", "trade_permits"):
        for row in public.get(key) or []:
            if isinstance(row, dict):
                rows.append(row)
    return rows


def required_rows(public: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in visible_rows(public) if row_status(row) == "REQUIRED" or row.get("required") is True]


def families_by_status(public: dict[str, Any]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for row in visible_rows(public):
        out.setdefault(family_from_row(row), set()).add(row_status(row))
    return out


def required_families(public: dict[str, Any]) -> set[str]:
    return {family_from_row(row) for row in required_rows(public)}


def public_text(public: dict[str, Any]) -> str:
    return json.dumps(public, sort_keys=True, default=str).lower()


def primary_family(public: dict[str, Any]) -> str:
    rows = required_rows(public) or visible_rows(public)
    if rows:
        return family_from_row(rows[0])
    name = str(public.get("permit_name") or public.get("permit_kind") or "").lower()
    synthetic = {"permit_type": name, "permit_name": name, "kind": name}
    return family_from_row(synthetic)


def row_has_condition(row: dict[str, Any]) -> bool:
    text = " ".join(str(row.get(k) or "") for k in (
        "trigger_condition", "trigger", "condition", "condition_text", "rationale", "customer_guidance", "reason", "notes", "scope_trigger", "verification_note", "required_if",
    )).lower()
    return bool(re.search(r"\b(if|when|where|only if|trigger|address-dependent|confirm|verify whether|provided that|unless)\b", text))


def assert_contract_has_source_basis(contract: dict[str, Any]) -> None:
    evidence = contract.get("evidence") or []
    assert evidence, f"{contract['case_id']} has no source/evidence basis"
    for item in evidence:
        assert item.get("url") and item.get("excerpt"), {"case": contract["case_id"], "bad_evidence": item}


def assert_contract_satisfied(contract: dict[str, Any], public: dict[str, Any]) -> None:
    assert_contract_has_source_basis(contract)
    expected_decision = contract["expected_decision"]
    actual_decision = str(public.get("permit_decision") or "").upper()
    assert actual_decision == expected_decision, {
        "case": contract["case_id"], "expected_decision": expected_decision,
        "actual_decision": actual_decision, "permit_name": public.get("permit_name"),
    }
    if expected_decision == "NOT_REQUIRED":
        assert not required_rows(public), {"case": contract["case_id"], "required_rows": required_rows(public)}
        return
    statuses = families_by_status(public)
    for expected in contract.get("expected_visible_families") or []:
        fam = expected["family"]
        allowed = set(expected["allowed_statuses"])
        actual = statuses.get(fam, set())
        assert actual & allowed, {
            "case": contract["case_id"], "missing_or_wrong_status_family": fam,
            "allowed": sorted(allowed), "actual_statuses": sorted(actual),
            "all_statuses": {k: sorted(v) for k, v in statuses.items()},
            "permit_name": public.get("permit_name"),
        }
        if expected.get("condition_required_when_not_required"):
            for row in visible_rows(public):
                if family_from_row(row) == fam and row_status(row) in {"VERIFY", "CONDITIONAL"}:
                    assert row_has_condition(row), {"case": contract["case_id"], "conditionless_row": row}
    hard_forbidden = set(contract.get("forbidden_required_families") or [])
    assert not (hard_forbidden & required_families(public)), {
        "case": contract["case_id"], "forbidden_required": sorted(hard_forbidden & required_families(public)),
        "required_families": sorted(required_families(public)),
    }
    expected_primary = contract.get("expected_primary_family")
    if expected_primary and expected_decision in {"REQUIRED", "NOT_REQUIRED"}:
        assert primary_family(public) == expected_primary, {
            "case": contract["case_id"], "expected_primary": expected_primary,
            "actual_primary": primary_family(public), "permit_name": public.get("permit_name"),
            "required_families": sorted(required_families(public)),
        }
    forbidden_primary = set(contract.get("forbidden_primary_families") or [])
    if expected_decision in {"REQUIRED", "NOT_REQUIRED"}:
        assert primary_family(public) not in forbidden_primary, {
            "case": contract["case_id"], "forbidden_primary": sorted(forbidden_primary),
            "actual_primary": primary_family(public), "permit_name": public.get("permit_name"),
        }
