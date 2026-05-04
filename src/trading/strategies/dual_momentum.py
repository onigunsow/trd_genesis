"""Antonacci dual momentum — monthly rotation across an asset universe.

At each month-end:
1. Compute trailing 12-month return for each asset.
2. Pick the asset with the highest momentum.
3. If that asset's 12M return > cash benchmark return, allocate 100% to it.
4. Else hold 100% cash (represented by zero weights everywhere).

Cash is implicit (sum of weights < 1 means cash holding).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from trading.strategies.base import Strategy, StrategyResult


@dataclass
class DualMomentum(Strategy):
    lookback_months: int = 12
    cash_benchmark: str | None = None     # if None, zero return
    name: str = "dual_momentum"
    _params: dict = field(default_factory=dict, init=False, repr=False)

    def compute(self, prices: pd.DataFrame) -> StrategyResult:
        # Resample to month-end closes.
        monthly = prices.resample("ME").last().dropna(how="all")
        # Trailing N-month return (skip current period, use prior period boundary).
        ret = monthly.pct_change(self.lookback_months)
        # Pick the asset with the highest return at each month-end.
        weights = pd.DataFrame(0.0, index=ret.index, columns=ret.columns)
        for ts, row in ret.iterrows():
            if row.isna().all():
                continue
            # Cash threshold
            cash_ret = (
                row[self.cash_benchmark]
                if self.cash_benchmark and self.cash_benchmark in row
                else 0.0
            )
            best = row.drop(self.cash_benchmark, errors="ignore") if self.cash_benchmark else row
            if best.dropna().empty:
                continue
            top = best.idxmax()
            top_ret = best[top]
            if pd.notna(top_ret) and top_ret > cash_ret:
                weights.at[ts, top] = 1.0
        # Forward-fill so the holding persists over the month, daily-aligned.
        daily_idx = prices.index
        daily_weights = weights.reindex(daily_idx, method="ffill").fillna(0.0)
        return StrategyResult(
            name=self.name,
            params={"lookback_months": self.lookback_months,
                    "cash_benchmark": self.cash_benchmark},
            weights=daily_weights,
        )
