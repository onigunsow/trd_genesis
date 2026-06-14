// 보유 종목 테이블
import { useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import { api } from '../api/client'
import { theme } from '../theme'

const s = {
  title: { fontSize: '0.7rem', color: '#8b949e', textTransform: 'uppercase' as const, letterSpacing: '0.08em', marginBottom: 10, borderBottom: '1px solid #21262d', paddingBottom: 6 },
  table: { width: '100%', borderCollapse: 'collapse' as const, fontSize: '0.78rem', fontFamily: theme.fontMono },
  th: { textAlign: 'left' as const, color: '#8b949e', fontWeight: 400, padding: '4px 8px 4px 0', borderBottom: '1px solid #21262d' },
  td: { padding: '5px 8px 5px 0', borderBottom: '1px solid #161b22', color: theme.textPrimary },
  empty: { color: '#6e7681', fontSize: '0.8rem', padding: '12px 0' },
  error: { color: '#f85149', fontSize: '0.75rem', padding: '6px 0' },
}

function fmt(v: number | null, dec = 0): string {
  if (v == null) return '—'
  return v.toLocaleString('ko-KR', { maximumFractionDigits: dec })
}

export default function HoldingsTable() {
  const fetcher = useCallback(() => api.fetchHoldings(), [])
  const { data, error } = usePolling(fetcher, 15_000)

  return (
    <section>
      <div style={s.title}>보유 종목</div>
      {error && <div style={s.error}>오류: {error}</div>}
      {!data || data.length === 0 ? (
        !error && <div style={s.empty}>보유 종목 없음</div>
      ) : (
        <table style={s.table}>
          <thead>
            <tr>
              <th style={s.th}>종목</th>
              <th style={{ ...s.th, textAlign: 'right' }}>수량</th>
              <th style={{ ...s.th, textAlign: 'right' }}>평균단가</th>
              <th style={{ ...s.th, textAlign: 'right' }}>매수총액</th>
            </tr>
          </thead>
          <tbody>
            {data.map((h) => (
              <tr key={h.ticker}>
                <td style={s.td}><strong>{h.ticker}</strong></td>
                <td style={{ ...s.td, textAlign: 'right' }}>{fmt(h.qty_net)}</td>
                <td style={{ ...s.td, textAlign: 'right' }}>{fmt(h.avg_fill_price)}</td>
                <td style={{ ...s.td, textAlign: 'right' }}>{fmt(h.total_cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
