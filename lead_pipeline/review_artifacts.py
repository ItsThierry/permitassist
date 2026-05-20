"""Local M9 internal review artifact renderer for M8 export events.

M9 reads already-local Phase 1 ``export_events`` rows and renders deterministic
Markdown + JSON artifacts for Boban/Titi review. It does not send, browse,
scrape, call a network path, create CRM records, or authorize outreach.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import ExportEligibility, GateStatus
from .internal_export import INTERNAL_EXPORT_SCHEMA_VERSION, INTERNAL_EXPORT_TARGET
from .schema import PHASE1_SCHEMA_VERSION

PHASE1_M9_ARTIFACT_SCHEMA_VERSION = "lead_pipeline_phase1_m9_internal_review_artifact_v1"
INTERNAL_REVIEW_BANNER = (
    "INTERNAL REVIEW ONLY — Boban/Titi local artifact — "
    "send_authorized=false — internal_review_only — no outreach/no CRM/no send"
)
JSON_ARTIFACT_FILENAME = "lead_pipeline_m9_internal_review_artifacts.json"
MARKDOWN_ARTIFACT_FILENAME = "lead_pipeline_m9_internal_review_artifacts.md"

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*['\"]?[A-Za-z0-9_.\-]{8,}"),
    re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{8,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)\bxox[baprs]-[A-Za-z0-9-]{10,}"),
)


class InternalReviewArtifactSafetyError(RuntimeError):
    """Raised when M9 cannot safely render an internal review artifact."""


@dataclass(frozen=True)
class InternalReviewArtifact:
    """Deterministic JSON + Markdown M9 artifact bundle."""

    payload: Mapping[str, Any]
    markdown: str

    @property
    def json_text(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n"

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class WrittenInternalReviewArtifacts:
    """Local file paths written by M9."""

    artifact: InternalReviewArtifact
    json_path: Path
    markdown_path: Path

    def manifest(self) -> dict[str, Any]:
        payload = self.artifact.to_dict()
        return {
            "artifact_schema_version": payload["artifact_schema_version"],
            "batch_id": payload["batch_id"],
            "export_event_count": payload["export_event_count"],
            "safety": payload["safety"],
            "json_path": str(self.json_path),
            "markdown_path": str(self.markdown_path),
        }


ROW_TABLES = {
    "entities",
    "facts",
    "source_observations",
    "verification_events",
    "suppression_events",
    "enrichment_events",
    "export_events",
}


def _rows(conn: sqlite3.Connection, query: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    cursor = conn.execute(query, tuple(params))
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _one(rows: Sequence[Mapping[str, Any]], *, context: str) -> Mapping[str, Any]:
    if len(rows) != 1:
        raise InternalReviewArtifactSafetyError(f"M9 expected exactly one {context} row")
    return rows[0]


def _json_list(value: Any, *, context: str) -> list[str]:
    try:
        loaded = json.loads(str(value))
    except (TypeError, json.JSONDecodeError) as exc:
        raise InternalReviewArtifactSafetyError(f"{context} is not parseable JSON") from exc
    if not isinstance(loaded, list):
        raise InternalReviewArtifactSafetyError(f"{context} is not a JSON list")
    items = [str(item) for item in loaded]
    if len(items) != len(set(items)):
        raise InternalReviewArtifactSafetyError(f"{context} contains duplicate lineage IDs")
    return items


def _json_dict(value: Any, *, context: str) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    try:
        loaded = json.loads(str(value))
    except (TypeError, json.JSONDecodeError) as exc:
        raise InternalReviewArtifactSafetyError(f"{context} is not parseable JSON") from exc
    if not isinstance(loaded, dict):
        raise InternalReviewArtifactSafetyError(f"{context} is not a JSON object")
    return dict(loaded)


def _stable_artifact_id(export_event_id: str, payload_hash: str) -> str:
    digest = hashlib.sha256(
        json.dumps([PHASE1_M9_ARTIFACT_SCHEMA_VERSION, export_event_id, payload_hash], separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return "m9_review_" + digest[:24]


def _contains_secret_like_text(value: Any) -> bool:
    if value is None:
        return False
    text = str(value)
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def _assert_no_secret_like_rows(rows: Sequence[Mapping[str, Any]], *, context: str) -> None:
    for row in rows:
        for value in row.values():
            if _contains_secret_like_text(value):
                raise InternalReviewArtifactSafetyError(f"secret-like value found in {context}; refusing M9 artifact")


def _assert_schema(row: Mapping[str, Any], *, context: str) -> None:
    if row.get("schema_version") != PHASE1_SCHEMA_VERSION:
        raise InternalReviewArtifactSafetyError(f"{context} schema_version mismatch")


def _load_by_ids(
    conn: sqlite3.Connection,
    *,
    table: str,
    id_column: str,
    ids: Sequence[str],
    order_column: str | None = None,
) -> list[dict[str, Any]]:
    if table not in ROW_TABLES:
        raise InternalReviewArtifactSafetyError("unsupported M9 artifact table lookup")
    if not ids:
        raise InternalReviewArtifactSafetyError(f"M9 requires at least one {table} id")
    placeholders = ",".join("?" for _ in ids)
    order_by = order_column or id_column
    rows = _rows(conn, f"SELECT * FROM {table} WHERE {id_column} IN ({placeholders}) ORDER BY {order_by}", tuple(ids))
    if len(rows) != len(set(ids)):
        raise InternalReviewArtifactSafetyError(f"M9 missing lineage rows in {table}")
    for row in rows:
        _assert_schema(row, context=table)
    _assert_no_secret_like_rows(rows, context=table)
    return rows


def _export_rows(conn: sqlite3.Connection, *, batch_id: str) -> list[dict[str, Any]]:
    rows = _rows(
        conn,
        "SELECT * FROM export_events WHERE batch_id = ? ORDER BY export_event_id",
        (batch_id,),
    )
    if not rows:
        raise InternalReviewArtifactSafetyError("M9 found no export_events for batch")
    _assert_no_secret_like_rows(rows, context="export_events")
    for row in rows:
        _assert_schema(row, context="export_events")
        if int(row.get("send_authorized") or 0) != 0:
            raise InternalReviewArtifactSafetyError("M9 refuses send_authorized export rows")
        if row.get("status") != ExportEligibility.INTERNAL_REVIEW_ONLY.value:
            raise InternalReviewArtifactSafetyError("M9 only renders internal_review_only export rows")
        if row.get("export_target") != INTERNAL_EXPORT_TARGET:
            raise InternalReviewArtifactSafetyError("M9 only renders internal_review_queue exports")
        if row.get("export_schema_version") != INTERNAL_EXPORT_SCHEMA_VERSION:
            raise InternalReviewArtifactSafetyError("M9 export_schema_version mismatch")
    return rows


def _icp_reasons(facts: Sequence[Mapping[str, Any]]) -> list[str]:
    wanted = {
        "permitassist_icp_segment": "ICP segment",
        "low_call_relevance_signal": "Relevance signal",
        "trade_category": "Trade category",
    }
    reasons = [
        f"{wanted[str(row['fact_type'])]}: {row['fact_value']}"
        for row in facts
        if str(row.get("fact_type")) in wanted
    ]
    if not reasons:
        raise InternalReviewArtifactSafetyError("M9 export is missing ICP reason facts")
    return reasons


def _event_reason_codes(row: Mapping[str, Any]) -> list[str]:
    if not row.get("reason_codes"):
        return []
    return _json_list(row.get("reason_codes"), context="verification reason_codes")


def _artifact_for_export(conn: sqlite3.Connection, export: Mapping[str, Any]) -> dict[str, Any]:
    fact_ids = _json_list(export.get("included_fact_ids"), context="included_fact_ids")
    observation_ids = _json_list(export.get("included_source_observation_ids"), context="included_source_observation_ids")
    verification_ids = _json_list(export.get("included_verification_event_ids"), context="included_verification_event_ids")

    entity = _one(
        _load_by_ids(conn, table="entities", id_column="entity_id", ids=[str(export["lead_entity_id"])]),
        context="entity",
    )
    facts = _load_by_ids(conn, table="facts", id_column="fact_id", ids=fact_ids, order_column="fact_type")
    observations = _load_by_ids(
        conn,
        table="source_observations",
        id_column="observation_id",
        ids=observation_ids,
    )
    verification_rows = _load_by_ids(
        conn,
        table="verification_events",
        id_column="verification_event_id",
        ids=verification_ids,
        order_column="verification_event_id",
    )
    suppression = _one(
        _load_by_ids(
            conn,
            table="suppression_events",
            id_column="suppression_event_id",
            ids=[str(export["suppression_event_id"])],
        ),
        context="suppression event",
    )
    enrichment = _one(
        _load_by_ids(
            conn,
            table="enrichment_events",
            id_column="enrichment_event_id",
            ids=[str(export["enrichment_event_id"])],
        ),
        context="enrichment event",
    )

    if any(str(row.get("entity_id")) != str(export["lead_entity_id"]) for row in facts):
        raise InternalReviewArtifactSafetyError("M9 fact lineage entity mismatch")
    observation_id_set = set(observation_ids)
    fact_observation_ids = {str(row.get("source_observation_id")) for row in facts}
    if any(str(row.get("source_observation_id")) not in observation_id_set for row in facts):
        raise InternalReviewArtifactSafetyError("M9 fact source lineage mismatch")
    if fact_observation_ids != observation_id_set:
        raise InternalReviewArtifactSafetyError("M9 source observation lineage includes unreferenced rows")
    lead_entity_id = str(export["lead_entity_id"])
    fact_id_set = set(fact_ids)
    for row in verification_rows:
        target_entity_id = row.get("target_entity_id")
        target_fact_id = row.get("target_fact_id")
        if target_entity_id is not None and str(target_entity_id) != lead_entity_id:
            raise InternalReviewArtifactSafetyError("M9 verification event lineage entity mismatch")
        if target_fact_id is not None and str(target_fact_id) not in fact_id_set:
            raise InternalReviewArtifactSafetyError("M9 verification event lineage fact mismatch")
    if any(not str(row.get("url_or_path", "")).startswith("fixture://") for row in observations):
        raise InternalReviewArtifactSafetyError("M9 only renders fixture:// source lineage")
    if any(int(row.get("network_used_flag") or 0) != 0 for row in verification_rows):
        raise InternalReviewArtifactSafetyError("M9 refuses network-tainted verification events")
    if any(row.get("result_status") != GateStatus.PASS_.value for row in verification_rows):
        raise InternalReviewArtifactSafetyError("M9 refuses non-pass verification events")
    if str(suppression.get("target_entity_id")) != str(export.get("lead_entity_id")):
        raise InternalReviewArtifactSafetyError("M9 suppression lineage entity mismatch")
    if suppression.get("status") != "clear":
        raise InternalReviewArtifactSafetyError("M9 requires clear suppression status for rendered exports")
    if str(enrichment.get("entity_id")) != str(export.get("lead_entity_id")):
        raise InternalReviewArtifactSafetyError("M9 enrichment lineage entity mismatch")
    enrichment_fact_ids = set(_json_list(enrichment.get("input_fact_ids"), context="enrichment input_fact_ids"))
    if enrichment_fact_ids != fact_id_set:
        raise InternalReviewArtifactSafetyError("M9 enrichment fact lineage mismatch")
    enrichment_observation_ids = set(
        _json_list(enrichment.get("input_observation_ids"), context="enrichment input_observation_ids")
    )
    if enrichment_observation_ids != observation_id_set:
        raise InternalReviewArtifactSafetyError("M9 enrichment observation lineage mismatch")
    if enrichment.get("validator_status") != GateStatus.PASS_.value:
        raise InternalReviewArtifactSafetyError("M9 requires pass enrichment status")
    if int(enrichment.get("unsupported_claim_count") or 0) != 0:
        raise InternalReviewArtifactSafetyError("M9 refuses enrichment with unsupported claims")

    enrichment_output = _json_dict(enrichment.get("output_json"), context="enrichment output_json")
    artifact_payload_hash = str(export["signed_payload_hash_sha256"])
    return {
        "artifact_id": _stable_artifact_id(str(export["export_event_id"]), artifact_payload_hash),
        "artifact_use": "boban_titi_review_only",
        "export_event_id": str(export["export_event_id"]),
        "batch_id": str(export["batch_id"]),
        "campaign_id": export.get("campaign_id"),
        "business_label": str(entity["canonical_label"]),
        "business_label_source": "entities.canonical_label",
        "lead_entity_id": str(export["lead_entity_id"]),
        "status": str(export["status"]),
        "internal_review_only": True,
        "send_authorized": False,
        "export_target": str(export["export_target"]),
        "blocked_reason": export.get("blocked_reason"),
        "icp_reasons": _icp_reasons(facts),
        "source_fixture_lineage": [
            {
                "source_observation_id": str(row["observation_id"]),
                "source_id": str(row["source_id"]),
                "url_or_path": str(row["url_or_path"]),
                "snippet_or_excerpt": str(row["snippet_or_excerpt"]),
                "payload_hash_sha256": str(row["payload_hash_sha256"]),
            }
            for row in observations
        ],
        "facts": [
            {
                "fact_id": str(row["fact_id"]),
                "fact_type": str(row["fact_type"]),
                "fact_value": str(row["fact_value"]),
                "source_observation_id": str(row["source_observation_id"]),
                "field_relevant_snippet": str(row["field_relevant_snippet"]),
            }
            for row in facts
        ],
        "suppression": {
            "suppression_event_id": str(suppression["suppression_event_id"]),
            "status": str(suppression["status"]),
            "reason": suppression.get("reason"),
            "checked_at_utc": suppression.get("checked_at_utc"),
        },
        "enrichment": {
            "enrichment_event_id": str(enrichment["enrichment_event_id"]),
            "validator_status": str(enrichment["validator_status"]),
            "unsupported_claim_count": int(enrichment.get("unsupported_claim_count") or 0),
            "summary": enrichment_output.get("summary"),
            "model_or_rule_version": enrichment.get("model_or_rule_version"),
        },
        "verification_event_ids": verification_ids,
        "verification_events": [
            {
                "verification_event_id": str(row["verification_event_id"]),
                "gate_name": str(row["gate_name"]),
                "result_status": str(row["result_status"]),
                "network_used_flag": int(row.get("network_used_flag") or 0),
                "reason_codes": _event_reason_codes(row),
            }
            for row in verification_rows
        ],
        "signed_payload_hash_sha256": artifact_payload_hash,
        "signature": str(export["signature"]),
    }


def _render_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Lead Pipeline M9 Internal Review Artifact",
        "",
        str(payload["banner"]),
        "",
        f"Batch: {payload['batch_id']}",
        f"Artifact schema: {payload['artifact_schema_version']}",
        f"Export events: {payload['export_event_count']}",
        "",
        "Safety boundary:",
        "- local_artifact_only=true",
        "- internal_review_only=true",
        "- send_authorized=false",
        "- no outreach / no CRM / no send",
        "",
    ]
    for export in payload["exports"]:
        lines.extend(
            [
                f"## Export event {export['export_event_id']}",
                "",
                f"Business label: {export['business_label']}",
                f"Lead entity ID: {export['lead_entity_id']}",
                f"Status: {export['status']}",
                f"Export target: {export['export_target']}",
                "send_authorized=false",
                "internal_review_only=true",
                "",
                "ICP reasons:",
            ]
        )
        lines.extend(f"- {reason}" for reason in export["icp_reasons"])
        lines.extend(["", "Source / fixture lineage:"])
        lines.extend(
            f"- {item['source_observation_id']}: {item['url_or_path']} — {item['snippet_or_excerpt']}"
            for item in export["source_fixture_lineage"]
        )
        lines.extend(
            [
                "",
                f"Suppression status: {export['suppression']['status']}",
                f"Suppression event ID: {export['suppression']['suppression_event_id']}",
                f"Enrichment status: {export['enrichment']['validator_status']}",
                f"Enrichment event ID: {export['enrichment']['enrichment_event_id']}",
                f"Enrichment unsupported claim count: {export['enrichment']['unsupported_claim_count']}",
                "",
                "Verification event IDs:",
            ]
        )
        lines.extend(f"- {event_id}" for event_id in export["verification_event_ids"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_internal_review_artifacts(conn: sqlite3.Connection, *, batch_id: str) -> InternalReviewArtifact:
    """Render deterministic local JSON + Markdown artifacts for M8 export rows."""

    exports = [_artifact_for_export(conn, row) for row in _export_rows(conn, batch_id=batch_id)]
    payload: dict[str, Any] = {
        "artifact_schema_version": PHASE1_M9_ARTIFACT_SCHEMA_VERSION,
        "banner": INTERNAL_REVIEW_BANNER,
        "batch_id": batch_id,
        "safety": {
            "local_artifact_only": True,
            "internal_review_only": True,
            "network_used": False,
            "outreach_attempted": False,
            "crm_sync_attempted": False,
            "send_authorized": False,
        },
        "export_event_count": len(exports),
        "exports": exports,
    }
    return InternalReviewArtifact(payload=payload, markdown=_render_markdown(payload))


def write_internal_review_artifacts(
    conn: sqlite3.Connection,
    *,
    output_dir: str | Path,
    batch_id: str,
) -> WrittenInternalReviewArtifacts:
    """Write M9 JSON + Markdown artifacts to a local directory only."""

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    artifact = render_internal_review_artifacts(conn, batch_id=batch_id)
    json_path = output_path / JSON_ARTIFACT_FILENAME
    markdown_path = output_path / MARKDOWN_ARTIFACT_FILENAME
    json_path.write_text(artifact.json_text, encoding="utf-8")
    markdown_path.write_text(artifact.markdown, encoding="utf-8")
    return WrittenInternalReviewArtifacts(artifact=artifact, json_path=json_path, markdown_path=markdown_path)
