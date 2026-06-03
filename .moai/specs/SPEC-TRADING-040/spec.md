---
id: SPEC-TRADING-040
version: 0.1.0
status: draft
created: 2026-06-03
updated: 2026-06-03
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "출구 정책 개편 — 적정 익절·트림 + 종목 집중 상한·자동 트림 + daily_count 매도 예산 분리 + 단기과열 반복매수 억제"
related_specs:
  - SPEC-TRADING-037   # 출구 백테스트(exit_sweep) — 적정 익절 임계는 이 백테스트로 보정(추측 금지)
  - SPEC-TRADING-039   # 페이퍼 합성 체결 + daily_pnl_pct 실현손익 — 출구 "경로" 복원(본 SPEC 은 출구 "정책")
  - SPEC-TRADING-033   # position_watchdog — 자동 출구 직접 매도 경로(classify_holding 확장 대상)
  - SPEC-TRADING-034   # 포트폴리오 페르소나 — 보유 종목수·집중 운영 룰
  - SPEC-TRADING-036   # late-cycle 방어 — 트림과 방향 일치(시너지), bull/현금 바닥 가드
---

# SPEC-TRADING-040 — 출구 정책 개편

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-06-03 | 0.3.0 | M1c 정체 로테이션(stagnation rotation) **배선 완료**. v0.2.0 에서 `is_stagnant()` 는 구현됐으나 watchdog 가 `holding_days`/`rsi` 를 주입하지 않아 실제로는 발화 불가였음. 본 버전이 두 입력을 연결: (1) **holding_days** = `orders` 테이블의 최초 매수 체결일(MIN(COALESCE(filled_at,ts)) WHERE side='buy' AND status IN('filled','partial'))로부터 경과일, no-rows→None(방어적 skip), helper `_holding_days()`. (2) **rsi** = 기존 screener 인라인 RSI 공식을 `strategy/volatility/rsi.py`(`rsi_from_closes`/`compute_rsi`)로 **추출·공유**(재구현 아님; screener 도 동일 함수 사용하도록 리팩터), helper `_ticker_rsi()`→`compute_rsi` 재사용, unavailable/error→None. (3) watchdog skip-branch 에서 집중 트림 미발생 시 `is_stagnant()` 평가→정체면 부분 로테이션(STAGNATION_TRIM_FRACTION=0.5, `_execute_trim(kind='rotate')`). **집중+정체 동일 `action='trim'` 멱등 마커 공유**→같은 종목/일 1회만 트림(double-sell 방지). over-sell clamp 유지. `rotate_exits` 메트릭 추가. **마이그레이션 불필요**(action='trim' 재사용). live 불변(seam 추가만). 17 신규 테스트(12 watchdog wiring + 5 rsi → rsi DB 분기 추가로 최종 8), 1141 passed / 6 pre-existing fails(0 신규 회귀; cli_bridge 1건 flaky 확인). **정직성:** 실제 보유(064350 −2.37% 5월초 보유 등)는 holding_days≥20·|pnl|<3% 충족 가능하나, **RSI 데이터가 ohlcv 캐시에 있어야 발화**—paper 보유 종목의 RSI 가용 여부는 라이브 검증 필요(없으면 None→방어적 skip, 첫 round-trip 은 집중 트림 경로로 발생). — 2026-06-03 | onigunsow |
| 2026-06-03 | 0.2.0 | Run-phase implementation (TDD, reproduction-first). **Locked decisions:** (1) 집중 상한 N% = **25%** (late-cycle 강화 20%); (2) 매도는 daily_count 에서 제외(매수만 카운트, K=2 예산 확보); (3) 단기과열 반복매수 = 당일 1회 + 손실 물타기 거부; (4) 익절 임계 = exit-backtest 도출. **exit-backtest 결과(2026-06-03, 10y KOSPI200, 201종목/90,803 trades/param):** take=3.0×ATR(wide) expectancy +0.11~+0.34%(전부 양수, 최대), take=2.0×ATR +0.06~−0.06%(0 근방), take=1.5×ATR(narrow) −0.10~−0.18%(전부 음수, 승률 41~58% 높지만 기대값 음수 = SPEC-037 트랩 재확인). **결정: 적정(moderate) 익절 추가 REJECT** — 3.0×ATR 미만의 어떤 익절도 기대값을 낮춤(REQ-040-1b 게이트 위반). 기존 live 3.0×ATR(최대 기대값) 유지. 첫 round-trip 은 **EV-면제 트림**(집중 상한+정체 로테이션)과 반복매수 차단으로 생성(EV-harming 조기 익절 강요 안 함). **구현:** M1a/1b=profit_take_gate.py(EV 게이트, adopt=False), M1c=is_stagnant()+rotate, M2=classify_concentration()+_execute_trim(watchdog 코드강제, position_action_markers action='trim' 멱등, over-sell clamp, late-cycle 강화), M3=check_pre_order side-aware(매도 count 제외, 매수 한도−K), M4=check_pre_order overheat 1/day+avg-down 거부, M5=roundtrips 측정+audit. **마이그레이션 030 불필요**(position_action_markers action 컬럼 free-form TEXT 재사용). live byte-for-byte 불변(check_pre_order 추가 인자 default 보존). 31 신규 테스트, 1121 passed / 6 pre-existing fails(0 신규 회귀). NOT pushed/deployed/migrated. — 2026-06-03 | onigunsow |
| 2026-06-03 | 0.1.0 | Initial draft. 라이브 DB + 프롬프트 검증(2026-06-03)으로 규명: SPEC-039 가 출구 "메커니즘"(페이퍼 합성 체결)을 고쳤으나 출구 "정책"이 극단치 전용이라 정상장에서 누적만 발생 → round-trip 0건 → 수익성 검증 불가. 7일 `persona_decisions` hold 362/buy 103/sell 3, 보유 6종목 −2.37%~+2.26%(RSI<85) 전부 출구 미해당, 086790 10주 집중·6/2 7회 물타기, 드문 매도 시그널은 daily_count·거짓 daily_loss 로 차단. 사용자 결정: 4방향 동시 구현 — (1)적정 익절/트림, (2)집중 상한+자동 트림, (3)daily_count 매도 예산 분리, (4)단기과열 반복매수 억제. 핵심 원칙: 익절 임계는 SPEC-037 `exit-backtest` 로 보정(추측 금지, 기대값 비감소 검증), TRIM 과 PROFIT/LOSS 출구 분리(트림=리스크 동기→기대값 제약 면제, 익절=기대값 동기→백테스트 제약 적용), 엔트리 엣지는 백테스트 불가 명시, late-cycle 방어와 시너지. paper-first·live 불변, money/risk 는 run 단계 reproduction-first TDD. 마이그레이션 030 예약(필요 시). — 2026-06-03 | onigunsow |

