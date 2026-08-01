import importlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
for path in (ROOT, API):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from api import permit_manifest, permit_model  # noqa: E402
from api.filing_packet_reconciler import ensure_required_filing_rows  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "fresh150_universal_red_contracts_20260730.json"
FRESH150 = Path("/mnt/d/AI/HermesData/hermes-default/backups/permitassist-checkpoints/2026-07-30-fresh150-real-life-eval")


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_phase0_fixture_is_complete_and_bound_to_exact_production_commit():
    fixture = _fixture()
    assert fixture["source_commit"] == "0a30728ac6c16ba9f6fef24d26046e6629e7631a"
    assert len(fixture["required_to_unknown"]) == 13
    assert len(fixture["conditional_overclaims"]) == 8
    ids = {
        *(row["case_id"] for row in fixture["required_to_unknown"]),
        *(row["case_id"] for row in fixture["conditional_overclaims"]),
        fixture["false_required_exemption"]["case_id"],
        fixture["infrastructure_failure"]["case_id"],
    }
    assert len(ids) == 23
    protected = fixture["protected_prior_correct"]
    assert len(protected) == 16
    assert {(row["segment"], row["coverage"]) for row in protected} == {
        ("commercial", "enriched_exact"),
        ("commercial", "fallback"),
        ("residential", "enriched_exact"),
        ("residential", "fallback"),
    }


def test_phase0_protected_prior_correct_sample_remains_definitive():
    if not FRESH150.is_dir():
        raise AssertionError("Fresh150 source artifacts are required; protected baseline must not skip")
    for case in _fixture()["protected_prior_correct"]:
        payload = json.loads((FRESH150 / case["artifact"]).read_text(encoding="utf-8"))
        assert payload.get("permit_decision") == "REQUIRED", case["case_id"]
        assert payload.get("permit_required") is True, case["case_id"]


def test_phase0_saved_unknowns_share_the_proven_integrity_sink_and_lost_sources():
    if not FRESH150.is_dir():
        raise AssertionError("Fresh150 source artifacts are required; this RED trace gate must not skip")
    for case in _fixture()["required_to_unknown"]:
        payload = json.loads((FRESH150 / case["artifact"]).read_text(encoding="utf-8"))
        assert payload["permit_decision"] == "UNKNOWN", case["case_id"]
        assert payload["decision_source"] == "permit_rule_engine_integrity_fail_closed", case["case_id"]
        assert payload.get("source_urls") == [], case["case_id"]
        assert payload.get("sources") == [], case["case_id"]


def test_red_conditional_row_cannot_be_promoted_by_filing_reconciliation():
    original = {
        "permit_decision": "CONDITIONAL",
        "permit_required": None,
        "permit_verdict": "CONDITIONAL",
        "permits_required": [
            {
                "permit_type": "Planning/Zoning Review",
                "filing_family": "planning_zoning",
                "status": "CONDITIONAL",
                "decision": "CONDITIONAL_REQUIRED",
                "required": False,
                "required_if": "only if the proposed use is not allowed by right",
                "source_url": "https://official.example.gov/zoning",
            }
        ],
    }
    out = ensure_required_filing_rows(
        original,
        "Commercial office interior refresh; no change of use, occupancy, walls, MEP, fire, exterior, or signage work.",
        "Example City",
        "EX",
    )
    assert out["permit_decision"] == "CONDITIONAL"
    assert out["permit_required"] is None
    assert out["permit_verdict"] == "CONDITIONAL"
    row = next(r for r in out["permits_required"] if r.get("filing_family") == "planning_zoning")
    assert row["status"] == "CONDITIONAL"
    assert row["decision"] == "CONDITIONAL_REQUIRED"
    assert row["required"] is False


def test_typed_package_projection_is_monotonic_for_every_nonbinary_status():
    for status in ("CONDITIONAL", "NEEDS_INPUT", "VERIFY"):
        original = {
            "permit_decision": status,
            "permit_required": None,
            "permit_verdict": status,
            "permits_required": [{
                "permit_type": "Electrical Permit Verification",
                "filing_family": "electrical",
                "status": status,
                "decision": status,
                "required": None,
            }],
        }
        model_input, package = permit_model.build_permit_package(
            permit_model.capture_permit_authority_input(original),
            "commercial tenant improvement with electrical outlets",
            "Example City",
            "EX",
        )
        assert package.decision == status
        assert package.required is False
        projected = permit_model.project_permit_package(
            model_input,
            package,
            "commercial tenant improvement with electrical outlets",
            "Example City",
            "EX",
        )
        assert projected["permit_decision"] == status
        assert projected["permit_required"] is None
        assert projected["permit_verdict"] == status
        rows = projected["permits_required"]
        assert rows
        assert any(row.get("status") == status for row in rows)
        assert all(row.get("status") in {"CONDITIONAL", "NEEDS_INPUT", "VERIFY"} for row in rows)
        assert all(row.get("required") is None for row in rows)


