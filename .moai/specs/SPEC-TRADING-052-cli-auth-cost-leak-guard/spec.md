---
id: SPEC-TRADING-052
version: 0.2.0
status: draft
created: 2026-06-16
updated: 2026-06-16
author: oni
priority: high
labels: ["trading", "cli", "cost", "reliability", "observability", "backend"]
---

# SPEC-TRADING-052: CLI 인증 끊김으로 인한 유료 API 비용 누수 방지 — degraded 감지·조기경고·비용0 강제(옵트인)

## HISTORY

- 0.2.0 (2026-06-16): plan-auditor REVISE(0.82) 7개 결함 교정 — 인용/정밀도 오류만, 아키텍처(3경로 커버리지·ADR-001 회귀 방화벽·fail-open)는 검증 통과로 불변. (D1) 가공 함수명 교정 — SPEC-031 throttle 실명은 `circuit_breaker.maybe_notify_halt(cooldown_seconds, now_provider)`(circuit_breaker.py L35; `maybe_send_halt_briefing`는 존재하지 않음, grep 0). 신규 헬퍼 시그니처 `maybe_send_cli_degraded_alert(cooldown_seconds=None, now_provider=None)`로 정정(now_provider는 `Callable[[], datetime]`). (D2) REQ-052-A1의 복합 EARS(두 WHEN + 밀반입 second shall)를 A1(연속 빈출력/CLICallError 트리거)·A1b(워처 하트비트 stale 트리거)로 분리. (D3) REQ-052-A3의 두 chained shall을 A3(단일소스 mandate)·A3b(3경로 공유참조 mandate)로 분리. (D4 — 가장 구현결정적) 코드 검증: `_record_cli_failure`(base.py L548-564)가 자동전환 발동 순간 `_cli_failure_count`를 L564에서 0으로 리셋 → 영속 degraded가 이 카운터에서 파생되면 자동전환 임계에서 **flap**. 신규 [HARD] REQ-052-A5로 in-SPEC 정규 해결(OQ-4): 영속 degraded는 in-process 자동전환 카운터와 **독립**, 감지 임계에서 True로 latch하고 CLI 성공/하트비트 신선 시(REQ-052-A2)에만 해제, L564 리셋과 디커플링. flap-방지 인수 테스트(AC-1c) 추가. (D5) A1 normative에서 "in-process 카운터 승격" HOW를 제거(ADR-002/plan으로 이미 이관). (D6) OQ-2(스키마) in-SPEC 확정(ADR-002): degraded-latch(boolean/timestamp) + 별도 throttle 타임스탬프 `cli_degraded_notified_at` 분리, mig 034 additive — AC-2/EC-2가 컬럼명을 literal로 단언 가능. (D7) REQ-052-B3/plan에서 교체 대상 명시 — base.py L541의 per-failure 무throttle `system_briefing`만 throttled alert로 대체, L557/L558 자동전환 알림은 보존. ADR-003 쿨다운 기본값 1시간(SPEC-031 6h보다 짧게, 무음 크레딧 소진의 시간민감성) named tunable로 확정(OQ-3). AC-5b가 보존해야 할 SPEC-016 폴백 테스트 모듈명 `tests/personas/test_fallback_consistency.py` 명시. OQ-2/3/4 RESOLVED, OQ-1/5 유지. **코드 미구현(plan only).**
- 0.1.0 (2026-06-16): 최초 초안. 2026-06-16 사고(호스트 claude 구독 세션 OAuth 만료 → `claude -p`가 exit=0인데 0바이트 출력으로 일관 실패 → CLI 비용0 경로 전면 붕괴 → 설계된 폴백/직접호출이 유료 Anthropic API(`claude-haiku-4-5`)로 새어 **크레딧 177건 소진(08:15~16:00)**)를 근거로 함. 코드 검증으로 확인된 핵심 사실: (1) 기존 `_record_cli_failure`(base.py L530, REQ-FALLBACK-06-3/4)는 **in-process 모듈 전역 카운터**(`_cli_failure_count`)로 3연속 실패 시 `cli_personas_enabled=False` 자동전환 + 매 실패마다 무(無)쿨다운 텔레그램 — 그러나 이 경로는 **유료 폴백을 *막지* 않고 유료 폴백을 *유발*한 뒤** 카운트한다. (2) 자동전환이 끝나면 `is_cli_mode_active()`(base.py L759)가 False가 되어 `decision.py` L103-148이 **처음부터 직접 `call_persona(sonnet/haiku)` 유료 호출** — 이 직접경로엔 실패 카운터·알림이 **전혀 없다**(조용한 누수의 본체). (3) 뉴스 `_call_haiku`(analyzer.py L213)의 `@block_if_cli_only_mode` 가드는 `cli_only_mode` 플래그에만 걸리고 `cli_personas_enabled`와 독립이라 사각지대 존재. 시장 중립, paper/live 동일 경로 유지. brownfield delta. **코드 미구현(plan only).**

