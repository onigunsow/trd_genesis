---
id: SPEC-TRADING-012
type: acceptance
title: "Acceptance Criteria — Event-CAR Modeling + Volatility-Adjusted Dynamic Thresholds"
---

# SPEC-TRADING-012 Acceptance Criteria

## Module 1 — Event-CAR Historical Database

### Scenario M1-1: Bootstrap CAR computation from historical data

```gherkin
Given the ohlcv table contains KOSPI/KOSDAQ daily data from 2019-01-01
  And the disclosures table contains DART filings from 2022-01-01
When the operator runs `trading car-bootstrap`
Then the event_car_history table is populated with CAR values
  And each row contains car_1d, car_5d, car_10d where data permits
  And benchmark_return fields use KOSPI for KOSPI stocks and KOSDAQ for KOSDAQ stocks
  And no lookahead data is used (CAR(T) uses only returns from T+1 onward)
  And the bootstrap covers event types: price_spike, disclosure, vix_shock, fx_shock
```

### Scenario M1-2: Daily rolling CAR update

```gherkin
Given car_filter_enabled is true
  And today is a trading day with market closed at 15:30 KST
When the post-market job runs at 16:30 KST
Then car_1d is computed for events from 1 trading day ago
  And car_5d is updated for events from 5 trading days ago
  And car_10d is updated for events from 10 trading days ago
  And audit_log contains event 'CAR_DAILY_UPDATE' with count of rows updated
```

### Scenario M1-3: CAR computation correctness

```gherkin
Given ticker "005930" had a +4% price spike on 2025-01-15
  And KOSPI index returned +0.5% on 2025-01-16
  And ticker "005930" returned +2.0% on 2025-01-16
When CAR is computed for this event
Then abnormal_return(2025-01-16) = 2.0% - 0.5% = 1.5%
  And car_1d = 1.5%
  And benchmark_return_1d = 0.5%
```

### Scenario M1-4: Data gap handling

```gherkin
Given ticker "123456" was halted (no trading) on days T+3 and T+4
When computing car_5d for an event on day T
Then the system uses trading days only (skips halted days)
  And car_5d uses the next 5 actual trading days (may span calendar days > 5)
```

---

## Module 2 — CAR Prediction Engine

### Scenario M2-1: Prediction with sufficient history

```gherkin
Given event_car_history contains 25 "disclosure/earnings" events for sector "semiconductor"
  And the mean car_5d for these events is +2.3% with std 1.1%
When a new "disclosure/earnings" event occurs for ticker "000660" (semiconductor sector)
Then the CAR prediction engine returns:
  | Field | Value |
  | predicted_car_5d | approximately +2.3% (weighted by recency and magnitude) |
  | confidence | > 0.5 (sufficient sample) |
  | sample_count | 25 |
  And the response includes top-5 most similar historical events
```

### Scenario M2-2: Prediction with insufficient history

```gherkin
Given event_car_history contains only 5 "disclosure/governance" events for sector "biotech"
When a new "disclosure/governance" event occurs for a biotech ticker
Then the CAR prediction engine returns:
  | Field | Value |
  | confidence | 0.0 |
  | sample_count | 5 |
  And the system falls through to broader event_type match (without subtype filter)
  And if broader match also < 10 samples, confidence remains 0.0
```

### Scenario M2-3: Recency weighting

```gherkin
Given event_car_history contains 30 "price_spike/positive_3pct" events
  And 10 events from last 6 months have mean car_5d = +1.8%
  And 20 events from 6-24 months ago have mean car_5d = +0.5%
When prediction is computed
Then the predicted_car_5d is closer to +1.8% than +0.5% (recency-weighted)
  And recent events receive approximately 2x the weight of older events
```

### Scenario M2-4: Statistics cache refresh

```gherkin
Given the nightly CAR computation completes successfully
When the event_car_stats table is refreshed
Then each unique (event_type, event_subtype, sector) combination has updated statistics
  And sample_count, mean, std, and median_abs_car_5d are recomputed
  And updated_at timestamp is refreshed
```

---

## Module 3 — Smart Event Filter

### Scenario M3-1: High-CAR event passes filter

```gherkin
Given car_filter_enabled is true
  And CAR_FILTER_THRESHOLD is 0.015 (1.5%)
  And a DART earnings disclosure arrives for ticker "000660"
  And the predicted |car_5d| is 2.8% with confidence 0.75
When the Smart Event Filter evaluates this event
Then the filter decision is "PASS"
  And Decision persona is invoked with Event-CAR context injected
  And event_filter_log records decision="PASS" with predicted_car_5d=0.028
  And audit_log records event "EVENT_CAR_PASSED"
```

