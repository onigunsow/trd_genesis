---
id: SPEC-TRADING-059
version: 0.1.0
status: draft
created: 2026-06-25
updated: 2026-06-25
author: oni
priority: high
issue_number: null
labels: [factor, quality, low-volatility, combined, backtest, edge, research, paper-only]
---

# SPEC-TRADING-059 — 증거 기반 정량 팩터 전략: 저변동성 + 퀄리티 결합 (058 M4+ 확장)

> 이것은 SPEC-058(저변동성 단독, completed)의 **연속 마일스톤(M4+)**이다. 058이
> 이름으로 예약한 퀄리티 + 결합을 구현한다. **plan only — 미구현.** 운영자 검토 후
> 별도 run 단계에서 구현한다. 058/057 본문은 수정하지 않는다.

## HISTORY

- 2026-06-25 v0.1.0 (draft): 최초 작성. SPEC-058(저변동성 단독, completed)이 DEF-1/2/3·
  REQ-058-M3-2에서 이름으로 예약한 퀄리티 + 저변동성×퀄리티 결합을 명세. 코드 검증으로
  확정: 기업 재무제표(revenue/COGS/total_assets/부채)는 데이터 계층에 **부재**
  (`fundamentals` 스키마 = market_cap/per/pbr/eps/bps/div_yield/dps, mig 006). 따라서
  Novy-Marx 정본 총수익성은 **추가 연기**하고, **기존 fundamentals 컬럼에서 파생 가능한
  ROE(EPS/BPS)를 최소 가용 퀄리티 셋**으로 채택(신규 데이터 출처·스키마 0). 단 펀더멘털
  **이력 backfill·point-in-time 펀더멘털 로더·상폐 펀더멘털 회수 실증**이 부재하여 본
  SPEC의 중심 데이터 의존성·리스크다(057 OHLCV는 backfill됐으나 펀더멘털은 forward-only).
  058 하니스(reconstruct_universe / engine.run / adapt_to_scorecard / apply_bonferroni /
  apply_alpha_haircut / compose_verdict / scorecard.decide)를 전부 **호출만** 한다. 정직
  프레이밍("결합이 저변동 단독을 못 이김 = 유효한 성공", 페이퍼 전용) 보존.

---

## 1. 배경 (Environment)

### 1.1 측정된 사실 (코드/실측 검증, 재유도 금지)

- 058 저변동성 단독 팩터는 구현·검증 완료(`status: completed`)이며 퀄리티·결합을 DEF-1/2/3
  으로 **SPEC-059에 명시적 위임**했다. REQ-058-M3-2는 "SPEC-059가 팩터를 추가하면(N≥2)
  동일 Bonferroni 보정이 자동 강화"라고 메커니즘을 미리 배선했다.
- **퀄리티 정본 입력 부재**: `fetch_fundamentals`(pykrx_adapter.py:57-89)는
  PER/PBR/EPS/BPS/DIV/DPS만 적재하고, `fundamentals` 스키마(006_fundamentals_flows.sql:4-16)에
  revenue/COGS/total_assets/부채 컬럼이 없다. 총수익성 = (revenue − COGS)/total_assets는
  **현 데이터 계층에서 계산 불가**(058 DEF-1 재확인). `026_edge_validation.sql:15`의
  `total_assets`는 KIS 계좌 NAV(`tot_evlu_amt`)이지 기업 총자산이 아니다.
- **퀄리티 프록시는 가용**: ROE = EPS/BPS(이익/순자산), 이익수익률 = 1/PER은 **기존
  fundamentals 컬럼에서 파생** 가능하다(신규 출처·스키마 0).
- **펀더멘털 이력 backfill 부재**: `kospi200-backfill`(scripts/kospi200_backfill_run.py)은
  **OHLCV만** backfill하고, 펀더멘털은 `refresh_market_data.py:128-189`가 **당일
  forward로만** 갱신한다. `historical_loader.py`는 OHLCV 전용이며 REQ-057-M1-5가 "소급
  펀더멘털 주입 금지"를 명시한다. → 퀄리티 walk-forward에 필요한 다년 EPS/BPS 이력(상폐
  포함)이 DB에 없다(§ R-1, 본 SPEC 중심 의존성).
- **결합 팩터 CLI 부재**: cli.py에 `lowvol|factor` 매치 0. 058의 `run_walk_forward_oos`/
  `compose_verdict`는 함수로만 존재하고 CLI 진입점이 없다 → 059가 `entry-alpha`(`:124`)
  패턴으로 `factor-alpha` CLI를 신규 추가한다.
