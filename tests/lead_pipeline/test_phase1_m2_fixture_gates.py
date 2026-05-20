from lead_pipeline.contracts import ExportEligibility, GateStatus, SuppressionStatus
from lead_pipeline.gates import (
    GateResult,
    contact_observation_gate,
    domain_quality_gate,
    email_syntax_gate,
    enrichment_quality_gate,
    evaluate_legacy_pitfall_fixture,
    icp_fit_gate,
    identity_gate,
    outreach_readiness_gate,
    sourceability_gate,
    suppression_gate,
)
from lead_pipeline.fixtures import (
    CLEAN_OUTREACH_READY_FIXTURE,
    LEGACY_PITFALL_FIXTURES,
    LEGACY_PITFALL_IDS,
    SOURCEABILITY_FIXTURES,
)
from lead_pipeline.schema import get_table_contract


EXPECTED_LEGACY_PITFALL_IDS = {
    "placeholder_email_rejection",
    "off_domain_email_review",
    "guessed_pattern_email_hold",
    "per_email_provenance_required",
    "official_license_seed_promotion_limit",
    "domain_name_only_match_review",
    "normalized_duplicate_candidate_without_delete",
    "mx_absent_hard_fail",
    "small_business_role_account_review",
    "catch_all_handling",
    "greylist_timeout_retry_discipline",
    "thin_content_quality_cap",
    "generic_icebreaker_rejection",
    "unsupported_contractor_software_claim_rejection",
    "coldforge_join_failure_fails_closed",
    "suppression_blocks_export",
    "duplicate_campaign_enrollment_blocks_export",
    "no_live_send_without_compliance_dry_run",
    "paid_adapter_disabled_by_default",
    "apollo_benchmark_assumption_labelled",
}


FAIL_CLOSED_STATUSES = {
    GateStatus.FAIL_CLOSED,
    GateStatus.BLOCKED_SOURCE_POLICY,
    GateStatus.REVIEW_REQUIRED,
    GateStatus.UNKNOWN_NOT_PROMOTED,
}


def assert_event_payload_conforms(result: GateResult) -> None:
    contract = get_table_contract(result.event_table)
    payload = result.to_event_payload()

    missing_required = [
        name
        for name, column in contract.columns.items()
        if column.required and column.default is None and name not in payload
    ]
    assert not missing_required, f"{result.gate_name} missing {missing_required}"
    assert set(payload).issubset(contract.columns), result.gate_name
    assert payload["schema_version"] == contract.schema_version
    assert payload["result_status"] == result.status.value
    assert payload["network_used_flag"] == 0
    assert payload["input_hash"]


def test_sourceability_gate_fails_closed_for_uncited_or_blocked_sources():
    assert sourceability_gate(SOURCEABILITY_FIXTURES["complete_first_party_service_page"]).status == GateStatus.PASS_

    for fixture_id in [
        "missing_timestamp",
        "missing_url",
        "missing_field_relevant_snippet",
        "blocked_captcha_source",
        "login_required_source",
        "paid_adapter_source",
    ]:
        result = sourceability_gate(SOURCEABILITY_FIXTURES[fixture_id])
        assert result.status in FAIL_CLOSED_STATUSES, fixture_id
        assert_event_payload_conforms(result)


def test_identity_and_icp_gates_require_source_backed_business_fit():
    assert identity_gate(CLEAN_OUTREACH_READY_FIXTURE["identity"]).status == GateStatus.PASS_
    assert icp_fit_gate(CLEAN_OUTREACH_READY_FIXTURE["icp"]).status == GateStatus.PASS_

    assert identity_gate({"business_name": "", "source_observation_id": "obs_1"}).status == GateStatus.FAIL_CLOSED
    assert identity_gate({"business_name": "Fixture Builders", "source_class": "aggregator_directory", "source_observation_id": "obs_agg", "field_relevant_snippet": "Fixture Builders profile"}).status == GateStatus.REVIEW_REQUIRED
    assert icp_fit_gate({"vertical_labels": ["general contractor"], "evidence_snippet": ""}).status == GateStatus.FAIL_CLOSED
    assert icp_fit_gate({"vertical_labels": ["permit expediter"], "evidence_snippet": "permit expediting"}).status == GateStatus.REVIEW_REQUIRED


