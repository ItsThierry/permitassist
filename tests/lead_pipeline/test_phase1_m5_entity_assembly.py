"""Phase 1 M5 entity/fact assembly + deterministic dedupe tests.

These tests assert that the M5 assembler:

  * builds business entities and facts only from explicit synthetic fixture
    records (no inference, no AI-created facts, no live fetches);
  * links every entity and fact to a real ``source_observations`` row by
    ``source_observation_id`` and carries a ``field_relevant_snippet`` on
    every fact;
  * omits unknown fields rather than guessing them;
  * skips records with missing/empty business identity or missing snippet
    with a deterministic review reason instead of inferring values;
  * deduplicates deterministically by normalized business-name / legal-name
    / website-domain keys and preserves reversible lineage by writing
    ``identity_edges`` rather than overwriting or deleting either entity;
  * uses only Phase-1-allowed promotion tiers (never
    ``live_outreach_ready_future_phase``);
  * never writes to ``export_events`` and never sets ``send_authorized``;
  * is idempotent on rerun (no UPDATE/DELETE);
  * imports no network, DNS, SMTP, paid-provider, or scraping clients.
"""

from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

import pytest

from lead_pipeline.connectors import (
    FetchMode,
    FixtureDocument,
    run_fixture_connector,
)
from lead_pipeline.contracts import PromotionTier
from lead_pipeline.event_writer import (
    initialize_sqlite_schema,
    write_connector_run_result,
)
from lead_pipeline.schema import (
    PHASE1_ALLOWED_PROMOTION_VALUES,
    PHASE1_SCHEMA_VERSION,
)

from lead_pipeline.assembly import (
    ASSEMBLY_VERSION,
    AssemblyFixtureRecord,
    AssemblySafetyError,
    AssemblySummary,
    assemble_entities_and_facts,
)


REPO_ROOT = Path(__file__).resolve().parents[2]

NETWORK_IMPORTS = {
    "requests",
    "httpx",
    "urllib",
    "socket",
    "smtplib",
    "dns",
    "aiohttp",
    "selenium",
    "playwright",
    "firecrawl",
    "brave",
}

M5_BATCH_ID = "batch_fixture_m5"


def _make_doc(url: str, snippet: str, payload: str) -> FixtureDocument:
    return FixtureDocument(
        url_or_path=url,
        fetched_at_utc="2026-05-20T00:00:00Z",
        payload_text=payload,
        snippet=snippet,
        content_type="text/plain",
    )


def _two_clean_docs() -> list[FixtureDocument]:
    return [
        _make_doc(
            "fixture://contractor-first-party/fixturebuild/services/commercial-ti",
            "We perform commercial tenant improvement build-outs.",
            "Fixture Build Group commercial TI page payload synthetic body A.",
        ),
        _make_doc(
            "fixture://contractor-first-party/fixturebuild/about",
            "Fixture Build Group is a licensed commercial general contractor.",
            "Fixture Build Group about page payload synthetic body B.",
        ),
    ]


def _clean_batch() -> dict[str, object]:
    return {
        "batch_id": M5_BATCH_ID,
        "approved_scope_ref": "phase1_fixture_only_lead_pipeline_m5",
        "adapter_id": "permitassist",
        "started_at_utc": "2026-05-20T00:00:00Z",
        "status": "open",
        "schema_version": PHASE1_SCHEMA_VERSION,
    }


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    initialize_sqlite_schema(conn)
    return conn


def _populate_observations(conn: sqlite3.Connection):
    """Write two fixture observations into ``conn`` and return the run result."""

    result = run_fixture_connector(
        "contractor_first_party_website",
        documents=_two_clean_docs(),
        batch_id=M5_BATCH_ID,
        mode=FetchMode.FIXTURE_ONLY,
    )
    write_connector_run_result(conn, result, batch=_clean_batch())
    return result


def _full_clean_record(observation_id: str) -> AssemblyFixtureRecord:
    return AssemblyFixtureRecord(
        observation_id=observation_id,
        field_relevant_snippet="Fixture Build Group commercial TI services snippet.",
        business_fields={
            "business_name": "Fixture Build Group",
            "legal_name": "Fixture Build Group, Inc.",
            "dba_name": "FixBuild",
            "website_url": "https://fixturebuild.example/services",
            "service_area": "Bay Area",
            "trade_category": "general_contractor",
            "license_class": "B",
            "contact_email": "hello@fixturebuild.example",
            "permitassist_icp_segment": "commercial_tenant_improvement_gc_design_build_remodeling",
            "low_call_relevance_signal": "explicit_commercial_ti_signal",
        },
    )


