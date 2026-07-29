# Session 1 Forensics and Official-Truth Audit

## Verdict

The preserved v1.1 benchmark is mechanically intact but companion truth is not fit for a 100-case precision/recall headline. Session 1 therefore fails closed on companion denominators instead of rewarding empty truth.

No provider was called, no raw was regenerated, no truth value was changed, and no runtime/product file was modified.

## Inputs

- Cases: 100 unique
- Preserved raw envelopes: 500 (five paths × 100)
- Paths: `engine`, `engine_luna`, `grok`, `gemini`, `luna`
- Fable 5 independent consultation SHA-256: `e733aa4a4e16c03c39ba2fd7dfb9adcb5286c25fcb4d2cdfb697cb40af822155`
- Final bounded plan SHA-256: `c799b18e51c108a40a12320fa6eb8d94e9f07de4843c60a176767eac64856029`

## Complete mismatch census

`mismatch_forensics_v12.jsonl` contains one evidence-bound record for every non-exact preserved `(case, path, dimension)` result:

- Total mismatch records: **611**
- Decision: **142**
- Primary family: **110**
- Companion set/status against original v1.1 truth: **359**
- Unique `(case, path, dimension)` keys: **611**
- Empty taxonomy assignments: **0**
- Raw references implicated: **424**

Path counts:

- Luna: 176
- Grok: 135
- Gemini: 124
- Engine: 89
- `engine_luna`: 87

Taxonomy assignments (multi-label, so totals exceed 611):

- `MODEL_OR_GENERIC_FALLBACK_GUESS`: 435
- `COMPANION_CLOSURE_INCOMPLETE`: 347
- `POST_RECONCILIATION_MUTATION`: 15
- `SCOPE_TAXONOMY_UNSUPPORTED`: 12
- `PUBLIC_RENDER_DIVERGENCE`: 6
- `RULE_OR_EXEMPTION_MISSING`: 4

Every record binds:

- raw relative path;
- raw-envelope SHA-256;
- raw-answer SHA-256;
- expected and adapted actual value;
- official source URLs;
- source-packet SHA-256;
- source-excerpt SHA-256 values;
- one or more categories from the established 10-category executable-coverage taxonomy.

## Engine decision forensics

The engine remains **96/100** on decision with four abstentions and zero confident `REQUIRED ↔ NOT_REQUIRED` flips:

1. `official-nyc-backyard-shed-placement-conditional`: expected `CONDITIONAL`, actual `VERIFY`.
2. `official-seattle-commercial-reroof-area-conditional`: expected `CONDITIONAL`, actual `VERIFY`.
3. `source-reviewed-c100-050`: expected `NOT_REQUIRED`, actual `VERIFY`.
4. `source-reviewed-r100-048`: expected `NOT_REQUIRED`, actual `VERIFY`.

These are attributed to `RULE_OR_EXEMPTION_MISSING`, not a generic model-quality bucket. Session 1 does not patch them.

## Primary-family measurement repair

v1.1 scored the engine’s top-level display `permit_kind` even when a typed primary row existed in `permits_required[0].family`. Benchmark v1.2 reads typed fields first without changing raw bytes.

Result on the same preserved engine raws:

- v1.1 engine family: 42/100
- v1.2 typed engine primary: **96/100**
- constant `BUILDING` baseline: 96/100

The repaired number confirms projection/measurement loss but does not prove broad product superiority: it only ties the trivial corpus baseline. The four remaining primary misses align with the four abstention cases.

## Companion truth audit

### Original distribution

- Empty expected companions: 93/100
- Non-empty expected companions: 7/100
- Constant-empty exact-set baseline: 93/100

### Required Session 1 labels for all empty sets

- `confirmed-none`: **0**
- `truth-incomplete`: **93**

No empty companion set had preserved affirmative official evidence establishing complete absence of all possible companion permits. Session 1 therefore does not assume any empty set is correct.

### Non-empty records

