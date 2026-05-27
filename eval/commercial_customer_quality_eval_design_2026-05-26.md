# PermitAssist commercial customer-quality eval design — real-key slice (design only)

Status: design only. Do not run against production, do not call live APIs, and do not spend keys until the approval gates below are satisfied.

## Existing repo/eval context inspected

- `scripts/run_eval.py` is the current deterministic harness. It defaults to `https://permitassist.io`, loads `PERMITASSIST_ADMIN_TOKEN` or a private fallback file, calls `/api/permit`, and grades regex/structural checks from `eval/permit_eval_cases.json`.
- `eval/permit_eval_cases.json` currently has 44 cases:
  - 36 commercial, 8 residential.
  - Target launch vertical coverage already present: 10 restaurant TI, 10 medical/dental clinic TI, 10 office TI.
  - Also present: retail/change-of-use, multifamily, ADU/residential controls.
- `tests/test_eval_vertical_scorecard.py` enforces the minimum broad pack coverage and verifies some grading failures.
- `scripts/summarize_stress_eval.py` summarizes cached rows and separates infra failures from engine-quality scores.
- Existing deterministic rubric is useful but too coarse for customer quality: URL/domain regexes, permit counts, broad trigger counts, and generic checklist tokens. It does not strictly grade exact portal/application names, exact application path, source-field provenance, or contractor usefulness.

## Non-negotiable coverage constraint

This eval is additive. It must not replace or shrink the existing 44-case deterministic pack and must not reduce residential, commercial, trade, or permit-type coverage.

Required release gate:

1. Existing full deterministic eval pack remains intact: `len(cases) >= 44` and vertical minimums from `tests/test_eval_vertical_scorecard.py` continue to pass.
2. New commercial customer-quality slice runs as a separate 10–15-case suite, then is summarized alongside the full pack.
3. Any launch decision must consider both:
   - broad regression coverage: all trades/residential+commercial/all permit types from existing pack;
   - customer-quality depth: this 12-case commercial TI slice.

## Eval objective

Measure whether PermitAssist gives a commercial contractor an actually usable answer for restaurant TI, medical/dental clinic TI, and office TI work:

- exact permit/application path, not just a city homepage;
- exact portal/application name/selection where the portal exposes one;
- official source fields for AHJ, portal, contact, fee/timeline/document requirements;
- scope-specific trigger reasoning that matches the job details;
- no generic filler or cross-vertical contamination;
- enough next-step detail for a contractor to apply or quote with confidence.

## Proposed suite size and balance

Use 12 cases: 4 restaurant TI, 4 medical/dental clinic TI, 4 office TI.

Selection principles:

- Reuse existing case shapes/metros where possible so this remains comparable to current eval history.
- Include both Accela-style portals and non-Accela city workflows.
- Include high-risk triggers: change of occupancy, Type I hood, grease interceptor, fire sprinkler/fire alarm, ADA path of travel, x-ray/radiation, medical gas/nitrous, low voltage/access control, HVAC diffuser/lighting controls, and non-invention of plumbing/mechanical when absent.
- Keep source verification locked before any paid run. Candidate golden fields below are design targets from existing repo fixtures/current case definitions and must be source-packed before live scoring.

## Case matrix

### Restaurant TI — 4 cases

1. `cq_phoenix_restaurant_ti_a2_hood_grease`
   - Base existing case: `phoenix_restaurant_ti_deep` / `phoenix_restaurant_ti`.
   - Query: `4200 sqft restaurant conversion from mercantile to A-2 with Type I hood, grease interceptor, fire alarm, patio, and ADA upgrades`.
   - City/state: Phoenix, AZ.
   - Expected scope: `commercial_restaurant`.
   - Candidate portal/application path to lock: City of Phoenix Planning & Development Department; Accela Citizen Access Phoenix; `https://aca-prod.accela.com/PHOENIX`; commercial building tenant improvement / change-of-occupancy application; companion mechanical hood, plumbing grease interceptor, electrical, fire sprinkler/fire alarm paths.
   - Must-hit triggers: B/M to A-2 change of occupancy, Type I hood/NFPA 96, grease interceptor, ADA path of travel/restrooms, fire sprinkler/fire alarm, patio zoning/ROW if applicable.
   - Hard negatives: state contractor-license page as apply URL; residential remodel; generic `building permit only`; no hood/grease/health/fire detail.

