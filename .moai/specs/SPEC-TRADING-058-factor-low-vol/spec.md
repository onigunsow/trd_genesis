---
id: SPEC-TRADING-058
version: 0.2.0
status: draft
created: 2026-06-21
updated: 2026-06-21
author: oni
priority: high
issue_number: null
labels: [factor, low-volatility, backtest, edge, research, paper-only]
---

# SPEC-TRADING-058 — 증거 기반 정량 팩터 전략: 저변동성 (Low-Volatility Factor, 저베타)

## HISTORY

- 2026-06-21 v0.2.0 (draft): 적대적 감사(REVISE 0.58, blocking 4건) + 운영자 범위 결정 반영. **범위를 저변동성/저베타 팩터 단독으로 축소**하고, 퀄리티(총수익성) 및 저변동성+퀄리티 결합은 **SPEC-059로 연기**한다. 연기 근거(감사로 검증됨): 퀄리티 입력 데이터가 시스템에 부재 — `fetch_fundamentals`(pykrx_adapter.py:57-89)는 PER/PBR/EPS/BPS/DIV/DPS만 적재하고, `fundamentals` 테이블 스키마(cache.py:117-151)에는 revenue/COGS/total_assets 컬럼이 없어 총수익성 계산이 불가능하다. 저변동성은 OHLCV만으로 산출되며 SPEC-057 M1이 이미 공급하므로 즉시 진행 가능하고, 문헌상 한국 #1 강건 팩터다. 추가 수정: **(B3)** GO 게이트 알파 모순 해소 — `scorecard.decide`가 소비하는 `benchmark.py`의 money-weighted 알파(benchmark.py:120-131, 자기라벨 "시간가중 아님")를 GO 게이트에서 금지하고, `engine.run`의 time-weighted equity-curve를 scorecard 입력으로 변환하는 **신규 어댑터**를 요구사항·인벤토리에 추가. **(B4)** 생존편향 게이트를 fail-CLOSED로 재작성(057 결과 부재/미기록 시 알파 부호보고 금지·bound only). **(M-a)** n 정의를 리밸런스 주기 수로 고정. **(M-b)** Bonferroni + 50% 할인 + scorecard GO를 단일 AND 판정 함수로 합성. **(M-c)** walk-forward를 리밸런스별 반복 point-in-time engine.run으로 정의(단일 full-sample을 OOS로 보고 금지). **(m-1/m-2)** YAML labels 추가, 저변동성 lookback을 단일 고정 기본값(120 거래일)으로 확정. 보존: 생존편향 상속(이제 fail-closed), engine.run 하니스(run_walk_forward 아님), 라이브/페이퍼-전용 3중 [HARD], 리서치 함정 제외 목록, Bonferroni+50%할인 선적용, "알파 없음=유효한 성공" 정직 프레이밍.
- 2026-06-21 v0.1.0 (draft): 최초 작성(저변동성+퀄리티+결합). 측정된 사실(SPEC-051 M2 OOS: LLM 재량 종목선정 expectancy -14,840 KRW/거래, alpha -11%p vs KOSPI, confidence↔PnL Spearman -0.455 = 반예측적)과 회의적 문헌 리뷰를 토대로 기계적(non-LLM) 팩터 전략을 명세. v0.2.0에서 저변동성 단독으로 축소.

---

## 1. 배경 (Environment)

### 1.1 측정된 사실 (코드/실측에서 검증됨, 재유도 금지)

- 현재 "전략"은 Claude LLM 페르소나(`decision.py`)가 기계적 스크리너 후보에서 **재량적으로** 종목을 고르는 것이며, 그 엣지는 **측정상 음수**다: expectancy **-14,840 KRW/거래**, alpha **-11%p vs KOSPI**, confidence↔PnL Spearman **-0.455** (확신할수록 더 틀림). 판정: NO-GO / INSUFFICIENT (SPEC-051 M2 OOS, n=8).
- 시스템에는 백테스트된 진입 알파가 없다(SPEC-057이 측정 능력을 구축 중).
- 라이브 트레이딩은 3중 게이트이며 3 게이트 전부 False. 검증 게이트(`validation_gate.py`)는 마이너스 기대값을 이유로 기본 REJECT 한다.
- 비용 모델은 이미 존재: `backtest/engine.py`의 `DEFAULT_FEE_RATE=0.00015` / `DEFAULT_TAX_RATE=0.0018` / `DEFAULT_SLIPPAGE=0.0005` (engine.py:21-23). `engine.run`은 `prices`(종가 DataFrame) + `weights`(목표 비중 DataFrame)를 받아 **time-weighted equity curve**를 산출하고(engine.py:70), `BacktestResult.equity_curve` + `BacktestResult.daily_returns`(engine.py:34-35,101-102)로 반환하며, 비용을 매수/매도 비중 변화에 부과한다(engine.py:67).
- GO/NO-GO 스코어카드는 이미 존재: `scorecard.py`의 `decide(analytics, benchmark)`(scorecard.py:49). GO 기준 = 슬리피지 보정 후 expectancy>0 AND profit_factor>1.0 AND KOSPI alpha>0 AND n>=`_MIN_SAMPLE`(=30). `validation_gate.py`는 이를 소비한다.
- **[감사 검증] GO 게이트 알파 모순**: `scorecard.decide`는 `benchmark.alpha_pct`(scorecard.py:55)를 소비하는데, 이 값은 `benchmark.py:120-131`에서 **money-weighted**(실투입 원가 대비 집계, 자기라벨 "money-weighted 근사", "시간가중 아님")로 산출된다. 따라서 `scorecard.py`를 그대로 재사용하면 058이 금지하려는 money-weighted 알파가 GO 게이트로 재유입된다. → B3 어댑터로 해소(REQ-058-M2-4a).
- **[감사 검증] 퀄리티 입력 데이터 부재**: `fetch_fundamentals`(pykrx_adapter.py:57-89)는 PER/PBR/EPS/BPS/DIV/DPS만 적재하고, `upsert_fundamentals`의 `fundamentals` 스키마(cache.py:117-151)에는 revenue/COGS/total_assets 컬럼이 없다. 총수익성=(revenue−COGS)/total_assets는 **현 데이터 계층에서 계산 불가**. → 퀄리티는 SPEC-059로 연기(아래 7절 DEFERRED).

