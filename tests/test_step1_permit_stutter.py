#!/usr/bin/env python3
"""
Step 1 test — verify 'Permit Permit' stutter fix in frontend templates.

The fix: conditionally append " Permit" only when permitName does NOT
already end with "Permit" (case-insensitive, trimmed).
"""

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_templates():
    """Yield every frontend HTML file that contains the justifier template."""
    frontend = REPO_ROOT / "frontend"
    for html in frontend.glob("**/*.html"):
        text = html.read_text(encoding="utf-8")
        # Only files with the actual customer-explanation header template
        if "Why Your ${permitName}" in text:
            yield html, text


def test_no_raw_permit_permit_template():
    """No file should still contain the raw double-Permit template string."""
    raw_bad = re.compile(r'\$\{permitName\}\s+Permit is Required')
    for html, text in _load_templates():
        matches = raw_bad.findall(text)
        assert len(matches) == 0, f"{html.name}: raw 'Permit Permit' template found"


def test_conditional_suffix_present():
    """Each file must define a suffix variable that checks endsWith('permit')."""
    for html, text in _load_templates():
        assert "const suffix" in text, f"{html.name}: missing 'const suffix' variable"
        assert "endsWith(\"permit\")" in text or "endsWith('permit')" in text, \
            f"{html.name}: missing endsWith('permit') check"
        assert "${suffix} Required" in text, f"{html.name}: template not using suffix"


def _eval_suffix_js(permit_name: str) -> str:
    """Simulate the JS suffix logic in Python."""
    if permit_name and permit_name.strip().lower().endswith("permit"):
        return " is"
    return " Permit is"


def _extract_full_header_js(permit_name: str) -> str:
    """What the JS template would render."""
    suffix = _eval_suffix_js(permit_name)
    return f"Why Your {permit_name}{suffix} Required"


def test_permit_kind_with_trailing_permit():
    """kind="Commercial Building / Tenant Improvement Permit" → exactly one trailing Permit."""
    assert _extract_full_header_js("Commercial Building / Tenant Improvement Permit") == \
           "Why Your Commercial Building / Tenant Improvement Permit is Required"


def test_permit_kind_without_trailing_permit():
    """kind="Commercial Building / Tenant Improvement" → header ends with '…Improvement Permit'."""
    assert _extract_full_header_js("Commercial Building / Tenant Improvement") == \
           "Why Your Commercial Building / Tenant Improvement Permit is Required"


def test_mechanical_permit():
    """kind="Mechanical Permit" → '…Mechanical Permit is Required' (no Permit Permit)."""
    assert _extract_full_header_js("Mechanical Permit") == \
           "Why Your Mechanical Permit is Required"


def test_theoretical_duplicate_blocked():
    """The string 'Permit  Permit' never appears in generated output."""
    cases = [
        "Building Permit",
        "Plumbing Permit",
        "Commercial Building / Tenant Improvement Permit",
        "Electrical Permit",
        "Residential Roofing",
        "",
        "Permit",  # Edge case: just the word "Permit"
    ]
    for name in cases:
        header = _extract_full_header_js(name)
        assert "Permit  Permit" not in header and "Permit Permit" not in header, \
            f"Double-Permit leak for name={name!r}: {header}"


if __name__ == "__main__":
    test_no_raw_permit_permit_template()
    test_conditional_suffix_present()
    test_permit_kind_with_trailing_permit()
    test_permit_kind_without_trailing_permit()
    test_mechanical_permit()
    test_theoretical_duplicate_blocked()
    print("Step 1 tests: ALL PASSED")
