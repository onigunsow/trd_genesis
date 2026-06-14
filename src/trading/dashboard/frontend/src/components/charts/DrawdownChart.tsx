// REQ-050-19/20: 드로다운 곡선 — ECharts
import ReactECharts from 'echarts-for-react'
import type { EquityPoint } from '../../api/types'
import { theme, echartsBaseOpts } from '../../theme'

interface Props {
  data: EquityPoint[]
}

export default function DrawdownChart({ data }: Props) {
  const dates = data.map((d) => d.trading_day)
  // drawdown_pct 는 서버에서 제공 (REQ-050-5); null 은 0으로 처리
  const drawdowns = data.map((d) => (d.drawdown_pct ?? 0) * 100)

  const option = {
    ...echartsBaseOpts,
    tooltip: {
      ...echartsBaseOpts.tooltip,
      trigger: 'axis',
      formatter: (params: unknown[]) => {
        const p = (params as Array<{ name: string; value: number }>)[0]
        return `${p.name}<br/>드로다운: ${p.value.toFixed(2)}%`
      },
    },
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
      { type: 'slider', height: 20, bottom: 5, borderColor: theme.border, fillerColor: theme.accentRed + '22', handleStyle: { color: theme.accentRed } },
    ],
    xAxis: {
      ...echartsBaseOpts.xAxis,
      type: 'category',
      data: dates,
    },
    yAxis: {
      ...echartsBaseOpts.yAxis,
      type: 'value',
      axisLabel: {
        ...echartsBaseOpts.yAxis.axisLabel,
        formatter: (v: number) => `${v.toFixed(1)}%`,
      },
      max: 0,
    },
    series: [
      {
        type: 'line',
        data: drawdowns,
        smooth: true,
        lineStyle: { color: theme.accentRed, width: 2 },
        itemStyle: { color: theme.accentRed },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: theme.accentRed + '00' },
              { offset: 1, color: theme.accentRed + '44' },
            ],
          },
        },
        symbol: 'none',
      },
    ],
  }

  return (
    <ReactECharts
      option={option}
      style={{ height: 200 }}
      opts={{ renderer: 'canvas' }}
    />
  )
}
