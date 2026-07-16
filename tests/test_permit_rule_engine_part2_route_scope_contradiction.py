from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from api import permit_rule_engine as pre


MANIFEST_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "permit_rule_engine_part2_route_scope_contradiction_red_manifest.json"
)


@pytest.fixture(scope="module", autouse=True)
def frozen_route_scope_contradiction_contract() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    root = Path(__file__).parents[1]
    for relative_path, expected in manifest["files"].items():
        actual = hashlib.sha256((root / relative_path).read_bytes()).hexdigest()
        assert actual == expected
    assert manifest["frozen_before_behavior_edit"] is True
    assert manifest["baseline_commit"] == "2fe43a4dae08d50373f17b02b7112f0abf7d3f5a"


def _route(source_url: str, source_quote: str) -> pre.ApplicationRoute:
    provenance = pre.ProvenanceRecord(
        source_url=source_url,
        source_quote=source_quote,
        retrieved_at="2026-07-15T00:00:00Z",
        snapshot_hash="a" * 64,
        snapshot_path="synthetic://route-scope-contradiction-contract",
        effective_date=None,
        freshness_class="current",
        last_verified_at="2026-07-15T00:00:00Z",
        publishable=True,
    )
    return pre.ApplicationRoute(
        permit_name="Commercial Building Permit",
        office_name="Example Building Department",
        apply_url=source_url,
        channel="online",
        provenance=(provenance,),
    )


@pytest.mark.parametrize(
    ("source_url", "source_quote"),
    (
        (
            "https://example.gov/residential/permits",
            "Residential permits only; for commercial projects contact the county.",
        ),
        (
            "https://example.gov/building/permits",
            "This online route accepts residential permit applications only. Commercial permits are handled by the county.",
        ),
        (
            "https://example.gov/residential/apply",
            "This page is not for commercial work; use the county commercial building portal.",
        ),
    ),
)
def test_residential_route_with_commercial_contradiction_fails_closed(
    source_url: str,
    source_quote: str,
) -> None:
    assert pre._route_scope_mismatch(
        _route(source_url, source_quote),
        "commercial_tenant_improvement",
    ) is True


def test_affirmative_residential_and_commercial_scope_remains_accepted() -> None:
    route = _route(
        "https://example.gov/building/permits",
        "For both residential and commercial projects, apply for building permits through this portal.",
    )
    assert pre._route_scope_mismatch(route, "commercial_tenant_improvement") is False
