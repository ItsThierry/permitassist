from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from api import permit_rule_engine as pre
from api.v24_decision_cells import (
    V24ResolutionStatus,
    resolve_v24_cell,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "permit_rule_engine_part2_red_no_neuter.json"
MANIFEST_PATH = Path(__file__).parent / "fixtures" / "permit_rule_engine_part2_red_no_neuter_manifest.json"


@pytest.fixture(scope="module")
def frozen_fixture() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    root = Path(__file__).parents[1]
    for relative_path, expected in manifest["files"].items():
        actual = hashlib.sha256((root / relative_path).read_bytes()).hexdigest()
        assert actual == expected
    assert manifest["frozen_before_behavior_edits"] is False
    assert manifest["contract_correction"] == "five_status_customer_projection_migration_20260730"
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_part2_versions_are_explicit_and_independent_of_part1() -> None:
    assert pre.CORE_ENVELOPE_SCHEMA_VERSION == "permitassist.decision-envelope.v2"
    assert pre.CORE_PROJECTION_SCHEMA_VERSION == "permitassist.customer-decision-projection.v1"
    assert pre.CORE_CACHE_SCHEMA_VERSION == "permitassist.rule-engine-cache.v1"
    assert pre.ENVELOPE_SCHEMA_VERSION == "permitassist.decision-envelope.v1"


def test_core_activation_is_disabled_by_default_and_requires_exact_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PERMITASSIST_RULE_ENGINE_CORE", raising=False)
    monkeypatch.delenv("PERMITASSIST_RULE_ENGINE_CORE_ALLOWLIST", raising=False)
    assert pre.core_activation_allowed("us-az-buckeye") is False

    monkeypatch.setenv("PERMITASSIST_RULE_ENGINE_CORE", "active")
    assert pre.core_activation_allowed("us-az-buckeye") is False
    monkeypatch.setenv("PERMITASSIST_RULE_ENGINE_CORE_ALLOWLIST", "us-az-phoenix, us-az-buckeye ")
    assert pre.core_activation_allowed("us-az-buckeye") is True
    assert pre.core_activation_allowed("us-az-buck") is False
    assert pre.core_activation_allowed("") is False


def test_jurisdiction_identity_is_stable_deduplicated_and_sorted(frozen_fixture: dict) -> None:
    case = frozen_fixture["cases"]["exact_identity"]
    first = pre.resolve_jurisdiction_identity(case["city"], case["state"], index=case["index"])
    second = pre.resolve_jurisdiction_identity(case["city"].upper(), case["state"].lower(), index=dict(reversed(list(case["index"].items()))))

    assert first.status is pre.JurisdictionResolutionStatus.EXACT
    assert first == second
    assert len(first.candidates) == 1
    assert first.candidates[0].jurisdiction_id == "us-ex-exampleville"
    assert first.candidates[0].cell_ids == (
        "cell-exampleville-remodel",
        "cell-exampleville-reroof",
    )


def test_jurisdiction_identity_abstains_on_multiple_stable_ids(frozen_fixture: dict) -> None:
    case = frozen_fixture["cases"]["ambiguous_identity"]
    resolved = pre.resolve_jurisdiction_identity(case["city"], case["state"], index=case["index"])

    assert resolved.status is pre.JurisdictionResolutionStatus.AMBIGUOUS
    assert tuple(c.jurisdiction_id for c in resolved.candidates) == (
        "us-ex-shared-city",
        "us-ex-shared-county",
    )
    assert resolved.selected is None


def test_closed_ontology_normalizes_atoms_and_rejects_unknown_scope() -> None:
    atoms = pre.normalize_work_atoms(
        "Residential reroof: replace asphalt shingles; no structural framing changes; 2,400 sq ft",
        "residential",
        facts={"occupancy_change": False, "structural_change": False},
    )
    assert atoms.valid is True
    assert [atom.ontology_node for atom in atoms.atoms] == ["roof_covering_replacement"]
    assert atoms.atoms[0].polarity is pre.WorkPolarity.POSITIVE
    assert atoms.atoms[0].thresholds[0].name == "area_sq_ft"
    assert atoms.atoms[0].thresholds[0].value == 2400

    unknown = pre.normalize_work_atoms("Install a quantum flux widget", "other", facts={"made_up_fact": True})
    assert unknown.valid is False
    assert "unknown_work_atom" in unknown.issue_codes
    assert "unknown_fact_key" in unknown.issue_codes


def test_negated_atoms_cannot_trigger_binary_scope() -> None:
    normalized = pre.normalize_work_atoms(
        "No roof replacement and no structural alteration; inspection only",
        "residential",
    )
    assert normalized.valid is False
    assert normalized.positive_atoms == ()
    assert all(atom.polarity is pre.WorkPolarity.NEGATED for atom in normalized.atoms)
    assert "no_positive_work_atom" in normalized.issue_codes


def test_per_family_authority_routes_do_not_collapse_to_first_route() -> None:
    cell = {
        "tier1": {
            "trade_authority": [
                {
                    "permit_family": "building",
                    "issuing_authority": "Exampleville Building Department",
                    "application_authority": "Exampleville Permit Center",
                    "application_channel": "online",
                    "routing_note": "Select Building Permit.",
                    "provenance": [{"source_url": "https://example.gov/building"}],
                },
                {
                    "permit_family": "electrical",
                    "issuing_authority": "State Electrical Board",
                    "application_authority": "State Trade Portal",
                    "application_channel": "online",
                    "routing_note": "File separately with the state.",
                    "provenance": [{"source_url": "https://example.gov/electrical"}],
                },
            ],
            "apply": [
                {
                    "permit_name": "Building Permit",
                    "office_name": "Exampleville Permit Center",
                    "apply_url": "https://example.gov/building/apply",
                    "channel": "online",
                    "provenance": [{
                        "source_url": "https://example.gov/building/apply",
                        "source_quote": "Apply online for a Building Permit.",
                        "snapshot_path": "tests/fixtures/permit_rule_engine_part2_red_no_neuter.json",
                        "snapshot_hash": "6ed10204a54ae37469b79a9b969ad95769cdbe101d1b00c3cada59c157c5991a",
                        "publishable": True,
                    }],
                },
                {
                    "permit_name": "Electrical Permit",
                    "office_name": "State Trade Portal",
                    "apply_url": "https://example.gov/electrical/apply",
                    "channel": "online",
                    "provenance": [{
                        "source_url": "https://example.gov/electrical/apply",
                        "source_quote": "Apply online for an Electrical Permit.",
                        "snapshot_path": "tests/fixtures/permit_rule_engine_part2_red_no_neuter.json",
                        "snapshot_hash": "6ed10204a54ae37469b79a9b969ad95769cdbe101d1b00c3cada59c157c5991a",
                        "publishable": True,
                    }],
                },
            ],
        }
    }
    routes = pre.build_family_authority_routes(cell)
    assert [route.family for route in routes] == ["building", "electrical"]
    assert routes[0].application_route.office_name == "Exampleville Permit Center"
    assert routes[1].application_route.office_name == "State Trade Portal"
    assert routes[0].application_route.apply_url != routes[1].application_route.apply_url


def test_exact_precedence_funnel_order_is_closed() -> None:
    assert pre.select_precedence_stage(V24ResolutionStatus.EXACT_CELL_PUBLISHABLE, "complete", False) is pre.PrecedenceStage.VALIDATED_EXACT_COMPLETE
    assert pre.select_precedence_stage(V24ResolutionStatus.EXACT_CELL_PUBLISHABLE, "partial", False) is pre.PrecedenceStage.VALIDATED_EXACT_PARTIAL
    assert pre.select_precedence_stage(V24ResolutionStatus.EXACT_CELL_FAIL_CLOSED, "fail_closed", False) is pre.PrecedenceStage.EXACT_FAIL_CLOSED
    assert pre.select_precedence_stage(V24ResolutionStatus.AHJ_NOT_COVERED, "none", True) is pre.PrecedenceStage.QUERY_OFFICIAL_EVIDENCE
    assert pre.select_precedence_stage(V24ResolutionStatus.AHJ_NOT_COVERED, "none", False) is pre.PrecedenceStage.INTERNAL_ABSTAIN
    assert tuple(stage.value for stage in pre.PrecedenceStage) == (
        "validated_exact_complete",
        "validated_exact_partial",
        "exact_fail_closed",
        "query_official_evidence",
        "internal_abstain",
    )


def test_source_floor_demotes_source_free_binary_but_preserves_affirmative_exemption(frozen_fixture: dict) -> None:
    source_floor = frozen_fixture["cases"]["source_floor"]
    source_free = pre.normalize_family_decision(source_floor["source_free_required"])
    exemption = pre.normalize_family_decision(source_floor["source_backed_exemption"])

    assert source_free.verdict is pre.DecisionVerdict.VERIFY
    assert "binary_without_publishable_provenance" in source_free.validation_issue_codes
    assert exemption.verdict is pre.DecisionVerdict.NOT_REQUIRED
    assert exemption.validation_issue_codes == ()


def test_no_neuter_projection_preserves_all_lanes_and_weak_statuses(frozen_fixture: dict) -> None:
    case = frozen_fixture["cases"]["no_neuter_ten_lane"]
    decisions = []
    for family in case["families"]:
        verdict = "REQUIRED"
        if family == "health":
            verdict = "CONDITIONAL"
        elif family == "liquor":
            verdict = "VERIFY"
        elif family == "wastewater":
            verdict = "ABSTAIN"
        decisions.append(
            pre.normalize_family_decision(
                {
                    "family": family,
                    "verdict": verdict,
                    "trigger": f"{family} scope review",
                    "provenance": [] if verdict not in {"REQUIRED", "NOT_REQUIRED"} else [{
                        "source_url": f"https://example.gov/{family}",
                        "source_quote": f"A {family} permit is required for this scope.",
                        "snapshot_hash": "b" * 64,
                        "snapshot_path": f"synthetic://{family}",
                        "publishable": True,
                    }],
                }
            )
        )

    payload = pre.build_sealed_projection_payload(
        jurisdiction_id="us-ex-exampleville",
        jurisdiction_name="Exampleville",
        state="EX",
        project_family=case["project_family"],
        main_decision=decisions[0],
        family_decisions=tuple(decisions),
        family_routes=(),
        coverage_status="partial",
        coverage_reason="weak lanes remain visible for verification",
        source_cell_id="synthetic-ten-lane",
    )
    rows = payload["family_decisions"]
    assert [row["family"] for row in rows] == case["families"]
    assert [row["verdict"] for row in rows if row["family"] in case["weak_families"]] == case["expected_weak_verdicts"]
    assert len(rows) == 10
    assert case["forbidden_maps_url"] not in json.dumps(payload, sort_keys=True)


def _build_buckeye_envelope() -> object:
    resolution = resolve_v24_cell("Buckeye", "AZ", "residential reroof", "residential", force=True)
    assert resolution.status is V24ResolutionStatus.EXACT_CELL_PUBLISHABLE
    return pre.build_core_decision_envelope(
        resolution,
        job_type="residential reroof",
        city="Buckeye",
        state="AZ",
        job_category="residential",
    )


def test_envelope_projection_is_canonical_hash_sealed_and_tamper_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERMITASSIST_RULE_ENGINE_CORE", "active")
    monkeypatch.setenv("PERMITASSIST_RULE_ENGINE_CORE_ALLOWLIST", "us-az-buckeye")
    envelope = _build_buckeye_envelope()
    wrapped = pre.attach_core_decision_envelope({"permit_decision": "NOT_REQUIRED", "poison": "legacy"}, envelope)
    projected = pre.extract_sealed_public_projection(wrapped, city="Buckeye", state="AZ")

    assert projected == json.loads(envelope.sealed_projection.payload_json)
    assert envelope.sealed_projection.payload_sha256 == hashlib.sha256(envelope.sealed_projection.payload_json.encode("utf-8")).hexdigest()
    projected["permit_name"] = "mutated copy"
    assert json.loads(envelope.sealed_projection.payload_json)["permit_name"] != "mutated copy"

    tampered = copy.deepcopy(wrapped)
    tampered["_permit_rule_engine_core"]["sealed_projection"]["payload_json"] = "{}"
    assert pre.extract_sealed_public_projection(tampered, city="Buckeye", state="AZ") is None


def test_cache_version_rejects_legacy_stale_and_tampered_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERMITASSIST_RULE_ENGINE_CORE", "active")
    monkeypatch.setenv("PERMITASSIST_RULE_ENGINE_CORE_ALLOWLIST", "us-az-buckeye")
    envelope = _build_buckeye_envelope()
    wrapped = pre.attach_core_decision_envelope({}, envelope)

    assert pre.validate_rule_engine_cache_payload(wrapped, required_version=pre.CORE_CACHE_SCHEMA_VERSION) is True
    assert pre.validate_rule_engine_cache_payload({}, required_version=pre.CORE_CACHE_SCHEMA_VERSION) is False
    stale = copy.deepcopy(wrapped)
    stale["_permit_rule_engine_cache_schema_version"] = "permitassist.rule-engine-cache.stale"
    assert pre.validate_rule_engine_cache_payload(stale, required_version=pre.CORE_CACHE_SCHEMA_VERSION) is False
    corrupted = copy.deepcopy(wrapped)
    corrupted["_permit_rule_engine_core"]["envelope_sha256"] = "0" * 64
    assert pre.validate_rule_engine_cache_payload(corrupted, required_version=pre.CORE_CACHE_SCHEMA_VERSION) is False


def test_flag_off_attachment_is_exact_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PERMITASSIST_RULE_ENGINE_CORE", raising=False)
    monkeypatch.delenv("PERMITASSIST_RULE_ENGINE_CORE_ALLOWLIST", raising=False)
    original = {"permit_decision": "REQUIRED", "nested": {"rows": [1, 2, 3]}}
    before = json.dumps(original, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")
    after_obj = pre.maybe_attach_core_decision_envelope(
        original,
        job_type="residential reroof",
        city="Buckeye",
        state="AZ",
        job_category="residential",
    )
    after = json.dumps(after_obj, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")
    assert after_obj is original
    assert after == before


def test_api_share_report_and_checklist_use_same_sealed_projection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PERMITASSIST_RULE_ENGINE_CORE", "active")
    monkeypatch.setenv("PERMITASSIST_RULE_ENGINE_CORE_ALLOWLIST", "us-az-buckeye")
    envelope = _build_buckeye_envelope()
    wrapped = pre.attach_core_decision_envelope(
        {
            "permit_decision": "NOT_REQUIRED",
            "permit_name": "POISON LEGACY PERMIT",
            "applying_office": "POISON LEGACY OFFICE",
            "inspections": ["POISON LEGACY INSPECTION"],
        },
        envelope,
    )
    sealed = json.loads(envelope.sealed_projection.payload_json)

    from api import server

    monkeypatch.setattr(server, "CACHE_DB", str(tmp_path / "cache.db"))
    api_view = server.build_customer_permit_view_model(wrapped, "residential reroof", "Buckeye", "AZ", "residential")
    share_view = server._sanitize_customer_result_for_request_scope(wrapped, "residential reroof", "Buckeye", "AZ")
    report_html = server.render_white_label_report_html({
        "result": wrapped,
        "job_type": "residential reroof",
        "city": "Buckeye",
        "state": "AZ",
    })
    checklist = server.get_or_create_checklist(wrapped, "residential reroof", "Buckeye", "AZ")

    assert api_view == sealed
    assert share_view == sealed
    assert sealed["permit_name"] in report_html
    assert "POISON LEGACY PERMIT" not in report_html
    assert "POISON LEGACY OFFICE" not in report_html
    assert "POISON LEGACY INSPECTION" not in json.dumps(checklist, sort_keys=True)
    assert "decision_projection_sha256" not in checklist
