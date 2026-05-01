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
    assert "body: JSON.stringify({ email, job, city, state, data: { ...data, inspections: normalizeInspectionItems(data.inspections || []) } })" in body
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
    sync_body = _function_body("syncLookupCounterFromResponse")
    assert "remaining<0" in sync_body
    assert sync_body.index("remaining<0") < sync_body.index("used>=0")
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


def test_commercial_justifier_header_is_scope_aware_not_homeowner_only():
    body = _function_body("renderResults")
    justifier_start = body.index("const justifierIntro")
    justifier_block = body[justifier_start: body.index("// City contractor registration alert", justifier_start)]
    assert "isCommercialPrimaryScope(d?._primary_scope)" in justifier_block
    assert "tenant, landlord, or owner" in justifier_block
    assert "homeowner fast" in justifier_block


def test_inspection_items_are_normalized_and_empty_placeholders_hidden():
    body = _function_body("normalizeInspectionItems")
    assert "typeof item === 'string'" in body
    assert "stage:'Inspection'" in body
    assert "/^inspection$/i.test(title)" in body
    render_body = _function_body("renderResults")
    assert "const insps = normalizeInspectionItems(d.inspections || [])" in render_body
    assert "normalizeInspectionItems(d.inspections || []).length" in HTML
    assert "normalizeInspectionItems(Array.isArray(d.inspect_checklist)" in HTML
    assert ".map(ins => ins.title || ins.stage || ins.description || ins.notes || '')" in HTML
