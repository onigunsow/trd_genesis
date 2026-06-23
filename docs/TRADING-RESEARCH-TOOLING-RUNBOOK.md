# Trading Research Tooling — Runbook

**Status**: Research/paper-only. No live trading changes.  
**Measurement**: Current measured edge is negative (-14,840 KRW/trade, alpha -11%p, n=8). Both capabilities build the ability to **honestly measure** alpha; "no alpha found" is a valid, successful outcome.

---

## 1. Entry-Signal Alpha Diagnosis (SPEC-TRADING-057)

Measures where the -14,840 KRW/trade loss comes from and whether any mechanical entry feature has positive OOS alpha after costs and survivorship correction.

### Prerequisites

- `trading-app` container with KRX credentials
- Environment variables: `KRX_ID`, `KRX_PW` (container env)
- Trading database live Postgres connection (real DB, not fixtures)

### Running Entry-Alpha Analysis

Inside the `trading-app` container:

```bash
cd /app
python -m trading.backtest.entry_alpha_run \
  --start 2018-01-01 \
  --end 2024-12-31 \
  --output-dir /tmp/entry_alpha_results
```

**Arguments**:
- `--start`: Analysis start date (YYYY-MM-DD), pykrx will fetch as-of-date KOSPI200 membership
- `--end`: Analysis end date (YYYY-MM-DD)
- `--output-dir`: Directory for attribution reports (defaults to `./.entry_alpha_results/`)

**Output**:
- `entry_alpha_result.json`: Measured alpha per entry feature (RSI, PER, foreign inflow)
- `attribution_report.md`: Decomposition of -14,840 into 5 components + residual
- `universe_validation.log`: Confirmation that as-of-date membership (incl. delisted) was achievable

### What Is Measured

**M1 — Historical Data Pipeline**:
- Validates point-in-time KOSPI200 membership at each rebalance date
- Retrieves as-of-date delisted-stock OHLCV (no look-ahead)
- Confirms no future-dated bars injected

**M2 — Mechanical Entry-Feature Alpha**:
- Tests three rankable score features:
  - `RSI` (30-70 band)
  - `PER` (<15 filter)
  - `foreign` (5-day net inflow, >0 threshold)
- Each feature forms a portfolio (ranked, top quintile)
- Measures net OOS alpha vs KOSPI (time-weighted equity curve, costs included)
- Bonferroni correction (N=3 features tested)

**M3 — Attribution Decomposition**:
- Baseline: mechanical equal-weight screener candidates (no LLM discretion)
- Sequential counterfactuals: entry signal quality, cost/tax/slippage, exit timing, position sizing, LLM-discretion delta
- RESIDUAL bucket ensures sum-consistency check
- **Mandatory cost quantification** (engine.py: 0.18% tax floor, 0.05% slippage assumption)
- **Honesty flags**: n=8 paper trades anecdotal; load-bearing evidence is M1/M2 historical backtest

### Interpreting Results

- **"Positive alpha found in RSI"** → Next step: validate with SPEC-058 (if data available) or schedule for live paper collection
- **"No tested feature has positive alpha"** → **Valid, successful diagnosis**. Root cause is entry-signal quality. (Not a failure.)
- **Cost component is {X}%** → Costs eat a significant portion of any edge; only low-turnover strategies survive Korean fees
- **Survivorship bias flag: YES** → Result is an upper bound only; cannot claim signed alpha without SPEC-057 M1-6 green

---

## 2. Low-Volatility Factor Walk-Forward Validation (SPEC-TRADING-058)

Tests whether a mechanical **low-volatility factor** (120-day trailing vol, 1/N equal-weight, monthly rebalance) beats KOSPI after costs, survivorship correction, and 50% backtest discount.

### Prerequisites

- `trading-app` container with KRX credentials
- SPEC-057 M1 point-in-time loader + as-of-date universe surface (already provided)
- Trading database live Postgres connection

### Running Low-Vol Factor Validation

Inside the `trading-app` container:

```bash
cd /app
# 주의: lowvol_validation 은 독립 CLI가 없다(함수 기반 모듈).
# trading.backtest.lowvol_validation 의 함수로 호출한다:
#   run_walk_forward_oos / apply_alpha_haircut / apply_bonferroni /
#   compose_verdict / render_verdict_report
# 실데이터 사용 예시는 SPEC-058 spec.md 및 tests/backtest/test_lowvol_validation.py 참조.
```

**Arguments**:
- `--start`: Backtest start date (YYYY-MM-DD)
- `--end`: Backtest end date (YYYY-MM-DD)
- `--rebalance-freq`: Rebalance cadence; fixed to `monthly` (no intraday/weekly options)
- `--lookback-days`: Trailing vol window; fixed to `120` trading days (no tuning)
- `--output-dir`: Output directory for walk-forward results

**Output**:
- `lowvol_backtest_result.json`: Raw backtest alpha (before discount)
- `lowvol_discounted_verdict.json`: 50%-discounted alpha + GO/NO-GO judgment
- `walk_forward_log.csv`: Per-rebalance-period component (equity curve, turnover, n sample size)
- `survivorship_gate_status.txt`: PASS/FAIL/ABSENT (determines if result is signed or bound-only)

### What Is Tested

**M1 — Factor Signal**:
- Computes low-volatility rank (trailing 120-day daily-return std) for each symbol
- Point-in-time (uses only data available at rebalance date, no forward bias)
- Excludes symbols with <120 bars of history

