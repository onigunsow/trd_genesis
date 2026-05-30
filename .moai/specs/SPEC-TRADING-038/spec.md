---
id: SPEC-TRADING-038
version: 0.1.0
status: draft
created: 2026-05-30
updated: 2026-05-30
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "실거래 준비 게이트 2건 — 일일손실 회로차단 완화 + 익절 마커 DB 영속화"
related_specs:
  - SPEC-TRADING-037   # 엣지 검증 감사가 본 게이트들을 표면화. 포지션별 스톱 플로어(-10%) 도입 → 일일한도 정합성 근거
  - SPEC-TRADING-033   # position_watchdog — 익절 1일 1회 인메모리 가드(REQ-038-2 의 수정 대상)
  - SPEC-TRADING-032   # auto_resume — daily_loss 비자동재개 불변(REQ-038-1 acceptance 근거)
  - SPEC-TRADING-031   # halt 알림/회로차단 컨텍스트
  - SPEC-TRADING-002   # 실거래 분리 — live 잠금 유지 근거(C-3)
  - SPEC-TRADING-001   # RISK_DAILY_MAX_LOSS 등 5대 하드 리밋 원천
---

# SPEC-TRADING-038 — 실거래 준비 게이트 2건: 일일손실 회로차단 완화 + 익절 마커 DB 영속화

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-30 | 0.1.0 | Initial draft. 엣지 검증 감사(SPEC-037 컨텍스트)가 **실거래(live) 전환 전에 닫아야 할 게이트** 들을 표면화. 본 SPEC 은 그중 **코드로 고칠 수 있는 2건**만 다룬다: (1) 일일 손실 회로차단 임계 −1.0% 가 과도하게 빡빡(정상 장중 변동을 실손실로 오인 → halt thrash; SPEC-037 이 포지션별 스톱 플로어 −10% 를 도입했으므로 포트폴리오 일일한도가 다종목 정상 스윙을 수용해야 함) → 약 **−2.5%(리스크 오너 확정)** 로 완화·env 구성 가능화. (2) `position_watchdog` 의 익절 1일 1회 가드가 **인메모리 전용** → 컨테이너 재시작 시 리셋 → **이중 익절(반매도 2회)** 위험 → **DB 영속화**. **중요 nuance**: 최근 정지는 daily-order-COUNT(10건) 트립이지 daily-LOSS 트립이 아니다 → REQ-038-1 은 거래 0건의 원인 수정이 **아니라** 실 P&L 스윙 대비 하드닝(긴급성 과장 금지). 세 번째 게이트(자격증명 회전)는 **운영자 수동**이라 코드 범위 밖(Non-Goals). **paper only, live 잠금 유지, money/risk 로직이므로 reproduction-first 필수**. — 2026-05-30 | onigunsow |

---

## Scope Summary

본 SPEC 은 **실거래(live) 전환 전 반드시 통과해야 하는 "live-readiness gate"** 중 **코드로 고칠 수 있는
2건**을 닫는다. paper 모드는 계속 활성, live 는 계속 잠금(C-3).

- **REQ-038-1 — 일일 손실 회로차단 완화**: `RISK_DAILY_MAX_LOSS = -0.01`(−1.0%)을 약 **−2.5%**
  (리스크 오너 확정 — Q-1)로 완화하고 **env 로 구성 가능**하게 한다. 손실 시 여전히 트립하며,
  **비자동재개 불변**(실손실은 수동 /resume — SPEC-032)을 유지한다.
- **REQ-038-2 — 익절 마커 DB 영속화**: `position_watchdog` 의 인메모리 `_TOOK_PROFIT` 가드를
  **DB 백킹**으로 바꿔, 컨테이너 재시작을 넘어 "오늘 이미 익절함" 이 살아남아 **이중 익절을 막는다**.

> 정확히 **2개 요구**다. 세 번째 게이트(credential rotation)는 **운영자가 별도로 수동 처리**하며 본 SPEC
> 범위가 아니다(Non-Goals 명시).

### ⚠️ 긴급성 경계 (과장 금지 — no-lies)

