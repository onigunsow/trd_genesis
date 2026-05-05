# SPEC-TRADING-011 Acceptance Criteria

## Overview

Acceptance criteria for JIT State Reconstruction Pipeline + Prototype-based Risk Management.
Format: Given-When-Then (Gherkin-style) scenarios organized by module.

---

## Module 1 — Delta Event Pipeline

### Scenario M1-1: WebSocket Connection at Market Open

```gherkin
Given the system is running on a business day
  And jit_pipeline_enabled = true
  And jit_websocket_enabled = true
  And the current time is 09:00 KST
When the scheduler triggers market-open event
Then the system shall connect to KIS WebSocket
  And subscribe to price updates for all positions + watchlist tickers
  And write audit_log event "DELTA_PIPELINE_STARTED"
  And write audit_log event "WEBSOCKET_CONNECTED"
  And the connection shall be established within 5 seconds
```

### Scenario M1-2: Price Update Delta Ingestion

```gherkin
Given KIS WebSocket is connected
  And ticker "005930" (Samsung) is subscribed
When a price update message is received for "005930" with price 78500
Then the system shall insert a row into delta_events:
  | event_type   | price_update                    |
  | source       | kis_ws                          |
  | ticker       | 005930                          |
  | payload      | {ticker, price: 78500, ...}     |
  | event_ts     | message timestamp               |
  And update the in-memory price cache for "005930"
```

### Scenario M1-3: WebSocket Disconnect and Reconnect

```gherkin
Given KIS WebSocket is connected during market hours
When the WebSocket connection is unexpectedly dropped
Then the system shall attempt reconnection with exponential backoff:
  | attempt | delay  |
  | 1       | 1s     |
  | 2       | 2s     |
  | 3       | 4s     |
  | 4       | 8s     |
  And write audit_log event "WEBSOCKET_DISCONNECTED"
  And on successful reconnect, re-subscribe to all tickers
  And write audit_log event "WEBSOCKET_RECONNECT"
```

### Scenario M1-4: WebSocket Max Reconnect Failure

```gherkin
Given KIS WebSocket has failed to reconnect 10 times in current session
When the 10th reconnection attempt fails
Then the system shall:
  | action                                              |
  | Disable WebSocket for remainder of day              |
  | Write audit_log event "WEBSOCKET_MAX_RETRY"         |
  | Emit Telegram alert to chat_id 60443392             |
  | Continue operating with static data (graceful degradation) |
```

### Scenario M1-5: DART Disclosure Polling

```gherkin
Given jit_dart_polling_enabled = true
  And market is open (09:00-15:30 KST)
  And positions include ticker "005930"
When 5-minute DART polling interval triggers
  And DART API returns a new disclosure for "005930" with report_type "earnings"
Then the system shall insert into delta_events:
  | event_type   | disclosure                           |
  | source       | dart_api                             |
  | ticker       | 005930                               |
  | payload      | {title, report_type: earnings, ...}  |
  And since report_type is "earnings" (material), emit event trigger to orchestrator
```

### Scenario M1-6: Market Close Pipeline Shutdown

```gherkin
Given the JIT pipeline is running
  And current time is 15:30 KST (market close)
When the scheduler triggers market-close event
Then the system shall:
  | action                                                    |
  | Gracefully disconnect KIS WebSocket                        |
  | Stop DART polling                                          |
  | Stop news RSS polling                                      |
  | Mark un-merged deltas with current snapshot_id             |
  | Write audit_log "DELTA_PIPELINE_STOPPED" with day summary  |
  | Summary includes: price_events=X, disclosures=Y, news=Z   |
```

### Scenario M1-7: Non-Market Hours Rejection

```gherkin
Given the current time is Saturday 10:00 KST (non-market day)
When the JIT pipeline is accidentally triggered
Then the system shall immediately stop
  And write audit_log warning "DELTA_PIPELINE_REJECTED_NON_MARKET"
  And NOT connect WebSocket or start polling
```

### Scenario M1-8: Nightly Delta Cleanup