Four cases have preserved official excerpts that enumerate the scoped conditional families at the claimed status and form the frozen v1.2 companion denominator:

- Albertville, AL residential remodel
- Andalusia, AL residential remodel
- Anniston, AL residential remodel
- Anderson County, SC residential remodel

Together they contain:

- truth-CONDITIONAL companion items: **18**
- truth-REQUIRED companion items: **0**

The other three non-empty records remain `truth-incomplete` because the preserved excerpts do not establish every claimed family at the claimed REQUIRED/CONDITIONAL status:

- Matanuska-Susitna Borough, AK commercial TI
- Anne Arundel County, MD commercial TI
- NYC backyard shed landmarks conditional

### Consequence

Benchmark v1.2 can report:

- companion precision on the four eligible cases;
- companion CONDITIONAL recall over 18 items;
- complete-set exactness on those four cases;
- dangerous omissions for adjudicated REQUIRED items (currently denominator 0).

It must report companion REQUIRED recall as **N/A**, not 0% or 100%, because the independently supportable denominator is zero.

No truth correction was applied. This avoids changing truth to improve any path’s score. `truth_audit_v12.json` preserves every source excerpt, URL, source packet hash, original truth origin, and Fable review binding.

## Offline v1.2 rescore

| Path | Decision | Primary family | Companion precision* | Conditional recall* | Required recall* | Decision abstentions | Confident flips |
|---|---:|---:|---:|---:|---:|---:|---:|
| Engine | 96/100 | 96/100 | 0/9 (0.0%) | 0/18 (0.0%) | N/A (0 items) | 4 | 0 |
| `engine_luna` | 99/100 | 98/100 | 0/9 (0.0%) | 0/18 (0.0%) | N/A | 0 | 0 |
| Grok | 64/100 | 85/100 | 10/11 (90.9%) | 10/18 (55.6%) | N/A | 34 | 0 |
| Gemini | 54/100 | 53/100 | 3/3 (100%) | 3/18 (16.7%) | N/A | 46 | 0 |
| Luna | 45/100 | 58/100 | 6/6 (100%) | 6/18 (33.3%) | N/A | 48 | 0 |

\* Companion metrics use only the four source-supportable records. They are development diagnostics, not promotion claims.

Engine companion failures on this denominator are status/closure failures rather than evidence to suppress companions:

- Albertville families appear as `VERIFY` instead of `CONDITIONAL`.
- Andalusia/Anniston/Anderson trade families are inflated to `REQUIRED`; several conditional families are omitted.

Session 2 may address serialization/status parity only with explicit approval. Session 1 changes nothing in runtime.

## Constant baselines

Reported beside every path:

- Constant `REQUIRED`: 95/100 decision
- Constant `BUILDING`: 96/100 primary family
- Constant empty under legacy truth: 93/100 exact set, explicitly marked invalid as a v1.2 companion headline
- Constant empty on v1.2 eligible cases: 0/4

## Ontology audit

The canonical ontology closes:

- 15 unique truth labels;
- 84 unique preserved raw/runtime/model labels;
- 18 unique v2.4 Cell `permit_kind` labels.

Closure: **PASS**. Unknown labels fail to `VERIFY`, not `BUILDING`.

## Independent review

Two independent layers are preserved:

1. Fable 5’s pre-Session-1 consultation independently identified the base-rate, enum-closure, projection, and under-filled-truth defects.
2. `independent_review_v12.py` independently re-parses the generated artifacts without importing `benchmark_v12.py` or any `api` module. It recomputes raw bindings, mismatch completeness, arithmetic, truth labels, ontology closure, protected hashes, and no-provider/runtime boundaries.

Independent deterministic review result: **PASS — 29 checks, 0 failures**.

## Limits carried forward

- v1.1 is diagnosis/development data only.
- Required-companion recall is not measurable from this preserved corpus.
- The current four-case companion denominator is too small for promotion or deployment decisions.
- No result authorizes a runtime change, data promotion, paid rerun, or production action.
