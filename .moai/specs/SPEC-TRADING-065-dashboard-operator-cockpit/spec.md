---
id: SPEC-TRADING-065
version: 0.1.0
status: draft
created_at: 2026-08-15
updated_at: 2026-08-15
author: oni
priority: high
issue_number: null
labels: [dashboard, frontend, observability, validation-gate, ux]
---

# SPEC-TRADING-065 — 운영자 조종석: 지표 나열에서 "질문에 답하는 화면"으로

## HISTORY

- 2026-08-15 (v0.1.0, draft): 초안. 운영자 지적("대시보드가 너무 아마추어용이다")에서 출발.
  같은 세션에 Orca 내장 브라우저로 개요·포트폴리오·손익추이·결정추적 4화면을 실측 캡처하고
  결함을 특정. 같은 날 배포한 13커밋(SPEC-064 이후 결함4·출구3·진입2·관측성3)의 검증 게이트
  (8/17~8/29)를 볼 화면이 하나도 없다는 사실이 직접 동기. B(수정 이후 필터)·C(게이트 뷰)를
  별도 카드로 덧붙이지 않고 이 재구성 안에 흡수한다 — 따로 붙이면 카드가 13개가 된다.

---

## 배경 (관측된 사실 — 2026-08-15 18:46 KST 실측 캡처)

### 결함 A — 개요 화면이 "같은 크기 카드 10개 + 그 아래 같은 숫자 한 번 더"

`KpiCards.tsx` 가 총자산·일일손익·누적손익·CAGR·MDD·승률·PF·Sharpe·Sortino·알파를 **동일
크기·동일 무게**로 2열 배치한다. 바로 밑 "엣지 스코어카드"에 승률 36.9%·CAGR 6.0%·MDD -4.9%·
Sharpe 0.51·PF 0.22·거래수 65 가 **다시** 나온다. 화면 절반이 중복.

위계가 없어 "이 계좌가 잘되고 있나"가 3초 안에 안 읽힌다. 실측값으로 말하면 CAGR +5.99%(초록)와
PF 0.22(빨강)가 같은 무게로 나란히 있는데, **PF 0.22 = 1원 벌 때 4.5원 잃는다** — 이게 유일한
헤드라인이어야 한다.

### 결함 B — 포트폴리오 도넛의 92.5% 가 회색 현금

종목 6개가 3도짜리 조각으로 라벨이 겹친다. 정보량 0. 전하려는 사실은 **자본 가동률 5.9%(30일
평균)** 하나이고, 그건 게이지 하나로 끝난다. 이 가동률이 실측된 최대 레버(모든 출구 튜닝이
투자된 6% 에만 작동)인데 화면에서 읽히지 않는다.

### 결함 C — 손익 추이가 이중 Y축 + 미완성 문구 노출

막대(일별)·선(누적)이 겹친 이중 축이라 어느 축인지 헷갈리고 우측 축 라벨이 잘린다(`-40` 만 보임).
상단에 **"전체기간 알파 — 백엔드 미지원"** 이 사용자에게 그대로 노출된다.

### 결함 D — 오늘 배포한 13개 변경 중 화면에서 보이는 것이 0개

8/17~8/29 검증 게이트 9항목(메모리 `project_2026_08_15_twelve_fixes_gate`)을 볼 화면이 없다:
- confidence 새 정의(20일 수익 확률)의 상관 — 기존 `fetch_confidence_analysis` 는 **체결 22건**만
  본다. 오늘 831건 결정 반사실이 체결분과 **정반대**였으므로 이 화면만 보면 게이트를 오독한다
- `entry_freshness` 라벨 — 어디에도 없음
- 리스크 HOLD 사유 분포(과열 82% → 새 재량 3종) — 없음
- 매수 축소 사유(`PORTFOLIO_ADJUSTMENT.rationale`, `PORTFOLIO_GATE_DROP`) — 결정 상세 미노출
- 보유기간별 손익(2~15일 -35만 / 16~30일 +5만 — 출구 튜닝의 근거) — 없음
- 재진입 쿨다운·세션 차단 건수 — 없음

### 결함 E — 8/17 이후 진입분을 잘라 볼 수 없다

scorecard·confidence·postmortem 전부 전기간 또는 `days=N` 뿐이다. 새 출구(floor -15%)로 열린
포지션과 옛 포지션(floor -10%)이 섞이면 PF 가 한동안 더 나빠 보인다(새 것은 -15% 까지 버팀).
**2주 게이트에서 PF>1 을 판정하려면 "수정 이후 진입분만" 필터가 필수**다.

### 결함 F (SPEC-064 후속) — 결정 추적 응답이 결정 하나에 rationale 7개 전문(8KB)을 실어 나른다

`GET /api/decisions/{id}/trace` 의 `decision.response_json` 이 그 사이클의 시그널 7개 rationale 을
통째로 포함한다. 프런트는 그중 1개만 쓴다. 성능보다 "무엇을 보여줄지 정하지 않았다"는 신호.

