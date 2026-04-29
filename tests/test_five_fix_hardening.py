"""Deterministic hardening tests for the 2026-04-28 five-fix review.

These tests intentionally avoid live LLM/API calls. They lock the exact template
leaks and underpriced-fee regressions called out in the four-city Opus review.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from api.research_engine import sanitize_free_text_urls  # noqa: E402
from api.fee_realism_guardrail import apply_fee_realism_guardrail  # noqa: E402


def test_bug1_strips_four_city_hallucinated_fee_urls_from_free_text():
    result = {
        "applying_office": "Phoenix Planning and Development Department",
        "fee_range": "Verify fee in https://ojp.gov/pdffiles1/Digitization/10429NCJRS.pdf and https://archive.org/details/dailycolonist1978.",
        "confidence_reason": "Also cited https://www.kauffman.org/wp-content/uploads/NETS_US_PublicFirms2013.xlsx plus http://pw.lacounty.gov for LA.",
    }

    sanitize_free_text_urls(result, "Phoenix", "AZ")
    combined = f"{result['fee_range']} {result['confidence_reason']}"

    for junk in ("ojp.gov", "archive.org", "kauffman.org", "pw.lacounty.gov"):
        assert junk not in combined
    assert "[verify with Phoenix Planning and Development Department]" in combined
    assert result["_url_strips"]


def test_bug5_commercial_restaurant_ti_fee_floor_overrides_residential_trade_anchors():
    result = {
        "fee_range": "$219 electrical + $558 HVAC",
        "hidden_triggers": [
            {"key": "commercial_kitchen_hood"},
            {"key": "grease_interceptor"},
            {"key": "change_of_occupancy"},
        ],
    }

    guarded = apply_fee_realism_guardrail(
        result,
        "3,200 sf commercial restaurant tenant improvement with Type I hood, grease interceptor, ADA restroom, patio",
        "Phoenix",
        "AZ",
        "commercial_restaurant",
    )

    assert guarded["_fee_adjusted"] is True
    assert guarded["_fee_floor_components"]["scope"] == "commercial_restaurant"
    assert guarded["_fee_floor_components"]["structured_low"] >= 15000
    assert "$219" not in guarded["fee_range"]
    assert "structured floor" in guarded["fee_range"]


def test_bug5_residential_trade_scope_remains_noop():
    result = {"fee_range": "$219-$558"}

    guarded = apply_fee_realism_guardrail(
        result,
        "replace residential electrical panel",
        "Phoenix",
        "AZ",
        "residential",
    )

    assert guarded["fee_range"] == "$219-$558"
    assert guarded["_fee_floor_check"] == "residential_no_override"


def test_bugs2_3_4_frontend_commercial_template_leaks_are_hardened():
    html = (ROOT / "frontend" / "index.html").read_text()

    # Bug 2: commercial scopes must hard-gate away from residential deck/patio fail points.
    assert "const COMMERCIAL_FAIL_POINTS" in html
    assert "primaryScope.startsWith('commercial_')" in html
    assert "return COMMERCIAL_FAIL_POINTS[primaryScope]" in html
    assert "outdoor patio" in html and "leaked into 4/4 commercial restaurant TIs" in html

    # Bug 3: residential electrical checklist card must be suppressed on commercial scopes.
    checklist_fn_start = html.index("function getInspectionChecklist")
    checklist_fn = html[checklist_fn_start: html.index("// ── Disambiguation Logic", checklist_fn_start)]
    assert "return null" in checklist_fn
    assert "primaryScope.startsWith('commercial_')" in checklist_fn
    assert "GFCI/AFCI" in checklist_fn

    # Bug 4: commercial customer justifier must not use homeowner / home-sale copy.
    explanation_start = html.index("function generateCustomerPermitExplanation")
    explanation_fn = html[explanation_start: html.index("function toggleCustomerExplanation", explanation_start)]
    commercial_start = explanation_fn.index("if (isCommercial)")
    commercial_return = explanation_fn.index("return `Why Your", commercial_start)
    residential_return = explanation_fn.index("return `Why Your", commercial_return + 1)
    commercial_branch = explanation_fn[commercial_start:residential_return]
    assert "Certificate of Occupancy" in commercial_branch
    assert "lease violations" in commercial_branch
    assert "business-insurance" in commercial_branch
    assert "homeowner" not in commercial_branch.lower()
    assert "selling your home" not in commercial_branch.lower()
