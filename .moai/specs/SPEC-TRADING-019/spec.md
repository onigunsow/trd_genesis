---
id: SPEC-TRADING-019
version: 0.1.0
status: draft
created: 2026-05-11
updated: 2026-05-11
author: onigunsow
priority: critical
issue_number: 0
domain: TRADING
title: "Market data automated refresh layer + stale monitoring"
related_specs:
  - SPEC-TRADING-018
  - SPEC-TRADING-016
  - SPEC-TRADING-013
  - SPEC-TRADING-009
---

# SPEC-TRADING-019 -- Market data automated refresh layer + stale monitoring

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-11 | 0.1.0 | Initial draft -- 6 EARS requirements, SPEC-018 라이브 검증 중 발견된 data infrastructure 결함 핫픽스 | onigunsow |

---

## Scope Summary

본 SPEC 은 **SPEC-018 (`feat/spec-018-blocked-tickers`)** 의 라이브 검증일(2026-05-11 14:41 KST)에 발견된 **decision-stage zero-trade 결함**을 해결한다. SPEC-018 의 micro persona 패치는 정상 작동했으나 (DEFAULT_WATCHLIST blocked → screened_tickers 로 universe fallback 성공), 직후 단계인 decision persona 가 4종의 valid 후보 (`005380` 현대차, `009540` HD한국조선해양, `161890` 한국콜마, `034730` SK이노베이션) 를 모두 거부하면서 `decisions: []` 를 반환했다.

근본 원인은 SPEC-016 / SPEC-018 의 persona 레이어와 **완전히 독립된 data infrastructure 결함**으로, 별도 hotfix SPEC 으로 분리한다.

### 근본 원인 (Verified Evidence)

`src/trading/scheduler/runner.py` 가 등록하는 14개의 cron 잡 중 **시장 데이터를 fetch 하는 잡이 0건**이다. 시스템은 약 10일 전 (~2026-05-01) 의 수동 시드 스냅샷에 의존하여 운영되어 왔고, daily_screen 이 산출하는 fresh 후보는 거의 모두 캐시 미스 상태이다.

**파일 레벨 증거**:

- `src/trading/scheduler/runner.py:32~196` — 등록된 cron 잡 전수 확인 (news_crawl, news_export, news_import, build_macro_context, build_micro_context, build_micro_news, build_macro_news, daily_screen, blocked_tickers_cache, pre_market, intraday×4, daily_report, weekly_macro, retrospective). **이 중 어느 잡도 `pykrx_adapter`, `fred_adapter`, `ecos_adapter`, `dart_adapter`, `yfinance_adapter` 를 호출하지 않는다.**
- `src/trading/scripts/fetch_data.py` — 수동 CLI 진입점만 존재 (`trading fetch-data --source pykrx --symbol 005930` 등). 모든 어댑터는 함수 단위로 정상 작동하지만, **스케줄러에서 호출되는 지점이 없다**.
- `src/trading/data/cache.py` 의 `upsert_ohlcv` — `(source, symbol, ts)` PK 로 idempotent upsert 작동. 데이터 입력만 되면 정상 캐싱.

### DB 감사 (2026-05-11 14:48 KST snapshot)

| Table | Latest data | Symbol count | Days stale |
|---|---|---|---|
| ohlcv | 2026-05-02 | 11 only | 9 |
| fundamentals | 2026-05-04 | 5 only (DEFAULT_WATCHLIST) | 7 |
| flows | 2026-05-04 | 5 only (DEFAULT_WATCHLIST) | 7 |
| disclosures | 2026-04-30 | 1183 | 11 |

### 왜 10일간 발견되지 않았는가

- **SPEC-016 Phase 1** 의 `DEFAULT_WATCHLIST = ["005930","000660","035420","035720","373220"]` 하드코딩이 micro persona 를 5종에 잠갔고, **그 5종은 수동 시드 데이터 (1800 rows each) 가 풍부**했다. data infrastructure 결함이 micro 레벨에서 가려졌다.
- **SPEC-018** 이 screened_tickers 로 universe 를 풀자, **screened 출신의 fresh 후보가 캐시 미스 상태였기에 decision persona 가 일관되게 거부**했다. 비로소 결함이 표면에 드러났다.
- **stale 알람이 부재**했기에 운영자가 데이터 cutover (~5/2) 시점에 인지하지 못했다.

### Universe gap 상세

- `daily_screen.run` (06:30 KST mon-fri) 이 산출하는 `data/screened_tickers.json` 은 매일 ~20개의 fresh 섹터 후보를 담는다.
- Micro persona 는 KOSPI200/KOSDAQ150 범위에서 폭넓게 후보를 고를 수 있다 (screened 리스트에 국한되지 않음).
- 오늘(2026-05-11) micro 가 선택한 4종 (005380, 009540, 161890, 034730) 은 **screened_tickers.json 에도 없었고 cache 에도 없었다**. decision persona 는 OHLCV/fundamentals/flows 가 없는 종목을 "근거 없음" 으로 거부.

### 본 SPEC 의 위치

- **SPEC-016 Phase 1 (완료, 2026-05-10 21:38 redeploy)**: 인프라/CLI/Jinja 정합성 안정화 — persona 파이프라인 작동
- **SPEC-016 Phase 2 (미시작)**: regime/risk_appetite DB 캐싱 — 본 SPEC 과 직교
- **SPEC-016 Phase 3 (미시작)**: 불장 모드 + 후기 사이클 방어
- **SPEC-017 (미시작)**: 실거래 전환 토글
- **SPEC-018 (이슈 발견, 2026-05-11 14:41 검증)**: micro persona 의 blocked-ticker 인식 + dynamic watchlist — persona 단계 fix 완료
- **SPEC-019 (본 SPEC, P0 Critical)**: 시장 데이터 자동 refresh layer + stale 모니터링 — data 단계 fix, SPEC-018 의 의도된 효과를 실현하기 위한 보완 SPEC

본 SPEC 은 SPEC-016 / SPEC-018 의 persona 변경과 **완전히 직교**한다: persona 코드 변경 없이도 즉시 적용 가능하다. 다만 본 SPEC 이 완료되어야 SPEC-018 의 fresh-universe 효과가 실제 거래로 이어진다.

### 비즈니스 임팩트

