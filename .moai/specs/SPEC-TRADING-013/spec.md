---
id: SPEC-TRADING-013
version: 0.1.0
status: draft
created: 2026-05-05
updated: 2026-05-05
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "Global + Sector News Crawling Infrastructure"
related_specs:
  - SPEC-TRADING-001
  - SPEC-TRADING-007
  - SPEC-TRADING-008
  - SPEC-TRADING-009
---

# SPEC-TRADING-013 — Global + Sector News Crawling Infrastructure

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-05 | 0.1.0 | Initial draft — 7 modules, 42 verified sources, sector-based news crawling | onigunsow |

## Scope Summary

The current `src/trading/contexts/rss_feeds.py` has limited coverage (12 feeds across 4 tiers) and several broken/outdated sources. This SPEC defines a comprehensive news crawling infrastructure with **42 verified sources** spanning 12 sectors, supporting both global (English) and domestic (Korean) news.

This SPEC **replaces** the existing `rss_feeds.py` entirely and upgrades SPEC-TRADING-007's `macro_news.md` and `micro_news.md` generation to use sector-specific, multi-source crawling instead of a handful of general feeds.

Key differentiators from SPEC-007:
- **Sector-specific coverage**: 12 distinct sectors vs. generic economy/stock categories
- **42 verified sources** (2026-05-05 tested) vs. 12 feeds
- **Dual content type**: RSS (31 feeds) + Web scraping (11 sites)
- **Per-persona context**: macro_news.md (global macro) + micro_news.md (watchlist sector-specific)
- **Health monitoring**: Dead feed detection + Telegram alerts

## Environment

- Existing SPEC-TRADING-001 infrastructure — Postgres 16-alpine, Docker compose, Telegram, httpx
- Existing cron schedule: 06:00 `build_macro_context`, 06:30 `build_micro_context`, 06:45 `build_micro_news`, Friday 16:30 `build_macro_news`
- New module: `src/trading/news/` (replaces `src/trading/contexts/rss_feeds.py`)
- New DB table: `news_articles` (Postgres 16-alpine)
- Dependencies already present: `httpx`, `feedparser` (in pyproject.toml)
- New dependency: `beautifulsoup4` (add to pyproject.toml if missing)
- All 42 sources verified accessible via httpx from Docker container (2026-05-05)
- No Playwright or headless browser required

## Assumptions

1. All 42 source URLs remain accessible from the Docker container over HTTPS without authentication or JavaScript rendering. Verified 2026-05-05.
2. RSS feeds return well-formed XML parseable by `feedparser` or `xml.etree.ElementTree`. Malformed feeds degrade gracefully (skip, log, alert).
3. Web sources (11 sites) have stable HTML structure for headline/article extraction via BeautifulSoup. Structure changes are detected by health monitoring and alerted.
4. Rate limiting of 1 second between requests to the same domain is sufficient to avoid IP blocking from all listed sources.
5. The existing cron schedule (06:00/06:30/06:45/Friday 16:30) provides adequate integration points for the new crawling infrastructure.
6. `beautifulsoup4` + `lxml` are production-stable and suitable for the project's dependency policy (no beta/alpha).
7. Content deduplication by title hash (SHA-256 of normalized title) is sufficient to prevent duplicate articles across crawl cycles.
8. The existing `data/contexts/` directory and persona prompt injection mechanism (SPEC-007) remain unchanged — only the content generation pipeline changes.

## Robustness Principles (SPEC-001 6 Principles Inherited)

This SPEC inherits SPEC-TRADING-001 v0.2.0's 6 Robustness principles. Specifically:

- **External dependency failure assumption** — Any of 42 sources may be temporarily or permanently unavailable. Crawling continues with available sources. Stale content is preferable to no content.
- **Silent failure prohibition** — Every fetch failure, parse error, or deduplication anomaly generates an audit_log entry + Telegram alert (batched, not per-source).
- **Automatic recovery + human notification** — Temporary failures auto-retry (max 3). Permanent failures (3+ consecutive cycles) trigger health alert to user.
- **Graceful degradation** — If all sources in a sector fail, the sector section in context .md files is marked "[DATA UNAVAILABLE]" rather than omitted.
- **State integrity via transactions** — Bulk article inserts use a single DB transaction per crawl cycle.

---

## Requirements (EARS Format)

EARS notation: **U**=Ubiquitous, **E**=Event-driven, **S**=State-driven, **O**=Optional, **N**=Unwanted

---

### Module 1 — Source Catalog

**REQ-NEWS-01-1 [U]** The system shall maintain a source catalog at `src/trading/news/sources.py` using frozen dataclass `NewsSource` with fields: `name: str`, `url: str`, `source_type: Literal["rss", "web"]`, `sector: str`, `language: Literal["en", "ko"]`, `notes: str = ""`.

