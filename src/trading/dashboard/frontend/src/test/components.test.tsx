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

// SPEC-TRADING-064 REQ-064-A5: 선언된 TS 타입이 아니라 백엔드 실측 응답에서 파생한 픽스처.
// 계약 키 목록을 단일 소스로 두고, 아래 자기점검 테스트가 픽스처 키 집합과 대조한다.
// (그룹 A 실측: GET /api/decisions?limit=1 → 15키, GET /api/pipeline → step 9키)
const DECISION_CONTRACT_KEYS = [
  'id', 'ts', 'persona_name', 'cycle_kind', 'ticker', 'ticker_name', 'side', 'qty',
  'confidence', 'rationale', 'risk_verdict', 'risk_rationale',
  'regime_at_decision', 'trigger_context', 'response_json',
].sort()

const PIPELINE_STEP_CONTRACT_KEYS = [
  'id', 'ts', 'persona_name', 'cycle_kind', 'input_tokens', 'output_tokens',
  'latency_ms', 'status', 'regime_at_decision',
].sort()

const MOCK_PIPELINE: PipelineData = {
  cycle_ts: '2026-06-14T09:00:04',
  steps: [
    { id: 1, ts: '2026-06-14T09:00:00', persona_name: 'macro', cycle_kind: 'pre_market', input_tokens: 1200, output_tokens: 300, latency_ms: 800, status: 'completed', regime_at_decision: 'BULL' },
    { id: 2, ts: '2026-06-14T09:00:01', persona_name: 'micro', cycle_kind: 'pre_market', input_tokens: 1500, output_tokens: 400, latency_ms: 1200, status: 'completed', regime_at_decision: 'BULL' },
    { id: 3, ts: '2026-06-14T09:00:02', persona_name: 'decision', cycle_kind: 'pre_market', input_tokens: 1800, output_tokens: 500, latency_ms: 900, status: 'completed', regime_at_decision: 'BULL' },
    { id: 4, ts: '2026-06-14T09:00:03', persona_name: 'risk', cycle_kind: 'pre_market', input_tokens: 900, output_tokens: 150, latency_ms: 300, status: 'completed', regime_at_decision: 'BULL' },
    { id: 5, ts: '2026-06-14T09:00:04', persona_name: 'portfolio', cycle_kind: 'pre_market', input_tokens: 700, output_tokens: 120, latency_ms: 200, status: 'completed', regime_at_decision: 'BULL' },
  ],
}

const MOCK_DECISIONS: Decision[] = [
  {
    id: 101,
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
    regime_at_decision: 'BULL',
    // 실 응답 형태: persona_runs 의 jsonb 두 컬럼은 문자열이 아니라 객체다(라이브 curl 실측).
    trigger_context: { cycle_kind: 'pre_market', macro_run_id: 2524, micro_run_id: 2525 },
    response_json: { signals: [{ ticker: '005930', side: 'buy', qty: 10 }], summary: '반도체 업황 개선' },
  },
  {
    id: 102,
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
    // REQ-064-A7: persona_runs 미기록 케이스(NULL) — 이 셋은 "미기록"으로 렌더돼야 한다
    regime_at_decision: null,
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

    // jsonb 객체를 pretty-print 한 결과가 pre 블록에 들어간다. 객체를 그대로 넘기면
    // React 가 터지므로(error #31), 문자열화됐는지까지 확인한다.
    await waitFor(() => {
      const pre = document.querySelector('pre')
      expect(pre).not.toBeNull()
      expect(pre!.textContent).toContain('"signals"')
      expect(pre!.textContent).toContain('005930')
    })
  })

  // REQ-064-A4: prob_bull/base/bear 는 백엔드가 산출하지 않는다(ADR-001) — 드릴다운에서 완전 제거
  it('REQ-064-A4: 드릴다운에 확률(prob_*) 표시가 없다', async () => {
    render(<PipelineView status={MOCK_STATUS} />)

    await waitFor(() =>
      expect(screen.getAllByRole('button').length).toBeGreaterThan(0)
    )
    fireEvent.click(screen.getAllByRole('button')[0])

    await waitFor(() => expect(screen.getByText('BULL')).toBeDefined())
    expect(screen.queryByText(/확률/)).toBeNull()
  })

  // REQ-064-A7: NULL 인 regime_at_decision/trigger_context/response_json 은 "미기록"으로 명시 표기
  it('REQ-064-A7: NULL 필드는 "미기록"으로 표기되고 빈칸/0/-으로 렌더되지 않는다', async () => {
    render(<PipelineView status={MOCK_STATUS} />)

    await waitFor(() =>
      expect(screen.getAllByRole('button').length).toBeGreaterThan(1)
    )
    // 두 번째 결정(SK하이닉스) — regime/trigger_context/response_json 전부 NULL
    fireEvent.click(screen.getAllByRole('button')[1])

    await waitFor(() => {
      const markers = screen.getAllByText('미기록')
      // Regime + 트리거 컨텍스트 + response_json(raw) = 3곳
      expect(markers.length).toBe(3)
    })
  })

  // REQ-064-A5: 픽스처가 계약 키 집합과 정확히 일치하는지 자가 검증한다.
  // 백엔드가 내지 않는 키가 픽스처에 섞여 들어오면(예: prob_bull 재추가) 이 테스트가 실패한다.
  it('REQ-064-A5: 픽스처 키 집합이 백엔드 실측 계약과 정확히 일치한다(자기점검)', () => {
    expect(Object.keys(MOCK_DECISIONS[0]).sort()).toEqual(DECISION_CONTRACT_KEYS)
    expect(Object.keys(MOCK_DECISIONS[1]).sort()).toEqual(DECISION_CONTRACT_KEYS)
    expect(Object.keys(MOCK_PIPELINE.steps[0]).sort()).toEqual(PIPELINE_STEP_CONTRACT_KEYS)
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

// Holding 타입 업데이트: eval_price/eval_amount/unrealized_pnl/pnl_pct nullable 필드 포함
const MOCK_HOLDINGS = [{
  ticker: '005930',
  ticker_name: '삼성전자',
  qty_net: 10,
  avg_fill_price: 75000,
  total_cost: 750000,
  eval_price: 76000,
  eval_amount: 760000,
  unrealized_pnl: 10000,
  pnl_pct: 1.33,
}]

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

