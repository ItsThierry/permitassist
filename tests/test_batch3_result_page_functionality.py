from pathlib import Path

FRONTEND_INDEX = Path(__file__).resolve().parents[1] / "frontend" / "index.html"
HTML = FRONTEND_INDEX.read_text(encoding="utf-8")


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


def test_report_view_keeps_all_result_actions_available():
    body = _function_body("renderResultAsReport")
    required_actions = [
        "renderStandardView()",
        "copyResult()",
        "shareResult()",
        "emailToForeman()",
        "window.print()",
        "flagAsWrong(this)",
    ]
    for action in required_actions:
        assert action in body
    assert "copy-feedback" in body
    assert "flag-feedback-inline" in body


def test_email_to_foreman_uses_event_listeners_not_inline_interpolated_fetch():
    body = _function_body("emailToForeman")
    assert "addEventListener('click', send)" in body
    assert "addEventListener('keydown'" in body
    assert "body: JSON.stringify({ email, job, city, state, data })" in body
    assert "onclick=\"(function()" not in body
    assert "window._lastLookupResult||{}" not in body
    assert "job:'${esc(job)}'" not in body


def test_free_lookup_counter_syncs_server_state_and_hides_for_paid_users():
    body = _function_body("updateCounterBarFromServer")
    assert "_serverFreeLookupsUsed = normalizedUsed" in body
    assert "_userIsPaid = false" in body
    assert "normalizedRemaining<0" in body
    assert "_userIsPaid = true" in body
    assert "Math.max(0,Math.min(100" in body
    assert "Math.min(normalizedUsed,total)" in body


def test_lookup_counter_only_advances_from_server_response_not_client_side_guess():
    assert "function incrementLookup(){return getLookupCount()}" in HTML
    submit_body = _function_body("doLookup")
    assert "syncLookupCounterFromResponse(res,data);" in submit_body
    assert "incrementLookup()" not in submit_body


def test_inspection_accordion_and_result_buttons_present_in_standard_view():
    body = _function_body("renderResults")
    assert "Don't Fail This Inspection" in body
    assert "toggleChecklist(this)" in body
    for action in [
        "shareResult()",
        "copyResult()",
        "emailToForeman()",
        "downloadReport()",
        "printChecklist()",
        "saveJobToTracker()",
    ]:
        assert action in body
