---
id: SPEC-TRADING-019
title: "Acceptance Criteria -- Market data automated refresh layer"
created: 2026-05-11
updated: 2026-05-11
status: ready_for_run
---

# Acceptance Criteria -- SPEC-TRADING-019

## Definition of Done

본 SPEC 은 다음 모든 조건이 충족될 때 `completed` 로 전환된다:

- [ ] REQ-019-1 ~ REQ-019-6 의 모든 P0 acceptance test 통과
- [ ] (선택) REQ-019-7, REQ-019-8 의 P1 acceptance test 통과 또는 명시적 follow-up 결정
- [ ] 기존 단위 테스트 65/65 (SPEC-018 baseline) + 신규 ~25 = ~90/90 모두 통과
- [ ] Coverage ≥ 85% (`.moai/config/sections/quality.yaml`)
- [ ] ruff / black 0건 위반
- [ ] PR 사용자 리뷰 완료
- [ ] `make redeploy` 후 컨테이너 healthcheck 5/5 통과
- [ ] APScheduler 로그에서 5개 신규 cron 잡 등록 확인 (`data_refresh_ohlcv`, `data_refresh_flows`, `data_refresh_fundamentals`, `data_refresh_disclosures`, `data_freshness_check`)
- [ ] 5/12 09:00 stale-monitor cron 이 실제 gap 감지 후 Telegram 알람 송출 (REQ-019-5 의 자기검증)
- [ ] 5/12 16:00 OHLCV cron 의 metric 로그에서 `success_count ≥ 50`, `error_count ≤ 5`
- [ ] 5/13 09:30 첫 intraday cycle 에서 micro persona universe ≥ 5종 + decision persona signals 비어있지 않음
- [ ] 5/14 09:00 stale-monitor 가 알람 송출하지 않음 (false-positive 검증)

---

## Test Scenarios (Given-When-Then)

### Scenario 1 — REQ-019-1 OHLCV refresh 정상 동작 (P0)

**Given**:
- scheduler 가 mon-fri 16:00 KST 에 도달
- `is_trading_day()` 가 True 를 반환 (오늘은 KRX 거래일)
- `get_data_universe()` 가 ≥ 50개 ticker 를 반환 (DEFAULT_WATCHLIST 5종 + screened 20종 + holdings 0~5종 + KOSPI200 top-50)
- `pykrx_adapter.fetch_ohlcv` 가 정상 작동

**When**:
- APScheduler 의 `data_refresh_ohlcv` cron 트리거 발생
- `refresh_market_data.refresh_ohlcv()` 호출

**Then**:
- `ohlcv.max(ts)` ≤ today 또는 today - 1 (KRX EOD 데이터 가용성에 따라)
- universe 의 각 ticker 에 대해 `cache.upsert_ohlcv` 가 호출됨
- 반환 metric dict 에 `success_count ≥ len(universe) * 0.9` (실패율 10% 이하)
- 반환 metric dict 에 `total_rows_upserted ≥ len(universe)` (각 ticker 당 ≥ 1 row)
- INFO 로그에 metric 출력 (`success_count`, `error_count`, `total_rows_upserted`, `duration_seconds`)

---

### Scenario 2 — REQ-019-5 stale 감지 + Telegram 알람 (P0)

**Given**:
- OHLCV 테이블의 max(ts) 가 today - 2 (48 시간 stale)
- 오늘은 KRX 거래일이고 어제도 거래일이었음 (휴장일 보정 없이 36h 초과 확정)
- Telegram client 가 mock 으로 호출 capture

**When**:
- scheduler 가 09:00 KST 에 도달
- `data_freshness.check_and_alert()` 호출

**Then**:
- 30 초 이내 Telegram client 의 `send_message` 가 호출됨
- 알람 메시지에 다음 모두 포함:
  - 문자열 `ohlcv`
  - 문자열 `latest:` 와 `today - 2` 의 ISO 일자
  - 문자열 `expected:` 와 직전 trading day 의 ISO 일자
  - 문자열 `stale` 또는 `days stale`