- 5/2 ~ 5/11 의 10거래일에 걸쳐 모든 intraday/pre_market cycle 이 캐시-miss-후보 거부로 zero-trade 처리되었음 (paper trading 이므로 실제 손실 0)
- SPEC-016 Phase 1 + SPEC-018 의 모든 게이트가 통과되었음에도 거래가 발생하지 않는 상태는 사용자 의도와 명확히 어긋남
- 본 SPEC 완료 후에는 매일 16:00 KST 자동 OHLCV refresh + stale 알람으로 동일 사고의 영구 재발 방지

---

## Environment

- 기존 SPEC-001 ~ SPEC-018 인프라 (Docker compose, Postgres 16-alpine, Telegram, KIS API)
- 기존 5-persona 시스템 (Macro/Micro/Decision/Risk/Portfolio)
- SPEC-016 Phase 1 의 인프라 패치 + SPEC-018 의 persona 패치가 모두 배포 완료된 상태
- 기존 어댑터 (모두 정상 작동, 호출 지점만 미연결):
  - `src/trading/data/pykrx_adapter.py` — KOSPI/KOSDAQ OHLCV, fundamentals, foreign/inst/individual flows
  - `src/trading/data/dart_adapter.py` — DART 공시 (365일 운영)
  - `src/trading/data/fred_adapter.py` — 미국 거시 시리즈 (현재 SPEC 범위 외)
  - `src/trading/data/ecos_adapter.py` — 한은 ECOS 시리즈 (현재 SPEC 범위 외)
  - `src/trading/data/yfinance_adapter.py` — 해외 지수 (현재 SPEC 범위 외)
- 기존 cache layer: `src/trading/data/cache.py` 의 `upsert_ohlcv`, `upsert_fundamentals`, `upsert_flows` — 모두 `(source, symbol, ts)` PK 로 idempotent
- 기존 운영 데이터 파일:
  - `data/screened_tickers.json` — 06:30 daily_screen 출력 (20 fresh 후보)
  - `data/blocked_tickers.json` — exchange feed (단기과열)
- 기존 KRX 휴장일 캘린더 헬퍼: `src/trading/scheduler/calendar.py` 의 `is_trading_day`, `reason_if_closed`
- 기존 Telegram 알람 인프라: `TELEGRAM_BOT_TOKEN` env (`.env`), 기존 `notify` 모듈 (정확한 진입점은 manager-tdd 가 확인)
- 신규 코드: `src/trading/data/universe.py` (universe 레지스트리), `src/trading/scripts/refresh_market_data.py` (cron 진입점), `src/trading/monitoring/data_freshness.py` (stale 체크 + 알람)
- 신규 테스트: `tests/data/test_universe.py`, `tests/scheduler/test_data_refresh_jobs.py`, `tests/monitoring/test_data_freshness.py`

## Assumptions

- A-1: `pykrx_adapter.fetch_ohlcv(symbol, start, end)` 의 시그니처는 manual CLI 에서 검증된 형태로 유지된다. 동일 함수가 cron 진입점에서 호출 가능하다.
- A-2: `pykrx_adapter.fetch_fundamentals` 및 `pykrx_adapter.fetch_flows` 가 동일하게 idempotent upsert 패턴을 따른다.
- A-3: `dart_adapter.list_recent(start, end)` 의 시그니처와 idempotency 가 manual CLI 에서 검증된 형태로 유지된다.
- A-4: `data/screened_tickers.json` 은 06:30 daily_screen 잡이 매일 ≥ 1건의 후보를 산출한다 (SPEC-018 의 A-2 와 동일 가정).
- A-5: KIS API 또는 pykrx 의 일일 호출 한도 (rate limit) 는 본 SPEC 의 universe 크기 (≤ 100 ticker × 1 day per cron run) 에 충분한 여유가 있다. 실제 한도 초과 시 retry/back-off 는 본 SPEC 의 implementation hint 로 위임.
- A-6: 활성 holdings 의 정식 조회 지점이 존재한다 (예: `src/trading/portfolio/state.py` 또는 DB `positions` 테이블). 정확한 진입점은 manager-tdd 가 확인.
- A-7: KOSPI200 top-50 종목 리스트는 정적 yaml 또는 pykrx 의 sector master 로부터 일일 조회 가능하다. 조회 비용은 무시 가능.
- A-8: KRX 휴장일 (`is_trading_day`) 의 데이터 fetch 시도는 비효율적이나 비파괴적이다 (이미 캐시된 행을 다시 upsert 해도 무해). 단, REQ-019-1 ~ REQ-019-3 의 cron 은 명시적으로 `mon-fri` 트리거 + `is_trading_day` 가드를 둔다.
- A-9: DART 는 365일 운영되므로 REQ-019-4 의 disclosures cron 은 매일 18:00 KST 무조건 실행 (KRX 휴장일 무관).
- A-10: 본 SPEC 의 변경은 SPEC-016 Phase 2 (regime DB 캐싱) 와 데이터 파이프라인이 직교한다. Phase 2 의 `macro_state_cache` 또는 `system_state` 컬럼 추가와 충돌하지 않는다.

---

## Goals

- **G-1 (Auto-refresh)**: OHLCV / fundamentals / flows / disclosures 의 4개 데이터 테이블이 **운영자 개입 없이** 일일 자동 갱신되도록 cron 인프라 구축.
- **G-2 (Stale visibility)**: 데이터가 비정상 stale 상태가 되었을 때 **30초 이내 Telegram 알람** 으로 운영자가 인지하도록 모니터링 추가.
- **G-3 (Single source of truth)**: 후보 universe (DEFAULT_WATCHLIST ∪ screened ∪ holdings ∪ KOSPI200 top-50) 의 정의를 `universe.py` 단일 모듈로 통합하여, fetch 정책과 watchlist 정책을 분리.
- **G-4 (Resilience)**: 개별 ticker 실패가 batch 전체를 중단시키지 않는다 (per-ticker try/except + error count 로그).
- **G-5 (Backward compatibility)**: 본 SPEC 의 변경은 기존 persona / scheduler 동작에 회귀를 일으키지 않는다. SPEC-016 / SPEC-018 의 모든 게이트 유지.
- **G-6 (Reproducibility)**: 본 SPEC 완료 후, 오늘의 시나리오 (10일 stale + screened 캐시 미스 → decision 거부) 를 단위 테스트로 영구 재현 가능.

