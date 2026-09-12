"""SPEC-TRADING-024 REQ-024-2 — ATR-based price threshold watcher tests.

@MX:SPEC: SPEC-TRADING-024
"""

from __future__ import annotations

from unittest.mock import patch


def _atr_payload(atr_14: float, close_price: float) -> dict:
    """Mimic compute_atr() return shape."""
    return {
        "atr_14": atr_14,
        "atr_pct": (atr_14 / close_price) * 100.0,
        "close_price": close_price,
        "date": "2026-05-15",
    }


class TestPriceThresholdPoll:
    """REQ-024-2: when price change exceeds 1.5x ATR, fire event."""

    def test_price_change_below_threshold_no_event(self):
        """ATR=1000 on 100000 close => threshold = 1.5%. -1% move = no event."""
        from trading.watchers import price_threshold

        target_tickers = ["005930"]
        # current_price 0.5% below last seen
        kis_quote = {"price": 99500, "change_pct": -0.5}

        with (
            patch.object(
                price_threshold,
                "_get_target_tickers",
                return_value=target_tickers,
            ),
            patch.object(
                price_threshold, "compute_atr", return_value=_atr_payload(1000.0, 100000.0)
            ),
            patch.object(
                price_threshold,
                "_get_kis_quote",
                return_value=kis_quote,
            ),
            patch.object(price_threshold, "_fire_trigger_event") as fire,
        ):
            metrics = price_threshold.poll_price_threshold()

        assert fire.call_count == 0
        assert metrics["checked"] == 1
        assert metrics["fired"] == 0

    def test_price_change_above_atr_threshold_fires_event(self):
        """-3% move on threshold 1.5% (ATR-based) fires event."""
        from trading.watchers import price_threshold

        target_tickers = ["005930"]
        # ATR=1000 on 100000 close => 1.5x ATR ratio = 1.5%. -3% > 1.5% => fire.
        kis_quote = {"price": 97000, "change_pct": -3.0}

        with (
            patch.object(
                price_threshold,
                "_get_target_tickers",
                return_value=target_tickers,
            ),
            patch.object(
                price_threshold, "compute_atr", return_value=_atr_payload(1000.0, 100000.0)
            ),
            patch.object(
                price_threshold,
                "_get_kis_quote",
                return_value=kis_quote,
            ),
            patch.object(price_threshold, "_fire_trigger_event") as fire,
        ):
            metrics = price_threshold.poll_price_threshold()

        assert fire.call_count == 1
        args, _ = fire.call_args
        assert args[0] == "005930"
        assert args[1] == "price_threshold"
        # metadata contains atr context
        metadata = args[2]
        assert "atr_14" in metadata
        assert "price_change_pct" in metadata
        assert metrics["fired"] == 1

    def test_same_ticker_throttled_within_cooldown(self):
        """Two consecutive polls for same ticker fire only once."""
        from trading.watchers import price_threshold
        from trading.watchers.throttle import TickerThrottle

        target_tickers = ["005930"]
        kis_quote = {"price": 97000, "change_pct": -3.0}
        # Shared throttle to simulate continued state across polls
        throttle = TickerThrottle(min_interval_sec=300, daily_cap=20)

        with (
            patch.object(price_threshold, "_get_target_tickers", return_value=target_tickers),
            patch.object(
                price_threshold, "compute_atr", return_value=_atr_payload(1000.0, 100000.0)
            ),
            patch.object(price_threshold, "_get_kis_quote", return_value=kis_quote),
            patch.object(price_threshold, "_get_shared_throttle", return_value=throttle),
            patch.object(price_threshold, "_fire_trigger_event") as fire,
        ):
            price_threshold.poll_price_threshold()
            price_threshold.poll_price_threshold()

        assert fire.call_count == 1

    def test_daily_cap_limits_firings(self):
        """daily_cap=2 blocks 3rd firing across distinct tickers."""
        from trading.watchers import price_threshold
        from trading.watchers.throttle import TickerThrottle

        target_tickers = ["A", "B", "C"]
        kis_quote = {"price": 97000, "change_pct": -3.0}
        throttle = TickerThrottle(min_interval_sec=300, daily_cap=2)

        with (
            patch.object(price_threshold, "_get_target_tickers", return_value=target_tickers),
            patch.object(
                price_threshold, "compute_atr", return_value=_atr_payload(1000.0, 100000.0)
            ),
            patch.object(price_threshold, "_get_kis_quote", return_value=kis_quote),
            patch.object(price_threshold, "_get_shared_throttle", return_value=throttle),
            patch.object(price_threshold, "_fire_trigger_event") as fire,
        ):
            price_threshold.poll_price_threshold()

        # Only 2 firings due to daily_cap
        assert fire.call_count == 2

    def test_atr_unavailable_skips_ticker_silently(self):
        """compute_atr returning None means insufficient data → skip silently."""
        from trading.watchers import price_threshold

        target_tickers = ["005930"]
        kis_quote = {"price": 97000, "change_pct": -3.0}

        with (
            patch.object(price_threshold, "_get_target_tickers", return_value=target_tickers),
            patch.object(price_threshold, "compute_atr", return_value=None),
            patch.object(price_threshold, "_get_kis_quote", return_value=kis_quote),
            patch.object(price_threshold, "_fire_trigger_event") as fire,
        ):
            metrics = price_threshold.poll_price_threshold()

        assert fire.call_count == 0
        assert metrics["skipped_no_atr"] == 1


