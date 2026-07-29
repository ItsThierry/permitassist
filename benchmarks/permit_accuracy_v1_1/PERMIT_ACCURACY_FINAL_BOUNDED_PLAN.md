# PermitAssist Permit-Accuracy Remediation — Final Bounded Plan

## Decision

Execute the existing PermitAssist executable-coverage doctrine as one four-session consolidation program. Do not add another decision layer, do not use runtime model voting, and do not patch benchmark cases individually.

The authoritative runtime remains authenticated Decision Cells plus deterministic executable rules. The customer-facing product receives one canonical Permit Manifest serialized through one sealed projection. Models may mine and review staged candidate rules offline; they never establish customer truth.

## Critical correction from Fable 5 review

The fresh benchmark is integrity-sound but its family/companion headline metrics are not yet semantically fit for implementation decisions:

- Truth distribution is 95 REQUIRED decisions, 96 BUILDING primary families, and 93 cases with zero expected companions.
- Constant baselines are therefore 95% decision, 96% primary family, and 93% empty-companion exact-set accuracy.
- Grok's 85% family and Gemini's 69% companions are below those trivial baselines; the benchmark does not prove model-specialist superiority.
- The benchmark family enum is not closed over its own truth labels.
- The engine egress often contains the correct typed family internally but exposes a display label to the scorer.
- Companion truth is under-filled relative to the existing W4 filing-packet doctrine, while the runtime can emit enriched companions that the truth omits.
- The four engine decision misses are honest abstentions, not confident wrong answers.

Therefore the scorer/truth contract must be repaired before runtime companion behavior is changed. Otherwise suppressing companions would produce a fake score improvement and neuter PermitAssist.

## Final architecture

### 1. Canonical permit-family ontology

One checked-in enum and mapping table covers every primary and companion label used by cells, runtime, customer output, and benchmarks. It includes a first-class `NO_PRIMARY_PERMIT` state and canonical families such as building, electrical, plumbing, mechanical, fire/life-safety, zoning/planning, occupancy/CO, demolition, sign, pool, moving, landmarks, and other validated local families.

CI fails if any truth/cell/runtime family label is unmapped.

### 2. Manifest as consolidation, not another layer

Extend the existing typed `CustomerPermitDecision`/sealed projection into the complete Permit Manifest instead of introducing a parallel object.

For each family, the manifest carries:

- canonical family;
- local permit name;
- status: `REQUIRED | CONDITIONAL | NOT_REQUIRED | NEEDS_INPUT | VERIFY`;
- source-backed trigger and exemption;
- authority;
- `source_ref`;
- coverage state;
- missing input fact when applicable.

The same manifest owns API, UI, report, share, and finalizer inputs. Regulated fields have one writer. Existing mutator layers must shrink rather than grow.

### 3. Provenance boundary for companions

- Source-backed cell/W4 companions may surface as REQUIRED or CONDITIONAL.
- Generic keyword/heuristic companions cannot surface as factual REQUIRED claims.
- Unsourced heuristic companions are demoted, not deleted, to visible VERIFY leads with confirm-with-AHJ framing.
- Missing required companions are never silently omitted.

### 4. Runtime model boundary

- Deterministic engine is the sole runtime authority.
- Grok, Gemini, and Luna may mine or red-team staged candidate rules offline, without dimension-locked specialist roles.
- No model output promotes without official-source evidence, deterministic quote/hash validation, and independent review.
- The benchmark's Luna-to-engine `engine_luna` path is retired as a product architecture.
- A true Engine-to-Luna finalizer may improve wording only and cannot add, remove, or mutate decisions, families, companions, statuses, authority, or evidence.

### 5. Bounded follow-up questions

PermitAssist may ask at most approximately five targeted questions when a source-backed trigger depends on a missing fact, such as roof area, setback, occupancy/use change, electrical scope, plumbing scope, HVAC scope, or fire-system scope.

Unresolved permits remain visible as CONDITIONAL or NEEDS_INPUT with the trigger and both branches. Questions refine the permit manifest but never erase the known primary result.

## Four-session implementation program

### Session 1 — Forensics, ontology, and benchmark v1.2

Runtime boundary: read-only.

Deliverables:

1. Attribute every family, companion, and decision mismatch in the preserved 100-case development set using the established 10-category executable-coverage taxonomy.
2. Record raw-envelope evidence for every attribution.
3. Audit all expected permit sets against official sources with independent truth review, especially the 93 empty-companion cases.
4. Mark each empty set as `confirmed-none` or `truth-incomplete` with evidence.
5. Create the canonical ontology and all adapters as a reviewed specification.
6. Build benchmark v1.2 with enum closure, companion precision/recall, dangerous omissions, conditional/needs-input scoring, constant baselines, and frozen coverage denominators.
7. Offline-rescore preserved raws only; no provider rerun.

Gate:

- 100% of misses attributed.
- Every truth correction source-backed and independently reviewed.
- Enum closure passes.
- Runtime files unchanged.

### Session 2 — Flag-gated egress parity and manifest consolidation

Deliverables:

1. Populate the manifest from existing typed internal fields; invent no new truth.
2. Surface canonical primary family, jurisdiction identity, and canonical companion family/status/source fields.
3. Demote unsourced heuristic companions to VERIFY rather than deleting them.
4. Route every customer surface through the single sealed projection.
5. Produce a frozen 100-case shadow replay and non-target byte-diff report.

Gates:

