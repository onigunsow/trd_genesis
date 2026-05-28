---
id: SPEC-TRADING-021
version: 0.1.0
status: draft
created: 2026-05-12
updated: 2026-05-12
author: onigunsow
priority: high
issue_number: 0
domain: TRADING
title: "미국 주식 시장 통합 — Anthropic financial-services 활용"
related_specs:
  - SPEC-TRADING-020
  - SPEC-TRADING-019
  - SPEC-TRADING-018
  - SPEC-TRADING-016
---

# SPEC-TRADING-021 -- 미국 주식 시장 통합 (Anthropic financial-services 활용)

## HISTORY

| Date | Version | Changes | Author |
|---|---|---|---|
| 2026-05-12 | 0.1.0 | Initial planning draft — 8 EARS requirements, 3-phase rollout, 구현은 KRX 안정화 게이트 통과 후 (5/19 KST 목표) 착수 | onigunsow |

---

## Scope Summary

본 SPEC 은 현재 KRX 전용 paper-trading 시스템 (SPEC-001 ~ SPEC-020) 에 **미국 증시 (NYSE / NASDAQ)** 를 병렬 통합하는 전략적 multi-phase planning SPEC 이다. 본 SPEC 자체는 **planning-only** 이며, 실제 구현은 SPEC-016 Phase 1 안정화 + SPEC-018/019/020 의 1주일 연속 paper-trading 무사고 운영 (~2026-05-19 KST 목표) 게이트 통과 후 착수한다.

### 위치 및 직교성

- **SPEC-016 Phase 1 (완료, 2026-05-10 21:38 redeploy, 5/5 healthcheck 통과)**: 인프라/CLI/Jinja 정합성 안정화
- **SPEC-018 / 019 / 020 (모두 2026-05-12 08:54 KST merged)**: micro persona blocked-ticker awareness, data refresh layer, DEFAULT_WATCHLIST 편향 제거 — 자율 KRX discovery pipeline 완성
- **SPEC-017 (미시작, KRX 실거래 전환)**: KRX 단일 시장 mock → real 토글, 본 SPEC 의 dual-market 코드와 독립
- **SPEC-021 (본 SPEC, P1 strategic)**: KRX 와 병렬 운영되는 미국 증시 지원 추가. 기존 5-persona 시스템은 재사용, market-specific 어댑터 / cycle / 브로커 / FX 만 신규 추가.

본 SPEC 은 SPEC-001 ~ SPEC-020 의 **KRX 전용 동작과 완전히 직교**한다: 모든 KRX cron 잡, persona 동작, 데이터 흐름은 본 SPEC 의 Phase 1~3 어느 시점에서도 회귀 없이 유지된다.

### Anthropic financial-services 활용 전략

`https://github.com/anthropics/financial-services` (Apache 2.0, 클론 위치 `/home/onigunsow/anthropic-financial-services` — read-only reference) 는 다음을 제공한다:

- **11 MCP data connectors**: Daloopa, Morningstar, S&P Global/Kensho, FactSet, Moody's, MT Newswires, Aiera, LSEG, PitchBook, Chronograph, Egnyte — 주로 미국/서구 시장 커버
- **30+ Claude Skills**: financial-analysis, investment-banking (DCF, LBO, M&A, comps), equity-research (earnings-analysis, morning-note), private-equity, fund-admin
- **명시적 disclaimer**: "do not make recommendations, execute transactions" — 본 repo 는 분석/문서 작성 도구이며, **실제 거래 의사결정/실행은 본 프로젝트의 5-persona (Macro/Micro/Decision/Risk/Portfolio) 시스템이 단독 책임**.

**활용 원칙**:

- Anthropic financial-services 의 Skill 은 **Claude Code plugin** 형태로 설치 (vendoring 금지). 우리 repo 의 `src/` 에는 절대 복사되지 않는다.
- decision persona 의 tool 호출 형태로 invoke (`/dcf AAPL` 같은 slash command).
- MCP connector 는 본 SPEC 의 Phase 2 시점에 구독/API key 결정 (Q-5 참조).

### 비즈니스 임팩트

- 사용자 (박세훈) 의 명시적 declaration: "추후 미국 증시도 투입될 예정" — 본 SPEC 으로 시장 확장 시점의 무계획 리팩토링을 사전 방지.
- KRX 시장 운영 시간 (09:00 ~ 15:30 KST) 과 NYSE 운영 시간 (22:30 ~ 05:00 KST 익일, summer time 기준) 이 비중첩이므로 운영 효율 (스케줄러 idle time 활용) 측면에서도 이상적.

