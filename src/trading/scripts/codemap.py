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
from urllib.parse import quote

import trading
from trading.db.session import connection

WINDOW_DAYS = 30

# 사람이 읽는 설명. 여기 적힌 심볼이 실제 호출그래프에 없으면 화면에 경고가 뜬다 —
# 손으로 쓴 설명이 조용히 낡는 것을 막는 장치다(verify_overview 참조).
STAGES = [
    ("상태·계좌 확보", "지금 매매해도 되는 상태인지, 계좌에 얼마가 있는지부터 확인한다.",
     ["get_system_state", "balance"]),
    ("후보 선별", "차단된 종목을 먼저 걷어내고 판단 대상만 남긴다.",
     ["_filter_and_expand_candidates", "get_blocked_tickers"]),
    ("페르소나 판단", "매크로·마이크로·의사결정 페르소나가 후보와 근거를 만든다. CLI 경로라 비용 0.",
     ["call_persona", "resolve_model"]),
    ("포트폴리오 조정", "제안을 거부하지 않고 국면·섹터 한도에 맞춰 수정한다.",
     ["_apply_portfolio_adjustment", "_emit_transparency", "adjust_for_regime",
      "enforce_sector_cap"]),
    ("리스크 관문", "여기서 막히면 주문이 나가지 않는다. 일일손실 한도만 하루 정지를 부른다.",
     ["check_pre_order", "record_breach", "check_pre_order_safety",
      "requires_circuit_halt", "trip"]),
    ("사이징", "얼마나 살지 정한다. 엣지 검증 미통과면 실거래 사이징이 차단된다.",
     ["compute_qty", "half_kelly_cap", "portfolio_heat", "is_validation_passed"]),
    ("실행", "중복 매도를 잠그고, 증권사 보유수량을 진실로 삼아 수량을 다시 깎는다.",
     ["_execute_signal", "guard_sell", "set_sell_inflight", "clamp_sell_to_confirmed",
      "intraday_reconcile", "resolve_stuck_orders"]),
    ("보고", "결과를 텔레그램으로 알린다. 실패도 알린다.",
     ["persona_briefing", "trade_briefing", "order_rejected", "system_error"]),
]

# 모듈별 픽토그램. 노코드 캔버스에서 타일 안에 들어간다.
MODULE_GLYPH = {
    "personas": "🧠", "risk": "🛡", "strategy": "📐", "edge": "📈", "kis": "🏦",
    "alerts": "🔔", "data": "📊", "db": "🗄", "screener": "🔍", "models": "🤖",
    "tools": "🔧", "config": "⚙", "scripts": "▶", "news": "📰", "watchers": "👁",
    "jit": "⚡", "tests": "🧪", "__entry__": "🚀",
}

MODULE_ROLE = {
    "personas": "사이클 전체 지휘 + LLM 판단 생성. 후보 선별·게이트 호출·실행·보고를 엮는 유일한 지점",
    "risk": "한도·안전성 검사와 매매 정지. 통과 못하면 주문 없음",
    "strategy": "수량 결정 — 변동성 목표 기본 수량에 half-Kelly 상한과 포트폴리오 히트를 적용",
    "edge": "엣지 검증과 실현손익 산출. 일일손실 한도의 입력이자 실거래 사이징의 관문",
    "kis": "증권사 연동. 보유수량을 단일 진실로 삼아 매도 수량을 클램프하고 미체결을 정리",
    "alerts": "텔레그램 통보",
    "data": "시세·종목 데이터 수집. KRX 장애 시 여기서 차단되어 판단 자체가 굶는다",
    "db": "상태 저장과 audit 기록",
    "screener": "유니버스 확장",
    "models": "어떤 모델로 판단할지 라우팅",
    "tools": "페르소나가 쓸 도구 결정",
    "config": "설정과 수수료 추정",
}


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