**REQ-NEWS-01-2 [U]** The system shall define 12 sector categories: `macro_economy`, `stock_market`, `semiconductor`, `biotech_pharma`, `energy_commodities`, `it_ai`, `finance_banking`, `auto_ev_battery`, `steel_materials`, `retail_consumer`, `gaming_entertainment`, `defense_aerospace`.

**REQ-NEWS-01-3 [U]** The source catalog shall contain exactly 42 verified sources: 31 RSS feeds and 11 web scraping targets, distributed across the 12 sectors as defined in the Verified Source Catalog.

**REQ-NEWS-01-4 [U]** Each `NewsSource` entry shall include verification metadata: `last_verified: date` field recording the most recent successful accessibility test.

**REQ-NEWS-01-5 [U]** The system shall provide helper functions: `get_sources_by_sector(sector: str) -> list[NewsSource]`, `get_sources_by_type(source_type: str) -> list[NewsSource]`, `get_sources_by_language(lang: str) -> list[NewsSource]`, `all_sources() -> list[NewsSource]`.

**REQ-NEWS-01-6 [E]** When the feature flag `news_crawling_v2_enabled` is `True` (default), the system shall use the new 42-source catalog. When `False`, the system shall fall back to SPEC-007's original 12-source catalog for rollback safety.

---

### Module 2 — RSS Fetcher

**REQ-NEWS-02-1 [U]** The system shall implement an RSS fetcher at `src/trading/news/rss_fetcher.py` that processes all sources where `source_type == "rss"` (31 feeds).

**REQ-NEWS-02-2 [U]** The RSS fetcher shall use `feedparser` as the primary XML parser with `xml.etree.ElementTree` as fallback for feeds that `feedparser` cannot parse.

**REQ-NEWS-02-3 [U]** The RSS fetcher shall extract the following fields from each feed entry: `title`, `link`, `published` (parsed to UTC datetime), `summary` (if available), `source_name`, `sector`, `language`.

**REQ-NEWS-02-4 [U]** The RSS fetcher shall execute parallel fetch using `asyncio.gather` with `httpx.AsyncClient`, respecting a maximum concurrency of 10 simultaneous connections.

**REQ-NEWS-02-5 [S]** While fetching RSS feeds, the system shall enforce rate limiting of minimum 1 second delay between consecutive requests to the same domain (tracked by domain, not by URL).

**REQ-NEWS-02-6 [S]** While a feed request exceeds the HTTP timeout of 15 seconds, the system shall abort the request, log the timeout, and continue with remaining feeds.

**REQ-NEWS-02-7 [E]** When an RSS feed returns non-2xx status or unparseable content, the system shall increment a failure counter for that source and skip it for the current cycle without affecting other sources.

**REQ-NEWS-02-8 [U]** The RSS fetcher shall set User-Agent header to `trading-bot/0.2 (personal use; non-commercial)` for all HTTP requests.

---

### Module 3 — Web Scraper

**REQ-NEWS-03-1 [U]** The system shall implement a web scraper at `src/trading/news/web_scraper.py` that processes all sources where `source_type == "web"` (11 sites).

**REQ-NEWS-03-2 [U]** The web scraper shall use `httpx.AsyncClient` for HTTP retrieval and `BeautifulSoup` with `lxml` parser for HTML parsing.

**REQ-NEWS-03-3 [U]** The web scraper shall implement per-site extraction rules as a registry of `ScrapeRule` dataclasses, each defining: `source_name: str`, `headline_selector: str` (CSS selector), `link_selector: str`, `date_selector: str | None`, `encoding: str = "utf-8"`.

**REQ-NEWS-03-4 [U]** The web scraper shall extract at minimum: article title (headline text), article URL (absolute link), and publication date (if available from page structure, else crawl timestamp).

**REQ-NEWS-03-5 [S]** While scraping web sources, the system shall enforce the same rate limiting as Module 2 (1 second between requests to same domain).

**REQ-NEWS-03-6 [E]** When a web source's HTML structure changes such that the configured CSS selectors yield zero results, the system shall log a `structure_change_detected` event and flag the source for health review.

**REQ-NEWS-03-7 [N]** The web scraper shall NOT use Playwright, Selenium, or any headless browser. All scraping shall be httpx + BeautifulSoup only.

---

### Module 4 — Content Normalizer

**REQ-NEWS-04-1 [U]** The system shall implement a content normalizer at `src/trading/news/normalizer.py` that produces a unified `Article` dataclass from both RSS and web scraper outputs.

**REQ-NEWS-04-2 [U]** The `Article` dataclass shall contain: `title: str`, `url: str`, `summary: str | None`, `source_name: str`, `sector: str`, `language: Literal["en", "ko"]`, `published_at: datetime` (UTC), `crawled_at: datetime` (UTC), `content_hash: str` (SHA-256 of normalized title).