> REQ-038-1 은 **최근 거래 0건/정지의 원인 수정이 아니다**. 진단 결과 최근 halt 는 **일일 주문수(count)
> 한도(10건)** 트립이지 **일일 손실(loss)** 트립이 아니었다. 일일손실 임계 완화는 **실제 P&L 스윙이
> 발생할 때를 위한 하드닝**이다. 어떤 산출물도 "이것이 거래 부재의 원인" 이라 함의해서는 안 된다.

---

## Goals

- **G-1 (REQ-038-1 값)**: `RISK_DAILY_MAX_LOSS` 가 −1.0% → 도출/확정값(권고 −2.5%)으로 완화되고
  env-var 로 구성 가능하다.
- **G-2 (REQ-038-1 동작 보존)**: 새 임계에서 P&L 이 한도를 넘으면 회로차단이 **여전히 트립**하고,
  daily-loss halt 는 **비자동재개**(SPEC-032 불변)로 남는다.
- **G-3 (REQ-038-2 영속화)**: 익절 1일 1회 가드가 **DB 에 영속**되어 컨테이너 재시작을 견딘다.
- **G-4 (REQ-038-2 멱등/일별)**: 마커는 거래일별이며(다음날 자연 리셋), DB UNIQUE 제약으로 **이중 익절
  불가**. 재시작 재현 테스트가 이를 증명한다.
- **G-5 (안전)**: paper only, live 잠금 불변. money/risk 변경은 reproduction-first(RED→GREEN).
  베이스라인 950 passed 대비 신규 회귀 0, 신규 코드 85%+ 커버리지.

---

## Requirements (EARS)

### REQ-038-1: 일일 손실 회로차단 임계 완화 (Ubiquitous + State-Driven)

시스템은 일일 손실 회로차단 임계를 정상 장중 변동을 실손실로 오인하지 않는 수준으로 완화하고, 환경변수로
구성 가능하게 해야 한다. 단, 완화된 임계에서도 실 손실은 여전히 차단·비자동재개되어야 한다.

- **(a) Ubiquitous — 값 완화** — 시스템의 `RISK_DAILY_MAX_LOSS` 기본값은 −1.0%(−0.01)에서
  **약 −2.5%(−0.025)** 로 완화한다. 정확한 값은 **리스크 오너가 run 시점에 확정**한다(Q-1: −2.0/−2.5/−3.0%).
- **(b) Ubiquitous — 구성 가능** — 임계는 환경변수(예 `RISK_DAILY_MAX_LOSS`)로 오버라이드 가능해야 한다.
  현 코드에 risk 상수 env-var 선례가 없으므로, **모듈 상수 + `os.getenv` 폴백**(권고) 또는 BaseSettings
  필드 승격 중 하나를 쓴다(Q-2). 페르소나-불변 하드 리밋이라는 의미는 유지한다.
- **(c) State-Driven — 트립 보존** — **While** 당일 손익(`daily_pnl_pct`)이 완화된 임계 이하이면,
  시스템은 회로차단 breach(`"daily_loss: ..."` 접두사)를 **여전히 기록**하고 NEW 주문을 차단한다
  (`limits.py` 로직·breach 접두사 무변경 — 값만 참조).
- **(d) Unwanted — 비자동재개 불변** — 시스템은 daily-loss 트립을 **자동 재개해서는 안 된다**.
  자동재개(SPEC-032 `auto_resume`)는 breach 문자열의 `"daily_loss"` 접두사로 키잉되므로 **임계 값 변경과
  무관하게** 실손실은 **수동 /resume** 만 가능해야 한다.
- **(e) Ubiquitous — 정합성 근거** — 본 완화는 SPEC-037 이 도입한 포지션별 스톱 플로어(−10% 등)와
  정합한다: 단일 포지션 정상 스톱이 포트폴리오 일일한도를 트립시키지 않도록 한다.

#### Acceptance Criteria — REQ-038-1

- [ ] `RISK_DAILY_MAX_LOSS` 기본값이 완화값(확정 시 −0.025 등)이고, env-var 오버라이드가 동작한다
      (env 설정/미설정 parametrize 테스트).