- 비용 모델 존재: `engine.py:21-23,67` `DEFAULT_FEE_RATE=0.00015`/`DEFAULT_TAX_RATE=0.0018`/
  `DEFAULT_SLIPPAGE=0.0005`. time-weighted equity-curve(`engine.run`). 새 비용 모델 금지.
- GO 게이트 존재: `scorecard.decide`(scorecard.py:49) = expectancy>0 AND PF>1.0 AND
  alpha>0 AND n≥30(`_MIN_SAMPLE`). 058 어댑터(`adapt_to_scorecard`)가 time-weighted 입력
  공급(money-weighted benchmark.py:120-131 차단).

### 1.2 전략 가설 (문헌 근거, 058 §1.2 상속 + 퀄리티 추가)

058이 채택한 **저변동성**(Kim-Lee 2018, 한국 강건 생존 팩터)에 **퀄리티**(Novy-Marx 2013,
수익성으로 우량주 식별)를 결합한다. 리서치 결론(2026-06-24): 한국/유동시장에서 빠른 진입
신호는 가장 빨리 감쇠하며(McLean-Pontiff; 057 verdict가 RSI/PER/foreign 전부 OOS 알파
미확인으로 확증), **살아남는 조합은 저변동성 + 퀄리티(저회전)**다. 모멘텀은 한국 reversal
함정(058 EX-1), ML은 과적합(058 EX-6), 1/N > 최적화(DeMiguel 2009). 본 SPEC은 ROE 프록시
기반 퀄리티를 저변동성과 결합해 "비용·생존편향·50% 할인 보정 후 KOSPI를 이기는가, 그리고
**퀄리티 추가가 저변동 단독을 개선하는가**"에 신뢰할 수 있는 답을 만든다. 양의 알파 발견은
목표가 아니다(§6).

## 2. 가정 (Assumptions)

- **[HARD] SPEC-058 의존**: 058의 `compute_low_vol_signal`·`build_monthly_weights`·
  `measure_turnover`·`adapt_to_scorecard`·`check_survivorship_gate`·`run_walk_forward_oos`·
  `apply_bonferroni`·`apply_alpha_haircut`·`compose_verdict`·`render_verdict_report`를
  **호출만** 한다. 재유도/재구현 금지(§5 인벤토리).
- **[HARD] SPEC-057 의존**: `reconstruct_universe`(생존편향-free as-of-date 유니버스,
  achievable 플래그)·`historical_loader`(point-in-time OHLCV)·`engine.run`·`scorecard.decide`를
  재사용. 058 M1이 공급한 가격 토대 위에서 동작.
- **[HARD] 생존편향 게이트 상속 (fail-CLOSED)**: 057 REQ-057-M1-6 PRECONDITION GATE가
  059에도 적용된다(`check_survivorship_gate` 호출). achievable=False/부재 → 결합 알파는
  "생존편향 상한·부호보고 금지·bound only"로 강제 다운그레이드. 부재가 signed alpha로
  fail-open 되어선 안 된다.
- **[미검증, R-2]** pykrx가 **상폐 종목 펀더멘털**(EPS/BPS)을 as-of-date로 반환하는지
  미검증. 057 M1-6은 상폐 **OHLCV** 회수만 실증했다(058 §2). 퀄리티는 상폐 펀더멘털도
  필요하며, 불가 시 생존편향 재유입 → REQ-059-M4-6 실증 게이트로 fail-closed.
- **[미검증, R-3]** pykrx EPS/BPS가 as-of-date 시점의 **trailing-reported PIT** 값인지,
  소급 restated 값인지 미검증. restated면 약한 look-ahead → REQ-059-M4-7로 실증.
- ROE=EPS/BPS는 정본 총수익성((rev−COGS)/assets)의 **프록시**이며, 정본은 데이터 부재로
  **추가 연기**(EX-12). 프록시 한계를 리포트에 정직하게 명시한다.
- 비용 상수는 058과 동일하게 보수적이지 않다(세금 0.18% floor, 슬리피지 0.05% 대형주 가정)
  → 알파 상향 편향 → 정직성 플래그(REQ-059-M6-8).
