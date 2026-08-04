from pathlib import Path

import pytest


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
HOME_PAGES = ("index.html", "preview-modern-reskinned-index.html")
TRADE_PAGES = tuple(f"trades/{name}.html" for name in ("electrical", "hvac", "plumbing", "roofing", "solar"))
PAGES = HOME_PAGES + TRADE_PAGES


@pytest.mark.parametrize("page", PAGES)
def test_structured_precharge_decline_has_a_dedicated_customer_surface(page: str) -> None:
    html = (FRONTEND / page).read_text(encoding="utf-8")

    assert 'id="lookup-decline"' in html
    assert 'role="alert"' in html
    assert "STRUCTURED_PRECHARGE_OUTCOMES" in html
    assert "'UNSUPPORTED'" in html
    assert "'NEEDS_FACT'" in html
    assert "'INFRA_FAILURE'" in html
    assert "STRUCTURED_PRECHARGE_OUTCOMES.has(data.coverage_outcome)" in html
    assert "data.report_created === false" in html
    assert "data.retained_charge === false" in html
    assert "const declineMessage = data.message" in html
    assert "No report was created and no lookup was charged." in html


@pytest.mark.parametrize("page", PAGES)
def test_structured_decline_returns_before_result_or_history_creation(page: str) -> None:
    html = (FRONTEND / page).read_text(encoding="utf-8")

    decline = html.index("const isStructuredDecline")
    success = html.index("currentResult = {", decline)
    branch = html[decline:success]

    assert "if (isStructuredDecline)" in branch
    assert "currentResult = null" in branch
    assert "pendingResult = null" in branch
    assert "results-inner" in branch
    assert "return;" in branch
    assert "renderCompletedLookupResult" not in branch
    assert "saveToHistory" not in branch


@pytest.mark.parametrize("page", PAGES)
def test_other_non_ok_responses_cannot_fall_through_to_success(page: str) -> None:
    html = (FRONTEND / page).read_text(encoding="utf-8")

    decline = html.index("const isStructuredDecline")
    success = html.index("currentResult = {", decline)
    branch = html[decline:success]

    assert "if (!res.ok) throw new Error(data.message || data.error || 'Lookup could not be completed.');" in branch


def test_production_page_cannot_recover_a_stale_prior_result_after_request_failure() -> None:
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    request_start = html.index("const declineEl = document.getElementById('lookup-decline');")
    fetch_start = html.index("const res = await fetch('/api/permit'", request_start)
    catch_start = html.index("} catch (err) {", fetch_start)
    catch_end = html.index("\n  }\n}", catch_start)

    assert "window._lastCompletedLookup = null;" in html[request_start:fetch_start]
    assert "recoverCompletedLookupResult" not in html[catch_start:catch_end]


@pytest.mark.parametrize("page", PAGES)
def test_source_backed_supported_results_are_not_labeled_as_ai_or_registry_fallback(page: str) -> None:
    html = (FRONTEND / page).read_text(encoding="utf-8")
    assert "function isSourceBackedSupported(" in html
    assert "Official source-backed exact scope" in html
    assert "launch_coverage_registry" in html


@pytest.mark.parametrize("page", HOME_PAGES)
def test_source_backed_supported_results_suppress_generic_trade_templates(page: str) -> None:
    html = (FRONTEND / page).read_text(encoding="utf-8")
    fail_points = html.split("function getInspectionFailPoints(", 1)[1].split("function ", 1)[0]
    checklist = html.split("function getInspectionChecklist(", 1)[1].split("function ", 1)[0]
    assert "isSourceBackedSupported(result)" in fail_points
    assert "return []" in fail_points
    assert "isSourceBackedSupported(result)" in checklist
    assert "return null" in checklist


@pytest.mark.parametrize("page", TRADE_PAGES)
def test_trade_entry_pages_suppress_generic_inspection_templates_for_source_backed_results(page: str) -> None:
    html = (FRONTEND / page).read_text(encoding="utf-8")
    checklist = html.split("function getInspectionChecklist(", 1)[1].split("function ", 1)[0]
    assert "isSourceBackedSupported(result)" in checklist
    assert "return null" in checklist
    render = html.split("// Inspection checklist", 1)[1].split("//", 1)[0]
    assert "getInspectionChecklist(job, d)" in render
    assert "if (inspChecklist)" in render
