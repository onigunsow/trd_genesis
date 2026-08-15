"""SPEC-TRADING-065 REQ-065-2a — since 필터는 진입일 기준이며 FIFO 짝을 깨지 않는다.

배경: 2026-08-15 에 출구 규칙(floor -10 → -15%)을 바꿨다. 옛 포지션과 새 포지션이
섞이면 PF 가 한동안 더 나빠 보이므로 검증 게이트는 "수정 이후 진입분만" 봐야 한다.
체결 ts 로 자르면 since 이전 매수/이후 매도가 짝을 잃어 unmatched 로 새고 실현손익이
어긋난다 — 그래서 전체 원장으로 왕복을 만든 뒤 entry_date 로 거른다.
"""

from __future__ import annotations

from datetime import date, datetime

from trading.edge.roundtrips import RoundTripResult, build_roundtrips, filter_since


def _buy(ticker, qty, price, ts, oid):
    return {"id": oid, "ts": datetime.fromisoformat(ts), "filled_at": None,
            "side": "buy", "ticker": ticker, "fill_qty": qty, "fill_price": price,
            "fee": 0, "confidence": None, "verdict": None}


def _sell(ticker, qty, price, ts, oid):
    return {"id": oid, "ts": datetime.fromisoformat(ts), "filled_at": None,
            "side": "sell", "ticker": ticker, "fill_qty": qty, "fill_price": price,
            "fee": 0, "confidence": None, "verdict": None, "correction": False}


SINCE = date(2026, 8, 17)  # 게이트 기준일 예시 — 테스트 안에서만 쓰는 픽스처


def _ledger():
    """since 를 걸치는 원장: 8/10 매수 → 8/20 매도(옛 진입), 8/18 매수 → 8/25 매도(새 진입)."""
    return [
        _buy("A", 1, 100, "2026-08-10T10:00:00", 1),
        _buy("A", 1, 110, "2026-08-18T10:00:00", 2),
        _sell("A", 1, 105, "2026-08-20T10:00:00", 3),  # FIFO → 8/10 lot 청산
        _sell("A", 1, 120, "2026-08-25T10:00:00", 4),  # FIFO → 8/18 lot 청산
    ]


class TestFilterSinceKeepsFifoIntact:
    def test_full_ledger_then_filter_by_entry_date(self):
        full = build_roundtrips(_ledger())
        assert len(full.roundtrips) == 2
        gated = filter_since(full, SINCE)
        assert [rt.entry_date for rt in gated.roundtrips] == [date(2026, 8, 18)]
        assert gated.roundtrips[0].exit_price == 120

    def test_ts_cut_would_break_pairing(self):
        """대조군: 체결 ts 로 자르면 8/20 매도가 진입 짝을 잃는다 — 그래서 안 쓴다."""
        cut = [r for r in _ledger() if r["ts"].date() >= SINCE]
        wrong = build_roundtrips(cut)
        # 8/18 매수 1주만 남는데 매도는 2건 → 하나는 unmatched,
        # 남은 왕복의 exit 가 8/20 으로 잘못 붙는다
        assert len(wrong.unmatched_sells) == 1
        assert wrong.roundtrips[0].exit_price == 105  # 잘못된 짝 (진짜는 120)

    def test_none_returns_input_unchanged(self):
        full = build_roundtrips(_ledger())
        assert filter_since(full, None) is full

    def test_open_qty_and_unmatched_not_filtered(self):
        """open_qty/unmatched 는 원장 전체의 사실 — 필터 대상이 아니다."""
        rows = [*_ledger(), _buy("A", 3, 130, "2026-08-11T10:00:00", 9)]  # 옛 미청산 3주
        full = build_roundtrips(rows)
        gated = filter_since(full, SINCE)
        assert gated.open_qty == full.open_qty
        assert gated.unmatched_sells == full.unmatched_sells

    def test_boundary_inclusive(self):
        rows = [_buy("B", 1, 100, "2026-08-17T09:00:00", 1),
                _sell("B", 1, 101, "2026-08-19T09:00:00", 2)]
        gated = filter_since(build_roundtrips(rows), SINCE)
        assert len(gated.roundtrips) == 1

    def test_returns_new_object(self):
        full = build_roundtrips(_ledger())
        gated = filter_since(full, SINCE)
        assert isinstance(gated, RoundTripResult)
        assert gated is not full
        assert len(full.roundtrips) == 2  # 입력 불변
