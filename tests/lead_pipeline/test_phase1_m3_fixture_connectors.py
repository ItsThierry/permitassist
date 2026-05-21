"""Phase 1 M3 fixture connector + PermitAssist adapter policy tests.

These tests assert that the M3 connector layer remains fixture-only with no
network/paid/login access, that paid/login/API sources are recorded with an
explicit BLOCKED policy status (exact enum match, not a broad category), and
that the PermitAssist adapter policy names its commercial TI ICP slice with a
hard internal-review-only export ceiling and no live outreach.
"""

from __future__ import annotations

import hashlib

import pytest

from lead_pipeline.contracts import ExportEligibility, PageType, SourceClass
from lead_pipeline.schema import PHASE1_SCHEMA_VERSION, get_table_contract

from lead_pipeline.connectors import (
    FIXTURE_CONNECTOR_REGISTRY,
    FIXTURE_ROBOTS_OR_TERMS_CLASSIFICATION,
    ConnectorPolicyStatus,
    ConnectorRunResult,
    ConnectorSpec,
    FetchMode,
    FixtureDocument,
    LiveFetchAttemptError,
    UnknownConnectorError,
    get_connector_spec,
    run_fixture_connector,
)
from lead_pipeline.adapters import (
    PERMITASSIST_ADAPTER_ID,
    AdapterPolicyError,
    PermitAssistAdapterPolicy,
    enforce_adapter_policy_for_connector,
    get_permitassist_adapter_policy,
)


EXPECTED_ALLOWED_CONNECTOR_IDS = {
    "state_contractor_license_registry",
    "secretary_of_state_business_registry",
    "municipal_permit_portal_public_search",
    "contractor_first_party_website",
    "fixture_search_discovery",
    "user_uploaded_company_list_csv",
}

EXPECTED_BLOCKED_CONNECTOR_IDS = {
    "apollo_io_b2b_database",
    "zoominfo_export",
    "linkedin_sales_navigator",
    "serper_google_search_api",
    "clearbit_enrichment_api",
    "generic_web_scrape_robots_disallow",
}

EXPECTED_BLOCKED_STATUSES = {
    "apollo_io_b2b_database": ConnectorPolicyStatus.BLOCKED_PAID_PHASE1,
    "zoominfo_export": ConnectorPolicyStatus.BLOCKED_PAID_PHASE1,
    "clearbit_enrichment_api": ConnectorPolicyStatus.BLOCKED_PAID_API_PHASE1,
    "serper_google_search_api": ConnectorPolicyStatus.BLOCKED_PAID_API_PHASE1,
    "linkedin_sales_navigator": ConnectorPolicyStatus.BLOCKED_LOGIN_PHASE1,
    "generic_web_scrape_robots_disallow": ConnectorPolicyStatus.BLOCKED_SCRAPING_REVIEW_REQUIRED_PHASE1,
}

EXPECTED_ALLOWED_SOURCE_CLASSES = {
    SourceClass.OFFICIAL_LICENSING,
    SourceClass.FIRST_PARTY_WEBSITE,
    SourceClass.SEARCH_OR_PLACES,
    SourceClass.USER_IMPORT,
}

EXPECTED_OFFICIAL_OR_FIRST_PARTY_FLAGS = {
    "state_contractor_license_registry": True,
    "secretary_of_state_business_registry": True,
    "municipal_permit_portal_public_search": True,
    "contractor_first_party_website": True,
    "fixture_search_discovery": False,
    "user_uploaded_company_list_csv": False,
}

EXPECTED_BLOCKED_SOURCE_CLASSES = {
    SourceClass.PURCHASED_VENDOR,
    SourceClass.SOCIAL_PROFILE,
    SourceClass.SCRAPED_PUBLIC_PAGE,
    SourceClass.SEARCH_OR_PLACES,
    SourceClass.AGGREGATOR_DIRECTORY,
}