- 이 전략의 **유효한 결과에는 "결합이 비용·생존편향·50% 할인 후 양의 OOS 알파가 없다" 또는
  "퀄리티 추가가 저변동 단독을 개선하지 않는다"가 포함된다** — 실패가 아닌 성공적 결과(§6).

## 3. 요구사항 (EARS Requirements)

### M4 — 퀄리티 팩터 신호 + 펀더멘털 PIT 데이터 토대

기존 fundamentals 컬럼에서 ROE(EPS/BPS) 퀄리티 신호를 결정적 순수 함수로 산출하고,
walk-forward에 필요한 펀더멘털 이력·PIT 로더·커버리지 게이트를 마련한다.

- **REQ-059-M4-1** (Ubiquitous): The system **shall** compute a **quality** factor as a
  pure function of point-in-time fundamentals — ranking each eligible symbol by **ROE proxy
  = EPS / BPS** (from existing `fundamentals.eps`/`fundamentals.bps`), selecting the
  **highest** quality quantile, restricted to the same KOSPI large-cap universe used by 058.
  A secondary **earnings-yield = 1/PER** ranking **shall** be computable as a robustness
  variant. The function **shall not** read any company financial-statement line item
  (revenue/COGS/total_assets) — those are absent (EX-12).
- **REQ-059-M4-2** (Ubiquitous): The quality signal function **shall** be deterministic —
  given the same `(symbol set, as-of date, point-in-time fundamentals)` it **shall** return
  the identical ranking. It takes injected data and **shall not** perform live pykrx/DB I/O
  (unit-testable on fixtures per C-4).
- **REQ-059-M4-3** (State-Driven): **While** computing quality at rebalance date T, the
  function **shall** use only fundamentals available at/before T, preserving the 057/058
  no-look-ahead invariant.
- **REQ-059-M4-4** (Event-Driven): **When** a symbol lacks fundamentals (EPS or BPS null,
  or BPS ≤ 0 making ROE undefined) at T, the function **shall** exclude that symbol
  explicitly rather than imputing a fabricated quality value.
- **REQ-059-M4-5** (Ubiquitous) [HARD] — 펀더멘털 이력 토대: The system **shall** provide
  (a) a **historical fundamentals backfill** reusing `pykrx_adapter.fetch_fundamentals`
  (no schema change — same `fundamentals` table) and (b) a **point-in-time fundamentals
  loader** analogous to `historical_loader` (ts ≤ cutoff invariant, no retroactive
  injection per REQ-057-M1-5). The system **shall not** invent a new fundamentals schema
  for ROE/earnings-yield.
- **REQ-059-M4-6** (State-Driven) [HARD] — 펀더멘털 커버리지 fail-CLOSED 게이트: **While**
  point-in-time fundamentals coverage across the walk-forward universe (incl. delisted
  names) is **NOT empirically established** (i.e. pykrx cannot return as-of-date EPS/BPS for
  removed/delisted constituents) **OR its result is absent/unrecorded**, every quality and
  combined backtest result **shall** be force-downgraded to a labeled **"fundamentals-
  survivorship-biased upper bound — sign-of-alpha reporting forbidden, bound only"** value,
  mirroring the 058 survivorship fail-closed pattern (`check_survivorship_gate`). Absence
  **shall** imply bound-only, never signed alpha.
- **REQ-059-M4-7** (Ubiquitous) — pykrx EPS PIT 실증: The system **shall** empirically
  establish and record whether pykrx EPS/BPS at a given date are **trailing-reported
  point-in-time** values (PIT-safe) or restated; **if** restated/unverifiable, **then** the
  report **shall** flag this as a residual look-ahead caveat (distinct from survivorship).

### M5 — 결합 팩터 구성 + 비용 인지 백테스트

z-score 합성으로 저변동성 + 퀄리티를 단일 결합 랭킹으로 합치고, 058 포트폴리오 plumbing을
재사용해 비용 인지 백테스트한다.

- **REQ-059-M5-1** (Ubiquitous) [HARD] — 결합 방법: The system **shall** combine the 058
  low-volatility ranking (`compute_low_vol_signal`) and the M4 quality ranking into a single
  **z-score composite** (default) — `z(inverse-vol) + z(quality)` — selecting the top-N
  composite names. A **rank-sum composite** **shall** also be computable as a robustness
  variant. The system **shall not** use a learned/ML combination weight (overfitting, 058 EX-6).
