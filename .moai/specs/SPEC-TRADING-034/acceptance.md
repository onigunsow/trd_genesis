# SPEC-TRADING-034 — Acceptance Criteria

휴면 포트폴리오 페르소나의 사이클 연결에 대한 구체적·검증 가능한 인수 시나리오.
모든 시나리오는 Given/When/Then 형식이며, 1회 조정은 공유 헬퍼
`_apply_portfolio_adjustment(signals, sig_ids, ...)` 호출(또는 사이클 1회 실행)로 모사한다.

가정/약어:
- "buy 시그널" = `{"ticker","side":"buy","qty",...}`; "sell 시그널" = `side="sell"`.
- `signals[i] ↔ sig_ids[i]` 는 위치 정렬(decision.run 산출). 조정 후에도 정렬은 보존된다.
- "포트폴리오 출력" = `portfolio.run(...).response_json` =
  `{"adjusted_signals":[{"ticker","side","qty_original","qty_adjusted","rationale"}],
  "rejected":[{"ticker","reason"}]}`. 테스트에서는 `portfolio.run` 을 mock 으로 주입하여 이 출력을
  구성한다.
- `holdings`/`holdings_count`/`total_assets`/`cash_pct` 는 인-스코프 `assets`(=`balance()`)에서
  파생. 테스트에서는 `assets`(또는 balance)를 fake/주입한다.
- "조정 알림" = `system_briefing("포트폴리오 조정", ...)`(텔레그램 send 는 mock 으로 카테고리/횟수
  검증). "감사" = `audit("PORTFOLIO_ADJUSTMENT", actor=..., details={...})`.
- "실행" = risk/execute 루프(`for sig, decision_id in zip(signals, sig_ids, ...)`)가 그 시그널을
  순회함. 테스트는 헬퍼 반환 `(new_signals, new_sig_ids)` 의 내용/정렬로 검증하거나, execute(`kis_sell`
  /`kis_buy`/risk) mock 호출로 검증.

---

## AC-1 — holdings≥5, 포트폴리오가 buy qty 축소 → 실행 qty 가 조정값 (REQ-034-1, REQ-034-2)

- Given `holdings_count = 6`(≥5), buy 시그널 X(`qty=10`)와 그 sig_id, 포트폴리오 출력
  `adjusted_signals=[{"ticker":X,"side":"buy","qty_original":10,"qty_adjusted":4,"rationale":"섹터 편중"}]`,
  `rejected=[]`
- When `_apply_portfolio_adjustment(...)` 가 실행됨
- Then 반환된 buy 시그널 X 의 `qty` 가 **4** 로 설정되고(구속력),
- And 이후 risk/execute 루프가 X 를 **qty=4** 로 순회한다(실행 qty = 조정값)
- And 포트폴리오 페르소나가 1회 호출되었다(`portfolio.run` mock 1회, buy 시그널만 `decision_signals`)

## AC-2 — 포트폴리오가 buy 거부 → 미실행 + res.rejected + 감사 (REQ-034-3)

- Given `holdings_count ≥ 5`, buy 시그널 Y(`qty=5`)와 sig_id `sid_Y`, 포트폴리오 출력
  `rejected=[{"ticker":Y,"reason":"섹터 편중"}]`
- When 헬퍼가 실행됨
- Then 반환된 시그널/sig_ids 에 Y/`sid_Y` 가 **포함되지 않고**(실행 집합에서 제거),
- And `res.rejected` 에 `sid_Y` 가 추가되며,
- And `audit("PORTFOLIO_ADJUSTMENT", details=...)` 에 Y 의 거부가 기록되고 Y 는 **실행되지 않는다**
  (risk/execute 루프 미도달)

## AC-3 — sell 시그널은 포트폴리오가 돌아가도 무조정 통과 (REQ-034-4)

- Given `holdings_count ≥ 5`, 시그널 [buy X(qty=10), sell S(qty=3)] 와 각 sig_id, 포트폴리오 출력이
  X 를 조정/거부하더라도(예 X qty_adjusted=4)
