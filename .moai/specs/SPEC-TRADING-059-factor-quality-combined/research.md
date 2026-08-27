# SPEC-TRADING-059 research — 저변동성 + 퀄리티 결합 팩터 (M4+ 확장)

> 본 문서는 plan 단계 코드베이스 분석 산출물이다. 모든 주장은 실제 코드 라인에 근거한다.
> 구현 코드는 작성하지 않는다. 운영자 검토 후 별도 run 단계에서 구현한다.

---

## 0. 이 SPEC의 위치 (SPEC-058의 확장)

운영자 요청은 "058을 퀄리티 팩터로 확장(저변동성 + 퀄리티 결합)"이다. SPEC-058
(`status: completed`)은 **저변동성 단독** 팩터를 출하하면서, 퀄리티와 결합을
명시적으로 **SPEC-059로 연기**했다. 058 doc의 직접 근거:

- 058 §7.1 **DEF-1** [HARD]: "퀄리티 팩터(gross profitability = (revenue − COGS) /
  total assets)는 현 데이터 계층에서 계산 불가하여 **SPEC-059로 연기**한다."
- 058 §7.1 **DEF-2** [HARD]: "저변동성 + 퀄리티 결합 팩터도 퀄리티 입력에 의존하므로
  SPEC-059로 함께 연기한다."
- 058 §7.1 **DEF-3** [HARD, 059 요구사항 기록]: "SPEC-059(퀄리티)는 펀더멘털을
  filing/availability date(공시·가용일)에 키잉해야 하며 fiscal-period-end에 키잉하면
  안 된다."
- 058 REQ-058-M3-2: "when SPEC-059 adds quality + combined factors (N≥2) the same
  correction tightens automatically."

즉 058의 설계 자체가 "퀄리티 = SPEC-059"를 이름으로 예약해 두었다. 따라서 본 작업의
구조 결정은 **058 본문을 in-place 수정(M4 추가)하지 않고, 058에 의존하는 신규
SPEC-TRADING-059를 만든다**(§5에서 정당화). 059는 058 팩터 전략 라인의 **M4+
연속 마일스톤**으로 프레이밍하여 운영자의 "058 확장" 의도를 충족한다.

---

## 1. 057/058 하니스 동작 + 재사용 가능 자산 (코드 검증 완료)

### 1.1 저변동성 팩터 코어 (058, 전부 재사용)

| 파일 | 함수/심볼 | 역할 | 059 재사용 |
|------|----------|------|-----------|
| `backtest/factor_lowvol.py` | `compute_low_vol_signal(prices_df, as_of_date, lookback=120)` → `LowVolResult(rankings, excluded_tickers)` | point-in-time 순수 함수, 120일 변동성 오름차순 랭킹(rank=1=최저변동), 이력부족 명시 제외 (`:49-126`) | **호출만** — 퀄리티 랭킹과 결합 |
| `backtest/lowvol_portfolio.py` | `build_monthly_weights(...)` (`:101-161`) | 저변동 분위 → 1/N 등가중 월간 비중 행렬 | 패턴 재사용(결합 랭킹 버전 신규) |
| `backtest/lowvol_portfolio.py` | `measure_turnover(weights_df)` (`:168-189`) | 리밸런스별 회전율 = Σ\|Δw\|/2 | **호출만** |
| `backtest/lowvol_portfolio.py` | `check_survivorship_gate(achievable)` → `SurvivorshipGateResult` (`:56-94`) | 057 achievable 플래그 → fail-CLOSED 게이트(None/False = bound-only) | **호출만** |
| `backtest/lowvol_portfolio.py` | `adapt_to_scorecard(backtest_result, kospi_returns, n_rebalances)` (`:285-313`) | `BacktestResult`(time-weighted) → `Analytics`/`Benchmark`. **money-weighted 알파 차단** | **호출만** (B3 어댑터) |
| `backtest/lowvol_validation.py` | `run_walk_forward_oos(...)` (`:214-339`) | 리밸런스별 반복 point-in-time `engine.run` (단일 full-sample 금지) | 패턴 재사용(결합 랭킹 버전 신규) |
| `backtest/lowvol_validation.py` | `apply_bonferroni(oos_returns, n_factors=1, alpha=0.05)` (`:138-207`) | Bonferroni-adjusted t-test. **`n_factors` 파라미터로 N≥2 자동 강화** (`:146` MX:NOTE) | **호출만** — 059는 `n_factors`를 변형 수로 전달 |
| `backtest/lowvol_validation.py` | `apply_alpha_haircut(raw, 0.5)` (`:119-131`) | McLean-Pontiff 50% 할인 | **호출만** |
| `backtest/lowvol_validation.py` | `compose_verdict(...)` (`:377-460`) | 생존편향→n<30→Bonferroni→할인알파→scorecard GO 단일 AND. 단락 순서 불변(MX:ANCHOR `:396`) | **호출만** |
| `backtest/lowvol_validation.py` | `render_verdict_report(...)` (`:467-538`) | 정직한 판정 텍스트(알파없음=성공, 생존편향 최우선 경고, 비용 정직성) | **호출/확장** |

