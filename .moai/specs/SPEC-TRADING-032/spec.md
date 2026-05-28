---
id: SPEC-TRADING-032
version: 0.1.0
status: draft
created: 2026-05-28
updated: 2026-05-28
author: onigunsow
priority: medium
issue_number: 0
domain: TRADING
title: "장 시작 전 자동 매매 재개 — 양성(benign) 자동 한도 정지에 한해 07:25 KST 재개"
related_specs:
  - SPEC-TRADING-015   # 알림 브리지 (system_briefing 경로)
  - SPEC-TRADING-016   # halt_state / circuit breaker 기원 + 리스크 한도(limits.py) 기원, 자본 보전 규칙
  - SPEC-TRADING-024   # scheduler/runner cron home, */15 intraday — daily_count 폭증의 원인
  - SPEC-TRADING-031   # reset() 이 halt_notified_at 초기화 (본 SPEC 의 reset 호출과 상호작용)
---

# SPEC-TRADING-032 — 장 시작 전 자동 매매 재개

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-28 | 0.1.0 | Initial draft. 매 평일 07:25 KST(기존 pre_market 07:30 직전) 스케줄러 잡이 halt 원인을 audit_log 로 분류하여, **양성 자동 한도 위반(daily_loss 제외)** 인 경우에만 `circuit_breaker.reset(actor="auto_resume_premarket")` 으로 자동 재개. 수동 `/halt`·daily_loss·원인 불명 시에는 재개하지 않고 "수동 검토 필요" 텔레그램 알림. 근본 원인(2026-05-28 11:48 daily_count 트립이 day-reset 없이 잔존) 검증 후 사용자 의사결정 반영 — 2026-05-28 | onigunsow |
| 2026-05-28 | 0.1.1 | 승인(approved) — run 진입. 열린질문 해소: Q-1 daily_loss 설정화 보류(기본 OFF 유지), Q-2 per-day guard 불필요(07:25 단일 cron=자연 1회/일), Q-3 `_wrap` 사용(KRX 휴장일 자동 스킵), Q-4 방어적 active-trip 식별(TRIP.ts > RESET.ts). 사용자 결정 2026-05-28 | onigunsow |

---

## Scope Summary

본 SPEC 은 **새 트레이딩 데이가 매매 정지(halt) 상태로 막혀 거래를 못 하는 운영 부담**을
자동화한다. 매 평일 07:25 KST 에, 직전 halt 가 **양성(benign)·자동(automatic) 한도 위반**으로
발생한 것이라면 자동으로 재개하고, 그 외(수동 정지·실손실·원인 불명)에는 정지를 그대로 두고
운영자에게 수동 검토를 알린다.

### 문제 (검증된 근본 원인)

`halt_state`(싱글톤 `system_state`, id=1)는 `src/trading/risk/circuit_breaker.py` 의
`trip(reason, details)` 가 true 로 설정하고 `reset(actor)` 가 해제한다. 자동 회로차단은
`src/trading/personas/orchestrator.py` 의 pre-order 한도 체크에서 발동한다(검증된 트립 사이트:
**라인 1051, 1484** — `trip(reason="pre-order limit breach", details={"breaches": chk.breaches})`).

`breaches` 리스트(`src/trading/risk/limits.py`)의 각 항목은 한도명으로 **prefix** 된 사람이 읽는
문자열이며, 정확히 다음 중 하나로 시작한다(limits.py 검증):

- `"daily_count: 오늘 주문 N → 한도 10"` — 일일 주문건수 cap. **양성**: 매 calendar-day 카운트가
  0 으로 리셋되므로 다음 날에는 재현되지 않는다. 단, **halt_state 자체는 day-reset 되지 않아**
  수동 `resume` 전까지 정지가 잔존한다(= 본 SPEC 이 자동화하는 통증).
- `"daily_loss: 오늘 손익 X% ≤ 한도 Y%"` — 일일 손실 한도. **실손실 이벤트** → 자동 재개 금지.
- `"single_order: ..."`, `"per_ticker: ..."`, total-invested 비율 위반 — 주문 사이징 위반.
  **양성(주문 거부형)** → daily_loss 가 없으면 자동 재개 허용.

수동 정지는 `src/trading/risk/emergency.py`(`/halt`, 라인 31, `reason="manual /halt"`) 와
`src/trading/cli.py`(라인 157, `reason="manual cli /halt"`) 가 `trip()` 을 호출한다.

