// SPEC-TRADING-064 그룹 C — 결정 추적 흐름도 프런트 테스트.
// REQ-064-A5/그룹 C 계약 준수: 픽스처는 이 태스크 명세가 준 실제 응답 shape 에서 파생한다.
// cytoscape 는 jsdom 에 Canvas 가 없어 echarts-for-react 와 같은 패턴으로 모킹한다.
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { vi, describe, it, expect, afterEach } from 'vitest'
import {
  buildGraph, buildElements, moduleOf, isPredicate, uniqueModules,
  type CyElement, type CyNodeData,
} from '../trace/graph'
import {
  TRACE_RESPONSE_CONTRACT_KEYS, TRACE_NODE_CONTRACT_KEYS,
  TRACE_EVENT_CONTRACT_KEYS, TRACE_ORDER_CONTRACT_KEYS,
} from '../trace/contract'
import type { DecisionTrace, TraceNodeState } from '../api/types'

// ── cytoscape 모킹 (jsdom 에 Canvas 없음 — echarts-for-react 모킹과 동일 패턴) ─────
function makeFakeCy() {
  // 자기 자신을 반환하는 체이닝 더블이라 TS 가 순환 초기화로 타입을 못 잡는다(TS7022).
  // 명시 주석으로 순환을 끊는다.
  const core: Record<string, unknown> = {
    on: vi.fn(() => core),
    nodes: vi.fn(() => ({ forEach: () => {} })),
    elements: vi.fn(() => ({ remove: vi.fn(), length: 0 })),
    add: vi.fn(() => core),
    layout: vi.fn(() => ({
      one: (evt: string, cb: () => void) => { if (evt === 'layoutstop') cb() },
      run: () => {},
    })),
    fit: vi.fn(() => core),
    destroy: vi.fn(),
    animate: vi.fn(() => core),
    zoom: vi.fn(() => 1),
    width: vi.fn(() => 800),
    height: vi.fn(() => 600),
  }
  return core
}
vi.mock('cytoscape', () => {
  const factory = vi.fn(() => makeFakeCy())
  ;(factory as unknown as { use: (ext: unknown) => void }).use = vi.fn()
  return { default: factory }
})
vi.mock('cytoscape-elk', () => ({ default: {} }))

import TraceView from '../components/TraceView'

// ── graph.ts 순수 함수 ───────────────────────────────────────────────────────

describe('trace/graph — moduleOf/isPredicate', () => {
  it('src/trading/<module>/... 에서 모듈명을 뽑는다', () => {
    expect(moduleOf('src/trading/risk/limits.py')).toBe('risk')
    expect(moduleOf('src/trading/kis/broker_truth.py')).toBe('kis')
    expect(moduleOf('')).toBe('')
  })

  it('check_/requires_/is_/guard_/has_ 접두를 판정 블록으로 인식한다', () => {
    expect(isPredicate('check_pre_order')).toBe(true)
    expect(isPredicate('requires_circuit_halt')).toBe(true)
    expect(isPredicate('guard_sell')).toBe(true)
    expect(isPredicate('compute_qty')).toBe(false)
  })
})

describe('trace/graph — buildGraph', () => {
  it('여러 run 결과를 하나의 노드/엣지 집합으로 합치고 진입점을 역산한다', () => {
    const doc = {
      entries: ['run_pre_market_cycle'],
      depth: 3,
      runs: [{
        results: [
          { node_id: 2, parent_id: 1, name: 'check_pre_order', file_path: 'src/trading/risk/limits.py', type: 'function', depth: 1 },
          { node_id: 3, parent_id: 2, name: 'record_breach', file_path: 'src/trading/risk/limits.py', type: 'function', depth: 2 },
        ],
      }],
    }
    const graph = buildGraph(doc)
    expect(graph.nodes.size).toBe(3) // 진입점(1) + 함수 2개
    expect(graph.nodes.get(1)?.type).toBe('진입점')
    expect(graph.nodes.get(1)?.name).toBe('run_pre_market_cycle')
    expect(graph.edges).toContainEqual([1, 2])
    expect(graph.edges).toContainEqual([2, 3])
  })

  it('실제 커밋된 callgraph.json 산출물을 에러 없이 조립한다(ADR-003 산출물 정합)', () => {
    const graph = buildGraph()
    expect(graph.nodes.size).toBeGreaterThan(100)
    expect(graph.edges.length).toBeGreaterThan(100)
    const mods = uniqueModules(graph)
    expect(mods).toContain('risk')
    expect(mods).toContain('kis')
  })
})

// buildElements 는 노드/엣지 유니언(CyElement)을 돌려준다 — 테스트에서 노드 필드에
// 접근할 때 매번 narrowing 하지 않도록 캐스팅 헬퍼로 좁힌다.
function asNode(e: CyElement | undefined): CyNodeData {
  if (!e || !('kind' in e.data)) throw new Error('노드 엘리먼트가 아니다')
  return e.data
}