2. `cq_los_angeles_restaurant_ti_health_plan_check`
   - Base existing case: `los_angeles_restaurant_ti`.
   - Query: `3500 sqft restaurant tenant improvement in existing retail shell with commercial hood, grease interceptor, health plan check, fire sprinkler modifications, and ADA restroom work`.
   - City/state: Los Angeles, CA.
   - Expected scope: `commercial_restaurant`.
   - Candidate portal/application path to lock: LADBS permit application path / ePlanLA or LADBS apply-for-a-permit path; `https://www.ladbs.org/services/apply-for-a-permit`; commercial tenant improvement/change-of-use building permit; separate mechanical, plumbing, electrical, fire sprinkler/hood suppression coordination; Los Angeles County/Public Health restaurant plan check as supporting source if applicable.
   - Must-hit triggers: commercial TI, A-2/restaurant occupancy if use changes, Type I hood, grease interceptor, health plan check, Title 24/energy/lighting if relevant, ADA path of travel, sprinkler modifications.
   - Hard negatives: CSLB page as apply URL; LA County public works as primary AHJ for City of LA building permit; residential ADU/garage filler.

3. `cq_seattle_restaurant_ti_services_portal`
   - Base existing case: `seattle_restaurant_ti`.
   - Query: `1800 sqft restaurant TI with commercial kitchen, hood suppression, grease interceptor, walk-in cooler electrical, seating layout changes, and possible MUP/change-of-use review`.
   - City/state: Seattle, WA.
   - Expected scope: `commercial_restaurant`.
   - Candidate portal/application path to lock: Seattle Department of Construction & Inspections; Seattle Services Portal / Accela; `https://cosaccela.seattle.gov`; commercial construction/tenant improvement application; separate electrical permit; fire sprinkler/fire alarm/hood suppression paths; King County/Seattle-King County Public Health plan review source if food establishment review applies.
   - Must-hit triggers: commercial TI, food establishment/health review, hood suppression, grease interceptor, electrical for walk-in cooler, SDCI + fire review, MUP/change-of-use only if conditions match.
   - Hard negatives: generic Seattle home repair path; high confidence with third-party-only sources; inventing plumbing if grease scope is omitted is a fail variant.

4. `cq_dallas_restaurant_ti_permitdallas`
   - Base existing stress/case family: Dallas restaurant stress output; current broad pack has Dallas office/control cases.
   - Query: `3200 sqft restaurant tenant finish in Dallas with B to A-2 change of occupancy, Type I hood, grease interceptor, ADA restrooms, sprinkler head relocations, and patio seating`.
   - City/state: Dallas, TX.
   - Expected scope: `commercial_restaurant`.
   - Candidate portal/application path to lock: Dallas Development Services / PermitDallas or DevelopDallas; `https://developdallas.dallascityhall.com/PermitDallas/` or current official replacement; commercial building interior alteration/change-of-occupancy application; mechanical, plumbing, electrical, fire protection applications.
   - Must-hit triggers: B to A-2, Type I hood, grease interceptor, TAS/TDLR accessibility if threshold applies, Dallas contractor registration, fire sprinkler/fire alarm, patio zoning/ROW conditional.
   - Hard negatives: Dallas County public works as primary building AHJ; Lebanon/GovInfo/third-party junk sources; Texas license page as primary apply URL.

### Medical/dental clinic TI — 4 cases

