---
id: SPEC-TRADING-012
version: 0.1.0
status: draft
created: 2026-05-05
updated: 2026-05-05
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "Algorithm & Strategy Upgrade — Event-CAR Modeling + Volatility-Adjusted Dynamic Thresholds"
related_specs:
  - SPEC-TRADING-001
  - SPEC-TRADING-009
  - SPEC-TRADING-011
---

# SPEC-TRADING-012 — Algorithm & Strategy Upgrade

## HISTORY

| Date | Version | Change | Author |
|---|---|---|---|
| 2026-05-05 | 0.1.0 | Draft — Event-CAR Modeling + Volatility-Adjusted Dynamic Thresholds (7 modules) | onigunsow |

## Scope Summary

This SPEC is the final piece of the 4-SPEC upgrade series (009-010-011-012). While prior SPECs improved the LLM architecture (009: Tool-calling + Reflection), cost optimization (010: Haiku hybrid + pgvector), and infrastructure (011: JIT pipeline + ProtoHedge), this SPEC upgrades the **core trading strategy and algorithms** themselves.

Two complementary strategy upgrades:

1. **Event-CAR Modeling** — Replace binary event triggers (SPEC-001 REQ-EVENT-04-6: +/-3% or DART) with a predictive filter based on Cumulative Abnormal Return (CAR) analysis. Only trigger Decision persona when historical data predicts the event will have material price impact (|predicted CAR| > threshold). Reduces noise, saves tokens, improves signal quality.

2. **Volatility-Adjusted Dynamic Thresholds** — Replace fixed rules in Decision persona (SPEC-001 REQ-PERSONA-04-11: RSI>85 take-profit, -7% stop-loss) with per-ticker ATR-normalized thresholds. Volatile stocks get wider bands, stable stocks get tighter bands. Decision persona receives computed thresholds as tool data rather than relying on hardcoded rules.

**Key Distinction from SPEC-011 ProtoHedge**:
- SPEC-011 ProtoHedge: Market-level regime detection adjusting overall exposure ceiling
- SPEC-012 Event-CAR: Per-event filtering reducing noise in trigger system
- SPEC-012 Dynamic Thresholds: Per-ticker volatility-normalized trade exit rules

**Research Basis**:
- **Janus-Q** (Event-driven trading): Treats events as primary decision units; filters events by predicted CAR magnitude
- **Trading-R1** (Volatility labeling): Uses volatility-driven dynamic labeling instead of fixed threshold rules

---

## Environment

- Existing SPEC-TRADING-001 infrastructure — Postgres 16-alpine, Anthropic API, Telegram, Docker compose
- SPEC-009 Tool-calling architecture active (`TOOL_CALLING_ENABLED=true`)
- SPEC-011 JIT pipeline active (`jit_pipeline_enabled=true`) providing real-time delta events
- SPEC-011 delta_events table provides intraday event stream for CAR trigger source
- Existing data adapters: pykrx (OHLCV, 2019-01-01+), dart_adapter (disclosures)
- Existing OHLCV cache in Postgres (`ohlcv` table, M3 backfill from 2019-01-01)
- Existing `disclosures` table (DART data, M3)
- Decision persona system prompt (`decision.jinja`) — currently encodes fixed 7-rule policy
- Tool Registry (`src/trading/tools/registry.py`) for new tool registration

## Assumptions

1. Historical OHLCV data (2019-01-01 to present) in the `ohlcv` table is sufficient to compute meaningful CAR statistics for common event types (disclosure categories, price spikes). Minimum 2 years of data provides ~500 trading days of event observations.
2. DART disclosure categories (`report_type` field) are stable and meaningful enough to group similar events for CAR computation. Key categories: major_event, earnings, governance, equity_change, investment.
3. 14-day ATR (Average True Range) computed from existing OHLCV data provides a reliable volatility measure for Korean stocks. ATR is widely used in quantitative trading for position sizing and threshold normalization.
4. The CAR prediction approach (historical average CAR by event type) is a reasonable first approximation. Future iterations may incorporate ML-based prediction, but this SPEC uses statistical averaging.
5. Event-CAR filtering is an ADDITIONAL layer on top of existing +/-3% trigger (not a replacement). The existing trigger fires first, then CAR filter decides whether to escalate to Decision persona.
6. Per-ticker ATR data updates once daily (post-market) from existing OHLCV pipeline. Intraday ATR recalculation is out of scope.
7. Feature flags allow safe rollout: fixed rules remain as ultimate fallback when features are disabled.
8. CAR threshold (default 1.5%) and ATR multipliers (default stop: 2x, take: 3x) are configurable via environment variables for tuning.

