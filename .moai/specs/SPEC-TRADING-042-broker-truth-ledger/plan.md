# SPEC-TRADING-042 — Implementation Plan

> 코드 미작성. 본 문서는 run 단계 실행 계획·ADR·리스크 정리.
> **LIVE 실거래 임박(수일 내)** 이 모든 판단을 지배한다.

## 중심 ADR (Architecture Decision Records)

### ADR-1 (KEY): 포지션 원장 진실원 — broker-poll reconciliation vs local optimistic ledger
- **결정:** 포지션의 진실원은 **KIS 계좌(broker-poll reconciliation)** 로 한다. 로컬 `positions`
  는 캐시일 뿐이며 인트라데이로 KIS 에 재수렴한다. local optimistic ledger(합성 매수가
  로컬 행을 권위 있게 생성)는 폐기한다.
- **이유:** LIVE 임박. 라이브에서 phantom position 은 실거래 손절 실패 = 치명적 자본 손실.
  paper-synthetic-centric 설계는 라이브에서 안전하지 않다. paper/live 가 동일하게 행동하려면
  진실원이 브로커여야 한다.
- **trade-offs (명시):**
  - *correctness vs latency/rate-limit:* broker-poll 은 KIS 왕복 지연·rate limit 비용이 있다.
    → 인트라데이 reconcile 을 **매 매도 사이클 직전 + 매 주문 직후** 로 한정하고, 필요 시
    짧은 TTL 캐시(예: 30~60s)로 호출을 throttle. 빈도는 run 단계 측정으로 보정.
  - *local optimistic 의 장점(즉시성)* 은 포기하나, 즉시성은 phantom 위험과 맞바꿀 수 없다.
- **KIS 페이퍼 한계 처리(SPEC-039 동기):** KIS 페이퍼는 당일 매도 체결을 미보고
  (SPEC-029 Layer 3). 이를 **broker-truth 위에서** 처리한다 — 합성 체결은 *좁은 paper
  fallback*(REQ-042-A4)으로만 남기고, 합성 직후 즉시 로컬 원장을 갱신해 직후 reconcile 과
  drift 0 을 보장. 라이브는 체결조회 폴링으로 이 fallback 자체가 불필요.

### ADR-2: 체결 확인 paper/live 패리티 — 소스만 분기, 코드 경로 단일
- **결정:** 체결 확인 로직을 단일 경로로 추상화하고, live=KIS 체결조회·paper=balance reconcile
  로 소스만 분기. 합성은 paper 전용 fallback 으로 격리.
- **이유:** 라이브 전환 전 paper 에서 검증한 동일 경로가 라이브에서도 동작해야 신뢰 가능.
  경로가 갈리면 paper 통과가 live 안전을 보장하지 못한다.
- **영향:** SPEC-039 의 `mode != PAPER` no-op 게이트(order.py L109)는 유지(live 합성 불가).

### ADR-3: order-state resolver/timeout 도입 — 결정성 보장
- **결정:** submitted 가 bounded window 초과 시 KIS 상태 폴 → filled, 또는 cancel→cancelled/
  expired. 누수 5건은 일회성 cleanup.
- **이유:** 현재 resolver/timeout 부재로 submitted 영구 정체(071050, 5건 누수). 미해소 주문은
  in-flight 락(C)을 영구 잠가 신규 출구까지 막는 2차 피해를 낳는다.
- **영향:** `submit_order` 가 합성 단계 throw 시에도 주문을 결정 상태로 수렴시킨다.

### ADR-4: 매도 in-flight 락은 마커 기반(코드 강제), 신규 시그널 보존
- **결정:** 종목당 매도 in-flight 동안 watchdog·persona 양쪽 재결정 억제. 해소·쿨다운 후
  진짜 신규 시그널은 통과. `position_action_markers` 재사용(멱등·재시작 견딤).
- **이유:** watchdog(*/5)와 persona orchestrator 가 둘 다 발사 → 033780 5분 4회. 락은
  중복 발사만 막고 자본 보존 출구는 막지 않아야 한다(REQ-042-C2).

### ADR-5: paper-first, live byte-for-byte 불변, reproduction-first
- **결정:** money/risk 로직(원장·resolver·락·집계)은 reproduction-first TDD. live 경로 불변,
  `live_unlocked` 게이트 미변경.
- **이유:** SPEC-038/039 거짓 halt 선례 — money 로직은 재현 테스트 선행이 안전. 라이브 임박
  상황에서 회귀는 실거래 손실로 직결.

## 마일스톤 (우선순위 기반, 시간 추정 없음)

- **Primary Goal:** REQ-042-A1~A5 broker-truth 단일 원장 + 인트라데이 reconcile.
  → phantom position 제거(2026-06-08 reject 재발 방지). **live-readiness 의 핵심 게이트.**
