# SPEC-TRADING-050 (compact) — 전문 인터랙티브 대시보드 개편

상태: draft v0.2.0 · mode: tdd · priority: high · [DELTA] SPEC-047 확장 · 관측 전용

## 한 줄 요약
정적 HTML(251줄·차트 0)을 React+Vite+TS SPA로 교체. 의사결정 파이프라인 가시화 +
자산 통계 차트 + 뉴스 인텔리전스 연결 + 폴링(5~15s). 엔진 write·엣지 계산 미변경.

## 잠긴 결정
- 프론트 = React+Vite+TS. Vite 정적 산출물을 **기존 FastAPI**(`dashboard/app.py`)가 서빙.
- 갱신 = 폴링 5~15초(WebSocket/SSE 아님).
- 차트 라이브러리 = expert-frontend 최종 선택(ECharts/Recharts; 가격류 lightweight-charts).

## 데이터 (검증 완료, file:line)
- 파이프라인: persona_runs(004; cycle_kind/trigger_context/response_json/tokens/
  latency_ms/regime_at_decision[024]), persona_decisions(004+033; **FK=persona_run_id**,
  side/qty/rationale/confidence/prob_bull|base|bear), risk_reviews(004; decision_id FK,
  verdict/rationale/code_rules_passed), portfolio_adjustments(005).
- 상태: system_state(halt_state/current_regime/current_risk_appetite/
  late_cycle_defense_active/late_cycle_level/cool_down_active), audit_log(halt 사유).
- 자산: daily_equity_snapshot(026), edge/analytics·benchmark·confidence·postmortem·
  scorecard(순수 함수, 지연 계산).
- 뉴스: news_articles(014), news_analysis(016; summary_2line/impact_score/sentiment/
  keywords), story_clusters(016; **portfolio_relevant/relevance_tickers**),
  news_trends(016).
- dashboard_ro(mig 032) 전 테이블 SELECT 가능.

## EARS 모듈 (26 REQ — 6a 포함)
- M1 백엔드 확장(REQ-1~8, 6a): 신규 /api/{news,story-clusters,trends,postmortem,
  confidence-analysis,pipeline} + 확장 /api/{decisions+risk LEFT JOIN, status+halt사유/
  cool_down/late_cycle, equity+drawdown}.
  **[D4 단일 접근] postmortem/confidence = 죽은/깨진 SPEC-048 stub(`pd.run_id`) 제거/대체
  → 원시행→어댑터→edge 순수함수(classify_decision_outcome/analyze)→JSON 단일 지연계산
  파이프라인.** REQ-6(stub제거·FK교정)·6a(재사용)·7(어댑터·N일·캐시)이 한 구현을 가리킴.
  REQ-8(Ubiquitous; redaction 범위=제외 자격증명/KIS payload/kis_order_no, 포함 rationale/
  confidence/prob_*/verdict).
- M2 프론트 기반(REQ-9~14): React+Vite+TS, FastAPI 서빙, 폴링 훅(실패 비차단), 다크 테마,
  API 타입, 도커 멀티스테이지 문서.
- M3 의사결정 시각화(REQ-15~18): 파이프라인 다이어그램(macro→micro→decision→risk→
  portfolio→sizing)+가드 상태, 최신 사이클 재구성, 결정 드릴다운(rationale/confidence/
  regime/risk verdict/trigger_context/raw), halt 표시.
- M4 자산 차트(REQ-19~21): 에쿼티/드로다운/수익분포/알파/누적실현손익/confidence 산점도/
  postmortem 4분류/페르소나 귀인 + 호버·줌 + 알파 graceful degrade.
- M5 뉴스 뷰(REQ-22~25): 포트폴리오 관련 클러스터 우선·감성/임팩트/키워드 트렌드, 의사결정
  연결 배지, 관련 필터.

## 핵심 제외
제어 액션 없음 · WebSocket/SSE 없음 · 실시간 스텝 추적(persona_step_progress mig034)은
후속 SPEC · 엔진/엣지 미변경 · 공개 노출 없음 · 인증 신규구축 없음 · 모바일 앱 없음.

## 위험
KOSPI 알파=지수데이터 의존(없으면 degrade) · 지연계산 부하(캐시+N일) · SPEC-048 stub 버그 ·
빌드 통합 · "현재 사이클"=최신 사이클 근사 · 기존 7엔드포인트 회귀 0(필드 추가만).

## DoD
계약 테스트(mock ro_connection) · vite build+서빙 스모크 · 컴포넌트/타입 테스트 ·
쓰기/DDL 0 · 민감필드 0 · 기존 스위트 회귀 0 · 도커 빌드 문서화.
