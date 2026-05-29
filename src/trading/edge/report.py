"""엣지 리포트 조립 — roundtrips → analytics/benchmark/confidence/time-weighted → scorecard.

기본은 **KIS 호출 없이** 기존 DB 데이터만 사용한다(``include_unrealized=True`` 일 때만
balance() 를 호출해 미실현 평가손익을 병기). 텔레그램 전송은 ``system_briefing`` 재사용.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from trading.db.session import connection
from trading.edge import analytics as _an
from trading.edge import benchmark as _bm
from trading.edge import confidence as _conf
from trading.edge import roundtrips as _rt
from trading.edge import scorecard as _sc

LOG = logging.getLogger(__name__)


def load_equity_snapshots(days: int | None = None) -> list[tuple[date, float]]:
    """daily_equity_snapshot → [(trading_day, total_assets)] 오름차순."""
    sql = "SELECT trading_day, total_assets FROM daily_equity_snapshot"
    params: list[Any] = []
    if days is not None:
        sql += " WHERE trading_day >= (CURRENT_DATE - (%s || ' days')::INTERVAL)"
        params.append(str(int(days)))
    sql += " ORDER BY trading_day"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [(r["trading_day"], float(r["total_assets"])) for r in cur.fetchall()]


def _maybe_balance(include_unrealized: bool) -> dict[str, Any] | None:
    if not include_unrealized:
        return None
    try:
        from trading.config import get_settings
        from trading.kis.account import balance
        from trading.kis.client import KisClient

        return balance(KisClient(get_settings().trading_mode))
    except Exception:  # noqa: BLE001 — 미실현은 부가정보; 실패해도 리포트는 진행
        LOG.warning("edge-report: balance() 실패 — 미실현 생략", exc_info=True)
        return None


def _time_weighted_text(tw: _an.TimeWeighted) -> str | None:
    if not tw.available:
        if tw.n_days:
            return (
                "【 시간가중 지표 】\n"
                f"  일별 스냅샷 {tw.n_days}행 < {_an.MIN_SNAPSHOT_ROWS}행 — 캘린더 시간가중 "
                "지표 보류(이벤트시간 지표만 신뢰). 스냅샷이 더 쌓이면 자동 활성화."
            )
        return None
    return (
        "【 시간가중 지표 (캘린더, 일별 자산 스냅샷) 】\n"
        f"  스냅샷 {tw.n_days}일  ({tw.start_value:,.0f}원 → {tw.end_value:,.0f}원, "
        f"{tw.total_return_pct:+.2f}%)\n"
        f"  CAGR {tw.cagr*100:+.1f}%  /  MDD {tw.mdd*100:.1f}%  /  Sharpe {tw.sharpe:.2f}"
    )


def generate(
    days: int | None = None,
    *,
    include_unrealized: bool = False,
    include_confidence: bool = True,
) -> str:
    """엣지 리포트 텍스트 생성(부작용 없음)."""
    rt_result = _rt.compute_roundtrips(days)
    bal = _maybe_balance(include_unrealized)

    analytics = _an.from_result(rt_result, balance=bal)
    benchmark = _bm.compute(rt_result.roundtrips)
    card = _sc.decide(analytics, benchmark)

    confidence_text = None
    if include_confidence and rt_result.roundtrips:
        confidence_text = _conf.render(_conf.analyze(rt_result.roundtrips))

    snapshots = load_equity_snapshots(days)
    tw = _an.time_weighted_metrics(snapshots)
    tw_text = _time_weighted_text(tw)

    return _sc.render(
        analytics,
        benchmark,
        card,
        days=days,
        confidence_text=confidence_text,
        time_weighted_text=tw_text,
        time_weighted=tw.available,
    )


def generate_and_send(
    days: int | None = None,
    *,
    telegram: bool = False,
    include_unrealized: bool = False,
) -> str:
    """리포트 생성 + (옵션) 텔레그램 전송. 텍스트 반환."""
    text = generate(days, include_unrealized=include_unrealized)
    if telegram:
        try:
            from trading.alerts.telegram import system_briefing

            system_briefing("엣지 리포트", text)
        except Exception:  # noqa: BLE001
            LOG.exception("edge-report: 텔레그램 전송 실패")
    return text