- 알람 메시지가 prod chat_id 로 송출 (`TELEGRAM_PROD_CHAT_ID` 또는 `.env` 의 명시된 변수)

---

### Scenario 3 — REQ-019-1 (c) + REQ-019-6 신규 screened ticker 캐시 backfill (P0)

**Given**:
- 신규 ticker Y (예: `005380` 현대차) 가 06:30 daily_screen 으로 `screened_tickers.json` 에 추가됨
- ticker Y 는 이전에 캐시된 적이 없음 (`cache.get_latest_ohlcv_ts("005380")` 가 None 반환)
- `pykrx_adapter.fetch_ohlcv` 가 90일치 데이터를 정상 반환

**When**:
- 16:00 KST `refresh_ohlcv()` 호출

**Then**:
- ticker Y 에 대해 `fetch_ohlcv("005380", today - 90 days, today)` 호출 (90일 backfill)
- `ohlcv` 테이블에 ticker Y 의 row 가 ≥ 90 개 (영업일 수에 따라 다소 변동 허용)
- 다음 날 cron 실행 시 ticker Y 는 incremental fetch (마지막 ts + 1d ~ today) 로 전환

---

### Scenario 4 — REQ-019-4 DART gap 자동 backfill (P0)

**Given**:
- `disclosures.max(rcept_dt)` 가 today - 11 (11일 stale, 오늘의 발견 상황)
- `dart_adapter.list_recent` 가 정상 작동

**When**:
- 배포 후 첫 disclosure cron (18:00) 실행
- `refresh_market_data.refresh_disclosures()` 호출

**Then**:
- 시스템이 자동으로 `--recent 12` 동등 모드로 전환 (today - 12 ~ today)
- `dart_adapter.list_recent(today - 12, today)` 호출
- 호출 후 `disclosures.max(rcept_dt) ≥ today - 1`
- 다음 날 cron 실행 시 일반 모드 (`today - 1 ~ today`) 로 복귀

---

### Scenario 5 — REQ-019-1 (d) per-ticker 실패 격리 (P0)

**Given**:
- universe 가 [A, B, Z, C, D] 5개 ticker
- `pykrx_adapter.fetch_ohlcv("Z", ...)` 가 `requests.exceptions.ConnectionError` 를 raise
- 다른 4개 ticker 는 정상 fetch

**When**:
- `refresh_ohlcv()` 호출

**Then**:
- ticker A, B, C, D 의 fetch 가 모두 호출됨 (Z 에서 batch 중단 금지)
- ticker A, B, C, D 의 row 가 정상 upsert
- 반환 metric dict 에 `success_count == 4`, `error_count == 1`
- logger.warning 으로 ticker Z 의 실패 기록 (예: `OHLCV fetch failed for Z: ConnectionError(...)`)
- batch 전체가 정상 종료 (예외가 cron 호출자까지 전파되지 않음)

---

### Scenario 6 — REQ-019-1 (f) 휴장일 가드 (P0)

**Given**:
- 오늘이 Saturday (KRX 휴장일)
- `is_trading_day()` 가 False 를 반환

**When**:
- (이론적으로) `data_refresh_ohlcv` cron 트리거 발생 — 단, `day_of_week="mon-fri"` 설정으로 APScheduler 가 트리거 자체를 발생시키지 않음
- 만약 수동으로 `_wrap("data_refresh_ohlcv", refresh_market_data.refresh_ohlcv)` 호출

**Then**:
- `_wrap` 의 `is_trading_day()` 가드가 True 가 아니므로 `refresh_ohlcv()` 호출되지 않음
- INFO 로그에 `"data_refresh_ohlcv skipped (non-trading day: <reason>)"` 출력
- 외부 API 호출 0회

---

### Scenario 7 — REQ-019-3 fundamentals weekly cron (P0)

