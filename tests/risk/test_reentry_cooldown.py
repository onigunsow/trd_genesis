"""손실 청산 후 재진입 쿨다운 (2026-08-15).

65건 왕복 분해: 064350 을 8번 사서 8번 다 손절(-139,096원), 071050 4번
(-54,500원). 손절 며칠 뒤 같은 종목을 다시 사서 같은 함정에 빠지는 패턴이
손실의 큰 몫이었다. check_pre_order 가 REENTRY_COOLDOWN_DAYS 안의 재매수를
막는다. 익절/회전 청산은 대상이 아니다.

DB 헬퍼(days_since_loss_exit)는 패치한다 — SQL 자체는 라이브 원장으로
실측했다(012330 손절 19일 전, 316140 16일 전, 익절 종목 068270 은 None).
"""

from __future__ import annotations

from unittest.mock import patch

from trading import config
from trading.risk import limits


def _buy(*, since_loss: int | None, ticker: str = "064350") -> limits.LimitCheck:
    with (
        patch.object(limits, "daily_pnl_pct", return_value=0.0),
        patch.object(limits, "daily_order_count_today", return_value=0),
        patch.object(limits, "buy_count_today", return_value=0),
        patch.object(limits, "days_since_loss_exit", return_value=since_loss),
    ):
        return limits.check_pre_order(
            side="buy", ticker=ticker, qty=1, ref_price=1,
            total_assets=10_000_000, holdings=[], mode="paper", market="KOSPI",
        )


def _breach_prefixes(chk: limits.LimitCheck) -> set[str]:
    return {b.split(":", 1)[0] for b in chk.breaches}


class TestReentryCooldown:
    def test_recent_loss_exit_blocks_buy(self):
        """손절 며칠 뒤 재매수 → 차단. 임계는 튜닝 값이라 상수 상대값으로."""
        chk = _buy(since_loss=max(0, config.REENTRY_COOLDOWN_DAYS - 1))
        assert "reentry_cooldown" in _breach_prefixes(chk)
        assert not chk.passed

    def test_loss_exit_at_boundary_allows_buy(self):
        """정확히 쿨다운 일수가 지나면 허용(< 비교)."""
        chk = _buy(since_loss=config.REENTRY_COOLDOWN_DAYS)
        assert "reentry_cooldown" not in _breach_prefixes(chk)

    def test_no_loss_exit_allows_buy(self):
        """손절 이력이 없으면(None) — 익절/회전으로 나간 종목 포함 — 허용."""
        chk = _buy(since_loss=None)
        assert "reentry_cooldown" not in _breach_prefixes(chk)
        assert chk.passed

    def test_sell_never_subject_to_cooldown(self):
        """쿨다운은 BUY 에만 — 손절 직후라도 SELL(추가 청산)은 막지 않는다."""
        with (
            patch.object(limits, "daily_pnl_pct", return_value=0.0),
            patch.object(limits, "daily_order_count_today", return_value=0),
            patch.object(limits, "days_since_loss_exit", return_value=0) as m,
        ):
            chk = limits.check_pre_order(
                side="sell", ticker="064350", qty=1, ref_price=1,
                total_assets=10_000_000, holdings=[], mode="paper", market="KOSPI",
            )
        assert "reentry_cooldown" not in _breach_prefixes(chk)
        assert m.call_count == 0, "SELL 경로는 쿨다운 조회조차 하지 않는다"

    def test_cooldown_disabled_when_zero(self, monkeypatch):
        """REENTRY_COOLDOWN_DAYS=0 이면 비활성 — 조회도 안 한다."""
        monkeypatch.setattr(limits, "REENTRY_COOLDOWN_DAYS", 0)
        with (
            patch.object(limits, "daily_pnl_pct", return_value=0.0),
            patch.object(limits, "daily_order_count_today", return_value=0),
            patch.object(limits, "buy_count_today", return_value=0),
            patch.object(limits, "days_since_loss_exit", return_value=0) as m,
        ):
            chk = limits.check_pre_order(
                side="buy", ticker="064350", qty=1, ref_price=1,
                total_assets=10_000_000, holdings=[], mode="paper", market="KOSPI",
            )
        assert "reentry_cooldown" not in _breach_prefixes(chk)
        assert m.call_count == 0

    def test_breach_message_names_ticker_and_days(self):
        chk = _buy(since_loss=3, ticker="071050")
        msg = next(b for b in chk.breaches if b.startswith("reentry_cooldown"))
        assert "071050" in msg
        assert "3일" in msg
        assert str(config.REENTRY_COOLDOWN_DAYS) in msg
