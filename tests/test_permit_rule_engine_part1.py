from __future__ import annotations

import copy
import hashlib
import inspect
import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from api import research_engine
from api.permit_rule_engine import (
    ADAPTER_VERSION,
    DECISION_ENVELOPE_VERSION,
    DIVERGENCE_TAXONOMY_VERSION,
    SHADOW_EVENT_VERSION,
    SHADOW_LOG_SETTING,
    SHADOW_SETTING,
    CoverageStatus,
    DivergenceCode,
    DecisionEnvelope,
    build_decision_envelope,
    classify_divergences,
    get_rule_engine_shadow_mode,
    observe_permit_rule_engine_shadow,
    prepare_permit_rule_engine_shadow,
    response_json_bytes,
    stable_sha256,
    to_primitive,
)
from api.v24_decision_cells import resolve_v24_cell
from scripts.generate_permit_rule_engine_part1_evidence import generate

ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "tests/fixtures/permit_rule_engine_part1_frozen_corpus.json"
BASE_SHA = "facea233ac4f821304288f14137e472e92a6fcfc"


def _envelope(city: str, state: str, job_type: str, job_category: str) -> DecisionEnvelope:
    resolution = resolve_v24_cell(city, state, job_type, job_category, force=True)
    return build_decision_envelope(
        resolution,
        job_type=job_type,
        city=city,
        state=state,
        job_category=job_category,
    )


def test_contract_is_typed_versioned_immutable_and_hash_stable() -> None:
    envelope = _envelope("Anchorage", "AK", "commercial tenant improvement", "commercial")
    assert envelope.schema_version == DECISION_ENVELOPE_VERSION
    assert envelope.adapter_version == ADAPTER_VERSION
    assert envelope.authority_context.schema_version == "permitassist.authority-context.v1"
    assert envelope.work_atoms[0].schema_version == "permitassist.work-atom.v1"
    assert envelope.fact_profile.schema_version == "permitassist.fact-profile.v1"
    assert envelope.source_package_manifest_sha256 == hashlib.sha256(
        (ROOT / "knowledge/v24/permitassist_v24_manifest.json").read_bytes()
    ).hexdigest()
    assert stable_sha256(to_primitive(envelope)) == stable_sha256(to_primitive(envelope))
    with pytest.raises(FrozenInstanceError):
        envelope.coverage_reason = "mutated"  # type: ignore[misc]


def test_strict_coverage_distinguishes_complete_partial_and_fail_closed() -> None:
    partial = _envelope("Anchorage", "AK", "commercial tenant improvement", "commercial")
    deep_partial = _envelope(
        "Matanuska-Susitna Borough", "AK", "commercial tenant improvement", "commercial"
    )
    complete = _envelope("Buckeye", "AZ", "residential reroof", "residential")
    fail_closed = _envelope("Yuma", "AZ", "residential remodel", "residential")
    assert partial.coverage_status == CoverageStatus.EXACT_PARTIAL
    assert deep_partial.coverage_status == CoverageStatus.EXACT_PARTIAL
    assert complete.coverage_status == CoverageStatus.EXACT_COMPLETE
    assert fail_closed.coverage_status == CoverageStatus.FAIL_CLOSED
    assert len(partial.family_decisions) == 1
    assert {item.family for item in deep_partial.family_decisions} == {
        "building",
        "electrical",
        "fire",
        "mechanical",
        "occupancy",
        "plumbing",
        "zoning",
    }
    assert fail_closed.main_verdict.value == "ABSTAIN"


def test_shadow_setting_is_off_by_default_and_independent_of_v24_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SHADOW_SETTING, raising=False)
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "off")
    assert get_rule_engine_shadow_mode() == "off"
    assert prepare_permit_rule_engine_shadow("residential reroof", "Buckeye", "AZ", "residential") is None

    monkeypatch.setenv(SHADOW_SETTING, "shadow")
    envelope = prepare_permit_rule_engine_shadow("residential reroof", "Buckeye", "AZ", "residential")
    assert envelope is not None
    assert envelope.coverage_status == CoverageStatus.EXACT_COMPLETE