**실측 사례(2026-05-28 11:48):** 회로차단이 `reason="pre-order limit breach"`,
`breaches=["daily_count: 오늘 주문 10 → 한도 10"]` 로 트립. daily_count 트립은 halt 의 자동
day-reset 이 없어 수동 `trading resume` 까지 정지 지속 → 본 SPEC 이 07:25 자동 재개로 해소.

### In scope

- 매 평일 **07:25 KST** 스케줄러 잡(`premarket_auto_resume`)이 halt 자동 재개 여부를 판정/수행.
- halt 원인 분류는 `audit_log` 의 **활성 트립(active TRIP)** 행(`CIRCUIT_BREAKER_TRIP`)에서 도출.
- **자동 재개 조건(AND):** `halt_state=true` **그리고** 활성 트립 `reason == "pre-order limit breach"`
  **그리고** `details.breaches` 에 `"daily_loss"` 로 시작하는 항목이 **하나도 없음**.
  → `circuit_breaker.reset(actor="auto_resume_premarket")` + "자동 재개" `system_briefing`.
- **자동 재개 금지(notify-only "수동 검토 필요"):** 수동 halt(`reason` 이 "manual" 로 시작) /
  breaches 에 daily_loss 포함 / 원인 불명(활성 트립 미확정) — 어느 하나라도면 재개 안 함 + 알림.
- `halt_state=false` 이미 해제 상태면 **no-op**(로그만, 텔레그램 없음).
- 모든 결정(재개/보류)에 대해 `audit_log` 항목(예: `AUTO_RESUME_PREMARKET`) 기록.

### Non-goals (명시적 비목표)

- **수동 `/halt` 자동 재개 절대 금지.** 비상 정지는 자본 보전을 위한 hard safety rule — 절대
  무음(silent) 해제하지 않는다.
- **daily_loss 자동 재개 금지.** 실손실 정지는 운영자 판단 영역. (향후 설정화는 Open Question Q-1.)
- **회로차단 트립 조건/리스크 한도(limits.py) 로직 변경 없음.** 본 SPEC 은 *재개 판정*에만 관여.
- **`trip()`/`reset()`/`/halt`/`/resume` 기존 동작 변경 없음.** `reset()` 은 호출만 하며 그 내부
  동작(halt 해제 + "회로차단 해제" 알림 + SPEC-031 `halt_notified_at=NULL`)은 그대로 재사용.
- pre_market(07:30)·intraday·daily report 등 다른 cron/브리핑의 빈도/포맷 변경 없음.
- 새 DB 레이어 도입 없음 — 기존 `session.py` 패턴 재사용.

---

## Environment

- 기존 SPEC-001 ~ SPEC-031 인프라 (Docker compose, Postgres 16-alpine, Telegram trading bot).
- `src/trading/scheduler/runner.py`:
  - `KST = pytz.timezone("Asia/Seoul")`(라인 33), `CronTrigger`(라인 13) import 보유.
  - 잡 등록 패턴: `sched.add_job(lambda: _wrap("name", fn), CronTrigger(day_of_week="mon-fri",
    hour=H, minute=M, timezone=KST), id="...", name="...")`.
  - **pre_market** 등록(라인 252~256): `id="pre_market"`, `hour=7, minute=30`, `name="pre_market 07:30"`.
    본 SPEC 신규 잡은 이 직전(07:25)에 등록.
  - `_wrap(name, fn)`(라인 90~101): 호출 전 `is_trading_day()`(Mon-Fri ∩ KRX 휴장일 제외) 가드 후
    실행 + start/ok/failed 로깅. (Q-3: _wrap 사용 시 KRX 휴장일에도 자동 스킵.)
- `src/trading/risk/circuit_breaker.py`:
  - `reset(actor="operator")`(라인 87~95): `update_system_state(halt_state=False,
    halt_notified_at=None, ...)` + `audit("CIRCUIT_BREAKER_RESET", ...)` + "회로차단 해제" 알림.
    본 SPEC 은 `reset(actor="auto_resume_premarket")` 로 호출만 한다 — 무변경 재사용.
  - `is_halted()`(라인 26), `trip()`(라인 73). `from trading.db.session import audit,
    get_system_state, update_system_state` 보유, `from trading.alerts.telegram import system_briefing`.
