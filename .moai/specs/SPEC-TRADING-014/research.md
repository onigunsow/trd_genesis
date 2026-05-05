---
id: SPEC-TRADING-014
type: research
created: 2026-05-05
---

# SPEC-TRADING-014 Research — News Intelligence Analysis Pipeline

## Codebase Analysis

### Upstream Dependencies (SPEC-013 News Crawling)

**news_articles DB schema** (from `storage.py`):
- Table: `news_articles` with `id BIGSERIAL PRIMARY KEY`
- Fields: title, url, summary, body_text, source_name, sector, language, published_at, crawled_at, content_hash, date_inferred
- Indexes: sector+published_at, language, crawled_at
- Deduplication: `ON CONFLICT (content_hash) DO NOTHING`
- Retention: 90 days

**Article dataclass** (from `normalizer.py`):
- Fields: title, url, summary, body_text, source_name, sector, language, published_at, crawled_at, content_hash, date_inferred
- body_text: max 5000 chars (already truncated)
- summary: max 500 chars (already truncated)

**DB access patterns** (from `storage.py`):
- `get_articles_by_sector(sector, days, language, limit)` — returns list of dicts
- `get_articles_multi_sector(sectors, days, language_priority, limit_per_sector)` — returns dict[sector, list]
- Connection: `from trading.db.session import connection` (context manager pattern)

**context_builder.py patterns**:
- Uses `TICKER_SECTOR_MAP` for ticker-to-sector mapping
- `SECTOR_DISPLAY_NAMES` for human-readable sector names
- `_format_article_line()` — current headline formatting
- `_format_sector_section()` — sector header + article list
- `build_macro_news()` / `build_micro_news()` — generates .md content
- `write_macro_news()` / `write_micro_news()` — atomic file write + audit log
- Output path: `contexts_dir() / "macro_news.md"` via `atomic_write()`

### Haiku Integration (SPEC-010 Model Router)

**Model routing** (from SPEC-010 spec.md):
- Haiku model identifier: `claude-haiku-4-5`
- Pricing: input $0.80/M tok, output $4/M tok
- Available via `anthropic` SDK (already in pyproject.toml)
- Model Router at `src/trading/personas/model_router.py` (for persona-level routing)
- SPEC-014 does NOT use Model Router — calls Haiku directly for batch analysis

**Existing Anthropic SDK usage pattern** (from personas/base.py):
```python
from anthropic import Anthropic
client = Anthropic()  # Uses ANTHROPIC_API_KEY from env
response = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=...,
    messages=[...],
)
```

### Scheduler Integration

**Existing scheduler** (from `src/trading/scheduler/daily.py`):
- Uses APScheduler
- Cron pattern: `scheduler.add_job(func, 'cron', hour=H, minute=M)`
- SPEC-013 crawl times: 08:00, 11:00, 14:30, 22:00, 01:00, 04:00
- Intelligence runs: 08:10, 11:10, 14:40, 22:10, 01:10, 04:10 (10min offset)

### CLI Pattern

**Existing CLI** (from `src/trading/cli.py`):
- Entry point: `trading <subcommand>`
- Uses Click framework
- Existing news commands: `trading crawl-news`, `trading news-health`
- New command: `trading analyze-news [--force] [--sector SECTOR]`

### Audit Logging

**Existing audit pattern** (from `src/trading/db/session.py`):
```python
from trading.db.session import audit
audit("EVENT_TYPE", actor="module_name", details={...})
```

### Context Registration

**get_static_context tool** (SPEC-009/010):
- Valid names: `macro_context`, `micro_context`, `macro_news`, `micro_news`
- New registrations needed: `intelligence_macro`, `intelligence_micro`
- Files stored at: `data/contexts/intelligence_macro.md`, `data/contexts/intelligence_micro.md`

---

## Implementation Plan

### Priority High — Core Pipeline

**Milestone 1: Database Schema + Models**
- Create migration `014_news_intelligence.sql` with 3 tables
- Create `models.py` with dataclasses: `AnalysisResult`, `StoryCluster`, `TrendEntry`
- Validate foreign key relationship with `news_articles`

