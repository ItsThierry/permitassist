#!/usr/bin/env python3
"""Local-only Phase 4 data coverage report for Fable 5 Session 2.

This script performs no network calls and does not open lead/PII files. It only
summarizes repo-local public/configuration artifacts so data-expansion work can
be planned without mutating production databases or paid services.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ADVERTISED_VERTICALS = ("solar", "hvac", "roofing", "plumbing", "electrical", "water heater")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def ahj_contact_coverage() -> dict[str, Any]:
    path = ROOT / "data" / "ahj_records.json"
    data = _load_json(path) or {}
    statuses: collections.Counter[str] = collections.Counter()
    missing_phone_or_address: list[str] = []
    for key, record in sorted(data.items()):
        contact = record.get("contact") if isinstance(record, dict) else {}
        if not isinstance(contact, dict):
            contact = {}
        status = str(contact.get("contact_status") or "unverified").lower()
        statuses[status] += 1
        if not (str(contact.get("phone") or "").strip() or str(contact.get("address") or "").strip()):
            missing_phone_or_address.append(key)
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": _sha256(path),
        "record_count": len(data),
        "status_counts": dict(sorted(statuses.items())),
        "verified_count": statuses.get("verified", 0),
        "coverage_gap_to_top25_verified": max(0, 25 - statuses.get("verified", 0)),
        "missing_phone_or_address": missing_phone_or_address,
    }


def v24_coverage() -> dict[str, Any]:
    path = ROOT / "knowledge" / "v24" / "permitassist_decision_cells_v24.json"
    data = _load_json(path) or {}
    cells = data.get("cells") if isinstance(data, dict) else []
    if not isinstance(cells, list):
        cells = []
    project_family_counts: collections.Counter[str] = collections.Counter()
    status_counts: collections.Counter[str] = collections.Counter()
    vertical_hits = {vertical: 0 for vertical in ADVERTISED_VERTICALS}
    tier1_complete = 0
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        text = " ".join(str(cell.get(k) or "") for k in ("cell_id", "project_family", "scope", "source_bucket")).lower()
        project_family_counts[str(cell.get("project_family") or "unknown")] += 1
        status_counts[str(cell.get("serving_status") or cell.get("status") or "unknown")] += 1
        if str(cell.get("serving_status") or "").upper() == "TIER1_COMPLETE":
            tier1_complete += 1
        for vertical in ADVERTISED_VERTICALS:
            if vertical in text or (vertical == "hvac" and any(tok in text for tok in ("mechanical", "heat pump", "mini split"))):
                vertical_hits[vertical] += 1
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": _sha256(path),
        "cell_count": len(cells),
        "tier1_complete_count": tier1_complete,
        "tier1_complete_percent": round((tier1_complete / len(cells) * 100), 2) if cells else 0,
        "project_family_counts_top20": dict(project_family_counts.most_common(20)),
        "serving_status_counts": dict(status_counts.most_common()),
        "advertised_vertical_string_hits": vertical_hits,
    }


def verified_city_artifacts() -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    for rel in ("data/verified_cities.json", "knowledge/verified_cities.json"):
        path = ROOT / rel
        data = _load_json(path)
        artifacts[rel] = {"exists": path.exists(), "sha256": _sha256(path), "count": len(data) if hasattr(data, "__len__") and data is not None else 0}
    for rel in ("data/verified_cities.db", "knowledge/verified_cities.db"):
        path = ROOT / rel
        info = {"exists": path.exists(), "sha256": _sha256(path), "tables": {}, "error": ""}
        if path.exists():
            try:
                con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                for (table,) in con.execute("select name from sqlite_master where type='table'").fetchall():
                    info["tables"][table] = con.execute(f"select count(*) from {table}").fetchone()[0]
                con.close()
            except Exception as exc:  # pragma: no cover - report-only fallback
                info["error"] = f"{exc.__class__.__name__}: {exc}"
        artifacts[rel] = info
    return artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "artifacts" / "titi_build_20260706_session2" / "phase4_data_coverage_report.json"))
    args = parser.parse_args()
    report = {
        "schema_version": "permitassist_phase4_data_coverage_report.v1",
        "network_calls": False,
        "prod_mutations": False,
        "lead_or_pii_files_opened": False,
        "ahj_contacts": ahj_contact_coverage(),
        "v24_cells": v24_coverage(),
        "verified_city_artifacts": verified_city_artifacts(),
        "phase4_next_actions": [
            "T-070: expand AHJ verified contacts from current verified_count toward top-25; verify each phone/address from official source before marking verified.",
            "T-071: add source-backed v24 cells for advertised verticals; current string-hit counts show coverage gaps by vertical.",
            "T-073: canonicalize verified_cities artifacts; DBs have matching counts but JSON artifacts differ in size/count and need generation-script ownership.",
            "T-076/T-077: keep SEO/city-fact changes gated until claims are source-backed and effective-date lint exists.",
        ],
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(out), "ahj_records": report["ahj_contacts"]["record_count"], "v24_cells": report["v24_cells"]["cell_count"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
