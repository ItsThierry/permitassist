from api.ahj_rule_packs import AHJ_RULES, resolve_ahj_rule


SOURCE = "https://www.sandiego.gov/development-services/permits/grading-permit"


def test_exact_ahj_rule_requires_jurisdiction_scope_and_uses_retained_registry_source():
    result = {"source_urls": [SOURCE]}
    rule = resolve_ahj_rule(
        "San Diego",
        "CA",
        "minor grading and drainage improvements for restaurant patio expansion",
        result,
    )
    assert rule is not None
    assert rule.family == "grading"
    assert rule.status == "REQUIRED"
    assert rule.source_claim_sha256 == "278530bcd81534f858ea5b18d3b21a5c27044ae627c07cd3e590e8fa980148e9"

    assert resolve_ahj_rule("San Diego", "CA", "replace an electrical panel", result) is None
    assert resolve_ahj_rule("Los Angeles", "CA", "minor grading", result) is None
    timeout_rule = resolve_ahj_rule("San Diego", "CA", "minor grading", {"source_urls": []})
    assert timeout_rule is not None
    assert timeout_rule.source_url == SOURCE


def test_retained_registry_rules_survive_runtime_source_timeout():
    queens = resolve_ahj_rule(
        "Queens", "NY", "replace shower and relocate drain six inches", {"source_urls": []},
    )
    seattle = resolve_ahj_rule(
        "Seattle", "WA", "build new detached backyard cottage with bathroom", {"source_urls": []},
    )
    assert queens is not None and queens.family == "plumbing" and queens.status == "REQUIRED"
    assert seattle is not None and seattle.family == "building" and seattle.status == "REQUIRED"


def test_exact_ahj_rules_do_not_treat_explicit_negative_scope_as_a_match():
    assert resolve_ahj_rule(
        "San Diego", "CA", "restaurant patio refresh; no grading, no excavation, and no earthwork", {},
    ) is None
    assert resolve_ahj_rule(
        "Queens", "NY", "replace shower trim; no drain or piping relocation", {},
    ) is None
    assert resolve_ahj_rule(
        "Seattle", "WA", "interior repaint; no detached ADU or backyard cottage construction", {},
    ) is None


def test_ahj_rule_registry_is_integrity_checked_at_import():
    assert AHJ_RULES
    assert all(rule.source_url.startswith("https://") for rule in AHJ_RULES)
    assert all(rule.source_quote and rule.source_claim_sha256 for rule in AHJ_RULES)


def test_exact_ahj_rule_claim_local_exclusions_fail_closed():
    valid = resolve_ahj_rule(
        "Seattle",
        "WA",
        "replace existing gas storage water heater with a heat pump water heater",
        {},
    )
    assert valid is not None
    assert valid.rule_id == "us-wa-seattle-replacement-water-heater-required-v1"

    for accepted_scope in (
        "Quoted and accepted scope: replace existing water heater today",
        "Estimate approved: replace existing water heater today",
        "Option authorized to replace existing water heater today",
        "Proposal accepted: replace existing water heater today",
        "Accepted quote: replace existing water heater today",
        "Approved estimate: replace existing water heater today",
        "Authorized option: replace existing water heater today",
        "Selected alternate: replace existing water heater today",
        "Approved proposal: replace existing water heater today",
        "Replace the water heater.",
        "Replacing existing gas water heater!",
        "Replacement of the old electric water heater?",
        "Replace water heater today; quote accepted.",
        "Gas water heater replacement now — approved estimate.",
    ):
        accepted = resolve_ahj_rule("Seattle", "WA", accepted_scope, {})
        assert accepted is not None, accepted_scope

    excluded_scopes = (
        "water heater replacement by others; our scope is faucet only",
        "water heater replacement is excluded from our scope",
        "water heater replacement not included; replace faucet only",
        "future water heater replacement; current scope is faucet only",
        "quoted alternate water heater replacement; base scope is faucet only",
        "replace the water heater's supply line only",
        "replace water heater in the future; today replace faucet only",
        "option to replace water heater; base scope is faucet only",
        "quote to replace water heater; authorized scope is faucet only",
        "estimate to replace water heater; no work authorized",
        "Owner declined to replace water heater; repair faucet only",
        "Replace water heater: valve only",
        "Replace water heater, valve only",
        "Replace faucet (water heater replacement by owner)",
        "Water heater replacement completed by others; our scope is faucet only",
        "Water heater replacement performed by others; our scope is faucet only",
        "Water heater replacement to be completed by others; our scope is faucet only",
        "Water heater replacement under a separate contract; our scope is faucet only",
        "Future owner will replace water heater; current scope is faucet only",
        "Phase 2 will replace water heater; current scope is faucet only",
        "Water heater replacement is a future phase; current scope is faucet only",
        "Allowance only to replace water heater; no work is authorized",
        "Quote accepted: water heater replacement; work by others",
        "Quote approved: water heater replacement. Work by others",
        "Option accepted: water heater replacement. Phase 2",
        "Proposal approved: water heater replacement; separate contract",
        "Bid authorized: water heater replacement. Allowance only",
        "Quote accepted: water heater replacement; expansion tank only",
        "Estimate approved: water heater replacement. Supply line only",
        "Water heater replacement today; quote accepted by others for future phase",
        "Replace water heater now; proposal accepted under separate contract",
    )
    for scope in excluded_scopes:
        assert resolve_ahj_rule("Seattle", "WA", scope, {}) is None, scope


def test_hpwh_acronym_is_water_heater_plumbing_scope():
    from api.scope_contract import build_scope_contract

    contract = build_scope_contract("HPWH replacement")
    assert contract["vertical"] == "water_heater"
    assert contract["family"] == "residential_single_trade"
