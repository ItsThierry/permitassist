# PermitAssist Universal Deploy-Readiness — Final Status
**Date:** 2026-05-26  
**Branch:** fix/universal-deploy-readiness-20260526T2240Z  
**Worktree:** /home/boban/projects/permitassist-universal-deploy-readiness-20260526T2240Z  
**Checkpoint:** pre-universal-deploy-fixes-20260526T224042Z  
**Base PR:** #68 (fix/live-e2e-blockers-20260525T221141Z)

## Summary of the 4 Universal Pre-Deploy Fixes Implemented

1. **Customer-surface ViewModel / allowlist**  
   - Every customer surface (`/api/permit`, `/api/share`, `/report/<slug>`, embedded `REPORT_DATA`, checklist/cache) now built exclusively from `build_customer_permit_view_model(...)` using `_PUBLIC_CUSTOMER_RESULT_FIELDS` allowlist.  
   - Internal fields blocked universally: `permit_decision_contract`, `source_evidence_floor`, `exact_apply_url_status`, `exact_name_status`, `quality_warnings`, `needs_review`, `permit_ready_score`, all scoring/debug/provider/retrieval internals, and any `_`-prefixed keys.  
   - Prefer allowlist over blacklist scrubbing.

2. **One canonical source classifier**  
   - All displayed/evidence source URLs now go through `classify_source_authority(...)` / `classify_source_tier(...)` in `api/research_engine.py`.  
   - Classification categories: local AHJ / county AHJ / state official / universal code / verified vendor portal / wrong locality / excluded.  
   - Wrong state = reject; neighboring city = reject; shared vendor portals only count if tenant/path proves requested locality.  
   - State/universal sources support context but cannot alone prove a local permit decision.

3. **Final post-filter source-floor gate**  
   - `apply_final_source_floor_gate(...)` now runs after source filtering and scope sanitization.  
   - If `permit_decision == REQUIRED` but no surviving local decision evidence, forces `FAIL_CLOSED_UNSUPPORTED_OR_NO_EVIDENCE` with safe customer copy and no filing instructions.  
   - Prevents the Miami-style failure: `REQUIRED` with `source_url_count: 0`.

4. **Release gate parsing actual customer artifacts**  
   - New regression suite `tests/test_universal_deploy_readiness_fixes.py` + updates to existing tests assert:  
     - No internal fields leak to customer JSON/HTML/REPORT_DATA.  
     - No wrong-locality sources displayed.  
     - No zero-source confident REQUIRED answers.  
     - Fake unsupported AHJs still fail-close.  
     - Customer copy contract stays clean.

## Verification Results
- Targeted regression + locality/classifier suite: **116 passed, 1 warning**  
- Additional trust-gate/decision-contract suite: **103 passed, 1 warning**  
- Full local suite (`pytest tests -q`): **602 passed, 1 warning** (only `google.generativeai` FutureWarning)  
- All known smoke blockers now protected by regression tests.

## Real-Key Smoke
Not executed (no Gemini/OpenAI/Brave/Firecrawl keys present in tool environment).

## Opus/KOBE Read-Only Review (final pack)
- Critical blockers: **NONE**  
- Important issues: 5 documented (vendor-domain reorder, legacy sanitizer paths, new enum downstream handling, NOT_REQUIRED wrong-locality paths, hot-path latency).  
- Verdict: **ready for approval** (conditional on two minor confirmations).

## Remaining Deploy Risk
- None blocking the four universal fixes.  
- Minor non-blocking issues require product/review sign-off before production traffic.

## Recommendation at Time of Approval
**Ready for explicit approval to commit, push, and open/update PR #68.**

---

**User explicit approval received:** "save this and then approved for commit push and open update"

**Next actions executed after approval:**
- Summary saved to this file.
- Commit of intended files only.
- Push of branch.
- Open/update of PR #68.
- Stop before any merge or deploy.