---

## Requirements

### REQ-019-1: Daily OHLCV refresh cron (Event-Driven, P0)

**When** KST 16:00 KRX 폐장 후 (mon-fri, 거래일 한정), **then** 시스템은 `get_data_universe()` 가 반환하는 universe 의 모든 ticker 에 대해 OHLCV 데이터를 fetch 하여 idempotent upsert 해야 한다.

세부:

- (a) **(Event-Driven)** **When** APScheduler 가 16:00 KST trigger 를 발생시키고 `is_trading_day()` 가 True 를 반환하면, 시스템은 `refresh_market_data.refresh_ohlcv()` 진입점을 호출한다.
- (b) **(Ubiquitous)** universe 는 `get_data_universe()` (REQ-019-6) 의 union: DEFAULT_WATCHLIST ∪ screened_tickers ∪ active holdings ∪ KOSPI200 top-50.
- (c) **(Ubiquitous)** 각 ticker 에 대해 `pykrx_adapter.fetch_ohlcv(symbol, start, end)` 를 호출 — start 는 ticker 별 기존 max(ts) + 1 day 또는 (캐시 미스 시) today - 90 days, end 는 today.
- (d) **(Event-Driven)** **When** 개별 ticker fetch 가 예외를 raise 하면, 시스템은 해당 ticker 만 스킵하고 logger.warning 으로 기록한 뒤 다음 ticker 로 진행한다. **전체 batch 를 중단해서는 안 된다.**
- (e) **(Ubiquitous)** 잡 종료 시 다음 metric 을 INFO 레벨로 로그: `total_tickers`, `success_count`, `error_count`, `total_rows_upserted`, `duration_seconds`.
- (f) **(Unwanted)** 시스템은 KRX 휴장일 (Saturday/Sunday/공휴일) 에 cron 을 실행해서는 **안 된다** — APScheduler `day_of_week="mon-fri"` + `_wrap` helper 의 `is_trading_day()` 가드 적용.
- (g) **(Ubiquitous)** upsert 는 `cache.upsert_ohlcv` 의 기존 `(source, symbol, ts)` PK 로 idempotent — 재실행 시 중복 행 생성 금지.

**Files affected**:

- `src/trading/scheduler/runner.py` — 16:00 cron 잡 추가
- `src/trading/scripts/refresh_market_data.py` (신규) — `refresh_ohlcv()` 진입점
- `src/trading/data/universe.py` (신규) — `get_data_universe()` (REQ-019-6 에서 정의)

**Dependencies**: REQ-019-6 (universe 레지스트리 선행). REQ-019-2, REQ-019-3 와 동일 universe 함수를 공유.

---

### REQ-019-2: Daily flows refresh cron (Event-Driven, P0)

**When** KST 16:05 KRX 폐장 후 (mon-fri, 거래일 한정), **then** 시스템은 동일 universe 의 모든 ticker 에 대해 외국인/기관/개인 순매수 데이터를 fetch 하여 upsert 해야 한다.

세부:

- (a) **(Event-Driven)** **When** APScheduler 가 16:05 KST trigger 를 발생시키고 `is_trading_day()` 가 True 이면, 시스템은 `refresh_market_data.refresh_flows()` 를 호출한다.
- (b) **(Ubiquitous)** universe 는 REQ-019-1 과 동일 (`get_data_universe()`).
- (c) **(Ubiquitous)** 각 ticker 에 대해 `pykrx_adapter.fetch_flows(symbol, start, end)` 호출.
- (d) **(Event-Driven)** **When** 개별 ticker 실패 시 REQ-019-1 (d) 와 동일한 per-ticker 격리 패턴 적용.
- (e) **(Ubiquitous)** REQ-019-1 (e) 와 동일한 metric 로그 출력.
- (f) **(Unwanted)** REQ-019-1 (f) 와 동일한 휴장일 가드.

**Files affected**:

- `src/trading/scheduler/runner.py` — 16:05 cron 잡 추가
- `src/trading/scripts/refresh_market_data.py` — `refresh_flows()` 진입점

**Dependencies**: REQ-019-6. REQ-019-1 이후 5분 간격으로 배치되어 rate-limit 충돌 회피.

---

### REQ-019-3: Weekly fundamentals refresh cron (State-Driven, P0)

**While** 주말 (Sunday) 18:00 KST 일 때, **the system shall** 동일 universe 의 모든 ticker 에 대해 fundamentals (PER/PBR/EPS/BPS/dividend) 데이터를 fetch 하여 upsert 해야 한다.

세부:

- (a) **(State-Driven)** Sunday 18:00 KST 단 1회 실행 (fundamentals 는 slow-moving 이므로 weekly 로 충분).
- (b) **(Ubiquitous)** universe 는 REQ-019-1 과 동일.
- (c) **(Ubiquitous)** 각 ticker 에 대해 `pykrx_adapter.fetch_fundamentals(symbol, start, end)` 호출.
- (d) **(Event-Driven)** REQ-019-1 (d) 와 동일한 per-ticker 격리.
- (e) **(Ubiquitous)** REQ-019-1 (e) 와 동일한 metric 로그.
- (f) **(Ubiquitous)** Sunday 실행이므로 `is_trading_day()` 가드는 적용하지 않는다 (fundamentals 는 시장 운영과 독립).

**Files affected**:

- `src/trading/scheduler/runner.py` — Sunday 18:00 cron 잡 추가
- `src/trading/scripts/refresh_market_data.py` — `refresh_fundamentals()` 진입점

**Dependencies**: REQ-019-6.

---

### REQ-019-4: Daily DART disclosure refresh cron + gap backfill (Event-Driven, P0)

**When** KST 18:00 (매일, 365일), **then** 시스템은 DART 공시를 fetch 하여 upsert 해야 한다. 또한 배포 직후 첫 실행에는 `--recent 12` 모드로 5/2 ~ 5/11 의 누적 gap 을 복구해야 한다.

세부:

- (a) **(Event-Driven)** **When** APScheduler 가 18:00 KST trigger 를 발생시키면 (요일 무관, DART 는 365일 운영), 시스템은 `refresh_market_data.refresh_disclosures()` 를 호출한다.
- (b) **(Ubiquitous)** 기본 호출: `dart_adapter.list_recent(today - 1, today)` (1일치 갱신).
- (c) **(State-Driven)** **While** `disclosures.max(rcept_dt)` 가 today - 2 이전이면 (캐시 gap 감지), 시스템은 자동으로 `--recent 12` 동등 호출 (`today - 12 ~ today`) 로 전환하여 누적 gap 을 복구한다.
- (d) **(Ubiquitous)** 배포 직후 첫 실행 시 (c) 의 조건이 자동 활성화되어 5/2 ~ 5/11 gap 을 복구한다 (별도 수동 명령 불필요).
- (e) **(Unwanted)** 시스템은 KRX 휴장일에도 본 cron 을 실행해야 한다 (A-9). `is_trading_day()` 가드를 적용해서는 **안 된다**.
- (f) **(Ubiquitous)** REQ-019-1 (e) 와 동일한 metric 로그.

**Files affected**:

- `src/trading/scheduler/runner.py` — 매일 18:00 cron 잡 추가
- `src/trading/scripts/refresh_market_data.py` — `refresh_disclosures()` 진입점 + gap 자동 감지

**Dependencies**: 없음 (독립). universe 함수와 무관 (DART 는 전체 공시 풀에서 fetch).

---

### REQ-019-5: Stale data monitoring cron + Telegram alert (Event-Driven + State-Driven, P0)

**When** KST 09:00 (mon-fri), **then** 시스템은 4개 데이터 테이블의 신선도를 점검하고 36시간 (KRX 휴장일 보정 후) 초과 stale 시 Telegram 알람을 송출해야 한다.

세부:

- (a) **(Event-Driven)** **When** APScheduler 가 09:00 KST trigger 를 발생시키고 `is_trading_day()` 가 True 이면, 시스템은 `data_freshness.check_and_alert()` 를 호출한다.
- (b) **(Ubiquitous)** 점검 대상 테이블: `ohlcv`, `fundamentals`, `flows`, `disclosures` 4개.
- (c) **(Ubiquitous)** 각 테이블에 대해 `SELECT MAX(ts) FROM {table}` 조회하여 latest_ts 확보.
- (d) **(Ubiquitous)** 기대 ts (`expected_ts`) 계산: 직전 trading day 의 종료 시각. KRX 휴장일 (Sat/Sun/공휴일) 은 `calendar.py` 의 헬퍼로 보정. fundamentals 는 직전 Sunday + 1d, disclosures 는 today - 1.
- (e) **(State-Driven)** **While** `now() - latest_ts > 36h` (KRX 휴장일 보정 후) 이면, Telegram 알람을 30초 이내 송출.
- (f) **(Ubiquitous)** 알람 메시지는 다음 필드를 반드시 포함:
  - table name (예: `ohlcv`)
  - latest_ts (예: `2026-05-02`)
  - expected_ts (예: `2026-05-09`)
  - days stale (예: `7 days stale`)
- (g) **(Ubiquitous)** 알람은 기존 Telegram 인프라 사용 (`TELEGRAM_BOT_TOKEN` env). 정확한 진입점은 manager-tdd 가 확인 (예: `trading.notify.telegram.send_message` 또는 직접 API 호출).
- (h) **(Unwanted)** 36시간 임계 미만일 때는 알람을 송출해서는 **안 된다** (false-positive 방지).
- (i) **(Ubiquitous)** stale 점검 결과는 모든 테이블에 대해 INFO 로그로 출력 (`table=ohlcv latest=... expected=... stale=ok|warn`), 알람 송출 여부와 무관.

**Files affected**:

- `src/trading/scheduler/runner.py` — 09:00 cron 잡 추가
- `src/trading/monitoring/data_freshness.py` (신규) — `check_and_alert()` 진입점

**Dependencies**: REQ-019-1 ~ REQ-019-4 (refresh cron 의 작동 검증을 위한 monitoring 도구). monitoring 자체는 refresh 와 독립적으로 작동 가능.

---

### REQ-019-6: Universe registry as single source of truth (Ubiquitous, P0)

시스템은 **fetch universe 정책의 단일 정의 지점** 으로 `src/trading/data/universe.py` 모듈을 보유해야 한다.

세부:

- (a) **(Ubiquitous)** `get_data_universe() -> list[str]` 함수가 다음 4 set 의 union 을 반환:
  - DEFAULT_WATCHLIST (SPEC-016 Phase 1 의 5종)
  - `data/screened_tickers.json` 의 screened (당일 daily_screen 출력)
  - active holdings (현 포지션 종목, 진입점은 manager-tdd 가 확인)
  - KOSPI200 top-50 (정적 yaml 또는 pykrx sector master 출처)
- (b) **(Ubiquitous)** 반환 list 는 ticker code 의 정렬된 deduplicated 리스트 (`sorted(set(...))`).
- (c) **(Unwanted)** universe 의 ticker 수가 0건이면 함수는 **빈 리스트가 아닌 DEFAULT_WATCHLIST 만이라도 반환** 해야 한다 (모든 fetch 정책이 무력화되는 catastrophic case 방지).
- (d) **(Ubiquitous)** 함수는 외부 IO 가 부분 실패해도 (예: screened_tickers.json 부재) 다른 source 의 결과를 그대로 union 하여 반환. 각 source 의 실패는 logger.warning 으로 기록.
- (e) **(Ubiquitous)** 함수는 watchlist 정책 (micro persona 가 어떤 universe 에서 후보를 고르는가) 과 **분리** 된다. 본 SPEC 은 fetch 정책만 정의.
- (f) **(Ubiquitous)** 함수는 KOSPI200 top-50 inclusion 의 출처를 `.moai/config/` 의 yaml 또는 `src/trading/data/kospi200.py` 모듈로 둔다 (정확한 위치는 manager-tdd 가 결정).

**Files affected**:

- `src/trading/data/universe.py` (신규)
- `tests/data/test_universe.py` (신규)
- `.moai/config/sections/data.yaml` 또는 `src/trading/data/kospi200.py` (KOSPI200 top-50 출처, manager-tdd 결정)

**Dependencies**: REQ-019-1, REQ-019-2, REQ-019-3 의 호출 지점.

---

### REQ-019-7 (P0, escalated from P1 per user decision 2026-05-11): Bootstrap backfill on container start

**While** 시스템이 부팅되고 OHLCV/fundamentals/flows/disclosures 중 어느 테이블이 row 수 0 이면, **the system shall** 90일치 자동 backfill 을 수행한 후 정상 cron 운영을 시작한다.

