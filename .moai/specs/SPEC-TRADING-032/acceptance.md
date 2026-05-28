# SPEC-TRADING-032 — Acceptance Criteria

장 시작 전 자동 매매 재개에 대한 구체적·검증 가능한 인수 시나리오.
모든 시나리오는 Given/When/Then 형식이며, 자동 점검은 `run_premarket_auto_resume()` 1회 호출로 모사한다.

가정/약어:
- "halt" = `system_state.halt_state=true`. "재개" = `circuit_breaker.reset(actor="auto_resume_premarket")`.
- "활성 트립" = halt 를 발동한 가장 최근의 `audit_log` `CIRCUIT_BREAKER_TRIP` 행
  (RESET 으로 이어지지 않은 것). 테스트에서는 audit_log 에 TRIP/RESET 행을 삽입하거나 조회를
  fake/주입하여 활성 트립을 구성한다.
- "자동 재개 알림" = `system_briefing("자동 재개", ...)`. "수동 검토 알림" =
  `system_briefing("수동 검토 필요", ...)`. 텔레그램 send 는 mock 으로 호출 횟수/카테고리 검증.
- breach 문자열은 limits.py 포맷을 따른다(예: `"daily_count: 오늘 주문 10 → 한도 10"`,
  `"daily_loss: 오늘 손익 -3.20% ≤ 한도 -3.00%"`, `"single_order: ..."`, `"per_ticker: ..."`).

---

## AC-1 — daily_count 단독 정지 → 자동 재개 (REQ-032-2, REQ-032-5)

- Given `halt_state=true` 이고 활성 트립이 `reason="pre-order limit breach"`,
  `details.breaches=["daily_count: 오늘 주문 10 → 한도 10"]` 인 상태
- When `run_premarket_auto_resume()` 가 실행됨
- Then `circuit_breaker.reset(actor="auto_resume_premarket")` 가 호출되어 `halt_state=false` 가 되고
- And "자동 재개" 텔레그램 브리핑이 **1회** 발송되며
- And `audit_log` 에 `AUTO_RESUME_PREMARKET` 항목(`details.decision="resumed"`,
  `cause` 가 daily_count 를 나타냄)이 1건 기록된다
- And (SPEC-031 연동) `reset()` 이 `halt_notified_at` 을 NULL 로 초기화하고 "회로차단 해제" 알림을
  종전대로 발송한다

## AC-2 — single_order / per_ticker (손실 없는) 정지 → 자동 재개 (REQ-032-2)

- Given `halt_state=true` 이고 활성 트립이 `reason="pre-order limit breach"`,
  `details.breaches=["single_order: 주문금액 ... > 한도 ..."]`(또는 `per_ticker: ...`) 인 상태
  (breaches 에 daily_loss 항목 없음)
- When `run_premarket_auto_resume()` 가 실행됨
- Then 매매가 **자동 재개**되고(`halt_state=false`), "자동 재개" 브리핑 1회 + `decision="resumed"`
  audit 1건이 기록된다

## AC-3 — daily_loss 정지 → 재개 안 함, 수동 검토 알림 (REQ-032-3b, REQ-032-5)

- Given `halt_state=true` 이고 활성 트립이 `reason="pre-order limit breach"`,
  `details.breaches=["daily_loss: 오늘 손익 -3.20% ≤ 한도 -3.00%"]` 인 상태
- When `run_premarket_auto_resume()` 가 실행됨
- Then `circuit_breaker.reset` 이 **호출되지 않고** `halt_state` 는 **true 로 유지**되며
- And "수동 검토 필요" 텔레그램 브리핑이 1회 발송되고
- And `audit_log` 에 `AUTO_RESUME_PREMARKET`(`decision="held"`, `cause="daily_loss"`)이 1건 기록된다

## AC-4 — 수동 `/halt` 정지 → 재개 안 함, 수동 검토 알림 (REQ-032-3a, REQ-032-5)

- Given `halt_state=true` 이고 활성 트립이 `reason="manual /halt"`(또는 `"manual cli /halt"`),
  `details.actor=<운영자>` 인 상태
- When `run_premarket_auto_resume()` 가 실행됨
- Then 매매는 **재개되지 않고**(reset 미호출, `halt_state` true 유지) "수동 검토 필요" 브리핑 1회 +
  `decision="held"`, `cause="manual"` audit 1건이 기록된다
- And (자본 보전 hard rule) 어떤 cooldown/조건에서도 수동 정지는 자동 재개되지 않는다

