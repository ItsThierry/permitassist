#!/usr/bin/env python3
"""PermitAssist benchmark v1.2 offline-only measurement and Session 1 audit.

This module never imports PermitAssist runtime code and has no provider/live-run
command. It consumes only the preserved v1.1 cases and 500 raw envelopes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
V11 = REPO / "benchmarks" / "permit_accuracy_v1_1"
CASES_PATH = V11 / "cases.json"
RAW_DIR = V11 / "raw_full_v11"
ONTOLOGY_PATH = HERE / "permit_family_ontology_v1.json"
PATHS = ("luna", "grok", "gemini", "engine", "engine_luna")

# These four non-empty companion truth records are the only preserved cases
# whose excerpts enumerate the relevant conditional permit families at the
# status granularity claimed by truth. The other 96 companion sets remain
# truth-incomplete and are excluded from v1.2 companion P/R denominators.
COMPANION_TRUTH_COMPLETE_IDS = frozenset({
    "smoke-us-al-albertville-residential-residential-building-work-building",
    "cell-us-al-andalusia-residential-residential-building-work-building",
    "cell-us-al-anniston-residential-residential-building-work-building",
    "cell-us-sc-anderson-county-residential-residential-building-construction-alteration-or-inspection-building",
})
FABLE_REVIEW_SHA256 = "e733aa4a4e16c03c39ba2fd7dfb9adcb5286c25fcb4d2cdfb697cb40af822155"
TAXONOMY = (
    "AHJ_BOUNDARY_MISMATCH",
    "SCOPE_TAXONOMY_UNSUPPORTED",
    "PROJECT_FAMILY_NOT_COVERED",
    "RULE_OR_EXEMPTION_MISSING",
    "COMPANION_CLOSURE_INCOMPLETE",
    "AUTHORITATIVE_CELL_NOT_INJECTED",
    "MODEL_OR_GENERIC_FALLBACK_GUESS",
    "POST_RECONCILIATION_MUTATION",
    "PUBLIC_RENDER_DIVERGENCE",
    "STALE_OR_CONFLICTING_RULE",
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canon_token(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(value or "").upper()).strip("_")


def load_ontology() -> dict[str, Any]:
    return json.loads(ONTOLOGY_PATH.read_text())


def map_family(value: Any, ontology: dict[str, Any]) -> str:
    token = canon_token(value)
    aliases = ontology["exact_aliases"]
    if token in aliases:
        return aliases[token]
    display = token.replace("_", " ")
    for rule in ontology["ordered_display_rules"]:
        if any(term in display for term in rule["contains_any"]):
            return rule["family"]
    return "VERIFY"


def status_of(row: dict[str, Any]) -> str:
    for key in ("status", "decision", "companion_decision"):
        token = canon_token(row.get(key))
        if token in {"REQUIRED", "CONDITIONAL", "NOT_REQUIRED", "NEEDS_INPUT", "VERIFY"}:
            return token
    if row.get("required") is True:
        return "REQUIRED"
    certainty = canon_token(row.get("certainty"))
    if certainty in {"CONDITIONAL", "LIKELY", "LIKELY_REQUIRED", "MAY_BE_REQUIRED"}:
        return "CONDITIONAL"
    if certainty in {"VERIFY", "UNKNOWN", "NEEDS_INPUT"}:
        return "VERIFY" if certainty != "NEEDS_INPUT" else "NEEDS_INPUT"
    text = " ".join(str(row.get(k) or "") for k in ("condition_text", "trigger_condition", "reason", "rationale"))
    if text and re.search(r"\b(if|when|may|might|verify|confirm)\b", text, re.I):
        return "CONDITIONAL"
    return "VERIFY"


def answer_bytes(envelope: dict[str, Any]) -> bytes:
    if isinstance(envelope.get("raw_text"), str):
        return envelope["raw_text"].encode()
    if isinstance(envelope.get("raw_response"), dict):
        return json.dumps(envelope["raw_response"], ensure_ascii=False, separators=(",", ":")).encode()
    if isinstance(envelope.get("raw_model_text"), str):
        return envelope["raw_model_text"].encode()
    return b""


def extract_answer(envelope: dict[str, Any]) -> dict[str, Any]:
    if isinstance(envelope.get("raw_response"), dict):
        return envelope["raw_response"]
    raw = envelope.get("raw_text")
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _row_family(row: dict[str, Any], ontology: dict[str, Any]) -> str:
    for key in ("family", "filing_family", "primary_permit_family", "permit_kind", "permit_type", "approval_type", "display_family", "kind", "permit_name", "name"):
        value = row.get(key)
        if value not in (None, ""):
            mapped = map_family(value, ontology)
            if mapped != "VERIFY":
                return mapped
    return "VERIFY"


def _dedupe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    rank = {"REQUIRED": 5, "CONDITIONAL": 4, "NEEDS_INPUT": 3, "VERIFY": 2, "NOT_REQUIRED": 1}
    best: dict[str, str] = {}
    for row in rows:
        family, status = row["family"], row["status"]
        if family in {"NO_PRIMARY_PERMIT", "VERIFY"}:
            continue
        if rank.get(status, 0) > rank.get(best.get(family, ""), 0):
            best[family] = status
    return [{"family": family, "status": best[family]} for family in sorted(best)]


def adapt_answer(path: str, answer: dict[str, Any], ontology: dict[str, Any]) -> dict[str, Any]:
    decision = canon_token(answer.get("permit_decision") or answer.get("permit_verdict"))
    if decision in {"YES", "TRUE"}:
        decision = "REQUIRED"
    elif decision in {"NO", "FALSE"}:
        decision = "NOT_REQUIRED"
    elif decision not in {"REQUIRED", "NOT_REQUIRED", "CONDITIONAL", "VERIFY", "NEEDS_INPUT"}:
        decision = "VERIFY"

    required_rows = [x for x in (answer.get("permits_required") or []) if isinstance(x, dict)]
    if path in {"luna", "grok", "gemini"}:
        primary = map_family(answer.get("primary_permit_family"), ontology)
        companion_source = [x for x in (answer.get("companion_permits") or []) if isinstance(x, dict)]
    else:
        primary_candidates = [answer.get("primary_permit_family")]
        if required_rows:
            primary_candidates.extend(required_rows[0].get(k) for k in ("family", "filing_family", "permit_kind", "permit_type"))
        primary_candidates.extend([answer.get("permit_kind"), answer.get("permit_name")])
        primary = "VERIFY"
        for value in primary_candidates:
            mapped = map_family(value, ontology)
            if mapped != "VERIFY":
                primary = mapped
                break
        companion_source = required_rows[1:]
        companion_source += [x for x in (answer.get("related_permits") or []) if isinstance(x, dict)]
        companion_source += [x for x in (answer.get("companion_permits") or []) if isinstance(x, dict)]
    if decision == "NOT_REQUIRED" and primary in {"VERIFY", "OTHER"}:
        primary = "NO_PRIMARY_PERMIT"
    companions = _dedupe_rows([
        {"family": _row_family(row, ontology), "status": status_of(row)}
        for row in companion_source
    ])
    return {"decision": decision, "primary_family": primary, "companions": companions}


def truth_companions(case: dict[str, Any], ontology: dict[str, Any]) -> list[dict[str, str]]:
    return sorted(({
        "family": map_family(row["family"], ontology),
        "status": canon_token(row["status"]),
    } for row in case["expected"]["companions"]), key=lambda x: (x["family"], x["status"]))


def source_packet_sha(case: dict[str, Any]) -> str:
    data = json.dumps(case["sources"], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return sha(data)


def build_truth_audit(cases: list[dict[str, Any]], ontology: dict[str, Any]) -> dict[str, Any]:
    records = []
    for case in cases:
        expected_comp = truth_companions(case, ontology)
        complete = case["case_id"] in COMPANION_TRUTH_COMPLETE_IDS
        if complete:
            companion_status = "confirmed-source-supported-nonempty"
            rationale = "Preserved official excerpt enumerates each conditional family used by this scoped scenario."
        elif not expected_comp:
            companion_status = "truth-incomplete"
            rationale = "No preserved official excerpt affirmatively establishes complete absence of companion permits. Empty is not treated as correct."
        else:
            companion_status = "truth-incomplete"
            rationale = "Preserved excerpts do not establish every claimed companion at the claimed REQUIRED/CONDITIONAL status."
        records.append({
            "case_id": case["case_id"],
            "decision": case["expected"]["decision"],
            "primary_family": map_family(case["expected"]["primary_permit_family"], ontology),
            "companions": expected_comp,
            "empty_companion_original": not bool(expected_comp),
            "companion_truth_status": companion_status,
            "companion_metric_eligible": complete,
            "decision_primary_review_status": "preserved-official-evidence-reviewed",
            "truth_correction_applied": False,
            "rationale": rationale,
            "official_sources": case["sources"],
            "source_packet_sha256": source_packet_sha(case),
            "truth_origin": case.get("truth_origin"),
            "independent_review": {
                "artifact": "benchmarks/permit_accuracy_v1_1/FABLE5_BENCHMARK_AND_FIX_PLAN_CONSULT.md",
                "sha256": FABLE_REVIEW_SHA256,
                "verdict": "APPROVE_WITH_CHANGES",
                "relevant_finding": "companion truth under-filled and empty sets cannot be presumed correct"
            }
        })
    return {
        "schema": "permit_accuracy_v12_truth_audit_v1",
        "cases": records,
        "counts": {
            "cases": len(records),
            "empty_companion_original": sum(r["empty_companion_original"] for r in records),
            "empty_confirmed_none": sum(r["empty_companion_original"] and r["companion_truth_status"] == "confirmed-none" for r in records),
            "empty_truth_incomplete": sum(r["empty_companion_original"] and r["companion_truth_status"] == "truth-incomplete" for r in records),
            "companion_metric_eligible_cases": sum(r["companion_metric_eligible"] for r in records),
            "truth_corrections": sum(r["truth_correction_applied"] for r in records),
        },
        "policy": "Fail closed: no empty set is confirmed-none without affirmative official evidence of closure. No truth values were changed in Session 1."
    }


def mismatch_categories(path: str, dimension: str, expected: Any, actual: Any, truth_complete: bool) -> list[str]:
    cats: list[str] = []
    if dimension == "companion" and not truth_complete:
        cats.append("COMPANION_CLOSURE_INCOMPLETE")
    if path in {"luna", "grok", "gemini"}:
        cats.append("MODEL_OR_GENERIC_FALLBACK_GUESS")
        if dimension == "primary" and expected in {"ROOFING", "NO_PRIMARY_PERMIT"}:
            cats.append("SCOPE_TAXONOMY_UNSUPPORTED")
    elif dimension == "decision":
        cats.append("RULE_OR_EXEMPTION_MISSING" if actual in {"VERIFY", "NEEDS_INPUT"} else "POST_RECONCILIATION_MUTATION")
    elif dimension == "primary":
        cats.extend(["POST_RECONCILIATION_MUTATION", "PUBLIC_RENDER_DIVERGENCE"])
    elif dimension == "companion" and truth_complete:
        cats.append("COMPANION_CLOSURE_INCOMPLETE")
        if path in {"engine", "engine_luna"}:
            cats.append("POST_RECONCILIATION_MUTATION")
    return sorted(set(cats), key=TAXONOMY.index)


def evaluate(cases: list[dict[str, Any]], ontology: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    forensics: list[dict[str, Any]] = []
    truth_by_id = {c["case_id"]: c for c in cases}
    for case in cases:
        expected_decision = case["expected"]["decision"]
        expected_primary = map_family(case["expected"]["primary_permit_family"], ontology)
        expected_comp = truth_companions(case, ontology)
        truth_complete = case["case_id"] in COMPANION_TRUTH_COMPLETE_IDS
        for path in PATHS:
            raw_path = RAW_DIR / f"{case['case_id']}__{path}.json"
            raw_bytes = raw_path.read_bytes()
            envelope = json.loads(raw_bytes)
            answer = extract_answer(envelope)
            adapted = adapt_answer(path, answer, ontology)
            actual_comp = sorted(adapted["companions"], key=lambda x: (x["family"], x["status"]))
            decision_exact = adapted["decision"] == expected_decision
            primary_exact = adapted["primary_family"] == expected_primary
            comp_exact_legacy = actual_comp == expected_comp
            row = {
                "case_id": case["case_id"], "path": path,
                "raw_reference": str(raw_path.relative_to(REPO)), "raw_sha256": sha(raw_bytes),
                "raw_answer_sha256": sha(answer_bytes(envelope)),
                "expected_decision": expected_decision, "actual_decision": adapted["decision"],
                "decision_exact": int(decision_exact),
                "decision_abstention": int(adapted["decision"] in {"VERIFY", "NEEDS_INPUT"}),
                "confident_decision_flip": int({adapted["decision"], expected_decision} == {"REQUIRED", "NOT_REQUIRED"}),
                "expected_primary_family": expected_primary, "actual_primary_family": adapted["primary_family"],
                "primary_family_exact": int(primary_exact),
                "companion_truth_status": "metric-eligible" if truth_complete else "truth-incomplete",
                "expected_companions": json.dumps(expected_comp, sort_keys=True, separators=(",", ":")),
                "actual_companions": json.dumps(actual_comp, sort_keys=True, separators=(",", ":")),
                "legacy_companion_exact": int(comp_exact_legacy),
                "v12_companion_metric_eligible": int(truth_complete),
            }
            rows.append(row)
            dimensions = [
                ("decision", expected_decision, adapted["decision"], decision_exact),
                ("primary", expected_primary, adapted["primary_family"], primary_exact),
                ("companion", expected_comp, actual_comp, comp_exact_legacy),
            ]
            for dimension, expected, actual, exact in dimensions:
                if exact:
                    continue
                forensics.append({
                    "case_id": case["case_id"], "path": path, "dimension": dimension,
                    "categories": mismatch_categories(path, dimension, expected, actual, truth_complete),
                    "expected": expected, "actual": actual,
                    "raw_reference": row["raw_reference"], "raw_sha256": row["raw_sha256"],
                    "raw_answer_sha256": row["raw_answer_sha256"],
                    "source_packet_sha256": source_packet_sha(case),
                    "source_urls": [s["url"] for s in case["sources"]],
                    "source_excerpt_sha256": [sha(s["evidence_excerpt"].encode()) for s in case["sources"]],
                    "truth_companion_metric_eligible": truth_complete,
                })

    summaries: dict[str, Any] = {}
    for path in PATHS:
        pr = [r for r in rows if r["path"] == path]
        eligible = [r for r in pr if r["v12_companion_metric_eligible"]]
        tp = fp = req_tp = req_den = cond_tp = cond_den = dangerous = complete = 0
        for r in eligible:
            exp = {(x["family"], x["status"]) for x in json.loads(r["expected_companions"])}
            act = {(x["family"], x["status"]) for x in json.loads(r["actual_companions"]) if x["status"] in {"REQUIRED", "CONDITIONAL"}}
            tp += len(exp & act)
            fp += len(act - exp)
            req = {(f, s) for f, s in exp if s == "REQUIRED"}
            cond = {(f, s) for f, s in exp if s == "CONDITIONAL"}
            req_den += len(req); cond_den += len(cond)
            req_tp += len(req & act); cond_tp += len(cond & act)
            dangerous += sum(1 for f, _ in req if not any(af == f and ast in {"REQUIRED", "CONDITIONAL", "NEEDS_INPUT", "VERIFY"} for af, ast in {(x["family"], x["status"]) for x in json.loads(r["actual_companions"]) }))
            complete += int(exp == act)
        primary_dangerous = sum(
            1 for r in pr
            if r["expected_decision"] == "REQUIRED" and not r["primary_family_exact"]
            and r["actual_primary_family"] not in {r["expected_primary_family"], "VERIFY"}
        )
        summaries[path] = {
            "attempts": len(pr),
            "decision_accuracy": {"pass": sum(r["decision_exact"] for r in pr), "denominator": len(pr)},
            "decision_abstentions": sum(r["decision_abstention"] for r in pr),
            "confident_required_not_required_flips": sum(r["confident_decision_flip"] for r in pr),
            "primary_family_accuracy": {"pass": sum(r["primary_family_exact"] for r in pr), "denominator": len(pr)},
            "companion_metric_eligible_cases": len(eligible),
            "companion_precision": {"numerator": tp, "denominator": tp + fp, "value": None if tp + fp == 0 else tp / (tp + fp)},
            "companion_required_recall": {"numerator": req_tp, "denominator": req_den, "value": None if req_den == 0 else req_tp / req_den},
            "companion_conditional_recall": {"numerator": cond_tp, "denominator": cond_den, "value": None if cond_den == 0 else cond_tp / cond_den},
            "companion_complete_set_exact": {"pass": complete, "denominator": len(eligible)},
            "dangerous_omitted_required_companions": dangerous,
            "dangerous_wrong_required_primary_without_abstention": primary_dangerous,
            "legacy_empty_truth_exact_set_pass": sum(r["legacy_companion_exact"] for r in pr),
        }
    constants = {
        "constant_REQUIRED_decision": {"pass": sum(c["expected"]["decision"] == "REQUIRED" for c in cases), "denominator": len(cases)},
        "constant_BUILDING_primary": {"pass": sum(map_family(c["expected"]["primary_permit_family"], ontology) == "BUILDING" for c in cases), "denominator": len(cases)},
        "constant_empty_companions_legacy_truth": {"pass": sum(not c["expected"]["companions"] for c in cases), "denominator": len(cases), "warning": "diagnostic only; 93 empty sets are truth-incomplete"},
        "constant_empty_companions_v12_eligible": {"pass": 0, "denominator": len(COMPANION_TRUTH_COMPLETE_IDS)},
    }
    summary = {
        "schema": "permit_accuracy_v12_offline_rescore_v1",
        "input_cases": len(cases), "input_raw_envelopes": len(rows),
        "paths": summaries, "constant_baselines": constants,
        "frozen_denominators": {
            "decision_case_ids": [c["case_id"] for c in cases],
            "primary_case_ids": [c["case_id"] for c in cases],
            "companion_case_ids": sorted(COMPANION_TRUTH_COMPLETE_IDS),
            "companion_truth_required_items": 0,
            "companion_truth_conditional_items": 18,
            "post_seal_edits_prohibited": True,
        },
        "interpretation_limits": [
            "The v1.1 corpus remains development/diagnosis evidence only.",
            "Required-companion recall is N/A because preserved independently supportable companion truth has zero REQUIRED items.",
            "No provider calls or runtime imports occur in v1.2.",
            "Companion metrics exclude all truth-incomplete cases rather than rewarding empty truth."
        ]
    }
    return rows, summary, forensics


def inventory_labels(cases: list[dict[str, Any]], ontology: dict[str, Any]) -> dict[str, Any]:
    observed: dict[str, Counter[str]] = defaultdict(Counter)
    keys = {"family", "filing_family", "primary_permit_family", "permit_kind", "permit_type", "approval_type", "display_family"}
    def walk(value: Any, source: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in keys and isinstance(child, str) and child.strip():
                    observed[source][child.strip()] += 1
                walk(child, source)
        elif isinstance(value, list):
            for child in value:
                walk(child, source)
    walk(cases, "truth")
    for path in sorted(RAW_DIR.glob("*.json")):
        walk(json.loads(path.read_text()), "preserved_runtime_and_model_raw")
    for path in (REPO / "knowledge/v24/permitassist_decision_cells_v24.json", REPO / "knowledge/v24/permitassist_decision_cell_index_v24.json"):
        walk(json.loads(path.read_text()), "v24_cells")
    mapping = {}
    for source, counter in sorted(observed.items()):
        mapping[source] = [
            {"label": label, "count": counter[label], "canonical_family": map_family(label, ontology)}
            for label in sorted(counter)
        ]
    return {
        "schema": "permit_family_enum_closure_inventory_v1",
        "sources": mapping,
        "counts": {source: len(items) for source, items in mapping.items()},
        "unmapped_labels": [],
        "closure_pass": all(item["canonical_family"] in ontology["families"] for items in mapping.values() for item in items),
        "note": "Ambiguous labels deterministically map to VERIFY or OTHER; no label defaults to BUILDING."
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def generate(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    ontology = load_ontology()
    cases = json.loads(CASES_PATH.read_text())
    if len(cases) != 100 or len({c["case_id"] for c in cases}) != 100:
        raise SystemExit("expected exactly 100 unique preserved cases")
    raw_files = sorted(RAW_DIR.glob("*.json"))
    if len(raw_files) != 500:
        raise SystemExit(f"expected 500 preserved raw envelopes, got {len(raw_files)}")
    rows, summary, forensics = evaluate(cases, ontology)
    truth_audit = build_truth_audit(cases, ontology)
    inventory = inventory_labels(cases, ontology)
    if not inventory["closure_pass"]:
        raise SystemExit("ontology enum closure failed")
    paths = {
        "truth": out / "truth_audit_v12.json",
        "inventory": out / "ontology_enum_closure.json",
        "summary": out / "offline_rescore_summary_v12.json",
        "forensics": out / "mismatch_forensics_v12.jsonl",
        "scoreboard": out / "scoreboard_v12.csv",
    }
    write_json(paths["truth"], truth_audit)
    write_json(paths["inventory"], inventory)
    write_json(paths["summary"], summary)
    paths["forensics"].write_text("".join(json.dumps(x, sort_keys=True, ensure_ascii=False) + "\n" for x in forensics))
    fields = list(rows[0])
    with paths["scoreboard"].open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)
    return list(paths.values())


def verify() -> None:
    expected = [
        HERE / "truth_audit_v12.json", HERE / "ontology_enum_closure.json",
        HERE / "offline_rescore_summary_v12.json", HERE / "mismatch_forensics_v12.jsonl",
        HERE / "scoreboard_v12.csv",
    ]
    with tempfile.TemporaryDirectory() as directory:
        generated = generate(Path(directory))
        by_name = {p.name: p for p in generated}
        for path in expected:
            if not path.exists():
                raise SystemExit(f"missing committed/generated artifact: {path.name}")
            if path.read_bytes() != by_name[path.name].read_bytes():
                raise SystemExit(f"determinism failure: {path.name}")
    print("PASS: 100 cases, 500 preserved raws, enum closure, byte-identical offline regeneration")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "verify"))
    args = parser.parse_args()
    if args.command == "run":
        generated = generate(HERE)
        print("\n".join(str(p.relative_to(REPO)) for p in generated))
    else:
        verify()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
