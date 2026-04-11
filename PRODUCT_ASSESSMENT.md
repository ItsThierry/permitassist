# PermitAssist — Honest Product Assessment
_Written: 2026-04-11 06:05 GMT+1 | For Boban's morning review_

---

## The Honest Verdict

PermitAssist has real bones — the concept is validated, the market gap is real, and the core lookup engine works. But the **product experience tonight exposed several gaps** that need fixing before it's something contractors will pay $19/mo for.

Here's everything, no sugar-coating.

---

## 🔴 Critical Issues (fix before any marketing)

### 1. Small city / rural area problem
**What happened:** Boban searched from Alsip, IL (a small Chicago suburb). No portal URL, no phone number returned. Button disappeared. Call button showed a vague message.

**Root cause:** Our KB has 150 cities — all are major metros. The US has 19,000+ incorporated municipalities. Contractors work in small towns constantly.

**Fix:** Three-layer approach:
- Layer 1: KB hit (150 cities — instant, exact)
- Layer 2: Tavily live search for that specific city (costs ~$0.002, already doing this)
- Layer 3: **Fall back to county/state level** — "We don't have exact data for Alsip, IL — here's Cook County's permit office, which covers most Chicago suburbs"
- Always return something useful. Never return nothing.

**Effort:** 2-3 hours. High priority.

---

### 2. Vague job descriptions
**What happened:** "Roof leak" → returns Building Permit advice. But a minor roof repair (patching 3 shingles) doesn't need a permit in most jurisdictions. A full replacement does.

**Root cause:** GPT doesn't ask for clarification. It makes assumptions.

**Fix:** Add job type disambiguation in the frontend. When the job description is ambiguous (contains "leak", "repair", "fix", "minor"), show a quick clarification:
- "Is this a minor repair (patching, less than 25% of roof)?" → likely no permit
- "Or a full/partial replacement?" → permit required

**Effort:** 2 hours. Medium priority.

---

### 3. PDF-only cities
**What happened:** Some cities still use paper permit applications. Clicking "Apply" opened a PDF which confused Boban.

**Status:** Fixed tonight — now shows "Paper Application Required" with download + address.

**Remaining issue:** GPT still sometimes returns PDF URLs as apply_url despite the instruction. Need to add server-side PDF URL detection in research_engine.py — strip PDF links from apply_url and put them in apply_pdf automatically.

**Effort:** 30 min. Do now.

---

### 4. Phone numbers missing for small cities
**What happened:** Alsip building dept phone not returned.

**Fix:** 
- Add county-level fallback phone numbers to KB for top 50 counties
- In Tavily search query, explicitly include "phone number" in the search
- If all else fails, return Google Maps link: `https://maps.google.com/search?q=Alsip+IL+building+permit+office`

**Effort:** 1 hour.

---

### 5. UI — not polished enough
**What happened:** Boban asked for a redesign (HireForge style). Didn't happen yet.

**Current UI problems:**
- Orange brand color feels generic, not professional
- Cards are fine but lack visual hierarchy
- "Look Up Permits" button needs to be more prominent
- Results page is information-dense but not visually clear
- Mobile experience needs refinement

**Effort:** 3-4 hours for a proper redesign.

---

## 🟡 Medium Issues (fix in week 1 post-launch)

### 6. No user accounts / subscription system
The $19/mo paywall points to `#upgrade` — there's no actual payment flow yet. Stripe links need to be created and wired.

**What's needed:**
- Create Stripe payment links ($19/mo Solo, $49/mo Team)
- After payment, give user a code/link that unlocks unlimited lookups
- Simplest version: email them a "magic unlock" that sets a localStorage flag
- Real version: Stripe webhook → generate unlock token → email it

**Effort:** 4-6 hours for basic version.

---

### 7. portal_selection field reliability
GPT should return "HVAC Replacement — Residential" as the portal selection. It does this about 70% of the time based on tonight's tests. The other 30% it leaves it blank or returns something generic.

