from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from api import permit_rule_engine as pre
from api.v24_decision_cells import V24Resolution, V24ResolutionStatus, load_v24_index


ROOT = Path(__file__).parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "permit_rule_engine_part3_red_no_neuter.json"
MANIFEST_PATH = ROOT / "tests" / "fixtures" / "permit_rule_engine_part3_red_no_neuter_manifest.json"


@pytest.fixture(scope="module")
def frozen_contract() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for relative_path, expected in manifest["files"].items():
        assert hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest() == expected
    assert manifest["frozen_before_behavior_edits"] is False
    assert manifest["contract_correction"] == "five_status_customer_projection_migration_20260730"
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def index() -> dict:
    return load_v24_index() or {}


@pytest.fixture(scope="module")
def migrated(index: dict) -> dict:
    return pre.migrate_v24_seed_index(index=index)


def _family_scope(project_family: str) -> tuple[str, str]:
    if project_family == "commercial_tenant_improvement":
        return "commercial tenant improvement", "commercial"
    if project_family == "residential_remodel":
        return "residential remodel", "residential"
    return "residential reroof", "residential"


def _resolution(index_key: str, cell: dict) -> V24Resolution:
    status = (
        V24ResolutionStatus.EXACT_CELL_FAIL_CLOSED
        if cell.get("status") == "FAIL_CLOSED" or cell.get("serving_status") == "FAIL_CLOSED"
        else V24ResolutionStatus.EXACT_CELL_PUBLISHABLE
    )
    return V24Resolution(status, cell=cell, key=index_key, reason="Part 3 frozen contract")


def test_part3_contract_versions_and_closed_classifications(frozen_contract: dict) -> None:
    assert pre.PART3_MIGRATION_SCHEMA_VERSION == "permitassist.rule-engine-seed-migration.v1"
    assert pre.PART3_PREDICATE_SCHEMA_VERSION == "permitassist.sourced-predicate.v1"
    assert pre.PART3_FACTORY_SCHEMA_VERSION == "permitassist.evidence-gated-factory.v1"
    assert tuple(item.value for item in pre.SeedClassification) == tuple(frozen_contract["required_seed_classifications"])


def test_every_legacy_cell_is_migrated_once_with_immutable_source_hash(
    frozen_contract: dict, index: dict, migrated: dict
) -> None:
    assert len(index) == frozen_contract["legacy_cell_count"]
    assert set(migrated) == set(index)
    assert len({seed.source_cell_id for seed in migrated.values()}) == len(index)
    assert all(seed.source_cell_sha256 == pre.stable_sha256(index[key]) for key, seed in migrated.items())
    assert all(seed.seed_sha256 == pre.stable_sha256(pre.seed_hash_payload(seed)) for seed in migrated.values())


def test_real_corpus_has_exact_locked_honest_classification_counts(
    frozen_contract: dict, migrated: dict
) -> None:
    counts = Counter(seed.classification.value for seed in migrated.values())
    assert dict(sorted(counts.items())) == frozen_contract["expected_real_corpus_classification_counts"]


def test_migration_preserves_every_observed_exact_permit_family(
    frozen_contract: dict, index: dict, migrated: dict
) -> None:
    observed = {
        str(row.get("permit_kind"))
        for cell in index.values()
        for row in (cell.get("tier1") or {}).get("permits_required") or []
        if row.get("permit_kind")
    }
    assert sorted(observed) == frozen_contract["observed_exact_permit_families"]
    migrated_families = {family for seed in migrated.values() for family in seed.source_families}
    assert migrated_families == observed
    assert all(set(seed.binary_families).issubset(set(seed.source_families)) for seed in migrated.values())


def test_ambiguous_identity_cells_become_jurisdiction_holds_not_binary(
    frozen_contract: dict, migrated: dict
) -> None:
    key = frozen_contract["jurisdiction_hold_canary"]["index_key"]
    seed = migrated[key]
    assert seed.classification is pre.SeedClassification.JURISDICTION_HOLD
    assert seed.binary_families == ()
    assert "jurisdiction_identity_ambiguous" in seed.issue_codes