- **REQ-059-M5-2** (Ubiquitous) [HARD] — 1/N 등가중: The combined portfolio **shall** be
  **1/N equal weight** over the selected top-N composite names (~10-20), inheriting 058
  ADR-058-2 (no per-name optimization).
- **REQ-059-M5-3** (Ubiquitous) [HARD] — 월간 + 허용 밴드 저회전: The portfolio **shall**
  rebalance on a **MONTHLY** outer cadence (058 hard constraint) with an added **no-trade
  tolerance band** — a held name is replaced only when its composite rank drifts beyond a
  configurable buffer past the top-N boundary. The backtest **shall** report measured
  turnover (`measure_turnover`). **If** measured turnover exceeds **50%/month**, **then** the
  result **shall** be flagged as violating the low-turnover survival constraint. The
  tolerance band **shall not** replace the monthly cadence — it tightens turnover only.
- **REQ-059-M5-4** (Ubiquitous) — 비용 인지 백테스트 via engine.run: The system **shall**
  backtest the combined portfolio through **`engine.run`** (prices + monthly composite 1/N
  weights → time-weighted equity), reusing the existing cost constants. The system
  **shall not** create a new cost model and **shall not** use `run_walk_forward` (exit-rule
  sweep harness).
- **REQ-059-M5-5** (Ubiquitous) [HARD] — 알파 정의 + 어댑터: The combined edge **shall** be
  reported as **net OOS alpha vs KOSPI, time-weighted equity-curve return from `engine.run`**,
  supplied to the GO gate **exclusively through 058's `adapt_to_scorecard`** adapter. The
  system **shall not** use `benchmark.py` money-weighted alpha anywhere (C-7).
- **REQ-059-M5-6** (State-Driven) [HARD] — 생존편향 + 펀더멘털 게이트 상속: **While** the
  057 universe achievable flag (`check_survivorship_gate`) OR the M4 fundamentals coverage
  gate (REQ-059-M4-6) reports bound-only/absent, every combined result **shall** be force-
  downgraded to bound-only, and M6 **shall** headline whichever survivorship caveat applies
  before any other component.

### M6 — Walk-forward OOS + Bonferroni(N≥2) + 50% 할인 + 단일 AND 판정 + 결합-증분 + CLI + 페이퍼 승급

- **REQ-059-M6-1** (Ubiquitous) [HARD] — Walk-forward OOS (반복 point-in-time): The system
  **shall** validate the combined portfolio with **repeated point-in-time `engine.run` at
  each rebalance T over subsequent unseen windows**, reusing the 058 `run_walk_forward_oos`
  pattern with composite ranking. The system **shall not** report a single full-sample run as OOS.
- **REQ-059-M6-2** (Ubiquitous) [HARD] — 다중검정 보정 (N≥2 자동 강화): Alpha **shall** be
  reported with **Bonferroni correction** via 058's `apply_bonferroni(n_factors=K)` where
  **K = number of tested variants** (low-vol-alone, quality-alone, combined; default K≥2,
  ≥3 when robustness variants are tested). This is the auto-tightening 058 REQ-058-M3-2
  anticipated. A variant **shall not** be PASS merely for positive alpha sign.
- **REQ-059-M6-3** (Ubiquitous) [HARD] — 50% 백테스트 할인: Before any GO judgment, measured
  alpha **shall** be discounted by **50%** (`apply_alpha_haircut`, McLean-Pontiff) and the
  GO/NO-GO judgment **shall** use the discounted figure. The report **shall** show both raw
  and discounted alpha.
- **REQ-059-M6-4** (Ubiquitous) [HARD] — 기존 GO 게이트 재사용 (임계 약화 금지): GO/NO-GO
  **shall** be determined via `scorecard.decide` fed only through `adapt_to_scorecard`
  (time-weighted). GO requires slippage-adjusted expectancy>0 AND PF>1.0 AND KOSPI alpha>0
  AND n≥30 (`_MIN_SAMPLE`). The system **shall not** weaken thresholds or add a parallel
  lenient gate.
- **REQ-059-M6-4a** (Ubiquitous) [HARD] — 단일 AND 판정: The final verdict **shall** be
  computed by 058's `compose_verdict` (생존편향/펀더멘털 게이트 → n<30 INCONCLUSIVE →
  Bonferroni → 50%-할인 알파 → scorecard GO, ANDed). A positive alpha sign alone **shall
  not** PASS; survivorship or fundamentals downgrade short-circuits to non-PASS.
