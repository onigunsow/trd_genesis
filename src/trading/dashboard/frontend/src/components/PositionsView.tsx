// FIX 2: 포지션 뷰 — 현재 보유(미실현) + 거래 완료(실현) 2섹션 분리
// 데이터 소스:
//   /api/holdings → Holding[] (qty_net > 0, eval_price null = 브로커-원장 드리프트)
//   /api/roundtrips → RoundTrip[] (실현 완료 거래)
// CRITICAL: eval_price null 행은 phantom — "—" 표시, 가격 fabricate 금지
import { useState, useCallback, useMemo } from 'react'
import { usePolling } from '../hooks/usePolling'
import { api } from '../api/client'
import type { Holding, RoundTrip } from '../api/types'
import { theme } from '../theme'
import { TickerLabel } from '../utils/ticker'
import OrdersTable from './OrdersTable'

// ── 포맷 헬퍼 ─────────────────────────────────────────────────────────────────
const fmtKrw = (v: number | null): string => {
  if (v == null) return '—'
  const abs = Math.abs(v)
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)}억`
  if (abs >= 1e4) return `${(v / 1e4).toFixed(0)}만`
  return v.toLocaleString('ko-KR')
}

const fmtPct = (v: number | null): string => {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

const fmtPrice = (v: number | null): string => {
  if (v == null) return '—'
  return v.toLocaleString('ko-KR')
}

// ── 실현 손익 종목별 집계 ─────────────────────────────────────────────────────
export interface RealizedTicker {
  ticker: string
  ticker_name: string
  count: number           // 라운드트립 건수
  total_pnl: number       // sum(net_pnl)
  // 누적 수익률: sum(net_pnl) / sum(entry_price * qty) * 100
  cum_return_pct: number
  wins: number
  losses: number
  last_exit_date: string  // max(exit_date)
}

// @MX:ANCHOR: [AUTO] aggregateByTicker — 거래 완료 섹션의 종목별 집계 진입점
// @MX:REASON: PositionsView 렌더·테스트·정렬 로직 3곳에서 참조 (fan_in >= 3)
export function aggregateByTicker(roundtrips: RoundTrip[]): RealizedTicker[] {
  const map = new Map<string, {
    ticker_name: string
    count: number
    total_pnl: number
    entry_cost: number  // sum(entry_price * qty) — 누적 수익률 분모
    wins: number
    losses: number
    last_exit_date: string
  }>()

  for (const rt of roundtrips) {
    const existing = map.get(rt.ticker)
    const entry_cost = rt.entry_price * rt.qty
    if (!existing) {
      map.set(rt.ticker, {
        ticker_name: rt.ticker_name,
        count: 1,
        total_pnl: rt.net_pnl,
        entry_cost,
        wins: rt.is_win ? 1 : 0,
        losses: rt.is_win ? 0 : 1,
        last_exit_date: rt.exit_date,
      })
    } else {
      existing.count += 1
      existing.total_pnl += rt.net_pnl
      existing.entry_cost += entry_cost
      existing.wins += rt.is_win ? 1 : 0
      existing.losses += rt.is_win ? 0 : 1
      if (rt.exit_date > existing.last_exit_date) {
        existing.last_exit_date = rt.exit_date
      }
    }
  }

  return Array.from(map.entries()).map(([ticker, v]) => ({
    ticker,
    ticker_name: v.ticker_name,
    count: v.count,
    total_pnl: v.total_pnl,
    cum_return_pct: v.entry_cost > 0 ? (v.total_pnl / v.entry_cost) * 100 : 0,
    wins: v.wins,
    losses: v.losses,
    last_exit_date: v.last_exit_date,
  }))
}

// ── 스타일 상수 ────────────────────────────────────────────────────────────────
const cardStyle: React.CSSProperties = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius)',
  padding: '16px',
  boxShadow: 'var(--shadow-sm)',
}

const sectionHeaderStyle: React.CSSProperties = {
  fontSize: '0.72rem',
  color: 'var(--text-muted)',
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.05em',
  marginBottom: 12,
}

const thStyle: React.CSSProperties = {
  padding: '7px 10px',
  textAlign: 'right',
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
  padding: '7px 10px',
  textAlign: 'right',
  fontSize: '0.78rem',
  borderBottom: '1px solid var(--border-light, #eaecef)',
  whiteSpace: 'nowrap',
  fontFamily: theme.fontMono,
  color: theme.textPrimary,
}

// ── 요약 카드 ─────────────────────────────────────────────────────────────────
interface SummaryCardsProps {
  totalUnrealized: number | null
  totalRealized: number
}

function SummaryCards({ totalUnrealized, totalRealized }: SummaryCardsProps) {
  const unrealizedColor =
    totalUnrealized == null ? theme.textPrimary
    : totalUnrealized >= 0 ? theme.accentGreen
    : theme.accentRed

  const realizedColor = totalRealized >= 0 ? theme.accentGreen : theme.accentRed

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12 }}>
      <div style={{ ...cardStyle, borderLeft: `3px solid ${unrealizedColor}` }}>
        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontWeight: 600, marginBottom: 4 }}>
          총 미실현손익
        </div>
        <div style={{ fontSize: '1.15rem', fontWeight: 700, fontFamily: theme.fontMono, color: unrealizedColor }}>
          {totalUnrealized == null ? '—' : fmtKrw(totalUnrealized)}
        </div>
        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>현재 보유 합산</div>
      </div>
      <div style={{ ...cardStyle, borderLeft: `3px solid ${realizedColor}` }}>
        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontWeight: 600, marginBottom: 4 }}>
          총 실현손익
        </div>
        <div style={{ fontSize: '1.15rem', fontWeight: 700, fontFamily: theme.fontMono, color: realizedColor }}>
          {fmtKrw(totalRealized)}
        </div>
        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>전체 라운드트립 합산</div>
      </div>
    </div>
  )
}

// ── 섹션 ① 현재 보유 (미실현) ─────────────────────────────────────────────────
type OpenSortKey = 'ticker' | 'qty_net' | 'avg_fill_price' | 'eval_price' | 'eval_amount' | 'unrealized_pnl' | 'pnl_pct'
type SortDir = 'asc' | 'desc'

interface OpenHoldingsSectionProps {
  holdings: Holding[]  // eval_price != null 로 필터된 KIS 확인 보유
  phantoms: Holding[]  // eval_price == null 인 드리프트 종목
  totalEval: number
}

function OpenHoldingsSection({ holdings, phantoms, totalEval }: OpenHoldingsSectionProps) {
  const [sortKey, setSortKey] = useState<OpenSortKey>('eval_amount')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const handleSort = (key: OpenSortKey) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const sortIcon = (key: OpenSortKey) =>
    sortKey === key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''

  const sorted = useMemo(() => {
    return [...holdings].sort((a, b) => {
      const av = a[sortKey] as number | string | null
      const bv = b[sortKey] as number | string | null
      if (av == null && bv == null) return 0
      if (av == null) return sortDir === 'asc' ? 1 : -1
      if (bv == null) return sortDir === 'asc' ? -1 : 1
      const diff = av < bv ? -1 : av > bv ? 1 : 0
      return sortDir === 'asc' ? diff : -diff
    })
  }, [holdings, sortKey, sortDir])

  const weightOf = (h: Holding): string => {
    if (h.eval_amount == null || totalEval === 0) return '—'
    return `${((h.eval_amount / totalEval) * 100).toFixed(1)}%`
  }

  return (
    <div style={cardStyle}>
      <div style={sectionHeaderStyle}>① 현재 보유 (미실현손익)</div>
      {holdings.length === 0 ? (
        <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem', padding: '12px 0' }}>
          KIS 확인 보유 종목 없음
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }}>
            <thead>
              <tr>
                <th style={{ ...thStyle, textAlign: 'left' }}>종목명(코드)</th>
                {([
                  ['qty_net', '수량'],
                  ['avg_fill_price', '매수평균단가'],
                  ['eval_price', '현재가'],
                  ['eval_amount', '평가금액'],
                  ['unrealized_pnl', '평가손익(미실현)'],
                  ['pnl_pct', '손익%'],
                ] as [OpenSortKey, string][]).map(([key, label]) => (
                  <th key={key} style={thStyle} onClick={() => handleSort(key)}>
                    {label}{sortIcon(key)}
                  </th>
                ))}
                <th style={thStyle}>비중</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(h => {
                const pnlColor = h.unrealized_pnl == null ? theme.textPrimary
                  : h.unrealized_pnl >= 0 ? theme.accentGreen : theme.accentRed
                const pctColor = h.pnl_pct == null ? theme.textPrimary
                  : h.pnl_pct >= 0 ? theme.accentGreen : theme.accentRed
                return (
                  <tr key={h.ticker}>
                    <td style={{ ...tdStyle, textAlign: 'left', fontWeight: 600, color: theme.accentBlue }}>
                      <TickerLabel ticker={h.ticker} tickerName={h.ticker_name} />
                    </td>
                    <td style={tdStyle}>{h.qty_net.toLocaleString()}</td>
                    <td style={tdStyle}>{fmtPrice(h.avg_fill_price)}</td>
                    <td style={tdStyle}>{fmtPrice(h.eval_price)}</td>
                    <td style={tdStyle}>{fmtKrw(h.eval_amount)}</td>
                    <td style={{ ...tdStyle, fontWeight: 600, color: pnlColor }}>
                      {fmtKrw(h.unrealized_pnl)}
                    </td>
                    <td style={{ ...tdStyle, color: pctColor }}>
                      {fmtPct(h.pnl_pct)}
                    </td>
                    <td style={{ ...tdStyle, color: 'var(--text-muted)' }}>
                      {weightOf(h)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* 드리프트 phantom 알림 */}
      {phantoms.length > 0 && (
        <div style={{
          marginTop: 10,
          padding: '6px 10px',
          background: '#fef9ec',
          border: '1px solid #f0d68a',
          borderRadius: 6,
          fontSize: '0.72rem',
          color: '#7c5900',
        }}>
          주문원장 기준 {phantoms.length}종목이 KIS 잔고에 없음(청산 또는 정합 드리프트):&nbsp;
          {phantoms.map(p => (p.ticker_name && p.ticker_name !== p.ticker)
            ? `${p.ticker_name}(${p.ticker})`
            : p.ticker
          ).join(', ')}
        </div>
      )}
    </div>
  )
}

// ── 섹션 ② 거래 완료 (실현) ──────────────────────────────────────────────────
type ClosedSortKey = keyof RealizedTicker
type ClosedSortDir = 'asc' | 'desc'

interface ClosedTradeSectionProps {
  rows: RealizedTicker[]
}

function ClosedTradeSection({ rows }: ClosedTradeSectionProps) {
  const [sortKey, setSortKey] = useState<ClosedSortKey>('last_exit_date')
  const [sortDir, setSortDir] = useState<ClosedSortDir>('desc')

  const handleSort = (key: ClosedSortKey) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const sortIcon = (key: ClosedSortKey) =>
    sortKey === key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''

  const sorted = useMemo(() => {
    return [...rows].sort((a, b) => {
      const av = a[sortKey] as number | string
      const bv = b[sortKey] as number | string
      if (av == null && bv == null) return 0
      if (av == null) return sortDir === 'asc' ? 1 : -1
      if (bv == null) return sortDir === 'asc' ? -1 : 1
      const diff = av < bv ? -1 : av > bv ? 1 : 0
      return sortDir === 'asc' ? diff : -diff
    })
  }, [rows, sortKey, sortDir])

  return (
    <div style={{
      ...cardStyle,
      borderTop: '2px solid var(--border)',
      background: 'var(--bg-panel, #f6f8fa)',
    }}>
      <div style={{ ...sectionHeaderStyle, color: 'var(--text-secondary)' }}>
        ② 거래 완료 (실현손익)
      </div>
      {rows.length === 0 ? (
        <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem', padding: '12px 0' }}>
          완료된 라운드트립 없음
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }}>
            <thead>
              <tr>
                <th style={{ ...thStyle, textAlign: 'left' }}>종목명(코드)</th>
                {([
                  ['count', '거래수'],
                  ['total_pnl', '총 실현손익'],
                  ['cum_return_pct', '누적 수익률'],
                  ['wins', '승'],
                  ['losses', '패'],
                  ['last_exit_date', '최근 청산일'],
                ] as [ClosedSortKey, string][]).map(([key, label]) => (
                  <th key={key} style={thStyle} onClick={() => handleSort(key)}>
                    {label}{sortIcon(key)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map(r => {
                const pnlColor = r.total_pnl >= 0 ? theme.accentGreen : theme.accentRed
                const retColor = r.cum_return_pct >= 0 ? theme.accentGreen : theme.accentRed
                return (
                  <tr key={r.ticker}>
                    <td style={{ ...tdStyle, textAlign: 'left', fontWeight: 600, color: theme.accentBlue }}>
                      <TickerLabel ticker={r.ticker} tickerName={r.ticker_name} />
                    </td>
                    <td style={tdStyle}>{r.count}</td>
                    <td style={{ ...tdStyle, fontWeight: 600, color: pnlColor }}>
                      {fmtKrw(r.total_pnl)}
                    </td>
                    <td style={{ ...tdStyle, color: retColor }}>
                      {fmtPct(r.cum_return_pct)}
                    </td>
                    <td style={{ ...tdStyle, color: theme.accentGreen }}>{r.wins}</td>
                    <td style={{ ...tdStyle, color: theme.accentRed }}>{r.losses}</td>
                    <td style={{ ...tdStyle, color: 'var(--text-muted)' }}>{r.last_exit_date}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── 메인 content 컴포넌트 (테스트 가능하도록 분리) ───────────────────────────
interface PositionsViewContentProps {
  holdings: Holding[]
  roundtrips: RoundTrip[]
  holdingsLoading: boolean
  roundtripsLoading: boolean
}

// @MX:ANCHOR: [AUTO] PositionsViewContent — 포지션 뷰 진입점
// @MX:REASON: PositionsView(폴링 래퍼)·App.tsx·테스트에서 참조 (fan_in >= 3)
export function PositionsViewContent({
  holdings,
  roundtrips,
  holdingsLoading,
  roundtripsLoading,
}: PositionsViewContentProps) {
  // 오픈 (eval_price != null) vs phantom (eval_price == null)
  const openHoldings = useMemo(
    () => holdings.filter(h => h.eval_price != null),
    [holdings],
  )
  const phantomHoldings = useMemo(
    () => holdings.filter(h => h.eval_price == null),
    [holdings],
  )

  // 총 미실현손익
  const totalUnrealized = useMemo(() => {
    if (openHoldings.length === 0) return null
    const sum = openHoldings.reduce((acc, h) => acc + (h.unrealized_pnl ?? 0), 0)
    return sum
  }, [openHoldings])

  // 총 실현손익
  const totalRealized = useMemo(
    () => roundtrips.reduce((acc, rt) => acc + rt.net_pnl, 0),
    [roundtrips],
  )

  // 총 평가금액 (비중 계산용)
  const totalEval = useMemo(
    () => openHoldings.reduce((acc, h) => acc + (h.eval_amount ?? 0), 0),
    [openHoldings],
  )

  // 종목별 집계
  const realizedByTicker = useMemo(() => aggregateByTicker(roundtrips), [roundtrips])

  if (holdingsLoading && holdings.length === 0 && roundtripsLoading && roundtrips.length === 0) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>
        포지션 데이터 로딩 중...
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* 상단 요약 카드 */}
      <SummaryCards totalUnrealized={totalUnrealized} totalRealized={totalRealized} />

      {/* 섹션 ① 현재 보유 */}
      <OpenHoldingsSection
        holdings={openHoldings}
        phantoms={phantomHoldings}
        totalEval={totalEval}
      />

      {/* 섹션 ② 거래 완료 */}
      <ClosedTradeSection rows={realizedByTicker} />

      {/* 최근 주문 (기존 유지) */}
      <OrdersTable />
    </div>
  )
}

// ── 폴링 포함 독립 컨테이너 ───────────────────────────────────────────────────
export default function PositionsView() {
  const holdingsFetcher = useCallback(() => api.fetchHoldings(), [])
  const roundtripsFetcher = useCallback(() => api.fetchRoundtrips(365, 1000), [])

  const { data: holdings, isLoading: holdingsLoading } = usePolling(holdingsFetcher, 15_000)
  const { data: roundtrips, isLoading: roundtripsLoading } = usePolling(roundtripsFetcher, 60_000)

  return (
    <PositionsViewContent
      holdings={holdings ?? []}
      roundtrips={roundtrips ?? []}
      holdingsLoading={holdingsLoading}
      roundtripsLoading={roundtripsLoading}
    />
  )
}
