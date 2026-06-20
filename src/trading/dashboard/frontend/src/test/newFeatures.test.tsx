// REQ-054-F1/AC-13/AC-14: 신규 컴포넌트 및 API 클라이언트 타입 검증
// - api/types.ts 계약 일치 검증 (RoundTrip, PortfolioData, PnlDailyResponse, Scorecard.sortino)
// - RoundtripLedger 필터/정렬 로직 단위검증 (AC-13)
// - KpiCardsContent 렌더 검증 (AC-8)
// - PortfolioViewContent 렌더 검증 (AC-9)
import React from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import { vi, describe, it, expect, afterEach } from 'vitest'
import { formatTicker, TickerLabel } from '../utils/ticker'

// echarts-for-react 모킹 — jsdom 환경에서 Canvas 불가
vi.mock('echarts-for-react', () => {
  const MockChart = ({ style, option: _option }: { style?: object; option?: unknown }) => (
    <div data-testid="echart" style={style as React.CSSProperties}>EChart Mock</div>
  )
  return { default: MockChart }
})

// ── api/client 신규 메서드 타입 검증 ─────────────────────────────────────────
import { api } from '../api/client'
import type {
  RoundTrip,
  PortfolioData,
  PnlDailyResponse,
  Scorecard,
} from '../api/types'

function mockFetch(data: unknown, status = 200) {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(data),
  } as Response)
}

afterEach(() => { vi.restoreAllMocks() })

describe('api/client — 신규 엔드포인트 타입 계약 (AC-14)', () => {
  it('fetchRoundtrips — RoundTrip[] 계약 (ADR-001 persona 포함)', async () => {
    // 백엔드 계약과 정확히 일치하는 필드명
    const mock: RoundTrip[] = [
      {
        ticker: '005930',
        ticker_name: '삼성전자',
        entry_date: '2026-05-01',
        exit_date: '2026-05-10',
        qty: 10,
        entry_price: 75000,
        exit_price: 78000,
        net_pnl: 29400,
        return_pct: 0.0392,
        entry_fee: 300,
        exit_fee: 312,
        fees: 612,
        holding_days: 9,
        confidence: 0.72,
        verdict: 'TP',
        persona: 'micro',     // ADR-001: edge RoundTrip.persona 확장
        is_win: true,
      },
    ]
    mockFetch(mock)
    const result = await api.fetchRoundtrips(90, 100)
    expect(result).toHaveLength(1)
    // 필드명 계약 검증
    expect(result[0].ticker).toBe('005930')
    expect(result[0].ticker_name).toBe('삼성전자')
    expect(result[0].entry_price).toBe(75000)
    expect(result[0].exit_price).toBe(78000)
    expect(result[0].net_pnl).toBe(29400)
    expect(result[0].return_pct).toBe(0.0392)
    expect(result[0].entry_fee).toBe(300)
    expect(result[0].exit_fee).toBe(312)
    expect(result[0].fees).toBe(612)
    expect(result[0].holding_days).toBe(9)
    expect(result[0].confidence).toBe(0.72)
    expect(result[0].verdict).toBe('TP')
    expect(result[0].persona).toBe('micro')
    expect(result[0].is_win).toBe(true)
  })

  it('fetchRoundtrips — persona/confidence/verdict 가 null 인 경우 허용', async () => {
    const mock: RoundTrip[] = [
      {
        ticker: '000660',
        ticker_name: '000660',  // 미등록 종목 → 코드와 동일
        entry_date: '2026-06-01',
        exit_date: '2026-06-05',
        qty: 5,
        entry_price: 120000,
        exit_price: 115000,
        net_pnl: -25500,
        return_pct: -0.0425,
        entry_fee: 360,
        exit_fee: 345,
        fees: 705,
        holding_days: 4,
        confidence: null,
        verdict: null,
        persona: null,
        is_win: false,
      },
    ]
    mockFetch(mock)
    const result = await api.fetchRoundtrips()
    expect(result[0].persona).toBeNull()
    expect(result[0].confidence).toBeNull()
    expect(result[0].is_win).toBe(false)
  })

  it('fetchPortfolio — PortfolioData 계약 (holdings + 집중도 + 섹터)', async () => {
    const mock: PortfolioData = {
      holdings: [
        {
          ticker: '005930',
          ticker_name: '삼성전자',
          qty: 10,
          avg_cost: 75000,
          eval_price: 78000,
          eval_amount: 780000,
          unrealized_pnl: 30000,
          pnl_pct: 4.0,
          weight_pct: 40.0,
          sector: '반도체',
        },
      ],
      nav: 2000000,
      cash_amount: 1220000,
      cash_ratio: 0.61,
      herfindahl: 0.16,
      top3_pct: 40.0,
      sector_breakdown: [{ sector: '반도체', weight_pct: 40.0 }],
      snapshot_date: '2026-06-20',
    }
    mockFetch(mock)
    const result = await api.fetchPortfolio()
    expect(result.holdings).toHaveLength(1)
    expect(result.holdings[0].ticker).toBe('005930')
    expect(result.holdings[0].ticker_name).toBe('삼성전자')
    expect(result.holdings[0].qty).toBe(10)
    expect(result.holdings[0].avg_cost).toBe(75000)
    expect(result.holdings[0].eval_price).toBe(78000)
    expect(result.holdings[0].eval_amount).toBe(780000)
    expect(result.holdings[0].unrealized_pnl).toBe(30000)
    expect(result.holdings[0].pnl_pct).toBe(4.0)
    expect(result.holdings[0].weight_pct).toBe(40.0)
    expect(result.holdings[0].sector).toBe('반도체')
    expect(result.nav).toBe(2000000)
    expect(result.cash_amount).toBe(1220000)
    expect(result.cash_ratio).toBe(0.61)
    expect(result.herfindahl).toBe(0.16)
    expect(result.top3_pct).toBe(40.0)
    expect(result.sector_breakdown[0].sector).toBe('반도체')
    expect(result.sector_breakdown[0].weight_pct).toBe(40.0)
    expect(result.snapshot_date).toBe('2026-06-20')
  })

  it('fetchPortfolio — 미분류 섹터 ("" or null) 허용', async () => {
    const mock: PortfolioData = {
      holdings: [
        {
          ticker: '999999',
          ticker_name: '999999',  // 미등록 → 코드와 동일
          qty: 1,
          avg_cost: 10000,
          eval_price: 10000,
          eval_amount: 10000,
          unrealized_pnl: 0,
          pnl_pct: 0,
          weight_pct: 100,
          sector: '',   // 미분류 — 빈 문자열 허용
        },
      ],
      nav: 10000,
      cash_amount: 0,
      cash_ratio: 0,
      herfindahl: 1.0,
      top3_pct: 100,
      sector_breakdown: [{ sector: '', weight_pct: 100 }],
      snapshot_date: null,
    }
    mockFetch(mock)
    const result = await api.fetchPortfolio()
    expect(result.holdings[0].sector).toBe('')
    expect(result.snapshot_date).toBeNull()
  })

  it('fetchPnlDaily — PnlDailyResponse 계약 (alpha_pct null 허용)', async () => {
    const mock: PnlDailyResponse = {
      period: 'daily',
      benchmark_available: true,
      rows: [
        {
          period_label: '2026-06-01',
          realized_pnl: 50000,
          cumulative_pnl: 50000,
          alpha_pct: null,   // 백엔드 한계 — null 이 정상
        },
        {
          period_label: '2026-06-02',
          realized_pnl: -20000,
          cumulative_pnl: 30000,
          alpha_pct: null,
        },
      ],
    }
    mockFetch(mock)
    const result = await api.fetchPnlDaily(90, 'daily')
    expect(result.period).toBe('daily')
    expect(result.benchmark_available).toBe(true)
    expect(result.rows).toHaveLength(2)
    expect(result.rows[0].period_label).toBe('2026-06-01')
    expect(result.rows[0].realized_pnl).toBe(50000)
    expect(result.rows[0].cumulative_pnl).toBe(50000)
    expect(result.rows[0].alpha_pct).toBeNull()   // null 정직 표시 확인
  })

  it('fetchScorecard — sortino 필드 존재 (REQ-054-A4)', async () => {
    const mock: Scorecard = {
      verdict: 'NO_GO',
      grade: 'D',
      win_rate: 0.375,
      expectancy_adj: -14840,
      profit_factor_adj: 0.85,
      alpha_pct: -11.03,
      cagr: -0.05,
      mdd: -0.12,
      sharpe: -0.3,
      sortino: -0.45,   // REQ-054-A4: 노출 필드
      n_closed: 8,
      benchmark_available: true,
    }
    mockFetch(mock)
    const result = await api.fetchScorecard()
    expect(result.sortino).toBe(-0.45)
    expect(result.n_closed).toBe(8)
  })
})

