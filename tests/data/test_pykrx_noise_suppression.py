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
        assert captured.out == "", f"stdout에 pykrx 소음이 잡힘 (억제 실패): {captured.out!r}"
        assert captured.err == "", f"stderr에 pykrx 소음이 잡힘 (억제 실패): {captured.err!r}"

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
        assert captured.out == "", f"stdout에 pykrx 소음 잡힘: {captured.out!r}"
        assert captured.err == "", f"stderr에 pykrx 소음 잡힘: {captured.err!r}"


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
        assert captured.out == "", f"stdout에 pykrx 소음 잡힘: {captured.out!r}"
        assert captured.err == "", f"stderr에 pykrx 소음 잡힘: {captured.err!r}"


class TestQuietPykrxHardening:
    """2026-07-02 인시던트 재현: fd 영구 devnull 26h + 소켓 타임아웃 미설정.

    _quiet_pykrx()가 다음 두 결함을 수정했는지 검증한다:
      1. 스레드 인터리브: T1 내부에서 T2가 진입하면 devnull fd를 "원본"으로
         저장 → T1 복원 → T2 복원(devnull) = fd 영구 devnull.
         수정: _QUIET_LOCK으로 진입을 직렬화.
      2. 소켓 타임아웃 미설정: pykrx 호출이 데드 소켓에서 무한 블로킹.
         수정: socket.setdefaulttimeout(_PYKRX_SOCKET_TIMEOUT_S) 후 복원.
    """

    def test_interleaved_threads_restore_fds(self):
        """스레드 인터리브 후 fd 1/2가 진입 전과 동일해야 한다.

        인시던트 재현 핵심: devnull을 "원본"으로 저장한 스레드(T2)가 **마지막에**
        가드를 빠져나가야 fd가 devnull로 영구 고정된다. 따라서 T2는 가드 안에서
        T1의 가드 종료(t1_exited)를 기다린 뒤에 빠져나가도록 순서를 강제한다.

        OLD CODE (락 없음): T1 안에서 T2 진입(devnull을 원본으로 저장) → T1 종료
        (실제 fd 복원) → T2 종료(devnull 복원) = fd 영구 devnull → FAIL.

        NEW CODE (락 있음): T2는 락에서 블로킹 → T1의 t2_entered.wait 가 2s
        타임아웃 후 T1 종료·t1_exited 설정 → T2 진입(실제 fd를 원본으로 저장)
        → 즉시 종료 → 올바르게 복원 → PASS. (데드락 없음)
        """
        import os
        import threading

        from trading.data.pykrx_adapter import _quiet_pykrx

        # 테스트 시작 전 fd 상태 스냅샷 (st_dev + st_ino로 동일성 판단)
        before_out = os.fstat(1)
        before_err = os.fstat(2)

        # 테스트 자체가 fd를 오염시킬 경우를 대비해 복원 저장
        saved_fd1 = os.dup(1)
        saved_fd2 = os.dup(2)

        t1_inside = threading.Event()
        t2_entered = threading.Event()
        t1_exited = threading.Event()

        def body_t1() -> None:
            with _quiet_pykrx():
                t1_inside.set()
                # T2가 진입 시도할 시간을 준다.
                # NEW CODE: T2는 락에서 블로킹 → t2_entered 미설정 → 타임아웃(정상).
                # OLD CODE: T2가 즉시 진입 → t2_entered 설정 → wait 반환.
                t2_entered.wait(timeout=2.0)
            t1_exited.set()

        def body_t2() -> None:
            t1_inside.wait(timeout=5.0)
            with _quiet_pykrx():  # NEW: T1 종료까지 블로킹. OLD: 즉시 진입(인터리브).
                t2_entered.set()
                # 인시던트 순서 강제: T1이 가드를 빠져나간 뒤에 T2가 마지막으로
                # 빠져나간다. OLD CODE에서는 이 시점의 복원이 devnull을 남긴다.
                t1_exited.wait(timeout=5.0)

        t1 = threading.Thread(target=body_t1, daemon=True)
        t2 = threading.Thread(target=body_t2, daemon=True)

        try:
            t1.start()
            t2.start()
            t1.join(timeout=8.0)
            t2.join(timeout=8.0)

            after_out = os.fstat(1)
            after_err = os.fstat(2)

            assert (after_out.st_dev, after_out.st_ino) == (
                before_out.st_dev,
                before_out.st_ino,
            ), (
                f"fd 1이 다른 파일을 가리킴: before={before_out.st_ino} after={after_out.st_ino}"
                " (인터리브로 인해 devnull에 영구 고정됐을 가능성)"
            )
            assert (after_err.st_dev, after_err.st_ino) == (
                before_err.st_dev,
                before_err.st_ino,
            ), f"fd 2가 다른 파일을 가리킴: before={before_err.st_ino} after={after_err.st_ino}"
        finally:
            # 테스트 실패 시에도 fd 복원하여 나머지 테스트 스위트 보호
            try:
                os.dup2(saved_fd1, 1)
                os.dup2(saved_fd2, 2)
            except OSError:
                pass
            finally:
                try:
                    os.close(saved_fd1)
                except OSError:
                    pass
                try:
                    os.close(saved_fd2)
                except OSError:
                    pass

    def test_reused_session_get_receives_injected_timeout(self, monkeypatch):
        """2026-07-06 재발 방지: pykrx 재사용 Session의 timeout 미지정 .get 이
        _quiet_pykrx() 안에서 per-request timeout을 주입받아야 한다.

        RED(수정 전): 주입 없음 → adapter.send 가 timeout=None 수신(무한 블로킹 위험).
        GREEN(수정 후): _sock_timeout_s 주입 → adapter.send 가 timeout=<값> 수신.
        """
        import requests

        from trading.data.pykrx_adapter import _quiet_pykrx

        monkeypatch.setenv("PYKRX_SOCKET_TIMEOUT", "8")
        captured: dict[str, object] = {}

        def fake_send(self, request, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            resp = requests.Response()
            resp.status_code = 200
            resp._content = b"{}"
            resp.url = request.url
            resp.request = request
            return resp

        monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)

        session = requests.Session()
        with _quiet_pykrx():
            session.get("http://example.invalid/data")

        assert captured.get("timeout") == 8.0, (
            f"재사용 Session .get 에 timeout 미주입(무한 블로킹 위험): {captured}"
        )

    def test_session_request_patch_removed_after_guard(self):
        """가드 종료 후 requests.Session.request 원복(패치 누수 없음)."""
        import requests

        from trading.data.pykrx_adapter import _quiet_pykrx

        before = requests.Session.request
        with _quiet_pykrx():
            assert requests.Session.request is not before, "가드 안에서 패치되지 않음"
        assert requests.Session.request is before, "가드 종료 후 request 패치 미원복"

    def test_socket_default_timeout_set_and_restored(self, monkeypatch):
        """_quiet_pykrx() 내부에서 socket.getdefaulttimeout()이 7.5s여야 한다.

        진입 전/후에는 기존 값(None 포함)이 복원되어야 한다.
        """
        import socket

        from trading.data.pykrx_adapter import _quiet_pykrx

        monkeypatch.setenv("PYKRX_SOCKET_TIMEOUT", "7.5")

        original_timeout = socket.getdefaulttimeout()
        timeout_inside: list[float | None] = []

        with _quiet_pykrx():
            timeout_inside.append(socket.getdefaulttimeout())

        assert timeout_inside == [7.5], (
            f"_quiet_pykrx() 내부 소켓 타임아웃이 7.5s가 아님: {timeout_inside}"
        )
        assert socket.getdefaulttimeout() == original_timeout, (
            f"_quiet_pykrx() 종료 후 소켓 타임아웃 미복원: "
            f"expected={original_timeout!r} got={socket.getdefaulttimeout()!r}"
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
