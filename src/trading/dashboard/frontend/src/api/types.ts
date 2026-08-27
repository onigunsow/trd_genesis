// REQ-054-F1: 모든 API 응답에 대한 TypeScript 타입 정의
// CRITICAL: 필드명/타입이 백엔드 JSON과 정확히 일치해야 함 (black-screen 방지)
// NUMERIC→number 변환은 db.py 로더에서 보장 — 모든 숫자 필드는 number 타입 사용

// ── /api/status ────────────────────────────────────────────────────────────
export interface GateConfig {
  since: string | null   // null = 토글 비활성. env DASHBOARD_GATE_SINCE > audit ACCOUNT_SWITCH(계좌 리셋)
  min_n: number
  source?: 'env' | 'account_switch' | null
}

export interface SystemStatus {
  gate?: GateConfig      // SPEC-065 REQ-065-2b
  halt_state: boolean
  halt_reason: string | null
  trading_mode: string
  current_regime: string
  current_risk_appetite: string
  late_cycle_defense_active: boolean
  late_cycle_level: string | null
  cool_down_active: boolean
  updated_at: string | null
  // SPEC-065 REQ-065-1c 상태줄 (선택 — 백엔드 실패 시 없음)
  last_resolver_run?: string | null
  last_cycle_at?: string | null
  orders_today?: number
  rejected_today?: number
  blocked_today?: number
}

// ── /api/decisions ─────────────────────────────────────────────────────────
// SPEC-TRADING-064 REQ-064-A2: 백엔드 fetch_recent_decisions 반환 키와 1:1 일치
// (queries.py:127-169). 응답에 없는 키(prob_bull/base/bear 등)는 선언 금지 — ADR-001.
export interface Decision {
  id: number
  ts: string
  persona_name: string
  cycle_kind: string
  ticker: string | null
  ticker_name: string | null  // REQ-054-F2: 한국어 종목명 (미등록 시 코드와 동일)
  side: 'buy' | 'sell' | null
  qty: number | null
  confidence: number | null
  rationale: string | null
  // REQ-050-3: risk_reviews LEFT JOIN
  risk_verdict: 'APPROVE' | 'HOLD' | 'REJECT' | null
  risk_rationale: string | null
  // 드릴다운용 추가 필드 (persona_runs JOIN) — NULL 이면 UI 는 "미기록"으로 표기(REQ-064-A7)
  regime_at_decision: string | null
  // trigger_context·response_json 은 persona_runs 의 jsonb 컬럼이라 문자열이 아니라 객체로 온다.
  // 문자열로 선언하면 React 가 객체를 그대로 렌더하려다 터진다(라이브 재현: React error #31).
  trigger_context: Record<string, unknown> | null
  response_json: Record<string, unknown> | null
}

// ── /api/orders ────────────────────────────────────────────────────────────
export interface Order {
  ts: string
  ticker: string
  ticker_name: string  // REQ-054-F2: 한국어 종목명 (미등록 시 코드와 동일)
  side: 'buy' | 'sell'
  qty: number
  fill_price: number | null
  status: 'filled' | 'submitted' | 'rejected' | 'cancelled' | string
}

// ── /api/holdings ──────────────────────────────────────────────────────────
// CRITICAL: eval_price/eval_amount/unrealized_pnl/pnl_pct 는 KIS 잔고 스냅샷에 없는 경우 null
// null 은 브로커-원장 드리프트를 의미 — 절대 fabricate 하지 말 것, "—" 로 표시
export interface Holding {
  ticker: string
  ticker_name: string  // REQ-054-F2: 한국어 종목명 (미등록 시 코드와 동일)
  qty_net: number
  avg_fill_price: number | null
  total_cost: number | null
  eval_price: number | null      // KIS 잔고 스냅샷 현재가 (없으면 null)
  eval_amount: number | null     // 평가금액 = eval_price * qty_net (없으면 null)
  unrealized_pnl: number | null  // 미실현 손익 (없으면 null)
  pnl_pct: number | null         // 손익률 % (예: 6.2 = 6.2%) — 백엔드에서 이미 % 단위
}

// ── /api/equity ────────────────────────────────────────────────────────────
export interface EquityPoint {
  trading_day: string
  total_assets: number
  stock_eval: number | null
  cash: number | null
  unrealized_pnl: number | null
  drawdown_pct: number | null
}

// ── /api/scorecard ─────────────────────────────────────────────────────────
// REQ-054-A4: sortino 필드 추가 (edge.analytics 산출값 노출)
export interface Scorecard {
  verdict: string
  grade: string
  win_rate: number | null
  expectancy_adj: number | null
  profit_factor_adj: number | null
  alpha_pct: number | null
  alpha_basis?: string | null          // 알파 산출 기준(대기 현금 제외 등 한계 포함)
  strategy_return_pct?: number | null  // 알파의 좌변
  kospi_return_pct?: number | null     // 알파의 우변
  n_unmatched_sells?: number   // 짝 없는 매도 — 왕복 지표에서 제외됨
  cagr: number | null
  mdd: number | null
  sharpe: number | null
  sortino: number        // REQ-054-A4: edge.analytics 에서 노출
  n_closed: number
  benchmark_available?: boolean
  reasons?: string[]
  // SPEC-065 REQ-065-2a/2c
  since?: string | null
  low_sample?: boolean
  gate_min_n?: number
}