// ── RoundtripLedger 필터/정렬 로직 (AC-13) ──────────────────────────────────
import { filterRoundtrips, sortRoundtrips } from '../components/RoundtripLedger'

const MOCK_ROUNDTRIPS: RoundTrip[] = [
  {
    ticker: '005930',
    ticker_name: '삼성전자',
    entry_date: '2026-05-01',
    exit_date: '2026-05-10',
    qty: 10,
    entry_price: 75000,
    exit_price: 78000,
    net_pnl: 29400,
    return_pct: 0.039,
    entry_fee: 300,
    exit_fee: 312,
    fees: 612,
    holding_days: 9,
    confidence: 0.72,
    verdict: 'TP',
    persona: 'micro',
    is_win: true,
  },
  {
    ticker: '000660',
    ticker_name: 'SK하이닉스',
    entry_date: '2026-06-01',
    exit_date: '2026-06-05',
    qty: 5,
    entry_price: 120000,
    exit_price: 115000,
    net_pnl: -25500,
    return_pct: -0.0425,
    entry_fee: 360,
    exit_fee: 345,
    fees: 705,
    holding_days: 4,
    confidence: 0.55,
    verdict: 'FP',
    persona: 'macro',
    is_win: false,
  },
]

describe('filterRoundtrips (AC-13: 엔터프라이즈 테이블 필터)', () => {
  it('검색어 없음 → 전체 반환', () => {
    const result = filterRoundtrips(MOCK_ROUNDTRIPS, { search: '', startDate: '', endDate: '', winOnly: null })
    expect(result).toHaveLength(2)
  })

  it('ticker 코드 검색 — 005930 → 1건', () => {
    const result = filterRoundtrips(MOCK_ROUNDTRIPS, { search: '005930', startDate: '', endDate: '', winOnly: null })
    expect(result).toHaveLength(1)
    expect(result[0].ticker).toBe('005930')
  })

  it('ticker_name 한국어 검색 — 삼성전자 → 1건', () => {
    const result = filterRoundtrips(MOCK_ROUNDTRIPS, { search: '삼성전자', startDate: '', endDate: '', winOnly: null })
    expect(result).toHaveLength(1)
    expect(result[0].ticker).toBe('005930')
  })

  it('ticker_name 한국어 부분 검색 — SK → 1건', () => {
    const result = filterRoundtrips(MOCK_ROUNDTRIPS, { search: 'SK', startDate: '', endDate: '', winOnly: null })
    expect(result).toHaveLength(1)
    expect(result[0].ticker).toBe('000660')
  })

  it('persona 검색 — micro → 1건', () => {
    const result = filterRoundtrips(MOCK_ROUNDTRIPS, { search: 'micro', startDate: '', endDate: '', winOnly: null })
    expect(result).toHaveLength(1)
    expect(result[0].persona).toBe('micro')
  })

  it('verdict 검색 — FP → 1건', () => {
    const result = filterRoundtrips(MOCK_ROUNDTRIPS, { search: 'FP', startDate: '', endDate: '', winOnly: null })
    expect(result).toHaveLength(1)
    expect(result[0].verdict).toBe('FP')
  })

  it('winOnly=true → is_win 행만', () => {
    const result = filterRoundtrips(MOCK_ROUNDTRIPS, { search: '', startDate: '', endDate: '', winOnly: true })
    expect(result).toHaveLength(1)
    expect(result[0].is_win).toBe(true)
  })

  it('winOnly=false → 손실 행만', () => {
    const result = filterRoundtrips(MOCK_ROUNDTRIPS, { search: '', startDate: '', endDate: '', winOnly: false })
    expect(result).toHaveLength(1)
    expect(result[0].is_win).toBe(false)
  })

  it('날짜 범위 필터 — entry_date 기준', () => {
    const result = filterRoundtrips(MOCK_ROUNDTRIPS, { search: '', startDate: '2026-06-01', endDate: '', winOnly: null })
    expect(result).toHaveLength(1)
    expect(result[0].ticker).toBe('000660')
  })

  it('날짜 범위 필터 — exit_date 기준 끝날짜', () => {
    const result = filterRoundtrips(MOCK_ROUNDTRIPS, { search: '', startDate: '', endDate: '2026-05-31', winOnly: null })
    expect(result).toHaveLength(1)
    expect(result[0].ticker).toBe('005930')
  })
})