---

## 배경 (WHY)

비용0 운영의 전제는 호스트 claude 구독 세션이 살아 있어 모든 페르소나/뉴스/리포트 LLM 호출이 `claude -p`(정액 구독)로 처리된다는 것이다(SPEC-015/030/034). 이 전제가 **조용히** 깨지면 — 호스트 OAuth가 만료되어 `claude -p`가 exit=0인데 응답이 비어 돌아오면 — 설계상 안전망인 폴백/직접 API 경로가 **유료 Anthropic API로 자동 우회**한다. 운영자는 여전히 "비용0"으로 믿고 있는데 크레딧이 시간당 수십 건씩 소모된다.

### 진단된 사고 (재조사 불필요 — 코드·로그 검증 완료)

2026-06-16, 호스트 claude CLI 구독 세션 인증 만료(chrome에 `claude.com/cai/oauth/authorize` 재로그인 URL 노출). 증상:

| 증상 | 발생 | 비고 |
|------|------|------|
| `persona call failed` | 26건 (09~14시 매시간 4건) | 전부 `credit balance too low` (HTTP 400) — 유료 크레딧 소진 |
| 유료 크레딧 소비 | 177건 (08:15~16:00) | `claude-haiku-4-5` 폴백/직접호출로 새어나감 |
| `daily_screen` 로그 | — | `Claude Code failed (exit=0)` (빈 출력 일관) |
| 호스트 `persona_watcher.log` | 오늘 2줄뿐 | 정규 macro/micro/decision/risk 실행 누락(워처 사실상 실명) |
| `daily_report`/`news` Haiku 폴백 | 실패 | 크레딧 소진 후 폴백마저 실패 |

운영자는 비용0으로 믿었으나 조용히 크레딧이 소모되었다. 즉 **"감지 없음 + 경고 없음 + 비용 차단 없음"** 3중 공백이 이 사고를 키웠다.

### 진단된 누수 경로 (코드 검증 — 3경로)

호스트 CLI가 죽으면 비용이 새는 경로는 균일하지 않다:

1. **페르소나 폴백 경로** — `call_persona_via_cli`(base.py L640) `except (CLITimeoutError, CLICallError)` → `_record_cli_failure`(L643) → **Haiku 유료 폴백**(L660). 이 경로엔 카운터·알림이 있으나 **카운터가 in-process 모듈 전역**(`_cli_failure_count`, L520)이라 프로세스 재시작·다중 워커 간 공유 안 됨, 알림은 **매 실패마다 무쿨다운**(스팸). 그리고 폴백을 *막지* 않고 *유발*한다.
2. **직접 API 경로(누수 본체)** — `_record_cli_failure`가 3연속 후 `cli_personas_enabled=False`로 자동전환(L548-551)하면, 이후 `is_cli_mode_active()`(L759)가 False를 반환 → `decision.py` L103-148의 `if/else`가 **처음부터 직접 `call_persona(...)` 유료 호출**. 이 직접경로엔 **실패 카운터·degraded 마킹·알림이 전무**. 워처 하트비트 stale(>60s, cli_bridge.py L36)일 때도 동일하게 직접 API로 빠진다.
3. **뉴스 `_call_haiku` 경로** — analyzer.py L213 `@block_if_cli_only_mode`는 `cli_only_mode`(SPEC-016) OR `cli_personas_enabled`(SPEC-015)를 보는 `is_cli_only_mode()`(L80)에 의존하나, 자동전환이 `cli_personas_enabled`만 끄고 `cli_only_mode`가 별도로 켜져 있지 않으면 가드가 통과되어 유료 Haiku 호출.

이 SPEC은 세 경로를 통합한 **degraded 감지(영속) + 조기경고(쿨다운) + 비용0 강제(옵트인)**로 메운다. 호스트 재인증 자체는 운영자 수동 조치(코드 범위 밖)다.

---

## 핵심 아키텍처 제약 (HARD)

