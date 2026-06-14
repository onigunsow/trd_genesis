# SPEC-TRADING-045 인수 기준 (Acceptance Criteria)

> 정직성 규칙: 모든 단언은 실제 테스트 출력/관측으로 검증한다. [확인 필요] 항목은 운영자
> 라이브 실측 전까지 "가정"으로 표기하며, 충족 전 라이브 전면 승급을 차단한다.

## AC-1 — 6/8 실패모드 재현 (reproduction-first) [HARD, 선행]

- **Given** 체결 확인 단계에서 합성/조회 실패(예: `ORDER_SYNTHETIC_ERROR` 또는 체결조회 예외)가
  주입된 상태와 fake clock.
- **When** SELL 주문이 제출되고 체결 확인이 실패한 뒤 `order_resolver` 윈도우(15분)가 경과한다.
- **Then** 해당 SELL은 `submitted`에 **영구히 갇히지 않고** `expired`(또는 KIS 확인 시 `filled`)로
  수렴하며, 매도 의도가 조용히 소실되지 않는다(다음 사이클 KIS 진실원으로 재평가 가능).
- **And (재현 게이트)** **수정 전** 이 테스트가 실패(주문이 submitted에 갇힘)를 **보여야** 하며,
  수정 후 통과한다. (REQ-045-B1/B2/B3)

## AC-2 — live 체결조회 seam 구현

- **Given** live 모드 클라이언트와 KIS `inquire-daily-ccld` 체결조회 응답(체결수량/체결가 포함).
- **When** `confirm_fills(client)`가 호출된다.
- **Then** live 분기가 `BrokerFillInquiryNotImplemented`를 raise하지 **않고** 실제 체결조회를
  수행하여 주문 상태를 broker 체결로 확정한다(REQ-045-A1).
- **And (페이서)** 체결조회는 `client.get()` 전역 TPS 페이서를 경유하며, 통제되지 않은 추가 KIS
  호출을 만들지 않는다(REQ-045-A3, SPEC-043 존중).
- **And (단일 경로)** 체결확인은 SPEC-042 단일 경로(`confirm_fills`) 안에서만 처리되고 병렬 경로를
  신설하지 않는다(REQ-045-A4).

## AC-3 — 미확인 체결 위조 금지

- **Given** live 체결조회가 빈 응답이거나 rt_cd≠0이거나 체결을 확정할 수 없는 상태.
- **When** `confirm_fills(client)`가 해당 주문을 처리한다.
- **Then** 주문을 `filled`로 **위조하지 않고**, 미확인을 audit하며 `order_resolver`의 `expired`
  수렴에 맡긴다(REQ-045-A2, REQ-045-B3, ADR-045-2).

## AC-4 — paper 경로 불변 (회귀 방지)

- **Given** PAPER 모드 클라이언트.
- **When** `confirm_fills(client)`가 호출된다.
- **Then** 기존 SPEC-029 balance-reconcile 경로를 그대로 사용하고 `inquire-daily-ccld`를 호출하지
  않는다(REQ-045-A5). order.py/fills.py/sell_lock.py/account.py는 byte-for-byte 불변.

## AC-5 — 라이브 멱등성 / 이중매도 안전

- **Given** position_watchdog(`*/5`)와 Decision 사이클이 같은 종목 손절을 동시에 평가하는 상황.
- **When** 두 경로가 매도를 시도한다.
- **Then** SPEC-042 `sell_lock.guard_sell`로 정확히 **한 건**의 live SELL만 발사되고 중복 KIS
  매도는 0이다(REQ-045-D1).
- **And (자동 해제)** live 체결조회가 주문 체결을 확인하면 `sell_lock` submitted leg가 자동 해제되어
  체결 후 정당한 새 매도 신호가 부당하게 영구 차단되지 않는다(REQ-045-D2).
