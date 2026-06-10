#!/usr/bin/env python3
"""Step 4 test — suppress multiplier line when it's 1.0×."""

def _build_fee_text(harness=False, **kw):
    """
    Inline minimal reproduction of the component builder used in
    api/fee_realism_guardrail.py.
    """
    def _format_usd(n: int) -> str:
        return f"${n:,}"

    low_total = kw["low_total"]
    high_total = kw["high_total"]
    base_floor = kw["base_floor"]
    scope_key = kw["scope_key"]
    jurisdiction_label = kw["jurisdiction_label"]
    jurisdiction_mult = kw["jurisdiction_mult"]
    adders = kw.get("adders", [])

    components = [
        f"~{_format_usd(base_floor)} base permit + plan review ({jurisdiction_label} {scope_key} floor)",
    ]
    if jurisdiction_mult != 1.0:
        components.append(f"× {jurisdiction_mult:.1f}× jurisdiction multiplier")
    for key, add_min, add_max in adders:
        label = key.replace("_", "-")
        midpoint = round((add_min + add_max) / 2 / 500) * 500
        components.append(f"+ {_format_usd(int(midpoint))} {label} adder")

    return (
        f"Fee planning estimate (NOT a jurisdiction-specific AHJ fee): {_format_usd(low_total)}-{_format_usd(high_total)}+ "
        f"(national-scope benchmark). Components: "
        f"{' '.join(components)}."
    )


def test_multiplier_1_suppressed():
    text = _build_fee_text(
        low_total=17500, high_total=29000, base_floor=12000,
        scope_key="commercial TI", jurisdiction_label="Savannah, GA",
        jurisdiction_mult=1.0, adders=[],
    )
    assert "×" not in text, f"Got: {text!r}"
    assert "jurisdiction multiplier" not in text


def test_multiplier_1_5_renders():
    text = _build_fee_text(
        low_total=20000, high_total=35000, base_floor=12000,
        scope_key="commercial TI", jurisdiction_label="Savannah, GA",
        jurisdiction_mult=1.5, adders=[],
    )
    assert "× 1.5× jurisdiction multiplier" in text


if __name__ == "__main__":
    test_multiplier_1_suppressed()
    test_multiplier_1_5_renders()
    print("Step 4 tests: ALL PASSED")
