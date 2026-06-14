---
id: SPEC-TRADING-044
version: 0.1.0
status: draft
created: 2026-06-14
updated: 2026-06-14
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "Measurement infrastructure — walk-forward backtest harness + KOSPI buy-and-hold benchmark + cost-adjusted expectancy scorecard (rule layer only)"
related_specs:
  - SPEC-TRADING-037   # exit_sweep — the single-pass deterministic exit-rule sweep this SPEC upgrades to walk-forward (train/test)
  - SPEC-TRADING-026   # edge-validation — benchmark.py / analytics.py / scorecard.py / roundtrips.py being extended here (mig 026)
  - SPEC-TRADING-040   # exit policy — the rule layer (exit/trim/sizing) whose cost-adjusted expectancy this SPEC measures
  - SPEC-TRADING-042   # broker-truth ledger — realized round-trips feeding the scorecard come from the centralised KIS ledger
---

# SPEC-TRADING-044 — Measurement infrastructure (rule layer only)

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-06-14 | 0.1.0 | 심층 감사 + 2026 문헌 리서치(FINSABER, KDD 2026) 결론을 받아 작성. 핵심 진단: 이 봇은 실행/리스크 인프라는 탄탄하나 **자기 전략의 원가보정 기대값이 양수인지 검증할 능력이 0**이다. 알파 원천(LLM 페르소나 결정)은 비결정적·기억편향이라 **근본적으로 백테스트 불가**. 문헌은 이 구성(LLM 에이전트, 룰 미검증)이 증거가 가장 약한 배치이며, 불장에서 LLM 에이전트가 buy-and-hold 에 진다고 예측한다 — 2025 KOSPI +76% 가 정확히 그 국면. 운영자 결정: **먼저 측정 인프라를 깔아** 룰로 표현 가능한 부분을 검증하고 KOSPI 벤치마크를 상시 가시화한다. 범위는 **룰 표현 가능 부분 한정**(출구 룰, 포지션 사이징, 레짐 필터). LLM 결정 리플레이는 명시적 비목표(Non-Goal). 라이브러리 결정: 기존 벡터화 `backtest/engine.py` 철학 보존 + **vectorbt 를 sweep/walk-forward 가 값을 하는 곳에만** 추가. 발견: `config.py` 의 `LIVE_FEE_SELL_KOSPI=0.00345`(구 거래세 0.18% 가정)가 2026 개편(매도측 합계 ≈0.20%)과 어긋남 — 세율을 단일 진실원천으로 승격하고 보정 필요(§Specifications C). reproduction-first TDD, look-ahead 부재는 **테스트된 불변식**. 전용 브랜치 필요(현 main 은 SPEC-043 머지 후). | onigunsow |

---

## Overview (Environment & Assumptions)

### Environment
- LLM 기반 자율 트레이딩 봇. 페이퍼(모의) 운영 중, 라이브 임박(SPEC-042).
- 알파 원천 = LLM 페르소나의 매수/매도 결정(비결정적, 메모리 의존). **백테스트 불가**.
- 룰 레이어 = 출구 룰(손절/익절/트림, SPEC-033/039/040), 포지션 사이징, 레짐 필터(SPEC-035/036). **결정적 → 백테스트 가능**.
- 현존 측정 자산:
  - `backtest/engine.py` — 가중치 기반 벡터화 백테스트(look-ahead 회피: `weights.shift(1)`).
  - `backtest/exit_sweep.py` — SPEC-037 결정적 출구 룰 sweep(단일 패스, train/test 분리 없음, in-sample 추천).
  - `edge/benchmark.py` — KOSPI 매수후보유 알파(라운드트립 기간, 원가기준 money-weighted 근사).
  - `edge/analytics.py` — 실현 순손익·승률·손익비·기대값·자산곡선·슬리피지 보정.
  - `edge/scorecard.py` — 표본 등급 + go/no-go 판정 + 한계 푸터.
  - `edge/roundtrips.py` — orders FIFO 원가 매칭 → 라운드트립.
  - `data/pykrx_adapter.py` + `data/cache.py` — pykrx OHLCV 페치 + Postgres `ohlcv` 캐시(`cached_ohlcv(source, symbol, start, end)`).