def _make_doc(url: str, snippet: str, payload: str, fetched_at: str = "2026-05-20T00:00:00Z") -> FixtureDocument:
    return FixtureDocument(
        url_or_path=url,
        fetched_at_utc=fetched_at,
        payload_text=payload,
        snippet=snippet,
        content_type="text/plain",
    )


def _clean_first_party_documents() -> list[FixtureDocument]:
    return [
        _make_doc(
            "fixture://contractor-first-party/fixturebuild/services/commercial-ti",
            "We perform commercial tenant improvement build-outs for restaurants, clinics, and offices.",
            "Fixture Build Group commercial TI service page payload synthetic body.",
        ),
        _make_doc(
            "fixture://contractor-first-party/fixturebuild/about",
            "Fixture Build Group is a licensed commercial general contractor in California.",
            "Fixture Build Group about page payload synthetic body.",
        ),
    ]


def test_fixture_connector_registry_includes_expected_allowed_and_blocked_connectors():
    assert set(FIXTURE_CONNECTOR_REGISTRY).issuperset(EXPECTED_ALLOWED_CONNECTOR_IDS)
    assert set(FIXTURE_CONNECTOR_REGISTRY).issuperset(EXPECTED_BLOCKED_CONNECTOR_IDS)
    assert set(FIXTURE_CONNECTOR_REGISTRY) == EXPECTED_ALLOWED_CONNECTOR_IDS | EXPECTED_BLOCKED_CONNECTOR_IDS

    for connector_id in EXPECTED_ALLOWED_CONNECTOR_IDS:
        spec = FIXTURE_CONNECTOR_REGISTRY[connector_id]
        assert spec.policy_status == ConnectorPolicyStatus.ALLOWED_FIXTURE_PHASE1, connector_id
        assert spec.allowed_phase == "phase1_fixture_only", connector_id
        assert spec.paid_flag is False, connector_id
        assert spec.requires_login is False, connector_id
        assert spec.requires_api_key is False, connector_id
        assert spec.source_class in EXPECTED_ALLOWED_SOURCE_CLASSES, connector_id
        assert spec.official_or_first_party_flag is EXPECTED_OFFICIAL_OR_FIRST_PARTY_FLAGS[connector_id], connector_id

    for connector_id in EXPECTED_BLOCKED_CONNECTOR_IDS:
        spec = FIXTURE_CONNECTOR_REGISTRY[connector_id]
        assert spec.policy_status == EXPECTED_BLOCKED_STATUSES[connector_id], connector_id
        assert spec.policy_status != ConnectorPolicyStatus.ALLOWED_FIXTURE_PHASE1, connector_id
        assert spec.allowed_phase != "phase1_fixture_only", connector_id
        assert spec.blocked_reason, connector_id
        assert spec.source_class in EXPECTED_BLOCKED_SOURCE_CLASSES, connector_id


def test_paid_login_api_and_scrape_source_policies_return_exact_blocked_status():
    for connector_id, expected_status in EXPECTED_BLOCKED_STATUSES.items():
        spec = get_connector_spec(connector_id)
        assert spec.policy_status == expected_status, connector_id
        assert spec.policy_status not in {
            ConnectorPolicyStatus.ALLOWED_FIXTURE_PHASE1,
        }


