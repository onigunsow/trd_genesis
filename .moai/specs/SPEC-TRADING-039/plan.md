# SPEC-TRADING-039 — Implementation Plan

> 방법론: **TDD** (money/risk 로직, [HARD] MoAI reproduction-first). run 단계는
> RED → GREEN → REFACTOR. 모든 새 함수는 paper/live 모드 분기 테스트 필수.

---

## 우선순위 / 마일스톤 (시간 추정 없음)

### Phase A (Primary Goal) — 재현 테스트 + 기준가 배선
1. **재현 테스트(RED)**: 2026-06-01 시나리오를 ScriptedCursor
   (`tests/kis/test_fills_balance_reconcile.py` 패턴) 로 재현.
   - 086790/055550 매도가 `submitted` 에서 안 벗어남 → fail.
   - 매수 2건 현금유출만으로 `daily_pnl_pct` 가 −3.34% → 거짓 daily_loss → fail.
2. **기준가 배선**: `kis/market.py:current_price` 를 합성 체결이 호출할 수
   있도록 thin accessor 확인. 조회 실패 graceful skip 경로 테스트.

### Phase B (Secondary Goal) — 합성 체결 엔진 + reconcile 가드 + over-sell
3. **합성 체결 레이어**: `submit_order` 성공 분기(order.py L131–136) 직후 삽입.
   - 페이퍼 hard gate(REQ-039-2): `mode==PAPER` assert.
   - 기준가(REQ-039-3): market→`inquire-price`, limit→`limit_price`.
   - over-sell 가드(REQ-039-4): 매도 시 `balance()` 보유 재확인, 초과 reject+audit.
   - `status='filled'`, `fill_qty`, `fill_price`, `filled_at=now()`, `source`
     마커 UPDATE + `ORDER_FILLED_SYNTHETIC` audit (fills.py L136–160 패턴 모방).
4. **마이그레이션 029**: `orders.synthetic BOOLEAN DEFAULT FALSE` (026/028 하우스
   스타일, IF NOT EXISTS + schema_migrations ON CONFLICT 멱등).
5. **reconcile 이중계산 가드**: `_transition_orders_fifo` 가 합성 체결분
   (`synthetic=TRUE` 또는 이미 filled) 을 이중 전이하지 않도록 보호.

### Phase C (Final Goal) — daily_pnl_pct 교정 + 통합 + 배포 검증
6. **daily_pnl_pct 교정**: `limits.py:daily_pnl_pct` 를 net 현금흐름 →
   당일 실현손익(`roundtrips.build_roundtrips` 의 `net_pnl` 합 / initial_capital)
   으로 교체. mode-agnostic. 매수 현금유출이 손실로 안 잡히는지 회귀.
7. **divergence 로깅(REQ-039-5)**: positions ↔ KIS balance 차이 audit.
8. **통합 + 배포 검증**: 전체 스위트 통과(베이스라인 회귀 0, 85%+), redeploy,
   live smoke(페이퍼 매도 1건이 `filled` 도달 + daily_pnl 정상 관측).

---

## 기술 접근

- 합성 체결을 `submit_order` **내부** 에 두어 `_execute_signal`(orchestrator),
  late_cycle, position_watchdog 의 모든 매도 경로가 자동 수혜(단일 chokepoint).
- live 경로 무변경: `mode==PAPER` 가드가 우회. live 는 reconcile backbone 만.
- positions 미러는 reconcile 에 위임(합성 체결 경로에서 이중 쓰기 금지).

## 마이그레이션 계획

- 다음 번호 **029** (027=SPEC-037 예약·미생성, 028=적용됨 — `ls migrations/` 확인).
- `029_orders_synthetic_marker.sql`: `ALTER TABLE orders ADD COLUMN IF NOT
  EXISTS synthetic BOOLEAN NOT NULL DEFAULT FALSE` + 주석 + schema_migrations.
- 멱등(재실행 안전), 026/028 스타일 준수.

## 리스크 분석

| 리스크 | 완화 |
|---|---|
| 합성 체결이 live 로 새어나감 | mode hard gate assert + audit, 테스트로 live 불변 검증(AC-3) |
| reconcile 이중계산 | synthetic 마커 인지 + buy 전용 accounted 집계 자연 제외 |
| 기준가 조회 실패 시 crash | try/except graceful skip → reconcile 위임 |
| over-sell | 매도 직전 balance 보유 재확인 reject(_confirm_qty 선례) |
| daily_pnl 교정 회귀 | roundtrips 순수함수 재사용, 당일 청산분만 합산 회귀 테스트 |
| 페이퍼 체결가 ≠ 실거래 | scorecard 톤 caveat 로깅, slippage 없음 명시 |

## MX 태그 대상

- `submit_order` (order.py): fan_in ≥ 3 (orchestrator/late_cycle/watchdog) →
  **@MX:ANCHOR** 후보. 합성 체결 레이어 추가 시 invariant(페이퍼 게이트) 명시.
- `reconcile_from_balance` (fills.py): 기존 @MX:ANCHOR 유지 — 이중계산 가드
  추가 시 @MX:REASON 갱신.
- `daily_pnl_pct` (limits.py): fan_in(check_pre_order + 프롬프트) → **@MX:ANCHOR**
  후보(거래 한도 invariant: 실현손익 기준).

## TDD 노트

money/risk 로직이므로 [HARD] reproduction-first: AC-1(매도 체결)·AC-4(daily_pnl)
재현 테스트를 먼저 작성·실패 확인 후 최소 수정으로 GREEN. ScriptedCursor 패턴
(`tests/kis/test_fills_balance_reconcile.py`) + roundtrips 순수함수 단위 테스트.
