"""pykrx 소음 억제 테스트 — RED-GREEN-REFACTOR.

pykrx는 KRX 세션 만료·재로그인 시도를 bare print()로 stdout에 출력하고,
내부 로거의 TypeError가 Python '--- Logging error ---' + 전체 트레이스백을
stderr에 쏟아낸다 (2026-06-25 실측: 17,046 로그라인/일, print x107 + 트레이스백 x321).

이 테스트는 _quiet_pykrx() 컨텍스트 매니저가:
  1. pykrx 호출 범위 내 stdout/stderr 소음을 완전 억제하는지
  2. 예외는 억제 없이 그대로 전파하는지
  3. 반환값이 변하지 않는지
를 검증한다.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv_df() -> pd.DataFrame:
    """테스트용 소형 OHLCV DataFrame 반환."""

    import pandas as pd

    dates = pd.to_datetime(["2026-06-24"])
    df = pd.DataFrame(
        {
            "시가": [70000],
            "고가": [71000],
            "저가": [69000],
            "종가": [70500],
            "거래량": [1000000],
        },
        index=dates,
    )
    return df


def _make_flows_df() -> pd.DataFrame:
    """테스트용 소형 flows DataFrame 반환."""
    import pandas as pd

    dates = pd.to_datetime(["2026-06-24"])
    df = pd.DataFrame(
        {
            "외국인합계": [500_000_000],
            "기관합계": [-200_000_000],
            "개인": [-300_000_000],
        },
        index=dates,
    )
    return df


# ---------------------------------------------------------------------------
# 1. fetch_ohlcv — pykrx print/stderr 소음 억제
# ---------------------------------------------------------------------------


class TestFetchOhlcvNoiseSuppression:
    """fetch_ohlcv() 가 _quiet_pykrx() 로 pykrx 소음을 억제해야 한다."""

    def test_stdout_and_stderr_empty_despite_pykrx_print(self, capsys, monkeypatch):
        """pykrx stub이 stdout·stderr에 소음을 뱉어도 capsys에는 아무것도 잡히지 않아야 한다.

        RED 조건: _quiet_pykrx() 없으면 capsys.out/err에 소음이 잡힘.
        GREEN 조건: _quiet_pykrx() 으로 감싸면 capsys.out/err 가 비어있음.
        """
        from datetime import date

        def _noisy_get_ohlcv(s, e, symbol):
            # pykrx 실제 패턴: bare print() 로 stdout에 출력
            print("KRX 로그인 시도...")
            print("KRX 세션 만료, 재로그인 시도...")
            # stderr 에도 쓰기 (broken logging 패턴 모방)
            sys.stderr.write("TypeError: not all arguments converted during string formatting\n")
            return _make_ohlcv_df()

        # pykrx.stock 전체를 mock으로 교체
        mock_stock = MagicMock()
        mock_stock.get_market_ohlcv_by_date.side_effect = _noisy_get_ohlcv
        monkeypatch.setitem(sys.modules, "pykrx", MagicMock(stock=mock_stock))
        monkeypatch.setitem(sys.modules, "pykrx.stock", mock_stock)

        # upsert_ohlcv stub — DB 미접촉
        with patch("trading.data.pykrx_adapter.upsert_ohlcv", return_value=1):
            from trading.data.pykrx_adapter import fetch_ohlcv

            result = fetch_ohlcv("005930", date(2026, 6, 24), date(2026, 6, 24))

        # 반환값은 정상이어야 함
        assert result == 1, f"upsert 결과가 다름: {result}"

        # stdout/stderr 에 pykrx 소음이 없어야 함
        captured = capsys.readouterr()
        assert captured.out == "", (
            f"stdout에 pykrx 소음이 잡힘 (억제 실패): {captured.out!r}"
        )
        assert captured.err == "", (
            f"stderr에 pykrx 소음이 잡힘 (억제 실패): {captured.err!r}"
        )

    def test_exception_propagates_unchanged(self, monkeypatch):
        """pykrx가 예외를 raise하면 _quiet_pykrx() 가 삼키지 않고 그대로 전파해야 한다.

        RED 조건: 억제 CM이 예외를 잡으면 이 테스트가 실패.
        GREEN 조건: 억제 CM은 예외를 삼키지 않으므로 ValueError가 전파됨.
        """
        from datetime import date

        def _raises(s, e, symbol):
            raise ValueError("boom — KRX unreachable")

        mock_stock = MagicMock()
        mock_stock.get_market_ohlcv_by_date.side_effect = _raises
        monkeypatch.setitem(sys.modules, "pykrx", MagicMock(stock=mock_stock))
        monkeypatch.setitem(sys.modules, "pykrx.stock", mock_stock)

        with patch("trading.data.pykrx_adapter.upsert_ohlcv", return_value=0):
            from trading.data.pykrx_adapter import fetch_ohlcv

            with pytest.raises(ValueError, match="boom"):
                fetch_ohlcv("005930", date(2026, 6, 24), date(2026, 6, 24))


# ---------------------------------------------------------------------------
# 2. fetch_flows — pykrx print/stderr 소음 억제
# ---------------------------------------------------------------------------


class TestFetchFlowsNoiseSuppression:
    """fetch_flows() 가 _quiet_pykrx() 로 pykrx 소음을 억제해야 한다."""

    def test_stdout_and_stderr_empty_despite_pykrx_print(self, capsys, monkeypatch):
        """fetch_flows: pykrx 소음이 capsys에 잡히지 않아야 한다."""
        from datetime import date

        def _noisy_get_flows(s, e, symbol):
            print("KRX 로그인 시도...")
            sys.stderr.write("--- Logging error ---\nTraceback (most recent call last)\n")
            return _make_flows_df()

        mock_stock = MagicMock()
        mock_stock.get_market_trading_value_by_date.side_effect = _noisy_get_flows
        monkeypatch.setitem(sys.modules, "pykrx", MagicMock(stock=mock_stock))
        monkeypatch.setitem(sys.modules, "pykrx.stock", mock_stock)

        with patch("trading.data.pykrx_adapter.upsert_flows", return_value=1):
            from trading.data.pykrx_adapter import fetch_flows

            result = fetch_flows("005930", date(2026, 6, 24), date(2026, 6, 24))

        assert result == 1
        captured = capsys.readouterr()
        assert captured.out == "", (
            f"stdout에 pykrx 소음 잡힘: {captured.out!r}"
        )
        assert captured.err == "", (
            f"stderr에 pykrx 소음 잡힘: {captured.err!r}"
        )


# ---------------------------------------------------------------------------
# 3. _fetch_kospi200_from_pykrx — pykrx print 소음 억제
# ---------------------------------------------------------------------------


class TestFetchKospi200NoiseSuppression:
    """_fetch_kospi200_from_pykrx() 가 _quiet_pykrx() 로 pykrx 소음을 억제해야 한다."""

    def test_stdout_empty_despite_pykrx_print_and_list_returned(self, capsys, monkeypatch):
        """_fetch_kospi200_from_pykrx: pykrx 소음 억제 + 리스트 정상 반환.

        RED 조건: _quiet_pykrx() 가 없으면 capsys.out 에 소음이 잡힘.
        GREEN 조건: _quiet_pykrx() 로 감싸면 capsys.out 가 비어있고 list 반환.
        """
        tickers = ["005930", "000660", "207940"]

        def _noisy_deposit_file(index_code):
            # pykrx 실제 인증 실패 시 패턴
            print(f"KRX 로그인 시도... (index={index_code})")
            print("KRX 세션 만료, 재로그인 시도...")
            sys.stderr.write("TypeError: not all arguments converted\n")
            return tickers

        mock_stock = MagicMock()
        mock_stock.get_index_portfolio_deposit_file.side_effect = _noisy_deposit_file
        monkeypatch.setitem(sys.modules, "pykrx", MagicMock(stock=mock_stock))
        monkeypatch.setitem(sys.modules, "pykrx.stock", mock_stock)

        from trading.data.universe import _fetch_kospi200_from_pykrx

        result = _fetch_kospi200_from_pykrx()

        assert result == tickers, f"반환값 다름: {result}"

        captured = capsys.readouterr()
        assert captured.out == "", (
            f"stdout에 pykrx 소음 잡힘: {captured.out!r}"
        )
        assert captured.err == "", (
            f"stderr에 pykrx 소음 잡힘: {captured.err!r}"
        )


class TestSilencePykrxAuthPrints:
    """_silence_pykrx_auth_prints — auth 모듈 bare print 출처 침묵화 검증."""

    def test_auth_print_replaced_with_noop(self, capsys):
        """auth 모듈에 no-op print 가 주입되어 호출해도 출력이 없어야 한다.

        실 pykrx import 의 import-time 로그인 print 오염을 피하려고 가짜 auth
        모듈을 sys.modules 에 주입한 뒤 침묵화 함수가 print 를 치환하는지 본다.
        """
        import sys as _sys
        import types

        fake_auth = types.ModuleType("pykrx.website.comm.auth")

        def _real_print(*args, **kwargs):
            print("KRX 로그인 시도...")

        fake_auth.print = _real_print  # type: ignore[attr-defined]
        fake_comm = types.ModuleType("pykrx.website.comm")
        fake_comm.auth = fake_auth  # type: ignore[attr-defined]

        with patch.dict(
            _sys.modules,
            {
                "pykrx.website.comm": fake_comm,
                "pykrx.website.comm.auth": fake_auth,
            },
        ):
            from trading.data.pykrx_adapter import (
                _silence_pykrx_auth_prints,
            )

            _silence_pykrx_auth_prints()
            # 치환 후 호출 — 아무 출력도 없어야 함
            fake_auth.print("KRX 세션 만료, 재로그인 시도...")

        captured = capsys.readouterr()
        assert captured.out == "", f"auth print 침묵화 실패: {captured.out!r}"
        assert fake_auth.print("x") is None

    def test_silence_is_graceful_when_auth_missing(self):
        """auth 모듈을 import 할 수 없어도 예외 없이 graceful skip."""
        import sys as _sys

        with patch.dict(_sys.modules, {"pykrx.website.comm.auth": None}):
            from trading.data.pykrx_adapter import (
                _silence_pykrx_auth_prints,
            )

            # 예외 전파 없이 조용히 통과해야 함
            _silence_pykrx_auth_prints()