5. `cq_boston_dental_clinic_long_form`
   - Base existing case: `boston_dental_clinic_ti`.
   - Query: `dental clinic tenant improvement with six operatories, nitrous oxide/oxygen lines, sterilization room, compressor/vacuum equipment, plumbing fixtures, accessibility upgrades, and x-ray room`.
   - City/state: Boston, MA.
   - Expected scope: `commercial_medical_clinic_ti`.
   - Candidate portal/application path to lock: Boston Inspectional Services Department; Boston online permitting / official permit portal; commercial long-form alteration/fit-out permit if scope requires plan review; plumbing/mechanical/electrical trade permits; fire alarm if notification devices change; Massachusetts radiation control/x-ray registration/supporting source.
   - Must-hit triggers: dental operatories, nitrous/medical gas or compressed gas, sterilization plumbing, vacuum/compressor equipment, x-ray/radiation shielding/registration, ADA patient route/restroom, fire alarm notification devices.
   - Hard negatives: restaurant hood/grease filler; generic office TI; residential dental office wording; missing x-ray/nitrous support.

6. `cq_cambridge_medical_clinic_xray_medgas`
   - Base existing case: `cambridge_medical_clinic_ti`.
   - Query: `medical clinic tenant improvement with exam rooms, handwashing sinks, accessible reception, x-ray shielding room, medical gas, and fire alarm notification updates`.
   - City/state: Cambridge, MA.
   - Expected scope: `commercial_medical_clinic_ti`.
   - Candidate portal/application path to lock: City of Cambridge Inspectional Services / official online permit portal; commercial building alteration/tenant fit-out application; plumbing/mechanical/electrical/fire alarm permits; state radiation-control source for x-ray; Board/DPH support if clinic licensing is triggered.
   - Must-hit triggers: exam-room sinks/plumbing, x-ray shielding/radiation, med gas, accessible reception/service counter, fire alarm notification, commercial alteration plan review.
   - Hard negatives: Boston as AHJ; generic `building permit`; restaurant/office copied checklist.

7. `cq_san_diego_medical_office_opendsd`
   - Base existing case: `san_diego_medical_office_ti`.
   - Query: `medical office tenant improvement with exam rooms, handwashing sinks, lab area, mechanical ventilation, accessible restroom, x-ray equipment, and fire-life-safety review`.
   - City/state: San Diego, CA.
   - Expected scope: `commercial_medical_clinic_ti`.
   - Candidate portal/application path to lock: City of San Diego Development Services / OpenDSD; commercial tenant improvement/building permit application; electrical/plumbing/mechanical permits; fire permit/review if alarm/sprinkler touched; California radiation/x-ray source as supporting source.
   - Must-hit triggers: clinic exam rooms, hand sinks, lab/mechanical ventilation, x-ray/radiation, accessibility, fire-life-safety.
   - Hard negatives: County of San Diego as primary AHJ for city building permit; CSLB as application path; generic ADA-only answer.

8. `cq_phoenix_urgent_care_medical_clinic`
   - Base existing case: `phoenix_medical_clinic_ti`.
   - Query: `urgent care clinic tenant improvement with exam rooms, x-ray shielding, medical gas, fire alarm notification devices, accessibility upgrades, plumbing and MEP work`.
   - City/state: Phoenix, AZ.
   - Expected scope: `commercial_medical_clinic_ti`.
   - Candidate portal/application path to lock: City of Phoenix PDD; Accela Citizen Access Phoenix; `https://aca-prod.accela.com/PHOENIX`; commercial tenant improvement/building permit; plumbing/mechanical/electrical/fire alarm permits; Arizona radiation regulatory source for x-ray as supporting source.
   - Must-hit triggers: urgent care/clinic occupancy implications, x-ray shielding/radiation registration, medical gas, exam sinks/plumbing, fire alarm notification appliances, ADA path of travel.
   - Hard negatives: restaurant hood/grease terms; no x-ray/medical gas handling; `health department food establishment` filler.

### Office TI — 4 cases

