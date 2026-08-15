"""시장별 정규장 세션 판정 + 주문 시각 가드 테스트.

재현 대상(2026-08-15 실측):
- 07:32~07:36 매수 → 40570000 장시작전 (pre_market 사이클, 12건+)
- 15:33 매수 → 40580000 장종료 (intraday 크론 hour="9-15" 가 15:30 발사)
- 토요일 매수 → 40100000 영업일 아님
"""

from __future__ import annotations

import zoneinfo
from datetime import datetime, time
from unittest.mock import MagicMock, patch

import pytest

from trading.data.market_session import (
    intraday_cron_slots,
    is_session_open,
    session_bounds,
)

KST = zoneinfo.ZoneInfo("Asia/Seoul")


def _kst(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=KST)


# 2026-08-14 는 금요일 정상 거래일 (실제 사고 당일)
_TRADING_DAY = (2026, 8, 14)


class TestSessionBounds:
    def test_kr_bounds_from_yaml(self):
        opened, closed, tz = session_bounds("KR")
        assert opened == time(9, 0)
        assert closed == time(15, 30)
        assert tz == "Asia/Seoul"

    def test_us_bounds_present_for_multimarket(self):
        """미국장 대비 — 시장 종속 값은 코드가 아니라 YAML 에 있어야 한다."""
        opened, closed, tz = session_bounds("US")
        assert opened == time(9, 30)
        assert closed == time(16, 0)
        assert tz == "America/New_York"

    def test_unknown_market_returns_none(self):
        assert session_bounds("ZZ") is None


class TestIsSessionOpen:
    @pytest.mark.parametrize(("hh", "mm"), [(9, 0), (9, 1), (12, 35), (15, 29)])
    def test_inside_session_is_open(self, hh, mm):
        assert is_session_open(_kst(*_TRADING_DAY, hh, mm), market="KR")

    def test_premarket_0733_is_closed(self):
        """07:33 pre_market 사이클 매수 재현 — 12건+ 거부의 원인."""
        assert not is_session_open(_kst(*_TRADING_DAY, 7, 33), market="KR")

    def test_close_tick_is_closed(self):
        """15:30 정각은 이미 마감 — 반개구간 [open, close)."""
        assert not is_session_open(_kst(*_TRADING_DAY, 15, 30), market="KR")

    def test_1533_after_close_is_closed(self):
        """8/14 15:33 매수 재현 — 크론이 15:30 에 발사해 3분 뒤 주문."""
        assert not is_session_open(_kst(*_TRADING_DAY, 15, 33), market="KR")

    def test_saturday_is_closed(self):
        """2026-08-08 토요일 매수 재현 (영업일 아님)."""
        assert not is_session_open(_kst(2026, 8, 8, 10, 0), market="KR")

    def test_holiday_is_closed(self):
        """2026-08-15 광복절 — 평일이어도 휴장."""
        assert not is_session_open(_kst(2026, 8, 15, 10, 0), market="KR")

    def test_naive_datetime_treated_as_market_local(self):
        assert is_session_open(datetime(2026, 8, 14, 10, 0), market="KR")
        assert not is_session_open(datetime(2026, 8, 14, 7, 33), market="KR")

    def test_unknown_market_fails_open(self):
        """판정 불가는 차단 근거가 아니다 — 설정 사고가 손절까지 막으면 안 된다."""
        assert is_session_open(_kst(*_TRADING_DAY, 3, 0), market="ZZ")


class TestIntradayCronSlots:
    def test_last_kr_15min_slot_is_1515(self):
        """마감 정각(15:30)은 슬롯에서 빠져야 한다 — 8/14 사고의 발사 시각."""
        slots = intraday_cron_slots(15, "KR")
        assert slots[0] == time(9, 0)
        assert slots[-1] == time(15, 15)
        assert time(15, 30) not in slots
        assert time(15, 45) not in slots

    def test_5min_slots_end_at_1525(self):
        slots = intraday_cron_slots(5, "KR")
        assert slots[-1] == time(15, 25)
        assert time(15, 30) not in slots

    def test_unknown_market_returns_empty(self):
        assert intraday_cron_slots(15, "ZZ") == []


class TestSubmitOrderGuard:
    """가드가 submit_order 단일 관문에서 실제로 주문을 끊는지."""

    def test_order_blocked_outside_session_before_kis_post(self):
        from trading.kis import order

        client = MagicMock()
        client.mode = MagicMock(value="paper")

        audit_sink = MagicMock()
        with (
            patch.object(order, "audit", audit_sink),
            patch.object(order, "_check_live_gate", MagicMock()),
            patch("trading.data.market_session.is_session_open", return_value=False),
            pytest.raises(order.MarketSessionClosedError),
        ):
            order.submit_order(client, ticker="096770", qty=1, side="buy")

        # KIS 왕복이 아예 없어야 한다 (거부 알림·수수료 추정도 발생 안 함).
        assert client.post.call_count == 0
        events = [c.args[0] for c in audit_sink.call_args_list if c.args]
        assert "ORDER_BLOCKED_OUTSIDE_SESSION" in events

    def test_order_proceeds_when_session_open(self):
        """세션이 열려 있으면 가드는 no-op — 정상 주문을 막지 않는다."""
        from trading.kis import order

        client = MagicMock()
        client.mode = MagicMock(value="paper")
        with (
            patch.object(order, "audit", MagicMock()),
            patch.object(order, "_check_live_gate", MagicMock()),
            patch("trading.data.market_session.is_session_open", return_value=True),
        ):
            order._check_market_session(
                client, ticker="096770", side="buy", qty=1,
                persona_decision_id=None,
            )  # raise 하지 않으면 통과