def test_minimum_ontology_predicates_templates_and_overlay_are_closed_and_sourced(
    frozen_contract: dict, index: dict
) -> None:
    ontology = pre.minimum_scope_ontology()
    predicates = pre.minimum_sourced_predicates()
    templates = pre.minimum_code_adoption_templates()
    assert list(ontology) == frozen_contract["scope_ontology_nodes"]
    assert predicates
    assert templates
    for predicate in predicates.values():
        assert set(frozen_contract["required_predicate_fields"]).issubset(pre.to_primitive(predicate))
        assert predicate.provenance and all(pre._publishable_provenance(item) for item in predicate.provenance)
    for template in templates.values():
        assert set(frozen_contract["required_template_fields"]).issubset(pre.to_primitive(template))
        assert template.provenance and all(pre._publishable_provenance(item) for item in template.provenance)
        assert set(template.predicate_ids).issubset(predicates)

    overlay = pre.build_ahj_overlay(index["AZ|buckeye|reroof"])
    assert set(frozen_contract["required_overlay_fields"]).issubset(pre.to_primitive(overlay))
    assert overlay.jurisdiction_id == "us-az-buckeye"
    assert overlay.provenance and all(pre._publishable_provenance(item) for item in overlay.provenance)


def test_factory_is_born_fail_closed_and_only_evidence_gate_can_promote(index: dict) -> None:
    candidate = pre.build_fail_closed_factory_seed(
        jurisdiction_id="us-ex-exampleville",
        ahj_name="Exampleville",
        state="EX",
        project_family="reroof",
        source_index_key="EX|exampleville|reroof",
    )
    assert candidate.classification is pre.SeedClassification.FAIL_CLOSED
    assert candidate.binary_families == ()
    assert pre.promote_factory_seed(candidate, source_cell={}) is candidate

    promoted = pre.promote_factory_seed(candidate, source_cell=index["AZ|buckeye|reroof"])
    assert promoted.classification in {pre.SeedClassification.EXACT_COMPLETE, pre.SeedClassification.EXACT_PARTIAL}
    assert promoted.binary_families
    assert promoted.seed_sha256 != candidate.seed_sha256


def test_factory_exception_is_fail_closed_not_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pre, "classify_v24_seed", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    result = pre.safe_factory_migrate_seed("EX|exampleville|reroof", {"jurisdiction_id": "us-ex-exampleville"})
    assert result.classification is pre.SeedClassification.FAIL_CLOSED
    assert result.binary_families == ()
    assert "factory_exception" in result.issue_codes


def test_verified_partial_customer_projection_is_useful_and_preserves_proven_families(
    frozen_contract: dict, index: dict, migrated: dict
) -> None:
    key = "AL|albertville|residential_remodel"
    cell = index[key]
    job_type, category = _family_scope(cell["project_family"])
    envelope = pre.build_core_decision_envelope(
        _resolution(key, cell),
        job_type=job_type,
        city=cell["ahj"],
        state=cell["state"],
        job_category=category,
    )
    payload = json.loads(envelope.sealed_projection.payload_json)
    seed = migrated[key]
    assert payload["seed_classification"] == seed.classification.value
    assert payload["permit_required"] is True
    assert set(seed.source_families).issubset({row["family"] for row in payload["family_decisions"]})
    assert payload["verification_tasks"]
    assert all(set(frozen_contract["customer_partial_contract"]["required_next_step_fields"]).issubset(task) for task in payload["verification_tasks"])
    assert frozen_contract["customer_partial_contract"]["forbidden_bare_maps_url"] not in json.dumps(payload, sort_keys=True)


def test_fail_closed_customer_projection_clears_claims_routes_and_binary(index: dict) -> None:
    key, cell = next((key, cell) for key, cell in sorted(index.items()) if cell.get("status") == "FAIL_CLOSED")
    job_type, category = _family_scope(cell["project_family"])
    envelope = pre.build_core_decision_envelope(
        _resolution(key, cell),
        job_type=job_type,
        city=cell["ahj"],
        state=cell["state"],
        job_category=category,
    )
    payload = json.loads(envelope.sealed_projection.payload_json)
    assert payload["seed_classification"] == "fail_closed"
    assert payload["permit_required"] is None
    assert payload["sources"] == []
    assert payload["claim_citations"] == []
    assert payload["family_authority_routes"] == []
    assert all(row["verdict"] == "NEEDS_INPUT" for row in payload["family_decisions"])


