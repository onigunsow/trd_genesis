"""2026-08-24 당일 매수 이력 안내.

프롬프트는 "단기과열 종목은 같은 날 1회만 통과한다"고 룰을 말하면서 정작 어느 종목을
이미 샀는지는 알려주지 않았다. 8/24 실측: LIMIT_BREACH 20건이 전부 repeat_buy 재제안.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from trading.risk import limits


def _rows(rows):
    conn = MagicMock()
    cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value = rows
    return conn


def test_counts_per_ticker():
    with patch.object(limits, "connection", return_value=_rows([
        {"ticker": "012330", "n": 1},
        {"ticker": "251270", "n": 2},
    ])):
        assert limits.tickers_bought_today() == {"012330": 1, "251270": 2}


def test_tuple_rows_supported():
    """dict_row 가 아닌 커서에서도 같은 결과 — 쿨다운 맵과 같은 관용."""
    with patch.object(limits, "connection", return_value=_rows([("012330", 1)])):
        assert limits.tickers_bought_today() == {"012330": 1}


def test_db_failure_is_graceful():
    """조회 실패는 안내 누락일 뿐 — check_pre_order 가 여전히 재매수를 막는다."""
    with patch.object(limits, "connection", side_effect=RuntimeError("db down")):
        assert limits.tickers_bought_today() == {}


def test_empty_when_no_buys():
    with patch.object(limits, "connection", return_value=_rows([])):
        assert limits.tickers_bought_today() == {}
