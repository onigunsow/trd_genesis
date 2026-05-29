---
id: SPEC-TRADING-037
version: 0.1.0
status: draft
created: 2026-05-30
updated: 2026-05-30
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "매도(exit) 경로 복구 + 10년 KOSPI200 백테스트 기반 손절/익절 파라미터 도출"
related_specs:
  - SPEC-TRADING-033   # 자동 손절/익절 워치독. position_watchdog 의 direct-sell-bypass 패턴 재사용(REQ-037-5)
  - SPEC-TRADING-029   # balance()/cash_pct·holdings — 포지션/체결 reconcile 컨텍스트(엣지 측정의 입력)
  - SPEC-TRADING-016   # 자본 보전 우선 정책. event-driven 출구 룰 계보
  - SPEC-TRADING-036   # 변동성 레짐 vs macro regime 분리 원칙(ATR 출구 룰은 변동성 레짐 유지 — 비목표 근거)
  - SPEC-TRADING-019   # KOSPI200 유니버스 소스(get_index_portfolio_deposit_file '1028')
  - SPEC-TRADING-002   # 실거래 분리 — live 잠금 유지 근거
---

# SPEC-TRADING-037 — 매도(exit) 경로 복구 + 10년 KOSPI200 백테스트 기반 손절/익절 파라미터 도출

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-30 | 0.1.0 | Initial draft. 엣지 검증 분석(95%+ 신뢰)이 26일 paper trading 에서 **BUY 21건 / SELL 0건 → 청산 라운드트립 0건 → 수익성 측정 불가** 를 발견. 근본 원인은 **설계 문제**(버그 아님): (1) 출구 임계 과대(2.0×ATR%, 한국 변동성에서 손절 −7~−15%/익절 +10~+22% → 보유가 도달 못 함), (2) Decision 페르소나 HOLD 편향(sell 348:129:2), (3) halt 게이트가 위험 축소 매도까지 차단, (4) ATR 불가 시 `effective_stop=None` → 영구 skip 잠복 버그. **사용자(리스크 오너) 결정**: 출구 경로를 고치되 **무턱대고 임계를 튜닝하지 말고 10년 KOSPI200 백테스트로 견고한 파라미터를 먼저 도출**. **결정적 scope 한계 명시**: 백테스트는 **결정적 출구 룰만** 검증하며 **LLM 진입 엣지는 검증 불가**(look-ahead bias) — 진입 엣지는 forward paper 로만 확인. **paper only, live 잠금 유지, money 로직이므로 reproduction-first 필수**. 사용자 정책 결정 반영 — 2026-05-30 | onigunsow |

---

## Scope Summary

본 SPEC 은 **거래 사이클이 라운드트립을 완성하지 못하는 문제**(BUY 만 21건, SELL 0건)를 해결한다.
시스템이 청산을 할 수 없으니 수익성을 측정할 수 없고, 따라서 전략 개선이 불가능한 상태다.

세 단계로 진행한다:

- **Phase A — 검증 인프라**: 10년치 KOSPI200 OHLCV 를 적재하고, **기존 백테스트 엔진**을
  출구 룰(exit-rule) 파라미터 스윕으로 확장한다. 유니버스 전체에 대해 진입을 시뮬레이션하고
  후보 출구 룰(ATR 멀티플라이어, 하드 스톱 플로어, 익절 설정)을 적용해 파라미터셋별
  **승률 / 기대값(expectancy) / MDD / 평균 보유기간**을 산출하고, 데이터로 정당화된
  **견고한 파라미터셋**을 권고한다.
- **Phase B — 출구 경로 수정 (백테스트 정당화 값 적용)**: 하드 스톱 플로어, 잠복 None-버그,
  count-halt 우회(위험 축소 매도), 프롬프트 sell-rule 정렬.
- **Phase C — 검증**: reproduction-first 테스트 + 배포(paper) 후 **최소 1건의 실제 청산
  라운드트립** 확인, 그 뒤 `trading edge-report` 로 실현 성과 측정 재개.

### ⚠️ 결정적 Scope 한계 (반드시 읽을 것)

> **백테스트는 결정적(deterministic) 출구 룰만 검증한다 — LLM 진입(entry) 엣지는 검증하지 못하며,
> 할 수도 없다.**
>
> - 손절/익절 룰은 **결정적**(가격이 임계에 닿으면 청산)이므로 과거 데이터로 견고성을 검증할 수 있다.
> - 그러나 "과거 그 시점에 LLM 이 무엇을 샀을지" 를 재현하는 것은 **look-ahead bias** 를 유발한다
>   (백테스트 시점의 LLM 은 미래를 본다). 따라서 백테스트로 **진입 엣지의 수익성을 증명할 수 없다**.
> - **백테스트의 산출물 = "출구 룰의 견고한 파라미터셋"**, **아님 = "전략이 돈을 번다는 증거"**.
> - 진입 엣지는 **오직 forward paper 검증**(라운드트립이 쌓인 뒤 `edge-report`)으로만 확인된다.
> - 본 SPEC 의 어떤 산출물도 "백테스트가 전체 수익성을 증명했다" 고 함의해서는 안 된다.