- [ ] 당일 손익이 **완화된 임계 이하**일 때 회로차단이 트립한다(`"daily_loss"` breach 기록 — 재현 테스트).
- [ ] 당일 손익이 −1%~완화임계 **사이**일 때(과거엔 트립, 이제는 정상)는 트립하지 않는다(하드닝 검증).
- [ ] daily-loss 트립은 `auto_resume` 가 **재개를 거부**한다(`(False, "daily_loss", ...)` — 회귀 테스트,
      SPEC-032 불변 잠금).
- [ ] `limits.py` 의 daily_loss 분기 로직/접두사는 **diff 무변경**(값 참조만 — 리뷰 확인).

**Dependencies**: `src/trading/config.py`, `src/trading/risk/limits.py`(참조), `risk/auto_resume.py`(불변 회귀).

---

### REQ-038-2: 익절(take-profit) 마커 DB 영속화 (Ubiquitous + Unwanted)

시스템은 "오늘 이미 익절함" 마커를 DB 에 영속해, 컨테이너 재시작 후에도 같은 거래일 같은 종목의 익절
반매도가 **반복되지 않도록** 해야 한다.

- **(a) Unwanted — 이중 익절 금지** — 시스템은 같은 거래일 같은 종목에 대해 익절 반매도를 **두 번
  실행해서는 안 된다**. 현 인메모리 `_TOOK_PROFIT` dict 는 재시작 시 리셋되어 14:00 익절 후 재시작하면
  14:30 에 다시 익절(이중 반매도)할 수 있다 — 이를 제거한다.
- **(b) Ubiquitous — DB 백킹** — `_took_profit_today(ticker)` 와 `_mark_took_profit(ticker)` 의
  백킹 스토어를 **DB** 로 전환한다. 두 함수의 **서명은 유지**(호출부 변경 최소). `classify_holding`
  순수함수는 **무변경**.
- **(c) Ubiquitous — 거래일별 + 멱등** — 마커는 **거래일(KST date)별**이며, 다음 거래일에는 자연히
  리셋된다(`trading_day != today`). DB UNIQUE 제약으로 같은 (거래일, 종목, 액션) 중복 기입을 거부한다
  (`ON CONFLICT DO NOTHING`).
- **(d) Ubiquitous — 저장소** — 전용 테이블 `position_action_markers(trading_day, ticker, action,
  created_at)` + `UNIQUE(trading_day, ticker, action)` 를 권고한다(audit_log 쿼리 재활용은 대안 — Q-4).
  마이그레이션은 멱등 하우스 스타일(026 모범), 번호 **028**(027 은 SPEC-037 예약 — Q-3).
- **(e) Event-Driven — 기록 시점** — **When** 워치독이 익절 반매도를 실행하면, **then** 시스템은
  같은 트랜잭션 흐름에서 DB 마커를 기입한다(실패 시 per-ticker 에러 격리로 흡수 — 사이클 abort 금지).

#### Acceptance Criteria — REQ-038-2

- [ ] **재시작 시뮬레이션**(인메모리 상태 초기화 + DB 마커 살아있음을 ScriptedCursor 로 모킹) 후
      `_took_profit_today(ticker)` 가 같은 거래일에 **여전히 True** 를 반환한다(이중 익절 방지 — RED→GREEN).
- [ ] 다음 거래일(`_today_kst` mock 변경)에는 `_took_profit_today(ticker)` 가 **False**(자연 리셋).
- [ ] `_mark_took_profit` 가 같은 (거래일, 종목, 'take_profit') 를 **두 번 기입해도** DB UNIQUE 로
      행이 1개만 존재한다(멱등 — `ON CONFLICT DO NOTHING` 검증).
- [ ] 마이그레이션 `028_position_action_markers.sql` 이 멱등(재실행해도 오류 없음)이고 `schema_migrations`
      에 등록된다.
- [ ] 재현 테스트: 익절 후 재시작 → 동일 종목 익절 신호 → **두 번째 반매도 미발생**(`classify_holding`
      이 `("skip", 0)` — took_profit_today=True).
