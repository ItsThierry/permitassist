"""PermitAssist v2.4 tiered Decision Cell schema, merge gate, and assembler.

This module is intentionally standalone and deterministic. It does not call AI
models and it does not mutate production data. It defines the v2.4 factory
contract used before any at-scale enrichment run:

* Tier 1 fields gate ship and require source/quote/snapshot provenance or an
  explicit fail-closed contact.
* Tier 2 fields are opportunistic; they are validated if present but never
  block a Tier 1-complete cell by being absent.
* Runtime assembly is value-locked. A model/rephraser may add narrative only;
  if it changes regulated values, deterministic fallback wins.
"""

# pyright: reportOptionalMemberAccess=false, reportArgumentType=false, reportOptionalIterable=false
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_FIELD_REGISTRY_PATH = ROOT_DIR / "schema" / "permitassist_v24" / "fields.json"
DEFAULT_V231_INDEX_PATH = ROOT_DIR / "knowledge" / "permitassist_decision_cell_index_v231.json"

TIER1_COLLECTIONS = ("permits_required", "trade_authority", "apply")
TIER2_COLLECTIONS = ("apply_path_detail", "fee_basis", "inspections")
REGULATED_PUBLIC_KEYS = (
    "permit_required",
    "permit_decision",
    "permit_name",
    "permits_required",
    "trade_authority",
    "apply",
    "sources",
    "serving_label",
    "fail_closed",
)


@dataclass(frozen=True)
class V24Issue:
    code: str
    path: str
    message: str
    severity: str = "error"

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class V24ValidationResult:
    ok: bool
    issues: tuple[V24Issue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "issues": [issue.as_dict() for issue in self.issues]}


class V24ValidationError(ValueError):
    def __init__(self, result: V24ValidationResult):
        self.result = result
        super().__init__(json.dumps(result.to_dict(), indent=2, sort_keys=True))


def _repo_path(path: str | Path | None, *, snapshot_root: str | Path | None = None) -> Path | None:
    if not path:
        return None
    p = Path(path)
    if p.is_absolute():
        return p
    if snapshot_root:
        return Path(snapshot_root) / p
    return ROOT_DIR / p


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_field_registry(path: str | Path | None = None) -> dict[str, Any]:
    registry_path = Path(path) if path else DEFAULT_FIELD_REGISTRY_PATH
    return json.loads(registry_path.read_text())


def validate_field_registry(registry: dict[str, Any] | None = None) -> V24ValidationResult:
    registry = registry or load_field_registry()
    issues: list[V24Issue] = []
    fields = registry.get("fields")
    if not isinstance(fields, dict):
        issues.append(V24Issue("registry_missing_fields", "fields", "Field registry must contain a fields object"))
        return V24ValidationResult(False, tuple(issues))

    for name, spec in fields.items():
        if not isinstance(spec, dict):
            issues.append(V24Issue("registry_field_not_object", f"fields.{name}", "Field spec must be an object"))
            continue
        tier = spec.get("tier")
        gate = spec.get("gate")
        provenance = spec.get("provenance")
        if tier not in (1, 2):
            issues.append(V24Issue("registry_bad_tier", f"fields.{name}.tier", "Tier must be 1 or 2"))
        if gate is True and tier != 1:
            issues.append(V24Issue("tier2_marked_gate", f"fields.{name}.gate", "Only Tier 1 fields may gate ship"))
        if tier == 1 and gate is not True:
            issues.append(V24Issue("tier1_not_gate", f"fields.{name}.gate", "Tier 1 fields must gate ship"))
        if gate is True and provenance not in {"required", "required_when_source_backed_else_contact_required"}:
            issues.append(V24Issue("gate_without_required_provenance", f"fields.{name}.provenance", "Gate fields require provenance"))
        if tier == 2 and gate is not False:
            issues.append(V24Issue("tier2_not_false_gate", f"fields.{name}.gate", "Tier 2 fields must never gate ship"))
    return V24ValidationResult(not issues, tuple(issues))


def _has_real_contact(contact: Any) -> bool:
    if not isinstance(contact, dict):
        return False
    # A label like "Permit office" is not a contact by itself. Fail-closed
    # serving must give the contractor at least one reachable channel.
    for key in ("phone", "email", "apply_url", "address"):
        value = str(contact.get(key) or "").strip()
        if value:
            return True
    return False


def _provenance_issues(
    provenance: Any,
    path: str,
    *,
    snapshot_root: str | Path | None = None,
    strict_snapshots: bool = True,
) -> list[V24Issue]:
    issues: list[V24Issue] = []
    if not isinstance(provenance, dict):
        return [V24Issue("missing_provenance", path, "Regulated field requires provenance object")]

    required = ("source_url", "source_quote", "retrieved_at", "snapshot_hash", "snapshot_path", "last_verified_at", "freshness_class")
    for key in required:
        if not str(provenance.get(key) or "").strip():
            issues.append(V24Issue("provenance_missing_field", f"{path}.{key}", f"Missing provenance field: {key}"))
    if provenance.get("publishable") is not True:
        issues.append(V24Issue("provenance_not_publishable", f"{path}.publishable", "Provenance must be publishable for regulated fields"))

    quote = str(provenance.get("source_quote") or "").strip()
    snapshot_path = _repo_path(provenance.get("snapshot_path"), snapshot_root=snapshot_root)
    expected_hash = str(provenance.get("snapshot_hash") or "").strip().lower()
    if snapshot_path and snapshot_path.exists() and snapshot_path.is_file():
        actual_hash = _sha256_file(snapshot_path)
        if expected_hash and actual_hash.lower() != expected_hash:
            issues.append(V24Issue("snapshot_hash_mismatch", f"{path}.snapshot_hash", f"Expected {expected_hash}, got {actual_hash}"))
        if quote:
            text = snapshot_path.read_text(errors="ignore")
            if _normalize_text(quote) not in _normalize_text(text):
                issues.append(V24Issue("quote_not_in_snapshot", f"{path}.source_quote", "Quote is not an exact normalized substring of snapshot"))
    elif strict_snapshots:
        issues.append(V24Issue("snapshot_missing", f"{path}.snapshot_path", f"Snapshot file not found: {snapshot_path}"))
    return issues


