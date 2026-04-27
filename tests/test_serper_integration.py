#!/usr/bin/env python3
"""Tier A trust-layer tests for Serper grounding + permit hedging."""

import os
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.research_engine as engine


class FakeResponse:
    def __init__(self, status_code=200, organic=None):
        self.status_code = status_code
        self._organic = organic or []
        self.headers = {}

    def json(self):
        return {"organic": self._organic}


@pytest.fixture(autouse=True)
def isolated_serper(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "SERPER_API_KEY", "test-serper-key")
    monkeypatch.setattr(engine, "SERPER_CACHE_DB", str(tmp_path / "serper_cache.db"))
    yield


def _base_result():
    return {
        "fee_range": "$450 - $750",
        "permits_required": [
            {"permit_type": "Mechanical Permit — HVAC Replacement (Residential)", "required": True}
        ],
        "companion_permits": [],
        "sources": [],
    }


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://permitmint.com/texas/austin/hvac-permit", engine.SOURCE_CLASS_EXCLUDED),
        ("https://abc.austintexas.gov/web/permit/public-search", engine.SOURCE_CLASS_OFFICIAL),
        ("https://www.roofingcontractor.com/articles/permit-guide", engine.SOURCE_CLASS_SUPPLEMENTARY),
        ("https://permitai.us/austin-tx-permits", engine.SOURCE_CLASS_EXCLUDED),
        ("https://codes.iccsafe.org/content/IBC2024P1", engine.SOURCE_CLASS_EXCLUDED),
    ],
)
def test_classify_source_url(url, expected):
    assert engine.classify_source_url(url) == expected


def test_serper_attaches_claim_sources(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=15, max_retries=2):
        q = json["q"]
        calls.append(q)
        q_lower = q.lower()
        if "fee schedule" in q_lower:
            link = "https://www.cityofpasadena.net/planning/permit-fee-schedule/"
            title = "Pasadena Permit Fee Schedule"
        elif "phone address" in q_lower:
            link = "https://www.cityofpasadena.net/planning/contact/"
            title = "Pasadena Building Department Contact"
        elif "code section" in q_lower:
            link = "https://library.municode.com/ca/pasadena/codes/code_of_ordinances"
            title = "Pasadena Code Section"
        elif "application requirements" in q_lower:
            link = "https://www.cityofpasadena.net/planning/permit-applications/"
            title = "Pasadena Permit Application Requirements"
        else:
            link = "https://www.cityofpasadena.net/planning/inspections/"
            title = "Pasadena Inspection Process"
        return FakeResponse(organic=[
            {"title": "Competitor HVAC guide", "link": "https://permitmint.com/guides/hvac/texas/pasadena/", "snippet": "Competitor page must never be cited"},
            {"title": title, "link": link, "snippet": "Official city permit page"},
        ])

    monkeypatch.setattr(engine, "_http_post_with_backoff", fake_post)

    result = engine.enrich_result_with_serper_sources(_base_result(), "HVAC replacement", "Pasadena", "CA")

    assert result["fee_source"]["url"].endswith("/permit-fee-schedule/")
    assert result["ahj_contact_source"]["url"].endswith("/contact/")
    assert "municode.com" in result["code_section_source"]["url"]
    assert result["required_documents_source"]["url"].endswith("/permit-applications/")
    assert result["inspection_process_source"]["url"].endswith("/inspections/")
    assert result["sources_status"] == "serper_verified"
    assert result["serper_credits_used"] == 5
    assert len(calls) == 5
    assert all("permitmint.com" not in src for src in result["sources"])


def test_fee_verify_caveat_keeps_number_and_adds_source(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=15, max_retries=2):
        return FakeResponse(organic=[{"title": "Fee Schedule", "link": "https://example.gov/fees", "snippet": "fees"}])

    monkeypatch.setattr(engine, "_http_post_with_backoff", fake_post)
    result = engine.enrich_result_with_serper_sources(_base_result(), "HVAC replacement", "Example", "TX")
    result = engine.apply_fee_verify_caveat(result)

    assert "$450 - $750" in result["fee_range"]
    assert result["fee_range"].startswith("Fee Estimate:")
    assert "verify in https://example.gov/fees before quoting" in result["fee_range"]


@pytest.mark.parametrize(
    "failure",
    ["timeout", "401", "empty"],
)
def test_serper_failure_gracefully_degrades(monkeypatch, failure):
    def fake_post(url, headers=None, json=None, timeout=15, max_retries=2):
        if failure == "timeout":
            raise requests.Timeout("boom")
        if failure == "401":
            return FakeResponse(status_code=401, organic=[])
        return FakeResponse(status_code=200, organic=[])

    monkeypatch.setattr(engine, "_http_post_with_backoff", fake_post)

    result = engine.enrich_result_with_serper_sources(_base_result(), "HVAC replacement", "Nowhere", "TX")

    assert result["sources_status"] == "serper_unavailable"
    assert "fee_source" not in result
    assert result["serper_credits_used"] >= 1


