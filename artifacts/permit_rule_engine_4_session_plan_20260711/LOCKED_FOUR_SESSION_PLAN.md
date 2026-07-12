# PermitAssist Permit Rule Engine — Locked Four-Session Execution Contract

**Created:** 2026-07-11  
**Status:** LOCKED / controlling implementation plan  
**Opus architecture verdict:** `PROCEED_WITH_ARCHITECTURE`  
**Source consultation:** `artifacts/opus_consult_best_solution_20260711/opus_review.md`

## Controlling objective

Complete the PermitAssist architecture correction in **no more than four focused implementation sessions**, with each numbered Part completed fully in its assigned session. Do not silently shrink, reorder, substitute, or defer a Part. If a genuine blocker prevents a Part from passing its gates, stop with `BLOCKED`, preserve all evidence, and name the blocker; never relabel partial completion as complete.

The four sessions deliver:

1. the complete engine architecture;
2. honest migration/classification of existing v2.4 coverage;
3. safe verified-partial behavior outside exact coverage;
4. a scalable rule/data factory;
5. independent review and controlled production cutover.

They do **not** fabricate complete coverage for every possible project permutation across ~2,200 AHJs. Correctness is guaranteed only inside declared executable coverage contracts; coverage breadth and abstention/verification rate are reported separately.

## Architecture invariants — never deviate

1. Resolve jurisdiction/authority before permit rules.
2. Canonical unit: `jurisdiction_id × scope_ontology_node × fact_profile × effective source set`.
3. Normalize the request into typed work atoms with polarity, thresholds, and relevant facts.
4. Authoritative official-source rules determine regulated fields. Luna may propose scope atoms, request clarification, and explain/format; it may not author or override permit truth.
5. `NOT_REQUIRED` requires affirmative official exemption evidence.
6. Internal `ABSTAIN`/coverage states are first-class. Unsupported or ambiguous inputs must not become guessed `REQUIRED + Building` answers.
7. Preserve proven dimensions and degrade only uncertain dimensions. Verified partial must remain useful, not generic contact-AHJ neutering.
8. Use one reconciliation funnel and one sealed customer projection. Nothing downstream may mutate regulated fields after sealing.
9. Existing 2,162 cells are legacy seeds: migrate/reverify and classify `EXACT_COMPLETE`, `EXACT_PARTIAL`, or `FAIL_CLOSED`; do not certify in place or discard.
10. Code-cluster templates are reusable candidates only. Local promotion additionally requires proven AHJ adoption, applicable administrative trigger, no conflicting amendment, and correct authority routing.
11. API/report/share/rendered output must derive from the same canonical envelope.
12. Report correctness, coverage breadth, packet completeness, jurisdiction certainty, and abstention/verification rate separately.
13. No city/case-specific patch strategy, no data/model guessing, no capability removal, and no benchmark memorization.
14. After every final patch: rerun all Part gates and obtain a fresh exact-diff/SHA-bound Opus review.

# Four-Part Plan

## Part 1 / Session 1 — Foundation, shadow engine, strict census, divergence proof

Complete all of the following in one session:

### Implementation

- Inspect the exact repository SHA, worktree state, current v2.4 package, current resolver path, and current customer egress before editing.
- Add typed, versioned foundations for:
  - `AuthorityContext`
  - `WorkAtom` / fact profile
  - per-family verdict/authority/provenance record
  - `CoverageStatus`
  - `DecisionEnvelope`
- Add a shadow-only envelope adapter around current v2.4 resolution.
- Integrate shadow computation into the research path behind an off-by-default setting that cannot change customer output.
- Add deterministic divergence classification for at least:
  - `AHJ_BOUNDARY_MISMATCH`
  - `SCOPE_TAXONOMY_UNSUPPORTED`
  - `PROJECT_FAMILY_NOT_COVERED`
  - `RULE_OR_EXEMPTION_MISSING`
  - `COMPANION_CLOSURE_INCOMPLETE`
  - `AUTHORITATIVE_CELL_NOT_INJECTED`
  - `MODEL_OR_GENERIC_FALLBACK_GUESS`
  - `POST_RECONCILIATION_MUTATION`
  - `PUBLIC_RENDER_DIVERGENCE`
  - `STALE_OR_CONFLICTING_RULE`
- Run all 2,162 v2.4 cells through strict request-time-equivalent validation and produce the honest census.

### Required Part 1 artifacts

- schema/type design and invariants;
- exact cell census: complete/partial/fail-closed;
- required-family depth histogram;
- authority completeness report;
- jurisdiction identity/ambiguity report;
- evidence/effective-date/conflict report;
- shadow-versus-current divergence ledger;
- deterministic rerun hashes/manifest;
- test output and secret scan;
- Opus final review and disposition of every finding;
- concise Part 1 closeout and exact Part 2 prompt.

### Mandatory Part 1 gates