- **REQ-059-M6-4b** (Ubiquitous) [HARD] — 결합 증분 정직성 (quality-adds-value test): The
  report **shall** report the combined variant's OOS alpha **alongside low-vol-alone**, and
  state plainly whether adding quality **improves over low-vol alone**. **When** the combined
  variant does **not** exceed low-vol-alone after costs/haircut, the report **shall** state
  this as a **valid, successful outcome** (quality adds no edge here) — not a failure.
- **REQ-059-M6-5** (State-Driven) [HARD] — 표본 floor (n = 리밸런스 주기 수): **n shall be
  the number of monthly REBALANCE PERIODS** in the walk-forward OOS sequence, not round-trip
  trades (set via `adapt_to_scorecard` n_rebalances → `Analytics.n_closed`). **While** n < 30
  rebalance periods, the result **shall** be **INCONCLUSIVE** — never PASS — regardless of
  alpha sign.
- **REQ-059-M6-6** (Event-Driven) [HARD] — CLI: The system **shall** provide a `factor-alpha`
  CLI command (analogous to `entry-alpha`, dependency-injected providers + lazy pykrx import)
  that runs the combined walk-forward and emits the verdict report. The command **shall**
  reuse 058/057 functions and **shall not** reimplement the harness.
- **REQ-059-M6-7** (Event-Driven) [HARD] — 페이퍼 전용 승급: **When** the combined factor
  earns a GO verdict, it **shall** be promoted to **PAPER OOS collection only — NOT live**.
  The system **shall not** touch `order.py`/`smoke_gate.py`/live gates/`live_unlocked`.
- **REQ-059-M6-8** (Ubiquitous) [HARD] — 정직성 플래그: The report **shall** flag (a) cost
  model uses a tax FLOOR (0.18%) and large-cap slippage (0.05%) — real costs may exceed,
  biasing alpha upward; (b) survivorship/fundamentals-coverage caveat first if any gate is
  bound-only; (c) **ROE=EPS/BPS is a PROXY** for Novy-Marx gross profitability — the
  canonical (revenue−COGS)/assets is deferred for data absence (EX-12); (d) the pykrx EPS
  PIT caveat per REQ-059-M4-7 if applicable. "결합 알파 없음" or "퀄리티 미개선" **shall** be
  stated as a valid successful outcome (REQ-059-M6-4b).

## 4. 비기능 제약 (Constraints) [HARD]

- **C-1** [HARD]: 연구/페이퍼 전용. 라이브 트레이딩 변경 없음. `order.py`/`smoke_gate.py`/
  라이브 게이트/`live_unlocked` 미접촉.
- **C-2** [HARD]: 058/057의 모든 팩터·검증·유니버스·비용·scorecard 함수를 **호출만** 한다.
  재유도/재구현 금지(§5). 058/057 본문·코드 동작 미변경.
- **C-3** [HARD]: Point-in-time / no-look-ahead 불변식 상속(가격·펀더멘털 양쪽).
  Walk-forward = 리밸런스별 반복 point-in-time engine.run.
- **C-4** [HARD]: 결정적·테스트 가능. 퀄리티 신호·결합·PIT 펀더멘털 로더는 주입 픽스처
  위에서 단위테스트 가능(라이브 pykrx/DB 미접촉). DB/SQL 경로 변경 시 실-Postgres
  통합테스트(SPEC-056) 실행 — 거짓그린 차단.
- **C-5** [HARD]: 저회전(<50%/월), 1/N 등가중, 월간 cadence는 설계 요구사항(058 상속).
  허용 밴드는 회전을 낮추는 강화일 뿐 cadence 대체 아님.
- **C-6** [HARD]: 정직 프레이밍 보존. "결합 알파 없음" 및 "퀄리티가 저변동 단독을 개선하지
  않음"은 유효한 성공 결과. 백테스트 알파는 50% 할인 후 판정.
- **C-7** [HARD]: GO 게이트에 money-weighted 알파(benchmark.py:120-131) 사용 금지. scorecard는
  058 `adapt_to_scorecard`(time-weighted)만 경유.
- **C-8** [HARD]: 컨테이너 전용 실행(`docker exec trading-app trading <cmd>`). pykrx는 lazy
  import, 단위테스트는 네트워크 차단 환경에서 픽스처 주입.