---

## Environment

- 기존 SPEC-001 ~ SPEC-020 인프라 (Docker compose, Postgres 16-alpine, Telegram, KIS API)
- 기존 5-persona 시스템 (Macro/Micro/Decision/Risk/Portfolio) — 본 SPEC 에서 prompt template 만 dual-market aware 로 확장 (Phase 3)
- 기존 KRX 어댑터 (재사용, 변경 없음):
  - `src/trading/data/pykrx_adapter.py` — KRX OHLCV / fundamentals / flows
  - `src/trading/data/dart_adapter.py` — DART 공시
  - `src/trading/data/fred_adapter.py` — FRED 미국 거시 (이미 미국 데이터, dual-market 시 macro persona 가 공유)
  - `src/trading/data/yfinance_adapter.py` — 현재 idle 상태, US 통합 시 base 로 활용 가능
- 기존 KIS API client: `src/trading/kis/*` — KRX 전용, 본 SPEC 에서 절대 수정 금지
- 기존 universe / scheduler / persona 인프라: SPEC-019 의 `get_data_universe()`, SPEC-020 의 fallback semantics, SPEC-016 의 cli_only_mode, SPEC-018 의 blocked-ticker awareness — 모두 KRX 전용 의미로 유지하되, 본 SPEC 은 동일 패턴의 US 평행 모듈을 신설
- Anthropic financial-services repo: `/home/onigunsow/anthropic-financial-services` (clone 진행 중, read-only)
- 신규 코드 (Phase 1 ~ 3 에 걸쳐 구현 — 본 SPEC 은 정의만):
  - `src/trading/data/__init__.py` — market namespace 도입
  - `src/trading/data/us_yfinance_adapter.py` — US OHLCV / fundamentals / flows
  - `src/trading/data/market_router.py` — `get_market(symbol)` 헬퍼
  - `src/trading/screener/us_daily_screen.py` — US universe 산출
  - `src/trading/personas/orchestrator.py` — `run_us_pre_market_cycle`, `run_us_intraday_cycle` 추가
  - `src/trading/scheduler/runner.py` — US cron 잡 5개 추가
  - `src/trading/us/<broker>_client.py` — 미국 브로커 client (Q-2 에서 broker 결정 후 확정)
  - `src/trading/fx/conversion.py` — KRW/USD 환산 헬퍼
  - `src/trading/personas/prompts/{macro,micro,decision,risk}.jinja` — market-specific 분기 추가
- 신규 DB migration: `schema_migrations/00NN_dual_market.sql` — `market` 컬럼 추가 또는 source-based inference (Q-1)
- 신규 테스트 디렉터리: `tests/data/test_market_router.py`, `tests/us/`, `tests/screener/test_us_daily_screen.py`, `tests/scheduler/test_us_cron_jobs.py`, `tests/fx/test_conversion.py`

---

## Assumptions

- A-1: 사용자 (박세훈) 의 declaration "추후 미국 증시도 투입될 예정" 은 strategic intent 이며 시점은 KRX 안정화 게이트 (~2026-05-19 KST) 통과 후 결정.
- A-2: Anthropic financial-services repo 의 Apache 2.0 라이선스가 상업적 활용에 제약을 부과하지 않는다 (실제 활용은 plugin 설치 + invoke 방식이므로 vendoring 미발생).
- A-3: Anthropic financial-services 의 30+ Skill 중 본 프로젝트가 활용할 후보는 DCF, comps-analysis, earnings-analysis, morning-note 4종으로 출발 (Phase 1 에서 plugin 설치만, Phase 2 에서 본격 invoke).
- A-4: 미국 ticker 는 알파벳 1~5자 (예: AAPL, GOOGL, BRK.A) 패턴이며 KRX ticker 는 6자리 숫자 패턴. `get_market(symbol)` 은 regex 분류 가능.
- A-5: yfinance Python 라이브러리는 미국 ticker 의 OHLCV / fundamentals / flows / 옵션 체인을 단일 의존성으로 커버한다. Anthropic FactSet MCP 는 Phase 2 이후 보강.
- A-6: NYSE 거래 시간 (09:30 ~ 16:00 ET, summer time 기준 KST 22:30 ~ 05:00 익일) 에 대한 cron 트리거는 APScheduler 의 timezone 인자로 처리 가능 (Asia/Seoul 과 America/New_York 동시 운영).
- A-7: 미국 paper trading 브로커 후보 (Alpaca Markets, IBKR paper, Tastytrade) 모두 무료 paper 계정 + REST API 를 제공한다. 본격 비교는 Phase 2 시점.
- A-8: 미국 시장은 한국 거주자에게 양도소득세 22% (장기 15% + 지방세) 또는 단기 누진세율이 적용된다. 정확한 세무 처리는 본 SPEC 의 Phase 3 회계 모듈에서 단순화 (15% / 30% 두 구간).
- A-9: KRX / US 거래 시간 비중첩성 (KRX 09:00~15:30 KST vs US 22:30~05:00 KST) 으로 인해 동일 Postgres 인스턴스에서 lock contention 없이 운영 가능.
- A-10: 본 SPEC 의 Phase 1 (data adapter 레이어 확장) 은 production KRX cycle 에 0% 영향이며 테스트만으로 검증 가능.
- A-11: 미국 거래일 캘린더 (NYSE holiday) 헬퍼는 `pandas_market_calendars` 또는 yfinance 자체 캘린더로 처리 가능.