def test_frozen_contract_corpus_has_flag_off_and_shadow_byte_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    assert corpus["corpus_kind"] == "deterministic_contract_replay"
    assert len(corpus["cases"]) == 12
    for case in corpus["cases"]:
        request = case["request"]
        legacy_result = copy.deepcopy(case["legacy_result"])
        before = response_json_bytes(legacy_result)

        monkeypatch.setenv(SHADOW_SETTING, "off")
        off = prepare_permit_rule_engine_shadow(**request)
        assert observe_permit_rule_engine_shadow(off, legacy_result) is None
        assert response_json_bytes(legacy_result) == before

        monkeypatch.setenv(SHADOW_SETTING, "shadow")
        envelope = prepare_permit_rule_engine_shadow(**request)
        observation = observe_permit_rule_engine_shadow(envelope, legacy_result)
        assert envelope is not None, case["case_id"]
        assert observation is not None, case["case_id"]
        assert observation.taxonomy_version == DIVERGENCE_TAXONOMY_VERSION
        assert observation.divergences
        assert response_json_bytes(legacy_result) == before, case["case_id"]


def test_observation_sink_is_deterministic_and_sink_failure_is_customer_safe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(SHADOW_LOG_SETTING, raising=False)
    envelope = _envelope("Anchorage", "AK", "commercial tenant improvement", "commercial")
    legacy = {"permit_decision": "required", "permit_required": True, "sources": []}
    before = copy.deepcopy(legacy)
    sink = tmp_path / "events.jsonl"
    first = observe_permit_rule_engine_shadow(envelope, legacy, sink_path=sink)
    second = observe_permit_rule_engine_shadow(envelope, legacy, sink_path=sink)
    assert first == second
    lines = sink.read_bytes().splitlines()
    assert len(lines) == 2
    assert lines[0] == lines[1]
    assert legacy == before

    # Relative paths are deliberately rejected inside the shadow-safe boundary.
    assert observe_permit_rule_engine_shadow(envelope, legacy, sink_path="relative.jsonl") == first
    assert legacy == before


def test_divergence_taxonomy_is_structured_and_sorted() -> None:
    envelope = _envelope(
        "Matanuska-Susitna Borough", "AK", "commercial tenant improvement", "commercial"
    )
    legacy = {
        "permit_decision": "not_required",
        "permit_required": False,
        "permits_required": [{"permit_kind": "building", "required": False}],
        "applying_office": "Wrong Office",
        "apply_url": "https://example.test/wrong",
        "sources": [],
    }
    divergences = classify_divergences(envelope, legacy)
    codes = [item.code.value for item in divergences]
    assert codes == sorted(codes)
    assert "DECISION_MISMATCH" in codes
    assert "FAMILY_MISSING_IN_LEGACY" in codes
    assert "AUTHORITY_MISMATCH" in codes
    assert "APPLY_ROUTE_MISMATCH" in codes
    assert "LEGACY_PROVENANCE_GAP" in codes


def test_locked_divergence_taxonomy_and_stage_comparisons_are_executable() -> None:
    locked = {
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
    }
    assert locked <= {item.value for item in DivergenceCode}

    envelope = _envelope(
        "Matanuska-Susitna Borough", "AK", "commercial tenant improvement", "commercial"
    )
    current = {
        "permit_decision": "required",
        "permit_required": True,
        "permits_required": [{"permit_kind": "building", "required": True}],
        "sources": [],
    }
    authoritative = {
        "permit_decision": "required",
        "permit_required": True,
        "permits_required": [
            {"permit_kind": "building", "required": True},
            {"permit_kind": "electrical", "required": True},
        ],
        "sources": [{"url": "https://example.test/official"}],
    }
    public = {
        "permit_decision": "not_required",
        "permit_required": False,
        "permits_required": [],
        "sources": [],
    }
    codes = {
        item.code.value
        for item in classify_divergences(
            envelope,
            current,
            authoritative_result=authoritative,
            public_result=public,
        )
    }
    assert "POST_RECONCILIATION_MUTATION" in codes
    assert "PUBLIC_RENDER_DIVERGENCE" in codes
    assert "COMPANION_CLOSURE_INCOMPLETE" in codes
    assert "AUTHORITATIVE_CELL_NOT_INJECTED" in codes

    invalid = replace(
        envelope,
        validation_ok=False,
        validation_issue_codes=("SYNTHETIC_CONTRACT_CONFLICT",),
    )
    assert "STALE_OR_CONFLICTING_RULE" in {
        item.code.value for item in classify_divergences(invalid, current)
    }

    unsupported = _envelope("Anchorage", "AK", "ground mount solar array", "commercial")
    unsupported_codes = {item.code.value for item in classify_divergences(unsupported, current)}
    assert "SCOPE_TAXONOMY_UNSUPPORTED" in unsupported_codes
    assert "PROJECT_FAMILY_NOT_COVERED" in unsupported_codes