// ── /api/news ──────────────────────────────────────────────────────────────
export interface NewsArticle {
  id: number
  title: string
  url: string | null
  summary: string | null
  summary_2line: string | null
  source_name: string | null
  sector: string | null
  published_at: string
  impact_score: number | null
  sentiment: string | null
  keywords: string[] | null
}

// ── /api/story-clusters ────────────────────────────────────────────────────
export interface StoryCluster {
  id: number
  representative_title: string
  sector: string | null
  sentiment_dominant: string | null
  portfolio_relevant: boolean
  relevance_tickers: string[] | null
  impact_max: number | null
  created_at: string
}

// ── /api/trends ────────────────────────────────────────────────────────────
export interface TrendPoint {
  keyword: string
  trend_date: string
  mention_count: number
  sentiment_positive: number
  sentiment_neutral: number
  sentiment_negative: number
  sentiment_avg: number | null
}

// ── /api/postmortem ────────────────────────────────────────────────────────
export interface PostmortemResult {
  counts: {
    TP: number
    FP: number
    REGIME_MISMATCH: number
    MISSED: number
  }
  total: number
  by_persona: Record<string, { TP: number; FP: number; REGIME_MISMATCH: number; MISSED: number }>
  days: number
}

// ── /api/confidence-analysis ───────────────────────────────────────────────
// CRITICAL: 백엔드 _bucket_dict 는 "label" 필드를 반환함 (bucket 아님)
// queries.py _bucket_dict: { "label": b.label, "n": b.n, "win_rate": b.win_rate, "avg_return_pct": b.avg_return_pct }
export interface ConfidenceBucket {
  label: string          // 버킷 레이블 (예: "HIGH", "MED-HIGH" 등) — 백엔드 필드명 "label"
  n: number              // 백엔드 필드명 "n" (count 아님)
  avg_return_pct: number | null  // 백엔드 필드명 "avg_return_pct" (avg_return 아님)
  win_rate: number | null
}

export interface ConfidenceAnalysis {
  buckets: ConfidenceBucket[]
  pearson: number | null
  spearman: number | null
  days: number
  n_with_conf?: number
  none_count?: number
}

// ── /api/pipeline ──────────────────────────────────────────────────────────
// SPEC-TRADING-064 REQ-064-A3: fetch_pipeline 반환 키와 1:1 일치(queries.py:939-992).
// status 는 백엔드가 실제로 내는 'error'|'completed' 만 포함한다.
// halt 상태는 이 응답에 없다 — /api/status(SystemStatus)의 halt_state 를 사용할 것.
export interface PipelineStep {
  id: number
  ts: string
  persona_name: string | null
  cycle_kind: string | null
  input_tokens: number | null
  output_tokens: number | null
  latency_ms: number | null
  status: 'error' | 'completed'
  regime_at_decision: string | null
}

export interface PipelineData {
  cycle_ts: string | null
  steps: PipelineStep[]
}

// ── /api/roundtrips ────────────────────────────────────────────────────────
// REQ-054-A1: edge.roundtrips.RoundTrip[] 를 그대로 반영 (ADR-001: persona 포함)
// CRITICAL: 필드명이 백엔드와 정확히 일치해야 함
export interface RoundTrip {
  ticker: string
  ticker_name: string  // REQ-054-F2: 한국어 종목명 (미등록 시 코드와 동일)
  entry_date: string
  exit_date: string
  qty: number
  entry_price: number
  exit_price: number
  net_pnl: number
  return_pct: number
  entry_fee: number
  exit_fee: number
  fees: number
  holding_days: number
  confidence: number | null
  verdict: string | null
  persona: string | null    // ADR-001: edge RoundTrip.persona 확장 적용
  is_win: boolean
}

// ── /api/portfolio ─────────────────────────────────────────────────────────
// REQ-054-A2: position_eval_snapshot + equity + ticker_metadata 조인 결과
// CRITICAL: NUMERIC→number 변환은 db.py 로더 보장
export interface PortfolioHolding {
  ticker: string
  ticker_name: string  // REQ-054-F2: 한국어 종목명 (미등록 시 코드와 동일)
  qty: number
  avg_cost: number
  eval_price: number
  eval_amount: number
  unrealized_pnl: number
  pnl_pct: number
  weight_pct: number
  sector: string
}

export interface SectorBreakdown {
  sector: string
  weight_pct: number
}