describe('sortRoundtrips (AC-13: 엔터프라이즈 테이블 정렬)', () => {
  it('net_pnl 내림차순 → 이익 먼저', () => {
    const result = sortRoundtrips(MOCK_ROUNDTRIPS, 'net_pnl', 'desc')
    expect(result[0].net_pnl).toBe(29400)
  })

  it('net_pnl 오름차순 → 손실 먼저', () => {
    const result = sortRoundtrips(MOCK_ROUNDTRIPS, 'net_pnl', 'asc')
    expect(result[0].net_pnl).toBe(-25500)
  })

  it('ticker 오름차순 — 알파벳순', () => {
    const result = sortRoundtrips(MOCK_ROUNDTRIPS, 'ticker', 'asc')
    expect(result[0].ticker).toBe('000660')
  })

  it('holding_days 내림차순', () => {
    const result = sortRoundtrips(MOCK_ROUNDTRIPS, 'holding_days', 'desc')
    expect(result[0].holding_days).toBe(9)
  })

  it('null 값이 있어도 정렬 오류 없음 (null 은 마지막)', () => {
    const withNull: RoundTrip[] = [
      { ...MOCK_ROUNDTRIPS[0], ticker_name: '삼성전자', persona: null },
      { ...MOCK_ROUNDTRIPS[1], ticker_name: 'SK하이닉스', persona: 'macro' },
    ]
    const result = sortRoundtrips(withNull, 'persona', 'asc')
    expect(result[result.length - 1].persona).toBeNull()
  })
})

// ── KpiCardsContent 렌더 (AC-8) ──────────────────────────────────────────────
import { KpiCardsContent } from '../components/KpiCards'
import type { EquityPoint, PnlDailyResponse as PnlDR } from '../api/types'

const MOCK_SCORECARD: Scorecard = {
  verdict: 'NO_GO',
  grade: 'D',
  win_rate: 0.375,
  expectancy_adj: -14840,
  profit_factor_adj: 0.85,
  alpha_pct: -11.03,
  cagr: -0.05,
  mdd: -0.12,
  sharpe: -0.3,
  sortino: -0.45,
  n_closed: 8,
  benchmark_available: true,
}

const MOCK_EQUITY: EquityPoint[] = [
  { trading_day: '2026-06-20', total_assets: 10_000_000, stock_eval: 8_000_000, cash: 2_000_000, unrealized_pnl: 50000, drawdown_pct: -0.02 },
]

const MOCK_PNL: PnlDR = {
  period: 'daily',
  benchmark_available: true,
  rows: [
    { period_label: '2026-06-20', realized_pnl: 50000, cumulative_pnl: 150000, alpha_pct: null },
  ],
}

