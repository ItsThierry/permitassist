from __future__ import annotations

import copy
import json
from datetime import datetime
from types import SimpleNamespace

from api import permit_rule_engine as pre
from api import research_engine
from api.v24_decision_cells import load_v24_index


def test_pre_activation_family_audit_preserves_every_binary_family_and_route() -> None:
    audit = pre.audit_pre_activation_family_preservation()
    assert audit["checked_cells"] == 2118
    assert audit["binary_family_occurrences"] > 0
    assert audit["violation_count"] == 0
    assert audit["violations"] == []
    assert audit["passed"] is True


def test_sourced_predicates_fail_unknown_and_evaluate_only_with_fact_and_source() -> None:
    predicate = next(iter(pre.minimum_sourced_predicates().values()))
    assert pre.evaluate_sourced_predicate(predicate, {}) is None
    assert pre.evaluate_sourced_predicate(
        predicate,
        {"project_family": predicate.expected_value},
    ) is True
    assert pre.evaluate_sourced_predicate(
        predicate,
        {"project_family": "unsupported"},
    ) is False


def test_factory_promotion_requires_local_authority_evidence() -> None:
    index = load_v24_index() or {}
    source = copy.deepcopy(index["AZ|buckeye|reroof"])
    candidate = pre.build_fail_closed_factory_seed(
        jurisdiction_id="us-ex-exampleville",
        ahj_name="Exampleville",
        state="EX",
        project_family="reroof",
        source_index_key="EX|exampleville|reroof",
    )
    source["tier1"]["trade_authority"] = []
    source["tier1"]["apply"] = []
    source["tier1"]["permits_required"][0]["issuing_authority"] = ""
    source["tier1"]["permits_required"][0]["application_authority"] = ""
    source["tier1"]["permits_required"][0]["applying_office"] = ""
    source["tier1"]["permits_required"][0]["apply_url"] = ""
    assert pre.promote_factory_seed(candidate, source_cell=source).classification is pre.SeedClassification.FAIL_CLOSED


def test_active_non_cached_research_path_writes_and_serves_validated_sealed_projection(
    monkeypatch,
) -> None:
    model_payload = {
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_decision": "required",
        "permit_name": "Building Permit",
        "permit_type": "Building Permit",
        "permits_required": [{"permit_type": "Building Permit", "kind": "building", "required": True}],
        "apply_url": "https://www.buckeyeaz.gov/government/development-services/building-safety",
        "applying_office": "City of Buckeye Development Services",
        "sources": [{"url": "https://www.buckeyeaz.gov/government/development-services/building-safety", "quote": "Synthetic non-cached integration replay."}],
        "confidence": "high",
        "confidence_reason": "Synthetic non-cached integration replay.",
        "summary": "Synthetic non-cached integration replay.",
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
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(model_payload)))])

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
    monkeypatch.setattr(research_engine, "enrich_result_with_serper_sources", lambda result, *_args: result)
    monkeypatch.setattr(research_engine, "_get_openai_client", lambda: FakeClient())
    monkeypatch.setattr(research_engine, "create_permitassist_chat_completion", fake_completion)
    monkeypatch.setattr(research_engine, "save_cache", capture_cache)
    monkeypatch.setattr(research_engine.time, "time", lambda: 1_700_000_000.0)
    monkeypatch.setattr(research_engine, "datetime", FrozenDateTime)
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "off")
    monkeypatch.setenv(pre.CORE_SETTING, "active")
    monkeypatch.setenv(pre.CORE_ALLOWLIST_SETTING, "us-az-buckeye")

    result = research_engine.research_permit(
        "residential reroof",
        "Buckeye",
        "AZ",
        use_cache=False,
        job_category="residential",
    )
    projection = pre.extract_sealed_public_projection(result, city="Buckeye", state="AZ")

    assert len(model_calls) == 1
    assert len(cache_writes) == 1
    assert projection is not None
    assert projection["seed_classification"] == "exact_complete"
    assert projection["permit_decision"] in {"REQUIRED", "NOT_REQUIRED"}
    assert pre.validate_rule_engine_cache_payload(
        cache_writes[0],
        required_version=pre.CORE_CACHE_SCHEMA_VERSION,
    )
    assert pre.extract_sealed_public_projection(cache_writes[0], city="Buckeye", state="AZ") == projection
