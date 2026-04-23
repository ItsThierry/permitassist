## Final Report

This report details the comprehensive upgrade of the PermitAssist research engine. All 13 requested improvements have been implemented, and a rigorous 20-case stress test has been completed to validate the enhancements.

### 1. Implemented Improvements

All 13 improvements were successfully integrated into `/data/permitassist/api/research_engine.py`:

-   [x] **IMPROVEMENT 1: Parallel Scraping:** Implemented `scrape_urls_parallel` using `concurrent.futures.ThreadPoolExecutor` to fetch multiple URLs concurrently, significantly speeding up the data gathering phase.
-   [x] **IMPROVEMENT 2: LLM Query Expansion:** Added `expand_permit_query` to generate alternative search terms, improving search relevancy for ambiguous job types.
-   [x] **IMPROVEMENT 3: Jurisdiction Disambiguation:** Integrated `normalize_jurisdiction` to handle edge cases like unincorporated areas, Alaskan boroughs, and Louisiana parishes, leading to more accurate initial searches.
-   [x] **IMPROVEMENT 4: Table-Aware Fee Extraction:** Implemented `extract_tables_from_html` and enhanced fee extraction logic to better capture fee schedules, which are often presented in HTML tables.
-   [x] **IMPROVEMENT 5: Multi-Page Link Following:** Added `find_followup_links` to crawl from the primary permit page to secondary pages (like fee schedules), enriching the context.
-   [x] **IMPROVEMENT 6: Context-Aware Phone Extraction:** Replaced simple regex with `extract_best_phone`, which scores potential phone numbers based on proximity to relevant keywords, improving the accuracy of contact information.
-   [x] **IMPROVEMENT 7: Per-Field Confidence Scoring:** Implemented `score_field_confidence` to assign `high`, `medium`, or `low` confidence to each extracted field, providing better signals for data reliability.
-   [x] **IMPROVEMENT 8: Source Citation Per Field:** The system now tracks and can display the source URL for each piece of structured data.
-   [x] **IMPROVEMENT 9: Contradiction Detection:** Added `detect_contradictions` to flag disagreements in critical data (like phone numbers and portal URLs) when multiple sources are scraped.
-   [x] **IMPROVEMENT 10: Auto-Update KB:** Implemented `auto_update_city_kb` to automatically write back high-confidence, `.gov`-sourced contact information to `cities.json`, allowing the system to learn and improve over time.
-   [x] **IMPROVEMENT 11: Content Freshness Signal:** Added `check_page_freshness` to check `Last-Modified` headers, providing users with a signal of how recently the source data was updated.
-   [x] **IMPROVEMENT 12: Staleness Detection with Content Hashing:** The caching mechanism now stores an MD5 hash of content and can check `ETag`/`Last-Modified` headers to detect page changes.
-   [x] **IMPROVEMENT 13: Success Pattern Learning:** A new `url_patterns` SQLite table was created to track which domains yield high-quality results, and this data is used to boost their ranking in future searches.

### 2. Stress Test Scores

The full stress test output is saved at `/data/permitassist/search_stress_test_v3.txt`.

**Per-city scores (out of 10):**
`9, 9, 8, 8, 10, 10, 10, 7, 7, 8, 10, 10, 10, 9, 7, 10, 10, 10, 7, 10`

### 3. Performance Analysis: New Average vs. Old

-   **Old Average Quality:** 7.8 / 10
-   **New Average Quality:** **8.95 / 10**

The implemented improvements resulted in a **+1.15 point increase** in average quality, bringing the system very close to the target of 9+/10. All 20 test cases scored a 7 or higher, indicating a significant reduction in low-quality results and outright failures.

### 4. Most Impactful Improvements

1.  **Parallel Scraping & Multi-Page Link Following (#1, #5):** This combination was the most significant architectural change. It allows the engine to build a much richer context from multiple official pages (e.g., main permit page + fee schedule page) in roughly the same amount of time the old engine took to scrape one page. This directly led to better fee and contact info extraction.
2.  **Context-Aware Phone Extraction (#6):** The previous regex-based phone scraper was prone to grabbing fax numbers or irrelevant phone numbers from headers/footers. The new context-aware system dramatically increased the likelihood of finding the correct building department phone number, which is a critical data point.
3.  **Jurisdiction Disambiguation (#3):** This pre-search step was highly effective for cases like "Unincorporated Harris County", "Kenai" (AK), and "Shreveport" (LA). By refining the search location *before* the first query, it prevented wasted searches and led directly to more relevant `.gov` or `.us` domains.
4.  **Auto-Update KB (#10):** During the test run, the engine successfully identified and added one new city (Biloxi, MS) to the knowledge base automatically. This self-improvement loop is a powerful feature for long-term quality growth.

### 5. Remaining Dependencies on External Services

While the engine is much more robust, premium external services are still key to overcoming certain web scraping challenges:

-   **JavaScript-Rendered Pages (SPA):** Many modern city permit portals (especially those using Accela, TylerTech, OpenGov) are single-page applications that require JavaScript to render. The current fallback using `jina.ai` and direct fetch can handle some of these, but a dedicated, JS-rendering scraping API like **Firecrawl** (for which a key can be provided) is the most reliable way to handle these complex sites. Without it, some portal pages will return little or no content.
-   **Rate Limiting & Blocks:** Aggressive scraping can be blocked by services like Cloudflare. While the current system has basic fallbacks and retries, a managed proxy service like **ScrapingBee** would provide IP rotation and more advanced anti-blocking measures, making the scraping layer more resilient.

### 6. Remaining Edge Cases & Challenges

-   **Ambiguous PDF Fee Schedules:** Some cities only publish fees in poorly structured PDFs. While the engine can now process text from PDFs, extracting structured fees from a complex, multi-page table inside a PDF remains a challenge and is a source of "fee not found" results.
-   **County-vs-City Jurisdiction:** For small towns, it's often difficult to determine programmatically whether the city handles its own permits or defers to the county. The jurisdiction disambiguation helps, but this still requires careful analysis of search results.
-   **Vague Portal Naming:** Even with LLM query expansion, some cities use non-standard or vague naming for permits in their online portals, which can be hard to guess correctly.

Overall, the upgrade was highly successful, delivering a major boost in accuracy, speed, and intelligence. The engine is now more resilient, learns from its successful discoveries, and provides a much more reliable foundation for the PermitAssist service.