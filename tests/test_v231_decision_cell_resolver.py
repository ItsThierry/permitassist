import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
for path in (ROOT, API):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def test_index_contract_and_golden_cells_present():
    index_path = ROOT / "knowledge" / "permitassist_decision_cell_index_v231.json"
    cells_path = ROOT / "knowledge" / "permitassist_decision_cells_v231.json"
    manifest_path = ROOT / "knowledge" / "permitassist_v231_import_manifest.json"

    assert index_path.exists()
    assert cells_path.exists()
    assert manifest_path.exists()

    index_doc = json.loads(index_path.read_text())
    index = index_doc["index"]
    assert len(index) == 2489

    expected = {
        "AZ|buckeye|reroof": "us-az-buckeye__residential__reroof__building",
        # Run3 compresses richer commercial-construction cells into the broad
        # live runtime bucket while preserving the cell slug/label internally.
        "AZ|goodyear|commercial_tenant_improvement": "us-az-goodyear__commercial__commercial_construction__building",
        "AZ|gilbert|commercial_tenant_improvement": "us-az-gilbert__commercial__commercial_tenant_improvement__building",
        "AK|anchorage|commercial_tenant_improvement": "us-ak-anchorage__commercial__commercial_tenant_improvement__building",
    }
    for key, cell_id in expected.items():
        assert index[key]["cell_id"] == cell_id
        assert index[key]["main_decision"] == "REQUIRED"
        assert index[key]["publish_status"] == "PUBLISHABLE"


def test_golden_exact_resolutions_and_project_classifier():
    from api.v231_decision_cells import ResolutionStatus, classify_project_candidates, resolve_v231_cell

    cases = [
        ("Buckeye", "AZ", "residential roof tear-off and reroof shingles", "residential", "us-az-buckeye__residential__reroof__building", "reroof", "AZ|buckeye|reroof"),
        ("Goodyear", "AZ", "new commercial construction retail shell", "commercial", "us-az-goodyear__commercial__commercial_construction__building", "commercial_construction", "AZ|goodyear|commercial_tenant_improvement"),
        ("Gilbert", "AZ", "commercial office tenant improvement", "commercial", "us-az-gilbert__commercial__commercial_tenant_improvement__building", "commercial_tenant_improvement", "AZ|gilbert|commercial_tenant_improvement"),
        ("Anchorage", "AK", "commercial office tenant improvement", "commercial", "us-ak-anchorage__commercial__commercial_tenant_improvement__building", "commercial_tenant_improvement", "AK|anchorage|commercial_tenant_improvement"),
    ]
    for city, state, job_type, category, cell_id, classifier_slug, expected_key in cases:
        assert classifier_slug in classify_project_candidates(job_type, category)
        resolution = resolve_v231_cell(city, state, job_type, category)
        assert resolution.status == ResolutionStatus.EXACT_CELL_COVERED
        assert resolution.cell["cell_id"] == cell_id
        assert resolution.key == expected_key


def test_wrong_project_and_noncovered_inputs_abstain_without_neighbor_cell():
    from api.v231_decision_cells import ResolutionStatus, resolve_v231_cell

    buckeye_ti = resolve_v231_cell("Buckeye", "AZ", "commercial tenant improvement", "commercial")
    assert buckeye_ti.status == ResolutionStatus.AHJ_COVERED_PROJECT_NOT_COVERED
    assert buckeye_ti.cell is None
    assert buckeye_ti.key == "AZ|buckeye|commercial_tenant_improvement"

    unknown = resolve_v231_cell("Not A Real Covered City", "AZ", "commercial tenant improvement", "commercial")
    assert unknown.status == ResolutionStatus.AHJ_NOT_COVERED
    assert unknown.cell is None

    typo = resolve_v231_cell("Buckeyee", "AZ", "reroof", "residential")
    assert typo.status == ResolutionStatus.AHJ_NOT_COVERED
    assert typo.cell is None