- [HARD] SPEC-016의 "Haiku 폴백은 유일하게 허용된 예외"(base.py L110-111) 의도를 **기본값에서 보존**한다. 폴백을 *차단*하는 동작은 새 플래그(`strict_cost_zero_mode`, 기본 OFF) 옵트인으로만 활성화한다 — 기본 동작 변경은 SPEC-016/030/034 회귀다.
- [HARD] 시장 중립(market-neutral). degraded 감지·경고·비용차단 로직에 한국 시장 종속 상수를 새로 도입하지 않는다(네트워크/운영 파라미터만).
- [HARD] paper/live 동일 코드 경로. cli_degraded 판정·경고·strict 차단은 시장 모드와 무관하게 동일하게 동작한다.
- [HARD] `is_cli_only_mode()`의 fail-open(DB 장애 시 False) 계약(base.py L95-100)을 약화하지 않는다 — DB 문제가 유일 작동 경로를 wedge하면 안 된다. degraded 영속화도 DB 장애 시 graceful.
- [HARD] 결정적 테스트를 위한 주입형 clock seam을 사용한다(SPEC-031 `circuit_breaker.py` L36 `now`/`cooldown_seconds` 주입 패턴, SPEC-051 FakeClock과 호환).

---

## 기존 시스템 컨텍스트 (BROWNFIELD)

### [EXISTING] 그대로 재사용·확장하는 자산

| 영역 | 위치 | 역할 |
|------|------|------|
| 페르소나 CLI 폴백 | `personas/base.py` `call_persona_via_cli` L640-687 | CLI 실패 시 Haiku 폴백(허용된 예외) |
| 실패 카운터(in-process) | `personas/base.py` `_record_cli_failure` L530-564, `_cli_failure_count` L520 | REQ-FALLBACK-06-3/4: 3연속 자동전환 + 매실패 알림 |
| 모드 단일소스 | `personas/base.py` `is_cli_only_mode()` L80-101 | system_state 읽기, fail-open(False) |
| 가드 데코레이터 | `personas/base.py` `block_if_cli_only_mode` L104-139 | news `_call_haiku`, deprecated `_llm_text`에만 적용 |
| 워처 liveness | `personas/base.py` `is_cli_mode_active()` L759-787 + `cli_bridge.py` `is_watcher_alive()` L82-91 (`HEARTBEAT_STALE_SECONDS=60.0` L36) | 하트비트 stale면 직접 API로 분기 |
| 직접 API 분기 | `personas/decision.py` L103-148 if/else (other personas 동형) | cli 비활성/stale → 유료 직접호출 |
| 폴백 모델 단일소스 | `personas/base.py` `_HAIKU_FALLBACK_MODEL="claude-haiku-4-5"` L36 | 폴백/직접 유료 모델 |
| 쿨다운 throttle 패턴 | `risk/circuit_breaker.py` `maybe_notify_halt(cooldown_seconds, now_provider)` L35-70, `reset()` L87-90이 `halt_notified_at`를 NULL로 리셋, `system_state.halt_notified_at`(mig 023) | SPEC-031: 영속 last-notified + 첫 트리거 즉시 발사 + 쿨다운 throttle + reset NULL 리셋 |
| 호스트 러너 | `scripts/persona_watcher.sh` L98-137 | `claude -p` exit≠0/빈응답 시 `error:"cli_failed (exit=N)"` 마킹 |

[EXISTING] 마이그레이션 최신 = **033**(`033_edge_hardening.sql`). 번호 027/030 결번(과거 정리). 본 SPEC이 system_state에 degraded 상태/throttle 타임스탬프 컬럼을 추가하면 **다음 가용 번호 034**.

### 현재 격차 (이 SPEC이 메우는 것)

- **degraded 상태가 영속·관측 불가**: 실패 카운터가 in-process 모듈 전역(`_cli_failure_count`)이라 재시작·다중 워커 간 공유 안 됨, system_state에 "CLI 불건강" 1급 상태가 없어 대시보드/알림/strict 차단이 참조할 단일소스 부재.
- **직접 API 경로에 감지·알림 전무**: `decision.py` 직접호출 분기(누수 본체)는 `_record_cli_failure`를 거치지 않아 조용히 유료 호출. 워처 stale도 동일.
- **유료 폴백 발동의 조기경고 부재(또는 스팸)**: 페르소나 폴백 알림은 매 실패마다 발사(폭주), 직접경로/뉴스경로는 무알림. 운영자가 "지금 크레딧이 새고 있다"를 적시에 못 받음.
- **비용0 강제 수단 부재**: cli_only_mode라도 폴백/직접 유료 호출을 막을 옵트인 정책이 없음 — 운영자가 "비용0 보장"을 선택할 수단이 없다.
- **폴백 가시성·사후집계 부재**: 유료 폴백 발동이 구조화 로그/일일 집계로 남지 않아 사후 비용 파악 곤란.

---

## 요구사항 (EARS)

요구사항 모듈 4개: A(degraded 감지·영속 — A1/A1b/A2/A3/A3b/A4/A5) + B(조기경고·쿨다운) + C(비용0 강제·옵트인) + D(폴백 가시성) + NFR.