세부:

- (a) **(State-Driven)** 컨테이너 entrypoint 가 row count 점검 후 빈 테이블이 감지되면 `refresh_market_data` 의 backfill 모드를 실행.
- (b) **(Ubiquitous)** backfill 범위: today - 90 days ~ today.
- (c) **(Ubiquitous)** backfill 진행 상황을 Telegram 으로 알림 (시작/완료/실패).
- (d) **(Unwanted)** 정상 운영 중인 컨테이너의 매 재시작마다 backfill 을 실행해서는 안 된다 — row count 0 일 때만 트리거.

**Files affected**:

- `src/trading/scripts/refresh_market_data.py` — bootstrap 분기 추가
- 컨테이너 entrypoint (docker-compose 또는 Dockerfile 의 CMD)

**Dependencies**: REQ-019-1 ~ REQ-019-4 위에 얹는 보강 로직. **P0 격상 사유**: R-7 (cold start false-positive) 대응 + 사용자 결정 (2026-05-11): bootstrap 미적용 시 첫 24h 동안 미적용 ticker가 발생할 수 있어 운영 신뢰성 보장이 필요.

---

### REQ-019-8 (Optional, P1): Per-ticker fetch latency budget

**While** 개별 ticker fetch 가 진행되는 동안, **the system shall** N 초 (default 10s) 타임아웃 후 다음 ticker 로 넘어가야 한다.

세부:

- (a) **(State-Driven)** 각 `pykrx_adapter.fetch_*` 호출에 N 초 타임아웃 적용 (default 10s, `.moai/config/sections/data.yaml` 로 설정 가능).
- (b) **(Event-Driven)** **When** 타임아웃 발생 시 해당 ticker 만 스킵하고 logger.warning 으로 기록.
- (c) **(Ubiquitous)** REQ-019-1 (e) 의 metric 에 `timeout_count` 필드 추가.

**Files affected**:

- `src/trading/scripts/refresh_market_data.py` — 타임아웃 wrapper 추가

**Dependencies**: REQ-019-1 ~ REQ-019-3 위에 얹는 안전망. 본 REQ 는 P1 (선택).

---

## Specifications

### S-1: Cron 스케줄 정의 (신규 5개 잡)

| 잡 ID | Cron | 진입점 | 가드 |
|---|---|---|---|
| `data_refresh_ohlcv` | mon-fri 16:00 | `refresh_market_data.refresh_ohlcv()` | `is_trading_day()` |
| `data_refresh_flows` | mon-fri 16:05 | `refresh_market_data.refresh_flows()` | `is_trading_day()` |
| `data_refresh_fundamentals` | sun 18:00 | `refresh_market_data.refresh_fundamentals()` | none (fundamentals 은 시장 무관) |
| `data_refresh_disclosures` | * 18:00 | `refresh_market_data.refresh_disclosures()` | none (DART 365일 운영) |
| `data_freshness_check` | mon-fri 09:00 | `data_freshness.check_and_alert()` | `is_trading_day()` |

기존 14개 cron 잡 위에 5개 추가 = 총 19개 (모두 한 `runner.py` 파일 내).

### S-2: `get_data_universe()` 의 반환 형식

```python
def get_data_universe() -> list[str]:
    """SPEC-019: Return union of DEFAULT_WATCHLIST ∪ screened ∪ holdings ∪ KOSPI200_top50.

    Returns sorted, deduplicated list of ticker codes (e.g. ["000660", "005380", ...]).

    Failure modes:
        - screened_tickers.json missing → log warning, skip
        - holdings query fails → log warning, skip
        - KOSPI200 source missing → log warning, skip
        - All sources fail → return DEFAULT_WATCHLIST (never empty list)
    """
```

### S-3: refresh_ohlcv() 의 진입점 형식 (의사 코드 수준)

```python
def refresh_ohlcv() -> dict:
    """SPEC-019: Daily OHLCV refresh entrypoint.

    Returns metric dict: {total_tickers, success_count, error_count, total_rows_upserted, duration_seconds}.
    """
    metrics = {"success_count": 0, "error_count": 0, "total_rows_upserted": 0}
    universe = get_data_universe()
    metrics["total_tickers"] = len(universe)
    start_time = time.monotonic()

    for ticker in universe:
        try:
            # 캐시 미스 시 90일치 백필, 그 외에는 incremental
            last_ts = cache.get_latest_ohlcv_ts(ticker)
            start = (last_ts + timedelta(days=1)) if last_ts else (date.today() - timedelta(days=90))
            n = pykrx_adapter.fetch_ohlcv(ticker, start, date.today())
            metrics["success_count"] += 1
            metrics["total_rows_upserted"] += n
        except Exception as e:
            LOG.warning("OHLCV fetch failed for %s: %s", ticker, e)
            metrics["error_count"] += 1

    metrics["duration_seconds"] = time.monotonic() - start_time
    LOG.info("refresh_ohlcv: %s", metrics)
    return metrics
```

### S-4: Stale check 의 임계 보정 로직

| Table | Expected ts | Stale 임계 |
|---|---|---|
| ohlcv | 직전 trading day | now() - 36h (휴장일 보정) |
| fundamentals | 직전 Sunday + 1d | now() - 8d (weekly + 1d 여유) |
| flows | 직전 trading day | now() - 36h (휴장일 보정) |
| disclosures | today - 1d (DART 365일) | now() - 36h (요일 무관) |

### S-5: Telegram 알람 메시지 형식

```
🚨 [SPEC-019] STALE DATA DETECTED

table: ohlcv
latest: 2026-05-02
expected: 2026-05-09
stale: 7 days

table: disclosures
latest: 2026-04-30
expected: 2026-05-10
stale: 11 days

→ check container logs / re-run /trading/scripts/refresh_market_data.py
```

### S-6: Acceptance Criteria (Given/When/Then)

본 SPEC 의 6개 acceptance 시나리오는 `acceptance.md` 에 상세 정의. spec.md 에서는 다음 5개를 정식 acceptance criteria 로 명시 (REQ-019-1 ~ REQ-019-5 매핑):

