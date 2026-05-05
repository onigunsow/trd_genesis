---
id: SPEC-TRADING-012
type: research
title: "Research Basis — Event-CAR Modeling + Volatility-Adjusted Dynamic Thresholds"
---

# SPEC-TRADING-012 Research Document

## Research Sources

### Primary Research: Janus-Q (Event-Driven CAR Trading)

**Core Concept**: Treat market events as the primary decision units, not time-based schedules. Each event has measurable subsequent price impact that can be quantified as Cumulative Abnormal Return (CAR).

**Key Insights**:
1. Events are heterogeneous — a DART earnings disclosure and a 3% price spike have fundamentally different information content
2. Not all events are actionable — many events produce statistically insignificant price impact
3. Historical CAR by event type provides a reliable filter for noise vs signal
4. Event-driven approach reduces unnecessary model invocations by filtering low-impact events

**Application to Trading System**:
- Current system (SPEC-001 REQ-EVENT-04-6) treats all events as binary triggers: +/-3% OR DART = invoke Decision
- Janus-Q insight: measure historical CAR for each event type+ticker combination
- Only invoke Decision when predicted |CAR| exceeds threshold
- Benefit: 30-50% reduction in event-triggered persona invocations (token savings + noise reduction)

**CAR Formula**:
```
Normal Return(t) = benchmark_return(t)  [KOSPI/KOSDAQ index return]
Abnormal Return(t) = actual_return(t) - normal_return(t)
CAR(N) = sum(AR(t)) for t in [event_day+1, event_day+N]
```

**Why 5-day CAR as primary metric**:
- 1-day CAR: too noisy, captures immediate reaction only (often overreaction)
- 5-day CAR: captures medium-term information absorption without too much drift
- 10-day CAR: secondary signal for sustained impact detection
- Literature suggests 5-day window balances signal-to-noise for event studies

---

### Primary Research: Trading-R1 (Volatility-Driven Dynamic Labeling)

**Core Concept**: Fixed threshold rules (e.g., -7% stop-loss, RSI>85 take-profit) are suboptimal because they ignore volatility regime. A -7% drawdown for a low-volatility utility stock is catastrophic; for a high-volatility biotech it may be routine noise.

**Key Insights**:
1. ATR (Average True Range) normalizes price movements by ticker-specific volatility
2. Stop-loss and take-profit should scale with volatility: wider for volatile stocks, tighter for stable ones
3. Fixed rules create two failure modes:
   - Too tight for volatile stocks: stopped out on noise (premature exit)
   - Too loose for stable stocks: allow excessive drawdown before triggering
4. Dynamic thresholds adapt to market conditions without human intervention

**ATR as Volatility Proxy**:
- ATR captures average price range including gaps (true range)
- 14-day period balances responsiveness with smoothness
- ATR_pct (ATR/Close) normalizes for different price levels
- Widely used in professional trading for position sizing and exit rules

**Multiplier Selection Rationale**:
- Stop-loss at 2x ATR: Standard "normal noise" filter. A stock that moves 2x its typical daily range is experiencing unusual pressure.
- Take-profit at 3x ATR: Captures above-average favorable moves. 3x ATR typically represents a multi-day directional move.
- Trailing stop at 1.5x ATR: Tighter than initial stop, locks in profit while allowing normal fluctuation.

**Application to Trading System**:
- Replace Decision persona rule (3) `-7% stop-loss` with `-2 * ATR_pct`
- Replace Decision persona rule (2) `RSI>85 take-profit` with `+3 * ATR_pct`
- Add trailing stop mechanism: `-1.5 * ATR_pct` from peak
- Deliver via SPEC-009 tool-calling: `get_dynamic_thresholds(ticker)`

---

## Current System Analysis

### Current Event Trigger Behavior (SPEC-001 REQ-EVENT-04-6)

```
Trigger Conditions (ANY fires Decision persona):
1. Held stock price change >= +/-3% intraday
2. New DART disclosure for held/watchlist ticker
3. VIX change >= +15% intraday
4. USD/KRW change >= +/-1% intraday

Problem: All triggers are binary (fire or not). No assessment of:
- Expected magnitude of price impact
- Historical success rate of similar events
- Whether the event type historically leads to actionable CAR
```

**Observed Issues**:
- Many DART disclosures (routine annual reports, minor governance changes) trigger Decision persona but result in "no action" signal
- Small VIX movements (barely crossing 15% threshold) rarely produce meaningful Korean market impact
- Frequent +/-3% moves in volatile stocks trigger excessive Decision invocations that conclude "hold position, within normal volatility"