### 비즈니스 목표 vs 자본 보전

사용자(가족 부양 책임 개인 투자자)는 "자본 보전 우선" 을 반복 강조했다. 출구 경로 복구는
**손실을 끊고(손절) 이익을 실현(익절)하는** 자본 보전 메커니즘이므로 이 원칙과 정렬된다.
모든 변경은 **paper 한정**이며 live 는 잠금 유지된다(C-7).

---

## Goals

- **G-1 (Phase A 데이터)**: ~10년치 KOSPI200 구성종목 OHLCV(+ KOSPI 지수 `1001`)가 `ohlcv` 테이블에
  적재되고, rate limit/휴장일/staleness 가 graceful 하게 처리된다.
- **G-2 (Phase A 하니스)**: 백테스트 엔진이 출구 룰 파라미터 스윕을 수행하고, 파라미터셋별
  승률/기대값/MDD/평균 보유를 산출하며, **데이터로 정당화된 권고 파라미터셋**을 출력한다.
- **G-3 (Phase B 플로어)**: 워치독이 `effective_stop = max(atr_stop, FLOOR)` 로 **하드 스톱 플로어**
  (Phase A 도출값, 기본 후보 −7%)를 강제한다.
- **G-4 (Phase B 버그)**: ATR 불가 시 `effective_stop`/`effective_take` 가 **수치값**으로 채워져
  (fixed_fallback 경로) 자동 매도가 동작한다 — None-skip 영구 미매도 제거.
- **G-5 (Phase B halt)**: 위험 축소 SELL 시그널이 **일일 주문수(count) halt 를 우회**한다(일일 손실
  halt 는 우회하지 않음). 어떤 halt 종류를 우회하는지 명시적·보수적으로 한정한다.
- **G-6 (Phase B 정렬)**: `decision.jinja` sell 룰(정적 −7%)이 동적 `effective_stop` 과 정렬되어
  페르소나 sell 과 워치독 exit 가 일치한다.
- **G-7 (Phase C)**: reproduction-first 테스트가 4개 시나리오를 증명하고, 배포(paper) 후 **최소 1건의
  실제 청산 라운드트립**이 관측되며 `edge-report` 가 실현 성과를 측정한다.
- **G-8 (안전)**: paper only, live 잠금 불변. money 로직 변경은 reproduction-first. 베이스라인
  950 passed 대비 신규 회귀 0, 신규 코드 85%+ 커버리지.

---

## Requirements (EARS)

### REQ-037-1: 10년 KOSPI200 OHLCV 적재 (Ubiquitous + Event-Driven) — Phase A

시스템은 ~10년치 KOSPI200 구성종목 OHLCV(+ KOSPI 지수)를 기존 `ohlcv` 캐시에 적재해야 한다.
이는 백테스트(REQ-037-2)의 입력 데이터다.

- **(a) Ubiquitous — 유니버스** — 시스템은 KOSPI200 구성종목 전체
  (`pykrx.stock.get_index_portfolio_deposit_file('1028')`)와 KOSPI 지수(코드 `1001`)의 일봉을
  대상으로 한다(`universe.py` 의 top-50 제한을 본 적재에서는 해제).
- **(b) Ubiquitous — 적재 경로** — 적재는 **기존** `data/pykrx_adapter.fetch_ohlcv` +
  `data/cache.upsert_ohlcv`(`ohlcv` 테이블)를 재사용한다. 신규 ohlcv 테이블/스키마를 만들지 않는다.
- **(c) Event-Driven — rate limit** — **When** pykrx 호출이 rate limit/타임아웃/예외를 받으면,
  **then** 시스템은 backoff 재시도하고, 최종 실패 시 해당 종목을 **건너뛰고**(전체 적재 abort 금지)
  스킵 목록에 기록한다(`except Exception:` graceful).
- **(d) State-Driven — staleness/휴장일** — **While** 종목 캐시가 이미 일부 구간을 보유하면,
  시스템은 `cached_ohlcv` 의 MIN/MAX ts 를 확인해 **증분 적재**(`fetch_incremental` 패턴)만 수행한다.
  휴장일·상장폐지·신규상장으로 인한 누락 구간은 정상으로 처리한다(데이터 공백 허용).
- **(e) Ubiquitous — 진행 보고** — 적재 종료 시 적재 종목 수 / 스킵 종목 수 / 커버 기간을 로깅한다.

#### Acceptance Criteria — REQ-037-1

- [ ] 적재 실행 후 `ohlcv` 테이블에 KOSPI 지수(`1001`) 및 KOSPI200 구성종목의 ~10년 일봉이 존재한다
      (`SELECT MIN(ts), MAX(ts), COUNT(DISTINCT symbol) FROM ohlcv` sanity — 기간 ≥ 약 9년, 종목 ≥ 150).
- [ ] rate limit/예외 mock 주입 시 해당 종목만 스킵되고 적재가 **성공 종료**(exit 0)한다(음성 테스트 —
      abort/crash 없음).
