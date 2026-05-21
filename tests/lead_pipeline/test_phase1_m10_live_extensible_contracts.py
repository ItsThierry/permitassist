"""Phase 1 M10 live-extensible contracts under fixture-only safety boundaries."""

from __future__ import annotations

import json
import sqlite3

import pytest

from lead_pipeline.assembly import AssemblyFixtureRecord, assemble_entities_and_facts
from lead_pipeline.connectors import (
    FetchMode,
    FixtureSearchCandidate,
    FixtureSearchDiscoveryConnector,
    FIXTURE_ROBOTS_OR_TERMS_CLASSIFICATION,
    LiveFetchAttemptError,
    SearchDiscoveryConnector,
    SearchDiscoveryFixtureQuery,
    run_fixture_search_discovery,
)
from lead_pipeline.contracts import (
    ExportEligibility,
    GateStatus,
    PageType,
    PromotionTier,
    RobotsTermsPrefetchStatus,
    SourceClass,
    identity_merge_key_order,
)
from lead_pipeline.event_writer import initialize_sqlite_schema, write_connector_run_result
from lead_pipeline.phase1_runner import BatchCreditCeiling, Phase1RunnerSafetyError, enforce_batch_credit_ceiling
from lead_pipeline.promotion import (
    SEARCH_ONLY_HOLD_SCORE,
    UNTRUSTED_OFFICIAL_OR_FIRST_PARTY_HOLD_SCORE,
    evaluate_entity_promotion,
    promote_entity,
)
from lead_pipeline.review_artifacts import InternalReviewArtifactSafetyError, render_internal_review_artifacts
from lead_pipeline.schema import PHASE1_SCHEMA_VERSION, get_table_contract

M10_BATCH_ID = "batch_fixture_m10"
NOW = "2026-05-20T00:00:00Z"


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    initialize_sqlite_schema(conn)
    return conn


def _batch() -> dict[str, object]:
    return {
        "batch_id": M10_BATCH_ID,
        "approved_scope_ref": "phase1_fixture_only_lead_pipeline_m10_live_extensible_contracts",
        "adapter_id": "permitassist",
        "started_at_utc": NOW,
        "status": "open",
        "schema_version": PHASE1_SCHEMA_VERSION,
    }


def _fact_id(conn: sqlite3.Connection, entity_id: str, fact_type: str) -> str:
    row = conn.execute(
        "SELECT fact_id FROM facts WHERE entity_id = ? AND fact_type = ?",
        (entity_id, fact_type),
    ).fetchone()
    assert row is not None
    return str(row[0])


def _insert_verification_pass(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    fact_id: str,
    gate_name: str,
    batch_id: str = M10_BATCH_ID,
) -> str:
    event_id = f"ve_m10_{gate_name}_{len(list(conn.execute('SELECT 1 FROM verification_events')))}"
    conn.execute(
        "INSERT INTO verification_events(verification_event_id, batch_id, target_entity_id, target_fact_id, "
        "gate_name, gate_version, input_hash, result_status, score, reason_codes, network_used_flag, "
        "cached_result_flag, observed_at_utc, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1.0, ?, 0, 0, ?, ?)",
        (
            event_id,
            batch_id,
            entity_id,
            fact_id,
            gate_name,
            "m10_fixture_gate_v1",
            f"hash_{event_id}",
            GateStatus.PASS_.value,
            json.dumps(["m10_fixture_pass"], sort_keys=True),
            NOW,
            PHASE1_SCHEMA_VERSION,
        ),
    )
    conn.commit()
    return event_id


def _insert_contact_passes(conn: sqlite3.Connection, entity_id: str, *, batch_id: str = M10_BATCH_ID) -> str:
    email_fact_id = _fact_id(conn, entity_id, "contact_email")
    for gate_name in ("contact_observation_gate", "email_syntax_gate", "domain_quality_gate"):
        _insert_verification_pass(conn, entity_id=entity_id, fact_id=email_fact_id, gate_name=gate_name, batch_id=batch_id)
    return email_fact_id


def _insert_clear_suppression(conn: sqlite3.Connection, *, entity_id: str, fact_id: str, batch_id: str = M10_BATCH_ID) -> str:
    event_id = f"sup_m10_{entity_id}"
    conn.execute(
        "INSERT INTO suppression_events(suppression_event_id, batch_id, target_entity_id, target_fact_id, "
        "suppression_source_ref, suppression_snapshot_hash, status, reason, checked_at_utc, schema_version) "
        "VALUES (?, ?, ?, ?, ?, ?, 'clear', 'clear', ?, ?)",
        (
            event_id,
            batch_id,
            entity_id,
            fact_id,
            "fixture://suppression/m10-zero-live-spend",
            f"hash_{event_id}",
            NOW,
            PHASE1_SCHEMA_VERSION,
        ),
    )
    conn.commit()
    return event_id


