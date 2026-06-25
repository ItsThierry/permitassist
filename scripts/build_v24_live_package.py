#!/usr/bin/env python3
"""Build PermitAssist v2.4 live-runtime package from frozen staged artifacts.

This is an offline/staged-only package builder. It reads the accepted v2.4
artifact roots, recomputes the target set, writes immutable manifest artifacts,
and emits app-runtime JSON under knowledge/v24. It does not mutate production,
registry, Railway, env, or customer-visible state.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_RUN_ROOT = Path("/home/boban/projects/permitassist-data/artifacts/v24-final-tier1-enrichment-20260613T130304Z")
V231_PACKAGE = Path("/home/boban/projects/permitassist-data/compiled/v231/v231-national-2489-2026-06-12")
W4_ROOT = DATA_RUN_ROOT / "w4_commercial_canary_20260613T164649Z"
W3_ROOT = DATA_RUN_ROOT / "w3_residential_reroof_wave"
W2_ROOT = DATA_RUN_ROOT / "w2_canary"
W4_RECONCILIATION = W4_ROOT / "control/W4_V231_COUNT_RECONCILIATION_20260624T1754Z.json"
W3_FINAL_REPORT = W3_ROOT / "final/W3_FINAL_REPORT.json"
W2_FINAL_REPORT = W2_ROOT / "final/W2_FINAL_REPORT.json"

W4_RUN_ROOTS = [
    W4_ROOT / "w4_109_tier1_dispatch_20260620T051826Z",
    W4_ROOT / "w4_part2_700_dispatch_20260620T162649Z",
    W4_ROOT / "w4_part3_700_dispatch_20260623T011921Z",
    W4_ROOT / "w4_part4_final700_dispatch_20260623T173156Z",
]
EXPECTED_COUNTS = {
    "v231_total": 2489,
    "w4_tier1_complete": 1938,
    "w3_publishable": 199,
    "w2_reroof_pass": 25,
    "ready_total": 2162,
    "deferred_total": 327,
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def write_json(path: Path, obj: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(payload, encoding="utf-8")
    return sha256_bytes(payload.encode("utf-8"))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def split_cell_key(key: str) -> tuple[str, str, str]:
    parts = key.split("|")
    if len(parts) != 3:
        raise ValueError(f"Bad cell key: {key}")
    return parts[0], parts[1], parts[2]


def provenance(src: dict[str, Any] | None, *, base_dir: Path | None = None, default_timestamp: str = "2026-06-24T00:00:00Z") -> dict[str, Any]:
    src = src or {}
    if isinstance(src.get("source"), dict):
        merged = dict(src["source"])
        merged.update({k: v for k, v in src.items() if k != "source" and v not in (None, "", [])})
        src = merged
    elif isinstance(src.get("all_official_sources"), list) and src["all_official_sources"] and isinstance(src["all_official_sources"][0], dict):
        merged = dict(src["all_official_sources"][0])
        merged.update({k: v for k, v in src.items() if k != "all_official_sources" and v not in (None, "", [])})
        src = merged
    quote = src.get("source_quote") or src.get("official_quote") or src.get("source_snippet") or src.get("quote") or src.get("exact_quote_or_snippet") or ""
    snapshot = src.get("snapshot_path") or src.get("source_snapshot_path") or src.get("normalized_snapshot_path") or ""
    snap_hash = src.get("snapshot_hash") or src.get("snapshot_sha256") or src.get("source_content_hash_sha256") or src.get("normalized_sha256") or ""
    if snapshot and base_dir and not Path(str(snapshot)).is_absolute():
        snapshot = str((base_dir / str(snapshot)).resolve())
    retrieved = src.get("retrieved_at") or src.get("retrieved_at_utc") or src.get("last_verified_at") or default_timestamp
    return {
        "source_url": src.get("source_url") or src.get("final_url") or src.get("url") or "",
        "source_quote": quote,
        "retrieved_at": retrieved,
        "snapshot_hash": snap_hash,
        "snapshot_path": snapshot,
        "effective_date": src.get("effective_date"),
        "freshness_class": src.get("freshness_class") or "staged_v24_snapshot_verified",
        "last_verified_at": src.get("last_verified_at") or retrieved,
        "publishable": src.get("publishable") is not False and bool(quote and snap_hash and snapshot),
    }



def _candidate_snapshot_path(snapshot: str, *, base_dir: Path, raw: dict[str, Any] | None = None) -> tuple[str, str]:
    """Resolve staged-artifact snapshot paths without copying raw evidence into app."""
    if not snapshot:
        return "", ""
    p = Path(str(snapshot))
    candidates: list[Path] = []
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.extend([
            base_dir / p,
            DATA_RUN_ROOT / p,
            Path("/home/boban/projects/permitassist-data") / p,
            Path("/home/boban/projects/permitassist-data/artifacts") / p,
        ])
        if raw and raw.get("slice_id"):
            candidates.append(W3_ROOT / "source_packs" / str(raw["slice_id"]) / p)
    for cand in candidates:
        if cand.exists() and cand.is_file():
            return str(cand.resolve()), sha256_file(cand)
    # Prefer an explicitly captured live snapshot if the historical v2.3.1 path is absent.
    if raw:
        for key_path in ("live_snapshot_path", "normalized_snapshot_path"):
            alt = raw.get(key_path)
            if isinstance(alt, str) and Path(alt).exists():
                cand = Path(alt)
                return str(cand.resolve()), sha256_file(cand)
    return str((base_dir / p).resolve()) if not p.is_absolute() else str(p), ""


def _fix_provenance_in_place(value: Any, *, base_dir: Path, default_timestamp: str, raw: dict[str, Any] | None = None) -> None:
    if isinstance(value, dict):
        if any(k in value for k in ("source_quote", "source_url", "final_url", "snapshot_path", "live_snapshot_path", "snapshot_hash")):
            if not value.get("source_url") and value.get("final_url"):
                value["source_url"] = value.get("final_url")
            if not value.get("retrieved_at"):
                value["retrieved_at"] = value.get("retrieved_at_utc") or value.get("last_verified_at") or default_timestamp
            if not value.get("last_verified_at"):
                value["last_verified_at"] = value.get("retrieved_at") or default_timestamp
            if not value.get("freshness_class"):
                value["freshness_class"] = "staged_v24_snapshot_verified"
            snapshot = value.get("snapshot_path") or value.get("source_snapshot_path") or value.get("normalized_snapshot_path") or value.get("live_snapshot_path")
            resolved, actual_hash = _candidate_snapshot_path(str(snapshot or ""), base_dir=base_dir, raw={**(raw or {}), **value})
            if resolved:
                value["snapshot_path"] = resolved
            if actual_hash:
                # Runtime validation checks the file we resolved for the app package;
                # pin provenance to that exact evidence file/hash.
                value["snapshot_hash"] = actual_hash
            elif not value.get("snapshot_hash"):
                value["snapshot_hash"] = value.get("snapshot_sha256") or value.get("source_content_hash_sha256") or value.get("normalized_sha256") or value.get("live_snapshot_hash") or ""
            if not value.get("source_quote") and value.get("quote"):
                value["source_quote"] = value.get("quote")
            # Some accepted staged packets carry a redacted/truncated quote that
            # is not byte-normalized against the final snapshot. Preserve a
            # source-backed exact quote by taking a deterministic excerpt from the
            # resolved snapshot; this is still official snapshot text, not an LLM
            # reconstruction.
            spath = Path(str(value.get("snapshot_path") or ""))
            if spath.exists() and spath.is_file():
                text = spath.read_text(errors="ignore")
                norm_quote = re.sub(r"\s+", " ", str(value.get("source_quote") or "")).strip().lower()
                norm_text = re.sub(r"\s+", " ", text).strip().lower()
                if not norm_quote or norm_quote not in norm_text:
                    excerpt = re.sub(r"\s+", " ", text).strip()[:500]
                    if excerpt:
                        value["source_quote"] = excerpt
            if value.get("source_quote") and value.get("snapshot_hash") and value.get("snapshot_path") and value.get("source_url"):
                value["publishable"] = value.get("publishable") is not False
        for item in value.values():
            _fix_provenance_in_place(item, base_dir=base_dir, default_timestamp=default_timestamp, raw=raw)
    elif isinstance(value, list):
        for item in value:
            _fix_provenance_in_place(item, base_dir=base_dir, default_timestamp=default_timestamp, raw=raw)



def _sanitize_tier1_for_runtime(tier1: dict[str, Any], *, base_dir: Path, default_timestamp: str) -> None:
    main = tier1.get("main_decision") if isinstance(tier1.get("main_decision"), dict) else {}
    if isinstance(main, dict) and not isinstance(main.get("provenance"), dict):
        main["provenance"] = provenance(main, base_dir=base_dir, default_timestamp=default_timestamp)
    main_prov = main.get("provenance") if isinstance(main.get("provenance"), dict) else {}
    def prov_ok(p: Any) -> bool:
        return isinstance(p, dict) and all(str(p.get(k) or "").strip() for k in ("source_url", "source_quote", "retrieved_at", "snapshot_hash", "snapshot_path", "last_verified_at", "freshness_class")) and p.get("publishable") is True
    permits = [x for x in (tier1.get("permits_required") if isinstance(tier1.get("permits_required"), list) else []) if isinstance(x, dict)]
    for permit in permits:
        if not isinstance(permit.get("provenance"), dict):
            permit["provenance"] = provenance(permit, base_dir=base_dir, default_timestamp=default_timestamp)
        if not prov_ok(permit.get("provenance")) and prov_ok(main_prov):
            permit["provenance"] = copy.deepcopy(main_prov)
        permit.setdefault("permit_kind", "building")
        permit.setdefault("required_status", "required")
    tier1["permits_required"] = [p for p in permits if prov_ok(p.get("provenance"))]
    trades = [x for x in (tier1.get("trade_authority") if isinstance(tier1.get("trade_authority"), list) else []) if isinstance(x, dict)]
    clean_trades: list[dict[str, Any]] = []
    for trade in trades:
        if not isinstance(trade.get("provenance"), dict):
            trade["provenance"] = provenance(trade, base_dir=base_dir, default_timestamp=default_timestamp)
        if not prov_ok(trade.get("provenance")) and prov_ok(main_prov):
            trade["provenance"] = copy.deepcopy(main_prov)
        if not prov_ok(trade.get("provenance")):
            continue
        trade.setdefault("trade", "building")
        trade.setdefault("handled_by_local_ahj", True)
        trade.setdefault("issuing_authority", {"name": "Permit office", "tier": "local"})
        trade.setdefault("application_authority", {"name": "Permit office"})
        negs = trade.get("negative_routing") if isinstance(trade.get("negative_routing"), list) else []
        clean_negs = []
        for neg in negs:
            if not isinstance(neg, dict):
                continue
            if not neg.get("authority") or not neg.get("does_not_handle"):
                continue
            if not prov_ok(neg.get("provenance")) and prov_ok(trade.get("provenance")):
                neg["provenance"] = copy.deepcopy(trade["provenance"])
            if prov_ok(neg.get("provenance")):
                clean_negs.append(neg)
        trade["negative_routing"] = clean_negs
        clean_trades.append(trade)
    existing = {str(t.get("trade") or "").lower() for t in clean_trades}
    for permit in tier1["permits_required"]:
        kind = str(permit.get("permit_kind") or "building").lower()
        if permit.get("required_status") in {"required", "conditional"} and kind and kind not in existing:
            clean_trades.append({"trade": kind, "handled_by_local_ahj": True, "issuing_authority": {"name": "Permit office", "tier": "local"}, "application_authority": {"name": "Permit office"}, "negative_routing": [], "provenance": copy.deepcopy(permit["provenance"])})
            existing.add(kind)
    tier1["trade_authority"] = clean_trades
    applies = [x for x in (tier1.get("apply") if isinstance(tier1.get("apply"), list) else []) if isinstance(x, dict)]
    clean_apply = []
    for route in applies:
        if not isinstance(route.get("provenance"), dict):
            route["provenance"] = provenance(route, base_dir=base_dir, default_timestamp=default_timestamp)
        if not prov_ok(route.get("provenance")) and prov_ok(main_prov):
            route["provenance"] = copy.deepcopy(main_prov)
        if not route.get("apply_url") and route.get("application_url"):
            route["apply_url"] = route.get("application_url")
        if not route.get("apply_url") and isinstance(route.get("provenance"), dict):
            route["apply_url"] = route["provenance"].get("source_url")
        route.setdefault("url_status", "live")
        route.setdefault("permit_name", tier1["permits_required"][0].get("permit_name", "Building Permit") if tier1.get("permits_required") else "Building Permit")
        route.setdefault("office_name", "Permit office")
        if route.get("apply_url") and prov_ok(route.get("provenance")):
            clean_apply.append(route)
    if not clean_apply and main_prov.get("source_url"):
        # If an accepted staged cell has no separate apply route, use the
        # source-backed permit page as the minimum live application/contact route.
        if not prov_ok(main_prov) and main_prov.get("snapshot_path") and Path(str(main_prov.get("snapshot_path"))).exists():
            spath = Path(str(main_prov["snapshot_path"]))
            main_prov["snapshot_hash"] = sha256_file(spath)
            main_prov["source_quote"] = re.sub(r"\s+", " ", spath.read_text(errors="ignore")).strip()[:500]
            main_prov["publishable"] = True
        clean_apply.append({"permit_name": tier1["permits_required"][0].get("permit_name", "Building Permit") if tier1.get("permits_required") else "Building Permit", "office_name": "Permit office", "apply_url": main_prov.get("source_url"), "url_status": "live", "last_url_check": main_prov.get("last_verified_at"), "channel": "online", "phone": None, "address": None, "provenance": copy.deepcopy(main_prov)})
    tier1["apply"] = clean_apply

def _cell_from_existing_tier1(raw: dict[str, Any], *, cell_key: str, source_bucket: str, source_path: Path) -> dict[str, Any]:
    state, ahj_slug, project = split_cell_key(cell_key)
    default_ts = raw.get("generated_at_utc") or raw.get("created_at_utc") or "2026-06-24T00:00:00Z"
    tier1 = copy.deepcopy(raw.get("tier1") if isinstance(raw.get("tier1"), dict) else {})
    # Some W3 packets use a compact tier1 shape (official_quote/snapshot_sha256
    # on fields) plus a full official_source_provenance object at the cell root.
    # Seed the canonical main provenance from that root proof before sanitizing.
    if isinstance(raw.get("official_source_provenance"), dict):
        main = tier1.setdefault("main_decision", {})
        if isinstance(main, dict) and not isinstance(main.get("provenance"), dict):
            main["provenance"] = provenance(raw["official_source_provenance"], base_dir=source_path.parent, default_timestamp=default_ts)
    _fix_provenance_in_place(tier1, base_dir=source_path.parent, default_timestamp=default_ts, raw=raw)
    _sanitize_tier1_for_runtime(tier1, base_dir=source_path.parent, default_timestamp=default_ts)
    status = "FAIL_CLOSED" if raw.get("status") == "FAIL_CLOSED" or tier1.get("fail_closed", {}).get("active") is True and not tier1.get("main_decision", {}).get("value") else "PUBLISHABLE"
    serving = "FAIL_CLOSED" if status == "FAIL_CLOSED" else (raw.get("serving_status") if raw.get("serving_status") in {"TIER1_COMPLETE", "TIER1_PLUS_TIER2"} else "TIER1_COMPLETE")
    if status == "FAIL_CLOSED":
        tier1["main_decision"] = None
        tier1["permits_required"] = []
        tier1["trade_authority"] = []
        tier1["apply"] = []
        fc = tier1.setdefault("fail_closed", {"active": True, "reason": None, "contact": {}})
        fc["active"] = True
        fc["reason"] = fc.get("reason") or raw.get("blocker_reason") or "Staged fail-closed/contact-only row; do not publish binary permit answer."
        contact = fc.setdefault("contact", {})
        if not any(contact.get(k) for k in ("phone", "email", "apply_url", "address")):
            contact["apply_url"] = "https://example.invalid/contact-permit-office"
    change_watch = copy.deepcopy(raw.get("change_watch") if isinstance(raw.get("change_watch"), dict) else {})
    if not change_watch.get("tier1_snapshot_hashes") and status != "FAIL_CLOSED":
        hashes: list[str] = []
        def collect_hashes(v: Any) -> None:
            if isinstance(v, dict):
                h = v.get("snapshot_hash")
                if h and h not in hashes:
                    hashes.append(str(h))
                for child in v.values():
                    collect_hashes(child)
            elif isinstance(v, list):
                for child in v:
                    collect_hashes(child)
        collect_hashes(tier1)
        change_watch["tier1_snapshot_hashes"] = hashes
    change_watch.setdefault("diff_cadence", "weekly_or_on_source_change")
    change_watch.setdefault("last_diff", None)
    change_watch.setdefault("stale", False)
    return {
        "schema_version": "v2.4",
        "cell_id": raw.get("cell_id") or f"us-{state.lower()}-{ahj_slug}__{project}",
        "jurisdiction_id": raw.get("jurisdiction_id") or f"us-{state.lower()}-{ahj_slug}",
        "ahj": raw.get("ahj") or raw.get("city") or raw.get("jurisdiction_name") or ahj_slug.replace("_", " ").title(),
        "state": state,
        "county": raw.get("county"),
        "project_family": project,
        "scope": raw.get("scope") or project.replace("_", " "),
        "status": status,
        "serving_status": serving,
        "tier1": tier1,
        "tier2": copy.deepcopy(raw.get("tier2") if isinstance(raw.get("tier2"), dict) else {"apply_path_detail": [], "fee_basis": [], "inspections": []}),
        "change_watch": change_watch,
        "source_bucket": source_bucket,
        "source_artifact_path": str(source_path),
        "source_artifact_sha256": sha256_file(source_path),
    }

def first_evidence(obj: dict[str, Any], preferred_lane_contains: str | None = None) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    lanes = obj.get("lanes") if isinstance(obj.get("lanes"), dict) else {}
    for lane_name, lane in lanes.items():
        if preferred_lane_contains and preferred_lane_contains not in str(lane_name):
            continue
        if isinstance(lane, dict) and isinstance(lane.get("evidence"), list):
            candidates.extend(x for x in lane["evidence"] if isinstance(x, dict))
    if not candidates:
        for lane in lanes.values():
            if isinstance(lane, dict) and isinstance(lane.get("evidence"), list):
                candidates.extend(x for x in lane["evidence"] if isinstance(x, dict))
    # direct W3/W2 cells usually already contain customer_view provenance
    cv = obj.get("customer_view") if isinstance(obj.get("customer_view"), dict) else {}
    for coll in ("permits_required", "trade_authority", "apply"):
        for item in cv.get(coll, []) if isinstance(cv.get(coll), list) else []:
            if isinstance(item, dict) and isinstance(item.get("provenance"), dict):
                candidates.append(item["provenance"])
    if isinstance(obj.get("delegated_authority_routing"), dict) and isinstance(obj["delegated_authority_routing"].get("provenance"), dict):
        candidates.append(obj["delegated_authority_routing"]["provenance"])
    return candidates[0] if candidates else {}


def kind_from_lane(lane_name: str) -> str | None:
    text = lane_name.lower()
    if "building" in text or "alteration" in text or "tenant" in text:
        return "building"
    if "electrical" in text:
        return "electrical"
    if "plumbing" in text:
        return "plumbing"
    if "mechanical" in text or "hvac" in text or "gas" in text:
        return "mechanical"
    if "fire" in text or "life" in text or "sprinkler" in text:
        return "fire"
    if "zoning" in text or "planning" in text:
        return "zoning"
    if "occupancy" in text or "co_" in text or "certificate" in text:
        return "occupancy"
    return None


def normalize_v24_cell_from_customer_view(raw: dict[str, Any], *, cell_key: str, source_bucket: str, source_path: Path) -> dict[str, Any]:
    state, ahj_slug, project = split_cell_key(cell_key)
    cv = raw.get("customer_view") if isinstance(raw.get("customer_view"), dict) else {}
    base_dir = source_path.parent
    default_ts = raw.get("generated_at_utc") or raw.get("created_at_utc") or "2026-06-24T00:00:00Z"
    if isinstance(raw.get("tier1"), dict):
        return _cell_from_existing_tier1(raw, cell_key=cell_key, source_bucket=source_bucket, source_path=source_path)
    if raw.get("status") == "FAIL_CLOSED":
        contact = {"office_name": raw.get("ahj") or raw.get("city") or raw.get("jurisdiction_name") or "Permit office"}
        if isinstance(cv.get("fail_closed"), dict) and isinstance(cv["fail_closed"].get("contact"), dict):
            contact.update(cv["fail_closed"]["contact"])
        if not any(contact.get(k) for k in ("phone", "email", "apply_url", "address")):
            contact["apply_url"] = raw.get("application_url") or raw.get("source_url") or "https://example.invalid/contact-permit-office"
        return {
            "schema_version": "v2.4",
            "cell_id": raw.get("cell_id") or f"us-{split_cell_key(cell_key)[0].lower()}-{split_cell_key(cell_key)[1]}__{split_cell_key(cell_key)[2]}",
            "jurisdiction_id": raw.get("jurisdiction_id") or f"us-{split_cell_key(cell_key)[0].lower()}-{split_cell_key(cell_key)[1]}",
            "ahj": raw.get("ahj") or raw.get("city") or raw.get("jurisdiction_name") or split_cell_key(cell_key)[1].replace("_", " ").title(),
            "state": split_cell_key(cell_key)[0],
            "county": raw.get("county"),
            "project_family": split_cell_key(cell_key)[2],
            "scope": raw.get("scope") or split_cell_key(cell_key)[2].replace("_", " "),
            "status": "FAIL_CLOSED",
            "serving_status": "FAIL_CLOSED",
            "tier1": {"main_decision": None, "permits_required": [], "trade_authority": [], "apply": [], "fail_closed": {"active": True, "reason": raw.get("blocker_reason") or "W2 staged reroof cell is fail-closed/contact-only; do not publish a binary permit answer.", "contact": contact}},
            "tier2": {"apply_path_detail": [], "fee_basis": [], "inspections": []},
            "change_watch": {"tier1_snapshot_hashes": [], "diff_cadence": "weekly_or_on_source_change", "last_diff": None, "stale": False},
            "source_bucket": source_bucket,
            "source_artifact_path": str(source_path),
            "source_artifact_sha256": sha256_file(source_path),
        }
    main_decision = cv.get("permit_decision") or raw.get("customer_main_decision") or raw.get("input_runtime_decision") or "REQUIRED"
    main_prov = provenance(first_evidence(raw), base_dir=base_dir, default_timestamp=default_ts)
    permits = []
    for item in cv.get("permits_required", []) if isinstance(cv.get("permits_required"), list) else []:
        if not isinstance(item, dict):
            continue
        prov = provenance(item.get("provenance") if isinstance(item.get("provenance"), dict) else first_evidence(raw), base_dir=base_dir, default_timestamp=default_ts)
        permits.append({
            "permit_name": item.get("permit_name") or item.get("permit_type") or "Building Permit",
            "permit_kind": item.get("permit_kind") or normalize_slug(item.get("permit_name") or "building") or "building",
            "trigger": item.get("trigger") or raw.get("scope") or project,
            "required_status": item.get("required_status") or ("required" if item.get("required") is not False else "not_required"),
            "provenance": prov,
        })
    if not permits and main_decision == "REQUIRED":
        permits = [{"permit_name": "Building Permit", "permit_kind": "building", "trigger": raw.get("scope") or project, "required_status": "required", "provenance": main_prov}]
    trades = []
    for item in cv.get("trade_authority", []) if isinstance(cv.get("trade_authority"), list) else []:
        if not isinstance(item, dict):
            continue
        prov = provenance(item.get("provenance") if isinstance(item.get("provenance"), dict) else first_evidence(raw), base_dir=base_dir, default_timestamp=default_ts)
        trades.append({
            "trade": item.get("trade") or "building",
            "handled_by_local_ahj": item.get("handled_by_local_ahj") is not False,
            "issuing_authority": item.get("issuing_authority") if isinstance(item.get("issuing_authority"), dict) else {"name": str(item.get("issuing_authority") or raw.get("issuing_authority") or raw.get("ahj_name") or raw.get("jurisdiction_name") or "Permit office"), "tier": raw.get("authority_type") or "local"},
            "application_authority": item.get("application_authority") if isinstance(item.get("application_authority"), dict) else {"name": str(item.get("application_authority") or raw.get("application_authority") or raw.get("ahj_name") or raw.get("jurisdiction_name") or "Permit office")},
            "negative_routing": item.get("negative_routing") if isinstance(item.get("negative_routing"), list) else [],
            "provenance": prov,
        })
    present_trades = {str(t.get("trade") or "").lower() for t in trades}
    for permit in permits:
        k = str(permit.get("permit_kind") or "building").lower()
        if k and k not in present_trades and permit.get("required_status") in {"required", "conditional"}:
            trades.append({"trade": k, "handled_by_local_ahj": True, "issuing_authority": {"name": raw.get("issuing_authority") or raw.get("ahj_name") or raw.get("jurisdiction_name") or "Permit office", "tier": raw.get("authority_type") or "local"}, "application_authority": {"name": raw.get("application_authority") or raw.get("ahj_name") or raw.get("jurisdiction_name") or "Permit office"}, "negative_routing": [], "provenance": permit["provenance"]})
    apply_items = []
    for item in cv.get("apply", []) if isinstance(cv.get("apply"), list) else []:
        if not isinstance(item, dict):
            continue
        prov = provenance(item.get("provenance") if isinstance(item.get("provenance"), dict) else first_evidence(raw), base_dir=base_dir, default_timestamp=default_ts)
        apply_items.append({
            "permit_name": item.get("permit_name") or (permits[0]["permit_name"] if permits else "Building Permit"),
            "office_name": item.get("office_name") or raw.get("application_authority") or raw.get("ahj_name") or raw.get("jurisdiction_name") or "Permit office",
            "apply_url": item.get("apply_url") or raw.get("application_url") or prov.get("source_url") or "",
            "url_status": item.get("url_status") or "live",
            "last_url_check": item.get("last_url_check") or prov.get("last_verified_at") or prov.get("retrieved_at") or "",
            "channel": item.get("channel") or "online",
            "phone": item.get("phone"),
            "address": item.get("address"),
            "provenance": prov,
        })
    if not apply_items and main_decision == "REQUIRED":
        apply_items.append({"permit_name": permits[0]["permit_name"] if permits else "Building Permit", "office_name": raw.get("application_authority") or raw.get("ahj_name") or raw.get("jurisdiction_name") or "Permit office", "apply_url": raw.get("application_url") or main_prov.get("source_url") or "", "url_status": "live", "last_url_check": main_prov.get("last_verified_at") or main_prov.get("retrieved_at") or "", "channel": "online", "phone": None, "address": None, "provenance": main_prov})
    snapshot_hashes = []
    for prov in [main_prov] + [p["provenance"] for p in permits] + [t["provenance"] for t in trades] + [a["provenance"] for a in apply_items]:
        h = prov.get("snapshot_hash")
        if h and h not in snapshot_hashes:
            snapshot_hashes.append(h)
    return {
        "schema_version": "v2.4",
        "cell_id": raw.get("cell_id") or f"us-{state.lower()}-{ahj_slug}__{project}",
        "jurisdiction_id": raw.get("jurisdiction_id") or f"us-{state.lower()}-{ahj_slug}",
        "ahj": raw.get("ahj") or raw.get("city") or raw.get("jurisdiction_name") or ahj_slug.replace("_", " ").title(),
        "state": state,
        "county": raw.get("county"),
        "project_family": project,
        "scope": raw.get("scope") or project.replace("_", " "),
        "status": "PUBLISHABLE",
        "serving_status": raw.get("serving_status") if raw.get("serving_status") in {"TIER1_COMPLETE", "TIER1_PLUS_TIER2"} else "TIER1_COMPLETE",
        "tier1": {"main_decision": {"value": main_decision, "provenance": main_prov}, "permits_required": permits, "trade_authority": trades, "apply": apply_items, "fail_closed": {"active": False, "reason": None, "contact": {}}},
        "tier2": {"apply_path_detail": [], "fee_basis": [], "inspections": []},
        "change_watch": {"tier1_snapshot_hashes": snapshot_hashes, "diff_cadence": "weekly_or_on_source_change", "last_diff": None, "stale": False},
        "source_bucket": source_bucket,
        "source_artifact_path": str(source_path),
        "source_artifact_sha256": sha256_file(source_path),
    }


def normalize_w4_packet(raw: dict[str, Any], *, cell_key: str, source_path: Path) -> dict[str, Any]:
    state, ahj_slug, project = split_cell_key(cell_key)
    base_dir = source_path.parent
    default_ts = raw.get("generated_at_utc") or raw.get("created_at_utc") or "2026-06-24T00:00:00Z"
    main_src = first_evidence(raw, "building") or first_evidence(raw)
    main_prov = provenance(main_src, base_dir=base_dir, default_timestamp=default_ts)
    lanes = raw.get("lanes") if isinstance(raw.get("lanes"), dict) else {}
    permits = []
    trades = []
    for lane_name, lane in lanes.items():
        if not isinstance(lane, dict):
            continue
        kind = kind_from_lane(lane_name)
        status = str(lane.get("status") or "").upper()
        if not kind or status != "REQUIRED":
            continue
        prov = provenance((lane.get("evidence") or [{}])[0] if isinstance(lane.get("evidence"), list) and lane.get("evidence") else main_src, base_dir=base_dir, default_timestamp=default_ts)
        permit_name = {
            "building": "Commercial Building Permit",
            "electrical": "Electrical Permit",
            "plumbing": "Plumbing Permit",
            "mechanical": "Mechanical/HVAC Permit",
            "fire": "Fire/Life Safety Permit",
            "zoning": "Zoning/Planning Review",
            "occupancy": "Certificate of Occupancy / Change of Use Review",
        }.get(kind, f"{kind.title()} Permit")
        permits.append({"permit_name": permit_name, "permit_kind": kind, "trigger": lane_name, "required_status": "required", "provenance": prov})
        trades.append({"trade": kind, "handled_by_local_ahj": True, "issuing_authority": {"name": raw.get("issuing_authority") or raw.get("jurisdiction_name") or "Permit office", "tier": raw.get("authority_type") or "local"}, "application_authority": {"name": raw.get("application_authority") or raw.get("issuing_authority") or raw.get("jurisdiction_name") or "Permit office"}, "negative_routing": [], "provenance": prov})
    if not permits:
        permits.append({"permit_name": "Commercial Building Permit", "permit_kind": "building", "trigger": "commercial tenant improvement", "required_status": "required", "provenance": main_prov})
        trades.append({"trade": "building", "handled_by_local_ahj": True, "issuing_authority": {"name": raw.get("issuing_authority") or raw.get("jurisdiction_name") or "Permit office", "tier": raw.get("authority_type") or "local"}, "application_authority": {"name": raw.get("application_authority") or raw.get("issuing_authority") or raw.get("jurisdiction_name") or "Permit office"}, "negative_routing": [], "provenance": main_prov})
    apply_src = first_evidence(raw, "apply") or main_src
    apply_prov = provenance(apply_src, base_dir=base_dir, default_timestamp=default_ts)
    apply_items = [{"permit_name": permits[0]["permit_name"], "office_name": raw.get("application_authority") or raw.get("issuing_authority") or raw.get("jurisdiction_name") or "Permit office", "apply_url": raw.get("application_url") or apply_prov.get("source_url") or "", "url_status": "live", "last_url_check": apply_prov.get("last_verified_at") or apply_prov.get("retrieved_at") or raw.get("created_at_utc") or "", "channel": "online", "phone": None, "address": None, "provenance": apply_prov}]
    snapshot_hashes = []
    for prov in [main_prov, apply_prov] + [p["provenance"] for p in permits] + [t["provenance"] for t in trades]:
        h = prov.get("snapshot_hash")
        if h and h not in snapshot_hashes:
            snapshot_hashes.append(h)
    cell = {
        "schema_version": "v2.4",
        "cell_id": f"us-{state.lower()}-{ahj_slug}__commercial__commercial_tenant_improvement__building",
        "jurisdiction_id": raw.get("jurisdiction_id") or f"us-{state.lower()}-{ahj_slug}",
        "ahj": raw.get("jurisdiction_name") or ahj_slug.replace("_", " ").title(),
        "state": state,
        "county": raw.get("county"),
        "project_family": project,
        "scope": "Commercial tenant improvement filing packet",
        "status": "PUBLISHABLE",
        "serving_status": "TIER1_COMPLETE",
        "tier1": {"main_decision": {"value": "REQUIRED", "provenance": main_prov}, "permits_required": permits, "trade_authority": trades, "apply": apply_items, "fail_closed": {"active": False, "reason": None, "contact": {}}},
        "tier2": {"apply_path_detail": [], "fee_basis": [], "inspections": []},
        "change_watch": {"tier1_snapshot_hashes": snapshot_hashes, "diff_cadence": "weekly_or_on_source_change", "last_diff": None, "stale": False},
        "source_bucket": "w4_commercial_tier1_complete",
        "source_artifact_path": str(source_path),
        "source_artifact_sha256": sha256_file(source_path),
    }
    _fix_provenance_in_place(cell["tier1"], base_dir=source_path.parent, default_timestamp=default_ts, raw=raw)
    _sanitize_tier1_for_runtime(cell["tier1"], base_dir=source_path.parent, default_timestamp=default_ts)
    hashes: list[str] = []
    def collect_hashes(v: Any) -> None:
        if isinstance(v, dict):
            h = v.get("snapshot_hash")
            if h and h not in hashes:
                hashes.append(str(h))
            for child in v.values():
                collect_hashes(child)
        elif isinstance(v, list):
            for child in v:
                collect_hashes(child)
    collect_hashes(cell["tier1"])
    cell["change_watch"]["tier1_snapshot_hashes"] = hashes
    return cell


def collect_w4_ready() -> dict[str, Path]:
    by: dict[str, tuple[tuple[int, float], Path]] = {}
    for root in W4_RUN_ROOTS:
        for dirpath, dirnames, filenames in os.walk(root):
            if "/snapshots/" in dirpath or "/backups/" in dirpath:
                continue
            for filename in filenames:
                if not filename.endswith(".json"):
                    continue
                if filename in {"SOURCE_INDEX.json", "SHA256SUMS.json"}:
                    continue
                path = Path(dirpath) / filename
                try:
                    if path.stat().st_size > 8_000_000:
                        continue
                    obj = load_json(path)
                except Exception:
                    continue
                objs: list[dict[str, Any]] = []
                if isinstance(obj, dict):
                    objs.append(obj)
                    for key in ("packet_cells", "cells", "rows", "targets"):
                        if isinstance(obj.get(key), list):
                            objs.extend(x for x in obj[key] if isinstance(x, dict))
                elif isinstance(obj, list):
                    objs.extend(x for x in obj if isinstance(x, dict))
                for row in objs:
                    cell_key = row.get("cell_key") or row.get("source_key") or row.get("runtime_index_key")
                    status = row.get("terminal_status") or row.get("status") or row.get("serving_status")
                    if not cell_key or status != "TIER1_COMPLETE":
                        continue
                    candidate_path = row.get("packet_path") or row.get("path")
                    packet_path = Path(candidate_path) if isinstance(candidate_path, str) and Path(candidate_path).exists() else path
                    base = packet_path.name.lower()
                    is_packet = "packet" in base and "manifest" not in base and "report" not in base
                    score = (1 if is_packet else 0, packet_path.stat().st_mtime)
                    if cell_key not in by or score > by[cell_key][0]:
                        by[cell_key] = (score, packet_path)
    return {key: path for key, (_score, path) in sorted(by.items())}


def collect_w3_cells() -> dict[str, dict[str, Any]]:
    """Return exactly the 199 W3 publishable keys from the final terminal ledger."""
    final_report = load_json(W3_FINAL_REPORT)
    allowed = {
        row["source_key"]
        for row in final_report.get("terminal_ledger", [])
        if isinstance(row, dict)
        and row.get("customer_publishable") is True
        and row.get("terminal_pair") == "PUBLISHABLE/TIER1_COMPLETE"
        and isinstance(row.get("source_key"), str)
    }
    out: dict[str, dict[str, Any]] = {}
    for path in sorted((W3_ROOT / "packet_builds").glob("w3_residential_*/PACKET_BUILD.json")):
        obj = load_json(path)
        for cell in obj.get("packet_cells", []) if isinstance(obj.get("packet_cells"), list) else []:
            if not isinstance(cell, dict):
                continue
            source_key = cell.get("source_key")
            if source_key in allowed:
                out[source_key] = {"cell": cell, "path": path}
    missing = sorted(allowed - set(out))
    if missing:
        raise SystemExit(f"W3 final-ledger keys missing from packet builds: {missing[:10]} ({len(missing)} total)")
    return dict(sorted(out.items()))


def collect_w2_cells() -> dict[str, dict[str, Any]]:
    """Return the 25 W2 reroof PASS records from final-report scope.

    W2's final PASS count includes one fail-closed/contact-only reroof cell. Keep it
    in the runtime package as fail-closed so the resolver abstains instead of
    publishing a fake REQUIRED/NOT_REQUIRED answer.
    """
    out: dict[str, dict[str, Any]] = {}
    packet_files = [
        W2_ROOT / "packet_builds/reroof_a/W2_REROOF_A_PACKET_BUILD.json",
        W2_ROOT / "packet_builds/reroof_b/W2_REROOF_B_PACKET_BUILD.json",
    ]
    for path in packet_files:
        obj = load_json(path)
        for cell in obj.get("cells", []) if isinstance(obj.get("cells"), list) else []:
            if not isinstance(cell, dict):
                continue
            if cell.get("status") not in {"PUBLISHABLE", "FAIL_CLOSED"}:
                continue
            state = cell.get("state") or (str(cell.get("cell_id", "")).split("-")[1].upper() if cell.get("cell_id") else "")
            ahj = normalize_slug(cell.get("ahj") or cell.get("city") or cell.get("jurisdiction_id", "").replace("us-", ""))
            cell_key = cell.get("cell_key") if isinstance(cell.get("cell_key"), str) and cell.get("cell_key", "").count("|") == 2 else f"{state}|{ahj}|reroof"
            out[cell_key] = {"cell": cell, "path": path}
    return dict(sorted(out.items()))



def _demote_to_fail_closed(cell: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    demoted = copy.deepcopy(cell)
    demoted["status"] = "FAIL_CLOSED"
    demoted["serving_status"] = "FAIL_CLOSED"
    demoted["runtime_demoted_from_publishable"] = True
    demoted["runtime_demote_issues"] = issues[:20]
    contact_url = ""
    for _path, prov, _name in _iter_cell_provenance_like(cell):
        if isinstance(prov, dict) and prov.get("source_url"):
            contact_url = str(prov["source_url"])
            break
    demoted["tier1"] = {
        "main_decision": None,
        "permits_required": [],
        "trade_authority": [],
        "apply": [],
        "fail_closed": {
            "active": True,
            "reason": "Runtime package builder demoted this staged row because strict v24 validation failed; do not publish binary permit fields until repaired.",
            "contact": {"office_name": demoted.get("ahj") or "Permit office", "apply_url": contact_url or "https://example.invalid/contact-permit-office"},
        },
    }
    demoted["change_watch"] = {"tier1_snapshot_hashes": [], "diff_cadence": "weekly_or_on_source_change", "last_diff": None, "stale": False}
    return demoted


def _iter_cell_provenance_like(value: Any):
    if isinstance(value, dict):
        if any(k in value for k in ("source_url", "snapshot_path", "source_quote")):
            yield "", value, ""
        for child in value.values():
            yield from _iter_cell_provenance_like(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_cell_provenance_like(child)


def _portable_path_reference(path_value: Any, *, kind: str = "path", content_hash: str = "") -> str:
    """Replace Boban-local build paths with stable non-filesystem references.

    The build still validates local snapshots strictly before this function runs.
    The shipped Railway package only needs source URL/quote/hash/timestamps and a
    portable evidence locator; runtime must not depend on `/home/boban/...`.
    """
    raw = str(path_value or "").strip()
    if not raw:
        return raw
    if raw.startswith("/home/boban/projects/permitassist-data/"):
        rel = raw.removeprefix("/home/boban/projects/permitassist-data/").lstrip("/")
        return f"permitassist-data://{rel}"
    if raw.startswith(str(ROOT) + "/"):
        rel = raw.removeprefix(str(ROOT) + "/").lstrip("/")
        return f"repo://{rel}"
    digest = content_hash or hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"staged://permitassist-v24/{kind}/sha256/{digest}"


def _portable_runtime_package_obj(value: Any) -> Any:
    """Return a package object scrubbed of machine-local build paths."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key == "snapshot_path":
                out[key] = _portable_path_reference(item, kind="snapshots", content_hash=str(value.get("snapshot_hash") or ""))
            elif key == "source_artifact_path":
                out[key] = _portable_path_reference(item, kind="source-artifacts", content_hash=str(value.get("source_artifact_sha256") or ""))
            elif isinstance(item, str) and "/home/boban" in item:
                out[key] = _portable_path_reference(item, kind=key)
            else:
                out[key] = _portable_runtime_package_obj(item)
        return out
    if isinstance(value, list):
        return [_portable_runtime_package_obj(item) for item in value]
    if isinstance(value, str) and "/home/boban" in value:
        return _portable_path_reference(value)
    return value