### 1.2 진단 토대 (057, 전부 재사용)

| 파일 | 함수/심볼 | 역할 | 059 재사용 |
|------|----------|------|-----------|
| `backtest/universe_reconstructor.py` | `reconstruct_universe(rebalance_date, membership_provider)` → `UniverseResult(tickers, achievable)` (`:64-133`) | as-of-date KOSPI200 멤버십(상폐 포함), `achievable` 플래그(MX:ANCHOR `:14`). 058 팩터 백테스트가 동일 의존(fan_in≥2) | **호출만** — 생존편향-free 유니버스 |
| `backtest/historical_loader.py` | `load_historical_ohlcv(...)` → `LoadResult(bars, coverage_gaps)` | point-in-time OHLCV 로더, `ts<=cutoff` 불변식(MX:ANCHOR `:16`), **REQ-057-M1-5: 소급 펀더멘털 주입 금지** (`:7`) | **호출만** (가격) |
| `backtest/engine.py` | `run(prices, weights)` → `BacktestResult(equity_curve, daily_returns)` | prices+weights → time-weighted equity. 비용 `DEFAULT_FEE_RATE=0.00015`/`DEFAULT_TAX_RATE=0.0018`/`DEFAULT_SLIPPAGE=0.0005` (`:21-23,67`) | **호출만** — 새 비용 모델 금지 |
| `edge/scorecard.py` | `decide(analytics, benchmark)` → `Scorecard`, `VERDICT_GO`, `_MIN_SAMPLE=30` (`:29,49-95`) | GO = expectancy>0 AND PF>1.0 AND alpha>0 AND n≥30. 임계 약화 금지 | **호출만** (어댑터 경유) |

### 1.3 CLI 표면 (검증 완료 — 결합 팩터 CLI는 부재)

`cli.py` 디스패처:
- `:118-119` `kospi200-backfill` → `scripts.kospi200_backfill_run.main`
- `:124-125` `entry-alpha` → `backtest.entry_alpha_run._cli_main` (057 진입 피처 알파)
- `:201` `edge-report` → paper 성적 → go/no-go

**`grep lowvol|factor` on cli.py = 0 매치.** 즉 058의 `run_walk_forward_oos` /
`compose_verdict`는 함수로 존재하나 **CLI 진입점이 없다**. 059는 `entry_alpha_run.py`
의 CLI 패턴(의존성 주입 provider + lazy pykrx import + `_cli_main`)을 본떠 결합 팩터
CLI(`factor-alpha`)를 신규로 추가해야 한다.

---

## 2. 퀄리티 팩터 데이터 가용성 조사 (핵심 — 코드로 검증)

### 2.1 현 데이터 계층에 무엇이 있는가

`fundamentals` 테이블 스키마 (`db/migrations/006_fundamentals_flows.sql:4-16`):
```
ticker, ts, market_cap, per, pbr, eps, bps, div_yield, dps
```
적재 경로 `pykrx_adapter.fetch_fundamentals` (`:57-89`)는
`stock.get_market_fundamental_by_date`를 호출하며 **PER/PBR/EPS/BPS/DIV/DPS만**
반환한다(per-share·비율 지표). 재무제표 라인아이템(revenue/COGS/total_assets/
net_income/부채)은 **적재되지 않는다**.

전 마이그레이션·data/ 디렉터리 grep 결과 `revenue|cogs|total_assets|gross_profit|
operating_income|net_income|roe|debt` 매치는 단 1건:
`db/migrations/026_edge_validation.sql:15 total_assets ... -- KIS tot_evlu_amt
(총자산평가금액)`. 이것은 **계좌 잔고 평가액(포트폴리오 NAV)**이지 기업 총자산이
아니다. → 기업 재무제표 데이터는 시스템 전체에 **부재**(058 DEF-1 재확인).

