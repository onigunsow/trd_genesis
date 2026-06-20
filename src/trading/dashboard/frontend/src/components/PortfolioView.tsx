// REQ-054-C2/C3/G1: 포트폴리오 구성 뷰
// - 종목별 비중 파이, 현금 비율, 집중도 지수(Herfindahl·상위N)
// - 섹터별 비중 파이 (미분류 종목은 "미분류(Unclassified)"로 집계)
// - 종목별 손익 테이블 (정렬 가능)
import { useState, useCallback } from 'react'
import ReactECharts from 'echarts-for-react'
import { usePolling } from '../hooks/usePolling'
import { api } from '../api/client'
import type { PortfolioData, PortfolioHolding } from '../api/types'
import { theme, echartsBaseOpts } from '../theme'

// 원화 포맷 헬퍼
const fmtKrw = (v: number): string => {
  const abs = Math.abs(v)
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)}억`
  if (abs >= 1e4) return `${(v / 1e4).toFixed(0)}만`
  return v.toLocaleString('ko-KR')
}
const fmtPct = (v: number): string => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`

type SortKey = keyof PortfolioHolding
type SortDir = 'asc' | 'desc'

interface HoldingsTableProps {
  holdings: PortfolioHolding[]
}

// 종목별 손익 테이블 — 정렬 가능 (REQ-054-C3, E1)
function HoldingsTableEnterprize({ holdings }: HoldingsTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>('weight_pct')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [search, setSearch] = useState('')

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const filtered = holdings
    .filter(h => h.ticker.toLowerCase().includes(search.toLowerCase()) ||
                 h.sector.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => {
      const av = a[sortKey] as number | string
      const bv = b[sortKey] as number | string
      const diff = av < bv ? -1 : av > bv ? 1 : 0
      return sortDir === 'asc' ? diff : -diff
    })

  const thStyle: React.CSSProperties = {
    padding: '8px 10px',
    textAlign: 'right' as const,
    fontSize: '0.72rem',
    color: 'var(--text-muted)',
    fontWeight: 600,
    cursor: 'pointer',
    whiteSpace: 'nowrap',
    borderBottom: '1px solid var(--border)',
    background: 'var(--bg)',
    userSelect: 'none',
  }
  const tdStyle: React.CSSProperties = {
    padding: '8px 10px',
    textAlign: 'right' as const,
    fontSize: '0.8rem',
    borderBottom: '1px solid var(--border-light)',
    whiteSpace: 'nowrap',
    fontFamily: 'var(--font-mono)',
  }

  const sortIcon = (key: SortKey) =>
    sortKey === key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <input
          type="text"
          placeholder="종목/섹터 검색..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{
            padding: '6px 10px',
            border: '1px solid var(--border)',
            borderRadius: 6,
            fontSize: '0.8rem',
            background: 'var(--bg)',
            color: 'var(--text-primary)',
            outline: 'none',
            width: 200,
          }}
          aria-label="종목 검색"
        />
        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
          {filtered.length}종목
        </span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
          <thead>
            <tr>
              {([
                ['ticker', '종목코드', 'left'],
                ['sector', '섹터', 'left'],
                ['qty', '수량', 'right'],
                ['avg_cost', '평단', 'right'],
                ['eval_price', '현재가', 'right'],
                ['eval_amount', '평가금액', 'right'],
                ['unrealized_pnl', '평가손익', 'right'],
                ['pnl_pct', '수익률', 'right'],
                ['weight_pct', '비중%', 'right'],
              ] as [SortKey, string, string][]).map(([key, label, align]) => (
                <th
                  key={key}
                  style={{ ...thStyle, textAlign: align as 'left' | 'right' }}
                  onClick={() => handleSort(key)}
                >
                  {label}{sortIcon(key)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={9} style={{ ...tdStyle, textAlign: 'center', color: 'var(--text-muted)', padding: 24 }}>
                  보유 데이터 없음
                </td>
              </tr>
            ) : (
              filtered.map(h => (
                <tr key={h.ticker} style={{ background: 'var(--bg-card)' }}>
                  <td style={{ ...tdStyle, textAlign: 'left', fontWeight: 600, color: 'var(--accent-blue)' }}>
                    {h.ticker}
                  </td>
                  <td style={{ ...tdStyle, textAlign: 'left', color: 'var(--text-secondary)' }}>
                    {h.sector || '미분류'}
                  </td>
                  <td style={tdStyle}>{h.qty.toLocaleString()}</td>
                  <td style={tdStyle}>{fmtKrw(h.avg_cost)}</td>
                  <td style={tdStyle}>{fmtKrw(h.eval_price)}</td>
                  <td style={tdStyle}>{fmtKrw(h.eval_amount)}</td>
                  <td style={{
                    ...tdStyle,
                    color: h.unrealized_pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)',
                  }}>
                    {fmtKrw(h.unrealized_pnl)}
                  </td>
                  <td style={{
                    ...tdStyle,
                    color: h.pnl_pct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)',
                  }}>
                    {fmtPct(h.pnl_pct)}
                  </td>
                  <td style={tdStyle}>{h.weight_pct.toFixed(2)}%</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

interface PortfolioViewContentProps {
  data: PortfolioData | null
  isLoading: boolean
  onExport: () => void
}

export function PortfolioViewContent({ data, isLoading, onExport }: PortfolioViewContentProps) {
  if (isLoading && !data) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>
        포트폴리오 데이터 로딩 중...
      </div>
    )
  }

  if (!data) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>
        포트폴리오 데이터 없음 (스냅샷 미적용 또는 보유 종목 없음)
      </div>
    )
  }

  // 종목별 비중 파이 데이터
  const holdingsPieData = data.holdings.map((h, i) => ({
    name: h.ticker,
    value: parseFloat(h.weight_pct.toFixed(2)),
    itemStyle: { color: theme.chartPalette[i % theme.chartPalette.length] },
  }))
  // 현금도 파이에 포함
  if (data.cash_ratio > 0) {
    holdingsPieData.push({
      name: '현금',
      value: parseFloat((data.cash_ratio * 100).toFixed(2)),
      itemStyle: { color: '#9ca3af' },
    })
  }

  // 섹터별 비중 파이 데이터 (REQ-054-G1: 미분류 폴백 포함)
  const sectorPieData = data.sector_breakdown.map((s, i) => ({
    name: s.sector || '미분류(Unclassified)',
    value: parseFloat(s.weight_pct.toFixed(2)),
    itemStyle: { color: theme.chartPalette[i % theme.chartPalette.length] },
  }))

  const pieOption = (seriesData: typeof holdingsPieData) => ({
    ...echartsBaseOpts,
    tooltip: {
      ...echartsBaseOpts.tooltip,
      trigger: 'item',
      formatter: '{b}: {c}%',
    },
    series: [{
      type: 'pie',
      radius: ['35%', '65%'],
      center: ['50%', '55%'],
      data: seriesData,
      label: { color: theme.textPrimary, fontSize: 11 },
      emphasis: { itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.1)' } },
    }],
  })

  const cardStyle: React.CSSProperties = {
    background: 'var(--bg-card)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius)',
    padding: '16px',
    boxShadow: 'var(--shadow-sm)',
  }

  const labelStyle: React.CSSProperties = {
    fontSize: '0.72rem',
    color: 'var(--text-muted)',
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    marginBottom: 12,
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* 스냅샷 시각 안내 */}
      {data.snapshot_date && (
        <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
          스냅샷 기준: {data.snapshot_date} (일 1회 reconcile 기준, 실시간 시세 아님)
        </div>
      )}

      {/* 집중도 지표 */}
      <div
        style={{
          ...cardStyle,
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
          gap: 16,
        }}
      >
        <div>
          <div style={labelStyle}>총 NAV</div>
          <div style={{ fontSize: '1.1rem', fontWeight: 700, fontFamily: 'var(--font-mono)' }}>
            {fmtKrw(data.nav)}
          </div>
        </div>
        <div>
          <div style={labelStyle}>현금 비율</div>
          <div style={{ fontSize: '1.1rem', fontWeight: 700, fontFamily: 'var(--font-mono)' }}>
            {(data.cash_ratio * 100).toFixed(1)}%
          </div>
          <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>{fmtKrw(data.cash_amount)}</div>
        </div>
        <div>
          <div style={labelStyle}>집중도 (Herfindahl)</div>
          <div style={{ fontSize: '1.1rem', fontWeight: 700, fontFamily: 'var(--font-mono)' }}>
            {data.herfindahl.toFixed(4)}
          </div>
          <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>0=분산, 1=집중</div>
        </div>
        <div>
          <div style={labelStyle}>상위 3종목 비중</div>
          <div style={{ fontSize: '1.1rem', fontWeight: 700, fontFamily: 'var(--font-mono)' }}>
            {data.top3_pct.toFixed(1)}%
          </div>
        </div>
      </div>

      {/* 파이 차트 2개 — 종목별 비중 + 섹터별 비중 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }}>
        <div style={cardStyle}>
          <div style={labelStyle}>종목별 비중 (투자비중 = 시가평가액/NAV)</div>
          <ReactECharts
            option={pieOption(holdingsPieData)}
            style={{ height: 240 }}
            notMerge
          />
        </div>
        <div style={cardStyle}>
          <div style={labelStyle}>섹터별 비중</div>
          {sectorPieData.length === 0 ? (
            <div style={{ height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: '0.8rem' }}>
              섹터 데이터 없음
            </div>
          ) : (
            <ReactECharts
              option={pieOption(sectorPieData)}
              style={{ height: 240 }}
              notMerge
            />
          )}
        </div>
      </div>

      {/* 종목별 손익 테이블 (REQ-054-C3) */}
      <div style={cardStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <div style={labelStyle}>종목별 보유/손익</div>
          <button
            onClick={onExport}
            style={{
              padding: '5px 12px',
              fontSize: '0.75rem',
              background: 'var(--accent-blue)',
              color: '#fff',
              border: 'none',
              borderRadius: 6,
              cursor: 'pointer',
            }}
            aria-label="포트폴리오 CSV 내보내기"
          >
            CSV 내보내기
          </button>
        </div>
        <HoldingsTableEnterprize holdings={data.holdings} />
      </div>
    </div>
  )
}

// 폴링 포함 독립 컨테이너
export default function PortfolioView() {
  const fetcher = useCallback(() => api.fetchPortfolio(), [])
  const { data, isLoading } = usePolling(fetcher, 60_000)

  return (
    <PortfolioViewContent
      data={data}
      isLoading={isLoading}
      onExport={() => api.exportPortfolio()}
    />
  )
}
