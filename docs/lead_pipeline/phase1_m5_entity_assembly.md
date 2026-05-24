# PermitAssist Lead Pipeline Phase 1 M5 — Entity / Fact Assembly + Deterministic Dedupe

Status: fixture-only Milestone 5. Internal-review-only.

M5 adds the first local entity / fact assembly layer on top of M1 schema metadata, M3 fixture connector lineage, and the M4 SQLite event writer. It performs no network, DNS, SMTP, scraping, paid-provider, browser, subprocess, ColdForge, production, env mutation, push, merge, deploy, or outreach action.

## Scope

M5 introduces:

- `lead_pipeline.assembly`
  - `ASSEMBLY_VERSION = "lead_pipeline_phase1_m5_entity_assembly_v1"`
  - `AssemblySafetyError`
  - `AssemblyFixtureRecord`
  - `AssemblySkip`
  - `AssemblySummary`
  - `assemble_entities_and_facts(conn, *, batch_id, records)`
- `tests/lead_pipeline/test_phase1_m5_entity_assembly.py`
  - happy-path entity + fact lineage assertions;
  - unknown-field omission and absent / whitespace skip-with-reason;
  - missing-snippet / missing-identity fail-closed skips;
  - safety errors for unknown observation ids, wrong-batch observations, and non-fixture URLs;
  - deterministic dedupe by business-name, legal-name, and website-domain via `identity_edges`;
  - non-destructive dedupe lineage (both entities preserved);
  - explicit "never write `live_outreach_ready_future_phase`" assertion;
  - explicit "never touch `export_events` / `send_authorized`" assertion;
  - idempotent rerun with no UPDATE / DELETE;
  - module-level no-network-imports assertion.

All code is stdlib-only plus local `lead_pipeline` modules.

## Public API

```python
from lead_pipeline.assembly import (
    ASSEMBLY_VERSION,
    AssemblyFixtureRecord,
    AssemblySafetyError,
    AssemblySkip,
    AssemblySummary,
    assemble_entities_and_facts,
)
```

### `AssemblyFixtureRecord`

```python
@dataclass(frozen=True)
class AssemblyFixtureRecord:
    observation_id: str
    field_relevant_snippet: str
    business_fields: Mapping[str, Any]
```

One explicit synthetic fixture record:

- `observation_id` must reference an already-persisted, fixture-only `source_observations` row (typically inserted by `write_connector_run_result(...)` in M4).
- `field_relevant_snippet` is the cited snippet attached to **every** fact created from this record. Empty / whitespace snippets cause the whole record to be skipped with `missing_field_relevant_snippet`.
- `business_fields` is a mapping of explicit business fields. Only keys in the allow-list below are turned into facts; **unknown keys are silently omitted, never guessed**.

### Allowed fact fields

The assembler writes facts only for these `FactField` values, taken verbatim from the M1 contract:

| Field key | M1 `FactField` |
|-----------|----------------|
| `business_name` | `BUSINESS_NAME` |
| `legal_name` | `LEGAL_NAME` |
| `dba_name` | `DBA_NAME` |
| `website_url` | `WEBSITE_URL` |
| `service_area` | `SERVICE_AREA` |
| `trade_category` | `TRADE_CATEGORY` |
| `license_class` | `LICENSE_CLASS` |
| `contact_email` | `CONTACT_EMAIL` |
| `permitassist_icp_segment` | `PERMITASSIST_ICP_SEGMENT` |
| `low_call_relevance_signal` | `LOW_CALL_RELEVANCE_SIGNAL` |

Empty strings, whitespace-only strings, `None`, and non-string values are skipped per-field (the rest of the record is still assembled). Keys not in this allow-list are dropped silently.

### Required identity fields

A record must carry at least one of:

- `business_name`
- `legal_name`
- `dba_name`
- `website_url` (resolvable to a non-empty domain)

If none of those is present and non-empty, the whole record is skipped with reason `missing_business_identity`. The assembler **never** infers a name from text.

### `assemble_entities_and_facts(conn, *, batch_id, records) -> AssemblySummary`

For each input record the function:

1. Loads the persisted `source_observations` row for `record.observation_id`.
2. Refuses (raises `AssemblySafetyError`) if the row is missing, belongs to a different `batch_id`, has a non-fixture `url_or_path`, or carries a drifted `schema_version`.
3. Skips (records to `AssemblySummary.skip_details`) if `field_relevant_snippet` is empty or no identity field is present.
4. Inserts one `entities` row with `entity_type = "business"`, `status = "raw_discovery"`, `created_from_observation_id = observation_id`.
5. Inserts one `facts` row per known non-empty business field, each with `promotion_status = "raw_discovery"`, the record's `source_observation_id`, and the record's `field_relevant_snippet`.
6. Runs deterministic dedupe (below) and writes `identity_edges` rows for duplicate candidates instead of overwriting / deleting either entity.
7. Commits and returns an `AssemblySummary`.

The writer side of M5 is built directly on `sqlite3` and `INSERT OR IGNORE`. It never issues `UPDATE` or `DELETE`, so duplicate runs are no-ops on already-present rows (see "Append-only / idempotent" below).

### `AssemblySummary`

```python
@dataclass(frozen=True)
class AssemblySummary:
    batch_id: str
    entities_inserted: int
    facts_inserted: int
    identity_edges_inserted: int
    records_skipped: int
    records_review_required: int
    skip_details: tuple[AssemblySkip, ...] = ()
```

