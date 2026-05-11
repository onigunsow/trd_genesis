"""SPEC-TRADING-019 REQ-019-9: Invalid/delisted ticker filter for daily_screen.

Pre-RED Discovery:
- Real-world case (2026-05-11 manual backfill): ticker 010620 현대미포조선
  returned 0 rows from pykrx (delisted/renamed). It was passing the mechanical
  screen but failing downstream cache lookups.
- Solution per user Q-6: probe each screened candidate with a 90-day OHLCV
  fetch. If 0 rows returned, drop ticker and log to data/invalid_tickers.json.
- Probe failures (network errors) keep the ticker conservatively + warning.
"""

from __future__ import annotations

import json
from unittest.mock import patch


class TestFilterInvalidTickers:
    """REQ-019-9: drop tickers that return 0 rows on OHLCV probe."""

    def test_zero_row_ticker_dropped(self, tmp_path, monkeypatch):
        """Probe returns 0 rows → ticker is dropped from output."""
        from trading.screener import invalid_ticker_filter as mod

        monkeypatch.setattr(mod, "INVALID_FILE", tmp_path / "invalid_tickers.json")

        def _probe(ticker, start, end):
            if ticker == "010620":
                return 0  # delisted
            return 60  # ~60 rows over 90d window

        candidates = ["005930", "010620", "005380"]
        with patch.object(mod, "_probe_ohlcv_rowcount", side_effect=_probe):
            result = mod.filter_invalid_tickers(candidates)

        assert "010620" not in result
        assert "005930" in result
        assert "005380" in result

    def test_invalid_tickers_logged_to_file(self, tmp_path, monkeypatch):
        """REQ-019-9: dropped tickers logged to data/invalid_tickers.json with timestamp."""
        from trading.screener import invalid_ticker_filter as mod

        invalid_path = tmp_path / "invalid_tickers.json"
        monkeypatch.setattr(mod, "INVALID_FILE", invalid_path)

        with patch.object(
            mod,
            "_probe_ohlcv_rowcount",
            side_effect=lambda t, s, e: 0 if t == "010620" else 50,
        ):
            mod.filter_invalid_tickers(["010620", "005930"])

        assert invalid_path.exists()
        body = json.loads(invalid_path.read_text())
        assert "010620" in body.get("tickers", []) or any(
            entry.get("ticker") == "010620" for entry in body.get("dropped", [])
        )
        # Timestamp should be present
        assert "date" in body or any(
            "date" in e or "ts" in e for e in body.get("dropped", [])
        )

    def test_probe_error_keeps_ticker_with_warning(self, tmp_path, monkeypatch, caplog):
        """Probe network error → ticker conservatively kept + WARNING logged."""
        from trading.screener import invalid_ticker_filter as mod

        monkeypatch.setattr(mod, "INVALID_FILE", tmp_path / "invalid_tickers.json")

        def _probe(ticker, start, end):
            if ticker == "FLAKY":
                raise ConnectionError("temporary network error")
            return 50

        with patch.object(mod, "_probe_ohlcv_rowcount", side_effect=_probe):
            with caplog.at_level("WARNING"):
                result = mod.filter_invalid_tickers(["005930", "FLAKY", "005380"])

        # Conservative: keep ticker on probe error
        assert "FLAKY" in result
        # Warning logged
        assert any(
            "FLAKY" in r.message or "probe" in r.message.lower() for r in caplog.records
        )

    def test_all_valid_tickers_pass_through(self, tmp_path, monkeypatch):
        """All probes return > 0 → output equals input (order preserved)."""
        from trading.screener import invalid_ticker_filter as mod

        monkeypatch.setattr(mod, "INVALID_FILE", tmp_path / "invalid_tickers.json")

        with patch.object(mod, "_probe_ohlcv_rowcount", return_value=60):
            result = mod.filter_invalid_tickers(["005930", "005380", "035720"])

        assert result == ["005930", "005380", "035720"]
