"""Phase 1 M6 promotion / eligibility gate tests.

M6 is the deterministic safety layer between M5 assembled entities and any
future export contract. These tests keep it fixture-only and verify the exact
fail-closed rules Boban requested before M7.
"""

from __future__ import annotations

import ast
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from lead_pipeline.assembly import AssemblyFixtureRecord, assemble_entities_and_facts
from lead_pipeline.connectors import FetchMode, FixtureDocument, run_fixture_connector
from lead_pipeline.contracts import ExportEligibility, GateStatus, PromotionTier, SuppressionStatus
from lead_pipeline.event_writer import initialize_sqlite_schema, write_connector_run_result
from lead_pipeline.promotion import (
    PROMOTION_VERSION,
    PromotionSafetyError,
    evaluate_entity_promotion,
    promote_entity,
    write_promotion_decision_event,
)
from lead_pipeline.schema import PHASE1_SCHEMA_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]
M6_BATCH_ID = "batch_fixture_m6"
NOW = "2026-05-20T00:00:00Z"

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
    "subprocess",
}


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    initialize_sqlite_schema(conn)
    return conn


def _batch(batch_id: str = M6_BATCH_ID) -> dict[str, object]:
    return {
        "batch_id": batch_id,
        "approved_scope_ref": "phase1_fixture_only_lead_pipeline_m6",
        "adapter_id": "permitassist",
        "started_at_utc": NOW,
        "status": "open",
        "schema_version": PHASE1_SCHEMA_VERSION,
    }


def _make_doc(path: str = "services/commercial-ti") -> FixtureDocument:
    return FixtureDocument(
        url_or_path=f"fixture://contractor-first-party/fixturebuild/{path}",
        fetched_at_utc=NOW,
        payload_text="Fixture Build Group commercial TI contractor synthetic payload.",
        snippet="Fixture Build Group performs commercial tenant improvement build-outs.",
        content_type="text/plain",
    )


def _populate_first_party_observation(conn: sqlite3.Connection, *, batch_id: str = M6_BATCH_ID):
    result = run_fixture_connector(
        "contractor_first_party_website",
        documents=[_make_doc()],
        batch_id=batch_id,
        mode=FetchMode.FIXTURE_ONLY,
    )
    write_connector_run_result(conn, result, batch=_batch(batch_id))
    return result.observations[0]["observation_id"]


def _assemble_entity(
    conn: sqlite3.Connection,
    *,
    batch_id: str = M6_BATCH_ID,
    fields: dict[str, object] | None = None,
    observation_id: str | None = None,
) -> str:
    obs_id = observation_id or _populate_first_party_observation(conn, batch_id=batch_id)
    business_fields = {
        "business_name": "Fixture Build Group",
        "website_url": "https://fixturebuild.test/services",
        "trade_category": "general_contractor",
        "permitassist_icp_segment": "commercial_tenant_improvement_gc_design_build_remodeler",
        "low_call_relevance_signal": "explicit commercial tenant improvement build-outs",
        "contact_email": "owner@fixturebuild.test",
    }
    if fields is not None:
        business_fields = fields
    assemble_entities_and_facts(
        conn,
        batch_id=batch_id,
        records=[
            AssemblyFixtureRecord(
                observation_id=obs_id,
                field_relevant_snippet="Fixture Build Group performs commercial tenant improvement build-outs.",
                business_fields=business_fields,
            )
        ],
    )
    return conn.execute("SELECT entity_id FROM entities").fetchone()[0]


def _fact_id(conn: sqlite3.Connection, entity_id: str, fact_type: str) -> str:
    row = conn.execute(
        "SELECT fact_id FROM facts WHERE entity_id = ? AND fact_type = ?",
        (entity_id, fact_type),
    ).fetchone()
    assert row is not None, fact_type
    return row[0]


def _insert_verification(
    conn: sqlite3.Connection,
    *,
    batch_id: str = M6_BATCH_ID,
    entity_id: str | None = None,
    fact_id: str | None = None,
    gate_name: str,
    status: str = GateStatus.PASS_.value,
    reason_codes: list[str] | None = None,
    network_used_flag: int = 0,
) -> str:
    event_id = f"ve_test_{gate_name}_{fact_id or entity_id}_{len(list(conn.execute('SELECT 1 FROM verification_events')))}"
    conn.execute(
        "INSERT INTO verification_events(verification_event_id, batch_id, target_entity_id, target_fact_id, "
        "gate_name, gate_version, input_hash, result_status, score, reason_codes, network_used_flag, "
        "cached_result_flag, observed_at_utc, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
        (
            event_id,
            batch_id,
            entity_id,
            fact_id,
            gate_name,
            "test_gate_v1",
            f"hash_{event_id}",
            status,
            1.0 if status == GateStatus.PASS_.value else 0.0,
            json.dumps(reason_codes or ["test_reason"]),
            network_used_flag,
            NOW,
            PHASE1_SCHEMA_VERSION,
        ),
    )
    conn.commit()
    return event_id


