# SPEC-TRADING-052 구현 계획 (Plan)

## 기술 접근

`personas/base.py` 중심의 brownfield delta + system_state 영속 컬럼 1회 마이그레이션(034). 핵심은 세 누수 경로(폴백/직접/뉴스)를 **단일 degraded 진실소스**로 통합하고, 그 위에 (a) 영속 감지, (b) SPEC-031 동형 쿨다운 경고, (c) 옵트인 strict 차단, (d) 구조화 로그를 얹는 것이다. 기본값 OFF에서 기존 동작 바이트 보존.

### 핵심 변경 지점

1. **마이그레이션 034** (REQ-052-NFR-3, ADR-002 — OQ-2 RESOLVED) — system_state 컬럼 추가(latch/throttle 분리)
   - `cli_degraded`(BOOLEAN DEFAULT false) — degraded latch(REQ-052-A5 단조 latch/clear).
   - `cli_degraded_since`(TIMESTAMPTZ NULL) — healthy→degraded 전이 시각.
   - `cli_consecutive_failures`(INTEGER DEFAULT 0) — 영속 연속 실패 횟수(in-process `_cli_failure_count`와 **독립**, REQ-052-A5).
   - `cli_degraded_notified_at`(TIMESTAMPTZ NULL) — **별도** REQ-052-B throttle 클럭(SPEC-031 `halt_notified_at` 동형).
   - `strict_cost_zero_mode`(BOOLEAN DEFAULT false) — ADR-001 옵트인 플래그.
   - 모든 컬럼 additive, idempotent(information_schema 가드, mig 023 house style) + audit_log INSERT.

2. **degraded 감지·영속 통합** (REQ-052-A1/A1b/A2/A3/A3b/A4/A5) — `personas/base.py`
   - in-process `_cli_failure_count`(L536)는 자동전환(REQ-FALLBACK-06-4) 신호로 **그대로 두고**, 영속 degraded latch(`cli_degraded`)·영속 카운터(`cli_consecutive_failures`)를 system_state에 **별도** 둔다(ADR-005 — 두 카운터 독립).
   - `_record_cli_failure`(L530-564): 영속 카운터 증가 + 임계 도달 시 `cli_degraded=True` **latch**(REQ-052-A1). **[HARD·D4]** L564의 in-process `_cli_failure_count=0` 리셋은 영속 latch에 영향을 주지 않음 — latch는 REQ-052-A2(성공/하트비트)에서만 해제(flap 방지, ADR-005/REQ-052-A5).
   - CLI 성공(`_reset_cli_failures`, L524) + 워처 신선 복귀: `cli_degraded=False` + 영속 카운터 0 (REQ-052-A2).
   - DB 실패 시 graceful(REQ-052-A4) — fail-open 계약 보존.
   - `is_cli_mode_active()`(L759) stale 분기에서도 degraded 마킹(직접경로 사각지대 해소, REQ-052-A1b).

3. **조기경고 throttle** (REQ-052-B, ADR-003) — SPEC-031 패턴 차용
   - `circuit_breaker.maybe_notify_halt(cooldown_seconds, now_provider)`(L35-70) 구조를 모델로 `maybe_send_cli_degraded_alert(cooldown_seconds=None, now_provider=None)` 신설. `now_provider`는 `Callable[[], datetime]` 주입 clock seam(bare 값 아님).
   - `cli_degraded_notified_at` NULL이거나 `now - last >= cooldown`이면 발사+스탬프, 아니면 throttle. 기본 쿨다운 `CLI_DEGRADED_ALERT_COOLDOWN_SECONDS=3600`(1h, ADR-003/OQ-3).
   - degraded 해제 시 `cli_degraded_notified_at=NULL` 리셋.
   - **[D7] 교체 대상 명시**: `_record_cli_failure`의 **base.py L541 `tg.system_briefing("CLI fallback", ...)`(per-failure 무throttle) 한 곳만** 이 throttled alert로 **대체**(REQ-052-B3). **L557/L558 `tg.system_briefing("CLI auto-disabled", ...)`(자동전환 알림)은 절대 건드리지 않고 보존** — throttle 대상 아님.

4. **비용0 강제(옵트인)** (REQ-052-C, ADR-001) — 폴백/직접 경로 가드
   - `call_persona_via_cli` 폴백 분기(L658-660) 진입 전 `strict_cost_zero_mode` 검사 → ON이면 유료 `call_persona` 호출 대신 defer(스킵) + 알림 + 구조화 로그.
   - `decision.py`(및 동형 페르소나) 직접 분기(L103-148)에서 strict ON + cli 활성이면 직접 유료 호출 대신 defer.
   - 뉴스 `_call_haiku`(analyzer.py L213) 경로도 동일 strict 가드 적용(또는 `block_if_cli_only_mode`를 strict-aware로 확장).
   - 기본 OFF에서는 **모든 분기 무변경**(REQ-052-C2, NFR-1).

