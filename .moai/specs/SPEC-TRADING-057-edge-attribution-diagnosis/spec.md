---
id: SPEC-TRADING-057
version: 0.2.2
status: draft
created: 2026-06-21
updated: 2026-06-21
author: oni
priority: high
issue_number: null
---

# SPEC-TRADING-057 — 엣지 귀인 진단 (Edge Attribution Diagnosis)

## HISTORY

- 2026-06-21 v0.2.2 (draft): **M1-6 생존편향 게이트 실증 완료** (trading-app 컨테이너). as-of-date 멤버십(2018 vs 오늘 67종목 상이)·상폐 OHLCV 회수(000030 미상장이나 2018-01 22봉) 모두 가능 → M1-6a(유니버스 재구성) 경로 확정, 다운그레이드 불필요. §2 가정에 실증 결과 기록.
- 2026-06-21 v0.2.1 (draft): 재감사 PASS 0.84의 비차단 N2 교정 — REQ-057-M1-1·재사용 인벤토리에서 미존재 식별자 `walk_forward.run`을 제거하고, M2 진입-포트폴리오 알파 경로가 `engine.run`(time-weighted portfolio backtest)이며 `run_walk_forward`(출구-룰 스윕)와 별개 하니스임을 명시. 구현 착수 전 교정.
- 2026-06-21 v0.2.0 (draft): 독립 적대적 감사(REVISE 0.62/1.0) 결함 반영 개정. (D1) 생존편향을 M1 PRECONDITION GATE로 승격(REQ-057-M1-6 신설: pykrx point-in-time 멤버십·상폐 OHLCV 실증 단계 + 불가 시 M2 알파를 "생존편향 상한·부호보고 금지" 강제 다운그레이드). (D2) 다중검정 보호 추가(REQ-057-M2-3에 Bonferroni 보정 + 리밸런스 표본 floor 미달 시 INCONCLUSIVE). (D4) M3 분해 방법론 명시(REQ-057-M3-1: 기계적 등가중 baseline + 순차 counterfactual + RESIDUAL 버킷 합치성 + 비용(b) 필수 정량). (D5) 알파 정의를 time-weighted equity-curve(engine.run)로 고정 + benchmark.py money-weighted와의 관계 명시. (D3) M3 비용모델 보수성 미검증 플래그 추가(세금 0.18% floor·대형주 슬리피지 가정). (D7) REQ-057-M2-1 닫힌 목록화(RSI/PER/foreign=랭킹 가능 score 피처 / market_cap·turnover=유니버스 게이트). 정직성 프레이밍(n=8 종속화·LLM 미검증·재사용 인벤토리)은 검증되어 보존됨. SPEC-058(저변동성+퀄리티 팩터 전략)이 M1 point-in-time 유니버스·과거 OHLCV 파이프라인을 공유 토대로 의존하므로 M1을 진단 전용으로 좁히지 않고 재사용 가능하게 설계.
- 2026-06-21 v0.1.0 (draft): 최초 작성. SPEC-051 M2 OOS 실측(expectancy -14,840 KRW/거래, alpha -11%p vs KOSPI, confidence↔PnL Spearman -0.455, n=8, NO-GO/INSUFFICIENT)을 받아, 운영자가 "새 알파를 만들기 전에 먼저 진단한다"고 명시적으로 결정. 이 SPEC은 기능 빌드가 아니라 **연구/진단 SPEC**이다. 3 마일스톤(M1 과거 OHLCV 파이프라인, M2 진입 신호 백테스트 가능화, M3 귀인 분해 리포트).

---

## 1. 배경 (Environment)

### 1.1 측정된 사실 (코드에서 검증됨, 재유도 금지)

