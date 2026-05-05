---
id: SPEC-TRADING-013
type: research
created: 2026-05-05
---

# SPEC-TRADING-013 Research — Global + Sector News Crawling Infrastructure

## Implementation Plan

### Milestone 1 (Primary Goal): Source Catalog + RSS Fetcher

**Scope**: Modules 1-2 — Replace `rss_feeds.py` with the new 42-source catalog and implement RSS fetching for 31 RSS feeds.

**Deliverables**:
- `src/trading/news/__init__.py`
- `src/trading/news/sources.py` — 42-source catalog with sector tagging
- `src/trading/news/rss_fetcher.py` — async RSS fetcher with rate limiting
- Unit tests for source catalog accessors
- Integration tests for RSS fetcher (mock HTTP + real format validation)

**Technical Approach**:
- Frozen dataclass `NewsSource` with explicit `Literal` types for sector/language
- `feedparser` as primary parser, `xml.etree.ElementTree` as fallback
- `httpx.AsyncClient` with connection pooling (max 10 connections)
- Domain-based rate limiter using `asyncio.Lock` per domain + timestamp tracking
- User-Agent header: `trading-bot/0.2 (personal use; non-commercial)`

**Dependencies**: None (standalone module, no DB required for catalog)

---

### Milestone 2 (Primary Goal): Web Scraper + Normalizer

**Scope**: Modules 3-4 — Web scraping for 11 sites + unified Article normalization.

**Deliverables**:
- `src/trading/news/web_scraper.py` — per-site CSS selector rules
- `src/trading/news/normalizer.py` — Article dataclass + dedup + title normalization
- `ScrapeRule` registry for 11 web sources
- Unit tests for normalizer (dedup, hash, truncation)
- Integration tests for web scraper (mock HTML responses)

**Technical Approach**:
- `ScrapeRule` dataclass per site with CSS selectors for headline/link/date
- BeautifulSoup + lxml for HTML parsing
- SHA-256 hash of normalized title for deduplication
- Summary truncation at word boundaries (max 500 chars)
- Same rate limiter shared with RSS fetcher (per-domain)

**Web Source Extraction Rules** (initial selectors, subject to health monitoring):

| Source | Headline Selector | Link Selector |
|---|---|---|
| bok.or.kr | `.bbs-list td.title a` | same element href |
| finance.naver.com/news | `.newsMainListBox li a` | same element href |
| finance.naver.com/research | `.research_list_main a` | same element href |
| thelec.kr | `.post-title a` | same element href |
| biotimes.co.kr | `.td-module-title a` | same element href |
| energy-news.co.kr | `.td-module-title a` | same element href |
| aitimes.kr | `.td-module-title a` | same element href |
| fntimes.com | `.article-list a` | same element href |
| snmnews.com | `.article-list a` | same element href |

Note: Exact selectors will be validated during implementation and adjusted per health monitoring.

**Dependencies**: Milestone 1 (source catalog)

---

### Milestone 3 (Primary Goal): Storage + DB Migration

**Scope**: Module 5 — Database table, article persistence, dedup, retention.

**Deliverables**:
- `src/trading/db/migrations/013_news_articles.sql`
- `src/trading/news/storage.py` — bulk insert, dedup, retention cleanup
- Integration tests with real Postgres

**Technical Approach**:
- Single `news_articles` table with `content_hash` UNIQUE constraint
- `ON CONFLICT (content_hash) DO NOTHING` for idempotent inserts
- Batch insert using `executemany` within a single transaction
- Daily retention cleanup via scheduled job (DELETE WHERE crawled_at < 90 days)
- `news_source_health` table for Module 7 (created in same migration)
- Indexes on (sector, published_at DESC) and (language) for efficient context queries

**Dependencies**: Milestone 2 (normalizer produces Article dataclass)

---

### Milestone 4 (Secondary Goal): Crawler Orchestrator + Cron Integration

**Scope**: Orchestrate Modules 2-5 into a unified crawl cycle and integrate with existing cron.

**Deliverables**:
- `src/trading/news/crawler.py` — orchestrates fetch -> normalize -> store
- Cron integration: 05:45 daily crawl
- CLI command: `trading crawl-news`
- `audit_log` entries for crawl statistics

