/**
 * SPEC-065 그룹 3 — 검증 게이트 뷰 (REQ-065-3a~3d).
 *
 * 2026-08-15 배포한 13개 변경이 8/17~ 어떻게 작동하는지 보는 자리다. 네 패널은
 * 그날 세션에서 결정 근거로 쓴 집계를 그대로 화면에 올린 것이라, 그 실측이 여기서
 * 재현되어야 한다(AC-3·AC-4). since 는 GateContext 에서 공유.
 *
 *  ③ 어디서 잃나   — 보유기간별 손익 (막대)
 *  ④ 진입 품질     — confidence x entry_freshness 매트릭스 (결정 전체 반사실)
 *  ⑤ 리스크 판정   — verdict 분포 + HOLD 사유 + HOLD 반사실 + 주문 도달률
 *  ⑥ 무엇이 막고 깎았나 — 게이트별 건수·삭감률 + 최근 사유 표
 */
import { useCallback } from 'react'
import ReactECharts from 'echarts-for-react'
import { usePolling } from '../hooks/usePolling'
import { api } from '../api/client'
import { theme, echartsBaseOpts } from '../theme'
import type { EntryQualityMatrix, HoldingPeriodPnl, RiskVerdicts, SizingGates } from '../api/types'
import { useGate } from './GateContext'

const panel: React.CSSProperties = {
  background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 8,
  padding: '14px 16px', boxShadow: 'var(--shadow-sm)', marginBottom: 14,
}
const h3: React.CSSProperties = { fontSize: '0.82rem', fontWeight: 700, margin: '0 0 4px', color: 'var(--text-primary)' }
const sub: React.CSSProperties = { fontSize: '0.7rem', color: 'var(--text-muted)', marginBottom: 10 }
const mono: React.CSSProperties = { fontFamily: 'var(--font-mono)' }
const th: React.CSSProperties = { textAlign: 'right', padding: '4px 8px', fontSize: '0.7rem', color: 'var(--text-muted)', fontWeight: 600, borderBottom: '1px solid var(--border)' }
const td: React.CSSProperties = { textAlign: 'right', padding: '4px 8px', fontSize: '0.78rem', ...mono }
const pos = (v: number | null | undefined) => (v == null ? 'var(--text-muted)' : v > 0 ? theme.accentGreen : v < 0 ? theme.accentRed : 'var(--text-secondary)')
const fpct = (v: number | null | undefined, d = 2) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(d)}%`)
const fwin = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`)

/* ③ 보유기간별 손익 ─────────────────────────────────────────────────────── */
function HoldingPeriodPanel({ d }: { d: HoldingPeriodPnl }) {
  const cats = d.buckets.map(b => b.bucket)
  const option = {
    ...echartsBaseOpts,
    grid: { left: 70, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'category', data: cats, axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', axisLabel: { formatter: (v: number) => `${(v / 10000).toFixed(0)}만` } },
    tooltip: {
      ...echartsBaseOpts.tooltip, trigger: 'axis',
      formatter: (ps: any[]) => {
        const b = d.buckets[ps[0].dataIndex]
        return `<b>${b.bucket}</b><br/>n=${b.n} · 승률 ${fwin(b.win_rate)} · 평균 ${fpct(b.avg_return_pct)}<br/>합계 <b>${b.sum_net_pnl.toLocaleString('ko-KR')}원</b>`
      },
    },
    series: [{
      type: 'bar', data: d.buckets.map(b => ({
        value: b.sum_net_pnl, itemStyle: { color: b.sum_net_pnl >= 0 ? theme.accentGreen : theme.accentRed },
      })),
      label: { show: true, position: 'top', fontSize: 10, formatter: (p: any) => `n=${d.buckets[p.dataIndex].n}` },
    }],
  }
  return (
    <div style={panel}>
      <h3 style={h3}>어디서 잃나 — 보유기간별 실현손익</h3>
      <div style={sub}>FIFO 왕복 {d.n_total}건{d.since ? ` · ${d.since}~ 진입분` : ''}. 막대 위 n = 건수.</div>
      <ReactECharts option={option} style={{ height: 220 }} notMerge />
      <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 6 }}>
        <thead><tr><th style={{ ...th, textAlign: 'left' }}>구간</th><th style={th}>n</th><th style={th}>승률</th><th style={th}>평균</th><th style={th}>합계</th></tr></thead>
        <tbody>{d.buckets.map(b => (
          <tr key={b.bucket}>
            <td style={{ ...td, textAlign: 'left' }}>{b.bucket}</td><td style={td}>{b.n}</td>
            <td style={{ ...td, color: pos((b.win_rate ?? 0.5) - 0.5) }}>{fwin(b.win_rate)}</td>
            <td style={{ ...td, color: pos(b.avg_return_pct) }}>{fpct(b.avg_return_pct)}</td>
            <td style={{ ...td, color: pos(b.sum_net_pnl) }}>{b.sum_net_pnl.toLocaleString('ko-KR')}</td>
          </tr>))}</tbody>
      </table>
    </div>
  )
}

