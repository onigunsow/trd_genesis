---
id: SPEC-TRADING-011
version: 0.1.0
status: draft
created: 2026-05-05
updated: 2026-05-05
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "System Infrastructure Upgrade — JIT State Reconstruction Pipeline + Prototype-based Risk Management"
related_specs:
  - SPEC-TRADING-001
  - SPEC-TRADING-007
  - SPEC-TRADING-009
  - SPEC-TRADING-010
---

# SPEC-TRADING-011 — System Infrastructure Upgrade

## HISTORY

| Date | Version | Change | Author |
|---|---|---|---|
| 2026-05-05 | 0.1.0 | Draft — JIT State Reconstruction Pipeline + ProtoHedge-style Risk Management (6 modules) | onigunsow |

## Scope Summary

SPEC-TRADING-007 established cron-based Static Context generation (06:00/06:30/06:45). SPEC-TRADING-009 introduced Tool-calling active retrieval. SPEC-TRADING-010 added pgvector semantic context retrieval. This SPEC addresses the fundamental limitation that all upstream context becomes stale between cron cycles.

Two complementary infrastructure upgrades:

1. **JIT (Just-in-Time) State Reconstruction Pipeline** — Pre-market snapshot (existing cron) + intraday delta event stream (KIS WebSocket prices, DART polling disclosures, news RSS) + O(1) merge at query time. Personas always receive the latest market state without waiting for the next cron cycle.

2. **ProtoHedge-style Risk Management** — Build a library of historical "market prototypes" (crash, rally, sideways scenarios with embeddings). Risk persona can query prototype similarity to provide interpretable, dynamic exposure adjustment: "Current market is 82% similar to 2024-08 crash prototype; recommend reducing exposure from 80% to 50%."

**Key Principles**:
- Existing cron .md generation continues as "base snapshot" source (not replaced)
- Static risk limits (SPEC-001 REQ-RISK-05-1: daily -1%, stock 20%, total 80%) remain as HARD floor
- Prototypes add a dynamic ceiling (never violate static floor, only tighten)
- Reuse SPEC-010 pgvector infrastructure for prototype embeddings
- Feature flags for incremental activation

### Research Basis

- **HSTR (Historical State Reconstruction)** — Pre-compute snapshots + delta updates for O(1) state reconstruction at query time (NotebookLM research)
- **ProtoHedge** — Compare current market to historical "market prototypes" for interpretable risk management (NotebookLM research)
- **AlphaQuanter** (SPEC-009 basis) — Active retrieval via tools; tools now return merged state
- **FinCon** (SPEC-009 basis) — Multi-agent debate; Risk persona gains prototype context for more informed verdicts

---

## Environment

- Existing SPEC-TRADING-001 infrastructure — Postgres 16-alpine, Anthropic API, Telegram, Docker compose
- SPEC-009 Tool-calling architecture active (`TOOL_CALLING_ENABLED=true`)
- SPEC-010 pgvector extension active (`CREATE EXTENSION vector;`) with embedding pipeline operational
- SPEC-010 `context_embeddings` table and `embeddings/` module available for reuse
- KIS Developers WebSocket API available for real-time price data (paper: `ws://ops.koreainvestment.com:21000`, live: `ws://ops.koreainvestment.com:31000`)
- APScheduler in-container cron system (SPEC-001) continues unchanged
- Existing cron context builders (`build_macro_context.py`, `build_micro_context.py`, etc.) remain as snapshot generators
- Docker compose topology: single `app` container with background threads (no additional containers required for WebSocket)

## Assumptions

1. KIS Developers WebSocket API is stable during market hours (09:00-15:30 KST) and provides real-time price updates for subscribed tickers with latency < 1 second. Maximum subscription: 40 tickers per connection (KIS paper mode documented limit).
2. DART API polling at 5-minute intervals during market hours provides timely disclosure coverage without rate limit issues (DART rate limit: 1000 req/day, 5-min polling = ~78 requests for 6.5h market session).
3. Delta event volume is manageable within PostgreSQL: estimated ~5000 price events/day (40 tickers x ~130 price updates/day) + ~10-20 disclosures/day + ~50 news items/day = ~5100 events/day.
4. Snapshot + delta merge at query time can achieve < 100ms latency using cached materialized merge (cache TTL: 10 seconds).
5. Historical market prototypes (initial library of 10-20 scenarios) provide sufficient diversity for meaningful similarity comparison. Prototype library grows incrementally through manual addition and automated extraction.
6. Embedding similarity using SPEC-010's configured embedding model (voyage-3 / text-embedding-3-small) produces meaningful financial scenario similarity scores (cosine similarity threshold 0.75+ indicates high resemblance).
7. ProtoHedge exposure adjustment is advisory to the Risk persona — it does NOT auto-execute trades. The Risk persona incorporates similarity context into its APPROVE/HOLD/REJECT verdict.
8. The existing `app` Docker container has sufficient resources to run a WebSocket background thread alongside APScheduler and Telegram bot (no separate container needed).
9. Feature flag activation follows the same pattern as SPEC-009/010 (phased rollout, per-module toggles in `system_state`).

