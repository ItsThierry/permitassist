"""Deterministic end-to-end fixture runner for Phase 1 M8.

M8 stitches the M1-M7 fixture-only pieces into one local review run:

* initialize the M1 SQLite fixture schema;
* run the M3 fixture connector and M4 event writer;
* assemble M5 entities/facts from explicit fixture records;
* add deterministic local verifier/suppression/enrichment fixture events;
* run M6 promotion decisions for every assembled lead; and
* write M7 no-send internal-review export rows only for eligible leads.

The runner performs no network, DNS, SMTP, browser, scraping, paid-provider,
filesystem persistence, subprocess, CRM, webhook, or outreach action. It exists
only to prove the internal pipeline joins and lineage are coherent before any
future real-data pilot.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .assembly import AssemblyFixtureRecord, AssemblySummary, assemble_entities_and_facts
from .connectors import FetchMode, FixtureDocument, run_fixture_connector
from .contracts import ExportEligibility, GateStatus, PromotionTier, SuppressionStatus
from .event_writer import WriteSummary, initialize_sqlite_schema, write_connector_run_result
from .internal_export import build_internal_export_contract, write_internal_export_event
from .promotion import PROMOTION_GATE_NAME, PromotionDecision, evaluate_entity_promotion, promote_entity
from .schema import PHASE1_SCHEMA_VERSION, REQUIRED_TABLES

PHASE1_M8_RUNNER_VERSION = "lead_pipeline_phase1_m8_fixture_runner_v1"
M8_FIXTURE_ID = "golden"
M8_BATCH_ID = "batch_fixture_m8_golden"
M8_OBSERVED_AT_UTC = "2026-05-20T00:00:00Z"


class Phase1RunnerSafetyError(RuntimeError):
    """Raised when M8 cannot run within the approved fixture-only contract."""


@dataclass(frozen=True)
class LeadPipelineResult:
    """Per-lead M8 review result with exact lineage IDs."""

    entity_id: str
    canonical_label: str
    promotion_tier: str
    gate_status: str
    export_eligibility: str
    reason_codes: tuple[str, ...]
    eligible_fact_ids: tuple[str, ...]
    source_observation_ids: tuple[str, ...]
    verification_event_ids: tuple[str, ...]
    suppression_event_id: str | None
    enrichment_event_id: str | None
    export_event_id: str | None
    send_authorized: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "canonical_label": self.canonical_label,
            "promotion_tier": self.promotion_tier,
            "gate_status": self.gate_status,
            "export_eligibility": self.export_eligibility,
            "reason_codes": list(self.reason_codes),
            "eligible_fact_ids": list(self.eligible_fact_ids),
            "source_observation_ids": list(self.source_observation_ids),
            "verification_event_ids": list(self.verification_event_ids),
            "suppression_event_id": self.suppression_event_id,
            "enrichment_event_id": self.enrichment_event_id,
            "export_event_id": self.export_event_id,
            "send_authorized": self.send_authorized,
        }


@dataclass(frozen=True)
class Phase1PipelineSummary:
    """JSON-safe M8 run summary."""

    runner_version: str
    fixture_id: str
    batch_id: str
    safety: Mapping[str, bool]
    connector_run_ids: tuple[str, ...]
    write_summary: Mapping[str, int | str]
    assembly_summary: Mapping[str, Any]
    table_counts: Mapping[str, int]
    leads: tuple[LeadPipelineResult, ...]
    internal_review_export_event_ids: tuple[str, ...]

    @property
    def exported_lead_count(self) -> int:
        return sum(1 for lead in self.leads if lead.export_event_id)

    @property
    def blocked_lead_count(self) -> int:
        return sum(1 for lead in self.leads if not lead.export_event_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runner_version": self.runner_version,
            "fixture_id": self.fixture_id,
            "batch_id": self.batch_id,
            "safety": dict(self.safety),
            "connector_run_ids": list(self.connector_run_ids),
            "write_summary": dict(self.write_summary),
            "assembly_summary": dict(self.assembly_summary),
            "table_counts": dict(self.table_counts),
            "leads": [lead.to_dict() for lead in self.leads],
            "internal_review_export_event_ids": list(self.internal_review_export_event_ids),
            "exported_lead_count": self.exported_lead_count,
            "blocked_lead_count": self.blocked_lead_count,
        }


@dataclass(frozen=True)
class Phase1PipelineRunResult:
    """M8 result object: JSON-safe summary plus the in-memory SQLite DB for tests."""

    summary: Phase1PipelineSummary
    conn: sqlite3.Connection

    def to_dict(self) -> dict[str, Any]:
        return self.summary.to_dict()


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _batch_payload() -> dict[str, Any]:
    return {
        "batch_id": M8_BATCH_ID,
        "approved_scope_ref": "phase1_fixture_only_lead_pipeline_m8_e2e_internal_review",
        "adapter_id": "permitassist",
        "started_at_utc": M8_OBSERVED_AT_UTC,
        "completed_at_utc": None,
        "status": "open",
        "source_mix_json": json.dumps(["contractor_first_party_website"], sort_keys=True),
        "notes": "Synthetic M8 golden fixture; no network, no paid API, no outreach.",
        "schema_version": PHASE1_SCHEMA_VERSION,
    }


def _fixture_documents() -> tuple[FixtureDocument, ...]:
    return (
        FixtureDocument(
            url_or_path="fixture://contractor-first-party/fixture-build/services-commercial-ti",
            fetched_at_utc=M8_OBSERVED_AT_UTC,
            payload_text="Fixture Build Group performs commercial tenant improvement build-outs for restaurants, clinics, and offices. Contact owner@fixturebuild.test.",
            snippet="Fixture Build Group performs commercial tenant improvement build-outs for restaurants, clinics, and offices.",
            content_type="text/plain",
        ),
        FixtureDocument(
            url_or_path="fixture://contractor-first-party/quiet-clinic-contractors/services",
            fetched_at_utc=M8_OBSERVED_AT_UTC,
            payload_text="Quiet Clinic Contractors performs clinic tenant improvements but publishes no email contact in this fixture.",
            snippet="Quiet Clinic Contractors performs clinic tenant improvements.",
            content_type="text/plain",
        ),
        FixtureDocument(
            url_or_path="fixture://contractor-first-party/suppressed-ti-contractors/services",
            fetched_at_utc=M8_OBSERVED_AT_UTC,
            payload_text="Suppressed TI Contractors performs office and restaurant tenant improvements. Contact owner@suppressedti.test.",
            snippet="Suppressed TI Contractors performs office and restaurant tenant improvements.",
            content_type="text/plain",
        ),
    )


def _assembly_records(observation_ids: Sequence[str]) -> tuple[AssemblyFixtureRecord, ...]:
    if len(observation_ids) != 3:
        raise Phase1RunnerSafetyError("M8 golden fixture expects exactly three observations")
    return (
        AssemblyFixtureRecord(
            observation_id=observation_ids[0],
            field_relevant_snippet="Fixture Build Group performs commercial tenant improvement build-outs for restaurants, clinics, and offices.",
            business_fields={
                "business_name": "Fixture Build Group",
                "website_url": "https://fixturebuild.test/services",
                "trade_category": "general_contractor",
                "permitassist_icp_segment": "commercial_tenant_improvement_gc_design_build_remodeler",
                "low_call_relevance_signal": "explicit commercial tenant improvement build-outs",
                "contact_email": "owner@fixturebuild.test",
            },
        ),
        AssemblyFixtureRecord(
            observation_id=observation_ids[1],
            field_relevant_snippet="Quiet Clinic Contractors performs clinic tenant improvements.",
            business_fields={
                "business_name": "Quiet Clinic Contractors",
                "website_url": "https://quietcliniccontractors.test/services",
                "trade_category": "general_contractor",
                "permitassist_icp_segment": "commercial_tenant_improvement_gc_design_build_remodeler",
                "low_call_relevance_signal": "clinic tenant improvement evidence but no observed contact",
            },
        ),
        AssemblyFixtureRecord(
            observation_id=observation_ids[2],
            field_relevant_snippet="Suppressed TI Contractors performs office and restaurant tenant improvements.",
            business_fields={
                "business_name": "Suppressed TI Contractors",
                "website_url": "https://suppressedti.test/services",
                "trade_category": "general_contractor",
                "permitassist_icp_segment": "commercial_tenant_improvement_gc_design_build_remodeler",
                "low_call_relevance_signal": "office and restaurant tenant improvement evidence",
                "contact_email": "owner@suppressedti.test",
            },
        ),
    )


def _rows(conn: sqlite3.Connection, query: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    cursor = conn.execute(query, tuple(params))
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _quote_required_table(name: str) -> str:
    if name not in REQUIRED_TABLES:
        raise Phase1RunnerSafetyError(f"unexpected Phase 1 table name {name!r}")
    return '"' + name.replace('"', '""') + '"'


def _allowed_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    quoted = _quote_required_table(table)
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(" + quoted + ")")}


def _insert_or_ignore(conn: sqlite3.Connection, table: str, payload: Mapping[str, Any]) -> int:
    quoted_table = _quote_required_table(table)
    allowed_columns = _allowed_columns(conn, table)
    columns = list(payload.keys())
    unexpected = sorted(set(columns) - allowed_columns)
    if unexpected:
        raise Phase1RunnerSafetyError(f"unexpected columns for {table}: {unexpected}")
    placeholders = ",".join("?" for _ in columns)
    col_list = ",".join('"' + column.replace('"', '""') + '"' for column in columns)
    query = "INSERT OR IGNORE INTO " + quoted_table + " (" + col_list + ") VALUES (" + placeholders + ")"
    cursor = conn.execute(query, [payload[column] for column in columns])
    return 1 if cursor.rowcount and cursor.rowcount > 0 else 0


def _fact_id(conn: sqlite3.Connection, entity_id: str, fact_type: str) -> str | None:
    row = conn.execute(
        "SELECT fact_id FROM facts WHERE entity_id = ? AND fact_type = ? ORDER BY fact_id",
        (entity_id, fact_type),
    ).fetchone()
    return str(row[0]) if row else None


def _fact_ids(conn: sqlite3.Connection, entity_id: str) -> tuple[str, ...]:
    return tuple(
        str(row[0])
        for row in conn.execute("SELECT fact_id FROM facts WHERE entity_id = ? ORDER BY fact_id", (entity_id,))
    )


def _observation_ids_for_entity(conn: sqlite3.Connection, entity_id: str) -> tuple[str, ...]:
    return tuple(
        str(row[0])
        for row in conn.execute(
            "SELECT DISTINCT source_observation_id FROM facts WHERE entity_id = ? ORDER BY source_observation_id",
            (entity_id,),
        )
    )


def _insert_verification_event(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    fact_id: str,
    gate_name: str,
    reason_code: str,
) -> str:
    event_id = "ve_m8_" + _stable_hash([entity_id, fact_id, gate_name, reason_code])[:24]
    payload = {
        "verification_event_id": event_id,
        "batch_id": M8_BATCH_ID,
        "target_entity_id": entity_id,
        "target_fact_id": fact_id,
        "gate_name": gate_name,
        "gate_version": PHASE1_M8_RUNNER_VERSION,
        "input_hash": _stable_hash([PHASE1_M8_RUNNER_VERSION, entity_id, fact_id, gate_name]),
        "result_status": GateStatus.PASS_.value,
        "score": 1.0,
        "reason_codes": json.dumps([reason_code], sort_keys=True),
        "network_used_flag": 0,
        "cached_result_flag": 0,
        "observed_at_utc": M8_OBSERVED_AT_UTC,
        "expires_at_utc": None,
        "raw_result_ref": None,
        "cost_event_id": None,
        "schema_version": PHASE1_SCHEMA_VERSION,
    }
    _insert_or_ignore(conn, "verification_events", payload)
    return event_id


def _insert_contact_passes(conn: sqlite3.Connection, entity_id: str) -> tuple[str, ...]:
    contact_fact_id = _fact_id(conn, entity_id, "contact_email")
    if not contact_fact_id:
        return ()
    return tuple(
        _insert_verification_event(
            conn,
            entity_id=entity_id,
            fact_id=contact_fact_id,
            gate_name=gate_name,
            reason_code=reason_code,
        )
        for gate_name, reason_code in (
            ("contact_observation_gate", "m8_contact_observed_with_provenance"),
            ("email_syntax_gate", "m8_email_syntax_pass"),
            ("domain_quality_gate", "m8_domain_quality_pass_mocked"),
        )
    )


def _insert_suppression_event(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    fact_id: str | None,
    status: SuppressionStatus,
) -> str:
    event_id = "sup_m8_" + _stable_hash([entity_id, fact_id, status.value])[:24]
    payload = {
        "suppression_event_id": event_id,
        "batch_id": M8_BATCH_ID,
        "target_entity_id": entity_id,
        "target_fact_id": fact_id,
        "suppression_source_ref": "fixture://suppression/internal-review-list",
        "suppression_snapshot_hash": _stable_hash(["suppression", entity_id, fact_id, status.value]),
        "status": status.value,
        "reason": status.value,
        "checked_at_utc": M8_OBSERVED_AT_UTC,
        "campaign_id": "phase1_m8_fixture_campaign",
        "expires_at_utc": None,
        "schema_version": PHASE1_SCHEMA_VERSION,
    }
    _insert_or_ignore(conn, "suppression_events", payload)
    return event_id


def _insert_enrichment_event(conn: sqlite3.Connection, *, entity_id: str) -> str:
    fact_ids = _fact_ids(conn, entity_id)
    observation_ids = _observation_ids_for_entity(conn, entity_id)
    if not fact_ids or not observation_ids:
        raise Phase1RunnerSafetyError(f"cannot enrich entity {entity_id!r} without fact/source lineage")
    event_id = "enrich_m8_" + _stable_hash([entity_id, fact_ids, observation_ids])[:24]
    payload = {
        "enrichment_event_id": event_id,
        "batch_id": M8_BATCH_ID,
        "entity_id": entity_id,
        "input_fact_ids": json.dumps(list(fact_ids), sort_keys=True),
        "input_observation_ids": json.dumps(list(observation_ids), sort_keys=True),
        "model_or_rule_version": PHASE1_M8_RUNNER_VERSION,
        "prompt_template_hash": None,
        "output_json": json.dumps(
            {"summary": "Source-backed commercial TI contractor fixture summary for internal review."},
            sort_keys=True,
        ),
        "quality_score": 1.0,
        "unsupported_claim_count": 0,
        "validator_status": GateStatus.PASS_.value,
        "validator_reason_codes": json.dumps(["m8_source_backed_enrichment"], sort_keys=True),
        "cost_event_id": None,
        "schema_version": PHASE1_SCHEMA_VERSION,
    }
    _insert_or_ignore(conn, "enrichment_events", payload)
    return event_id


def _entity_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return _rows(
        conn,
        "SELECT entity_id, canonical_label FROM entities ORDER BY canonical_label, entity_id",
    )


def _m6_event_id(conn: sqlite3.Connection, decision: PromotionDecision) -> str:
    rows = _rows(
        conn,
        "SELECT verification_event_id FROM verification_events WHERE gate_name = ? AND target_entity_id = ? "
        "AND input_hash = ? ORDER BY verification_event_id",
        (PROMOTION_GATE_NAME, decision.entity_id, decision.input_hash),
    )
    if len(rows) != 1:
        raise Phase1RunnerSafetyError("M8 expected exactly one persisted M6 event per decision")
    return str(rows[0]["verification_event_id"])


def _existing_m6_decision(conn: sqlite3.Connection, *, entity_id: str) -> PromotionDecision | None:
    rows = _rows(
        conn,
        "SELECT verification_event_id, batch_id, target_entity_id, input_hash, result_status, score, "
        "reason_codes, raw_result_ref, network_used_flag, schema_version FROM verification_events WHERE gate_name = ? AND target_entity_id = ? "
        "ORDER BY verification_event_id",
        (PROMOTION_GATE_NAME, entity_id),
    )
    if not rows:
        return None
    if len(rows) != 1:
        raise Phase1RunnerSafetyError("expected exactly one persisted M6 event per entity")
    row = rows[0]
    if int(row["network_used_flag"] or 0) != 0:
        raise Phase1RunnerSafetyError("persisted M6 event used network and cannot be replayed by M8")
    if str(row["schema_version"]) != PHASE1_SCHEMA_VERSION:
        raise Phase1RunnerSafetyError("persisted M6 event schema_version does not match Phase 1")
    try:
        raw = json.loads(str(row["raw_result_ref"]))
    except (TypeError, json.JSONDecodeError) as exc:
        raise Phase1RunnerSafetyError("persisted M6 event raw_result_ref is malformed") from exc
    if not isinstance(raw, dict):
        raise Phase1RunnerSafetyError("persisted M6 event raw_result_ref is not an object")
    try:
        promotion_tier = PromotionTier(str(raw["promotion_tier"]))
        export_eligibility = ExportEligibility(str(raw["export_eligibility"]))
        status = GateStatus(str(row["result_status"]))
        reason_codes = tuple(str(item) for item in json.loads(str(row["reason_codes"])))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise Phase1RunnerSafetyError("persisted M6 event raw_result_ref is missing required decision fields") from exc
    if raw.get("schema_version") not in (None, PHASE1_SCHEMA_VERSION):
        raise Phase1RunnerSafetyError("persisted M6 event raw_result_ref schema_version does not match Phase 1")
    return PromotionDecision(
        batch_id=str(row["batch_id"]),
        entity_id=str(row["target_entity_id"]),
        promotion_tier=promotion_tier,
        status=status,
        export_eligibility=export_eligibility,
        reason_codes=reason_codes,
        eligible_fact_ids=tuple(str(item) for item in raw.get("eligible_fact_ids", [])),
        source_observation_ids=tuple(str(item) for item in raw.get("source_observation_ids", [])),
        verification_event_ids=tuple(str(item) for item in raw.get("verification_event_ids", [])),
        suppression_event_id=raw.get("suppression_event_id"),
        enrichment_event_id=raw.get("enrichment_event_id"),
        identity_edge_ids=tuple(str(item) for item in raw.get("identity_edge_ids", [])),
        score=row.get("score"),
        input_hash=str(row["input_hash"]),
        send_authorized=bool(raw.get("send_authorized", False)),
    )


def _without_m6_event_ids(conn: sqlite3.Connection, event_ids: Sequence[str]) -> tuple[str, ...]:
    m6_event_ids = {
        str(row[0])
        for row in conn.execute(
            "SELECT verification_event_id FROM verification_events WHERE gate_name = ?",
            (PROMOTION_GATE_NAME,),
        )
    }
    return tuple(event_id for event_id in event_ids if event_id not in m6_event_ids)


def _decision_integrity_tuple(conn: sqlite3.Connection, decision: PromotionDecision) -> tuple[Any, ...]:
    return (
        decision.batch_id,
        decision.entity_id,
        decision.promotion_tier,
        decision.status,
        decision.export_eligibility,
        decision.reason_codes,
        decision.eligible_fact_ids,
        decision.source_observation_ids,
        _without_m6_event_ids(conn, decision.verification_event_ids),
        decision.suppression_event_id,
        decision.enrichment_event_id,
        decision.identity_edge_ids,
        decision.send_authorized,
    )


def _assert_existing_m6_decision_matches_fresh_evaluation(
    conn: sqlite3.Connection,
    existing: PromotionDecision,
    fresh: PromotionDecision,
) -> None:
    if _decision_integrity_tuple(conn, existing) != _decision_integrity_tuple(conn, fresh):
        raise Phase1RunnerSafetyError("persisted M6 decision does not match fresh evaluation")


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in REQUIRED_TABLES:
        query = "SELECT COUNT(*) FROM " + _quote_required_table(table)
        counts[table] = int(conn.execute(query).fetchone()[0])
    return counts


def _write_summary_to_dict(summary: WriteSummary) -> dict[str, int | str]:
    return {
        "batch_id": summary.batch_id,
        "connector_run_id": summary.connector_run_id,
        "batches_inserted": summary.batches_inserted,
        "sources_inserted": summary.sources_inserted,
        "cost_events_inserted": summary.cost_events_inserted,
        "observations_inserted": summary.observations_inserted,
        "batches_duplicate": summary.batches_duplicate,
        "sources_duplicate": summary.sources_duplicate,
        "cost_events_duplicate": summary.cost_events_duplicate,
        "observations_duplicate": summary.observations_duplicate,
    }


def _assembly_summary_to_dict(summary: AssemblySummary) -> dict[str, Any]:
    return {
        "batch_id": summary.batch_id,
        "entities_inserted": summary.entities_inserted,
        "facts_inserted": summary.facts_inserted,
        "identity_edges_inserted": summary.identity_edges_inserted,
        "records_skipped": summary.records_skipped,
        "records_review_required": summary.records_review_required,
        "skip_details": [
            {"observation_id": item.observation_id, "reason": item.reason} for item in summary.skip_details
        ],
    }


def _prepare_fixture_events(conn: sqlite3.Connection) -> None:
    for row in _entity_rows(conn):
        entity_id = str(row["entity_id"])
        label = str(row["canonical_label"])
        contact_fact_id = _fact_id(conn, entity_id, "contact_email")
        if label in {"Fixture Build Group", "Suppressed TI Contractors"}:
            _insert_contact_passes(conn, entity_id)
        if label == "Fixture Build Group":
            _insert_suppression_event(
                conn,
                entity_id=entity_id,
                fact_id=contact_fact_id,
                status=SuppressionStatus.CLEAR,
            )
            _insert_enrichment_event(conn, entity_id=entity_id)
        elif label == "Suppressed TI Contractors":
            _insert_suppression_event(
                conn,
                entity_id=entity_id,
                fact_id=contact_fact_id,
                status=SuppressionStatus.SUPPRESSED_EMAIL,
            )
            _insert_enrichment_event(conn, entity_id=entity_id)
    conn.commit()


def _run_m6_m7(conn: sqlite3.Connection) -> tuple[LeadPipelineResult, ...]:
    leads: list[LeadPipelineResult] = []
    for entity in _entity_rows(conn):
        entity_id = str(entity["entity_id"])
        existing_decision = _existing_m6_decision(conn, entity_id=entity_id)
        if existing_decision is None:
            decision = promote_entity(conn, batch_id=M8_BATCH_ID, entity_id=entity_id)
        else:
            fresh_decision = evaluate_entity_promotion(conn, batch_id=M8_BATCH_ID, entity_id=entity_id)
            _assert_existing_m6_decision_matches_fresh_evaluation(conn, existing_decision, fresh_decision)
            decision = existing_decision
        m6_event_id = _m6_event_id(conn, decision)
        export_event_id: str | None = None
        verification_event_ids: tuple[str, ...] = tuple(sorted((*decision.verification_event_ids, m6_event_id)))
        if decision.export_eligibility == ExportEligibility.INTERNAL_REVIEW_ONLY:
            contract = build_internal_export_contract(conn, decision, campaign_id="phase1_m8_fixture_campaign")
            write_internal_export_event(conn, contract)
            export_event_id = contract.export_event_id
            verification_event_ids = contract.included_verification_event_ids
        leads.append(
            LeadPipelineResult(
                entity_id=entity_id,
                canonical_label=str(entity["canonical_label"]),
                promotion_tier=decision.promotion_tier.value,
                gate_status=decision.status.value,
                export_eligibility=decision.export_eligibility.value,
                reason_codes=decision.reason_codes,
                eligible_fact_ids=decision.eligible_fact_ids,
                source_observation_ids=decision.source_observation_ids,
                verification_event_ids=verification_event_ids,
                suppression_event_id=decision.suppression_event_id,
                enrichment_event_id=decision.enrichment_event_id,
                export_event_id=export_event_id,
                send_authorized=decision.send_authorized,
            )
        )
    return tuple(leads)


def run_phase1_fixture_pipeline(
    *,
    fixture_id: str = M8_FIXTURE_ID,
    conn: sqlite3.Connection | None = None,
) -> Phase1PipelineRunResult:
    """Run the deterministic M8 fixture-only pipeline.

    ``fixture_id='golden'`` is the only approved M8 fixture. If ``conn`` is
    provided, it must already contain the M1 SQLite fixture schema; this allows
    tests to prove idempotent replay on the same local database. If ``conn`` is
    omitted, an in-memory SQLite connection is created and initialized.
    """

    if fixture_id != M8_FIXTURE_ID:
        raise Phase1RunnerSafetyError(f"unknown fixture_id {fixture_id!r}; only {M8_FIXTURE_ID!r} is approved")

    if conn is None:
        conn = sqlite3.connect(":memory:")
        initialize_sqlite_schema(conn)
    elif not _has_required_tables(conn):
        raise Phase1RunnerSafetyError("provided SQLite connection is missing the Phase 1 fixture schema")

    documents = _fixture_documents()
    connector_result = run_fixture_connector(
        "contractor_first_party_website",
        documents=documents,
        batch_id=M8_BATCH_ID,
        mode=FetchMode.FIXTURE_ONLY,
    )
    if connector_result.network_used or connector_result.send_authorized:
        raise Phase1RunnerSafetyError("fixture connector violated no-network/no-send contract")
    write_summary = write_connector_run_result(conn, connector_result, batch=_batch_payload())
    observation_ids = [str(row["observation_id"]) for row in connector_result.observations]
    assembly_summary = assemble_entities_and_facts(
        conn,
        batch_id=M8_BATCH_ID,
        records=_assembly_records(observation_ids),
    )
    _prepare_fixture_events(conn)
    leads = _run_m6_m7(conn)
    table_counts = _table_counts(conn)
    export_event_ids = tuple(
        str(row[0]) for row in conn.execute("SELECT export_event_id FROM export_events ORDER BY export_event_id")
    )
    if any(lead.send_authorized for lead in leads):
        raise Phase1RunnerSafetyError("M8 produced a send_authorized lead, which is forbidden")
    send_sum = int(conn.execute("SELECT COALESCE(SUM(send_authorized), 0) FROM export_events").fetchone()[0])
    if send_sum != 0:
        raise Phase1RunnerSafetyError("M8 produced a send_authorized export row, which is forbidden")

    summary = Phase1PipelineSummary(
        runner_version=PHASE1_M8_RUNNER_VERSION,
        fixture_id=fixture_id,
        batch_id=M8_BATCH_ID,
        safety={
            "fixture_only": True,
            "network_used": False,
            "paid_api_used": False,
            "outreach_attempted": False,
            "send_authorized": False,
        },
        connector_run_ids=(connector_result.connector_run_id,),
        write_summary=_write_summary_to_dict(write_summary),
        assembly_summary=_assembly_summary_to_dict(assembly_summary),
        table_counts=table_counts,
        leads=leads,
        internal_review_export_event_ids=export_event_ids,
    )
    return Phase1PipelineRunResult(summary=summary, conn=conn)


def _has_required_tables(conn: sqlite3.Connection) -> bool:
    found = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    return set(REQUIRED_TABLES).issubset(found)


def summary_to_json(summary: Phase1PipelineSummary | Phase1PipelineRunResult) -> str:
    """Serialize an M8 summary deterministically for CLI/internal review."""

    payload = summary.to_dict()
    return json.dumps(payload, sort_keys=True, indent=2) + "\n"
