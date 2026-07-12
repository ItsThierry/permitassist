# Permit Rule Engine Part 2 — immutable closeout

## Status

**PART 2: PASS**

Immutable local checkpoint tag:

```text
permit-rule-engine-part2-20260712
```

Resolve the exact checkpoint commit with:

```bash
git rev-parse permit-rule-engine-part2-20260712^{commit}
```

The checkpoint is local only. Nothing was pushed or deployed, no production configuration was changed, and Part 2 activation remains disabled by default.

## Sealed ancestry

- Part 1 annotated tag: `permit-rule-engine-part1-20260712`
- Part 1 peeled commit: `1db644ee09e3a13e423462e5d8d8934d916dad71`
- Part 2 frozen RED checkpoint: `26bbc4aa131ff15ee9e4eb09f5a8aab2e1ac982b`
- Part 2 branch: `work/permit-rule-engine-part2-20260712`
- Controlling plan: `artifacts/permit_rule_engine_4_session_plan_20260711/LOCKED_FOUR_SESSION_PLAN.md`

## Delivered Part 2 contract

1. Stable jurisdiction candidate resolution, deterministic deduplication/sorting, and ambiguity abstention.
2. Per-family issuing/application authority routing.
3. Closed work ontology with polarity, threshold, declared-fact, and invalid-scope validation.
4. One closed precedence funnel in the locked order:
   - validated exact complete;
   - validated exact partial;
   - exact fail-closed;
   - jurisdiction-bound official query evidence;
   - internal abstain.
5. Source floors that prevent source-free `REQUIRED` and `NOT_REQUIRED` decisions.
6. Independently versioned Part 2 envelope, projection, and cache schemas with stale/tampered cache rejection.
7. One SHA-256-sealed customer projection controlling API, report, share, renderer, and checklist mirrors.
8. Customer route-provenance allowlisting that excludes internal metadata.
9. Disabled-by-default activation requiring both exact `active` mode and an exact jurisdiction-ID allowlist hit.
10. Flag-off byte parity with sealed Part 1.

## Final gates

| Gate | Final result |
|---|---:|
| Focused Part 1 + Part 2 + integration | **32 passed** |
| Full repository suite | **1,109 passed, 47 skipped, 18 failed** |
| Sealed Part 1 baseline failures | **18** |
| New failure node IDs | **0** |
| Removed failure node IDs | **0** |
| Exact baseline failure-set match | **true** |
| Flag-off payload byte parity | **true** |
| Base/Part 2 payload SHA-256 | `422b974f02ad7689e031bf474d07bed833d63a4b5b58ef0fe261bed2e16d1e40` |
| Deterministic evidence rerun | **byte-identical** |
| Shipped cell census | **2,162** |
| Stable candidate violations | **0** |
| Source-free binary violations | **0** |
| Fail-closed/internal-abstain binary violations | **0** |
| Missing core-family route violations | **0** |
| Public internal-metadata violations | **0** |
| Secret-contract tests | **3 passed** |
| Added-line secret scan | **0 hits** |
| Compile/static/diff checks | **passed** |

The unchanged 18 full-suite failures are the sealed Part 1 stale evidence-pack baseline in `test_solar_mep_controlled_activation_runtime.py` and `test_step7c_evidence_pack_local_gates.py`. Their exact node-ID set matches Part 1; Part 2 introduced no failure.

## Opus 4.8 disposition

- First exact-diff model: `claude-opus-4-8`
- First verdict: `NOT_READY`
- Blocker: `EXACT_FAIL_CLOSED` abstained only the main verdict while retaining binary family rows.
- Remediation: every fail-closed family is now rebuilt as `ABSTAIN`; customer mirrors carry `required_status=ABSTAIN`, `required=maybe`, and top-line `permit_required=null`.
- Additional hardening: customer family authority routes redact internal provenance metadata.
- Final exact-diff model: `claude-opus-4-8`
- Final verdict: **READY**
- Final blockers: **NONE**

Authoritative final review:

```text
artifacts/permit_rule_engine_part2_20260712/review/final/opus_4_8_final_review.md
```

## Production boundary

- Part 2 is locally deploy-ready only behind disabled flags.
- No customer or production activation occurred.
- No push, deployment, Railway/environment/registry mutation, paid Live100 run, or customer-data mutation occurred.
- Do not activate any jurisdiction without the Part 3 migration/reverification and pre-activation non-core-family audit required by the final review.
- Do not begin Part 4 from this checkpoint.

## Next handoff

Use `artifacts/permit_rule_engine_part2_20260712/PART3_HANDOFF.md` verbatim for Session 3.