def test_timeout_fallback_never_invents_binary_truth_for_major_or_minor_scope():
    server = importlib.import_module("server")
    for scope in (
        "commercial tenant improvement with electrical and plumbing work",
        "minor interior cosmetic touch-up",
    ):
        fallback = server._build_degraded_lookup_fallback(
            scope, "Example City", "EX", reason="test_timeout"
        )
        assert fallback["permit_decision"] == "NEEDS_INPUT"
        assert fallback["permit_required"] is None
        assert fallback["permit_verdict"] == "NEEDS_INPUT"
        assert fallback["permits_required"]
        assert all(row.get("required") is None for row in fallback["permits_required"])


def test_all_thirteen_saved_integrity_sinks_reenter_scope_authority_ladder():
    if not FRESH150.is_dir():
        raise AssertionError("Fresh150 source artifacts are required; replay must not skip")
    server = importlib.import_module("server")

    for case in _fixture()["required_to_unknown"]:
        payload = json.loads((FRESH150 / case["artifact"]).read_text(encoding="utf-8"))
        replay = server.finalize_permit_lookup_result(
            payload,
            case["job_type"],
            case["city"],
            case["state"],
            job_category="commercial" if "-C-" in case["case_id"] else "residential",
        )
        assert replay["permit_decision"] == "NEEDS_INPUT", case["case_id"]
        assert replay["permit_required"] is None, case["case_id"]
        assert replay["decision_source"] == "trigger_specific_scope_floor", case["case_id"]
        assert replay.get("family_decisions"), case["case_id"]
        assert all(
            row.get("status") == "NEEDS_INPUT" and row.get("required") is None
            for row in replay["family_decisions"]
            if isinstance(row, dict)
        ), case["case_id"]


def test_all_eight_saved_conditional_contracts_survive_full_finalization():
    if not FRESH150.is_dir():
        raise AssertionError("Fresh150 source artifacts are required; replay must not skip")
    server = importlib.import_module("server")

    for case in _fixture()["conditional_overclaims"]:
        payload = json.loads((FRESH150 / case["artifact"]).read_text(encoding="utf-8"))
        payload["permit_decision"] = "CONDITIONAL"
        payload["permit_required"] = None
        payload["permit_verdict"] = "CONDITIONAL"
        for key in ("permits_required", "family_decisions", "related_permits", "companion_permits"):
            for row in payload.get(key) or []:
                if isinstance(row, dict):
                    row["status"] = "CONDITIONAL"
                    row["decision"] = "CONDITIONAL"
                    row["required_status"] = "CONDITIONAL"
                    row["required"] = None
        replay = server.finalize_permit_lookup_result(
            payload,
            case["job_type"],
            case["city"],
            case["state"],
            job_category="commercial" if "-C-" in case["case_id"] else "residential",
        )
        assert replay["permit_decision"] == "CONDITIONAL", case["case_id"]
        assert replay["permit_required"] is None, case["case_id"]


def test_saved_nj_false_required_is_repaired_by_sourced_state_exemption():
    if not FRESH150.is_dir():
        raise AssertionError("Fresh150 source artifacts are required; replay must not skip")
    server = importlib.import_module("server")
    case = _fixture()["false_required_exemption"]
    payload = json.loads((FRESH150 / case["artifact"]).read_text(encoding="utf-8"))
    replay = server.finalize_permit_lookup_result(
        payload,
        "Residential roof replacement on a detached one-family home using composition shingles, with underlayment replacement and no structural, solar, or skylight work.",
        "Clinton Township",
        "NJ",
        job_category="residential",
    )
    assert replay["permit_decision"] == "NOT_REQUIRED"
    assert replay["permit_required"] is False
    assert replay["decision_source"] == "nj_ucc_detached_one_two_family_roof_covering_ordinary_maintenance"
    assert replay["code_citation"]["url"] == "https://www.nj.gov/dca/codes/codreg/pdf_regs/njac_5_23_2.pdf"


