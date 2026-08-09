// SPEC-TRADING-064 REQ-064-C1: GET /api/decisions/{id}/trace 실제 응답 키 계약.
// 그룹 A 의 DECISION_CONTRACT_KEYS 패턴과 동일 — 타입이 아니라 이 목록을 픽스처와
// 대조해서 백엔드가 안 주는 키가 픽스처에 섞여 들어오면 테스트가 실패하게 만든다.
export const TRACE_RESPONSE_CONTRACT_KEYS = ['decision', 'nodes', 'orders', 'unmatched_events'].sort()

export const TRACE_NODE_CONTRACT_KEYS = ['file', 'function', 'module', 'state', 'events'].sort()

export const TRACE_EVENT_CONTRACT_KEYS = ['event_type', 'ts', 'actor', 'details'].sort()

export const TRACE_ORDER_CONTRACT_KEYS = [
  'id', 'ts', 'side', 'ticker', 'qty', 'status', 'rejected_reason',
  'fill_price', 'fill_qty', 'synthetic', 'correction', 'origin',
].sort()