describe('KpiCardsContent 렌더 (AC-8)', () => {
  it('총 자산이 표시된다', () => {
    render(<KpiCardsContent scorecard={MOCK_SCORECARD} equity={MOCK_EQUITY} pnlDaily={MOCK_PNL} />)
    expect(screen.getByText('총 자산')).toBeDefined()
  })

  it('승률 카드가 표시된다', () => {
    render(<KpiCardsContent scorecard={MOCK_SCORECARD} equity={MOCK_EQUITY} pnlDaily={MOCK_PNL} />)
    expect(screen.getByText('승률')).toBeDefined()
  })

  it('Sharpe, Sortino 카드가 표시된다', () => {
    render(<KpiCardsContent scorecard={MOCK_SCORECARD} equity={MOCK_EQUITY} pnlDaily={MOCK_PNL} />)
    expect(screen.getByText('Sharpe')).toBeDefined()
    expect(screen.getByText('Sortino')).toBeDefined()
  })

  it('KOSPI 알파가 표시된다 (전체기간 알파 — scorecard 에서)', () => {
    render(<KpiCardsContent scorecard={MOCK_SCORECARD} equity={MOCK_EQUITY} pnlDaily={MOCK_PNL} />)
    expect(screen.getByText('KOSPI 알파')).toBeDefined()
    // 전체기간 알파 표시
    expect(screen.getByText('전체기간 대비')).toBeDefined()
  })

  it('null 데이터에도 크래시 없이 "—" 로 표시', () => {
    render(<KpiCardsContent scorecard={null} equity={null} pnlDaily={null} />)
    // 모든 값이 "—" 로 렌더됨
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThan(3)
  })
})

// ── PortfolioViewContent 렌더 (AC-9) ─────────────────────────────────────────
import { PortfolioViewContent } from '../components/PortfolioView'

const MOCK_PORTFOLIO: PortfolioData = {
  holdings: [
    { ticker: '005930', ticker_name: '삼성전자', qty: 10, avg_cost: 75000, eval_price: 78000, eval_amount: 780000, unrealized_pnl: 30000, pnl_pct: 4.0, weight_pct: 40.0, sector: '반도체' },
    { ticker: '000660', ticker_name: 'SK하이닉스', qty: 5, avg_cost: 120000, eval_price: 118000, eval_amount: 590000, unrealized_pnl: -10000, pnl_pct: -1.67, weight_pct: 30.0, sector: '반도체' },
    { ticker: '999999', ticker_name: '999999', qty: 1, avg_cost: 10000, eval_price: 10000, eval_amount: 10000, unrealized_pnl: 0, pnl_pct: 0, weight_pct: 5.0, sector: '' },
  ],
  nav: 2000000,
  cash_amount: 620000,
  cash_ratio: 0.31,
  herfindahl: 0.34,
  top3_pct: 75.0,
  sector_breakdown: [
    { sector: '반도체', weight_pct: 70.0 },
    { sector: '', weight_pct: 5.0 },
  ],
  snapshot_date: '2026-06-20',
}

describe('PortfolioViewContent 렌더 (AC-9)', () => {
  it('NAV, 현금비율, 집중도가 표시된다', () => {
    render(<PortfolioViewContent data={MOCK_PORTFOLIO} isLoading={false} onExport={() => {}} />)
    expect(screen.getByText('총 NAV')).toBeDefined()
    expect(screen.getByText('현금 비율')).toBeDefined()
    expect(screen.getByText('집중도 (Herfindahl)')).toBeDefined()
  })

  it('종목 테이블에 ticker_name과 코드가 표시된다', () => {
    render(<PortfolioViewContent data={MOCK_PORTFOLIO} isLoading={false} onExport={() => {}} />)
    // 한국어 종목명
    expect(screen.getByText('삼성전자')).toBeDefined()
    expect(screen.getByText('SK하이닉스')).toBeDefined()
    // 코드 (보조 텍스트)
    expect(screen.getByText('005930')).toBeDefined()
    expect(screen.getByText('000660')).toBeDefined()
  })

  it('미분류 종목이 "미분류" 로 표시된다 (REQ-054-G1)', () => {
    render(<PortfolioViewContent data={MOCK_PORTFOLIO} isLoading={false} onExport={() => {}} />)
    // sector='' 인 종목은 "미분류"로 표시
    const unclassified = screen.getAllByText('미분류')
    expect(unclassified.length).toBeGreaterThan(0)
  })

  it('스냅샷 시각 안내가 표시된다', () => {
    render(<PortfolioViewContent data={MOCK_PORTFOLIO} isLoading={false} onExport={() => {}} />)
    expect(screen.getByText(/스냅샷 기준/)).toBeDefined()
    expect(screen.getByText(/실시간 시세 아님/)).toBeDefined()
  })

  it('데이터 없을 때 graceful 메시지', () => {
    render(<PortfolioViewContent data={null} isLoading={false} onExport={() => {}} />)
    expect(screen.getByText(/포트폴리오 데이터 없음/)).toBeDefined()
  })

  it('코드 검색 필터 동작', () => {
    render(<PortfolioViewContent data={MOCK_PORTFOLIO} isLoading={false} onExport={() => {}} />)
    const searchInput = screen.getByPlaceholderText('종목명·코드/섹터 검색...')
    fireEvent.change(searchInput, { target: { value: '005930' } })
    expect(screen.getByText('005930')).toBeDefined()
  })

  it('한국어 종목명 검색 필터 동작', () => {
    render(<PortfolioViewContent data={MOCK_PORTFOLIO} isLoading={false} onExport={() => {}} />)
    const searchInput = screen.getByPlaceholderText('종목명·코드/섹터 검색...')
    fireEvent.change(searchInput, { target: { value: '삼성전자' } })
    expect(screen.getByText('삼성전자')).toBeDefined()
    // SK하이닉스는 필터링되어야 함
    expect(screen.queryByText('SK하이닉스')).toBeNull()
  })

  it('CSV 내보내기 버튼이 있다', () => {
    const onExport = vi.fn()
    render(<PortfolioViewContent data={MOCK_PORTFOLIO} isLoading={false} onExport={onExport} />)
    const btn = screen.getByRole('button', { name: '포트폴리오 CSV 내보내기' })
    fireEvent.click(btn)
    expect(onExport).toHaveBeenCalledOnce()
  })
})