describe('trace/graph — buildElements (REQ-064-C2/C6a)', () => {
  const doc = {
    entries: ['run_pre_market_cycle'],
    depth: 3,
    runs: [{
      results: [
        { node_id: 2, parent_id: 1, name: 'check_pre_order', file_path: 'src/trading/risk/limits.py', type: 'function', depth: 1 },
        { node_id: 3, parent_id: 2, name: 'record_breach', file_path: 'src/trading/risk/limits.py', type: 'function', depth: 2 },
        { node_id: 4, parent_id: 1, name: 'compute_qty', file_path: 'src/trading/strategy/sizing.py', type: 'function', depth: 1 },
      ],
    }],
  }
  const graph = buildGraph(doc)

  function stateOf(states: Record<string, { state: TraceNodeState; eventCount: number }>) {
    return (file: string, fn: string) => states[`${file}::${fn}`] ?? { state: 'not_involved' as TraceNodeState, eventCount: 0 }
  }

  it('접힌 모듈은 함수를 카드 하나로 말고, recordedCount 를 집계한다', () => {
    const lookup = stateOf({
      'src/trading/risk/limits.py::record_breach': { state: 'recorded', eventCount: 1 },
    })
    const els = buildElements(graph, new Set(), lookup)
    const riskModule = asNode(els.find((e) => 'kind' in e.data && e.data.kind === 'module' && e.data.module === 'risk'))
    expect(riskModule.fns).toBe(2)
    expect(riskModule.recordedCount).toBe(1)
    // recorded 자식이 있으면 모듈 카드도 recorded 로 롤업된다(REQ-064-C2)
    expect(riskModule.state).toBe('recorded')
  })

  it('펼친 모듈은 compound 부모를 만들고 함수는 그 안에 그대로 남는다(REQ-064-C6a)', () => {
    const lookup = stateOf({})
    const els = buildElements(graph, new Set(['risk']), lookup)
    const group = els.find((e) => 'kind' in e.data && e.data.kind === 'group' && e.data.module === 'risk')
    expect(group).toBeDefined()
    const fn = asNode(els.find((e) => 'kind' in e.data && e.data.kind === 'function' && e.data.label.startsWith('record_breach')))
    expect(fn.parent).toBe('g:risk')
  })

  it('양끝이 모두 recorded 인 엣지만 pathHot 이다', () => {
    const lookup = stateOf({
      'src/trading/risk/limits.py::check_pre_order': { state: 'recorded', eventCount: 2 },
      'src/trading/risk/limits.py::record_breach': { state: 'recorded', eventCount: 1 },
    })
    const els = buildElements(graph, new Set(['risk']), lookup)
    const edge = els.find((e) => 'source' in e.data && e.data.source === '2' && e.data.target === '3')
    expect(edge).toBeDefined()
    expect(edge && !('kind' in edge.data) && edge.data.pathHot).toBe(true)
  })

  it('상태별 라벨 칩 — decision_agnostic/rule_based 는 상태 문구가 라벨에 붙는다', () => {
    const lookup = stateOf({
      'src/trading/risk/limits.py::check_pre_order': { state: 'decision_agnostic', eventCount: 1 },
      'src/trading/risk/limits.py::record_breach': { state: 'rule_based', eventCount: 1 },
    })
    const els = buildElements(graph, new Set(['risk']), lookup)
    const agnostic = asNode(els.find((e) => 'kind' in e.data && e.data.label.startsWith('check_pre_order')))
    const ruleBased = asNode(els.find((e) => 'kind' in e.data && e.data.label.startsWith('record_breach')))
    expect(agnostic.label).toContain('결정 단위 아님')
    expect(ruleBased.label).toContain('규칙 기반 실행')
  })
})

// ── 계약 자기점검 (REQ-064-C1) ────────────────────────────────────────────────

const TRACE_FIXTURE: DecisionTrace = {
  decision: {
    id: 501, ts: '2026-08-07T15:10:00+09:00', persona_name: 'decision', cycle_kind: 'intraday',
    ticker: '005930', ticker_name: '삼성전자', side: 'buy', qty: 10, confidence: 0.7,
    rationale: '반도체 업황 개선', risk_verdict: 'APPROVE', risk_rationale: '한도 내 허용',
    regime_at_decision: 'BULL', trigger_context: null, response_json: null,
  },
  nodes: [
    {
      file: 'src/trading/risk/limits.py', function: 'record_breach', module: 'risk', state: 'recorded',
      events: [{ event_type: 'LIMIT_BREACH', ts: '2026-08-07T15:10:00+09:00', actor: 'risk', details: {} }],
    },
  ],
  orders: [
    {
      id: 1, ts: '2026-08-07T15:10:05+09:00', side: 'buy', ticker: '005930', qty: 10,
      status: 'filled', rejected_reason: null, fill_price: 70000, fill_qty: 10,
      synthetic: false, correction: false, origin: 'decision',
    },
  ],
  unmatched_events: [],
}

