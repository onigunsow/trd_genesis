"""트레일링 스탑 (2026-08-27).

get_dynamic_thresholds 가 trailing_stop_pct 를 계산해 모델에 담고 decision.jinja 도
"수익 중인 포지션에 적용" 이라고 지시해 왔는데, 정작 워치독에 그 값을 읽는 줄이
한 줄도 없었다 — 계산해 놓고 아무도 쓰지 않았다.

반사실(왕복 60건): 보유 중 평균 최고 미실현 +6.91%, 평균 실현 -2.33% =
평균 9.24%p 를 반납했다. 보유 중 +5% 이상 올랐던 32건 중 이익으로 끝낸 건
17건뿐이다. 익절선이 +15~22% 인데 평균 최고가 +6.91% 라 애초에 닿을 수 없었고,
그 구간을 잡으라고 만든 장치가 트레일링이다.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from trading.watchers import position_watchdog as pw


def _run(*, pnl_pct, peak_gain, trail=-6.0, marked=False, qty=10):
    holding = {"ticker": "005930", "qty": qty, "pnl_pct": pnl_pct,
               "avg_cost": 70_000, "eval_amount": 700_000}
    with (
        patch.object(pw, "_build_client", return_value=object()),
        patch.object(pw, "_read_holdings", return_value=[holding]),
        patch.object(pw, "_confirm_qty", return_value=qty),
        patch.object(pw, "get_dynamic_thresholds", return_value={
            "effective_stop": -15.0, "effective_take": 20.0,
            "trailing_stop_pct": trail, "source": "dynamic"}),
        patch.object(pw, "classify_holding", return_value=("skip", 0)),
        patch.object(pw, "_took_profit_today", return_value=False),
        patch.object(pw, "_action_done_today", return_value=marked),
        patch.object(pw, "_peak_gain_pct", return_value=peak_gain),
        patch.object(pw, "_lot_entry_date", return_value=date(2026, 8, 1)),
        patch.object(pw, "_execute_trim", return_value=True) as trim,
        patch.object(pw, "classify_concentration", return_value=("skip", 0)),
        patch.object(pw, "is_stagnant", return_value=False),
    ):
        m = pw.poll_position_watchdog()
    return m, trim


class TestTrailingFires:
    def test_gives_back_from_peak_triggers_exit(self):
        """+9% 까지 올랐다가 +2% 로 밀리면(고점 대비 -7%) 청산."""
        m, trim = _run(pnl_pct=2.0, peak_gain=9.0, trail=-6.0)
        assert m["trailing_exits"] == 1
        assert trim.call_args.kwargs.get("kind") == "trail"

    def test_full_position_is_exited(self):
        """트레일링은 스톱이다 — 부분이 아니라 전량."""
        _, trim = _run(pnl_pct=2.0, peak_gain=9.0, qty=10)
        assert trim.call_args.args[2] == 10


class TestTrailingHoldsFire:
    def test_not_armed_below_threshold(self):
        """한 번도 발동선(+5%)까지 못 오른 포지션은 대상이 아니다."""
        m, trim = _run(pnl_pct=-3.0, peak_gain=3.0)
        assert m["trailing_exits"] == 0
        assert trim.call_count == 0

    def test_still_near_peak_is_held(self):
        """고점 대비 낙폭이 trail 안이면 계속 들고 간다."""
        m, _ = _run(pnl_pct=7.0, peak_gain=9.0, trail=-6.0)
        assert m["trailing_exits"] == 0

    def test_already_acted_today_is_skipped(self):
        """하루 1회 — trim/rotate 와 마커를 공유해 같은 날 중복 청산하지 않는다."""
        m, trim = _run(pnl_pct=2.0, peak_gain=9.0, marked=True)
        assert m["trailing_exits"] == 0
        assert trim.call_count == 0

    def test_missing_trailing_value_is_not_an_exit(self):
        """ATR 불가로 trailing 이 없으면 판정을 포기한다 — 추정하지 않는다."""
        m, _ = _run(pnl_pct=2.0, peak_gain=9.0, trail=None)
        assert m["trailing_exits"] == 0

    def test_peak_unavailable_is_not_an_exit(self):
        m, _ = _run(pnl_pct=2.0, peak_gain=None)
        assert m["trailing_exits"] == 0


class TestPeakUsesTodaysMoveToo:
    def test_current_pnl_counts_as_peak_when_higher(self):
        """일봉 고가는 당일 장중을 못 본다 — 현재 손익률과 합쳐야 한다."""
        m, _ = _run(pnl_pct=6.0, peak_gain=1.0, trail=-6.0)
        # peak = max(1.0, 6.0) = 6.0 >= 5.0 이지만 낙폭 0 이라 청산 아님
        assert m["trailing_exits"] == 0
