---
id: SPEC-TRADING-022
title: "Acceptance Criteria -- Data refresh layer 2-bug hotfix"
created: 2026-05-14
updated: 2026-05-14
status: ready_for_run
---

# Acceptance Criteria -- SPEC-TRADING-022

## Definition of Done

본 SPEC 은 다음 모든 조건이 충족될 때 `completed` 로 전환된다:

- [ ] REQ-022-1 의 P0 acceptance test 통과 (시나리오 1, 5)
- [ ] REQ-022-2 의 P0 acceptance test 통과 (시나리오 2, 3)
- [ ] (Operational) 시나리오 4 — manual backfill 후 다음 09:00 stale-monitor 에서 flows 알림 사라짐
- [ ] 기존 단위 테스트 483/483 + 신규 ~4 = ~487 모두 통과
- [ ] Coverage ≥ 85%
- [ ] ruff / black 0건 위반
- [ ] PR 사용자 리뷰 완료
- [ ] `make redeploy` 후 컨테이너 healthcheck 5/5 통과
- [ ] Manual backfill 후 `SELECT MAX(ts) FROM flows;` 가 today (또는 가장 최근 거래일) 반환
- [ ] 다음 cycle scheduler 로그에서 `universe source 'active_holdings' failed` warning 없음

---

## Test Scenarios (Given-When-Then)

### Scenario 1 — flows refresh 가 자체 latest_ts 사용 (REQ-022-1, P0)

**Given**:
- flows 테이블의 `max(ts)` 가 today - 5일 (5일 stale 상태)
- ohlcv 테이블의 `max(ts)` 가 today (정상 갱신 완료)
- `_get_latest_ohlcv_ts(ticker)` 를 mock 하여 today 반환 (기존 silent-skip 버그 재현 조건)
- `_get_latest_flows_ts(ticker)` 신설 후 today - 5 반환 가정

**When**:
- `refresh_flows()` 가 호출되어 각 ticker 에 대해 `_fetch_flows_for_ticker(ticker)` 실행

**Then**:
- `_fetch_flows_for_ticker` 가 0 을 반환하지 **않음** (silent-skip 버그 사라짐)
- `_pykrx_fetch_flows(ticker, start, today)` 가 호출됨 — `start` = today - 4일
- refresh 완료 후 flows.max(ts) ≤ 1 거래일 from today (휴장일 고려)
- metrics: `total_rows_upserted > 0`

---

### Scenario 2 — empty positions 시 universe assembly 정상 (REQ-022-2, P0)

**Given**:
- `positions` 테이블이 empty (0 rows)
- 다른 universe source (DEFAULT_WATCHLIST, screened_tickers, KOSPI200) 는 정상 작동

**When**:
- `get_data_universe()` 호출

**Then**:
- 예외 없이 정상 반환
- universe 는 DEFAULT / screened / KOSPI200 의 union 으로 assemble
- active_holdings 는 빈 list 로 처리되어 union 에 영향 없음

---

### Scenario 3 — schema mismatch 시 graceful degradation (REQ-022-2, P0)

**Given**:
- `positions` 테이블에 query 시 `psycopg2.errors.UndefinedColumn` 또는 generic Exception 발생하도록 DB connection 을 mock

**When**:
- `_get_active_holdings()` 직접 호출

**Then**:
- 함수가 예외를 raise 하지 않음
- 반환값이 빈 list `[]`
- `logger.warning` 으로 schema mismatch (또는 query failure) 기록됨
- 호출 후 `get_data_universe()` 의 다른 source 는 정상 작동 (universe assembly abort 되지 않음)

---

### Scenario 4 — 사용자 관측 STALE alert 해소 (operational, P0)

**Given**:
- 5/14 09:00 stale-monitor cron 이 사용자에게 flows STALE DATA 알림 전송 (9일 stale 누적)
- SPEC-022 이 deploy 되고 manual backfill 1회 invoke 완료

**When**:
- 5/15 09:00 (또는 deploy 후 다음 평일 09:00) stale-monitor cron 실행

**Then**:
- flows 에 대한 STALE DATA 알림이 **발송되지 않음**
- stale-monitor 의 internal check: flows.max(ts) ≥ today - threshold (SPEC-019 의 stale_threshold_days 기준)

---

