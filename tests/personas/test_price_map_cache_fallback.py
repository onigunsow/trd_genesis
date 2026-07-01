"""CHANGE B — _build_price_map: 미보유 BUY 종목 OHLCV 캐시 fallback TDD 테스트."""

from __future__ import annotations

from unittest.mock import patch


class TestBuildPriceMapCacheFallback:
    """_build_price_map이 미보유 BUY 종목을 OHLCV 캐시에서 가격 추정한다."""

    def _buy(self, ticker: str, qty: int = 10) -> dict:
        return {"ticker": ticker, "side": "buy", "qty": qty}

    def test_미보유_BUY_캐시_종가로_가격_설정(self):
        """BUY 신호 종목이 holdings에 없고 캐시에 있으면 캐시 종가를 사용한다."""
        from trading.personas.portfolio_gate import _build_price_map

        signals = [self._buy("055550")]
        holdings: list[dict] = []  # 미보유

        with patch("trading.data.cache.latest_close", return_value=38_500) as mock_lc:
            price_map = _build_price_map(signals, holdings)

        assert "055550" in price_map
        assert price_map["055550"] == 38_500
        mock_lc.assert_called_once()

    def test_미보유_BUY_캐시_없으면_가격_미설정(self):
        """BUY 신호 종목이 holdings에 없고 캐시도 없으면 price_map에 미포함."""
        from trading.personas.portfolio_gate import _build_price_map

        signals = [self._buy("999999")]
        holdings: list[dict] = []

        with patch("trading.data.cache.latest_close", return_value=None):
            price_map = _build_price_map(signals, holdings)

        assert "999999" not in price_map

    def test_미보유_BUY_캐시_0이면_가격_미설정(self):
        """캐시 종가가 0이면 price_map에 미포함 (fail-open 유지)."""
        from trading.personas.portfolio_gate import _build_price_map

        signals = [self._buy("888888")]
        holdings: list[dict] = []

        with patch("trading.data.cache.latest_close", return_value=0):
            price_map = _build_price_map(signals, holdings)

        assert "888888" not in price_map

    def test_보유_종목은_holdings_우선(self):
        """holdings에 있는 종목은 캐시를 호출하지 않고 기존 로직으로 가격 설정."""
        from trading.personas.portfolio_gate import _build_price_map

        holdings = [{
            "ticker": "005930",
            "qty": 10,
            "eval_amount": 800_000,
            "avg_cost": 80_000,
        }]
        signals = [self._buy("005930")]

        with patch("trading.data.cache.latest_close") as mock_lc:
            price_map = _build_price_map(signals, holdings)

        # holdings에서 가격 계산: 800_000 // 10 = 80_000
        assert price_map["005930"] == 80_000
        # 캐시 미호출
        mock_lc.assert_not_called()

    def test_복합_시나리오_보유와_미보유_혼합(self):
        """보유 종목은 holdings, 미보유 종목은 캐시에서 각각 가격 설정."""
        from trading.personas.portfolio_gate import _build_price_map

        holdings = [{
            "ticker": "005930",
            "qty": 5,
            "eval_amount": 400_000,
            "avg_cost": 80_000,
        }]
        signals = [self._buy("005930"), self._buy("055550")]

        def _mock_lc(source, symbol):
            if symbol == "055550":
                return 40_000
            return None

        with patch("trading.data.cache.latest_close", side_effect=_mock_lc):
            price_map = _build_price_map(signals, holdings)

        assert price_map["005930"] == 80_000  # holdings 기반
        assert price_map["055550"] == 40_000  # 캐시 기반
