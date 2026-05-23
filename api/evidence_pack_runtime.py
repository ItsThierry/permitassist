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

DEFAULT_EVIDENCE_PACK_REGISTRY_PATH = Path(__file__).resolve().with_name("evidence_pack_registry.v1.json")
CODE_OWNED_EVIDENCE_PACK_ROOT = Path(__file__).resolve().parent
CONSERVATIVE_PACK_CONTROLLED_FIELDS = frozenset(
    {
        "apply_url",
        "approval_timeline",
        "companion_reviews_triggers",
        "fee_range",
        "inspections",
        "permit_type",
    }
)
PERMIT_NAME_SOURCE_FIELDS = ("display_permit_name", "official_permit_name", "official_application_category")
PERMIT_NAME_TEMPLATE_FIELDS = frozenset(
    {
        "display_permit_name",
        "official_permit_name",
        "official_application_category",
        "display_source_field",
        "name_source_precedence",
        "residential_project_type",
        "trade_permit_names",
        "septic_or_sewer_review",
        "customer_visibility_tier",
    }
)
PACK_FAIL_CLOSED_FIELDS = CONSERVATIVE_PACK_CONTROLLED_FIELDS
PROTECTED_PREVIEW_EVIDENCE_PACK_MODES = frozenset(
    {
        "phase7b_golden_local_preview",
        "phase1b_commercial_ti_exact_names_preview",
        "solar_mep_controlled_preview",
    }
)
_EMPTY_EVIDENCE_PACK_REGISTRY: dict[str, Any] = {
    "schema": "permitassist.evidence_pack.registry.v1",
    "runtime_contract_schema": "permitassist.evidence_pack.runtime.v1",
    "runtime_contract_version": "step7l_runtime_contract_v1",
    "supported_fields": sorted(CONSERVATIVE_PACK_CONTROLLED_FIELDS),
    "pack_versions": {},
    "modes": {},
}


def _registry_file() -> Path:
    override = os.environ.get("PERMITASSIST_EVIDENCE_PACK_REGISTRY_PATH", "").strip()
    return Path(override) if override else DEFAULT_EVIDENCE_PACK_REGISTRY_PATH


