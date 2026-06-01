# SPEC-TRADING-039 — Research: 페이퍼 모드 출구 경로 복원

> 분석 일자: 2026-06-01 · 작성자: onigunsow · 상태: research (plan 단계)
> 본 문서는 구현이 아니라 **데이터 흐름 분석 + 근본 원인 검증 + ADR** 이다.

---

## 1. 문제 요약 (라이브 DB + 코드 검증, 100% 확정)

페이퍼 트레이딩에서 **SELL 주문이 영원히 `status='submitted'` 에 멈춰 체결되지 않는다.**

라이브 Postgres `orders` 테이블 최근 21일: buy filled 23 / rejected 4,
**sell submitted 2 / filled 0**. 2026-06-01 두 매도
(086790 qty 3 @09:34, 055550 qty 2 @09:35) 모두 `status='submitted'`,
`fill_qty=NULL` 로 잔류.

영향:
1. **출구 경로 미검증** — 완결된 round-trip 0건 → 수익성 판단 불가
   ([[edge-validation]] 감사의 치명적 발견과 동일 사안).
2. **`daily_pnl_pct` 가 net 거래 현금흐름을 P&L 로 오인** → 2026-06-01 09:47
   `daily_loss: -3.34%` halt 은 순전히 기아 매수 2건의 현금 유출
   (−(167866+168675)/10,074,006 = −3.34%) 이었고 실 EOD P&L 은 **+24,283원
   (양수)** 였다. daily_loss halt 은 **비자동재개**([[spec-032-status]]) → 거짓
   양성 하나로 하루 종일 거래 정지.

---

## 2. 3-Layer 근본 원인 (file:line 인용)

### Layer 1 — 제출 시점 체결 로직 부재
`src/trading/kis/order.py:submit_order` (L48–210).
- KIS 성공(`rt_cd=="0"`) 시 `new_status = "submitted"` (L136) 로만 설정.
- 매수/매도 **어느 쪽도** 제출 시점에 fill 로직이 없다. fill 은 별도
  reconcile 에서만 발생.
- `kis_order_no` 정규화(L133–135), audit(`ORDER_SUBMITTED`/`ORDER_REJECTED`,
  L149–186) 까지만. `fill_qty`/`fill_price`/`filled_at` 는 NULL 로 남는다.
- live_unlocked 게이트 `_check_live_gate` (L30–45) 가 이미 모드 분기점으로
  존재 — 합성 체결 게이트가 붙을 동일 함수 내 자연스러운 위치.

### Layer 2 — 유일한 fill-sync 경로가 매수 전용
`src/trading/kis/fills.py:_transition_orders_fifo` (L57–162).
- `WHERE ticker=%s AND side='buy'` (L80–84 의 accounted 집계, L97–98 의 open
  orders 조회) — **`side='sell'` 은 파일 전체에 한 번도 등장하지 않음**.
- `inquire-balance` 의 ticker 누적 보유량에서 fill 을 역추론:
  `newly_filled = max(0, held_qty - already_accounted)` (L90).
- 매수는 보유량을 **증가**시켜 감지 가능, 매도는 보유량을 **감소**시켜 이
  방법으로 감지 불가. 보유량 **감소분을 열린 SELL 주문에 귀속**하는 로직이
  전무 → 매도는 `submitted` 를 벗어날 코드 경로가 아예 없다.
- `_mirror_positions` (L170–254): held ticker 를 UPSERT, balance 에서 사라진
  종목은 `qty=0` 으로 zero-out (DELETE 안 함, ADR-029-4). 합성 체결 경로는 이
  positions 미러를 **이중 적용하지 말아야** 한다(reconcile 가 이미 처리).

### Layer 3 — 데이터 소스의 페이퍼 한계 (역사적 맥락)
[[kis-paper-endpoints]] 참조. SPEC-029 v0.1.0 은 `inquire-daily-ccld`
(VTTC8001R) 사용 → 매도 체결을 보고하나, **KIS 페이퍼가 당일 체결에 빈 응답**
(2026-05-28 검증: msg_cd 70070000, output1=[]). v0.2.0 이 balance-reconcile
로 전환하며 매수 전용 한계를 상속 (fills.py L1–21 헤더에 명시).