## AC-5 — daily_count + daily_loss 혼재 → 재개 안 함 (REQ-032-3b)

- Given `halt_state=true` 이고 활성 트립이 `reason="pre-order limit breach"`,
  `details.breaches=["daily_count: 오늘 주문 10 → 한도 10", "daily_loss: 오늘 손익 -3.50% ≤ 한도 -3.00%"]`
  인 상태
- When `run_premarket_auto_resume()` 가 실행됨
- Then breaches 에 daily_loss 가 **존재**하므로 매매는 **재개되지 않고**(daily_count 가 양성이어도
  loss 가 있으면 HOLD), "수동 검토 필요" 브리핑 1회 + `decision="held"`, `cause="daily_loss"` audit
  1건이 기록된다

## AC-6 — 미정지 상태 → no-op, 텔레그램 없음 (REQ-032-4)

- Given `halt_state=false` 인 상태
- When `run_premarket_auto_resume()` 가 실행됨
- Then `circuit_breaker.reset` 이 호출되지 않고
- And **어떤 텔레그램 브리핑도 발송되지 않는다**(send 호출 0회)
- And `LOG.info` 로 "정지 아님 — 자동 재개 스킵" 류 로그가 1줄 남는다(감사 항목은 선택)

## AC-7 — halt_state=true 이나 활성 트립 원인 불명 → 재개 안 함, 수동 검토 알림 (REQ-032-3c)

- Given `halt_state=true` 이나 audit_log 에 활성 `CIRCUIT_BREAKER_TRIP` 행이 없거나
  (예: 가장 최근 이벤트가 RESET 이거나 TRIP 행 자체 부재), 또는 활성 트립의 `reason` 이
  "pre-order limit breach"·"manual" 중 어느 것도 아니거나, breaches 가 누락/비어있음/형식 불량인 상태
- When `run_premarket_auto_resume()` 가 실행됨
- Then 원인을 확정할 수 없으므로 **재개하지 않고**(defensive default, `halt_state` true 유지)
  "수동 검토 필요" 브리핑 1회 + `decision="held"`, `cause` 가 불명/unknown 임을 나타내는 audit 1건이
  기록된다

## AC-8 — 07:25 KST 평일 스케줄 등록 (REQ-032-1)

- Given 스케줄러 `main()` 이 잡을 등록한 상태
- When 등록된 잡 목록을 확인
- Then `id="premarket_auto_resume"` 잡이 존재하고 그 CronTrigger 가
  `day_of_week="mon-fri"`, `hour=7`, `minute=25`, `timezone=KST` 이며
- And 해당 잡은 pre_market(07:30) 잡보다 먼저 트리거되도록 설정되어 있다
- And 잡 콜백이 `run_premarket_auto_resume` 를 (`_wrap` 경유로) 호출한다

## AC-9 — 기존 회로차단/리스크 경로 무회귀 (REQ-032-6)

- Given 본 SPEC 의 신규 잡/모듈이 적용된 시스템
- When `trip()`, `reset()`(수동 `/resume`), limits.py 한도 판정이 각각 동작
- Then 이들의 알림 횟수·내용·반환값이 **종전과 동일**하다(본 SPEC 은 reset 을 호출만 하고
  trip/limits 는 조회/참조만 — 내부 로직 무수정)
- And (정적 검사) 신규 모듈은 circuit_breaker/limits 의 함수를 import 하여 호출/조회만 하며
  그 정의를 수정하지 않는다

---

## Definition of Done

- AC-1 ~ AC-9 전부 통과(자동 테스트 우선, 텔레그램 send 와 `circuit_breaker.reset` 은 mock 으로
  호출 횟수·인자(actor="auto_resume_premarket") 검증).
- `classify_halt()` 의 7개 분기(daily_count/single_order·per_ticker/daily_loss/manual/혼재/불명/미정지)
  가 단위 테스트로 커버됨.
- audit_log active-trip 조회가 TRIP-only / RESET-after-TRIP / no-TRIP 케이스를 올바르게 구분.
- runner.py 에 `premarket_auto_resume` 잡이 07:25 mon-fri KST 로 등록(스케줄 테스트 or 정적 검증).
- 어떤 경로에서도 수동 `/halt` 와 daily_loss 정지가 자동 재개되지 않음(AC-3, AC-4, AC-5 강제).
- TRUST 5 게이트 통과(coverage 85%+ 대상 모듈), ruff/black clean.
