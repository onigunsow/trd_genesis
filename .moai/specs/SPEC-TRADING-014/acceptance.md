---
id: SPEC-TRADING-014
type: acceptance
created: 2026-05-05
---

# SPEC-TRADING-014 Acceptance Criteria — News Intelligence Analysis Pipeline

## Definition of Done

- All 6 modules implemented and unit-tested (>= 85% coverage)
- Integration test with real Haiku API call (1 batch of 10 articles)
- Intelligence .md files generated and readable by `get_static_context`
- Cron schedule registered (6 daily runs)
- CLI `trading analyze-news` operational
- Feature flag `NEWS_INTELLIGENCE_ENABLED` toggling works
- Monthly cost projection validated (< 30,000 KRW)
- DB migration applied without errors
- Audit logging for all pipeline stages

---

## AT-01: Article Analyzer (Module 1)

### Scenario 1.1: Successful batch analysis

```gherkin
Given 15 unanalyzed articles exist in news_articles (crawled in last hour)
When the analysis pipeline runs
Then articles are batched into 2 groups (10 + 5)
And each batch is sent to Claude Haiku 4.5
And each article receives: summary_2line, impact_score (1-5), keywords (3-5), sentiment
And results are stored in news_analysis table with correct article_id references
And audit_log records articles_processed=15, batches_sent=2
```

### Scenario 1.2: Incremental processing (only unanalyzed articles)

```gherkin
Given 100 articles in news_articles, 80 already have entries in news_analysis
When the analysis pipeline runs
Then only 20 unanalyzed articles are selected for processing
And 2 Haiku batches are sent (10 + 10)
And no previously analyzed articles are re-processed
```

### Scenario 1.3: Max articles per run cap

```gherkin
Given 150 unanalyzed articles exist in news_articles
When the analysis pipeline runs
Then only the most recent 100 articles (by published_at) are processed
And remaining 50 are deferred to next run
And audit_log records articles_processed=100, articles_deferred=50
```

### Scenario 1.4: Haiku API failure with retry

```gherkin
Given 10 unanalyzed articles exist
When the Haiku API returns 500 error on first attempt
Then the system waits 5 seconds
And retries the batch once
When the retry succeeds
Then analysis results are stored normally
```

### Scenario 1.5: Haiku API failure after retry

```gherkin
Given 20 unanalyzed articles in 2 batches
When batch 1 Haiku call fails on both attempts (initial + retry)
And batch 2 Haiku call succeeds
Then batch 1 articles remain unanalyzed (no news_analysis entry)
And batch 2 articles are stored successfully
And audit_log records haiku_calls_failed=1, haiku_calls_succeeded=1
```

### Scenario 1.6: Haiku response parsing

```gherkin
Given a batch of 10 articles sent to Haiku
When Haiku returns a JSON array with 10 analysis objects
Then each object is validated for required fields
And impact_score is between 1 and 5
And sentiment is one of: positive, neutral, negative
And keywords list has 3-5 entries
And summary_2line contains exactly 2 lines
```

### Scenario 1.7: Cost tracking

```gherkin
Given a successful analysis run processing 50 articles (5 batches)
When the run completes
Then each news_analysis row has token_input, token_output, cost_krw populated
And audit_log records total_cost_krw (sum of all batch costs)
And total_cost_krw is approximately 14 KRW (5 batches * ~2.8 KRW each)
```

---

## AT-02: Story Clustering (Module 2)

### Scenario 2.1: Title similarity clustering

```gherkin
Given two analyzed articles within 24 hours:
  - "US-이란 호르무즈 해협 군사 충돌 발생" (Reuters)
  - "이란-미국 호르무즈 교전, 유가 급등" (Bloomberg)
When clustering runs
Then both articles are grouped into one story cluster
And representative_title is the higher-impact article's title
And source_count = 2
```

### Scenario 2.2: Keyword overlap clustering

```gherkin
Given two analyzed articles within 24 hours:
  - Title: "반도체 수출 사상 최고" (keywords: ["반도체", "수출", "삼성전자"])
  - Title: "삼성전자 반도체 매출 급증 전망" (keywords: ["삼성전자", "반도체", "실적"])
When clustering runs (keyword overlap >= 2: "반도체", "삼성전자")
Then both articles are grouped into one story cluster
```

### Scenario 2.3: No false clustering across 24-hour boundary

```gherkin
Given two similar articles:
  - Article A published 2026-05-04 10:00
  - Article B published 2026-05-05 12:00 (26 hours apart)
When clustering runs
Then articles are NOT clustered together (outside 24-hour window)
And each forms its own single-article cluster
```

