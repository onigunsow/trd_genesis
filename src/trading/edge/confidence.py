"""Phase 2 — LLM 확신도 엣지 + 위험 게이트 override 분석.

"LLM 이 확신한 매매가 진짜 더 벌었나" 가 엣지 유무의 핵심 질문이다. confidence 구간별 성적,
confidence↔수익률 상관(Pearson/Spearman), 그리고 위험 게이트가 가치 있는지 직접 증거
(HOLD/REJECT 였는데 체결된 거래 vs APPROVE 거래의 성적 비교)를 낸다.

상관계수는 stdlib 만으로 계산(scipy 비의존). n<3 가드.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Sequence

from trading.edge.roundtrips import RoundTrip

# (label, lo_inclusive, hi_exclusive)
_BUCKETS = [
    ("<0.50", 0.0, 0.5),
    ("0.50–0.70", 0.5, 0.7),
    ("0.70–0.85", 0.7, 0.85),
    (">0.85", 0.85, 1.0001),
]


@dataclass
class Bucket:
    label: str
    n: int = 0
    win_rate: float = 0.0
    avg_return_pct: float = 0.0
    expectancy: float = 0.0     # 거래당 평균 순손익


@dataclass
class GroupStat:
    label: str
    n: int = 0
    win_rate: float = 0.0
    expectancy: float = 0.0


@dataclass
class ConfidenceReport:
    buckets: list[Bucket] = field(default_factory=list)
    none_count: int = 0          # confidence 없는(수동/M2) 거래 수
    n_with_conf: int = 0
    pearson: float | None = None
    spearman: float | None = None
    # 위험 게이트 override
    approve: GroupStat | None = None
    overridden: GroupStat | None = None  # HOLD/REJECT 였는데 체결
    none_verdict_count: int = 0


def _group_stat(label: str, rts: Sequence[RoundTrip]) -> GroupStat:
    if not rts:
        return GroupStat(label=label)
    wins = sum(1 for r in rts if r.is_win)
    nets = [r.net_pnl for r in rts]
    return GroupStat(
        label=label,
        n=len(rts),
        win_rate=wins / len(rts),
        expectancy=statistics.mean(nets),
    )


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _rank(values: list[float]) -> list[float]:
    """평균 순위(동점 보정)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    return _pearson(_rank(xs), _rank(ys))


def analyze(roundtrips: Sequence[RoundTrip]) -> ConfidenceReport:
    rep = ConfidenceReport()

    with_conf = [r for r in roundtrips if r.confidence is not None]
    rep.none_count = len(roundtrips) - len(with_conf)
    rep.n_with_conf = len(with_conf)

    for label, lo, hi in _BUCKETS:
        members = [r for r in with_conf if lo <= float(r.confidence) < hi]
        b = Bucket(label=label, n=len(members))
        if members:
            b.win_rate = sum(1 for r in members if r.is_win) / len(members)
            b.avg_return_pct = statistics.mean([r.return_pct for r in members])
            b.expectancy = statistics.mean([r.net_pnl for r in members])
        rep.buckets.append(b)

    if len(with_conf) >= 3:
        xs = [float(r.confidence) for r in with_conf]
        ys = [r.net_pnl for r in with_conf]
        rep.pearson = _pearson(xs, ys)
        rep.spearman = _spearman(xs, ys)

    # 위험 게이트 override.
    approve = [r for r in roundtrips if r.verdict == "APPROVE"]
    overridden = [r for r in roundtrips if r.verdict in ("HOLD", "REJECT")]
    rep.none_verdict_count = sum(1 for r in roundtrips if r.verdict is None)
    rep.approve = _group_stat("APPROVE", approve)
    rep.overridden = _group_stat("HOLD/REJECT(override 체결)", overridden)
    return rep


def _won(x: float) -> str:
    return f"{x:,.0f}원"


def render(rep: ConfidenceReport) -> str:
    out = ["【 LLM 확신도 엣지 】"]
    if rep.n_with_conf == 0:
        out.append("  confidence 연결된 거래 없음(수동/M2 주문). 분석 불가.")
        return "\n".join(out)

    out.append(f"  (confidence 연결 {rep.n_with_conf}건, 미연결 {rep.none_count}건)")
    out.append("  구간별  n   승률    평균수익률   기대값")
    for b in rep.buckets:
        if b.n == 0:
            out.append(f"   {b.label:<10} {b.n:>3}   —")
            continue
        out.append(
            f"   {b.label:<10} {b.n:>3}  {b.win_rate*100:5.1f}%  "
            f"{b.avg_return_pct:+7.2f}%  {_won(b.expectancy)}"
        )

    def _corr(v: float | None) -> str:
        return "n<3(불가)" if v is None else f"{v:+.3f}"

    out.append(
        f"  상관(confidence↔순손익): Pearson {_corr(rep.pearson)}, Spearman {_corr(rep.spearman)}"
    )
    out.append("  → 양(+)이면 확신도가 높을수록 더 벌었다는 신호. 0 근처/음수면 확신도가 무의미.")

    out.append("  【 위험 게이트 override 분석 】")
    for g in (rep.approve, rep.overridden):
        if g and g.n:
            out.append(
                f"   {g.label}: n={g.n}, 승률 {g.win_rate*100:.1f}%, 기대값 {_won(g.expectancy)}"
            )
    if rep.approve and rep.overridden and rep.approve.n and rep.overridden.n:
        if rep.overridden.expectancy < rep.approve.expectancy:
            out.append("   → override(HOLD/REJECT 무시 체결)가 더 나빴음: 위험 게이트가 가치 있음.")
        else:
            out.append("   → override가 더 낫거나 비슷: 위험 게이트의 가치 불분명(표본 주의).")
    elif rep.none_verdict_count and not (rep.approve.n if rep.approve else 0):
        out.append(f"   verdict 연결 없음({rep.none_verdict_count}건) — override 분석 불가.")
    return "\n".join(out)
