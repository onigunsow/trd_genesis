# SPEC-TRADING-040 — Implementation Plan

> 코드 미작성. 본 문서는 run 단계 실행 계획·ADR·리스크 정리.

## 중심 ADR (Architecture Decision Records)

### ADR-1: TRIM 과 PROFIT/LOSS 출구를 별개 요구로 분리
- **결정:** 트림(집중 상한·정체 로테이션)과 익절(profit-taking)을 별개 요구 모듈로 둔다.
- **이유:** 동기가 다르다 — 트림은 리스크/리밸런싱(기대값 중립이라도 집중 리스크 감소로 정당),
  익절은 기대값(이익 실현). 백테스트 기대값 제약은 **익절에만** 적용하고 트림은 면제한다.
- **영향:** narrow take-profit 트랩(SPEC-037 결과)으로 기대값을 깎지 않으면서, 집중·정체는
  EV 중립이어도 해소할 수 있다.

### ADR-2: 임계는 백테스트로 보정 — SPEC 에서 추측 하드코딩 금지
- **결정:** 적정 익절 임계값은 run 단계에서 `trading exit-backtest`(exit_sweep.py)로 결정.
- **이유:** SPEC-037 가 이미 10y KOSPI200 데이터로 robust 선택 로직(`recommend`) 보유.
  추측한 narrow take-profit 은 음수 기대값 트랩.
- **영향:** 익절 임계는 기대값 비감소 검증을 통과해야 채택(REQ-040-1b 게이트).

### ADR-3: 집중 상한 자동 트림은 코드 강제(페르소나 아님)
- **결정:** 집중 상한 초과 시 자동 트림은 position_watchdog 직접 매도 경로로 코드 강제.
- **이유:** 결정 페르소나는 7일간 sell 3건뿐 — 프롬프트 권고로는 트림이 일어나지 않는다.
- **영향:** watchdog `classify_holding` 에 trim 분기 추가, `position_action_markers` 멱등 가드 재사용.

### ADR-4: daily_count 매도 예산은 예방적 분리(사후 bypass 와 병행)
- **결정:** 매수를 `한도 − K` 로 제한해 매도용 K건을 항상 확보. SPEC-037 사후 bypass 는 안전망 유지.
- **이유:** 사후 bypass 는 이미 halt 트립된 뒤 동작 — 예방적 분리가 매도 여지를 *미리* 남긴다.
- **영향:** `check_pre_order`/orchestrator 매수 경로에 side-aware 카운트 분기. live 카운트 의미 불변.

### ADR-5: 단기과열 반복매수 억제 = 엔트리 컨트롤(코드 강제)
- **결정:** decision.jinja "1일 1회" 권고를 코드 강제로, screener 감점과 연계.
- **이유:** 086790 6/2 7회 물타기 같은 누적·집중을 진입 단계에서 차단(출구 부담 경감).
- **영향:** 누적·집중의 *원인*을 줄여 ADR-3 트림 부담도 줄인다.

### ADR-6: paper-first, live 불변, reproduction-first
- **결정:** money/risk 로직(트림·예산·차단)은 reproduction-first TDD. live 경로 byte-for-byte 불변.
- **이유:** SPEC-038/039 의 거짓 halt 사례 — money 로직은 재현 테스트 선행이 안전.

## 마일스톤 (우선순위 기반, 시간 추정 없음)

- **Primary Goal:** REQ-040-2 집중 상한 자동 트림(코드 강제) + REQ-040-1c 정체 로테이션
  → 첫 완성 round-trip 발생(수익성 검증 게이트 해제). 086790 집중 즉시 해소.
- **Secondary Goal:** REQ-040-1a/1b 적정 익절(백테스트 보정) — 정상 구간 익절 round-trip.
- **Tertiary Goal:** REQ-040-3 daily_count 매도 예산 분리(매수가 매도를 굶기지 않음).
- **Final Goal:** REQ-040-4 단기과열 반복매수 억제(누적·집중 원인 차단).
- **횡단:** REQ-040-5 round-trip 측정·정직성·audit (전 마일스톤 공통).

## 기술 접근

1. 백테스트 보정: `trading exit-backtest` 실행 → robust 익절 임계 도출 → REQ-040-1b 게이트.
2. watchdog 확장: `classify_holding` 에 `trim`(집중 상한) / `rotate`(정체) 분기 추가,
   `eff_stop`/`eff_take` 와 별도의 트림 트리거. 멱등 마커 `action='trim'`(스키마 변경 가능성 확인).
3. 예산 분리: side-aware daily_count — 매수는 `한도 − K`, 매도/트림은 항상 통과.
4. 엔트리 억제: screener 감점 강화 + `check_pre_order`(또는 orchestrator) 당일 반복매수 카운트 가드.
5. 정합: late_cycle 활성 시 트림 트리거 강화(REQ-040-2c).

## 리스크 및 대응

| 리스크 | 대응 |
|---|---|
| 적정 익절이 기대값을 깎는 트랩이 됨 | REQ-040-1b 백테스트 게이트(기대값 비감소 강제) |
| 트림 과다로 과매매·비용 누적 | 멱등 1일 1회 가드 + 부분 트림(전량 아님) + 비용 인지(decision.jinja L33-43) |
| 매도 예산 분리가 live 카운트 의미 변경 | paper-first, live 카운트 함수 불변, side-aware 분기만 |
| 트림과 late-cycle forced sell 중복 | 멱등 마커로 같은 종목 중복 트림 방지, 방어 우선(REQ-040-2c) |
| 반복매수 차단이 정상 분할매수 막음 | 단기과열(55)·손실 물타기 조건으로 한정(REQ-040-4a/4b) |
| over-sell/공매도 | SPEC-039 clamp 패턴 준수(REQ-040-2d) |

## 마이그레이션

- 잠정: **불필요** 가능성 높음(`position_action_markers` 재사용, `orders` 집계).
- 신규 컬럼 필요 확정 시에만 **030** 사용(현재 최신 029, 027 결번).
- run 단계 첫 작업: `position_action_markers` 스키마 확인 → trim 마커 수용 가능 여부 결정.

## reproduction-first 대상(money/risk)
- 정상 구간 보유(+2.26%/RSI<85)가 트림/익절을 트리거 → 첫 round-trip (AC-1).
- 086790 집중 초과 → 자동 트림 (AC-2).
- 매수가 daily_count 소진해도 매도 예산 보존 (AC-3).
- 단기과열 동일 종목 반복 매수 차단 (AC-4).