def _insert_enrichment(conn: sqlite3.Connection, *, entity_id: str, batch_id: str = M10_BATCH_ID) -> str:
    fact_ids = [row[0] for row in conn.execute("SELECT fact_id FROM facts WHERE entity_id = ?", (entity_id,))]
    observation_ids = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT source_observation_id FROM facts WHERE entity_id = ?",
            (entity_id,),
        )
    ]
    event_id = f"enrich_m10_{entity_id}"
    conn.execute(
        "INSERT INTO enrichment_events(enrichment_event_id, batch_id, entity_id, input_fact_ids, "
        "input_observation_ids, model_or_rule_version, output_json, quality_score, unsupported_claim_count, "
        "validator_status, validator_reason_codes, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, 1.0, 0, ?, ?, ?)",
        (
            event_id,
            batch_id,
            entity_id,
            json.dumps(fact_ids, sort_keys=True),
            json.dumps(observation_ids, sort_keys=True),
            "m10_fixture_rule_v1",
            json.dumps({"summary": "Search-only fixture enrichment must still not promote."}, sort_keys=True),
            GateStatus.PASS_.value,
            json.dumps(["m10_source_backed_fixture"], sort_keys=True),
            PHASE1_SCHEMA_VERSION,
        ),
    )
    conn.commit()
    return event_id


def _search_query() -> SearchDiscoveryFixtureQuery:
    return SearchDiscoveryFixtureQuery(
        query="commercial tenant improvement contractors fixture city",
        fetched_at_utc=NOW,
        candidates=(
            FixtureSearchCandidate(
                title="Fixture Search Build Group - commercial TI",
                url_or_path="fixture://search-discovery/results/fixture-search-build-group",
                snippet="Search result says Fixture Search Build Group performs commercial tenant improvements.",
                page_type=PageType.SEARCH_RESULT,
            ),
        ),
    )


def _insert_source_observation(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    observation_id: str,
    source_class: SourceClass,
    page_type: PageType,
    official_or_first_party_flag: int = 0,
    snippet: str = "Fixture source says the contractor performs commercial tenant improvements.",
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO batches(batch_id, approved_scope_ref, adapter_id, started_at_utc, status, schema_version) "
        "VALUES (?, ?, 'permitassist', ?, 'open', ?)",
        (M10_BATCH_ID, "phase1_fixture_only_lead_pipeline_m10_live_extensible_contracts", NOW, PHASE1_SCHEMA_VERSION),
    )
    conn.execute(
        "INSERT INTO sources(source_id, source_class, source_name, base_url_or_path, official_or_first_party_flag, "
        "terms_notes, robots_notes, terms_prefetch_status, robots_prefetch_status, requires_login, paid_flag, "
        "allowed_phase, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)",
        (
            source_id,
            source_class.value,
            f"Fixture {source_class.value}",
            f"fixture://{source_class.value}/{source_id}",
            official_or_first_party_flag,
            "fixture-only terms placeholder",
            "fixture-only robots placeholder",
            RobotsTermsPrefetchStatus.ALLOWED_FIXTURE_ONLY.value,
            RobotsTermsPrefetchStatus.ALLOWED_FIXTURE_ONLY.value,
            "phase1_fixture_only",
            PHASE1_SCHEMA_VERSION,
        ),
    )
    conn.execute(
        "INSERT INTO source_observations(observation_id, source_id, batch_id, observed_at_utc, url_or_path, "
        "content_type, payload_hash_sha256, snippet_or_excerpt, extractor_version, page_type, "
        "robots_or_terms_classification, terms_prefetch_status, robots_prefetch_status, blocked_or_captcha_flag, "
        "schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
        (
            observation_id,
            source_id,
            M10_BATCH_ID,
            NOW,
            f"fixture://{source_class.value}/{source_id}/profile",
            "text/plain",
            f"hash_{observation_id}",
            snippet,
            "m10_fixture_test",
            page_type.value,
            FIXTURE_ROBOTS_OR_TERMS_CLASSIFICATION,
            RobotsTermsPrefetchStatus.ALLOWED_FIXTURE_ONLY.value,
            RobotsTermsPrefetchStatus.ALLOWED_FIXTURE_ONLY.value,
            PHASE1_SCHEMA_VERSION,
        ),
    )


def _insert_fact(
    conn: sqlite3.Connection,
    *,
    fact_id: str,
    entity_id: str,
    fact_type: str,
    fact_value: str,
    observation_id: str,
    snippet: str = "Fixture source says the contractor performs commercial tenant improvements.",
) -> None:
    conn.execute(
        "INSERT INTO facts(fact_id, entity_id, fact_type, fact_value, normalized_value, confidence, promotion_status, "
        "source_observation_id, field_relevant_snippet, valid_from_observed_at_utc, schema_version) "
        "VALUES (?, ?, ?, ?, ?, 1.0, ?, ?, ?, ?, ?)",
        (
            fact_id,
            entity_id,
            fact_type,
            fact_value,
            fact_value.lower() if fact_type != "contact_email" else fact_value.lower(),
            PromotionTier.RAW_DISCOVERY.value,
            observation_id,
            snippet,
            NOW,
            PHASE1_SCHEMA_VERSION,
        ),
    )


def test_m10_contract_enums_and_schema_fields_exist_for_page_type_and_prefetch():
    assert {item.value for item in PageType} == {
        "first_party_homepage",
        "first_party_services",
        "first_party_contact",
        "official_registry",
        "search_result",
        "aggregator_directory",
        "blocked_unknown",
    }
    assert {item.value for item in RobotsTermsPrefetchStatus} == {
        "allowed_fixture_only",
        "allowed_public",
        "conditional_review_required",
        "blocked",
    }

    source_columns = get_table_contract("sources").columns
    observation_columns = get_table_contract("source_observations").columns
    assert "terms_prefetch_status" in source_columns
    assert "robots_prefetch_status" in source_columns
    assert "page_type" in observation_columns
    assert "terms_prefetch_status" in observation_columns
    assert "robots_prefetch_status" in observation_columns
    assert "raw_result_ref" in observation_columns


