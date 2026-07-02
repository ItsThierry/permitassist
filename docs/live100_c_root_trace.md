# Live100 C-root trace — Phase 0, no code changes

Date: 2026-07-02
Artifact root: `artifacts/live_customer_100_fable5_customer_pov_20260702T121009Z`
Baseline result: `A=12 B=66 C=22 F=0` from `FINAL_FACTCHECKED_FABLE5_DOUBLECHECK_REPORT.md:7-12`.

## Phase-0 verdict

Tracing **does not contradict** the Fable 5 plan. It confirms the root cause but with an important refinement:

- PermitAssist already has partial request-scope machinery (`ScopeFactsV2`, a family reconciliation gate, and public-packet projection), but it is **not a closed-world project-scope model**.
- Current outputs are still assembled from a mixture of request text, LLM/retrieval rows, advisory prose, post-hoc gates, and renderer projection. REQUIRED rows can remain associated with the lookup rather than derived from explicit scope attributes.
- Several gates are order-dependent and lossy: some current code can identify/veto an overreach when re-run in isolation, while the recorded customer artifact still contains that overreach after the full production path.
- The fix must not be subtractive-only: `C-010` proves recall/priority failure (`sign_illuminated` is detected but electrical stays conditional), and `R-049` in the Fable report proves missing trade-standard requirements (gas pressure test).

**Stop point:** This document completes Phase 0 only. Do not start Phase 1 implementation until Boban approves.

---

## Repository state observed

Command run:

```bash
git status --short && git branch --show-current && git log --oneline -5
```

Output:

```text
M api/scope_contract.py
fix/universal-customer-view-20260629
d2c2626 fix: add full customer packet reconciliation gates
433d2f9 fix: align repaired action-path source metadata
b2443dd fix: repair Live100 action paths without neutering
486f6d4 fix: harden universal customer permit packets
1161f67 fix: implement Live100 fix-for-good invariants
```

Note: `api/scope_contract.py` was already modified before this Phase-0 doc write in this session. This Phase 0 created only `docs/live100_c_root_trace.md`.

---

## Pipeline map with file:line anchors

### 1. Intake / request model

- HTTP `/api/permit` is handled in `api/server.py:10458-10658`.
- The customer response is built by `build_customer_permit_view_model(...)` before serialization at `api/server.py:10656-10658`.
- Share/save/report paths also call the same customer ViewModel boundary:
  - saving share result: `api/server.py:7727-7730`
  - reading shared result: `api/server.py:7764-7767`
  - checklist route: `api/server.py:10920-10923`
  - sample/demo response path: `api/server.py:10700-10702`

Observed intake shape from Live100 `cases.jsonl` for traced records:

```json
{
  "id": "R-033|C-010|R-034",
  "job_type": "free-text project scope",
  "segment": "residential|commercial",
  "city": "...",
  "state": "...",
  "zip_code": "...",
  "job_value": 1200
}
```

There is no closed-enum `ProjectScopeAttributes` object in the recorded API artifact.

### 2. Retrieval + LLM synthesis layer

Primary lookup entry:

- `research_permit(...)` starts at `api/research_engine.py:8980`.
- It builds a request-level scope contract before cache/retrieval at `api/research_engine.py:8991-8997`.
- Cache hits are post-processed and still run the same guard/enrichment stack at `api/research_engine.py:9051-9068`.
- Web/search context is built at `api/research_engine.py:9110-9114`; prompt context is assembled at `api/research_engine.py:9132-9148`.
- The LLM user prompt asks for exact permit names, fees, inspections, and application details at `api/research_engine.py:9176-9206`.
- OpenAI/Gemini calls still generate JSON that includes authoritative customer facts, not just prose:
  - Gemini JSON call: `api/research_engine.py:9221-9237`
  - OpenAI JSON call: `api/research_engine.py:9239-9254`
- LLM result parsing and repair happens at `api/research_engine.py:9291-9341`.

