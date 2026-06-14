---
id: SPEC-TRADING-050
version: 0.2.0
status: draft
created: 2026-06-14
updated: 2026-06-14
author: oni
priority: high
issue_number: null
labels: [dashboard, frontend, observability, ui-ux, brownfield]
---

# SPEC-TRADING-050 — 전문 인터랙티브 대시보드 전면 개편 (Dashboard Overhaul)

## HISTORY

- v0.2.0 (2026-06-14): plan-auditor 1차 감사(0.68 FAIL) 결함 7건 반영(iteration 2).
  D4(BLOCKING): postmortem/confidence 접근 모순 해소 — 죽은/깨진 stub(`pd.run_id`)
  제거하고 "원시 DB 행 → 어댑터 → edge 도메인 객체 → 지연계산"의 단일 일관 구현으로
  통합(REQ-050-6 stub 제거·REQ-050-7 어댑터 요구 명시). D2: acceptance.md 전
  시나리오에 REQ 태그 부여. D3: REQ-050-4/-7/-23 인수 시나리오 추가 + -2/-5/-20 부분
  커버 완성. D1: REQ-050-8 (Unwanted)→(Ubiquitous) 정정. D5: 비테스트성 정상요구문
  (전문 테마·차트 라이브러리 선택)을 측정가능 기준화 또는 plan 디자인 노트로 이동.
  D6: 복합 REQ-050-6을 단일 책임으로 분할. D7: 민감필드 redaction 범위 명확화.
- v0.1.0 (2026-06-14): 최초 초안. 운영자가 현재 대시보드(251줄 정적 HTML, 차트 0개,
  텍스트 테이블 5개)에 강한 불만 → 의사결정 구조(페르소나 파이프라인)·"왜 이런 결과인지"·
  자산 통계 시각화·뉴스 인텔리전스 활용을 전문적·인터랙티브하게 표시하도록 전면 개편.
  운영자 확정: 프론트엔드 = React + Vite + TypeScript 빌드(Vite 정적 산출물을 기존
  FastAPI 가 서빙), 갱신 = 자동 폴링 5~15초(WebSocket 아님), 차트 = 전문 금융 차트
  라이브러리(expert-frontend 최종 선택). [DELTA] SPEC-047(읽기 전용 대시보드 기반)을
  확장한다.

---

## 개요 (Overview)

[DELTA] 본 SPEC은 SPEC-047(읽기 전용 모니터링 대시보드)이 만든 **읽기 계층**을 확장하여
정적 HTML을 **전문적·인터랙티브한 React 단일 페이지 앱(SPA)** 으로 교체한다. 핵심 목표는
운영자가 다음 네 가지를 한눈에·깊이 있게 볼 수 있게 하는 것이다:

1. **의사결정 구조의 가시화** — 페르소나 파이프라인(macro → micro → decision → risk →
   portfolio → sizing)이 "지금/최신 사이클"에서 어떤 흐름으로 결론에 도달했는지, 그리고
   "왜 이런 결과인지"(근거·confidence·regime·리스크 verdict)를 드릴다운으로.
2. **자산의 통계적 변화** — 에쿼티 곡선·드로다운·수익 분포·KOSPI 대비 알파·누적 실현손익·
   confidence-수익 상관·postmortem 4분류 등 전문 금융 차트로.
3. **뉴스 인텔리전스 활용** — 뉴스 수집 → 정제(요약·감성·임팩트·키워드 트렌드·스토리
   클러스터)가 어떻게 의사결정에 연결되는지(포트폴리오 관련성 필터 포함).
4. **인터랙티브·전문 인터페이스** — 다크 금융 테마, 폴링 자동 갱신, 차트 호버/줌, 결정 행
   클릭 시 상세 패널.

[HARD] 본 SPEC은 **관측(observability) 전용**이다. 트레이딩 엔진(orchestrator)의 write
경로·페르소나 로직·엣지 계산을 변경하지 않는다. 데이터는 대부분 이미 Postgres에 존재하므로
작업의 본질은 (a) **읽기 API 확장**과 (b) **시각화 프론트엔드 신규 구축**이다.

