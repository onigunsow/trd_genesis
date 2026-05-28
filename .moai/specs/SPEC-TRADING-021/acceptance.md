---
id: SPEC-TRADING-021
title: "Acceptance Criteria -- 미국 주식 시장 통합 (Anthropic financial-services 활용)"
created: 2026-05-12
updated: 2026-05-12
status: draft
---

# Acceptance Criteria -- SPEC-TRADING-021

본 문서는 SPEC-TRADING-021 의 8개 EARS 요구사항에 대한 검증 기준을 Given-When-Then 시나리오로 정의한다. 본 SPEC 은 planning-only 이므로 실제 검증은 향후 구현 시점 (Phase 1 ~ 3 각 단계) 에 수행한다.

---

## Phase 1 — Foundation

### Scenario 1: Market dispatch 가 KRX 와 US ticker 를 정확히 분류 (REQ-021-2)

- **Given** dual-market 지원 (`src/trading/data/market_router.py`) 이 production 에 배포되어 있다.
- **When** `get_market("005930")` 이 호출된다.
- **Then** 반환값은 `"KRX"` 이다.
- **And When** `get_market("AAPL")` 이 호출된다.
- **Then** 반환값은 `"US"` 이다.
- **And When** `get_market("INVALID@#$")` 이 호출된다.
- **Then** `ValueError` 가 raise 된다.

### Scenario 2: US 어댑터가 yfinance 로 OHLCV 를 fetch 하고 cache 에 idempotent upsert (REQ-021-1)

- **Given** `src/trading/data/us_yfinance_adapter.py` 가 구현되어 있다.
- **And** AAPL 의 cache 에는 아직 데이터가 0 행이다.
- **When** `us_yfinance_adapter.fetch_ohlcv("AAPL", "2026-04-01", "2026-05-01")` 이 호출된다.
- **Then** yfinance API 로부터 ~20 거래일 분의 OHLCV row 가 가져와진다.
- **And** `ohlcv` 테이블에 `source="yfinance"`, `symbol="AAPL"` 인 row 가 ~20건 upsert 된다.
- **And When** 동일 함수가 다시 한 번 호출된다.
- **Then** 새 row 는 추가되지 않는다 (idempotent upsert). 기존 row 의 PK `(source, symbol, ts)` 충돌로 ignore.

### Scenario 3: KRX 어댑터 시그니처는 dual-market 변경 후에도 unchanged (REQ-021-1 (d))

- **Given** Phase 1 (REQ-021-1, REQ-021-2, REQ-021-3) 이 production 에 merge 된 직후 상태이다.
- **When** SPEC-019 의 14개 KRX cron 잡 (refresh_ohlcv, refresh_flows, refresh_fundamentals, refresh_disclosures, daily_screen, blocked_tickers_cache, build_macro_context, build_micro_context, pre_market, intraday×4, daily_report) 이 정상 스케줄대로 실행된다.
- **Then** 모든 14개 잡이 SPEC-019 baseline 과 동일한 row count + duration 으로 종료한다.
- **And** KRX baseline test suite (SPEC-020 기준) 가 100% 통과한다.
- **And** `src/trading/data/pykrx_adapter.py`, `src/trading/data/dart_adapter.py`, `src/trading/kis/*` 의 git diff 가 0 line 이다.

### Scenario 4: Anthropic financial-services Skill 이 plugin 으로 설치되고 우리 repo 에 0 byte vendoring (REQ-021-3)

- **Given** Phase 1 의 M-7 milestone 이 완료되어 README 에 plugin 설치 가이드가 존재한다.
- **When** 운영자가 `claude plugin install dcf-model@anthropics/financial-services` 를 실행한다.
- **Then** plugin 이 Claude Code 의 plugin 디렉터리 (`~/.claude/plugins/` 또는 동등 위치) 에 설치된다.
- **And When** `find /home/onigunsow/trading/src -type f | xargs grep -l "anthropics/financial-services"` 가 실행된다.
- **Then** 매칭되는 파일이 **0건** 이다 (vendoring 0).
- **And When** decision persona 가 향후 (Phase 3 REQ-021-7) `/dcf AAPL` 을 tool 호출 형태로 invoke 한다.
- **Then** plugin 의 DCF 모델이 발동되어 valuation 결과를 반환한다.

---

## Phase 2 — US Market Coverage

### Scenario 5: US screener 가 22:00 KST 에 NYSE pre-market universe 산출 (REQ-021-4)