@pytest.mark.parametrize("column", ["terms_prefetch_status", "robots_prefetch_status"])
def test_m10_source_prefetch_status_fields_are_schema_checked_enums(column: str):
    conn = _connection()
    payload = {
        "source_id": f"src_bad_{column}",
        "source_class": SourceClass.FIRST_PARTY_WEBSITE.value,
        "source_name": "Bad enum source",
        "base_url_or_path": "fixture://bad-enum/",
        "official_or_first_party_flag": 1,
        "requires_login": 0,
        "paid_flag": 0,
        "allowed_phase": "phase1_fixture_only",
        "schema_version": PHASE1_SCHEMA_VERSION,
        column: "not_a_prefetch_status",
    }

    columns = ", ".join(payload)
    placeholders = ", ".join("?" for _ in payload)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"INSERT INTO sources({columns}) VALUES ({placeholders})", tuple(payload.values()))


@pytest.mark.parametrize("column,bad_value", [
    ("page_type", "not_a_page_type"),
    ("terms_prefetch_status", "not_a_prefetch_status"),
    ("robots_prefetch_status", "not_a_prefetch_status"),
])
def test_m10_source_observation_m10_fields_are_schema_checked_enums(column: str, bad_value: str):
    conn = _connection()
    conn.execute("INSERT INTO batches(batch_id, approved_scope_ref, started_at_utc, status) VALUES (?, ?, ?, ?)",
                 (M10_BATCH_ID, "m10_enum_regression", NOW, "open"))
    conn.execute(
        "INSERT INTO sources(source_id, source_class, source_name, base_url_or_path, official_or_first_party_flag, "
        "requires_login, paid_flag, allowed_phase, schema_version) VALUES (?, ?, ?, ?, 1, 0, 0, ?, ?)",
        ("src_m10_enum", SourceClass.FIRST_PARTY_WEBSITE.value, "M10 enum source", "fixture://m10-enum/",
         "phase1_fixture_only", PHASE1_SCHEMA_VERSION),
    )
    payload = {
        "observation_id": f"obs_bad_{column}",
        "source_id": "src_m10_enum",
        "batch_id": M10_BATCH_ID,
        "observed_at_utc": NOW,
        "url_or_path": "fixture://m10-enum/contact",
        "payload_hash_sha256": "0" * 64,
        "snippet_or_excerpt": "Bad enum fixture row.",
        "blocked_or_captcha_flag": 0,
        "schema_version": PHASE1_SCHEMA_VERSION,
        column: bad_value,
    }
    columns = ", ".join(payload)
    placeholders = ", ".join("?" for _ in payload)

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"INSERT INTO source_observations({columns}) VALUES ({placeholders})", tuple(payload.values()))


def test_m10_identity_merge_key_order_is_strongest_to_weakest_with_name_last():
    assert identity_merge_key_order() == (
        "license",
        "secretary_of_state_entity_id",
        "domain",
        "phone",
        "address",
        "business_name",
    )
    assert identity_merge_key_order()[-1] == "business_name"


def test_m10_fixture_search_discovery_connector_emits_search_lineage_only_no_network_no_send():
    connector: SearchDiscoveryConnector = FixtureSearchDiscoveryConnector()
    run = connector.run_fixture_search(query=_search_query(), batch_id=M10_BATCH_ID, mode=FetchMode.FIXTURE_ONLY)
    direct_run = run_fixture_search_discovery(_search_query(), batch_id=M10_BATCH_ID)

    assert run.connector_id == "fixture_search_discovery"
    assert run.mode == FetchMode.FIXTURE_ONLY.value
    assert run.network_used is False
    assert run.send_authorized is False
    assert run.source["source_class"] == SourceClass.SEARCH_OR_PLACES.value
    assert run.source["official_or_first_party_flag"] == 0
    assert run.source["paid_flag"] == 0
    assert run.source["requires_login"] == 0
    assert run.source["terms_prefetch_status"] == RobotsTermsPrefetchStatus.ALLOWED_FIXTURE_ONLY.value
    assert run.source["robots_prefetch_status"] == RobotsTermsPrefetchStatus.ALLOWED_FIXTURE_ONLY.value

    [observation] = run.observations
    assert observation["url_or_path"].startswith("fixture://search-discovery/")
    assert observation["page_type"] == PageType.SEARCH_RESULT.value
    assert observation["robots_prefetch_status"] == RobotsTermsPrefetchStatus.ALLOWED_FIXTURE_ONLY.value
    assert observation["terms_prefetch_status"] == RobotsTermsPrefetchStatus.ALLOWED_FIXTURE_ONLY.value
    raw = json.loads(str(observation["raw_result_ref"]))
    assert raw["search_discovery_only"] is True
    assert raw["page_type"] == PageType.SEARCH_RESULT.value
    assert raw["candidate"]["title"] == "Fixture Search Build Group - commercial TI"
    assert direct_run.connector_run_id == run.connector_run_id

    with pytest.raises(LiveFetchAttemptError, match="fixture"):
        run_fixture_search_discovery(
            SearchDiscoveryFixtureQuery(
                query="bad live candidate",
                fetched_at_utc=NOW,
                candidates=(
                    FixtureSearchCandidate(
                        title="Live URL candidate",
                        url_or_path="https://example.test/live",
                        snippet="not fixture",
                    ),
                ),
            ),
            batch_id=M10_BATCH_ID,
        )

    with pytest.raises(LiveFetchAttemptError, match="FIXTURE_ONLY"):
        connector.run_fixture_search(query=_search_query(), batch_id=M10_BATCH_ID, mode="live")


