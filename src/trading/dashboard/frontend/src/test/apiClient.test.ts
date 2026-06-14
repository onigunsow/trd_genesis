// AC-M2-3: API 클라이언트 타입 형태 테스트 (fetch 모킹, 실제 네트워크 없음)
import { vi, describe, it, expect, afterEach } from 'vitest'
import { api } from '../api/client'
import type {
  SystemStatus,
  Decision,
  Order,
  Holding,
  EquityPoint,
  Scorecard,
  NewsArticle,
  StoryCluster,
  TrendPoint,
  PostmortemResult,
  ConfidenceAnalysis,
  PipelineData,
} from '../api/types'

// fetch 전역 모킹
function mockFetch(data: unknown, status = 200) {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(data),
  } as Response)
}

describe('api/client 타입 형태 검증', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('fetchStatus — SystemStatus 형태 반환', async () => {
    const mock: SystemStatus = {
      halt_state: false,
      halt_reason: null,
      trading_mode: 'live',
      current_regime: 'BULL',
      current_risk_appetite: 'MEDIUM',
      late_cycle_defense_active: false,
      late_cycle_level: null,
      cool_down_active: false,
      updated_at: '2026-06-14T10:00:00',
    }
    mockFetch(mock)
    const result = await api.fetchStatus()
    expect(result.halt_state).toBe(false)
    expect(result.trading_mode).toBe('live')
    expect(result.current_regime).toBe('BULL')
  })

  it('fetchDecisions — Decision[] 형태 반환 + risk_verdict 포함', async () => {
    const mock: Decision[] = [
      {
        ts: '2026-06-14T09:00:00',
        persona_name: 'micro',
        cycle_kind: 'intraday',
        ticker: '005930',
        side: 'buy',
        qty: 10,
        confidence: 0.72,
        rationale: '긍정 신호',
        risk_verdict: 'APPROVE',
        risk_rationale: '한도 내',
        prob_bull: 0.6,
        prob_base: 0.3,
        prob_bear: 0.1,
        regime_at_decision: 'BULL',
        trigger_context: 'intraday',
        response_json: null,
      },
    ]
    mockFetch(mock)
    const result = await api.fetchDecisions(10)
    expect(result).toHaveLength(1)
    expect(result[0].risk_verdict).toBe('APPROVE')
    expect(result[0].prob_bull).toBe(0.6)
  })

  it('fetchOrders — Order[] 형태 반환', async () => {
    const mock: Order[] = [
      { ts: '2026-06-14T09:01:00', ticker: '005930', side: 'buy', qty: 10, fill_price: 75000, status: 'filled' },
    ]
    mockFetch(mock)
    const result = await api.fetchOrders(10)
    expect(result[0].ticker).toBe('005930')
    expect(result[0].status).toBe('filled')
  })

  it('fetchHoldings — Holding[] 형태 반환', async () => {
    const mock: Holding[] = [
      { ticker: '005930', qty_net: 10, avg_fill_price: 75000, total_cost: 750000 },
    ]
    mockFetch(mock)
    const result = await api.fetchHoldings()
    expect(result[0].ticker).toBe('005930')
    expect(result[0].qty_net).toBe(10)
  })

  it('fetchEquity — EquityPoint[] drawdown_pct 포함 (REQ-050-5)', async () => {
    const mock: EquityPoint[] = [
      { trading_day: '2026-06-01', total_assets: 10_000_000, stock_eval: 8_000_000, cash: 2_000_000, unrealized_pnl: 100_000, drawdown_pct: -0.02 },
    ]
    mockFetch(mock)
    const result = await api.fetchEquity(90)
    expect(result[0].drawdown_pct).toBe(-0.02)
    expect(result[0].total_assets).toBe(10_000_000)
  })

  it('fetchScorecard — Scorecard 형태 + benchmark_available 옵션 포함', async () => {
    const mock: Scorecard = {
      verdict: 'GO_LIVE', grade: 'B', win_rate: 0.55, expectancy_adj: 0.02,
      profit_factor_adj: 1.3, alpha_pct: -11.03, cagr: 0.08, mdd: -0.12,
      sharpe: 0.9, n_closed: 20, benchmark_available: true,
    }
    mockFetch(mock)
    const result = await api.fetchScorecard()
    expect(result.verdict).toBe('GO_LIVE')
    expect(result.benchmark_available).toBe(true)
  })

  it('fetchStoryClusters — StoryCluster[] portfolio_relevant + relevance_tickers 포함', async () => {
    const mock: StoryCluster[] = [
      {
        id: 1, representative_title: '삼성전자 호실적',
        sector: '반도체', sentiment_dominant: 'positive',
        portfolio_relevant: true, relevance_tickers: ['005930'],
        impact_max: 4, created_at: '2026-06-14T08:00:00',
      },
    ]
    mockFetch(mock)
    const result = await api.fetchStoryClusters(7, 10)
    expect(result[0].portfolio_relevant).toBe(true)
    expect(result[0].relevance_tickers).toContain('005930')
  })

  it('fetchPostmortem — PostmortemResult 4분류 counts 포함', async () => {
    const mock: PostmortemResult = {
      counts: { TP: 5, FP: 2, REGIME_MISMATCH: 1, MISSED: 3 },
      total: 11,
      by_persona: { micro: { TP: 3, FP: 1, REGIME_MISMATCH: 0, MISSED: 1 } },
      days: 30,
    }
    mockFetch(mock)
    const result = await api.fetchPostmortem(30)
    expect(result.counts.TP).toBe(5)
    expect(result.total).toBe(11)
    expect(result.by_persona['micro']?.TP).toBe(3)
  })

  it('fetchConfidenceAnalysis — ConfidenceAnalysis pearson/spearman 포함', async () => {
    const mock: ConfidenceAnalysis = {
      buckets: [{ bucket: '0.5-0.6', count: 10, avg_return: 0.01, win_rate: 0.55 }],
      pearson: 0.12,
      spearman: -0.455,
      days: 30,
    }
    mockFetch(mock)
    const result = await api.fetchConfidenceAnalysis(30)
    expect(result.spearman).toBe(-0.455)
    expect(result.buckets).toHaveLength(1)
  })

  it('fetchPipeline — PipelineData steps 포함', async () => {
    const mock: PipelineData = {
      cycle_id: 'cycle-001',
      cycle_started_at: '2026-06-14T09:00:00',
      steps: [
        { step: 'macro', persona_name: 'macro', cycle_kind: 'pre_market', status: 'completed', latency_ms: 1200, started_at: '2026-06-14T09:00:00', decisions: [], verdicts: [] },
      ],
      halt_state: false,
      halt_reason: null,
    }
    mockFetch(mock)
    const result = await api.fetchPipeline()
    expect(result.steps).toHaveLength(1)
    expect(result.steps[0].step).toBe('macro')
    expect(result.halt_state).toBe(false)
  })

  it('HTTP 오류 시 Error 를 던진다', async () => {
    mockFetch({}, 503)
    await expect(api.fetchStatus()).rejects.toThrow('HTTP 503')
  })

  it('fetchNews — NewsArticle[] impact_score + sentiment 포함', async () => {
    const mock: NewsArticle[] = [
      {
        id: 1, title: '테스트 뉴스', url: null, summary: null,
        summary_2line: '짧은 요약', source_name: '연합뉴스',
        sector: '반도체', published_at: '2026-06-14T08:00:00',
        impact_score: 3, sentiment: 'positive', keywords: ['반도체'],
      },
    ]
    mockFetch(mock)
    const result = await api.fetchNews(7, 10)
    expect(result[0].impact_score).toBe(3)
    expect(result[0].sentiment).toBe('positive')
  })

  it('fetchTrends — TrendPoint[] 감성 분포 포함', async () => {
    const mock: TrendPoint[] = [
      {
        keyword: '반도체', trend_date: '2026-06-14',
        mention_count: 15, sentiment_positive: 8,
        sentiment_neutral: 5, sentiment_negative: 2,
        sentiment_avg: 0.6,
      },
    ]
    mockFetch(mock)
    const result = await api.fetchTrends('daily', 14)
    expect(result[0].keyword).toBe('반도체')
    expect(result[0].sentiment_positive).toBe(8)
  })
})
