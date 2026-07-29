# PermitAssist Permit-Accuracy Benchmark v1.2

## Scope

Offline-only Session 1 measurement repair over the preserved v1.1 corpus.

- Inputs: 100 frozen cases and 500 preserved raw envelopes.
- Provider calls: forbidden and absent.
- PermitAssist runtime imports: forbidden and absent.
- Runtime/product behavior: unchanged.
- Corpus role: diagnosis/development only, never final promotion proof.

## Run

```bash
PYTHONDONTWRITEBYTECODE=1 python3 benchmarks/permit_accuracy_v1_2/benchmark_v12.py run
PYTHONDONTWRITEBYTECODE=1 python3 benchmarks/permit_accuracy_v1_2/benchmark_v12.py verify
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v benchmarks/permit_accuracy_v1_2/test_benchmark_v12.py
PYTHONDONTWRITEBYTECODE=1 python3 benchmarks/permit_accuracy_v1_2/independent_review_v12.py
```

`verify` regenerates the five derived artifacts into a temporary directory and requires byte identity.

## Metric definitions

### Decision accuracy

Exact match over all 100 frozen cases.

Separately report:

- abstentions (`VERIFY` or `NEEDS_INPUT`);
- confident `REQUIRED ↔ NOT_REQUIRED` flips.

An abstention is not counted as a correct in-scope answer.

### Primary-family accuracy

Exact canonical-enum match over all 100 cases. `NO_PRIMARY_PERMIT` is first-class. Display strings are never scored when a typed runtime family exists.

### Companion precision

At `(canonical family, status)` granularity for emitted `REQUIRED` or `CONDITIONAL` statuses:

```text
TP / (TP + FP)
```

Only source-supportable, metric-eligible truth cases enter the denominator. Truth-incomplete cases do not create false positives.

### Companion REQUIRED recall

Truth-REQUIRED families emitted as REQUIRED. If the truth denominator is zero, the result is `null` / **N/A**, never fabricated as 0% or 100%.

### Companion CONDITIONAL recall

Truth-CONDITIONAL families emitted as CONDITIONAL. Status inflation to REQUIRED is not a true positive.

### Complete-set exact match

Exact family/status set on metric-eligible cases. This is secondary and cannot override precision, recall, or dangerous-omission reporting.

### Dangerous omission

A truth-REQUIRED companion absent with no same-family `REQUIRED`, `CONDITIONAL`, `NEEDS_INPUT`, or `VERIFY` placeholder. Primary required-family mismatches without abstention are reported separately.

### Abstention / needs-input

`VERIFY` and `NEEDS_INPUT` are preserved as explicit states, excluded from factual companion precision, and counted separately. They are not converted to a confident answer by the scorer.

## Frozen denominators

- Decision: 100 published case IDs.
- Primary family: the same 100 IDs.
- Companion P/R: four source-supportable non-empty cases, 18 CONDITIONAL items, zero REQUIRED items.
- Post-seal truth or denominator edits: prohibited.

The 93 original empty-companion cases are all `truth-incomplete`, not `confirmed-none`.

## Constant baselines

Always reported:

- constant `REQUIRED`: 95/100;
- constant `BUILDING`: 96/100;
- constant empty under legacy truth: 93/100, diagnostic only and explicitly invalid as a v1.2 companion headline;
- constant empty on v1.2 eligible cases: 0/4.

## Artifacts

- `SESSION1_OPENING_BASELINE.json` — pre-write repository baseline.
- `permit_family_ontology_v1.json` — canonical enum and adapters.
- `CANONICAL_PERMIT_FAMILY_ONTOLOGY_SPEC.md` — reviewed specification.
- `truth_audit_v12.json` — all 100 truth records with evidence and empty-set labels.
- `ontology_enum_closure.json` — truth/raw/cell label inventory.
- `scoreboard_v12.csv` — 500 offline-rescored rows.
- `offline_rescore_summary_v12.json` — metrics, baselines, and frozen denominators.
- `mismatch_forensics_v12.jsonl` — complete evidence-bound mismatch attribution.
- `SESSION1_FORENSICS_AND_TRUTH_AUDIT.md` — human audit.
- `INDEPENDENT_REVIEW_V12.json` — separate deterministic review.
- `SESSION1_MANIFEST.json` and `SESSION1_SHA256SUMS` — final seal.
- `SESSION1_CLOSEOUT.md` — binary gate closeout.

## Anti-neutering rules

- Empty truth cannot reward deleting companions.
- Source-backed companions cannot be suppressed to improve a metric.
- Unknown labels map to `VERIFY`, never to `BUILDING`.
- Measurement adapters do not mutate raw envelopes.
- Runtime adoption belongs to Session 2 and requires separate approval.