## 배경 / 근거 (Context — Explore 검증 완료, file:line)

코드베이스 조사로 확인한 사실(파일·행 근거):

- **현재 대시보드**(SPEC-047 산출물):
  - `src/trading/dashboard/app.py` — FastAPI 7개 엔드포인트(`/`, `/health`,
    `/api/status`, `/api/decisions`, `/api/orders`, `/api/holdings`, `/api/equity`,
    `/api/scorecard`). 정적 HTML을 `index()`(app.py:43-49)로 서빙.
  - `src/trading/dashboard/queries.py` — `ro_connection` 통한 읽기 전용 쿼리.
    SPEC-048이 추가한 `fetch_postmortem_distribution`/`fetch_calibration_scores`
    (queries.py:204-263)는 **`pd.run_id` 조인 컬럼을 사용 — 버그**(실제 FK는
    `persona_decisions.persona_run_id`, 004_personas.sql:29). M1에서 교정 필요.
  - `src/trading/dashboard/db.py` — `dashboard_ro` 역할 DSN(SELECT 전용, mig 032).
  - `src/trading/dashboard/static/index.html` — 251줄 정적 HTML, **차트 0개**.
  - `compose.yaml:147-165` — `dashboard-api` 서비스가 이미 `uvicorn
    trading.dashboard.app:app --port 8080` 으로 기동. Grafana 서비스도 프로비저닝됨
    (compose.yaml:186-212).
- **파이프라인 데이터**(이미 존재):
  - `persona_runs`(004): `persona_name`/`cycle_kind`/`trigger_context`/
    `response_json`/`input_tokens`/`output_tokens`/`latency_ms`,
    `regime_at_decision`(024).
  - `persona_decisions`(004 + 033): `persona_run_id`(FK)/`ticker`/`side`/`qty`/
    `rationale`/`confidence`/`prob_bull`/`prob_base`/`prob_bear`.
  - `risk_reviews`(004): `decision_id`(FK)/`verdict`(APPROVE|HOLD|REJECT)/`rationale`/
    `code_rules_passed`/`raw`. → `/api/decisions` 에 LEFT JOIN 가능.
  - `portfolio_adjustments`(005): `qty_original`/`qty_adjusted`/`rationale`.
- **현재 상태 데이터**(이미 존재):
  - `system_state`: `halt_state`/`current_regime`/`current_risk_appetite`/
    `late_cycle_defense_active`/`late_cycle_level`(025)/`cool_down_active`(033).
  - `cool_down_events`(033), `late_cycle_events`(025), `audit_log`(halt 사유 TRIP).
- **자산 통계**(이미 존재 / 순수 함수):
  - `daily_equity_snapshot`(026): `total_assets`/`stock_eval`/`cash`/`unrealized_pnl`/
    `realized_pnl_cum`.
  - `edge/analytics.py`: `equity_curve`/MDD/Sharpe/CAGR/win_rate/profit_factor/
    expectancy. `edge/benchmark.py`: `alpha_pct` vs KOSPI(`kospi_closes`).
  - `edge/confidence.py`: confidence-수익 버킷 + Pearson/Spearman(`analyze`).
  - `edge/postmortem.py`: TP/FP/REGIME_MISMATCH/MISSED 분류 + 페르소나 귀인
    (`classify_decision_outcome`, 순수 함수).
  - `edge/scorecard.py`: verdict/grade.
