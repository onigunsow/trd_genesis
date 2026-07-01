"""CHANGE A — cache.latest_close() TDD 테스트.

latest_close(source, symbol) 가 DB에서 가장 최근 종가를 반환하고,
행이 없으면 None 을 반환함을 검증한다.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

# ---------------------------------------------------------------------------
# FakeConnection / FakeCursor 재사용 (conftest 패턴 미러)
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, row=None):
        self._row = row
        self.last_sql: str = ""
        self.last_params: tuple = ()

    def execute(self, sql, params=None):
        self.last_sql = sql
        self.last_params = params or ()

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _FakeConn:
    def __init__(self, row=None):
        self._cursor = _FakeCursor(row)

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@contextmanager
def _make_conn(row=None):
    yield _FakeConn(row)


# ---------------------------------------------------------------------------
# RED: latest_close 테스트
# ---------------------------------------------------------------------------

class TestLatestClose:
    """cache.latest_close() — DB 마지막 종가 조회."""

    def test_행_있을때_int_반환(self):
        """ohlcv 행이 있으면 close 값을 int로 반환한다."""
        from trading.data.cache import latest_close

        row = {"close": 75_300}
        with patch("trading.data.cache.connection", side_effect=lambda: _make_conn(row)):
            result = latest_close("pykrx", "005930")

        assert result == 75_300
        assert isinstance(result, int)

    def test_행_없을때_None(self):
        """ohlcv 행이 없으면 None 반환."""
        from trading.data.cache import latest_close

        with patch("trading.data.cache.connection", side_effect=lambda: _make_conn(None)):
            result = latest_close("pykrx", "999999")

        assert result is None

    def test_close_None이면_None(self):
        """close 컬럼이 NULL이면 None 반환."""
        from trading.data.cache import latest_close

        row = {"close": None}
        with patch("trading.data.cache.connection", side_effect=lambda: _make_conn(row)):
            result = latest_close("pykrx", "005930")

        assert result is None

    def test_sql에_ORDER_BY_ts_DESC_LIMIT_1_포함(self):
        """SQL이 ORDER BY ts DESC LIMIT 1 을 포함해야 한다 (최신 종가 보장)."""
        from trading.data.cache import latest_close

        cursor_ref: list[_FakeCursor] = []

        @contextmanager
        def _spy_conn():
            c = _FakeCursor({"close": 10_000})
            cursor_ref.append(c)
            conn = _FakeConn()
            conn._cursor = c
            yield conn

        with patch("trading.data.cache.connection", side_effect=_spy_conn):
            latest_close("pykrx", "035420")

        sql = cursor_ref[0].last_sql.upper()
        assert "ORDER BY TS DESC" in sql
        assert "LIMIT 1" in sql

    def test_tuple_형식_row도_처리(self):
        """fetchone 이 dict 가 아닌 tuple 을 반환해도 close 를 읽는다."""
        from trading.data.cache import latest_close

        # tuple 형식: row[0] = close
        class _TupleCursor(_FakeCursor):
            def fetchone(self):
                return (55_000,)  # tuple

        @contextmanager
        def _tuple_conn():
            conn = _FakeConn()
            conn._cursor = _TupleCursor()
            yield conn

        with patch("trading.data.cache.connection", side_effect=_tuple_conn):
            result = latest_close("pykrx", "000660")

        assert result == 55_000
