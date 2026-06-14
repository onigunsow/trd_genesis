// REQ-050-13: 모든 API 응답에 대한 TypeScript 타입 정의

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
  side: 'buy' | 'sell'
  qty: number
  fill_price: number | null
  status: 'filled' | 'submitted' | 'rejected' | 'cancelled' | string
}

// ── /api/holdings ──────────────────────────────────────────────────────────
export interface Holding {
  ticker: string
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