def _iter_tier1_items(cell: dict[str, Any]) -> Iterable[tuple[str, Any, str]]:
    tier1 = cell.get("tier1") if isinstance(cell.get("tier1"), dict) else {}
    yield "tier1.main_decision.provenance", tier1.get("main_decision", {}).get("provenance") if isinstance(tier1.get("main_decision"), dict) else None, "main_decision"
    for collection in TIER1_COLLECTIONS:
        items = tier1.get(collection)
        if isinstance(items, list):
            for idx, item in enumerate(items):
                yield f"tier1.{collection}[{idx}].provenance", item.get("provenance") if isinstance(item, dict) else None, collection


def _iter_tier2_items(cell: dict[str, Any]) -> Iterable[tuple[str, Any, str]]:
    tier2 = cell.get("tier2") if isinstance(cell.get("tier2"), dict) else {}
    for collection in TIER2_COLLECTIONS:
        items = tier2.get(collection)
        if isinstance(items, list):
            for idx, item in enumerate(items):
                if isinstance(item, dict) and item:
                    yield f"tier2.{collection}[{idx}].provenance", item.get("provenance"), collection


def validate_v24_cell(
    cell: dict[str, Any],
    *,
    snapshot_root: str | Path | None = None,
    live_url_checker: Callable[[str], bool] | None = None,
    strict_snapshots: bool = True,
    require_live_url_check: bool = True,
) -> V24ValidationResult:
    """Validate one v2.4 cell against the mechanical Tier 1/Tier 2 contract.

    `live_url_checker` is injected so tests and offline factory dry-runs can stay
    deterministic. Production gates can pass a real HTTP checker.
    """
    issues: list[V24Issue] = []
    registry_result = validate_field_registry()
    issues.extend(registry_result.issues)

    if cell.get("schema_version") != "v2.4":
        issues.append(V24Issue("bad_schema_version", "schema_version", "Expected schema_version v2.4"))
    if not str(cell.get("cell_id") or "").strip():
        issues.append(V24Issue("missing_cell_id", "cell_id", "cell_id is required"))
    if cell.get("status") not in {"PUBLISHABLE", "DRAFT", "FAIL_CLOSED"}:
        issues.append(V24Issue("bad_status", "status", "Invalid status"))
    if cell.get("serving_status") not in {"SPINE_ONLY", "TIER1_COMPLETE", "TIER1_PLUS_TIER2", "FAIL_CLOSED"}:
        issues.append(V24Issue("bad_serving_status", "serving_status", "Invalid serving_status"))
    if cell.get("status") == "PUBLISHABLE" and cell.get("serving_status") not in {"TIER1_COMPLETE", "TIER1_PLUS_TIER2"}:
        issues.append(V24Issue("publishable_not_tier1_complete", "serving_status", "PUBLISHABLE cells must be Tier 1 complete"))

    tier1 = cell.get("tier1") if isinstance(cell.get("tier1"), dict) else {}
    fail_closed = tier1.get("fail_closed") if isinstance(tier1.get("fail_closed"), dict) else {"active": False}
    fail_closed_active = fail_closed.get("active") is True or cell.get("status") == "FAIL_CLOSED"
    if fail_closed_active:
        if not str(fail_closed.get("reason") or "").strip():
            issues.append(V24Issue("fail_closed_missing_reason", "tier1.fail_closed.reason", "Fail-closed cells require a reason"))
        if not _has_real_contact(fail_closed.get("contact")):
            issues.append(V24Issue("fail_closed_missing_contact", "tier1.fail_closed.contact", "Fail-closed cells require a real contact"))

    main_decision = tier1.get("main_decision") if isinstance(tier1.get("main_decision"), dict) else None
    main_value = main_decision.get("value") if isinstance(main_decision, dict) else None
    not_required_cell = main_value == "NOT_REQUIRED"
    if not main_decision and not fail_closed_active:
        issues.append(V24Issue("missing_main_decision", "tier1.main_decision", "Tier 1 main decision is required unless fail-closed"))
    elif isinstance(main_decision, dict) and main_value not in {"REQUIRED", "NOT_REQUIRED"}:
        issues.append(V24Issue("bad_main_decision", "tier1.main_decision.value", "Main decision must be REQUIRED or NOT_REQUIRED"))

    for collection in TIER1_COLLECTIONS:
        items = tier1.get(collection)
        if not isinstance(items, list) or not items:
            # A verified NOT_REQUIRED exemption should not fabricate permit,
            # trade-authority, or apply-route rows. Main-decision provenance is
            # the Tier 1 gate for a no-permit cell.
            if not fail_closed_active and not not_required_cell:
                issues.append(V24Issue("missing_tier1_collection", f"tier1.{collection}", f"Tier 1 collection {collection} is required unless fail-closed or verified NOT_REQUIRED"))
            continue
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                issues.append(V24Issue("tier1_item_not_object", f"tier1.{collection}[{idx}]", "Tier 1 item must be an object"))
            if not_required_cell and collection == "permits_required" and isinstance(item, dict) and item.get("required_status") in {"required", "conditional"}:
                issues.append(V24Issue("not_required_contains_required_permit", f"tier1.permits_required[{idx}].required_status", "NOT_REQUIRED cells may not contain required/conditional permit rows"))
        if not_required_cell and collection in {"trade_authority", "apply"} and isinstance(items, list) and items:
            issues.append(V24Issue("not_required_contains_routing_rows", f"tier1.{collection}", "NOT_REQUIRED cells must not fabricate trade authority or apply-route rows"))

    if not fail_closed_active:
        for path, provenance, _name in _iter_tier1_items(cell):
            issues.extend(_provenance_issues(provenance, path, snapshot_root=snapshot_root, strict_snapshots=strict_snapshots))

    # Tier 2 never gates by absence, but if present it must be sourced.
    for path, provenance, _name in _iter_tier2_items(cell):
        issues.extend(_provenance_issues(provenance, path, snapshot_root=snapshot_root, strict_snapshots=strict_snapshots))

    # Negative routing must be explicit and sourced; absence is not negative knowledge.
    trade_items = tier1.get("trade_authority") if isinstance(tier1.get("trade_authority"), list) else []
    for idx, item in enumerate(trade_items):
        if not isinstance(item, dict):
            continue
        negs = item.get("negative_routing")
        if negs is None:
            issues.append(V24Issue("negative_routing_missing", f"tier1.trade_authority[{idx}].negative_routing", "negative_routing must be an array, even if empty"))
            continue
        if not isinstance(negs, list):
            issues.append(V24Issue("negative_routing_not_array", f"tier1.trade_authority[{idx}].negative_routing", "negative_routing must be an array"))
            continue
        for nidx, neg in enumerate(negs):
            if not isinstance(neg, dict):
                issues.append(V24Issue("negative_routing_not_object", f"tier1.trade_authority[{idx}].negative_routing[{nidx}]", "Negative routing entry must be an object"))
                continue
            for key in ("authority", "does_not_handle"):
                if not str(neg.get(key) or "").strip():
                    issues.append(V24Issue("negative_routing_missing_field", f"tier1.trade_authority[{idx}].negative_routing[{nidx}].{key}", "Negative knowledge requires explicit authority and does_not_handle fields"))
            neg_prov = neg.get("provenance") if isinstance(neg.get("provenance"), dict) else None
            if not neg_prov:
                issues.append(V24Issue("negative_routing_missing_full_provenance", f"tier1.trade_authority[{idx}].negative_routing[{nidx}].provenance", "Negative knowledge requires full quote/hash provenance, not loose text"))
            else:
                issues.extend(_provenance_issues(neg_prov, f"tier1.trade_authority[{idx}].negative_routing[{nidx}].provenance", snapshot_root=snapshot_root, strict_snapshots=strict_snapshots))

    # Each required/conditional permit kind needs a trade authority route.
    permits = tier1.get("permits_required") if isinstance(tier1.get("permits_required"), list) else []
    trades = {str(item.get("trade") or "").lower().strip() for item in trade_items if isinstance(item, dict)}
    for idx, permit in enumerate(permits):
        if not isinstance(permit, dict):
            continue
        status = permit.get("required_status")
        kind = str(permit.get("permit_kind") or "").lower().strip()
        if status in {"required", "conditional"} and kind and kind not in trades:
            issues.append(V24Issue("triggered_trade_missing_authority", f"tier1.permits_required[{idx}].permit_kind", f"Triggered permit kind {kind} has no trade_authority route"))

    apply_items = tier1.get("apply") if isinstance(tier1.get("apply"), list) else []
    if cell.get("status") == "PUBLISHABLE" and not not_required_cell:
        live_routes = [route for route in apply_items if isinstance(route, dict) and route.get("url_status") == "live" and str(route.get("apply_url") or "").strip()]
        if not live_routes:
            issues.append(V24Issue("publishable_missing_live_apply_url", "tier1.apply", "PUBLISHABLE Tier 1 cells require at least one live apply URL route"))
        if require_live_url_check and live_routes and live_url_checker is None:
            issues.append(V24Issue("live_url_checker_required", "tier1.apply", "Strict merge gate requires a live_url_checker for PUBLISHABLE apply routes"))
    if live_url_checker:
        for idx, route in enumerate(apply_items):
            if not isinstance(route, dict):
                continue
            url = str(route.get("apply_url") or "").strip()
            if route.get("url_status") == "live" and url and not live_url_checker(url):
                issues.append(V24Issue("apply_url_not_live", f"tier1.apply[{idx}].apply_url", "Apply URL is marked live but did not pass checker"))

    change_watch = cell.get("change_watch") if isinstance(cell.get("change_watch"), dict) else {}
    if not fail_closed_active and not change_watch.get("tier1_snapshot_hashes"):
        issues.append(V24Issue("change_watch_missing_hashes", "change_watch.tier1_snapshot_hashes", "Tier 1 snapshot hashes are required"))

    return V24ValidationResult(not any(issue.severity == "error" for issue in issues), tuple(issues))