- [ ] 이미 적재된 종목 재실행 시 **증분 적재**만 수행한다(중복 INSERT 없음 — `upsert_ohlcv` 멱등 확인).
- [ ] 적재 종료 로그에 적재/스킵 종목 수와 커버 기간이 기록된다.

**Dependencies**: 없음. (REQ-037-2 의 입력.)

---

### REQ-037-2: 출구 룰 파라미터 스윕 백테스트 하니스 (Ubiquitous + State-Driven) — Phase A

시스템은 **기존 백테스트 엔진**(`backtest/engine.py`)을 확장해, 10년 유니버스에서 진입을
시뮬레이션하고 후보 출구 룰을 적용하는 **파라미터 스윕**을 수행해야 한다. 산출물은 데이터로
정당화된 권고 출구 파라미터셋이다.

- **(a) Ubiquitous — 결정적 출구 룰 시뮬레이션** — 시스템은 각 후보 진입에 대해 결정적 출구 룰
  (손절: ATR 멀티플라이어 + **하드 스톱 플로어**, 익절: take-profit 설정, 선택적 트레일링)을
  적용해 청산하고, 라운드트립을 생성한다. 비용은 엔진 상수(수수료/거래세/슬리피지)를 적용한다.
- **(b) State-Driven — 파라미터 스윕** — **While** 후보 파라미터셋(예: STOP_ATR_MULTIPLIER ∈
  {1.0, 1.5, 2.0}, FLOOR ∈ {−5%, −7%, −10%}, TAKE_ATR_MULTIPLIER ∈ {1.5, 2.0, 3.0})을 순회하는 동안,
  각 파라미터셋에 대해 **승률 / 기대값(expectancy) / MDD / 평균 보유기간 / 거래수**를 계산한다.
- **(c) Ubiquitous — 진입 모델 = 메커니컬 (look-ahead-free)** — 진입은 **결정적·메커니컬 모델**
  (예: 유니버스 균등/랜덤 시드 고정 진입, 또는 단순 모멘텀 필터)을 쓴다. **LLM 진입을 재현하지
  않는다**(look-ahead 금지). 진입 모델은 출구 룰 비교를 위한 **공통 통제 변인**일 뿐이다(C-1).
- **(d) Ubiquitous — 권고 출력** — 시스템은 스윕 결과를 정렬해 **견고한 권고 파라미터셋**과 그
  근거 지표를 출력한다(과최적화 회피를 위해 단일 최고점이 아니라 인접 파라미터에서도 안정적인
  영역을 선호한다 — robustness 우선).
- **(e) Ubiquitous — 영속화** — 스윕 결과는 `benchmark_runs`(strategy 라벨 구분) 또는 신규
  테이블(필요 시 S-1)에 영속화하고, run_id 를 출력한다.
- **(f) 비목표(명시 defer)** — 본 하니스는 **출구 룰만** 평가한다. LLM 진입 엣지·전체 전략 수익성은
  평가하지 않는다(Scope 한계 + C-1).

#### Acceptance Criteria — REQ-037-2

- [ ] 하니스가 10년 데이터(또는 테스트용 합성/축소 데이터)에서 파라미터셋별 승률/기대값/MDD/평균
      보유/거래수를 산출한다(metrics dict 비어있지 않음).
- [ ] 알려진 합성 데이터(예: 단조 상승 시리즈)에서 metrics 가 **상식적**이다(상승장에서 익절 위주 청산,
      MDD 작음 — sanity 테스트).
- [ ] 파라미터 스윕이 ≥ 9개 조합(3×3 등)을 평가하고 각 조합의 지표를 반환한다.
- [ ] **권고 파라미터셋**이 출력되고 근거 지표가 동반된다(단일 최고점이 아닌 robust 영역 선호 로직 확인).
- [ ] 스윕 결과가 영속화되고 run_id 가 출력된다.
- [ ] 하니스는 **결정적 출구 룰만** 평가함을 출력/주석에 명시한다(LLM 진입 미검증 — Scope 한계 반영).

**Dependencies**: REQ-037-1(OHLCV 데이터), `backtest/engine.py`(metrics 재사용).

---

### REQ-037-3: 하드 스톱 플로어 (State-Driven) — Phase B

워치독의 유효 손절은 ATR 기반 손절과 하드 플로어 중 **더 보수적인 쪽(덜 깊은 손절)**을 취해야 한다.

- **(a) State-Driven** — **While** 동적 임계가 계산되는 동안, 시스템은
  `effective_stop = max(atr_stop, FLOOR)` 를 적용한다(FLOOR 는 음수 %, 예 −7%. `max` 이므로
  −15% 의 깊은 atr_stop 은 −7% 로 끌어올려져 **더 빨리 손절**된다).
- **(b) Ubiquitous — FLOOR 값 출처** — FLOOR 의 기본 후보는 **−7%** 이며, **Phase A(REQ-037-2)에서
  도출된 데이터 정당화 값**으로 확정한다. 환경변수(예 `STOP_FLOOR_PCT`)로 구성 가능하게 한다
  (기존 `STOP_ATR_MULTIPLIER` 패턴).
