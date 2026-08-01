import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
for path in (ROOT, API):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from api.v24_decision_cells import (  # noqa: E402
    V24ResolutionStatus,
    get_v24_mode,
    load_v24_index,
    reconcile_authoritative_result,
    regulated_payload_from_cell,
    resolve_v24_cell,
    validate_v24_cell,
)

PKG = ROOT / "knowledge" / "v24"
INDEX = PKG / "permitassist_decision_cell_index_v24.json"
MANIFEST = PKG / "permitassist_v24_manifest.json"


def _manifest():
    return json.loads(MANIFEST.read_text())


def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _walk_path_fields(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if isinstance(child, str) and ("path" in key.lower() or key.endswith("_file")):
                yield child_path, child
            yield from _walk_path_fields(child, child_path)
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            yield from _walk_path_fields(child, f"{path}[{idx}]")


def test_v24_package_manifest_counts_and_hashes():
    manifest = _manifest()
    assert manifest["counts"]["ready_total"] == 2162
    assert manifest["counts"]["deferred_total"] == 327
    assert manifest["counts"]["w4_tier1_complete"] == 1938
    assert manifest["counts"]["w3_publishable"] == 200
    assert manifest["counts"]["w2_reroof_pass"] == 25
    cells = json.loads((PKG / manifest["decision_cells_file"]).read_text())["cells"]
    assert len(cells) == 2162
    assert all(cell.get("source_artifact_sha256") for cell in cells[:25])


def test_v24_index_loads_and_runtime_portable_sample_validates(monkeypatch):
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    index = load_v24_index(index_path=INDEX, manifest_path=MANIFEST)
    assert index is not None
    assert len(index) == 2162
    sample = index["AK|anchorage|commercial_tenant_improvement"]
    assert validate_v24_cell(sample, strict_snapshots=False, require_live_url_check=False).ok


def test_v24_shipped_package_has_no_boban_paths_and_keeps_source_metadata(monkeypatch):
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    for path in PKG.glob("*.json"):
        assert "/home/boban" not in path.read_text(encoding="utf-8"), path

    index = load_v24_index(index_path=INDEX, manifest_path=MANIFEST)
    assert index is not None
    cells_doc = json.loads((PKG / _manifest()["decision_cells_file"]).read_text(encoding="utf-8"))
    for path_name, value in _walk_path_fields(cells_doc):
        assert not value.startswith(("/home/", "/Users/", "/tmp/", "/mnt/")), (path_name, value)
        assert not re.match(r"^[A-Za-z]:\\", value), (path_name, value)
    for key in (
        "AK|anchorage|commercial_tenant_improvement",
        "AL|albertville|residential_remodel",
        "AZ|buckeye|reroof",
    ):
        cell = index[key]
        provenances = [d for d in _walk_dicts(cell) if {"source_url", "source_quote", "snapshot_hash", "snapshot_path"}.issubset(d)]
        assert provenances, key
        assert validate_v24_cell(cell, strict_snapshots=False, require_live_url_check=False).ok
        for prov in provenances:
            assert prov["source_url"]
            assert prov["source_quote"]
            assert prov["snapshot_hash"]
            assert prov["retrieved_at"]
            assert prov["last_verified_at"]
            assert not str(prov["snapshot_path"]).startswith("/home/boban")


def test_v24_prod_sim_resolves_w4_w3_w2_without_local_snapshot_files(monkeypatch):
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    import api.v24_decision_cells as v24

    original_validate = v24.validate_v24_cell

    def runtime_validate_guard(cell, **kwargs):
        assert kwargs.get("strict_snapshots") is False
        return original_validate(cell, **kwargs)

    monkeypatch.setattr(v24, "validate_v24_cell", runtime_validate_guard)
    cases = [
        ("Anchorage", "AK", "commercial tenant improvement", "commercial"),
        ("Albertville", "AL", "residential remodel", "residential"),
        ("Buckeye", "AZ", "reroof", "residential"),
    ]
    for city, state, job_type, category in cases:
        resolution = resolve_v24_cell(city, state, job_type, category)
        assert resolution.status == V24ResolutionStatus.EXACT_CELL_PUBLISHABLE
        result = {"permit_required": False, "permit_decision": "NOT_REQUIRED", "permit_verdict": "NO", "permits_required": []}
        reconcile_authoritative_result(result, v24_resolution=resolution, v231_resolution=None)
        assert result["permit_required"] is True
        assert result["permit_verdict"] == "YES"

    fail_closed = resolve_v24_cell("Yuma", "AZ", "residential remodel", "residential")
    assert fail_closed.status == V24ResolutionStatus.EXACT_CELL_FAIL_CLOSED
    result = {"permit_required": True, "permit_decision": "REQUIRED", "permit_verdict": "YES"}
    reconcile_authoritative_result(result, v24_resolution=fail_closed, v231_resolution={"publish_status": "PUBLISHABLE"})
    assert result["permit_required"] is True
    assert result["permit_verdict"] == "YES"
    assert result["_v24_fail_closed_live_policy"] == "static_data_gap_live_research_allowed_when_source_backed"


def test_v24_resolver_is_flag_gated_and_exact_publishable_wins(monkeypatch):
    monkeypatch.delenv("PERMITASSIST_V24_MODE", raising=False)
    off = resolve_v24_cell("Anchorage", "AK", "commercial tenant improvement", "commercial")
    assert off.status == V24ResolutionStatus.INDEX_UNAVAILABLE
    assert get_v24_mode() == "off"

    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    resolution = resolve_v24_cell("Anchorage", "AK", "commercial tenant improvement", "commercial")
    assert resolution.status == V24ResolutionStatus.EXACT_CELL_PUBLISHABLE
    result = {"permit_required": False, "permit_decision": "NOT_REQUIRED", "permit_verdict": "NO", "permits_required": []}
    reconcile_authoritative_result(result, v24_resolution=resolution, v231_resolution=None)
    assert result["permit_required"] is True
    assert result["permit_decision"] == "REQUIRED"
    assert result["permit_verdict"] == "YES"
    assert result["_field_sources"]["permit_required"] == "permitassist_v24_decision_cell"
    assert result["_decision_cell_primary_lock"]["source"] == "permitassist_v24_decision_cell"


def test_v24_fail_closed_blocks_v231_fallback_without_neutering_live_answer(monkeypatch):
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    resolution = resolve_v24_cell("Yuma", "AZ", "residential remodel", "residential")
    assert resolution.status == V24ResolutionStatus.EXACT_CELL_FAIL_CLOSED
    result = {"permit_required": True, "permit_decision": "REQUIRED", "permit_verdict": "YES"}
    fake_v231 = {"publish_status": "PUBLISHABLE", "main_decision": "REQUIRED", "permit_name": "Building Permit"}
    reconcile_authoritative_result(result, v24_resolution=resolution, v231_resolution=fake_v231)
    assert result["permit_required"] is True
    assert result["permit_verdict"] == "YES"
    assert result["_field_sources"]["fail_closed"] == "permitassist_v24_static_data_gap"
    assert "_v231_decision_cell" not in result


def test_v24_fail_closed_no_live_answer_still_contacts_office(monkeypatch):
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    resolution = resolve_v24_cell("Yuma", "AZ", "residential remodel", "residential")
    result = {}
    reconcile_authoritative_result(result, v24_resolution=resolution, v231_resolution=None)
    assert result["permit_required"] is None
    assert result["permit_verdict"] == "NEEDS_INPUT"
    assert result["_field_sources"]["permit_required"] == "permitassist_v24_fail_closed_no_live_answer"


def test_v24_falls_back_to_v231_when_no_exact_v24(monkeypatch):
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    resolution = resolve_v24_cell("Definitely Missing City", "ZZ", "commercial tenant improvement", "commercial")
    assert resolution.status in {V24ResolutionStatus.AHJ_NOT_COVERED, V24ResolutionStatus.INDEX_UNAVAILABLE}
    v231_cell = {
        "publish_status": "PUBLISHABLE",
        "main_decision": "REQUIRED",
        "permit_name": "Building Permit",
        "ahj_name": "Fallback City",
        "authority_model": {"application_url": "https://fallback.example.gov", "application_authority": "Fallback Office"},
        "source_evidence": [{"url": "https://fallback.example.gov", "quote": "Apply for a building permit."}],
    }
    result = {"permit_required": False, "permit_decision": "NOT_REQUIRED", "permit_verdict": "NO"}
    reconcile_authoritative_result(result, v24_resolution=resolution, v231_resolution=v231_cell)
    assert result["permit_required"] is True
    assert result["_field_sources"]["permit_required"] == "permitassist_v231_decision_cell"


def test_v24_internal_markers_do_not_escape_customer_sanitizer(monkeypatch):
    pytest = __import__("pytest")
    pytest.importorskip("requests")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-import-only")
    from api.server import sanitize_customer_visible_result
    raw = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "confidence_reason": "permitassist_v24_decision_cell v2.4 _v24_resolution_status cell_id resolver",
        "notes": "Use permitassist_v24_fail_closed if the decision_cell is not publishable.",
        "_v24_resolution_status": "exact_cell_publishable",
        "_v24_cell_id": "us-ak-anchorage__commercial__commercial_tenant_improvement__building",
        "_decision_cell_primary_lock": {"source": "permitassist_v24_decision_cell"},
    }
    clean = sanitize_customer_visible_result(raw)
    rendered = json.dumps(clean).lower()
    assert "permitassist_v24" not in rendered
    assert "_v24" not in rendered
    assert "decision_cell" not in rendered
    assert "cell_id" not in rendered