### 1.2 전략 가설 (문헌 근거)

회의적 문헌 리뷰의 결론은 다음과 같다. 한국 시장에서:

- **모멘텀은 함정**이다(reversal 경향) — 명시적으로 제외.
- **저변동성/저베타**가 강건한 생존 팩터다. KOSPI200 저위험 이상현상은 직접 복제된다(Kim-Lee 2018, Pacific-Basin Finance Journal). 본 SPEC이 채택하는 **단일 팩터**다.
- 퀄리티(총수익성)도 강건 팩터지만 입력 데이터 부재로 SPEC-059로 연기(7절 DEFERRED).
- 한국 비용(매도세 0.18-0.23% + 슬리피지)을 견디는 것은 **저회전(<50%/월)** 전략뿐이다. 순진한 1/N 등가중이 최적화를 이긴다(DeMiguel-Garlappi-Uppal 2009).
- ML/DL과 팩터 타이밍은 소형 리테일 계좌에 과적합 함정이다. FINSABER는 LLM 경로가 본 프로젝트가 측정한 그대로 실패함을 보였다.
- **정직한 base rate**: 라이브에서 비용 차감 후 양의 알파를 발견할 확률 ~20-30%. 백테스트 알파는 출판 후 감쇠(McLean-Pontiff)로 **~50% 할인**해야 한다.

따라서 이 SPEC은 "기계적·저회전·등가중 **저변동성** 팩터가 비용/생존편향 보정 후 KOSPI를 이기는가"에 대한 **신뢰할 수 있는 답**을 만든다. 양의 알파 발견은 목표가 아니다(아래 6절).

## 2. 가정 (Assumptions)

- **[HARD] SPEC-057 의존**: SPEC-057 M1이 (a) `pykrx_adapter`를 감싸는 point-in-time 과거 OHLCV 로더와 (b) as-of-date 유니버스 surface(상폐/제외 종목 포함, ADR-057-4의 "재사용 가능한 diagnosis-agnostic surface")를 제공한다. 058은 이를 **재사용**하며 새 로더를 만들지 않는다. **저변동성 팩터는 OHLCV만 필요하므로 SPEC-057 M1이 공급하는 가격 데이터로 충분하다**(펀더멘털 불필요). SPEC-057 M1이 미완이면 058 M2/M3는 BLOCKED(8절 ADR-058-5).
- **[HARD] 생존편향 게이트 상속 (fail-CLOSED)**: SPEC-057 REQ-057-M1-6 PRECONDITION GATE가 058에도 그대로 적용된다. pykrx가 point-in-time 멤버십(상폐 포함)을 줄 수 없으면 **또는 그 게이트 결과가 부재/미기록이면**, 058 저변동성 백테스트도 동등하게 생존편향되어 있다(REQ-058-M2-5). 이는 -14,840을 만든 바로 그 오류다. 부재는 기본적으로 bound-only를 함의하며 절대 signed alpha를 함의하지 않는다.
- 저변동성/저베타는 trailing **120 거래일**(단일 고정 기본값, 단일 config 소스) 일간 수익률 변동성(또는 KOSPI 대비 베타)으로 측정 가능하며, **OHLCV만으로 산출되어 펀더멘털 결측에 강건하다** — 이것이 저변동성을 첫 팩터로 택한 핵심 이유다(퀄리티는 데이터 부재로 불가).
- 비용 모델은 이미 존재(`engine.py`). 058은 이를 재사용한다. **단, 이 상수들은 보수적이지 않다**(SPEC-057 D3와 동일): 세금 0.18%는 실제 한국 매도세 0.18-0.23% 범위의 하단(floor)이고 슬리피지 0.05%는 소형/저유동성에 낙관적이다 → 알파를 상향 편향시킨다.
- 유니버스 hard gate(market_cap>1조, 거래대금>100억)는 SPEC-057 M2의 gate 특성화를 재사용한다(`daily_screen._screen_ticker:239,246`).
- **[B3] GO 게이트 알파는 time-weighted여야 한다**: `scorecard.decide`가 소비하는 `benchmark.alpha_pct`는 money-weighted이므로(benchmark.py:120-131), 058은 `engine.run`의 time-weighted equity-curve를 scorecard 입력으로 변환하는 어댑터를 만들고(REQ-058-M2-4a), money-weighted 알파를 GO 게이트 어디에서도 쓰지 않는다.
- 이 전략의 **유효한 결과에는 "저변동성 팩터가 비용·생존편향 보정 후 양의 OOS 알파를 보이지 않는다"가 포함된다** — 이는 실패가 아니라 성공적 결과다(6절).