- vectorbt 는 **현재 미설치이며 의도적으로 회피**됨(pyproject.toml L46-47: "avoiding vectorbt's heavy numba/llvmlite dependency").

### Assumptions
- pykrx OHLCV 는 `cached_ohlcv` 로 [start, end] 구간 조회가 가능하고, point-in-time 윈도를 명시적 종료일 슬라이스로 강제할 수 있다(미래 바 미포함).
- 룰 레이어 검증으로 충분하지 않다는 것은 알려져 있다(LLM 결정 레이어 미검증). 이 SPEC 은 **필요조건이지 충분조건이 아님**을 정직하게 명시한다(§ADR-002).
- 기존 `engine.run()` 의 look-ahead 회피 의미론(prior-day weights)과 `exit_sweep.simulate_position` 의 결정적 출구 의미론(stop 우선, intraday low/high)은 정확하며 보존한다.
- `LIVE_FEE_SELL_*` 세율 상수는 **단일 진실원천**으로 통합 가능하고, 분산된 매직넘버(decision.jinja 프롬프트 포함)는 그 원천을 참조하도록 좁힐 수 있다.
- vectorbt 추가는 sweep 의 파라미터 그리드 평가와 walk-forward 윈도 루프에 한해 값을 한다. 기존 `engine.py` 의 역할(가중치 백테스트)은 대체하지 않는다.

---

## Requirements (EARS) — 3 groups → A (walk-forward), B (benchmark), C (cost-adjusted scorecard)

### Group A (REQ-044-A) — Walk-forward / point-in-time backtest harness

- **REQ-044-A1 (Ubiquitous, point-in-time discipline):**
  The harness shall feed historical OHLCV strictly point-in-time: for any in-sample or
  forward window ending at date `T`, only bars with `ts <= T` shall be visible to parameter
  fitting and simulation.

- **REQ-044-A2 (Unwanted, no look-ahead):**
  The harness shall NOT allow any forward (future) bar to influence in-sample parameter
  selection. A look-ahead leak shall be a tested invariant (a deliberately future-leaking
  fixture must FAIL an assertion in the test suite).

- **REQ-044-A3 (Event-driven, rolling train/test split):**
  WHEN the harness runs over a date range, it shall split the range into rolling
  in-sample (train) and out-of-sample (forward/test) windows, fit exit/sizing/regime
  parameters on each train window, and evaluate the fitted parameters on the immediately
  following unseen test window.

- **REQ-044-A4 (Ubiquitous, OOS metrics only):**
  The harness shall report out-of-sample (forward-window) metrics as its primary output;
  in-sample fitted metrics may be reported only as a clearly-labelled secondary diagnostic,
  never as the headline result.

- **REQ-044-A5 (State-driven, reuse deterministic exit semantics):**
  WHERE exit rules are simulated, the harness shall reuse the existing deterministic exit
  semantics from `exit_sweep.simulate_position` (stop checked before take, intraday
  low/high, `max(-stop_atr_mult*atr, floor)`), not a re-implemented variant.

- **REQ-044-A6 (Optional, vectorbt for sweeps/windows):**
  WHERE a parameter grid or a rolling-window loop is evaluated, the harness MAY use
  vectorbt to vectorise the sweep and the walk-forward windows; the existing
  `backtest/engine.py` weighted-backtest path shall remain unchanged and shall NOT be
  routed through vectorbt.

- **REQ-044-A7 (Trackable, determinism & testability):**
  The harness shall be deterministic given fixed OHLCV input and shall accept injected
  price data (no live pykrx fetch in tests), so walk-forward splits and OOS metrics are
  reproducible in unit tests.