def test_v24_deterministic_fallback_covers_exact_cells_without_ai(monkeypatch):
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    from api.research_engine import _deterministic_v24_result_from_resolution

    publishable = resolve_v24_cell("Anchorage", "AK", "commercial tenant improvement", "commercial")
    result = _deterministic_v24_result_from_resolution(publishable, "commercial tenant improvement", "Anchorage", "AK")
    assert result is not None
    assert result["permit_required"] is True
    assert result["permit_decision"] == "REQUIRED"
    assert result["permit_verdict"] == "YES"
    assert result["apply_url"]
    assert result["sources"]

    fail_closed = resolve_v24_cell("Yuma", "AZ", "residential remodel", "residential")
    result = _deterministic_v24_result_from_resolution(fail_closed, "residential remodel", "Yuma", "AZ")
    assert result is not None
    assert result["permit_required"] is None
    assert result["permit_verdict"] == "NEEDS_INPUT"
    assert result["needs_review"] is True


def test_v24_fail_closed_survives_customer_view_model(monkeypatch):
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-import-only")
    from api.research_engine import _deterministic_v24_result_from_resolution
    from api.server import build_customer_permit_view_model

    fail_closed = resolve_v24_cell("Yuma", "AZ", "residential remodel", "residential")
    raw = _deterministic_v24_result_from_resolution(fail_closed, "residential remodel", "Yuma", "AZ")
    assert raw is not None
    public = build_customer_permit_view_model(raw, "residential remodel", "Yuma", "AZ", job_category="residential")
    assert public["permit_required"] is None
    assert public["permit_verdict"] == "NEEDS_INPUT"
    assert public["permit_decision"] == "NEEDS_INPUT"
    rendered = json.dumps(public).lower()
    assert "_v24" not in rendered
    assert "decision_cell" not in rendered
    assert "cell_id" not in rendered


