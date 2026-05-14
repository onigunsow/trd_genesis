---
id: SPEC-TRADING-023
version: 0.1.0
status: draft
created: 2026-05-14
updated: 2026-05-14
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "Universe 동적 확장 — 페르소나 후보 자동 데이터 보강"
related_specs:
  - SPEC-TRADING-022
  - SPEC-TRADING-020
  - SPEC-TRADING-019
  - SPEC-TRADING-018
  - SPEC-TRADING-016
---

# SPEC-TRADING-023 -- Universe 동적 확장 — 페르소나 후보 자동 데이터 보강

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-14 | 0.1.0 | Initial draft — 6 EARS requirements, micro persona 가 universe-out ticker 추천 시 자동 데이터 fetch + 영구 universe 확장 | onigunsow |

---

## Scope Summary

본 SPEC 은 **자율 거래 시스템 비전 완성을 위한 마지막 architectural 보강**이다. 2026-05-14 07:30 KST pre_market cycle 에서 발견된 "right candidate identified but blocked by data layer" 패턴의 네 번째 인스턴스를 영구적으로 해결한다.

### 발견된 결함 (Verified Evidence 2026-05-14 KST)

오늘 07:30 pre_market cycle:

1. micro persona 가 KOSPI200+KOSDAQ150 범위 분석 후 **케이씨텍 (281820)** 을 유일한 strong fundamental 후보로 식별 (1Q26 매출 +101% YoY, 영업익 +344% YoY, 영업이익률 22.3%, 반도체 CMP 슬러리 테마)
2. decision persona 가 검토했으나 verdict = HOLD
3. Decision rationale 인용: "케이씨텍(281820)은 1Q 어닝 서프라이즈로 펀더멘탈 양호하나 **오늘자 시세 데이터 미수신으로 기술적·수급 판단 불가**하여 진입 보류"

### 근본 원인

281820 은 `get_data_universe()` (현재 = DEFAULT_WATCHLIST ∪ screened_tickers ∪ active_holdings ∪ KOSPI200 top-50) **에 포함되지 않는다**. mid-cap 종목이므로 KOSPI200 top-50 컷오프에 들지 못한다. 결과적으로 SPEC-019 의 일일 refresh cron (16:00 OHLCV / 16:05 flows) 이 281820 을 **fetch 한 적이 한 번도 없다**. micro 가 추천해도 decision 이 검증할 OHLCV/flows 데이터가 부재.

### "Right candidate, blocked by data" 패턴 4차 발생

| 발생일 | SPEC | 사례 |
|---|---|---|
| 2026-05-11 09:30~14:30 | SPEC-018 | DEFAULT 5종 동시 단기과열 → universe fallback 필요 |
| 2026-05-11 14:41 | SPEC-019 | screened 4 후보 시세 데이터 0 → daily refresh cron 부재 |
| 2026-05-12 07:33 | SPEC-020 | 055550 (screened-only) blocked_cache 미검사 → DEFAULT bias 제거 |
| 2026-05-14 07:30 | **SPEC-023** | 281820 (universe-out) 시세 미수신 → 본 SPEC |

### 사용자 명시적 전략 목표 (2026-05-12 선언)

> "사용자 사전 큐레이팅 없이 시스템이 자율적으로 후보를 발굴 / 모니터 / 진입 타이밍을 추천하는 시스템."

본 SPEC 은 이 비전의 **마지막 architectural 게이트**: persona 가 자유롭게 추천한 ticker 가 universe 밖이어도 시스템이 즉시 데이터를 보강하고 다음날부터는 영구 monitoring 대상에 편입.

### 본 SPEC 의 위치

- **SPEC-018 (완료)**: micro persona blocked-ticker 인식 + dynamic watchlist
- **SPEC-019 (완료)**: 일일 자동 데이터 refresh cron (16:00/16:05/Sun18:00/매일18:00) + stale 알람
- **SPEC-020 (완료, main 머지)**: DEFAULT_WATCHLIST bias 제거 — screened 우선
- **SPEC-022 (feat 브랜치, 머지 대기)**: 데이터 refresh hotfix
- **SPEC-023 (본 SPEC, P1 High)**: universe 동적 확장 — persona 자율 발굴의 마지막 데이터 게이트

본 SPEC 은 SPEC-018/019/020/022 의 모든 변경과 **직교**한다. persona 코드 변경 없이도 적용 가능.

### 비즈니스 임팩트

- 281820 같은 mid-cap fundamental winner 를 micro 가 추천해도 decision 이 거부하는 시나리오를 영구 차단
- 자율 발굴 / 자율 monitoring 의 완성 — 사용자 큐레이팅 의존도 0
- SPEC-019 의 일일 refresh 가 자동으로 dynamic_tickers 도 포함 → 다음날부터 영구 monitoring
- 본 SPEC 완료 후, persona 가 추천할 수 있는 universe = "fetch 가능한 모든 한국 상장 종목" 으로 사실상 확장

---

## Environment