## 3. 요구사항 (EARS Requirements)

### M1 — 저변동성 팩터 신호 계산 (Low-Volatility Factor Signal, 순수 함수)

SPEC-057 M1 데이터(OHLCV) 위에서 저변동성 팩터 신호를 결정적 순수 함수로 산출한다.

- **REQ-058-M1-1** (Ubiquitous): The system **shall** compute a **low-volatility / low-beta** factor as a pure function of point-in-time OHLCV — ranking each eligible symbol by trailing daily-return volatility (window = **120 trading days fixed default**, from a single config source) or KOSPI-relative beta — selecting the **lowest** quantile as the long set, restricted to the KOSPI large-cap universe (the `market_cap > 1조` / `거래대금 > 100억` gate from `daily_screen._screen_ticker:239,246`).
- **REQ-058-M1-2** (Ubiquitous): Each factor signal function **shall** be deterministic and reproducible — given the same `(symbol set, as-of date, point-in-time bars)` it **shall** return the identical ranking. The functions take injected data and **shall not** perform live pykrx/DB I/O (testable on fixtures per C-4).
- **REQ-058-M1-3** (State-Driven): **While** computing the factor at rebalance date T, the function **shall** use only information available at T (trailing window strictly before/at T for prices), preserving the SPEC-057 no-look-ahead invariant.
- **REQ-058-M1-4** (Event-Driven): **When** a symbol lacks sufficient history to compute the factor at T (e.g. < 120 trailing bars), the function **shall** exclude that symbol from the ranking explicitly rather than imputing a fabricated factor value.

### M2 — 포트폴리오 구성 + 비용 인지 백테스트 (Portfolio Construction + Cost-Aware Backtest)

최저 변동성 분위 → 1/N 등가중, 월간 리밸런스, `engine.run` 비용 모델, 생존편향 게이트 상속(fail-closed), time-weighted 알파 어댑터.

- **REQ-058-M2-1** (Ubiquitous) [HARD] — 1/N 등가중 + 월간 리밸런스: The system **shall** construct the low-volatility portfolio as **1/N equal weight** over the selected lowest-volatility quantile (~10-20 names) with **MONTHLY rebalance**. Low turnover is a **hard design requirement, not a preference** — equal weight and monthly cadence are fixed (no per-name optimization, no more frequent rebalance).
- **REQ-058-M2-2** (Ubiquitous) [HARD] — 회전 예산: The constructed portfolio **shall** keep monthly turnover **below 50% per month**, and the backtest **shall** report measured turnover. **If** measured turnover exceeds the 50%/month budget, **then** the result **shall** be flagged as violating the low-turnover survival constraint (Korean costs only survive low turnover per the literature).
- **REQ-058-M2-3** (Ubiquitous) — 비용 인지 백테스트 via engine.run: The system **shall** backtest the low-volatility portfolio through **`engine.run`** (prices + monthly-rebalanced 1/N weights → time-weighted equity curve), applying the existing `DEFAULT_FEE_RATE` + `DEFAULT_TAX_RATE` + `DEFAULT_SLIPPAGE` cost model (engine.py:21-23,67). The system **shall not** create a new cost model and **shall not** use `run_walk_forward` (that is the exit-rule sweep harness, a different harness).
- **REQ-058-M2-4** (Ubiquitous) [HARD] — 알파 정의 고정 (time-weighted only): The portfolio's edge **shall** be reported as **net OOS alpha vs KOSPI, defined as the time-weighted equity-curve return from `engine.run`** (after costs), consistent with SPEC-057 REQ-057-M2-3 / ADR-057-5. The system **shall not** use `benchmark.py`'s money-weighted cost-basis alpha (benchmark.py:120-131) anywhere — neither in reporting nor in the GO gate.
- **REQ-058-M2-4a** (Ubiquitous) [HARD] — time-weighted → scorecard 어댑터 (B3): The system **shall** provide a **NEW adapter** that converts `engine.run`'s `BacktestResult` (time-weighted `equity_curve` / `daily_returns`, engine.py:34-35,101-102) into the inputs that `scorecard.decide` consumes — i.e. an `Analytics`-shaped object (expectancy, profit_factor, n) and a `Benchmark`-shaped object whose `alpha_pct` is the **TIME-WEIGHTED** strategy-vs-KOSPI alpha (equity-curve return − KOSPI return). [HARD] The adapter **shall not** populate `Benchmark.alpha_pct` from `benchmark.py`'s money-weighted aggregate. The GO gate (`scorecard.decide`) **shall** be fed exclusively through this adapter so that the forbidden money-weighted alpha cannot enter the GO decision.
- **REQ-058-M2-5** (State-Driven) [HARD] — 생존편향 게이트 상속, fail-CLOSED (the single most important constraint): **While** SPEC-057's REQ-057-M1-6 precondition gate reports that point-in-time membership (incl. delisted) is **NOT** achievable, **OR its result is absent / null / unrecorded**, every 058 factor backtest result **shall** be force-downgraded to a labeled **"survivorship-biased upper bound — sign-of-alpha reporting forbidden, bound only"** value, and the M3 verdict **shall** headline survivorship bias as the dominant caveat. Absence of a recorded "achievable" verdict **shall** imply bound-only, **never** signed alpha (no fail-open default). A factor strategy backtested on survivors-only is worthless and **shall not** claim alpha.
- **REQ-058-M2-6** (State-Driven): **While** SPEC-057's precondition gate explicitly reports that point-in-time membership IS achievable (a positively recorded verdict), the system **shall** reconstruct the as-of-date KOSPI large-cap universe per monthly rebalance window (delisted losers present) before forming the portfolio, reusing the SPEC-057 M1-6a universe surface.