def test_v24_paid_v1_customer_view_model_does_not_leak_internals(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-import-only")
    from api.server import build_customer_permit_view_model, finalize_permit_lookup_result

    raw = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_name": "Building Permit",
        "apply_url": "https://official.example.gov/apply",
        "permits_required": [{"permit_type": "Building Permit", "required": True}],
        "sources": [{"url": "https://official.example.gov/apply", "quote": "Apply for a building permit."}],
        "_v24_resolution_status": "exact_cell_publishable",
        "_v24_cell_id": "us-az-buckeye__reroof",
        "_decision_cell_primary_lock": {"source": "permitassist_v24_decision_cell", "cell_id": "us-az-buckeye__reroof"},
        "debug": {"snapshot_hash": "abc", "source_snapshot_path": "/home/boban/projects/permitassist-data/snapshot.txt"},
    }
    finalized = finalize_permit_lookup_result(raw, "reroof", "Buckeye", "AZ", job_category="residential", evidence_allowed=False)
    public = build_customer_permit_view_model(finalized, "reroof", "Buckeye", "AZ", job_category="residential")
    rendered = json.dumps(public, sort_keys=True).lower()
    for marker in ("_v24", "permitassist_v24", "v2.4", "decision_cell", "cell_id", "resolver", "snapshot_hash", "source_snapshot_path", "/home/boban"):
        assert marker not in rendered