---

## Goals

- **G-1 (Dual-market foundation)**: KRX / US 양 시장이 동일한 5-persona 시스템 위에서 병렬 운영되는 아키텍처 확립. 시장 간 코드 결합 없음.
- **G-2 (Backward compatibility)**: Phase 1 ~ 3 어느 시점에서도 기존 KRX persona / cycle / 거래 동작이 회귀 없이 유지. SPEC-016 ~ SPEC-020 의 모든 게이트 영구 유지.
- **G-3 (Anthropic financial-services leverage)**: 미국 시장 분석 (DCF, comps, earnings) 은 Anthropic Skill plugin 으로 invoke 하여 자체 구현 비용 최소화. vendoring 금지로 upstream 추적성 유지.
- **G-4 (Market-aware persona)**: macro / micro / decision / risk persona 가 trigger context 의 market flag (`KRX` | `US`) 를 인지하여 시장별 universe / 수수료 / 거래 시간 / 세금을 분기.
- **G-5 (FX & tax consolidation)**: KRW / USD PnL 의 일관된 환산 + 시장별 세금 분리 회계. 일일 보고서가 KRW / USD / consolidated 3종 PnL 동시 표출.
- **G-6 (Phased rollout)**: 3 단계 점진적 배포로 risk 통제. 각 Phase 가 독립적으로 verifiable + rollback 가능.
- **G-7 (Strategic decoupling)**: 본 SPEC 의 어떤 코드도 SPEC-017 (KRX 실거래 전환) 의 KIS API 영역을 건드리지 않는다.

---

## Requirements

### Phase 1 — Foundation (REQ-021-1 ~ REQ-021-3, P0 within SPEC-021)

#### REQ-021-1: Dual-market data adapter layer (Ubiquitous, P0)

**시스템은 항상** `src/trading/data/` 의 어댑터 호출이 시장을 명시적으로 식별할 수 있는 인터페이스를 제공해야 한다.

세부:

- (a) **(Ubiquitous)** `src/trading/data/__init__.py` 는 market namespace 를 노출한다 (예: `from trading.data import krx, us`).
- (b) **(Ubiquitous)** 신규 `src/trading/data/us_yfinance_adapter.py` (또는 기존 `yfinance_adapter.py` 확장) 는 US OHLCV / fundamentals / flows fetch 함수를 제공한다.
- (c) **(Ubiquitous)** 어댑터 probe interface: `fetch_ohlcv(symbol, start, end, market="us")` — market 인자가 누락되면 기존 KRX 동작 유지 (Default backward compat).
- (d) **(Unwanted)** 기존 KRX 어댑터 호출 (`pykrx_adapter.fetch_ohlcv(symbol, start, end)`) 의 시그니처는 **변경되어서는 안 된다** — SPEC-019 의 cron 잡 14개가 무회귀로 동작해야 함.
- (e) **(Ubiquitous)** US 어댑터의 결과 row 는 `cache.upsert_ohlcv` 의 기존 PK 와 호환 — `source` 컬럼이 `yfinance` 또는 `daloopa` 등으로 구분.

**Files affected**:

- `src/trading/data/__init__.py` (수정, market namespace 노출)
- `src/trading/data/us_yfinance_adapter.py` (신규)
- `src/trading/data/market_router.py` (신규, REQ-021-2 에서 정의)

**Dependencies**: REQ-021-2 (symbol namespacing 선행).

---

