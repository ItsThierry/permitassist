import json
import re
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


ROOT = Path(__file__).resolve().parents[1]
SERVER_SOURCE = (ROOT / "api" / "server.py").read_text(encoding="utf-8")


def _import_server(tmp_path, monkeypatch):
    # Reuse lightweight third-party stubs, but keep the real research_engine so
    # these regressions exercise the canonical source classifier/floor.
    _debug_helper._install_server_import_stubs()
    import os
    import sys

    sys.modules.pop("research_engine", None)
    sys.modules.pop("api.server", None)
    repo_root = Path(__file__).resolve().parents[1]
    api_root = repo_root / "api"
    for path in (str(repo_root), str(api_root)):
        if path not in sys.path:
            sys.path.insert(0, path)
    monkeypatch.setenv("FREE_LOOKUP_DB", str(tmp_path / "ip_lookups.db"))
    monkeypatch.setenv("PERMITASSIST_NO_BACKGROUND_WORKERS", "1")
    from api import server

    server.CACHE_DB = str(tmp_path / "cache.db")
    server.DATA_DIR = str(tmp_path)
    server.init_db()
    return server


def _assert_no_forbidden_customer_fields(value):
    forbidden = {
        "permit_decision_contract",
        "source_evidence_floor",
        "exact_apply_url_status",
        "exact_name_status",
        "quality_warnings",
        "needs_review",
        "permit_ready_score",
        "debug_trace",
        "provider_metadata",
        "retrieval_diagnostics",
        "_evidence_pack",
    }
    found = []

    def walk(item, path=""):
        if isinstance(item, dict):
            for key, child in item.items():
                key_text = str(key)
                child_path = f"{path}.{key_text}" if path else key_text
                if key_text in forbidden or key_text.startswith("_"):
                    found.append(child_path)
                walk(child, child_path)
        elif isinstance(item, list):
            for idx, child in enumerate(item):
                walk(child, f"{path}[{idx}]")

    walk(value)
    assert found == []
    serialized = json.dumps(value, sort_keys=True).lower()
    assert "source_evidence_floor" not in serialized
    assert "permit_decision_contract" not in serialized
    assert "needs review" not in serialized


def _dirty_required_result(*, sources=None):
    return {
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_kind": "Commercial Building / Tenant Improvement",
        "permit_name": "Building Permit — Tenant Improvement / Medical Clinic Interior Alteration",
        "permits_required": [{"permit_type": "Building Permit — Tenant Improvement / Medical Clinic Interior Alteration"}],
        "customer_headline": "Permit required: Commercial Building / Tenant Improvement.",
        "customer_next_step": "File under Commercial Building / Tenant Improvement with the local building department.",
        "fee_range": "$1,000-$3,000",
        "confidence": "high",
        "needs_review": True,
        "quality_warnings": ["Needs review for: fee_range"],
        "permit_ready_score": {"score": 91},
        "debug_trace": {"model": "test"},
        "provider_metadata": {"provider": "test"},
        "retrieval_diagnostics": {"source_url_count": 0},
        "permit_decision_contract": {
            "permit_decision": "REQUIRED",
            "permit_kind": "Commercial Building / Tenant Improvement",
            "source_evidence_floor": {"status": "satisfied"},
            "exact_name_status": "verified",
            "exact_apply_url_status": "unverified",
        },
        "source_evidence_floor": {"status": "satisfied"},
        "exact_name_status": "verified",
        "exact_apply_url_status": "unverified",
        "sources": sources if sources is not None else [
            {"url": "https://www.miami.gov/Services/Permits", "title": "Miami Building"},
        ],
    }


