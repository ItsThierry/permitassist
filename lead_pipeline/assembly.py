"""Deterministic stdlib-only entity + fact assembly for Phase 1 M5.

The M5 assembler builds the first local entity/fact representation on top of
M1 schema metadata, M3 fixture connector lineage, and the M4 SQLite event
writer. It is internal-review-only: it never enables live outreach, never
sets ``send_authorized``, never writes ``export_events``, never uses
``LIVE_OUTREACH_READY_FUTURE_PHASE``, and never infers a field from text.

Inputs are explicit synthetic fixture records (``AssemblyFixtureRecord``)
that name an already-persisted ``source_observations`` row by id and carry a
per-record ``field_relevant_snippet`` plus a mapping of explicit business
fields. Unknown fields are silently omitted, not guessed. Records missing
required provenance (snippet) or missing any identity field
(business_name / legal_name / dba_name / website_url) are skipped with a
deterministic review reason and never reach the database.

Dedupe is deterministic and *non-destructive*. When two records share a
normalized business-name / legal-name / website-domain key, two entities are
still created (one per record, preserving full per-observation lineage), and
a single ``identity_edges`` row of type ``"duplicate_candidate"`` with
``review_status = "review_required"`` is written between them. No row is
ever overwritten or deleted by the assembler.

The module performs no network, DNS, SMTP, scraping, paid-provider, browser,
subprocess, or outreach action.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .connectors import FIXTURE_URL_PREFIX
from .contracts import (
    EntityKind,
    FactField,
    GateStatus,
    PromotionTier,
    assert_phase1_promotion_allowed,
)
from .schema import PHASE1_SCHEMA_VERSION, get_table_contract

ASSEMBLY_VERSION = "lead_pipeline_phase1_m5_entity_assembly_v1"

# Newly assembled rows are deliberately the lowest Phase 1 tier; downstream
# gates promote them via verification_events in later milestones.
_ENTITY_STATUS = PromotionTier.RAW_DISCOVERY.value
_FACT_PROMOTION_STATUS = PromotionTier.RAW_DISCOVERY.value
_DUPLICATE_EDGE_TYPE = "duplicate_candidate"
_EDGE_REVIEW_STATUS = GateStatus.REVIEW_REQUIRED.value
_EDGE_CREATED_BY = "phase1_m5_entity_assembler"

_ALLOWED_FACT_FIELDS: tuple[str, ...] = (
    FactField.BUSINESS_NAME.value,
    FactField.LEGAL_NAME.value,
    FactField.DBA_NAME.value,
    FactField.WEBSITE_URL.value,
    FactField.SERVICE_AREA.value,
    FactField.TRADE_CATEGORY.value,
    FactField.LICENSE_CLASS.value,
    FactField.CONTACT_EMAIL.value,
    FactField.PERMITASSIST_ICP_SEGMENT.value,
    FactField.LOW_CALL_RELEVANCE_SIGNAL.value,
)

_IDENTITY_FIELDS = (
    FactField.BUSINESS_NAME.value,
    FactField.LEGAL_NAME.value,
    FactField.DBA_NAME.value,
    FactField.WEBSITE_URL.value,
)

_LEGAL_SUFFIX_TOKENS = (
    "inc",
    "incorporated",
    "llc",
    "l.l.c",
    "llp",
    "ltd",
    "limited",
    "co",
    "corp",
    "corporation",
    "company",
)

_PUNCT_RE = re.compile(r"[^\w\s]+")
_WS_RE = re.compile(r"\s+")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


class AssemblySafetyError(RuntimeError):
    """Raised when an M5 assembly call would violate Phase 1 safety guarantees."""


@dataclass(frozen=True)
class AssemblyFixtureRecord:
    """One explicit synthetic fixture record naming an observation and fields.

    ``observation_id`` must reference an already-persisted, fixture-only
    ``source_observations`` row. ``field_relevant_snippet`` is the cited
    snippet attached to every fact created from this record. Only keys in
    :data:`_ALLOWED_FACT_FIELDS` are turned into facts; unknown keys are
    silently omitted (the assembler never guesses).
    """

    observation_id: str
    field_relevant_snippet: str
    business_fields: Mapping[str, Any]


@dataclass(frozen=True)
class AssemblySkip:
    """Why a single input record was skipped, recorded deterministically."""

    observation_id: str | None
    reason: str


@dataclass(frozen=True)
class AssemblySummary:
    """Deterministic per-call count of what the assembler did."""

    batch_id: str
    entities_inserted: int
    facts_inserted: int
    identity_edges_inserted: int
    records_skipped: int
    records_review_required: int
    skip_details: tuple[AssemblySkip, ...] = field(default_factory=tuple)


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, default=str, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _entity_id_for(observation_id: str, normalized_key: str) -> str:
    return f"ent_{_stable_hash([observation_id, normalized_key])[:24]}"


def _fact_id_for(entity_id: str, fact_type: str, fact_value: str) -> str:
    return f"fct_{_stable_hash([entity_id, fact_type, fact_value])[:24]}"


def _edge_id_for(left: str, right: str, match_key: str) -> str:
    a, b = sorted((left, right))
    return f"edg_{_stable_hash([a, b, _DUPLICATE_EDGE_TYPE, match_key])[:24]}"


def _normalize_text(value: str) -> str:
    stripped = _PUNCT_RE.sub(" ", value.strip().lower())
    collapsed = _WS_RE.sub(" ", stripped).strip()
    if not collapsed:
        return ""
    tokens = collapsed.split(" ")
    while tokens and tokens[-1] in _LEGAL_SUFFIX_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def _domain_from_url(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    without_scheme = _SCHEME_RE.sub("", text, count=1)
    host = without_scheme.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    host = host.split("@", 1)[-1]
    host = host.split(":", 1)[0]
    host = host.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return ""
    return host


def _clean_string_value(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _record_identity_values(business_fields: Mapping[str, Any]) -> dict[str, str]:
    """Return the cleaned, non-empty identity values present on the record."""

    out: dict[str, str] = {}
    for key in _IDENTITY_FIELDS:
        cleaned = _clean_string_value(business_fields.get(key))
        if cleaned:
            out[key] = cleaned
    return out


def _build_match_keys(business_fields: Mapping[str, Any]) -> dict[str, str]:
    """Build the normalized dedupe keys for one record.

    Priority for selecting the *primary* edge key when multiple keys match
    is: business_name → legal_name → website_domain. Returned dict is
    ordered to reflect that priority.
    """

    keys: dict[str, str] = {}
    business_name = _clean_string_value(business_fields.get(FactField.BUSINESS_NAME.value))
    if business_name:
        normalized = _normalize_text(business_name)
        if normalized:
            keys["business_name"] = normalized
    legal_name = _clean_string_value(business_fields.get(FactField.LEGAL_NAME.value))
    if legal_name:
        normalized = _normalize_text(legal_name)
        if normalized:
            keys["legal_name"] = normalized
    website_url = _clean_string_value(business_fields.get(FactField.WEBSITE_URL.value))
    if website_url:
        domain = _domain_from_url(website_url)
        if domain:
            keys["website_domain"] = domain
    return keys


def _canonical_label(business_fields: Mapping[str, Any]) -> str | None:
    """Pick a deterministic label for the entity row."""

    for key in (
        FactField.BUSINESS_NAME.value,
        FactField.LEGAL_NAME.value,
        FactField.DBA_NAME.value,
    ):
        cleaned = _clean_string_value(business_fields.get(key))
        if cleaned:
            return cleaned
    # If only a website_url is present, fall back to the domain itself so the
    # entity still has a stable canonical label.
    website_url = _clean_string_value(business_fields.get(FactField.WEBSITE_URL.value))
    if website_url:
        domain = _domain_from_url(website_url)
        if domain:
            return domain
    return None


def _normalized_entity_key(
    business_fields: Mapping[str, Any], match_keys: Mapping[str, str]
) -> str | None:
    """Normalized identifier stored on the entity row.

    Reuses the priority business_name → legal_name → website_domain so the
    persisted ``entities.normalized_key`` column reflects the strongest
    available dedupe key for that record.
    """

    for key in ("business_name", "legal_name", "website_domain"):
        if key in match_keys:
            return match_keys[key]
    dba = _clean_string_value(business_fields.get(FactField.DBA_NAME.value))
    if dba:
        normalized = _normalize_text(dba)
        if normalized:
            return normalized
    return None


def _filter_columns(payload: Mapping[str, Any], allowed: Iterable[str]) -> dict[str, Any]:
    allowed_set = set(allowed)
    return {key: value for key, value in payload.items() if key in allowed_set}


def _insert_or_ignore(
    conn: sqlite3.Connection, table: str, payload: Mapping[str, Any]
) -> int:
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


def _load_observation(
    conn: sqlite3.Connection, observation_id: str, expected_batch_id: str
) -> tuple[str, str, str]:
    row = conn.execute(
        "SELECT url_or_path, schema_version, batch_id FROM source_observations "
        "WHERE observation_id = ?",
        (observation_id,),
    ).fetchone()
    if row is None:
        raise AssemblySafetyError(
            f"refusing to assemble from unknown observation_id {observation_id!r}: "
            "no source_observations row found"
        )
    url_or_path, schema_version, observed_batch_id = row
    if observed_batch_id != expected_batch_id:
        raise AssemblySafetyError(
            f"refusing to assemble from observation {observation_id!r}: "
            f"batch_id mismatch (got {observed_batch_id!r}, "
            f"expected {expected_batch_id!r})"
        )
    if not isinstance(url_or_path, str) or not url_or_path.startswith(FIXTURE_URL_PREFIX):
        raise AssemblySafetyError(
            f"refusing to assemble from observation {observation_id!r}: "
            f"url_or_path must use {FIXTURE_URL_PREFIX!r} prefix; got {url_or_path!r}"
        )
    if schema_version != PHASE1_SCHEMA_VERSION:
        raise AssemblySafetyError(
            f"refusing to assemble from observation {observation_id!r}: "
            f"schema_version mismatch (got {schema_version!r}, "
            f"expected {PHASE1_SCHEMA_VERSION!r})"
        )
    return url_or_path, schema_version, observed_batch_id


def _entity_payload(
    *,
    entity_id: str,
    canonical_label: str,
    normalized_key: str,
    observation_id: str,
) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "entity_type": EntityKind.BUSINESS.value,
        "canonical_label": canonical_label,
        "normalized_key": normalized_key,
        "status": _ENTITY_STATUS,
        "created_from_observation_id": observation_id,
        "created_at_utc": None,
        "schema_version": PHASE1_SCHEMA_VERSION,
    }


def _fact_payload(
    *,
    entity_id: str,
    fact_type: str,
    fact_value: str,
    source_observation_id: str,
    field_relevant_snippet: str,
) -> dict[str, Any]:
    return {
        "fact_id": _fact_id_for(entity_id, fact_type, fact_value),
        "entity_id": entity_id,
        "fact_type": fact_type,
        "fact_value": fact_value,
        "normalized_value": None,
        "confidence": None,
        "promotion_status": _FACT_PROMOTION_STATUS,
        "source_observation_id": source_observation_id,
        "field_relevant_snippet": field_relevant_snippet,
        "promoted_by_gate_event_id": None,
        "supersedes_fact_id": None,
        "valid_from_observed_at_utc": None,
        "status_reason": None,
        "schema_version": PHASE1_SCHEMA_VERSION,
    }


def _identity_edge_payload(
    *,
    from_entity_id: str,
    to_entity_id: str,
    match_key: str,
    evidence_fact_ids: Sequence[str],
) -> dict[str, Any]:
    left, right = sorted((from_entity_id, to_entity_id))
    return {
        "edge_id": _edge_id_for(left, right, match_key),
        "from_entity_id": left,
        "to_entity_id": right,
        "edge_type": _DUPLICATE_EDGE_TYPE,
        "match_key": match_key,
        "match_confidence": None,
        "evidence_fact_ids": json.dumps(sorted(evidence_fact_ids)),
        "review_status": _EDGE_REVIEW_STATUS,
        "created_by": _EDGE_CREATED_BY,
        "superseded_by_edge_id": None,
        "schema_version": PHASE1_SCHEMA_VERSION,
    }


def assemble_entities_and_facts(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    records: Sequence[AssemblyFixtureRecord],
) -> AssemblySummary:
    """Build entities, facts, and identity-edges from fixture records.

    Records that are missing the cited per-fact snippet or any identity
    field (business_name / legal_name / dba_name / website_url) are skipped
    with a deterministic review reason and never reach the database.
    Records that name an ``observation_id`` not present in
    ``source_observations`` — or whose persisted observation has a
    non-fixture ``url_or_path`` — raise :class:`AssemblySafetyError` before
    any write.

    The assembler validates that the Phase 1 promotion tier used for new
    entities and facts is allowed (i.e. not
    ``live_outreach_ready_future_phase``) and refuses to call
    ``INSERT OR IGNORE`` on ``export_events`` or any send-authorizing
    column.
    """

    assert_phase1_promotion_allowed(_ENTITY_STATUS)
    assert_phase1_promotion_allowed(_FACT_PROMOTION_STATUS)

    skip_details: list[AssemblySkip] = []
    # Per-batch dedupe registry: match_key_type -> normalized_value -> (entity_id, fact_id_for_that_key)
    key_index: dict[str, dict[str, tuple[str, str]]] = {}

    entities_inserted = 0
    facts_inserted = 0
    edges_inserted = 0
    records_review_required = 0

    for record in records:
        url_or_path, _schema_version, _observed_batch_id = _load_observation(
            conn, record.observation_id, batch_id
        )
        snippet = _clean_string_value(record.field_relevant_snippet)
        if snippet is None:
            skip_details.append(
                AssemblySkip(
                    observation_id=record.observation_id,
                    reason="missing_field_relevant_snippet",
                )
            )
            continue

        identity_values = _record_identity_values(record.business_fields)
        if not identity_values:
            skip_details.append(
                AssemblySkip(
                    observation_id=record.observation_id,
                    reason="missing_business_identity",
                )
            )
            continue

        match_keys = _build_match_keys(record.business_fields)
        canonical_label = _canonical_label(record.business_fields)
        normalized_key = _normalized_entity_key(record.business_fields, match_keys)
        if not canonical_label or not normalized_key:
            skip_details.append(
                AssemblySkip(
                    observation_id=record.observation_id,
                    reason="missing_business_identity",
                )
            )
            continue

        entity_id = _entity_id_for(record.observation_id, normalized_key)
        entities_inserted += _insert_or_ignore(
            conn,
            "entities",
            _entity_payload(
                entity_id=entity_id,
                canonical_label=canonical_label,
                normalized_key=normalized_key,
                observation_id=record.observation_id,
            ),
        )

        # Insert one fact per known, non-empty business field.
        record_fact_ids: dict[str, str] = {}
        for field_name in _ALLOWED_FACT_FIELDS:
            cleaned = _clean_string_value(record.business_fields.get(field_name))
            if cleaned is None:
                continue
            fact_id = _fact_id_for(entity_id, field_name, cleaned)
            facts_inserted += _insert_or_ignore(
                conn,
                "facts",
                _fact_payload(
                    entity_id=entity_id,
                    fact_type=field_name,
                    fact_value=cleaned,
                    source_observation_id=record.observation_id,
                    field_relevant_snippet=snippet,
                ),
            )
            record_fact_ids[field_name] = fact_id

        # Deterministic dedupe via identity_edges (priority order:
        # business_name, legal_name, website_domain). One edge per pair.
        edge_written_for_record = False
        edge_partner_ids: set[str] = set()
        for key_type in ("business_name", "legal_name", "website_domain"):
            key_value = match_keys.get(key_type)
            if not key_value:
                continue
            type_index = key_index.setdefault(key_type, {})
            prior = type_index.get(key_value)
            if prior is not None:
                prior_entity_id, prior_fact_id = prior
                if prior_entity_id != entity_id and prior_entity_id not in edge_partner_ids:
                    field_for_key = (
                        FactField.BUSINESS_NAME.value
                        if key_type == "business_name"
                        else FactField.LEGAL_NAME.value
                        if key_type == "legal_name"
                        else FactField.WEBSITE_URL.value
                    )
                    own_fact_id = record_fact_ids.get(field_for_key)
                    evidence: list[str] = [prior_fact_id]
                    if own_fact_id:
                        evidence.append(own_fact_id)
                    inserted_edge = _insert_or_ignore(
                        conn,
                        "identity_edges",
                        _identity_edge_payload(
                            from_entity_id=prior_entity_id,
                            to_entity_id=entity_id,
                            match_key=key_value,
                            evidence_fact_ids=evidence,
                        ),
                    )
                    edges_inserted += inserted_edge
                    if inserted_edge:
                        edge_written_for_record = True
                    edge_partner_ids.add(prior_entity_id)
            else:
                field_for_key = (
                    FactField.BUSINESS_NAME.value
                    if key_type == "business_name"
                    else FactField.LEGAL_NAME.value
                    if key_type == "legal_name"
                    else FactField.WEBSITE_URL.value
                )
                fact_id_for_key = record_fact_ids.get(field_for_key, "")
                type_index[key_value] = (entity_id, fact_id_for_key)

        if edge_written_for_record:
            records_review_required += 1

    conn.commit()
    return AssemblySummary(
        batch_id=batch_id,
        entities_inserted=entities_inserted,
        facts_inserted=facts_inserted,
        identity_edges_inserted=edges_inserted,
        records_skipped=len(skip_details),
        records_review_required=records_review_required,
        skip_details=tuple(skip_details),
    )