def assert_v24_cell_valid(cell: dict[str, Any], **kwargs: Any) -> None:
    result = validate_v24_cell(cell, **kwargs)
    if not result.ok:
        raise V24ValidationError(result)


def regulated_payload_from_cell(cell: dict[str, Any]) -> dict[str, Any]:
    tier1 = cell.get("tier1") if isinstance(cell.get("tier1"), dict) else {}
    main = tier1.get("main_decision") if isinstance(tier1.get("main_decision"), dict) else {}
    fail_closed = tier1.get("fail_closed") if isinstance(tier1.get("fail_closed"), dict) else {"active": False}
    decision = main.get("value")
    permit_required = decision == "REQUIRED"
    permits = copy.deepcopy(tier1.get("permits_required") if isinstance(tier1.get("permits_required"), list) else [])
    primary = permits[0] if permits and isinstance(permits[0], dict) else {}
    sources: list[dict[str, str]] = []
    for _path, prov, _name in _iter_tier1_items(cell):
        if isinstance(prov, dict) and prov.get("source_url"):
            item = {"url": str(prov.get("source_url")), "quote": str(prov.get("source_quote") or "")[:500]}
            if item not in sources:
                sources.append(item)
    label = "verified official path" if cell.get("status") == "PUBLISHABLE" and cell.get("serving_status") in {"TIER1_COMPLETE", "TIER1_PLUS_TIER2"} else "unverified — routing you to the office"
    return {
        "permit_required": permit_required if decision in {"REQUIRED", "NOT_REQUIRED"} else None,
        "permit_decision": decision,
        "permit_name": "No permit required" if decision == "NOT_REQUIRED" else (primary.get("permit_name") or None),
        "permits_required": permits,
        "trade_authority": copy.deepcopy(tier1.get("trade_authority") if isinstance(tier1.get("trade_authority"), list) else []),
        "apply": copy.deepcopy(tier1.get("apply") if isinstance(tier1.get("apply"), list) else []),
        "sources": sources,
        "serving_label": label,
        "fail_closed": copy.deepcopy(fail_closed),
    }


