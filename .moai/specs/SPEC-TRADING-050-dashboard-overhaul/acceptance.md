# SPEC-TRADING-050 — 인수 기준 (Acceptance Criteria)

Given/When/Then 시나리오. 마일스톤별 2개 이상. 모든 시나리오는 관측 전용(쓰기/제어 없음).
각 시나리오에 대응 REQ-050-N 태그 부여(D2 추적성).

## M1 — 백엔드 API 확장

### AC-M1-1 (신규 엔드포인트 계약 — story-clusters 관련성 필터) — REQ-050-2
- **Given** mock `ro_connection` 이 `portfolio_relevant=true` 1행과 `false` 1행을 반환할 때
- **When** `GET /api/story-clusters` 를 호출하면
- **Then** 응답 JSON에 `representative_title`/`sector`/`sentiment_dominant`/
  `portfolio_relevant`/`relevance_tickers` 필드가 포함되고, HTTP 200 이다.

### AC-M1-2 (decisions + risk_reviews LEFT JOIN) — REQ-050-3
- **Given** mock 이 `risk_reviews.decision_id` 로 매칭되는 verdict='REJECT' 행을 가질 때
- **When** `GET /api/decisions` 를 호출하면
- **Then** 각 결정 행에 `risk_verdict`/`risk_rationale` 가 포함되고, 매칭 없는 결정은
  해당 필드가 null 이며 행이 누락되지 않는다(LEFT JOIN).

### AC-M1-3 (postmortem/confidence 단일 접근 + stub FK 교정 — 회귀) — REQ-050-6, REQ-050-6a
- **Given** `persona_decisions.persona_run_id` 가 올바른 FK 인 스키마에서, SPEC-048의 죽은
  stub(`pd.run_id`)이 제거/대체된 상태일 때
- **When** `/api/postmortem` 및 `/api/confidence-analysis` 를 호출하면
- **Then** 대체 지연계산 함수가 `pd.persona_run_id` 로 조인되어 런타임 오류 없이 200 을
  반환하고, edge 순수 함수(`classify_decision_outcome`/`analyze`) 결과가 JSON 으로
  반환된다(트레이딩 로직 복제 없음).

### AC-M1-4 (postmortem/confidence 어댑터 + N일·캐시 — 부하 민감, 위험 R2) — REQ-050-7
- **Given** 최근 60일 분량의 결정 행이 있고 N=30(기본)일 때
- **When** `/api/postmortem` 을 짧은 간격으로 두 번 호출하면
- **Then** 첫 호출은 원시 행을 어댑터로 edge 도메인 객체로 변환해 계산하고 최근 30일만
  포함하며, 두 번째 호출은 캐시(TTL)에서 응답하여 DB 재질의가 발생하지 않는다.

### AC-M1-5 (status 확장 — halt 사유/cool_down/late_cycle) — REQ-050-4
- **Given** `system_state.halt_state=true`, `cool_down_active=true`,
  `late_cycle_defense_active=true` 이고 audit_log 에 최근 TRIP 행이 있을 때
- **When** `GET /api/status` 를 호출하면
- **Then** 응답에 halt 사유(audit_log 최근 TRIP)·`cool_down_active`·
  `late_cycle_defense_active`/`late_cycle_level` 이 함께 포함된다.

### AC-M1-6 (읽기 전용·민감 필드 redaction 범위) — REQ-050-1, REQ-050-8
- **Given** 어떤 신규/확장 엔드포인트든
- **When** 호출하면
- **Then** 응답에 제외 대상(자격증명·KIS `request`/`response` 페이로드·`kis_order_no`)이
  포함되지 않고, 포함 허용 대상(rationale·confidence·prob_*·verdict)은 정상 노출되며,
  쓰기/DDL 시도가 0 이다(읽기 전용 역할).

## M2 — 프론트엔드 기반

### AC-M2-1 (빌드 + FastAPI 서빙) — REQ-050-9, REQ-050-14
- **Given** React+Vite+TS 프로젝트가 스캐폴딩된 상태에서
- **When** `vite build` 후 FastAPI 를 기동하고 루트(`/`)에 접근하면
- **Then** 빌드된 SPA 가 로드되고(브라우저 스모크), 정적 자산이 200 으로 서빙된다.

