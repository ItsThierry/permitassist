from __future__ import annotations

import copy
import json
import sqlite3

from api import permit_rule_engine as pre
from api import research_engine
from api.v24_decision_cells import V24Resolution, V24ResolutionStatus, resolve_v24_cell


def _active_buckeye_result(monkeypatch):
    monkeypatch.setenv(pre.CORE_SETTING, "active")
    resolution = resolve_v24_cell(
        "Buckeye",
        "AZ",
        "residential reroof",
        "residential",
        force=True,
    )
    assert resolution.cell is not None
    jurisdiction_id = str(resolution.cell["jurisdiction_id"])
    monkeypatch.setenv(pre.CORE_ALLOWLIST_SETTING, jurisdiction_id)
    envelope = pre.build_core_decision_envelope(
        resolution,
        job_type="residential reroof",
        city="Buckeye",
        state="AZ",
        job_category="residential",
    )
    return pre.attach_core_decision_envelope({"legacy": "ignored"}, envelope)


def test_active_cache_namespace_is_versioned_but_flag_off_key_is_unchanged(monkeypatch) -> None:
    monkeypatch.delenv(pre.CORE_SETTING, raising=False)
    monkeypatch.delenv(pre.CORE_ALLOWLIST_SETTING, raising=False)
    baseline = research_engine.cache_key("residential reroof", "Buckeye", "AZ", "residential")
    assert pre.core_cache_schema_for_request("Buckeye", "AZ") is None
    assert research_engine.cache_key(
        "residential reroof",
        "Buckeye",
        "AZ",
        "residential",
        rule_engine_cache_schema_version=None,
    ) == baseline

    active = _active_buckeye_result(monkeypatch)
    del active
    required = pre.core_cache_schema_for_request("Buckeye", "AZ")
    assert required == pre.CORE_CACHE_SCHEMA_VERSION
    active_key = research_engine.cache_key(
        "residential reroof",
        "Buckeye",
        "AZ",
        "residential",
        rule_engine_cache_schema_version=required,
    )
    assert active_key != baseline


