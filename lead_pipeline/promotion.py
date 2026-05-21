"""Deterministic fixture-only promotion / eligibility gate for Phase 1 M6.

M6 is the first layer that decides how far an assembled M5 business entity may
advance through the lead pipeline. It is deliberately conservative: it reads
only local SQLite fixture rows, emits at most one verification-event-shaped
promotion decision, and never authorizes live outreach or writes export events.

The gate promotes only when every prerequisite has explicit provenance:

* source-backed entity/facts from M4/M5 fixture observations;
* non-destructive identity state with no unresolved duplicate edge;
* source-backed PermitAssist ICP facts;
* a contact email observed as a fact;
* independent verification_events for contact/email/domain checks;
* suppression clear snapshot;
* optional enrichment that is citation-backed and validator-passed before a row
  can become ``outreach_ready_internal_only``.

Unknown is not pass. Suppression conflicts fail closed into a hold. Aggregator
only evidence requires review. The module performs no network, DNS, SMTP,
scraping, paid-provider, subprocess, browser, filesystem, export, or outreach
action.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .contracts import (
    ExportEligibility,
    FactField,
    GateStatus,
    PromotionTier,
    SourceClass,
    SuppressionStatus,
    assert_phase1_promotion_allowed,
)
from .schema import PHASE1_SCHEMA_VERSION, get_table_contract

PROMOTION_VERSION = "lead_pipeline_phase1_m6_promotion_gate_v1"
PROMOTION_GATE_NAME = "phase1_m6_promotion_eligibility_gate"
PROMOTION_OBSERVED_AT_UTC = "2026-05-20T00:00:00Z"
SEARCH_ONLY_HOLD_SCORE = 0.4
UNTRUSTED_OFFICIAL_OR_FIRST_PARTY_HOLD_SCORE = 0.45

_IDENTITY_FACT_TYPES = {
    FactField.BUSINESS_NAME.value,
    FactField.LEGAL_NAME.value,
    FactField.DBA_NAME.value,
    FactField.WEBSITE_URL.value,
}
_ICP_FACT_TYPES = {
    FactField.PERMITASSIST_ICP_SEGMENT.value,
    FactField.LOW_CALL_RELEVANCE_SIGNAL.value,
    FactField.TRADE_CATEGORY.value,
    FactField.LICENSE_CLASS.value,
}
_CONTACT_FACT_TYPES = {FactField.CONTACT_EMAIL.value}
_CONTACT_PASS_GATES = {
    "contact_observation_gate",
    "email_syntax_gate",
    "domain_quality_gate",
}
_NO_SEND_LOCAL_PARTS = {
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "bounce",
}
_PLACEHOLDER_EMAIL_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "test.com",
    "domain.com",
}
_EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")


class PromotionSafetyError(RuntimeError):
    """Raised when M6 cannot evaluate safely from local fixture evidence."""


@dataclass(frozen=True)
class PromotionDecision:
    """Machine-readable M6 decision for one assembled entity."""

    batch_id: str
    entity_id: str
    promotion_tier: PromotionTier
    status: GateStatus
    export_eligibility: ExportEligibility
    reason_codes: tuple[str, ...]
    eligible_fact_ids: tuple[str, ...] = field(default_factory=tuple)
    source_observation_ids: tuple[str, ...] = field(default_factory=tuple)
    verification_event_ids: tuple[str, ...] = field(default_factory=tuple)
    suppression_event_id: str | None = None
    enrichment_event_id: str | None = None
    identity_edge_ids: tuple[str, ...] = field(default_factory=tuple)
    score: float | None = None
    input_hash: str = ""
    send_authorized: bool = False

    def to_verification_event_payload(self) -> dict[str, Any]:
        """Return an append-only M1 verification_events-compatible payload."""

        return {
            "verification_event_id": f"ve_m6_promotion_{self.input_hash[:24]}",
            "batch_id": self.batch_id,
            "target_entity_id": self.entity_id,
            "target_fact_id": None,
            "gate_name": PROMOTION_GATE_NAME,
            "gate_version": PROMOTION_VERSION,
            "input_hash": self.input_hash,
            "result_status": self.status.value,
            "score": self.score,
            "reason_codes": json.dumps(list(self.reason_codes), sort_keys=True),
            "network_used_flag": 0,
            "cached_result_flag": 0,
            "observed_at_utc": PROMOTION_OBSERVED_AT_UTC,
            "expires_at_utc": None,
            "raw_result_ref": json.dumps(
                {
                    "promotion_tier": self.promotion_tier.value,
                    "export_eligibility": self.export_eligibility.value,
                    "eligible_fact_ids": list(self.eligible_fact_ids),
                    "source_observation_ids": list(self.source_observation_ids),
                    "verification_event_ids": list(self.verification_event_ids),
                    "suppression_event_id": self.suppression_event_id,
                    "enrichment_event_id": self.enrichment_event_id,
                    "identity_edge_ids": list(self.identity_edge_ids),
                    "send_authorized": self.send_authorized,
                },
                sort_keys=True,
            ),
            "cost_event_id": None,
            "schema_version": PHASE1_SCHEMA_VERSION,
        }


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_load_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return [str(value)]
    if isinstance(loaded, list):
        return [str(item) for item in loaded]
    return [str(loaded)]


def _filter_columns(payload: Mapping[str, Any], allowed: Iterable[str]) -> dict[str, Any]:
    allowed_set = set(allowed)
    return {key: value for key, value in payload.items() if key in allowed_set}


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


def _rows(conn: sqlite3.Connection, query: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    cursor = conn.execute(query, tuple(params))
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _decision(
    *,
    batch_id: str,
    entity_id: str,
    promotion_tier: PromotionTier,
    status: GateStatus,
    export_eligibility: ExportEligibility,
    reason_codes: Sequence[str],
    facts: Sequence[Mapping[str, Any]] = (),
    source_rows: Sequence[Mapping[str, Any]] = (),
    verification_rows: Sequence[Mapping[str, Any]] = (),
    suppression_row: Mapping[str, Any] | None = None,
    enrichment_row: Mapping[str, Any] | None = None,
    identity_edges: Sequence[Mapping[str, Any]] = (),
    score: float | None = None,
) -> PromotionDecision:
    assert_phase1_promotion_allowed(promotion_tier)
    fact_ids = tuple(sorted(str(row["fact_id"]) for row in facts if row.get("fact_id")))
    observation_ids = tuple(
        sorted({str(row["source_observation_id"]) for row in facts if row.get("source_observation_id")})
    )
    verification_ids = tuple(
        sorted(str(row["verification_event_id"]) for row in verification_rows if row.get("verification_event_id"))
    )
    edge_ids = tuple(sorted(str(row["edge_id"]) for row in identity_edges if row.get("edge_id")))
    suppression_event_id = None
    if suppression_row and suppression_row.get("suppression_event_id"):
        suppression_event_id = str(suppression_row["suppression_event_id"])
    enrichment_event_id = None
    if enrichment_row and enrichment_row.get("enrichment_event_id"):
        enrichment_event_id = str(enrichment_row["enrichment_event_id"])
    fingerprint = {
        "batch_id": batch_id,
        "entity_id": entity_id,
        "promotion_tier": promotion_tier.value,
        "status": status.value,
        "export_eligibility": export_eligibility.value,
        "reason_codes": list(reason_codes),
        "fact_ids": fact_ids,
        "source_observation_ids": observation_ids,
        "source_ids": sorted(str(row.get("source_id")) for row in source_rows if row.get("source_id")),
        "verification_event_ids": verification_ids,
        "suppression_event_id": suppression_event_id,
        "enrichment_event_id": enrichment_event_id,
        "identity_edge_ids": edge_ids,
        "score": score,
        "send_authorized": False,
    }
    return PromotionDecision(
        batch_id=batch_id,
        entity_id=entity_id,
        promotion_tier=promotion_tier,
        status=status,
        export_eligibility=export_eligibility,
        reason_codes=tuple(reason_codes),
        eligible_fact_ids=fact_ids,
        source_observation_ids=observation_ids,
        verification_event_ids=verification_ids,
        suppression_event_id=suppression_event_id,
        enrichment_event_id=enrichment_event_id,
        identity_edge_ids=edge_ids,
        score=score,
        input_hash=_stable_hash(fingerprint),
        send_authorized=False,
    )


def _load_entity(conn: sqlite3.Connection, entity_id: str) -> Mapping[str, Any]:
    found = _rows(
        conn,
        "SELECT entity_id, entity_type, canonical_label, normalized_key, status, "
        "created_from_observation_id, schema_version FROM entities WHERE entity_id = ?",
        (entity_id,),
    )
    if not found:
        raise PromotionSafetyError(f"unknown entity_id {entity_id!r}")
    entity = found[0]
    if entity.get("schema_version") != PHASE1_SCHEMA_VERSION:
        raise PromotionSafetyError(
            f"entity {entity_id!r} schema_version mismatch: got {entity.get('schema_version')!r}, "
            f"expected {PHASE1_SCHEMA_VERSION!r}"
        )
    return entity


def _load_facts(conn: sqlite3.Connection, entity_id: str) -> list[dict[str, Any]]:
    return _rows(
        conn,
        "SELECT fact_id, entity_id, fact_type, fact_value, promotion_status, "
        "source_observation_id, field_relevant_snippet, promoted_by_gate_event_id, schema_version "
        "FROM facts WHERE entity_id = ? ORDER BY fact_id",
        (entity_id,),
    )


def _load_source_rows(conn: sqlite3.Connection, facts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    observation_ids = sorted({str(fact.get("source_observation_id")) for fact in facts if fact.get("source_observation_id")})
    if not observation_ids:
        return []
    placeholders = ",".join("?" for _ in observation_ids)
    return _rows(
        conn,
        "SELECT so.observation_id, so.source_id, so.batch_id, so.url_or_path, "
        "so.snippet_or_excerpt, so.payload_hash_sha256, so.schema_version AS observation_schema_version, "
        "s.source_class, s.official_or_first_party_flag, s.requires_login, s.paid_flag, "
        "s.allowed_phase, s.schema_version AS source_schema_version "
        "FROM source_observations so JOIN sources s ON s.source_id = so.source_id "
        f"WHERE so.observation_id IN ({placeholders}) ORDER BY so.observation_id",
        observation_ids,
    )


def _load_verification_rows(conn: sqlite3.Connection, entity_id: str, fact_ids: Sequence[str]) -> list[dict[str, Any]]:
    clauses = ["target_entity_id = ?"]
    params: list[Any] = [entity_id]
    if fact_ids:
        placeholders = ",".join("?" for _ in fact_ids)
        clauses.append(f"target_fact_id IN ({placeholders})")
        params.extend(fact_ids)
    return _rows(
        conn,
        "SELECT verification_event_id, target_entity_id, target_fact_id, gate_name, gate_version, "
        "result_status, reason_codes, network_used_flag, schema_version FROM verification_events "
        f"WHERE {' OR '.join(clauses)} ORDER BY verification_event_id",
        params,
    )


def _load_suppression_rows(conn: sqlite3.Connection, entity_id: str, fact_ids: Sequence[str]) -> list[dict[str, Any]]:
    clauses = ["target_entity_id = ?"]
    params: list[Any] = [entity_id]
    if fact_ids:
        placeholders = ",".join("?" for _ in fact_ids)
        clauses.append(f"target_fact_id IN ({placeholders})")
        params.extend(fact_ids)
    return _rows(
        conn,
        "SELECT suppression_event_id, target_entity_id, target_fact_id, status, reason, "
        "suppression_snapshot_hash, checked_at_utc, schema_version FROM suppression_events "
        f"WHERE {' OR '.join(clauses)} ORDER BY checked_at_utc, suppression_event_id",
        params,
    )


def _load_enrichment_rows(conn: sqlite3.Connection, entity_id: str) -> list[dict[str, Any]]:
    return _rows(
        conn,
        "SELECT enrichment_event_id, entity_id, input_fact_ids, input_observation_ids, "
        "unsupported_claim_count, validator_status, validator_reason_codes, schema_version "
        "FROM enrichment_events WHERE entity_id = ? ORDER BY enrichment_event_id",
        (entity_id,),
    )


def _load_identity_edges(conn: sqlite3.Connection, entity_id: str) -> list[dict[str, Any]]:
    return _rows(
        conn,
        "SELECT edge_id, from_entity_id, to_entity_id, edge_type, match_key, review_status, "
        "evidence_fact_ids, superseded_by_edge_id, schema_version FROM identity_edges "
        "WHERE (from_entity_id = ? OR to_entity_id = ?) AND superseded_by_edge_id IS NULL "
        "ORDER BY edge_id",
        (entity_id, entity_id),
    )


def _has_fact_type(facts: Sequence[Mapping[str, Any]], fact_types: set[str]) -> bool:
    return any(str(fact.get("fact_type")) in fact_types for fact in facts)


def _facts_by_type(facts: Sequence[Mapping[str, Any]], fact_types: set[str]) -> list[Mapping[str, Any]]:
    return [fact for fact in facts if str(fact.get("fact_type")) in fact_types]


def _unsafe_email_reason(email: str) -> str | None:
    value = email.strip().lower()
    if not value or not _EMAIL_RE.match(value):
        return "invalid_contact_email_fact"
    local, domain = value.rsplit("@", 1)
    if local in _NO_SEND_LOCAL_PARTS:
        return "no_reply_or_internal_email"
    if domain in _PLACEHOLDER_EMAIL_DOMAINS or local in {"email", "user", "name", "test"}:
        return "placeholder_email"
    return None


def _fact_has_passes(
    fact: Mapping[str, Any],
    verification_rows: Sequence[Mapping[str, Any]],
    required_gate_names: set[str],
) -> tuple[bool, list[Mapping[str, Any]], list[str]]:
    fact_id = str(fact.get("fact_id"))
    relevant = [
        row
        for row in verification_rows
        if row.get("target_fact_id") == fact_id and row.get("gate_name") in required_gate_names
    ]
    pass_gate_names = {
        str(row.get("gate_name"))
        for row in relevant
        if row.get("result_status") == GateStatus.PASS_.value
        and int(row.get("network_used_flag") or 0) == 0
    }
    missing = sorted(required_gate_names - pass_gate_names)
    return not missing, relevant, missing


def _latest_suppression_decision(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    return rows[-1] if rows else None


def _latest_valid_enrichment(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    for row in reversed(rows):
        if row.get("validator_status") != GateStatus.PASS_.value:
            continue
        if int(row.get("unsupported_claim_count") or 0) != 0:
            continue
        if not _json_load_list(row.get("input_fact_ids")):
            continue
        if not _json_load_list(row.get("input_observation_ids")):
            continue
        return row
    return None


def _has_invalid_enrichment(rows: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        row.get("validator_status") not in {None, GateStatus.PASS_.value}
        or int(row.get("unsupported_claim_count") or 0) > 0
        or not _json_load_list(row.get("input_fact_ids"))
        or not _json_load_list(row.get("input_observation_ids"))
        for row in rows
    )


def evaluate_entity_promotion(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    entity_id: str,
) -> PromotionDecision:
    """Evaluate one M5-assembled entity without side effects.

    The returned decision is deterministic and safe to persist through
    :func:`write_promotion_decision_event`. It never returns the reserved
    ``live_outreach_ready_future_phase`` tier and never sets send authorization.
    """

    _load_entity(conn, entity_id)
    facts = _load_facts(conn, entity_id)
    fact_ids = tuple(str(fact["fact_id"]) for fact in facts)
    source_rows = _load_source_rows(conn, facts)
    verification_rows = _load_verification_rows(conn, entity_id, fact_ids)
    suppression_rows = _load_suppression_rows(conn, entity_id, fact_ids)
    enrichment_rows = _load_enrichment_rows(conn, entity_id)
    identity_edges = _load_identity_edges(conn, entity_id)

    if not facts or not source_rows:
        return _decision(
            batch_id=batch_id,
            entity_id=entity_id,
            promotion_tier=PromotionTier.RAW_DISCOVERY,
            status=GateStatus.FAIL_CLOSED,
            export_eligibility=ExportEligibility.BLOCKED_UNCITED,
            reason_codes=("missing_source_provenance",),
            facts=facts,
            source_rows=source_rows,
            score=0.0,
        )

    if len(source_rows) != len({fact.get("source_observation_id") for fact in facts}):
        return _decision(
            batch_id=batch_id,
            entity_id=entity_id,
            promotion_tier=PromotionTier.RAW_DISCOVERY,
            status=GateStatus.FAIL_CLOSED,
            export_eligibility=ExportEligibility.BLOCKED_UNCITED,
            reason_codes=("source_observation_join_failure",),
            facts=facts,
            source_rows=source_rows,
            score=0.0,
        )

    if any(fact.get("schema_version") != PHASE1_SCHEMA_VERSION for fact in facts):
        return _decision(
            batch_id=batch_id,
            entity_id=entity_id,
            promotion_tier=PromotionTier.RAW_DISCOVERY,
            status=GateStatus.FAIL_CLOSED,
            export_eligibility=ExportEligibility.BLOCKED_UNKNOWN,
            reason_codes=("schema_version_mismatch",),
            facts=facts,
            source_rows=source_rows,
            score=0.0,
        )

    if any(not fact.get("field_relevant_snippet") for fact in facts):
        return _decision(
            batch_id=batch_id,
            entity_id=entity_id,
            promotion_tier=PromotionTier.RAW_DISCOVERY,
            status=GateStatus.FAIL_CLOSED,
            export_eligibility=ExportEligibility.BLOCKED_UNCITED,
            reason_codes=("missing_field_level_citation",),
            facts=facts,
            source_rows=source_rows,
            score=0.0,
        )

    if any(row.get("batch_id") != batch_id for row in source_rows):
        return _decision(
            batch_id=batch_id,
            entity_id=entity_id,
            promotion_tier=PromotionTier.RAW_DISCOVERY,
            status=GateStatus.FAIL_CLOSED,
            export_eligibility=ExportEligibility.BLOCKED_UNKNOWN,
            reason_codes=("batch_id_mismatch",),
            facts=facts,
            source_rows=source_rows,
            score=0.0,
        )

    if any(row.get("observation_schema_version") != PHASE1_SCHEMA_VERSION or row.get("source_schema_version") != PHASE1_SCHEMA_VERSION for row in source_rows):
        return _decision(
            batch_id=batch_id,
            entity_id=entity_id,
            promotion_tier=PromotionTier.RAW_DISCOVERY,
            status=GateStatus.FAIL_CLOSED,
            export_eligibility=ExportEligibility.BLOCKED_UNKNOWN,
            reason_codes=("schema_version_mismatch",),
            facts=facts,
            source_rows=source_rows,
            score=0.0,
        )

    if any(int(row.get("requires_login") or 0) or int(row.get("paid_flag") or 0) for row in source_rows):
        return _decision(
            batch_id=batch_id,
            entity_id=entity_id,
            promotion_tier=PromotionTier.RAW_DISCOVERY,
            status=GateStatus.BLOCKED_SOURCE_POLICY,
            export_eligibility=ExportEligibility.NOT_EXPORTABLE,
            reason_codes=("blocked_paid_or_login_source",),
            facts=facts,
            source_rows=source_rows,
            score=0.0,
        )

    if identity_edges:
        return _decision(
            batch_id=batch_id,
            entity_id=entity_id,
            promotion_tier=PromotionTier.QUALIFIED_LEAD_REVIEW_REQUIRED,
            status=GateStatus.REVIEW_REQUIRED,
            export_eligibility=ExportEligibility.REVIEW_REQUIRED,
            reason_codes=("identity_ambiguity_requires_human_review",),
            facts=facts,
            source_rows=source_rows,
            verification_rows=verification_rows,
            identity_edges=identity_edges,
            score=0.5,
        )

    source_classes = {str(row.get("source_class")) for row in source_rows}
    authoritative_evidence_present = any(
        str(row.get("source_class")) in {SourceClass.OFFICIAL_LICENSING.value, SourceClass.FIRST_PARTY_WEBSITE.value}
        and int(row.get("official_or_first_party_flag") or 0)
        for row in source_rows
    )
    raw_discovery_only_classes = {
        SourceClass.SEARCH_OR_PLACES.value,
        SourceClass.AGGREGATOR_DIRECTORY.value,
    }
    raw_or_untrusted_first_party_classes = raw_discovery_only_classes | {
        SourceClass.OFFICIAL_LICENSING.value,
        SourceClass.FIRST_PARTY_WEBSITE.value,
    }
    # Use subset, not intersection: user imports and other approved seed inputs have separate
    # review gates; this branch only handles raw discovery plus untrusted official/first-party-shaped evidence.
    if source_classes and source_classes <= raw_or_untrusted_first_party_classes and not authoritative_evidence_present:
        if SourceClass.SEARCH_OR_PLACES.value in source_classes:
            reason_codes = ["search_only_evidence_not_promotable"]
            if not source_classes <= raw_discovery_only_classes:
                reason_codes.append("untrusted_official_or_first_party_evidence_requires_corroboration")
            return _decision(
                batch_id=batch_id,
                entity_id=entity_id,
                promotion_tier=PromotionTier.QUALIFIED_LEAD_REVIEW_REQUIRED,
                status=GateStatus.REVIEW_REQUIRED,
                export_eligibility=ExportEligibility.REVIEW_REQUIRED,
                reason_codes=tuple(reason_codes),
                facts=facts,
                source_rows=source_rows,
                verification_rows=verification_rows,
                score=SEARCH_ONLY_HOLD_SCORE,
            )
        reason_codes = []
        if SourceClass.AGGREGATOR_DIRECTORY.value in source_classes:
            reason_codes.append("aggregator_only_evidence_requires_corroboration")
        if not source_classes <= raw_discovery_only_classes:
            reason_codes.append("untrusted_official_or_first_party_evidence_requires_corroboration")
        return _decision(
            batch_id=batch_id,
            entity_id=entity_id,
            promotion_tier=PromotionTier.QUALIFIED_LEAD_REVIEW_REQUIRED,
            status=GateStatus.REVIEW_REQUIRED,
            export_eligibility=ExportEligibility.REVIEW_REQUIRED,
            reason_codes=tuple(reason_codes),
            facts=facts,
            source_rows=source_rows,
            verification_rows=verification_rows,
            score=UNTRUSTED_OFFICIAL_OR_FIRST_PARTY_HOLD_SCORE,
        )

    if not _has_fact_type(facts, _IDENTITY_FACT_TYPES):
        return _decision(
            batch_id=batch_id,
            entity_id=entity_id,
            promotion_tier=PromotionTier.RAW_DISCOVERY,
            status=GateStatus.FAIL_CLOSED,
            export_eligibility=ExportEligibility.BLOCKED_UNCITED,
            reason_codes=("missing_cited_business_identity",),
            facts=facts,
            source_rows=source_rows,
            score=0.0,
        )

    if not _has_fact_type(facts, _ICP_FACT_TYPES):
        return _decision(
            batch_id=batch_id,
            entity_id=entity_id,
            promotion_tier=PromotionTier.IDENTITY_CANDIDATE,
            status=GateStatus.UNKNOWN_NOT_PROMOTED,
            export_eligibility=ExportEligibility.NOT_EXPORTABLE,
            reason_codes=("missing_source_backed_permitassist_icp_fit",),
            facts=facts,
            source_rows=source_rows,
            verification_rows=verification_rows,
            score=0.35,
        )

    contact_facts = _facts_by_type(facts, _CONTACT_FACT_TYPES)
    if not contact_facts:
        return _decision(
            batch_id=batch_id,
            entity_id=entity_id,
            promotion_tier=PromotionTier.ICP_CANDIDATE,
            status=GateStatus.UNKNOWN_NOT_PROMOTED,
            export_eligibility=ExportEligibility.NOT_EXPORTABLE,
            reason_codes=("missing_contact_candidate",),
            facts=facts,
            source_rows=source_rows,
            verification_rows=verification_rows,
            score=0.5,
        )

    for contact_fact in contact_facts:
        unsafe_reason = _unsafe_email_reason(str(contact_fact.get("fact_value") or ""))
        if unsafe_reason:
            return _decision(
                batch_id=batch_id,
                entity_id=entity_id,
                promotion_tier=PromotionTier.CONTACT_CANDIDATE,
                status=GateStatus.FAIL_CLOSED,
                export_eligibility=ExportEligibility.BLOCKED_UNVERIFIED_CONTACT,
                reason_codes=(unsafe_reason,),
                facts=facts,
                source_rows=source_rows,
                verification_rows=verification_rows,
                score=0.0,
            )

    verified_contact_fact: Mapping[str, Any] | None = None
    used_verification_rows: list[Mapping[str, Any]] = []
    missing_gate_names: list[str] = []
    for contact_fact in contact_facts:
        ok, relevant_rows, missing = _fact_has_passes(contact_fact, verification_rows, _CONTACT_PASS_GATES)
        if ok:
            verified_contact_fact = contact_fact
            used_verification_rows = list(relevant_rows)
            break
        missing_gate_names = missing

    if verified_contact_fact is None:
        reason = "missing_independent_contact_verification"
        if missing_gate_names:
            reason = "missing_independent_contact_verification:" + ",".join(missing_gate_names)
        return _decision(
            batch_id=batch_id,
            entity_id=entity_id,
            promotion_tier=PromotionTier.CONTACT_CANDIDATE,
            status=GateStatus.UNKNOWN_NOT_PROMOTED,
            export_eligibility=ExportEligibility.BLOCKED_UNVERIFIED_CONTACT,
            reason_codes=(reason,),
            facts=facts,
            source_rows=source_rows,
            verification_rows=verification_rows,
            score=0.55,
        )

    latest_suppression = _latest_suppression_decision(suppression_rows)
    if latest_suppression is None:
        return _decision(
            batch_id=batch_id,
            entity_id=entity_id,
            promotion_tier=PromotionTier.VERIFIED_CONTACT,
            status=GateStatus.UNKNOWN_NOT_PROMOTED,
            export_eligibility=ExportEligibility.BLOCKED_UNKNOWN,
            reason_codes=("missing_suppression_snapshot",),
            facts=facts,
            source_rows=source_rows,
            verification_rows=used_verification_rows,
            score=0.65,
        )

    suppression_status = latest_suppression.get("status")
    if suppression_status == SuppressionStatus.SUPPRESSION_CONFLICT_HOLD.value:
        return _decision(
            batch_id=batch_id,
            entity_id=entity_id,
            promotion_tier=PromotionTier.QUALIFIED_LEAD_REVIEW_REQUIRED,
            status=GateStatus.UNKNOWN_NOT_PROMOTED,
            export_eligibility=ExportEligibility.BLOCKED_UNKNOWN,
            reason_codes=("suppression_conflict_hold",),
            facts=facts,
            source_rows=source_rows,
            verification_rows=used_verification_rows,
            suppression_row=latest_suppression,
            score=0.0,
        )
    if suppression_status != SuppressionStatus.CLEAR.value:
        return _decision(
            batch_id=batch_id,
            entity_id=entity_id,
            promotion_tier=PromotionTier.QUALIFIED_LEAD_REVIEW_REQUIRED,
            status=GateStatus.FAIL_CLOSED,
            export_eligibility=ExportEligibility.BLOCKED_SUPPRESSED,
            reason_codes=("suppression_blocks_promotion",),
            facts=facts,
            source_rows=source_rows,
            verification_rows=used_verification_rows,
            suppression_row=latest_suppression,
            score=0.0,
        )

    if _has_invalid_enrichment(enrichment_rows):
        return _decision(
            batch_id=batch_id,
            entity_id=entity_id,
            promotion_tier=PromotionTier.VERIFIED_CONTACT,
            status=GateStatus.UNKNOWN_NOT_PROMOTED,
            export_eligibility=ExportEligibility.BLOCKED_UNCITED,
            reason_codes=("uncited_or_invalid_enrichment_not_promoted",),
            facts=facts,
            source_rows=source_rows,
            verification_rows=used_verification_rows,
            suppression_row=latest_suppression,
            score=0.65,
        )

    valid_enrichment = _latest_valid_enrichment(enrichment_rows)
    if valid_enrichment is None:
        return _decision(
            batch_id=batch_id,
            entity_id=entity_id,
            promotion_tier=PromotionTier.VERIFIED_CONTACT,
            status=GateStatus.PASS_,
            export_eligibility=ExportEligibility.NOT_EXPORTABLE,
            reason_codes=("verified_contact_not_enriched_for_internal_export",),
            facts=facts,
            source_rows=source_rows,
            verification_rows=used_verification_rows,
            suppression_row=latest_suppression,
            score=0.75,
        )

    return _decision(
        batch_id=batch_id,
        entity_id=entity_id,
        promotion_tier=PromotionTier.OUTREACH_READY_INTERNAL_ONLY,
        status=GateStatus.PASS_,
        export_eligibility=ExportEligibility.INTERNAL_REVIEW_ONLY,
        reason_codes=("outreach_ready_internal_review_only_no_send",),
        facts=facts,
        source_rows=source_rows,
        verification_rows=used_verification_rows,
        suppression_row=latest_suppression,
        enrichment_row=valid_enrichment,
        score=1.0,
    )


def write_promotion_decision_event(conn: sqlite3.Connection, decision: PromotionDecision) -> int:
    """Persist a decision as one append-only verification_events row.

    This function intentionally does not update ``entities``/``facts`` and does
    not write ``export_events``. M7 will consume the M6 decision and build an
    internal no-send export contract.
    """

    if decision.send_authorized:
        raise PromotionSafetyError("M6 decisions may never authorize sending")
    assert_phase1_promotion_allowed(decision.promotion_tier)
    inserted = _insert_or_ignore(conn, "verification_events", decision.to_verification_event_payload())
    conn.commit()
    return inserted


def promote_entity(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    entity_id: str,
) -> PromotionDecision:
    """Evaluate and persist one M6 promotion decision."""

    decision = evaluate_entity_promotion(conn, batch_id=batch_id, entity_id=entity_id)
    write_promotion_decision_event(conn, decision)
    return decision