- When 헬퍼가 실행됨
- Then sell S 는 **무변경**(qty=3, 드롭/축소 없음)으로 반환 시그널/sig_ids 에 보존되며,
- And 포트폴리오 페르소나에는 **buy 시그널만**(X) `decision_signals` 로 전달된다(sell S 미전달),
- And 시그널↔sig_id 정렬이 보존되어 S 의 sig_id 가 S 와 짝을 유지한다

## AC-4 — holdings<5 → 포트폴리오 미호출, 시그널 무변경 (REQ-034-5)

- Given `holdings_count = 3`(<5), buy 시그널 [X(qty=10), Z(qty=2)] 와 sig_ids
- When 헬퍼가 실행됨
- Then `portfolio.run` 이 **호출되지 않고**(mock 0회 = Sonnet 비용 0),
- And 반환 시그널/sig_ids 가 입력과 **완전히 동일**하다(무변경 통과)

## AC-5 — 포트폴리오 페르소나 오류/잘못된 JSON → 미조정 폴백 + 알림 (REQ-034-6)

- Given `holdings_count ≥ 5`, buy 시그널 [X(qty=10)], 그리고 `portfolio.run` 이 (a) 예외를 던지거나
  (b) `response_json=None`(잘못된/누락 JSON)을 반환하거나 (c) 필수 키 누락
- When 헬퍼가 실행됨
- Then 반환 시그널/sig_ids 가 **입력과 동일**(미조정 폴백)하여 사이클이 **계속**되고(거래 차단 없음),
- And 오류가 로그 + 텔레그램(`system_error` 또는 `system_briefing`)으로 알려지며,
- And (텔레그램 실패) 텔레그램 발송이 실패해도 swallow 되어 헬퍼는 예외 없이 입력 시그널을 반환한다

## AC-6 — qty_adjusted == 0 → 드롭(거부 취급) (REQ-034-2)

- Given `holdings_count ≥ 5`, buy 시그널 X(`qty=10`)와 sig_id, 포트폴리오 출력
  `adjusted_signals=[{"ticker":X,"qty_original":10,"qty_adjusted":0,...}]`
- When 헬퍼가 실행됨
- Then X 는 실행 집합에서 **제거**되고(qty_adjusted==0 ⇒ 드롭), `res.rejected` + audit 에 기록되며
  실행되지 않는다(AC-2 와 동일한 거부 경로)

## AC-7 — adjusted ticker 가 buy 시그널에 없음 → 무시 (경계)

- Given `holdings_count ≥ 5`, buy 시그널 [X(qty=10)] 만 존재, 포트폴리오 출력
  `adjusted_signals=[{"ticker":"999999","qty_adjusted":1,...}]`(입력에 없는 ticker)
- When 헬퍼가 실행됨
- Then 매칭되지 않는 "999999" 조정은 **무시**되고(no-op), X 는 무변경(qty=10)으로 유지된다
- And (rejected 도 동일) 입력에 없는 ticker 의 rejected 항목도 무시된다

## AC-8 — 세 사이클(pre_market/intraday/event) 모두에 적용 (REQ-034-1)

- Given 본 SPEC 의 헬퍼/호출이 적용된 시스템
- When `run_pre_market_cycle` / `run_intraday_cycle` / `run_event_trigger_cycle` 가 각각 실행되고
  decision 이 buy 시그널을 산출하며 holdings≥5
- Then 세 사이클 **모두** halt 게이트 통과 직후·`zip(signals, sig_ids)` 실행 루프 직전에 포트폴리오
  조정 헬퍼가 호출되어, 실행 루프가 **조정된** signals/sig_ids 를 순회한다(정적/동적 검증)

## AC-9 — 비자명 조정 시 텔레그램 + 감사 (REQ-034-7)

- Given AC-1(축소) 또는 AC-2(거부)가 발생하는 상태
- When 헬퍼가 실행됨
- Then `system_briefing("포트폴리오 조정", <message>)` 가 1회 발송되며(message 에 어떤 buy 가 얼마로
  축소/드롭되었는지·사유 포함),
- And `audit("PORTFOLIO_ADJUSTMENT", details={"cycle":..., "adjusted":[...], "rejected":[...]})` 1건이
  기록된다
