---
id: SPEC-TRADING-054
version: 0.2.0
status: draft
created_at: 2026-06-20
updated_at: 2026-06-20
author: oni
priority: high
issue_number: null
labels: [dashboard, edge, read-only, asset-management, migration, reconcile-writer]
---

# SPEC-TRADING-054 — 엔터프라이즈급 자산운용·거래내역 대시보드

## HISTORY

- 2026-06-20 (v0.2.0, draft): plan-auditor FAIL(0.55) 결함 반영 개정.
  - [CRITICAL/D1] 종목별 평가금액·현재가·평가손익이 읽기전용 DB에 **부재**함을 확인(검증: `fetch_holdings`=ticker/qty_net/avg_fill_price/total_cost만, `daily_equity_snapshot`=포트폴리오 총액 단위, positions=ticker/qty/avg_cost, 시세 테이블 없음). → ADR-004 신설: 신규 `position_eval_snapshot` 테이블 + reconcile writer. 데이터 출처는 이미 종목별 평가금액을 가져오는 KIS inquire-balance(`account.py:60-72`의 `eval_amount`/`current_price`/`pnl_amount`/`pnl_pct`). REQ-054-A2/C2/C3를 이 스냅샷 기반으로 재서술.
  - [D5/ADR-002] 섹터: `ticker_metadata(ticker, sector, industry)` 테이블 + 마이그레이션 신설로 **확정 요구 승격**(REQ-054-G1 조건부→필수). ADR-002 과장 표현 정정(스키마 전무 → "ticker→업종 마스터 부재").
  - [D2] frontmatter: `labels` 추가, `created`/`updated` → `created_at`/`updated_at`.
  - [D3] REQ-054-B3 반응형에 구체 breakpoint(사이드바 ≤768px collapsible, 콘텐츠 그리드 ≥1280px 다열) + AC.
  - [D4] REQ-054-C2 "익스포저" 정의 명확화(투자비중=시가평가액/NAV).
  - [D6] REQ-054-B1 주관적 표현("핀테크/SaaS 느낌") 정규문에서 제거.
  - 마일스톤 재정렬: 데이터 파운데이션(마이그레이션 035/036 + reconcile writer)을 **M1.5**로 신설, 종목별/포트폴리오 엔드포인트(M3)는 그 뒤 의존.
  - [확인 필요] 신규 테이블 2개·마이그레이션 2건·reconcile 쓰기경로 추가는 **범위 확장 결정**이며, 운영자 본인의 명시적 확인이 선행되어야 함(코디네이터 경유 동의는 권한 없음). 본 개정은 plan-auditor 권고 설계를 반영하되 이 게이트를 명시한다.
- 2026-06-20 (v0.1.0, draft): 초안 작성. SPEC-050(다크 4탭 React 대시보드)의 [DELTA] 후속. 밝은(라이트) 전문 팔레트 전면 개편 + 자산운용 4지표군 + 라운드트립 거래원장 + 엔터프라이즈 테이블 기능. 계산 단일 원천은 기존 `edge` 패키지(재구현 금지). 피드백 루프는 본 SPEC 범위 외(후속).

---

## 1. 목적 (Why)

현재 대시보드(SPEC-050: `src/trading/dashboard/`, React+Vite+TS+ECharts, 다크 테마, 4탭 = 파이프라인/자산 통계/뉴스 인텔리전스/포지션·주문)는 관측용 데모 수준이다. 운영자는 이를 **밝은(라이트) 전문 팔레트의 엔터프라이즈급 자산운용 + 거래내역 도구**로 재탄생시키길 원한다. 동일한 데이터 레이어는 향후 **LIVE 트레이딩 서비스**에도 그대로 공급될 예정이므로, 계산 로직은 단일 원천(공유 코어)에서 읽어야 한다.

핵심 동기:
- 자산운용자가 실제로 쓰는 지표(누적/일일 실현손익, 수익률, MDD, 승률, 손익비, Sharpe, KOSPI 알파)와 포트폴리오 집중도를 한눈에 본다.
- 매수→매도 **라운드트립 원장**(1행 = 1왕복)으로 거래내역을 정직하게 추적한다.
- 필터/정렬/검색/기간선택/CSV 내보내기 등 실무 도구 기능을 갖춘다.
- [CRITICAL] 손익·라운드트립·KPI 계산을 **재구현하지 않고** 기존 `edge` 패키지에서만 읽는다. 과거 중복 손익계산이 버그를 낳았다(SPEC-039/041/048).

