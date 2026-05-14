---
id: SPEC-TRADING-022
version: 0.1.0
status: draft
created: 2026-05-14
updated: 2026-05-14
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "Data refresh layer 2-bug hotfix — flows stale + holdings schema"
related_specs:
  - SPEC-TRADING-019
  - SPEC-TRADING-020
---

# SPEC-TRADING-022 -- Data refresh layer 2-bug hotfix

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-14 | 0.1.0 | Initial draft — SPEC-019/020 follow-up. 2 EARS requirements, flows refresh silent skip + active_holdings universe crash. ~20 LOC across 4 files. | onigunsow |

---

## Scope Summary

본 SPEC 은 **SPEC-019 (data refresh layer + universe registry)** 와 **SPEC-020 (DEFAULT_WATCHLIST bias removal)** 의 직접적인 follow-up hotfix 이다. SPEC-019 가 만든 refresh layer 와 universe registry 의 두 가지 별개 버그가 운영 중 발견되었다.

### Bug #1 — flows refresh silent 0-rows (verified evidence)

5/14 09:00 KST stale-monitor cron 이 9일째 flows 테이블에 대한 STALE DATA 알림을 전송 중. flows.max(ts) 는 5/8 에 멈춰있으나 OHLCV 는 매일 갱신됨. 근본 원인 (`src/trading/scripts/refresh_market_data.py:127-137`):

```python
def _fetch_flows_for_ticker(ticker, today_override=None):
    today = today_override or date.today()
    last_ts = _get_latest_ohlcv_ts(ticker)  # BUG: ohlcv 의 ts 를 사용
    if last_ts is None:
        start = today - timedelta(days=BACKFILL_WINDOW_DAYS)
    else:
        start = last_ts + timedelta(days=1)
    if start > today:
        return 0
    return _pykrx_fetch_flows(ticker, start, today)
```

**실패 시퀀스**:

1. 16:00 `refresh_ohlcv` 실행 → ohlcv.max(ts) = today
2. 16:05 `refresh_flows` 실행 → `_get_latest_ohlcv_ts(ticker)` 가 today 반환
3. `start = today + 1day = tomorrow`
4. `if start > today: return 0`
5. metrics: `success_count=58, total_rows_upserted=0` (silent — healthy 처럼 보임)

SPEC-019 의 stale-monitor (REQ-019-3) 는 정확히 9일 째 알림 중이나, refresh 로직 자체가 문제를 고치지 못함. flows 와 ohlcv 의 latest_ts 가 독립적으로 추적되어야 함.

### Bug #2 — active_holdings universe source crashes (verified evidence)

5/13 16:05 scheduler 로그:

```
WARNING trading.data.universe universe source 'active_holdings' failed:
  column "shares" does not exist
```

SPEC-019 의 manager-tdd 가 `_get_active_holdings()` 를 `SELECT DISTINCT ticker FROM positions WHERE shares > 0` 으로 구현했으나, 실제 `positions` 테이블의 컬럼명이 다름 (`qty`, `quantity`, 또는 기타). 매 cycle 마다 warning 발생하지만 SPEC-019 의 `_safe_call` 헬퍼가 흡수 → universe assembly 는 계속 진행되어 user-facing 영향은 제한적이나, holdings 가 universe 에서 누락됨.

### 본 SPEC 의 위치

- **SPEC-016 Phase 1 (완료)**: persona 파이프라인 안정화
- **SPEC-018 (완료, `5734034`)**: micro persona blocked-ticker 인식
- **SPEC-019 (완료, `3d78aa9`)**: data refresh + universe registry
- **SPEC-020 (완료, `4efb8c5` 라인)**: DEFAULT_WATCHLIST bias removal
- **SPEC-022 (본 SPEC, high)**: SPEC-019 의 두 운영 버그 hotfix

본 SPEC 은 SPEC-019 의 의도된 효과 (autonomous refresh + universe assembly) 가 모든 source 에서 일관되게 작동하도록 정정한다.

### 비즈니스 임팩트