**Technical Approach**:
- `CrawlOrchestrator` class coordinates: fetch all sources -> normalize -> deduplicate -> store
- Parallel RSS fetch (asyncio.gather, max 10 concurrent)
- Sequential web scrape (rate limited, 11 sites)
- Single DB transaction for all inserts per cycle
- Statistics: total_fetched, new_inserted, duplicates_skipped, sources_failed, duration
- Feature flag check at entry point (`NEWS_CRAWLING_V2_ENABLED`)

**Cron Integration** (modify `src/trading/scheduler/daily.py`):
```python
# Add before existing 06:00 job
scheduler.add_job(crawl_all_news, CronTrigger(hour=5, minute=45), id="crawl_news")
```

**CLI Integration** (modify `src/trading/cli.py`):
```python
@app.command()
def crawl_news(sector: str = None, source: str = None, force: bool = False):
    """Crawl news sources (all or filtered by sector/source)."""
```

**Dependencies**: Milestones 1-3

---

### Milestone 5 (Secondary Goal): Sector Context Builder

**Scope**: Module 6 — Generate `macro_news.md` and `micro_news.md` from `news_articles` table.

**Deliverables**:
- `src/trading/news/context_builder.py` — sector-aware .md generation
- Upgrade existing `build_macro_news()` and `build_micro_news()` functions
- Ticker-to-sector mapping utility
- Integration tests with DB fixtures

**Technical Approach**:
- `macro_news.md`: Query `news_articles` WHERE sector IN (macro_economy, finance_banking, energy_commodities) AND published_at > 7 days ago, LIMIT 50, ORDER BY published_at DESC
- `micro_news.md`: Load watchlist tickers -> map to sectors -> query `news_articles` for those sectors, last 3 days, LIMIT 30 per sector
- No LLM calls — pure DB query + markdown formatting
- Ticker-to-sector mapping: hardcoded initial mapping + future pykrx sector lookup
- Format: `## [Sector] (N articles, YYYY-MM-DD HH:MM KST)\n- [Title] | Source | Date`

**Persona Integration**:
- `macro_news.md` -> injected into Macro Persona input (weekly, SPEC-007 REQ-CTX-01-5)
- `micro_news.md` -> injected into Micro Persona input (daily, SPEC-007 REQ-CTX-01-4)

**Dependencies**: Milestone 4 (crawler must populate news_articles)

---

### Milestone 6 (Final Goal): Health Monitoring

**Scope**: Module 7 — Per-source health tracking, auto-disable, Telegram alerts.

**Deliverables**:
- `src/trading/news/health.py` — health tracker + alert logic
- CLI command: `trading news-health`
- Telegram alert integration
- Weekly health summary (optional)

**Technical Approach**:
- `news_source_health` table tracks per-source metrics
- After each crawl cycle: update health counters for all sources
- Alert thresholds: 3 consecutive failures (warning), 7 consecutive (auto-disable)
- Auto-disable sets `enabled = False` in health table; crawler skips disabled sources
- Manual re-enable via `trading crawl-news --source NAME --force`
- `trading news-health` CLI shows formatted table (name, sector, status, success rate, last activity)

**Dependencies**: Milestones 3-4 (DB table + crawler integration)

---

### Milestone 7 (Final Goal): Migration + Cleanup

**Scope**: Remove old `rss_feeds.py`, update all imports, documentation.

**Deliverables**:
- Delete `src/trading/contexts/rss_feeds.py`
- Update all imports referencing old module
- Update SPEC-007 context builders to use new `news_articles` data
- Feature flag validation: ensure rollback path works
- End-to-end integration test: full crawl -> context generation -> persona injection

**Dependencies**: All previous milestones

---

## Technical Approach

### Architecture Decision: Sector-Based vs. Tier-Based

**Decision**: Replace tier-based categorization (Tier 1-4) with sector-based categorization (12 sectors).

**Rationale**:
- Tiers represent reliability levels, not content domains — unreliable for persona context routing
- Sectors directly map to watchlist tickers, enabling targeted micro_news.md generation
- Persona system (Micro Persona) analyzes stocks by sector, matching news to analysis domain
- 12 sectors cover all major Korean stock market segments

### Architecture Decision: No LLM in Context Builder

**Decision**: Context builder generates raw headline lists, not LLM summaries.

**Rationale**:
- SPEC-007 REQ-CTX-01-5 already defines a weekly LLM summary step (Friday 16:30)
- Daily context should be cost-free (no API calls)
- Personas receive raw headlines and perform their own analysis — this matches the "data in, analysis out" pattern
- Token budget: 50 headlines x ~20 tokens each = ~1000 tokens — minimal injection cost

