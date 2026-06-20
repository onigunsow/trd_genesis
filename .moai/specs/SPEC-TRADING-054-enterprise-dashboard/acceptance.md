# SPEC-TRADING-054 — 수락 기준 (acceptance.md)

Given-When-Then 시나리오. 모든 기준은 관측 가능(테스트 출력·HTTP 응답·콘솔·파일)해야 한다.

## AC-1 — /api/roundtrips 가 edge 코어를 읽는다 (REQ-054-A1, A6)
- **Given** paper 체결이 존재하는 DB,
- **When** `GET /api/roundtrips` 호출,
- **Then** 응답은 `RoundTrip[]` JSON이며 각 행에 ticker·entry_date·exit_date·qty·entry_price·exit_price·net_pnl·return_pct·entry_fee·exit_fee·fees·holding_days·confidence·verdict·persona·is_win 필드가 있고, pytest가 핸들러가 `edge.roundtrips.compute_roundtrips`를 호출함을(mock/spy) 검증한다. 대시보드 레이어에 손익 수식 코드가 없음을 grep로 확인.

## AC-2 — /api/portfolio 집중도·섹터 (REQ-054-A2, ADR-004)
- **Given** `position_eval_snapshot`(최신 trading_day) + 최신 equity 총액 + `ticker_metadata`,
- **When** `GET /api/portfolio`,
- **Then** 응답에 종목별 weight_pct(=market_value/NAV)·market_value·unrealized_pnl, cash_ratio, herfindahl, top3_pct, 섹터별 비중이 있고 weight_pct 합 ≈ 100%(현금 제외 기준 명시), pytest로 Herfindahl 계산 일치 + 미분류 폴백 검증. 종목별 평가값은 스냅샷 테이블에서만 옴(대시보드 재계산 없음).

## AC-2b — reconcile writer (REQ-054-A9, ADR-004) [M1.5]
- **Given** KIS inquire-balance 종목별 응답(eval_amount·current_price·pnl_amount 포함),
- **When** reconcile(SPEC-029) 실행,
- **Then** `position_eval_snapshot`에 trading_day+ticker upsert가 발생하고(멱등), pytest가 **트레이딩 결정/사이징/리스크 출력이 writer 추가 전과 동일**함을(저장만, 결정 미변경) 검증한다. reconcile 쓰기 실패가 트레이딩 흐름을 막지 않음(격리)도 확인.

## AC-3 — /api/pnl-daily 기간 그룹핑 + 알파 (REQ-054-A3, A8)
- **Given** 라운드트립 + (가용 시) KOSPI 종가,
- **When** `GET /api/pnl-daily?period=weekly`,
- **Then** 주별 실현손익·누적·KOSPI 상대가 반환된다.
- **And When** KOSPI 데이터 미스(`benchmark.available=False`),
- **Then** 알파 필드는 null 또는 `available:false`이며 200을 반환(빈 패널/0 오기재 아님).

## AC-4 — sortino 노출 (REQ-054-A4)
- **Given** 닫힌 라운드트립이 있는 상태,
- **When** `GET /api/scorecard`,
- **Then** 응답에 `sortino` 필드가 존재하고 값은 `edge.analytics` 산출과 일치(신규 계산 없음, 노출만).

## AC-5 — CSV 내보내기 단일원천 (REQ-054-A5, E2, ADR-003)
- **Given** 동일 시점 데이터,
- **When** `GET /api/export/roundtrips.csv`,
- **Then** `Content-Type: text/csv`·`Content-Disposition: attachment`이고, CSV 데이터 행 수 = `/api/roundtrips` JSON 행 수가 정확히 일치(교차검증).

## AC-6 — 대시보드 읽기전용 불변 (REQ-054-A7) [HARD]
- **Given** 신규/변경된 **대시보드** 코드(`src/trading/dashboard/`),
- **When** 코드 정적 점검,
- **Then** 어떤 테이블(`position_eval_snapshot`·`ticker_metadata` 포함)에도 INSERT/UPDATE/DELETE가 0건이며, 모든 신규 fetch가 `ro_connection`을 사용한다. 쓰기는 오직 reconcile writer(트레이딩 루프)와 ticker_metadata 로더에만 존재함을 확인.

