#!/usr/bin/env python3
"""BG-POLISH-01 QA cache-bypass contract tests."""

from pathlib import Path
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import api.research_engine as engine

ROOT = Path(__file__).resolve().parents[1]
SERVER = (ROOT / "api" / "server.py").read_text(encoding="utf-8")
ENGINE = (ROOT / "api" / "research_engine.py").read_text(encoding="utf-8")


def test_task4_api_exposes_authorized_cache_bypass_header_and_threads_it_to_engine():
    assert "X-PermitAssist-Cache-Mode" in SERVER
    assert "qa_cache_mode" in SERVER
    assert "use_cache=(not evidence_allowed) and (not qa_cache_mode)" in SERVER or "and (not qa_cache_mode)" in SERVER
    assert "suppress_cache_write=evidence_allowed or qa_cache_mode == \"bypass\"" in SERVER
    assert "bypass_lookup_caches=bool(qa_cache_mode)" in SERVER


def test_task4_research_permit_forced_fresh_bypasses_search_and_pdf_caches():
    assert "bypass_lookup_caches: bool = False" in ENGINE
    assert "build_search_context(job_type, city, state, zip_code, city_match_level, bypass_cache=bypass_lookup_caches, suppress_cache_write=suppress_cache_write)" in ENGINE
    assert "cached = None if bypass_cache else get_search_cache" in ENGINE
    assert "cached = '' if bypass_cache else get_cached_pdf_text" in ENGINE
    assert "if not suppress_cache_write:\n            set_search_cache" in ENGINE
    assert "suppress_cache_write=evidence_allowed or qa_cache_mode == \"bypass\"" in SERVER
    assert "and (is_benchmark or admin_bypass) else \"\"" in SERVER


def test_task4_extract_pdf_text_skips_cached_pdf_when_bypass_enabled(monkeypatch):
    calls = {"cache": 0, "http": 0}

    monkeypatch.setattr(engine, "is_pdf_url", lambda url: True)

    def fake_cache(*args, **kwargs):
        calls["cache"] += 1
        return "STALE PDF CACHE"

    class FakeResp:
        content = b"%PDF fake"
        def raise_for_status(self):
            return None

    def fake_get(*args, **kwargs):
        calls["http"] += 1
        return FakeResp()

    monkeypatch.setattr(engine, "get_cached_pdf_text", fake_cache)
    monkeypatch.setattr(engine.requests, "get", fake_get)
    monkeypatch.setattr(engine, "_extract_pdf_text_with_pdfplumber", lambda *args, **kwargs: "Fresh PDF text with enough meaningful characters to pass the one hundred character threshold. " * 2)
    monkeypatch.setattr(engine, "cache_pdf_text", lambda *args, **kwargs: None)

    out = engine.extract_pdf_text("https://example.gov/file.pdf", "Battle Ground", "WA", bypass_cache=True)

    assert "Fresh PDF text" in out
    assert calls["cache"] == 0
    assert calls["http"] == 1


def test_task4_bypass_mode_can_suppress_pdf_cache_write(monkeypatch):
    calls = {"write": 0}

    monkeypatch.setattr(engine, "is_pdf_url", lambda url: True)
    monkeypatch.setattr(engine, "get_cached_pdf_text", lambda *args, **kwargs: "STALE PDF CACHE")

    class FakeResp:
        content = b"%PDF fake"
        def raise_for_status(self):
            return None

    monkeypatch.setattr(engine.requests, "get", lambda *args, **kwargs: FakeResp())
    monkeypatch.setattr(engine, "_extract_pdf_text_with_pdfplumber", lambda *args, **kwargs: "Fresh PDF text with enough meaningful characters to pass the one hundred character threshold. " * 2)

    def fake_write(*args, **kwargs):
        calls["write"] += 1

    monkeypatch.setattr(engine, "cache_pdf_text", fake_write)

    out = engine.extract_pdf_text(
        "https://example.gov/file.pdf",
        "Battle Ground",
        "WA",
        bypass_cache=True,
        suppress_cache_write=True,
    )

    assert "Fresh PDF text" in out
    assert calls["write"] == 0