def test_ambiguous_project_text_abstains_instead_of_guessing_from_category():
    from api.v231_decision_cells import ResolutionStatus, classify_project_candidates, resolve_v231_cell

    assert classify_project_candidates("commercial work", "commercial") == []
    assert classify_project_candidates("commercial waterproofing membrane", "commercial") == []
    assert classify_project_candidates("soundproofing an office conference room", "commercial") == []
    assert classify_project_candidates("anti corrosion coating", "commercial") == []
    assert classify_project_candidates("multi tenant directory signage", "commercial") == []
    assert classify_project_candidates("residential interior alteration", "residential") == ["residential_remodel"]
    assert classify_project_candidates("home interior remodel", "residential") == ["residential_remodel"]
    assert classify_project_candidates("new building", "residential") == []
    assert classify_project_candidates("ground-up detached garage", "residential") == []
    assert classify_project_candidates("commercial tenant improvement and reroof", "commercial") == []
    assert classify_project_candidates("new commercial shell plus office tenant improvement", "commercial") == []
    resolution = resolve_v231_cell("Goodyear", "AZ", "commercial work", "commercial")
    assert resolution.status == ResolutionStatus.AMBIGUOUS_ABSTAIN
    assert resolution.cell is None

    residential_new_building = resolve_v231_cell("Goodyear", "AZ", "new building", "residential")
    assert residential_new_building.status == ResolutionStatus.AMBIGUOUS_ABSTAIN
    assert residential_new_building.cell is None

    residential_ground_up = resolve_v231_cell("Goodyear", "AZ", "ground-up detached garage", "residential")
    assert residential_ground_up.status == ResolutionStatus.AMBIGUOUS_ABSTAIN
    assert residential_ground_up.cell is None


def test_missing_corrupt_and_malformed_index_fail_safe(tmp_path):
    from api.v231_decision_cells import ResolutionStatus, resolve_v231_cell

    missing = tmp_path / "missing.json"
    assert resolve_v231_cell("Buckeye", "AZ", "reroof", "residential", index_path=missing).status == ResolutionStatus.INDEX_UNAVAILABLE

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json")
    assert resolve_v231_cell("Buckeye", "AZ", "reroof", "residential", index_path=corrupt).status == ResolutionStatus.INDEX_UNAVAILABLE

    malformed = tmp_path / "malformed.json"
    malformed.write_text(json.dumps({"index": []}))
    assert resolve_v231_cell("Buckeye", "AZ", "reroof", "residential", index_path=malformed).status == ResolutionStatus.INDEX_UNAVAILABLE


def test_late_reconciliation_conflict_precedence_merge_not_neuter_and_idempotency():
    from api.v231_decision_cells import reconcile_v231_result, resolve_v231_cell

    base = {
        "permit_verdict": "NO",
        "permit_required": False,
        "permit_decision": "NOT_REQUIRED",
        "not_required_reason": "stale cache claimed no permit required",
        "permit_name": "Stale Generic Permit",
        "applying_office": "Old Office",
        "apply_url": "https://old.example.invalid",
        "authority_model": {"pipeline": "structured authority model"},
        "permits_required": [
            {"permit_type": "Electrical Permit", "required": True, "source": "pipeline"},
            {"permit_type": "Building Permit", "required": "maybe", "source": "pipeline", "notes": "stale"},
        ],
        "sources": [{"url": "https://pipeline.example/source", "title": "Pipeline source"}],
        "hidden_triggers": [{"trigger": "ada_path_of_travel", "severity": "watch"}],
        "state_expert_pack": {"state": "AZ"},
        "fee_range": "$500-$2,000 verify with AHJ",
        "fee_calculator": {"fee": None, "note": "Provide job value"},
        "approval_timeline": "2-4 weeks",
        "checklist": ["Existing plan set item"],
        "rejection_patterns": ["Missing site plan"],
        "permit_ready_score": 68,
        "_field_sources": {"fee_range": "pipeline"},
    }
    original = copy.deepcopy(base)
    resolution = resolve_v231_cell("Gilbert", "AZ", "commercial office tenant improvement", "commercial")

    once = reconcile_v231_result(copy.deepcopy(base), resolution)
    twice = reconcile_v231_result(copy.deepcopy(once), resolution)

    assert once == twice
    assert once["permit_verdict"] == "YES"
    assert once["permit_required"] is True
    assert once["permit_decision"] == "REQUIRED"
    assert "not_required_reason" not in once
    assert once["permit_name"] == "Building Permit"
    assert once["applying_office"] == "Town of Gilbert Development Services"
    assert once["apply_url"] == "https://www.gilbertaz.gov/how-do-i-/apply-for/building-permit"
    assert once["_v231_decision_cell"]["cell_id"] == "us-az-gilbert__commercial__commercial_tenant_improvement__building"

    # Product layers are additive and must not be wiped by the authoritative cell.
    for field in (
        "hidden_triggers",
        "state_expert_pack",
        "fee_range",
        "fee_calculator",
        "approval_timeline",
        "checklist",
        "rejection_patterns",
        "permit_ready_score",
        "authority_model",
    ):
        assert once[field] == original[field]

    permit_names = [p["permit_type"] for p in once["permits_required"]]
    assert "Electrical Permit" in permit_names
    assert permit_names.count("Building Permit") == 1
    building = next(p for p in once["permits_required"] if p["permit_type"] == "Building Permit")
    assert building["required"] is True
    assert building["source"] == "permitassist_v231_decision_cell"

    urls = [s.get("url") for s in once["sources"]]
    assert "https://www.gilbertaz.gov/how-do-i-/apply-for/building-permit" in urls
    assert "https://pipeline.example/source" in urls
    assert len(urls) == len(set(urls))
    assert once["_field_sources"]["permit_required"] == "permitassist_v231_decision_cell"
    assert once["_field_sources"]["fee_range"] == "pipeline"


