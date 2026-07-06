from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import decision_resolver
import live100_fable5_final_gate as fable_gate
import research_engine
from source_provenance import has_local_official_source


def test_change_of_use_requires_explicit_transition_not_bare_use_word() -> None:
    assert decision_resolver._explicit_change_of_use("open a fitness studio") is False
    assert decision_resolver._explicit_change_of_use("convert former retail store to fitness studio") is True


def test_source_adjudicated_not_required_comes_from_data_and_sets_decision_basis() -> None:
    out = decision_resolver.resolve_customer_decision({
        "result": {"permit_decision": "NOT_REQUIRED", "permit_required": False, "sources": [{"url": "https://www.chicago.gov/city/en/depts/bldgs.html"}]},
        "job_type": "repair drywall in one room, no structural, no electrical",
        "city": "Chicago",
        "state": "IL",
        "scope_contract": {"category": "residential"},
    })
    assert out["permit_decision"] == "NOT_REQUIRED"
    assert out["decision_basis"] == "source_adjudicated_not_required"


def test_roofing_and_solar_have_word_boundary_required_floors() -> None:
    reroof = fable_gate.apply_fable5_final_customer_gate(
        {"permit_decision": "REQUIRED", "permit_required": True, "permits_required": []},
        "tear-off reroof and replace shingles",
        "Phoenix",
        "AZ",
        {"category": "residential"},
    )
    solar = fable_gate.apply_fable5_final_customer_gate(
        {"permit_decision": "REQUIRED", "permit_required": True, "permits_required": []},
        "install solar panels and inverter",
        "Phoenix",
        "AZ",
        {"category": "residential"},
    )
    families = {row.get("family") for row in reroof.get("permits_required", [])}
    assert "roofing" in families
    solar_families = {row.get("family") for row in solar.get("permits_required", [])}
    assert "solar_pv" in solar_families
    no_false = fable_gate._triggered_families("install solarization window film", "residential")
    assert "solar_pv" not in no_false


def test_provenance_oracle_rejects_nonlocal_official_source_for_local_truth() -> None:
    assert not has_local_official_source({"source_url": "https://www.nyc.gov/site/buildings/index.page"}, city="Phoenix", state="AZ")
    assert has_local_official_source({"source_url": "https://www.phoenix.gov/pdd"}, city="Phoenix", state="AZ")


def test_serper_cache_rejects_wrong_locality_and_no_rank_fail_open(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(research_engine, "SERPER_CACHE_DB", str(tmp_path / "serper_cache.db"))
    bad = {"url": "https://www.nyc.gov/site/buildings/index.page", "title": "NYC DOB", "snippet": "New York permits"}
    research_engine._set_cached_serper_source("Phoenix", "AZ", "permit", "Phoenix permit portal", bad)
    assert research_engine._get_cached_serper_source("Phoenix", "AZ", "permit", "Phoenix permit portal") is None

    monkeypatch.setattr(research_engine, "_rank_search_results", lambda *a, **k: [])
    source = research_engine._best_serper_source_from_results([
        {"link": "https://www.phoenix.gov/pdd", "title": "Phoenix PDD", "snippet": "Phoenix permits"}
    ], "Phoenix", "AZ", scope_text="permit", claim_type="permit")
    assert source is None


def test_static_locality_filter_is_fail_closed_not_silent_fail_open() -> None:
    text = (API / "research_engine.py").read_text(encoding="utf-8")
    assert "ranked = ranked or allowed" not in text
    assert "_source_locality_filter_failed_closed" in text