**Milestone 2: Article Analyzer (Module 1)**
- Create `prompts.py` with Haiku prompt template
- Create `analyzer.py` with batch processing logic
- Implement incremental article selection (since last run)
- Implement batch size control (10/batch, max 100/run)
- Add retry logic (1 retry, 5s delay)
- Add cost tracking per batch

**Milestone 3: Story Clustering (Module 2)**
- Create `clustering.py` with title similarity + keyword overlap
- Implement 24-hour window clustering
- Implement cluster merging (new articles into existing clusters)
- Select representative title (highest impact)
- Compute cluster metadata (source_count, impact_max, sentiment_dominant)

### Priority Medium — Enrichment

**Milestone 4: Trend Analyzer (Module 3)**
- Create `trends.py` with daily/weekly aggregation
- Implement keyword frequency counting from `news_analysis.keywords`
- Implement sector sentiment distribution
- Implement rising/falling keyword detection (>50% change)
- Store results with upsert pattern

**Milestone 5: Portfolio Relevance Tagger (Module 4)**
- Create `relevance.py` using existing `TICKER_SECTOR_MAP`
- Implement sector matching logic
- Implement [투자 주목] tagging (impact >= 4 AND portfolio-relevant)
- Implement critical Telegram alert (impact == 5 AND portfolio-relevant)

### Priority High — Output Generation

**Milestone 6: Intelligence Report Generator (Module 5)**
- Create `reporter.py` with intelligence .md generation
- Implement story cluster formatting (Korean, investor-focused)
- Implement trend snapshot section
- Implement per-file limits (50 macro clusters, 30/sector micro)
- Implement `[DATA UNAVAILABLE]` fallback
- Register new context names in `get_static_context` tool

### Priority Medium — Integration

**Milestone 7: Scheduler + CLI (Module 6)**
- Create `scheduler.py` as pipeline orchestrator
- Add 6 cron jobs to `daily.py`
- Add `trading analyze-news` CLI command
- Implement feature flag check (`NEWS_INTELLIGENCE_ENABLED`)
- Add audit logging for complete pipeline runs

**Milestone 8: Persona Prompt Updates**
- Update relevant persona system prompts to reference intelligence context
- Add `intelligence_macro` and `intelligence_micro` to tool definitions
- Validate persona can access new context files via `get_static_context`

---

## Technical Approach

### Haiku Batch Analysis Design

**Prompt structure** (per batch of 10 articles):
```
You are a financial news analyst for Korean stock market investors.

Analyze each article and provide:
1. summary_2line: 2 Korean sentences - key point + market implication
2. impact_score: 1-5 (1=negligible, 5=critical market impact)
3. keywords: 3-5 Korean keywords
4. sentiment: positive/neutral/negative (for stock market impact)

Articles:
[1] Title: ... | Source: ... | Sector: ...
    Body: (first 1000 chars)...
[2] ...

Respond in JSON array format.
```

**Token estimation per batch**:
- System instruction: ~200 tokens
- Per article (title + source + body excerpt): ~130 tokens
- 10 articles: ~1300 tokens input
- Total input per batch: ~1500 tokens
- Output per article: ~20 tokens (summary + score + keywords + sentiment)
- Total output per batch: ~200 tokens
- Cost per batch: (1500 * 0.80 + 200 * 4.00) / 1,000,000 * 1350 = ~2.8 KRW

**Daily cost estimate**:
- 6 runs/day * ~10 batches/run * 2.8 KRW = ~168 KRW/day
- Monthly: ~5,000 KRW (well within 30,000 KRW budget)

### Clustering Algorithm

**Phase 1: Title similarity** (fast pre-filter)
- Normalize titles (lowercase, strip punctuation, remove source name suffixes)
- Use Python's `difflib.SequenceMatcher` or `Levenshtein` for ratio
- Threshold: 0.6 ratio (tunable)

**Phase 2: Keyword overlap** (semantic reinforcement)
- Compare `keywords` arrays from `news_analysis`
- Threshold: >= 2 shared keywords
- Both within 24-hour window