# ---------------------------------------------------------------------------
# Version + module hygiene


def test_assembly_version_is_phase1_m5_entity_assembly_v1():
    assert ASSEMBLY_VERSION == "lead_pipeline_phase1_m5_entity_assembly_v1"


def test_assembly_module_has_no_network_or_paid_imports():
    path = REPO_ROOT / "lead_pipeline" / "assembly.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        imported: list[str] = []
        if isinstance(node, ast.Import):
            imported = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported = [node.module.split(".")[0]]
        for module_name in imported:
            if module_name in NETWORK_IMPORTS:
                offenders.append(f"{path.name} imports {module_name}")
    assert not offenders, (
        "Phase 1 M5 assembler must stay fixture-only/no-network:\n"
        + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# Happy path


def test_assembly_creates_one_entity_and_facts_with_lineage_from_clean_record():
    conn = _connection()
    run = _populate_observations(conn)
    obs_id = run.observations[0]["observation_id"]

    record = _full_clean_record(obs_id)
    summary = assemble_entities_and_facts(
        conn, batch_id=M5_BATCH_ID, records=[record]
    )

    assert isinstance(summary, AssemblySummary)
    assert summary.batch_id == M5_BATCH_ID
    assert summary.entities_inserted == 1
    assert summary.facts_inserted == 10
    assert summary.identity_edges_inserted == 0
    assert summary.records_skipped == 0
    assert summary.records_review_required == 0

    entity_rows = conn.execute(
        "SELECT entity_id, entity_type, canonical_label, normalized_key, status, "
        "created_from_observation_id, schema_version FROM entities"
    ).fetchall()
    assert len(entity_rows) == 1
    (entity_id, etype, label, nkey, status, created_from, schema_version) = entity_rows[0]
    assert etype == "business"
    assert label == "Fixture Build Group"
    assert nkey == "fixture build group"
    assert created_from == obs_id
    assert status in PHASE1_ALLOWED_PROMOTION_VALUES
    assert status != PromotionTier.LIVE_OUTREACH_READY_FUTURE_PHASE.value
    assert schema_version == PHASE1_SCHEMA_VERSION

    fact_rows = conn.execute(
        "SELECT fact_type, fact_value, source_observation_id, "
        "field_relevant_snippet, promotion_status, entity_id, schema_version "
        "FROM facts ORDER BY fact_type"
    ).fetchall()
    assert len(fact_rows) == 10

    fact_types = {row[0] for row in fact_rows}
    assert fact_types == {
        "business_name",
        "legal_name",
        "dba_name",
        "website_url",
        "service_area",
        "trade_category",
        "license_class",
        "contact_email",
        "permitassist_icp_segment",
        "low_call_relevance_signal",
    }

    for fact_type, fact_value, sobs, snippet, promo, eid, fv_schema in fact_rows:
        assert sobs == obs_id, fact_type
        assert snippet == record.field_relevant_snippet, fact_type
        assert eid == entity_id, fact_type
        assert promo in PHASE1_ALLOWED_PROMOTION_VALUES, fact_type
        assert promo != PromotionTier.LIVE_OUTREACH_READY_FUTURE_PHASE.value, fact_type
        assert fv_schema == PHASE1_SCHEMA_VERSION, fact_type
        assert fact_value, fact_type


def test_assembly_omits_unknown_fields_and_does_not_infer():
    conn = _connection()
    run = _populate_observations(conn)
    obs_id = run.observations[0]["observation_id"]

    record = AssemblyFixtureRecord(
        observation_id=obs_id,
        field_relevant_snippet="Fixture Build Group commercial TI snippet.",
        business_fields={
            "business_name": "Fixture Build Group",
            "trade_category": "general_contractor",
            # Unknown columns must be ignored, not inferred or coerced.
            "owner_personality_score": 0.99,
            "phone_number": "555-555-5555",
            "yelp_rating": 4.8,
        },
    )
    summary = assemble_entities_and_facts(
        conn, batch_id=M5_BATCH_ID, records=[record]
    )

    assert summary.entities_inserted == 1
    assert summary.facts_inserted == 2
    fact_types = {row[0] for row in conn.execute("SELECT fact_type FROM facts").fetchall()}
    assert fact_types == {"business_name", "trade_category"}


def test_assembly_skips_empty_or_whitespace_business_field_values():
    conn = _connection()
    run = _populate_observations(conn)
    obs_id = run.observations[0]["observation_id"]

    record = AssemblyFixtureRecord(
        observation_id=obs_id,
        field_relevant_snippet="Fixture Build Group snippet.",
        business_fields={
            "business_name": "Fixture Build Group",
            "legal_name": "   ",  # whitespace only — must be skipped, not inserted
            "dba_name": "",  # empty — must be skipped
            "website_url": None,  # None — must be skipped
            "trade_category": "general_contractor",
        },
    )
    summary = assemble_entities_and_facts(
        conn, batch_id=M5_BATCH_ID, records=[record]
    )

    assert summary.entities_inserted == 1
    assert summary.facts_inserted == 2
    fact_types = {row[0] for row in conn.execute("SELECT fact_type FROM facts").fetchall()}
    assert fact_types == {"business_name", "trade_category"}


# ---------------------------------------------------------------------------
# Fail-closed / skip-with-reason behavior


def test_assembly_skips_record_missing_field_relevant_snippet():
    conn = _connection()
    run = _populate_observations(conn)
    obs_id = run.observations[0]["observation_id"]

    record = AssemblyFixtureRecord(
        observation_id=obs_id,
        field_relevant_snippet="",
        business_fields={"business_name": "Fixture Build Group"},
    )
    summary = assemble_entities_and_facts(
        conn, batch_id=M5_BATCH_ID, records=[record]
    )

    assert summary.entities_inserted == 0
    assert summary.facts_inserted == 0
    assert summary.records_skipped == 1
    assert any(
        "missing_field_relevant_snippet" in skip.reason for skip in summary.skip_details
    )
    # No row reached the DB.
    assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0


def test_assembly_skips_record_with_no_identity_field():
    conn = _connection()
    run = _populate_observations(conn)
    obs_id = run.observations[0]["observation_id"]

    record = AssemblyFixtureRecord(
        observation_id=obs_id,
        field_relevant_snippet="Some snippet.",
        business_fields={
            "service_area": "Bay Area",
            "trade_category": "general_contractor",
        },
    )
    summary = assemble_entities_and_facts(
        conn, batch_id=M5_BATCH_ID, records=[record]
    )

    assert summary.entities_inserted == 0
    assert summary.facts_inserted == 0
    assert summary.records_skipped == 1
    assert any(
        "missing_business_identity" in skip.reason for skip in summary.skip_details
    )


def test_assembly_rejects_record_with_unknown_observation_id():
    conn = _connection()
    _populate_observations(conn)

    record = AssemblyFixtureRecord(
        observation_id="obs_does_not_exist_anywhere",
        field_relevant_snippet="snippet",
        business_fields={"business_name": "Fixture Build Group"},
    )
    with pytest.raises(AssemblySafetyError) as excinfo:
        assemble_entities_and_facts(
            conn, batch_id=M5_BATCH_ID, records=[record]
        )
    assert "obs_does_not_exist_anywhere" in str(excinfo.value)
    assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0


def test_assembly_rejects_observation_with_non_fixture_url_or_path():
    """Defense in depth: even if a non-fixture row somehow got into source_observations,
    the assembler must refuse to use it as evidence."""

    conn = _connection()
    # Insert a non-fixture observation row directly, bypassing the M4 writer.
    conn.execute(
        "INSERT INTO batches(batch_id, approved_scope_ref, adapter_id, started_at_utc, status, schema_version) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "batch_bad_m5",
            "phase1_fixture_only",
            "permitassist",
            "2026-05-20T00:00:00Z",
            "open",
            PHASE1_SCHEMA_VERSION,
        ),
    )
    conn.execute(
        "INSERT INTO sources(source_id, source_class, source_name, base_url_or_path, "
        "official_or_first_party_flag, requires_login, paid_flag, allowed_phase, schema_version) "
        "VALUES (?, ?, ?, ?, 1, 0, 0, ?, ?)",
        (
            "src_bad_m5",
            "first_party_website",
            "bad source",
            "fixture://bad-source/",
            "phase1_fixture_only",
            PHASE1_SCHEMA_VERSION,
        ),
    )
    conn.execute(
        "INSERT INTO source_observations(observation_id, source_id, batch_id, "
        "observed_at_utc, url_or_path, payload_hash_sha256, snippet_or_excerpt, "
        "blocked_or_captcha_flag, schema_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
        (
            "obs_bad_m5",
            "src_bad_m5",
            "batch_bad_m5",
            "2026-05-20T00:00:00Z",
            "https://live.example/page",
            "hash_bad",
            "snip",
            PHASE1_SCHEMA_VERSION,
        ),
    )
    conn.commit()

    record = AssemblyFixtureRecord(
        observation_id="obs_bad_m5",
        field_relevant_snippet="snippet",
        business_fields={"business_name": "Bad Co"},
    )
    with pytest.raises(AssemblySafetyError) as excinfo:
        assemble_entities_and_facts(
            conn, batch_id="batch_bad_m5", records=[record]
        )
    assert "fixture://" in str(excinfo.value)