- **(c)** 변경 파일: `strategy/volatility/thresholds.py`(`effective_stop` 계산), 필요 시
  `watchers/position_watchdog.py`. 익절 플로어/캡은 기존 `MAX_TAKE_PROFIT_PCT` 가드를 유지한다.

#### Acceptance Criteria — REQ-037-3

- [ ] atr_stop=−15% (extreme 변동성) + FLOOR=−7% 시 `effective_stop == −7%`(plateau 확인).
- [ ] atr_stop=−4% (low 변동성) + FLOOR=−7% 시 `effective_stop == −4%`(얕은 손절은 그대로 — max 동작).
- [ ] FLOOR 환경변수 override 가 동작한다(parametrize 테스트).
- [ ] 기존 `MAX_STOP_LOSS_PCT` 가드레일과 충돌하지 않는다(가드레일 적용 순서 회귀 테스트).

**Dependencies**: REQ-037-2(FLOOR 값). `strategy/volatility/thresholds.py`.

---

### REQ-037-4: 잠복 None-임계 버그 수정 (Unwanted) — Phase B

ATR/ohlcv 불가로 fallback 이 발생해도, 유효 손절/익절은 **수치값**으로 채워져 자동 매도가
동작해야 한다.

- **(a) Unwanted** — 시스템은 ATR 불가 시 `effective_stop`/`effective_take` 를 **None 으로 남겨
  두어서는 안 된다**. `DynamicThresholds` 의 fallback 경로(`source="fixed_fallback"`)는
  `fixed_fallback_stop`(−7.0)/`fixed_fallback_take` 를 `effective_stop`/`effective_take` 로
  **연결(populate)** 해야 한다.
- **(b) Ubiquitous** — `position_watchdog.classify_holding` 의 `if eff_stop is None ...: skip`
  방어는 유지하되, fallback 경로가 None 을 만들지 않으므로 ATR 불가 종목도 **수치 손절로 분류**된다.
- **(c)** 변경 파일: `strategy/volatility/models.py`(또는 `thresholds.py` fallback 분기 —
  fixed_fallback_take 가 `"RSI>85"` 문자열이므로 `effective_take` 수치화 정책을 명확히 한다.
  권고: 워치독은 익절을 RSI 기반으로 별도 평가하거나, fallback take 를 보수적 수치(예 엔진 합의값)로
  매핑. **run 에서 확정** — Q-2).

#### Acceptance Criteria — REQ-037-4

- [ ] `compute_atr` 가 None 을 반환하도록 mock 했을 때 `get_dynamic_thresholds(...)["effective_stop"]`
      가 **수치값**(예 −7.0)이다(None 아님) — reproduction-first 음성 테스트.
- [ ] ATR 불가 + 포지션 pnl −8% 일 때 `classify_holding` 이 `("stop", qty)` 를 반환한다(영구 skip 제거).
- [ ] ATR 불가 + 포지션 pnl −3% 일 때 `("skip", 0)`(아직 손절선 미도달 — 정상).
- [ ] `effective_take` 의 fallback 정책(수치화 또는 RSI 위임)이 테스트로 명시된다.

**Dependencies**: `strategy/volatility/models.py`, `watchers/position_watchdog.py`.

---

### REQ-037-5: 위험 축소 SELL 의 count-halt 우회 (Event-Driven + Unwanted) — Phase B

위험 축소 SELL 시그널은 **일일 주문수(count) 회로차단**이 트립한 사이클에서도 실행될 수 있어야 한다.
단, **일일 손실(loss) halt 는 우회하지 않는다**.

- **(a) Event-Driven** — **When** orchestrator 의 halt 게이트(`if state["halt_state"]: return res`,
  pre_market ~892~903 / intraday ~1369~1380)가 사이클을 스킵하려 할 때, **then** 시스템은 그 사이클의
  SELL 시그널(위험 축소 exit)은 **계속 처리**하고 BUY/신규 진입만 차단한다.
- **(b) Unwanted — halt 종류 명시 한정** — 시스템은 **일일 주문수(count) 트립으로 인한 halt 만**
  SELL 에 대해 우회한다. **일일 손실(daily-loss) halt·수동 halt·기타 위험 halt 는 SELL 도 차단**한다
  (위험 축소조차 막아야 하는 상태). 우회 대상 halt 종류를 코드에서 명시적으로 판별한다(불명확 시
  보수적으로 차단 — fail-safe).
- **(c) Ubiquitous — 패턴 일관성** — 이 우회는 SPEC-033 `position_watchdog` 의 direct-sell-bypass
  (워치독이 이미 halt/일일주문수 게이트를 우회해 직접 매도)와 **방향 일관**되게, **페르소나 sell
  경로**로 확장하는 것이다. 이중 매도 가드를 둔다.
- **(d) Ubiquitous — 로깅/알림** — count-halt 우회 SELL 실행은 audit_log + Telegram 에 기록한다
  (예: `"COUNT-HALT BYPASS SELL: {ticker} {qty}주 (위험 축소)"`).