This is the first major architectural mismatch with the target plan: the LLM currently emits structured facts/families/fees/docs, then later code repairs them. In the target architecture, the LLM must only classify closed enums or rephrase a deterministic `DecisionObject`.

### 3. Existing request-scope model

Existing scope data is request-text regex/heuristic based, not a closed enum with confidence and unknown handling.

- `ScopeFactsV2` dataclass: `api/scope_contract.py:125-154`.
- Trade/special/negative pattern tables: `api/scope_contract.py:157-203`.
- V2 positive facts are derived in `_scope_facts_v2_positive(...)`: `api/scope_contract.py:319-398`.
- V2 negative facts are derived in `_scope_facts_v2_negative(...)`: `api/scope_contract.py:401-443`.
- `build_scope_facts_v2(...)` constructs the object: `api/scope_contract.py:446-469`.
- `build_scope_contract(...)` constructs a broader category/vertical/firebreak contract: `api/scope_contract.py:533-590` and following.
- The final scope-text firebreak only removes forbidden customer-visible strings after the fact: `api/scope_contract.py:793-853`.

Important observed failure source:

- `_SPECIAL_SIGNAL_PATTERNS["health"]` includes bare `"kitchen"` at `api/scope_contract.py:181-189`.
- `R-033` is a residential *kitchen outlet* GFCI swap, so the current regex model extracts `food_service` despite the scope not being food-service regulated.

### 4. Filing packet reconciliation

- Filing family registry includes food/FOG/CO families:
  - health food: `api/filing_packet_reconciler.py:100-107`
  - wastewater/FOG: `api/filing_packet_reconciler.py:116-123`
  - CO/change-of-occupancy begins at `api/filing_packet_reconciler.py:124-127`
- `detect_filing_scope_signals(...)` extracts signals from both the **request scope** and selected **advisory/result fields**: `api/filing_packet_reconciler.py:350-373`.
- Commercial TI/change-of-use adds a building-TI filing signal at `api/filing_packet_reconciler.py:376-382`.
- Electrical signal rule: `api/filing_packet_reconciler.py:383-386`.
- Commercial kitchen/food-service signal rule: `api/filing_packet_reconciler.py:391-392`.
- FOG/wastewater signal rule: `api/filing_packet_reconciler.py:415-417`.
- CO/change-of-use signal rule: `api/filing_packet_reconciler.py:422-423`.
- New required filing rows are created by `_new_row(...)`: `api/filing_packet_reconciler.py:629-657`.
- `ensure_required_filing_rows(...)` injects/repairs rows and forces `permit_decision=REQUIRED` when rows exist: `api/filing_packet_reconciler.py:684-780`, especially `api/filing_packet_reconciler.py:710-749`.

Critical issue: because `detect_filing_scope_signals` uses `advisory_text` from existing result rows/prose (`api/filing_packet_reconciler.py:353-373`), a bad LLM/result association can become a new filing signal. This is association-based composition, not closed-world derivation.

### 5. Decision contract / finalizer / customer ViewModel

- `apply_permit_decision_contract(...)` normalizes to REQUIRED/NOT_REQUIRED and stores a decision contract: `api/permit_decision.py:1087-1199`.
- The pure final projection boundary is `finalize_customer_public_projection(...)`: `api/server.py:4371-4395`.
- Main customer ViewModel builder is `build_customer_permit_view_model(...)`: `api/server.py:4664-5071`.
- It invokes scope contract, residential gate, decision contract, filing reconciler, source filters, final gates, and public packet projection in sequence:
  - scope contract: `api/server.py:4675-4679`
  - pre-sanitize/decision resolver: `api/server.py:4728-4748`
  - filing row injection inside ViewModel: `api/server.py:4764-4766`
  - public summary derivation: `api/server.py:4830-4836`, `api/server.py:4880-4886`, `api/server.py:4930-4937`, `api/server.py:4971-4979`
  - final family reconciliation gate, AHJ guard, public-packet projection: `api/server.py:5050-5068`
