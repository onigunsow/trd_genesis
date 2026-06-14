// 스코어카드 패널 — 그리드 형태의 지표 표시
import type { Scorecard as ScorecardType } from '../../api/types'
import { theme } from '../../theme'

interface Props {
  data: ScorecardType
}

function fmt(v: number | null | undefined, decimals = 2, suffix = ''): string {
  if (v == null) return '—'
  return v.toLocaleString('ko-KR', { maximumFractionDigits: decimals }) + suffix
}

function pct(v: number | null | undefined, decimals = 1): string {
  if (v == null) return '—'
  return (v * 100).toFixed(decimals) + '%'
}

const verdictColor = (verdict: string): string => {
  if (verdict.includes('NO') && !verdict.includes('WEAK')) return theme.accentRed
  if (verdict.includes('WEAK')) return theme.accentYellow
  return theme.accentGreen
}

const s = {
  grid: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: 8,
  },
  item: {
    background: theme.bgCard,
    border: `1px solid ${theme.border}`,
    borderRadius: 6,
    padding: '8px 12px',
  },
  label: { fontSize: '0.68rem', color: theme.textSecondary },
  value: { fontSize: '1.05rem', fontWeight: 700 as const, marginTop: 2, fontFamily: theme.fontMono },
  reasons: {
    marginTop: 10,
    padding: '6px 10px',
    background: theme.bgCard,
    border: `1px solid ${theme.borderLight}`,
    borderRadius: 6,
    fontSize: '0.72rem',
    color: theme.textSecondary,
  },
}

export default function Scorecard({ data }: Props) {
  return (
    <div>
      <div style={s.grid}>
        <div style={s.item}>
          <div style={s.label}>판정</div>
          <div style={{ ...s.value, color: verdictColor(data.verdict ?? '') }}>{data.verdict || '—'}</div>
        </div>
        <div style={s.item}>
          <div style={s.label}>등급</div>
          <div style={s.value}>{data.grade || '—'}</div>
        </div>
        <div style={s.item}>
          <div style={s.label}>승률</div>
          <div style={s.value}>{pct(data.win_rate)}</div>
        </div>
        <div style={s.item}>
          <div style={s.label}>CAGR</div>
          <div style={s.value}>{pct(data.cagr)}</div>
        </div>
        <div style={s.item}>
          <div style={s.label}>MDD</div>
          <div style={{ ...s.value, color: data.mdd != null && data.mdd < -0.1 ? theme.accentRed : theme.textPrimary }}>
            {pct(data.mdd)}
          </div>
        </div>
        <div style={s.item}>
          <div style={s.label}>Sharpe</div>
          <div style={s.value}>{fmt(data.sharpe, 2)}</div>
        </div>
        <div style={s.item}>
          <div style={s.label}>Profit Factor</div>
          <div style={s.value}>{fmt(data.profit_factor_adj, 2)}</div>
        </div>
        <div style={s.item}>
          <div style={s.label}>종료 거래 수</div>
          <div style={s.value}>{data.n_closed ?? '—'}</div>
        </div>
      </div>
      {data.reasons && data.reasons.length > 0 && (
        <ul style={s.reasons}>
          {data.reasons.map((r, i) => (
            <li key={i} style={{ marginBottom: 2, listStyle: 'disc', marginLeft: 14 }}>{r}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