export interface PortfolioData {
  nav_date?: string | null
  nav_date_mismatch?: boolean
  holdings: PortfolioHolding[]
  nav: number
  cash_amount: number
  cash_ratio: number
  herfindahl: number
  top3_pct: number
  sector_breakdown: SectorBreakdown[]
  snapshot_date: string | null
}

// ── /api/decisions/{id}/trace ──────────────────────────────────────────────
// SPEC-TRADING-064 REQ-064-C1: 결정 하나의 추적 페이로드.
// state 는 REQ-064-C2 의 네 상태만 허용한다 — 빈칸이 "통과"로 읽혀선 안 된다.
export type TraceNodeState = 'recorded' | 'decision_agnostic' | 'rule_based' | 'not_involved'

export interface TraceEvent {
  event_type: string
  ts: string
  actor: string
  // jsonb 컬럼 — Decision.trigger_context 와 같은 이유로 객체로 선언한다(React error #31 회피).
  details: Record<string, unknown>
}

export interface TraceNode {
  file: string
  function: string
  module: string
  state: TraceNodeState
  events: TraceEvent[]
}

// REQ-064-C10: origin==='rule_based' 는 "규칙 기반 실행(LLM 결정 없음)" — "기록 없음"과 다르다.
export interface TraceOrder {
  id: number
  ts: string
  side: 'buy' | 'sell'
  ticker: string
  qty: number
  status: string
  rejected_reason: string | null
  fill_price: number | null
  fill_qty: number | null
  synthetic: boolean
  correction: boolean
  origin: 'decision' | 'rule_based'
}

export interface DecisionTrace {
  decision: Decision
  nodes: TraceNode[]
  orders: TraceOrder[]
  unmatched_events: TraceEvent[]
}

// ── /api/pnl-daily ─────────────────────────────────────────────────────────
// REQ-054-A3: 기간별 실현손익 + 누적 + KOSPI 상대
// 주의: alpha_pct 는 백엔드 한계로 현재 null 반환 — UI 는 null 을 그대로 표시 (가짜 데이터 금지)
export interface PnlDailyRow {
  period_label: string
  realized_pnl: number
  cumulative_pnl: number
  alpha_pct: number | null   // 현재 백엔드 한계로 null — 전체기간 알파는 scorecard 에서 별도 표시
}

export interface PnlDailyResponse {
  period: string
  benchmark_available: boolean
  rows: PnlDailyRow[]
}


// ── SPEC-065 그룹 3: /api/gate/* ─────────────────────────────────────────────
export interface HoldingBucket {
  bucket: string
  n: number
  win_rate: number | null
  avg_return_pct: number | null
  sum_net_pnl: number
}
export interface HoldingPeriodPnl {
  since: string | null
  n_total: number
  buckets: HoldingBucket[]
}

export interface EntryQualityCell {
  conf_bucket: number
  freshness: string          // early | confirmed | late | unlabeled
  n: number
  n_with_horizon: number
  ret_20d: number | null
  ret_40d: number | null
  win_20d: number | null
}
export interface EntryQualityMatrix {
  since: string | null
  horizons: number[]
  basis: string              // "all_buy_decisions" — 체결 무관
  cells: EntryQualityCell[]
}

export interface RiskVerdicts {
  since: string | null
  n: number
  verdicts: Record<string, number>
  hold_reasons: { reason: string; n: number; share: number | null }[]
  hold_counterfactual: { n: number; ret_20d: number | null; ret_40d: number | null }
  execution_reach_share: number | null   // risk APPROVE 중 실제 주문 도달 비율
  execution_reach_n: number
  horizons: number[]
}

export interface SizingGateRow {
  gate: string
  n: number
  avg_cut_pct: number | null
}
export interface SizingRecent {
  ts: string
  gate: string
  ticker: string | null
  qty_original: number | null
  qty_adjusted: number | null
  reason: string | null
  decision_id: number | null
}
export interface SizingGates {
  since: string | null
  gates: SizingGateRow[]
  recent: SizingRecent[]
  n_events: number
}

/* 진입 근거 귀속 (2026-08-27) — 종목이 아니라 판단을 채점한다.
   머리기사(flow_cohorts)만 읽으면 시장 방향을 수급으로 오독한다. 화면은
   by_month·sign_flip_months·regime_robust 를 반드시 함께 보여줘야 한다. */
export interface AttrCohort {
  label: string
  n: number
  n_scored: number
  ret: number | null
  median: number | null
  win: number | null
}
export interface AttrMonth {
  month: string
  inflow: AttrCohort
  outflow: AttrCohort
  comparable: boolean
  sign_flipped: boolean
}
export interface EntryAttribution {
  since: string | null
  horizon_trading_days: number
  flow_window_trading_days: number
  basis: string
  n_total: number
  n_scored: number
  flow_cohorts: AttrCohort[]
  by_month: AttrMonth[]
  sign_flip_months: number
  months_comparable: number
  regime_robust: boolean
  confidence_definition_boundary: string
  confidence_cohorts: {
    old_definition: AttrCohort[]
    new_definition: AttrCohort[]
  }
}