def _stable_hash(obj: Any) -> str:
    encoded = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def deterministic_narrative(payload: dict[str, Any]) -> str:
    if payload.get("fail_closed", {}).get("active"):
        contact = payload.get("fail_closed", {}).get("contact") or {}
        office = contact.get("office_name") or "the permit office"
        return f"PermitAssist cannot verify every required Tier 1 field for this covered AHJ yet. Contact {office} before starting work."
    decision = payload.get("permit_decision")
    permit_name = payload.get("permit_name") or "permit"
    route = (payload.get("apply") or [{}])[0] if isinstance(payload.get("apply"), list) else {}
    office = route.get("office_name") or "the permit office"
    if decision == "REQUIRED":
        return f"Permit required. Apply for {permit_name} through {office} before starting work."
    if decision == "NOT_REQUIRED":
        return "No permit required based on verified official exemption evidence."
    return "PermitAssist cannot verify a concrete permit decision yet. Contact the permit office before starting work."


def _narrative_contradicts_decision(narrative: str, decision: Any) -> bool:
    text = _normalize_text(narrative)
    if not text:
        return False
    no_permit_claim = bool(re.search(r"\b(no|not)\s+(?:building\s+|trade\s+|construction\s+)?permit\s+(?:is\s+)?(?:required|needed)\b", text)) or "permit not required" in text or "no permit required" in text
    required_claim = bool(re.search(r"\bpermit\s+(?:is\s+)?(?:required|needed)\b", text)) or "must obtain a permit" in text or "apply for" in text
    if decision == "REQUIRED" and no_permit_claim:
        return True
    if decision == "NOT_REQUIRED" and required_claim and not no_permit_claim:
        return True
    return False


def assemble_customer_view(
    cell: dict[str, Any],
    *,
    narrative_rewriter: Callable[[dict[str, Any]], dict[str, Any] | str] | None = None,
) -> dict[str, Any]:
    """Assemble a customer view with a regulated-field lock.

    A rewriter may return a string narrative or a dict containing narrative plus
    cosmetic fields. If it changes any regulated key, its output is discarded and
    the deterministic narrative is used.
    """
    payload = regulated_payload_from_cell(cell)
    regulated_hash = _stable_hash({k: payload.get(k) for k in REGULATED_PUBLIC_KEYS})
    output = copy.deepcopy(payload)
    output["narrative"] = deterministic_narrative(payload)
    output["regulated_payload_hash"] = regulated_hash

    if not narrative_rewriter:
        return output

    try:
        rewrite = narrative_rewriter(copy.deepcopy(payload))
    except Exception:
        output["narrative_rewrite_status"] = "failed_exception_deterministic_fallback"
        return output

    if isinstance(rewrite, str):
        if _narrative_contradicts_decision(rewrite, payload.get("permit_decision")):
            output["narrative_rewrite_status"] = "rejected_narrative_contradicts_regulated_decision"
            return output
        output["narrative"] = rewrite
        output["narrative_rewrite_status"] = "accepted_narrative_only"
        return output
    if not isinstance(rewrite, dict):
        output["narrative_rewrite_status"] = "failed_bad_shape_deterministic_fallback"
        return output

    candidate = copy.deepcopy(output)
    for key, value in rewrite.items():
        candidate[key] = value
    candidate_regulated = {k: candidate.get(k) for k in REGULATED_PUBLIC_KEYS}
    if _stable_hash(candidate_regulated) != regulated_hash:
        output["narrative_rewrite_status"] = "rejected_regulated_field_tamper_deterministic_fallback"
        return output
    if isinstance(candidate.get("narrative"), str) and _narrative_contradicts_decision(candidate["narrative"], payload.get("permit_decision")):
        output["narrative_rewrite_status"] = "rejected_narrative_contradicts_regulated_decision"
        return output
    candidate["regulated_payload_hash"] = regulated_hash
    candidate["narrative_rewrite_status"] = "accepted_regulated_fields_unchanged"
    return candidate


def provenance_from_v231_source(source: dict[str, Any], *, fallback_snapshot_path: str = "") -> dict[str, Any]:
    snapshot_hash = source.get("fingerprint_sha256") or source.get("snapshot_sha256") or source.get("raw_fingerprint_sha256") or ""
    return {
        "source_url": source.get("final_url") or source.get("url") or "",
        "source_quote": source.get("quote") or "",
        "retrieved_at": source.get("accessed_at_utc") or source.get("retrieved_at") or "",
        "snapshot_hash": snapshot_hash,
        "snapshot_path": source.get("snapshot_path") or fallback_snapshot_path,
        "effective_date": source.get("effective_date"),
        "freshness_class": "v231_imported_needs_v24_snapshot_verification",
        "last_verified_at": source.get("accessed_at_utc") or source.get("retrieved_at") or "",
        "publishable": bool(source.get("official_source") is True and source.get("quote")),
    }


