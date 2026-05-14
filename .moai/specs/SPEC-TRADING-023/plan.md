---
spec_id: SPEC-TRADING-023
version: 0.1.0
status: draft
created: 2026-05-14
priority: high
---

# Implementation Plan — SPEC-TRADING-023

## Primary Goal

micro persona 가 추천한 universe-out ticker (예: 281820 케이씨텍) 에 대해 시스템이 자동으로 OHLCV/flows 90일 backfill 을 수행하고, 해당 ticker 를 영구 monitoring universe (dynamic_tickers) 에 편입시켜 다음날부터 SPEC-019 일일 refresh 가 자동 처리하도록 한다. 결과적으로 "right candidate, blocked by data" 패턴의 영구적 해결.

## Technical Approach

### Architecture Overview

본 SPEC 은 4개의 계층 변경을 도입한다:

1. **Storage layer**: 신규 DB 테이블 `dynamic_tickers` — auto-expanded ticker 의 영구 저장 + audit trail
2. **Data layer**: 신규 모듈 `src/trading/data/dynamic_universe.py` — register/list_active/evict CRUD API
3. **Universe layer**: 기존 `src/trading/data/universe.py` 의 `get_data_universe()` 확장 — 5-source union (priority order: screened → dynamic → holdings → KOSPI200 → DEFAULT)
4. **Orchestration layer**: `src/trading/personas/orchestrator.py` 의 micro→decision hook 에 auto-expansion 호출 삽입

### Data Flow

```
[micro persona returns candidates]
        ↓
[orchestrator hook: filter candidates lacking recent OHLCV]
        ↓
[refresh_market_data.expand_universe_for_tickers(missing)]
        ↓
   ├── pykrx OHLCV 90d backfill (per-ticker, timeout 30s, total 120s)
   ├── pykrx flows 90d backfill
   ├── (optional) pykrx fundamentals
   ├── failed tickers → drop from candidates, log warning
   └── successful tickers → dynamic_universe.register(ticker, source)
        ↓
[SPEC-018 blocked_tickers filter (existing)]
        ↓
[decision persona with refined candidates]
        ↓
[next day 16:00 SPEC-019 cron]
        ↓
[get_data_universe() now includes dynamic_tickers → ticker auto-refreshed forever]
```

### Critical Design Decisions

#### D-1: DB Table over JSON File (Q-1 decision)

**Choice**: DB 테이블 `dynamic_tickers` (PostgreSQL)
**Rejected alternative**: `data/dynamic_universe.json` 파일

**Rationale**:
- Atomicity — 100-cap FIFO eviction 시 INSERT + DELETE 를 단일 transaction 으로 처리해야 race condition 없음. JSON 파일은 file lock 도입 시 복잡도 폭증.
- Audit trail — `first_seen_at`, `last_used_at` 컬럼으로 발견 시점 및 사용 빈도 추적. daily report 의 "오늘 N건" 카운팅에 SQL 한 줄로 즉시 가능.
- Cross-cycle consistency — pre_market / intraday / daily_refresh 가 동일 DB 에 접근. JSON 도입 시 별도 마이그레이션 + 동기화 로직 필요.
- 기존 인프라 재사용 — SPEC-019 의 schema_migrations 패턴 그대로 활용. 추가 의존성 0.

#### D-2: Hook Location (micro → decision, before blocked filter)

**Choice**: `orchestrator.py` 의 micro persona 호출 직후 + SPEC-018 blocked_tickers filter 직전
**Rejected alternative**: decision persona 내부 또는 micro persona 내부

**Rationale**:
- micro 내부 hook → micro 의 prompt 책임 영역 침범. persona 분리 원칙 위반.
- decision 내부 hook → decision 이 OHLCV 부재를 인지한 후 fetch 하면 동일 cycle 내 LLM 재호출 필요. token cost 증가.
- orchestrator hook → persona 외부의 infrastructure 책임으로 깔끔히 분리. SPEC-018 의 blocked filter 와 같은 layer 에서 sequential 적용.
- blocked filter **전에** 실행 — auto-expansion 은 blocked 여부와 무관하게 데이터를 fetch 해야 함 (blocked ticker 도 다음 주기 추천 가능성 존재).

#### D-3: Per-ticker timeout 30s, total 120s

**Choice**: 30s per ticker, 120s total batch
**Rationale**: pykrx 평균 응답 ~ 2~5s (실측 SPEC-019 운영 데이터). 30s 는 6배 안전 마진. 120s 는 4개 ticker 동시 fetch 의 worst-case (30s × 4 = 120s) 수용. decision persona 호출은 5s 이내 시작되어야 하므로 120s ceiling 합리적. config 로 override 가능 (REQ-023-4 c).

#### D-4: 100-ticker FIFO Cap

**Choice**: 100 개 ticker, FIFO eviction by `first_seen_at`
**Rationale**: conservative starting point. KOSPI+KOSDAQ 대형주 (KOSPI200 top-50 이 이미 universe 포함) 외 micro 가 추가 추천할 mid-cap 풀은 월 ~ 5~10건 추정. 100 cap = 10~20개월치 발견 누적분. 1년 실측 후 follow-up SPEC 에서 LRU 또는 score-based eviction 도입 검토.

#### D-5: No Telegram alert for auto-expansion events

**Choice**: daily report 에만 통합 (REQ-023-6 f)
**Rationale**: alert fatigue 방지. auto-expansion 은 정상 동작이지 incident 가 아님. SPEC-019 의 stale-monitor 알람과 카테고리 분리. 운영자는 16:00 daily report 에서 일일 발생 건수 확인.

---

## Milestones