### Group B (REQ-044-B) — KOSPI buy-and-hold benchmark tracker

- **REQ-044-B1 (Ubiquitous, daily-report visibility):**
  The system shall surface cumulative excess return versus a passive KOSPI buy-and-hold
  over the same period in the daily report, so "did we beat just holding the index" is
  always visible.

- **REQ-044-B2 (Event-driven, period alignment):**
  WHEN the benchmark is computed, it shall use the same period boundaries as the strategy
  measurement (round-trip span and/or daily-snapshot span) and shall label the comparison
  basis (money-weighted cost-basis vs time-weighted) exactly as the source data permits.

- **REQ-044-B3 (State-driven, graceful unavailability):**
  IF KOSPI index closes are unavailable for the period, THEN the benchmark shall report
  `available=False` and the daily report shall state "알파 미확인" rather than fabricating
  a comparison.

- **REQ-044-B4 (Ubiquitous, extend not duplicate):**
  The benchmark tracker shall extend `edge/benchmark.py` (reusing `kospi_closes`, the
  `cached_ohlcv` cache, and `Benchmark`), not introduce a parallel KOSPI-loading path.

### Group C (REQ-044-C) — Cost-adjusted expectancy scorecard + tax single source of truth

- **REQ-044-C1 (Ubiquitous, net expectancy):**
  The scorecard shall compute and surface net expectancy =
  `(win% × avg_win) − (loss% × avg_loss) − round_trip_cost`, with round-trip cost taken
  from the configurable cost source (REQ-044-C5), not a hard-coded literal.

- **REQ-044-C2 (Ubiquitous, additional risk-adjusted metrics):**
  The scorecard shall extend `edge/analytics.py` / `edge/scorecard.py` with Sortino ratio,
  profit factor (already present — keep), and a cost-adjusted win rate (win rate counting
  only trades whose net-of-round-trip-cost return is positive).

- **REQ-044-C3 (Unwanted, no scattered magic tax numbers):**
  The system shall NOT scatter transaction-tax / fee literals across modules. Fee, tax,
  and slippage rates shall resolve from a single configurable source in `config.py`;
  duplicated literals (including the `decision.jinja` prompt comment) shall reference or be
  derived from that source.

- **REQ-044-C4 (Event-driven, 2026 tax correction):**
  WHEN the round-trip cost is computed for the KOSPI sell side, it shall reflect the 2026
  Korean securities-transaction-tax structure (combined sell-side ≈ 0.20%: 거래세 ≈ 0.05%
  + 농특세 0.15%), correcting the current `LIVE_FEE_SELL_KOSPI = 0.00345` constant which
  encodes the pre-2026 거래세 0.18%. (The component split is medium-uncertainty; the
  ≈0.20% combined sell-side total is the solid target — see §Open Questions Q-C1.)

- **REQ-044-C5 (Ubiquitous, configurable cost source):**
  Fee/tax/slippage shall be exposed as named, documented constants in `config.py` with a
  single derivation for `LIVE_ROUND_TRIP_COST_KOSPI` / `_KOSDAQ`, so a future rate change
  is a one-line edit and every consumer (analytics, exit_sweep, scorecard, walk-forward
  harness) reads the same value.

- **REQ-044-C6 (Ubiquitous, honesty footer preserved):**
  The scorecard shall preserve the existing always-on limitations footer and GO/NO-GO
  semantics (sample grade, KOSPI-alpha must-pass), now driven by the cost-adjusted
  metrics — a good headline number with insufficient sample still cannot be GO.

---

## Specifications

### A — Walk-forward harness
- New module (e.g. `backtest/walk_forward.py`) that wraps `exit_sweep` primitives. It accepts
  injected OHLCV (dict[symbol, list[Bar]] or a thin pykrx-backed loader using `cached_ohlcv`)
  and a rolling window schedule (train_len, test_len, step).