## 2. 배경 — 확정된 지반(Ground Truth, 읽기전용 조사로 검증)

### 2.1 공유 계산 코어는 이미 존재한다 — `trading.edge` 패키지

본 SPEC의 모든 신규 손익/KPI/라운드트립 요구는 아래 기존 모듈에서 읽는다. 신규 손익 수식 작성은 금지된다.

| edge 모듈 | 제공 | 비고 |
|---|---|---|
| `edge.roundtrips` | `RoundTrip` 데이터클래스(FIFO 매칭 1청크 = 매수→매도 1왕복). `compute_roundtrips(days)`, `build_roundtrips(rows)`(순수함수), `load_fill_rows`(DB) | `RoundTrip`은 ticker/entry_date/exit_date/qty/entry_price/exit_price/entry_fee/exit_fee/net_pnl/return_pct/holding_days/fees/cost_basis/proceeds/is_win/confidence/verdict 노출. **persona 이름은 현재 미노출**(ADR-001 참조) |
| `edge.analytics` | `Analytics`(win_rate, profit_factor, expectancy, expectancy_adj, profit_factor_adj, avg_win, avg_loss, sortino, equity_curve, realized_mdd_krw, avg/median/max_holding_days 등). `from_result(rt_result, balance)`, `time_weighted_metrics`(sharpe) | sortino는 이미 계산되나 스코어카드 API에 **미노출** |
| `edge.benchmark` | `Benchmark`(kospi_return_pct, strategy_return_pct, alpha_pct, cumulative_excess_return_pct). `compute(roundtrips)`, `kospi_closes(start,end)` | KOSPI 매수후보유 대비 알파. 지수 데이터 미스 시 `available=False` 폴백 |
| `edge.confidence` | confidence ↔ P&L 상관 분석 | 기존 `/api/confidence-analysis` 사용 |
| `edge.postmortem` | 거래 사후분석 4분류 + 페르소나 귀속 | 기존 `/api/postmortem` 사용 |
| `edge.report` | `generate(days, ...)` — roundtrips→analytics/benchmark/confidence→scorecard 전체 조립(CLI `trading edge-report`가 사용) | 신규 엔드포인트가 동일 조립 패턴 재사용 |

`src/trading/dashboard/queries.py`는 **이미** 이 코어를 읽는다: `fetch_scorecard()`가 `compute_roundtrips`→`from_result`→`benchmark.compute`→`scorecard.decide`를 호출하고, `fetch_postmortem()`가 `build_roundtrips`+`kospi_closes`를 호출한다. 신규 요구는 이 검증된 패턴을 따른다.

### 2.2 재사용 가능한 기존 엔드포인트 (변경 없이 활용)

`/api/status`(halt_state·trading_mode·current_regime·current_risk_appetite·late_cycle_*·cool_down_active·halt_reason·updated_at), `/api/decisions`, `/api/orders`, `/api/holdings`(ticker·qty_net·avg_fill_price·total_cost), `/api/equity`(trading_day·total_assets·stock_eval·cash·unrealized_pnl·drawdown_pct), `/api/scorecard`, `/api/news`, `/api/story-clusters`, `/api/trends`, `/api/postmortem`, `/api/confidence-analysis`, `/api/pipeline`.

- `/api/status`의 `cool_down_active`는 mig033 미적용 환경에서 `psycopg.errors.UndefinedColumn`을 잡아 `false`로 graceful 폴백한다(`queries.py:fetch_system_status`의 `_sql` 이중 시도). 이 폴백은 **보존**한다.
- `dashboard/db.py`에 NUMERIC→float 로더가 이미 등록되어 JSON 계약이 깨끗하다. 보존한다.

### 2.2.1 [CRITICAL] 종목별 평가 데이터는 읽기전용 DB에 부재 — 신규 스냅샷 테이블 필요 (D1/ADR-004)