### Scenario 2.4: Cluster metadata computation

```gherkin
Given a cluster of 3 articles from Reuters, Bloomberg, WSJ:
  - impact_scores: [4, 5, 3]
  - sentiments: [negative, negative, neutral]
When cluster metadata is computed
Then impact_max = 5
And source_count = 3
And sentiment_dominant = "negative"
And representative_title = title of the article with impact_score=5
```

### Scenario 2.5: Incremental re-clustering

```gherkin
Given an existing cluster with articles [A, B] from 08:10 run
When 11:10 run adds article C (similar to A and B, within 24h)
Then cluster is updated to include [A, B, C]
And source_count is recalculated
And impact_max is recalculated
```

---

## AT-03: Trend Analyzer (Module 3)

### Scenario 3.1: Daily keyword aggregation

```gherkin
Given 50 analyzed articles today with keywords including:
  - "반도체" appears in 15 articles
  - "금리인상" appears in 10 articles
  - "이란" appears in 8 articles
When daily trend aggregation runs
Then news_trends table has entries for each keyword
And mention_count matches article counts (반도체=15, 금리인상=10, 이란=8)
```

### Scenario 3.2: Sector sentiment distribution

```gherkin
Given 20 articles in semiconductor sector today:
  - 12 positive, 5 neutral, 3 negative
When daily trend aggregation runs
Then semiconductor sector entry has:
  - sentiment_positive = 12
  - sentiment_neutral = 5
  - sentiment_negative = 3
  - sentiment_avg = (12 - 3) / 20 = 0.45
```

### Scenario 3.3: Weekly rising/falling keywords

```gherkin
Given keyword "이란" mentioned 20 times this week vs 5 times last week
And keyword "부동산" mentioned 3 times this week vs 15 times last week
When weekly trend analysis runs
Then "이란" is flagged as rising (20/5 = 4.0 > 1.5 threshold)
And "부동산" is flagged as falling (3/15 = 0.2 < 0.5 threshold)
```

### Scenario 3.4: Cold start (no analysis data for today)

```gherkin
Given no articles have been analyzed today (analysis pipeline not yet run)
When daily trend aggregation executes
Then no error is raised
And no new rows are inserted into news_trends
And previous trend data remains unchanged
```

---

## AT-04: Portfolio Relevance Tagger (Module 4)

### Scenario 4.1: Portfolio-relevant high-impact cluster

```gherkin
Given a story cluster:
  - sector: "semiconductor"
  - impact_max: 5
And watchlist includes ticker "005930" (Samsung Electronics, sector: semiconductor)
When relevance tagging runs
Then cluster receives portfolio_relevant = TRUE
And cluster receives relevance_tickers = ["005930"]
And cluster is tagged [투자 주목] in intelligence report
And Telegram alert is sent (impact == 5 AND portfolio-relevant)
```

### Scenario 4.2: High-impact but not portfolio-relevant

```gherkin
Given a story cluster:
  - sector: "defense_aerospace"
  - impact_max: 5
And watchlist has no tickers in defense_aerospace sector
When relevance tagging runs
Then cluster receives portfolio_relevant = FALSE
And cluster is NOT tagged [투자 주목]
And no Telegram alert is sent
```

### Scenario 4.3: Portfolio-relevant but low impact

```gherkin
Given a story cluster:
  - sector: "semiconductor"
  - impact_max: 3
And watchlist includes semiconductor sector tickers
When relevance tagging runs
Then cluster receives portfolio_relevant = TRUE (sector matches)
But cluster is NOT tagged [투자 주목] (impact < 4)
```

### Scenario 4.4: Empty watchlist (full coverage mode)

```gherkin
Given watchlist is empty or unavailable
And a story cluster with impact_max = 4 in any sector
When relevance tagging runs
Then cluster is tagged [투자 주목] (full coverage mode)
And portfolio_relevant = TRUE for all high-impact clusters
```

### Scenario 4.5: Telegram alert for critical stories

```gherkin
Given a cluster with impact_max=5, sector="energy_commodities"
And ticker "051910" (LG Chem) in watchlist maps to energy_commodities
When relevance tagging identifies this cluster
Then Telegram message is sent:
  "[NEWS ALERT] {title} (Impact 5/5, Sector: energy_commodities) — 포트폴리오 관련 고위험 뉴스 감지"
```

---

## AT-05: Intelligence Report Generator (Module 5)

### Scenario 5.1: Macro intelligence file generation