- `_build_customer_result_summary(...)` merges fee/cost/timeline/source fields for the customer summary: `api/server.py:2659-2703`.

### 6. Family reconciliation gate

The current gate is useful but still not the target closed-world composer.

- Family classifier: `api/family_reconciliation_gate.py:42-60`, `api/family_reconciliation_gate.py:104-148`.
- Conditional text table: `api/family_reconciliation_gate.py:82-93`.
- Positive support checks: `api/family_reconciliation_gate.py:165-187`.
- Veto checks from negative facts: `api/family_reconciliation_gate.py:190-210`.
- Row reconciliation: `api/family_reconciliation_gate.py:248-325`.
- Required mirror syncing chooses first kept row as lead: `api/family_reconciliation_gate.py:328-347`.
- Gate entry point: `api/family_reconciliation_gate.py:350-373`.

Key gaps:

- `sign_illuminated` exists in scope facts, but `_positive_supports_family(...)` does not map it to electrical support (`api/family_reconciliation_gate.py:165-187`).
- `co_change_of_occupancy` is supported by generic `sign`/`planning_zoning` positives (`api/family_reconciliation_gate.py:174-175`) instead of being hard-gated to explicit `change_of_use=true`.
- Lead permit is still effectively first-row/first-kept-row (`api/family_reconciliation_gate.py:334-337`), not a deterministic lead-permit rule table keyed to primary scope.

### 7. Public packet / customer data shapes

- `PublicPacketDTO` rows have `decision`, `family`, `permit_name`, `reason`, `conditional_text`, `source`, `action_url`, `fees`, `documents`, `inspections`: `api/public_packet.py:1-28`.
- `build_public_packet(...)` builds rows from `permits_required` and conditionals: `api/public_packet.py:89-123`.
- `_row_from_permit(...)` copies row fee/docs/inspections or falls back to global `result.fee_range`, `result.what_to_bring`, `result.requirements`, `result.inspections`: `api/public_packet.py:79-85`.
- `apply_public_packet_projection(...)` writes `public_packet`, `canonical_public_packet`, `public_packet_rows`, and customer summaries: `api/public_packet.py:154-173`.

Critical issue: a single untyped `fee_range` and global docs/inspections can be stamped onto every family row. This is visible in `R-033`, `R-034`, and `C-010` traces below.

### 8. Renderer and visible-decision verifier

- Share/report rendering calls `_sanitize_customer_result_for_request_scope(...)` and embeds sanitized report payload: `api/server.py:8178-8215`.
- The full-rendered verification artifact shows existing verifier scope:
  - `render_ok=100`: `FULL_RENDERED_VERIFICATION.md:7-8`
  - `decision_visible_match=100`: `FULL_RENDERED_VERIFICATION.md:9`
  - `source_reachability_rate=0.6866`: `FULL_RENDERED_VERIFICATION.md:13-15`
  - no secret leaks / no hard contradictions / no segment template leaks: `FULL_RENDERED_VERIFICATION.md:16-18`

The existing verifier catches overall rendered decision-vs-API decision, source status, and coarse contradictions. It does **not** prove per-family/status/lead/doc/fee fidelity against a `DecisionObject` because that object does not yet exist.

### 9. Fee and source/link data shapes

- Fee is primarily a single string field `fee_range`, with guard/caveat logic in `apply_fee_verify_caveat(...)`: `api/research_engine.py:6980-7078`.
- Vague/wide fee guard rewrites `fee_range`: `api/research_engine.py:9357-9397`.
- Total/project cost estimate fallback is separate but can be used in customer summary/caveat: `api/research_engine.py:9506-9531` and `api/server.py:2686-2690`.
- Public-packet row fee fallback copies `result.fee_range` to rows: `api/public_packet.py:79-85`.
- Source normalization/classification starts at `api/research_engine.py:410-412` and `api/research_engine.py:812-814`.
- Locality filtering begins at `api/research_engine.py:930-931` and is run at `api/research_engine.py:9703-9713`.
- `sanitize_result_urls(...)` validates only a top-level apply URL at `api/server.py:5075-5091`; the Live100 rendered verifier separately records `apply_status` and `source_statuses` in `FULL_RENDERED_VERIFICATION.json`.