def test_cache_legacy_unknown_check_ignores_internal_scope_contract():
    from api.decision_resolver import contains_legacy_unknown_state

    cached = {
        "permit_decision": "REQUIRED",
        "permit_required": True,
        "permit_verdict": "YES",
        "_scope_contract": {"occupancy_class": "unknown"},
    }
    assert contains_legacy_unknown_state(cached) is False


def test_cache_legacy_unknown_check_preserves_valid_nonbinary_states():
    from api.decision_resolver import contains_legacy_unknown_state

    for decision in ("CONDITIONAL", "VERIFY"):
        cached = {
            "permit_decision": decision,
            "permit_required": None,
            "permit_verdict": "VERIFY",
        }
        assert contains_legacy_unknown_state(cached) is False

    assert contains_legacy_unknown_state({
        "permit_decision": "UNKNOWN",
        "permit_required": None,
        "permit_verdict": "VERIFY",
    }) is True


def test_cache_read_preserves_valid_nonbinary_rows(monkeypatch, tmp_path):
    from api import research_engine

    monkeypatch.setattr(research_engine, "CACHE_DB", str(tmp_path / "nonbinary-cache.db"))
    monkeypatch.setattr(research_engine, "_capture_validators", lambda *_args, **_kwargs: ("", ""))
    research_engine.init_cache()

    for decision in ("CONDITIONAL", "VERIFY"):
        key = f"valid-nonbinary-{decision.lower()}"
        payload = {
            "permit_decision": decision,
            "permit_required": None,
            "permit_verdict": "VERIFY",
        }
        research_engine.save_cache(
            key,
            "residential remodel",
            "residential",
            "Exampleville",
            "EX",
            "",
            payload,
        )
        cached = research_engine.get_cached(key)
        assert cached is not None
        assert cached["permit_decision"] == decision
        assert cached["permit_required"] is None

    research_engine.save_cache(
        "schema-only-empty",
        "residential remodel",
        "residential",
        "Exampleville",
        "EX",
        "",
        {},
    )
    assert research_engine.get_cached("schema-only-empty") is None


def test_v24_delmar_repaired_cell_is_publishable_and_source_backed(monkeypatch):
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    resolution = resolve_v24_cell("Delmar", "DE", "residential reroof", "residential")
    assert resolution.status == V24ResolutionStatus.EXACT_CELL_PUBLISHABLE
    assert resolution.key == "DE|delmar|residential_remodel"
    cell = resolution.cell or {}
    validation = validate_v24_cell(cell, strict_snapshots=False, require_live_url_check=False)
    assert validation.ok, validation.to_dict()
    payload = regulated_payload_from_cell(cell)
    assert payload["permit_required"] is True
    assert payload["permit_decision"] == "REQUIRED"
    rendered = json.dumps(payload).lower()
    assert "townofdelmar.us" in rendered
    assert "residential_building_permit_application" in rendered
    assert "iccsafe.org" not in rendered


def test_unquoted_live_source_cannot_preserve_required_customer_decision(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-import-only")
    from api.server import build_customer_permit_view_model

    raw = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_name": "Building Permit",
        "sources": [{"url": "https://www.townofdelmar.us/pdfs/RESIDENTIAL_BUILDING_PERMIT_APPLICATION_FINAL_as_of_1_6_26%2E.pdf", "quote": "Type of Construction: Roofing"}],
        "apply_url": "https://www.townofdelmar.us/pdfs/RESIDENTIAL_BUILDING_PERMIT_APPLICATION_FINAL_as_of_1_6_26%2E.pdf",
        "fail_closed": {"active": True, "reason": "static package data gap"},
        "confidence_reason": "live source-backed answer",
    }
    public = build_customer_permit_view_model(raw, "residential reroof", "Delmar", "DE", job_category="residential")
    assert public["permit_required"] is None
    assert public["permit_verdict"] == "VERIFY"
    assert public["permit_decision"] == "VERIFY"
    assert "townofdelmar.us" in json.dumps(public.get("sources") or []).lower()
    assert (public.get("apply_path") or {}).get("channel") == "contact_authority"


