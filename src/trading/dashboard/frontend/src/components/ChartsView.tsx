// M4: 자산 통계 차트 (REQ-050-19/20/21)
// ECharts + echarts-for-react 사용
import { useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import { api } from '../api/client'
import { theme } from '../theme'
import EquityChart from './charts/EquityChart'
import DrawdownChart from './charts/DrawdownChart'
import ReturnsDistribution from './charts/ReturnsDistribution'
import CumRealizedPnl from './charts/CumRealizedPnl'
import ConfidenceScatter from './charts/ConfidenceScatter'
import PostmortemBreakdown from './charts/PostmortemBreakdown'
import Scorecard from './charts/Scorecard'

const s = {
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(480px, 1fr))',
    gap: 16,
  },
  card: {
    background: theme.bgCard,
    border: `1px solid ${theme.border}`,
    borderRadius: 8,
    padding: '14px 16px',
    boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
  },
  cardTitle: {
    fontSize: '0.7rem',
    color: theme.textSecondary,
    textTransform: 'uppercase' as const,
    letterSpacing: '0.08em',
    marginBottom: 10,
    borderBottom: `1px solid ${theme.borderLight}`,
    paddingBottom: 6,
  },
  errorNote: { color: theme.accentRed, fontSize: '0.75rem', padding: '8px 0' },
  empty: { color: theme.textMuted, fontSize: '0.8rem', padding: '40px 0', textAlign: 'center' as const },
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={s.card}>
      <div style={s.cardTitle}>{title}</div>
      {children}
    </div>
  )
}

export default function ChartsView() {
  const equityFetcher = useCallback(() => api.fetchEquity(180), [])
  const scorecardFetcher = useCallback(() => api.fetchScorecard(), [])
  const postmortemFetcher = useCallback(() => api.fetchPostmortem(30), [])
  const confidenceFetcher = useCallback(() => api.fetchConfidenceAnalysis(30), [])

  const { data: equity, error: equityError } = usePolling(equityFetcher, 60_000)
  const { data: scorecard, error: scorecardError } = usePolling(scorecardFetcher, 60_000)
  const { data: postmortem, error: postmortemError } = usePolling(postmortemFetcher, 60_000)
  const { data: confidence, error: confidenceError } = usePolling(confidenceFetcher, 60_000)

  return (
    <div style={s.grid}>
      {/* 스코어카드 */}
      <Card title="엣지 스코어카드">
        {scorecardError && <div style={s.errorNote}>오류: {scorecardError}</div>}
        {scorecard ? <Scorecard data={scorecard} /> : !scorecardError && <div style={s.empty}>로딩 중...</div>}
      </Card>

      {/* 에쿼티 곡선 */}
      <Card title="에쿼티 곡선">
        {equityError && <div style={s.errorNote}>오류: {equityError}</div>}
        {equity ? (
          equity.length < 2
            ? <div style={s.empty}>데이터 부족 (스냅샷 &lt; 2개)</div>
            : <EquityChart data={equity} />
        ) : !equityError && <div style={s.empty}>로딩 중...</div>}
      </Card>

      {/* 드로다운 */}
      <Card title="드로다운 곡선">
        {equityError && <div style={s.errorNote}>오류: {equityError}</div>}
        {equity ? (
          equity.length < 2
            ? <div style={s.empty}>데이터 부족</div>
            : <DrawdownChart data={equity} />
        ) : !equityError && <div style={s.empty}>로딩 중...</div>}
      </Card>

      {/* 수익률 분포 히스토그램 (REQ-050-19) */}
      <Card title="일별 수익률 분포">
        {equityError && <div style={s.errorNote}>오류: {equityError}</div>}
        {equity ? (
          equity.length < 2
            ? <div style={s.empty}>데이터 부족</div>
            : <ReturnsDistribution data={equity} />
        ) : !equityError && <div style={s.empty}>로딩 중...</div>}
      </Card>

      {/* 누적 실현손익 (REQ-050-19) */}
      <Card title="누적 손익">
        {equityError && <div style={s.errorNote}>오류: {equityError}</div>}
        {equity ? (
          equity.length < 2
            ? <div style={s.empty}>데이터 부족</div>
            : <CumRealizedPnl data={equity} />
        ) : !equityError && <div style={s.empty}>로딩 중...</div>}
      </Card>

      {/* REQ-050-21: KOSPI 알파 — benchmark_available=false 시 graceful degrade */}
      <Card title="KOSPI 대비 알파">
        {scorecardError && <div style={s.errorNote}>오류: {scorecardError}</div>}
        {scorecard ? (
          scorecard.benchmark_available === false || scorecard.alpha_pct == null
            ? <div style={s.empty}>데이터 없음 (KOSPI 지수 데이터 미가용)</div>
            : (
              <div style={{ padding: '20px 0', textAlign: 'center' }}>
                <div style={{ fontSize: '2.2rem', fontWeight: 700, color: scorecard.alpha_pct >= 0 ? theme.accentGreen : theme.accentRed, fontFamily: 'var(--font-mono)' }}>
                  {scorecard.alpha_pct >= 0 ? '+' : ''}{scorecard.alpha_pct.toFixed(2)}%
                </div>
                <div style={{ color: theme.textSecondary, fontSize: '0.75rem', marginTop: 6 }}>vs KOSPI (누적 알파)</div>
              </div>
            )
        ) : !scorecardError && <div style={s.empty}>로딩 중...</div>}
      </Card>

      {/* Confidence 산점도 / 버킷 */}
      <Card title="Confidence-수익 상관">
        {confidenceError && <div style={s.errorNote}>오류: {confidenceError}</div>}
        {confidence ? (
          <ConfidenceScatter data={confidence} />
        ) : !confidenceError && <div style={s.empty}>로딩 중...</div>}
      </Card>

      {/* Postmortem 4분류 */}
      <Card title="Postmortem 분류">
        {postmortemError && <div style={s.errorNote}>오류: {postmortemError}</div>}
        {postmortem ? (
          <PostmortemBreakdown data={postmortem} />
        ) : !postmortemError && <div style={s.empty}>로딩 중...</div>}
      </Card>
    </div>
  )
}
