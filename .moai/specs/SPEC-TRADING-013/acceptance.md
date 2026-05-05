---
id: SPEC-TRADING-013
type: acceptance
created: 2026-05-05
related_spec: SPEC-TRADING-013/spec.md
---

# SPEC-TRADING-013 Acceptance Criteria

## Module 1: Source Catalog

**AC-1-1: Source catalog structure validation**
- Given: The module `src/trading/news/sources.py` exists
- When: The `NewsSource` dataclass is inspected
- Then: It is a frozen dataclass with fields `name: str`, `url: str`, `source_type: Literal["rss", "web"]`, `sector: str`, `language: Literal["en", "ko"]`, `notes: str`, `last_verified: date`

**AC-1-2: Sector category completeness**
- Given: The source catalog is loaded
- When: All unique sector values are extracted from the catalog
- Then: Exactly 12 sectors exist: `macro_economy`, `stock_market`, `semiconductor`, `biotech_pharma`, `energy_commodities`, `it_ai`, `finance_banking`, `auto_ev_battery`, `steel_materials`, `retail_consumer`, `gaming_entertainment`, `defense_aerospace`

**AC-1-3: Source count validation**
- Given: The source catalog is loaded via `all_sources()`
- When: Sources are counted by `source_type`
- Then: There are exactly 42 total sources — 31 with `source_type == "rss"` and 11 with `source_type == "web"`

**AC-1-4: Helper function sector filtering**
- Given: The source catalog contains sources tagged with sector `semiconductor`
- When: `get_sources_by_sector("semiconductor")` is called
- Then: Only sources with `sector == "semiconductor"` are returned and the list is non-empty

**AC-1-5: Helper function type filtering**
- Given: The source catalog is loaded
- When: `get_sources_by_type("web")` is called
- Then: Exactly 11 sources are returned, all with `source_type == "web"`

**AC-1-6: Helper function language filtering**
- Given: The source catalog contains both English and Korean sources
- When: `get_sources_by_language("ko")` is called
- Then: Only sources with `language == "ko"` are returned

**AC-1-7: Feature flag enabled (default)**
- Given: The configuration `NEWS_CRAWLING_V2_ENABLED` is `True` (default)
- When: The news crawling system initializes
- Then: The new 42-source catalog is active

**AC-1-8: Feature flag disabled (rollback)**
- Given: The configuration `NEWS_CRAWLING_V2_ENABLED` is set to `False`
- When: The news crawling system initializes
- Then: The system falls back to SPEC-007's original 12-source catalog and the new 42-source catalog is not used

---

## Module 2: RSS Fetcher

**AC-2-1: Successful RSS fetch and parse**
- Given: An RSS feed source returns valid XML with 10 entries
- When: The RSS fetcher processes the source
- Then: 10 articles are extracted with fields `title`, `link`, `published` (UTC datetime), `summary`, `source_name`, `sector`, `language`

**AC-2-2: Parallel fetch with concurrency limit**
- Given: 31 RSS sources are configured
- When: The RSS fetcher executes a full crawl cycle
- Then: Requests are dispatched via `asyncio.gather` with a maximum of 10 simultaneous connections

**AC-2-3: Rate limiting per domain**
- Given: Two RSS sources share the same domain (e.g., `fnnews.com`)
- When: Both sources are fetched in the same cycle
- Then: A minimum of 1 second delay is enforced between consecutive requests to that domain

**AC-2-4: HTTP timeout handling**
- Given: An RSS feed takes longer than 15 seconds to respond
- When: The timeout threshold is reached
- Then: The request is aborted, a timeout is logged, the failure counter for that source is incremented, and remaining feeds continue processing

**AC-2-5: Non-2xx response handling**
- Given: An RSS feed returns HTTP 503
- When: The fetcher processes the response
- Then: The failure counter for that source is incremented, the source is skipped for the current cycle, and other sources are unaffected

**AC-2-6: Unparseable XML fallback**
- Given: An RSS feed returns content that `feedparser` cannot parse
- When: The fetcher attempts to process it
- Then: The fetcher attempts parsing with `xml.etree.ElementTree` as fallback before skipping the source

**AC-2-7: User-Agent header**
- Given: Any HTTP request is made by the RSS fetcher
- When: The request headers are inspected
- Then: The `User-Agent` header is set to `trading-bot/0.2 (personal use; non-commercial)`

**AC-2-8: Feed with missing published date**
- Given: An RSS entry lacks a `published` or `pubDate` field
- When: The fetcher processes that entry
- Then: The entry is still extracted with `published` set to `None` (deferred to normalizer for inference)

