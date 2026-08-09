// SPEC-TRADING-064 REQ-064-C6/C6a/C7 — cytoscape + ELK 로 결정 추적 흐름도를 렌더한다.
// codemap.py 의 검증된 n8n 스타일 캔버스/상호작용을 그대로 이식한다(ADR-002). dagre 는
// compound(부모 상자)를 지원하지 않아 모듈을 펼치면 함수가 다음 단으로 흩어졌었다.
import { useEffect, useMemo, useRef, useState } from 'react'
import cytoscape from 'cytoscape'
import elk from 'cytoscape-elk'
import {
  buildElements, colorFor, uniqueModules, GLYPH_URI, ELK_LAYOUT,
  type GraphModel, type TraceStateLookup, type NodeKind,
} from '../trace/graph'
import type { TraceNodeState } from '../api/types'
import { theme } from '../theme'

cytoscape.use(elk)

export interface SelectedNode {
  id: string
  kind: NodeKind
  label: string
  file: string
  module: string
  state: TraceNodeState
}

interface OverlayButton {
  key: string
  x: number
  y: number
  action: 'expand' | 'collapse'
  module: string
}

interface Props {
  graph: GraphModel
  expanded: ReadonlySet<string>
  onToggleModule: (module: string) => void
  stateLookup: TraceStateLookup
  onSelect: (sel: SelectedNode | null) => void
}

const s = {
  wrap: { position: 'relative' as const, flex: 1, minHeight: 0, border: `1px solid ${theme.border}`, borderRadius: 8, overflow: 'hidden' },
  canvas: {
    position: 'absolute' as const, inset: 0,
    backgroundColor: '#f7f7f8',
    backgroundImage: 'radial-gradient(#d6d8dd 1px, transparent 1px)',
    backgroundSize: '18px 18px',
  },
  overlay: { position: 'absolute' as const, inset: 0, pointerEvents: 'none' as const },
  expBtn: {
    position: 'absolute' as const, pointerEvents: 'auto' as const, width: 20, height: 20, borderRadius: '50%',
    border: '1.5px solid #dcdfe4', background: '#fff', color: '#4b5563', fontSize: 13, lineHeight: 1,
    padding: 0, cursor: 'pointer', boxShadow: '0 1px 3px rgba(0,0,0,.14)',
  },
  colBtn: {
    position: 'absolute' as const, pointerEvents: 'auto' as const, height: 21, borderRadius: 11,
    border: `1.5px solid ${theme.accentBlue}`, background: '#fff', color: theme.accentBlue, fontSize: 11,
    padding: '0 9px', lineHeight: '19px', cursor: 'pointer', boxShadow: '0 1px 3px rgba(0,0,0,.14)',
  },
  zoom: {
    position: 'absolute' as const, left: 12, bottom: 12, display: 'flex', gap: 4, zIndex: 5,
    background: '#fff', border: `1px solid ${theme.border}`, borderRadius: 8, padding: 3,
    boxShadow: '0 1px 4px rgba(0,0,0,.08)',
  },
  zoomBtn: { width: 26, height: 26, border: 0, background: 'none', color: '#4b5563', borderRadius: 5, fontSize: 14, cursor: 'pointer' },
}