def test_authority_scope_canaries_preserve_exact_family_rows(
    frozen_contract: dict, index: dict, migrated: dict
) -> None:
    for canary in frozen_contract["authority_scope_canaries"]:
        key = canary["index_key"]
        cell = index[key]
        envelope = pre.build_core_decision_envelope(
            _resolution(key, cell),
            job_type=canary["job_type"],
            city=cell["ahj"],
            state=cell["state"],
            job_category=canary["job_category"],
        )
        payload = json.loads(envelope.sealed_projection.payload_json)
        families = {row["family"] for row in payload["family_decisions"]}
        if canary.get("expected_primary_family"):
            assert payload["permit_kind"] == canary["expected_primary_family"]
        if canary.get("expected_exact_families"):
            assert set(canary["expected_exact_families"]).issubset(families)
        assert payload["source_cell_id"] == migrated[key].source_cell_id


def test_seeded_random_validation_is_deterministic_and_counterfactuals_fail_closed(
    frozen_contract: dict, index: dict, migrated: dict
) -> None:
    config = frozen_contract["random_validation"]
    first = pre.deterministic_seed_sample(index, seed=config["seed"], sample_size=config["sample_size"])
    second = pre.deterministic_seed_sample(dict(reversed(list(index.items()))), seed=config["seed"], sample_size=config["sample_size"])
    assert first == second
    assert len(first) == config["sample_size"]
    assert all(pre.reverify_migrated_seed(migrated[key], index[key]).ok for key in first)

    assert pre.classify_request_scope("install quantum flux widget", "other") is pre.SeedClassification.UNSUPPORTED_SCOPE
    assert pre.classify_request_scope("no roof replacement and no structural alteration; inspection only", "residential") is pre.SeedClassification.UNSUPPORTED_SCOPE

    tampered = copy.deepcopy(index["AZ|buckeye|reroof"])
    tampered["tier1"]["main_decision"]["provenance"]["snapshot_hash"] = "0" * 64
    classified = pre.safe_factory_migrate_seed("AZ|buckeye|reroof", tampered)
    assert classified.classification is pre.SeedClassification.FAIL_CLOSED
    assert classified.binary_families == ()


def test_no_neuter_ten_lane_projection_keeps_all_weak_lanes(frozen_contract: dict) -> None:
    families = frozen_contract["w4_ten_lane_families"]
    decisions = tuple(
        pre.normalize_family_decision(
            {
                "family": family,
                "verdict": "REQUIRED" if family == "building" else "VERIFY",
                "trigger": f"{family} review",
                "provenance": [{
                    "source_url": "https://example.gov/rules",
                    "source_quote": "A building permit is required for construction work.",
                    "snapshot_hash": "a" * 64,
                    "snapshot_path": "synthetic://part3",
                    "publishable": True,
                }] if family == "building" else [],
            }
        )
        for family in families
    )
    payload = pre.build_sealed_projection_payload(
        jurisdiction_id="us-ex-exampleville",
        jurisdiction_name="Exampleville",
        state="EX",
        project_family="commercial_tenant_improvement",
        main_decision=decisions[0],
        family_decisions=decisions,
        family_routes=(),
        coverage_status="validated_exact_partial",
        coverage_reason="verified building decision; remaining lanes require verification",
        source_cell_id="synthetic-part3-ten-lane",
        seed_classification=pre.SeedClassification.EXACT_PARTIAL,
    )
    assert [row["family"] for row in payload["family_decisions"]] == families
    assert len(payload["verification_tasks"]) == 9
    assert payload["permit_required"] is True


def test_core_flag_off_remains_byte_exact_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(pre.CORE_SETTING, raising=False)
    monkeypatch.delenv(pre.CORE_ALLOWLIST_SETTING, raising=False)
    original = {"permit_decision": "REQUIRED", "nested": {"families": ["building", "electrical"]}}
    before = pre.response_json_bytes(original)
    after = pre.maybe_attach_core_decision_envelope(
        original,
        job_type="residential remodel",
        city="Albertville",
        state="AL",
        job_category="residential",
    )
    assert after is original
    assert pre.response_json_bytes(after) == before
