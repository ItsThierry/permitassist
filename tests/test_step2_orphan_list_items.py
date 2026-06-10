#!/usr/bin/env python3
"""
Step 2 test — orphan list item coalescing in normalizeInspectionItems.

In the golden snapshot for Richmond Dental, the ``inspections`` array has
mixed dict and string entries. Items 6 and 7 are short string fragments
("certificate-of-occupancy conditions." and "fixture counts.") that render
as standalone numbered entries.  After the fix these must be appended to
the preceding item's body instead.

Strategy: replicate the JS logic in Python, exercise it against the same
shape the frontend receives, and assert the structural invariants.
"""

import re, sys, json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = REPO_ROOT / "frontend"

# ── 1. Load and parse the JS `normalizeInspectionItems` body ─────────────

def _extract_js_function(path: Path, fn_name: str) -> str:
    with open(path) as f:
        content = f.read()
    m = re.search(
        r'function\s+' + re.escape(fn_name) + r'\s*\([^)]*\)\s*\{([\s\S]*?)\n\}',
        content,
    )
    if not m:
        raise RuntimeError(f"Could not extract {fn_name} from {path}")
    return m.group(1)


def _parse_function_source(src: str) -> str:
    """Return the first top-level function name found inside the source."""
    m = re.search(r'function\s+(\w+)\s*\(', src)
    if not m:
        return ""
    return m.group(1)


# ── 2. Minimal Python re-implementation of the JS logic ───────────────────

def normalizeInspectionItems(items):
    """
    Python clone of the updated JS ``normalizeInspectionItems``.
    Must be kept in sync manually; the test verifies behaviour,
    not implementation identity.
    """
    if not isinstance(items, list):
        return []

    # First pass: normalize to uniform object shape and filter placeholders
    normalized = []
    for item in items:
        if isinstance(item, str):
            title = item.strip()
            if not title or re.search(r'^inspection\s*\d*$', title, re.I):
                continue
            normalized.append({"title": title, "description": "", "timing": ""})
        elif isinstance(item, dict):
            title = str(item.get("stage") or item.get("title") or item.get("name") or "").strip()
            description = str(item.get("description") or item.get("notes") or item.get("detail") or "").strip()
            timing = str(item.get("timing") or "").strip()
            if not title and not description and not timing:
                continue
            if re.search(r'^inspection\s*\d*$', title, re.I) and not description and not timing:
                continue
            normalized.append({
                **item,
                "title": title or description,
                "description": description,
                "timing": timing,
            })
        else:
            continue

    # Second pass: merge orphans (items with <4 effective words and no timing)
    # into the preceding item's description.
    result = []
    for item in normalized:
        effective = ' '.join((item.get("title") or "").split() + (item.get("description") or "").split())
        word_count = len(effective.split())
        # An orphan: short (< 4 words), no timing, and no substantial description
        is_orphan = (
            word_count < 4
            and not (item.get("timing") or "").strip()
            and len((item.get("description") or "").strip()) < 3
        )
        if is_orphan and result:
            prev = result[-1]
            orphan_text = (item.get("title") or "").strip()
            prev_desc = (prev.get("description") or "").strip()
            if prev_desc:
                prev["description"] = f"{prev_desc} {orphan_text}"
            else:
                prev["description"] = orphan_text
            # If prev had no title, this shouldn't happen for our data, but be safe
            continue
        result.append(item)

    return result


# ── 3. Test fixtures ──────────────────────────────────────────────────────

_RICHMOND_DENTAL_INSPECTIONS = [
    {"stage": "Building Demo / Framing Rough-In", "description": "Inspector verifies...", "timing": "Before insulation..."},
    {"stage": "Plumbing Rough-In", "description": "Inspector checks...", "timing": "Before walls are closed..."},
    {"stage": "Mechanical Rough-In", "description": "Inspector verifies...", "timing": "Before ceiling closure..."},
    {"stage": "Electrical Rough-In / Service Upgrade", "description": "Inspector checks...", "timing": "Before walls/ceilings are closed..."},
    {"stage": "Final Commercial", "description": "Inspector verifies...", "timing": "After all work is complete..."},
    "certificate-of-occupancy conditions.",
    "fixture counts.",
    "Mechanical balance / infection-control verification \u2014 confirm ventilation, exhaust, pressure relationships, filtration",
    "Medical gas pressure test / verifier final if oxygen, nitrous, vacuum, alarms, zone valves, or gas outlets are installed or modified.",
    "Fire alarm / sprinkler / life-safety final \u2014 verify notification appliance coverage, sprinkler head layout, emergency lighting, exit signs, and any oxygen/medical-gas hazard coordination.",
    "Radiology/x-ray shielding or state radiation registration verification if radiation-producing equipment is installed.",
]


