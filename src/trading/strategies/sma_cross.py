"""SMA crossover strategy — single asset, long-only.

When fast SMA > slow SMA: target weight = 1. Else target weight = 0.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from trading.strategies.base import Strategy, StrategyResult


@dataclass
class SmaCross(Strategy):
    fast: int = 20
    slow: int = 60
    name: str = "sma_cross"

    def compute(self, prices: pd.DataFrame) -> StrategyResult:
        if prices.shape[1] != 1:
            raise ValueError("sma_cross expects single-asset prices DataFrame")
        col = prices.columns[0]
        s = prices[col]
        fast_ma = s.rolling(self.fast).mean()
        slow_ma = s.rolling(self.slow).mean()
        signal = (fast_ma > slow_ma).astype(float)
        # Hold target weight; flat-out if NaN.
        weights = pd.DataFrame({col: signal.fillna(0.0)})
        return StrategyResult(
            name=self.name,
            params={"fast": self.fast, "slow": self.slow},
            weights=weights,
        )