종목별 **시가평가액(market_value)·현재가·평가손익(unrealized_pnl)**은 현재 읽기전용 DB 어디에도 없다(검증됨):
- `fetch_holdings` → ticker·qty_net·avg_fill_price·total_cost 만(원가 기반, 현재가 없음).
- `daily_equity_snapshot`(equity) → 포트폴리오 **총액 단위**(total_assets·stock_eval·cash·unrealized_pnl·drawdown_pct)만, 종목별 분해 없음.
- positions → ticker·qty·avg_cost 만.
- 시세(현재가) 테이블 없음.

따라서 REQ-054-A2/C2/C3(종목별 평가금액·현재가·평가손익·비중)는 기존 데이터의 단순 조인으로 충족 불가하다. ADR-004로 신규 `position_eval_snapshot` 테이블을 도입하고, **이미 종목별 평가금액을 가져오는** KIS inquire-balance 경로(`account.py:60-72` = `eval_amount`/`current_price`(prpr)/`pnl_amount`(evlu_pfls_amt)/`pnl_pct`(evlu_pfls_rt))의 reconcile(SPEC-029) 단계에서 그 값을 persist 한다. 결정 로직은 바꾸지 않고 "이미 가져온 데이터를 저장만" 한다. 대시보드는 이 테이블을 **읽기만** 한다(§6 ADR-004).

### 2.3 프론트엔드 스택(보존)

React+Vite+TS+ECharts. FastAPI가 vite 빌드 산출물을 `/static`으로 서빙(`base:/static/`, `outDir ../static`). 빌드 산출물은 git 커밋(컨테이너는 python-only, node 빌드 없음). 프론트 테스트=vitest, 백엔드 테스트=pytest. 기존 파일: `frontend/src/api/types.ts`, `api/client.ts`, `theme.ts`, `hooks/usePolling.ts`, `components/`(StatusBar·HoldingsTable·OrdersTable·PipelineView·NewsView·ChartsView·ErrorBoundary + charts/).

### 2.4 측정된 현실 — 자동 피드백을 본 SPEC에서 막는 이유

현재 실측 엣지는 **음성**이다: 거래당 기대값 -14,840원, KOSPI 대비 알파 -11%p, confidence↔P&L Spearman -0.455(반예측적), 표본 n=8 라운드트립. 이 데이터로 라이브 결정을 자동 조정(페르소나 재가중·레짐 게이팅)하는 것은 표본·유의성 게이트 없이는 시기상조다. 따라서 본 SPEC은 **관측 + 공유코어 읽기 레이어까지만** 다룬다(§7 비목표).

## 3. 가정 (Assumptions)

- (A1) `edge` 패키지의 라운드트립/손익/알파 수식은 정확하다고 신뢰한다(SPEC-044에서 검증·CLI에서 사용 중). 본 SPEC은 이를 **읽기만** 한다.
- (A2) 라운드트립은 `mode='paper'` 체결만 대상이다(현재 paper 운영). LIVE 전환 시 `load_fill_rows`의 mode 필터가 확장될 수 있으나 그 확장은 본 SPEC 범위 외(읽는 쪽은 변경 불필요).
- (A3) KOSPI 알파 패널은 지수 데이터(`benchmark.kospi_closes`)에 의존하며, 미스 시 `available=False`로 graceful degrade한다(빈 패널 아닌 "데이터 없음" 표기).
- (A4) 대시보드는 읽기전용이다. 어떤 신규 코드도 트레이딩 상태(system_state·orders·positions)를 변경하지 않는다.

## 4. EARS 요구사항

### 그룹 A — 공유코어 읽기 백엔드 엔드포인트 (M1)

REQ-054-A1 (Event-driven):
> **When** 클라이언트가 `GET /api/roundtrips`를 호출하면, the 대시보드 백엔드 **shall** `edge.roundtrips.compute_roundtrips(days)`로 산출한 `RoundTrip[]`을 JSON 배열로 반환한다(필드: ticker, entry_date, exit_date, qty, entry_price, exit_price, net_pnl, return_pct, entry_fee, exit_fee, fees, holding_days, confidence, verdict, persona, is_win).

