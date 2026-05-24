# PermitAssist Lead Pipeline Phase 1 M3 — Fixture Connector + Adapter Policy Contract

Status: fixture-only Milestone 3.

M3 defines deterministic connector contracts and the PermitAssist adapter policy on top of M1 schema metadata and M2 gate semantics. It does **not** authorize or implement live lead collection, network access, scraping, DNS, SMTP probing, Apollo/paid APIs, ColdForge import, outreach, production database writes, customer-visible artifacts, Railway changes, GitHub remote actions, Hermes config/env changes, or deploys.

## Scope

M3 introduces:

- `lead_pipeline.connectors`
  - `ConnectorSpec`
  - `FixtureDocument`
  - `ConnectorRunResult`
  - `FetchMode.FIXTURE_ONLY`
  - `ConnectorPolicyStatus`
  - `FIXTURE_CONNECTOR_REGISTRY`
  - `get_connector_spec(...)`
  - `run_fixture_connector(...)`
- `lead_pipeline.adapters`
  - `PermitAssistAdapterPolicy`
  - `get_permitassist_adapter_policy(...)`
  - `enforce_adapter_policy_for_connector(...)`
  - `assert_no_live_outreach(...)`
- `tests/lead_pipeline/test_phase1_m3_fixture_connectors.py`
  - exact connector policy assertions;
  - M1 table-shape payload assertions;
  - live-mode and live-URL rejection checks;
  - PermitAssist adapter export-ceiling/no-outreach checks.

All code is stdlib-only plus local `lead_pipeline` modules.

## Connector policy registry

Allowed fixture-only connector ids:

1. `state_contractor_license_registry`
   - source class: `official_licensing_registry`
   - official/first-party flag: `1`
   - allowed phase: `phase1_fixture_only`
2. `secretary_of_state_business_registry`
   - source class: `official_licensing_registry`
   - official/first-party flag: `1`
   - allowed phase: `phase1_fixture_only`
3. `municipal_permit_portal_public_search`
   - source class: `official_licensing_registry`
   - official/first-party flag: `1`
   - allowed phase: `phase1_fixture_only`
4. `contractor_first_party_website`
   - source class: `first_party_website`
   - official/first-party flag: `1`
   - allowed phase: `phase1_fixture_only`
5. `user_uploaded_company_list_csv`
   - source class: `user_import`
   - official/first-party flag: `0`
   - allowed phase: `phase1_fixture_only`
   - note: allowed seed lineage, but not official/first-party evidence.

Blocked connector ids and exact statuses:

1. `apollo_io_b2b_database` → `blocked_paid_phase1`
2. `zoominfo_export` → `blocked_paid_phase1`
3. `serper_google_search_api` → `blocked_paid_api_phase1`
4. `clearbit_enrichment_api` → `blocked_paid_api_phase1`
5. `linkedin_sales_navigator` → `blocked_login_phase1`
6. `generic_web_scrape_robots_disallow` → `blocked_scraping_review_required_phase1`

Blocked connectors are represented as policy metadata only. They cannot be run through `run_fixture_connector(...)`.

## Fixture run output shape

`run_fixture_connector(...)` accepts only:

- a connector id that the PermitAssist adapter allows;
- `mode=FetchMode.FIXTURE_ONLY` or the equivalent string value;
- one or more `FixtureDocument` inputs whose `url_or_path` begins with `fixture://`;
- an explicit `batch_id`.

It returns a `ConnectorRunResult` with:

- `network_used = False`
- `send_authorized = False`
- one M1-compatible `sources` payload dictionary;
- one M1-compatible `source_observations` payload dictionary per fixture document;
- one M1-compatible `cost_events` payload dictionary per fixture document.

M3 does not write payloads to a database. Later milestones can append them through a separately approved event writer.

## Required lineage preserved

Each source payload includes:

- `source_id`
- `source_class`
- `source_name`
- `base_url_or_path`
- `official_or_first_party_flag`
- `terms_notes`
- `robots_notes`
- `requires_login`
- `paid_flag`
- `allowed_phase`
- `schema_version`

Each observation payload includes:

- `observation_id`
- `source_id`
- `batch_id`
- `connector_run_id`
- `observed_at_utc`
- `url_or_path`
- `content_type`
- `payload_hash_sha256`
- `snippet_or_excerpt`
- `extractor_version`
- `robots_or_terms_classification = fixture_only_no_fetch`
- `blocked_or_captcha_flag = 0`
- `cost_event_id`
- `schema_version`

Each cost payload includes:

- `cost_event_id`
- `batch_id`
- `connector_run_id`
- `stage = raw_collection`
- `provider_or_tool = fixture_connector::<connector_id>`
- `units_consumed = 1.0`
- `cost_usd = 0.0`
- `allocated_flag = 1`
- `created_at_utc`
- `schema_version`

## PermitAssist adapter policy

M3 defines `PERMITASSIST_ADAPTER_ID = permitassist`.

ICP slice:

- `commercial_tenant_improvement_gc_design_build_remodeling`

Allowed source classes:

- `official_licensing_registry`
- `first_party_website`
- `user_import`

Blocked source classes:

- `purchased_vendor_raw_seed`
- `social_profile`
- `scraped_public_page_future_phase`
- `search_or_places_discovery`
- `aggregator_directory`

Export and outreach boundary:

- `export_ceiling = internal_review_only`
- `live_outreach_allowed = False`
- `send_authorized_allowed = False`
- connector payloads do not include `send_authorized`, `outreach_authorized`, `live_send`, or `campaign_id` keys.

## Fail-closed behavior

The connector layer raises before producing payloads when:

- fetch mode is anything except `fixture_only`;
- any document uses a non-`fixture://` URL/path;
- connector id is unknown;
- connector id is blocked by paid/login/API/scrape policy;
- no fixture documents are provided.

## Boundary confirmation

M3 is still a local fixture contract. It imports no HTTP, DNS, SMTP, browser, subprocess, or paid-provider clients. It performs no network, no filesystem write, no DB write, no outreach, no remote GitHub action, no Railway action, and no production/customer-visible action.