def test_not_required_cell_sets_no_only_when_pipeline_has_no_required_safety_signal():
    from api.v231_decision_cells import ResolutionStatus, V231Resolution, reconcile_v231_result

    cell = {
        "cell_id": "test-not-required-cell",
        "jurisdiction_id": "test-city",
        "project_type_slug": "residential_remodel",
        "main_decision": "NOT_REQUIRED",
        "publish_status": "PUBLISHABLE",
        "permit_name": "Building Permit",
        "ahj_name": "Test City Building Department",
        "authority_model": {
            "authority_type": "municipal",
            "application_authority": "Test City Building Department",
            "application_url": "https://example.gov/permits",
        },
        "source_evidence": [{"url": "https://example.gov/permits", "final_url": "https://example.gov/permits", "quote": "No building permit is required."}],
        "customer_action": "No building permit required for this exact scope; keep HOA/private rules separate.",
    }
    resolution = V231Resolution(ResolutionStatus.EXACT_CELL_COVERED, cell=cell)
    base = {
        "permit_verdict": "NO",
        "permit_required": False,
        "permit_decision": "NOT_REQUIRED",
        "permit_name": "Pipeline no-permit finding",
        "permits_required": [],
        "sources": [],
        "authority_model": {"existing": "structured model must survive"},
    }

    out = reconcile_v231_result(copy.deepcopy(base), resolution)

    assert out["permit_verdict"] == "NO"
    assert out["permit_required"] is False
    assert out["permit_decision"] == "NOT_REQUIRED"
    assert out["permit_name"] == "Pipeline no-permit finding"
    assert out["permits_required"] == []
    assert out["authority_model"] == {"existing": "structured model must survive"}
    assert out["application_authority_name"] == "Test City Building Department"


def test_not_required_cell_refuses_to_suppress_pipeline_required_safety_signals():
    from api.v231_decision_cells import ResolutionStatus, V231Resolution, reconcile_v231_result

    cell = {
        "cell_id": "test-not-required-conflict-cell",
        "jurisdiction_id": "test-city",
        "project_type_slug": "residential_remodel",
        "main_decision": "NOT_REQUIRED",
        "publish_status": "PUBLISHABLE",
        "permit_name": "Building Permit",
        "ahj_name": "Test City Building Department",
        "authority_model": {"application_url": "https://example.gov/permits"},
        "source_evidence": [{"url": "https://example.gov/permits", "quote": "No building permit is required."}],
    }
    resolution = V231Resolution(ResolutionStatus.EXACT_CELL_COVERED, cell=cell)
    base = {
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permit_name": "Electrical Permit",
        "permits_required": [{"permit_type": "Electrical Permit", "required": True, "source": "pipeline"}],
        "hidden_triggers": [{"trigger": "service_upgrade", "severity": "critical"}],
        "checklist": ["Pipeline electrical checklist"],
        "sources": [{"url": "https://pipeline.example/electrical"}],
    }

    out = reconcile_v231_result(copy.deepcopy(base), resolution)

    for field, value in base.items():
        assert out[field] == value
    assert out["_v231_resolution_status"] == "not_required_safety_conflict"
    assert out["_field_sources"]["permit_required"] == "pipeline_safety_signal"


