---
id: SPEC-TRADING-031
version: 0.1.0
status: draft
created: 2026-05-28
updated: 2026-05-28
author: onigunsow
priority: medium
issue_number: 0
domain: TRADING
title: "매매 정지(halt) 브리핑 쿨다운 — 사이클별 중복 텔레그램 알림 억제"
related_specs:
  - SPEC-TRADING-015   # cli_only_mode / 알림 브리지 (system_briefing 경로)
  - SPEC-TRADING-016   # halt_state / circuit breaker 기원, 자본 보전 규칙
  - SPEC-TRADING-024   # adaptive */15 intraday cron — halt 중 사이클 폭증의 원인
  - SPEC-TRADING-027   # consolidated 브리핑 시스템
---

# SPEC-TRADING-031 — 매매 정지(halt) 브리핑 쿨다운

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-28 | 0.1.0 | Initial draft. halt_state=true 동안 매 사이클이 동일한 "매매 정지" 텔레그램 브리핑을 발송하는 중복 스팸(일 ~28회)을 쿨다운(기본 6h)으로 억제. 첫 사이클은 즉시 알림, resume 시 throttle 상태 초기화. halt 게이트 자체(거래 스킵)와 trip()/`/halt`/`/resume` 초기 알림은 무회귀. 사용자 의사결정 반영 — 2026-05-28 | onigunsow |
| 2026-05-28 | 0.1.1 | 승인(approved) — run 진입. Q-1 해소: 쿨다운은 `.moai/config/sections/scheduler.yaml` 의 `halt_notify_cooldown_seconds` 키로 둠(기존 `trigger_cooldown_seconds` 선례, 재배포 없이 조정). 사용자 결정 2026-05-28 | onigunsow |

---

## Scope Summary

본 SPEC 은 `halt_state=true` 동안 발생하는 **사이클별 중복 텔레그램 "매매 정지" 알림 스팸**을
제거하는 작고 잘 정의된 동작 변경이다.

### 문제 (검증된 근본 원인)

`src/trading/personas/orchestrator.py` 에는 동일한 halt 게이트가 **3곳**에 존재한다:

- `891` (pre_market 사이클)
- `1180` (intraday 사이클)
- `1329` (event-trigger 사이클)

세 게이트 모두 다음과 같이 동일하다:

```python
if state["halt_state"]:
    tg.system_briefing("매매 정지", "halt_state=true 이므로 매매 차단됨")
    return res
```

SPEC-024 v0.3.0 의 adaptive `*/15` intraday cron (09:00~15:30 KST 사이 약 26 사이클) 에
pre_market 및 event trigger 사이클이 더해져, **halt 동안 하루 약 28회의 동일 메시지**가 발송된다.

halt **episode 의 최초 알림**은 이미 별도 경로로 발송된다:
- 자동 회로차단 시: `src/trading/risk/circuit_breaker.py` `trip()` 이 "회로차단" 브리핑 발송.
- 수동 정지 시: `/halt` 명령이 `trip()` 을 호출(`src/trading/risk/emergency.py`).

따라서 사이클별 "매매 정지" 메시지는 **순수 중복 스팸**이며, 운영자에게 정보 가치가 없다.

### In scope
- 사이클별 "매매 정지" 텔레그램 브리핑을 **쿨다운 윈도(기본 6h)당 최대 1회**로 throttle.
- 새 halt episode 의 **첫 사이클은 즉시 알림**(쿨다운이 episode 첫 메시지를 억제하지 않음).
- `circuit_breaker.reset()`(=`/resume`) 시 throttle 상태 초기화 → 다음 episode 첫 사이클 즉시 알림.
- halt **게이트(거래 스킵)** 동작 보존 — 알림 빈도만 변경, 스킵 자체는 매 사이클 그대로 `return`.
- halt 스킵의 **사이클별 로깅**(LOG.info) 유지/추가 — 텔레그램이 throttle 되어도 로그는 매 스킵 기록.

