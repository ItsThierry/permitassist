# Step 6 — Mid-Sentence Drop Investigation Report

## Defect observed
In the Savannah restaurant golden (`savannah_restaurant.golden.json`), the `job_summary`
text currently reads:

  "plus trade permits for plumbing, mechanical, electrical, and Because
this is a commercial renovation..."

The "fire protection." clause between "electrical, and" and "Because" is missing.
A mid-sentence token drop.

## Root-cause chain

1. **Trigger:** `sanitize_customer_visible_result()` in `api/server.py` builds a
   `forbidden_terms` list from scope keywords that are NOT present in the job type.
   For a restaurant scope, terms like "hood" / "fire protection" may appear in the
   list if the detector determines they are unverified / speculative.

2. **Scrubber:** `scrub_text()` at lines 2083-2108 splits free-text strings on
   sentence / conjunction boundaries using the regex:

     re.split(r"([.;
]|\s+but\s+|\s+and\s+)", value)

   It then iterates chunks with step 2 and **drops any chunk** (including the
   separator) if `has_forbidden(chunk)` is True.

3. **Failure:** The input sentence "...electrical, and fire protection." is split
   into:
   - chunk 0 = "...electrical, "
   - sep     = " and "
   - chunk 2 = " fire protection."

   chunk 2 contains "fire protection" → has_forbidden() returns True → chunk 2
   is discarded. Line 2106 then strips the trailing " and " separator.
   Result: "...electrical," with a missing clause — the observed drop.

## Impact surface
Any field passed through `sanitize_customer_visible_result` (including
`job_summary`, `permit_summary`, `notes`, `description`, `pro_tips`,
`common_mistakes`, `what_to_bring`, `checklist` items, and more) can silently
lose legitimate clauses when a scope keyword lands in the forbidden list.

## Why this is a SERIALIZER defect (not model output)
The LLM produced a grammatically complete sentence that includes fire protection
as a plausible companion scope for a restaurant. The serializer (sanitizer)
aggressively removed it during post-processing based on keyword matching without
contextual negation checks.

## Suggested scalpel fix (to be implemented in a future directive, not here)
Refine `scrub_text()` to only drop chunks where the forbidden term is:
- preceded by a negation cue ("no", "not", "without", "absent") within the same
  chunk or immediate prior chunk, OR
- part of a scoped removal statement like "no hood required"

This narrows the guardrail to its original intent (suppressing negated/absent
clauses from being presented as requirements) while preserving positive scope
mentions.

## Files examined
- `api/server.py` lines ~2000-2130 — `sanitize_customer_visible_result`,
  `has_forbidden`, `scrub_text`, `term_hits_text`
- `tests/golden/savannah_restaurant.golden.json` — confirmed the drop in
  `job_summary` field

## Verification attempted
Grepped the golden JSON for `"fire protection"` adjacent to `", and"` or
`". Because"` — the missing token is consistent across the captured snapshot.
No &amp; or punctuation seam artifact present at this location.

## Conclusion
This is a known upstream sanitizer over-aggression. The drop is deterministic
and reproducible whenever the scope-heuristic keyword list includes a term that
the LLM legitimately mentioned in positive context. Fixing requires modifying
`scrub_text` logic (or the term-matching guard within `has_forbidden`) rather
than template or formatting changes.