---

## 개요 (Environment & Assumptions)

### Environment
- 페이퍼(모의) 자동매매 운영 중. SPEC-039 로 매도 합성 체결·`daily_pnl_pct` 실현손익 교정 완료.
- 결정 페르소나(decision.jinja)와 position_watchdog(*/5)가 출구를 담당하나 둘 다 극단치 전용.
- late-cycle 방어 moderate 활성(margin ~35.7조, 현금 바닥 30%, 신규 진입 제한).

### Assumptions
- 출구 룰 임계는 SPEC-037 `exit_sweep`/`trading exit-backtest`(10y KOSPI200, 201종목/454k행)로 보정 가능하다.
- 트림(집중 상한·정체 로테이션)은 기대값 중립이라도 집중 리스크 감소로 정당하다.
- LLM 엔트리 엣지는 look-ahead 때문에 백테스트로 검증 불가하다(forward paper 만 검증).
- 위험 축소 출구(트림 포함)는 매수 게이트·count-halt 에 막히면 안 된다(capital-preservation 하드룰).

---

## 요구사항 (EARS Requirements) — 4방향 → 5모듈

### 모듈 1 (REQ-040-1) — 적정 익절·정체 로테이션 룰 [방향 1: 적정 익절/트림 중 "익절"]

- **REQ-040-1a (State-driven, profit-taking):**
  IF 보유 종목의 평가익이 *적정 익절 임계*(SPEC-037 `exit-backtest` 로 보정된 값, 기대값 비감소 검증 통과)에 도달
  THEN 시스템은 단계적 부분 익절 시그널을 발생시켜야 한다.
  (기존 RSI>85 극단 룰은 유지하되, 그 아래의 정상 구간 익절 단계를 **추가**한다.)