def test_generic_icc_apply_url_and_unbound_required_claim_both_demote(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-import-only")
    from api.server import build_customer_permit_view_model, finalize_permit_lookup_result

    raw = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_name": "Roofing Permit — Tear-Off / Re-Roof",
        "apply_url": "https://www.iccsafe.org/products-and-services/i-codes/2018-i-codes/irc/",
        "source_urls": ["https://www.iccsafe.org/products-and-services/i-codes/2018-i-codes/irc/"],
        "sources": [{"url": "https://www.iccsafe.org/products-and-services/i-codes/2018-i-codes/irc/", "quote": "IRC"}],
    }
    finalized = finalize_permit_lookup_result(raw, "residential reroof", "Delmar", "DE", job_category="residential", evidence_allowed=False)
    public = build_customer_permit_view_model(finalized, "residential reroof", "Delmar", "DE", job_category="residential")
    assert public["permit_required"] is None
    assert public["permit_verdict"] == "VERIFY"
    assert public["permit_decision"] == "VERIFY"
    # The verified Delmar page is an informational office page, not an
    # application start, and the generic ICC reference does not prove the local
    # REQUIRED claim. Preserve both as context while exposing an honest
    # contact-AHJ filing path and nonbinary decision.
    assert public.get("apply_url") is None
    assert "iccsafe.org" not in (public.get("apply_url") or "")
    assert "townofdelmar.us" in json.dumps(public.get("sources") or []).lower()
    assert (public.get("apply_path") or {}).get("channel") == "contact_authority"


def test_v24_crook_county_exact_cell_uses_official_not_required_authority(monkeypatch):
    """A specialty-trade source must not establish a county building permit."""
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")

    resolution = resolve_v24_cell(
        "Crook County",
        "WY",
        "commercial office tenant improvement",
        "commercial",
    )
    assert resolution.status == V24ResolutionStatus.EXACT_CELL_PUBLISHABLE
    assert resolution.key == "WY|crook_county|commercial_tenant_improvement"

    cell = resolution.cell or {}
    validation = validate_v24_cell(cell, strict_snapshots=False, require_live_url_check=False)
    assert validation.ok, validation.to_dict()
    assert cell["tier1"]["main_decision"]["value"] == "NOT_REQUIRED"
    assert cell["tier1"]["permits_required"] == []
    assert cell["tier1"]["trade_authority"] == []
    assert cell["tier1"]["apply"] == []

    provenance = cell["tier1"]["main_decision"]["provenance"]
    assert "crookcounty.wy.gov" in provenance["source_url"]
    assert "does not have Land Use Regulations, Zoning Regulations, Adopted Building Codes, or Building Permit Requirements" in provenance["source_quote"]
    snapshot_path = ROOT / provenance["snapshot_path"]
    assert snapshot_path.is_file()
    assert hashlib.sha256(snapshot_path.read_bytes()).hexdigest() == provenance["snapshot_hash"]
    assert provenance["source_quote"] in snapshot_path.read_text(encoding="utf-8")

    payload = regulated_payload_from_cell(cell)
    assert payload["permit_required"] is False
    assert payload["permit_decision"] == "NOT_REQUIRED"
    assert payload["permit_name"] == "No permit required"
    assert payload["permits_required"] == []
    assert payload["apply"] == []

    rendered = json.dumps(payload, sort_keys=True).lower()
    assert "commercial building permit" not in rendered
    assert "permit to wire" not in rendered
    assert "private holding tank" not in rendered


def test_v24_crook_county_not_required_overrides_pipeline_required_conflict(monkeypatch):
    """The corrected exact authority wins across the real v2.3.1->v2.4 merge order."""
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    from api.v231_decision_cells import reconcile_v231_result, resolve_v231_cell

    result = {
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_verdict": "YES",
        "permit_name": "Commercial Building Permit",
        "permits_required": [
            {
                "permit_type": "Commercial Building Permit",
                "permit_kind": "building",
                "required": True,
            }
        ],
        "hidden_triggers": [],
    }
    v231 = resolve_v231_cell(
        "Crook County",
        "WY",
        "commercial office tenant improvement",
        "commercial",
    )
    reconcile_v231_result(result, v231)
    assert result["_v231_resolution_status"] == "exact_cell_covered"
    assert result["permit_required"] is False
    assert result["permit_decision"] == "NOT_REQUIRED"

    v24 = resolve_v24_cell(
        "Crook County",
        "WY",
        "commercial office tenant improvement",
        "commercial",
    )
    reconcile_authoritative_result(result, v24_resolution=v24, v231_resolution=v231)

    assert result["permit_required"] is False
    assert result["permit_decision"] == "NOT_REQUIRED"
    assert result["permit_verdict"] == "NO"
    assert result["permit_name"] == "No permit required"
    assert result["permits_required"] == []
    assert result["_field_sources"]["permit_required"] == "permitassist_v24_decision_cell"
    assert result["_decision_cell_primary_lock"]["permit_decision"] == "NOT_REQUIRED"