There is no rendered fee object with `fee_type ∈ {permit_fee, project_cost_estimate, benchmark_estimate}` in the traced API outputs.

---

## End-to-end traces with logged intermediates

Trace command used (read-only, no source writes):

```bash
python3 - <<'PY'
from pathlib import Path
import json, sys
ROOT=Path('/home/boban/projects/permitassist')
sys.path.insert(0, str(ROOT/'api'))
from scope_contract import build_scope_contract, build_scope_facts_v2
from filing_packet_reconciler import detect_filing_scope_signals, ensure_required_filing_rows
from family_reconciliation_gate import apply_family_reconciliation_gate
# Load cases.jsonl from the Fable5 Live100 artifact and print intake,
# scope_contract, ScopeFactsV2, filing signals, artifact rows, and isolated gate output.
PY
```

### R-033 — residential GFCI outlet swap, food/FOG leak

**Intake:** `replace 12 kitchen outlets with GFCI in existing boxes no new circuits, job value 1200`; residential; St. Louis, MO.

**Fable final C reason:** `FINAL_FACTCHECKED_FABLE5_DOUBLECHECK_REPORT.md:38` — wastewater/FOG and health food rendered REQUIRED for a residential GFCI swap; title says New Circuit despite no-new-circuits scope.

Logged intermediates:

```json
{
  "scope_contract": {
    "category": "residential",
    "family": "residential_other",
    "vertical": "generic",
    "forbidden_scope_tags": ["commercial_food", "commercial_health", "commercial_ti", "medical_clinic_ti", "office_ti", "residential_adu", "residential_solar", "restaurant_ti", "retail_ti", "solar_pv"]
  },
  "scope_facts_v2": {
    "positive_facts": ["electrical", "food_service"],
    "negative_facts": ["existing_circuits", "no_food_service_change"],
    "special_signals": ["health"],
    "trade_signals": ["electrical"],
    "dominant_family": "electrical"
  },
  "filing_signals": [
    "electrical_work -> electrical",
    "grease_fog_wastewater -> plumbing,wastewater_pretreatment_fog",
    "planning_zoning_clearance -> planning_zoning",
    "fire_life_safety_review -> fire_suppression"
  ],
  "artifact_required_rows": [
    "Electrical Permit REQUIRED",
    "Food Establishment Health Plan Review / Permit REQUIRED"
  ],
  "artifact_summary": "Required permit package: Electrical Permit; Food Establishment Health Plan Review / Permit."
}
```

Observed injection points:

1. The word `kitchen` triggers `special_signals=["health"]` because `_SPECIAL_SIGNAL_PATTERNS["health"]` includes bare `"kitchen"` at `api/scope_contract.py:181-189`.
2. `_scope_facts_v2_positive(...)` converts `health` into `food_service` at `api/scope_contract.py:339-340`.
3. The negative rule correctly adds `no_food_service_change` for residential non-food scopes at `api/scope_contract.py:431-432`, but the recorded artifact still had a REQUIRED food row. This proves the current gates are not a single authoritative closed-world composer.
4. `detect_filing_scope_signals(...)` reads advisory/result text as well as request text (`api/filing_packet_reconciler.py:350-373`), so a bad food/FOG row or prose can be reinterpreted as scope evidence.
5. `public_packet` stamps the global electrical `$85` fee and GFCI docs onto the unrelated health row via fallback fields (`api/public_packet.py:79-85`).

Isolated current gate check on the recorded artifact:

```json
{
  "gated_required_rows": ["Electrical Permit"],
  "gated_audit": [
    {"action": "KEEP", "family": "electrical", "basis": "positive scope fact or source-backed row"},
    {"action": "VETO", "family": "health_food", "basis": "negative fact no_food_service_change contradicts food/FOG trigger"}
  ]
}
```