### Primary Goal: P0 Requirements Implementation
- REQ-023-1: orchestrator hook + on-demand fetch trigger
- REQ-023-2: dynamic_universe registry + DB persistence
- REQ-023-5: get_data_universe() priority order 확장

### Secondary Goal: P1 Resilience Layer
- REQ-023-3: failure handling — graceful drop
- REQ-023-4: latency budget — timeout enforcement

### Tertiary Goal: Observability
- REQ-023-6: log + daily report integration

### Final Goal: Validation & Rollout
- 488 + ~10 = ~498 단위 테스트 통과
- coverage ≥ 85%
- `make redeploy` 후 5/5 healthcheck
- 첫 auto-expansion 이벤트 실측 검증 (다음 pre_market 또는 intraday cycle)

---

## Implementation Sequence (manager-tdd 참고)

### Phase A: Pre-RED Exploration (코드 탐색)

1. `grep -n "run_micro_persona\|decision_persona\|candidate" src/trading/personas/orchestrator.py` 로 hook point 의 정확한 line 식별
2. `ls schema_migrations/` 로 다음 migration 번호 확인 (예: 마지막 0008 → 신규 0009)
3. `grep -n "get_latest_ohlcv_ts\|get_latest_ts" src/trading/data/cache.py` 로 helper 존재 여부 확인
4. `cat src/trading/reports/daily_report.py | head -80` 로 daily report 의 행 추가 위치 확인

### Phase B: RED — 핵심 테스트 작성 (모두 실패 확인)

1. `tests/data/test_dynamic_universe.py`:
   - test_register_new_ticker_returns_true
   - test_register_existing_ticker_returns_false_updates_last_used
   - test_list_active_returns_sorted_tickers
   - test_fifo_eviction_when_cap_reached (cap=3 fixture)
   - test_evict_removes_oldest_first_seen_at

2. `tests/personas/test_universe_auto_expansion.py`:
   - test_universe_out_candidate_triggers_expansion (281820 시나리오)
   - test_already_in_universe_no_expansion
   - test_delisted_ticker_dropped_from_candidates
   - test_total_timeout_drops_unprocessed_tickers
   - test_auto_expansion_runs_before_blocked_filter

### Phase C: GREEN — 최소 구현

1. `schema_migrations/00NN_dynamic_tickers.sql` 작성 + 적용
2. `src/trading/data/dynamic_universe.py` 작성 (register, list_active, FIFO logic)
3. `src/trading/data/universe.py` 의 `get_data_universe()` 에 dynamic 포함 + priority order 정렬
4. `src/trading/scripts/refresh_market_data.py` 에 `expand_universe_for_tickers` 추가 (per-ticker + total timeout, per-ticker isolation)
5. `src/trading/personas/orchestrator.py` 의 micro→decision 사이에 hook 삽입
6. `src/trading/reports/daily_report.py` 에 auto-expansion 행 추가

### Phase D: REFACTOR — 코드 정리

- duplicate logic 제거, helper 추출
- 기존 488개 테스트 전수 통과 확인
- ruff / black / coverage 검증

### Phase E: 사용자 리뷰 + 머지

- PR 생성 (`feat/spec-023-universe-auto-expansion`)
- SPEC-022 머지 후 본 SPEC 머지
- 단일 redeploy

---

## Risk Mitigation

| 리스크 | 대응 방안 |
|---|---|
| orchestrator hook 위치 오류 | Phase A 의 grep 단계에서 정확한 line 확인. Phase B 의 통합 테스트로 blocked filter 와의 순서 검증 |
| FIFO eviction race condition | Phase B 에 동시성 테스트 포함. Phase C 에서 INSERT + DELETE 를 transaction wrap |
| pykrx delisted ticker 가 dynamic_tickers 에 등록 | REQ-023-3 (b) 의 단위 테스트로 검증. 실패 ticker 는 register 안 됨 |
| daily report 회귀 테스트 깨짐 | Phase D 에서 기존 daily_report 테스트의 expected 출력 동시 업데이트 |
| 100-cap 이 너무 작음 | config 로 즉시 조정 가능. 1주일 운영 후 재평가 |

---

## Dependencies on Other SPECs

- **SPEC-019 (완료)**: `get_data_universe()`, `refresh_market_data.py` 의 기반 구조 활용
- **SPEC-020 (완료)**: DEFAULT_WATCHLIST priority demotion 패턴 (priority order 의 최하위)
- **SPEC-018 (완료)**: blocked_tickers filter — 본 SPEC 의 auto-expansion 은 이 filter 이전에 실행
- **SPEC-022 (머지 대기)**: 데이터 refresh hotfix — 본 SPEC 은 SPEC-022 머지 후 단일 redeploy

본 SPEC 은 위 SPEC 의 변경에 회귀를 일으키지 않는다. 모든 기존 게이트 유지.

---

## Effort Estimate (priority-based, not time-based)

- **Highest priority** (P0 implementation): REQ-023-1, REQ-023-2, REQ-023-5
- **High priority** (P1 resilience): REQ-023-3, REQ-023-4
- **Medium priority** (P1 observability): REQ-023-6
- **Highest priority** (validation): coverage / healthcheck / first auto-expansion 검증

LOC 추정: 본문 ~130~200 LOC, 테스트 ~120~180 LOC, 총 ~250~380 LOC. architectural SPEC 의 mid-size 범주.

---

## Out of Scope (Reaffirmed)

본 plan 은 SPEC.md 의 Non-Goals 섹션을 그대로 준수한다:

- 실거래 / 미국 마켓 / persona prompt 변경 / 신규 persona 타입 / KOSPI200 source 변경 / 90일 초과 backfill / FIFO 외 eviction / 자동 cleanup 정책 / pykrx 외 데이터 소스 — 모두 out of scope.
