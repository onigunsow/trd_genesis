"""페이퍼 매도 오만료 원장 소급 교정 — 판정 불변식 테스트.

핵심 안전 요구: 실제로 체결되지 않은 매도를 체결로 날조하면 안 된다.
판정은 KIS 진실(POSITION_SYNCED)로만 한다 — 잔고가 매도수량만큼 정확히
줄었을 때만 교정 대상이다.
"""

from __future__ import annotations

from trading.kis.expired_sell_repair import _classify, _resolve_price


def _row(**kw):
    base = {
        "id": 1, "ticker": "055550", "qty": 1, "mode": "paper",
        "pre_qty": 1, "post_qty": 0, "snap_px": 107_300, "ohlcv_px": 107_400,
    }
    base.update(kw)
    return base


class TestClassify:
    def test_balance_dropped_by_sold_qty_is_eligible(self):
        """정상 케이스: 1주 보유 → 1주 매도 → 잔고 0. 실체결이므로 교정 대상."""
        eligible, reason = _classify(_row(pre_qty=1, post_qty=0, qty=1))
        assert eligible
        assert "exactly the sold quantity" in reason

    def test_partial_sell_is_eligible(self):
        """부분 매도: 6주 중 3주 매도 → 잔고 3. 델타가 수량과 일치하므로 대상."""
        eligible, _ = _classify(_row(pre_qty=6, post_qty=3, qty=3))
        assert eligible

    def test_balance_unchanged_is_genuinely_unfilled(self):
        """order 64 재현: 잔고가 안 줄었다 → 진짜 미체결. expired 가 옳다."""
        eligible, reason = _classify(_row(pre_qty=4, post_qty=4, qty=4))
        assert not eligible
        assert "genuinely unfilled" in reason

    def test_missing_pre_sync_is_skipped(self):
        """order 93 재현: 전후 POSITION_SYNCED 근거 없음 → 손대지 않는다."""
        eligible, reason = _classify(_row(pre_qty=None))
        assert not eligible
        assert "no POSITION_SYNCED evidence" in reason

    def test_missing_post_sync_is_skipped(self):
        eligible, _ = _classify(_row(post_qty=None))
        assert not eligible

    def test_phantom_position_is_skipped(self):
        """RC-1 유령 포지션: KIS 잔고가 애초에 0 → 델타 0 ≠ 수량 → 배제."""
        eligible, reason = _classify(_row(pre_qty=0, post_qty=0, qty=1))
        assert not eligible
        assert "genuinely unfilled" in reason

    def test_partial_delta_mismatch_is_skipped(self):
        """잔고는 줄었지만 매도수량과 다르다 → 다른 원인이므로 손대지 않는다."""
        eligible, _ = _classify(_row(pre_qty=5, post_qty=3, qty=1))
        assert not eligible

    def test_no_price_source_is_skipped(self):
        """체결가를 근사할 수 없으면 교정하지 않는다 (가격 날조 금지)."""
        eligible, reason = _classify(
            _row(snap_px=None, ohlcv_px=None)
        )
        assert not eligible
        assert "no price source" in reason


class TestResolvePrice:
    def test_snapshot_price_preferred(self):
        """스냅샷은 보유 중 마지막 동기화 시점 시장가 — 일봉 종가보다 정확."""
        px, src = _resolve_price(_row(snap_px=107_300, ohlcv_px=107_400))
        assert px == 107_300
        assert src == "position_eval_snapshot.eval_price"

    def test_ohlcv_fallback_when_no_snapshot(self):
        px, src = _resolve_price(_row(snap_px=None, ohlcv_px=216_000))
        assert px == 216_000
        assert src == "ohlcv.close"

    def test_unavailable_when_both_missing(self):
        px, src = _resolve_price(_row(snap_px=None, ohlcv_px=None))
        assert px is None
        assert src == "unavailable"