```gherkin
Given delta_events contains events from 10 days ago with merged=true
  And delta_events contains events from 3 days ago with merged=true
  And delta_events contains events from today with merged=false
When the nightly cleanup job runs at 03:00 KST
Then the system shall delete events where:
  | condition         | age > 7 days AND merged = true |
  | deleted           | 10-day-old events              |
  | preserved         | 3-day-old events (within 7 days) |
  | preserved         | today's un-merged events       |
```

### Scenario M1-9: Snapshot Creation on Cron Build

```gherkin
Given the cron job build_micro_context.py completes at 06:30
  And the generated file is data/contexts/micro_context.md
When the context build completes
Then the system shall:
  | action                                                |
  | Create new snapshots row with snapshot_type="micro"    |
  | Set content_hash to SHA256 of generated file           |
  | Mark all previous un-merged deltas for "micro" as merged=true |
  | Reset delta_count for the new snapshot                 |
```

---

## Module 2 — Snapshot + Delta Merge Engine

### Scenario M2-1: Cached Merge Read (Hot Path)

```gherkin
Given merged state cache for snapshot_type="micro" is valid (within 10s TTL)
  And the cache was computed 5 seconds ago
When a tool calls get_merged_state("micro")
Then the system shall return the cached merged state
  And response time shall be < 1ms
  And no database query shall be executed
```

### Scenario M2-2: Cold Merge (Cache Miss)

```gherkin
Given merged state cache for snapshot_type="micro" has expired (TTL > 10s)
  And the latest snapshot for "micro" was generated at 06:30
  And there are 200 un-merged delta_events since 06:30
When a tool calls get_merged_state("micro")
Then the system shall:
  | step | action                                               |
  | 1    | Load base snapshot (micro_context.md parsed content)  |
  | 2    | Query delta_events WHERE snapshot_id=current AND merged=false |
  | 3    | Apply deltas sequentially to base state               |
  | 4    | Cache the merged result with 10s TTL                  |
  | 5    | Return merged state                                   |
  And total response time shall be < 100ms
```

### Scenario M2-3: Price Delta Application

```gherkin
Given base snapshot has ticker "005930" with price 78000 (from 06:30)
  And delta_events contains:
  | event_ts | ticker | payload.price |
  | 10:15:00 | 005930 | 78200         |
  | 10:30:00 | 005930 | 78500         |
  | 11:00:00 | 005930 | 78300         |
When merge engine applies deltas
Then the merged state for "005930" shall have price=78300 (latest)
  And change_from_snapshot = +300 (+0.38%)
```

### Scenario M2-4: Single Ticker Query

```gherkin
Given merged state includes 40 tickers
When a tool calls get_ticker_current("005930")
Then the system shall return only ticker "005930" merged state:
  | field              | value                        |
  | price              | latest from deltas           |
  | volume             | accumulated from deltas      |
  | change_pct         | vs snapshot open price       |
  | disclosures_today  | list of new disclosures      |
  | news_today         | list of relevant news        |
  | last_delta_time    | timestamp of latest delta    |
```

### Scenario M2-5: Merge Performance Degradation Alert

```gherkin
Given a merge operation takes 250ms (exceeds 200ms threshold)
When this is the 3rd consecutive slow merge
Then the system shall:
  | action                                        |
  | Write audit_log "MERGE_SLOW" with duration    |
  | Emit Telegram alert suggesting delta compaction |
```

### Scenario M2-6: Read-Only Guarantee

```gherkin
Given base snapshot has content_hash "abc123"
  And delta_events table has 500 rows for current snapshot
When merge engine completes a merge operation
Then the snapshot content_hash shall still be "abc123" (unchanged)
  And delta_events row count shall still be 500 (unchanged)
  And no UPDATE or DELETE shall have been executed on source data
```

---

## Module 3 — Market Prototype Library

### Scenario M3-1: Initial Prototype Seeding

