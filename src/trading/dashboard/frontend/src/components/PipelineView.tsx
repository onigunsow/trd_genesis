// M3: 파이프라인 다이어그램 + 결정 드릴다운 (REQ-050-15/16/17/18)
import { useState, useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import { api } from '../api/client'
import type { SystemStatus, Decision } from '../api/types'
import { formatTicker } from '../utils/ticker'
import { theme } from '../theme'

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
    color: theme.textSecondary,
    textTransform: 'uppercase' as const,
    letterSpacing: '0.08em',
    marginBottom: 10,
    borderBottom: `1px solid ${theme.border}`,
    paddingBottom: 6,
  },
  haltNote: {
    padding: '10px 14px',
    background: '#ffeef0',
    border: `1px solid ${theme.accentRed}`,
    borderRadius: 6,
    color: theme.accentRed,
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
    background: status === 'completed' ? '#f0fff4' : status === 'running' ? '#f0f6ff' : theme.bgCard,
    border: `1px solid ${status === 'completed' ? theme.accentGreen : status === 'running' ? theme.accentBlue : theme.border}`,
    borderRadius: 8,
    padding: '10px 12px',
    fontSize: '0.75rem',
    flexShrink: 0,
  }),
  stepName: {
    fontWeight: 600 as const,
    color: theme.textPrimary,
    marginBottom: 4,
  },
  stepMeta: {
    color: theme.textSecondary,
    fontSize: '0.68rem',
  },
  arrow: {
    display: 'flex',
    alignItems: 'center',
    padding: '0 6px',
    color: theme.border,
    fontSize: '1.1rem',
    marginTop: 14,
    flexShrink: 0,
  },
  statusDot: (status: string) => ({
    width: 6,
    height: 6,
    borderRadius: '50%',
    background: status === 'completed' ? theme.accentGreen : status === 'running' ? theme.accentBlue : theme.textMuted,
    display: 'inline-block',
    marginRight: 5,
  }),
  decisionList: { display: 'grid', gap: 8 },
  decisionRow: (selected: boolean) => ({
    padding: '10px 14px',
    background: selected ? '#f0f6ff' : theme.bgCard,
    border: `1px solid ${selected ? theme.accentBlue : theme.borderLight}`,
    borderRadius: 6,
    cursor: 'pointer',
    fontSize: '0.78rem',
    transition: 'border-color 0.15s',
  }),
  decisionMeta: { color: theme.textSecondary, fontSize: '0.7rem', marginBottom: 4 },
  sideBuy: { color: theme.buy, fontWeight: 600 as const },
  sideSell: { color: theme.sell, fontWeight: 600 as const },
  drilldown: {
    background: theme.bgCard,
    border: `1px solid ${theme.border}`,
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
  drillLabel: { color: theme.textSecondary, fontFamily: 'var(--font-mono)', fontSize: '0.7rem' },
  drillValue: { color: theme.textPrimary, wordBreak: 'break-word' as const },
  verdictBadge: (v: string | null) => ({
    display: 'inline-block',
    padding: '1px 8px',
    borderRadius: 10,
    fontSize: '0.7rem',
    background: v === 'APPROVE' ? '#e6f4ea' : v === 'REJECT' ? '#ffeef0' : '#fff8e1',
    color: v === 'APPROVE' ? theme.accentGreen : v === 'REJECT' ? theme.accentRed : theme.accentYellow,
    fontFamily: 'var(--font-mono)',
    fontWeight: 600 as const,
    border: `1px solid ${v === 'APPROVE' ? theme.accentGreen + '55' : v === 'REJECT' ? theme.accentRed + '55' : theme.accentYellow + '55'}`,
  }),
  empty: { color: theme.textMuted, fontSize: '0.8rem', padding: '20px 0', textAlign: 'center' as const },
  errorNote: { color: theme.accentRed, fontSize: '0.75rem', padding: '8px 0' },
  latency: { color: theme.textMuted, fontSize: '0.68rem' },
  raw: {
    background: theme.bg,
    border: `1px solid ${theme.border}`,
    borderRadius: 4,
    padding: '6px 8px',
    fontFamily: 'var(--font-mono)',
    fontSize: '0.68rem',
    color: theme.textSecondary,
    overflowX: 'auto' as const,
    maxHeight: 120,
    overflowY: 'auto' as const,
    whiteSpace: 'pre-wrap' as const,
    wordBreak: 'break-all' as const,
  },
  // REQ-064-A7: NULL 값은 "미기록"으로 명시 표기 (빈 문자열/0/-/공백과 구별)
  drillValueUnrecorded: {
    color: theme.textMuted,
    fontStyle: 'italic' as const,
    fontSize: '0.75rem',
  },
}

