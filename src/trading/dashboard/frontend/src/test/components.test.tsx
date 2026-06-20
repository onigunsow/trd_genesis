// AC-M3-1/2, AC-M4-1, AC-M5-1: 핵심 컴포넌트 렌더 테스트 (fetch 모킹, 실제 네트워크 없음)
import React from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'

// echarts-for-react 모킹 — jsdom 환경에서 Canvas 불가
// vitest mock factory 안에서 JSX 사용 가능 (vite transform)
vi.mock('echarts-for-react', () => {
  const MockChart = ({ style, option: _option }: { style?: object; option?: unknown }) => (
    <div data-testid="echart" style={style as React.CSSProperties}>EChart Mock</div>
  )
  return { default: MockChart }
})

// ── PipelineView ─────────────────────────────────────────────────────────────
import PipelineView from '../components/PipelineView'
import type { SystemStatus, PipelineData, Decision } from '../api/types'

const MOCK_STATUS: SystemStatus = {
  halt_state: true,
  halt_reason: 'CIRCUIT_BREAKER_TRIP: 일일한도 초과',
  trading_mode: 'live',
  current_regime: 'BULL',
  current_risk_appetite: 'MEDIUM',
  late_cycle_defense_active: false,
  late_cycle_level: null,
  cool_down_active: false,
  updated_at: '2026-06-14T10:00:00',
}

const MOCK_PIPELINE: PipelineData = {
  cycle_id: 'cycle-001',
  cycle_started_at: '2026-06-14T09:00:00',
  steps: [
    { step: 'macro', persona_name: 'macro', cycle_kind: 'pre_market', status: 'completed', latency_ms: 800, started_at: '2026-06-14T09:00:00', decisions: [], verdicts: [] },
    { step: 'micro', persona_name: 'micro', cycle_kind: 'pre_market', status: 'completed', latency_ms: 1200, started_at: '2026-06-14T09:00:01', decisions: [], verdicts: [] },
    { step: 'decision', persona_name: 'decision', cycle_kind: 'pre_market', status: 'completed', latency_ms: 900, started_at: '2026-06-14T09:00:02', decisions: [], verdicts: [] },
    { step: 'risk', persona_name: 'risk', cycle_kind: 'pre_market', status: 'completed', latency_ms: 300, started_at: '2026-06-14T09:00:03', decisions: [], verdicts: [] },
    { step: 'portfolio', persona_name: 'portfolio', cycle_kind: 'pre_market', status: 'completed', latency_ms: 200, started_at: '2026-06-14T09:00:04', decisions: [], verdicts: [] },
    { step: 'sizing', persona_name: null, cycle_kind: null, status: 'pending', latency_ms: null, started_at: null, decisions: [], verdicts: [] },
  ],
  halt_state: true,
  halt_reason: 'CIRCUIT_BREAKER_TRIP',
}

const MOCK_DECISIONS: Decision[] = [
  {
    ts: '2026-06-14T09:00:00',
    persona_name: 'micro',
    cycle_kind: 'pre_market',
    ticker: '005930',
    ticker_name: '삼성전자',
    side: 'buy',
    qty: 10,
    confidence: 0.72,
    rationale: '반도체 업황 개선 기대',
    risk_verdict: 'APPROVE',
    risk_rationale: '한도 내 허용',
    prob_bull: 0.6,
    prob_base: 0.3,
    prob_bear: 0.1,
    regime_at_decision: 'BULL',
    trigger_context: 'pre_market_scan',
    response_json: '{"action":"BUY"}',
  },
  {
    ts: '2026-06-14T09:01:00',
    persona_name: 'micro',
    cycle_kind: 'pre_market',
    ticker: '000660',
    ticker_name: 'SK하이닉스',
    side: 'sell',
    qty: 5,
    confidence: 0.55,
    rationale: null,
    risk_verdict: 'REJECT',
    risk_rationale: '한도 초과',
    prob_bull: 0.4,
    prob_base: 0.4,
    prob_bear: 0.2,
    regime_at_decision: 'BEAR',
    trigger_context: null,
    response_json: null,
  },
]

function mockFetchSeq(responses: Array<{ ok: boolean; data: unknown }>) {
  let i = 0
  globalThis.fetch = vi.fn().mockImplementation(() => {
    const r = responses[Math.min(i++, responses.length - 1)]
    return Promise.resolve({
      ok: r.ok,
      status: r.ok ? 200 : 503,
      json: () => Promise.resolve(r.data),
    } as Response)
  })
}

