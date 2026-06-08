# SPEC-TRADING-042 — Acceptance Criteria

> Given-When-Then. AC-1~4 는 RC-1~4 reproduction(재현) 시나리오를 포함한다(money/risk → run 단계 TDD 선행).
> AC-5 는 **LIVE 스위치 전 PAPER 에서 반드시 통과해야 하는 live-readiness 게이트**다.

## AC-1 — broker-truth 단일 원장이 phantom 매도 reject 를 제거 [REQ-042-A] (RC-1 reproduction)

- **Given** 합성 매수로 로컬 `positions` 에 보유 행이 생성됐으나 KIS 잔고엔 해당 보유가 없다
  (phantom position, 2026-06-08 000270 상황).
- **When** 매도 결정 사이클이 실행되고, 그 직전 인트라데이 reconcile 이 수행된다.
- **Then** 매도는 KIS 잔고로 확인된 보유에만 발생하고, phantom 에 대한 실 KIS 매도가
  "잔고내역이 없습니다"로 reject 되지 않는다. 매 주문 직후에도 reconcile 이 수행된다.
- **And (paper/live 패리티)** 체결 확인이 동일 코드 경로(live=체결조회, paper=balance reconcile)
  를 사용한다(REQ-042-A3).
- **And (좁은 fallback)** paper 합성 fallback 적용 시 직후 reconcile 과 drift 가 0 이다(REQ-042-A4).
- **And (재현 게이트)** 인트라데이 reconcile 도입 전에는 phantom 매도가 reject 됨을 보이는
  실패 테스트(000270 −10.8% reject 재현)가 선행된다.
- **And (live 안전)** live 경로에서 합성 체결이 수행되지 않고(`mode != PAPER` no-op), KIS 미확인
  보유에 매도가 발생하지 않는다(REQ-042-A5).

## AC-2 — submitted 정체가 resolver 로 반드시 해소 [REQ-042-B] (RC-2 reproduction)

- **Given** 주문이 KIS 수락(rt_cd=0) 후 합성/체결 단계 throw 로 `submitted` 에 머문다(071050 상황).
- **When** order-state resolver 가 bounded window 초과 주문을 평가한다.
- **Then** KIS 체결 상태 폴 결과에 따라 `filled` 로 표시되거나, 취소 후 `cancelled`/`expired`
  로 표시되어 **반드시 해소** 된다.
- **And (일회성 cleanup)** 현재 누수 5건(086790/055550/064350/000270/071050)이 일회성 경로로
  해소된다(REQ-042-B2).
- **And (안전)** 이미 체결/취소된 주문을 이중 전이하지 않고, 미체결을 임의 filled 처리하지
  않는다(REQ-042-B3).
- **And (재현 게이트)** resolver 도입 전에는 submitted 가 영구 정체됨을 보이는 실패 테스트가 선행된다.

## AC-3 — 매도 in-flight 락이 중복 손절 발사를 제거 [REQ-042-C] (RC-3 reproduction)

- **Given** 종목(033780)의 매도가 pending/in-flight 이고, watchdog(*/5)와 persona orchestrator 가
  같은 종목 손절을 동시에 평가한다.
- **When** 두 경로가 같은 사이클대에 매도를 시도한다.
- **Then** 종목당 in-flight 락이 중복 발사를 억제하여 5분간 4회가 아닌 단일 매도만 발생한다.
- **And (신규 시그널 보존)** 직전 매도가 **해소된 뒤**의 진짜 신규 출구 시그널은 막히지 않는다
  (REQ-042-C2).
- **And (멱등)** 락이 `position_action_markers` 로 재시작에 견디게 관리된다(REQ-042-C3).
- **And (재현 게이트)** 락 도입 전에는 033780 이 5분 4회(09:04/09:31/09:32/09:34) 발사됨을 보이는
  실패 테스트(2026-06-08 재현)가 선행된다.

## AC-4 — realized_pnl_cum 집계·영속화 + 자산 정합 [REQ-042-D] (RC-4 reproduction)

- **Given** 확인된 매도 체결로 round-trip 이 완성된다.
- **When** realized P&L 집계가 실행된다.
- **Then** `daily_equity_snapshot.realized_pnl_cum` 이 (수수료 차감) 채워지고 더 이상 NULL 이 아니다.
- **And (정합)** 헤드라인 자산(SPEC-041 D+2 basis)과 정합되며, net 현금흐름을 실현손익으로
  오인하지 않는다(REQ-042-D2, SPEC-039 정합).
- **And (재현 게이트)** 백필 도입 전에는 round-trip 완성에도 realized_pnl_cum 이 전 행 NULL 임을
  보이는 실패 테스트가 선행된다.

## AC-5 — LIVE-READINESS 게이트 (PAPER 에서 라이브 스위치 전 필수 통과)

- **Given** PAPER 모드 한 거래일 전 구간(full session) 운영 데이터.
- **When** 세션 종료 시 검증한다.
- **Then** 아래를 **모두** 만족해야 라이브 스위치를 허용한다:
  - **zero phantom position** — 로컬 `positions` 가 세션 전 구간 KIS 잔고와 정합(divergence 0).
  - **zero stuck submitted** — 세션 종료 시 미해소 submitted 0건.
  - **모든 결정 손절이 체결 확인 또는 명시 취소** — 결정된 모든 stop-loss 가 filled 로 확인되거나
    사유와 함께 cancelled/expired 로 명시 해소(reject 후 방치 0).
  - **realized_pnl_cum 채워짐 + 자산 정합** — round-trip 발생 시 realized_pnl_cum non-NULL,
    헤드라인 자산과 일치.
- **And (정직성)** 산출 리포트는 "페이퍼 체결가 ≠ 실거래 체결가" caveat 와 paper/live 패리티
  검증 범위를 명시한다.

## Definition of Done
- [ ] AC-1~4 reproduction 테스트(RC-1~4) 선행 → 통과.
- [ ] broker-truth 단일 원장 + 인트라데이 reconcile(매도 사이클 전 + 주문 후).
- [ ] paper/live 체결 확인 단일 코드 경로(live=체결조회, paper=reconcile).
- [ ] 합성 = 좁은 paper fallback, drift 0, live 합성 불가 유지.
- [ ] order-state resolver/timeout + 누수 5건 cleanup.
- [ ] 매도 in-flight 락 + 쿨다운(신규 시그널 보존, 멱등).
- [ ] realized_pnl_cum 집계·영속화 + 헤드라인 자산 정합.
- [ ] **AC-5 live-readiness 게이트 PAPER full-session 통과** (라이브 스위치 전제).
- [ ] live 경로 byte-for-byte 불변, `live_unlocked` 미변경.
- [ ] 신규 행위 전부 audit_log 추적.
- [ ] 마이그레이션 031 필요 여부 확정(불필요 시 미사용).

## 품질 게이트
- pytest 커버리지 ≥ 85%, money/risk reproduction-first(RC-1~4).
- ruff/black 통과. EARS 추적성 유지(spec ↔ acceptance).
- **LIVE 임박: AC-5 미통과 시 라이브 스위치 금지(하드 게이트).**
