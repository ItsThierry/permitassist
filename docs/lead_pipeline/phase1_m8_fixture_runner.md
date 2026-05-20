# Phase 1 M8 — End-to-End Internal Review Runner

## Purpose

M8 proves that the Phase 1 Lead Pipeline can run as one deterministic local flow from M1 schema initialization through M7 internal export, without live data or outreach.

The runner is intentionally narrow:

- fixture-only synthetic inputs;
- in-memory/local SQLite only;
- no network, DNS, SMTP, browser, scraping, subprocess, CRM, webhook, or paid API calls;
- no customer-visible output;
- no live outreach readiness;
- `send_authorized=false` everywhere.

The goal is to validate joins, lineage, promotion behavior, export eligibility, and no-send safety before any real-data pilot.

## Version

`lead_pipeline_phase1_m8_fixture_runner_v1`

## Entrypoints

Python API:

```python
from lead_pipeline.phase1_runner import run_phase1_fixture_pipeline

result = run_phase1_fixture_pipeline(fixture_id="golden")
summary = result.to_dict()
```

CLI:

```bash
python -m lead_pipeline.run_phase1_fixture_pipeline --fixture golden --format json
```

Only `fixture_id="golden"` is supported in M8. Unknown fixture IDs fail closed before writing rows.

## Golden fixture shape

The M8 golden fixture creates one batch using the `contractor_first_party_website` fixture connector and three synthetic observations/entities:

1. `Fixture Build Group`
   - Complete source-backed identity, ICP evidence, observed contact email, deterministic contact verifier passes, clear suppression snapshot, and cited enrichment.
   - Expected result: `outreach_ready_internal_only` / `internal_review_only`.
   - M7 writes one `export_events` row for `internal_review_queue`.

2. `Quiet Clinic Contractors`
   - Source-backed identity and ICP evidence but no observed contact email.
   - Expected result: blocked/not exported with reason `missing_contact_candidate`.

3. `Suppressed TI Contractors`
   - Source-backed identity, ICP evidence, observed contact email, deterministic verifier passes, and cited enrichment, but suppression is `suppressed_email`.
   - Expected result: blocked/not exported with reason `suppression_blocks_promotion`.

## Summary contract

`run_phase1_fixture_pipeline(...).to_dict()` returns a JSON-safe summary with:

- `runner_version`
- `fixture_id`
- `batch_id`
- `safety`
  - `fixture_only=true`
  - `network_used=false`
  - `paid_api_used=false`
  - `outreach_attempted=false`
  - `send_authorized=false`
- `connector_run_ids`
- `write_summary`
- `assembly_summary`
- `table_counts`
- `leads`
  - `entity_id`
  - `canonical_label`
  - `promotion_tier`
  - `gate_status`
  - `export_eligibility`
  - `reason_codes`
  - `eligible_fact_ids`
  - `source_observation_ids`
  - `verification_event_ids`
  - `suppression_event_id`
  - `enrichment_event_id`
  - `export_event_id`
  - `send_authorized`
- `internal_review_export_event_ids`
- `exported_lead_count`
- `blocked_lead_count`

## Safety invariants

M8 must remain deterministic and local:

- Connector mode is `fixture_only`.
- All fixture document URLs use `fixture://`.
- The connector result must report `network_used=false` and `send_authorized=false`.
- The M4 writer re-checks fixture connector safety before persistence.
- M5 assembly only turns explicit fixture fields into facts; it does not infer missing fields.
- M8 creates deterministic local verification, suppression, and enrichment fixture events with `network_used_flag=0`. These intermediate events are synthetic fixtures, not live M2 verifier/provider calls.
- M6 must persist exactly one promotion event per decision. On replay, M8 reuses an existing persisted M6 event only after comparing the persisted decision payload to a fresh deterministic evaluation; mismatches or malformed persisted JSON fail closed.
- M7 writes only `internal_review_queue` export rows with `status=internal_review_only` and `send_authorized=0`.
- The runner checks the final `export_events` ledger and fails if any row has `send_authorized != 0`.

## Idempotency

M8 supports replay on an existing initialized SQLite connection:

```python
import sqlite3
from lead_pipeline.event_writer import initialize_sqlite_schema
from lead_pipeline.phase1_runner import run_phase1_fixture_pipeline

conn = sqlite3.connect(":memory:")
initialize_sqlite_schema(conn)
first = run_phase1_fixture_pipeline(fixture_id="golden", conn=conn).to_dict()
second = run_phase1_fixture_pipeline(fixture_id="golden", conn=conn).to_dict()
```

The final table counts, lead summaries, and internal export event IDs must remain stable across replay. Deterministic primary keys plus `INSERT OR IGNORE` prevent duplicate event rows.

## Tests

M8 is covered by `tests/lead_pipeline/test_phase1_m8_fixture_runner.py`:

- version and no-network/no-outreach import hygiene;
- full M1→M7 golden fixture execution;
- exact exported and blocked lead outcomes;
- export event lineage and no-send payload assertions;
- idempotent replay on the same SQLite connection;
- tampered or malformed persisted M6 replay rows fail closed;
- unknown/missing-schema connections fail closed;
- persisted observations remain `fixture://` only;
- no network/no-outreach import hygiene;
- CLI JSON summary output.

Recommended verification before commit:

```bash
python -m compileall -q lead_pipeline tests/lead_pipeline/test_phase1_m8_fixture_runner.py
python -m pytest tests/lead_pipeline/test_phase1_m8_fixture_runner.py -q
python -m pytest tests/lead_pipeline/test_phase1_m6_promotion.py tests/lead_pipeline/test_phase1_m7_internal_export.py tests/lead_pipeline/test_phase1_m8_fixture_runner.py -q
python -m pytest tests/lead_pipeline -q
python -m pytest -q
```