**시나리오 1 — REQ-019-1 OHLCV refresh 정상 동작**:
- **Given** scheduler 가 mon-fri 16:00 KST 에 도달 AND `is_trading_day()` 가 True AND `DEFAULT_WATCHLIST` 가 비어있지 않음,
- **When** daily OHLCV cron trigger 발생,
- **Then** `ohlcv.max(ts)` ≤ 1 trading day from today AND row count 가 약 N tickers × 1 day 증가.

**시나리오 2 — REQ-019-5 stale 감지 + 알람**:
- **Given** OHLCV 의 ticker X 가 48 시간 stale,
- **When** 09:00 stale-monitor cron 실행,
- **Then** 30 초 이내 Telegram 알람 송출 AND 알람 메시지에 ticker X 의 stale 정보 포함.

**시나리오 3 — REQ-019-1 (b) + REQ-019-6 screened ticker backfill**:
- **Given** 신규 ticker Y 가 06:30 daily_screen 으로 `screened_tickers.json` 에 추가됨 (이전 캐시 없음),
- **When** 다음 16:00 OHLCV cron 실행,
- **Then** `ohlcv` 에 ticker Y 의 row 가 ≥ 90 개 (캐시 미스 → 90일 backfill).

**시나리오 4 — REQ-019-4 DART gap backfill**:
- **Given** `disclosures.max(rcept_dt)` 가 11일 stale (오늘의 발견),
- **When** 배포 후 첫 disclosure cron 실행,
- **Then** `--recent 12` 자동 활성화 AND `disclosures.max(rcept_dt) ≥ today - 1`.

**시나리오 5 — REQ-019-1 (d) per-ticker 실패 격리**:
- **Given** universe 중 ticker Z 의 fetch 가 pykrx network error 를 raise,
- **When** 16:00 OHLCV cron 진행 중,
- **Then** 남은 ticker 가 모두 처리됨 AND `metrics["error_count"]` ≥ 1 가 로그에 기록.

---

## Non-Goals (Out of Scope)

본 SPEC 은 다음 항목을 **명시적으로 다루지 않는다**:

- **신규 persona 타입 또는 LLM 로직 변경** — SPEC-016 Phase 2/3 영역
- **실거래 전환 토글** — SPEC-017 영역
- **frontend / dashboard for data freshness** — 본 SPEC 은 CLI / cron / Telegram 알람 전용
- **pykrx 에서 유료 데이터 소스 (Bloomberg, Refinitiv 등) 로 마이그레이션** — 향후 별도 SPEC
- **cross-exchange (US stocks, futures, FX 등) 데이터 통합** — KRX 한정
- **SPEC-016 또는 SPEC-018 의 persona 코드 변경** — 본 SPEC 은 data infrastructure 만 다룬다
- **macro_context / micro_context / news pipeline 의 데이터 출처 확장** — SPEC-016 Phase 2 의 REQ-016-2-4 영역
- **rate-limit 회피의 정교한 back-off / retry 알고리즘** — 본 SPEC 은 per-ticker 격리 + 단순 skip 만 다룬다. 정교한 retry 는 follow-up
- **active holdings 의 정의 변경** — 진입점은 manager-tdd 가 기존 코드로부터 발견 (현 포지션 종목 조회)

---

## Implementation Hints (manager-tdd 참고용, 본 SPEC 에서는 구현하지 않음)

본 SPEC 은 specification 만 정의하며, 실 코드 작성은 `/moai:2-run SPEC-TRADING-019` 단계의 manager-tdd 에 위임한다. 다음은 manager-tdd 에 전달할 힌트이다:

- **Telegram 알람 진입점**: 기존 `trading.notify.telegram.send_message` 가 존재하면 재사용. 미존재 시 `requests.post` 로 `https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage` 직접 호출. chat_id 는 `.env` 의 `TELEGRAM_CHAT_ID` 사용.
- **pykrx 어댑터**: 기존에 backfill 범위를 받는 형태로 작동 — `pykrx_adapter.fetch_ohlcv(symbol, start, end)`. 호출 시그니처 변경 불필요.
- **KRX 휴장일 캘린더**: `src/trading/scheduler/calendar.py` 의 `is_trading_day()`, `reason_if_closed()` 활용. REQ-019-5 의 expected_ts 계산에는 직전 trading day 를 찾는 헬퍼가 필요할 수 있음 (없으면 신규 추가).
- **테스트 가능성**: REQ-019-5 의 `check_and_alert()` 는 `datetime.now()` 를 직접 사용하지 않고 `clock` 파라미터를 주입받는 형태로 작성 (단위 테스트에서 시간 mock).
- **Idempotency**: `cache.upsert_ohlcv` 의 기존 `(source, symbol, ts)` PK 가 이미 idempotent. cron 재실행 시 중복 행 생성 없음 — manager-tdd 가 추가 unique constraint 부여 불필요.
- **Active holdings 조회**: `src/trading/portfolio/` 또는 `src/trading/db/` 디렉터리를 grep 하여 현 포지션 조회 함수를 발견 (예: `get_active_positions()` 또는 `positions` 테이블 쿼리). 정확한 진입점은 코드 탐색 후 결정.
- **KOSPI200 top-50 출처**: pykrx 의 `stock.get_index_portfolio_deposit_file("1028")` 류 함수가 KOSPI200 종목 조회 가능. 또는 정적 yaml 로 매일 한 번 갱신. manager-tdd 가 비용/정확성 trade-off 평가 후 결정.
- **테스트 fixture**: REQ-019-1 ~ REQ-019-4 의 cron 진입점은 pytest 의 monkeypatch 로 어댑터를 stub. 실제 외부 API 호출 없이 universe / metric 검증.
- **회귀 영향**: 본 SPEC 은 기존 cron 잡을 변경하지 않고 추가만 한다. SPEC-016 / SPEC-018 의 모든 게이트 유지. 다만 `runner.py` 의 `main()` 함수 line count 증가에 따라 `len(jobs) == 14` 류 assert 가 기존 테스트에 있다면 갱신 필요.

---

## Files Expected to Change (구현 단계 참고)