## Robustness Principles (SPEC-001 6-Principle Inheritance)

This SPEC inherits SPEC-TRADING-001 v0.2.0's 6 Robustness Principles:

- **External dependency failure assumption (Principle 1)** — CAR computation depends on historical data; if data is insufficient for a ticker/event, fall back to existing binary trigger behavior.
- **State integrity via transactions (Principle 2)** — CAR database entries and ATR cache updates are idempotent (upsert by ticker+date).
- **Failure silence prohibition (Principle 3)** — CAR computation failures, ATR calculation errors all emit audit_log + Telegram alert. Feature degrades gracefully to fixed rules.
- **Auto-recovery with human notification (Principle 4)** — Insufficient CAR history for new event types defaults to "pass-through" (trigger fires as before) with warning logged.
- **Tests harden the specification (Principle 5)** — CAR computation, ATR calculator, dynamic threshold tool require 85%+ coverage.
- **Graceful degradation (Principle 6)** — If CAR filter fails, all events pass through (conservative). If ATR unavailable, fixed thresholds apply (current behavior preserved).

---

## Requirements (EARS)

EARS notation: **U**=Ubiquitous, **E**=Event-driven, **S**=State-driven, **O**=Optional, **N**=Unwanted

---

### Module 1 — Event-CAR Historical Database

**REQ-CAR-01-1 [U]** The system shall implement an Event-CAR Historical Database under `src/trading/strategy/car/` that records the measured Cumulative Abnormal Return for each past event occurrence.

**REQ-CAR-01-2 [U]** The system shall create an `event_car_history` table:

```sql
CREATE TABLE event_car_history (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    event_type TEXT NOT NULL,           -- 'price_spike', 'disclosure', 'vix_shock', 'fx_shock'
    event_subtype TEXT,                 -- disclosure: 'earnings', 'governance', 'equity_change', etc.
    event_date DATE NOT NULL,
    event_magnitude REAL,              -- e.g., price change % that triggered, or disclosure importance
    car_1d REAL,                       -- Cumulative Abnormal Return: 1-day post-event
    car_5d REAL,                       -- CAR: 5-day post-event
    car_10d REAL,                      -- CAR: 10-day post-event
    benchmark_return_1d REAL,          -- KOSPI return same period (for abnormal calc)
    benchmark_return_5d REAL,
    benchmark_return_10d REAL,
    volume_ratio REAL,                 -- volume vs 20-day average on event day
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(ticker, event_type, event_date)
);

CREATE INDEX idx_event_car_type ON event_car_history(event_type, event_subtype);
CREATE INDEX idx_event_car_ticker ON event_car_history(ticker, event_date DESC);
CREATE INDEX idx_event_car_date ON event_car_history(event_date DESC);
```

**REQ-CAR-01-3 [U]** The Cumulative Abnormal Return (CAR) shall be computed as:
- `abnormal_return(t) = ticker_return(t) - benchmark_return(t)`
- `CAR(N) = sum(abnormal_return(t)) for t in [event_day+1, event_day+N]`
- Benchmark: KOSPI index return for KOSPI-listed tickers, KOSDAQ index return for KOSDAQ-listed tickers

**REQ-CAR-01-4 [U]** Event types to track:

| Event Type | Event Subtype | Trigger Source |
|---|---|---|
| `price_spike` | `positive_3pct`, `negative_3pct`, `positive_5pct`, `negative_5pct` | OHLCV daily change |
| `disclosure` | `earnings`, `governance`, `major_event`, `equity_change`, `investment` | DART `disclosures` table |
| `vix_shock` | `spike_15pct` | yfinance VIX history |
| `fx_shock` | `krw_weaken_1pct`, `krw_strengthen_1pct` | USD/KRX daily change |

**REQ-CAR-01-5 [E]** When the system is deployed, a bootstrap job (`trading car-bootstrap`) shall compute CAR for all historical events from 2022-01-01 to present using existing `ohlcv` and `disclosures` tables. This populates the initial `event_car_history`.

**REQ-CAR-01-6 [E]** When a new trading day completes (post-market 16:30 KST), the system shall compute and insert CAR values for events that occurred 1, 5, and 10 trading days ago (rolling window). Specifically:
- Events from 1 trading day ago: compute `car_1d`
- Events from 5 trading days ago: update `car_5d`
- Events from 10 trading days ago: update `car_10d`

**REQ-CAR-01-7 [U]** CAR data retention: all `event_car_history` rows from 2022-01-01 onward shall be retained permanently. No automatic deletion.