/* ④ 진입 품질 매트릭스 ────────────────────────────────────────────────── */
const FRESH_ORDER = ['early', 'confirmed', 'late', 'unlabeled']
function EntryQualityPanel({ d }: { d: EntryQualityMatrix }) {
  const confs = [...new Set(d.cells.map(c => c.conf_bucket))].sort((a, b) => a - b)
  const freshes = FRESH_ORDER.filter(f => d.cells.some(c => c.freshness === f))
  const cell = (cb: number, f: string) => d.cells.find(c => c.conf_bucket === cb && c.freshness === f)
  const [h1, h2] = d.horizons
  return (
    <div style={panel}>
      <h3 style={h3}>진입 품질 — confidence × entry_freshness</h3>
      <div style={sub}>
        <b>매수 결정 전체</b>(체결 무관)의 결정일 종가 대비 {h1}/{h2}일 후 수익. 체결분만 보면 표본 편향으로 정반대 결론이 났다.
        {freshes.length === 1 && freshes[0] === 'unlabeled' && ' entry_freshness 는 2026-08-15 이후 결정부터 채워진다.'}
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', width: '100%' }}>
          <thead><tr>
            <th style={{ ...th, textAlign: 'left' }}>conf ＼ freshness</th>
            {freshes.map(f => <th key={f} style={th} colSpan={3}>{f}</th>)}
          </tr><tr>
            <th style={th}></th>
            {freshes.map(f => (<><th key={f + 'n'} style={th}>n</th><th key={f + '1'} style={th}>{h1}d</th><th key={f + '2'} style={th}>{h2}d</th></>))}
          </tr></thead>
          <tbody>{confs.map(cb => (
            <tr key={cb}>
              <td style={{ ...td, textAlign: 'left' }}>{cb.toFixed(1)}</td>
              {freshes.map(f => { const c = cell(cb, f); return (<>
                <td key={f + 'n'} style={td}>{c?.n ?? ''}</td>
                <td key={f + '1'} style={{ ...td, color: pos(c?.ret_20d) }}>{c ? fpct(c.ret_20d) : ''}</td>
                <td key={f + '2'} style={{ ...td, color: pos(c?.ret_40d) }}>{c ? fpct(c.ret_40d) : ''}</td>
              </>) })}
            </tr>))}</tbody>
        </table>
      </div>
    </div>
  )
}

/* ⑤ 리스크 판정 ────────────────────────────────────────────────────────── */
function RiskPanel({ d }: { d: RiskVerdicts }) {
  const total = Object.values(d.verdicts).reduce((a, b) => a + b, 0) || 1
  const order = ['APPROVE', 'HOLD', 'REJECT']
  const colors: Record<string, string> = { APPROVE: theme.accentGreen, HOLD: theme.accentYellow, REJECT: theme.accentRed }
  const [h1, h2] = d.horizons
  const cf = d.hold_counterfactual
  return (
    <div style={panel}>
      <h3 style={h3}>리스크 페르소나 — 무엇을 거르나</h3>
      <div style={sub}>검토 {d.n}건{d.since ? ` · ${d.since}~` : ''} · 주문 도달 {d.execution_reach_share == null ? '—' : `${Math.round(d.execution_reach_share * 100)}% (APPROVE ${d.execution_reach_n}건)`}</div>
      <div style={{ display: 'flex', height: 14, borderRadius: 4, overflow: 'hidden', marginBottom: 8 }}>
        {order.map(v => (d.verdicts[v] ?? 0) > 0 && (
          <div key={v} title={`${v} ${d.verdicts[v]}`} style={{ width: `${100 * (d.verdicts[v] ?? 0) / total}%`, background: colors[v] }} />
        ))}
      </div>
      <div style={{ fontSize: '0.74rem', marginBottom: 10, display: 'flex', gap: 14 }}>
        {order.map(v => <span key={v}><span style={{ color: colors[v] }}>■</span> {v} {d.verdicts[v] ?? 0} ({Math.round(100 * (d.verdicts[v] ?? 0) / total)}%)</span>)}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <div>
          <div style={{ fontSize: '0.72rem', fontWeight: 600, marginBottom: 4 }}>HOLD/REJECT 사유</div>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <tbody>{d.hold_reasons.map(r => (
              <tr key={r.reason}>
                <td style={{ ...td, textAlign: 'left', fontFamily: 'inherit' }}>{r.reason}</td>
                <td style={td}>{r.n}</td>
                <td style={{ ...td, color: 'var(--text-muted)' }}>{r.share == null ? '' : `${Math.round(r.share * 100)}%`}</td>
              </tr>))}</tbody>
          </table>
        </div>
        <div>
          <div style={{ fontSize: '0.72rem', fontWeight: 600, marginBottom: 4 }}>거른 종목을 들었다면 (반사실, n={cf.n})</div>
          <div style={{ ...mono, fontSize: '0.9rem' }}>
            <span style={{ color: 'var(--text-muted)' }}>{h1}d</span> <b style={{ color: pos(cf.ret_20d) }}>{fpct(cf.ret_20d)}</b>
            <span style={{ color: 'var(--text-muted)', marginLeft: 16 }}>{h2}d</span> <b style={{ color: pos(cf.ret_40d) }}>{fpct(cf.ret_40d)}</b>
          </div>
          <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 4 }}>
            양수면 거른 게 손해 — 리스크가 "일찍 산 것"을 걸렀다는 뜻.
          </div>
        </div>
      </div>
    </div>
  )
}