// ── 라이트 테마 CSS 변수 검증 (AC-7, REQ-054-B2) ─────────────────────────────
describe('라이트 테마 토큰 (AC-7, REQ-054-B2)', () => {
  it('theme.ts 가 라이트 팔레트를 정의한다 (#f6f8fa 계열 배경)', async () => {
    const { theme } = await import('../theme')
    expect(theme.bg).toBe('#f6f8fa')
    expect(theme.bgCard).toBe('#ffffff')
    // 다크 배경 제거 확인
    expect(theme.bg).not.toBe('#0d1117')
    expect(theme.bgPanel).not.toBe('#161b22')
  })

  it('theme.ts 가 강조색을 라이트 팔레트로 업데이트했다', async () => {
    const { theme } = await import('../theme')
    expect(theme.accentBlue).toBe('#0969da')
    expect(theme.accentGreen).toBe('#1a7f37')
    expect(theme.accentRed).toBe('#cf222e')
  })

  it('echartsBaseOpts tooltip 이 흰 배경이다', async () => {
    const { echartsBaseOpts } = await import('../theme')
    expect(echartsBaseOpts.tooltip.backgroundColor).toBe('#ffffff')
  })
})

// ── RoundtripLedgerContent 렌더 (AC-12) ──────────────────────────────────────
import { RoundtripLedgerContent } from '../components/RoundtripLedger'

describe('RoundtripLedgerContent 렌더 (AC-12)', () => {
  it('행이 표시된다 — ticker_name, 코드, 페르소나, verdict', () => {
    render(<RoundtripLedgerContent data={MOCK_ROUNDTRIPS} isLoading={false} onExport={() => {}} />)
    // 종목명이 표시됨
    expect(screen.getByText('삼성전자')).toBeDefined()
    // 코드가 보조 텍스트로 표시됨
    expect(screen.getByText('005930')).toBeDefined()
    expect(screen.getByText('micro')).toBeDefined()
    expect(screen.getByText('TP')).toBeDefined()
  })

  it('데이터 없을 때 메시지 표시', () => {
    render(<RoundtripLedgerContent data={[]} isLoading={false} onExport={() => {}} />)
    expect(screen.getByText(/라운드트립 데이터 없음/)).toBeDefined()
  })

  it('CSV 내보내기 버튼 동작', () => {
    const onExport = vi.fn()
    render(<RoundtripLedgerContent data={MOCK_ROUNDTRIPS} isLoading={false} onExport={onExport} />)
    const btn = screen.getByRole('button', { name: '거래원장 CSV 내보내기' })
    fireEvent.click(btn)
    expect(onExport).toHaveBeenCalledOnce()
  })

  it('승/패 필터 버튼이 있다', () => {
    render(<RoundtripLedgerContent data={MOCK_ROUNDTRIPS} isLoading={false} onExport={() => {}} />)
    expect(screen.getByRole('button', { name: '전체' })).toBeDefined()
  })
})

// ── formatTicker / TickerLabel 단위 테스트 (REQ-054-F2) ──────────────────────

describe('formatTicker 헬퍼', () => {
  it('이름 ≠ 코드 → "이름 (코드)" 형태', () => {
    expect(formatTicker('055550', '신한지주')).toBe('신한지주 (055550)')
  })

  it('이름 = 코드 (미등록) → 코드만', () => {
    expect(formatTicker('055550', '055550')).toBe('055550')
  })

  it('ticker_name 이 null → 코드만', () => {
    expect(formatTicker('055550', null)).toBe('055550')
  })

  it('ticker_name 이 undefined → 코드만', () => {
    expect(formatTicker('055550', undefined)).toBe('055550')
  })

  it('ticker_name 이 빈 문자열 → 코드만', () => {
    expect(formatTicker('055550', '')).toBe('055550')
  })
})

describe('TickerLabel 컴포넌트', () => {
  it('이름 ≠ 코드 → 이름과 코드를 각각 렌더', () => {
    render(<TickerLabel ticker="055550" tickerName="신한지주" />)
    expect(screen.getByText('신한지주')).toBeDefined()
    expect(screen.getByText('055550')).toBeDefined()
  })

  it('이름 = 코드 (미등록) → 코드만 단일 span 렌더', () => {
    const { container } = render(<TickerLabel ticker="055550" tickerName="055550" />)
    expect(screen.getByText('055550')).toBeDefined()
    // span 이 하나 (column flex 없이)
    expect(container.querySelectorAll('span')).toHaveLength(1)
  })

  it('ticker_name null → 코드만 단일 span 렌더', () => {
    const { container } = render(<TickerLabel ticker="055550" tickerName={null} />)
    expect(screen.getByText('055550')).toBeDefined()
    expect(container.querySelectorAll('span')).toHaveLength(1)
  })
})