### Scenario 5 — 신규 ticker 의 flows pull (REQ-022-1, P0)

**Given**:
- 신규 ticker `281820` (케이씨텍) 가 `screened_tickers.json` 에 추가됨
- flows 테이블에 281820 의 row 가 0개 (`_get_latest_flows_ts("281820")` → None)

**When**:
- `refresh_flows()` 가 호출되어 281820 에 대해 `_fetch_flows_for_ticker("281820")` 실행

**Then**:
- `last_ts` 가 None 으로 감지됨
- `start = today - BACKFILL_WINDOW_DAYS` 로 설정 (full backfill window)
- `_pykrx_fetch_flows("281820", start, today)` 호출됨
- 1회 refresh 후 `_get_latest_flows_ts("281820")` 가 today (또는 가장 최근 거래일) 반환

---

## Negative / Edge Case Scenarios

### Scenario 6 — Pre-fix 버그 재현 (regression baseline)

**Given**:
- `_get_latest_ohlcv_ts(ticker)` mock 으로 today 반환
- 기존 코드 (`last_ts = _get_latest_ohlcv_ts(ticker)`) 로 실행

**When**:
- `_fetch_flows_for_ticker(ticker)` 실행

**Then**:
- `start = today + 1day = tomorrow`
- `if start > today: return 0` short-circuit 발동
- 함수가 0 반환 — silent skip 버그 재현됨

이 시나리오는 fix 전 baseline 으로 RED 단계에서 확인. fix 후 동일 mock 으로 시나리오 1 의 결과 검증.

---

### Scenario 7 — DB connection 자체 실패 (REQ-022-2 의 extreme case)

**Given**:
- DB connection 자체가 실패 (network down, connection pool 고갈 등)

**When**:
- `_get_active_holdings()` 호출

**Then**:
- defensive guard (try/except) 가 작동
- 빈 list `[]` 반환
- warning 로그 기록
- universe assembly 의 다른 source 는 영향 받지 않음 (DEFAULT / screened / KOSPI200 가 정상 작동 가정)

---

## Verification Method

### Unit Tests

- `tests/scheduler/test_data_refresh_jobs.py` (extend) — 시나리오 1, 5, 6
- `tests/data/test_universe.py` (extend) — 시나리오 2, 3, 7

### Integration / Live Verification

- Manual backfill 후 `SELECT MAX(ts) FROM flows;` 결과 — 시나리오 4 의 사전조건 검증
- 5/15 09:00 stale-monitor cron 로그 — 시나리오 4 의 outcome 검증
- 다음 cycle scheduler 로그 (any cycle after deploy) — `universe source 'active_holdings' failed` warning 부재 확인

---

## Quality Gate Criteria

| Criterion | Target | Verification |
|---|---|---|
| Test count | 483 (baseline) + ~4 (new) = ~487 | `make test` 또는 `pytest` |
| Test pass rate | 100% | CI logs |
| Coverage | ≥ 85% | `pytest --cov` |
| ruff violations | 0 | `make lint` |
| black violations | 0 | `make format-check` |
| Container healthcheck | 5/5 | `docker compose ps` |
| flows.max(ts) post-backfill | today (또는 최근 거래일) | `psql` query |
| 5/15 09:00 stale-monitor | flows 알림 없음 | Log inspection |
| Scheduler 로그 | active_holdings warning 없음 | `journalctl` 또는 docker logs |

---

## Out-of-Scope Verification (NOT in this SPEC)

- Persona prompt tuning
- Universe auto-expansion for out-of-universe ticker suggestions — SPEC-023 (future)
- KOSPI200 dynamic top-50 — SPEC-019 Q-1
- DART disclosure backfill — SPEC-019 가 이미 다룸
- 실거래 전환 — SPEC-017
- DEFAULT_WATCHLIST 5종 변경

---

## Notes

- 본 SPEC 의 모든 unit test 시나리오는 mock 기반으로 검증 가능 — DB / 외부 API 호출 없음.
- 시나리오 4 (operational STALE alert 해소) 는 manual backfill + 다음 09:00 의 실제 cron 로그로 확인.
- 시나리오 6 은 RED 단계의 baseline. fix 후에는 통과 (해당 path 가 더 이상 작동하지 않으므로 시나리오 자체 제거 또는 historical reference 로 유지).
