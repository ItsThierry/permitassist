from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SURFACES = [
    ROOT / "frontend" / "index.html",
    ROOT / "frontend" / "preview-modern-reskinned-index.html",
    *[
        ROOT / "frontend" / "trades" / name
        for name in ("solar.html", "roofing.html", "electrical.html", "hvac.html", "plumbing.html")
    ],
]


def _helper_source(page: str) -> str:
    start = page.index("function normalizePermitFamily")
    end = page.index("function customerStatusLabel", start)
    return page[start:end]


def _run_helper(path: Path, payload: dict) -> dict:
    page = path.read_text()
    helper = _helper_source(page)
    script = f"""
const esc = value => String(value ?? '').replace(/[&<>\"']/g, '');
function rowCustomerStatus(row) {{
  const raw = String(row?.status || row?.verdict || row?.decision || row?.required_status || '').toUpperCase().trim();
  if (['REQUIRED','NOT_REQUIRED','CONDITIONAL','VERIFY','NEEDS_INPUT'].includes(raw)) return raw;
  if (row?.required === true) return 'REQUIRED';
  if (row?.required === false) return 'NOT_REQUIRED';
  return 'NEEDS_INPUT';
}}
function customerStatusLabel(status) {{ return status; }}
{helper}
const payload = {json.dumps(payload)};
const view = buildPermitFamilyView(payload);
const html = renderPermitFamilyMatrix(view, payload.applying_office || 'the issuing authority');
process.stdout.write(JSON.stringify({{
  statuses: view.allRows.map(row => [row._family, row._status]),
  required: view.requiredRows.map(row => row._family),
  verification: view.verificationRows.map(row => row._family),
  notRequired: view.notRequiredRows.map(row => row._family),
  html
}}));
"""
    completed = subprocess.run(
        ["node", "-e", script], check=True, text=True, capture_output=True
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize("path", SURFACES, ids=lambda path: path.name)
def test_mixed_manifest_renders_typed_family_matrix_without_promoting_leads(path: Path):
    result = _run_helper(path, {
        "applying_office": "Example Building Department",
        "permit_manifest": {
            "schema_version": "permit_manifest_v1",
            "primary": {"family": "BUILDING", "status": "REQUIRED", "local_name": "Building Permit"},
            "companions": [
                {"family": "ELECTRICAL", "status": "VERIFY", "local_name": "Electrical review"},
                {"family": "PLUMBING", "status": "CONDITIONAL", "local_name": "Plumbing review"},
                {"family": "FIRE", "status": "NOT_REQUIRED", "local_name": "Fire review"},
            ],
        },
        "permits_required": [{"family": "FIRE", "status": "REQUIRED"}],
    })
    assert result["statuses"] == [
        ["BUILDING", "REQUIRED"],
        ["ELECTRICAL", "VERIFY"],
        ["PLUMBING", "CONDITIONAL"],
        ["FIRE_LIFE_SAFETY", "NOT_REQUIRED"],
    ]
    assert result["required"] == ["BUILDING"]
    assert result["verification"] == ["ELECTRICAL", "PLUMBING"]
    assert result["notRequired"] == ["FIRE_LIFE_SAFETY"]
    for family, status in result["statuses"]:
        assert f'data-family="{family}"' in result["html"]
        assert f'data-status="{status}"' in result["html"]


@pytest.mark.parametrize("path", SURFACES, ids=lambda path: path.name)
def test_all_nonbinary_result_has_verify_contact_copy_and_no_required_lane(path: Path):
    result = _run_helper(path, {
        "applying_office": "Example Building Department",
        "apply_url": "https://example.gov/apply",
        "family_decisions": [
            {"family": "BUILDING", "status": "VERIFY", "permit_type": "Building verification"},
            {"family": "ELECTRICAL", "status": "NEEDS_INPUT", "permit_type": "Electrical details needed"},
        ],
        "permits_required": [],
    })
    assert result["required"] == []
    assert result["verification"] == ["BUILDING", "ELECTRICAL"]
    rendered = result["html"].lower()
    assert "verify" in rendered
    assert "contact" in rendered


def _function_body(page: str, name: str) -> str:
    marker = f"function {name}("
    start = page.index(marker)
    candidates = [
        page.find("\nfunction ", start + 1),
        page.find("\nasync function ", start + 1),
    ]
    candidates = [index for index in candidates if index != -1]
    end = min(candidates) if candidates else len(page)
    return page[start:end]


@pytest.mark.parametrize("path", SURFACES, ids=lambda path: path.name)
def test_surface_builds_family_view_inside_render_results_before_first_use(path: Path):
    page = path.read_text()
    render_results = _function_body(page, "renderResults")
    declaration = "const familyView = buildPermitFamilyView(d);"
    required_lane = "const permits = familyView.requiredRows;"
    matrix_use = "html += renderPermitFamilyMatrix(familyView, office);"
    verification_lane = "const companionPermits = familyView.verificationRows;"
    assert declaration in render_results
    assert required_lane in render_results
    assert verification_lane in render_results
    assert matrix_use in render_results
    assert render_results.index(declaration) < render_results.index(required_lane)
    assert render_results.index(declaration) < render_results.index(matrix_use)
    assert render_results.index(declaration) < render_results.index(verification_lane)
    assert "if (permits.length > 0) {" in render_results
    if "/trades/" in path.as_posix():
        assert "if(!d.apply_url || !permits.length) return '';" in page
        assert "if (verdict === 'yes') {" in render_results