- flows 테이블 9일 stale → 매일 갱신으로 정상화
- 사용자에게 매일 09:00 전송되는 useless STALE DATA 알림 제거
- active_holdings 가 universe 에 정상 반영 → portfolio 보유 종목 monitoring 정상화
- stale-monitor 신뢰도 회복 (현재는 알림이 와도 refresh 가 못 고치는 무력화 상태)

---

## Environment

- SPEC-019 + SPEC-020 의 redeploy 가 완료된 상태 (main `dfe904e`)
- 기존 `src/trading/scripts/refresh_market_data.py` — `_get_latest_ohlcv_ts` (line 56 부근) 헬퍼 존재
- 기존 `src/trading/data/universe.py` — `_get_active_holdings()` 함수 (SPEC-019 신설)
- 기존 `positions` 테이블 — 실제 schema 는 manager-tdd 가 `\d positions` 로 확인 필요
- 기존 483 test pass baseline
- 신규 코드 최소 — 기존 2개 소스 파일 정정 + 2개 테스트 파일 확장

## Assumptions

- A-1: `_get_latest_ohlcv_ts` 헬퍼는 SPEC-019 에서 검증됨 — 동일 패턴으로 `_get_latest_flows_ts` 작성 가능.
- A-2: `flows` 테이블의 schema 는 `(ticker, ts, ...)` 로 ohlcv 와 동일한 (ticker, ts) primary key 구조 — manager-tdd 가 RED 직전 확인.
- A-3: `positions` 테이블은 존재하지만 컬럼명이 SPEC-019 에서 가정한 `shares` 가 아님. manager-tdd 가 `\d positions` 로 실제 schema 확인.
- A-4: SPEC-019 의 `_safe_call` 패턴 (warning + return []) 은 유지. 본 SPEC 은 그 위에 schema-aware guard 추가.
- A-5: 5/8 → today 의 flows 갭은 SPEC-022 deploy 후 manual `refresh_flows()` invocation 으로 backfill — 본 SPEC 의 코드 변경에는 포함하지 않음 (rollout step 으로 명시).

---

## Goals

- **G-1 (Independent flows tracking)**: flows refresh 가 flows 테이블의 자체 latest_ts 를 사용.
- **G-2 (Schema-resilient holdings)**: active_holdings universe source 가 실제 컬럼명 정정 + schema mismatch 시 crash 가 아닌 graceful degradation.
- **G-3 (Operational restoration)**: 9일 누적 flows 갭 해소 (manual backfill) + 다음 09:00 stale-monitor cycle 에서 flows 알림 사라짐.

---

## Requirements

### REQ-022-1: flows refresh 가 flows 의 자체 latest_ts 사용 (Event-Driven + Ubiquitous, P0)

**When** `_fetch_flows_for_ticker(ticker, today_override)` 가 호출되면, **then** 시스템은 ohlcv 가 아닌 flows 테이블의 latest_ts 를 기준으로 backfill 범위를 결정해야 한다.

세부:

- (a) **(Ubiquitous)** 시스템은 `_get_latest_flows_ts(ticker: str) -> date | None` 헬퍼 함수를 신설해야 한다 — `flows` 테이블에 대해 `SELECT MAX(ts) FROM flows WHERE ticker = %s` 패턴 (line 56 부근의 `_get_latest_ohlcv_ts` 와 동일 구조).
- (b) **(Event-Driven)** **When** `_fetch_flows_for_ticker` 가 호출되면, `last_ts` 는 `_get_latest_flows_ts(ticker)` 의 반환값을 사용한다 — `_get_latest_ohlcv_ts(ticker)` 호출은 **제거**된다.
- (c) **(Ubiquitous)** 기존의 backfill window 로직 (`last_ts is None → today - BACKFILL_WINDOW_DAYS`, `else → last_ts + 1day`) 과 `if start > today: return 0` short-circuit 은 변경 없음.
- (d) **(Unwanted)** 시스템은 더 이상 flows refresh 단계에서 ohlcv 의 latest_ts 에 의존해서는 **안 된다** — 두 테이블의 latest_ts 는 독립적으로 추적된다.
- (e) **(Ubiquitous)** 코멘트 (`# Flows don't have a separate cache range helper; mirror OHLCV window.`) 는 새 helper 도입을 반영하도록 갱신.

