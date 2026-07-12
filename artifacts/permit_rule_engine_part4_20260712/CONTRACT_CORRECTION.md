# Part 4 contract correction

The initial frozen Part 4 red contract required the report-embedded JSON to equal the complete sealed customer projection. During implementation, that was found to conflict with the pre-existing and separately tested public-report default-deny boundary, which intentionally excludes `claim_citations` from the embedded `<script type="application/json">` payload.

The corrected contract preserves both requirements:

- the hash-sealed shared-result storage DTO must equal the exact sealed customer projection;
- report-embedded JSON must preserve all decision-bearing fields, ten-lane family decisions, routes, and verification tasks;
- `claim_citations` remains excluded from report-embedded JSON under the existing public boundary contract;
- the white-label report may render customer-safe source footnotes directly without placing internal citation objects in report-data JSON.

Git history preserves the original red contract and this explicit correction. No production activation setting was changed.
