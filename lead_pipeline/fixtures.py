"""Synthetic fixture catalog for Phase 1 M2 gate tests.

These fixtures are deliberately local dictionaries: no real leads, no customer data,
no external fetches, and no paid-provider outputs. They encode incident classes from
LeadForge/EnrichForge/verifier salvage work so gate behavior can fail closed before
any live connector exists.
"""

from __future__ import annotations

from types import MappingProxyType

from .contracts import SourceClass, SuppressionStatus

FIXTURE_OBSERVED_AT_UTC = "2026-05-20T00:00:00Z"
FIXTURE_BATCH_ID = "batch_fixture_m2"

SOURCEABILITY_FIXTURES = MappingProxyType(
    {
        "complete_first_party_service_page": {
            "source_class": SourceClass.FIRST_PARTY_WEBSITE.value,
            "url_or_path": "fixture://first-party/services/commercial-ti",
            "observed_at_utc": FIXTURE_OBSERVED_AT_UTC,
            "payload_hash_sha256": "sha256_first_party_fixture",
            "field_relevant_snippet": "We perform commercial tenant improvement build-outs.",
            "blocked_or_captcha_flag": False,
            "requires_login": False,
            "paid_flag": False,
        },
        "missing_timestamp": {
            "source_class": SourceClass.FIRST_PARTY_WEBSITE.value,
            "url_or_path": "fixture://first-party/services",
            "payload_hash_sha256": "sha256_missing_timestamp",
            "field_relevant_snippet": "Commercial tenant improvements.",
        },
        "missing_url": {
            "source_class": SourceClass.FIRST_PARTY_WEBSITE.value,
            "observed_at_utc": FIXTURE_OBSERVED_AT_UTC,
            "payload_hash_sha256": "sha256_missing_url",
            "field_relevant_snippet": "Commercial tenant improvements.",
        },
        "missing_field_relevant_snippet": {
            "source_class": SourceClass.FIRST_PARTY_WEBSITE.value,
            "url_or_path": "fixture://first-party/services",
            "observed_at_utc": FIXTURE_OBSERVED_AT_UTC,
            "payload_hash_sha256": "sha256_missing_snippet",
        },
        "blocked_captcha_source": {
            "source_class": SourceClass.SCRAPED_PUBLIC_PAGE.value,
            "url_or_path": "fixture://blocked/captcha",
            "observed_at_utc": FIXTURE_OBSERVED_AT_UTC,
            "payload_hash_sha256": "sha256_blocked_captcha",
            "field_relevant_snippet": "captcha wall",
            "blocked_or_captcha_flag": True,
        },
        "login_required_source": {
            "source_class": SourceClass.SOCIAL_PROFILE.value,
            "url_or_path": "fixture://login/profile",
            "observed_at_utc": FIXTURE_OBSERVED_AT_UTC,
            "payload_hash_sha256": "sha256_login_required",
            "field_relevant_snippet": "profile hidden behind login",
            "requires_login": True,
        },
        "paid_adapter_source": {
            "source_class": SourceClass.PURCHASED_VENDOR.value,
            "url_or_path": "fixture://paid/vendor/export",
            "observed_at_utc": FIXTURE_OBSERVED_AT_UTC,
            "payload_hash_sha256": "sha256_paid_source",
            "field_relevant_snippet": "paid vendor lead row",
            "paid_flag": True,
        },
    }
)

CLEAN_OUTREACH_READY_FIXTURE = MappingProxyType(
    {
        "identity": {
            "business_name": "Fixture Build Group",
            "source_class": SourceClass.FIRST_PARTY_WEBSITE.value,
            "source_observation_id": "obs_clean_identity",
            "field_relevant_snippet": "Fixture Build Group is a licensed commercial general contractor.",
            "domain": "fixturebuild.com",
        },
        "icp": {
            "vertical_labels": ["commercial tenant improvement", "general contractor"],
            "evidence_snippet": "Commercial tenant improvement build-outs for restaurants, clinics, and offices.",
            "source_observation_id": "obs_clean_icp",
        },
        "contact": {
            "email": "estimating@fixturebuild.com",
            "domain": "fixturebuild.com",
            "observed_on_domain": True,
            "source_observation_id": "obs_clean_contact",
            "field_relevant_snippet": "For commercial estimating contact estimating@fixturebuild.com.",
            "guessed_pattern": False,
        },
        "enrichment": {
            "summary": "Commercial TI contractor with restaurant, clinic, and office build-out evidence.",
            "supporting_fact_ids": ["fact_identity", "fact_icp"],
            "supporting_observation_ids": ["obs_clean_identity", "obs_clean_icp"],
            "unsupported_claim_count": 0,
        },
        "readiness": {
            "sourceability_status": "pass",
            "identity_status": "pass",
            "icp_status": "pass",
            "contact_status": "pass",
            "email_status": "pass",
            "domain_status": "pass",
            "suppression_status": "pass",
            "enrichment_status": "pass",
            "human_review_status": "pass",
            "cited_business_identity": True,
            "verified_contact_fact": True,
            "send_authorized": False,
        },
    }
)