```gherkin
Given the system is freshly deployed with migration v12
When the prototype seed script runs
Then market_prototypes table shall contain exactly 10 rows:
  | name                    | category   |
  | 2024-08-crash           | crash      |
  | 2024-11-rally           | rally      |
  | 2020-03-covid-crash     | crash      |
  | 2020-04-covid-recovery  | recovery   |
  | 2022-rate-hike-bear     | correction |
  | 2023-ai-rally           | rally      |
  | 2024-04-sideways        | sideways   |
  | 2022-09-credit-crisis   | correction |
  | 2021-11-peak            | correction |
  | 2024-01-value-rotation  | rally      |
  And each row shall have a non-null embedding vector (1024 dimensions)
  And each row shall have is_active=true
```

### Scenario M3-2: Prototype Addition via CLI

```gherkin
Given a user wants to add a new prototype "2025-03-tariff-shock"
When the command "trading prototype-add" is executed with:
  | field               | value                           |
  | name                | 2025-03-tariff-shock            |
  | category            | correction                      |
  | time_period_start   | 2025-03-01                      |
  | time_period_end     | 2025-03-20                      |
  | description         | US tariff escalation impact     |
  | market_conditions   | {kospi_change: -6.5, ...}       |
Then the system shall:
  | action                                                |
  | Generate embedding from description + indicators       |
  | Insert into market_prototypes with is_active=true      |
  | Write audit_log "PROTOTYPE_ADDED"                      |
  | Emit Telegram: "Market prototype added: 2025-03-tariff-shock (correction)" |
```

### Scenario M3-3: Prototype Similarity Search

```gherkin
Given market_prototypes contains 10 active prototypes
  And the current market state has:
  | indicator            | value |
  | kospi_5d_change      | -7.5% |
  | vix                  | 32    |
  | usd_krw              | 1370  |
  | foreign_net_sell     | 8 days |
When prototype similarity is computed
Then the system shall:
  | step | action                                             |
  | 1    | Generate embedding for current state text           |
  | 2    | Query pgvector for cosine similarity (top 3)        |
  | 3    | Return results ordered by similarity descending     |
  And the result shall include prototype names, categories, and similarity scores
  And "2024-08-crash" should rank highest (most similar to current conditions)
```

### Scenario M3-4: Auto-Extract Draft Prototype

```gherkin
Given the user runs "trading prototype-extract --period 2025-01-15..2025-02-01"
When the extraction completes
Then the system shall:
  | action                                              |
  | Fetch historical data from pykrx/yfinance for period |
  | Compute market condition indicators automatically    |
  | Generate a draft prototype entry                     |
  | Set is_active=false (requires human review)          |
  | Display draft for user confirmation                  |
  And the prototype shall NOT be queryable until user sets is_active=true
```

---

## Module 4 — Dynamic Risk Exposure

### Scenario M4-1: High Crash Similarity Ceiling

```gherkin
Given prototype_risk_enabled = true
  And prototype similarity returns:
  | prototype       | category | similarity |
  | 2024-08-crash   | crash    | 0.82       |
  | 2022-rate-hike  | correct. | 0.68       |
When the dynamic exposure engine computes ceiling
Then the applied ceiling shall be 50% (0.80 <= similarity < 0.85 for crash)
  And prototype_similarity_log shall record:
  | applied_ceiling_pct | 50.0  |
  | static_limit_pct    | 80.0  |
  And Telegram alert shall be emitted:
  "ProtoHedge Alert: Current market 82% similar to 2024-08-crash. Dynamic ceiling: 50%."
```

### Scenario M4-2: Extreme Crash Similarity

```gherkin
Given prototype similarity returns:
  | prototype           | category | similarity |
  | 2020-03-covid-crash | crash    | 0.88       |
When the dynamic exposure engine computes ceiling
Then the applied ceiling shall be 30% (similarity >= 0.85 for crash)
  And Telegram alert shall be emitted with extreme warning
```

### Scenario M4-3: Rally Similarity (Expanded Ceiling)

```gherkin
Given prototype similarity returns:
  | prototype      | category | similarity |
  | 2024-11-rally  | rally    | 0.87       |
When the dynamic exposure engine computes ceiling
Then the advisory ceiling shall be 90% (>= 0.85 for rally)
  And this is ADVISORY only — static limit 80% remains as HARD max
  And Risk persona receives context that rally conditions suggest opportunity
```

### Scenario M4-4: No Significant Similarity

