"""SPEC-TRADING-036 REQ-036-1 — Korean market momentum snapshot tests.

The momentum layer separates three concerns so each is independently testable:
- ``gather_momentum()``  : best-effort I/O that fills a ``MomentumSnapshot``
  (each field may be ``None`` on failure) — NEVER raises (C-9).
- ``render_section()``   : pure markdown renderer producing the
  ``## 한국 시장 모멘텀`` section, with ``(unavailable: ...)`` markers for
  missing external fields and always-present robust fields when available.

@MX:SPEC: SPEC-TRADING-036
"""

from __future__ import annotations

from unittest.mock import patch

from trading.data import korea_momentum as km


def _full_snapshot():
    return km.MomentumSnapshot(
        kospi_close=7498.34,
        kospi_daily_pct=0.42,
        kospi_5d_pct=2.18,
        kospi_52w_ratio_pct=99.2,
        kosdaq_close=1210.55,
        kosdaq_daily_pct=0.31,
        kosdaq_5d_pct=1.05,
        vix=22.1,
        vkospi=None,
        vkospi_marker="(unavailable: KRX OpenAPI 401)",
        margin_jo=35.7,
        margin_stale=False,
        deposits_jo=124.8,
        deposits_stale=False,
        foreign_5d=-9430,
        institution_5d=3560,
        individual_5d=5870,
    )


# ---------------------------------------------------------------------------
# render_section — section header + robust fields + unavailable markers
# ---------------------------------------------------------------------------
class TestRenderSection:
    def test_section_header_present(self):
        out = km.render_section(_full_snapshot())
        assert "## 한국 시장 모멘텀" in out

    def test_robust_signals_rendered(self):
        out = km.render_section(_full_snapshot())
        assert "KOSPI" in out
        assert "KOSDAQ" in out
        assert "VIX" in out
        assert "99.2" in out  # 52-week ratio
        assert "+0.42%" in out

    def test_ecos_funds_rendered_in_jo(self):
        out = km.render_section(_full_snapshot())
        assert "35.7" in out  # margin 조원
        assert "124.8" in out  # deposits 조원

    def test_vkospi_unavailable_marker(self):
        out = km.render_section(_full_snapshot())
        assert "V-KOSPI" in out
        assert "unavailable" in out

    def test_missing_funds_show_unavailable(self):
        snap = _full_snapshot()
        snap = km.MomentumSnapshot(**{**snap.__dict__, "margin_jo": None})
        out = km.render_section(snap)
        assert "신용융자" in out
        assert "unavailable" in out

    def test_stale_funds_marked_stale(self):
        snap = _full_snapshot()
        snap = km.MomentumSnapshot(**{**snap.__dict__, "deposits_stale": True})
        out = km.render_section(snap)
        assert "stale" in out

    def test_render_never_raises_on_all_none(self):
        empty = km.MomentumSnapshot(
            kospi_close=None, kospi_daily_pct=None, kospi_5d_pct=None,
            kospi_52w_ratio_pct=None, kosdaq_close=None, kosdaq_daily_pct=None,
            kosdaq_5d_pct=None, vix=None, vkospi=None,
            vkospi_marker="(unavailable)", margin_jo=None, margin_stale=False,
            deposits_jo=None, deposits_stale=False, foreign_5d=None,
            institution_5d=None, individual_5d=None,
        )
        out = km.render_section(empty)
        assert "## 한국 시장 모멘텀" in out


# ---------------------------------------------------------------------------
# gather_momentum — graceful: all sub-fetches may fail, never raises
# ---------------------------------------------------------------------------
class TestGatherMomentum:
    def test_gather_never_raises_when_everything_fails(self):
        def _boom(*_a, **_k):
            raise RuntimeError("provider down")

        with (
            patch.object(km, "_gather_index_block", side_effect=_boom),
            patch.object(km, "_gather_flows_block", side_effect=_boom),
            patch.object(km, "latest_market_funds", side_effect=_boom),
            patch.object(km, "fetch_vkospi", side_effect=_boom),
            patch.object(km, "vkospi_marker", side_effect=_boom),
            patch.object(km, "_gather_vix", side_effect=_boom),
        ):
            snap = km.gather_momentum()
        # Snapshot returned with safe Nones; no exception escapes.
        assert snap.margin_jo is None
        assert snap.vkospi is None
        assert snap.kospi_daily_pct is None

    def test_gather_fills_funds_from_ecos(self):
        with (
            patch.object(km, "_gather_index_block", return_value={}),
            patch.object(km, "_gather_flows_block", return_value={}),
            patch.object(
                km,
                "latest_market_funds",
                return_value={
                    "margin_jo": 35.7, "margin_stale": False,
                    "deposits_jo": 124.8, "deposits_stale": False,
                },
            ),
            patch.object(km, "fetch_vkospi", return_value=None),
            patch.object(km, "vkospi_marker", return_value="(unavailable)"),
            patch.object(km, "_gather_vix", return_value=20.0),
        ):
            snap = km.gather_momentum()
        assert snap.margin_jo == 35.7
        assert snap.deposits_jo == 124.8
        assert snap.vix == 20.0