- And (무변경 시) 조정/거부가 전혀 없으면(모든 buy 무변경) 불필요한 텔레그램/감사를 남기지 않을 수 있다

## AC-10 — 조정 이후에도 risk 한도 게이트 정상 적용 (REQ-034-7c)

- Given AC-1 로 X 의 qty 가 4 로 조정된 상태
- When 조정된 X(qty=4)가 risk/execute 루프로 흐름
- Then X(qty=4)는 여전히 기존 risk 페르소나 검토 / `check_pre_order` 등 기존 게이트를 거치며,
- And 포트폴리오 레이어는 risk 게이트를 **우회하지 않는다**(조정은 risk 입력 qty 를 바꿀 뿐 게이트
  자체는 무변경)

## AC-11 — 기존 흐름 무회귀 (REQ-034-8)

- Given 본 SPEC 의 헬퍼/호출이 적용된 시스템
- When (a) holdings<5, 또는 (b) buy 시그널 없음, 또는 (c) 포트폴리오 실패 상태에서 사이클이 동작
- Then decision/risk/execute, `sig_ids` 의미, 세 사이클의 halt 게이트·qty=0 스킵·3+ HOLD 차단·
  briefing 등 기타 동작이 **종전과 동일**하다(시그널/sig_ids 무변경 통과)
- And (정적 검사) 신규 헬퍼는 portfolio/decision/balance 의 함수를 import 하여 호출/조회만 하며 그
  정의를 수정하지 않는다

## AC-12 — cli_only_mode 에서 포트폴리오 페르소나가 CLI 경로로 동작(비용 0) (REQ-034-9)

- Given `cli_only_mode=true`(`is_cli_mode_active()` 가 True 를 반환하도록 mock)이고 `holdings_count
  ≥ 5`, buy 시그널 [X] 가 존재하는 상태
- When 포트폴리오 페르소나가 실행됨(`portfolio.run(...)` 호출)
- Then `portfolio.run` 은 `is_cli_mode_active()` 분기를 타서 **`call_persona_via_cli(...)` 를 호출**
  하며(CLI 브리지 mock 1회), **직접 유료 API(`call_persona`)는 호출되지 않는다**(mock 0회)
- And `expect_json=True` 로 호출되어 JSON 응답을 파싱한다
- And (대조) `is_cli_mode_active()` 가 False 면 기존 `call_persona`(API) 경로를 타며, 두 경로 모두
  실패 시 REQ-034-6 미조정 폴백이 적용된다(AC-5 와 정합)
- And (테스트 격리) CLI 브리지는 mock 으로 대체되어 네트워크/실제 `claude -p` 를 호출하지 않는다

---

## Definition of Done

- AC-1 ~ AC-12 전부 통과(자동 테스트 우선; `portfolio.run`·`call_persona_via_cli`·`is_cli_mode_active`
  ·`system_briefing`·`audit`·balance/`assets` 는 mock/주입으로 호출 횟수·인자·반환 정렬 검증).
- 순수 매핑 로직(adjusted/rejected 적용, buy/non-buy 분리, 정렬 보존, qty_adjusted==0 드롭, 미매칭
  ticker 무시)이 단위 테스트로 커버됨.
- holdings<5 → portfolio 미호출(AC-4) 및 페르소나 실패 → 미조정 폴백(AC-5)이 강제됨.
- sell 무조정 통과(AC-3) 및 buy-only 입력 전달이 강제됨.
- 세 사이클 모두에 삽입(AC-8) — 정적(코드 경로) 또는 동적(사이클 실행 mock) 검증.
- 조정 시 telegram + audit(AC-9), 조정 후 risk 게이트 유지(AC-10), 기존 흐름 무회귀(AC-11).
- `portfolio.run` CLI 분기 전환(REQ-034-9): cli_only_mode 에서 `call_persona_via_cli` 경로 사용
  (AC-12), CLI 브리지 mock 으로 검증(네트워크 미사용).
- TRUST 5 게이트 통과(coverage 85%+ 대상 모듈), ruff/black clean.