def _insert_contact_passes(conn: sqlite3.Connection, entity_id: str) -> str:
    email_fact_id = _fact_id(conn, entity_id, "contact_email")
    for gate_name in ("contact_observation_gate", "email_syntax_gate", "domain_quality_gate"):
        _insert_verification(conn, entity_id=entity_id, fact_id=email_fact_id, gate_name=gate_name)
    return email_fact_id


def _insert_suppression(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    fact_id: str | None = None,
    status: str = SuppressionStatus.CLEAR.value,
) -> str:
    event_id = f"sup_test_{entity_id}_{status}_{len(list(conn.execute('SELECT 1 FROM suppression_events')))}"
    conn.execute(
        "INSERT INTO suppression_events(suppression_event_id, batch_id, target_entity_id, target_fact_id, "
        "suppression_source_ref, suppression_snapshot_hash, status, reason, checked_at_utc, schema_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event_id,
            M6_BATCH_ID,
            entity_id,
            fact_id,
            "fixture://suppression/internal-list",
            f"hash_{event_id}",
            status,
            status,
            NOW,
            PHASE1_SCHEMA_VERSION,
        ),
    )
    conn.commit()
    return event_id


def _insert_enrichment(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    fact_ids: list[str] | None = None,
    observation_ids: list[str] | None = None,
    validator_status: str = GateStatus.PASS_.value,
    unsupported_claim_count: int = 0,
) -> str:
    if fact_ids is None:
        fact_ids = [row[0] for row in conn.execute("SELECT fact_id FROM facts WHERE entity_id = ?", (entity_id,))]
    if observation_ids is None:
        observation_ids = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT source_observation_id FROM facts WHERE entity_id = ?",
                (entity_id,),
            )
        ]
    event_id = f"enrich_test_{entity_id}_{len(list(conn.execute('SELECT 1 FROM enrichment_events')))}"
    conn.execute(
        "INSERT INTO enrichment_events(enrichment_event_id, batch_id, entity_id, input_fact_ids, "
        "input_observation_ids, model_or_rule_version, output_json, quality_score, unsupported_claim_count, "
        "validator_status, validator_reason_codes, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event_id,
            M6_BATCH_ID,
            entity_id,
            json.dumps(fact_ids, sort_keys=True),
            json.dumps(observation_ids, sort_keys=True),
            "fixture_rule_v1",
            json.dumps({"summary": "Source-backed commercial TI contractor fixture summary."}, sort_keys=True),
            1.0 if validator_status == GateStatus.PASS_.value else 0.0,
            unsupported_claim_count,
            validator_status,
            json.dumps(["source_backed_enrichment"]),
            PHASE1_SCHEMA_VERSION,
        ),
    )
    conn.commit()
    return event_id


# ---------------------------------------------------------------------------
# Version + module hygiene


def test_promotion_version_is_phase1_m6_promotion_gate_v1():
    assert PROMOTION_VERSION == "lead_pipeline_phase1_m6_promotion_gate_v1"


def test_promotion_module_has_no_network_or_outreach_imports():
    path = REPO_ROOT / "lead_pipeline" / "promotion.py"
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
    assert not offenders, "M6 promotion gate must stay fixture-only/no-network: " + ", ".join(offenders)


# ---------------------------------------------------------------------------
# Required fail-closed / promotion behavior


def test_m6_blocks_entity_with_no_source_provenance():
    conn = _connection()
    conn.execute(
        "INSERT INTO entities(entity_id, entity_type, canonical_label, normalized_key, status, schema_version) "
        "VALUES (?, 'business', ?, ?, ?, ?)",
        ("ent_no_source", "No Source LLC", "no source", PromotionTier.RAW_DISCOVERY.value, PHASE1_SCHEMA_VERSION),
    )
    decision = evaluate_entity_promotion(conn, batch_id=M6_BATCH_ID, entity_id="ent_no_source")
    assert decision.status == GateStatus.FAIL_CLOSED
    assert decision.export_eligibility == ExportEligibility.BLOCKED_UNCITED
    assert decision.reason_codes == ("missing_source_provenance",)
    assert decision.send_authorized is False