def test_contact_verifier_gates_block_unsafe_email_candidates_without_network():
    clean_contact = CLEAN_OUTREACH_READY_FIXTURE["contact"]
    assert contact_observation_gate(clean_contact).status == GateStatus.PASS_
    assert email_syntax_gate(clean_contact["email"]).status == GateStatus.PASS_
    assert domain_quality_gate({"domain": "fixturebuild.com", "mx_status": "present"}).status == GateStatus.PASS_

    unsafe_contacts = [
        {"email": "info@example.com", "observed_on_domain": False, "source_observation_id": "obs_1", "field_relevant_snippet": "Email info@example.com"},
        {"email": "john@fixturebuild.com", "guessed_pattern": True, "source_observation_id": "obs_1", "field_relevant_snippet": "guessed"},
        {"email": "noreply@fixturebuild.com", "observed_on_domain": True, "source_observation_id": "obs_1", "field_relevant_snippet": "noreply"},
        {"email": "estimating@fixturebuild.com", "observed_on_domain": True, "source_observation_id": "", "field_relevant_snippet": "Email us"},
    ]
    for contact in unsafe_contacts:
        result = contact_observation_gate(contact)
        assert result.status in FAIL_CLOSED_STATUSES
        assert_event_payload_conforms(result)

    assert email_syntax_gate("not-an-email").status == GateStatus.FAIL_CLOSED
    assert domain_quality_gate({"domain": "fixturebuild.invalid", "mx_status": "absent"}).status == GateStatus.FAIL_CLOSED
    assert domain_quality_gate({"domain": "mailinator.com", "mx_status": "present", "disposable": True}).status == GateStatus.FAIL_CLOSED
    assert domain_quality_gate({"domain": "gmail.com", "mx_status": "present", "free_provider": True}).status == GateStatus.REVIEW_REQUIRED
    assert domain_quality_gate({"domain": "fixturebuild.com", "mx_status": "timeout"}).status == GateStatus.UNKNOWN_NOT_PROMOTED


def test_suppression_enrichment_and_outreach_readiness_fail_closed():
    assert suppression_gate({"status": SuppressionStatus.CLEAR.value, "snapshot_hash": "hash_fixture"}).status == GateStatus.PASS_
    for status in [
        SuppressionStatus.SUPPRESSED_EMAIL.value,
        SuppressionStatus.SUPPRESSED_DOMAIN.value,
        SuppressionStatus.DUPLICATE_IN_CAMPAIGN.value,
        SuppressionStatus.SUPPRESSION_CONFLICT_HOLD.value,
        SuppressionStatus.SUPPRESSION_UNKNOWN_HOLD.value,
    ]:
        result = suppression_gate({"status": status, "snapshot_hash": "hash_fixture"})
        assert result.status in FAIL_CLOSED_STATUSES

    assert enrichment_quality_gate(CLEAN_OUTREACH_READY_FIXTURE["enrichment"]).status == GateStatus.PASS_
    for enrichment in [
        {"summary": "Great company!", "supporting_fact_ids": ["fact_1"], "unsupported_claim_count": 0},
        {"summary": "Uses Procore for all projects", "supporting_fact_ids": ["fact_1"], "unsupported_claim_count": 1},
        {"summary": "x" * 241, "supporting_fact_ids": ["fact_1"], "unsupported_claim_count": 0},
        {"summary": "Commercial contractor", "supporting_fact_ids": [], "unsupported_claim_count": 0},
    ]:
        assert enrichment_quality_gate(enrichment).status in FAIL_CLOSED_STATUSES

    ready = outreach_readiness_gate(CLEAN_OUTREACH_READY_FIXTURE["readiness"])
    assert ready.status == GateStatus.PASS_
    assert ready.export_eligibility == ExportEligibility.INTERNAL_REVIEW_ONLY
    assert ready.send_authorized is False

    blocked = outreach_readiness_gate({**CLEAN_OUTREACH_READY_FIXTURE["readiness"], "suppression_status": GateStatus.UNKNOWN_NOT_PROMOTED.value})
    assert blocked.status == GateStatus.FAIL_CLOSED
    assert blocked.export_eligibility == ExportEligibility.BLOCKED_UNKNOWN


def test_legacy_pitfall_catalog_is_complete_and_each_fixture_fails_closed():
    assert set(LEGACY_PITFALL_IDS) == EXPECTED_LEGACY_PITFALL_IDS
    assert set(LEGACY_PITFALL_FIXTURES) == EXPECTED_LEGACY_PITFALL_IDS

    for fixture_id, fixture in LEGACY_PITFALL_FIXTURES.items():
        result = evaluate_legacy_pitfall_fixture(fixture)
        assert result.status in FAIL_CLOSED_STATUSES, fixture_id
        assert result.reason_codes, fixture_id
        assert result.network_used is False, fixture_id
        assert result.send_authorized is False, fixture_id
        assert_event_payload_conforms(result)