def test_no_exact_cell_reconciliation_leaves_result_unchanged():
    from api.v231_decision_cells import ResolutionStatus, V231Resolution, reconcile_v231_result, resolve_v231_cell

    original = {
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_decision": "REQUIRED",
        "permits_required": [{"permit_type": "Pipeline Permit", "required": True}],
        "sources": [{"url": "https://pipeline.example/source"}],
        "checklist": ["Pipeline checklist"],
    }
    resolution = resolve_v231_cell("Buckeye", "AZ", "commercial tenant improvement", "commercial")
    assert reconcile_v231_result(copy.deepcopy(original), resolution) == original

    for status in (
        ResolutionStatus.AMBIGUOUS_ABSTAIN,
        ResolutionStatus.AHJ_NOT_COVERED,
        ResolutionStatus.INDEX_UNAVAILABLE,
    ):
        assert reconcile_v231_result(copy.deepcopy(original), V231Resolution(status, reason="test")) == original


def test_internal_v231_fields_do_not_escape_customer_view_model():
    import os

    os.environ.setdefault("OPENAI_API_KEY", "test-key-for-import-only")
    from api.server import build_customer_permit_view_model
    from api.v231_decision_cells import reconcile_v231_result, resolve_v231_cell

    result = reconcile_v231_result(
        {
            "permit_verdict": "NO",
            "permit_required": False,
            "permit_decision": "NOT_REQUIRED",
            "permit_name": "Stale Generic Permit",
            "permits_required": [{"permit_type": "Building Permit", "required": "maybe", "source": "pipeline"}],
            "sources": [{"url": "https://pipeline.example/source", "title": "Pipeline source"}],
        },
        resolve_v231_cell("Gilbert", "AZ", "commercial office tenant improvement", "commercial"),
    )

    public = build_customer_permit_view_model(result, "commercial office tenant improvement", "Gilbert", "AZ")
    serialized = json.dumps(public, sort_keys=True).lower()

    assert "_v231" not in serialized
    assert "v2.3.1" not in serialized
    assert "v231-runtime-resolver" not in serialized
    assert "permitassist_v231_decision_cell" not in serialized


def test_build_authoritative_context_is_grounding_only_and_not_customer_marketing():
    from api.v231_decision_cells import ResolutionStatus, V231Resolution, build_v231_prompt_context, resolve_v231_cell

    resolution = resolve_v231_cell("Goodyear", "AZ", "new commercial construction retail shell", "commercial")
    context = build_v231_prompt_context(resolution)
    assert "AUTHORITATIVE v2.3.1 DECISION CELL CONTEXT" in context
    assert "us-az-goodyear__commercial__commercial_construction__building" in context
    assert "Run the normal PermitAssist pipeline" in context
    assert "do not short-circuit" in context.lower()
    assert "tell the customer v2.3.1" not in context.lower()

    not_required_context = build_v231_prompt_context(V231Resolution(ResolutionStatus.EXACT_CELL_COVERED, cell={
        "cell_id": "test-not-required-cell",
        "main_decision": "NOT_REQUIRED",
        "publish_status": "PUBLISHABLE",
        "permit_name": "Building Permit",
        "source_evidence": [{"quote": "No building permit is required."}],
    }))
    assert not_required_context == ""


def _apply_contract(result, job_type, city="Goodyear", state="AZ"):
    from api.permit_decision import apply_permit_decision_contract

    return apply_permit_decision_contract(copy.deepcopy(result), job_type, city, state)