**결론**: 페이퍼에서 매도 체결을 알 수 있는 신뢰 가능한 KIS 조회 경로가 없다.
→ 제출 시점 **합성 체결(synthetic fill)** 이 유일한 페이퍼 출구 복원 수단.

---

## 3. `daily_pnl_pct` 오류 메커니즘

`src/trading/risk/limits.py:daily_pnl_pct` (L55–80).
```
buy  filled/partial → -fill_price * fill_qty   (현금 유출)
sell filled/partial → +fill_price * fill_qty   (현금 유입)
```
이는 P&L 이 아니라 **net 거래 현금흐름(net trade cash flow)** 이다. 매도가
`filled` 에 영영 도달하지 못하므로(Layer 2) sell 분기는 영구히 0 기여 → 순매수
일은 항상 손실로 보인다. `check_pre_order` (L83–149) 가 L119–121 에서 이 값을
`RISK_DAILY_MAX_LOSS`(현재 −0.025, [[spec-038-status]] 반영) 와 비교 → 거짓
breach → halt.

올바른 실 P&L 정의(두 안):
- (A) **실현손익 from fills**: `roundtrips.build_roundtrips` 의 FIFO 원가
  매칭(`src/trading/edge/roundtrips.py` L127–201) 의 `net_pnl` 합 — 이미
  존재하는 순수 함수. 당일 청산분만 합산하면 실현 P&L.
- (B) **KIS balance delta**: `account.balance()` 의 `pnl_total`
  (`evlu_pfls_smtl_amt`, account.py L84) — 미실현 포함 평가손익.

본 SPEC 권고: **(A) 실현손익 우선**(거래 한도는 "오늘 실현한 손실"이 기준이어야
거짓 차단 방지), 합성 체결로 sell 이 filled 되면 (A) 가 자연히 정상 작동.
mode-agnostic 정합성 수정이므로 live 에서도 보수적으로 옳다.

---

## 4. 참조 구현 — 합성 체결이 모방해야 할 쓰기 패턴

`_transition_orders_fifo` 가 fill 을 기록하는 방식 (fills.py L136–160):
```
UPDATE orders SET status, fill_qty, fill_price, filled_at = now() WHERE id
+ audit ORDER_FILLED / ORDER_PARTIAL (source 필드 포함)
```
합성 체결은 **동일 컬럼 집합**을 쓰되 `source='paper_synthetic'`,
`filled_at=now()` 로 표시. positions 미러는 reconcile 에 위임(이중 쓰기 금지).

기준가 조회: `src/trading/kis/market.py:current_price` (L10–53).
- 엔드포인트 `inquire-price`, tr_id `FHKST01010100` (paper/live 동일).
- 반환 `price`(stck_prpr) = 현재가. 시장가 주문의 기준가로 사용.
- 실패 시 `KisError` raise → 합성 체결은 try/except 로 감싸 graceful skip.

over-sell 가드 선례: `src/trading/watchers/position_watchdog.py:_confirm_qty`
(L101–110) — 매도 직전 `balance()` 로 live qty 재확인. 본 SPEC 의 over-sell
가드는 이 패턴을 합성 체결 경로에도 적용(보유 초과 매도는 reject + audit).

호출 흐름: `orchestrator._execute_signal` (L872–897) → `kis_buy/kis_sell` →
`submit_order`. late_cycle(L41) · position_watchdog(L38) 도 `kis_sell` 직접
호출 → 합성 체결을 `submit_order` 안에 두면 **모든 매도 경로가 자동 수혜**.

---

## 5. 중심 ADR — Source-of-Truth 설계 (live-readiness-conservative)

사용자 결정(Q1): "실거래 할거기 때문에 좀더 보수적으로 접근해라."

### 선택안: Hybrid (보수 가드형 합성 체결)
- **합성 체결은 페이퍼 전용 hard gate**: `client.mode == PAPER` 일 때만. live
  에서는 구조적으로 불가능(assert + audit; mode != paper 면 절대 합성 안 함).