### Scenario M3-2: Low-CAR event is blocked

```gherkin
Given car_filter_enabled is true
  And CAR_FILTER_THRESHOLD is 0.015 (1.5%)
  And a routine DART governance disclosure arrives for ticker "005930"
  And the predicted |car_5d| is 0.4% with confidence 0.8
When the Smart Event Filter evaluates this event
Then the filter decision is "BLOCK"
  And Decision persona is NOT invoked (token savings)
  And event_filter_log records decision="BLOCK"
  And audit_log records event "EVENT_CAR_FILTERED"
```

### Scenario M3-3: Low-confidence event passes through (conservative)

```gherkin
Given car_filter_enabled is true
  And a new event type occurs with confidence 0.3 (insufficient history)
When the Smart Event Filter evaluates this event
Then the filter decision is "PASS_LOW_CONFIDENCE"
  And Decision persona IS invoked (conservative pass-through)
  And event_filter_log records decision="PASS_LOW_CONFIDENCE"
```

### Scenario M3-4: Safety-critical events bypass filter

```gherkin
Given car_filter_enabled is true
  And a circuit breaker condition approaches (daily loss at -0.7%)
When an event trigger fires for the approaching limit
Then the Smart Event Filter does NOT evaluate this event
  And the event proceeds directly to Decision+Risk pipeline
  And the filter is bypassed for safety-critical conditions
```

### Scenario M3-5: CAR context injected into Decision persona

```gherkin
Given an event passes the Smart Event Filter with PASS decision
When Decision persona is invoked
Then the input contains an "[Event-CAR Context]" section
  And the section includes: event_type, predicted_car_5d, confidence, sample_count
  And the section includes: top-3 most similar historical events with actual CARs
  And the section includes: interpretation (positive/negative/neutral impact)
```

### Scenario M3-6: Filter disabled via feature flag

```gherkin
Given car_filter_enabled is false
When any event trigger fires
Then all events pass directly to Decision persona (no filtering)
  And no event_filter_log entries are created
  And no Event-CAR context is injected
  And behavior is identical to pre-SPEC-012
```

### Scenario M3-7: Daily report filter summary

```gherkin
Given car_filter_enabled is true
  And during today's session: 8 events triggered, 3 passed, 4 blocked, 1 low-confidence pass
When the daily report is generated at 16:00 KST
Then the report includes an "[Event-CAR Filter]" section
  And shows: events triggered=8, passed (high CAR)=3, blocked (low CAR)=4, passed (low confidence)=1
  And shows: estimated token savings from blocked invocations
```

---

## Module 4 — Volatility Calculator (ATR-based)

### Scenario M4-1: Standard ATR computation

```gherkin
Given ohlcv table contains 30 trading days of data for ticker "005930"
  And the data includes valid High, Low, Close prices
When ATR is computed for this ticker
Then atr_14 is the 14-day EMA of True Range values
  And atr_pct = atr_14 / latest_close * 100
  And the result is stored in atr_cache with today's date
```

### Scenario M4-2: True Range formula correctness

```gherkin
Given for day T:
  | Field | Value |
  | High(T) | 150,000 |
  | Low(T) | 145,000 |
  | Close(T-1) | 148,000 |
When True Range is computed for day T
Then TR(T) = max(150000-145000, |150000-148000|, |145000-148000|)
     = max(5000, 2000, 3000) = 5000
```

### Scenario M4-3: Volatility regime classification

```gherkin
Given ticker "005930" has 1-year ATR_pct history
  And 25th percentile = 1.2%, 75th = 2.5%, 90th = 3.5%
  And today's ATR_pct = 2.8%
When volatility regime is classified
Then regime = "high" (between 75th and 90th percentile)
```

### Scenario M4-4: Insufficient data handling

```gherkin
Given ticker "999999" is a recent IPO with only 3 days of data
When ATR computation is attempted
Then the system returns None (insufficient data, minimum 5 days required)
  And audit_log records event "ATR_INSUFFICIENT_DATA" for this ticker
  And dynamic threshold tool returns source="fixed_fallback" for this ticker
```

### Scenario M4-5: Nightly ATR update job

```gherkin
Given 35 tickers in positions + watchlist
When the nightly ATR update runs at 16:30 KST
Then atr_cache is updated for all 35 tickers
  And computation completes in < 5 seconds
  And audit_log records event "ATR_DAILY_UPDATE" with ticker count
```

---

## Module 5 — Dynamic Threshold Tool

### Scenario M5-1: Normal volatility ticker