- 시스템 자체 검증 게이트가 "현재 마이너스 기대값"을 이유로 기본 REJECT 한다 (`src/trading/edge/validation_gate.py`, 기본 `False`).
- 최근 측정 엣지 (SPEC-051 M2 OOS, n=8): expectancy **-14,840 KRW/거래**, alpha **-11%p vs KOSPI**, confidence↔PnL Spearman **-0.455** (반예측적 — LLM이 확신할수록 더 틀림). 판정: NO-GO / INSUFFICIENT.
- 라이브 트레이딩은 3중 게이트이며 3 게이트 전부 False.
- 실제 "전략"은 Claude LLM 페르소나(`decision.py`)가 기계적 스크리너의 일일 ~20 후보에서 **재량적(discretionary)** 종목을 고르는 것이다.
- **어디에도 백테스트된 진입 알파가 없다.**

### 1.2 근본 원인 가설

시스템은 자신의 **진입 신호(종목 선정)에 알파가 있는지 측정할 방법이 전혀 없다**:
- `walk_forward.py`는 **출구 룰만** 검증한다 (헤더 ADR-002에 명시: "LLM 결정 레이어 미검증 ... 기계적 진입 제어변수, 출구 룰만 검증").
- 과거 데이터 소스가 하니스에 **배선되어 있지 않다** (헤더에 명시: "결정성: 주입된 OHLCV 로 실행, 라이브 pykrx/DB 없음").

따라서 -14,840이 어디서 오는지, 어떤 진입 신호라도 비용 차감 후 양의 OOS 알파를 갖는지 **현재로서는 답할 수 없다.** 이 SPEC은 그 답을 만들 능력을 구축한다.

## 2. 가정 (Assumptions)

- `src/trading/data/pykrx_adapter.py`의 `fetch_ohlcv` / `fetch_fundamentals` / `fetch_flows`는 캐시/DB에 행을 적재한다(반환값은 행 수 `int`). M1은 이를 point-in-time 로더로 감싸 하니스에 공급한다 — 어댑터 자체는 변경하지 않는다.
- 스크리너 진입 피처(`daily_screen._screen_ticker`)는 DB에서 읽힌다: market_cap(>1조), 일평균 거래대금(>100억), RSI(30-70), PER(<15), foreign 5일 순매수(>0). 이 피처 정의가 M2의 측정 대상이다.
- 비용 모델은 이미 존재한다(`backtest/engine.py`의 `DEFAULT_FEE_RATE=0.00015` / `DEFAULT_SLIPPAGE=0.0005` / `DEFAULT_TAX_RATE=0.0018`). M2는 이를 재사용하며 새 비용 모델을 만들지 않는다. **단, 이 상수들은 보수적이지 않다**: 세금 0.18%는 실제 한국 매도세 0.18-0.23% 범위의 하단(floor)이고, 슬리피지 0.05%는 소형/저유동성 종목에는 낙관적이다 → 비용 과소계상은 알파를 상향 편향시키므로 M3가 이를 명시적으로 플래그한다(D3).
- pykrx로 다년치 한국 주식 일봉/펀더멘털/수급 이력을 받을 수 있다 — **단, 이는 검증되지 않은 낙관적 가정이며 M1-6이 실증한다.** 특히 (a) `get_index_portfolio_deposit_file`이 as-of-date 과거 멤버십(상폐/제외 종목 포함)을 지원하는지, (b) 상장폐지 종목의 과거 OHLCV가 회수 가능한지는 **미검증**이다. 이 두 가정이 깨지면 데이터셋은 오늘 생존한 KOSPI200 구성종목만 담게 되어 생존편향이 진단의 핵심 질문(진입 피처가 승자와 패자를 가르는가)을 직접 오염시킨다.
  - **[실증 완료 2026-06-21, trading-app 컨테이너]**: (a) `get_index_portfolio_deposit_file('1028', date='20180102')`가 2018년 시점 200종목을 반환하고 오늘과 **67종목 상이**(편출·상폐 종목 회수됨) → as-of-date 과거 멤버십 **가능**. (b) 편출 종목 `000030`(오늘 미상장=상폐)의 2018-01 OHLCV **22봉 정상 회수** → 상폐 종목 과거 OHLCV **회수 가능**. **결론: M1-6a 경로(생존편향-free as-of-date 유니버스 재구성) 달성 가능 — M1-6b 다운그레이드 불필요.** 단, KRX 세션 로그인(KRX_ID/PW, 컨테이너 환경변수)이 필요하며 샌드박스(네트워크 차단)에서는 불가.