def convert_v231_cell_to_v24_spine(cell: dict[str, Any], *, index_key: str = "") -> dict[str, Any]:
    """Convert a v2.3.1 cell into a v2.4 spine candidate.

    This is a factory starting point, not a claim that the cell is Tier 1
    complete. Rows with insufficient v2.3.1 evidence become explicit
    fail-closed candidates instead of invalid drafts.
    """
    key_parts = index_key.split("|") if index_key else []
    key_state = key_parts[0] if len(key_parts) >= 1 else ""
    key_ahj = key_parts[1] if len(key_parts) >= 2 else ""
    key_project = key_parts[2] if len(key_parts) >= 3 else ""
    evidence = cell.get("source_evidence") if isinstance(cell.get("source_evidence"), list) else []
    primary_source = evidence[0] if evidence and isinstance(evidence[0], dict) else {}
    route_source = next((src for src in evidence if isinstance(src, dict) and (src.get("final_url") or src.get("url"))), primary_source)
    prov = provenance_from_v231_source(primary_source)
    route_prov = provenance_from_v231_source(route_source)
    authority = cell.get("authority_model") if isinstance(cell.get("authority_model"), dict) else {}
    permit_kind = str(cell.get("permit_kind") or "building").lower()
    permit_name = cell.get("permit_name") or "Building Permit"
    ahj = cell.get("city") or cell.get("ahj_name") or key_ahj.replace("_", " ").title()
    state = cell.get("state") or key_state
    project_family = cell.get("project_type_slug") or key_project
    jurisdiction_id = cell.get("jurisdiction_id") or "-".join(part for part in ["us", state.lower(), key_ahj] if part)
    derived_cell_id = cell.get("cell_id") or "__".join(part for part in [jurisdiction_id, project_family, permit_kind] if part)
    office = authority.get("application_authority") or authority.get("issuing_authority") or cell.get("ahj_name") or (f"{ahj} permit office" if ahj else "Permit office")
    apply_url = authority.get("application_url") or route_prov.get("source_url") or ""
    snapshot_hashes = []
    for src in evidence:
        if isinstance(src, dict):
            h = src.get("fingerprint_sha256") or src.get("snapshot_sha256") or src.get("raw_fingerprint_sha256")
            if h and h not in snapshot_hashes:
                snapshot_hashes.append(h)
    has_publishable_source = bool(prov.get("publishable") and prov.get("source_quote") and prov.get("snapshot_hash"))
    has_min_identity = bool(derived_cell_id and jurisdiction_id and ahj and state and project_family)
    fail_closed = not (has_publishable_source and has_min_identity and cell.get("main_decision") in {"REQUIRED", "NOT_REQUIRED"})
    status = "FAIL_CLOSED" if fail_closed else "DRAFT"
    serving_status = "FAIL_CLOSED" if fail_closed else "SPINE_ONLY"
    fail_reason = None
    if fail_closed:
        missing = []
        if not has_min_identity:
            missing.append("identity")
        if not has_publishable_source:
            missing.append("publishable v2.3.1 source quote/hash")
        if cell.get("main_decision") not in {"REQUIRED", "NOT_REQUIRED"}:
            missing.append("binary main decision")
        fail_reason = "v2.4 Tier 1 source floor not met during v2.3.1 spine migration: " + ", ".join(missing)

    tier1 = {
        "main_decision": {"value": cell.get("main_decision"), "provenance": prov},
        "permits_required": [
            {
                "permit_name": permit_name,
                "permit_kind": permit_kind,
                "trigger": cell.get("customer_action") or f"{cell.get('project_type_label') or project_family} work",
                "required_status": "required" if cell.get("main_decision") == "REQUIRED" else "not_required",
                "provenance": prov,
            }
        ],
        "trade_authority": [
            {
                "trade": permit_kind,
                "handled_by_local_ahj": (authority.get("authority_type") or "local") == "local",
                "issuing_authority": {"name": authority.get("issuing_authority") or office, "tier": authority.get("authority_type") or "local"},
                "application_authority": {"name": office},
                "negative_routing": [],
                "provenance": route_prov,
                "routing_overlay": None,
            }
        ],
        "apply": [
            {
                "permit_name": permit_name,
                "office_name": office,
                "apply_url": apply_url,
                "url_status": "stale" if apply_url else "broken",
                "last_url_check": route_prov.get("last_verified_at") or "",
                "channel": "online" if apply_url else "phone",
                "phone": None,
                "address": None,
                "provenance": route_prov,
            }
        ],
        "fail_closed": {
            "active": fail_closed,
            "reason": fail_reason,
            "contact": {"office_name": office, "apply_url": apply_url} if fail_closed else {},
        },
    }
    if fail_closed:
        # Keep identity/contact, but do not pretend incomplete v2.3.1 rows are
        # Tier 1-complete. The enrichment run must rebuild/verify them.
        tier1["permits_required"] = []
        tier1["trade_authority"] = []
        tier1["apply"] = []

    return {
        "schema_version": "v2.4",
        "cell_id": derived_cell_id,
        "jurisdiction_id": jurisdiction_id,
        "ahj": ahj,
        "state": state,
        "county": cell.get("county"),
        "project_family": project_family,
        "scope": cell.get("project_type_label"),
        "status": status,
        "serving_status": serving_status,
        "tier1": tier1,
        "tier2": {"apply_path_detail": [], "fee_basis": [], "inspections": []},
        "change_watch": {
            "tier1_snapshot_hashes": snapshot_hashes,
            "diff_cadence": "tier1_surface_weekly_or_on_source_change",
            "last_diff": None,
            "stale": True,
        },
        "factory_notes": [
            "Converted from v2.3.1 as a spine candidate only.",
            "Must enrich trade_authority for all triggered trades and live-check apply URL before Tier1 publish.",
            "FAIL_CLOSED candidates require source/contact rebuild before customer verified serving." if fail_closed else "DRAFT candidate has v2.3.1 source spine but still needs v2.4 enrichment gates.",
        ],
    }


