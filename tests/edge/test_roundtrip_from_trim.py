"""SPEC-TRADING-040 M5 (REQ-040-5a) — first round-trip from a concentration trim.

Reproduction gate (AC-1/AC-2): before SPEC-040 a normal-range accumulated holding
(e.g. 086790: many same-day buys, pnl in [-2.37%, +2.26%], RSI < 85) never sold —
the extreme stop/take rules did not fire and the persona did not sell — so NO
round-trip ever completed and profitability stayed unverifiable.

With the SPEC-040 concentration trim, the watchdog issues a partial SELL of the
over-weight position. This test pins, via the pure FIFO ``build_roundtrips``
matcher, that such a trim SELL produces the FIRST completed round-trip with a
real net P&L — i.e. the exit *policy* fix actually closes the loop the exit
*mechanism* fix (SPEC-039) prepared.

@MX:SPEC: SPEC-TRADING-040
"""

from __future__ import annotations

from datetime import datetime

from trading.edge.roundtrips import build_roundtrips


def _fill(ticker, side, qty, price, ts, oid, fee=0.0):
    return {
        "ticker": ticker,
        "side": side,
        "fill_qty": qty,
        "fill_price": price,
        "fee": fee,
        "ts": datetime.fromisoformat(ts),
        "filled_at": datetime.fromisoformat(ts),
        "id": oid,
        "persona_decision_id": None,
        "confidence": None,
        "verdict": None,
    }


class TestRoundTripFromTrim:
    def test_accumulation_without_sell_has_no_roundtrip(self):
        """Reproduction: pure accumulation (the pre-040 086790 pattern) → 0 round-trips."""
        rows = [
            _fill("086790", "buy", 10, 10_000, "2026-06-02T09:30:00", 1),
            _fill("086790", "buy", 10, 9_800, "2026-06-02T10:00:00", 2),
            _fill("086790", "buy", 10, 9_900, "2026-06-02T10:30:00", 3),
        ]
        result = build_roundtrips(rows)
        assert result.roundtrips == []
        assert result.open_qty.get("086790") == 30

    def test_trim_sell_completes_first_roundtrip(self):
        """The concentration-trim partial SELL closes a FIFO chunk → first round-trip."""
        rows = [
            _fill("086790", "buy", 10, 10_000, "2026-06-02T09:30:00", 1),
            _fill("086790", "buy", 10, 9_800, "2026-06-02T10:00:00", 2),
            _fill("086790", "buy", 10, 9_900, "2026-06-02T10:30:00", 3),
            # SPEC-040 concentration trim: sell 15 of 30 to bring weight to cap.
            _fill("086790", "sell", 15, 10_200, "2026-06-03T10:00:00", 4),
        ]
        result = build_roundtrips(rows)

        assert len(result.roundtrips) >= 1, "trim must complete at least one round-trip"
        total_qty = sum(rt.qty for rt in result.roundtrips)
        assert total_qty == 15  # exactly the trimmed quantity is matched
        # FIFO: first 10 @10,000 then 5 @9,800, all exited @10,200 → net positive.
        net = sum(rt.net_pnl for rt in result.roundtrips)
        assert net > 0
        assert result.open_qty.get("086790") == 15  # remainder stays open
