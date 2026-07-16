from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from api import permit_rule_engine as pre


MANIFEST_PATH = Path(__file__).parent / "fixtures" / "permit_rule_engine_part2_route_scope_residual_red_manifest.json"


@pytest.fixture(scope="module", autouse=True)
def _verify_frozen_contract() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text())
    assert manifest["baseline_commit"] == "6a2316cb0ec46c4a0d41d6c0c7c6e3864e4c3516"
    assert manifest["frozen_before_behavior_edit"] is True
    test_path = Path(__file__)
    assert hashlib.sha256(test_path.read_bytes()).hexdigest() == manifest["files"][test_path.name]


def _route(source_quote: str) -> pre.ApplicationRoute:
    provenance = pre.ProvenanceRecord(
        source_url="https://example.gov/building/permits",
        source_quote=source_quote,
        retrieved_at="2026-07-15T00:00:00Z",
        snapshot_hash="c" * 64,
        snapshot_path="synthetic://reviewer-residual-route-scope",
        effective_date=None,
        freshness_class="current",
        last_verified_at="2026-07-15T00:00:00Z",
        publishable=True,
    )
    return pre.ApplicationRoute(
        permit_name="Commercial Building Permit",
        office_name="Example City Building Department",
        apply_url="https://example.gov/building/permits",
        channel="online",
        provenance=(provenance,),
    )


@pytest.mark.parametrize(
    "source_quote",
    (
        "Use this portal for residential permits; commercial projects are handled by the county.",
        "Residential permits are accepted here; no commercial permits are issued.",
        "Residential permits are accepted here; this office excludes commercial projects.",
        "Residential permits are accepted here; commercial projects go through a separate county portal.",
    ),
)
def test_residual_commercial_route_contradictions_fail_closed(source_quote: str) -> None:
    assert pre._route_scope_mismatch(
        _route(source_quote),
        "commercial_tenant_improvement",
    ) is True


@pytest.mark.parametrize(
    "source_quote,office_name",
    (
        (
            "Residential and commercial permits are processed by the City Building Department through this portal.",
            "Example City Building Department",
        ),
        (
            "Residential and commercial permits are handled by the County Building Department through this portal.",
            "Example County Building Department",
        ),
        (
            "This page provides both residential and commercial permit applications.",
            "Example City Building Department",
        ),
    ),
)
def test_affirmative_same_authority_commercial_scope_remains_accepted(
    source_quote: str,
    office_name: str,
) -> None:
    route = _route(source_quote)
    route = pre.ApplicationRoute(
        permit_name=route.permit_name,
        office_name=office_name,
        apply_url=route.apply_url,
        channel=route.channel,
        provenance=route.provenance,
    )
    assert pre._route_scope_mismatch(route, "commercial_tenant_improvement") is False