// REQ-064-C7: 편집 어포던스(추가/삭제/드래그 재배선) 없음. 노드 드래그는 시각 정렬용일 뿐
// 좌표는 레이아웃 재실행 시마다 버려지고, 엣지/토폴로지는 절대 바뀌지 않는다.
export default function TraceFlowGraph({ graph, expanded, onToggleModule, stateLookup, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<cytoscape.Core | null>(null)
  const [buttons, setButtons] = useState<OverlayButton[]>([])

  const mods = useMemo(() => uniqueModules(graph), [graph])
  const color = useMemo(() => (mod: string) => colorFor(mod, mods), [mods])

  // cy 인스턴스는 마운트 시 한 번만 만든다 — 스타일은 mods(고정 구조)에만 의존.
  useEffect(() => {
    if (!containerRef.current) return
    const cy = cytoscape({
      container: containerRef.current,
      elements: [],
      boxSelectionEnabled: false,
      style: buildStylesheet(color),
    })
    cyRef.current = cy

    const placeButtons = () => {
      const next: OverlayButton[] = []
      cy.nodes('[kind = "module"]').forEach((n) => {
        const p = n.renderedPosition()
        next.push({
          key: `exp:${n.id()}`, action: 'expand', module: String(n.data('module')),
          x: p.x + n.renderedWidth() / 2 - 10, y: p.y + n.renderedHeight() / 2 - 10,
        })
      })
      cy.nodes(':parent').forEach((n) => {
        const bb = n.renderedBoundingBox()
        next.push({ key: `col:${n.id()}`, action: 'collapse', module: String(n.data('module')), x: bb.x2 - 24, y: bb.y1 + 4 })
      })
      setButtons(next)
    }

    cy.on('pan zoom resize', placeButtons)
    cy.on('tap', 'node', (evt) => {
      const n = evt.target
      const kind = n.data('kind') as NodeKind
      onSelect({
        id: n.id(), kind,
        label: String(n.data('label')).split('\n')[0],
        file: String(n.data('file') ?? ''),
        module: String(n.data('module')),
        state: n.data('state') as TraceNodeState,
      })
    })
    cy.on('tap', (evt) => {
      if (evt.target === cy) onSelect(null)
    })
    ;(cy as unknown as { __placeButtons: () => void }).__placeButtons = placeButtons

    return () => {
      cy.destroy()
      cyRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 그래프/펼침 상태/노드 상태가 바뀔 때마다 엘리먼트를 다시 조립하고 ELK 를 재실행한다.
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.elements().remove()
    cy.add(buildElements(graph, expanded, stateLookup))
    const placeButtons = (cy as unknown as { __placeButtons?: () => void }).__placeButtons
    // ELK 는 비동기다 — layoutstop 을 기다리지 않고 fit 하면 좌표가 아직 0 이다.
    const lay = cy.layout(ELK_LAYOUT as unknown as cytoscape.LayoutOptions)
    lay.one('layoutstop', () => {
      cy.fit(undefined, 40)
      placeButtons?.()
    })
    lay.run()
  }, [graph, expanded, stateLookup])

  const zoomBy = (factor: number) => {
    const cy = cyRef.current
    if (!cy) return
    cy.animate(
      { zoom: { level: cy.zoom() * factor, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } } },
      { duration: 150 },
    )
  }

  return (
    <div style={s.wrap}>
      <div ref={containerRef} style={s.canvas} />
      <div style={s.overlay}>
        {buttons.map((b) =>
          b.action === 'expand' ? (
            <button
              key={b.key} style={{ ...s.expBtn, left: b.x, top: b.y }}
              title={`${b.module} 펼치기`} aria-label={`${b.module} 모듈 펼치기`}
              onClick={(e) => { e.stopPropagation(); onToggleModule(b.module) }}
            >+</button>
          ) : (
            <button
              key={b.key} style={{ ...s.colBtn, left: b.x, top: b.y }}
              title={`${b.module} 접기`} aria-label={`${b.module} 모듈 접기`}
              onClick={(e) => { e.stopPropagation(); onToggleModule(b.module) }}
            >− {b.module}</button>
          ),
        )}
      </div>
      <div style={s.zoom}>
        <button style={s.zoomBtn} title="축소" aria-label="축소" onClick={() => zoomBy(1 / 1.3)}>−</button>
        <button style={s.zoomBtn} title="확대" aria-label="확대" onClick={() => zoomBy(1.3)}>+</button>
        <button style={s.zoomBtn} title="전체 맞춤" aria-label="전체 맞춤" onClick={() => cyRef.current?.animate({ fit: { eles: cyRef.current.elements(), padding: 40 } }, { duration: 250 })}>⤢</button>
      </div>
    </div>
  )
}

function buildStylesheet(color: (mod: string) => string): cytoscape.StylesheetStyle[] {
  return [
    {
      selector: 'node',
      style: {
        shape: 'round-rectangle', width: 58, height: 58,
        'background-color': '#ffffff',
        'background-image': (e: cytoscape.NodeSingular) => GLYPH_URI[e.data('module')] || 'none',
        'background-fit': 'none', 'background-clip': 'none',
        label: 'data(label)',
        color: '#20242b', 'font-size': 10.5, 'font-family': 'ui-monospace,monospace',
        'text-valign': 'bottom', 'text-halign': 'center', 'text-margin-y': 8,
        'text-wrap': 'wrap', 'text-max-width': 120,
        'border-width': 1.5, 'border-color': '#dcdfe4', 'border-opacity': 1,
        'shadow-blur': 5, 'shadow-color': '#20242b', 'shadow-opacity': 0.1, 'shadow-offset-y': 1,
      } as unknown as cytoscape.Css.Node,
    },
    // REQ-064-C2 ① 관여함 — 모듈 색 테두리 + 이벤트 건수(라벨에 이미 포함).
    { selector: 'node[state = "recorded"]', style: { 'border-width': 2.5, 'border-color': (e: cytoscape.NodeSingular) => color(String(e.data('module'))) } as unknown as cytoscape.Css.Node },
    // ② 관여했으나 결정 단위 아님 — 점선 테두리.
    { selector: 'node[state = "decision_agnostic"]', style: { 'border-width': 2, 'border-style': 'dashed', 'border-color': (e: cytoscape.NodeSingular) => color(String(e.data('module'))) } as unknown as cytoscape.Css.Node },
    // ④ 규칙 기반 실행 — 보라색 계열로 명확히 구분.
    { selector: 'node[state = "rule_based"]', style: { 'border-width': 2.5, 'border-color': '#8250df', 'background-color': '#f7f2ff' } as unknown as cytoscape.Css.Node },
    // ③ 관여 안 함 — 옅게 페이드, 배지 없음.
    { selector: 'node[state = "not_involved"]', style: { opacity: 0.42, 'border-color': '#e5e7eb' } as unknown as cytoscape.Css.Node },
    { selector: 'node[kind = "predicate"]', style: { shape: 'diamond', width: 66, height: 66 } as unknown as cytoscape.Css.Node },
    {
      selector: 'node[kind = "entry"]',
      style: {
        shape: 'round-rectangle', width: 66, height: 66,
        'background-color': '#fff6f4', 'border-width': 2.5, 'border-color': '#ff6d5a',
        'background-image': GLYPH_URI.__entry__, 'font-size': 11.5, 'font-weight': 'bold',
      } as unknown as cytoscape.Css.Node,
    },
    {
      selector: ':parent',
      style: {
        shape: 'round-rectangle',
        'background-color': (e: cytoscape.NodeSingular) => color(String(e.data('module'))),
        'background-opacity': 0.06, 'background-image': 'none',
        'border-width': 2, 'border-color': (e: cytoscape.NodeSingular) => color(String(e.data('module'))), 'border-opacity': 0.55,
        label: (e: cytoscape.NodeSingular) => `${e.data('label')}  ${e.data('fns')}개 함수` + (e.data('recordedCount') ? ` · ${e.data('recordedCount')}건 관여` : ''),
        'text-valign': 'top', 'text-halign': 'center', 'text-margin-y': 4,
        'font-size': 12, 'font-weight': 'bold', color: '#4b5563', 'shadow-blur': 0, padding: '18px',
      } as unknown as cytoscape.Css.Node,
    },
    {
      selector: 'node[kind = "module"]',
      style: {
        width: 84, height: 84, 'font-size': 12.5, 'font-weight': 'bold', 'text-margin-y': 9,
        label: (e: cytoscape.NodeSingular) => `${e.data('label')}\n${e.data('fns')}개 함수` + (e.data('recordedCount') ? ` · ${e.data('recordedCount')}건 관여` : ''),
        'background-image': (e: cytoscape.NodeSingular) => GLYPH_URI[e.data('module')] || 'none',
      } as unknown as cytoscape.Css.Node,
    },
    {
      selector: 'edge',
      style: {
        width: 1.6, 'line-color': '#b8bdc7', 'curve-style': 'bezier', 'control-point-step-size': 60,
        'source-arrow-shape': 'circle', 'source-arrow-color': '#b8bdc7',
        'target-arrow-shape': 'triangle', 'target-arrow-color': '#b8bdc7', 'arrow-scale': 0.75,
      } as unknown as cytoscape.Css.Edge,
    },
    // 인수기준: 이 결정의 경로(양끝 recorded)만 별도로 강조.
    { selector: 'edge[?pathHot]', style: { 'line-color': '#ff6d5a', 'source-arrow-color': '#ff6d5a', 'target-arrow-color': '#ff6d5a', width: 2.4, 'z-index': 9 } as unknown as cytoscape.Css.Edge },
  ]
}
