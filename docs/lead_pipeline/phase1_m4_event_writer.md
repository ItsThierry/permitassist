# PermitAssist Lead Pipeline Phase 1 M4 — SQLite Event Writer / Persistence Layer

Status: fixture-only Milestone 4.

M4 introduces a small deterministic stdlib-only persistence layer that writes the M3 `ConnectorRunResult` into the M1 SQLite schema. It performs no network, DNS, SMTP, scraping, paid-provider, ColdForge, production, env mutation, push, merge, deploy, or outreach action.

## Scope

M4 adds:

- `lead_pipeline.event_writer`
  - `PERSISTENCE_VERSION = "lead_pipeline_phase1_m4_event_writer_v1"`
  - `PersistenceSafetyError`
  - `WriteSummary`
  - `initialize_sqlite_schema(conn)`
  - `write_connector_run_result(conn, result, *, batch)`
- `tests/lead_pipeline/test_phase1_m4_event_writer.py`
  - schema initialization with foreign keys ON
  - one-shot insert across `batches`, `sources`, `cost_events`, `source_observations`
  - deterministic summary row counts
  - idempotent duplicate runs report duplicates without mutation
  - eight write-time safety boundaries (network, send, mode, blocked / unknown connector, fixture URL prefix, schema version, allowed phase, paid / login policy)
  - batch payload sanity (`batch_id` match, schema_version)
  - module-level no-network-imports assertion

All code is stdlib-only plus local `lead_pipeline` modules.

## Public API

```python
from lead_pipeline.event_writer import (
    PERSISTENCE_VERSION,
    PersistenceSafetyError,
    WriteSummary,
    initialize_sqlite_schema,
    write_connector_run_result,
)
```

### `initialize_sqlite_schema(conn)`

Applies the M1 fixture DDL on a `sqlite3.Connection` and explicitly runs `PRAGMA foreign_keys = ON` outside the schema script so FK enforcement is active at insert time. The schema content is sourced from `lead_pipeline.schema.create_sqlite_schema()`.

### `write_connector_run_result(conn, result, *, batch)`

Persists an M3 `ConnectorRunResult` into the four tables that M3 emits payloads for:

1. `batches`
2. `sources`
3. `cost_events`
4. `source_observations`

The order respects foreign keys: `source_observations` references both `sources` and `cost_events`, both of which reference `batches`.

The writer never issues `UPDATE` or `DELETE`. Duplicate primary keys are silently absorbed via `INSERT OR IGNORE` and counted in the returned `WriteSummary`. The decision recorded for this milestone is **"report as duplicate without mutation"** — chosen so that the M5 controller can rerun a deterministic batch without losing append-only safety.

### `WriteSummary`

```python
@dataclass(frozen=True)
class WriteSummary:
    batch_id: str
    connector_run_id: str
    batches_inserted: int
    sources_inserted: int
    cost_events_inserted: int
    observations_inserted: int
    batches_duplicate: int
    sources_duplicate: int
    cost_events_duplicate: int
    observations_duplicate: int
```

The summary is deterministic for a given input: the same `ConnectorRunResult` and `batch` payload always produce the same counts on a clean DB and the same duplicate counts on a re-run.

## Write-time safety boundary

M4 deliberately re-checks Phase 1 safety at the *write* boundary, not just at the connector boundary. A drifted payload that somehow reaches the writer must fail closed before any row is inserted. The checks are run in order and the first failure raises `PersistenceSafetyError`:

1. `result.network_used` must be `False`.
2. `result.send_authorized` must be `False`.
3. `result.mode` must equal `FetchMode.FIXTURE_ONLY.value` (`"fixture_only"`).
4. `result.connector_id` must resolve via `enforce_adapter_policy_for_connector(...)` — blocked or unknown ids raise.
5. `result.source["base_url_or_path"]` must start with `fixture://`.
6. `result.source["allowed_phase"]` must equal `phase1_fixture_only`.
7. `result.source["paid_flag"]` and `result.source["requires_login"]` must both be `0`.
8. `result.source["schema_version"]`, every observation's `schema_version`, and every cost event's `schema_version` must equal `lead_pipeline_phase1_m1_v1`.
9. Every observation's `url_or_path` must start with `fixture://`.
10. The `batch` payload's `batch_id` must match the (single, consistent) `batch_id` used by all observations and cost events emitted by the connector.
11. The `batch` payload's `schema_version` (when present) must equal the M1 schema version, and the required fields `approved_scope_ref` and `started_at_utc` must be present.

These checks are intentionally redundant with M3's connector-time policy. The redundancy is the point: a future caller that bypasses the connector layer (for example, a backfill loader) must still be unable to insert a paid, login-gated, network-fetched, or live-send-authorized row into the M1 schema.

## Append-only semantics

`source_observations` and `cost_events` are append-only tables in M1. M4 relies on the M1 SQLite triggers (`<table>_append_only_no_update` and `<table>_append_only_no_delete`) to enforce that at the database boundary. The writer itself emits no `UPDATE` or `DELETE`, and duplicate IDs are reported in `WriteSummary.*_duplicate` counts rather than overwriting existing data.

A re-run with identical inputs is therefore safe:

```
first  = write_connector_run_result(conn, result, batch=batch)
# first.observations_inserted == len(result.observations)
second = write_connector_run_result(conn, result, batch=batch)
# second.observations_inserted == 0
# second.observations_duplicate == len(result.observations)
```

## Boundary confirmation

M4 is still a local fixture contract. It imports no HTTP, DNS, SMTP, browser, subprocess, or paid-provider clients. It performs no network, no filesystem write to user-owned paths, no production database write, no outreach, no remote GitHub action, no Railway action, and no production / customer-visible action. The only side effect is rows inserted into a caller-provided `sqlite3.Connection` (in-memory or local file).

## Relationship to later milestones

- **M5 / controller** can call `write_connector_run_result(...)` once per allowed connector run for a given approved fixture batch, then summarize counts across all calls using the deterministic `WriteSummary` values.
- **Future live phase** can layer additional event types (`facts`, `verification_events`, `enrichment_events`, `suppression_events`, `export_events`, `human_review_events`) on top of the same `initialize_sqlite_schema(...)` helper. M4 deliberately does not write those tables, because doing so would conflate M3 source/observation lineage with downstream gate, enrichment, suppression, and export decisions that have their own Boban-approved gating in later milestones.