class TestEdgeTrigger:
    """레벨 트리거 -> 엣지 트리거 (2026-09-12).

    change_pct 는 전일 종가 대비 당일 누적 변동률이라 한 번 임계를 넘으면 장 마감까지
    조건이 참이다. 2026-09-09 실측: 096770 한 종목이 13회 발사(8.99 -> 11.52 를 오가며
    값이 내려가도 발사), 정규 intraday 사이클 5개가 CYCLE_SKIPPED_IN_FLIGHT 로 파괴됐다.
    2026-09-02 에 volume_anomaly 를 고쳤는데(2246f59) 이 파일은 손대지 않았다.
    """

    @staticmethod
    def _poll(change_pct, last):
        from trading.watchers import price_threshold

        with (
            patch.object(price_threshold, "_get_target_tickers", return_value=["096770"]),
            patch.object(
                price_threshold, "compute_atr", return_value=_atr_payload(1000.0, 100000.0)
            ),
            patch.object(
                price_threshold, "_get_kis_quote",
                return_value={"price": 100000, "change_pct": change_pct},
            ),
            patch.object(price_threshold, "_last_fire", return_value=last),
            patch.object(price_threshold, "_fire_trigger_event") as fire,
        ):
            return price_threshold.poll_price_threshold(), fire

    def test_첫_발사는_통과한다(self):
        """임계 1.5%. 직전 발사 기록이 없으면 막지 않는다."""
        m, fire = self._poll(9.0, None)
        assert fire.call_count == 1
        assert m["level_suppressed"] == 0

    def test_같은_방향에서_충분히_더_안_움직이면_억제된다(self):
        """9/09 실측 모양 — 8.99 발사 후 11.52 는 8.99+1.5 를 넘지만,
        임계가 8.95 였던 실전에서는 17.94 가 필요해 전부 억제됐을 값이다."""
        m, fire = self._poll(10.0, (9.0, 9.0))   # 9.0 + 1.5 = 10.5 > 10.0
        assert fire.call_count == 0
        assert m["level_suppressed"] == 1

    def test_값이_내려가도_억제된다(self):
        """실측에 14:25 10.94 -> 14:35 10.72 재발사가 있었다."""
        m, fire = self._poll(10.72, (10.94, 10.94))
        assert fire.call_count == 0
        assert m["level_suppressed"] == 1

    def test_임계만큼_더_움직이면_새_신호다(self):
        m, fire = self._poll(10.6, (9.0, 9.0))   # 9.0 + 1.5 = 10.5 <= 10.6
        assert fire.call_count == 1
        assert m["level_suppressed"] == 0

    def test_방향이_뒤집히면_크기와_무관하게_새_신호다(self):
        """+9% 에서 -9% 로 뒤집힌 건 같은 신호가 아니다."""
        m, fire = self._poll(-9.0, (9.0, 9.0))
        assert fire.call_count == 1
        assert m["level_suppressed"] == 0

    def test_조회_실패는_감시자를_침묵시키지_않는다(self):
        """_last_fire 는 실패 시 None 을 준다 — 발사를 막지 않는 규약."""
        _, fire = self._poll(9.0, None)
        assert fire.call_count == 1