- **Secondary Goal:** REQ-042-B1~B3 order-state resolver/timeout + 5건 cleanup.
  → submitted 영구 정체 제거(결정성).
- **Tertiary Goal:** REQ-042-C1~C3 매도 in-flight 락 + 쿨다운.
  → 중복 손절 발사 제거(033780 재발 방지).
- **Final Goal:** REQ-042-D1~D3 realized_pnl_cum 집계·영속화 + 자산 정합.
- **횡단:** REQ-042-D3 audit + 정직성(paper 체결가 ≠ 실거래 caveat 유지).

## 기술 접근

1. **단일 원장:** `positions` 를 캐시로 명시. 매도 결정 직전 `reconcile_from_balance` 호출
   (인트라데이) → KIS 잔고 기준 보유로 재수렴. `_synthetic_fill` 의 로컬 행 생성은 fallback
   으로 격리하고 직후 reconcile 과 정합. `fills._transition_orders_fifo` 의 buy 전용 제약을
   체결-확인 추상화 안에서 재구성(매도 확인 경로 추가).
2. **패리티:** 체결 확인 함수 = (live) 체결조회 폴 / (paper) balance reconcile. 동일 시그니처.
3. **resolver:** submitted window 초과분 폴 → KIS 상태 매핑(filled/cancelled/expired). cleanup
   스크립트(5건).
4. **in-flight 락:** watchdog `poll_position_watchdog`·persona 매도 경로에 종목당 락 체크 삽입,
   `position_action_markers`(action='sell_inflight' 등) 멱등 마커 + 쿨다운.
5. **realized P&L:** round-trip 청산 시 `edge/roundtrips` net_pnl(수수료 차감) →
   `daily_equity_snapshot.realized_pnl_cum` 백필, 헤드라인 자산(SPEC-041 D+2) 정합.

## 리스크 및 대응

| 리스크 | 대응 |
|---|---|
| **(LIVE) phantom 잔존 → 실거래 손절 실패** | broker-truth 진실원 + 매 매도 사이클 전 reconcile(REQ-042-A1/A2), live-readiness 게이트 PAPER 통과 필수 |
| 인트라데이 reconcile KIS rate limit 초과 | 매도 사이클 전 + 주문 후로 한정 + TTL 캐시/throttle(ADR-1 trade-off), 빈도 run 단계 측정 보정 |
| 합성 fallback 이 다시 drift 유발 | 합성 직후 즉시 원장 갱신 + 직후 reconcile 정합(REQ-042-A4), drift audit 로깅 |
| resolver 가 미체결을 임의 filled 처리 | KIS 상태 확인 기반만 전이(REQ-042-B3), 합성 마커·체결 상태 이중계산 가드 |
| in-flight 락이 진짜 신규 출구 영구 차단 | 해소·쿨다운 후 통과(REQ-042-C2), resolver(B)가 submitted 를 반드시 해소해 락 영구화 방지 |
| over-sell/공매도 | KIS 확인 보유 clamp(SPEC-039/033 선례, REQ-042-A5) |
| live 경로 회귀 | live byte-for-byte 불변, `mode != PAPER` no-op 유지, reproduction-first(ADR-5) |
| realized_pnl 이중계산 | round-trip FIFO 단일 소스, synthetic 마커 인지(REQ-042-D1) |

## 마이그레이션

- 잠정: `realized_pnl_cum` 은 mig 026 기존 컬럼 재사용(백필만). in-flight 락은
  `position_action_markers` 재사용 가능성 높음 → **마이그레이션 불필요 가능**.
- 신규 컬럼/상태 확정 시에만 **031** 사용(현재 최신 029, 027 결번, 030=SPEC-040 예약·미사용).
- run 단계 첫 작업: `orders.status` enum(cancelled/expired 수용 여부)·`position_action_markers`
  스키마 확인 → resolver·락 마커 수용 가능 여부 결정.

## reproduction-first 대상(money/risk — RC-1~4 각각)
- **RC-1 재현:** 합성 매수로 로컬 positions 부풀린 상태에서 매도 시도 → 실 KIS reject
  "잔고내역이 없습니다"(000270 2026-06-08 재현) → 인트라데이 reconcile 도입 후 통과 (AC-1).
- **RC-2 재현:** 합성 단계 throw 시 submitted 영구 정체 → resolver 도입 후 filled/cancelled
  로 해소(071050 재현) (AC-2).
- **RC-3 재현:** watchdog+persona 동일 종목 5분 4회 매도 발사(033780 재현) → 락 도입 후 1회 (AC-3).
- **RC-4 재현:** round-trip 완성에도 realized_pnl_cum NULL → 백필 후 채워지고 자산 정합 (AC-4).