def test_m10_search_only_evidence_is_structurally_held_not_promoted_to_internal_review():
    conn = _connection()
    search_run = run_fixture_search_discovery(_search_query(), batch_id=M10_BATCH_ID)
    write_connector_run_result(conn, search_run, batch=_batch())
    observation_id = str(search_run.observations[0]["observation_id"])
    assemble_entities_and_facts(
        conn,
        batch_id=M10_BATCH_ID,
        records=(
            AssemblyFixtureRecord(
                observation_id=observation_id,
                field_relevant_snippet="Search result says Fixture Search Build Group performs commercial tenant improvements.",
                business_fields={
                    "business_name": "Fixture Search Build Group",
                    "website_url": "https://fixturesearchbuild.test/services",
                    "trade_category": "general_contractor",
                    "permitassist_icp_segment": "commercial_tenant_improvement_gc_design_build_remodeler",
                    "low_call_relevance_signal": "search-result-only commercial tenant improvement mention",
                    "contact_email": "owner@fixturesearchbuild.test",
                },
            ),
        ),
    )
    entity_id = conn.execute("SELECT entity_id FROM entities").fetchone()[0]
    email_fact_id = _insert_contact_passes(conn, entity_id)
    _insert_clear_suppression(conn, entity_id=entity_id, fact_id=email_fact_id)
    _insert_enrichment(conn, entity_id=entity_id)

    decision = evaluate_entity_promotion(conn, batch_id=M10_BATCH_ID, entity_id=entity_id)

    assert decision.promotion_tier == PromotionTier.QUALIFIED_LEAD_REVIEW_REQUIRED
    assert decision.status == GateStatus.REVIEW_REQUIRED
    assert decision.export_eligibility == ExportEligibility.REVIEW_REQUIRED
    assert decision.reason_codes == ("search_only_evidence_not_promotable",)
    assert decision.send_authorized is False
    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 0


def test_m10_search_only_promotion_event_flows_into_internal_review_artifact_non_exported_rows():
    from lead_pipeline.phase1_runner import run_phase1_fixture_pipeline

    run = run_phase1_fixture_pipeline(fixture_id="golden")
    conn = run.conn
    batch_id = run.summary.batch_id
    search_run = run_fixture_search_discovery(_search_query(), batch_id=batch_id)
    write_connector_run_result(
        conn,
        search_run,
        batch={
            "batch_id": batch_id,
            "approved_scope_ref": "phase1_fixture_only_lead_pipeline_m10_search_artifact_regression",
            "adapter_id": "permitassist",
            "started_at_utc": NOW,
            "status": "open",
            "schema_version": PHASE1_SCHEMA_VERSION,
        },
    )
    observation_id = str(search_run.observations[0]["observation_id"])
    assemble_entities_and_facts(
        conn,
        batch_id=batch_id,
        records=(
            AssemblyFixtureRecord(
                observation_id=observation_id,
                field_relevant_snippet="Search result says Artifact Search Build Group performs commercial tenant improvements.",
                business_fields={
                    "business_name": "Artifact Search Build Group",
                    "website_url": "https://artifactsearchbuild.test/services",
                    "trade_category": "general_contractor",
                    "permitassist_icp_segment": "commercial_tenant_improvement_gc_design_build_remodeler",
                    "low_call_relevance_signal": "search-result-only commercial tenant improvement mention",
                    "contact_email": "owner@artifactsearchbuild.test",
                },
            ),
        ),
    )
    entity_id = conn.execute(
        "SELECT entity_id FROM entities WHERE canonical_label = 'Artifact Search Build Group'"
    ).fetchone()[0]
    email_fact_id = _insert_contact_passes(conn, entity_id, batch_id=batch_id)
    _insert_clear_suppression(conn, entity_id=entity_id, fact_id=email_fact_id, batch_id=batch_id)
    _insert_enrichment(conn, entity_id=entity_id, batch_id=batch_id)

    decision = promote_entity(conn, batch_id=batch_id, entity_id=entity_id)
    artifact = render_internal_review_artifacts(conn, batch_id=batch_id).to_dict()

    assert decision.reason_codes == ("search_only_evidence_not_promotable",)
    assert decision.send_authorized is False
    labels = {item["business_label"]: item for item in artifact["non_exported_leads"]}
    held = labels["Artifact Search Build Group"]
    assert held["reason_codes"] == ["search_only_evidence_not_promotable"]
    assert held["send_authorized"] is False
    assert held["cost_placeholder"]["cost_usd"] == 0.0