def test_research_path_adapter_is_observational_and_wraps_both_return_paths() -> None:
    source = inspect.getsource(research_engine.research_permit)
    assert source.index("prepare_permit_rule_engine_shadow") < source.index("key = cache_key")
    assert source.count("observe_permit_rule_engine_shadow") == 2
    assert "result.update(rule_engine_shadow" not in source
    assert "cached.update(rule_engine_shadow" not in source
    assert "save_cache(key, job_type, job_category, city, state, zip_code, result)" in source
    assert source.rindex("observe_permit_rule_engine_shadow") > source.rindex("save_cache(")
    assert source.count("authoritative_result=rule_engine_authoritative_result") == 2


def test_research_permit_cached_path_is_byte_identical_off_and_shadowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base_payload = {
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_decision": "required",
        "permit_name": "Building Permit",
        "permit_type": "Building Permit",
        "permits_required": [{"permit_type": "Building Permit", "kind": "building", "required": True}],
        "apply_url": "https://www.muni.org/Departments/OCPD/development-services/permits-inspections/Pages/default.aspx",
        "applying_office": "Municipality of Anchorage",
        "sources": [
            {
                "url": "https://www.muni.org/Departments/OCPD/development-services/permits-inspections/Pages/default.aspx",
                "quote": "Synthetic contract replay only.",
            }
        ],
        "confidence": "high",
        "confidence_reason": "Frozen contract replay.",
        "summary": "Frozen contract replay.",
        "warnings": [],
    }

    monkeypatch.setattr(research_engine, "init_cache", lambda: None)
    monkeypatch.setattr(
        research_engine,
        "get_cached",
        lambda _key, _refresh_callback=None: copy.deepcopy(base_payload),
    )
    monkeypatch.setattr(
        research_engine,
        "enrich_result_with_serper_sources",
        lambda result, _job_type, _city, _state: result,
    )
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "off")
    sink = tmp_path / "research-path-shadow.jsonl"
    monkeypatch.setenv("PERMITASSIST_RULE_ENGINE_SHADOW_LOG", str(sink))

    monkeypatch.setenv("PERMITASSIST_RULE_ENGINE_SHADOW", "off")
    flag_off = research_engine.research_permit(
        "commercial tenant improvement",
        "Anchorage",
        "AK",
        use_cache=True,
        job_category="commercial",
    )
    assert not sink.exists()

    monkeypatch.setenv("PERMITASSIST_RULE_ENGINE_SHADOW", "shadow")
    shadowed = research_engine.research_permit(
        "commercial tenant improvement",
        "Anchorage",
        "AK",
        use_cache=True,
        job_category="commercial",
    )

    assert response_json_bytes(flag_off) == response_json_bytes(shadowed)
    observations = sink.read_text(encoding="utf-8").splitlines()
    assert len(observations) == 1
    assert json.loads(observations[0])["schema_version"] == SHADOW_EVENT_VERSION