**Estimated waste**: 40-60% of event-triggered invocations result in "no new signal" from Decision persona. Each invocation costs ~2000 tokens (Decision + Risk).

### Current Decision Persona Rules (SPEC-001 REQ-PERSONA-04-11)

```
7-Rule Portfolio Operating Policy:
(1) Cash floor 30-50%
(2) Take-profit when RSI > 85          <-- TO BE MADE DYNAMIC
(3) Stop-loss at -7%                   <-- TO BE MADE DYNAMIC
(4) Sector cap 40%
(5) 3-7 holdings
(6) Value-trap avoidance
(7) No short selling
```

**Problem with fixed rules (2) and (3)**:
- Samsung (005930): ATR_pct ~ 1.8%. Fixed -7% stop = 3.9x ATR. Too loose — allows ~4 days of normal adverse movement before triggering.
- KOSDAQ biotech: ATR_pct ~ 5%. Fixed -7% stop = 1.4x ATR. Too tight — gets stopped out on normal daily noise.
- SK Hynix (000660): ATR_pct ~ 3.2%. Fixed -7% stop = 2.2x ATR. Roughly appropriate by accident.

---

## Implementation Design Decisions

### Decision: Statistical Mean vs ML for CAR Prediction

**Chosen: Weighted Statistical Mean (v1)**
- Pro: Interpretable, debuggable, no training infrastructure needed
- Pro: Works with small sample sizes (min 10 events)
- Pro: Transparent weighting (recency + magnitude similarity)
- Con: Cannot capture non-linear interactions between event features
- Con: Cannot leverage cross-ticker transfer learning

**Rejected: ML Model (future v2)**
- Requires labeled training data (events + outcomes)
- Requires model training pipeline (not yet justified)
- Risk of overfitting on small sample sizes per event type
- Future SPEC can upgrade prediction engine without changing interface

### Decision: CAR Filter Position (before vs after Decision)

**Chosen: BEFORE Decision persona invocation**
- Reason: Primary goal is token savings. If filter is after Decision, Decision still gets invoked (no savings)
- Reason: Filter is cheap (< 50ms database lookup) vs Decision (~5s API call)
- Tradeoff: False negatives (filter blocks an event that would have been actionable)
- Mitigation: Low-confidence events pass through (conservative). Retrospective validation catches over-filtering.

### Decision: ATR Period (7 vs 14 vs 21 days)

**Chosen: 14-day ATR (industry standard)**
- 7-day: Too responsive to short-term spikes, unstable
- 14-day: Balanced — standard in most quantitative trading systems
- 21-day: Too slow to adapt to regime changes (e.g., earnings season volatility spike)
- Note: 14-day is the default for RSI (which we already reference), maintaining consistency

### Decision: Threshold Multipliers (2x/3x ATR)

**Rationale for 2x stop**:
- Academic literature: 2x ATR captures ~95% of normal daily noise
- A move exceeding 2x ATR is statistically significant (unusual)
- More conservative than professional trading (many use 1-1.5x) but appropriate for a system prioritizing capital preservation

**Rationale for 3x take**:
- Asymmetric by design: let winners run slightly longer than stopping losers
- 3x ATR represents a meaningful multi-day directional move
- Risk-reward ratio: 3x take / 2x stop = 1.5:1 (acceptable)

### Decision: Guardrail Hard Limits

**-15% max stop and +30% max take**:
- Even for extreme volatility stocks (ATR_pct = 8%), dynamic stop = -16% would exceed guardrail
- Prevents pathological cases where ATR spikes (e.g., after halt) produce absurd thresholds
- -15% is generous enough for most volatile Korean stocks while preventing catastrophe
- +30% prevents holding forever in expectation of unlikely moves

---

## Integration Architecture

### Data Flow: Event-CAR Filter

```
[SPEC-011 JIT Delta Event Pipeline]
    |
    v
[SPEC-001 Event Trigger: +/-3%, DART, VIX, FX]
    |
    | (existing trigger fires)
    v
[NEW: Smart Event Filter] <-- CAR Prediction Engine <-- event_car_history DB
    |
    |-- BLOCK: audit_log, skip Decision (token savings)
    |-- PASS: inject CAR context, proceed to Decision
    v
[Decision Persona (with CAR context)]
    |
    v
[Risk Persona (unchanged)]
```

### Data Flow: Dynamic Thresholds

