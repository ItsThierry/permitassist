from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "api"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from api import permit_rule_engine as pre  # noqa: E402


FAMILIES = (
    "building",
    "electrical",
    "plumbing",
    "mechanical",
    "fire",
    "health",
    "liquor",
    "wastewater",
    "occupancy",
    "zoning",
)


def _required(family: str) -> pre.CoreFamilyDecision:
    return pre.normalize_family_decision(
        {
            "family": family,
            "verdict": "REQUIRED",
            "trigger": f"{family} work requires authorization when in scope",
            "provenance": [
                {
                    "source_url": f"https://example.gov/{family}",
                    "source_quote": f"A {family} permit is required when {family} work is performed.",
                    "snapshot_hash": (family[0] if family[0] in "abcdef" else "a") * 64,
                    "snapshot_path": f"synthetic://{family}",
                    "publishable": True,
                }
            ],
        }
    )


def _by_family(rows: tuple[pre.CoreFamilyDecision, ...]) -> dict[str, pre.CoreFamilyDecision]:
    return {row.family: row for row in rows}


def test_binary_companions_require_positive_customer_scope() -> None:
    work = pre.normalize_work_atoms(
        "Commercial tenant improvement with fixed non-load-bearing partitions only; "
        "no electrical, plumbing, HVAC, fire alarm, commercial kitchen, liquor, "
        "wastewater, occupancy change, or zoning work.",
        "commercial",
    )

    gated = pre._scope_gate_family_decisions(
        tuple(_required(family) for family in FAMILIES),
        work=work,
        project_family="commercial_tenant_improvement",
    )
    by_family = _by_family(gated)

    assert set(by_family) == set(FAMILIES)
    assert next(
        atom for atom in work.atoms if atom.ontology_node == "structural_alteration"
    ).polarity is pre.WorkPolarity.NEGATED
    assert by_family["building"].verdict is pre.FamilyVerdict.REQUIRED
    for family in set(FAMILIES) - {"building"}:
        assert by_family[family].verdict is pre.FamilyVerdict.VERIFY
        assert "family_scope_not_positively_established" in by_family[family].validation_issue_codes
        assert by_family[family].provenance


def test_positively_described_companions_keep_source_backed_binary_verdicts() -> None:
    work = pre.normalize_work_atoms(
        "Commercial tenant improvement with electrical wiring, plumbing, HVAC, fire alarm, "
        "commercial kitchen food service, liquor service, grease interceptor wastewater, "
        "change of occupancy, and zoning review.",
        "commercial",
    )

    gated = pre._scope_gate_family_decisions(
        tuple(_required(family) for family in FAMILIES),
        work=work,
        project_family="commercial_tenant_improvement",
    )

    assert set(_by_family(gated)) == set(FAMILIES)
    assert all(row.verdict is pre.FamilyVerdict.REQUIRED for row in gated)
    assert all("family_scope_not_positively_established" not in row.validation_issue_codes for row in gated)


def test_closed_world_true_facts_activate_companions_without_keyword_guessing() -> None:
    work = pre.normalize_work_atoms(
        "Commercial tenant improvement with fixed partitions.",
        "commercial",
        facts={"electrical_scope": True, "plumbing_scope": True},
    )

    gated = pre._scope_gate_family_decisions(
        tuple(_required(family) for family in FAMILIES),
        work=work,
        project_family="commercial_tenant_improvement",
    )
    by_family = _by_family(gated)

    assert by_family["electrical"].verdict is pre.FamilyVerdict.REQUIRED
    assert by_family["plumbing"].verdict is pre.FamilyVerdict.REQUIRED
    assert by_family["mechanical"].verdict is pre.FamilyVerdict.VERIFY


def test_negated_list_stops_at_new_affirmative_action() -> None:
    work = pre.normalize_work_atoms(
        "Commercial tenant improvement with no plumbing or HVAC; install new fire alarm devices.",
        "commercial",
    )
    polarity = {atom.ontology_node: atom.polarity for atom in work.atoms}

    assert polarity["plumbing_work"] is pre.WorkPolarity.NEGATED
    assert polarity["mechanical_work"] is pre.WorkPolarity.NEGATED
    assert polarity["fire_life_safety_work"] is pre.WorkPolarity.POSITIVE