def test_v24_crook_county_not_required_survives_customer_and_report_boundaries(monkeypatch):
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-import-only")
    from api.research_engine import _deterministic_v24_result_from_resolution
    from api import server

    resolution = resolve_v24_cell(
        "Crook County",
        "WY",
        "commercial office tenant improvement",
        "commercial",
    )
    raw = _deterministic_v24_result_from_resolution(
        resolution,
        "commercial office tenant improvement",
        "Crook County",
        "WY",
    )
    assert raw is not None
    reconcile_authoritative_result(raw, v24_resolution=resolution, v231_resolution=None)
    public = server.build_customer_response_egress(
        server._mark_server_owned_result(raw),
        "commercial office tenant improvement",
        "Crook County",
        "WY",
        job_category="commercial",
    )
    assert public["permit_required"] is False
    assert public["permit_decision"] == "NOT_REQUIRED"
    assert public["permit_verdict"] == "NO"

    report_html = server.render_white_label_report_html(
        {
            "result": public,
            "job_type": "commercial office tenant improvement",
            "city": "Crook County",
            "state": "WY",
        }
    )
    rendered = (json.dumps(public, sort_keys=True) + report_html).lower()
    for forbidden in (
        "commercial building permit",
        "permit to wire",
        "private holding tank",
        "permitassist_v24",
        "_v24",
        "decision_cell",
        "cell_id",
        "/home/boban",
    ):
        assert forbidden not in rendered


