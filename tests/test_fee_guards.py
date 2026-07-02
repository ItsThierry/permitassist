from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from fee_realism_guardrail import apply_fee_realism_guardrail
from research_engine import apply_fee_verify_caveat


def test_caveat_grammar_never_garbled():
    out = apply_fee_verify_caveat({"fee_range": "$85 flat fee — verify in before quoting"})
    assert "verify in before" not in out["fee_range"].lower()
    assert "building department" in out["fee_range"].lower()


def test_caveat_deduped():
    out = apply_fee_verify_caveat({"fee_range": "$85 — verify current fees with the issuing office before quoting — verify current fees with the issuing office before quoting"})
    assert out["fee_range"].lower().count("verify with the building department before quoting") == 1


def test_exact_local_fee_with_caveat_preserved():
    out = apply_fee_verify_caveat({"fee_range": "$67 plumbing permit + 12% surcharge — verify in before quoting"})
    assert "$67" in out["fee_range"]
    assert "12%" in out["fee_range"]


def test_sprinkler_adder_blocked_by_negative_fact():
    out = apply_fee_realism_guardrail(
        {"fee_range": "Call to confirm", "hidden_triggers": ["fire_sprinkler_modify"]},
        "commercial warehouse racking 8 feet tall anchored to slab no sprinklers altered, job value 30000",
        "Fort Worth",
        "TX",
        "commercial",
    )
    components = out.get("_fee_floor_components", {})
    assert all(c.get("key") != "fire_sprinkler_modify" for c in components.get("trigger_adders", []))
    assert "fire-sprinkler-modify" not in str(out.get("fee_range") or "")


def test_local_fee_beats_benchmark_but_benchmark_retained_as_labeled_range():
    out = apply_fee_realism_guardrail({"fee_range": "$20,000 official local fee"}, "commercial office TI 1000 sf job value 100000", "Phoenix", "AZ", "commercial_office_ti")
    assert out["_fee_floor_check"] == "llm_above_floor"
    assert "$20,000" in out["fee_range"]


def test_placeholder_fee_url_dropped_fee_kept():
    out = apply_fee_verify_caveat({"fee_range": "$100 local fee", "fee_source": {"url": "https://example.com/placeholder"}})
    assert "$100" in out["fee_range"]
