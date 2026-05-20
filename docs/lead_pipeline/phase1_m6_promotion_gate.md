# Phase 1 M6 — Promotion / Eligibility Gate

M6 evaluates M5-assembled business entities and decides the highest safe Phase 1 promotion tier. It is fixture-only, deterministic, and local SQLite only.

## Safety boundary

M6 does **not**:

- fetch live URLs;
- call DNS, SMTP, browser, scraping, paid provider, or external APIs;
- infer missing facts;
- update or delete existing rows;
- write `export_events`;
- set `send_authorized=true`;
- write `live_outreach_ready_future_phase`.

M6 may write exactly one append-only `verification_events` row for its own decision through `promote_entity()` / `write_promotion_decision_event()`.

## Inputs

M6 reads only local fixture tables created by earlier milestones:

- `entities` and `facts` from M5;
- `sources` and `source_observations` from M4/M3;
- prior `verification_events` for contact/email/domain checks;
- `suppression_events` for clear/blocked/hold state;
- `enrichment_events` only when citation-backed and validator-passed;
- `identity_edges` for duplicate/ambiguity review.

## Conservative promotion logic

Highest possible Phase 1 state is `outreach_ready_internal_only`, with `export_eligibility=internal_review_only` and `send_authorized=false`.

Fail/review rules:

- no source provenance → `fail_closed`, `blocked_uncited`;
- wrong batch context → `fail_closed`, `blocked_unknown`;
- missing field-level snippet → `fail_closed`, `blocked_uncited`;
- paid/login source → `blocked_source_policy`;
- duplicate identity edge → `review_required`;
- aggregator-only evidence → `review_required`;
- missing ICP proof → `unknown_not_promoted`;
- guessed/unverified contact → `blocked_unverified_contact`;
- invalid/no-reply/placeholder email → `fail_closed`;
- missing suppression snapshot → `blocked_unknown`;
- suppression conflict hold → `unknown_not_promoted`, `blocked_unknown`;
- suppression block → `fail_closed`, `blocked_suppressed`;
- uncited/invalid enrichment → `unknown_not_promoted`, `blocked_uncited`.

Pass states:

- identity + ICP + independently verified email + suppression clear, but no valid enrichment → `verified_contact`, not exportable.
- all of the above plus citation-backed validator-passed enrichment → `outreach_ready_internal_only`, internal review only, no send authorization.

## Test coverage

`tests/lead_pipeline/test_phase1_m6_promotion.py` asserts:

- no network/outreach imports;
- exact version string;
- source provenance required;
- wrong batch context fails closed;
- guessed emails cannot become verified contacts;
- verified contacts remain not exportable without enrichment;
- suppression conflict fails closed;
- placeholder/no-reply emails are blocked;
- aggregator-only leads require review;
- uncited enrichment cannot promote;
- duplicate identity edges require human review without destructive merge;
- golden path reaches internal-review-only and writes no export rows;
- exact machine-readable reason codes;
- Phase 1 rejects prior verifier passes that used network;
- `send_authorized=true` decision objects are refused before write;
- idempotent decision write.
