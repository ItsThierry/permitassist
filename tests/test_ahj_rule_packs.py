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