- 기존 SPEC-001 ~ SPEC-022 인프라 (Docker compose, Postgres 16-alpine, Telegram, KIS API, pykrx)
- 기존 5-persona 시스템 (Macro/Micro/Decision/Risk/Portfolio)
- SPEC-018 + SPEC-019 + SPEC-020 redeploy 완료 + SPEC-022 머지 대기 상태
- 기존 어댑터: `src/trading/data/pykrx_adapter.py` (`fetch_ohlcv`, `fetch_flows`, `fetch_fundamentals` 모두 idempotent)
- 기존 cache layer: `src/trading/data/cache.py` 의 `upsert_ohlcv`, `upsert_fundamentals`, `upsert_flows` — `(source, symbol, ts)` PK
- 기존 universe registry: `src/trading/data/universe.py` 의 `get_data_universe()` (DEFAULT ∪ screened ∪ holdings ∪ KOSPI200 top-50)
- 기존 orchestrator: `src/trading/personas/orchestrator.py` — micro → decision → risk → portfolio chain
- 기존 refresh entrypoint: `src/trading/scripts/refresh_market_data.py` 의 `refresh_ohlcv()`, `refresh_flows()`, `refresh_fundamentals()`
- 기존 daily report: `src/trading/reports/daily_report.py` (16:00 KST)
- 기존 schema migration 시스템: `schema_migrations/` 디렉터리
- 신규 모듈: `src/trading/data/dynamic_universe.py` (CRUD)
- 신규 entrypoint: `refresh_market_data.expand_universe_for_tickers(tickers: list[str])`
- 신규 DB 테이블: `dynamic_tickers` (저장 전략 결정은 Q-1 참조)
- 신규 테스트: `tests/data/test_dynamic_universe.py`, `tests/personas/test_universe_auto_expansion.py`

## Assumptions

- A-1: micro persona 의 출력 형식 (후보 ticker 리스트 + confidence) 은 SPEC-018/020 의 구조 그대로 유지된다.
- A-2: decision persona 호출 직전에 candidate ticker 리스트를 검사할 수 있는 hook point 가 `orchestrator.py` 에 존재한다 (line ~960 영역).
- A-3: `pykrx_adapter.fetch_ohlcv` / `fetch_flows` / `fetch_fundamentals` 는 임의의 KOSPI/KOSDAQ ticker 에 대해 호출 가능하다 (단, delisted 종목은 예외 발생).
- A-4: `cache.get_latest_ohlcv_ts(ticker)` 류 helper 가 존재하거나 즉시 추가 가능하다 (SPEC-019 의 incremental fetch 로직과 동일).
- A-5: SPEC-019 의 일일 refresh cron 은 `get_data_universe()` 의 반환값을 호출 시점에 dynamic 하게 평가한다 (변경된 universe 가 다음 cron 에 자동 반영).
- A-6: KRX 휴장일 가드 (`is_trading_day()`) 는 auto-expansion 에는 적용하지 않는다 — micro 가 휴장일 직전 또는 직후 추천 시에도 fetch 가능해야 한다 (90일 backfill 은 과거 거래일을 자동 처리).
- A-7: dynamic_tickers 의 100-cap 은 manager-tdd 가 실측 데이터 (월간 발견 신규 ticker 수) 로 조정 가능한 conservative starting point.
- A-8: 본 SPEC 은 KRX 한정. 미국 / 해외 마켓은 SPEC-021 영역 (out of scope).

---

## Goals

- **G-1 (Zero data-blocking decision)**: micro 가 추천한 ticker 의 OHLCV 데이터 부재로 인한 decision HOLD 시나리오를 영구 제거.
- **G-2 (Persona freedom)**: micro persona 가 한국 상장 종목 풀 전체에서 자유롭게 후보를 추천할 수 있고, 시스템이 데이터 게이트를 자동 해소.
- **G-3 (Permanent monitoring)**: 한 번 auto-expanded ticker 는 SPEC-019 일일 refresh 에 자동 편입되어 다음날부터 영구 monitoring.
- **G-4 (Resilience)**: 개별 ticker fetch 실패가 batch 전체를 중단시키지 않는다. delisted / network error 는 우아하게 drop.
- **G-5 (Bounded growth)**: dynamic_tickers 무한 증가 방지 — 100-cap + FIFO eviction.
- **G-6 (Backward compatibility)**: SPEC-018/019/020/022 의 모든 게이트 유지. SPEC-018 의 blocked_tickers 필터링 패턴은 auto-expansion 후에 적용.
- **G-7 (Observability)**: auto-expansion 이벤트는 매번 로그 + 일일 report 의 metric 으로 노출.

---

## Requirements

### REQ-023-1: On-demand data fetch trigger before decision persona (Event-Driven, P0)

**When** micro persona 가 candidate 리스트를 반환한 직후 AND decision persona 호출 직전, **then** 시스템은 각 candidate 에 대해 최근 7일 이내 OHLCV 데이터 존재 여부를 점검하고, 부재한 ticker 에 대해 90일치 backfill 을 자동 수행해야 한다.

