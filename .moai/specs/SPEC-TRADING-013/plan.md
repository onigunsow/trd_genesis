# Execution Plan: SPEC-TRADING-013 — Global + Sector News Crawling Infrastructure

Created: 2026-05-05
SPEC Version: 0.1.0
Development Mode: DDD (ANALYZE-PRESERVE-IMPROVE)
Agent: manager-strategy

---

## 1. Plan Summary

Replace the existing 12-feed tier-based `rss_feeds.py` with a comprehensive 42-source sector-based news crawling infrastructure spanning 7 modules. The new system introduces parallel async RSS fetching, httpx+BeautifulSoup web scraping, PostgreSQL-backed article storage with deduplication, sector-aware context file generation, and per-source health monitoring with Telegram alerts.

Key architectural change: Data flows from direct-fetch-per-request to crawl-store-query pattern. Context files (`macro_news.md`, `micro_news.md`) are now generated from a DB table rather than fetched live.

---

## 2. Critical Findings from Codebase Analysis

### Migration Number Conflict

The SPEC references `013_news_articles.sql`, but migration `013_event_car_atr.sql` already exists (SPEC-TRADING-012). The correct migration filename is `014_news_articles.sql`.

### Dependency Gaps

| Package | pyproject.toml | uv.lock | Action |
|---------|---------------|---------|--------|
| `beautifulsoup4` | Missing | Present (4.14.3, transitive) | Add explicitly: `"beautifulsoup4>=4.12"` |
| `lxml` | Missing | Missing | Add: `"lxml>=5.0"` |
| `feedparser` | Present (>=6.0) | Present | No action |
| `httpx` | Present (>=0.28) | Present | No action |

### Scheduler Integration Point

Current pattern in `src/trading/scheduler/runner.py` (line 62-77):
- 06:00: `build_macro_context` (daily, all days)
- 06:30: `build_micro_context` (daily, all days)
- 06:45: `build_micro_news` (weekdays only)
- Fri 16:30: `build_macro_news` (weekly)

New job inserts at 05:45 (before all existing context jobs).

### Preservation Requirements

1. `data/contexts/macro_news.md` path unchanged (persona injection point)
2. `data/contexts/micro_news.md` path unchanged (persona injection point)
3. Existing 06:00/06:30/06:45/Fri-16:30 cron schedule unmodified
4. `audit_log` table pattern maintained (same `audit()` function)
5. Telegram alert pattern maintained (same `system_briefing()` function)
6. Feature flag enables instant rollback to SPEC-007 behavior

---

## 3. Task Decomposition

### TASK-001: Source Catalog (`src/trading/news/sources.py`)

**Description**: Create the 42-source catalog as frozen dataclasses with sector tagging and helper functions.

**Requirement Mapping**: REQ-NEWS-01-1 through REQ-NEWS-01-6

**Dependencies**: None (standalone module)

**Acceptance Criteria**:
- AC-1-1 through AC-1-8 pass
- `NewsSource` frozen dataclass with all specified fields
- 12 sector constants defined
- 42 sources catalogued (31 RSS + 11 web)
- Helper functions return correct filtered subsets
- Feature flag toggle between v2 and legacy catalog

**Effort**: S (Small)

---

### TASK-002: Rate Limiter (shared utility)

**Description**: Implement per-domain rate limiter using asyncio.Lock + timestamp tracking. Shared between RSS fetcher and web scraper.

**Requirement Mapping**: REQ-NEWS-02-5, REQ-NEWS-03-5

**Dependencies**: None (utility module)

**Acceptance Criteria**:
- AC-2-3, AC-3-4, AC-INT-5 pass
- Enforces 1-second minimum between requests to same domain
- Thread-safe via asyncio.Lock per domain
- Domain extracted from URL (not full URL)

**Effort**: S (Small)

---

### TASK-003: RSS Fetcher (`src/trading/news/rss_fetcher.py`)

**Description**: Async RSS fetcher processing 31 feeds with parallel execution, concurrency limit, feedparser+fallback parsing.

**Requirement Mapping**: REQ-NEWS-02-1 through REQ-NEWS-02-8