**REQ-CAR-01-8 [N]** The CAR computation shall NOT use lookahead data. CAR for an event on day T uses only returns from day T+1 onward. The computation job runs post-market when all returns are settled.

---

### Module 2 — CAR Prediction Engine

**REQ-CARPRED-02-1 [U]** The system shall implement a CAR Prediction Engine under `src/trading/strategy/car/predictor.py` that estimates the expected CAR magnitude for a new event based on historical similar events.

**REQ-CARPRED-02-2 [U]** The prediction algorithm shall:
1. Query `event_car_history` for events matching: same `event_type` + same `event_subtype` (or same `event_type` if subtype has < 10 observations)
2. Filter to events within the same sector (using existing `fundamentals.sector` data) if available
3. Compute: `predicted_car_5d = weighted_mean(car_5d)` where weights are:
   - Recency weight: more recent events get higher weight (exponential decay, half-life = 180 days)
   - Magnitude similarity: events with similar `event_magnitude` get higher weight (Gaussian kernel)
4. Compute `prediction_confidence`: based on sample size (N events) and variance

**REQ-CARPRED-02-3 [U]** The prediction output shall be a Pydantic model:

```python
class CARPrediction(BaseModel):
    event_type: str
    event_subtype: str | None
    ticker: str
    predicted_car_1d: float
    predicted_car_5d: float
    predicted_car_10d: float
    confidence: float          # 0.0 ~ 1.0 based on sample size + variance
    sample_count: int          # number of historical events used
    similar_events: list[dict] # top-5 most similar historical events (for explainability)
```

**REQ-CARPRED-02-4 [S]** While sample_count < 10 for a given event_type+subtype combination, the prediction confidence shall be set to 0.0 (insufficient data), and the CAR filter shall pass-through (trigger fires as in current behavior).

**REQ-CARPRED-02-5 [U]** The system shall maintain a daily precomputed cache of CAR statistics by event_type+subtype in an `event_car_stats` table:

```sql
CREATE TABLE event_car_stats (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    event_subtype TEXT,
    sector TEXT,                        -- nullable, for sector-specific stats
    sample_count INTEGER NOT NULL,
    mean_car_1d REAL NOT NULL,
    mean_car_5d REAL NOT NULL,
    mean_car_10d REAL NOT NULL,
    std_car_1d REAL NOT NULL,
    std_car_5d REAL NOT NULL,
    std_car_10d REAL NOT NULL,
    median_abs_car_5d REAL NOT NULL,   -- median absolute CAR for threshold calibration
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(event_type, event_subtype, sector)
);
```

**REQ-CARPRED-02-6 [E]** When the nightly CAR computation completes (REQ-CAR-01-6), the system shall refresh the `event_car_stats` table with updated aggregated statistics.

---

### Module 3 — Smart Event Filter

**REQ-FILTER-03-1 [U]** The system shall implement a Smart Event Filter under `src/trading/strategy/car/filter.py` that sits between the existing event trigger system (SPEC-001 REQ-EVENT-04-6) and Decision persona invocation.

**REQ-FILTER-03-2 [U]** The event filtering pipeline shall be:
```
[Existing Event Trigger fires: +/-3%, DART, VIX, FX]
    |
    v
[Smart Event Filter]
    |-- Predict CAR for this event (Module 2)
    |-- If |predicted_car_5d| >= CAR_THRESHOLD: PASS (invoke Decision)
    |-- If |predicted_car_5d| < CAR_THRESHOLD AND confidence > 0.5: BLOCK (skip Decision)
    |-- If confidence <= 0.5: PASS (insufficient data, conservative pass-through)
    |
    v
[Decision persona invocation (if PASS)]
```

**REQ-FILTER-03-3 [U]** The CAR threshold shall be configurable via environment variable `CAR_FILTER_THRESHOLD` with default value `0.015` (1.5% absolute predicted CAR).

**REQ-FILTER-03-4 [E]** When the Smart Event Filter blocks an event, the system shall:
1. Write `audit_log` event `EVENT_CAR_FILTERED` with: ticker, event_type, predicted_car_5d, threshold, confidence, sample_count
2. NOT invoke Decision persona (token savings)
3. Include filtered event count in the daily report

**REQ-FILTER-03-5 [E]** When the Smart Event Filter passes an event, the system shall inject CAR prediction context into the Decision persona's input:
```
[Event-CAR Context]
Event: {event_type}/{event_subtype} for {ticker}
Predicted 5-day CAR: {predicted_car_5d:+.2%} (confidence: {confidence:.0%}, N={sample_count})
Historical similar events: {top-3 similar events with their actual CARs}
Interpretation: This event type historically leads to {positive/negative/neutral} price impact
```

