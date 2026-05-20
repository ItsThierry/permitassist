# PermitAssist Lead Pipeline Phase 1 Milestone 1 Schema Contract

Status: fixture-only Milestone 1 skeleton.

This document mirrors `lead_pipeline/contracts.py` and `lead_pipeline/schema.py`. It describes local schema metadata, enum contracts, and fixture-test safety guarantees only. It does not authorize or implement live lead collection, network access, scraping, paid APIs, Apollo usage, SMTP probing, outreach, production data mutation, customer-visible artifacts, Railway changes, GitHub remote actions, Hermes core/config/env changes, or ColdForge changes.

## Scope

Milestone 1 creates a deterministic, stdlib-only contract layer for an evidence-first lead intelligence pipeline. The production target remains Postgres-first, but this milestone uses SQLite-compatible DDL strings only as a local fixture validator.

Allowed implementation areas used:

- `lead_pipeline/**`
- `tests/lead_pipeline/**`
- `docs/lead_pipeline/**`

## Schema version

`PHASE1_SCHEMA_VERSION = lead_pipeline_phase1_m1_v1`

Every table contract includes a required `schema_version` column so future migration and audit tooling can reject mixed contract versions.

## Required tables

The required table order is deterministic:

1. `batches`
2. `sources`
3. `source_observations`
4. `entities`
5. `facts`
6. `identity_edges`
7. `verification_events`
8. `enrichment_events`
9. `suppression_events`
10. `export_events`
11. `cost_events`
12. `human_review_events`

### `batches`

Purpose: approved fixture/review unit for one bounded no-network batch.

Primary key: `batch_id`

Key columns:

- `approved_scope_ref`
- `adapter_id`
- `started_at_utc`
- `completed_at_utc`
- `status`
- `source_mix_json`
- `notes`
- `schema_version`

### `sources`

Purpose: source registry and policy object. Paid/login sources may be represented as blocked metadata, but Milestone 1 does not call them.

Primary key: `source_id`

Key columns:

- `source_class`
- `source_name`
- `base_url_or_path`
- `official_or_first_party_flag`
- `terms_notes`
- `robots_notes`
- `requires_login`
- `paid_flag`
- `allowed_phase`
- `rate_limit_policy_id`
- `budget_policy_id`
- `schema_version`

### `source_observations`

Purpose: immutable fetched/imported fixture evidence unit.

Primary key: `observation_id`

Foreign keys:

- `source_id -> sources.source_id`
- `batch_id -> batches.batch_id`
- `cost_event_id -> cost_events.cost_event_id`

Required lineage columns:

- `source_id`
- `batch_id`
- `payload_hash_sha256`
- `snippet_or_excerpt`

Append-only: yes. SQLite fixture DDL creates update/delete abort triggers for this table.

### `entities`

Purpose: canonical candidate business/person/contact container.

Primary key: `entity_id`

Foreign keys:

- `created_from_observation_id -> source_observations.observation_id`

Key columns:

- `entity_type`
- `canonical_label`
- `normalized_key`
- `status`
- `created_from_observation_id`
- `created_at_utc`
- `schema_version`

### `facts`

Purpose: field-level assertion with source and gate lineage. Corrections supersede, never overwrite.

Primary key: `fact_id`

Foreign keys:

- `entity_id -> entities.entity_id`
- `source_observation_id -> source_observations.observation_id`
- `promoted_by_gate_event_id -> verification_events.verification_event_id`
- `supersedes_fact_id -> facts.fact_id`

Required lineage columns:

- `entity_id`
- `source_observation_id`
- `field_relevant_snippet`
- `promoted_by_gate_event_id`
- `supersedes_fact_id`

Append-only: yes. SQLite fixture DDL creates update/delete abort triggers for this table.

Promotion statuses in Phase 1 are limited to:

- `raw_discovery`
- `identity_candidate`
- `icp_candidate`
- `contact_candidate`
- `verified_contact`
- `qualified_lead_review_required`
- `outreach_ready_internal_only`

`live_outreach_ready_future_phase` is explicitly blocked by `assert_phase1_promotion_allowed(...)` and is not accepted by the Phase 1 `facts.promotion_status` contract.

### `identity_edges`

Purpose: non-destructive identity resolution and duplicate handling.

Primary key: `edge_id`

Foreign keys:

- `from_entity_id -> entities.entity_id`
- `to_entity_id -> entities.entity_id`
- `superseded_by_edge_id -> identity_edges.edge_id`

Required lineage columns:

- `from_entity_id`
- `to_entity_id`
- `evidence_fact_ids`

Append-only: yes.

### `verification_events`

Purpose: deterministic fixture gate and future replayable verifier result ledger.

Primary key: `verification_event_id`

Foreign keys:

- `batch_id -> batches.batch_id`
- `target_entity_id -> entities.entity_id`
- `target_fact_id -> facts.fact_id`
- `cost_event_id -> cost_events.cost_event_id`

Required lineage columns:

- `target_entity_id`
- `target_fact_id`
- `input_hash`
- `result_status`

Append-only: yes.

### `enrichment_events`

Purpose: evidence-backed classification/enrichment event. It is never primary evidence by itself.

Primary key: `enrichment_event_id`

Foreign keys:

- `batch_id -> batches.batch_id`
- `entity_id -> entities.entity_id`
- `cost_event_id -> cost_events.cost_event_id`

Required lineage columns:

- `entity_id`
- `input_fact_ids`
- `input_observation_ids`
- `validator_status`

Append-only: yes.

### `suppression_events`

Purpose: fail-closed suppression snapshot ledger.

Primary key: `suppression_event_id`

Foreign keys:

- `batch_id -> batches.batch_id`
- `target_entity_id -> entities.entity_id`
- `target_fact_id -> facts.fact_id`

Required lineage columns:

- `target_entity_id`
- `target_fact_id`
- `suppression_snapshot_hash`
- `status`

Append-only: yes.

### `export_events`

Purpose: immutable internal/no-send export handoff record.

Primary key: `export_event_id`

Foreign keys:

- `batch_id -> batches.batch_id`
- `lead_entity_id -> entities.entity_id`
- `suppression_event_id -> suppression_events.suppression_event_id`
- `enrichment_event_id -> enrichment_events.enrichment_event_id`
- `human_review_event_id -> human_review_events.review_event_id`

Required lineage columns:

- `lead_entity_id`
- `included_fact_ids`
- `included_source_observation_ids`
- `included_verification_event_ids`
- `suppression_event_id`
- `human_review_event_id`

Safety fields:

- `signed_payload_hash_sha256`
- `signature`
- `send_authorized`
- `status`
- `blocked_reason`

`send_authorized` defaults to `False`, and SQLite fixture DDL adds `CHECK (send_authorized = 0)`. Phase 1 can create internal review/export records but cannot represent a send-authorized live outreach event.

Append-only: yes.

### `cost_events`

Purpose: per-batch/per-row/per-stage cost and waste attribution.

Primary key: `cost_event_id`

Foreign keys:

- `batch_id -> batches.batch_id`
- `entity_id -> entities.entity_id`

Required lineage columns:

- `batch_id`
- `entity_id`
- `stage`
- `provider_or_tool`

Append-only: yes.

### `human_review_events`

Purpose: explicit review decision for edge cases and internal readiness.

Primary key: `review_event_id`

Foreign keys:

- `entity_id -> entities.entity_id`

Required lineage columns:

- `entity_id`
- `decision`
- `input_fact_ids`

Append-only: yes.

## Enum contracts

### Source classes

- `official_licensing_registry`
- `first_party_website`
- `search_or_places_discovery`
- `aggregator_directory`
- `social_profile`
- `scraped_public_page_future_phase`
- `purchased_vendor_raw_seed`
- `user_import`

### Entity kinds

- `business`
- `person`
- `domain`
- `phone`
- `email`
- `address`
- `source_profile`

### Fact fields

- `business_name`
- `legal_name`
- `dba_name`
- `website_url`
- `service_area`
- `trade_category`
- `vertical_fit_label`
- `license_class`
- `contact_email`
- `contact_role`
- `permitassist_icp_segment`
- `low_call_relevance_signal`
- `contractor_software_signal`

### Promotion tiers

Phase 1 allowed:

- `raw_discovery`
- `identity_candidate`
- `icp_candidate`
- `contact_candidate`
- `verified_contact`
- `qualified_lead_review_required`
- `outreach_ready_internal_only`

Future/live outreach reserved and blocked in Phase 1:

- `live_outreach_ready_future_phase`

### Gate statuses

- `pass`
- `fail_closed`
- `blocked_source_policy`
- `review_required`
- `unknown_not_promoted`
- `reserved_future_phase`

### Suppression statuses

- `clear`
- `suppressed_email`
- `suppressed_domain`
- `duplicate_in_campaign`
- `not_interested`
- `suppression_conflict_hold`
- `suppression_unknown_hold`

### Export eligibility

- `not_exportable`
- `blocked_unknown`
- `blocked_suppressed`
- `blocked_uncited`
- `blocked_unverified_contact`
- `review_required`
- `internal_review_only`
- `future_live_outreach_reserved`

### Cost stages

- `raw_collection`
- `identity_resolution`
- `contact_discovery`
- `contact_verification`
- `icp_classification`
- `enrichment_validation`
- `suppression_check`
- `human_review`
- `export_preparation`
- `waste_allocation`

## Safety guarantees represented by tests

`tests/lead_pipeline/test_phase1_schema_contract.py` verifies:

- all required table contracts exist;
- schema version is present on every table;
- required primary keys are marked required;
- required enums expose stable contract values;
- `live_outreach_ready_future_phase` cannot be promoted in Phase 1;
- `facts` preserve entity, source observation, gate event, field snippet, and supersession lineage;
- event tables preserve lineage metadata;
- append-only tables receive SQLite update/delete guards;
- SQLite foreign keys reject fact rows without required entity/observation lineage;
- package imports do not include network/provider modules such as `requests`, `httpx`, `urllib`, `socket`, SMTP, DNS, browser automation, Firecrawl, or Brave.

## Boundary confirmation

Milestone 1 is a schema/docs/tests skeleton only. It performs no live fetches, no network calls, no scraping, no paid API calls, no Apollo calls, no SMTP probing, no outreach, no customer-visible actions, no production data mutation, no PermitAssist runtime behavior change, no Railway mutation, no GitHub remote action, no Hermes core/config/env/gateway change, and no ColdForge action.
