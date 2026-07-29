# PermitAssist Fresh Diagnostic Benchmark v1.1 — Closeout

## Scope

Fresh 100-case diagnostic run across five configured paths, 500 total attempts. This benchmark is diagnostic-only and is not a deployment or promotion gate.

Paths executed:

1. Luna — `gpt-5.6-luna` through `openai-codex`
2. Grok — `x-ai/grok-4.5` through OpenRouter, fallback disabled, observed upstream `xAI`
3. Gemini — `Gemini 3.6 Flash (High)` through Google Antigravity OAuth
4. PermitAssist local engine
5. Configured `engine_luna` adapter

Important semantic limitation: the inherited `engine_luna` adapter is Luna-to-engine augmentation. It does not send the engine result to Luna for finalization (`engine_result_supplied_to_model=false`). Therefore it must not be represented as a true Engine+Luna finalizer benchmark.

## Integrity and execution verification

- Fresh raw envelopes: 500
- Fresh scoreboard rows: 500
- Unique cases: 100
- Paths per case: 5
- Provider transport success: 100/100 for every path
- Typed/schema-valid result coverage: 100/100 for every path
- Requested/observed identity mismatches: 0
- Raw checksum mismatches: 0
- Raw answer content mutations: 0
- Fresh v1.1 checksum manifest: 507/507 verified
- Frozen v1 checksum manifest: 501/501 verified
- Active benchmark/test runner after completion: none

The first smoke attempt used system Python and was invalid because `httpx` and `bs4` were unavailable. Its 30 rows/raws were preserved separately with the suffix `_invalid_python312`. It was not used in the final result. The smoke and full run were then executed with the project-capable Hermes virtual environment.

A scorer projection defect discovered during closeout omitted engine jurisdiction fields. It was corrected offline, covered by the 18-test harness suite, and the final scoreboard was rebuilt from the same preserved 500 raw envelopes. No provider calls were repeated. The pre-correction scoreboard and summary were preserved with `_pre_jurisdiction_projection_fix` filenames.

## Corrected aggregate results

Composite is the total pass rate over nine deterministic dimensions per path (900 checks per path). It is not a substitute for the dimension-level results.

| Rank | Path | Composite | Decision | Family | Companions | Filing office | Destination | URL role | Official evidence | All 9 exact |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | PermitAssist engine | 72.8% | 96% | 42% | 39% | 81% | 98% | 99% | 96% | 1/100 |
| 2 | `engine_luna` configured path | 70.8% | 99% | 41% | 18% | 83% | 98% | 99% | 99% | 0/100 |
| 3 | Grok 4.5 | 66.9% | 64% | 85% | 15% | 24% | 87% | 34% | 100% | 0/100 |
| 4 | Gemini 3.6 Flash High | 65.9% | 54% | 53% | 69% | 14% | 84% | 20% | 100% | 2/100 |
| 5 | Luna | 62.3% | 45% | 58% | 20% | 23% | 78% | 45% | 100% | 1/100 |

## Main findings

- PermitAssist engine ranked first on the nine-dimension aggregate and led on filing destination and URL-role accuracy.
- The configured `engine_luna` path had the best decision accuracy at 99%, but performed worse than engine alone on companion permits and was not a true finalizer architecture.
- Engine-alone decision misses were limited to four cases:
  - `official-nyc-backyard-shed-placement-conditional`
  - `official-seattle-commercial-reroof-area-conditional`
  - `source-reviewed-c100-050`
  - `source-reviewed-r100-048`
- The configured `engine_luna` path missed one decision: `source-reviewed-r100-048`.
- Engine customer egress omitted measurable jurisdiction identity in most rows, producing only 4/100 jurisdiction-identity passes. The `engine_luna` egress produced 0/100. This is a real output-contract measurement result, not a provider failure.
- Permit-family and companion-permit exactness remain major PermitAssist weaknesses.
- Standalone models were strong at returning official evidence, but much weaker on exact filing office and URL-role classification.
- The “all nine dimensions exact” metric is intentionally strict; most rows were classified as semantic failures despite valid transport and schema coverage.

## Artifacts

- `benchmarks/permit_accuracy_v1_1/raw_full_v11/`
- `benchmarks/permit_accuracy_v1_1/scoreboard_full_v11.csv`
- `benchmarks/permit_accuracy_v1_1/diagnostic_full_v11_summary.json`
- `benchmarks/permit_accuracy_v1_1/checksums_full_v11.sha256`
- `benchmarks/permit_accuracy_v1_1/scoreboard_full_v11_pre_jurisdiction_projection_fix.csv`
- `benchmarks/permit_accuracy_v1_1/raw_smoke_v11_invalid_python312/`

## Boundary

No deployment, push, merge, commit, or production activation was performed. These results compare the frozen local diagnostic candidate only.
