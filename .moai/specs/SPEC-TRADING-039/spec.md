---
id: SPEC-TRADING-039
version: 0.1.0
status: draft
created: 2026-06-01
updated: 2026-06-01
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "페이퍼 모드 출구 경로 복원 — 매도 합성 체결(paper synthetic fill) + daily_pnl_pct 실 P&L 교정"
related_specs:
  - SPEC-TRADING-029   # balance-reconcile fill sync — 매수 전용 한계의 원천(Layer 2), 공유 backbone 유지
  - SPEC-TRADING-037   # 출구 규칙 — 본 SPEC 이 출구 "경로"를 복원해야 037 출구 "규칙"이 검증됨
  - SPEC-TRADING-038   # daily-loss 게이트 — env RISK_DAILY_MAX_LOSS=-0.025, daily_pnl_pct 소비처
  - SPEC-TRADING-033   # position_watchdog — 매도 직접 호출 경로(_confirm_qty over-sell 선례)
  - SPEC-TRADING-032   # auto_resume — daily_loss 비자동재개(거짓 halt 의 피해 증폭 맥락)
  - SPEC-TRADING-002   # 실거래 분리 — live 잠금/모드 게이트 원천
---

# SPEC-TRADING-039 — 페이퍼 모드 출구 경로 복원

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-06-01 | 0.1.0 | Initial draft. 라이브 DB + 코드 검증(100% 확정)으로 페이퍼 매도가 영원히 `submitted` 에 멈춤을 규명: (L1) `submit_order` 제출 시 fill 로직 부재, (L2) 유일 fill-sync 경로 `_transition_orders_fifo` 가 `side='buy'` 전용, (L3) KIS 페이퍼가 당일 매도 체결 미보고(SPEC-029 한계 상속). 결과 round-trip 0건 → 출구 미검증·수익성 판단 불가 + `daily_pnl_pct` 가 net 현금흐름을 P&L 로 오인 → 2026-06-01 거짓 −3.34% daily_loss halt(실 EOD +24,283원). 해결: **페이퍼 전용 합성 체결(B안)** + **daily_pnl_pct 실현손익 교정**. 사용자 결정 Q1=live-readiness-conservative(합성은 paper hard gate, reconcile backbone 유지·이중계산 가드·KIS 제출 유지·divergence 로깅), Q2=`inquire-price` 기준가(지정가는 limit_price), Q3=daily_pnl_pct 교정 포함. money/risk 로직 → reproduction-first 필수. — 2026-06-01 | onigunsow |
| 2026-06-01 | 0.2.0 | Implemented (TDD reproduction-first). **Locked decisions:** (1) `daily_pnl_pct` = 체결 기반 **실현손익** (edge/roundtrips FIFO `net_pnl` 의 당일 청산분 합 / initial_capital). 매수 현금유출을 손실로 계산하지 않음(2026-06-01 버그). 미실현 intraday drawdown 은 범위 외(position_watchdog −10% floor, SPEC-037 소유). mode-agnostic 정합 수정. (2) over-sell = **보유분 clamp** — 매도 시 min(요청, 보유), 초과분은 `OVERSELL_CLAMPED` audit, 절대 공매도 안 함, 보유분 출구는 실행. (3) 합성 마커 = 신규 컬럼 `orders.synthetic BOOLEAN NOT NULL DEFAULT FALSE` (mig `029_orders_synthetic_marker.sql`, ADD COLUMN IF NOT EXISTS 멱등). `_transition_orders_fifo` 의 두 WHERE 절에 `AND synthetic = false` 추가 → reconcile 가 합성 체결분 이중계산 방지. 합성 체결은 `submit_order` KIS 성공분기 직후 paper hard gate(`mode==PAPER`) 안에서 실행, live 경로 byte-for-byte 불변. 시장가=`current_price`, 지정가=`limit_price`, 조회 실패/0 → audit `ORDER_SYNTHETIC_SKIPPED` 후 graceful skip. positions 즉시 갱신(매수 가중평균·매도 qty 감소, 0 미만 불가) + reconcile 가 KIS balance 기준으로 재수렴. — 2026-06-01 | onigunsow |

---

## Scope Summary

페이퍼 트레이딩의 **출구 경로(매도 체결 확인)** 를 복원하고, 그로 인해 오염된
**일일 손익 기준** 을 실 P&L 로 교정한다. live 모드는 1줄도 변경 없이 잠금 유지.

- **REQ-039-1 — 페이퍼 합성 체결(매수·매도 대칭)**: 페이퍼 모드 주문을 제출
  시점에 기준가로 `filled` 처리. 매수/매도 동일 메커니즘.
