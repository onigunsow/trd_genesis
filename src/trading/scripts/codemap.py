"""의사결정 흐름도 생성기.

구조는 code-graph-mcp 호출그래프에서, 결과는 DB 실측에서 가져와 하나의 HTML로 합친다.
둘을 잇는 다리는 소스 코드의 ``audit("EVENT_TYPE", ...)`` 리터럴이다 —
ast 로 감싸는 함수까지 찾아내므로 이벤트 건수가 함수 블록 단위로 정확히 붙는다.

사용법(호스트에서):

    for s in run_pre_market_cycle run_intraday_cycle; do
        code-graph-mcp callgraph "$s" --direction callees --depth 3 --json
    done | docker exec -i trading-app python -m trading.scripts.codemap > .codemap/index.html

컨테이너는 .codemap/ 에 쓸 수 없으므로 HTML 은 stdout 으로만 내보낸다.
"""

from __future__ import annotations

import ast
import html
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import trading
from trading.db.session import connection

WINDOW_DAYS = 30


# --- 입력: 호출그래프 -------------------------------------------------------


def read_callgraphs(text: str) -> list[dict[str, Any]]:
    """이어붙은 JSON 오브젝트 여러 개를 순서대로 읽는다."""
    dec = json.JSONDecoder()
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            break
        obj, i = dec.raw_decode(text, i)
        out.append(obj)
    return out


def build_graph(
    runs: list[dict[str, Any]], entry_names: list[str] | None = None
) -> tuple[dict[int, dict], set[tuple[int, int]]]:
    """호출그래프 실행 결과들을 하나의 노드/엣지 집합으로 합친다.

    ``entry_names`` 는 호출그래프를 뽑을 때 쓴 심볼 이름을 순서대로 받는다 —
    진입점 노드는 results 에 안 들어오므로 이름을 밖에서 알려줘야 한다.
    """
    entry_names = entry_names or []
    nodes: dict[int, dict] = {}
    edges: set[tuple[int, int]] = set()
    for idx, run in enumerate(runs):
        rows = run.get("results", [])
        for r in rows:
            nodes[r["node_id"]] = {
                "id": r["node_id"],
                "name": r["name"],
                "file": r["file_path"],
                "type": r["type"],
                "depth": r["depth"],
            }
            edges.add((r["parent_id"], r["node_id"]))
        # 진입점 노드는 results 에 없다 — depth 1 행의 parent_id 로 역산한다.
        label = entry_names[idx] if idx < len(entry_names) else None
        for rid in {r["parent_id"] for r in rows if r["depth"] == 1}:
            nodes.setdefault(
                rid,
                {
                    "id": rid,
                    "name": label or f"entry:{rid}",
                    "file": "",
                    "type": "진입점",
                    "depth": 0,
                },
            )
    edges = {(a, b) for a, b in edges if a in nodes and b in nodes and a != b}
    return nodes, edges


# --- 다리: 소스의 audit() 리터럴 -------------------------------------------


def scan_audit_calls(pkg: Path | None = None) -> dict[tuple[str, str], list[str]]:
    """(파일, 감싸는 함수) -> [event_type] 매핑을 소스에서 추출한다.

    파일 경로는 code-graph-mcp 와 맞추기 위해 ``src/trading/...`` 형태로 만든다.
    """
    pkg = pkg or Path(trading.__file__).resolve().parent
    src_root = pkg.parent  # .../src
    found: dict[tuple[str, str], list[str]] = defaultdict(list)

    for path in pkg.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        rel = str(Path(src_root.name) / path.relative_to(src_root))
        stack: list[str] = []

        def walk(node: ast.AST) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    stack.append(child.name)
                    walk(child)
                    stack.pop()
                    continue
                if isinstance(child, ast.Call):
                    fn = child.func
                    name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
                    if name in ("audit", "_audit"):
                        for arg in child.args:
                            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                                lit = arg.value
                                if lit.isupper() and "_" in lit:
                                    found[(rel, stack[-1] if stack else "<module>")].append(lit)
                                break
                walk(child)

        walk(tree)
    return dict(found)


