# SPEC-TRADING-031 — Acceptance Criteria

매매 정지(halt) 브리핑 쿨다운에 대한 구체적·검증 가능한 인수 시나리오.
모든 시나리오는 Given/When/Then 형식이며, 별도 명시가 없으면 쿨다운 기본값 = 21600초(6시간)을 가정한다.

가정/약어:
- "halt 사이클" = `halt_state=true` 인 상태에서 실행된 트레이딩 사이클(pre_market / intraday / event-trigger).
- "halt 브리핑" = 사이클 게이트가 발송하는 `system_briefing("매매 정지", ...)` 텔레그램 메시지.
- "초기 알림" = `circuit_breaker.trip()` 의 "회로차단" 또는 `reset()` 의 "회로차단 해제" 메시지(별개 경로).
- 시간 경과는 테스트에서 `halt_notified_at` 을 과거로 설정하거나 시계를 주입(monkeypatch)하여 모사한다.

---

## AC-1 — 첫 halt 사이클은 알림, 6h 내 두 번째 사이클은 미알림 (REQ-031-1, REQ-031-2)

- Given `halt_state=true` 이고 `halt_notified_at IS NULL` 인 상태
- When 첫 halt 사이클이 게이트에 진입
- Then "매매 정지" 텔레그램 브리핑이 **1회 발송**되고
- And `halt_notified_at` 이 현재 시각으로 갱신되며
- And 해당 사이클은 거래를 스킵하고 `return` 한다
- When 같은 episode 내에서(쿨다운 6h 미경과) 두 번째 halt 사이클이 게이트에 진입
- Then "매매 정지" 브리핑은 **발송되지 않는다**(텔레그램 send 호출 0회)
- And `halt_notified_at` 값은 변하지 않는다(또는 갱신되어도 추가 발송 없음)

## AC-2 — 쿨다운 6h 경과 후 사이클은 재알림 (REQ-031-1)

- Given `halt_state=true` 이고 `halt_notified_at` 이 **6시간 1분 전**으로 설정된 상태
- When halt 사이클이 게이트에 진입
- Then "매매 정지" 브리핑이 **다시 1회 발송**되고
- And `halt_notified_at` 이 현재 시각으로 갱신된다
- And (경계 확인) `halt_notified_at` 이 정확히 6시간 전(>= cooldown) 인 경우에도 발송된다;
  5시간 59분 전(< cooldown) 인 경우에는 발송되지 않는다

## AC-3 — resume 후 재-halt 시 첫 사이클 즉시 알림 (REQ-031-2, REQ-031-3)

- Given `halt_state=true`, `halt_notified_at` 이 최근(쿨다운 미경과)으로 설정되어 알림이 throttle 중인 상태
- When `circuit_breaker.reset()`(=`/resume`) 이 호출되어 halt 가 해제됨
- Then `halt_state=false` 가 되고 `halt_notified_at` 이 **NULL 로 초기화**된다
- And "회로차단 해제" 초기 알림이 1회 발송된다(기존 동작, REQ-031-5 와 일관)
- When 이후 새 episode 로 `trip()` 이 다시 halt 를 발동하고 첫 halt 사이클이 게이트에 진입
- Then 쿨다운 잔여와 무관하게 "매매 정지" 브리핑이 **첫 사이클에서 즉시 발송**된다
  (이전 episode 의 `halt_notified_at` 이 남아 억제하지 않음)

## AC-4 — halt 게이트는 매 사이클 거래 스킵 + 매 스킵 로깅 (REQ-031-4)

- Given `halt_state=true` 이고 쿨다운으로 텔레그램 알림이 throttle 중인 상태
- When 연속된 여러 halt 사이클(예: 5개)이 게이트에 진입
- Then **모든** 사이클이 거래(risk/execute)를 수행하지 않고 즉시 `return res` 한다
  (체결/주문 제출 0건, 게이트 이후 코드 미실행)
- And 텔레그램 브리핑이 throttle 되어 미발송인 사이클을 포함해 **모든 스킵마다** 로그
  (예: `LOG.info`)가 1줄씩 남아, 로그상으로 5회 스킵 전부가 추적 가능하다

## AC-5 — 초기 trip()/`/halt`/`/resume` 알림 무회귀 (REQ-031-5)

- Given 시스템이 정상 운영 중
- When 회로차단 `trip()` 이 발동(자동 한도 위반 또는 `/halt`)
- Then "회로차단" 초기 알림이 **종전과 동일하게 1회** 발송된다(쿨다운 무관, 억제 없음)
- When `/resume`(`reset()`) 이 호출
- Then "회로차단 해제" 알림이 **종전과 동일하게 1회** 발송된다
- And (정적 검사) 사이클 게이트의 쿨다운 로직이 `trip()`/`reset()` 의 `system_briefing` 호출
  경로를 감싸거나 변경하지 않는다(쿨다운은 "매매 정지" 사이클 브리핑에만 적용)

## AC-6 — 쿨다운 설정 가능성 + 기본값 (REQ-031-6)

- Given 쿨다운 설정이 제공되지 않은 상태
- When halt 쿨다운 로직이 동작
- Then 기본 쿨다운 = **21600초(6시간)** 가 적용된다
- And (설정 home 이 구현된 경우) 쿨다운 값을 다른 값(예: 3600초)으로 변경하면 throttle 경계가
  그 값에 따라 동작한다(예: 1시간 1분 후 재알림, 59분 후 미알림)

## AC-7 — 영속화/재시작 생존 (REQ-031-1c)

- Given `halt_state=true` 이고 `halt_notified_at` 이 최근으로 설정되어 throttle 중인 상태
- When 프로세스/컨테이너가 재시작된 뒤 halt 사이클이 다시 진입(쿨다운 미경과)
- Then `halt_notified_at` 이 DB 에서 그대로 읽혀 "매매 정지" 브리핑이 **발송되지 않는다**
  (재시작이 쿨다운을 리셋하지 않는다 — calendar-day 가 아닌 순수 경과시간 기반)

---

## Definition of Done

- AC-1 ~ AC-7 전부 통과(자동 테스트 우선, 텔레그램 send 는 mock 으로 호출 횟수 검증).
- `src/trading/db/migrations/023_halt_notify_cooldown.sql` 가 idempotent 하게(2회 적용 무오류) 동작.
- 3개 orchestrator 게이트 모두 헬퍼 경유로 치환되고 스킵 로깅 포함.
- `circuit_breaker.reset()` 가 `halt_notified_at` 을 NULL 로 초기화.
- TRUST 5 게이트 통과(coverage 85%+ 대상 모듈), ruff/black clean.