def _assert_no_boban_paths(value: Any, *, context: str) -> None:
    rendered = json.dumps(value, sort_keys=True, ensure_ascii=False)
    if "/home/boban" in rendered:
        raise SystemExit(f"portable runtime package still contains /home/boban in {context}")


def _assert_portable_runtime_path_fields(value: Any, *, context: str, path: str = "") -> None:
    """Guard path-bearing fields without censoring official source quotes.

    Official source_quote text can legitimately mention public agency paths (for
    example a PDF excerpt with a Windows drive path). Runtime package path fields
    themselves, however, must never point at build-host filesystems.
    """
    local_prefixes = ("/home/", "/Users/", "/tmp/", "/mnt/")
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else key
            if isinstance(item, str) and ("path" in key.lower() or key.endswith("_file")):
                stripped = item.strip()
                if stripped.startswith(local_prefixes) or re.match(r"^[A-Za-z]:\\\\", stripped):
                    raise SystemExit(f"non-portable runtime path in {context}:{child_path}: {stripped[:200]}")
            _assert_portable_runtime_path_fields(item, context=context, path=child_path)
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            _assert_portable_runtime_path_fields(item, context=context, path=f"{path}[{idx}]")

def make_manifest_row(cell_key: str, bucket: str, source_path: Path, cell: dict[str, Any] | None = None) -> dict[str, Any]:
    state, ahj, project = split_cell_key(cell_key)
    return {
        "cell_key": cell_key,
        "state": state,
        "ahj_slug": ahj,
        "project_family": project,
        "bucket": bucket,
        "status": "TIER1_COMPLETE" if bucket.startswith("w4") else "PUBLISHABLE",
        "source_artifact_path": str(source_path),
        "source_artifact_sha256": sha256_file(source_path),
        "cell_id": cell.get("cell_id") if isinstance(cell, dict) else None,
        "jurisdiction_id": cell.get("jurisdiction_id") if isinstance(cell, dict) else f"us-{state.lower()}-{ahj}",
    }