Interpretation: current local gate can veto this if applied cleanly at the right boundary, but the live artifact proves the production path still serialized the overreach. Phase 3 must replace order-dependent repair with a deterministic `DecisionObject` composer.

### C-010 — illuminated sign, electrical demoted + CO overreach + fee conflation

**Intake:** `install monument sign and illuminated wall sign for retail tenant, job value 22000`; commercial; Gilbert, AZ.

**Fable final C reason:** `FINAL_FACTCHECKED_FABLE5_DOUBLECHECK_REPORT.md:43` — CO/change-of-occupancy REQUIRED for sign install, fabrication/installation costs mixed into fee line, electrical conditional despite explicit illuminated sign.

Logged intermediates:

```json
{
  "scope_contract": {
    "category": "commercial",
    "family": "commercial_other",
    "vertical": "generic"
  },
  "scope_facts_v2": {
    "positive_facts": ["sign", "sign_illuminated"],
    "negative_facts": [],
    "occupancy_change": false,
    "trade_signals": []
  },
  "artifact_required_rows": [
    "Sign Permit REQUIRED",
    "Planning / Zoning Use Clearance REQUIRED",
    "Certificate of Occupancy / Change-of-Occupancy Approval REQUIRED"
  ],
  "artifact_related_rows": [
    "Electrical Permit — Illuminated Sign CONDITIONAL"
  ],
  "artifact_fee_range": "Permit fees need verification ... $300 - $900 permit/plan review fees plus $4,200 - $11,600 for fabrication, electrical, and installation"
}
```

Observed injection points:

1. Scope extraction detects `sign_illuminated` at `api/scope_contract.py:362-365`, but no `electrical` positive fact is added for illuminated signs.
2. `_positive_supports_family(...)` handles `sign` at `api/family_reconciliation_gate.py:170-171`, but does not treat `sign_illuminated` as electrical at `api/family_reconciliation_gate.py:165-187`.
3. The add loop only adds families for `_positive_supports_family(...)` matches at `api/family_reconciliation_gate.py:303-315`; therefore electrical remains conditional instead of REQUIRED even though the attribute exists.
4. CO/change-of-occupancy is kept because `_positive_supports_family(...)` allows `planning_zoning` and `co_change_of_occupancy` when positives include `sign` (`api/family_reconciliation_gate.py:174-175`). This violates planned invariant I3: CO only when `change_of_use=true`.
5. Fee conflation is structural: `fee_range` is a single string (`api/research_engine.py:9357-9397`, `api/research_engine.py:6980-7078`), and public packet row fees copy that string (`api/public_packet.py:79-85`). There is no `fee_type` to prevent project/fabrication cost from rendering under Fees.
6. Rendered verification caught the apply/source URLs as 403 in `FULL_RENDERED_VERIFICATION.json`, but the customer artifact still rendered them because liveness is not a pre-render quarantine gate.

Isolated current gate check on the recorded artifact:

```json
{
  "gated_required_rows": [
    "Sign Permit",
    "Planning / Zoning Use Clearance",
    "Certificate of Occupancy / Change-of-Occupancy Approval"
  ],
  "gated_conditional_rows": ["Electrical Permit — Illuminated Sign"],
  "gated_audit": [
    {"action": "KEEP", "family": "sign"},
    {"action": "KEEP", "family": "planning_zoning"},
    {"action": "KEEP", "family": "co_change_of_occupancy"}
  ]
}
```

Interpretation: this is not fixable by subtractive filtering alone. The system already knows `sign_illuminated`, but no bidirectional trade rule promotes electrical to REQUIRED, while an overly broad sign/planning support rule keeps CO REQUIRED.

### R-034 — battery storage mislabeled as Solar PV structural lead

**Intake:** `install residential battery backup tied to existing solar system, job value 18000`; residential; Boise, ID.

**Fable final C reason:** `FINAL_FACTCHECKED_FABLE5_DOUBLECHECK_REPORT.md:39` — lead is `Building Permit — Solar PV (Structural Racking & Roof Penetrations)` for battery-only install; docs demand structural engineering/roof-load items; mechanical fee PDF cited for electrical work.

