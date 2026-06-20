// REQ-050-15/18: 시스템 상태바 — regime/risk/halt/cool_down/late_cycle 색상 코딩
import type { SystemStatus } from '../api/types'
import { theme } from '../theme'

interface Props {
  status: SystemStatus | null
}

const styles = {
  bar: {
    padding: '6px 20px',
    background: theme.bgPanel,
    borderBottom: `1px solid ${theme.border}`,
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap' as const,
    gap: 16,
    fontSize: '0.75rem',
  },
  haltBanner: {
    padding: '7px 20px',
    background: theme.halt,
    color: '#fff',
    fontWeight: 'bold' as const,
    textAlign: 'center' as const,
    fontSize: '0.85rem',
    letterSpacing: '0.04em',
  },
  item: {
    display: 'flex',
    alignItems: 'center',
    gap: 5,
    color: theme.textSecondary,
  },
  dot: (active: boolean, halt: boolean) => ({
    width: 7,
    height: 7,
    borderRadius: '50%',
    background: halt ? theme.halt : active ? theme.ok : theme.textMuted,
    display: 'inline-block',
    flexShrink: 0,
  }),
  badge: (color: string) => ({
    display: 'inline-block',
    padding: '1px 7px',
    borderRadius: 10,
    fontSize: '0.7rem',
    background: color + '18',
    color: color,
    fontFamily: 'var(--font-mono)',
    fontWeight: 600 as const,
    border: `1px solid ${color}33`,
  }),
}

function regimeColor(regime: string | null | undefined): string {
  if (!regime) return theme.textSecondary
  const r = regime.toUpperCase()
  if (r.includes('BULL')) return theme.accentGreen
  if (r.includes('BEAR')) return theme.accentRed
  return theme.accentYellow
}

function riskColor(risk: string | null | undefined): string {
  if (!risk) return theme.textSecondary
  const r = risk.toUpperCase()
  if (r === 'HIGH') return theme.accentRed
  if (r === 'LOW') return theme.accentGreen
  return theme.accentYellow
}

export default function StatusBar({ status }: Props) {
  if (!status) {
    return (
      <div style={styles.bar}>
        <span style={{ color: theme.textMuted }}>상태 로딩 중...</span>
      </div>
    )
  }

  const isHalt = status.halt_state === true
  const isCoolDown = status.cool_down_active === true
  const isLateCycle = status.late_cycle_defense_active === true

  return (
    <>
      {/* REQ-050-18: halt 상태 배너 */}
      {isHalt && (
        <div style={styles.haltBanner} role="alert" aria-live="assertive">
          SYSTEM HALTED — 매매 정지 중{status.halt_reason ? ` (${status.halt_reason})` : ''}
        </div>
      )}

      <div style={styles.bar} role="status" aria-label="시스템 상태">
        {/* 활성 점 + 모드 */}
        <span style={styles.item}>
          <span style={styles.dot(!isHalt, isHalt)} />
          <span style={{ color: isHalt ? theme.accentRed : theme.textPrimary }}>
            {status.trading_mode || '—'}
          </span>
        </span>

        {/* Regime */}
        <span style={styles.item}>
          Regime:&nbsp;
          <span style={styles.badge(regimeColor(status.current_regime))}>
            {status.current_regime || '—'}
          </span>
        </span>

        {/* Risk */}
        <span style={styles.item}>
          Risk:&nbsp;
          <span style={styles.badge(riskColor(status.current_risk_appetite))}>
            {status.current_risk_appetite || '—'}
          </span>
        </span>

        {/* Late-cycle */}
        {isLateCycle && (
          <span style={styles.item}>
            <span style={styles.badge('#e3b341')}>
              LATE-CYCLE {status.late_cycle_level ? `Lv${status.late_cycle_level}` : ''}
            </span>
          </span>
        )}

        {/* Cool-down */}
        {isCoolDown && (
          <span style={styles.item}>
            <span style={styles.badge('#a371f7')}>COOL-DOWN</span>
          </span>
        )}
      </div>
    </>
  )
}