#### Acceptance Criteria — REQ-037-5

- [ ] count-halt(`halt_state=true`, reason=일일주문수) 사이클에 위험 축소 SELL 시그널이 있을 때
      SELL 이 **실행**되고 BUY 는 차단된다(reproduction-first 시나리오 테스트).
- [ ] daily-loss halt 사이클에서는 SELL 도 **차단**된다(음성 테스트 — fail-safe 보증).
- [ ] 우회 대상이 아닌 halt(수동/불명) 에서는 SELL 차단(보수적 fail-safe).
- [ ] 이중 매도 가드: 같은 종목이 워치독과 페르소나 양쪽에서 동시 매도 시그널 시 중복 주문 미발생.
- [ ] 우회 SELL 실행 시 audit_log + Telegram 알림 송출(mock 검증).

**Dependencies**: SPEC-033(direct-sell-bypass 패턴), `personas/orchestrator.py` halt 게이트.

---

### REQ-037-6: decision.jinja sell 룰 정렬 (Ubiquitous) — Phase B

페르소나 sell 룰과 워치독 exit 가 일치하도록, `decision.jinja` 의 정적 −7% 손절 룰을 동적
`effective_stop` 에 정렬한다.

- **(a) Ubiquitous** — `decision.jinja` 라인 ~14 의 "평가손실 −7% 도달 시 매도" 정적 플랫 룰을,
  종목별 `get_dynamic_thresholds` 의 `effective_stop`(REQ-037-3 플로어 적용 후) 기준으로 표현하도록
  수정한다(라인 170/174 의 동적 임계 안내와 일관). fallback(`source="fixed_fallback"`) 시에만 −7%/
  RSI>85 고정 룰을 사용한다는 점을 명확히 한다.
- **(b) Unwanted — 비목표 경계** — 본 변경은 **sell-rule 프롬프트 정렬에 한정**한다. entry/LLM 판단
  로직(매수 시그널 생성·confidence·종목 선정)은 **변경하지 않는다**(C-2).
- **(c)** 변경 파일: `personas/prompts/decision.jinja`(필요 시 `risk.jinja` 일관성 확인).

#### Acceptance Criteria — REQ-037-6

- [ ] `decision.jinja` 에 정적 단일 "−7% 도달 시 매도" 가 **동적 effective_stop 기준**으로 표현된다
      (grep — 정적 −7% 가 fallback 컨텍스트로만 남고 1차 룰은 effective_stop 참조).
- [ ] fallback 시 −7%/RSI>85 고정 룰 사용 안내가 유지된다(라인 174 일관).
- [ ] entry/매수 시그널 관련 프롬프트 라인은 **diff 에서 변경 없음**(비목표 경계 — 리뷰 확인).

**Dependencies**: REQ-037-3(effective_stop 플로어). `personas/prompts/decision.jinja`.

---

### REQ-037-7: 검증 — reproduction-first + 배포 후 라운드트립 (Ubiquitous) — Phase C

money/risk 로직 변경이므로, 모든 수정은 **재현 테스트 우선**으로 증명되고, 배포(paper) 후 실제
청산이 관측되어야 한다.

- **(a) Ubiquitous — reproduction-first (HARD)** — 각 Phase B 수정은 **수정 전에 실패하는 재현
  테스트**를 먼저 작성하고(수정 후 통과), 다음을 증명한다:
  - 새 스톱 플로어 이하 포지션이 SELL 을 트리거한다(REQ-037-3).
  - ATR 불가 포지션이 수치 손절을 받는다(None-skip 없음 — REQ-037-4).
  - count-halt 사이클에서도 위험 축소 SELL 이 실행된다(REQ-037-5).
  - 백테스트 하니스가 알려진 데이터에서 상식적 metrics 를 낸다(REQ-037-2).
- **(b) Event-Driven — 배포 후 검증** — **When** paper 배포 후 첫 청산이 발생하면, **then**
  `closed round-trip` 이 ≥ 1건 관측되고 `trading edge-report` 가 **실현 성과**(승률/expectancy)를
  측정 시작한다.
- **(c) Ubiquitous — Scope 한계 재명시** — 검증 리포트/커밋 메시지는 백테스트가 출구 룰만 검증했고
  진입 엣지는 forward paper 로만 확인됨을 명시한다(과대 주장 금지 — C-1, no-lies).

#### Acceptance Criteria — REQ-037-7

- [ ] REQ-037-3/4/5/2 각각에 대해 **수정 전 RED, 수정 후 GREEN** 인 재현 테스트가 존재한다.
- [ ] 베이스라인 950 passed 대비 신규 회귀 0, 신규 코드 85%+ 커버리지.
- [ ] (배포 후) paper 에서 최소 1건의 청산 라운드트립이 `edge-report` 에 반영된다(go/no-go 측정 재개).
- [ ] 검증 산출물에 "백테스트=출구 룰만, 진입 엣지=forward paper" 한계가 명시된다.