5. **폴백 가시성** (REQ-052-D)
   - 세 경로 공통 구조화 로그 헬퍼(`persona`/`path`/`model`/`reason`) — grep/집계 가능 스키마.
   - (Optional) 일일 폴백 발동 횟수 집계 — audit 또는 일일 리포트 훅(best-effort, ADR-004).

### 마일스톤 (우선순위 기반, 시간 추정 없음)

- **M1 (Priority High)**: 재현 테스트 먼저 — `claude -p` exit=0 빈출력(CLICallError) 연속 N회 주입 → 현재 코드가 조용히 Haiku 폴백/직접호출로 새는 것 확인(RED). 직접경로(decision.py stale 분기)에 감지·알림 부재도 RED로 고정.
- **M2 (Priority High)**: REQ-052-A1/A1b/A2/A3/A3b/A4/A5 구현 — degraded 영속 통합(mig 034 + base.py 영속 latch/카운터, in-process 카운터와 **독립** = ADR-005), 세 경로 단일소스 참조. DB 실패 graceful(A4). **flap-방지 테스트(AC-1c, REQ-052-A5)** 선행 고정 — in-process L564 리셋에도 degraded latch 유지. M1 감지 테스트 GREEN.
- **M3 (Priority High)**: REQ-052-B 구현 — SPEC-031 동형 쿨다운 경고(`maybe_send_cli_degraded_alert`, 주입 clock), 무쿨다운 매실패 알림 대체. throttle 결정적 테스트(FakeClock).
- **M4 (Priority High)**: REQ-052-C 구현 — `strict_cost_zero_mode` 옵트인. ON 시 폴백/직접/뉴스 차단·defer + 알림. **기본 OFF 회귀 테스트(기존 폴백 동작 불변)** 우선 고정(NFR-1).
- **M5 (Priority Medium)**: REQ-052-D — 세 경로 구조화 로그 통일 + (Optional)일일 집계.
- **M6 (Priority Medium)**: SPEC-016/030/034/043 회귀 검증 — strict OFF 기본에서 기존 테스트 전부 GREEN, 자동전환(REQ-FALLBACK-06-4) 동작 보존 단언.

## 위험 (Risks)

- **R1 (기본동작 회귀)**: strict 가드/throttle 추가가 기본 OFF 경로를 건드리면 SPEC-016 폴백 회귀 → **strict OFF에서 분기 무변경 테스트(NFR-1)를 M4 선행 가드로 고정**. ADR-001 [HARD] 기본 OFF.
- **R2 (degraded latch flap — D4 핵심)**: 영속 degraded를 in-process `_cli_failure_count`에서 파생하면 자동전환 시 L564 카운터 리셋으로 healthy↔degraded **flap** → **[HARD] ADR-005/REQ-052-A5로 두 카운터를 디커플링**(latch는 REQ-052-A2에서만 해제), flap-방지 테스트(AC-1c)로 가드. 다중 워커 race는 SPEC-031 `halt_notified_at` 동형 단조 갱신으로 완화.
- **R3 (DB 실패 wedge)**: degraded 영속 DB 접근 실패가 사이클을 막으면 fail-open 계약 위반 → REQ-052-A4 graceful 강제, `is_cli_only_mode` fail-open(False) 패턴 준수(AC로 단언).
- **R4 (뉴스 경로 가드 일관성)**: `_call_haiku`의 `block_if_cli_only_mode`가 `cli_only_mode`만 보고 `cli_personas_enabled` 자동전환과 어긋나는 사각지대 → REQ-052-A3 단일소스 + strict 가드를 세 경로 동일 적용으로 해소.
- **R5 (알림 대체 누락)**: 무쿨다운 알림을 throttle로 대체하며 자동전환(L557) 알림까지 throttle돼 중요 신호 손실 → REQ-052-B3 단언(자동전환 알림 보존).

## 종속성

- SPEC-016(폴백 허용된 예외) — 기본 OFF에서 의도 보존, 약화 금지.
- SPEC-031(쿨다운 throttle 패턴) — `maybe_notify_halt(cooldown_seconds, now_provider)`(circuit_breaker.py L35-70) 구조 차용.
- SPEC-043(graceful defer) — strict defer가 다음 슬롯 멱등 재처리 패턴과 정합.
- SPEC-030/034(CLI 비용0) — 본 SPEC이 비용0 전제 붕괴를 방어.
- mig 최신 033 → 본 SPEC 034.

## 위임

- 구현: manager-tdd (quality.yaml development_mode 따름).
- 브랜치/PR: manager-git (OQ-5 확정 후 — 현 브랜치 stale 가능성 확인).
