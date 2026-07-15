from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from api import permit_rule_engine as pre


MANIFEST_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "permit_rule_engine_part2_route_scope_red_manifest.json"
)


@pytest.fixture(scope="module", autouse=True)
def frozen_route_scope_red_contract() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    root = Path(__file__).parents[1]
    for relative_path, expected in manifest["files"].items():
        actual = hashlib.sha256((root / relative_path).read_bytes()).hexdigest()
        assert actual == expected
    assert manifest["frozen_before_route_scope_behavior_edit"] is True
    assert manifest["baseline_commit"] == "3dc8a78512ab782378ab61b97b76c25f5a1faa02"


def _route(source_url: str, source_quote: str) -> pre.ApplicationRoute:
    provenance = pre.ProvenanceRecord(
        source_url=source_url,
        source_quote=source_quote,
        retrieved_at="2026-07-15T00:00:00Z",
        snapshot_hash="a" * 64,
        snapshot_path="synthetic://route-scope-contract",
        effective_date=None,
        freshness_class="current",
        last_verified_at="2026-07-15T00:00:00Z",
        publishable=True,
    )
    return pre.ApplicationRoute(
        permit_name="Commercial Building Permit",
        office_name="Example Building Department",
        apply_url="https://example.gov/permits/commercial",
        channel="online",
        provenance=(provenance,),
    )


def test_mixed_residential_and_commercial_official_scope_is_not_misclassified() -> None:
    route = _route(
        "https://example.gov/building-permits",
        (
            "For both residential and commercial projects, building plans are required "
            "for alterations and improvements."
        ),
    )
    assert pre._route_scope_mismatch(route, "commercial_tenant_improvement") is False


def test_residential_only_route_still_fails_closed_for_commercial_scope() -> None:
    route = _route(
        "https://example.gov/residential/permits",
        "Residential building permits apply to new homes and residential alterations.",
    )
    assert pre._route_scope_mismatch(route, "commercial_tenant_improvement") is True
    assert pre._route_scope_mismatch(route, "residential_remodel") is False


def test_commercial_only_and_empty_route_provenance_are_not_false_mismatches() -> None:
    commercial = _route(
        "https://example.gov/commercial/permits",
        "Commercial tenant improvements require a building permit.",
    )
    empty = pre.ApplicationRoute(
        permit_name="",
        office_name="Example Building Department",
        apply_url="",
        channel="verify",
        provenance=(),
    )
    assert pre._route_scope_mismatch(commercial, "commercial_tenant_improvement") is False
    assert pre._route_scope_mismatch(empty, "commercial_tenant_improvement") is False