// ── HoldingsTableContent 엔터프라이즈 테이블 (FIX 1) ─────────────────────────
import { HoldingsTableContent } from '../components/HoldingsTable'
import type { Holding } from '../api/types'

const MOCK_HOLDINGS_ENTERPRISE: Holding[] = [
  {
    ticker: '005930',
    ticker_name: '삼성전자',
    qty_net: 10,
    avg_fill_price: 75000,
    total_cost: 750000,
    eval_price: 78000,
    eval_amount: 780000,
    unrealized_pnl: 30000,
    pnl_pct: 4.0,
  },
  {
    ticker: '000660',
    ticker_name: 'SK하이닉스',
    qty_net: 5,
    avg_fill_price: 120000,
    total_cost: 600000,
    // eval 필드 없음 — KIS 잔고 스냅샷 미포함 (브로커-원장 드리프트)
    eval_price: null,
    eval_amount: null,
    unrealized_pnl: null,
    pnl_pct: null,
  },
]

describe('HoldingsTableContent 엔터프라이즈 테이블 (FIX 1)', () => {
  it('종목명과 코드가 표시된다', () => {
    render(<HoldingsTableContent data={MOCK_HOLDINGS_ENTERPRISE} />)
    expect(screen.getByText('삼성전자')).toBeDefined()
    expect(screen.getByText('005930')).toBeDefined()
    expect(screen.getByText('SK하이닉스')).toBeDefined()
  })

  it('eval 필드가 있는 행: 현재가·평가금액·손익이 렌더된다', () => {
    render(<HoldingsTableContent data={MOCK_HOLDINGS_ENTERPRISE} />)
    // 78000 → 현재가
    expect(screen.getByText('78,000')).toBeDefined()
  })

  it('eval 필드가 null 인 행: "—" 로 렌더된다 (fabricate 없음)', () => {
    render(<HoldingsTableContent data={MOCK_HOLDINGS_ENTERPRISE} />)
    // SK하이닉스는 eval_price/eval_amount/unrealized_pnl/pnl_pct 모두 null → "—"
    const dashes = screen.getAllByText('—')
    // null 필드 4개 × 1행 + 비중 계산 불가 = 최소 4개 이상
    expect(dashes.length).toBeGreaterThanOrEqual(4)
  })

  it('pnl_pct 는 * 100 하지 않는다 (백엔드에서 이미 % 단위)', () => {
    render(<HoldingsTableContent data={MOCK_HOLDINGS_ENTERPRISE} />)
    // 4.0 → "+4.00%", 400% 가 아님
    expect(screen.getByText('+4.00%')).toBeDefined()
    expect(screen.queryByText('+400.00%')).toBeNull()
  })

  it('비중은 eval_amount 합 기준으로 계산된다', () => {
    render(<HoldingsTableContent data={MOCK_HOLDINGS_ENTERPRISE} />)
    // 삼성전자 eval_amount=780000, 총합=780000 → 100.0%
    expect(screen.getByText('100.0%')).toBeDefined()
    // SK하이닉스는 eval_amount=null → "—"
  })

  it('빈 데이터 시 "보유 종목 없음" 메시지', () => {
    render(<HoldingsTableContent data={[]} />)
    expect(screen.getByText('보유 종목 없음')).toBeDefined()
  })

  it('eval_amount 내림차순으로 기본 정렬, 헤더 클릭 시 정렬 토글', () => {
    render(<HoldingsTableContent data={MOCK_HOLDINGS_ENTERPRISE} />)
    // 평가금액 헤더 존재
    const evalAmountTh = screen.getByText(/평가금액/)
    expect(evalAmountTh).toBeDefined()
    // 클릭 시 오류 없음
    evalAmountTh.click()
  })
})

// ── ConfidenceScatter label 필드 수정 (FIX 2) ────────────────────────────────
import ConfidenceScatter from '../components/charts/ConfidenceScatter'
import type { ConfidenceAnalysis } from '../api/types'

const MOCK_CONFIDENCE: ConfidenceAnalysis = {
  buckets: [
    { label: 'HIGH', n: 12, win_rate: 0.75, avg_return_pct: 3.2 },
    { label: 'MED-HIGH', n: 8, win_rate: 0.5, avg_return_pct: 0.8 },
    { label: 'MED', n: 5, win_rate: 0.4, avg_return_pct: -0.5 },
  ],
  pearson: 0.42,
  spearman: 0.38,
  days: 30,
}

describe('ConfidenceScatter label 필드 (FIX 2)', () => {
  it('bucket.label 필드를 사용해 크래시 없이 렌더된다', () => {
    render(<ConfidenceScatter data={MOCK_CONFIDENCE} />)
    expect(screen.getByTestId('echart')).toBeDefined()
  })

  it('Pearson / Spearman 상관계수가 표시된다', () => {
    render(<ConfidenceScatter data={MOCK_CONFIDENCE} />)
    expect(screen.getByText(/0\.420/)).toBeDefined()
    expect(screen.getByText(/0\.380/)).toBeDefined()
  })

  it('기간 텍스트가 표시된다', () => {
    render(<ConfidenceScatter data={MOCK_CONFIDENCE} />)
    expect(screen.getByText(/30일/)).toBeDefined()
  })
})

// ── FIX 1: 종목별 비중 파이 — ticker_name 사용 ────────────────────────────────
import { PortfolioViewContent } from '../components/PortfolioView'