def test_assembly_rejects_observation_from_wrong_batch_id():
    conn = _connection()
    run = _populate_observations(conn)
    obs_id = run.observations[0]["observation_id"]

    record = AssemblyFixtureRecord(
        observation_id=obs_id,
        field_relevant_snippet="Fixture Build Group snippet.",
        business_fields={"business_name": "Fixture Build Group"},
    )

    with pytest.raises(AssemblySafetyError) as excinfo:
        assemble_entities_and_facts(
            conn, batch_id="different_batch_id", records=[record]
        )
    assert "batch_id mismatch" in str(excinfo.value)
    assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Deterministic dedupe via identity_edges


def test_assembly_dedupes_same_normalized_business_name_via_identity_edge():
    conn = _connection()
    run = _populate_observations(conn)
    obs_a = run.observations[0]["observation_id"]
    obs_b = run.observations[1]["observation_id"]

    records = [
        AssemblyFixtureRecord(
            observation_id=obs_a,
            field_relevant_snippet="services page mentions Fixture Build Group.",
            business_fields={"business_name": "Fixture Build Group"},
        ),
        AssemblyFixtureRecord(
            observation_id=obs_b,
            field_relevant_snippet="about page mentions Fixture Build Group Inc.",
            business_fields={"business_name": "Fixture Build Group, Inc."},
        ),
    ]
    summary = assemble_entities_and_facts(
        conn, batch_id=M5_BATCH_ID, records=records
    )

    # Two records → two entities (lineage preserved), one identity_edge candidate.
    assert summary.entities_inserted == 2
    assert summary.facts_inserted == 2
    assert summary.identity_edges_inserted == 1
    assert summary.records_review_required == 1

    entity_rows = conn.execute(
        "SELECT entity_id FROM entities ORDER BY entity_id"
    ).fetchall()
    assert len(entity_rows) == 2

    edge_rows = conn.execute(
        "SELECT edge_type, match_key, review_status, evidence_fact_ids, "
        "from_entity_id, to_entity_id, created_by, schema_version "
        "FROM identity_edges"
    ).fetchall()
    assert len(edge_rows) == 1
    (etype, match_key, review_status, ev_fact_ids, frm, to,
     created_by, schema_version) = edge_rows[0]
    assert etype == "duplicate_candidate"
    assert match_key == "fixture build group"
    assert review_status == "review_required"
    assert frm != to
    assert {frm, to} == {row[0] for row in entity_rows}
    parsed = json.loads(ev_fact_ids)
    assert isinstance(parsed, list) and len(parsed) >= 1
    assert created_by
    assert schema_version == PHASE1_SCHEMA_VERSION