---

## Module 3: Web Scraper

**AC-3-1: Successful web scrape with CSS selectors**
- Given: A web source (e.g., `thelec.kr`) returns valid HTML matching configured CSS selectors
- When: The web scraper processes the source
- Then: Article titles and URLs are extracted correctly using the configured `ScrapeRule`

**AC-3-2: ScrapeRule registry completeness**
- Given: The `ScrapeRule` registry is loaded
- When: All rules are counted
- Then: Exactly 11 `ScrapeRule` entries exist, one per web source

**AC-3-3: Absolute URL resolution**
- Given: A web source has relative links (e.g., `/article/12345`)
- When: The scraper extracts article URLs
- Then: All URLs are resolved to absolute form (e.g., `https://www.thelec.kr/article/12345`)

**AC-3-4: Rate limiting compliance**
- Given: Multiple web sources are processed sequentially
- When: Two requests target the same domain
- Then: A minimum of 1 second delay is enforced between them (same rate limiter as RSS fetcher)

**AC-3-5: HTML structure change detection**
- Given: A web source's HTML structure has changed and CSS selectors yield zero results
- When: The scraper processes that source
- Then: A `structure_change_detected` event is logged, the source is flagged for health review, and other sources continue processing

**AC-3-6: No headless browser usage**
- Given: The web scraper module exists
- When: Its dependencies and imports are inspected
- Then: No imports of Playwright, Selenium, or any headless browser library exist; only `httpx` and `BeautifulSoup` are used

**AC-3-7: Encoding handling**
- Given: A web source uses `euc-kr` encoding
- When: The scraper fetches and parses the page
- Then: The content is decoded using the encoding specified in the `ScrapeRule` and parsed correctly without mojibake

---

## Module 4: Content Normalizer

**AC-4-1: Unified Article dataclass from RSS**
- Given: The RSS fetcher returns raw entry data
- When: The normalizer processes it
- Then: An `Article` dataclass is produced with all required fields: `title`, `url`, `summary`, `source_name`, `sector`, `language`, `published_at` (UTC), `crawled_at` (UTC), `content_hash`

**AC-4-2: Unified Article dataclass from web scraper**
- Given: The web scraper returns extracted headline data
- When: The normalizer processes it
- Then: An equivalent `Article` dataclass is produced with the same structure as RSS-derived articles

**AC-4-3: Title normalization**
- Given: A raw title contains leading/trailing whitespace, multiple internal spaces, and RSS encoding artifacts (e.g., `&amp;`)
- When: The normalizer processes the title
- Then: The title is cleaned: whitespace stripped, multiple spaces collapsed, encoding artifacts resolved

**AC-4-4: Content hash deduplication within batch**
- Given: Two articles with identical normalized titles appear in the same crawl batch
- When: The normalizer deduplicates the batch
- Then: Only the first occurrence is kept; the duplicate is discarded

**AC-4-5: Content hash calculation**
- Given: An article with title "Samsung Q1 Earnings Beat Expectations"
- When: The content hash is computed
- Then: The hash equals SHA-256 of the normalized title string

**AC-4-6: Date inference for missing published_at**
- Given: An article has no parseable publication date from the source
- When: The normalizer processes it
- Then: `published_at` is set to `crawled_at` value and `date_inferred` is set to `True`

**AC-4-7: Summary truncation at word boundary**
- Given: An article has a summary of 700 characters
- When: The normalizer processes the summary
- Then: The summary is truncated to a maximum of 500 characters, cut at the nearest word boundary (no mid-word truncation)

**AC-4-8: Summary within limit preserved**
- Given: An article has a summary of 300 characters
- When: The normalizer processes the summary
- Then: The summary is preserved as-is without modification

---

## Module 5: Storage and Integration

**AC-5-1: Article insertion success**
- Given: A normalized `Article` with a unique `content_hash`
- When: It is inserted into the `news_articles` table
- Then: The row is persisted with all fields correctly mapped to the table schema

**AC-5-2: Cross-cycle deduplication via ON CONFLICT**
- Given: An article with `content_hash = "abc123"` already exists in `news_articles`
- When: A new crawl cycle produces an article with the same `content_hash`
- Then: The insert is silently skipped (`ON CONFLICT DO NOTHING`), no error is raised, and the existing row is unchanged