**Dependencies**: TASK-001 (source catalog), TASK-002 (rate limiter)

**Acceptance Criteria**:
- AC-2-1 through AC-2-8 pass
- `asyncio.gather` with Semaphore(10) for concurrency
- feedparser primary, xml.etree.ElementTree fallback
- 15-second HTTP timeout with graceful skip
- User-Agent header set correctly
- Failure counter incremented per source on error

**Effort**: M (Medium)

---

### TASK-004: Web Scraper (`src/trading/news/web_scraper.py`)

**Description**: Web scraper for 11 sites using httpx+BeautifulSoup with per-site CSS selector rules.

**Requirement Mapping**: REQ-NEWS-03-1 through REQ-NEWS-03-7

**Dependencies**: TASK-001 (source catalog), TASK-002 (rate limiter)

**Acceptance Criteria**:
- AC-3-1 through AC-3-7 pass
- `ScrapeRule` dataclass registry for 11 sites
- Absolute URL resolution for relative links
- Structure change detection (zero-result CSS selectors)
- No Playwright/Selenium imports
- Per-site encoding support (utf-8, euc-kr)

**Effort**: M (Medium)

---

### TASK-005: Content Normalizer (`src/trading/news/normalizer.py`)

**Description**: Unified Article dataclass from both RSS and web scraper outputs, with deduplication and title normalization.

**Requirement Mapping**: REQ-NEWS-04-1 through REQ-NEWS-04-6

**Dependencies**: TASK-003 (RSS fetcher output), TASK-004 (web scraper output)

**Acceptance Criteria**:
- AC-4-1 through AC-4-8 pass
- `Article` dataclass with all specified fields
- SHA-256 content hash of normalized title
- Within-batch deduplication by hash
- Date inference when published_at unavailable
- Summary truncation at word boundary (max 500 chars)
- Title normalization (whitespace, encoding artifacts)

**Effort**: S (Small)

---

### TASK-006: DB Migration + Storage (`src/trading/news/storage.py`)

**Description**: Create `news_articles` and `news_source_health` tables, implement bulk insert with ON CONFLICT dedup, retention cleanup.

**Requirement Mapping**: REQ-NEWS-05-1 through REQ-NEWS-05-4, REQ-NEWS-07-5

**Dependencies**: TASK-005 (Article dataclass)

**Acceptance Criteria**:
- AC-5-1, AC-5-2, AC-5-3, AC-5-5, AC-5-10, AC-INT-6 pass
- Migration file: `014_news_articles.sql` (not 013 — conflict resolved)
- `news_articles` table with UNIQUE on content_hash + 3 indexes
- `news_source_health` table (created in same migration)
- `ON CONFLICT (content_hash) DO NOTHING` for idempotent inserts
- Single transaction per crawl batch
- 90-day retention DELETE job

**Effort**: M (Medium)

---

### TASK-007: Crawler Orchestrator + Cron + CLI

**Description**: Coordinate Modules 2-5 into unified crawl cycle. Integrate with APScheduler (05:45 daily) and CLI commands.

**Requirement Mapping**: REQ-NEWS-05-3, REQ-NEWS-05-5, REQ-NEWS-05-6

**Dependencies**: TASK-003, TASK-004, TASK-005, TASK-006

**Acceptance Criteria**:
- AC-5-4, AC-5-6, AC-5-7, AC-5-8, AC-5-9, AC-INT-1, AC-INT-3, AC-INT-4, AC-INT-7 pass
- `CrawlOrchestrator` class: fetch -> normalize -> deduplicate -> store
- Feature flag check at entry point
- `audit_log` entry with crawl statistics
- Cron job at 05:45 in `scheduler/runner.py`
- CLI: `trading crawl-news [--sector] [--source] [--force]`

**Effort**: M (Medium)

---

### TASK-008: Sector Context Builder (`src/trading/news/context_builder.py`)

**Description**: Generate `macro_news.md` and `micro_news.md` from DB queries instead of live RSS fetch. Ticker-to-sector mapping.