**Dependencies**: REQ-037-2~6 전체. `edge/*`, `cli.py edge-report`.

---

## Specifications

### S-1: (선택) 마이그레이션 027 — 파라미터 스윕 결과 영속화

> 권고: 기존 `benchmark_runs` 로 충분하면 신규 테이블 불필요(REQ-037-2 e). 파라미터셋별 상세 지표
> (승률/expectancy/평균보유)를 구조적으로 저장하려면 신규 테이블을 둔다. raw SQL, 순차(`027_`),
> 멱등(IF NOT EXISTS), `migrate.py` 자동 발견. 재배포 후 `docker exec trading-app trading migrate`
> 수동 적용(자동 boot 미적용 — 하우스 스타일).

파일명 예: `src/trading/db/migrations/027_exit_rule_sweep.sql`

```sql
-- SPEC-TRADING-037 REQ-037-2: 출구 룰 파라미터 스윕 결과(선택적 상세 저장).
-- 멱등: CREATE TABLE IF NOT EXISTS + schema_migrations ON CONFLICT (026 하우스 스타일).
-- 주의: 본 스윕은 결정적 출구 룰만 평가한다(LLM 진입 엣지 미검증).

CREATE TABLE IF NOT EXISTS exit_rule_sweep (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT,                 -- benchmark_runs 참조(있으면)
    stop_atr_mult   NUMERIC,
    stop_floor_pct  NUMERIC,                -- 음수 % (예 -7.0)
    take_atr_mult   NUMERIC,
    win_rate        NUMERIC,                -- 0~1
    expectancy      NUMERIC,                -- 평균 라운드트립 수익(비용 차감 후)
    mdd             NUMERIC,
    avg_hold_days   NUMERIC,
    trades          INTEGER,
    is_recommended  BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS exit_rule_sweep_recommended_idx
    ON exit_rule_sweep (is_recommended, created_at DESC);

COMMENT ON TABLE exit_rule_sweep IS
    'SPEC-TRADING-037 REQ-037-2: 결정적 출구 룰 파라미터 스윕 결과. 진입 엣지 미검증.';

INSERT INTO schema_migrations (version) VALUES ('027_exit_rule_sweep')
    ON CONFLICT DO NOTHING;

INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"027_exit_rule_sweep"}'::JSONB);
```

> 컬럼/테이블명은 권고안이며 일관성 내에서 조정 가능. benchmark_runs 만으로 충분하면 본 마이그레이션 생략.

### S-2: 하드 스톱 플로어 적용 순서 (REQ-037-3)

```
stop_loss_pct  = -STOP_ATR_MULTIPLIER * atr_pct        # 예 -15% (extreme)
atr_stop       = max(stop_loss_pct, -MAX_STOP_LOSS_PCT) # 기존 가드레일 (-15% 캡)
effective_stop = max(atr_stop, STOP_FLOOR_PCT)          # 신규 플로어 (-7%) → -7%
```

- `max` 이므로 **더 얕은(덜 깊은) 손절**이 채택된다 → 한국 변동성에서 손절이 **더 빨리** 발동.
- low 변동성(atr_stop=−4%)이면 `max(-4%, -7%) = -4%` → 얕은 손절 유지(과민 손절 방지).

### S-3: count-halt 우회 판별 (REQ-037-5)

```
SELL 실행 허용 조건 (위험 축소 exit):
    halt_state == true
    AND halt_reason == 'daily_order_count'   # 일일 주문수 트립만
    AND signal.side == 'sell'
    AND signal.is_risk_reducing == true       # 보유 포지션 축소

SELL 차단 (fail-safe):
    halt_reason ∈ {'daily_loss', 'manual', <unknown>}  # 위험 축소도 차단
```

> halt 종류 판별이 불명확하면 **보수적으로 차단**(fail-safe). 워치독 direct-sell-bypass(SPEC-033)와
> 방향 일관, 이중 매도 가드 필수.

### S-4: 백테스트 진입 모델 — look-ahead-free 통제 변인 (REQ-037-2)

```
진입 = 결정적·메커니컬 (예: 시드 고정 균등 진입 또는 단순 모멘텀 필터)
        ↑ LLM 진입 재현 금지 (look-ahead bias)
출구 = 파라미터 스윕 대상 (ATR 멀티 / 플로어 / 익절)
        ↑ 이것만이 백테스트가 검증하는 결정적 룰

산출 = "견고한 출구 파라미터셋"  (≠ "전략 수익성 증거")
```

---

## Constraints (구현 제약 — 반드시 준수)

- **C-1 (결정적 scope 한계 — 최우선)**: 백테스트(REQ-037-2)는 **결정적 출구 룰만** 검증한다.
  **LLM 진입 엣지는 검증 불가**(look-ahead bias). 어떤 산출물/커밋/리포트도 "백테스트가 전체 수익성을
  증명했다" 고 함의해서는 안 된다. 진입 엣지는 forward paper(`edge-report`)로만 확인(no-lies 메모리 준수).