def test_companion_hedging_like_for_like_hvac_swap():
    result = {
        "permits_required": [
            {"permit_type": "Mechanical Permit — HVAC Condenser Swap", "required": True}
        ],
        "companion_permits": [
            {"permit_type": "Electrical Permit", "reason": "Required for disconnect/reconnect", "certainty": "almost_certain"},
            {"permit_type": "Gas Permit", "reason": "Required for gas unit", "certainty": "likely"},
            {"permit_type": "Plumbing Permit", "reason": "Required for condensate", "certainty": "possible"},
        ],
    }

    hedged = engine.hedge_companion_permits(result, "like-for-like HVAC condenser swap")
    reasons = " ".join(cp["reason"] for cp in hedged["companion_permits"])

    assert "May be required if:" in reasons
    assert "new wiring" in reasons
    assert "gas piping modification" in reasons
    assert "pipe relocation" in reasons
    assert "Required for" not in reasons
    assert all(cp["requirement_label"] == "May be required based on scope" for cp in hedged["companion_permits"])


def test_companion_hedging_keeps_primary_required_for_full_hvac_replacement():
    result = {
        "permits_required": [
            {"permit_type": "Mechanical Permit — HVAC System Replacement", "required": True, "notes": "Primary permit"}
        ],
        "companion_permits": [
            {"permit_type": "Electrical Permit", "reason": "Required for new disconnect", "certainty": "almost_certain"}
        ],
    }

    hedged = engine.hedge_companion_permits(result, "full HVAC system replacement with new disconnect")

    assert hedged["permits_required"][0]["required"] is True
    assert hedged["permits_required"][0]["permit_type"] == "Mechanical Permit — HVAC System Replacement"
    assert hedged["companion_permits"][0]["reason"].startswith("May be required if:")
    assert hedged["companion_permits"][0]["certainty"] == "conditional"



def test_serper_claim_queries_run_concurrently(monkeypatch):
    calls = []
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_post(url, headers=None, json=None, timeout=15, max_retries=2):
        nonlocal active, max_active
        with lock:
            calls.append(json["q"])
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.15)
        with lock:
            active -= 1
        slug = json["q"].split()[0].lower()
        return FakeResponse(organic=[{
            "title": f"Official {slug} source",
            "link": f"https://example.gov/{slug}-{len(calls)}",
            "snippet": "Official city permit page",
        }])

    monkeypatch.setattr(engine, "_http_post_with_backoff", fake_post)

    started = time.perf_counter()
    result = engine.enrich_result_with_serper_sources(_base_result(), "HVAC replacement", "Concurrency", "TX")
    elapsed = time.perf_counter() - started

    assert len(calls) == 5
    assert max_active > 1
    assert elapsed < 0.45
    assert result["sources_status"] == "serper_verified"
    assert result["serper_credits_used"] == 5


def test_serper_parallel_caps_at_five_queries_and_degrades_partially(monkeypatch):
    calls = []
    active = 0
    max_active = 0
    lock = threading.Lock()

    extra_claims = [(f"claim_{i}", f"claim_{i}_source", f"Cap City TX claim {i} permit source") for i in range(7)]
    monkeypatch.setattr(engine, "_serper_claim_queries", lambda job_type, city, state, result: extra_claims)

    def fake_post(url, headers=None, json=None, timeout=15, max_retries=2):
        nonlocal active, max_active
        q = json["q"]
        with lock:
            calls.append(q)
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        if "claim 2" in q or "claim 4" in q:
            raise requests.Timeout("simulated timeout")
        return FakeResponse(organic=[{
            "title": "Official permit source",
            "link": f"https://example.gov/{q.split()[-3]}-{q.split()[-1]}",
            "snippet": "Official city permit page",
        }])

    monkeypatch.setattr(engine, "_http_post_with_backoff", fake_post)

    result = engine.enrich_result_with_serper_sources(_base_result(), "panel upgrade", "Cap City", "TX")

    assert len(calls) == 5
    assert max_active <= engine.SERPER_TRUST_MAX_CONCURRENCY
    assert result["serper_credits_used"] == 5
    assert result["sources_status"] == "serper_verified"
    assert "claim_2_source" not in result
    assert "claim_4_source" not in result
    assert result["claim_0_source"]["url"].startswith("https://example.gov/")