### M3 — Walk-forward OOS 검증 + 다중검정 + 50% 할인 + GO/NO-GO 단일 판정 함수 + 정직한 판정 (페이퍼 전용 승급)

- **REQ-058-M3-1** (Ubiquitous) [HARD] — Walk-forward OOS (반복 point-in-time): The system **shall** validate the portfolio with **walk-forward out-of-sample** evaluation defined as **repeated point-in-time `engine.run` at each rebalance T over subsequent unseen windows**: factor ranking and selection at each rebalance T use only data available at T, and performance is measured on the subsequent unseen window; results are concatenated across rebalances. The system **shall not** report a single full-sample `engine.run` over the entire history as if it were OOS.
- **REQ-058-M3-2** (Ubiquitous) [HARD] — 다중검정 보정: The factor's alpha **shall** be reported with a **multiple-testing correction** (Bonferroni-adjusted significance level α/N, where **N = number of tested factors; for this low-vol-only SPEC N=1 so α/1 = α**), reusing the SPEC-057 REQ-057-M2-3a pattern. The mechanism **shall** be kept generic so that when SPEC-059 adds quality + combined factors (N≥2) the same correction tightens automatically. A factor **shall not** be labeled PASS merely because its alpha sign is positive; it MUST clear the Bonferroni-adjusted bar.
- **REQ-058-M3-3** (Ubiquitous) [HARD] — 50% 백테스트 할인: Before any GO judgment, the measured backtest alpha **shall** be discounted by **50%** (McLean-Pontiff post-publication decay) and the GO/NO-GO judgment **shall** be made on the discounted figure. The report **shall** show both raw and discounted alpha.
- **REQ-058-M3-4** (Ubiquitous) [HARD] — 기존 GO 게이트 재사용 (임계 약화 금지): GO/NO-GO **shall** be determined via the existing `scorecard.py` / `validation_gate.py`, fed exclusively through the REQ-058-M2-4a adapter (time-weighted inputs). GO requires slippage-adjusted **expectancy > 0 AND profit_factor > 1.0 AND KOSPI alpha > 0 AND n >= 30** (`_MIN_SAMPLE`). The system **shall not** weaken these thresholds, lower `_MIN_SAMPLE`, or introduce a parallel lenient gate.
- **REQ-058-M3-4a** (Ubiquitous) [HARD] — 단일 AND 판정 함수 (M-b): The final verdict **shall** be computed by a **single composed decision function** that ANDs three independent conditions: (1) Bonferroni-adjusted significance passed (REQ-058-M3-2), (2) 50%-discounted alpha used (REQ-058-M3-3), and (3) `scorecard.decide` returns GO on adapter-supplied time-weighted inputs (REQ-058-M3-4). A positive alpha sign alone **shall not** PASS — all three must hold. The composed function **shall** short-circuit to a non-PASS verdict if survivorship downgrade (REQ-058-M2-5) is in effect.
- **REQ-058-M3-5** (State-Driven) [HARD] — 표본 floor (n = 리밸런스 주기 수): **n shall be defined as the number of monthly REBALANCE PERIODS in the walk-forward OOS sequence, NOT the number of round-trip trades.** The adapter (REQ-058-M2-4a) **shall** set `Analytics.n_closed` from the rebalance-period count, not from a trade count, so that a high intra-rebalance trade count cannot leak a PASS before ~30 monthly rebalances accumulate. **While** the rebalance-period OOS sample is below the floor (n < 30 rebalance periods), the result **shall** be labeled **INCONCLUSIVE** — never PASS — regardless of alpha sign or magnitude.
- **REQ-058-M3-6** (Event-Driven) [HARD] — 페이퍼 전용 승급: **When** the factor earns a GO verdict, it **shall** be promoted to **PAPER out-of-sample collection only — NOT live**. The system **shall not** touch `order.py` / `smoke_gate.py` / live gates / `live_unlocked`. Live promotion is out of scope for this SPEC.
- **REQ-058-M3-7** (Event-Driven) [HARD] — 정직한 판정: **When** the low-volatility factor shows no net-positive OOS alpha after costs and survivorship correction, the report **shall** state this plainly as a **valid, successful outcome** — not a failure, error, or incomplete run.
- **REQ-058-M3-8** (Ubiquitous) [HARD] — 비용/생존편향 정직성 플래그: The report **shall** flag that (a) the cost model uses a tax FLOOR (0.18%, low end of the real 0.18-0.23% Korean sell-tax range) and large-cap slippage (0.05%) — real small/illiquid costs may exceed these and bias alpha upward; and (b) if survivorship gate failed or was absent (REQ-058-M2-5), survivorship bias is the dominant caveat stated before any other component.

