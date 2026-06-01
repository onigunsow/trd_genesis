# SPEC-TRADING-039 — Acceptance Criteria

> Given-When-Then. [HARD] money/risk → reproduction-first: AC-1·AC-4 는 수정 전
> 반드시 실패해야 한다(RED). 모든 시나리오는 ScriptedCursor + 모의 KisClient 로
> DB 없이 단위화하거나 통합 픽스처로 검증.

---

## AC-1 — 페이퍼 매도 체결 복원 (REQ-039-1, REQ-039-3) [재현 테스트]

```gherkin
Given 페이퍼 모드 KisClient 이고 086790 을 3주, 055550 을 2주 보유 중
  And inquire-price 가 086790=55,900 / 055550=84,000 현재가를 반환
When 시스템이 086790 매도 3주, 055550 매도 2주(시장가)를 submit_order 로 제출
Then 두 주문 모두 status='filled', fill_qty=주문수량,
     fill_price=inquire-price 현재가, filled_at != NULL, synthetic=TRUE 이고
  And 각 주문에 ORDER_FILLED_SYNTHETIC audit_log 가 1건씩 기록되며
  And roundtrips.build_roundtrips 가 두 매도를 매수 로트와 매칭해
     완결 round-trip 으로 집계한다
# 수정 전(RED): 두 주문이 status='submitted', fill_qty=NULL 로 잔류 → fail.
```

## AC-2 — over-sell 가드 (REQ-039-4) [edge case, 2026-06-01 재현]

```gherkin
Given 페이퍼 모드이고 055550 을 1주만 보유
When 시스템이 055550 매도 2주를 제출
Then 매도는 reject(또는 보유분 1주로 clamp 후 초과 1주 reject)되고
  And LIMIT_BREACH/OVER_SELL_REJECTED audit 가 기록되며
  And 어떤 경우에도 보유(1주)를 초과한 체결(2주)이 발생하지 않는다
```

## AC-3 — live 모드 불변 (REQ-039-2) [assertion]

```gherkin
Given live 모드 KisClient (live_unlocked 여부 무관)
When 합성 체결 진입 조건을 평가
Then 합성 체결은 절대 수행되지 않고(status 변경 없음, synthetic=FALSE 유지)
  And live 의 fill 기록 경로는 오직 SPEC-029 reconcile_from_balance 뿐이며
  And submit_order 의 live 경로 동작이 SPEC-039 적용 전과 byte-for-byte 동일하다
# (필요 시) mode!=PAPER 에서 합성 체결 함수 직접 호출 시 audit 후 no-op/assert.
```

## AC-4 — daily_pnl_pct 실 P&L 정합 (REQ-039-5) [재현 테스트]

```gherkin
Given 오늘 기아(000270) 매수 2건이 체결(현금유출 167,866 + 168,675)되고
  And 당일 청산(매도)된 포지션의 실현손익 합이 +24,283원이며
  And initial_capital = 10,074,006
When daily_pnl_pct(initial_capital) 를 호출
Then 반환값은 net 현금흐름 기반 -3.34% 가 아니라
     실현손익 기반 약 +0.24%(+24,283/10,074,006) 이고
  And check_pre_order 의 daily_loss 분기가 RISK_DAILY_MAX_LOSS(-2.5%) 를
     breach 하지 않아 halt 가 트립되지 않는다
# 수정 전(RED): 매수 현금유출만 합산 → -3.34% → 거짓 daily_loss breach → fail.
```

## AC-5 — 기준가 조회 실패 graceful (REQ-039-3) [edge case]

```gherkin
Given 페이퍼 모드 시장가 매수이고 inquire-price 가 KisError 를 raise
When submit_order 가 합성 체결을 시도
Then 합성 체결은 audit 후 skip 되고(주문은 KIS 에 정상 제출된 채 submitted 유지)
  And 예외가 호출자로 전파되지 않으며(crash 없음)
  And 이후 reconcile_from_balance 가 해당 주문의 fill 처리를 위임받는다
```

---

## Definition of Done

- [ ] 위 5개 AC 전부 통과(AC-1·AC-4 는 RED→GREEN 순서 입증).
- [ ] 전체 테스트 스위트 베이스라인 회귀 0, 커버리지 85%+.
- [ ] live 경로 무변경 확인(AC-3).
- [ ] 마이그레이션 029 멱등 적용(재실행 안전).
- [ ] redeploy 후 live smoke: 페이퍼 매도 1건이 `filled` 도달 + daily_pnl 양수일
      양수 관측 + ORDER_FILLED_SYNTHETIC audit 확인.
- [ ] "페이퍼 체결가 ≠ 실거래 체결가" caveat 로깅 확인.
