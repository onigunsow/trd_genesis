---
id: SPEC-TRADING-021
title: "Implementation Plan -- 미국 주식 시장 통합 (Anthropic financial-services 활용)"
created: 2026-05-12
updated: 2026-05-12
status: draft
---

# Implementation Plan -- SPEC-TRADING-021

## Context Recap

- **상위 SPEC**: 본 SPEC 은 SPEC-016 Phase 1 (인프라/CLI/Jinja 안정화) + SPEC-018 (micro blocked-ticker awareness) + SPEC-019 (data refresh layer + `get_data_universe()`) + SPEC-020 (DEFAULT_WATCHLIST 편향 제거) 위에 얹는 **strategic multi-phase 확장 SPEC**.
- **타입**: planning-only. 본 plan.md 는 향후 구현 시점에 manager-ddd 또는 manager-tdd 가 참조하는 reference 이며, 본 plan 작성 시점 (2026-05-12) 에 코드 변경은 0 건.
- **차단 조건**: SPEC-016 / 018 / 019 / 020 의 1주 연속 paper-trading 무사고 운영 (~2026-05-19 KST 목표 게이트 통과) 전까지 구현 착수 금지.
- **3-Phase rollout**: Phase 1 (Foundation, 데이터 어댑터) → Phase 2 (US Coverage, screener/cycle/broker) → Phase 3 (Persona + Accounting).

## Implementation Approach

### Methodology

- **Mode**: TDD (RED-GREEN-REFACTOR) — `.moai/config/sections/quality.yaml` default. KRX 회귀 방지가 critical 하므로 test-first 필수.
- **Rationale**: 본 SPEC 의 모든 변경은 dual-market 분기 로직이고, KRX 동작에 0 영향이 핵심 acceptance criterion. test-first 로 시장별 분기 boundary 를 명시적으로 고정해야 회귀 방지 가능.

### Milestones (Priority-based, no time estimates)

본 SPEC 은 3 Phase 의 large-scope 작업이므로 milestone 을 Phase × Priority 로 나열. 각 Phase 가 독립 PR 로 출시 가능.

#### Phase 1 — Foundation (Primary Goal, P0 within SPEC-021)

1. **M-1 (Pre-RED, 탐색)**: 
   - `src/trading/data/yfinance_adapter.py` 의 현재 사용처 grep 으로 전수 조사 (idle 상태 가정 검증).
   - DB schema 검토 — `ohlcv`, `fundamentals`, `flows`, `disclosures` 의 PK 와 컬럼 확인. Q-1 (market column vs source inference) 결정.
   - `src/trading/data/cache.py` 의 upsert 함수 시그니처 확인 — US source 추가 시 호환성 검증.
2. **M-2 (RED, market_router)**: `tests/data/test_market_router.py` 신규 작성 — `get_market("005930") == "KRX"`, `get_market("AAPL") == "US"`, `get_market("invalid") raises ValueError`. 실패 확인.
3. **M-3 (RED, us_adapter)**: `tests/data/test_us_yfinance_adapter.py` 신규 — `fetch_ohlcv("AAPL", start, end)` mock 응답 + cache upsert 호환성 검증. 실패 확인.
4. **M-4 (GREEN, market_router)**: `src/trading/data/market_router.py` 신규 — `get_market` regex 분류 구현. M-2 통과.
5. **M-5 (GREEN, us_adapter)**: `src/trading/data/us_yfinance_adapter.py` 신규 — yfinance 라이브러리 wrapper, `cache.upsert_ohlcv` 호출. M-3 통과.
6. **M-6 (GREEN, schema)**: Q-1 결정에 따라 `schema_migrations/00NN_dual_market.sql` 작성 또는 미생성. 결정 근거를 본 plan.md 에 기록.
7. **M-7 (Plugin)**: Anthropic financial-services plugin (4 Skill: dcf-model, comps-analysis, earnings-analysis, morning-note) 설치 절차를 `README.md` 의 "Optional integrations" 섹션에 문서화. plugin 코드는 본 repo 에 0 byte 도 복사 금지.
8. **M-8 (REFACTOR)**: 코드 정리, type hint + docstring 보강. KRX baseline test suite (SPEC-020 기준) 모두 통과 확인, coverage ≥ 85%.
9. **M-9 (Phase 1 게이트)**: PR review + merge. 본 시점부터 production 에 dual-market data layer 활성화 (단, US cron / persona / broker 는 아직 미연결).

#### Phase 2 — US Market Coverage (Secondary Goal, P1)