- Shadow envelope computed for 100% of the chosen replay corpus.
- All 2,162 cells receive deterministic honest classification.
- Flag-off behavior is byte-identical to the pre-Part-1 customer output on the frozen corpus.
- Shadow computation has zero customer-facing side effects and cannot alter caches or regulated fields.
- Census and divergence reports are reproducible from scripts, not hand-counted.
- No source-free `NOT_REQUIRED` route is introduced.
- Focused tests, relevant broader regression tests, compile/static checks, deterministic replay, and secret scan pass or are baseline-compared with zero newly introduced failures.
- Final Opus review targets the final exact diff/checkpoint and returns no unresolved blocker.
- Preserve an immutable local checkpoint and verification manifest.

### Part 1 boundaries

- Do not begin Part 2.
- No production/customer behavior change.
- No push, deployment, Railway/env/registry/compiled production mutation, paid Live100, or live customer rerun.

## Part 2 / Session 2 — Jurisdiction-first resolver, scope compiler, single funnel, sealed projection

Complete all of the following in one session:

- Implement stable jurisdiction identity and candidate-set/ambiguity behavior.
- Implement per-family authority routing.
- Implement closed scope ontology and deterministic work-atom/polarity/threshold validation.
- Implement one strict precedence funnel:
  1. validated exact complete;
  2. validated exact partial;
  3. exact fail-closed;
  4. query-time official evidence meeting the same evidence floor;
  5. internal abstain/verified partial.
- Prevent generic rules, classifiers and Luna from manufacturing regulated truth.
- Introduce envelope/cache schema versioning and stale-cache rejection.
- Implement one sealed projection for all public mirrors, report/share and renderer inputs.
- Keep the entire new path behind disabled/allowlisted flags.
- Freeze RED and no-neuter fixtures before behavior edits.
- Prove flag-off byte parity and flag-on invariants.
- Independent Opus final review, blocker remediation, full rerun, immutable checkpoint.

Part 2 must finish locally deploy-ready behind disabled flags, but must not push/deploy or start migration/factory work from Part 3.

## Part 3 / Session 3 — Legacy migration, verified-partial UX, executable rule factory

Complete all of the following in one session:

- Migrate/reverify every legacy v2.4 cell as a seed; never trust it in place.
- Produce immutable `EXACT_COMPLETE`, `EXACT_PARTIAL`, `FAIL_CLOSED`, jurisdiction-hold and unsupported-scope classifications.
- Implement customer-useful verified-partial behavior without exposing a fabricated binary decision.
- Preserve proven top-line decisions/families and demote only unproven dimensions to conditional/verify.
- Implement the first production-quality minimum scope ontology and predicate schema.
- Implement reusable code/adoption baseline templates plus per-AHJ overlays.
- Require local adoption/administrative/authority evidence before inherited templates can promote.
- Implement deterministic factory generation born fail-closed and promoted only through evidence gates.
- Run canary generation and random/counterfactual validation over representative authority models and scope nodes.
- Prove no coverage-count or packet-capability neutering.
- Independent Opus final review, blocker remediation, full rerun, immutable checkpoint.

Part 3 must end with an honest production candidate package and reports, but no push/deploy unless Boban separately changes the boundary.

## Part 4 / Session 4 — Full verification, integration, controlled release and post-deploy proof

Complete all of the following in one session:

- Integrate the exact Part 1–3 checkpoints into the sealed deployment candidate.
- Freeze exact production rollback checkpoint and restore procedure before deployment.
- Run focused, boundary, full regression, random covered-population, unsupported/ambiguous, counterfactual, cache, report/share, rendered-browser, security and secret gates.
- Compare exact failure sets, not only totals; zero unapproved A/B→C/F or supported→unsupported regressions.
- Obtain independent Opus/Fable final review against the exact immutable candidate; remediate every blocker and rerun everything after the final patch.
- Prepare deploy proof and ask Boban for explicit push/deploy approval **inside Session 4** if it was not already included in his Session 4 message.
- After approval: push only the sealed SHA, prove Railway exact SHA/deployment success, health, logs, API behavior, browser/rendered output, cache behavior and rollback readiness.
- Run post-deploy customer-contract smokes that include exact-complete, exact-partial, unsupported, jurisdiction-ambiguous, NOT_REQUIRED-evidence and multi-authority cases.
- Publish final correctness/coverage/abstention/packet-completeness metrics without an unqualified “2,162 AHJs covered” claim.

# Exact copy/paste prompts

## Prompt for the next session — Part 1