def test_m6_blocks_guessed_email_without_independent_verifier_pass():
    conn = _connection()
    entity_id = _assemble_entity(conn)
    decision = evaluate_entity_promotion(conn, batch_id=M6_BATCH_ID, entity_id=entity_id)
    assert decision.promotion_tier == PromotionTier.CONTACT_CANDIDATE
    assert decision.status == GateStatus.UNKNOWN_NOT_PROMOTED
    assert decision.export_eligibility == ExportEligibility.BLOCKED_UNVERIFIED_CONTACT
    assert any("missing_independent_contact_verification" in reason for reason in decision.reason_codes)
    assert decision.send_authorized is False


def test_m6_refuses_wrong_batch_context_even_when_entity_has_fixture_evidence():
    conn = _connection()
    entity_id = _assemble_entity(conn)
    decision = evaluate_entity_promotion(conn, batch_id="batch_fixture_m6_wrong", entity_id=entity_id)
    assert decision.status == GateStatus.FAIL_CLOSED
    assert decision.export_eligibility == ExportEligibility.BLOCKED_UNKNOWN
    assert decision.reason_codes == ("batch_id_mismatch",)
    assert decision.send_authorized is False


def test_m6_verified_email_with_source_provenance_reaches_verified_contact_not_send_ready():
    conn = _connection()
    entity_id = _assemble_entity(conn)
    _insert_contact_passes(conn, entity_id)
    _insert_suppression(conn, entity_id=entity_id, status=SuppressionStatus.CLEAR.value)

    decision = evaluate_entity_promotion(conn, batch_id=M6_BATCH_ID, entity_id=entity_id)

    assert decision.status == GateStatus.PASS_
    assert decision.promotion_tier == PromotionTier.VERIFIED_CONTACT
    assert decision.export_eligibility == ExportEligibility.NOT_EXPORTABLE
    assert decision.reason_codes == ("verified_contact_not_enriched_for_internal_export",)
    assert decision.send_authorized is False


def test_m6_suppression_conflict_blocks_even_when_all_other_gates_pass():
    conn = _connection()
    entity_id = _assemble_entity(conn)
    _insert_contact_passes(conn, entity_id)
    _insert_suppression(conn, entity_id=entity_id, status=SuppressionStatus.SUPPRESSION_CONFLICT_HOLD.value)
    _insert_enrichment(conn, entity_id=entity_id)

    decision = evaluate_entity_promotion(conn, batch_id=M6_BATCH_ID, entity_id=entity_id)

    assert decision.status == GateStatus.UNKNOWN_NOT_PROMOTED
    assert decision.promotion_tier == PromotionTier.QUALIFIED_LEAD_REVIEW_REQUIRED
    assert decision.export_eligibility == ExportEligibility.BLOCKED_UNKNOWN
    assert decision.reason_codes == ("suppression_conflict_hold",)
    assert decision.send_authorized is False


def test_m6_blocks_placeholder_no_reply_internal_email():
    conn = _connection()
    entity_id = _assemble_entity(
        conn,
        fields={
            "business_name": "Fixture Build Group",
            "website_url": "https://fixturebuild.test/services",
            "trade_category": "general_contractor",
            "permitassist_icp_segment": "commercial_tenant_improvement_gc_design_build_remodeler",
            "low_call_relevance_signal": "explicit commercial tenant improvement build-outs",
            "contact_email": "no-reply@fixturebuild.test",
        },
    )
    _insert_contact_passes(conn, entity_id)
    decision = evaluate_entity_promotion(conn, batch_id=M6_BATCH_ID, entity_id=entity_id)
    assert decision.status == GateStatus.FAIL_CLOSED
    assert decision.export_eligibility == ExportEligibility.BLOCKED_UNVERIFIED_CONTACT
    assert decision.reason_codes == ("no_reply_or_internal_email",)


