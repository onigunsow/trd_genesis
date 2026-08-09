// SPEC-TRADING-064 REQ-064-C5/C8/C9/C10 — 결정 추적 흐름도.
// "결정 피드에서 한 건을 고르면 그 결정이 지나간 경로가 보인다"(운영자 요구)의 진입점.
// ADR-004: PipelineView 는 건드리지 않는다 — 새 뷰를 나란히 추가해 비교 가능하게 한다.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { usePolling } from '../hooks/usePolling'
import { api } from '../api/client'
import type { Decision, DecisionTrace, TraceNode, TraceNodeState, TraceOrder } from '../api/types'
import { formatTicker } from '../utils/ticker'
import { theme } from '../theme'
import { buildGraph, moduleOf, MODULE_ROLE } from '../trace/graph'
import TraceFlowGraph, { type SelectedNode } from './TraceFlowGraph'

const GRAPH = buildGraph()

const STATE_META: Record<TraceNodeState, { label: string; dot: string; desc: string }> = {
  recorded: { label: '① 관여함', dot: theme.accentBlue, desc: '이 결정의 audit 기록이 있다' },
  decision_agnostic: { label: '② 결정 단위 아님', dot: theme.accentYellow, desc: '관여했지만 배치/계좌 단위 기록이라 이 결정 하나로 못 좁힌다' },
  not_involved: { label: '③ 관여 안 함', dot: theme.textMuted, desc: '이 결정에 대한 기록이 없다' },
  rule_based: { label: '④ 규칙 기반 실행', dot: theme.accentPurple, desc: 'LLM 결정 없이 규칙(손절/익절/교정)으로 실행됨' },
}