- Permit decisions remain exactly unchanged at 96/100 on the development replay.
- Primary-family accuracy reaches at least 90%; expected target is at least 95% if the projection diagnosis is correct.
- Jurisdiction identity reaches at least 99% where the internal record contains identity.
- No source-backed companion is removed.
- No REQUIRED/CONDITIONAL companion lacks `source_ref`.
- Enriched v2.4/W4 cell counts and capability census are unchanged.
- Filing destination remains at least 98%; URL-role remains at least 99%.
- Existing trust/customer-boundary suites remain green.
- Feature flag off restores byte-identical prior behavior.

Falsifier:

If family accuracy does not reach 90% from egress/ontology consolidation alone, stop and re-attribute. Do not begin packet data expansion on a false diagnosis.

### Session 3 — Staged permit-packet depth pilot

Define a named AHJ × job-scope pilot spanning residential reroof/remodel, commercial TI, restaurant TI, change of use, structural, electrical service, plumbing, HVAC, and fire-system work across city/county/delegated/state/fire-district authority models.

Deliverables:

1. Complete source-backed permit-family packets for the declared pilot scope.
2. Store explicit REQUIRED/CONDITIONAL/NOT_REQUIRED/NEEDS_INPUT/VERIFY rules, triggers, exemptions, authority, and source references.
3. Use offline models only for candidate discovery and adversarial omission review.
4. Run deterministic source, quote, hash, ontology, and projection validation.
5. Keep all work staged; no registry, compiled-runtime, deploy, or customer mutation.

Gates within the declared pilot scope:

- Primary-family accuracy >=90%.
- Companion precision >=90%.
- Companion recall for truth-REQUIRED families >=90%.
- Companion recall for truth-CONDITIONAL families >=90%.
- Dangerous omitted REQUIRED permits = 0.
- Every unresolved companion remains visible as CONDITIONAL/NEEDS_INPUT/VERIFY.

Stop condition:

If official sources cannot establish companion truth for more than 20% of pilot contracts, stop. Do not fabricate rules or loop on code; redefine those contracts as conditional/needs-input and renegotiate the measurable supported scope.

### Session 4 — One sealed blind validation

Build a fresh 100-case blind set by truth builders independent of the runtime implementers.

Requirements:

- fresh, non-alphabet-clustered AHJs;
- diverse job archetypes and authority models;
- at least 15% non-REQUIRED decisions;
- meaningful companion-bearing cases;
- threshold conditionals and jurisdiction ambiguity;
- in-scope and out-of-scope cases;
- official-source truth with dual independent review;
- cases, truth, metrics, and hashes sealed before execution.

Run exactly once.

Acceptance gates:

- In-scope primary-family accuracy >=90%.
- Companion precision >=90%.
- Companion REQUIRED recall >=90%.
- Companion CONDITIONAL recall >=90%.
- Dangerous omissions = 0.
- Decision accuracy >=95% with zero confident REQUIRED↔NOT_REQUIRED flips.
- Honest abstention/needs-input behavior for unsupported or incomplete inputs.
- Complete-set exact match reported transparently, with >=75% as a secondary target rather than a gaming incentive.
- Filing destination and URL role do not regress below the current engine baseline.

Deployment is a separate Boban-approved action after this program passes.

## Metric and denominator rules

The denominator is a pre-published list of `jurisdiction × job-scope ontology node × fact profile` contracts frozen before blind execution.

Anti-gaming rules:

- At least 70% of the blind sample must come from the declared supported population.
- The system cannot label a case out-of-scope after seeing the answer.
- Abstentions never count as accurate in-scope answers.
- VERIFY/NEEDS_INPUT is budget-capped and must name the missing trigger fact.
- Empty companion output cannot receive a misleading headline without precision/recall and constant-empty baseline reporting.
- Post-seal denominator or truth edits are prohibited.

## Mandatory anti-neutering invariants

1. Authenticated server-held Decision Cells remain authoritative.
2. Manifest projection cannot change `permit_decision`.
3. Existing enriched-cell counts and W4 packet-field census remain unchanged.
4. Source-backed companions cannot be deleted to improve scores.
5. Every REQUIRED/CONDITIONAL companion carries provenance.
6. Unsourced companions demote to VERIFY rather than disappear.
7. Filing destinations, URLs, and office fields remain non-regressed.
8. API/UI/report/share surfaces serialize from the same manifest.
9. Finalizers are wording-only and fail closed on regulated-field differences.
10. Any patch containing benchmark-case-specific production strings is rejected.

## Anti-loop stop rules

- The current v1.1 set becomes diagnosis/development data only and is never used as final proof.
- No provider reruns during Sessions 1–3 unless a separately approved harness smoke is essential.
- Every implementation change must name the failure category it solves.
- No per-case production patches.
- One blind set, one pre-registered run.
- If blind results differ by more than 15 points from the repaired dev set, stop and audit truth/selection bias.
- If more than 30% of blind misses come from novel failure categories, stop: the architecture hypothesis is falsified.
- Do not create benchmark v1.3 and continue patching after a failed blind run.
- The number of regulated-field writers/mutators must shrink in Session 2; if it grows, reject the design.

## Final verdict

Proceed with this four-session program only. It preserves PermitAssist's full scope, existing enriched capability, trust boundary, and fail-closed behavior while removing the benchmark/scorer incentives that would otherwise reward neutering the product.

Fable 5 verdict: `APPROVE_WITH_CHANGES`.