### REQ-052-A: CLI degraded 감지·영속 [MODIFY]

`@MX:SPEC SPEC-TRADING-052 REQ-052-A`

- **REQ-052-A1** (Event-Driven): 호스트 CLI 호출이 연속 N회(임계 튜닝 가능, 기본은 기존 `_CLI_AUTO_DISABLE_THRESHOLD=3` 재사용) **exit=0 빈출력 또는 `CLICallError`/`CLITimeoutError`로 실패**하면(WHEN), 시스템은 system_state에 **"CLI degraded" 상태**(degraded 전이 시각·연속 실패 횟수 포함)를 영속 기록해야 한다(shall).
- **REQ-052-A1b** (Event-Driven): 워처 하트비트가 stale(`HEARTBEAT_STALE_SECONDS` 초과)이면(WHEN), 시스템은 동일한 system_state "CLI degraded" 상태를 영속 기록해야 한다(shall) — 직접 API 경로가 빠지는 사각지대(REQ-052-A1과 같은 단일 degraded 소스)를 마킹한다.
- **REQ-052-A2** (Event-Driven): 호스트 CLI 호출이 성공하거나 워처 하트비트가 다시 신선해지면(WHEN), 시스템은 degraded 상태를 해제(healthy 복귀)하고 연속 실패 카운터를 0으로 리셋해야 한다(shall). (SPEC-031 `reset()`이 throttle 클럭을 NULL로 되돌리는 패턴과 동형)
- **REQ-052-A3** (Ubiquitous): degraded 판정·영속은 단일 진실소스(system_state)를 통해 이뤄져야 한다(shall).
- **REQ-052-A3b** (Ubiquitous): 페르소나 폴백 경로·직접 API 경로·뉴스 `_call_haiku` 경로는 **동일한 degraded 상태(REQ-052-A3의 단일소스)**를 참조·갱신해야 한다(shall) — 세 경로의 사각지대를 통합한다.
- **REQ-052-A4** (Unwanted): degraded 영속 기록을 위한 DB 접근이 실패하면(IF), 시스템은 유일 작동 경로를 wedge해서는 안 되며(then shall not), 기존 fail-open 계약을 보존하여 degraded 마킹 실패가 사이클 자체를 막지 않도록 해야 한다(shall) — 단 이 경우 graceful 로그를 남긴다.
- **REQ-052-A5** (Ubiquitous) **[HARD]**: 영속 degraded 상태는 in-process 자동전환 카운터(`_cli_failure_count`, base.py)와 **독립**이어야 한다(shall). 영속 degraded는 감지 임계(REQ-052-A1/A1b) 도달 시 True로 **latch**하고, **오직** CLI 호출 성공 또는 워처 하트비트 신선 복귀 시(REQ-052-A2)에만 False로 해제되어야 한다(shall). 자동전환(`cli_personas_enabled=False`)이 발동하며 in-process 카운터를 0으로 리셋하더라도(base.py `_record_cli_failure`), 영속 degraded는 그 리셋의 영향을 받아 **flap(토글)해서는 안 된다**(shall not). (WHY: `_record_cli_failure`는 자동전환 임계 도달 순간 카운터를 0으로 리셋한다 — 영속 degraded가 이 카운터에서 파생되면 자동전환 임계에서 healthy↔degraded가 진동한다. OQ-4 in-SPEC 정규 해결 = ADR-005.)

### REQ-052-B: 조기경고·쿨다운 throttle [NEW]

`@MX:SPEC SPEC-TRADING-052 REQ-052-B`

- **REQ-052-B1** (Event-Driven): 시스템이 healthy → degraded로 전이하거나(WHEN), 유료 API 폴백/직접호출이 **실제로 발동**하면(WHEN), 시스템은 운영자에게 텔레그램 경고("CLI 불건강 — 유료 API로 비용 누수 중, 호스트 재인증 필요")를 보내야 한다(shall). 경고는 누수 사실과 권장 조치(호스트 재인증)를 명시한다.
- **REQ-052-B2** (Unwanted): degraded가 지속되어 매 사이클 폴백이 발동하더라도(IF), 시스템은 경고를 매 사이클마다 보내 폭주시켜서는 안 되며(then shall not), **쿨다운 throttle**(SPEC-031 패턴: 영속 last-notified 타임스탬프, 기본 쿨다운 튜닝 가능)로 한 에피소드당 첫 발생 즉시 + 쿨다운 간격으로 제한해야 한다(shall). degraded 해제(healthy 복귀) 시 throttle 클럭을 리셋하여 다음 에피소드 첫 발생이 즉시 알림되게 한다(shall).
- **REQ-052-B3** (Ubiquitous): 조기경고는 기존 `_record_cli_failure`의 **per-failure 무쿨다운 알림 — 구체적으로 base.py L541의 `tg.system_briefing("CLI fallback", ...)`(REQ-FALLBACK-06-3)** 한 곳만 throttled alert로 **대체**하여 스팸을 제거해야 한다(shall). 자동전환 시 발사되는 **CLI auto-disabled 알림(base.py L557/L558, `tg.system_briefing("CLI auto-disabled", ...)`, REQ-FALLBACK-06-4)은 그대로 보존**되어야 하며 throttle 대상이 아니다(shall not throttle). (알림 throttle만 변경, 자동전환 정책 및 그 알림은 불변)

