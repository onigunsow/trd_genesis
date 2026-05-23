"""SPEC-TRADING-026 — Overheating (단기과열) softening at the screener.

These tests pin the SPEC-026 behavioural change layered on top of SPEC-025:

- 단기과열 (stat_cls 55) is NO LONGER excluded from the candidate pool. It is
  kept and score-penalized so strong names can still surface, especially on
  market-wide overheating days (REQ-026-1, REQ-026-2).
- True risk states (관리 51 / 투자위험 52 / 투자경고 53 / 거래정지 54) and any
  blocked entry WITHOUT an explicit stat_cls 55 remain a HARD exclude
  (conservative default — REQ-026-3).
- Threshold guard: a market-wide overheating regime (many 55 tickers) uses a
  light penalty; a stock-specific handful uses a strong penalty (REQ-026-4).
- The blocked file is accepted when dated today OR yesterday (KST), because the
  refresh cron (07:25) runs AFTER the screener (06:30) (REQ-026-5 / cron fix).

Test strategy mirrors tests/screener/test_blocked_filter.py: a fake cursor
feeds qualifying rows so every ticker scores identically, isolating the
penalty/exclusion logic from DB/KIS integration.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")


# ---------------------------------------------------------------------------
# Minimal mock harness — every ticker returns the SAME qualifying score so the
# only score difference comes from the SPEC-026 overheating penalty.
# ---------------------------------------------------------------------------

def _fund_row(market_cap: float = 5e12, per: float = 10.0) -> dict[str, Any]:
    return {"market_cap": market_cap, "per": per, "pbr": 1.0, "div_yield": 0.0}


def _ohlcv_rows(n: int = 25, base: float = 100_000.0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i in range(n):
        delta = (1 if i % 2 == 0 else -1) * 100.0
        rows.append({"ts": f"2026-05-{19 - i:02d}", "close": base + delta, "volume": 200_000})
    return rows


def _flows_row(f5: int = 1_000_000_000) -> dict[str, Any]:
    return {"f5": f5}


class _SeqCursor:
    def __init__(self, universe: list[str]) -> None:
        self._universe = universe
        self._mode = "universe"

    def execute(self, sql: str, params: Any = None) -> None:
        s = sql.strip().lower()
        if "from ohlcv" in s and "distinct symbol" in s:
            self._mode = "universe"
        elif "from fundamentals" in s:
            self._mode = "fund"
        elif "from ohlcv" in s:
            self._mode = "ohlcv"
        elif "from flows" in s:
            self._mode = "flows"
        else:
            self._mode = "other"

    def fetchone(self) -> dict[str, Any] | None:
        if self._mode == "fund":
            return _fund_row()
        if self._mode == "flows":
            return _flows_row()
        return None

    def fetchall(self) -> list[dict[str, Any]]:
        if self._mode == "universe":
            return [{"symbol": t} for t in self._universe]
        if self._mode == "ohlcv":
            return _ohlcv_rows()
        return []

    def __enter__(self) -> _SeqCursor:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _SeqConnection:
    def __init__(self, universe: list[str]) -> None:
        self._cursor = _SeqCursor(universe)

    def cursor(self) -> _SeqCursor:
        return self._cursor

    def __enter__(self) -> _SeqConnection:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


@contextmanager
def _patched_connection(universe: list[str]):
    yield _SeqConnection(universe)


def _write_blocked(tmp_path, mapping: dict[str, dict], *, day_offset: int = 0):
    """Write blocked_tickers.json with a KST date offset by ``day_offset`` days."""
    file_date = (datetime.now(_KST).date() - timedelta(days=day_offset)).isoformat()
    p = tmp_path / "blocked_tickers.json"
    p.write_text(json.dumps({"date": file_date, "blocked": mapping}, ensure_ascii=False))
    return p


def _run_with(mod, universe, blocked_path, tmp_path):
    """Invoke run() with output redirected to tmp and DB mocked."""
    import unittest.mock as um

    with um.patch.object(mod, "BLOCKED_FILE", blocked_path), \
         um.patch.object(mod, "SCREEN_FILE", tmp_path / "screened_tickers.json"), \
         um.patch.object(mod, "PENDING_FILE", tmp_path / "pending_screen.json"), \
         um.patch.object(mod, "DEFAULT_WATCHLIST", []), \
         patch(
             "trading.screener.daily_screen.connection",
             side_effect=lambda *a, **kw: _patched_connection(universe),
         ):
        return mod.run()


# ---------------------------------------------------------------------------
# stat_cls classification (market.py)
# ---------------------------------------------------------------------------

class TestStatClsClassification:
    def test_overheated_only_55(self):
        from trading.kis.market import OVERHEAT_STAT_CLS, is_overheated

        assert OVERHEAT_STAT_CLS == "55"
        assert is_overheated("55") is True
        for code in ("00", "51", "52", "53", "54", ""):
            assert is_overheated(code) is False

    def test_hard_block_is_anything_but_normal_or_overheated(self):
        from trading.kis.market import is_hard_block

        for code in ("51", "52", "53", "54"):
            assert is_hard_block(code) is True
        assert is_hard_block("00") is False  # normal
        assert is_hard_block("55") is False  # overheated → soft, not hard
        # Unknown / missing codes default to HARD block (conservative).
        assert is_hard_block("") is True
        assert is_hard_block("99") is True


# ---------------------------------------------------------------------------
# REQ-026-4 — threshold guard penalty selection (pure helper)
# ---------------------------------------------------------------------------

class TestOverheatPenaltyHelper:
    def test_stock_specific_uses_strong_penalty(self):
        from trading.screener import daily_screen as mod

        penalty, market_wide = mod._overheat_penalty(overheat_count=1, pool_size=100)
        assert market_wide is False
        assert penalty == mod.OVERHEAT_PENALTY_NORMAL
        assert mod.OVERHEAT_PENALTY_NORMAL > mod.OVERHEAT_PENALTY_MARKETWIDE

    def test_market_wide_by_count(self):
        from trading.screener import daily_screen as mod

        penalty, market_wide = mod._overheat_penalty(
            overheat_count=mod.OVERHEAT_MARKETWIDE_COUNT, pool_size=200
        )
        assert market_wide is True
        assert penalty == mod.OVERHEAT_PENALTY_MARKETWIDE

    def test_market_wide_by_ratio(self):
        from trading.screener import daily_screen as mod

        # Below the count threshold but above the ratio threshold.
        penalty, market_wide = mod._overheat_penalty(overheat_count=4, pool_size=10)
        assert market_wide is True
        assert penalty == mod.OVERHEAT_PENALTY_MARKETWIDE

    def test_empty_pool_is_safe(self):
        from trading.screener import daily_screen as mod

        penalty, market_wide = mod._overheat_penalty(overheat_count=0, pool_size=0)
        assert market_wide is False
        assert penalty == mod.OVERHEAT_PENALTY_NORMAL


# ---------------------------------------------------------------------------
# REQ-026-3 / REQ-026-5 — _load_blocked_map
# ---------------------------------------------------------------------------

class TestLoadBlockedMap:
    def test_returns_ticker_to_stat_cls(self, tmp_path, monkeypatch):
        from trading.screener import daily_screen as mod

        path = _write_blocked(tmp_path, {
            "005930": {"stat_cls": "55", "reason": "단기과열"},
            "000660": {"stat_cls": "51", "reason": "관리"},
        })
        monkeypatch.setattr(mod, "BLOCKED_FILE", path)

        m = mod._load_blocked_map()
        assert m == {"005930": "55", "000660": "51"}

    def test_missing_stat_cls_becomes_empty_string(self, tmp_path, monkeypatch):
        from trading.screener import daily_screen as mod

        path = _write_blocked(tmp_path, {"005930": {"reason": "intraday safety"}})
        monkeypatch.setattr(mod, "BLOCKED_FILE", path)

        assert mod._load_blocked_map() == {"005930": ""}

    def test_yesterday_is_accepted(self, tmp_path, monkeypatch):
        """REQ-026-5: refresh cron runs after the screener, so yesterday's file
        is the freshest available at screen time and must be honoured."""
        from trading.screener import daily_screen as mod

        path = _write_blocked(
            tmp_path, {"005930": {"stat_cls": "55"}}, day_offset=1
        )
        monkeypatch.setattr(mod, "BLOCKED_FILE", path)

        assert mod._load_blocked_map() == {"005930": "55"}

    def test_two_days_old_is_stale(self, tmp_path, monkeypatch, caplog):
        from trading.screener import daily_screen as mod

        path = _write_blocked(
            tmp_path, {"005930": {"stat_cls": "55"}}, day_offset=2
        )
        monkeypatch.setattr(mod, "BLOCKED_FILE", path)

        with caplog.at_level("WARNING"):
            assert mod._load_blocked_map() == {}
        assert any("stale" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# REQ-026-1 / REQ-026-2 — run() softens 55, hard-excludes the rest
# ---------------------------------------------------------------------------

class TestRunSoftensOverheat:
    def test_overheated_kept_but_penalized(self, tmp_path, monkeypatch):
        from trading.screener import daily_screen as mod

        universe = ["005930", "000660"]  # 005930 overheated(55), 000660 normal
        path = _write_blocked(tmp_path, {"005930": {"stat_cls": "55"}})

        result = _run_with(mod, universe, path, tmp_path)

        # REQ-026-1: overheated ticker is NOT excluded.
        assert "005930" in result["tickers"]
        assert "000660" in result["tickers"]

        # REQ-026-2: overheated ticker is flagged and ranks BELOW the normal one.
        details = {d["ticker"]: d for d in result["details"]}
        assert details["005930"].get("overheated") is True
        assert details["000660"].get("overheated") in (False, None)
        assert details["005930"]["score"] < details["000660"]["score"]

    def test_hard_block_still_excluded(self, tmp_path, monkeypatch):
        from trading.screener import daily_screen as mod

        universe = ["005930", "000660"]  # 005930 관리(51) → hard exclude
        path = _write_blocked(tmp_path, {"005930": {"stat_cls": "51"}})

        result = _run_with(mod, universe, path, tmp_path)

        assert "005930" not in result["tickers"]
        assert "000660" in result["tickers"]

    def test_blocked_without_stat_cls_is_hard_excluded(self, tmp_path, monkeypatch):
        """Conservative default: no stat_cls → treat as hard block, not soften."""
        from trading.screener import daily_screen as mod

        universe = ["005930", "000660"]
        path = _write_blocked(tmp_path, {"005930": {"reason": "단기과열"}})  # no stat_cls

        result = _run_with(mod, universe, path, tmp_path)

        assert "005930" not in result["tickers"]