- `src/trading/db/session.py`:
  - `connection(autocommit=False)` 컨텍스트 매니저(`dict_row`), `audit(event_type, actor, details)`,
    `get_system_state()`(SELECT *), `update_system_state(**fields)`. **신규 DB 레이어 불필요.**
  - audit_log SELECT 선례: `src/trading/news/intelligence/scheduler.py:280~287`
    (`with connection() as conn, conn.cursor() as cur: cur.execute("SELECT ... FROM audit_log ...")`).
- `audit_log` 스키마(마이그레이션 001): `id BIGSERIAL, ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  event_type TEXT, actor TEXT, details JSONB`. **정렬 기준 컬럼 = `ts`** (인덱스 `audit_log_ts_idx
  ON audit_log (ts DESC)` 존재). (주의: 일부 모듈의 `created_at` 정렬은 audit_log 가 아닌 다른
  테이블 대상 — audit_log 는 `ts` 가 정답.)
- `src/trading/alerts/telegram.py`: `system_briefing(category, message)`(라인 70, 위치 인자 2개).
  테스트에서는 네트워크 호출 없이 mock 으로 send 횟수 검증.
- 마이그레이션 디렉터리 현재 최고 번호 = `023_halt_notify_cooldown.sql`(SPEC-031). → 본 SPEC 이
  스키마 변경을 필요로 한다면 신규 번호는 **024**. (단, 본 SPEC 은 신규 컬럼이 필요 없을 수 있음
  — Specifications 참조.)
- `.moai/config/sections/scheduler.yaml` 존재. **런타임에 로드되지 않음**(SPEC-031 검증: `src/trading/`
  에 YAML loader 없음). 모든 schedule/cooldown 값은 **코드 상수**가 source of truth. 07:25 시각도
  runner.py 의 `CronTrigger` 코드 상수로 하드코딩하며, scheduler.yaml 문서화는 선택(미러).

---

## Assumptions

- A-1: 검증 시점(2026-05-28)에 자동 트립 사이트는 orchestrator.py 라인 ~1051, ~1484 이며 둘 다
  `trip(reason="pre-order limit breach", details={"breaches": chk.breaches})` 이다(grep 검증).
  구현 시점에 라인이 이동했을 수 있으므로 구현자는 패턴(`circuit_breaker.trip(reason="pre-order
  limit breach", details={"breaches": ...})`)으로 재확인한다. 본 SPEC 은 트립 사이트를 변경하지 않는다.
- A-2: `halt_state=true` 인 동안에는 RESET 으로 이어지지 않은 TRIP 이 정확히 1개(가장 최근의 것)
  존재한다 — RESET 은 `halt_state=false` 를 만들기 때문이다. 따라서 "가장 최근 TRIP 행"이 활성
  트립이다. 단, 운영자가 SQL 로 `halt_state` 를 직접 true 로 만든 경우 등 **TRIP 행이 없을 수
  있으므로**(원인 불명) 방어적으로 처리한다(재개 안 함).
- A-3: 활성 트립의 `details` 는 `{"reason": <str>, "breaches": [<str>, ...]}`(자동) 또는
  `{"reason": "manual /halt"|"manual cli /halt", "actor": <str>}`(수동) 형태이다(`trip()` 이
  `{"reason": reason, **(details or {})}` 로 기록 — circuit_breaker.py:76~77 검증).
- A-4: limits.py 의 breach 문자열은 한도명 prefix 로 시작한다: `daily_loss:`, `daily_count:`,
  `single_order:`, `per_ticker:`, 그리고 total-invested 비율 위반(limits.py:139~ 의 문자열).
  daily_loss 판정은 `entry.startswith("daily_loss")` prefix 검사로 정확히 가능하다(limits.py:121 검증).
- A-5: 수동 halt 의 reason 은 항상 "manual" 로 시작한다(`"manual /halt"`, `"manual cli /halt"`).
  따라서 `reason.startswith("manual")` 로 수동 정지를 식별할 수 있다(emergency.py:31, cli.py:157 검증).
- A-6: `circuit_breaker.reset()` 호출은 멱등에 가깝다 — 이미 `halt_state=false` 면 본 잡은 reset 을
  호출하지 않는다(no-op 분기). reset 의 부수효과("회로차단 해제" 알림 등)는 실제 재개 시에만 발생.
