import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REPORT_TEMPLATE = ROOT / "frontend" / "report.html"


def _extract_js_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    in_string = None
    escape = False
    in_line_comment = False
    in_block_comment = False
    for idx in range(brace, len(source)):
        ch = source[idx]
        nxt = source[idx + 1] if idx + 1 < len(source) else ""
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
            continue
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_string:
                in_string = None
            continue
        if ch in {"'", '"', "`"}:
            in_string = ch
            continue
        if ch == "/" and nxt == "/":
            in_line_comment = True
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : idx + 1]
    raise AssertionError(f"Could not extract {name}")


def _rendered_permit_name(data: dict) -> str:
    html = REPORT_TEMPLATE.read_text(encoding="utf-8")
    helper = _extract_js_function(html, "specificDisplayPermitKind")
    script = f"""
{helper}
const d = {json.dumps(data)};
const rows = Array.isArray(d.public_packet?.rows) ? d.public_packet.rows : [];
const required = rows.filter(row => row && row.decision === 'REQUIRED');
const legacy = [
  ...required.map(row => row && (row.permit_name || row.permit_type)),
  d.permit_name,
  d.permit_type,
];
const lead = legacy.find(value => String(value || '').trim()) || 'Permit details';
const specific = specificDisplayPermitKind(
  d.public_packet?.display_permit_kind,
  d.permit_kind,
  ...legacy,
);
console.log(JSON.stringify(specific || lead));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {
                "permit_kind": "Residential Building / Remodel",
                "permit_name": "Building Permit",
                "public_packet": {
                    "display_permit_kind": "Residential Building / Remodel",
                    "rows": [{"decision": "REQUIRED", "permit_name": "Building Permit"}],
                },
            },
            "Residential Building / Remodel",
        ),
        (
            {
                "permit_kind": "Residential Building / Remodel",
                "public_packet": {
                    "display_permit_kind": "Building Permit",
                    "rows": [{"decision": "REQUIRED", "permit_name": "Building Permit"}],
                },
            },
            "Residential Building / Remodel",
        ),
        (
            {
                "permit_kind": "Commercial Building / Tenant Improvement",
                "public_packet": {
                    "display_permit_kind": "Permit package: Building Permit; Electrical Permit",
                    "rows": [{"decision": "REQUIRED", "permit_name": "Building Permit"}],
                },
            },
            "Commercial Building / Tenant Improvement",
        ),
        (
            {
                "permit_kind": "Building",
                "public_packet": {
                    "display_permit_kind": "Building Permit — Addition",
                    "rows": [{"decision": "REQUIRED", "permit_name": "Building Permit"}],
                },
            },
            "Building Permit — Addition",
        ),
        (
            {
                "permit_kind": "Commercial Building / Tenant Improvement",
                "permit_name": "Permit package: Building Permit; Electrical Permit",
                "public_packet": {
                    "display_permit_kind": "Commercial Building / Tenant Improvement",
                    "rows": [
                        {"decision": "REQUIRED", "permit_name": "Commercial Building / Tenant Improvement Permit"},
                        {"decision": "REQUIRED", "permit_name": "Electrical Permit"},
                    ],
                },
            },
            "Commercial Building / Tenant Improvement",
        ),
        (
            {
                "permit_kind": "Permit package",
                "public_packet": {
                    "display_permit_kind": "Permit package",
                    "rows": [
                        {"decision": "REQUIRED", "permit_name": "Electrical Permit — New Circuit / Equipment Connection"},
                        {"decision": "REQUIRED", "permit_name": "Mechanical Permit — Ductless Mini-Split / Heat Pump Installation"},
                        {"decision": "REQUIRED", "permit_name": "Refrigeration Permit — Split-System Heat Pump / Mini-Split"},
                    ],
                },
            },
            "Electrical Permit — New Circuit / Equipment Connection",
        ),
        (
            {
                "permit_kind": "Building",
                "permit_name": "Building Permit",
                "public_packet": {
                    "display_permit_kind": "Building",
                    "rows": [{"decision": "REQUIRED", "permit_name": "Building Permit"}],
                },
            },
            "Building Permit",
        ),
        (
            {
                "permit_kind": "Permit package",
                "public_packet": {
                    "display_permit_kind": "Permit package",
                    "rows": [
                        {"decision": "REQUIRED", "permit_name": "Roofing Permit"},
                        {"decision": "REQUIRED", "permit_name": "Roofing Permit — Tear-Off / Re-Roof"},
                    ],
                },
            },
            "Roofing Permit — Tear-Off / Re-Roof",
        ),
        (
            {
                "permit_kind": "UNKNOWN",
                "permit_name": "Roofing Permit — Tear-Off / Re-Roof",
                "public_packet": {
                    "display_permit_kind": "Verify with permit office",
                    "rows": [{"decision": "REQUIRED", "permit_name": "Roofing Permit — Tear-Off / Re-Roof"}],
                },
            },
            "Roofing Permit — Tear-Off / Re-Roof",
        ),
    ],
)
def test_report_visible_label_prefers_sealed_specific_kind_without_hiding_lead_rows(data: dict, expected: str) -> None:
    assert _rendered_permit_name(data) == expected


def test_report_title_h1_and_core_permit_share_the_same_precedence_resolved_label() -> None:
    html = REPORT_TEMPLATE.read_text(encoding="utf-8")

    assert "specificDisplayPermitKind" in html
    assert "const specificPermitKind = specificDisplayPermitKind(" in html
    assert "const permitName = textValue(specificPermitKind || leadPermitName, 'Permit details');" in html
    assert "addText(heroText, 'h1', { className: 'headline', text: permitName });" in html
    assert "meta.appendChild(metaRow('Permit', permitName));" in html
    assert "document.title = `${permitName} | PermitAssist Report`;" in html


def test_report_specific_kind_filter_matches_backend_uncertainty_and_generic_contract() -> None:
    html = REPORT_TEMPLATE.read_text(encoding="utf-8")
    helper = _extract_js_function(html, "specificDisplayPermitKind")
    script = f"""
{helper}
const cases = [
  ['Permit package'],
  ['Permit package: Building Permit; Electrical Permit'],
  ['Building Permit'],
  ['Verify with permit office'],
  ['UNKNOWN'],
  ['Likely Building Permit'],
  ['Required?'],
  ['Building Permit', 'Roofing Permit — Tear-Off / Re-Roof'],
  ['Roofing Permit', 'Roofing Permit — Tear-Off / Re-Roof'],
  ['', 'Residential Building / Remodel'],
];
console.log(JSON.stringify(cases.map(values => specificDisplayPermitKind(...values))));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert json.loads(completed.stdout) == ["", "", "", "", "", "", "", "Roofing Permit — Tear-Off / Re-Roof", "Roofing Permit — Tear-Off / Re-Roof", "Residential Building / Remodel"]
