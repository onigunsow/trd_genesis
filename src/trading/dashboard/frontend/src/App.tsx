// REQ-054-B3: 좌측 사이드바 네비게이션 (상단 탭 → 사이드바 교체)
// REQ-054-B4: 패널별 ErrorBoundary 격리
// REQ-054-B3: ≤768px collapsible, ≥1280px 콘텐츠 다열 그리드, 가로 오버플로 없음
import { useState, useCallback } from 'react'
import { usePolling } from './hooks/usePolling'
import { api } from './api/client'
import StatusBar from './components/StatusBar'
import PipelineView from './components/PipelineView'
import ChartsView from './components/ChartsView'
import NewsView from './components/NewsView'
import HoldingsTable from './components/HoldingsTable'
import OrdersTable from './components/OrdersTable'
import ErrorBoundary from './components/ErrorBoundary'
import KpiCards from './components/KpiCards'
import PortfolioView from './components/PortfolioView'
import RoundtripLedger from './components/RoundtripLedger'
import PnlTrendView from './components/PnlTrendView'

type View = 'overview' | 'portfolio' | 'roundtrips' | 'pnl' | 'pipeline' | 'news' | 'positions'

const NAV_ITEMS: Array<{ id: View; label: string; icon: string }> = [
  { id: 'overview',   label: '개요',        icon: '◈' },
  { id: 'portfolio',  label: '포트폴리오',   icon: '◉' },
  { id: 'roundtrips', label: '거래원장',     icon: '◎' },
  { id: 'pnl',        label: '손익 추이',    icon: '◆' },
  { id: 'pipeline',   label: '파이프라인',   icon: '▶' },
  { id: 'news',       label: '뉴스',         icon: '◐' },
  { id: 'positions',  label: '포지션/주문',  icon: '☰' },
]