def test_fixture_connector_run_emits_deterministic_sha256_payload_hashes_and_no_network_flag():
    docs = _clean_first_party_documents()
    first_run = run_fixture_connector(
        "contractor_first_party_website",
        documents=docs,
        batch_id="batch_fixture_m3",
        mode=FetchMode.FIXTURE_ONLY,
    )
    second_run = run_fixture_connector(
        "contractor_first_party_website",
        documents=docs,
        batch_id="batch_fixture_m3",
        mode=FetchMode.FIXTURE_ONLY,
    )

    assert isinstance(first_run, ConnectorRunResult)
    assert first_run.network_used is False
    assert first_run.mode == FetchMode.FIXTURE_ONLY.value
    assert first_run.connector_id == "contractor_first_party_website"
    assert first_run.send_authorized is False

    # Determinism: same inputs ⇒ same hashes and same observation IDs.
    assert [obs["payload_hash_sha256"] for obs in first_run.observations] == [
        obs["payload_hash_sha256"] for obs in second_run.observations
    ]
    assert [obs["observation_id"] for obs in first_run.observations] == [
        obs["observation_id"] for obs in second_run.observations
    ]
    assert first_run.source["source_id"] == second_run.source["source_id"]

    # SHA256 hex check — matches our own recomputation of the payload text.
    for obs, doc in zip(first_run.observations, docs):
        expected = hashlib.sha256(doc.payload_text.encode("utf-8")).hexdigest()
        assert obs["payload_hash_sha256"] == expected
        assert obs["url_or_path"] == doc.url_or_path
        assert obs["observed_at_utc"] == doc.fetched_at_utc
        assert obs["snippet_or_excerpt"] == doc.snippet
        assert obs["blocked_or_captcha_flag"] == 0
        assert obs["page_type"] in {
            PageType.FIRST_PARTY_HOMEPAGE.value,
            PageType.FIRST_PARTY_SERVICES.value,
            PageType.FIRST_PARTY_CONTACT.value,
        }
        assert obs["page_type"] != PageType.BLOCKED_UNKNOWN.value
        assert obs["robots_or_terms_classification"] == FIXTURE_ROBOTS_OR_TERMS_CLASSIFICATION


def test_fixture_connector_run_payload_keys_are_subset_of_m1_table_contracts():
    docs = _clean_first_party_documents()
    run = run_fixture_connector(
        "contractor_first_party_website",
        documents=docs,
        batch_id="batch_fixture_m3",
        mode=FetchMode.FIXTURE_ONLY,
    )

    sources_contract = get_table_contract("sources")
    obs_contract = get_table_contract("source_observations")
    cost_contract = get_table_contract("cost_events")

    assert set(run.source).issubset(sources_contract.columns), set(run.source) - set(sources_contract.columns)
    assert run.source["schema_version"] == PHASE1_SCHEMA_VERSION
    assert run.source["source_class"] == SourceClass.FIRST_PARTY_WEBSITE.value
    assert run.source["official_or_first_party_flag"] == 1
    assert run.source["paid_flag"] == 0
    assert run.source["requires_login"] == 0
    assert run.source["allowed_phase"] == "phase1_fixture_only"
    assert run.source["base_url_or_path"].startswith("fixture://")
    assert run.source["terms_notes"]
    assert run.source["robots_notes"]

    for obs in run.observations:
        assert set(obs).issubset(obs_contract.columns), set(obs) - set(obs_contract.columns)
        # M1 required-without-default columns are present.
        missing_required = [
            name
            for name, column in obs_contract.columns.items()
            if column.required and column.default is None and name not in obs
        ]
        assert not missing_required, missing_required
        assert obs["schema_version"] == PHASE1_SCHEMA_VERSION
        assert obs["source_id"] == run.source["source_id"]
        assert obs["batch_id"] == "batch_fixture_m3"
        assert obs["connector_run_id"] == run.connector_run_id
        assert obs["payload_hash_sha256"]
        assert obs["snippet_or_excerpt"]
        assert obs["url_or_path"]
        assert obs["observed_at_utc"]

    assert run.cost_events, "cost events should be emitted for each fixture observation"
    for cost in run.cost_events:
        assert set(cost).issubset(cost_contract.columns), set(cost) - set(cost_contract.columns)
        missing_required = [
            name
            for name, column in cost_contract.columns.items()
            if column.required and column.default is None and name not in cost
        ]
        assert not missing_required, missing_required
        assert cost["schema_version"] == PHASE1_SCHEMA_VERSION
        assert cost["stage"] == "raw_collection"
        assert cost["cost_usd"] == 0.0
        assert cost["batch_id"] == "batch_fixture_m3"
        assert cost["connector_run_id"] == run.connector_run_id