- [ ] DB 마커 기입 실패 시 워치독 폴이 crash 하지 않고 해당 종목만 건너뛴다(graceful — 음성 테스트).

**Dependencies**: `src/trading/watchers/position_watchdog.py`, 신규 `db/migrations/028_position_action_markers.sql`, `db/session`(audit/connection).

---

## Specifications

### S-1: REQ-038-1 임계 완화 + env 폴백 (권고안)

```python
# src/trading/config.py — 페르소나-불변 하드 리밋(런타임 가변 아님) → 모듈 상수 + env 폴백.
RISK_DAILY_MAX_LOSS: Final[float] = float(os.getenv("RISK_DAILY_MAX_LOSS", "-0.025"))  # 기본 -2.5%
```

- `limits.py` L118–121 의 daily_loss 분기와 **breach 접두사 `"daily_loss"`** 는 그대로 둔다(값만 참조).
- ⇒ `auto_resume._DAILY_LOSS_PREFIX = "daily_loss"` 매칭이 유지되어 **비자동재개 불변이 자동 보존**된다.
- 정확한 값은 리스크 오너 확정(Q-1). −2.5% 는 권고 기본.

### S-2: REQ-038-2 마이그레이션 (028, 026 하우스 스타일)

파일명: `src/trading/db/migrations/028_position_action_markers.sql`

```sql
-- SPEC-TRADING-038 REQ-038-2: 익절 1일 1회 가드의 DB 영속화(재시작 내성 → 이중 익절 방지).
-- 멱등: CREATE TABLE IF NOT EXISTS + schema_migrations ON CONFLICT (026 하우스 스타일).

CREATE TABLE IF NOT EXISTS position_action_markers (
    id           BIGSERIAL    PRIMARY KEY,
    trading_day  DATE         NOT NULL,             -- KST 거래일
    ticker       TEXT         NOT NULL,
    action       TEXT         NOT NULL,             -- 예: 'take_profit'
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT position_action_markers_uniq UNIQUE (trading_day, ticker, action)
);

CREATE INDEX IF NOT EXISTS position_action_markers_lookup_idx
    ON position_action_markers (trading_day, ticker, action);

COMMENT ON TABLE position_action_markers IS
    'SPEC-TRADING-038 REQ-038-2: 포지션 액션 1일 1회 가드(예 익절)의 재시작 내성 영속 마커.';

INSERT INTO schema_migrations (version) VALUES ('028_position_action_markers')
    ON CONFLICT DO NOTHING;

INSERT INTO audit_log (event_type, actor, details)
VALUES ('SCHEMA_MIGRATED', 'init', '{"migration":"028_position_action_markers"}'::JSONB);
```

> 번호 028: 026 이 디스크 최신, 027 은 SPEC-037 이 선택적으로 예약(미생성). 충돌 회피 우선(Q-3).

### S-3: REQ-038-2 DB 백킹 함수 (서명 유지)

```python
# position_watchdog.py — 서명 유지, 백킹만 DB 로.
def _took_profit_today(ticker: str) -> bool:
    # SELECT 1 FROM position_action_markers
    #  WHERE trading_day=%s AND ticker=%s AND action='take_profit'
    # _today_kst() 를 trading_day 로. 결과 존재 → True.

def _mark_took_profit(ticker: str) -> None:
    # INSERT INTO position_action_markers (trading_day, ticker, action)
    # VALUES (%s, %s, 'take_profit') ON CONFLICT DO NOTHING
```

- `_today_kst()` 는 테스트 seam 으로 유지(다음날 리셋 검증). 인메모리 dict 는 제거(또는 폴 내 1회 캐시로 강등).
- DB 호출 실패는 `poll_position_watchdog` 의 per-ticker try/except 가 흡수(graceful — REQ-038-2 e).

---

## Constraints (구현 제약 — 반드시 준수)

- **C-1 (reproduction-first — HARD)**: money/risk 로직이므로 양 요구의 각 수정은 **실패하는 재현 테스트
  우선**(CLAUDE.md HARD Rule 4). 수정 전 RED, 수정 후 GREEN.
