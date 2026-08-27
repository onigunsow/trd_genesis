"""진입 바닥선 강제 (2026-08-27).

프롬프트는 "confidence 가 0.50 이하면 진입 자체를 하지 않는다" 를 하드 룰로
선언해 왔는데 코드에 그걸 읽는 줄이 없었다 — risk/ 전체와 오케스트레이터에서
confidence 참조 0건. 그 결과 페르소나가 0.45 라고 써놓고 그 종목을 산 게 8건이다.

자기가 내놓은 숫자를 자기가 어기면 confidence 필드 자체가 무의미해진다.
임계값의 최적성은 별개 문제다(옛 정의 표본 n=8 로는 판정 불가) — 계약이
지켜지는지가 먼저다.
"""

from __future__ import annotations

from unittest.mock import patch

from trading.risk import limits


def _check(**kw):
    base = dict(
        side="buy", ticker="005930", qty=1, ref_price=70_000,
        total_assets=10_000_000, holdings=[], mode="paper",
    )
    base.update(kw)
    with (
        patch.object(limits, "daily_pnl_pct", return_value=0.0),
        patch.object(limits, "daily_order_count_today", return_value=0),
        patch.object(limits, "buy_count_today", return_value=0),
        patch.object(limits, "days_since_loss_exit", return_value=None),
    ):
        return limits.check_pre_order(**base)


def _floor_breaches(chk):
    return [b for b in chk.breaches if b.startswith("confidence_floor")]


class TestBuyIsBlockedBelowFloor:
    def test_at_or_below_floor_is_blocked(self):
        for c in (0.45, 0.50):
            chk = _check(confidence=c)
            assert _floor_breaches(chk), f"conf {c} 는 막혀야 한다"
            assert not chk.passed

    def test_above_floor_passes_the_gate(self):
        chk = _check(confidence=0.51)
        assert not _floor_breaches(chk)

    def test_breach_message_names_the_threshold(self):
        """운영자가 로그만 보고 왜 막혔는지 알 수 있어야 한다."""
        msg = _floor_breaches(_check(confidence=0.40))[0]
        assert "0.40" in msg and f"{limits.DECISION_CONFIDENCE_FLOOR:.2f}" in msg


class TestSellIsNeverBlocked:
    def test_low_confidence_sell_passes(self):
        """위험을 줄이는 매도가 한도에 걸려 봉쇄되면 한도가 손실을 키운다."""
        chk = _check(side="sell", confidence=0.10,
                     holdings=[{"ticker": "005930", "qty": 10, "pnl_pct": -5.0,
                                "eval_amount": 700_000, "avg_cost": 70_000}])
        assert not _floor_breaches(chk)


class TestUnknownConfidenceIsNotBlocked:
    def test_none_passes(self):
        """워치독 매도 등 페르소나 밖 경로는 confidence 가 없다 — 모르는 것은 막지 않는다."""
        assert not _floor_breaches(_check(confidence=None))

    def test_omitted_argument_passes(self):
        assert not _floor_breaches(_check())


class TestSignalConfidenceExtraction:
    def test_missing_and_malformed_become_none(self):
        from trading.personas.orchestrator import _sig_confidence
        assert _sig_confidence({}) is None
        assert _sig_confidence({"confidence": None}) is None
        assert _sig_confidence({"confidence": "n/a"}) is None
        assert _sig_confidence({"confidence": "0.62"}) == 0.62
        assert _sig_confidence({"confidence": 0.45}) == 0.45
