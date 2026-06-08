---
id: SPEC-TRADING-042
version: 0.1.0
status: draft
created: 2026-06-08
updated: 2026-06-08
author: onigunsow
priority: critical
issue_number: 0
domain: TRADING
title: "출구 체결 신뢰성 — broker-truth 단일 원장 + 인트라데이 체결 정합 (paper/live 패리티), LIVE 임박 대응"
related_specs:
  - SPEC-TRADING-039   # 페이퍼 합성 체결 — 본 SPEC 이 합성을 broker-truth 기반 좁은 fallback 으로 재구성(진짜 잔여 뿌리)
  - SPEC-TRADING-040   # 출구 정책 — 정책은 정상이나 체결/원장이 실패하면 무의미(본 SPEC 이 토대 복원)
  - SPEC-TRADING-041   # 텔레그램 UX — 매도 실현손익 라인·자산 정합(realized_pnl_cum 정합 대상)
  - SPEC-TRADING-029   # balance-reconcile fill sync — 매수 전용 한계의 원천(인트라데이로 끌어올림)
  - SPEC-TRADING-033   # position_watchdog — 직접 매도 경로(in-flight 락 삽입 지점, _confirm_qty 선례)
  - SPEC-TRADING-038   # daily-loss 게이트 — realized_pnl 소비처(거짓 halt 맥락)
  - SPEC-TRADING-002   # 실거래 분리 — live_unlocked 잠금/모드 게이트 원천(불변 보존)
---

# SPEC-TRADING-042 — 출구 체결 신뢰성: broker-truth 단일 원장

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-06-08 | 0.1.0 | Initial draft. 2026-06-08 폭락일(KOSPI −5.5%, 포트폴리오 −2.9%) 라이브 DB·코드 재검증으로 규명: 시스템이 손절을 **올바르게 결정**했으나 **체결/원장 계층**이 실패 — 000270 −10.8%/033780 −7.3%/071050 −8.1% 손절이 reject("모의투자 잔고내역이 없습니다") 또는 submitted 영구 정체. 근본원인 4가지(RC-1 이중원장 발산·phantom position: `_synthetic_fill` 이 합성 매수로 로컬 positions 부풀림, KIS 잔고엔 없음, 매도는 실주문→reject; reconcile 은 15:59 1회·매수 전용. RC-2 비결정적 매도·submitted 정체: resolver/timeout 부재로 5건 누수. RC-3 매도 in-flight 락 없음: watchdog+persona 둘 다 발사→033780 5분 4회. RC-4 realized_pnl_cum 전 행 NULL). **LIVE 임박(수일 내)** → phantom position = 실거래 손절 실패 = 치명. 사용자 결정: 4방향 동시 전면 개편(A broker-truth 단일원장+인트라데이 reconcile, B order-state resolver/timeout, C 매도 in-flight 락+쿨다운, D realized_pnl 집계). 핵심 ADR=원장 진실원을 broker-poll reconcile 로(local optimistic 원장 아님), paper/live 패리티. paper-first 검증 후 live-readiness 게이트 통과 필수. money/risk → run 단계 reproduction-first TDD. 마이그레이션 031 예약. — 2026-06-08 | onigunsow |

---

## 개요 (Environment & Assumptions)

### Environment
- 페이퍼(모의) 자동매매 운영 중. **수일 내 LIVE 실거래 전환 예정** — 이 마감이 모든 설계를 지배.
- 출구 정책(SPEC-040)·UX(SPEC-041)·경로(SPEC-039)는 갖췄으나, 그 토대인 포지션 원장의
  진실원이 깨져 phantom position 으로 손절 체결이 실패한다(2026-06-08 실증).
- KIS 계좌(paper VTTC8434R / live TTTC8434R)는 `inquire-balance` 로 보유를 보고.
  페이퍼는 당일 매도 체결을 미보고(SPEC-029 Layer 3), 라이브는 체결조회 폴링 가능.

### Assumptions
- KIS 계좌 잔고/체결조회가 포지션의 **권위 있는 진실원** 이며, 로컬 `positions` 는 캐시다.
- 인트라데이 reconcile(매 매도 사이클 전 + 매 주문 후)은 KIS rate limit 내에서 수행 가능하다
  (필요 시 짧은 TTL 캐시/throttle).
- 라이브 체결조회와 페이퍼 balance reconcile 은 **동일 체결-확인 코드 경로** 로 추상화 가능하다.
- 위험 축소 출구(손절·트림·익절)는 정합·락이 추가돼도 막히면 안 된다(capital-preservation 하드룰).
- live 경로는 byte-for-byte 불변이어야 하며, `live_unlocked` 게이트는 유지된다.

---

## 요구사항 (EARS Requirements) — 4방향 → 4모듈

### 모듈 A (REQ-042-A) — broker-truth 단일 원장 + 인트라데이 체결 정합 [방향 A]

- **REQ-042-A1 (Ubiquitous, 진실원):**
  시스템은 KIS 계좌 잔고/체결을 **권위 있는 포지션 진실원** 으로 삼고, 로컬 `positions` 를
  그 캐시로만 취급해야 한다. 매도 결정은 KIS 잔고로 확인된 보유 수량에 대해서만 발생시켜야 한다.

- **REQ-042-A2 (Event-driven, 인트라데이 reconcile):**
  WHEN 매도 결정 사이클이 시작되기 직전, 그리고 WHEN 임의의 주문이 제출된 직후,
  THEN 시스템은 로컬 `positions` 를 KIS 잔고와 정합(reconcile)하여 phantom position 이
  매도 시도를 구동하지 못하게 해야 한다. (현재 15:59 1회 → 인트라데이로 끌어올림.)