**Fix:** Better few-shot examples in the system prompt. Show GPT exactly what good portal_selection looks like for each trade.

**Effort:** 1 hour.

---

### 8. Cache is empty (we cleared it)
We cleared 30 cached results tonight to force the improved prompt. Good call — but it means the first 30 unique lookups will all hit OpenAI. At $0.015 each that's $0.45 — negligible. But worth noting.

---

### 9. "HVAC repair" vs "HVAC replacement" 
Many contractors ask about repairs, not replacements. Repair of an existing system (no refrigerant work, no new circuits) is often exempt from permits. The tool should handle this distinction clearly.

---

### 10. No "not required" result handling
When a job doesn't need a permit, the UI should make that crystal clear with a big ✅ "No Permit Required" message — not just a subtle "required: false" buried in the result.

---

## 🟢 Database Expansion — Is It Worth It?

### Current state: 150 cities, all 51 states

**The honest answer: more cities help SEO more than they help the product.**

Here's why:

The product already works for unlisted cities — Tavily searches live building department data. A contractor in Boise, Idaho gets a good result even though Boise isn't in our KB. The KB just makes it faster and more accurate.

**What actually matters more than more cities:**

1. **Deeper data on existing cities** — fees, timelines, exact portal sub-types, phone numbers, office hours. This is what contractors actually need.

2. **County-level data** — more contractors work in unincorporated areas than in city limits. Harris County TX (no permit required for HVAC) is more valuable than adding the 5th city in Nebraska.

3. **State-level licensing rules** — who can pull which permit varies by state. This is currently in states.json but not surfaced clearly enough.

**Recommendation:** Don't add more cities yet. Deepen the 150 we have. Add county data for the top 20 counties by contractor population.

---

## The Real Gap vs Competitors

| Feature | PermitAssist | PermitFlow | City website |
|---|---|---|---|
| Small city support | ⚠️ Partial | ✅ Yes | ✅ Yes |
| Instant results | ✅ Yes | ❌ Sales process | ❌ Manual |
| Price | $19/mo | $500+/mo | Free |
| Mobile friendly | ✅ Yes | ⚠️ Desktop | ❌ Usually no |
| All trades | ✅ Yes | ✅ Yes | ✅ Yes |
| Exact fees | ✅ 150 cities | ✅ Yes | ✅ Yes |
| Online portal link | ✅ 39 cities | ✅ Yes | N/A |
| Phone number | ⚠️ Inconsistent | ✅ Yes | ✅ Yes |

**Our gap vs a city website:** A contractor can just Google "[city] building permit" and get the same info. Our value is: 1) it's instant and formatted, 2) it works across all trades in one place, 3) we tell them the exact portal selection (what PermitFlow doesn't even do).

---

## Priority Fix List for Tomorrow

| Priority | Fix | Effort |
|---|---|---|
| 🔴 1 | Small city fallback to county/state level | 3h |
| 🔴 2 | UI redesign (HireForge style) | 4h |
| 🔴 3 | Stripe payment links + basic unlock flow | 4h |
| 🔴 4 | Server-side PDF URL stripping | 30min |
| 🟡 5 | Job type disambiguation (repair vs replace) | 2h |
| 🟡 6 | Better phone number retrieval | 1h |
| 🟡 7 | "No permit required" clear UI state | 1h |
| 🟡 8 | portal_selection few-shot examples | 1h |
| 🟢 9 | County-level KB data (top 20 counties) | 4h |
| 🟢 10 | Google Maps fallback for office location | 30min |

**Total to ship-worthy:** ~12-15 hours of focused work.

---

## Bottom Line

The engine works. The data is solid. The UX needs polish and the edge cases (small cities, vague jobs, PDF-only portals) need handling.

This is 2-3 focused days away from being something contractors would genuinely pay for. Not weeks. Not months. Days.

The cold email campaign (Apr 16) should still go out — but it should drive people to a **waiting list or free tool**, not straight to a paywall. Get email captures, fix the product, then convert.

---
_Thierry — written at 06:05 GMT+1 while Boban sleeps_
