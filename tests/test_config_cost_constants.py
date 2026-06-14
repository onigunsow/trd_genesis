"""SPEC-TRADING-044 M1 — config.py 비용 단일 진실원천 + 2026 세율 보정 테스트.

AC-5: KOSPI 매도측 2026 구조(거래세 ≈0.05% + 농특세 0.15% ≈ 0.20% 합계),
fee/tax가 단일 명명 상수에서 1회 파생, 세율 변경이 한 줄 수정.
"""
from __future__ import annotations

import math


class TestKospi2026TaxCorrection:
    """KOSPI 매도측 2026 세율 보정 검증."""

    def test_kospi_broker_fee_exists_and_correct(self):
        """브로커 수수료 상수가 0.015% (0.00015) 로 정의됨."""
        from trading.config import KOSPI_BROKER_FEE
        assert math.isclose(KOSPI_BROKER_FEE, 0.00015, rel_tol=1e-9)

    def test_kospi_tx_tax_exists_and_correct(self):
        """2026 KOSPI 거래세 ≈0.05% (0.0005) — 기존 0.18% 에서 인하."""
        from trading.config import KOSPI_TX_TAX
        assert math.isclose(KOSPI_TX_TAX, 0.0005, rel_tol=1e-9)

    def test_kospi_rural_tax_exists_and_correct(self):
        """농어촌특별세 0.15% (0.0015)."""
        from trading.config import KOSPI_RURAL_TAX
        assert math.isclose(KOSPI_RURAL_TAX, 0.0015, rel_tol=1e-9)

    def test_live_fee_sell_kospi_is_derived_from_components(self):
        """LIVE_FEE_SELL_KOSPI = KOSPI_BROKER_FEE + KOSPI_TX_TAX + KOSPI_RURAL_TAX."""
        from trading.config import KOSPI_BROKER_FEE, KOSPI_RURAL_TAX, KOSPI_TX_TAX, LIVE_FEE_SELL_KOSPI
        expected = KOSPI_BROKER_FEE + KOSPI_TX_TAX + KOSPI_RURAL_TAX
        assert math.isclose(LIVE_FEE_SELL_KOSPI, expected, rel_tol=1e-9)

    def test_live_fee_sell_kospi_is_0_00215(self):
        """2026 보정: KOSPI 매도 합계 ≈ 0.215% = 0.00215 (기존 0.345% 에서 인하)."""
        from trading.config import LIVE_FEE_SELL_KOSPI
        assert math.isclose(LIVE_FEE_SELL_KOSPI, 0.00215, rel_tol=1e-6)

    def test_kospi_round_trip_cost_is_0_0023(self):
        """KOSPI round-trip = 매수 0.015% + 매도 0.215% ≈ 0.0023 (기존 ≈0.0036 에서 인하)."""
        from trading.config import LIVE_ROUND_TRIP_COST_KOSPI
        assert math.isclose(LIVE_ROUND_TRIP_COST_KOSPI, 0.0023, rel_tol=1e-6)

    def test_kospi_round_trip_derived_from_buy_plus_sell(self):
        """LIVE_ROUND_TRIP_COST_KOSPI = LIVE_FEE_BUY + LIVE_FEE_SELL_KOSPI."""
        from trading.config import LIVE_FEE_BUY, LIVE_FEE_SELL_KOSPI, LIVE_ROUND_TRIP_COST_KOSPI
        assert math.isclose(LIVE_ROUND_TRIP_COST_KOSPI, LIVE_FEE_BUY + LIVE_FEE_SELL_KOSPI, rel_tol=1e-9)


class TestKosdaq2026TaxCorrection:
    """KOSDAQ 매도측 2026 세율 검증 (농특세 없음)."""

    def test_kosdaq_tx_tax_exists_and_correct(self):
        """KOSDAQ 거래세 0.20% (0.002) — 농특세 없음."""
        from trading.config import KOSDAQ_TX_TAX
        assert math.isclose(KOSDAQ_TX_TAX, 0.002, rel_tol=1e-9)

    def test_live_fee_sell_kosdaq_is_derived_from_components(self):
        """LIVE_FEE_SELL_KOSDAQ = KOSPI_BROKER_FEE + KOSDAQ_TX_TAX (농특세 없음)."""
        from trading.config import KOSDAQ_TX_TAX, KOSPI_BROKER_FEE, LIVE_FEE_SELL_KOSDAQ
        expected = KOSPI_BROKER_FEE + KOSDAQ_TX_TAX
        assert math.isclose(LIVE_FEE_SELL_KOSDAQ, expected, rel_tol=1e-9)

    def test_live_fee_sell_kosdaq_is_0_00215(self):
        """2026 보정: KOSDAQ 매도 합계 ≈ 0.215% = 0.00215."""
        from trading.config import LIVE_FEE_SELL_KOSDAQ
        assert math.isclose(LIVE_FEE_SELL_KOSDAQ, 0.00215, rel_tol=1e-6)

    def test_kosdaq_round_trip_cost_is_0_0023(self):
        """KOSDAQ round-trip = 0.015% + 0.215% ≈ 0.0023."""
        from trading.config import LIVE_ROUND_TRIP_COST_KOSDAQ
        assert math.isclose(LIVE_ROUND_TRIP_COST_KOSDAQ, 0.0023, rel_tol=1e-6)

    def test_kosdaq_round_trip_derived_from_buy_plus_sell(self):
        """LIVE_ROUND_TRIP_COST_KOSDAQ = LIVE_FEE_BUY + LIVE_FEE_SELL_KOSDAQ."""
        from trading.config import LIVE_FEE_BUY, LIVE_FEE_SELL_KOSDAQ, LIVE_ROUND_TRIP_COST_KOSDAQ
        assert math.isclose(LIVE_ROUND_TRIP_COST_KOSDAQ, LIVE_FEE_BUY + LIVE_FEE_SELL_KOSDAQ, rel_tol=1e-9)


class TestSingleSourceOfTruth:
    """단일 진실원천 불변식 — 소비자 전원이 동일 값을 읽는지 검증."""

    def test_buy_fee_constant_is_0_00015(self):
        """매수 수수료는 config.py 에서 읽어야 한다 (0.015%)."""
        from trading.config import LIVE_FEE_BUY
        assert math.isclose(LIVE_FEE_BUY, 0.00015, rel_tol=1e-9)

    def test_estimate_fee_uses_corrected_constants(self):
        """estimate_fee() 가 보정된 config 상수를 통해 계산한다."""
        from trading.config import estimate_fee
        # KOSPI 매도 10,000,000 원 → 0.215% = 21,500원
        fee = estimate_fee(mode="live", side="sell", market="KOSPI", notional=10_000_000)
        assert fee == 21_500

    def test_estimate_fee_kosdaq_sell(self):
        """KOSDAQ 매도 10,000,000 원 → 0.215% = 21,500원."""
        from trading.config import estimate_fee
        fee = estimate_fee(mode="live", side="sell", market="KOSDAQ", notional=10_000_000)
        assert fee == 21_500

    def test_estimate_fee_buy_unchanged(self):
        """매수 수수료 변경 없음: 10,000,000원 → 0.015% = 1,500원."""
        from trading.config import estimate_fee
        fee = estimate_fee(mode="live", side="buy", market="KOSPI", notional=10_000_000)
        assert fee == 1_500
