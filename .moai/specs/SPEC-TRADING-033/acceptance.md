# SPEC-TRADING-033 — Acceptance Criteria

자동 손절/익절 포지션 워치독에 대한 구체적·검증 가능한 인수 시나리오.
모든 시나리오는 Given/When/Then 형식이며, 1회 폴은 `poll_position_watchdog()` 호출로 모사한다.

가정/약어:
- "보유" = KIS `balance()` 가 반환한 holdings 항목(`ticker`, `qty`, `pnl_pct`, ...). 테스트에서는
  `balance()` 와 `get_dynamic_thresholds()` 를 fake/주입하여 보유·임계를 구성한다.
- "손절" = `kis_sell(client, ticker=, qty=<전량>, ...)`. "익절" = `kis_sell(..., qty=max(1, qty//2))`.
  `kis_sell` 은 mock 으로 호출 횟수·인자(ticker/qty) 검증.
- `effective_stop` 은 음수 %(예 -8.5), `effective_take` 는 양수 %(예 +12).
- "자동 손절 알림" = `system_briefing("자동 손절", ...)`, "자동 익절 알림" =
  `system_briefing("자동 익절", ...)`. 텔레그램 send 는 mock 으로 카테고리/횟수 검증.
- "감사" = `audit("POSITION_WATCHDOG_EXIT", actor="position_watchdog", details={...})`.

---

## AC-1 — 손절 임계 이하 → 전량 매도 + "자동 손절" + 감사 (REQ-033-2, REQ-033-5)

- Given 보유 종목 X 의 `pnl_pct = -10.0`, `get_dynamic_thresholds(X).effective_stop = -8.5`,
  `qty = 7` 인 상태
- When `poll_position_watchdog()` 가 실행됨
- Then `kis_sell(client, ticker=X, qty=7, ...)` 가 **1회** 호출되고(전량)
- And "자동 손절" 텔레그램 브리핑이 1회 발송되며(message 에 ticker·pnl_pct·effective_stop·qty 포함)
- And `audit("POSITION_WATCHDOG_EXIT", details={"kind":"stop","ticker":X,"pnl_pct":-10.0,
  "threshold":-8.5,"qty":7})` 1건이 기록된다

## AC-2 — 익절 임계 이상 → 반량 매도 + "자동 익절" + 감사 (REQ-033-3, REQ-033-5)

- Given 보유 종목 Y 의 `pnl_pct = +14.0`, `get_dynamic_thresholds(Y).effective_take = +12.0`,
  `qty = 6`, 당일 아직 익절 안 함
- When `poll_position_watchdog()` 가 실행됨
- Then `kis_sell(client, ticker=Y, qty=3, ...)`(= `max(1, 6//2)`)가 1회 호출되고
- And "자동 익절" 브리핑 1회 + `audit(..., details={"kind":"take","ticker":Y,"pnl_pct":+14.0,
  "threshold":+12.0,"qty":3})` 1건이 기록되며
- And 종목 Y 가 **당일 익절됨**으로 표시된다

## AC-3 — 같은 날 이미 익절한 종목 → 재매도 없음 (REQ-033-3b)

- Given 종목 Y 가 AC-2 로 당일 이미 익절되었고, 잔여 보유의 `pnl_pct` 가 여전히
  `effective_take` 이상인 상태(예 +13.0)
- When 같은 KST 당일에 `poll_position_watchdog()` 가 다시 실행됨
- Then 종목 Y 에 대해 `kis_sell` 이 **다시 호출되지 않고**(반복 반량 매도 없음)
- And 익절 브리핑/감사도 추가로 발생하지 않는다
- And (날짜 경계) KST 날짜가 바뀐 다음 거래일에는 가드가 리셋되어 다시 익절 가능하다

## AC-4 — halt_state=true 라도 손절 실행 (REQ-033-4)

- Given `system_state.halt_state = true` 이고 보유 종목 X 의 `pnl_pct <= effective_stop`
- When `poll_position_watchdog()` 가 실행됨
- Then 워치독은 orchestrator 사이클 halt 게이트를 **거치지 않으므로** `kis_sell(전량)` 이 **여전히
  호출**되어 손절이 실행되고, "자동 손절" 브리핑 + 감사가 기록된다
- And (자본 보전 hard rule) 리스크 축소 청산은 halt 에 의해 차단되지 않는다
- And 익절도 동일하게 halt 상태에서 실행된다(Q-2 RESOLVED=YES)

## AC-5 — 일일 주문건수 한도 도달 상태라도 손절 실행 (REQ-033-4)

- Given 오늘 주문건수가 `RISK_DAILY_ORDER_COUNT_MAX` 에 도달한 상태이고 보유 종목 X 의
  `pnl_pct <= effective_stop`
- When `poll_position_watchdog()` 가 실행됨
- Then 워치독은 `check_pre_order`(daily-count 게이트)를 **거치지 않으므로** `kis_sell` 이 **여전히
  호출**되어 손절이 실행된다(청산 주문은 orders 에 기록되어 카운트를 *증가*시키지만 차단되지 않음)