def test_m10_search_plus_aggregator_without_first_party_or_official_evidence_is_not_promotable():
    conn = _connection()
    search_run = run_fixture_search_discovery(_search_query(), batch_id=M10_BATCH_ID)
    write_connector_run_result(conn, search_run, batch=_batch())
    observation_id = str(search_run.observations[0]["observation_id"])
    assemble_entities_and_facts(
        conn,
        batch_id=M10_BATCH_ID,
        records=(
            AssemblyFixtureRecord(
                observation_id=observation_id,
                field_relevant_snippet="Search result says Mixed Weak Build Group performs commercial tenant improvements.",
                business_fields={
                    "business_name": "Mixed Weak Build Group",
                    "website_url": "https://mixedweakbuild.test/services",
                    "trade_category": "general_contractor",
                    "permitassist_icp_segment": "commercial_tenant_improvement_gc_design_build_remodeler",
                    "low_call_relevance_signal": "search and directory only commercial tenant improvement mention",
                    "contact_email": "owner@mixedweakbuild.test",
                },
            ),
        ),
    )
    entity_id = conn.execute("SELECT entity_id FROM entities WHERE canonical_label = 'Mixed Weak Build Group'").fetchone()[0]
    conn.execute(
        "INSERT INTO sources(source_id, source_class, source_name, base_url_or_path, official_or_first_party_flag, "
        "terms_notes, robots_notes, terms_prefetch_status, robots_prefetch_status, requires_login, paid_flag, "
        "allowed_phase, schema_version) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, 0, 0, ?, ?)",
        (
            "src_m10_aggregator_directory",
            SourceClass.AGGREGATOR_DIRECTORY.value,
            "Fixture aggregator directory",
            "fixture://aggregator-directory/mixed-weak-build-group",
            "fixture-only aggregator terms placeholder",
            "fixture-only aggregator robots placeholder",
            RobotsTermsPrefetchStatus.ALLOWED_FIXTURE_ONLY.value,
            RobotsTermsPrefetchStatus.ALLOWED_FIXTURE_ONLY.value,
            "phase1_fixture_only",
            PHASE1_SCHEMA_VERSION,
        ),
    )
    conn.execute(
        "INSERT INTO source_observations(observation_id, source_id, batch_id, observed_at_utc, url_or_path, "
        "content_type, payload_hash_sha256, snippet_or_excerpt, extractor_version, page_type, "
        "robots_or_terms_classification, terms_prefetch_status, robots_prefetch_status, blocked_or_captcha_flag, "
        "schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
        (
            "obs_m10_aggregator_directory",
            "src_m10_aggregator_directory",
            M10_BATCH_ID,
            NOW,
            "fixture://aggregator-directory/mixed-weak-build-group/profile",
            "text/plain",
            "hash_m10_aggregator_directory",
            "Aggregator directory repeats Mixed Weak Build Group commercial TI services.",
            "m10_fixture_test",
            PageType.AGGREGATOR_DIRECTORY.value,
            FIXTURE_ROBOTS_OR_TERMS_CLASSIFICATION,
            RobotsTermsPrefetchStatus.ALLOWED_FIXTURE_ONLY.value,
            RobotsTermsPrefetchStatus.ALLOWED_FIXTURE_ONLY.value,
            PHASE1_SCHEMA_VERSION,
        ),
    )
    conn.execute(
        "INSERT INTO facts(fact_id, entity_id, fact_type, fact_value, normalized_value, confidence, promotion_status, "
        "source_observation_id, field_relevant_snippet, valid_from_observed_at_utc, schema_version) "
        "VALUES (?, ?, ?, ?, ?, 1.0, ?, ?, ?, ?, ?)",
        (
            "fct_m10_aggregator_trade_category",
            entity_id,
            "trade_category",
            "general_contractor",
            "general_contractor",
            PromotionTier.RAW_DISCOVERY.value,
            "obs_m10_aggregator_directory",
            "Aggregator directory repeats Mixed Weak Build Group commercial TI services.",
            NOW,
            PHASE1_SCHEMA_VERSION,
        ),
    )
    conn.commit()
    email_fact_id = _insert_contact_passes(conn, entity_id)
    _insert_clear_suppression(conn, entity_id=entity_id, fact_id=email_fact_id)
    _insert_enrichment(conn, entity_id=entity_id)

    decision = evaluate_entity_promotion(conn, batch_id=M10_BATCH_ID, entity_id=entity_id)

    assert decision.promotion_tier == PromotionTier.QUALIFIED_LEAD_REVIEW_REQUIRED
    assert decision.status == GateStatus.REVIEW_REQUIRED
    assert decision.export_eligibility == ExportEligibility.REVIEW_REQUIRED
    assert decision.reason_codes == ("search_only_evidence_not_promotable",)
    assert decision.send_authorized is False


def test_m10_aggregator_only_without_authoritative_evidence_remains_review_required():
    conn = _connection()
    _insert_source_observation(
        conn,
        source_id="src_m10_aggregator_only",
        observation_id="obs_m10_aggregator_only",
        source_class=SourceClass.AGGREGATOR_DIRECTORY,
        page_type=PageType.AGGREGATOR_DIRECTORY,
        snippet="Aggregator directory says Aggregator Only Build Group performs commercial tenant improvements.",
    )
    assemble_entities_and_facts(
        conn,
        batch_id=M10_BATCH_ID,
        records=(
            AssemblyFixtureRecord(
                observation_id="obs_m10_aggregator_only",
                field_relevant_snippet="Aggregator directory says Aggregator Only Build Group performs commercial tenant improvements.",
                business_fields={
                    "business_name": "Aggregator Only Build Group",
                    "website_url": "https://aggregatoronlybuild.test/services",
                    "trade_category": "general_contractor",
                    "permitassist_icp_segment": "commercial_tenant_improvement_gc_design_build_remodeler",
                    "low_call_relevance_signal": "aggregator-only commercial tenant improvement mention",
                    "contact_email": "owner@aggregatoronlybuild.test",
                },
            ),
        ),
    )
    entity_id = conn.execute("SELECT entity_id FROM entities WHERE canonical_label = 'Aggregator Only Build Group'").fetchone()[0]
    email_fact_id = _insert_contact_passes(conn, entity_id)
    _insert_clear_suppression(conn, entity_id=entity_id, fact_id=email_fact_id)
    _insert_enrichment(conn, entity_id=entity_id)

    decision = evaluate_entity_promotion(conn, batch_id=M10_BATCH_ID, entity_id=entity_id)

    assert decision.status == GateStatus.REVIEW_REQUIRED
    assert decision.export_eligibility == ExportEligibility.REVIEW_REQUIRED
    assert decision.reason_codes == ("aggregator_only_evidence_requires_corroboration",)
    assert decision.send_authorized is False