```gherkin
Given story clusters exist for sectors: macro_economy (10), finance_banking (8), energy_commodities (5)
When intelligence report generates intelligence_macro.md
Then file is written to data/contexts/intelligence_macro.md
And file contains 3 sector sections
And clusters are sorted by impact_max DESC within each section
And [투자 주목] tagged clusters appear with tag prefix
And trend snapshot section appears at bottom
```

### Scenario 5.2: Story cluster formatting

```gherkin
Given a story cluster:
  - representative_title: "US-이란 호르무즈 교전 재개"
  - impact_max: 5
  - sources: Reuters, Bloomberg, WSJ (3건)
  - portfolio_relevant: TRUE
  - summary_2line: "4주간 유지되던 휴전 붕괴. 유가 3% 급등, 안전자산 선호 강화\n에너지 섹터 직접 영향. 포트폴리오 내 에너지 비중 점검 필요"
When formatted for intelligence report
Then output matches:
  "### [투자 주목] US-이란 호르무즈 교전 재개 (Impact: 5/5)"
  "_Sources: Reuters, Bloomberg, WSJ (3건) | 2026-05-05_"
  "- 4주간 유지되던 휴전 붕괴. 유가 3% 급등, 안전자산 선호 강화"
  "- 에너지 섹터 직접 영향. 포트폴리오 내 에너지 비중 점검 필요"
```

### Scenario 5.3: Trend snapshot section

```gherkin
Given weekly trend data:
  - rising: ["이란", "금리인상", "AI반도체"]
  - falling: ["경기침체", "부동산"]
  - sector sentiments: macro_economy Negative(60%), it_ai Positive(70%)
When trend snapshot is generated
Then output includes:
  "## 주간 트렌드 (05/01~05/05)"
  "상승 키워드: 이란, 금리인상, AI반도체"
  "하락 키워드: 경기침체, 부동산"
  "섹터 센티멘트: 매크로 Negative(60%), IT Positive(70%)"
```

### Scenario 5.4: File overwrite (not accumulate)

```gherkin
Given intelligence_macro.md exists from previous run (22:10 yesterday)
When 08:10 today's analysis generates new intelligence
Then intelligence_macro.md is completely overwritten with new content
And file size reflects only current run's data (no historical accumulation)
```

### Scenario 5.5: Data unavailable sector

```gherkin
Given no analyzed articles exist for energy_commodities sector today
When intelligence_macro.md is generated
Then energy_commodities section shows:
  "## Energy & Commodities [DATA UNAVAILABLE — awaiting analysis]"
```

### Scenario 5.6: Maximum cluster limit enforcement

```gherkin
Given 70 story clusters exist for macro sectors
When intelligence_macro.md is generated
Then only top 50 clusters (by impact_max, then source_count) are included
And remaining 20 lower-impact clusters are truncated
```

### Scenario 5.7: Context tool registration

```gherkin
Given intelligence_macro.md has been generated
When a persona calls get_static_context(name="intelligence_macro")
Then the full content of intelligence_macro.md is returned
And the response format matches existing macro_news/micro_news behavior
```

---

## AT-06: Scheduler + CLI (Module 6)

### Scenario 6.1: Scheduled cron execution

```gherkin
Given NEWS_INTELLIGENCE_ENABLED is TRUE in system_state
When the system clock reaches 08:10 KST
Then the intelligence pipeline executes:
  1. Article analysis (Module 1)
  2. Story clustering (Module 2)
  3. Trend aggregation (Module 3)
  4. Portfolio relevance tagging (Module 4)
  5. Intelligence report generation (Module 5)
And audit_log records NEWS_INTELLIGENCE_RUN_OK with timing details
```

### Scenario 6.2: Feature flag disabled

```gherkin
Given NEWS_INTELLIGENCE_ENABLED is FALSE in system_state
When the system clock reaches 08:10 KST
Then the scheduled intelligence run is skipped
And no Haiku API calls are made
And existing intelligence .md files are retained (stale but present)
And audit_log records NEWS_INTELLIGENCE_SKIP with reason="feature_disabled"
```

### Scenario 6.3: CLI manual trigger

```gherkin
Given 30 unanalyzed articles exist
When user runs: trading analyze-news
Then the full pipeline executes (same as scheduled run)
And console shows progress: "Analyzing 30 articles (3 batches)..."
And console shows result: "Done: 30 analyzed, 5 clusters, 2 [투자 주목]"
```

### Scenario 6.4: CLI with --force flag

