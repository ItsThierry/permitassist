from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from api import permit_rule_engine as pre
from api.v24_decision_cells import V24Resolution, V24ResolutionStatus, load_v24_index


MANIFEST_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "permit_rule_engine_part2_jurisdiction_id_canonicalization_red_manifest.json"
)


@pytest.fixture(scope="module", autouse=True)
def frozen_supplemental_red_contract() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    root = Path(__file__).parents[1]
    for relative_path, expected in manifest["files"].items():
        actual = hashlib.sha256((root / relative_path).read_bytes()).hexdigest()
        assert actual == expected
    assert manifest["frozen_before_behavior_edits"] is True
    assert manifest["baseline_red_checkpoint"] == "04f970c23b31d171a32d998af673601c8a506349"


def test_jurisdiction_id_canonicalizer_is_narrow_idempotent_and_separator_only() -> None:
    canonicalize = pre._canonical_jurisdiction_id
    equivalents = (
        "us-wi-eau-claire",
        "US-WI-EAU_CLAIRE",
        " us wi eau__claire ",
        "--us---wi---eau-claire--",
    )
    assert {canonicalize(value) for value in equivalents} == {"us-wi-eau-claire"}
    assert canonicalize(canonicalize("US-WI-EAU_CLAIRE")) == "us-wi-eau-claire"
    assert canonicalize("") == ""
    assert canonicalize(None) == ""
    assert canonicalize("us-ex-springfield") != canonicalize("us-ex-spring-field")
    assert canonicalize("us.ex.springfield") != canonicalize("us-ex-springfield")


def test_alias_merge_is_order_independent_and_token_distinctions_remain_ambiguous() -> None:
    equivalent = {
        "EX|a|commercial_tenant_improvement": {
            "cell_id": "alias-a",
            "jurisdiction_id": "us-ex-spring_field",
            "ahj": "Spring Field",
            "state": "EX",
            "county": "Example County",
            "project_family": "commercial_tenant_improvement",
        },
        "EX|b|commercial_tenant_improvement": {
            "cell_id": "alias-b",
            "jurisdiction_id": "us_ex_spring-field",
            "ahj": "Spring Field",
            "state": "EX",
            "county": "Example County",
            "project_family": "commercial_tenant_improvement",
        },
    }
    first = pre.resolve_jurisdiction_identity("Spring Field", "EX", index=equivalent)
    second = pre.resolve_jurisdiction_identity(
        "SPRING FIELD",
        "ex",
        index=dict(reversed(list(equivalent.items()))),
    )
    assert first == second
    assert first.status is pre.JurisdictionResolutionStatus.EXACT
    assert first.selected is not None
    assert first.selected.jurisdiction_id == "us-ex-spring-field"
    assert first.selected.cell_ids == ("alias-a", "alias-b")

    token_distinct = {
        **equivalent,
        "EX|c|commercial_tenant_improvement": {
            "cell_id": "distinct-c",
            "jurisdiction_id": "us-ex-springfield",
            "ahj": "Spring Field",
            "state": "EX",
            "county": "Example County",
            "project_family": "commercial_tenant_improvement",
        },
    }
    ambiguous = pre.resolve_jurisdiction_identity(
        "Spring Field",
        "EX",
        index=token_distinct,
    )
    assert ambiguous.status is pre.JurisdictionResolutionStatus.AMBIGUOUS
    assert ambiguous.selected is None
    assert tuple(candidate.jurisdiction_id for candidate in ambiguous.candidates) == (
        "us-ex-spring-field",
        "us-ex-springfield",
    )


def test_core_allowlist_uses_the_same_canonical_jurisdiction_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(pre.CORE_SETTING, "active")
    monkeypatch.setenv(pre.CORE_ALLOWLIST_SETTING, " US_WI_EAU_CLAIRE ")
    assert pre.core_activation_allowed("us-wi-eau-claire") is True
    assert pre.core_activation_allowed("us_wi_eau_claire") is True
    assert pre.core_activation_allowed("us-wi-eau-claire-county") is False
    assert pre.core_activation_allowed("us.wi.eau.claire") is False


def test_official_query_jurisdiction_comparison_uses_canonical_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = load_v24_index()
    assert index is not None
    source = copy.deepcopy(index["WI|eau_claire|commercial_tenant_improvement"])
    source["cell_id"] = "synthetic-separator-comparison"
    source["jurisdiction_id"] = "us-ex-spring_field"
    source["ahj"] = "Spring Field"
    source["state"] = "EX"

    resolution = V24Resolution(
        V24ResolutionStatus.AHJ_NOT_COVERED,
        cell=source,
        key="EX|spring_field|commercial_tenant_improvement",
        reason="synthetic-official-query-separator-contract",
    )
    evidence = pre.OfficialQueryEvidence(
        query="site:example.gov commercial tenant improvement permit",
        jurisdiction_id="us-ex-spring-field",
        source_url="https://example.gov/permits/commercial",
        source_quote="A commercial building permit is required for tenant improvements.",
        snapshot_hash="a" * 64,
        checked_at="2026-07-15T00:00:00Z",
        publishable=True,
        family="building",
        verdict=pre.FamilyVerdict.REQUIRED,
        snapshot_path="synthetic://official-query-separator-contract",
    )
    monkeypatch.setenv(pre.OFFICIAL_QUERY_EVIDENCE_SETTING, "active")

    envelope = pre.build_core_decision_envelope(
        resolution,
        job_type="Commercial tenant improvement",
        city="Spring Field",
        state="EX",
        job_category="commercial",
        official_query_evidence=evidence,
    )
    assert envelope.jurisdiction.status is pre.JurisdictionResolutionStatus.EXACT
    assert envelope.jurisdiction.selected is not None
    assert envelope.jurisdiction.selected.jurisdiction_id == "us-ex-spring-field"
    assert envelope.precedence_stage is pre.PrecedenceStage.QUERY_OFFICIAL_EVIDENCE
    assert envelope.main_decision.verdict is pre.FamilyVerdict.REQUIRED
