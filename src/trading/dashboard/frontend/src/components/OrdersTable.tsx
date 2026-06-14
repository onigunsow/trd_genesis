// 최근 주문 테이블
import { useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import { api } from '../api/client'
import { theme } from '../theme'

const PILL: Record<string, { bg: string; color: string }> = {
  filled:    { bg: '#1f4f2e', color: '#3fb950' },
  submitted: { bg: '#2f2a10', color: '#e3b341' },
  rejected:  { bg: '#3d1f1f', color: '#f85149' },
  cancelled: { bg: '#262626', color: '#8b949e' },
}

const s = {
  title: { fontSize: '0.7rem', color: '#8b949e', textTransform: 'uppercase' as const, letterSpacing: '0.08em', marginBottom: 10, borderBottom: '1px solid #21262d', paddingBottom: 6 },
  table: { width: '100%', borderCollapse: 'collapse' as const, fontSize: '0.78rem', fontFamily: theme.fontMono },
  th: { textAlign: 'left' as const, color: '#8b949e', fontWeight: 400, padding: '4px 8px 4px 0', borderBottom: '1px solid #21262d' },
  td: { padding: '5px 8px 5px 0', borderBottom: '1px solid #161b22', color: theme.textPrimary },
  empty: { color: '#6e7681', fontSize: '0.8rem', padding: '12px 0' },
  error: { color: '#f85149', fontSize: '0.75rem', padding: '6px 0' },
}

function fmtTs(ts: string): string {
  try {
    return new Date(ts).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', hour12: false }).slice(0, 16)
  } catch { return ts }
}

function fmt(v: number | null, dec = 0): string {
  if (v == null) return '—'
  return v.toLocaleString('ko-KR', { maximumFractionDigits: dec })
}

export default function OrdersTable() {
  const fetcher = useCallback(() => api.fetchOrders(30), [])
  const { data, error } = usePolling(fetcher, 15_000)

  return (
    <section>
      <div style={s.title}>최근 주문</div>
      {error && <div style={s.error}>오류: {error}</div>}
      {!data || data.length === 0 ? (
        !error && <div style={s.empty}>주문 없음</div>
      ) : (
        <table style={s.table}>
          <thead>
            <tr>
              <th style={s.th}>시각</th>
              <th style={s.th}>종목</th>
              <th style={s.th}>방향</th>
              <th style={{ ...s.th, textAlign: 'right' }}>수량</th>
              <th style={{ ...s.th, textAlign: 'right' }}>체결가</th>
              <th style={s.th}>상태</th>
            </tr>
          </thead>
          <tbody>
            {data.map((o, i) => {
              const pill = PILL[o.status] ?? { bg: '#262626', color: '#8b949e' }
              return (
                <tr key={i}>
                  <td style={{ ...s.td, color: '#8b949e' }}>{fmtTs(o.ts)}</td>
                  <td style={s.td}><strong>{o.ticker}</strong></td>
                  <td style={{ ...s.td, color: o.side === 'buy' ? '#3fb950' : '#f85149', fontWeight: 600 }}>
                    {o.side?.toUpperCase()}
                  </td>
                  <td style={{ ...s.td, textAlign: 'right' }}>{fmt(o.qty)}</td>
                  <td style={{ ...s.td, textAlign: 'right' }}>{fmt(o.fill_price)}</td>
                  <td style={s.td}>
                    <span style={{ display: 'inline-block', padding: '1px 7px', borderRadius: 10, fontSize: '0.68rem', background: pill.bg, color: pill.color }}>
                      {o.status}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </section>
  )
}