Logged intermediates:

```json
{
  "scope_contract": {
    "category": "residential",
    "family": "residential_single_trade",
    "vertical": "solar_pv"
  },
  "scope_facts_v2": {
    "positive_facts": ["building", "electrical"],
    "negative_facts": ["no_food_service_change"],
    "trade_signals": [],
    "vertical": "solar_pv"
  },
  "artifact_required_rows": [
    "Building Permit — Solar PV (Structural Racking & Roof Penetrations) REQUIRED",
    "Electrical Permit — Solar PV / Battery System REQUIRED"
  ],
  "artifact_related_rows": [
    "Utility Interconnection / Permission to Operate VERIFY"
  ]
}
```

Observed injection points:

1. Any `solar`/`pv` term adds both `electrical` and `building` positives at `api/scope_contract.py:356-357`.
2. `build_scope_contract(...)` sets `vertical="solar_pv"` when any solar/PV term appears at `api/scope_contract.py:570-572`. The phrase is “tied to existing solar system,” not “install new roof PV/racking.”
3. The current scope model has no first-class `battery_storage` / `ess` attribute and no distinction between existing PV context and new PV structural work.
4. Family classification maps structural/solar-like rows into generic building: `api/family_reconciliation_gate.py:119-122`.
5. Lead permit sync sets `permit_name` to the first required row at `api/family_reconciliation_gate.py:334-337`, so a retained building/solar structural row becomes the headline lead.
6. Public-packet row construction copies the same global docs/inspections/fee into both building and electrical rows (`api/public_packet.py:79-85`), preserving structural/racking docs for a battery tie-in.

Isolated current gate check on the recorded artifact:

```json
{
  "gated_required_rows": [
    "Building Permit — Solar PV (Structural Racking & Roof Penetrations)",
    "Electrical Permit — Solar PV / Battery System"
  ],
  "gated_audit": [
    {"action": "KEEP", "family": "building"},
    {"action": "KEEP", "family": "electrical"}
  ]
}
```

Interpretation: the root is classification/lead, not simple over-inclusion. The system needs closed attributes (`battery_storage`, `existing_pv_context`, `new_pv_panels`, `roof_penetrations`, `structural_mounting`) and a deterministic lead rule table.

---

## Six C-taxonomy categories attributed to code/data locations

