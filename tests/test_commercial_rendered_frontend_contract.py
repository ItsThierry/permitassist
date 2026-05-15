from pathlib import Path

FRONTEND_INDEX = Path(__file__).resolve().parents[1] / "frontend" / "index.html"
HTML = FRONTEND_INDEX.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    markers = [f"function {name}(", f"async function {name}("]
    starts = [HTML.index(marker) for marker in markers if marker in HTML]
    if not starts:
        raise AssertionError(f"Could not find function {name}")
    start = min(starts)
    candidates = [HTML.find("\nfunction ", start + 1), HTML.find("\nasync function ", start + 1)]
    candidates = [idx for idx in candidates if idx != -1]
    end = min(candidates) if candidates else len(HTML)
    return HTML[start:end]


def test_frontend_establishes_canonical_rendering_context_before_rendering():
    body = _function_body("renderResults")
    assert "const renderContext = getRenderingContext(d, job, city, state)" in body
    assert "renderContext.allow_homeowner_template && searchMode === 'homeowner'" in body
    assert "const permits = normalizePermitDisplayData(d, renderContext)" in body
    assert "const safePermitName = getPermitDisplayName(d, renderContext)" in body


def test_frontend_never_uses_raw_fallback_names_for_display_slots():
    assert "function isFallbackPermitName(" in HTML
    assert "function sanitizePermitDisplayName(" in HTML
    body = _function_body("renderResults")
    assert "const exactPermitName = safePermitName" in body
    assert "const portalName = safePermitName" in body
    assert "const permitName = safePermitName" in body
    forbidden = "portalSel || d.permit_name || d.permit_type || permits[0]?.permit_type"
    assert forbidden not in body


def test_frontend_confidence_badge_is_core_claim_aware():
    body = _function_body("renderResults")
    assert "const confBadgeHtml = buildConfidenceBadgeHtml(d, renderContext, city, state)" in body
    badge_body = _function_body("buildConfidenceBadgeHtml")
    assert "corePermitUnverified(d)" in badge_body
    assert "Needs AHJ verification" in badge_body
    assert "Verified · official sources" in badge_body
    assert "hasLocalAhjSource(d, city, state)" in badge_body


def test_frontend_sources_are_jurisdiction_filtered_before_display():
    assert "function classifySourceForJurisdiction(" in HTML
    assert "function getOfficialSourceLinks(" in HTML
    assert "function normalizeStateForAhjKey(" in HTML
    assert "STATE_ABBR_BY_NAME" in HTML
    assert "'philadelphia|pa': ['phila.gov', 'permits.phila.gov']" in HTML
    body = _function_body("renderResults")
    assert "const sourceLinks = getOfficialSourceLinks(d, city, state)" in body
    assert "Array.isArray(d.sources) ? d.sources.filter(Boolean).slice(0, 5)" not in body


def test_frontend_uses_vertical_inspection_profiles_when_engine_output_is_sparse_or_wrong():
    assert "function resolveInspectionItems(" in HTML
    resolver = _function_body("resolveInspectionItems")
    assert "commercial_restaurant" in resolver
    assert "Hood and grease duct inspection" in HTML
    assert "Medical gas, imaging/shielding" in HTML
    assert "Commercial alteration / tenant-improvement" in HTML
    body = _function_body("renderResults")
    assert "const insps = normalizeInspectionItems(resolveInspectionItems(d, renderContext, job))" in body