const s = {
  // 고정 3칼럼 그리드('260px 1fr 320px')는 좁은 창에서 가운데 1fr 이 0px 로 눌려
  // 그래프 캔버스가 통째로 사라졌다(실측: 창 768px → canvas 0×774, container w=0).
  // flex-wrap 으로 바꿔 넓으면 3칼럼, 좁으면 그래프가 자기 줄을 온전히 차지한다.
  // 좁은 창에서 결정 목록이 화면 높이를 다 먹어 그래프가 아래로 밀려나 보이지 않았다.
  // 각 칼럼 높이를 묶고 목록은 자기 안에서 스크롤시킨다(실측: 창 768px).
  layout: {
    display: 'flex',
    flexWrap: 'wrap' as const,
    gap: 12,
    alignItems: 'flex-start' as const,
  },
  colPicker: {
    flex: '1 1 240px',
    maxWidth: 320,
    maxHeight: 'calc(100vh - 220px)',
    display: 'flex',
    flexDirection: 'column' as const,
    minWidth: 0,
    gap: 10,
  },
  // 흐름도가 이 화면의 주제다. order:-1 로 항상 맨 위 전체 폭을 차지하게 해서
  // 좁은 창에서 결정 목록에 밀려 화면 밖으로 나가지 않도록 한다.
  colGraph: {
    order: -1,
    flex: '1 1 100%',
    display: 'flex',
    flexDirection: 'column' as const,
    minWidth: 0,
    height: 'min(58vh, 540px)',
    gap: 10,
  },
  colDetail: { flex: '1 1 300px', maxWidth: 420, display: 'flex', flexDirection: 'column' as const, minWidth: 0, gap: 10 },
  col: { display: 'flex', flexDirection: 'column' as const, minHeight: 0, gap: 10 },
  panel: { background: theme.bgCard, border: `1px solid ${theme.border}`, borderRadius: 8, padding: 12, overflowY: 'auto' as const },
  sectionTitle: { fontSize: '0.7rem', color: theme.textSecondary, textTransform: 'uppercase' as const, letterSpacing: '0.08em', marginBottom: 8 },
  decisionRow: (active: boolean) => ({
    padding: '8px 10px', borderRadius: 6, cursor: 'pointer', fontSize: '0.75rem', marginBottom: 4,
    background: active ? '#eaf1fe' : 'transparent', border: `1px solid ${active ? theme.accentBlue : 'transparent'}`,
  }),
  meta: { color: theme.textSecondary, fontSize: '0.68rem' },
  legendRow: { display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.72rem', marginBottom: 6, color: theme.textSecondary },
  dot: (c: string) => ({ width: 8, height: 8, borderRadius: '50%', background: c, flexShrink: 0 }),
  empty: { color: theme.textMuted, fontSize: '0.8rem', padding: '16px 0', textAlign: 'center' as const },
  error: { color: theme.accentRed, fontSize: '0.75rem', padding: '8px 0' },
  unrecorded: { color: theme.textMuted, fontStyle: 'italic' as const, fontSize: '0.75rem' },
  raw: {
    background: theme.bg, border: `1px solid ${theme.border}`, borderRadius: 4, padding: '6px 8px',
    fontFamily: 'var(--font-mono)', fontSize: '0.68rem', color: theme.textSecondary,
    overflowX: 'auto' as const, maxHeight: 160, overflowY: 'auto' as const, whiteSpace: 'pre-wrap' as const, wordBreak: 'break-all' as const,
  },
  fieldRow: { display: 'grid', gridTemplateColumns: '90px 1fr', gap: '2px 8px', marginBottom: 4, fontSize: '0.75rem' },
  fieldLabel: { color: theme.textSecondary, fontFamily: 'var(--font-mono)', fontSize: '0.68rem' },
  orderTable: { width: '100%', borderCollapse: 'collapse' as const, fontSize: '0.7rem' },
  orderTh: { textAlign: 'left' as const, color: theme.textSecondary, fontWeight: 400, padding: '3px 4px', borderBottom: `1px solid ${theme.borderLight}` },
  orderTd: { padding: '4px', borderBottom: `1px solid ${theme.borderLight}`, cursor: 'pointer' },
  ruleBasedBadge: {
    display: 'inline-block', padding: '1px 6px', borderRadius: 8, fontSize: '0.62rem', fontWeight: 600 as const,
    background: '#f7f2ff', color: theme.accentPurple, border: `1px solid ${theme.accentPurple}55`,
  },
}

function fmtTs(ts: string | null): string {
  if (!ts) return '—'
  try {
    return new Date(ts).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', hour12: false }).slice(0, 16)
  } catch {
    return ts
  }
}

// 결정 필드 한 줄. NULL 은 REQ-064-C9 대로 "미기록"으로만 표기한다(0/빈칸/성공으로 렌더 금지).
function Field({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div style={s.fieldRow}>
      <div style={s.fieldLabel}>{label}</div>
      <div>{value == null ? <span style={s.unrecorded}>미기록</span> : String(value)}</div>
    </div>
  )
}

function findTraceNode(nodes: TraceNode[], file: string, fn: string): TraceNode | undefined {
  return nodes.find((n) => n.file === file && n.function === fn)
}

// 거부 사유는 "거부됐는데 사유가 없다(SPEC-063 이 잡은 결손)"와 "애초에 거부가 아니라 사유
// 필드가 해당 없다"를 구별해야 한다 — 후자를 "미기록"으로 부르면 오히려 오독을 만든다.
function rejectedReasonDisplay(o: TraceOrder): string {
  if (o.status !== 'rejected') return '—'
  return o.rejected_reason ?? '미기록'
}

function fillFieldDisplay(o: TraceOrder, value: number | null): string {
  if (o.status !== 'filled') return '—'
  return value == null ? '미기록' : value.toLocaleString('ko-KR')
}

export default function TraceView() {
  const decisionsFetcher = useCallback(() => api.fetchDecisions(30), [])
  const { data: decisions, error: decisionsError } = usePolling(decisionsFetcher, 15_000)

  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [trace, setTrace] = useState<DecisionTrace | null>(null)
  const [traceError, setTraceError] = useState<string | null>(null)
  const [traceLoading, setTraceLoading] = useState(false)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [selection, setSelection] = useState<SelectedNode | null>(null)
  const [selectedOrder, setSelectedOrder] = useState<TraceOrder | null>(null)

  useEffect(() => {
    if (selectedId == null) return
    let cancelled = false
    setTraceLoading(true)
    setTraceError(null)
    setSelection(null)
    setSelectedOrder(null)
    api
      .fetchTrace(selectedId)
      .then((t) => {
        if (cancelled) return
        setTrace(t)
        // 이 결정이 실제로 관여한(recorded) 모듈은 그 자리에서 자동으로 펼쳐 보여준다.
        setExpanded(new Set(t.nodes.filter((n) => n.state === 'recorded').map((n) => moduleOf(n.file))))
        setTraceLoading(false)
      })
      .catch((err) => {
        if (cancelled) return
        setTraceError(err instanceof Error ? err.message : String(err))
        setTraceLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedId])

  const stateLookup = useMemo(() => {
    const idx = new Map<string, { state: TraceNodeState; eventCount: number }>()
    for (const n of trace?.nodes ?? []) {
      idx.set(`${n.file}::${n.function}`, { state: n.state, eventCount: n.events.length })
    }
    return (file: string, fn: string) => idx.get(`${file}::${fn}`) ?? { state: 'not_involved' as TraceNodeState, eventCount: 0 }
  }, [trace])

  const toggleModule = useCallback((mod: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      next.has(mod) ? next.delete(mod) : next.add(mod)
      return next
    })
  }, [])

  return (
    <div>
      <h2 style={{ fontSize: '0.95rem', fontWeight: 700, marginBottom: 12, color: theme.textPrimary }}>
        결정 추적 흐름도
      </h2>
      <div style={s.layout}>
        {/* 좌측 — 결정 피드 선택 + 범례 */}
        <div style={s.colPicker}>
          <div style={{ ...s.panel, flex: 1 }}>
            <div style={s.sectionTitle}>결정 선택</div>
            {decisionsError && <div style={s.error}>오류: {decisionsError}</div>}
            {(!decisions || decisions.length === 0) && !decisionsError && <div style={s.empty}>결정 없음</div>}
            {(decisions ?? []).map((d: Decision) => (
              <div
                key={d.id} role="button" tabIndex={0} aria-pressed={selectedId === d.id}
                style={s.decisionRow(selectedId === d.id)}
                onClick={() => setSelectedId(d.id)}
                onKeyDown={(e) => e.key === 'Enter' && setSelectedId(d.id)}
              >
                <div style={s.meta}>{fmtTs(d.ts)} · {d.persona_name ?? '—'}</div>
                <div>
                  <span style={{ color: d.side === 'buy' ? theme.buy : theme.sell, fontWeight: 600 }}>{d.side?.toUpperCase() ?? '—'}</span>
                  {' '}{d.ticker ? formatTicker(d.ticker, d.ticker_name) : '—'}
                </div>
              </div>
            ))}
          </div>
          <div style={s.panel}>
            <div style={s.sectionTitle}>범례</div>
            {(Object.keys(STATE_META) as TraceNodeState[]).map((st) => (
              <div key={st} style={s.legendRow}>
                <span style={s.dot(STATE_META[st].dot)} />
                <span><strong>{STATE_META[st].label}</strong> — {STATE_META[st].desc}</span>
              </div>
            ))}
          </div>
        </div>

        {/* 중앙 — 흐름도 */}
        <div style={s.colGraph}>
          {traceError && <div style={s.error}>추적 조회 오류: {traceError}</div>}
          {!trace && !traceLoading && !traceError && (
            <div style={{ ...s.panel, flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <div style={s.empty}>좌측에서 결정을 선택하면 그 결정이 지나간 경로가 표시됩니다.</div>
            </div>
          )}
          {trace && (
            <TraceFlowGraph
              graph={GRAPH}
              expanded={expanded}
              onToggleModule={toggleModule}
              stateLookup={stateLookup}
              onSelect={(sel) => { setSelection(sel); setSelectedOrder(null) }}
            />
          )}
        </div>

        {/* 우측 — 상세 패널 */}
        <div style={{ ...s.colDetail, overflowY: 'auto' }}>
          {trace && (
            <div style={s.panel}>
              <div style={s.sectionTitle}>이 결정</div>
              <Field label="종목" value={trace.decision.ticker ? formatTicker(trace.decision.ticker, trace.decision.ticker_name) : null} />
              <Field label="방향/수량" value={trace.decision.side && trace.decision.qty != null ? `${trace.decision.side.toUpperCase()} ${trace.decision.qty}주` : null} />
              <Field label="근거" value={trace.decision.rationale} />
              <Field label="신뢰도" value={trace.decision.confidence} />
              <Field label="리스크 판정" value={trace.decision.risk_verdict} />
              <Field label="리스크 근거" value={trace.decision.risk_rationale} />
            </div>
          )}

          {trace && (
            <div style={s.panel} role="region" aria-label="선택 상세">
              <div style={s.sectionTitle}>선택한 블록</div>
              {!selection && !selectedOrder && (
                <div style={s.empty}>노드나 아래 주문 행을 클릭하면 판단 내용이 여기 표시됩니다.</div>
              )}
              {selection && <NodeDetail sel={selection} trace={trace} />}
              {selectedOrder && <OrderDetail order={selectedOrder} />}
            </div>
          )}

          {trace && (
            <div style={s.panel}>
              <div style={s.sectionTitle}>연결된 주문 ({trace.orders.length})</div>
              {trace.orders.length === 0 && <div style={s.empty}>연결된 주문 없음</div>}
              {trace.orders.length > 0 && (
                <table style={s.orderTable}>
                  <thead>
                    <tr>
                      <th style={s.orderTh}>시각</th>
                      <th style={s.orderTh}>종목</th>
                      <th style={s.orderTh}>상태</th>
                      <th style={s.orderTh}>출처</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trace.orders.map((o) => (
                      <tr key={o.id} onClick={() => { setSelectedOrder(o); setSelection(null) }}>
                        <td style={s.orderTd}>{fmtTs(o.ts)}</td>
                        <td style={s.orderTd}>{formatTicker(o.ticker, undefined)}</td>
                        <td style={s.orderTd}>{o.status}</td>
                        <td style={s.orderTd}>
                          {/* REQ-064-C10: 규칙 기반 실행은 "기록 없음"과 다르게 표기한다 */}
                          {o.origin === 'rule_based' ? <span style={s.ruleBasedBadge}>규칙 기반 실행 (LLM 결정 없음)</span> : '결정 연계'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function NodeDetail({ sel, trace }: { sel: SelectedNode; trace: DecisionTrace }) {
  if (sel.kind === 'module' || sel.kind === 'group') {
    return (
      <div>
        <div><strong>{sel.module}</strong> <StateChip state={sel.state} /></div>
        <div style={{ ...s.meta, marginTop: 4 }}>{MODULE_ROLE[sel.module] ?? ''}</div>
      </div>
    )
  }

  const tn = findTraceNode(trace.nodes, sel.file, sel.label)
  return (
    <div>
      <div><strong>{sel.label}</strong> <StateChip state={sel.state} /></div>
      <div style={{ margin: '4px 0' }}><code style={{ fontSize: '0.68rem', color: theme.textSecondary }}>{sel.file || '—'}</code></div>
      {/* REQ-064-C8: 결정/리스크 노드는 결정 본문 필드를 우선 보여준다. */}
      {sel.module === 'personas' && (
        <>
          <Field label="근거" value={trace.decision.rationale} />
          <Field label="신뢰도" value={trace.decision.confidence} />
          <Field label="방향" value={trace.decision.side} />
          <Field label="수량" value={trace.decision.qty} />
        </>
      )}
      {sel.module === 'risk' && (
        <>
          <Field label="리스크 판정" value={trace.decision.risk_verdict} />
          <Field label="리스크 근거" value={trace.decision.risk_rationale} />
        </>
      )}
      {/* REQ-064-C9: 이 노드에 대한 기록이 없으면 추정하지 않고 "미기록"으로만 말한다. */}
      {(!tn || tn.events.length === 0) && <div style={{ ...s.unrecorded, marginTop: 6 }}>미기록</div>}
      {tn && tn.events.length > 0 && (
        <div style={{ marginTop: 6 }}>
          {tn.events.map((ev, i) => (
            <div key={i} style={{ marginBottom: 6 }}>
              <div style={{ fontSize: '0.72rem', fontWeight: 600 }}>{ev.event_type} <span style={s.meta}>{fmtTs(ev.ts)} · {ev.actor}</span></div>
              {/* REQ-064-C8: 특정 모양이 없는 이벤트는 details 를 pretty-print JSON 으로 보여준다. */}
              <pre style={s.raw}>{JSON.stringify(ev.details, null, 2)}</pre>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function OrderDetail({ order }: { order: TraceOrder }) {
  return (
    <div>
      <div>
        <strong>{formatTicker(order.ticker, undefined)}</strong>{' '}
        <span style={{ color: order.side === 'buy' ? theme.buy : theme.sell, fontWeight: 600 }}>{order.side.toUpperCase()}</span>{' '}
        {order.qty}주
      </div>
      {order.origin === 'rule_based' && <div style={{ margin: '4px 0' }}><span style={s.ruleBasedBadge}>규칙 기반 실행 (LLM 결정 없음)</span></div>}
      <Field label="상태" value={order.status} />
      <Field label="거부 사유" value={rejectedReasonDisplay(order)} />
      <Field label="체결가" value={fillFieldDisplay(order, order.fill_price)} />
      <Field label="체결수량" value={fillFieldDisplay(order, order.fill_qty)} />
      <Field label="합성/교정" value={`${order.synthetic ? '합성' : '실체결'} / ${order.correction ? '교정' : '원본'}`} />
    </div>
  )
}

function StateChip({ state }: { state: TraceNodeState }) {
  const meta = STATE_META[state]
  return (
    <span style={{ ...s.legendRow, display: 'inline-flex', marginBottom: 0, marginLeft: 4 }}>
      <span style={s.dot(meta.dot)} />
      {meta.label}
    </span>
  )
}