### REQ-052-C: 비용0 강제(strict_cost_zero_mode) — 옵트인 [NEW, 정책 = ADR-001]

`@MX:SPEC SPEC-TRADING-052 REQ-052-C`

- **REQ-052-C1** (State-Driven): `strict_cost_zero_mode`가 켜져 있고(WHILE) cli_only_mode/cli_personas_enabled가 활성인 동안, 유료 API 폴백 또는 직접 유료 호출이 발동하려 하면(WHEN), 시스템은 그 유료 호출을 **차단(defer)**하고 해당 페르소나/뉴스 사이클을 건너뛰어야 한다(shall) — 다음 호스트-CLI 슬롯에서 멱등 재처리(SPEC-043 graceful defer 패턴과 동형). 크레딧이 새지 않는다.
- **REQ-052-C2** (Unwanted): `strict_cost_zero_mode`가 **꺼져 있으면**(기본값 OFF) (IF), 시스템은 기존 SPEC-016 동작을 그대로 유지하여 Haiku 폴백을 허용해야 하며(then shall), 본 SPEC이 기본 폴백 가용성을 약화해서는 안 된다(shall not). (기본값 보존 = SPEC-016/030/034 회귀 0)
- **REQ-052-C3** (Event-Driven): strict 모드가 유료 호출을 차단(defer)할 때마다(WHEN), 시스템은 운영자에게 그 사실(어떤 페르소나/슬롯이 비용0 보장을 위해 스킵됐는지)을 REQ-052-B 쿨다운 throttle과 정합되게 알리고(shall) 구조화 로그를 남겨야 한다(shall) — 사이클이 조용히 비는 것을 방지.

### REQ-052-D: 폴백 가시성·사후집계 [NEW]

`@MX:SPEC SPEC-TRADING-052 REQ-052-D`

- **REQ-052-D1** (Event-Driven): 유료 API 폴백 또는 직접 유료 호출이 발동할 때마다(WHEN), 시스템은 구조화 로그(페르소나/경로/모델/사유 포함)를 남겨야 한다(shall) — 세 경로(폴백/직접/뉴스)가 동일 스키마로 기록되어 사후 grep/집계가 가능해야 한다(shall).
- **REQ-052-D2** (Optional): 운영자가 사후 비용을 파악할 수 있도록(WHERE 가능), 시스템은 일일 폴백 발동 횟수와 추정 유료 호출 건수를 집계(예: 일일 리포트 또는 audit 집계)할 수 있어야 한다(may) — 추정 비용 산정은 best-effort(정확한 청구액 아님).

### REQ-052-NFR: 비기능 요구사항

- **REQ-052-NFR-1** (Ubiquitous): 구현은 SPEC-016/030/034/043/051 회귀를 0으로 유지해야 한다(shall). 특히 `strict_cost_zero_mode` 기본 OFF에서 기존 폴백/직접경로 동작과 관련 테스트가 전부 GREEN을 유지한다(pre-existing 6건 제외).
- **REQ-052-NFR-2** (Ubiquitous): 모든 신규 동작은 TDD 재현 우선으로 개발해야 한다(shall) — 연속 빈출력/CLICallError 주입으로 degraded 전이를, 클럭 주입으로 쿨다운 throttle을, strict 모드에서 폴백 차단·defer를 결정적 테스트로 작성한다. (CLAUDE.md Rule 4)
- **REQ-052-NFR-3** (Ubiquitous): degraded 상태/throttle 타임스탬프 영속이 DB 스키마 변경을 요구하면 **다음 가용 마이그레이션 번호(034)**를 사용해야 하며(shall), 마이그레이션은 idempotent(information_schema 가드, mig 023 house style)여야 한다(shall).
- **REQ-052-NFR-4** (Ubiquitous): degraded 감지·경고·strict 차단 로직은 시장 중립을 유지하며 한국 시장 종속 상수를 새로 도입하지 않아야 한다(shall not).