세부:

- (a) **(Event-Driven)** **When** orchestrator 의 micro→decision 전환 hook 에 도달하면, 시스템은 candidate 리스트의 각 ticker 에 대해 `cache.get_latest_ohlcv_ts(ticker)` 를 호출.
- (b) **(State-Driven)** **While** `latest_ts < today - 7 days` 또는 `latest_ts is None` 이면, 해당 ticker 는 auto-expansion 대상.
- (c) **(Ubiquitous)** auto-expansion 대상 ticker 에 대해 `refresh_market_data.expand_universe_for_tickers([tickers])` 를 호출. 이 함수는 OHLCV + flows + (가능하면 fundamentals) 90일치를 fetch 하여 upsert.
- (d) **(Ubiquitous)** auto-expansion 이 성공하면 해당 ticker 를 `dynamic_universe.register(ticker, source="micro_recommendation")` 로 dynamic_tickers 레지스트리에 추가 (REQ-023-2).
- (e) **(Event-Driven)** **When** auto-expansion 이 완료되면 (성공/실패 무관), decision persona 호출로 진행. auto-expansion 실패한 ticker 는 REQ-023-3 에 따라 candidate 에서 drop.
- (f) **(Unwanted)** 시스템은 SPEC-018 의 blocked_tickers 필터링을 auto-expansion **이전** 에 적용해서는 안 된다. auto-expansion 은 blocked 여부와 무관하게 실행 (blocked 는 별개 게이트).
- (g) **(Ubiquitous)** auto-expansion 호출 시점 (orchestrator 의 micro→decision 사이) 의 정확한 line 위치는 manager-tdd 가 결정 (`src/trading/personas/orchestrator.py` line ~960 영역의 call graph 분석 후).

**Files affected**:

- `src/trading/personas/orchestrator.py` — auto-expansion hook 추가 (micro→decision 사이)
- `src/trading/scripts/refresh_market_data.py` — `expand_universe_for_tickers(tickers: list[str])` 추가
- `src/trading/data/dynamic_universe.py` (신규) — `register()`, `is_known()` API

**Dependencies**: REQ-023-2 (레지스트리 선행), REQ-023-3 (실패 처리), REQ-023-4 (타임아웃).

---

### REQ-023-2: Dynamic universe registry persistence (Ubiquitous, P0)

시스템은 auto-expanded ticker 를 영구 저장하는 **dynamic_universe registry** 를 보유해야 한다. SPEC-019 의 일일 refresh cron 은 이 registry 의 ticker 도 자동으로 universe 에 포함시켜야 한다.

세부:

- (a) **(Ubiquitous)** 저장 매체: DB 테이블 `dynamic_tickers` (Q-1 의 권장안). 컬럼: `(ticker text PK, first_seen_at timestamptz NOT NULL DEFAULT now(), last_used_at timestamptz NOT NULL DEFAULT now(), source text NOT NULL)`.
- (b) **(Ubiquitous)** `dynamic_universe.register(ticker: str, source: str) -> bool` API: ticker 가 이미 존재하면 `last_used_at` 만 업데이트 후 False 반환, 신규면 INSERT 후 True 반환.
- (c) **(Ubiquitous)** `dynamic_universe.list_active() -> list[str]` API: 현재 등록된 모든 dynamic ticker 의 정렬된 리스트 반환.
- (d) **(State-Driven)** **While** `dynamic_tickers` 의 row 수가 100 (REQ-023-5 의 cap) 에 도달했고 신규 ticker 가 register 되면, `first_seen_at` 가장 오래된 row 1건을 DELETE (FIFO eviction).
- (e) **(Ubiquitous)** `get_data_universe()` (SPEC-019 REQ-019-6) 는 union 에 `dynamic_universe.list_active()` 의 결과를 추가해야 한다 (REQ-023-5 의 priority order).
- (f) **(Unwanted)** dynamic_tickers 의 row 수가 cap 초과 상태로 영속되어서는 **안 된다** (cap 위반 시 단위 테스트 실패).

**Files affected**:

- `schema_migrations/00NN_dynamic_tickers.sql` (신규) — 테이블 + 인덱스 생성
- `src/trading/data/dynamic_universe.py` (신규) — CRUD API
- `src/trading/data/universe.py` — `get_data_universe()` 가 `dynamic_universe.list_active()` 를 union 에 포함하도록 수정
- `tests/data/test_dynamic_universe.py` (신규)

**Dependencies**: REQ-023-5 (cap + priority), REQ-023-1 (호출 지점).

---

### REQ-023-3: Failure handling — graceful drop on fetch failure (Event-Driven, P1)

**When** auto-expansion 의 개별 ticker fetch 가 실패하면 (network error, pykrx error, delisted ticker, timeout 등), **then** 시스템은 해당 ticker 만 candidate 리스트에서 drop 하고 logger.warning 으로 기록한 뒤 나머지 candidate 로 decision persona 를 정상 호출해야 한다.