### Non-goals (명시적 비목표)
- **halt 게이트 동작 변경 금지.** halt 사이클은 종전처럼 거래를 건너뛰고 즉시 `return` 한다.
- **초기 trip()/`/halt`/`/resume` 알림 변경 금지.** 이들은 1:1 로 유지된다.
- **회로차단 트립 조건/리스크 한도 로직 변경 없음.** 본 SPEC 은 알림 빈도에만 관여한다.
- 무관한 다른 브리핑(페르소나/사이클체인/일일 리포트)의 빈도/포맷 변경 없음.
- 새 cron/스케줄 추가 없음.

---

## Environment

- 기존 SPEC-001 ~ SPEC-030 인프라 (Docker compose, Postgres 16-alpine, Telegram trading bot).
- `src/trading/personas/orchestrator.py`: 3개 halt 게이트(라인 891/1180/1329, 본 SPEC 검증 기준).
  - 이미 `from trading.risk import circuit_breaker`, `get_system_state`, `update_system_state`,
    `import logging`, `LOG = logging.getLogger(__name__)`, `from trading.alerts import telegram as tg`
    를 import 한다 — 신규 import 최소.
- `src/trading/risk/circuit_breaker.py`: `trip()`(halt 진입 + "회로차단" 알림), `reset()`(halt 해제 +
  "회로차단 해제" 알림). `/halt`·`/resume` 이 각각 이를 호출.
- `src/trading/db/session.py`: `get_system_state()`(SELECT *, 컬럼 추가에 탄력적),
  `update_system_state(**fields)`(임의 컬럼 지원, `None` 전달 시 SQL NULL). **accessor 변경 불필요.**
- `system_state` 싱글톤 테이블(row id=1). 컬럼 추가는 idempotent ALTER 마이그레이션으로.
- 마이그레이션 디렉터리 `src/trading/db/migrations/` 현재 최고 번호 `022_add_filled_at.sql`.
  → 본 SPEC 신규 마이그레이션 번호 = **023**.
- `.moai/config/sections/scheduler.yaml` 존재(SPEC-024). `trigger_cooldown_seconds: 300` 등 쿨다운
  키 선례 보유 — 쿨다운 설정 home 후보(구현자 선택).
- 컨테이너 재시작 시에도 throttle 상태가 살아남아야 하므로 상태는 **DB(system_state)** 에 둔다.

---

## Assumptions

- A-1: 3개 halt 게이트는 본 SPEC 작성 시점(2026-05-28)에 라인 891/1180/1329 에 있으며, 셋 다
  바이트 단위로 동일하다(grep 검증 완료). 구현 시점에 라인이 이동했을 수 있으므로 구현자는
  패턴(`if state["halt_state"]:` → `tg.system_briefing("매매 정지", ...)` → `return res`)으로 재확인한다.
- A-2: `update_system_state(halt_notified_at=None)` 은 `halt_notified_at = NULL` 로 매핑된다
  (session.py:74~89 의 `%s` 파라미터 바인딩으로 psycopg adapter 가 None→NULL 처리).
- A-3: `get_system_state()` 는 SELECT * 이므로 신규 `halt_notified_at` 컬럼이 반환 dict 에 자동 포함된다.
- A-4: halt episode 의 최초 알림은 항상 `trip()` 경로(자동 회로차단 또는 `/halt`)로 발송된다. 즉
  "사이클 게이트의 첫 알림"은 episode 최초 알림과 별개의 운영 신호(= halt 가 지속 중임을 주기적
  재확인)이며, 본 SPEC 은 이 사이클 게이트 알림에만 쿨다운을 적용한다. trip() 알림은 무관.
- A-5: 쿨다운은 **순수 경과시간(elapsed-time)** 기반이며 calendar-day(자정 리셋) 가 아니다.
  상태가 DB 에 있으므로 컨테이너 재시작 후에도 쿨다운이 이어진다(day-boundary note).
- A-6: 쿨다운 기본값 21600초(6시간)는 사용자 승인값이며 설정 가능해야 한다.

---

## Requirements (EARS)

### REQ-031-1 (Event-driven) — halt 사이클 브리핑 쿨다운 throttle

