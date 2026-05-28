import copy
import json
from pathlib import Path

from test_universal_deploy_readiness_fixes import _import_server


ROOT = Path(__file__).resolve().parents[1]
DALLAS_PORTAL = "https://developdallas.dallascityhall.com/PermitDallas/"
SOUTHLAKE_URL = "https://www.cityofsouthlake.com/123/Building-Inspections"
AUSTIN_URL = "https://www.austintexas.gov/department/development-services"
TEXAS_STATE_URL = "https://www.tdlr.texas.gov/TABS/"


def _base_required_result():
    return {
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_kind": "Commercial Building / Tenant Improvement",
        "permit_name": "Building Permit — Commercial Office Tenant Improvement",
        "permits_required": [
            {"permit_type": "Building Permit — Commercial Office Tenant Improvement", "required": True}
        ],
        "permits_required_logic": [
            {
                "permit_type": "Building Permit — Commercial Office Tenant Improvement",
                "included_because": "Commercial interior alterations normally require local AHJ review.",
                "scope_trigger": "Interior partitions, lighting, electrical/data, and accessibility work",
            }
        ],
        "customer_headline": "Permit required for the commercial tenant improvement.",
        "customer_next_step": "Use the local building department portal after verifying trade submittals.",
        "job_summary": "Commercial office tenant improvement in Dallas, TX.",
        "inspection_booking": "Schedule inspections with the local building department.",
        "applying_office": "Dallas Development Services Department",
        "building_dept_name": "Dallas Development Services Department",
        "apply_url": "",
        "online_application_url": "",
        "apply_path": {},
        "sources": [],
        "source_urls": [],
        "claim_citations": [],
        "confidence": "high",
    }


def _blank_structured_urls(result):
    result["apply_url"] = ""
    result["online_application_url"] = ""
    result["sources"] = []
    result["source_urls"] = []
    result["claim_citations"] = []
    apply_path = result.get("apply_path")
    if not isinstance(apply_path, dict):
        apply_path = {}
    for key in ("portal_url", "url", "source_url"):
        apply_path.pop(key, None)
    result["apply_path"] = apply_path
    return result


def _final_public(server, result):
    out = server.finalize_permit_lookup_result(
        result,
        "commercial office tenant improvement",
        "Dallas",
        "TX",
        is_cached=False,
        evidence_allowed=False,
    )
    return server.build_customer_permit_view_model(out, "commercial office tenant improvement", "Dallas", "TX")


def _assert_required_with_official_local_source(public, url=DALLAS_PORTAL):
    assert public["permit_decision"] == "REQUIRED"
    assert public["permit_verdict"] == "YES"
    assert url in public.get("source_urls", [])
    assert any(
        src.get("url") == url and src.get("source_type") == "official_local"
        for src in public.get("sources", [])
    )


def test_production_shape_inspection_booking_only_local_portal_satisfies_source_floor(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda url, timeout=5: True)
    result = _base_required_result()
    result["inspection_booking"] = f"Book inspections through PermitDallas: {DALLAS_PORTAL}."

    public = _final_public(server, result)

    _assert_required_with_official_local_source(public)


def test_recorded_dallas_fixture_free_text_only_local_portal_satisfies_source_floor(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda url, timeout=5: True)
    fixture_path = ROOT / "eval" / "stress-test-2026-04-28-commercial" / "dallas-office.json"
    result = json.loads(fixture_path.read_text(encoding="utf-8"))
    result.pop("expert_notes", None)
    _blank_structured_urls(result)
    result["permit_decision"] = "REQUIRED"
    result["permit_verdict"] = "YES"
    result["permit_required"] = True
    result["inspection_booking"] = f"Schedule inspections through PermitDallas only here: {DALLAS_PORTAL}."

    public = _final_public(server, result)

    _assert_required_with_official_local_source(public)


def test_wrong_locality_free_text_url_is_not_promoted_and_is_hidden_after_required_resolution(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda url, timeout=5: True)
    result = _base_required_result()
    result["inspection_booking"] = f"Wrong city portal: {AUSTIN_URL}."
    result["customer_next_step"] = f"Apply at {SOUTHLAKE_URL} before starting work."

    public = _final_public(server, result)
    blob = json.dumps(public, sort_keys=True)

    assert public["permit_decision"] == "REQUIRED"
    assert public["permit_required"] is True
    assert public.get("source_urls", []) == []
    assert public.get("sources", []) == []
    assert public.get("source_support", {}).get("decision_mutation_allowed") is False
    assert AUSTIN_URL not in blob
    assert SOUTHLAKE_URL not in blob
    assert "austintexas.gov" not in blob
    assert "cityofsouthlake.com" not in blob


