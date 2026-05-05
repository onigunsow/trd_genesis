---
id: SPEC-TRADING-014
version: 0.1.0
status: draft
created: 2026-05-05
updated: 2026-05-05
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "News Intelligence Analysis Pipeline"
related_specs:
  - SPEC-TRADING-013
  - SPEC-TRADING-010
  - SPEC-TRADING-007
  - SPEC-TRADING-001
---

# SPEC-TRADING-014 — News Intelligence Analysis Pipeline

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-05 | 0.1.0 | Initial draft — 6 modules, Haiku 4.5 LLM analysis, cron-based intelligence pipeline | onigunsow |

## Scope Summary

SPEC-TRADING-013 provides raw news crawling from 42 sources into `news_articles` DB. The current `context_builder.py` generates **headline-only** `.md` files with no summarization, impact scoring, or story clustering. This SPEC transforms raw articles into **actionable intelligence reports** using Claude Haiku 4.5 for cost-efficient LLM analysis.

Key transformation: Headlines-only format → Intelligence reports with:
- **2-line summaries** per article (LLM-generated)
- **Impact scoring** (1-5 scale)
- **Story clustering** (multiple sources covering same event = 1 story)
- **Portfolio relevance tagging** ([투자 주목] for high-impact stories matching holdings)
- **Trend tracking** (keyword frequency, sector sentiment, rising/falling topics)

This SPEC does NOT create an API service. It runs as **cron jobs** inside the existing Docker container, executing 10 minutes after each SPEC-013 crawl cycle.

### Cost Projection

| Item | Per-Run Cost | Daily Cost (6 runs) | Monthly Cost |
|---|---|---|---|
| Haiku batch calls (~10 articles/call) | ~0.5 KRW/call | ~150-300 KRW | ~5,000-9,000 KRW |
| Total SPEC-014 addition | — | — | **~20,000-30,000 KRW/month** |

---

## Environment

- Existing SPEC-TRADING-001 infrastructure — Postgres 16-alpine, Docker compose, Telegram, httpx
- Existing SPEC-TRADING-013 news crawling — 42 sources, `news_articles` DB table, 6 crawl cycles/day
- Existing SPEC-TRADING-010 Haiku routing — `claude-haiku-4-5` model available via Model Router
- Existing cron schedule: crawls at 08:00, 11:00, 14:30, 22:00, 01:00, 04:00 (SPEC-013)
- Existing `context_builder.py` — generates `macro_news.md` and `micro_news.md` (headline-only)
- Existing `TICKER_SECTOR_MAP` in `context_builder.py` — ticker-to-sector mapping
- New module: `src/trading/news/intelligence/` (6 sub-modules)
- New DB tables: `news_analysis`, `story_clusters`, `news_trends`
- Output files: `data/contexts/intelligence_macro.md`, `data/contexts/intelligence_micro.md`
- Dependencies: `anthropic` SDK (already present), no new external dependencies

## Assumptions

