# SPEC-TRADING-054 — 구현 계획 (plan.md)

본 문서는 WHAT/WHY가 아닌 HOW(구현 접근)를 다룬다. 시간 추정 없음, 우선순위 라벨만 사용. (v0.2.0: plan-auditor FAIL 반영 — M1.5 데이터 파운데이션 신설.)

## 기술 접근

### 단일원천 원칙 (전 마일스톤 관통)
모든 손익/라운드트립/KPI/알파 수치는 `trading.edge` 패키지에서 읽는다. 신규 수식 작성 금지(REQ-054-A6). 검증된 패턴 = `queries.py`의 `fetch_scorecard()`/`fetch_postmortem()`가 이미 `compute_roundtrips`→`from_result`→`benchmark.compute`→`scorecard.decide`를 호출하는 방식을 그대로 따른다. 종목별 평가/섹터는 edge가 아닌 신규 스냅샷·메타 테이블에서 읽되, 그 데이터의 writer는 트레이딩 루프(reconcile)·로더이지 대시보드가 아니다(읽기전용 불변).

### 데이터 파운데이션 (M1.5 — 종목별 평가·섹터, 검증된 부재 해소) [선행]
[CRITICAL] 종목별 시가평가액·현재가·평가손익은 읽기전용 DB에 **부재**(검증: holdings=원가만, equity=총액만, positions=avg_cost만, 시세 테이블 없음). 따라서:
- 마이그레이션 035: `position_eval_snapshot(trading_day, ticker, qty, avg_cost, eval_price, eval_amount, unrealized_pnl)`, PK=(trading_day, ticker) upsert.
- reconcile writer: SPEC-029 reconcile 경로(KIS inquire-balance, `account.py:60-72`가 이미 종목별 `eval_amount`/`current_price`(prpr)/`pnl_amount`(evlu_pfls_amt)/`pnl_pct`(evlu_pfls_rt)를 반환)에 스냅샷 upsert 1지점 추가. **결정 로직 미변경 — 저장만**(REQ-054-A9, `@MX:ANCHOR`).
- 마이그레이션 036: `ticker_metadata(ticker, sector, industry)` + KRX 업종 매핑 로더(pykrx/KRX 업종분류, 1회+주기 갱신, 트레이딩 경로와 분리된 별도 유틸).
- [HARD 경계] 대시보드는 두 테이블 읽기전용(`ro_connection`). 쓰기는 reconcile writer와 ticker_metadata 로더에서만.
- [확인 필요] 신규 테이블 2개·마이그레이션 2건·reconcile 쓰기경로 추가는 범위 확장 → 운영자 본인 확인 선행(코디네이터 경유 동의는 권한 없음).

### 백엔드 (FastAPI, `dashboard/app.py` + `queries.py`)
- 신규 엔드포인트는 `app.py`에 라우트를, 데이터 조립은 `queries.py`에 `fetch_*` 함수로 추가(기존 컨벤션 일치).
- 읽기전용 DSN(`ro_connection`) 경유 — 쓰기 불가(REQ-054-A7 구조적 보장).
- `/api/portfolio`·종목별 P&L은 `position_eval_snapshot`(최신 trading_day) + `ticker_metadata` 조인에서 읽음(M1.5 선행 필수).
- 503 패턴: 기존 핸들러처럼 예외 시 HTTPException(503). `cool_down_active` graceful 폴백은 절대 제거하지 않음(REQ-054-F3).
- CSV: `StreamingResponse` + `csv.writer`, `Content-Disposition: attachment`. 행 소스는 fetch 함수 재호출(중복 계산 경로 금지, ADR-003). dataset ∈ {roundtrips, portfolio, pnl-daily}.

### 프론트엔드 (React+Vite+TS+ECharts)
- `theme.ts`를 CSS 변수 토큰으로 리팩터 → `index.css`에 `:root { --bg, --card, --accent-info/profit/loss, --space-*, --radius-*, --shadow-* }` 정의, 컴포넌트는 토큰 참조(하드코딩 색 금지).
- 상단 탭 → 좌측 사이드바 셸: `App.tsx` 레이아웃 재구성. ≤768px 사이드바 collapsible(햄버거), ≥1280px 콘텐츠 다열 그리드. 가로 오버플로 0(REQ-054-B3).
- 신규 엔드포인트마다 `api/types.ts`에 대응 타입 + `api/client.ts` fetch 함수 추가(REQ-054-F1).
- 패널별 `ErrorBoundary` 격리 유지(REQ-054-B4).
- 빌드 산출물(`../static`) git 커밋(컨테이너 python-only).

### edge 코어 최소 확장 (ADR-001)
- `edge.roundtrips._FILL_SQL`에 `pd.persona` SELECT 추가.
- `RoundTrip` 데이터클래스에 `persona: str | None = None` 필드 추가, `build_roundtrips`에서 행→필드 매핑.
- 기존 edge 단위테스트의 입력 행 fixture 갱신(회귀 점검).

## 마일스톤 (우선순위·의존성 순서)

### M1 — 공유코어 읽기 백엔드 (Priority High)
- `/api/roundtrips`(A1), `/api/pnl-daily`(A3) 신규 fetch + 라우트.
- `/api/scorecard`에 `sortino` 노출(A4) — `fetch_scorecard()`에 1줄 추가.
- `/api/export/{dataset}.csv`(A5, ADR-003) — roundtrips/pnl-daily(portfolio는 M1.5 후).
- edge 코어 persona 확장(ADR-001, D2 의존).
- pytest: 각 fetch가 edge 호출(spy)·CSV 헤더/행·알파 unavailable 폴백(A8).