def _goodyear_construction_cell_result():
    from api.v231_decision_cells import reconcile_v231_result, resolve_v231_cell

    base = {
        "permit_verdict": "NO",
        "permit_required": False,
        "permit_decision": "NOT_REQUIRED",
        "permit_name": "Commercial Building / Tenant Improvement Permit",
        "customer_next_step": "File under Commercial Building / Tenant Improvement Permit with Goodyear Development Services.",
        "customer_result_summary": {
            "next_step": "File under Commercial Building / Tenant Improvement Permit with Goodyear Development Services.",
        },
        "customer_first_screen_summary": {
            "next_action": "File under Commercial Building / Tenant Improvement Permit with Goodyear Development Services.",
        },
        "permits_required": [
            {"permit_type": "Commercial Building / Tenant Improvement Permit", "kind": "Commercial Building / Tenant Improvement", "required": True, "source": "pipeline"},
            {"permit_type": "Electrical Permit", "kind": "Electrical", "required": True, "source": "pipeline"},
        ],
        "sources": [{"url": "https://www.goodyearaz.gov/government/departments/development-services/building-safety", "title": "Goodyear Building Safety"}],
        "claim_citations": [{"field": "permit_name", "source_url": "https://www.goodyearaz.gov/government/departments/development-services/building-safety", "claim": "Goodyear permits", "quoted_snippet": "Building Safety"}],
    }
    resolution = resolve_v231_cell("Goodyear", "AZ", "new commercial construction retail shell", "commercial")
    return reconcile_v231_result(base, resolution)


def _synthetic_not_required_resolution(cell_id="test-not-required-cell"):
    from api.v231_decision_cells import ResolutionStatus, V231Resolution

    return V231Resolution(ResolutionStatus.EXACT_CELL_COVERED, cell={
        "cell_id": cell_id,
        "jurisdiction_id": "test-city",
        "project_type_slug": "residential_remodel",
        "main_decision": "NOT_REQUIRED",
        "publish_status": "PUBLISHABLE",
        "permit_name": "Building Permit",
        "ahj_name": "Test City Building Department",
        "authority_model": {
            "application_authority": "Test City Building Department",
            "application_url": "https://testcity.gov/permits/no-permit-needed",
        },
        "source_evidence": [{"url": "https://testcity.gov/permits/no-permit-needed", "final_url": "https://testcity.gov/permits/no-permit-needed", "quote": "No permit is required for this exact scope."}],
        "customer_action": "Keep the official no-permit note with the job file before starting work.",
    })


def _serialized_public(value):
    return json.dumps(value, sort_keys=True, default=str).lower()


def test_v231_goodyear_construction_direct_resolver_preserves_cell_primary():
    out = _apply_contract(_goodyear_construction_cell_result(), "new commercial construction retail shell")

    assert out["permit_decision"] == "REQUIRED"
    assert out["permit_required"] is True
    assert out["permit_name"] == "Construction Permit"
    assert out["permit_kind"] == "Building"
    assert out["permits_required"][0]["permit_type"] == "Construction Permit"
    assert "Tenant Improvement" not in out["customer_headline"]
    assert "Tenant Improvement" not in out["customer_next_step"]
    assert "Construction Permit" in out["customer_next_step"]


def test_goodyear_commercial_construction_cell_stays_primary_in_public_view_model():
    import os

    os.environ.setdefault("OPENAI_API_KEY", "test-key-for-import-only")
    from api.server import build_customer_permit_view_model

    public = build_customer_permit_view_model(_goodyear_construction_cell_result(), "new commercial construction retail shell", "Goodyear", "AZ")

    assert public["permit_name"] == "Construction Permit"
    assert public["permit_kind"] == "Building"
    assert public["permits_required"][0]["permit_type"] == "Construction Permit"
    assert "Tenant Improvement" not in public["customer_headline"]
    assert "Tenant Improvement" not in public["customer_next_step"]
    assert "Construction Permit" in public["customer_next_step"]
    assert "Tenant Improvement" not in public["customer_result_summary"]["next_step"]
    assert "Construction Permit" in public["customer_result_summary"]["next_step"]
    assert "Tenant Improvement" not in public["customer_first_screen_summary"]["next_action"]
    assert "Construction Permit" in public["customer_first_screen_summary"]["next_action"]


