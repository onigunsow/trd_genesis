# SPEC-TRADING-031 — Implementation Plan

매매 정지(halt) 브리핑 쿨다운. 작고 잘 정의된 동작 변경 — 알림 빈도만 바꾸고 게이트/리스크 로직은 무회귀.

## Technical Approach

상태를 `system_state` 싱글톤에 영속화하고(컨테이너 재시작 생존), 작은 결정 헬퍼가 쿨다운을
판정한다. orchestrator 의 3개 halt 게이트는 `tg.system_briefing` 직접 호출 대신 헬퍼를 호출하며,
헬퍼 결과와 무관하게 매 사이클 스킵 로깅 후 종전처럼 `return` 한다. resume 시 헬퍼 상태를 초기화한다.

### 핵심 결정 (사용자 승인)
- throttle 정책 = **cooldown**, 기본 21600초(6시간), 설정 가능.
- episode 첫 사이클 = **즉시 알림**(쿨다운이 첫 메시지를 억제하지 않음, `halt_notified_at IS NULL` 분기).
- `reset()`(=`/resume`) 시 `halt_notified_at = NULL` 로 초기화.
- halt 게이트(거래 스킵 + `return`) 보존, 매 스킵 `LOG.info`.
- trip()/`/halt`/`/resume` 초기 알림 1:1 무회귀.

## Milestones (우선순위 기반, 시간 추정 없음)

### Primary Goal (P-High) — 상태 영속화 + 결정 헬퍼
- 마이그레이션 `023_halt_notify_cooldown.sql` 작성: `system_state` 에 `halt_notified_at TIMESTAMPTZ NULL`
  추가. idempotent ALTER 패턴(013/010 선례) + `schema_migrations` 등록.
- `circuit_breaker.py` 에 쿨다운 결정 헬퍼 추가:
  - `halt_notified_at IS NULL` 또는 `now - halt_notified_at >= cooldown` → 발송 + 타임스탬프 갱신.
  - 그 외 → 스킵. (헬퍼가 `tg.system_briefing` 까지 수행하고 발송 여부 bool 반환 권장.)
- 쿨다운 기본 상수 `HALT_NOTIFY_COOLDOWN_SECONDS = 21600` 정의(설정 home 은 Q-1).
- 대상 REQ: REQ-031-1, REQ-031-2, REQ-031-6 / 검증: AC-1, AC-2, AC-6, AC-7

### Secondary Goal (P-High) — orchestrator 게이트 치환 + resume 초기화
- orchestrator.py 3개 게이트(라인 891/1180/1329, 구현 시 패턴으로 재확인) 의 직접
  `tg.system_briefing("매매 정지", ...)` 호출을 헬퍼 호출로 교체.
- 각 게이트에 `LOG.info` 스킵 로깅 추가(throttle 여부와 무관하게 매 사이클 1줄). 기존 `return res` 유지.
- `circuit_breaker.reset()` 에서 `update_system_state(halt_state=False, halt_notified_at=None, ...)` 로
  throttle 초기화.
- 대상 REQ: REQ-031-3, REQ-031-4 / 검증: AC-3, AC-4

### Final Goal (P-Medium) — 무회귀 검증 + 테스트
- trip()/`/halt`/`/resume` 초기 알림이 변하지 않음을 정적·동적으로 확인.
- 단위 테스트 `tests/risk/test_halt_notify_throttle.py`(권장): NULL 첫 사이클 발송, 6h 미경과 미발송,
  6h 경과 재발송, resume 초기화 후 즉시 재발송, 매 스킵 로깅, 재시작 생존(상태 영속) 시나리오.
- 텔레그램 `system_briefing` 은 mock 으로 호출 횟수 검증.
- 대상 REQ: REQ-031-5 / 검증: AC-5, 전체 AC 회귀

### Optional Goal (P-Low) — 설정 home
- `scheduler.yaml` 에 `halt_notify_cooldown_seconds` 키 추가(기존 `trigger_cooldown_seconds` 선례).
  미구현 시 상수 기본값으로 충분(Q-1).

## Architecture / Design Direction

- 상태는 DB(`system_state`)에 단일 컬럼으로 — 별도 테이블 불필요, 재시작 생존, accessor 무변경
  (`get_system_state` SELECT *, `update_system_state(**fields)` 임의 컬럼 + None→NULL).
- 쿨다운 판정 로직은 `circuit_breaker.py` 에 집중(halt/resume 도메인과 동일 모듈) — orchestrator 는
  헬퍼만 호출해 3중 중복을 제거.
- "초기 알림(episode-level)" 과 "사이클 게이트 알림(periodic 재확인)" 을 명확히 분리: 쿨다운은
  후자에만 적용.

## Risks and Mitigations

- R-1 (게이트 라인 이동): 본 SPEC 의 라인(891/1180/1329)은 작성 시점 기준. 구현자는 패턴
  (`if state["halt_state"]:` → `system_briefing("매매 정지")` → `return res`)으로 3곳을 재탐색.
- R-2 (resume 누락 초기화): `reset()` 에 초기화를 넣지 않으면 다음 episode 첫 사이클이 억제될 수 있음
  → AC-3 가 이를 강제 검증.
- R-3 (None→NULL 매핑): psycopg 파라미터 바인딩 가정(A-2). 테스트에서 실제 DB(또는 동등 fake)로
  NULL 저장/조회 검증 권장.
- R-4 (trip() 경로 오염): 쿨다운 헬퍼가 episode 초기 알림 경로를 감싸지 않도록 주의 → AC-5 정적 검사.

## Dependencies

- 선행 SPEC 무차단(additive). SPEC-016(halt 기원)/024(스팸 증폭 원인)/027(브리핑)/015(알림) 와 호환.
- 마이그레이션 번호 023 은 현재 최고 022 다음으로 확정(충돌 없음).

## Out of Scope

- 회로차단 트립 조건/리스크 한도 변경, 다른 브리핑 빈도/포맷 변경, 새 cron, calendar-day 리셋 방식.