10. **M-10 (Q-2 결정)**: Alpaca Paper API / IBKR paper / Tastytrade 의 1주 PoC 비교 (Python SDK, rate limit, paper→real 전환 용이성). 선정 결과를 plan.md 갱신.
11. **M-11 (RED, screener)**: `tests/screener/test_us_daily_screen.py` 신규 — S&P 500 + NASDAQ-100 universe 산출 검증 + `us_screened_tickers.json` schema 검증.
12. **M-12 (GREEN, screener)**: `src/trading/screener/us_daily_screen.py` 신규.
13. **M-13 (RED, cron)**: `tests/scheduler/test_us_cron_jobs.py` 신규 — 6 cron jobs (us_pre_market, us_intraday_1~4, us_post_market) 의 trigger 시각 + NYSE holiday 가드 + KRX cron 과의 비충돌 검증.
14. **M-14 (GREEN, cron)**: `src/trading/scheduler/runner.py` 에 6 cron jobs 추가. `timezone="America/New_York"` 으로 ET 기준 등록 (DST 자동 처리).
15. **M-15 (GREEN, orchestrator)**: `src/trading/personas/orchestrator.py` 에 `run_us_pre_market_cycle`, `run_us_intraday_cycle`, `run_us_post_market_cycle` 진입점 추가.
16. **M-16 (RED, broker)**: `tests/us/test_<broker>_client.py` 신규 — paper account mock 으로 place_order / cancel_order / query_positions / query_balance 검증.
17. **M-17 (GREEN, broker)**: `src/trading/us/<broker>_client.py` 신규 — Q-2 의 선정 브로커 SDK wrapper.
18. **M-18 (Dispatch)**: `orchestrator.py` 에 `get_market(symbol)` 기반 dispatch 분기 추가 — KIS API 호출이 US ticker 에 절대 발동되지 않음을 단위 테스트로 검증.
19. **M-19 (Phase 2 게이트)**: paper broker 로 1주 연속 paper trading + zero-error 검증 후 PR merge.

#### Phase 3 — Persona Hardening + Accounting (Final Goal, P1)

20. **M-20 (RED, persona)**: `tests/personas/test_market_awareness.py` 신규 — 4 persona prompt 의 `market="US"` trigger 응답 검증 (영문 종목명, USD, US cost model).
21. **M-21 (GREEN, persona)**: 4개 prompt template (`macro.jinja`, `micro.jinja`, `decision.jinja`, `risk.jinja`) 에 `{% if market == "US" %}` 분기 추가.
22. **M-22 (RED, fx)**: `tests/fx/test_conversion.py` 신규 — `usd_to_krw`, `krw_to_usd` 의 정확성 + FX source rate cache 검증.
23. **M-23 (GREEN, fx)**: `src/trading/fx/conversion.py` 신규. FX source 는 yfinance `KRW=X` 또는 ECOS (M-22 에서 결정).
24. **M-24 (GREEN, tax)**: `src/trading/accounting/<tax module>.py` 신규 — US 단순 2-tier 세율 (15% long / 30% short) + KRX 별도 추적.
25. **M-25 (GREEN, daily_report)**: `src/trading/reporting/daily_report.py` 수정 — KRW / USD / consolidated 3 PnL 표출.
26. **M-26 (REFACTOR)**: 코드 정리, hardcoded FX rate grep 으로 0건 검증, 전체 test suite 통과 + coverage ≥ 85%.
27. **M-27 (Phase 3 게이트)**: PR review + merge. SPEC-021 완료.

### Technical Approach

**시장 분리 원칙**:

- 모든 신규 코드는 `src/trading/data/`, `src/trading/screener/`, `src/trading/us/`, `src/trading/fx/`, `src/trading/accounting/` 디렉터리에 격리.
- 기존 KRX 코드 (`src/trading/kis/*`, `src/trading/data/pykrx_adapter.py`, `src/trading/data/dart_adapter.py`) 는 본 SPEC 의 어느 Phase 에서도 수정되지 않는다.
- `src/trading/personas/orchestrator.py`, `src/trading/scheduler/runner.py`, `src/trading/personas/prompts/*.jinja` 만 dual-market dispatch 를 위해 수정 — 단, KRX path 의 동작은 0 영향.

**Anthropic financial-services 활용 패턴**:

- Plugin 설치: `claude plugin install dcf-model@anthropics/financial-services` (외부 행위).
- Invoke 패턴: decision persona prompt 에서 tool 호출 형태로 발동 (Phase 3 의 REQ-021-7 시점).
- Vendoring 금지: `src/trading/**` 에 Anthropic 코드 0 byte. CI lint 에 grep 가드 추가 가능 (선택).

