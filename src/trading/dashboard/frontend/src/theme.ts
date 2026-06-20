// REQ-054-B1/B2: 라이트 전문 팔레트 + CSS 변수 기반 토큰
// 전문 자산운용 도구 톤: 연회색 배경, 흰 카드, 파랑/초록/빨강 강조색

export const theme = {
  // 배경 계층 (라이트 팔레트)
  bg: '#f6f8fa',           // 앱 배경 — 연회색 (AC-7 기준)
  bgPanel: '#ffffff',      // 패널/사이드바 배경 — 흰색
  bgCard: '#ffffff',       // 카드 배경 — 흰색
  bgHover: '#f3f4f6',      // 호버 상태

  // 경계선 (연한 회색)
  border: '#d0d7de',
  borderLight: '#e8ecf0',

  // 텍스트 (어두운 계열 — 라이트 배경에서 가독성)
  textPrimary: '#1f2328',
  textSecondary: '#656d76',
  textMuted: '#9ca3af',

  // 강조색 — 전문 금융 톤
  accentBlue: '#0969da',     // 중립/정보
  accentGreen: '#1a7f37',    // 이익/양수
  accentRed: '#cf222e',      // 손실/음수
  accentYellow: '#9a6700',   // 경고
  accentOrange: '#bc4c00',
  accentPurple: '#8250df',

  // 상태별 색
  buy: '#1a7f37',
  sell: '#cf222e',
  halt: '#cf222e',
  warning: '#9a6700',
  ok: '#1a7f37',

  // 사이드바
  sidebarWidth: '220px',
  sidebarCollapsedWidth: '0px',

  // ECharts 라이트 테마 팔레트
  chartPalette: [
    '#0969da',
    '#1a7f37',
    '#cf222e',
    '#9a6700',
    '#8250df',
    '#1b7fc4',
    '#bc4c00',
    '#0f786b',
  ],

  // 폰트
  fontMono: "'Courier New', 'Consolas', monospace",
  fontSans: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans KR', sans-serif",

  // 반응형 브레이크포인트
  mobileBreak: '768px',
  wideBreak: '1280px',
} as const

// ECharts 공통 옵션 (라이트 테마)
// REQ-054-B1: 라이트 배경에 맞게 축/그리드/툴팁 색상 조정
export const echartsBaseOpts = {
  backgroundColor: 'transparent',
  textStyle: { color: theme.textSecondary, fontFamily: theme.fontSans, fontSize: 11 },
  tooltip: {
    backgroundColor: '#ffffff',
    borderColor: theme.border,
    textStyle: { color: theme.textPrimary, fontSize: 11 },
    extraCssText: 'box-shadow: 0 4px 12px rgba(0,0,0,0.12);',
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
    splitLine: { lineStyle: { color: theme.borderLight, type: 'dashed' as const } },
  },
  yAxis: {
    axisLine: { lineStyle: { color: theme.border } },
    axisTick: { lineStyle: { color: theme.border } },
    axisLabel: { color: theme.textSecondary, fontSize: 10 },
    splitLine: { lineStyle: { color: theme.borderLight, type: 'dashed' as const } },
  },
} as const