**REQ-FILTER-03-6 [U]** The Smart Event Filter shall persist all decisions to an `event_filter_log` table:

```sql
CREATE TABLE event_filter_log (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_subtype TEXT,
    event_magnitude REAL,
    predicted_car_5d REAL,
    confidence REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    threshold REAL NOT NULL,
    decision TEXT NOT NULL,            -- 'PASS', 'BLOCK', 'PASS_LOW_CONFIDENCE'
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_event_filter_log_ts ON event_filter_log(created_at DESC);
CREATE INDEX idx_event_filter_log_ticker ON event_filter_log(ticker, created_at DESC);
```

**REQ-FILTER-03-7 [N]** The Smart Event Filter shall NOT block events that trigger from existing hard-coded circuit breaker conditions (daily loss limit approaching). Safety-critical triggers bypass CAR filter.

**REQ-FILTER-03-8 [U]** The daily report shall include an Event-CAR filter summary:
```
[Event-CAR Filter]
Events triggered today: 8
Passed (high CAR): 3
Blocked (low CAR): 4
Passed (low confidence): 1
Estimated token savings: ~12,000 tokens (4 blocked Decision+Risk invocations)
```

---

### Module 4 — Volatility Calculator (ATR-based)

**REQ-VOL-04-1 [U]** The system shall implement a Volatility Calculator under `src/trading/strategy/volatility/` that computes per-ticker ATR (Average True Range) for dynamic threshold normalization.

**REQ-VOL-04-2 [U]** The ATR computation shall follow the standard 14-day formula:
- `True Range(t) = max(High(t) - Low(t), |High(t) - Close(t-1)|, |Low(t) - Close(t-1)|)`
- `ATR(14) = EMA(True Range, period=14)` (Exponential Moving Average smoothing)
- ATR percentage: `ATR_pct = ATR / Close * 100`

**REQ-VOL-04-3 [U]** The system shall maintain an `atr_cache` table:

```sql
CREATE TABLE atr_cache (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    date DATE NOT NULL,
    atr_14 REAL NOT NULL,              -- absolute ATR value
    atr_pct REAL NOT NULL,             -- ATR as percentage of close price
    close_price REAL NOT NULL,
    volatility_regime TEXT NOT NULL,    -- 'low', 'normal', 'high', 'extreme'
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(ticker, date)
);

CREATE INDEX idx_atr_cache_ticker_date ON atr_cache(ticker, date DESC);
```