// 파이프라인 6장 카드 스트립 표시용 로컬 뷰모델.
// 백엔드 PipelineStep(9키, status: 'error'|'completed')엔 'step'/'pending' 개념이 없다 —
// 6단계 스켈레톤을 위해 프런트에서만 파생한다(ADR-004: PipelineView 6장 카드는 그룹 C 미변경).
interface DisplayStep {
  step: string
  persona_name: string | null
  cycle_kind: string | null
  status: 'completed' | 'error' | 'pending'
  latency_ms: number | null
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
  const orderedSteps: DisplayStep[] = pipeline
    ? STEP_ORDER.map((key) => {
        // 백엔드 /api/pipeline 은 단계를 persona_name 으로 반환한다(step 필드 없음).
        // null-safe 로 매칭하고, 찾은 항목엔 표준 키(step)를 부여한다.
        const found = pipeline.steps.find((st) =>
          (st.persona_name ?? '').toLowerCase().includes(key),
        )
        return found
          ? { step: key, persona_name: found.persona_name, cycle_kind: found.cycle_kind, status: found.status, latency_ms: found.latency_ms }
          : { step: key, persona_name: null, cycle_kind: null, status: 'pending', latency_ms: null }
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
          {pipeline?.cycle_ts && (
            <span style={{ fontWeight: 400, marginLeft: 8 }}>
              ({fmtTs(pipeline.cycle_ts)})
              {/* 2026-08-23: 묵은 사이클도 '최신' 으로 보이던 문제 — 경과 시간 표기 */}
              {(() => {
                const age = (pipeline as { cycle_age_seconds?: number | null }).cycle_age_seconds
                if (age == null || age < 3600) return null
                const h = Math.floor(age / 3600)
                return <span style={{ color: 'var(--accent-red)' }}> · {h >= 24 ? `${Math.floor(h / 24)}일` : `${h}시간`} 경과</span>
              })()}
            </span>
          )}
        </div>

        {/* 실패한 페르소나는 persona_runs 에 행을 남기지 않아 위 단계 목록에서 사라진다.
            audit_log 에서 끌어온 실패를 함께 보여주지 않으면 '전부 정상' 으로 읽힌다. */}
        {!!(pipeline as { failed_personas?: {persona: string; n: number}[] })?.failed_personas?.length && (
          <div style={s.errorNote}>
            이 사이클 실패:{' '}
            {(pipeline as { failed_personas: {persona: string; n: number}[] }).failed_personas
              .map((f) => `${f.persona} ${f.n}회`)
              .join(' · ')}
          </div>
        )}

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
                    <div style={{ marginTop: 4, fontSize: '0.65rem', color: theme.accentRed }}>HALT</div>
                  )}
                  {status?.cool_down_active && step.step === 'risk' && (
                    <div style={{ marginTop: 2, fontSize: '0.65rem', color: theme.accentPurple }}>COOL-DOWN</div>
                  )}
                  {status?.late_cycle_defense_active && step.step === 'portfolio' && (
                    <div style={{ marginTop: 2, fontSize: '0.65rem', color: theme.accentYellow }}>LATE-CYCLE</div>
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
                  {' '}<strong>{d.ticker ? formatTicker(d.ticker, d.ticker_name) : '—'}</strong>
                  {d.qty != null && <span style={{ color: theme.textSecondary }}> {d.qty}주</span>}
                  {d.confidence != null && (
                    <span style={{ color: theme.textMuted, marginLeft: 8 }}>
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
                    {/* ADR-001: prob_bull/base/bear 는 723/723 NULL — Decision 페르소나가 산출하지 않아 제거(REQ-064-A4) */}
                    <DrilldownRow label="Regime" value={d.regime_at_decision} emptyAsUnrecorded />
                    <DrilldownRow label="리스크 판정" value={d.risk_verdict} badge={d.risk_verdict} />
                    <DrilldownRow label="리스크 근거" value={d.risk_rationale} />
                    <DrilldownRow
                      label="트리거 컨텍스트"
                      value={d.trigger_context != null ? JSON.stringify(d.trigger_context) : null}
                      emptyAsUnrecorded
                    />
                    <div style={{ marginTop: 8 }}>
                      <div style={{ color: theme.textSecondary, fontSize: '0.7rem', marginBottom: 4 }}>response_json (raw)</div>
                      {/* REQ-064-A7: NULL 이면 빈 <pre> 로 침묵하지 말고 "미기록"으로 명시 */}
                      {d.response_json != null ? (
                        <pre style={s.raw}>{JSON.stringify(d.response_json, null, 2)}</pre>
                      ) : (
                        <div style={s.drillValueUnrecorded}>미기록</div>
                      )}
                    </div>
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
  emptyAsUnrecorded = false,
}: {
  label: string
  value: string | number | null | undefined
  badge?: string | null
  // REQ-064-A7: NULL(≠빈 문자열)을 "미기록"으로 명시. 빈 문자열은 그대로 통과시켜 구별한다.
  emptyAsUnrecorded?: boolean
}) {
  if (value == null) {
    if (!emptyAsUnrecorded) return null
    return (
      <div style={s.drillRow}>
        <div style={s.drillLabel}>{label}</div>
        <div style={s.drillValueUnrecorded}>미기록</div>
      </div>
    )
  }
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
