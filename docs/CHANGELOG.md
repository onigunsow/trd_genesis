# CHANGELOG

## [Unreleased]

### Added

#### SPEC-TRADING-057 — 엣지 귀인 진단 (Edge Attribution Diagnosis)
- **New modules** under `src/trading/backtest/`:
  - `universe_reconstructor.py`: as-of-date historical KOSPI200 membership with delisted constituents (survivorship-bias-free)
  - `historical_loader.py`: point-in-time OHLCV loader wrapping `pykrx_adapter` (look-ahead guarded, reproducible)
  - `feature_alpha_measurer.py`: entry-feature alpha measurement (RSI/PER/foreign) with Bonferroni correction and sample floor
  - `attribution_report.py`: 5-component decomposition (entry signal, cost/slippage/tax, exit timing, position sizing, LLM-discretion delta) + RESIDUAL bucket with mandatory cost quantification and honesty flags
  - `entry_alpha_run.py`: CLI harness (`trading entry-alpha --start --end`)
- **Verification**: Offline 1962 tests passed; real-KRX real-DB end-to-end confirmed
- **Honest framing**: Measured edge is currently negative (-14,840 KRW/trade, alpha -11%p); "no-alpha = valid success" framing preserved throughout

#### SPEC-TRADING-058 — 증거 기반 정량 팩터 전략: 저변동성 (Low-Volatility Factor, 저베타)
- **New modules** under `src/trading/strategy/factor/` and `src/trading/backtest/`:
  - `factor_lowvol.py`: low-volatility/low-beta factor (120 trading days fixed lookback, OHLCV-only, point-in-time pure function)
  - `lowvol_portfolio.py`: portfolio construction (1/N equal-weight, monthly rebalance, turnover budget <50%/month, GO gate adapter)
  - `lowvol_validation.py`: walk-forward OOS validation (repeated point-in-time `engine.run` per rebalance window, Bonferroni + 50% McLean-Pontiff discount, single AND decision gate)
- **New adapter** (SPEC-057 + SPEC-058 shared):
  - `benchmark_to_analytics_adapter.py` (working name): Converts `engine.run` time-weighted equity-curve (`BacktestResult.equity_curve / daily_returns`) to `scorecard.decide` inputs (`Analytics` + time-weighted `Benchmark.alpha_pct`), blocking forbidden `benchmark.py` money-weighted alpha from GO gate
- **Verification**: Offline 208 backtest tests passed; container walk-forward smoke test PASS
- **Constraints**:
  - **Paper-only**: GO-verdict factors promoted to paper OOS collection only (not live)
  - **Survivorship-bias gate** (inherited from SPEC-057): fail-closed — absent gate result → bound-only (no signed alpha)
  - **Honest framing**: "No factor positive net OOS alpha after costs + survivorship correction = valid success"; 50% backtest discount applied before any GO judgment
- **Deferred to SPEC-059**:
  - Quality factor (gross profitability) — input data (revenue/COGS/total_assets) absent from current `fundamentals` schema
  - Combined low-volatility + quality factor

### Documentation
- **Runbook**: `docs/TRADING-RESEARCH-TOOLING-RUNBOOK.md` — how to run `trading entry-alpha` and low-vol walk-forward validation, honest-framing caveats

## Notes

- Both SPECs 057 and 058 are **research/diagnosis only**, not live-trading implementations
- Measured edge is currently **negative** (-14,840 KRW/trade, n=8); both SPECs build the **capability to measure** whether any signal has alpha, not a proven strategy
- "No alpha found = valid success" is explicitly preserved in all documentation and reporting
- Real results pending from multi-year entry-alpha background run; document does not state its outcome
