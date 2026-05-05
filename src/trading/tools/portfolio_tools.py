"""Portfolio tool functions — KIS account balance and watchlist.

REQ-TOOL-01-3: Standalone functions wrapping existing logic.
REQ-TOOL-01-8: No Anthropic API or persona calls. Data-only.
"""

from __future__ import annotations

from typing import Any

from trading.config import get_settings
from trading.kis.account import balance
from trading.kis.client import KisClient
from trading.personas.context import DEFAULT_WATCHLIST, TICKER_NAMES


def get_portfolio_status() -> dict[str, Any]:
    """Fetch current portfolio positions and asset summary from KIS.

    Returns:
        Dict with total_assets, cash, stock_eval, cash_pct, holdings list.
    """
    s = get_settings()
    client = KisClient(s.trading_mode)
    bal = balance(client)

    total = bal.get("total_assets", 0)
    cash = bal.get("cash_d2", 0)
    stock_eval = bal.get("stock_eval", 0)
    cash_pct = (cash / total * 100) if total > 0 else 100.0
    equity_pct = (stock_eval / total * 100) if total > 0 else 0.0

    holdings = bal.get("holdings", [])
    # Simplify holdings for LLM consumption
    simplified_holdings = [
        {
            "ticker": h.get("ticker", ""),
            "name": h.get("name", ""),
            "qty": h.get("qty", 0),
            "avg_cost": h.get("avg_cost", 0),
            "current_price": h.get("current_price", 0),
            "pnl_pct": round(
                ((h.get("current_price", 0) - h.get("avg_cost", 1)) / h.get("avg_cost", 1) * 100), 2
            ) if h.get("avg_cost", 0) > 0 else 0.0,
        }
        for h in holdings
        if h.get("qty", 0) > 0
    ]

    return {
        "total_assets": total,
        "cash": cash,
        "stock_eval": stock_eval,
        "cash_pct": round(cash_pct, 1),
        "equity_pct": round(equity_pct, 1),
        "holdings_count": len(simplified_holdings),
        "holdings": simplified_holdings,
    }


def get_watchlist() -> dict[str, Any]:
    """Return current watchlist ticker codes and names.

    Returns:
        Dict with 'tickers' list containing code and name pairs.
    """
    tickers = [
        {"code": code, "name": TICKER_NAMES.get(code, code)}
        for code in DEFAULT_WATCHLIST
    ]
    return {"count": len(tickers), "tickers": tickers}