- **And (멱등)** 동일 주문에 대한 반복 체결조회는 이미 터미널/advanced 상태인 주문을 재전이하지
  않는다(REQ-045-D3).

## AC-6 — 소액 라이브 스모크 게이트 (execution-only) [LIVE 승급 하드 게이트]

- **Given** 운영자 라이브 자격증명 + `live_unlocked=true` + 최소 수량(예: 1주)·최소 종목 설정.
- **When** 소액 라이브 매수→매도 round-trip이 한 건 완료된다.
- **Then** 아래 **관측 가능한 증거**를 **모두** 충족해야 전면 라이브 승급을 허용한다:
  - **실제 체결 확인된 live fill 1건** — `inquire-daily-ccld`로 broker 체결이 확인됨(만료 아님).
  - **원장 정합** — orders 상태/positions/realized_pnl_cum이 broker 진실원과 일치.
  - **stuck submitted 0** — 미해소 submitted 0건.
- **And (차단)** 어느 한 증거라도 미충족이면 전면 라이브 승급을 **차단**하고 사유와 함께 운영자에게
  보고한다(REQ-045-C3, SPEC-042 AC-5 보완).
- **And (범위 명시)** 스모크 리포트는 "본 게이트는 **실행 경로** 검증이며 **전략 수익성 검증이 아님**
  (전략 측정은 SPEC-044 + 향후 hybrid 재설계 소관)"을 명시한다(REQ-045-C4).

## 엣지 케이스 (Edge Cases)

- E-1: live 체결조회가 부분체결(partial)을 반환 → `partial` 상태로 정확 매핑, 후속 조회로 `filled` 진행.
- E-2: 3개월 이전 주문 조회 필요 시 `CTSC9115R` 분기(가정 A-1) — 당일 스모크에는 불필요하나 매핑은 정의.
- E-3: 체결조회 도중 TPS 한계 → SPEC-043 페이서가 흡수, watchdog blind 미발생.
- E-4: KIS가 주문번호(ODNO)를 빈 문자열로 반환 → 위조 금지·expired 위임(AC-3 경로).
- E-5: 같은 거래일 동일 종목 재매도 신호(체결 후) → submitted leg 해제로 정당 통과(AC-5 자동 해제).

## Definition of Done

- [ ] M1 6/8 재현 테스트 선행(수정 전 실패 → 수정 후 통과). [HARD]
- [ ] `confirm_fills()` live 분기 = 실제 `inquire-daily-ccld` 체결조회(raise 제거).
- [ ] 미확인 체결 위조 금지 → `order_resolver` expired 수렴 위임.
- [ ] live 체결조회 = `client.get()` 경유(SPEC-043 페이서 존중), 통제되지 않은 호출 0.
- [ ] paper 경로(SPEC-029) byte-불변, order.py/fills.py/sell_lock.py/account.py 미변경.
- [ ] 라이브 멱등성/이중매도 안전(sell_lock submitted leg 자동 해제 포함).
- [ ] 소액 라이브 스모크 게이트 정의 + 판정 로직 + "실행-only" 범위 명시.
- [ ] [확인 필요-1/2/3] 운영자 실측 게이트 충족(live TR_ID·스키마·websocket 가용성).
- [ ] 신규 행위 전부 audit_log 추적.
- [ ] 마이그레이션 필요 여부 확정(불요 시 미사용; 신규 번호는 mig 030 충돌 회피해 run에서 확정).
- [ ] SPEC-044 소유 파일(config.py/backtest/edge/pyproject/일일리포트) 미접촉 확인.

## 품질 게이트

- pytest 커버리지 ≥ 85%, money/risk reproduction-first(M1).
- ruff/black 통과. EARS 추적성(spec ↔ acceptance) 유지.
- **LIVE 임박: AC-6 스모크 게이트 미통과 시 전면 라이브 승급 금지(하드 게이트).**
- 정직성: [확인 필요] 항목 운영자 실측 전 단언 금지.
