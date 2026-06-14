// M5: 뉴스 인텔리전스 뷰 (REQ-050-22/23/24/25)
import { useState, useCallback } from 'react'
import { usePolling } from '../hooks/usePolling'
import { api } from '../api/client'
import { theme } from '../theme'
import type { StoryCluster } from '../api/types'
import KeywordTrends from './charts/KeywordTrends'

const s = {
  grid: { display: 'grid', gap: 20 },
  sectionTitle: { fontSize: '0.7rem', color: '#8b949e', textTransform: 'uppercase' as const, letterSpacing: '0.08em', marginBottom: 10, borderBottom: '1px solid #21262d', paddingBottom: 6 },
  filterRow: { display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' as const },
  filterBtn: (active: boolean) => ({
    padding: '4px 12px',
    borderRadius: 6,
    border: `1px solid ${active ? theme.accentBlue : theme.border}`,
    background: active ? theme.accentBlue + '22' : 'transparent',
    color: active ? theme.accentBlue : theme.textSecondary,
    fontSize: '0.72rem',
    cursor: 'pointer',
    fontFamily: theme.fontSans,
  }),
  clusterCard: (relevant: boolean) => ({
    background: relevant ? '#1a2040' : theme.bgPanel,
    border: `1px solid ${relevant ? theme.accentBlue + '55' : theme.borderLight}`,
    borderRadius: 8,
    padding: '12px 14px',
    marginBottom: 8,
  }),
  clusterTitle: { fontSize: '0.82rem', color: theme.textPrimary, fontWeight: 600 as const, marginBottom: 6 },
  clusterMeta: { display: 'flex', gap: 8, flexWrap: 'wrap' as const, marginBottom: 6, fontSize: '0.7rem' },
  sentimentBadge: (s: string | null) => ({
    display: 'inline-block',
    padding: '1px 7px',
    borderRadius: 10,
    fontSize: '0.68rem',
    background: s === 'positive' ? '#1f4f2e' : s === 'negative' ? '#3d1f1f' : '#21262d',
    color: s === 'positive' ? '#3fb950' : s === 'negative' ? '#f85149' : '#8b949e',
  }),
  tickerBadge: (linked: boolean) => ({
    display: 'inline-block',
    padding: '1px 7px',
    borderRadius: 10,
    fontSize: '0.68rem',
    background: linked ? theme.accentBlue + '33' : '#21262d',
    color: linked ? theme.accentBlue : '#8b949e',
    border: linked ? `1px solid ${theme.accentBlue}55` : 'none',
    fontFamily: theme.fontMono,
    fontWeight: linked ? 600 as const : 400 as const,
  }),
  newsCard: {
    background: theme.bgPanel,
    border: `1px solid ${theme.borderLight}`,
    borderRadius: 6,
    padding: '10px 12px',
    marginBottom: 6,
  },
  newsTitle: { fontSize: '0.8rem', color: theme.textPrimary, marginBottom: 4 },
  newsMeta: { fontSize: '0.68rem', color: theme.textSecondary, display: 'flex', gap: 8, flexWrap: 'wrap' as const },
  impactDot: (score: number | null) => ({
    display: 'inline-block',
    width: 7,
    height: 7,
    borderRadius: '50%',
    background: score == null ? '#6e7681' : score >= 4 ? '#f85149' : score >= 3 ? '#e3b341' : '#3fb950',
    verticalAlign: 'middle',
    marginRight: 3,
  }),
  empty: { color: '#6e7681', fontSize: '0.8rem', padding: '12px 0' },
  error: { color: '#f85149', fontSize: '0.75rem', padding: '6px 0' },
}

export default function NewsView() {
  const [portfolioOnly, setPortfolioOnly] = useState(false)

  const clustersFetcher = useCallback(() => api.fetchStoryClusters(7, 50), [])
  const newsFetcher = useCallback(() => api.fetchNews(7, 50), [])
  const holdingsFetcher = useCallback(() => api.fetchHoldings(), [])
  const trendsFetcher = useCallback(() => api.fetchTrends('daily', 14), [])

  const { data: clusters, error: clustersError } = usePolling(clustersFetcher, 30_000)
  const { data: news, error: newsError } = usePolling(newsFetcher, 30_000)
  const { data: holdings } = usePolling(holdingsFetcher, 30_000)
  const { data: trends, error: trendsError } = usePolling(trendsFetcher, 60_000)

  // 현재 보유 종목 코드 집합 (REQ-050-24: 관련 종목 연결 표시)
  const holdingTickers = new Set((holdings ?? []).map((h) => h.ticker))

  // REQ-050-22: portfolio_relevant=true 우선 정렬
  const sortedClusters = clusters
    ? [...clusters].sort((a, b) => (b.portfolio_relevant ? 1 : 0) - (a.portfolio_relevant ? 1 : 0))
    : []

  // REQ-050-25: "포트폴리오 관련만" 필터
  const filteredClusters: StoryCluster[] = portfolioOnly
    ? sortedClusters.filter((c) => c.portfolio_relevant)
    : sortedClusters

  const isLinked = (cluster: StoryCluster): boolean => {
    if (!cluster.relevance_tickers) return false
    return cluster.relevance_tickers.some((t) => holdingTickers.has(t))
  }

  return (
    <div style={s.grid}>
      {/* 스토리 클러스터 */}
      <section>
        <div style={s.sectionTitle}>스토리 클러스터</div>

        {/* REQ-050-25: 필터 토글 */}
        <div style={s.filterRow}>
          <button
            style={s.filterBtn(!portfolioOnly)}
            onClick={() => setPortfolioOnly(false)}
            aria-pressed={!portfolioOnly}
          >
            전체
          </button>
          <button
            style={s.filterBtn(portfolioOnly)}
            onClick={() => setPortfolioOnly(true)}
            aria-pressed={portfolioOnly}
          >
            포트폴리오 관련만
          </button>
        </div>

        {clustersError && <div style={s.error}>오류 (마지막 데이터 유지): {clustersError}</div>}
        {filteredClusters.length === 0 && !clustersError && <div style={s.empty}>클러스터 없음</div>}

        {filteredClusters.map((c) => {
          const linked = isLinked(c)
          return (
            <div key={c.id} style={s.clusterCard(c.portfolio_relevant)}>
              {/* REQ-050-22: 대표 제목 */}
              <div style={s.clusterTitle}>{c.representative_title}</div>
              <div style={s.clusterMeta}>
                {/* 감성 */}
                <span style={s.sentimentBadge(c.sentiment_dominant)}>
                  {c.sentiment_dominant ?? '—'}
                </span>
                {/* 섹터 */}
                {c.sector && (
                  <span style={{ color: theme.textSecondary, fontSize: '0.68rem' }}>{c.sector}</span>
                )}
                {/* 포트폴리오 관련 */}
                {c.portfolio_relevant && (
                  <span style={{ color: theme.accentBlue, fontSize: '0.68rem', fontWeight: 600 }}>
                    포트폴리오 관련
                  </span>
                )}
              </div>
              {/* REQ-050-22/24: relevance_tickers — 보유 종목과 겹치면 배지 강조 */}
              {c.relevance_tickers && c.relevance_tickers.length > 0 && (
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                  {c.relevance_tickers.map((t) => (
                    <span key={t} style={s.tickerBadge(holdingTickers.has(t))}>
                      {t}
                      {holdingTickers.has(t) && ' *'}
                    </span>
                  ))}
                  {/* REQ-050-24: 의사결정 연결 표시 */}
                  {linked && (
                    <span style={{ fontSize: '0.65rem', color: theme.accentBlue, alignSelf: 'center' }}>
                      ← 의사결정 연결
                    </span>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </section>

      {/* 키워드 트렌드 (REQ-050-23) */}
      <section>
        <div style={s.sectionTitle}>키워드 트렌드</div>
        {trendsError && <div style={s.error}>오류 (마지막 데이터 유지): {trendsError}</div>}
        {trends != null ? (
          <KeywordTrends data={trends} />
        ) : !trendsError && <div style={s.empty}>로딩 중...</div>}
      </section>

      {/* 개별 뉴스 (REQ-050-23) */}
      <section>
        <div style={s.sectionTitle}>최근 뉴스</div>
        {newsError && <div style={s.error}>오류: {newsError}</div>}
        {(!news || news.length === 0) && !newsError && <div style={s.empty}>뉴스 없음</div>}
        {(news ?? []).map((n) => (
          <div key={n.id} style={s.newsCard}>
            <div style={s.newsTitle}>{n.title}</div>
            {n.summary_2line && (
              <div style={{ fontSize: '0.72rem', color: theme.textSecondary, marginBottom: 4 }}>
                {n.summary_2line}
              </div>
            )}
            <div style={s.newsMeta}>
              {/* 임팩트 점 + 점수 */}
              <span>
                <span style={s.impactDot(n.impact_score)} />
                임팩트 {n.impact_score ?? '—'}
              </span>
              {/* 감성 */}
              {n.sentiment && (
                <span style={s.sentimentBadge(n.sentiment)}>{n.sentiment}</span>
              )}
              {/* 출처 */}
              {n.source_name && <span>{n.source_name}</span>}
              {/* 섹터 */}
              {n.sector && <span>{n.sector}</span>}
            </div>
          </div>
        ))}
      </section>
    </div>
  )
}