- A-7: 07:25 잡과 pre_market 07:30 잡은 별개 cron 으로 5분 간격이며, 본 잡의 작업량(DB 1~2 회 조회
  + 선택적 reset)은 수 초 이내라 07:30 과 겹치지 않는다.

---

## Requirements (EARS)

### REQ-032-1 (Event-driven) — 07:25 KST 평일 자동 재개 점검 실행

**WHEN** 매 평일(mon-fri) **07:25 KST** 가 도래하면, **THEN** 시스템은 장 시작 전 자동 재개
점검(`run_premarket_auto_resume()`)을 1회 실행해야 한다.
- (a) 점검 시각은 runner.py 의 `CronTrigger(day_of_week="mon-fri", hour=7, minute=25,
  timezone=KST)`, 잡 id `premarket_auto_resume` 로 하드코딩하며 기존 pre_market/intraday 잡과
  동일한 `_wrap(...)` 래퍼로 등록한다(런타임 source = 코드; scheduler.yaml 미러는 선택).
- (b) 점검은 pre_market(07:30) 사이클보다 **먼저** 실행되어, 재개 시 당일 첫 거래가 가능하게 한다.

### REQ-032-2 (State-driven) — 양성 자동 한도 정지에 한해서만 자동 재개

**IF** 점검 시점에 `halt_state=true` **이고** 활성 트립의 `reason == "pre-order limit breach"`
**이고** `details.breaches` 가 비어있지 않은 리스트로서 `"daily_loss"` 로 시작하는 항목을 **하나도
포함하지 않으면**, **THEN** 시스템은 `circuit_breaker.reset(actor="auto_resume_premarket")` 를
호출하여 매매를 재개하고, "자동 재개"를 알리는 `system_briefing` 을 발송해야 한다.
- (a) 자동 재개는 daily_count 단독, single_order/per_ticker 단독, 또는 이들의 (daily_loss 없는)
  조합 모두에 대해 동일하게 적용된다.
- (b) `reset()` 의 기존 동작(halt 해제 + "회로차단 해제" 알림 + SPEC-031 `halt_notified_at=NULL`)은
  그대로 재사용되며 본 SPEC 이 변경하지 않는다.

### REQ-032-3 (Unwanted) — 수동 정지·실손실·원인 불명은 절대 자동 재개 금지

시스템은 다음 중 **어느 하나라도** 해당하면 매매를 자동 재개하지 **않아야** 하며, 대신 정지를
유지한 채 "수동 검토 필요"를 알리는 `system_briefing` 을 발송해야 한다.
- (a) 활성 트립이 **수동 halt**(`reason` 이 "manual" 로 시작)인 경우. — 자본 보전 hard rule.
- (b) `details.breaches` 에 `"daily_loss"` 로 시작하는 항목이 **하나라도** 있는 경우(daily_count 등
  양성 항목과 혼재되어 있어도 daily_loss 가 있으면 보류).
- (c) **원인을 확정할 수 없는** 경우(활성 트립 행 없음, reason 이 "pre-order limit breach"·"manual"
  중 어느 것도 아님, breaches 가 누락/비어있음/형식 불량 등) — defensive default = 재개 안 함.

### REQ-032-4 (Ubiquitous) — 미정지 상태에서는 no-op

`halt_state=false` 인 경우, 시스템은 어떤 재개 동작도 하지 않고 점검 결과를 **로그로만** 남겨야 한다.
- (a) 미정지 no-op 에서는 텔레그램 브리핑을 발송하지 **않는다**(불필요 알림 방지).

### REQ-032-5 (Ubiquitous) — 모든 결정의 알림 + 감사 기록

시스템은 모든 자동 재개 점검 결정(재개됨 / 보류됨)에 대해, REQ-032-4 의 미정지 no-op 를 제외하고
**텔레그램 브리핑 1회**와 **`audit_log` 항목 1건**을 남겨야 한다.
- (a) 감사 항목은 결정 내용을 담는다(예: event_type `AUTO_RESUME_PREMARKET`,
  `details={"decision": "resumed"|"held", "cause": <str>, "detail": <str>}`).
- (b) 미정지 no-op 도 **감사 항목은 남길 수 있으나**(선택), 텔레그램은 발송하지 않는다.

### REQ-032-6 (Ubiquitous) — 기존 회로차단/리스크 경로 무회귀