def test_assembly_dedupes_same_website_domain_via_identity_edge():
    conn = _connection()
    run = _populate_observations(conn)
    obs_a = run.observations[0]["observation_id"]
    obs_b = run.observations[1]["observation_id"]

    records = [
        AssemblyFixtureRecord(
            observation_id=obs_a,
            field_relevant_snippet="services page",
            business_fields={
                "business_name": "Fixture Build Group",
                "website_url": "https://www.fixturebuild.example/services",
            },
        ),
        AssemblyFixtureRecord(
            observation_id=obs_b,
            field_relevant_snippet="about page",
            business_fields={
                "business_name": "Totally Different Brand LLC",
                "website_url": "https://fixturebuild.example/about",
            },
        ),
    ]
    summary = assemble_entities_and_facts(
        conn, batch_id=M5_BATCH_ID, records=records
    )

    assert summary.entities_inserted == 2
    assert summary.identity_edges_inserted == 1
    edge_rows = conn.execute(
        "SELECT match_key, edge_type FROM identity_edges"
    ).fetchall()
    assert len(edge_rows) == 1
    assert edge_rows[0][0] == "fixturebuild.example"
    assert edge_rows[0][1] == "duplicate_candidate"


def test_assembly_dedupes_same_normalized_legal_name_via_identity_edge():
    conn = _connection()
    run = _populate_observations(conn)
    obs_a = run.observations[0]["observation_id"]
    obs_b = run.observations[1]["observation_id"]

    records = [
        AssemblyFixtureRecord(
            observation_id=obs_a,
            field_relevant_snippet="state license filing snippet a.",
            business_fields={
                "business_name": "FixBuild West",
                "legal_name": "Fixture Build Group, Inc.",
            },
        ),
        AssemblyFixtureRecord(
            observation_id=obs_b,
            field_relevant_snippet="sos filing snippet b.",
            business_fields={
                "business_name": "FixBuild East",
                "legal_name": "FIXTURE BUILD GROUP INC.",
            },
        ),
    ]
    summary = assemble_entities_and_facts(
        conn, batch_id=M5_BATCH_ID, records=records
    )
    assert summary.entities_inserted == 2
    assert summary.identity_edges_inserted == 1
    (match_key,) = conn.execute(
        "SELECT match_key FROM identity_edges"
    ).fetchone()
    # Same normalized legal name regardless of "Inc."/punctuation/case.
    assert match_key == "fixture build group"