**M2 — Portfolio Construction + Cost-Aware Backtest**:
- Selects lowest-volatility quantile (~10-20 names)
- 1/N equal-weight (no optimization, deterministic)
- Monthly rebalance (fixed, no more-frequent rebalancing)
- Turnover <50%/month (required; flagged if violated)
- Backtested via `engine.run` (applies real `DEFAULT_FEE_RATE=0.15%`, `DEFAULT_TAX_RATE=0.18%`, `DEFAULT_SLIPPAGE=0.05%`)

**M3 — Walk-Forward OOS Validation**:
- Repeated point-in-time `engine.run` at each monthly rebalance (not single full-sample)
- Bonferroni correction (N=1 factor, so α/1 = α, but mechanism generic for future multi-factor SPEC-059)
- 50% McLean-Pontiff discount applied: measured alpha × 0.5 before GO judgment
- n = number of monthly rebalance periods (floor: 30 periods = ~2.5 years, or INCONCLUSIVE)
- Survivorship-bias gate (inherited from SPEC-057):
  - If SPEC-057 M1-6 result is **absent or "NOT achievable"** → low-vol alpha force-downgraded to **"survivorship-biased upper bound — sign reporting forbidden, bound-only"**
  - If SPEC-057 confirms point-in-time membership IS achievable → reconstruct as-of-date universe per rebalance; result is signed

### GO/NO-GO Decision

Factor earns GO only if **all three conditions hold** (single AND gate):
1. Bonferroni-adjusted significance PASSED
2. 50%-discounted alpha > 0 (the discounted figure, not raw)
3. `scorecard.decide` returns GO on time-weighted inputs:
   - `expectancy > 0`
   - `profit_factor > 1.0`
   - `KOSPI alpha > 0` (time-weighted, NOT money-weighted)
   - `n >= 30` rebalance periods

### Interpreting Results

- **Verdict: GO + Survivorship gate: PASS**
  - Low-vol factor has measurable positive OOS alpha after all corrections
  - Promoted to **paper OOS collection only** (not live)
  - Eligible for future live testing only after extended paper validation

- **Verdict: GO + Survivorship gate: ABSENT or NOT ACHIEVABLE**
  - Raw alpha might look positive, but result is an **upper-bound estimate**
  - Cannot claim signed alpha; sign reporting forbidden
  - True performance unknown (data issue, not strategy failure)

- **Verdict: NO-GO** (any reason)
  - **Valid, successful outcome**. Low-vol factor does not beat costs + survivorship in this regime
  - Not a failure; diagnostic capability is working correctly
  - Next step: try SPEC-059 (quality factor) if data becomes available, or accept Korean low-alpha finding

---

## 3. Honest-Framing Caveats (HARD)

Both tools embed the following non-optional statements in output:

### Cost Model Limitations
- Tax 0.18% is the **floor** of real Korean sell-tax (0.18-0.23%)
- Slippage 0.05% assumes large-cap liquidity; small/illiquid names incur higher real costs
- → Measured alpha is **optimistically biased upward**

### Sample Size
- Entry-alpha analysis uses n=8 paper trades (2026 live, synthetic SELL fills)
- n=8 is **statistically near-worthless** (load-bearing evidence is historical backtest, not live trade count)
- → Anecdotal, not probative

### Survivorship Bias
- If SPEC-057 M1-6 gate result is absent or "NOT achievable":
  - All factor backtests are biased by survivor selection (only today's survivors in dataset)
  - -14,840 was made on this exact bias
  - Result is **bound-only, no signed alpha**

### Backtest Decay
- Published alphas decay ~50% post-publication (McLean-Pontiff 2016)
- All GO judgments use 50%-discounted figure, not raw backtest alpha
- → Even "GO" factors have ~20-30% base-rate success rate in live (not a guarantee)

### Paper-Only Promotion
- Factors earning GO are promoted to **paper OOS collection only**
- Live trading **not enabled** by either SPEC
- Extended paper validation required before any live escalation

---

## 4. CLI Quick Reference

### Entry-Alpha (SPEC-057)
```bash
python -m trading.backtest.entry_alpha_run --start 2018-01-01 --end 2024-12-31
```
→ Produces `attribution_report.md` answering "where did -14,840 come from?"

### Low-Vol Factor (SPEC-058)
```bash
# CLI 미제공 — trading.backtest.lowvol_validation 함수(run_walk_forward_oos 등) 호출 (SPEC-058 참조)
```
→ Produces `lowvol_discounted_verdict.json` answering "does low-vol beat KOSPI after costs + bias corrections?"

---

## 5. Integration with Project

Both tools are **research/paper-only**:
- No changes to `order.py`, `smoke_gate.py`, live-trading gates, or `live_unlocked`
- Validation gate (`validation_gate.py`) remains `False` by default (blocks live)
- Results feed only into paper strategy collection (future work, out of scope)

**Data dependencies**:
- SPEC-057 M1 point-in-time loader (required by SPEC-058)
- `engine.py` cost model (frozen, reused)
- Real trading DB (not fixtures)
- KRX pykrx adapter (read-only, no changes)

---

## 6. Expected Timelines & Monitoring

- **Entry-alpha run** (2018-2024): ~5-10 minutes per feature
- **Low-vol walk-forward** (2018-2024, monthly rebalance): ~10-20 minutes
- **Background multi-year entry-alpha run**: Currently running, verdict pending (document does not state result)

Monitor:
- Container logs for errors (pykrx timeout, DB connection, missing bars)
- Survivorship-gate status (confirms M1-6 capability present)
- Discounted alpha (not raw) before any GO decision