**WHEN** 한 트레이딩 사이클(pre_market / intraday / event-trigger)이 `halt_state=true` 게이트에
진입하면, **THEN** 시스템은 "매매 정지" 텔레그램 브리핑을 **쿨다운 윈도(기본 21600초=6시간)당 최대
1회**만 발송해야 한다.
- (a) 마지막 발송 이후 경과시간이 쿨다운 미만이면 텔레그램 브리핑을 **발송하지 않아야** 한다.
- (b) 발송 결정 시 `system_state.halt_notified_at` 을 현재 시각으로 갱신한다.
- (c) 쿨다운 판정/타임스탬프 상태는 DB(`system_state`)에 영속화되어 컨테이너 재시작에도 유지된다.

### REQ-031-2 (State-driven) — halt episode 첫 사이클 즉시 알림

**IF** 새로 진입한(또는 throttle 상태가 초기화된) halt episode 의 **첫 사이클**이 게이트에 도달하면
(즉 `halt_notified_at` 이 NULL 이면), **THEN** 시스템은 쿨다운과 무관하게 "매매 정지" 브리핑을
**즉시 1회 발송**하고 `halt_notified_at` 을 현재 시각으로 설정해야 한다.
- (a) 쿨다운은 episode 의 두 번째 사이클부터 적용된다(첫 메시지는 절대 억제하지 않음).

### REQ-031-3 (Event-driven) — resume 시 throttle 초기화

**WHEN** halt 가 `circuit_breaker.reset()`(=`/resume`)으로 해제되면, **THEN** 시스템은
`system_state.halt_notified_at` 을 **NULL 로 초기화**하여, 다음 halt episode 의 첫 사이클이
REQ-031-2 에 따라 즉시 알림되도록 보장해야 한다.
- (a) 초기화는 `reset()` 의 halt 해제 동작과 함께 원자적으로 수행한다(별도 사용자 액션 불필요).

### REQ-031-4 (Ubiquitous) — 게이트/스킵 동작 보존 + 사이클별 로깅

시스템은 본 기능을 위해 halt 게이트의 거래 차단 동작을 변경하지 **않아야** 한다.
- (a) `halt_state=true` 인 모든 사이클은 종전과 동일하게 거래를 건너뛰고 즉시 `return res` 해야 한다.
- (b) 텔레그램 알림이 throttle 되어 발송되지 않은 사이클에서도, 시스템은 **매 스킵마다 로그**
  (예: `LOG.info`)를 남겨 운영 로그에서 모든 halt 스킵을 추적할 수 있어야 한다.

### REQ-031-5 (Unwanted) — 초기 trip()/halt/resume 알림 무회귀

시스템은 본 기능 도입으로 인해 회로차단 트립 시 `trip()` 의 "회로차단" 알림, 수동 `/halt` 알림,
`/resume`(`reset()`)의 "회로차단 해제" 알림의 **횟수·내용을 변경하거나 억제하지 않아야** 한다.
이들 episode-level 알림은 1:1 로 유지된다. 본 SPEC 의 쿨다운은 오직 **사이클 게이트의 "매매 정지"
브리핑**에만 적용된다.

### REQ-031-6 (Optional) — 쿨다운 설정 가능성

가능하면 시스템은 쿨다운 길이를 운영자가 재배포 없이 조정할 수 있도록 설정 가능하게 제공해야 한다
(기본 21600초). 모듈 상수 또는 설정 키(`.moai/config/sections/scheduler.yaml`) 중 구현자 선택.
설정 미제공 시 기본 21600초 상수를 사용한다.

---

## Specifications

### 권장 메커니즘 (구현 가이드 — 구현자 재량 여지 있음)

대상 REQ: REQ-031-1 ~ REQ-031-6

- **상태 영속화:** `system_state` 싱글톤에 `halt_notified_at TIMESTAMPTZ NULL` 컬럼을 추가한다.
  마이그레이션 `src/trading/db/migrations/023_halt_notify_cooldown.sql` 를 13/10번 마이그레이션의
  idempotent ALTER 패턴(`ADD COLUMN IF NOT EXISTS` 또는 `information_schema.columns` 가드 +
  `schema_migrations` 등록)으로 작성한다. `update_system_state`/`get_system_state` 는 임의 컬럼을
  지원하므로 **accessor 코드 변경은 불필요**하다.

