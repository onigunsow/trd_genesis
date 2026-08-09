// SPEC-TRADING-064 REQ-064-C3/C4/C6/C6a — `src/trading/scripts/codemap.py` 의 그래프 조립
// 로직(build_graph/buildElements)을 프런트로 이식한다. 신규 그래프 엔진을 만들지 않는다.
//
// 구조 데이터는 ADR-003 (a) 에 따라 커밋된 산출물을 빌드 타임에 임포트한다.
// 훗날 엔드포인트로 옮기더라도 이 파일의 `loadStructure()` 한 곳만 바꾸면 된다.
import structureDoc from '../../../data/callgraph.json'
import type { TraceNodeState } from '../api/types'

// ── 구조 산출물 파싱 (ADR-003) ───────────────────────────────────────────────

interface RawResult {
  node_id: number
  parent_id: number
  name: string
  file_path: string
  type: string
  depth: number
}

interface RawRun {
  results: RawResult[]
}

interface StructureDoc {
  entries: string[]
  depth: number
  runs: RawRun[]
}

export interface StructureNode {
  id: number
  name: string
  file: string
  type: string
  depth: number
}

export interface GraphModel {
  nodes: Map<number, StructureNode>
  edges: Array<[number, number]>
}

/** 커밋된 콜그래프 산출물을 읽는다. 인자를 주면 테스트에서 임의 문서로 대체할 수 있다. */
export function loadStructure(doc: StructureDoc = structureDoc as StructureDoc): StructureDoc {
  return doc
}

// codemap.py::build_graph() 포팅 — 여러 callgraph 실행 결과를 노드/엣지 하나로 합친다.
export function buildGraph(doc: StructureDoc = structureDoc as StructureDoc): GraphModel {
  const nodes = new Map<number, StructureNode>()
  const edgeSet = new Set<string>()

  doc.runs.forEach((run, idx) => {
    const rows = run.results ?? []
    for (const r of rows) {
      nodes.set(r.node_id, { id: r.node_id, name: r.name, file: r.file_path, type: r.type, depth: r.depth })
      edgeSet.add(`${r.parent_id}|${r.node_id}`)
    }
    // 진입점 노드는 results 에 없다 — depth 1 행의 parent_id 로 역산한다.
    const label = doc.entries[idx]
    const rootIds = new Set(rows.filter((r) => r.depth === 1).map((r) => r.parent_id))
    for (const rid of rootIds) {
      if (!nodes.has(rid)) {
        nodes.set(rid, { id: rid, name: label ?? `entry:${rid}`, file: '', type: '진입점', depth: 0 })
      }
    }
  })

  const edges: Array<[number, number]> = []
  for (const key of edgeSet) {
    const [a, b] = key.split('|').map(Number)
    if (nodes.has(a) && nodes.has(b) && a !== b) edges.push([a, b])
  }
  return { nodes, edges }
}

// ── 모듈/판정 분류 ────────────────────────────────────────────────────────

