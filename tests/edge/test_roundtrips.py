"""Edge Validation Phase 1 — FIFO 라운드트립 매칭.

순수 함수 ``build_roundtrips`` 만 검증(DB 없음). FIFO 순서, 부분체결 분할, 수수료 안분,
재고 초과 매도(unmatched_sells), 진입 confidence/verdict 귀속을 다룬다.
"""

from __future__ import annotations

from datetime import datetime

from trading.edge.roundtrips import build_roundtrips


def _buy(ticker, qty, price, fee=0, ts="2026-01-01T10:00:00", oid=1, confidence=None, verdict=None):
    return {
        "id": oid, "ts": datetime.fromisoformat(ts), "filled_at": None,
        "side": "buy", "ticker": ticker, "fill_qty": qty, "fill_price": price,
        "fee": fee, "confidence": confidence, "verdict": verdict,
    }


def _sell(ticker, qty, price, fee=0, ts="2026-01-05T10:00:00", oid=2):
    return {
        "id": oid, "ts": datetime.fromisoformat(ts), "filled_at": None,
        "side": "sell", "ticker": ticker, "fill_qty": qty, "fill_price": price,
        "fee": fee, "confidence": None, "verdict": None,
    }


class TestFifoBasic:
    def test_single_buy_single_sell(self):
        rows = [
            _buy("005930", 10, 70_000, fee=100, ts="2026-01-01T10:00:00", oid=1),
            _sell("005930", 10, 77_000, fee=110, ts="2026-01-10T10:00:00", oid=2),
        ]
        res = build_roundtrips(rows)
        assert len(res.roundtrips) == 1
        assert not res.unmatched_sells
        rt = res.roundtrips[0]
        assert rt.qty == 10
        assert rt.entry_price == 70_000
        assert rt.exit_price == 77_000
        assert rt.gross_pnl == (77_000 - 70_000) * 10
        assert rt.net_pnl == rt.gross_pnl - 100 - 110
        assert rt.holding_days == 9
        assert rt.is_win

    def test_fifo_order_oldest_lot_first(self):
        # 두 매수 로트(다른 단가) → 일부 매도. FIFO 면 첫(싼) 로트가 먼저 소진.
        rows = [
            _buy("A", 10, 100, ts="2026-01-01T10:00:00", oid=1),
            _buy("A", 10, 200, ts="2026-01-02T10:00:00", oid=2),
            _sell("A", 5, 300, ts="2026-01-03T10:00:00", oid=3),
        ]
        res = build_roundtrips(rows)
        assert len(res.roundtrips) == 1
        assert res.roundtrips[0].entry_price == 100  # 첫 로트
        assert res.roundtrips[0].qty == 5
        assert res.open_qty["A"] == 15  # 5 남은 첫 로트 + 10 둘째 로트


class TestPartialAndSpanning:
    def test_sell_spans_two_lots_creates_two_roundtrips(self):
        rows = [
            _buy("A", 10, 100, ts="2026-01-01T10:00:00", oid=1),
            _buy("A", 10, 200, ts="2026-01-02T10:00:00", oid=2),
            _sell("A", 15, 300, ts="2026-01-03T10:00:00", oid=3),
        ]
        res = build_roundtrips(rows)
        assert len(res.roundtrips) == 2
        qtys = sorted(r.qty for r in res.roundtrips)
        assert qtys == [5, 10]
        prices = sorted(r.entry_price for r in res.roundtrips)
        assert prices == [100, 200]
        assert res.open_qty.get("A", 0) == 5  # 둘째 로트에 5 남음


class TestFeeApportionment:
    def test_buy_fee_split_across_matched_chunks(self):
        # 매수 1건(20주, fee 200 → 주당 10) 을 두 매도로 나눠 청산.
        rows = [
            _buy("A", 20, 100, fee=200, ts="2026-01-01T10:00:00", oid=1),
            _sell("A", 5, 110, fee=50, ts="2026-01-02T10:00:00", oid=2),   # 매도 fee 주당 10
            _sell("A", 15, 120, fee=150, ts="2026-01-03T10:00:00", oid=3),  # 매도 fee 주당 10
        ]
        res = build_roundtrips(rows)
        assert len(res.roundtrips) == 2
        first = next(r for r in res.roundtrips if r.qty == 5)
        # 진입 수수료 = 주당 10 * 5 = 50, 청산 수수료 = 주당 10 * 5 = 50
        assert first.entry_fee == 50
        assert first.exit_fee == 50
        # 진입 수수료 총합은 원래 매수 fee 와 일치해야 한다(누락/중복 없음).
        total_entry_fee = sum(r.entry_fee for r in res.roundtrips)
        assert total_entry_fee == 200


class TestUnmatchedSells:
    def test_oversold_is_recorded_not_dropped(self):
        rows = [
            _buy("A", 5, 100, ts="2026-01-01T10:00:00", oid=1),
            _sell("A", 8, 120, ts="2026-01-02T10:00:00", oid=2),  # 3주 초과
        ]
        res = build_roundtrips(rows)
        assert len(res.roundtrips) == 1
        assert res.roundtrips[0].qty == 5
        assert len(res.unmatched_sells) == 1
        assert res.unmatched_sells[0].qty == 3
        assert res.unmatched_sells[0].ticker == "A"

    def test_sell_with_no_inventory(self):
        rows = [_sell("A", 4, 100, ts="2026-01-02T10:00:00", oid=1)]
        res = build_roundtrips(rows)
        assert not res.roundtrips
        assert len(res.unmatched_sells) == 1
        assert res.unmatched_sells[0].qty == 4


class TestDecisionAttribution:
    def test_entry_confidence_and_verdict_carry_from_buy(self):
        rows = [
            _buy("A", 10, 100, ts="2026-01-01T10:00:00", oid=1,
                 confidence=0.82, verdict="APPROVE"),
            _sell("A", 10, 120, ts="2026-01-02T10:00:00", oid=2),
        ]
        res = build_roundtrips(rows)
        rt = res.roundtrips[0]
        assert rt.confidence == 0.82
        assert rt.verdict == "APPROVE"

    def test_zero_qty_rows_ignored(self):
        rows = [
            _buy("A", 0, 100, ts="2026-01-01T10:00:00", oid=1),
            _sell("A", 0, 120, ts="2026-01-02T10:00:00", oid=2),
        ]
        res = build_roundtrips(rows)
        assert not res.roundtrips
        assert not res.unmatched_sells

    def test_per_ticker_isolation(self):
        rows = [
            _buy("A", 10, 100, ts="2026-01-01T10:00:00", oid=1),
            _buy("B", 10, 100, ts="2026-01-01T10:00:00", oid=2),
            _sell("A", 10, 110, ts="2026-01-02T10:00:00", oid=3),
        ]
        res = build_roundtrips(rows)
        assert len(res.roundtrips) == 1
        assert res.roundtrips[0].ticker == "A"
        assert res.open_qty.get("B") == 10