## 4. 비기능 제약 (Constraints) [HARD]

- **C-1** [HARD]: 연구/페이퍼 전용. 라이브 트레이딩 변경 없음. `order.py` / `smoke_gate.py` / 라이브 게이트 / `live_unlocked`를 절대 건드리지 않는다.
- **C-2** [HARD]: SPEC-057 M1(point-in-time 로더 + as-of-date 유니버스 surface) 및 `backtest/engine.py` 비용 모델·`edge/scorecard.py`·`edge/validation_gate.py`를 재사용. 재유도/재구현 금지(5절 인벤토리에 reused vs new 명시). 단, time-weighted→scorecard 어댑터는 신규(REQ-058-M2-4a).
- **C-3** [HARD]: Point-in-time / no-look-ahead 규율은 불변식이다(SPEC-057 M1에서 상속). Walk-forward = 리밸런스별 반복 point-in-time engine.run(REQ-058-M3-1).
- **C-4** [HARD]: 결정적이고 테스트 가능. 팩터 신호 함수·포트폴리오 구성·time-weighted 어댑터는 주입 픽스처 위에서 단위테스트 가능해야 한다(라이브 pykrx/DB 미접촉). DB/SQL 경로 변경 시 실-Postgres 통합테스트(SPEC-056) 실행 — 거짓그린 차단.
- **C-5** [HARD]: 저회전(<50%/월), 1/N 등가중, 월간 리밸런스는 **설계 요구사항**이며 튜닝 가능한 선호가 아니다.
- **C-6** [HARD]: 정직한 프레이밍이 SPEC까지 살아남아야 한다. 목표는 진실 학습이며 "저변동성 팩터 알파 없음"은 유효한 결과다. 백테스트 알파는 50% 할인 후 판정한다.
- **C-7** [HARD]: GO 게이트에 money-weighted 알파(benchmark.py:120-131) 사용 금지. scorecard는 오직 REQ-058-M2-4a 어댑터(time-weighted)를 통해서만 공급받는다(B3).

## 5. 재사용 vs 신규 인벤토리 (Reused vs New) [HARD]

### 재사용 (REUSE — 수정 금지 또는 호출만)

| 파일/자산 | 역할 | M |
|------|------|---|
| SPEC-057 M1 point-in-time 로더 + as-of-date 유니버스 surface | 과거 OHLCV 공급, 상폐 포함 유니버스, no-look-ahead(ADR-057-4) | M1, M2 |
| SPEC-057 REQ-057-M1-6 생존편향 PRECONDITION GATE | 멤버십/상폐 회수 가능 여부 판정 — 058이 상속(fail-closed) | M2 |
| `src/trading/backtest/engine.py` (`run`, `BacktestResult.equity_curve`/`daily_returns`, `DEFAULT_FEE_RATE`/`DEFAULT_TAX_RATE`/`DEFAULT_SLIPPAGE`) | prices+weights → time-weighted equity curve, 비용 모델 | M2 |
| `src/trading/data/pykrx_adapter.py` | 과거 OHLCV 적재(SPEC-057 로더 경유, 직접 미변경) | M1 |
| `src/trading/screener/daily_screen.py:239,246` | 유니버스 hard gate(market_cap>1조 / 거래대금>100억) 특성화 재사용 | M1, M2 |
| `src/trading/edge/scorecard.py` (`decide`, GO 기준, `_MIN_SAMPLE=30`) | GO/NO-GO 판정(임계 약화 금지, 어댑터 경유 공급) | M3 |
| `src/trading/edge/validation_gate.py` | 게이트 소비 | M3 |

