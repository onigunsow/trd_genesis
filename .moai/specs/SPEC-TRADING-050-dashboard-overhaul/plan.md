# SPEC-TRADING-050 — 구현 계획 (Plan)

## 개발 방식

- `development_mode = tdd` (RED → GREEN → REFACTOR). Brownfield: 기존 코드 이해 후
  실패 테스트 작성.
- 백엔드: API 계약 테스트(mock `ro_connection`) 선행. 프론트: 컴포넌트/타입 테스트 선행.
- [HARD] 기존 테스트 회귀 0. 기존 7개 엔드포인트 계약은 필드 추가만(제거 금지).

## 기술 접근 (Technical Approach)

### 백엔드 (M1)
- 신규 쿼리는 `src/trading/dashboard/queries.py` 에 `ro_connection` 으로 추가.
- 신규 엔드포인트는 `src/trading/dashboard/app.py` 에 GET-only 로 추가.
- 페르소나 파이프라인 쿼리는 올바른 FK `persona_decisions.persona_run_id` 사용
  (SPEC-048 stub의 `pd.run_id` 교정 포함).
- **[D4 단일 접근 — postmortem/confidence]** SPEC-048이 남긴 죽은/깨진 stub
  (`fetch_postmortem_distribution`/`fetch_calibration_scores`, queries.py:204-263)을
  제거/대체한다. 대체 구현은 다음 단일 파이프라인을 따른다:
  1. `ro_connection` 으로 원시 DB 행 SELECT(올바른 FK 조인).
  2. **어댑터**: raw rows → `edge/postmortem.py`·`edge/confidence.py` 순수 함수가
     소비하는 도메인 객체(예: RoundTrip/Decision 형태)로 변환.
  3. `postmortem.classify_decision_outcome()` / `confidence.analyze()` 호출(지연 계산).
  4. 결과를 JSON 직렬화.
  - 두 엔드포인트는 이 한 구현을 공유. REQ-050-6(stub 제거·FK 교정)·REQ-050-6a(재사용)·
    REQ-050-7(어댑터·N일·캐시)이 모두 이 구현을 가리킨다.
- 서버측 캐시(예: TTL 메모리 캐시) + 최근 N일(기본 30, 구성 가능) 제한으로 폴링 부하 억제
  (위험 R2).
- 민감 필드 redaction: **제외** = 자격증명·KIS request/response·`kis_order_no`(SPEC-047
  `_SENSITIVE_FIELDS` 계승). **포함** = LLM rationale·confidence·prob_*·verdict 등 근거.

### 프론트엔드 (M2~M5)
- `dashboard/frontend/` (또는 합의된 경로)에 React + Vite + TS 스캐폴딩.
- `vite build` → 정적 산출물을 기존 FastAPI 가 서빙(현 `static/` 마운트 + `index()`
  라우트가 빌드된 SPA 엔트리 반환). expert-frontend가 산출물 경로·라우트 최종 결정.
- 공용 폴링 훅(5~15초, 실패 시 마지막 데이터 유지·비차단 오류).
- 모든 API 응답 TypeScript 타입 정의(백엔드 계약과 일치).
- 도커: 멀티스테이지(Node 빌드 → 정적 복사) 또는 빌드 산출물 커밋 — expert-devops 자문.

### 디자인 노트 (Design Notes — 정상 REQ 아님; D5)
- **차트 라이브러리 선택**(REQ 아님, 구현 결정): expert-frontend 가 최종 선택. 후보 —
  ECharts/Recharts(일반 차트), 가격류는 lightweight-charts 고려. 선택 기준은 REQ-050-20
  의 측정 가능 인터랙션(호버 툴팁·줌/팬)을 만족하는지.
- **다크 테마**: 어두운 배경 + 충분한 명도 대비(전문 금융 대시보드 관례). 측정 기준은
  REQ-050-10(다크 테마 적용)·M4 AC(인터랙션).

## 마일스톤 (Milestones, 우선순위 기반)

### M1 — 백엔드 API 확장 (Priority High)
- 신규 엔드포인트: `/api/news`, `/api/story-clusters`, `/api/trends`, `/api/postmortem`,
  `/api/confidence-analysis`, `/api/pipeline`.
