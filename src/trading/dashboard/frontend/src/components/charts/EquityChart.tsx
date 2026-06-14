// REQ-050-19/20: 에쿼티 곡선 — ECharts (호버 툴팁 + 줌/팬)
import ReactECharts from 'echarts-for-react'
import type { EquityPoint } from '../../api/types'
import { theme, echartsBaseOpts } from '../../theme'

interface Props {
  data: EquityPoint[]
}

export default function EquityChart({ data }: Props) {
  const dates = data.map((d) => d.trading_day)
  const assets = data.map((d) => d.total_assets)

  const option = {
    ...echartsBaseOpts,
    tooltip: {
      ...echartsBaseOpts.tooltip,
      trigger: 'axis',
      formatter: (params: unknown[]) => {
        const p = (params as Array<{ name: string; value: number }>)[0]
        return `${p.name}<br/>총자산: ₩${p.value.toLocaleString('ko-KR')}`
      },
    },
    // REQ-050-20: 줌/팬
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
      { type: 'slider', height: 20, bottom: 5, borderColor: theme.border, fillerColor: theme.accentBlue + '22', handleStyle: { color: theme.accentBlue } },
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
        formatter: (v: number) => `${(v / 1_000_000).toFixed(1)}M`,
      },
    },
    series: [
      {
        type: 'line',
        data: assets,
        smooth: true,
        lineStyle: { color: theme.accentBlue, width: 2 },
        itemStyle: { color: theme.accentBlue },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: theme.accentBlue + '44' },
              { offset: 1, color: theme.accentBlue + '00' },
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
      style={{ height: 220 }}
      opts={{ renderer: 'canvas' }}
    />
  )
}
