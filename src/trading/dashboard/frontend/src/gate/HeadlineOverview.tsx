/**
 * SPEC-065 그룹 1 — 개요 헤드라인 (REQ-065-1a/1b/1c).
 *
 * 질문 순서대로: ① 살아있나(상태줄) ② 돈을 버나(헤드라인 = 판정+PF+기대값, 40%)
 * 보조 3개(승률·MDD·알파). 나머지(CAGR·Sharpe·Sortino·총자산)는 접힘.
 * 같은 숫자는 한 번만 — 종전 KpiCards + 엣지 스코어카드 중복을 이 컴포넌트가 대체한다.
 * REQ-065-2c: since 필터 중 표본 부족이면 판정 옆에 "표본 부족 (n=K)".
 */
import { useCallback, useState } from 'react'
import { usePolling } from '../hooks/usePolling'
import { api } from '../api/client'
import type { Scorecard, SystemStatus } from '../api/types'
import { useGate } from './GateContext'

const won = (v: number | null | undefined) =>
  v == null ? '—' : `${v < 0 ? '-' : v > 0 ? '+' : ''}${Math.abs(Math.round(v)).toLocaleString('ko-KR')}원`
const pct = (v: number | null | undefined, digits = 1) =>
  v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`
const num = (v: number | null | undefined, digits = 2) => (v == null ? '—' : v.toFixed(digits))
const ago = (iso: string | null | undefined) => {
  if (!iso) return '—'
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (m < 60) return `${m}분 전`
  if (m < 60 * 48) return `${Math.round(m / 60)}시간 전`
  return `${Math.round(m / 1440)}일 전`
}

const card: React.CSSProperties = {
  background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 8,
  padding: '14px 18px', boxShadow: 'var(--shadow-sm)',
}
const label: React.CSSProperties = {
  fontSize: '0.68rem', color: 'var(--text-muted)', letterSpacing: '0.04em', textTransform: 'uppercase',
}

export function StatusLine({ status }: { status: SystemStatus | null }) {
  if (!status) return null
  // 주말·휴장일을 건너뛰므로 48h 를 '살아있음' 기준으로 둔다(정밀 거래일 판정은 서버 몫).
  const alive = status.last_resolver_run
    ? Date.now() - new Date(status.last_resolver_run).getTime() < 48 * 3600 * 1000 : false
  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: '4px 18px', alignItems: 'center',
      fontSize: '0.74rem', color: 'var(--text-secondary)', marginBottom: 12,
    }}>
      <span title="스케줄러 resolver 하트비트 (SPEC-055)">
        <span style={{ color: alive ? 'var(--accent-green)' : 'var(--accent-red)' }}>●</span>{' '}
        하트비트 {ago(status.last_resolver_run)}
      </span>
      <span>마지막 결정 사이클 {ago(status.last_cycle_at)}</span>
      <span>오늘 주문 <b>{status.orders_today ?? '—'}</b> · 거부 <b>{status.rejected_today ?? '—'}</b> · 차단 <b>{status.blocked_today ?? '—'}</b></span>
      {status.halt_state && (
        <span style={{ color: 'var(--accent-red)', fontWeight: 600 }}>HALT {status.halt_reason ?? ''}</span>
      )}
    </div>
  )
}

function Headline({ sc }: { sc: Scorecard }) {
  const go = sc.verdict === 'GO'
  const color = go ? 'var(--accent-green)' : 'var(--accent-red)'
  const pf = sc.profit_factor_adj
  return (
    <div style={{ ...card, display: 'grid', gridTemplateColumns: 'auto 1fr 1fr', gap: 24, alignItems: 'center' }}>
      <div>
        <div style={label}>판정{sc.since ? ` · ${sc.since}~ 진입분` : ' · 전기간'}</div>
        <div style={{ fontSize: '2.6rem', fontWeight: 800, color, lineHeight: 1.05, fontFamily: 'var(--font-mono)' }}>
          {sc.verdict}
        </div>
        <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
          {sc.grade} · {sc.n_closed}건 왕복
          {sc.low_sample && (
            <span style={{ marginLeft: 8, color: 'var(--accent-red)', fontWeight: 600 }}>
              표본 부족 (n={sc.n_closed} &lt; {sc.gate_min_n})
            </span>
          )}
        </div>
      </div>
      <div>
        <div style={label}>손익비 (슬리피지 보정)</div>
        <div style={{ fontSize: '2rem', fontWeight: 700, color: pf != null && pf >= 1 ? 'var(--accent-green)' : 'var(--accent-red)', fontFamily: 'var(--font-mono)' }}>
          {pf === Infinity ? '∞' : num(pf)}
        </div>
        <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
          {pf != null && pf < 1 ? `1원 벌 때 ${(1 / pf).toFixed(1)}원 잃음` : pf != null ? `1원 잃을 때 ${pf.toFixed(1)}원 벎` : ''}
        </div>
      </div>
      <div>
        <div style={label}>거래당 기대값 (보정)</div>
        <div style={{ fontSize: '2rem', fontWeight: 700, color: (sc.expectancy_adj ?? 0) >= 0 ? 'var(--accent-green)' : 'var(--accent-red)', fontFamily: 'var(--font-mono)' }}>
          {won(sc.expectancy_adj)}
        </div>
        <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
          {(sc.reasons ?? []).slice(0, 1).join(' ')}
        </div>
      </div>
    </div>
  )
}

function Secondary({ sc }: { sc: Scorecard }) {
  const items = [
    { k: '승률', v: sc.win_rate == null ? '—' : `${(sc.win_rate * 100).toFixed(1)}%`, good: (sc.win_rate ?? 0) >= 0.5 },
    // mdd 는 소수 비율(-0.049), cagr/alpha_pct 는 이미 % — 단위가 다르다(KpiCards.fmt 와 동일 규약)
    { k: 'MDD', v: sc.mdd == null ? '—' : pct(sc.mdd * 100, 2), good: (sc.mdd ?? 0) > -0.10 },
    { k: 'KOSPI 알파', v: sc.benchmark_available ? `${pct(sc.alpha_pct, 2)}p` : '—', good: (sc.alpha_pct ?? 0) > 0 },
  ]
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginTop: 12 }}>
      {items.map(it => (
        <div key={it.k} style={card}>
          <div style={label}>{it.k}</div>
          <div style={{ fontSize: '1.3rem', fontWeight: 700, color: it.good ? 'var(--accent-green)' : 'var(--accent-red)', fontFamily: 'var(--font-mono)' }}>{it.v}</div>
        </div>
      ))}
    </div>
  )
}

function Details({ sc }: { sc: Scorecard }) {
  const [open, setOpen] = useState(false)
  const rows: [string, string][] = [
    ['CAGR', sc.cagr == null ? '—' : pct(sc.cagr * 100, 2)], ['Sharpe', num(sc.sharpe)], ['Sortino', num(sc.sortino)],
  ]
  return (
    <div style={{ marginTop: 10 }}>
      <button onClick={() => setOpen(o => !o)} style={{
        background: 'none', border: 'none', color: 'var(--text-muted)', fontSize: '0.72rem', cursor: 'pointer', padding: 0,
      }}>
        {open ? '▾' : '▸'} 상세 지표
      </button>
      {open && (
        <div style={{ ...card, marginTop: 6, display: 'flex', gap: 24, fontSize: '0.8rem', fontFamily: 'var(--font-mono)' }}>
          {rows.map(([k, v]) => <span key={k}><span style={{ color: 'var(--text-muted)' }}>{k}</span> {v}</span>)}
        </div>
      )}
    </div>
  )
}

export function HeadlineOverview({ status }: { status: SystemStatus | null }) {
  const { since } = useGate()
  const fetcher = useCallback(() => api.fetchScorecard(since), [since])
  const { data: sc, error } = usePolling(fetcher, 60_000)
  return (
    <div>
      <StatusLine status={status} />
      {error && <div style={{ color: 'var(--accent-red)', fontSize: '0.75rem' }}>스코어카드 조회 오류: {error}</div>}
      {sc && (<><Headline sc={sc} /><Secondary sc={sc} /><Details sc={sc} /></>)}
    </div>
  )
}
