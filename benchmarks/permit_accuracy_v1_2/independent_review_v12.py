#!/usr/bin/env python3
"""Independent, offline review of Session 1 benchmark v1.2 artifacts.

Deliberately does not import benchmark_v12.py or any PermitAssist api module.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(condition: bool, name: str, detail: str, checks: list[dict]) -> None:
    checks.append({"check": name, "pass": bool(condition), "detail": detail})


def main() -> int:
    checks: list[dict] = []
    baseline = json.loads((HERE / "SESSION1_OPENING_BASELINE.json").read_text())
    ontology = json.loads((HERE / "permit_family_ontology_v1.json").read_text())
    closure = json.loads((HERE / "ontology_enum_closure.json").read_text())
    audit = json.loads((HERE / "truth_audit_v12.json").read_text())
    summary = json.loads((HERE / "offline_rescore_summary_v12.json").read_text())
    with (HERE / "scoreboard_v12.csv").open(newline="", encoding="utf-8") as handle:
        scoreboard = list(csv.DictReader(handle))
    forensics = [json.loads(line) for line in (HERE / "mismatch_forensics_v12.jsonl").read_text().splitlines()]

    for relative, expected in baseline["authoritative_input_sha256"].items():
        actual = sha(REPO / relative)
        check(actual == expected, f"authoritative_hash:{relative}", f"expected={expected} actual={actual}", checks)
    for relative, expected in baseline["protected_path_sha256"].items():
        actual = sha(REPO / relative)
        check(actual == expected, f"protected_hash:{relative}", f"expected={expected} actual={actual}", checks)

    implementation = (HERE / "benchmark_v12.py").read_text()
    forbidden = ["call_path(", "research_permit(", "import httpx", "import requests", "from api", "import api"]
    present = [token for token in forbidden if token in implementation]
    check(not present, "offline_no_provider_runtime_surface", f"forbidden_tokens_present={present}", checks)

    case_ids = [r["case_id"] for r in audit["cases"]]
    check(len(case_ids) == 100 and len(set(case_ids)) == 100, "truth_audit_100_unique", f"rows={len(case_ids)} unique={len(set(case_ids))}", checks)
    empty = [r for r in audit["cases"] if r["empty_companion_original"]]
    bad_empty = [r["case_id"] for r in empty if r["companion_truth_status"] not in {"confirmed-none", "truth-incomplete"}]
    check(len(empty) == 93 and not bad_empty, "all_93_empty_sets_binary_labeled", f"empty={len(empty)} bad={bad_empty}", checks)
    check(all(r["companion_truth_status"] == "truth-incomplete" for r in empty), "no_empty_assumed_correct", "all 93 empty sets fail closed as truth-incomplete", checks)
    corrections = [r["case_id"] for r in audit["cases"] if r["truth_correction_applied"]]
    check(not corrections, "no_unreviewed_truth_corrections", f"corrections={corrections}", checks)
    bad_source = [r["case_id"] for r in audit["cases"] if not r["official_sources"] or any(not str(s.get("url", "")).startswith(("http://", "https://")) for s in r["official_sources"])]
    check(not bad_source, "truth_rows_bind_official_source_packets", f"bad={bad_source}", checks)

    mapped = [item for items in closure["sources"].values() for item in items]
    families = set(ontology["families"])
    bad_map = [item for item in mapped if item["canonical_family"] not in families]
    check(closure["closure_pass"] and not bad_map, "ontology_enum_closure", f"labels={len(mapped)} bad={len(bad_map)}", checks)
    required_distinctions = {"NO_PRIMARY_PERMIT", "ROOFING", "FIRE_LIFE_SAFETY", "ZONING_PLANNING", "OCCUPANCY_CO", "DEMOLITION", "POOL_SPA", "MOVING", "LANDMARKS_HISTORIC"}
    check(required_distinctions.issubset(families), "ontology_safety_distinctions", f"missing={sorted(required_distinctions-families)}", checks)

    keys = {(r["case_id"], r["path"]) for r in scoreboard}
    check(len(scoreboard) == 500 and len(keys) == 500, "scoreboard_500_unique", f"rows={len(scoreboard)} unique={len(keys)}", checks)
    raw_bad = []
    for row in scoreboard:
        path = REPO / row["raw_reference"]
        if not path.exists() or sha(path) != row["raw_sha256"]:
            raw_bad.append(row["raw_reference"])
    check(not raw_bad, "scoreboard_raw_hash_binding", f"bad={len(raw_bad)}", checks)

    expected_mismatches = set()
    for row in scoreboard:
        if row["decision_exact"] != "1": expected_mismatches.add((row["case_id"], row["path"], "decision"))
        if row["primary_family_exact"] != "1": expected_mismatches.add((row["case_id"], row["path"], "primary"))
        if row["legacy_companion_exact"] != "1": expected_mismatches.add((row["case_id"], row["path"], "companion"))
    actual_mismatches = {(r["case_id"], r["path"], r["dimension"]) for r in forensics}
    check(expected_mismatches == actual_mismatches and len(forensics) == len(actual_mismatches), "forensics_complete_one_to_one", f"expected={len(expected_mismatches)} actual={len(actual_mismatches)} rows={len(forensics)}", checks)
    raw_ref_map = {(r["case_id"], r["path"]): r for r in scoreboard}
    forensic_bad = []
    for record in forensics:
        row = raw_ref_map[(record["case_id"], record["path"])]
        if not record["categories"] or record["raw_reference"] != row["raw_reference"] or record["raw_sha256"] != row["raw_sha256"]:
            forensic_bad.append((record["case_id"], record["path"], record["dimension"]))
    check(not forensic_bad, "forensics_taxonomy_and_evidence_binding", f"bad={len(forensic_bad)}", checks)

    arithmetic_bad = []
    for path, metrics in summary["paths"].items():
        rows = [r for r in scoreboard if r["path"] == path]
        decision_pass = sum(int(r["decision_exact"]) for r in rows)
        primary_pass = sum(int(r["primary_family_exact"]) for r in rows)
        if metrics["decision_accuracy"] != {"pass": decision_pass, "denominator": len(rows)}:
            arithmetic_bad.append((path, "decision"))
        if metrics["primary_family_accuracy"] != {"pass": primary_pass, "denominator": len(rows)}:
            arithmetic_bad.append((path, "primary"))
    check(not arithmetic_bad, "summary_arithmetic_recomputed", f"bad={arithmetic_bad}", checks)
    frozen = summary["frozen_denominators"]
    check(len(frozen["companion_case_ids"]) == 4 and frozen["companion_truth_conditional_items"] == 18 and frozen["companion_truth_required_items"] == 0, "companion_denominator_fail_closed", json.dumps(frozen, sort_keys=True), checks)
    baselines = summary["constant_baselines"]
    check(baselines["constant_REQUIRED_decision"]["pass"] == 95 and baselines["constant_BUILDING_primary"]["pass"] == 96 and baselines["constant_empty_companions_legacy_truth"]["pass"] == 93, "constant_baselines_present", json.dumps(baselines, sort_keys=True), checks)

    failed = [c for c in checks if not c["pass"]]
    report = {
        "schema": "permit_accuracy_v12_independent_review_v1",
        "review_method": "independent deterministic implementation; benchmark_v12.py not imported",
        "checks": checks,
        "check_count": len(checks),
        "failed_count": len(failed),
        "verdict": "PASS" if not failed else "FAIL",
        "findings": {
            "truth": "93/93 empty companion sets are truth-incomplete; no empty set is rewarded as confirmed-none.",
            "measurement": "500 rows and all mismatch dimensions independently reconcile to raw hashes.",
            "ontology": f"{len(mapped)} observed labels close into the canonical enum.",
            "runtime_boundary": "Opening protected-path hashes remain unchanged.",
            "limitation": "Required-companion recall is correctly N/A on the supportable v1.2 denominator (0 REQUIRED items)."
        },
        "category_counts": dict(sorted(Counter(cat for row in forensics for cat in row["categories"]).items())),
    }
    (HERE / "INDEPENDENT_REVIEW_V12.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"verdict": report["verdict"], "checks": len(checks), "failed": len(failed)}, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
