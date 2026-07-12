# Part 3 Frozen Contract Amendment 1

Date: 2026-07-12

## Correction

The original frozen fixture included `"unsupported_scope": 0` in the expected `Counter` serialization for the real-corpus census. Python's `Counter` does not serialize keys that were never observed, so the assertion was mathematically impossible for a corpus with zero unsupported-scope seeds.

The zero-valued entry was removed. No denominator, positive threshold, expected failure class, canary, random seed, counterfactual, or customer-truth assertion changed. The expected non-zero classes still sum to the immutable 2,162-cell corpus.

## Timing and integrity

This amendment was committed while the worktree contained no behavior changes. The in-progress behavior diff was saved outside the repository, the worktree was reset to the frozen-contract checkpoint, this correction was applied and re-hashed, and only then was implementation resumed.