def test_same_state_official_free_text_url_degrades_source_support_without_killing_decision(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda url, timeout=5: True)
    authority = server.classify_source_authority(TEXAS_STATE_URL, "Dallas", "TX", result={})
    assert authority["display_allowed"] is True
    assert authority["local_decision_evidence"] is False
    result = _base_required_result()
    result["customer_next_step"] = f"Review the same-state agency reference: {TEXAS_STATE_URL}."

    public = _final_public(server, result)

    assert public["permit_decision"] == "REQUIRED"
    assert public["permit_required"] is True
    assert TEXAS_STATE_URL not in public.get("source_urls", [])
    assert public.get("source_support", {}).get("decision_mutation_allowed") is False
    assert not any(src.get("url") == TEXAS_STATE_URL for src in public.get("sources", []))


def test_required_resolution_strips_bad_urls_but_valid_required_result_keeps_good_free_text_url(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda url, timeout=5: True)
    good = _base_required_result()
    good["inspection_booking"] = f"Book through {DALLAS_PORTAL}."
    good_public = _final_public(server, copy.deepcopy(good))
    assert good_public["permit_decision"] == "REQUIRED"
    assert DALLAS_PORTAL in json.dumps(good_public, sort_keys=True)

    bad = _base_required_result()
    bad["inspection_booking"] = f"Do not leak unsupported URL {SOUTHLAKE_URL}. Keep this non-URL text."
    bad_public = _final_public(server, bad)
    bad_blob = json.dumps(bad_public, sort_keys=True)
    assert bad_public["permit_decision"] == "REQUIRED"
    assert bad_public["permit_required"] is True
    assert SOUTHLAKE_URL not in bad_blob
    assert bad_public.get("source_support", {}).get("decision_mutation_allowed") is False
    assert "Keep this non-URL text" in bad_blob


def test_internal_rejected_debug_urls_are_not_promoted_by_free_text_walker(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda url, timeout=5: True)
    result = _base_required_result()
    result.update(
        {
            "expert_notes": f"Internal-only local portal {DALLAS_PORTAL}",
            "debug_trace": {"candidate": DALLAS_PORTAL},
            "retrieval_diagnostics": [{"candidate": DALLAS_PORTAL}],
            "raw_retrieval": f"raw html {DALLAS_PORTAL}",
            "search_debug": {"url": DALLAS_PORTAL},
            "scoring_debug": {"url": DALLAS_PORTAL},
            "rejected_sources": [{"url": DALLAS_PORTAL, "reason": "test rejected"}],
            "_sources_locality_dropped": [{"url": DALLAS_PORTAL}],
        }
    )

    public = _final_public(server, result)

    assert public["permit_decision"] == "REQUIRED"
    assert public["permit_required"] is True
    assert public.get("source_urls", []) == []
    assert public.get("source_support", {}).get("decision_mutation_allowed") is False
    assert not any(src.get("url") == DALLAS_PORTAL for src in public.get("sources", []))


def test_recursive_free_text_walker_finds_non_debug_nested_customer_text(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda url, timeout=5: True)
    result = _base_required_result()
    result["customer_guidance"] = {
        "steps": [
            {"text": f"Portal URL appears only in nested free text: {DALLAS_PORTAL}),"}
        ]
    }

    public = _final_public(server, result)

    _assert_required_with_official_local_source(public)


def test_source_url_discovery_is_idempotent_for_repeated_view_model_builds(tmp_path, monkeypatch):
    server = _import_server(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "validate_url", lambda url, timeout=5: True)
    result = _base_required_result()
    result["inspection_booking"] = f"Book through {DALLAS_PORTAL}. Duplicate mention: {DALLAS_PORTAL}"

    first = _final_public(server, result)
    second = server.build_customer_permit_view_model(first, "commercial office tenant improvement", "Dallas", "TX")

    assert first["permit_decision"] == second["permit_decision"] == "REQUIRED"
    assert first.get("source_urls", []) == [DALLAS_PORTAL]
    assert second.get("source_urls", []) == [DALLAS_PORTAL]
    assert [src.get("url") for src in second.get("sources", [])].count(DALLAS_PORTAL) == 1
