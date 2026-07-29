# PermitAssist Permit-Accuracy Program — Session 1 Binary Closeout

## Binary verdict

# SESSION_1_COMPLETE

Session 1 passed every approved runtime/product-read-only gate. This is a benchmark/artifact closeout only. It is **not** permission to implement Session 2, modify runtime/customer behavior, call providers, rerun paid benchmarks, deploy, push, merge, commit, touch the registry, or operate Railway/production.

## Scope completed

- [x] Loaded and hash-verified the saved four-session plan.
- [x] Loaded and hash-verified the Fable 5 consultation.
- [x] Captured the opening dirty tree before any Session 1 write.
- [x] Preserved all pre-existing tracked and untracked user work.
- [x] Verified the sealed v1.1 corpus: 507/507 SHA-256 entries.
- [x] Audited all 100 truth records and all 500 preserved raw envelopes.
- [x] Classified every decision, primary-family, and companion mismatch.
- [x] Labeled all 93 original empty companion sets `confirmed-none` or `truth-incomplete`.
- [x] Defined a closed canonical permit-family ontology and adapters.
- [x] Implemented benchmark v1.2 measurement only under `benchmarks/permit_accuracy_v1_2/`.
- [x] Added deterministic/offline/no-network tests.
- [x] Rescored all preserved raws offline without provider calls.
- [x] Reported frozen denominators, constant baselines, abstentions, dangerous omissions, and companion P/R.
- [x] Ran independent deterministic review and remediated no out-of-scope code.
- [x] Proved zero Session 1 runtime/product diff.
- [x] Sealed artifacts with a manifest and SHA-256 checksum file.

## Authoritative input verification

| Artifact | Expected SHA-256 | Actual | Gate |
|---|---|---|---|
| `PERMIT_ACCURACY_FINAL_BOUNDED_PLAN.md` | `c799b18e51c108a40a12320fa6eb8d94e9f07de4843c60a176767eac64856029` | same | PASS |
| `FABLE5_BENCHMARK_AND_FIX_PLAN_CONSULT.md` | `e733aa4a4e16c03c39ba2fd7dfb9adcb5286c25fcb4d2cdfb697cb40af822155` | same | PASS |

Repository opening point:

- Branch: `fix/authority-timeout-successor-v11`
- HEAD: `8b0eb9bbfc51186a603d69295d482a6ab78fb8ea`
- Tracked worktree diff SHA-256: `5444d376d6ecfa51ec1f20357efa04e2178873a313dbbd49b7ed77a90d390662`
- Staged diff SHA-256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- Opening non-Session status digest: `f2c2b59746ee4418b38ef51902e1c6119058015f65d009bf5f9b2a8375a50542`

## Truth-audit gate

- Cases audited: **100/100**
- Empty original companion sets: **93**
- Empty `confirmed-none`: **0**
- Empty `truth-incomplete`: **93**
- Non-empty source-supportable companion cases admitted to v1.2 metric: **4**
- Source-supportable CONDITIONAL companion items: **18**
- Source-supportable REQUIRED companion items: **0**
- Truth corrections applied: **0**

Gate: **PASS**. Empty truth was never assumed correct. Required-companion recall is explicitly **N/A** because its frozen denominator is zero.

## Complete forensics gate

- One-to-one mismatch rows: **611**
- Decision mismatches: 142
- Primary-family mismatches: 110
- Legacy-truth companion mismatches: 359
- Missing category assignments: 0
- Raw hash/reference failures: 0

Gate: **PASS**.

## Canonical ontology gate

- Canonical permit-family values: 27
- Canonical statuses: 5
- Observed truth labels closed: 15
- Observed preserved runtime/model labels closed: 84
- Observed v2.4 Cell labels closed: 18
- Unmapped labels: 0
- Unknown-label behavior: `VERIFY`, never default `BUILDING`

Gate: **PASS**.

## Offline rescore

| Path | Decision exact | Primary-family exact | Companion precision* | CONDITIONAL recall* | REQUIRED recall* |
|---|---:|---:|---:|---:|---:|
| Engine | 96/100 | 96/100 | 0/9 | 0/18 | N/A |
| `engine_luna` | 99/100 | 98/100 | 0/9 | 0/18 | N/A |
| Grok | 64/100 | 85/100 | 10/11 | 10/18 | N/A |
| Gemini | 54/100 | 53/100 | 3/3 | 3/18 | N/A |
| Luna | 45/100 | 58/100 | 6/6 | 6/18 | N/A |

\* Frozen four-case, 18-item source-supportable companion denominator. Development/diagnostic only.

Constant baselines:

- Constant REQUIRED decision: 95/100
- Constant BUILDING family: 96/100
- Constant empty legacy companions: 93/100, explicitly invalid as a v1.2 headline
- Constant empty v1.2 eligible cases: 0/4

## Determinism and independent-review gates

Executed:

```text
v1.1 SHA-256 seal: 507/507 OK
v1.1 + v1.2 unit tests: 31 passed, 0 failed
v1.2 byte-identical offline regeneration: PASS
independent deterministic review: 29 checks, 0 failures, PASS
```

The independent reviewer does not import `benchmark_v12.py` or PermitAssist `api` code. It independently re-parses truth, scoreboard, forensics, raw hashes, enum closure, arithmetic, protected hashes, and boundary tokens.

## Zero runtime/product diff proof

Opening versus closeout:

| Proof | Opening | Closeout | Result |
|---|---|---|---|
| Tracked worktree diff SHA-256 | `5444d376...90662` | `5444d376...90662` | identical |
| Staged diff SHA-256 | `e3b0c442...b855` | `e3b0c442...b855` | identical |
| Non-Session porcelain-v1-z digest | `f2c2b597...0542` | `f2c2b597...0542` | identical |
| Non-Session status entries | 1189 | 1189 | identical |
| Nine protected runtime/product file hashes | opening set | opening set | identical |

Every Session 1-created file is under:

```text
benchmarks/permit_accuracy_v1_2/
```

No file under `api/`, customer UI/product code, `tests/`, registry packages, deployment configuration, Railway, or production was changed by Session 1.

## Prohibited actions confirmation

- Provider calls: **0**
- Paid benchmark reruns: **0**
- Raw recollection/regeneration: **0**
- Runtime/customer behavior patches: **0**
- Individual case patches: **0**
- Deployments: **0**
- Pushes/merges/commits: **0**
- Registry/Railway/production touches: **0**

## Carry-forward gate

Session 2 remains separately approval-gated. Session 1 authorizes no implementation or production action.

Final status: **SESSION_1_COMPLETE**
