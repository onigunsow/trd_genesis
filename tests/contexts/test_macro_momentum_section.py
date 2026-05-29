"""SPEC-TRADING-036 REQ-036-1 — build_macro_context momentum section tests.

AC: the built macro_context contains the ``## 한국 시장 모멘텀`` section, and a
forced fetch failure still produces the section with ``(unavailable)`` markers
without aborting the build.

@MX:SPEC: SPEC-TRADING-036
"""

from __future__ import annotations

from unittest.mock import patch

from trading.contexts import build_macro_context as bmc
from trading.data import korea_momentum as km


def _snap(**over):
    base = dict(
        kospi_close=7498.34, kospi_daily_pct=0.42, kospi_5d_pct=2.18,
        kospi_52w_ratio_pct=99.2, kosdaq_close=1210.55, kosdaq_daily_pct=0.31,
        kosdaq_5d_pct=1.05, vix=22.1, vkospi=None,
        vkospi_marker="(unavailable: KRX OpenAPI 401)", margin_jo=35.7,
        margin_stale=False, deposits_jo=124.8, deposits_stale=False,
        foreign_5d=-9430, institution_5d=3560, individual_5d=5870,
    )
    base.update(over)
    return km.MomentumSnapshot(**base)


class TestMacroContextMomentumSection:
    def test_section_present_in_build(self):
        with (
            patch.object(bmc, "_latest_macro_table", return_value="_(macro)_"),
            patch.object(bmc, "_global_assets_table", return_value="_(global)_"),
            patch.object(bmc, "_korea_market_table", return_value="_(korea)_"),
            patch.object(bmc, "gather_momentum", return_value=_snap()),
        ):
            out = bmc.build()
        assert "## 한국 시장 모멘텀" in out
        assert "35.7조원" in out

    def test_build_survives_momentum_failure(self):
        def _boom():
            raise RuntimeError("momentum down")

        with (
            patch.object(bmc, "_latest_macro_table", return_value="_(macro)_"),
            patch.object(bmc, "_global_assets_table", return_value="_(global)_"),
            patch.object(bmc, "_korea_market_table", return_value="_(korea)_"),
            patch.object(bmc, "gather_momentum", side_effect=_boom),
        ):
            out = bmc.build()
        # Build still produces a document with the section header (graceful).
        assert "## 한국 시장 모멘텀" in out
        assert "Macro Context" in out

    def test_vkospi_unavailable_marker_in_build(self):
        with (
            patch.object(bmc, "_latest_macro_table", return_value="_(macro)_"),
            patch.object(bmc, "_global_assets_table", return_value="_(global)_"),
            patch.object(bmc, "_korea_market_table", return_value="_(korea)_"),
            patch.object(bmc, "gather_momentum", return_value=_snap()),
        ):
            out = bmc.build()
        assert "V-KOSPI" in out
        assert "unavailable" in out