def test_assembly_distinct_records_create_no_identity_edges():
    conn = _connection()
    run = _populate_observations(conn)
    obs_a = run.observations[0]["observation_id"]
    obs_b = run.observations[1]["observation_id"]

    records = [
        AssemblyFixtureRecord(
            observation_id=obs_a,
            field_relevant_snippet="services page",
            business_fields={
                "business_name": "Fixture Build Group",
                "website_url": "https://fixturebuild.example/services",
            },
        ),
        AssemblyFixtureRecord(
            observation_id=obs_b,
            field_relevant_snippet="about page",
            business_fields={
                "business_name": "Sample Remodelers",
                "website_url": "https://sampleremodelers.example/about",
            },
        ),
    ]
    summary = assemble_entities_and_facts(
        conn, batch_id=M5_BATCH_ID, records=records
    )

    assert summary.entities_inserted == 2
    assert summary.identity_edges_inserted == 0
    assert summary.records_review_required == 0


def test_assembly_dedupe_preserves_both_entities_no_overwrite_or_delete():
    """Append-only safety: dedupe must NEVER drop a row; it must write an edge."""

    conn = _connection()
    run = _populate_observations(conn)
    obs_a = run.observations[0]["observation_id"]
    obs_b = run.observations[1]["observation_id"]

    records = [
        AssemblyFixtureRecord(
            observation_id=obs_a,
            field_relevant_snippet="services page",
            business_fields={"business_name": "Fixture Build Group"},
        ),
        AssemblyFixtureRecord(
            observation_id=obs_b,
            field_relevant_snippet="about page",
            business_fields={"business_name": "Fixture Build Group"},
        ),
    ]
    assemble_entities_and_facts(
        conn, batch_id=M5_BATCH_ID, records=records
    )

    # Both entities still exist with full per-observation lineage.
    rows = conn.execute(
        "SELECT created_from_observation_id FROM entities "
        "ORDER BY created_from_observation_id"
    ).fetchall()
    created_from_ids = sorted(row[0] for row in rows)
    assert created_from_ids == sorted([obs_a, obs_b])

    # Identity edge exists; from/to entities are both still in entities.
    (frm, to) = conn.execute(
        "SELECT from_entity_id, to_entity_id FROM identity_edges"
    ).fetchone()
    entity_ids = {
        row[0] for row in conn.execute("SELECT entity_id FROM entities").fetchall()
    }
    assert frm in entity_ids
    assert to in entity_ids


# ---------------------------------------------------------------------------
# Phase 1 safety — never write blocked tier, export_events, or send_authorized