```gherkin
Given prototype similarity returns all matches < 0.75
When the dynamic exposure engine computes ceiling
Then no dynamic ceiling shall be applied
  And static limit of 80% remains sole enforcement
  And prototype_similarity_log records applied_ceiling_pct = NULL
```

### Scenario M4-5: Multiple Prototype Matches (Most Restrictive)

```gherkin
Given prototype similarity returns:
  | prototype           | category   | similarity | individual_ceiling |
  | 2024-08-crash       | crash      | 0.81       | 50%               |
  | 2022-09-credit      | correction | 0.77       | 60%               |
  | 2024-11-rally       | rally      | 0.76       | 90% (advisory)    |
When the dynamic exposure engine computes ceiling
Then the applied ceiling shall be 50% (most restrictive among >= 0.75 matches)
  And the rally advisory is overridden by crash/correction matches
```

### Scenario M4-6: ProtoHedge Context Injection to Risk Persona

```gherkin
Given prototype_risk_enabled = true
  And latest similarity result shows 2024-08-crash at 82% (ceiling 50%)
When the Risk persona is invoked for a Decision signal
Then Risk's input shall include:
  """
  [ProtoHedge Context]
  Current market similarity analysis:
  1. 2024-08-crash: 82% similar — Recommended ceiling: 50%
  2. 2022-09-credit-crisis: 68% similar — Below threshold
  Applied dynamic ceiling: 50% (vs static 80%)
  Reasoning: Yen carry trade unwind pattern + foreign net sell 8 days
  """
  And Risk persona can use this context in its APPROVE/HOLD/REJECT decision
```

### Scenario M4-7: Dynamic Ceiling Does NOT Override Static

```gherkin
Given static limit MAX_TOTAL_EXPOSURE_PCT = 80%
  And dynamic ceiling from ProtoHedge = 50%
  And current actual exposure = 45%
  And Decision persona signals a buy that would bring exposure to 55%
When code-rule limits check runs
Then the code-rule check shall PASS (55% < static 80%)
  And Risk persona shall receive ProtoHedge context showing ceiling 50%
  And Risk persona may REJECT based on 55% > 50% dynamic ceiling (its decision)
  And if Risk APPROVES despite dynamic ceiling, the trade proceeds (advisory only)
  But if actual exposure would exceed static 80%, code-rule HARD blocks regardless
```

### Scenario M4-8: Telegram Prototype Status Command

```gherkin
Given prototype_risk_enabled = true
  And latest similarity analysis completed at 09:30
When user sends "/prototype-status" via Telegram from chat_id 60443392
Then the system shall reply within 5 seconds with:
  """
  [ProtoHedge Status]
  Last computed: 09:30 KST

  Top-3 matches:
  1. 2024-08-crash (crash): 82% [ceiling: 50%]
  2. 2022-rate-hike (correction): 68% [below threshold]
  3. 2024-04-sideways (sideways): 55% [below threshold]

  Applied ceiling: 50% (static: 80%)
  Alerts today: 1
  """
```

---

## Module 5 — Tool Integration

### Scenario M5-1: Enhanced get_ticker_technicals (JIT Enabled)

```gherkin
Given jit_pipeline_enabled = true
  And base snapshot for "005930" shows price 78000 (from 06:30)
  And delta_events show latest price 78500 (from 10:30)
When Micro persona calls get_ticker_technicals(ticker="005930")
Then the response shall include:
  | field          | value                    |
  | price          | 78500 (merged, not 78000) |
  | data_source    | jit_merged               |
  | freshness.base | 2026-05-05T06:30:00+09:00 |
  | freshness.last_delta | 2026-05-05T10:30:00+09:00 |
  | freshness.staleness_seconds | < 15 |
  And technical indicators (MA, RSI, MACD) shall be computed using merged price history
```

### Scenario M5-2: Enhanced get_portfolio_status (Real-time P&L)

```gherkin
Given jit_pipeline_enabled = true
  And portfolio has position: 005930, 5 shares, avg_cost 78000
  And latest merged price for 005930 = 78500
When Decision persona calls get_portfolio_status()
Then the response shall include:
  | field                  | value                |
  | positions[0].unrealized_pnl | +2500 (5 × 500) |
  | positions[0].unrealized_pnl_pct | +0.64% |
  | positions[0].last_price_update | 10:30 KST |
  | delta_events_today | 47 |
```