def test_m6_aggregator_only_evidence_requires_review_not_outreach_ready():
    conn = _connection()
    conn.execute(
        "INSERT INTO batches(batch_id, approved_scope_ref, adapter_id, started_at_utc, status, schema_version) "
        "VALUES (?, ?, 'permitassist', ?, 'open', ?)",
        (M6_BATCH_ID, "phase1_fixture_only_aggregator_test", NOW, PHASE1_SCHEMA_VERSION),
    )
    conn.execute(
        "INSERT INTO sources(source_id, source_class, source_name, base_url_or_path, official_or_first_party_flag, "
        "requires_login, paid_flag, allowed_phase, schema_version) VALUES (?, ?, ?, ?, 0, 0, 0, ?, ?)",
        (
            "src_aggregator_fixture",
            "aggregator_directory",
            "Fixture aggregator directory",
            "fixture://aggregator-directory/fixturebuild",
            "phase1_fixture_only",
            PHASE1_SCHEMA_VERSION,
        ),
    )
    conn.execute(
        "INSERT INTO source_observations(observation_id, source_id, batch_id, observed_at_utc, url_or_path, "
        "payload_hash_sha256, snippet_or_excerpt, blocked_or_captcha_flag, schema_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
        (
            "obs_aggregator_fixture",
            "src_aggregator_fixture",
            M6_BATCH_ID,
            NOW,
            "fixture://aggregator-directory/fixturebuild/profile",
            "hash_aggregator_fixture",
            "Directory says Fixture Build Group is a contractor.",
            PHASE1_SCHEMA_VERSION,
        ),
    )
    entity_id = _assemble_entity(conn, observation_id="obs_aggregator_fixture")

    decision = evaluate_entity_promotion(conn, batch_id=M6_BATCH_ID, entity_id=entity_id)

    assert decision.status == GateStatus.REVIEW_REQUIRED
    assert decision.promotion_tier == PromotionTier.QUALIFIED_LEAD_REVIEW_REQUIRED
    assert decision.export_eligibility == ExportEligibility.REVIEW_REQUIRED
    assert decision.reason_codes == ("aggregator_only_evidence_requires_corroboration",)


def test_m6_uncited_ai_enrichment_does_not_promote_to_outreach_ready():
    conn = _connection()
    entity_id = _assemble_entity(conn)
    _insert_contact_passes(conn, entity_id)
    _insert_suppression(conn, entity_id=entity_id, status=SuppressionStatus.CLEAR.value)
    _insert_enrichment(
        conn,
        entity_id=entity_id,
        fact_ids=[],
        observation_ids=[],
        validator_status=GateStatus.UNKNOWN_NOT_PROMOTED.value,
        unsupported_claim_count=1,
    )

    decision = evaluate_entity_promotion(conn, batch_id=M6_BATCH_ID, entity_id=entity_id)

    assert decision.status == GateStatus.UNKNOWN_NOT_PROMOTED
    assert decision.promotion_tier == PromotionTier.VERIFIED_CONTACT
    assert decision.export_eligibility == ExportEligibility.BLOCKED_UNCITED
    assert decision.reason_codes == ("uncited_or_invalid_enrichment_not_promoted",)


def test_m6_duplicate_identity_edge_requires_human_review_no_destructive_merge():
    conn = _connection()
    # Two observations so M5 creates two entities plus duplicate_candidate identity edge.
    result = run_fixture_connector(
        "contractor_first_party_website",
        documents=[_make_doc("services"), _make_doc("about")],
        batch_id=M6_BATCH_ID,
        mode=FetchMode.FIXTURE_ONLY,
    )
    write_connector_run_result(conn, result, batch=_batch())
    records = [
        AssemblyFixtureRecord(
            observation_id=result.observations[0]["observation_id"],
            field_relevant_snippet="services page",
            business_fields={"business_name": "Fixture Build Group", "trade_category": "general_contractor"},
        ),
        AssemblyFixtureRecord(
            observation_id=result.observations[1]["observation_id"],
            field_relevant_snippet="about page",
            business_fields={"business_name": "Fixture Build Group, Inc.", "trade_category": "general_contractor"},
        ),
    ]
    assemble_entities_and_facts(conn, batch_id=M6_BATCH_ID, records=records)
    entity_id = conn.execute("SELECT entity_id FROM entities ORDER BY entity_id LIMIT 1").fetchone()[0]

    decision = evaluate_entity_promotion(conn, batch_id=M6_BATCH_ID, entity_id=entity_id)

    assert decision.status == GateStatus.REVIEW_REQUIRED
    assert decision.promotion_tier == PromotionTier.QUALIFIED_LEAD_REVIEW_REQUIRED
    assert decision.export_eligibility == ExportEligibility.REVIEW_REQUIRED
    assert decision.identity_edge_ids
    assert decision.reason_codes == ("identity_ambiguity_requires_human_review",)
    assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 2


