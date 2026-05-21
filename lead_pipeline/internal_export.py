"""Deterministic no-send internal export contract for Phase 1 M7.

M7 is the first Phase 1 module allowed to write ``export_events``. It does not
send, schedule, sync, webhook, email, scrape, browse, or call any network path.
It only converts a persisted M6 ``outreach_ready_internal_only`` decision into
an append-only SQLite export ledger row for Boban/Titi internal review.

The exported row stores lineage IDs plus a deterministic hash/signature over the
canonical payload. It intentionally does not store a raw outreach body, message,
or destination. Future live outreach remains reserved for a separately approved
phase.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .contracts import ExportEligibility, GateStatus, PromotionTier, assert_phase1_promotion_allowed
from .promotion import PROMOTION_GATE_NAME, PromotionDecision, promote_entity
from .schema import PHASE1_SCHEMA_VERSION, get_table_contract

INTERNAL_EXPORT_SCHEMA_VERSION = "lead_pipeline_phase1_m7_internal_export_v1"
INTERNAL_EXPORT_TARGET = "internal_review_queue"
# Fixed timestamp is intentional for fixture-only determinism/idempotency.
INTERNAL_EXPORT_CREATED_AT_UTC = "2026-05-20T00:00:00Z"
INTERNAL_EXPORT_BLOCKED_REASON = "no_send_internal_review_only"

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*['\"]?[A-Za-z0-9_.\-]{8,}"),
    re.compile(r"(?i)(?:^|[_\-\s])(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*['\"]?[A-Za-z0-9_.\-]{8,}"),
    re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{8,}\b"),
    # AWS key pattern is split so repo-level literal scans do not flag the detector itself.
    re.compile(r"\b" "AK" "IA" r"[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)\bxox[baprs]-[A-Za-z0-9-]{10,}"),
)


class InternalExportSafetyError(RuntimeError):
    """Raised when M7 cannot safely produce a no-send internal export."""


@dataclass(frozen=True)
class InternalExportContract:
    """Machine-readable M7 export contract for one internal-review lead."""

    export_event_id: str
    batch_id: str
    campaign_id: str | None
    export_target: str
    export_schema_version: str
    lead_entity_id: str
    included_fact_ids: tuple[str, ...]
    included_source_observation_ids: tuple[str, ...]
    included_verification_event_ids: tuple[str, ...]
    suppression_event_id: str
    enrichment_event_id: str
    human_review_event_id: str | None
    signed_payload_hash_sha256: str
    signature: str
    send_authorized: bool
    status: ExportEligibility
    blocked_reason: str
    created_at_utc: str
    schema_version: str

    def to_export_event_payload(self) -> dict[str, Any]:
        """Return an ``export_events``-compatible payload."""

        return {
            "export_event_id": self.export_event_id,
            "batch_id": self.batch_id,
            "campaign_id": self.campaign_id,
            "export_target": self.export_target,
            "export_schema_version": self.export_schema_version,
            "lead_entity_id": self.lead_entity_id,
            "included_fact_ids": json.dumps(list(self.included_fact_ids), sort_keys=True),
            "included_source_observation_ids": json.dumps(
                list(self.included_source_observation_ids), sort_keys=True
            ),
            "included_verification_event_ids": json.dumps(
                list(self.included_verification_event_ids), sort_keys=True
            ),
            "suppression_event_id": self.suppression_event_id,
            "enrichment_event_id": self.enrichment_event_id,
            "human_review_event_id": self.human_review_event_id,
            "signed_payload_hash_sha256": self.signed_payload_hash_sha256,
            "signature": self.signature,
            "send_authorized": 1 if self.send_authorized else 0,
            "status": self.status.value,
            "blocked_reason": self.blocked_reason,
            "created_at_utc": self.created_at_utc,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class InternalExportWriteResult:
    """Result of evaluating and writing one internal export row."""

    contract: InternalExportContract
    inserted: int


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_load_dict(value: Any) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    loaded = json.loads(str(value))
    if isinstance(loaded, dict):
        return loaded
    raise InternalExportSafetyError("persisted M6 promotion event raw_result_ref is not an object")


def _json_load_list(value: Any, *, context: str) -> list[Any]:
    if value is None or value == "":
        return []
    loaded = json.loads(str(value))
    if isinstance(loaded, list):
        return loaded
    raise InternalExportSafetyError(f"{context} is not a list")


def _filter_columns(payload: Mapping[str, Any], allowed: Iterable[str]) -> dict[str, Any]:
    allowed_set = set(allowed)
    return {key: value for key, value in payload.items() if key in allowed_set}


def _rows(conn: sqlite3.Connection, query: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    cursor = conn.execute(query, tuple(params))
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _insert_or_ignore(conn: sqlite3.Connection, table: str, payload: Mapping[str, Any]) -> int:
    contract = get_table_contract(table)
    filtered = _filter_columns(payload, contract.columns.keys())
    columns = list(filtered.keys())
    placeholders = ",".join("?" for _ in columns)
    col_list = ",".join(columns)
    cursor = conn.execute(
        f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})",
        [filtered[col] for col in columns],
    )
    return 1 if cursor.rowcount and cursor.rowcount > 0 else 0


def _contains_secret_like_text(value: Any) -> bool:
    if value is None:
        return False
    text = str(value)
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def _assert_no_secret_like_values(rows: Sequence[Mapping[str, Any]], *, context: str) -> None:
    for row in rows:
        for value in row.values():
            if _contains_secret_like_text(value):
                raise InternalExportSafetyError(f"secret-like value found in {context}; refusing internal export")


def _assert_no_secret_like_freeform(*, campaign_id: str | None, human_review_event_id: str | None) -> None:
    for label, value in (
        ("campaign_id", campaign_id),
        ("human_review_event_id", human_review_event_id),
    ):
        if _contains_secret_like_text(value):
            raise InternalExportSafetyError(f"secret-like value found in {label}; refusing internal export")


_ALLOWED_OPTIONAL_EVENTS = {
    ("suppression_events", "suppression_event_id"),
    ("enrichment_events", "enrichment_event_id"),
    ("human_review_events", "review_event_id"),
}


def _load_entity(conn: sqlite3.Connection, entity_id: str) -> Mapping[str, Any]:
    rows = _rows(
        conn,
        "SELECT entity_id, entity_type, canonical_label, normalized_key, status, schema_version "
        "FROM entities WHERE entity_id = ?",
        (entity_id,),
    )
    if not rows:
        raise InternalExportSafetyError(f"unknown entity_id {entity_id!r}")
    entity = rows[0]
    if entity.get("schema_version") != PHASE1_SCHEMA_VERSION:
        raise InternalExportSafetyError("entity schema_version mismatch")
    return entity


def _load_facts(conn: sqlite3.Connection, fact_ids: Sequence[str], entity_id: str) -> list[dict[str, Any]]:
    if not fact_ids:
        raise InternalExportSafetyError("M7 requires at least one included fact")
    placeholders = ",".join("?" for _ in fact_ids)
    rows = _rows(
        conn,
        "SELECT fact_id, entity_id, fact_type, fact_value, normalized_value, source_observation_id, "
        "field_relevant_snippet, schema_version FROM facts "
        f"WHERE fact_id IN ({placeholders}) ORDER BY fact_id",
        tuple(fact_ids),
    )
    if len(rows) != len(set(fact_ids)):
        raise InternalExportSafetyError("included fact IDs are not all present")
    if any(row.get("entity_id") != entity_id for row in rows):
        raise InternalExportSafetyError("included fact does not belong to lead entity")
    if any(row.get("schema_version") != PHASE1_SCHEMA_VERSION for row in rows):
        raise InternalExportSafetyError("fact schema_version mismatch")
    if any(not row.get("source_observation_id") or not row.get("field_relevant_snippet") for row in rows):
        raise InternalExportSafetyError("included fact missing source/citation lineage")
    return rows


def _load_source_observations(conn: sqlite3.Connection, observation_ids: Sequence[str], batch_id: str) -> list[dict[str, Any]]:
    if not observation_ids:
        raise InternalExportSafetyError("M7 requires at least one source observation")
    placeholders = ",".join("?" for _ in observation_ids)
    rows = _rows(
        conn,
        "SELECT observation_id, source_id, batch_id, url_or_path, payload_hash_sha256, "
        "snippet_or_excerpt, schema_version FROM source_observations "
        f"WHERE observation_id IN ({placeholders}) ORDER BY observation_id",
        tuple(observation_ids),
    )
    if len(rows) != len(set(observation_ids)):
        raise InternalExportSafetyError("included source observations are not all present")
    if any(row.get("batch_id") != batch_id for row in rows):
        raise InternalExportSafetyError("source observation batch_id mismatch")
    if any(row.get("schema_version") != PHASE1_SCHEMA_VERSION for row in rows):
        raise InternalExportSafetyError("source observation schema_version mismatch")
    return rows


def _load_optional_event(
    conn: sqlite3.Connection,
    *,
    table: str,
    id_column: str,
    event_id: str | None,
) -> Mapping[str, Any] | None:
    if not event_id:
        return None
    if (table, id_column) not in _ALLOWED_OPTIONAL_EVENTS:
        raise InternalExportSafetyError("unsupported optional event lookup")
    rows = _rows(conn, f"SELECT * FROM {table} WHERE {id_column} = ?", (event_id,))
    if len(rows) != 1:
        raise InternalExportSafetyError(f"referenced {table} row is missing")
    row = rows[0]
    if row.get("schema_version") != PHASE1_SCHEMA_VERSION:
        raise InternalExportSafetyError(f"{table} schema_version mismatch")
    return row


def _load_m6_event(conn: sqlite3.Connection, decision: PromotionDecision) -> Mapping[str, Any]:
    rows = _rows(
        conn,
        "SELECT verification_event_id, batch_id, target_entity_id, gate_name, gate_version, input_hash, "
        "result_status, reason_codes, network_used_flag, raw_result_ref, schema_version "
        "FROM verification_events WHERE gate_name = ? AND target_entity_id = ? AND input_hash = ? "
        "ORDER BY verification_event_id",
        (PROMOTION_GATE_NAME, decision.entity_id, decision.input_hash),
    )
    if len(rows) != 1:
        raise InternalExportSafetyError("M7 requires exactly one persisted M6 promotion event before export")
    event = rows[0]
    if event.get("batch_id") != decision.batch_id:
        raise InternalExportSafetyError("persisted M6 promotion event batch mismatch")
    if event.get("result_status") != GateStatus.PASS_.value:
        raise InternalExportSafetyError("persisted M6 promotion event did not pass")
    if int(event.get("network_used_flag") or 0) != 0:
        raise InternalExportSafetyError("persisted M6 promotion event used network; refusing Phase 1 export")
    if event.get("schema_version") != PHASE1_SCHEMA_VERSION:
        raise InternalExportSafetyError("persisted M6 promotion event schema_version mismatch")
    raw = _json_load_dict(event.get("raw_result_ref"))
    expected = {
        "promotion_tier": decision.promotion_tier.value,
        "export_eligibility": decision.export_eligibility.value,
        "eligible_fact_ids": list(decision.eligible_fact_ids),
        "source_observation_ids": list(decision.source_observation_ids),
        "verification_event_ids": list(decision.verification_event_ids),
        "suppression_event_id": decision.suppression_event_id,
        "enrichment_event_id": decision.enrichment_event_id,
        "identity_edge_ids": list(decision.identity_edge_ids),
        "send_authorized": decision.send_authorized,
    }
    if raw != expected:
        raise InternalExportSafetyError("persisted M6 promotion event does not match decision")
    return event


def _assert_decision_is_exportable(decision: PromotionDecision) -> None:
    if decision.send_authorized:
        raise InternalExportSafetyError("M7 refuses any decision with send_authorized=true")
    if decision.promotion_tier == PromotionTier.LIVE_OUTREACH_READY_FUTURE_PHASE:
        raise InternalExportSafetyError("future live outreach tier is reserved and cannot be exported in Phase 1")
    assert_phase1_promotion_allowed(decision.promotion_tier)
    if decision.status != GateStatus.PASS_:
        raise InternalExportSafetyError("decision is not exportable: M6 status is not pass")
    if decision.promotion_tier != PromotionTier.OUTREACH_READY_INTERNAL_ONLY:
        raise InternalExportSafetyError(
            "decision is not exportable: expected internal_review_only eligibility "
            "and outreach_ready_internal_only tier"
        )
    if decision.export_eligibility != ExportEligibility.INTERNAL_REVIEW_ONLY:
        raise InternalExportSafetyError("decision is not exportable: expected internal_review_only eligibility")
    if not decision.suppression_event_id:
        raise InternalExportSafetyError("decision is not exportable: missing clear suppression event")
    if not decision.enrichment_event_id:
        raise InternalExportSafetyError("decision is not exportable: missing cited enrichment event")


def _canonical_export_payload(
    *,
    decision: PromotionDecision,
    m6_event_id: str,
    campaign_id: str | None,
    export_target: str,
    human_review_event_id: str | None,
) -> dict[str, Any]:
    return {
        "batch_id": decision.batch_id,
        "campaign_id": campaign_id,
        "export_target": export_target,
        "export_schema_version": INTERNAL_EXPORT_SCHEMA_VERSION,
        "lead_entity_id": decision.entity_id,
        "included_fact_ids": sorted(decision.eligible_fact_ids),
        "included_source_observation_ids": sorted(decision.source_observation_ids),
        "included_verification_event_ids": sorted((*decision.verification_event_ids, m6_event_id)),
        "suppression_event_id": decision.suppression_event_id,
        "enrichment_event_id": decision.enrichment_event_id,
        "human_review_event_id": human_review_event_id,
        "status": ExportEligibility.INTERNAL_REVIEW_ONLY.value,
        "send_authorized": False,
        "blocked_reason": INTERNAL_EXPORT_BLOCKED_REASON,
        "created_at_utc": INTERNAL_EXPORT_CREATED_AT_UTC,
    }


def build_internal_export_contract(
    conn: sqlite3.Connection,
    decision: PromotionDecision,
    *,
    campaign_id: str | None = None,
    export_target: str = INTERNAL_EXPORT_TARGET,
    human_review_event_id: str | None = None,
) -> InternalExportContract:
    """Build a deterministic no-send M7 internal export contract.

    The decision must be a persisted M6 pass with
    ``outreach_ready_internal_only`` / ``internal_review_only``. The function
    has no side effects and performs no external I/O.
    """

    if export_target != INTERNAL_EXPORT_TARGET:
        raise InternalExportSafetyError("Phase 1 M7 only supports the internal_review_queue target")
    _assert_decision_is_exportable(decision)
    _assert_no_secret_like_freeform(campaign_id=campaign_id, human_review_event_id=human_review_event_id)
    _load_entity(conn, decision.entity_id)
    m6_event = _load_m6_event(conn, decision)
    fact_rows = _load_facts(conn, decision.eligible_fact_ids, decision.entity_id)
    observation_rows = _load_source_observations(conn, decision.source_observation_ids, decision.batch_id)
    suppression_row = _load_optional_event(
        conn,
        table="suppression_events",
        id_column="suppression_event_id",
        event_id=decision.suppression_event_id,
    )
    enrichment_row = _load_optional_event(
        conn,
        table="enrichment_events",
        id_column="enrichment_event_id",
        event_id=decision.enrichment_event_id,
    )
    if suppression_row is None or suppression_row.get("status") != "clear":
        raise InternalExportSafetyError("decision suppression event is not clear")
    if enrichment_row is None or enrichment_row.get("validator_status") != GateStatus.PASS_.value:
        raise InternalExportSafetyError("decision enrichment event is not validator-passed")
    if int(enrichment_row.get("unsupported_claim_count") or 0) != 0:
        raise InternalExportSafetyError("decision enrichment event contains unsupported claims")
    enrichment_fact_ids = _json_load_list(enrichment_row.get("input_fact_ids"), context="enrichment input facts")
    enrichment_observation_ids = _json_load_list(
        enrichment_row.get("input_observation_ids"), context="enrichment input observations"
    )
    if not set(decision.eligible_fact_ids).issubset(set(enrichment_fact_ids)):
        raise InternalExportSafetyError("enrichment event does not cover exported facts")
    if not set(decision.source_observation_ids).issubset(set(enrichment_observation_ids)):
        raise InternalExportSafetyError("enrichment event does not cover exported observations")
    human_review_row = None
    if human_review_event_id:
        human_review_row = _load_optional_event(
            conn,
            table="human_review_events",
            id_column="review_event_id",
            event_id=human_review_event_id,
        )
        if human_review_row is None or human_review_row.get("entity_id") != decision.entity_id:
            raise InternalExportSafetyError("human review event does not belong to lead entity")
        if human_review_row.get("decision") != GateStatus.PASS_.value:
            raise InternalExportSafetyError("human review event did not pass")
        review_fact_ids = _json_load_list(human_review_row.get("input_fact_ids"), context="human review input facts")
        if not set(decision.eligible_fact_ids).issubset(set(review_fact_ids)):
            raise InternalExportSafetyError("human review event does not cover exported facts")

    _assert_no_secret_like_values(fact_rows, context="export facts")
    _assert_no_secret_like_values(observation_rows, context="export source observations")
    _assert_no_secret_like_values([m6_event], context="M6 promotion event")
    event_rows = [row for row in (suppression_row, enrichment_row, human_review_row) if row is not None]
    _assert_no_secret_like_values(event_rows, context="decision events")

    m6_event_id = str(m6_event["verification_event_id"])
    canonical_payload = _canonical_export_payload(
        decision=decision,
        m6_event_id=m6_event_id,
        campaign_id=campaign_id,
        export_target=export_target,
        human_review_event_id=human_review_event_id,
    )
    signed_payload_hash = _stable_hash(canonical_payload)
    signature = "phase1-local-signature:" + _stable_hash(
        {
            "schema": INTERNAL_EXPORT_SCHEMA_VERSION,
            "payload_hash": signed_payload_hash,
            "send_authorized": False,
        }
    )
    export_event_id = f"exp_m7_internal_{signed_payload_hash[:24]}"

    return InternalExportContract(
        export_event_id=export_event_id,
        batch_id=decision.batch_id,
        campaign_id=campaign_id,
        export_target=export_target,
        export_schema_version=INTERNAL_EXPORT_SCHEMA_VERSION,
        lead_entity_id=decision.entity_id,
        included_fact_ids=tuple(canonical_payload["included_fact_ids"]),
        included_source_observation_ids=tuple(canonical_payload["included_source_observation_ids"]),
        included_verification_event_ids=tuple(canonical_payload["included_verification_event_ids"]),
        suppression_event_id=str(decision.suppression_event_id),
        enrichment_event_id=str(decision.enrichment_event_id),
        human_review_event_id=human_review_event_id,
        signed_payload_hash_sha256=signed_payload_hash,
        signature=signature,
        send_authorized=False,
        status=ExportEligibility.INTERNAL_REVIEW_ONLY,
        blocked_reason=INTERNAL_EXPORT_BLOCKED_REASON,
        created_at_utc=INTERNAL_EXPORT_CREATED_AT_UTC,
        schema_version=PHASE1_SCHEMA_VERSION,
    )


def write_internal_export_event(conn: sqlite3.Connection, contract: InternalExportContract) -> int:
    """Persist one append-only no-send ``export_events`` row."""

    if contract.send_authorized:
        raise InternalExportSafetyError("M7 refuses to write send_authorized export events")
    if contract.status != ExportEligibility.INTERNAL_REVIEW_ONLY:
        raise InternalExportSafetyError("M7 only writes internal_review_only export events")
    if contract.export_target != INTERNAL_EXPORT_TARGET:
        raise InternalExportSafetyError("M7 only writes internal_review_queue export events")
    inserted = _insert_or_ignore(conn, "export_events", contract.to_export_event_payload())
    conn.commit()
    return inserted


def prepare_internal_export(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    entity_id: str,
    campaign_id: str | None = None,
    export_target: str = INTERNAL_EXPORT_TARGET,
    human_review_event_id: str | None = None,
) -> InternalExportWriteResult:
    """Run the M6 gate, then persist the M7 no-send internal export row.

    Re-running is idempotent because both the M6 verification event and M7 export
    event IDs are deterministic and inserted with ``INSERT OR IGNORE``.
    """

    decision = promote_entity(conn, batch_id=batch_id, entity_id=entity_id)
    contract = build_internal_export_contract(
        conn,
        decision,
        campaign_id=campaign_id,
        export_target=export_target,
        human_review_event_id=human_review_event_id,
    )
    inserted = write_internal_export_event(conn, contract)
    return InternalExportWriteResult(contract=contract, inserted=inserted)
