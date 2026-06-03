# SPEC-TRADING-040 — Acceptance Criteria

> Given-When-Then. AC-1~4 는 reproduction(재현) 시나리오를 포함한다(money/risk → run 단계 TDD 선행).

## AC-1 — 정상 구간 보유가 익절/정체 트림으로 첫 round-trip 발생 [REQ-040-1, 5a] (reproduction)

- **Given** 보유 종목이 정상 구간(예: 평가익 +2.26%, RSI < 85)이고, 적정 익절 임계가
  SPEC-037 `exit-backtest` 로 보정되어 기대값 비감소를 통과한 값으로 설정되어 있다.
- **When** 출구 평가 사이클(결정 페르소나 또는 position_watchdog)이 실행된다.
- **Then** 해당 보유에 부분 익절(또는 정체 조건 시 부분 로테이션) 시그널이 발생하고,
  매도가 합성 체결되어(SPEC-039) `edge/roundtrips.py` 가 **첫 완성 round-trip** 을 기록한다.
- **And (재현 게이트)** 동일 보유가 기존 극단 룰(RSI>85)에서는 매도되지 않았음을 보이는 실패 테스트가
  선행되어, 본 룰 추가로 비로소 round-trip 이 완성됨을 증명한다.
- **And** 적정 익절 임계가 기대값을 낮추는 값이면 적용되지 않는다(REQ-040-1b).

## AC-2 — 종목 집중 초과 시 자동 트림 [REQ-040-2, 5c] (reproduction)

- **Given** 단일 종목(예: 086790)이 포트폴리오의 집중 상한 N% 를 초과한다(예: 086790 10주 누적).
- **When** position_watchdog */5 폴 또는 집중 평가 경로가 실행된다.
- **Then** 해당 종목을 상한 이하로 되돌리는 자동 트림(부분 매도)이 **코드 강제로** 실행되고,
  `POSITION_WATCHDOG_*`/트림 audit 가 기록된다.
- **And (멱등)** 같은 KST 거래일에 같은 종목의 트림이 중복 실행되지 않는다(마커 가드, REQ-040-2b).
- **And (late-cycle 시너지)** 방어 활성 시 더 낮은 트림 트리거가 적용된다(REQ-040-2c).
- **And (안전)** 보유 수량을 초과하거나 미보유 종목에 매도하지 않는다(REQ-040-2d, over-sell clamp).
- **And (재현 게이트)** 트림 도입 전에는 086790 이 무한 누적되어 트림이 일어나지 않음을 보이는
  실패 테스트가 선행된다.

## AC-3 — 매수가 daily_count 를 소진해도 매도 예산 보존 [REQ-040-3] (reproduction)

- **Given** 당일 매수가 일일 주문 한도(`RISK_DAILY_ORDER_COUNT_MAX=10`)에 근접하고,
  보유 종목에 위험 축소 매도(손절/트림/익절) 시그널이 대기 중이다.
- **When** 추가 매수와 대기 매도가 같은 사이클에서 평가된다.
- **Then** 매수는 `한도 − K` 에서 차단되어 매도용 K건이 보존되고, 대기 매도가 실행된다.
- **And (재현 게이트)** 예산 분리 도입 전에는 매수가 카운터를 소진해(예: 11:48 halt) 매도가
  발사되지 못함을 보이는 실패 테스트(5/26·5/28 사례 재현)가 선행된다.
- **And** live 의 `daily_order_count_today` 카운트 의미는 변하지 않는다(REQ-040-3c, paper-first).

## AC-4 — 단기과열 동일 종목 반복 매수 차단 [REQ-040-4] (reproduction)

- **Given** 종목 086790 이 단기과열(stat_cls=55) 상태이고 손실 구간이며, 당일 이미 임계 횟수 매수되었다.
- **When** 같은 종목에 추가 매수 시그널이 시도된다.
- **Then** 추가 매수가 차단(또는 강한 감점으로 탈락)되고, 차단 audit 가 기록된다.
- **And (가치 트랩 회피)** 손실 구간 + 단기과열 종목의 물타기 매수는 거부된다(REQ-040-4b).
- **And (재현 게이트)** 억제 도입 전에는 086790 을 당일 7회 물타기할 수 있었음을 보이는
  실패 테스트(6/2 사례 재현)가 선행된다.

## AC-5 — 정직성·정합 [REQ-040-5b, ADR]

- **Given** 본 SPEC 산출 리포트/문서.
- **When** 백테스트 결과를 인용한다.
- **Then** 백테스트가 **출구 룰만** 검증하며 LLM 엔트리 엣지는 look-ahead 로 검증 불가함을
  명시한다(edge/scorecard.py `limitations_footer` 톤 일치).
- **And** 트림(리스크 동기)과 익절(기대값 동기)이 분리되어, 익절만 백테스트 기대값 제약을 받음을 명시한다.

## Definition of Done
- [ ] AC-1~4 reproduction 테스트 선행 → 통과(첫 round-trip 발생 확인).
- [ ] 적정 익절 임계가 `exit-backtest` 기대값 비감소 검증 통과.
- [ ] 집중 자동 트림 코드 강제 + 멱등 가드 + over-sell clamp.
- [ ] daily_count 매도 예산 분리(paper-first, live 카운트 불변).
- [ ] 단기과열 반복매수 차단(손실 물타기 거부 포함).
- [ ] live 경로 byte-for-byte 불변, `live_unlocked` 미변경.
- [ ] 신규 행위 전부 audit_log 추적.
- [ ] 마이그레이션 030 필요 여부 확정(불필요 시 미사용).

## 품질 게이트
- pytest 커버리지 ≥ 85%, money/risk reproduction-first.
- ruff/black 통과. EARS 추적성 유지(spec ↔ acceptance).