- **REQ-040-1b (Unwanted):**
  적정 익절 임계는 SPEC-037 백테스트에서 기대값을 낮추는 값(예: narrow take-profit 트랩)으로
  설정되어서는 안 된다. 시스템은 백테스트 기대값 비감소를 통과하지 못한 익절 임계를 적용하지 않아야 한다.

- **REQ-040-1c (State-driven, 정체 로테이션 = TRIM 계열):**
  IF 보유 종목이 *정체 조건*(보유일수 임계 초과 AND 손익 무수익 구간 AND RSI 중립)에 해당
  THEN 시스템은 해당 보유의 부분 로테이션(트림) 시그널을 발생시켜야 한다.
  (정체 로테이션은 리스크/리밸런싱 동기 → REQ-040-1b 기대값 제약 **면제**.)

> 분리 원칙: REQ-040-1a/1b(익절)는 기대값 제약 적용, REQ-040-1c(정체 트림)는 면제.

### 모듈 2 (REQ-040-2) — 종목 집중 상한 + 자동 트림 [방향 2]

- **REQ-040-2a (State-driven, code-enforced TRIM):**
  IF 단일 종목 평가금액이 포트폴리오의 *집중 상한 N%*(run 단계 확정, `RISK_PER_TICKER_MAX_POSITION=20%` 와 정합)를 초과
  THEN 시스템은 해당 종목을 상한 이하로 되돌리는 자동 트림(부분 매도)을 실행해야 한다.
  (페르소나가 사실상 매도하지 않으므로 **코드 강제** — position_watchdog 직접 매도 경로 재사용.)

- **REQ-040-2b (Ubiquitous, 멱등 가드):**
  시스템은 같은 종목의 자동 트림을 같은 KST 거래일에 중복 실행하지 않도록 멱등 마커로 가드해야 한다
  (position_watchdog 의 `position_action_markers` 패턴 재사용).

- **REQ-040-2c (State-driven, late-cycle 시너지):**
  WHILE late-cycle 방어가 활성인 동안, 시스템은 집중 상한 N% 를 강화(더 낮은 트림 트리거)해야 한다.
  (방어와 충돌 아닌 시너지 — REQ-036-3 의 현금 바닥/forced sell 과 정합.)

- **REQ-040-2d (Unwanted):**
  자동 트림은 보유하지 않은 종목에 대해 매도(공매도)를 발생시켜서는 안 되며,
  보유 수량을 초과해 매도해서는 안 된다(SPEC-039 over-sell clamp 선례 준수).

### 모듈 3 (REQ-040-3) — daily_count 매도 예산 분리 [방향 3]

- **REQ-040-3a (Event-driven, 예방적 예산):**
  WHEN 일일 주문 수가 `RISK_DAILY_ORDER_COUNT_MAX(=10)` 에 접근(매수가 카운터를 소진하려 할 때)
  THEN 시스템은 매도용 예산 K건을 항상 확보하여, 매수가 매도 예산을 잠식하지 못하게 해야 한다.
  (예: 매수는 `한도 − K` 건으로 제한, 잔여 K건은 매도 전용. 메커니즘은 run 단계 확정.)