---

## Exclusions (What NOT to Build) — 범위 제외

이 섹션은 [HARD] 필수이며 범위 폭주를 막는다.

1. **호스트 claude 재인증 자동화 제외 [경계]**: 호스트 OAuth 재로그인은 **운영자 수동 조치**(코드 범위 밖)다. 본 SPEC은 "감지·경고·비용차단"에만 집중한다. 인증 토큰 자동 갱신·재로그인 봇·세션 키 회전은 구현하지 않는다.
2. **기본 폴백 동작 변경 금지 [HARD]**: `strict_cost_zero_mode` 기본값은 OFF. SPEC-016의 "Haiku 폴백 허용된 예외"를 기본에서 보존한다. 폴백 차단은 옵트인으로만.
3. **자동전환 정책 재설계 제외**: 기존 REQ-FALLBACK-06-4(3연속 → `cli_personas_enabled=False` 자동전환)의 임계·동작을 변경하지 않는다. 본 SPEC은 알림 throttle만 바꾸고 degraded 상태를 영속화·통합한다.
4. **유료 API 청구 정밀 집계 제외**: REQ-052-D2는 best-effort 추정(발동 횟수 기반)이며 Anthropic 청구 API 연동·정확한 USD 산정은 범위 밖.
5. **circuit-breaker/halt 연동 추가 금지**: CLI degraded를 거래 halt_state나 회로차단기에 연결하지 않는다(별도 정책 영역). degraded는 비용 보호 영역, halt는 손실 보호 영역으로 분리 유지.
6. **CLI 브리지/워처 프로토콜 재설계 제외**: 호스트 `persona_watcher.sh`·call/result 파일 스키마·하트비트 메커니즘을 재설계하지 않는다. 기존 신호(exit code, 빈응답, 하트비트 stale)를 소비만 한다.
7. **직접 API 경로 제거 제외**: `decision.py` 등의 직접 `call_persona` 분기 자체를 없애지 않는다(strict OFF에서는 정당한 안전망). strict ON에서 차단·defer만 추가한다.

---

## ADR (Architecture Decision Records)

### ADR-001: strict_cost_zero_mode 기본값 OFF + SPEC-016 정책 충돌 처리 [핵심]

- **충돌**: SPEC-016 REQ-016-1-3은 Haiku 폴백을 "유일하게 허용된 예외"로 **의도적으로** 가드에서 제외했다(base.py L110-111). 즉 폴백 가용성은 SPEC-016의 설계 의도다. 반대로 본 사고는 그 폴백이 *조용히* 유료로 새는 것이 문제였다. "비용0 보장"과 "폴백 가용성(사이클 누락 방지)"은 직접 충돌한다.
- **결정 [HARD]**: 둘을 **양립 불가 정책으로 보고 새 플래그 `strict_cost_zero_mode`(system_state, 기본 OFF)로 옵트인**하게 한다. 기본값 OFF에서는 SPEC-016 동작이 **바이트 단위로 보존**(폴백 허용) — 회귀 0. 운영자가 ON으로 켜면 폴백/직접 유료 호출을 차단·defer(REQ-052-C1)하여 비용0을 보장하되 그 사이클은 건너뛴다(데이터 공백 감수).
- **근거**: (1) 기본 동작을 바꾸면 SPEC-016/030/034 회귀 — 절대 회피. (2) 트레이드오프("비용0 절대보장 vs 폴백으로 사이클 유지")는 **운영자의 가치판단**이지 코드가 임의 결정할 사안이 아니다 → OQ-1로 운영자에게 명시적 선택을 받는다. (3) 옵트인 플래그는 가역적(언제든 OFF)이고 A/B 관측 가능.
- **상태**: 확정(옵트인 설계) — 기본값 OFF는 [HARD]. ON 시 동작·기본 쿨다운 값은 OQ-1/OQ-3.

### ADR-002: degraded 상태 영속화 — in-process 카운터 승격 vs 신규 상태