---

## 설계 원칙 (프로 트레이딩 조종석)

1. **질문 순서 = 화면 순서.** 운영자가 매일 묻는 순서: 살아있나 → 돈을 버나 → 새 규칙은 어떤가 →
   어디서 잃나 → 진입 품질 → 무엇이 막고 깎았나 → 포트폴리오.
2. **헤드라인 하나가 화면의 40%.** 나머지는 보조·접힘.
3. **같은 숫자는 한 번만.**
4. **미완성 문구 노출 금지.** "백엔드 미지원" 류는 기능을 빼거나 완성하거나 둘 중 하나.
5. **B/C 흡수.** 날짜 필터·게이트 뷰는 카드 추가가 아니라 위 구조의 일부.
6. **재사용 우선.** `edge.roundtrips`·`edge.confidence`·`fetch_scorecard`·SPEC-064 trace 그래프·
   `theme.ts` 토큰(SPEC-054)을 그대로 쓴다. 신규 차트 라이브러리 금지(ECharts 유지).
7. **하드코딩 금지 [HARD].** 게이트 기준일(8/17)·임계(PF 1.0)는 설정/쿼리 파라미터. 코드 리터럴 금지.

---

## 요구사항 (EARS)

### 그룹 1 — 개요 재구성 (결함 A·B·C)

- **REQ-065-1a** WHEN 개요를 열면 THE 시스템 SHALL 헤드라인 영역에 **GO/NO-GO 판정 + PF + 거래당
  기대값(슬리피지 보정)** 을 화면 폭 40% 이상으로 표시하고, 승률·MDD·KOSPI 알파를 보조 3개로만
  둔다. CAGR·Sharpe·Sortino·총자산은 접힘("상세 지표") 안으로 옮긴다.
- **REQ-065-1b** THE 시스템 SHALL 개요에서 같은 지표를 두 번 표시하지 않는다(엣지 스코어카드 블록 제거,
  판정만 헤드라인으로 승격).
- **REQ-065-1c** WHEN 개요를 열면 THE 시스템 SHALL 최상단 상태줄에 스케줄러 하트비트(마지막
  resolver 실행)·마지막 사이클 시각·오늘 주문/거부/차단 건수·halt 상태를 한 줄로 표시한다.
- **REQ-065-1d** THE 시스템 SHALL 포트폴리오 도넛을 **자본 가동률 게이지**(투자비중 %, 30일 평균 병기)
  + 종목 표(비중·손익·보유일·effective_stop 까지 거리%)로 대체한다.
- **REQ-065-1e** THE 시스템 SHALL 손익 추이의 이중 Y축을 없애고 누적 실현손익 단일 선 + 일별 막대를
  **분리된 두 패널**로 그린다. "백엔드 미지원" 문구는 기능 구현(기간별 알파) 또는 제거 중 하나로
  해소한다 — 노출 금지.

### 그룹 2 — "수정 이후" 필터 (결함 E, B 흡수)

- **REQ-065-2a** THE 시스템 SHALL scorecard·confidence·postmortem·roundtrip 조회에 공통 `since`
  (ISO date) 파라미터를 받아 **그 날 이후 진입(entry_date)** 왕복만 집계한다.
- **REQ-065-2b** WHEN 운영자가 개요 상단 토글 "수정 이후만"을 켜면 THE 시스템 SHALL 그룹 1·3·4 전체
  집계를 `since=<게이트 기준일>` 로 재조회한다. 기준일은 설정값(`.moai/config` 또는 env
  `DASHBOARD_GATE_SINCE`)이며 코드 리터럴 금지.
- **REQ-065-2c** WHEN `since` 이후 왕복이 N 미만이면(N 설정, 기본 10) THE 시스템 SHALL 헤드라인에
  "표본 부족 (n=K)" 를 판정과 함께 표시한다 — 표본 없는 PF 를 판정으로 오독하지 않게.

### 그룹 3 — 게이트 뷰 (결함 D, C 흡수)

- **REQ-065-3a** THE 시스템 SHALL **보유기간별 손익** 패널을 제공한다: 0-1/2-5/6-15/16-30/31+ 일
  구간별 n·승률·평균%·합계원. 소스는 `compute_roundtrips`.
- **REQ-065-3b** THE 시스템 SHALL **진입 품질 매트릭스**를 제공한다: 행=confidence 버킷, 열=
  `entry_freshness`(early/confirmed/late/미기재), 셀=n·20일 반사실 수익·40일 반사실 수익. 소스는
  `persona_decisions`(side='buy', 체결 여부 무관) × `ohlcv` — **체결분이 아니라 결정 전체**다.
  체결분만 보는 기존 `fetch_confidence_analysis` 는 "체결 기준" 탭으로 유지하되 기본 탭이 아니다.
