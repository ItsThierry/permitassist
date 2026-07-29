# Canonical Permit-Family Ontology — Session 1 Specification

## Status

**Reviewed specification only.** Session 1 does not import this ontology into `api/`, alter runtime serialization, or change customer behavior. The machine-readable authority is `permit_family_ontology_v1.json`.

## Purpose

Create one closed semantic vocabulary for labels seen in:

- v1.1 benchmark truth;
- all 500 preserved v1.1 raw envelopes;
- v2.4 Decision Cell `permit_kind` values;
- current typed runtime rows (`family`, `filing_family`, `display_family`, `permit_kind`, `permit_type`, and `approval_type`).

The ontology must never turn an unknown label into `BUILDING` merely to preserve a binary answer.

## Canonical families

- `NO_PRIMARY_PERMIT`
- `BUILDING`
- `ROOFING`
- `ELECTRICAL`
- `PLUMBING`
- `MECHANICAL`
- `REFRIGERATION`
- `GAS`
- `FIRE_LIFE_SAFETY`
- `ZONING_PLANNING`
- `OCCUPANCY_CO`
- `DEMOLITION`
- `SIGN`
- `POOL_SPA`
- `MOVING`
- `LANDMARKS_HISTORIC`
- `HEALTH`
- `GRADING_SITE_CIVIL_ROW`
- `WASTEWATER_FOG`
- `ENVIRONMENTAL`
- `LIQUOR`
- `MANUFACTURED_STRUCTURE`
- `TRADE_OR_SUBPERMIT_REVIEW`
- `ACCESSIBILITY`
- `UTILITY`
- `OTHER`
- `VERIFY`

## Canonical statuses

- `REQUIRED`
- `CONDITIONAL`
- `NOT_REQUIRED`
- `NEEDS_INPUT`
- `VERIFY`

`LIKELY`, `RELATED`, or generic heuristic prose is not a regulated verdict. An adapter may conservatively project a trigger-bearing `LIKELY` row to `CONDITIONAL` for diagnosis, but Session 2 must require provenance before any factual REQUIRED/CONDITIONAL customer claim.

## Coverage states

- `EXACT_COMPLETE`
- `EXACT_PARTIAL`
- `AHJ_COVERED_SCOPE_UNSUPPORTED`
- `JURISDICTION_AMBIGUOUS`
- `AHJ_UNCOVERED`
- `FAIL_CLOSED`

Coverage state is orthogonal to decision and family. A known AHJ does not imply a supported scope.

## Adapter contracts

### Truth adapter

Read only:

1. `expected.primary_permit_family`
2. `expected.companions[].family`

Display strings are not truth.

### Standalone model adapter

Read only the frozen model schema fields:

1. `primary_permit_family`
2. `companion_permits[].family`
3. `companion_permits[].status`

No scorer repair may invent absent values.

### Runtime primary adapter

Use this precedence:

1. `primary_permit_family`
2. `permits_required[0].family`
3. `permits_required[0].filing_family`
4. `permits_required[0].permit_kind`
5. top-level `permit_kind`
6. top-level `permit_name`

This repairs measurement only: the typed internal row is measured before a display label. It does not rewrite the preserved raw envelope.

### Runtime companion adapter

Collect and deduplicate:

1. `permits_required[1:]`
2. `related_permits`
3. `companion_permits`

Status precedence for duplicate families is:

`REQUIRED > CONDITIONAL > NEEDS_INPUT > VERIFY > NOT_REQUIRED`

No family is deleted to improve a score.

### Cell adapter

Map each v2.4 `permit_kind` through exact aliases first, then ordered display rules. Important mappings include:

- `building_construction`, `building_trade` → `BUILDING`
- `zoning`, `zoning_planning` → `ZONING_PLANNING`
- `septic_oss_health` → `HEALTH`
- `manufactured_structure_installation` → `MANUFACTURED_STRUCTURE`
- `trade_or_subpermit_review` → `TRADE_OR_SUBPERMIT_REVIEW`

## Safety distinctions that cannot collapse

- `ROOFING` remains distinct from `BUILDING` when a typed/source label says roofing.
- `OCCUPANCY_CO` is not zoning.
- `FIRE_LIFE_SAFETY` is not building.
- `NO_PRIMARY_PERMIT` is first-class and valid as primary only with `NOT_REQUIRED`.
- `LANDMARKS_HISTORIC` is distinct from general zoning/planning.
- Unknown or ambiguous labels map to `VERIFY`, never to `BUILDING`.

## Enum-closure result

`ontology_enum_closure.json` inventories and maps:

- 15 unique relevant truth labels;
- 84 unique relevant preserved raw/runtime/model labels;
- 18 unique v2.4 Cell `permit_kind` labels.

Closure verdict: **PASS**. The deterministic test re-walks the sources and rejects any canonical value outside the enum.

## Session 2 adoption requirements

If Boban later approves Session 2 implementation:

1. Adopt one checked-in runtime enum rather than copying this mapping into multiple modules.
2. Preserve current authenticated Decision Cell authority and decisions.
3. Serialize one typed primary family and typed companion family/status/source shape through the sealed projection.
4. Demote unsourced heuristic companions to visible `VERIFY`; do not delete source-backed rows.
5. Feature-flag the customer-visible change and prove flag-off byte identity.
6. Fail CI when any newly observed truth/cell/runtime label lacks deterministic closure.

## Session 1 boundary proof

This specification and its benchmark adapter live only under `benchmarks/permit_accuracy_v1_2/`. All opening protected runtime/product file SHA-256 values are unchanged.
