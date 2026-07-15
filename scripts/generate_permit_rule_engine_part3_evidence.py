#!/usr/bin/env python3
"""Generate deterministic Part 3 Permit Rule Engine production-candidate evidence."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(DEFAULT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(DEFAULT_REPO_ROOT))

from api import permit_rule_engine as pre
from api.v24_decision_cells import V24Resolution, V24ResolutionStatus, load_v24_index

SCHEMA_VERSION = "permitassist.rule-engine-part3-evidence.v1"
PROJECT_SCOPE = {
    "commercial_tenant_improvement": ("commercial tenant improvement", "commercial"),
    "residential_remodel": ("residential remodel", "residential"),
    "reroof": ("residential reroof", "residential"),
}


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.write_bytes(canonical_bytes(value))


def write_jsonl(path: Path, rows: list[Any]) -> None:
    path.write_bytes(b"".join(canonical_bytes(row) for row in rows))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolution(key: str, cell: Mapping[str, Any]) -> V24Resolution:
    status = (
        V24ResolutionStatus.EXACT_CELL_FAIL_CLOSED
        if cell.get("status") == "FAIL_CLOSED" or cell.get("serving_status") == "FAIL_CLOSED"
        else V24ResolutionStatus.EXACT_CELL_PUBLISHABLE
    )
    return V24Resolution(
        status=status,
        key=key,
        cell=copy.deepcopy(dict(cell)),
        reason="part3 deterministic evidence replay",
    )


def scope(cell: Mapping[str, Any]) -> tuple[str, str]:
    return PROJECT_SCOPE[str(cell["project_family"])]


def public_projection(key: str, cell: Mapping[str, Any]) -> dict[str, Any]:
    job_type, category = scope(cell)
    envelope = pre.build_core_decision_envelope(
        resolution(key, cell),
        job_type=job_type,
        city=str(cell["ahj"]),
        state=str(cell["state"]),
        job_category=category,
    )
    return json.loads(envelope.sealed_projection.payload_json)


def source_family_verdicts(cell: Mapping[str, Any]) -> dict[str, str]:
    output: dict[str, str] = {}
    for row in cell.get("tier1", {}).get("permits_required", []):
        family = pre.normalize_exact_source_family(row.get("permit_kind"))
        verdict = str(row.get("required_status") or row.get("decision") or "").strip().upper()
        if family and verdict in {"REQUIRED", "NOT_REQUIRED"}:
            output[family] = verdict
    return output


def ambiguous_identity_names(index: Mapping[str, Any]) -> set[tuple[str, str]]:
    ids: dict[tuple[str, str], set[str]] = {}
    for cell in index.values():
        pair = (str(cell.get("state", "")).upper(), str(cell.get("ahj", "")).casefold())
        ids.setdefault(pair, set()).add(str(cell.get("jurisdiction_id", "")).lower())
    return {pair for pair, values in ids.items() if len(values) > 1}


def canonical_alias_reconciliation_names(
    index: Mapping[str, Any],
) -> set[tuple[str, str]]:
    """Return raw-ambiguous AHJ names whose IDs differ only by separators."""
    raw_ids: dict[tuple[str, str], set[str]] = {}
    canonical_ids: dict[tuple[str, str], set[str]] = {}
    for cell in index.values():
        pair = (
            str(cell.get("state", "")).upper(),
            str(cell.get("ahj", "")).casefold(),
        )
        raw_id = str(cell.get("jurisdiction_id", "")).strip().lower()
        canonical_id = pre._canonical_jurisdiction_id(raw_id)
        if raw_id:
            raw_ids.setdefault(pair, set()).add(raw_id)
        if canonical_id:
            canonical_ids.setdefault(pair, set()).add(canonical_id)
    return {
        pair
        for pair, values in raw_ids.items()
        if len(values) > 1 and len(canonical_ids.get(pair, set())) == 1
    }


def generate(repo_root: Path, output_dir: Path, source_commit: str) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    index = load_v24_index() or {}
    contract_path = repo_root / "tests/fixtures/permit_rule_engine_part3_red_no_neuter.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    canary_keys = [row["index_key"] for row in contract["authority_scope_canaries"]]
    canary_keys.append(contract["jurisdiction_hold_canary"]["index_key"])
    canary_key_set = set(canary_keys)
    seeds = pre.migrate_v24_seed_index(index=index)

    seed_rows = [pre.to_primitive(seeds[key]) for key in sorted(seeds)]
    reverify_rows: list[dict[str, Any]] = []
    for key in sorted(index):
        verification = pre.reverify_migrated_seed(seeds[key], index[key])
        reverify_rows.append({"source_index_key": key, **pre.to_primitive(verification)})

    classification_counts = Counter(seed.classification.value for seed in seeds.values())
    family_occurrences = Counter(family for seed in seeds.values() for family in seed.source_families)
    binary_occurrences = Counter(family for seed in seeds.values() for family in seed.binary_families)
    ambiguous_names = ambiguous_identity_names(index)
    canonical_alias_names = canonical_alias_reconciliation_names(index)

    customer_violations: list[dict[str, Any]] = []
    canonical_alias_reconciliations: list[dict[str, Any]] = []
    unexpected_projection_transitions: list[dict[str, Any]] = []
    projection_rows: list[dict[str, Any]] = []
    verified_partial_actionable = 0
    verified_partial_with_tasks = 0
    w4_checked = 0
    for key in sorted(index):
        cell = index[key]
        seed = seeds[key]
        payload = public_projection(key, cell)
        family_rows = {row["family"]: row for row in payload["family_decisions"]}
        issues: list[str] = []
        identity_name = (
            str(cell.get("state", "")).upper(),
            str(cell.get("ahj", "")).casefold(),
        )
        canonical_alias_reconciliation = (
            identity_name in canonical_alias_names
            and seed.classification is pre.SeedClassification.JURISDICTION_HOLD
            and payload["seed_classification"]
            in {
                pre.SeedClassification.EXACT_COMPLETE.value,
                pre.SeedClassification.EXACT_PARTIAL.value,
            }
        )
        if payload["seed_classification"] != seed.classification.value:
            transition = {
                "source_index_key": key,
                "raw_seed_classification": seed.classification.value,
                "customer_projection_classification": payload[
                    "seed_classification"
                ],
            }
            if canonical_alias_reconciliation:
                canonical_alias_reconciliations.append(
                    {
                        **transition,
                        "canonical_jurisdiction_id": pre._canonical_jurisdiction_id(
                            cell.get("jurisdiction_id")
                        ),
                    }
                )
            else:
                issues.append("seed_classification_mismatch")
                unexpected_projection_transitions.append(transition)
        for family, verdict in source_family_verdicts(cell).items():
            if canonical_alias_reconciliation or seed.classification in {
                pre.SeedClassification.EXACT_COMPLETE,
                pre.SeedClassification.EXACT_PARTIAL,
            }:
                if family not in family_rows:
                    issues.append(f"missing_source_family:{family}")
                elif family_rows[family]["verdict"] != verdict:
                    issues.append(f"source_family_verdict_changed:{family}")
        if seed.classification in {
            pre.SeedClassification.FAIL_CLOSED,
            pre.SeedClassification.JURISDICTION_HOLD,
            pre.SeedClassification.UNSUPPORTED_SCOPE,
        } and not canonical_alias_reconciliation:
            if payload["permit_decision"] != "UNKNOWN" or payload["permit_required"] is not None:
                issues.append("unsafe_top_line_binary")
            if any(row["verdict"] in {"REQUIRED", "NOT_REQUIRED"} for row in payload["family_decisions"]):
                issues.append("unsafe_family_binary")
            if payload["sources"] or payload["claim_citations"] or payload["family_authority_routes"]:
                issues.append("fail_closed_claim_or_route_leak")
        if seed.classification is pre.SeedClassification.EXACT_PARTIAL:
            if payload["customer_next_step"].startswith("Verify"):
                verified_partial_actionable += 1
            if payload["verification_tasks"]:
                verified_partial_with_tasks += 1
        if seed.project_family == "commercial_tenant_improvement" and seed.classification is pre.SeedClassification.EXACT_PARTIAL:
            w4_checked += 1
            missing = sorted(set(contract["w4_ten_lane_families"]) - set(family_rows))
            if missing:
                issues.append("w4_lane_neutering:" + ",".join(missing))
        public_serialized = json.dumps(payload, sort_keys=True)
        for forbidden in ("snapshot_path", "publishable", "authority_tier", "handled_by_local_ahj"):
            if forbidden in public_serialized:
                issues.append(f"public_internal_metadata:{forbidden}")
        if "https://www.google.com/maps\"" in public_serialized:
            issues.append("bare_maps_destination")
        if issues:
            customer_violations.append({"source_index_key": key, "issues": sorted(set(issues))})
        if key in canary_key_set:
            projection_rows.append({"source_index_key": key, "projection": payload})

    canary_rows: list[dict[str, Any]] = []
    for key in canary_keys:
        seed = seeds[key]
        overlay = pre.build_ahj_overlay(index[key])
        promotion_ok, promotion_issues = pre._overlay_promotion_gate(seed, index[key])
        candidate = pre.build_fail_closed_factory_seed(
            jurisdiction_id=seed.jurisdiction_id,
            ahj_name=seed.ahj_name,
            state=seed.state,
            project_family=seed.project_family,
            source_index_key=key,
        )
        promoted = pre.promote_factory_seed(candidate, source_cell=index[key])
        final_promotion_accepted = promoted.classification in {
            pre.SeedClassification.EXACT_COMPLETE,
            pre.SeedClassification.EXACT_PARTIAL,
        }
        expected_promotion_accepted = seed.classification in {
            pre.SeedClassification.EXACT_COMPLETE,
            pre.SeedClassification.EXACT_PARTIAL,
        }
        canary_rows.append(
            {
                "source_index_key": key,
                "classification": seed.classification.value,
                "source_families": list(seed.source_families),
                "binary_families": list(seed.binary_families),
                "overlay": pre.to_primitive(overlay),
                "promotion_gate_passed": promotion_ok,
                "promotion_issue_codes": list(promotion_issues),
                "final_promotion_accepted": final_promotion_accepted,
                "expected_promotion_accepted": expected_promotion_accepted,
                "outcome_passed": final_promotion_accepted == expected_promotion_accepted,
            }
        )

    random_keys = pre.deterministic_seed_sample(
        index,
        seed=contract["random_validation"]["seed"],
        sample_size=int(contract["random_validation"]["sample_size"]),
    )
    random_rows = []
    for key in random_keys:
        check = pre.reverify_migrated_seed(seeds[key], index[key])
        random_rows.append({"source_index_key": key, **pre.to_primitive(check)})

    counterfactual_rows: list[dict[str, Any]] = []
    tampered = copy.deepcopy(index["AZ|buckeye|reroof"])
    tampered["tier1"]["main_decision"]["provenance"]["snapshot_hash"] = "0" * 64
    tampered_seed = pre.safe_factory_migrate_seed("AZ|buckeye|reroof", tampered)
    counterfactual_rows.append(
        {
            "case_id": "tampered_provenance_hash",
            "classification": tampered_seed.classification.value,
            "binary_families": list(tampered_seed.binary_families),
            "passed": tampered_seed.classification is pre.SeedClassification.FAIL_CLOSED and not tampered_seed.binary_families,
        }
    )
    unsupported_unknown = pre.classify_request_scope("install quantum flux widget", "")
    counterfactual_rows.append(
        {
            "case_id": "unknown_scope",
            "classification": unsupported_unknown.value,
            "passed": unsupported_unknown is pre.SeedClassification.UNSUPPORTED_SCOPE,
        }
    )
    unsupported = pre.classify_request_scope(
        "no roof replacement and no structural alteration; inspection only",
        "residential",
    )
    counterfactual_rows.append(
        {
            "case_id": "negated_unknown_scope",
            "classification": unsupported.value,
            "passed": unsupported is pre.SeedClassification.UNSUPPORTED_SCOPE,
        }
    )
    synthetic_a = copy.deepcopy(index["WI|eau_claire|commercial_tenant_improvement"])
    synthetic_b = copy.deepcopy(synthetic_a)
    synthetic_a.update(
        {
            "cell_id": "genuine-token-ambiguity-a",
            "jurisdiction_id": "us-ex-springfield",
            "ahj": "Spring Field",
            "state": "EX",
        }
    )
    synthetic_b.update(
        {
            "cell_id": "genuine-token-ambiguity-b",
            "jurisdiction_id": "us-ex-spring-field",
            "ahj": "Spring Field",
            "state": "EX",
        }
    )
    synthetic_index = {
        "EX|springfield|commercial_tenant_improvement": synthetic_a,
        "EX|spring_field|commercial_tenant_improvement": synthetic_b,
    }
    original_index_loader = pre.load_v24_index
    try:
        pre.load_v24_index = lambda: synthetic_index  # type: ignore[assignment]
        ambiguous_envelope = pre.build_core_decision_envelope(
            resolution(
                "EX|springfield|commercial_tenant_improvement",
                synthetic_a,
            ),
            job_type="Commercial tenant improvement",
            city="Spring Field",
            state="EX",
            job_category="commercial",
        )
    finally:
        pre.load_v24_index = original_index_loader  # type: ignore[assignment]
    ambiguous_projection = json.loads(
        ambiguous_envelope.sealed_projection.payload_json
    )
    counterfactual_rows.append(
        {
            "case_id": "ambiguous_jurisdiction",
            "source_index_key": "synthetic-token-distinct-jurisdictions",
            "classification": ambiguous_projection["seed_classification"],
            "permit_decision": ambiguous_projection["permit_decision"],
            "passed": ambiguous_projection["seed_classification"] == "jurisdiction_hold" and ambiguous_projection["permit_decision"] == "UNKNOWN",
        }
    )
    original_classifier = pre.classify_v24_seed
    try:
        def _raise_factory_exception(*_args: Any, **_kwargs: Any) -> pre.MigratedSeed:
            raise RuntimeError("deterministic factory exception counterfactual")

        pre.classify_v24_seed = _raise_factory_exception  # type: ignore[assignment]
        factory_exception_seed = pre.safe_factory_migrate_seed(
            "AZ|buckeye|reroof",
            index["AZ|buckeye|reroof"],
        )
    finally:
        pre.classify_v24_seed = original_classifier  # type: ignore[assignment]
    counterfactual_rows.append(
        {
            "case_id": "factory_exception",
            "classification": factory_exception_seed.classification.value,
            "binary_families": list(factory_exception_seed.binary_families),
            "passed": (
                factory_exception_seed.classification is pre.SeedClassification.FAIL_CLOSED
                and not factory_exception_seed.binary_families
                and "factory_exception" in factory_exception_seed.issue_codes
            ),
        }
    )

    preactivation = pre.audit_pre_activation_family_preservation(index)
    predicates = pre.minimum_sourced_predicates()
    templates = pre.minimum_code_adoption_templates()
    model_package = {
        "migration_schema_version": pre.PART3_MIGRATION_SCHEMA_VERSION,
        "factory_schema_version": pre.PART3_FACTORY_SCHEMA_VERSION,
        "overlay_schema_version": pre.PART3_OVERLAY_SCHEMA_VERSION,
        "scope_ontology": pre.minimum_scope_ontology(),
        "sourced_predicates": {key: pre.to_primitive(value) for key, value in sorted(predicates.items())},
        "code_adoption_templates": {key: pre.to_primitive(value) for key, value in sorted(templates.items())},
    }

    original_core = os.environ.pop(pre.CORE_SETTING, None)
    original_allowlist = os.environ.pop(pre.CORE_ALLOWLIST_SETTING, None)
    try:
        parity_input = {"permit_decision": "REQUIRED", "nested": {"families": ["building", "electrical"]}}
        before = pre.response_json_bytes(parity_input)
        after = pre.response_json_bytes(
            pre.maybe_attach_core_decision_envelope(
                parity_input,
                job_type="residential reroof",
                city="Buckeye",
                state="AZ",
                job_category="residential",
            )
        )
    finally:
        if original_core is not None:
            os.environ[pre.CORE_SETTING] = original_core
        if original_allowlist is not None:
            os.environ[pre.CORE_ALLOWLIST_SETTING] = original_allowlist
    flag_off = {"byte_identical": before == after, "before_sha256": sha256_bytes(before), "after_sha256": sha256_bytes(after)}

    expected_counts = contract["expected_real_corpus_classification_counts"]
    actual_counts = dict(sorted(classification_counts.items()))
    identity_name_count = len(
        {
            (str(cell.get("ahj", "")).casefold(), str(cell.get("state", "")).upper())
            for cell in index.values()
        }
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "source_commit": source_commit,
        "source_tag": contract["source_tag"],
        "source_contract_sha256": sha256_bytes(contract_path.read_bytes()),
        "cell_count": len(index),
        "migrated_seed_count": len(seeds),
        "unique_source_cell_id_count": len({seed.source_cell_id for seed in seeds.values()}),
        "unique_seed_sha256_count": len({seed.seed_sha256 for seed in seeds.values()}),
        "classification_counts": actual_counts,
        "classification_counts_match_frozen_contract": actual_counts == expected_counts,
        "classification_total": sum(classification_counts.values()),
        "jurisdiction_cell_id_count": len({str(cell.get("jurisdiction_id", "")).lower() for cell in index.values()}),
        "jurisdiction_identity_name_count": identity_name_count,
        "ambiguous_identity_name_count": len(ambiguous_names),
        "ambiguous_seed_count": classification_counts["jurisdiction_hold"],
        "canonical_alias_identity_name_count": len(canonical_alias_names),
        "canonical_alias_reconciliation_count": len(
            canonical_alias_reconciliations
        ),
        "unexpected_projection_transition_count": len(
            unexpected_projection_transitions
        ),
        "reverification_pass_count": sum(1 for row in reverify_rows if row["ok"]),
        "reverification_failure_count": sum(1 for row in reverify_rows if not row["ok"]),
        "source_family_labels": sorted(family_occurrences),
        "source_family_occurrences": dict(sorted(family_occurrences.items())),
        "binary_family_occurrences": dict(sorted(binary_occurrences.items())),
        "customer_projection_checked_count": len(index),
        "customer_projection_violation_count": len(customer_violations),
        "verified_partial_actionable_next_step_count": verified_partial_actionable,
        "verified_partial_with_family_verification_tasks_count": verified_partial_with_tasks,
        "w4_exact_partial_checked_count": w4_checked,
        "preactivation_family_audit_passed": preactivation["passed"],
        "preactivation_family_violation_count": preactivation["violation_count"],
        "canary_count": len(canary_rows),
        "canary_outcome_pass_count": sum(1 for row in canary_rows if row["outcome_passed"]),
        "canary_promotion_pass_count": sum(1 for row in canary_rows if row["promotion_gate_passed"]),
        "seeded_random_seed": contract["random_validation"]["seed"],
        "seeded_random_sample_count": len(random_rows),
        "seeded_random_pass_count": sum(1 for row in random_rows if row["ok"]),
        "counterfactual_count": len(counterfactual_rows),
        "counterfactual_pass_count": sum(1 for row in counterfactual_rows if row["passed"]),
        "genuine_ambiguity_counterfactual_passed": next(
            row["passed"]
            for row in counterfactual_rows
            if row["case_id"] == "ambiguous_jurisdiction"
        ),
        "flag_off_byte_parity": flag_off,
        "activation_boundary": "disabled_by_default_exact_allowlist_only",
        "production_push_or_deploy": False,
    }

    hard_failures = {
        "cell_count": summary["cell_count"] != 2162,
        "migration_count": summary["migrated_seed_count"] != 2162,
        "classification_contract": not summary["classification_counts_match_frozen_contract"],
        "reverification": summary["reverification_failure_count"] != 0,
        "customer_projection": summary["customer_projection_violation_count"] != 0,
        "verified_partial_ux": (
            summary["verified_partial_actionable_next_step_count"] != classification_counts["exact_partial"]
            or summary["verified_partial_with_family_verification_tasks_count"] != classification_counts["exact_partial"]
        ),
        "preactivation_family_audit": not summary["preactivation_family_audit_passed"],
        "canaries": summary["canary_outcome_pass_count"] != summary["canary_count"],
        "seeded_random": summary["seeded_random_pass_count"] != summary["seeded_random_sample_count"],
        "counterfactuals": summary["counterfactual_pass_count"] != summary["counterfactual_count"],
        "canonical_alias_reconciliation": (
            summary["canonical_alias_identity_name_count"] != 5
            or summary["canonical_alias_reconciliation_count"] != 10
            or summary["unexpected_projection_transition_count"] != 0
            or not summary["genuine_ambiguity_counterfactual_passed"]
        ),
        "flag_off_parity": not flag_off["byte_identical"],
        "exact_family_preservation": sorted(family_occurrences) != contract["observed_exact_permit_families"],
        "seed_uniqueness": summary["unique_source_cell_id_count"] != 2162 or summary["unique_seed_sha256_count"] != 2162,
        "identity_topology": (
            summary["jurisdiction_identity_name_count"] != 2157
            or summary["ambiguous_identity_name_count"] != 5
            or summary["ambiguous_seed_count"] != 10
        ),
    }
    summary["hard_failures"] = hard_failures
    summary["passed"] = not any(hard_failures.values())

    write_jsonl(output_dir / "migration_seeds.jsonl", seed_rows)
    write_jsonl(output_dir / "reverification.jsonl", reverify_rows)
    write_json(output_dir / "canary_results.json", {"schema_version": SCHEMA_VERSION, "rows": canary_rows})
    write_json(output_dir / "seeded_random_and_counterfactual_results.json", {"schema_version": SCHEMA_VERSION, "random_rows": random_rows, "counterfactual_rows": counterfactual_rows})
    write_json(output_dir / "customer_projection_audit.json", {"schema_version": SCHEMA_VERSION, "violations": customer_violations, "canary_projections": projection_rows})
    write_json(
        output_dir / "canonical_alias_reconciliation_audit.json",
        {
            "schema_version": SCHEMA_VERSION,
            "allowed_reconciliations": canonical_alias_reconciliations,
            "unexpected_projection_transitions": unexpected_projection_transitions,
        },
    )
    write_json(output_dir / "preactivation_family_audit.json", preactivation)
    write_json(output_dir / "ontology_predicates_templates.json", model_package)
    write_json(output_dir / "flag_off_parity.json", flag_off)
    write_json(output_dir / "summary.json", summary)

    files: dict[str, dict[str, Any]] = {}
    for path in sorted(output_dir.iterdir()):
        if not path.is_file() or path.name == "manifest.json":
            continue
        data = path.read_bytes()
        files[path.name] = {"bytes": len(data), "sha256": sha256_bytes(data)}
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source_commit": source_commit,
        "files": files,
        "artifact_set_sha256": pre.stable_sha256(files),
        "manifest_self_hash_excluded": True,
    }
    write_json(output_dir / "manifest.json", manifest)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    summary = generate(args.repo_root.resolve(), args.output_dir.resolve(), args.source_commit)
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
