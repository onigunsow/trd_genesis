---
id: SPEC-TRADING-064
version: 0.1.0
status: draft
created_at: 2026-08-09
updated_at: 2026-08-09
author: oni
priority: high
issue_number: null
labels: [dashboard, observability, decision-trace, audit, frontend]
---

# SPEC-TRADING-064 — 결정 추적 흐름도: 어떤 모듈이 무엇을 판단했는지 복원

## HISTORY

- 2026-08-09 (v0.1.0, draft): 초안 작성. 운영자 지적("대시보드 파이프라인 부분이 제일 마음에
  안들었었다. 어떤 결정 피드가 있으면 어떤 모듈이 어떻게 동작하고 어떻게 연계가 되어 그런 결정을
  한건지 알수가 없었다. / 각각의 노드에서 판단한 내용은 블럭에 표시를 하면 되는거고")에서 출발.
  같은 세션의 라이브 API 호출·DB information_schema 조회·전 audit 호출부 감사로 세 결함(A/B/C)을
  실측 확정하고, SPEC-062의 그룹 단계 분리 관례(A/B/C 순차 배포)를 따라 3그룹으로 나눔.
  이미 병합된 `src/trading/scripts/codemap.py`(f05aa78)의 그래프 생성·ast 브릿지·n8n 스타일
  렌더링을 **재사용 자산**으로 못박고 신규 구축을 금지함.

---

## 배경 (관측된 사실)

운영자는 결정 피드에서 한 건을 고르면 **어떤 모듈이 어떤 순서로 관여했고 각 모듈이 무엇을
판단했는지**를 노드 블록 위에서 보길 원한다. 현재 대시보드는 이 셋 중 어느 것도 못 준다.
원인은 UI 디자인이 아니라 세 층에 걸친 결함이다.

### 결함 A — 결정 상세 계약 드리프트 (백엔드가 안 주는 필드를 프런트가 읽는다)

`GET /api/decisions?limit=1`의 실제 응답 키는 정확히 12개다:
`confidence, cycle_kind, id, persona_name, qty, rationale, risk_rationale, risk_verdict, side,
ticker, ticker_name, ts`.

그런데 `src/trading/dashboard/frontend/src/components/PipelineView.tsx`의 드릴다운은
`d.regime_at_decision`(297행), `d.prob_bull`/`d.prob_base`/`d.prob_bear`(301-302행),
`d.trigger_context`(308행), `d.response_json`(309-312행) — **여섯 필드를 읽는다. 전부 응답에
없다.** `src/trading/dashboard/frontend/src/api/types.ts:19-39`의 `Decision` 타입이 이들을
선언하고 있어 `tsc --noEmit`은 통과하고, 런타임에서만 빈칸으로 렌더된다.

프런트 테스트 픽스처가 **실제 응답이 아니라 선언된 타입**을 목킹하므로 테스트도 초록이다.
이 저장소가 반복해 데인 거짓그린(SPEC-042 positions.mode·order_type, SPEC-054 pd.persona 503)과
같은 유형이다.

`/api/pipeline`도 동일하다. 실제 응답은 `{cycle_ts, steps[]}`이고 각 step 키는
`cycle_kind, id, input_tokens, latency_ms, output_tokens, persona_name, regime_at_decision,
status, ts` 9개뿐인데, TS `PipelineData`/`PipelineStep`은 `cycle_id, cycle_started_at,
halt_state, halt_reason, step, started_at, decisions, verdicts`를 선언한다 — 전부 부재.
`status`는 4-값 union으로 선언됐지만 백엔드는 `'error'|'completed'`만 낸다.

### 결함 B — decision_id 배선 불균일 (기록은 있는데 결정에 못 붙는다)

`audit_log`에는 관여 기록이 이미 쌓이고 있다. 그러나 27개 `audit()`/`_audit()` 호출부 중
`details.decision_id`를 최상위로 실어보내는 곳은 5곳뿐이고, 그중 `LIMIT_BREACH`
(`src/trading/risk/limits.py:248`)는 `details.context.decision_id`로 **깊이가 다르다**.
나머지는 값이 스코프에 있음에도 dict 키를 안 넣었거나(4곳), 한 홉만 넘기면 되는데 시그니처를
안 바꿨거나(5곳), SELECT 한 컬럼만 추가하면 되는데 안 했다(4곳).

결과: "이 결정 때문에 무엇이 일어났는가"를 SQL 한 번으로 물어볼 수 없다. 결함 A를 고쳐도
피드 드릴다운은 여전히 **결정 본문만** 보여줄 뿐 모듈 관여 경로를 못 그린다.

### 결함 C — 파이프라인 뷰가 흐름도가 아니다

`PipelineView.tsx`(349행)는 halt 배너 → `STEP_ORDER = ['macro','micro','decision','risk',
'portfolio','sizing']` 고정 6장 카드를 `›` 글리프로 이은 가로 flexbox → 결정 피드 리스트 순서다.
**그래프가 아니고**, 백엔드에 `step` 필드가 없어 `persona_name` 부분문자열 스캔으로 단계를
맞춘다. 노드도 엣지도 없으니 "어떤 모듈이 어떻게 연계됐는지"가 구조적으로 표현 불가하다.

---

## 근거 (코드·DB 실측, 2026-08-09)

### 이미 존재하는 자산 (재사용 대상 — 신규 구축 금지)

`src/trading/scripts/codemap.py`(커밋 f05aa78, main 병합 완료)는 이미 대화형 결정-흐름
그래프를 생성한다. 본 SPEC은 이것을 **대시보드로 편입**하는 작업이며, 그래프 엔진·스타일·
브릿지를 새로 만드는 것이 아니다.

- **구조**: `code-graph-mcp callgraph <entry> --direction callees --depth 3 --json`,
  진입점 `run_pre_market_cycle` / `run_intraday_cycle` → 블록 133개 / 파일 33개 / 엣지 163개.
- **성과**: `audit_log` / `orders` 30일 집계.
- **둘을 잇는 브릿지 (핵심 자산)**: `src/trading/**/*.py`에 대한 `ast` 스캔이
  `audit("EVENT_TYPE", ...)` / `_audit(...)` 리터럴을 찾아 **감싸는 함수**를 식별,
  `(file, function) -> [event_type]` 맵을 만든다. 77개 블록이 감사 기록을 낸다.
  이 맵을 **역방향**으로 돌리면 event_type 집합 → 강조할 함수 노드 집합이 나온다.
  그룹 C의 노드 강조는 전적으로 이 역인덱스로 구현한다.
- **렌더링**: n8n 스타일 — 도트 그리드 캔버스, 흰 카드 노드 + 모듈 픽토그램, 술어 함수
  (`check_`/`requires_`/`is_`/`guard_`/`has_` 접두)는 다이아몬드, 소스 포트 점이 달린 베지어
  엣지, 모듈 단위 접기와 제자리 +/− 펼침, 레벨 인지 우측 패널, 접히는 사이드바.
  cytoscape 3.30 + cytoscape-dagre(CDN).
- **현재 한계**: 수동 셸 파이프로만 생성되고, 컨테이너가 `.codemap/`에 쓸 수 없어 HTML이
  stdout으로만 나온다.
- **선행 자기점검**: `--selftest`가 ast 브릿지가 `risk/limits.py`의 `LIMIT_BREACH`를 여전히
  찾는지, STAGES 심볼이 소스에 남아있는지 검증한다.

### 대시보드 현재 상태

- 서비스 `trading-dashboard-api`, `127.0.0.1:8080`, FastAPI `src/trading/dashboard/app.py`,
  uvicorn `--workers 1`, `--reload` 없음.
- `compose.yaml`이 `./src:/app/src:ro` 바인드마운트. `Dockerfile`은 파이썬 전용 — **Node 스테이지
  없음**. 프런트는 호스트에서 `npm run build`(`tsc --noEmit && vite build`), `vite.config.ts`가
  `build.outDir: '../static'`, `base: '/static/'`. 즉 `src/trading/dashboard/static/`의 번들은
  **git 추적 산출물**이다. 재빌드하면 컨테이너 재시작 없이 즉시 반영되지만(바인드마운트 +
  요청마다 읽는 `StaticFiles`), 커밋해야 영속된다.
- react-router 없음. `App.tsx`가 `useState<View>` + `type View =
  'overview'|'portfolio'|'roundtrips'|'pnl'|'pipeline'|'news'|'positions'` + `NAV_ITEMS` 사이드바.
  새 페이지 = union 멤버 추가 + NAV_ITEMS 항목 + `<ErrorBoundary>` 안 조건부 렌더.
- 프런트 의존성: `echarts` ^5.5.0, `echarts-for-react` ^3.0.2, react 18.
  **cytoscape/dagre/mermaid/d3/react-flow 없음.** 스타일은 `theme.ts`를 참조하는 평범한 JS
  스타일 객체 상수(`const s = {...}`), `App.tsx`/`StatusBar`만 별도로 CSS 커스텀 프로퍼티 사용.
  CSS 모듈/tailwind/styled-components 없음.
- 테스트: 프런트 Vitest + @testing-library(`frontend/src/test/`, `components.test.tsx`에
  `describe('PipelineView')`와 드릴다운 블록). 파이썬 `tests/dashboard/test_queries.py`에
  `TestFetchPipeline`·`TestFetchRecentDecisions`. `tests/dashboard/test_endpoints.py`는
  `TestClient`를 쓰지만 **`TestPipelineEndpoint`가 없다** — `/api/pipeline`은 엔드포인트 레벨
  테스트가 전무하다.

### 없는 데이터가 실제로 사는 곳 (information_schema 실측)

- `trigger_context`, `regime_at_decision`, `response_json`은 **`persona_runs`** 테이블에 있다
  (`persona_decisions`가 아니다).
- `queries.fetch_recent_decisions`(`src/trading/dashboard/queries.py:127-169`)는 **이미**
  `JOIN persona_runs pr ON pr.id = pd.persona_run_id`를 한다. 세 필드 모두 SELECT 목록에 이름만
  추가하면 닿는다 — **신규 JOIN·마이그레이션·스키마 변경 없음.**
- `prob_bull`/`prob_base`/`prob_bear`는 `persona_decisions`에 컬럼은 있으나 **최근 30일 723행 중
  0행 채워짐(전부 NULL)**. Decision 페르소나가 애초에 안 낸다 — `persona_decisions.raw` 키는
  `qty, side, ticker, rationale, confidence, intended_position_pct`뿐.
- 같은 30일 채워진 것: `rationale` 723/723(300-510자), `confidence` 723/723, `raw` 723/723
  (438-623자). `risk_reviews` 270행은 이미 LEFT JOIN 되어 있다.

### decision_id 배선 전수 감사 (27개 호출부)

정본 id는 `persona_decisions.id`. `src/trading/personas/decision.py:159-178`에서 RETURNING으로
`sig_ids: list[int]`에 담아 `decision.py:180`이 `(res, sig_ids)`로 반환한다. 두 사이클 모두
`for sig, decision_id in zip(signals, sig_ids, strict=False):`로 순회한다
(`orchestrator.py:1239` pre-market, `orchestrator.py:1735` intraday). 즉 그 루프 몸통에서
호출되는 모든 것에 `decision_id`가 **평범한 지역 변수로 이미 스코프에 있다**.

- **TIER 1 — 이미 실려 있음(작업 0)**: `orchestrator.py:944` SIZING_DETERMINISTIC(최상위, 949행),
  `orchestrator.py:1091` EXEC_FAILED(최상위, 1092행), `orchestrator.py:1336`
  ORDER_BLOCKED_SAFETY(pre-market, 최상위 1337행), `orchestrator.py:1810` ORDER_BLOCKED_SAFETY
  (intraday, 최상위 1811행), `limits.py:248` LIMIT_BREACH — **단 이것만
  `details.context.decision_id`로 깊이가 다름**(호출부 `orchestrator.py:1383`의
  `record_breach(chk, {"signal": sig, "decision_id": decision_id})`). 최상위로 정규화 대상.
- **TIER 2 — 값이 이미 스코프, dict 키만 추가(시그니처 무변경)**: `orchestrator.py:1251`
  TICKER_BLOCKED_BY_HOLDS(pre-market, 루프변수 1239), `orchestrator.py:1747` 동(intraday, 1735),
  `orchestrator.py:425` COUNT_HALT_BYPASS_SELL(`_maybe_count_halt_bypass`의 `bypass_ids`가 409행),
  `circuit_breaker.py:76` CIRCUIT_BREAKER_TRIP(`trip(reason, details=None)`이 이미 자유형 dict를
  받는데 루프 안 호출부 `orchestrator.py:1393`이 안 넘길 뿐).
- **TIER 3 — 한 홉 선택적 시그니처 변경(5곳). 이 함수들은 결정이 아예 없는 경로에서도
  불린다 → 신규 파라미터는 반드시 Optional이고 부재는 정직하게 기록해야 한다**:
  `broker_truth.py:129` PHANTOM_SELL_BLOCKED / `broker_truth.py:144`
  OVERSELL_CLAMPED_PRESUBMIT(`clamp_sell_to_confirmed(client, ticker, qty)`, 유일 호출부
  `orchestrator.py:1046`에 id 있음), `sell_lock.py:163` SELL_INFLIGHT_LOCKED
  (`set_sell_inflight(ticker)`, 호출부 `orchestrator.py:1076`은 id 있음 / `watchers/
  position_watchdog.py:512`는 cron 폴링이라 결정 자체가 없음), `sell_lock.py:210`
  SELL_SUPPRESSED_DUPLICATE(`guard_sell(ticker, *, actor, now=None)`, `orchestrator.py:1041`은
  id 있음 / `position_watchdog.py:361`·`503`은 없음), `portfolio_gate.py:426`
  PORTFOLIO_ADJUSTMENT(`_emit_transparency(cycle_kind, adjusted_report, rejected_report)`) —
  이건 여러 종목을 한 행에 담는 **배치 이벤트**다. 호출부 `_apply_portfolio_adjustment`의
  `sid`가 스코프에 있고(`portfolio_gate.py:358`의 `orig_qty = {sid: ...}`), 올바른 해법은
  스칼라 파라미터가 아니라 `adjusted_report`/`rejected_report` **각 항목 안의 per-item id**다.
  추가로 `res.decision_run_id`(`orchestrator.py:1197`에서 설정, 호출 1230/1725보다 앞)가
  이 이벤트의 자연스러운 사이클 레벨 상관키다.
- **TIER 4 — 기존 SELECT에 컬럼 하나 추가 후 헬퍼 1~2단 전달(4곳)**: `broker_truth.py:471`
  ORDER_FILLED / ORDER_PARTIAL(`_emit_fill_audit` ← `_apply_one_fill:455`, 구동 SQL
  `broker_truth.py:363-374`가 `id, qty, fill_qty, status, kis_order_no, ticker, side`를 뽑음 →
  `persona_decision_id` 추가), `order_resolver.py:237` ORDER_RESOLVED /
  `order_resolver.py:261` STUCK_ORDER_EXPIRED(`_resolve_one`, 후보 SELECT
  `order_resolver.py:153,165`가 `id, ts, side, ticker, qty, status` → `persona_decision_id` 추가).
- **TIER 5 — 구조적으로 배치/계좌 단위, 자연스러운 단일 결정 id가 없음(5곳). "누락"이 아니라
  올바르게 결정 비종속임**: `orchestrator.py:615` SILENT_MODE_ON(무신호 3사이클 연속 집계),
  `circuit_breaker.py:91` CIRCUIT_BREAKER_RESET(운영자 `/resume` 액션), `broker_truth.py:201`
  INTRADAY_RECONCILE(계좌 전체 대사 — 이벤트에 ticker 필드조차 없음), `order_resolver.py:322`
  STUCK_ORDER_CLEANUP(정리 실행 전체 요약), `sell_lock.py:186` SELL_INFLIGHT_CLEARED
  (`sell_lock.py:231`의 stale 마커 정리, 호출 문맥 없음).
- **TIER 6 — 결정 id 이야기가 성립 안 함**: `telegram.py:249` SYSTEM_ERROR는 Micro/Decision
  페르소나 실패(`orchestrator.py:1124,1195`)를 포함한 횡단 실패에서 발화 — 즉 **결정 id가
  존재할 수 있기 이전** 시점이다.
- **TIER 7 — 모듈 경계를 넘는 두 홉(1곳)**: `telegram.py:298` ORDER_REJECT_ALERT
  (`order_rejected(order_id, ticker, side, qty, mode, reason, ...)`). 유일 호출부
  `kis/order.py:391`(`submit_order`)은 `persona_decision_id`를 **받아서 orders 행에 쓰면서**
  텔레그램 호출로는 안 넘긴다.

감사 호출이 **0건**임을 확인한 순수 계산/게이트: `risk/blocked_cache.py`,
`edge/validation_gate.py`, `strategy/sizing/vol_target.py`, `strategy/sizing/kelly.py`.

### orders.persona_decision_id NULL의 완전한 설명 (128/180 채워짐)

`orders` INSERT는 `kis/order.py:submit_order` 한 곳뿐이고(`order.py:271-289`, 파라미터
`order.py:232`) 받은 값을 그대로 쓴다. 모든 NULL은 **호출자가 None을 넘긴 것**이다.

1. `kis/ghost_convergence.py:_insert_correction_sell`(112-140행) — 원시 INSERT,
   `correction=TRUE, synthetic=TRUE, persona_decision_id=NULL`. 기존 코드 주석:
   `persona_decision_id=NULL — 교정 행은 LLM 결정이 아님`.
2. `risk/late_cycle.py:217` `forced_deleverage`, `watchers/position_watchdog.py:368`
   `_execute_trim`, `watchers/position_watchdog.py:511` 손절/익절 청산 — 전부 명시적으로
   `persona_decision_id=None`. 규칙 기반 결정적 청산이지 LLM 결정이 아니다.
3. `scripts/paper_buy_one.py:48` 개발 스크립트(무시 가능).

`synthetic=TRUE`가 곧 NULL을 뜻하지는 않는다 — `_synthetic_fill`은 기존 행을 UPDATE만 한다.

**설계 귀결**: `persona_decision_id`가 NULL인 주문은 "출처 불명"이 아니라 **"규칙 기반 실행,
LLM 결정 없음"**이다. UI는 이것을 "기록 없음"과 **구별해서** 말해야 한다.

---

## 목표

1. 결정 피드에서 한 건을 고르면, 그 결정의 **전체 상세**(레짐·트리거 문맥·원본 응답)를
   본다 — 백엔드가 이미 JOIN해 둔 데이터를 반환하기만 하면 된다. (그룹 A)
2. `audit_log`에 남는 관여 기록을 **결정 단위로 조회 가능**하게 만든다 — 최상위
   `details.decision_id` 단일 규약. 배치/계좌 단위 이벤트는 면제를 **명문화**해서 후대가
   "고치려" 들지 않게 한다. (그룹 B)
3. 결정 하나를 골라 **codemap 흐름도 위에 그 결정의 경로만 강조**하고, 각 노드 블록에
   그 단계가 무엇을 판단했는지 표시한다. (그룹 C)
4. 세 그룹 모두 **거짓그린을 낳지 않는 방식**으로 검증한다 — 픽스처는 실제 응답에서 파생,
   SQL 변경은 통합 테스트 필수, 시그니처 변경 전 특성화 테스트 선행.

---

## 요구사항 (EARS)

### 그룹 A — 결정 상세 복구 (계약 정합) · Priority High

- REQ-064-A1: WHEN `/api/decisions`가 호출될 때, THE 시스템 SHALL
  `fetch_recent_decisions`(`src/trading/dashboard/queries.py:127-169`)의 SELECT 목록에 이미
  JOIN된 `persona_runs`의 `regime_at_decision`, `trigger_context`, `response_json`을 포함해
  반환한다. 신규 JOIN·신규 테이블·마이그레이션을 추가하지 않는다.
- REQ-064-A2: THE `Decision` 타입(`src/trading/dashboard/frontend/src/api/types.ts:19-39`)
  SHALL 백엔드가 실제 반환하는 키 집합과 1:1로 일치한다. 백엔드가 반환하지 않는 필드를
  타입에 선언하는 것을 금지한다.
- REQ-064-A3: THE `PipelineData`/`PipelineStep` 타입 SHALL `/api/pipeline`의 실제 응답
  (`{cycle_ts, steps[]}`, step 키 9개: `cycle_kind, id, input_tokens, latency_ms,
  output_tokens, persona_name, regime_at_decision, status, ts`)과 일치하며, `status` union은
  백엔드가 실제로 내는 `'error'|'completed'`만 포함한다.
- REQ-064-A4: THE 드릴다운 UI(`PipelineView.tsx:301-302`) SHALL `prob_bull`/`prob_base`/
  `prob_bear` 표시를 제거한다(ADR-001). 723/723 NULL이며 페르소나가 산출하지 않는 값을
  렌더하지 않는다.
- REQ-064-A5: THE 프런트 테스트 픽스처(`frontend/src/test/components.test.tsx`의
  `describe('PipelineView')` 드릴다운 블록) SHALL 선언된 TS 타입이 아니라 **실제 백엔드 응답
  샘플**에서 파생한 객체를 사용한다. IF 픽스처에 백엔드가 내지 않는 키가 있으면, THEN 테스트는
  실패해야 한다.
- REQ-064-A6: THE `tests/dashboard/test_endpoints.py` SHALL `TestPipelineEndpoint`를 신설해
  `/api/pipeline`의 응답 키 집합과 `status` 값 도메인을 `TestClient`로 검증한다(현재 엔드포인트
  레벨 테스트 전무).
- REQ-064-A7: IF `trigger_context`·`response_json`·`regime_at_decision`이 NULL이면, THEN THE UI
  SHALL "미기록"으로 명시 표기한다. 빈 문자열·`0`·`-`·공백으로 렌더해 "통과"나 "정상"으로
  읽히게 해서는 안 된다.
- REQ-064-A8: THE `tests/dashboard/test_queries.py`의 `TestFetchRecentDecisions` SHALL dict를
  직접 주입하는 대신 `MultiCursor`/`FakeConnection` 경유로 실제 SQL 문자열 실행 경로를 타며,
  SELECT 목록 변경은 배포 전 `pytest tests/integration/ -m integration`으로 실 Postgres에서
  검증한다.

### 그룹 B — decision_id 일관 배선 · Priority High

- REQ-064-B1: THE 시스템 SHALL `audit_log.details`의 **최상위 키 `decision_id`**를 결정 상관키의
  단일 규약으로 정의하고, 그룹 B가 다루는 모든 이벤트가 이 위치를 따르게 한다. 중첩 경로
  (`details.context.decision_id` 등)를 새로 도입하지 않는다.
- REQ-064-B2: WHEN `record_breach`가 `LIMIT_BREACH`를 기록할 때(`src/trading/risk/limits.py:248`,
  호출부 `orchestrator.py:1383`), THE 시스템 SHALL `decision_id`를 `details` 최상위에 싣는다.
  기존 `details.context` 페이로드는 보존한다(관측성 회귀 0).
- REQ-064-B3: WHEN TIER 2 이벤트가 기록될 때 — `orchestrator.py:1251`·`1747`
  TICKER_BLOCKED_BY_HOLDS, `orchestrator.py:425` COUNT_HALT_BYPASS_SELL,
  `circuit_breaker.py:76` CIRCUIT_BREAKER_TRIP(호출부 `orchestrator.py:1393`) — THE 시스템 SHALL
  이미 스코프에 있는 `decision_id`(각각 루프변수 1239/1735, `bypass_ids`(409행), 루프변수)를
  `details` 최상위에 추가한다. 함수 시그니처는 변경하지 않는다.
- REQ-064-B4: WHERE TIER 3 함수가 결정 문맥을 받을 수 있는 경우
  (`clamp_sell_to_confirmed`(`broker_truth.py:129,144`), `set_sell_inflight`
  (`sell_lock.py:163`), `guard_sell`(`sell_lock.py:210`)), THE 시스템 SHALL **Optional
  키워드 파라미터**로 `decision_id`를 받는다. IF 호출자가 워치독 경로
  (`position_watchdog.py:361`·`503`·`512`)라서 결정이 존재하지 않으면, THEN 이벤트는
  `decision_id: null` 과 함께 출처를 밝히는 `decision_scope: "watchdog"`를 기록한다.
  결정 없는 경로를 깨뜨리는 필수 파라미터로 만들지 않는다.
- REQ-064-B5: WHEN `_emit_transparency`가 `PORTFOLIO_ADJUSTMENT`를 기록할 때
  (`portfolio_gate.py:426`), THE 시스템 SHALL 스칼라 `decision_id` 대신 `adjusted_report`/
  `rejected_report` **각 항목 안에 per-item `decision_id`**를 싣고(호출부
  `_apply_portfolio_adjustment`의 `sid`, `portfolio_gate.py:358`), 이벤트 최상위에는 사이클
  상관키로 `decision_run_id`(`orchestrator.py:1197`에서 설정)를 기록한다. 이 이벤트의
  `decision_scope`는 `"batch"`다.
- REQ-064-B6: WHEN TIER 4 이벤트가 기록될 때 — `broker_truth.py:471` ORDER_FILLED/ORDER_PARTIAL,
  `order_resolver.py:237` ORDER_RESOLVED, `order_resolver.py:261` STUCK_ORDER_EXPIRED — THE
  시스템 SHALL 구동 SELECT(`broker_truth.py:363-374`, `order_resolver.py:153,165`)에
  `persona_decision_id` 컬럼을 추가하고 `_emit_fill_audit`/`_resolve_one`까지 전달해
  `details.decision_id`로 기록한다. IF 해당 orders 행의 `persona_decision_id`가 NULL이면,
  THEN `decision_id: null` + `decision_scope: "rule_based"`로 기록한다(C5의 규칙 기반 실행).
- REQ-064-B7: WHEN `submit_order`가 거부되어 `order_rejected`를 호출할 때
  (`kis/order.py:391` → `telegram.py:298` ORDER_REJECT_ALERT), THE 시스템 SHALL 이미 받아서
  orders 행에 쓰고 있는 `persona_decision_id`를 텔레그램 호출로도 전달해
  `details.decision_id`에 기록한다.
- REQ-064-B8: THE 시스템 SHALL TIER 5/6 이벤트 — `orchestrator.py:615` SILENT_MODE_ON,
  `circuit_breaker.py:91` CIRCUIT_BREAKER_RESET, `broker_truth.py:201` INTRADAY_RECONCILE,
  `order_resolver.py:322` STUCK_ORDER_CLEANUP, `sell_lock.py:186` SELL_INFLIGHT_CLEARED,
  `telegram.py:249` SYSTEM_ERROR — 를 **영구 면제**로 두고, 각 이벤트에
  `decision_scope`(`"aggregate"`/`"operator"`/`"account"`/`"cleanup"`/`"system"`)를 기록해
  결정 비종속이 **의도된 설계**임을 기계 판독 가능하게 남긴다. 이 면제 목록과 사유는 본 SPEC에
  고정되며, 후속 작업자가 "누락"으로 오인해 배선하지 않는다.
- REQ-064-B9: IF 그룹 B가 가동 중인 매매 경로 함수의 시그니처를 바꾸면, THEN 변경 이전에 해당
  호출부의 특성화 테스트가 먼저 존재해야 한다. 대상 스위트:
  `tests/personas/test_orchestrator.py`(83행이 이미 decision_id 참조),
  `test_orchestrator_sizing_guard.py`, `test_sizing_seam.py`, `test_intraday_cycle.py`,
  `test_count_halt_sell_bypass.py`, `test_reflection_loop.py`, `test_portfolio_gate.py`(감사
  단언 14건), `tests/risk/test_circuit_halt_classification.py`,
  `tests/kis/test_broker_truth.py`(17건), `tests/kis/test_order_resolver.py`(17건),
  `tests/watchers/test_sell_inflight_lock.py`, `tests/watchers/test_sell_dup_reproduction.py`,
  `tests/alerts/test_order_rejected_alert.py`, `tests/kis/test_ghost_convergence.py`.
  `risk/circuit_breaker.py`의 `trip()`/`reset()`은 직접 단위 테스트가 없으므로 신설한다.

### 그룹 C — 결정 추적 흐름도 (대시보드 편입) · Priority Medium

- REQ-064-C1: WHEN `GET /api/decisions/{decision_id}/trace`가 호출될 때, THE 시스템 SHALL
  그 결정 하나의 추적 페이로드를 반환한다: 결정 본문(그룹 A 확장 필드 포함), 시각 순
  `audit_events[]`(`details.decision_id = {id}`로 조회, `event_type`·`ts`·`details`·
  `decision_scope`), 연결된 `orders[]`(`persona_decision_id = {id}`), 그리고 강조 대상 노드
  집합을 계산하기 위한 `event_type` 목록.
- REQ-064-C2: [HARD] THE 흐름도 SHALL 각 노드의 상태를 **최소 네 가지로 구분**해 표시한다:
  ① 관여함(이 결정 id의 기록 있음) ② 관여했으나 기록이 결정 단위가 아님(TIER 5/6 —
  `decision_scope`가 batch/account/system 등) ③ 관여 안 함(해당 결정에 대한 기록 없음)
  ④ LLM 결정이 아닌 규칙 기반 실행(`orders.persona_decision_id IS NULL`, 근거 C5).
  빈칸이 "통과"로 읽혀서는 안 된다.
- REQ-064-C3: THE 노드 강조 SHALL `src/trading/scripts/codemap.py`의 ast 브릿지가 만드는
  `(file, function) -> [event_type]` 맵을 **역방향 인덱스**로 사용해 event_type 집합에서 함수
  노드 집합을 얻는다. 감사 이벤트 → 노드 매핑을 별도로 하드코딩하지 않는다.
- REQ-064-C4: THE 시스템 SHALL 그래프 구조 데이터를 커밋된 산출물로 공급하고(ADR-003 (a)),
  생성 명령을 SPEC과 저장소에 문서화한다. WHEN 자기점검이 실행될 때, THE 시스템 SHALL 커밋된
  구조가 현재 소스와 어긋나면 실패한다 — 최소한 (i) 산출물의 모든 노드 심볼이 소스에 존재하고
  (ii) ast 브릿지가 여전히 `risk/limits.py`의 `LIMIT_BREACH`를 찾는지 검증한다(기존
  `--selftest` 확장).
- REQ-064-C5: THE 프런트엔드 SHALL 새 `View` union 멤버(예: `'trace'`)와 대응 `NAV_ITEMS`
  항목을 추가하고 `<ErrorBoundary>` 안에서 조건부 렌더한다. 그룹 C에서 `PipelineView.tsx`의
  6장 카드 스트립은 **건드리지 않는다**(ADR-004).
- REQ-064-C6: THE 흐름도 SHALL cytoscape + cytoscape-dagre로 좌→우 레이어 DAG를 렌더하며,
  `codemap.py`가 이미 검증한 시각/상호작용 규약(흰 카드 노드 + 모듈 픽토그램, 술어 함수
  다이아몬드, 소스 포트 점 베지어 엣지, 모듈 접기/제자리 펼침, 레벨 인지 우측 패널)을 그대로
  재사용한다.
- REQ-064-C7: [HARD] THE 흐름도 SHALL 토폴로지 편집(노드 추가/삭제/드래그 재배선/순서 변경)
  기능을 제공하지 않는다. 읽기 전용 실행 뷰만 제공한다.
- REQ-064-C8: WHEN 노드가 선택될 때, THE 시스템 SHALL 그 단계가 **무엇을 판단했는지**를 블록
  또는 인접 패널에 표시한다: decision 노드는 `rationale`·`confidence`·`side`·`qty`,
  risk 노드는 `risk_verdict`·`risk_rationale`, limits 노드는 `LIMIT_BREACH` breach 토큰,
  portfolio 노드는 해당 항목의 조정 전/후 수량, sizing 노드는 SIZING_DETERMINISTIC 상세,
  주문 노드는 orders 상태와 `rejected_reason`.
- REQ-064-C9: [HARD] THE 시스템 SHALL 수치를 추정하지 않는다. IF DB에 값이 없으면, THEN
  "미기록"으로 표시한다. NULL을 `0`이나 성공/통과로 렌더하지 않는다.
- REQ-064-C10: WHEN 규칙 기반 실행 주문(`persona_decision_id IS NULL` — `late_cycle.py:217`
  `forced_deleverage`, `position_watchdog.py:368` `_execute_trim`, `position_watchdog.py:511`
  손절/익절, `ghost_convergence.py` 교정 행)이 표시될 때, THE 시스템 SHALL **"규칙 기반 실행
  (LLM 결정 없음)"**으로 명시하고 "기록 없음"과 시각적으로 구별한다.
- REQ-064-C11: THE 추적 엔드포인트와 뷰 SHALL 읽기 전용이다. 트레이딩 엔진 경로에 쓰기를
  하지 않으며, 페르소나 재실행이나 LLM 호출을 유발하지 않는다(비용 0).

---

## 아키텍처 결정 (ADR)

### ADR-001 — `prob_bull`/`prob_base`/`prob_bear`: 드릴다운에서 **제거**한다

- 선택: UI에서 제거.
- 기각 1(NULL 노출): 최근 30일 723/723 전부 NULL이다. 렌더하면 REQ-064-C9(추정 금지·NULL을
  성공으로 렌더 금지)를 정면으로 위반하고, 운영자에게 "확률 모델이 있다"는 잘못된 인상을 준다.
- 기각 2(Decision 페르소나 프롬프트를 확장해 확률을 내게 함): 이는 관측 복구가 아니라 **신규
  모델링 기능**이다. 프롬프트 + 파서 + 스키마 검증 + 백테스트 영향까지 딸려오며 본 SPEC의
  범위(계약 정합·관측성)를 벗어난다.
- 재도입 조건(후속 작업): Decision 페르소나 프롬프트 변경 + `persona_decisions.raw` 파서 변경 +
  채워짐 실측(>0행) 확인. 그때 별도 SPEC으로 다룬다.

### ADR-002 — 그래프 렌더링 라이브러리: **cytoscape + cytoscape-dagre 신규 도입**

- 선택: `cytoscape` + `cytoscape-dagre`를 프런트 의존성으로 추가하고, `codemap.py`가 이미
  동작 검증한 스타일/상호작용 코드를 이식한다.
- 기각(기존 echarts 재사용): echarts는 이미 있지만 `graph` 시리즈의 레이아웃은 force/circular/
  none뿐이라 **계층형 DAG 레이아웃이 없다**. 좌→우 흐름을 만들려면 어차피 dagre로 좌표를 직접
  계산해 주입해야 하고, 그러면 "기존 의존성 재사용"의 이점이 사라지면서 `codemap.py`의 검증된
  렌더링 코드를 버리고 다시 쓰는 비용만 남는다.
- 번들 크기 우려는 해당 없음: 대시보드는 127.0.0.1(Tailscale 내부)에 바인딩된 단일 운영자용
  도구이며 공개 트래픽이 없다.

### ADR-003 — 구조 데이터 공급: **커밋된 산출물 (a)**, 장기적으로 (c)

컨테이너 안에는 `code-graph-mcp` CLI가 없다. 세 선택지:

- (a) 콜그래프 JSON을 **빌드 산출물로 커밋**하고, 문서화된 명령으로 재생성한다.
- (b) 이미지에 CLI를 설치한다.
- (c) 기존 ast 스캐너 안에서 `ast`로 콜그래프까지 직접 추출한다.

- 선택: **(a)**. 프런트 번들(`src/trading/dashboard/static/`)이 이미 git 추적 산출물이라
  저장소 관례와 일치하고, Dockerfile(파이썬 전용, Node 스테이지 없음)을 건드리지 않으며,
  런타임 의존성이 0이다.
- 기각 (b): 이미지에 Node/CLI 툴체인을 넣는 것은 컨테이너 표면을 넓히고 매매 컨테이너의
  재현성을 떨어뜨린다. 관측 기능이 매매 이미지를 바꿀 이유가 없다.
- 후속 (c): ast 브릿지가 이미 같은 파일들을 파싱하므로 호출 엣지 추출을 여기로 흡수하면
  산출물 커밋 자체가 불필요해진다. 본 SPEC 범위 밖(후속 작업).
- **부패 감지 필수 조건**: (a)는 정의상 낡을 수 있다. REQ-064-C4대로 기존 `--selftest`를
  확장해 커밋된 구조가 소스와 어긋나면 **실패**해야 한다. 이 자기점검 없이 (a)를 채택하는 것은
  금지한다.

### ADR-004 — 새 뷰 신설 vs `PipelineView` 대체: **새 뷰를 먼저 추가**

- 선택: `View` union에 새 멤버를 추가하고 `PipelineView`는 그룹 C에서 **손대지 않는다**.
- 근거: 운영자가 두 뷰를 나란히 비교한 뒤 대체 여부를 판단할 수 있다. `PipelineView`는
  halt 배너와 결정 피드 리스트라는 별개 기능도 담고 있어, 흐름도로의 즉시 대체는 관측성 회귀
  위험을 동반한다. react-router가 없어 새 뷰 추가 비용은 union 멤버 + NAV_ITEMS 항목 +
  조건부 렌더 세 줄 수준이다.
- 대체(6장 카드 스트립 제거)는 운영자가 비교 후 요청하면 후속 SPEC으로 처리한다.

---

## 제약 (Constraints)

- [HARD] 노드 상태는 최소 네 가지를 구분해 표시한다: ① 관여함(기록 있음) ② 관여했으나 기록이
  결정 단위가 아님(TIER 5 배치/계좌 단위) ③ 관여 안 함 ④ LLM 결정이 아닌 규칙 기반 실행
  (`orders.persona_decision_id IS NULL`, 근거 C5). 빈칸이 "통과"로 읽혀서는 안 된다.
- [HARD] 수치를 추정하지 않는다. DB에 없으면 미기록으로 표시한다. NULL을 0이나 성공으로
  렌더하지 않는다.
- [HARD] 토폴로지 편집 기능은 만들지 않는다. n8n의 실행 뷰만 가져온다. 근거:
  `guard_sell`·`clamp_sell_to_confirmed`·`requires_circuit_halt`는 순서와 위치가 곧 안전장치라,
  편집 가능하게 만들면 드래그 한 번이 안전장치를 우회한다.
- [HARD] 시장 종속 값 하드코딩 금지. 시장별 YAML + `active_market()`. 미국 주식도 같은 로직으로
  갈 예정이다.
- [HARD] 모든 실행은 컨테이너 안에서(`docker exec trading-app trading <cmd>`). 호스트 직접
  실행은 DB 미접근 hang.
- [HARD] 그룹 B는 가동 중인 매매 경로를 건드린다. 시그니처 변경 전에 해당 호출부의 특성화
  테스트가 먼저 있어야 한다(REQ-064-B9 목록 활용).
- SQL 변경 시 배포 전 `pytest tests/integration/ -m integration` 필수. mock은 거짓그린을 낸다 —
  단위 테스트는 dict 직접 주입이 아니라 `MultiCursor`/`FakeConnection`으로 실 SQL 실행 경로를
  타야 한다.
- 프런트 변경은 호스트에서 `npm run build` 후 `src/trading/dashboard/static/` 산출물을 커밋해야
  반영이 영속된다(바인드마운트라 재시작 없이 즉시 보이지만, 커밋하지 않으면 사라진다).
- 그룹 A는 단독 배포 가능하며 가장 싸고 효과가 크다. 그룹 B → 그룹 C 순서 의존
  (C1의 추적 조회는 B의 `decision_id` 규약에 의존).
- 신규 테이블·마이그레이션 없음. 그룹 A/C가 읽는 데이터는 전부 기존 스키마와 기존 JOIN 안에 있다.

---

## Exclusions (What NOT to Build)

- 흐름도의 **토폴로지 편집기**(노드 추가/삭제/드래그 재배선/실행 순서 변경) — 안전장치 우회
  위험, 영구 제외.
- `prob_bull`/`prob_base`/`prob_bear` 채우기 — Decision 페르소나 프롬프트/파서 변경이 필요한
  신규 모델링 기능(ADR-001).
- `PipelineView.tsx`의 6장 카드 스트립 **제거/대체** — 비교 후 후속 SPEC(ADR-004).
- TIER 5/6 감사 이벤트에 억지 `decision_id` 부여 — 구조적으로 배치/계좌/시스템 단위다
  (REQ-064-B8, 영구 면제).
- 신규 테이블·마이그레이션·`persona_decisions`/`persona_runs` 스키마 변경.
- WebSocket/SSE 실시간 스트리밍 — 기존 폴링 유지.
- 컨테이너 이미지에 `code-graph-mcp` CLI 설치(ADR-003 (b) 기각).
- ast 기반 콜그래프 자체 추출(ADR-003 (c)) — 후속 작업.
- 대시보드 인증·외부 노출·멀티유저 — 127.0.0.1 바인딩 유지.
- 추적 뷰에서의 LLM 재호출/페르소나 재실행 — 읽기 전용, 비용 0.
- 매매 엔진 동작 변경 — 그룹 B는 `audit_log.details` 페이로드만 늘리며, 주문 판단 로직은
  바꾸지 않는다.

---

## 인수 기준

### 그룹 A

- `GET /api/decisions?limit=1`이 `regime_at_decision`·`trigger_context`·`response_json`을
  포함해 15개 키를 반환한다(라이브 curl 실측으로 확인).
- `Decision`/`PipelineStep` 타입의 키 집합이 실제 응답 키 집합과 정확히 일치한다.
  응답에 없는 키를 타입에 남기면 리뷰에서 거부한다.
- 개정 전 픽스처로 돌리면 프런트 드릴다운 테스트가 **실패**하고, 실제 응답 파생 픽스처로
  바꾸면 통과한다(거짓그린 제거 증명).
- `tests/dashboard/test_endpoints.py::TestPipelineEndpoint`가 존재하고 통과한다.
- `TestFetchRecentDecisions`가 `MultiCursor`/`FakeConnection`으로 실 SQL 문자열을 실행한다.
- `pytest tests/integration/ -m integration` 그린(실 Postgres).
- 드릴다운에 `prob_*` 표시가 없다.
- NULL 필드는 "미기록"으로 렌더된다(스냅샷/단언 테스트).

### 그룹 B

- TIER 1~4 및 TIER 7 대상 이벤트가 전부 `details` **최상위** `decision_id`를 싣는다
  (이벤트 타입별 단위 테스트).
- `LIMIT_BREACH`가 최상위 `decision_id`를 싣고 기존 `details.context` 페이로드도 보존한다.
- 워치독 경로(`position_watchdog.py:361`·`503`·`512`)에서 `guard_sell`/`set_sell_inflight`가
  파라미터 없이 호출돼도 예외 없이 동작하고, 이벤트에 `decision_id: null` +
  `decision_scope: "watchdog"`가 기록된다.
- `PORTFOLIO_ADJUSTMENT`가 항목별 `decision_id`와 최상위 `decision_run_id`를 싣는다.
- `persona_decision_id IS NULL`인 주문의 fill/resolve 이벤트가
  `decision_scope: "rule_based"`로 기록된다.
- TIER 5/6 여섯 이벤트가 `decision_scope`를 싣고 `decision_id`는 싣지 않는다(면제 고정 테스트).
- `risk/circuit_breaker.py`의 `trip()`/`reset()` 직접 단위 테스트가 신설되어 통과한다.
- REQ-064-B9의 특성화 테스트 스위트 전체 회귀 0.
- 단일 결정 id로 `audit_log`를 조회하면 그 결정에 관여한 이벤트가 시각 순으로 나온다
  (통합 테스트).

### 그룹 C

- 결정 피드에서 한 건 선택 → 흐름도에서 그 결정의 경로만 강조되고, 노드 상태 네 종류가
  시각적으로 구별된다(스크린샷 또는 Playwright 단언).
- 노드 선택 시 그 단계의 판단 내용(REQ-064-C8 목록)이 표시된다.
- 규칙 기반 실행 주문이 "규칙 기반 실행(LLM 결정 없음)"으로 표기되며 "기록 없음"과 다르게
  보인다.
- 자기점검이 커밋된 구조 산출물과 소스 불일치 시 **실패**한다(고의로 함수명을 바꾼 상태에서
  실패를 확인).
- ast 브릿지 역인덱스가 `LIMIT_BREACH` → `risk/limits.py`의 해당 함수 노드를 반환한다.
- 흐름도에 편집 어포던스(드래그 재배선/노드 추가·삭제 UI)가 존재하지 않는다.
- `PipelineView.tsx` diff 0(그룹 C 범위 내).
- `npm run build` 성공 + `src/trading/dashboard/static/` 산출물 커밋.
- 추적 엔드포인트 호출이 유료 API 호출 0건(SPEC-053 PAID_CALL 계측으로 확인).

---

## @MX 태그 대상

- `src/trading/scripts/codemap.py` — audit emitter ast 스캐너(감사 리터럴 → 감싸는 함수 매핑).

  ```
  # @MX:ANCHOR: audit-emitter map is bidirectional — codemap renders forward
  #   ((file, function) -> event_types); the decision-trace view consumes it in
  #   reverse (event_types -> nodes to highlight).
  # @MX:REASON: Two consumers depend on the exact shape of this mapping. A silent
  #   change (renaming keys, dropping the enclosing-function resolution) breaks
  #   node highlighting with no test failure in the codemap path.
  # @MX:SPEC: SPEC-TRADING-064
  ```

- `src/trading/dashboard/queries.py::fetch_recent_decisions` (127-169행).

  ```
  # @MX:ANCHOR: response key set is a contract with the frontend Decision type.
  # @MX:REASON: SPEC-TRADING-064 결함 A — the TS type declared six fields the SELECT
  #   never returned; tsc passed and the UI rendered blank. Adding or removing a
  #   column here requires updating api/types.ts and the response-derived fixtures.
  # @MX:SPEC: SPEC-TRADING-064
  ```

- `src/trading/watchers/sell_lock.py::guard_sell` (210행 부근).

  ```
  # @MX:WARN: money path. decision_id is Optional by design — the watchdog callers
  #   (position_watchdog.py:361,503) have no decision.
  # @MX:REASON: Making decision_id required here would raise on the watchdog path and
  #   disable duplicate-sell suppression, the exact guard SPEC-042 added.
  # @MX:SPEC: SPEC-TRADING-064
  ```

- `src/trading/watchers/sell_lock.py::set_sell_inflight` (163행 부근).

  ```
  # @MX:WARN: money path. Optional decision_id; position_watchdog.py:512 calls without one.
  # @MX:REASON: The in-flight lock must engage identically with or without a decision id.
  #   Ordering and unconditional engagement are the invariant; the audit payload is not.
  # @MX:SPEC: SPEC-TRADING-064
  ```

- `src/trading/kis/broker_truth.py::clamp_sell_to_confirmed` (129/144행 부근).

  ```
  # @MX:WARN: money path. Clamping to broker-confirmed quantity is the invariant;
  #   the added decision_id only enriches the audit payload.
  # @MX:REASON: SPEC-TRADING-042 made the broker the single source of truth. A refactor
  #   that threads decision_id must not alter the clamp arithmetic or its ordering
  #   relative to submit.
  # @MX:SPEC: SPEC-TRADING-042, SPEC-TRADING-064
  ```

- `src/trading/risk/limits.py::record_breach` (248행 부근).

  ```
  # @MX:NOTE: decision_id is emitted at details top level; details.context keeps the
  #   original payload for backward compatibility with existing readers.
  # @MX:SPEC: SPEC-TRADING-064
  ```

- 신규 `GET /api/decisions/{decision_id}/trace` 핸들러 (`src/trading/dashboard/app.py`).

  ```
  # @MX:NOTE: read-only. No engine writes, no persona re-run, no paid API calls.
  #   Node highlighting derives from audit event_types via the codemap reverse index.
  # @MX:SPEC: SPEC-TRADING-064
  ```

- 커밋된 구조 산출물 로더 + 부패 감지 자기점검 (`codemap.py --selftest` 확장).

  ```
  # @MX:WARN: the committed callgraph artifact is a build product and can go stale.
  # @MX:REASON: ADR-003 chose the committed-artifact option only on the condition that
  #   this selftest fails when the artifact no longer matches source symbols.
  #   Removing or weakening this check silently ships a wrong flow graph.
  # @MX:SPEC: SPEC-TRADING-064
  ```

---

## 관련 SPEC

- **SPEC-TRADING-050** (대시보드 개편, React+Vite+TS) — 본 SPEC이 확장하는 프런트 기반.
  결함 A의 TS 타입/픽스처 드리프트는 그 당시 도입된 계약이 갈라진 결과다.
- **SPEC-TRADING-054** (엔터프라이즈 대시보드) — 읽기 전용·계산 재구현 금지 원칙, 그리고
  "단위 테스트는 그린인데 라이브에서 503"(pd.persona) 거짓그린 선례. REQ-064-A5/A8의 근거.
- **SPEC-TRADING-056** (통합 테스트 레이어) — SQL 변경 시 실 Postgres 검증 게이트.
  그룹 A의 SELECT 변경과 그룹 B의 TIER 4 컬럼 추가가 여기에 걸린다.
- **SPEC-TRADING-063** (주문 거부 관측성) — 같은 계열의 "시스템이 조용해진" 결함
  (`rejected_reason`이 리포트에서 빠져 나흘간 계좌 만료가 안 보임). 본 SPEC의 REQ-064-C8은
  그 `rejected_reason`을 주문 노드 블록에 그대로 노출한다.
- **SPEC-TRADING-042** (broker truth 단일 원장) — `orders`/체결 진실 원천, `clamp_sell_to_confirmed`
  및 in-flight 락의 소유 SPEC. 그룹 B TIER 3/4가 이 경로를 건드리므로 특성화 테스트 선행 대상.
- **SPEC-TRADING-062** — 본 SPEC이 따르는 A/B/C 단계 분리 배포 관례의 선례.
