// REQ-050-19/20: 일별 수익률 분포 히스토그램 — ECharts
import ReactECharts from 'echarts-for-react'
import type { EquityPoint } from '../../api/types'
import { theme, echartsBaseOpts } from '../../theme'

interface Props {
  data: EquityPoint[]
}

// 일별 수익률 계산 (total_assets 기준)
function calcDailyReturns(data: EquityPoint[]): number[] {
  const returns: number[] = []
  for (let i = 1; i < data.length; i++) {
    const prev = data[i - 1].total_assets
    const curr = data[i].total_assets
    if (prev > 0) {
      returns.push(((curr - prev) / prev) * 100)
    }
  }
  return returns
}

// 히스토그램 버킷 생성
function buildHistogram(returns: number[], bins = 20): { labels: string[]; counts: number[] } {
  if (returns.length === 0) return { labels: [], counts: [] }
  const min = Math.min(...returns)
  const max = Math.max(...returns)
  const range = max - min || 1
  const step = range / bins
  const counts = new Array<number>(bins).fill(0)
  for (const r of returns) {
    const idx = Math.min(Math.floor((r - min) / step), bins - 1)
    counts[idx]++
  }
  const labels = counts.map((_, i) => `${(min + i * step).toFixed(1)}%`)
  return { labels, counts }
}

export default function ReturnsDistribution({ data }: Props) {
  const returns = calcDailyReturns(data)
  const { labels, counts } = buildHistogram(returns, 20)

  if (returns.length === 0) {
    return (
      <div style={{ color: theme.textMuted, fontSize: '0.8rem', padding: '30px 0', textAlign: 'center' }}>
        데이터 부족
      </div>
    )
  }

  const positiveCount = returns.filter((r) => r >= 0).length
  const negativeCount = returns.filter((r) => r < 0).length
  const avgReturn = returns.reduce((a, b) => a + b, 0) / returns.length

  const option = {
    ...echartsBaseOpts,
    tooltip: {
      ...echartsBaseOpts.tooltip,
      trigger: 'axis',
      formatter: (params: unknown[]) => {
        const p = (params as Array<{ name: string; value: number }>)[0]
        return `${p.name}<br/>빈도: ${p.value}회`
      },
    },
    xAxis: {
      ...echartsBaseOpts.xAxis,
      type: 'category',
      data: labels,
      axisLabel: { ...echartsBaseOpts.xAxis.axisLabel, rotate: 30, fontSize: 9 },
    },
    yAxis: {
      ...echartsBaseOpts.yAxis,
      type: 'value',
      name: '빈도',
      nameTextStyle: { color: theme.textMuted, fontSize: 10 },
    },
    series: [
      {
        type: 'bar',
        data: counts.map((c, i) => ({
          value: c,
          itemStyle: {
            color: parseFloat(labels[i] ?? '0') >= 0 ? theme.accentGreen + 'cc' : theme.accentRed + 'cc',
          },
        })),
        barCategoryGap: '5%',
      },
    ],
  }

  return (
    <div>
      <ReactECharts option={option} style={{ height: 200 }} opts={{ renderer: 'canvas' }} />
      <div style={{ display: 'flex', gap: 16, marginTop: 6, fontSize: '0.7rem', color: theme.textSecondary }}>
        <span>
          양수: <strong style={{ color: theme.accentGreen }}>{positiveCount}일</strong>
        </span>
        <span>
          음수: <strong style={{ color: theme.accentRed }}>{negativeCount}일</strong>
        </span>
        <span>
          평균: <strong style={{ color: avgReturn >= 0 ? theme.accentGreen : theme.accentRed }}>
            {avgReturn.toFixed(3)}%
          </strong>
        </span>
      </div>
    </div>
  )
}
