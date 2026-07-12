# Permit Rule Engine Part 3 — final exact-checkpoint rereview

Perform this review synchronously yourself. Do not edit files, invoke shell commands, delegate, start background work, or defer verification.

## Exact checkpoint

- Repository: `/home/boban/projects/permitassist-rule-engine-part3-20260712`
- Base tag: `permit-rule-engine-part2-20260712`
- Base commit: `c3a05b10eb3876d15e97e1484ecdd84b616e20cb`
- Final candidate source commit: `403ef1543076da6097ccf134c90ef43c005f6532`
- Exact diff SHA-256: `e8a5cb897572285335edba9da66fe01d207278b4f347f99f406f3dc83d419810`
- Exact diff: `artifacts/permit_rule_engine_part3_20260712/review/final/exact_candidate_diff.patch`

An earlier review of source `41f8c367...` approved but identified family-level placeholder provenance as defense-in-depth. The orchestrator treated that concrete source-integrity observation as mandatory, added `_publishable_provenance` rejection for an all-zero SHA-256, added a regression test, committed source `403ef154...`, regenerated all evidence twice, and reran the complete gate matrix. The earlier review is archived under `review/superseded_41f8c36/` and must not be counted. Review only this final candidate.

Start by reading the final exact diff, then read production code, tests, and evidence as needed.

## Controlling requirements

1. Migrate and deterministically reverify all 2,162 v2.4 legacy cells as immutable seeds.
2. Preserve exact source-family labels and source-backed binary conclusions.
3. Classify honestly as exact complete, exact partial, fail closed, jurisdiction hold, or unsupported scope.
4. Preserve verified-partial binary truth while exposing every unresolved lane with tasks and verification-before-application copy.
5. Fail-closed, ambiguous, unsupported, malformed, and factory-error paths must clear binary decisions, routes, public sources, and citations.
6. Implement minimum closed ontology, sourced predicates, reusable adoption templates, AHJ overlays, and a factory born fail closed.
7. Promotion requires sourced predicate, stable identity, local adoption/administrative evidence, explicit family preservation/override, and authority/application routing.
8. Preserve commercial W4’s ten lanes and all proven non-core legacy family labels.
9. Activation remains disabled by default and exact-allowlist-only. No deployment or production activation.
10. Preserve Part 1–2 behavior with zero new full-suite failure node IDs.

Legacy hash domains may differ. Require valid non-placeholder provenance and canonical full-cell hash binding; do not require unlike legacy hash-domain fields to equal one another.

Canaries must distinguish the lower-level overlay/evidence gate from identity eligibility and final composed promotion. An ambiguous cell may pass the evidence layer while final promotion rejects it.

## Final evidence

- Frozen contract:
  - `tests/fixtures/permit_rule_engine_part3_red_no_neuter.json`
  - `tests/fixtures/permit_rule_engine_part3_red_no_neuter_manifest.json`
  - `artifacts/permit_rule_engine_part3_20260712/CONTRACT_AMENDMENT_1.md`
- Final deterministic evidence:
  - `artifacts/permit_rule_engine_part3_20260712/generated_run1/summary.json`
  - `artifacts/permit_rule_engine_part3_20260712/generated_run1/manifest.json`
  - `artifacts/permit_rule_engine_part3_20260712/generated_run1/canary_results.json`
  - `artifacts/permit_rule_engine_part3_20260712/generated_run1/preactivation_family_audit.json`
  - `artifacts/permit_rule_engine_part3_20260712/generated_run1/customer_projection_audit.json`
  - `artifacts/permit_rule_engine_part3_20260712/generated_run1/seeded_random_and_counterfactual_results.json`
  - `artifacts/permit_rule_engine_part3_20260712/gates/determinism_report.json`
- Final gates:
  - `artifacts/permit_rule_engine_part3_20260712/gates/pre_opus_focused_tests.txt`
  - `artifacts/permit_rule_engine_part3_20260712/gates/pre_opus_baseline_comparison.json`
  - `artifacts/permit_rule_engine_part3_20260712/gates/pre_opus_secret_scan.json`
  - `artifacts/permit_rule_engine_part3_20260712/gates/pre_opus_secret_contract_tests.txt`
  - `artifacts/permit_rule_engine_part3_20260712/gates/pre_opus_static_checks.txt`

Final recorded results:

- Focused Part 1–3/integration: 53 passed.
- Full suite: 1,130 passed, 47 skipped, 18 failed; exact same 18-node baseline; no new or resolved failures.
- Secret contract: 3 passed; added-line scan: zero hits over 2,177 lines.
- Migration/reverification: 2,162/2,162.
- Classification: 36 complete, 2,082 partial, 34 fail closed, 10 jurisdiction hold.
- Customer audit: 2,162 checked, zero violations.
- Verified-partial tasks and verify-first next steps: 2,082/2,082.
- Random: 128/128; counterfactuals: 5/5; canaries: 4/4.
- Pre-activation family audit: zero violations.
- Two independent evidence runs: all 10 files byte-identical.

## Required output

1. `CHECKPOINT`: exact final SHA and diff hash reviewed.
2. `BLOCKERS`: severity, file/symbol, concrete failure mode, remediation; `NONE` if empty.
3. `NONBLOCKING`: observations only.
4. `EVIDENCE ASSESSMENT`.
5. `VERDICT`: final line must be exactly one token:
   - `APPROVE_CHECKPOINT`
   - `BLOCK_CHECKPOINT`

Any unresolved correctness, fail-closed, source-integrity, verified-partial, determinism, or no-neuter issue is a blocker regardless of severity wording.