- Point-in-time enforcement: for each window ending at `T`, slice bars to `ts <= T` BEFORE
  fitting; the simulator never indexes beyond the window. The look-ahead invariant test feeds
  a fixture where a future bar would change the pick and asserts it does not (REQ-044-A2).
- Per train window: run `run_sweep` (reused) to fit `ExitParams`; apply the fitted params on
  the next test window via `run_exit_simulation`; collect OOS `SweepMetrics`.
- Output: aggregate OOS metrics across all test windows (the headline), plus per-window rows
  and the in-sample diagnostic (clearly labelled). Replaces the single-pass `recommend()` flow
  as the trustworthy result — `recommend()` stays as the in-sample primitive it wraps.
- vectorbt: used only to vectorise the grid evaluation and/or the window loop (REQ-044-A6).
  Add to pyproject `optional-dependencies` (e.g. `backtest` extra) so the heavy numba/llvmlite
  chain is NOT forced on the runtime container — see §Open Questions Q-A2.

### B — KOSPI benchmark
- Extend `edge/benchmark.py`: add a cumulative-excess-return surface consumable by the daily
  report. Reuse `kospi_closes` + `Benchmark`. Period alignment per REQ-044-B2; graceful
  `available=False` preserved (REQ-044-B3).
- Daily report wiring: surface "전략 vs KOSPI 매수후보유, 누적 초과수익" line. Exact daily-report
  integration point per §Open Questions Q-B1.

### C — Cost-adjusted scorecard + tax SoT
- `config.py` (`src/trading/config.py` L106-133): make fee/tax/slippage the single source.
  Recommended shape (finalised in run): split `LIVE_FEE_SELL_KOSPI` into named components
  (`KOSPI_BROKER_FEE`, `KOSPI_TX_TAX`, `KOSPI_RURAL_TAX`) and derive the sell total + round-trip
  cost from them, with a comment citing the 2026 reform. `decision.jinja` L37-38 comment derives
  from / references the same constants.
- **Tax finding (flag, do not silently change):** current `LIVE_FEE_SELL_KOSPI = 0.00345`
  = 0.015% 수수료 + 0.18% 거래세 + 0.15% 농특세. The 2026 structure is sell-side ≈ 0.20% combined
  (거래세 ≈ 0.05% + 농특세 0.15%), i.e. KOSPI sell total ≈ 0.215% (incl. 0.015% fee) → round-trip
  KOSPI ≈ 0.0023 (not 0.0036). KOSDAQ 2026 거래세 ≈ 0.20% (no 농특세) so KOSDAQ sell ≈ 0.215%
  stays ≈ unchanged. The correction makes the bot's cost model LESS pessimistic on KOSPI — which
  matters because the GO/NO-GO gate and exit-profit floor (decision.jinja, SPEC-040) are tuned to
  the old number. The exact 거래세 component is Q-C1; the ≈0.20% combined is the solid target.
- `edge/analytics.py`: add Sortino (downside-deviation Sharpe analogue on per-trade returns) and
  cost-adjusted win rate. Keep existing profit factor / expectancy / slippage-adjusted block.
- `edge/scorecard.py`: net-expectancy line (REQ-044-C1) and the new metrics in `render()`; GO/NO-GO
  and footer semantics preserved (REQ-044-C6). The `GO_LIVE_GATES` list is orthogonal (kept).

### Migration
- Reserve **migration 030** only if walk-forward OOS results or a tax-version stamp must be
  persisted (currently in-process / report-only → likely none). Latest applied is 031
  (`031_orders_status_expired.sql`); 027 / 030 are vacant. Confirm in run.

## @MX annotations (targets)

- `@MX:ANCHOR` — walk-forward point-in-time slice (high fan_in / safety invariant): every train
  and test window derives only from bars with `ts <= T`. Invariant: no future bar reaches
  parameter fitting; an injected future-leaking fixture must fail the guard.