| C-taxonomy category | Evidence | Current code/data location | Why current architecture fails |
|---|---|---|---|
| 1. Unrelated REQUIRED family / contamination | R-033 food/FOG for residential GFCI; many Fable C cases in `FINAL_FACTCHECKED...:30-52` | Scope health special signal includes bare kitchen (`api/scope_contract.py:181-189`); `food_service` positive added from health (`api/scope_contract.py:339-340`); filing signals read advisory/result text (`api/filing_packet_reconciler.py:350-373`) and food/FOG phrases (`api/filing_packet_reconciler.py:391-417`) | Bad association can become new filing evidence; REQUIRED rows not exclusively derived from closed scope predicates. |
| 2. Missing/demoted required family / priority recall | C-010 electrical conditional despite `illuminated wall sign`; R-049 missing gas pressure test per `FINAL_FACTCHECKED...:42` | `sign_illuminated` extracted (`api/scope_contract.py:362-365`) but not mapped to electrical support/add (`api/family_reconciliation_gate.py:165-187`, `api/family_reconciliation_gate.py:303-315`) | A filter-only fix would worsen this. Need bidirectional trade rules and trade-standard doc requirements. |
| 3. Wrong lead / primary classification | R-034 Solar PV structural lead for battery; C-015 plumbing lead for hood per `FINAL_FACTCHECKED...:44` | Solar/PV context adds building+electrical (`api/scope_contract.py:356-357`); vertical solar_pv from any solar term (`api/scope_contract.py:570-572`); first row becomes lead (`api/family_reconciliation_gate.py:334-337`) | Lead is not determined by explicit primary-work rule table; context terms can dominate primary scope. |
| 4. Off-scope docs/checklists/timelines/prose contradictions | R-033 “New Circuit” despite no new circuits; R-034 roof-load docs; C-010 TI timeline/CO; other cases in `FINAL_FACTCHECKED...:36-51` | LLM prompt asks for exact docs/timelines in generated JSON (`api/research_engine.py:9193-9206`); public packet falls back to global docs/inspections for each row (`api/public_packet.py:79-85`) | Docs/prose are not derived from per-family applicability predicates; renderer cannot prove per-family fidelity. |
| 5. Untyped fees / project cost rendered as fees | C-010 fabrication/install cost in fee line; R-032/R-005/C-036 in Fable report | Single `fee_range` string is rewritten/guarded (`api/research_engine.py:9357-9397`, `api/research_engine.py:6980-7078`); cost fallback exists separately (`api/research_engine.py:9506-9531`) but summary can read fee and total cost together (`api/server.py:2686-2690`); row fee fallback copies global fee (`api/public_packet.py:79-85`) | No `fee_type`, so renderer cannot enforce “permit fee only under Fees.” |
| 6. Source/apply link liveness/relevance defects | Full verifier source reachability only 68.66%; C-010 all Gilbert/ROC links 403; many C cases have DNS/404/blog/placeholder defects | Existing verifier records statuses in artifact (`FULL_RENDERED_VERIFICATION.md:7-18`), but rendering still permits failed URLs; top-level URL sanitizer is limited (`api/server.py:5075-5091`); source locality filter is not liveness/relevance quarantine (`api/research_engine.py:930-931`, `api/research_engine.py:9703-9713`) | Liveness is post-run evidence, not a deterministic pre-render gate with quarantine/fallback to verified AHJ landing pages. |

---

## Commands run during Phase 0

```bash
# Repo state
git status --short && git branch --show-current && git log --oneline -5

# Read-only artifact/code discovery via Hermes tools:
# - search_files over api/, scripts/, tests/, and artifact root
# - read_file for Fable reports, core API files, tests, verifier artifacts

# Read-only trace script, no writes:
python3 - <<'PY'
from pathlib import Path
import json, sys
ROOT=Path('/home/boban/projects/permitassist')
sys.path.insert(0, str(ROOT/'api'))
from scope_contract import build_scope_contract, build_scope_facts_v2
from filing_packet_reconciler import detect_filing_scope_signals, ensure_required_filing_rows
from family_reconciliation_gate import apply_family_reconciliation_gate
# loaded R-033/C-010/R-034 from artifacts/.../cases.jsonl and printed intermediates
PY
```

No tests were run in Phase 0 because the task was explicitly tracing/documentation only and forbade implementation before this gate.

---

## Exact commands for later phases

Use these from repo root `/home/boban/projects/permitassist`.

### Baseline/source-control sanity

```bash
git status --short
git branch --show-current
git diff --stat
```

### Focused current contract tests before Phase 1 patches

```bash
PYTHONPATH=api python3 -m pytest -q \
  tests/test_scope_facts_v2.py \
  tests/test_family_reconciliation_gate.py \
  tests/test_public_packet_parity.py \
  tests/test_c_case_contracts.py \
  tests/test_fee_guards.py \
  tests/test_public_share_report_boundary.py
```

### Characterization tests to add before patching Phase 1/3

Add failing tests that encode the corrected `DecisionObject` expectations for:

```bash
PYTHONPATH=api python3 -m pytest -q \
  tests/test_live100_solve_for_good_scope_attributes.py \
  tests/test_live100_solve_for_good_decision_object.py \
  tests/test_live100_solve_for_good_render_fidelity.py
```

Expected new assertions:

- `R-033`: required family includes electrical only; food/FOG/health are not REQUIRED; no “new circuit” label when `existing_circuits/no_new_circuits` is true.
- `C-010`: sign and electrical are REQUIRED; CO is not REQUIRED unless `change_of_use=true`; fabrication/install cost is not rendered as permit fee.
- `R-034`: electrical/ESS is lead; Solar PV structural/racking is not lead unless new PV panels/roof penetrations/structural mounting are in scope.

