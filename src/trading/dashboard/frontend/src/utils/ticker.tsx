// REQ-054-F2: 종목코드 + 한국어 종목명 표시 헬퍼
// 백엔드 계약: ticker_name 은 항상 string (미등록 시 코드 자체를 반환, 절대 null 아님)
// 표시 규칙: 이름 ≠ 코드 → "종목명 (코드)" / 같으면 코드만 표시 (중복 방지)

import React from 'react'

/**
 * 종목명 + 코드를 "신한지주 (055550)" 형태로 포맷한다.
 * ticker_name 이 코드와 동일하면 코드 하나만 반환한다.
 */
export function formatTicker(ticker: string, tickerName: string | null | undefined): string {
  if (!tickerName || tickerName === ticker) return ticker
  return `${tickerName} (${ticker})`
}

interface TickerLabelProps {
  ticker: string
  tickerName: string | null | undefined
  /** 코드(보조 텍스트) 색상. 기본값: var(--text-muted) */
  mutedColor?: string
  className?: string
  style?: React.CSSProperties
}

/**
 * 종목명(1차) + 코드(2차/muted) 를 인라인으로 표시하는 컴포넌트.
 * ticker_name 이 코드와 동일하면 코드만 표시한다.
 *
 * @MX:NOTE: [AUTO] 전체 대시보드에서 공유하는 ticker 표시 단일소스 (fan_in >= 6)
 * @MX:ANCHOR: [AUTO] 종목명 표시 인터페이스 — 수정 시 모든 테이블·카드에 영향
 * @MX:REASON: HoldingsTable/OrdersTable/RoundtripLedger/PortfolioView/PipelineView 에서 공통 사용
 */
export function TickerLabel({ ticker, tickerName, mutedColor, style, className }: TickerLabelProps) {
  // ticker_name 이 없거나 코드와 동일 → 코드만
  if (!tickerName || tickerName === ticker) {
    return (
      <span style={style} className={className}>
        {ticker}
      </span>
    )
  }

  return (
    <span style={{ display: 'inline-flex', flexDirection: 'column', lineHeight: 1.3, ...style }} className={className}>
      <span>{tickerName}</span>
      <span style={{ fontSize: '0.68em', color: mutedColor ?? 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
        {ticker}
      </span>
    </span>
  )
}