- **C-2 (entry 로직 불변)**: entry/LLM 판단 로직(매수 시그널·confidence·종목 선정)은 **변경 금지**.
  본 SPEC 의 프롬프트 변경은 **sell-rule 정렬에 한정**(REQ-037-6).
- **C-3 (paper only / live 잠금)**: 모든 변경은 paper 환경에서만 활성. live 거래는 **잠금 유지**
  (SPEC-002). 출구 룰 활성화의 live 전환은 별도 사용자 승인 + 별도 SPEC(C-7).
- **C-4 (reproduction-first — HARD)**: money/risk 로직이므로 각 Phase B 수정은 **실패하는 재현 테스트
  우선**(CLAUDE.md HARD Rule 4). 수정 전 RED, 수정 후 GREEN 입증(REQ-037-7 a).
- **C-5 (재사용)**: `backtest/engine.py`(metrics), `data/pykrx_adapter.py`+`data/cache.py`(ohlcv),
  `strategy/volatility/*`(ATR), `watchers/position_watchdog.py`, `edge/*`+`cli.py edge-report`(측정)를
  재사용한다. 중복 구현 금지.
- **C-6 (테스트)**: `.venv/bin/python -m pytest`(docker 이미지에 pytest 없음). 베이스라인 **950 passed**.
  신규 회귀 0, 신규 코드 85%+(TRUST 5).
- **C-7 (실거래 분리)**: 출구 파라미터·count-halt 우회의 live 적용은 **별도 사용자 승인 + 별도 SPEC**.
- **C-8 (lint)**: ruff 가 BLE001 select 안 함 → `# noqa: BLE001` 금지(RUF100 유발). 평범한
  `except Exception:` 사용(graceful 적재/스윕 포함). Python 룰 준수(타입힌트, bare except 금지,
  print 아닌 logging).
- **C-9 (마이그레이션)**: 신규 마이그레이션 필요 시 raw SQL `027_*.sql`, 순차, 멱등(IF NOT EXISTS),
  `migrate.py` 자동 발견. 재배포 후 `docker exec trading-app trading migrate` 수동 실행.
- **C-10 (브랜치)**: 작업 브랜치는 이미 `fix/SPEC-TRADING-026-overheating-softening`(HEAD 7144194).
  신규 브랜치 생성 금지, 커밋/배포는 오케스트레이터가 처리.

---

## Deferred / Non-Goals (명시적 비목표)

- **LLM 진입 엣지 검증 / 전체 전략 수익성 증명**: 백테스트로 불가능(look-ahead). forward paper 로만 확인.
  본 SPEC 은 **출구 룰의 견고한 파라미터셋 도출 + 출구 경로 복구**까지.
- **entry/매수 시그널 로직 변경**: Decision 페르소나의 HOLD 편향 자체(348:129:2)는 **건드리지 않는다**.
  sell-rule 프롬프트 정렬만(REQ-037-6). HOLD 편향 개선은 별도 SPEC 후보.
- **live(실거래) 출구 활성화**: paper 검증까지(C-3/C-7). 별도 SPEC.
- **ATR 출구 룰의 macro regime 연결**: ATR 손절/익절은 **변동성 레짐** 유지(SPEC-033/036 원칙 — macro
  regime 과 분리). 본 SPEC 은 변동성 기반 출구의 파라미터만 보정.
- **daily-loss halt·수동 halt·회로차단 로직 변경 없음** — 위험 축소 SELL 우회는 **count-halt 한정**
  (REQ-037-5 b). 손실 halt 는 최종 hard gate 로 불변.
- **신규 ohlcv 스키마/테이블**: 기존 `ohlcv` 캐시 재사용(REQ-037-1 b).

---

## Risks

| ID | 리스크 | 영향 | 가능성 | 완화 |
|---|---|---|---|---|
| R-1 | 백테스트가 전체 수익성을 증명한다는 **과대 해석** | Critical | Medium | C-1 결정적 scope 한계를 SPEC·하니스 출력·커밋에 반복 명시. 진입은 메커니컬 통제 변인(S-4). no-lies 메모리 준수 |
| R-2 | 파라미터 스윕 **과최적화**(overfitting) — 단일 최고점 채택 | High | Medium | robustness 우선(인접 파라미터 안정 영역 선호 — REQ-037-2 d). 10년 + 유니버스 전체로 표본 확대. 비용(수수료/세금/슬리피지) 반영 |
| R-3 | 하드 스톱 플로어가 **과민 손절**(휩쏘) 유발 | Medium | Medium | low 변동성은 얕은 손절 유지(max 동작 — S-2). FLOOR 는 백테스트 도출값. paper 검증 후 보정(C-6) |
| R-4 | count-halt 우회가 **daily-loss halt 까지 우회** → 손실 상태에서 추가 행동 | Critical | Low | halt 종류 명시 판별(S-3), 불명 시 보수적 차단(fail-safe). daily-loss 음성 테스트(AC REQ-037-5) |
| R-5 | None-버그 수정이 **기존 의도적 skip 방어**를 깨뜨림 | Medium | Low | fallback 이 수치값을 채우므로 None-skip 방어는 유지하되 도달 안 됨(REQ-037-4 b). 회귀 테스트 |
| R-6 | 10년 적재가 **pykrx rate limit** 으로 장시간/실패 | Medium | Medium | backoff + 종목 스킵 graceful(REQ-037-1 c), 증분 적재(d). 1회성 적재라 사이클 영향 없음 |
| R-7 | entry 로직을 **의도치 않게 변경**(프롬프트 정렬 중) | High | Low | C-2 비목표 경계, diff 리뷰(AC REQ-037-6 c) — entry 라인 무변경 확인 |
| R-8 | 이중 매도(워치독 + 페르소나 동시) | High | Medium | 이중 매도 가드(REQ-037-5 c), AC 검증 |