#### REQ-021-2: Symbol namespacing — KRX / US 분리 (Ubiquitous, P0)

**시스템은 항상** 모든 ticker symbol 이 명확하게 KRX / US 시장으로 분류 가능해야 한다.

세부:

- (a) **(Ubiquitous)** 신규 헬퍼 `get_market(symbol: str) -> Literal["KRX", "US"]` 를 `src/trading/data/market_router.py` 에 정의. 6자리 숫자 → `"KRX"`, 알파벳 (1~5자, 점 포함) → `"US"`.
- (b) **(Event-Driven)** **When** `get_market` 이 호출되고 symbol 이 어느 패턴에도 매칭되지 않으면, **then** 시스템은 `ValueError` 를 raise 해야 한다 (silently default 금지).
- (c) **(Ubiquitous)** DB `ohlcv.source` 컬럼은 다음 값을 갖는다: `pykrx` (KRX), `yfinance` (US base), `daloopa` (US enhanced, Phase 2), etc.
- (d) **(Unwanted)** 시스템은 동일 ticker symbol 이 양 시장에 동시 존재하는 경우 (예: 6자리 미국 ticker) 를 가정하지 않는다 — A-4 에 의해 패턴 분리됨.
- (e) **(Ubiquitous)** Schema 설계 결정 (Q-1): `market` 컬럼을 ohlcv / fundamentals / flows / disclosures 에 추가할지, `source` 기반 inference 로 갈지는 본 SPEC 구현 단계 (M-1 미정의 단계, Phase 1 시작 시점) 에서 결정한다.

**Files affected**:

- `src/trading/data/market_router.py` (신규)
- `schema_migrations/00NN_dual_market.sql` (신규, Q-1 결정에 따라 column 추가 또는 미생성)

**Dependencies**: 없음. Phase 1 의 가장 먼저 작업.

---

#### REQ-021-3: Anthropic financial-services Skill 통합 (Ubiquitous, P0)

**시스템은 항상** Anthropic financial-services 의 30+ Skill 을 Claude Code plugin 형태로 활용하며, 우리 repo 의 `src/` 디렉터리에 vendoring 하지 않는다.

세부:

- (a) **(Ubiquitous)** Phase 1 활용 Skill (4종): `dcf-model`, `comps-analysis`, `earnings-analysis`, `morning-note` — A-3 에 따라.
- (b) **(Ubiquitous)** 설치 경로: `claude plugin install <name>@anthropics/financial-services` (Claude Code marketplace).
- (c) **(Unwanted)** 시스템은 Anthropic financial-services 의 어떤 코드도 본 repo 의 `src/trading/**` 로 복사해서는 **안 된다** (vendoring 금지).
- (d) **(Ubiquitous)** 본 SPEC 은 plugin 설치 절차 + invoke 패턴 (예: decision persona 가 tool 호출로 `/dcf AAPL` 발동) 을 문서화한다. 실제 invoke 는 Phase 2 의 REQ-021-7 prompt template 변경 시점에 활성화.
- (e) **(Ubiquitous)** Phase 2 의 MCP connector 활용 (Daloopa, FactSet 등) 은 Q-5 의 구독/API key 결정 이후 별도 SPEC 으로 분리 가능.

**Files affected**:

- (없음 — plugin 설치는 외부 행위, 우리 repo 변경 0)
- 단, `README.md` 의 "Optional integrations" 섹션에 Anthropic plugin 활용 가이드 추가 (Phase 1 마무리 시점).

**Dependencies**: 없음. Phase 1 의 마지막 작업.

---

### Phase 2 — US Market Coverage (REQ-021-4 ~ REQ-021-6, P1)

#### REQ-021-4: US watchlist + screener (Event-Driven, P1)

**When** 22:00 KST (mon-fri, NYSE pre-market 직전), **then** 시스템은 S&P 500 + NASDAQ-100 universe 에서 daily screening 을 실행하여 `data/us_screened_tickers.json` 을 산출해야 한다.

세부:

- (a) **(Event-Driven)** APScheduler 가 22:00 KST trigger 를 발생시키면, 시스템은 `src/trading/screener/us_daily_screen.py` 의 `run()` 진입점을 호출한다.
- (b) **(Ubiquitous)** Universe source: S&P 500 + NASDAQ-100 (~600 tickers, 중복 제거 후 ~570). yfinance 의 index constituents 조회 또는 Anthropic FactSet MCP (Phase 2 후반).
- (c) **(Ubiquitous)** 출력 파일: `data/us_screened_tickers.json` — KRX 의 `data/screened_tickers.json` 과 완전 분리. SPEC-019 의 `get_data_universe()` 와 동일 schema 유지.
- (d) **(Unwanted)** 시스템은 KRX 의 `data/screened_tickers.json` 과 US 결과를 동일 파일에 merge 해서는 **안 된다** — 시장 분리 유지.
- (e) **(Ubiquitous)** NYSE 휴장일 (NYSE holiday calendar) 에는 cron 을 실행해서는 안 된다 — A-11 의 `pandas_market_calendars` 또는 동등 헬퍼 사용.

**Files affected**:

- `src/trading/screener/us_daily_screen.py` (신규)
- `data/us_screened_tickers.json` (신규 운영 데이터)
- `src/trading/scheduler/runner.py` (REQ-021-5 에서 cron 잡 추가)

**Dependencies**: REQ-021-1, REQ-021-2.

---

#### REQ-021-5: US persona cycle scheduler (Event-Driven, P1)

**When** NYSE 운영 시간 (KST 22:30 ~ 05:00 익일) 에 정해진 cycle 시각이 도래하면, **then** 시스템은 US-specific persona cycle 을 실행해야 한다.

세부:

- (a) **(Event-Driven)** 신규 cron 잡 (5종, KRX cron 과 독립):
  - `us_pre_market` 22:00 KST mon-fri (= 08:00 ET, NYSE open 90 분 전)
  - `us_intraday_1` 23:30 KST mon-fri (= 10:30 ET)
  - `us_intraday_2` 01:00 KST tue-sat (= 12:00 ET, 한국 시각으로 익일)
  - `us_intraday_3` 03:00 KST tue-sat (= 14:00 ET)
  - `us_intraday_4` 04:30 KST tue-sat (= 15:30 ET, NYSE close 30 분 전)
  - `us_post_market` 06:00 KST tue-sat (= 16:00 ET, after-hours 직후)
- (b) **(Ubiquitous)** 각 cron 은 `orchestrator.run_us_<cycle_kind>_cycle()` 진입점 호출. `cycle_kind` 는 `us_pre_market`, `us_intraday`, `us_post_market`.
- (c) **(Ubiquitous)** trigger_context 의 `market` 키는 항상 `"US"` 로 설정 (REQ-021-7 의 prompt 분기 입력).
- (d) **(Unwanted)** US cron 잡은 KRX cron 잡 (SPEC-019 의 14개) 과 동일 분/시각 충돌이 없어야 한다 — A-9 의 거래 시간 비중첩성 보장.
- (e) **(State-Driven)** NYSE 휴장일에는 모든 US cron 이 no-op 처리 (REQ-021-4 의 (e) 와 동일 헬퍼).
- (f) **(Ubiquitous)** Summer time / standard time 자동 처리: APScheduler 의 `timezone="America/New_York"` 인자 활용. 즉 cron 등록 시 KST 가 아닌 ET 기준 시각으로 등록.

**Files affected**:

- `src/trading/personas/orchestrator.py` (US cycle entry points 추가)
- `src/trading/scheduler/runner.py` (US cron 잡 6개 추가)

**Dependencies**: REQ-021-1, REQ-021-2, REQ-021-4, REQ-021-7 (persona prompt market awareness).

---

#### REQ-021-6: US execution adapter (Ubiquitous, P1)

**시스템은 항상** 미국 ticker 의 거래 주문이 KIS API 가 아닌 US-specific 브로커 client 로 라우팅되어야 한다.

세부:

- (a) **(Ubiquitous)** 신규 `src/trading/us/<broker>_client.py` 는 KRX 의 `src/trading/kis/` 와 동일 인터페이스 형태 (place_order, cancel_order, query_positions, query_balance) 를 제공한다.
- (b) **(Ubiquitous)** Phase 2 초기 브로커 선택은 paper trading 만 — 후보: Alpaca Paper API / IBKR paper account / Tastytrade paper. 선정 기준 (cost, API quality, Python SDK 성숙도) 은 Q-2 에서 명시.
- (c) **(Unwanted)** 시스템은 미국 ticker 주문을 절대 KIS API (`src/trading/kis/*`) 로 라우팅해서는 **안 된다** — `get_market(symbol)` 결과로 dispatch 분기.
- (d) **(Ubiquitous)** 실거래 전환은 본 SPEC 의 scope 외 (Non-goal 참조). Phase 2 는 paper-trading 검증으로 종료.
- (e) **(Event-Driven)** **When** US 주문이 broker error 를 반환하면, **then** 시스템은 KRX 주문과 동일한 retry / alarm 패턴 (SPEC-013 / SPEC-016) 을 적용한다 — Telegram 알람 + DB 로그.