- `@MX:ANCHOR` — `config.py` cost single-source: `LIVE_ROUND_TRIP_COST_KOSPI/_KOSDAQ` is the only
  derivation; analytics / exit_sweep / scorecard / walk-forward all read it. Invariant: changing a
  rate is a one-line edit with no other literal to chase.
- `@MX:NOTE` — benchmark cumulative-excess surface: documents money-weighted vs time-weighted
  labelling and graceful `available=False`.
- `@MX:WARN` — vectorbt optional dependency boundary (heavy numba/llvmlite): the runtime container
  must not import vectorbt; it is a `backtest` extra used by the offline harness only.

## Traceability

| REQ | Group | Reused / new asset | Verification (acceptance) |
|---|---|---|---|
| REQ-044-A1~A2 | A walk-forward | new `walk_forward.py`; `exit_sweep` bars | AC-1 (look-ahead invariant) |
| REQ-044-A3~A4 | A walk-forward | rolling train/test; OOS aggregate | AC-2 |
| REQ-044-A5 | A walk-forward | `exit_sweep.simulate_position` (reused) | AC-2 |
| REQ-044-A6 | A walk-forward | vectorbt (optional extra); `engine.py` unchanged | AC-2, AC-6 |
| REQ-044-A7 | A walk-forward | injected OHLCV; deterministic | AC-1, AC-2 |
| REQ-044-B1~B4 | B benchmark | `edge/benchmark.py` (`kospi_closes`, `Benchmark`); daily report | AC-3 |
| REQ-044-C1~C2 | C scorecard | `edge/analytics.py`, `edge/scorecard.py` | AC-4 |
| REQ-044-C3~C5 | C scorecard | `config.py` L106-133; `decision.jinja` L37-38 | AC-5 |
| REQ-044-C6 | C scorecard | `scorecard.render()` GO/NO-GO + footer | AC-4 |

---

## ADR (Architecture Decision Records)

### ADR-001 — vectorbt as an optional `backtest` extra, alongside (not replacing) `engine.py`

- **Context:** vectorbt is currently avoided (pyproject L46-47) due to numba/llvmlite weight. But
  walk-forward over a parameter grid × rolling windows is exactly where vectorisation earns its
  keep, and the existing pure-Python `exit_sweep` loop will be slow at that scale.
- **Decision:** add vectorbt as an **optional** dependency (`[project.optional-dependencies] backtest`),
  used ONLY by the offline walk-forward harness for grid/window vectorisation. The runtime trading
  container does NOT install or import it. `backtest/engine.py` (weighted backtest) stays as-is.
- **Consequences:** offline analysis gains speed; runtime image stays light; a `@MX:WARN` guards the
  import boundary. Risk: a contributor importing vectorbt into a runtime path — mitigated by the
  boundary tag + a test asserting runtime modules do not import vectorbt.
- **Alternatives rejected:** (a) rip out `engine.py` and go all-vectorbt — violates the operator's
  "keep the engine philosophy" lock and bloats the runtime; (b) pure-Python walk-forward only —
  works but slow on large grids, no leverage from a maintained library.

### ADR-002 — This harness measures the RULE layer; the LLM decision layer stays unvalidated (necessary, not sufficient)

- **Context:** Walk-forward validation adds real complexity and itself risks "meta-overfitting" — the
  walk-forward setup (window sizes, grid, robustness scoring) can be tuned until it flatters the
  rules. Wiecki/Quantopian (2016) found backtest Sharpe has near-zero predictive power for live
  Sharpe (R² < 0.025). Over-trusting OOS numbers would just relocate the overfitting.
- **Decision:** scope this SPEC honestly to the **rule layer only** (exit/sizing/regime). The harness
  output is "a robust, OOS-tested rule parameter region", NEVER "the strategy is profitable". The LLM
  entry/exit decision edge remains un-backtestable (non-deterministic, memorization bias) and is
  confirmed ONLY by forward paper trading via the existing `edge-report` (SPEC-026/037) and the KOSPI
  benchmark (Group B). Every harness output carries this caveat in its rationale (the `exit_sweep`
  module already does this — preserve and extend it).