### AC-M2-2 (폴링 자동 갱신 + 실패 비차단) — REQ-050-11, REQ-050-12
- **Given** 한 뷰가 5~15초 폴링 훅을 사용 중일 때
- **When** API 가 일시적으로 503 을 반환하면
- **Then** 마지막 정상 데이터가 유지되고, 비차단 오류 표시가 보이며, 다음 주기에 재시도가
  발생한다(테스트: 폴링 훅 단위 테스트).

### AC-M2-3 (API 타입 계약 일치 + 갱신 시각) — REQ-050-13
- **Given** 백엔드 엔드포인트의 응답 형태가 정의된 상태에서
- **When** 프론트 타입 테스트를 실행하고 뷰를 렌더링하면
- **Then** 각 엔드포인트 응답에 대응하는 TypeScript 타입이 존재해 컴파일 오류가 없고,
  각 뷰가 "마지막 갱신 시각"을 표시한다.

### AC-M2-4 (다크 테마 적용) — REQ-050-10
- **Given** SPA 가 로드된 상태에서
- **When** 임의의 뷰를 렌더링하면
- **Then** 다크 테마(어두운 배경 + 대비)가 적용되어 있다(컴포넌트 테스트: 테마 클래스/
  토큰 적용 확인).

## M3 — 의사결정 시각화

### AC-M3-1 (파이프라인 다이어그램 + 가드 상태) — REQ-050-15, REQ-050-16, REQ-050-18
- **Given** `/api/pipeline` 이 최신 사이클의 persona_runs 를 반환하고 `halt_state=true`
  일 때
- **When** 파이프라인 뷰를 렌더링하면
- **Then** macro→micro→decision→risk→portfolio→sizing 단계가 최신 사이클로 재구성되어
  표시되고, halt 상태와 사유(audit_log TRIP)가 상단에 명확히 표시된다.

### AC-M3-2 (결정 드릴다운) — REQ-050-17
- **Given** 결정 피드에 confidence·regime·risk verdict 가 있는 행이 있을 때
- **When** 사용자가 그 행을 클릭하면
- **Then** 상세 패널에 rationale·confidence·`regime_at_decision`·risk verdict/
  rationale·`trigger_context`·`response_json`(raw)가 표시된다.

## M4 — 자산 통계 차트

### AC-M4-1 (차트 렌더 + 인터랙션) — REQ-050-19, REQ-050-20
- **Given** `/api/equity`(drawdown 포함)·scorecard·postmortem·confidence 데이터가 로드된
  상태에서
- **When** 자산 통계 뷰를 렌더링하면
- **Then** 에쿼티 곡선·드로다운·수익 분포·누적 실현손익·confidence 산점도·postmortem
  4분류·페르소나 귀인 차트가 렌더되고, 호버 시 수치 툴팁이(해당 차트에서 줌/팬이)
  나타난다.

### AC-M4-2 (KOSPI 알파 graceful degrade) — REQ-050-21
- **Given** `benchmark_available=false`(지수 데이터 없음) 응답일 때
- **When** 알파 차트를 렌더링하면
- **Then** 알파 차트는 "데이터 없음"으로 표시되고, 나머지 차트는 정상 렌더된다(앱이
  깨지지 않음).

## M5 — 뉴스 인텔리전스 뷰

### AC-M5-1 (포트폴리오 관련 클러스터 우선 + 필터) — REQ-050-22, REQ-050-25
- **Given** `/api/story-clusters` 가 `portfolio_relevant` true/false 혼합을 반환할 때
- **When** "포트폴리오 관련만" 필터를 활성화하면
- **Then** `portfolio_relevant=true` 인 클러스터만 표시되고, 각 클러스터의 감성·대표
  제목·섹터·`relevance_tickers` 가 노출된다.