def test_assembly_never_writes_live_outreach_promotion_tier_anywhere():
    conn = _connection()
    run = _populate_observations(conn)
    obs_id = run.observations[0]["observation_id"]
    assemble_entities_and_facts(
        conn,
        batch_id=M5_BATCH_ID,
        records=[_full_clean_record(obs_id)],
    )
    promo_set = {
        row[0] for row in conn.execute("SELECT promotion_status FROM facts").fetchall()
    }
    assert promo_set
    assert PromotionTier.LIVE_OUTREACH_READY_FUTURE_PHASE.value not in promo_set
    for promo in promo_set:
        assert promo in PHASE1_ALLOWED_PROMOTION_VALUES

    status_set = {
        row[0] for row in conn.execute("SELECT status FROM entities").fetchall()
    }
    assert PromotionTier.LIVE_OUTREACH_READY_FUTURE_PHASE.value not in status_set


def test_assembly_does_not_write_export_events_or_set_send_authorized():
    conn = _connection()
    run = _populate_observations(conn)
    obs_id = run.observations[0]["observation_id"]
    assemble_entities_and_facts(
        conn,
        batch_id=M5_BATCH_ID,
        records=[_full_clean_record(obs_id)],
    )

    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 0
    # Other downstream gate-ledger tables must also remain untouched in M5.
    for table in (
        "verification_events",
        "enrichment_events",
        "suppression_events",
        "human_review_events",
    ):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table


def test_assembly_rerun_reports_duplicates_without_mutation():
    conn = _connection()
    run = _populate_observations(conn)
    obs_a = run.observations[0]["observation_id"]
    obs_b = run.observations[1]["observation_id"]

    records = [
        AssemblyFixtureRecord(
            observation_id=obs_a,
            field_relevant_snippet="services page",
            business_fields={"business_name": "Fixture Build Group"},
        ),
        AssemblyFixtureRecord(
            observation_id=obs_b,
            field_relevant_snippet="about page",
            business_fields={"business_name": "Fixture Build Group"},
        ),
    ]
    first = assemble_entities_and_facts(
        conn, batch_id=M5_BATCH_ID, records=records
    )
    snapshot_entities = conn.execute(
        "SELECT entity_id, canonical_label, normalized_key, created_from_observation_id "
        "FROM entities ORDER BY entity_id"
    ).fetchall()
    snapshot_facts = conn.execute(
        "SELECT fact_id, fact_type, fact_value, entity_id, source_observation_id "
        "FROM facts ORDER BY fact_id"
    ).fetchall()
    snapshot_edges = conn.execute(
        "SELECT edge_id, from_entity_id, to_entity_id, match_key "
        "FROM identity_edges ORDER BY edge_id"
    ).fetchall()

    second = assemble_entities_and_facts(
        conn, batch_id=M5_BATCH_ID, records=records
    )

    assert conn.execute(
        "SELECT entity_id, canonical_label, normalized_key, created_from_observation_id "
        "FROM entities ORDER BY entity_id"
    ).fetchall() == snapshot_entities
    assert conn.execute(
        "SELECT fact_id, fact_type, fact_value, entity_id, source_observation_id "
        "FROM facts ORDER BY fact_id"
    ).fetchall() == snapshot_facts
    assert conn.execute(
        "SELECT edge_id, from_entity_id, to_entity_id, match_key "
        "FROM identity_edges ORDER BY edge_id"
    ).fetchall() == snapshot_edges

    assert first.entities_inserted == 2
    assert second.entities_inserted == 0
    assert second.facts_inserted == 0
    assert second.identity_edges_inserted == 0
    assert second.records_review_required == 0


def test_assembly_summary_is_deterministic_across_calls():
    conn_a = _connection()
    _populate_observations(conn_a)
    obs_a = list(
        conn_a.execute("SELECT observation_id FROM source_observations ORDER BY observation_id")
    )
    record_a = AssemblyFixtureRecord(
        observation_id=obs_a[0][0],
        field_relevant_snippet="snippet",
        business_fields={"business_name": "Fixture Build Group"},
    )
    summary_a = assemble_entities_and_facts(
        conn_a, batch_id=M5_BATCH_ID, records=[record_a]
    )

    conn_b = _connection()
    _populate_observations(conn_b)
    obs_b = list(
        conn_b.execute("SELECT observation_id FROM source_observations ORDER BY observation_id")
    )
    record_b = AssemblyFixtureRecord(
        observation_id=obs_b[0][0],
        field_relevant_snippet="snippet",
        business_fields={"business_name": "Fixture Build Group"},
    )
    summary_b = assemble_entities_and_facts(
        conn_b, batch_id=M5_BATCH_ID, records=[record_b]
    )

    assert summary_a == summary_b
