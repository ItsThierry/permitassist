# PermitAssist Lead Pipeline Phase 1 M2 — Fixture Gate Library Contract

Milestone 2 adds pure, deterministic fixture gates on top of the M1 schema contract. It remains fixture-only: no network, no scraping, no Apollo/paid APIs, no SMTP, no outreach, no production database, no ColdForge import, no Railway/env/GitHub remote mutation, and no customer-visible output.

## Scope

M2 introduces:

- `lead_pipeline.gates`
  - `GateResult`: immutable event-shaped gate output.
  - `sourceability_gate`
  - `identity_gate`
  - `icp_fit_gate`
  - `contact_observation_gate`
  - `email_syntax_gate`
  - `domain_quality_gate`
  - `suppression_gate`
  - `enrichment_quality_gate`
  - `outreach_readiness_gate`
  - `benchmark_assumption_gate`
  - `evaluate_legacy_pitfall_fixture`
- `lead_pipeline.fixtures`
  - synthetic sourceability fixtures
  - a clean internal-review-ready fixture
  - a complete legacy pitfall catalog from the Phase 1 build plan
- `tests/lead_pipeline/test_phase1_m2_fixture_gates.py`
  - fail-closed regression tests
  - no-network event payload checks
  - catalog completeness checks

## Event shape

Every gate returns a `GateResult`. `GateResult.to_event_payload()` emits a payload compatible with the M1 `verification_events` table shape:

- `verification_event_id`
- `batch_id`
- `gate_name`
- `gate_version`
- `input_hash`
- `result_status`
- `score`
- `reason_codes`
- `network_used_flag = 0`
- `cached_result_flag = 0`
- `observed_at_utc`
- `schema_version`

M2 does not write these payloads to any database. Later milestones can append them through real event writers after a separate approval.

## Fail-closed behavior

The gate library blocks or routes to review for:

- missing source URL/path, timestamp, payload hash, or field-relevant snippet;
- blocked/CAPTCHA/login/paid sources;
- uncited business identity;
- aggregator-only identity;
- official-license-only seeds promoted too far;
- domain/name-only identity matches;
- duplicate candidates that need identity edges rather than destructive deletion;
- uncited/generic ICP labels;
- ambiguous permit expediters/designers/code consultants;
- placeholder, guessed, no-reply/internal, off-domain, and unprovenanced emails;
- invalid email syntax;
- absent MX, disposable domains, parked domains, greylist/timeouts, catch-all domains, and free-provider review cases using mocked inputs only;
- suppressed, duplicate, unknown, or conflicting suppression snapshots;
- unsupported enrichment claims, generic icebreakers, overlong output, or thin/uncited content;
- any live-send attempt or ColdForge/handoff join failure;
- Apollo benchmark assumptions lacking explicit assumption labels.

## Legacy pitfall catalog

`LEGACY_PITFALL_IDS` is intentionally explicit so M2 quality is not based on tribal memory. The fixture test asserts exact coverage for the pitfall list from the Phase 1 build plan:

1. placeholder email rejection
2. off-domain email review
3. guessed-pattern email hold
4. per-email provenance required
5. official license seed promotion limit
6. domain/name-only match review
7. normalized duplicate candidate without destructive deletion
8. MX absent hard fail
9. small-business role-account review
10. catch-all handling
11. greylist/timeout retry discipline
12. thin content quality cap
13. generic icebreaker rejection
14. unsupported contractor-software claim rejection
15. ColdForge join failure fails closed
16. suppression blocks export
17. duplicate campaign enrollment blocks export
18. no live-send without compliance/dry-run
19. paid adapter disabled by default
20. Apollo benchmark assumption labelled

## Boundary confirmation

M2 fixtures are synthetic and contain no real lead PII. The library imports only Python stdlib modules plus local `lead_pipeline` modules. It performs no DNS lookup, socket operation, HTTP request, SMTP probe, browser automation, filesystem write, database write, paid-provider call, or outreach action.