# --- 결과: DB 실측 ----------------------------------------------------------


def fetch_outcomes() -> tuple[dict[str, int], list[dict[str, Any]]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT event_type, count(*) AS n FROM audit_log "
            f"WHERE ts > now() - interval '{WINDOW_DAYS} days' GROUP BY 1"
        )
        events = {r["event_type"]: r["n"] for r in cur.fetchall()}
        cur.execute(
            "SELECT status, rejected_reason, count(*) AS n FROM orders "
            f"WHERE ts > now() - interval '{WINDOW_DAYS} days' "
            "GROUP BY 1, 2 ORDER BY n DESC"
        )
        orders = [dict(r) for r in cur.fetchall()]
    return events, orders


# --- 출력 -------------------------------------------------------------------


def to_cytoscape(
    nodes: dict[int, dict],
    edges: set[tuple[int, int]],
    audit_map: dict[tuple[str, str], list[str]],
    event_counts: dict[str, int],
) -> list[dict[str, Any]]:
    elements = []
    for nid, n in nodes.items():
        path = n["file"].removeprefix("src/trading/").removesuffix(".py") or "?"
        evs = audit_map.get((n["file"], n["name"]), [])
        # 소스에 있는 이벤트만 센다. DB 에 기록이 없으면 0 — 추정하지 않는다.
        rows = [{"event": e, "n": event_counts.get(e, 0)} for e in sorted(set(evs))]
        elements.append(
            {
                "data": {
                    "id": str(nid),
                    "label": n["name"],
                    "file": n["file"],
                    "module": path.split("/")[0],
                    "path": path,
                    "kind": n["type"],
                    "events": rows,
                    "total": sum(r["n"] for r in rows),
                }
            }
        )
    for a, b in edges:
        elements.append({"data": {"id": f"e{a}-{b}", "source": str(a), "target": str(b)}})
    return elements


def render(
    elements: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    n_nodes: int,
    n_files: int,
) -> str:
    payload = json.dumps(elements, ensure_ascii=False)
    order_rows = (
        "".join(
            "<tr><td>{}</td><td>{}</td><td class=num>{}</td></tr>".format(
                html.escape(str(o["status"])),
                html.escape(str(o["rejected_reason"] or "")),
                o["n"],
            )
            for o in orders
        )
        or "<tr><td colspan=3 class=dim>기록 없음</td></tr>"
    )

    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>trading — 의사결정 흐름도</title>
<script src="https://cdn.jsdelivr.net/npm/cytoscape@3.30.2/dist/cytoscape.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.js"></script>
<style>
:root{{--bg:#0f1115;--panel:#161a21;--fg:#e6e9ef;--dim:#8b95a5;--line:#242a33}}
*{{box-sizing:border-box}}
body{{margin:0;height:100vh;display:flex;background:var(--bg);color:var(--fg);
 font:14px/1.55 -apple-system,"Noto Sans KR",sans-serif;overflow:hidden}}
#cy{{flex:1;height:100vh}}
aside{{width:360px;background:var(--panel);border-left:1px solid var(--line);
 padding:18px;overflow-y:auto}}
h1{{font-size:16px;margin:0 0 2px}}
h2{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);
 margin:22px 0 8px;font-weight:600}}
.sub{{color:var(--dim);font-size:12px}}
input{{width:100%;background:#0f1115;border:1px solid var(--line);color:var(--fg);
 border-radius:6px;padding:7px 10px;font:inherit;font-size:13px;margin-top:12px}}