### Scenario M5-3: New Tool get_delta_events

```gherkin
Given jit_pipeline_enabled = true
  And delta_events contains 5 events for ticker "005930" today
When Risk persona calls get_delta_events(ticker="005930", limit=3)
Then the response shall return the 3 most recent events:
  | event_type   | event_ts | payload.price |
  | price_update | 10:30    | 78500         |
  | price_update | 10:15    | 78200         |
  | disclosure   | 09:45    | {title: "..."}|
```

### Scenario M5-4: New Tool get_market_prototype_similarity

```gherkin
Given prototype_risk_enabled = true
  And Risk persona's tool set includes get_market_prototype_similarity
When Risk persona calls get_market_prototype_similarity()
Then the response shall include:
  | field                    | value                    |
  | top_matches[0].name     | 2024-08-crash            |
  | top_matches[0].similarity | 0.82                   |
  | top_matches[0].ceiling_pct | 50                    |
  | applied_ceiling_pct     | 50                       |
  | static_limit_pct        | 80                       |
```

### Scenario M5-5: Graceful Degradation (JIT Disabled)

```gherkin
Given jit_pipeline_enabled = false
When any persona calls get_ticker_technicals(ticker="005930")
Then the response shall use existing static data sources (SPEC-009 behavior)
  And response shall NOT include freshness object
  And data_source shall be "static" (not "jit_merged")
```

### Scenario M5-6: Tool Response Size Limit

```gherkin
Given merged state for a ticker has accumulated 100+ disclosure events today
When a tool response would exceed 2000 tokens
Then the response shall be truncated with:
  | field            | value                    |
  | truncated        | true                     |
  | full_events_count | 107                     |
  | returned_count   | 20 (most recent)         |
```

---

## Module 6 — Migration & Feature Flags

### Scenario M6-1: Phase A Deployment (Infrastructure Only)

```gherkin
Given migration v12 has been applied
  And jit_pipeline_enabled = false (default)
  And prototype_risk_enabled = false (default)
When the system starts
Then delta_events table shall exist but be empty
  And snapshots table shall exist
  And market_prototypes table shall exist but be empty
  And all existing SPEC-009/010 functionality shall work unchanged
  And no WebSocket connections shall be attempted
```

### Scenario M6-2: Feature Flag Toggle via Telegram

```gherkin
Given user sends "/jit on" from chat_id 60443392
When the system processes the command
Then the system shall:
  | action                                          |
  | Update system_state.jit_pipeline_enabled = true |
  | Write audit_log "JIT_PIPELINE_ENABLED"          |
  | Reply: "JIT Pipeline enabled. WebSocket will connect at next market open." |
  And response time shall be < 5 seconds
```

### Scenario M6-3: Granular WebSocket Toggle

```gherkin
Given jit_pipeline_enabled = true
  And user sends "/jit ws off" from chat_id 60443392
When the system processes the command
Then the system shall:
  | action                                           |
  | Update system_state.jit_websocket_enabled = false |
  | If WebSocket is connected, gracefully disconnect   |
  | DART and news polling continue unchanged           |
  | Reply: "WebSocket disabled. DART/News polling continues." |
```

### Scenario M6-4: Rollback Procedure

```gherkin
Given jit_pipeline_enabled = true
  And prototype_risk_enabled = true
  And system is experiencing issues
When user sends "/jit off" followed by "/prototype off"
Then the system shall:
  | action                                              |
  | Set jit_pipeline_enabled = false                    |
  | Disconnect WebSocket immediately                    |
  | Stop DART/news polling immediately                  |
  | Set prototype_risk_enabled = false                  |
  | Risk persona no longer receives ProtoHedge context  |
  | All tools revert to static data sources             |
  | delta_events and market_prototypes data preserved   |
  | System operates identically to pre-SPEC-011        |
```

### Scenario M6-5: Phase D Monitoring Metrics