export default function App() {
  const [activeView, setActiveView] = useState<View>('overview')
  const [sidebarOpen, setSidebarOpen] = useState(true)

  const statusFetcher = useCallback(() => api.fetchStatus(), [])
  const { data: status, error: statusError, lastUpdatedAt } = usePolling(statusFetcher, 10_000)

  const lastUpdatedStr = lastUpdatedAt
    ? lastUpdatedAt.toLocaleTimeString('ko-KR', { hour12: false, timeZone: 'Asia/Seoul' })
    : null

  // 사이드바 스타일 — CSS 변수 토큰 참조 (REQ-054-B2)
  const sidebarStyle: React.CSSProperties = {
    width: sidebarOpen ? 'var(--sidebar-width)' : '0',
    minWidth: sidebarOpen ? 'var(--sidebar-width)' : '0',
    overflow: 'hidden',
    background: 'var(--bg-panel)',
    borderRight: '1px solid var(--border)',
    display: 'flex',
    flexDirection: 'column',
    transition: 'width 0.2s ease, min-width 0.2s ease',
    flexShrink: 0,
  }

  const navBtnStyle = (active: boolean): React.CSSProperties => ({
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '10px 16px',
    width: '100%',
    border: 'none',
    background: active ? '#e8f0fe' : 'transparent',
    color: active ? 'var(--accent-blue)' : 'var(--text-secondary)',
    fontSize: '0.82rem',
    fontWeight: active ? 600 : 400,
    cursor: 'pointer',
    textAlign: 'left' as const,
    borderLeft: active ? '3px solid var(--accent-blue)' : '3px solid transparent',
    whiteSpace: 'nowrap',
    transition: 'background 0.1s',
  })

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      minHeight: '100vh',
      background: 'var(--bg)',
      overflow: 'hidden',
    }}>
      {/* 헤더 */}
      <header style={{
        display: 'flex',
        alignItems: 'center',
        padding: '0 16px',
        height: 48,
        background: 'var(--bg-panel)',
        borderBottom: '1px solid var(--border)',
        boxShadow: 'var(--shadow-sm)',
        flexShrink: 0,
        gap: 12,
        zIndex: 10,
      }}>
        {/* 햄버거 토글 (REQ-054-B3: ≤768px collapsible) */}
        <button
          onClick={() => setSidebarOpen(o => !o)}
          aria-label={sidebarOpen ? '사이드바 닫기' : '사이드바 열기'}
          style={{
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            fontSize: '1.1rem',
            color: 'var(--text-secondary)',
            lineHeight: 1,
            padding: '4px 6px',
            borderRadius: 4,
          }}
        >
          ☰
        </button>
        <span style={{
          fontSize: '0.9rem',
          fontWeight: 700,
          color: 'var(--accent-blue)',
          fontFamily: 'var(--font-mono)',
          letterSpacing: '0.04em',
        }}>
          Trading Dashboard
        </span>
        {lastUpdatedStr && (
          <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginLeft: 'auto' }}>
            갱신: {lastUpdatedStr}
          </span>
        )}
      </header>

      {/* 상태바 */}
      <StatusBar status={status} />

      {/* 폴링 오류 배너 */}
      {statusError && (
        <div style={{
          padding: '6px 20px',
          background: '#fef2f2',
          color: 'var(--accent-red)',
          fontSize: '0.75rem',
          borderBottom: '1px solid var(--border)',
        }}>
          상태 조회 오류 (마지막 데이터 유지): {statusError}
        </div>
      )}

      {/* 바디 — 사이드바 + 콘텐츠 */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* 좌측 사이드바 (REQ-054-B3) */}
        <nav
          aria-label="메인 네비게이션"
          style={sidebarStyle}
        >
          <div style={{ paddingTop: 8 }}>
            {NAV_ITEMS.map(item => (
              <button
                key={item.id}
                onClick={() => setActiveView(item.id)}
                style={navBtnStyle(activeView === item.id)}
                aria-current={activeView === item.id ? 'page' : undefined}
              >
                <span style={{ fontSize: '0.85rem', opacity: 0.7 }}>{item.icon}</span>
                {item.label}
              </button>
            ))}
          </div>
        </nav>

        {/* 메인 콘텐츠 — REQ-054-B3: ≥1280px 다열 그리드 */}
        <main
          style={{
            flex: 1,
            overflowY: 'auto',
            overflowX: 'hidden',
            padding: '20px',
            minWidth: 0,
          }}
        >
          {activeView === 'overview' && (
            <ErrorBoundary label="개요">
              <h2 style={{ fontSize: '0.95rem', fontWeight: 700, marginBottom: 16, color: 'var(--text-primary)' }}>
                성과 개요
              </h2>
              <KpiCards />
              {/* 개요에서는 ChartsView 도 함께 (REQ-054-C1 + 기존 자산통계) */}
              <ErrorBoundary label="자산 차트">
                <ChartsView />
              </ErrorBoundary>
            </ErrorBoundary>
          )}

          {activeView === 'portfolio' && (
            <ErrorBoundary label="포트폴리오">
              <h2 style={{ fontSize: '0.95rem', fontWeight: 700, marginBottom: 16, color: 'var(--text-primary)' }}>
                포트폴리오 구성
              </h2>
              <PortfolioView />
            </ErrorBoundary>
          )}

          {activeView === 'roundtrips' && (
            <ErrorBoundary label="거래원장">
              <h2 style={{ fontSize: '0.95rem', fontWeight: 700, marginBottom: 16, color: 'var(--text-primary)' }}>
                라운드트립 거래 원장
              </h2>
              <RoundtripLedger />
            </ErrorBoundary>
          )}

          {activeView === 'pnl' && (
            <ErrorBoundary label="손익 추이">
              <h2 style={{ fontSize: '0.95rem', fontWeight: 700, marginBottom: 16, color: 'var(--text-primary)' }}>
                기간별 손익 추이
              </h2>
              <PnlTrendView />
            </ErrorBoundary>
          )}

          {activeView === 'pipeline' && (
            <ErrorBoundary label="파이프라인">
              <PipelineView status={status} />
            </ErrorBoundary>
          )}

          {activeView === 'news' && (
            <ErrorBoundary label="뉴스 인텔리전스">
              <NewsView />
            </ErrorBoundary>
          )}

          {activeView === 'positions' && (
            <ErrorBoundary label="포지션 / 주문">
              <div style={{ display: 'grid', gap: 24 }}>
                <HoldingsTable />
                <OrdersTable />
              </div>
            </ErrorBoundary>
          )}
        </main>
      </div>
    </div>
  )
}