### 신규 (NEW)

| 컴포넌트 | 역할 | M |
|----------|------|---|
| 저변동성 팩터 신호 모듈 (`strategy/factor/` 하위, 신규) | 저변동성/저베타 순수 함수, 결정적, no-look-ahead, 주입 OHLCV, 120일 고정 lookback | M1 |
| 포트폴리오 구성기 (신규) | 최저 변동성 분위 → 1/N 등가중, 월간 리밸런스, 회전 예산 측정, engine.run 배선 | M2 |
| **time-weighted → scorecard 어댑터 (신규, B3)** | `BacktestResult`(time-weighted equity/daily returns) → `Analytics`(expectancy/PF/n=리밸런스수) + `Benchmark`(time-weighted alpha_pct). money-weighted 알파 주입 금지 | M2, M3 |
| 팩터 OOS 검증/판정 모듈 (신규) | walk-forward OOS(반복 point-in-time engine.run) + Bonferroni + 50% 할인 + scorecard GO를 **단일 AND 판정 함수**로 합성 + 정직한 판정 + 페이퍼 전용 승급 | M3 |

주: `src/trading/edge/benchmark.py`(money-weighted, `:120-131`)는 **058이 사용하지 않는다** — GO 게이트·알파 보고 어디에서도 금지(C-7, REQ-058-M2-4). 058 알파는 신규 어댑터의 time-weighted 값으로만 산출된다.

주: `strategy/sizing/` (SPEC-046 vol-targeting)은 **리스크 정규화 사이징**이며 058의 수익 예측 팩터와 별개다 — 058 범위에서 제외(7절 EX-7).

## 6. 이 SPEC의 "이김의 정의" (Definition of Winning) [HARD]

이 SPEC은 다음 질문에 **신뢰할 수 있는 답**을 만들어내면 이긴 것이다:

> "이 계좌에 대해, 증거 기반 기계적 **저변동성** 팩터가 비용·생존편향 보정 후 KOSPI를 이기는가?"

- "이김"은 양의 알파를 **발견하는 것이 아니다.** 신뢰할 수 있는 측정과 정직한 답을 만드는 것이다.
- "저변동성 팩터가 비용·생존편향·50% 할인 보정 후 양의 OOS 알파가 없다"는 **유효하고 성공적인 결과**다.
- GO 판정을 받은 팩터조차 **페이퍼 OOS 수집으로만** 간다 — 라이브가 아니다.
- n<30 리밸런스 주기에 근거한 어떤 결론도 PASS로 취급하지 않는다.

## 7. 제외 사항 + 연기 (Exclusions & Deferred — What NOT to Build) [HARD]

### 7.1 연기 (DEFERRED to SPEC-059)

- **DEF-1** [HARD]: **퀄리티 팩터(gross profitability = (revenue − COGS) / total assets)** 는 현 데이터 계층에서 계산 불가하여 **SPEC-059로 연기**한다. 검증된 근거: `fetch_fundamentals`(pykrx_adapter.py:57-89)는 PER/PBR/EPS/BPS/DIV/DPS만 적재하고, `fundamentals` 스키마(cache.py:117-151)에는 revenue/COGS/total_assets 컬럼이 없다. SPEC-059는 (a) **DART/OpenDartReader 펀더멘털 소스**를 추가하고 (b) **filing-date point-in-time**(공시/가용일 기준, fiscal-period-end 아님)을 처리해야 한다(아래 DEF-3 = 독립 look-ahead killer).
- **DEF-2** [HARD]: **저변동성 + 퀄리티 결합 팩터** 도 퀄리티 입력에 의존하므로 SPEC-059로 함께 연기한다. 본 SPEC은 단일 팩터(저변동성)만 활성 범위로 한다.
- **DEF-3** [HARD, 059 요구사항 기록]: SPEC-059(퀄리티)는 펀더멘털을 **filing/availability date(공시·가용일)** 에 키잉해야 하며 fiscal-period-end에 키잉하면 안 된다. 그것이 불가하면 보수적 reporting lag(예: 회계분기말 + 90일)를 적용해야 한다. 이는 생존편향과 **독립된 별개의 look-ahead killer**다. **058(저변동성, 가격 전용)에는 해당 없음(N/A)** — 저변동성은 가격 데이터만 쓰고 공시 시차 문제가 없으므로 명시적으로 N/A로 기록한다.

### 7.2 제외 (EXCLUSIONS)

