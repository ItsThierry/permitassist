# Permit Rule Engine — exact Part 3 handoff

## Immutable starting point

Start Part 3 only from this local annotated tag:

```text
permit-rule-engine-part2-20260712
```

Before editing, resolve and record the tag and reverify the checkpoint:

```bash
git rev-parse permit-rule-engine-part2-20260712^{commit}
git rev-parse permit-rule-engine-part1-20260712^{commit}
git status --short
```

The worktree must be clean. Do not substitute another branch tip, merge later work, or start from an unsealed working tree.

Sealed Part 1 ancestry:

```text
permit-rule-engine-part1-20260712
1db644ee09e3a13e423462e5d8d8934d916dad71
```

Controlling plan:

```text
artifacts/permit_rule_engine_4_session_plan_20260711/LOCKED_FOUR_SESSION_PLAN.md
```

Part 2 checkpoint manifest:

```text
artifacts/permit_rule_engine_part2_20260712/CHECKPOINT_MANIFEST.json
```

## Part 1–2 contract that Part 3 must preserve

- Part 1 shadow remains independently disabled unless `PERMITASSIST_RULE_ENGINE_SHADOW=shadow` exactly.
- Part 2 serving remains independently disabled unless `PERMITASSIST_RULE_ENGINE_CORE=active` exactly **and** the selected stable jurisdiction ID is an exact member of `PERMITASSIST_RULE_ENGINE_CORE_ALLOWLIST`.
- Flag-off behavior is byte-identical to sealed Part 1; payload SHA-256 is `422b974f02ad7689e031bf474d07bed833d63a4b5b58ef0fe261bed2e16d1e40` on both sides.
- Jurisdiction candidate sets are deterministic, deduplicated, and sorted; ambiguous or uncovered jurisdictions cannot select an authority.
- Regulated decisions flow only through the closed precedence funnel: validated exact complete → validated exact partial → exact fail-closed → jurisdiction-bound official query evidence → internal abstain.
- `EXACT_FAIL_CLOSED` and `INTERNAL_ABSTAIN` cannot emit binary main or family decisions.
- Source-free `REQUIRED` and `NOT_REQUIRED` decisions are prohibited.
- Query-time evidence must identify the selected stable jurisdiction and meet the same official-source floor.
- The Part 2 envelope, sealed projection, and cache schemas are versioned independently; stale or hash-invalid rows are rejected.
- One validated sealed projection controls API, report, share, renderer, and checklist mirror fields; poisoned legacy fields cannot override it.
- Customer route provenance is allowlisted and cannot expose `snapshot_path`, `publishable`, `authority_tier`, or `handled_by_local_ahj`.
- The locked commercial W4 ten-lane boundary remains: building, electrical, plumbing, mechanical, fire, health, liquor, wastewater, occupancy/CO, and zoning/planning.
- Weak dimensions remain visible as conditional/verify/abstain; do not delete families or reduce packet depth to make gates pass.
- Customer Maps destinations must never fall back to bare `https://www.google.com/maps`.
- Part 1 and Part 2 evidence generators are deterministic and offline.

## Final Part 2 evidence

| Metric | Value |
|---|---:|
| Shipped cells | 2,162 |
| Stable identities | 790 exact, 5 ambiguous |
| Validated exact complete | 36 |
| Validated exact partial | 2,092 |
| Exact fail-closed | 34 |
| Source-free binary violations | 0 |
| Fail-closed/internal-abstain binary violations | 0 |
| Missing core-family routes | 0 |
| Public internal-metadata violations | 0 |
| Focused tests | 32 passed |
| Full suite | 1,109 passed, 47 skipped, 18 baseline failures |
| Newly introduced failures | 0 |
| Final Opus verdict | READY; no blockers |

Authoritative evidence:

```text
artifacts/permit_rule_engine_part2_20260712/generated/manifest.json
artifacts/permit_rule_engine_part2_20260712/generated/summary.json
artifacts/permit_rule_engine_part2_20260712/gates/post_opus_baseline_comparison.json
artifacts/permit_rule_engine_part2_20260712/gates/post_opus_flag_off_parity.json
artifacts/permit_rule_engine_part2_20260712/gates/post_opus_determinism.json
artifacts/permit_rule_engine_part2_20260712/review/final/opus_4_8_final_review.md
```

## Exact Part 3 scope — do not reorder, shrink, defer, or begin Part 4

Complete all of Part 3 / Session 3 from the locked plan:

1. Migrate and reverify every legacy v2.4 cell as a seed; never trust it in place.
2. Produce immutable `EXACT_COMPLETE`, `EXACT_PARTIAL`, `FAIL_CLOSED`, jurisdiction-hold, and unsupported-scope classifications.
3. Implement customer-useful verified-partial behavior without exposing a fabricated binary decision.
4. Preserve proven top-line decisions and proven filing families; demote only unproven dimensions to conditional/verify.
5. Implement the first production-quality minimum scope ontology and sourced predicate schema.
6. Implement reusable code/adoption baseline templates plus per-AHJ overlays.
7. Require local adoption, administrative, and authority evidence before inherited templates can promote.
8. Implement deterministic factory generation born fail-closed and promoted only through evidence gates.
9. Run canary generation and random/counterfactual validation over representative authority models and scope nodes.
10. Prove no coverage-count, family, routing, or packet-capability neutering.
11. Obtain final exact-checkpoint Opus review, remediate every blocker, rerun every mandatory gate after the last patch, and create the immutable Part 3 checkpoint.

## Mandatory pre-activation duties carried by the final Part 2 review

- Add a per-jurisdiction pre-activation gate proving the allowlisted cell set contains no proven filing family outside the closed ontology without an explicit preservation/routing decision. Do not silently demote demolition, grading, sign, or another proven non-core family.
- Add one offline non-cached active-compute integration case covering `research_permit` → `maybe_attach_core_decision_envelope` → cache write → validated sealed projection extraction.
- Consider clearing fail-closed `sources`/`claim_citations` and making the abstain next step explicitly “verify,” rather than “apply,” as defense-in-depth. Do not weaken useful routing.

These are Part 3 pre-activation duties, not permission to activate Part 2 directly.

## Session 3 safety boundary

- Do not activate Part 2 or Part 3 for production customers.
- Do not push or deploy unless Boban explicitly changes the boundary.
- Do not mutate Railway, production environment variables, registries, compiled production artifacts, customer data, or production caches.
- Do not run paid Live100 unless separately authorized.
- Do not begin Part 4.
- Do not add a fifth session or defer a mandatory Part 3 gate.

## Exact copy/paste prompt for Session 3

```text
Resume from the verified immutable Part 2 checkpoint under the locked four-session Permit Rule Engine plan. Reverify Parts 1–2 manifests/checkpoints first. Execute Part 3 fully in this session: migrate/reverify every legacy v2.4 cell as a seed into honest exact-complete/exact-partial/fail-closed/jurisdiction-hold/unsupported classifications; implement useful verified-partial customer behavior; build the minimum production-quality scope ontology, sourced predicate schema, reusable code/adoption templates, per-AHJ overlays, and fail-closed evidence-gated factory; run representative authority/scope canaries plus random and counterfactual validation; prove no product/coverage/packet neutering. Produce the exact production candidate package and all locked metrics/artifacts. Obtain final exact-checkpoint Opus review, remediate every blocker, rerun all gates after the final patch, and create the immutable local Part 3 checkpoint. Do not begin Part 4 and do not push/deploy unless I explicitly change that boundary.
```