**Files affected**:

- `src/trading/scripts/refresh_market_data.py` — `_get_latest_flows_ts` 신설 + `_fetch_flows_for_ticker` 정정
- `tests/scheduler/test_data_refresh_jobs.py` — regression test 추가 (silent-skip 버그 재현 → 수정 검증)

**Dependencies**: SPEC-019 REQ-019-1 ~ REQ-019-2 (refresh layer 의 기존 구조).

---

### REQ-022-2: active_holdings universe source resilience (Event-Driven + Unwanted, P0)

**When** `data.universe._get_active_holdings()` 가 호출되면, **then** 시스템은 `positions` 테이블의 실제 schema 를 기준으로 query 해야 한다. **If** schema mismatch 또는 query error 가 발생하면, **then** 시스템은 crash 없이 빈 list 와 warning 로그를 반환해야 한다.

세부:

- (a) **(Ubiquitous)** manager-tdd 는 RED 단계 직전 `\d positions` 또는 ORM model 정의를 확인하여 실제 컬럼명 (`shares` / `qty` / `quantity` / 기타) 을 식별한다.
- (b) **(Event-Driven)** **When** `_get_active_holdings()` 가 실행되면, query 는 실제 컬럼명을 사용한다 (예: `SELECT DISTINCT ticker FROM positions WHERE qty > 0`).
- (c) **(Unwanted) (Defensive guard)** **If** query 실행 중 `psycopg2.errors.UndefinedColumn` 또는 generic `Exception` 이 raise 되면, **then** 시스템은 `logger.warning` 으로 schema mismatch 를 기록하고 빈 list `[]` 를 반환해야 한다 — universe assembly 의 다른 source 는 정상 진행.
- (d) **(Ubiquitous)** SPEC-019 의 `_safe_call` wrapper 는 그대로 활용. 본 SPEC 의 추가 guard 는 `_get_active_holdings` 내부의 try/except 로 query-level 격리.
- (e) **(Unwanted)** 시스템은 schema mismatch 시 universe assembly 전체를 abort 해서는 **안 된다** — DEFAULT_WATCHLIST, screened, KOSPI200 source 는 정상 작동해야 한다.
- (f) **(Ubiquitous)** 다음 cycle 의 scheduler 로그에서 `universe source 'active_holdings' failed: column "shares" does not exist` 형태의 warning 이 더 이상 발생하지 않아야 한다 (정상 query 또는 정상 empty 반환).

**Files affected**:

- `src/trading/data/universe.py` — `_get_active_holdings()` 의 컬럼명 정정 + defensive guard
- `tests/data/test_universe.py` — schema error path regression test 추가

**Dependencies**: SPEC-019 REQ-019-6 (universe assembly 구조).

---

## Specifications

### S-1: `_get_latest_flows_ts` 의사 코드

```python
def _get_latest_flows_ts(ticker: str) -> date | None:
    """Return the most recent flows.ts for a given ticker, or None if no rows."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(ts) FROM flows WHERE ticker = %s",
                (ticker,),
            )
            row = cur.fetchone()
            return row[0] if row and row[0] is not None else None
```

(line 56 부근의 `_get_latest_ohlcv_ts` 와 mirror 구조. manager-tdd 가 실제 헬퍼 패턴 follow.)

### S-2: `_fetch_flows_for_ticker` 의 정정 의사 코드

```python
def _fetch_flows_for_ticker(ticker, today_override=None):
    today = today_override or date.today()
    last_ts = _get_latest_flows_ts(ticker)  # SPEC-022: was _get_latest_ohlcv_ts
    if last_ts is None:
        start = today - timedelta(days=BACKFILL_WINDOW_DAYS)
    else:
        start = last_ts + timedelta(days=1)
    if start > today:
        return 0
    return _pykrx_fetch_flows(ticker, start, today)
```

### S-3: `_get_active_holdings` 의 정정 의사 코드

```python
def _get_active_holdings() -> list[str]:
    """SPEC-022: schema-resilient active holdings query."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT ticker FROM positions WHERE <actual_col> > 0"
                )
                return [row[0] for row in cur.fetchall()]
    except Exception as exc:
        logger.warning(
            "active_holdings query failed (schema mismatch?): %s", exc
        )
        return []
```