세부:

- (a) **(Event-Driven)** **When** `pykrx_adapter.fetch_ohlcv` 또는 `fetch_flows` 가 예외를 raise 하면, 시스템은 해당 ticker 에 대해 logger.warning (`auto_expansion failed for {ticker}: {exception}`) 으로 기록.
- (b) **(Ubiquitous)** 실패한 ticker 는 dynamic_tickers 레지스트리에 추가되지 **않는다** (delisted ticker 가 영구 monitoring 에 편입되는 것 방지).
- (c) **(Ubiquitous)** 실패한 ticker 는 micro 의 candidate 리스트에서 제거되어 decision persona 에 전달되지 않는다.
- (d) **(Ubiquitous)** auto-expansion 의 전체 batch 가 실패해도 (예: 모든 candidate 가 실패) decision persona 는 빈 candidate 리스트로 정상 호출되어야 한다 (`signals: []` 반환은 정상 동작).
- (e) **(Ubiquitous)** 실패 카운트는 REQ-023-6 의 daily report metric 에 포함.
- (f) **(Unwanted)** 시스템은 fetch 실패 시 retry 를 즉시 반복해서는 안 된다 — 단순 skip + 다음 cycle 또는 SPEC-019 의 일일 refresh 에 위임 (SPEC-018 SPEC-019 의 per-ticker isolation 패턴과 동일).

**Files affected**:

- `src/trading/scripts/refresh_market_data.py` — `expand_universe_for_tickers` 내 per-ticker try/except
- `src/trading/personas/orchestrator.py` — 실패 ticker 의 candidate 제거 로직

**Dependencies**: REQ-023-1 (호출 지점). SPEC-019 REQ-019-1 (d) 의 invalid_ticker_filter 패턴과 동일 구조.

---

### REQ-023-4: Latency budget — per-ticker and total timeout (State-Driven, P1)

**While** auto-expansion 이 진행되는 동안, **the system shall** per-ticker 타임아웃 (default 30s) 과 total batch 타임아웃 (default 120s) 을 강제하여 decision persona 호출이 무한 대기에 빠지지 않도록 해야 한다.

세부:

- (a) **(State-Driven)** **While** 개별 ticker fetch 가 30s 를 초과하면, 시스템은 해당 fetch 를 abort 하고 REQ-023-3 (a) 의 실패 처리를 적용.
- (b) **(State-Driven)** **While** `expand_universe_for_tickers` 의 누적 실행 시간이 120s 를 초과하면, 시스템은 미처리 ticker 를 모두 candidate 에서 drop 하고 즉시 함수를 반환.
- (c) **(Ubiquitous)** 타임아웃 임계는 `.moai/config/sections/data.yaml` 또는 환경변수 (`AUTO_EXPANSION_PER_TICKER_TIMEOUT`, `AUTO_EXPANSION_TOTAL_TIMEOUT`) 로 설정 가능.
- (d) **(Ubiquitous)** 타임아웃 발생 ticker 의 카운트는 REQ-023-6 의 metric `timeout_count` 로 누적.
- (e) **(Unwanted)** 시스템은 타임아웃 발생 시 decision persona 호출을 무한 대기시켜서는 **안 된다** — 강제 abort 후 진행.
- (f) **(Ubiquitous)** 타임아웃 wrapper 의 구현은 SPEC-019 REQ-019-8 의 패턴 재사용 가능 (`concurrent.futures` 또는 `signal.alarm`, manager-tdd 결정).

**Files affected**:

- `src/trading/scripts/refresh_market_data.py` — 타임아웃 wrapper
- `.moai/config/sections/data.yaml` 또는 환경변수 처리

**Dependencies**: REQ-023-1, REQ-023-3.

---

### REQ-023-5: Universe registry includes dynamic tickers with priority order (Ubiquitous, P0)

`get_data_universe()` (SPEC-019 REQ-019-6) 는 dynamic_tickers 를 **5개 source 의 union** 에 포함시켜야 한다. universe 의 우선순위 정렬은 다음 순서로 한다.

세부:

- (a) **(Ubiquitous)** `get_data_universe() -> list[str]` 의 새로운 union 구성: `screened ∪ dynamic ∪ holdings ∪ KOSPI200_top50 ∪ DEFAULT_WATCHLIST`.
- (b) **(Ubiquitous)** 결과 list 는 다음 priority order 로 정렬: (1) screened, (2) dynamic, (3) holdings, (4) KOSPI200 top-50, (5) DEFAULT_WATCHLIST. 동일 source 내에서는 ticker code 사전순.
- (c) **(Ubiquitous)** 동일 ticker 가 여러 source 에 등장하면 가장 높은 priority 의 source 에 귀속 (중복 제거).
- (d) **(State-Driven)** **While** `dynamic_universe.list_active()` 의 row 수가 100 (cap) 초과 상태이면, REQ-023-2 (d) 의 FIFO eviction 이 즉시 적용되어 cap 이내로 복원.
- (e) **(Ubiquitous)** 100-cap 은 `.moai/config/sections/data.yaml` 의 `dynamic_universe.cap` 으로 설정 가능 (default 100).
- (f) **(Unwanted)** universe 정렬은 watchlist 정책 (micro persona 가 어떤 universe 에서 후보를 고르는가) 과 **분리** 된다 — 본 SPEC 은 fetch 정책의 우선순위만 정의.

