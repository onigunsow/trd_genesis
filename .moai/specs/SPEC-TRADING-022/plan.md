---
id: SPEC-TRADING-022
title: "Implementation Plan -- Data refresh layer 2-bug hotfix"
created: 2026-05-14
updated: 2026-05-14
status: ready_for_run
---

# Implementation Plan -- SPEC-TRADING-022

## Context Recap

- **상위 SPEC**: SPEC-022 는 SPEC-019 (commit `3d78aa9`) + SPEC-020 (`4efb8c5` 라인) 위에 얹는 2-버그 hotfix.
- **발견 시점**:
  - Bug #1: 2026-05-14 09:00 KST. flows 테이블이 5/8 부터 9일 stale. SPEC-019 의 stale-monitor 가 정확히 매일 알림 전송 중이나 refresh 로직이 silent-skip.
  - Bug #2: 2026-05-13 16:05 scheduler 로그. `active_holdings` universe source 가 매 cycle 마다 `column "shares" does not exist` warning.
- **근본 원인**:
  - Bug #1: `_fetch_flows_for_ticker` 가 ohlcv 의 latest_ts 를 기준으로 backfill 범위 결정 → ohlcv 가 먼저 today 로 갱신된 후 flows refresh 가 항상 short-circuit (`if start > today: return 0`).
  - Bug #2: SPEC-019 manager-tdd 가 `positions` 테이블의 컬럼명을 `shares` 로 가정했으나 실제 schema 와 불일치.
- **해결 전략**: flows 자체 latest_ts helper 신설 + active_holdings 의 실제 schema 적용 + defensive guard.

## Implementation Approach

### Methodology

- **Mode**: TDD (RED-GREEN-REFACTOR) — `.moai/config/sections/quality.yaml` default.
- **Rationale**: 두 버그 모두 silent failure 또는 caught exception 으로 nature 가 regression test 로 명확히 캡처 가능. mock 기반 단위 테스트로 충분.

### Milestones (Priority-based)

본 SPEC 은 단일 Phase 의 lightweight hotfix.

**Primary Goal (P0, hotfix 출시 조건)**:

1. **M-1 (Pre-RED, schema discovery)**: 코드 + DB 탐색
   - `\d positions` (또는 `src/trading/data/models.py` 류) 로 실제 컬럼명 식별
   - `flows` 테이블의 schema 확인 — primary key 가 (ticker, ts) 인지
   - `src/trading/scripts/refresh_market_data.py:56` 부근의 `_get_latest_ohlcv_ts` 헬퍼 패턴 확인
   - (Q-1) `_fetch_fundamentals_for_ticker` 가 동일 silent-skip pattern 인지 빠른 audit
2. **M-2 (RED, flows silent-skip)**: `tests/scheduler/test_data_refresh_jobs.py` 확장 — `_get_latest_ohlcv_ts` mock 으로 today 반환 + flows 의 last_ts 가 5일 전인 시나리오에서 `_fetch_flows_for_ticker` 의 반환값 검증. 현재 logic 에서는 0 반환 (silent skip), fix 후에는 정상 fetch. 실패 확인.
3. **M-3 (RED, schema mismatch)**: `tests/data/test_universe.py` 확장 — DB query 가 `UndefinedColumn` 류 exception raise 하도록 mock + `_get_active_holdings()` 가 crash 없이 빈 list 반환 검증. 실패 확인.
4. **M-4 (GREEN, flows helper)**: `src/trading/scripts/refresh_market_data.py` 에 `_get_latest_flows_ts(ticker)` 신설 + `_fetch_flows_for_ticker` 의 `last_ts = _get_latest_ohlcv_ts(ticker)` → `last_ts = _get_latest_flows_ts(ticker)` 로 교체 (~8 LOC). M-2 통과.
5. **M-5 (GREEN, holdings query)**: `src/trading/data/universe.py:_get_active_holdings` 의 컬럼명을 M-1 에서 확인한 실제 값으로 정정 + try/except 로 defensive guard 추가 (~7 LOC). M-3 통과.
6. **M-6 (REFACTOR)**: 코멘트 정리 (line 127-129 의 misleading 코멘트), type hint 보강, 기존 483 baseline 회귀 확인, coverage ≥ 85% 검증.
7. **M-7 (Deploy)**: PR `feat/spec-022-data-refresh-hardening` 생성, 사용자 리뷰, `make redeploy`, healthcheck 5/5.
8. **M-8 (Manual backfill)**: 사용자가 deploy 직후 1회 invoke:
   ```bash
   docker compose exec trading python -c \
     "from trading.scripts.refresh_market_data import refresh_flows; refresh_flows()"
   ```
   5/8 → today 의 9일 갭 복구.
