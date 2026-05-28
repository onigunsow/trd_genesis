"""SPEC-TRADING-029 v0.2.0 (REQ-029-9) — KRX ticker display-name resolver.

Resolves a ticker's Korean display name for trade-alert rendering. The primary
source is pykrx (``get_market_ticker_name``), which can resolve any KRX-listed
ticker independently of account holdings — important because a freshly submitted
order may not yet appear in ``inquire-balance`` holdings, making the balance
``prdt_name`` unreliable at alert time.

Fallback chain: pykrx -> static ``context.TICKER_NAMES`` dict -> ``None``.
Results are memoised with ``lru_cache`` so the pykrx network call happens at
most once per ticker per process.
"""

from __future__ import annotations

import logging
from functools import lru_cache

LOG = logging.getLogger(__name__)


def _pykrx_name(ticker: str) -> str | None:
    """Resolve via pykrx, or ``None`` on any error / empty result."""
    try:
        from pykrx import stock  # lazy import (heavy + optional at runtime)

        name = stock.get_market_ticker_name(ticker)
    except Exception as exc:  # any pykrx/network failure falls back
        LOG.debug("pykrx ticker-name lookup failed for %s: %s", ticker, exc)
        return None
    name = (name or "").strip()
    return name or None


def _static_name(ticker: str) -> str | None:
    """Resolve via the offline ``context.TICKER_NAMES`` fallback dict."""
    try:
        from trading.personas.context import TICKER_NAMES
    except Exception as exc:  # never let a fallback raise
        LOG.debug("static TICKER_NAMES unavailable: %s", exc)
        return None
    return TICKER_NAMES.get(ticker)


@lru_cache(maxsize=2048)
def ticker_name(ticker: str) -> str | None:
    """Return the KRX display name for ``ticker`` (pykrx -> static -> None).

    @MX:NOTE: memoised with lru_cache so the pykrx call fires once per ticker
    per process (EC-029-9). Callers must not rely on this picking up renames
    within a single process lifetime.
    """
    return _pykrx_name(ticker) or _static_name(ticker)
