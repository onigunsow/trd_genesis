// REQ-054-D1/D2/E1/E2: 라운드트립 거래 원장
// - 1행 = 매수→매도 1왕복
// - 진입가·청산가·실현손익·수익률%·수수료·보유기간·페르소나·verdict
// - 정렬/필터/검색/날짜범위/CSV 내보내기 지원
import { useState, useCallback, useMemo } from 'react'
import { usePolling } from '../hooks/usePolling'
import { api } from '../api/client'
import type { RoundTrip } from '../api/types'

// 포맷 헬퍼
const fmtKrw = (v: number): string => {
  const abs = Math.abs(v)
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)}억`
  if (abs >= 1e4) return `${(v / 1e4).toFixed(0)}만`
  return v.toLocaleString('ko-KR')
}
const fmtPct = (v: number): string => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(2)}%`
const fmtPrice = (v: number): string => v.toLocaleString('ko-KR')

type SortKey = keyof RoundTrip
type SortDir = 'asc' | 'desc'

interface FilterState {
  search: string
  startDate: string
  endDate: string
  winOnly: boolean | null  // null = 전체
}

export function filterRoundtrips(rows: RoundTrip[], filter: FilterState): RoundTrip[] {
  return rows.filter(r => {
    // 검색 — ticker, persona, verdict
    if (filter.search) {
      const q = filter.search.toLowerCase()
      const match =
        r.ticker.toLowerCase().includes(q) ||
        (r.persona ?? '').toLowerCase().includes(q) ||
        (r.verdict ?? '').toLowerCase().includes(q)
      if (!match) return false
    }
    // 날짜 범위 — entry_date 기준
    if (filter.startDate && r.entry_date < filter.startDate) return false
    if (filter.endDate && r.exit_date > filter.endDate) return false
    // 승/패 필터
    if (filter.winOnly === true && !r.is_win) return false
    if (filter.winOnly === false && r.is_win) return false
    return true
  })
}

export function sortRoundtrips(rows: RoundTrip[], key: SortKey, dir: SortDir): RoundTrip[] {
  return [...rows].sort((a, b) => {
    const av = a[key] as number | string | boolean | null
    const bv = b[key] as number | string | boolean | null
    if (av == null && bv == null) return 0
    if (av == null) return dir === 'asc' ? 1 : -1
    if (bv == null) return dir === 'asc' ? -1 : 1
    const diff = av < bv ? -1 : av > bv ? 1 : 0
    return dir === 'asc' ? diff : -diff
  })
}

interface RoundtripLedgerContentProps {
  data: RoundTrip[]
  isLoading: boolean
  onExport: () => void
}