- **결정**: 기존 in-process `_cli_failure_count`(base.py L520, 재시작·다중워커 간 비공유)를 **system_state 영속 컬럼으로 승격·통합**한다. degraded 전이 시각 + 연속 실패 횟수 + (선택)throttle last-notified를 system_state에 둔다.
- **근거**: (1) 세 누수 경로(폴백/직접/뉴스)가 **단일 진실소스**를 참조해야 통합 감지·strict 차단·대시보드 노출이 가능(REQ-052-A3). (2) SPEC-031이 `halt_notified_at`를 system_state에 둬 재시작에도 throttle이 생존하는 검증된 패턴을 그대로 차용(mig 023). (3) in-process 전역은 다중 워커/컨테이너 재시작에서 신뢰 불가 — 사고 당시 워처/스케줄러 분리 프로세스라 카운터가 분산됐을 가능성.
- **호환**: `is_cli_only_mode()`/`is_cli_mode_active()` 기존 시그니처 보존, 신규 컬럼은 `get_system_state()` dict에 추가 키로만 노출. 컬럼 부재 시 graceful(SPEC-050 status 패널 `cool_down_active` graceful 폴백 선례).
- **결정(스키마 확정 — OQ-2 RESOLVED)**: degraded-latch와 throttle 클럭을 **분리**한 다음 컬럼들을 mig 034에 **additive**로 추가한다:
  - `cli_degraded`(BOOLEAN DEFAULT false) — degraded latch 상태(REQ-052-A5가 단조 latch/clear를 규정).
  - `cli_degraded_since`(TIMESTAMPTZ NULL) — healthy→degraded 전이 시각(관측·집계용).
  - `cli_consecutive_failures`(INTEGER DEFAULT 0) — 영속 연속 실패 횟수(in-process `_cli_failure_count`와 독립; REQ-052-A5).
  - `cli_degraded_notified_at`(TIMESTAMPTZ NULL) — **별도** throttle 클럭(REQ-052-B, SPEC-031 `halt_notified_at` 동형). throttle 타임스탬프를 degraded latch와 분리하므로 알림 throttle이 latch 상태를 오염시키지 않는다.
  - `strict_cost_zero_mode`(BOOLEAN DEFAULT false) — ADR-001 옵트인 플래그.
- **근거(분리 선택)**: degraded latch(REQ-052-A5)와 알림 throttle(REQ-052-B)은 서로 다른 라이프사이클(latch는 성공/하트비트로, throttle은 쿨다운으로 해제)이라 별도 컬럼이 결합도를 낮춘다. AC-2/EC-2가 `cli_degraded_notified_at`를 literal로 단언할 수 있게 grounding한다.
- **상태**: 확정(OQ-2 RESOLVED). 모든 컬럼 additive·idempotent(information_schema 가드), 컬럼 부재 시 graceful 폴백.

### ADR-003: 조기경고 throttle = SPEC-031 쿨다운 패턴 재사용

- **결정**: REQ-052-B 경고 throttle을 SPEC-031 `maybe_notify_halt(cooldown_seconds, now_provider)`(circuit_breaker.py L35-70)과 **동형**의 신규 헬퍼 `maybe_send_cli_degraded_alert(cooldown_seconds=None, now_provider=None)`로 구현한다 — 영속 last-notified 타임스탬프(`cli_degraded_notified_at`), 첫 에피소드 발생 즉시 발사, 쿨다운 간격 throttle, healthy 복귀 시 NULL 리셋. `now_provider`는 `Callable[[], datetime]` 테스트 seam(bare datetime 값이 아니라 호출 가능 provider — circuit_breaker.py L37/L59와 동형).
- **근거**: SPEC-031이 정확히 같은 문제(degraded 상태 지속 중 매 사이클 텔레그램 폭주)를 영속 throttle로 이미 해결했고 라이브 검증됐다(메모리 기록). 새 메커니즘을 발명하지 않고 검증된 패턴 재사용 = 단순성·일관성. 기존 `_record_cli_failure`의 무쿨다운 알림(base.py L541)을 이 throttle로 대체(REQ-052-B3).
- **결정(쿨다운 기본값 — OQ-3 RESOLVED)**: 기본 쿨다운 = **1시간(`CLI_DEGRADED_ALERT_COOLDOWN_SECONDS = 3600`)**, named tunable 상수(`cooldown_seconds` 주입 seam으로 override 가능, 운영자 튜닝 가능). SPEC-031의 6h(`HALT_NOTIFY_COOLDOWN_SECONDS=21600`)보다 **짧게** 잡은 이유: 무음 크레딧 소진은 분 단위로 누적되는 시간민감 사고라 더 잦은 리마인드가 정당하다(첫 알림은 에피소드 시작 즉시, 반복 리마인드는 1h 간격). circuit_breaker.py L23 상수와 동일한 "코드 default가 런타임 단일 진실소스" house style을 따른다.
- **상태**: 확정(패턴 재사용 + 1h 기본값, OQ-3 RESOLVED). 운영자 튜닝 가능.

### ADR-005: degraded latch vs 자동전환 카운터 독립 (D4 / OQ-4 RESOLVED) [핵심·구현결정적]

