from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
ART = ROOT / "artifacts" / "live100_fable5_phase0_phase1_20260705"
LEDGER = ROOT / "tests" / "fixtures" / "live100_fable5_green_family_inflation_ledger_20260705.json"


def _bucket(family: str) -> str:
    return "building" if family in {"building", "building_ti", "building_adu", "demolition", "racking"} else family


def test_fable5_final_gate_has_no_removed_bypass_or_canonical_injection_tokens():
    text = (ROOT / "api" / "live100_fable5_final_gate.py").read_text()
    assert "fable5_final_floor" not in text
    assert "https://www.naperville.il.us/services/permits--licenses/" not in text
    assert not re.search(r"_NAMED_AUTHORITY_CONTACTS\s*=\s*\{", text)
    assert not re.search(r"_CANONICAL_AUTHORITY_FIXES\s*=\s*\{", text)


def test_fable5_cosmetic_not_required_negative_and_positive_probes():
    from live100_fable5_final_gate import _cosmetic_not_required
    from public_packet import _fable5_cosmetic_not_required_scope

    cosmetic = "replace kitchen cabinets and countertops same layout no sink move no electrical no walls"
    structural = "replace cabinets, remove non-load-bearing wall, no electrical changes"
    assert _cosmetic_not_required(cosmetic) is True
    assert _fable5_cosmetic_not_required_scope(cosmetic, "residential") is True
    assert _cosmetic_not_required(structural) is False
    assert _fable5_cosmetic_not_required_scope(structural, "residential") is False


def test_fable5_request_supports_family_negation_and_repair_shop_demotion():
    from public_packet import _fable5_negative_family_override, _fable5_request_supports_family

    assert _fable5_request_supports_family("mechanical", "replace exhaust grille no exhaust changes no ductwork") is False
    assert _fable5_request_supports_family("mechanical", "install dust collection system with explosion venting") is True
    repair_scope = "convert warehouse to marine repair shop with solvent storage ventilation compressor and floor drain"
    assert _fable5_negative_family_override("health_food", repair_scope) is True
    assert _fable5_negative_family_override("wastewater_pretreatment_fog", repair_scope) is True
    brewery_scope = "convert warehouse to brewery taproom with floor drains trench drains brewing equipment"
    assert _fable5_negative_family_override("health_food", brewery_scope) is False


def test_green_family_inflation_ledger_matches_latest_conditions_artifact():
    ledger = json.loads(LEDGER.read_text())
    manifest_path = ART / ledger["artifact_label"] / "manifest.json"
    if not manifest_path.exists():
        pytest.skip(f"offline replay artifact missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    green = json.loads((ROOT / "tests" / "fixtures" / "live100_fable5_final_green_freeze_68_20260705.json").read_text())["cases"]
    rows = {str(row["case_id"]): row for row in manifest.get("rows", [])}
    observed = []
    for cid, frozen in green.items():
        before = {_bucket(str(f)) for f in frozen.get("canonical_required_family_keys") or []}
        after = {_bucket(str(f)) for f in rows[cid].get("required_families") or []}
        if before != after:
            observed.append({"case_id": cid, "added": sorted(after - before), "missing": sorted(before - after)})
    assert observed == ledger["items"]
    assert all(not item["missing"] for item in observed)


def test_final_gate_and_public_projection_are_idempotent_on_sample_artifact():
    from live100_fable5_final_gate import apply_fable5_final_customer_gate
    from public_packet import apply_public_packet_projection

    sample = next((ART / "fable5_api_conditions3_pass2" / "public_json").glob("*R-036.json"))
    original = json.loads(sample.read_text())
    job_type = "replace kitchen cabinets and countertops same layout no sink move no electrical no walls, job value 19000"
    once = apply_public_packet_projection(apply_fable5_final_customer_gate(copy.deepcopy(original), job_type, "", "", None, None), None)
    twice = apply_public_packet_projection(apply_fable5_final_customer_gate(copy.deepcopy(once), job_type, "", "", None, None), None)
    assert once == twice