**Given**:
- 오늘이 Sunday 18:00 KST
- `get_data_universe()` 가 정상 동작
- `pykrx_adapter.fetch_fundamentals` 가 정상 작동

**When**:
- `data_refresh_fundamentals` cron 트리거 발생
- `refresh_market_data.refresh_fundamentals()` 호출

**Then**:
- `is_trading_day()` 가드 적용 안 됨 (Sunday 도 실행)
- universe 의 모든 ticker 에 대해 `fetch_fundamentals` 호출
- `fundamentals.max(ts)` ≤ today
- 다음 cron 은 다음 주 Sunday 까지 발생 안 함 (weekly)

---

### Scenario 8 — REQ-019-5 KRX 휴장일 보정 (false-positive 방지, P0)

**Given**:
- 오늘이 Monday 09:00 KST
- 어제 Sunday 와 그제 Saturday 가 모두 휴장
- OHLCV 의 max(ts) 가 직전 Friday (= today - 3, 즉 72 시간 전)
- 단순 36h 임계로는 stale 이지만, 휴장일 보정 시 OK

**When**:
- `data_freshness.check_and_alert()` 호출

**Then**:
- expected_ts 계산이 직전 trading day = 지난 Friday 로 결정됨 (Sat/Sun 휴장 보정)
- max(ts) == expected_ts 이므로 stale 아님
- Telegram 알람 송출 **안 됨**
- INFO 로그에 `table=ohlcv latest=... expected=... stale=ok` 출력

---

### Scenario 9 — REQ-019-6 universe registry union (P0)

**Given**:
- DEFAULT_WATCHLIST = `["005930", "000660", "035420", "035720", "373220"]`
- `screened_tickers.json` = `["005380", "009540", "161890"]`
- active holdings = `["035720", "005380"]` (035720, 005380 은 다른 source 와 중복)
- KOSPI200 top-50 = `["005930", "000660", "207940", "005935", ...]` (50개)

**When**:
- `get_data_universe()` 호출

**Then**:
- 반환 리스트가 sorted dedup 형식 (예: `["000660", "005380", "005930", "005935", "009540", "035420", "035720", "161890", "207940", "373220", ...]`)
- 길이 ≤ DEFAULT (5) + screened (3) + holdings (2) + KOSPI200 (50) = 60 (중복 제거 후 실제는 더 작음)
- 길이 ≥ DEFAULT_WATCHLIST 길이 (5) — 최소 보장
- 모든 원소가 6자리 ticker code 형식 (예: `"005930"`)

---

### Scenario 10 — REQ-019-6 (c) DEFAULT_WATCHLIST fallback (P0)

**Given**:
- `screened_tickers.json` 이 파일 부재
- active holdings 조회가 DB 연결 실패로 raise
- KOSPI200 source 가 외부 IO 실패로 raise
- DEFAULT_WATCHLIST 만 가용

**When**:
- `get_data_universe()` 호출

**Then**:
- 반환 리스트가 DEFAULT_WATCHLIST 와 정확히 일치 (`["000660", "005930", "035420", "035720", "373220"]` sorted)
- 빈 리스트 반환 **안 됨** (catastrophic case 방지)
- logger.warning 3건 출력 (각 source 의 실패 기록)

---

### Scenario 11 — Optional REQ-019-7 bootstrap backfill (P1)

**Given**:
- 컨테이너가 최초 부팅
- `ohlcv` 테이블이 빈 상태 (row count == 0)
- `pykrx_adapter.fetch_ohlcv` 정상 작동

**When**:
- 컨테이너 entrypoint 의 bootstrap 가드 실행

**Then**:
- 자동으로 90일 backfill 모드 진입 — `refresh_market_data` 의 backfill 함수 호출
- universe 의 모든 ticker 에 대해 90일치 fetch 완료 후 정상 cron 운영 시작
- Telegram 알람 ("bootstrap backfill started/completed") 송출
- 다음 컨테이너 재시작 시 (테이블 비어있지 않음) bootstrap 트리거되지 않음

