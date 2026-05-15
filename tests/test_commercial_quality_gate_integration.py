from importlib import util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = REPO_ROOT / "api" / "server.py"

_HELPER_SPEC = util.spec_from_file_location(
    "debug_headers_helper",
    Path(__file__).with_name("test_debug_headers_endpoint.py"),
)
_debug_helper = util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_debug_helper)
_import_server = _debug_helper._import_server

FALLBACK_TITLE = "Permit required -- exact permit type needs AHJ verification"


def test_finalize_pipeline_reenforces_rendered_contract_after_phase7h_statement():
    source = SERVER_PATH.read_text(encoding="utf-8")
    tail = source.split("ensure_customer_visible_permit_trust_statement(result, city, state)", 1)[1].split("return result", 1)[0]
    assert "_enforce_rendered_output_contract(result, job_type, city, state" in tail


def test_quality_gate_attaches_canonical_context_display_names_source_classes_and_lint(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    server = _import_server(tmp_path, monkeypatch)
    result = {
        "permit_verdict": "YES",
        "confidence": "high",
        "confidence_reason": "Exact permit category needs AHJ verification",
        "needs_review": True,
        "data_source": "city",
        "_primary_scope": "commercial_restaurant",
        "job_category": "commercial",
        "permit_name": FALLBACK_TITLE,
        "permit_type": FALLBACK_TITLE,
        "permits_required": [{"permit_type": FALLBACK_TITLE, "portal_selection": FALLBACK_TITLE, "required": True}],
        "inspections": ["Mechanical rough inspection for split-system HVAC", "Final mechanical inspection"],
        "sources": [
            "https://www.chicago.gov/city/en/depts/bldgs.html",
            "https://www.phila.gov/departments/department-of-licenses-and-inspections/",
        ],
    }

    gated = server.apply_permitiq_quality_gate(
        result,
        "Chicago restaurant TI with Type I hood, grease duct, Ansul suppression, grease interceptor, 38 apartments upstairs",
        "Chicago",
        "IL",
    )

    assert gated["_rendering_context"]["is_commercial"] is True
    assert gated["_rendering_context"]["vertical"] == "commercial_restaurant"
    assert gated["_rendering_context"]["template_family"] == "commercial"
    assert gated["_rendering_context"]["allow_homeowner_template"] is False
    assert gated["_permit_display_name"] == "Commercial Alteration / Tenant Improvement Permit"
    assert FALLBACK_TITLE not in gated["permits_required"][0]["permit_type"]
    assert FALLBACK_TITLE not in gated["permits_required"][0]["portal_selection"]
    classes = {s["host"]: s["source_class"] for s in gated["_source_classification"]}
    assert classes["chicago.gov"] == "local_ahj"
    assert classes["phila.gov"] == "wrong_local_ahj"
    assert all("phila.gov" not in src for src in gated["_official_sources"])
    assert gated["_rendered_lint"]["passed"] is True
    assert gated["_rendered_lint"]["violations"] == []


def test_quality_gate_adds_vertical_inspection_defaults_for_sparse_medical_and_office(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    server = _import_server(tmp_path, monkeypatch)
    clinic = server.apply_permitiq_quality_gate(
        {"permit_verdict": "YES", "_primary_scope": "commercial_medical_clinic_ti", "job_category": "commercial", "permits_required": [{"permit_type": "Commercial Alteration Permit"}], "sources": []},
        "office-to-clinic conversion with exam rooms, hand sinks, x-ray and medical gas",
        "Chicago",
        "IL",
    )
    office = server.apply_permitiq_quality_gate(
        {"permit_verdict": "YES", "_primary_scope": "commercial_office_ti", "job_category": "commercial", "permits_required": [{"permit_type": "Commercial Alteration Permit"}], "sources": []},
        "high-rise multi-tenant office TI with demising partitions, no structural work",
        "Chicago",
        "IL",
    )
    assert clinic["_rendering_context"]["inspection_profile"] == "medical_clinic_ti"
    assert any("Medical gas" in item or "imaging" in item for item in clinic["inspections"])
    assert office["_rendering_context"]["inspection_profile"] == "office_ti"
    assert any("Egress" in item or "egress" in item for item in office["inspections"])
    assert not any("Medical gas" in item for item in office["inspections"])


def test_quality_gate_proxy_verified_claim_requires_city_data_and_local_ahj(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    server = _import_server(tmp_path, monkeypatch)
    gated = server.apply_permitiq_quality_gate(
        {
            "permit_verdict": "YES",
            "confidence": "high",
            "confidence_reason": "High-confidence local research, but not city database verified.",
            "data_source": "web_search",
            "_primary_scope": "commercial_office_ti",
            "job_category": "commercial",
            "permit_name": "Commercial Alteration Permit",
            "permit_type": "Commercial Alteration Permit",
            "permits_required": [{"permit_type": "Commercial Alteration Permit", "required": True}],
            "inspections": [
                "Commercial alteration / tenant-improvement building inspection",
                "Egress and accessibility inspection",
                "Final building inspection",
            ],
            "sources": ["https://www.chicago.gov/city/en/depts/bldgs.html"],
        },
        "office tenant improvement with demising partitions",
        "Chicago",
        "Illinois",
    )
    proxy_text = server._render_lint_proxy_text(gated)
    assert "Verified · official sources" not in proxy_text
    assert gated["_rendered_lint"]["passed"] is True


def test_finalize_pipeline_does_not_reintroduce_fallback_text_after_quality_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    server = _import_server(tmp_path, monkeypatch)
    finalized = server.finalize_permit_lookup_result(
        {
            "permit_verdict": "YES",
            "confidence": "high",
            "confidence_reason": "Permit required, but exact permit type needs AHJ verification.",
            "data_source": "city",
            "_primary_scope": "commercial_restaurant",
            "job_category": "commercial",
            "inspections": [
                "Building rough inspection",
                "Building final inspection",
                "Commercial kitchen hood and grease duct inspection",
                "Fire suppression and fire alarm inspection",
                "Health inspection",
                "Egress and occupancy final inspection",
            ],
            "sources": ["https://www.chicago.gov/city/en/depts/bldgs.html"],
        },
        "Chicago restaurant tenant improvement with Type I hood and grease duct",
        "Chicago",
        "IL",
        evidence_allowed=False,
    )

    assert finalized["_rendering_context"]["template_family"] == "commercial"
    assert finalized["_permit_display_name"] == "Building Permit — Tenant Improvement / Restaurant Interior Alteration"
    names = [finalized.get("permit_name"), finalized.get("permit_type")]
    names.extend(p.get("permit_type") for p in finalized.get("permits_required", []) if isinstance(p, dict))
    assert all("needs AHJ verification" not in str(name) for name in names)
    assert all("exact permit type" not in str(name).lower() for name in names)
    assert finalized["_rendered_lint"]["passed"] is True
