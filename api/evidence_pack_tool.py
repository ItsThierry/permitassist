"""Offline Step 7B Evidence Pack Tool for PermitAssist.

This module intentionally does **not** wire Step 6A evidence into production
lookup. It loads accepted Road-to-Perfection Step 6A artifacts, normalizes them
into a field-level evidence-pack shape, validates ingestion readiness, and emits
version/fingerprint/fail-closed metadata for a future Step 7C import.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable
from urllib.parse import urlparse

EVIDENCE_PACK_VERSION = "step7b_offline_v1"
DEFAULT_PROJECT_BRIEFS_DIR = Path("/home/boban/.hermes/project-briefs")
DEFAULT_OUTPUT_DIR = Path("artifacts/step7b_evidence_pack")
SUPPORTED_FIELDS = {
    "permit_type",
    "apply_url",
    "fee_range",
    "approval_timeline",
    "inspections",
    "companion_reviews_triggers",
}
STATUS_TO_CONFIDENCE = {
    "verified": "high",
    "partial": "medium",
    "needs_verification": "needs_verification",
}
REVERIFY_AFTER_DAYS = 30
STALE_AFTER_DAYS = 60


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _format_utc(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _artifact_sort_key(path: Path) -> tuple[int, str]:
    name = path.name
    match = re.search(r"corrected-v(\d+)", name)
    priority = 1 + int(match.group(1)) if match else 0
    return priority, name


def discover_step6a_artifacts(project_briefs_dir: str | Path = DEFAULT_PROJECT_BRIEFS_DIR) -> list[Path]:
    """Return accepted Step 6A evidence-upgrade artifacts, de-duplicated by batch.

    Review/self/source-validation files are ignored. Corrected artifacts are
    preferred over superseded originals. The final 23-gap surgical pass is
    appended separately when present.
    """
    root = Path(project_briefs_dir)
    by_batch: dict[int, Path] = {}
    pattern = "permitassist-road-to-perfection-step6a-batch*-evidence-upgrades*.json"
    batch_re = re.compile(r"step6a-batch(\d+)-")
    for path in sorted(root.glob(pattern)):
        if not re.fullmatch(r"permitassist-road-to-perfection-step6a-batch\d+-evidence-upgrades(?:-corrected-v\d+)?-\d{4}-\d{2}-\d{2}\.json", path.name):
            continue
        match = batch_re.search(path.name)
        if not match:
            continue
        batch = int(match.group(1))
        current = by_batch.get(batch)
        if current is None or _artifact_sort_key(path) > _artifact_sort_key(current):
            by_batch[batch] = path

    discovered = [by_batch[k] for k in sorted(by_batch)]
    surgical = root / "permitassist-road-to-perfection-step6a-23-gap-surgical-pass-2026-05-05.json"
    if surgical.exists():
        discovered.append(surgical)
    return discovered


def fail_closed_decision(fetch_status: Any) -> dict[str, Any]:
    """Describe the Step 7B/7C fail-closed policy for source runtime checks."""
    status = str(fetch_status or "").lower()
    transient_or_blocked = any(marker in status for marker in ("403", "404", "500", "502", "503", "blocked", "cloudflare", "access_denied", "timeout"))
    browser_rendered_success = "browser_rendered_200" in status
    if not status:
        action = "downgrade_to_needs_verification_until_revalidated"
    elif transient_or_blocked and not browser_rendered_success:
        action = "downgrade_to_needs_verification_until_revalidated"
    elif transient_or_blocked and browser_rendered_success:
        action = "use_existing_evidence_only_if_not_expired_and_display_stale_warning"
    else:
        action = "keep_current_status_if_quote_hash_still_matches"
    return {
        "fetch_status": fetch_status,
        "runtime_action": action,
        "must_not_promote": True,
        "production_import_note": "If current revalidation cannot confirm the exact quote/hash within freshness policy, Step 7C must downgrade the field to needs_verification and show a visible warning.",
    }


def _extract_quote(evidence: dict[str, Any]) -> tuple[str, bool]:
    quote = str(evidence.get("exact_quote") or evidence.get("quoted_snippet") or "").strip()
    quote_found = evidence.get("quote_found_current_run")
    if quote:
        return quote, bool(True if quote_found is None else quote_found)

    snippets = evidence.get("exact_snippets") or []
    parts: list[str] = []
    found = False
    if isinstance(snippets, list):
        for item in snippets:
            if isinstance(item, dict):
                text = str(item.get("exact_quote") or item.get("quoted_snippet") or "").strip()
                check = str(item.get("quote_check") or "").lower()
                if text:
                    parts.append(text)
                    found = found or check in {"pass", "true", "found"}
            elif isinstance(item, str) and item.strip():
                parts.append(item.strip())
                found = True
    joined = "\n---\n".join(parts)
    return joined, bool(found and joined)


def _normalized_length(evidence: dict[str, Any]) -> int | None:
    for key in (
        "normalized_source_text_length",
        "normalized_rendered_text_length",
        "normalized_text_length",
        "normalized_rendered_text_len",
        "normalized_text_len",
        "source_content_length",
        "content_length",
        "browser_rendered_text_length",
    ):
        value = evidence.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _normalized_hash(evidence: dict[str, Any]) -> str:
    value = (
        evidence.get("normalized_source_text_sha256")
        or evidence.get("normalized_rendered_text_sha256")
        or evidence.get("normalized_text_sha256")
        or evidence.get("source_content_sha256")
        or evidence.get("content_sha256")
        or evidence.get("browser_rendered_text_sha256")
        or ""
    )
    return str(value).strip()


def _source_title(evidence: dict[str, Any]) -> str:
    return str(evidence.get("source_title") or evidence.get("browser_title") or evidence.get("title") or evidence.get("source_host") or "").strip()


def _source_url(evidence: dict[str, Any]) -> str:
    return str(evidence.get("source_url") or evidence.get("source_final_url") or evidence.get("final_url") or evidence.get("url") or evidence.get("linked_pdf_url") or "").strip()


def _validate_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _review_accepts_artifact(review: dict[str, Any]) -> bool:
    """Return true only for clean independent-review PASS shapes.

    Step 6A artifacts were often written before the independent reviewer updated
    the original artifact's status. Step 7B may reconcile that companion review,
    but it must not treat PASS_WITH_BLOCKERS or blocker-bearing reviews as
    accepted.
    """
    status = str(
        review.get("verdict")
        or review.get("status")
        or review.get("review_status")
        or review.get("overall_verdict")
        or ""
    ).upper()
    blockers = review.get("blockers")
    has_blockers = bool(blockers) if isinstance(blockers, (list, tuple, dict)) else bool(str(blockers or "").strip())
    if has_blockers or "BLOCKER" in status or "FAIL" in status:
        return False
    if review.get("accepted") is True:
        return True
    return "PASS" in status


def _independent_review_candidates(artifact_path: Path) -> list[Path]:
    batch_match = re.search(r"step6a-batch(\d+)-", artifact_path.name)
    if not batch_match:
        return []
    batch = batch_match.group(1)
    patterns = [
        f"permitassist-road-to-perfection-step6a-batch{batch}-*independent-review*.json",
        f"permitassist-road-to-perfection-step6a-batch{batch}-independent-review*.json",
    ]
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(sorted(artifact_path.parent.glob(pattern)))
    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in seen and candidate.exists():
            unique.append(candidate)
            seen.add(candidate)
    return unique


def _artifact_is_independently_accepted(artifact: dict[str, Any], artifact_path: Path | None = None) -> bool:
    status = str(artifact.get("status") or "").upper()
    if "PASS_ACCEPTED" in status:
        return True
    # Older zero-row artifacts sometimes omitted status; they cannot promote rows.
    if not artifact.get("accepted_upgrades"):
        return True
    if artifact_path is not None:
        for review_path in _independent_review_candidates(artifact_path):
            try:
                if _review_accepts_artifact(_read_json(review_path)):
                    return True
            except Exception:
                continue
    return False


def _row_evidence_items(row: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = row.get("evidence") or []
    if isinstance(evidence, list) and evidence:
        return [item for item in evidence if isinstance(item, dict)]

    nested_source = row.get("source") if isinstance(row.get("source"), dict) else {}
    legacy_quote = row.get("exact_official_quote") or row.get("exact_quote") or row.get("quoted_snippet")
    legacy_snippets = row.get("exact_snippets") or row.get("additional_exact_snippets") or nested_source.get("exact_snippets") or []
    legacy_url = row.get("source_url") or row.get("source_final_url") or nested_source.get("source_url") or nested_source.get("source_final_url") or nested_source.get("url")
    if legacy_quote or legacy_url or legacy_snippets:
        return [
            {
                "source_id": row.get("source_id") or nested_source.get("source_id") or "legacy_flat_row_source",
                "source_url": legacy_url or "",
                "source_final_url": row.get("source_final_url") or nested_source.get("source_final_url") or legacy_url or "",
                "source_host": row.get("source_host") or nested_source.get("source_host") or "",
                "source_title": row.get("source_title") or row.get("browser_title") or nested_source.get("source_title") or nested_source.get("browser_title") or "",
                "source_type": row.get("source_type") or nested_source.get("source_type") or "unknown",
                "exact_quote": legacy_quote or "",
                "exact_snippets": legacy_snippets,
                "quote_found_current_run": row.get("quote_found_current_run", row.get("quote_found", True if legacy_quote or legacy_snippets else False)),
                "normalized_source_text_sha256": row.get("normalized_source_text_sha256") or row.get("normalized_rendered_text_sha256") or row.get("source_text_sha256") or nested_source.get("normalized_source_text_sha256") or nested_source.get("normalized_text_sha256") or nested_source.get("content_sha256") or "",
                "normalized_source_text_length": row.get("normalized_source_text_length") or row.get("normalized_rendered_text_length") or nested_source.get("normalized_source_text_length") or nested_source.get("normalized_text_length"),
                "validation_method": row.get("validation_method") or row.get("verification_method") or nested_source.get("validation_method") or nested_source.get("fetched_via") or "legacy flat Step 6A row adapted by Step 7B offline tool",
                "fetch_status_code": row.get("fetch_status_code") or nested_source.get("fetch_status_code") or nested_source.get("status_code") or ("validated_current_run" if nested_source.get("normalized_text_sha256") and nested_source.get("normalized_text_length") else None),
            }
        ]
    return []




def _source_validation_candidates(artifact_path: Path) -> list[Path]:
    """Return likely companion source-validation artifacts for a Step 6A upgrade artifact."""
    name = artifact_path.name
    candidates: list[Path] = []
    batch_match = re.search(r"(step6a-batch\d+)-", name)
    if batch_match:
        prefix = f"permitassist-road-to-perfection-{batch_match.group(1)}"
        candidates.extend(sorted(artifact_path.parent.glob(f"{prefix}-source-validation*.json")))
    if "step6a-23-gap-surgical-pass" in name:
        candidates.extend(sorted(artifact_path.parent.glob("permitassist-road-to-perfection-step6a-23-gap-surgical-pass-source-validation*.json")))
    # Preserve order while de-duping.
    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in seen and candidate.exists():
            unique.append(candidate)
            seen.add(candidate)
    return unique


def _source_validation_items(artifact_path: Path) -> dict[str, dict[str, Any]]:
    """Index companion source-validation metadata by source_id and source_url.

    Step 6A artifacts evolved over many batches. Some rows kept only a source_id
    or URL while the companion source-validation artifact held normalized text
    length/hash, exact snippets, title, and current fetch status. This index lets
    Step 7B backfill those metadata fields without re-fetching the web or
    promoting unsupported claims.
    """
    indexed: dict[str, dict[str, Any]] = {}
    for candidate in _source_validation_candidates(artifact_path):
        try:
            payload = _read_json(candidate)
        except Exception:
            continue
        sources = payload.get("sources") or payload.get("sources_checked") or []
        if isinstance(sources, dict):
            source_iter = []
            for source_id, source in sources.items():
                if isinstance(source, dict):
                    enriched = dict(source)
                    enriched.setdefault("source_id", source_id)
                    source_iter.append(enriched)
        elif isinstance(sources, list) and sources:
            source_iter = [source for source in sources if isinstance(source, dict)]
        elif any(payload.get(key) for key in ("source_url", "final_url", "url")):
            source_iter = [payload]
        else:
            source_iter = []
        for source in source_iter:
            for key in (
                source.get("source_id"),
                source.get("source_url"),
                source.get("source_final_url"),
                source.get("final_url"),
                source.get("url"),
                source.get("linked_pdf_url"),
            ):
                if key:
                    indexed[str(key).strip()] = source
    return indexed


def _source_validation_match(evidence: dict[str, Any], validation_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for key in (evidence.get("source_id"), evidence.get("source_url"), evidence.get("source_final_url")):
        if key and str(key).strip() in validation_index:
            return validation_index[str(key).strip()]
    return {}


def _metadata_value(evidence: dict[str, Any], validation: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = evidence.get(key)
        if value not in (None, "", []):
            return value
    for key in keys:
        value = validation.get(key)
        if value not in (None, "", []):
            return value
    return None


def _fallback_source_scope_limit(row: dict[str, Any], field: str) -> str:
    """Generate a conservative display scope when old accepted artifacts omitted one.

    This is intentionally narrow: it can remove a packaging blocker, but it does
    not promote fields beyond the specific cited field and still makes every other
    core field off-limits for runtime reuse.
    """
    if not field:
        return ""
    labels = {
        "permit_type": "permit type",
        "apply_url": "apply URL/path",
        "fee_range": "fee range",
        "approval_timeline": "approval timeline",
        "inspections": "inspection guidance",
        "companion_reviews_triggers": "companion review trigger guidance",
    }
    field_label = labels.get(field, field.replace("_", " "))
    other = [label for key, label in labels.items() if key != field]
    vertical = str(row.get("vertical") or "this vertical").replace("_", " ")
    return (
        f"Step 7B generated conservative scope: use only for {field_label} evidence for {vertical} "
        f"at this AHJ/state as supported by the cited field-level quote. Does not verify "
        f"{', '.join(other[:-1])}, or {other[-1]}. Does not verify unrelated AHJs, unrelated verticals, "
        "fees/timelines/apply paths unless this record's own field is that field."
    )


def _display_contract(scope_limit: str, field: str) -> dict[str, Any]:
    scope = scope_limit.lower()
    aliases = {
        "fee_range": ("fee_range", "fee range", "fees", "fee schedule"),
        "approval_timeline": ("approval_timeline", "approval timeline", "timelines", "timeline"),
        "apply_url": ("apply_url", "apply url", "apply URL".lower()),
        "permit_type": ("permit_type", "permit type"),
        "inspections": ("inspections", "inspection sequence"),
        "companion_reviews_triggers": ("companion reviews", "companion_reviews_triggers", "routing"),
    }
    forbidden: list[str] = []
    for candidate, terms in aliases.items():
        if candidate == field:
            continue
        if any(
            f"does not verify {term}" in scope
            or f"no {term}" in scope
            or re.search(rf"\b(?:no|does not verify)\b[^.]*\b{re.escape(term)}\b", scope)
            for term in terms
        ):
            forbidden.append(candidate)
    return {
        "must_display_scope_limit": bool(scope_limit),
        "forbids_local_ahj_certainty": bool(scope_limit) and any(marker in scope for marker in ("no local", "statewide", "conditional")),
        "must_not_use_for_fields": sorted(set(forbidden)),
    }


def _generated_row_id(row: dict[str, Any], artifact: dict[str, Any], index: int) -> str:
    parts = [
        str(artifact.get("batch_id") or artifact.get("title") or "artifact"),
        str(row.get("state") or "unknown"),
        str(row.get("ahj_name") or "unknown-ahj"),
        str(row.get("vertical") or "unknown-vertical"),
        str(row.get("field") or "unknown-field"),
        str(index),
    ]
    slug = re.sub(r"[^a-z0-9]+", "-", "-".join(parts).lower()).strip("-")
    return slug[:160]


def _normalize_record(row: dict[str, Any], artifact: dict[str, Any], artifact_path: Path, index: int) -> dict[str, Any]:
    proposed_status = str(row.get("proposed_status") or row.get("status_after") or row.get("field_status") or "needs_verification").lower()
    if proposed_status not in STATUS_TO_CONFIDENCE:
        proposed_status = "needs_verification"

    checked_at = row.get("checked_at_utc") or artifact.get("accepted_utc") or artifact.get("updated_utc") or artifact.get("created_utc")
    checked_dt = _parse_utc(str(checked_at) if checked_at else None)
    reverify_after = checked_dt + timedelta(days=REVERIFY_AFTER_DAYS) if checked_dt else None
    stale_after = checked_dt + timedelta(days=STALE_AFTER_DAYS) if checked_dt else None

    validation_errors: list[str] = []
    if not _artifact_is_independently_accepted(artifact, artifact_path):
        validation_errors.append("artifact_not_independently_accepted")

    source_validation_index = _source_validation_items(artifact_path)
    evidence_items = []
    for e_index, evidence in enumerate(_row_evidence_items(row), 1):
        if not isinstance(evidence, dict):
            validation_errors.append("non_object_evidence_item")
            continue
        source_validation = _source_validation_match(evidence, source_validation_index)
        merged_evidence = dict(source_validation)
        merged_evidence.update({k: v for k, v in evidence.items() if v not in (None, "", [])})
        quote, quote_found = _extract_quote(merged_evidence)
        url = _source_url(merged_evidence)
        norm_hash = _normalized_hash(merged_evidence) or str(_metadata_value(merged_evidence, source_validation, "normalized_text_sha256", "content_sha256") or "").strip()
        norm_len = _normalized_length(merged_evidence)
        if norm_len is None:
            for key in ("normalized_text_length", "normalized_rendered_text_len", "normalized_text_len"):
                value = _metadata_value(merged_evidence, source_validation, key)
                if isinstance(value, int):
                    norm_len = value
                    break
                if isinstance(value, str) and value.isdigit():
                    norm_len = int(value)
                    break
        fetch_status = _metadata_value(merged_evidence, source_validation, "fetch_status_code", "http_status", "status_code", "status")
        if fetch_status is None and norm_hash and norm_len and quote_found:
            fetch_status = "validated_current_run"
        source_title = _source_title(merged_evidence) or str(_metadata_value(merged_evidence, source_validation, "title") or "").strip()
        item_errors: list[str] = []
        if not url or not _validate_url(url):
            item_errors.append("invalid_or_missing_source_url")
        if not quote:
            item_errors.append("missing_field_level_quote_or_snippet")
        if not quote_found:
            item_errors.append("quote_not_found_current_run")
        if not norm_hash or not re.fullmatch(r"[0-9a-fA-F]{64}", norm_hash):
            item_errors.append("missing_or_invalid_normalized_source_text_sha256")
        if norm_len is None or norm_len <= 0:
            item_errors.append("missing_normalized_source_text_length")
        if fetch_status is None:
            item_errors.append("missing_fetch_status_code")
        if not source_title:
            item_errors.append("missing_source_title")
        validation_errors.extend(item_errors)
        evidence_items.append({
            "source_id": merged_evidence.get("source_id") or f"source_{e_index}",
            "source_url": url,
            "source_title": source_title,
            "source_type": merged_evidence.get("source_type") or "unknown",
            "source_host": merged_evidence.get("source_host") or urlparse(url).netloc,
            "exact_quote_or_snippet": quote,
            "quote_found": quote_found,
            "normalized_source_text_sha256": norm_hash,
            "normalized_source_text_length": norm_len,
            "last_verified_utc": _format_utc(checked_dt),
            "reverify_after_utc": _format_utc(reverify_after),
            "stale_after_utc": _format_utc(stale_after),
            "validation_method": merged_evidence.get("validation_method") or merged_evidence.get("verification_method") or merged_evidence.get("fetched_via") or "",
            "fetch_status_code": fetch_status,
            "runtime_fail_closed_policy": fail_closed_decision(fetch_status),
            "validation_errors": item_errors,
        })

    field = str(row.get("field") or "").strip()
    source_scope_was_generated = False
    scope_limit = str(row.get("source_scope_limit") or row.get("limits_and_caveats") or "").strip()
    if not scope_limit:
        scope_limit = _fallback_source_scope_limit(row, field)
        source_scope_was_generated = bool(scope_limit)
    if not scope_limit:
        validation_errors.append("missing_source_scope_limit")
    if field not in SUPPORTED_FIELDS:
        validation_errors.append("unsupported_or_missing_field")
    if not evidence_items:
        validation_errors.append("missing_field_level_evidence")
    if not checked_dt:
        validation_errors.append("missing_or_invalid_checked_at_utc")

    ingestion_ready = not validation_errors
    field_status = proposed_status if ingestion_ready else "needs_verification"
    confidence = STATUS_TO_CONFIDENCE[field_status]
    record = {
        "record_id": row.get("row_id") or _generated_row_id(row, artifact, index),
        "source_artifact": artifact_path.name,
        "source_artifact_path": str(artifact_path),
        "source_batch_id": artifact.get("batch_id") or artifact.get("title") or artifact_path.stem,
        "state": row.get("state") or "",
        "ahj_name": row.get("ahj_name") or "",
        "vertical": row.get("vertical") or "",
        "field": field,
        "old_status": row.get("old_status") or "",
        "proposed_status_from_artifact": proposed_status,
        "field_status": field_status,
        "confidence": confidence,
        "claim_value": row.get("claim_value_after") or row.get("claim_value") or "",
        "source_scope_limit": scope_limit,
        "source_scope_limit_generated": source_scope_was_generated,
        "display_contract": _display_contract(scope_limit, field),
        "field_evidence": evidence_items,
        "freshness": {
            "last_verified_utc": _format_utc(checked_dt),
            "reverify_after_utc": _format_utc(reverify_after),
            "stale_after_utc": _format_utc(stale_after),
            "default_reverify_after_days": REVERIFY_AFTER_DAYS,
            "stale_after_days": STALE_AFTER_DAYS,
        },
        "ingestion_ready": ingestion_ready,
        "validation_errors": sorted(set(validation_errors)),
    }
    record["record_fingerprint_sha256"] = _sha256_json({k: record[k] for k in record if k not in {"record_fingerprint_sha256", "source_artifact_path"}})
    return record


def _load_records(paths: Iterable[str | Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    artifact_summaries: list[dict[str, Any]] = []
    for path_like in paths:
        path = Path(path_like)
        artifact = _read_json(path)
        rows = artifact.get("accepted_upgrades") or []
        if not isinstance(rows, list):
            rows = []
        artifact_summaries.append({
            "path": str(path),
            "artifact_name": path.name,
            "batch_id": artifact.get("batch_id") or artifact.get("title") or path.stem,
            "created_utc": artifact.get("created_utc") or artifact.get("accepted_utc") or artifact.get("updated_utc") or "",
            "accepted_upgrade_rows": len(rows),
        })
        for index, row in enumerate(rows, 1):
            if isinstance(row, dict):
                records.append(_normalize_record(row, artifact, path, index))
    return records, artifact_summaries


def _validation_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    ready = [r for r in records if r["ingestion_ready"]]
    blocked = [r for r in records if not r["ingestion_ready"]]
    by_error: dict[str, int] = {}
    by_field: dict[str, dict[str, int]] = {}
    by_status: dict[str, int] = {}
    for record in records:
        by_status[record["field_status"]] = by_status.get(record["field_status"], 0) + 1
        field_counts = by_field.setdefault(record["field"] or "unknown", {"total": 0, "ingestion_ready": 0, "blocked": 0})
        field_counts["total"] += 1
        field_counts["ingestion_ready" if record["ingestion_ready"] else "blocked"] += 1
        for error in record["validation_errors"]:
            by_error[error] = by_error.get(error, 0) + 1
    return {
        "verdict": "PASS_OFFLINE_READY" if records and not blocked else "FAIL_CLOSED_NOT_INGESTION_READY",
        "total_records": len(records),
        "ingestion_ready_records": len(ready),
        "fail_closed_records": len(blocked),
        "field_status_counts_after_validation": dict(sorted(by_status.items())),
        "errors_by_type": dict(sorted(by_error.items())),
        "records_by_field": by_field,
        "ingestion_regression_contract": {
            "required_record_fields": [
                "record_id", "state", "ahj_name", "vertical", "field", "field_status", "confidence",
                "claim_value", "source_scope_limit", "display_contract", "field_evidence", "freshness",
                "record_fingerprint_sha256",
            ],
            "required_evidence_fields": [
                "source_url", "source_title", "exact_quote_or_snippet", "quote_found",
                "normalized_source_text_sha256", "normalized_source_text_length", "fetch_status_code", "last_verified_utc",
                "reverify_after_utc", "runtime_fail_closed_policy",
            ],
            "production_wiring_precondition": "Every production-imported record must be ingestion_ready=true; failures must remain needs_verification with visible warning.",
        },
    }


def build_evidence_pack(paths: Iterable[str | Path], generated_at_utc: str | None = None) -> dict[str, Any]:
    generated = generated_at_utc or _utc_now()
    records, artifact_summaries = _load_records(paths)
    records = sorted(records, key=lambda r: (str(r["state"]), str(r["ahj_name"]), str(r["vertical"]), str(r["field"]), str(r["record_id"])))
    fingerprint_artifact_summaries = [
        {k: v for k, v in summary.items() if k != "path"}
        for summary in artifact_summaries
    ]
    fingerprint_records = [
        {k: v for k, v in record.items() if k != "source_artifact_path"}
        for record in records
    ]
    fingerprint = _sha256_json({
        "version": EVIDENCE_PACK_VERSION,
        "records": fingerprint_records,
        "artifact_summaries": fingerprint_artifact_summaries,
    })
    return {
        "metadata": {
            "title": "PermitAssist Step 7B Offline Evidence Pack",
            "evidence_pack_version": EVIDENCE_PACK_VERSION,
            "generated_at_utc": generated,
            "fingerprint_sha256": fingerprint,
            "production_wiring_allowed": False,
            "source_freshness_policy": {
                "default_reverify_after_days": REVERIFY_AFTER_DAYS,
                "stale_after_days": STALE_AFTER_DAYS,
                "rule": "Before Step 7C production import, revalidate official-source quote/hash freshness; expired or unreachable evidence fails closed.",
            },
            "fail_closed_policy": {
                "403": "downgrade_to_needs_verification_until_revalidated",
                "404": "downgrade_to_needs_verification_until_revalidated",
                "500": "downgrade_to_needs_verification_until_revalidated",
                "blocked": "downgrade_to_needs_verification_until_revalidated",
                "browser_rendered_200_direct_requests_403": "use_existing_evidence_only_if_not_expired_and_display_stale_warning",
            },
            "step7c_blockers": [
                "No production import until apply_path support labels mirror field evidence.",
                "No production import until claim citations consume field_evidence per field, not broad first-source snippets.",
                "No production import until smart cache keys include evidence_pack_version/fingerprint.",
                "No production import until /api/permit, /api/batch-permit, and /api/v1/permit parity is decided/tested.",
            ],
        },
        "artifact_inputs": artifact_summaries,
        "records": records,
        "validation": _validation_summary(records),
    }


def render_markdown_report(pack: dict[str, Any]) -> str:
    metadata = pack["metadata"]
    validation = pack["validation"]
    lines = [
        "# PermitAssist Step 7B Offline Evidence Pack",
        "",
        f"Generated: {metadata['generated_at_utc']}",
        f"Evidence version: `{metadata['evidence_pack_version']}`",
        f"Fingerprint: `{metadata['fingerprint_sha256']}`",
        f"Production wiring allowed: {str(metadata['production_wiring_allowed']).lower()}",
        "",
        "## Verdict",
        "",
        f"- Validation verdict: **{validation['verdict']}**",
        f"- Total records: {validation['total_records']}",
        f"- Ingestion-ready records: {validation['ingestion_ready_records']}",
        f"- Fail-closed records: {validation['fail_closed_records']}",
        "",
        "## Source freshness / fail-closed policy",
        "",
        f"- Reverify after: {metadata['source_freshness_policy']['default_reverify_after_days']} days",
        f"- Stale after: {metadata['source_freshness_policy']['stale_after_days']} days",
        "- 403/404/500/blocked later source checks must downgrade to `needs_verification` until revalidated, unless a documented browser-rendered public page remains fresh and displays a stale-warning policy.",
        "",
        "## Validation errors by type",
        "",
    ]
    if validation["errors_by_type"]:
        lines += [f"- {key}: {value}" for key, value in validation["errors_by_type"].items()]
    else:
        lines.append("- None")
    lines += ["", "## Records by field", ""]
    for field, counts in sorted(validation["records_by_field"].items()):
        lines.append(f"- {field}: total {counts['total']}, ready {counts['ingestion_ready']}, fail-closed {counts['blocked']}")
    lines += ["", "## Step 7C blockers", ""]
    lines += [f"- {item}" for item in metadata["step7c_blockers"]]
    lines += ["", "## Artifact inputs", ""]
    for artifact in pack["artifact_inputs"]:
        lines.append(f"- {artifact['batch_id']}: {artifact['accepted_upgrade_rows']} rows — `{artifact['path']}`")
    lines += ["", "## Sample fail-closed records", ""]
    blocked = [r for r in pack["records"] if not r["ingestion_ready"]][:20]
    if blocked:
        for record in blocked:
            lines.append(f"- {record['record_id']} ({record['state']} / {record['ahj_name']} / {record['vertical']} / {record['field']}): {', '.join(record['validation_errors'])}")
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def write_evidence_pack_outputs(pack: dict[str, Any], output_dir: str | Path = DEFAULT_OUTPUT_DIR, stem: str | None = None) -> tuple[Path, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    output_stem = stem or f"permitassist-step7b-offline-evidence-pack-{pack['metadata']['generated_at_utc'].replace(':', '').replace('-', '').replace('T', 'T').replace('Z', 'Z')}"
    json_path = out / f"{output_stem}.json"
    md_path = out / f"{output_stem}.md"
    json_path.write_text(json.dumps(pack, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown_report(pack), encoding="utf-8")
    return json_path, md_path