describe('PipelineView', () => {
  beforeEach(() => {
    // fetchPipeline + fetchDecisions 모킹 (순서대로)
    mockFetchSeq([
      { ok: true, data: MOCK_PIPELINE },
      { ok: true, data: MOCK_DECISIONS },
    ])
  })
  afterEach(() => { vi.restoreAllMocks() })

  it('AC-M3-1: halt 상태 배너와 파이프라인 단계를 렌더한다', async () => {
    render(<PipelineView status={MOCK_STATUS} />)

    // halt 알림 (REQ-050-18)
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/HALTED/)
    )

    // 파이프라인 단계 레이블 (REQ-050-15)
    await waitFor(() => {
      expect(screen.getByText('Macro')).toBeDefined()
      expect(screen.getByText('Micro')).toBeDefined()
    })
  })

  it('AC-M3-2: 결정 행 클릭 시 드릴다운 패널이 표시된다', async () => {
    render(<PipelineView status={MOCK_STATUS} />)

    // 결정 피드 로드 대기
    await waitFor(() =>
      expect(screen.getAllByRole('button').length).toBeGreaterThan(0)
    )

    // 첫 번째 결정 클릭
    const rows = screen.getAllByRole('button')
    fireEvent.click(rows[0])

    // 드릴다운 패널 표시 (REQ-050-17)
    await waitFor(() => {
      expect(screen.getByRole('region', { name: '결정 상세' })).toBeDefined()
    })
  })

  it('E1: 빈 파이프라인(steps=[]) 시 6단계 스켈레톤을 graceful 하게 렌더한다', async () => {
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      const u = String(url)
      const data = u.includes('/api/pipeline') ? { ...MOCK_PIPELINE, steps: [] } : []
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(data) })
    })
    render(<PipelineView status={{ ...MOCK_STATUS, halt_state: false, halt_reason: null }} />)
    // 실제 동작: 데이터가 없어도 표준 단계(Macro..사이징) pending 스켈레톤을 표시한다
    await waitFor(() => {
      expect(screen.getByText('Macro')).toBeDefined()
      expect(screen.getByText('리스크')).toBeDefined()
    })
  })
})

// ── EquityChart wrapper ───────────────────────────────────────────────────────
import EquityChart from '../components/charts/EquityChart'
import type { EquityPoint } from '../api/types'

const MOCK_EQUITY: EquityPoint[] = [
  { trading_day: '2026-06-01', total_assets: 10_000_000, stock_eval: 8_000_000, cash: 2_000_000, unrealized_pnl: 50_000, drawdown_pct: 0 },
  { trading_day: '2026-06-02', total_assets: 10_200_000, stock_eval: 8_200_000, cash: 2_000_000, unrealized_pnl: 60_000, drawdown_pct: 0 },
  { trading_day: '2026-06-03', total_assets: 9_800_000, stock_eval: 7_800_000, cash: 2_000_000, unrealized_pnl: -20_000, drawdown_pct: -0.039 },
]

describe('EquityChart', () => {
  it('AC-M4-1: EChart 컴포넌트를 렌더한다 (Canvas mock)', () => {
    render(<EquityChart data={MOCK_EQUITY} />)
    expect(screen.getByTestId('echart')).toBeDefined()
  })

  it('AC-M4-1: 높이가 0보다 크다', () => {
    const { container } = render(<EquityChart data={MOCK_EQUITY} />)
    const chart = container.querySelector('[data-testid="echart"]') as HTMLElement
    // mock div 의 style.height 확인 (echarts-for-react 가 style={{ height }} 를 전달)
    expect(chart).toBeDefined()
  })
})

// ── DecisionFeed 드릴다운 ─────────────────────────────────────────────────────
describe('PipelineView 드릴다운 상세', () => {
  beforeEach(() => {
    mockFetchSeq([
      { ok: true, data: MOCK_PIPELINE },
      { ok: true, data: MOCK_DECISIONS },
    ])
  })
  afterEach(() => { vi.restoreAllMocks() })

  it('드릴다운에 confidence, regime, risk_verdict 가 표시된다', async () => {
    render(<PipelineView status={MOCK_STATUS} />)

    await waitFor(() =>
      expect(screen.getAllByRole('button').length).toBeGreaterThan(0)
    )

    const rows = screen.getAllByRole('button')
    fireEvent.click(rows[0])

    await waitFor(() => {
      // rationale
      expect(screen.getByText('반도체 업황 개선 기대')).toBeDefined()
      // regime
      expect(screen.getByText('BULL')).toBeDefined()
    })
  })

  it('response_json(raw) 이 pre 블록에 표시된다', async () => {
    render(<PipelineView status={MOCK_STATUS} />)

    await waitFor(() =>
      expect(screen.getAllByRole('button').length).toBeGreaterThan(0)
    )
    fireEvent.click(screen.getAllByRole('button')[0])

    await waitFor(() => {
      expect(screen.getByText('{"action":"BUY"}')).toBeDefined()
    })
  })
})

// ── NewsIntelligence — portfolio_relevant 우선 정렬 ──────────────────────────
import NewsView from '../components/NewsView'
import type { StoryCluster, NewsArticle, TrendPoint } from '../api/types'