```text
Resume PermitAssist using the locked four-session Permit Rule Engine plan saved in:
- artifacts/permit_rule_engine_4_session_plan_20260711/LOCKED_FOUR_SESSION_PLAN.md
- the durable permitassist-p0-p3-trust-fixes skill reference `references/permit-rule-engine-locked-four-session-plan-2026-07-11.md`
- Opus consultation: artifacts/opus_consult_best_solution_20260711/opus_review.md

Execute Part 1 completely and properly in this session. Do not give me another plan-only response and do not stop after scaffolding. Inspect the exact current repository/SHA/worktree and existing artifacts first, then implement the full Part 1 contract:

1. typed/versioned AuthorityContext, WorkAtom/fact profile, per-family verdict/authority/provenance, CoverageStatus and DecisionEnvelope foundations;
2. a shadow-only DecisionEnvelope adapter integrated into the research path behind an off-by-default setting with zero customer-visible/cache/regulated-field effects;
3. deterministic divergence taxonomy/instrumentation;
4. strict request-time-equivalent validation and honest classification of all 2,162 v2.4 cells;
5. complete reproducible census, family-depth, authority, jurisdiction, evidence/effective-date/conflict and divergence artifacts;
6. frozen corpus byte-parity proving flag-off and shadow execution do not change customer output;
7. focused + relevant broader tests, compile/static checks, deterministic rerun manifest, baseline failure-set comparison and secret scan;
8. final read-only Opus review against the exact final diff/checkpoint, remediation of every real blocker, and a complete rerun after the final patch;
9. immutable local Part 1 checkpoint and exact Part 2 handoff.

Part 1 is not complete unless every mandatory gate in the locked plan is satisfied with real artifacts. If a genuine blocker remains, report BLOCKED honestly; do not call partial work complete. Do not start Part 2. No push/deploy/Railway/env/registry/compiled-production mutation, paid Live100 or live customer rerun. You are authorized to create a local immutable Part 1 checkpoint commit after all gates pass, but not to push it.
```

## Prompt for Session 2 — Part 2

```text
Resume from the verified immutable Part 1 checkpoint under the locked four-session Permit Rule Engine plan. Reverify the Part 1 manifest/checkpoint first. Then execute Part 2 fully in this session: jurisdiction-first identity/candidate resolution, per-family authority routing, closed work-atom ontology with polarity/threshold validation, the single strict precedence funnel, internal abstain/verified-partial semantics, cache/envelope versioning, and one sealed canonical customer projection across API/report/share/rendered mirrors—all behind disabled/allowlisted flags. Freeze RED and no-neuter fixtures before behavior changes; prove flag-off byte parity and flag-on invariants; prevent generic classifiers/Luna/late passes from authoring or mutating regulated truth. Run all locked Part 2 gates, full relevant regressions and exact failure-set comparisons, obtain final exact-checkpoint Opus review, remediate every blocker, rerun after the final patch, and create the immutable local Part 2 checkpoint. Do not begin Part 3 and do not push/deploy or mutate production.
```

## Prompt for Session 3 — Part 3

```text
Resume from the verified immutable Part 2 checkpoint under the locked four-session Permit Rule Engine plan. Reverify Parts 1–2 manifests/checkpoints first. Execute Part 3 fully in this session: migrate/reverify every legacy v2.4 cell as a seed into honest exact-complete/exact-partial/fail-closed/jurisdiction-hold/unsupported classifications; implement useful verified-partial customer behavior; build the minimum production-quality scope ontology, sourced predicate schema, reusable code/adoption templates, per-AHJ overlays, and fail-closed evidence-gated factory; run representative authority/scope canaries plus random and counterfactual validation; prove no product/coverage/packet neutering. Produce the exact production candidate package and all locked metrics/artifacts. Obtain final exact-checkpoint Opus review, remediate every blocker, rerun all gates after the final patch, and create the immutable local Part 3 checkpoint. Do not begin Part 4 and do not push/deploy unless I explicitly change that boundary.
```

## Prompt for Session 4 — Part 4

```text
Resume from the verified immutable Part 3 checkpoint under the locked four-session Permit Rule Engine plan. Reverify Parts 1–3 manifests/checkpoints and integrate only their sealed artifacts. Execute Part 4 fully in this session: build the exact deployment candidate; preserve and verify a rollback checkpoint/restore path; run focused, boundary, full regression, random covered-population, unsupported/ambiguous, counterfactual, cache, report/share, rendered-browser, security and secret gates; compare exact failure sets; obtain final SHA-bound Opus/Fable review; remediate every blocker and rerun everything after the final patch. Then present the sealed SHA and readiness proof and ask me for explicit push/deploy approval in this same session if my opening message did not already authorize deployment. After approval, push only the sealed SHA, verify Railway exact SHA/deployment/health/logs, run live API and rendered-browser smokes including complete/partial/unsupported/jurisdiction-ambiguous/NOT_REQUIRED/multi-authority cases, verify rollback readiness, and publish honest correctness, breadth, packet-completeness and abstention metrics. Do not claim completion until the exact deployed SHA and live customer boundary are proven.
```

## Closeout rule

At the end of each Part, answer first with:

- `PART N: PASS / BLOCKED`
- exact immutable checkpoint SHA or artifact hash;
- mandatory gate totals and introduced-failure count;
- Opus verdict;
- production boundary;
- exact prompt for the next Part.

No Part may borrow unfinished work from a later Part to disguise an incomplete current Part. No session may silently defer an assigned mandatory gate to a fifth session.
