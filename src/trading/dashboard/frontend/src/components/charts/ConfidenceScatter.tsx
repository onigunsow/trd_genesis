// REQ-050-19/20: Confidence-수익 상관 — 버킷 막대 + 상관계수
import ReactECharts from 'echarts-for-react'
import type { ConfidenceAnalysis } from '../../api/types'
import { theme, echartsBaseOpts } from '../../theme'

interface Props {
  data: ConfidenceAnalysis
}

export default function ConfidenceScatter({ data }: Props) {
  const buckets = data.buckets
  const labels = buckets.map((b) => b.bucket)
  const winRates = buckets.map((b) => b.win_rate != null ? +(b.win_rate * 100).toFixed(1) : null)
  const counts = buckets.map((b) => b.count)

  const option = {
    ...echartsBaseOpts,
    tooltip: {
      ...echartsBaseOpts.tooltip,
      trigger: 'axis',
      formatter: (params: unknown[]) => {
        const ps = params as Array<{ name: string; value: number | null; seriesName: string }>
        return ps.map(p => `${p.seriesName}: ${p.value ?? '—'}`).join('<br/>')
      },
    },
    legend: {
      data: ['승률(%)', '거래 수'],
      textStyle: { color: theme.textSecondary, fontSize: 10 },
      top: 0,
    },
    xAxis: {
      ...echartsBaseOpts.xAxis,
      type: 'category',
      data: labels,
      axisLabel: { ...echartsBaseOpts.xAxis.axisLabel, rotate: 30 },
    },
    yAxis: [
      {
        ...echartsBaseOpts.yAxis,
        type: 'value',
        name: '승률(%)',
        nameTextStyle: { color: theme.textMuted, fontSize: 10 },
        axisLabel: { ...echartsBaseOpts.yAxis.axisLabel, formatter: (v: number) => `${v}%` },
      },
      {
        ...echartsBaseOpts.yAxis,
        type: 'value',
        name: '거래 수',
        nameTextStyle: { color: theme.textMuted, fontSize: 10 },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '승률(%)',
        type: 'bar',
        yAxisIndex: 0,
        data: winRates,
        itemStyle: { color: theme.accentBlue },
        barMaxWidth: 40,
      },
      {
        name: '거래 수',
        type: 'line',
        yAxisIndex: 1,
        data: counts,
        lineStyle: { color: theme.accentYellow, width: 1.5 },
        itemStyle: { color: theme.accentYellow },
        symbol: 'circle',
        symbolSize: 5,
      },
    ],
  }

  const pearsonStr = data.pearson != null ? data.pearson.toFixed(3) : '—'
  const spearmanStr = data.spearman != null ? data.spearman.toFixed(3) : '—'

  return (
    <div>
      <ReactECharts option={option} style={{ height: 200 }} opts={{ renderer: 'canvas' }} />
      <div style={{ display: 'flex', gap: 16, marginTop: 8, fontSize: '0.72rem', color: theme.textSecondary }}>
        <span>Pearson: <strong style={{ color: theme.textPrimary }}>{pearsonStr}</strong></span>
        <span>Spearman: <strong style={{ color: theme.textPrimary }}>{spearmanStr}</strong></span>
        <span style={{ color: theme.textMuted }}>(최근 {data.days}일)</span>
      </div>
    </div>
  )
}