LEGACY_PITFALL_IDS = (
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
)

LEGACY_PITFALL_FIXTURES = MappingProxyType(
    {
        "placeholder_email_rejection": {"gate": "contact_observation", "email": "email@example.com", "observed_on_domain": False, "source_observation_id": "obs_1", "field_relevant_snippet": "email@example.com"},
        "off_domain_email_review": {"gate": "contact_observation", "email": "sales@bookingvendor.com", "domain": "fixturebuild.com", "observed_on_domain": False, "source_observation_id": "obs_1", "field_relevant_snippet": "booking vendor email"},
        "guessed_pattern_email_hold": {"gate": "contact_observation", "email": "john@fixturebuild.com", "domain": "fixturebuild.com", "guessed_pattern": True, "observed_on_domain": True, "source_observation_id": "obs_1", "field_relevant_snippet": "guessed pattern"},
        "per_email_provenance_required": {"gate": "contact_observation", "email": "estimating@fixturebuild.com", "domain": "fixturebuild.com", "observed_on_domain": True, "source_observation_id": "", "field_relevant_snippet": ""},
        "official_license_seed_promotion_limit": {"gate": "identity", "business_name": "Fixture Build Group", "source_class": SourceClass.OFFICIAL_LICENSING.value, "source_observation_id": "obs_license", "field_relevant_snippet": "License record only", "license_only_seed": True},
        "domain_name_only_match_review": {"gate": "identity", "business_name": "Fixture Build", "domain": "fixturebuild.com", "source_observation_id": "obs_domain", "field_relevant_snippet": "domain-only match", "domain_name_only_match": True},
        "normalized_duplicate_candidate_without_delete": {"gate": "identity", "business_name": "Fixture Build Group LLC", "source_observation_id": "obs_dup", "field_relevant_snippet": "duplicate candidate", "duplicate_candidate": True},
        "mx_absent_hard_fail": {"gate": "domain_quality", "domain": "fixturebuild.invalid", "mx_status": "absent"},
        "small_business_role_account_review": {"gate": "contact_observation", "email": "info@fixturebuild.com", "domain": "fixturebuild.com", "observed_on_domain": True, "source_observation_id": "obs_1", "field_relevant_snippet": "info@fixturebuild.com", "role_account": True},
        "catch_all_handling": {"gate": "domain_quality", "domain": "fixturebuild.com", "mx_status": "present", "catch_all": True},
        "greylist_timeout_retry_discipline": {"gate": "domain_quality", "domain": "fixturebuild.com", "mx_status": "timeout"},
        "thin_content_quality_cap": {"gate": "enrichment_quality", "summary": "Commercial contractor", "supporting_fact_ids": [], "unsupported_claim_count": 0},
        "generic_icebreaker_rejection": {"gate": "enrichment_quality", "summary": "Great company!", "supporting_fact_ids": ["fact_1"], "unsupported_claim_count": 0},
        "unsupported_contractor_software_claim_rejection": {"gate": "enrichment_quality", "summary": "Uses Procore for every project", "supporting_fact_ids": ["fact_1"], "unsupported_claim_count": 1},
        "coldforge_join_failure_fails_closed": {"gate": "outreach_readiness", "join_failure": True, "send_authorized": False},
        "suppression_blocks_export": {"gate": "suppression", "status": SuppressionStatus.SUPPRESSED_EMAIL.value, "snapshot_hash": "hash_suppressed"},
        "duplicate_campaign_enrollment_blocks_export": {"gate": "suppression", "status": SuppressionStatus.DUPLICATE_IN_CAMPAIGN.value, "snapshot_hash": "hash_duplicate"},
        "no_live_send_without_compliance_dry_run": {"gate": "outreach_readiness", "send_authorized": True, "compliance_dry_run": False},
        "paid_adapter_disabled_by_default": {"gate": "sourceability", **SOURCEABILITY_FIXTURES["paid_adapter_source"]},
        "apollo_benchmark_assumption_labelled": {"gate": "benchmark_assumption", "apollo_credit_cost_usd": 0.024, "assumption_label": ""},
    }
)