**시장 시간 처리**:

- APScheduler 의 `timezone="America/New_York"` 인자 활용 — DST (summer/standard time) 자동 처리. cron 등록 시 KST 변환 시각이 아닌 ET 기준 시각 (예: 08:00, 10:30, 12:00, 14:00, 15:30, 16:00 ET) 으로 등록.
- KRX cron 은 기존대로 KST 기준 유지 (변경 없음).

### Architecture Direction

- **Dual-market dispatch**: `get_market(symbol)` 가 single source of truth. orchestrator / risk / reporting 모든 지점에서 동일 헬퍼 호출.
- **Adapter layer**: market-specific 어댑터 (`pykrx_adapter`, `us_yfinance_adapter`) 가 동일 인터페이스 (`fetch_ohlcv(symbol, start, end)`) 노출 + 결과 row 의 `source` 컬럼으로 시장 구분.
- **Universe registry**: SPEC-019 의 `get_data_universe()` 와 평행하게 `get_us_data_universe()` 신설 (Phase 2). KRX universe 와 절대 merge 금지.
- **Persona prompt**: trigger_context 의 `market` flag 가 진입 후 prompt template 의 분기로 분리. persona 코드 자체는 시장 무관 (시장 의존성은 prompt 에 격리).

### Risks and Mitigations

- **Risk 1 (KRX 회귀)**: 본 SPEC 의 dispatch / 어댑터 변경이 KRX 동작에 영향 → **Mitigation**: 각 Phase 에 KRX baseline test suite 통과를 exit criterion 으로 명시. SPEC-019 의 478 tests + SPEC-020 의 추가 tests 가 unmodified state 유지.
- **Risk 2 (Anthropic financial-services API 변경)**: plugin 의 인터페이스 변경 시 우리 persona 호출 깨짐 → **Mitigation**: vendoring 없이 plugin 만 활용 + Phase 2 후반 본격 invoke 전 plugin pinning 정책 결정.
- **Risk 3 (US 브로커 paper account 정책 변경)**: Alpaca / IBKR 등이 무료 paper 정책 폐지 → **Mitigation**: Q-2 의 PoC 단계에서 backup 브로커 1종 선정.
- **Risk 4 (DST 처리 버그)**: APScheduler timezone 처리 오류로 US cycle 이 잘못된 시각에 발동 → **Mitigation**: M-13 의 cron test 에 DST 전환일 (3월 둘째 일요일, 11월 첫 일요일) edge case 추가.
- **Risk 5 (FX rate stale)**: yfinance KRW=X 가 weekend / holiday 에 갱신 안 됨 → **Mitigation**: `conversion.py` 에 multi-source fallback (yfinance → ECOS → 마지막 캐시).
- **Risk 6 (Phase 의존성 단절)**: Phase 1 만 merge 된 상태에서 사용자가 Phase 2 가 작동한다고 오해 → **Mitigation**: README 의 "Optional integrations" 섹션에 각 Phase 의 활성화 상태를 명시.

### Verification Approach

각 Phase 의 exit criteria 는 본 SPEC 의 spec.md `## Rollout Plan` 에 명시. acceptance.md 의 Given-When-Then 시나리오로 정량 검증.

- Phase 1: unit test only (production 0 영향) + KRX baseline 회귀 0
- Phase 2: paper broker 1주 연속 paper trading + 0 error
- Phase 3: persona prompt 검증 + FX/세금 unit test + daily_report 통합 검증

### Out-of-scope for This SPEC

본 SPEC 의 plan 은 다음을 의도적으로 배제:

- 실제 코드 작성 (planning-only 단계)
- 미국 실거래 (real money) 전환 — 별도 SPEC
- 옵션 / derivatives 거래 (Q-7 보류)
- KRX cron / persona / 어댑터의 회귀 수정
- Anthropic financial-services 의 vendoring
- 시장 간 arbitrage 로직

---

## Cross-Reference

- spec.md (본 SPEC 의 EARS 요구사항 8건)
- acceptance.md (본 SPEC 의 Given-When-Then 시나리오)
- SPEC-TRADING-019 spec.md (data refresh layer 패턴 참조)
- SPEC-TRADING-020 spec.md (DEFAULT_WATCHLIST 편향 제거 — US universe 도 동일 원칙 적용)
- `/home/onigunsow/anthropic-financial-services` (read-only reference, vendoring 금지)