```
[Nightly Post-Market Job: 16:30 KST]
    |
    v
[ATR Calculator] <-- ohlcv table (existing M3 data)
    |
    v
[atr_cache table] (updated daily per ticker)

[Decision Persona invocation]
    |
    | (via SPEC-009 tool-calling)
    v
[get_dynamic_thresholds(ticker)] <-- atr_cache lookup + threshold math
    |
    v
[DynamicThresholds response: stop, take, trail, regime]
    |
    v
[Decision uses dynamic values instead of fixed -7%/RSI>85]
```

### Interaction with Existing SPECs

| SPEC | Relationship | Integration Point |
|---|---|---|
| SPEC-001 | Base system | Event triggers remain unchanged; CAR is additional filter layer |
| SPEC-001 | Decision prompt | Rules (2)(3) become dynamic (feature-flagged) |
| SPEC-009 | Tool architecture | `get_dynamic_thresholds` registered in tool registry |
| SPEC-009 | Reflection loop | Dynamic thresholds available in both original and revised signals |
| SPEC-010 | Model router | No interaction (SPEC-012 tools use Sonnet through standard path) |
| SPEC-011 | JIT pipeline | Delta events provide real-time trigger data to CAR filter |
| SPEC-011 | ProtoHedge | Complementary: portfolio-level ceiling vs per-ticker exit rules |

---

## Expected Outcomes

### Token Savings (CAR Filter)

| Metric | Current | With CAR Filter | Savings |
|---|---|---|---|
| Event triggers/month | 50-100 | 50-100 (triggers unchanged) | 0 |
| Decision persona invocations from events | 50-100 | 30-60 | 20-40 avoided |
| Tokens per Decision+Risk invocation | ~2000 tok | ~2000 tok | N/A |
| Monthly token savings | 0 | 40K-80K tokens | ~40K-80K saved |
| Monthly cost savings (Sonnet) | 0 | ~3,000-6,000 KRW | ~5,000 KRW |

### Trade Quality (Dynamic Thresholds)

| Scenario | Fixed Rules | Dynamic Thresholds | Improvement |
|---|---|---|---|
| Volatile stock normal noise | Stopped out at -7% | Holds through -6% (2x ATR=3.5% * 2 = 7%, so barely triggers) | Fewer premature exits |
| Stable stock gradual decline | Takes 7% loss before stopping | Stops at -3.6% (2x ATR=1.8% * 2) | Earlier loss control |
| Strong momentum run | Takes profit too early (RSI>85) | Runs to +9.6% (3x ATR=3.2%) | Larger winners |
| Low-vol stock small gain | Misses take at +2.5% (RSI not at 85) | Takes at +2.7% (3x ATR=0.9%) | Captures small moves |

### Risk Management Enhancement

- Per-ticker calibration means no one-size-fits-all approach
- Volatility regime awareness enables Position persona (M5+) to adjust sizing
- CAR prediction gives Decision persona CONVICTION signal (high CAR = stronger action, low CAR = caution)
- Retrospective validation ensures filter does not over-filter

---

## Validation Strategy

### Phase B Validation: CAR Filter Accuracy

Track for 2 weeks after enabling `car_filter_enabled=true`:

1. **Filter precision**: What % of BLOCKED events would have resulted in "no signal" from Decision?
   - Target: > 60% (most blocked events truly were noise)
   - Measurement: For blocked events, compute actual 5-day CAR. Compare to threshold.

2. **Filter recall**: What % of PASSED events produced actionable Decision signals?
   - Target: > 70% (most passed events actually matter)
   - Measurement: Track Decision outcomes (trade vs no-trade) for passed events.

3. **False negative rate**: Did blocking miss any high-impact events?
   - Target: < 10% (very few material events get blocked)
   - Measurement: For blocked events, if actual |CAR| > 2x threshold, flag as false negative.

### Phase D Validation: Dynamic Threshold Effectiveness

Track for 2 weeks after enabling `dynamic_thresholds_enabled=true`:

1. **Stop-loss effectiveness**: Compare stopped-out positions vs continuing beyond stop level
   - Counterfactual: if position had NOT been stopped, would it have recovered?
   - Target: < 30% of stops are "recovered" within 5 days (most stops were correct)

2. **Take-profit capture**: Are dynamic takes capturing more profit than fixed RSI>85?
   - Measurement: Compare exit price (dynamic) vs what RSI>85 would have suggested
   - Target: Average exit is >= fixed rule exit (dynamic >= fixed on average)

3. **Volatility regime adaptation**: Do threshold adjustments make sense qualitatively?
   - Weekly Telegram report showing threshold values per position
   - Human review (onigunsow) validates reasonableness