---

### Scenario 12 — Optional REQ-019-8 per-ticker timeout (P1)

**Given**:
- universe 에 ticker T 포함
- `pykrx_adapter.fetch_ohlcv("T", ...)` 가 15 초 후 응답 (default timeout 10s 초과)
- 다른 ticker 는 정상

**When**:
- `refresh_ohlcv()` 호출

**Then**:
- ticker T 는 10초 후 타임아웃 → 스킵
- logger.warning 으로 timeout 기록
- 반환 metric dict 에 `timeout_count == 1` 필드 포함
- 남은 ticker 는 정상 처리, batch 중단 안 됨

---

### Scenario 13 — End-to-end live cycle 검증 (Definition of Done 의 라이브 게이트, P0)

**Given**:
- SPEC-019 의 변경이 commit + merged + redeploy 완료
- 5/12 16:00 OHLCV cron + 16:05 flows cron 완료
- 5/12 18:00 disclosures cron + gap backfill 완료
- 5/13 09:00 stale-monitor 알람 송출 안 됨 (모든 테이블 fresh)
- `data/blocked_tickers.json` 에 오늘의 단기과열 종목 포함 (현실 데이터)
- `data/screened_tickers.json` 에 오늘의 06:30 daily_screen 출력 (20 후보)

**When**:
- 5/13 09:30 KST `data_freshness_check` cron 완료 후 09:30 intraday scheduler 잡 실행
- micro persona → decision persona → risk persona 순서 진행

**Then**:
- DB 쿼리:
  ```sql
  SELECT persona_name, jsonb_array_length(response_json->'candidates') AS n_candidates
  FROM persona_runs
  WHERE ts >= CURRENT_DATE + INTERVAL '9 hours'
    AND ts < CURRENT_DATE + INTERVAL '10 hours'
    AND persona_name = 'micro'
  ORDER BY ts DESC LIMIT 1;
  ```
  - `n_candidates ≥ 5`
- DB 쿼리:
  ```sql
  SELECT persona_name, jsonb_array_length(response_json->'signals') AS n_signals
  FROM persona_runs
  WHERE ts >= CURRENT_DATE + INTERVAL '9 hours'
    AND persona_name = 'decision'
  ORDER BY ts DESC LIMIT 1;
  ```
  - `n_signals ≥ 1` (ENTRY / HOLD / WATCH 무관, 캐시 hit 으로 평가됨)
- decision persona 의 rationale 에 "데이터 부재" 또는 "캐시 미스" 류 거부 사유 부재

---

## Quality Gates (TRUST 5)

### Tested

- **Unit tests**: 신규 ~25 (3 신규 파일) + 기존 65 (SPEC-018 baseline) = ~90/90 PASS
- **Coverage**: ≥ 85% (lines). 본 SPEC 의 변경 영역 (`data/universe.py`, `scripts/refresh_market_data.py`, `monitoring/data_freshness.py`) 은 ≥ 90%
- **Characterization**: Scenario 6, 8 이 기존 동작의 characterization test 역할 — 휴장일 가드 / KRX 휴장일 보정의 회귀 방지

### Readable

- 신규 함수에 type hint 보강:
  - `get_data_universe() -> list[str]`
  - `refresh_ohlcv() -> dict[str, int | float]`
  - `check_and_alert(clock: Callable[[], datetime] = datetime.now) -> list[StaleAlert]`
- 각 진입점에 SPEC-019 reference 가 포함된 docstring (예: `"""SPEC-019 REQ-019-1: Daily OHLCV refresh entrypoint. ..."""`)
- 신규 테스트의 각 케이스에 의도를 설명하는 docstring + Given/When/Then 주석

### Unified

- `ruff check .` 0 위반
- `black --check .` 0 위반
- `mypy src/trading/data/ src/trading/scripts/refresh_market_data.py src/trading/monitoring/` (선택, 프로젝트에서 mypy 사용 중이라면) 0 위반