- **C-2 (paper only / live 잠금)**: 모든 변경은 paper 에서만 활성. live 는 **잠금 유지**(SPEC-002).
- **C-3 (긴급성 과장 금지 — no-lies)**: REQ-038-1 은 거래 부재의 원인이 아니라 P&L 스윙 대비 하드닝임을
  커밋/리포트에 정확히 기술한다(최근 halt = count 트립).
- **C-4 (재사용)**: `limits.py` daily_loss 분기·breach 접두사, `auto_resume` 불변, `position_watchdog`
  `classify_holding`(순수), `db/session`(audit/connection), 026 마이그레이션 하우스 스타일,
  `ScriptedCursor` 테스트 더블을 재사용한다. 중복 구현 금지.
- **C-5 (테스트)**: `.venv/bin/python -m pytest`(docker 이미지에 pytest 없음). 베이스라인 **950 passed**.
  신규 회귀 0, 신규 코드 85%+(TRUST 5).
- **C-6 (lint/Python 룰)**: ruff(BLE001 → `# noqa: BLE001` 금지(RUF100); 평범한 `except Exception:`),
  타입힌트, bare except 금지, mutable default 금지, print 아닌 logging.
- **C-7 (마이그레이션)**: raw SQL `028_*.sql`, 순차, 멱등(IF NOT EXISTS + ON CONFLICT), `migrate.py`
  자동 발견. 재배포 후 `docker exec trading-app trading migrate` **수동 실행**(자동 boot 미적용).
- **C-8 (브랜치)**: 작업 브랜치는 이미 `fix/SPEC-TRADING-026-overheating-softening`. 신규 브랜치 생성
  금지, 커밋/배포는 오케스트레이터가 처리. 구현 코드 작성 금지(본 SPEC = 명세만).

---

## Deferred / Non-Goals (명시적 비목표)

- **자격증명 회전(credential rotation)**: 세 번째 live-readiness 게이트. **운영자가 별도로 수동 처리**
  (handled separately by the operator). 코드 변경 없음 — 본 SPEC 범위 밖.
- **entry/LLM 판단 로직 변경**: 매수 시그널·confidence·종목 선정·HOLD 편향 등 **건드리지 않는다**.
- **SPEC-037 출구 임계 변경**: 포지션별 스톱/익절 임계값(SPEC-037 도출)은 **변경하지 않는다**. 본 SPEC 은
  포트폴리오 **일일 손실 한도**(별개 회로차단)만 완화한다.
- **daily-order-COUNT 한도 변경 없음**: `RISK_DAILY_ORDER_COUNT_MAX`(10) 는 무변경. 본 SPEC 은 daily-LOSS
  한도만 다룸(둘은 별개 — C-3).
- **live(실거래) 활성화**: paper 검증까지. live 전환은 별도 사용자 승인 + 별도 SPEC(SPEC-002/C-2).
- **회로차단/auto_resume 로직 변경**: daily_loss 비자동재개 불변(SPEC-032)은 **보존만** 하고 로직은 무변경.

---

## Risks

| ID | 리스크 | 영향 | 가능성 | 완화 |
|---|---|---|---|---|
| R-1 | 일일손실 임계 완화가 **실손실 보호를 느슨하게** 함 | Critical | Low | −2.5% 는 여전히 보수적 하드 게이트. 비자동재개 불변 유지(수동 /resume). 값은 리스크 오너 확정(Q-1) |
| R-2 | 완화가 "거래 부재의 원인 수정" 으로 **과대 해석** | Medium | Medium | C-3: 최근 halt = count 트립임을 명시. 본 변경은 하드닝(no-lies) |
| R-3 | DB 마커 조회가 **워치독 폴 지연/실패** 유발 | Medium | Low | per-ticker try/except 흡수(graceful, REQ-038-2 e). 단순 인덱스 조회 |
| R-4 | DB 마커와 인메모리 캐시 **불일치**(이중 소스) | Medium | Low | 인메모리 dict 제거(또는 폴-로컬 1회 캐시로 강등). DB 가 단일 진실원 |
| R-5 | 마이그레이션 번호 **028 비연속**(027 미생성 시) | Low | Medium | 번호 충돌보다 안전. SPEC-037 027 미사용 확정 시 027 로 당김(Q-3). migrate.py 는 등록 버전만 검사 |
| R-6 | env 폴백이 **잘못된 부호/형식**(양수 등) 유입 | High | Low | 음수 fraction 검증/테스트(parametrize). 기본값 −0.025 명시 |
| R-7 | 익절 마커 영속화가 **정상 1일 1회 동작**을 깨뜨림 | High | Low | `classify_holding` 무변경, 서명 유지. RED→GREEN 재현 테스트로 다음날 리셋·당일 차단 모두 검증 |