**Requirement Mapping**: REQ-NEWS-06-1 through REQ-NEWS-06-7

**Dependencies**: TASK-006 (storage layer), TASK-007 (crawler populates data)

**Acceptance Criteria**:
- AC-6-1 through AC-6-8 pass
- `macro_news.md`: sectors (macro_economy, finance_banking, energy_commodities), 7 days, max 50 headlines, English prioritized
- `micro_news.md`: watchlist ticker sectors, 3 days, max 30/sector, Korean prioritized
- Format: `## [Sector] (N articles, YYYY-MM-DD HH:MM KST)` + bullet list
- Empty watchlist -> full coverage mode
- Unknown ticker -> defaults to `stock_market`
- Failed sector -> `[DATA UNAVAILABLE]`
- No LLM calls (raw headlines only)

**Effort**: M (Medium)

---

### TASK-009: Health Monitoring (`src/trading/news/health.py`)

**Description**: Per-source health tracking, auto-disable at 7 failures, Telegram alerts at 3 failures, CLI display.

**Requirement Mapping**: REQ-NEWS-07-1 through REQ-NEWS-07-8

**Dependencies**: TASK-006 (health table), TASK-007 (crawler updates health)

**Acceptance Criteria**:
- AC-7-1 through AC-7-9 pass
- Health metrics: consecutive_failures, last_success, last_failure, totals
- 3 consecutive failures -> Telegram warning
- 7 consecutive failures -> auto-disable + critical alert
- Manual re-enable via `--force` flag resets counters
- CLI `trading news-health` formatted table
- Health state persisted in PostgreSQL (survives restart)
- Weekly summary Telegram (optional, Sunday)

**Effort**: M (Medium)

---

### TASK-010: Migration, Cleanup, and E2E Validation

**Description**: Remove old `rss_feeds.py`, update imports in existing modules, upgrade `build_macro_news.py` and `build_micro_news.py` to use new pipeline, validate feature flag rollback.

**Requirement Mapping**: Traceability (full system integration)

**Dependencies**: All previous tasks (TASK-001 through TASK-009)

**Acceptance Criteria**:
- AC-INT-1 through AC-INT-7 pass
- `src/trading/contexts/rss_feeds.py` deleted
- All imports referencing old module updated
- `build_macro_news.py` upgraded to query `news_articles`
- `build_micro_news.py` upgraded to include sector news from DB
- Feature flag `False` -> system falls back to old behavior
- E2E: crawl_all_news -> context generation -> files exist with correct format
- `pyproject.toml` updated with `beautifulsoup4>=4.12` and `lxml>=5.0`

**Effort**: M (Medium)

---

## 4. Implementation Phases

### Phase 1: Foundation Layer

**Tasks**: TASK-001, TASK-002, TASK-006 (migration only)
**Goal**: Establish catalog, rate limiter, and database schema
**DDD Cycle**:
- ANALYZE: Read existing `rss_feeds.py`, understand Feed dataclass and tier system
- PRESERVE: Ensure all 12 original feeds exist in new catalog (absorbed)
- IMPROVE: Expand to 42 sources with sector tagging

**Files Created/Modified**:
- `src/trading/news/__init__.py` (new)
- `src/trading/news/sources.py` (new)
- `src/trading/news/rate_limiter.py` (new)
- `src/trading/db/migrations/014_news_articles.sql` (new)
- `pyproject.toml` (add beautifulsoup4, lxml)

---

### Phase 2: Data Acquisition

**Tasks**: TASK-003, TASK-004
**Goal**: Implement both data fetchers (RSS + web)
**DDD Cycle**:
- ANALYZE: Read existing `_fetch_feed()` in `build_macro_news.py` (synchronous httpx+feedparser pattern)
- PRESERVE: Same feedparser parsing logic, same timeout/user-agent values
- IMPROVE: Async, parallel (semaphore 10), fallback parser, structured error handling

**Files Created/Modified**:
- `src/trading/news/rss_fetcher.py` (new)
- `src/trading/news/web_scraper.py` (new)

---

### Phase 3: Pipeline Core