- **뉴스 인텔리전스**(이미 존재):
  - `news_articles`(014): `title`/`url`/`summary`/`source_name`/`sector`/
    `published_at`.
  - `news_analysis`(016): `summary_2line`/`impact_score`(1-5)/`sentiment`/`keywords`.
  - `story_clusters`(016): `representative_title`/`sector`/`sentiment_dominant`/
    **`portfolio_relevant`**/**`relevance_tickers`**.
  - `news_trends`(016): `keyword`/`mention_count`/`sentiment_positive|neutral|
    negative`/`sentiment_avg`.
- **DB 접근**: `dashboard_ro` 역할은 mig 032 default privileges로 전 테이블 SELECT 가능.
  본 SPEC의 신규/확장 쿼리는 모두 이 읽기 범위 내.

## 가정 (Assumptions)

- A1: 프론트엔드는 React + Vite + TypeScript로 빌드하며, Vite 빌드 산출물(정적 JS/CSS)을
  **기존 FastAPI**(`dashboard/app.py`)가 서빙한다. 새 웹 서버를 추가하지 않는다.
- A2: 갱신은 자동 폴링(5~15초)으로 충분하다(시스템은 5/15분 사이클의 저빈도). WebSocket/
  SSE는 명시적으로 범위에서 제외한다.
- A3: 자산곡선/스코어카드 등 일부 지표는 일 1회 갱신(장마감 스냅샷)이라 실시간이 아니다 —
  UI는 "마지막 갱신 시각"을 표시한다.
- A4: 접근은 SPEC-047이 확립한 Tailscale VPN 전용 경계 안에서만 이루어진다(공개 인터넷
  노출 없음). 본 SPEC은 접근 경계를 변경하지 않는다.
- A5: postmortem/confidence/scorecard 는 read 시점에 순수 함수로 **지연 계산**한다(스키마
  변경 없음). 성능 위해 최근 N일 제한·서버측 캐시를 허용한다.
- A6: KOSPI 상대수익(알파) 계산은 `ohlcv`의 지수 종가 데이터 가용성에 의존한다(없으면
  `benchmark_available=false`로 graceful degrade — plan 위험 참조).
- A7: 운영자는 CLI 초심자다 → run 단계의 빌드·도커 통합 절차는 단계별로 풀어 쓴다.

## 요구사항 (EARS Requirements)

### M1 — 백엔드 API 확장 (Backend API extension)

- REQ-050-1 (Ubiquitous): 대시보드 API는 모든 신규/확장 엔드포인트에서 SPEC-047의 **읽기
  전용 역할(`dashboard_ro`)** 연결(`ro_connection`)만 사용 **shall**한다(INSERT/UPDATE/
  DELETE/DDL 불가).
- REQ-050-2 (Ubiquitous): the system **shall** 다음 신규 읽기 엔드포인트를 제공한다 —
  `GET /api/news`(news_articles + news_analysis 조인), `GET /api/story-clusters`
  (`portfolio_relevant`/`relevance_tickers` 포함), `GET /api/trends`(news_trends),
  `GET /api/postmortem`(최근 N일, 지연 분류), `GET /api/confidence-analysis`
  (confidence 버킷 + Pearson/Spearman), `GET /api/pipeline`(최신 사이클의 persona_runs
  재구성). 각 엔드포인트는 HTTP 200 + 정의된 JSON 형태를 반환 **shall**한다.
- REQ-050-3 (Event-Driven): **When** 클라이언트가 `GET /api/decisions` 를 호출하면, the
  system **shall** `persona_decisions` 에 `risk_reviews`(verdict/rationale)를
  `decision_id` 로 LEFT JOIN 하여 반환한다(매칭 없는 결정은 verdict/rationale 가 null
  이며 행이 누락되지 않는다).
- REQ-050-4 (Event-Driven): **When** 클라이언트가 `GET /api/status` 를 호출하면, the
  system **shall** halt 사유(audit_log 최근 TRIP) + `cool_down_active` +
  `late_cycle_defense_active`/`late_cycle_level` 을 함께 반환한다.
- REQ-050-5 (Event-Driven): **When** 클라이언트가 `GET /api/equity` 를 호출하면, the
  system **shall** 일별 스냅샷에 더해 drawdown(러닝 맥스 대비 낙폭) 곡선을 함께 반환한다.

  **[D4 통합 — postmortem/confidence 단일 접근]** `/api/postmortem` 과
  `/api/confidence-analysis` 는 **하나의 일관된 구현**을 가진다. SPEC-048이 남긴 죽은/
  깨진 stub(`fetch_postmortem_distribution`/`fetch_calibration_scores`, queries.py:204-263,
  엔드포인트 미연결·`pd.run_id` 조인 오류·mocked 테스트만 참조)을 **제거/대체**하고,
  "원시 DB 행(`ro_connection` SELECT) → 어댑터(raw rows → edge 도메인 객체) →
  edge 순수 함수(`postmortem.classify_decision_outcome()` / `confidence.analyze()`) →
  JSON" 의 **지연계산 쿼리 함수**로 통합한다. REQ-050-6 과 REQ-050-7 은 이 단일 구현을
  가리킨다.

- REQ-050-6 (Ubiquitous): the system **shall** SPEC-048의 죽은/깨진 stub
  (`fetch_postmortem_distribution`/`fetch_calibration_scores`)을 제거하고, 이를
  대체하는 postmortem/confidence 지연계산 쿼리 함수가 올바른 FK
  (`persona_decisions.persona_run_id`, `pd.run_id` 아님)로 `persona_runs` 를 조인
  **shall**한다. (단일 책임: stub 제거·FK 교정)
- REQ-050-6a (Ubiquitous): 모든 신규 쿼리는 기존 `edge/`·`db`·`queries.py` 코드를 재사용
  **shall**하며 트레이딩 도메인 로직을 복제하지 않는다. (단일 책임: 재사용)
- REQ-050-7 (State-Driven): **While** `/api/postmortem`·`/api/confidence-analysis` 가
  지연 계산되는 동안, the system **shall** (a) 원시 DB 행을 edge 순수 함수가 소비하는
  도메인 객체로 변환하는 **어댑터**를 거치고, (b) 조회 범위를 최근 N일로 제한하며(기본
  30일, 구성 가능), (c) 서버측 캐시(TTL)를 적용하여 폴링 부하를 억제 **shall**한다.
  (위험 R2 대응)
- REQ-050-8 (Ubiquitous): the system **shall** 모든 API 응답에서 민감 필드를 redaction
  **shall**한다. **제외(redact)** = 자격증명·KIS 요청/응답 페이로드(`request`/`response`)·
  `kis_order_no`. **포함(노출 허용)** = LLM rationale·confidence·prob_bull/base/bear·
  verdict 등 의사결정 근거. (SPEC-047 `_SENSITIVE_FIELDS` 정책 계승·확장; D7 범위 명확화)

### M2 — 프론트엔드 기반 (Frontend foundation: React + Vite + TS)

- REQ-050-9 (Ubiquitous): the system **shall** React + Vite + TypeScript 프로젝트를
  스캐폴딩하고, `vite build` 산출물(정적 자산)을 **기존 FastAPI** 가 서빙한다(현
  `index()` 라우트가 빌드된 SPA 엔트리를 반환).
- REQ-050-10 (Ubiquitous): 프론트엔드는 다크 테마(어두운 배경 + 충분한 대비)를 적용하고,
  데이터 시각화는 **인터랙티브 차트 컴포넌트**(호버 툴팁 + 해당 차트에서 줌/팬 — 측정
  기준은 M4 AC)로 렌더 **shall**한다. (차트 라이브러리 구체 선택은 plan 디자인 노트 참조 —
  정상 REQ에서 제외; D5)
- REQ-050-11 (State-Driven): **While** 페이지가 열려 있는 동안, the system **shall** 각
  뷰의 API 엔드포인트를 5~15초 간격으로 자동 폴링하여 표시 데이터를 갱신한다(공용 폴링 훅).
- REQ-050-12 (Event-Driven): **When** 폴링 요청이 실패하면(API 503 등), the system
  **shall** 마지막 정상 데이터를 유지한 채 비차단(non-blocking) 오류 표시를 보여주고 다음
  주기에 재시도한다.
- REQ-050-13 (Ubiquitous): the system **shall** 모든 API 응답에 대한 TypeScript 타입을
  정의하고, 각 뷰가 "마지막 갱신 시각"을 표시한다.
- REQ-050-14 (Ubiquitous): the system **shall** 도커 빌드 단계(멀티스테이지: Node 빌드 →
  정적 산출물 복사)와 FastAPI 서빙 통합을 운영 문서로 기록한다.

### M3 — 의사결정 시각화 (Decision visualization)

- REQ-050-15 (Ubiquitous): the system **shall** 페르소나 파이프라인 다이어그램(macro →
  micro → decision → risk → portfolio → sizing)을 표시하고, 각 단계의 리스크 가드 상태
  (halt/cool_down/late_cycle)를 시각적으로 나타낸다.
- REQ-050-16 (Event-Driven): **When** `/api/pipeline` 이 최신 사이클 데이터를 반환하면,
  the system **shall** 그 사이클의 `persona_runs` 를 단계별로 재구성하여 "현재/최신
  의사결정 과정"으로 표현한다.
- REQ-050-17 (Event-Driven): **When** 사용자가 결정 행을 클릭하면, the system **shall**
  상세 드릴다운 패널에 rationale·confidence·`regime_at_decision`·리스크 verdict/
  rationale·`trigger_context`·`response_json`(raw)을 표시한다.
- REQ-050-18 (State-Driven): **While** `system_state.halt_state` 가 true 인 동안, the
  system **shall** 파이프라인 뷰 상단에 halt 상태와 그 사유(audit_log TRIP)를 명확히
  표시한다.

### M4 — 자산 통계 차트 (Asset statistics charts)

- REQ-050-19 (Ubiquitous): the system **shall** 다음 차트를 렌더링한다 — 에쿼티 곡선,
  드로다운 곡선, 수익 분포(히스토그램), KOSPI 대비 알파, 누적 실현손익,
  confidence-수익 산점도, postmortem 4분류(TP/FP/REGIME_MISMATCH/MISSED) 분포,
  페르소나별 귀인.
- REQ-050-20 (Event-Driven): **When** 차트 데이터가 로드되면, the system **shall**
  호버 시 수치 툴팁과(해당 차트에서) 줌/팬 인터랙션을 제공한다.
- REQ-050-21 (Unwanted): **If** KOSPI 지수 데이터가 없어 알파를 계산할 수 없으면, **then**
  the system **shall** 해당 차트를 "데이터 없음"으로 graceful 하게 표시하고 다른 차트는
  정상 렌더링한다(`benchmark_available=false` 처리).

### M5 — 뉴스 인텔리전스 뷰 (News intelligence view)

- REQ-050-22 (Ubiquitous): the system **shall** 포트폴리오 관련 스토리 클러스터
  (`portfolio_relevant=true`)를 우선 표시하고, 각 클러스터의 감성(`sentiment_dominant`)·
  대표 제목·섹터·`relevance_tickers` 를 노출한다.
- REQ-050-23 (Ubiquitous): the system **shall** 키워드 트렌드(`news_trends`:
  mention_count·감성 분포)와 개별 뉴스(요약·임팩트·감성)를 표시한다.
- REQ-050-24 (Event-Driven): **When** 스토리 클러스터의 `relevance_tickers` 가 현재
  보유종목/결정 종목과 겹치면, the system **shall** 그 클러스터가 의사결정과 연결됨을
  시각적으로 표시한다(예: 관련 종목 배지).
- REQ-050-25 (State-Driven): **While** 사용자가 "포트폴리오 관련만" 필터를 활성화한
  동안, the system **shall** `portfolio_relevant=true` 인 클러스터만 표시한다.

## 비기능 요구사항 (Non-Functional)

- NFR-1 (관측 전용): 트레이딩 엔진 write 경로·페르소나·엣지 계산 미변경. 새 지표 계산
  추가 없음(기존 산출물 표시 + 지연 read-side 분류만).
- NFR-2 (보안): SPEC-047의 Tailscale VPN 전용 경계·읽기 전용 DB 역할·민감 필드 제외 정책
  계승. 본 SPEC은 접근 경계를 약화하지 않는다.
- NFR-3 (성능): 폴링 간격 5~15초. postmortem/confidence 지연 계산은 최근 N일 제한 +
  서버측 캐시로 폴링 부하를 억제.
- NFR-4 (회귀 0): 기존 테스트 회귀 0. 기존 7개 엔드포인트의 계약은 깨지 않고 확장만 한다
  (`/api/decisions`/`/api/status`/`/api/equity` 는 필드 추가만, 제거 없음).
- NFR-5 (재사용): 쿼리는 `edge/`·`db`·`queries.py` 재사용. 트레이딩 로직 복제 금지.
- NFR-6 (테스트): 프론트는 컴포넌트/타입 테스트, 백엔드는 API 계약 테스트(mock
  `ro_connection`).

## Exclusions (What NOT to Build)

- EXC-1: **제어 액션 없음.** halt/resume, 주문 제출/취소, 모드/설정 변경 등 어떤 쓰기/제어
  경로도 만들지 않는다. 제어는 기존 CLI/Telegram 에 그대로 둔다.
- EXC-2: **WebSocket/SSE 미구현.** 실시간 푸시는 폴링으로 대체하며 명시적으로 연기한다.
- EXC-3: **실시간 "현재 진행중 스텝" 추적 미포함(MVP).** 엔진 write가 필요한
  `persona_step_progress`(mig 034 예약)는 **선택적·후속 마일스톤**으로 분리한다. MVP는
  최신 사이클 `persona_runs` 재구성으로 "현재 의사결정 과정"을 표현한다.
- EXC-4: **트레이딩 코어·엣지 계산 미변경.** 페르소나/오케스트레이터/리스크/엣지 로직을
  수정하지 않으며, 새 데이터 산출이나 새 지표 정의를 추가하지 않는다(표시·지연 분류만).
- EXC-5: **공개 인터넷 노출 없음.** 리버스 프록시 공개 도메인·포트포워딩·터널 등 어떤 공개
  접근 경로도 구성하지 않는다(SPEC-047 경계 유지).
- EXC-6: **인증·사용자 관리 신규 구축 없음.** 접근 통제는 Tailscale 경계에 의존한다.
- EXC-7: **모바일 네이티브 앱 없음.** 브라우저용 React SPA(반응형)만.

## 정직성 고지 (Honesty)

- 본 SPEC은 **관측 전용**이다 — 트레이딩 동작·수익률·엣지를 바꾸지 않고 가시성만 높인다.
- 위험은 "새 계산"이 아니라 "노출 표면"과 "read-side 지연 계산 비용"에 있다 — 읽기 전용
  역할·VPN 경계·캐시·최근 N일 제한이 핵심 통제다.
- "현재 의사결정 과정"은 MVP에서 **최신 사이클 재구성**으로 근사한다 — 진짜 실시간 스텝
  추적(엔진 write)은 후속 마일스톤으로 정직하게 분리했다.
- KOSPI 알파는 지수 데이터 가용성에 의존하므로 없을 때 graceful degrade 한다(거짓 수치
  금지).

## 관련 SPEC

- SPEC-TRADING-047 (monitoring-dashboard): [DELTA] 본 SPEC이 확장하는 읽기 계층·
  `dashboard_ro` 역할·Tailscale 경계·FastAPI 서빙 기반.
- SPEC-TRADING-048 (edge-hardening): `prob_bull/base/bear`(mig 033) + postmortem/
  confidence read-side 쿼리 stub(본 SPEC M1에서 FK 버그 교정).
- SPEC-TRADING-044 (measurement-infrastructure): 스코어카드/KOSPI 알파/walk-forward
  산출(M4에서 표시).
- SPEC-TRADING-042 (broker-truth-ledger): `daily_equity_snapshot`/`orders`/`fills`
  진실원장(자산 차트 소스).
- SPEC-016 / 024 / 025 / 035 / 036: `system_state` 의 regime/late_cycle/cool_down 컬럼
  (상태·파이프라인 가드 소스).