def scan_audit_calls(pkg: Path | None = None) -> tuple[dict[tuple[str, str], list[str]], set[str]]:
    """(파일, 감싸는 함수) -> [event_type] 매핑과, 소스에 존재하는 전체 함수 이름을 뽑는다.

    파일 경로는 code-graph-mcp 와 맞추기 위해 ``src/trading/...`` 형태로 만든다.
    함수 이름 집합은 "설명이 낡았다"와 "이 경로 밖에 있다"를 구분하는 데 쓴다.
    """
    pkg = pkg or Path(trading.__file__).resolve().parent
    src_root = pkg.parent  # .../src
    found: dict[tuple[str, str], list[str]] = defaultdict(list)
    all_funcs: set[str] = set()

    for path in pkg.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        rel = str(Path(src_root.name) / path.relative_to(src_root))
        stack: list[str] = []

        def walk(node: ast.AST) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    all_funcs.add(child.name)
                    if isinstance(child, ast.ClassDef):
                        walk(child)
                        continue
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
    return dict(found), all_funcs


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
        kind = n["type"]
        if kind != "진입점" and n["name"].startswith(
            ("check_", "requires_", "is_", "guard_", "_split_", "has_")
        ):
            kind = "판정"
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
                    "kind": kind,
                    "events": rows,
                    "total": sum(r["n"] for r in rows),
                }
            }
        )
    # 선 위 라벨은 지어내지 않는다 — 도착 블록이 실제로 남긴 기록을 그대로 쓴다.
    label_of = {
        e["data"]["id"]: (
            f'{max(e["data"]["events"], key=lambda r: r["n"])["event"]} '
            f'{max(e["data"]["events"], key=lambda r: r["n"])["n"]}'
            if e["data"]["total"]
            else ""
        )
        for e in elements
    }
    for a, b in edges:
        elements.append(
            {
                "data": {
                    "id": f"e{a}-{b}",
                    "source": str(a),
                    "target": str(b),
                    "label": label_of.get(str(b), ""),
                }
            }
        )
    return elements


def glyph_uris() -> dict[str, str]:
    """모듈 픽토그램을 인라인 SVG data URI 로 만든다(외부 아이콘 의존 없음)."""
    out = {}
    for mod, ch in MODULE_GLYPH.items():
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="44" height="44">'
            f'<text x="22" y="30" font-size="21" text-anchor="middle">{ch}</text></svg>'
        )
        out[mod] = "data:image/svg+xml;utf8," + quote(svg, safe="")
    return out


def render_overview(elements: list[dict[str, Any]], all_funcs: set[str]) -> tuple[str, str, int]:
    """사람이 읽는 설명 화면. 설명에 적힌 심볼을 실제 그래프와 대조해 낡음을 잡아낸다."""
    by_name: dict[str, dict] = {}
    for e in elements:
        d = e["data"]
        if "source" not in d:
            by_name.setdefault(d["label"], d)

    stale = 0
    cards = []
    mermaid = ["flowchart LR", '  E(["진입점"])']
    prev = "E"
    for i, (title, why, syms) in enumerate(STAGES):
        node = f"S{i}"
        mermaid.append(f'  {prev} --> {node}["{i + 1}. {title}"]')
        prev = node
        chips = []
        total = 0
        for s in syms:
            hit = by_name.get(s)
            if hit is None:
                # 소스에 있으면 단지 이 깊이 밖일 뿐이고, 없으면 설명이 낡은 것이다.
                if s in all_funcs:
                    chips.append(
                        f'<span class="chip far" title="소스에 있으나 이 경로 깊이 밖">'
                        f"{html.escape(s)}</span>"
                    )
                else:
                    stale += 1
                    chips.append(
                        f'<span class="chip miss" title="소스에서 사라짐">⚠ {html.escape(s)}</span>'
                    )
                continue
            total += hit["total"]
            n = f' <b>{hit["total"]}</b>' if hit["total"] else ""
            chips.append(f'<span class="chip ok" data-go="{html.escape(s)}">{html.escape(s)}{n}</span>')
        cards.append(
            f'<div class="card"><div class="ct"><span class="no">{i + 1}</span>{html.escape(title)}'
            + (f'<span class="tot">{total}건</span>' if total else "")
            + f'</div><div class="cw">{html.escape(why)}</div><div>{"".join(chips)}</div></div>'
        )

    mods_seen = {e["data"]["module"] for e in elements if "source" not in e["data"]}
    rows = "".join(
        f"<tr><td class=mod>{html.escape(m)}</td><td>{html.escape(MODULE_ROLE.get(m, ''))}</td></tr>"
        for m in sorted(mods_seen)
        if m and m != "?"
    )
    table = f"<table><tr><th>모듈</th><th>어떤 결정에 관여하는가</th></tr>{rows}</table>"
    return "".join(cards) + table, "\n".join(mermaid), stale