| File | Change Type | Rough LOC | Owner |
|---|---|---|---|
| `src/trading/scheduler/runner.py` | Modify (add 5 cron jobs) | +30 ~ +50 | manager-tdd |
| `src/trading/data/universe.py` | New file (universe registry) | +60 ~ +100 | manager-tdd |
| `src/trading/scripts/refresh_market_data.py` | New file (cron entrypoint) | +150 ~ +250 | manager-tdd |
| `src/trading/monitoring/data_freshness.py` | New file (stale check + alert) | +100 ~ +150 | manager-tdd |
| `tests/data/test_universe.py` | New file | +80 ~ +120 | manager-tdd |
| `tests/scheduler/test_data_refresh_jobs.py` | New file | +150 ~ +250 | manager-tdd |
| `tests/monitoring/test_data_freshness.py` | New file | +100 ~ +150 | manager-tdd |
| `tests/test_orchestrator.py` 또는 `tests/scheduler/test_runner.py` | Modify (job count assertion) | +0 ~ +10 | manager-tdd |
| `.moai/config/sections/data.yaml` 또는 `src/trading/data/kospi200.py` | New (KOSPI200 source) | +30 ~ +60 | manager-tdd |

총 변경 LOC 추정: ~700 ~ 1100 LOC, 9 파일, 신규 7 파일 / 수정 2 파일.

---

## Constraints

- **C-1**: 본 SPEC 의 변경은 backward compatible 해야 한다. 기존 14개 cron 잡 및 persona 동작에 회귀 없음.
- **C-2**: SPEC-016 Phase 2 (regime DB 캐싱) 의 향후 도입 시 본 SPEC 의 변경과 충돌하지 않아야 한다. universe / refresh / freshness 모듈은 regime 컬럼에 독립적.
- **C-3**: Coverage 임계 85% 유지 (`.moai/config/sections/quality.yaml`).
- **C-4**: 본 SPEC 은 P0 Critical 이므로, `/moai:2-run SPEC-TRADING-019` 의 manager-tdd 가 RED-GREEN-REFACTOR 사이클을 신속히 진행해야 한다. 목표는 5/11 (월) 늦은 저녁 또는 5/12 (화) 오전 redeploy 완료, 5/12 09:30 첫 intraday cycle 에서 캐시 hit + ≥ 1건 후보 진입 검증.
- **C-5**: 본 SPEC 의 모든 변경은 git branch `feat/spec-019-data-refresh` 로 격리, PR 단위로 사용자 리뷰.
- **C-6**: 본 SPEC 은 SPEC-016 Phase 1 + SPEC-018 위에서만 동작. 두 SPEC 의 redeploy 가 모두 완료된 상태가 전제.
- **C-7**: pykrx / DART 의 외부 API 호출 비용은 본 SPEC 의 universe 크기 (≤ 100 ticker × 1 day per cron run, 일 ~ 200 호출) 에서 무료 한도 이내. 비용 모니터링은 별도 SPEC.
- **C-8**: Telegram 알람의 chat_id 는 `.env` 의 prod bot (`@sehoon_trd_bot` / `HeremesOniTrade`) 으로 송출. dev bot (`@onitrddev_bot`) 또는 cron bot (`@kaji_genesis`) 으로 잘못 송출되어서는 안 된다. 정확한 chat_id 확인은 manager-tdd 가 `.env` 또는 사용자 메모리에서 확인.

---

## Risks

| ID | 리스크 | 영향 | 가능성 | 대응 |
|---|---|---|---|---|
| R-1 | pykrx 의 rate-limit 초과로 일부 cron 잡 실패 | Medium | Medium | per-ticker 격리 (REQ-019-1 d) 로 batch 중단 방지. 16:00 / 16:05 5분 간격으로 OHLCV/flows 분리. 정교한 retry 는 REQ-019-8 의 P1 follow-up |
| R-2 | DART API 의 12일 backfill 호출이 일일 한도 초과 | Medium | Low | DART 일일 한도는 보통 1만 회 이상 — 12일 backfill ≤ 1000 호출 이내 |
| R-3 | KOSPI200 top-50 조회가 매 cron 마다 외부 API 부담 | Low | Medium | 정적 yaml 우선 검토. 동적 조회 시 daily cache 도입 |
| R-4 | active holdings 조회의 정식 진입점이 코드베이스에 명확하지 않음 | Medium | Medium | manager-tdd 가 RED 단계 직전에 코드 탐색 후 결정. 미발견 시 빈 set 으로 대체 (DEFAULT_WATCHLIST + screened 만으로도 충분) |
| R-5 | Telegram 알람의 잘못된 chat_id (dev/prod 혼동) | High | Low | C-8 명시. manager-tdd 가 `.env` 확인 후 명시적 변수명 사용 (`TELEGRAM_PROD_CHAT_ID`). 단위 테스트에서 chat_id 검증 |
| R-6 | 09:00 stale 알람이 false-positive 발생 (실제 KRX 휴장일 직후) | Medium | Medium | REQ-019-5 (e) 의 KRX 휴장일 보정 로직 단위 테스트 필수. 휴장일 후 첫 거래일에는 expected_ts 가 정확히 직전 trading day 로 계산되어야 함 |
| R-7 | 컨테이너 재시작 직후 첫 09:00 알람이 stale 을 잘못 감지 (REQ-019-7 미적용 시) | Medium | Medium | REQ-019-7 (bootstrap backfill) 을 P0 으로 격상 검토. 또는 첫 24시간 동안 알람 grace period 적용 |
| R-8 | 본 SPEC 완료 후에도 cron 자체가 실패하면 데이터 stale 재발 | Medium | Low | REQ-019-5 의 stale 알람이 정확히 이 시나리오의 catch-net 역할 — defense in depth |

---

## Rollout Plan

### 단일 Phase — 5/11 (월) 저녁 ~ 5/12 (화) 오전

1. (저녁) `feat/spec-019-data-refresh` 브랜치 생성
2. (저녁 ~) `/moai:2-run SPEC-TRADING-019` 실행 → manager-tdd 가 RED-GREEN-REFACTOR 사이클 진행
   - Pre-RED: active holdings / KOSPI200 source 의 코드 탐색
   - RED: 3개 테스트 파일에 핵심 테스트 케이스 작성, 모두 실패 확인
   - GREEN: `universe.py`, `refresh_market_data.py`, `data_freshness.py` 구현 + `runner.py` cron 잡 5개 추가
   - REFACTOR: 코드 정리 + 기존 65 테스트 (SPEC-018 기준) 통과 확인