def audit_v231_index_for_v24(index_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(index_path) if index_path else DEFAULT_V231_INDEX_PATH
    doc = json.loads(path.read_text())
    index = doc.get("index") if isinstance(doc, dict) else {}
    if not isinstance(index, dict):
        raise ValueError("v231 index must contain an index object")
    counts = {
        "total_index_entries": len(index),
        "publishable": 0,
        "has_authority_model": 0,
        "has_apply_url": 0,
        "has_source_quote": 0,
        "has_companion_permits": 0,
        "needs_v24_trade_authority_enrichment": 0,
        "needs_v24_live_url_check": 0,
        "tier2_backlog_mentions": {},
    }
    by_project: dict[str, int] = {}
    for key, cell in index.items():
        if not isinstance(cell, dict):
            continue
        project = key.split("|")[-1] if "|" in key else str(cell.get("project_type_slug") or "unknown")
        by_project[project] = by_project.get(project, 0) + 1
        if cell.get("publish_status") == "PUBLISHABLE":
            counts["publishable"] += 1
        authority = cell.get("authority_model") if isinstance(cell.get("authority_model"), dict) else {}
        if authority:
            counts["has_authority_model"] += 1
        if authority.get("application_url"):
            counts["has_apply_url"] += 1
        evidence = cell.get("source_evidence") if isinstance(cell.get("source_evidence"), list) else []
        if any(isinstance(src, dict) and src.get("quote") for src in evidence):
            counts["has_source_quote"] += 1
        companions = cell.get("companion_permits") if isinstance(cell.get("companion_permits"), list) else []
        if companions:
            counts["has_companion_permits"] += 1
            counts["needs_v24_trade_authority_enrichment"] += 1
        # v2.3.1 URLs are historical; v2.4 requires live-check status.
        counts["needs_v24_live_url_check"] += 1
        for item in cell.get("enrichment_backlog") or []:
            key2 = str(item)
            counts["tier2_backlog_mentions"][key2] = counts["tier2_backlog_mentions"].get(key2, 0) + 1
    counts["by_project_family"] = dict(sorted(by_project.items()))
    return counts

# ─── Runtime resolver / late reconciliation ──────────────────────────────────
from enum import Enum
import os

V24_RESOLVER_VERSION = "v24-live-runtime-resolver-2026-06-24"
DEFAULT_V24_INDEX_PATH = ROOT_DIR / "knowledge" / "v24" / "permitassist_decision_cell_index_v24.json"
DEFAULT_V24_MANIFEST_PATH = ROOT_DIR / "knowledge" / "v24" / "permitassist_v24_manifest.json"

class V24ResolutionStatus(str, Enum):
    EXACT_CELL_PUBLISHABLE = "exact_cell_publishable"
    EXACT_CELL_FAIL_CLOSED = "exact_cell_fail_closed"
    AHJ_COVERED_PROJECT_NOT_COVERED = "ahj_covered_project_not_covered"
    AHJ_NOT_COVERED = "ahj_not_covered"
    AMBIGUOUS_ABSTAIN = "ambiguous_abstain"
    INDEX_UNAVAILABLE = "index_unavailable"
    VALIDATION_FAILED_DEMOTED = "validation_failed_demoted"

@dataclass(frozen=True)
class V24Resolution:
    status: V24ResolutionStatus
    cell: dict[str, Any] | None = None
    key: str | None = None
    project_candidates: tuple[str, ...] = ()
    reason: str = ""
    resolver_version: str = V24_RESOLVER_VERSION
    package_manifest_sha256: str | None = None

_V24_INDEX_CACHE: dict[str, Any] = {}
_V24_INDEX_CACHE_SOURCE: Path | None = None
_V24_INDEX_CACHE_MTIME_NS: int | None = None
_V24_INDEX_CACHE_LOAD_FAILED = False


def get_v24_mode() -> str:
    mode = str(os.environ.get("PERMITASSIST_V24_MODE", "off") or "off").strip().lower()
    return mode if mode in {"off", "shadow", "active"} else "off"


def _normalize_ahj_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")


def _classify_project_candidates(job_type: str, job_category: str = "") -> list[str]:
    try:
        from .v231_decision_cells import classify_project_candidates
    except Exception:
        try:
            from v231_decision_cells import classify_project_candidates
        except Exception:
            classify_project_candidates = None
    if classify_project_candidates:
        candidates = list(classify_project_candidates(job_type, job_category))
        text = _normalize_text(f"{job_type} {job_category}")
        if "roof" in text and "residential_remodel" not in candidates and ("residential" in text or "home" in text):
            # Many v24 W3 residential cells cover roofing inside the broader
            # residential_remodel family. Try exact reroof first, then the
            # source-backed broader residential family instead of dropping to a
            # generic live answer or fail-closed path.
            candidates.append("residential_remodel")
        return candidates
    text = _normalize_text(f"{job_type} {job_category}")
    if "roof" in text:
        return ["reroof"]
    if "residential" in text or "home" in text or "kitchen" in text or "bath" in text:
        return ["residential_remodel"]
    if any(t in text for t in ("tenant improvement", " ti ", "buildout", "build out", "commercial")):
        return ["commercial_tenant_improvement"]
    return []


def _sha256_text_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_v24_index(index_path: str | Path | None = None, manifest_path: str | Path | None = None, expected_manifest_sha256: str | None = None) -> dict[str, dict[str, Any]] | None:
    global _V24_INDEX_CACHE, _V24_INDEX_CACHE_SOURCE, _V24_INDEX_CACHE_MTIME_NS, _V24_INDEX_CACHE_LOAD_FAILED
    path = Path(index_path or os.environ.get("PERMITASSIST_V24_INDEX_PATH") or DEFAULT_V24_INDEX_PATH)
    manifest = Path(manifest_path or os.environ.get("PERMITASSIST_V24_MANIFEST_PATH") or DEFAULT_V24_MANIFEST_PATH)
    expected = expected_manifest_sha256 or os.environ.get("PERMITASSIST_V24_MANIFEST_SHA256")
    try:
        stat = path.stat()
    except OSError:
        _V24_INDEX_CACHE_LOAD_FAILED = True
        return None
    if index_path is None and _V24_INDEX_CACHE_SOURCE == path and _V24_INDEX_CACHE_MTIME_NS == stat.st_mtime_ns and not _V24_INDEX_CACHE_LOAD_FAILED:
        return _V24_INDEX_CACHE
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        entries = obj.get("index") if isinstance(obj, dict) else None
        if not isinstance(entries, dict):
            raise ValueError("missing index")
        if manifest.exists() and expected and _sha256_text_file(manifest) != expected:
            raise ValueError("manifest sha256 mismatch")
        normalized: dict[str, dict[str, Any]] = {}
        for key, cell in entries.items():
            if not isinstance(key, str) or not isinstance(cell, dict):
                raise ValueError("bad index entry")
            normalized[key] = cell
    except Exception:
        _V24_INDEX_CACHE_LOAD_FAILED = True
        return None
    if index_path is None:
        _V24_INDEX_CACHE = normalized
        _V24_INDEX_CACHE_SOURCE = path
        _V24_INDEX_CACHE_MTIME_NS = stat.st_mtime_ns
        _V24_INDEX_CACHE_LOAD_FAILED = False
    return normalized


def _v24_ahj_has_any_project(index: dict[str, dict[str, Any]], state_key: str, ahj_key: str) -> bool:
    return any(key.startswith(f"{state_key}|{ahj_key}|") for key in index)


def resolve_v24_cell(city: str, state: str, job_type: str, job_category: str = "", *, index_path: str | Path | None = None, force: bool = False) -> V24Resolution:
    if get_v24_mode() == "off" and not force:
        return V24Resolution(V24ResolutionStatus.INDEX_UNAVAILABLE, reason="v24 mode off")
    state_key = (state or "").strip().upper()
    ahj_key = _normalize_ahj_key(city)
    if not state_key or not ahj_key:
        return V24Resolution(V24ResolutionStatus.AMBIGUOUS_ABSTAIN, reason="missing AHJ/state")
    index = load_v24_index(index_path=index_path)
    if not index:
        return V24Resolution(V24ResolutionStatus.INDEX_UNAVAILABLE, reason="v24 index unavailable")
    candidates = tuple(_classify_project_candidates(job_type, job_category))
    if not candidates:
        status = V24ResolutionStatus.AMBIGUOUS_ABSTAIN if _v24_ahj_has_any_project(index, state_key, ahj_key) else V24ResolutionStatus.AHJ_NOT_COVERED
        return V24Resolution(status, project_candidates=(), reason="project family ambiguous or unsupported")
    for project in candidates:
        key = f"{state_key}|{ahj_key}|{project}"
        cell = index.get(key)
        if not isinstance(cell, dict):
            continue
        if cell.get("status") == "FAIL_CLOSED" or cell.get("serving_status") == "FAIL_CLOSED":
            return V24Resolution(V24ResolutionStatus.EXACT_CELL_FAIL_CLOSED, cell=copy.deepcopy(cell), key=key, project_candidates=candidates, reason="exact v24 fail-closed/contact-only cell")
        # Runtime/prod packages are portable: build-time validation proves the
        # source snapshot exists, hashes, and quote matches, but Railway should
        # not need Boban-local snapshot files mounted to serve a vetted cell.
        validation = validate_v24_cell(cell, strict_snapshots=False, require_live_url_check=False)
        if validation.ok and cell.get("status") == "PUBLISHABLE" and cell.get("serving_status") in {"TIER1_COMPLETE", "TIER1_PLUS_TIER2"}:
            return V24Resolution(V24ResolutionStatus.EXACT_CELL_PUBLISHABLE, cell=copy.deepcopy(cell), key=key, project_candidates=candidates, reason="exact publishable v24 AHJ/project cell")
        return V24Resolution(V24ResolutionStatus.VALIDATION_FAILED_DEMOTED, cell=copy.deepcopy(cell), key=key, project_candidates=candidates, reason="v24 cell exists but runtime validation failed")
    if _v24_ahj_has_any_project(index, state_key, ahj_key):
        return V24Resolution(V24ResolutionStatus.AHJ_COVERED_PROJECT_NOT_COVERED, key=f"{state_key}|{ahj_key}|{candidates[0]}", project_candidates=candidates, reason="AHJ covered but project family not covered")
    return V24Resolution(V24ResolutionStatus.AHJ_NOT_COVERED, key=f"{state_key}|{ahj_key}|{candidates[0]}", project_candidates=candidates, reason="AHJ not covered by v24 index")


def build_v24_prompt_context(resolution: V24Resolution | None) -> str:
    if not isinstance(resolution, V24Resolution) or resolution.status != V24ResolutionStatus.EXACT_CELL_PUBLISHABLE or not resolution.cell:
        return ""
    cell = resolution.cell
    payload = regulated_payload_from_cell(cell)
    quote = (payload.get("sources") or [{}])[0].get("quote", "") if isinstance(payload.get("sources"), list) else ""
    return (
        "=== AUTHORITATIVE v2.4 DECISION CELL CONTEXT (GROUNDING ONLY) ===\n"
        f"Cell: {cell.get('cell_id')}\nExact AHJ/project: {resolution.key}\n"
        f"Main decision: {payload.get('permit_decision')} for {payload.get('permit_name')}.\n"
        f"Apply URL: {(payload.get('apply') or [{}])[0].get('apply_url','') if isinstance(payload.get('apply'), list) else ''}\n"
        f"Official quote: {quote[:700]}\n"
        "Run the normal pipeline; late v24 reconciliation is final for regulated primary fields only.\n"
        "=== END v2.4 DECISION CELL CONTEXT ==="
    )


def reconcile_v24_result(result: dict[str, Any], resolution: V24Resolution | None) -> dict[str, Any]:
    if not isinstance(result, dict) or get_v24_mode() != "active":
        return result
    if not isinstance(resolution, V24Resolution):
        return result
    result["_v24_resolution_status"] = resolution.status.value
    if resolution.status == V24ResolutionStatus.EXACT_CELL_FAIL_CLOSED:
        cell = resolution.cell or {}
        result["_v24_cell_id"] = cell.get("cell_id")
        fc = cell.get("tier1", {}).get("fail_closed", {}) if isinstance(cell.get("tier1"), dict) else {}
        result["fail_closed"] = copy.deepcopy(fc) if isinstance(fc, dict) else {"active": True}
        result["_v24_fail_closed_live_policy"] = "static_data_gap_live_research_allowed_when_source_backed"

        # A fail-closed v24 row means the static package cannot lock regulated
        # fields. It must not neuter the engine: if the normal live-research path
        # already produced a binary REQUIRED/NOT_REQUIRED answer, preserve it and
        # let the final source floor require AHJ-specific evidence/caveats. Only
        # when there is no live binary answer do we use the safe contact-only
        # fallback.
        decision = str(result.get("permit_decision") or "").upper().strip()
        verdict = str(result.get("permit_verdict") or "").upper().strip()
        binary_answer = result.get("permit_required") in {True, False} or decision in {"REQUIRED", "NOT_REQUIRED"} or verdict in {"YES", "NO", "REQUIRED", "NOT_REQUIRED"}
        field_sources = dict(result.get("_field_sources") or {}) if isinstance(result.get("_field_sources"), dict) else {}
        field_sources["fail_closed"] = "permitassist_v24_static_data_gap"
        if not binary_answer:
            result["permit_decision"] = "UNKNOWN"
            result["permit_required"] = None
            result["permit_verdict"] = "CONTACT_AHJ"
            result["confidence"] = "fail_closed"
            result["confidence_reason"] = "v2.4 static Decision Cell is fail-closed and no source-backed live answer was produced; routing to office confirmation."
            for field in ("permit_verdict", "permit_required", "permit_decision"):
                field_sources[field] = "permitassist_v24_fail_closed_no_live_answer"
        else:
            result.setdefault("confidence_reason", "v2.4 static Decision Cell has a data gap; live source-backed engine answer is preserved and must cite AHJ-specific evidence.")
            warnings = result.setdefault("warnings", [])
            warning = "Static v2.4 package row has a source/data gap; this answer relies on live source-backed research rather than a locked decision cell."
            if isinstance(warnings, list) and warning not in warnings:
                warnings.append(warning)
        result["_field_sources"] = field_sources
        return result
    if resolution.status != V24ResolutionStatus.EXACT_CELL_PUBLISHABLE or not resolution.cell:
        return result
    cell = resolution.cell
    # Runtime/prod serving trusts the build-time strict snapshot gate and the
    # shipped URL/quote/hash metadata; it must not depend on local snapshot file
    # presence after packaging.
    validation = validate_v24_cell(cell, strict_snapshots=False, require_live_url_check=False)
    if not validation.ok:
        result["_v24_resolution_status"] = V24ResolutionStatus.VALIDATION_FAILED_DEMOTED.value
        return result
    payload = regulated_payload_from_cell(cell)
    result["permit_required"] = payload.get("permit_required")
    result["permit_decision"] = payload.get("permit_decision")
    result["permit_verdict"] = "YES" if payload.get("permit_required") is True else "NO"
    if payload.get("permit_name"):
        result["permit_name"] = payload.get("permit_name")
    applies = payload.get("apply") if isinstance(payload.get("apply"), list) else []
    if applies:
        route = applies[0] if isinstance(applies[0], dict) else {}
        result["apply_url"] = route.get("apply_url") or result.get("apply_url")
        result["applying_office"] = route.get("office_name") or result.get("applying_office")
    if isinstance(payload.get("permits_required"), list):
        result["permits_required"] = copy.deepcopy(payload["permits_required"])
    if isinstance(payload.get("trade_authority"), list):
        result["trade_authority"] = copy.deepcopy(payload["trade_authority"])
    if isinstance(payload.get("sources"), list):
        existing = result.get("sources") if isinstance(result.get("sources"), list) else []
        result["sources"] = copy.deepcopy(payload["sources"]) + [s for s in existing if s not in payload["sources"]]
    field_sources = dict(result.get("_field_sources") or {}) if isinstance(result.get("_field_sources"), dict) else {}
    for field in ("permit_verdict", "permit_required", "permit_decision", "permit_name", "permits_required", "trade_authority", "applying_office", "apply_url"):
        if result.get(field) is not None:
            field_sources[field] = "permitassist_v24_decision_cell"
    result["_field_sources"] = field_sources
    trusted_cell_sources = copy.deepcopy(payload.get("sources")) if isinstance(payload.get("sources"), list) else []
    result["_decision_cell_primary_lock"] = {
        "source": "permitassist_v24_decision_cell",
        "exact_match": True,
        "cell_id": cell.get("cell_id"),
        "permit_decision": payload.get("permit_decision"),
        "permit_required": payload.get("permit_required"),
        "permit_name": payload.get("permit_name"),
        "apply_url": result.get("apply_url"),
        "applying_office": result.get("applying_office"),
        # Bind official provenance into the same server-held exact-cell lock as
        # the regulatory decision.  Later finalizers may rebuild or sanitize the
        # public source mirrors; they must not make a validated exact
        # NOT_REQUIRED cell look unbacked merely because those mutable mirrors
        # were stale.  Submitted DTOs cannot create this lock.
        "source_urls": [source.get("url") for source in trusted_cell_sources if isinstance(source, dict) and source.get("url")],
        "sources": trusted_cell_sources,
    }
    result["_v24_cell_id"] = cell.get("cell_id")
    result["_v24_resolver_version"] = V24_RESOLVER_VERSION
    return result


def reconcile_authoritative_result(result: dict[str, Any], *, v24_resolution: V24Resolution | None = None, v231_resolution: Any = None) -> dict[str, Any]:
    if get_v24_mode() == "active" and isinstance(v24_resolution, V24Resolution) and v24_resolution.status in {V24ResolutionStatus.EXACT_CELL_PUBLISHABLE, V24ResolutionStatus.EXACT_CELL_FAIL_CLOSED}:
        reconcile_v24_result(result, v24_resolution)
        return result
    try:
        from .v231_decision_cells import reconcile_v231_result
    except Exception:
        try:
            from v231_decision_cells import reconcile_v231_result
        except Exception:
            reconcile_v231_result = None
    if reconcile_v231_result:
        return reconcile_v231_result(result, v231_resolution)
    return result
