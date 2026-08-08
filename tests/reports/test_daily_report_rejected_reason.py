"""SPEC-TRADING-063 — 일일 리포트가 주문 거부 사유를 싣는다 (재현 우선).

2026-08-06 일일 리포트는 "3건 모두 거부(rejected) 되어 체결이 하나도 없었습니다"
까지는 썼지만 곧바로 "거부 사유는 이번 데이터에 포함되어 있지 않아 원인은 확인할
수 없습니다" 로 끝났다. 원인은 프롬프트가 아니라 배관이다 —
daily_report._gather_today() 의 sql_orders 가 `rejected_reason` 을 SELECT 하지
않는데, 같은 파일의 LLM 프롬프트는 "rejected_reason 필드에 명시되어 있으니 추측
하지 말고 그대로 인용" 하라고 지시한다. 모델은 없는 필드를 인용할 수 없으므로
정직하게 모른다고 답했고, 그 결과 나흘짜리 고장의 사유가 운영자에게 닿지 않았다.

AC-1  orders 페이로드의 각 행에 rejected_reason 키가 존재한다.
AC-2  거부된 주문의 사유 원문이 값으로 실린다.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

_REASON = "40910000:모의투자 주문이 불가한 계좌입니다."

_REJECTED_ROW = {
    "id": 991,
    "ticker": "316140",
    "side": "buy",
    "qty": 8,
    "status": "rejected",
    "fill_price": None,
    "fill_qty": None,
    "fee": 0,
    "mode": "paper",
    "rejected_reason": _REASON,
}


class _OrdersCursor:
    """orders SELECT 에만 행을 돌려주고 나머지 질의는 비운다."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._last_is_orders = False

    def execute(self, sql: str, params: Any = None) -> None:
        flat = " ".join(sql.split())
        self.calls.append(flat)
        self._last_is_orders = (
            "FROM orders WHERE ts::date = CURRENT_DATE ORDER BY id" in flat
        )

    def fetchall(self) -> list[dict[str, Any]]:
        # 실제 드라이버는 SELECT 목록에 있는 컬럼만 돌려준다. 컬럼이 SQL 에
        # 없으면 키도 없어야 재현이 성립하므로 SQL 을 보고 투영한다.
        if not self._last_is_orders:
            return []
        sql = self.calls[-1]
        return [{k: v for k, v in _REJECTED_ROW.items() if k in sql}]

    def fetchone(self) -> dict[str, Any]:
        return {}

    def __enter__(self) -> _OrdersCursor:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class _Conn:
    def __init__(self, cur: _OrdersCursor) -> None:
        self._cur = cur

    def cursor(self) -> _OrdersCursor:
        return self._cur

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


@contextmanager
def _conn_factory(cur: _OrdersCursor):
    yield _Conn(cur)


class TestGatherTodayCarriesRejectedReason:
    def _gather(self) -> dict[str, Any]:
        from trading.reports import daily_report as dr

        cur = _OrdersCursor()
        with patch.object(dr, "connection",
                          side_effect=lambda *a, **k: _conn_factory(cur)), \
             patch.object(dr, "_gather_intelligence", return_value={}), \
             patch.object(dr, "_collect_portfolio", return_value={}):
            return dr._gather_today()

    def test_orders_rows_expose_rejected_reason_key(self):
        """AC-1: 키 자체가 없으면 LLM 은 사유를 인용할 방법이 없다."""
        data = self._gather()
        assert data["orders"], "orders 페이로드가 비어 재현이 성립하지 않음"
        assert "rejected_reason" in data["orders"][0]

    def test_rejected_reason_value_is_the_broker_message(self):
        """AC-2: 8/6 에 운영자가 못 본 그 문자열이 그대로 실려야 한다."""
        data = self._gather()
        assert data["orders"][0]["rejected_reason"] == _REASON
