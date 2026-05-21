"""Deterministic schema metadata and SQLite fixture DDL for Phase 1 M1.

The production architecture remains Postgres-first, but Milestone 1 deliberately
uses stdlib-only metadata plus SQLite-compatible fixture DDL so tests can prove
lineage, append-only representation, and Phase 1 safety without a database
server, network, connector, paid API, or outreach system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .contracts import (
    CostStage,
    EntityKind,
    ExportEligibility,
    FactField,
    GateStatus,
    PageType,
    PromotionTier,
    RobotsTermsPrefetchStatus,
    SourceClass,
    SuppressionStatus,
)

PHASE1_SCHEMA_VERSION = "lead_pipeline_phase1_m1_v1"

REQUIRED_TABLES = (
    "batches",
    "sources",
    "source_observations",
    "entities",
    "facts",
    "identity_edges",
    "verification_events",
    "enrichment_events",
    "suppression_events",
    "export_events",
    "cost_events",
    "human_review_events",
)

APPEND_ONLY_TABLES = frozenset(
    {
        "source_observations",
        "facts",
        "identity_edges",
        "verification_events",
        "enrichment_events",
        "suppression_events",
        "export_events",
        "cost_events",
        "human_review_events",
    }
)

PHASE1_ALLOWED_PROMOTION_VALUES = tuple(
    tier.value
    for tier in (
        PromotionTier.RAW_DISCOVERY,
        PromotionTier.IDENTITY_CANDIDATE,
        PromotionTier.ICP_CANDIDATE,
        PromotionTier.CONTACT_CANDIDATE,
        PromotionTier.VERIFIED_CONTACT,
        PromotionTier.QUALIFIED_LEAD_REVIEW_REQUIRED,
        PromotionTier.OUTREACH_READY_INTERNAL_ONLY,
    )
)


@dataclass(frozen=True)
class ColumnContract:
    """Column metadata used by docs, schema tests, and fixture DDL."""

    name: str
    data_type: str = "TEXT"
    required: bool = False
    references: tuple[str, str] | None = None
    default: str | int | float | bool | None = None
    enum_values: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class TableContract:
    """Table-level schema contract."""

    table_name: str
    primary_key: str
    columns: Mapping[str, ColumnContract]
    append_only: bool = False
    lineage_columns: tuple[str, ...] = ()
    schema_version: str = PHASE1_SCHEMA_VERSION
    purpose: str = ""


def _c(
    name: str,
    data_type: str = "TEXT",
    *,
    required: bool = False,
    references: tuple[str, str] | None = None,
    default: str | int | float | bool | None = None,
    enum_values: tuple[str, ...] = (),
    description: str = "",
) -> ColumnContract:
    return ColumnContract(
        name=name,
        data_type=data_type,
        required=required,
        references=references,
        default=default,
        enum_values=enum_values,
        description=description,
    )


def _table(
    table_name: str,
    primary_key: str,
    columns: list[ColumnContract],
    *,
    append_only: bool = False,
    lineage_columns: tuple[str, ...] = (),
    purpose: str = "",
) -> TableContract:
    by_name = {column.name: column for column in columns}
    if primary_key not in by_name:
        raise ValueError(f"{table_name} missing primary key column {primary_key}")
    if "schema_version" not in by_name:
        raise ValueError(f"{table_name} missing schema_version column")
    return TableContract(
        table_name=table_name,
        primary_key=primary_key,
        columns=MappingProxyType(by_name),
        append_only=append_only,
        lineage_columns=lineage_columns,
        purpose=purpose,
    )


_SOURCE_CLASS_VALUES = tuple(item.value for item in SourceClass)
_ENTITY_KIND_VALUES = tuple(item.value for item in EntityKind)
_FACT_FIELD_VALUES = tuple(item.value for item in FactField)
_GATE_STATUS_VALUES = tuple(item.value for item in GateStatus)
_PAGE_TYPE_VALUES = tuple(item.value for item in PageType)
_PREFETCH_STATUS_VALUES = tuple(item.value for item in RobotsTermsPrefetchStatus)
_SUPPRESSION_VALUES = tuple(item.value for item in SuppressionStatus)
_EXPORT_VALUES = tuple(item.value for item in ExportEligibility)
_COST_STAGE_VALUES = tuple(item.value for item in CostStage)

_SCHEMA_CONTRACTS = MappingProxyType(
    {
        "batches": _table(
            "batches",
            "batch_id",
            [
                _c("batch_id", required=True),
                _c("approved_scope_ref", required=True),
                _c("adapter_id", required=True, default="permitassist"),
                _c("started_at_utc", required=True),
                _c("completed_at_utc"),
                _c("status", required=True, default="open"),
                _c("source_mix_json"),
                _c("notes"),
                _c("schema_version", required=True, default=PHASE1_SCHEMA_VERSION),
            ],
            purpose="Approved fixture/review unit for one bounded no-network batch.",
        ),
        "sources": _table(
            "sources",
            "source_id",
            [
                _c("source_id", required=True),
                _c("source_class", required=True, enum_values=_SOURCE_CLASS_VALUES),
                _c("source_name", required=True),
                _c("base_url_or_path", required=True),
                _c("official_or_first_party_flag", "INTEGER", required=True, default=False),
                _c("terms_notes"),
                _c("robots_notes"),
                _c(
                    "terms_prefetch_status",
                    enum_values=_PREFETCH_STATUS_VALUES,
                    default=RobotsTermsPrefetchStatus.ALLOWED_FIXTURE_ONLY.value,
                ),
                _c(
                    "robots_prefetch_status",
                    enum_values=_PREFETCH_STATUS_VALUES,
                    default=RobotsTermsPrefetchStatus.ALLOWED_FIXTURE_ONLY.value,
                ),
                _c("requires_login", "INTEGER", required=True, default=False),
                _c("paid_flag", "INTEGER", required=True, default=False),
                _c("allowed_phase", required=True, default="phase1_fixture_only"),
                _c("rate_limit_policy_id"),
                _c("budget_policy_id"),
                _c("schema_version", required=True, default=PHASE1_SCHEMA_VERSION),
            ],
            purpose="Source registry and policy object; paid/login sources are represented but blocked by default.",
        ),
        "source_observations": _table(
            "source_observations",
            "observation_id",
            [
                _c("observation_id", required=True),
                _c("source_id", required=True, references=("sources", "source_id")),
                _c("batch_id", required=True, references=("batches", "batch_id")),
                _c("connector_run_id"),
                _c("observed_at_utc", required=True),
                _c("url_or_path", required=True),
                _c("http_status", "INTEGER"),
                _c("content_type"),
                _c("payload_hash_sha256", required=True),
                _c("snapshot_ref"),
                _c("raw_payload_ref"),
                _c("raw_result_ref"),
                _c("snippet_or_excerpt", required=True),
                _c("extractor_version"),
                _c(
                    "page_type",
                    enum_values=_PAGE_TYPE_VALUES,
                    default=PageType.BLOCKED_UNKNOWN.value,
                ),
                _c("robots_or_terms_classification"),
                _c(
                    "terms_prefetch_status",
                    enum_values=_PREFETCH_STATUS_VALUES,
                    default=RobotsTermsPrefetchStatus.ALLOWED_FIXTURE_ONLY.value,
                ),
                _c(
                    "robots_prefetch_status",
                    enum_values=_PREFETCH_STATUS_VALUES,
                    default=RobotsTermsPrefetchStatus.ALLOWED_FIXTURE_ONLY.value,
                ),
                _c("blocked_or_captcha_flag", "INTEGER", required=True, default=False),
                _c("cost_event_id", references=("cost_events", "cost_event_id")),
                _c("schema_version", required=True, default=PHASE1_SCHEMA_VERSION),
            ],
            append_only=True,
            lineage_columns=("source_id", "batch_id", "payload_hash_sha256", "snippet_or_excerpt"),
            purpose="Immutable fetched/imported fixture evidence unit.",
        ),
        "entities": _table(
            "entities",
            "entity_id",
            [
                _c("entity_id", required=True),
                _c("entity_type", required=True, enum_values=_ENTITY_KIND_VALUES),
                _c("canonical_label", required=True),
                _c("normalized_key", required=True),
                _c("status", required=True, default=PromotionTier.RAW_DISCOVERY.value),
                _c(
                    "created_from_observation_id",
                    references=("source_observations", "observation_id"),
                ),
                _c("created_at_utc"),
                _c("schema_version", required=True, default=PHASE1_SCHEMA_VERSION),
            ],
            lineage_columns=("created_from_observation_id",),
            purpose="Canonical candidate business/person/contact container.",
        ),
        "facts": _table(
            "facts",
            "fact_id",
            [
                _c("fact_id", required=True),
                _c("entity_id", required=True, references=("entities", "entity_id")),
                _c("fact_type", required=True, enum_values=_FACT_FIELD_VALUES),
                _c("fact_value", required=True),
                _c("normalized_value"),
                _c("confidence", "REAL"),
                _c("promotion_status", required=True, enum_values=PHASE1_ALLOWED_PROMOTION_VALUES),
                _c(
                    "source_observation_id",
                    required=True,
                    references=("source_observations", "observation_id"),
                ),
                _c("field_relevant_snippet", required=True),
                _c(
                    "promoted_by_gate_event_id",
                    references=("verification_events", "verification_event_id"),
                ),
                _c("supersedes_fact_id", references=("facts", "fact_id")),
                _c("valid_from_observed_at_utc"),
                _c("status_reason"),
                _c("schema_version", required=True, default=PHASE1_SCHEMA_VERSION),
            ],
            append_only=True,
            lineage_columns=(
                "entity_id",
                "source_observation_id",
                "field_relevant_snippet",
                "promoted_by_gate_event_id",
                "supersedes_fact_id",
            ),
            purpose="Field-level assertion with source and gate lineage; corrections supersede, never overwrite.",
        ),
        "identity_edges": _table(
            "identity_edges",
            "edge_id",
            [
                _c("edge_id", required=True),
                _c("from_entity_id", required=True, references=("entities", "entity_id")),
                _c("to_entity_id", required=True, references=("entities", "entity_id")),
                _c("edge_type", required=True),
                _c("match_key", required=True),
                _c("match_confidence", "REAL"),
                _c("evidence_fact_ids", required=True),
                _c("review_status", required=True, default=GateStatus.REVIEW_REQUIRED.value),
                _c("created_by", required=True, default="phase1_fixture_contract"),
                _c("superseded_by_edge_id", references=("identity_edges", "edge_id")),
                _c("schema_version", required=True, default=PHASE1_SCHEMA_VERSION),
            ],
            append_only=True,
            lineage_columns=("from_entity_id", "to_entity_id", "evidence_fact_ids"),
            purpose="Non-destructive identity resolution and duplicate handling.",
        ),
        "verification_events": _table(
            "verification_events",
            "verification_event_id",
            [
                _c("verification_event_id", required=True),
                _c("batch_id", required=True, references=("batches", "batch_id")),
                _c("target_entity_id", references=("entities", "entity_id")),
                _c("target_fact_id", references=("facts", "fact_id")),
                _c("gate_name", required=True),
                _c("gate_version", required=True),
                _c("input_hash", required=True),
                _c("result_status", required=True, enum_values=_GATE_STATUS_VALUES),
                _c("score", "REAL"),
                _c("reason_codes"),
                _c("network_used_flag", "INTEGER", required=True, default=False),
                _c("cached_result_flag", "INTEGER", required=True, default=False),
                _c("observed_at_utc", required=True),
                _c("expires_at_utc"),
                _c("raw_result_ref"),
                _c("cost_event_id", references=("cost_events", "cost_event_id")),
                _c("schema_version", required=True, default=PHASE1_SCHEMA_VERSION),
            ],
            append_only=True,
            lineage_columns=("target_entity_id", "target_fact_id", "input_hash", "result_status"),
            purpose="Deterministic fixture gate and future replayable verifier result ledger.",
        ),
        "enrichment_events": _table(
            "enrichment_events",
            "enrichment_event_id",
            [
                _c("enrichment_event_id", required=True),
                _c("batch_id", required=True, references=("batches", "batch_id")),
                _c("entity_id", required=True, references=("entities", "entity_id")),
                _c("input_fact_ids", required=True),
                _c("input_observation_ids", required=True),
                _c("model_or_rule_version", required=True),
                _c("prompt_template_hash"),
                _c("output_json", required=True),
                _c("quality_score", "REAL"),
                _c("unsupported_claim_count", "INTEGER", required=True, default=0),
                _c("validator_status", required=True, enum_values=_GATE_STATUS_VALUES),
                _c("validator_reason_codes"),
                _c("cost_event_id", references=("cost_events", "cost_event_id")),
                _c("schema_version", required=True, default=PHASE1_SCHEMA_VERSION),
            ],
            append_only=True,
            lineage_columns=("entity_id", "input_fact_ids", "input_observation_ids", "validator_status"),
            purpose="Evidence-backed classification/enrichment event; never primary evidence.",
        ),
        "suppression_events": _table(
            "suppression_events",
            "suppression_event_id",
            [
                _c("suppression_event_id", required=True),
                _c("batch_id", required=True, references=("batches", "batch_id")),
                _c("target_entity_id", references=("entities", "entity_id")),
                _c("target_fact_id", references=("facts", "fact_id")),
                _c("suppression_source_ref", required=True),
                _c("suppression_snapshot_hash", required=True),
                _c("status", required=True, enum_values=_SUPPRESSION_VALUES),
                _c("reason"),
                _c("checked_at_utc", required=True),
                _c("campaign_id"),
                _c("expires_at_utc"),
                _c("schema_version", required=True, default=PHASE1_SCHEMA_VERSION),
            ],
            append_only=True,
            lineage_columns=("target_entity_id", "target_fact_id", "suppression_snapshot_hash", "status"),
            purpose="Fail-closed suppression snapshot ledger.",
        ),
        "export_events": _table(
            "export_events",
            "export_event_id",
            [
                _c("export_event_id", required=True),
                _c("batch_id", required=True, references=("batches", "batch_id")),
                _c("campaign_id"),
                _c("export_target", required=True),
                _c("export_schema_version", required=True),
                _c("lead_entity_id", required=True, references=("entities", "entity_id")),
                _c("included_fact_ids", required=True),
                _c("included_source_observation_ids", required=True),
                _c("included_verification_event_ids", required=True),
                _c("suppression_event_id", references=("suppression_events", "suppression_event_id")),
                _c("enrichment_event_id", references=("enrichment_events", "enrichment_event_id")),
                _c("human_review_event_id", references=("human_review_events", "review_event_id")),
                _c("signed_payload_hash_sha256", required=True),
                _c("signature", required=True),
                _c("send_authorized", "INTEGER", required=True, default=False),
                _c("status", required=True, enum_values=_EXPORT_VALUES),
                _c("blocked_reason"),
                _c("created_at_utc", required=True),
                _c("schema_version", required=True, default=PHASE1_SCHEMA_VERSION),
            ],
            append_only=True,
            lineage_columns=(
                "lead_entity_id",
                "included_fact_ids",
                "included_source_observation_ids",
                "included_verification_event_ids",
                "suppression_event_id",
                "human_review_event_id",
            ),
            purpose="Immutable internal/no-send export handoff record.",
        ),
        "cost_events": _table(
            "cost_events",
            "cost_event_id",
            [
                _c("cost_event_id", required=True),
                _c("batch_id", required=True, references=("batches", "batch_id")),
                _c("entity_id", references=("entities", "entity_id")),
                _c("connector_run_id"),
                _c("stage", required=True, enum_values=_COST_STAGE_VALUES),
                _c("provider_or_tool", required=True),
                _c("units_consumed", "REAL", required=True, default=0),
                _c("cost_usd", "REAL", required=True, default=0),
                _c("allocated_flag", "INTEGER", required=True, default=True),
                _c("waste_reason"),
                _c("created_at_utc", required=True),
                _c("schema_version", required=True, default=PHASE1_SCHEMA_VERSION),
            ],
            append_only=True,
            lineage_columns=("batch_id", "entity_id", "stage", "provider_or_tool"),
            purpose="Per-batch/per-row/per-stage cost and waste attribution.",
        ),
        "human_review_events": _table(
            "human_review_events",
            "review_event_id",
            [
                _c("review_event_id", required=True),
                _c("entity_id", required=True, references=("entities", "entity_id")),
                _c("reviewer", required=True),
                _c("reviewed_at_utc", required=True),
                _c("rubric_version", required=True),
                _c("decision", required=True, enum_values=_GATE_STATUS_VALUES),
                _c("reason_codes"),
                _c("notes_ref"),
                _c("input_fact_ids", required=True),
                _c("schema_version", required=True, default=PHASE1_SCHEMA_VERSION),
            ],
            append_only=True,
            lineage_columns=("entity_id", "decision", "input_fact_ids"),
            purpose="Explicit review decision for edge cases and internal readiness.",
        ),
    }
)


def get_table_contracts() -> Mapping[str, TableContract]:
    """Return all M1 table contracts in required deterministic order."""

    return MappingProxyType({table_name: _SCHEMA_CONTRACTS[table_name] for table_name in REQUIRED_TABLES})


def get_table_contract(table_name: str) -> TableContract:
    """Return one table contract by exact name."""

    return _SCHEMA_CONTRACTS[table_name]


def _sql_literal(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + value.replace("'", "''") + "'"


def _column_sql(column: ColumnContract, primary_key: str) -> str:
    parts = [column.name, column.data_type]
    if column.name == primary_key:
        parts.append("PRIMARY KEY")
    if column.required:
        parts.append("NOT NULL")
    if column.default is not None:
        parts.append("DEFAULT")
        parts.append(_sql_literal(column.default))
    if column.enum_values:
        allowed = ", ".join(_sql_literal(value) for value in column.enum_values)
        parts.append(f"CHECK ({column.name} IN ({allowed}))")
    if column.name == "send_authorized":
        parts.append("CHECK (send_authorized = 0)")
    if column.references:
        ref_table, ref_column = column.references
        parts.append(f"REFERENCES {ref_table}({ref_column})")
    return " ".join(parts)


def _create_table_sql(table: TableContract) -> str:
    columns = [
        "  " + _column_sql(column, table.primary_key)
        for column in table.columns.values()
    ]
    return f"CREATE TABLE {table.table_name} (\n" + ",\n".join(columns) + "\n);"


def _append_only_trigger_sql(table_name: str) -> str:
    return f"""
CREATE TRIGGER {table_name}_append_only_no_update
BEFORE UPDATE ON {table_name}
BEGIN
  SELECT RAISE(ABORT, '{table_name} is append-only; write a superseding row instead');
END;
CREATE TRIGGER {table_name}_append_only_no_delete
BEFORE DELETE ON {table_name}
BEGIN
  SELECT RAISE(ABORT, '{table_name} is append-only; write a tombstone/redaction event instead');
END;
""".strip()


def create_sqlite_schema() -> str:
    """Build SQLite fixture DDL for contract tests.

    SQLite is used only as a deterministic local validator. It is not a
    production database choice and does not perform any external I/O.
    """

    statements = ["PRAGMA foreign_keys = ON;"]
    for table_name in REQUIRED_TABLES:
        statements.append(_create_table_sql(_SCHEMA_CONTRACTS[table_name]))
    for table_name in REQUIRED_TABLES:
        if _SCHEMA_CONTRACTS[table_name].append_only:
            statements.append(_append_only_trigger_sql(table_name))
    return "\n\n".join(statements) + "\n"