```gherkin
Given 50 articles today, all already analyzed
When user runs: trading analyze-news --force
Then all 50 articles are re-analyzed (existing news_analysis entries updated)
And clusters are regenerated
And intelligence files are refreshed
```

### Scenario 6.5: CLI with --sector filter

```gherkin
Given 100 unanalyzed articles across 5 sectors
When user runs: trading analyze-news --sector semiconductor
Then only semiconductor sector articles are processed
And clustering/trends only updated for semiconductor
And intelligence files reflect semiconductor-only update
```

### Scenario 6.6: Consecutive failure alerting

```gherkin
Given 2 previous intelligence runs failed (NEWS_INTELLIGENCE_RUN_FAIL in audit_log)
When the 3rd consecutive run also fails
Then Telegram alert is sent: "[NEWS INTEL] 3회 연속 분석 파이프라인 실패. 확인 필요."
```

### Scenario 6.7: Feature flag override via CLI

```gherkin
Given NEWS_INTELLIGENCE_ENABLED is FALSE
When user runs: trading analyze-news --force
Then the pipeline executes despite feature flag (CLI override)
And audit_log records: "Manual override — feature flag disabled but --force used"
```

---

## AT-07: Non-Functional Requirements

### Scenario 7.1: Performance — full pipeline under 120 seconds

```gherkin
Given 100 unanalyzed articles (maximum per run)
When the full pipeline executes
Then total duration is <= 120 seconds
And audit_log records duration_seconds <= 120
```

### Scenario 7.2: Performance — clustering under 10 seconds

```gherkin
Given 500 articles in the 24-hour clustering window
When story clustering executes
Then clustering completes in <= 10 seconds
```

### Scenario 7.3: Performance — report generation under 5 seconds

```gherkin
Given 50 story clusters with all metadata computed
When intelligence report generation executes
Then both .md files are generated in <= 5 seconds total
```

### Scenario 7.4: Cost — monthly budget compliance

```gherkin
Given the system runs 6 analysis cycles per day for 30 days
And average 50 new articles per cycle (5 batches)
When monthly cost is calculated
Then total Haiku cost is approximately:
  6 runs * 30 days * 5 batches * 2.8 KRW = ~2,520 KRW
And total is well within 30,000 KRW monthly budget
```

### Scenario 7.5: Data retention

```gherkin
Given news_analysis records older than 90 days exist
And news_trends records older than 365 days exist
When the daily cleanup job runs
Then news_analysis records > 90 days are deleted
And news_trends records > 365 days are deleted
And story_clusters records > 90 days are deleted
```

### Scenario 7.6: Observability — complete audit trail

```gherkin
Given a complete pipeline run
Then audit_log contains entries for:
  - NEWS_INTELLIGENCE_RUN_OK (or FAIL)
  - Per-batch Haiku call metrics (tokens, cost, duration)
  - Clustering results (clusters formed, articles grouped)
  - Trend update summary
  - Relevance tagging results
  - Report generation confirmation
```

### Scenario 7.7: Intelligence file validity

```gherkin
Given intelligence_macro.md is generated
When parsed as UTF-8 text
Then file is valid UTF-8
And file contains valid markdown structure
And file size is <= 50KB (reasonable for context injection)
And file can be read by get_static_context without error
```

---

## Quality Gates

| Gate | Threshold | Tool |
|---|---|---|
| Unit test coverage | >= 85% per module | pytest + coverage |
| Integration test (Haiku) | 1 batch of 10 real articles | pytest (mark: integration) |
| Linting | zero ruff errors | ruff check |
| Type checking | zero mypy errors | mypy --strict |
| DB migration | applies cleanly on fresh DB | psql migration script |
| Intelligence file format | parseable by get_static_context | functional test |
| Cost projection | < 30,000 KRW/month estimated | calculation from test data |
| Performance | pipeline < 120s for 100 articles | pytest benchmark |

---

## Verification Methods

| Module | Primary Verification | Secondary Verification |
|---|---|---|
| M1 (Analyzer) | Unit tests with mocked Haiku + 1 integration test | audit_log cost tracking vs budget |
| M2 (Clustering) | Unit tests with known article pairs (positive + negative cases) | Manual spot-check via CLI |
| M3 (Trends) | Unit tests with seeded keyword data | Weekly trend report visual inspection |
| M4 (Relevance) | Unit tests with known ticker/sector mappings | Telegram alert delivery verification |
| M5 (Reporter) | Snapshot tests comparing generated .md with expected format | get_static_context integration test |
| M6 (Scheduler) | Integration test with mocked pipeline + real cron registration | 24-hour soak test in Docker |
