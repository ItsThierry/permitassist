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
        "debug_headers_helper_universal_quality",
        Path(__file__).with_name("test_debug_headers_endpoint.py"),
    )
    assert helper_spec is not None
    assert helper_spec.loader is not None
    helper = util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)
    return helper._import_server(tmp_path, monkeypatch)


def _surface_text(value) -> str:
    return json.dumps(value, sort_keys=True).lower()


def test_lookup_success_uses_deterministic_render_recovery_not_history_only():
    html = FRONTEND_INDEX.read_text(encoding="utf-8")
    assert "function renderCompletedLookupResult(" in html
    assert "function recoverCompletedLookupResult(" in html
    assert "window._lastCompletedLookup" in html
    assert "open recovered result" in html.lower()

    success_block = html[html.index("async function doLookup()") : html.index("// ── Voice Input")]
    assert "renderCompletedLookupResult(currentResult);" in success_block
    catch_block = success_block[success_block.index("} catch (err) {") :]
    assert "if (recoverCompletedLookupResult(err)) return;" in catch_block


def test_wrong_state_specific_customer_claims_are_removed_but_cross_applicable_ada_remains(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    contaminated = {
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_kind": "Commercial Building / Tenant Improvement",
        "permit_name": "Commercial Building / Tenant Improvement Permit",
        "permits_required": [
            {"permit_type": "Commercial Building / Tenant Improvement Permit", "required": True},
            {"permit_type": "Electrical Permit", "required": True},
        ],
        "applying_office": "Dallas Development Services Department",
        "sources": [{"url": "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection", "title": "Dallas Building Inspection"}],
        "checklist": [
            "Submit Dallas commercial TI plans and electrical load summary.",
            "Provide California Title 24 energy forms before plan review.",
            "Attach WSEC envelope compliance worksheet.",
            "ADA accessible route details may apply for public accommodation alterations under federal accessibility rules.",
        ],
        "pro_tips": [
            "Do not forget Oregon/Washington seismic strapping notes for water heaters.",
            "Confirm NEC panel schedules for relocated lighting circuits.",
        ],
        "related_permits": [
            {"permit_type": "California Title 24 energy permit"},
            {"permit_type": "Accessibility review (ADA/federal)"},
        ],
    }

    out = server.finalize_permit_lookup_result(
        contaminated,
        "Dallas TX commercial office tenant improvement, non-structural partitions, lighting relocation, minor electrical/data",
        "Dallas",
        "TX",
        evidence_allowed=False,
    )
    public = server.build_customer_permit_view_model(out, "office tenant improvement", "Dallas", "TX")
    text = _surface_text(public)

    for forbidden in ("title 24", "wsec", "california", "oregon", "washington", "seismic strapping"):
        assert forbidden not in text
    assert "ada" in text or "accessibility" in text
    assert "nec" in text or "electrical" in text
    assert public["permit_decision"] == "REQUIRED"
    assert public["permit_kind"] == "Commercial Building / Tenant Improvement"


def test_normalized_customer_answer_fields_are_canonical_and_freshness_consistent(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    result = {
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_kind": "Plumbing",
        "permit_name": "Residential Plumbing Permit — Water Heater Replacement",
        "permits_required": [{"permit_type": "Residential Plumbing Permit — Water Heater Replacement", "required": True}],
        "applying_office": "City of Phoenix Planning & Development Department",
        "fee_range": "Confirm with the department; fees vary by fixture and valuation.",
        "approval_timeline": {"simple": "same day to 5 business days"},
        "last_updated": "Unknown",
        "verified_date": "2026-05-27",
        "sources": [{"url": "https://www.phoenix.gov/pdd/development/permits", "title": "Phoenix Planning & Development Permits"}],
    }

    out = server.finalize_permit_lookup_result(
        result,
        "Phoenix AZ like-for-like 50-gallon gas water heater replacement in same location, no plumbing relocation",
        "Phoenix",
        "AZ",
        evidence_allowed=False,
    )
    public = server.build_customer_permit_view_model(out, "water heater replacement", "Phoenix", "AZ")
    summary = public.get("customer_result_summary")

    assert isinstance(summary, dict)
    assert summary["permit_decision"] == public["permit_decision"] == "REQUIRED"
    assert summary["permit_kind"] == public["permit_kind"] == "Plumbing"
    assert summary["permit_name"] == public["permit_name"]
    assert summary["ahj_department"] == public["applying_office"]
    assert summary["next_step"] == public["customer_next_step"]
    assert summary["timeline"]
    assert summary["fee_cost_caveat"]
    assert summary["freshness_label"] != "Last updated: Unknown"
    assert "unknown" not in summary["freshness_label"].lower()
    assert "verified" in summary["source_cue"].lower() or "source" in summary["source_cue"].lower()

    rendered_text = _surface_text(public)
    assert "last updated: unknown" not in rendered_text
    assert "permit permit" not in rendered_text


def _quality_lint_hits(text: str) -> list[str]:
    patterns = {
        "stutter_permit_permit": r"\bpermit\s+permit\b",
        "unfilled_braces": r"\{\{|\}\}",
        "unfilled_js_template": r"\$\{[^}]+\}",
        "unknown_freshness": r"last\s+updated\s*:\s*unknown",
        "repeated_caveat": r"verify\s+with\s+the\s+building\s+department.{0,80}verify\s+with\s+the\s+building\s+department",
    }
    return [name for name, pattern in patterns.items() if re.search(pattern, text, flags=re.I | re.S)]


def _assert_customer_quality(server, result: dict, job: str, city: str, state: str, forbidden: tuple[str, ...] = (), required_terms: tuple[str, ...] = ()):  # noqa: ANN001
    out = server.finalize_permit_lookup_result(result, job, city, state, evidence_allowed=False)
    public = server.build_customer_permit_view_model(out, job, city, state)
    text = _surface_text(public)
    summary = public.get("customer_result_summary")

    assert isinstance(public, dict) and public
    assert isinstance(summary, dict)
    assert public.get("permit_decision") in {"REQUIRED", "CONDITIONAL", "NOT_REQUIRED", "FAIL_CLOSED_UNSUPPORTED_OR_NO_EVIDENCE"}
    assert summary.get("permit_decision") == public.get("permit_decision")
    assert public.get("permit_kind")
    assert summary.get("permit_kind") == public.get("permit_kind")
    assert public.get("customer_next_step")
    assert summary.get("next_step") == public.get("customer_next_step")
    assert public.get("applying_office") or public.get("building_dept_name")
    assert summary.get("ahj_department")
    assert summary.get("timeline")
    assert summary.get("fee_cost_caveat")
    assert summary.get("source_cue")
    assert "source" in summary.get("source_cue", "").lower() or "verified" in summary.get("source_cue", "").lower()
    assert public.get("sources")
    for source in public.get("sources") or []:
        assert isinstance(source, dict)
        assert source.get("url", "").startswith("https://")
        assert source.get("title") and not str(source.get("title")).startswith("http")
        assert source.get("publisher")
        assert source.get("source_type")
        assert source.get("jurisdiction")
    assert not _quality_lint_hits(text)
    assert not server.lint_customer_visible_result(public, city, state)
    first_screen = public.get("customer_first_screen_summary")
    assert isinstance(first_screen, dict)
    assert first_screen.get("decision") == summary.get("permit_decision")
    assert first_screen.get("kind_category") == summary.get("permit_kind")
    assert first_screen.get("next_action") == summary.get("next_step")
    assert first_screen.get("ahj_department") == summary.get("ahj_department")
    assert first_screen.get("source_cue") == summary.get("source_cue")
    for token in forbidden:
        assert token.lower() not in text
    for token in required_terms:
        assert token.lower() in text

    useful_fact_count = 0
    for key in ("checklist", "pro_tips", "requirements", "documents_needed", "permits_required", "companion_permits"):
        value = public.get(key)
        if isinstance(value, list):
            useful_fact_count += len(value)
        elif value:
            useful_fact_count += 1
    assert useful_fact_count >= 2

    report_html = server.render_white_label_report_html({"result": public, "job_type": job, "city": city, "state": state})
    assert str(summary["permit_kind"]).split(" /")[0] in report_html
    assert str(summary["next_step"]).split(".")[0][:40] in report_html
    return public


def test_cached_customer_fixture_matrix_quality_contract(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    fixtures = [
        {
            "name": "Dallas TX commercial office TI",
            "job": "2,500 sq ft Dallas TX commercial office tenant improvement with non-structural partitions, lighting relocation, minor electrical/data",
            "city": "Dallas",
            "state": "TX",
            "forbidden": ("title 24", "wsec", "california", "oregon", "washington", "seismic strapping"),
            "required_terms": ("ada", "nec"),
            "result": {
                "permit_decision": "REQUIRED",
                "permit_verdict": "YES",
                "permit_kind": "Commercial Building / Tenant Improvement",
                "permit_name": "Commercial Building / Tenant Improvement Permit",
                "applying_office": "Dallas Development Services Department",
                "customer_next_step": "Start with Dallas commercial building permit intake and attach trade sheets for electrical/data scope.",
                "approval_timeline": {"simple": "same day over-the-counter"},
                "fee_range": "Fees vary by declared valuation and plan-review fees; confirm in the Dallas fee schedule.",
                "verified_date": "2026-05-27",
                "sources": [{"url": "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection", "title": "Dallas Building Inspection"}],
                "checklist": ["Submit Dallas commercial TI plans and electrical load summary.", "Provide California Title 24 energy forms.", "ADA accessible route details may apply under federal accessibility rules."],
                "pro_tips": ["Confirm NEC panel schedules for relocated lighting circuits.", "Do not forget Oregon/Washington seismic strapping notes."],
            },
        },
        {
            "name": "Phoenix AZ residential water heater",
            "job": "Phoenix AZ residential like-for-like 50-gallon gas water heater replacement in same location",
            "city": "Phoenix",
            "state": "AZ",
            "forbidden": ("title 24", "wsec", "california", "oregon", "washington", "seismic strapping"),
            "required_terms": ("plumbing",),
            "result": {
                "permit_decision": "REQUIRED",
                "permit_verdict": "YES",
                "permit_kind": "Plumbing",
                "permit_name": "Residential Plumbing Permit — Water Heater Replacement",
                "applying_office": "City of Phoenix Planning & Development Department",
                "customer_next_step": "Confirm Phoenix plumbing permit intake for same-location gas water heater replacement before installation.",
                "approval_timeline": {"simple": "same day to 5 business days"},
                "fee_range": "Confirm current fixture and inspection fees with Phoenix before quoting.",
                "verified_date": "2026-05-27",
                "sources": [{"url": "https://www.phoenix.gov/pdd/development/permits", "title": "Phoenix Planning & Development Permits"}],
                "checklist": ["Document same-location replacement and gas connection scope.", "California seismic strapping and WSEC forms are not local Phoenix filing items."],
                "pro_tips": ["Schedule inspection through Phoenix after permit issuance."],
            },
        },
        {
            "name": "Miami FL residential exterior/trade",
            "job": "Miami FL simple residential exterior door replacement with porch light relocation",
            "city": "Miami",
            "state": "FL",
            "forbidden": ("title 24", "wsec"),
            "required_terms": ("building", "electrical"),
            "result": {
                "permit_decision": "REQUIRED",
                "permit_verdict": "YES",
                "permit_kind": "Residential Building / Electrical",
                "permit_name": "Residential Building Permit with Electrical Trade",
                "applying_office": "Miami-Dade County Building Department",
                "customer_next_step": "Confirm Miami-Dade residential building permit intake and add electrical trade details for porch light relocation.",
                "approval_timeline": {"simple": "several business days depending on completeness"},
                "fee_range": "Fees vary by valuation, trade add-ons, and current county fee schedule.",
                "verified_date": "2026-05-27",
                "sources": [{"url": "https://www.miamidade.gov/miami/florida/permits/building.page", "title": "Miami-Dade Building Permits"}],
                "checklist": ["Prepare exterior opening details.", "Add electrical trade note for porch light relocation."],
            },
        },
        {
            "name": "Atlanta GA commercial restaurant TI",
            "job": "Atlanta GA commercial restaurant tenant improvement with partitions, Type I hood, grease interceptor, electrical and plumbing",
            "city": "Atlanta",
            "state": "GA",
            "forbidden": ("title 24", "wsec", "california"),
            "required_terms": ("commercial", "health"),
            "result": {
                "permit_decision": "REQUIRED",
                "permit_verdict": "YES",
                "permit_kind": "Commercial Building / Tenant Improvement",
                "permit_name": "Commercial Building Permit — Restaurant Tenant Improvement",
                "applying_office": "City of Atlanta Office of Buildings",
                "customer_next_step": "Start Atlanta commercial building permit intake and coordinate food-service, hood, fire, plumbing, and health review triggers.",
                "approval_timeline": {"simple": "2 to 6 weeks depending on review cycles"},
                "fee_range": "Fees vary by valuation and trade reviews; confirm current Atlanta schedule.",
                "verified_date": "2026-05-27",
                "sources": [{"url": "https://www.atlantaga.gov/georgia/government/departments/city-planning/building-permits", "title": "Atlanta Building Permits"}],
                "checklist": ["Submit restaurant TI plans.", "Include hood, grease interceptor, plumbing, fire/life-safety, and health review scope."],
            },
        },
        {
            "name": "Cross-applicable ADA/federal commercial TI",
            "job": "Dallas TX commercial tenant improvement public accommodation with accessible route and restroom upgrades plus lighting relocation",
            "city": "Dallas",
            "state": "TX",
            "forbidden": ("title 24", "wsec", "california"),
            "required_terms": ("ada", "federal", "nec"),
            "result": {
                "permit_decision": "REQUIRED",
                "permit_verdict": "YES",
                "permit_kind": "Commercial Building / Tenant Improvement",
                "permit_name": "Commercial Building / Tenant Improvement Permit",
                "applying_office": "Dallas Development Services Department",
                "customer_next_step": "File Dallas commercial TI plans and keep accessibility details tied to public-accommodation scope.",
                "approval_timeline": {"simple": "several business days to a few weeks"},
                "fee_range": "Fees vary by valuation and trade scope; confirm with Dallas before quoting.",
                "verified_date": "2026-05-27",
                "sources": [{"url": "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection", "title": "Dallas Building Inspection"}],
                "checklist": ["Show ADA accessible route and restroom details where public-accommodation federal accessibility rules apply.", "Provide NEC panel schedules for lighting relocation."],
                "pro_tips": ["Federal ADA/accessibility review can remain relevant even when state energy-code references are removed."],
            },
        },
    ]

    outputs = {}
    for fixture in fixtures:
        outputs[fixture["name"]] = _assert_customer_quality(
            server,
            fixture["result"],
            fixture["job"],
            fixture["city"],
            fixture["state"],
            fixture["forbidden"],
            fixture["required_terms"],
        )

    html = FRONTEND_INDEX.read_text(encoding="utf-8")
    assert "function recoverCompletedLookupResult(" in html
    assert "function renderCompletedLookupResult(" in html
    assert "window._lastCompletedLookup" in html
    assert "Open recovered result" in html
    assert outputs


def test_wrong_state_filter_preserves_source_metadata_state_names_and_prunes_blank_bullets(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    result = {
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_kind": "Commercial Building / Tenant Improvement",
        "permit_name": "Commercial Building / Tenant Improvement Permit",
        "applying_office": "Dallas Development Services Department",
        "customer_next_step": "Start with Dallas commercial TI intake before construction.",
        "approval_timeline": {"simple": "several business days to a few weeks"},
        "fee_range": "Fees vary by valuation and trade scope.",
        "verified_date": "2026-05-27",
        "sources": [
            {
                "url": "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection",
                "title": "Washington County example referenced by Dallas Building Inspection publisher notes",
                "publisher": "Washington County archive label",
                "snippet": "Source title contains a state-name word but is not a Washington State Energy Code claim.",
            }
        ],
        "checklist": [
            "Submit Dallas commercial TI plans and electrical load summary.",
            "Attach Washington State Energy Code worksheet.",
            "Provide California Title 24 energy forms.",
        ],
        "pro_tips": ["", "Confirm NEC panel schedules for relocated lighting circuits."],
        "related_permits": [
            {"permit_type": ""},
            {"permit_type": "Accessibility review", "reason": "ADA/federal accessibility can remain cross-applicable."},
        ],
    }

    out = server.finalize_permit_lookup_result(
        result,
        "Dallas TX commercial office tenant improvement with lighting relocation",
        "Dallas",
        "TX",
        evidence_allowed=False,
    )
    public = server.build_customer_permit_view_model(out, "commercial office tenant improvement", "Dallas", "TX")
    text = _surface_text(public)

    assert "washington state energy code" not in text
    assert "title 24" not in text
    assert "california" not in text
    assert "submit dallas commercial ti plans" in text
    assert "washington county example" in text
    assert "washington county archive label" in text
    assert '""' not in json.dumps(public.get("checklist") or [])
    assert all(item for item in public.get("pro_tips") or [])
    assert all(item.get("permit_type") for item in public.get("related_permits") or [])


def test_customer_visible_source_urls_require_https_and_are_deduped_with_structured_provenance(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    result = {
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_kind": "Plumbing",
        "permit_name": "Residential Plumbing Permit",
        "applying_office": "City of Phoenix Planning & Development Department",
        "customer_next_step": "Confirm Phoenix plumbing permit intake before installation.",
        "approval_timeline": {"simple": "same day to 5 business days"},
        "fee_range": "Confirm current fixture and inspection fees.",
        "verified_date": "2026-05-27",
        "sources": [
            {"url": "http://www.phoenix.gov/pdd/development/permits", "title": "Insecure Phoenix Permits"},
            {"url": "https://www.phoenix.gov/pdd/development/permits", "title": "Phoenix Permits", "publisher": "City of Phoenix", "source_date": "2026-05-27"},
            {"url": "https://www.phoenix.gov/pdd/development/permits", "title": "Duplicate Phoenix Permits"},
        ],
    }

    out = server.finalize_permit_lookup_result(result, "Phoenix gas water heater replacement", "Phoenix", "AZ", evidence_allowed=False)
    public = server.build_customer_permit_view_model(out, "water heater replacement", "Phoenix", "AZ")

    sources = public.get("sources") or []
    assert [src["url"] for src in sources] == ["https://www.phoenix.gov/pdd/development/permits"]
    source = sources[0]
    assert source["title"] == "Phoenix Permits"
    assert source["publisher"] == "City of Phoenix"
    assert source["source_type"] in {"official_local", "official_source"}
    assert source["jurisdiction"] == "Phoenix, AZ"
    assert source["date"] == "2026-05-27"
    assert public.get("source_urls") == ["https://www.phoenix.gov/pdd/development/permits"]


def test_detect_primary_scope_contract_is_string_with_documented_legacy_dict_normalizer(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    assert isinstance(server.detect_primary_scope("Phoenix residential gas water heater replacement"), str)
    assert server._canonical_primary_scope_label("residential_trade") == "residential_trade"
    assert server._canonical_primary_scope_label({"primary_scope": "residential_trade", "confidence": 0.92}) == "residential_trade"
    assert server._canonical_primary_scope_label({"scope": "commercial_restaurant"}) == "commercial_restaurant"

    monkeypatch.setattr(server, "detect_primary_scope", lambda _job: {"primary_scope": "residential_trade"})
    result = {"permits_required": [{"permit_type": "Residential Plumbing Permit"}]}
    server._repair_residential_trade_model_leak(result, "Phoenix residential gas water heater replacement", "Phoenix")
    assert "commercial" not in result["permits_required"][0]["permit_type"].lower()


def test_second_summary_sanitization_uses_jurisdiction_context_and_frontend_renders_mobile_first_summary(tmp_path, monkeypatch):
    server = _load_server(tmp_path, monkeypatch)
    result = {
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_kind": "Commercial Building / Tenant Improvement",
        "permit_name": "Commercial Building / Tenant Improvement Permit Permit",
        "applying_office": "Dallas Development Services Department",
        "customer_next_step": "Submit Dallas commercial TI plans before construction.",
        "approval_timeline": {"simple": "several business days to a few weeks"},
        "fee_range": "Fees vary by valuation and trade scope.",
        "verified_date": "2026-05-27",
        "sources": [{"url": "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection", "title": "California Title 24 forms"}],
        "checklist": ["Submit Dallas commercial TI plans."],
    }

    out = server.finalize_permit_lookup_result(result, "Dallas commercial office tenant improvement", "Dallas", "TX", evidence_allowed=False)
    public = server.build_customer_permit_view_model(out, "commercial office tenant improvement", "Dallas", "TX")
    text = _surface_text(public)
    assert "permit permit" not in text
    assert "title 24" not in text
    assert "california" not in text
    assert public["customer_first_screen_summary"]["decision"] == "REQUIRED"

    html = FRONTEND_INDEX.read_text(encoding="utf-8")
    assert "customer_first_screen_summary" in html
    assert "mobile-first-answer-card" in html
    assert "customer_result_summary" in html