def render(
    elements: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    n_nodes: int,
    n_files: int,
    all_funcs: set[str],
) -> str:
    payload = json.dumps(elements, ensure_ascii=False)
    glyphs = json.dumps(glyph_uris(), ensure_ascii=False)
    roles = json.dumps(MODULE_ROLE, ensure_ascii=False)
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
    overview, mermaid_src, stale = render_overview(elements, all_funcs)
    stale_banner = (
        f'<div class="warn">설명에 적힌 심볼 {stale}개가 소스에서 사라졌습니다 — '
        "코드가 바뀌었는데 설명이 안 따라왔다는 뜻입니다. STAGES 를 고치세요.</div>"
        if stale
        else ""
    )

    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>trading — 의사결정 흐름도</title>
<script src="https://cdn.jsdelivr.net/npm/cytoscape@3.30.2/dist/cytoscape.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<style>
:root{{--bg:#0f1115;--panel:#161a21;--fg:#e6e9ef;--dim:#8b95a5;--line:#242a33}}
*{{box-sizing:border-box}}
body{{margin:0;height:100vh;display:flex;background:var(--bg);color:var(--fg);
 font:14px/1.55 -apple-system,"Noto Sans KR",sans-serif;overflow:hidden}}
main{{flex:1;height:100vh;display:flex;flex-direction:column;min-width:0}}
nav{{display:flex;gap:4px;padding:10px 14px 0;border-bottom:1px solid var(--line)}}
nav button{{background:none;border:0;border-bottom:2px solid transparent;color:var(--dim);
 font:inherit;font-size:13px;padding:8px 14px;cursor:pointer}}
nav button.on{{color:var(--fg);border-bottom-color:#7aa2f7}}
#crumb{{margin-left:16px;align-self:center;font-size:12px;color:var(--dim)}}
#crumb a{{color:#7aa2f7;text-decoration:none}}
#crumb b{{color:var(--fg);margin-left:4px}}
#ov{{flex:1;overflow-y:auto;padding:24px 28px 60px}}
#ov.hide,#cy.hide{{display:none}}
#cy{{flex:1;min-height:0;background:#f6f7f9}}
.warn{{background:#3d2b2b;border:1px solid #c0616f;border-radius:8px;padding:10px 14px;
 margin-bottom:18px;font-size:13px}}
.mm{{background:#12151b;border:1px solid var(--line);border-radius:10px;padding:14px;
 margin-bottom:22px;overflow-x:auto}}
.card{{border-left:2px solid #2c333f;padding:2px 0 14px 16px;margin-left:6px}}
.ct{{font-size:15px;font-weight:600;display:flex;align-items:center;gap:9px}}
.no{{background:#7aa2f7;color:#0f1115;border-radius:50%;width:20px;height:20px;
 display:inline-flex;align-items:center;justify-content:center;font-size:11px;flex:0 0 auto}}
.tot{{margin-left:auto;font-size:12px;color:var(--dim);font-weight:400}}
.cw{{color:#b7c0cf;font-size:13px;margin:3px 0 7px}}
.chip.ok{{cursor:pointer;font-family:ui-monospace,monospace}}
.chip.ok:hover{{border-color:#7aa2f7;color:#fff}}
.chip.miss{{border-color:#c0616f;color:#f7768e}}
#ov table{{margin-top:26px}}
td.mod{{font-family:ui-monospace,monospace;color:#c0caf5;white-space:nowrap}}
tr.go{{cursor:pointer}}
tr.go:hover td{{background:#1e2430;color:#fff}}
.chip.go{{cursor:pointer}}
.chip.go:hover{{border-color:#7aa2f7;color:#fff}}
button.drill{{margin:10px 0 2px;width:100%;background:#1e2430;border:1px solid var(--line);
 color:#7aa2f7;border-radius:6px;padding:7px;font:inherit;font-size:12px;cursor:pointer}}
button.drill:hover{{border-color:#7aa2f7;background:#232a37}}
aside .cw{{color:#b7c0cf;font-size:13px;margin:4px 0 6px}}
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
<main>
  <nav>
    <button id="tab-ov" class="on">개요 — 판단은 이렇게 흐른다</button>
    <button id="tab-gr">상세 그래프 — {n_nodes}개 블록</button>
    <span id="crumb"></span>
  </nav>
  <div id="ov">
    {stale_banner}
    <div class="mm"><pre class="mermaid">{mermaid_src}</pre></div>
    {overview}
  </div>
  <div id="cy" class="hide"></div>
</main>
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
const GLYPH = {glyphs};
const ROLE = {roles};
const mods = [...new Set(els.filter(e => e.data.module).map(e => e.data.module))].sort();
const palette = ['#2f6df6','#e8453c','#2aa84a','#f5a623','#8b53d4','#00a9c4','#ff7043',
                 '#12a594','#5c6bc0','#e0518f','#66a83a','#b07a2e'];
const color = m => palette[Math.max(0, mods.indexOf(m)) % palette.length];
document.getElementById('legend').innerHTML = mods.map(m =>
  '<span data-m="' + m + '"><i class="dot" style="background:' + color(m) + '"></i>' +
  m + '</span>').join('');

const FN = els.filter(e => !e.data.source);
const ED = els.filter(e => e.data.source);
const BY_ID = Object.fromEntries(FN.map(e => [e.data.id, e.data]));

// 1단계 — 모듈 뷰. 진입점은 모듈로 접지 않고 그대로 둔다(흐름의 시작점이라서).
function moduleView() {{
  const nodes = {{}};
  for (const e of FN) {{
    const d = e.data;
    if (d.kind === '진입점') {{ nodes[d.id] = {{data: d}}; continue; }}
    const k = 'm:' + d.module;
    if (!nodes[k]) nodes[k] = {{data: {{
      id: k, label: d.module, module: d.module, kind: '모듈', path: d.module,
      file: '', events: [], total: 0, fns: 0
    }}}};
    nodes[k].data.fns++;
    nodes[k].data.total += d.total;
  }}
  const key = d => d.kind === '진입점' ? d.id : 'm:' + d.module;
  const cnt = {{}};
  for (const e of ED) {{
    const s = BY_ID[e.data.source], t = BY_ID[e.data.target];
    if (!s || !t) continue;
    const a = key(s), b = key(t);
    if (a === b) continue;
    cnt[a + '>' + b] = (cnt[a + '>' + b] || 0) + 1;
  }}
  const edges = Object.entries(cnt).map(([k, n]) => {{
    const p = k.split('>');
    return {{data: {{id: 'me' + k, source: p[0], target: p[1], label: n + '개 호출'}}}};
  }});
  return Object.values(nodes).concat(edges);
}}

// 2단계 — 한 모듈의 함수들 + 그 함수들과 직접 이어진 바깥 블록.
function drillView(m) {{
  const own = new Set(FN.filter(e => e.data.module === m).map(e => e.data.id));
  const keep = new Set(own);
  for (const e of ED) {{
    if (own.has(e.data.source)) keep.add(e.data.target);
    if (own.has(e.data.target)) keep.add(e.data.source);
  }}
  return FN.filter(e => keep.has(e.data.id))
    .concat(ED.filter(e => keep.has(e.data.source) && keep.has(e.data.target)));
}}

const LAYOUT = {{name: 'dagre', rankDir: 'LR', nodeSep: 34, rankSep: 150, edgeSep: 14}};
let view = null;   // null = 모듈 뷰, 문자열 = 그 모듈의 함수 뷰

function setView(m) {{
  view = m;
  cy.elements().remove();
  cy.add(m ? drillView(m) : moduleView());
  cy.layout(LAYOUT).run();
  cy.fit(40);
  document.getElementById('crumb').innerHTML = m
    ? '<a href="#" id="back">← 모듈 전체</a> <b>' + m + '</b>'
    : '모듈을 클릭하면 그 안의 함수 블록이 열립니다';
  const b = document.getElementById('back');
  if (b) b.onclick = ev => {{ ev.preventDefault(); setView(null); }};
  renderLevel();
}}

const cy = cytoscape({{
  container: document.getElementById('cy'),
  elements: [],
  style: [
    // 노코드 캔버스 — 색 타일 안에 픽토그램, 이름은 타일 아래.
    {{selector: 'node', style: {{
      'shape': 'round-rectangle', 'width': 44, 'height': 44,
      'background-color': e => color(e.data('module')),
      'background-image': e => GLYPH[e.data('module')] || 'none',
      'background-fit': 'none', 'background-clip': 'none',
      'label': 'data(label)',
      'color': '#3b4453', 'font-size': 10, 'font-family': 'ui-monospace,monospace',
      'text-valign': 'bottom', 'text-halign': 'center', 'text-margin-y': 7,
      'text-wrap': 'wrap', 'text-max-width': 110,
      'border-width': e => e.data('events').length ? 3 : 0, 'border-color': '#ffffff',
      'border-opacity': 1,
      'shadow-blur': 6, 'shadow-color': '#8a93a5', 'shadow-opacity': .25,
      'shadow-offset-y': 2
    }}}},
    // 이름이 check_/requires_/is_/guard_ 로 시작하면 판정 블록 — 다이아몬드로 세운다.
    {{selector: 'node[kind = "판정"]', style: {{
      'shape': 'diamond', 'width': 52, 'height': 52, 'font-weight': 'bold'
    }}}},
    {{selector: 'node[kind = "진입점"]', style: {{
      'shape': 'ellipse', 'width': 56, 'height': 56, 'background-color': '#2f6df6',
      'background-image': GLYPH['__entry__'],
      'font-size': 12, 'font-weight': 'bold', 'color': '#1b2534'
    }}}},
    // 모듈 타일은 함수 타일보다 크고, 라벨에 함수 수와 기록 건수를 함께 적는다.
    {{selector: 'node[kind = "모듈"]', style: {{
      'width': 78, 'height': 78, 'font-size': 13, 'font-weight': 'bold',
      'text-margin-y': 9, 'color': '#1b2534',
      'label': e => e.data('label') + '\\n' + e.data('fns') + '개 함수'
        + (e.data('total') ? ' · ' + e.data('total') + '건' : ''),
      'background-image': e => GLYPH[e.data('module')] || 'none',
      'border-width': e => e.data('total') ? 4 : 0
    }}}},
    {{selector: 'edge', style: {{
      'width': 1.2, 'line-color': '#c3cad6', 'target-arrow-color': '#c3cad6',
      'target-arrow-shape': 'triangle', 'arrow-scale': .9,
      'curve-style': 'taxi', 'taxi-direction': 'horizontal',
      // 팬아웃이 큰 노드에서 모든 선이 같은 x 에서 꺾이면 세로줄 뭉치가 된다.
      // 엣지마다 꺾는 지점을 흩뿌려 겹침을 푼다.
      'taxi-turn': e => (18 + (e.id().length * 13 + e.id().charCodeAt(1) * 7) % 62) + 'px',
      'taxi-turn-min-distance': 10,
      'label': 'data(label)', 'font-size': 9, 'color': '#7c8798',
      'text-background-color': '#f6f7f9', 'text-background-opacity': 1,
      'text-background-padding': 2
    }}}},
    {{selector: '.faded', style: {{'opacity': .12, 'text-opacity': .05}}}},
    {{selector: '.hot', style: {{
      'line-color': '#2f6df6', 'target-arrow-color': '#2f6df6', 'width': 2,
      'color': '#2f6df6', 'z-index': 9
    }}}}
  ]
}});

// ---- 우측 패널: 지금 보고 있는 레벨에 맞는 내용을 낸다 --------------------
const SEL = document.getElementById('sel');
const esc = s => String(s).replace(/[&<>]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;'}})[c]);
const fnsOf = m => FN.filter(e => e.data.module === m).map(e => e.data);
const evTable = rows => rows.length
  ? '<table><tr><th>남기는 기록</th><th class=num>' + WIN + '일</th></tr>' +
    rows.map(e => '<tr><td>' + esc(e.event) + '</td><td class=num>' +
      (e.n ? e.n : '<span class=dim>0</span>') + '</td></tr>').join('') + '</table>'
  : '<div class="dim">audit 기록을 남기지 않습니다.</div>';

// 모듈 사이 연결 — 어느 모듈에서 들어오고 어디로 나가는지.
function modLinks(m) {{
  const inn = {{}}, out = {{}};
  for (const e of ED) {{
    const s = BY_ID[e.data.source], t = BY_ID[e.data.target];
    if (!s || !t) continue;
    const sm = s.kind === '진입점' ? s.label : s.module;
    const tm = t.kind === '진입점' ? t.label : t.module;
    if (tm === m && sm !== m) inn[sm] = (inn[sm] || 0) + 1;
    if (sm === m && tm !== m) out[tm] = (out[tm] || 0) + 1;
  }}
  const fmt = o => Object.entries(o).sort((a, b) => b[1] - a[1])
    .map(([k, n]) => '<span class="chip">' + esc(k) + ' ' + n + '</span>').join('') || '<span class=dim>없음</span>';
  return '<h2>연결</h2><div class="cw">들어옴</div><div>' + fmt(inn) +
         '</div><div class="cw" style="margin-top:8px">나감</div><div>' + fmt(out) + '</div>';
}}

// 레벨 0 — 모듈 순위표.
function panelModules() {{
  const rows = mods.filter(m => m && m !== '?').map(m => {{
    const f = fnsOf(m);
    return {{m, n: f.length, t: f.reduce((a, d) => a + d.total, 0)}};
  }}).sort((a, b) => b.t - a.t || b.n - a.n);
  SEL.innerHTML = '<div class="dim" style="font-size:12px;margin-bottom:8px">' +
    '모듈 타일을 클릭하면 여기에 상세가 나옵니다.</div>' +
    '<table><tr><th>모듈</th><th class=num>함수</th><th class=num>기록</th></tr>' +
    rows.map(r => '<tr class="go" data-m="' + esc(r.m) + '"><td class=mod>' + esc(r.m) +
      '</td><td class=num>' + r.n + '</td><td class=num>' +
      (r.t ? r.t : '<span class=dim>0</span>') + '</td></tr>').join('') + '</table>';
}}

// 레벨 0 선택 — 모듈 상세.
function panelModule(m) {{
  const f = fnsOf(m);
  const hot = f.filter(d => d.total).sort((a, b) => b.total - a.total);
  SEL.innerHTML =
    '<div><b>' + esc(m) + '</b> <span class="chip">모듈</span></div>' +
    '<div class="cw">' + esc(ROLE[m] || '') + '</div>' +
    '<div class="dim" style="font-size:12px">함수 ' + f.length + '개 · 30일 기록 ' +
      f.reduce((a, d) => a + d.total, 0) + '건</div>' +
    '<button class="drill" data-go-m="' + esc(m) + '">안의 함수 블록 열기 →</button>' +
    (hot.length ? '<h2>기록을 남기는 함수</h2><table>' + hot.map(d =>
      '<tr class="go" data-f="' + esc(d.label) + '"><td class=mod>' + esc(d.label) +
      '</td><td class=num>' + d.total + '</td></tr>').join('') + '</table>' : '') +
    modLinks(m);
}}

// 레벨 1 — 이 모듈의 함수 목록.
function panelFuncs(m) {{
  const f = fnsOf(m).sort((a, b) => b.total - a.total || a.label.localeCompare(b.label));
  SEL.innerHTML =
    '<div><b>' + esc(m) + '</b> <span class="chip">함수 ' + f.length + '개</span></div>' +
    '<div class="cw">' + esc(ROLE[m] || '') + '</div>' +
    '<div class="dim" style="font-size:12px;margin-bottom:6px">블록을 클릭하면 상세가 나옵니다.</div>' +
    '<table>' + f.map(d => '<tr class="go" data-f="' + esc(d.label) + '"><td class=mod>' +
      esc(d.label) + (d.kind === '판정' ? ' <span class="chip">판정</span>' : '') +
      '</td><td class=num>' + (d.total ? d.total : '<span class=dim>·</span>') +
      '</td></tr>').join('') + '</table>' + modLinks(m);
}}

// 레벨 1 선택 — 함수 상세 + 호출 관계.
function panelFunc(n) {{
  const d = n.data();
  const list = (eles, none) => eles.length
    ? eles.map(x => '<span class="chip go" data-f="' + esc(x.data('label')) + '">' +
        esc(x.data('label')) + '</span>').join('')
    : '<span class=dim>' + none + '</span>';
  SEL.innerHTML =
    '<div><b>' + esc(d.label) + '</b> <span class="chip">' + esc(d.kind) + '</span></div>' +
    '<div style="margin:6px 0"><code>' + esc(d.file || '—') + '</code></div>' +
    evTable(d.events) +
    '<h2>이 블록을 부르는 곳</h2><div>' + list(n.incomers('node'), '없음(진입점 계열)') + '</div>' +
    '<h2>이 블록이 부르는 곳</h2><div>' + list(n.outgoers('node'), '없음(말단)') + '</div>';
}}

function renderLevel() {{ view ? panelFuncs(view) : panelModules(); }}

function focusFn(label) {{
  const n = cy.nodes().filter(x => x.data('label') === label);
  if (!n.length) return;
  cy.elements().addClass('faded');
  n.union(n.predecessors()).union(n.successors()).removeClass('faded');
  n.predecessors('edge').addClass('hot');
  cy.animate({{center: {{eles: n}}}}, {{duration: 250}});
  panelFunc(n[0]);
}}

cy.on('tap', 'node', e => {{
  const n = e.target;
  if (n.data('kind') === '모듈') {{ panelModule(n.data('module')); return; }}
  const hood = n.predecessors().union(n.successors()).union(n);
  cy.elements().addClass('faded');
  hood.removeClass('faded');
  n.predecessors('edge').addClass('hot');
  panelFunc(n);
}});
cy.on('tap', e => {{
  if (e.target === cy) {{ cy.elements().removeClass('faded').removeClass('hot'); renderLevel(); }}
}});

// 패널 안의 행·칩·버튼 클릭을 한 곳에서 처리한다.
SEL.addEventListener('click', ev => {{
  const drill = ev.target.closest('[data-go-m]');
  if (drill) {{ setView(drill.dataset.goM); return; }}
  const row = ev.target.closest('[data-m]');
  if (row) {{ panelModule(row.dataset.m); return; }}
  const f = ev.target.closest('[data-f]');
  if (f) {{
    const src = FN.find(x => x.data.label === f.dataset.f);
    if (src && view !== src.data.module) setView(src.data.module);
    focusFn(f.dataset.f);
  }}
}});

// 범례 클릭 = 그 모듈의 함수 뷰로 내려간다.
document.getElementById('legend').addEventListener('click', ev => {{
  const chip = ev.target.closest('[data-m]');
  if (!chip) return;
  tab(true);
  setView(view === chip.dataset.m ? null : chip.dataset.m);
}});

setView(null);

mermaid.initialize({{startOnLoad: true, theme: 'dark',
  themeVariables: {{fontSize: '13px', fontFamily: 'Noto Sans KR, sans-serif'}},
  flowchart: {{curve: 'basis', nodeSpacing: 30, rankSpacing: 40}}}});

// 탭 전환. 그래프는 숨겨진 채로 레이아웃되면 크기가 0 이라 처음 보일 때 다시 맞춘다.
let fitted = false;
function tab(showGraph) {{
  document.getElementById('ov').classList.toggle('hide', showGraph);
  document.getElementById('cy').classList.toggle('hide', !showGraph);
  document.getElementById('tab-ov').classList.toggle('on', !showGraph);
  document.getElementById('tab-gr').classList.toggle('on', showGraph);
  if (showGraph) {{ cy.resize(); if (!fitted) {{ cy.fit(30); fitted = true; }} }}
}}
document.getElementById('tab-ov').onclick = () => tab(false);
document.getElementById('tab-gr').onclick = () => tab(true);

// 개요의 심볼 칩을 누르면 그래프로 넘어가 그 블록을 선택한다.
document.getElementById('ov').addEventListener('click', ev => {{
  const chip = ev.target.closest('[data-go]');
  if (!chip) return;
  const src = FN.find(e => e.data.label === chip.dataset.go);
  if (!src) return;
  tab(true);
  if (view !== src.data.module) setView(src.data.module);
  focusFn(chip.dataset.go);
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
    m, funcs = scan_audit_calls()
    hits = {k: v for k, v in m.items() if "LIMIT_BREACH" in v}
    assert hits, "risk/limits.py 의 LIMIT_BREACH 를 못 찾았다 — ast 스캔이 깨졌다"
    assert any("limits.py" in f for f, _ in hits), f"엉뚱한 파일에서 찾았다: {hits}"
    assert all(f.startswith("src/trading/") for f, _ in m), "경로 형식이 code-graph 와 안 맞는다"
    # 설명이 낡았는지 판정하는 근거 — STAGES 심볼이 소스에 실재하는지 여기서 본다.
    missing = {s for _, _, syms in STAGES for s in syms if s not in funcs}
    assert not missing, f"STAGES 설명이 낡았다 — 소스에 없는 심볼: {sorted(missing)}"

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
    audit_map, all_funcs = scan_audit_calls()
    event_counts, orders = fetch_outcomes()
    elements = to_cytoscape(nodes, edges, audit_map, event_counts)
    n_files = len({n["file"] for n in nodes.values() if n["file"]})
    sys.stdout.write(render(elements, orders, len(nodes), n_files, all_funcs))


if __name__ == "__main__":
    main()
