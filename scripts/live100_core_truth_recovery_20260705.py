#!/usr/bin/env python3
from __future__ import annotations

"""Local-only Live100 core-permit-truth recovery gate for 5fe5c20.

This script replays the fresh random 50/50 Live100 artifact through the local
customer boundary, then validates protected core-truth fields case-by-case.
It never calls production, paid APIs, or external networks.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import types
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
DEFAULT_ARTIFACT_ROOT = ROOT / "artifacts" / "live100_random_50_50_customer_pov_5fe5c20_20260705T204831Z"
DEFAULT_FACTCHECK_CSV = DEFAULT_ARTIFACT_ROOT / "final_factcheck_confirmation_fable5" / "FINAL_FACTCHECK_TITI_FABLE5_CONFIRMED_GRADES.csv"
DEFAULT_OUT_ROOT = ROOT / "artifacts" / "live100_core_truth_recovery_20260705"

PROTECTED_FIELDS = (
    "permit_required",
    "permit_decision",
    "permit_verdict",
    "required_families",
    "required_permit_names",
    "authority",
    "apply_url",
    "apply_path",
)

FALSE_NOT_REQUIRED_EXPECTED: dict[str, set[str]] = {
    "R-017": {"building", "electrical", "plumbing"},
    "R-018": {"building", "electrical", "plumbing"},
    "R-039": {"building", "electrical", "plumbing"},
    "R-050": {"building", "electrical", "plumbing"},
    "C-005": {"building", "fire_suppression"},
    "C-018": {"building", "fire_suppression"},
    "C-045": {"building", "fire_suppression"},
    "C-046": {"building", "fire_suppression"},
}

WRONG_AHJ_EXPECTATIONS: dict[str, dict[str, Any]] = {
    "R-025": {"must_contain": ["des moines"], "must_not_contain": ["west des moines", "wdm.iowa.gov"]},
    "C-007": {"must_contain": ["des moines"], "must_not_contain": ["west des moines", "wdm.iowa.gov"]},
    "R-027": {"must_not_contain": ["clark county", "clarkcountynv.gov"], "allow_if_caveat": True},
    "C-033": {"must_contain": ["las vegas"], "must_not_contain": ["clark county", "clarkcountynv.gov", "permit-fee-estimator"]},
    "C-035": {"must_contain": ["cedar rapids"], "must_not_contain": ["linn county", "linncountyiowa.gov"]},
}

NO_CHANGE_USE_OVERREACH = {"C-002", "C-013", "C-028", "C-030", "C-031"}
DEMO_AS_TI = {"C-016", "C-024"}
RESIDENTIAL_FOOD_FOG = {"R-018", "R-024", "R-038", "R-039", "R-050"}
WRONG_EGRESS_SUBTYPE = {"R-014", "R-029"}
UNSUPPORTED_REFRIGERATION = {"R-046"}
SIGN_OVERREACH = {"C-025"}
WATER_HEATER_OVERPRESCRIPTION = {"R-003"}
JERSEY_CITY_ROOF_ORDINARY_MAINTENANCE_RISK = {"R-007"}

BUILDING_BUCKET = {"building", "building_ti", "building_adu", "demolition", "racking"}


def install_runtime_stubs() -> None:
    requests_stub = types.ModuleType("requests")
    setattr(requests_stub, "post", lambda *a, **k: None)
    setattr(requests_stub, "get", lambda *a, **k: None)
    setattr(requests_stub, "head", lambda *a, **k: types.SimpleNamespace(status_code=200))
    setattr(requests_stub, "exceptions", types.SimpleNamespace(Timeout=TimeoutError, RequestException=Exception))
    sys.modules.setdefault("requests", requests_stub)

    openai_stub = types.ModuleType("openai")
    setattr(openai_stub, "OpenAI", lambda *a, **k: object())
    sys.modules.setdefault("openai", openai_stub)

    google_stub = types.ModuleType("google")
    genai_stub = types.ModuleType("google.generativeai")
    setattr(genai_stub, "configure", lambda *a, **k: None)
    sys.modules.setdefault("google", google_stub)
    sys.modules.setdefault("google.generativeai", genai_stub)


def import_server(out_root: Path):
    install_runtime_stubs()
    os.environ.setdefault("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
    os.environ.setdefault("OPENAI_API_KEY", "offline-not-used")
    os.environ["PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD"] = "1"
    os.environ["FREE_LOOKUP_DB"] = str(out_root / "free_lookup.db")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if str(API) not in sys.path:
        sys.path.insert(0, str(API))
    for name in ("server", "api.server"):
        sys.modules.pop(name, None)
    from api import server as server_mod  # noqa: PLC0415

    server_mod.CACHE_DB = str(out_root / "offline_cache.db")
    server_mod.DATA_DIR = str(out_root)
    try:
        server_mod.init_db()
    except Exception:
        pass
    return server_mod


def read_cases(artifact_root: Path) -> list[dict[str, Any]]:
    path = artifact_root / "cases.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_final_grades(csv_path: Path) -> dict[str, dict[str, str]]:
    grades: dict[str, dict[str, str]] = {}
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cid = row.get("case_id") or row.get("CASE_ID") or ""
            if cid:
                grades[cid] = row
    return grades


def family_from_row(row: dict[str, Any]) -> str:
    fam = str(row.get("family") or row.get("filing_family") or "").strip().lower()
    if fam:
        return fam
    text = " ".join(str(row.get(k) or "") for k in ("permit_type", "permit_name", "approval_type", "kind")).lower()
    checks = [
        ("wastewater_pretreatment_fog", r"\b(?:fog|grease|pretreatment|wastewater)\b"),
        ("health_food", r"\b(?:health|food establishment|food service)\b"),
        ("fire_suppression", r"\b(?:fire|sprinkler|suppression|high[- ]piled)\b"),
        ("co_change_of_occupancy", r"\b(?:certificate of occupancy|change[- ]of[- ]occupancy|change[- ]of[- ]use)\b"),
        ("planning_zoning", r"\b(?:planning|zoning|land use)\b"),
        ("refrigeration", r"\b(?:refrigeration|refrigerant)\b"),
        ("mechanical", r"\b(?:mechanical|hvac|heat pump|rtu|rooftop|duct)\b"),
        ("plumbing", r"\b(?:plumbing|sink|water heater|gas|drain)\b"),
        ("electrical", r"\b(?:electrical|circuit|panel|receptacle|lighting)\b"),
        ("sign", r"\bsign\b"),
        ("building", r"\b(?:building|construction|demolition|racking|shed|deck|roof|window)\b"),
    ]
    for fam_name, pattern in checks:
        if re.search(pattern, text, re.I):
            return fam_name
    return ""


def bucket_family(fam: str) -> str:
    return "building" if fam in BUILDING_BUCKET else fam


def required_families(public: dict[str, Any], *, bucket: bool = False) -> list[str]:
    fams = []
    for row in public.get("permits_required") or []:
        if not isinstance(row, dict):
            continue
        fam = family_from_row(row)
        if fam:
            fams.append(bucket_family(fam) if bucket else fam)
    return sorted(dict.fromkeys(fams))


def public_text(public: dict[str, Any]) -> str:
    return json.dumps(public, sort_keys=True, default=str).lower()


def action_text(public: dict[str, Any]) -> str:
    parts = [str(public.get("applying_office") or ""), str(public.get("building_dept_name") or ""), str(public.get("apply_url") or "")]
    ap_raw = public.get("apply_path")
    ap: dict[str, Any] = ap_raw if isinstance(ap_raw, dict) else {}
    parts.extend(str(ap.get(k) or "") for k in ("office_name", "authority", "portal_url", "url"))
    for src in public.get("sources") or []:
        if isinstance(src, dict):
            parts.extend(str(src.get(k) or "") for k in ("url", "title", "label"))
    return "\n".join(parts).lower()


def protected_snapshot(public: dict[str, Any]) -> dict[str, Any]:
    ap_raw = public.get("apply_path")
    ap: dict[str, Any] = ap_raw if isinstance(ap_raw, dict) else {}
    return {
        "permit_required": public.get("permit_required"),
        "permit_decision": str(public.get("permit_decision") or "").upper(),
        "permit_verdict": str(public.get("permit_verdict") or "").upper(),
        "required_families": required_families(public),
        "required_family_buckets": required_families(public, bucket=True),
        "required_permit_names": [str(row.get("permit_name") or row.get("permit_type") or "") for row in public.get("permits_required") or [] if isinstance(row, dict)],
        "authority": public.get("applying_office") or public.get("building_dept_name") or ap.get("office_name") or "",
        "apply_url": public.get("apply_url") or public.get("online_application_url") or ap.get("portal_url") or "",
        "apply_path": {k: ap.get(k) for k in ("state", "status", "typed_status", "channel", "office_name", "portal_url", "authority") if k in ap},
    }


def replay_all(artifact_root: Path, out_root: Path) -> dict[str, dict[str, Any]]:
    server_mod = import_server(out_root)
    rows: dict[str, dict[str, Any]] = {}
    for rec in read_cases(artifact_root):
        case = rec["case"]
        cid = case["id"]
        public = server_mod.build_customer_permit_view_model(
            deepcopy(rec.get("response_body") or {}),
            case.get("job_type") or "",
            case.get("city") or "",
            case.get("state") or "",
            job_category=case.get("segment"),
        )
        rows[cid] = {
            "case": case,
            "public": public,
            "protected": protected_snapshot(public),
            "baseline_protected": protected_snapshot(rec.get("response_body") or {}),
        }
    return rows


def _add_error(errors: list[dict[str, Any]], case_id: str, gate: str, message: str, got: Any = None, expected: Any = None) -> None:
    errors.append({"case_id": case_id, "gate": gate, "message": message, "got": got, "expected": expected})


def validate_rows(rows: dict[str, dict[str, Any]], grades: dict[str, dict[str, str]]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    per_case: dict[str, dict[str, Any]] = {}

    for cid, entry in sorted(rows.items()):
        public = entry["public"]
        prot = entry["protected"]
        grade_row = grades.get(cid, {})
        grade = grade_row.get("confirmed_grade") or grade_row.get("final_grade") or grade_row.get("grade") or ""
        case_errors_start = len(errors)
        fams = set(prot["required_family_buckets"])
        exact_fams = set(prot["required_families"])
        text = public_text(public)
        atext = action_text(public)

        if prot["permit_decision"] == "NOT_REQUIRED" and any(
            token in text for token in ("required permit package", "permit required:", "pull ")
        ):
            _add_error(errors, cid, "decision_copy_parity", "NOT_REQUIRED result contains required-permit customer copy")
        if prot["permit_decision"] == "REQUIRED" and "no permit required" in text:
            _add_error(errors, cid, "decision_copy_parity", "REQUIRED result contains no-permit customer copy")

        if cid in FALSE_NOT_REQUIRED_EXPECTED:
            expected = FALSE_NOT_REQUIRED_EXPECTED[cid]
            if prot["permit_decision"] != "REQUIRED" or public.get("permit_required") is not True:
                _add_error(errors, cid, "false_not_required", "expected REQUIRED for safety-critical permit-triggering scope", prot, "REQUIRED")
            missing = sorted(expected - fams)
            if missing:
                _add_error(errors, cid, "false_not_required_families", "missing required core family buckets", sorted(fams), sorted(expected))

        if cid in WRONG_AHJ_EXPECTATIONS:
            spec = WRONG_AHJ_EXPECTATIONS[cid]
            if spec.get("allow_if_caveat") and any(bad in atext for bad in spec.get("must_not_contain", [])):
                caveat_ok = bool(re.search(r"\b(?:unincorporated|inside city limits|exact address|city-vs-county|city vs county)\b", text, re.I))
                if not caveat_ok:
                    _add_error(errors, cid, "wrong_ahj", "county/wrong-AHJ action path lacks exact-address/city-county caveat", atext, spec)
            else:
                for required in spec.get("must_contain", []):
                    if required not in atext:
                        _add_error(errors, cid, "wrong_ahj", f"action path missing expected authority token {required!r}", atext, spec)
                for forbidden in spec.get("must_not_contain", []):
                    if forbidden in atext:
                        _add_error(errors, cid, "wrong_ahj", f"action path contains forbidden authority/domain token {forbidden!r}", atext, spec)

        if cid in NO_CHANGE_USE_OVERREACH:
            if "fire_suppression" not in exact_fams:
                _add_error(errors, cid, "no_change_use_core", "sprinkler scope lost fire_suppression core family", sorted(exact_fams), "fire_suppression")
            for bad in ("co_change_of_occupancy", "planning_zoning"):
                if bad in exact_fams:
                    _add_error(errors, cid, "no_change_use_overreach", f"no-change-use sprinkler scope hard-requires {bad}", sorted(exact_fams), f"no {bad}")

        if cid in DEMO_AS_TI:
            names = "\n".join(prot["required_permit_names"]).lower()
            if "tenant improvement" in names or "commercial ti" in names:
                _add_error(errors, cid, "demo_as_ti", "pure demo scope is labeled as TI in required permit names", names, "demolition/building only")

        if cid in RESIDENTIAL_FOOD_FOG:
            if re.search(r"\b(?:food establishment|food service|fog|grease interceptor|commercial kitchen)\b", text, re.I):
                _add_error(errors, cid, "residential_commercial_contamination", "residential kitchen output contains commercial food/FOG contamination")

        if cid in WRONG_EGRESS_SUBTYPE:
            names = "\n".join(prot["required_permit_names"]).lower()
            if re.search(r"\begress\b|\bwindow well\b", names, re.I):
                _add_error(errors, cid, "wrong_egress_subtype", "like-for-like window scope is labeled as egress/window-well work", names, "window replacement without egress subtype")

        if cid in UNSUPPORTED_REFRIGERATION and "refrigeration" in exact_fams:
            _add_error(errors, cid, "unsupported_refrigeration", "unsupported standalone refrigeration companion is hard-required", sorted(exact_fams), "mechanical/electrical without refrigeration")

        if cid in SIGN_OVERREACH and "sign" in exact_fams:
            _add_error(errors, cid, "sign_overreach", "sign-prep-only scope hard-requires sign permit", sorted(exact_fams), "no sign family unless actual sign/illumination scope")

        if cid in WATER_HEATER_OVERPRESCRIPTION and "building" in fams:
            _add_error(errors, cid, "water_heater_overprescription", "like-for-like water heater hard-requires standalone building family", sorted(fams), "plumbing/gas only")

        if cid in JERSEY_CITY_ROOF_ORDINARY_MAINTENANCE_RISK and prot["permit_decision"] == "REQUIRED" and "building" in fams:
            if not re.search(r"ordinary[- ]maintenance|ordinary maintenance", text, re.I):
                _add_error(errors, cid, "ordinary_maintenance_overprescription_risk", "Jersey City like-for-like roof remains flat REQUIRED building without source-backed ordinary-maintenance handling", prot, "NOT_REQUIRED or explicit source-backed caveat")

        if grade in {"A", "B"}:
            base = entry["baseline_protected"]
            if base["permit_decision"] in {"REQUIRED", "NOT_REQUIRED"} and prot["permit_decision"] != base["permit_decision"]:
                _add_error(errors, cid, "green_freeze_decision", "A/B case decision changed", prot["permit_decision"], base["permit_decision"])
            if base["permit_decision"] == "REQUIRED":
                missing = sorted(set(base["required_family_buckets"]) - set(prot["required_family_buckets"]))
                if missing:
                    _add_error(errors, cid, "green_freeze_families", "A/B case lost required family bucket(s)", prot["required_family_buckets"], base["required_family_buckets"])
                if base.get("apply_url") and not prot.get("apply_url"):
                    _add_error(errors, cid, "green_freeze_action_path", "A/B case lost nonblank apply URL", prot.get("apply_url"), base.get("apply_url"))

        per_case[cid] = {
            "case_id": cid,
            "grade": grade,
            "segment": entry["case"].get("segment"),
            "city": entry["case"].get("city"),
            "state": entry["case"].get("state"),
            "bucket": entry["case"].get("bucket"),
            "protected": prot,
            "baseline_protected": entry["baseline_protected"],
            "pass": len(errors) == case_errors_start,
            "errors": errors[case_errors_start:],
        }

    false_nr_errors = [e for e in errors if e["gate"].startswith("false_not_required")]
    green_errors = [e for e in errors if e["gate"].startswith("green_freeze")]
    wrong_ahj_errors = [e for e in errors if e["gate"] == "wrong_ahj"]
    contamination_errors = [e for e in errors if e["gate"] == "residential_commercial_contamination"]

    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "pass": not errors,
        "errors": errors,
        "error_count": len(errors),
        "case_count": len(rows),
        "ab_green_freeze_count": sum(1 for row in grades.values() if (row.get("confirmed_grade") or row.get("final_grade") or row.get("grade")) in {"A", "B"}),
        "gate_counts": {
            "false_not_required_errors": len(false_nr_errors),
            "green_freeze_errors": len(green_errors),
            "wrong_ahj_errors": len(wrong_ahj_errors),
            "residential_commercial_contamination_errors": len(contamination_errors),
        },
        "per_case": per_case,
        "protected_fields": PROTECTED_FIELDS,
    }


def write_outputs(report: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "CORE_TRUTH_PROTECTED_FIELDS_SUMMARY.json"
    summary_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    csv_path = out_dir / "CORE_TRUTH_PROTECTED_FIELDS_CASES.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["case_id", "grade", "pass", "decision", "required_families", "authority", "apply_url", "errors"])
        for cid, row in sorted(report["per_case"].items()):
            prot = row["protected"]
            writer.writerow([
                cid,
                row["grade"],
                row["pass"],
                prot["permit_decision"],
                ";".join(prot["required_families"]),
                prot["authority"],
                prot["apply_url"],
                ";".join(f"{e['gate']}:{e['message']}" for e in row["errors"]),
            ])
    (out_dir / "SHA256SUMS.txt").write_text(
        "\n".join(
            f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}"
            for p in (summary_path, csv_path)
        ) + "\n",
        encoding="utf-8",
    )


def secret_scan(out_dir: Path) -> dict[str, Any]:
    patterns = [
        re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
        re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
        re.compile(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{24,}"),
    ]
    hits: list[dict[str, str]] = []
    for path in out_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".json", ".csv", ".txt", ".md"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in patterns:
            if pattern.search(text):
                hits.append({"file": str(path), "pattern": pattern.pattern})
    result = {"secret_scan_hit_count": len(hits), "hits": hits, "pass": not hits}
    (out_dir / "CORE_TRUTH_SECRET_SCAN.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def run_validate(args: argparse.Namespace) -> int:
    out_dir = args.out / args.label
    rows = replay_all(args.artifact_root, out_dir)
    grades = read_final_grades(args.factcheck_csv)
    report = validate_rows(rows, grades)
    report["artifact_root"] = str(args.artifact_root)
    report["factcheck_csv"] = str(args.factcheck_csv)
    report["label"] = args.label
    write_outputs(report, out_dir)
    scan = secret_scan(out_dir)
    report["secret_scan"] = scan
    # Rewrite summary with secret-scan included.
    (out_dir / "CORE_TRUTH_PROTECTED_FIELDS_SUMMARY.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "pass": report["pass"] and scan["pass"],
        "case_count": report["case_count"],
        "error_count": report["error_count"],
        "gate_counts": report["gate_counts"],
        "secret_scan_hit_count": scan["secret_scan_hit_count"],
        "summary": str(out_dir / "CORE_TRUTH_PROTECTED_FIELDS_SUMMARY.json"),
        "cases_csv": str(out_dir / "CORE_TRUTH_PROTECTED_FIELDS_CASES.csv"),
    }, indent=2, sort_keys=True))
    return 0 if report["pass"] and scan["pass"] else 1


def run_determinism(args: argparse.Namespace) -> int:
    labels = [args.label + "_run1", args.label + "_run2"]
    reports = []
    for label in labels:
        ns = argparse.Namespace(**{**vars(args), "label": label})
        rc = run_validate(ns)
        if rc != 0 and not args.allow_contract_failures:
            return rc
        reports.append(json.loads((args.out / label / "CORE_TRUTH_PROTECTED_FIELDS_SUMMARY.json").read_text(encoding="utf-8")))
    def protected_digest(report: dict[str, Any]) -> str:
        comparable = {
            cid: row["protected"] for cid, row in sorted(report["per_case"].items())
        }
        return hashlib.sha256(json.dumps(comparable, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    digest1 = protected_digest(reports[0])
    digest2 = protected_digest(reports[1])
    det = {"determinism_pass": digest1 == digest2, "run1_digest": digest1, "run2_digest": digest2, "labels": labels}
    det_dir = args.out / args.label
    det_dir.mkdir(parents=True, exist_ok=True)
    (det_dir / "CORE_TRUTH_DETERMINISM_REPORT.json").write_text(json.dumps(det, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(det, indent=2, sort_keys=True))
    return 0 if det["determinism_pass"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["validate", "determinism"])
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--factcheck-csv", type=Path, default=DEFAULT_FACTCHECK_CSV)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--label", default="local_validation")
    parser.add_argument("--allow-contract-failures", action="store_true", help="determinism mode only: continue despite validation failures")
    args = parser.parse_args()
    if args.mode == "validate":
        return run_validate(args)
    if args.mode == "determinism":
        return run_determinism(args)
    raise AssertionError(args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
