"""SPEC-TRADING-063 — 주문 거부 전용 알림 (재현 우선).

2026-08-04~06 KIS 모의투자 계좌의 주문권한이 만료되어 주문 15건이 전부
`40910000:모의투자 주문이 불가한 계좌입니다.` 로 거부됐다. 그러나 거부에 대한
전용 알림 경로가 없어 나흘간 발견되지 않았고, 그 사이 체결은 0건이었다
(엣지 검증 데이터도 함께 공백). 실거래였다면 실제 주문이 같은 방식으로
조용히 사라진다.

AC-1  order_rejected() 는 종목·방향·수량·사유를 담은 메시지를 발송한다.
AC-2  silent_mode 여도 발송한다 — 거부는 실행경로 고장이라 침묵 대상이 아니다.
AC-3  같은 사유가 쿨다운 안에 반복되면 1회만 발송한다(사이클마다 도배 방지).
AC-4  텔레그램 전송이 실패해도 예외를 밖으로 내보내지 않는다(주문 경로 보호).
AC-5  쓰로틀 상태는 audit_log(DB)에 둔다 — 컨테이너 재시작에도 살아남아야 한다.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

# ---------------------------------------------------------------------------
# 스크립트 DB 더블 (tests/kis/test_synthetic_fill.py 패턴과 동일)
# ---------------------------------------------------------------------------


class ScriptedCursor:
    def __init__(self, fetchone_queue: list[Any] | None = None) -> None:
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

    def __enter__(self) -> ScriptedConn:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


@contextmanager
def _conn_factory(cursor: ScriptedCursor):
    yield ScriptedConn(cursor)


_REASON = "40910000:모의투자 주문이 불가한 계좌입니다."


def _kwargs(**over: Any) -> dict[str, Any]:
    base = dict(
        order_id=4321,
        ticker="316140",
        side="buy",
        qty=8,
        mode="paper",
        reason=_REASON,
    )
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# AC-1 / AC-2 발송
# ---------------------------------------------------------------------------


class TestOrderRejectedSends:
    def test_message_carries_ticker_side_qty_and_reason(self):
        """AC-1: 운영자가 사유를 바로 읽을 수 있어야 한다."""
        from trading.alerts import telegram as tg

        with patch.object(tg, "_send_raw") as send, \
             patch.object(tg, "_reject_alert_throttled", return_value=False), \
             patch.object(tg, "_record_reject_alert"):
            sent = tg.order_rejected(**_kwargs())

        assert sent is True
        text = send.call_args[0][0]
        assert "316140" in text
        assert "매수" in text
        assert "8" in text
        # 사유 원문이 그대로 실려야 한다(추측 금지 — 8/6 리포트 실패 지점).
        assert "모의투자 주문이 불가한 계좌입니다" in text

    def test_bypasses_silent_mode(self):
        """AC-2: silent_mode 는 브리핑 소음만 줄이는 장치이지 고장 은폐가 아니다."""
        from trading.alerts import telegram as tg

        with patch.object(tg, "_send_raw") as send, \
             patch.object(tg, "_briefing_silent", return_value=True), \
             patch.object(tg, "_reject_alert_throttled", return_value=False), \
             patch.object(tg, "_record_reject_alert"):
            sent = tg.order_rejected(**_kwargs())

        assert sent is True
        assert send.called


# ---------------------------------------------------------------------------
# AC-3 쓰로틀
# ---------------------------------------------------------------------------


class TestOrderRejectedThrottle:
    def test_same_reason_within_cooldown_is_suppressed(self):
        """AC-3: 계좌 만료 같은 브로커측 고장은 사이클마다 반복 거부를 만든다."""
        from trading.alerts import telegram as tg

        with patch.object(tg, "_send_raw") as send, \
             patch.object(tg, "_reject_alert_throttled", return_value=True), \
             patch.object(tg, "_record_reject_alert"):
            sent = tg.order_rejected(**_kwargs())

        assert sent is False
        assert not send.called

    def test_distinct_reasons_are_throttled_independently(self):
        """다른 사유는 별개 key — 새 고장이 이전 고장에 가려지면 안 된다."""
        from trading.alerts import telegram as tg

        seen: list[str] = []

        def _spy(key: str, _cd: int) -> bool:
            seen.append(key)
            return False

        with patch.object(tg, "_send_raw"), \
             patch.object(tg, "_reject_alert_throttled", side_effect=_spy), \
             patch.object(tg, "_record_reject_alert"):
            tg.order_rejected(**_kwargs())
            tg.order_rejected(**_kwargs(reason="40310000:주문가능금액을 초과합니다."))

        assert len(seen) == 2
        assert seen[0] != seen[1]

    def test_throttle_state_is_read_from_audit_log(self):
        """AC-5: 메모리 dict 가 아니라 DB 를 봐야 재시작에도 쓰로틀이 유지된다."""
        from trading.alerts import telegram as tg

        cur = ScriptedCursor(fetchone_queue=[{"exists": 1}])
        with patch("trading.db.session.connection",
                   side_effect=lambda *a, **k: _conn_factory(cur)):
            throttled = tg._reject_alert_throttled("paper:X", 3600)

        assert throttled is True
        sql = " ".join(cur.calls[0][0].split())
        assert "audit_log" in sql
        assert "ORDER_REJECT_ALERT" in sql

    def test_throttle_lookup_failure_fails_open(self):
        """조회 실패 시 발송 쪽으로 열린다 — 알림 누락이 도배보다 위험하다."""
        from trading.alerts import telegram as tg

        with patch("trading.db.session.connection", side_effect=RuntimeError("db down")):
            assert tg._reject_alert_throttled("paper:X", 3600) is False


# ---------------------------------------------------------------------------
# SPEC-TRADING-064 REQ-064-B7 — decision_id 배선
# ---------------------------------------------------------------------------


class TestOrderRejectedDecisionId:
    def test_decision_id_recorded_when_provided(self):
        """submit_order이 넘긴 persona_decision_id가 audit details에 실린다."""
        from trading.alerts import telegram as tg

        captured: dict[str, Any] = {}

        def _spy(key: str, details: dict[str, Any]) -> None:
            captured.update(details)

        with patch.object(tg, "_send_raw"), \
             patch.object(tg, "_reject_alert_throttled", return_value=False), \
             patch.object(tg, "_record_reject_alert", side_effect=_spy):
            tg.order_rejected(**_kwargs(), decision_id=555)

        assert captured["decision_id"] == 555

    def test_decision_id_defaults_to_none(self):
        """decision_id 미지정 시 None으로 명시 기록된다(키 누락 금지)."""
        from trading.alerts import telegram as tg

        captured: dict[str, Any] = {}

        def _spy(key: str, details: dict[str, Any]) -> None:
            captured.update(details)

        with patch.object(tg, "_send_raw"), \
             patch.object(tg, "_reject_alert_throttled", return_value=False), \
             patch.object(tg, "_record_reject_alert", side_effect=_spy):
            tg.order_rejected(**_kwargs())

        assert captured["decision_id"] is None


# ---------------------------------------------------------------------------
# AC-4 격리
# ---------------------------------------------------------------------------


class TestOrderRejectedIsolation:
    def test_telegram_failure_does_not_raise(self):
        """AC-4: 알림 실패가 주문 처리 흐름을 깨뜨리면 안 된다."""
        from trading.alerts import telegram as tg

        with patch.object(tg, "_send_raw", side_effect=RuntimeError("telegram down")), \
             patch.object(tg, "_reject_alert_throttled", return_value=False), \
             patch.object(tg, "_record_reject_alert"):
            assert tg.order_rejected(**_kwargs()) is False

    def test_audit_failure_does_not_raise(self):
        """감사 기록 실패도 격리 — 이미 보낸 알림이 롤백되지는 않는다."""
        from trading.alerts import telegram as tg

        with patch.object(tg, "_send_raw"), \
             patch.object(tg, "_reject_alert_throttled", return_value=False), \
             patch.object(tg, "_record_reject_alert", side_effect=RuntimeError("db down")):
            assert tg.order_rejected(**_kwargs()) is True