**Files affected**:

- `src/trading/data/universe.py` — `get_data_universe()` 의 union 확장 + priority 정렬
- `src/trading/data/dynamic_universe.py` — `list_active()` API
- `.moai/config/sections/data.yaml` — `dynamic_universe.cap` 설정

**Dependencies**: REQ-023-2. SPEC-019 REQ-019-6 의 backward-compatible 확장.

---

### REQ-023-6: Observability — log + daily report integration (Ubiquitous, P1)

시스템은 매 auto-expansion 이벤트를 구조화 로그 + 일일 report 의 metric 으로 노출해야 한다.

세부:

- (a) **(Ubiquitous)** 매 `expand_universe_for_tickers` 호출 종료 시 다음 metric 을 INFO 레벨로 로그: `cycle_kind` (pre_market/intraday/etc), `requested_tickers`, `success_count`, `error_count`, `timeout_count`, `total_rows_upserted`, `duration_ms`, `dynamic_universe_size`.
- (b) **(Ubiquitous)** 매 ticker 의 등록/eviction 이벤트는 INFO 레벨로 별도 로그: `dynamic_universe registered ticker={X} source={Y}`, `dynamic_universe evicted ticker={Z} (FIFO, was first_seen={ts})`.
- (c) **(Ubiquitous)** SPEC-019 의 daily_report (16:00 KST) 는 다음 행을 추가로 포함해야 한다: `오늘 auto-expansion: N건 (티커: X, Y, Z)`. N=0 이면 행 자체를 표시하지 않거나 `오늘 auto-expansion: 없음` 으로 명시.
- (d) **(Ubiquitous)** daily report 의 auto-expansion 카운트는 `dynamic_tickers` 의 당일 `first_seen_at` row 수로 계산.
- (e) **(State-Driven)** **While** 일일 auto-expansion 횟수가 5건을 초과하면, daily report 에 `⚠️ auto-expansion frequency high — check screening output` 류 경고를 추가 (선택적, manager-tdd 결정).
- (f) **(Ubiquitous)** Telegram 알람은 별도 송출하지 **않는다** — daily report 에 통합되어 한 번에 전달. (false-alert fatigue 방지)

**Files affected**:

- `src/trading/scripts/refresh_market_data.py` — metric 로그 추가
- `src/trading/data/dynamic_universe.py` — register/evict 로그
- `src/trading/reports/daily_report.py` — auto-expansion 행 추가

**Dependencies**: REQ-023-1 ~ REQ-023-5 의 모든 변경 위에 얹는 observability 레이어.

---

## Specifications

### S-1: dynamic_tickers 테이블 스키마

```sql
CREATE TABLE IF NOT EXISTS dynamic_tickers (
    ticker          TEXT PRIMARY KEY,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    source          TEXT NOT NULL  -- 'micro_recommendation' | 'manual' | future sources
);

CREATE INDEX IF NOT EXISTS idx_dynamic_tickers_first_seen
    ON dynamic_tickers (first_seen_at);
```

### S-2: `expand_universe_for_tickers()` 진입점 형식 (의사 코드)

```python
def expand_universe_for_tickers(
    tickers: list[str],
    cycle_kind: str,
    *,
    per_ticker_timeout_s: int = 30,
    total_timeout_s: int = 120,
) -> dict:
    """SPEC-023: On-demand backfill for micro candidates not in universe.

    Returns metric dict: {
        cycle_kind, requested_tickers, success_count, error_count,
        timeout_count, total_rows_upserted, duration_ms, dynamic_universe_size,
        successful_tickers: list[str],  # used by orchestrator to filter candidates
    }.
    """
```

### S-3: orchestrator hook 위치 (의사 코드)

```python
# src/trading/personas/orchestrator.py, around line 960 (manager-tdd to verify)

micro_result = await run_micro_persona(...)
candidate_tickers = extract_candidates(micro_result)

# SPEC-023 hook: auto-expand universe for candidates without recent OHLCV
to_expand = [t for t in candidate_tickers if not has_recent_ohlcv(t, days=7)]
if to_expand:
    expansion_metrics = expand_universe_for_tickers(
        to_expand, cycle_kind=cycle_kind,
    )
    successful = set(expansion_metrics["successful_tickers"])
    candidate_tickers = [t for t in candidate_tickers if (t not in to_expand) or (t in successful)]

# SPEC-018 blocked_tickers filter (existing)
candidate_tickers = filter_blocked(candidate_tickers)

# Proceed to decision persona
decision_result = await run_decision_persona(candidate_tickers, ...)
```

### S-4: `get_data_universe()` priority order 확장 (의사 코드)