- **SPEC-029 balance-reconcile 를 공유 fill-recording backbone 으로 유지**:
  live·paper 가 구조를 공유 → 페이퍼 검증이 live 신뢰로 전이. 합성 체결은
  reconcile 를 **대체하지 않고** 페이퍼 전용 **추가 레이어**.
- **silent divergence 금지**: 모든 합성 체결은 audit_log 이벤트 발행
  (`ORDER_FILLED_SYNTHETIC`). reconcile FIFO 는 합성 체결분을 **이중 계산하지
  않도록** 가드(이미 filled 인 주문은 accounted 에 포함되어 자연 제외되나,
  `source` 마커로 명시 보호).
- **KIS 페이퍼 제출은 유지**: 실제 브로커 왕복은 그대로 수행(`submit_order` 의
  KIS POST 유지) → 합성 체결은 그 위에 로컬 fill 상태/positions 만 갱신.
- **reconciliation honesty**: 로컬 positions vs KIS 페이퍼 balance divergence
  를 로그/관측(합성 체결임을 항상 인지).

### 기각안
- **순수 local-positions-source-of-truth**: 로컬을 진실로 삼고 KIS balance 무시.
  → 기각: live 전환 시 reconcile backbone 이 없어 검증 전이 불가, KIS 와의
  drift 를 영영 못 봄. 보수성 위배.
- **balance-only sell FIFO** (보유 감소분을 열린 SELL 에 귀속): paper balance
  가 매도 직후 즉시 갱신되는지 불확실(D+2 정산 타이밍, account.py L67–73 의
  tot_evlu_amt nuance), 부분 체결·동일 종목 매수/매도 혼재 시 귀속 모호. →
  기각: 신뢰 불가 + 복잡. 합성 체결이 결정론적.

### 정직성 caveat
페이퍼 합성 체결은 slippage·market-impact 가 없다("페이퍼 체결가 ≠ 실거래
체결가"). `src/trading/edge/scorecard.py` 의 경고 톤과 일관되게 surface 할 것.

---

## 6. 리스크 / 암묵적 계약

- `orders.status` CHECK 제약(002 L16–17)에 `'filled'/'partial'` 이미 포함 →
  스키마 변경 불필요. 단 합성 여부 식별 위해 마커 필요.
- **마이그레이션 029** 권고: orders 에 `synthetic BOOLEAN DEFAULT FALSE` 컬럼
  추가(또는 audit-only). 027 은 SPEC-037 예약(디스크 미생성), 028 적용됨 → **029
  채택**. 026/028 하우스 스타일(IF NOT EXISTS + schema_migrations ON CONFLICT)
  멱등.
- reconcile 의 `already_accounted` 는 buy 전용 집계라 sell 합성 체결과 무충돌.
  단 positions 미러가 합성 체결로 줄어든 보유를 KIS balance 와 맞추는지 검증
  필요(divergence 로깅으로 관측).
- 부분 체결: 시장가 페이퍼는 전량 즉시 체결 가정이 자연. 합성 체결은 전량 fill.
- `_execute_signal` 의 try/except (L893–897) 가 이미 EXEC_FAILED audit → 합성
  체결 실패도 동일 안전망.

---

## 7. 권고 접근

1. `submit_order` 성공 분기(order.py L131–136) 직후 **페이퍼+시장가/지정가**
   합성 체결 레이어 삽입: `mode==PAPER` 가드 → 기준가 조회(market/limit) →
   over-sell 가드 → `status='filled'`, `fill_qty=qty`, `fill_price=ref`,
   `filled_at=now()`, `source` 마커 → audit `ORDER_FILLED_SYNTHETIC`.
2. live 경로는 1줄도 안 건드림(가드로 우회).
3. `daily_pnl_pct` 를 실현손익(roundtrips 기반) 으로 교정 — mode-agnostic.
4. reconcile FIFO 에 합성 체결분 이중계산 방지 가드(`source` 마커 인지).
5. reproduction-first: 2026-06-01 시나리오를 RED 테스트로 먼저 재현.

모듈 ≤ 5 (EARS), acceptance ≥ 2 G/W/T (재현 + over-sell edge + live 불변 +
daily_pnl 정합).