**Files affected**:

- `src/trading/us/__init__.py` (신규)
- `src/trading/us/<broker>_client.py` (신규)
- `src/trading/personas/orchestrator.py` (dispatch 분기 추가)

**Dependencies**: REQ-021-1, REQ-021-2, Q-2 결정.

---

### Phase 3 — Persona Hardening + Accounting (REQ-021-7 ~ REQ-021-8, P1)

#### REQ-021-7: Persona prompt templates dual-market aware (Ubiquitous, P1)

**시스템은 항상** macro / micro / decision / risk persona prompt 가 trigger_context 의 `market` flag (`KRX` | `US`) 를 인지하여 시장별 universe / 수수료 / 거래 시간 / 분석 도구를 분기해야 한다.

세부:

- (a) **(Ubiquitous)** 각 prompt template (`src/trading/personas/prompts/{macro,micro,decision,risk}.jinja`) 에 `{% if market == "US" %}` 분기 추가.
- (b) **(Ubiquitous)** Macro persona: `is_us_session` 플래그 인지 + US 거시 지표 우선순위 (FRED, FOMC, CPI, NFP) — FRED 어댑터 (기존) 재사용.
- (c) **(Ubiquitous)** Micro persona: 시장별 universe 명시 — KRX 는 한국 ticker + 한글 종목명 + KRW, US 는 영문 ticker + 영문 종목명 + USD.
- (d) **(Ubiquitous)** Decision persona: 시장별 거래 비용 분기 — KRX 0.36% 왕복 (SPEC-016 Phase 1 의 cost model), US ~0.00% 수수료 + SEC 수수료 미세. Anthropic financial-services Skill (DCF, comps) 호출 가능 (REQ-021-3 의 4종 Skill).
- (e) **(Ubiquitous)** Risk persona: 시장별 포지션 한도 / 손절 정책 분기. US 는 long-only 로 시작 (Q-7 의 옵션/공매도는 본 SPEC scope 외).
- (f) **(Ubiquitous)** Anthropic financial-services 의 "Pitch Agent" system prompt 구조 (handoff workflow) 를 US persona 의 reference 로 활용 — vendoring 없이 패턴만 차용.
- (g) **(Unwanted)** KRX 시장의 prompt 동작은 본 변경으로 회귀가 발생해서는 **안 된다** — SPEC-018 / 019 / 020 의 모든 acceptance test 유지.

**Files affected**:

- `src/trading/personas/prompts/macro.jinja` (수정)
- `src/trading/personas/prompts/micro.jinja` (수정)
- `src/trading/personas/prompts/decision.jinja` (수정)
- `src/trading/personas/prompts/risk.jinja` (수정)
- `tests/personas/test_market_awareness.py` (신규)

**Dependencies**: REQ-021-1 ~ REQ-021-6, REQ-021-3.

---

#### REQ-021-8: Currency + tax accounting (Ubiquitous, P1)

**시스템은 항상** KRW / USD 변환이 단일 helper 모듈을 통과하고, 시장별 세금 회계가 분리되어 보고되어야 한다.

세부:

- (a) **(Ubiquitous)** 신규 `src/trading/fx/conversion.py` 는 `usd_to_krw(amount, ts)`, `krw_to_usd(amount, ts)` 헬퍼를 제공. FX rate source 는 yfinance 의 `KRW=X` 또는 한은 ECOS (기존 `ecos_adapter`).
- (b) **(Unwanted)** 시스템은 코드 내 어디서도 hardcoded FX rate (예: `1300 KRW/USD`) 를 사용해서는 **안 된다** — 항상 `conversion.py` 경유.
- (c) **(Ubiquitous)** 미국 시장 capital gains tax 모델: 단기 (1년 미만 보유) 30% 원천징수, 장기 15% — A-8 의 단순화 정책. 정확한 한국 거주자 세무는 회계사 검토 후 future SPEC 으로 정교화.
- (d) **(Ubiquitous)** KRX 손실은 US 이익을 상쇄하지 **않는다** — 시장별 세무 분리 추적 (Korean income tax law).
- (e) **(Ubiquitous)** `src/trading/reporting/daily_report.py` 는 KRW PnL / USD PnL / consolidated PnL (USD → KRW 환산 후 합산) 3종을 동시에 표출.
- (f) **(Event-Driven)** **When** daily_report cron 이 실행되면, **then** 시스템은 conversion.py 를 통해 USD 보유 종목의 시가평가를 당일 KRW 환산하여 consolidated PnL 을 계산한다.

