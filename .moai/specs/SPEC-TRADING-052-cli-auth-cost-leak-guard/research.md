# SPEC-TRADING-052 연구 (Research) — 코드베이스 검증

## 누수 경로 코드 검증 (확정 — 재조사 불필요)

### 경로 1: 페르소나 CLI 폴백 (카운터 있음, in-process)
`src/trading/personas/base.py`:
- `call_persona_via_cli` L640 `except (CLITimeoutError, CLICallError)` → `_record_cli_failure`(L643) → `assert_fallback_model`(L649) → **Haiku 유료 폴백 `call_persona(model=_HAIKU_FALLBACK_MODEL)`**(L660-670). 이중실패 시 `tg.system_briefing("Double failure")` + raise(L671-687).
- 이 폴백 분기엔 `@block_if_cli_only_mode`가 **의도적으로 없음**(L110-111 docstring: "single sanctioned exception"). SPEC-016 설계 의도.
- `_record_cli_failure`(L530-564): `_cli_failure_count`(**모듈 전역**, L520) 증가 → 매 실패마다 `tg.system_briefing("CLI fallback", ...)`(L541, **무쿨다운**) → 3연속(`_CLI_AUTO_DISABLE_THRESHOLD=3`, L521) 도달 시 `update_system_state(cli_personas_enabled=False)`(L551) + audit + "CLI auto-disabled" 알림(L558), 카운터 리셋(L564).
- `_reset_cli_failures`(L524): 성공 시 카운터 0.

### 경로 2: 직접 API (누수 본체 — 감지·알림 전무)
- 자동전환으로 `cli_personas_enabled=False`가 되면 `is_cli_mode_active()`(L759-787)가 False 반환.
- `is_cli_mode_active`는 추가로 워처 하트비트 stale(`is_watcher_alive()`, cli_bridge.py L82-91, `HEARTBEAT_STALE_SECONDS=60.0` L36) 시에도 False + "Watcher stale" 알림(L776-782, 무throttle).
- `personas/decision.py` L103-148: `if is_cli_mode_active(): call_persona_via_cli(...) else: call_persona(...)`. **else 분기가 직접 유료 호출** — 여기엔 `_record_cli_failure`/degraded 마킹/throttle 알림이 **전혀 없다**. 다른 페르소나(macro/micro/risk)도 동형.
- → 사고의 조용한 누수 본체. degraded 감지를 이 분기까지 확장해야 함(REQ-052-A1/A3).

### 경로 3: 뉴스 `_call_haiku` (가드 플래그 불일치 사각지대)
- `news/intelligence/analyzer.py` L213 `@block_if_cli_only_mode def _call_haiku(...)`, 호출 L631/637.
- `block_if_cli_only_mode`(base.py L104-139)는 `is_cli_only_mode()`(L80-101) = `cli_only_mode` OR `cli_personas_enabled`에 의존.
- 자동전환은 `cli_personas_enabled=False`만 끔 → `cli_only_mode`가 별도로 True가 아니면 `is_cli_only_mode()`가 False가 되어 **가드 통과 → 유료 Haiku 호출**. 플래그 불일치 사각지대.
- 단, `scheduler.py` L195-208의 import 폴백은 `is_cli_only_mode()` True면 graceful defer(SPEC-043) — 이건 안전.

## 기존 throttle 패턴 (재사용 대상 — SPEC-031)
`src/trading/risk/circuit_breaker.py`:
- `maybe_notify_halt(cooldown_seconds=None, now_provider=None)`(L35-70): `system_state.halt_notified_at`(mig 023) NULL이거나 `now-last >= cooldown`이면 발사+스탬프, 아니면 skip. `HALT_NOTIFY_COOLDOWN_SECONDS=21600`(6h, L23). seam: `cooldown_seconds`(override) + `now_provider`(**`Callable[[], datetime]`** — bare datetime이 아니라 호출 가능 provider, L37/L59에서 `(now_provider or (lambda: datetime.now(...)))()`로 호출).
- `reset()`(L87-90): `update_system_state(halt_state=False, halt_notified_at=None)` — 에피소드 종료 시 throttle 클럭 NULL 리셋(다음 에피소드 첫 발생 즉시).
- → REQ-052-B `maybe_send_cli_degraded_alert(cooldown_seconds=None, now_provider=None)`를 이 구조와 **동형**으로 신설(now_provider도 `Callable[[], datetime]`). mig 023 SQL house style(information_schema 가드 + COMMENT + schema_migrations + audit_log)을 mig 034가 따른다.
- (정정: 0.1.0 초안의 `maybe_send_halt_briefing`은 존재하지 않는 함수명이었다 — 실명 `maybe_notify_halt`. grep 검증 완료.)