9. `cq_seattle_office_ti_low_voltage_sprinkler`
   - Base existing case: `seattle_office_ti_deep`.
   - Query: `office TI with private offices, conference room, lighting controls, low-voltage/data cabling, access control, sprinkler head adjustments, and accessible route updates`.
   - City/state: Seattle, WA.
   - Expected scope: `commercial_office_ti`.
   - Candidate portal/application path to lock: SDCI Seattle Services Portal; `https://cosaccela.seattle.gov`; commercial construction/tenant improvement permit; separate electrical permit for lighting/power; fire sprinkler modification; low-voltage/access-control permit or electrical/fire/security review when regulated.
   - Must-hit triggers: demising/partitions, lighting controls/energy code, low voltage/access control, sprinkler head relocation, accessibility path of travel, possible fire alarm if egress/security interfaces.
   - Hard negatives: restaurant/medical filler; inventing plumbing when no plumbing scope; residential HVAC misclassification.

10. `cq_los_angeles_office_ti_title24_sprinkler`
    - Base existing case: `los_angeles_office_ti_deep`.
    - Query: `office tenant improvement with nonbearing partitions, lighting controls, ceiling work, low voltage cabling, mechanical distribution changes, sprinkler head relocations, and disabled-access upgrades`.
    - City/state: Los Angeles, CA.
    - Expected scope: `commercial_office_ti`.
    - Candidate portal/application path to lock: LADBS permit application/ePlanLA path; `https://www.ladbs.org/services/apply-for-a-permit`; commercial tenant improvement/interior alteration building permit; electrical, mechanical, fire sprinkler permits; Title 24 energy documentation source.
    - Must-hit triggers: commercial TI plan check, Title 24/lighting controls, accessibility path of travel, fire sprinkler relocation, mechanical distribution, low-voltage boundary.
    - Hard negatives: CSLB as primary apply URL; restaurant hood/grease; ADU/hillside tree filler.

11. `cq_dallas_office_ti_access_control`
    - Base existing case: `dallas_office_ti_deep`.
    - Query: `office tenant finish with demising partition, data room, emergency lighting, HVAC zoning, access control at suite doors, fire alarm devices, sprinkler head relocation, and path-of-travel accessibility`.
    - City/state: Dallas, TX.
    - Expected scope: `commercial_office_ti`.
    - Candidate portal/application path to lock: Dallas Development Services / PermitDallas or current official portal; commercial interior alteration/tenant finish permit; electrical, mechanical, fire alarm/fire sprinkler permits; TAS/TDLR accessibility if construction cost threshold/scope applies.
    - Must-hit triggers: demising partition, data room/electrical, emergency lighting, HVAC zoning, access control and fire egress interfaces, fire alarm/sprinkler, TAS/accessibility.
    - Hard negatives: TDLR page as primary building apply URL; Dallas County as primary AHJ; no fire/egress handling for access control.

12. `cq_houston_office_ti_one_stop_or_online`
    - Base existing case: `houston_office_ti`.
    - Query: `commercial office TI with new partitions, ceiling grid, lighting controls, low-voltage cabling, sprinkler head relocation, HVAC diffuser moves, and accessible reception counter`.
    - City/state: Houston, TX.
    - Expected scope: `commercial_office_ti`.
    - Candidate portal/application path to lock: Houston Permitting Center / City of Houston official online submittal path; commercial building permit / commercial alteration application; electrical/mechanical/fire protection companion permits; TAS/TDLR accessibility if applicable.
    - Must-hit triggers: commercial alteration building permit, lighting controls/energy, low voltage/data, sprinkler relocation, HVAC diffuser moves, accessibility reception counter, no plumbing unless fixtures are added.
    - Hard negatives: Harris County as primary AHJ for City of Houston; TDLR/third-party as application path; restaurant/medical copied triggers.

## Ground-truth case fixture shape

Create a separate fixture file after official-source lock, e.g. `eval/commercial_customer_quality_cases.json`:

