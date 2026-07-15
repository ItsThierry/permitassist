from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import generate_permit_rule_engine_part3_evidence as generator


MANIFEST_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "permit_rule_engine_part3_alias_reconciliation_red_manifest.json"
)


def test_part3_evidence_distinguishes_safe_alias_reconciliation_from_genuine_ambiguity(
    tmp_path: Path,
) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    root = Path(__file__).parents[1]
    for relative_path, expected in manifest["files"].items():
        actual = hashlib.sha256((root / relative_path).read_bytes()).hexdigest()
        assert actual == expected
    assert manifest["frozen_before_evidence_harness_edit"] is True

    summary = generator.generate(
        root,
        tmp_path / "part3",
        source_commit="part3-alias-reconciliation-red-contract",
    )

    assert summary["canonical_alias_reconciliation_count"] == 10
    assert summary["unexpected_projection_transition_count"] == 0
    assert summary["genuine_ambiguity_counterfactual_passed"] is True
    assert summary["customer_projection_violation_count"] == 0
    assert summary["counterfactual_pass_count"] == summary["counterfactual_count"]
    assert summary["passed"] is True
