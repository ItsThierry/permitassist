# Lead Pipeline Phase 1 M7 — Internal Export Contract

Milestone 7 is the first Phase 1 component allowed to write `export_events`.
It remains strictly internal-review-only: no sending, no outreach system, no
webhook, no CRM sync, no network, no paid API, no browser, no scraping, no DNS,
no SMTP, and no subprocess.

## Boundary

M7 consumes a persisted M6 promotion decision and writes one deterministic
append-only export ledger row for Boban/Titi internal review.

Allowed:

- read local SQLite fixture tables;
- verify the persisted M6 promotion event matches the in-memory decision;
- gather lineage IDs for facts, source observations, verification events,
  suppression event, and enrichment event;
- compute a deterministic canonical payload hash;
- write an `export_events` row with `send_authorized = 0` and
  `status = internal_review_only`.

Forbidden:

- sending email/messages/calls;
- live outreach authorization;
- network/DNS/SMTP/browser/scraping/subprocess access;
- paid-provider calls;
- CRM/webhook exports;
- exporting non-passed M6 decisions;
- exporting `review_required`, suppressed, uncited, unverified, or unenriched
  decisions;
- storing or signing secret-like values, including free-form `campaign_id` and
  `human_review_event_id` inputs;
- attaching a human-review event that belongs to another lead entity.

## Required Input State

A decision is exportable only when M6 produced and persisted all of these:

- `promotion_tier = outreach_ready_internal_only`
- `status = pass`
- `export_eligibility = internal_review_only`
- `send_authorized = false`
- a clear suppression event
- a validator-passed enrichment event with zero unsupported claims
- source-backed fact and observation lineage
- human review event must belong to the same exported lead entity, if present
- enrichment event lineage must cover the exported fact and source-observation IDs
- a persisted M6 `verification_events` row whose `raw_result_ref` exactly
  matches the decision

Any mismatch fails closed with `InternalExportSafetyError` before an
`export_events` row is written.

## Export Row Shape

M7 writes to `export_events` only. The row contains lineage and integrity fields:

- `export_target = internal_review_queue`
- `export_schema_version = lead_pipeline_phase1_m7_internal_export_v1`
- `included_fact_ids`
- `included_source_observation_ids`
- `included_verification_event_ids`, including the M6 promotion event ID
- `suppression_event_id`
- `enrichment_event_id`
- `signed_payload_hash_sha256`
- `signature = phase1-local-signature:<hash>`
  - local tamper-evidence attestation, not a cryptographic non-repudiation
    signature
- `send_authorized = 0`
- `status = internal_review_only`
- `blocked_reason = no_send_internal_review_only`

It does **not** store an outreach body, send destination, message draft, or live
campaign instruction.

## Determinism / Idempotency

The canonical payload is sorted and hashed. The export event ID is derived from
that signed payload hash:

`exp_m7_internal_<first 24 sha256 chars>`

`write_internal_export_event` uses `INSERT OR IGNORE`, so rerunning the same M7
preparation returns `inserted = 0` after the first successful insert.

## Secret Safety

Before signing, M7 scans included fact rows, source observation rows, free-form
export parameters, the M6 promotion event, suppression event, enrichment event,
and optional human review event for secret-like text such as:

- `api_key = ...`
- `token = ...`
- `password = ...`
- `sk_live_...` / `sk_test_...`
- AWS access key style values
- bearer tokens
- Slack token style values

Any match blocks export.

## Primary API

- `build_internal_export_contract(conn, decision)`
  - side-effect-free builder;
  - requires a persisted matching M6 promotion event.

- `write_internal_export_event(conn, contract)`
  - writes exactly one append-only `export_events` row;
  - refuses any `send_authorized = true` contract.

- `prepare_internal_export(conn, batch_id, entity_id)`
  - reruns/persists M6 idempotently;
  - builds and writes the M7 export row idempotently.

## Verification

Covered by `tests/lead_pipeline/test_phase1_m7_internal_export.py`:

- no-network/no-outreach import scan;
- no persisted M6 event blocks export;
- verified-contact-but-not-enriched blocks export;
- duplicate/review-required blocks export;
- send-authorized and future-live decision objects block export;
- secret-like values block export, including free-form campaign/review IDs;
- wrong-entity or missing human review IDs block export;
- network-tainted M6 events block export;
- enrichment lineage that does not cover exported decision facts blocks export;
- unsupported export targets block export;
- tampered M6 event mismatch blocks export;
- positive path writes signed no-send internal-review export row;
- reruns are idempotent;
- tampered `send_authorized` contract is refused before DB write.