def test_cell_primary_lock_survives_double_contract_application():
    once = _apply_contract(_goodyear_construction_cell_result(), "new commercial construction retail shell")
    twice = _apply_contract(once, "new commercial construction retail shell")

    assert twice["permit_name"] == "Construction Permit"
    assert twice["permit_kind"] == "Building"
    assert twice["permits_required"][0]["permit_type"] == "Construction Permit"


def test_cell_primary_lock_survives_customer_view_sanitize_and_terminal_pass():
    import os

    os.environ.setdefault("OPENAI_API_KEY", "test-key-for-import-only")
    from api.server import build_customer_permit_view_model, sanitize_customer_visible_result
    from api.permit_decision import apply_permit_decision_contract

    original = _goodyear_construction_cell_result()
    cell_lock = original.get("_decision_cell_primary_lock")
    cleaned = sanitize_customer_visible_result(original, strip_internal_keys=True)
    if cell_lock:
        cleaned["_decision_cell_primary_lock"] = cell_lock
    terminal = apply_permit_decision_contract(cleaned, "new commercial construction retail shell", "Goodyear", "AZ")
    public = build_customer_permit_view_model(original, "new commercial construction retail shell", "Goodyear", "AZ")

    assert terminal["permit_name"] == "Construction Permit"
    assert public["permit_name"] == "Construction Permit"
    assert public["permits_required"][0]["permit_type"] == "Construction Permit"


def test_not_required_cell_suppressed_when_pipeline_required_safety_signal_present():
    from api.v231_decision_cells import reconcile_v231_result

    base = {"permit_verdict": "YES", "permit_required": True, "permit_decision": "REQUIRED", "permit_name": "Electrical Permit", "permits_required": [{"permit_type": "Electrical Permit", "required": True}], "hidden_triggers": [{"trigger": "service_upgrade"}]}
    out = reconcile_v231_result(copy.deepcopy(base), _synthetic_not_required_resolution("safety-conflict-cell"))

    assert out["permit_decision"] == "REQUIRED"
    assert out["permit_required"] is True
    assert out["permits_required"] == base["permits_required"]
    assert "_decision_cell_primary_lock" not in out


def test_not_required_cell_lock_keeps_no_permit_primary_and_does_not_inject_row():
    from api.v231_decision_cells import reconcile_v231_result

    base = {"permit_verdict": "NO", "permit_required": False, "permit_decision": "NOT_REQUIRED", "permit_name": "No permit required", "permits_required": [], "sources": ["https://testcity.gov/permits/no-permit-needed"], "positive_exemption_evidence": [{"source_url": "https://testcity.gov/permits/no-permit-needed", "quote": "No permit is required."}]}
    out = reconcile_v231_result(copy.deepcopy(base), _synthetic_not_required_resolution())
    contracted = _apply_contract(out, "cosmetic repainting only no electrical plumbing mechanical or wall changes", "Test City", "AZ")

    assert contracted["permit_decision"] == "NOT_REQUIRED"
    assert contracted["permit_required"] is False
    assert contracted["permit_name"] == "No permit required"
    assert contracted["permits_required"] == []


def test_noncovered_ahj_fallback_has_no_lock_and_is_unchanged():
    from api.v231_decision_cells import reconcile_v231_result, resolve_v231_cell

    base = {"permit_verdict": "YES", "permit_required": True, "permit_decision": "REQUIRED", "permit_name": "Pipeline Permit", "permits_required": [{"permit_type": "Pipeline Permit", "required": True}]}
    out = reconcile_v231_result(copy.deepcopy(base), resolve_v231_cell("Not A Real Covered City", "AZ", "commercial tenant improvement", "commercial"))

    assert out == base
    assert "_decision_cell_primary_lock" not in out


