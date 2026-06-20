// 보유 종목 엔터프라이즈 테이블
// 컬럼: 종목, 수량, 매수평균단가, 현재가, 평가금액, 평가손익, 손익%, 비중
// CRITICAL: eval_price/eval_amount/unrealized_pnl/pnl_pct 는 KIS 잔고 스냅샷 미포함 시 null
//           null 은 "—" 로 표시 — 절대 fabricate 하지 말 것
import { useState, useCallback, useMemo } from 'react'
import { usePolling } from '../hooks/usePolling'
import { api } from '../api/client'
import type { Holding } from '../api/types'
import { theme } from '../theme'
import { TickerLabel } from '../utils/ticker'

// KRW 포맷: 만/억 단위 축약
const fmtKrw = (v: number | null): string => {
  if (v == null) return '—'
  const abs = Math.abs(v)
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)}억`
  if (abs >= 1e4) return `${(v / 1e4).toFixed(0)}만`
  return v.toLocaleString('ko-KR')
}

// 손익% 포맷: 백엔드에서 이미 % 단위 (예: 6.2 = 6.2%) — * 100 불필요
const fmtPct = (v: number | null): string => {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

const fmtPrice = (v: number | null): string => {
  if (v == null) return '—'
  return v.toLocaleString('ko-KR')
}

const fmtQty = (v: number): string => v.toLocaleString('ko-KR')

// 비중 계산: eval_amount / 총 eval_amount 합계
function computeWeight(h: Holding, totalEval: number): string {
  if (h.eval_amount == null || totalEval === 0) return '—'
  return `${((h.eval_amount / totalEval) * 100).toFixed(1)}%`
}

type SortKey = 'ticker' | 'qty_net' | 'avg_fill_price' | 'eval_price' | 'eval_amount' | 'unrealized_pnl' | 'pnl_pct'
type SortDir = 'asc' | 'desc'

function sortHoldings(rows: Holding[], key: SortKey, dir: SortDir): Holding[] {
  return [...rows].sort((a, b) => {
    const av = a[key] as number | string | null
    const bv = b[key] as number | string | null
    if (av == null && bv == null) return 0
    if (av == null) return dir === 'asc' ? 1 : -1
    if (bv == null) return dir === 'asc' ? -1 : 1
    const diff = av < bv ? -1 : av > bv ? 1 : 0
    return dir === 'asc' ? diff : -diff
  })
}

interface HoldingsTableContentProps {
  data: Holding[]
}

// @MX:ANCHOR: [AUTO] HoldingsTableContent — /api/holdings 표시 엔터프라이즈 테이블 진입점
// @MX:REASON: HoldingsTable, HoldingsTableContent (내보내기), 테스트에서 참조 (fan_in >= 3)
export function HoldingsTableContent({ data }: HoldingsTableContentProps) {
  const [sortKey, setSortKey] = useState<SortKey>('eval_amount')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const sortIcon = (key: SortKey) =>
    sortKey === key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''

  // 비중 계산 기준: eval_amount 가 있는 행의 합계만 사용
  const totalEval = useMemo(
    () => data.reduce((sum, h) => sum + (h.eval_amount ?? 0), 0),
    [data],
  )

  const sorted = useMemo(
    () => sortHoldings(data, sortKey, sortDir),
    [data, sortKey, sortDir],
  )

  const thStyle: React.CSSProperties = {
    padding: '7px 10px',
    textAlign: 'right' as const,
    fontSize: '0.72rem',
    color: '#8b949e',
    fontWeight: 600,
    cursor: 'pointer',
    whiteSpace: 'nowrap',
    borderBottom: `1px solid ${theme.border}`,
    background: theme.bg,
    userSelect: 'none',
  }
  const tdStyle: React.CSSProperties = {
    padding: '7px 10px',
    textAlign: 'right' as const,
    fontSize: '0.78rem',
    borderBottom: `1px solid ${theme.borderLight}`,
    whiteSpace: 'nowrap',
    fontFamily: theme.fontMono,
    color: theme.textPrimary,
  }

  if (data.length === 0) {
    return (
      <div style={{ color: '#6e7681', fontSize: '0.8rem', padding: '12px 0' }}>
        보유 종목 없음
      </div>
    )
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem', fontFamily: theme.fontMono }}>
        <thead>
          <tr>
            <th style={{ ...thStyle, textAlign: 'left' }}>종목</th>
            {([
              ['qty_net', '수량'],
              ['avg_fill_price', '매수평균단가'],
              ['eval_price', '현재가'],
              ['eval_amount', '평가금액'],
              ['unrealized_pnl', '평가손익'],
              ['pnl_pct', '손익%'],
            ] as [SortKey, string][]).map(([key, label]) => (
              <th
                key={key}
                style={thStyle}
                onClick={() => handleSort(key)}
              >
                {label}{sortIcon(key)}
              </th>
            ))}
            {/* 비중은 계산값이므로 정렬 없음 */}
            <th style={thStyle}>비중</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((h) => {
            const pnlColor =
              h.unrealized_pnl == null ? theme.textPrimary
              : h.unrealized_pnl >= 0 ? theme.accentGreen
              : theme.accentRed
            const pctColor =
              h.pnl_pct == null ? theme.textPrimary
              : h.pnl_pct >= 0 ? theme.accentGreen
              : theme.accentRed

            return (
              <tr key={h.ticker}>
                <td style={{ ...tdStyle, textAlign: 'left', fontWeight: 600, color: theme.accentBlue }}>
                  <TickerLabel ticker={h.ticker} tickerName={h.ticker_name} />
                </td>
                <td style={tdStyle}>{fmtQty(h.qty_net)}</td>
                <td style={tdStyle}>{fmtPrice(h.avg_fill_price)}</td>
                <td style={tdStyle}>{fmtPrice(h.eval_price)}</td>
                <td style={tdStyle}>{fmtKrw(h.eval_amount)}</td>
                <td style={{ ...tdStyle, fontWeight: 600, color: pnlColor }}>
                  {fmtKrw(h.unrealized_pnl)}
                </td>
                <td style={{ ...tdStyle, color: pctColor }}>
                  {fmtPct(h.pnl_pct)}
                </td>
                <td style={{ ...tdStyle, color: '#8b949e' }}>
                  {computeWeight(h, totalEval)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// 폴링 포함 독립 컨테이너
export default function HoldingsTable() {
  const fetcher = useCallback(() => api.fetchHoldings(), [])
  const { data, error } = usePolling(fetcher, 15_000)

  return (
    <section>
      <div style={{
        fontSize: '0.7rem',
        color: '#8b949e',
        textTransform: 'uppercase' as const,
        letterSpacing: '0.08em',
        marginBottom: 10,
        borderBottom: `1px solid ${theme.border}`,
        paddingBottom: 6,
      }}>
        보유 종목
      </div>
      {error && (
        <div style={{ color: '#f85149', fontSize: '0.75rem', padding: '6px 0' }}>
          오류: {error}
        </div>
      )}
      <HoldingsTableContent data={data ?? []} />
    </section>
  )
}
