"""Phase 1 M7 internal export contract tests.

M7 is the first milestone allowed to write ``export_events``. It remains a
fixture-only, no-network, no-outreach handoff: export records are for Boban/Titi
internal review only and must never authorize send/live outreach.
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
from lead_pipeline.promotion import PromotionDecision, evaluate_entity_promotion, promote_entity
from lead_pipeline.schema import PHASE1_SCHEMA_VERSION
from lead_pipeline.internal_export import (
    INTERNAL_EXPORT_SCHEMA_VERSION,
    INTERNAL_EXPORT_TARGET,
    InternalExportSafetyError,
    build_internal_export_contract,
    prepare_internal_export,
    write_internal_export_event,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
M7_BATCH_ID = "batch_fixture_m7"
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


def _batch(batch_id: str = M7_BATCH_ID) -> dict[str, object]:
    return {
        "batch_id": batch_id,
        "approved_scope_ref": "phase1_fixture_only_lead_pipeline_m7",
        "adapter_id": "permitassist",
        "started_at_utc": NOW,
        "status": "open",
        "schema_version": PHASE1_SCHEMA_VERSION,
    }


def _make_doc(path: str = "services/commercial-ti", snippet: str | None = None) -> FixtureDocument:
    return FixtureDocument(
        url_or_path=f"fixture://contractor-first-party/fixturebuild/{path}",
        fetched_at_utc=NOW,
        payload_text="Fixture Build Group commercial TI contractor synthetic payload.",
        snippet=snippet or "Fixture Build Group performs commercial tenant improvement build-outs.",
        content_type="text/plain",
    )


def _populate_first_party_observation(conn: sqlite3.Connection, *, batch_id: str = M7_BATCH_ID) -> str:
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
    batch_id: str = M7_BATCH_ID,
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
    batch_id: str = M7_BATCH_ID,
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
            M7_BATCH_ID,
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
    output_json: dict[str, object] | None = None,
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
            M7_BATCH_ID,
            entity_id,
            json.dumps(fact_ids, sort_keys=True),
            json.dumps(observation_ids, sort_keys=True),
            "fixture_rule_v1",
            json.dumps(output_json or {"summary": "Source-backed commercial TI contractor fixture summary."}, sort_keys=True),
            1.0 if validator_status == GateStatus.PASS_.value else 0.0,
            unsupported_claim_count,
            validator_status,
            json.dumps(["source_backed_enrichment"]),
            PHASE1_SCHEMA_VERSION,
        ),
    )
    conn.commit()
    return event_id


def _golden_internal_ready(conn: sqlite3.Connection) -> tuple[str, PromotionDecision, str, str, str]:
    entity_id = _assemble_entity(conn)
    email_fact_id = _insert_contact_passes(conn, entity_id)
    suppression_event_id = _insert_suppression(
        conn, entity_id=entity_id, fact_id=email_fact_id, status=SuppressionStatus.CLEAR.value
    )
    enrichment_event_id = _insert_enrichment(conn, entity_id=entity_id)
    decision = promote_entity(conn, batch_id=M7_BATCH_ID, entity_id=entity_id)
    m6_event_id = conn.execute(
        "SELECT verification_event_id FROM verification_events WHERE gate_name = 'phase1_m6_promotion_eligibility_gate'"
    ).fetchone()[0]
    return entity_id, decision, m6_event_id, suppression_event_id, enrichment_event_id


# ---------------------------------------------------------------------------
# Version + module hygiene


def test_m7_version_and_target_are_internal_review_only():
    assert INTERNAL_EXPORT_SCHEMA_VERSION == "lead_pipeline_phase1_m7_internal_export_v1"
    assert INTERNAL_EXPORT_TARGET == "internal_review_queue"


def test_m7_module_has_no_network_or_outreach_imports():
    path = REPO_ROOT / "lead_pipeline" / "internal_export.py"
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
    assert not offenders, "M7 internal export must stay fixture-only/no-network: " + ", ".join(offenders)


# ---------------------------------------------------------------------------
# Required fail-closed behavior


def test_m7_refuses_to_export_without_persisted_m6_promotion_event():
    conn = _connection()
    entity_id = _assemble_entity(conn)
    email_fact_id = _insert_contact_passes(conn, entity_id)
    _insert_suppression(conn, entity_id=entity_id, fact_id=email_fact_id, status=SuppressionStatus.CLEAR.value)
    _insert_enrichment(conn, entity_id=entity_id)
    decision = evaluate_entity_promotion(conn, batch_id=M7_BATCH_ID, entity_id=entity_id)

    with pytest.raises(InternalExportSafetyError, match="persisted M6 promotion event"):
        build_internal_export_contract(conn, decision)

    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 0


def test_m7_blocks_non_internal_review_decision_even_if_contact_verified():
    conn = _connection()
    entity_id = _assemble_entity(conn)
    email_fact_id = _insert_contact_passes(conn, entity_id)
    _insert_suppression(conn, entity_id=entity_id, fact_id=email_fact_id, status=SuppressionStatus.CLEAR.value)
    decision = promote_entity(conn, batch_id=M7_BATCH_ID, entity_id=entity_id)

    with pytest.raises(InternalExportSafetyError, match="internal_review_only"):
        build_internal_export_contract(conn, decision)

    assert decision.export_eligibility == ExportEligibility.NOT_EXPORTABLE
    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 0


def test_m7_blocks_review_required_duplicate_identity_from_export_event():
    conn = _connection()
    result = run_fixture_connector(
        "contractor_first_party_website",
        documents=[_make_doc("services"), _make_doc("about")],
        batch_id=M7_BATCH_ID,
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
    assemble_entities_and_facts(conn, batch_id=M7_BATCH_ID, records=records)
    entity_id = conn.execute("SELECT entity_id FROM entities ORDER BY entity_id LIMIT 1").fetchone()[0]
    decision = promote_entity(conn, batch_id=M7_BATCH_ID, entity_id=entity_id)

    with pytest.raises(InternalExportSafetyError, match="decision is not exportable"):
        build_internal_export_contract(conn, decision)

    assert decision.export_eligibility == ExportEligibility.REVIEW_REQUIRED
    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 0


def test_m7_refuses_any_send_authorized_or_future_live_decision_object():
    conn = _connection()
    _, decision, _, _, _ = _golden_internal_ready(conn)

    with pytest.raises(InternalExportSafetyError, match="send_authorized"):
        build_internal_export_contract(conn, replace(decision, send_authorized=True))

    with pytest.raises(InternalExportSafetyError, match="future live outreach"):
        build_internal_export_contract(
            conn,
            replace(
                decision,
                promotion_tier=PromotionTier.LIVE_OUTREACH_READY_FUTURE_PHASE,
                export_eligibility=ExportEligibility.FUTURE_LIVE_OUTREACH_RESERVED,
            ),
        )

    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 0


def test_m7_refuses_secret_like_fact_or_enrichment_content_before_signing():
    conn = _connection()
    stripe_like = "sk_" + "liv" + "e_" + "abcdefghi"
    entity_id = _assemble_entity(
        conn,
        fields={
            "business_name": f"{stripe_like} Fixture Build Group",
            "website_url": "https://fixturebuild.test/services",
            "trade_category": "general_contractor",
            "permitassist_icp_segment": "commercial_tenant_improvement_gc_design_build_remodeler",
            "low_call_relevance_signal": "explicit commercial tenant improvement build-outs",
            "contact_email": "owner@fixturebuild.test",
        },
    )
    email_fact_id = _insert_contact_passes(conn, entity_id)
    _insert_suppression(conn, entity_id=entity_id, fact_id=email_fact_id, status=SuppressionStatus.CLEAR.value)
    _insert_enrichment(conn, entity_id=entity_id)
    decision = promote_entity(conn, batch_id=M7_BATCH_ID, entity_id=entity_id)

    with pytest.raises(InternalExportSafetyError, match="secret-like"):
        build_internal_export_contract(conn, decision)

    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 0


def test_m7_refuses_secret_like_freeform_export_parameters():
    conn = _connection()
    _, decision, _, _, _ = _golden_internal_ready(conn)
    stripe_like = "sk_" + "liv" + "e_" + "abcdefghi"

    with pytest.raises(InternalExportSafetyError, match="secret-like"):
        build_internal_export_contract(conn, decision, campaign_id=stripe_like)

    with pytest.raises(InternalExportSafetyError, match="secret-like"):
        build_internal_export_contract(conn, decision, human_review_event_id="review_token=abcdef123456")

    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 0


def test_m7_refuses_human_review_event_for_different_entity():
    conn = _connection()
    _, decision, _, _, _ = _golden_internal_ready(conn)
    conn.execute(
        "INSERT INTO entities(entity_id, entity_type, canonical_label, normalized_key, status, schema_version) "
        "VALUES (?, 'business', ?, ?, ?, ?)",
        ("ent_other_review_target", "Other Review Target", "other review target", PromotionTier.RAW_DISCOVERY.value, PHASE1_SCHEMA_VERSION),
    )
    conn.execute(
        "INSERT INTO human_review_events(review_event_id, entity_id, reviewer, reviewed_at_utc, rubric_version, "
        "decision, reason_codes, input_fact_ids, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "review_other_entity",
            "ent_other_review_target",
            "titi_fixture_reviewer",
            NOW,
            "phase1_m7_fixture_rubric",
            GateStatus.PASS_.value,
            json.dumps(["fixture_review"]),
            json.dumps(list(decision.eligible_fact_ids)),
            PHASE1_SCHEMA_VERSION,
        ),
    )
    conn.commit()

    with pytest.raises(InternalExportSafetyError, match="human review event does not belong"):
        build_internal_export_contract(conn, decision, human_review_event_id="review_other_entity")

    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 0


def test_m7_refuses_missing_human_review_event():
    conn = _connection()
    _, decision, _, _, _ = _golden_internal_ready(conn)

    with pytest.raises(InternalExportSafetyError, match="referenced human_review_events row is missing"):
        build_internal_export_contract(conn, decision, human_review_event_id="review_missing")

    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 0


def test_m7_refuses_unsupported_export_target():
    conn = _connection()
    _, decision, _, _, _ = _golden_internal_ready(conn)

    with pytest.raises(InternalExportSafetyError, match="internal_review_queue"):
        build_internal_export_contract(conn, decision, export_target="crm")

    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 0


def test_m7_refuses_network_tainted_persisted_m6_event():
    conn = _connection()
    _, decision, m6_event_id, _, _ = _golden_internal_ready(conn)
    conn.execute("DROP TRIGGER verification_events_append_only_no_update")
    conn.execute(
        "UPDATE verification_events SET network_used_flag = 1 WHERE verification_event_id = ?",
        (m6_event_id,),
    )
    conn.commit()

    with pytest.raises(InternalExportSafetyError, match="used network"):
        build_internal_export_contract(conn, decision)

    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 0


def test_m7_refuses_enrichment_lineage_that_does_not_cover_decision():
    conn = _connection()
    _, decision, _, _, enrichment_event_id = _golden_internal_ready(conn)
    conn.execute("DROP TRIGGER enrichment_events_append_only_no_update")
    conn.execute(
        "UPDATE enrichment_events SET input_fact_ids = ? WHERE enrichment_event_id = ?",
        (json.dumps(["fact_unrelated"]), enrichment_event_id),
    )
    conn.commit()

    with pytest.raises(InternalExportSafetyError, match="enrichment event does not cover"):
        build_internal_export_contract(conn, decision)

    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 0


def test_m7_refuses_tampered_persisted_m6_event_mismatch():
    conn = _connection()
    _, decision, m6_event_id, _, _ = _golden_internal_ready(conn)
    # Append-only table blocks UPDATE in normal operation; disable only to simulate storage corruption.
    conn.execute("PRAGMA recursive_triggers = OFF")
    conn.execute("DROP TRIGGER verification_events_append_only_no_update")
    conn.execute(
        "UPDATE verification_events SET raw_result_ref = ? WHERE verification_event_id = ?",
        (json.dumps({"promotion_tier": PromotionTier.VERIFIED_CONTACT.value}), m6_event_id),
    )
    conn.commit()

    with pytest.raises(InternalExportSafetyError, match="does not match decision"):
        build_internal_export_contract(conn, decision)

    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Positive contract, idempotency, and SQL no-send guard


def test_m7_positive_path_writes_signed_internal_review_export_event_no_send():
    conn = _connection()
    entity_id, decision, m6_event_id, suppression_event_id, enrichment_event_id = _golden_internal_ready(conn)

    contract = build_internal_export_contract(conn, decision)
    inserted = write_internal_export_event(conn, contract)

    assert inserted == 1
    assert contract.export_event_id.startswith("exp_m7_internal_")
    assert contract.export_target == INTERNAL_EXPORT_TARGET
    assert contract.status == ExportEligibility.INTERNAL_REVIEW_ONLY
    assert contract.send_authorized is False
    assert contract.lead_entity_id == entity_id
    assert m6_event_id in contract.included_verification_event_ids
    assert contract.suppression_event_id == suppression_event_id
    assert contract.enrichment_event_id == enrichment_event_id
    assert contract.signed_payload_hash_sha256
    assert contract.signature.startswith("phase1-local-signature:")

    row = conn.execute(
        "SELECT export_target, export_schema_version, lead_entity_id, included_fact_ids, "
        "included_source_observation_ids, included_verification_event_ids, suppression_event_id, "
        "enrichment_event_id, signed_payload_hash_sha256, signature, send_authorized, status, blocked_reason "
        "FROM export_events WHERE export_event_id = ?",
        (contract.export_event_id,),
    ).fetchone()
    assert row is not None
    (
        export_target,
        export_schema_version,
        lead_entity_id,
        included_fact_ids,
        included_observation_ids,
        included_verification_ids,
        persisted_suppression_id,
        persisted_enrichment_id,
        signed_hash,
        signature,
        send_authorized,
        status,
        blocked_reason,
    ) = row
    assert export_target == INTERNAL_EXPORT_TARGET
    assert export_schema_version == INTERNAL_EXPORT_SCHEMA_VERSION
    assert lead_entity_id == entity_id
    assert json.loads(included_fact_ids) == list(contract.included_fact_ids)
    assert json.loads(included_observation_ids) == list(contract.included_source_observation_ids)
    assert json.loads(included_verification_ids) == list(contract.included_verification_event_ids)
    assert persisted_suppression_id == suppression_event_id
    assert persisted_enrichment_id == enrichment_event_id
    assert signed_hash == contract.signed_payload_hash_sha256
    assert signature == contract.signature
    assert send_authorized == 0
    assert status == ExportEligibility.INTERNAL_REVIEW_ONLY.value
    assert blocked_reason == "no_send_internal_review_only"


def test_m7_prepare_internal_export_is_idempotent_and_never_authorizes_send():
    conn = _connection()
    entity_id, _, _, _, _ = _golden_internal_ready(conn)

    first = prepare_internal_export(conn, batch_id=M7_BATCH_ID, entity_id=entity_id)
    second = prepare_internal_export(conn, batch_id=M7_BATCH_ID, entity_id=entity_id)

    assert first.inserted == 1
    assert second.inserted == 0
    assert first.contract.export_event_id == second.contract.export_event_id
    rows = conn.execute("SELECT send_authorized, status FROM export_events").fetchall()
    assert rows == [(0, ExportEligibility.INTERNAL_REVIEW_ONLY.value)]


def test_m7_database_schema_rejects_send_authorized_even_if_payload_is_tampered():
    conn = _connection()
    _, decision, _, _, _ = _golden_internal_ready(conn)
    contract = build_internal_export_contract(conn, decision)
    unsafe = replace(contract, send_authorized=True)

    with pytest.raises(InternalExportSafetyError, match="send_authorized"):
        write_internal_export_event(conn, unsafe)

    assert conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0] == 0