export function moduleOf(file: string): string {
  if (!file) return ''
  const stripped = file.replace(/^src\/trading\//, '').replace(/\.py$/, '')
  return stripped.split('/')[0] || '?'
}

// codemap.py 의 판정 접두 규칙과 동일.
export function isPredicate(name: string): boolean {
  return /^(check_|requires_|is_|guard_|has_|_split_)/.test(name)
}

export function uniqueModules(graph: GraphModel): string[] {
  const set = new Set<string>()
  for (const n of graph.nodes.values()) {
    if (n.type === '진입점') continue
    const m = moduleOf(n.file)
    if (m && m !== '?') set.add(m)
  }
  return [...set].sort()
}

const PALETTE = [
  '#2f6df6', '#e8453c', '#2aa84a', '#f5a623', '#8b53d4', '#00a9c4',
  '#ff7043', '#12a594', '#5c6bc0', '#e0518f', '#66a83a', '#b07a2e',
]

export function colorFor(module: string, mods: string[]): string {
  const idx = mods.indexOf(module)
  return PALETTE[Math.max(0, idx) % PALETTE.length]
}

// 모듈별 픽토그램(codemap.py MODULE_GLYPH 포팅) — 노코드 캔버스 카드 안에 들어간다.
export const MODULE_GLYPH: Record<string, string> = {
  personas: '🧠', risk: '🛡', strategy: '📐', edge: '📈', kis: '🏦',
  alerts: '🔔', data: '📊', db: '🗄', screener: '🔍', models: '🤖',
  tools: '🔧', config: '⚙', scripts: '▶', news: '📰', watchers: '👁',
  jit: '⚡', tests: '🧪', __entry__: '🚀',
}

export const MODULE_ROLE: Record<string, string> = {
  personas: '사이클 전체 지휘 + LLM 판단 생성. 후보 선별·게이트 호출·실행·보고를 엮는 유일한 지점',
  risk: '한도·안전성 검사와 매매 정지. 통과 못하면 주문 없음',
  strategy: '수량 결정 — 변동성 목표 기본 수량에 half-Kelly 상한과 포트폴리오 히트를 적용',
  edge: '엣지 검증과 실현손익 산출. 일일손실 한도의 입력이자 실거래 사이징의 관문',
  kis: '증권사 연동. 보유수량을 단일 진실로 삼아 매도 수량을 클램프하고 미체결을 정리',
  alerts: '텔레그램 통보',
  data: '시세·종목 데이터 수집. KRX 장애 시 여기서 차단되어 판단 자체가 굶는다',
  db: '상태 저장과 audit 기록',
  screener: '유니버스 확장',
  models: '어떤 모델로 판단할지 라우팅',
  tools: '페르소나가 쓸 도구 결정',
  config: '설정과 수수료 추정',
}

function glyphSvg(ch: string): string {
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" width="52" height="52">' +
    `<text x="26" y="36" font-size="27" text-anchor="middle">${ch}</text></svg>`
  return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg)
}

// 외부 아이콘 의존 없이 인라인 SVG data URI 로 픽토그램을 만든다(codemap.py::glyph_uris 포팅).
export const GLYPH_URI: Record<string, string> = Object.fromEntries(
  Object.entries(MODULE_GLYPH).map(([mod, ch]) => [mod, glyphSvg(ch)]),
)

// ── 결정 상태 병합(cytoscape 엘리먼트 조립) ─────────────────────────────────

export type NodeKind = 'entry' | 'predicate' | 'function' | 'module' | 'group'

export type TraceStateLookup = (file: string, fn: string) => { state: TraceNodeState; eventCount: number }

export interface CyNodeData {
  id: string
  label: string
  file: string
  module: string
  kind: NodeKind
  state: TraceNodeState
  parent?: string
  fns?: number
  recordedCount?: number
}

export interface CyEdgeData {
  id: string
  source: string
  target: string
  // REQ-064-C2/인수기준: 이 결정의 경로(양끝 모두 recorded)만 강조하기 위한 플래그.
  pathHot: boolean
}

export type CyElement = { data: CyNodeData } | { data: CyEdgeData }

interface LeafInfo {
  structId: number
  name: string
  file: string
  module: string
  kind: Exclude<NodeKind, 'module' | 'group'>
  state: TraceNodeState
  eventCount: number
}

function classify(n: StructureNode, stateLookup: TraceStateLookup): LeafInfo {
  const isEntry = n.type === '진입점'
  const kind: LeafInfo['kind'] = isEntry ? 'entry' : isPredicate(n.name) ? 'predicate' : 'function'
  const module = moduleOf(n.file)
  const { state, eventCount } = isEntry
    ? { state: 'not_involved' as TraceNodeState, eventCount: 0 }
    : stateLookup(n.file, n.name)
  return { structId: n.id, name: n.name, file: n.file, module, kind, state, eventCount }
}

function rollupState(states: TraceNodeState[]): TraceNodeState {
  if (states.includes('recorded')) return 'recorded'
  if (states.includes('rule_based')) return 'rule_based'
  if (states.includes('decision_agnostic')) return 'decision_agnostic'
  return 'not_involved'
}