def test_active_cache_read_rejects_stale_and_tampered_rows(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(research_engine, "CACHE_DB", str(tmp_path / "permit-cache.db"))
    monkeypatch.setattr(research_engine, "_capture_validators", lambda *_args, **_kwargs: ("", ""))
    research_engine.init_cache()
    key = "rule-engine-part2-stale"
    stale = {"_cache_schema_version": research_engine.FILING_PACKET_CACHE_SCHEMA_VERSION}
    conn = sqlite3.connect(research_engine.CACHE_DB)
    conn.execute(
        "INSERT INTO permit_cache (cache_key, job_type, job_category, city, state, zip_code, result_json, created_at, hits) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (key, "residential reroof", "residential", "Buckeye", "AZ", "", json.dumps(stale), "2099-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()

    assert research_engine.get_cached(
        key,
        required_rule_engine_cache_version=pre.CORE_CACHE_SCHEMA_VERSION,
    ) is None
    conn = sqlite3.connect(research_engine.CACHE_DB)
    assert conn.execute("SELECT COUNT(*) FROM permit_cache WHERE cache_key = ?", [key]).fetchone()[0] == 0
    conn.close()

    valid = _active_buckeye_result(monkeypatch)
    valid["_cache_schema_version"] = research_engine.FILING_PACKET_CACHE_SCHEMA_VERSION
    research_engine.save_cache(
        key,
        "residential reroof",
        "residential",
        "Buckeye",
        "AZ",
        "",
        valid,
    )
    loaded = research_engine.get_cached(
        key,
        required_rule_engine_cache_version=pre.CORE_CACHE_SCHEMA_VERSION,
    )
    assert loaded is not None
    assert pre.validate_rule_engine_cache_payload(loaded, required_version=pre.CORE_CACHE_SCHEMA_VERSION)

    tampered = copy.deepcopy(valid)
    tampered["_permit_rule_engine_core"]["sealed_projection"]["payload_json"] = "{}"
    assert not pre.validate_rule_engine_cache_payload(tampered, required_version=pre.CORE_CACHE_SCHEMA_VERSION)


def test_core_off_attachment_is_same_object_and_same_bytes(monkeypatch) -> None:
    monkeypatch.delenv(pre.CORE_SETTING, raising=False)
    monkeypatch.delenv(pre.CORE_ALLOWLIST_SETTING, raising=False)
    payload = {"permit_decision": "required", "permit_required": True, "rows": [{"b": 2, "a": 1}]}
    before = pre.response_json_bytes(payload)
    returned = pre.maybe_attach_core_decision_envelope(
        payload,
        job_type="residential reroof",
        city="Buckeye",
        state="AZ",
        job_category="residential",
    )
    assert returned is payload
    assert pre.response_json_bytes(returned) == before


def test_exact_fail_closed_abstains_every_family_and_customer_mirror() -> None:
    provenance = [{
        "source_url": "https://example.gov/building",
        "source_quote": "A building permit is required.",
        "snapshot_hash": "a" * 64,
        "snapshot_path": "synthetic://fail-closed",
        "publishable": True,
    }]
    cell = {
        "status": "FAIL_CLOSED",
        "cell_id": "synthetic-fail-closed",
        "jurisdiction_id": "us-ex-exampleville",
        "ahj": "Exampleville",
        "state": "EX",
        "project_family": "residential_remodel",
        "scope": "residential remodel",
        "tier1": {
            "main_decision": {"value": "REQUIRED", "provenance": provenance},
            "permits_required": [
                {
                    "permit_kind": "building",
                    "required_status": "REQUIRED",
                    "trigger": "residential remodel",
                    "provenance": provenance,
                },
                {
                    "permit_kind": "electrical",
                    "required_status": "NOT_REQUIRED",
                    "trigger": "no electrical work",
                    "provenance": provenance,
                },
            ],
        },
    }
    envelope = pre.build_core_decision_envelope(
        V24Resolution(
            V24ResolutionStatus.EXACT_CELL_FAIL_CLOSED,
            cell=cell,
            key="EX|exampleville|residential_remodel",
            reason="synthetic fail-closed regression",
        ),
        job_type="residential remodel",
        city="Exampleville",
        state="EX",
        job_category="residential",
    )
    projection = json.loads(envelope.sealed_projection.payload_json)

    assert envelope.precedence_stage is pre.PrecedenceStage.EXACT_FAIL_CLOSED
    assert envelope.main_decision.verdict is pre.FamilyVerdict.ABSTAIN
    assert envelope.family_decisions
    assert all(decision.verdict is pre.FamilyVerdict.ABSTAIN for decision in envelope.family_decisions)
    assert all("exact_cell_fail_closed" in decision.validation_issue_codes for decision in envelope.family_decisions)
    assert projection["permit_required"] is None
    assert all(row["required_status"] == "NEEDS_INPUT" for row in projection["permits_required"])
    assert all(row["required"] is None for row in projection["permits_required"])


def test_customer_authority_routes_redact_internal_provenance_metadata() -> None:
    decision = pre.normalize_family_decision(
        {
            "family": "building",
            "verdict": "REQUIRED",
            "trigger": "building work",
            "provenance": [{
                "source_url": "https://example.gov/building",
                "source_quote": "A building permit is required.",
                "snapshot_hash": "b" * 64,
                "snapshot_path": "internal/snapshot.json",
                "publishable": True,
                "authority_tier": "TIER1_COMPLETE",
                "handled_by_local_ahj": True,
            }],
        }
    )
    routes = pre.build_family_authority_routes(
        {
            "tier1": {
                "trade_authority": [{
                    "permit_family": "building",
                    "issuing_authority": "Exampleville Building Department",
                    "application_authority": "Exampleville Permit Center",
                    "authority_tier": "TIER1_COMPLETE",
                    "handled_by_local_ahj": True,
                }],
                "apply": [{
                    "permit_name": "Building Permit",
                    "office_name": "Exampleville Permit Center",
                    "apply_url": "https://example.gov/building/apply",
                    "channel": "online",
                    "provenance": {
                        "source_url": "https://example.gov/building/apply",
                        "source_quote": "Apply online.",
                        "snapshot_hash": "c" * 64,
                        "snapshot_path": "internal/apply.json",
                        "publishable": True,
                    },
                }],
            },
        }
    )
    payload = pre.build_sealed_projection_payload(
        jurisdiction_id="us-ex-exampleville",
        jurisdiction_name="Exampleville",
        state="EX",
        project_family="residential_remodel",
        main_decision=decision,
        family_decisions=(decision,),
        family_routes=routes,
        coverage_status="validated_exact_complete",
        coverage_reason="complete",
        source_cell_id="synthetic-complete",
    )
    serialized = json.dumps(payload["family_authority_routes"], sort_keys=True)

    assert "https://example.gov/building/apply" in serialized
    for forbidden in ("snapshot_path", "publishable", "authority_tier", "handled_by_local_ahj"):
        assert forbidden not in serialized


def test_active_research_permit_cache_path_serves_validated_sealed_projection(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(research_engine, "CACHE_DB", str(tmp_path / "active-research-cache.db"))
    monkeypatch.setattr(research_engine, "_capture_validators", lambda *_args, **_kwargs: ("", ""))
    monkeypatch.setattr(research_engine, "prepare_permit_rule_engine_shadow", lambda *_args, **_kwargs: None)
    for name in (
        "apply_scope_aware_permit_classification",
        "apply_office_ti_rulebook",
        "apply_medical_clinic_ti_rulebook",
        "enforce_ti_min_permits_floor",
        "enforce_commercial_primary_permit_guardrail",
        "repair_residential_home_office_commercial_leak",
        "validate_and_sanitize_permit_result",
        "scrub_hidden_trigger_internal_metadata",
        "apply_state_expert_pack",
        "hedge_companion_permits",
        "enrich_result_with_serper_sources",
        "apply_source_locality_hard_block",
        "apply_fee_verify_caveat",
        "apply_rulebook_depth",
        "sanitize_non_food_office_breakroom_text",
        "reconcile_v231_result",
        "reconcile_authoritative_result",
    ):
        monkeypatch.setattr(research_engine, name, lambda *_args, **_kwargs: None)
    monkeypatch.setattr(research_engine, "ensure_required_filing_rows", lambda *_args, **_kwargs: {})

    wrapped = _active_buckeye_result(monkeypatch)
    wrapped.update(
        {
            "_cache_schema_version": research_engine.FILING_PACKET_CACHE_SCHEMA_VERSION,
            "missing_fields": [],
            "needs_review": False,
            "confidence_reason": "sealed cache fixture",
            "sources": [],
            "companion_permits": [],
            "checklist": [],
            "rejection_patterns": [],
            "permit_ready_score": 100,
            "fee_calculator": {},
        }
    )
    research_engine.init_cache()
    cache_version = pre.core_cache_schema_for_request("Buckeye", "AZ")
    key = research_engine.cache_key(
        "residential reroof",
        "Buckeye",
        "AZ",
        "residential",
        rule_engine_cache_schema_version=cache_version,
    )
    research_engine.save_cache(
        key,
        "residential reroof",
        "residential",
        "Buckeye",
        "AZ",
        "",
        wrapped,
    )

    served = research_engine.research_permit(
        "residential reroof",
        "Buckeye",
        "AZ",
        use_cache=True,
        job_category="residential",
    )
    projection = pre.extract_sealed_public_projection(served, city="Buckeye", state="AZ")

    assert served["_cached"] is True
    assert pre.validate_rule_engine_cache_payload(served, required_version=pre.CORE_CACHE_SCHEMA_VERSION)
    assert projection is not None
    assert projection["decision_source"] == "sealed_permit_rule_engine_envelope"
    assert "legacy" not in json.dumps(projection, sort_keys=True)