def test_red_advisory_prose_cannot_author_a_regulated_family():
    result = {
        "permit_decision": "VERIFY",
        "permit_required": None,
        "permit_verdict": "VERIFY",
        "permits_required": [],
        "checklist": ["If the use changes later, ask whether a certificate of occupancy is needed."],
        "pro_tips": ["A future restaurant buildout could need health, fire, plumbing, and zoning approvals."],
    }
    out = ensure_required_filing_rows(
        result,
        "Commercial office furniture replacement only; no change of use, occupancy, walls, MEP, fire, exterior, or signage work.",
        "Example City",
        "EX",
    )
    assert out.get("permits_required") == []
    assert out["permit_decision"] == "VERIFY"
    assert out["permit_required"] is None


def test_red_typed_package_supports_all_five_canonical_statuses():
    expected = {"REQUIRED", "CONDITIONAL", "NOT_REQUIRED", "NEEDS_INPUT", "VERIFY"}
    assert {status.value for status in permit_model.PermitStatus} == expected
    assert permit_manifest.STATUSES == expected


def test_red_claim_source_rejects_url_plus_prose_and_demotes_hard_claim():
    malformed = "https://official.example.gov/rule This page says a permit is required"
    row = permit_manifest._canonical_row(
        {
            "family": "building",
            "permit_type": "Building Permit",
            "status": "REQUIRED",
            "required": True,
            "source_url": malformed,
        },
        primary=False,
    )
    assert row["source_ref"] is None
    assert row["status"] == "VERIFY"
    assert "source_url" not in row


def test_red_one_authority_ladder_and_state_rule_registry_are_runtime_contracts():
    module = importlib.import_module("api.state_rule_packs")
    assert hasattr(module, "resolve_state_rule")
    assert hasattr(module, "StateRule")
    assert hasattr(module, "AuthorityModel")
    engine = importlib.import_module("api.permit_rule_engine")
    assert hasattr(engine, "resolve_decision_authority_ladder")


def test_red_frontend_maybe_never_enters_yes_or_no_homeowner_branch():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "const permitRequired = (verdict === 'yes' || verdict === 'maybe');" not in html
    assert not re.search(r"verdict\s*===\s*['\"]maybe['\"].{0,250}YES — A permit was required", html, re.S)
    assert "CONDITIONAL — a permit may be required" in html
    assert "VERIFY — permit requirement unresolved" in html


def test_red_server_exposes_persistent_idempotency_ledger_contract():
    ledger = importlib.import_module("api.lookup_execution_ledger")
    assert hasattr(ledger, "LookupExecutionLedger")
    assert hasattr(ledger.LookupExecutionLedger, "claim")
    assert hasattr(ledger.LookupExecutionLedger, "complete")
    assert hasattr(ledger.LookupExecutionLedger, "replay")


def test_red_family_scorer_is_status_aware_and_refuses_unadjudicated_gold():
    scorer = importlib.import_module("benchmarks.permit_accuracy_v1_2.family_scorer")
    score = scorer.score_family_status_pairs(
        truth=[("BUILDING", "CONDITIONAL")],
        predicted=[("BUILDING", "REQUIRED")],
        gold_adjudicated=True,
    )
    assert score["false_positives"] == 1
    assert score["true_positives"] == 0
    assert score["conditional_as_hard_overclaims"] == 1
    assert score["hard_false_positives"] == 1
    try:
        scorer.score_family_status_pairs(
            truth=[("BUILDING", "REQUIRED")],
            predicted=[("BUILDING", "REQUIRED")],
            gold_adjudicated=False,
        )
    except scorer.UnadjudicatedGoldError:
        pass
    else:
        raise AssertionError("family score must fail closed until disputed gold is adjudicated")


def test_family_scorer_prefers_structured_rows_and_collapses_strongest_status():
    scorer = importlib.import_module("benchmarks.permit_accuracy_v1_2.family_scorer")
    rows = scorer.extract_customer_family_statuses({
        "permit_decision": "REQUIRED",
        "permit_kind": "Roofing Permit",
        "family_decisions": [
            {"family": "building", "status": "CONDITIONAL"},
            {"family": "building", "status": "REQUIRED"},
            {"family": "electrical", "status": "VERIFY"},
        ],
    })
    assert rows == [("BUILDING", "REQUIRED"), ("ELECTRICAL", "VERIFY")]
    assert ("ROOFING", "REQUIRED") not in rows
