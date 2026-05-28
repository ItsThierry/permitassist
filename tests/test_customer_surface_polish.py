import json
import re
import sys
from importlib import util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "api"
FRONTEND_INDEX = ROOT / "frontend" / "index.html"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))


def _load_server(tmp_path, monkeypatch):
    helper_spec = util.spec_from_file_location(
        "debug_headers_helper_customer_surface_polish",
        Path(__file__).with_name("test_debug_headers_endpoint.py"),
    )
    assert helper_spec is not None
    assert helper_spec.loader is not None
    helper = util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)
    return helper._import_server(tmp_path, monkeypatch)


def _surface_text(value) -> str:
    return json.dumps(value, sort_keys=True, default=str).lower()


def test_customer_view_model_polishes_internal_terms_placeholders_and_fragments_without_thinning_rich_output(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    result = {
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_kind": "Commercial Building / Tenant Improvement",
        "permit_name": "Commercial Building Permit — Restaurant Tenant Improvement",
        "permits_required": [{"permit_type": "Commercial Building Permit — Restaurant Tenant Improvement", "required": True}],
        "applying_office": "City of Atlanta Office of Buildings",
        "customer_next_step": "Use source-backed evidence and verify in [verify with City of Atlanta Office of Buildings] before quoting.",
        "summary": "Atlanta, GA.exterior/signage work.signage review may apply with restaurant TI buildout.",
        "job_summary": "Restaurant tenant improvement with hood, grease interceptor, plumbing, electrical, and dining-room accessibility scope.",
        "approval_timeline": {"simple": "2 to 6 weeks depending on review cycles"},
        "fee_range": "$18,000-$42,000 planning estimate; source-backed threshold uses jurisdiction multiplier, ti floor, and ADA-path-of-travel adder; confirm before filing.",
        "confidence_reason": "source-backed threshold and source-backed evidence; needs_verification for quoted snippet.",
        "last_updated": "Unknown",
        "sources": [{"url": "https://www.atlantaga.gov/georgia/government/departments/city-planning/building-permits", "title": "Atlanta Building Permits"}],
        "checklist": [
            "Submit restaurant TI plans and fixture schedule.",
            "Include hood, grease interceptor, plumbing, fire/life-safety, health, signage, and accessibility scope.",
        ],
        "companion_permits": [
            {"permit_type": "Mechanical / Hood Review", "certainty": "likely", "reason": "Kitchen Type I hood scope."},
            {"permit_type": "Health Department Review", "certainty": "likely", "reason": "Food-service tenant improvement."},
            {"permit_type": "Sign Permit", "certainty": "likely", "reason": "Exterior signage work."},
        ],
        "hidden_triggers": [
            {"title": "Health review", "why_it_matters": "Food-service buildout can require health approval.", "severity": "high"},
        ],
    }

    job = "Atlanta GA commercial restaurant tenant improvement with commercial kitchen, Type I hood, grease interceptor, health review, and exterior signage"
    out = server.finalize_permit_lookup_result(result, job, "Atlanta", "GA", evidence_allowed=False)
    public = server.build_customer_permit_view_model(out, job, "Atlanta", "GA")
    text = _surface_text(public)

    assert public["permit_decision"] == "REQUIRED"
    assert public["permit_verdict"] == "YES"
    assert public.get("companion_permits") and len(public["companion_permits"]) >= 2
    assert public.get("checklist") and len(public["checklist"]) >= 2
    assert public.get("sources")
    assert "hood" in text and "health" in text and "sign" in text

    banned = (
        "last updated: unknown",
        "invalid date",
        "[verify",
        "${",
        "{{",
        "}}",
        "source-backed threshold",
        "source-backed evidence",
        "source-backed exemption",
        "needs_verification",
        "fail_closed",
        "verify before merging",
        "structured floor",
        "ti floor",
        "jurisdiction multiplier",
        "ada-path-of-travel adder",
        "atlanta, ga.exterior",
        "work.signage",
    )
    for token in banned:
        assert token not in text
    assert not re.search(r"\bpending\b", text)

    assert not server.lint_customer_visible_result(public, "Atlanta", "GA")


def test_url_replacement_helpers_return_customer_safe_prose_not_bracket_scaffolding():
    sys.modules.pop("research_engine", None)
    import research_engine

    phoenix = {
        "applying_office": "City of Phoenix Planning & Development Department",
        "fee_range": "Verify fee in https://ojp.gov/pdffiles1/Digitization/10429NCJRS.pdf before quoting.",
    }
    cleaned_phoenix = research_engine.sanitize_free_text_urls(dict(phoenix), "Phoenix", "AZ")
    phoenix_text = _surface_text({"fee_range": cleaned_phoenix.get("fee_range"), "applying_office": cleaned_phoenix.get("applying_office")})

    atlanta = {
        "applying_office": "City of Atlanta Office of Buildings",
        "confidence_reason": "Matched the wrong portal https://archive.org/details/dailycolonist1978 for fee backup.",
    }
    cleaned_atlanta = research_engine.sanitize_free_text_urls(dict(atlanta), "Atlanta", "GA")
    atlanta_text = _surface_text({"confidence_reason": cleaned_atlanta.get("confidence_reason"), "applying_office": cleaned_atlanta.get("applying_office")})

    assert "[verify" not in phoenix_text
    assert "[verify" not in atlanta_text
    assert "ojp.gov" not in phoenix_text
    assert "archive.org" not in atlanta_text
    assert "city of phoenix planning & development department" in phoenix_text
    assert "city of atlanta office of buildings" in atlanta_text


def test_permit_decision_plain_language_replaces_source_backed_customer_copy():
    import permit_decision

    not_required = permit_decision._safe_next_step({}, permit_decision.PERMIT_DECISION_NOT_REQUIRED, "Minor Repair", "Seattle", "WA")
    conditional = permit_decision._safe_next_step(
        {"condition_threshold": {"threshold": "under 120 square feet and no utilities"}},
        permit_decision.PERMIT_DECISION_CONDITIONAL,
        "Accessory Structure",
        "Seattle",
        "WA",
    )
    cleaned = permit_decision._strip_customer_banned_text(
        {
            "next_step": "source-backed threshold plus source-backed evidence; needs_verification pending.",
            "reason": "source-backed exemption applies only below the listed condition.",
        }
    )
    text = _surface_text({"not_required": not_required, "conditional": conditional, "cleaned": cleaned})

    for token in ("source-backed threshold", "source-backed evidence", "source-backed exemption", "needs_verification", "pending"):
        assert token not in text
    assert "official source" in text or "local building department" in text or "listed condition" in text


def test_frontend_hero_uses_safe_freshness_and_concise_fee_copy_while_report_keeps_fee_detail():
    html = FRONTEND_INDEX.read_text(encoding="utf-8")
    render_block = html[html.index("function renderResults") : html.index("// ── Report Template Renderer")]

    assert "Last updated: ${heroUpdatedLabel}" not in render_block
    assert "heroFreshnessLabel" in render_block
    assert "Source date not published" in html
    assert "conciseHeroFeeCopy" in html
    assert "const heroFeeBadge" in render_block
    assert "💰 ${esc(heroFeeBadge)}" in render_block
    assert "<div class=\"big-price\">${esc(fee)}</div>" in render_block