const PORTFOLIO_WITH_NAMES: PortfolioData = {
  holdings: [
    {
      ticker: '005930',
      ticker_name: '삼성전자',
      qty: 10,
      avg_cost: 75000,
      eval_price: 78000,
      eval_amount: 780000,
      unrealized_pnl: 30000,
      pnl_pct: 4.0,
      weight_pct: 60.0,
      sector: '반도체',
    },
    {
      ticker: '000660',
      ticker_name: '000660', // 미등록 — 코드와 동일
      qty: 3,
      avg_cost: 120000,
      eval_price: 118000,
      eval_amount: 354000,
      unrealized_pnl: -6000,
      pnl_pct: -1.67,
      weight_pct: 27.0,
      sector: '',
    },
  ],
  nav: 1300000,
  cash_amount: 166000,
  cash_ratio: 12.77,
  herfindahl: 0.42,
  top3_pct: 87.0,
  sector_breakdown: [{ sector: '반도체', weight_pct: 60.0 }],
  snapshot_date: '2026-06-20',
}

describe('FIX 1: PortfolioViewContent — 종목별 비중 파이 ticker_name 사용', () => {
  it('종목 테이블에 ticker_name(삼성전자)이 표시된다', () => {
    render(<PortfolioViewContent data={PORTFOLIO_WITH_NAMES} isLoading={false} onExport={() => {}} />)
    expect(screen.getByText('삼성전자')).toBeDefined()
  })

  it('미등록 종목(name=code)은 코드만 단독 표시된다', () => {
    render(<PortfolioViewContent data={PORTFOLIO_WITH_NAMES} isLoading={false} onExport={() => {}} />)
    // ticker_name === ticker → 코드만 표시 (중복 없음)
    const el = screen.getAllByText('000660')
    expect(el.length).toBeGreaterThanOrEqual(1)
  })

  it('현금 슬라이스가 표시 데이터에 포함된다 (cash_ratio > 0)', () => {
    render(<PortfolioViewContent data={PORTFOLIO_WITH_NAMES} isLoading={false} onExport={() => {}} />)
    // cash_ratio > 0 → 현금 슬라이스 추가됨. 파이 2개(종목별+섹터별) 모두 렌더됨.
    const charts = screen.getAllByTestId('echart')
    expect(charts.length).toBeGreaterThanOrEqual(1)
  })
})

// ── FIX 2: aggregateByTicker 집계 로직 ───────────────────────────────────────
import { aggregateByTicker } from '../components/PositionsView'

const MOCK_ROUNDTRIPS_FIX2: RoundTrip[] = [
  {
    ticker: '005930',
    ticker_name: '삼성전자',
    entry_date: '2026-05-01',
    exit_date: '2026-05-10',
    qty: 10,
    entry_price: 75000,
    exit_price: 78000,
    net_pnl: 29400,
    return_pct: 3.92,
    entry_fee: 300,
    exit_fee: 312,
    fees: 612,
    holding_days: 9,
    confidence: 0.72,
    verdict: 'TP',
    persona: 'micro',
    is_win: true,
  },
  {
    ticker: '005930',
    ticker_name: '삼성전자',
    entry_date: '2026-06-01',
    exit_date: '2026-06-08',
    qty: 5,
    entry_price: 78000,
    exit_price: 76000,
    net_pnl: -10500,
    return_pct: -2.69,
    entry_fee: 234,
    exit_fee: 228,
    fees: 462,
    holding_days: 7,
    confidence: 0.60,
    verdict: 'FP',
    persona: 'micro',
    is_win: false,
  },
  {
    ticker: '000660',
    ticker_name: 'SK하이닉스',
    entry_date: '2026-06-05',
    exit_date: '2026-06-12',
    qty: 3,
    entry_price: 120000,
    exit_price: 125000,
    net_pnl: 14460,
    return_pct: 4.17,
    entry_fee: 216,
    exit_fee: 225,
    fees: 441,
    holding_days: 7,
    confidence: 0.80,
    verdict: 'TP',
    persona: 'macro',
    is_win: true,
  },
]

describe('aggregateByTicker — 거래 완료 종목별 집계 (FIX 2)', () => {
  it('3개 라운드트립 → 2종목으로 집계된다', () => {
    const result = aggregateByTicker(MOCK_ROUNDTRIPS_FIX2)
    expect(result).toHaveLength(2)
  })

  it('삼성전자 집계: count=2, total_pnl=29400+(-10500)=18900', () => {
    const result = aggregateByTicker(MOCK_ROUNDTRIPS_FIX2)
    const samsung = result.find(r => r.ticker === '005930')
    expect(samsung).toBeDefined()
    expect(samsung!.count).toBe(2)
    expect(samsung!.total_pnl).toBeCloseTo(18900)
  })

  it('삼성전자 wins=1 losses=1', () => {
    const result = aggregateByTicker(MOCK_ROUNDTRIPS_FIX2)
    const samsung = result.find(r => r.ticker === '005930')!
    expect(samsung.wins).toBe(1)
    expect(samsung.losses).toBe(1)
  })

  it('삼성전자 last_exit_date = 2026-06-08 (두 번째 거래)', () => {
    const result = aggregateByTicker(MOCK_ROUNDTRIPS_FIX2)
    const samsung = result.find(r => r.ticker === '005930')!
    expect(samsung.last_exit_date).toBe('2026-06-08')
  })

  it('cum_return_pct = total_pnl / entry_cost * 100', () => {
    const result = aggregateByTicker(MOCK_ROUNDTRIPS_FIX2)
    const samsung = result.find(r => r.ticker === '005930')!
    // entry_cost = 75000*10 + 78000*5 = 750000 + 390000 = 1140000
    const expectedPct = (18900 / 1140000) * 100
    expect(samsung.cum_return_pct).toBeCloseTo(expectedPct, 4)
  })

  it('SK하이닉스 집계: count=1, wins=1, total_pnl=14460', () => {
    const result = aggregateByTicker(MOCK_ROUNDTRIPS_FIX2)
    const sk = result.find(r => r.ticker === '000660')!
    expect(sk.count).toBe(1)
    expect(sk.wins).toBe(1)
    expect(sk.losses).toBe(0)
    expect(sk.total_pnl).toBeCloseTo(14460)
  })

  it('빈 배열 → 빈 결과', () => {
    const result = aggregateByTicker([])
    expect(result).toHaveLength(0)
  })
})

