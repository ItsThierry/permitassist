#!/usr/bin/env python3
"""Regression tests for cached stress eval summarization."""

import csv
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import summarize_stress_eval as summary


def test_summarize_separates_infra_failures_from_engine_quality():
    rows = [
        {"id": "ok_high", "score": 100, "status": 200},
        {"id": "ok_low", "score": 70, "status": 200},
        {"id": "zero_engine_score", "score": 0, "status": 200},
        {"id": "transport_error_with_200", "score": 5, "status": 200, "error": "connection reset by peer"},
        {"id": "park_city_retry_needed", "score": 10, "status": 502},
        {"id": "timeout_case", "score": 20, "error": "Gateway timeout"},
    ]
    out = summary.summarize(rows)
    assert out["total_rows"] == 6
    assert out["infra_failure_count"] == 3
    assert out["infra_failure_ids"] == ["transport_error_with_200", "park_city_retry_needed", "timeout_case"]
    assert out["engine_quality_count"] == 3
    assert out["mean_all_rows_with_scores"] == 34.17
    assert out["mean_engine_quality_only"] == 56.67
    assert out["under_85_ids_engine_only"] == ["ok_low", "zero_engine_score"]
    assert out["under_80_ids_engine_only"] == ["ok_low", "zero_engine_score"]


def test_load_rows_supports_json_object_and_csv(tmp_path):
    json_path = tmp_path / "raw.json"
    json_path.write_text(json.dumps({"results": [{"id": "a", "score": 90}]}), encoding="utf-8")
    assert summary.load_rows(json_path) == [{"id": "a", "score": 90}]

    csv_path = tmp_path / "raw.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["id", "score", "status"])
        writer.writeheader()
        writer.writerow({"id": "b", "score": "80", "status": "200"})
    assert summary.load_rows(csv_path) == [{"id": "b", "score": "80", "status": "200"}]
