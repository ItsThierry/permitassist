#!/usr/bin/env python3
"""Benchmark PermitIQ Tier A Serper claim enrichment sequential vs parallel.

This intentionally benchmarks the Serper trust-layer stage in isolation using a
fixed synthetic HTTP delay. That keeps the run deterministic, avoids spending
live Serper credits, and measures exactly the regression this refactor targets:
five claim queries per lookup that used to run serially.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OPENAI_API_KEY", "benchmark-not-used")

import api.research_engine as engine  # noqa: E402


class FakeResponse:
    status_code = 200
    headers = {}

    def __init__(self, query: str):
        self.query = query

    def json(self):
        slug = "-".join(self.query.lower().split()[:6])
        return {
            "organic": [
                {
                    "title": f"Official permit source for {self.query[:48]}",
                    "link": f"https://example.gov/{slug}",
                    "snippet": "Official city permit guidance, fee schedule, contact, code, documents, and inspection details.",
                }
            ]
        }


SAMPLE_LOOKUPS = [
    ("Pasadena", "CA", "HVAC condenser changeout"),
    ("Houston", "TX", "HVAC condenser changeout"),
    ("Phoenix", "AZ", "solar PV install"),
    ("Atlanta", "GA", "water heater swap"),
    ("Dallas", "TX", "panel upgrade"),
]


def base_result() -> dict:
    return {
        "fee_range": "$450 - $750",
        "permits_required": [{"permit_type": "Trade Permit", "required": True}],
        "companion_permits": [],
        "sources": [],
    }


def install_fake_serper() -> None:
    engine.SERPER_API_KEY = "benchmark-serper-key"
    engine._get_cached_serper_source = lambda city, state, claim_type: None
    engine._set_cached_serper_source = lambda city, state, claim_type, query, source: None

    def fake_post(url, headers=None, json=None, timeout=15, max_retries=2):
        time.sleep(0.22)
        return FakeResponse((json or {}).get("q", "permit source"))

    engine._http_post_with_backoff = fake_post


def time_lookup(label: str, fn, city: str, state: str, job: str) -> tuple[float, dict]:
    start = time.perf_counter()
    result = fn(base_result(), job, city, state)
    elapsed = time.perf_counter() - start
    if result.get("serper_credits_used") != 5:
        raise AssertionError(f"{label} {city}, {state} used {result.get('serper_credits_used')} credits, expected 5")
    if result.get("sources_status") != "serper_verified":
        raise AssertionError(f"{label} {city}, {state} status {result.get('sources_status')}, expected serper_verified")
    return elapsed, result


def main() -> int:
    install_fake_serper()

    sequential_times = []
    parallel_times = []

    print("PermitIQ Serper parallel benchmark")
    print("Synthetic Serper HTTP latency: 0.22s per claim query; 5 claim queries per lookup")
    print()
    print(f"{'Lookup':45} {'Sequential':>12} {'Parallel':>12} {'Speedup':>9}")
    print("-" * 82)

    for city, state, job in SAMPLE_LOOKUPS:
        seq_t, _ = time_lookup("sequential", engine._enrich_result_with_serper_sources_sequential, city, state, job)
        par_t, _ = time_lookup("parallel", engine.enrich_result_with_serper_sources, city, state, job)
        sequential_times.append(seq_t)
        parallel_times.append(par_t)
        lookup = f"{city} {state} {job}"
        speedup = seq_t / par_t if par_t else float("inf")
        print(f"{lookup:45} {seq_t:10.3f}s {par_t:10.3f}s {speedup:8.2f}x")

    seq_total = sum(sequential_times)
    par_total = sum(parallel_times)
    total_speedup = seq_total / par_total if par_total else float("inf")
    print("-" * 82)
    print(f"{'TOTAL':45} {seq_total:10.3f}s {par_total:10.3f}s {total_speedup:8.2f}x")
    print()
    print(f"Sequential total: {seq_total:.3f}s")
    print(f"Parallel total:   {par_total:.3f}s")
    print(f"Speedup:          {total_speedup:.2f}x")

    if total_speedup < 2.5:
        raise AssertionError(f"Parallel Serper enrichment speedup {total_speedup:.2f}x < required 2.50x")
    print("PASS: parallel Serper enrichment is >= 2.5x faster than sequential")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
