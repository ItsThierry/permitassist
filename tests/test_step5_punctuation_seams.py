#!/usr/bin/env python3
"""Step 5 test — repair punctuation seams from LLM fragment splicing."""
import re


def normalize_seams(value: str) -> str:
    """Inline reproduction of the two seam fixes added in Step 5."""
    value = re.sub(r"(?<!\.)\.\.(?!\.)", ". ", value)         # "queue..Check" → "queue. Check"
    value = re.sub(r"\.\s+and\s+([a-z])", r" and \1", value)  # "bidding. and save" → "bidding and save"
    value = re.sub(r"\s{2,}", " ", value).strip()
    return value


def test_double_period():
    assert normalize_seams("Check local queue..Submit early") == "Check local queue. Submit early"


def test_period_before_and_lowercase():
    assert normalize_seams("Get receipt before bidding. and save it.") == "Get receipt before bidding and save it."


def test_period_before_and_uppercase_unchanged():
    # "Scope. And then file" — two real sentences, keep period
    assert normalize_seams("Scope. And then file") == "Scope. And then file"


def test_combined():
    assert normalize_seams("Plan review.. Get receipt. and submit") == "Plan review. Get receipt and submit"


if __name__ == "__main__":
    test_double_period()
    test_period_before_and_lowercase()
    test_period_before_and_uppercase_unchanged()
    test_combined()
    print("Step 5 tests: ALL PASSED")