```python
def get_data_universe() -> list[str]:
    """SPEC-023 update: include dynamic_tickers in union, with priority order.

    Priority (highest → lowest):
        1. screened_tickers.json (today's daily_screen output)
        2. dynamic_universe.list_active()      # NEW (SPEC-023)
        3. active holdings
        4. KOSPI200 top-50
        5. DEFAULT_WATCHLIST

    Within same priority bucket, sort by ticker code ascending.
    Deduplicate: ticker assigned to highest-priority source only.
    """
```

### S-5: Acceptance Criteria 매핑

acceptance.md 의 6개 G/W/T 시나리오는 REQ-023-1 ~ REQ-023-6 에 다음과 같이 매핑된다:

- 시나리오 1 (281820 정상 auto-expansion): REQ-023-1 + REQ-023-2 + REQ-023-5
- 시나리오 2 (다음날 16:00 cron 자동 포함): REQ-023-2 + REQ-023-5 + SPEC-019 통합
- 시나리오 3 (delisted ticker graceful drop): REQ-023-3
- 시나리오 4 (FIFO eviction): REQ-023-2 (d) + REQ-023-5 (d)
- 시나리오 5 (timeout drop): REQ-023-4 + REQ-023-3
- 시나리오 6 (daily report integration): REQ-023-6

---

## Non-Goals (Out of Scope)

본 SPEC 은 다음 항목을 **명시적으로 다루지 않는다**:

- 실거래 전환 토글 — SPEC-017 영역
- 미국 / 해외 마켓 데이터 통합 — SPEC-021 영역
- persona prompt 변경 — micro/decision/risk/portfolio 의 LLM 프롬프트는 무관
- 신규 persona 타입 추가
- KOSPI200 universe source 변경 — SPEC-019 Q-1 에서 pykrx dynamic 으로 결정됨
- 90일 초과 백필 — 90일이면 기술 분석에 충분 (이동평균, RSI, 거래대금 추이)
- FIFO 외 eviction 전략 (LRU, score-based) — premature optimization, follow-up SPEC 가능
- dynamic_tickers 의 자동 cleanup 정책 (예: 90일간 사용되지 않은 ticker 제거) — follow-up SPEC
- pykrx 외 데이터 소스 추가 (Bloomberg, Refinitiv 등)
- screened_tickers.json 의 출력 알고리즘 변경 — SPEC-013 영역

---

## Implementation Hints (manager-tdd 참고용)

본 SPEC 은 specification 만 정의. 실 코드는 `/moai:2-run SPEC-TRADING-023` 의 manager-tdd 에 위임. 힌트:

- **Storage 결정 (Q-1)**: DB 테이블 `dynamic_tickers` 권장. 이유: (1) atomicity — cap eviction 시 race condition 회피, (2) audit trail — `first_seen_at` 으로 발견 시점 추적, (3) SPEC-019 의 cron 이 이미 DB 접근 — JSON 파일 신규 도입 대비 인프라 비용 0.
- **Orchestrator hook 위치**: `src/trading/personas/orchestrator.py` 의 micro persona 호출 직후 + decision persona 호출 직전. line ~960 영역으로 추정되나 manager-tdd 가 grep 으로 정확한 위치 확인 필요. 키워드: `run_micro_persona`, `decision_persona`, `candidate`.
- **`has_recent_ohlcv` helper**: `cache.get_latest_ohlcv_ts(ticker)` 의 반환값과 `today - 7 days` 비교. helper 신규 추가 또는 inline 처리.
- **Timeout 구현**: SPEC-019 REQ-019-8 가 이미 정의한 패턴 재사용. `concurrent.futures.ThreadPoolExecutor.submit().result(timeout=N)` 또는 `signal.alarm` 중 선택. pykrx 가 동기 라이브러리이므로 thread pool 권장.
- **Migration filename**: `schema_migrations/` 디렉터리 확인 후 다음 번호 사용 (예: 기존 마지막이 0008 이면 `0009_dynamic_tickers.sql`).
- **FIFO eviction race condition**: cap 100 도달 시 INSERT 와 DELETE 를 동일 transaction 으로 묶기 (PostgreSQL `WITH` CTE 또는 `BEGIN/COMMIT` block).
- **테스트 fixture**: `tests/personas/test_universe_auto_expansion.py` 에서 pykrx_adapter 를 monkeypatch 로 stub. 281820 시나리오 + delisted 시나리오 + timeout 시나리오를 단위 테스트로 영구 재현.
- **회귀 영향**: 본 SPEC 은 SPEC-018 의 blocked_tickers filter 의 **상위** (먼저 실행) 에서 작동. blocked_tickers 가 candidate 리스트에서 ticker 를 제거하는 것은 auto-expansion 이후. SPEC-018 의 모든 테스트 그대로 통과해야 함.
- **Daily report 통합**: `src/trading/reports/daily_report.py` 의 기존 섹션에 한 줄 추가. SQL: `SELECT ticker FROM dynamic_tickers WHERE first_seen_at::date = current_date::date ORDER BY first_seen_at`.