**AC-5-3: Bulk insert transaction atomicity**
- Given: A crawl cycle produces 100 articles for insertion
- When: The storage module inserts them
- Then: All inserts occur within a single database transaction; if any system error occurs, all are rolled back

**AC-5-4: Audit log on crawl completion**
- Given: A crawl cycle completes (success or partial)
- When: The cycle finishes
- Then: An entry is written to `audit_log` containing: total articles fetched, new articles inserted, duplicates skipped, sources failed, total duration in seconds

**AC-5-5: 90-day retention cleanup**
- Given: Articles exist in `news_articles` with `crawled_at` older than 90 days
- When: The daily cleanup job executes
- Then: All articles where `crawled_at < NOW() - INTERVAL '90 days'` are deleted

**AC-5-6: Cron integration with existing schedule**
- Given: The existing 06:00 `build_macro_context` cron job runs daily
- When: The scheduler is configured
- Then: A new `crawl_all_news` job is scheduled at 05:45 (15 minutes before context build), and it completes before the 06:00 job starts

**AC-5-7: CLI crawl command (full crawl)**
- Given: The CLI is available
- When: `trading crawl-news` is executed without arguments
- Then: All 42 enabled sources are crawled and results are stored

**AC-5-8: CLI crawl command (sector filter)**
- Given: The CLI is available
- When: `trading crawl-news --sector semiconductor` is executed
- Then: Only sources with `sector == "semiconductor"` are crawled

**AC-5-9: CLI crawl command (force single source)**
- Given: A source has been auto-disabled by health monitoring
- When: `trading crawl-news --source "The Elec" --force` is executed
- Then: The source is crawled regardless of its disabled status

**AC-5-10: Database schema validation**
- Given: The migration `013_news_articles.sql` is applied
- When: The `news_articles` table is inspected
- Then: The table has a `content_hash` UNIQUE constraint, an index on `(sector, published_at DESC)`, an index on `(language)`, and an index on `(crawled_at DESC)`

---

## Module 6: Sector Context Builder

**AC-6-1: macro_news.md generation**
- Given: `news_articles` contains articles from the last 7 days across sectors `macro_economy`, `finance_banking`, `energy_commodities`
- When: `build_macro_news` executes (Friday 16:30 or on-demand)
- Then: `data/contexts/macro_news.md` is generated containing headlines grouped by sector, sorted by `published_at` DESC, maximum 50 headlines total, with English-language sources prioritized

**AC-6-2: micro_news.md generation with watchlist**
- Given: The watchlist contains tickers mapped to sectors `semiconductor` and `biotech_pharma`, and `news_articles` has articles from the last 3 days in those sectors
- When: `build_micro_news` executes (weekday 06:45)
- Then: `data/contexts/micro_news.md` is generated containing headlines for `semiconductor` and `biotech_pharma` sectors, maximum 30 headlines per sector, with Korean-language sources prioritized

**AC-6-3: Context file format compliance**
- Given: The context builder generates `macro_news.md`
- When: The file content is inspected
- Then: Each section follows the format: `## [Sector Name] (N articles, last updated YYYY-MM-DD HH:MM KST)` followed by lines of `- [Title] | Source | Date`

**AC-6-4: Empty watchlist fallback**
- Given: The watchlist is empty or undefined
- When: `build_micro_news` executes
- Then: `micro_news.md` includes articles from ALL available sectors (full coverage mode) rather than producing an empty file

**AC-6-5: No LLM calls in context builder**
- Given: The `context_builder.py` module exists
- When: Its imports and function calls are inspected
- Then: No LLM API calls are made (no openai, anthropic, or similar library invocations); output is raw headlines only

**AC-6-6: Ticker-to-sector mapping**
- Given: A ticker "005930" (Samsung Electronics) is in the watchlist
- When: The ticker-to-sector mapping is applied
- Then: The ticker maps to `semiconductor` sector for news matching

**AC-6-7: Unknown ticker defaults to stock_market**
- Given: A ticker with no known sector mapping is in the watchlist
- When: The ticker-to-sector mapping is applied
- Then: The ticker defaults to `stock_market` sector

**AC-6-8: Sector with all sources failed**
- Given: All sources in the `defense_aerospace` sector have failed in the latest crawl cycle
- When: The context builder generates the .md file
- Then: The section for that sector is marked `[DATA UNAVAILABLE]` rather than being omitted

---

## Module 7: Health Monitoring

**AC-7-1: Health metrics tracking per source**
- Given: A source is crawled (success or failure)
- When: The health module updates metrics
- Then: The `news_source_health` table reflects updated values for `consecutive_failures`, `last_success` or `last_failure`, `total_fetches`, `total_failures`

