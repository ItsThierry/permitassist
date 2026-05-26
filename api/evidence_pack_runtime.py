"""Step 7 evidence-pack runtime helpers.

Disabled by default. Preview/staging evidence-pack mode requires an explicit
PERMITASSIST_EVIDENCE_PACK_ENABLED=true flag, a validated mode, an existing pack
path, and an expected metadata fingerprint match. Any failed contract check
returns a safe runtime object with zero records so API outputs can fail closed
without leaking filesystem paths or env internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from datetime import datetime, timezone
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

RUNTIME_CONTRACT_SCHEMA = "permitassist.evidence_pack.runtime.v1"
RUNTIME_CONTRACT_VERSION = "step7l_runtime_contract_v1"
SUPPORTED_EVIDENCE_PACK_VERSIONS = (
    "step7b_offline_v1",
    "step7b_offline_v1_test",
    "step7h_dallas_only_staging_v1",
    "step7u_dallas_only_production_preview_v1",
)
ALLOWED_EVIDENCE_PACK_MODES = {
    "dallas_step7h_preview",
    "dallas_step7u_production_preview",
    "solar_mep_controlled_preview",
}
SOLAR_MEP_CONTROLLED_PACK_FAMILY = "solar_commercial_mep_offline_v1"
SOLAR_MEP_CONTROLLED_PROMOTION = {
    "path_suffix": "evidence_packs/offline/solar_mep/permitassist-step7b-solar-commercial-mep-offline-evidence-pack-20260507.json",
    "raw_sha256": "678d1ad5b8ed04dc4350d26d133bc52f382c868bda3e407ca7e6b0fc642c3875",
    "fingerprint_sha256": "02fea6c42faee64c5ab1dd9cec2319a819b53f7f10e5abd58e1ae0fbbfe0116b",
    "evidence_pack_version": "step7b_offline_v1",
    "record_count": 9,
    "pack_family": SOLAR_MEP_CONTROLLED_PACK_FAMILY,
    "eligibility": "controlled_preview_only",
}
PRODUCTION_WIRING_ALLOWED_BY_VERSION = {
    "step7b_offline_v1": False,
    "step7b_offline_v1_test": False,
    "step7h_dallas_only_staging_v1": False,
    "step7u_dallas_only_production_preview_v1": True,
}
EVIDENCE_PACK_VERSION_BY_MODE = {
    "dallas_step7h_preview": "step7h_dallas_only_staging_v1",
    "dallas_step7u_production_preview": "step7u_dallas_only_production_preview_v1",
    "solar_mep_controlled_preview": "step7b_offline_v1",
}
VALID_CONTRACT_STATUSES = {
    "valid",
    "disabled",
    "invalid_path",
    "invalid_version",
    "invalid_fingerprint",
    "invalid_production_wiring",
    "stale",
    "invalid_contract",
}

ALLOWED_REQUEST_VERTICALS = {
    "restaurant_ti",
    "medical_clinic_ti",
    "office_ti",
    "retail_ti",
    "solar_pv_battery",
    "commercial_mechanical",
    "commercial_electrical",
    "commercial_plumbing",
    "commercial_mep_ti",
}
SOLAR_MEP_REQUEST_VERTICALS = {
    "solar_pv_battery",
    "commercial_mechanical",
    "commercial_electrical",
    "commercial_plumbing",
    "commercial_mep_ti",
}


@dataclass(frozen=True)
class EvidencePackRuntime:
    path: str
    version: str
    fingerprint: str
    records: tuple[dict[str, Any], ...]
    metadata_contract_valid: bool = True
    contract_warnings: tuple[str, ...] = ()
    production_wiring_allowed: bool = False
    fingerprint_valid: bool = False
    mode: str = ""
    contract_status: str = "valid"
    enabled: bool = True

    @property
    def active(self) -> bool:
        return self.enabled and self.contract_status == "valid"


def _norm(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _env_truthy(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def evidence_pack_enabled() -> bool:
    """Explicit kill switch: path alone must not activate pack mode."""
    return _env_truthy("PERMITASSIST_EVIDENCE_PACK_ENABLED")


def _invalid_runtime(status: str, *, mode: str = "", version: str = "unknown", fingerprint: str = "", warnings: tuple[str, ...] = ()) -> EvidencePackRuntime:
    status = status if status in VALID_CONTRACT_STATUSES else "invalid_contract"
    return EvidencePackRuntime(
        path="",
        version=version or "unknown",
        fingerprint=fingerprint or "",
        records=(),
        metadata_contract_valid=False,
        contract_warnings=tuple(sorted(set(warnings or (status,)))),
        production_wiring_allowed=False,
        fingerprint_valid=False,
        mode=mode,
        contract_status=status,
        enabled=True,
    )


def _canonical_vertical(value: Any) -> str:
    text = _norm(value)
    aliases = {
        "restaurant ti": "restaurant_ti",
        "restaurant tenant improvement": "restaurant_ti",
        "commercial restaurant": "restaurant_ti",
        "commercial restaurant ti": "restaurant_ti",
        "food service ti": "restaurant_ti",
        "medical clinic ti": "medical_clinic_ti",
        "medical clinic tenant improvement": "medical_clinic_ti",
        "dental clinic ti": "medical_clinic_ti",
        "dental ti": "medical_clinic_ti",
        "healthcare ti": "medical_clinic_ti",
        "health care ti": "medical_clinic_ti",
        "office ti": "office_ti",
        "office tenant improvement": "office_ti",
        "commercial office": "office_ti",
        "commercial office ti": "office_ti",
        "retail ti": "retail_ti",
        "retail tenant improvement": "retail_ti",
        "solar pv": "solar_pv_battery",
        "solar photovoltaic": "solar_pv_battery",
        "solar pv battery": "solar_pv_battery",
        "solar pv battery storage": "solar_pv_battery",
        "battery storage": "solar_pv_battery",
        "ess": "solar_pv_battery",
        "energy storage": "solar_pv_battery",
        "commercial mechanical": "commercial_mechanical",
        "commercial mechanical permit": "commercial_mechanical",
        "commercial electrical": "commercial_electrical",
        "commercial electrical permit": "commercial_electrical",
        "commercial plumbing": "commercial_plumbing",
        "commercial plumbing permit": "commercial_plumbing",
        "commercial mep": "commercial_mep_ti",
        "commercial mep ti": "commercial_mep_ti",
        "commercial mep tenant improvement": "commercial_mep_ti",
        "cross trade commercial mep": "commercial_mep_ti",
        "cross trade mep": "commercial_mep_ti",
        "ahj level": "ahj_level",
    }
    if text in aliases:
        return aliases[text]
    return text.replace(" ", "_")


def canonical_request_vertical(value: Any) -> str | None:
    """Return a supported request vertical, or None for unsafe/unknown input."""
    vertical = _canonical_vertical(value)
    if vertical in ALLOWED_REQUEST_VERTICALS:
        return vertical
    return None


def _term_is_locally_negated(text: str, term_start: int) -> bool:
    prefix = text[max(0, term_start - 72):term_start]
    prefix_negated = bool(
        re.search(
            r"(?:\bno\b|\bwithout\b|\bnot\b|\bnone of\b|\bexcludes?\b|\bexcluding\b|\bdoes not include\b|\bdoesn't include\b|\bno new\b)"
            r"(?:\s+(?:and|or|any|new))*"
            r"(?:[\s,;/()-]+[a-z0-9]+){0,4}[\s,;/()-]*$",
            prefix,
            flags=re.I,
        )
    )
    if prefix_negated:
        return True
    suffix = text[term_start:term_start + 96]
    return bool(
        re.search(
            r"^[a-z0-9\s,;/()'\"-]{0,48}\b(?:not included|excluded|not in scope|outside(?: the)? scope|not part|not proposed)\b",
            suffix,
            flags=re.I,
        )
    )


def _contains_unnegated_phrase(text: str, phrase: str) -> bool:
    phrase_lc = (phrase or "").lower()
    if not phrase_lc:
        return False
    pattern = re.compile(r"(?<![a-z0-9])" + re.escape(phrase_lc) + r"(?![a-z0-9])", flags=re.I)
    for match in pattern.finditer(text or ""):
        if not _term_is_locally_negated(text, match.start()):
            return True
    return False


def _job_has_healthcare_scope_signal(text: str) -> bool:
    admin_context = any(_contains_unnegated_phrase(text, term) for term in ("professional office", "office ti", "office tenant improvement", "office suite", "corporate office", "law office"))
    clinical_terms = (
        "medical clinic", "dental clinic", "health clinic", "urgent care", "doctor office",
        "doctor s office", "doctor's office", "clinic tenant improvement", "clinic ti",
        "exam room", "exam rooms", "patient care", "patient room", "treatment room",
        "procedure room", "medical gas", "med gas", "x-ray", "x ray", "radiology",
    )
    if any(_contains_unnegated_phrase(text, term) for term in clinical_terms):
        return True
    if admin_context:
        return False
    return any(_contains_unnegated_phrase(text, term) for term in ("medical office tenant", "dental office tenant", "medical office", "healthcare"))


def _vertical_for_job(job_type: str, *, explicit_vertical: Any = None, result: dict[str, Any] | None = None) -> str:
    for candidate in (
        explicit_vertical,
        (result or {}).get("evidence_vertical") if isinstance(result, dict) else None,
        (result or {}).get("vertical") if isinstance(result, dict) else None,
        (result or {}).get("active_vertical") if isinstance(result, dict) else None,
        ((result or {}).get("state_schema_context") or {}).get("active_vertical") if isinstance((result or {}).get("state_schema_context"), dict) else None,
        (result or {}).get("_primary_scope") if isinstance(result, dict) else None,
    ):
        vertical = canonical_request_vertical(candidate)
        if vertical:
            return vertical
    text = _norm(job_type)
    solar_negated = bool(re.search(r"\b(?:no|without)\s+(?:solar|pv|photovoltaic|battery|batteries|ess|energy storage)\b", text))
    if not solar_negated and re.search(r"\b(solar|pv|photovoltaic|battery storage|batteries|ess|energy storage|powerwall)\b", text):
        return "solar_pv_battery"
    if _job_has_healthcare_scope_signal(text):
        return "medical_clinic_ti"
    office_signal = bool(re.search(r"\b(office|law office|corporate office|professional office|tenant office)\b", text))
    negated_restaurant_signal = bool(
        re.search(
            r"\b(?:no|without)\s+(?:restaurant|food service|type\s*i\s*hood|hood|fryer|griddle|ansul|grease interceptor|commercial kitchen|restaurant expansion)(?:\s+needed)?\b",
            text,
        )
        or re.search(r"\b(?:non\s*[- ]?restaurant|not\s+a\s+restaurant)\b", text)
    )
    if office_signal and negated_restaurant_signal:
        return "office_ti"
    if (
        "commercial kitchen" in text
        or "food service" in text
        or re.search(r"\b(restaurant|hood|coffee shop|espresso bar|cafe|café|sandwich shop|quick serve)\b", text)
    ):
        return "restaurant_ti"
    if office_signal:
        return "office_ti"
    if re.search(r"\bretail\b", text):
        return "retail_ti"
    commercial_signal = bool(re.search(r"\b(commercial|mep)\b", text))
    trade_hits = {
        "mechanical": bool(re.search(r"\b(mechanical|hvac|rtu|duct|ventilation|exhaust)\b", text)),
        "electrical": bool(re.search(r"\b(electrical|lighting|branch circuit|panel|wiring)\b", text)),
        "plumbing": bool(re.search(r"\b(plumbing|fixture|fixtures|dwv|water supply|sanitary|restroom|sink)\b", text)),
    }
    if commercial_signal and ("mep" in text or sum(trade_hits.values()) >= 2):
        return "commercial_mep_ti"
    if commercial_signal and trade_hits["mechanical"]:
        return "commercial_mechanical"
    if commercial_signal and trade_hits["electrical"]:
        return "commercial_electrical"
    if commercial_signal and trade_hits["plumbing"]:
        return "commercial_plumbing"
    return "unknown"


def _canonical_ahj_name(value: Any) -> str:
    text = _norm(value)
    ahj_units = "city and county|city|town|village|county|borough|parish|township|municipality"
    text = re.sub(rf"^({ahj_units}) of ", "", text)
    text = re.sub(rf" ({ahj_units})$", "", text)
    if text in {"nyc", "new york city"}:
        return "new york"
    return text


def _ahj_matches(record_ahj: str, city: str) -> bool:
    ahj = _canonical_ahj_name(record_ahj)
    city_n = _canonical_ahj_name(city)
    if not ahj or not city_n:
        return False
    return ahj == city_n


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _record_is_fresh(record: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    cutoffs: list[datetime] = []
    for key in ("stale_after_utc", "reverify_after_utc"):
        raw = record.get(key)
        if str(raw or "").strip():
            cutoff = _parse_utc(raw)
            if cutoff is None:
                return False
            cutoffs.append(cutoff)
    evidence = record.get("field_evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, dict):
                continue
            for key in ("stale_after_utc", "reverify_after_utc"):
                raw = item.get(key)
                if str(raw or "").strip():
                    cutoff = _parse_utc(raw)
                    if cutoff is None:
                        return False
                    cutoffs.append(cutoff)
    if not cutoffs:
        return False
    return all(cutoff > now for cutoff in cutoffs)


def _truthy_flag(value: Any) -> bool:
    return value is True or str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _primary_field_evidence(record: dict[str, Any]) -> dict[str, Any]:
    """Return the first evidence item that is specific to this record field."""
    field = str(record.get("field") or "")
    evidence = record.get("field_evidence") or []
    if not isinstance(evidence, list):
        return {}
    for item in evidence:
        if not isinstance(item, dict):
            continue
        item_field = str(item.get("field") or "").strip()
        if item_field and item_field != field:
            continue
        return item
    return {}


def _record_step7c_blockers(record: dict[str, Any]) -> tuple[str, ...]:
    """Return production-trust blockers for a candidate field record."""
    field = str(record.get("field") or "").strip()
    evidence = record.get("field_evidence") or []
    items = evidence if isinstance(evidence, list) else []

    def item_applies_to_record_field(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        item_field = str(item.get("field") or "").strip()
        return not item_field or item_field == field

    scoped_items = [item for item in items if item_applies_to_record_field(item)]
    blockers: list[str] = []
    if _truthy_flag(record.get("source_scope_limit_generated")) or any(
        _truthy_flag(item.get("source_scope_limit_generated"))
        for item in scoped_items
    ):
        blockers.append("source_scope_limit_generated")
    if _truthy_flag(record.get("fetch_status_inferred")) or any(
        _truthy_flag(item.get("fetch_status_inferred"))
        for item in scoped_items
    ):
        blockers.append("fetch_status_inferred")
    return tuple(sorted(set(blockers)))


def _field_evidence_confidence(record: dict[str, Any]) -> str:
    status = str(record.get("field_status") or "").strip().lower()
    confidence = str(record.get("confidence") or "").strip().lower()
    if status == "verified" and confidence in {"high", "verified"}:
        return "high"
    if status in {"partial", "limited"} or confidence in {"medium", "partial", "limited"}:
        return "medium"
    return "needs_verification"


def _validate_metadata_contract(metadata: dict[str, Any], *, expected_fingerprint: str, mode: str) -> tuple[str, str, str, tuple[str, ...], bool, bool]:
    """Validate metadata and map failures to the Step 7P/7U fail-closed enum."""
    warnings: list[str] = []
    status = "valid"
    version = str(metadata.get("evidence_pack_version") or "").strip()
    if version not in SUPPORTED_EVIDENCE_PACK_VERSIONS:
        warnings.append("unsupported_evidence_pack_version")
        status = "invalid_version"
    expected_version = EVIDENCE_PACK_VERSION_BY_MODE.get(mode)
    mode_locked_versions = set(EVIDENCE_PACK_VERSION_BY_MODE.values())
    strict_mode = mode in {"dallas_step7u_production_preview", "solar_mep_controlled_preview"}
    if expected_version and version != expected_version and (strict_mode or version in mode_locked_versions):
        warnings.append("evidence_pack_version_not_allowed_for_mode")
        if status == "valid":
            status = "invalid_version"
    fingerprint = str(metadata.get("fingerprint_sha256") or "").strip()
    fingerprint_shape_valid = bool(re.fullmatch(r"[0-9a-fA-F]{64}", fingerprint))
    expected_valid = bool(re.fullmatch(r"[0-9a-fA-F]{64}", expected_fingerprint or ""))
    fingerprint_valid = fingerprint_shape_valid and expected_valid and fingerprint.lower() == expected_fingerprint.lower()
    if not fingerprint_valid:
        warnings.append("missing_invalid_or_mismatched_expected_fingerprint")
        if status == "valid":
            status = "invalid_fingerprint"
    production_wiring_allowed = bool(metadata.get("production_wiring_allowed"))
    expected_production_wiring = PRODUCTION_WIRING_ALLOWED_BY_VERSION.get(version, False)
    if production_wiring_allowed != expected_production_wiring:
        warning = "production_wiring_required_for_production_preview_pack" if expected_production_wiring else "production_wiring_allowed_must_remain_false_for_staging_pack"
        warnings.append(warning)
        if status == "valid":
            status = "invalid_production_wiring"
    return version or "unknown", fingerprint, status, tuple(sorted(set(warnings))), production_wiring_allowed, fingerprint_valid


def _validate_solar_mep_controlled_promotion(path: Path, raw_bytes: bytes, data: dict[str, Any], metadata: dict[str, Any]) -> tuple[str, ...]:
    """Pin solar/MEP controlled-preview eligibility to one reviewed offline pack."""
    warnings: list[str] = []
    promotion = SOLAR_MEP_CONTROLLED_PROMOTION
    normalized_path = str(path).replace("\\", "/")
    if not normalized_path.endswith(str(promotion["path_suffix"])):
        warnings.append("solar_mep_promoted_pack_path_mismatch")
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    if raw_sha != promotion["raw_sha256"]:
        warnings.append("solar_mep_promoted_pack_raw_sha_mismatch")
    if str(metadata.get("evidence_pack_version") or "").strip() != promotion["evidence_pack_version"]:
        warnings.append("solar_mep_promoted_pack_version_mismatch")
    if str(metadata.get("fingerprint_sha256") or "").strip() != promotion["fingerprint_sha256"]:
        warnings.append("solar_mep_promoted_pack_fingerprint_mismatch")
    records = data.get("records") or []
    if not isinstance(records, list) or len(records) != promotion["record_count"]:
        warnings.append("solar_mep_promoted_pack_record_count_mismatch")
    families = {str(record.get("pack_family") or "").strip() for record in records if isinstance(record, dict)}
    if families != {promotion["pack_family"]}:
        warnings.append("solar_mep_promoted_pack_family_mismatch")
    return tuple(sorted(set(warnings)))


@lru_cache(maxsize=4)
def _load_pack(path_text: str, mtime_ns: int, size: int, mode: str, expected_fingerprint: str) -> EvidencePackRuntime:
    path = Path(path_text)
    raw_bytes = path.read_bytes()
    data = json.loads(raw_bytes.decode("utf-8"))
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    version, fingerprint, contract_status, contract_warnings, production_wiring_allowed, fingerprint_valid = _validate_metadata_contract(
        metadata,
        expected_fingerprint=expected_fingerprint,
        mode=mode,
    )
    records = []
    source_records = data.get("records") or [] if contract_status == "valid" else []
    if contract_status == "valid" and mode == "solar_mep_controlled_preview":
        promotion_warnings = _validate_solar_mep_controlled_promotion(path, raw_bytes, data, metadata)
        if promotion_warnings:
            contract_status = "invalid_contract"
            contract_warnings = tuple(sorted(set(contract_warnings + promotion_warnings)))
            source_records = []
        else:
            families = {str(record.get("pack_family") or "").strip() for record in source_records if isinstance(record, dict)}
            if families != {SOLAR_MEP_CONTROLLED_PACK_FAMILY}:
                contract_status = "invalid_contract"
                contract_warnings = tuple(sorted(set(contract_warnings + ("solar_mep_pack_family_required_for_mode",))))
                source_records = []
    stale_candidate_count = 0
    for record in source_records:
        if not isinstance(record, dict):
            continue
        if record.get("ingestion_ready") is not True:
            continue
        field = str(record.get("field") or "")
        if field not in SUPPORTED_FIELDS:
            continue
        if not _record_is_fresh(record):
            stale_candidate_count += 1
            continue
        first = _primary_field_evidence(record)
        if not first:
            continue
        source_url = str(first.get("source_url") or "")
        if urlparse(source_url).scheme not in {"http", "https"}:
            continue
        if not str(first.get("exact_quote_or_snippet") or "").strip():
            continue
        records.append(record)
    if contract_status == "valid" and not records and stale_candidate_count:
        contract_status = "stale"
        contract_warnings = tuple(sorted(set(contract_warnings + ("no_current_fresh_ingestion_ready_records",))))
    return EvidencePackRuntime(
        "",
        version,
        fingerprint,
        tuple(records) if contract_status == "valid" else (),
        metadata_contract_valid=contract_status == "valid",
        contract_warnings=contract_warnings,
        production_wiring_allowed=production_wiring_allowed,
        fingerprint_valid=fingerprint_valid and contract_status == "valid",
        mode=mode,
        contract_status=contract_status,
        enabled=True,
    )


def get_local_evidence_pack() -> EvidencePackRuntime | None:
    """Return runtime when explicitly enabled; path alone is ignored."""
    if not evidence_pack_enabled():
        return None
    mode = os.environ.get("PERMITASSIST_EVIDENCE_PACK_MODE", "").strip()
    if mode not in ALLOWED_EVIDENCE_PACK_MODES:
        return _invalid_runtime("invalid_contract", mode=mode, warnings=("invalid_evidence_pack_mode",))
    expected = os.environ.get("PERMITASSIST_EVIDENCE_PACK_EXPECTED_FINGERPRINT", "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected or ""):
        return _invalid_runtime("invalid_fingerprint", mode=mode, warnings=("missing_or_invalid_expected_fingerprint",))
    path_text = os.environ.get("PERMITASSIST_EVIDENCE_PACK_PATH", "").strip()
    if not path_text:
        return _invalid_runtime("invalid_path", mode=mode, warnings=("missing_evidence_pack_path",))
    path = Path(path_text)
    if not path.exists() or not path.is_file():
        return _invalid_runtime("invalid_path", mode=mode, warnings=("evidence_pack_path_missing_or_not_file",))
    try:
        stat = path.stat()
        return _load_pack(str(path), stat.st_mtime_ns, stat.st_size, mode, expected)
    except Exception:
        return _invalid_runtime("invalid_contract", mode=mode, warnings=("evidence_pack_load_error",))


def _match_records_with_fresh_count(pack: EvidencePackRuntime | None, job_type: str, city: str, state: str, *, explicit_vertical: Any = None, result: dict[str, Any] | None = None) -> tuple[dict[str, dict[str, Any]], int, dict[str, tuple[str, ...]]]:
    """Return one matching record per field plus fresh count and Step 7C blockers."""
    if not pack or not pack.active:
        return {}, 0, {}
    state_n = str(state or "").upper().strip()
    vertical = _vertical_for_job(job_type, explicit_vertical=explicit_vertical, result=result)
    fresh_records = [record for record in pack.records if _record_is_fresh(record)]
    matches: dict[str, dict[str, Any]] = {}
    blocked_fields: dict[str, tuple[str, ...]] = {}
    for allowed_verticals in ((vertical,), ("ahj_level",)):
        if allowed_verticals == ("ahj_level",) and vertical == "unknown":
            continue
        if (
            allowed_verticals == ("ahj_level",)
            and vertical in SOLAR_MEP_REQUEST_VERTICALS
            and pack.mode != "solar_mep_controlled_preview"
        ):
            continue
        for record in fresh_records:
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
            blockers = _record_step7c_blockers(record)
            if blockers:
                if field not in matches:
                    blocked_fields.setdefault(field, blockers)
                continue
            matches.setdefault(field, record)
            blocked_fields.pop(field, None)
    return matches, len(fresh_records), blocked_fields


def match_records(pack: EvidencePackRuntime | None, job_type: str, city: str, state: str, *, explicit_vertical: Any = None, result: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Return one ingestion-ready matching record per field for the request."""
    matches, _fresh_count, _blocked_fields = _match_records_with_fresh_count(
        pack,
        job_type,
        city,
        state,
        explicit_vertical=explicit_vertical,
        result=result,
    )
    return matches


