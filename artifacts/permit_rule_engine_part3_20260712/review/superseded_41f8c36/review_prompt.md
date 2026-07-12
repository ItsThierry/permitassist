# Permit Rule Engine Part 3 — exact-checkpoint final review

You are the final independent Opus reviewer. Perform this review synchronously yourself. Do not edit files, invoke shell commands, delegate, start background work, or defer verification.

## Exact checkpoint

- Repository: `/home/boban/projects/permitassist-rule-engine-part3-20260712`
- Immutable base tag: `permit-rule-engine-part2-20260712`
- Base commit: `c3a05b10eb3876d15e97e1484ecdd84b616e20cb`
- Candidate source commit: `41f8c367150877c477b456bc0585f74d5390a777`
- Exact diff SHA-256: `8c107e9a018dc50863f7ced6e1846d976d06fb5421ce7031e8eb0ed3c7f2c7e1`
- Exact diff: `artifacts/permit_rule_engine_part3_20260712/review/final/exact_candidate_diff.patch`

Start by reading the exact diff. Read the underlying production code/tests and evidence files as needed.

## Controlling requirements

Part 3 must:

1. Migrate and deterministically reverify all 2,162 v2.4 legacy cells as immutable seeds, not certify them in place.
2. Preserve exact source-family labels and source-backed `REQUIRED` / `NOT_REQUIRED` conclusions.
3. Classify honestly as exact complete, exact partial, fail closed, jurisdiction hold, or unsupported scope.
4. Keep verified-partial customer UX useful: retain proven binary conclusions; expose every unresolved lane with verification tasks; require verification before application.
5. For fail-closed, ambiguous, unsupported, malformed, or factory-error paths, clear all binary decisions, routes, public sources, and claim citations.
6. Implement a minimum closed scope ontology, sourced predicates, reusable adoption templates, AHJ overlays, and a factory born fail closed.
7. Permit promotion only when sourced predicate, stable identity, local adoption/administrative evidence, explicit family preservation/override, and authority/application routing all pass.
8. Preserve commercial W4’s ten filing lanes and non-core proven legacy family labels—no taxonomy collapse or capability neutering.
9. Keep activation disabled by default and exact-allowlist-only. No production activation or deploy is part of this checkpoint.
10. Preserve Part 1–2 behavior and introduce no new full-suite failure node IDs.

Important hash-domain rule: legacy source waves use different decision-snapshot, live-file, and aggregate-watch hash domains. A valid non-placeholder provenance hash plus canonical full-cell hash binding is required; unlike legacy hash domains must not be assumed equal.

Important canary rule: distinguish the lower-level evidence/overlay gate from seed eligibility and final composed promotion. An ambiguous jurisdiction may have valid local evidence while final promotion must still reject it.

## Evidence to inspect

- Frozen contract and amendment:
  - `tests/fixtures/permit_rule_engine_part3_red_no_neuter.json`
  - `tests/fixtures/permit_rule_engine_part3_red_no_neuter_manifest.json`
  - `artifacts/permit_rule_engine_part3_20260712/CONTRACT_AMENDMENT_1.md`
- Candidate evidence run 1:
  - `artifacts/permit_rule_engine_part3_20260712/generated_run1/summary.json`
  - `artifacts/permit_rule_engine_part3_20260712/generated_run1/manifest.json`
  - `artifacts/permit_rule_engine_part3_20260712/generated_run1/canary_results.json`
  - `artifacts/permit_rule_engine_part3_20260712/generated_run1/preactivation_family_audit.json`
  - `artifacts/permit_rule_engine_part3_20260712/generated_run1/customer_projection_audit.json`
  - `artifacts/permit_rule_engine_part3_20260712/generated_run1/seeded_random_and_counterfactual_results.json`
- Determinism: `artifacts/permit_rule_engine_part3_20260712/gates/determinism_report.json`
- Focused tests: `artifacts/permit_rule_engine_part3_20260712/gates/pre_opus_focused_tests.txt`
- Full-suite baseline comparison: `artifacts/permit_rule_engine_part3_20260712/gates/pre_opus_baseline_comparison.json`
- Secret scan: `artifacts/permit_rule_engine_part3_20260712/gates/pre_opus_secret_scan.json`
- Static checks: `artifacts/permit_rule_engine_part3_20260712/gates/pre_opus_static_checks.txt`

Recorded gate results:

- Focused Part 1–3 and integration: 52 passed.
- Full suite: 1,129 passed, 47 skipped, 18 failed.
- Baseline failures: 18; exact failure-set match; zero new and zero removed node IDs.
- Seed migration/reverification: 2,162/2,162.
- Classification: 36 exact complete, 2,082 exact partial, 34 fail closed, 10 jurisdiction hold.
- Customer projection audit: 2,162 checked, zero violations.
- Verified-partial actionable next steps and family tasks: 2,082/2,082.
- Fixed-seed random validation: 128/128.
- Counterfactuals: 5/5.
- Pre-activation family audit: zero violations.
- Independent evidence runs: all 10 files byte-identical.
- Added-line secret scan: zero hits.

## Required review output

Review correctness, safety, determinism, source/provenance integrity, customer contract, promotion composition, test adequacy, and evidence-script honesty. Reproduce findings by reading exact symbols and artifacts; do not accept summaries blindly.

Structure the response as:

1. `CHECKPOINT`: exact candidate SHA and diff hash reviewed.
2. `BLOCKERS`: each blocker with severity, file/symbol, concrete failure mode, and required remediation; write `NONE` if empty.
3. `NONBLOCKING`: observations that do not block this checkpoint.
4. `EVIDENCE ASSESSMENT`: whether the recorded artifacts support the claims.
5. `VERDICT`: end with exactly one token on its own final line:
   - `APPROVE_CHECKPOINT`
   - `BLOCK_CHECKPOINT`

Any unresolved correctness, fail-closed, source-integrity, verified-partial, determinism, or no-neuter issue is a blocker.