---

## Files Expected to Change (구현 단계 참고)

| File | Change Type | Rough LOC | Owner |
|---|---|---|---|
| `src/trading/data/dynamic_universe.py` | New file | +40 ~ +60 | manager-tdd |
| `src/trading/data/universe.py` | Modify (extend `get_data_universe`) | +15 ~ +25 | manager-tdd |
| `src/trading/scripts/refresh_market_data.py` | Modify (add `expand_universe_for_tickers`) | +40 ~ +60 | manager-tdd |
| `src/trading/personas/orchestrator.py` | Modify (auto-expansion hook) | +15 ~ +25 | manager-tdd |
| `src/trading/reports/daily_report.py` | Modify (auto-expansion metric line) | +5 ~ +15 | manager-tdd |
| `schema_migrations/00NN_dynamic_tickers.sql` | New file | +15 | manager-tdd |
| `tests/data/test_dynamic_universe.py` | New file | +60 ~ +90 | manager-tdd |
| `tests/personas/test_universe_auto_expansion.py` | New file | +60 ~ +90 | manager-tdd |

총 변경 LOC 추정: ~250 ~ 380 LOC (테스트 포함), 8 파일, 신규 4 파일 / 수정 4 파일. 본문(non-test) 코드 ~130 ~ 200 LOC — 가이드라인의 100~150 LOC 보다 약간 상회하나 architectural SPEC 의 수용 범위.

---

## Constraints

- **C-1**: backward compatible 필수. SPEC-018/019/020/022 의 모든 게이트 유지. 기존 488개 테스트 그대로 통과.
- **C-2**: Coverage 임계 85% 유지 (`.moai/config/sections/quality.yaml`).
- **C-3**: 본 SPEC 의 변경은 `feat/spec-023-universe-auto-expansion` 브랜치로 격리, PR 단위로 사용자 리뷰.
- **C-4**: SPEC-022 머지 후 본 SPEC 머지. 단일 redeploy 로 SPEC-022 + SPEC-023 동시 반영.
- **C-5**: dynamic_tickers 테이블은 schema migration 으로만 생성. ad-hoc DDL 금지.
- **C-6**: auto-expansion 의 추가 외부 API 호출 (pykrx) 은 평균 cycle 당 1~3건 ticker 수준 — SPEC-019 의 100 ticker × daily refresh 와 비교하여 무시 가능한 비용.
- **C-7**: 본 SPEC 은 P1 High 이나, micro persona 가 universe-out ticker 를 자주 추천하는 패턴 발견 시 P0 으로 격상 검토.

---

## Risks

| ID | 리스크 | 영향 | 가능성 | 대응 |
|---|---|---|---|---|
| R-1 | pykrx fetch 가 rate-limit 초과 — 동일 시간대 SPEC-019 daily refresh 와 충돌 | Medium | Low | auto-expansion 은 pre_market (07:30) / intraday (09:30~) 에 주로 발생, SPEC-019 16:00 과 시간대 무관 |
| R-2 | delisted ticker 가 dynamic_tickers 에 등록되어 영구 noise | Medium | Medium | REQ-023-3 (b) — fetch 실패 시 register 하지 않음. 단위 테스트로 검증 |
| R-3 | 100-cap 이 너무 작아 활발한 발견 시 빈번한 eviction | Low | Medium | manager-tdd 가 1주일 운영 후 cap 재평가. config 로 즉시 조정 가능 |
| R-4 | orchestrator hook 위치가 잘못되어 blocked_tickers filter 와 순서 충돌 | High | Low | REQ-023-1 (f) 명시 — manager-tdd 가 RED 단계에서 통합 테스트로 검증 |
| R-5 | dynamic_tickers eviction 시 INSERT/DELETE race condition | Medium | Low | C-5 의 schema migration 에서 transaction wrap. 단위 테스트로 동시성 검증 |
| R-6 | timeout 30s 가 pykrx 평균 응답 시간 (1~3s) 대비 과도하게 관대 | Low | Low | conservative 값 — 실측 후 짧게 조정 가능. 첫 배포는 안전 우선 |
| R-7 | dynamic_tickers 가 SPEC-019 의 일일 refresh 부하를 점진 증가 | Low | Medium | 100-cap 이 hard limit 으로 작용. 추가 ticker 100건은 SPEC-019 의 universe size (~ 50~150) 대비 무시 가능 |
| R-8 | daily report 에 auto-expansion 행이 추가되어 기존 report 포맷 회귀 테스트 깨짐 | Low | Medium | manager-tdd 가 기존 daily_report 테스트의 expected 출력 업데이트 |

---

## Rollout Plan

### 단일 Phase — SPEC-022 머지 후 즉시