(`<actual_col>` 은 manager-tdd 가 M-1 단계에서 결정.)

### S-4: Acceptance Criteria (요약 — 상세는 acceptance.md)

5 G/W/T 시나리오:

1. (REQ-022-1) flows refresh 가 5일 stale 상태에서 정상 refresh 후 max(ts) == today
2. (REQ-022-2) positions 빈 상태에서 universe 가 다른 source 로 정상 assemble
3. (REQ-022-2) schema mismatch 시 warning + 빈 list 반환, no crash
4. (operational) 5/14 09:00 user-observed STALE alert 가 deploy 후 다음 09:00 cycle 에서 사라짐
5. (REQ-022-1) 신규 ticker (281820 케이씨텍) 가 screened 에 추가된 후 flows refresh 시 정상 pull

---

## Non-Goals (Out of Scope)

본 SPEC 은 다음을 **명시적으로 다루지 않는다**:

- Micro persona 가 universe 외 ticker 를 suggest 하는 경우의 universe auto-expansion — 별도 SPEC-023 으로 추후 분리
- Persona prompt tuning
- KOSPI200 dynamic top-50 전략 — SPEC-019 Q-1 영역
- DART disclosure backfill — SPEC-019 가 이미 다룸
- Fundamentals refresh 의 동일 silent skip 가능성 — Q-1 로 manager-tdd 가 audit (본 SPEC 의 plan.md M-1 에 명시)
- 실거래 전환 — SPEC-017
- DEFAULT_WATCHLIST 5종 변경

---

## Implementation Hints (manager-tdd 참고용, 본 SPEC 에서는 구현하지 않음)

- **변경 LOC 예측**: 총 ~20 LOC (소스 2개 파일 + 테스트 2개 파일 확장).
- **schema discovery**: `psql` 또는 ORM model 파일 (`src/trading/data/models.py` 류) 에서 `positions` 의 실제 컬럼명 확인.
- **silent-skip 재현 test**: `_get_latest_ohlcv_ts` 를 mock 하여 today 반환 → `_fetch_flows_for_ticker` 가 0 반환하는 현재 동작 확인 → fix 후 정상 fetch 검증.
- **5/8 → today 갭 backfill**: 본 SPEC 의 코드 변경에는 포함하지 않음. deploy 후 사용자가 manual python -c 또는 management command 로 `refresh_flows()` 1회 invoke 하여 backfill — plan.md rollout step 에 명시.
- **회귀 영향**: 기존 483 test pass baseline 유지. 신규 ~4 test 추가 후 ~487 pass 목표.
- **Fundamentals audit (Q-1)**: `_fetch_fundamentals_for_ticker` (있다면) 가 동일 패턴의 silent skip 을 갖는지 빠른 검토.

---

## Files Expected to Change (구현 단계 참고)

| File | Change Type | Rough LOC | Owner |
|---|---|---|---|
| `src/trading/scripts/refresh_market_data.py` | Modify (helper + call) | +8 ~ +12 | manager-tdd |
| `src/trading/data/universe.py` | Modify (column + guard) | +5 ~ +10 | manager-tdd |
| `tests/scheduler/test_data_refresh_jobs.py` | Extend | +20 ~ +30 | manager-tdd |
| `tests/data/test_universe.py` | Extend | +15 ~ +25 | manager-tdd |

총 변경 LOC 추정: ~50 ~ 80 LOC, 4 파일 (소스 2 + 테스트 2). 핵심 production 변경은 ~20 LOC.

---

## Constraints

- **C-1**: backward compatible — flows.max(ts) == None (신규 ticker) 인 경우 기존 BACKFILL_WINDOW_DAYS 동작 유지.
- **C-2**: positions 테이블이 비어있는 경우 universe 가 정상 assemble (DEFAULT/screened/KOSPI200 만으로).
- **C-3**: 기존 483 test pass baseline 유지. 신규 ~4 test 추가 후 ~487 pass.
- **C-4**: Coverage ≥ 85% 유지.
- **C-5**: 본 SPEC 의 모든 변경은 git branch `feat/spec-022-data-refresh-hardening` 으로 격리, 단일 PR.
- **C-6**: 본 SPEC 은 high (not critical) — 시스템이 부분적으로 정상 동작 중. 단, 9일 stale 은 운영 degradation 이므로 빠른 hotfix 권장.