**REQ-NEWS-04-3 [U]** Title normalization shall: strip whitespace, collapse multiple spaces, remove leading/trailing punctuation artifacts from RSS encoding.

**REQ-NEWS-04-4 [U]** The content normalizer shall deduplicate articles by `content_hash` within a single crawl cycle — if the same title hash already exists in the current batch, only the first occurrence is kept.

**REQ-NEWS-04-5 [E]** When an article's `published_at` cannot be parsed from source data, the system shall use `crawled_at` as the publication timestamp and set a flag `date_inferred: bool = True`.

**REQ-NEWS-04-6 [U]** The normalizer shall truncate `summary` to maximum 500 characters (preserving word boundaries) to control DB storage and context token usage.

---

### Module 5 — Storage & Integration

**REQ-NEWS-05-1 [U]** The system shall store normalized articles in a PostgreSQL table `news_articles` with schema:

```sql
CREATE TABLE news_articles (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    summary TEXT,
    source_name VARCHAR(100) NOT NULL,
    sector VARCHAR(50) NOT NULL,
    language VARCHAR(5) NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    crawled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    content_hash VARCHAR(64) NOT NULL,
    date_inferred BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_content_hash UNIQUE (content_hash)
);

CREATE INDEX idx_news_sector_published ON news_articles (sector, published_at DESC);
CREATE INDEX idx_news_language ON news_articles (language);
CREATE INDEX idx_news_crawled ON news_articles (crawled_at DESC);
```

**REQ-NEWS-05-2 [U]** Article insertion shall use `ON CONFLICT (content_hash) DO NOTHING` to handle cross-cycle deduplication without errors.

**REQ-NEWS-05-3 [E]** When a crawl cycle completes, the system shall log to `audit_log`: total articles fetched, new articles inserted, duplicates skipped, sources failed, total duration.

**REQ-NEWS-05-4 [U]** The system shall retain articles in `news_articles` for 90 days. A daily cleanup job shall delete articles where `crawled_at < NOW() - INTERVAL '90 days'`.

**REQ-NEWS-05-5 [E]** When the existing 06:00 cron `build_macro_context` executes, the system shall first trigger a news crawl cycle (all 42 sources) before context file generation.

**REQ-NEWS-05-6 [U]** The crawl cycle shall be callable both via cron integration and via CLI: `trading crawl-news [--sector SECTOR] [--force]`.

---

### Module 6 — Sector Context Builder

**REQ-NEWS-06-1 [U]** The system shall implement a sector context builder at `src/trading/news/context_builder.py` that generates persona-ready `.md` context files from `news_articles`.

**REQ-NEWS-06-2 [E]** When `build_macro_news` executes (Friday 16:30 or on-demand), the system shall generate `data/contexts/macro_news.md` containing:
- Global macro headlines from sectors: `macro_economy`, `finance_banking`, `energy_commodities`
- English-language sources prioritized, Korean supplementary
- Last 7 days of articles, grouped by sector, sorted by `published_at` DESC
- Maximum 50 headlines total (most recent per sector)

**REQ-NEWS-06-3 [E]** When `build_micro_news` executes (weekday 06:45), the system shall generate `data/contexts/micro_news.md` containing:
- Sector-specific news matched to watchlist ticker sectors
- Mapping: watchlist tickers -> their sectors -> relevant news from those sectors
- Last 3 days of articles, grouped by ticker-relevant sector
- Korean-language sources prioritized for domestic tickers
- Maximum 30 headlines per sector

**REQ-NEWS-06-4 [U]** Context `.md` files shall follow this format per section:

```markdown
## [Sector Name] (N articles, last updated YYYY-MM-DD HH:MM KST)

- [Title] | Source | Date
- [Title] | Source | Date
...
```

**REQ-NEWS-06-5 [S]** While the watchlist is empty or undefined, `micro_news.md` shall include all available sectors (full coverage mode) rather than generating an empty file.

**REQ-NEWS-06-6 [N]** The sector context builder shall NOT call any LLM for summarization. Context files contain raw headlines only. LLM summarization is deferred to the persona prompt layer (SPEC-007 REQ-CTX-01-5 remains for weekly LLM summary as a separate optional step).

**REQ-NEWS-06-7 [U]** The system shall provide a ticker-to-sector mapping function. If a ticker's sector cannot be determined, it shall default to the `stock_market` sector for news matching.

---

### Module 7 — Health Monitoring

**REQ-NEWS-07-1 [U]** The system shall implement health monitoring at `src/trading/news/health.py` that tracks per-source availability metrics.

**REQ-NEWS-07-2 [U]** For each source, the system shall maintain: `consecutive_failures: int`, `last_success: datetime | None`, `last_failure: datetime | None`, `total_fetches: int`, `total_failures: int`.