REQ-054-A2 (Event-driven):
> **When** 클라이언트가 `GET /api/portfolio`(포트폴리오 구성)를 호출하면, the 백엔드 **shall** 신규 `position_eval_snapshot` 테이블(최신 trading_day)에서 종목별 market_value(eval_amount)·current_price·unrealized_pnl 을 읽고, equity 총액과 결합하여 종목별 weight_pct(=market_value/NAV), cash_ratio, 집중도 지표(Herfindahl 지수, 상위 3종목 비중%), 그리고 `ticker_metadata` 조인으로 섹터별 비중을 반환한다(ADR-004·ADR-002 의존).

REQ-054-A9 (Event-driven) — reconcile writer (데이터 파운데이션, M1.5):
> **When** 트레이딩 루프의 일일 reconcile(SPEC-029, KIS inquire-balance)가 종목별 잔고를 가져오면, the reconcile 경로 **shall** 이미 응답에 포함된 종목별 평가값(eval_amount·current_price·pnl_amount·pnl_pct)을 `position_eval_snapshot(trading_day, ticker, qty, avg_cost, eval_price, eval_amount, unrealized_pnl)`에 upsert 하며, 트레이딩 **결정 로직은 변경하지 않는다**(이미 가져온 데이터를 저장만).

REQ-054-A3 (Event-driven):
> **When** 클라이언트가 `GET /api/pnl-daily`를 호출하면, the 백엔드 **shall** `edge` 라운드트립의 exit_date 기준 일별 실현손익과 누적 실현손익을, 그리고 가용 시 동일기간 KOSPI 상대(알파)를 반환하며, `period` 파라미터(daily|weekly|monthly)로 그룹핑을 지원한다.

REQ-054-A4 (Ubiquitous):
> The `/api/scorecard` 엔드포인트 **shall** `edge.analytics`에서 이미 계산된 `sortino` 값을 응답에 포함한다(현재 미노출 필드 노출만 추가, 신규 계산 금지).

REQ-054-A5 (Event-driven):
> **When** 클라이언트가 `GET /api/export/{dataset}.csv`(dataset ∈ {roundtrips, portfolio, pnl-daily})를 호출하면, the 백엔드 **shall** 해당 데이터셋을 `text/csv`(`Content-Disposition: attachment`)로 스트리밍 반환하며, 행 값은 동일 edge 코어/`position_eval_snapshot` 산출물에서 나온다(별도 계산 경로 금지).

REQ-054-A6 (Unwanted behavior):
> **If** 신규 엔드포인트가 라운드트립·실현손익·KPI 수치를 산출해야 한다면, **then** the 코드 **shall** 반드시 `edge` 모듈(roundtrips/analytics/benchmark)을 호출해야 하며, 대시보드 레이어에서 손익·수익률·알파 수식을 직접 구현해서는 안 된다.

REQ-054-A7 (Unwanted behavior) — 대시보드 읽기전용 불변 [HARD]:
> **If** 어떤 신규 또는 변경된 **대시보드** 코드(`src/trading/dashboard/`)가 실행되더라도, **then** the 코드 **shall** 어떤 테이블(`position_eval_snapshot`·`ticker_metadata` 포함)에도 INSERT/UPDATE/DELETE를 수행해서는 안 되며, 읽기전용 DSN(`ro_connection`) 경유로만 읽는다. 쓰기는 오직 트레이딩 루프(reconcile writer REQ-054-A9)와 `ticker_metadata` 로더에서만 발생한다. 이 경계(대시보드=읽기전용, 트레이딩 루프=유일한 writer)는 불변이다.

REQ-054-A8 (State-driven):
> **While** KOSPI 지수 데이터가 가용하지 않은 동안(`benchmark.available == False`), the `/api/pnl-daily`·`/api/scorecard` 알파 관련 필드 **shall** null 또는 `available:false` 플래그로 표기되어야 하며, 빈 패널이나 0 오기재로 보여서는 안 된다.

### 그룹 B — 라이트 테마 + 사이드바 셸 (M2)