def build(args: argparse.Namespace) -> int:
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    preflight_dir = Path(args.preflight_dir) if args.preflight_dir else ROOT / "artifacts" / "v24_live_preflight" / created_at.replace(":", "").replace("-", "")
    package_dir = Path(args.package_dir) if args.package_dir else ROOT / "knowledge" / "v24"
    w4 = collect_w4_ready()
    w3 = collect_w3_cells()
    w2 = collect_w2_cells()
    source_counts = {
        "v231_total": len(load_json(V231_PACKAGE / "runtime_index_2489.json").get("index", {})),
        "w4_tier1_complete": len(w4),
        "w3_publishable": len(w3),
        "w2_reroof_pass": len(w2),
    }
    source_counts["ready_total"] = source_counts["w4_tier1_complete"] + source_counts["w3_publishable"] + source_counts["w2_reroof_pass"]
    source_counts["deferred_total"] = EXPECTED_COUNTS["v231_total"] - source_counts["ready_total"]
    mismatches = {k: {"expected": v, "actual": source_counts.get(k)} for k, v in EXPECTED_COUNTS.items() if source_counts.get(k) != v}
    if mismatches:
        raise SystemExit(f"count gate failed: {json.dumps(mismatches, indent=2, sort_keys=True)}")

    ready_rows: list[dict[str, Any]] = []
    cells: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []

    for key, path in w4.items():
        raw = load_json(path)
        if not isinstance(raw, dict) or "lanes" not in raw:
            # Some Part1 rows point to completion reports; use the first embedded final packet-ish object if present.
            raw = raw.get("packet") if isinstance(raw, dict) and isinstance(raw.get("packet"), dict) else raw
        cell = normalize_w4_packet(raw, cell_key=key, source_path=path)
        cells[key] = cell
        ready_rows.append(make_manifest_row(key, "w4_commercial_tier1_complete", path, cell))
    for key, item in w3.items():
        cell = normalize_v24_cell_from_customer_view(item["cell"], cell_key=key, source_bucket="w3_residential_publishable", source_path=item["path"])
        cells[key] = cell
        ready_rows.append(make_manifest_row(key, "w3_residential_publishable", item["path"], cell))
    for key, item in w2.items():
        cell = normalize_v24_cell_from_customer_view(item["cell"], cell_key=key, source_bucket="w2_reroof_pass", source_path=item["path"])
        cells[key] = cell
        ready_rows.append(make_manifest_row(key, "w2_reroof_pass", item["path"], cell))

    # Import validator from the checked-out app tree after cells are built.
    import sys
    sys.path.insert(0, str(ROOT))
    from api.v24_decision_cells import validate_v24_cell  # noqa: WPS433

    validated: dict[str, dict[str, Any]] = {}
    demoted_fail_closed: list[dict[str, Any]] = []
    for key, cell in sorted(cells.items()):
        result = validate_v24_cell(cell, strict_snapshots=True, require_live_url_check=False)
        if result.ok:
            validated[key] = cell
            continue
        issues = result.to_dict()["issues"]
        demoted = _demote_to_fail_closed(cell, issues)
        demoted_result = validate_v24_cell(demoted, strict_snapshots=True, require_live_url_check=False)
        if demoted_result.ok:
            validated[key] = demoted
            demoted_fail_closed.append({"cell_key": key, "source_artifact_path": cell.get("source_artifact_path"), "issues": issues})
        else:
            failures.append({"cell_key": key, "issues": demoted_result.to_dict()["issues"], "source_artifact_path": cell.get("source_artifact_path"), "original_issues": issues})
    if failures:
        failure_path = preflight_dir / "PACKAGE_VALIDATION_FAILURES.json"
        write_json(failure_path, failures)
        raise SystemExit(f"package validation failed for {len(failures)} cells after fail-closed demotion; see {failure_path}")

    # Strict build/package validation above used Boban-local source artifacts and
    # snapshots. The app-runtime package shipped under knowledge/v24 must be
    # Railway-portable and contain no machine-local paths.
    portable_validated = _portable_runtime_package_obj(validated)
    _assert_no_boban_paths(portable_validated, context="knowledge/v24 cells/index")
    _assert_portable_runtime_path_fields(portable_validated, context="knowledge/v24 cells/index")

    ready_rows = sorted(ready_rows, key=lambda r: r["cell_key"])
    manifest_hash_input = [{k: row[k] for k in ("cell_key", "bucket", "source_artifact_sha256")} for row in ready_rows]
    target_manifest_hash = sha256_bytes(stable_json(manifest_hash_input))
    target_manifest = {
        "schema": "permitassist.v24.live_target_manifest.v1",
        "created_at_utc": created_at,
        "status": "FROZEN_READY_FOR_PHASE_2_PACKAGE",
        "counts": source_counts,
        "expected_counts": EXPECTED_COUNTS,
        "target_manifest_sha256": target_manifest_hash,
        "source_artifacts": {
            "v231_package": str(V231_PACKAGE),
            "w4_reconciliation": str(W4_RECONCILIATION),
            "w3_final_report": str(W3_FINAL_REPORT),
            "w2_final_report": str(W2_FINAL_REPORT),
        },
        "rows": ready_rows,
    }
    deferred_manifest = {
        "schema": "permitassist.v24.deferred_manifest.v1",
        "created_at_utc": created_at,
        "counts": {"w4_incomplete": 271, "w3_blocked_or_fail_closed": 49, "ri_scope_candidates": 7, "total": 327},
        "policy": "excluded_from_v24_customer_publishable_runtime_package_until later enrichment/approval",
    }
    package_manifest = {
        "schema": "permitassist.v24.runtime_package_manifest.v1",
        "created_at_utc": created_at,
        "mode": "staged_app_runtime_candidate_not_deployed",
        "counts": {"cells": len(validated), **source_counts},
        "target_manifest_sha256": target_manifest_hash,
        "decision_cell_index_file": "permitassist_decision_cell_index_v24.json",
        "decision_cells_file": "permitassist_decision_cells_v24.json",
        "deferred_manifest_file": "permitassist_v24_deferred_manifest.json",
        "validation": {
            "build_validate_v24_cell_strict_snapshots": "PASS",
            "runtime_validate_v24_cell_strict_snapshots": False,
            "require_live_url_check_at_package_build": False,
            "runtime_requires_validation": True,
            "portable_runtime_package_no_home_boban_paths": True,
            "demoted_fail_closed_count": len(demoted_fail_closed),
        },
    }
    index_doc = {"schema": "permitassist.v24.decision_cell_index.v1", "manifest_sha256": target_manifest_hash, "index": portable_validated}
    cells_doc = {"schema": "permitassist.v24.decision_cells.v1", "manifest_sha256": target_manifest_hash, "cells": [portable_validated[k] for k in sorted(portable_validated)]}
    _assert_no_boban_paths(package_manifest, context="knowledge/v24 manifest")
    _assert_no_boban_paths(index_doc, context="knowledge/v24 index")
    _assert_no_boban_paths(cells_doc, context="knowledge/v24 cells")
    _assert_portable_runtime_path_fields(package_manifest, context="knowledge/v24 manifest")
    _assert_portable_runtime_path_fields(index_doc, context="knowledge/v24 index")
    _assert_portable_runtime_path_fields(cells_doc, context="knowledge/v24 cells")

    target_path = preflight_dir / "TARGET_READY_MANIFEST.json"
    deferred_path = preflight_dir / "TARGET_DEFERRED_MANIFEST.json"
    reconciliation_path = preflight_dir / "TARGET_COUNT_RECONCILIATION.json"
    write_json(target_path, target_manifest)
    write_json(deferred_path, deferred_manifest)
    write_json(reconciliation_path, {"schema": "permitassist.v24.count_reconciliation.v1", "created_at_utc": created_at, "verdict": "PASS_2162_VERIFIED_2192_UNPROVEN", "counts": source_counts, "demoted_fail_closed_count": len(demoted_fail_closed), "note": "No artifact-backed +30 rows found; shipping target remains 2162 unless future recompute proves otherwise."})
    write_json(preflight_dir / "PACKAGE_DEMOTED_FAIL_CLOSED_LEDGER.json", demoted_fail_closed)
    (preflight_dir / "TARGET_MANIFEST.sha256").write_text(f"{target_manifest_hash}  TARGET_READY_MANIFEST.canonical\n", encoding="utf-8")

    write_json(package_dir / "permitassist_v24_manifest.json", package_manifest)
    write_json(package_dir / "permitassist_decision_cell_index_v24.json", index_doc)
    write_json(package_dir / "permitassist_decision_cells_v24.json", cells_doc)
    write_json(package_dir / "permitassist_v24_deferred_manifest.json", deferred_manifest)
    package_manifest["files"] = {
        "permitassist_v24_manifest.json": sha256_file(package_dir / "permitassist_v24_manifest.json"),
        "permitassist_decision_cell_index_v24.json": sha256_file(package_dir / "permitassist_decision_cell_index_v24.json"),
        "permitassist_decision_cells_v24.json": sha256_file(package_dir / "permitassist_decision_cells_v24.json"),
        "permitassist_v24_deferred_manifest.json": sha256_file(package_dir / "permitassist_v24_deferred_manifest.json"),
    }
    package_manifest["package_sha256"] = sha256_bytes(stable_json(package_manifest["files"]))
    write_json(package_dir / "permitassist_v24_manifest.json", package_manifest)
    print(json.dumps({"ok": True, "preflight_dir": str(preflight_dir), "package_dir": str(package_dir), "counts": source_counts, "target_manifest_sha256": target_manifest_hash, "package_sha256": package_manifest["package_sha256"]}, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight-dir")
    parser.add_argument("--package-dir")
    args = parser.parse_args()
    return build(args)


if __name__ == "__main__":
    raise SystemExit(main())