## Robustness Principles (SPEC-001 6-Principle Inheritance)

This SPEC inherits SPEC-TRADING-001 v0.2.0's 6 Robustness Principles:

- **External dependency failure assumption (Principle 1)** — WebSocket disconnect: auto-reconnect with exponential backoff. DART API failure: skip cycle, retry next 5-min window. Embedding API failure: use most recent cached prototype similarity.
- **Data corruption prevention (Principle 2)** — Delta events are append-only. Merge is read-only operation on immutable snapshot + immutable deltas. No destructive updates.
- **Failure silence prohibition (Principle 3)** — WebSocket disconnect, DART polling failure, merge timeout all emit audit_log + Telegram alert.
- **Auto-recovery with human notification (Principle 4)** — WebSocket auto-reconnect. Delta table auto-cleanup (7-day retention). Prototype similarity cache auto-refresh.
- **Tests harden the specification (Principle 5)** — Delta pipeline, merge engine, prototype similarity modules require 85%+ coverage.
- **Graceful degradation (Principle 6)** — If JIT pipeline fails, system falls back to cron-only static context (current behavior). If prototype similarity fails, static limits remain enforced (no dynamic ceiling).

---

## Requirements (EARS)

EARS notation: **U**=Ubiquitous, **E**=Event-driven, **S**=State-driven, **O**=Optional, **N**=Unwanted

---

### Module 1 — Delta Event Pipeline (Intraday Event Streaming)

**REQ-DELTA-01-1 [U]** The system shall implement a Delta Event Pipeline under `src/trading/jit/` that captures intraday market events from three sources: KIS WebSocket (real-time prices), DART API (disclosure polling), and news RSS feeds (headline polling).

**REQ-DELTA-01-2 [U]** The system shall create a `delta_events` table:

```sql
CREATE TABLE delta_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,          -- 'price_update', 'disclosure', 'news'
    source TEXT NOT NULL,              -- 'kis_ws', 'dart_api', 'news_rss'
    ticker TEXT,                       -- stock code (nullable for market-wide events)
    payload JSONB NOT NULL,            -- event-specific data
    event_ts TIMESTAMPTZ NOT NULL,     -- when the event actually occurred
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    snapshot_id BIGINT,               -- references the base snapshot this delta belongs to
    merged BOOLEAN NOT NULL DEFAULT false  -- whether this delta has been folded into next snapshot
);

CREATE INDEX idx_delta_events_type_ts ON delta_events(event_type, event_ts DESC);
CREATE INDEX idx_delta_events_ticker_ts ON delta_events(ticker, event_ts DESC) WHERE ticker IS NOT NULL;
CREATE INDEX idx_delta_events_snapshot ON delta_events(snapshot_id) WHERE merged = false;
```

**REQ-DELTA-01-3 [U]** The `delta_events.payload` JSONB shall contain source-specific data:

| event_type | payload schema |
|---|---|
| `price_update` | `{ticker, price, volume, change_pct, high, low, market_cap, timestamp}` |
| `disclosure` | `{ticker, title, report_type, url, filing_date, summary}` |
| `news` | `{ticker, headline, source_name, url, published_at, sentiment}` |

**REQ-DELTA-01-4 [E]** When the system detects market open (09:00 KST on business days), the Delta Event Pipeline shall start:
1. Connect KIS WebSocket and subscribe to price updates for: all positions + watchlist + today's candidates (max 40 tickers)
2. Start DART disclosure polling (every 5 minutes, for positions + watchlist tickers)
3. Start news RSS polling (every 10 minutes, broad market + watchlist)

**REQ-DELTA-01-5 [E]** When the system detects market close (15:30 KST), the Delta Event Pipeline shall:
1. Gracefully disconnect KIS WebSocket
2. Stop DART and news polling
3. Mark the day's un-merged deltas with current `snapshot_id`
4. Write `audit_log` event `DELTA_PIPELINE_STOPPED` with day summary (event count by type)

**REQ-DELTA-01-6 [U]** KIS WebSocket connection shall implement:
- Auto-reconnect with exponential backoff (initial: 1s, max: 60s, jitter: +/- 500ms)
- Heartbeat ping every 30 seconds
- Connection health monitoring: if no data received for 60 seconds during market hours, trigger reconnect
- Maximum reconnect attempts: 10 per market session. After 10 failures, emit Telegram alert and disable WebSocket for the day.

**REQ-DELTA-01-7 [E]** When a KIS WebSocket message is received, the system shall:
1. Parse the real-time price data
2. Insert into `delta_events` with `event_type='price_update'`
3. Update the in-memory price cache (used by merge engine)
4. If the price change exceeds existing event trigger thresholds (SPEC-001: +/-3% for positions), emit the event trigger signal to the orchestrator