### Architecture Decision: httpx + BeautifulSoup (No Playwright)

**Decision**: Use httpx + BeautifulSoup for all web scraping.

**Rationale**:
- All 11 web sources verified accessible via httpx without JavaScript rendering
- Playwright adds ~200MB Docker image bloat + complexity
- BeautifulSoup handles all target sites' server-rendered HTML
- If a source migrates to SPA in the future, health monitoring will detect and alert

### Rate Limiting Strategy

**Implementation**: Per-domain asyncio semaphore + timestamp tracking.

```
Domain registry (in-memory dict):
  domain -> (last_request_time, asyncio.Lock)

Before each request:
  1. Acquire domain lock
  2. Check time since last request
  3. If < 1 second, sleep(remaining)
  4. Execute request
  5. Update last_request_time
  6. Release domain lock
```

This ensures politeness across concurrent requests to the same domain.

### Concurrency Model

```
crawl_all_news():
    1. Load enabled sources from catalog + health table
    2. Group by source_type:
       - RSS sources (31): asyncio.gather with Semaphore(10)
       - WEB sources (11): sequential with rate limiting
    3. Normalize all results -> List[Article]
    4. Deduplicate by content_hash
    5. Bulk insert to DB (single transaction)
    6. Update health counters
    7. Log statistics to audit_log
```

### Error Handling Strategy

| Error Type | Action | Alert |
|---|---|---|
| HTTP timeout (>15s) | Skip source, increment failure | After 3 consecutive |
| Non-2xx response | Skip source, increment failure | After 3 consecutive |
| XML parse error | Try xml.etree fallback, then skip | After 3 consecutive |
| HTML selector miss (0 results) | Log structure_change, skip | Immediate (structure change) |
| DB insert error | Rollback batch, retry once | Immediate |
| All sources in sector fail | Mark sector "[DATA UNAVAILABLE]" | Immediate |

---

## Verified Source Catalog (42 Sources)

### Macro/Economy (10 sources)
| # | Name | URL | Type | Lang |
|---|---|---|---|---|
| 1 | Fed Press Releases | https://www.federalreserve.gov/feeds/press_all.xml | RSS | en |
| 2 | ECB Press | https://www.ecb.europa.eu/rss/press.html | RSS | en |
| 3 | CNBC Economy | https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258 | RSS | en |
| 4 | CNBC Markets | https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839135 | RSS | en |
| 5 | Reuters Markets (GNews) | https://news.google.com/rss/search?q=site:reuters.com+markets+economy&hl=en | RSS | en |
| 6 | FN Economy | https://www.fnnews.com/rss/r20/fn_realnews_economy.xml | RSS | ko |
| 7 | Yonhap Economy | https://www.yna.co.kr/rss/economy.xml | RSS | ko |
| 8 | Hankyung Real-time | https://www.hankyung.com/feed/all-news | RSS | ko |
| 9 | Seoul Economy | https://www.sedaily.com/Rss/Economy | RSS | ko |
| 10 | Bank of Korea Press | https://www.bok.or.kr/portal/bbs/P0000559/list.do?menuNo=200761 | WEB | ko |

### Stock Market (5 sources)
| # | Name | URL | Type | Lang |
|---|---|---|---|---|
| 11 | FN Stock | https://www.fnnews.com/rss/r20/fn_realnews_stock.xml | RSS | ko |
| 12 | FN Finance | https://www.fnnews.com/rss/r20/fn_realnews_finance.xml | RSS | ko |
| 13 | Maeil Securities | https://www.mk.co.kr/rss/30100041/ | RSS | ko |
| 14 | Naver Finance News | https://finance.naver.com/news/mainnews.naver | WEB | ko |
| 15 | Naver Finance Research | https://finance.naver.com/research/ | WEB | ko |

### Semiconductor (3 sources)
| # | Name | URL | Type | Lang |
|---|---|---|---|---|
| 16 | SemiAnalysis | https://newsletter.semianalysis.com/feed | RSS | en |
| 17 | SemiEngineering | https://semiengineering.com/feed/ | RSS | en |
| 18 | The Elec | https://www.thelec.kr/ | WEB | ko |

### Biotech/Pharma (4 sources)
| # | Name | URL | Type | Lang |
|---|---|---|---|---|
| 19 | FierceBiotech | https://www.fiercebiotech.com/rss/xml | RSS | en |
| 20 | BioPharma Dive | https://www.biopharmadive.com/feeds/news/ | RSS | en |
| 21 | Endpoints News | https://endpoints.news/feed/ | RSS | en |
| 22 | BioTimes | https://www.biotimes.co.kr/ | WEB | ko |