### Offline old-vs-new replay gate

Existing replay script currently points at the prior `20260701T234354Z` artifact in `scripts/full_customer_fix_for_good_replay_20260702.py:26`. For this Fable5 run, either update/add a new script argument or copy it to a new dated script with:

```python
ARTIFACT_ROOT = ROOT / "artifacts" / "live_customer_100_fable5_customer_pov_20260702T121009Z"
```

Then run:

```bash
PERMITASSIST_FULL_CUSTOMER_FIX_FOR_GOOD=1 \
PYTHONPATH=api:tests \
python3 scripts/full_customer_fix_for_good_replay_20260702.py
```

Gate for Phase 3 cutover:

- all 22 C defects gone in corrected DecisionObjects/customer packet,
- zero recall regression on the 78 A/B cases,
- every previously-correct REQUIRED family remains REQUIRED unless adjudicated otherwise.

### Render / visible verifier gate

Existing artifact verifier output is `artifacts/live_customer_100_fable5_customer_pov_20260702T121009Z/FULL_RENDERED_VERIFICATION.md` and `.json`. The next implementation phase must extend this from overall decision matching to per-family/status/lead/doc/fee fidelity.

Target command shape for the new verifier:

```bash
PYTHONPATH=api python3 scripts/live100_render_fidelity_verification.py \
  --artifact-root artifacts/live_customer_100_fable5_customer_pov_20260702T121009Z \
  --decision-objects artifacts/live100_solve_for_good_offline/decision_objects.jsonl \
  --out artifacts/live100_solve_for_good_offline/render_fidelity.json
```

Required gate: `100/100` pass for family/status/lead parity and contradiction lint.

### Link liveness/relevance dry run gate

Target command shape for Phase 4:

```bash
PYTHONPATH=api python3 scripts/live100_link_liveness_relevance.py \
  --artifact-root artifacts/live_customer_100_fable5_customer_pov_20260702T121009Z \
  --check-http --check-dns --check-ssl --check-redirect-relevance \
  --reject-placeholder-patterns '/12345,placeholder,tbd' \
  --out artifacts/live100_solve_for_good_offline/link_liveness.json
```

Required gate: zero dead/irrelevant URLs renderable; failures are quarantined and renderer falls back only to verified AHJ landing page.

### Final paid production Live100 — only after local gates pass and Boban approves

Do not run/deploy/push/mutate production without explicit approval. The documented command shape, based on existing artifact conventions, is:

```bash
# Requires Boban-approved paid session/auth setup; do not run until approved.
python3 scripts/live_customer_100_paid_runner.py \
  --cases tests/fixtures/live100_solve_for_good/cases_plan.json \
  --base-url https://permitassist.io \
  --out artifacts/live_customer_100_solve_for_good_$(date -u +%Y%m%dT%H%M%SZ)
```

Then run Fable second-pass fact-check and the extended rendered verifier on that new artifact root.

---

## Remaining risks before Phase 1

1. **Current uncommitted `api/scope_contract.py` change** must be reviewed before patching; do not overwrite it blindly.
2. **Existing gates can hide root causes in isolation.** Example: R-033 isolated local family gate vetoes health food, but live artifact rendered it. Tests must exercise the full ViewModel/render path, not just unit helpers.
3. **C-010 proves no-neuter risk.** The fix must add/keep required electrical for illuminated signs, not just remove CO.
4. **R-034 proves classification/lead risk.** Need separate `battery_storage/ESS` attributes and lead rules; broad `solar_pv` is too coarse.
5. **Fee/link fixes are orthogonal.** Closed-world scope will not solve fee conflation or dead/irrelevant links; Phase 4 must be separate.

## Approval gate

Phase 0 is complete. Await Boban approval before writing Phase 1 tests or modifying implementation code.