- **C-9** [HARD]: 신규 데이터 스키마/컬럼 추가 금지(ROE/이익수익률은 기존 fundamentals 파생).
  정본 총수익성용 DART 재무제표 스키마는 본 SPEC 범위 밖(EX-12).

## 5. 재사용 vs 신규 인벤토리 (Reused vs New) [HARD]

### 재사용 (REUSE — 호출만)

| 파일/심볼 | 역할 | M |
|----------|------|---|
| `backtest/factor_lowvol.py` `compute_low_vol_signal` | 저변동 랭킹(결합 입력) | M5 |
| `backtest/lowvol_portfolio.py` `measure_turnover`/`adapt_to_scorecard`/`check_survivorship_gate` | 회전 측정 / time-weighted 어댑터 / 생존편향 fail-closed | M5, M6 |
| `backtest/lowvol_validation.py` `run_walk_forward_oos`(패턴)/`apply_bonferroni`/`apply_alpha_haircut`/`compose_verdict`/`render_verdict_report` | walk-forward / 다중검정(N≥2) / 50% 할인 / 단일 AND / 정직 리포트 | M6 |
| `backtest/universe_reconstructor.py` `reconstruct_universe` | 생존편향-free as-of-date 유니버스(achievable) | M5 |
| `backtest/historical_loader.py` | point-in-time OHLCV 로더 | M5 |
| `backtest/engine.py` `run` + 비용 상수 | time-weighted equity, 비용 모델 | M5, M6 |
| `data/pykrx_adapter.py` `fetch_fundamentals` | 펀더멘털 적재(이력 backfill·미변경 호출) | M4 |
| `data/cache.py` `upsert_fundamentals` / `fundamentals` 스키마 | 기존 EPS/BPS 적재(스키마 변경 0) | M4 |
| `edge/scorecard.py` `decide`/`VERDICT_GO`/`_MIN_SAMPLE=30` | GO 판정(임계 약화 금지, 어댑터 경유) | M6 |
| `backtest/entry_alpha_run.py` (CLI 패턴) | `factor-alpha` CLI 본보기 | M6 |

### 신규 (NEW)

| 컴포넌트 | 역할 | M |
|----------|------|---|
| 퀄리티 팩터 순수 함수 `compute_quality_signal` (신규) | ROE=EPS/BPS 랭킹, PIT, 결정적, 결측 명시 제외 | M4 |
| 펀더멘털 이력 backfill + point-in-time 펀더멘털 로더 (신규) | EPS/BPS 다년 이력(상폐 포함) + ts≤cutoff 불변식. 스키마 변경 0 | M4 |
| 펀더멘털 커버리지 fail-closed 게이트 + 상폐 펀더멘털 회수 실증 (신규) | check_survivorship_gate 동형. 미회수 시 bound-only | M4 |
| 결합 함수 `combine_factor_scores` (신규) | z-score/랭크 합성 → 결합 랭킹 | M5 |
| 결합 walk-forward 오케스트레이터 (신규) | 058 run_walk_forward_oos 패턴의 결합-랭킹 버전 + 결합-증분 비교 | M6 |
| `factor-alpha` CLI (신규) | 결합 walk-forward 실행 + 판정 리포트(entry-alpha 패턴) | M6 |

주: `benchmark.py` money-weighted 알파는 058과 동일하게 **금지**(C-7, EX-11).
`strategy/sizing/`(SPEC-046 vol-targeting)은 리스크 사이징이며 059 수익예측 팩터와 별개(EX-7).

## 6. 이 SPEC의 "이김의 정의" (Definition of Winning) [HARD]

다음 두 질문에 **신뢰할 수 있는 답**을 만들면 이긴 것이다:

> "저변동성 + 퀄리티 결합이 비용·생존편향·50% 할인 보정 후 KOSPI를 이기는가? 그리고
> **퀄리티 추가가 저변동 단독(058)을 개선하는가?**"

- "이김"은 양의 알파 발견이 아니다 — 신뢰할 수 있는 측정과 정직한 답이다.
- "결합이 비용·생존편향·50% 할인 후 양의 OOS 알파가 없다" 또는 "퀄리티 추가가 저변동
  단독을 개선하지 않는다"는 **유효하고 성공적인 결과**다(REQ-059-M6-4b).
- GO 판정 결합 팩터조차 **페이퍼 OOS 수집으로만** 간다 — 라이브 아님.
- n<30 리밸런스 주기 / 생존편향·펀더멘털 bound-only에 근거한 어떤 결론도 PASS 아님.

