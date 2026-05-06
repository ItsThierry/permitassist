# A4 verification — ESS/solar/battery residue purge

## Files changed
- `api/research_engine.py`
  - Added `purge_solar_ess_residue()` post-assembly / pre-render-cache guard.
  - Scope detection honors `_primary_scope`, `job_type`, and `hidden_triggers[].id`.
  - Leaves commercial scopes untouched.
  - Leaves solar/PV/battery/ESS residential jobs untouched.
- `tests/test_a4_solar_ess_residue.py`
  - 12 regression tests covering 6 non-solar residential scenarios, 3 positive solar/battery cases, and 2 commercial untouched cases.
- `eval/phase1-verify/A4-phoenix-water-heater-after.json`
- `eval/phase1-verify/A4-vegas-reroof-after.json`

## Test results
- `./.venv-test/bin/pytest tests/test_a4_solar_ess_residue.py -q` → 12 passed
- `./.venv-test/bin/pytest -q --ignore=tests/test_tier_b_engine_accuracy.py` → 75 passed
- `./.venv-test/bin/pytest tests/test_tier_b_engine_accuracy.py -q` → 8 passed

Only warning: deprecated `google.generativeai` package import.

## Golden residue check
Command:

```bash
grep -RIn "ESS\|Energy Storage\|solar\|Solar\|battery\|Battery\|PV\|NFPA 855\|NEC 706\|IRC 324.10" \
  eval/phase1-verify/A4-phoenix-water-heater-after.json \
  eval/phase1-verify/A4-vegas-reroof-after.json
```

Result: no matches.

## INCOMPLETE flags
None.
