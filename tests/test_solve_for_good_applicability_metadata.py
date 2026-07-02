from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))
ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_fable5_customer_pov_20260702T121009Z"

from closed_world_decision import FAMILY_APPLICABILITY_METADATA, canonical_family, family_metadata_for  # noqa: E402


def test_family_applicability_metadata_has_required_fields():
    assert FAMILY_APPLICABILITY_METADATA
    for family, meta in FAMILY_APPLICABILITY_METADATA.items():
        assert meta["applies_when"], family
        assert meta["occupancy_scope"] in {"any", "residential", "commercial"}, family
        assert "lead_eligible_for" in meta, family
        assert meta["provenance"].startswith("family_level_bulk_backfill"), family


def test_100_percent_live100_appearing_families_are_tagged():
    missing = []
    for line in (ARTIFACT_ROOT / "cases.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        body = rec.get("response_body") or {}
        for key in ["permits_required", "conditional_permits", "related_permits"]:
            for row in body.get(key) or []:
                if not isinstance(row, dict):
                    continue
                family = row.get("family") or row.get("filing_family") or row.get("permit_family")
                name = row.get("permit_name") or row.get("permit_type")
                if family_metadata_for(family, name) is None:
                    missing.append((rec["case"]["id"], key, family, name, canonical_family(family, name)))
    assert not missing