9. **M-9 (Backfill verification)**: `SELECT MAX(ts) FROM flows;` 가 today (또는 가장 최근 거래일) 반환 확인.
10. **M-10 (Cron gate)**: 5/15 09:00 stale-monitor cron — flows STALE 알림 사라짐 확인.
11. **M-11 (Holdings gate)**: 다음 cycle scheduler 로그 — `universe source 'active_holdings' failed` warning 사라짐 확인.

**Secondary Goal (P1, optional follow-up)**:

12. **M-12**: Q-1 의 fundamentals audit 결과 — 동일 패턴 발견 시 follow-up SPEC 또는 본 SPEC 의 REFACTOR 단계에서 추가 fix.
13. **M-13**: `/moai:3-sync SPEC-TRADING-022` — CHANGELOG 갱신, SPEC 상태 `completed` 로 전환.

---

## Technical Approach

### A. flows 자체 latest_ts helper (REQ-022-1)

**현재 (line 56 부근의 ohlcv helper, 검증된 패턴)**:

```python
def _get_latest_ohlcv_ts(ticker: str) -> date | None:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(ts) FROM ohlcv WHERE ticker = %s",
                (ticker,),
            )
            row = cur.fetchone()
            return row[0] if row and row[0] is not None else None
```

**신설 (mirror 구조)**:

```python
def _get_latest_flows_ts(ticker: str) -> date | None:
    """SPEC-022: independent latest_ts for flows table."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(ts) FROM flows WHERE ticker = %s",
                (ticker,),
            )
            row = cur.fetchone()
            return row[0] if row and row[0] is not None else None
```

**`_fetch_flows_for_ticker` 변경 (1 line)**:

```python
# was: last_ts = _get_latest_ohlcv_ts(ticker)
last_ts = _get_latest_flows_ts(ticker)
```

코멘트 (line 127-128) 도 갱신:

```python
# was: # Flows don't have a separate cache range helper; mirror OHLCV window.
# new: # SPEC-022: flows table tracks its own latest_ts independent of ohlcv.
```

### B. active_holdings schema 정정 + defensive guard (REQ-022-2)

**현재 (가정 위치, manager-tdd 가 확인)**:

```python
def _get_active_holdings() -> list[str]:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ticker FROM positions WHERE shares > 0"
            )
            return [row[0] for row in cur.fetchall()]
```

**변경**:

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

`<actual_col>` 은 M-1 단계에서 manager-tdd 가 결정 (예상: `qty`, `quantity`, 또는 SQLAlchemy ORM model 의 Column 이름).

### C. Manual backfill (M-8, 코드 변경 아님)

본 SPEC 의 코드는 backfill 을 자동으로 실행하지 않는다. deploy 후 사용자가 1회 수동 invoke:

```bash
docker compose exec trading python -c \
  "from trading.scripts.refresh_market_data import refresh_flows; refresh_flows()"
```

근거: SPEC-019 의 BACKFILL_WINDOW_DAYS 가 충분히 길어야 (예: 30일) 9일 갭을 cover. manager-tdd 가 M-1 단계에서 BACKFILL_WINDOW_DAYS 값 확인하여 부족 시 manual 한정으로 일시적 확장 또는 multiple invocation.

---

## Risks and Mitigation

| ID | 리스크 | 대응 (M-x 매핑) |
|---|---|---|
| R-1 | positions 컬럼명 확인 실패 | M-1 에서 `\d positions` + ORM model 양쪽 확인 |
| R-2 | flows schema 가정 오류 | M-1 에서 확인. (ticker, ts) primary key 가 아니라면 query 패턴 조정 |
| R-3 | manual backfill 시 API rate-limit | 사용자가 batch 또는 multiple invocation 으로 분산 |
| R-4 | 신규 helper 의 connection pool 점유 | `_get_latest_ohlcv_ts` 패턴 그대로 follow |
| R-5 | fundamentals 의 동일 silent-skip | Q-1 의 M-1 audit. 발견 시 본 SPEC 또는 follow-up |

---

## Dependencies

- **Hard dependency**: SPEC-019 (merged `3d78aa9`) — refresh layer 와 universe registry 의 구조.
- **Hard dependency**: SPEC-020 (merged `4efb8c5` 라인) — DEFAULT/screened semantics 확정.
- **Soft dependency**: 483 test pass baseline — 회귀 없음 검증 기준.

---

## Notes

- 본 SPEC 은 **2-bug hotfix** — 신규 모듈 0개, 신규 파일 0개, 수정 2 파일 (소스) + 확장 2 파일 (테스트).
- 핵심 production 변경은 ~20 LOC. 테스트 포함 총 ~50-80 LOC.
- 시간 (clock-time) 추정 금지. priority-based milestone 으로만 표현.
- Manual backfill (M-8) 은 본 SPEC 의 코드 책임 밖이나 acceptance 의 일부.