---

## Risks

| ID | 리스크 | 영향 | 가능성 | 대응 |
|---|---|---|---|---|
| R-1 | `positions` 의 실제 컬럼명 확인 실패 | Medium | Low | M-1 에서 `\d positions` + ORM model 양쪽 확인. 불확실 시 guard 만 적용하고 column query 는 follow-up |
| R-2 | flows 테이블의 (ticker, ts) primary key 구조 가정 오류 | Low | Low | A-2. manager-tdd 가 schema 확인 |
| R-3 | manual backfill 실행 시 KIS/pykrx API rate-limit 초과 | Low | Medium | 사용자가 batch 단위로 invoke. 기존 rate-limit 핸들링 활용 |
| R-4 | 신규 helper 가 connection pool 점유 | Low | Low | `_get_latest_ohlcv_ts` 패턴 그대로 follow — 검증된 패턴 |
| R-5 | Fundamentals refresh 의 동일 버그 잠재 | Low | Medium | Q-1 audit. 발견되면 본 SPEC 범위 확장 또는 별도 SPEC |

---

## Rollout Plan

### 단일 Phase — 5/14 hotfix window

1. (RED-GREEN-REFACTOR via `/moai:2-run SPEC-TRADING-022`)
   - Pre-RED M-1: `\d positions` 확인 + flows schema 확인 + fundamentals audit (Q-1)
   - RED: regression tests 작성 (silent-skip 재현 + schema-error path)
   - GREEN: helper 신설 + 분기 정정 + defensive guard (~20 LOC)
   - REFACTOR: 정리 + 483 baseline 회귀 검증
2. Coverage / lint 통과, PR 생성, 사용자 리뷰
3. `make redeploy` 로 컨테이너 재배포
4. **Manual backfill**: 사용자가 deploy 직후 `python -c "from trading.scripts.refresh_market_data import refresh_flows; refresh_flows()"` 류 1회 invoke 하여 5/8 → today 갭 복구
5. 다음 09:00 (5/15) stale-monitor cron — flows 알림 사라짐 확인
6. `/moai:3-sync SPEC-TRADING-022` 으로 문서 동기화

### Safety Gates

- **종료 전 게이트 1**: 단위 테스트 ~487 통과 (기존 483 + 신규 ~4) AND coverage ≥ 85%
- **종료 전 게이트 2**: 사용자가 직접 `make redeploy` 후 컨테이너 healthcheck 5/5 통과
- **종료 전 게이트 3**: Manual backfill 후 `SELECT MAX(ts) FROM flows;` 가 today (또는 가장 최근 거래일) 반환
- **종료 전 게이트 4**: 다음날 09:00 stale-monitor 로그에서 flows STALE 알림 없음
- **종료 전 게이트 5**: 다음 cycle scheduler 로그에서 `universe source 'active_holdings' failed` warning 없음

---

## Open Questions

- **Q-1 (audit)**: `refresh_fundamentals` (있다면) 도 동일한 silent skip pattern 을 갖는가? — M-1 단계에서 manager-tdd 가 빠른 audit. 발견 시 본 SPEC 의 REQ-022-3 으로 확장하거나 별도 follow-up SPEC.

---

## Traceability

| Requirement | Phase | Acceptance Criteria | Files Affected (대표) |
|---|---|---|---|
| REQ-022-1 | hotfix (P0) | acceptance.md 시나리오 1, 5 | `scripts/refresh_market_data.py`, `tests/scheduler/test_data_refresh_jobs.py` |
| REQ-022-2 | hotfix (P0) | acceptance.md 시나리오 2, 3 | `data/universe.py`, `tests/data/test_universe.py` |
| (Operational) | hotfix | acceptance.md 시나리오 4 | rollout step (manual backfill + 09:00 verify) |