- 이 진단의 **유효한 결과에는 "테스트한 어떤 신호도 알파가 없다"가 포함된다** — 이는 실패가 아니라 성공적 진단이다.

## 3. 요구사항 (EARS Requirements)

### M1 — 과거 OHLCV 데이터 파이프라인 (Historical OHLCV Pipeline)

진단의 토대. walk_forward 하니스가 실제 다년치 한국 주식 이력 위에서 돌게 한다.

- **REQ-057-M1-1** (Ubiquitous): The system **shall** provide a point-in-time historical loader that wraps `pykrx_adapter` (fetch_ohlcv/fetch_fundamentals/fetch_flows) and supplies bars to the backtest harness, without modifying `pykrx_adapter.py` itself. NOTE (N2 교정): M2 진입-포트폴리오 알파는 `engine.run`(prices+weights → time-weighted equity curve, `engine.py`)에서 산출된다. 이는 `run_walk_forward`(`walk_forward.py`의 출구-룰 OOS 스윕 — 함수명은 `run_walk_forward`이며 `walk_forward.run`은 존재하지 않음)와 별개 하니스다.
- **REQ-057-M1-2** (Ubiquitous): The loader **shall** be deterministic and reproducible — given the same `(symbol set, date range)`, it **shall** return byte-identical bar sequences across runs.
- **REQ-057-M1-3** (State-Driven): **While** a historical backtest is being assembled, the loader **shall** expose only bars whose `ts <= cutoff` for any cutoff date, preserving the `_slice_bars` look-ahead invariant (`walk_forward.py` @MX:ANCHOR).
- **REQ-057-M1-4** (Event-Driven): **When** the requested historical range exceeds the locally cached/DB-available range, the loader **shall** report the coverage gap explicitly (missing symbols/dates) rather than silently returning partial data.
- **REQ-057-M1-5** (Unwanted): The loader **shall not** inject any future-dated bar, survivorship-biased universe, or restated fundamental into a training window.
- **REQ-057-M1-6** (Ubiquitous) [HARD] — 생존편향 PRECONDITION GATE: Before any alpha is measured, M1 **shall** empirically establish two facts and record them as a precondition result: (1) whether pykrx `get_index_portfolio_deposit_file` supports **as-of-date historical KOSPI200 membership including delisted/removed constituents**, and (2) whether **delisted-stock historical OHLCV is retrievable**. The current loader path (`universe.py:80`, `kospi200_backfill.py:71-78,143-159`) fetches ONLY today's surviving constituents — this gate determines whether point-in-time reconstruction is achievable at all.
  - **REQ-057-M1-6a** (State-Driven): **While** point-in-time membership (incl. delisted) IS achievable, M1 **shall** reconstruct the as-of-date universe per rebalance window and supply it to M2 (delisted losers present in the dataset). The reconstruction MUST be a reusable, diagnosis-agnostic point-in-time universe surface (see ADR-057-4 — SPEC-058 factor backtests depend on the same foundation).
  - **REQ-057-M1-6b** (State-Driven) [HARD]: **While** point-in-time membership is NOT achievable, M2 alpha output **shall** be force-downgraded to a labeled **"survivorship-biased upper bound — sign-of-alpha reporting forbidden, bound only"** value, and M3 **shall** headline survivorship bias as the dominant caveat (the single most load-bearing limitation, stated before any other component).

### M2 — 진입 신호 백테스트 가능화 (Entry-Signal Backtestability, lift ADR-002 limitation)