---

## Open Questions

- **Q-1 (run 시 확정 — FLOOR 값)**: 하드 스톱 플로어 기본 후보는 −7% 이나, **Phase A 백테스트 도출값**
  으로 확정한다. 스윕 결과가 −7% 와 크게 다르면(예 −5% 가 robust) 사용자 승인 하에 채택.
- **Q-2 (run 시 확정 — effective_take fallback 수치화)**: `fixed_fallback_take` 가 `"RSI>85"` 문자열
  이라 `effective_take` 수치 매핑이 모호하다. 옵션: (a) 워치독이 fallback 시 익절을 RSI 기반 별도 평가,
  (b) fallback take 를 보수적 수치(엔진 합의값)로 매핑. run 에서 확정(REQ-037-4 c).
- **Q-3 (백테스트 진입 모델)**: 메커니컬 진입을 (a) 시드 고정 균등 진입, (b) 단순 모멘텀 필터(예 5일
  수익률 상위), (c) 둘 다 비교 중 무엇으로? — 출구 룰 비교의 통제 변인이므로 **단순·결정적**이면 충분.
  run 에서 결정(S-4).
- **Q-4 (스윕 격자)**: STOP_ATR_MULTIPLIER / FLOOR / TAKE_ATR_MULTIPLIER 의 격자 범위·간격은 초기값
  (1.0/1.5/2.0, −5/−7/−10, 1.5/2.0/3.0)이며, 1차 결과 후 관심 영역을 좁혀 재스윕할 수 있다.
- **Q-5 (마이그레이션 필요성)**: 파라미터 스윕 상세 영속화에 신규 테이블(S-1)이 필요한지, 기존
  `benchmark_runs` 로 충분한지 — run 에서 확정.

---

## Traceability

| 요구 | Phase | 영향 파일 | 테스트(신규) |
|---|---|---|---|
| REQ-037-1 | A | `data/pykrx_adapter.py`(재사용), `data/cache.py`(재사용), `data/universe.py`(KOSPI200 소스 — top-N 해제), 신규 적재 스크립트/CLI | `tests/data/test_kospi200_backfill.py` |
| REQ-037-2 | A | `backtest/engine.py`(metrics 재사용), 신규 `backtest/exit_sweep.py`, 신규 CLI(예 `trading backtest-exit-sweep`), `db/migrations/027_*.sql`(선택) | `tests/backtest/test_exit_sweep.py` |
| REQ-037-3 | B | `strategy/volatility/thresholds.py`(effective_stop 플로어), `watchers/position_watchdog.py` | `tests/strategy/test_stop_floor.py` |
| REQ-037-4 | B | `strategy/volatility/models.py`(fallback → effective 연결), `strategy/volatility/thresholds.py`(fallback 분기), `watchers/position_watchdog.py` | `tests/strategy/test_fallback_threshold.py` |
| REQ-037-5 | B | `personas/orchestrator.py`(halt 게이트 — sell 우회), halt 종류 판별 헬퍼, Telegram notifier | `tests/personas/test_count_halt_sell_bypass.py` |
| REQ-037-6 | B | `personas/prompts/decision.jinja`(sell 룰 정렬), 필요 시 `risk.jinja` | `tests/personas/test_sell_rule_alignment.py`(grep/렌더 검증) |
| REQ-037-7 | C | (전 Phase 검증), `edge/*`(재사용), `cli.py edge-report`(재사용) | 위 재현 테스트 전체 + 배포 후 라운드트립 관측 |

| 외부 의존 | 설명 |
|---|---|
| SPEC-TRADING-033 | `position_watchdog` direct-sell-bypass 패턴(count-halt 우회 sell 의 본보기, REQ-037-5) |
| SPEC-TRADING-029 | balance()/cash_pct·holdings — 포지션/체결 reconcile(엣지 측정 입력) |
| SPEC-TRADING-016 | 자본 보전 우선 정책 + event-driven 출구 룰 계보 |
| SPEC-TRADING-036 | 변동성 레짐 vs macro regime 분리(ATR 출구는 변동성 레짐 유지 — 비목표 근거) |
| SPEC-TRADING-019 | KOSPI200 유니버스 소스(`get_index_portfolio_deposit_file '1028'`) |
| SPEC-TRADING-002 | 실거래 분리 — live 잠금 유지 근거(C-3/C-7) |
