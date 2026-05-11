"""SPEC-TRADING-019 REQ-019-9: Invalid / delisted ticker filter.

Discovered during 2026-05-11 manual backfill: ticker ``010620 현대미포조선``
passed the mechanical screen but pykrx returned 0 rows for a 90-day window
(likely delisted or renamed). Without this filter, downstream cache lookups
fail silently and the ticker shows up in screened_tickers.json as a phantom.

Per user decision Q-6 (2026-05-11), the filter probes each candidate with a
short OHLCV fetch and drops zero-row tickers, logging the drop to
``data/invalid_tickers.json`` for human review.
"""

# @MX:NOTE: SPEC-019 REQ-019-9 conservative on probe error (keep + warn)
# @MX:SPEC: SPEC-TRADING-019

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from trading.config import project_root
from trading.data import pykrx_adapter

LOG = logging.getLogger(__name__)

INVALID_FILE: Path = project_root() / "data" / "invalid_tickers.json"
PROBE_WINDOW_DAYS = 90


def _probe_ohlcv_rowcount(ticker: str, start: date, end: date) -> int:
    """Thin wrapper around pykrx_adapter.fetch_ohlcv for monkeypatch isolation."""
    return pykrx_adapter.fetch_ohlcv(ticker, start, end)


def _write_invalid_log(dropped: list[dict[str, str]]) -> None:
    """Persist dropped tickers to ``data/invalid_tickers.json`` for review."""
    try:
        INVALID_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, Any] = {}
        if INVALID_FILE.exists():
            try:
                existing = json.loads(INVALID_FILE.read_text())
            except Exception:
                existing = {}
        prior = existing.get("dropped", [])
        merged = list(prior) + list(dropped)
        payload = {
            "date": date.today().isoformat(),
            "tickers": sorted({d["ticker"] for d in merged}),
            "dropped": merged,
        }
        INVALID_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    except Exception:
        LOG.exception("invalid_tickers log write failed")


def filter_invalid_tickers(candidates: list[str]) -> list[str]:
    """Drop candidates that return 0 rows on a 90-day OHLCV probe.

    REQ-019-9:
        - 0 rows  → ticker dropped, logged to data/invalid_tickers.json.
        - probe error (network etc.) → ticker kept conservatively with warning.
    """
    today = date.today()
    start = today - timedelta(days=PROBE_WINDOW_DAYS)
    survivors: list[str] = []
    dropped: list[dict[str, str]] = []

    for ticker in candidates:
        try:
            rows = _probe_ohlcv_rowcount(ticker, start, today)
        except Exception as exc:
            LOG.warning(
                "invalid_ticker_filter: probe error for %s, keeping conservatively: %s",
                ticker,
                exc,
            )
            survivors.append(ticker)
            continue

        if rows is None or rows == 0:
            LOG.warning(
                "invalid_ticker_filter: dropping %s (0 rows in %d-day probe)",
                ticker,
                PROBE_WINDOW_DAYS,
            )
            dropped.append(
                {
                    "ticker": ticker,
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "reason": "zero_rows_probe",
                }
            )
        else:
            survivors.append(ticker)

    if dropped:
        _write_invalid_log(dropped)

    return survivors