`dart_adapter.py`는 존재하나 `list_recent`(`:30-90`)는 **공시 메타데이터만**
적재한다(rcept_no/corp_name/report_nm/rcept_dt → `disclosures` 테이블,
`cache.upsert_disclosure:183-203`). **재무제표 수치 fetch 없음.**

### 2.2 퀄리티 지표를 데이터 비용 기준으로 3개 티어로 분류

| 지표 | 정의 | 데이터 출처 | 신규 스키마? | 신규 backfill? | look-ahead 위험 |
|------|------|------------|------------|--------------|---------------|
| **ROE 프록시** | EPS / BPS (주당이익/주당순자산 = 이익/순자산) | **기존 `fundamentals.eps/bps`** | **불필요** | 펀더멘털 이력 backfill 필요(§2.3) | 약(pykrx EPS PIT 가정 §2.4) |
| **이익수익률** | EPS / price = 1/PER | **기존 `fundamentals.per`** | 불필요 | 동상 | 약 |
| **이익 안정성** | EPS 시계열 표준편차/추세 | 기존 eps (이력 필요) | 불필요 | 동상 + 다년 이력 | 약 |
| 총수익성(Novy-Marx 정본) | (revenue − COGS)/total_assets | **DART 재무제표(신규)** | **신규 테이블 필요** | DART 전수 backfill 필요 | **강(filing-date PIT = DEF-3)** |
| 부채비율 | 부채/자기자본 | DART 대차대조표(신규) | 신규 필요 | 신규 필요 | 강 |
| 발생액(accruals) | (순이익−영업현금흐름)/자산 | DART(신규) | 신규 필요 | 신규 필요 | 강 |

**결론**:
- **티어 A**(ROE=EPS/BPS, 이익수익률=1/PER, 이익 안정성)는 **신규 데이터 출처·신규
  스키마 0**으로 기존 `fundamentals` 컬럼에서 파생 가능하다. 단 §2.3의 펀더멘털 이력
  backfill이 선행 조건이다.
- **티어 B**(총수익성 정본·부채·발생액)는 DART 재무제표 파싱·신규 스키마·전수
  backfill·filing-date PIT(DEF-3)를 요구한다 → **추가 연기**(§ EX, 059 범위 밖).

**최소 가용 퀄리티 셋 (운영자 지침 "2-3개 강건·가용 지표 선호")**:
1. **ROE = EPS / BPS** (PRIMARY). 이익/순자산 = 자기자본이익률. Novy-Marx 수익성
   팩터의 정신(수익성으로 우량주 식별)을 **이미 매일 fetch하는 per-share 데이터로**
   계산. 신규 출처·스키마 0.
2. **이익수익률 = 1/PER** (robustness 보조). 기존 per 컬럼.

→ 정본 총수익성/부채/발생액은 정직하게 **프록시 한계**를 기록하고 추가 연기.

### 2.3 [최대 리스크] 펀더멘털 이력 backfill 갭

검증된 사실:
- `kospi200-backfill`(`scripts/kospi200_backfill_run.py`)은 **OHLCV만** backfill한다
  (`backfill_all`은 `fetch_incremental`=OHLCV 경로). 펀더멘털은 backfill하지 않는다.
- 펀더멘털은 `scripts/refresh_market_data.py`(`:128-189,253`)가 **당일 forward로만**
  갱신한다(`_fetch_fundamentals_for_ticker`는 `start ~ today`).
- `historical_loader.py`는 **OHLCV 전용**이며 REQ-057-M1-5가 "소급 펀더멘털 주입
  금지"를 명시(`:7`). 즉 퀄리티 팩터는 **point-in-time 펀더멘털 로더가 부재**하다.

함의: 저변동성(가격 전용)은 10년 OHLCV backfill 위에서 바로 walk-forward가 가능했지만,
퀄리티는 **walk-forward 모든 윈도(상폐 종목 포함)에 걸친 EPS/BPS 이력**이 필요한데
그 이력이 DB에 없다. → 059는 (a) `fetch_fundamentals`를 재사용한 **펀더멘털 이력
backfill**(OHLCV backfill과 동형, 스키마 변경 0)과 (b) **point-in-time 펀더멘털
로더**(historical_loader의 펀더멘털 버전)를 선행 조건으로 둔다. 이것이 본 SPEC의
중심 데이터 의존성이며 fail-closed 커버리지 게이트로 강제한다.