---

## Open Questions

- **Q-1 (run/리스크 오너 확정 — 임계값)**: `RISK_DAILY_MAX_LOSS` 신규 값을 **−2.0% / −2.5% / −3.0%**
  중 무엇으로? 오케스트레이터가 run 시점에 정확한 수치를 확정한다. 기본 권고 **−2.5%**.
- **Q-2 (env 구성 방식)**: (A) 모듈 상수 + `os.getenv` 폴백(권고 — 하드 리밋 의미 보존) vs
  (B) `BaseSettings` 필드 승격(런타임 가변, 하드 리밋엔 과함). run 확정.
- **Q-3 (마이그레이션 번호)**: **028**(권고 — 027 은 SPEC-037 이 선택적으로 예약, 디스크 미생성) vs
  027(SPEC-037 이 027 을 끝내 안 쓴다고 확정될 경우). run 확정.
- **Q-4 (마커 저장소)**: **전용 테이블 `position_action_markers`**(권고 — UNIQUE 제약 = 이중 익절 DB
  레벨 방지) vs `audit_log` 쿼리 재활용(테이블 추가 없음, 조회 복잡). run 확정.

---

## Traceability

| 요구 | 영향 파일 | 테스트(신규/갱신) |
|---|---|---|
| REQ-038-1 | `config.py`(`RISK_DAILY_MAX_LOSS` 완화+env), `risk/limits.py`(값 참조·무변경), `risk/auto_resume.py`(불변 회귀) | `tests/risk/test_daily_loss_limit.py`(신규) + 기존 limits 테스트 갱신 |
| REQ-038-2 | `watchers/position_watchdog.py`(`_took_profit_today`/`_mark_took_profit` DB 백킹), `db/migrations/028_position_action_markers.sql`(신규), `db/session`(재사용) | `tests/watchers/test_take_profit_persistence.py`(신규, ScriptedCursor 재시작 시뮬레이션) |

| 외부 의존 | 설명 |
|---|---|
| SPEC-TRADING-037 | 엣지 검증 감사가 게이트 표면화 + 포지션별 스톱 플로어 → 일일한도 정합성 근거(REQ-038-1 e). 027 마이그레이션 예약 → 028 채택 근거 |
| SPEC-TRADING-033 | `position_watchdog` 인메모리 익절 가드(REQ-038-2 수정 대상) + direct-sell 흐름 |
| SPEC-TRADING-032 | `auto_resume` daily_loss 비자동재개 불변(REQ-038-1 d acceptance 근거) |
| SPEC-TRADING-002 | 실거래 분리 — live 잠금 유지(C-2) |
| SPEC-TRADING-001 | 5대 하드 리밋 원천(`RISK_DAILY_MAX_LOSS`) |

---

## Verification Notes (no-lies / scope honesty)

- 본 SPEC 은 **명세만** 산출한다(구현 코드 없음 — C-8).
- REQ-038-1 의 긴급성은 **하드닝 수준**이다(최근 halt 는 count 트립, loss 트립 아님 — research §1-4).
- 자격증명 회전은 **운영자 수동**이며 본 SPEC 의 어떤 산출물도 그것을 코드로 처리했다고 함의하지 않는다.
- 마이그레이션 번호(028)·임계값(−2.5%)·저장소(전용 테이블)는 모두 **권고**이며 run/사용자 확정 대상(Q-1~Q-4).