## 7. 제외 사항 + 연기 (Exclusions & Deferred — What NOT to Build) [HARD]

### 7.1 추가 연기 (FURTHER DEFERRED)

- **DEF-1** [HARD]: **정본 총수익성**(gross profitability = (revenue − COGS)/total_assets,
  Novy-Marx)·**부채비율**·**발생액**은 DART 재무제표 라인아이템·신규 스키마·전수 backfill·
  **filing-date point-in-time**(공시일 키잉, 058 DEF-3 = 독립 look-ahead killer)를 요구하여
  본 SPEC 범위 밖이다. 059는 기존 fundamentals에서 파생 가능한 **ROE 프록시**만 활성 범위로
  한다. 정본 퀄리티는 DART 재무제표 통합을 선행조건으로 향후 SPEC으로 연기한다.

### 7.2 제외 (EXCLUSIONS)

- **EX-1~EX-7** [HARD]: 058 상속 — 모멘텀(EX-1)·단기 reversal(EX-2)·추세추종(EX-3)·
  vol-managed 타이밍(EX-4)·팩터 타이밍(EX-5)·ML 팩터 동물원(EX-6)·평균분산/최소분산
  최적화(EX-7, 1/N 고정). SPEC-046 vol-targeting은 리스크 사이징으로만 유지.
- **EX-8** [HARD]: 라이브 실행 경로(`order.py`/`smoke_gate.py`/라이브 게이트/`live_unlocked`)
  미접촉. GO 결합 팩터도 페이퍼 전용.
- **EX-9** [HARD]: GO 게이트 임계(expectancy>0/PF>1.0/alpha>0/n≥30) 약화·병렬 관대 게이트 금지.
- **EX-10** [HARD]: 새 비용 모델/수수료 상수 금지 — `engine.py` 상수 재사용.
- **EX-11** [HARD]: `benchmark.py` money-weighted 알파를 GO 게이트·알파 보고에 사용 금지.
  059 알파는 058 `adapt_to_scorecard`의 time-weighted 값 전용.
- **EX-12** [HARD]: 기업 재무제표 라인아이템(revenue/COGS/total_assets/부채) 읽기·신규
  스키마·DART 재무제표 파싱 금지(DEF-1 연기). M4 퀄리티는 EPS/BPS·PER 파생만.
- **EX-13** [HARD]: ML/학습 기반 결합 가중 금지 — z-score/랭크 합성만(REQ-059-M5-1).

## 8. 위험 / 의존성 (Risks & Dependencies)

- **R-1 [최대]** 펀더멘털 이력 backfill 갭: OHLCV는 10년 backfill됐으나 펀더멘털은
  forward-only다(§1.1). 퀄리티 walk-forward는 다년 EPS/BPS 이력(상폐 포함)이 필요 →
  M5/M6는 펀더멘털 이력 backfill(REQ-059-M4-5) 완료 전 **BLOCKED**. `fetch_fundamentals`
  재사용으로 스키마 변경은 0이나 KRX 호출량·rate-limit 비용 발생. (ADR-059-3)
- **R-2** 상폐 종목 펀더멘털 회수 미검증: 057이 OHLCV는 회수 실증했으나 펀더멘털은 미검증.
  불가 시 퀄리티 생존편향 재유입 → REQ-059-M4-6 fail-closed bound-only.
- **R-3** pykrx EPS/BPS PIT 충실도 미검증(restated 가능성) → REQ-059-M4-7 실증·플래그.
- **R-4** 다중검정 인플레이션: 저변동/퀄리티/결합 + 변형 = 3~5 가설 → Bonferroni N≥2 필수.
- **R-5** 결합이 저변동 단독을 못 이길 수 있음 — "퀄리티 미개선"은 유효한 성공(REQ-059-M6-4b).
- **R-6** ROE=EPS/BPS는 정본 총수익성의 프록시 — 한계 정직 기록(REQ-059-M6-8c).
- **의존성**: SPEC-058(completed, 호출 토대) + SPEC-057(M1 유니버스/로더). 058/057 미변경.
  마이그레이션 불필요(스키마 변경 0). 펀더멘털 이력 backfill은 운영 1회성(컨테이너).

## 9. ADR (설계 결정)