def test_wrong_project_guard_has_no_lock():
    from api.v231_decision_cells import reconcile_v231_result, resolve_v231_cell

    base = {"permit_decision": "REQUIRED", "permit_required": True, "permit_name": "Pipeline Permit", "permits_required": [{"permit_type": "Pipeline Permit", "required": True}]}
    out = reconcile_v231_result(copy.deepcopy(base), resolve_v231_cell("Buckeye", "AZ", "commercial tenant improvement", "commercial"))

    assert out == base
    assert "_decision_cell_primary_lock" not in out


def test_ambiguous_broad_input_guard_has_no_lock():
    from api.v231_decision_cells import reconcile_v231_result, resolve_v231_cell

    base = {"permit_decision": "REQUIRED", "permit_required": True, "permit_name": "Pipeline Permit", "permits_required": [{"permit_type": "Pipeline Permit", "required": True}]}
    out = reconcile_v231_result(copy.deepcopy(base), resolve_v231_cell("Goodyear", "AZ", "new building", "residential"))

    assert out == base
    assert "_decision_cell_primary_lock" not in out


def test_ti_cells_still_primary_when_cell_says_ti():
    from api.v231_decision_cells import reconcile_v231_result, resolve_v231_cell

    base = {"permit_verdict": "YES", "permit_required": True, "permit_decision": "REQUIRED", "permit_name": "Pipeline Building Permit", "permits_required": [{"permit_type": "Building Permit", "required": True}], "sources": ["https://www.gilbertaz.gov/how-do-i-/apply-for/building-permit"]}
    out = _apply_contract(reconcile_v231_result(base, resolve_v231_cell("Gilbert", "AZ", "commercial office tenant improvement", "commercial")), "commercial office tenant improvement", "Gilbert", "AZ")

    assert out["permit_decision"] == "REQUIRED"
    assert out["permit_name"] == "Building Permit"
    assert out["permit_kind"] == "Building"
    assert out["permits_required"][0]["permit_type"] == "Building Permit"


def test_locked_primary_preserves_required_trade_and_companion_rows():
    out = _apply_contract(_goodyear_construction_cell_result(), "new commercial construction retail shell with electrical service", "Goodyear", "AZ")
    permit_names = [permit.get("permit_type") for permit in out["permits_required"]]

    assert permit_names[0] == "Construction Permit"
    assert "Electrical Permit" in permit_names
    assert any(trade.get("kind") == "Electrical" for trade in out.get("trade_permits", []))


def test_locked_primary_dedupes_cosmetic_construction_name_variants():
    result = _goodyear_construction_cell_result()
    result["permits_required"].extend([
        {"permit_type": "Construction permit", "required": True, "source": "pipeline"},
        {"permit_type": "Building Construction Permit", "required": True, "source": "pipeline"},
    ])

    out = _apply_contract(result, "new commercial construction retail shell", "Goodyear", "AZ")
    names = [permit.get("permit_type") for permit in out["permits_required"]]

    assert names[0] == "Construction Permit"
    assert names.count("Construction Permit") == 1
    assert not any(name in {"Construction permit", "Building Construction Permit"} for name in names[1:])


def test_locked_apply_url_still_passes_customer_source_filtering():
    import os

    os.environ.setdefault("OPENAI_API_KEY", "test-key-for-import-only")
    from api.server import build_customer_permit_view_model

    public = build_customer_permit_view_model(_goodyear_construction_cell_result(), "new commercial construction retail shell", "Goodyear", "AZ")
    urls = public.get("source_urls") or []

    assert public["apply_url"].startswith("https://")
    assert public["apply_url"] in urls or any(src.get("url") == public["apply_url"] for src in public.get("sources", []) if isinstance(src, dict))


def test_internal_cell_lock_never_escapes_public_view_model():
    import os

    os.environ.setdefault("OPENAI_API_KEY", "test-key-for-import-only")
    from api.server import build_customer_permit_view_model

    public = build_customer_permit_view_model(_goodyear_construction_cell_result(), "new commercial construction retail shell", "Goodyear", "AZ")
    serialized = _serialized_public(public)

    for forbidden in ("v2.3.1", "_v231_", "permitassist_v231_decision_cell", "_decision_cell_primary_lock", "decision_cell", "cell_id", "resolver", "source metadata"):
        assert forbidden not in serialized