REQ-054-B1 (Ubiquitous):
> The 대시보드 UI **shall** 밝은(라이트) 팔레트를 사용한다: 배경은 연회색 계열(#f6f8fa 계열), 카드 배경은 흰색, 강조색은 파랑(중립/정보)·초록(이익)·빨강(손실)로 한정한다. 다크 토글은 요구되지 않는다. (디자인 의도: 전문 자산운용 도구 톤 — 비정규 참고용.)

REQ-054-B2 (Ubiquitous):
> The `theme.ts` **shall** CSS 변수 기반 토큰(색·간격·반경·그림자)으로 리팩터되어 유지보수성을 확보하며, 모든 컴포넌트는 하드코딩 색 대신 토큰을 참조한다.

REQ-054-B3 (Ubiquitous):
> The 레이아웃 **shall** 상단 탭 네비게이션을 **좌측 사이드바 네비게이션**으로 교체하며, 뷰포트 폭 ≤768px에서 사이드바는 collapsible(햄버거 토글)로 접히고, 콘텐츠 그리드는 ≥1280px에서 다열(multi-column)로 배치된다. 모든 breakpoint에서 가로 스크롤(가로 오버플로)이 발생해서는 안 된다.

REQ-054-B4 (Ubiquitous):
> The 각 주요 패널 **shall** 기존 `ErrorBoundary` 패널별 격리를 유지하여, 한 패널의 런타임 오류가 전체 화면을 검게(black-screen) 만들지 않도록 한다.

### 그룹 C — 자산운용 4지표군 (M3 + M4)

REQ-054-C1 (Event-driven) — (a) 성과 요약 KPI 카드:
> **When** 운영자가 대시보드 개요를 열면, the UI **shall** KPI 카드로 총자산, 일일·누적 실현손익, 수익률%, MDD, 승률, 평균 손익비(avg win/loss), Sharpe, KOSPI 알파를 표시하며, 모든 값은 `/api/scorecard`+`/api/equity`+`/api/pnl-daily`(= edge 코어)에서 온다.

REQ-054-C2 (Event-driven) — (b) 포트폴리오 구성/집중도:
> **When** 운영자가 포트폴리오 뷰를 열면, the UI **shall** 종목별 비중 파이차트, 현금 비율, 집중도 지수(Herfindahl 및 상위 N 비중%), 섹터별 비중, 그리고 투자비중(=시가평가액/NAV)을 `/api/portfolio`에서 받아 표시한다. ("익스포저"는 투자비중으로 정의하며 weight_pct와 동일 개념 — 별도 모호 지표를 추가하지 않는다.)

REQ-054-C3 (Event-driven) — (c) 종목별 손익/보유 테이블:
> **When** 운영자가 보유 테이블을 열면, the UI **shall** `position_eval_snapshot` 기반으로 종목별 평가손익(unrealized_pnl), 수익률%, 평단(avg_cost)/현재가(eval_price), 평가금액(market_value), 비중(weight_pct)을 정렬 가능한 표로 표시한다(ADR-004 의존). 보유일수는 가용 시 표기한다.

REQ-054-C4 (Event-driven) — (d) 기간 손익 추이:
> **When** 운영자가 기간 손익 뷰를 열고 날짜 범위를 선택하면, the UI **shall** 일/주/월 실현손익 막대 + 누적 라인 + KOSPI 상대(알파)를 `/api/pnl-daily?period=`에서 받아 표시한다.

### 그룹 D — 라운드트립 거래원장 (M4)

REQ-054-D1 (Event-driven):
> **When** 운영자가 거래내역 뷰를 열면, the UI **shall** 1행 = 매수→매도 1왕복(라운드트립)으로, 진입가·청산가·실현손익·수익률%·수수료·보유기간·페르소나 귀속을 `/api/roundtrips`에서 받아 표시한다.

REQ-054-D2 (Optional/Where):
> **Where** 라운드트립 데이터가 페르소나 귀속을 포함하는 경우, the 거래원장 **shall** 진입 의사결정의 페르소나 이름을 행마다 표기한다(ADR-001의 edge 변경 의존).

### 그룹 E — 엔터프라이즈 테이블 기능 (M5)

REQ-054-E1 (Ubiquitous):
> The 모든 데이터 테이블(거래원장, 보유, 손익) **shall** 필터·정렬·검색·날짜범위 선택을 지원한다.

REQ-054-E2 (Event-driven):
> **When** 운영자가 CSV 내보내기를 클릭하면, the UI **shall** `/api/export/{dataset}.csv`를 호출하여 거래원장/보유/손익을 파일로 다운로드한다.

### 그룹 F — 안정성·데이터계약·검증 (M6)

REQ-054-F1 (Unwanted behavior):
> **If** 신규 엔드포인트가 추가되면, **then** 대응하는 TypeScript 타입이 `api/types.ts`에 정확히 존재해야 하며, 필드명/타입이 백엔드 JSON과 불일치해서는 안 된다(SPEC-050 black-screen 교훈: NUMERIC→문자열·필드명 불일치가 크래시 원인이었음).

REQ-054-F2 (State-driven):
> **While** 대시보드가 렌더링되는 동안, the 브라우저 콘솔 **shall** 에러 0건이어야 하며, 이는 Playwright 콘솔 클린 검증으로 확인한다.

REQ-054-F3 (Unwanted behavior):
> **If** `/api/status`가 mig033 미적용 환경에서 호출되더라도, **then** 엔드포인트 **shall** `cool_down_active=false` graceful 폴백을 유지하여 503을 반환해서는 안 된다(기존 폴백 보존·회귀 금지).

### 그룹 G — 섹터 분류 (확정 요구, ADR-002)

REQ-054-G1 (Event-driven):
> **When** 운영자가 포트폴리오 구성 뷰를 열면, the UI **shall** `position_eval_snapshot`과 `ticker_metadata`(ticker→sector) 조인 결과로 섹터별 비중 파이와 섹터별 손익을 표시한다. `ticker_metadata`에 매핑이 없는 종목은 "미분류(Unclassified)" 섹터로 집계하며 조용히 누락하지 않는다.

## 5. 마일스톤 → 요구 매핑

| 마일스톤 | 범위 | 요구 | 의존 |
|---|---|---|---|
| M1 | 공유코어 읽기 백엔드(roundtrips/pnl-daily/sortino 노출/CSV) + edge persona 확장 | A1, A3, A4, A5, A6, A8 | — |
| **M1.5 (데이터 파운데이션, 선행)** | 마이그레이션 035(`position_eval_snapshot`)·036(`ticker_metadata`) + reconcile writer(REQ-054-A9) + `ticker_metadata` 로더 | A9, A2(섹터/평가 데이터 공급) | M3 이전 필수 |
| M2 | 라이트 테마 + CSS 변수 토큰 + 사이드바 셸 + 패널 격리 | B1~B4 | — |
| M3 | KPI 카드 + 포트폴리오 구성(평가/섹터) | C1, C2, A2, A7 | **M1.5 선행** |
| M4 | 종목별 손익 테이블 + 기간 손익 추이 + 라운드트립 원장 | C3, C4, D1, D2 | C3는 M1.5 선행 |
| M5 | 엔터프라이즈 테이블 기능(필터/정렬/검색/기간) + CSV | E1, E2 | — |
| M6 | 안정성·데이터계약·503 보존·Playwright 콘솔/뷰포트 검증 | F1~F3 | — |
| G | 섹터 분해(파이·섹터손익) | G1 | M1.5 선행 |

의존성 요약: **M1.5(데이터 파운데이션) → M3/C3/G1**. 평가·섹터 데이터를 공급하는 마이그레이션과 reconcile writer가 종목별/포트폴리오/섹터 엔드포인트보다 먼저 완료되어야 한다.

## 6. 아키텍처 결정 (ADR)

### ADR-001 — 라운드트립 페르소나 귀속

문제: `RoundTrip`은 진입 의사결정의 `confidence`/`verdict`는 노출하지만 **persona 이름은 미노출**. `edge.roundtrips._FILL_SQL`은 `persona_decisions`를 조인하나 `pd.confidence`만 SELECT하고 `pd.persona`는 빼고 있음(`postmortem`은 별도로 persona를 다룸).

결정: REQ-054-D2 충족을 위해 `_FILL_SQL`에 `pd.persona`를 추가하고 `RoundTrip`에 `persona: str | None` 필드를 추가한다. 이는 edge 코어의 **최소 확장**(읽기 전용 SELECT 컬럼 1개 + 데이터클래스 필드 1개)이며 기존 손익 수식은 불변. edge의 단일원천 원칙을 깨지 않는다(대시보드가 persona를 재계산하지 않음).

리스크: edge 단위테스트의 `build_roundtrips` 입력 행 스키마 변경 → 기존 테스트 갱신 필요(회귀 점검 대상).

### ADR-002 — 섹터 마스터 테이블 (확정)

문제 (정정, D5): 스키마에 섹터 데이터가 "전혀 없다"는 표현은 과장이었다. 뉴스/이벤트 테이블에는 섹터 문자열이 존재한다. 부재한 것은 **보유 주식의 ticker→업종 마스터** 매핑이다.

결정: 신규 `ticker_metadata(ticker, sector, industry)` 조회 테이블 + 마이그레이션 036 을 도입하여 섹터 분해(REQ-054-G1)를 **확정 요구로 포함**한다(조건부 → 필수 승격).

섹터 데이터 적재(로더):
- 1차 출처는 KRX 업종분류(pykrx의 업종/섹터 조회 또는 KRX 업종분류 데이터). 종목코드→업종명 매핑을 `ticker_metadata`에 1회 적재 후 주기적(예: 주 1회 또는 신규 종목 편입 시) 갱신하는 로더 스크립트/잡을 둔다.
- 로더는 트레이딩 결정 경로와 분리된 별도 유틸이다(대시보드는 이 테이블을 읽기만, REQ-054-A7).
- 매핑 미존재 종목은 "미분류"로 폴백(REQ-054-G1).

[확인 필요] 본 결정(테이블·마이그레이션·로더 추가)은 범위 확장이므로 운영자 본인의 명시적 확인이 선행되어야 한다. 코디네이터 경유 동의는 권한이 없으며, 본 SPEC은 plan-auditor 권고 설계를 기재한 것이다.

### ADR-003 — CSV 내보내기 위치

결정: CSV는 프론트 클라이언트측 변환이 아니라 **백엔드 엔드포인트**(`/api/export/{dataset}.csv`)로 제공한다. 이유: (1) 단일원천 — CSV 행이 edge 코어/스냅샷과 동일 산출물을 보장, (2) 대용량/필터 일관성, (3) 컨테이너 python-only 환경에 적합. 프론트는 링크/다운로드 트리거만 담당.

### ADR-004 — 종목별 평가 스냅샷 테이블 + reconcile writer (확정, D1 해결)

문제: 종목별 시가평가액·현재가·평가손익이 읽기전용 DB에 부재(§2.2.1 검증). REQ-054-A2/C2/C3은 기존 데이터로 충족 불가.

결정:
- 신규 테이블 `position_eval_snapshot(trading_day, ticker, qty, avg_cost, eval_price, eval_amount, unrealized_pnl)` + 마이그레이션 035. (PK = trading_day+ticker upsert.)
- 데이터 출처: 매일 reconcile(SPEC-029, KIS inquire-balance)가 **이미** 종목별 평가값을 가져온다(`account.py:60-72`의 `eval_amount`/`current_price`(prpr)/`pnl_amount`(evlu_pfls_amt)/`pnl_pct`(evlu_pfls_rt)). 그 값을 신규 테이블에 upsert 하는 writer를 reconcile 경로에 추가한다(REQ-054-A9).
- 이것이 본 SPEC이 트레이딩 루프를 건드리는 **유일한 지점**이며, 결정 로직은 불변 — "이미 가져온 데이터를 저장만" 한다.
- 대시보드는 이 테이블을 **읽기만** 한다(`/api/portfolio`·종목별 P&L). [HARD 불변] 대시보드 자체는 여전히 읽기전용(REQ-054-A7). 쓰기는 reconcile에서만 발생.

대안 고려: (대안1) 대시보드가 호출 시점에 KIS를 직접 조회 → 기각(대시보드 읽기전용 불변 위배 + 유료 API 의존 + 장외 시간 실패). (대안2) equity 총액에서 종목별 역산 → 불가능(분해 정보 없음). 따라서 reconcile-writer가 유일하게 정합적.

[확인 필요] 신규 테이블·마이그레이션·reconcile 쓰기경로 추가는 범위 확장 결정이므로 운영자 본인의 명시적 확인이 선행되어야 한다.

## 7. 비목표 / 향후 작업 (Exclusions — What NOT to Build)

[HARD] 본 SPEC은 다음을 **구현하지 않는다**:

1. **피드백 루프(거래결정 자동 반영)** — 이 데이터로 라이브 트레이딩 결정을 조정(페르소나 재가중·레짐 게이팅·confidence 기반 사이징)하는 것은 **별도 후속 SPEC**이다. 본 SPEC은 엔터프라이즈 대시보드 + 공유코어 읽기 레이어까지만. 연기 사유(정직한 제약): 현재 실측 엣지가 음성(기대값 -14,840원/거래, 알파 -11%p, confidence Spearman -0.455)이고 표본이 n=8로 극소이므로, 어떤 자동 피드백도 표본수·유의성 게이트가 선행되어야 한다(본 SPEC에서 시기상조).
2. **손익/라운드트립/KPI 수식의 신규 구현** — 전부 `edge` 모듈에서 읽는다(REQ-054-A6 강제).
3. **트레이딩 상태 변경** — 대시보드는 읽기전용. 주문/포지션/halt 상태를 쓰지 않는다(REQ-054-A7).
4. **다크 테마 토글** — 운영자가 라이트 단일 팔레트로 확정(REQ-054-B1).
5. **LIVE 체결 mode 필터 확장** — `load_fill_rows`의 mode='paper' → live 확장은 LIVE 서비스 SPEC 소관(읽는 쪽 변경 불필요).
6. **실시간 스텝 진행 표시**(persona_step_progress 등) — SPEC-050에서 후속 분리된 항목, 본 SPEC에서도 범위 외.
7. **장중 실시간 종목 시세**(틱/분봉) — `position_eval_snapshot`은 reconcile 시점(일 1회+체결 후) 스냅샷이며, 실시간 시세 스트리밍은 범위 외(스냅샷 기준 시각을 UI에 명시).
8. **트레이딩 결정 로직 변경** — reconcile writer(REQ-054-A9)는 "이미 가져온 데이터 저장만"이며 결정/사이징/리스크 로직을 변경하지 않는다.

## 8. @MX 태그 대상

- `edge.roundtrips.build_roundtrips` / `compute_roundtrips` — 다수 호출자(report·queries·신규 엔드포인트 3개·postmortem). high fan_in → `@MX:ANCHOR`(불변 계약: FIFO 매칭·net_pnl 정의). ADR-001로 `persona` 필드 추가 시 ANCHOR 주석 갱신.
- 신규 `/api/roundtrips`·`/api/portfolio`·`/api/pnl-daily`·`/api/export` 핸들러 — `@MX:NOTE`(edge 코어/스냅샷 읽기 전용·재계산 금지 의도 명시).
- reconcile writer(REQ-054-A9, `position_eval_snapshot` upsert) — 트레이딩 루프가 대시보드 데이터를 공급하는 경계 지점, `@MX:ANCHOR`(불변 계약: 결정 로직 미변경·저장만) + 이미 가져온 KIS 응답만 사용함을 명시.
- `queries.fetch_system_status`의 cool_down graceful 폴백 — 기존 동작 보존 대상, `@MX:WARN` 후보(mig 의존성).

## 9. 의존 / 연관 SPEC

- [DELTA] SPEC-050(다크 4탭 대시보드) — 본 SPEC이 확장·교체.
- SPEC-044(측정 인프라) — `edge` 코어 검증·스코어카드 출처.
- SPEC-047(읽기전용 대시보드) — 읽기전용 DSN·status 엔드포인트 기반.
- SPEC-048(엣지 경화) — postmortem 4분류·persona 귀속 패턴 참고.
- SPEC-029(KIS fill sync/reconcile) — reconcile writer(REQ-054-A9)가 이 경로에 종목별 평가 스냅샷 upsert를 추가(ADR-004). inquire-balance 종목별 응답(`account.py`) 재사용.
- 후속(미생성) — 피드백 루프 SPEC(§7 #1).

## 10. 신규 마이그레이션 / 테이블 요약

| 마이그레이션 | 테이블 | 용도 | writer |
|---|---|---|---|
| 035 | `position_eval_snapshot(trading_day, ticker, qty, avg_cost, eval_price, eval_amount, unrealized_pnl)` | 종목별 평가 스냅샷(현재가·평가금액·평가손익) | reconcile(SPEC-029, REQ-054-A9) |
| 036 | `ticker_metadata(ticker, sector, industry)` | 종목→업종 마스터(섹터 분해) | 별도 로더 스크립트(ADR-002) |

대시보드는 두 테이블 모두 **읽기전용**. 최신 적용 마이그레이션은 034이므로 신규 번호는 035·036.
