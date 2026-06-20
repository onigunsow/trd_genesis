// REQ-054-F1: 모든 엔드포인트에 대한 타입 지정 fetcher
// CRITICAL: NUMERIC→number 변환은 db.py 로더 보장 — 문자열 파싱 불필요
import type {
  SystemStatus,
  Decision,
  Order,
  Holding,
  EquityPoint,
  Scorecard,
  NewsArticle,
  StoryCluster,
  TrendPoint,
  PostmortemResult,
  ConfidenceAnalysis,
  PipelineData,
  RoundTrip,
  PortfolioData,
  PnlDailyResponse,
} from './types'

// same-origin 요청 — FastAPI 가 /static/ 과 /api/ 를 모두 서빙
const BASE = ''

async function get<T>(path: string): Promise<T> {
  const res = await fetch(BASE + path)
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${path}`)
  }
  return res.json() as Promise<T>
}

// CSV 다운로드 — 브라우저 anchor 클릭으로 처리 (REQ-054-A5, ADR-003)
function downloadCsv(path: string, filename: string): void {
  const a = document.createElement('a')
  a.href = BASE + path
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

export const api = {
  // ── 상태 ──────────────────────────────────────────────────────────────────
  fetchStatus: () => get<SystemStatus>('/api/status'),

  // ── 의사결정 ──────────────────────────────────────────────────────────────
  fetchDecisions: (limit = 50) =>
    get<Decision[]>(`/api/decisions?limit=${limit}`),

  // ── 주문 ──────────────────────────────────────────────────────────────────
  fetchOrders: (limit = 50) => get<Order[]>(`/api/orders?limit=${limit}`),

  // ── 보유종목 ──────────────────────────────────────────────────────────────
  fetchHoldings: () => get<Holding[]>('/api/holdings'),

  // ── 자산 곡선 + drawdown ───────────────────────────────────────────────────
  fetchEquity: (days = 90) => get<EquityPoint[]>(`/api/equity?days=${days}`),

  // ── 스코어카드 (REQ-054-A4: sortino 포함) ────────────────────────────────
  fetchScorecard: () => get<Scorecard>('/api/scorecard'),

  // ── 뉴스 ──────────────────────────────────────────────────────────────────
  fetchNews: (days = 7, limit = 50) =>
    get<NewsArticle[]>(`/api/news?days=${days}&limit=${limit}`),

  // ── 스토리 클러스터 ───────────────────────────────────────────────────────
  fetchStoryClusters: (days = 7, limit = 50) =>
    get<StoryCluster[]>(`/api/story-clusters?days=${days}&limit=${limit}`),

  // ── 키워드 트렌드 ─────────────────────────────────────────────────────────
  fetchTrends: (trendType = 'daily', days = 14) =>
    get<TrendPoint[]>(`/api/trends?trend_type=${trendType}&days=${days}`),

  // ── Postmortem 분포 ───────────────────────────────────────────────────────
  fetchPostmortem: (days = 30) =>
    get<PostmortemResult>(`/api/postmortem?days=${days}`),

  // ── Confidence 분석 ───────────────────────────────────────────────────────
  fetchConfidenceAnalysis: (days = 30) =>
    get<ConfidenceAnalysis>(`/api/confidence-analysis?days=${days}`),

  // ── 파이프라인 ────────────────────────────────────────────────────────────
  fetchPipeline: () => get<PipelineData>('/api/pipeline'),

  // ── 라운드트립 원장 (REQ-054-A1, D1) ─────────────────────────────────────
  // days: 기간 필터, limit: 최대 행 수
  fetchRoundtrips: (days = 90, limit = 500) =>
    get<RoundTrip[]>(`/api/roundtrips?days=${days}&limit=${limit}`),

  // ── 포트폴리오 구성 (REQ-054-A2, C2) ─────────────────────────────────────
  // position_eval_snapshot + equity + ticker_metadata 조인 결과
  fetchPortfolio: () => get<PortfolioData>('/api/portfolio'),

  // ── 기간별 손익 추이 (REQ-054-A3, C4) ────────────────────────────────────
  // period: daily|weekly|monthly, start_date/end_date: YYYY-MM-DD
  fetchPnlDaily: (
    days = 90,
    period: 'daily' | 'weekly' | 'monthly' = 'daily',
    startDate?: string,
    endDate?: string,
  ) => {
    const params = new URLSearchParams({ days: String(days), period })
    if (startDate) params.set('start_date', startDate)
    if (endDate) params.set('end_date', endDate)
    return get<PnlDailyResponse>(`/api/pnl-daily?${params}`)
  },

  // ── CSV 내보내기 (REQ-054-A5, E2, ADR-003) ────────────────────────────────
  // 백엔드가 edge 코어/스냅샷 동일 데이터를 text/csv 로 반환
  exportRoundtrips: () => downloadCsv('/api/export/roundtrips.csv', 'roundtrips.csv'),
  exportPortfolio: () => downloadCsv('/api/export/portfolio.csv', 'portfolio.csv'),
  exportPnlDaily: () => downloadCsv('/api/export/pnl-daily.csv', 'pnl-daily.csv'),
}