- **EX-1** [HARD]: **모멘텀** — 한국 reversal 함정. 시계열/횡단면 모멘텀 모두 제외.
- **EX-2** [HARD]: **단기 reversal** — 회전이 폭증해 한국 비용을 못 견딤. 제외.
- **EX-3** [HARD]: **시계열 모멘텀/추세추종(trend-following)** — 단일 종목에 부적합. 제외.
- **EX-4** [HARD]: **변동성 관리 타이밍 / Moreira-Muir vol-managed** — OOS 실패. 제외(저변동성 횡단면 랭킹과 혼동 금지: 058은 종목 횡단면 저변동성 랭킹이지 vol-managed 시장 타이밍이 아니다).
- **EX-5** [HARD]: **팩터 타이밍** — Asness가 입증한 함정이며, 실패한 LLM confidence와 같은 계열(예측적 타이밍). 제외.
- **EX-6** [HARD]: **ML/DL 팩터 동물원(factor zoo)** — 소형/협소 데이터에 과적합. 제외(Gu-Kelly-Xiu / FINSABER 근거).
- **EX-7** [HARD]: **최소분산/리스크패리티/평균분산 최적화** — 1/N에 패배(DeMiguel-Garlappi-Uppal 2009). 제외. SPEC-046 vol-targeting은 **리스크 사이징 정규화로만** 유지되며 수익 예측이 아니므로 058 범위 밖.
- **EX-8** [HARD]: 라이브 실행 경로(`order.py`, `smoke_gate.py`, 라이브 게이트, `live_unlocked`)를 건드리지 않는다. GO 팩터도 페이퍼 전용.
- **EX-9** [HARD]: GO 게이트 임계(`scorecard.py` expectancy>0/PF>1.0/alpha>0/n>=30)를 약화하거나 병렬 관대 게이트를 만들지 않는다.
- **EX-10** [HARD]: 새 비용 모델/수수료 상수를 만들지 않는다 — `engine.py` 상수 재사용.
- **EX-11** [HARD]: `benchmark.py`의 money-weighted 알파를 GO 게이트나 알파 보고에 사용하지 않는다(B3, C-7). 058 알파는 신규 어댑터의 time-weighted 값 전용.
- **EX-12** [HARD]: 펀더멘털 기반 퀄리티는 본 SPEC 범위 밖(DEF-1로 SPEC-059 연기). 058 M1/M2/M3는 펀더멘털을 읽지 않는다.

## 8. ADR (설계 결정)

- **ADR-058-1 — 기계적 팩터 > LLM/ML**: LLM 재량(SPEC-051 실측 음수 엣지·반예측적 confidence)과 ML 팩터 동물원(과적합)은 모두 본 계좌 규모/데이터에서 실패가 입증/측정되었다. 기계적·해석가능·저파라미터 팩터(저변동성)는 한국에서 강건한 생존 팩터다(Kim-Lee 2018). 재현 가능하고 OOS 백테스트가 원리적으로 가능하다는 점이 LLM 대비 결정적 우위다.
- **ADR-058-1a — 저변동성 우선, 퀄리티 연기(데이터 강제)**: 저변동성은 OHLCV만으로 산출되고 SPEC-057 M1이 이미 공급하며 문헌상 한국 #1 강건 팩터다. 반면 퀄리티(총수익성)는 입력(revenue/COGS/total_assets)이 데이터 계층에 부재함이 감사로 검증되었다(cache.py:117-151). 따라서 데이터가 갖춰진 저변동성을 먼저 출하하고, 퀄리티+결합은 DART/OpenDartReader 통합을 선행조건으로 SPEC-059로 연기한다. 이는 우선순위가 아니라 데이터 가용성에 의한 강제 결정이다.
- **ADR-058-2 — 1/N 등가중 > 최적화**: DeMiguel-Garlappi-Uppal(2009)은 순진한 1/N이 표본외에서 최적화 기반 비중을 이긴다고 입증했다. 추정오차가 최적화의 이론적 이득을 잡아먹는다. 소형 리테일 계좌에서는 더욱 그렇다. 따라서 비중은 1/N 고정(EX-7).
- **ADR-058-3 — 월간·저회전(<50%/월)**: 한국 비용(매도세 0.18-0.23% + 슬리피지)은 고회전 전략을 죽인다. 문헌상 살아남는 것은 저회전 전략뿐이다. 월간 리밸런스 + 회전 예산을 **설계 요구사항**으로 고정한다(C-5, REQ-058-M2-1/2).
- **ADR-058-4 — SPEC-057에서 생존편향 상속, fail-CLOSED (가장 중요)**: 생존종목만으로 백테스트한 팩터 전략은 무가치하다 — 이는 -14,840을 만든 바로 그 오류다. 058은 SPEC-057의 REQ-057-M1-6 게이트를 상속한다: 멤버십/상폐 회수 불가 시 **또는 게이트 결과가 부재/미기록 시** 058 알파는 "생존편향 상한·부호보고 금지·bound only"로 강제 다운그레이드된다(REQ-058-M2-5). 부재가 signed alpha로 fail-open 되어선 안 된다 — 명시적 "achievable" 기록만이 signed alpha를 허용한다.
- **ADR-058-5 — SPEC-057 M1 선행 의존(BLOCKED 조건)**: 058 M2/M3는 057 M1의 point-in-time 로더·유니버스 surface 없이는 의미가 없다. 057 M1이 미완이면 058은 BLOCKED 상태로 보류한다(가정 절). 058 M1(저변동성 순수 함수)은 주입 픽스처로 057 M1과 병행 개발 가능하나, M2의 실데이터 백테스트는 057 M1 완료가 선행 조건이다.
- **ADR-058-6 — 백테스트 알파 50% 할인 + 정직한 base rate**: McLean-Pontiff는 출판된 이상현상이 출판 후 ~50% 감쇠함을 보였다. 라이브 양의 알파 base rate는 ~20-30%다. 따라서 GO 판정은 50% 할인된 알파 위에서 내리고(REQ-058-M3-3), GO 팩터도 페이퍼 OOS로만 보낸다(REQ-058-M3-6). 낙관 편향을 구조적으로 차단한다.
- **ADR-058-7 — 알파 정의 = time-weighted equity-curve + scorecard 어댑터 (B3, SPEC-057 ADR-057-5 상속)**: 058의 알파는 `engine.run`의 시간가중 equity-curve 수익률로 고정한다. `scorecard.decide`가 소비하는 `benchmark.alpha_pct`는 money-weighted이므로(benchmark.py:120-131), 그대로 재사용하면 금지된 money-weighted 알파가 GO 게이트로 재유입된다. 따라서 `BacktestResult`(time-weighted)를 scorecard 입력(`Analytics`/`Benchmark`)으로 변환하는 신규 어댑터를 만들고(REQ-058-M2-4a), scorecard는 오직 이 어댑터를 통해서만 공급받는다. money-weighted 알파는 058 어디에서도 쓰지 않는다(C-7, EX-11). scorecard의 GO 임계(expectancy>0/PF>1.0/alpha>0/n>=30)는 불변으로 재사용하되, 그 입력만 time-weighted로 교체한다.
- **ADR-058-8 — 단일 AND 판정 함수 (M-b)**: Bonferroni 유의성·50% 할인·scorecard GO 세 조건을 분리해 두면 어느 하나만 통과해도 PASS로 오인될 위험이 있다. 따라서 셋을 AND로 묶는 단일 합성 판정 함수를 둔다(REQ-058-M3-4a). 부호만 양수인 알파는 PASS 불가. 생존편향 다운그레이드(REQ-058-M2-5)가 작동 중이면 합성 함수는 즉시 non-PASS로 단락한다. 저변동성 단독이라 현재 N=1(Bonferroni α/1)이지만, 059가 팩터를 추가하면 동일 메커니즘이 자동으로 강화된다.