`records_review_required` counts input records that produced at least one new `identity_edges` row (i.e. records that became dedupe partners of an earlier record in the same call). `skip_details` is a deterministic, fully-attributable list of why each skipped record was dropped — the assembler never silently discards an input.

## Deterministic dedupe via `identity_edges`

Records are deduplicated by three normalized keys, in priority order:

1. `business_name` — text lowercased, punctuation stripped, whitespace collapsed, trailing legal-suffix tokens (`inc`, `llc`, `ltd`, `co`, `corp`, `corporation`, `company`, `llp`, `l.l.c`, `incorporated`, `limited`) removed.
2. `legal_name` — same normalization as `business_name`.
3. `website_domain` — host parsed from the URL, lowercased, `www.` prefix stripped.

When a record shares a normalized key with an *earlier* record in the same call, the assembler:

- Creates a **second** entity for the new record (preserving its own per-observation lineage — no overwrite, no delete).
- Writes a single `identity_edges` row with:
  - `edge_type = "duplicate_candidate"`
  - `match_key = <normalized matching value>` (e.g. `"fixture build group"` or `"fixturebuild.example"`)
  - `review_status = "review_required"` (M2 `GateStatus.REVIEW_REQUIRED`)
  - `from_entity_id` / `to_entity_id` sorted lexically so the edge is stable across input order
  - `evidence_fact_ids` set to a JSON-serialized, sorted list of the `fact_id`s that established the match key (one from each side)
  - `created_by = "phase1_m5_entity_assembler"`

If a pair of records shares multiple keys (e.g. same `business_name` *and* same `website_domain`), exactly one edge is written: the highest-priority matching key wins, and the same partner pair is not edge-written twice in the same call.

This dedupe is reversible: both entities, both fact sets, and the connecting edge are all present in the DB. Downstream gates (M2 + later milestones) can promote, fail, or merge them by writing additional `verification_events` and superseding `identity_edges` rows — they never have to recover deleted state.

## Phase 1 promotion tiers — what M5 may and may not write

M5 only writes the lowest two of the Phase 1 allowed tiers, never the future-phase tier:

- `entities.status` → `"raw_discovery"`
- `facts.promotion_status` → `"raw_discovery"`

M5 calls `assert_phase1_promotion_allowed(...)` on both values at module entry, so any future change that tried to use `live_outreach_ready_future_phase` would raise `Phase1ContractError` before any row is written.

M5 **does not write**:

- `verification_events` (M2 / later gate ledgers)
- `enrichment_events`
- `suppression_events`
- `export_events` (handoff records — explicitly outside Phase 1 M5)
- `human_review_events`

It also **never sets `send_authorized`** on any row anywhere. The `export_events.send_authorized` column has a `CHECK (send_authorized = 0)` constraint at the M1 schema layer, but M5 sidesteps that table entirely so there is no possible code path that could authorize sending.

## Write-time safety boundary

The assembler re-enforces Phase 1 safety at the *assembly* boundary, on top of the M3 connector boundary and the M4 writer boundary:

1. Every referenced `observation_id` must already exist in `source_observations`.
2. Every referenced observation's persisted `batch_id` must equal the `batch_id` passed to `assemble_entities_and_facts(...)`, so the returned `AssemblySummary.batch_id` cannot claim rows from another batch.
3. Every referenced observation's `url_or_path` must start with `fixture://` (defense in depth in case a non-fixture row was somehow inserted by a future caller that bypassed the M4 writer).
4. Every referenced observation's `schema_version` must equal `lead_pipeline_phase1_m1_v1`.
5. `entity_type`, `status`, and `promotion_status` literals are taken from the `EntityKind`, `FactField`, `PromotionTier`, and `GateStatus` enums — not from caller input — so drift in the input cannot leak into the persisted rows.
6. Phase 1 promotion-tier guardrails are asserted at module entry via `assert_phase1_promotion_allowed(...)`.

All observation failures raise `AssemblySafetyError` before any insert; promotion-tier failures raise `Phase1ContractError` before any insert.

## Append-only / idempotent

`entities` is a candidate-container table (not append-only at the trigger layer in M1), and `facts` / `identity_edges` are append-only with M1 SQLite triggers. The assembler still treats all three as idempotent: row IDs are deterministic stable hashes of their content, so a second `assemble_entities_and_facts(...)` call with the same records writes zero new rows and the M5 `AssemblySummary` correctly reports `entities_inserted = facts_inserted = identity_edges_inserted = 0`. The DB snapshot is byte-identical before and after the rerun.

## Boundary confirmation

M5 imports no HTTP, DNS, SMTP, browser, subprocess, or paid-provider clients. The test
`test_assembly_module_has_no_network_or_paid_imports` AST-scans `lead_pipeline/assembly.py` for the same blocked-import set used by M1 and M4. The only side effect is rows inserted into a caller-provided `sqlite3.Connection` (typically the in-memory or local file connection initialized by `initialize_sqlite_schema(...)` from M4).

## Relationship to later milestones

- **M6 / controller** can sequence one or more `run_fixture_connector(...)` → `write_connector_run_result(...)` → `assemble_entities_and_facts(...)` triples per approved fixture batch, then summarize across batches using the deterministic `AssemblySummary` values.
- **M2 gates** can be run against the persisted entities and facts to populate `verification_events` rows that promote tiers (raw discovery → identity candidate → ICP candidate → ...), respecting the existing fail-closed behavior.
- **Future live phase** can layer additional event types onto the same SQLite schema, but the M5 assembler deliberately produces no rows that authorize live outreach. The hard ceiling remains internal review only.