- **REQ-042-A3 (Ubiquitous, paper/live 패리티):**
  체결 확인은 paper·live 가 **동일 코드 경로** 를 사용해야 한다 — live 는 KIS 체결조회
  (주식일별주문체결조회) 폴링, paper 는 balance reconcile 로 소스만 분기한다.

- **REQ-042-A4 (State-driven, 좁은 paper fallback):**
  IF 페이퍼 모드이고 KIS 가 당일 매도 체결을 정말로 보고하지 못하는 경우에 한해,
  THEN 시스템은 합성 체결을 **bounded fallback** 으로 적용하되, 즉시 로컬 원장을 갱신하여
  직후 인트라데이 reconcile 과 **drift 가 발생하지 않게** 해야 한다.
  (합성을 broker-semantics 를 미러링하는 paper fill-simulation 으로 대체할 수 있는지 검토.)

- **REQ-042-A5 (Unwanted, live 안전):**
  시스템은 live 경로에서 합성 체결을 수행해서는 안 되며(`mode != PAPER` no-op 유지),
  KIS 잔고로 확인되지 않은 phantom 보유에 대해 매도를 발생시켜서는 안 된다.

### 모듈 B (REQ-042-B) — order-state resolver / timeout [방향 B]

- **REQ-042-B1 (State-driven, resolver):**
  IF 주문이 bounded window 를 초과해 `submitted` 에 머무르면,
  THEN 시스템은 KIS 체결 상태를 폴링하여 `filled` 로 표시하거나, 취소 후
  `cancelled`/`expired` 로 표시하여 **반드시 해소** 해야 한다.

- **REQ-042-B2 (Ubiquitous, 일회성 cleanup):**
  시스템은 현재 누수된 5건(086790/055550/064350/000270/071050, 2026-06-01 이후 submitted)을
  일회성으로 해소하는 cleanup 경로를 제공해야 한다.

- **REQ-042-B3 (Unwanted):**
  resolver 는 이미 체결/취소된 주문을 이중 전이해서는 안 되며(합성 마커·KIS 체결 상태 인지),
  실거래 미체결 주문을 임의로 filled 처리해서는 안 된다(KIS 상태 확인 기반).

### 모듈 C (REQ-042-C) — 매도 in-flight 락 + 쿨다운 [방향 C]

- **REQ-042-C1 (State-driven, in-flight 락):**
  IF 특정 종목의 매도가 pending/in-flight(미해소 submitted 또는 직전 발사 후 쿨다운 내)이면,
  THEN 시스템은 watchdog·persona 양쪽에서 같은 종목 매도 재결정을 억제하여 중복 손절 발사를
  방지해야 한다(033780 5분 4회 재발 방지).

- **REQ-042-C2 (Unwanted, 신규 시그널 보존):**
  in-flight 락·쿨다운은 직전 매도가 **해소된 뒤** 발생하는 *진짜 신규* 출구 시그널을
  막아서는 안 된다. (자본 보존 출구를 영구 차단하지 않는다.)

- **REQ-042-C3 (Ubiquitous, 멱등):**
  in-flight 락은 재시작에 견디도록 `position_action_markers` 패턴을 재사용하여 멱등하게
  관리해야 한다.

### 모듈 D (REQ-042-D) — 실현 P&L 집계 [방향 D]

- **REQ-042-D1 (Ubiquitous):**
  시스템은 확인된 매도 체결로부터 `daily_equity_snapshot.realized_pnl_cum` 을 (수수료 차감)
  집계·영속화해야 한다(현재 전 행 NULL).

- **REQ-042-D2 (State-driven, 정합):**
  IF realized_pnl_cum 이 갱신되면, THEN 헤드라인 자산(SPEC-041 D+2 basis)과 정합되어야 하며,
  net 현금흐름을 실현손익으로 오인하지 않아야 한다(SPEC-039 daily_pnl_pct 교정과 정합).

- **REQ-042-D3 (Trackable):**
  모든 신규 정합/resolver/락/집계 행위는 audit_log 로 추적 가능해야 한다.

---

## 사양 (Specifications)

- 진실원: KIS `inquire-balance`(보유) + 체결조회(live 체결). 로컬 `positions` 는 캐시·재수렴.
- 인트라데이 reconcile 빈도/캐시 TTL: run 단계 확정(KIS rate limit 내, Q-1).
- 합성 체결: 좁은 paper fallback 또는 fill-simulation 으로 재구성, drift 0(Q-2). live 불가 유지.
- submitted 해소 window·취소 TR: run 단계 확정(Q-3).
- in-flight 락 범위·쿨다운: 종목당 1매도 in-flight + 쿨다운, 마커 기반(Q-4).
- realized_pnl_cum 백필: round-trip 청산 증분 또는 일배치(Q-5). 헤드라인 자산 정합.
- 마이그레이션: 신규 컬럼 필요 시 **031** (현재 최신 029; 027 결번, 030 은 SPEC-040 예약·미사용).

## Traceability

| REQ | 방향 | 재사용 자산 | 검증(acceptance) |
|---|---|---|---|
| REQ-042-A1~A5 | A 단일원장 | account.balance, fills.reconcile_from_balance, order._synthetic_fill, _held_qty | AC-1, AC-5 |
| REQ-042-B1~B3 | B resolver | order.submit_order, fills, orders.status | AC-2 |
| REQ-042-C1~C3 | C in-flight 락 | watchdog poll/kis_sell, position_action_markers | AC-3 |
| REQ-042-D1~D3 | D 실현 P&L | edge/roundtrips, daily_equity_snapshot(mig 026), SPEC-041 자산 정합 | AC-4 |
| 공통 | live 안전·정직성 | live_unlocked 게이트, audit_log | AC-1~5 공통 |
