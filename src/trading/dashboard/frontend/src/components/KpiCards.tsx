// REQ-054-C1: 성과 요약 KPI 카드
// 총자산·일일/누적 실현손익·수익률%·MDD·승률·평균 손익비·Sharpe·Sortino·KOSPI 알파
// 모든 값은 /api/scorecard + /api/equity + /api/pnl-daily 에서 옴 (edge 코어 읽기 전용)
import { useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import { api } from '../api/client'
import type { Scorecard, EquityPoint, PnlDailyResponse } from '../api/types'

// 숫자 포맷 헬퍼
const fmt = {
  // 원화 금액 (억 단위 축약)
  krw: (v: number | null | undefined): string => {
    if (v == null) return '—'
    const abs = Math.abs(v)
    if (abs >= 1e8) return `${(v / 1e8).toFixed(2)}억`
    if (abs >= 1e4) return `${(v / 1e4).toFixed(0)}만`
    return v.toLocaleString('ko-KR') + '원'
  },
  // 퍼센트
  pct: (v: number | null | undefined, decimals = 2): string => {
    if (v == null) return '—'
    return `${v >= 0 ? '+' : ''}${(v * 100).toFixed(decimals)}%`
  },
  // 비율 (이미 % 단위인 경우)
  pctRaw: (v: number | null | undefined, decimals = 2): string => {
    if (v == null) return '—'
    return `${v >= 0 ? '+' : ''}${v.toFixed(decimals)}%`
  },
  // 소수 비율
  ratio: (v: number | null | undefined, decimals = 2): string => {
    if (v == null) return '—'
    return v.toFixed(decimals)
  },
}

interface KpiCardProps {
  label: string
  value: string
  sub?: string
  positive?: boolean | null  // null = 중립
  unit?: string
}

// 개별 KPI 카드
function KpiCard({ label, value, sub, positive, unit }: KpiCardProps) {
  const valueColor =
    positive === true
      ? 'var(--accent-green)'
      : positive === false
      ? 'var(--accent-red)'
      : 'var(--text-primary)'

  return (
    <div
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius)',
        padding: '14px 16px',
        minWidth: 0,
        boxShadow: 'var(--shadow-sm)',
      }}
    >
      <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginBottom: 4, fontWeight: 500 }}>
        {label}
      </div>
      <div style={{ fontSize: '1.15rem', fontWeight: 700, color: valueColor, fontFamily: 'var(--font-mono)' }}>
        {value}
        {unit && <span style={{ fontSize: '0.75rem', fontWeight: 400, marginLeft: 3 }}>{unit}</span>}
      </div>
      {sub && (
        <div style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginTop: 3 }}>{sub}</div>
      )}
    </div>
  )
}

interface Props {
  scorecard: Scorecard | null
  equity: EquityPoint[] | null
  pnlDaily: PnlDailyResponse | null
}

export function KpiCardsContent({ scorecard, equity, pnlDaily }: Props) {
  // 최신 자산 스냅샷
  const latestEquity = equity && equity.length > 0 ? equity[equity.length - 1] : null
  const totalAssets = latestEquity?.total_assets ?? null

  // 일일 실현손익 — pnl-daily 마지막 행
  const latestPnlRow =
    pnlDaily && pnlDaily.rows.length > 0
      ? pnlDaily.rows[pnlDaily.rows.length - 1]
      : null
  const dailyPnl = latestPnlRow?.realized_pnl ?? null
  const cumulativePnl = latestPnlRow?.cumulative_pnl ?? null

  // KOSPI 알파 — scorecard 전체기간 값 (기간별 alpha_pct 는 백엔드 한계로 null)
  const alphaAvail = scorecard?.benchmark_available !== false
  const alphaPct = scorecard?.alpha_pct ?? null

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))',
        gap: 12,
        marginBottom: 24,
      }}
    >
      <KpiCard
        label="총 자산"
        value={fmt.krw(totalAssets)}
        positive={null}
      />
      <KpiCard
        label="일일 실현손익"
        value={fmt.krw(dailyPnl)}
        positive={dailyPnl != null ? dailyPnl >= 0 : null}
      />
      <KpiCard
        label="누적 실현손익"
        value={fmt.krw(cumulativePnl)}
        positive={cumulativePnl != null ? cumulativePnl >= 0 : null}
      />
      <KpiCard
        label="수익률"
        value={scorecard?.cagr != null ? fmt.pct(scorecard.cagr) : '—'}
        positive={scorecard?.cagr != null ? scorecard.cagr >= 0 : null}
        sub="CAGR"
      />
      <KpiCard
        label="MDD"
        value={scorecard?.mdd != null ? fmt.pct(scorecard.mdd) : '—'}
        positive={scorecard?.mdd != null ? scorecard.mdd >= 0 : null}
        sub="최대 낙폭"
      />
      <KpiCard
        label="승률"
        value={scorecard?.win_rate != null ? fmt.pct(scorecard.win_rate) : '—'}
        positive={scorecard?.win_rate != null ? scorecard.win_rate >= 0.5 : null}
        sub={`${scorecard?.n_closed ?? 0}건 완료`}
      />
      <KpiCard
        label="손익비"
        value={scorecard?.profit_factor_adj != null ? fmt.ratio(scorecard.profit_factor_adj) : '—'}
        positive={scorecard?.profit_factor_adj != null ? scorecard.profit_factor_adj >= 1 : null}
        sub="adj. profit factor"
      />
      <KpiCard
        label="Sharpe"
        value={scorecard?.sharpe != null ? fmt.ratio(scorecard.sharpe) : '—'}
        positive={scorecard?.sharpe != null ? scorecard.sharpe >= 0 : null}
      />
      <KpiCard
        label="Sortino"
        value={fmt.ratio(scorecard?.sortino)}
        positive={scorecard?.sortino != null ? scorecard.sortino >= 0 : null}
      />
      <KpiCard
        label="KOSPI 알파"
        value={alphaAvail && alphaPct != null ? fmt.pctRaw(alphaPct) : '—'}
        positive={alphaAvail && alphaPct != null ? alphaPct >= 0 : null}
        sub={alphaAvail ? '전체기간 대비' : '데이터 없음'}
      />
    </div>
  )
}

// 폴링 포함 독립 컨테이너
export default function KpiCards() {
  const scorecardFetcher = useCallback(() => api.fetchScorecard(), [])
  const equityFetcher = useCallback(() => api.fetchEquity(90), [])
  const pnlFetcher = useCallback(() => api.fetchPnlDaily(90, 'daily'), [])

  const { data: scorecard } = usePolling(scorecardFetcher, 60_000)
  const { data: equity } = usePolling(equityFetcher, 60_000)
  const { data: pnlDaily } = usePolling(pnlFetcher, 60_000)

  return <KpiCardsContent scorecard={scorecard} equity={equity} pnlDaily={pnlDaily} />
}
