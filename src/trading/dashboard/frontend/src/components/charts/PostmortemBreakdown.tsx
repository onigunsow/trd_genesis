// REQ-050-19/20: Postmortem 4분류 + 페르소나 귀인 — ECharts 파이/막대
import ReactECharts from 'echarts-for-react'
import type { PostmortemResult } from '../../api/types'
import { theme, echartsBaseOpts } from '../../theme'

interface Props {
  data: PostmortemResult
}

const CLASS_COLORS: Record<string, string> = {
  TRUE_POSITIVE: theme.accentGreen,
  FALSE_POSITIVE: theme.accentRed,
  REGIME_MISMATCH: theme.accentYellow,
  MISSED: theme.textMuted,
}

const CLASS_LABELS: Record<string, string> = {
  TRUE_POSITIVE: 'TP (적중)',
  FALSE_POSITIVE: 'FP (오신호)',
  REGIME_MISMATCH: 'REGIME_MISMATCH',
  MISSED: 'MISSED (기회 누락)',
}

export default function PostmortemBreakdown({ data }: Props) {
  // 백엔드 /api/postmortem 은 distribution/per_persona 키로 반환한다(counts/by_persona 아님).
  // 양쪽 키 + null 을 모두 방어하여 Object.entries(undefined) 크래시를 방지한다.
  const d = (data ?? {}) as unknown as Record<string, unknown>
  const counts = (d.distribution ?? d.counts ?? {}) as Record<string, number>
  const total = (d.total as number) ?? 0
  const by_persona = (d.per_persona ?? d.by_persona ?? {}) as Record<string, Record<string, number>>

  // 파이 차트: 4분류 전체
  const pieData = Object.entries(counts).map(([k, v]) => ({
    name: CLASS_LABELS[k] ?? k,
    value: v,
    itemStyle: { color: CLASS_COLORS[k] ?? theme.accentBlue },
  }))

  const pieOption = {
    ...echartsBaseOpts,
    tooltip: {
      ...echartsBaseOpts.tooltip,
      trigger: 'item',
      formatter: '{b}: {c} ({d}%)',
    },
    series: [
      {
        type: 'pie',
        radius: ['40%', '70%'],
        data: pieData,
        label: { color: theme.textSecondary, fontSize: 10 },
        labelLine: { lineStyle: { color: theme.border } },
      },
    ],
  }

  // 페르소나별 귀인 막대 (by_persona 가 있을 때)
  const personas = Object.keys(by_persona)
  const hasPersona = personas.length > 0

  const barOption = hasPersona ? {
    ...echartsBaseOpts,
    tooltip: { ...echartsBaseOpts.tooltip, trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: {
      data: ['TP', 'FP', 'REGIME_MISMATCH', 'MISSED'],
      textStyle: { color: theme.textSecondary, fontSize: 10 },
      top: 0,
    },
    xAxis: { ...echartsBaseOpts.xAxis, type: 'category', data: personas, axisLabel: { ...echartsBaseOpts.xAxis.axisLabel, rotate: 20 } },
    yAxis: { ...echartsBaseOpts.yAxis, type: 'value' },
    series: (['TP', 'FP', 'REGIME_MISMATCH', 'MISSED'] as const).map((cls) => ({
      name: cls,
      type: 'bar',
      stack: 'total',
      data: personas.map((p) => by_persona[p]?.[cls] ?? 0),
      itemStyle: { color: CLASS_COLORS[cls] },
    })),
  } : null

  return (
    <div>
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' as const, marginBottom: 8 }}>
        {Object.entries(counts).map(([k, v]) => (
          <span key={k} style={{ fontSize: '0.7rem', padding: '2px 8px', borderRadius: 10, background: (CLASS_COLORS[k] ?? theme.accentBlue) + '22', color: CLASS_COLORS[k] ?? theme.accentBlue }}>
            {k}: {v}
          </span>
        ))}
        <span style={{ fontSize: '0.7rem', color: theme.textMuted }}>총 {total}건 / {data.days}일</span>
      </div>

      {total === 0 ? (
        <div style={{ color: theme.textMuted, fontSize: '0.8rem', padding: '30px 0', textAlign: 'center' }}>데이터 없음</div>
      ) : (
        <ReactECharts option={pieOption} style={{ height: 180 }} opts={{ renderer: 'canvas' }} />
      )}

      {hasPersona && barOption && (
        <>
          <div style={{ fontSize: '0.7rem', color: theme.textSecondary, margin: '10px 0 4px' }}>페르소나별 귀인</div>
          <ReactECharts option={barOption} style={{ height: 160 }} opts={{ renderer: 'canvas' }} />
        </>
      )}
    </div>
  )
}