```gherkin
Given the system has been running Phase D for 3 trading days
When the daily report is generated
Then the JIT/ProtoHedge section shall include:
  """
  [JIT Pipeline]
  WebSocket uptime: 98.2% (6h 23m / 6h 30m)
  Delta events today: 4,821 (price: 4,752, disclosures: 12, news: 57)
  Merge latency: P50=2ms, P95=15ms, P99=45ms
  Tool freshness: avg 4.2s since last delta

  [ProtoHedge]
  Today's highest similarity: 2024-08-crash (78%)
  Applied ceiling: 60% (static: 80%)
  Prototype alerts: 0 (threshold >= 80% not reached)
  Similarity computations: 8
  """
```

---

## Non-Functional Requirements

### Scenario NFR-1: Delta Ingestion Throughput

```gherkin
Given 40 tickers subscribed via WebSocket
  And peak market activity generating 100 events/second burst
When events arrive at peak rate
Then all events shall be persisted to delta_events within 50ms per batch
  And no events shall be dropped
  And batch size shall be 10 events per insert
```

### Scenario NFR-2: Merge Engine Latency

```gherkin
Given 5000 un-merged delta events for a snapshot
When a cold merge is triggered
Then the merge shall complete in < 100ms
  And subsequent cached reads shall respond in < 1ms
  And memory usage shall not exceed 50MB
```

### Scenario NFR-3: Prototype Similarity Latency

```gherkin
Given 20 active prototypes in market_prototypes
When prototype similarity is computed
Then total latency (embedding generation + pgvector query) shall be < 500ms
  And if latency exceeds 500ms, use cached previous result
```

### Scenario NFR-4: WebSocket Reliability

```gherkin
Given 5 consecutive trading days
When WebSocket uptime is measured
Then average uptime during market hours shall be >= 95%
  And if uptime < 95% for 3 consecutive days, Telegram alert shall be emitted
```

### Scenario NFR-5: Cost Neutrality

```gherkin
Given SPEC-011 is fully operational (Phase E)
When monthly cost is calculated
Then additional cost from SPEC-011 shall be < 1,000 KRW/month
  And this is limited to embedding API calls only
  And KIS WebSocket, DART polling, and news polling shall be free
```

### Scenario NFR-6: Storage Bounds

```gherkin
Given 7-day delta retention policy
  And ~5000 events/day average
When storage is measured after 30 days of operation
Then delta_events table shall contain <= 35,000 rows (~7 days worth)
  And total storage (delta_events + prototypes + similarity_log) < 100MB
```

---

## Quality Gates

### Definition of Done

- [ ] All 6 modules implemented with feature flags
- [ ] Migration v12 applied successfully (delta_events, snapshots, market_prototypes, prototype_similarity_log, system_state flags)
- [ ] 10 initial market prototypes seeded with valid embeddings
- [ ] KIS WebSocket connects and receives price updates during paper market hours
- [ ] DART polling retrieves disclosures at 5-minute intervals
- [ ] Merge Engine returns merged state in < 100ms cold, < 1ms cached
- [ ] Prototype similarity computation completes in < 500ms
- [ ] Dynamic exposure ceiling correctly applied (tighten only, never loosen beyond static)
- [ ] All existing SPEC-009/010 tools work unchanged when JIT disabled (backward compat)
- [ ] Feature flags toggle correctly via Telegram commands
- [ ] Daily report includes JIT + ProtoHedge summary sections
- [ ] Rollback procedure tested: /jit off + /prototype off reverts to pre-SPEC-011 behavior
- [ ] Unit test coverage >= 85% for all new modules
- [ ] Integration test: full cycle (market open → delta ingestion → merge → tool query → persona invocation → market close)
- [ ] Audit log captures all new event types

### Verification Methods

| Method | Coverage |
|---|---|
| Unit tests (pytest) | Merge engine, prototype similarity, exposure calculation, delta ingestion |
| Integration tests | WebSocket connection lifecycle, DART polling cycle, tool enhancement verification |
| Manual verification | Telegram commands, daily report format, prototype seed validation |
| Shadow testing | Run JIT-enabled alongside static for 1 week, compare tool responses |
| Load testing | Simulate 100 events/second burst, verify no drops |