def test_fixture_connector_rejects_live_or_non_fixture_mode():
    docs = _clean_first_party_documents()
    with pytest.raises(LiveFetchAttemptError):
        run_fixture_connector(
            "contractor_first_party_website",
            documents=docs,
            batch_id="batch_fixture_m3",
            mode="live",  # type: ignore[arg-type]
        )
    with pytest.raises(LiveFetchAttemptError):
        run_fixture_connector(
            "contractor_first_party_website",
            documents=docs,
            batch_id="batch_fixture_m3",
            mode="http_fetch",  # type: ignore[arg-type]
        )


def test_fixture_connector_rejects_documents_with_live_urls():
    bad_docs = [
        _make_doc(
            "https://www.fixturebuild.com/services",
            "We perform commercial tenant improvement build-outs.",
            "live URL payload",
        )
    ]
    with pytest.raises(LiveFetchAttemptError):
        run_fixture_connector(
            "contractor_first_party_website",
            documents=bad_docs,
            batch_id="batch_fixture_m3",
            mode=FetchMode.FIXTURE_ONLY,
        )


def test_fixture_connector_refuses_blocked_connector_ids_and_unknown_ids():
    docs = _clean_first_party_documents()
    for connector_id, expected_status in EXPECTED_BLOCKED_STATUSES.items():
        with pytest.raises(AdapterPolicyError) as excinfo:
            run_fixture_connector(
                connector_id,
                documents=docs,
                batch_id="batch_fixture_m3",
                mode=FetchMode.FIXTURE_ONLY,
            )
        assert expected_status.value in str(excinfo.value), connector_id

    with pytest.raises(UnknownConnectorError):
        run_fixture_connector(
            "this_connector_does_not_exist",
            documents=docs,
            batch_id="batch_fixture_m3",
            mode=FetchMode.FIXTURE_ONLY,
        )


def test_permitassist_adapter_policy_names_icp_allowed_blocked_export_ceiling_and_no_live_outreach():
    policy = get_permitassist_adapter_policy()

    assert isinstance(policy, PermitAssistAdapterPolicy)
    assert policy.adapter_id == PERMITASSIST_ADAPTER_ID == "permitassist"
    assert policy.precision_first is True
    assert policy.live_outreach_allowed is False
    assert policy.send_authorized_allowed is False
    assert policy.allowed_phase == "phase1_fixture_only"
    assert policy.export_ceiling == ExportEligibility.INTERNAL_REVIEW_ONLY

    # ICP slice — commercial TI GC / design-build / remodeling.
    assert "commercial_tenant_improvement_gc_design_build_remodeling" in policy.icp_segments

    # Allowed and blocked source classes are explicit (exact match, not a broad category).
    assert set(policy.allowed_source_classes) == EXPECTED_ALLOWED_SOURCE_CLASSES
    assert set(policy.blocked_source_classes) == EXPECTED_BLOCKED_SOURCE_CLASSES
    assert set(policy.allowed_connector_ids) == EXPECTED_ALLOWED_CONNECTOR_IDS
    assert set(policy.blocked_connector_ids) == EXPECTED_BLOCKED_CONNECTOR_IDS

    # Connector ids must stay disjoint even when a source class is valid in one
    # fixture-only connector and blocked in a live/paid connector.
    assert set(policy.allowed_connector_ids).isdisjoint(policy.blocked_connector_ids)


def test_search_or_places_policy_depends_on_connector_id_not_source_class_alone():
    policy = get_permitassist_adapter_policy()

    assert SourceClass.SEARCH_OR_PLACES in policy.allowed_source_classes
    assert SourceClass.SEARCH_OR_PLACES in policy.blocked_source_classes

    fixture_search = get_connector_spec("fixture_search_discovery")
    live_serper = get_connector_spec("serper_google_search_api")

    assert fixture_search.source_class is SourceClass.SEARCH_OR_PLACES
    assert fixture_search.policy_status == ConnectorPolicyStatus.ALLOWED_FIXTURE_PHASE1
    assert fixture_search.paid_flag is False
    assert fixture_search.requires_api_key is False
    assert (
        enforce_adapter_policy_for_connector("fixture_search_discovery")
        == ConnectorPolicyStatus.ALLOWED_FIXTURE_PHASE1
    )

    assert live_serper.source_class is SourceClass.SEARCH_OR_PLACES
    assert live_serper.policy_status == ConnectorPolicyStatus.BLOCKED_PAID_API_PHASE1
    assert live_serper.paid_flag is True
    assert live_serper.requires_api_key is True
    with pytest.raises(AdapterPolicyError) as excinfo:
        enforce_adapter_policy_for_connector("serper_google_search_api")
    assert ConnectorPolicyStatus.BLOCKED_PAID_API_PHASE1.value in str(excinfo.value)