def test_research_permit_fresh_path_and_cache_write_are_byte_identical_off_and_shadowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model_payload = {
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_decision": "required",
        "permit_name": "Building Permit",
        "permit_type": "Building Permit",
        "permits_required": [
            {"permit_type": "Building Permit", "kind": "building", "required": True}
        ],
        "apply_url": "https://www.muni.org/Departments/OCPD/development-services/permits-inspections/Pages/default.aspx",
        "applying_office": "Municipality of Anchorage",
        "sources": [
            {
                "url": "https://www.muni.org/Departments/OCPD/development-services/permits-inspections/Pages/default.aspx",
                "quote": "Synthetic fresh-path contract replay only.",
            }
        ],
        "confidence": "high",
        "confidence_reason": "Synthetic fresh-path contract replay.",
        "summary": "Synthetic fresh-path contract replay.",
        "warnings": [],
    }

    class FakeClient:
        def with_options(self, **_kwargs: object) -> "FakeClient":
            return self

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> "FrozenDateTime":
            return cls(2026, 7, 12, 1, 0, 0, tzinfo=tz)  # type: ignore[arg-type]

        @classmethod
        def utcnow(cls) -> "FrozenDateTime":
            return cls(2026, 7, 12, 1, 0, 0)

    model_calls: list[dict[str, object]] = []
    cache_writes: list[dict[str, object]] = []

    def fake_completion(_client: object, **kwargs: object) -> SimpleNamespace:
        model_calls.append(dict(kwargs))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(model_payload)))]
        )

    def capture_cache(
        _key: str,
        _job_type: str,
        _job_category: str,
        _city: str,
        _state: str,
        _zip_code: str | None,
        result: dict[str, object],
    ) -> None:
        cache_writes.append(copy.deepcopy(result))

    monkeypatch.setattr(research_engine, "init_cache", lambda: None)
    monkeypatch.setattr(research_engine, "build_search_context", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        research_engine,
        "enrich_result_with_serper_sources",
        lambda result, _job_type, _city, _state: result,
    )
    monkeypatch.setattr(research_engine, "_get_openai_client", lambda: FakeClient())
    monkeypatch.setattr(research_engine, "create_permitassist_chat_completion", fake_completion)
    monkeypatch.setattr(research_engine, "save_cache", capture_cache)
    monkeypatch.setattr(research_engine.time, "time", lambda: 1_700_000_000.0)
    monkeypatch.setattr(research_engine, "datetime", FrozenDateTime)
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "off")
    sink = tmp_path / "fresh-path-shadow.jsonl"
    monkeypatch.setenv("PERMITASSIST_RULE_ENGINE_SHADOW_LOG", str(sink))

    monkeypatch.setenv("PERMITASSIST_RULE_ENGINE_SHADOW", "off")
    flag_off = research_engine.research_permit(
        "commercial tenant improvement",
        "Anchorage",
        "AK",
        use_cache=False,
        job_category="commercial",
    )
    assert not sink.exists()

    monkeypatch.setenv("PERMITASSIST_RULE_ENGINE_SHADOW", "shadow")
    shadowed = research_engine.research_permit(
        "commercial tenant improvement",
        "Anchorage",
        "AK",
        use_cache=False,
        job_category="commercial",
    )

    assert response_json_bytes(flag_off) == response_json_bytes(shadowed)
    assert len(cache_writes) == 2
    assert response_json_bytes(cache_writes[0]) == response_json_bytes(cache_writes[1])
    assert len(model_calls) == 2, "shadow mode must not add a model call"
    observations = sink.read_text(encoding="utf-8").splitlines()
    assert len(observations) == 1
    assert json.loads(observations[0])["schema_version"] == SHADOW_EVENT_VERSION


def test_complete_census_is_reproducible_and_preserves_baseline_failures(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first = generate(ROOT, first_dir, CORPUS_PATH, BASE_SHA)
    second = generate(ROOT, second_dir, CORPUS_PATH, BASE_SHA)

    assert first == second
    assert first["cell_count"] == 2162
    assert first["all_cells_classified"] is True
    assert first["portable_request_validation_failure_count"] == 0
    assert sum(first["strict_classification_counts"].values()) == 2162
    assert first["strict_classification_counts"]["FAIL_CLOSED"] == 34
    assert first["legacy_tier1_complete_count"] == 2128
    assert first["strict_exact_complete_count"] < first["legacy_tier1_complete_count"]
    assert first["replay"]["corpus_case_count"] == 12
    assert first["replay"]["shadow_envelope_coverage_percent"] == 100.0
    assert first["replay"]["customer_payload_mutation_count"] == 0
    assert first["replay"]["byte_parity_case_count"] == 12

    first_manifest = json.loads((first_dir / "manifest.json").read_text(encoding="utf-8"))
    second_manifest = json.loads((second_dir / "manifest.json").read_text(encoding="utf-8"))
    assert first_manifest == second_manifest
    assert first_manifest["artifact_set_sha256"] == stable_sha256(first_manifest["files"])
    assert sum(1 for _ in (first_dir / "cell_census.jsonl").open(encoding="utf-8")) == 2162
    assert sum(1 for _ in (first_dir / "frozen_replay_envelopes.jsonl").open(encoding="utf-8")) == 12
    assert hashlib.sha256((first_dir / "cell_census.jsonl").read_bytes()).hexdigest() == first_manifest["files"]["cell_census.jsonl"]["sha256"]


def test_part1_module_contains_no_part2_execution_surface() -> None:
    source = (ROOT / "api/permit_rule_engine.py").read_text(encoding="utf-8")
    prohibited = (
        "replace_legacy_decision",
        "promote_shadow_decision",
        "write_back_decision_cell",
        "mutate_customer_result",
        "rule_engine_rollout_percentage",
    )
    assert all(token not in source for token in prohibited)