기계적 스크리너 진입 피처가 과거 알파를 갖는지 측정한다. **LLM은 백테스트하지 않는다** (계속 미검증 상태). 기계적 후보 신호만 검증한다.

- **REQ-057-M2-1** (Ubiquitous) — 닫힌 측정 목록 (closed list, per `daily_screen._screen_ticker`): The mechanical screener entry criteria are **asymmetric** and **shall** be treated in two distinct classes (no open "any other criterion"):
  - **(A) Rankable SCORE features — measured for per-feature alpha**: `RSI band` (`:267`, +2.0 when 30-70), `PER` (`:272`, +1.5 when 0<PER<15), `foreign 5d net inflow` (`:277`, +1.0, +0.5 when >50억). These produce a continuous score and CAN form a ranked portfolio. For each, the system **shall** measure whether a portfolio formed on that feature beats KOSPI out-of-sample after the `engine.py` cost model. (The `market_cap > 10조` bonus at `:285` is a tie-breaking score nudge, folded into the market_cap gate effect — not measured as a standalone alpha feature.)
  - **(B) Universe-defining HARD GATES — NOT per-feature portfolios**: `market_cap > 1조` (`:239`, `return None` cutoff) and `turnover/거래대금 > 100억` (`:246`, `return None` cutoff). These are binary all-pass/all-fail filters and CANNOT form a ranking portfolio. The system **shall** characterize their effect as the **universe filter** (how the eligible set changes), not as a per-feature alpha portfolio.
- **REQ-057-M2-2** (State-Driven): **While** forming each feature-based portfolio for a rebalance date T, the system **shall** rank/select using only information available at T (point-in-time fundamentals and flows), preserving the M1 no-look-ahead invariant.
- **REQ-057-M2-3** (Ubiquitous) — 알파 정의 고정 + 다중검정 보호: Each rankable feature's measured edge **shall** be reported as **net OOS alpha vs KOSPI, defined as the time-weighted equity-curve return from `engine.run`** (after `DEFAULT_FEE_RATE` + `DEFAULT_SLIPPAGE` + `DEFAULT_TAX_RATE`), NOT the money-weighted cost-basis aggregate used by `benchmark.py:120-131` (which is explicitly labeled "money-weighted 근사, not time-weighted" at `benchmark.py:4`). M3's component decomposition MUST use this single time-weighted definition for consistency; the relationship to benchmark.py's money-weighted measure **shall** be stated, not silently mixed.
  - **REQ-057-M2-3a** (Ubiquitous) [HARD] — 다중검정 보정: Because N rankable features (currently 3: RSI/PER/foreign) are tested against KOSPI, each feature's alpha **shall** be reported with a **multiple-testing correction** (Bonferroni-adjusted significance level α/N for N tested features). A feature **shall not** be labeled PASS merely because its sign is positive; it MUST clear the Bonferroni-adjusted significance bar.
  - **REQ-057-M2-3b** (State-Driven) [HARD] — 표본 floor: **While** a feature's rebalance sample is below a stated floor (default 30 rebalances), the result **shall** be labeled **INCONCLUSIVE** — never PASS — regardless of alpha sign or magnitude.