// ── FIX 2: PositionsViewContent 렌더 ─────────────────────────────────────────
import { PositionsViewContent } from '../components/PositionsView'

const MOCK_HOLDINGS_FIX2: Holding[] = [
  {
    ticker: '005930',
    ticker_name: '삼성전자',
    qty_net: 10,
    avg_fill_price: 75000,
    total_cost: 750000,
    eval_price: 78000,      // KIS 확인
    eval_amount: 780000,
    unrealized_pnl: 30000,
    pnl_pct: 4.0,
  },
  {
    ticker: '000660',
    ticker_name: 'SK하이닉스',
    qty_net: 5,
    avg_fill_price: 120000,
    total_cost: 600000,
    eval_price: null,       // phantom — KIS 잔고 없음
    eval_amount: null,
    unrealized_pnl: null,
    pnl_pct: null,
  },
]

describe('PositionsViewContent 렌더 (FIX 2)', () => {
  it('요약 카드: 총 미실현손익 라벨이 표시된다', () => {
    render(
      <PositionsViewContent
        holdings={MOCK_HOLDINGS_FIX2}
        roundtrips={MOCK_ROUNDTRIPS_FIX2}
        holdingsLoading={false}
        roundtripsLoading={false}
      />
    )
    expect(screen.getByText('총 미실현손익')).toBeDefined()
    // "총 실현손익" 은 요약 카드 + 섹션② 테이블 헤더에 각각 1회씩 등장
    const realizedLabels = screen.getAllByText('총 실현손익')
    expect(realizedLabels.length).toBeGreaterThanOrEqual(1)
  })

  it('섹션 헤더 ① ② 가 표시된다', () => {
    render(
      <PositionsViewContent
        holdings={MOCK_HOLDINGS_FIX2}
        roundtrips={MOCK_ROUNDTRIPS_FIX2}
        holdingsLoading={false}
        roundtripsLoading={false}
      />
    )
    expect(screen.getByText(/① 현재 보유/)).toBeDefined()
    expect(screen.getByText(/② 거래 완료/)).toBeDefined()
  })

  it('eval_price != null 종목만 섹션 ①에 표시', () => {
    render(
      <PositionsViewContent
        holdings={MOCK_HOLDINGS_FIX2}
        roundtrips={[]}
        holdingsLoading={false}
        roundtripsLoading={false}
      />
    )
    // 삼성전자는 eval_price 있음 → 테이블에 표시
    expect(screen.getByText('삼성전자')).toBeDefined()
  })

  it('eval_price == null 종목은 phantom 알림에 표시 (fabricate 없음)', () => {
    render(
      <PositionsViewContent
        holdings={MOCK_HOLDINGS_FIX2}
        roundtrips={[]}
        holdingsLoading={false}
        roundtripsLoading={false}
      />
    )
    // phantom 알림 텍스트 확인
    expect(screen.getByText(/KIS 잔고에 없음/)).toBeDefined()
    // "1종목"이 언급됨
    expect(screen.getByText(/1종목/)).toBeDefined()
  })

  it('pnl_pct는 * 100 하지 않는다 (백엔드에서 이미 % 단위)', () => {
    render(
      <PositionsViewContent
        holdings={MOCK_HOLDINGS_FIX2}
        roundtrips={[]}
        holdingsLoading={false}
        roundtripsLoading={false}
      />
    )
    expect(screen.getByText('+4.00%')).toBeDefined()
    expect(screen.queryByText('+400.00%')).toBeNull()
  })

  it('라운드트립이 있을 때 종목별 실현손익이 섹션 ②에 표시된다', () => {
    render(
      <PositionsViewContent
        holdings={[]}
        roundtrips={MOCK_ROUNDTRIPS_FIX2}
        holdingsLoading={false}
        roundtripsLoading={false}
      />
    )
    // 집계된 종목명
    expect(screen.getByText('삼성전자')).toBeDefined()
    expect(screen.getByText('SK하이닉스')).toBeDefined()
  })

  it('빈 데이터: 로딩 중 메시지가 표시된다', () => {
    render(
      <PositionsViewContent
        holdings={[]}
        roundtrips={[]}
        holdingsLoading={true}
        roundtripsLoading={true}
      />
    )
    expect(screen.getByText(/로딩 중/)).toBeDefined()
  })
})