def _citation_from_record(field: str, record: dict[str, Any], checked_at: str) -> dict[str, Any]:
    first = _primary_field_evidence(record)
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
        "field_evidence_confidence": _field_evidence_confidence(record),
        "field_status": str(record.get("field_status") or "needs_verification").lower(),
        "evidence_pack_record_id": record.get("record_id"),
        "evidence_pack_fingerprint": record.get("record_fingerprint_sha256"),
        "source_scope_limit": record.get("source_scope_limit") or "",
    }


def _suppress_pack_controlled_fields(result: dict[str, Any], failed_closed: list[str]) -> None:
    if "permit_type" in failed_closed:
        result["permits_required"] = []
    if "apply_url" in failed_closed:
        result["apply_url"] = None
        result["inspection_booking"] = None
        result["_url_warning"] = "Evidence pack is enabled, but no valid matching apply URL evidence is active. Failing closed; verify with the AHJ."
    if "fee_range" in failed_closed:
        result["fee_range"] = None
    if "approval_timeline" in failed_closed:
        result["approval_timeline"] = None
    if "inspections" in failed_closed:
        result["inspections"] = None
    if "companion_reviews_triggers" in failed_closed:
        result["companion_reviews_triggers"] = None
    result["claim_citations"] = []


def _safe_meta(pack: EvidencePackRuntime, *, matches: dict[str, dict[str, Any]], failed_closed: list[str], fresh_count: int, request_vertical: str, blocked_fields: dict[str, tuple[str, ...]] | None = None) -> dict[str, Any]:
    return {
        "enabled": True,
        "contract_schema": RUNTIME_CONTRACT_SCHEMA,
        "runtime_contract_version": RUNTIME_CONTRACT_VERSION,
        "contract_status": pack.contract_status,
        "mode": pack.mode,
        "evidence_pack_version": pack.version,
        "version": pack.version,
        "fingerprint_valid": pack.fingerprint_valid,
        "fingerprint_prefix": (pack.fingerprint or "")[:12] if pack.fingerprint_valid else "",
        "matched_fields": sorted(matches),
        "failed_closed_fields": failed_closed,
        "matched_field_confidence": {field: _field_evidence_confidence(record) for field, record in sorted(matches.items())},
        "blocked_fields": {field: list(reasons) for field, reasons in sorted((blocked_fields or {}).items())},
        "request_vertical": request_vertical,
        "cache_bypassed": True,
    }