## 호스트 러너 신호 (소비 대상)
`scripts/persona_watcher.sh` L98-137:
- `RESPONSE=$(... | claude -p --max-turns 1)`, `EXIT_CODE=$?`.
- `if EXIT_CODE==0 && -n RESPONSE` → 정상 result; `else` → `error:"cli_failed (exit=$EXIT_CODE)"` 마킹.
- 사고: exit=0인데 RESPONSE 빈문자열 → `-n RESPONSE` False → else 분기 → result.error 세팅 → cli_bridge `call_persona_cli`가 CLICallError raise(L170-173). 즉 **exit=0 빈출력도 CLICallError로 귀결**(EC-1 근거). 본 SPEC은 이 신호를 소비만, 러너 스키마 미변경(Exclusion #6).

## system_state 컬럼 현황 (검증)
- `cli_personas_enabled`(mig 018, BOOLEAN DEFAULT false). `cli_only_mode`(SPEC-016 컬럼, `is_cli_only_mode`가 읽음).
- `halt_notified_at`(mig 023, TIMESTAMPTZ) — throttle 패턴 선례.
- 본 SPEC 신규: `strict_cost_zero_mode`, `cli_degraded`(또는 카운터 분리), `cli_degraded_notified_at`. → mig 034(OQ-2 스키마 확정 후).

## 마이그레이션 상태
- 최신 = `033_edge_hardening.sql`(SPEC-048). 번호 027/030 결번(과거 정리, 기능 영향 없음).
- 본 SPEC은 system_state 컬럼 추가 필요 → mig **034**(다음 가용). idempotent(mig 023 house style).

## 시장 중립성
- 누수 경로(base.py/decision.py/analyzer.py)는 LLM 라우팅·비용 레이어로 시장 무관. degraded/strict/throttle 상수는 운영 파라미터 → NFR-4 충족 용이.

## SPEC-016 정책 충돌 (ADR-001 근거)
- base.py L107-111 docstring 명시: Haiku 폴백은 "single sanctioned exception", `block_if_cli_only_mode`를 폴백에 붙이지 **않는 것이 SPEC-016 의도**. 따라서 폴백 차단을 기본 동작으로 바꾸면 **명백한 SPEC-016 회귀**. → `strict_cost_zero_mode` 옵트인(기본 OFF) 필수. 운영자 가치판단(OQ-1).

## 미해결 (구현 전 확인)
- OQ-1 (OPEN·non-blocking): strict 운영 기본값(비용0 보장 vs 폴백 가용성) — 운영자 정책 선택. 구현은 기본 OFF로 진행.
- OQ-2 (RESOLVED, ADR-002): degraded 영속 스키마 — latch(`cli_degraded`/`cli_degraded_since`/`cli_consecutive_failures`)와 throttle(`cli_degraded_notified_at`) 분리, mig 034 additive.
- OQ-3 (RESOLVED, ADR-003): 조기경고 쿨다운 기본값 = 1시간(`CLI_DEGRADED_ALERT_COOLDOWN_SECONDS=3600`, named tunable).
- OQ-4 (RESOLVED, ADR-005): 영속 degraded는 in-process 자동전환 카운터와 독립(latch, A2에서만 해제). 감지 임계 N=3 재사용.
- OQ-5 (OPEN·non-blocking): 전용 브랜치 필요 여부(현 브랜치 stale 가능성, manager-git).