시스템은 본 기능 도입으로 인해 `trip()`, `reset()`, `/halt`, `/resume`, limits.py 한도 판정의
**동작·횟수·내용을 변경하지 않아야** 한다. 본 SPEC 은 이들을 *호출/조회*만 하며 그 내부 로직을
수정하지 않는다.

---

## Specifications

### 권장 메커니즘 (구현 가이드 — 구현자 재량 여지 있음)

대상 REQ: REQ-032-1 ~ REQ-032-6

- **신규 모듈:** `src/trading/risk/auto_resume.py` 를 둔다.
  - 순수에 가까운 분류기 `classify_halt(state, active_trip) -> (should_resume: bool, cause: str,
    detail: str)`:
    - `halt_state` 가 false → (False, "not_halted", "")  *(엔트리 함수에서 사전 분기해도 무방)*
    - 활성 트립 미확정 → (False, "undeterminable", "<사유>")  *(REQ-032-3c)*
    - `reason.startswith("manual")` → (False, "manual", reason)  *(REQ-032-3a)*
    - `reason != "pre-order limit breach"` → (False, "unknown_reason", reason)  *(REQ-032-3c)*
    - breaches 가 리스트가 아니거나 비어있음 → (False, "undeterminable", "<breaches 형식 불량>")
    - breaches 에 `startswith("daily_loss")` 항목 존재 → (False, "daily_loss", <breaches>)  *(REQ-032-3b)*
    - 그 외(daily_loss 없는 자동 한도 위반) → (True, "<breach prefixes 요약>", <breaches>)  *(REQ-032-2)*
  - 엔트리 함수 `run_premarket_auto_resume()`:
    1. `state = get_system_state()`; `if not state["halt_state"]:` → `LOG.info(...)` + (선택) audit
       no-op 기록 후 return (REQ-032-4, 텔레그램 없음).
    2. 활성 트립 조회(아래 "active trip 식별" 참조).
    3. `should_resume, cause, detail = classify_halt(...)`.
    4. `should_resume` → `circuit_breaker.reset(actor="auto_resume_premarket")` +
       `system_briefing("자동 재개", f"장 시작 전 자동 매매 재개 (사유: {cause})")` +
       `audit("AUTO_RESUME_PREMARKET", actor="auto_resume_premarket",
       details={"decision":"resumed","cause":cause,"detail":detail})`.
    5. else → `system_briefing("수동 검토 필요", f"자동 재개 보류 (사유: {cause}). 수동 확인 필요.")` +
       `audit("AUTO_RESUME_PREMARKET", ..., details={"decision":"held","cause":cause,"detail":detail})`.
  - 텔레그램 실패는 swallow(circuit_breaker 의 try/except 선례)하여 잡 자체는 죽지 않게 한다.

- **active trip(활성 트립) 식별 — audit_log 조회:** `session.py` 의 `connection()` 컨텍스트
  매니저를 재사용(news/intelligence/scheduler.py:280~287 선례)하여 raw SQL 로 조회한다.
  정렬 컬럼은 **`ts`**(audit_log_ts_idx 존재). 권장(가장 방어적) 방식:
  - 최근 `CIRCUIT_BREAKER_TRIP` 1건과 최근 `CIRCUIT_BREAKER_RESET` 1건을 각각 `ORDER BY ts DESC
    LIMIT 1` 로 읽는다.
  - TRIP 이 없거나 `RESET.ts >= TRIP.ts` 면 → 활성 트립 **미확정**(원인 불명, REQ-032-3c) 처리.
  - 그렇지 않으면 그 TRIP 행(`details`, `actor`)을 활성 트립으로 사용.
  - (단순 대안) `halt_state=true` 라는 불변식상 "가장 최근 TRIP" 만으로도 충분하나, RESET 비교를
    더하면 SQL 직접 변조/경합 등 엣지에 견고하다. 구현자 판단으로 단순 버전 선택 가능.
  - 단일 쿼리 최적화(선택): `SELECT event_type, ts, details FROM audit_log WHERE event_type IN
    ('CIRCUIT_BREAKER_TRIP','CIRCUIT_BREAKER_RESET') ORDER BY ts DESC LIMIT 1` 의 결과가 TRIP 이면
    활성, RESET 이면 미확정.

