import json
from importlib import util
from pathlib import Path

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
assert _HELPER_SPEC is not None
assert _HELPER_SPEC.loader is not None
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_import_server = _debug_helper._import_server


def _commercial_result_with_string_inspections():
    return {
        "permit_name": "Commercial Tenant Improvement Building Permit",
        "permit_verdict": "YES",
        "permit_required": True,
        "permits_required": [
            {"permit_type": "Commercial Tenant Improvement Building Permit"}
        ],
        "fee_range": "$1,200-$4,000 planning estimate",
        "approval_timeline": {"simple": "2-4 weeks", "complex": "4-8 weeks"},
        "what_to_bring": ["Commercial TI plans"],
        "inspections": [
            "Framing inspection before cover",
            "MEP rough inspections before cover",
            {"stage": "Final building inspection", "timing": "Before occupancy"},
        ],
        "pro_tips": ["Coordinate plan review before starting work."],
    }


def test_build_checklist_fallback_accepts_string_inspections(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    checklist = server.build_checklist_fallback(
        _commercial_result_with_string_inspections(),
        "Dallas commercial medical clinic TI",
        "Dallas",
        "TX",
    )

    labels = [item["label"] for item in checklist["items"]]
    assert any("Schedule inspection: Framing inspection before cover" in label for label in labels)
    assert any("Schedule inspection: MEP rough inspections before cover" in label for label in labels)
    assert any("Schedule inspection: Final building inspection — Before occupancy" in label for label in labels)


def test_render_share_page_accepts_saved_result_with_string_inspections(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "load_report_template", lambda: "__REPORT_DATA__")
    share = {
        "data": _commercial_result_with_string_inspections(),
        "job_type": "Dallas commercial office TI",
        "city": "Dallas",
        "state": "TX",
    }

    html = server.render_share_page(share)

    payload = json.loads(html)
    labels = [item["label"] for item in payload["checklist"]["items"]]
    assert payload["share"]["job_type"] == "Dallas commercial office TI"
    assert any("Schedule inspection: Framing inspection before cover" in label for label in labels)


def _assert_no_internal_customer_terms(value):
    serialized = json.dumps(value, sort_keys=True)
    forbidden = [
        "Engine flagged",
        "Needs review",
        "needs_review",
        "Verified · official sources",
        "Hidden triggers",
        "Planning estimate only",
        "verify before merging",
        "jurisdiction multiplier",
        "TI floor",
        "ada-path-of-travel adder",
    ]
    leaked = [term for term in forbidden if term.lower() in serialized.lower()]
    assert leaked == []


def _result_with_internal_engine_wording():
    return {
        "needs_review": True,
        "permit_name": "Commercial Tenant Improvement Building Permit",
        "permit_verdict": "YES",
        "permit_required": True,
        "fee_range": "Fee Estimate: **$6,000-$9,500+** (structured TI floor × jurisdiction multiplier + ada-path-of-travel adder).",
        "approval_timeline": {"simple": "Planning estimate only: 1-3 business days"},
        "confidence_reason": "Verified · official sources. Needs review for: fee_range",
        "quality_warnings": ["Engine flagged this answer for review"],
        "warnings": ["Needs review for: fee_range"],
        "hidden_triggers": [
            {
                "title": "Hidden triggers: ADA path",
                "why_it_matters": "Engine flagged this as a high-risk trigger.",
                "citations": ["Local sign/facade standard [verify before merging]"],
            }
        ],
        "claim_citations": [
            {
                "claim": "Fee range uses jurisdiction multiplier",
                "value": "TI floor plus ada-path-of-travel adder",
                "quoted_snippet": "verify before merging",
            }
        ],
    }


def test_customer_output_sanitizer_removes_internal_engine_wording(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    cleaned = server.sanitize_customer_visible_result(_result_with_internal_engine_wording())

    _assert_no_internal_customer_terms(cleaned)
    assert cleaned["confidence_reason"] == "Source support is degraded; use the resolved permit decision and current local filing category before filing."
    assert cleaned["fee_range"] == "Fee varies by exact scope; confirm current fees with the building department before quoting."


def test_render_share_page_sanitizes_internal_engine_wording(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "load_report_template", lambda: "__REPORT_DATA__")
    share = {
        "data": _result_with_internal_engine_wording(),
        "job_type": "Dallas commercial office TI",
        "city": "Dallas",
        "state": "TX",
    }

    html = server.render_share_page(share)

    payload = json.loads(html)
    _assert_no_internal_customer_terms(payload["share"]["data"])
    _assert_no_internal_customer_terms(payload["checklist"])


def test_render_share_page_uses_request_scope_firebreak_for_saved_dirty_result(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "load_report_template", lambda: "__REPORT_DATA__")
    share = {
        "data": {
            "permit_name": "Commercial Tenant Improvement Building Permit",
            "permit_verdict": "YES",
            "pro_tips": [
                "Ask the AHJ if residential owner-builder paperwork applies.",
                "Coordinate health and fire review before permit intake.",
            ],
            "what_to_bring": ["Owner-builder affidavit for homeowner ADU solar PV"],
            "ahj_contact_source": "AHJ report source",
        },
        "job_type": "Commercial restaurant tenant improvement with Type I hood and grease interceptor",
        "city": "Austin",
        "state": "TX",
    }

    html = server.render_share_page(share)

    payload = json.loads(html)
    serialized = json.dumps(payload, sort_keys=True).lower()
    assert "ahj_contact_source" not in serialized
    assert "ahj" not in serialized
    assert "homeowner" not in serialized
    assert "owner-builder" not in serialized
    assert "adu" not in serialized
    assert "solar" not in serialized
    assert "residential" not in serialized
    assert "coordinate health and fire review" in serialized


def test_customer_sanitizer_removes_raw_disambiguation_labels_fragments_and_ahj(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    cleaned = server.sanitize_customer_visible_result({
        "permit_name": "Residential Building Permit — Addition (layout/plumbing/electrical/wall changes)",
        "job_summary": "Kitchen remodel (full replacement) requires **building review** with AHJ verification.",
        "what_to_bring": ["connection points", "Scaled plans", "Verify whether plumbing applies or Verify electrical because panels changed."],
        "pro_tips": ["fire marshal before permit intake."],
        "permit_verdict": "YES",
    })

    serialized = json.dumps(cleaned, sort_keys=True).lower()
    assert "(full replacement)" not in serialized
    assert "layout/plumbing/electrical/wall changes" not in serialized
    assert "**" not in serialized
    assert "ahj" not in serialized
    assert "connection points" not in serialized
    assert "or verify" not in serialized
    assert "coordinate fire-marshal review before permit intake" in serialized


def test_customer_sanitizer_rebalances_commercial_ti_timeline_and_fee_copy(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    cleaned = server.sanitize_customer_visible_result({
        "_primary_scope": "commercial_office_ti",
        "permit_name": "Commercial Tenant Improvement Building Permit",
        "permit_verdict": "YES",
        "fee_range": "Call to confirm",
        "approval_timeline": {
            "simple": "Same day OTC for simple trade permits",
            "complex": "5-10 business days if plan review required",
        },
    })

    assert "valuation" in cleaned["fee_range"].lower()
    assert "trade scope" in cleaned["fee_range"].lower()
    assert "call to confirm" not in cleaned["fee_range"].lower()
    simple = cleaned["approval_timeline"]["simple"].lower()
    assert "plan review" in simple
    assert "same day" not in simple
    assert "otc" not in simple