- **ADR-059-1 — 신규 SPEC-059(058 의존) > 058 in-place M4**: 058이 DEF-1/2/3·REQ-058-M3-2에서
  퀄리티+결합을 이름으로 예약했고, 058은 completed이며 그 정직한 완결성(저변동 단독)을 적대적
  감사가 승인했다. 퀄리티는 058에 없던 데이터 리스크(펀더멘털 backfill·PIT 펀더멘털·상폐
  회수)를 도입하므로 별도 게이트·판정 사이클이 마땅하다. 059는 058 함수를 호출만 하므로 코드
  결합 최소·문서 분리 최대. "058 확장"은 전략 라인의 M4+ 연속으로 충족(research §5).
- **ADR-059-2 — ROE(EPS/BPS) 프록시 > 정본 총수익성(데이터 강제)**: 정본 (rev−COGS)/assets는
  데이터 부재(코드 검증). ROE=EPS/BPS는 기존 fundamentals에서 신규 출처·스키마 0으로 파생되며
  Novy-Marx 수익성의 정신(수익성으로 우량주 식별)을 담는다. 정본 퀄리티는 DART 통합 선행으로
  추가 연기. 우선순위가 아닌 데이터 가용성에 의한 강제 결정.
- **ADR-059-3 — 펀더멘털 이력 토대 선행(BLOCKED 조건)**: M5/M6은 펀더멘털 이력 backfill·PIT
  로더·상폐 회수 실증(M4) 없이는 무의미. M4 데이터 토대 미완 시 059는 BLOCKED. M4 퀄리티 순수
  함수는 주입 픽스처로 병행 개발 가능하나, 실데이터 결합 백테스트는 M4 토대 완료가 선행 조건.
- **ADR-059-4 — z-score 합성 > 순차 필터 / ML**: 두 팩터 정보를 동등하게 보존하는 z-score
  합성을 기본 채택(랭크 합성을 robustness 변형으로 병행). ML 가중은 과적합(058 EX-6) 제외.
- **ADR-059-5 — 월간 + 허용 밴드(저회전 강화)**: 058 월간 cadence를 유지하되, 리서치 권고대로
  no-trade 허용 밴드를 추가해 회전을 50%/월 예산 아래로 낮춘다. cadence 대체가 아닌 강화.
- **ADR-059-6 — 생존편향 + 펀더멘털 이중 fail-CLOSED**: 058 생존편향 게이트에 더해 펀더멘털
  커버리지 게이트를 추가한다. 둘 중 하나라도 bound-only/부재면 결합 알파는 bound-only로 강제
  다운그레이드(REQ-059-M5-6, M6-4a 단락). 부재의 fail-open 금지.
- **ADR-059-7 — 결합-증분 정직성**: 결합 알파만 보고하면 "퀄리티가 실제로 기여했는가"를 숨길
  수 있다. 따라서 결합 vs 저변동-단독을 나란히 보고하고, 미개선을 유효한 성공으로 명시한다
  (REQ-059-M6-4b). 058이 N≥2로 예고한 Bonferroni가 이 다중 비교를 자동 보정한다.
- **ADR-059-8 — 058/057 호출만, 미변경**: 058/057은 completed이며 회귀를 주면 안 된다. 059는
  전부 호출/래핑하고 신규 코드는 퀄리티·결합·펀더멘털 토대·CLI에 국한한다(§5).

## 10. 출처 (Sources)

058 §9 상속 + 퀄리티 근거:
- **Novy-Marx (2013)** — gross profitability(총수익성)가 퀄리티 팩터의 강건한 형태. ROE
  프록시의 정신적 근거이자 정본(rev−COGS/assets)의 연기 대상 정의.
- **Kim & Lee (2018)**, *Pacific-Basin Finance Journal* — KOSPI200 저위험 이상현상(058 저변동
  근거, 결합의 한 축).
- **McLean & Pontiff (2016)**, *Journal of Finance* — 출판 후 ~50% 알파 감쇠(50% 할인 근거).
- **DeMiguel, Garlappi & Uppal (2009)**, *RFS* — 1/N > 최적화(등가중·EX-7 근거).
- **Gu, Kelly & Xiu (2020)**, *RFS* / **FINSABER** — ML 과적합(EX-6/EX-13 근거).
- **Asness et al.** — 팩터 타이밍 함정(EX-5 근거).

주: 위 인용은 058 회의적 리뷰에서 식별된 1차 문헌이며 전략 채택/제외의 근거다. 구체 페이지/
표는 구현 시 research에서 검증할 것(미검증 인용은 단정하지 않음).
