// REQ-054-C4: 기간 손익 추이 뷰
// - 일/주/월 실현손익 막대 + 누적 라인
// - 전체기간 KOSPI 알파는 scorecard 에서 별도 표시 (기간별 alpha_pct 는 백엔드 한계로 null)
// - 백엔드 한계 정직 표시: per-period alpha_pct = null → "전체기간 알파" 별도 표기
import { useState, useCallback } from 'react'
import ReactECharts from 'echarts-for-react'
import { usePolling } from '../hooks/usePolling'
import { api } from '../api/client'
import type { PnlDailyResponse, Scorecard } from '../api/types'
import { theme, echartsBaseOpts } from '../theme'

type Period = 'daily' | 'weekly' | 'monthly'

interface Props {
  scorecard?: Scorecard | null
}

export default function PnlTrendView({ scorecard }: Props) {
  const [period, setPeriod] = useState<Period>('daily')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')

  const fetcher = useCallback(
    () => api.fetchPnlDaily(180, period, startDate || undefined, endDate || undefined),
    [period, startDate, endDate],
  )
  const { data, isLoading, error } = usePolling(fetcher, 60_000)

  const cardStyle: React.CSSProperties = {
    background: 'var(--bg-card)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius)',
    padding: '16px',
    boxShadow: 'var(--shadow-sm)',
  }
  const labelStyle: React.CSSProperties = {
    fontSize: '0.72rem',
    color: 'var(--text-muted)',
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    marginBottom: 12,
  }
  const btnStyle = (active: boolean): React.CSSProperties => ({
    padding: '5px 12px',
    fontSize: '0.75rem',
    border: '1px solid var(--border)',
    borderRadius: 6,
    cursor: 'pointer',
    background: active ? 'var(--accent-blue)' : 'var(--bg)',
    color: active ? '#fff' : 'var(--text-secondary)',
    fontWeight: active ? 600 : 400,
  })

  // 차트 옵션
  const buildChartOption = (pnlData: PnlDailyResponse) => {
    const labels = pnlData.rows.map(r => r.period_label)
    const realizedVals = pnlData.rows.map(r => r.realized_pnl)
    const cumulativeVals = pnlData.rows.map(r => r.cumulative_pnl)

    return {
      ...echartsBaseOpts,
      tooltip: {
        ...echartsBaseOpts.tooltip,
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: unknown[]) => {
          const ps = params as Array<{ seriesName: string; value: number; color: string }>
          let html = `<div style="font-size:11px"><b>${(ps[0] as { axisValueLabel?: string }).axisValueLabel ?? ''}</b><br/>`
          ps.forEach(p => {
            const sign = (p.value ?? 0) >= 0 ? '+' : ''
            html += `<span style="color:${p.color}">●</span> ${p.seriesName}: ${sign}${Math.round(p.value ?? 0).toLocaleString()}원<br/>`
          })
          return html + '</div>'
        },
      },
      legend: {
        top: 5,
        right: 10,
        textStyle: { color: theme.textSecondary, fontSize: 11 },
      },
      xAxis: {
        ...echartsBaseOpts.xAxis,
        type: 'category',
        data: labels,
        axisLabel: { color: theme.textSecondary, fontSize: 9, rotate: labels.length > 20 ? 30 : 0 },
      },
      yAxis: [
        { ...echartsBaseOpts.yAxis, type: 'value', name: '실현손익(원)', position: 'left' },
        { ...echartsBaseOpts.yAxis, type: 'value', name: '누적(원)', position: 'right', splitLine: { show: false } },
      ],
      series: [
        {
          name: '실현손익',
          type: 'bar',
          data: realizedVals.map(v => ({
            value: v,
            itemStyle: { color: v >= 0 ? theme.accentGreen : theme.accentRed },
          })),
          yAxisIndex: 0,
        },
        {
          name: '누적 실현손익',
          type: 'line',
          data: cumulativeVals,
          smooth: true,
          yAxisIndex: 1,
          lineStyle: { color: theme.accentBlue, width: 2 },
          itemStyle: { color: theme.accentBlue },
          symbol: 'none',
        },
      ],
    }
  }

  // 전체기간 알파 표시 (기간별 alpha_pct 는 null 이므로 정직하게 별도 표기)
  const alphaPct = scorecard?.alpha_pct
  const alphaAvail = scorecard?.benchmark_available !== false && alphaPct != null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* 전체기간 알파 안내 — 정직한 UI (REQ-054-A8, 기간별 null 근거) */}
      <div
        style={{
          ...cardStyle,
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          padding: '12px 16px',
        }}
      >
        <div>
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', fontWeight: 600 }}>
            전체기간 알파 (vs KOSPI)
          </span>
          <div style={{
            fontSize: '1.1rem',
            fontWeight: 700,
            fontFamily: 'var(--font-mono)',
            color: alphaAvail && alphaPct != null
              ? (alphaPct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)')
              : 'var(--text-muted)',
          }}>
            {alphaAvail && alphaPct != null
              ? `${alphaPct >= 0 ? '+' : ''}${alphaPct.toFixed(2)}%`
              : '—'}
          </div>
          <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)' }}>
            * 기간별 알파는 백엔드 미지원 → 전체기간 단일값만 표시
          </div>
        </div>
      </div>

      {/* 컨트롤 */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
        {(['daily', 'weekly', 'monthly'] as Period[]).map(p => (
          <button key={p} style={btnStyle(period === p)} onClick={() => setPeriod(p)}>
            {p === 'daily' ? '일별' : p === 'weekly' ? '주별' : '월별'}
          </button>
        ))}
        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginLeft: 8 }}>기간:</span>
        <input
          type="date"
          value={startDate}
          onChange={e => setStartDate(e.target.value)}
          style={{ padding: '5px 8px', border: '1px solid var(--border)', borderRadius: 6, fontSize: '0.75rem', background: 'var(--bg)', color: 'var(--text-primary)' }}
          aria-label="시작 날짜"
        />
        <span style={{ color: 'var(--text-muted)' }}>~</span>
        <input
          type="date"
          value={endDate}
          onChange={e => setEndDate(e.target.value)}
          style={{ padding: '5px 8px', border: '1px solid var(--border)', borderRadius: 6, fontSize: '0.75rem', background: 'var(--bg)', color: 'var(--text-primary)' }}
          aria-label="종료 날짜"
        />
        <button
          onClick={() => api.exportPnlDaily()}
          style={{
            marginLeft: 'auto',
            padding: '5px 12px',
            fontSize: '0.75rem',
            background: 'var(--accent-blue)',
            color: '#fff',
            border: 'none',
            borderRadius: 6,
            cursor: 'pointer',
          }}
          aria-label="손익 CSV 내보내기"
        >
          CSV 내보내기
        </button>
      </div>

      {/* 차트 */}
      <div style={cardStyle}>
        {error && (
          <div style={{ color: 'var(--accent-red)', fontSize: '0.8rem', marginBottom: 8 }}>
            데이터 로드 오류: {error}
          </div>
        )}
        {isLoading && !data ? (
          <div style={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>
            로딩 중...
          </div>
        ) : !data || data.rows.length === 0 ? (
          <div style={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>
            손익 데이터 없음
          </div>
        ) : (
          <ReactECharts option={buildChartOption(data)} style={{ height: 300 }} notMerge />
        )}
      </div>
    </div>
  )
}
