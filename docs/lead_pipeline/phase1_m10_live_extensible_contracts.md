# PermitAssist Lead Pipeline Phase 1 M10 — Live-Extensible Contracts Under Fixture-Only Boundaries

Status: local fixture-only contract milestone.

M10 adds contract surface for future live-capable lead collection without enabling live collection today. It keeps Phase 1 local-only: no network, scraping, DNS, SMTP, browser automation, paid APIs, CRM sync, outreach, production writes, customer-visible artifacts, or send authorization.

## Safety boundary

M10 does **not** authorize:

- live search or places APIs;
- paid provider spend;
- scraping or login-required sources;
- customer-visible exports;
- CRM writes;
- email/SMS/outreach sends;
- `send_authorized=true`;
- `live_outreach_ready_future_phase`.

All runnable connector paths remain `fixture://` only and emit `network_used=false`, `send_authorized=false`.

## New contract surface

M10 introduces or hardens:

- `PageType`
  - `first_party_homepage`
  - `first_party_services`
  - `first_party_contact`
  - `official_registry`
  - `search_result`
  - `aggregator_directory`
  - `blocked_unknown`
- `RobotsTermsPrefetchStatus`
  - `allowed_fixture_only`
  - `allowed_public`
  - `conditional_review_required`
  - `blocked`
- `FixtureSearchDiscoveryConnector` and `run_fixture_search_discovery(...)`
- `SearchDiscoveryFixtureQuery` / `FixtureSearchCandidate`
- `BatchCreditCeiling` / `enforce_batch_credit_ceiling(...)`
- `IDENTITY_MERGE_KEY_ORDER` / `identity_merge_key_order()`
- derived M9 cost summaries from `cost_events`

## Schema additions and enforcement

M10 extends fixture schema contracts with:

- `sources.terms_prefetch_status`
- `sources.robots_prefetch_status`
- `source_observations.page_type`
- `source_observations.terms_prefetch_status`
- `source_observations.robots_prefetch_status`
- `source_observations.raw_result_ref`

`raw_result_ref` is populated by search-discovery fixture rows that preserve raw result shape; non-search fixture connectors leave it null unless a future fixture explicitly supplies raw-result lineage.

The enum-backed fields are enforced by SQLite `CHECK (...)` constraints in the local fixture schema. Tests assert both field existence and rejection of invalid enum values.

## Search discovery policy

`fixture_search_discovery` is allowed only as synthetic raw candidate metadata. It is useful for future live-search shape testing, but it is not authoritative evidence.

Search-only evidence must stay review-held:

- promotion tier: `qualified_lead_review_required`
- gate status: `review_required`
- export eligibility: `review_required`
- reason code: `search_only_evidence_not_promotable`
- `send_authorized=false`

Search plus untrusted official/first-party-shaped evidence also stays review-held and emits both reason codes:

- `search_only_evidence_not_promotable`
- `untrusted_official_or_first_party_evidence_requires_corroboration`

Search plus approved `user_import` seed lineage is treated as approved seed lineage, not raw search-only evidence. It can reach `outreach_ready_internal_only` only through the existing internal-review-only path, with `send_authorized=false` and no live send authority.

## Source-class policy wording

The M3 `allowed_source_classes` / `blocked_source_classes` policy values are advisory registry validation lists. Runtime allow/block status is decided by connector id plus exact `ConnectorPolicyStatus`.

This matters because `search_or_places_discovery` appears in both advisory lists:

- `fixture_search_discovery` is allowed as fixture-only raw candidate metadata;
- `serper_google_search_api` and other live/paid search connectors remain blocked.

Do not use source class alone as an authorization gate.

## Cost safety

M9 artifact cost summaries are derived from local `cost_events`, not hardcoded placeholders.

The renderer fails closed unless every provider is one of the allowed fixture connector providers from `FIXTURE_CONNECTOR_REGISTRY` and every fixture cost is exactly `0.0`. A provider string that merely looks like `fixture_connector::<id>` is not enough unless `<id>` is an allowed fixture connector.

Rendered summaries include:

- `cost_event_count`
- `fixture_cost_event_count`
- per-provider `event_count`
- per-provider `units_consumed`
- per-provider `cost_usd`
- `live_provider_credits_authorized=0`
- `live_provider_credits_used=0`
- `live_provider_cost_usd=0.0`
- `paid_api_used=false`

## Batch credit ceiling

`BatchCreditCeiling` is a future-live guardrail represented locally. M10 tests assert fail-closed behavior for live-spend-shaped attempts, including:

- live mode with no explicit approval;
- live mode exceeding approved credit ceiling;
- kill switch enabled.

No paid or live provider is called by M10.

## Identity merge key order

`identity_merge_key_order()` exposes the approved strongest-to-weakest merge key order for future identity-resolution work:

1. license
2. secretary_of_state_entity_id
3. domain
4. phone
5. address
6. business_name

M10 records the contract and ordering only. Phase 1 remains non-destructive: unresolved identity ambiguity routes to human review rather than destructive merging.

## Reason-code and score constants

M10 names the review-hold scores used by promotion logic:

- `SEARCH_ONLY_HOLD_SCORE = 0.4`
- `UNTRUSTED_OFFICIAL_OR_FIRST_PARTY_HOLD_SCORE = 0.45`

Reason codes added/hardened by M10:

- `search_only_evidence_not_promotable`
- `aggregator_only_evidence_requires_corroboration`
- `untrusted_official_or_first_party_evidence_requires_corroboration`

## Test coverage

`tests/lead_pipeline/test_phase1_m10_live_extensible_contracts.py` asserts:

- new enums and schema fields exist;
- invalid M10 enum field values are rejected by schema `CHECK` constraints;
- fixture search discovery emits only fixture lineage and no network/send authority;
- search-only and weak-source evidence remains review-held;
- untrusted official/first-party-shaped evidence uses truthful reason codes;
- search plus untrusted official/first-party evidence emits both applicable reason codes;
- approved `user_import` seed lineage is not mislabeled as raw search-only evidence;
- batch credit ceilings fail closed for live-spend-shaped attempts;
- internal review artifacts include non-exported rows and derived zero-cost summaries;
- artifact rendering refuses non-fixture or nonzero cost events.

## Local verification command

From the repository root:

```bash
PYTHONPATH=. uv run --with pytest pytest tests/lead_pipeline/ -q
```
