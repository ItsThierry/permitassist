#!/usr/bin/env python3
"""Build the Phase 7B Golden Evidence local/internal preview pack.

This script transforms the approved Phase 7B Golden Evidence artifact into the
small runtime field-record format used by the local fail-closed evidence overlay.
It deliberately includes only customer-safe promoted/caveated core fields and
never promotes unsupported fee/timeline/forms/inspection/trade/companion/specialty
claims.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path("/home/boban/.hermes/project-briefs/artifacts/permitassist-phase7/PA7B-HYBRID10-20260511/golden-evidence-pack-third-pass.json")
DEFAULT_OUTPUT = REPO_ROOT / "data" / "evidence_packs" / "local_golden" / "phase7b" / "permitassist-phase7b-golden-local-preview-pack-PA7B-HYBRID10-20260511.json"
sys.path.insert(0, str(REPO_ROOT / "api"))
from evidence_pack_runtime import canonical_json_sha256  # noqa: E402

BATCH_ID = "PA7B-HYBRID10-20260511"
SOURCE_ARTIFACT = "golden-evidence-pack-third-pass.json"
SOURCE_SHA256 = "91e0589edbf17aae15f2dc21f3734c333e9d4f02debe0e1575f7117f884753f9"
SOURCE_SCHEMA_NAME = "permitassist_golden_evidence_record_v1"
MODE = "phase7b_golden_local_preview"
EVIDENCE_PACK_VERSION = "phase7b_golden_local_preview_v1"
LOCKED_VERTICALS = ("medical_clinic_ti", "office_ti", "restaurant_ti")
PROMOTED_STATUSES = {"promoted_source_backed", "promoted_caveated_source_backed"}
ROW_READINESS_BY_SOURCE = {
    "customer-ready": "customer_ready",
    "customer-ready-with-minor-caveat": "customer_ready_with_minor_caveat",
    "safe-with-caveat": "safe_with_caveat",
    "internal-only": "internal_only",
}
FIELD_CERTAINTY_BY_STATUS = {
    "promoted_source_backed": "promoted_clean",
    "promoted_caveated_source_backed": "promoted_caveated",
    "verified_not_published_or_not_found_on_official_sources": "not_published_on_official_sources",
    "not_researched_enough": "not_researched_enough",
    "rejected": "rejected",
}
CUSTOMER_SURFACE_POLICY_BY_CERTAINTY = {
    "promoted_clean": "show_as_fact",
    "promoted_caveated": "show_with_caveat",
    "not_published_on_official_sources": "suppress_but_log_internal",
    "not_researched_enough": "suppress_but_log_internal",
    "rejected": "never_show",
}
FIELD_MAP = {
    "department_portal_application_method": "apply_url",
    "permit_type_name": "permit_type",
}
SUPPRESSED_SOURCE_FIELDS = {
    "fees",
    "timelines",
    "forms_checklists",
    "inspections",
    "companion_permits_reviews",
    "specialty_vertical_triggers",
    "trade_permit_obligations",
}
EXACT_PERMIT_NAME_TEMPLATE_FIELDS = (
    "display_permit_name",
    "official_permit_name",
    "official_application_category",
    "display_source_field",
    "name_source_precedence",
    "residential_project_type",
    "trade_permit_names",
    "septic_or_sewer_review",
    "customer_visibility_tier",
)
PERMIT_NAME_STATUS_ENUM = (
    "exact_official_name_confirmed",
    "official_category_confirmed_exact_label_missing",
    "no_evidence_found_after_completed_search",
    "needs_research",
    "conflict_needs_human_review",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_hash(value: Any) -> str:
    return canonical_json_sha256(value)


def _is_http_url(value: Any) -> bool:
    return urlparse(str(value or "")).scheme in {"http", "https"}


def _source_title(row: dict[str, Any], url: str) -> str:
    for source in row.get("official_evidence_sources") or []:
        if isinstance(source, dict) and source.get("url") == url and source.get("title"):
            return str(source["title"])
    return "Official AHJ source"


def _first_evidence(field: dict[str, Any], row: dict[str, Any]) -> dict[str, Any] | None:
    last_verified_date = str(field.get("last_verified_date") or "").strip()
    if not last_verified_date:
        raise ValueError(f"promoted field missing last_verified_date: {row.get('row_id')}")
    for snippet in field.get("evidence_snippets") or []:
        if not isinstance(snippet, dict):
            continue
        url = str(snippet.get("url") or "")
        quote = str(snippet.get("snippet") or "").strip()
        if _is_http_url(url) and quote:
            return {
                "source_url": url,
                "source_title": _source_title(row, url),
                "exact_quote_or_snippet": quote,
                "quote_found": True,
                "last_verified_utc": f"{last_verified_date}T00:00:00Z",
                "reverify_after_utc": "2027-04-12T00:00:00Z",
                "stale_after_utc": "2027-05-12T00:00:00Z",
            }
    return None


def _transform_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    if row.get("batch_id") != BATCH_ID:
        raise ValueError(f"unexpected batch_id for {row.get('row_id')}")
    if row.get("record_schema") != SOURCE_SCHEMA_NAME:
        raise ValueError(f"unexpected source schema for {row.get('row_id')}")
    if row.get("vertical_slug") not in LOCKED_VERTICALS:
        raise ValueError(f"unexpected vertical for {row.get('row_id')}")

    for source_field in SUPPRESSED_SOURCE_FIELDS:
        status = ((row.get("fields") or {}).get(source_field) or {}).get("status")
        if status != "verified_not_published_or_not_found_on_official_sources":
            raise ValueError(f"unsupported field unexpectedly promoted: {row.get('row_id')} {source_field} {status}")

    records: list[dict[str, Any]] = []
    for source_field, runtime_field in FIELD_MAP.items():
        field = (row.get("fields") or {}).get(source_field) or {}
        status = field.get("status")
        if status not in PROMOTED_STATUSES:
            continue
        value = field.get("value")
        value_text = str(value or "").strip()
        if not value_text:
            raise ValueError(f"promoted field has empty claim value: {row.get('row_id')} {source_field}")
        if runtime_field == "apply_url" and not _is_http_url(value):
            raise ValueError(f"promoted apply_url is not a valid http/https URL: {row.get('row_id')}")
        evidence = _first_evidence(field, row)
        if not evidence:
            continue
        caveat = str(field.get("customer_facing_caveat") or "").strip()
        row_caveat = ""
        if row.get("row_level_classification") == "safe-with-caveat":
            row_caveat = str(((row.get("source_addendum") or {}).get("strengthened_customer_caveat") or caveat)).strip()
        row_readiness = ROW_READINESS_BY_SOURCE.get(str(row.get("row_level_classification") or ""), "internal_only")
        field_certainty = FIELD_CERTAINTY_BY_STATUS[status]
        customer_surface_policy = CUSTOMER_SURFACE_POLICY_BY_CERTAINTY[field_certainty]
        caveat_type = "none"
        if caveat and row_caveat:
            caveat_type = "row_and_field_caveat"
        elif row_caveat:
            caveat_type = "row_caveat"
        elif caveat:
            caveat_type = "field_caveat"
        caveat_text = row_caveat or caveat
        last_verified_date = str(field.get("last_verified_date") or "").strip()
        next_reverification_due = "2027-04-12"
        stale_after_date = "2027-05-12"
        record = {
            "record_id": f"phase7b-golden::{row['row_id']}::{runtime_field}",
            "state": row["state"],
            "ahj_name": row["ahj"],
            "city": row["city"],
            "vertical": row["vertical_slug"],
            "field": runtime_field,
            "claim_value": value,
            "field_status": "verified" if status == "promoted_source_backed" else "partial",
            "confidence": "high" if status == "promoted_source_backed" else "medium",
            "ingestion_ready": True,
            "source_scope_limit": caveat or row_caveat or "Official-source-backed field; verify the exact filing path with the AHJ before filing.",
            "customer_facing_caveat": caveat,
            "row_level_caveat": row_caveat,
            "caveat_type": caveat_type,
            "caveat_text": caveat_text,
            "row_readiness": row_readiness,
            "field_certainty": field_certainty,
            "customer_surface_policy": customer_surface_policy,
            "source_retrieval_method": "locked_local_artifact_transform",
            "source_snapshot_ref": f"{SOURCE_ARTIFACT}#{row['row_id']}::{source_field}",
            "source_golden_field": source_field,
            "source_golden_field_status": status,
            "golden_row_id": row["row_id"],
            "golden_row_status": row.get("golden_row_status"),
            "row_level_classification": row.get("row_level_classification"),
            "batch_id": row.get("batch_id"),
            "record_schema": "permitassist.phase7b_golden_local_runtime_record.v1",
            "field_evidence": [{
                **evidence,
                "field": runtime_field,
                "source_retrieval_method": "locked_local_artifact_transform",
                "source_snapshot_ref": f"{SOURCE_ARTIFACT}#{row['row_id']}::{source_field}",
            }],
            "last_verified_date": last_verified_date,
            "next_reverification_due": next_reverification_due,
            "stale_after_date": stale_after_date,
            "last_verified_utc": evidence["last_verified_utc"],
            "reverify_after_utc": evidence["reverify_after_utc"],
            "stale_after_utc": evidence["stale_after_utc"],
        }
        record["record_fingerprint_sha256"] = _canonical_hash({k: v for k, v in record.items() if k != "record_fingerprint_sha256"})
        records.append(record)
    return records


def _pack_fingerprint(pack: dict[str, Any]) -> str:
    metadata = dict(pack.get("metadata") or {})
    metadata.pop("fingerprint_sha256", None)
    return _canonical_hash({"metadata": metadata, "records": pack.get("records") or []})


def _validate_exact_runtime_records(records: list[dict[str, Any]]) -> None:
    pairs = [(str(r.get("golden_row_id") or ""), str(r.get("field") or "")) for r in records]
    if len(records) != 60 or len(set(pairs)) != 60:
        raise ValueError("local golden runtime contract requires exactly 60 unique row/field records")
    by_row: dict[str, list[str]] = {}
    for record in records:
        row_id = str(record.get("golden_row_id") or "")
        field = str(record.get("field") or "")
        by_row.setdefault(row_id, []).append(field)
        value = str(record.get("claim_value") or "").strip()
        if field == "permit_type" and not value:
            raise ValueError(f"permit_type cannot be empty for {row_id}")
        if field == "apply_url" and not _is_http_url(value):
            raise ValueError(f"apply_url must be a valid http/https URL for {row_id}")
    if len(by_row) != 30:
        raise ValueError("local golden runtime contract requires exactly 30 row IDs")
    bad_rows = {row_id: fields for row_id, fields in by_row.items() if sorted(fields) != ["apply_url", "permit_type"]}
    if bad_rows:
        raise ValueError(f"local golden runtime contract requires one permit_type and one apply_url per row: {sorted(bad_rows)[:3]}")


def _internal_unpromoted_field_reasons(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reasons: list[dict[str, Any]] = []
    for row in rows:
        for source_field in sorted(SUPPRESSED_SOURCE_FIELDS):
            field = ((row.get("fields") or {}).get(source_field) or {})
            status = str(field.get("status") or "").strip()
            certainty = FIELD_CERTAINTY_BY_STATUS.get(status, "not_researched_enough")
            reasons.append({
                "golden_row_id": row.get("row_id"),
                "source_field": source_field,
                "field_certainty": certainty,
                "customer_surface_policy": CUSTOMER_SURFACE_POLICY_BY_CERTAINTY.get(certainty, "suppress_but_log_internal"),
                "reason": "not promoted for customer output because Phase 7C only treats permit_type/apply_url as official-source-backed; unsupported fields remain unavailable unless separately source-confirmed.",
            })
    return reasons


def build_pack(source_path: Path) -> dict[str, Any]:
    raw = source_path.read_bytes()
    if _sha256(raw) != SOURCE_SHA256:
        raise ValueError("source artifact SHA does not match the approved Phase 7B Golden artifact")
    source = json.loads(raw.decode("utf-8"))
    rows = source.get("records") or []
    if source.get("artifact") != SOURCE_ARTIFACT or source.get("batch_id") != BATCH_ID:
        raise ValueError("source artifact identity mismatch")
    if ((source.get("schema") or {}).get("name")) != SOURCE_SCHEMA_NAME:
        raise ValueError("source schema mismatch")
    if len(rows) != 30:
        raise ValueError("source row count mismatch")

    records = []
    for row in rows:
        records.extend(_transform_row(row))
    _validate_exact_runtime_records(records)
    row_counts = Counter(row.get("row_level_classification") for row in rows)
    metadata = {
        "evidence_pack_version": EVIDENCE_PACK_VERSION,
        "fingerprint_sha256": "0" * 64,
        "production_wiring_allowed": False,
        "mode": MODE,
        "eligibility": "local_internal_preview_only",
        "batch_id": BATCH_ID,
        "source_artifact": SOURCE_ARTIFACT,
        "source_artifact_sha256": SOURCE_SHA256,
        "source_schema_name": SOURCE_SCHEMA_NAME,
        "golden_row_count": 30,
        "transformed_record_count": len(records),
        "locked_verticals": list(LOCKED_VERTICALS),
        "row_level_classification_counts": dict(sorted(row_counts.items())),
        "row_readiness_enum": ["customer_ready", "customer_ready_with_minor_caveat", "safe_with_caveat", "internal_only"],
        "field_certainty_enum": ["promoted_clean", "promoted_caveated", "not_published_on_official_sources", "not_researched_enough", "rejected"],
        "caveat_type_enum": ["none", "field_caveat", "row_caveat", "row_and_field_caveat"],
        "customer_surface_policy_enum": ["show_as_fact", "show_with_caveat", "suppress_but_log_internal", "never_show"],
        "exact_permit_name_template_fields": list(EXACT_PERMIT_NAME_TEMPLATE_FIELDS),
        "name_source_precedence": ["display_permit_name", "official_permit_name", "official_application_category"],
        "permit_name_status_enum": list(PERMIT_NAME_STATUS_ENUM),
        "customer_visibility_tier_enum": ["customer_ready", "show_with_caveat", "internal_only", "never_show"],
        "residential_commercial_expansion_ready": True,
        "suppressed_source_fields": sorted(SUPPRESSED_SOURCE_FIELDS),
        "internal_unpromoted_field_reasons": _internal_unpromoted_field_reasons(rows),
        "generated_from": SOURCE_ARTIFACT,
        "contract": "phase7b_golden_local_preview_internal_only_v1",
    }
    pack = {
        "metadata": metadata,
        "validation": {"verdict": "LOCAL_INTERNAL_PREVIEW_ONLY", "public_activation_allowed": False},
        "records": records,
    }
    pack["metadata"]["fingerprint_sha256"] = _pack_fingerprint(pack)
    return pack


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail if output differs")
    args = parser.parse_args()

    pack = build_pack(args.source)
    output_text = json.dumps(pack, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.check:
        current = args.output.read_text(encoding="utf-8")
        if current != output_text:
            raise SystemExit("generated local golden pack differs from checked-in output")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output_text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