### Secured

- `TELEGRAM_BOT_TOKEN` 은 env 로만 접근 — 코드 / 로그에 출력 금지
- pykrx / DART 호출은 인증 불요, 추가 보안 영향 없음
- per-ticker 실패가 batch 의 다른 ticker 데이터에 영향 주지 않음 (격리)
- `data/screened_tickers.json` / `data/blocked_tickers.json` 의 파일 read 는 exception 처리 — 파일 부재가 batch 를 중단시키지 않음

### Trackable

- 모든 commit message 가 `feat(SPEC-TRADING-019): ...` 또는 `fix(SPEC-TRADING-019): ...` 형식 (conventional commits)
- PR description 이 본 SPEC 의 모든 REQ-019-* 항목 참조
- MX tag 적용:
  - `get_data_universe()` 에 `@MX:ANCHOR` (fan_in ≥ 4)
  - per-ticker try/except 블록에 `@MX:NOTE` (batch isolation 의도 명시)
  - DART gap 자동 감지 분기에 `@MX:NOTE` (first-deploy recovery 의도)
  - `check_and_alert()` 에 `@MX:ANCHOR` (운영 가시성 핵심 진입점)
  - Telegram 송출 지점에 `@MX:WARN` + `@MX:REASON` (외부 IO + secrets)

---

## Verification Methods and Tools

| Verification | Tool / Command | 기대 결과 |
|---|---|---|
| 단위 테스트 통과 | `pytest tests/ -v` | ~90 passed in N seconds |
| Coverage | `pytest --cov=src/trading --cov-report=term-missing` | TOTAL coverage ≥ 85% |
| Linter | `ruff check .` | All checks passed! |
| Formatter | `black --check .` | All done! |
| 컨테이너 healthcheck | `docker compose ps` | scheduler healthy 5/5 |
| Cron 잡 등록 확인 | `docker compose logs scheduler --since 1h \| grep "data_refresh\|data_freshness"` | 5개 잡 등록 로그 출력 |
| Stale 알람 실증 (5/12 09:00) | Telegram 수신 + `docker compose logs scheduler \| grep "data_freshness"` | gap 알람 메시지 수신 + INFO 로그 |
| OHLCV refresh metric (5/12 16:00) | `docker compose logs scheduler --since 1h \| grep "refresh_ohlcv"` | `success_count ≥ 50, error_count ≤ 5` |
| DART gap 복구 (5/12 18:00) | `psql -c "SELECT MAX(rcept_dt) FROM disclosures"` | ≥ today - 1 |
| 라이브 cycle (Scenario 13, 5/13 09:30) | 위 SQL 쿼리 | `n_candidates ≥ 5, n_signals ≥ 1` |
| False-positive 검증 (5/14 09:00) | Telegram 수신 부재 + INFO 로그 `stale=ok` | 알람 송출 안 됨 |

---

## Out of Scope (Verification 대상 아님)

다음은 본 SPEC 의 acceptance 에서 검증하지 않는다:

- decision persona 의 ENTRY 신호 생성 여부 (HOLD/WATCH 도 데이터 캐시 hit + universe 가 살아 있음을 증명하면 충분)
- 라이브 거래 P&L 결과 (paper trading 환경, 본 SPEC 은 인프라 fix)
- pykrx / DART 의 데이터 정확성 (외부 API 신뢰)
- KIS Open API 의 실시간 가격 정확성
- SPEC-016 Phase 2 (regime DB) 의 통합 동작
- 새로운 데이터 소스 (Bloomberg, Refinitiv) 도입 검증
- frontend / dashboard 검증
- 정교한 rate-limit retry / back-off 알고리즘 (REQ-019-8 의 단순 timeout 까지만)
- cross-exchange 데이터 (US stocks, futures, FX) 검증
- macro_context / news pipeline 의 데이터 출처 확장 (SPEC-016 Phase 2 영역)