- **Given** Phase 2 의 `src/trading/screener/us_daily_screen.py` 가 배포되고 cron 이 등록되어 있다.
- **And** 오늘은 NYSE 거래일 (월~금, US holiday 제외) 이다.
- **When** APScheduler 가 22:00 KST trigger 를 발생시킨다.
- **Then** `us_daily_screen.run()` 이 호출되어 S&P 500 + NASDAQ-100 universe 에서 screening 을 수행한다.
- **And** `data/us_screened_tickers.json` 이 갱신되어 ≥ 10개의 fresh ticker 를 포함한다.
- **And** `data/screened_tickers.json` (KRX 용) 의 내용은 **변경되지 않는다** — 시장 분리 유지.
- **And When** 오늘이 NYSE holiday 이면, 22:00 KST trigger 에 cron 이 no-op 으로 종료하고 `us_screened_tickers.json` 은 갱신되지 않는다.

### Scenario 6: US persona cycle 6종이 NYSE 거래 시간에 trigger (REQ-021-5)

- **Given** Phase 2 의 6 cron jobs (us_pre_market, us_intraday_1~4, us_post_market) 가 `timezone="America/New_York"` 으로 등록되어 있다.
- **And** 오늘은 NYSE 거래일이다.
- **When** APScheduler 가 ET 10:30 (= KST 23:30) trigger 를 발생시킨다.
- **Then** `orchestrator.run_us_intraday_cycle()` 이 호출되며, trigger_context 의 `market` 키는 `"US"`, `cycle_kind` 는 `"us_intraday"` 이다.
- **And** 동일 시각에 KRX cron 잡은 **하나도 발동하지 않는다** (KRX 거래 시간 09:00~15:30 KST 와 비중첩).
- **And When** DST 전환일 (3월 둘째 일요일) 직후, 10:30 ET 는 KST 22:30 으로 자동 이동한다 — APScheduler timezone 인자가 처리.

### Scenario 7: US 거래 신호가 KIS API 가 아닌 US broker 로 라우팅 (REQ-021-6)

- **Given** Phase 2 의 US broker client (`src/trading/us/<broker>_client.py`) 가 paper account 로 연결되어 있다.
- **And** 23:45 KST 에 micro persona 가 AAPL buy signal 을 생성하고 decision persona 가 승인한다.
- **When** orchestrator 가 주문 실행 단계에 진입한다.
- **Then** `get_market("AAPL") == "US"` 분기로 US broker client 가 호출된다.
- **And** KIS API (`src/trading/kis/*`) 는 **호출되지 않는다** — code coverage tool 로 검증 가능.
- **And** paper broker 가 buy order 를 accept 하고 fill 응답을 반환한다.
- **And When** 동일 cycle 에서 KRX ticker (예: 005930) 의 signal 이 생성되면, 그 주문은 KIS API 로 라우팅된다 — dual dispatch 검증.

### Scenario 8: Phase 1 만 merge 된 상태에서 KRX cycle 무회귀 (REQ-021-1 ~ REQ-021-3 부분 배포)

- **Given** Phase 1 의 REQ-021-1, REQ-021-2, REQ-021-3 만 production 에 merge 되어 있고, Phase 2 (US cron / broker) 와 Phase 3 (persona / FX) 는 아직 배포 전이다.
- **When** 본 시점에 KRX 의 모든 cron 잡 (SPEC-019 의 14개) 이 정해진 스케줄대로 실행된다.
- **Then** 14개 잡이 SPEC-020 안정화 게이트 통과 시점 (~2026-05-19 KST) 의 baseline 과 동일한 row count, error count, duration 으로 종료한다.
- **And** KRX persona (macro/micro/decision/risk/portfolio) 의 응답은 한국 ticker / 한글 종목명 / KRW 단위로 유지된다.
- **And** US cron 잡은 아직 등록되지 않았으므로 22:00 KST 에 어떤 트리거도 발동하지 않는다.

---

## Phase 3 — Persona Hardening + Accounting

### Scenario 9: Decision persona 가 Anthropic financial-services DCF Skill 을 invoke (REQ-021-3 + REQ-021-7)

- **Given** Phase 3 까지 배포되어 4개 persona prompt 가 dual-market aware 이고, Anthropic plugin (dcf-model 등 4 Skill) 이 설치되어 있다.
- **And** 23:30 KST 에 us_intraday cycle 이 실행되어 decision persona 가 AAPL 후보를 평가한다.
- **When** decision persona prompt 가 `market="US"` 분기로 진입하여 valuation tool 호출 (`/dcf AAPL`) 을 발동한다.
- **Then** Anthropic financial-services 의 dcf-model Skill 이 호출되어 AAPL 의 DCF valuation 결과를 반환한다.
- **And** 결과는 decision persona 의 reasoning context 에 통합되어 buy/sell/hold 결정에 반영된다.

### Scenario 10: FX 환산이 단일 helper 경유, hardcoded rate 0건 (REQ-021-8)