### AC-M5-2 (의사결정 연결 표시) — REQ-050-24
- **Given** 한 클러스터의 `relevance_tickers` 가 현재 보유종목/결정 종목과 겹칠 때
- **When** 뉴스 뷰를 렌더링하면
- **Then** 그 클러스터에 의사결정 연결(관련 종목 배지 등) 표시가 나타난다.

### AC-M5-3 (키워드 트렌드 + 개별 뉴스 표시) — REQ-050-23
- **Given** `/api/trends`(news_trends)·`/api/news`(news_articles + news_analysis)가
  데이터를 반환할 때
- **When** 뉴스 뷰를 렌더링하면
- **Then** 키워드 트렌드(mention_count·감성 분포)와 개별 뉴스(요약·임팩트·감성)가
  표시된다.

## 엣지 케이스 (Edge Cases)

- E1 (REQ-050-16): `persona_runs` 가 비어 있는 신규 환경 → 파이프라인 뷰가 "데이터 없음"
  안내(빈 배열 처리), 500 금지.
- E2 (REQ-050-19): `daily_equity_snapshot` 스냅샷 < 2개 → 시계열 지표(CAGR/MDD/Sharpe)
  null 안전 처리.
- E3 (REQ-050-11): 폴링 동시성 — 이전 요청 미완 시 중복 발사 방지(in-flight 가드).
- E4 (REQ-050-2): 매우 큰 limit 요청 → 서버측 상한(SPEC-047 패턴: `min(limit, 200)`) 유지.

## 추적성 매트릭스 (REQ ↔ AC 정방향 매핑, D2)

| REQ | 대응 AC |
|-----|---------|
| REQ-050-1 | AC-M1-6 |
| REQ-050-2 | AC-M1-1, E4 |
| REQ-050-3 | AC-M1-2 |
| REQ-050-4 | AC-M1-5 |
| REQ-050-5 | AC-M4-1(equity drawdown 소비) |
| REQ-050-6 | AC-M1-3 |
| REQ-050-6a | AC-M1-3 |
| REQ-050-7 | AC-M1-4 |
| REQ-050-8 | AC-M1-6 |
| REQ-050-9 | AC-M2-1 |
| REQ-050-10 | AC-M2-4 |
| REQ-050-11 | AC-M2-2, E3 |
| REQ-050-12 | AC-M2-2 |
| REQ-050-13 | AC-M2-3 |
| REQ-050-14 | AC-M2-1 |
| REQ-050-15 | AC-M3-1 |
| REQ-050-16 | AC-M3-1, E1 |
| REQ-050-17 | AC-M3-2 |
| REQ-050-18 | AC-M3-1 |
| REQ-050-19 | AC-M4-1, E2 |
| REQ-050-20 | AC-M4-1 |
| REQ-050-21 | AC-M4-2 |
| REQ-050-22 | AC-M5-1 |
| REQ-050-23 | AC-M5-3 |
| REQ-050-24 | AC-M5-2 |
| REQ-050-25 | AC-M5-1 |

## 품질 게이트 (Quality Gate / Definition of Done)

- [ ] 모든 EARS 요구사항(REQ-050-1~25, 6a 포함) 구현·테스트(추적성 매트릭스 전 항목 커버).
- [ ] 신규/확장 엔드포인트 API 계약 테스트 통과(mock `ro_connection`).
- [ ] postmortem/confidence 단일 지연계산 파이프라인(어댑터→edge 순수함수) 동작 + 캐시·
      N일 제한 검증(AC-M1-3, AC-M1-4).
- [ ] 프론트 컴포넌트/타입 테스트 통과 + `vite build` 성공 + FastAPI 서빙 스모크.
- [ ] 쓰기/DDL 시도 0, 민감 필드 redaction(제외=자격증명/KIS payload/kis_order_no) 검증.
- [ ] 기존 전체 테스트 스위트 0 회귀(특히 SPEC-047 엔드포인트 계약).
- [ ] Exclusions 준수(제어 없음·WebSocket 없음·엔진 미변경·공개 노출 없음).
- [ ] 도커 빌드 통합·정적 산출물 경로 문서화 완료.
- [ ] TRUST 5 통과(Tested/Readable/Unified/Secured/Trackable).
