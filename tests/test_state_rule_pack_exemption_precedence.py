import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.permit_rule_engine import resolve_decision_authority_ladder  # noqa: E402
from api.state_rule_packs import AuthorityModel, StateRule  # noqa: E402


def test_nj_detached_one_two_family_roof_covering_exemption_is_statewide_and_sourced():
    result = resolve_decision_authority_ladder(
        job_type=(
            "Detached single-family asphalt-shingle roof-covering replacement "
            "with no structural framing, solar, skylight, or change-of-use work."
        ),
        city="Any New Jersey municipality",
        state="NJ",
    )
    assert result["permit_decision"] == "NOT_REQUIRED"
    assert result["authority_tier"] == "STATE_RULE"
    row = result["family_decisions"][0]
    assert row["status"] == "NOT_REQUIRED"
    assert row["source_ref"].startswith("https://www.nj.gov/")


def test_nj_roof_exemption_does_not_apply_to_disqualifying_scope_or_other_state():
    structural = resolve_decision_authority_ladder(
        job_type="Detached single-family reroof with structural rafter replacement.",
        city="Any municipality",
        state="NJ",
    )
    other_state = resolve_decision_authority_ladder(
        job_type="Detached single-family roof-covering replacement with no structural work.",
        city="Any municipality",
        state="PA",
    )
    assert structural.get("authority_tier") != "STATE_RULE"
    assert other_state.get("authority_tier") != "STATE_RULE"


def test_nc_like_kind_door_exemption_requires_threshold_and_scope_facts():
    qualifying = resolve_decision_authority_ladder(
        job_type="Replace an interior prehung door in the same opening with no wall framing or header changes; project cost under $40,000.",
        city="Raleigh",
        state="NC",
    )
    missing_cost = resolve_decision_authority_ladder(
        job_type="Replace an interior prehung door same size, no wall framing or header changes.",
        city="Raleigh",
        state="NC",
    )
    structural = resolve_decision_authority_ladder(
        job_type="Replace and widen an interior door opening with structural header replacement; project cost $5,000.",
        city="Raleigh",
        state="NC",
    )
    assert qualifying["permit_decision"] == "NOT_REQUIRED"
    assert qualifying["authority_tier"] == "STATE_RULE"
    assert qualifying["family_decisions"][0]["source_ref"].startswith("https://www.ncleg.gov/")
    assert missing_cost.get("authority_tier") != "STATE_RULE"
    assert structural.get("authority_tier") != "STATE_RULE"


def test_state_rule_rejects_status_outside_public_vocabulary():
    with pytest.raises(ValueError, match="invalid state-rule status"):
        StateRule(
            rule_id="invalid",
            state="XX",
            authority_model=AuthorityModel.STATEWIDE_MANDATORY,
            family="BUILDING",
            status="MAYBE",
            predicate=lambda _scope: True,
            source_url="https://example.gov/rule",
            citation="Test only",
            effective_date="2026-01-01",
            trigger="Test only",
        )