- And (실거부 허용) 만약 KIS 가 하한가/locked 로 매도를 거부하면 그것은 정책이 아닌 현실 제약이므로
  로깅 후 skip 되고 sweep 은 계속된다

## AC-6 — 임계 안쪽(thresholds 사이) → 무동작 (경계)

- Given 보유 종목 Z 의 `pnl_pct = +2.0`, `effective_stop = -8.5`, `effective_take = +12.0`
  (`effective_stop < pnl_pct < effective_take`)
- When `poll_position_watchdog()` 가 실행됨
- Then `kis_sell` 이 호출되지 않고, 어떤 텔레그램 브리핑/감사 청산 항목도 발생하지 않는다(metrics
  `skipped` 만 증가)

## AC-7 — ATR 미가용 → 고정 폴백 임계 사용, 크래시 없음 (REQ-033-6, REQ-033-8)

- Given 보유 종목 W 의 ATR 가 미가용이어서 `get_dynamic_thresholds(W).source = "fixed_fallback"`
  이고 폴백 `effective_stop`/`effective_take` 가 반환되는 상태
- When `poll_position_watchdog()` 가 실행됨
- Then 워치독은 폴백 임계로 정상 평가하며(크래시 없음), 폴백 임계 기준으로 손절/익절/skip 을 판정한다

## AC-8 — qty==1 익절 → 1주 매도(전량 청산) 문서화 (REQ-033-3a)

- Given 보유 종목 V 의 `qty = 1`, `pnl_pct >= effective_take`, 당일 미익절
- When `poll_position_watchdog()` 가 실행됨
- Then `kis_sell(client, ticker=V, qty=1, ...)`(= `max(1, 1//2) = 1`)가 호출되어 **전량 청산**되고
- And 이는 "익절이 전량 청산이 되는" 의도된 엣지로 문서화된다

## AC-9 — 한 종목 오류 → 다른 보유 종목 계속 평가 (REQ-033-6a)

- Given 보유 종목 [A, B, C] 중 B 의 `get_dynamic_thresholds`(또는 quote/kis_sell)가 예외를
  던지고, A 는 손절 조건, C 는 익절 조건을 만족하는 상태
- When `poll_position_watchdog()` 가 실행됨
- Then B 의 오류는 로깅 후 격리되어(metrics `errors` 증가) sweep 을 중단시키지 않고,
- And A 는 손절, C 는 익절이 정상 실행되며 워치독은 예외로 종료되지 않는다(스케줄러 무중단)
- And 텔레그램 발송 실패가 발생해도 swallow 되어 청산 판정/실행은 계속된다

## AC-10 — `*/5` 09-15 KST 평일 스케줄 등록 (REQ-033-1)

- Given 스케줄러 `main()` 이 잡을 등록한 상태
- When 등록된 잡 목록을 확인
- Then `id="position_watchdog"` 잡이 존재하고 그 CronTrigger 가
  `day_of_week="mon-fri"`, `hour="9-15"`, `minute="*/5"`, `timezone=KST` 이며
- And 잡 콜백이 `poll_position_watchdog` 를 (`_wrap` 경유로) 호출한다(KRX 휴장일 자동 스킵)

## AC-11 — 기존 거래 흐름/watcher 무회귀 (REQ-033-7)

- Given 본 SPEC 의 신규 잡/모듈이 적용된 시스템
- When decision persona 매수/매도, orchestrator 사이클, 기존 watcher(price_threshold/
  volume_anomaly/blocked_release), `_execute_signal`/`kis_sell`/limits.py 가 각각 동작
- Then 이들의 동작·반환·횟수가 **종전과 동일**하다(본 SPEC 은 balance/thresholds/kis_sell 을
  호출/조회만 하고 내부 로직을 무수정)
- And (정적 검사) 신규 모듈은 account/thresholds/order 의 함수를 import 하여 호출/조회만 하며 그
  정의를 수정하지 않는다

---

## Definition of Done

- AC-1 ~ AC-11 전부 통과(자동 테스트 우선; `kis_sell`·`system_briefing`·`balance`·
  `get_dynamic_thresholds` 는 mock/주입으로 호출 횟수·인자 검증).
- 판정 헬퍼(`classify_holding` 류)의 분기(stop / take / skip / qty==1 / 폴백 / 당일 가드)가 단위
  테스트로 커버됨.
- halt_state=true / 일일 cap 도달 상태에서도 손절·익절이 실행됨(AC-4, AC-5 강제).
- 익절 same-day per-ticker 가드가 반복 반량 매도를 차단함(AC-3 강제).
- per-ticker 오류 격리 + ATR 폴백 + 텔레그램 실패 swallow 로 워치독이 스케줄러를 죽이지 않음(AC-9).
- runner.py 에 `position_watchdog` 잡이 `*/5` 09-15 KST mon-fri 로 등록(스케줄 테스트 or 정적 검증).
- TRUST 5 게이트 통과(coverage 85%+ 대상 모듈), ruff/black clean.