- **REQ-065-3c** THE 시스템 SHALL **리스크 판정** 패널을 제공한다: verdict 분포(APPROVE/HOLD/REJECT)
  + HOLD 사유 상위 N(rationale 키워드 집계: 단기과열/늦은 진입/20일 근거/재진입/기타) +
  HOLD 종목의 20/40일 반사실. `code_rules_passed` true/false 비율 병기.
- **REQ-065-3d** THE 시스템 SHALL **매수 축소·차단** 패널을 제공한다: 게이트별(portfolio_persona /
  sector_cap / cash_floor / reentry_cooldown / session / limit_breach) 건수·평균 삭감률, 최근 N건의
  rationale 표. 소스는 `PORTFOLIO_ADJUSTMENT`·`PORTFOLIO_GATE_DROP`·`LIMIT_BREACH`·
  `ORDER_BLOCKED_OUTSIDE_SESSION`.
- **REQ-065-3e** THE 시스템 SHALL 결정 상세(SPEC-064 드릴다운)에 `entry_freshness`·`code_rules_passed`·
  해당 결정의 축소/드롭 rationale 을 노출한다.

### 그룹 4 — 결정 추적 응답 슬림화 (결함 F)

- **REQ-065-4a** THE `GET /api/decisions/{id}/trace` SHALL `decision.response_json` 을 **해당
  decision_id 의 시그널 1개**로 줄이고, 사이클 요약(`summary`)만 별도 필드로 준다. 프런트가 안
  쓰는 형제 시그널 6개 rationale 은 내려보내지 않는다.

---

## 비요구 (명시적으로 안 하는 것)

- 실시간 시세 스트리밍(현재 일 1회 reconcile 기준 유지 — 화면에 "스냅샷 기준" 명시)
- 신규 차트 라이브러리·디자인 시스템 교체(SPEC-054 팔레트·ECharts 유지)
- 결정 추적 그래프 자체 개편(SPEC-064 산출물 재사용)
- 모바일 레이아웃

---

## 수용 기준 (AC) — 전부 라이브 실측으로 검증

- **AC-1** 개요 첫 화면(1280px 세로 스크롤 없이)에 GO/NO-GO·PF·기대값이 보이고, 같은 지표가 두 번
  나오지 않는다. Playwright 로 텍스트 중복 0 확인.
- **AC-2** "수정 이후만" 토글 → 모든 집계 API 가 `since` 를 받고, 응답의 `n` 이 전기간보다 작거나
  같다. `since` 이후 왕복 < N 이면 "표본 부족" 문구.
- **AC-3** 진입 품질 매트릭스가 결정 전체(체결 무관) 기준이며, 2026-08-15 시점 데이터로
  conf 0.4 구간 20일 수익이 0.6 구간보다 높게 나온다(오늘 실측 재현). `entry_freshness` 열은
  8/17 이전 데이터에선 "미기재" 하나만 채워진다.
- **AC-4** 리스크 패널의 HOLD 사유 집계에서 2026-08-15 이전 데이터는 "단기과열" 이 82% 로 나온다
  (오늘 실측 재현) — 8/17 이후 비율 변화가 이 패널에서 읽혀야 한다.
- **AC-5** 매수 축소 패널이 8/14 `PORTFOLIO_ADJUSTMENT`(316140 6→3) 을 rationale 없음으로,
  8/17 이후 건은 rationale 채워진 채로 구분 표시한다.
- **AC-6** trace 응답 크기가 3036 기준 8KB → 2KB 이하.
- **AC-7** 하드코딩 0: 게이트 기준일·표본 N·PF 임계가 설정에서 오고, 테스트는 상수 상대값.
- **AC-8** `npx vite build` 통과, 기존 dashboard 단위 테스트(148) 회귀 0.

---

## 구현 순서 (그룹 = 커밋 단위, SPEC-062 관례)

1. **그룹 2 먼저** (`since` 파라미터·설정) — 다른 그룹이 전부 이걸 쓴다. 백엔드만.
2. **그룹 3 백엔드** — 쿼리 4개(`fetch_holding_period_pnl` / `fetch_entry_quality_matrix` /
   `fetch_risk_verdicts` / `fetch_sizing_gates`) + 라우트. 오늘 세션에서 쓴 SQL 을 그대로 옮긴다.
3. **그룹 4** — trace 슬림화(작음).
4. **그룹 1 + 그룹 3 프런트** — 개요 재구성과 게이트 뷰를 한 화면 체계로.
5. Playwright 실측 → AC 대조 → 메모리 갱신.

**목표: 8/17(월) 장 시작 전.** 게이트 관측이 이 화면 없이 시작되면 SQL 로 대체 가능하나
오독 위험이 있다(결함 D 의 confidence 사례).

---

## 관련

- SPEC-054(엔터프라이즈 개편·팔레트) / SPEC-064(결정 추적) — 재사용
- 메모리 `project_2026_08_15_twelve_fixes_gate`(게이트 9항목), `feedback_evidence_over_intuition`
- 오늘 커밋: b6ab9a1…98bed29 (13+1)