def test_m10_aggregator_plus_untrusted_first_party_emits_both_weak_source_reason_codes():
    conn = _connection()
    _insert_source_observation(
        conn,
        source_id="src_m10_aggregator_mixed_untrusted",
        observation_id="obs_m10_aggregator_mixed_untrusted",
        source_class=SourceClass.AGGREGATOR_DIRECTORY,
        page_type=PageType.AGGREGATOR_DIRECTORY,
        snippet="Aggregator says Aggregator Mixed Untrusted Build Group performs commercial tenant improvements.",
    )
    assemble_entities_and_facts(
        conn,
        batch_id=M10_BATCH_ID,
        records=(
            AssemblyFixtureRecord(
                observation_id="obs_m10_aggregator_mixed_untrusted",
                field_relevant_snippet="Aggregator says Aggregator Mixed Untrusted Build Group performs commercial tenant improvements.",
                business_fields={
                    "business_name": "Aggregator Mixed Untrusted Build Group",
                    "website_url": "https://aggregatormixeduntrusted.test/services",
                    "trade_category": "general_contractor",
                    "permitassist_icp_segment": "commercial_tenant_improvement_gc_design_build_remodeler",
                    "low_call_relevance_signal": "aggregator plus untrusted first-party fixture mention",
                    "contact_email": "owner@aggregatormixeduntrusted.test",
                },
            ),
        ),
    )
    entity_id = conn.execute(
        "SELECT entity_id FROM entities WHERE canonical_label = 'Aggregator Mixed Untrusted Build Group'"
    ).fetchone()[0]
    _insert_source_observation(
        conn,
        source_id="src_m10_mixed_untrusted_first_party",
        observation_id="obs_m10_mixed_untrusted_first_party",
        source_class=SourceClass.FIRST_PARTY_WEBSITE,
        page_type=PageType.FIRST_PARTY_SERVICES,
        official_or_first_party_flag=0,
        snippet="Untrusted first-party-classed fixture repeats commercial TI services.",
    )
    _insert_fact(
        conn,
        fact_id="fct_m10_mixed_untrusted_trade",
        entity_id=entity_id,
        fact_type="trade_category",
        fact_value="general_contractor",
        observation_id="obs_m10_mixed_untrusted_first_party",
        snippet="Untrusted first-party-classed fixture repeats commercial TI services.",
    )
    conn.commit()
    email_fact_id = _insert_contact_passes(conn, entity_id)
    _insert_clear_suppression(conn, entity_id=entity_id, fact_id=email_fact_id)
    _insert_enrichment(conn, entity_id=entity_id)

    decision = evaluate_entity_promotion(conn, batch_id=M10_BATCH_ID, entity_id=entity_id)

    assert decision.status == GateStatus.REVIEW_REQUIRED
    assert decision.export_eligibility == ExportEligibility.REVIEW_REQUIRED
    assert decision.reason_codes == (
        "aggregator_only_evidence_requires_corroboration",
        "untrusted_official_or_first_party_evidence_requires_corroboration",
    )
    assert decision.score == UNTRUSTED_OFFICIAL_OR_FIRST_PARTY_HOLD_SCORE
    assert decision.send_authorized is False


@pytest.mark.parametrize(
    ("source_class", "page_type", "business_label"),
    (
        (SourceClass.OFFICIAL_LICENSING, PageType.OFFICIAL_REGISTRY, "Untrusted Official Build Group"),
        (SourceClass.FIRST_PARTY_WEBSITE, PageType.FIRST_PARTY_SERVICES, "Untrusted First Party Build Group"),
    ),
)
def test_m10_untrusted_official_or_first_party_only_uses_truthful_reason_code(
    source_class: SourceClass,
    page_type: PageType,
    business_label: str,
):
    conn = _connection()
    observation_id = f"obs_m10_untrusted_{source_class.value}"
    _insert_source_observation(
        conn,
        source_id=f"src_m10_untrusted_{source_class.value}",
        observation_id=observation_id,
        source_class=source_class,
        page_type=page_type,
        official_or_first_party_flag=0,
        snippet=f"Untrusted {source_class.value} fixture says {business_label} performs commercial tenant improvements.",
    )
    assemble_entities_and_facts(
        conn,
        batch_id=M10_BATCH_ID,
        records=(
            AssemblyFixtureRecord(
                observation_id=observation_id,
                field_relevant_snippet=f"Untrusted {source_class.value} fixture says {business_label} performs commercial tenant improvements.",
                business_fields={
                    "business_name": business_label,
                    "website_url": f"https://{business_label.lower().replace(' ', '')}.test/services",
                    "trade_category": "general_contractor",
                    "permitassist_icp_segment": "commercial_tenant_improvement_gc_design_build_remodeler",
                    "low_call_relevance_signal": "untrusted official or first-party-shaped fixture mention",
                    "contact_email": f"owner@{business_label.lower().replace(' ', '')}.test",
                },
            ),
        ),
    )
    entity_id = conn.execute("SELECT entity_id FROM entities WHERE canonical_label = ?", (business_label,)).fetchone()[0]
    email_fact_id = _insert_contact_passes(conn, entity_id)
    _insert_clear_suppression(conn, entity_id=entity_id, fact_id=email_fact_id)
    _insert_enrichment(conn, entity_id=entity_id)

    decision = evaluate_entity_promotion(conn, batch_id=M10_BATCH_ID, entity_id=entity_id)

    assert decision.status == GateStatus.REVIEW_REQUIRED
    assert decision.export_eligibility == ExportEligibility.REVIEW_REQUIRED
    assert decision.reason_codes == ("untrusted_official_or_first_party_evidence_requires_corroboration",)
    assert decision.score == UNTRUSTED_OFFICIAL_OR_FIRST_PARTY_HOLD_SCORE
    assert decision.send_authorized is False


