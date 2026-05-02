from pathlib import Path
from importlib import util
import json
import subprocess
import textwrap

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_import_server = _debug_helper._import_server

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "frontend" / "index.html"
HTML = INDEX.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    markers = [f"function {name}(", f"async function {name}("]
    starts = [HTML.index(marker) for marker in markers if marker in HTML]
    if not starts:
        raise AssertionError(f"Could not find function {name}")
    start = min(starts)
    candidates = [
        HTML.find("\nfunction ", start + 1),
        HTML.find("\nasync function ", start + 1),
    ]
    candidates = [idx for idx in candidates if idx != -1]
    end = min(candidates) if candidates else len(HTML)
    return HTML[start:end]


def test_phase5_report_view_surfaces_state_overlay_quality_and_citation_footnotes():
    report = _function_body("renderResultAsReport")

    assert "renderStateOverlayReportHtml(d, _esc)" in report
    assert "State overlay guidance" in HTML
    assert "not final legal/code authority" in HTML
    assert "Quality gate warnings" in report
    assert "Claim source footnotes" in report
    assert "No quoted snippet available yet — verify with AHJ." in report
    # State overlay source URLs must be shown as overlay sources, not promoted into code_citation UI.
    assert "state_schema_context" in HTML
    assert "code_citation = state_schema_context" not in HTML
    assert "d.code_citation = d.state_schema_context" not in HTML


def test_phase5_copy_and_download_include_renderer_ready_guidance():
    copy_body = _function_body("copyResult")
    download_body = _function_body("downloadReport")

    assert "STATE OVERLAY GUIDANCE (not final code authority)" in copy_body
    assert "COMPANION PERMITS / REVIEWS TO CHECK" in copy_body
    assert "WHAT TO BRING / UPLOAD" in copy_body
    assert "CLAIM SOURCE FOOTNOTES" in copy_body
    assert "buildStateOverlayCopyLines(d)" in copy_body

    assert "stateOverlayHtml" in download_body
    assert "renderStateOverlayReportHtml(d, efn)" in download_body
    assert "citationFootnotes" in download_body
    assert "State overlay guidance is planning support, not final legal/code authority." in download_body


def test_uncertainty_warnings_include_backend_quality_gate_warnings():
    body = _function_body("buildUncertaintyWarnings")

    assert "Array.isArray(d.quality_warnings)" in body
    assert "Quality gate:" in body


def test_white_label_report_surfaces_state_overlay_without_unsafe_url_promotion(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permits_required": [{"permit_type": "Building Permit — Medical Clinic TI"}],
        "apply_url": "https://city.example.gov/permits",
        "state_schema_context": {
            "state": "TX",
            "state_name": "Texas",
            "vertical": "medical_clinic_ti",
            "triggered_rules": [
                {
                    "title": "Texas medical clinic licensing overlay",
                    "summary": "Outpatient clinic work may need separate healthcare licensing review.",
                    "contractor_guidance": ["Confirm healthcare licensing status before final permit filing."],
                    "watch_out": ["Do not assume a building permit covers healthcare licensing."],
                    "source_title": "Texas HHS licensing guidance",
                    "source_url": "https://hhs.texas.gov/example",
                    "confidence": "medium",
                }
            ],
            "overlay_slots": [
                {
                    "verified_sources": [
                        {"title": "Texas official source", "url": "https://hhs.texas.gov/example", "verified_on": "2026-05-02"},
                        {"title": "Unsafe", "url": "javascript:alert(1)", "verified_on": "2026-05-02"},
                    ]
                }
            ],
        },
    }

    html = server.render_white_label_report_html({
        "contractor_name": "Boban Build Co",
        "job_type": "medical clinic TI",
        "city": "Austin",
        "state": "TX",
        "result": result,
    })

    assert "State overlay guidance" in html
    assert "Texas medical clinic licensing overlay" in html
    assert "Confirm healthcare licensing status" in html
    assert "https://hhs.texas.gov/example" in html
    assert "javascript:" not in html
    assert "not final legal/code authority" in html


