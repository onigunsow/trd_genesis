"""2026-09-17 주간 로그 감사에서 나온 결함 고정.

① 결정·리스크 입력의 daily_order_count=0 / daily_pnl_pct=0.0 하드코딩(7곳)
   9/15 11:47 리스크가 "오늘 매매 0/10" 이라며 HOLD 했는데 실제 주문은 이미 10건.
② 매도 쪽 '오늘 이미 매도한 종목' 부재
   9/15 316140 을 같은 일봉 RSI 73.7 로 15분마다 6번 매도(전량), 일일 주문 6건 소진.
③ 프롬프트 섹터 한도 40% 리터럴 vs 체제 반영 35%
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from trading.personas.orchestrator import _daily_counters
from trading.risk import limits

# ---------------------------------------------------------------------------
# ① 실측 주입
# ---------------------------------------------------------------------------


def test_daily_counters_use_the_same_source_as_the_hard_limit() -> None:
    with (
        patch("trading.risk.limits.daily_order_count_today", return_value=10),
        patch("trading.risk.limits.daily_pnl_pct", return_value=-0.0123),
    ):
        out = _daily_counters(10_000_000)

    assert out == {"daily_order_count": 10, "daily_pnl_pct": -1.23}


def test_daily_counters_report_unknown_not_zero_on_failure() -> None:
    """조회 실패를 0 으로 채우면 '모름'이 '주문 없음'으로 둔갑한다."""
    with (
        patch("trading.risk.limits.daily_order_count_today", side_effect=RuntimeError("db")),
        patch("trading.risk.limits.daily_pnl_pct", side_effect=RuntimeError("db")),
    ):
        out = _daily_counters(10_000_000)

    assert out == {"daily_order_count": None, "daily_pnl_pct": None}


def test_no_hardcoded_zero_counters_left_in_orchestrator() -> None:
    import inspect

    from trading.personas import orchestrator

    src = inspect.getsource(orchestrator)
    assert '"daily_order_count": 0' not in src
    assert '"daily_pnl_pct": 0.0' not in src


# ---------------------------------------------------------------------------
# ② 당일 매도 이력
# ---------------------------------------------------------------------------


class _SpyCursor:
    def __init__(self) -> None:
        self.params: Any = None

    def execute(self, _sql: str, params: Any = None) -> None:
        self.params = params

    def fetchall(self) -> list[dict[str, Any]]:
        return [{"ticker": "316140", "n": 6}]

    def __enter__(self) -> _SpyCursor:
        return self

    def __exit__(self, *_: Any) -> None:
        pass


def _patched_conn(cur: _SpyCursor):
    class _Conn:
        def cursor(self) -> _SpyCursor:
            return cur

        def __enter__(self) -> _Conn:
            return self

        def __exit__(self, *_: Any) -> None:
            pass

    @contextmanager
    def _c(*_a: Any, **_k: Any):
        yield _Conn()

    return patch("trading.risk.limits.connection", side_effect=_c)


def test_sold_today_queries_sell_side() -> None:
    cur = _SpyCursor()
    with _patched_conn(cur):
        assert limits.tickers_sold_today() == {"316140": 6}
    assert cur.params == ("sell",)


def test_bought_today_still_queries_buy_side() -> None:
    """공용 헬퍼로 옮긴 뒤에도 매수 쪽 의미가 그대로다."""
    cur = _SpyCursor()
    with _patched_conn(cur):
        limits.tickers_bought_today()
    assert cur.params == ("buy",)


# ---------------------------------------------------------------------------
# 프롬프트 렌더
# ---------------------------------------------------------------------------


def _render(name: str, **extra: Any) -> str:
    from trading.personas.base import render_prompt

    ctx: dict[str, Any] = {
        "today": "2026-09-17",
        "cycle_kind": "intraday",
        "event_trigger": None,
        "car_context": None,
        "dynamic_thresholds_enabled": False,
        "assets": {},
    }
    ctx.update(extra)
    return render_prompt(name, **ctx)


def test_decision_prompt_lists_todays_sells_and_forbids_same_evidence_resell() -> None:
    p = _render("decision.jinja", sold_today={"316140": 6})

    assert "오늘 이미 매도한 종목" in p
    assert "316140: 6회" in p
    assert "장중에 값이 바뀌지 않는다" in p


def test_decision_prompt_shows_real_count_and_unknown_pnl() -> None:
    p = _render("decision.jinja", daily_order_count=10, daily_pnl_pct=None,
                risk_daily_order_count_max=10)

    assert "10 / 10건" in p
    assert "모름(조회 실패)" in p


def test_decision_prompt_sector_cap_follows_regime_value() -> None:
    p = _render("decision.jinja", regime_sector_cap_pct=35.0)

    assert "자본의 35.0% 초과 금지" in p
    assert "자본의 40% 초과 금지" not in p