def test_m10_search_plus_unflagged_first_party_source_is_not_authoritative_evidence():
    conn = _connection()
    search_run = run_fixture_search_discovery(_search_query(), batch_id=M10_BATCH_ID)
    write_connector_run_result(conn, search_run, batch=_batch())
    observation_id = str(search_run.observations[0]["observation_id"])
    assemble_entities_and_facts(
        conn,
        batch_id=M10_BATCH_ID,
        records=(
            AssemblyFixtureRecord(
                observation_id=observation_id,
                field_relevant_snippet="Search result says Unflagged First Party Build Group performs commercial tenant improvements.",
                business_fields={
                    "business_name": "Unflagged First Party Build Group",
                    "website_url": "https://unflaggedfirstpartybuild.test/services",
                    "trade_category": "general_contractor",
                    "permitassist_icp_segment": "commercial_tenant_improvement_gc_design_build_remodeler",
                    "low_call_relevance_signal": "search result plus untrusted first-party-classed fixture mention",
                    "contact_email": "owner@unflaggedfirstpartybuild.test",
                },
            ),
        ),
    )
    entity_id = conn.execute(
        "SELECT entity_id FROM entities WHERE canonical_label = 'Unflagged First Party Build Group'"
    ).fetchone()[0]
    _insert_source_observation(
        conn,
        source_id="src_m10_unflagged_first_party",
        observation_id="obs_m10_unflagged_first_party",
        source_class=SourceClass.FIRST_PARTY_WEBSITE,
        page_type=PageType.FIRST_PARTY_SERVICES,
        official_or_first_party_flag=0,
        snippet="Unflagged first-party-classed fixture repeats commercial TI services.",
    )
    _insert_fact(
        conn,
        fact_id="fct_m10_unflagged_first_party_trade",
        entity_id=entity_id,
        fact_type="trade_category",
        fact_value="general_contractor",
        observation_id="obs_m10_unflagged_first_party",
        snippet="Unflagged first-party-classed fixture repeats commercial TI services.",
    )
    conn.commit()
    email_fact_id = _insert_contact_passes(conn, entity_id)
    _insert_clear_suppression(conn, entity_id=entity_id, fact_id=email_fact_id)
    _insert_enrichment(conn, entity_id=entity_id)

    decision = evaluate_entity_promotion(conn, batch_id=M10_BATCH_ID, entity_id=entity_id)

    assert decision.status == GateStatus.REVIEW_REQUIRED
    assert decision.export_eligibility == ExportEligibility.REVIEW_REQUIRED
    assert decision.reason_codes == (
        "search_only_evidence_not_promotable",
        "untrusted_official_or_first_party_evidence_requires_corroboration",
    )
    assert decision.score == SEARCH_ONLY_HOLD_SCORE
    assert decision.send_authorized is False


def test_m10_user_import_plus_search_keeps_approved_seed_lineage_out_of_search_only_hold():
    conn = _connection()
    search_run = run_fixture_search_discovery(_search_query(), batch_id=M10_BATCH_ID)
    write_connector_run_result(conn, search_run, batch=_batch())
    observation_id = str(search_run.observations[0]["observation_id"])
    assemble_entities_and_facts(
        conn,
        batch_id=M10_BATCH_ID,
        records=(
            AssemblyFixtureRecord(
                observation_id=observation_id,
                field_relevant_snippet="Search result says User Seed Mixed Build Group performs commercial tenant improvements.",
                business_fields={
                    "business_name": "User Seed Mixed Build Group",
                    "website_url": "https://userseedmixed.test/services",
                    "trade_category": "general_contractor",
                    "permitassist_icp_segment": "commercial_tenant_improvement_gc_design_build_remodeler",
                    "low_call_relevance_signal": "search plus approved user-import seed lineage",
                    "contact_email": "owner@userseedmixed.test",
                },
            ),
        ),
    )
    entity_id = conn.execute("SELECT entity_id FROM entities WHERE canonical_label = 'User Seed Mixed Build Group'").fetchone()[0]
    _insert_source_observation(
        conn,
        source_id="src_m10_user_import_mixed",
        observation_id="obs_m10_user_import_mixed",
        source_class=SourceClass.USER_IMPORT,
        page_type=PageType.BLOCKED_UNKNOWN,
        official_or_first_party_flag=0,
        snippet="Approved user import seed repeats User Seed Mixed Build Group identity.",
    )
    _insert_fact(
        conn,
        fact_id="fct_m10_user_import_mixed_identity",
        entity_id=entity_id,
        fact_type="business_name",
        fact_value="User Seed Mixed Build Group",
        observation_id="obs_m10_user_import_mixed",
        snippet="Approved user import seed repeats User Seed Mixed Build Group identity.",
    )
    conn.commit()
    email_fact_id = _insert_contact_passes(conn, entity_id)
    _insert_clear_suppression(conn, entity_id=entity_id, fact_id=email_fact_id)
    _insert_enrichment(conn, entity_id=entity_id)

    decision = evaluate_entity_promotion(conn, batch_id=M10_BATCH_ID, entity_id=entity_id)

    assert decision.status == GateStatus.PASS_
    assert decision.promotion_tier == PromotionTier.OUTREACH_READY_INTERNAL_ONLY
    assert decision.export_eligibility == ExportEligibility.INTERNAL_REVIEW_ONLY
    assert decision.reason_codes == ("outreach_ready_internal_review_only_no_send",)
    assert decision.send_authorized is False