/* ⑥ 사이징 게이트 ─────────────────────────────────────────────────────── */
function SizingPanel({ d }: { d: SizingGates }) {
  return (
    <div style={panel}>
      <h3 style={h3}>무엇이 매수를 막고 깎았나</h3>
      <div style={sub}>감사 이벤트 {d.n_events}건{d.since ? ` · ${d.since}~` : ''}. 삭감률은 수량 조정이 있는 게이트만.</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
        {d.gates.map(g => (
          <div key={g.gate} style={{ border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', fontSize: '0.74rem' }}>
            <div style={{ color: 'var(--text-muted)' }}>{g.gate}</div>
            <div style={{ ...mono, fontSize: '0.95rem', fontWeight: 700 }}>{g.n}<span style={{ fontSize: '0.7rem', fontWeight: 400, color: 'var(--text-muted)' }}>건</span>
              {g.avg_cut_pct != null && <span style={{ marginLeft: 8, color: theme.accentRed }}>−{Math.round(g.avg_cut_pct)}%</span>}
            </div>
          </div>))}
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead><tr>
          <th style={{ ...th, textAlign: 'left' }}>시각</th><th style={{ ...th, textAlign: 'left' }}>게이트</th>
          <th style={{ ...th, textAlign: 'left' }}>종목</th><th style={th}>수량</th><th style={{ ...th, textAlign: 'left' }}>사유</th>
        </tr></thead>
        <tbody>{d.recent.map((r, i) => (
          <tr key={i} style={{ borderBottom: '1px solid var(--border-light, #eee)' }}>
            <td style={{ ...td, textAlign: 'left', color: 'var(--text-muted)' }}>{r.ts.slice(5, 16).replace('T', ' ')}</td>
            <td style={{ ...td, textAlign: 'left', fontFamily: 'inherit' }}>{r.gate}</td>
            <td style={{ ...td, textAlign: 'left' }}>{r.ticker ?? ''}</td>
            <td style={{ ...td, whiteSpace: 'nowrap' }}>{r.qty_original ?? '—'} → {r.qty_adjusted ?? '—'}</td>
            <td style={{ ...td, textAlign: 'left', fontFamily: 'inherit', fontSize: '0.72rem', color: r.reason ? 'var(--text-secondary)' : 'var(--text-muted)', maxWidth: 420, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.reason ?? ''}>
              {r.reason ?? '(사유 미기록 — 2026-08-15 이전 이벤트)'}
            </td>
          </tr>))}</tbody>
      </table>
    </div>
  )
}

/* 컨테이너 ─────────────────────────────────────────────────────────────── */
function Err({ what, e }: { what: string; e: string | null }) {
  return e ? <div style={{ ...panel, color: theme.accentRed, fontSize: '0.75rem' }}>{what} 조회 오류: {e}</div> : null
}

export function GateView() {
  const { since } = useGate()
  const fH = useCallback(() => api.fetchGateHoldingPeriod(since), [since])
  const fE = useCallback(() => api.fetchGateEntryQuality(since), [since])
  const fR = useCallback(() => api.fetchGateRisk(since), [since])
  const fS = useCallback(() => api.fetchGateSizing(since, 20), [since])
  const H = usePolling(fH, 120_000)
  const E = usePolling(fE, 120_000)
  const R = usePolling(fR, 120_000)
  const S = usePolling(fS, 120_000)
  return (
    <div>
      {H.data ? <HoldingPeriodPanel d={H.data} /> : <Err what="보유기간" e={H.error} />}
      {E.data ? <EntryQualityPanel d={E.data} /> : <Err what="진입 품질" e={E.error} />}
      {R.data ? <RiskPanel d={R.data} /> : <Err what="리스크 판정" e={R.error} />}
      {S.data ? <SizingPanel d={S.data} /> : <Err what="사이징 게이트" e={S.error} />}
    </div>
  )
}