**REQ-VOL-04-4 [U]** Volatility regime classification based on ATR percentile rank (relative to ticker's own 1-year ATR history):
- `low`: ATR_pct below 25th percentile of own history
- `normal`: 25th to 75th percentile
- `high`: 75th to 90th percentile
- `extreme`: above 90th percentile

**REQ-VOL-04-5 [E]** When the post-market data refresh completes (daily ~16:30 KST), the system shall compute and cache ATR for all positions + watchlist tickers using the most recent 30 trading days of OHLCV data.

**REQ-VOL-04-6 [U]** The ATR computation shall handle data gaps gracefully:
- If fewer than 14 days of data exist for a ticker: use available data with minimum 5 days (if < 5, return `None` and fall back to fixed thresholds)
- If OHLCV data has gaps (halted days): skip gap days in computation, do not interpolate

**REQ-VOL-04-7 [N]** The Volatility Calculator shall NOT compute ATR intraday. ATR updates once per trading day using settled close prices. Intraday volatility estimation is out of scope.

---

### Module 5 — Dynamic Threshold Tool

**REQ-DYNTH-05-1 [U]** The system shall implement a Dynamic Threshold Tool registered in the SPEC-009 Tool Registry as `get_dynamic_thresholds`:

| Tool Name | Description | Input | Output |
|---|---|---|---|
| `get_dynamic_thresholds` | Per-ticker ATR-based stop/take thresholds | `ticker: str` | Dynamic threshold dict |

**REQ-DYNTH-05-2 [U]** The `get_dynamic_thresholds` tool shall compute:

```python
class DynamicThresholds(BaseModel):
    ticker: str
    atr_14: float                    # current 14-day ATR (absolute)
    atr_pct: float                   # ATR as % of close
    volatility_regime: str           # low/normal/high/extreme
    stop_loss_pct: float             # -2 * ATR_pct (default multiplier)
    take_profit_pct: float           # +3 * ATR_pct (default multiplier)
    trailing_stop_pct: float         # -1.5 * ATR_pct
    fixed_fallback_stop: float       # -7.0% (SPEC-001 original)
    fixed_fallback_take: float       # RSI>85 equivalent (hardcoded reference)
    effective_stop: float            # max(dynamic_stop, -MAX_STOP_LOSS_PCT)
    effective_take: float            # min(dynamic_take, +MAX_TAKE_PROFIT_PCT)
    source: str                      # 'dynamic' or 'fixed_fallback'
    last_computed: str               # ISO timestamp
```

**REQ-DYNTH-05-3 [U]** Dynamic threshold computation rules:
- `stop_loss_pct = -STOP_ATR_MULTIPLIER * ATR_pct` (env var `STOP_ATR_MULTIPLIER`, default: 2.0)
- `take_profit_pct = +TAKE_ATR_MULTIPLIER * ATR_pct` (env var `TAKE_ATR_MULTIPLIER`, default: 3.0)
- `trailing_stop_pct = -TRAIL_ATR_MULTIPLIER * ATR_pct` (env var `TRAIL_ATR_MULTIPLIER`, default: 1.5)

**REQ-DYNTH-05-4 [U]** Guardrail limits (hard floor/ceiling on dynamic thresholds):
- `effective_stop = max(stop_loss_pct, -MAX_STOP_LOSS_PCT)` where `MAX_STOP_LOSS_PCT = 15.0%` (env var)
- `effective_take = min(take_profit_pct, +MAX_TAKE_PROFIT_PCT)` where `MAX_TAKE_PROFIT_PCT = 30.0%` (env var)
- Dynamic stop shall NEVER be looser than -15% regardless of ATR
- Dynamic take shall NEVER exceed +30% regardless of ATR

**REQ-DYNTH-05-5 [S]** While ATR data is unavailable for a ticker (new listing, insufficient history), the tool shall return `source: "fixed_fallback"` with SPEC-001 original fixed rules: stop = -7%, take = RSI>85 logic (not a percentage, but flagged as requiring RSI check).

**REQ-DYNTH-05-6 [E]** When the Decision persona is invoked (SPEC-009 REQ-PTOOL-02-5), the tool set shall include `get_dynamic_thresholds` when `dynamic_thresholds_enabled=true`. Decision persona queries thresholds for tickers it considers trading.

**REQ-DYNTH-05-7 [U]** Example outputs for different volatility profiles:

| Ticker | ATR_pct | Regime | Stop | Take | Reasoning |
|---|---|---|---|---|---|
| Samsung (005930) | 1.8% | normal | -3.6% | +5.4% | Blue chip, moderate vol |
| SK Hynix (000660) | 3.2% | high | -6.4% | +9.6% | Semiconductor, wide bands |
| KOSDAQ biotech | 5.1% | extreme | -10.2% | +15.0% (capped) | Very volatile, max cap |
| Utility stock | 0.9% | low | -1.8% | +2.7% | Stable, tight bands |

---

### Module 6 — Decision Persona Prompt Integration

**REQ-PROMPT-06-1 [U]** The Decision persona system prompt (`decision.jinja`) shall be updated to reference dynamic thresholds when available:
- Remove hardcoded `-7% stop-loss` and `RSI>85 take-profit` as primary rules
- Replace with: *"Use dynamic thresholds from `get_dynamic_thresholds` tool for each ticker. If dynamic thresholds are unavailable, fall back to -7% stop / RSI>85 take."*
- The existing 7-rule policy structure remains, but rules (2) and (3) reference dynamic values

**REQ-PROMPT-06-2 [U]** The Decision persona prompt shall additionally reference Event-CAR context when provided:
- *"If Event-CAR context is provided, consider the predicted abnormal return in your analysis. High |CAR| events deserve stronger conviction; low |CAR| events suggest caution."*

**REQ-PROMPT-06-3 [U]** The Risk persona prompt shall NOT be modified. Risk evaluates signals independently using its own tool calls (SPEC-009 REQ-REFL-03-9 SoD independence).

**REQ-PROMPT-06-4 [S]** While `dynamic_thresholds_enabled=false`, the Decision persona prompt shall use the original fixed rules (SPEC-001 REQ-PERSONA-04-11 unchanged).

**REQ-PROMPT-06-5 [U]** The updated Decision persona prompt shall include guidance on interpreting dynamic thresholds:
```
## Dynamic Threshold Rules (when available)
- Stop-loss: Use the `effective_stop` from get_dynamic_thresholds for each ticker
- Take-profit: Use the `effective_take` from get_dynamic_thresholds for each ticker
- Trailing stop: Apply `trailing_stop_pct` for positions in profit
- Volatility awareness: In 'extreme' regime, consider smaller position sizes
- Fallback: If tool returns source="fixed_fallback", use -7% stop / RSI>85 take
```

**REQ-PROMPT-06-6 [N]** The prompt update shall NOT remove any existing safety rules (cash floor, sector cap, holding count, value-trap avoidance, no shorting). Only rules (2) stop-loss and (3) take-profit are made dynamic. The remaining 5 rules of the 7-rule policy are unchanged.

---

### Module 7 — Migration & Feature Flags

**REQ-MIGR-07-1 [U]** The migration shall be phased:
- **Phase A** (Week 1): Deploy `event_car_history`, `event_car_stats`, `atr_cache` tables. Run bootstrap job (`trading car-bootstrap`). Feature flags OFF.
- **Phase B** (Week 2): Deploy `event_filter_log` table + Smart Event Filter. Enable `car_filter_enabled=true`. Monitor filter decisions, compare blocked events' actual CAR to validate predictions.
- **Phase C** (Week 3): Deploy `get_dynamic_thresholds` tool. Enable `dynamic_thresholds_enabled=true` for watchlist first (not positions). Monitor threshold values for reasonableness.
- **Phase D** (Week 4): Enable `dynamic_thresholds_enabled=true` for positions (full). Update Decision persona prompt. Monitor trade outcomes vs fixed-rule counterfactual.
- **Phase E** (Week 5+): Full operation. CAR filter + dynamic thresholds active. Tuning phase (adjust multipliers, threshold based on data).

**REQ-MIGR-07-2 [U]** Feature flags in `system_state`:
- `car_filter_enabled` (BOOLEAN, default: false) — Controls Event-CAR Smart Filter activation
- `dynamic_thresholds_enabled` (BOOLEAN, default: false) — Controls dynamic threshold tool availability + prompt update

**REQ-MIGR-07-3 [E]** When feature flags are toggled via Telegram commands from `chat_id=60443392`, the system shall:
- `/car-filter on|off` — Toggle `car_filter_enabled`
- `/dyn-threshold on|off` — Toggle `dynamic_thresholds_enabled`
- All toggles: write `audit_log`, confirm via Telegram within 5 seconds

**REQ-MIGR-07-4 [S]** While `car_filter_enabled=false`, all events pass through to Decision persona unchanged (SPEC-001 behavior). While `dynamic_thresholds_enabled=false`, Decision persona uses fixed rules (SPEC-001 behavior).

**REQ-MIGR-07-5 [U]** The fixed rules (stop -7%, take RSI>85) shall ALWAYS remain in `config.py` as ultimate fallback constants. They are never deleted from the codebase regardless of feature flag state.

**REQ-MIGR-07-6 [U]** Configurable parameters (environment variables):

| Variable | Default | Description |
|---|---|---|
| `CAR_FILTER_THRESHOLD` | 0.015 | Minimum |predicted_car_5d| to pass filter |
| `CAR_BOOTSTRAP_START` | 2022-01-01 | Bootstrap historical data start date |
| `STOP_ATR_MULTIPLIER` | 2.0 | Stop-loss = -N * ATR_pct |
| `TAKE_ATR_MULTIPLIER` | 3.0 | Take-profit = +N * ATR_pct |
| `TRAIL_ATR_MULTIPLIER` | 1.5 | Trailing stop = -N * ATR_pct |
| `MAX_STOP_LOSS_PCT` | 15.0 | Hard floor: dynamic stop never looser than this |
| `MAX_TAKE_PROFIT_PCT` | 30.0 | Hard ceiling: dynamic take never higher than this |

**REQ-MIGR-07-7 [N]** The system shall NOT remove existing event trigger logic (SPEC-001 REQ-EVENT-04-6). The +/-3% trigger, DART disclosure trigger, and VIX/FX triggers continue to fire exactly as before. The CAR filter is an ADDITIONAL layer between trigger and Decision invocation.

**REQ-MIGR-07-8 [U]** Rollback procedure:
1. Set `car_filter_enabled=false` and `dynamic_thresholds_enabled=false` via Telegram or DB
2. System immediately reverts to SPEC-001 behavior (all events pass, fixed rules apply)
3. No data loss: `event_car_history` and `atr_cache` tables remain for re-activation
4. Decision persona prompt automatically uses fixed rules when flag is off

---

### Non-Functional Requirements

**REQ-NFR-12-1 [U, Performance]** CAR prediction for a single event shall complete in < 50ms (database query + weighted mean computation).

**REQ-NFR-12-2 [U, Performance]** ATR computation for 40 tickers shall complete in < 5 seconds during nightly update.

**REQ-NFR-12-3 [U, Performance]** `get_dynamic_thresholds` tool response shall complete in < 20ms (cached ATR lookup + arithmetic).

**REQ-NFR-12-4 [U, Storage]** Event-CAR history: estimated ~2000 events/year x 4 years = ~8000 rows. Minimal storage impact (< 5MB).

**REQ-NFR-12-5 [U, Cost]** Token savings from CAR filtering: estimated 30-50% reduction in event-triggered Decision+Risk invocations. If currently ~50-100 event triggers/month, filtering 40% saves ~20-40 invocations x ~2000 tokens each = ~40K-80K tokens/month.

**REQ-NFR-12-6 [U, Observability]** All CAR predictions, filter decisions, and ATR computations shall be logged to `audit_log` with event types:
- `CAR_PREDICTION_COMPUTED`
- `EVENT_CAR_FILTERED` (blocked)
- `EVENT_CAR_PASSED`
- `ATR_DAILY_UPDATE`
- `DYNAMIC_THRESHOLD_SERVED`
- `DYNAMIC_THRESHOLD_FALLBACK`

**REQ-NFR-12-7 [U, Validation]** During Phase B (CAR filter active), the system shall retrospectively validate filter accuracy by comparing:
- Blocked events: compute actual CAR after 5 days. If actual |CAR| > threshold for >30% of blocked events, emit Telegram alert (filter too aggressive).
- Passed events: track actual impact. Compile weekly CAR accuracy report.

---

## Specifications (Implementation Summary)

### DB Schema Changes (Migration v13)

```sql
-- Event-CAR Historical Database
CREATE TABLE event_car_history (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_subtype TEXT,
    event_date DATE NOT NULL,
    event_magnitude REAL,
    car_1d REAL,
    car_5d REAL,
    car_10d REAL,
    benchmark_return_1d REAL,
    benchmark_return_5d REAL,
    benchmark_return_10d REAL,
    volume_ratio REAL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(ticker, event_type, event_date)
);
CREATE INDEX idx_event_car_type ON event_car_history(event_type, event_subtype);
CREATE INDEX idx_event_car_ticker ON event_car_history(ticker, event_date DESC);
CREATE INDEX idx_event_car_date ON event_car_history(event_date DESC);

-- CAR Statistics Cache
CREATE TABLE event_car_stats (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    event_subtype TEXT,
    sector TEXT,
    sample_count INTEGER NOT NULL,
    mean_car_1d REAL NOT NULL,
    mean_car_5d REAL NOT NULL,
    mean_car_10d REAL NOT NULL,
    std_car_1d REAL NOT NULL,
    std_car_5d REAL NOT NULL,
    std_car_10d REAL NOT NULL,
    median_abs_car_5d REAL NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(event_type, event_subtype, sector)
);

-- Event Filter Log
CREATE TABLE event_filter_log (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_subtype TEXT,
    event_magnitude REAL,
    predicted_car_5d REAL,
    confidence REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    threshold REAL NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_event_filter_log_ts ON event_filter_log(created_at DESC);
CREATE INDEX idx_event_filter_log_ticker ON event_filter_log(ticker, created_at DESC);

-- ATR Cache
CREATE TABLE atr_cache (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    date DATE NOT NULL,
    atr_14 REAL NOT NULL,
    atr_pct REAL NOT NULL,
    close_price REAL NOT NULL,
    volatility_regime TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(ticker, date)
);
CREATE INDEX idx_atr_cache_ticker_date ON atr_cache(ticker, date DESC);

-- Feature flags in system_state
ALTER TABLE system_state ADD COLUMN car_filter_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE system_state ADD COLUMN dynamic_thresholds_enabled BOOLEAN NOT NULL DEFAULT false;
```

### New Module Structure

```
src/trading/strategy/
├── __init__.py
├── car/
│   ├── __init__.py
│   ├── models.py              # CARPrediction, EventCARRecord Pydantic models
│   ├── calculator.py          # CAR computation from OHLCV data
│   ├── predictor.py           # CAR prediction engine (weighted historical mean)
│   ├── filter.py              # Smart Event Filter logic (PASS/BLOCK)
│   ├── bootstrap.py           # Historical CAR bootstrap job
│   └── stats.py               # CAR statistics aggregation and caching
└── volatility/
    ├── __init__.py
    ├── models.py              # DynamicThresholds Pydantic model
    ├── atr.py                 # ATR computation (14-day EMA)
    ├── regime.py              # Volatility regime classification
    └── thresholds.py          # Dynamic threshold computation with guardrails
```

### Modified Modules

- `src/trading/tools/registry.py` — Register `get_dynamic_thresholds` tool
- `src/trading/tools/market_tools.py` — Implement `get_dynamic_thresholds` tool function
- `src/trading/personas/orchestrator.py` — Insert CAR filter between event trigger and Decision invocation; inject CAR context into Decision input
- `src/trading/personas/prompts/decision.jinja` — Update rules (2)(3) to reference dynamic thresholds
- `src/trading/scheduler/daily.py` — Add nightly ATR update + CAR computation jobs
- `src/trading/reports/daily_report.py` — Add Event-CAR filter summary + dynamic threshold stats
- `src/trading/bot/telegram_bot.py` — Add `/car-filter`, `/dyn-threshold` commands
- `src/trading/config.py` — Add CAR/ATR configuration constants + env var loading
- `src/trading/db/migrations/013_event_car_atr.sql` — Above schema
- `src/trading/scripts/car_bootstrap.py` — CLI entrypoint for `trading car-bootstrap`

### Compatibility with SPEC-009 + SPEC-011

- **Tool-calling (SPEC-009)**: `get_dynamic_thresholds` is registered as a standard tool. Decision persona calls it via tool-use loop.
- **JIT Pipeline (SPEC-011)**: Delta events provide real-time trigger data. CAR filter evaluates delta-triggered events. No conflict — CAR filter receives the same event types from either JIT or cron path.
- **Reflection Loop (SPEC-009)**: Works identically. Dynamic thresholds are part of Decision's tool context whether evaluating original or revised signal.
- **ProtoHedge (SPEC-011)**: Complementary. ProtoHedge adjusts overall exposure ceiling; dynamic thresholds adjust per-ticker exit rules. Both can be active simultaneously without conflict.

### Dependent SPECs

This SPEC requires:
- SPEC-TRADING-001 (Core system) — Event triggers, Decision persona, risk limits
- SPEC-TRADING-009 (Tool-calling) — Tool registry for `get_dynamic_thresholds`
- SPEC-TRADING-011 (JIT pipeline) — Optional but recommended for real-time event data

---

## Traceability

| REQ ID | Module | Implementation Location (planned) | Verification |
|---|---|---|---|
| REQ-CAR-01-1~8 | M1 (CAR Database) | `src/trading/strategy/car/calculator.py`, `bootstrap.py` | M1 scenarios |
| REQ-CARPRED-02-1~6 | M2 (CAR Prediction) | `src/trading/strategy/car/predictor.py`, `stats.py` | M2 scenarios |
| REQ-FILTER-03-1~8 | M3 (Smart Filter) | `src/trading/strategy/car/filter.py`, `personas/orchestrator.py` | M3 scenarios |
| REQ-VOL-04-1~7 | M4 (Volatility Calculator) | `src/trading/strategy/volatility/atr.py`, `regime.py` | M4 scenarios |
| REQ-DYNTH-05-1~7 | M5 (Dynamic Threshold Tool) | `src/trading/strategy/volatility/thresholds.py`, `tools/market_tools.py` | M5 scenarios |
| REQ-PROMPT-06-1~6 | M6 (Prompt Integration) | `personas/prompts/decision.jinja` | M6 scenarios |
| REQ-MIGR-07-1~8 | M7 (Migration) | `system_state`, `bot/telegram_bot.py`, `config.py` | M7 scenarios |
| REQ-NFR-12-1~7 | Cross-cutting | All modules | NFR scenarios |

---

## Future Scope (Out of Scope for SPEC-TRADING-012)

- **ML-based CAR prediction** — Current approach uses weighted historical mean. Future: gradient boosting or transformer model trained on event features for more accurate CAR prediction.
- **Intraday ATR recalculation** — Currently ATR updates daily. Future: 5-minute ATR from WebSocket data for intraday threshold adjustment.
- **CAR for portfolio-level events** — Current approach is per-ticker. Future: aggregate CAR when multiple correlated positions experience simultaneous events.
- **Adaptive CAR threshold** — Currently fixed 1.5%. Future: self-adjusting threshold based on filter accuracy feedback loop.
- **Dynamic position sizing from ATR** — Current SPEC only uses ATR for exit thresholds. Future: position size = f(ATR) for volatility-normalized risk per trade.
- **Event clustering** — Multiple events on same ticker same day treated independently. Future: cluster correlated events for combined CAR prediction.
- **Sector-wide CAR contagion** — When one stock in a sector has high-CAR event, predict impact on sector peers.