- **스케줄러 등록:** runner.py 의 pre_market 잡(라인 252) **직전**에 추가:
  ```
  sched.add_job(
      lambda: _wrap("premarket_auto_resume", run_premarket_auto_resume),
      CronTrigger(day_of_week="mon-fri", hour=7, minute=25, timezone=KST),
      id="premarket_auto_resume",
      name="premarket_auto_resume 07:25",
  )
  ```
  `_wrap` 사용 시 KRX 휴장일 자동 스킵(Q-3). import 는 `from trading.risk.auto_resume import
  run_premarket_auto_resume`.

- **스키마:** 본 SPEC 은 **신규 컬럼이 필요 없다**(상태를 audit_log 에서 도출하며, 결정 자체는
  audit_log 에 기록). 따라서 신규 마이그레이션은 **불필요**. (만약 per-day guard(Q-2)를 채택한다면
  마이그레이션 번호는 024 가 다음 번호이다.)

- **설정/상수:** 07:25 시각·"pre-order limit breach" 문자열·"manual" prefix·"daily_loss" prefix 는
  코드 상수/리터럴. scheduler.yaml 에 문서용 키 추가는 선택(런타임 미로드).

### 영향 파일 (예정)

- `src/trading/risk/auto_resume.py` (신규 — 분류기 + 엔트리 함수)
- `src/trading/scheduler/runner.py` (신규 잡 등록 + import)
- `src/trading/risk/circuit_breaker.py` (변경 불필요 — `reset()` 호출만)
- `src/trading/risk/emergency.py`, `src/trading/cli.py`, `src/trading/risk/limits.py`,
  `src/trading/personas/orchestrator.py` (변경 불필요 — 조회/참조만)
- 테스트: `tests/risk/test_auto_resume.py`(권장)

---

## Open Questions (Flag Only)

- **Q-1 (daily_loss 향후 설정화)**: 현재 daily_loss 는 hard HOLD(자동 재개 금지). 향후 운영자가
  "daily_loss 도 다음 날 자동 재개" 를 원하면 설정 플래그로 노출할 수 있으나, 자본 보전 관점에서
  기본 OFF 권장. **현 SPEC 범위 외 — flag only.**
- **Q-2 (하루 1회 재개 가드)**: 잡이 07:25 평일 1회만 도므로 자연히 하루 1회이다. 단, 07:25 재개
  직후 장 중 동일 daily_count 가 다시 트립하면 그날은 더 이상 자동 재개되지 않고 익일 07:25 에
  재개된다(의도된 동작). 명시적 per-day guard(예: `system_state.auto_resumed_on` 날짜 컬럼,
  마이그레이션 024)는 **불필요해 보이나** 추가 안전장치로 고려 가능. **flag only.**
- **Q-3 (`_wrap` vs raw mon-fri)**: `_wrap` 사용 시 KRX 휴장일에도 자동 스킵된다(권장 — 휴장일에
  재개할 이유 없음). 단 장기 연휴 동안 daily_count halt 는 연휴 다음 첫 거래일 07:25 까지 잔존
  (허용 가능). raw `day_of_week="mon-fri"`(휴장일 포함) 로 매 평일 재개를 원하면 `_wrap` 대신
  직접 래핑. **구현자 선택 — flag.**
- **Q-4 (active trip 식별 단순 vs 방어)**: "가장 최근 TRIP" 단순 방식 vs "TRIP.ts > RESET.ts"
  방어 방식. 권장은 방어 방식(SQL 변조/경합 견고). **구현자 선택 — Specifications 참조.**

---

## Acceptance & Traceability

자세한 acceptance criteria 는 `acceptance.md` 참조.
구현 계획 및 milestone 은 `plan.md` 참조.

| REQ | 구현 대상(예정) | 검증(acceptance.md) |
| --- | --- | --- |
| REQ-032-1 | runner.py 07:25 잡 등록 | AC-8 |
| REQ-032-2 | classify_halt 양성 분기 + reset 호출 | AC-1, AC-2 |
| REQ-032-3 | classify_halt HOLD 분기(manual/daily_loss/불명) | AC-3, AC-4, AC-5, AC-7 |
| REQ-032-4 | not_halted no-op 분기 | AC-6 |
| REQ-032-5 | system_briefing + audit("AUTO_RESUME_PREMARKET") | AC-1~AC-5, AC-7 |
| REQ-032-6 | trip()/reset()/limits 무회귀(정적+동적) | AC-9 |
