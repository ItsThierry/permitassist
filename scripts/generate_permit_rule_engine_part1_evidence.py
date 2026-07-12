#!/usr/bin/env python3
"""Generate deterministic Permit Rule Engine Part 1 evidence.

The census validates every shipped v2.4 cell with the same portable validation
semantics used by the request-time resolver.  It also executes the frozen
contract corpus with shadowing off and on and proves byte-for-byte preservation
of the supplied legacy payloads.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from api.permit_rule_engine import (  # noqa: E402
    ADAPTER_VERSION,
    DECISION_ENVELOPE_VERSION,
    DIVERGENCE_TAXONOMY_VERSION,
    RULE_ENGINE_SCHEMA_VERSION,
    SHADOW_LOG_SETTING,
    SHADOW_SETTING,
    DivergenceCode,
    canonical_json_bytes,
    classify_divergences,
    envelope_from_cell_for_census,
    observe_permit_rule_engine_shadow,
    prepare_permit_rule_engine_shadow,
    response_json_bytes,
    stable_sha256,
    to_primitive,
)

EXPECTED_BASE_SHA = "facea233ac4f821304288f14137e472e92a6fcfc"
ARTIFACT_SCHEMA_VERSION = "permitassist.rule-engine-part1-evidence.v1"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def write_jsonl(path: Path, values: Iterable[Any]) -> None:
    with path.open("wb") as handle:
        for value in values:
            handle.write(canonical_json_bytes(value) + b"\n")


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[Mapping[str, Any]]) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in fieldnames})
    path.write_text(buffer.getvalue(), encoding="utf-8", newline="")


def mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def rows(value: Any) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def walk_provenance(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        if "source_url" in value and ("snapshot_hash" in value or "source_quote" in value):
            found.append(dict(value))
        for child in value.values():
            found.extend(walk_provenance(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(walk_provenance(child))
    return found


def source_domain(url: Any) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").lower()
    except Exception:
        return ""


def census_cell(index_key: str, cell: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    envelope = envelope_from_cell_for_census(cell, index_key)
    envelope_data = to_primitive(envelope)
    tier1 = mapping(cell.get("tier1"))
    apply_rows = rows(tier1.get("apply"))
    provenance = walk_provenance(cell)
    source_urls = sorted({str(item.get("source_url") or "").strip() for item in provenance if item.get("source_url")})
    source_domains = sorted({source_domain(url) for url in source_urls if source_domain(url)})
    effective_dates = sorted(
        {str(item.get("effective_date") or "").strip() for item in provenance if item.get("effective_date")}
    )
    families = sorted({item["family"] for item in envelope_data["family_decisions"]})
    authority_families = sorted({item["family"] for item in envelope_data["authority_context"]["family_authorities"]})

    conflicts: list[dict[str, Any]] = []
    by_source_dates: dict[str, set[str]] = defaultdict(set)
    by_source_hashes: dict[str, set[str]] = defaultdict(set)
    for item in provenance:
        url = str(item.get("source_url") or "").strip()
        if not url:
            continue
        date = str(item.get("effective_date") or "").strip()
        snapshot_hash = str(item.get("snapshot_hash") or "").strip().lower()
        if date:
            by_source_dates[url].add(date)
        if snapshot_hash:
            by_source_hashes[url].add(snapshot_hash)
    for url in sorted(set(by_source_dates) | set(by_source_hashes)):
        if len(by_source_dates[url]) > 1:
            conflicts.append(
                {"cell_id": cell.get("cell_id"), "conflict_type": "SOURCE_EFFECTIVE_DATE", "key": url, "values": sorted(by_source_dates[url])}
            )
        if len(by_source_hashes[url]) > 1:
            conflicts.append(
                {"cell_id": cell.get("cell_id"), "conflict_type": "SOURCE_SNAPSHOT_HASH", "key": url, "values": sorted(by_source_hashes[url])}
            )

    family_verdicts: dict[str, set[str]] = defaultdict(set)
    for decision in envelope_data["family_decisions"]:
        family_verdicts[decision["family"]].add(decision["verdict"])
    for family, verdicts in sorted(family_verdicts.items()):
        if len(verdicts) > 1:
            conflicts.append(
                {"cell_id": cell.get("cell_id"), "conflict_type": "FAMILY_VERDICT", "key": family, "values": sorted(verdicts)}
            )

    family_authorities: dict[str, set[str]] = defaultdict(set)
    for authority in envelope_data["authority_context"]["family_authorities"]:
        family_authorities[authority["family"]].add(
            f"{authority['issuing_authority']}|{authority['application_authority']}|{authority['authority_tier']}"
        )
    for family, authorities in sorted(family_authorities.items()):
        if len(authorities) > 1:
            conflicts.append(
                {"cell_id": cell.get("cell_id"), "conflict_type": "FAMILY_AUTHORITY", "key": family, "values": sorted(authorities)}
            )

    record = {
        "index_key": index_key,
        "cell_id": str(cell.get("cell_id") or ""),
        "jurisdiction_id": str(cell.get("jurisdiction_id") or ""),
        "state": str(cell.get("state") or ""),
        "ahj": str(cell.get("ahj") or ""),
        "project_family": str(cell.get("project_family") or ""),
        "source_status": str(cell.get("status") or ""),
        "source_serving_status": str(cell.get("serving_status") or ""),
        "source_completeness": str(cell.get("serving_status") or cell.get("completeness") or ""),
        "strict_classification": envelope.coverage_status.value,
        "strict_reason": envelope.coverage_reason,
        "portable_request_validation_ok": envelope_data["validation_ok"],
        "validation_issue_codes": list(envelope.validation_issue_codes),
        "permit_family_depth": len(families),
        "permit_families": families,
        "authority_family_depth": len(authority_families),
        "authority_families": authority_families,
        "application_route_count": len(apply_rows),
        "provenance_record_count": len(provenance),
        "source_url_count": len(source_urls),
        "source_domains": source_domains,
        "effective_dates": effective_dates,
        "source_set_sha256": envelope.source_set_sha256,
        "envelope_sha256": stable_sha256(envelope_data),
    }
    return record, envelope_data, conflicts


def run_replay(corpus: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    replay_rows: list[dict[str, Any]] = []
    divergence_rows: list[dict[str, Any]] = []
    divergence_counts: Counter[str] = Counter()
    mutation_count = 0
    missing_envelope_count = 0
    old_shadow = os.environ.get(SHADOW_SETTING)
    old_log = os.environ.pop(SHADOW_LOG_SETTING, None)
    try:
        for case in sorted(rows(corpus.get("cases")), key=lambda item: str(item.get("case_id") or "")):
            case_id = str(case.get("case_id") or "")
            request = mapping(case.get("request"))
            legacy_result = mapping(case.get("legacy_result"))
            before = response_json_bytes(legacy_result)

            os.environ[SHADOW_SETTING] = "off"
            off_envelope = prepare_permit_rule_engine_shadow(**request)
            off_observation = observe_permit_rule_engine_shadow(off_envelope, legacy_result)
            off_after = response_json_bytes(legacy_result)

            os.environ[SHADOW_SETTING] = "shadow"
            shadow_envelope = prepare_permit_rule_engine_shadow(**request)
            shadow_observation = observe_permit_rule_engine_shadow(shadow_envelope, legacy_result)
            shadow_after = response_json_bytes(legacy_result)

            if off_envelope is not None or off_observation is not None:
                raise AssertionError(f"flag-off adapter executed for {case_id}")
            if shadow_envelope is None or shadow_observation is None:
                missing_envelope_count += 1
                raise AssertionError(f"shadow envelope missing for {case_id}")
            byte_parity = before == off_after == shadow_after
            if not byte_parity:
                mutation_count += 1

            envelope_data = to_primitive(shadow_envelope)
            observation_data = to_primitive(shadow_observation)
            replay_divergences = classify_divergences(
                shadow_envelope,
                legacy_result,
                authoritative_result=mapping(case.get("authoritative_result")) or None,
                public_result=mapping(case.get("public_result")) or None,
            )
            replay_divergence_data = [to_primitive(item) for item in replay_divergences]
            replay_rows.append(
                {
                    "case_id": case_id,
                    "request": request,
                    "legacy_payload_sha256_before": hashlib.sha256(before).hexdigest(),
                    "legacy_payload_sha256_flag_off": hashlib.sha256(off_after).hexdigest(),
                    "legacy_payload_sha256_shadow": hashlib.sha256(shadow_after).hexdigest(),
                    "byte_parity": byte_parity,
                    "envelope": envelope_data,
                    "observation": observation_data,
                    "replay_divergences": replay_divergence_data,
                }
            )
            for divergence in replay_divergence_data:
                row = {"case_id": case_id, "coverage_status": observation_data["coverage_status"], **divergence}
                divergence_rows.append(row)
                divergence_counts[divergence["code"]] += 1
    finally:
        if old_shadow is None:
            os.environ.pop(SHADOW_SETTING, None)
        else:
            os.environ[SHADOW_SETTING] = old_shadow
        if old_log is not None:
            os.environ[SHADOW_LOG_SETTING] = old_log

    locked_codes = (
        DivergenceCode.AHJ_BOUNDARY_MISMATCH,
        DivergenceCode.SCOPE_TAXONOMY_UNSUPPORTED,
        DivergenceCode.PROJECT_FAMILY_NOT_COVERED,
        DivergenceCode.RULE_OR_EXEMPTION_MISSING,
        DivergenceCode.COMPANION_CLOSURE_INCOMPLETE,
        DivergenceCode.AUTHORITATIVE_CELL_NOT_INJECTED,
        DivergenceCode.MODEL_OR_GENERIC_FALLBACK_GUESS,
        DivergenceCode.POST_RECONCILIATION_MUTATION,
        DivergenceCode.PUBLIC_RENDER_DIVERGENCE,
        DivergenceCode.STALE_OR_CONFLICTING_RULE,
    )
    locked_taxonomy_counts = {
        code.value: divergence_counts.get(code.value, 0)
        for code in locked_codes
    }
    summary = {
        "corpus_case_count": len(replay_rows),
        "flag_off_case_count": len(replay_rows),
        "flag_off_envelope_count": 0,
        "shadow_envelope_count": len(replay_rows) - missing_envelope_count,
        "shadow_envelope_coverage_percent": 100.0 if replay_rows and not missing_envelope_count else 0.0,
        "customer_payload_mutation_count": mutation_count,
        "byte_parity_case_count": sum(1 for item in replay_rows if item["byte_parity"]),
        "divergence_counts": dict(sorted(divergence_counts.items())),
        "locked_taxonomy_counts": locked_taxonomy_counts,
        "locked_taxonomy_class_count": len(locked_taxonomy_counts),
    }
    return replay_rows, divergence_rows, summary


def generate(root: Path, output_dir: Path, corpus_path: Path, base_sha: str) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = root / "knowledge/v24/permitassist_decision_cell_index_v24.json"
    manifest_path = root / "knowledge/v24/permitassist_v24_manifest.json"
    package_path = root / "knowledge/v24/permitassist_decision_cells_v24.json"
    index_doc = json.loads(index_path.read_text(encoding="utf-8"))
    index = mapping(index_doc.get("index"))
    if len(index) != 2162:
        raise AssertionError(f"expected 2162 indexed cells, found {len(index)}")

    census_rows: list[dict[str, Any]] = []
    authority_rows: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    jurisdiction_cells: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    global_evidence: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"snapshot_hashes": set(), "effective_dates": set(), "cell_ids": set()}
    )

    for index_key in sorted(index):
        cell = mapping(index[index_key])
        for provenance in walk_provenance(cell):
            source_url = str(provenance.get("source_url") or "").strip()
            if not source_url:
                continue
            evidence = global_evidence[source_url]
            evidence["cell_ids"].add(str(cell.get("cell_id") or index_key))
            snapshot_hash = str(provenance.get("snapshot_sha256") or provenance.get("snapshot_hash") or "").strip()
            effective_date = str(provenance.get("effective_date") or "").strip()
            if snapshot_hash:
                evidence["snapshot_hashes"].add(snapshot_hash)
            if effective_date:
                evidence["effective_dates"].add(effective_date)
        record, envelope, cell_conflicts = census_cell(index_key, cell)
        census_rows.append(record)
        conflicts.extend(cell_conflicts)
        jurisdiction_key = (record["state"], record["jurisdiction_id"], record["ahj"])
        jurisdiction_cells[jurisdiction_key].append(record)
        for authority in envelope["authority_context"]["family_authorities"]:
            authority_rows.append(
                {
                    "cell_id": record["cell_id"],
                    "state": record["state"],
                    "ahj": record["ahj"],
                    "project_family": record["project_family"],
                    "family": authority["family"],
                    "issuing_authority": authority["issuing_authority"],
                    "application_authority": authority["application_authority"],
                    "authority_tier": authority["authority_tier"],
                    "handled_by_local_ahj": authority["handled_by_local_ahj"],
                }
            )

    for source_url, evidence in sorted(global_evidence.items()):
        snapshot_hashes = sorted(evidence["snapshot_hashes"])
        effective_dates = sorted(evidence["effective_dates"])
        cell_ids = sorted(evidence["cell_ids"])
        if len(snapshot_hashes) > 1:
            conflicts.append(
                {
                    "conflict_type": "GLOBAL_SOURCE_SNAPSHOT_HASH_CONFLICT",
                    "source_url": source_url,
                    "cell_ids": cell_ids,
                    "values": snapshot_hashes,
                }
            )
        if len(effective_dates) > 1:
            conflicts.append(
                {
                    "conflict_type": "GLOBAL_SOURCE_EFFECTIVE_DATE_CONFLICT",
                    "source_url": source_url,
                    "cell_ids": cell_ids,
                    "values": effective_dates,
                }
            )

    classification_counts = Counter(item["strict_classification"] for item in census_rows)
    source_status_counts = Counter(item["source_status"] for item in census_rows)
    source_completeness_counts = Counter(item["source_completeness"] for item in census_rows)
    validation_failure_count = sum(item["portable_request_validation_ok"] is False for item in census_rows)
    family_counts: dict[str, Counter[str]] = defaultdict(Counter)
    family_depth_counts: Counter[tuple[str, str, int, int]] = Counter()
    for item in census_rows:
        family_counts[item["project_family"]][item["strict_classification"]] += 1
        family_depth_counts[
            (
                item["project_family"],
                item["strict_classification"],
                item["permit_family_depth"],
                item["authority_family_depth"],
            )
        ] += 1

    family_depth_rows = [
        {
            "project_family": key[0],
            "strict_classification": key[1],
            "permit_family_depth": key[2],
            "authority_family_depth": key[3],
            "cell_count": count,
        }
        for key, count in sorted(family_depth_counts.items())
    ]

    jurisdiction_rows: list[dict[str, Any]] = []
    jurisdiction_id_shapes: dict[str, set[tuple[str, str]]] = defaultdict(set)
    normalized_name_shapes: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (state, jurisdiction_id, ahj), items in sorted(jurisdiction_cells.items()):
        jurisdiction_id_shapes[jurisdiction_id].add((state, ahj))
        normalized_name_shapes[(state, "_".join(ahj.lower().split()))].add(jurisdiction_id)
        jurisdiction_rows.append(
            {
                "state": state,
                "jurisdiction_id": jurisdiction_id,
                "ahj": ahj,
                "cell_count": len(items),
                "project_families": "|".join(sorted({item["project_family"] for item in items})),
                "strict_classifications": "|".join(sorted({item["strict_classification"] for item in items})),
                "source_statuses": "|".join(sorted({item["source_status"] for item in items})),
            }
        )
    for jurisdiction_id, values in sorted(jurisdiction_id_shapes.items()):
        if jurisdiction_id and len(values) > 1:
            conflicts.append(
                {"conflict_type": "JURISDICTION_ID_SHAPE", "key": jurisdiction_id, "values": [f"{state}|{ahj}" for state, ahj in sorted(values)]}
            )
    for key, values in sorted(normalized_name_shapes.items()):
        if len(values) > 1:
            conflicts.append(
                {"conflict_type": "JURISDICTION_NORMALIZED_NAME", "key": "|".join(key), "values": sorted(values)}
            )

    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    replay_rows, divergence_rows, replay_summary = run_replay(corpus)
    if replay_summary["customer_payload_mutation_count"]:
        raise AssertionError("shadow execution mutated frozen customer payloads")

    write_jsonl(output_dir / "cell_census.jsonl", census_rows)
    write_csv(
        output_dir / "family_depth_census.csv",
        ["project_family", "strict_classification", "permit_family_depth", "authority_family_depth", "cell_count"],
        family_depth_rows,
    )
    write_csv(
        output_dir / "authority_census.csv",
        [
            "cell_id",
            "state",
            "ahj",
            "project_family",
            "family",
            "issuing_authority",
            "application_authority",
            "authority_tier",
            "handled_by_local_ahj",
        ],
        sorted(authority_rows, key=lambda item: tuple(str(item[key]) for key in ("state", "ahj", "project_family", "family", "cell_id"))),
    )
    write_csv(
        output_dir / "jurisdiction_census.csv",
        ["state", "jurisdiction_id", "ahj", "cell_count", "project_families", "strict_classifications", "source_statuses"],
        jurisdiction_rows,
    )
    write_json(
        output_dir / "evidence_effective_date_conflicts.json",
        {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "conflict_count": len(conflicts),
            "conflict_counts": dict(sorted(Counter(item["conflict_type"] for item in conflicts).items())),
            "conflicts": sorted(
                conflicts,
                key=lambda item: (
                    item["conflict_type"],
                    str(item.get("cell_id") or ""),
                    str(item.get("key") or item.get("source_url") or ""),
                ),
            ),
        },
    )
    write_jsonl(output_dir / "frozen_replay_envelopes.jsonl", replay_rows)
    write_jsonl(output_dir / "divergence_ledger.jsonl", divergence_rows)

    summary = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "inspected_base_sha": base_sha,
        "cell_count": len(census_rows),
        "all_cells_classified": len(census_rows) == 2162,
        "portable_request_validation_failure_count": validation_failure_count,
        "strict_classification_counts": dict(sorted(classification_counts.items())),
        "source_status_counts": dict(sorted(source_status_counts.items())),
        "source_completeness_counts": dict(sorted(source_completeness_counts.items())),
        "strict_classification_by_family": {
            family: dict(sorted(counts.items())) for family, counts in sorted(family_counts.items())
        },
        "legacy_tier1_complete_count": source_completeness_counts.get("TIER1_COMPLETE", 0),
        "strict_exact_complete_count": classification_counts.get("EXACT_COMPLETE", 0),
        "strict_partial_or_fail_closed_count": (
            classification_counts.get("EXACT_PARTIAL", 0)
            + classification_counts.get("FAIL_CLOSED", 0)
            + classification_counts.get("VALIDATION_FAILED", 0)
        ),
        "authority_row_count": len(authority_rows),
        "jurisdiction_row_count": len(jurisdiction_rows),
        "conflict_count": len(conflicts),
        "conflict_counts": dict(sorted(Counter(item["conflict_type"] for item in conflicts).items())),
        "unique_source_url_count": len(global_evidence),
        "source_urls_without_effective_date_count": sum(
            1 for evidence in global_evidence.values() if not evidence["effective_dates"]
        ),
        "cells_without_effective_date_count": sum(
            1 for item in census_rows if not item["effective_dates"]
        ),
        "replay": replay_summary,
        "versions": {
            "rule_engine_schema": RULE_ENGINE_SCHEMA_VERSION,
            "decision_envelope": DECISION_ENVELOPE_VERSION,
            "shadow_adapter": ADAPTER_VERSION,
            "divergence_taxonomy": DIVERGENCE_TAXONOMY_VERSION,
        },
        "inputs": {
            "decision_cell_index_sha256": file_sha256(index_path),
            "decision_cell_package_sha256": file_sha256(package_path),
            "v24_manifest_sha256": file_sha256(manifest_path),
            "frozen_corpus_sha256": file_sha256(corpus_path),
            "generator_sha256": file_sha256(Path(__file__).resolve()),
            "permit_rule_engine_sha256": file_sha256(root / "api/permit_rule_engine.py"),
        },
    }
    write_json(output_dir / "summary.json", summary)

    readme = """# Permit Rule Engine Part 1 evidence\n\nThis directory is generated by `scripts/generate_permit_rule_engine_part1_evidence.py`.\nAll files are deterministic: no timestamps, network calls, or mutable runtime data are used.\n\n- `cell_census.jsonl`: all 2,162 v2.4 cells, request-time-equivalent validation, strict depth/coverage classification, and evidence hashes.\n- `family_depth_census.csv`: aggregate permit/authority depth by project family and strict classification.\n- `authority_census.csv`: every per-family authority row exposed by DecisionEnvelope.\n- `jurisdiction_census.csv`: jurisdiction boundary inventory.\n- `evidence_effective_date_conflicts.json`: deterministic source-date/hash, verdict, authority, and jurisdiction conflicts.\n- `frozen_replay_envelopes.jsonl`: full envelope/observation for every synthetic frozen contract case plus byte-parity hashes.\n- `divergence_ledger.jsonl`: structured divergence taxonomy rows for the frozen contract corpus.\n- `summary.json`: gate totals and immutable input hashes.\n- `manifest.json`: hashes of every generated artifact.\n\nThe corpus explicitly identifies itself as synthetic contract replay data; it is not represented as production or historical customer evidence.\n"""
    (output_dir / "README.md").write_text(readme, encoding="utf-8", newline="\n")

    generated_files = sorted(path for path in output_dir.iterdir() if path.is_file() and path.name != "manifest.json")
    artifact_manifest = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "inspected_base_sha": base_sha,
        "files": {path.name: {"sha256": file_sha256(path), "bytes": path.stat().st_size} for path in generated_files},
    }
    artifact_manifest["artifact_set_sha256"] = stable_sha256(artifact_manifest["files"])
    write_json(output_dir / "manifest.json", artifact_manifest)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/permit_rule_engine_part1_20260711/generated"),
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path("tests/fixtures/permit_rule_engine_part1_frozen_corpus.json"),
    )
    parser.add_argument("--base-sha", default=EXPECTED_BASE_SHA)
    args = parser.parse_args()
    root = args.root.resolve()
    output_dir = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    corpus_path = args.corpus if args.corpus.is_absolute() else root / args.corpus
    summary = generate(root, output_dir.resolve(), corpus_path.resolve(), args.base_sha)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