- **REQ-057-M2-4** (Unwanted): The system **shall not** backtest, score, or claim alpha for the LLM discretionary decision layer — the LLM remains explicitly unvalidated (ADR-002 for the LLM layer is preserved; only the MECHANICAL entry features become backtestable).
- **REQ-057-M2-5** (Optional): **Where** a combined/composite mechanical signal (e.g., screener's full OR-of-criteria pass) can be expressed deterministically, the system **may** also measure its net OOS alpha as a baseline candidate.

### M3 — 귀인 분해 리포트 (Attribution Decomposition Report)

측정된 -14,840 KRW/거래가 어디서 오는지 단일 리포트로 분해한다.

- **REQ-057-M3-1** (Ubiquitous) — 분해 방법론 명시: The system **shall** produce a single attribution report that decomposes the measured -14,840 KRW/trade into attributable components: (a) entry signal quality, (b) cost/slippage/tax drag, (c) exit timing, (d) position sizing, (e) LLM-discretion-vs-mechanical delta. Because these components are inherently interdependent (e.g. LLM-discretion-delta overlaps with entry by definition), the report **shall** use this specified methodology, not an unspecified split:
  - **Baseline**: a mechanical equal-weight portfolio of the screener's candidate set (no LLM discretion, no sizing skew) over the same period.
  - **Sequential counterfactuals**: derive each component by swapping ONE factor at a time relative to the baseline (entry: mechanical-feature portfolio vs baseline; exit: actual exit timing vs baseline exit; sizing: actual position sizes vs equal-weight; LLM-delta: actual LLM-selected portfolio vs mechanical-feature portfolio). Each swap's marginal effect is its attributed component.
  - **RESIDUAL bucket** [HARD]: an explicit residual component **shall** be included so that (a)+(b)+(c)+(d)+(e)+residual **sums to the measured total** (-14,840 KRW/trade). The report MUST show this sum-consistency check.
  - **Mandatory quantified components**: component **(b) cost/slippage/tax drag is MANDATORY and MUST be quantified** — it is directly computable from the `engine.py` cost model and is never eligible for the "insufficient data" valve. Components (a), (c), (d), (e) may be labeled insufficient per REQ-057-M3-5 only when the underlying point-in-time data is genuinely absent (not as a blanket escape).
- **REQ-057-M3-1b** (Ubiquitous) [HARD] — 비용모델 보수성 플래그 (D3, cost-side analogue of the n=8 honesty flag): The report **shall** flag that "the cost model uses a tax FLOOR (0.18%, low end of the real 0.18-0.23% Korean sell-tax range) and large-cap slippage assumptions (0.05%); real costs — especially for small/illiquid names — may exceed these, which biases measured alpha upward."
- **REQ-057-M3-2** (Ubiquitous): The report **shall** reuse `postmortem.py` (4-category classification + persona attribution), `confidence.py` (Spearman/Pearson conf↔PnL), and `roundtrips.py`/`trade_stats.py` (round-trip ledger + per-trade stats) — it **shall not** reimplement these.
- **REQ-057-M3-3** (Ubiquitous) [HARD]: The report **shall** honestly flag that the live-fill postmortem (n=8, synthetic SELL fills) is anecdotal / statistically near-worthless, and that the load-bearing evidence is the M1/M2 historical backtest — **not** the 8 paper trades.
- **REQ-057-M3-4** (Event-Driven): **When** M2 finds that no tested mechanical entry feature has positive net OOS alpha, the report **shall** state this conclusion plainly as a valid, successful diagnostic outcome (not an error or incomplete run).
- **REQ-057-M3-5** (State-Driven): **While** any attribution component cannot be quantified from available data, the report **shall** label that component "insufficient data" rather than emitting a fabricated number.

## 4. 비기능 제약 (Constraints) [HARD]

- **C-1** [HARD]: 페이퍼/연구 전용. 라이브 트레이딩 변경 없음, 신규 실행 경로 없음. `order.py` / `smoke_gate.py` / 라이브 게이트를 절대 건드리지 않는다.
- **C-2** [HARD]: 기존 `edge/*` 및 `backtest/*` 모듈 재사용. 재유도/재구현 금지 (5절 인벤토리에 reused vs new 명시).
- **C-3** [HARD]: Point-in-time / no-look-ahead 규율은 불변식이다 (`walk_forward.py`에 이미 @MX:ANCHOR 존재).
- **C-4** [HARD]: 결정적이고 테스트 가능. 신규 백테스트 코드는 주입 픽스처 위에서 테스트 가능해야 한다 (프로젝트는 거짓그린을 죽이기 위해 실-Postgres 통합테스트를 막 추가함 — 동일 기준 적용).
- **C-5** [HARD]: 정직한 프레이밍이 SPEC까지 살아남아야 한다. 목표는 진실 학습(어떤 신호라도 알파가 있는가?)이며, "테스트한 어떤 신호도 알파 없음"은 유효한 결과다.

## 5. 재사용 vs 신규 인벤토리 (Reused vs New) [HARD]

리빌드 방지를 위해 명시한다.

### 재사용 (REUSE — 수정 금지 또는 호출만)

| 파일 | 역할 | M |
|------|------|---|
| `src/trading/data/pykrx_adapter.py` | 과거 OHLCV/펀더멘털/수급 적재(호출만, 미변경) | M1 |
| `src/trading/backtest/walk_forward.py` | `run_walk_forward`=출구-룰 OOS 스윕(진입 알파 아님), `_slice_bars` point-in-time 불변식 참조 | M1 |
| `src/trading/backtest/exit_sweep.py` | 출구 시뮬레이션 의미론 | M2 |
| `src/trading/backtest/engine.py` | **M2 포트폴리오 백테스트 하니스** `engine.run`(prices+weights→time-weighted equity curve) + `DEFAULT_FEE_RATE`/`DEFAULT_SLIPPAGE`/`DEFAULT_TAX_RATE` 비용 모델 | M2 |
| `src/trading/screener/daily_screen.py` | 진입 피처 정의 — gate(market_cap`:239`/거래대금`:246`) vs score(RSI`:267`/PER`:272`/foreign`:277`) | M2 |
| `src/trading/data/universe.py`, `kospi200_backfill.py` | 현 유니버스 로더(오늘 생존종목만) — M1-6 게이트의 측정 기준선 | M1 |
| `src/trading/edge/benchmark.py` | money-weighted 알파(`:120-131`, "not time-weighted" 라벨) — M2 time-weighted와 대비/관계 명시 대상 | M2, M3 |
| `src/trading/edge/postmortem.py` | 4분류 + persona 귀인 | M3 |
| `src/trading/edge/confidence.py` | conf↔PnL Spearman/Pearson (`_spearman`) | M3 |
| `src/trading/edge/roundtrips.py` | round-trip 원장 | M3 |
| `src/trading/edge/trade_stats.py` | per-trade 통계 | M3 |

### 신규 (NEW)

| 컴포넌트 | 역할 | M |
|----------|------|---|
| point-in-time historical 로더 + as-of-date 유니버스 (신규 모듈, `backtest/` 하위) | pykrx_adapter → walk_forward 배선, no-look-ahead, 결정적, M1-6 생존편향 게이트, **재사용 가능(SPEC-058 의존)** | M1 |
| 진입 피처 OOS 알파 측정기 (신규 모듈) | score 피처별 포트폴리오 vs KOSPI(time-weighted), 비용차감 OOS, point-in-time, Bonferroni + 표본 floor | M2 |
| 귀인 분해 리포트 (신규 모듈) | 5컴포넌트 + RESIDUAL 합치성 분해(baseline + 순차 counterfactual), edge/* 재사용, n=8·비용·생존편향 정직성 플래그 | M3 |

## 6. 이 SPEC의 "이김의 정의" (Definition of Winning) [HARD]

이 SPEC은 다음 질문에 **신뢰할 수 있는 답**을 만들어내면 이긴 것이다:

> "-14,840 KRW/거래는 어디서 오는가, 그리고 비용 차감 후 양의 OOS 알파를 갖는 진입 신호가 하나라도 있는가?"

- "이김"은 양의 알파를 **발견하는 것이 아니다.** 신뢰할 수 있는 측정 능력과 정직한 답을 만드는 것이다.
- "테스트한 어떤 기계적 진입 신호도 비용 차감 후 양의 OOS 알파가 없다"는 **유효하고 성공적인 진단**이다.
- n=8 페이퍼 트레이드에 근거한 어떤 결론도 load-bearing 증거로 취급하지 않는다 — load-bearing 증거는 M1/M2 과거 백테스트다.

## 7. 제외 사항 (Exclusions — What NOT to Build) [HARD]

- **EX-1**: 새 알파/진입 전략을 만들지 않는다. 이것은 진단이지 알파 빌드가 아니다 (운영자가 "진단 먼저"라고 명시).
- **EX-2**: LLM 결정 레이어를 백테스트하거나 검증하지 않는다 — LLM은 미검증 상태로 유지된다 (ADR-002 LLM 부분 보존).
- **EX-3**: 라이브 실행 경로(`order.py`, `smoke_gate.py`, 라이브 게이트)를 건드리지 않는다.
- **EX-4**: `pykrx_adapter.py` / `walk_forward.py` / `engine.py` / `exit_sweep.py` / `edge/*`의 기존 동작을 변경하지 않는다(호출/래핑만).
- **EX-5**: 새 비용 모델/수수료 상수를 만들지 않는다 — `engine.py`의 기존 상수를 재사용한다.
- **EX-6**: 검증 게이트(`validation_gate.py`)의 기본 REJECT를 풀거나 실거래를 활성화하지 않는다.

## 8. ADR (설계 결정)

- **ADR-057-1 — ADR-002 부분적 해제**: SPEC-044의 ADR-002는 (a) "출구 룰만 검증" (b) "LLM 미검증" 두 가지를 묶었다. 이 SPEC은 (a)만 해제한다 — **기계적** 진입 피처는 백테스트 가능해진다. (b)는 보존 — LLM 재량 레이어는 여전히 검증하지 않는다. 이유: LLM은 결정적으로 재현 불가하여 OOS 백테스트가 원리적으로 불가능(SPEC-044/메모리 ADR-002 근거 유지).
- **ADR-057-2 — load-bearing 증거 = 과거 백테스트**: n=8 라이브/페이퍼 표본은 통계적으로 무의미에 가깝다(메모리·confidence._spearman의 `len < 3` 가드와 동일 철학). 진단의 무게중심은 다년치 M1/M2 OOS에 둔다. n=8은 일화로만 인용.
- **ADR-057-3 — 어댑터 미변경, 래핑만**: `pykrx_adapter`는 행 수를 반환하는 적재기다. 하니스가 기대하는 in-memory bar 시퀀스로의 변환은 신규 로더가 담당해 어댑터의 기존 호출자(스크리너 등)에 회귀를 주지 않는다.
- **ADR-057-4 — M1 point-in-time 유니버스는 진단 전용이 아니라 공유 토대**: M1-6a의 as-of-date 유니버스 재구성과 과거 OHLCV 파이프라인은 후속 SPEC-058(저변동성+퀄리티 팩터 전략 — 연구상 한국에서 살아남는 팩터; 모멘텀은 한국에서 함정, ML은 과적합 취약)이 팩터 백테스트에 동일하게 의존한다. 따라서 로더/유니버스 인터페이스는 진단 리포트에만 묶이는 형태(diagnosis-specific)가 아니라 임의 피처/팩터 백테스트가 재사용할 수 있는 일반 surface로 설계한다. 단, 이 SPEC의 범위는 진단까지이며 팩터 전략 자체는 SPEC-058로 분리한다(EX-1 보존).
- **ADR-057-5 — 알파 정의 = time-weighted equity-curve (D5)**: M2/M3의 알파는 `engine.run`의 시간가중 equity-curve 수익률로 고정한다. `benchmark.py`의 money-weighted(원가기준 집계, `:120-131`) 측정은 라이브 누적 초과수익 surface 용도로 보존하되, M2/M3 백테스트 분해와 혼용하지 않는다. 두 정의의 관계(동일 신호도 자금 가중 방식에 따라 값이 달라짐)를 리포트에 명시한다.