**REQ-DELTA-01-8 [E]** When DART polling detects a new disclosure for a position/watchlist ticker, the system shall:
1. Insert into `delta_events` with `event_type='disclosure'`
2. If disclosure is material (report_type in ['major_event', 'earnings', 'governance']), emit event trigger to orchestrator

**REQ-DELTA-01-9 [U]** Delta event retention policy: events older than 7 days and marked `merged=true` shall be deleted by a nightly cleanup job (03:00 KST). Un-merged events are never deleted.

**REQ-DELTA-01-10 [N]** The Delta Event Pipeline shall NOT operate on weekends, holidays, or non-market hours. If accidentally started outside market hours, it shall immediately stop and log warning.

**REQ-DELTA-01-11 [U]** The system shall create a `snapshots` table to track base snapshot generations:

```sql
CREATE TABLE snapshots (
    id BIGSERIAL PRIMARY KEY,
    snapshot_type TEXT NOT NULL,       -- 'macro', 'micro', 'news'
    generated_at TIMESTAMPTZ NOT NULL,
    file_path TEXT NOT NULL,           -- path to .md file
    content_hash TEXT NOT NULL,        -- SHA256 of file content
    delta_count INTEGER DEFAULT 0,    -- number of deltas since this snapshot
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**REQ-DELTA-01-12 [E]** When a cron context build completes (SPEC-007 triggers at 06:00/06:30/06:45), the system shall:
1. Create a new `snapshots` row with the generated file metadata
2. Mark all previous un-merged `delta_events` for that snapshot type as `merged=true`
3. Reset the delta counter for the new snapshot

---

### Module 2 — Snapshot + Delta Merge Engine (O(1) State Reconstruction)

**REQ-MERGE-02-1 [U]** The system shall implement a Merge Engine under `src/trading/jit/merge.py` that reconstructs the current market state by combining the latest snapshot with all un-merged delta events in O(1) amortized time complexity.

**REQ-MERGE-02-2 [U]** The Merge Engine shall maintain an in-memory merged state cache with:
- Cache key: `(snapshot_type, snapshot_id)`
- Cache value: merged state dictionary
- Cache TTL: 10 seconds (configurable via `JIT_CACHE_TTL_SECONDS`)
- Cache invalidation: on new delta event arrival (lazy invalidation at next read)

**REQ-MERGE-02-3 [U]** The merge algorithm shall follow:
1. Load base snapshot (parsed .md content or structured data from snapshot table)
2. Query `delta_events WHERE snapshot_id = current_snapshot AND merged = false ORDER BY event_ts ASC`
3. Apply deltas sequentially to base state:
   - `price_update`: override ticker's price/volume/change fields
   - `disclosure`: append to ticker's disclosure list
   - `news`: append to market/ticker news list
4. Return merged state dictionary

**REQ-MERGE-02-4 [U]** The Merge Engine shall provide the following interface functions:
- `get_merged_state(snapshot_type: str) -> MergedState` — Full merged state for a snapshot type
- `get_ticker_current(ticker: str) -> TickerState` — Single ticker merged state (price + disclosures + news)
- `get_market_summary() -> MarketSummary` — Aggregate market state (indices, breadth, volume)
- `get_deltas_since(snapshot_type: str, since: datetime) -> list[DeltaEvent]` — Raw deltas since timestamp

**REQ-MERGE-02-5 [S]** While the in-memory cache is valid (within TTL), the Merge Engine shall return cached results without database queries. While the cache is expired or invalidated, the Merge Engine shall execute the full merge and refresh the cache.

**REQ-MERGE-02-6 [U]** Merge Engine performance requirements:
- Cached read: < 1ms
- Cold merge (no cache): < 100ms for typical delta volume (< 5000 events/day)
- Memory footprint: < 50MB for full merged state of 40 tickers

**REQ-MERGE-02-7 [E]** When a merge operation exceeds 200ms (performance degradation), the system shall:
1. Log `audit_log` event `MERGE_SLOW` with duration and delta count
2. If 3 consecutive slow merges, emit Telegram alert suggesting delta compaction

**REQ-MERGE-02-8 [N]** The Merge Engine shall NOT modify the base snapshot or delta events. It is a pure read operation. State reconstruction is always repeatable from the same snapshot + deltas.

---

### Module 3 — Market Prototype Library (Historical Scenario Embeddings)

**REQ-PROTO-03-1 [U]** The system shall implement a Market Prototype Library under `src/trading/prototypes/` that stores historical market scenarios as structured data with embeddings for similarity comparison.

**REQ-PROTO-03-2 [U]** The system shall create a `market_prototypes` table:

```sql
CREATE TABLE market_prototypes (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,                    -- e.g., '2024-08-crash', '2024-11-rally'
    description TEXT NOT NULL,                    -- human-readable scenario description
    category TEXT NOT NULL,                       -- 'crash', 'rally', 'sideways', 'correction', 'recovery'
    time_period_start DATE NOT NULL,
    time_period_end DATE NOT NULL,
    market_conditions JSONB NOT NULL,             -- structured market state during this period
    key_indicators JSONB NOT NULL,                -- indicator values that characterize this scenario
    outcome JSONB NOT NULL,                       -- what happened (drawdown %, recovery days, etc.)
    risk_recommendation JSONB NOT NULL,           -- recommended exposure adjustments
    embedding vector(1024) NOT NULL,              -- scenario embedding (reuse SPEC-010 model)
    source TEXT NOT NULL DEFAULT 'manual',        -- 'manual' or 'auto_extracted'
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_prototypes_category ON market_prototypes(category);
CREATE INDEX idx_prototypes_embedding ON market_prototypes
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
```

**REQ-PROTO-03-3 [U]** The `market_conditions` JSONB shall capture:

```json
{
  "kospi_change_pct": -8.2,
  "kosdaq_change_pct": -12.1,
  "vix_level": 38.5,
  "usd_krw": 1380,
  "foreign_net_sell_days": 10,
  "market_breadth_pct": 15,
  "sector_rotation": "defensive",
  "volume_ratio_vs_20d": 2.8,
  "credit_balance_change_pct": -5.0,
  "fed_rate_direction": "hold",
  "bok_rate_direction": "hold"
}
```

**REQ-PROTO-03-4 [U]** The `key_indicators` JSONB shall contain the indicator values that most characterize the scenario (used for embedding generation):

```json
{
  "trigger_event": "US recession fears + Yen carry trade unwind",
  "leading_signals": ["VIX > 30", "USD/KRW > 1350", "Foreign net sell > 5 days"],
  "duration_days": 12,
  "max_drawdown_pct": -12.5,
  "recovery_days": 45,
  "affected_sectors": ["tech", "growth"],
  "safe_sectors": ["utilities", "healthcare"]
}
```

**REQ-PROTO-03-5 [U]** The `risk_recommendation` JSONB shall specify exposure adjustment rules:

```json
{
  "max_exposure_pct": 50,
  "max_single_stock_pct": 10,
  "avoid_sectors": ["tech", "growth"],
  "prefer_sectors": ["utilities", "healthcare"],
  "reduce_position_if_held": true,
  "reasoning": "High crash similarity suggests defensive posture. Reduce tech exposure, increase cash."
}
```

**REQ-PROTO-03-6 [U]** The system shall provide an initial prototype library of at least 10 scenarios:

| Name | Category | Period | Key Characteristic |
|---|---|---|---|
| `2024-08-crash` | crash | 2024-08-01 ~ 2024-08-15 | Yen carry trade unwind + US recession fear |
| `2024-11-rally` | rally | 2024-11-01 ~ 2024-12-15 | Trump election rally + tech rotation |
| `2020-03-covid-crash` | crash | 2020-02-20 ~ 2020-03-23 | COVID pandemic global selloff |
| `2020-04-covid-recovery` | recovery | 2020-03-24 ~ 2020-06-30 | Stimulus-driven V-shaped recovery |
| `2022-rate-hike-bear` | correction | 2022-01-01 ~ 2022-10-15 | Fed aggressive rate hikes, growth selloff |
| `2023-ai-rally` | rally | 2023-01-01 ~ 2023-07-31 | AI hype (NVIDIA, semiconductors) |
| `2024-04-sideways` | sideways | 2024-04-01 ~ 2024-06-30 | Range-bound, low volatility |
| `2022-09-credit-crisis` | correction | 2022-09-01 ~ 2022-11-30 | Legoland/HK crisis, Korean credit |
| `2021-11-peak` | correction | 2021-11-01 ~ 2022-01-31 | KOSPI peak rotation, retail exit |
| `2024-01-value-rotation` | rally | 2024-01-01 ~ 2024-03-31 | Corporate Value-Up program, financials |

**REQ-PROTO-03-7 [U]** Prototype embedding generation shall:
1. Concatenate `description + category + key_indicators + market_conditions` into a text representation
2. Generate embedding using SPEC-010's configured embedding model (voyage-3 / text-embedding-3-small)
3. Store in `market_prototypes.embedding` column

**REQ-PROTO-03-8 [E]** When a new prototype is added (via CLI or admin tool), the system shall:
1. Generate embedding for the prototype
2. Insert into `market_prototypes`
3. Write `audit_log` event `PROTOTYPE_ADDED`
4. Emit Telegram confirmation: "Market prototype added: {name} ({category})"

**REQ-PROTO-03-9 [O]** Where possible, the system shall provide an automated prototype extraction tool (`trading prototype-extract --period 2024-08`) that:
1. Fetches historical data for the specified period from data adapters
2. Computes market condition indicators automatically
3. Generates a draft prototype for human review before activation

**REQ-PROTO-03-10 [N]** The system shall NOT automatically activate extracted prototypes. All new prototypes require manual review (`is_active=false` until explicitly activated by user).

---

### Module 4 — Dynamic Risk Exposure (Prototype-based Exposure Ceiling)

**REQ-DYNRISK-04-1 [U]** The system shall implement a Dynamic Risk Exposure module under `src/trading/prototypes/exposure.py` that computes prototype similarity and recommends exposure ceilings based on current market conditions.

**REQ-DYNRISK-04-2 [U]** The Dynamic Risk Exposure computation shall:
1. Construct a "current market state" vector from latest merged state (Module 2):
   - KOSPI/KOSDAQ change (5-day rolling)
   - VIX level
   - USD/KRW level
   - Foreign net buy/sell (5-day cumulative)
   - Market breadth (advancing / total stocks)
   - Volume ratio vs 20-day average
2. Generate embedding for the current market state text representation
3. Execute pgvector cosine similarity search against `market_prototypes` (active only)
4. Return top-K (K=3) most similar prototypes with similarity scores

**REQ-DYNRISK-04-3 [U]** The system shall apply the following exposure adjustment logic:

| Similarity Score | Category | Exposure Ceiling |
|---|---|---|
| >= 0.85 | crash | 30% (from static 80%) |
| >= 0.80 | crash | 50% |
| >= 0.75 | crash/correction | 60% |
| >= 0.85 | rally | 90% (allow higher than static floor for upside) |
| < 0.75 | any | No dynamic adjustment (use static limits) |

**REQ-DYNRISK-04-4 [U]** Dynamic exposure ceiling rules:
- Dynamic ceiling can only TIGHTEN limits below static limits (never loosen beyond static 80%)
- Exception: rally prototypes with >= 0.85 similarity may recommend up to 90% exposure, but this is advisory only (Risk persona decides)
- Static limits (SPEC-001 REQ-RISK-05-1) remain as absolute HARD floor — code-rule enforcement unchanged
- Multiple prototype matches: use the MOST restrictive ceiling among all >= 0.75 matches

**REQ-DYNRISK-04-5 [N]** Dynamic Risk Exposure shall NOT auto-execute trades or force position liquidation. It provides advisory context to the Risk persona and code-rule limits module.

**REQ-DYNRISK-04-6 [E]** When prototype similarity computation completes for a trading cycle, the system shall:
1. Store result in `prototype_similarity_log` table
2. If highest similarity >= 0.75, inject similarity context into Risk persona's input
3. If highest similarity >= 0.85 (crash/correction category), emit Telegram alert:
   "ProtoHedge Alert: Current market {similarity}% similar to {prototype_name}. Dynamic ceiling: {ceiling}%."

**REQ-DYNRISK-04-7 [U]** The system shall create a `prototype_similarity_log` table:

```sql
CREATE TABLE prototype_similarity_log (
    id BIGSERIAL PRIMARY KEY,
    cycle_kind TEXT NOT NULL,          -- 'pre_market', 'intraday', 'event'
    current_state_embedding vector(1024),
    top_matches JSONB NOT NULL,        -- [{prototype_id, name, category, similarity, ceiling_pct}]
    applied_ceiling_pct REAL,          -- actual ceiling applied (most restrictive)
    static_limit_pct REAL NOT NULL DEFAULT 80.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_proto_sim_log_ts ON prototype_similarity_log(created_at DESC);
```

**REQ-DYNRISK-04-8 [E]** When the Risk persona is invoked (SPEC-009 REQ-PTOOL-02-6), the system shall additionally inject prototype similarity context into Risk's input if any match >= 0.75:

```
[ProtoHedge Context]
Current market similarity analysis:
1. 2024-08-crash: 82% similar — Recommended ceiling: 50%
2. 2022-rate-hike-bear: 71% similar — Below threshold
Applied dynamic ceiling: 50% (vs static 80%)
Reasoning: Yen carry trade unwind pattern + foreign net sell 8 days
```

**REQ-DYNRISK-04-9 [U]** The daily report shall include a ProtoHedge summary:
```
[ProtoHedge]
Today's highest similarity: 2024-08-crash (82%)
Applied ceiling: 50% (static: 80%)
Prototype alerts: 1 (09:30 cycle)
```

**REQ-DYNRISK-04-10 [E]** When the Telegram command `/prototype-status` is received from `chat_id=60443392`, the system shall reply with: current top-3 prototype matches, applied ceiling, and static limit comparison.

---

### Module 5 — Tool Integration (Enhance Existing Tools with Real-time Merged State)

**REQ-TOOLINT-05-1 [U]** The existing SPEC-009 tool `get_ticker_technicals` shall be enhanced to return merged state (snapshot + deltas) when JIT pipeline is enabled. The tool shall:
1. Call Merge Engine `get_ticker_current(ticker)` for real-time price/volume
2. Compute technical indicators (MA, RSI, MACD) using merged price data
3. Return enhanced result with `data_freshness` field indicating last delta timestamp

**REQ-TOOLINT-05-2 [U]** The existing SPEC-009 tool `get_portfolio_status` shall be enhanced to:
1. Include real-time unrealized P&L using merged price data (vs last cron snapshot)
2. Include `last_price_update` timestamp for each position
3. Include `delta_events_today` count for context awareness

**REQ-TOOLINT-05-3 [U]** The system shall implement new tools for the Tool Registry (SPEC-009):

| Tool Name | Description | Input | Output |
|---|---|---|---|
| `get_delta_events` | Recent intraday events for a ticker | `ticker: str, event_type: str \| None, limit: int` | List of recent delta events |
| `get_market_prototype_similarity` | Current market prototype similarity | (none) | Top-3 prototype matches with similarity scores |
| `get_intraday_price_history` | Intraday price movements from deltas | `ticker: str` | Chronological price updates today |

**REQ-TOOLINT-05-4 [E]** When the Risk persona is invoked AND `prototype_risk_enabled=true`, the tool set shall additionally include `get_market_prototype_similarity`. The Risk persona can actively query prototype context to inform its verdict.

**REQ-TOOLINT-05-5 [E]** When any persona invokes `get_ticker_technicals` AND `jit_pipeline_enabled=true`, the response shall include a `freshness` object:
```json
{
  "data_source": "jit_merged",
  "base_snapshot_time": "2026-05-05T06:30:00+09:00",
  "last_delta_time": "2026-05-05T11:23:45+09:00",
  "deltas_applied": 47,
  "staleness_seconds": 5
}
```

**REQ-TOOLINT-05-6 [S]** While `jit_pipeline_enabled=false`, all tools shall return data from the existing static sources (SPEC-009 behavior unchanged). While `jit_pipeline_enabled=true`, tools shall use the Merge Engine for freshest data.

**REQ-TOOLINT-05-7 [U]** Tool response size for merged data shall not exceed 2000 tokens per call. If merged state exceeds this limit, the tool shall summarize and provide a `truncated: true` flag with `full_events_count`.

---

### Module 6 — Migration, Feature Flags & Phased Rollout

**REQ-MIGR-06-1 [U]** The migration shall be phased:
- **Phase A** (Week 1): Deploy delta_events table + snapshots table + merge engine. Feature flag `jit_pipeline_enabled=false`. Unit tests pass. Cron continues unchanged.
- **Phase B** (Week 2): Deploy market_prototypes table + initial 10 prototypes + similarity engine. Feature flag `prototype_risk_enabled=false`. Prototype data validated.
- **Phase C** (Week 3): Enable `jit_pipeline_enabled=true`. Start WebSocket during market hours. Monitor delta ingestion, merge latency, data freshness. Tools return merged state.
- **Phase D** (Week 4): Enable `prototype_risk_enabled=true`. Risk persona receives ProtoHedge context. Monitor prototype alerts, exposure adjustments, Risk verdict changes.
- **Phase E** (Week 5+): Full operation. Remove experimental flags optional. Both modules active.

**REQ-MIGR-06-2 [U]** Feature flags in `system_state`:
- `jit_pipeline_enabled` (BOOLEAN, default: false) — Controls delta pipeline and merge engine activation
- `prototype_risk_enabled` (BOOLEAN, default: false) — Controls prototype similarity and dynamic exposure
- `jit_websocket_enabled` (BOOLEAN, default: false) — Controls KIS WebSocket connection (sub-flag of jit_pipeline)
- `jit_dart_polling_enabled` (BOOLEAN, default: false) — Controls DART polling (sub-flag of jit_pipeline)
- `jit_news_polling_enabled` (BOOLEAN, default: false) — Controls news RSS polling (sub-flag of jit_pipeline)

**REQ-MIGR-06-3 [E]** When feature flags are toggled via Telegram commands, the system shall:
- `/jit on|off` — Toggle `jit_pipeline_enabled` (master switch)
- `/jit ws on|off` — Toggle WebSocket only
- `/jit dart on|off` — Toggle DART polling only
- `/jit news on|off` — Toggle news polling only
- `/prototype on|off` — Toggle `prototype_risk_enabled`
- All toggles: write `audit_log`, confirm via Telegram within 5 seconds

**REQ-MIGR-06-4 [S]** While `jit_pipeline_enabled=false`, the system shall operate identically to pre-SPEC-011 behavior (SPEC-009/010 tools use static data only). No WebSocket connections, no delta ingestion, no merge operations.

**REQ-MIGR-06-5 [S]** While `prototype_risk_enabled=false`, the Risk persona shall NOT receive ProtoHedge context. The `get_market_prototype_similarity` tool shall not be included in Risk's tool set. Static limits are the only enforcement.

**REQ-MIGR-06-6 [U]** Rollback procedure (any phase):
1. Set affected feature flags to `false` via Telegram or direct DB update
2. System immediately reverts to pre-SPEC-011 behavior for that module
3. No data loss: delta_events and prototype tables remain for re-activation
4. WebSocket gracefully disconnects if running

**REQ-MIGR-06-7 [N]** The system shall NOT delete delta_events, snapshots, or market_prototypes tables during rollback. Rollback only disables active processing; re-activation is instant.

**REQ-MIGR-06-8 [U]** Monitoring metrics for Phase C/D validation:
- Delta ingestion rate (events/minute) vs expected
- Merge latency P50/P95/P99
- WebSocket uptime percentage
- Tool response freshness (seconds since last delta)
- Prototype similarity computation latency
- Daily cost impact (embedding API calls for prototypes)

---

### Non-Functional Requirements

**REQ-NFR-11-1 [U, Performance]** Delta event ingestion shall handle at least 100 events/second sustained (well above expected 5000/day peak). Database write batching: 10 events per batch insert.

**REQ-NFR-11-2 [U, Performance]** Merge Engine cached read shall respond in < 1ms. Cold merge shall complete in < 100ms for up to 10,000 un-merged deltas.

**REQ-NFR-11-3 [U, Performance]** Prototype similarity search (embedding generation + pgvector query) shall complete in < 500ms per invocation.

**REQ-NFR-11-4 [U, Storage]** Delta events table growth: ~5000 events/day x 7 days retention = ~35,000 rows. With cleanup job, table stays bounded. Estimated storage: < 50MB.

**REQ-NFR-11-5 [U, Reliability]** KIS WebSocket uptime during market hours shall be >= 95% (measured as connected_minutes / market_minutes). If below 95% for 3 consecutive days, emit Telegram alert for investigation.

**REQ-NFR-11-6 [U, Cost]** Additional monthly cost from SPEC-011:
- Embedding API for prototypes: negligible (~100 embeddings × $0.06/M tokens < $0.01)
- Embedding API for current state (per-cycle): ~10 queries/day × 22 days × $0.06/M ≈ < $0.50/month
- KIS WebSocket: free (included in KIS Developers subscription)
- DART polling: free (within daily limit)
- Total additional monthly cost: < $1 (embedding only)

**REQ-NFR-11-7 [U, Observability]** All JIT pipeline operations, prototype computations, and dynamic exposure adjustments shall be logged to `audit_log` with appropriate event types:
- `DELTA_PIPELINE_STARTED`, `DELTA_PIPELINE_STOPPED`
- `WEBSOCKET_CONNECTED`, `WEBSOCKET_DISCONNECTED`, `WEBSOCKET_RECONNECT`
- `DART_POLL_COMPLETED`, `NEWS_POLL_COMPLETED`
- `MERGE_SLOW`, `MERGE_COMPLETED`
- `PROTOTYPE_SIMILARITY_COMPUTED`, `PROTOTYPE_ALERT_TRIGGERED`
- `DYNAMIC_CEILING_APPLIED`

---

## Specifications (Implementation Summary)

### DB Schema Changes (Migration v12)

```sql
-- Delta Event Pipeline
CREATE TABLE snapshots (
    id BIGSERIAL PRIMARY KEY,
    snapshot_type TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    file_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    delta_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE delta_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    ticker TEXT,
    payload JSONB NOT NULL,
    event_ts TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    snapshot_id BIGINT REFERENCES snapshots(id),
    merged BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX idx_delta_events_type_ts ON delta_events(event_type, event_ts DESC);
CREATE INDEX idx_delta_events_ticker_ts ON delta_events(ticker, event_ts DESC) WHERE ticker IS NOT NULL;
CREATE INDEX idx_delta_events_snapshot ON delta_events(snapshot_id) WHERE merged = false;

-- Market Prototype Library
CREATE TABLE market_prototypes (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    time_period_start DATE NOT NULL,
    time_period_end DATE NOT NULL,
    market_conditions JSONB NOT NULL,
    key_indicators JSONB NOT NULL,
    outcome JSONB NOT NULL,
    risk_recommendation JSONB NOT NULL,
    embedding vector(1024) NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_prototypes_category ON market_prototypes(category);
CREATE INDEX idx_prototypes_embedding ON market_prototypes
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- Prototype Similarity Log
CREATE TABLE prototype_similarity_log (
    id BIGSERIAL PRIMARY KEY,
    cycle_kind TEXT NOT NULL,
    current_state_embedding vector(1024),
    top_matches JSONB NOT NULL,
    applied_ceiling_pct REAL,
    static_limit_pct REAL NOT NULL DEFAULT 80.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_proto_sim_log_ts ON prototype_similarity_log(created_at DESC);

-- Feature flags in system_state
ALTER TABLE system_state ADD COLUMN jit_pipeline_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE system_state ADD COLUMN jit_websocket_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE system_state ADD COLUMN jit_dart_polling_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE system_state ADD COLUMN jit_news_polling_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE system_state ADD COLUMN prototype_risk_enabled BOOLEAN NOT NULL DEFAULT false;
```

### New Module Structure

```
src/trading/jit/
├── __init__.py
├── pipeline.py           # Delta Event Pipeline orchestrator (start/stop)
├── websocket.py          # KIS WebSocket connection manager
├── dart_poller.py        # DART disclosure polling (5-min interval)
├── news_poller.py        # News RSS polling (10-min interval)
├── merge.py              # Snapshot + Delta Merge Engine
├── cache.py              # In-memory merged state cache (TTL-based)
└── cleanup.py            # Nightly delta cleanup job

src/trading/prototypes/
├── __init__.py
├── library.py            # Prototype CRUD operations
├── similarity.py         # Current state vs prototype similarity computation
├── exposure.py           # Dynamic exposure ceiling calculation
├── extractor.py          # Automated prototype extraction from historical data
└── seed.py               # Initial 10 prototype seed data
```

### Modified Modules

- `src/trading/tools/market_tools.py` — `get_ticker_technicals`, `get_ticker_flows` enhanced with merge engine
- `src/trading/tools/portfolio_tools.py` — `get_portfolio_status` enhanced with real-time P&L
- `src/trading/tools/registry.py` — New tools: `get_delta_events`, `get_market_prototype_similarity`, `get_intraday_price_history`
- `src/trading/personas/orchestrator.py` — Inject ProtoHedge context into Risk persona input
- `src/trading/scheduler/daily.py` — Start/stop JIT pipeline at market open/close
- `src/trading/reports/daily_report.py` — ProtoHedge summary section + JIT freshness stats
- `src/trading/bot/telegram_bot.py` — `/jit`, `/prototype`, `/prototype-status` commands
- `src/trading/contexts/build_*.py` — Create snapshot record after context generation
- `src/trading/config.py` — JIT/prototype configuration constants
- `src/trading/db/migrations/012_jit_prototypes.sql` — Above schema

### Compatibility with SPEC-008 + SPEC-009 + SPEC-010

- **Prompt Caching (SPEC-008)**: Tool definitions including new tools remain cached. Merge engine results are dynamic (not cached by Anthropic API — this is correct behavior).
- **Tool-calling (SPEC-009)**: Existing tools enhanced transparently. New tools added to registry. No breaking changes to tool-use loop.
- **pgvector (SPEC-010)**: Prototype embeddings reuse same pgvector extension, same embedding model configuration, same pipeline pattern. Separate table (`market_prototypes`) from context embeddings (`context_embeddings`).
- **Model Router (SPEC-010)**: ProtoHedge context injection is independent of model routing. Risk persona always uses Sonnet regardless.
- **Reflection Loop (SPEC-009)**: Works identically. ProtoHedge context is part of Risk's input whether evaluating original or revised signal.

### Dependent SPECs

This SPEC requires:
- SPEC-TRADING-009 (Tool-calling) — Tool registry and executor must be operational
- SPEC-TRADING-010 (pgvector) — pgvector extension and embedding pipeline must be active
- SPEC-TRADING-007 (Static Context) — Cron context builds provide base snapshots
- SPEC-TRADING-001 (Core system) — Risk limits, personas, orchestrator

---

## Traceability

| REQ ID | Module | Implementation Location (planned) | Verification |
|---|---|---|---|
| REQ-DELTA-01-1~12 | M1 (Delta Pipeline) | `src/trading/jit/*`, `db/migrations/012_*` | M1 scenarios |
| REQ-MERGE-02-1~8 | M2 (Merge Engine) | `src/trading/jit/merge.py`, `jit/cache.py` | M2 scenarios |
| REQ-PROTO-03-1~10 | M3 (Prototype Library) | `src/trading/prototypes/*` | M3 scenarios |
| REQ-DYNRISK-04-1~10 | M4 (Dynamic Risk) | `src/trading/prototypes/exposure.py`, `personas/orchestrator.py` | M4 scenarios |
| REQ-TOOLINT-05-1~7 | M5 (Tool Integration) | `src/trading/tools/*` | M5 scenarios |
| REQ-MIGR-06-1~8 | M6 (Migration) | `system_state`, `bot/telegram_bot.py` | M6 scenarios |
| REQ-NFR-11-1~7 | Cross-cutting | All modules | NFR scenarios |

---

## Future Scope (Out of Scope for SPEC-TRADING-011)

- **KIS WebSocket for live mode** — This SPEC covers paper mode WebSocket only. Live mode WebSocket (different endpoint/auth) is M6 scope.
- **Real-time event trigger enhancement** — Current +/-3% trigger threshold could be made dynamic based on prototype similarity (e.g., tighter threshold during crash-similar markets).
- **Prototype auto-detection** — Automated detection of "we are entering a new prototype-worthy scenario" and real-time prototype creation.
- **Cross-market prototype correlation** — Compare Korean market to US/global prototypes (e.g., "US market 3 days ago looked like current KR market").
- **Minute-level candle reconstruction** — Currently deltas track discrete events. Future: reconstruct minute-level OHLCV candles from WebSocket data for intraday technical analysis.
- **Delta compression** — For long market sessions, compress old intraday deltas into hourly summaries while keeping recent deltas granular.
- **ProtoHedge for Portfolio persona** — Currently only Risk persona uses prototypes. Portfolio persona could use them for sector allocation guidance.
