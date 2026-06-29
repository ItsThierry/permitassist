from __future__ import annotations

import copy
import csv
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))
os.environ.setdefault("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

PRIOR_ROOT = ROOT / "artifacts" / "live_customer_100_50_50_20260629T120408Z"
REAL_ROOT = ROOT / "artifacts" / "live_customer_100_real_customer_50_50_20260629T160333Z"
CONTRACT_PATH = ROOT / "tests" / "fixtures" / "universal_customer_view_contracts_20260629.json"
OLD_CONTRACT_PATH = ROOT / "tests" / "fixtures" / "live_customer_100_50_50_fix_contracts_20260629.json"


def server_module():
    import server  # noqa: WPS433
    return server


def load_universal_fixture() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text())


def _root_for_id(root_id: str) -> Path:
    if root_id == "prior_50_50_120408":
        return PRIOR_ROOT
    if root_id == "real_customer_50_50_160333":
        return REAL_ROOT
    raise KeyError(root_id)


def _case_from_prior(root: Path, case_id: str) -> dict[str, Any]:
    for line in (root / "cases.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        case = obj.get("case") or {}
        if case.get("id") == case_id:
            return {
                "case_id": case_id,
                "segment": case.get("segment"),
                "bucket": case.get("bucket"),
                "city": case.get("city"),
                "state": case.get("state"),
                "scope": case.get("job_type"),
                "response_body": obj.get("response_body") or _read_response_body(Path(case["artifact_json_path"])),
                "artifact_response_json_path": case.get("artifact_json_path"),
                "artifact_report_text_path": case.get("report_text_path"),
                "artifact_visible_text_path": case.get("visible_text_path"),
                "root_id": "prior_50_50_120408",
            }
    raise KeyError((root, case_id))


def _case_from_real(root: Path, case_id: str) -> dict[str, Any]:
    for line in (root / "cases_sanitized.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if obj.get("case_id") == case_id:
            return {
                "case_id": case_id,
                "segment": obj.get("segment"),
                "bucket": obj.get("bucket"),
                "city": obj.get("city"),
                "state": obj.get("state"),
                "scope": obj.get("scope"),
                "response_body": obj.get("response_body") or {},
                "artifact_response_json_path": str(root / "cases_sanitized.jsonl"),
                "artifact_report_text_path": None,
                "artifact_visible_text_path": None,
                "root_id": "real_customer_50_50_160333",
            }
    raise KeyError((root, case_id))


def _read_response_body(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text())
    body = raw.get("response_body") or raw.get("body") or raw
    assert isinstance(body, dict), path
    return body


def load_case(root_id: str, case_id: str) -> dict[str, Any]:
    root = _root_for_id(root_id)
    if root_id == "prior_50_50_120408":
        return _case_from_prior(root, case_id)
    return _case_from_real(root, case_id)


def load_all_cases(root_id: str) -> list[dict[str, Any]]:
    root = _root_for_id(root_id)
    if root_id == "prior_50_50_120408":
        out = []
        for line in (root / "cases.jsonl").read_text().splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            case = obj.get("case") or {}
            out.append(load_case(root_id, case["id"]))
        return out
    return [load_case(root_id, json.loads(line)["case_id"]) for line in (root / "cases_sanitized.jsonl").read_text().splitlines() if line.strip()]


def final_grades(root_id: str) -> dict[str, str]:
    root = _root_for_id(root_id)
    rows = list(csv.DictReader((root / "FINAL_TITI_OPUS_GRADES.csv").open()))
    return {row["case_id"]: (row.get("final_grade") or row.get("grade") or "").strip().upper() for row in rows}


def non_a_case_ids(root_id: str) -> set[str]:
    return {case_id for case_id, grade in final_grades(root_id).items() if grade and grade != "A"}


def build_public(case: dict[str, Any], source: dict[str, Any] | None = None) -> dict[str, Any]:
    server = server_module()
    return server.build_customer_permit_view_model(
        copy.deepcopy(source if source is not None else case["response_body"]),
        case.get("scope") or "",
        case.get("city") or "",
        case.get("state") or "",
        job_category=case.get("segment"),
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
    server = server_module()
    for func_name in ("_pa20_row_family", "_customer_row_family"):
        func = getattr(server, func_name, None)
        if callable(func):
            fam = str(func(row) or "").lower()
            if fam:
                aliases = {
                    "building_ti": "building", "building_adu": "building", "fire_suppression": "fire",
                    "health_food_establishment": "health", "wastewater_pretreatment_fog": "wastewater",
                    "co_change_of_occupancy": "co", "planning_zoning": "planning", "historic_review": "historic",
                    "right_of_way": "grading", "site_civil": "grading",
                }
                return aliases.get(fam, fam)
    text = " ".join(str(row.get(k) or "") for k in (
        "filing_family", "family", "display_family", "kind", "category", "permit_kind",
        "permit_type", "permit_name", "approval_type", "portal_selection",
    )).lower()
    if re.search(r"\b(grade|grading|sitework|site work|drainage|land disturbance|civil|right[- ]of[- ]way|encroachment)\b", text):
        return "grading"
    if "foundation" in text:
        return "foundation"
    return "other"


def visible_rows(public: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
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


def assert_no_required_no_permit_contradiction(case: dict[str, Any], public: dict[str, Any]) -> None:
    text = public_text(public)
    decision = str(public.get("permit_decision") or "").upper()
    if decision == "REQUIRED" or public.get("permit_required") is True:
        assert not re.search(r"\bno permit required\b|\bno permit submission needed\b", text), {"case": case["case_id"], "decision": decision, "permit_name": public.get("permit_name")}
        assert required_rows(public), {"case": case["case_id"], "permit_name": public.get("permit_name")}
    if decision == "NOT_REQUIRED" or public.get("permit_required") is False:
        assert not required_rows(public), {"case": case["case_id"], "required_rows": required_rows(public)}
        assert "file the required permit" not in text, case["case_id"]


def assert_no_collapsed_package(public: dict[str, Any], case_id: str) -> None:
    collapse_re = re.compile(r"^\s*multiple permits required\s*:", re.I)
    fields = {
        "permit_name": public.get("permit_name"),
        "permit_type": public.get("permit_type"),
    }
    ap = public.get("apply_path") if isinstance(public.get("apply_path"), dict) else {}
    fields["apply_path.permit_type"] = ap.get("permit_type")
    for field, value in fields.items():
        assert not (isinstance(value, str) and collapse_re.search(value)), {"case": case_id, "field": field, "value": value}
    for row in visible_rows(public):
        for key in ("permit_type", "permit_name", "approval_type", "portal_selection"):
            value = row.get(key)
            assert not (isinstance(value, str) and collapse_re.search(value)), {"case": case_id, "row": row, "key": key}


def assert_row_coherence(public: dict[str, Any], case_id: str) -> None:
    for row in visible_rows(public):
        fam = family_from_row(row)
        kind = str(row.get("kind") or row.get("display_family") or "").lower()
        name = " ".join(str(row.get(k) or "") for k in ("permit_type", "permit_name", "approval_type", "portal_selection")).lower()
        checklist = json.dumps(row.get("checklist") or row.get("documents") or row.get("requirements") or [], default=str).lower()
        if fam in {"building", "electrical", "plumbing", "mechanical", "sign", "fire", "zoning", "planning", "co", "health", "grading", "liquor"}:
            assert fam in kind or kind in {"", "other"} or (fam == "co" and "occupancy" in kind) or (fam == "grading" and any(t in kind for t in ("site", "civil", "right", "grading"))), {"case": case_id, "family": fam, "kind": kind, "row": row}
        wrong_cues = {
            "building": ("electrical checklist", "wire", "circuit"),
            "electrical": ("plumbing checklist", "water line", "sewer"),
            "plumbing": ("electrical checklist", "breaker", "panel"),
            "liquor": ("building permit", "tenant improvement"),
        }
        for cue in wrong_cues.get(fam, ()):  # only hard catch obvious cross-family leaks
            assert cue not in checklist, {"case": case_id, "family": fam, "cue": cue, "row": row}
        if fam == "liquor":
            assert "liquor" in name or "alcohol" in name, {"case": case_id, "family": fam, "name": name, "row": row}


def assert_basic_public_invariants(case: dict[str, Any], public: dict[str, Any]) -> None:
    assert public.get("permit_decision") in {"REQUIRED", "NOT_REQUIRED"}, {"case": case["case_id"], "decision": public.get("permit_decision")}
    assert public.get("permit_required") in {True, False}, {"case": case["case_id"], "permit_required": public.get("permit_required")}
    assert_no_required_no_permit_contradiction(case, public)
    assert_row_coherence(public, case["case_id"])
    assert_no_collapsed_package(public, case["case_id"])


def assert_contract_satisfied(contract: dict[str, Any], public: dict[str, Any]) -> None:
    case_id = contract["case_id"]
    decision = str(public.get("permit_decision") or "").upper()
    forbidden_decisions = set(contract.get("forbidden_decisions") or [])
    assert decision not in forbidden_decisions, {"case": case_id, "decision": decision, "forbidden": sorted(forbidden_decisions), "permit_name": public.get("permit_name")}
    expected_decision = contract.get("expected_decision")
    if expected_decision:
        assert decision == expected_decision, {"case": case_id, "expected_decision": expected_decision, "actual_decision": decision, "permit_name": public.get("permit_name")}
    statuses = families_by_status(public)
    for expected in contract.get("expected_visible_families") or []:
        fam = expected["family"]
        allowed = set(expected.get("allowed_statuses") or ["REQUIRED", "VERIFY", "CONDITIONAL"])
        actual = statuses.get(fam, set())
        assert actual & allowed, {"case": case_id, "missing_or_wrong_status_family": fam, "allowed": sorted(allowed), "actual_statuses": sorted(actual), "all_statuses": {k: sorted(v) for k, v in statuses.items()}, "permit_name": public.get("permit_name")}
    forbidden_required = set(contract.get("forbidden_required_families") or [])
    assert not (forbidden_required & required_families(public)), {"case": case_id, "forbidden_required": sorted(forbidden_required & required_families(public)), "required_families": sorted(required_families(public))}
    expected_primary = contract.get("expected_primary_family")
    if expected_primary:
        rows = required_rows(public)
        assert rows, {"case": case_id, "expected_primary": expected_primary, "permit_name": public.get("permit_name")}
        actual_primary = family_from_row(rows[0])
        assert actual_primary == expected_primary, {"case": case_id, "expected_primary": expected_primary, "actual_primary": actual_primary, "required_families": sorted(required_families(public)), "permit_name": public.get("permit_name")}
    if contract.get("must_not_collapse"):
        assert_no_collapsed_package(public, case_id)
    min_visible_rows = contract.get("min_visible_rows")
    if min_visible_rows is not None:
        assert len(visible_rows(public)) >= int(min_visible_rows), {"case": case_id, "min_visible_rows": min_visible_rows, "visible_rows": visible_rows(public)}
    min_sources = contract.get("min_source_urls")
    if min_sources is not None and str(public.get("permit_decision") or "").upper() == "REQUIRED" and required_rows(public):
        urls = public.get("source_urls") if isinstance(public.get("source_urls"), list) else []
        assert len([u for u in urls if u]) >= int(min_sources), {"case": case_id, "source_urls": urls, "permit_name": public.get("permit_name")}
    forbidden_source_patterns = [re.compile(p, re.I) for p in contract.get("forbidden_source_patterns") or []]
    if forbidden_source_patterns:
        blob = json.dumps({"sources": public.get("sources"), "source_urls": public.get("source_urls"), "apply_path": public.get("apply_path"), "apply_url": public.get("apply_url"), "online_application_url": public.get("online_application_url")}, default=str)
        for pat in forbidden_source_patterns:
            assert not pat.search(blob), {"case": case_id, "pattern": pat.pattern, "source_blob": blob[:1000]}


def load_contracts() -> list[dict[str, Any]]:
    return list(load_universal_fixture()["contracts"])


def load_no_neuter_anchors() -> list[dict[str, Any]]:
    return list(load_universal_fixture()["no_neuter_anchors"])


def load_root_cases_for_ids(root_id: str, ids: set[str]) -> list[dict[str, Any]]:
    return [load_case(root_id, case_id) for case_id in sorted(ids)]
