"""Backtest engine — minimal vectorised long-only equity curve simulator.

Independent of vectorbt to keep the M3 dependency footprint low. Computes:
- equity curve
- CAGR
- Max drawdown (MDD)
- Sharpe ratio (annualised, rf=0)
- trade count
"""