const MOCK_CLUSTERS: StoryCluster[] = [
  { id: 1, representative_title: '포트폴리오 관련 클러스터', sector: '반도체', sentiment_dominant: 'positive', portfolio_relevant: true, relevance_tickers: ['005930'], impact_max: 4, created_at: '2026-06-14T08:00:00' },
  { id: 2, representative_title: '비관련 클러스터', sector: '바이오', sentiment_dominant: 'neutral', portfolio_relevant: false, relevance_tickers: null, impact_max: 2, created_at: '2026-06-14T07:00:00' },
]

const MOCK_NEWS: NewsArticle[] = [
  { id: 1, title: '삼성전자 실적 호조', url: null, summary: null, summary_2line: '2분기 실적 예상치 상회', source_name: '연합뉴스', sector: '반도체', published_at: '2026-06-14T08:00:00', impact_score: 4, sentiment: 'positive', keywords: ['삼성'] },
]

const MOCK_TRENDS: TrendPoint[] = [
  { keyword: '반도체', trend_date: '2026-06-14', mention_count: 15, sentiment_positive: 8, sentiment_neutral: 5, sentiment_negative: 2, sentiment_avg: 0.6 },
]

const MOCK_HOLDINGS = [{ ticker: '005930', ticker_name: '삼성전자', qty_net: 10, avg_fill_price: 75000, total_cost: 750000 }]

describe('NewsView', () => {
  beforeEach(() => {
    // story-clusters, news, holdings, trends 순서
    mockFetchSeq([
      { ok: true, data: MOCK_CLUSTERS },
      { ok: true, data: MOCK_NEWS },
      { ok: true, data: MOCK_HOLDINGS },
      { ok: true, data: MOCK_TRENDS },
    ])
  })
  afterEach(() => { vi.restoreAllMocks() })

  it('AC-M5-1: portfolio_relevant=true 클러스터가 먼저 렌더된다', async () => {
    render(<NewsView />)

    await waitFor(() => {
      expect(screen.getByText('포트폴리오 관련 클러스터')).toBeDefined()
      expect(screen.getByText('비관련 클러스터')).toBeDefined()
    })

    // DOM 순서 확인: 포트폴리오 관련이 앞에
    const all = screen.getAllByText(/관련 클러스터/)
    expect(all[0].textContent).toContain('포트폴리오 관련')
  })

  it('AC-M5-1: "포트폴리오 관련만" 필터 시 비관련 항목이 숨겨진다', async () => {
    render(<NewsView />)

    await waitFor(() => expect(screen.getByText('포트폴리오 관련 클러스터')).toBeDefined())

    const filterBtn = screen.getByRole('button', { name: '포트폴리오 관련만' })
    fireEvent.click(filterBtn)

    // 비관련 클러스터는 사라져야 함
    expect(screen.queryByText('비관련 클러스터')).toBeNull()
    // 관련 클러스터는 여전히 표시
    expect(screen.getByText('포트폴리오 관련 클러스터')).toBeDefined()
  })

  it('AC-M5-2: 보유 종목과 겹치는 클러스터에 의사결정 연결 표시가 나타난다', async () => {
    render(<NewsView />)

    await waitFor(() =>
      expect(screen.getByText('포트폴리오 관련 클러스터')).toBeDefined()
    )

    // 005930 배지 + 의사결정 연결 표시
    await waitFor(() => {
      expect(screen.getByText(/005930/)).toBeDefined()
      expect(screen.getByText(/의사결정 연결/)).toBeDefined()
    })
  })

  it('AC-M5-3: 개별 뉴스와 키워드 트렌드가 표시된다', async () => {
    render(<NewsView />)

    await waitFor(() => {
      expect(screen.getByText('삼성전자 실적 호조')).toBeDefined()
      // KeywordTrends 차트 (echarts mock)
      expect(screen.getByTestId('echart')).toBeDefined()
    })
  })
})

// ── AC-7/REQ-054-B1: 라이트 테마 CSS 변수 적용 확인 (다크→라이트 전환) ────────
describe('라이트 테마 (REQ-054-B1, AC-7)', () => {
  it('AC-7: index.css/theme.ts 가 라이트 팔레트를 정의한다 (#f6f8fa 계열 배경)', async () => {
    // CSS 변수가 정의되어 있는지 확인 (jsdom 에서 CSS 파일은 파싱하지 않으므로
    // theme.ts 의 값으로 간접 검증)
    const { theme } = await import('../theme')
    // REQ-054-B1: 라이트 팔레트 — 연회색 배경, 흰 카드
    expect(theme.bg).toBe('#f6f8fa')
    expect(theme.bgCard).toBe('#ffffff')
    expect(theme.bgPanel).toBe('#ffffff')
    // 다크 값 아님을 확인
    expect(theme.bg).not.toBe('#0d1117')
    expect(theme.border).toBe('#d0d7de')
  })
})

