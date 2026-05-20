# Lead Pipeline Phase 1 M9 — internal review artifacts

Status: local fixture-only internal review artifact renderer.

M9 turns M8 `export_events` into deterministic local Markdown + JSON files for Boban/Titi review. It is not a sender, CRM sync, campaign system, live acquisition tool, or customer-facing feature.

## Safety boundary

- Local artifact only.
- Reads already-local M8 SQLite `export_events` and their lineage rows.
- No network, browser, scraping, email, webhook, CRM, paid API, or outreach path.
- Every rendered artifact carries the banner: `INTERNAL REVIEW ONLY — Boban/Titi local artifact — send_authorized=false — internal_review_only — no outreach/no CRM/no send`.
- M9 fails closed if an export row has `send_authorized != 0`, non-`internal_review_only` status, non-`internal_review_queue` target, schema drift, network-tainted verification event, missing lineage, non-`fixture://` source lineage, unsupported enrichment claims, or secret-like content.

## CLI

```bash
python -m lead_pipeline.run_internal_review_artifacts --fixture golden --output-dir /tmp/lead-pipeline-m9-review
```

Outputs:

- `lead_pipeline_m9_internal_review_artifacts.json`
- `lead_pipeline_m9_internal_review_artifacts.md`

The CLI prints a JSON manifest containing only local file paths and safety flags.

## Artifact content

Each rendered export includes:

- business label from `entities.canonical_label`;
- ICP reasons from source-backed fact rows (`permitassist_icp_segment`, `low_call_relevance_signal`, `trade_category`);
- source/fixture lineage from `source_observations` with `fixture://` URLs, snippets, and payload hashes;
- fact lineage IDs and snippets;
- suppression event ID/status/reason;
- enrichment event ID/status/unsupported claim count/summary;
- verification event IDs and gate names;
- explicit `send_authorized=false` and `internal_review_only=true`.

## Review intent

Boban/Titi can inspect whether the exported lead is commercially useful before any future live acquisition or outreach milestone is approved. M9 deliberately does not change any production/runtime/customer path.
