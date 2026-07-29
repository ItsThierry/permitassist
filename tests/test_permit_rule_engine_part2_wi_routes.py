from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from api import permit_rule_engine as pre
from api.v24_decision_cells import V24Resolution, V24ResolutionStatus, load_v24_index


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "permit_rule_engine_part2_wi_commercial_ti_route_red.json"
)
MANIFEST_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "permit_rule_engine_part2_wi_commercial_ti_route_red_manifest.json"
)


def _canonical_cell_sha256(cell: dict) -> str:
    payload = json.dumps(
        cell,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture(scope="module")
def frozen_wi_route_contract() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    root = Path(__file__).parents[1]
    for relative_path, expected in manifest["files"].items():
        actual = hashlib.sha256((root / relative_path).read_bytes()).hexdigest()
        assert actual == expected
    assert manifest["frozen_before_behavior_edits"] is True

    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert fixture["frozen_before_behavior_edits"] is True
    assert fixture["baseline_commit"] == "54f46f343665b9b45fc1fb7b23b98e5714cb487f"
    assert len(fixture["cases"]) == 10
    assert len(fixture["expected_blocker_rows"]) == 10
    return fixture


def test_equivalent_separator_aliases_deduplicate_but_distinct_ids_stay_ambiguous() -> None:
    equivalent_index = {
        "EX|spring_field|commercial_tenant_improvement": {
            "cell_id": "spring-field-canonical",
            "jurisdiction_id": "us-ex-spring-field",
            "ahj": "Spring Field",
            "state": "EX",
            "project_family": "commercial_tenant_improvement",
        },
        "EX|us_ex_spring_field|commercial_tenant_improvement": {
            "cell_id": "spring-field-separator-alias",
            "jurisdiction_id": "us-ex-spring_field",
            "ahj": "Spring Field",
            "state": "EX",
            "project_family": "commercial_tenant_improvement",
        },
    }

    exact = pre.resolve_jurisdiction_identity(
        "Spring Field",
        "EX",
        index=equivalent_index,
    )
    assert exact.status is pre.JurisdictionResolutionStatus.EXACT
    assert exact.selected is not None
    assert exact.selected.jurisdiction_id == "us-ex-spring-field"
    assert exact.selected.cell_ids == (
        "spring-field-canonical",
        "spring-field-separator-alias",
    )

    genuinely_distinct_index = {
        **equivalent_index,
        "EX|spring_field_county|commercial_tenant_improvement": {
            "cell_id": "spring-field-county-authority",
            "jurisdiction_id": "us-ex-spring-field-county",
            "ahj": "Spring Field",
            "state": "EX",
            "project_family": "commercial_tenant_improvement",
        },
    }
    ambiguous = pre.resolve_jurisdiction_identity(
        "Spring Field",
        "EX",
        index=genuinely_distinct_index,
    )
    assert ambiguous.status is pre.JurisdictionResolutionStatus.AMBIGUOUS
    assert ambiguous.selected is None
    assert tuple(candidate.jurisdiction_id for candidate in ambiguous.candidates) == (
        "us-ex-spring-field",
        "us-ex-spring-field-county",
    )


def test_all_frozen_wi_commercial_ti_cells_keep_source_backed_family_routes(
    frozen_wi_route_contract: dict,
) -> None:
    index = load_v24_index()
    assert index is not None

    observed_rows: list[str] = []
    fail_closed_routes: list[str] = []
    for case in frozen_wi_route_contract["cases"]:
        cell_key = case["cell_key"]
        cell = index[cell_key]
        assert _canonical_cell_sha256(cell) == case["expected_source_cell_sha256"]

        identity = pre.resolve_jurisdiction_identity(
            case["ahj"],
            case["state"],
            index=index,
        )
        assert identity.status is pre.JurisdictionResolutionStatus.EXACT, cell_key
        assert identity.selected is not None
        assert (
            identity.selected.jurisdiction_id
            == case["expected_canonical_jurisdiction_id"]
        )

        resolution = V24Resolution(
            V24ResolutionStatus.EXACT_CELL_PUBLISHABLE,
            cell=cell,
            key=cell_key,
            reason="frozen-wi-commercial-ti-route-contract",
        )
        envelope = pre.build_core_decision_envelope(
            resolution,
            job_type="Commercial tenant improvement",
            city=case["ahj"],
            state=case["state"],
            job_category="commercial",
        )
        assert envelope.jurisdiction.status is pre.JurisdictionResolutionStatus.EXACT
        assert envelope.jurisdiction.selected is not None
        assert (
            envelope.jurisdiction.selected.jurisdiction_id
            == case["expected_canonical_jurisdiction_id"]
        )
        assert envelope.precedence_stage not in {
            pre.PrecedenceStage.EXACT_FAIL_CLOSED,
            pre.PrecedenceStage.INTERNAL_ABSTAIN,
        }

        expected_families = set(case["expected_families"])
        routed_families = {route.family for route in envelope.family_routes}
        assert routed_families == expected_families, cell_key
        for route in envelope.family_routes:
            assert route.authority.family == route.family
            assert route.authority.issuing_authority
            assert route.authority.application_authority
            assert route.application_route.office_name
            if route.application_route.apply_url:
                assert route.application_route.apply_url.startswith("https://")
                assert route.application_route.provenance
                assert any(
                    pre._publishable_provenance(record)
                    for record in route.application_route.provenance
                )
                assert any(
                    record.source_url in case["official_source_urls"]
                    for record in route.application_route.provenance
                )
            else:
                assert route.application_route.channel == "verify"
                assert not route.application_route.provenance
                assert "application_route_role_not_proven" in (
                    route.application_route.validation_issue_codes
                )
                fail_closed_routes.append(f"{cell_key}|{route.family}")

        public_projection = json.loads(envelope.sealed_projection.payload_json)
        public_routes = {
            row["family"]: row["application_route"]
            for row in public_projection["family_authority_routes"]
        }
        assert set(public_routes) == expected_families
        assert all(
            row["apply_url"].startswith("https://")
            or (
                not row["apply_url"]
                and row["channel"] == "verify"
                and "application_route_role_not_proven"
                in row["validation_issue_codes"]
            )
            for row in public_routes.values()
        )
        observed_rows.append(f"{cell_key}|{','.join(sorted(expected_families))}")

    assert observed_rows == frozen_wi_route_contract["expected_blocker_rows"]
    assert fail_closed_routes == [
        "WI|us_wi_fond_du_lac|commercial_tenant_improvement|building",
        "WI|us_wi_west_allis|commercial_tenant_improvement|electrical",
        "WI|us_wi_west_allis|commercial_tenant_improvement|occupancy",
        "WI|us_wi_west_allis|commercial_tenant_improvement|plumbing",
    ]