```json
{
  "_meta": {
    "suite": "commercial_customer_quality_real_key_slice",
    "version": "design-v0.1",
    "case_count": 12,
    "network_policy": "no live source lookup during eval run; official sources pre-locked"
  },
  "cases": [
    {
      "id": "cq_phoenix_restaurant_ti_a2_hood_grease",
      "category": "commercial",
      "scope": "commercial_restaurant",
      "city": "Phoenix",
      "state": "AZ",
      "job_type": "4200 sqft restaurant conversion from mercantile to A-2 with Type I hood, grease interceptor, fire alarm, patio, and ADA upgrades",
      "golden": {
        "ahj": "City of Phoenix Planning & Development Department",
        "portal_name": "Accela Citizen Access / Phoenix",
        "apply_url_exact_or_host": "https://aca-prod.accela.com/PHOENIX",
        "primary_application_path": "Commercial building permit / tenant improvement / change of occupancy",
        "primary_application_name_patterns": ["commercial", "tenant improvement", "change of occupancy"],
        "required_permit_families": ["building", "mechanical hood", "plumbing grease interceptor", "electrical", "fire sprinkler/fire alarm"],
        "official_sources": {
          "portal": ["phoenix.gov", "aca-prod.accela.com/PHOENIX"],
          "permit_requirements": ["phoenix.gov"],
          "fees_or_plan_review": ["phoenix.gov"],
          "inspections_or_contact": ["phoenix.gov"]
        },
        "supporting_external_sources_allowed": ["roc.az.gov"],
        "blocked_source_domains": ["kauffman.org", "archive.org", "huduser.gov", "permitflow.com", "permitmint.com", "govinfo.gov", "lebanon.in.gov"]
      }
    }
  ]
}
```

Notes:

- `golden.official_sources` must be populated only from source-pack review, not from the engine response being judged.
- `primary_application_path` should be a human-readable exact path if the portal exposes nested selections; if the portal only exposes a general commercial application, record that exactly and do not fabricate deeper selections.
- The evaluator should accept official portal host migrations only if the source pack explicitly records the migration.

## Scoring rubric

Score each case 0–100. Use deterministic checks where possible plus an optional human/LLM customer-quality judge only after budget approval.

### A. Jurisdiction, portal, and application path — 20 pts

- 6 pts: correct AHJ/applying office for city limits, not county/state/third-party unless explicitly the AHJ.
- 5 pts: exact official portal URL or official application page, not a generic city homepage when a permit portal/application page is available.
- 5 pts: exact portal/application name or selection path for the primary permit.
- 4 pts: correct companion application paths/names for trade/fire/health/medical support permits.

### B. Permit taxonomy and scope classification — 18 pts

- 5 pts: `_primary_scope` matches expected commercial vertical.
- 5 pts: primary permit name is commercial TI/interior alteration/change-of-occupancy where applicable.
- 4 pts: required companion permit families are present without undercounting.
- 4 pts: no invented permits where scope does not support them, especially plumbing for office TI when no fixtures are touched.

### C. Official source grounding and field provenance — 17 pts

- 5 pts: `sources` include official city/AHJ portal and permit-requirement sources.
- 4 pts: `_field_sources` or equivalent map critical fields to official sources: `apply_url`, `applying_office`, `apply_phone`, `fee_range`, `approval_timeline`, `permits_required`.
- 3 pts: fee/timeline uncertainty is calibrated when official fee math is not exact.
- 3 pts: source list excludes blocked junk/irrelevant third-party domains.
- 2 pts: confidence is not `high` unless official source coverage is complete.

### D. Vertical-specific triggers and reasoning — 20 pts

- Restaurant TI: Type I hood/NFPA 96, grease interceptor, food/health review, fire suppression/sprinkler/fire alarm, occupancy/use change, ADA path, patio/ROW conditional.
- Medical/dental clinic TI: exam sinks/plumbing, x-ray/radiation shielding/registration, medical gas/nitrous, sterilization/lab ventilation, fire alarm notification, ADA route/reception/restroom, clinic licensing conditional.
- Office TI: demising/nonbearing partitions, lighting controls/energy code, low voltage/access control, sprinkler/fire alarm relocation, HVAC diffuser/zoning, ADA path/reception, no restaurant/medical contamination.