```gherkin
Given ticker "005930" has ATR_pct = 1.8% (regime: "normal")
  And STOP_ATR_MULTIPLIER = 2.0, TAKE_ATR_MULTIPLIER = 3.0
When Decision persona calls get_dynamic_thresholds("005930")
Then the tool returns:
  | Field | Value |
  | atr_pct | 1.8 |
  | volatility_regime | "normal" |
  | stop_loss_pct | -3.6 |
  | take_profit_pct | +5.4 |
  | trailing_stop_pct | -2.7 |
  | effective_stop | -3.6 (within -15% guardrail) |
  | effective_take | +5.4 (within +30% guardrail) |
  | source | "dynamic" |
```

### Scenario M5-2: High volatility ticker with guardrail

```gherkin
Given ticker "BIOTECH" has ATR_pct = 8.0% (regime: "extreme")
  And STOP_ATR_MULTIPLIER = 2.0, MAX_STOP_LOSS_PCT = 15.0
When Decision persona calls get_dynamic_thresholds("BIOTECH")
Then raw stop_loss_pct = -16.0% (exceeds guardrail)
  And effective_stop = -15.0% (capped by MAX_STOP_LOSS_PCT)
  And take_profit_pct = +24.0%
  And effective_take = +24.0% (within +30% guardrail)
  And source = "dynamic"
```

### Scenario M5-3: Fallback to fixed thresholds

```gherkin
Given ticker "NEW_IPO" has no ATR data (recent listing, < 5 days)
When Decision persona calls get_dynamic_thresholds("NEW_IPO")
Then the tool returns:
  | Field | Value |
  | source | "fixed_fallback" |
  | fixed_fallback_stop | -7.0 |
  | fixed_fallback_take | "RSI>85" |
  And atr_pct = null
  And volatility_regime = null
```

### Scenario M5-4: Tool response time

```gherkin
Given atr_cache contains current data for the requested ticker
When get_dynamic_thresholds is called
Then the response is returned in < 20ms
  And no external API calls are made (pure cache lookup + computation)
```

### Scenario M5-5: Feature flag integration

```gherkin
Given dynamic_thresholds_enabled is true
When Decision persona is invoked (SPEC-009 tool-calling)
Then get_dynamic_thresholds is included in the available tool set
  And Decision persona can call it for any ticker it considers trading
```

### Scenario M5-6: Feature flag disabled

```gherkin
Given dynamic_thresholds_enabled is false
When Decision persona is invoked
Then get_dynamic_thresholds is NOT included in the tool set
  And Decision persona uses fixed rules from the original 7-rule policy
```

---

## Module 6 — Decision Persona Prompt Integration

### Scenario M6-1: Dynamic threshold prompt when enabled

```gherkin
Given dynamic_thresholds_enabled is true
When Decision persona system prompt is rendered
Then the prompt contains "Dynamic Threshold Rules" section
  And the prompt instructs: "Use effective_stop from get_dynamic_thresholds for each ticker"
  And the prompt instructs: "Use effective_take from get_dynamic_thresholds for each ticker"
  And the prompt retains rules (1)(4)(5)(6)(7) unchanged
  And the prompt references fixed -7%/RSI>85 as fallback only
```

### Scenario M6-2: Event-CAR context usage in prompt

```gherkin
Given car_filter_enabled is true
  And an event passes the Smart Event Filter
When Decision persona is invoked with CAR context
Then the prompt section "[Event-CAR Context]" is present in the input
  And Decision persona can reference predicted CAR in its signal rationale
  And the prompt instructs: "High |CAR| events deserve stronger conviction"
```

### Scenario M6-3: Risk persona independence preserved

```gherkin
Given both car_filter_enabled and dynamic_thresholds_enabled are true
When Risk persona is invoked (evaluating a Decision signal)
Then Risk persona prompt is NOT modified by SPEC-012
  And Risk persona evaluates independently (SoD preserved)
  And Risk persona may call get_dynamic_thresholds via tool (SPEC-009) for its own analysis
```

### Scenario M6-4: Prompt fallback when disabled

```gherkin
Given dynamic_thresholds_enabled is false
When Decision persona system prompt is rendered
Then the prompt uses original SPEC-001 fixed rules: -7% stop, RSI>85 take
  And no "Dynamic Threshold Rules" section is present
  And behavior is identical to pre-SPEC-012
```

---

## Module 7 — Migration & Feature Flags

### Scenario M7-1: Feature flag toggle via Telegram

```gherkin
Given operator sends "/car-filter on" from chat_id=60443392
When the system processes the command
Then system_state.car_filter_enabled is set to true
  And audit_log records event "CAR_FILTER_ENABLED"
  And Telegram confirmation is sent within 5 seconds
  And the Smart Event Filter begins evaluating subsequent events
```

### Scenario M7-2: Rollback via feature flag

