"""KRX 서킷 브레이커 RED-first 테스트.

근본 원인: KRX 데이터 포털이 IP를 403 차단 — pykrx가 실패해도 스케줄러가
고정 주기로 계속 호출하고, pykrx가 실패할 때마다 공격적으로 재로그인하여
죽은 엔드포인트를 hammer함.

서킷 브레이커 목표:
  - 연속 실패 ≥ 임계 → OPEN (pykrx 호출 차단)
  - OPEN 상태 지수 백오프: 15m → 1h → 6h → 24h (상한)
  - half-open: open_until 경과 후 1회 probe → 성공=close, 실패=더 긴 쿨다운
  - closed→open 전이 시 텔레그램 알림 정확히 1회
  - 배치 시작 시 서킷 확인 → OPEN이면 종목 루프 없이 조기 종료
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 헬퍼: 고정 시각 공급자
# ---------------------------------------------------------------------------

def _fixed_now(dt: datetime):
    """테스트 seam — 고정된 현재 시각 반환."""
    return lambda: dt


def _utc(hour: int = 12, minute: int = 0) -> datetime:
    return datetime(2026, 6, 28, hour, minute, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 1. 연속 실패 → OPEN 전이
# ---------------------------------------------------------------------------


class TestCircuitOpen:
    """연속 실패 N회 → 서킷 OPEN."""

    def test_consecutive_failures_reach_threshold_opens_circuit(self):
        """연속 실패 3회(기본 임계) 후 서킷이 OPEN 상태가 돼야 한다."""
        from trading.data.krx_circuit_breaker import CircuitState, KrxCircuitBreaker

        cb = KrxCircuitBreaker(failure_threshold=3)
        assert cb.state == CircuitState.CLOSED

        for _ in range(2):
            cb.record_failure(now=_utc())

        # 2회 실패 → 아직 CLOSED
        assert cb.state == CircuitState.CLOSED

        # 3번째 실패 → OPEN
        cb.record_failure(now=_utc())
        assert cb.state == CircuitState.OPEN

    def test_failure_below_threshold_keeps_circuit_closed(self):
        """임계 미달 실패는 서킷을 닫아둔다."""
        from trading.data.krx_circuit_breaker import CircuitState, KrxCircuitBreaker

        cb = KrxCircuitBreaker(failure_threshold=3)
        cb.record_failure(now=_utc())
        assert cb.state == CircuitState.CLOSED

    def test_success_resets_consecutive_failure_counter(self):
        """성공이 연속 실패 카운터를 0으로 리셋한다."""
        from trading.data.krx_circuit_breaker import CircuitState, KrxCircuitBreaker

        cb = KrxCircuitBreaker(failure_threshold=3)
        cb.record_failure(now=_utc())
        cb.record_failure(now=_utc())
        cb.record_success(now=_utc())  # 카운터 리셋
        cb.record_failure(now=_utc())  # 리셋 후 1번째 실패

        # 임계(3) 미달이므로 CLOSED
        assert cb.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# 2. OPEN 상태에서 pykrx 호출 차단 (스파이로 미호출 단언)
# ---------------------------------------------------------------------------


class TestCircuitBlocksCall:
    """OPEN 상태에서 pykrx를 호출하지 않고 KrxCircuitOpen 예외 발생."""

    def test_open_circuit_raises_krx_circuit_open_without_calling_pykrx(self):
        """서킷 OPEN 상태에서 check_or_raise()는 pykrx 미호출, 예외 발생."""
        from trading.data.krx_circuit_breaker import (
            KrxCircuitBreaker,
            KrxCircuitOpen,
        )

        cb = KrxCircuitBreaker(failure_threshold=1)
        cb.record_failure(now=_utc())  # 즉시 OPEN

        pykrx_stock = MagicMock()

        with pytest.raises(KrxCircuitOpen):
            cb.check_or_raise(now=_utc())

        # pykrx_stock.get_* 미호출 (서킷이 예외를 먼저 던지므로)
        pykrx_stock.get_market_ohlcv_by_date.assert_not_called()

    def test_closed_circuit_does_not_raise(self):
        """CLOSED 상태에서 check_or_raise()는 예외 없이 통과."""
        from trading.data.krx_circuit_breaker import KrxCircuitBreaker

        cb = KrxCircuitBreaker(failure_threshold=3)
        # 예외 없음
        cb.check_or_raise(now=_utc())

    def test_pykrx_adapter_fetch_ohlcv_skips_when_circuit_open(self):
        """fetch_ohlcv()가 서킷 OPEN이면 KrxCircuitOpen을 즉시 발생시킨다.

        check_or_raise()가 예외를 던지므로 이후 pykrx stock.get_* 호출은
        도달하지 않는다 — 실행 흐름 차단으로 미호출을 보장.
        """
        from datetime import date

        from trading.data import krx_circuit_breaker as cb_mod
        from trading.data import pykrx_adapter

        mock_cb = MagicMock()
        mock_cb.check_or_raise.side_effect = cb_mod.KrxCircuitOpen("open")

        # pykrx lazy-import를 통째로 차단해 실제 KRX 호출 불가 확인
        mock_pykrx_stock = MagicMock()

        with (
            patch.object(cb_mod, "_get_shared_breaker", return_value=mock_cb),
            patch.dict("sys.modules", {"pykrx": MagicMock(), "pykrx.stock": mock_pykrx_stock}),
        ):
            with pytest.raises(cb_mod.KrxCircuitOpen):
                pykrx_adapter.fetch_ohlcv(
                    "005930", date(2026, 1, 1), date(2026, 6, 1)
                )

            # check_or_raise가 예외를 던져 stock 코드에 미도달
            mock_pykrx_stock.get_market_ohlcv_by_date.assert_not_called()

    def test_pykrx_adapter_fetch_flows_skips_when_circuit_open(self):
        """fetch_flows()가 서킷 OPEN이면 KrxCircuitOpen을 즉시 발생시킨다."""
        from datetime import date

        from trading.data import krx_circuit_breaker as cb_mod
        from trading.data import pykrx_adapter

        mock_cb = MagicMock()
        mock_cb.check_or_raise.side_effect = cb_mod.KrxCircuitOpen("open")

        mock_pykrx_stock = MagicMock()

        with (
            patch.object(cb_mod, "_get_shared_breaker", return_value=mock_cb),
            patch.dict("sys.modules", {"pykrx": MagicMock(), "pykrx.stock": mock_pykrx_stock}),
        ):
            with pytest.raises(cb_mod.KrxCircuitOpen):
                pykrx_adapter.fetch_flows(
                    "005930", date(2026, 1, 1), date(2026, 6, 1)
                )

            mock_pykrx_stock.get_market_trading_value_by_date.assert_not_called()

    def test_pykrx_adapter_fetch_fundamentals_skips_when_circuit_open(self):
        """fetch_fundamentals()가 서킷 OPEN이면 KrxCircuitOpen을 즉시 발생시킨다."""
        from datetime import date

        from trading.data import krx_circuit_breaker as cb_mod
        from trading.data import pykrx_adapter

        mock_cb = MagicMock()
        mock_cb.check_or_raise.side_effect = cb_mod.KrxCircuitOpen("open")

        mock_pykrx_stock = MagicMock()

        with (
            patch.object(cb_mod, "_get_shared_breaker", return_value=mock_cb),
            patch.dict("sys.modules", {"pykrx": MagicMock(), "pykrx.stock": mock_pykrx_stock}),
        ):
            with pytest.raises(cb_mod.KrxCircuitOpen):
                pykrx_adapter.fetch_fundamentals(
                    "005930", date(2026, 1, 1), date(2026, 6, 1)
                )

            mock_pykrx_stock.get_market_fundamental_by_date.assert_not_called()


# ---------------------------------------------------------------------------
# 3. 지수 백오프 쿨다운 검증
# ---------------------------------------------------------------------------


class TestExponentialBackoff:
    """쿨다운이 지수적으로 증가: 15m → 1h → 6h → 24h(상한)."""

    def test_first_open_cooldown_is_15_minutes(self):
        """첫 번째 OPEN: 쿨다운 15분."""
        from trading.data.krx_circuit_breaker import KrxCircuitBreaker

        now = _utc(12)
        cb = KrxCircuitBreaker(failure_threshold=1)
        cb.record_failure(now=now)

        assert cb.state.name == "OPEN"
        expected_until = now + timedelta(minutes=15)
        assert cb.open_until is not None
        # 15분 이내 ±1초 허용
        diff = abs((cb.open_until - expected_until).total_seconds())
        assert diff < 2, f"첫 쿨다운이 15분이어야 하는데 {cb.open_until}"

    def test_second_open_cooldown_is_1_hour(self):
        """두 번째 OPEN(half-open probe 실패): 쿨다운 1시간."""
        from trading.data.krx_circuit_breaker import KrxCircuitBreaker

        t0 = _utc(10)
        cb = KrxCircuitBreaker(failure_threshold=1)

        # 첫 번째 OPEN
        cb.record_failure(now=t0)

        # 15분 경과 후 half-open probe → 실패 → 두 번째 OPEN
        t1 = t0 + timedelta(minutes=16)
        cb.record_failure(now=t1)

        expected_until = t1 + timedelta(hours=1)
        diff = abs((cb.open_until - expected_until).total_seconds())
        assert diff < 2, f"두 번째 쿨다운이 1시간이어야 하는데 {cb.open_until}"

    def test_third_open_cooldown_is_6_hours(self):
        """세 번째 OPEN: 쿨다운 6시간."""
        from trading.data.krx_circuit_breaker import KrxCircuitBreaker

        t0 = _utc(8)
        cb = KrxCircuitBreaker(failure_threshold=1)

        cb.record_failure(now=t0)                           # 1회: 15m
        cb.record_failure(now=t0 + timedelta(minutes=16))   # 2회: 1h
        cb.record_failure(now=t0 + timedelta(hours=2))      # 3회: 6h

        t3 = t0 + timedelta(hours=2)
        expected_until = t3 + timedelta(hours=6)
        diff = abs((cb.open_until - expected_until).total_seconds())
        assert diff < 2, f"세 번째 쿨다운이 6시간이어야 하는데 {cb.open_until}"

    def test_fourth_open_cooldown_capped_at_24_hours(self):
        """네 번째 이상 OPEN: 쿨다운 상한 24시간."""
        from trading.data.krx_circuit_breaker import KrxCircuitBreaker

        t0 = _utc(6)
        cb = KrxCircuitBreaker(failure_threshold=1)

        cb.record_failure(now=t0)                           # 1회: 15m
        cb.record_failure(now=t0 + timedelta(minutes=16))   # 2회: 1h
        cb.record_failure(now=t0 + timedelta(hours=2))      # 3회: 6h
        cb.record_failure(now=t0 + timedelta(hours=9))      # 4회: 24h (상한)

        t4 = t0 + timedelta(hours=9)
        expected_until = t4 + timedelta(hours=24)
        diff = abs((cb.open_until - expected_until).total_seconds())
        assert diff < 2, f"네 번째 쿨다운 상한이 24시간이어야 하는데 {cb.open_until}"

    def test_fifth_open_still_capped_at_24_hours(self):
        """다섯 번째 이상도 상한 24시간을 넘지 않는다."""
        from trading.data.krx_circuit_breaker import KrxCircuitBreaker

        t0 = _utc(0)
        cb = KrxCircuitBreaker(failure_threshold=1)

        for i in range(5):
            cb.record_failure(now=t0 + timedelta(hours=i * 25))

        tn = t0 + timedelta(hours=4 * 25)
        expected_until = tn + timedelta(hours=24)
        diff = abs((cb.open_until - expected_until).total_seconds())
        assert diff < 2


# ---------------------------------------------------------------------------
# 4. half-open: 쿨다운 경과 후 1회 probe
# ---------------------------------------------------------------------------


class TestHalfOpen:
    """half-open: open_until 경과 시 정확히 1회 probe 허용."""

    def test_after_cooldown_circuit_allows_one_probe(self):
        """open_until 경과 → check_or_raise()가 HALF_OPEN으로 1회 통과."""
        from trading.data.krx_circuit_breaker import CircuitState, KrxCircuitBreaker

        t0 = _utc(10)
        cb = KrxCircuitBreaker(failure_threshold=1)
        cb.record_failure(now=t0)
        assert cb.state == CircuitState.OPEN

        # 16분 경과 → half-open probe 허용
        t1 = t0 + timedelta(minutes=16)
        # 예외 없이 통과해야 함
        cb.check_or_raise(now=t1)

    def test_half_open_probe_success_closes_circuit(self):
        """half-open probe 성공 → 서킷 CLOSED, 카운터 리셋."""
        from trading.data.krx_circuit_breaker import CircuitState, KrxCircuitBreaker

        t0 = _utc(10)
        cb = KrxCircuitBreaker(failure_threshold=1)
        cb.record_failure(now=t0)

        t1 = t0 + timedelta(minutes=16)
        cb.check_or_raise(now=t1)   # probe 진입
        cb.record_success(now=t1)   # 성공 → CLOSED

        assert cb.state == CircuitState.CLOSED
        # CLOSED이므로 추가 호출도 예외 없음
        cb.check_or_raise(now=t1)

    def test_half_open_probe_failure_reopens_with_longer_cooldown(self):
        """half-open probe 실패 → 더 긴 쿨다운으로 re-open."""
        from trading.data.krx_circuit_breaker import CircuitState, KrxCircuitBreaker

        t0 = _utc(10)
        cb = KrxCircuitBreaker(failure_threshold=1)
        cb.record_failure(now=t0)  # 첫 OPEN: 쿨다운 15m

        t1 = t0 + timedelta(minutes=16)
        cb.check_or_raise(now=t1)  # probe 진입 (HALF_OPEN)
        cb.record_failure(now=t1)  # 실패 → re-open

        assert cb.state == CircuitState.OPEN
        # 두 번째 쿨다운 = 1시간
        expected_until = t1 + timedelta(hours=1)
        diff = abs((cb.open_until - expected_until).total_seconds())
        assert diff < 2, f"re-open 쿨다운이 1시간이어야 하는데 {cb.open_until}"

    def test_open_within_cooldown_still_raises(self):
        """쿨다운 미경과 → check_or_raise()가 여전히 KrxCircuitOpen 발생."""
        from trading.data.krx_circuit_breaker import (
            KrxCircuitBreaker,
            KrxCircuitOpen,
        )

        t0 = _utc(10)
        cb = KrxCircuitBreaker(failure_threshold=1)
        cb.record_failure(now=t0)

        # 14분 경과 (쿨다운 15분 미경과)
        t1 = t0 + timedelta(minutes=14)
        with pytest.raises(KrxCircuitOpen):
            cb.check_or_raise(now=t1)


# ---------------------------------------------------------------------------
# 5. 텔레그램 알림 정확히 1회 (closed→open 전이 시)
# ---------------------------------------------------------------------------


class TestTelegramAlert:
    """closed→open 전이 시 텔레그램 알림 정확히 1회."""

    def test_telegram_notified_exactly_once_on_closed_to_open(self):
        """서킷이 closed→open 될 때 텔레그램 알림 정확히 1회."""
        from trading.data.krx_circuit_breaker import KrxCircuitBreaker

        mock_briefing = MagicMock()
        cb = KrxCircuitBreaker(failure_threshold=2, _notify_fn=mock_briefing)

        t = _utc(12)
        cb.record_failure(now=t)  # 1회 실패 (CLOSED)
        mock_briefing.assert_not_called()

        cb.record_failure(now=t)  # 2회 실패 → OPEN
        mock_briefing.assert_called_once()

    def test_telegram_not_called_again_on_subsequent_failures_while_open(self):
        """OPEN 상태 유지 중 추가 실패(half-open re-open)에는 알림 없음."""
        from trading.data.krx_circuit_breaker import KrxCircuitBreaker

        mock_briefing = MagicMock()
        cb = KrxCircuitBreaker(failure_threshold=1, _notify_fn=mock_briefing)

        t0 = _utc(10)
        cb.record_failure(now=t0)  # OPEN → 알림 1회
        assert mock_briefing.call_count == 1

        # half-open probe 실패 → re-open이지만 이미 open 에피소드 중
        t1 = t0 + timedelta(minutes=16)
        cb.check_or_raise(now=t1)
        cb.record_failure(now=t1)  # re-open
        # 추가 알림 없음
        assert mock_briefing.call_count == 1

    def test_telegram_called_again_after_circuit_closes_and_reopens(self):
        """close 후 재open 시 알림 다시 1회 발생."""
        from trading.data.krx_circuit_breaker import KrxCircuitBreaker

        mock_briefing = MagicMock()
        cb = KrxCircuitBreaker(failure_threshold=1, _notify_fn=mock_briefing)

        t0 = _utc(10)
        cb.record_failure(now=t0)  # 첫 OPEN → 알림 1회

        # half-open close
        t1 = t0 + timedelta(minutes=16)
        cb.check_or_raise(now=t1)
        cb.record_success(now=t1)  # CLOSED

        # 새 에피소드
        cb.record_failure(now=t1)  # 두 번째 OPEN → 알림 다시 1회
        assert mock_briefing.call_count == 2


# ---------------------------------------------------------------------------
# 6. 배치 조기 종료 — per-ticker 호출 0, 로그 1줄
# ---------------------------------------------------------------------------


class TestBatchEarlyExit:
    """refresh_ohlcv/flows/fundamentals — OPEN이면 종목 루프 없이 조기 종료."""

    def test_refresh_ohlcv_skips_ticker_loop_when_circuit_open(self, caplog):
        """서킷 OPEN이면 refresh_ohlcv()가 ticker 루프를 돌지 않는다."""
        import logging

        from trading.data import krx_circuit_breaker as cb_mod
        from trading.scripts import refresh_market_data as mod

        mock_cb = MagicMock()
        mock_cb.check_or_raise.side_effect = cb_mod.KrxCircuitOpen(
            "open until 2026-06-28T14:00:00"
        )

        universe = ["005930", "000660", "068270"]
        per_ticker_spy = MagicMock(return_value=5)

        with caplog.at_level(logging.WARNING):
            with (
                patch.object(cb_mod, "_get_shared_breaker", return_value=mock_cb),
                patch.object(mod, "get_data_universe", return_value=universe),
                patch.object(mod, "_fetch_ohlcv_for_ticker", per_ticker_spy),
            ):
                metrics = mod.refresh_ohlcv()

        # per-ticker 호출 0회
        per_ticker_spy.assert_not_called()
        # 메트릭에서 조기 종료 표시
        assert metrics.get("circuit_open") is True
        # 로그 1줄 (스팸 없음) — "서킷" 또는 "circuit" 키워드 포함 여부
        assert "서킷" in caplog.text or "circuit" in caplog.text.lower(), (
            f"서킷 관련 로그가 없음. caplog.text={caplog.text!r}"
        )

    def test_refresh_flows_skips_ticker_loop_when_circuit_open(self, caplog):
        """서킷 OPEN이면 refresh_flows()가 ticker 루프를 돌지 않는다."""
        from trading.data import krx_circuit_breaker as cb_mod
        from trading.scripts import refresh_market_data as mod

        mock_cb = MagicMock()
        mock_cb.check_or_raise.side_effect = cb_mod.KrxCircuitOpen("open")

        per_ticker_spy = MagicMock(return_value=5)

        with (
            patch.object(cb_mod, "_get_shared_breaker", return_value=mock_cb),
            patch.object(mod, "get_data_universe", return_value=["A", "B"]),
            patch.object(mod, "_fetch_flows_for_ticker", per_ticker_spy),
            caplog.at_level("WARNING"),
        ):
            metrics = mod.refresh_flows()

        per_ticker_spy.assert_not_called()
        assert metrics.get("circuit_open") is True

    def test_refresh_fundamentals_skips_ticker_loop_when_circuit_open(self, caplog):
        """서킷 OPEN이면 refresh_fundamentals()가 ticker 루프를 돌지 않는다."""
        from trading.data import krx_circuit_breaker as cb_mod
        from trading.scripts import refresh_market_data as mod

        mock_cb = MagicMock()
        mock_cb.check_or_raise.side_effect = cb_mod.KrxCircuitOpen("open")

        per_ticker_spy = MagicMock(return_value=5)

        with (
            patch.object(cb_mod, "_get_shared_breaker", return_value=mock_cb),
            patch.object(mod, "get_data_universe", return_value=["A", "B"]),
            patch.object(mod, "_fetch_fundamentals_for_ticker", per_ticker_spy),
            caplog.at_level("WARNING"),
        ):
            metrics = mod.refresh_fundamentals()

        per_ticker_spy.assert_not_called()
        assert metrics.get("circuit_open") is True

    def test_refresh_ohlcv_proceeds_normally_when_circuit_closed(self):
        """서킷 CLOSED이면 refresh_ohlcv()가 정상 ticker 루프를 실행한다."""
        from trading.data import krx_circuit_breaker as cb_mod
        from trading.scripts import refresh_market_data as mod

        mock_cb = MagicMock()
        mock_cb.check_or_raise.return_value = None  # CLOSED: 예외 없음

        per_ticker_spy = MagicMock(return_value=5)

        with (
            patch.object(cb_mod, "_get_shared_breaker", return_value=mock_cb),
            patch.object(mod, "get_data_universe", return_value=["005930", "000660"]),
            patch.object(mod, "_fetch_ohlcv_for_ticker", per_ticker_spy),
        ):
            metrics = mod.refresh_ohlcv()

        assert per_ticker_spy.call_count == 2
        assert metrics.get("circuit_open") is not True


# ---------------------------------------------------------------------------
# 7. universe._fetch_kospi200_from_pykrx 가드
# ---------------------------------------------------------------------------


class TestUniverseKrxGuard:
    """universe._fetch_kospi200_from_pykrx도 서킷 OPEN이면 단락."""

    def test_fetch_kospi200_from_pykrx_raises_when_circuit_open(self):
        """서킷 OPEN이면 _fetch_kospi200_from_pykrx가 KrxCircuitOpen을 발생시킨다."""
        from trading.data import krx_circuit_breaker as cb_mod
        from trading.data import universe

        mock_cb = MagicMock()
        mock_cb.check_or_raise.side_effect = cb_mod.KrxCircuitOpen("open")

        mock_pykrx_stock = MagicMock()

        with (
            patch.object(cb_mod, "_get_shared_breaker", return_value=mock_cb),
            patch.dict("sys.modules", {"pykrx": MagicMock(), "pykrx.stock": mock_pykrx_stock}),
        ):
            with pytest.raises(cb_mod.KrxCircuitOpen):
                universe._fetch_kospi200_from_pykrx()

            mock_pykrx_stock.get_index_portfolio_deposit_file.assert_not_called()

    def test_fetch_kospi200_proceeds_when_circuit_closed(self):
        """서킷 CLOSED이면 _fetch_kospi200_from_pykrx가 정상 실행된다.

        _quiet_pykrx 와 stock.get_index_portfolio_deposit_file 을 mock해
        실제 KRX 네트워크 호출 없이 CLOSED 경로를 검증한다.
        """
        from contextlib import contextmanager

        from trading.data import krx_circuit_breaker as cb_mod
        from trading.data import pykrx_adapter, universe

        mock_cb = MagicMock()
        mock_cb.check_or_raise.return_value = None  # CLOSED
        mock_cb.record_success = MagicMock()
        mock_cb.record_failure = MagicMock()

        mock_stock = MagicMock()
        mock_stock.get_index_portfolio_deposit_file.return_value = ["005930"]

        @contextmanager
        def _noop_quiet():
            yield

        with (
            patch.object(cb_mod, "_get_shared_breaker", return_value=mock_cb),
            patch.object(pykrx_adapter, "_quiet_pykrx", _noop_quiet),
        ):
            # stock lazy import를 대체: pykrx 모듈 자체를 mock
            import sys
            mock_pykrx = MagicMock()
            mock_pykrx.stock = mock_stock
            original = sys.modules.get("pykrx")
            sys.modules["pykrx"] = mock_pykrx
            sys.modules["pykrx.stock"] = mock_stock
            try:
                result = universe._fetch_kospi200_from_pykrx()
            finally:
                if original is None:
                    sys.modules.pop("pykrx", None)
                    sys.modules.pop("pykrx.stock", None)
                else:
                    sys.modules["pykrx"] = original

        assert result == ["005930"]
        mock_stock.get_index_portfolio_deposit_file.assert_called_once()


# ---------------------------------------------------------------------------
# 8. 영속 상태 — DB state_store seam
# ---------------------------------------------------------------------------


class TestPersistentState:
    """서킷 상태가 state_store를 통해 영속된다."""

    def test_state_is_saved_to_store_on_open(self):
        """OPEN 전이 시 state_store.save()가 호출된다."""
        from trading.data.krx_circuit_breaker import KrxCircuitBreaker

        mock_store = MagicMock()
        mock_briefing = MagicMock()
        cb = KrxCircuitBreaker(
            failure_threshold=1,
            _notify_fn=mock_briefing,
            _state_store=mock_store,
        )

        t = _utc(12)
        cb.record_failure(now=t)

        mock_store.save.assert_called_once()

    def test_state_is_saved_on_success_close(self):
        """CLOSED 전이 시 state_store.save()가 호출된다."""
        from trading.data.krx_circuit_breaker import KrxCircuitBreaker

        mock_store = MagicMock()
        mock_briefing = MagicMock()
        cb = KrxCircuitBreaker(
            failure_threshold=1,
            _notify_fn=mock_briefing,
            _state_store=mock_store,
        )

        t0 = _utc(10)
        cb.record_failure(now=t0)    # OPEN

        t1 = t0 + timedelta(minutes=16)
        cb.check_or_raise(now=t1)
        cb.record_success(now=t1)    # CLOSED

        # OPEN 1회 + CLOSED 1회 = 최소 2회 save
        assert mock_store.save.call_count >= 2

    def test_breaker_loads_state_from_store_on_init(self):
        """초기화 시 state_store에서 상태를 읽어 복원한다."""
        from trading.data.krx_circuit_breaker import CircuitState, KrxCircuitBreaker

        open_until = _utc(14)
        mock_store = MagicMock()
        mock_store.load.return_value = {
            "state": "OPEN",
            "open_until": open_until.isoformat(),
            "cooldown_level": 1,
            "consecutive_failures": 1,
        }

        cb = KrxCircuitBreaker(failure_threshold=3, _state_store=mock_store)

        # 저장된 OPEN 상태가 복원되어야 함
        assert cb.state == CircuitState.OPEN
        assert cb.open_until is not None