- 확장: `/api/decisions`(+risk_reviews LEFT JOIN), `/api/status`(+halt 사유/cool_down/
  late_cycle), `/api/equity`(+drawdown 곡선).
- **[D4]** SPEC-048 죽은/깨진 stub 제거/대체 → postmortem/confidence 단일 지연계산
  파이프라인(원시 행 → 어댑터 → edge 순수함수 → JSON). FK 교정·캐시·최근 N일 제한 포함.
- 산출: 확장된 `queries.py`·`app.py`, API 계약 테스트.

### M2 — 프론트엔드 기반 (Priority High; M1 이후)
- React+Vite+TS 스캐폴딩, FastAPI 서빙 통합, 공용 폴링 훅, 다크 테마, API 타입,
  도커 빌드 문서. 산출: 빌드되는 SPA 셸 + 폴링/타입 인프라.

### M3 — 의사결정 시각화 (Priority High; M2 이후)
- 파이프라인 다이어그램(macro→…→sizing) + 가드 상태, 최신 사이클 재구성, 결정 드릴다운.
- 산출: 파이프라인 뷰 + 드릴다운 패널.

### M4 — 자산 통계 차트 (Priority Medium; M2 이후, M3와 병행 가능)
- 에쿼티/드로다운/수익분포/알파/누적실현손익/confidence 산점도/postmortem 4분류/
  페르소나 귀인 차트 + 인터랙션 + 알파 graceful degrade.

### M5 — 뉴스 인텔리전스 뷰 (Priority Medium; M2 이후)
- 포트폴리오 관련 스토리 클러스터·키워드 트렌드·개별 뉴스 + 의사결정 연결 표시 + 관련 필터.

### (후속, 범위 외) 실시간 스텝 추적
- `persona_step_progress`(mig 034) — 엔진 write 필요. 본 SPEC MVP 제외, 별도 SPEC.

## 위험 (Risks)

- R1 (KOSPI 데이터): 알파 계산은 `ohlcv` 지수 종가 가용성에 의존. **run에서 실제 가용성
  확인**; 없으면 `benchmark_available=false` graceful degrade(거짓 수치 금지).
- R2 (지연 계산 부하): postmortem/confidence를 매 폴링 계산하면 비용↑. → 캐시 + 최근 N일
  제한 필수. run에서 캐시 TTL·N 튜닝.
- R3 (SPEC-048 stub 버그): `pd.run_id` 조인은 런타임 실패. M1에서 반드시 교정하고 회귀
  테스트 추가.
- R4 (프론트 빌드 통합): Vite 산출물 경로·FastAPI 라우트·도커 멀티스테이지 불일치 위험.
  M2에서 빌드→서빙 end-to-end 스모크로 조기 검증.
- R5 ("현재 사이클" 근사): 최신 사이클 재구성은 진짜 실시간이 아님 — UI에 "최신 사이클"
  라벨·갱신 시각 표기로 오해 방지.
- R6 (회귀): 기존 7개 엔드포인트 응답 형태 변경 시 SPEC-047 테스트 깨짐. 필드 추가만
  허용, 계약 테스트로 가드.

## 검증 게이트 (Verification Gates)

- 백엔드: 신규/확장 엔드포인트 계약 테스트 통과(mock `ro_connection`), 쓰기/DDL 시도 0.
- 프론트: `vite build` 성공 + FastAPI 서빙 스모크(브라우저 로드) + 컴포넌트/타입 테스트.
- 통합: 폴링 자동 갱신 동작, 결정 드릴다운, 차트 렌더, 뉴스 관련성 필터 동작.
- 회귀: 기존 전체 테스트 스위트 0 회귀.

## 자문 권고 (Expert Consultation)

- expert-frontend: 차트 라이브러리 최종 선택·SPA 구조·다크 금융 테마·차트 인터랙션.
- expert-backend: 지연 계산 캐시 전략·엔드포인트 계약·JOIN 정확성.
- expert-devops: Vite 멀티스테이지 도커 빌드·정적 산출물 서빙·Tailscale 경계 유지.