```gherkin
Given car_filter_enabled is true and dynamic_thresholds_enabled is true
When operator sends "/car-filter off" and "/dyn-threshold off"
Then system immediately reverts to SPEC-001 behavior
  And all events pass through to Decision persona (no filtering)
  And Decision persona uses fixed -7%/RSI>85 rules
  And event_car_history and atr_cache tables remain intact (no data loss)
  And re-enabling later resumes with existing data
```

### Scenario M7-3: Phase A deployment validation

```gherkin
Given migration v13 is applied (tables created)
  And car-bootstrap has been run
When system starts with car_filter_enabled=false and dynamic_thresholds_enabled=false
Then the system operates identically to pre-SPEC-012
  And event_car_history contains historical CAR data (read-only at this phase)
  And atr_cache contains daily ATR values (computed but not used by Decision)
  And no persona behavior changes
```

### Scenario M7-4: Fixed rules always available as fallback

```gherkin
Given any combination of feature flag states
When config.py is loaded
Then FIXED_STOP_LOSS_PCT = -7.0 is always defined
  And FIXED_TAKE_PROFIT_RSI = 85 is always defined
  And these constants are never removed regardless of SPEC-012 feature state
```

### Scenario M7-5: Configuration via environment variables

```gherkin
Given CAR_FILTER_THRESHOLD=0.02 is set in .env
  And STOP_ATR_MULTIPLIER=2.5 is set in .env
When the system loads configuration
Then the CAR filter uses 2.0% threshold (not default 1.5%)
  And dynamic stop uses 2.5x ATR multiplier (not default 2.0x)
  And all configurable parameters are loaded from env vars with fallback to defaults
```

---

## Non-Functional Requirements

### Scenario NFR-1: CAR prediction performance

```gherkin
Given event_car_history contains 5000+ rows
When CAR prediction is requested for a single event
Then prediction completes in < 50ms
  And includes database query + weighted mean computation
```

### Scenario NFR-2: ATR batch computation performance

```gherkin
Given 40 tickers need ATR update
When nightly ATR job runs
Then all 40 tickers are computed in < 5 seconds total
  And results are batch-inserted into atr_cache
```

### Scenario NFR-3: Dynamic threshold tool performance

```gherkin
Given atr_cache contains current data
When get_dynamic_thresholds is called
Then response is returned in < 20ms
  And no external network calls are made
```

### Scenario NFR-4: Retrospective filter validation

```gherkin
Given car_filter_enabled has been active for 2 weeks
When weekly validation report is generated
Then for each blocked event:
  - Actual 5-day return is computed
  - Actual |CAR| is compared to threshold
  - If > 30% of blocked events had actual |CAR| > threshold: emit alert
Then the report includes filter precision, recall, and false negative rate
```

### Scenario NFR-5: Observability

```gherkin
Given SPEC-012 modules are active
When any CAR prediction, filter decision, ATR computation, or threshold serve occurs
Then a corresponding audit_log entry is created
  And the daily report includes summary statistics for all SPEC-012 operations
```

---

## Quality Gates

| Criterion | Target | Measurement |
|---|---|---|
| Test coverage (strategy/car/) | >= 85% | pytest --cov |
| Test coverage (strategy/volatility/) | >= 85% | pytest --cov |
| CAR computation correctness | 100% of test cases pass | Unit tests with known data |
| ATR computation correctness | 100% of test cases pass | Unit tests with known OHLCV |
| Filter decision correctness | All scenarios above pass | Integration tests |
| Tool response time | < 20ms for cached | Load tests |
| No regression | All SPEC-001 acceptance criteria still pass | Full test suite |
| Feature flag isolation | Disabled = identical to pre-SPEC-012 | A/B test with flags off |

---

## Definition of Done

- [ ] Migration v13 applied, all tables created
- [ ] CAR bootstrap completed (2022-01-01 to present)
- [ ] ATR computation verified for sample tickers
- [ ] Smart Event Filter unit + integration tests passing
- [ ] get_dynamic_thresholds tool registered and tested
- [ ] Decision persona prompt updated (feature-flagged)
- [ ] Telegram commands (/car-filter, /dyn-threshold) functional
- [ ] Daily report sections added (CAR filter summary, threshold stats)
- [ ] Feature flags default OFF (safe deployment)
- [ ] Phase A validation: all tests pass with flags OFF
- [ ] Phase B validation: CAR filter accuracy > 60% precision after 1 week
- [ ] Phase C/D validation: dynamic thresholds produce reasonable values for all positions
- [ ] No regression in existing SPEC-001 acceptance tests
- [ ] Coverage >= 85% for new modules
- [ ] audit_log events emitted for all SPEC-012 operations