## AC-7 — 라이트 테마 + CSS 변수 + 반응형 사이드바 (REQ-054-B1~B3, D3)
- **Given** 빌드된 대시보드,
- **When** 브라우저로 연다,
- **Then** 배경은 밝은 팔레트(#f6f8fa 계열), 좌측 사이드바 네비게이션이 보이고 상단 탭은 없으며, `theme.ts`/`index.css`가 CSS 변수 토큰을 정의하고 컴포넌트가 하드코딩 색 대신 토큰을 참조한다(grep 확인).
- **And When** Playwright 뷰포트를 768px로 설정,
- **Then** 사이드바가 collapsible(햄버거)로 접히고 가로 오버플로가 0이다.
- **And When** 뷰포트를 1440px(≥1280)로 설정,
- **Then** 콘텐츠 그리드가 다열로 배치되고 가로 오버플로가 0이다.

## AC-8 — KPI 카드 (REQ-054-C1)
- **Given** scorecard+equity+pnl-daily 응답,
- **When** 개요 뷰를 연다,
- **Then** 총자산·일일/누적 실현손익·수익률%·MDD·승률·평균 손익비·Sharpe·KOSPI 알파 카드가 모두 표시되고, 표시값이 해당 API 값과 일치한다.

## AC-9 — 포트폴리오 구성 뷰 (REQ-054-C2, D4)
- **Given** /api/portfolio 응답,
- **When** 포트폴리오 뷰를 연다,
- **Then** 종목별 비중 파이·현금비율·집중도 지수(Herfindahl·상위N)·섹터 파이가 표시되고, "익스포저"는 투자비중(=시가평가액/NAV)으로 weight_pct와 동일 개념으로만 표기된다(별도 모호 지표 없음).

## AC-10 — 종목별 손익 테이블 (REQ-054-C3, E1, ADR-004)
- **Given** `position_eval_snapshot` 기반 종목 데이터,
- **When** 보유 테이블에서 컬럼 헤더를 클릭,
- **Then** 평가손익(unrealized_pnl)·수익률%·평단/현재가(eval_price)·평가금액·비중이 표시되고 정렬이 동작한다(보유일수는 가용 시).

## AC-11 — 기간 손익 추이 + 날짜범위 (REQ-054-C4)
- **Given** pnl-daily 데이터,
- **When** 날짜범위를 선택하고 daily/weekly/monthly 전환,
- **Then** 실현손익 막대 + 누적 라인 + KOSPI 상대가 선택 기간에 맞게 갱신된다.

## AC-12 — 라운드트립 원장 + 페르소나 (REQ-054-D1, D2, ADR-001)
- **Given** /api/roundtrips 응답,
- **When** 거래내역 뷰를 연다,
- **Then** 1행 = 1왕복으로 진입가·청산가·실현손익·수익률%·수수료·보유기간·페르소나가 표시되고, persona 필드가 채워져 있다(edge `RoundTrip.persona` 확장 적용 확인).

## AC-13 — 엔터프라이즈 테이블 기능 (REQ-054-E1)
- **Given** 거래원장/보유/손익 테이블,
- **When** 검색어 입력·필터·정렬·날짜범위 적용,
- **Then** 각 기능이 모든 테이블에서 동작한다(vitest로 필터/정렬 로직 단위검증).

## AC-14 — 데이터계약 일치 (REQ-054-F1)
- **Given** 신규 엔드포인트 각각,
- **When** 타입 점검,
- **Then** `api/types.ts`에 대응 타입이 존재하고 필드명/타입이 백엔드 JSON과 일치(NUMERIC→number 로더 보존 확인).

## AC-15 — Playwright 콘솔 클린 (REQ-054-F2) [HARD 게이트]
- **Given** 배포된 대시보드,
- **When** Playwright로 모든 뷰(개요/포트폴리오/거래내역/손익/뉴스 등) 순회,
- **Then** 브라우저 콘솔 에러 0건(black-screen 없음, ErrorBoundary가 개별 패널만 격리).

## AC-16 — 503/cool_down 폴백 회귀 (REQ-054-F3)
- **Given** mig033 미적용(`cool_down_active` 컬럼 부재) 환경,
- **When** `GET /api/status`,
- **Then** 503이 아니라 200 + `cool_down_active=false` graceful 폴백을 반환(기존 동작 보존).

## AC-17 — 섹터 분해 (REQ-054-G1, ADR-002)
- **Given** `position_eval_snapshot` ⋈ `ticker_metadata`,
- **When** 포트폴리오 구성 뷰를 연다,
- **Then** 섹터별 비중 파이와 섹터별 손익이 표시되고, `ticker_metadata` 매핑이 없는 종목은 "미분류(Unclassified)"로 집계된다(조용한 누락 아님). pytest로 미분류 폴백 검증.

## Definition of Done
- [ ] M1·M1.5·M2~M6·G 전 요구의 AC 통과.
- [ ] pytest 전체 통과 + 회귀 0.
- [ ] vitest 전체 통과.
- [ ] Playwright 콘솔 에러 0(AC-15) + 뷰포트 768/1440 가로 오버플로 0(AC-7).
- [ ] CSV 행 수 = JSON 행 수 교차검증 통과(AC-5).
- [ ] 손익/라운드트립/KPI 수식이 edge 코어에만 존재(대시보드 레이어 grep 0건, REQ-054-A6).
- [ ] 대시보드 읽기전용 불변 확인(AC-6) — 신규 테이블에도 대시보드 write 0.
- [ ] reconcile writer가 트레이딩 결정 출력 불변(AC-2b).
- [ ] 마이그레이션 035/036 라이브 적용(`docker exec ... trading migrate`).
- [ ] 신규 테이블·마이그레이션·reconcile 쓰기경로 추가에 대한 운영자 본인 확인 기록([확인 필요] 게이트).
- [ ] 빌드 산출물 git 커밋(컨테이너 python-only).

## 품질 게이트 (TRUST 5)
- Tested: 신규 fetch·CSV·필터/정렬 단위테스트 + Playwright 콘솔.
- Readable: 한국어 주석, edge 읽기 의도 @MX:NOTE.
- Unified: 기존 dashboard fetch_* 컨벤션 일치.
- Secured: 읽기전용 DSN, 트레이딩 상태 미변경.
- Trackable: SPEC-TRADING-054 커밋 참조, @MX:ANCHOR(roundtrips).