@lru_cache(maxsize=4)
def _load_registry(path_text: str, mtime_ns: int, size: int) -> dict[str, Any]:
    data = json.loads(Path(path_text).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != "permitassist.evidence_pack.registry.v1":
        raise ValueError("invalid evidence-pack registry schema")
    return data


def _evidence_pack_registry() -> dict[str, Any]:
    path = _registry_file()
    try:
        stat = path.stat()
        return _load_registry(str(path), stat.st_mtime_ns, stat.st_size)
    except (OSError, json.JSONDecodeError, ValueError):
        # Registry config is startup-critical metadata. If it is missing or
        # invalid, keep the app online and let evidence-pack behavior fail
        # closed through empty modes/versions instead of crashing import.
        return dict(_EMPTY_EVIDENCE_PACK_REGISTRY)


def _registry_modes() -> dict[str, dict[str, Any]]:
    modes = _evidence_pack_registry().get("modes") or {}
    if not isinstance(modes, dict):
        return {}
    return {str(k): v for k, v in modes.items() if isinstance(v, dict)}


def _code_owned_pack_fallback_path(configured_path: Path, mode: str) -> Path | None:
    """Resolve approved bundled packs when Railway's data volume hides repo data/.

    This is intentionally narrow: only registry-declared `data/evidence_packs/...`
    suffixes are eligible, and the caller still performs fingerprint/contract
    validation after the fallback path is selected.
    """
    promotion = _promotion_config(mode)
    suffix = str(promotion.get("path_suffix") or "").strip()
    if not suffix.startswith("data/evidence_packs/"):
        return None
    configured_text = configured_path.as_posix()
    if not configured_text.endswith(suffix):
        return None
    candidate = CODE_OWNED_EVIDENCE_PACK_ROOT / suffix
    if candidate.exists() and candidate.is_file():
        return candidate
    return None


def _mode_config(mode: str) -> dict[str, Any]:
    return dict(_registry_modes().get(str(mode or ""), {}))


def _promotion_config(mode: str) -> dict[str, Any]:
    promotion = _mode_config(mode).get("promotion") or {}
    return dict(promotion) if isinstance(promotion, dict) else {}


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _mode_has_valid_protected_preview_activation(mode: str) -> bool:
    if mode not in PROTECTED_PREVIEW_EVIDENCE_PACK_MODES:
        return True
    activation = _mode_config(mode).get("activation") or {}
    if not isinstance(activation, dict):
        return False
    token_env = str(activation.get("token_env") or "").strip()
    return bool(
        activation.get("preview_only_required")
        and activation.get("preview_route_required")
        and token_env
    )


def evidence_pack_mode_request_gate_valid(mode: str) -> bool:
    return str(mode or "") in ALLOWED_EVIDENCE_PACK_MODES and _mode_has_valid_protected_preview_activation(str(mode or ""))

def evidence_pack_mode_public_redaction(mode: str) -> str:
    return str(_mode_config(mode).get("public_redaction") or "redact_internal_fields")


def evidence_pack_mode_requires_preview_only(mode: str) -> bool:
    activation = _mode_config(mode).get("activation") or {}
    return bool(isinstance(activation, dict) and activation.get("preview_only_required"))


def evidence_pack_mode_requires_loader_preview_only(mode: str) -> bool:
    activation = _mode_config(mode).get("activation") or {}
    return bool(isinstance(activation, dict) and activation.get("loader_preview_only_required"))


def evidence_pack_mode_requires_preview_route(mode: str) -> bool:
    activation = _mode_config(mode).get("activation") or {}
    return bool(isinstance(activation, dict) and activation.get("preview_route_required"))


def evidence_pack_mode_preview_token_config(mode: str) -> dict[str, str]:
    activation = _mode_config(mode).get("activation") or {}
    if not isinstance(activation, dict):
        return {}
    token_env = str(activation.get("token_env") or "").strip()
    if not token_env:
        return {}
    return {
        "token_env": token_env,
        "token_header_env": str(activation.get("token_header_env") or "").strip(),
        "token_header_default": str(activation.get("token_header_default") or "").strip(),
    }


def evidence_pack_mode_coverage_truth(mode: str) -> dict[str, Any]:
    coverage = _mode_config(mode).get("coverage_truth") or {}
    return dict(coverage) if isinstance(coverage, dict) else {}


def _registry_supported_fields() -> set[str]:
    fields = _evidence_pack_registry().get("supported_fields") or []
    if not isinstance(fields, list):
        return set(CONSERVATIVE_PACK_CONTROLLED_FIELDS)
    parsed = {str(field) for field in fields if field}
    return parsed or set(CONSERVATIVE_PACK_CONTROLLED_FIELDS)


def _registry_pack_versions() -> dict[str, dict[str, Any]]:
    pack_versions = _evidence_pack_registry().get("pack_versions") or {}
    if not isinstance(pack_versions, dict):
        return {}
    return {
        str(version): config
        for version, config in pack_versions.items()
        if isinstance(config, dict)
    }


def _compat_promotion(mode: str) -> dict[str, Any]:
    promotion = _promotion_config(mode)
    metadata_equals = _as_dict(promotion.get("metadata_equals"))
    record_field_exact = _as_dict(promotion.get("record_field_exact"))
    compat = dict(promotion)
    if "fingerprint_sha256" in metadata_equals:
        compat["fingerprint_sha256"] = metadata_equals["fingerprint_sha256"]
    if "evidence_pack_version" in metadata_equals:
        compat["evidence_pack_version"] = metadata_equals["evidence_pack_version"]
    if "pack_family" in record_field_exact:
        compat["pack_family"] = record_field_exact["pack_family"]
    return compat


SUPPORTED_FIELDS = _registry_supported_fields()
RUNTIME_CONTRACT_SCHEMA = str(_evidence_pack_registry().get("runtime_contract_schema") or "permitassist.evidence_pack.runtime.v1")
RUNTIME_CONTRACT_VERSION = str(_evidence_pack_registry().get("runtime_contract_version") or "step7l_runtime_contract_v1")
SUPPORTED_EVIDENCE_PACK_VERSIONS = tuple(sorted(_registry_pack_versions()))
ALLOWED_EVIDENCE_PACK_MODES = set(_registry_modes())
PRODUCTION_WIRING_ALLOWED_BY_VERSION = {
    version: bool(config.get("production_wiring_allowed"))
    for version, config in _registry_pack_versions().items()
}
EVIDENCE_PACK_VERSION_BY_MODE = {
    mode: str(config.get("expected_version") or "")
    for mode, config in _registry_modes().items()
    if config.get("expected_version")
}
PHASE7B_GOLDEN_LOCAL_MODE = "phase7b_golden_local_preview"  # Compatibility alias; contract values live in registry.
PHASE7B_GOLDEN_LOCAL_VERSION = EVIDENCE_PACK_VERSION_BY_MODE.get(PHASE7B_GOLDEN_LOCAL_MODE, "")
PHASE7B_GOLDEN_LOCAL_PROMOTION = _compat_promotion(PHASE7B_GOLDEN_LOCAL_MODE)
SOLAR_MEP_CONTROLLED_PACK_FAMILY = str((_compat_promotion("solar_mep_controlled_preview").get("pack_family") or ""))
SOLAR_MEP_CONTROLLED_PROMOTION = _compat_promotion("solar_mep_controlled_preview")
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
    "residential",
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


def canonical_json_sha256(value: Any) -> str:
    """Shared canonical SHA-256 helper for builder/runtime fingerprints."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


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
        "residential": "residential",
        "residential remodel": "residential",
        "residential bathroom remodel": "residential",
        "single family": "residential",
        "single family residential": "residential",
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
    if re.search(r"\b(residential|single family|single family home|dwelling|home remodel|bathroom remodel|kitchen remodel)\b", text):
        return "residential"
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
    mode_config = _mode_config(mode)
    expected_version = EVIDENCE_PACK_VERSION_BY_MODE.get(mode)
    mode_locked_versions = set(EVIDENCE_PACK_VERSION_BY_MODE.values())
    strict_mode = bool(mode_config.get("strict_version"))
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
    """Validate the solar/MEP controlled-preview pack through the registry manifest."""
    return _validate_manifest_promotion("solar_mep_controlled_preview", path, raw_bytes, data, metadata)


def _phase7b_golden_pack_fingerprint(data: dict[str, Any]) -> str:
    """Recompute a generated local-golden pack fingerprint."""
    metadata = dict(data.get("metadata") or {})
    metadata.pop("fingerprint_sha256", None)
    payload = {"metadata": metadata, "records": data.get("records") or []}
    return canonical_json_sha256(payload)


def _warning(prefix: str, suffix: str) -> str:
    return f"{prefix}_{suffix}" if prefix else suffix


def _validate_manifest_promotion(mode: str, path: Path, raw_bytes: bytes | None, data: dict[str, Any], metadata: dict[str, Any]) -> tuple[str, ...]:
    """Validate pack-specific pinned runtime-contract rules from the registry manifest."""
    promotion = _promotion_config(mode)
    if not promotion:
        return ()
    prefix = str(promotion.get("warning_prefix") or mode).strip()
    warnings: list[str] = []
    normalized_path = str(path).replace("\\", "/")
    path_suffix = str(promotion.get("path_suffix") or "").strip()
    if path_suffix and not normalized_path.endswith(path_suffix):
        warnings.append(_warning(prefix, "path_mismatch"))
    raw_sha = str(promotion.get("raw_sha256") or "").strip()
    if raw_sha and raw_bytes is not None and hashlib.sha256(raw_bytes).hexdigest() != raw_sha:
        warnings.append(_warning(prefix, "raw_sha_mismatch"))
    for key, expected in _as_dict(promotion.get("metadata_equals")).items():
        actual = str(metadata.get(key) or "").strip()
        if actual != str(expected):
            suffix = {
                "evidence_pack_version": "version_mismatch",
                "fingerprint_sha256": "fingerprint_mismatch",
            }.get(key, f"{key}_mismatch")
            warnings.append(_warning(prefix, suffix))
    for key, expected in _as_dict(promotion.get("metadata_int_equals")).items():
        try:
            actual = int(metadata.get(key) or -1)
        except (TypeError, ValueError):
            actual = -1
        expected_int = _as_int(expected)
        if expected_int is None or actual != expected_int:
            warnings.append(_warning(prefix, f"{key}_mismatch"))
    for key, expected in _as_dict(promotion.get("metadata_set_equals")).items():
        if set(_as_list(metadata.get(key))) != set(_as_list(expected)):
            warnings.append(_warning(prefix, f"{key}_mismatch"))
    for key, expected in _as_dict(promotion.get("metadata_dict_equals")).items():
        if _as_dict(metadata.get(key)) != _as_dict(expected):
            warnings.append(_warning(prefix, f"{key}_mismatch"))
    records = data.get("records") or []
    if not isinstance(records, list):
        warnings.append(_warning(prefix, "record_count_mismatch"))
        records = []
    expected_count = promotion.get("record_count")
    expected_count_int = _as_int(expected_count)
    if expected_count is not None and (expected_count_int is None or len(records) != expected_count_int):
        warnings.append(_warning(prefix, "record_count_mismatch"))
    for field_name, expected_value in _as_dict(promotion.get("record_field_exact")).items():
        values = {str(record.get(field_name) or "").strip() for record in records if isinstance(record, dict)}
        if values != {str(expected_value)}:
            warnings.append(_warning(prefix, f"{field_name}_mismatch"))
    unique_row_field = str(promotion.get("unique_row_id_field") or "").strip()
    if unique_row_field:
        row_ids = {str(record.get(unique_row_field) or "").strip() for record in records if isinstance(record, dict)}
        expected_rows = _as_int(promotion.get("unique_row_count")) or 0
        if len(row_ids) != expected_rows:
            warnings.append(_warning(prefix, "row_count_mismatch"))
        expected_fields = sorted(str(field) for field in _as_list(promotion.get("exact_fields_per_row")))
        if expected_fields:
            row_fields: dict[str, list[str]] = {}
            seen_pairs: set[tuple[str, str]] = set()
            duplicate_pairs: set[tuple[str, str]] = set()
            for record in records:
                if not isinstance(record, dict):
                    warnings.append(_warning(prefix, "invalid_record_shape"))
                    continue
                row_id = str(record.get(unique_row_field) or "").strip()
                field = str(record.get("field") or "").strip()
                pair = (row_id, field)
                if pair in seen_pairs:
                    duplicate_pairs.add(pair)
                seen_pairs.add(pair)
                row_fields.setdefault(row_id, []).append(field)
                value = str(record.get("claim_value") or "").strip()
                if field == "permit_type" and not value:
                    warnings.append(_warning(prefix, "empty_promoted_claim"))
                if field == "apply_url" and urlparse(value).scheme not in {"http", "https"}:
                    warnings.append(_warning(prefix, "invalid_apply_url"))
            if expected_rows and (len(records) != expected_rows * len(expected_fields) or len(seen_pairs) != expected_rows * len(expected_fields)):
                warnings.append(_warning(prefix, "duplicate_or_missing_row_field"))
            if duplicate_pairs:
                warnings.append(_warning(prefix, "duplicate_row_field"))
            for _row_id, fields in row_fields.items():
                if sorted(fields) != expected_fields:
                    warnings.append(_warning(prefix, "exact_field_set_mismatch"))
                    break
    expected_record_fields = set(str(field) for field in _as_list(promotion.get("record_fields_exact")))
    if expected_record_fields:
        fields = {str(record.get("field") or "").strip() for record in records if isinstance(record, dict)}
        if fields != expected_record_fields:
            warnings.append(_warning(prefix, "runtime_fields_mismatch"))
    expected_verticals = set(str(vertical) for vertical in _as_list(promotion.get("record_verticals_exact")))
    if expected_verticals:
        verticals = {str(record.get("vertical") or "").strip() for record in records if isinstance(record, dict)}
        if verticals != expected_verticals:
            warnings.append(_warning(prefix, "record_verticals_mismatch"))
    allowed_statuses = set(str(status) for status in _as_list(promotion.get("source_golden_field_status_allowed")))
    if allowed_statuses:
        statuses = {str(record.get("source_golden_field_status") or "").strip() for record in records if isinstance(record, dict)}
        if not statuses or not statuses <= allowed_statuses:
            warnings.append(_warning(prefix, "unpromoted_status_present"))
    if promotion.get("fingerprint_algorithm") == "metadata_without_fingerprint_and_records_canonical_sha256":
        if str(metadata.get("fingerprint_sha256") or "").strip() != _phase7b_golden_pack_fingerprint(data):
            warnings.append(_warning(prefix, "fingerprint_mismatch"))
    return tuple(sorted(set(warnings)))


def _validate_phase7b_golden_local_promotion(path: Path, data: dict[str, Any], metadata: dict[str, Any]) -> tuple[str, ...]:
    """Validate Phase 7B/7C Golden local-preview eligibility through the registry manifest."""
    return _validate_manifest_promotion(PHASE7B_GOLDEN_LOCAL_MODE, path, None, data, metadata)


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
    promotion_warnings = _validate_manifest_promotion(mode, path, raw_bytes, data, metadata)
    if promotion_warnings:
        if contract_status != "valid":
            promotion_prefix = str(_promotion_config(mode).get("warning_prefix") or mode).strip()
            promotion_warnings = tuple(sorted(set(promotion_warnings + (_warning(promotion_prefix, "metadata_contract_invalid"),))))
        contract_status = "invalid_contract"
        contract_warnings = tuple(sorted(set(contract_warnings + promotion_warnings)))
        source_records = []
    elif _promotion_config(mode):
        source_records = data.get("records") or []
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
        value_text = str(record.get("claim_value") or "").strip()
        if not value_text:
            continue
        if field == "apply_url" and urlparse(value_text).scheme not in {"http", "https"}:
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
    if not evidence_pack_mode_request_gate_valid(mode):
        return _invalid_runtime(
            "invalid_contract",
            mode=mode,
            version=EVIDENCE_PACK_VERSION_BY_MODE.get(mode, "unknown"),
            warnings=("invalid_evidence_pack_mode_activation",),
        )
    if evidence_pack_mode_requires_loader_preview_only(mode) and not _env_truthy("PERMITASSIST_EVIDENCE_PACK_PREVIEW_ONLY"):
        activation = _mode_config(mode).get("activation") or {}
        warning = str(activation.get("missing_preview_only_warning") or "preview_only_required")
        return _invalid_runtime("invalid_contract", mode=mode, version=EVIDENCE_PACK_VERSION_BY_MODE.get(mode, "unknown"), warnings=(warning,))
    expected = os.environ.get("PERMITASSIST_EVIDENCE_PACK_EXPECTED_FINGERPRINT", "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected or ""):
        return _invalid_runtime("invalid_fingerprint", mode=mode, warnings=("missing_or_invalid_expected_fingerprint",))
    path_text = os.environ.get("PERMITASSIST_EVIDENCE_PACK_PATH", "").strip()
    if not path_text:
        return _invalid_runtime("invalid_path", mode=mode, warnings=("missing_evidence_pack_path",))
    path = Path(path_text)
    if not path.exists() or not path.is_file():
        fallback_path = _code_owned_pack_fallback_path(path, mode)
        if fallback_path is None:
            return _invalid_runtime("invalid_path", mode=mode, warnings=("evidence_pack_path_missing_or_not_file",))
        path = fallback_path
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
        "claim": "Official source field evidence",
        "value": value,
        "source_url": str(first.get("source_url") or ""),
        "source_title": str(first.get("source_title") or ""),
        "quoted_snippet": quote,
        "checked_at": checked_at,
        "confidence": str(record.get("confidence") or "needs_verification").lower(),
        "field_evidence_confidence": _field_evidence_confidence(record),
        "field_status": str(record.get("field_status") or "needs_verification").lower(),
        "source_scope_limit": record.get("source_scope_limit") or "",
    }


def _source_backed_permit_name(matches: dict[str, dict[str, Any]]) -> tuple[str, str, str, str]:
    """Return (display name, source field, status, confidence) using official-source precedence."""
    for field in PERMIT_NAME_SOURCE_FIELDS:
        record = matches.get(field)
        value = str((record or {}).get("claim_value") or "").strip()
        if not value:
            continue
        if field in {"display_permit_name", "official_permit_name"}:
            return value, field, "exact_official_name_confirmed", "high"
        return value, field, "official_category_confirmed_exact_label_missing", "medium"
    return "", "", "pending_official_source_retrieval", "low"


def _apply_source_backed_permit_name(result: dict[str, Any], matches: dict[str, dict[str, Any]]) -> None:
    display, source_field, status, confidence = _source_backed_permit_name(matches)
    result["permit_name_status"] = status
    result["permit_name_confidence"] = confidence
    result["permit_required_confidence"] = "high" if source_field else str(result.get("confidence") or "medium").lower()
    if source_field:
        result["_permit_display_name"] = display
        result["permit_name_source_field"] = source_field
        result["permit_name"] = display
        result["permit_type"] = display
        if status == "official_category_confirmed_exact_label_missing":
            warnings = result.setdefault("quality_warnings", [])
            caveat = "Official application category is source-confirmed; exact local permit label is not source-confirmed yet."
            if caveat not in warnings:
                warnings.append(caveat)
        result["permit_required"] = True
        permits = result.get("permits_required") if isinstance(result.get("permits_required"), list) else []
        if permits and isinstance(permits[0], dict):
            permits[0]["permit_type"] = display
            permits[0]["portal_selection"] = display
            permits[0]["required"] = True
        else:
            result["permits_required"] = [{"permit_type": display, "portal_selection": display, "required": True}]


def _suppress_pack_controlled_fields(result: dict[str, Any], failed_closed: list[str]) -> None:
    if "permit_type" in failed_closed:
        result["permits_required"] = []
    if "apply_url" in failed_closed:
        result["apply_url"] = None
        result["inspection_booking"] = None
        result["_url_warning"] = "Official-source apply route is still under internal source review for this location/vertical."
    if "fee_range" in failed_closed:
        result["fee_range"] = None
    if "approval_timeline" in failed_closed:
        result["approval_timeline"] = None
    if "inspections" in failed_closed:
        result["inspections"] = None
    if "companion_reviews_triggers" in failed_closed:
        result["companion_reviews_triggers"] = None
    result["claim_citations"] = []


def _add_review_reason(result: dict[str, Any], reason: str) -> None:
    allowed = {
        "unsupported_ahj",
        "unsupported_vertical",
        "caveated_row",
        "unsupported_fields_failed_closed",
        "invalid_pack_contract",
        "empty_promoted_claim",
    }
    if reason not in allowed:
        return
    reasons = result.setdefault("needs_review_reasons", [])
    if reason not in reasons:
        reasons.append(reason)
    result["needs_review"] = True


def _coverage_truth_for_mode(mode: str) -> dict[str, Any]:
    """Public-safe coverage disclosure configured by evidence-pack mode."""
    return evidence_pack_mode_coverage_truth(mode)


def _safe_meta(pack: EvidencePackRuntime, *, matches: dict[str, dict[str, Any]], failed_closed: list[str], fresh_count: int, request_vertical: str, blocked_fields: dict[str, tuple[str, ...]] | None = None) -> dict[str, Any]:
    meta = {
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
        "public_redaction": evidence_pack_mode_public_redaction(pack.mode),
    }
    name_value, name_source_field, name_status, name_confidence = _source_backed_permit_name(matches)
    meta.update({
        "permit_name_source_field": name_source_field,
        "permit_name_status": name_status,
        "permit_name_confidence": name_confidence,
    })
    if name_source_field:
        meta["permit_name_display_value"] = name_value
    if evidence_pack_mode_public_redaction(pack.mode) == "drop_evidence_pack" and matches:
        matched_records = [record for _field, record in sorted(matches.items())]
        row_ids = sorted({str(record.get("golden_row_id") or "") for record in matched_records if record.get("golden_row_id")})
        classifications = sorted({str(record.get("row_level_classification") or "") for record in matched_records if record.get("row_level_classification")})
        batches = sorted({str(record.get("batch_id") or "") for record in matched_records if record.get("batch_id")})
        meta["local_golden"] = {
            "row_id": row_ids[0] if len(row_ids) == 1 else "",
            "matched_row_ids": row_ids,
            "row_level_classification": classifications[0] if len(classifications) == 1 else "",
            "batch_id": batches[0] if len(batches) == 1 else "",
            "source_field_statuses": {field: record.get("source_golden_field_status") for field, record in sorted(matches.items())},
            "contract": "phase7b_golden_local_preview_internal_only_v1",
        }
    return meta


def _customer_facing_timeline(value: Any, *, city: str, state: str, pack: EvidencePackRuntime) -> dict[str, str] | None:
    """Return safe customer timeline shape while preserving source detail separately.

    Evidence-pack records historically promoted bare strings into
    result["approval_timeline"]. Customer renderers expect dict-or-None, so this
    constructor is part of the source/boundary normalization for Stage 1.
    """
    text = str(value or "").strip()
    if not text:
        return None
    if (
        pack.mode == "dallas_step7u_production_preview"
        and _canonical_ahj_name(city) == "dallas"
        and str(state or "").upper().strip() == "TX"
        and "214.904" in text
    ):
        return {
            "simple": (
                "Dallas local queue time still needs AHJ/portal confirmation. "
                "Texas law gives a municipal building-permit outer action deadline: "
                "act by the 45th day after submission, or issue written inability-to-act/deadline notice; "
                "if notice is issued, grant/deny is due within 30 days after notice is received."
            )
        }
    return {"simple": text}


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
    failed_closed = sorted(PACK_FAIL_CLOSED_FIELDS - set(matches))
    if any(field in matches for field in PERMIT_NAME_SOURCE_FIELDS) and "permit_type" in failed_closed:
        failed_closed.remove("permit_type")

    if pack.contract_status != "valid":
        failed_closed = sorted(PACK_FAIL_CLOSED_FIELDS)
        _suppress_pack_controlled_fields(result, failed_closed)
        _add_review_reason(result, "invalid_pack_contract")
        warnings = result.setdefault("quality_warnings", [])
        warning = "Official-source evidence contract is not valid; unconfirmed fields are hidden."
        if warning not in warnings:
            warnings.append(warning)
        result["_evidence_pack"] = _safe_meta(pack, matches={}, failed_closed=failed_closed, fresh_count=0, request_vertical=request_vertical, blocked_fields=blocked_fields)
        return result

    if pack.mode == PHASE7B_GOLDEN_LOCAL_MODE and not matches:
        # Golden preview is locked to the approved 30 AHJ×vertical rows only. For
        # unsupported AHJs/verticals, fail pack-controlled fields closed but do
        # not leak local-golden metadata into the response.
        _suppress_pack_controlled_fields(result, sorted(PACK_FAIL_CLOSED_FIELDS))
        supported_verticals = set(_promotion_config(pack.mode).get("record_verticals_exact") or _promotion_config(pack.mode).get("locked_verticals") or [])
        unsupported_reason = "unsupported_vertical" if supported_verticals and request_vertical not in supported_verticals else "unsupported_ahj"
        _add_review_reason(result, unsupported_reason)
        _add_review_reason(result, "unsupported_fields_failed_closed")
        warnings = result.setdefault("quality_warnings", [])
        warning = "Official-source coverage is incomplete for this AHJ/vertical; unconfirmed fields are hidden."
        if warning not in warnings:
            warnings.append(warning)
        result.pop("_evidence_pack", None)
        return result

    citations = []
    for field, record in matches.items():
        value = record.get("claim_value")
        value_text = str(value or "").strip()
        is_empty_promoted_claim = not value_text
        if is_empty_promoted_claim:
            failed_closed.append((field, "empty_promoted_claim"))
            _add_review_reason(result, "empty_promoted_claim")
            continue
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
        field_caveat = str(record.get("customer_facing_caveat") or "").strip()
        if field_caveat:
            warnings = result.setdefault("quality_warnings", [])
            if field_caveat not in warnings:
                warnings.append(field_caveat)
        row_caveat = str(record.get("row_level_caveat") or "").strip()
        if row_caveat:
            _add_review_reason(result, "caveated_row")
            warnings = result.setdefault("quality_warnings", [])
            caveat_warning = f"Coverage caveat: {row_caveat}"
            if caveat_warning not in warnings:
                warnings.append(caveat_warning)

    if any(field in matches for field in PERMIT_NAME_SOURCE_FIELDS):
        _apply_source_backed_permit_name(result, matches)

    _suppress_pack_controlled_fields(result, failed_closed)
    if citations:
        result["claim_citations"] = citations
    result["_evidence_pack"] = _safe_meta(pack, matches=matches, failed_closed=failed_closed, fresh_count=current_fresh_records_loaded, request_vertical=request_vertical, blocked_fields=blocked_fields)
    coverage_truth = _coverage_truth_for_mode(pack.mode)
    if coverage_truth:
        result["coverage_truth"] = coverage_truth
    if failed_closed:
        _add_review_reason(result, "unsupported_fields_failed_closed")
        warnings = result.setdefault("quality_warnings", [])
        warning = "Official-source coverage is incomplete for this AHJ/vertical; unconfirmed fields are hidden."
        if warning not in warnings:
            warnings.append(warning)
    return result
