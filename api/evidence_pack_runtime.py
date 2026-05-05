"""Local-only Step 7C evidence-pack runtime helpers.

Disabled unless PERMITASSIST_EVIDENCE_PACK_PATH points at a Step 7B pack.
The runtime intentionally imports only ingestion_ready records and never promotes
fail-closed Step 7B rows into API output.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

SUPPORTED_FIELDS = {
    "permit_type",
    "apply_url",
    "fee_range",
    "approval_timeline",
    "inspections",
    "companion_reviews_triggers",
}


@dataclass(frozen=True)
class EvidencePackRuntime:
    path: str
    version: str
    fingerprint: str
    records: tuple[dict[str, Any], ...]

    @property
    def enabled(self) -> bool:
        return bool(self.path)


def _norm(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _vertical_for_job(job_type: str) -> str:
    text = _norm(job_type)
    if any(token in text for token in ("restaurant", "kitchen", "hood", "food service")):
        return "restaurant_ti"
    if any(token in text for token in ("medical", "clinic", "dental", "exam room", "healthcare")):
        return "medical_clinic_ti"
    if "office" in text:
        return "office_ti"
    if "retail" in text:
        return "retail_ti"
    return text.replace(" ", "_")


def _ahj_matches(record_ahj: str, city: str) -> bool:
    ahj = _norm(record_ahj)
    city_n = _norm(city)
    if not ahj or not city_n:
        return False
    variants = {
        city_n,
        f"city of {city_n}",
        f"{city_n} city",
        f"{city_n} county",
        f"county of {city_n}",
    }
    return ahj in variants or city_n in ahj or ahj in city_n


def _pack_hash(data: dict[str, Any]) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@lru_cache(maxsize=4)
def _load_pack(path_text: str, mtime_ns: int, size: int) -> EvidencePackRuntime:
    path = Path(path_text)
    data = json.loads(path.read_text(encoding="utf-8"))
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    version = str(metadata.get("evidence_pack_version") or "unknown")
    fingerprint = str(metadata.get("fingerprint_sha256") or _pack_hash(data))
    records = []
    for record in data.get("records") or []:
        if not isinstance(record, dict):
            continue
        if record.get("ingestion_ready") is not True:
            continue
        field = str(record.get("field") or "")
        if field not in SUPPORTED_FIELDS:
            continue
        evidence = record.get("field_evidence")
        if not isinstance(evidence, list) or not evidence or not isinstance(evidence[0], dict):
            continue
        first = evidence[0]
        source_url = str(first.get("source_url") or "")
        if urlparse(source_url).scheme not in {"http", "https"}:
            continue
        if not str(first.get("exact_quote_or_snippet") or "").strip():
            continue
        records.append(record)
    return EvidencePackRuntime(str(path), version, fingerprint, tuple(records))


def get_local_evidence_pack() -> EvidencePackRuntime | None:
    """Return the local evidence pack if explicitly enabled; otherwise None."""
    path_text = os.environ.get("PERMITASSIST_EVIDENCE_PACK_PATH", "").strip()
    if not path_text:
        return None
    path = Path(path_text)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"PERMITASSIST_EVIDENCE_PACK_PATH does not exist: {path}")
    stat = path.stat()
    return _load_pack(str(path), stat.st_mtime_ns, stat.st_size)


def match_records(pack: EvidencePackRuntime | None, job_type: str, city: str, state: str) -> dict[str, dict[str, Any]]:
    """Return one ingestion-ready matching record per field for the request."""
    if not pack:
        return {}
    state_n = str(state or "").upper().strip()
    vertical = _vertical_for_job(job_type)
    matches: dict[str, dict[str, Any]] = {}
    # Prefer exact vertical evidence. Allow narrow AHJ-level evidence only for
    # fields where an AHJ-wide claim can be customer-useful without pretending it
    # is vertical-specific. The display contract/source_scope_limit still travels
    # with the citation and warning so the UI can show that limitation.
    for allowed_verticals in ((vertical,), ("ahj_level",)):
        for record in pack.records:
            if str(record.get("state") or "").upper().strip() != state_n:
                continue
            record_vertical = str(record.get("vertical") or "")
            if record_vertical not in allowed_verticals:
                continue
            field = str(record.get("field") or "")
            if record_vertical == "ahj_level" and field not in {"approval_timeline"}:
                continue
            if not _ahj_matches(str(record.get("ahj_name") or ""), city):
                continue
            matches.setdefault(field, record)
    return matches


def _citation_from_record(field: str, record: dict[str, Any], checked_at: str) -> dict[str, Any]:
    evidence = record.get("field_evidence") or []
    first = evidence[0] if evidence and isinstance(evidence[0], dict) else {}
    quote = str(first.get("exact_quote_or_snippet") or "")
    value = str(record.get("claim_value") or "")
    if field == "apply_url" and urlparse(value).scheme not in {"http", "https"}:
        value = ""
    return {
        "field": field,
        "claim": "Evidence-pack field evidence",
        "value": value,
        "source_url": str(first.get("source_url") or ""),
        "source_title": str(first.get("source_title") or ""),
        "quoted_snippet": quote,
        "checked_at": checked_at,
        "confidence": str(record.get("confidence") or "needs_verification").lower(),
        "evidence_pack_record_id": record.get("record_id"),
        "evidence_pack_fingerprint": record.get("record_fingerprint_sha256"),
        "source_scope_limit": record.get("source_scope_limit") or "",
    }


def apply_evidence_pack_fail_closed(result: dict[str, Any], job_type: str, city: str, state: str, checked_at: str) -> dict[str, Any]:
    """Overlay local pack evidence and fail closed for unsupported fields.

    When the pack is enabled, field values in SUPPORTED_FIELDS are considered
    displayable only when an ingestion_ready record matches the exact field,
    state, vertical, and AHJ/city. Missing matching evidence clears that field
    instead of falling back to broad sources[0] provenance.
    """
    pack = get_local_evidence_pack()
    if not pack:
        return result

    matches = match_records(pack, job_type, city, state)
    failed_closed = sorted(SUPPORTED_FIELDS - set(matches))
    citations = []

    for field, record in matches.items():
        value = record.get("claim_value")
        if field == "permit_type":
            permits = result.get("permits_required") if isinstance(result.get("permits_required"), list) else []
            if permits and isinstance(permits[0], dict):
                permits[0]["permit_type"] = value
            else:
                result["permits_required"] = [{"permit_type": value, "required": True, "notes": "Local evidence-pack record."}]
        elif field == "inspections":
            result["inspections"] = value
        elif field == "companion_reviews_triggers":
            result["companion_reviews_triggers"] = value
            warnings = result.setdefault("quality_warnings", [])
            scope_limit = record.get("source_scope_limit") or ""
            if scope_limit and scope_limit not in warnings:
                warnings.append(scope_limit)
        else:
            result[field] = value
        citations.append(_citation_from_record(field, record, checked_at))
        scope_limit = record.get("source_scope_limit") or ""
        if scope_limit:
            warnings = result.setdefault("quality_warnings", [])
            if scope_limit not in warnings:
                warnings.append(scope_limit)

    if "permit_type" in failed_closed:
        result["permits_required"] = []
    if "apply_url" in failed_closed:
        result["apply_url"] = None
        result["inspection_booking"] = None
        result["_url_warning"] = "Local evidence pack is enabled, but no ingestion-ready apply URL evidence matched this AHJ/vertical. Failing closed; verify with the AHJ."
    if "fee_range" in failed_closed:
        result["fee_range"] = None
    if "approval_timeline" in failed_closed:
        result["approval_timeline"] = None
    if "inspections" in failed_closed:
        result["inspections"] = None
    if "companion_reviews_triggers" in failed_closed:
        result["companion_reviews_triggers"] = None

    result["claim_citations"] = citations
    result["_evidence_pack"] = {
        "enabled": True,
        "path": pack.path,
        "version": pack.version,
        "fingerprint_sha256": pack.fingerprint,
        "ingestion_ready_records_loaded": len(pack.records),
        "matched_fields": sorted(matches),
        "failed_closed_fields": failed_closed,
        "cache_bypassed": True,
    }
    if failed_closed:
        result["needs_review"] = True
        warnings = result.setdefault("quality_warnings", [])
        warning = "Local evidence pack is enabled; fields without ingestion-ready matching evidence were failed closed."
        if warning not in warnings:
            warnings.append(warning)
    return result