Award:

- 12 pts for required trigger coverage.
- 4 pts for conditionals labelled correctly (`required`, `conditional`, `possible`) with `required_if` text.
- 4 pts for trigger-to-permit mapping in `permits_required_logic` or equivalent.

### E. Contractor usefulness — 15 pts

- 4 pts: actionable `what_to_bring` tailored to the city/vertical.
- 3 pts: inspection booking details are specific enough to schedule.
- 3 pts: licensing/registration requirements are city/state-specific.
- 3 pts: common mistakes/pro tips are non-generic and likely to prevent delays.
- 2 pts: fee/timeline notes are useful without overclaiming precision.

### F. No filler, no contamination, and output hygiene — 10 pts

- 4 pts: no residential/ADU/roof/HVAC-only filler in commercial TI output.
- 2 pts: no restaurant terms in medical/office outputs; no medical terms in restaurant/office outputs unless scope supports them.
- 2 pts: no placeholder fields, malformed URLs, empty `portal_selection`, or duplicate boilerplate.
- 2 pts: concise enough to be usable; long code-citation dumps do not bury application steps.

## Hard failure caps

Apply caps after raw scoring:

- Wrong city/AHJ or state/license page as primary apply URL: max 50.
- No exact official apply path/portal URL: max 65.
- Missing primary commercial TI/interior alteration/change-of-occupancy permit for TI scope: max 60.
- `_primary_scope` residential or wrong commercial vertical: max 60.
- No official sources but confidence is high: max 70.
- Restaurant case missing both hood and grease-interceptor handling: max 65.
- Medical/dental case missing x-ray/radiation when x-ray is in prompt: max 70.
- Medical/dental case missing medical gas/nitrous when in prompt: max 75.
- Office case invents Type I hood/grease/health food review: max 70.
- Blocked junk source used for a critical field: max 75; max 60 if it drives `apply_url` or fee.
- Generic filler answer that a contractor cannot use to apply: max 70 even if structural fields exist.

## Pass/fail thresholds

Commercial customer-quality suite passes only if all are true:

- Overall mean score >= 90.
- Each vertical mean >= 90.
- No case < 85.
- Zero hard failures for wrong AHJ, wrong apply URL, wrong vertical, or blocked critical source.
- At least 10/12 cases have exact official portal/application path and primary application name accepted by the source pack.
- All 12 cases include official source provenance for `apply_url`, `applying_office`, `permits_required`, and at least one of `fee_range` or `approval_timeline`.
- Existing broad coverage gate still passes: current 44-case deterministic eval/test coverage remains intact.

Triage levels:

- Green: mean >= 92, all cases >= 88, zero hard caps.
- Yellow: mean 90–91.9 or one case 85–87.9, no hard caps; requires manual review before launch.
- Red: any case < 85, any hard cap, any wrong-AHJ/wrong-portal; block launch.

## Safe command shape

Do not use current `scripts/run_eval.py` defaults for this suite because it defaults to production and can load an admin token fallback.

### Design/static validation only — safe now

Proposed future command for fixture validation without network or keys:

```bash
cd /home/boban/projects/permitassist-live-e2e-blockers-20260525T221141Z
unset PERMITASSIST_ADMIN_TOKEN OPENAI_API_KEY ANTHROPIC_API_KEY TAVILY_API_KEY SERPER_API_KEY
python3 scripts/run_commercial_customer_quality_eval.py \
  --cases eval/commercial_customer_quality_cases.json \
  --validate-only \
  --no-network \
  --no-write
```

### Local/staging response grading — after approval only