3. Coverage 검증, ruff/black 통과, PR 생성, 사용자 리뷰
4. `make redeploy` (SPEC-016 Phase 1 의 단일 진입점) 으로 컨테이너 재배포
5. (5/12 06:30) daily_screen 잡 정상 작동 확인 (기존 동작 유지)
6. (5/12 09:00) stale-monitor cron 첫 실행 — gap 알람 (예상): `ohlcv 9d stale, disclosures 11d stale` Telegram 송출 확인. 알람이 송출되지 않으면 monitoring 결함.
7. (5/12 09:30) 첫 intraday cycle — 데이터는 아직 refresh 안 됨 (16:00 cron 미실행) → micro persona 가 screened 후보로 캐시 미스 (오늘과 동일). 본 SPEC 의 cycle gate 가 아님.
8. (5/12 16:00) OHLCV refresh cron 첫 실행 — Telegram 알람 (또는 로그) 으로 success_count / error_count 확인. universe 전체에 대해 90일 backfill 수행 (캐시 미스 + REQ-019-1 (c)).
9. (5/12 16:05) flows cron 실행.
10. (5/13 09:00) stale-monitor 재실행 — 이제 모든 테이블이 fresh (전일 16:00 refresh 이후 < 36h) → 알람 송출되지 않음.
11. (5/13 09:30) 첫 intraday cycle — micro 가 screened 후보 선택, decision 이 캐시 hit 한 OHLCV/flows 로 평가, signals ≥ 1 건 진입 후보 반환 (본 SPEC 의 출구 게이트).
12. `/moai:3-sync SPEC-TRADING-019` 으로 문서 동기화, SPEC 상태를 `completed` 로 변경.

### Safety Gates

- **종료 전 게이트 1**: 단위 테스트 N/N 통과 (기존 65 + 신규 ~30 = ~95) AND coverage ≥ 85%
- **종료 전 게이트 2**: 사용자가 직접 `make redeploy` 후 컨테이너 healthcheck 5/5 통과 + 5개 신규 cron 잡이 APScheduler 로그에 등록 확인
- **종료 전 게이트 3**: 5/12 09:00 stale-monitor cron 이 실제 gap 을 감지하여 Telegram 알람 송출 (false-negative 검증)
- **종료 전 게이트 4**: 5/12 16:00 OHLCV cron 의 metric 로그에서 `success_count ≥ 50`, `error_count ≤ 5`
- **종료 전 게이트 5**: 5/13 09:30 cycle 에서 micro persona universe ≥ 5종 + decision persona signals 비어있지 않음 (SPEC-018 의 게이트와 동일하되 이번엔 데이터 캐시 hit 으로 통과 가능)
- **종료 전 게이트 6**: 5/14 09:00 stale-monitor 가 알람 송출하지 않음 (모든 테이블 fresh 확인, false-positive 검증)

---

## Open Questions

- **Q-1**: KOSPI200 top-50 inclusion 의 출처를 yaml (정적, 매주 수동 갱신) 으로 둘 것인가, pykrx 동적 조회로 둘 것인가? — manager-tdd 가 호출 비용 / 정확성 trade-off 평가 후 결정. 권장: 정적 yaml + 월 1회 sync 잡.
- **Q-2**: 16:00 OHLCV cron 의 acceptable max latency 는? KRX EOD 데이터 가용성이 종목에 따라 16:00 ~ 16:30 사이에 들쭉날쭉하다. 16:00 시작이 너무 이른가? — manager-tdd 가 첫 실행에서 error_count 비율 관찰 후 16:30 또는 17:00 으로 늦출지 결정.
- **Q-3**: 주말 OHLCV fetch 시도는 skip 할 것인가, attempted-but-tolerated-fail 할 것인가? — 본 SPEC 의 REQ-019-1 (f) 는 명시적으로 `mon-fri` + `is_trading_day()` 가드로 skip 정책 채택. 만약 향후 공휴일 직전/직후 데이터 누락 사례 발생 시 재검토.
- **Q-4**: REQ-019-7 (bootstrap backfill) 을 P0 으로 격상해야 하는가? — R-7 의 false-positive 우려로 권장: P0 으로 격상하고 5/12 첫 컨테이너 시작 시 자동 90일 backfill 수행. 단, manager-tdd 의 작업량 증가 → 사용자 승인 필요.
- **Q-5**: REQ-019-5 의 알람을 prod bot (`@sehoon_trd_bot`) 으로 보낼지 dev bot (`@onitrddev_bot`) 으로 보낼지? — 사용자 메모리 (`feedback_telegram_commands.md`) 기준으로는 prod bot 이 거래 알림 전담. data infrastructure 알람은 dev bot 이 적절할 수도 있음 — 사용자 결정 필요.
- **Q-6**: 본 SPEC 의 변경이 SPEC-014 (뉴스 분류기) 또는 SPEC-013 (스크리닝 잡) 의 출력에 영향을 주는가? — 답: 아니오. 본 SPEC 은 fetch 정책만 추가하며, screening / news pipeline 의 출력 포맷에는 손대지 않는다.

---

## Traceability

| Requirement | Phase | Acceptance Criteria | Files Affected (대표) |
|---|---|---|---|
| REQ-019-1 | hotfix (P0) | S-6 시나리오 1, 3, 5 | `scheduler/runner.py`, `scripts/refresh_market_data.py` |
| REQ-019-2 | hotfix (P0) | S-6 시나리오 5 (flows 동일 패턴) | `scheduler/runner.py`, `scripts/refresh_market_data.py` |
| REQ-019-3 | hotfix (P0) | acceptance.md 시나리오 (weekly) | `scheduler/runner.py`, `scripts/refresh_market_data.py` |
| REQ-019-4 | hotfix (P0) | S-6 시나리오 4 | `scheduler/runner.py`, `scripts/refresh_market_data.py` |
| REQ-019-5 | hotfix (P0) | S-6 시나리오 2 | `monitoring/data_freshness.py`, `scheduler/runner.py` |
| REQ-019-6 | hotfix (P0) | acceptance.md universe 시나리오 | `data/universe.py`, `tests/data/test_universe.py` |
| REQ-019-7 | hotfix (P1, optional) | acceptance.md bootstrap 시나리오 | `scripts/refresh_market_data.py`, 컨테이너 entrypoint |
| REQ-019-8 | hotfix (P1, optional) | acceptance.md timeout 시나리오 | `scripts/refresh_market_data.py` |
