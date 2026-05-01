#!/usr/bin/env python3
"""Summarize cached PermitAssist stress eval rows without making engine calls.

Focus: keep reliability/infra failures separate from engine-quality scores so a
502/timeout does not hide or distort permit-quality results.

Input supports:
  - JSON list of rows
  - JSON object with a `results`, `rows`, or `cases` list
  - CSV with columns such as id, score, status/http_status, error/exception

Usage:
  python3 scripts/summarize_stress_eval.py path/to/raw.json
  python3 scripts/summarize_stress_eval.py path/to/raw.csv --json
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import mean
from typing import Any

TRANSIENT_INFRA_STATUSES = {0, 408, 425, 429, 500, 502, 503, 504}


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def row_id(row: dict[str, Any]) -> str:
    for key in ("id", "case_id", "slug", "name"):
        value = row.get(key)
        if value:
            return str(value)
    return "unknown"


def row_status(row: dict[str, Any]) -> int | None:
    for key in ("status", "http_status", "status_code", "response_status"):
        status = _as_int(row.get(key))
        if status is not None:
            return status
    meta = row.get("_meta")
    if isinstance(meta, dict):
        return _as_int(meta.get("status") or meta.get("http_status"))
    return None


def is_transient_infra_failure(row: dict[str, Any]) -> bool:
    status = row_status(row)
    if status in TRANSIENT_INFRA_STATUSES:
        return True
    err = " ".join(str(row.get(k) or "") for k in ("error", "exception", "failure", "message")).lower()
    explicit_tokens = (
        "timeout", "timed out", "gateway timeout", "bad gateway", "service unavailable",
        "502", "503", "504", "connection reset", "connection refused",
        "connection aborted", "connection timeout", "connect timeout",
    )
    return any(token in err for token in explicit_tokens)


def row_score(row: dict[str, Any]) -> float | None:
    for key in ("score", "overall_score", "quality_score", "total_score"):
        score = _as_float(row.get(key))
        if score is not None:
            return score
    scoring = row.get("scoring") or row.get("score_detail")
    if isinstance(scoring, dict):
        for key in ("score", "overall_score", "quality_score", "total"):
            score = _as_float(scoring.get(key))
            if score is not None:
                return score
    return None


def load_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as fh:
            return [dict(row) for row in csv.DictReader(fh)]

    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("results", "rows", "cases"):
            rows = data.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        return [data]
    raise ValueError(f"Unsupported input structure in {path}")


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    infra = [row for row in rows if is_transient_infra_failure(row)]
    engine_rows = [row for row in rows if not is_transient_infra_failure(row)]
    engine_scores = [score for row in engine_rows if (score := row_score(row)) is not None]
    all_scores = [score for row in rows if (score := row_score(row)) is not None]
    under_85 = [row for row in engine_rows if (score := row_score(row)) is not None and score < 85]
    under_80 = [row for row in engine_rows if (score := row_score(row)) is not None and score < 80]

    return {
        "total_rows": len(rows),
        "infra_failure_count": len(infra),
        "infra_failure_ids": [row_id(row) for row in infra],
        "engine_quality_count": len(engine_rows),
        "mean_all_rows_with_scores": round(mean(all_scores), 2) if all_scores else None,
        "mean_engine_quality_only": round(mean(engine_scores), 2) if engine_scores else None,
        "under_85_count_engine_only": len(under_85),
        "under_85_ids_engine_only": [row_id(row) for row in under_85],
        "under_80_count_engine_only": len(under_80),
        "under_80_ids_engine_only": [row_id(row) for row in under_80],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path, help="Cached stress eval JSON/CSV path")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args()

    rows = load_rows(args.path)
    summary = summarize(rows)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"total_rows: {summary['total_rows']}")
        print(f"infra_failure_count: {summary['infra_failure_count']}")
        if summary["infra_failure_ids"]:
            print("infra_failure_ids: " + ", ".join(summary["infra_failure_ids"]))
        print(f"engine_quality_count: {summary['engine_quality_count']}")
        print(f"mean_all_rows_with_scores: {summary['mean_all_rows_with_scores']}")
        print(f"mean_engine_quality_only: {summary['mean_engine_quality_only']}")
        print(f"under_85_count_engine_only: {summary['under_85_count_engine_only']}")
        if summary["under_85_ids_engine_only"]:
            print("under_85_ids_engine_only: " + ", ".join(summary["under_85_ids_engine_only"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