```bash
cd /home/boban/projects/permitassist-live-e2e-blockers-20260525T221141Z
export PERMITASSIST_EVAL_APPROVED=1
export PERMITASSIST_ENV=staging
export PERMITASSIST_DISABLE_LIVE_SEARCH=1
export PERMITASSIST_MAX_CASES=12
export PERMITASSIST_MAX_LLM_CALLS=12
export PERMITASSIST_MAX_SEARCH_CALLS=0
export PERMITASSIST_BUDGET_USD=3.00
python3 scripts/run_commercial_customer_quality_eval.py \
  --cases eval/commercial_customer_quality_cases.json \
  --base-url "$PERMITASSIST_STAGING_URL" \
  --no-prod \
  --budget-usd 3.00 \
  --max-cases 12 \
  --max-judge-calls 12 \
  --rate-limit-rps 0.2 \
  --output-dir artifacts/evals/commercial-customer-quality/$(date -u +%Y%m%dT%H%M%SZ)
```

Required harness protections:

- Refuse `https://permitassist.io`, `permitassist.io`, or any production hostname unless a separate `--allow-prod` flag and written approval token are supplied.
- Refuse to auto-load admin tokens from private fallback files; keys must be passed explicitly for approved staging runs.
- Refuse to run if any source lookup/web-search provider is enabled; eval should grade responses against pre-locked official source packs.
- Print estimated max cost before first paid call and require `PERMITASSIST_EVAL_APPROVED=1`.
- Stop at first budget breach, hard-fail portal mismatch rate > 0, or 2 consecutive infra failures.

## Key/cost controls

- Use a dedicated eval-only key/project with a hard platform cap; never use production tenant/admin keys.
- One engine call per case; max 12–15 calls.
- Optional judge: one LLM judge call per case, low max tokens, temperature 0, pinned model. No web-enabled judge.
- No automatic retries except one retry for transient 408/429/500/502/503/504; retries count against budget.
- No cache busting unless explicitly approved; if cache busting is required, append a deterministic suite run id and cap at one pass.
- Do not run source discovery during scoring. Source discovery happens once in a separate manual/source-pack phase, with no production API calls.
- Persist full artifacts locally only; redact keys, auth headers, cookies, admin tokens, and user identifiers.

## Approval gates

1. Source-pack gate:
   - For every case, an official source pack exists with portal URL, application name/path, AHJ contact, fee/timeline source, and required documents/source notes.
   - Source pack reviewer signs off that candidate golden values are official and current.

2. Static fixture gate:
   - Fixture validates JSON schema.
   - Exactly 10–15 cases.
   - Balanced target verticals or explicit written rationale.
   - No blocked domains in golden official sources.

3. Harness safety gate:
   - `--validate-only --no-network --no-write` works.
   - Production URL denylist test passes.
   - Key fallback disabled for this suite.
   - Budget/call caps unit-tested.

4. Broad coverage gate:
   - Existing tests including `tests/test_eval_vertical_scorecard.py` pass.
   - Existing `eval/permit_eval_cases.json` coverage is not reduced.

5. Staging smoke gate:
   - Run only 2 cases first: one restaurant, one office or medical.
   - No production hostname.
   - No live search calls.
   - Cost within budget preflight.

6. Full staging gate:
   - Run 12 cases once.
   - Summarize artifacts with infra failures separated from quality failures.
   - Manual review any yellow case before accepting.

7. Launch decision gate:
   - Customer-quality pass thresholds met.
   - Broad regression pack still green.
   - Any source-pack caveats documented and reflected in confidence/uncertainty.

## Recommended implementation tasks, still design-only

- Add `eval/commercial_customer_quality_cases.json` after source-pack lock.
- Add `scripts/run_commercial_customer_quality_eval.py` or extend existing `run_eval.py` behind safe flags; do not use prod defaults.
- Add schema tests:
  - case count 10–15;
  - exactly target vertical coverage;
  - official source fields present;
  - production denylist;
  - no key fallback;
  - hard caps applied correctly.
- Keep current `scripts/run_eval.py` and `eval/permit_eval_cases.json` broad pack untouched except for separate improvements.