- **REQ-039-2 — 페이퍼 전용 hard gate**: 합성 체결은 `mode==PAPER` 에서만.
  live 에서는 구조적으로 불가능(assert + audit).
- **REQ-039-3 — 기준가 + reconcile 이중계산 가드**: 시장가=`inquire-price`
  현재가, 지정가=`limit_price`. 조회 실패 시 graceful skip(crash 금지).
  reconcile FIFO 는 합성 체결분을 이중 계산하지 않는다.
- **REQ-039-4 — over-sell 가드**: 보유 수량 초과 매도는 reject + audit(절대
  oversell 금지).
- **REQ-039-5 — daily_pnl_pct 실 P&L 교정 + 정직성 로깅**: `daily_pnl_pct` 를
  net 현금흐름이 아닌 실현손익으로 교정(mode-agnostic). 모든 합성 체결과
  positions↔KIS balance divergence 를 audit 로 surface.

Non-Goals: live 합성 체결, 부분 체결 시뮬레이션, slippage/market-impact 모델링,
`inquire-daily-ccld` 페이퍼 재도입.

---

## EARS Requirements

### REQ-039-1 — 페이퍼 합성 체결 (Event-Driven)
**WHEN** 페이퍼 모드에서 주문이 KIS 에 성공적으로 제출되어 `kis_order_no` 를
부여받으면, **THEN** 시스템은 해당 주문을 제출 시점에 `status='filled'`,
`fill_qty=qty`, `fill_price=기준가`, `filled_at=now()` 로 갱신하고 합성 마커를
기록해야 한다. 매수·매도에 **대칭** 적용한다.

### REQ-039-2 — 페이퍼 전용 hard gate (State-Driven + Unwanted)
**IF** `client.mode != PAPER` **THEN** 시스템은 합성 체결을 **수행하지 않아야
한다**. live 경로의 fill 기록은 오직 SPEC-029 balance-reconcile backbone 으로만
이뤄진다. 합성 체결 진입 전 mode 를 assert 하고, 위반 시도 시 audit 후 중단한다.

### REQ-039-3 — 기준가 결정 + reconcile 이중계산 가드 (Event-Driven + Constraint)
**WHEN** 합성 체결이 기준가를 필요로 하면, 시스템은 시장가 주문에 대해
`inquire-price`(FHKST01010100) 현재가를, 지정가 주문에 대해 `limit_price` 를
기준가로 사용해야 한다. **IF** 기준가 조회가 실패하면 **THEN** 합성 체결을 audit
후 skip 하고 reconcile 에 위임하며 **crash 하지 않아야 한다**. reconcile FIFO 는
합성 체결로 이미 `filled/partial` 인 주문을 **이중 전이하지 않아야 한다**(합성
마커 인지).

### REQ-039-4 — over-sell 가드 (Unwanted)
**IF** 매도 수량이 현재 보유 수량을 초과하면 **THEN** 시스템은 그 매도를 reject
하고 audit 해야 하며, 보유를 초과해 매도(oversell)하지 **않아야 한다**.
(2026-06-01 055550: 보유 1 vs 매도 2 사례 — 보수적으로 reject.)

### REQ-039-5 — daily_pnl_pct 실 P&L 교정 + 정직성 로깅 (Ubiquitous + Optional)
시스템은 `daily_pnl_pct` 를 net 거래 현금흐름이 아니라 **당일 실현손익**(fill
기반 FIFO 원가 매칭, `roundtrips` 재사용)으로 **항상** 계산해야 한다. 매수 현금
유출을 손실로 취급하지 않는다. **가능하면**, 모든 합성 체결 이벤트와 로컬
positions ↔ KIS 페이퍼 balance 간 divergence 를 audit_log 로 관측 가능하게
제공한다("페이퍼 체결가 ≠ 실거래 체결가" caveat 포함).

---

## Traceability

| REQ | 대상 파일(예상) | 검증 |
|---|---|---|
| REQ-039-1 | `src/trading/kis/order.py` (synthetic fill layer) | acceptance AC-1 |
| REQ-039-2 | `src/trading/kis/order.py` (mode gate) | acceptance AC-3 |
| REQ-039-3 | `order.py` + `kis/market.py` + `kis/fills.py` | acceptance AC-1, AC-5 |
| REQ-039-4 | `order.py` (over-sell guard, `_confirm_qty` 선례) | acceptance AC-2 |
| REQ-039-5 | `src/trading/risk/limits.py` + `edge/roundtrips.py` | acceptance AC-4 |
| migration | `src/trading/db/migrations/029_*.sql` | plan Phase B |