**Tasks**: TASK-005, TASK-006 (storage layer)
**Goal**: Normalize articles and persist to database
**DDD Cycle**:
- ANALYZE: Read existing dedup logic in `build_macro_news.py` (title-based `seen_titles` set)
- PRESERVE: Deduplication concept (same title = same article)
- IMPROVE: SHA-256 hash, DB-level UNIQUE constraint, bulk transactional insert

**Files Created/Modified**:
- `src/trading/news/normalizer.py` (new)
- `src/trading/news/storage.py` (new)

---

### Phase 4: Orchestration and Integration

**Tasks**: TASK-007, TASK-008
**Goal**: Wire everything together with cron and context generation
**DDD Cycle**:
- ANALYZE: Read `scheduler/runner.py` add_job pattern, read context builder output format
- PRESERVE: Same `data/contexts/` output path, same cron schedule for existing jobs, same `audit()` call pattern
- IMPROVE: New 05:45 crawl job, CLI commands, DB-backed context generation

**Files Created/Modified**:
- `src/trading/news/crawler.py` (new)
- `src/trading/news/context_builder.py` (new)
- `src/trading/scheduler/runner.py` (modify: add 05:45 crawl job)
- `src/trading/cli.py` (modify: add `crawl-news` and `news-health` commands)
- `src/trading/config.py` (modify: add NEWS_* feature flags)

---

### Phase 5: Observability

**Tasks**: TASK-009
**Goal**: Health monitoring with auto-disable and alerts
**DDD Cycle**:
- ANALYZE: Read existing Tier 4 health check in `build_macro_news.py` (`_record_t4_health`)
- PRESERVE: Same Telegram alert pattern (`system_briefing`), same audit_log usage
- IMPROVE: Per-source tracking, auto-disable at 7 failures, formatted CLI table, weekly summary

**Files Created/Modified**:
- `src/trading/news/health.py` (new)

---

### Phase 6: Migration and Validation

**Tasks**: TASK-010
**Goal**: Remove old module, upgrade existing builders, validate rollback
**DDD Cycle**:
- ANALYZE: Identify all import references to `trading.contexts.rss_feeds`
- PRESERVE: Feature flag ensures rollback path works (v2=False -> old behavior)
- IMPROVE: Clean removal of deprecated module, upgraded context builders

**Files Deleted/Modified**:
- `src/trading/contexts/rss_feeds.py` (delete)
- `src/trading/contexts/build_macro_news.py` (modify: use new pipeline when v2 enabled)
- `src/trading/contexts/build_micro_news.py` (modify: add sector news section from DB)

---

## 5. TAG Chain

```
TAG-001 [Source Catalog + Migration]
    |
    +-----> TAG-002 [RSS Fetcher]
    |                |
    +-----> TAG-003 [Web Scraper]
    |                |
    |       TAG-004 [Normalizer + Storage]  <-- depends on TAG-002, TAG-003
    |                |
    +-----> TAG-005 [Crawler + Cron + CLI]  <-- depends on TAG-004
                     |
            +--------+--------+
            |                 |
    TAG-006 [Context Builder]  TAG-007 [Health Monitoring]
            |                 |
            +--------+--------+
                     |
            TAG-008 [Migration + E2E]
```

Dependency summary:
- TAG-001 -> TAG-002, TAG-003 (parallel)
- TAG-002, TAG-003 -> TAG-004
- TAG-004 -> TAG-005
- TAG-005 -> TAG-006, TAG-007 (parallel)
- TAG-006, TAG-007 -> TAG-008

---

## 6. Technology Stack

### New Dependencies

| Library | Version | Purpose | Selection Rationale |
|---------|---------|---------|-------------------|
| `beautifulsoup4` | >=4.12 | HTML parsing for web scraper | LTS, no-JS requirement, verified in SPEC |
| `lxml` | >=5.0 | Fast XML/HTML parser backend | C-extension performance, BeautifulSoup recommended |

### Existing Dependencies (no change)

