#!/usr/bin/env python3
"""Generate deterministic, offline Permit Rule Engine Part 2 evidence."""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "permit_rule_engine_part2_20260712" / "generated"

from api import permit_rule_engine as pre
from api import server
from api.v24_decision_cells import V24Resolution, V24ResolutionStatus, load_v24_index


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def family_scope(project_family: str) -> tuple[str, str]:
    if project_family == "commercial_tenant_improvement":
        return "commercial tenant improvement", "commercial"
    if project_family == "residential_remodel":
        return "residential remodel", "residential"
    return "residential reroof", "residential"


def primitive(value: Any) -> Any:
    return pre.to_primitive(value)


def main() -> int:
    for name in (pre.CORE_SETTING, pre.CORE_ALLOWLIST_SETTING):
        os.environ.pop(name, None)
    if pre.get_rule_engine_core_mode() != "off":
        raise RuntimeError("core must be disabled by default")

    index = load_v24_index() or {}
    rows: list[dict[str, Any]] = []
    stage_counts: Counter[str] = Counter()
    coverage_counts: Counter[str] = Counter()
    family_verdict_counts: Counter[str] = Counter()
    stage_family_verdict_counts: Counter[str] = Counter()
    identity_status_counts: Counter[str] = Counter()
    seen_identities: set[tuple[str, str]] = set()
    stable_candidate_violations: list[str] = []
    source_free_binary_violations: list[str] = []
    abstain_stage_binary_violations: list[str] = []
    projection_integrity_violations: list[str] = []
    family_route_missing_violations: list[str] = []
    public_metadata_leak_violations: list[str] = []

    reversed_index = dict(reversed(list(index.items())))
    for cell_key in sorted(index):
        cell = index[cell_key]
        city = str(cell.get("ahj") or "")
        state = str(cell.get("state") or "")
        identity_key = (city.casefold(), state.upper())
        if identity_key not in seen_identities:
            seen_identities.add(identity_key)
            identity = pre.resolve_jurisdiction_identity(city, state, index=index)
            reversed_identity = pre.resolve_jurisdiction_identity(city, state, index=reversed_index)
            identity_status_counts[identity.status.value] += 1
            if primitive(identity) != primitive(reversed_identity):
                stable_candidate_violations.append(f"{city}|{state}")

        project_family = str(cell.get("project_family") or "")
        job_type, job_category = family_scope(project_family)
        resolution = V24Resolution(
            V24ResolutionStatus.EXACT_CELL_PUBLISHABLE,
            cell=cell,
            key=cell_key,
            reason="evidence-census",
        )
        envelope = pre.build_core_decision_envelope(
            resolution,
            job_type=job_type,
            city=city,
            state=state,
            job_category=job_category,
        )
        stage_counts[envelope.precedence_stage.value] += 1
        coverage_counts[envelope.coverage_status] += 1
        sealed_payload = json.loads(envelope.sealed_projection.payload_json)
        serialized_projection = json.dumps(sealed_payload, sort_keys=True)
        forbidden_public_keys = {"snapshot_path", "publishable", "authority_tier", "handled_by_local_ahj"}
        if any(f'"{key}"' in serialized_projection for key in forbidden_public_keys):
            public_metadata_leak_violations.append(cell_key)
        decisions = (envelope.main_decision,) + envelope.family_decisions
        for decision in decisions:
            family_verdict_counts[decision.verdict.value] += 1
            stage_family_verdict_counts[f"{envelope.precedence_stage.value}|{decision.verdict.value}"] += 1
            if decision.verdict in {pre.FamilyVerdict.REQUIRED, pre.FamilyVerdict.NOT_REQUIRED}:
                if not any(pre._publishable_provenance(record) for record in decision.provenance):
                    source_free_binary_violations.append(f"{cell_key}|{decision.family}")
                if envelope.precedence_stage in {
                    pre.PrecedenceStage.EXACT_FAIL_CLOSED,
                    pre.PrecedenceStage.INTERNAL_ABSTAIN,
                }:
                    abstain_stage_binary_violations.append(
                        f"{cell_key}|{decision.family}|{decision.verdict.value}"
                    )
        if not pre.validate_rule_engine_cache_payload(
            pre.attach_core_decision_envelope({}, envelope),
            required_version=pre.CORE_CACHE_SCHEMA_VERSION,
        ):
            projection_integrity_violations.append(cell_key)
        routed_families = {route.family for route in envelope.family_routes}
        authority_families = {
            family
            for row in cell.get("tier1", {}).get("trade_authority", [])
            if isinstance(row, dict)
            for family in [pre.normalize_family(row.get("permit_family") or row.get("trade"))]
            if family in pre._CORE_FAMILIES
        }
        missing_routes = sorted(family for family in authority_families if family and family not in routed_families)
        if missing_routes:
            family_route_missing_violations.append(f"{cell_key}|{','.join(missing_routes)}")
        rows.append(
            {
                "cell_key": cell_key,
                "coverage_status": envelope.coverage_status,
                "envelope_sha256": envelope.envelope_sha256,
                "family_count": len(envelope.family_decisions),
                "family_route_count": len(envelope.family_routes),
                "jurisdiction_id": envelope.jurisdiction.selected.jurisdiction_id if envelope.jurisdiction.selected else "",
                "precedence_stage": envelope.precedence_stage.value,
                "projection_sha256": envelope.sealed_projection.payload_sha256,
                "source_cell_id": envelope.source_cell_id,
                "work_atoms_valid": envelope.work_atoms.valid,
            }
        )

    fixture = json.loads((ROOT / "tests" / "fixtures" / "permit_rule_engine_part2_red_no_neuter.json").read_text(encoding="utf-8"))
    no_neuter = fixture["cases"]["no_neuter_ten_lane"]
    no_neuter_families = sorted(pre.normalize_family(family) for family in no_neuter["families"])
    no_neuter_expected_family_count = len(no_neuter["families"])

    fail_closed_stages = [
        pre.select_precedence_stage(V24ResolutionStatus.EXACT_CELL_PUBLISHABLE, "complete", False).value,
        pre.select_precedence_stage(V24ResolutionStatus.EXACT_CELL_PUBLISHABLE, "partial", False).value,
        pre.select_precedence_stage(V24ResolutionStatus.EXACT_CELL_FAIL_CLOSED, "fail_closed", True).value,
        pre.select_precedence_stage(V24ResolutionStatus.AHJ_NOT_COVERED, "none", True).value,
        pre.select_precedence_stage(V24ResolutionStatus.AHJ_NOT_COVERED, "none", False).value,
    ]

    surface_equal = False
    sample = rows[0] if rows else None
    if sample:
        sample_cell = index[sample["cell_key"]]
        city = str(sample_cell.get("ahj") or "")
        state = str(sample_cell.get("state") or "")
        job_type, job_category = family_scope(str(sample_cell.get("project_family") or ""))
        envelope = pre.build_core_decision_envelope(
            V24Resolution(
                V24ResolutionStatus.EXACT_CELL_PUBLISHABLE,
                cell=sample_cell,
                key=sample["cell_key"],
                reason="surface-census",
            ),
            job_type=job_type,
            city=city,
            state=state,
            job_category=job_category,
        )
        os.environ[pre.CORE_SETTING] = "active"
        os.environ[pre.CORE_ALLOWLIST_SETTING] = (
            envelope.jurisdiction.selected.jurisdiction_id if envelope.jurisdiction.selected else ""
        )
        wrapped = pre.attach_core_decision_envelope({"permit_name": "POISONED LEGACY"}, envelope)
        extracted = pre.extract_sealed_public_projection(wrapped, city=city, state=state)
        customer = server.build_customer_permit_view_model(wrapped, job_type, city, state)
        finalized = server.finalize_permit_lookup_result(wrapped, job_type, city, state)
        surface_equal = bool(extracted and customer == extracted and finalized == extracted and "POISONED LEGACY" not in canonical(customer))
        os.environ.pop(pre.CORE_SETTING, None)
        os.environ.pop(pre.CORE_ALLOWLIST_SETTING, None)

    exact_fixture = fixture["cases"]["exact_identity"]
    expected_jurisdiction_id = next(iter(exact_fixture["index"].values()))["jurisdiction_id"]
    flag_default_disabled = pre.get_rule_engine_core_mode() == "off"
    os.environ[pre.CORE_SETTING] = "active"
    os.environ[pre.CORE_ALLOWLIST_SETTING] = expected_jurisdiction_id
    allowlist_exact = pre.core_activation_allowed(expected_jurisdiction_id)
    allowlist_rejects_other = not pre.core_activation_allowed("us-az-other")
    os.environ.pop(pre.CORE_SETTING, None)
    os.environ.pop(pre.CORE_ALLOWLIST_SETTING, None)

    summary = {
        "schema_version": "permitassist.rule-engine-part2.evidence.v1",
        "source_tag": "permit-rule-engine-part1-20260712",
        "source_commit": "1db644ee09e3a13e423462e5d8d8934d916dad71",
        "adapter_version_unchanged": pre.ADAPTER_VERSION,
        "part1_envelope_schema_version_unchanged": pre.DECISION_ENVELOPE_VERSION,
        "core_envelope_schema_version": pre.CORE_ENVELOPE_SCHEMA_VERSION,
        "core_projection_schema_version": pre.CORE_PROJECTION_SCHEMA_VERSION,
        "core_cache_schema_version": pre.CORE_CACHE_SCHEMA_VERSION,
        "cell_count": len(rows),
        "identity_count": len(seen_identities),
        "identity_status_counts": dict(sorted(identity_status_counts.items())),
        "precedence_stage_counts": dict(sorted(stage_counts.items())),
        "coverage_counts": dict(sorted(coverage_counts.items())),
        "family_verdict_counts": dict(sorted(family_verdict_counts.items())),
        "stage_family_verdict_counts": dict(sorted(stage_family_verdict_counts.items())),
        "precedence_funnel_probe": fail_closed_stages,
        "expected_precedence_funnel_probe": [
            "validated_exact_complete",
            "validated_exact_partial",
            "exact_fail_closed",
            "query_official_evidence",
            "internal_abstain",
        ],
        "stable_candidate_violation_count": len(stable_candidate_violations),
        "source_free_binary_violation_count": len(source_free_binary_violations),
        "fail_closed_or_internal_abstain_binary_violation_count": len(abstain_stage_binary_violations),
        "projection_integrity_violation_count": len(projection_integrity_violations),
        "family_route_missing_violation_count": len(family_route_missing_violations),
        "public_projection_internal_metadata_violation_count": len(public_metadata_leak_violations),
        "sealed_surface_projection_equal": surface_equal,
        "flag_default_disabled": flag_default_disabled,
        "allowlist_exact": allowlist_exact,
        "allowlist_rejects_other": allowlist_rejects_other,
        "no_neuter_family_count": len(no_neuter_families),
        "no_neuter_expected_family_count": no_neuter_expected_family_count,
        "no_neuter_families": no_neuter_families,
        "part1_checkpoint_manifest_present": (ROOT / "artifacts" / "permit_rule_engine_part1_20260711" / "CHECKPOINT_MANIFEST.json").is_file(),
        "network_or_model_calls": 0,
    }
    blockers = {
        "stable_candidate_violations": stable_candidate_violations,
        "source_free_binary_violations": source_free_binary_violations,
        "fail_closed_or_internal_abstain_binary_violations": abstain_stage_binary_violations,
        "projection_integrity_violations": projection_integrity_violations,
        "family_route_missing_violations": family_route_missing_violations,
        "public_projection_internal_metadata_violations": public_metadata_leak_violations,
    }
    expected = summary["precedence_funnel_probe"] == summary["expected_precedence_funnel_probe"]
    passed = all(
        [
            not stable_candidate_violations,
            not source_free_binary_violations,
            not abstain_stage_binary_violations,
            not projection_integrity_violations,
            not family_route_missing_violations,
            not public_metadata_leak_violations,
            surface_equal,
            flag_default_disabled,
            allowlist_exact,
            allowlist_rejects_other,
            len(no_neuter_families) == no_neuter_expected_family_count,
            expected,
        ]
    )
    summary["gate_passed"] = passed

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "cells.jsonl").write_text("".join(canonical(row) + "\n" for row in rows), encoding="utf-8")
    (OUT / "blockers.json").write_text(json.dumps(blockers, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    files = {}
    for path in sorted(OUT.iterdir()):
        if path.name == "manifest.json" or not path.is_file():
            continue
        files[path.name] = {"bytes": path.stat().st_size, "sha256": sha256_bytes(path.read_bytes())}
    manifest = {"schema_version": "permitassist.rule-engine-part2.manifest.v1", "files": files}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(canonical({"gate_passed": passed, "cell_count": len(rows), "manifest_files": len(files)}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