1. Claude Haiku 4.5 (`claude-haiku-4-5`) produces adequate quality for 2-line article summarization, impact scoring, keyword extraction, and sentiment classification. These are low-reasoning tasks ideal for Haiku.
2. Batch size of 10 articles per Haiku call balances cost efficiency (fewer calls) with context quality (articles fit within Haiku's context window with room for structured output).
3. TF-IDF or title similarity (cosine > 0.7 threshold) is sufficient for story clustering without requiring LLM involvement. Embedding-based clustering is deferred to future scope.
4. SPEC-013 crawl cycles consistently complete within 10 minutes, making a 10-minute offset reliable for analysis scheduling.
5. The `news_articles` table has a reliable `crawled_at` timestamp for incremental processing (fetch only articles since last analysis run).
6. Intelligence `.md` files overwriting each run is acceptable — personas read the latest snapshot, not historical accumulation.
7. The existing `get_static_context` tool (SPEC-009/010) can serve `intelligence_macro` and `intelligence_micro` as additional context sources without modification to the tool interface.
8. Monthly cost of ~20,000-30,000 KRW is acceptable within the SPEC-010 target of <= 100,000 KRW/month total system cost.

## Robustness Principles (SPEC-001 6 Principles Inherited)

This SPEC inherits SPEC-TRADING-001 v0.2.0's 6 Robustness Principles:

- **External dependency failure assumption (Principle 1)** — Haiku API failure: skip analysis for this cycle, retain previous intelligence files. DB failures: log and alert, no crash.
- **Silent failure prohibition (Principle 3)** — Every Haiku call failure, DB error, or scheduling anomaly generates `audit_log` entry + Telegram alert (batched).
- **Automatic recovery + human notification (Principle 4)** — Failed analysis runs auto-retry on next cycle. 3+ consecutive failures trigger Telegram alert.
- **Graceful degradation (Principle 6)** — If Haiku is unavailable, intelligence files retain last successful content (stale but present). Personas fall back to headline-only context from SPEC-013's `context_builder.py`.
- **State integrity via transactions (Principle 2)** — Analysis results use single DB transaction per batch. Partial failures do not corrupt existing analysis data.

---

## Requirements (EARS Format)

EARS notation: **U**=Ubiquitous, **E**=Event-driven, **S**=State-driven, **O**=Optional, **N**=Unwanted

---

### Module 1 — Article Analyzer (Haiku LLM)

**REQ-INTEL-01-1 [U]** The system shall implement an article analyzer at `src/trading/news/intelligence/analyzer.py` that processes unanalyzed articles from `news_articles` using Claude Haiku 4.5.

**REQ-INTEL-01-2 [U]** The article analyzer shall batch articles in groups of 10 per Haiku API call, sending article titles, summaries (if available), and body_text (first 1000 chars) for analysis.

**REQ-INTEL-01-3 [U]** For each article in a batch, the Haiku call shall produce:
- `summary_2line`: str — 2-line Korean summary of the article's key point and market implication
- `impact_score`: int (1-5) — market impact rating (1=negligible, 5=critical)
- `keywords`: list[str] — 3-5 Korean keywords extracted from content
- `sentiment`: Literal["positive", "neutral", "negative"] — overall market sentiment

**REQ-INTEL-01-4 [U]** The Haiku prompt shall instruct the model to analyze from a Korean stock market investor's perspective, focusing on market impact rather than general news value.

**REQ-INTEL-01-5 [U]** Analysis results shall be stored in a `news_analysis` table:

```sql
CREATE TABLE news_analysis (
    id BIGSERIAL PRIMARY KEY,
    article_id BIGINT NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
    summary_2line TEXT NOT NULL,
    impact_score SMALLINT NOT NULL CHECK (impact_score BETWEEN 1 AND 5),
    keywords TEXT[] NOT NULL,
    sentiment VARCHAR(10) NOT NULL CHECK (sentiment IN ('positive', 'neutral', 'negative')),
    analyzed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    model_used VARCHAR(50) NOT NULL DEFAULT 'claude-haiku-4-5',
    token_input INTEGER,
    token_output INTEGER,
    cost_krw REAL,
    UNIQUE(article_id)
);

CREATE INDEX idx_news_analysis_impact ON news_analysis (impact_score DESC);
CREATE INDEX idx_news_analysis_analyzed ON news_analysis (analyzed_at DESC);
CREATE INDEX idx_news_analysis_sentiment ON news_analysis (sentiment);
```

**REQ-INTEL-01-6 [E]** When an analysis run begins, the system shall query `news_articles` for articles where `id NOT IN (SELECT article_id FROM news_analysis)` AND `crawled_at > last_analysis_run_timestamp`, ordered by `published_at DESC`.

**REQ-INTEL-01-7 [S]** While the number of unanalyzed articles exceeds 100 in a single run, the system shall process only the most recent 100 articles (by `published_at`) and defer remaining to the next cycle to avoid Haiku timeout/cost spikes.

**REQ-INTEL-01-8 [E]** When a Haiku API call fails (timeout, rate limit, server error), the system shall retry once after 5 seconds. If the retry also fails, skip the batch and continue with remaining batches. Log failure to `audit_log`.

**REQ-INTEL-01-9 [U]** The system shall record per-run metrics to `audit_log`: articles_processed, batches_sent, haiku_calls_succeeded, haiku_calls_failed, total_input_tokens, total_output_tokens, total_cost_krw, duration_seconds.

**REQ-INTEL-01-10 [N]** The article analyzer shall NOT use any model other than Claude Haiku 4.5 for article analysis. Sonnet/Opus are reserved for decision-critical personas only.

---

### Module 2 — Story Clustering

**REQ-INTEL-02-1 [U]** The system shall implement story clustering at `src/trading/news/intelligence/clustering.py` that groups related articles covering the same event into story clusters.

**REQ-INTEL-02-2 [U]** Clustering shall use a combination of:
- Title similarity (normalized Levenshtein ratio > 0.6 OR)
- Keyword overlap (>= 2 shared keywords from `news_analysis.keywords`)
Both conditions evaluated within a 24-hour `published_at` window.

**REQ-INTEL-02-3 [U]** No LLM shall be used for clustering. The algorithm shall be purely computational (string similarity + keyword set intersection).

**REQ-INTEL-02-4 [U]** Clustering results shall be stored in a `story_clusters` table:

```sql
CREATE TABLE story_clusters (
    id BIGSERIAL PRIMARY KEY,
    representative_title TEXT NOT NULL,
    article_ids BIGINT[] NOT NULL,
    source_count INTEGER NOT NULL,
    impact_max SMALLINT NOT NULL,
    sector VARCHAR(50) NOT NULL,
    keywords TEXT[] NOT NULL,
    sentiment_dominant VARCHAR(10) NOT NULL,
    first_published TIMESTAMPTZ NOT NULL,
    last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cluster_date DATE NOT NULL DEFAULT CURRENT_DATE
);

CREATE INDEX idx_story_clusters_date ON story_clusters (cluster_date DESC);
CREATE INDEX idx_story_clusters_impact ON story_clusters (impact_max DESC);
CREATE INDEX idx_story_clusters_sector ON story_clusters (sector);
```

**REQ-INTEL-02-5 [U]** The `representative_title` shall be selected as the title from the highest-impact article within the cluster.

**REQ-INTEL-02-6 [U]** `source_count` shall reflect the number of distinct `source_name` values across articles in the cluster.

**REQ-INTEL-02-7 [U]** `impact_max` shall be the maximum `impact_score` among all articles in the cluster.

**REQ-INTEL-02-8 [U]** `sentiment_dominant` shall be the most frequent sentiment value among clustered articles.

**REQ-INTEL-02-9 [E]** When new analysis results are inserted (Module 1 completes), the clustering module shall re-cluster articles from the last 24 hours to incorporate new articles into existing or new clusters.

---

### Module 3 — Trend Analyzer

**REQ-INTEL-03-1 [U]** The system shall implement trend analysis at `src/trading/news/intelligence/trends.py` that aggregates keyword frequency and sentiment distribution from `news_analysis` data.

**REQ-INTEL-03-2 [U]** No additional LLM calls shall be required for trend analysis. All metrics shall be computed from existing `news_analysis` records (pure SQL/Python aggregation).

**REQ-INTEL-03-3 [U]** Trend data shall be stored in a `news_trends` table:

```sql
CREATE TABLE news_trends (
    id BIGSERIAL PRIMARY KEY,
    trend_date DATE NOT NULL,
    trend_type VARCHAR(10) NOT NULL CHECK (trend_type IN ('daily', 'weekly')),
    sector VARCHAR(50),
    keyword VARCHAR(100) NOT NULL,
    mention_count INTEGER NOT NULL DEFAULT 0,
    sentiment_positive INTEGER NOT NULL DEFAULT 0,
    sentiment_neutral INTEGER NOT NULL DEFAULT 0,
    sentiment_negative INTEGER NOT NULL DEFAULT 0,
    sentiment_avg REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(trend_date, trend_type, sector, keyword)
);

CREATE INDEX idx_news_trends_date_type ON news_trends (trend_date DESC, trend_type);
CREATE INDEX idx_news_trends_keyword ON news_trends (keyword, trend_date DESC);
```

**REQ-INTEL-03-4 [E]** When daily trend aggregation executes, the system shall:
1. Count keyword occurrences from `news_analysis.keywords` for articles published today
2. Compute per-sector sentiment distribution (positive/neutral/negative counts)
3. Calculate `sentiment_avg` as: (positive - negative) / total articles per sector
4. Upsert results into `news_trends` with `trend_type='daily'`

**REQ-INTEL-03-5 [E]** When weekly trend aggregation executes (Sunday or on-demand), the system shall:
1. Aggregate daily keyword counts for the past 7 days
2. Identify **rising keywords**: keywords with > 50% increase vs. previous week
3. Identify **falling keywords**: keywords with > 50% decrease vs. previous week
4. Compute weekly sector sentiment averages
5. Upsert results into `news_trends` with `trend_type='weekly'`

**REQ-INTEL-03-6 [S]** While no analysis data exists for the current day (cold start or crawl failure), the trend analyzer shall skip aggregation without error and retain previous trend data.

---

### Module 4 — Portfolio Relevance Tagger

**REQ-INTEL-04-1 [U]** The system shall implement portfolio relevance tagging at `src/trading/news/intelligence/relevance.py` that cross-references story clusters with current watchlist/portfolio holdings.

**REQ-INTEL-04-2 [U]** The system shall use `TICKER_SECTOR_MAP` from `context_builder.py` to determine which sectors are portfolio-relevant. A story cluster is portfolio-relevant if its `sector` matches any sector in the current watchlist/portfolio.

**REQ-INTEL-04-3 [U]** A story cluster shall receive the [투자 주목] tag when BOTH conditions are met:
- `impact_max >= 4` (high or critical impact)
- Cluster sector matches a held/watched ticker's sector

**REQ-INTEL-04-4 [E]** When a story cluster receives [투자 주목] tag AND `impact_max == 5`, the system shall additionally emit a Telegram alert: `"[NEWS ALERT] {representative_title} (Impact 5/5, Sector: {sector}) — 포트폴리오 관련 고위험 뉴스 감지"`.

**REQ-INTEL-04-5 [S]** While the watchlist is empty or unavailable, the system shall tag ALL story clusters with `impact_max >= 4` as [투자 주목] (full coverage mode, same pattern as SPEC-013 REQ-NEWS-06-5).

**REQ-INTEL-04-6 [U]** Portfolio relevance results shall be stored as a boolean column in `story_clusters`:

```sql
ALTER TABLE story_clusters ADD COLUMN portfolio_relevant BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE story_clusters ADD COLUMN relevance_tickers TEXT[];
```

---

### Module 5 — Intelligence Report Generator

**REQ-INTEL-05-1 [U]** The system shall implement intelligence report generation at `src/trading/news/intelligence/reporter.py` that produces formatted markdown intelligence files.

**REQ-INTEL-05-2 [U]** The system shall generate two output files:
- `data/contexts/intelligence_macro.md` — Global macro intelligence (macro_economy, finance_banking, energy_commodities sectors)
- `data/contexts/intelligence_micro.md` — Sector-specific intelligence (all sectors matching watchlist, or all sectors in full coverage mode)

**REQ-INTEL-05-3 [U]** Each intelligence file shall OVERWRITE on every run (snapshot, not accumulation). The file represents the current intelligence state.

**REQ-INTEL-05-4 [U]** Intelligence reports shall present story clusters (not individual articles) as the primary unit, formatted as:

```markdown
### [투자 주목] {representative_title} (Impact: {impact_max}/5)
_Sources: {source_names} ({source_count}건) | {first_published date}_
- {summary_2line line 1}
- {summary_2line line 2}
```

For non-portfolio-relevant stories:
```markdown
### {representative_title} (Impact: {impact_max}/5)
_Sources: {source_names} ({source_count}건) | {first_published date}_
- {summary_2line line 1}
- {summary_2line line 2}
```

**REQ-INTEL-05-5 [U]** Story clusters within each sector shall be sorted by `impact_max DESC`, then by `source_count DESC` (more sources = more significant).

**REQ-INTEL-05-6 [U]** Each intelligence file shall include a trend snapshot section at the bottom:

```markdown
## 주간 트렌드 ({date_range})
상승 키워드: {rising_keywords}
하락 키워드: {falling_keywords}
섹터 센티멘트: {sector}: {sentiment_label}({percentage}%), ...
```

**REQ-INTEL-05-7 [U]** Maximum content per file: 50 story clusters for `intelligence_macro.md`, 30 story clusters per sector for `intelligence_micro.md`. Excess clusters (lower impact) are truncated.

**REQ-INTEL-05-8 [S]** While no analyzed articles exist for a sector, that sector section shall display `[DATA UNAVAILABLE — awaiting analysis]` rather than being omitted.

**REQ-INTEL-05-9 [U]** The generated intelligence files shall be readable by the existing `get_static_context` tool. Register `intelligence_macro` and `intelligence_micro` as valid context names alongside existing `macro_context`, `micro_context`, `macro_news`, `micro_news`.

---

### Module 6 — Scheduler Integration

**REQ-INTEL-06-1 [U]** The system shall schedule intelligence analysis runs at the following times (10 minutes after each SPEC-013 crawl):
- 08:10, 11:10, 14:40, 22:10, 01:10, 04:10 KST

**REQ-INTEL-06-2 [U]** Each scheduled run shall execute the full pipeline in order:
1. Module 1: Analyze unanalyzed articles (incremental)
2. Module 2: Re-cluster stories (last 24 hours)
3. Module 3: Update daily trends
4. Module 4: Tag portfolio-relevant clusters
5. Module 5: Generate intelligence .md files

**REQ-INTEL-06-3 [U]** The system shall provide a CLI command `trading analyze-news [--force] [--sector SECTOR]` for manual triggering.
- `--force`: Re-analyze all articles from today regardless of analysis state
- `--sector SECTOR`: Limit analysis to articles from the specified sector only

**REQ-INTEL-06-4 [E]** When a scheduled analysis run completes, the system shall log to `audit_log`: `NEWS_INTELLIGENCE_RUN_OK` with details including articles_analyzed, clusters_formed, trends_updated, intelligence_files_generated, total_cost_krw, duration_seconds.

**REQ-INTEL-06-5 [E]** When a scheduled analysis run fails entirely (not partial batch failure), the system shall log `NEWS_INTELLIGENCE_RUN_FAIL` and emit a Telegram alert if 3+ consecutive runs fail.

**REQ-INTEL-06-6 [U]** The intelligence pipeline shall be controlled by feature flag `NEWS_INTELLIGENCE_ENABLED` (default: `true`) in `system_state`. When `false`, scheduled runs are skipped and existing intelligence files are retained (stale but present).

**REQ-INTEL-06-7 [S]** While the `NEWS_INTELLIGENCE_ENABLED` flag is `false`, the CLI command `trading analyze-news --force` shall still execute (override for manual debugging).

---

### Non-Functional Requirements

**REQ-NFR-14-1 [U, Cost]** Monthly Haiku API cost for intelligence analysis shall not exceed 30,000 KRW. If exceeded, emit Telegram warning.

**REQ-NFR-14-2 [U, Performance]** A single analysis run (all 6 modules) shall complete within 120 seconds for up to 100 unanalyzed articles. Haiku batch calls are the bottleneck; target <= 3 seconds per batch.

**REQ-NFR-14-3 [U, Performance]** Story clustering (Module 2) shall complete within 10 seconds for up to 500 articles in the 24-hour window.

**REQ-NFR-14-4 [U, Performance]** Intelligence report generation (Module 5) shall complete within 5 seconds (pure string formatting, no LLM).

**REQ-NFR-14-5 [U, Storage]** `news_analysis` and `story_clusters` tables shall retain data for 90 days (matching `news_articles` retention). `news_trends` shall retain data for 365 days.

**REQ-NFR-14-6 [U, Observability]** All pipeline steps (analyze, cluster, trend, relevance, report, schedule) shall be logged to `audit_log` with appropriate event types and timing metrics.

**REQ-NFR-14-7 [U, Quality]** Intelligence files must be valid UTF-8 markdown, parseable by the `get_static_context` tool without modification to the tool interface.

---

## Specifications

### File Structure

```
src/trading/news/intelligence/
    __init__.py
    analyzer.py          # Module 1: Article analyzer (Haiku LLM batches)
    clustering.py        # Module 2: Story clustering (TF-IDF / title similarity)
    trends.py            # Module 3: Trend aggregation (pure SQL/Python)
    relevance.py         # Module 4: Portfolio relevance tagging
    reporter.py          # Module 5: Intelligence .md report generator
    scheduler.py         # Module 6: Cron integration + CLI entry point
    models.py            # Shared dataclasses (AnalysisResult, StoryCluster, TrendEntry)
    prompts.py           # Haiku prompt templates for article analysis
```

### Database Migration

New migration file: `src/trading/db/migrations/014_news_intelligence.sql`

Contents: `news_analysis` table, `story_clusters` table (with `portfolio_relevant` and `relevance_tickers` columns), `news_trends` table, and required indexes.

### Configuration

Feature flag in `system_state` table (existing SPEC-010 pattern):
```python
NEWS_INTELLIGENCE_ENABLED: bool = True  # Toggle intelligence pipeline
```

Config constants in `src/trading/config.py`:
```python
# SPEC-014: News Intelligence
NEWS_INTELLIGENCE_BATCH_SIZE: int = 10        # Articles per Haiku call
NEWS_INTELLIGENCE_MAX_PER_RUN: int = 100      # Max articles per run
NEWS_INTELLIGENCE_CLUSTER_SIMILARITY: float = 0.6  # Title similarity threshold
NEWS_INTELLIGENCE_CLUSTER_KEYWORD_MIN: int = 2     # Min shared keywords
NEWS_INTELLIGENCE_IMPACT_ALERT_THRESHOLD: int = 4  # Min impact for [투자 주목]
NEWS_INTELLIGENCE_HAIKU_TIMEOUT: float = 30.0      # Haiku API timeout seconds
NEWS_INTELLIGENCE_RETENTION_ANALYSIS_DAYS: int = 90
NEWS_INTELLIGENCE_RETENTION_TRENDS_DAYS: int = 365
```

### Integration Points

1. **Cron schedule** (existing `src/trading/scheduler/daily.py`):
   - Add 6 new scheduled jobs at 08:10, 11:10, 14:40, 22:10, 01:10, 04:10
   - Each triggers `run_intelligence_pipeline()`

2. **CLI** (existing `src/trading/cli.py`):
   - `trading analyze-news [--force] [--sector SECTOR]`

3. **Context access** (SPEC-009/010 `get_static_context` tool):
   - Register `intelligence_macro` and `intelligence_micro` as valid context names
   - Personas can call `get_static_context(name="intelligence_macro")` to read intelligence

4. **Persona prompts** (update system prompts):
   - Add instruction to use `get_static_context(name="intelligence_macro")` for macro intelligence
   - Add instruction to use `get_static_context(name="intelligence_micro")` for sector intelligence
   - Intelligence replaces raw headline context for decision-making quality

5. **Existing context_builder.py** (SPEC-013 Module 6):
   - Remains operational for `macro_news.md` and `micro_news.md` (headline-only backup)
   - Intelligence files supplement (not replace) headline files during transition
   - After validation period, personas switch primary context source to intelligence files

### Haiku Prompt Design

The article analysis prompt shall:
- Accept 10 articles as structured input (title + summary + body excerpt)
- Output structured JSON array with per-article analysis
- Include Korean market investor perspective instruction
- Use `response_format` for reliable JSON parsing
- Total prompt size per batch: ~500 input tokens (instruction) + ~100 tokens/article = ~1500 tokens input
- Expected output: ~200 tokens (20 tokens/article analysis)

### Fallback Strategy

| Failure Scenario | Behavior |
|---|---|
| Haiku API unavailable | Skip analysis, retain previous intelligence files |
| Partial batch failure | Process successful batches, log failures, continue |
| All batches fail in run | Emit Telegram alert, retain stale intelligence files |
| DB connection failure | Log error, skip entire run, retry next cycle |
| 3+ consecutive full failures | Telegram critical alert, human review required |

### Dependent SPECs

This SPEC requires:
- SPEC-TRADING-013 (News Crawling) — `news_articles` table must be populated
- SPEC-TRADING-010 (Haiku Routing) — Haiku model available via `anthropic` SDK
- SPEC-TRADING-009 (Tool-calling) — `get_static_context` tool operational for context delivery

---

## Traceability

| Requirement | Acceptance Test | Implementation |
|---|---|---|
| REQ-INTEL-01-* | AT-01: Article analysis integration test | `src/trading/news/intelligence/analyzer.py` |
| REQ-INTEL-02-* | AT-02: Clustering accuracy test | `src/trading/news/intelligence/clustering.py` |
| REQ-INTEL-03-* | AT-03: Trend aggregation test | `src/trading/news/intelligence/trends.py` |
| REQ-INTEL-04-* | AT-04: Relevance tagging test | `src/trading/news/intelligence/relevance.py` |
| REQ-INTEL-05-* | AT-05: Report generation format test | `src/trading/news/intelligence/reporter.py` |
| REQ-INTEL-06-* | AT-06: Scheduler + CLI integration test | `src/trading/news/intelligence/scheduler.py` |
| REQ-NFR-14-* | AT-07: Performance + cost threshold test | Cross-module |
