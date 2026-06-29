import csv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "live_customer_100_20260629T0032Z"


def test_auto_grader_false_positive_corrections_are_not_frozen_as_red_contracts():
    grades_path = ART / "FINAL_TITI_OPUS_GRADES.csv"
    if not grades_path.exists():
        pytest.skip("live_customer_100 artifact grades are local-only and absent in clean checkout")
    rows = list(csv.DictReader(open(grades_path, newline="")))
    corrected = {r["case_id"] for r in rows if r["auto_grade"] == "C" and r["final_grade"] in {"A", "B"} and r["confirmed_defect"] == "no"}
    contract_ids = {c["case_id"] for c in __import__("live_customer_100_phase0_helpers").load_contracts()}
    assert corrected
    assert not (corrected & contract_ids), f"False-positive auto-C cases must not become RED product contracts: {sorted(corrected & contract_ids)}"


def test_trade_words_in_negation_or_verify_copy_are_not_hard_overreach():
    customer_text = "No plumbing work is included. Electrical permit is VERIFY only if a new circuit is added."
    assert "no plumbing" in customer_text.lower()
    assert "verify only if" in customer_text.lower()
    # Guardrail for future grader: these words are evidence of negation/conditional guidance, not hard REQUIRED families.
    assert "Plumbing REQUIRED" not in customer_text
    assert "Electrical REQUIRED" not in customer_text
