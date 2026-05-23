"""SPEC-TRADING-026 — execution-gate softening for 단기과열(55).

``check_pre_order_safety`` previously hard-blocked ANY non-normal stat_cls.
SPEC-026 keeps the hard block for genuine risk states (관리 51 / 투자위험 52 /
투자경고 53 / 거래정지 54) but treats 단기과열(55) as a tradeable-but-cautioned
state: the order is allowed (``passed=True``) and flagged ``overheated=True`` so
the orchestrator can size-cap it and force a limit order (single-price auction).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from trading.risk import market_safety as ms


def _quote(stat_cls: str) -> dict[str, Any]:
    return {
        "ticker": "005930",
        "price": 70_000,
        "stat_cls": stat_cls,
        "is_normal": stat_cls == "00",
        "near_upper_limit": False,
        "near_lower_limit": False,
        "upper_limit": 91_000,
        "lower_limit": 49_000,
    }


def _balance() -> dict[str, Any]:
    return {
        "buyable_effective": 100_000_000,
        "buyable": 100_000_000,
        "nrcvb_buy_amt": 0,
        "total_assets": 100_000_000,
    }


def _check(stat_cls: str, side: str = "buy"):
    with (
        patch.object(ms, "current_price", return_value=_quote(stat_cls)),
        patch.object(ms, "balance", return_value=_balance()),
    ):
        return ms.check_pre_order_safety(
            None, ticker="005930", side=side, qty=10, notional=700_000
        )


class TestOverheatExecutionGate:
    def test_overheated_55_allowed_and_flagged(self):
        res = _check("55")
        assert res.passed is True, "단기과열(55) must NOT hard-block"
        assert res.overheated is True
        assert not res.blockers

    def test_hard_block_51_still_blocks(self):
        res = _check("51")
        assert res.passed is False
        assert res.overheated is False
        assert any("stat_cls" in b for b in res.blockers)

    def test_hard_block_54_still_blocks(self):
        res = _check("54")
        assert res.passed is False
        assert any("stat_cls" in b for b in res.blockers)

    def test_normal_passes_not_overheated(self):
        res = _check("00")
        assert res.passed is True
        assert res.overheated is False