- **결정 헬퍼:** `circuit_breaker.py`(또는 신규 함수)에 작은 헬퍼를 둔다. 의사결정 규칙:
  - `halt_notified_at` 이 NULL **이거나** `now - halt_notified_at >= cooldown` 이면 → 발송 +
    `halt_notified_at = now` 기록.
  - 그렇지 않으면 → 발송 스킵.
  - 헬퍼는 발송 여부(bool)를 반환하거나 내부에서 `tg.system_briefing` 을 직접 호출하는 형태 중 선택.
    (호출부 단순화를 위해 헬퍼가 발송까지 수행하고 "발송됨/스킵됨"을 반환하는 형태 권장.)

- **게이트 치환:** orchestrator.py 의 3개 게이트(라인 891/1180/1329)에서
  `tg.system_briefing("매매 정지", ...)` 직접 호출을 헬퍼 호출로 교체한다. 각 게이트는 헬퍼 결과와
  무관하게 **반드시 매 사이클 `LOG.info`(스킵 로깅)를 남기고** 종전처럼 `return res` 한다.

- **resume 초기화:** `circuit_breaker.reset()` 에서 halt 해제와 함께
  `update_system_state(halt_state=False, halt_notified_at=None, ...)` 로 throttle 을 초기화한다.

- **쿨다운 설정:** 기본 21600초. 모듈 상수(`HALT_NOTIFY_COOLDOWN_SECONDS = 21600`) 가 가장 단순한
  허용 옵션이며, 설정 home 이 필요하면 `scheduler.yaml` 의 기존 cooldown 키 선례를 따라
  `halt_notify_cooldown_seconds` 키를 추가한다. (Open Question Q-1 참조.)

- **day-boundary note:** 쿨다운은 순수 경과시간 기반(calendar-day 아님). 상태가 DB 에 있으므로
  컨테이너 재시작에도 쿨다운이 유지된다.

### 영향 파일 (예정)

- `src/trading/db/migrations/023_halt_notify_cooldown.sql` (신규, idempotent)
- `src/trading/risk/circuit_breaker.py` (헬퍼 추가 + `reset()` 에 throttle 초기화)
- `src/trading/personas/orchestrator.py` (3개 게이트 치환 + 스킵 로깅)
- `src/trading/risk/emergency.py` (변경 불필요 — `/resume` 는 `reset()` 경유로 자동 반영)
- 테스트: `tests/risk/test_halt_notify_throttle.py`(권장) 등

---

## Open Questions (Flag Only)

- **Q-1 (쿨다운 설정 home)**: **[해소 2026-05-28]** `.moai/config/sections/scheduler.yaml` 에
  `halt_notify_cooldown_seconds: 21600` 키를 추가한다(기존 `trigger_cooldown_seconds: 300` 선례).
  ops 가 재배포 없이 조정 가능. 코드는 미설정 시 21600초 기본값으로 fallback.

---

## Acceptance & Traceability

자세한 acceptance criteria 는 `acceptance.md` 참조.
구현 계획 및 milestone 은 `plan.md` 참조.

| REQ | 구현 대상(예정) | 검증(acceptance.md) |
| --- | --- | --- |
| REQ-031-1 | 쿨다운 헬퍼 + `halt_notified_at` | AC-1, AC-2 |
| REQ-031-2 | 헬퍼 NULL 분기(첫 사이클) | AC-1, AC-3 |
| REQ-031-3 | `circuit_breaker.reset()` 초기화 | AC-3 |
| REQ-031-4 | 3개 게이트 보존 + 스킵 로깅 | AC-4 |
| REQ-031-5 | trip()/halt/resume 무회귀(정적+동적) | AC-5 |
| REQ-031-6 | 쿨다운 상수/설정 키 | AC-6 |