### Energy/Commodities (4 sources)
| # | Name | URL | Type | Lang |
|---|---|---|---|---|
| 23 | OilPrice | https://oilprice.com/rss/main | RSS | en |
| 24 | Rigzone | https://www.rigzone.com/news/rss/rigzone_latest.aspx | RSS | en |
| 25 | EIA Today in Energy | https://www.eia.gov/rss/todayinenergy.xml | RSS | en |
| 26 | Energy News Korea | https://www.energy-news.co.kr/ | WEB | ko |

### IT/AI (4 sources)
| # | Name | URL | Type | Lang |
|---|---|---|---|---|
| 27 | TechCrunch | https://techcrunch.com/feed/ | RSS | en |
| 28 | Ars Technica | https://feeds.arstechnica.com/arstechnica/technology-lab | RSS | en |
| 29 | The Verge | https://www.theverge.com/rss/index.xml | RSS | en |
| 30 | AI Times Korea | https://www.aitimes.kr/ | WEB | ko |

### Finance/Banking (2 sources)
| # | Name | URL | Type | Lang |
|---|---|---|---|---|
| 31 | FT Markets (GNews) | https://news.google.com/rss/search?q=site:ft.com+markets&hl=en | RSS | en |
| 32 | Korea Financial News | https://www.fntimes.com/ | WEB | ko |

### Auto/EV/Battery (2 sources)
| # | Name | URL | Type | Lang |
|---|---|---|---|---|
| 33 | Electrek | https://electrek.co/feed/ | RSS | en |
| 34 | InsideEVs | https://insideevs.com/rss/news/all/ | RSS | en |

### Steel/Materials (1 source)
| # | Name | URL | Type | Lang |
|---|---|---|---|---|
| 35 | Steel Metal News | https://www.snmnews.com/ | WEB | ko |

### Retail/Consumer (2 sources)
| # | Name | URL | Type | Lang |
|---|---|---|---|---|
| 36 | Retail Dive | https://www.retaildive.com/feeds/news/ | RSS | en |
| 37 | Food Dive | https://www.fooddive.com/feeds/news/ | RSS | en |

### Gaming/Entertainment (3 sources)
| # | Name | URL | Type | Lang |
|---|---|---|---|---|
| 38 | GamesIndustry.biz | https://www.gamesindustry.biz/feed | RSS | en |
| 39 | IGN | https://feeds.feedburner.com/ign/all | RSS | en |
| 40 | Variety | https://variety.com/feed/ | RSS | en |

### Defense/Aerospace (1 source)
| # | Name | URL | Type | Lang |
|---|---|---|---|---|
| 41 | Defense News | https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml | RSS | en |

### Source Not Yet Assigned (1 source — counted separately in Stock Market)
| # | Name | URL | Type | Lang | Notes |
|---|---|---|---|---|---|
| 42 | Naver Finance Research | (see #15) | WEB | ko | Already counted in Stock Market |

**Summary**: 31 RSS + 11 WEB = 42 total (verified 2026-05-05 from Docker container via httpx)

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Web source HTML structure changes | Medium | Medium | Health monitoring detects (0 results), Telegram alert, manual selector update |
| Google News RSS rate limiting | Low | Low | 1s delay, polite User-Agent, low frequency (1x/day) |
| Source permanently shuts down | Low | Low | Auto-disable after 7 failures, sector has multiple sources |
| BeautifulSoup selector breaks | Medium | Low | Per-site rules, health check, fallback to other sources in sector |
| Large crawl volume overwhelms DB | Low | Low | 42 sources x ~20 articles = ~840 rows/day, trivial for Postgres |
| Duplicate detection false positive | Very Low | Low | SHA-256 collision probability negligible for article titles |
| Token budget increase from larger context | Low | Medium | Context builder caps at 50 headlines (macro) / 30 per sector (micro) |

---

## Expert Consultation Recommendations

**Backend Expert (expert-backend)**: Recommended for architecture review of:
- Async crawling concurrency model
- Database schema optimization (indexes, partitioning for 90-day retention)
- Rate limiter implementation pattern
- Error recovery and transaction handling

**DevOps Expert (expert-devops)**: Optional for:
- Docker container networking implications (outbound HTTPS to 42 domains)
- Cron scheduling reliability within apscheduler
