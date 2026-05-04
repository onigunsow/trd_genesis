"""Vectorised backtest engine.

Inputs:
- prices: DataFrame of close prices (index=date, cols=asset)
- weights: DataFrame of target weights (same shape as prices)
- fee_rate: per-trade transaction cost as fraction (default 0.0005, i.e. 5bp)

Outputs metrics dict (cagr, mdd, sharpe, trades, final_equity, equity_curve).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


# Korean market conventions
DEFAULT_FEE_RATE = 0.00015     # 매매수수료
DEFAULT_TAX_RATE = 0.0018      # 거래세 (매도 시)
DEFAULT_SLIPPAGE = 0.0005      # 시장가 슬리피지
TRADING_DAYS_PER_YEAR = 252


@dataclass
class BacktestResult:
    cagr: float
    mdd: float
    sharpe: float
    trades: int
    final_equity: float
    equity_curve: pd.Series = field(repr=False)
    daily_returns: pd.Series = field(repr=False)


def run(
    prices: pd.DataFrame,
    weights: pd.DataFrame,
    *,
    initial_capital: float = 10_000_000.0,
    fee_rate: float = DEFAULT_FEE_RATE,
    tax_rate: float = DEFAULT_TAX_RATE,
    slippage: float = DEFAULT_SLIPPAGE,
) -> BacktestResult:
    # Align indexes.
    common_idx = prices.index.intersection(weights.index)
    prices = prices.loc[common_idx]
    weights = weights.loc[common_idx].reindex(columns=prices.columns).fillna(0.0)

    # Daily asset returns.
    asset_rets = prices.pct_change().fillna(0.0)

    # Portfolio return = previous-day weights * today's asset return.
    # Use the prior-day weights to avoid look-ahead.
    weights_lag = weights.shift(1).fillna(0.0)
    port_rets = (weights_lag * asset_rets).sum(axis=1)

    # Trading cost: weight changes incur fees on the changed fraction.
    weight_changes = weights.diff().abs().fillna(0.0).sum(axis=1)
    # Buying side cost.
    buy_increase = weights.diff().clip(lower=0).fillna(0.0).sum(axis=1)
    # Selling side: tax + fee.
    sell_decrease = (-weights.diff().clip(upper=0).fillna(0.0)).sum(axis=1)

    cost = (buy_increase + sell_decrease) * (fee_rate + slippage) + sell_decrease * tax_rate
    port_rets = port_rets - cost

    equity = (1 + port_rets).cumprod() * initial_capital
    final_equity = float(equity.iloc[-1]) if len(equity) else initial_capital

    # CAGR
    if len(equity) > 1:
        years = (equity.index[-1] - equity.index[0]).days / 365.25
        cagr = (final_equity / initial_capital) ** (1 / years) - 1 if years > 0 else 0.0
    else:
        cagr = 0.0

    # MDD
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    mdd = float(drawdown.min()) if len(drawdown) else 0.0

    # Sharpe (annualised, rf=0)
    daily_std = float(port_rets.std())
    sharpe = (
        (port_rets.mean() / daily_std) * math.sqrt(TRADING_DAYS_PER_YEAR)
        if daily_std and not np.isnan(daily_std)
        else 0.0
    )

    trades = int((weight_changes > 0).sum())

    return BacktestResult(
        cagr=float(cagr),
        mdd=mdd,
        sharpe=float(sharpe),
        trades=trades,
        final_equity=final_equity,
        equity_curve=equity,
        daily_returns=port_rets,
    )