**REQ-NEWS-07-3 [E]** When a source accumulates 3 consecutive failures, the system shall send a Telegram alert: `"[NEWS HEALTH] {source_name} ({sector}) failed 3 consecutive times. Last error: {error}. Consider review."`.

**REQ-NEWS-07-4 [E]** When a source accumulates 7 consecutive failures (1 week of daily crawls), the system shall automatically disable the source (set `enabled: bool = False` in health state) and send a critical Telegram alert.

**REQ-NEWS-07-5 [U]** Health state shall be persisted in a PostgreSQL table `news_source_health`:

```sql
CREATE TABLE news_source_health (
    source_name VARCHAR(100) PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_success TIMESTAMPTZ,
    last_failure TIMESTAMPTZ,
    last_error TEXT,
    total_fetches INTEGER NOT NULL DEFAULT 0,
    total_failures INTEGER NOT NULL DEFAULT 0
);
```

**REQ-NEWS-07-6 [E]** When a previously disabled source succeeds on a manual retry (`trading crawl-news --source NAME --force`), the system shall re-enable it and reset `consecutive_failures` to 0.

**REQ-NEWS-07-7 [U]** The system shall provide a CLI command `trading news-health` that displays a table of all sources with their health status, success rate, and last activity timestamps.

**REQ-NEWS-07-8 [O]** Where possible, the system should provide a weekly health summary via Telegram (Sunday) showing: total sources active/disabled, average success rate, sectors with degraded coverage.

---

## Specifications

### File Structure

```
src/trading/news/
    __init__.py
    sources.py          # Module 1: Source catalog (42 sources)
    rss_fetcher.py      # Module 2: RSS fetcher (feedparser + asyncio)
    web_scraper.py      # Module 3: Web scraper (httpx + BeautifulSoup)
    normalizer.py       # Module 4: Content normalizer (Article dataclass)
    storage.py          # Module 5: DB storage (news_articles table)
    context_builder.py  # Module 6: Sector context .md generator
    health.py           # Module 7: Health monitoring
    crawler.py          # Orchestrator: coordinates Modules 2-5
```

### Database Migration

New migration file: `src/trading/db/migrations/013_news_articles.sql`

### Configuration

Feature flag in `src/trading/config.py`:
```python
NEWS_CRAWLING_V2_ENABLED: bool = True  # Toggle new 42-source system
NEWS_HTTP_TIMEOUT: float = 15.0
NEWS_MAX_CONCURRENCY: int = 10
NEWS_RATE_LIMIT_SECONDS: float = 1.0
NEWS_RETENTION_DAYS: int = 90
NEWS_MAX_SUMMARY_LENGTH: int = 500
```

### Integration Points

1. **Cron schedule** (existing `src/trading/scheduler/daily.py`):
   - 05:45 — `crawl_all_news()` (new, before existing 06:00 context build)
   - 06:00 — `build_macro_context()` (unchanged)
   - 06:45 — `build_micro_news()` (upgraded to use `news_articles` table)
   - Friday 16:30 — `build_macro_news()` (upgraded to use `news_articles` table)

2. **CLI** (existing `src/trading/cli.py`):
   - `trading crawl-news [--sector SECTOR] [--source NAME] [--force]`
   - `trading news-health`

3. **Persona context injection** (SPEC-007 mechanism unchanged):
   - `macro_news.md` -> Macro Persona input
   - `micro_news.md` -> Micro Persona input

### Replacement Strategy

The existing `src/trading/contexts/rss_feeds.py` is **fully replaced** by this SPEC:
- All 12 original feeds are absorbed into the new 42-source catalog
- The `Feed` dataclass is replaced by `NewsSource`
- The `all_news_feeds()` / `tier4_only()` functions are replaced by sector-based accessors
- Old tier system (1-4) is replaced by sector categorization

### Dependencies

Add to `pyproject.toml` (if not present):
- `beautifulsoup4 >= 4.12`
- `lxml >= 5.0` (parser backend for BeautifulSoup)
- `feedparser >= 6.0` (already present)

---

## Traceability

| Requirement | Acceptance Test | Implementation |
|---|---|---|
| REQ-NEWS-01-* | AT-01: Source catalog validation | `src/trading/news/sources.py` |
| REQ-NEWS-02-* | AT-02: RSS fetch integration test | `src/trading/news/rss_fetcher.py` |
| REQ-NEWS-03-* | AT-03: Web scraper integration test | `src/trading/news/web_scraper.py` |
| REQ-NEWS-04-* | AT-04: Normalizer unit test | `src/trading/news/normalizer.py` |
| REQ-NEWS-05-* | AT-05: Storage + dedup test | `src/trading/news/storage.py` |
| REQ-NEWS-06-* | AT-06: Context builder integration test | `src/trading/news/context_builder.py` |
| REQ-NEWS-07-* | AT-07: Health monitoring test | `src/trading/news/health.py` |
