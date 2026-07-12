# Permit Rule Engine — exact Part 2 handoff

## Immutable starting point

Start Part 2 only from the local annotated tag:

```text
permit-rule-engine-part1-20260712
```

Resolve and record it before editing:

```bash
git rev-parse permit-rule-engine-part1-20260712^{commit}
git status --short
```

The worktree must be clean. Do not substitute another branch tip or later commit.

Inspected pre-Part-1 base:

```text
facea233ac4f821304288f14137e472e92a6fcfc
```

Controlling plan:

```text
artifacts/permit_rule_engine_4_session_plan_20260711/LOCKED_FOUR_SESSION_PLAN.md
```

## Part 1 contract that Part 2 must preserve

- Legacy behavior remains authoritative; Part 1 introduces no serving activation.
- `DecisionEnvelope` records are frozen, versioned, deterministic, Python-3.9-compatible contracts.
- The shadow adapter is disabled unless `PERMITASSIST_RULE_ENGINE_SHADOW=shadow` exactly.
- Shadow preparation and observation are exception-isolated and never merge into customer/cache payloads.
- Flag-off and shadow-on replay payloads are insertion-order JSON-byte identical.
- Cached and fresh research paths, including cache writes, are byte-identical off/on in the frozen tests; shadow adds no model call.
- The source package manifest file SHA-256 is pinned in each envelope.
- Validation success and strict coverage classification remain separate.
- All 2,162 shipped v2.4 cells are classified exactly once using request-time-equivalent portable validation.
- Commercial tenant improvement remains subject to the locked ten-lane W4 closure boundary: building, electrical, plumbing, mechanical, fire, health, liquor, wastewater, occupancy/CO, zoning/planning. Every potentially activated lane requires explicit supported closure; absent closure cannot be `EXACT_COMPLETE`.
- Missing evidence, effective dates, authority, routes, family closure, unsupported scope, ambiguous jurisdiction, or validation cannot be promoted by generic/model inference.
- The ten locked divergence classes and their versioned names are stable evidence keys.
- Part 1 evidence is deterministic, local, synthetic/non-customer, and independent of live APIs/models.

Final Part 1 census at checkpoint preparation:

| Metric | Value |
|---|---:|
| Shipped cells | 2,162 |
| Portable request validation failures | 0 |
| `EXACT_COMPLETE` | 36 |
| `EXACT_PARTIAL` | 2,092 |
| `FAIL_CLOSED` | 34 |
| Frozen replay cases | 12 |
| Flag-off envelopes | 0 |
| Shadow envelopes | 12 |
| Byte-parity cases | 12 |
| Payload mutations | 0 |

Authoritative Part 1 artifacts:

```text
artifacts/permit_rule_engine_part1_20260711/generated/manifest.json
artifacts/permit_rule_engine_part1_20260711/generated/summary.json
artifacts/permit_rule_engine_part1_20260711/generation_determinism.json
artifacts/permit_rule_engine_part1_20260711/baseline_failure_comparison.json
artifacts/permit_rule_engine_part1_20260711/secret_scan_disposition.json
artifacts/permit_rule_engine_part1_20260711/final_opus_review.txt
```

## Exact Part 2 scope — do not reorder, shrink, or defer

Complete only Part 2 / Session 2 from the locked plan:

1. Implement stable jurisdiction identity plus explicit candidate-set and ambiguity behavior.
2. Implement per-family authority routing.
3. Implement the closed scope ontology and deterministic work-atom, polarity, threshold, and fact validation.
4. Implement one strict precedence funnel, in this exact order:
   1. validated exact complete;
   2. validated exact partial;
   3. exact fail-closed;
   4. query-time official evidence meeting the same evidence floor;
   5. internal abstain/verified partial.
5. Prevent generic rules, classifiers, and Luna from manufacturing regulated truth.
6. Introduce envelope/cache schema versioning and stale-cache rejection.
7. Implement one sealed projection for every public mirror, report/share surface, and renderer input.
8. Keep the entire Part 2 path behind disabled/allowlisted flags.
9. Freeze RED and no-neuter fixtures before behavior edits.
10. Prove flag-off byte parity and flag-on invariants.
11. Obtain independent final exact-diff Opus review, remediate every blocker, rerun every gate, and create an immutable local Part 2 checkpoint.

## Mandatory Part 2 safety and quality constraints

- Do not activate Part 2 for production customers.
- Do not begin Part 3 migration/factory work.
- Do not push, deploy, mutate Railway/env/registry configuration, run paid Live100, or mutate customer/production data.
- No source-free `REQUIRED` or `NOT_REQUIRED` decision.
- No family removal, coverage neutering, generic map fallback, or authority downgrade to make tests pass.
- Weak dimensions become explicit conditional/verify/abstain outcomes; preserve proven top-line decisions and proven filing families.
- Preserve the exact Part 1 evidence and baseline artifacts; write Part 2 evidence to a new directory.
- Compare exact failure node-ID sets against `baseline_failure_comparison.json`, not only totals.
- A completion signal without accessible review findings is not approval.
- Final approval requires the exact final diff, real static/test/secret/determinism artifacts, and no unresolved Opus blocker.

## Required Part 2 closeout

End locally deploy-ready behind disabled flags, with:

- typed/versioned schema and cache migration rules;
- jurisdiction/authority/scope/funnel evidence;
- sealed-projection parity across every public surface;
- RED/no-neuter and boundary replay artifacts;
- focused and broad exact-baseline test evidence;
- compile/static/secret/determinism evidence;
- final exact-diff Opus approval;
- immutable local Part 2 checkpoint and exact Part 3 handoff.

No push or deployment is authorized by this handoff.