- **Consequences:** the SPEC delivers a necessary-but-not-sufficient measurement layer. The
  complementary track — a future **hybrid-redesign SPEC (LLM → signal, rules → sizing/exit)** that
  makes more of the alpha rule-expressible and therefore testable — is explicitly named as out of
  scope here and recommended as the next strategic SPEC.
- **Alternatives rejected:** (a) attempt to replay/backtest LLM decisions — injects look-ahead and
  memorization bias, the exact anti-pattern the 2026 literature flags; (b) present OOS Sharpe as a
  go-live signal — contradicted by the R² < 0.025 evidence; the GO/NO-GO gate stays anchored on
  forward paper edge + KOSPI alpha + sample sufficiency, not backtest Sharpe.

---

## Exclusions (What NOT to Build)

- **NOT** a backtest or replay of LLM persona decisions. The alpha source is non-deterministic and
  memorization-biased; replaying it injects look-ahead. (ADR-002.)
- **NOT** a replacement of `backtest/engine.py`. Its weighted-backtest philosophy and look-ahead
  semantics are preserved; vectorbt is additive and optional only.
- **NOT** a go-live signal from backtest/OOS Sharpe. Backtest Sharpe has near-zero predictive power
  for live Sharpe (R² < 0.025); GO/NO-GO stays anchored on forward paper edge + KOSPI alpha + sample.
- **NOT** the hybrid LLM→signal / rules→sizing redesign. That is the complementary next-track SPEC,
  explicitly out of scope here.
- **NOT** a runtime dependency on vectorbt. The trading container must not import it.
- **NOT** a new KOSPI data path. Group B extends `edge/benchmark.py`; no parallel index loader.
- **NOT** intraday / tick-level simulation. The harness operates on daily OHLCV bars (pykrx grain).

---

## Open Questions (for operator)

- **Q-A1 (walk-forward window schedule):** default train/test/step lengths? (e.g. train 12mo /
  test 3mo / step 3mo.) Drives how many OOS windows the limited pykrx history yields.
- **Q-A2 (vectorbt install path):** confirm vectorbt as a `backtest` optional extra (offline only),
  NOT a runtime dependency — acceptable given the numba/llvmlite weight the project deliberately
  avoided?
- **Q-B1 (daily-report integration point):** which existing daily-report builder surfaces the
  KOSPI cumulative-excess line, and is the report CLI-only (cost 0) like SPEC-030?
- **Q-C1 (2026 거래세 component split):** the ≈0.20% combined KOSPI sell-side is solid, but the
  거래세-vs-농특세 split (≈0.05% + 0.15%) is medium-uncertainty. Confirm the exact 2026 거래세 rate
  before flipping `LIVE_FEE_SELL_KOSPI` — and note the correction makes the cost model LESS
  pessimistic on KOSPI, which may need a re-tune of the SPEC-040 exit-profit floor / GO-NO-GO gate.
- **Q-C2 (Sortino MAR):** target/minimum-acceptable-return for the Sortino downside deviation —
  0 (default) or the round-trip cost hurdle?

---

## Quality Gates (TRUST 5)

- **Tested:** 85%+ coverage for all new code (walk-forward harness, new analytics metrics, config
  cost-source). The no-look-ahead property (REQ-044-A2) is a **tested invariant**: a future-leaking
  fixture must fail an assertion. Walk-forward OOS metrics deterministic under injected OHLCV.
- **Readable:** Korean docstrings per `code_comments: ko`; reuse existing module naming.
- **Unified:** ruff/black clean; reuse `exit_sweep` / `benchmark` / `analytics` primitives, no
  duplicated cost or KOSPI-loading logic.
- **Secured:** no credential handling in scope; no network in tests (injected data).
- **Trackable:** `@MX:ANCHOR` on the point-in-time slice and the cost single-source; conventional
  Korean commits referencing SPEC-TRADING-044.
