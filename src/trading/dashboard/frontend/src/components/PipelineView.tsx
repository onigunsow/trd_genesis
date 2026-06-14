// M3: 파이프라인 다이어그램 + 결정 드릴다운 (REQ-050-15/16/17/18)
import { useState, useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import { api } from '../api/client'
import type { SystemStatus, Decision } from '../api/types'

interface Props {
  status: SystemStatus | null
}

// 파이프라인 단계 순서 (REQ-050-15)
const STEP_ORDER = ['macro', 'micro', 'decision', 'risk', 'portfolio', 'sizing']

const STEP_LABELS: Record<string, string> = {
  macro: 'Macro',
  micro: 'Micro',
  decision: '결정',
  risk: '리스크',
  portfolio: '포트폴리오',
  sizing: '사이징',
}

const s = {
  container: { display: 'grid', gap: 20 },
  sectionTitle: {
    fontSize: '0.7rem',
    color: '#8b949e',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.08em',
    marginBottom: 10,
    borderBottom: '1px solid #21262d',
    paddingBottom: 6,
  },
  haltNote: {
    padding: '10px 14px',
    background: '#3d1f1f',
    border: '1px solid #da3633',
    borderRadius: 6,
    color: '#f85149',
    fontSize: '0.8rem',
    marginBottom: 12,
  },
  pipeline: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 0,
    overflowX: 'auto' as const,
    padding: '12px 0',
  },
  stepCard: (status: string) => ({
    minWidth: 120,
    background: status === 'completed' ? '#1a2e1a' : status === 'running' ? '#1a2040' : '#1c2128',
    border: `1px solid ${status === 'completed' ? '#3fb950' : status === 'running' ? '#58a6ff' : '#30363d'}`,
    borderRadius: 8,
    padding: '10px 12px',
    fontSize: '0.75rem',
    flexShrink: 0,
  }),
  stepName: {
    fontWeight: 600 as const,
    color: '#e6edf3',
    marginBottom: 4,
  },
  stepMeta: {
    color: '#8b949e',
    fontSize: '0.68rem',
  },
  arrow: {
    display: 'flex',
    alignItems: 'center',
    padding: '0 6px',
    color: '#30363d',
    fontSize: '1.1rem',
    marginTop: 14,
    flexShrink: 0,
  },
  statusDot: (status: string) => ({
    width: 6,
    height: 6,
    borderRadius: '50%',
    background: status === 'completed' ? '#3fb950' : status === 'running' ? '#58a6ff' : '#6e7681',
    display: 'inline-block',
    marginRight: 5,
  }),
  decisionList: { display: 'grid', gap: 8 },
  decisionRow: (selected: boolean) => ({
    padding: '10px 14px',
    background: selected ? '#1a2040' : '#161b22',
    border: `1px solid ${selected ? '#58a6ff' : '#21262d'}`,
    borderRadius: 6,
    cursor: 'pointer',
    fontSize: '0.78rem',
    transition: 'border-color 0.15s',
  }),
  decisionMeta: { color: '#8b949e', fontSize: '0.7rem', marginBottom: 4 },
  sideBuy: { color: '#3fb950', fontWeight: 600 as const },
  sideSell: { color: '#f85149', fontWeight: 600 as const },
  drilldown: {
    background: '#1c2128',
    border: '1px solid #30363d',
    borderRadius: 8,
    padding: '14px 16px',
    fontSize: '0.78rem',
    marginTop: 8,
  },
  drillRow: {
    display: 'grid',
    gridTemplateColumns: '130px 1fr',
    gap: '4px 10px',
    marginBottom: 6,
  },
  drillLabel: { color: '#8b949e', fontFamily: 'var(--font-mono)', fontSize: '0.7rem' },
  drillValue: { color: '#e6edf3', wordBreak: 'break-word' as const },
  verdictBadge: (v: string | null) => ({
    display: 'inline-block',
    padding: '1px 8px',
    borderRadius: 10,
    fontSize: '0.7rem',
    background: v === 'APPROVE' ? '#1f4f2e' : v === 'REJECT' ? '#3d1f1f' : '#2f2a10',
    color: v === 'APPROVE' ? '#3fb950' : v === 'REJECT' ? '#f85149' : '#e3b341',
    fontFamily: 'var(--font-mono)',
    fontWeight: 600 as const,
  }),
  empty: { color: '#6e7681', fontSize: '0.8rem', padding: '20px 0', textAlign: 'center' as const },
  errorNote: { color: '#f85149', fontSize: '0.75rem', padding: '8px 0' },
  latency: { color: '#6e7681', fontSize: '0.68rem' },
  raw: {
    background: '#0d1117',
    border: '1px solid #21262d',
    borderRadius: 4,
    padding: '6px 8px',
    fontFamily: 'var(--font-mono)',
    fontSize: '0.68rem',
    color: '#8b949e',
    overflowX: 'auto' as const,
    maxHeight: 120,
    overflowY: 'auto' as const,
    whiteSpace: 'pre-wrap' as const,
    wordBreak: 'break-all' as const,
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

function fmtMs(ms: number | null): string {
  if (ms == null) return ''
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export default function PipelineView({ status }: Props) {
  const [selectedDecision, setSelectedDecision] = useState<Decision | null>(null)

  const pipelineFetcher = useCallback(() => api.fetchPipeline(), [])
  const decisionsFetcher = useCallback(() => api.fetchDecisions(30), [])

  const { data: pipeline, error: pipelineError } = usePolling(pipelineFetcher, 10_000)
  const { data: decisions, error: decisionsError } = usePolling(decisionsFetcher, 10_000)

  // 파이프라인 스텝을 정해진 순서로 정렬
  const orderedSteps = pipeline
    ? STEP_ORDER.map((key) => {
        const found = pipeline.steps.find((st) => st.step.toLowerCase().includes(key))
        return found ?? { step: key, persona_name: null, cycle_kind: null, status: 'pending' as const, latency_ms: null, started_at: null, decisions: [], verdicts: [] }
      })
    : []

  const handleDecisionClick = (d: Decision) => {
    setSelectedDecision(prev => (prev === d ? null : d))
  }

  return (
    <div style={s.container}>
      {/* REQ-050-18: halt 상태 + 사유 */}
      {status?.halt_state && (
        <div style={s.haltNote} role="alert">
          SYSTEM HALTED{status.halt_reason ? ` — ${status.halt_reason}` : ''}
        </div>
      )}

      {/* 파이프라인 다이어그램 (REQ-050-15/16) */}
      <section>
        <div style={s.sectionTitle}>
          최신 사이클 파이프라인
          {pipeline?.cycle_started_at && (
            <span style={{ fontWeight: 400, marginLeft: 8 }}>
              ({fmtTs(pipeline.cycle_started_at)})
            </span>
          )}
        </div>

        {pipelineError && <div style={s.errorNote}>파이프라인 오류 (마지막 데이터 유지): {pipelineError}</div>}

        {/* E1: 빈 배열 처리 */}
        {pipeline && orderedSteps.length === 0 && (
          <div style={s.empty}>파이프라인 데이터 없음 (신규 환경)</div>
        )}

        {orderedSteps.length > 0 && (
          <div style={s.pipeline}>
            {orderedSteps.map((step, i) => (
              <div key={step.step} style={{ display: 'flex', alignItems: 'flex-start' }}>
                <div style={s.stepCard(step.status)}>
                  <div style={s.stepName}>
                    <span style={s.statusDot(step.status)} />
                    {STEP_LABELS[step.step] ?? step.step}
                  </div>
                  {step.persona_name && (
                    <div style={s.stepMeta}>{step.persona_name}</div>
                  )}
                  {step.latency_ms != null && (
                    <div style={s.latency}>{fmtMs(step.latency_ms)}</div>
                  )}
                  {/* 리스크 가드 배지 */}
                  {status?.halt_state && step.step === 'risk' && (
                    <div style={{ marginTop: 4, fontSize: '0.65rem', color: '#f85149' }}>HALT</div>
                  )}
                  {status?.cool_down_active && step.step === 'risk' && (
                    <div style={{ marginTop: 2, fontSize: '0.65rem', color: '#a371f7' }}>COOL-DOWN</div>
                  )}
                  {status?.late_cycle_defense_active && step.step === 'portfolio' && (
                    <div style={{ marginTop: 2, fontSize: '0.65rem', color: '#e3b341' }}>LATE-CYCLE</div>
                  )}
                </div>
                {i < orderedSteps.length - 1 && <div style={s.arrow}>›</div>}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 결정 피드 + 드릴다운 (REQ-050-17) */}
      <section>
        <div style={s.sectionTitle}>결정 피드</div>

        {decisionsError && <div style={s.errorNote}>결정 오류 (마지막 데이터 유지): {decisionsError}</div>}

        {(!decisions || decisions.length === 0) && !decisionsError && (
          <div style={s.empty}>결정 없음</div>
        )}

        <div style={s.decisionList}>
          {(decisions ?? []).map((d, i) => {
            const isSelected = selectedDecision === d
            return (
              <div key={i}>
                <div
                  style={s.decisionRow(isSelected)}
                  onClick={() => handleDecisionClick(d)}
                  role="button"
                  aria-expanded={isSelected}
                  aria-label={`결정 상세 ${d.ticker ?? ''}`}
                  tabIndex={0}
                  onKeyDown={(e) => e.key === 'Enter' && handleDecisionClick(d)}
                >
                  <div style={s.decisionMeta}>
                    {fmtTs(d.ts)} &nbsp;|&nbsp; {d.persona_name ?? '—'} &nbsp;|&nbsp; {d.cycle_kind ?? '—'}
                  </div>
                  <span style={d.side === 'buy' ? s.sideBuy : s.sideSell}>
                    {d.side?.toUpperCase() ?? '—'}
                  </span>
                  {' '}<strong>{d.ticker ?? '—'}</strong>
                  {d.qty != null && <span style={{ color: '#8b949e' }}> {d.qty}주</span>}
                  {d.confidence != null && (
                    <span style={{ color: '#6e7681', marginLeft: 8 }}>
                      신뢰도 {d.confidence.toFixed(2)}
                    </span>
                  )}
                  {d.risk_verdict && (
                    <span style={{ ...s.verdictBadge(d.risk_verdict), marginLeft: 8 }}>
                      {d.risk_verdict}
                    </span>
                  )}
                </div>

                {/* REQ-050-17: 드릴다운 패널 */}
                {isSelected && (
                  <div style={s.drilldown} role="region" aria-label="결정 상세">
                    <DrilldownRow label="근거 (rationale)" value={d.rationale} />
                    <DrilldownRow label="신뢰도" value={d.confidence != null ? d.confidence.toFixed(3) : null} />
                    <DrilldownRow label="Regime" value={d.regime_at_decision} />
                    <DrilldownRow
                      label="확률 (Bull/Base/Bear)"
                      value={
                        d.prob_bull != null
                          ? `${(d.prob_bull * 100).toFixed(0)}% / ${(d.prob_base ?? 0) * 100 | 0}% / ${(d.prob_bear ?? 0) * 100 | 0}%`
                          : null
                      }
                    />
                    <DrilldownRow label="리스크 판정" value={d.risk_verdict} badge={d.risk_verdict} />
                    <DrilldownRow label="리스크 근거" value={d.risk_rationale} />
                    <DrilldownRow label="트리거 컨텍스트" value={d.trigger_context} />
                    {d.response_json && (
                      <div style={{ marginTop: 8 }}>
                        <div style={{ color: '#8b949e', fontSize: '0.7rem', marginBottom: 4 }}>response_json (raw)</div>
                        <pre style={s.raw}>{d.response_json}</pre>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </section>
    </div>
  )
}

function DrilldownRow({
  label,
  value,
  badge,
}: {
  label: string
  value: string | number | null | undefined
  badge?: string | null
}) {
  if (value == null) return null
  return (
    <div style={s.drillRow}>
      <div style={s.drillLabel}>{label}</div>
      <div style={s.drillValue}>
        {badge ? (
          <span style={s.verdictBadge(badge)}>{value}</span>
        ) : (
          String(value)
        )}
      </div>
    </div>
  )
}