// @MX:NOTE: [AUTO] 라운드트립 렌더링 — edge 코어 읽기 전용, 손익 재계산 없음 (REQ-054-A6)
export function RoundtripLedgerContent({ data, isLoading, onExport }: RoundtripLedgerContentProps) {
  const [sortKey, setSortKey] = useState<SortKey>('exit_date')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [filter, setFilter] = useState<FilterState>({
    search: '',
    startDate: '',
    endDate: '',
    winOnly: null,
  })

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const processedRows = useMemo(
    () => sortRoundtrips(filterRoundtrips(data, filter), sortKey, sortDir),
    [data, filter, sortKey, sortDir],
  )

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
    fontSize: '0.78rem',
    borderBottom: '1px solid var(--border-light)',
    whiteSpace: 'nowrap',
    fontFamily: 'var(--font-mono)',
  }

  const sortIcon = (key: SortKey) =>
    sortKey === key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''

  const winFilterBtnStyle = (val: boolean | null): React.CSSProperties => ({
    padding: '4px 10px',
    fontSize: '0.72rem',
    border: '1px solid var(--border)',
    borderRadius: 6,
    cursor: 'pointer',
    background: filter.winOnly === val ? 'var(--accent-blue)' : 'var(--bg)',
    color: filter.winOnly === val ? '#fff' : 'var(--text-secondary)',
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* 컨트롤 바 */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
        <input
          type="text"
          placeholder="종목/페르소나/verdict 검색..."
          value={filter.search}
          onChange={e => setFilter(f => ({ ...f, search: e.target.value }))}
          style={{
            padding: '6px 10px',
            border: '1px solid var(--border)',
            borderRadius: 6,
            fontSize: '0.8rem',
            background: 'var(--bg)',
            color: 'var(--text-primary)',
            outline: 'none',
            width: 220,
          }}
          aria-label="라운드트립 검색"
        />
        <input
          type="date"
          value={filter.startDate}
          onChange={e => setFilter(f => ({ ...f, startDate: e.target.value }))}
          style={{ padding: '5px 8px', border: '1px solid var(--border)', borderRadius: 6, fontSize: '0.75rem', background: 'var(--bg)', color: 'var(--text-primary)' }}
          aria-label="시작 날짜"
        />
        <span style={{ color: 'var(--text-muted)' }}>~</span>
        <input
          type="date"
          value={filter.endDate}
          onChange={e => setFilter(f => ({ ...f, endDate: e.target.value }))}
          style={{ padding: '5px 8px', border: '1px solid var(--border)', borderRadius: 6, fontSize: '0.75rem', background: 'var(--bg)', color: 'var(--text-primary)' }}
          aria-label="종료 날짜"
        />
        <button style={winFilterBtnStyle(null)} onClick={() => setFilter(f => ({ ...f, winOnly: null }))}>전체</button>
        <button style={winFilterBtnStyle(true)} onClick={() => setFilter(f => ({ ...f, winOnly: true }))}>
          <span style={{ color: 'var(--accent-green)' }}>익</span>만
        </button>
        <button style={winFilterBtnStyle(false)} onClick={() => setFilter(f => ({ ...f, winOnly: false }))}>
          <span style={{ color: 'var(--accent-red)' }}>손</span>만
        </button>
        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginLeft: 4 }}>
          {isLoading ? '로딩 중...' : `${processedRows.length}건`}
        </span>
        <button
          onClick={onExport}
          style={{
            marginLeft: 'auto',
            padding: '5px 12px',
            fontSize: '0.75rem',
            background: 'var(--accent-blue)',
            color: '#fff',
            border: 'none',
            borderRadius: 6,
            cursor: 'pointer',
          }}
          aria-label="거래원장 CSV 내보내기"
        >
          CSV 내보내기
        </button>
      </div>

      {/* 테이블 */}
      <div
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          boxShadow: 'var(--shadow-sm)',
          overflowX: 'auto',
        }}
      >
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }}>
          <thead>
            <tr>
              {([
                ['ticker', '종목', 'left'],
                ['entry_date', '매수일', 'left'],
                ['exit_date', '매도일', 'left'],
                ['qty', '수량', 'right'],
                ['entry_price', '진입가', 'right'],
                ['exit_price', '청산가', 'right'],
                ['net_pnl', '실현손익', 'right'],
                ['return_pct', '수익률', 'right'],
                ['fees', '수수료', 'right'],
                ['holding_days', '보유일', 'right'],
                ['persona', '페르소나', 'left'],
                ['verdict', 'Verdict', 'left'],
                ['confidence', 'Conf.', 'right'],
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
            {processedRows.length === 0 ? (
              <tr>
                <td colSpan={13} style={{ ...tdStyle, textAlign: 'center', color: 'var(--text-muted)', padding: 32 }}>
                  {isLoading ? '데이터 로딩 중...' : '라운드트립 데이터 없음 (매수→매도 완료 거래가 없습니다)'}
                </td>
              </tr>
            ) : (
              processedRows.map((r, i) => (
                <tr
                  key={`${r.ticker}-${r.entry_date}-${r.exit_date}-${i}`}
                  style={{
                    background: r.is_win ? '#f0fdf4' : '#fff5f5',
                  }}
                >
                  <td style={{ ...tdStyle, textAlign: 'left', fontWeight: 600, color: 'var(--accent-blue)' }}>
                    {r.ticker}
                  </td>
                  <td style={{ ...tdStyle, textAlign: 'left', fontSize: '0.72rem' }}>{r.entry_date}</td>
                  <td style={{ ...tdStyle, textAlign: 'left', fontSize: '0.72rem' }}>{r.exit_date}</td>
                  <td style={tdStyle}>{r.qty.toLocaleString()}</td>
                  <td style={tdStyle}>{fmtPrice(r.entry_price)}</td>
                  <td style={tdStyle}>{fmtPrice(r.exit_price)}</td>
                  <td style={{
                    ...tdStyle,
                    fontWeight: 600,
                    color: r.net_pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)',
                  }}>
                    {fmtKrw(r.net_pnl)}
                  </td>
                  <td style={{
                    ...tdStyle,
                    color: r.return_pct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)',
                  }}>
                    {fmtPct(r.return_pct)}
                  </td>
                  <td style={{ ...tdStyle, color: 'var(--text-secondary)' }}>{fmtKrw(r.fees)}</td>
                  <td style={tdStyle}>{r.holding_days}일</td>
                  <td style={{ ...tdStyle, textAlign: 'left', color: 'var(--text-secondary)', fontSize: '0.72rem' }}>
                    {r.persona ?? '—'}
                  </td>
                  <td style={{ ...tdStyle, textAlign: 'left', fontSize: '0.72rem' }}>
                    {r.verdict ?? '—'}
                  </td>
                  <td style={{ ...tdStyle, color: 'var(--text-secondary)' }}>
                    {r.confidence != null ? (r.confidence * 100).toFixed(0) + '%' : '—'}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// 폴링 포함 독립 컨테이너
export default function RoundtripLedger() {
  const fetcher = useCallback(() => api.fetchRoundtrips(365, 500), [])
  const { data, isLoading } = usePolling(fetcher, 60_000)

  return (
    <RoundtripLedgerContent
      data={data ?? []}
      isLoading={isLoading}
      onExport={() => api.exportRoundtrips()}
    />
  )
}