**Phase 3: Cluster formation**
- Union-Find (disjoint sets) algorithm for transitive clustering
- If A~B and B~C, then A,B,C form one cluster
- Representative: article with highest impact_score

### Trend Computation

**Daily trends** (runs each analysis cycle):
```sql
SELECT unnest(keywords) as keyword, sector,
       COUNT(*) as mention_count,
       SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END) as pos,
       SUM(CASE WHEN sentiment='neutral' THEN 1 ELSE 0 END) as neu,
       SUM(CASE WHEN sentiment='negative' THEN 1 ELSE 0 END) as neg
FROM news_analysis na
JOIN news_articles a ON na.article_id = a.id
WHERE a.published_at >= CURRENT_DATE
GROUP BY keyword, sector
ORDER BY mention_count DESC
LIMIT 100;
```

**Weekly rising/falling** (runs Sunday or on-demand):
- Compare this week's keyword counts vs. previous week
- Rising: current_count / previous_count > 1.5 (50% increase)
- Falling: current_count / previous_count < 0.5 (50% decrease)

---

## Risks and Mitigation

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Haiku output parsing failure (malformed JSON) | Medium | Low | Retry with explicit JSON schema instruction; skip individual articles on parse error |
| Clustering false positives (unrelated articles clustered) | Low | Medium | Conservative thresholds (0.6 title, 2 keywords); manual review via CLI |
| Haiku cost exceeds budget | Low | Medium | Max 100 articles/run cap; monitoring via audit_log; Telegram alert at 80% budget |
| Intelligence file too large for persona context | Low | High | Strict limits (50 clusters macro, 30/sector micro); truncate at ~4000 tokens per file |
| SPEC-013 crawl delay > 10 minutes | Low | Low | Intelligence pipeline checks for new articles; if none found, no-op and log |
| Concurrent scheduler runs (race condition) | Low | Medium | Use file-based lock or DB advisory lock; skip if previous run still active |

---

## Architecture Decisions

### ADR-1: Haiku Only (No Sonnet/Opus for Analysis)

**Decision**: Use Claude Haiku 4.5 exclusively for article analysis.

**Rationale**:
- Article summarization and impact scoring are low-reasoning tasks
- Haiku cost is ~73% lower than Sonnet per token
- Quality requirement is "adequate summary" not "deep analysis"
- Deep analysis is the persona's job (they read intelligence context + make decisions)
- Monthly budget impact: ~5,000-9,000 KRW vs. ~20,000-35,000 KRW with Sonnet

### ADR-2: No Embeddings for Clustering

**Decision**: Use simple title similarity + keyword overlap instead of embedding cosine similarity.

**Rationale**:
- SPEC-010 embedding infrastructure exists but adds complexity
- Financial news titles are highly descriptive (same event = similar words)
- Keyword overlap from LLM extraction provides semantic signal without embedding cost
- If clustering quality is insufficient, embeddings can be added in future iteration

### ADR-3: Overwrite (Not Accumulate) Intelligence Files

**Decision**: Each run overwrites intelligence .md files completely.

**Rationale**:
- Personas need current intelligence snapshot, not historical log
- Prevents unbounded file growth
- Simple implementation (write entire file)
- Historical data preserved in DB tables for trend analysis
- Matches existing `context_builder.py` pattern (macro_news.md overwrites each build)

### ADR-4: Supplement (Not Replace) Existing Context Builder

**Decision**: Intelligence files supplement headline-only files during transition period.

**Rationale**:
- Risk mitigation: if intelligence pipeline fails, headline-only backup exists
- Gradual adoption: personas can reference both during validation
- After validation period (2 weeks), switch primary context source to intelligence
- No code deletion of `context_builder.py` in this SPEC

### ADR-5: Fixed Cron Offset (10 Minutes After Crawl)

**Decision**: Analysis runs at fixed times (10min after crawl) rather than event-triggered.

**Rationale**:
- Simpler scheduling (no event bus needed)
- Predictable timing for monitoring
- 10-minute buffer provides comfortable margin for crawl completion
- If crawl is late, analysis processes whatever articles are available (graceful)
- Matches existing APScheduler cron pattern