describe('trace/contract — 응답 키 계약 자기점검(REQ-064-C1)', () => {
  it('픽스처 키 집합이 태스크가 준 실제 응답 shape 과 정확히 일치한다', () => {
    expect(Object.keys(TRACE_FIXTURE).sort()).toEqual(TRACE_RESPONSE_CONTRACT_KEYS)
    expect(Object.keys(TRACE_FIXTURE.nodes[0]).sort()).toEqual(TRACE_NODE_CONTRACT_KEYS)
    expect(Object.keys(TRACE_FIXTURE.nodes[0].events[0]).sort()).toEqual(TRACE_EVENT_CONTRACT_KEYS)
    expect(Object.keys(TRACE_FIXTURE.orders[0]).sort()).toEqual(TRACE_ORDER_CONTRACT_KEYS)
  })
})

// ── TraceView 통합 렌더 ───────────────────────────────────────────────────────

function mockFetchSeq(responses: Array<{ ok: boolean; data: unknown }>) {
  let i = 0
  globalThis.fetch = vi.fn().mockImplementation(() => {
    const r = responses[Math.min(i++, responses.length - 1)]
    return Promise.resolve({ ok: r.ok, status: r.ok ? 200 : 503, json: () => Promise.resolve(r.data) } as Response)
  })
}

const MOCK_DECISIONS = [TRACE_FIXTURE.decision]

describe('TraceView', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('결정 선택 → 추적 조회 → 범례·연결된 주문이 표시된다', async () => {
    mockFetchSeq([{ ok: true, data: MOCK_DECISIONS }, { ok: true, data: TRACE_FIXTURE }])
    render(<TraceView />)

    await waitFor(() => expect(screen.getByText(/① 관여함/)).toBeDefined())

    const rows = screen.getAllByRole('button')
    fireEvent.click(rows[0])

    await waitFor(() => expect(screen.getByText(/연결된 주문 \(1\)/)).toBeDefined())
    // 결정 요약 패널
    expect(screen.getByText('반도체 업황 개선')).toBeDefined()
  })

  it('REQ-064-C10: 규칙 기반 실행 주문은 "기록 없음"과 다르게 배지로 구분된다', async () => {
    const ruleBasedTrace: DecisionTrace = {
      ...TRACE_FIXTURE,
      orders: [
        ...TRACE_FIXTURE.orders,
        {
          id: 2, ts: '2026-08-07T15:20:00+09:00', side: 'sell', ticker: '005930', qty: 5,
          status: 'filled', rejected_reason: null, fill_price: 71000, fill_qty: 5,
          synthetic: false, correction: false, origin: 'rule_based',
        },
      ],
    }
    mockFetchSeq([{ ok: true, data: MOCK_DECISIONS }, { ok: true, data: ruleBasedTrace }])
    render(<TraceView />)

    await waitFor(() => expect(screen.getAllByRole('button').length).toBeGreaterThan(0))
    fireEvent.click(screen.getAllByRole('button')[0])

    await waitFor(() => {
      expect(screen.getByText('규칙 기반 실행 (LLM 결정 없음)')).toBeDefined()
      expect(screen.getByText('결정 연계')).toBeDefined()
    })
  })

  it('REQ-064-C9: 거부 아닌 주문의 거부사유는 "—", 거부인데 사유가 없으면 "미기록"', async () => {
    const trace: DecisionTrace = {
      ...TRACE_FIXTURE,
      orders: [
        { id: 1, ts: '2026-08-07T15:10:05+09:00', side: 'buy', ticker: '005930', qty: 10, status: 'filled', rejected_reason: null, fill_price: 70000, fill_qty: 10, synthetic: false, correction: false, origin: 'decision' },
        { id: 2, ts: '2026-08-07T15:11:00+09:00', side: 'buy', ticker: '005930', qty: 10, status: 'rejected', rejected_reason: null, fill_price: null, fill_qty: null, synthetic: false, correction: false, origin: 'decision' },
      ],
    }
    mockFetchSeq([{ ok: true, data: MOCK_DECISIONS }, { ok: true, data: trace }])
    render(<TraceView />)

    await waitFor(() => expect(screen.getAllByRole('button').length).toBeGreaterThan(0))
    fireEvent.click(screen.getAllByRole('button')[0])
    await waitFor(() => expect(screen.getByText(/연결된 주문 \(2\)/)).toBeDefined())

    // 두 번째(거부) 주문 클릭 → 상세 패널에 "미기록"이 뜬다(추정 금지, REQ-064-C9)
    const orderRows = screen.getAllByText('rejected')
    fireEvent.click(orderRows[0].closest('tr')!)
    await waitFor(() => expect(screen.getByText('거부 사유')).toBeDefined())
    expect(screen.getAllByText('미기록').length).toBeGreaterThan(0)
  })
})