1. SPEC-022 의 `feat/spec-022-...` PR 이 main 머지 완료 확인
2. `feat/spec-023-universe-auto-expansion` 브랜치 생성 (base: main 의 최신 commit)
3. `/moai:2-run SPEC-TRADING-023` 실행 → manager-tdd 가 RED-GREEN-REFACTOR 사이클
   - Pre-RED: orchestrator.py 의 micro→decision hook point 확인 + schema_migrations 디렉터리 마지막 번호 확인
   - RED: 2개 신규 테스트 파일에 핵심 케이스 (281820 시나리오, delisted 시나리오, FIFO eviction, timeout) 작성, 모두 실패 확인
   - GREEN: `dynamic_universe.py` 신규 작성, `expand_universe_for_tickers` 추가, orchestrator hook 삽입, daily_report 행 추가
   - REFACTOR: 코드 정리 + 기존 488개 테스트 통과 확인 + coverage ≥ 85% 검증
4. Coverage / ruff / black 통과, PR 생성, 사용자 리뷰
5. SPEC-022 → main 머지 (대기), SPEC-023 → main 머지, 단일 redeploy
6. `make redeploy` 후 healthcheck 5/5 통과 확인
7. (다음날 07:30) pre_market cycle 에서 micro 가 universe-out ticker 추천 시 auto-expansion 발동 확인 (Telegram dev bot 로그 또는 daily report 의 16:00 row 확인)
8. `/moai:3-sync SPEC-TRADING-023` 으로 문서 동기화, status → `completed`

### Safety Gates

- **게이트 1**: 488 + ~10 = ~498 단위 테스트 통과 AND coverage ≥ 85%
- **게이트 2**: `make redeploy` 후 5/5 healthcheck 통과 + APScheduler 의 cron 잡 카운트 정상 (SPEC-019 의 19개 그대로)
- **게이트 3**: 다음 pre_market 또는 intraday cycle 에서 universe-out ticker 추천 시 (테스트 시나리오로 수동 트리거 가능), auto-expansion 로그가 출력되고 decision persona 가 정상 signals 반환
- **게이트 4**: 16:00 daily report 에 auto-expansion 행이 정상 표시 (당일 발생 시) 또는 표시 없음 (미발생 시)
- **게이트 5**: 다음날 16:00 SPEC-019 daily refresh 가 dynamic_tickers 의 ticker 도 자동 포함 — `success_count` 증가로 확인

---

## Open Questions

- **Q-1**: dynamic_universe 의 storage — DB 테이블 vs JSON 파일? — **권장: DB 테이블**. 이유: (1) cap eviction 의 atomicity, (2) audit trail (first_seen_at, last_used_at), (3) SPEC-019 가 이미 DB 의존, JSON 신규 도입 시 cross-instance 동기화 이슈. manager-tdd 최종 결정.
- **Q-2**: auto-expansion 을 intraday cycle 에도 트리거할 것인가, pre_market 만? — **권장: 양쪽 모두**. 이유: micro 가 intraday 에 새 후보를 발견할 수 있고, fetch 비용은 ticker 1~3건 × 90일 = 무시 가능. manager-tdd 가 비용/효익 평가 후 결정.
- **Q-3**: 100-ticker cap 이 적절한가? — **manager-tdd 가 첫 1주일 실측 후 조정**. 초기 conservative 값. 1년치 운영 데이터 누적 후 별도 SPEC 으로 자동 조정 정책 도입 검토.
- **Q-4**: auto-expansion 이 KIS API rate limit 을 존중해야 하는가? — **답: pykrx 만 사용**. KIS API 는 본 SPEC 영역 외. SPEC-019 의 backoff 패턴 (있다면) 재사용. 없다면 단순 try/except 로 충분.
- **Q-5**: dynamic_tickers 의 자동 cleanup (90일 미사용 ticker 제거) 을 본 SPEC 에 포함해야 하는가? — **답: 아니오**. premature optimization. follow-up SPEC 로 분리.
- **Q-6**: auto-expansion 실패 시 Telegram 알람을 즉시 송출할 것인가? — **답: 아니오**. REQ-023-6 (f) 에 명시 — daily report 에 통합. fatigue 방지.

---

## Traceability

| Requirement | Phase | Acceptance Criteria | Files Affected (대표) |
|---|---|---|---|
| REQ-023-1 | architectural (P0) | acceptance.md 시나리오 1 | `personas/orchestrator.py`, `scripts/refresh_market_data.py` |
| REQ-023-2 | architectural (P0) | acceptance.md 시나리오 1, 4 | `data/dynamic_universe.py`, `schema_migrations/00NN` |
| REQ-023-3 | architectural (P1) | acceptance.md 시나리오 3 | `scripts/refresh_market_data.py`, `personas/orchestrator.py` |
| REQ-023-4 | architectural (P1) | acceptance.md 시나리오 5 | `scripts/refresh_market_data.py` |
| REQ-023-5 | architectural (P0) | acceptance.md 시나리오 2, 4 | `data/universe.py`, `data/dynamic_universe.py` |
| REQ-023-6 | architectural (P1) | acceptance.md 시나리오 6 | `reports/daily_report.py`, `scripts/refresh_market_data.py` |
