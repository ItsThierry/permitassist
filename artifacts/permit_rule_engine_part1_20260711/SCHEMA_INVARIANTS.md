# Permit Rule Engine Part 1 — schema and safety invariants

Base inspected: `facea233ac4f821304288f14137e472e92a6fcfc`

## Versioned contract

The Part 1 contract is frozen under these external version identifiers:

- `permitassist.rule-engine.v1`
- `permitassist.authority-context.v1`
- `permitassist.work-atom.v1`
- `permitassist.fact-profile.v1`
- `permitassist.decision-envelope.v1`
- `permitassist.rule-engine-shadow-event.v1`
- `permitassist.rule-engine-shadow-adapter.v1`
- `permitassist.divergence-taxonomy.v1`

`DecisionEnvelope` contains a deterministic request fingerprint, source-cell identity, strict coverage status and reason, authoritative main and family decisions, per-family authority, application routes, provenance, request-time validation outcome, and canonical source-set hash.

All schema records are frozen dataclasses. They intentionally avoid the Python 3.10-only `dataclass(slots=True)` option so the repository's supported Python 3.9 import path remains intact. Canonical JSON serialization sorts keys and uses stable separators; hashes are SHA-256 over those canonical bytes.

## Strict coverage semantics

A shipped cell is classified independently from its legacy `TIER1_COMPLETE` label.

- `FAIL_CLOSED`: the shipped cell is explicitly fail-closed.
- `VALIDATION_FAILED`: portable request-time validation fails.
- `EXACT_PARTIAL`: the AHJ/project cell resolves exactly but is missing explicit family, authority, provenance, or source-backed application-route closure.
- `EXACT_COMPLETE`: portable validation passes and all required family, authority, provenance, and source-backed route closures are explicit.

Commercial tenant improvement uses the locked ten-lane W4 filing-packet boundary: building, electrical, plumbing, mechanical, fire, health, liquor, wastewater, occupancy/CO, and zoning/planning. A lane may be `NOT_REQUIRED`, but it must be explicit and source-backed. Missing lanes are partial; they are never inferred from silence.

Unsupported scope in an exactly covered AHJ is `AHJ_COVERED_SCOPE_UNSUPPORTED`. Uncovered AHJs, ambiguous jurisdiction matches, unavailable indexes, and validation failures remain separate fail-closed conditions.

Portable census validation uses the same `validate_v24_cell` semantics as request-time resolution with live URL checks and strict local snapshot availability deliberately disabled. Those two environment-dependent dimensions are reported as out of scope rather than silently claimed.

## Shadow boundary

The shadow adapter is disabled unless `PERMITASSIST_RULE_ENGINE_SHADOW=shadow` (the implementation also accepts the explicit boolean aliases `1`, `true`, and `on`). When disabled it produces no envelope and no observation.

When enabled:

1. The existing v2.4 resolver remains authoritative.
2. Shadow data is never merged into the returned customer payload.
3. Shadow observation runs only after the existing cache-write path.
4. Observer failures are swallowed at the integration boundary.
5. The optional JSONL sink accepts only an absolute local path.
6. The adapter performs no network or model calls and writes no customer, regulated, production, or application-cache state.

Part 1 does not activate authoritative serving, change customer rendering, alter reconciliation, add fallback routing, or deploy anything.

## Divergence taxonomy

The locked diagnostic classes are:

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

Lower-level deterministic diagnostics may accompany these classes but cannot replace them.

## Evidence invariants

- The census must contain exactly all 2,162 shipped v2.4 index cells.
- Every row records portable validation, strict classification, family and authority depth, application-route count, provenance count/domains, effective dates, and stable hashes.
- Conflict reporting includes per-cell and package-global snapshot/effective-date conflicts plus jurisdiction-shape conflicts.
- The frozen replay corpus is synthetic contract data, not customer or historical production data.
- Every replay executes both flag-off and shadow-on paths and compares deterministic insertion-order JSON bytes before and after, so value and key-order drift both fail parity.
- A valid run has zero payload mutations and byte parity for every corpus case.
- Re-running the generator with identical inputs must reproduce identical generated-file hashes.

## Part 2 boundary

Part 2 may consume this envelope only after its own locked-plan gates. Part 1 does not authorize serving activation, customer-output changes, data promotion, cache migration, deployment, or push.