table{{border-collapse:collapse;width:100%;font-size:12px}}
td,th{{border-bottom:1px solid var(--line);padding:5px 6px;text-align:left;vertical-align:top}}
th{{color:var(--dim);font-weight:600}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}
.dim{{color:var(--dim)}}
code{{font:12px ui-monospace,monospace;color:#c0caf5;word-break:break-all}}
#sel{{min-height:60px}}
.chip{{display:inline-block;background:#1e2430;border:1px solid var(--line);
 border-radius:5px;padding:1px 7px;font-size:11px;color:var(--dim)}}
.legend span{{display:inline-flex;align-items:center;gap:5px;margin:3px 9px 3px 0;font-size:11px}}
.dot{{width:9px;height:9px;border-radius:50%;display:inline-block}}
</style></head><body>
<div id="cy"></div>
<aside>
  <h1>의사결정 흐름도</h1>
  <div class="sub">{n_nodes}개 블록 · {n_files}개 파일 · 결과는 최근 {WINDOW_DAYS}일 실측</div>
  <input id="q" placeholder="함수 / 모듈 검색">
  <h2>선택한 블록</h2>
  <div id="sel" class="dim">노드를 클릭하면 파일 경로와 실제로 남긴 기록이 나옵니다.
    테두리가 있는 노드는 audit 기록을 남기는 지점입니다.</div>
  <h2>모듈</h2>
  <div id="legend" class="legend"></div>
  <h2>주문 결과 {WINDOW_DAYS}일</h2>
  <table><tr><th>상태</th><th>사유</th><th class=num>건</th></tr>{order_rows}</table>
</aside>
<script>
const WIN = {WINDOW_DAYS};
const els = {payload};
const mods = [...new Set(els.filter(e => e.data.module).map(e => e.data.module))].sort();
const palette = ['#7aa2f7','#f7768e','#9ece6a','#e0af68','#bb9af7','#7dcfff','#ff9e64',
                 '#73daca','#c0caf5','#f7c8dc','#a0d8b3','#d9a066'];
const color = m => palette[Math.max(0, mods.indexOf(m)) % palette.length];
document.getElementById('legend').innerHTML = mods.map(m =>
  '<span data-m="' + m + '"><i class="dot" style="background:' + color(m) + '"></i>' +
  m + '</span>').join('');

const cy = cytoscape({{
  container: document.getElementById('cy'),
  elements: els,
  layout: {{name: 'dagre', rankDir: 'LR', nodeSep: 26, rankSep: 260, edgeSep: 10}},
  style: [
    {{selector: 'node', style: {{
      'label': 'data(label)', 'color': '#e6e9ef', 'font-size': 11,
      'font-family': 'ui-monospace,monospace',
      'text-valign': 'center', 'text-halign': 'right', 'text-margin-x': 7,
      'background-color': e => color(e.data('module')),
      'width':  e => 12 + Math.min(26, Math.sqrt(e.data('total') || 0) * 2),
      'height': e => 12 + Math.min(26, Math.sqrt(e.data('total') || 0) * 2),
      'border-width': e => e.data('events').length ? 2 : 0, 'border-color': '#fff'
    }}}},
    {{selector: 'node[kind = "진입점"]', style: {{
      'shape': 'round-rectangle', 'width': 15, 'height': 15,
      'background-color': '#fff', 'font-size': 13, 'font-weight': 'bold'
    }}}},
    {{selector: 'edge', style: {{
      'width': 1, 'line-color': '#2c333f', 'target-arrow-color': '#2c333f',
      'target-arrow-shape': 'triangle', 'arrow-scale': .7, 'curve-style': 'bezier'
    }}}},
    {{selector: '.faded', style: {{'opacity': .1, 'text-opacity': .04}}}},
    {{selector: '.hot', style: {{
      'line-color': '#7aa2f7', 'target-arrow-color': '#7aa2f7', 'width': 2, 'z-index': 9
    }}}}
  ]
}});

function show(n) {{
  const d = n.data();
  let evs;
  if (d.events.length) {{
    evs = '<table><tr><th>남기는 기록</th><th class=num>' + WIN + '일</th></tr>' +
      d.events.map(e => '<tr><td>' + e.event + '</td><td class=num>' +
        (e.n ? e.n : '<span class=dim>0</span>') + '</td></tr>').join('') + '</table>';
  }} else {{
    evs = '<div class="dim">이 블록은 audit 기록을 남기지 않습니다.</div>';
  }}
  document.getElementById('sel').innerHTML =
    '<div><b>' + d.label + '</b> <span class="chip">' + d.kind + '</span></div>' +
    '<div style="margin:6px 0"><code>' + (d.file || '—') + '</code></div>' + evs;
}}

cy.on('tap', 'node', e => {{
  const n = e.target;
  const hood = n.predecessors().union(n.successors()).union(n);
  cy.elements().addClass('faded');
  hood.removeClass('faded');
  n.predecessors('edge').addClass('hot');
  show(n);
}});
cy.on('tap', e => {{
  if (e.target === cy) cy.elements().removeClass('faded').removeClass('hot');
}});

// 모듈 범례 클릭 = 그 모듈이 관여하는 경로만 남긴다.
let picked = null;
document.getElementById('legend').addEventListener('click', ev => {{
  const chip = ev.target.closest('[data-m]');
  if (!chip) return;
  const m = chip.dataset.m;
  cy.elements().removeClass('faded').removeClass('hot');
  document.querySelectorAll('#legend [data-m]').forEach(c => c.style.opacity = 1);
  if (picked === m) {{ picked = null; return; }}
  picked = m;
  const own = cy.nodes().filter(n => n.data('module') === m);
  const hood = own.union(own.predecessors()).union(own.successors());
  cy.elements().addClass('faded');
  hood.removeClass('faded');
  own.predecessors('edge').addClass('hot');
  document.querySelectorAll('#legend [data-m]').forEach(
    c => c.style.opacity = c.dataset.m === m ? 1 : .35);
}});

document.getElementById('q').addEventListener('input', ev => {{
  const q = ev.target.value.trim().toLowerCase();
  cy.elements().removeClass('faded').removeClass('hot');
  if (!q) return;
  cy.nodes().forEach(n => {{
    const hit = (n.data('label') + ' ' + n.data('path')).toLowerCase().includes(q);
    if (!hit) n.addClass('faded');
  }});
}});
</script></body></html>"""


def selftest() -> None:
    """가장 작은 실행 가능한 검사 — 다리(ast 스캔)와 그래프 조립이 살아있는지만 본다."""
    m = scan_audit_calls()
    hits = {k: v for k, v in m.items() if "LIMIT_BREACH" in v}
    assert hits, "risk/limits.py 의 LIMIT_BREACH 를 못 찾았다 — ast 스캔이 깨졌다"
    assert any("limits.py" in f for f, _ in hits), f"엉뚱한 파일에서 찾았다: {hits}"
    assert all(f.startswith("src/trading/") for f, _ in m), "경로 형식이 code-graph 와 안 맞는다"

    nodes, edges = build_graph(
        read_callgraphs(
            '{"results":[{"depth":1,"direction":"callees","file_path":"a.py",'
            '"name":"x","node_id":2,"parent_id":1,"type":"function"}]}'
        )
    )
    assert set(nodes) == {1, 2} and edges == {(1, 2)}, (nodes, edges)
    print(f"selftest ok: {len(m)}개 블록이 audit 기록을 남긴다", file=sys.stderr)


def main() -> None:
    if "--selftest" in sys.argv:
        selftest()
        return
    entry_names = [a for a in sys.argv[1:] if not a.startswith("-")]
    runs = read_callgraphs(sys.stdin.read())
    if not runs:
        sys.exit("호출그래프 JSON 이 stdin 으로 들어오지 않았다")
    nodes, edges = build_graph(runs, entry_names)
    audit_map = scan_audit_calls()
    event_counts, orders = fetch_outcomes()
    elements = to_cytoscape(nodes, edges, audit_map, event_counts)
    n_files = len({n["file"] for n in nodes.values() if n["file"]})
    sys.stdout.write(render(elements, orders, len(nodes), n_files))


if __name__ == "__main__":
    main()
