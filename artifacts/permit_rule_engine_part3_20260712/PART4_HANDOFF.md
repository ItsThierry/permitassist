# Permit Rule Engine Part 4 handoff

## Required entry checkpoint

Resume only from the verified immutable local tag:

```text
permit-rule-engine-part3-20260712
```

Before implementation, independently verify:

1. `git rev-parse permit-rule-engine-part3-20260712^{commit}` resolves to the sealed checkpoint.
2. Parts 1–3 annotated tags peel successfully and ancestry is linear.
3. Every file/hash in `artifacts/permit_rule_engine_part3_20260712/CHECKPOINT_MANIFEST.json` matches.
4. `FINAL_GATE_SUMMARY.json` reports `passed=true`.
5. Both generated evidence runs are byte-identical according to `gates/determinism_report.json`.
6. Final Opus review targets source `403ef1543076da6097ccf134c90ef43c005f6532`, ends `APPROVE_CHECKPOINT`, and has zero blockers.
7. The worktree used for Part 4 is clean and created from the peeled Part 3 tag, not from an unsealed branch tip.

If any check fails, stop as `BLOCKED`; do not repair or reinterpret the sealed Part 3 checkpoint in place.

## Controlling Part 4 prompt

```text
Resume from the verified immutable Part 3 checkpoint under the locked four-session Permit Rule Engine plan. Reverify Parts 1–3 manifests/checkpoints and integrate only their sealed artifacts. Execute Part 4 fully in this session: build the exact deployment candidate; preserve and verify a rollback checkpoint/restore path; run focused, boundary, full regression, random covered-population, unsupported/ambiguous, counterfactual, cache, report/share, rendered-browser, security and secret gates; compare exact failure sets; obtain final SHA-bound Opus/Fable review; remediate every blocker and rerun everything after the final patch. Then present the sealed SHA and readiness proof and ask me for explicit push/deploy approval in this same session if my opening message did not already authorize deployment. After approval, push only the sealed SHA, verify Railway exact SHA/deployment/health/logs, run live API and rendered-browser smokes including complete/partial/unsupported/jurisdiction-ambiguous/NOT_REQUIRED/multi-authority cases, verify rollback readiness, and publish honest correctness, breadth, packet-completeness and abstention metrics. Do not claim completion until the exact deployed SHA and live customer boundary are proven.
```

## Part 3 invariants Part 4 must preserve

- All 2,162 legacy cells remain immutable seeds bound to canonical full-cell hashes.
- Exact legacy source-family labels are customer truth and must not be collapsed.
- Proven binary top-line/family conclusions survive verified-partial output.
- Every unresolved partial lane remains visible with verification-before-application guidance.
- Fail-closed, ambiguous, unsupported, malformed, and factory-error paths emit no binary claims, routes, public sources, or citations.
- Promotion requires stable identity plus sourced predicate, local adoption/administrative evidence, explicit family preservation/override, and authority/application routing.
- W4 retains all ten filing lanes.
- Part 1–3 regulated fields flow through one sealed projection; late code/model/render passes may not rewrite them.
- Activation remains disabled by default and requires an exact jurisdiction allowlist.

## Sealed Part 3 evidence

- Source candidate: `403ef1543076da6097ccf134c90ef43c005f6532`
- Classification: 36 exact complete, 2,082 exact partial, 34 fail closed, 10 jurisdiction hold.
- Reverification: 2,162/2,162.
- Customer audit: 2,162 checked, zero violations.
- Random/counterfactual/canary: 128/128, 5/5, 4/4.
- Pre-activation family violations: zero.
- Full-suite baseline: exact unchanged 18-node failure set.
- Final Opus verdict: `APPROVE_CHECKPOINT`.

## Part 4 boundaries

- Do not push or deploy until the exact deployment candidate, rollback path, full gate matrix, and SHA-bound reviews pass and Boban provides explicit push/deploy approval.
- Health-only proof is insufficient. After approval, prove exact Railway SHA, deployment state, health, logs, live API truth, and rendered-browser truth.
- Any post-review patch invalidates review and all final gates; rerun the complete matrix.
- No fifth session may be created by silently deferring a mandatory Part 4 gate.