- **Given** Phase 3 의 `src/trading/fx/conversion.py` 가 배포되어 있다.
- **When** `grep -rE "1[0-9]{3}\s*(KRW|won|원)" src/trading/` 이 실행된다 (hardcoded FX rate 패턴).
- **Then** 매칭되는 코드가 **0건** 이다 (단, 테스트 fixture 와 docstring 예시는 제외 가능).
- **And When** `daily_report.py` 가 USD 보유 종목의 KRW 환산을 수행한다.
- **Then** 모든 환산 호출이 `conversion.usd_to_krw(amount, ts)` 경유이며, FX rate source 는 당일 yfinance KRW=X 또는 fallback (ECOS / 캐시) 로부터 조회된다.

### Scenario 11: Daily report 가 KRW / USD / consolidated 3 PnL 동시 표출 (REQ-021-8)

- **Given** Phase 3 의 daily_report 가 dual-PnL 표출로 수정되어 있다.
- **And** 운영자가 KRX 종목 (예: 005930) 과 US 종목 (예: AAPL) 을 각각 보유 중이다.
- **When** 22:00 KST 의 daily_report cron 이 실행된다.
- **Then** 보고서에 다음 3개 PnL 섹션이 포함된다:
  - KRX PnL: KRW 단위, 005930 평가손익 + 매매손익
  - US PnL: USD 단위, AAPL 평가손익 + 매매손익
  - Consolidated PnL: USD → KRW 환산 후 합산
- **And** 세금 회계 섹션에는 KRX 와 US 가 분리된 줄로 표시되며, KRX 손실이 US 이익을 상쇄하지 않음이 명시된다.

### Scenario 12: Persona prompt 가 market flag 로 universe / 종목명 / 통화 분기 (REQ-021-7)

- **Given** Phase 3 의 4개 persona prompt 가 `{% if market == "US" %}` 분기를 포함한다.
- **When** trigger_context = `{"market": "US", "cycle_kind": "us_intraday", ...}` 으로 micro persona 가 호출된다.
- **Then** prompt 의 output 에 영문 ticker (예: AAPL, MSFT) 와 영문 종목명 (Apple Inc., Microsoft Corp.) 이 USD 단위로 표시된다.
- **And** universe 는 `data/us_screened_tickers.json` (KRX 의 screened 와 분리) 에서 도출된다.
- **And When** 동일 cycle 에서 trigger_context = `{"market": "KRX", ...}` 로 micro persona 가 호출된다.
- **Then** 한국 ticker (예: 005930) + 한글 종목명 (삼성전자) + KRW 가 표시된다 — KRX baseline 회귀 0.

---

## Quality Gates

본 SPEC 의 각 Phase 가 production 에 merge 되기 위한 정량 게이트:

- **Phase 1 게이트**:
  - 단위 테스트 100% 통과 (KRX baseline 478 tests + SPEC-021 신규 tests)
  - Coverage ≥ 85%
  - KRX cron 14개 + persona 5개 의 git diff 가 0 line (직접 수정 0 보장)
  - `find src -type f | xargs grep -l "anthropics/financial-services"` 매칭 0건

- **Phase 2 게이트**:
  - Phase 1 게이트 + 신규 US cron 6개 단위 테스트 100% 통과
  - US paper broker 1주 연속 paper trading + 0 error
  - DST 전환 edge case test 통과
  - `get_market(symbol)` dispatch coverage 100% (KRX/US 양 경로 모두 hit)

- **Phase 3 게이트**:
  - Phase 1 + Phase 2 게이트 + 신규 persona/FX/세금 test 100% 통과
  - hardcoded FX rate grep 0건
  - daily_report 의 3 PnL 표출 검증 (KRX-only / US-only / dual 보유 3 시나리오)
  - 4 persona prompt 의 market flag 분기 검증

---

## Definition of Done (per Phase)

### Phase 1 DoD
- spec.md REQ-021-1 ~ REQ-021-3 구현 완료
- 단위 테스트 + KRX baseline 회귀 0
- Anthropic plugin 설치 가이드 README 통합
- PR review 통과 + main 머지

### Phase 2 DoD
- spec.md REQ-021-4 ~ REQ-021-6 구현 완료
- Q-2 (broker 선택) 결정 plan.md 에 기록
- 1주 paper trading 무사고
- PR review 통과 + main 머지

### Phase 3 DoD
- spec.md REQ-021-7 ~ REQ-021-8 구현 완료
- KRW/USD dual-PnL 일일 보고서 정상 출력
- 시장별 세금 분리 회계 검증
- PR review 통과 + main 머지
- 본 SPEC 의 모든 open question (Q-1 ~ Q-7) 의 해결 상태가 plan.md 또는 follow-up SPEC 으로 명확히 정리됨
