# PermitAssist v2.4 — Schema + Factory Build Artifact

This is the local implementation artifact for the v2.4 contract. It is **not** a prod import/deploy. It defines the machine boundary required before any 2,489-cell enrichment run starts.

## Why v2.3.1 was not enough

v2.3.1 answered the main binary decision for exact AHJ/project cells, but it was thin around the fields where GPT-5.5 live research can beat us:

- full triggered permit classes beyond the primary building permit;
- per-trade authority routing, especially split authority and negative knowledge;
- exact apply office/live URL confidence;
- deterministic serving boundary preventing GPT-5.4-mini/search enrichment from splicing in unsourced regulated claims.

The fix is **not** to maintain every fee/inspection/portal label forever. The fix is to make the Tier 1 spine impossible to beat on covered AHJs and make everything else opportunistic/non-blocking.

## Built artifacts

- `schema/permitassist_v24/fields.json`
  - Machine field registry.
  - Enforces: Tier 1 gates ship; Tier 2 never gates ship; all regulated fields require provenance if present.

- `schema/permitassist_v24/decision_cell.schema.json`
  - JSON Schema shape for a v2.4 Tiered Verified Decision Cell.

- `api/v24_decision_cells.py`
  - Deterministic merge-gate validator.
  - Deterministic runtime/customer assembler with regulated-field hash lock.
  - v2.3.1 → v2.4 DRAFT spine converter.
  - v2.3.1 index audit helper.

- `scripts/permitassist_v24_factory.py`
  - Local/staged CLI for registry check, v2.3.1 audit, DRAFT spine generation, and cell validation.

- `tests/test_v24_schema_factory.py`
  - Contract tests for field registry, merge gate, negative routing, assembler tamper rejection, Tier 2 non-blocking behavior, and v2.3.1 spine conversion.

## v2.4 cell states

- `DRAFT / SPINE_ONLY`
  - Migrated from v2.3.1 or partially enriched.
  - Not publishable as v2.4 Tier 1.

- `PUBLISHABLE / TIER1_COMPLETE`
  - All Tier 1 fields validated by deterministic merge gate.
  - May serve with truthful green verified label.

- `PUBLISHABLE / TIER1_PLUS_TIER2`
  - Tier 1 complete plus verified opportunistic Tier 2 data.

- `FAIL_CLOSED / FAIL_CLOSED`
  - Tier 1 cannot be verified, but the cell has a real office/contact route.
  - Never renders guessed permit/authority/fee/inspection claims.

## Tier 1: can-never-lose fields

Tier 1 gates ship:

1. `main_decision`
   - `REQUIRED` / `NOT_REQUIRED` only.
   - Positive official provenance required.
   - `NOT_REQUIRED` cells may not carry required/conditional permit rows or fabricated trade/apply routes.

2. `permits_required[]`
   - Existence of each required/conditional permit class.
   - Not exact portal label.
   - Every triggered permit kind must have a matching `trade_authority` route.

3. `trade_authority[]`
   - Per-trade issuing/application authority.
   - Negative routing is explicit and source/hash/quote verified with full provenance.
   - Absence is never treated as negative knowledge.

4. `apply[]`
   - Office + live URL/channel/contact.
   - `PUBLISHABLE` Tier 1 cells require at least one `url_status=live` apply route and a real URL check.
   - Exact portal category is not here; it is Tier 2.

5. `fail_closed`
   - If Tier 1 cannot be verified, cell must route to a real contact.

6. `change_watch`
   - Tier 1 snapshot hashes registered for future diffing.

## Tier 2: operational enrichment

Tier 2 never gates ship and has no completion SLA:

- `apply_path_detail[]` — exact portal category/path only if verified.
- `fee_basis[]` — official formula/amount/effective date only if verified.
- `inspections[]` — official inspection sequence only if verified.

If Tier 2 is absent, customer output omits it or says not verified. If Tier 2 is present, it still has to pass provenance validation.

## Runtime assembler rule

For covered v2.4 cells:

1. Build regulated payload deterministically from stored fields.
2. Hash regulated payload.
3. Optional model/rephraser may add narrative only.
4. If the rephraser changes any regulated field, discard it and use deterministic fallback.

This replaces the failure mode where an AI result is later cleaned by destructive string scrubbers.

## Factory flow

### Gate 0 — before scale

- Live runtime trace: prove prod covered lookups hit the cell at serve time.
- 171K distribution: identify covered-vs-fallback traffic split and priority order.
- Build can continue locally, but at-scale enrichment priority depends on this data.

### Gate 1 — narrow proof

- Build Worcester Tier 1 packet.
- Build Pittsburgh/split-authority Tier 1 packet.
- Head-to-head against GPT-5.5/Gemini on Tier 1 only.
- Lose on any Tier 1 field → fix schema/factory before scale.

### Gate 2 — 25 mixed cells

- Mixed states/project families/authority models.
- Must pass: quote/hash, live URL, negative routing, assembler lock, GPT adversarial diff on required permit classes.

### Gate 3 — all-current-AHJ enrichment launch gate

- Run the v2.4 factory over all current runtime/covered cells as staged-only output.
- Every cell must end as `PUBLISHABLE/TIER1_COMPLETE`, `PUBLISHABLE/TIER1_PLUS_TIER2`, or honest `FAIL_CLOSED`; never pad or fake Tier 1 completeness from v2.3.1 spine data.
- Produce script-computed counts, blocker classes, package/manifest hashes, merge-gate report, and customer-output/head-to-head smoke before any import/deploy.
- If Gate 1 + Gate 2 + Gate 3 pass, start nonstop Tier 1 enrichment for all AHJs we have, sorted by live traffic/coverage priority.
- Tier 2 is filled opportunistically only.
- No approved/compiled/runtime/prod/git/Railway/deploy/customer-visible mutation happens from Gate 3 without Boban's separate explicit approval.

## CLI examples

```bash
python3 scripts/permitassist_v24_factory.py registry-check
python3 scripts/permitassist_v24_factory.py audit-v231
python3 scripts/permitassist_v24_factory.py migrate-v231-spines --limit 25 --output artifacts/v24/spine_candidates_25.json
python3 scripts/permitassist_v24_factory.py validate-cell artifacts/v24/spine_candidates_25.json --allow-missing-snapshots
```

`--allow-missing-snapshots` is dry-run only. A real merge gate must not use it.

## Readiness meaning

When these tests pass, the **factory is ready to start enrichment**. It does **not** mean the 2,489 AHJs are already enriched. The next real run must produce v2.4 Tier 1 cells and pass the merge gate.