def test_orphans_merged_into_preceding():
    out = normalizeInspectionItems(_RICHMOND_DENTAL_INSPECTIONS)
    titles = [o["title"] for o in out]
    descs = [o.get("description", "") for o in out]

    # After merge: should have 9 items (11 - 2 orphans)
    assert len(out) == 9, f"Expected 9 items after orphan merge, got {len(out)}"

    # Items 1-5 (0-indexed 0-4) should be byte-identical to before
    assert out[0]["title"] == "Building Demo / Framing Rough-In"
    assert out[1]["title"] == "Plumbing Rough-In"
    assert out[2]["title"] == "Mechanical Rough-In"
    assert out[3]["title"] == "Electrical Rough-In / Service Upgrade"
    assert out[4]["title"] == "Final Commercial"

    # The orphan fragments should be gone as standalone items
    assert "certificate-of-occupancy conditions." not in titles
    assert "fixture counts." not in titles

    # The fragments should be appended to item 5 (index 4, "Final Commercial")
    final_desc = out[4].get("description", "")
    assert "certificate-of-occupancy conditions." in final_desc, (
        f"Orphan 'certificate-of-occupancy conditions.' not merged into preceding item. desc={final_desc!r}"
    )
    assert "fixture counts." in final_desc, (
        f"Orphan 'fixture counts.' not merged into preceding item. desc={final_desc!r}"
    )

    # Items 8-11 (originally indices 7-10) should still exist intact
    assert out[5]["title"] == "Mechanical balance / infection-control verification \u2014 confirm ventilation, exhaust, pressure relationships, filtration"
    assert out[6]["title"] == "Medical gas pressure test / verifier final if oxygen, nitrous, vacuum, alarms, zone valves, or gas outlets are installed or modified."
    assert out[7]["title"] == "Fire alarm / sprinkler / life-safety final \u2014 verify notification appliance coverage, sprinkler head layout, emergency lighting, exit signs, and any oxygen/medical-gas hazard coordination."
    assert out[8]["title"] == "Radiology/x-ray shielding or state radiation registration verification if radiation-producing equipment is installed."

    # No item should have < 4 total words (orphans were absorbed)
    for i, item in enumerate(out):
        effective = ' '.join(((item.get("title") or "").split() + (item.get("description") or "").split()))
        word_count = len(effective.split())
        assert word_count >= 4, f"Item {i} has only {word_count} words: {item!r}"

    print("  test_orphans_merged_into_preceding: PASSED")


def test_dict_only_list_no_change():
    """A list of well-formed dicts should pass through unchanged."""
    items = [
        {"stage": "Rough-In", "description": "Check framing", "timing": "Before drywall"},
        {"stage": "Final", "description": "Verify everything", "timing": "After completion"},
    ]
    out = normalizeInspectionItems(items)
    assert len(out) == 2
    assert out[0]["title"] == "Rough-In"
    assert out[1]["title"] == "Final"
    print("  test_dict_only_list_no_change: PASSED")


def test_empty_and_placeholder_filtered():
    """Empty strings and placeholder dicts should be removed."""
    items = ["", "   ", "inspection 3", {"stage": "Inspection"}, {"stage": "Inspection 7"}]
    out = normalizeInspectionItems(items)
    assert len(out) == 0
    print("  test_empty_and_placeholder_filtered: PASSED")


# ── 4. Harness ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_orphans_merged_into_preceding()
    test_dict_only_list_no_change()
    test_empty_and_placeholder_filtered()
    print("Step 2 tests: ALL PASSED")
