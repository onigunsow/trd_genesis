// REQ-050-13: 모든 엔드포인트에 대한 타입 지정 fetcher
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

  // ── 자산 곡선 + drawdown (REQ-050-5) ─────────────────────────────────────
  fetchEquity: (days = 90) => get<EquityPoint[]>(`/api/equity?days=${days}`),

  // ── 스코어카드 ────────────────────────────────────────────────────────────
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
}