def test_public_view_model_is_allowlisted_and_removes_internal_fields(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    public = server.build_customer_permit_view_model(
        _dirty_required_result(),
        "medical clinic tenant improvement",
        "Miami",
        "FL",
    )

    _assert_no_forbidden_customer_fields(public)
    assert public["permit_decision"] == "REQUIRED"
    assert public["permit_kind"] == "Commercial Building / Tenant Improvement"
    assert public["sources"]
    assert all("miami.gov" in src["url"] for src in public["sources"])


def test_report_share_artifacts_embed_only_public_customer_view_model(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(
        server,
        "load_report_template",
        lambda: "<html><body><h1>Permit Report</h1><script>window.REPORT_DATA = __REPORT_DATA__;</script></body></html>",
    )
    dirty = _dirty_required_result()

    slug = server.create_share("medical clinic tenant improvement", "Miami", "FL", dirty)
    share_json = server.get_share(slug)
    html = server.render_share_page(share_json)
    match = re.search(r"window\.REPORT_DATA\s*=\s*(\{.*\});", html)
    assert match, html
    embedded = json.loads(match.group(1))

    _assert_no_forbidden_customer_fields(share_json)
    _assert_no_forbidden_customer_fields(embedded)
    assert "Permit Report" in html
    assert "permit_decision_contract" not in html
    assert "quality_warnings" not in html


def test_final_decision_fails_closed_when_required_has_no_surviving_local_sources(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = _dirty_required_result(sources=[
        "https://des.wa.gov/services/facilities-leasing/public-works-design-construction/public-works-contracting",
        "https://www.icc-safe.org/products-and-services/i-codes/2021-i-codes/ibc/",
    ])

    out = server.finalize_permit_lookup_result(
        result,
        "office tenant improvement",
        "Austin",
        "TX",
        is_cached=False,
        evidence_allowed=False,
    )

    public = server.build_customer_permit_view_model(out, "office tenant improvement", "Austin", "TX")
    assert public["permit_decision"] == "FAIL_CLOSED_UNSUPPORTED_OR_NO_EVIDENCE"
    assert public.get("source_urls", []) == []
    assert public.get("permits_required", []) == []
    assert "File under" not in public.get("customer_next_step", "")


def test_local_ahj_apply_url_only_satisfies_final_source_floor(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda url, timeout=5: True)
    apply_url = "https://developdallas.dallascityhall.com/PermitDallas/"
    result = _dirty_required_result(sources=[])
    result.update({
        "permit_name": "Building Permit — Office Tenant Improvement",
        "apply_url": apply_url,
        "online_application_url": apply_url,
        "applying_office": "Dallas Development Services Department",
        "building_dept_name": "Dallas Development Services Department",
    })

    out = server.finalize_permit_lookup_result(
        result,
        "commercial office tenant improvement",
        "Dallas",
        "TX",
        is_cached=False,
        evidence_allowed=False,
    )
    public = server.build_customer_permit_view_model(out, "commercial office tenant improvement", "Dallas", "TX")

    assert public["permit_decision"] == "REQUIRED"
    assert public["permit_verdict"] == "YES"
    assert apply_url in public.get("source_urls", [])
    assert any(src.get("source_type") == "official_local" for src in public.get("sources", []))


def test_recorded_dallas_office_fixture_public_view_model_stays_required_with_local_apply_url(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda url, timeout=5: True)
    apply_url = "https://developdallas.dallascityhall.com/PermitDallas/"
    fixture_path = ROOT / "eval" / "stress-test-2026-04-28-commercial" / "dallas-office.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    # This legacy recorded output includes broad Texas enrichment notes that the
    # scope firebreak correctly strips under pytest. They are unrelated to the
    # Dallas final-decision/apply-URL source-floor regression pinned here.
    fixture.pop("expert_notes", None)

    out = server.finalize_permit_lookup_result(
        fixture,
        "4,500 sf commercial office tenant improvement on the 3rd floor in Dallas, TX with partitions, lighting, electrical/data, sprinkler relocation, and accessible path upgrades",
        "Dallas",
        "TX",
        is_cached=False,
        evidence_allowed=False,
    )
    public = server.build_customer_permit_view_model(
        out,
        "commercial office tenant improvement",
        "Dallas",
        "TX",
    )

    assert public["permit_decision"] == "REQUIRED"
    assert public["permit_verdict"] == "YES"
    assert apply_url in public.get("source_urls", [])
    assert any(
        src.get("url") == apply_url and src.get("source_type") == "official_local"
        for src in public.get("sources", [])
    )


def test_production_shape_free_text_local_portal_satisfies_final_source_floor(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda url, timeout=5: True)
    apply_url = "https://developdallas.dallascityhall.com/PermitDallas/"
    result = _dirty_required_result(sources=[])
    result.update({
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "source_urls": [],
        "claim_citations": [],
        "apply_url": "",
        "online_application_url": "",
        "apply_path": {},
        "inspection_booking": f"Book inspections through the Dallas online portal: {apply_url}",
        "customer_next_step": "File through the local Dallas permit portal after verifying trade submittals.",
        "applying_office": "Dallas Development Services Department",
        "building_dept_name": "Dallas Development Services Department",
    })

    out = server.finalize_permit_lookup_result(
        result,
        "commercial office tenant improvement",
        "Dallas",
        "TX",
        is_cached=False,
        evidence_allowed=False,
    )
    public = server.build_customer_permit_view_model(out, "commercial office tenant improvement", "Dallas", "TX")

    assert public["permit_decision"] == "REQUIRED"
    assert public["permit_verdict"] == "YES"
    assert apply_url in public.get("source_urls", [])
    assert any(
        src.get("url") == apply_url and src.get("source_type") == "official_local"
        for src in public.get("sources", [])
    )


def test_fail_closed_public_view_model_strips_free_text_urls(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda url, timeout=5: True)
    wrong_url = "https://www.cityofsouthlake.com/123/Building-Inspections"
    result = _dirty_required_result(sources=[])
    result.update({
        "source_urls": [],
        "claim_citations": [],
        "apply_url": "",
        "online_application_url": "",
        "apply_path": {"steps": [f"Apply at {wrong_url}"]},
        "inspection_booking": f"Book inspection at {wrong_url}",
        "customer_next_step": f"File through {wrong_url} before starting work.",
        "job_summary": f"Dallas office TI. Portal: {wrong_url}",
        "permit_summary": {"notes": [f"Nested unsupported portal {wrong_url}"]},
    })

    out = server.finalize_permit_lookup_result(
        result,
        "commercial office tenant improvement",
        "Dallas",
        "TX",
        is_cached=False,
        evidence_allowed=False,
    )
    public = server.build_customer_permit_view_model(out, "commercial office tenant improvement", "Dallas", "TX")
    blob = json.dumps(public, sort_keys=True)

    assert public["permit_decision"] == "FAIL_CLOSED_UNSUPPORTED_OR_NO_EVIDENCE"
    assert public.get("source_urls", []) == []
    assert wrong_url not in blob
    assert "cityofsouthlake.com" not in blob


def test_wrong_locality_apply_url_only_still_fails_closed(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda url, timeout=5: True)
    wrong_url = "https://www.cityofsouthlake.com/123/Building-Inspections"
    result = _dirty_required_result(sources=[])
    result.update({
        "apply_url": wrong_url,
        "online_application_url": wrong_url,
        "applying_office": "Dallas Development Services Department",
    })

    out = server.finalize_permit_lookup_result(
        result,
        "commercial office tenant improvement",
        "Dallas",
        "TX",
        is_cached=False,
        evidence_allowed=False,
    )
    public = server.build_customer_permit_view_model(out, "commercial office tenant improvement", "Dallas", "TX")

    assert public["permit_decision"] == "FAIL_CLOSED_UNSUPPORTED_OR_NO_EVIDENCE"
    assert public.get("source_urls", []) == []
    assert public.get("apply_url") in ("", None)


def test_known_smoke_wrong_locality_urls_are_excluded_by_canonical_classifier():
    from api.research_engine import classify_source_authority, classify_source_tier, is_url_allowed_for_locality

    cases = [
        ("https://www.northmiamifl.gov/", "Miami", "FL"),
        ("https://www.miamilakes-fl.gov/", "Miami", "FL"),
        ("https://des.wa.gov/services/facilities-leasing/public-works-design-construction", "Austin", "TX"),
        ("https://www.mountainview.gov/depts/comdev/building/permits.asp", "San Jose", "CA"),
    ]
    for url, city, state in cases:
        authority = classify_source_authority(url, city, state)
        assert authority["category"] in {"wrong_locality", "excluded"}
        assert classify_source_tier(url, city, state) == "wrong"
        assert not is_url_allowed_for_locality(url, city, state)

    local = classify_source_authority("https://www.miami.gov/Services/Permits", "Miami", "FL")
    assert local["category"] == "local_ahj"
    assert local["local_decision_evidence"] is True

    universal = classify_source_authority("https://www.icc-safe.org/products-and-services/i-codes/2021-i-codes/ibc/", "Austin", "TX")
    assert universal["category"] == "universal_code"
    assert universal["display_allowed"] is True
    assert universal["local_decision_evidence"] is False


def test_fake_unsupported_required_answer_still_fail_closes(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    out = server.finalize_permit_lookup_result(
        _dirty_required_result(sources=[]),
        "tenant improvement",
        "Madeupville",
        "ZZ",
        is_cached=False,
        evidence_allowed=False,
    )
    public = server.build_customer_permit_view_model(out, "tenant improvement", "Madeupville", "ZZ")

    assert public["permit_decision"] == "FAIL_CLOSED_UNSUPPORTED_OR_NO_EVIDENCE"
    assert public.get("sources", []) == []
    assert "permit_decision_contract" not in out
    assert "source_evidence_floor" not in out
    assert "verify" in public.get("customer_next_step", "").lower()
    assert "file under" not in json.dumps(public).lower()


def test_public_view_model_rechecks_source_floor_after_scope_sanitizer(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    dirty = _dirty_required_result(sources=[{"url": "https://www.miami.gov/Services/Permits", "title": "Miami Building"}])

    def strip_sources_after_first_gate(result, scope_contract, fail_on_removal_in_tests=False):
        out = dict(result)
        out["sources"] = []
        out["source_urls"] = []
        out["claim_citations"] = []
        return out

    monkeypatch.setattr(server, "sanitize_result_for_scope_contract", strip_sources_after_first_gate)
    public = server.build_customer_permit_view_model(dirty, "medical clinic tenant improvement", "Miami", "FL")

    assert public["permit_decision"] == "FAIL_CLOSED_UNSUPPORTED_OR_NO_EVIDENCE"
    assert public.get("sources", []) == []
    assert public.get("source_urls", []) == []
    assert "file under" not in json.dumps(public).lower()


def test_public_citations_require_display_allowed_source_url(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    dirty = _dirty_required_result()
    dirty["claim_citations"] = [
        {"field": "permit_name", "claim": "No URL citation", "quoted_snippet": "should not display"},
        {"field": "permit_name", "claim": "Wrong source", "source_url": "https://www.mountainview.gov/permits", "quoted_snippet": "wrong city"},
        {"field": "permit_name", "claim": "Miami source", "source_url": "https://www.miami.gov/Services/Permits", "quoted_snippet": "local"},
    ]

    public = server.build_customer_permit_view_model(dirty, "medical clinic tenant improvement", "Miami", "FL")

    assert public.get("claim_citations") == [
        {"field": "permit_name", "claim": "Miami source", "source_url": "https://www.miami.gov/Services/Permits", "quoted_snippet": "local"}
    ]


def test_scope_removed_source_and_citation_text_is_not_reattached_by_view_model(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    dirty = _dirty_required_result(
        sources=[{"url": "https://www.miami.gov/Services/Permits", "title": "Homeowner ADU residential solar portal"}]
    )
    dirty["claim_citations"] = [
        {
            "field": "permit_name",
            "claim": "Use homeowner ADU residential solar source",
            "source_url": "https://www.miami.gov/Services/Permits",
            "source_title": "Homeowner ADU residential solar portal",
            "quoted_snippet": "Homeowner ADU residential solar permit",
        }
    ]

    public = server.build_customer_permit_view_model(dirty, "commercial office tenant improvement", "Miami", "FL")
    blob = json.dumps(public, sort_keys=True).lower()

    assert public["permit_decision"] == "FAIL_CLOSED_UNSUPPORTED_OR_NO_EVIDENCE"
    assert public.get("sources", []) == []
    assert "homeowner" not in blob
    assert "adu" not in blob
    assert "residential solar" not in blob
    assert "file under" not in blob


def test_batch_permit_customer_path_uses_public_view_model():
    route = SERVER_SOURCE.split('elif path == "/api/batch-permit":', 1)[1].split('elif path == "/api/beta-event":', 1)[0]

    assert "build_customer_permit_view_model(result, job_type, city, state)" in route
    assert "response.setdefault" not in route
    assert "permit_ready_score" not in route
    assert "rejection_patterns" not in route


def test_server_binds_real_source_classifier_not_silent_fail_closed_stub(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)

    authority = server.classify_source_authority("https://www.miami.gov/Services/Permits", "Miami", "FL")
    assert authority["category"] == "local_ahj"
    assert authority["display_allowed"] is True
    assert server.classify_source_tier("https://www.miami.gov/Services/Permits", "Miami", "FL") == "ahj"


def test_jurisdiction_authority_graph_accepts_consolidated_ahj_hosts():
    from api.research_engine import classify_source_authority

    cases = [
        ("https://www.nyc.gov/site/buildings/index.page", "Brooklyn", "NY"),
        ("https://a810-dobnow.nyc.gov/publish/Index.html#!/", "Queens", "NY"),
        ("https://dob.dc.gov/page/permit-resources", "Washington", "DC"),
        ("https://permitwizard.dcra.dc.gov/", "Washington", "DC"),
        ("https://www.miamidade.gov/permits/", "Miami", "FL"),
        ("https://www.miamidade.gov/Apps/RER/EPSPortal/", "Coral Gables", "FL"),
        ("https://code.mecknc.gov/permitting", "Charlotte", "NC"),
        ("https://webpermit.mecklenburgcountync.gov/Default.aspx?PosseMenuName=ViewPermits", "Charlotte", "NC"),
        ("https://www.naperville.il.us/services/permits--licenses/", "Naperville", "IL"),
    ]

    for url, city, state in cases:
        authority = classify_source_authority(url, city, state)
        assert authority["tier"] == "ahj", (url, city, state, authority)
        assert authority["display_allowed"] is True, (url, city, state, authority)
        assert authority["local_decision_evidence"] is True, (url, city, state, authority)


def test_named_ahj_blank_apply_url_gets_canonical_official_start_url(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    result = _dirty_required_result(sources=[])
    result.update({
        "source_urls": [],
        "claim_citations": [],
        "apply_url": "",
        "online_application_url": "",
        "applying_office": "NYC Department of Buildings",
        "building_dept_name": "NYC Department of Buildings",
        "customer_next_step": "Use DOB NOW through the NYC Department of Buildings before starting work.",
    })

    out = server.finalize_permit_lookup_result(
        result,
        "Brooklyn dental clinic tenant improvement with plumbing and accessibility work",
        "Brooklyn",
        "NY",
        is_cached=False,
        evidence_allowed=False,
    )
    public = server.build_customer_permit_view_model(out, "Brooklyn dental clinic tenant improvement", "Brooklyn", "NY")

    assert public["permit_decision"] == "REQUIRED"
    assert public.get("apply_url") == "https://www.nyc.gov/site/buildings/index.page"
    assert public.get("source_urls") == ["https://www.nyc.gov/site/buildings/index.page"]
    assert any(src.get("source_type") == "official_local" for src in public.get("sources", []))


def test_source_backed_result_never_keeps_not_source_backed_apply_path(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    source_url = "https://code.mecknc.gov/permitting"
    result = _dirty_required_result(sources=[{"url": source_url, "title": "Mecklenburg County Code Enforcement Permitting"}])
    result.update({
        "apply_path": {
            "support_level": "not source-backed",
            "verification_note": "stale fail-closed text",
            "steps": ["Verify directly with the Charlotte building department."],
        },
        "applying_office": "Mecklenburg County Code Enforcement",
    })

    public = server.build_customer_permit_view_model(result, "Charlotte daycare tenant improvement", "Charlotte", "NC")

    assert public["permit_decision"] == "REQUIRED"
    assert public["apply_path"]["support_level"] != "not source-backed"
    assert public["apply_path"]["portal_url"] == source_url
    assert source_url in public.get("source_urls", [])