def _customer_facing_timeline(value: Any, *, city: str, state: str, pack: EvidencePackRuntime) -> str:
    """Return short contractor-facing timeline copy while preserving source detail separately."""
    text = str(value or "").strip()
    if (
        pack.mode == "dallas_step7u_production_preview"
        and _canonical_ahj_name(city) == "dallas"
        and str(state or "").upper().strip() == "TX"
        and "214.904" in text
    ):
        return (
            "Dallas local queue time still needs local portal confirmation. "
            "Texas law gives a municipal building-permit outer action deadline: "
            "act by the 45th day after submission, or issue written inability-to-act/deadline notice; "
            "if notice is issued, grant/deny is due within 30 days after notice is received."
        )
    return text


def apply_evidence_pack_fail_closed(
    result: dict[str, Any],
    job_type: str,
    city: str,
    state: str,
    checked_at: str,
    *,
    explicit_vertical: Any = None,
    force_contract_status: str | None = None,
) -> dict[str, Any]:
    """Overlay pack evidence only when the runtime contract is valid.

    Invalid/stale/disabled contract states suppress every pack-controlled field
    and expose only safe diagnostics. No filesystem path, env var, Railway
    internals, or full fingerprint is included in `_evidence_pack`.
    """
    pack = get_local_evidence_pack()
    if not pack:
        return result

    if force_contract_status:
        pack = EvidencePackRuntime(
            path="",
            version=pack.version,
            fingerprint=pack.fingerprint,
            records=(),
            metadata_contract_valid=False,
            contract_warnings=tuple(sorted(set(pack.contract_warnings + ("unexpected_cache_interaction",)))),
            production_wiring_allowed=pack.production_wiring_allowed,
            fingerprint_valid=False,
            mode=pack.mode,
            contract_status=force_contract_status if force_contract_status in VALID_CONTRACT_STATUSES else "invalid_contract",
            enabled=True,
        )

    matches, current_fresh_records_loaded, blocked_fields = _match_records_with_fresh_count(pack, job_type, city, state, explicit_vertical=explicit_vertical, result=result)
    request_vertical = _vertical_for_job(job_type, explicit_vertical=explicit_vertical, result=result)
    failed_closed = sorted(SUPPORTED_FIELDS - set(matches))

    if pack.contract_status != "valid":
        failed_closed = sorted(SUPPORTED_FIELDS)
        _suppress_pack_controlled_fields(result, failed_closed)
        result["needs_review"] = True
        warnings = result.setdefault("quality_warnings", [])
        warning = "Evidence pack contract is not valid; all pack-controlled fields were failed closed."
        if warning not in warnings:
            warnings.append(warning)
        result["_evidence_pack"] = _safe_meta(pack, matches={}, failed_closed=failed_closed, fresh_count=0, request_vertical=request_vertical, blocked_fields=blocked_fields)
        return result

    citations = []
    for field, record in matches.items():
        value = record.get("claim_value")
        if field == "approval_timeline":
            result["approval_timeline"] = _customer_facing_timeline(value, city=city, state=state, pack=pack)
        elif field == "permit_type":
            result["permit_type"] = value
            # A verified permit_type record in the current Step 7 evidence-pack
            # contract means this path is permit-required; future no-permit
            # records should use a different field/status before changing this.
            result["permit_required"] = True
            permits = result.get("permits_required") if isinstance(result.get("permits_required"), list) else []
            if permits and isinstance(permits[0], dict):
                permits[0]["permit_type"] = value
            else:
                result["permits_required"] = [{"permit_type": value, "required": True, "notes": "Evidence-pack record."}]
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

    _suppress_pack_controlled_fields(result, failed_closed)
    if citations:
        result["claim_citations"] = citations
    result["_evidence_pack"] = _safe_meta(pack, matches=matches, failed_closed=failed_closed, fresh_count=current_fresh_records_loaded, request_vertical=request_vertical, blocked_fields=blocked_fields)
    if failed_closed:
        result["needs_review"] = True
        warnings = result.setdefault("quality_warnings", [])
        warning = "Evidence pack is enabled; fields without ingestion-ready matching evidence were failed closed."
        if warning not in warnings:
            warnings.append(warning)
    return result
