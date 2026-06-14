// REQ-050-10: 다크 전문 금융 테마 토큰
export const theme = {
  // 배경 계층
  bg: '#0d1117',
  bgPanel: '#161b22',
  bgCard: '#1c2128',
  bgHover: '#21262d',

  // 경계선
  border: '#30363d',
  borderLight: '#21262d',

  // 텍스트
  textPrimary: '#e6edf3',
  textSecondary: '#8b949e',
  textMuted: '#6e7681',

  // 강조색
  accentBlue: '#58a6ff',
  accentGreen: '#3fb950',
  accentRed: '#f85149',
  accentYellow: '#e3b341',
  accentOrange: '#d29922',
  accentPurple: '#a371f7',
  accentCyan: '#39d353',

  // 상태별 색
  buy: '#3fb950',
  sell: '#f85149',
  halt: '#da3633',
  warning: '#e3b341',
  ok: '#3fb950',

  // ECharts 테마 색상 팔레트
  chartPalette: [
    '#58a6ff',
    '#3fb950',
    '#f85149',
    '#e3b341',
    '#a371f7',
    '#39d353',
    '#d29922',
    '#79c0ff',
  ],

  // 폰트
  fontMono: "'Courier New', 'Consolas', monospace",
  fontSans: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",

  // 반응형 브레이크포인트
  mobileBreak: '768px',
} as const

// ECharts 공통 옵션 (다크 테마)
export const echartsBaseOpts = {
  backgroundColor: 'transparent',
  textStyle: { color: theme.textSecondary, fontFamily: theme.fontMono, fontSize: 11 },
  tooltip: {
    backgroundColor: theme.bgPanel,
    borderColor: theme.border,
    textStyle: { color: theme.textPrimary, fontSize: 11 },
  },
  grid: {
    left: 60,
    right: 20,
    top: 30,
    bottom: 40,
    containLabel: false,
  },
  xAxis: {
    axisLine: { lineStyle: { color: theme.border } },
    axisTick: { lineStyle: { color: theme.border } },
    axisLabel: { color: theme.textSecondary, fontSize: 10 },
    splitLine: { lineStyle: { color: theme.borderLight, type: 'dashed' } },
  },
  yAxis: {
    axisLine: { lineStyle: { color: theme.border } },
    axisTick: { lineStyle: { color: theme.border } },
    axisLabel: { color: theme.textSecondary, fontSize: 10 },
    splitLine: { lineStyle: { color: theme.borderLight, type: 'dashed' } },
  },
} as const