### 2.4 [미검증 가정] 상폐 종목 펀더멘털 회수 + pykrx EPS PIT 충실도

057 M1-6은 상폐 종목 **OHLCV** 회수 가능을 실증했다(058 §2: `000030` 2018-01 22봉).
그러나 상폐 종목 **펀더멘털**(`get_market_fundamental_by_date`)이 회수되는지는
**미검증**이다. 만약 불가하면 퀄리티 팩터는 생존종목만 담아 생존편향이 재유입된다 →
057 M1-6과 동형의 **실증 게이트**가 059에도 필요하다.

또한 pykrx의 EPS/BPS가 as-of-date에 **그 시점까지 보고된 trailing 이익** 기준인지
(PIT-safe), 아니면 **소급 재작성(restated)** 값인지 **미검증**이다. restated면 약한
look-ahead. → 구현 시 실증 검증할 가정으로 명시(REQ-059-M4-PIT).

---

## 3. 결합 방법 설계 옵션 (tradeoff)

| 옵션 | 방법 | 장점 | 단점 | 채택 |
|------|------|------|------|------|
| **A. z-score 합성** | z(저변동 역랭킹) + z(ROE) → 합산 → top-N | 두 팩터 동등 가중, 결정적, 058 패턴과 정합 | z 표준화에 횡단면 분포 가정 | **채택(기본)** |
| B. 랭크 합성 | rank(저변동) + rank(ROE) → 최소합 top-N | 이상치 강건(분포 무가정) | 동점 처리 필요 | 보조/robustness |
| C. 순차 필터 | 저변동 분위 통과 → 그 안에서 ROE top | 해석 단순 | 한 팩터 정보 손실 | 제외 |
| D. ML 가중 | 학습된 결합 가중 | (이론상)최적 | **과적합**(058 EX-6) | **제외** |

**채택**: A(z-score 합성) 기본 + B(랭크 합성)를 robustness 변형으로 동시 측정.
선택된 종목 → **1/N 등가중**(058 ADR-058-2 상속, 최적화 금지). 가중은
SPEC-046 vol-targeting과 별개(058 EX-7과 동일하게 사이징은 058 범위 밖).

**리밸런스 cadence + 회전**: 058은 **월간 1/N**을 hard 제약으로 고정했다. 리서치는
순수 시간 기반보다 **tolerance-band/move-based**(드리프트 밴드)를 권고한다 →
059는 **월간 외부 루프를 유지**하되, 종목이 top-N 경계에서 buffer 이상 이탈할 때만
교체하는 **no-trade 허용 밴드**를 추가해 회전을 058의 50%/월 예산 아래로 낮춘다.
`measure_turnover` 재사용으로 측정·보고. 밴드는 시간 cadence를 대체하지 않는 **강화**다.

---

## 4. 측정 & 게이트 (057/058 하니스 전부 상속)

- 유니버스: `reconstruct_universe`(생존편향-free, 상폐 포함) + `achievable` fail-closed.
- 백테스트: `engine.run`(time-weighted, 기존 비용 상수). 새 비용 모델 금지.
- 알파: `adapt_to_scorecard`(time-weighted only, money-weighted 차단) → `scorecard.decide`.
- walk-forward: 리밸런스별 반복 point-in-time `engine.run`(단일 full-sample 금지).
- 다중검정: `apply_bonferroni(n_factors=K)` — K = 검정 변형 수(저변동 / 퀄리티 / 결합 =
  최소 2~3). **058이 예고한 자동 강화**(REQ-058-M3-2).
- 50% 할인: `apply_alpha_haircut`.
- 판정: `compose_verdict` 단일 AND(생존편향→n<30→Bonferroni→할인알파→scorecard GO).
- CLI: `factor-alpha`(신규, `entry-alpha` 패턴 본뜸).
- 승급: GO → **페이퍼 OOS 전용**(라이브 경로 미접촉).

---

## 5. 구조 결정: 신규 SPEC-059(058 의존) vs 058 in-place M4 — 권고 + 정당화

**권고: 058에 의존하는 신규 SPEC-TRADING-059를 만든다(M4+ 라인 확장으로 프레이밍).**

