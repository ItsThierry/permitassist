# PermitAssist SEO Playbook
_Generated: 2026-04-11_

## What We Built

**411 SEO pages** generated from the verified knowledge base:
- **351 City × Trade pages** — `/permits/{trade}/{city-state}` (e.g. `/permits/hvac/houston-tx`)
- **51 State hub pages** — `/permits/state/{state-name}` (e.g. `/permits/state/texas`)  
- **9 Trade guide pages** — `/permits/guide/{trade}` (e.g. `/permits/guide/hvac`)
- **1 Index page** — `/permits/` (links hub, internal linking backbone)
- **sitemap.xml** — 413 URLs, submitted to Google Search Console
- **robots.txt** — allows all crawlers, points to sitemap

Every page has:
- ✅ Exact title tags with keyword + city/state + year
- ✅ Meta descriptions (155 chars max, includes keyword)
- ✅ Canonical URLs
- ✅ Open Graph tags (Facebook/LinkedIn sharing)
- ✅ Twitter Card tags
- ✅ FAQ schema (JSON-LD) on city+trade pages → Google rich results
- ✅ HowTo schema on trade guide pages
- ✅ Breadcrumb navigation (structured)
- ✅ Internal linking (city → other trades, city → state, trade → all cities)
- ✅ Mobile-responsive layout
- ✅ Font preconnect (performance)
- ✅ Verified fee data from official sources
- ✅ CTA → free tool on every page

---

## Keyword Strategy

### Primary targets (high intent, low competition)

| Keyword Pattern | Example | Why it ranks |
|---|---|---|
| `[trade] permit [city] [state]` | "HVAC permit Houston TX" | No competitor has exact fee data |
| `do I need a permit for [trade] in [city]` | "do I need a permit for HVAC in Phoenix" | FAQ schema catches this |
| `[trade] permit cost [city]` | "roofing permit cost Chicago" | We have exact city fees |
| `[trade] permit requirements [state]` | "electrical permit requirements Texas" | State page targets this |
| `how much is a [trade] permit in [city]` | "how much is a plumbing permit in Atlanta" | FAQ schema |

### Why we win vs PermitFlow
- PermitFlow's city pages: generic building permit info, no specific trade fees
- Our pages: exact verified fees per trade per city ($68 first system, $91 minimum, etc.)
- Google rewards specificity and freshness — we have both

---

## Launch Steps (do these on deployment day)

### 1. Google Search Console
1. Go to https://search.google.com/search-console
2. Add property: `https://permitassist.io`
3. Verify via DNS TXT record (add to Namecheap DNS panel):
   - Type: TXT | Host: @ | Value: `google-site-verification=XXXX`
4. Submit sitemap: Sitemaps → Add → `https://permitassist.io/sitemap.xml`
5. Done. Google will crawl within 24-72 hours.

### 2. Google Business Profile (optional but valuable)
- Not applicable yet (no physical location)
- Create when we have a real business address

### 3. Bing Webmaster Tools
- https://www.bing.com/webmasters
- Add site, verify via DNS
- Submit sitemap (Bing has 12% of searches — free traffic)

### 4. Index Request for Key Pages
After submitting sitemap, manually request indexing for highest-value pages:
- https://permitassist.io/permits/hvac/houston-tx
- https://permitassist.io/permits/hvac/phoenix-az
- https://permitassist.io/permits/hvac/chicago-il
- https://permitassist.io/permits/guide/hvac
- https://permitassist.io/permits/state/texas
In Search Console: URL Inspection → Enter URL → Request Indexing

---

## Content Expansion Plan (Month 2+)

### Phase 2: Blog Content (long-tail informational)
These target earlier-funnel searchers — builds authority fast.

**High-value blog posts to write:**
1. "Do You Need a Permit to Replace HVAC in Texas?" (targets 50+ cities at once)
2. "HVAC Permit Checklist: What Contractors Need Before Starting"
3. "What Happens If You Do Electrical Work Without a Permit?"
4. "How Long Does a Roofing Permit Take in Florida?"
5. "King County WA Permit Fees 49% Increase — What Contractors Need to Know"
6. "No Permit Required? The Harris County TX HVAC Rule Explained"
7. "How to Pull Your Own HVAC Permit as a Homeowner (State by State)"
8. "HVAC Permit Exemptions: When You Don't Need One"
9. "Permit Denied? Common Reasons and How to Fix Them"
10. "Mini Split vs Central HVAC: Do Permit Requirements Differ?"

### Phase 3: More Cities (expand KB)
Add 30 more cities → 270 more city×trade pages. Target:
- Sacramento, San Bernardino, Riverside (CA contractors)
- Tampa (FL — huge HVAC market)
- Charlotte (NC — fast-growing)
- Salt Lake City (UT)
- Boise (ID — fast-growing)
- Cleveland, Akron (OH — more coverage)
- Detroit, Grand Rapids (MI)

### Phase 4: County Pages
County-level pages for unincorporated areas (big for TX, FL, AZ contractors):
- Harris County TX (Katy, Sugar Land, Pearland — no permit for HVAC!)
- Maricopa County AZ
- Broward County FL
- Cook County IL

---

## Backlink Strategy

**Quick wins:**
1. Reddit posts in r/HVAC, r/electricians, r/Roofing — link to relevant guide pages
2. Answer Quora questions about permits — link to trade guide
3. Reach out to HVAC contractor associations — ask for a resource link
4. Guest posts on contractor blogs ("we built a free permit tool")

**Long-term:**
- HARO (Help a Reporter Out) — quote on home improvement articles → links
- Local contractor Facebook groups — share the free tool

---

## Tracking

### What to watch in Search Console (monthly)
- Total impressions → are we getting seen?
- Click-through rate → is our title compelling enough?
- Average position → are we climbing?
- Top queries → what are people searching that finds us?

### Conversion tracking
- Add Google Analytics 4 (G-TREG49VYYZ already configured)
- Track: pageview → tool use → email capture
- Key metric: organic visitor → email capture rate

---

## Regenerating Pages

When you add new cities or update fees:
```bash
python3 /data/.openclaw/workspace/projects/permitassist/seo/generate_seo.py
```
Then redeploy Railway (auto-deploy on git push or manual trigger).
New pages will have updated `lastmod` in sitemap → Google recrawls faster.

---

## Current Page Count
| Type | Count |
|---|---|
| City × Trade | 351 |
| State hubs | 51 |
| Trade guides | 9 |
| Index | 1 |
| **Total** | **412** |

Target: 1,000+ pages by Month 3 (add more cities + blog content)