def test_frontend_state_overlay_runtime_filters_unsafe_urls_and_malformed_arrays(tmp_path):
    script = "\n".join([
        "const esc = (v) => String(v ?? '').replace(/[&<>\\\"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','\\\"':'&quot;',\"'\":'&#039;'}[ch]));",
        _function_body("safeExternalUrl"),
        _function_body("normalizeStateOverlayReportData"),
        _function_body("buildStateOverlayCopyLines"),
        _function_body("renderStateOverlayReportHtml"),
        textwrap.dedent(
            """
            const sample = {
              state_schema_context: {
                state_name: 'Texas',
                vertical: 'medical clinic TI',
                triggered_rules: [{
                  title: 'Rule',
                  summary: 'Plan check may apply.',
                  contractor_guidance: 'malformed string should not throw',
                  watch_out: {bad: true},
                  source_title: 'Bad source title',
                  source_url: 'javascript:alert(1)',
                  secondary_source_title: 'Good source',
                  secondary_source_url: 'https://agency.example.gov/path?q=1'
                }],
                overlay_slots: [{ verified_sources: [
                  {title: 'Unsafe data source', url: 'data:text/html,boom'},
                  {title: 'Relative source', url: '/local/path'},
                  {title: 'Good source', url: 'https://agency.example.gov/path?q=1'}
                ]}]
              },
              claim_citations: [{source_url: 'javascript:alert(2)'}]
            };
            const normalized = normalizeStateOverlayReportData(sample);
            const copy = buildStateOverlayCopyLines(sample).join(String.fromCharCode(10));
            const html = renderStateOverlayReportHtml(sample, esc);
            console.log(JSON.stringify({normalized, copy, html}));
            """
        ),
    ])
    script_path = tmp_path / "phase5-overlay-runtime.js"
    script_path.write_text(script, encoding="utf-8")
    completed = subprocess.run(["node", str(script_path)], check=True, capture_output=True, text=True)
    out = json.loads(completed.stdout)

    assert out["normalized"]["sources"] == [{"title": "Good source", "url": "https://agency.example.gov/path?q=1", "verified_on": ""}]
    assert "javascript:" not in out["copy"]
    assert "data:text" not in out["html"]
    assert "href=\"https://agency.example.gov/path?q=1\"" in out["html"]


def test_frontend_claim_citation_surfaces_only_safe_source_urls():
    report = _function_body("renderResultAsReport")
    copy_body = _function_body("copyResult")
    download_body = _function_body("downloadReport")

    assert "safeExternalUrl(c.source_url)" in report
    assert "safeExternalUrl(c.source_url)" in copy_body
    assert "safeExternalUrl(c.source_url)" in download_body
    assert "c.source_url ? ` Source: ${c.source_url}`" not in copy_body


def test_frontend_result_view_legacy_links_are_sanitized():
    result_body = _function_body("renderResults")

    assert "const safeMaps = safeExternalUrl(maps);" in result_body
    assert "const safeApplyUrl = safeExternalUrl(applyUrl);" in result_body
    assert "const safeApplyPdf = safeExternalUrl(applyPdf);" in result_body
    assert 'href="${esc(maps)}"' not in result_body
    assert 'href="${esc(applyUrl)}"' not in result_body
    assert 'href="${esc(applyPdf)}"' not in result_body


def test_white_label_legacy_sources_portal_and_maps_are_sanitized(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    html = server.render_white_label_report_html({
        "contractor_name": "Boban Build Co",
        "job_type": "office TI",
        "city": "Austin",
        "state": "TX",
        "result": {
            "apply_url": "javascript:alert(1)",
            "apply_google_maps": "data:text/html,boom",
            "sources": ["javascript:alert(2)", "https://agency.example.gov/source"],
            "permits_required": [{"permit_type": "Building Permit", "required": True}],
        },
    })

    assert "javascript:" not in html
    assert "data:text" not in html
    assert "https://agency.example.gov/source" in html
    assert "noopener noreferrer" in html
