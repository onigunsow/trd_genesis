"""2026-08-23 재진입 쿨다운 안내.

못 사는 종목을 페르소나가 매 사이클 재제안하던 문제(8/18~21 buy 결정 73건 중 32건)
를 막기 위해, 쿨다운 잔여일을 프롬프트에 주입한다.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from trading.risk import limits


def _rows(rows):
    conn = MagicMock()
    cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value = rows
    return conn


def test_remaining_days_computed_and_expired_dropped():
    with (
        patch.object(limits, "REENTRY_COOLDOWN_DAYS", 10),
        patch.object(limits, "connection", return_value=_rows([
            {"ticker": "055550", "days_since": 8},   # 잔여 2
            {"ticker": "064350", "days_since": 10},  # 만료 → 제외
            {"ticker": "071050", "days_since": 0},   # 잔여 10
        ])),
    ):
        assert limits.tickers_in_reentry_cooldown() == {"055550": 2, "071050": 10}


def test_disabled_returns_empty_without_query():
    with (
        patch.object(limits, "REENTRY_COOLDOWN_DAYS", 0),
        patch.object(limits, "connection", side_effect=AssertionError("쿼리 금지")),
    ):
        assert limits.tickers_in_reentry_cooldown() == {}


def test_db_failure_is_graceful():
    """조회 실패는 안내 누락일 뿐 — 한도 검사가 여전히 매수를 막는다."""
    with (
        patch.object(limits, "REENTRY_COOLDOWN_DAYS", 10),
        patch.object(limits, "connection", side_effect=RuntimeError("db down")),
    ):
        assert limits.tickers_in_reentry_cooldown() == {}