근거:
1. **058이 스스로 059를 이름으로 예약**했다(DEF-1/2/3, REQ-058-M3-2). 058 본문을
   M4로 재오픈하면 058의 HISTORY(적대적 감사 REVISE 0.58에서 "퀄리티는 SPEC-059로
   연기"가 **차단 결함의 해소책**이었음)와 정면 충돌한다. 058은 `completed`이며 그
   완결성(저변동 단독을 정직하게 출하)을 보존해야 한다.
2. **퀄리티는 새 리스크 표면**이다 — 058(가격 전용)에 없던 펀더멘털 이력
   backfill·PIT 펀더멘털 로더·상폐 펀더멘털 회수 실증·filing-date 위험을 도입한다.
   별도 게이트·별도 판정 사이클이 마땅하다.
3. **058 메커니즘이 이미 N≥2를 받도록 설계**됐다(`apply_bonferroni(n_factors=)`).
   059는 058 함수를 **호출만** 하므로 코드 결합은 최소, 문서 분리는 최대 — 깔끔하다.
4. 운영자의 "058 확장" 의도는 **전략 라인의 확장**을 뜻하며, 059를 "058 팩터
   전략의 M4+ 연속 마일스톤"으로 명명하면 의도와 형식이 모두 충족된다.

대안(058 M4 in-place)을 택하지 않는 이유: completed 문서 재오픈 + 감사 결정 번복 +
서로 다른 데이터 리스크를 한 문서에 혼합 → 추적성·정직성 저하.

---

## 6. 신규 vs 재사용 요약 (구현 가이드)

**신규(NEW)**:
1. 퀄리티 팩터 순수 함수 `compute_quality_signal(fundamentals_df, as_of_date)` →
   ROE(EPS/BPS) 랭킹, PIT, 결정적, 이력부족 명시 제외.
2. 결합 함수 `combine_factor_scores(lowvol_rankings, quality_rankings, method)` →
   z-score/랭크 합성 결합 랭킹.
3. point-in-time **펀더멘털 로더**(historical_loader의 펀더멘털 대응, REQ-057-M1-5
   준수) + 펀더멘털 이력 **backfill**(OHLCV backfill 동형, 스키마 변경 0).
4. 펀더멘털 커버리지 **fail-closed 게이트**(check_survivorship_gate 패턴) + 상폐
   펀더멘털 회수 실증.
5. 결합 walk-forward 오케스트레이터(058 `run_walk_forward_oos` 패턴, 결합 랭킹 버전).
6. CLI `factor-alpha`(`entry_alpha_run._cli_main` 패턴).

**재사용(REUSE, 호출만)**: §1.1/§1.2 전 함수 + `apply_bonferroni`/`apply_alpha_haircut`/
`compose_verdict`/`render_verdict_report`/`adapt_to_scorecard`/`check_survivorship_gate`/
`reconstruct_universe`/`engine.run`/`scorecard.decide`. **`benchmark.py` money-weighted
알파는 058과 동일하게 금지.**

**MX 타깃**(high fan_in / danger):
- `compute_quality_signal` → ANCHOR(look-ahead 불변식, combine+walkforward가 호출 fan_in≥2).
- `combine_factor_scores` → ANCHOR(결합이 059의 새 코어).
- 펀더멘털 커버리지 게이트 → WARN(fail-closed, check_survivorship_gate 동형).
- 결합 walk-forward 루프 → WARN(루프 내 engine.run 반복) + ANCHOR(진입점).
- 기존 ANCHOR(adapt_to_scorecard/compose_verdict/reconstruct_universe)는 **재사용,
  중복 태깅 금지**.

---

## 7. 검증되지 않은 가정 (구현 시 실증 필요, 단정 금지)

1. pykrx가 **상폐 종목 펀더멘털**(EPS/BPS)을 as-of-date로 반환하는가(057 M1-6
   OHLCV 동형 실증 필요). 불가 시 퀄리티 생존편향 재유입 → fail-closed bound-only.
2. pykrx EPS/BPS가 **trailing-reported PIT** 값인가, restated인가(restated면 약 look-ahead).
3. ROE=EPS/BPS가 한국에서 강건한 퀄리티 프록시인가(문헌은 정본 총수익성 기준; 프록시
   한계를 정직하게 기록).
4. 펀더멘털 이력 backfill의 KRX rate-limit/세션 비용(OHLCV backfill 대비 추가 호출량).

문헌 인용(058 §9 상속, 미검증 페이지는 단정 금지): Novy-Marx(2013) 총수익성,
McLean-Pontiff(2016) 50% 감쇠, DeMiguel-Garlappi-Uppal(2009) 1/N, Kim-Lee(2018)
한국 저위험, Asness(팩터 타이밍 함정), Gu-Kelly-Xiu(2020)/FINSABER(ML 과적합).
