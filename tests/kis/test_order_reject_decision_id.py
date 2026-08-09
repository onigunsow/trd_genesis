"""SPEC-TRADING-064 REQ-064-B7 — submit_order → order_rejected decision_id 배선.

``submit_order``이 이미 받아 orders 행에 쓰고 있는 ``persona_decision_id``를
거부 알림 경로(``telegram.order_rejected``)로도 전달하는지 특성화한다
(SPEC-TRADING-063이 만든 ORDER_REJECT_ALERT 경로).

All tests are offline: ``client.post`` returns a scripted rejecting KisResponse,
the DB connection is a ``ScriptedCursor``. No DB, no network.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trading.config import TradingMode
from trading.kis.client import KisError, KisResponse


class ScriptedCursor:
    def __init__(self, *, fetchone_queue: list[Any] | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._one = list(fetchone_queue or [])

    def execute(self, sql: str, params: Any = None) -> None:
        self.calls.append((sql, params))

    def fetchone(self) -> Any:
        return self._one.pop(0) if self._one else None

    def __enter__(self) -> ScriptedCursor:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class ScriptedConn:
    def __init__(self, cursor: ScriptedCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> ScriptedCursor:
        return self._cursor

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None

    def __enter__(self) -> ScriptedConn:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


def _paper_client() -> MagicMock:
    client = MagicMock()
    client.mode = TradingMode.PAPER
    client.account_prefix = "50000000"
    client.account_suffix = "01"
    client.tr_id.side_effect = lambda paper_id, live_id: paper_id
    client.post.return_value = KisResponse(
        status_code=200,
        rt_cd="1",
        msg_cd="40910000",
        msg="모의투자 주문이 불가한 계좌입니다.",
        output={},
        raw={"rt_cd": "1"},
    )
    return client


class TestSubmitOrderPassesDecisionIdToRejectAlert:
    def test_rejected_order_forwards_persona_decision_id(self):
        from trading.kis import order

        client = _paper_client()
        cursor = ScriptedCursor(fetchone_queue=[{"id": 99}])

        @contextmanager
        def _conn_factory(*_a: Any, **_k: Any):
            yield ScriptedConn(cursor)

        with (
            patch.object(order, "connection", _conn_factory),
            patch.object(order, "audit", MagicMock()),
            patch("trading.alerts.telegram.order_rejected") as m_reject,
            pytest.raises(KisError),
        ):
            order.submit_order(
                client, ticker="316140", qty=8, side="buy",
                persona_decision_id=321,
            )

        assert m_reject.call_count == 1
        assert m_reject.call_args.kwargs["decision_id"] == 321

    def test_rejected_order_with_no_decision_forwards_none(self):
        """persona_decision_id 미지정(규칙 기반) 주문도 거부 알림에 None을
        명시적으로 넘긴다 — 키 누락 금지."""
        from trading.kis import order

        client = _paper_client()
        cursor = ScriptedCursor(fetchone_queue=[{"id": 100}])

        @contextmanager
        def _conn_factory(*_a: Any, **_k: Any):
            yield ScriptedConn(cursor)

        with (
            patch.object(order, "connection", _conn_factory),
            patch.object(order, "audit", MagicMock()),
            patch("trading.alerts.telegram.order_rejected") as m_reject,
            pytest.raises(KisError),
        ):
            order.submit_order(client, ticker="316140", qty=8, side="sell")

        assert m_reject.call_args.kwargs["decision_id"] is None