def test_adapter_policy_enforcement_blocks_paid_login_api_scrape_with_exact_status():
    for connector_id in EXPECTED_ALLOWED_CONNECTOR_IDS:
        assert (
            enforce_adapter_policy_for_connector(connector_id)
            == ConnectorPolicyStatus.ALLOWED_FIXTURE_PHASE1
        )

    for connector_id, expected_status in EXPECTED_BLOCKED_STATUSES.items():
        with pytest.raises(AdapterPolicyError) as excinfo:
            enforce_adapter_policy_for_connector(connector_id)
        # Exact expected status (not just "any blocked") — addresses the Opus M2 note.
        assert expected_status.value in str(excinfo.value)


def test_connector_output_never_sets_or_implies_send_authorized():
    docs = _clean_first_party_documents()
    run = run_fixture_connector(
        "contractor_first_party_website",
        documents=docs,
        batch_id="batch_fixture_m3",
        mode=FetchMode.FIXTURE_ONLY,
    )
    assert run.send_authorized is False
    # The connector payloads must not include any sending/outreach permission column.
    forbidden_keys = {"send_authorized", "outreach_authorized", "live_send", "campaign_id"}
    assert forbidden_keys.isdisjoint(run.source)
    for obs in run.observations:
        assert forbidden_keys.isdisjoint(obs)
    for cost in run.cost_events:
        assert forbidden_keys.isdisjoint(cost)


def test_user_import_connector_preserves_import_lineage():
    docs = [
        FixtureDocument(
            url_or_path="fixture://user-import/seed-list/commercial-ti-2026-05-20.csv",
            fetched_at_utc="2026-05-20T00:00:00Z",
            payload_text="row1,Fixture Build Group,commercial TI GC\nrow2,Sample Remodelers,design build remodel",
            snippet="Fixture Build Group, Sample Remodelers — operator-imported commercial TI seed list.",
            content_type="text/csv",
        )
    ]
    run = run_fixture_connector(
        "user_uploaded_company_list_csv",
        documents=docs,
        batch_id="batch_fixture_m3",
        mode=FetchMode.FIXTURE_ONLY,
    )
    assert run.source["source_class"] == SourceClass.USER_IMPORT.value
    # User imports are allowed seed lineage, but they are not official/first-party evidence.
    assert run.source["official_or_first_party_flag"] == 0
    assert run.network_used is False
    assert run.observations[0]["content_type"] == "text/csv"
    assert run.observations[0]["url_or_path"].startswith("fixture://user-import/")


def test_official_licensing_connector_emits_official_first_party_flag():
    docs = [
        FixtureDocument(
            url_or_path="fixture://state-license-registry/CA/CSLB/lookup/12345",
            fetched_at_utc="2026-05-20T00:00:00Z",
            payload_text="License #12345 — Fixture Build Group — Class B General Building Contractor — Active",
            snippet="Active Class B General Building Contractor license for Fixture Build Group.",
            content_type="text/html",
        )
    ]
    run = run_fixture_connector(
        "state_contractor_license_registry",
        documents=docs,
        batch_id="batch_fixture_m3",
        mode=FetchMode.FIXTURE_ONLY,
    )
    assert run.source["source_class"] == SourceClass.OFFICIAL_LICENSING.value
    assert run.source["official_or_first_party_flag"] == 1
    assert run.source["terms_notes"]
    assert run.source["robots_notes"]
