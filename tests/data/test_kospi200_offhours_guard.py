"""오프시간 KRX pykrx 빈결과 및 장외 스팸 방지 재현 테스트.

Bug 재현:
1. pykrx 가 빈 결과를 반환할 때 _read_kospi200_top50 이 IndexError 없이
   빈 리스트를 반환해야 한다 (이미 except 로 잡히지만 pykrx 내부
   index -1 예외가 WARNING 으로 기록되는 것은 지저분함).
2. 장외 시간 (KRX 미개장) 에 get_data_universe() 가 호출될 때
   pykrx HTTP 요청 자체를 건너뛰어야 한다 — pykrx 로그인 시도가
   JSONDecodeError x 104 / 트레이스백 x 312 를 양산하는 근본 원인.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import pytz

# ---------------------------------------------------------------------------
# 공통 캐시 격리 fixture — 모든 테스트는 prod data/ 캐시를 오염시키지 않음
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_kospi200_cache(tmp_path, monkeypatch):
    """_kospi200_cache_path 를 임시 경로로 교체해 prod 캐시 오염 방지."""
    from trading.data import universe

    isolated = tmp_path / "kospi200_top50.json"
    monkeypatch.setattr(universe, "_kospi200_cache_path", lambda: isolated)
    return isolated


# ---------------------------------------------------------------------------
# Bug 1: _read_kospi200_top50 — 빈 pykrx 결과 시 깔끔한 [] 반환
# ---------------------------------------------------------------------------


class TestReadKospi200EmptyResult:
    """pykrx 가 빈 데이터프레임/리스트를 반환할 때 IndexError 없이 처리."""

    def test_empty_pykrx_result_returns_empty_list_no_exception(self, caplog):
        """_fetch_kospi200_from_pykrx 가 [] 를 반환하면
        _read_kospi200_top50 은 IndexError 없이 [] 를 돌려줘야 한다.

        pykrx 가 장중에 [] 를 반환한 경우 — 캐시 없음 + 장중 + 빈 fetch.
        """

        from trading.data import universe

        kst = pytz.timezone("Asia/Seoul")
        # 장중 시간으로 고정
        market_now = kst.localize(datetime(2026, 6, 25, 10, 0, 0))

        with (
            patch.object(universe, "_fetch_kospi200_from_pykrx", return_value=[]),
            patch("trading.data.universe._now_kst", return_value=market_now),
        ):
            with caplog.at_level("WARNING"):
                result = universe._read_kospi200_top50()

        # 빈 결과여도 예외 없이 [] 반환
        assert result == []
        # "index -1 out of bounds" 같은 IndexError 트레이스백이 없어야 함
        assert not any(
            "index" in r.message.lower() and "out of bounds" in r.message.lower()
            for r in caplog.records
        ), f"IndexError 트레이스백 감지: {[r.message for r in caplog.records]}"

    def test_pykrx_numpy_index_error_is_caught_cleanly(self, caplog):
        """pykrx 내부에서 numpy IndexError 가 발생해도 [] 반환, 단일 WARNING.

        장중 시간에 pykrx 가 IndexError 를 발생시키는 경우를 모사.
        로그 메시지가 'KOSPI200 source unavailable' 이어야 하고
        트레이스백 전파가 없어야 한다.
        """

        from trading.data import universe

        kst = pytz.timezone("Asia/Seoul")
        # 2026-06-25 는 수요일 — 장중 시간으로 설정
        market_now = kst.localize(datetime(2026, 6, 25, 10, 0, 0))

        def _pykrx_raises_index_error():
            # pykrx 오프시간 실제 예외 패턴 모방
            import numpy as np

            arr = np.array([])
            _ = arr[-1]  # IndexError: index -1 is out of bounds for axis 0 with size 0

        with (
            patch.object(
                universe, "_fetch_kospi200_from_pykrx", side_effect=_pykrx_raises_index_error
            ),
            patch("trading.data.universe._now_kst", return_value=market_now),
        ):
            with caplog.at_level("WARNING"):
                result = universe._read_kospi200_top50()

        # 예외가 밖으로 전파되면 안 됨
        assert result == []
        # 경고 1개만 기록
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1, f"경고 {len(warnings)}개: {[r.message for r in warnings]}"
        msg = warnings[0].message.lower()
        assert "unavailable" in msg or "kospi" in msg


# ---------------------------------------------------------------------------
# Bug 2: 장외 시간 pykrx 호출 자체를 건너뜀 (스팸 방지)
# ---------------------------------------------------------------------------


class TestKospi200OffHoursSkip:
    """장외 시간에 _fetch_kospi200_from_pykrx 를 호출하지 않아야 한다.

    KRX 정규장: 평일 09:00-15:30 KST.
    장외(06:20 blocked_cache cron 등)에 pykrx 를 건드리면
    로그인 실패로 JSONDecodeError x 104 + 트레이스백 x 312 양산.
    """

    def _make_kst_datetime(self, hour: int, minute: int = 0) -> datetime:
        """KST(UTC+9) 기준의 aware datetime 반환."""

        kst = pytz.timezone("Asia/Seoul")
        # 2026-06-25 는 수요일(평일) — 공휴일 아님
        return kst.localize(datetime(2026, 6, 25, hour, minute, 0))

    def test_off_hours_morning_skips_pykrx_and_returns_empty(self, caplog):
        """06:20 KST (장전) + 빈 캐시 — pykrx fetch 를 건너뛰고 [] 반환."""
        from trading.data import universe

        off_hours_now = self._make_kst_datetime(6, 20)

        pykrx_called = []

        def _spy_pykrx():
            pykrx_called.append(True)
            return ["005930"]  # 실제로 호출되면 non-empty 반환

        with (
            patch.object(universe, "_fetch_kospi200_from_pykrx", side_effect=_spy_pykrx),
            patch("trading.data.universe._now_kst", return_value=off_hours_now),
        ):
            with caplog.at_level("INFO"):
                result = universe._read_kospi200_top50()

        # 장외에는 pykrx 를 호출하면 안 됨
        assert pykrx_called == [], (
            f"장외(06:20 KST) 인데 pykrx 가 {len(pykrx_called)}회 호출됨 — "
            "스팸 방지 가드가 없음"
        )
        # 빈 캐시 + 장외 → [] 반환
        assert result == []
        # INFO 로그 확인 (장외/캐시 없음 메시지)
        infos = [r for r in caplog.records if r.levelname == "INFO"]
        assert len(infos) >= 1
        assert any(
            "장외" in r.message
            or "캐시" in r.message
            or "skip" in r.message.lower()
            or "장" in r.message
            or "마감" in r.message
            or "off" in r.message.lower()
            for r in infos
        ), f"장외/캐시 로그 없음: {[r.message for r in caplog.records]}"

    def test_during_market_hours_pykrx_is_called(self, caplog):
        """10:00 KST (장중) + 빈 캐시 — pykrx fetch 가 정상 호출되고 캐시 파일 작성됨."""
        from trading.data import universe

        market_hours_now = self._make_kst_datetime(10, 0)

        pykrx_called = []

        def _spy_pykrx():
            pykrx_called.append(True)
            return ["005930", "000660"]

        with (
            patch.object(universe, "_fetch_kospi200_from_pykrx", side_effect=_spy_pykrx),
            patch("trading.data.universe._now_kst", return_value=market_hours_now),
        ):
            result = universe._read_kospi200_top50()

        # 장중에는 pykrx 호출해야 함
        assert pykrx_called == [True], "장중(10:00 KST) 인데 pykrx 미호출"
        assert result == ["005930", "000660"]
        # 캐시 파일이 작성되어야 함
        cache_path = universe._kospi200_cache_path()
        assert cache_path.exists(), "장중 fetch 후 캐시 파일이 기록되어야 함"
        cached = json.loads(cache_path.read_text())
        assert cached["tickers"] == ["005930", "000660"]
        assert cached["trading_day"] == "2026-06-25"

    def test_after_close_evening_skips_pykrx(self):
        """22:00 KST (장마감 후) + 빈 캐시 — pykrx fetch 건너뜀."""
        from trading.data import universe

        after_close = self._make_kst_datetime(22, 0)

        pykrx_called = []

        def _spy_pykrx():
            pykrx_called.append(True)
            return ["005930"]

        with (
            patch.object(universe, "_fetch_kospi200_from_pykrx", side_effect=_spy_pykrx),
            patch("trading.data.universe._now_kst", return_value=after_close),
        ):
            result = universe._read_kospi200_top50()

        assert pykrx_called == [], "장마감 후 22:00 KST 인데 pykrx 호출됨"
        assert result == []

    def test_weekend_skips_pykrx(self):
        """토요일 10:00 KST + 빈 캐시 — pykrx fetch 건너뜀 (비거래일)."""

        from trading.data import universe

        kst = pytz.timezone("Asia/Seoul")
        # 2026-06-27 은 토요일
        saturday_10am = kst.localize(datetime(2026, 6, 27, 10, 0, 0))

        pykrx_called = []

        def _spy_pykrx():
            pykrx_called.append(True)
            return ["005930"]

        with (
            patch.object(universe, "_fetch_kospi200_from_pykrx", side_effect=_spy_pykrx),
            patch("trading.data.universe._now_kst", return_value=saturday_10am),
        ):
            result = universe._read_kospi200_top50()

        assert pykrx_called == [], "토요일인데 pykrx 호출됨"
        assert result == []

    def test_get_data_universe_does_not_call_pykrx_off_hours(self):
        """get_data_universe() 가 장외에 호출될 때 pykrx 를 건드리지 않음.

        이것이 blocked_tickers_cache 06:20 cron 에서 pykrx 스팸이 발생한
        근본 경로 — get_data_universe → _read_kospi200_top50 → pykrx.
        빈 캐시 + 장외 → screened-only 유니버스, pykrx 0회 호출.
        """

        from trading.data import universe

        kst = pytz.timezone("Asia/Seoul")
        off_hours = kst.localize(datetime(2026, 6, 25, 6, 20, 0))  # 평일 06:20

        pykrx_called = []

        def _spy_pykrx():
            pykrx_called.append(True)
            return ["005930"]

        with (
            patch.object(universe, "_fetch_kospi200_from_pykrx", side_effect=_spy_pykrx),
            patch("trading.data.universe._now_kst", return_value=off_hours),
            patch("trading.data.universe._read_screened_tickers", return_value=["005380"]),
            patch("trading.data.universe._read_active_holdings", return_value=[]),
            patch("trading.data.universe._read_dynamic_tickers", return_value=[]),
        ):
            result = universe.get_data_universe()

        # 유니버스는 screened tickers 로 구성되어야 함
        assert "005380" in result
        # pykrx 는 건드리면 안 됨
        assert pykrx_called == [], (
            f"get_data_universe() 장외 호출에서 pykrx 가 {len(pykrx_called)}회 호출됨"
        )


# ---------------------------------------------------------------------------
# 캐시 동작 테스트 (신규)
# ---------------------------------------------------------------------------


class TestKospi200MembershipCache:
    """KOSPI200 멤버십 파일 캐시 동작 검증.

    캐시가 당일이면 서빙, 구버전이면 장중 재조회·장외 구버전 서빙.
    """

    def _make_kst(self, day: date, hour: int, minute: int = 0) -> datetime:

        kst = pytz.timezone("Asia/Seoul")
        return kst.localize(datetime(day.year, day.month, day.day, hour, minute, 0))

    def _write_cache(self, cache_path: Path, tickers: list[str], trading_day: str) -> None:
        """테스트용 캐시 파일 직접 작성."""
        cache_path.write_text(
            json.dumps(
                {
                    "tickers": tickers,
                    "trading_day": trading_day,
                    "fetched_at": f"{trading_day}T10:00:00+09:00",
                }
            )
        )

    def test_off_hours_with_fresh_cache_returns_cache_no_pykrx(self):
        """장외 + 당일 캐시 → 캐시 반환, pykrx 0회 호출."""
        from trading.data import universe

        today = date(2026, 6, 25)  # 수요일
        off_hours_now = self._make_kst(today, 22, 0)

        # 캐시에 당일 데이터 기록
        cache_path = universe._kospi200_cache_path()
        self._write_cache(cache_path, ["005930", "000660", "035420"], today.isoformat())

        pykrx_called = []

        def _spy_pykrx():
            pykrx_called.append(True)
            return ["005930"]

        with (
            patch.object(universe, "_fetch_kospi200_from_pykrx", side_effect=_spy_pykrx),
            patch("trading.data.universe._now_kst", return_value=off_hours_now),
        ):
            result = universe._read_kospi200_top50()

        # 당일 캐시 → 그대로 반환
        assert result == ["005930", "000660", "035420"]
        # pykrx 미호출
        assert pykrx_called == [], "당일 캐시가 있는데 pykrx 를 호출함"

    def test_off_hours_with_stale_cache_returns_stale_cache_no_pykrx(self):
        """장외 + 구버전 캐시 → 구버전 캐시 반환, pykrx 0회 호출."""
        from trading.data import universe

        today = date(2026, 6, 25)  # 수요일
        yesterday = date(2026, 6, 24)  # 화요일 (어제)
        off_hours_now = self._make_kst(today, 22, 0)

        # 어제 날짜 캐시 기록
        cache_path = universe._kospi200_cache_path()
        stale_tickers = ["005930", "000660"]
        self._write_cache(cache_path, stale_tickers, yesterday.isoformat())

        pykrx_called = []

        def _spy_pykrx():
            pykrx_called.append(True)
            return ["005930"]

        with (
            patch.object(universe, "_fetch_kospi200_from_pykrx", side_effect=_spy_pykrx),
            patch("trading.data.universe._now_kst", return_value=off_hours_now),
        ):
            result = universe._read_kospi200_top50()

        # 장외에서는 구버전 캐시라도 반환 (pykrx 미호출)
        assert result == stale_tickers
        assert pykrx_called == [], "장외인데 pykrx 를 호출함"

    def test_market_hours_fresh_cache_skips_pykrx(self):
        """장중 + 당일 캐시 → 캐시 반환, pykrx 0회 호출 (불필요한 재조회 방지)."""
        from trading.data import universe

        today = date(2026, 6, 25)  # 수요일
        market_now = self._make_kst(today, 10, 0)

        # 당일 캐시 기록
        cache_path = universe._kospi200_cache_path()
        fresh_tickers = ["005930", "000660", "207940"]
        self._write_cache(cache_path, fresh_tickers, today.isoformat())

        pykrx_called = []

        def _spy_pykrx():
            pykrx_called.append(True)
            return ["005930"]

        with (
            patch.object(universe, "_fetch_kospi200_from_pykrx", side_effect=_spy_pykrx),
            patch("trading.data.universe._now_kst", return_value=market_now),
        ):
            result = universe._read_kospi200_top50()

        # 당일 캐시 → pykrx 없이 반환
        assert result == fresh_tickers
        assert pykrx_called == [], "당일 캐시가 있는데 장중에 pykrx 를 호출함"

    def test_market_hours_stale_cache_refetches_and_updates(self):
        """장중 + 구버전 캐시 → pykrx 재조회 후 새 캐시 파일 갱신."""
        from trading.data import universe

        today = date(2026, 6, 25)  # 수요일
        yesterday = date(2026, 6, 24)
        market_now = self._make_kst(today, 10, 0)

        # 어제 날짜 캐시
        cache_path = universe._kospi200_cache_path()
        self._write_cache(cache_path, ["005930"], yesterday.isoformat())

        new_tickers = ["005930", "000660", "207940", "051910"]

        pykrx_called = []

        def _spy_pykrx():
            pykrx_called.append(True)
            return new_tickers

        with (
            patch.object(universe, "_fetch_kospi200_from_pykrx", side_effect=_spy_pykrx),
            patch("trading.data.universe._now_kst", return_value=market_now),
        ):
            result = universe._read_kospi200_top50()

        # pykrx 1회 호출
        assert pykrx_called == [True], "구버전 캐시 + 장중인데 pykrx 미호출"
        # 새 목록 반환
        assert result == new_tickers
        # 캐시 파일 갱신 확인
        assert cache_path.exists()
        cached = json.loads(cache_path.read_text())
        assert cached["tickers"] == new_tickers
        assert cached["trading_day"] == today.isoformat()

    def test_market_fetch_failure_falls_back_to_stale_cache(self, caplog):
        """장중 + pykrx 실패 + 구버전 캐시 → 구버전 캐시 반환, 경고 로그."""
        from trading.data import universe

        today = date(2026, 6, 25)  # 수요일
        yesterday = date(2026, 6, 24)
        market_now = self._make_kst(today, 10, 0)

        # 구버전 캐시
        cache_path = universe._kospi200_cache_path()
        stale_tickers = ["005930", "000660"]
        self._write_cache(cache_path, stale_tickers, yesterday.isoformat())

        with (
            patch.object(
                universe,
                "_fetch_kospi200_from_pykrx",
                side_effect=RuntimeError("KRX 타임아웃"),
            ),
            patch("trading.data.universe._now_kst", return_value=market_now),
        ):
            with caplog.at_level("WARNING"):
                result = universe._read_kospi200_top50()

        # pykrx 실패 → 구버전 캐시 반환 ([] 가 아님)
        assert result == stale_tickers, "pykrx 실패 시 구버전 캐시를 반환해야 함"
        # 경고 로그 확인
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) >= 1

    def test_cold_start_off_hours_no_cache_returns_empty(self):
        """캐시 없음 + 장외 → [] 반환 (cold start)."""

        from trading.data import universe

        kst = pytz.timezone("Asia/Seoul")
        # 장전 시간 (캐시 파일 없음 — autouse fixture 가 빈 tmp_path 를 제공)
        off_hours_now = kst.localize(datetime(2026, 6, 25, 6, 20, 0))

        pykrx_called = []

        def _spy_pykrx():
            pykrx_called.append(True)
            return ["005930"]

        with (
            patch.object(universe, "_fetch_kospi200_from_pykrx", side_effect=_spy_pykrx),
            patch("trading.data.universe._now_kst", return_value=off_hours_now),
        ):
            result = universe._read_kospi200_top50()

        # cold start + 장외 → []
        assert result == []
        assert pykrx_called == [], "cold start 장외인데 pykrx 를 호출함"
