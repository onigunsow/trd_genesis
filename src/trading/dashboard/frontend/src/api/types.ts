// REQ-054-F1: 모든 API 응답에 대한 TypeScript 타입 정의
// CRITICAL: 필드명/타입이 백엔드 JSON과 정확히 일치해야 함 (black-screen 방지)
// NUMERIC→number 변환은 db.py 로더에서 보장 — 모든 숫자 필드는 number 타입 사용

// ── /api/status ────────────────────────────────────────────────────────────
export interface SystemStatus {
  halt_state: boolean
  halt_reason: string | null
  trading_mode: string
  current_regime: string
  current_risk_appetite: string
  late_cycle_defense_active: boolean
  late_cycle_level: string | null
  cool_down_active: boolean
  updated_at: string | null
}

// ── /api/decisions ─────────────────────────────────────────────────────────
export interface Decision {
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
  prob_bull: number | null
  prob_base: number | null
  prob_bear: number | null
  // 드릴다운용 추가 필드
  regime_at_decision: string | null
  trigger_context: string | null
  response_json: string | null
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
export interface Holding {
  ticker: string
  ticker_name: string  // REQ-054-F2: 한국어 종목명 (미등록 시 코드와 동일)
  qty_net: number
  avg_fill_price: number | null
  total_cost: number | null
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
  cagr: number | null
  mdd: number | null
  sharpe: number | null
  sortino: number        // REQ-054-A4: edge.analytics 에서 노출
  n_closed: number
  benchmark_available?: boolean
  reasons?: string[]
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
export interface ConfidenceBucket {
  bucket: string
  count: number
  avg_return: number | null
  win_rate: number | null
}

export interface ConfidenceAnalysis {
  buckets: ConfidenceBucket[]
  pearson: number | null
  spearman: number | null
  days: number
}

// ── /api/pipeline ──────────────────────────────────────────────────────────
export interface PipelineStep {
  step: string
  persona_name: string | null
  cycle_kind: string | null
  status: 'completed' | 'running' | 'skipped' | 'pending'
  latency_ms: number | null
  started_at: string | null
  decisions: Decision[]
  verdicts: Array<{ verdict: string; rationale: string | null }>
}

export interface PipelineData {
  cycle_id: string | null
  cycle_started_at: string | null
  steps: PipelineStep[]
  halt_state: boolean
  halt_reason: string | null
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
  holdings: PortfolioHolding[]
  nav: number
  cash_amount: number
  cash_ratio: number
  herfindahl: number
  top3_pct: number
  sector_breakdown: SectorBreakdown[]
  snapshot_date: string | null
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