def test_m6_positive_golden_path_reaches_internal_review_only_outreach_ready_no_send():
    conn = _connection()
    entity_id = _assemble_entity(conn)
    email_fact_id = _insert_contact_passes(conn, entity_id)
    suppression_event_id = _insert_suppression(conn, entity_id=entity_id, fact_id=email_fact_id, status=SuppressionStatus.CLEAR.value)
    enrichment_event_id = _insert_enrichment(conn, entity_id=entity_id)

    decision = promote_entity(conn, batch_id=M6_BATCH_ID, entity_id=entity_id)

    assert decision.status == GateStatus.PASS_
    assert decision.promotion_tier == PromotionTier.OUTREACH_READY_INTERNAL_ONLY
    assert decision.export_eligibility == ExportEligibility.INTERNAL_REVIEW_ONLY
    assert decision.reason_codes == ("outreach_ready_internal_review_only_no_send",)
    assert decision.suppression_event_id == suppression_event_id
    assert decision.enrichment_event_id == enrichment_event_id
    assert decision.send_authorized is False
    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 0

    event_rows = conn.execute(
        "SELECT gate_name, gate_version, result_status, reason_codes, network_used_flag, raw_result_ref "
        "FROM verification_events WHERE gate_name = ?",
        ("phase1_m6_promotion_eligibility_gate",),
    ).fetchall()
    assert len(event_rows) == 1
    gate_name, gate_version, result_status, reason_codes, network_used_flag, raw_result_ref = event_rows[0]
    assert gate_name == "phase1_m6_promotion_eligibility_gate"
    assert gate_version == PROMOTION_VERSION
    assert result_status == GateStatus.PASS_.value
    assert json.loads(reason_codes) == ["outreach_ready_internal_review_only_no_send"]
    assert network_used_flag == 0
    raw = json.loads(raw_result_ref)
    assert raw["promotion_tier"] == PromotionTier.OUTREACH_READY_INTERNAL_ONLY.value
    assert raw["export_eligibility"] == ExportEligibility.INTERNAL_REVIEW_ONLY.value
    assert raw["send_authorized"] is False


def test_m6_emits_exact_machine_readable_reason_codes_for_missing_gates():
    conn = _connection()
    entity_id = _assemble_entity(conn)
    email_fact_id = _fact_id(conn, entity_id, "contact_email")
    _insert_verification(conn, entity_id=entity_id, fact_id=email_fact_id, gate_name="email_syntax_gate")

    decision = evaluate_entity_promotion(conn, batch_id=M6_BATCH_ID, entity_id=entity_id)

    assert decision.status == GateStatus.UNKNOWN_NOT_PROMOTED
    assert decision.reason_codes == (
        "missing_independent_contact_verification:contact_observation_gate,domain_quality_gate",
    )
    assert all(" " not in reason for reason in decision.reason_codes)


def test_m6_rejects_prior_verifier_pass_that_used_network_in_phase1():
    conn = _connection()
    entity_id = _assemble_entity(conn)
    email_fact_id = _fact_id(conn, entity_id, "contact_email")
    _insert_verification(
        conn,
        entity_id=entity_id,
        fact_id=email_fact_id,
        gate_name="contact_observation_gate",
        network_used_flag=1,
    )
    _insert_verification(conn, entity_id=entity_id, fact_id=email_fact_id, gate_name="email_syntax_gate")
    _insert_verification(conn, entity_id=entity_id, fact_id=email_fact_id, gate_name="domain_quality_gate")

    decision = evaluate_entity_promotion(conn, batch_id=M6_BATCH_ID, entity_id=entity_id)

    assert decision.status == GateStatus.UNKNOWN_NOT_PROMOTED
    assert decision.export_eligibility == ExportEligibility.BLOCKED_UNVERIFIED_CONTACT
    assert decision.reason_codes == (
        "missing_independent_contact_verification:contact_observation_gate",
    )


def test_m6_write_decision_refuses_any_send_authorized_decision_object():
    conn = _connection()
    entity_id = _assemble_entity(conn)
    _insert_contact_passes(conn, entity_id)
    _insert_suppression(conn, entity_id=entity_id, status=SuppressionStatus.CLEAR.value)
    decision = evaluate_entity_promotion(conn, batch_id=M6_BATCH_ID, entity_id=entity_id)
    unsafe_decision = replace(decision, send_authorized=True)

    with pytest.raises(PromotionSafetyError):
        write_promotion_decision_event(conn, unsafe_decision)

    assert conn.execute(
        "SELECT COUNT(*) FROM verification_events WHERE gate_name = ?",
        ("phase1_m6_promotion_eligibility_gate",),
    ).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 0


def test_m6_write_decision_is_idempotent_and_never_writes_export_events():
    conn = _connection()
    entity_id = _assemble_entity(conn)
    _insert_contact_passes(conn, entity_id)
    _insert_suppression(conn, entity_id=entity_id, status=SuppressionStatus.CLEAR.value)
    decision = evaluate_entity_promotion(conn, batch_id=M6_BATCH_ID, entity_id=entity_id)

    first = write_promotion_decision_event(conn, decision)
    second = write_promotion_decision_event(conn, decision)

    assert first == 1
    assert second == 0
    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 0
    assert conn.execute(
        "SELECT send_authorized FROM export_events"
    ).fetchall() == []