- **REQ-040-3b (Ubiquitous, 위험 축소 출구 보존):**
  매도 예산은 위험 축소 출구(손절·트림·익절)에만 적용되며, 신규 매수에는 적용되지 않아야 한다.
  (SPEC-037 의 사후 count-halt SELL bypass(orchestrator L288-410)는 안전망으로 유지하되,
   본 요구는 *예방적* 분리 — halt 트립 전에 매도 여지를 남긴다.)

- **REQ-040-3c (Unwanted):**
  매도 예산 분리는 live 주문 경로의 카운트 의미(`daily_order_count_today`)를 변경해서는 안 되며,
  paper 우선으로 도입한다(live 영향 최소화).

### 모듈 4 (REQ-040-4) — 단기과열 반복매수 억제 [방향 4]

- **REQ-040-4a (Unwanted, entry control):**
  IF 같은 종목에 당일 *반복 매수*(임계 횟수 초과)가 단기과열(stat_cls=55) 상태에서 시도
  THEN 시스템은 추가 매수 시그널을 차단(또는 강한 감점)해야 한다.
  (decision.jinja L30 "1일 1회" 권고를 **코드 강제**로 승격, screener L67-70 감점 강화와 연계.)

- **REQ-040-4b (State-driven):**
  IF 보유 종목이 손실 구간이면서 단기과열 상태
  THEN 시스템은 해당 종목 물타기(평균단가 낮추기) 매수를 거부해야 한다(가치 트랩 회피, decision.jinja L17 정합).

### 모듈 5 (REQ-040-5) — round-trip 검증 + 정직성 [횡단 관심사: 측정·정직성]

- **REQ-040-5a (Ubiquitous):**
  시스템은 첫 완성 round-trip 이 발생함을 `edge/roundtrips.py`(FIFO `net_pnl`)로 측정·기록해야 한다.

- **REQ-040-5b (Ubiquitous, 정직성):**
  본 SPEC 산출물(리포트·문서)은 백테스트가 **출구 룰만** 검증하며 LLM 엔트리 엣지는
  look-ahead 로 검증 불가함을 명시해야 한다(edge/scorecard.py `limitations_footer` 톤 일치).

- **REQ-040-5c (Trackable):**
  모든 신규 트림/예산 분리/반복매수 차단 행위는 audit_log 로 추적 가능해야 한다.

---

## 사양 (Specifications)

- 적정 익절 임계: SPEC-037 `exit-backtest` 결과 기반(run 단계). 기대값 비감소 검증 필수.
- 집중 상한 N%: run 단계 확정(20% 와 정합, late-cycle 강화). 자동 트림은 position_watchdog 경로.
- 매도 예산 K: run 단계 확정. paper 우선, live 카운트 의미 불변.
- 단기과열 반복매수 임계: run 단계 확정(당일 N회 / 55 상태 / 손실 물타기).
- 마이그레이션: 가능하면 불필요(`position_action_markers` 재사용, `orders` 집계). 필요 시 **030** 예약.

## Traceability

| REQ | 방향 | 재사용 자산 | 검증(acceptance) |
|---|---|---|---|
| REQ-040-1a/1b | 1 익절 | decision.jinja, exit_sweep | AC-1 |
| REQ-040-1c | 1 트림(정체) | decision.jinja, watchdog | AC-1 |
| REQ-040-2a~2d | 2 집중 트림 | position_watchdog, limits, late_cycle | AC-2 |
| REQ-040-3a~3c | 3 매도 예산 | limits, orchestrator bypass | AC-3 |
| REQ-040-4a/4b | 4 반복매수 억제 | screener, decision.jinja | AC-4 |
| REQ-040-5a~5c | 측정·정직성 | edge/roundtrips, scorecard, audit | AC-1~4 공통 |