def test_m10_batch_credit_ceiling_and_kill_switch_fail_closed_for_live_spend():
    policy = BatchCreditCeiling(batch_id=M10_BATCH_ID, max_live_provider_credits=0, kill_switch_engaged=False)
    decision = enforce_batch_credit_ceiling(policy, requested_live_provider_credits=0)
    assert decision.allowed is True
    assert decision.live_provider_credits_authorized == 0
    assert decision.reason_code == "fixture_zero_live_spend_allowed"

    with pytest.raises(Phase1RunnerSafetyError, match="credit ceiling"):
        enforce_batch_credit_ceiling(policy, requested_live_provider_credits=1)

    killed = BatchCreditCeiling(batch_id=M10_BATCH_ID, max_live_provider_credits=0, kill_switch_engaged=True)
    with pytest.raises(Phase1RunnerSafetyError, match="kill switch"):
        enforce_batch_credit_ceiling(killed, requested_live_provider_credits=0)


def test_m10_internal_review_only_export_rows_cannot_be_mutated_to_send_authorized():
    from lead_pipeline.phase1_runner import run_phase1_fixture_pipeline

    run = run_phase1_fixture_pipeline(fixture_id="golden")
    export_event_id = run.conn.execute("SELECT export_event_id FROM export_events LIMIT 1").fetchone()[0]

    with pytest.raises(sqlite3.DatabaseError):
        run.conn.execute("UPDATE export_events SET send_authorized = 1 WHERE export_event_id = ?", (export_event_id,))

    assert run.conn.execute("SELECT COALESCE(SUM(send_authorized), 0) FROM export_events").fetchone()[0] == 0


def test_m10_internal_review_artifact_includes_non_exported_rows_reason_codes_and_zero_cost_summary():
    from lead_pipeline.phase1_runner import run_phase1_fixture_pipeline

    run = run_phase1_fixture_pipeline(fixture_id="golden")
    artifact = render_internal_review_artifacts(run.conn, batch_id=run.summary.batch_id)
    payload = artifact.to_dict()

    assert payload["cost_summary"] == {
        "fixture_mode": True,
        "live_provider_credits_authorized": 0,
        "live_provider_credits_used": 0,
        "live_provider_cost_usd": 0.0,
        "fixture_cost_usd": 0.0,
        "paid_api_used": False,
        "cost_event_count": 3,
        "fixture_cost_event_count": 3,
        "providers": [
            {
                "provider_or_tool": "fixture_connector::contractor_first_party_website",
                "event_count": 3,
                "units_consumed": 3.0,
                "cost_usd": 0.0,
            }
        ],
    }
    assert payload["non_exported_lead_count"] == 2
    labels = {item["business_label"]: item for item in payload["non_exported_leads"]}
    assert labels["Quiet Clinic Contractors"]["reason_codes"] == ["missing_contact_candidate"]
    assert labels["Quiet Clinic Contractors"]["cost_placeholder"]["cost_usd"] == 0.0
    assert labels["Suppressed TI Contractors"]["reason_codes"] == ["suppression_blocks_promotion"]
    assert all(item["send_authorized"] is False for item in payload["non_exported_leads"])
    assert "Non-exported leads" in artifact.markdown
    assert "suppression_blocks_promotion" in artifact.markdown


def test_m10_internal_review_artifact_refuses_non_fixture_cost_events():
    from lead_pipeline.phase1_runner import run_phase1_fixture_pipeline

    run = run_phase1_fixture_pipeline(fixture_id="golden")
    run.conn.execute(
        "INSERT INTO cost_events(cost_event_id, batch_id, stage, provider_or_tool, units_consumed, cost_usd, "
        "allocated_flag, created_at_utc, schema_version) VALUES (?, ?, 'raw_collection', ?, 1.0, 1.23, 1, ?, ?)",
        (
            "cost_m10_bad_live_provider",
            run.summary.batch_id,
            "fixture_connector::apollo_io_b2b_database",
            NOW,
            PHASE1_SCHEMA_VERSION,
        ),
    )
    run.conn.commit()

    with pytest.raises(InternalReviewArtifactSafetyError, match="non-fixture cost provider"):
        render_internal_review_artifacts(run.conn, batch_id=run.summary.batch_id)
