// REQ-050-23: 키워드 트렌드 — 감성 분포 누적 막대 (ECharts)
import ReactECharts from 'echarts-for-react'
import type { TrendPoint } from '../../api/types'
import { theme, echartsBaseOpts } from '../../theme'

interface Props {
  data: TrendPoint[]
}

export default function KeywordTrends({ data }: Props) {
  if (data.length === 0) {
    return (
      <div style={{ color: theme.textMuted, fontSize: '0.8rem', padding: '20px 0', textAlign: 'center' }}>
        트렌드 데이터 없음
      </div>
    )
  }

  // 키워드별로 그룹화 (상위 10개 mention_count 기준)
  const byKeyword = new Map<string, TrendPoint[]>()
  for (const p of data) {
    const arr = byKeyword.get(p.keyword) ?? []
    arr.push(p)
    byKeyword.set(p.keyword, arr)
  }

  // 키워드별 총 mention_count 합산 → 상위 10개
  const topKeywords = [...byKeyword.entries()]
    .map(([kw, pts]) => ({ kw, total: pts.reduce((s, p) => s + p.mention_count, 0) }))
    .sort((a, b) => b.total - a.total)
    .slice(0, 10)
    .map((x) => x.kw)

  // 각 키워드별 mention_count / sentiment_positive / negative 집계
  const kwTotals = topKeywords.map((kw) => {
    const pts = byKeyword.get(kw) ?? []
    return {
      kw,
      mention: pts.reduce((s, p) => s + p.mention_count, 0),
      pos: pts.reduce((s, p) => s + p.sentiment_positive, 0),
      neg: pts.reduce((s, p) => s + p.sentiment_negative, 0),
      neu: pts.reduce((s, p) => s + p.sentiment_neutral, 0),
    }
  })

  const option = {
    ...echartsBaseOpts,
    tooltip: {
      ...echartsBaseOpts.tooltip,
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: unknown[]) => {
        const ps = params as Array<{ name: string; value: number; seriesName: string }>
        const name = ps[0]?.name ?? ''
        return name + ps.map((p) => `<br/>${p.seriesName}: ${p.value}`).join('')
      },
    },
    legend: {
      data: ['긍정', '중립', '부정'],
      textStyle: { color: theme.textSecondary, fontSize: 10 },
      top: 0,
    },
    grid: { ...echartsBaseOpts.grid, left: 100, bottom: 20 },
    xAxis: { ...echartsBaseOpts.xAxis, type: 'value' },
    yAxis: {
      ...echartsBaseOpts.yAxis,
      type: 'category',
      data: kwTotals.map((k) => k.kw).reverse(),
      axisLabel: { ...echartsBaseOpts.yAxis.axisLabel, fontSize: 10 },
    },
    series: [
      {
        name: '긍정',
        type: 'bar',
        stack: 'total',
        data: kwTotals.map((k) => k.pos).reverse(),
        itemStyle: { color: theme.accentGreen + 'cc' },
      },
      {
        name: '중립',
        type: 'bar',
        stack: 'total',
        data: kwTotals.map((k) => k.neu).reverse(),
        itemStyle: { color: theme.textSecondary + 'cc' },
      },
      {
        name: '부정',
        type: 'bar',
        stack: 'total',
        data: kwTotals.map((k) => k.neg).reverse(),
        itemStyle: { color: theme.accentRed + 'cc' },
      },
    ],
  }

  return (
    <ReactECharts
      option={option}
      style={{ height: Math.max(180, topKeywords.length * 24 + 60) }}
      opts={{ renderer: 'canvas' }}
    />
  )
}
