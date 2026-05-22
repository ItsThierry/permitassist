from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
REPORT = (ROOT / "frontend" / "report.html").read_text(encoding="utf-8")
SERVER = (ROOT / "api" / "server.py").read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    markers = [f"function {name}(", f"async function {name}("]
    starts = [source.index(marker) for marker in markers if marker in source]
    assert starts, f"{name} not found"
    start = min(starts)
    candidates = [source.find("\nfunction ", start + 1), source.find("\nasync function ", start + 1)]
    candidates = [idx for idx in candidates if idx != -1]
    return source[start:min(candidates) if candidates else len(source)]


def test_downloaded_report_uses_real_pdf_endpoint_and_headers():
    assert 'path == "/api/report-pdf"' in SERVER
    assert '"Content-Type", "application/pdf"' in SERVER
    assert "Content-Disposition" in SERVER
    body = _function_body(FRONTEND, "downloadReport")
    assert "fetch('/api/report-pdf'" in body
    assert ".pdf`" in body
    assert "text/html;charset" not in body
    assert ".html" not in body


def test_first_lookup_is_not_blocked_by_email_gate():
    body = _function_body(FRONTEND, "doLookup")
    assert "showPreLookupGate();" not in body
    assert "requireEmailForDetail" in FRONTEND
    assert "The permit verdict is free" in FRONTEND


def test_customer_badges_do_not_use_ai_estimate_wording():
    assert "AI estimate" not in FRONTEND
    assert "Pending Verification" in FRONTEND
    assert "State Code Default" in FRONTEND


def test_fee_surfaces_are_split_into_customer_safe_components():
    assert "Plan Check Deposit / Upfront" in FRONTEND
    assert "Permit Issuance Fee" in FRONTEND
    assert "Plan Check Deposit / Upfront" in REPORT
    assert "Permit Issuance Fee" in REPORT
    assert "fee_breakdown_text" in SERVER
    assert '"fees": fee_breakdown_text(result)' in SERVER


def test_commercial_autocomplete_covers_launch_verticals_and_triggers():
    for example in [
        "Restaurant TI",
        "Medical clinic TI",
        "Office TI",
        "Commercial remodel",
        "Medical gas installation",
        "Grease interceptor installation",
    ]:
        assert example in FRONTEND