**Files affected**:

- `src/trading/fx/conversion.py` (신규)
- `src/trading/fx/__init__.py` (신규)
- `src/trading/reporting/daily_report.py` (수정 — dual-PnL 표출)
- `src/trading/accounting/<tax module>.py` (신규, 단순 2-tier 세율)
- `tests/fx/test_conversion.py` (신규)

**Dependencies**: REQ-021-1, REQ-021-6 (포지션 조회 시점).

---

## Open Questions (해결 보류, Phase 별 결정 시점 명시)

본 SPEC 은 **planning-only** 이므로 다음 질문들은 의도적으로 미해결 상태로 남긴다.

- **Q-1** (Phase 1 시작 시점): DB schema 에 `market` 컬럼을 추가할 것인지, `source` 기반 inference 로 갈 것인지. column 추가 = migration 비용 + 명확성, source inference = migration 비용 0 + 쿼리 복잡도. 결정 trigger: REQ-021-2 의 M-1 (탐색) 단계.
- **Q-2** (Phase 2 시작 시점): 미국 브로커 선택 — Alpaca Paper API vs IBKR paper vs Tastytrade. 평가 기준: ① Python SDK 성숙도, ② API rate limit, ③ paper / real 전환 용이성, ④ 옵션/공매도 지원 (Q-7 이후 확장 대비). 결정 trigger: Phase 1 종료 후 prototype 1주 PoC.
- **Q-3** (Phase 2 설계 시점): US persona cycle 이 KRX 와 동일하게 Claude CLI bridge (`cli_only_mode`, SPEC-016 Phase 1) 를 사용할지, Direct Anthropic API 로 latency 우선 호출할지. NYSE intraday cycle 은 23:30 ~ 04:30 KST 의 5회 cycle 이므로 latency 민감도가 KRX 보다 높음. 결정 trigger: Phase 2 cycle 구현 시 PoC 비교.
- **Q-4** (Phase 2 설계 시점): SPEC-019 의 KOSPI200 top-50 inclusion (DEFAULT_WATCHLIST 보강) 의 미국 등가물 — S&P 500 top-50 vs Magnificent 7 vs sector rotation list. screener output 의 보강 universe 정책 결정.
- **Q-5** (Phase 2 후반): Anthropic financial-services 의 11개 MCP connector (Daloopa, Morningstar, S&P Global/Kensho, FactSet, Moody's, MT Newswires, Aiera, LSEG, PitchBook, Chronograph, Egnyte) 중 구독/API key 를 조달할 connector 선정. 비용 / 데이터 품질 / 우리 시스템과의 직교성 평가.
- **Q-6** (Phase 1 종료 시점): Phase 1 (REQ-021-1 ~ REQ-021-3) 이 Phase 2 broker 선택 (Q-2) 결정 없이 단독 release 가능한지. 가능하다면 Phase 1 을 빠르게 production 에 merge 하여 후속 작업의 baseline 으로 활용.
- **Q-7** (Phase 2 ~ Phase 3 사이): US 시장은 공매도 (short selling) + 옵션 (options) 거래가 합법적으로 활성화됨. 본 SPEC 의 scope 에 long-short 또는 옵션을 포함할지, 또는 US long-only 첫 단계로 가고 향후 SPEC 으로 분리할지. 본 SPEC 의 Non-goal 에서는 일단 제외.

---

## Non-Goals (본 SPEC scope 외)

- 미국 실거래 (real money) 전환 — SPEC-017 이 KRX 실거래를 다룬 것처럼 별도 SPEC 으로 분리 예정
- 옵션 / derivatives 거래 (Q-7 보류)
- 암호화폐 / crypto 시장
- 시장 간 arbitrage (KRX vs US ADR, 예: 005930 삼성전자 vs SSNLF) — 향후 advanced SPEC
- Anthropic financial-services Skill / MCP 의 vendoring (본 repo 내 복사 금지, plugin 설치만)
- 기존 KRX persona 시스템 대체 — 본 SPEC 은 KRX 동작에 0 영향
- KRX cron / persona / 어댑터의 회귀 수정 (SPEC-018/019/020 안정화 게이트 통과 후 본 SPEC 의 Phase 1 시작)

---

## Rollout Plan (Priority-Based, no time estimates)

본 SPEC 은 3 단계 점진적 배포로 risk 통제. 각 Phase 는 독립 verifiable + rollback 가능.

### Phase 1 (Foundation, P0 within SPEC-021)

- **포함 REQ**: REQ-021-1, REQ-021-2, REQ-021-3
- **검증 방법**: 테스트만 (production KRX 동작 0 영향) — `tests/data/test_market_router.py`, `tests/data/test_us_adapter.py`
- **Exit criteria**: 
  - `get_market()` 의 KRX / US 분류 100% 정확
  - `us_yfinance_adapter.fetch_ohlcv("AAPL", ...)` 가 정상 row 반환 + cache 에 idempotent upsert
  - Anthropic financial-services plugin 설치 가이드 README 에 통합
  - 기존 KRX cron / persona / cycle 테스트 (SPEC-019 baseline 478 tests, SPEC-020 추가 tests) 모두 통과

### Phase 2 (US Market Coverage, P1)

- **포함 REQ**: REQ-021-4, REQ-021-5, REQ-021-6
- **검증 방법**: Alpaca (또는 Q-2 의 선정 브로커) paper account 실거래 검증 + 1주 paper trading 무사고
- **Exit criteria**:
  - `us_screened_tickers.json` 이 매일 22:00 KST 자동 갱신, ≥ 10개 후보 산출
  - US pre_market / intraday / post_market cycle 6 종이 NYSE 거래일에 정상 실행
  - paper broker 로 buy / sell 주문 round-trip 성공
  - US cycle 실패 시 Telegram 알람 도착 (SPEC-019 동일 패턴)

### Phase 3 (Persona Hardening + Accounting, P1)

- **포함 REQ**: REQ-021-7, REQ-021-8
- **검증 방법**: persona test (`tests/personas/test_market_awareness.py`) + FX/세금 unit test + daily_report 출력 비교
- **Exit criteria**:
  - 4개 persona prompt 가 `market="US"` trigger_context 에서 미국 universe / 영문 종목명 / USD 단위로 응답
  - `conversion.py` 호출이 hardcoded FX rate 0 건
  - daily_report 가 KRW / USD / consolidated 3 PnL 동시 표출
  - 시장별 세금 (KRW 누진 vs USD 15%/30%) 분리 계산

### 의존성 & 차단 조건

- **BLOCKED-UNTIL**: SPEC-016 Phase 1 + SPEC-018 + SPEC-019 + SPEC-020 의 1주 연속 paper-trading 무사고 운영 (~2026-05-19 KST 목표).
- **RELATED**: 향후 yaml-based watchlist SPEC (SPEC-020 의 follow-up TODO), Telegram dev/prod bot 분리 SPEC (SPEC-019 Q-5 의 follow-up TODO) 과 호환되도록 universe / 알람 인터페이스 설계.

---

## Traceability

- @SPEC:REQ-021-1 → `src/trading/data/__init__.py`, `src/trading/data/us_yfinance_adapter.py`
- @SPEC:REQ-021-2 → `src/trading/data/market_router.py`, `schema_migrations/00NN_dual_market.sql`
- @SPEC:REQ-021-3 → Anthropic plugin 설치 (외부), `README.md`
- @SPEC:REQ-021-4 → `src/trading/screener/us_daily_screen.py`, `data/us_screened_tickers.json`
- @SPEC:REQ-021-5 → `src/trading/personas/orchestrator.py`, `src/trading/scheduler/runner.py`
- @SPEC:REQ-021-6 → `src/trading/us/<broker>_client.py`
- @SPEC:REQ-021-7 → `src/trading/personas/prompts/{macro,micro,decision,risk}.jinja`
- @SPEC:REQ-021-8 → `src/trading/fx/conversion.py`, `src/trading/reporting/daily_report.py`, `src/trading/accounting/<tax module>.py`

Cross-references:
- SPEC-TRADING-016 (Phase 1 redeploy baseline)
- SPEC-TRADING-018 (micro persona blocked-ticker awareness — KRX 시장, 본 SPEC 의 US persona 도 동일 패턴 적용)
- SPEC-TRADING-019 (`get_data_universe()` registry — 본 SPEC 은 동일 패턴의 `get_us_data_universe()` 신설)
- SPEC-TRADING-020 (DEFAULT_WATCHLIST 편향 제거 — 본 SPEC 의 US universe 는 처음부터 hardcoded watchlist 금지)