**AC-7-2: Successful fetch resets consecutive failures**
- Given: A source has `consecutive_failures = 2`
- When: The next crawl of that source succeeds
- Then: `consecutive_failures` is reset to 0, `last_success` is updated, and `total_fetches` is incremented

**AC-7-3: Warning alert at 3 consecutive failures**
- Given: A source accumulates 3 consecutive failures
- When: The health check evaluates after the crawl cycle
- Then: A Telegram alert is sent with message format: `"[NEWS HEALTH] {source_name} ({sector}) failed 3 consecutive times. Last error: {error}. Consider review."`

**AC-7-4: Auto-disable at 7 consecutive failures**
- Given: A source accumulates 7 consecutive failures
- When: The health check evaluates after the crawl cycle
- Then: The source is automatically disabled (`enabled = False` in `news_source_health`), a critical Telegram alert is sent, and subsequent crawl cycles skip this source

**AC-7-5: Disabled source skipped during crawl**
- Given: A source has `enabled = False` in the health table
- When: The crawler loads sources for a crawl cycle
- Then: The disabled source is excluded from the crawl (not fetched)

**AC-7-6: Manual re-enable via force flag**
- Given: A source is disabled (`enabled = False`)
- When: `trading crawl-news --source "The Elec" --force` is executed and the crawl succeeds
- Then: The source is re-enabled (`enabled = True`), `consecutive_failures` is reset to 0, and `last_success` is updated

**AC-7-7: news-health CLI output**
- Given: Multiple sources exist with varying health states
- When: `trading news-health` is executed
- Then: A formatted table is displayed showing each source's name, sector, enabled/disabled status, success rate percentage, last success timestamp, and last failure timestamp

**AC-7-8: Weekly health summary (optional)**
- Given: The weekly summary feature is enabled
- When: Sunday arrives
- Then: A Telegram message is sent containing: total active sources, total disabled sources, average success rate across all sources, and sectors with degraded coverage (less than 50% source availability)

**AC-7-9: Health state persistence**
- Given: The system restarts (Docker container restart)
- When: The health module initializes
- Then: All health state is recovered from the `news_source_health` PostgreSQL table; no in-memory state is lost

---

## Cross-Module Integration Scenarios

**AC-INT-1: Full crawl cycle end-to-end**
- Given: All 42 sources are enabled and accessible
- When: `crawl_all_news()` executes at 05:45
- Then: RSS feeds are fetched in parallel (max 10 concurrent), web sources are scraped sequentially with rate limiting, all results are normalized to `Article` dataclass, deduplicated by content_hash, inserted into `news_articles` in a single transaction, health counters are updated, and an audit_log entry is written

**AC-INT-2: Crawl followed by context generation**
- Given: A successful crawl cycle at 05:45 inserted articles into `news_articles`
- When: `build_micro_news()` executes at 06:45
- Then: `micro_news.md` contains headlines from the newly crawled articles matching watchlist sectors

**AC-INT-3: Feature flag disables entire new system**
- Given: `NEWS_CRAWLING_V2_ENABLED` is set to `False`
- When: The 05:45 cron job triggers
- Then: The new 42-source crawl does NOT execute, and the system falls back to SPEC-007's original behavior

**AC-INT-4: Partial source failure graceful degradation**
- Given: 5 out of 42 sources fail during a crawl cycle (timeout or HTTP error)
- When: The crawl cycle completes
- Then: Articles from the 37 successful sources are stored normally, the 5 failed sources have their health counters incremented, and no data from previous successful cycles is lost

**AC-INT-5: Rate limiter shared across modules**
- Given: Source `fnnews.com` has both RSS feeds and a web page to scrape
- When: Both are processed in the same crawl cycle
- Then: The rate limiter enforces 1-second delay between ALL requests to `fnnews.com` regardless of whether they come from the RSS fetcher or web scraper

**AC-INT-6: Database schema migration**
- Given: The project runs on a fresh database with existing migrations applied
- When: Migration `013_news_articles.sql` is executed
- Then: Both `news_articles` and `news_source_health` tables are created with correct schemas, constraints, and indexes

**AC-INT-7: Existing cron schedule unaffected**
- Given: The system is running with the new crawling infrastructure
- When: The existing 06:00 `build_macro_context` and 06:30 `build_micro_context` jobs execute
- Then: They continue to function unchanged (new crawl job at 05:45 does not interfere with or delay the existing schedule)
