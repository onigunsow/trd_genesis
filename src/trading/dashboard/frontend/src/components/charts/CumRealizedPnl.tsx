// REQ-050-19/20: 누적 실현손익 곡선 — ECharts
// EquityPoint.unrealized_pnl 을 대리 지표로 사용; 실현손익은 별도 필드 없음 → total_assets 기반 PnL 표시
import ReactECharts from 'echarts-for-react'
import type { EquityPoint } from '../../api/types'
import { theme, echartsBaseOpts } from '../../theme'

interface Props {
  data: EquityPoint[]
}

export default function CumRealizedPnl({ data }: Props) {
  // 첫 날 기준 누적 손익 계산 (총자산 기준)
  const base = data[0]?.total_assets ?? 0
  const dates = data.map((d) => d.trading_day)
  const pnls = data.map((d) => d.total_assets - base)
  // 미실현 손익도 별도 표시
  const unrealized = data.map((d) => d.unrealized_pnl ?? null)

  const option = {
    ...echartsBaseOpts,
    tooltip: {
      ...echartsBaseOpts.tooltip,
      trigger: 'axis',
      formatter: (params: unknown[]) => {
        const ps = params as Array<{ name: string; value: number | null; seriesName: string }>
        return (
          ps[0].name +
          ps.map((p) =>
            `<br/>${p.seriesName}: ₩${(p.value ?? 0).toLocaleString('ko-KR')}`
          ).join('')
        )
      },
    },
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
      {
        type: 'slider', height: 18, bottom: 5,
        borderColor: theme.border,
        fillerColor: theme.accentGreen + '22',
        handleStyle: { color: theme.accentGreen },
      },
    ],
    legend: {
      data: ['누적 손익', '미실현 손익'],
      textStyle: { color: theme.textSecondary, fontSize: 10 },
      top: 0,
    },
    xAxis: { ...echartsBaseOpts.xAxis, type: 'category', data: dates },
    yAxis: {
      ...echartsBaseOpts.yAxis,
      type: 'value',
      axisLabel: {
        ...echartsBaseOpts.yAxis.axisLabel,
        formatter: (v: number) =>
          v >= 0
            ? `+${(v / 1_000).toFixed(0)}K`
            : `${(v / 1_000).toFixed(0)}K`,
      },
    },
    series: [
      {
        name: '누적 손익',
        type: 'line',
        data: pnls,
        smooth: true,
        lineStyle: { color: theme.accentGreen, width: 2 },
        itemStyle: { color: theme.accentGreen },
        areaStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: theme.accentGreen + '33' },
              { offset: 1, color: theme.accentGreen + '00' },
            ],
          },
        },
        symbol: 'none',
      },
      {
        name: '미실현 손익',
        type: 'line',
        data: unrealized,
        smooth: true,
        lineStyle: { color: theme.accentYellow, width: 1.5, type: 'dashed' },
        itemStyle: { color: theme.accentYellow },
        symbol: 'none',
      },
    ],
  }

  return (
    <ReactECharts
      option={option}
      style={{ height: 220 }}
      opts={{ renderer: 'canvas' }}
    />
  )
}
