// 메인 앱 — 탭 기반 레이아웃
// M2: 기반 구조 + 상태바, M3: 파이프라인/결정, M4: 차트, M5: 뉴스
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

type Tab = 'pipeline' | 'charts' | 'news' | 'positions'

const TAB_LABELS: Record<Tab, string> = {
  pipeline: '파이프라인',
  charts: '자산 통계',
  news: '뉴스 인텔리전스',
  positions: '포지션 / 주문',
}

const styles = {
  app: {
    display: 'flex',
    flexDirection: 'column' as const,
    minHeight: '100vh',
    background: '#0d1117',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '10px 20px',
    background: '#161b22',
    borderBottom: '1px solid #30363d',
    flexWrap: 'wrap' as const,
    gap: 8,
  },
  title: {
    fontSize: '0.95rem',
    fontWeight: 600,
    color: '#58a6ff',
    fontFamily: 'var(--font-mono)',
    letterSpacing: '0.04em',
  },
  lastUpdated: {
    fontSize: '0.7rem',
    color: '#6e7681',
    fontFamily: 'var(--font-mono)',
  },
  tabs: {
    display: 'flex',
    background: '#161b22',
    borderBottom: '1px solid #30363d',
    padding: '0 12px',
    gap: 4,
    overflowX: 'auto' as const,
  },
  tab: {
    padding: '8px 16px',
    fontSize: '0.8rem',
    cursor: 'pointer',
    border: 'none',
    background: 'transparent',
    color: '#8b949e',
    borderBottom: '2px solid transparent',
    fontFamily: 'var(--font-sans)',
    whiteSpace: 'nowrap' as const,
    transition: 'color 0.15s',
  },
  tabActive: {
    color: '#58a6ff',
    borderBottom: '2px solid #58a6ff',
  },
  content: {
    flex: 1,
    padding: '16px 20px',
    overflowY: 'auto' as const,
  },
  errorBanner: {
    padding: '6px 20px',
    background: '#3d1f1f',
    color: '#f85149',
    fontSize: '0.75rem',
    borderBottom: '1px solid #30363d',
  },
}

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('pipeline')

  // 상태 폴링 — 전역 (10초)
  const statusFetcher = useCallback(() => api.fetchStatus(), [])
  const { data: status, error: statusError, lastUpdatedAt } = usePolling(statusFetcher, 10_000)

  const lastUpdatedStr = lastUpdatedAt
    ? lastUpdatedAt.toLocaleTimeString('ko-KR', { hour12: false, timeZone: 'Asia/Seoul' })
    : null

  return (
    <div style={styles.app}>
      {/* 헤더 */}
      <header style={styles.header}>
        <span style={styles.title}>Trading Dashboard</span>
        {lastUpdatedStr && (
          <span style={styles.lastUpdated}>갱신: {lastUpdatedStr}</span>
        )}
      </header>

      {/* 상태바 — REQ-050-15: halt/regime/risk/cool_down/late_cycle */}
      <StatusBar status={status} />

      {/* 폴링 오류 — 비차단 (REQ-050-12) */}
      {statusError && (
        <div style={styles.errorBanner}>
          상태 조회 오류 (마지막 데이터 유지): {statusError}
        </div>
      )}

      {/* 탭 */}
      <nav style={styles.tabs} role="tablist" aria-label="대시보드 탭">
        {(Object.keys(TAB_LABELS) as Tab[]).map((tab) => (
          <button
            key={tab}
            role="tab"
            aria-selected={activeTab === tab}
            style={{
              ...styles.tab,
              ...(activeTab === tab ? styles.tabActive : {}),
            }}
            onClick={() => setActiveTab(tab)}
          >
            {TAB_LABELS[tab]}
          </button>
        ))}
      </nav>

      {/* 콘텐츠 */}
      <main style={styles.content} role="tabpanel">
        {activeTab === 'pipeline' && (
          <ErrorBoundary label="파이프라인"><PipelineView status={status} /></ErrorBoundary>
        )}
        {activeTab === 'charts' && (
          <ErrorBoundary label="자산 통계"><ChartsView /></ErrorBoundary>
        )}
        {activeTab === 'news' && (
          <ErrorBoundary label="뉴스 인텔리전스"><NewsView /></ErrorBoundary>
        )}
        {activeTab === 'positions' && (
          <ErrorBoundary label="포지션 / 주문">
            <div style={{ display: 'grid', gap: 24 }}>
              <HoldingsTable />
              <OrdersTable />
            </div>
          </ErrorBoundary>
        )}
      </main>
    </div>
  )
}