## 9. 출처 (Sources)

문헌 근거(회의적 리뷰 기반, 본 SPEC의 전략 채택/제외 결정의 토대):

- **Kim & Lee (2018)**, *Pacific-Basin Finance Journal* — KOSPI200 저위험(저변동성/저베타) 이상현상 직접 복제. 058 저변동성 팩터의 한국 근거(본 SPEC의 핵심 채택 근거).
- **McLean & Pontiff (2016)**, *Journal of Finance* — "Does Academic Research Destroy Stock Return Predictability?" 출판 후 ~50% 알파 감쇠. REQ-058-M3-3의 50% 할인 근거.
- **Novy-Marx (2013)** — gross profitability(총수익성)가 퀄리티 팩터의 강건한 형태. SPEC-059(퀄리티 연기) 정의 근거.
- **Jegadeesh & Titman (1993)** — 모멘텀 원전. 단, 한국 reversal 특성으로 058에서는 함정으로 제외(EX-1).
- **DeMiguel, Garlappi & Uppal (2009)**, *Review of Financial Studies* — "Optimal Versus Naive Diversification": 1/N이 표본외에서 최적화를 이김. ADR-058-2 / EX-7 근거.
- **Gu, Kelly & Xiu (2020)**, *Review of Financial Studies* — "Empirical Asset Pricing via Machine Learning": ML의 데이터 요구량/과적합 위험. 소형 계좌 ML 제외(EX-6) 근거.
- **Novy-Marx & Velikov (2016)** — 거래비용 차감 후 이상현상 수익성: 저회전 팩터만 비용을 견딤. ADR-058-3 근거.
- **Asness et al.** — 팩터 타이밍의 어려움(입증된 함정). EX-5 근거.
- **Moreira & Muir (2017)** — vol-managed portfolios; OOS 강건성 논쟁. EX-4 제외 근거.
- **FINSABER** — LLM 트레이딩 경로가 본 프로젝트 실측과 동일하게 실패. ADR-058-1 근거.

주: 위 인용은 회의적 리뷰 과정에서 식별된 1차 문헌이며, 본 SPEC은 이를 전략 채택/제외 결정의 근거로 사용한다. 구체 페이지/표는 구현 시 research.md에서 검증할 것(미검증 인용은 단정하지 않음).