def test_v24_exact_not_required_lock_survives_internal_finalize_until_public_egress(monkeypatch):
    monkeypatch.setenv("PERMITASSIST_V24_MODE", "active")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-import-only")
    monkeypatch.setenv("SESSION_SECRET", "local-test-only")
    from api.research_engine import _deterministic_v24_result_from_resolution
    from api import server

    resolution = resolve_v24_cell(
        "Crook County",
        "WY",
        "commercial tenant improvement",
        "commercial",
    )
    raw = _deterministic_v24_result_from_resolution(
        resolution,
        "commercial tenant improvement",
        "Crook County",
        "WY",
    )
    assert raw is not None
    reconcile_authoritative_result(raw, v24_resolution=resolution, v231_resolution=None)
    companion_warning = "Commercial scope may require companion reviews/permits not fully proven here: electrical, mechanical, plumbing."
    raw["warnings"] = [
        "Commercial scope detected; primary permit was repaired or forced away from residential/trade-only leakage. confirm the exact permit name and form title with the building department before filing.",
        companion_warning,
        "The unverified NOT_REQUIRED answer was demoted to VERIFY because no claim-linked official decision evidence was available.",
    ]
    raw["source_support"] = {
        "has_source_backed_evidence": False,
        "has_official_source": True,
        "decision_mutation_allowed": False,
        "confidence_tier": "AHJ_DIRECT",
    }
    raw["permits_required_logic"] = [{
        "permit_type": "Commercial Building / Tenant Improvement Permit",
        "included_because": "Official permit rule: tenant-improvement work requires building/TI review.",
        "filing_family": "building",
    }]
    raw["apply_path"] = {
        "state": "NOT_APPLICABLE",
        "channel": "no_permit_required",
        "permit_category": "Building",
        "permit_type": "Commercial Building / Tenant Improvement Permit",
        "documents_to_prepare": ["scope of work", "plans/drawings if required"],
        "portal_selection_path": [
            "No verified online filing path is available; verify the correct portal before filing",
            "Ask the building department which permit category best matches",
        ],
        "steps": [
            "No verified online filing path is available; verify the correct portal before filing",
            "Prepare scope of work before final submission",
        ],
        "stop_before": "final submit, payment, signature, or legal attestation",
    }
    assert raw["_decision_cell_primary_lock"]["permit_decision"] == "NOT_REQUIRED"

    # Keep this gate offline while retaining the real classifier, source-floor,
    # lock-enforcement, sanitizer, and customer-egress sequence.
    monkeypatch.setattr(server, "sanitize_result_urls", lambda result: result)
    monkeypatch.setattr(server, "enrich_result_response", lambda result, *_args, **_kwargs: result)
    monkeypatch.setattr(server, "apply_permitiq_quality_gate", lambda result, *_args, **_kwargs: result)

    finalized = server.finalize_permit_lookup_result(
        server._mark_server_owned_result(raw),
        "commercial tenant improvement",
        "Crook County",
        "WY",
        evidence_allowed=False,
        job_category="commercial",
    )
    assert finalized["_decision_cell_primary_lock"]["permit_decision"] == "NOT_REQUIRED"
    assert finalized["permit_decision"] == "NOT_REQUIRED"
    assert finalized["permit_required"] is False
    assert finalized["permits_required"] == []
    assert finalized["apply_path"]["documents_to_prepare"] == []
    assert finalized["apply_path"]["portal_selection_path"] == []
    assert finalized["apply_path"]["steps"] == []
    assert finalized["apply_path"]["stop_before"] is None

    public = server.build_customer_response_egress(
        server._mark_server_owned_result(finalized),
        "commercial tenant improvement",
        "Crook County",
        "WY",
        job_category="commercial",
    )
    assert public["permit_decision"] == "NOT_REQUIRED"
    assert public["permit_required"] is False
    assert public["permit_verdict"] == "NO"
    assert public["permit_name"] == "No permit required"
    assert public["permits_required"] == []
    assert public["customer_headline"].lower().startswith("no permit required")
    assert "file the required permit" not in public["customer_next_step"].lower()
    assert public["customer_result_summary"]["permit_decision"] == "NOT_REQUIRED"
    assert public["customer_result_summary"]["permit_name"] == "No permit required"
    assert public["customer_first_screen_summary"]["decision"] == "NOT_REQUIRED"
    assert "file the required permit" not in public["customer_first_screen_summary"]["next_action"].lower()
    assert companion_warning in public["warnings"]
    related_by_family = {
        row.get("family"): row
        for row in public.get("related_permits", [])
        if isinstance(row, dict)
    }
    assert related_by_family["planning"]["decision"] == "CONDITIONAL"
    assert related_by_family["planning"]["condition_text"]
    assert related_by_family["occupancy"]["decision"] == "CONDITIONAL"
    assert related_by_family["occupancy"]["condition_text"]
    assert public["source_support"]["has_source_backed_evidence"] is True
    assert public["source_support"]["has_official_source"] is True
    assert public["source_support"]["decision_mutation_allowed"] is False
    assert public["permits_required_logic"] == []
    assert public["apply_path"]["permit_type"] is None
    assert public["apply_path"]["permit_category"] is None
    assert public["apply_path"].get("documents_to_prepare", []) == []
    assert public["apply_path"].get("portal_selection_path", []) == []
    assert public["apply_path"].get("steps", []) == []
    assert public["apply_path"].get("stop_before") is None
    public_blob = json.dumps(public, sort_keys=True).lower()
    assert "permit required for the resolved scope" not in public_blob
    assert "required permit package" not in public_blob
    assert "file the required permit" not in public_blob
    assert "before filing" not in public_blob
    assert "demoted to verify" not in public_blob
    assert "primary permit was repaired" not in public_blob
    assert "forced away from residential" not in public_blob
    assert "requires building/ti review" not in public_blob
    assert "commercial building / tenant improvement permit" not in public_blob
    checklist = server.build_checklist_fallback(
        public,
        "commercial tenant improvement",
        "Crook County",
        "WY",
    )
    checklist_blob = json.dumps(checklist, sort_keys=True).lower()
    assert checklist["title"] == "No-Primary-Permit Scope Checklist"
    assert "pull no permit required" not in checklist_blob
    assert "pay permit fee" not in checklist_blob
    assert "required documents" not in checklist_blob
    assert "planning/zoning use verification" in checklist_blob
    assert "certificate of occupancy" in checklist_blob
    report_template = server.load_report_template()
    assert "const isNotRequired" in report_template
    assert "isRequired ? 'Required documents' : (isNotRequired ? 'Scope records & safeguards' : 'Information to verify')" in report_template
    assert "isRequired ? 'Inspection checklist' : (isNotRequired ? 'Inspection status' : 'Possible inspection triggers')" in report_template
    assert "isRequired ? 'Pre-construction checklist' : (isNotRequired ? 'Scope & companion checklist' : 'Verification checklist')" in report_template
    assert "No primary permit inspection applies to the resolved scope" in report_template
    assert "_decision_cell_primary_lock" not in public_blob