function labelFor(name: string, state: TraceNodeState, eventCount: number): string {
  // REQ-064-C2: 빈칸이 "통과"로 읽혀선 안 된다 — 상태별 칩을 라벨에 바로 붙인다.
  if (state === 'decision_agnostic') return `${name}\n결정 단위 아님`
  if (state === 'rule_based') return `${name}\n규칙 기반 실행`
  if (state === 'recorded' && eventCount) return `${name}\n${eventCount}건`
  return name
}

/**
 * codemap.py 의 클라이언트 buildElements() 포팅. 펼쳐진 모듈만 compound 부모 상자로
 * 남고(ADR-002/REQ-064-C6a), 나머지는 모듈 카드 하나로 접힌다.
 */
export function buildElements(
  graph: GraphModel,
  expanded: ReadonlySet<string>,
  stateLookup: TraceStateLookup,
): CyElement[] {
  const leaves = new Map<number, LeafInfo>()
  for (const n of graph.nodes.values()) {
    leaves.set(n.id, classify(n, stateLookup))
  }

  const anchorOf = (id: number): string => {
    const leaf = leaves.get(id)
    if (!leaf) return String(id)
    if (leaf.kind === 'entry') return String(id)
    return expanded.has(leaf.module) ? String(id) : `m:${leaf.module}`
  }

  const nodesOut = new Map<string, CyNodeData>()

  for (const mod of expanded) {
    const own = [...leaves.values()].filter((l) => l.module === mod)
    if (!own.length) continue
    nodesOut.set(`g:${mod}`, {
      id: `g:${mod}`, label: mod, file: '', module: mod, kind: 'group',
      state: rollupState(own.map((l) => l.state)),
      fns: own.length,
      recordedCount: own.filter((l) => l.state === 'recorded').length,
    })
  }

  for (const leaf of leaves.values()) {
    const id = anchorOf(leaf.structId)
    if (id === String(leaf.structId)) {
      const parent = leaf.kind !== 'entry' && expanded.has(leaf.module) ? `g:${leaf.module}` : undefined
      nodesOut.set(id, {
        id, label: labelFor(leaf.name, leaf.state, leaf.eventCount), file: leaf.file,
        module: leaf.module, kind: leaf.kind, state: leaf.state, parent,
      })
      continue
    }
    let mod = nodesOut.get(id)
    if (!mod) {
      mod = { id, label: leaf.module, file: '', module: leaf.module, kind: 'module', state: 'not_involved', fns: 0, recordedCount: 0 }
      nodesOut.set(id, mod)
    }
    mod.fns = (mod.fns ?? 0) + 1
    if (leaf.state === 'recorded') mod.recordedCount = (mod.recordedCount ?? 0) + 1
    mod.state = rollupState([mod.state, leaf.state])
  }

  const edgeCount = new Map<string, number>()
  for (const [a, b] of graph.edges) {
    const sa = anchorOf(a)
    const sb = anchorOf(b)
    if (sa === sb) continue
    const key = `${sa}|${sb}`
    edgeCount.set(key, (edgeCount.get(key) ?? 0) + 1)
  }

  const edgesOut: CyElement[] = [...edgeCount.keys()].map((key) => {
    const sepIdx = key.indexOf('|')
    const source = key.slice(0, sepIdx)
    const target = key.slice(sepIdx + 1)
    const pathHot = nodesOut.get(source)?.state === 'recorded' && nodesOut.get(target)?.state === 'recorded'
    return { data: { id: `e:${key}`, source, target, pathHot } }
  })

  return [...[...nodesOut.values()].map((d) => ({ data: d })), ...edgesOut]
}

// ELK layered — INCLUDE_CHILDREN 이 부모 상자 안팎을 한 번에 계층 배치한다(ADR-002).
// dagre 는 compound 를 지원하지 않아 펼친 모듈의 함수가 다음 단으로 흩어졌다(2026-08-09 실측).
export const ELK_LAYOUT = {
  name: 'elk',
  elk: {
    algorithm: 'layered',
    'elk.direction': 'RIGHT',
    'elk.hierarchyHandling': 'INCLUDE_CHILDREN',
    'elk.layered.spacing.nodeNodeBetweenLayers': 95,
    'elk.spacing.nodeNode': 34,
    'elk.padding': '[top=42,left=20,bottom=20,right=20]',
    'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES',
  },
}