| Library | Current | Used For |
|---------|---------|----------|
| `httpx` | >=0.28 | HTTP client (async) |
| `feedparser` | >=6.0 | RSS XML parsing |
| `apscheduler` | >=3.11 | Cron scheduling |
| `psycopg[binary]` | >=3.2 | PostgreSQL driver |
| `structlog` | >=25.1 | Structured logging |

### Configuration Additions (`src/trading/config.py`)

```python
NEWS_CRAWLING_V2_ENABLED: bool = True
NEWS_HTTP_TIMEOUT: float = 15.0
NEWS_MAX_CONCURRENCY: int = 10
NEWS_RATE_LIMIT_SECONDS: float = 1.0
NEWS_RETENTION_DAYS: int = 90
NEWS_MAX_SUMMARY_LENGTH: int = 500
```

---

## 7. Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|-----------|
| Web source HTML structure changes | Medium | Medium | Health monitoring detects 0-results, Telegram alert, per-site selector updates |
| Migration number 013 already taken | Confirmed | High | Resolved: use `014_news_articles.sql` |
| `beautifulsoup4` not in pyproject.toml | Confirmed | Medium | Add explicitly to dependencies |
| Rate limiting insufficient for some sources | Low | Low | 1s delay + polite User-Agent; monitor via health |
| Large context file bloats persona token budget | Low | Medium | Hard caps: 50 headlines (macro), 30/sector (micro) |
| Rollback needed after deployment | Low | High | Feature flag `NEWS_CRAWLING_V2_ENABLED=False` restores old behavior |
| Existing cron jobs disrupted | Low | High | New job at 05:45 (15 min before any existing job); no modification to existing jobs |
| DB transaction timeout on bulk insert | Very Low | Low | ~840 rows/day max; trivial for Postgres |

---

## 8. Effort Estimate

| Task | Effort | Lines (est.) | Complexity |
|------|--------|-------------|-----------|
| TASK-001: Source Catalog | S | ~200 | Low (data entry + frozen dataclass) |
| TASK-002: Rate Limiter | S | ~50 | Low (asyncio.Lock + dict) |
| TASK-003: RSS Fetcher | M | ~150 | Medium (async, concurrency, fallback) |
| TASK-004: Web Scraper | M | ~180 | Medium (per-site rules, encoding) |
| TASK-005: Normalizer | S | ~100 | Low (dataclass, hash, truncation) |
| TASK-006: Storage + Migration | M | ~120 | Medium (SQL, transactions, ON CONFLICT) |
| TASK-007: Crawler + Cron + CLI | M | ~150 | Medium (orchestration, integration) |
| TASK-008: Context Builder | M | ~150 | Medium (DB queries, format, ticker mapping) |
| TASK-009: Health Monitoring | M | ~180 | Medium (state machine, alerts, CLI) |
| TASK-010: Migration + E2E | M | ~100 | Medium (import cleanup, flag validation) |

**Total estimated effort**: ~1,380 lines of new/modified code across 10 tasks.
**Total estimated time**: 6-8 DDD cycles (one task per cycle).

---

## 9. Expert Delegation Recommendations

**Primary: expert-backend**
- Async crawling concurrency model (asyncio.gather + Semaphore)
- Database schema optimization (indexes, ON CONFLICT pattern)
- Rate limiter implementation (per-domain Lock pattern)
- Transaction handling (bulk insert atomicity)
- CLI integration (typer/click command registration)

**Optional: expert-devops**
- Docker container outbound HTTPS to 42 domains (no firewall issues expected, but verify)
- APScheduler reliability within container lifecycle

---

## 10. Handover to manager-ddd

Upon approval, the following context transfers to manager-ddd:

- **TAG chain**: TAG-001 through TAG-008 with dependency graph above
- **Library versions**: beautifulsoup4>=4.12, lxml>=5.0 (add to pyproject.toml)
- **Key decisions**:
  - Migration file is `014_news_articles.sql` (not 013)
  - Shared rate limiter between RSS and web modules
  - Feature flag at orchestrator entry point for rollback
  - No LLM calls in context builder (raw headlines only)
  - DDD: preserve existing context file paths and cron schedule
- **Task list**: TASK-001 through TASK-010 with dependencies and acceptance criteria