- **충돌(코드 검증)**: `_record_cli_failure`(base.py L548-564)는 자동전환 임계(`_CLI_AUTO_DISABLE_THRESHOLD=3`) 도달 시 `cli_personas_enabled=False`로 전환한 직후 **L564에서 `_cli_failure_count`를 0으로 리셋**한다. 만약 영속 degraded 상태를 이 동일 in-process 카운터에서 파생하면, 자동전환 임계를 지날 때마다 카운터가 0으로 돌아가 degraded가 healthy↔degraded로 **flap(진동)**한다.
- **결정 [HARD] (REQ-052-A5)**: 영속 degraded(`cli_degraded` latch)는 in-process 자동전환 카운터와 **완전히 디커플링**한다. degraded는 감지 임계(REQ-052-A1/A1b)에서 True로 **latch**하고, **오직** REQ-052-A2 조건(CLI 성공 / 하트비트 신선 복귀)에서만 False로 해제된다. L564의 in-process 리셋은 degraded latch에 **영향을 주지 않는다**. 영속 `cli_consecutive_failures`는 자체 카운터로 두되(ADR-002), latch 해제 트리거는 A2뿐이다.
- **근거**: (1) 자동전환은 in-process·일시적 신호(폴백을 *유발*한 뒤 카운트), degraded는 영속·관측 1급 상태 — 라이프사이클이 다르므로 결합하면 안 된다. (2) flap은 알림 throttle(REQ-052-B)을 무효화하고 대시보드를 신뢰 불가로 만든다. (3) SPEC-031 `halt_notified_at`이 trip 카운터와 독립인 것과 같은 분리 원칙.
- **검증**: flap-방지 인수 테스트(AC-1c) — in-process 카운터가 L564에서 0으로 리셋되어도 영속 degraded가 True를 유지함을 단언.
- **상태**: 확정(OQ-4 RESOLVED, [HARD]).

### ADR-004: REQ-052-D2(일일 집계) 범위 — best-effort 추정

- **결정**: 폴백 발동 **횟수** 집계는 구현하되(REQ-052-D1 구조화 로그 기반), **정확한 유료 청구액 산정은 best-effort 추정**으로 한정한다(REQ-052-D2 Optional).
- **근거**: 정확한 청구액은 Anthropic 측 토큰·캐시·티어 정보가 필요해 본 SPEC 범위(비용 누수 *감지·차단*)를 흐린다. 운영자에겐 "오늘 N건 유료 폴백 발동, 추정 ~X건 호출"이면 사후 파악에 충분.
- **상태**: 확정(Optional 한정).

---

## Open Questions (운영자 확인 필요)

- **OQ-1 (ADR-001 정책 선택 — 핵심, OPEN·non-blocking)**: "비용0 절대 보장(strict_cost_zero_mode ON: 호스트 CLI 죽으면 페르소나/뉴스 사이클을 *건너뛰고* 크레딧 0 보장)" vs "폴백 가용성 유지(strict OFF, 기본: 호스트 CLI 죽어도 유료 Haiku로 사이클 유지하되 비용 발생 + 즉시 경고)" — 운영자가 어느 쪽을 **기본 운영 정책**으로 둘 것인가? (구현은 [HARD] 플래그 OFF 기본으로 진행하므로 **non-blocking**, 운영자가 ON 전환 여부를 배포 후 결정)
- **OQ-2 (ADR-002 영속 스키마) — RESOLVED**: ADR-002에서 in-SPEC 확정. degraded-latch(`cli_degraded`/`cli_degraded_since`/`cli_consecutive_failures`)와 throttle 클럭(`cli_degraded_notified_at`)을 **분리** 컬럼으로, mig 034에 additive. (D6)
- **OQ-3 (ADR-003 쿨다운 값) — RESOLVED**: ADR-003에서 in-SPEC 확정. 기본 쿨다운 = **1시간**(`CLI_DEGRADED_ALERT_COOLDOWN_SECONDS=3600`, named tunable, 운영자 튜닝 가능). SPEC-031 6h보다 짧게 — 무음 크레딧 소진의 시간민감성.
- **OQ-4 (감지 임계 N / 카운터 독립) — RESOLVED**: ADR-005에서 in-SPEC 정규 확정([HARD] REQ-052-A5). 영속 degraded는 in-process 자동전환 카운터와 **독립**(latch, A2에서만 해제, L564 리셋과 디커플링) — flap 방지. 감지 임계 N은 기본 `_CLI_AUTO_DISABLE_THRESHOLD=3` 재사용(튜닝 가능 상수).
- **OQ-5 (전용 브랜치) — OPEN·non-blocking**: 메모리상 "main 단일 트렁크" 기록. 현 브랜치가 stale일 수 있으니 구현 전 브랜치 상태 확인 후 전용 브랜치 필요 여부 판단(manager-git 위임).