### M1.5 — 데이터 파운데이션 (Priority High, M3/C3/G1 선행 필수)
- 마이그레이션 035(`position_eval_snapshot`) + 036(`ticker_metadata`).
- reconcile writer(REQ-054-A9): SPEC-029 경로에 스냅샷 upsert, `@MX:ANCHOR`(결정 미변경).
- `ticker_metadata` 로더(ADR-002): KRX 업종 매핑 적재 스크립트.
- `/api/portfolio`(A2) fetch: 스냅샷+메타 조인(weight_pct·cash_ratio·Herfindahl·top3·섹터).
- pytest: writer가 결정 로직 미변경(저장만)·스냅샷 upsert 멱등·미분류 폴백(G1).

### M2 — 라이트 테마 + 사이드바 셸 (Priority High)
- `theme.ts` CSS 변수화(B2), 라이트 팔레트(B1).
- 사이드바 네비게이션 + 반응형(≤768px collapsible / ≥1280px 다열, B3).
- ErrorBoundary 격리 유지(B4).
- vitest: 사이드바 렌더·라우팅·breakpoint 스냅샷.

### M3 — KPI + 포트폴리오 (Priority High, **M1.5 선행**)
- KPI 카드(C1) ← scorecard+equity+pnl-daily.
- 포트폴리오 구성 뷰(C2, A7): 비중 파이·현금비율·Herfindahl·상위3·섹터 파이 ← /api/portfolio.

### M4 — 거래원장 + 기간 손익 (Priority High)
- 라운드트립 원장 테이블(D1, D2) ← /api/roundtrips.
- 종목별 손익 보유 테이블(C3) ← position_eval_snapshot(**M1.5 선행**).
- 기간 손익 추이 차트(C4) ← /api/pnl-daily?period=, 날짜범위.

### M5 — 엔터프라이즈 테이블 기능 + CSV (Priority Medium)
- 필터/정렬/검색/기간(E1) — 테이블 공통 훅/유틸.
- CSV 다운로드 트리거(E2) ← /api/export.

### M6 — 안정성·검증 (Priority High, 종결 게이트)
- 데이터계약 점검: 모든 신규 타입 ↔ 백엔드 JSON 일치(F1).
- Playwright 콘솔 클린(F2) — 각 뷰 순회 + 뷰포트 1440px·768px 양쪽 콘솔 에러/가로 오버플로 0.
- 503/cool_down 폴백 회귀(F3).

### G — 섹터 분해 (Priority Medium, **M1.5 선행**)
- 섹터별 비중 파이 + 섹터별 손익(G1) ← position_eval_snapshot ⋈ ticker_metadata.

## 리스크

- R1 (ADR-001): edge `RoundTrip` 필드 추가가 `report`/`queries`/`postmortem` 기존 호출을 깨지 않는지 — high fan_in. 회귀 테스트 필수(`@MX:ANCHOR`).
- R2 (F1/F2): SPEC-050 black-screen 재발 — NUMERIC→float 로더 보존 + 타입 일치 + Playwright 콘솔/뷰포트 검증으로 봉쇄.
- R3 (A8): KOSPI 지수 데이터 미스 시 알파 — null/available:false 강제, 빈 패널/0 오기재 금지.
- R4 (ADR-004): reconcile writer가 트레이딩 루프를 건드림 — 유일한 write 지점. [HARD] 결정/사이징/리스크 로직 미변경(저장만), 회귀 테스트로 결정 출력 동일성 검증. reconcile 실패 시 스냅샷만 누락하고 트레이딩은 불영향(쓰기 실패 격리).
- R5 (ADR-002): ticker_metadata 매핑 누락 종목 — "미분류" 폴백으로 조용한 누락 방지. 로더는 트레이딩 경로와 분리(실패해도 트레이딩 불영향).
- R6: 배경 에이전트 npm/pytest 실행 불가 — 빌드·테스트는 메인 세션 직접 검증(SPEC-050 교훈).
- R7 (스냅샷 신선도): `position_eval_snapshot`은 reconcile 시점(일 1회+체결 후) 기준 — 장중 실시간 아님. UI에 스냅샷 기준 시각 명시(§7 비목표 #7).

## 마이그레이션
- 035: `position_eval_snapshot`(종목별 평가 스냅샷, reconcile writer).
- 036: `ticker_metadata`(종목→업종 마스터, 로더).
- 최신 적용 마이그레이션은 034 → 신규 번호 035·036.
- 대시보드는 두 테이블 읽기전용.

## 검증 게이트 (배포 후)
- pytest/vitest 전체 통과, 회귀 0.
- reconcile writer 적용 후 트레이딩 결정 출력 불변 확인(R4).
- 마이그레이션 035/036 라이브 적용(`docker exec ... trading migrate`) — SPEC-050 미적용 교훈.
- Playwright 콘솔 에러 0 + 뷰포트 1440/768 가로 오버플로 0.
- `/api/export/*.csv` 행 수 = `/api/*` JSON 행 수 일치(단일원천 교차검증).
- 라이브 컨테이너에서 라이트 테마·사이드바·KPI 카드·섹터 파이 육안 확인.
