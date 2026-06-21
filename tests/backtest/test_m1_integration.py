"""SPEC-TRADING-057 M1 — 실 KRX 네트워크 통합 테스트.

이 파일은 trading-app docker 컨테이너 내부에서만 실행한다.
호스트 환경(단위 테스트 CI)에서는 -m integration 마크 없이 실행 시 자동 스킵.

검증 대상:
- M1-6a: 2018-01-02 as-of-date 멤버십이 오늘과 다른지 (상폐 종목 차이)
- M1-6b: 상폐 종목 000030의 2018-01 OHLCV 회수 가능 여부
- REQ-057-M1-3: ts <= cutoff 불변식이 실제 KRX 데이터에서도 유지되는지
"""
from __future__ import annotations

import os
from datetime import date

import pytest

# 통합 테스트 전용 마커 — 컨테이너 환경에서만 실행
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def _require_krx():
    """KRX 세션 환경변수가 없으면 스킵."""
    if not os.environ.get("KRX_ID") or not os.environ.get("KRX_PW"):
        pytest.skip("KRX_ID/KRX_PW 환경변수 없음 — 컨테이너 전용 테스트")


class TestM16RealKrx:
    """실 KRX 네트워크로 M1-6 생존편향 게이트 검증."""

    def test_as_of_date_membership_differs_from_today(self, _require_krx):
        """2018-01-02 as-of-date 멤버십이 오늘과 다른지 확인 (상폐 종목 차이 증거)."""
        from trading.backtest.universe_reconstructor import reconstruct_universe

        r_2018 = reconstruct_universe(date(2018, 1, 2))
        r_today = reconstruct_universe(date.today())

        assert r_2018.achievable, "2018-01-02 as-of-date 재구성 실패"
        assert r_today.achievable, "오늘 as-of-date 재구성 실패"
        assert len(r_2018.tickers) > 0
        assert set(r_2018.tickers) != set(r_today.tickers), (
            "2018 유니버스와 오늘 유니버스가 동일 — as-of-date 지원 미작동 의심"
        )

    def test_delisted_ticker_ohlcv_retrievable(self, _require_krx):
        """상폐 종목 000030의 2018-01 OHLCV가 회수 가능한지 확인."""
        from trading.backtest.historical_loader import load_historical_ohlcv

        result = load_historical_ohlcv(
            tickers=["000030"],
            start=date(2018, 1, 2),
            end=date(2018, 1, 31),
            cutoff=date(2018, 1, 31),
        )

        bars = result.bars.get("000030", [])
        assert len(bars) > 0, "상폐 종목 000030 OHLCV 회수 실패 — M1-6a 달성 불가"

    def test_point_in_time_cutoff_holds_with_real_data(self, _require_krx):
        """실 데이터에서도 ts <= cutoff 불변식이 유지된다."""
        from trading.backtest.historical_loader import load_historical_ohlcv

        cutoff = date(2018, 1, 15)
        result = load_historical_ohlcv(
            tickers=["005380"],
            start=date(2018, 1, 2),
            end=date(2018, 1, 31),
            cutoff=cutoff,
        )

        for bar in result.bars.get("005380", []):
            assert bar["ts"] <= cutoff, f"미래 바 누출: ts={bar['ts']} > cutoff={cutoff}"
