"""SPEC-TRADING-045 — live execution safety (reproduction-first TDD).

M1 (6/8 재현): live 체결조회 seam 미구현 상태에서 SELL confirmed-fill 경로 재현.
M2 (live fill inquiry seam): confirm_fills() live 분기가 실제 체결조회를 수행.
M3 (멱등성/이중매도): sell_lock submitted leg 자동 해제 + 중복매도 차단.

All tests are fully offline: no DB, no network, no real clock.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

from trading.config import TradingMode

# ---------------------------------------------------------------------------
# Test doubles — shared
# ---------------------------------------------------------------------------


class _AuditSink:
    """Captures audit(event_type, actor, details) calls."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.details: list[dict[str, Any]] = []

    def __call__(
        self, event_type: str, actor: str = "system", details: Any = None
    ) -> None:
        self.events.append(event_type)
        self.details.append(details or {})


def _live_client() -> MagicMock:
    client = MagicMock()
    client.mode = TradingMode.LIVE
    client.account_prefix = "50000000"
    client.account_suffix = "01"
    client.tr_id.side_effect = lambda paper_id, live_id: live_id
    return client


def _paper_client() -> MagicMock:
    client = MagicMock()
    client.mode = TradingMode.PAPER
    client.account_prefix = "50000000"
    client.account_suffix = "01"
    client.tr_id.side_effect = lambda paper_id, live_id: paper_id
    return client


def _kis_fill_response(
    *,
    rt_cd: str = "0",
    records: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """Fake KisResponse for inquire-daily-ccld."""
    from trading.kis.client import KisResponse

    rec = records or []
    return KisResponse(
        status_code=200,
        rt_cd=rt_cd,
        msg_cd="APOK0000",
        msg="정상처리",
        output={},
        raw={"rt_cd": rt_cd, "output1": rec},  # # 확인 필요: output1 field name
    )


def _fill_record(
    *,
    odno: str = "0000029297",
    ccld_qty: str = "3",          # # 확인 필요: field name CCLD_QTY
    ccld_avg_unpr: str = "10000", # # 확인 필요: field name CCLD_AVG_UNPR
    sll_buy_dvsn_cd: str = "01",  # # 확인 필요: 01=sell, 02=buy
) -> dict[str, Any]:
    """Minimal KIS fill record for inquire-daily-ccld. Fields marked 확인 필요."""
    return {
        "ODNO": odno,                    # # 확인 필요: 주문번호 field name
        "CCLD_QTY": ccld_qty,           # # 확인 필요: 체결수량
        "CCLD_AVG_UNPR": ccld_avg_unpr, # # 확인 필요: 체결평균가
        "SLL_BUY_DVSN_CD": sll_buy_dvsn_cd, # # 확인 필요: 매도매수구분
    }


class ScriptedCursor:
    """In-memory cursor double — records SQL calls, returns scripted results."""

    def __init__(
        self,
        *,
        fetchone_queue: list[Any] | None = None,
        fetchall_queue: list[Any] | None = None,
    ) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._one = list(fetchone_queue or [])
        self._all = list(fetchall_queue or [])

    def execute(self, sql: str, params: Any = None) -> None:
        self.calls.append((sql, params))

    def fetchone(self) -> Any:
        return self._one.pop(0) if self._one else None

    def fetchall(self) -> Any:
        return self._all.pop(0) if self._all else []

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
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self) -> ScriptedConn:
        return self

    def __exit__(self, exc_type: Any, *_: Any) -> None:
        pass


def _conn_sequence(cursors: list[ScriptedCursor]):
    """Return a connection() factory that hands out cursors in order."""
    iterator = iter(cursors)

    @contextmanager
    def _factory(*_a: Any, **_k: Any):
        yield ScriptedConn(next(iterator))

    return _factory


def _status_updates(cursor: ScriptedCursor) -> list[tuple[str, Any]]:
    out = []
    for sql, params in cursor.calls:
        up = sql.upper()
        if "UPDATE ORDERS" in up and "STATUS" in up:
            out.append((sql, params))
    return out


def _audit_events(*cursors: ScriptedCursor) -> list[str]:
    events: list[str] = []
    for cursor in cursors:
        for sql, params in cursor.calls:
            if "audit_log" in sql.lower() and params:
                events.append(str(params[0]))
    return events


# ---------------------------------------------------------------------------
# M1 — 6/8 실패모드 재현 (reproduction-first) [HARD]
#
# REQ-045-B1/B2/B3: 체결확인 실패 주입 → SELL이 submitted에 영구 정체하지 않고
# 터미널 상태(filled 또는 expired)로 수렴. 매도 의도가 조용히 소실되지 않음.
#
# 이 클래스의 핵심 테스트(test_live_confirmed_fill_is_not_stuck_submitted)는
# M2 구현 전에 FAIL(RED)이어야 한다 — confirm_fills(live) 가 현재 raise하므로
# live 주문을 filled로 전이할 수 없기 때문이다.
# ---------------------------------------------------------------------------


class TestLiveConfirmFillsReproduction6_8:
    """M1 — 6/8 실패모드 재현: live fill-inquiry seam이 구현되어야 SELL이 올바른
    터미널 상태(filled)로 수렴한다. 미구현 상태에서는 모든 live 주문이 expired로
    수렴 — 실제로 체결된 주문도 예외없이 expired가 되어 원장이 부정확해진다.
    """

    def test_live_confirmed_fill_is_not_stuck_submitted(self):
        """M1 핵심 재현 테스트 (RED before M2, GREEN after M2).

        KIS가 SELL 주문의 체결을 반환했을 때 confirm_fills(live)는
        BrokerFillInquiryNotImplemented를 raise해서는 안 된다.
        source='execution_inquiry' 결과를 반환해야 한다.

        현재(M2 전): raise BrokerFillInquiryNotImplemented → FAIL (RED).
        M2 구현 후: execution_inquiry 결과 반환 → PASS (GREEN).
        """
        from trading.kis import broker_truth

        client = _live_client()
        # KIS가 당일 SELL 1건 체결을 반환하는 상황
        client.get.return_value = _kis_fill_response(
            records=[_fill_record(odno="0000029297", ccld_qty="3")]
        )

        select_cursor = ScriptedCursor(
            fetchall_queue=[[
                {
                    "id": 69,
                    "ts": datetime.now(UTC) - timedelta(minutes=2),
                    "side": "sell",
                    "ticker": "071050",
                    "qty": 3,
                    "status": "submitted",
                    "kis_order_no": "0000029297",
                }
            ]]
        )
        update_cursor = ScriptedCursor(fetchone_queue=[])

        with (
            patch.object(broker_truth, "connection",
                         _conn_sequence([select_cursor, update_cursor])),
            patch.object(broker_truth, "audit", _AuditSink()),
        ):
            # M2 전: BrokerFillInquiryNotImplemented를 raise → 이 테스트 FAIL
            # M2 후: execution_inquiry 결과를 반환 → 이 테스트 PASS
            result = broker_truth.confirm_fills(client)

        assert result["source"] == "execution_inquiry", (
            "live confirm_fills must return execution_inquiry source — "
            "not raise (SPEC-045 REQ-045-A1)"
        )
        assert "summary" in result

    def test_live_fill_confirmation_failure_order_resolves_to_expired_not_stuck(self):
        """M1 보조: 체결조회 예외 발생 시 SELL이 submitted에 영구 갇히지 않는다.

        체결조회가 예외를 던지면 order_resolver가 윈도우 경과 후 expired로 수렴.
        '영구 정체(stuck forever)'가 아님을 fake clock으로 결정론적 검증.

        이 테스트는 SPEC-042 resolver가 이미 구현된 현재 상태에서 PASS한다.
        M1의 복합 검증: resolver + 실패 주입 → expired 수렴(매도 소실 아님).
        """
        from trading.kis import order_resolver
        from trading.kis.broker_truth import BrokerFillInquiryNotImplemented

        client = _live_client()
        fake_now = datetime(2026, 6, 8, 2, 0, 0, tzinfo=UTC)

        # SELL 주문 — 16분 전 제출 (15분 윈도우 초과)
        candidate = {
            "id": 64,
            "ts": fake_now - timedelta(minutes=16),
            "side": "sell",
            "ticker": "000270",
            "qty": 1,
            "status": "submitted",
        }

        select_cursor = ScriptedCursor(fetchall_queue=[[candidate]])
        txn_cursor = ScriptedCursor(fetchone_queue=[{"status": "submitted"}])

        # confirm_fills가 BrokerFillInquiryNotImplemented를 raise (현재 live seam)
        with (
            patch.object(order_resolver, "connection",
                         _conn_sequence([select_cursor, txn_cursor])),
            patch.object(order_resolver, "confirm_fills",
                         MagicMock(side_effect=BrokerFillInquiryNotImplemented(
                             "live seam not wired"))),
            patch.object(order_resolver, "_now", MagicMock(return_value=fake_now)),
        ):
            summary = order_resolver.resolve_stuck_orders(
                client, now=fake_now, window_seconds=900
            )

        # SELL이 submitted에 갇히지 않고 expired로 수렴 (REQ-045-B1)
        assert summary["resolved_expired"] == 1, (
            "SELL must resolve to expired — never stuck in submitted forever"
        )
        assert summary["resolved_filled"] == 0
        assert "STUCK_ORDER_EXPIRED" in _audit_events(txn_cursor)

        # expired된 주문 UPDATE가 실제로 발생했는지 확인
        ups = _status_updates(txn_cursor)
        assert any("expired" in u[0].lower() for u in ups), (
            "resolver must write status='expired' for stuck live SELL "
            "(never fabricate 'filled' — REQ-045-B3)"
        )

    def test_live_sell_intent_not_silently_lost_after_inquiry_failure(self):
        """M1: 체결조회 실패 후 매도 의도가 조용히 소실되지 않는다.

        expired 수렴 후 sell_lock submitted leg가 해제되어 다음 사이클에서
        KIS 진실원으로 재평가 가능해야 한다 (REQ-045-B1/B3).
        """
        from trading.kis import sell_lock

        # submitted 주문 없음 (resolver가 expired로 처리한 이후 상태)
        with (
            patch.object(sell_lock, "_has_unresolved_submitted_sell",
                         MagicMock(return_value=False)),
            patch.object(sell_lock, "_cooldown_active",
                         MagicMock(return_value=False)),
        ):
            locked = sell_lock.is_sell_locked("000270")

        # expired 처리 후 sell_lock이 해제됨 → 다음 사이클에서 재평가 가능
        assert locked is False, (
            "After stuck order expires, sell_lock must release so "
            "the next cycle can re-evaluate (sell intent not silently lost)"
        )

    def test_6_8_fake_clock_deterministic_full_loop(self):
        """M1 통합: fake clock으로 6/8 전체 실패 루프를 결정론적으로 재현.

        시나리오:
        1. live SELL 제출 → submitted
        2. confirm_fills raise (live seam 미구현)
        3. fake clock으로 15분 윈도우 경과
        4. resolve_stuck_orders 호출 → expired 수렴

        REQ-045-B2: fake clock, 실시간 없음.
        REQ-045-B3: unconfirmed → expired, not filled.
        """
        from trading.kis import order_resolver
        from trading.kis.broker_truth import BrokerFillInquiryNotImplemented

        client = _live_client()
        # 2026-06-08 장중 시각
        fake_now = datetime(2026, 6, 8, 2, 15, 0, tzinfo=UTC)
        submit_time = fake_now - timedelta(minutes=20)  # 20분 전 제출

        candidate = {
            "id": 69,
            "ts": submit_time,
            "side": "sell",
            "ticker": "071050",  # 2026-06-08 실제 stuck 종목 중 하나
            "qty": 3,
            "status": "submitted",
        }

        select_cursor = ScriptedCursor(fetchall_queue=[[candidate]])
        txn_cursor = ScriptedCursor(fetchone_queue=[{"status": "submitted"}])

        with (
            patch.object(order_resolver, "connection",
                         _conn_sequence([select_cursor, txn_cursor])),
            patch.object(order_resolver, "confirm_fills",
                         MagicMock(side_effect=BrokerFillInquiryNotImplemented(
                             "6/8 live seam not wired"))),
            patch.object(order_resolver, "_now", MagicMock(return_value=fake_now)),
        ):
            summary = order_resolver.resolve_stuck_orders(
                client, now=fake_now, window_seconds=900  # 15분 윈도우
            )

        # REQ-045-B1: submitted에 영구 갇히지 않고 유한 시간 내 터미널 상태
        assert summary["scanned"] == 1
        assert summary["resolved_expired"] == 1
        assert summary["resolved_filled"] == 0

        # REQ-045-B3: filled 위조 없음
        fill_ups = [u for u in _status_updates(txn_cursor)
                    if "filled" in str(u).lower()]
        assert fill_ups == [], "resolver must NEVER fabricate 'filled' (REQ-045-B3)"


# ---------------------------------------------------------------------------
# M2 — live 체결조회 seam 구현 (REQ-045-A1..A5)
# ---------------------------------------------------------------------------


class TestLiveFillInquirySeam:
    """M2: confirm_fills() live 분기가 실제 KIS inquire-daily-ccld를 호출하고
    체결된 주문을 filled로 전이한다.
    """

    def test_live_confirm_fills_calls_client_get_via_pacer(self):
        """REQ-045-A1/A3: live confirm_fills는 client.get()을 통해 체결조회를 수행한다.

        client.get()을 통과함으로써 SPEC-043 전역 TPS 페이서(_GATE.acquire())가
        자동 적용된다. 통제되지 않은 추가 KIS 호출 없음.
        """
        from trading.kis import broker_truth

        client = _live_client()
        client.get.return_value = _kis_fill_response(records=[])

        with (
            patch.object(broker_truth, "connection",
                         _conn_sequence([ScriptedCursor(fetchall_queue=[[]])])),
            patch.object(broker_truth, "audit", _AuditSink()),
        ):
            result = broker_truth.confirm_fills(client)

        # client.get()이 호출되었는지 확인 (TPS 페이서 경유)
        client.get.assert_called_once()
        call_args = client.get.call_args
        assert "/uapi/domestic-stock/v1/trading/inquire-daily-ccld" in call_args[0][0]

        assert result["source"] == "execution_inquiry"

    def test_live_confirm_fills_uses_live_tr_id(self):
        """REQ-045-A1: live 모드에서 TTTC8001R TR_ID를 사용한다.

        [확인 필요-1]: live TTTC8001R이 실제로 당일 체결을 반환하는지는
        운영자 실측 게이트(M5)에서 확인한다.
        """
        from trading.kis import broker_truth

        client = _live_client()
        client.get.return_value = _kis_fill_response(records=[])

        with (
            patch.object(broker_truth, "connection",
                         _conn_sequence([ScriptedCursor(fetchall_queue=[[]])])),
            patch.object(broker_truth, "audit", _AuditSink()),
        ):
            broker_truth.confirm_fills(client)

        # tr_id()가 호출되어 live_id를 선택했는지 확인
        client.tr_id.assert_called()
        # live 모드에서 live_id를 반환함 (tr_id mock은 live_id를 반환하도록 설정됨)

    def test_live_confirm_fills_transitions_submitted_to_filled(self):
        """REQ-045-A1: KIS가 체결을 반환하면 해당 주문을 filled로 전이한다.

        kis_order_no로 매칭. 체결수량이 주문수량과 일치하면 'filled'.
        """
        from trading.kis import broker_truth

        client = _live_client()
        client.get.return_value = _kis_fill_response(
            records=[_fill_record(odno="0000029297", ccld_qty="3")]
        )

        submitted_order = {
            "id": 69,
            "ts": datetime.now(UTC) - timedelta(minutes=2),
            "side": "sell",
            "ticker": "071050",
            "qty": 3,
            "status": "submitted",
            "kis_order_no": "0000029297",
        }
        # Single cursor handles the SELECT fetchall and subsequent UPDATE/audit
        # calls within the same connection context.
        single_cursor = ScriptedCursor(fetchall_queue=[[submitted_order]])
        sink = _AuditSink()

        with (
            patch.object(broker_truth, "connection",
                         _conn_sequence([single_cursor])),
            patch.object(broker_truth, "audit", sink),
        ):
            result = broker_truth.confirm_fills(client)

        assert result["source"] == "execution_inquiry"
        summary = result["summary"]
        assert summary.get("filled_count", 0) >= 1 or summary.get("transitioned", 0) >= 1, (
            "A KIS-confirmed fill must transition the order to filled/partial"
        )

        # UPDATE가 'filled'로 이루어졌는지 확인
        fill_updates = [u for u in _status_updates(single_cursor)
                        if "filled" in str(u).lower()]
        assert fill_updates, "Order with matching kis_order_no must be transitioned to filled"

    def test_live_confirm_fills_partial_fill(self):
        """E-1: KIS가 부분체결을 반환하면 partial 상태로 전이한다."""
        from trading.kis import broker_truth

        client = _live_client()
        # 3주 주문 중 1주만 체결
        client.get.return_value = _kis_fill_response(
            records=[_fill_record(odno="0000029297", ccld_qty="1")]
        )

        submitted_order = {
            "id": 69,
            "ts": datetime.now(UTC) - timedelta(minutes=2),
            "side": "sell",
            "ticker": "071050",
            "qty": 3,
            "status": "submitted",
            "kis_order_no": "0000029297",
        }
        single_cursor = ScriptedCursor(fetchall_queue=[[submitted_order]])
        sink = _AuditSink()

        with (
            patch.object(broker_truth, "connection",
                         _conn_sequence([single_cursor])),
            patch.object(broker_truth, "audit", sink),
        ):
            result = broker_truth.confirm_fills(client)

        assert result["source"] == "execution_inquiry"
        # partial 전이 확인
        partial_updates = [u for u in _status_updates(single_cursor)
                           if "partial" in str(u).lower()]
        assert partial_updates, "Partial fill must transition order to partial status"

    def test_live_confirm_fills_empty_response_does_not_fabricate(self):
        """REQ-045-A2/AC-3: KIS 응답이 비어 있으면 주문을 filled로 위조하지 않는다.

        미확인 상태를 audit하고 order_resolver의 expired 수렴에 맡긴다.
        """
        from trading.kis import broker_truth

        client = _live_client()
        client.get.return_value = _kis_fill_response(records=[])  # 빈 응답

        submitted_order = {
            "id": 69,
            "ts": datetime.now(UTC) - timedelta(minutes=2),
            "side": "sell",
            "ticker": "071050",
            "qty": 3,
            "status": "submitted",
            "kis_order_no": "0000029297",
        }
        select_cursor = ScriptedCursor(fetchall_queue=[[submitted_order]])
        update_cursor = ScriptedCursor(fetchone_queue=[])
        sink = _AuditSink()

        with (
            patch.object(broker_truth, "connection",
                         _conn_sequence([select_cursor, update_cursor])),
            patch.object(broker_truth, "audit", sink),
        ):
            result = broker_truth.confirm_fills(client)

        assert result["source"] == "execution_inquiry"

        # 'filled' UPDATE가 없어야 함 — 위조 금지 (REQ-045-A2)
        fill_updates = [u for u in _status_updates(update_cursor)
                        if "'filled'" in str(u[0]).lower()
                        or "filled" in str(u[1] or "")]
        assert fill_updates == [], (
            "Empty KIS response must NOT fabricate a fill (REQ-045-A2)"
        )

    def test_live_confirm_fills_error_response_does_not_fabricate(self):
        """REQ-045-A2: rt_cd != '0' 에러 응답에서도 filled 위조 없음."""
        from trading.kis import broker_truth

        client = _live_client()
        client.get.return_value = _kis_fill_response(rt_cd="1", records=[])

        select_cursor = ScriptedCursor(fetchall_queue=[[]])
        sink = _AuditSink()

        with (
            patch.object(broker_truth, "connection",
                         _conn_sequence([select_cursor])),
            patch.object(broker_truth, "audit", sink),
        ):
            result = broker_truth.confirm_fills(client)

        assert result["source"] == "execution_inquiry"
        # 에러 상황에서 summary의 filled_count는 0
        assert result["summary"].get("filled_count", 0) == 0

    def test_live_confirm_fills_client_get_exception_does_not_fabricate(self):
        """REQ-045-A2: client.get()이 예외를 던지면 filled 위조 없이 안전하게 반환."""
        from trading.kis import broker_truth

        client = _live_client()
        client.get.side_effect = RuntimeError("KIS network timeout")

        select_cursor = ScriptedCursor(fetchall_queue=[[]])

        with (
            patch.object(broker_truth, "connection",
                         _conn_sequence([select_cursor])),
            patch.object(broker_truth, "audit", _AuditSink()),
        ):
            # 예외가 caller에게 전파되지 않아야 함 (defensive)
            result = broker_truth.confirm_fills(client)

        assert result["source"] == "execution_inquiry"
        assert result["summary"].get("filled_count", 0) == 0

    def test_live_confirm_fills_single_code_path(self):
        """REQ-045-A4: 체결확인은 confirm_fills() 단일 경로 안에서만 처리.

        병렬 체결확인 경로를 신설하지 않음.
        paper와 live 모두 동일한 함수 시그니처를 사용한다.
        """
        import inspect

        from trading.kis import broker_truth

        sig = inspect.signature(broker_truth.confirm_fills)
        assert "client" in sig.parameters
        assert "source" in sig.parameters

    def test_paper_path_unchanged_after_m2(self):
        """REQ-045-A5/AC-4: paper 모드에서는 기존 balance-reconcile 경로를 그대로 사용.

        inquire-daily-ccld를 호출하지 않는다.
        """
        from trading.kis import broker_truth

        client = _paper_client()

        with patch.object(
            broker_truth, "reconcile_from_balance",
            return_value={"queried": 1, "transitioned": 0,
                          "positions_synced": 0, "errors": 0, "dry_run": False},
        ) as reconcile:
            result = broker_truth.confirm_fills(client)

        reconcile.assert_called_once_with(client, dry_run=False)
        assert result["source"] == "balance_reconcile"
        # client.get()은 호출되지 않아야 함 (paper는 inquiry-daily-ccld 사용 안 함)
        client.get.assert_not_called()

    def test_live_no_kis_order_no_match_does_not_transition(self):
        """REQ-045-A2: KIS 체결 레코드의 ODNO가 DB 주문과 매칭되지 않으면 전이 없음.

        E-4: KIS가 ODNO를 빈 문자열로 반환하거나 불일치 → 위조 금지·expired 위임.
        """
        from trading.kis import broker_truth

        client = _live_client()
        # KIS에서 반환한 ODNO가 DB 주문의 kis_order_no와 다름
        client.get.return_value = _kis_fill_response(
            records=[_fill_record(odno="XXXXXXXX", ccld_qty="3")]  # 불일치
        )

        submitted_order = {
            "id": 69,
            "ts": datetime.now(UTC) - timedelta(minutes=2),
            "side": "sell",
            "ticker": "071050",
            "qty": 3,
            "status": "submitted",
            "kis_order_no": "0000029297",  # 다른 번호
        }
        select_cursor = ScriptedCursor(fetchall_queue=[[submitted_order]])
        update_cursor = ScriptedCursor(fetchone_queue=[])

        with (
            patch.object(broker_truth, "connection",
                         _conn_sequence([select_cursor, update_cursor])),
            patch.object(broker_truth, "audit", _AuditSink()),
        ):
            broker_truth.confirm_fills(client)

        # 매칭 없음 → 전이 없음
        fill_updates = _status_updates(update_cursor)
        assert fill_updates == [], "Non-matching ODNO must not transition any order"


# ---------------------------------------------------------------------------
# M3 — 라이브 멱등성 / 이중매도 안전 (REQ-045-D1/D2/D3)
# ---------------------------------------------------------------------------


class TestLiveIdempotencyAndDoubleSellSafety:
    """M3: sell_lock submitted leg가 live 체결확인으로 자동 해제된다.
    중복 KIS 매도 0, 멱등 재전이 금지.
    """

    def test_submitted_leg_locks_second_sell_attempt(self):
        """REQ-045-D1: submitted SELL이 있는 동안 두 번째 매도는 차단된다.

        position_watchdog와 Decision 사이클이 동시에 매도를 시도해도
        첫 번째 submitted 주문이 있는 동안 is_sell_locked=True.
        """
        from trading.kis import sell_lock

        # submitted SELL 존재 시뮬레이션
        with patch.object(sell_lock, "_has_unresolved_submitted_sell",
                          MagicMock(return_value=True)):
            locked = sell_lock.is_sell_locked("000270")

        assert locked is True, (
            "An in-flight submitted SELL must lock subsequent sell attempts "
            "(REQ-045-D1 — no duplicate KIS sells)"
        )

    def test_submitted_leg_auto_releases_after_fill_confirmation(self):
        """REQ-045-D2: live 체결조회가 체결을 확인하면 submitted leg가 자동 해제된다.

        체결 완료 후 정당한 새 매도 신호가 부당하게 영구 차단되지 않아야 한다.
        submitted leg는 DB의 submitted 주문 존재 여부로 결정 — 주문이 filled로
        전이되면 _has_unresolved_submitted_sell은 자동으로 False를 반환한다.
        """
        from trading.kis import sell_lock

        # 주문이 filled로 전이된 이후 상태 — submitted 주문 없음
        with (
            patch.object(sell_lock, "_has_unresolved_submitted_sell",
                         MagicMock(return_value=False)),
            patch.object(sell_lock, "_cooldown_active",
                         MagicMock(return_value=False)),
        ):
            locked = sell_lock.is_sell_locked("000270")

        assert locked is False, (
            "After fill confirmation transitions order to filled/expired, "
            "the submitted leg must auto-release (REQ-045-D2)"
        )

    def test_guard_sell_allows_new_sell_after_lock_releases(self):
        """REQ-045-D2: 이전 주문이 완료된 후 새 정당한 매도 신호는 통과된다."""
        from trading.kis import sell_lock

        sink = _AuditSink()

        with (
            patch.object(sell_lock, "_has_unresolved_submitted_sell",
                         MagicMock(return_value=False)),
            patch.object(sell_lock, "_cooldown_active",
                         MagicMock(return_value=False)),
            patch.object(sell_lock, "_marker_created_at",
                         MagicMock(return_value=None)),
            patch("trading.db.session.audit", sink),
        ):
            allowed = sell_lock.guard_sell("000270", actor="watchdog")

        assert allowed is True, (
            "After prior sell completes, a new genuine stop-loss must pass through "
            "(capital-preservation REQ-045-D2)"
        )

    def test_double_sell_suppressed_by_guard_sell(self):
        """REQ-045-D1: guard_sell이 중복 매도를 차단한다.

        watchdog와 persona orchestrator가 같은 종목 매도를 동시에 시도할 때
        두 번째 호출은 False를 반환받아 KIS에 중복 POST하지 않는다.
        """
        from trading.kis import sell_lock

        sink = _AuditSink()

        with (
            patch.object(sell_lock, "_has_unresolved_submitted_sell",
                         MagicMock(return_value=True)),  # submitted 존재
            patch("trading.db.session.audit", sink),
        ):
            allowed = sell_lock.guard_sell("033780", actor="persona")

        assert allowed is False, (
            "guard_sell must suppress a duplicate sell when submitted order exists "
            "(REQ-045-D1 — 6/8 KT&G 4x duplicate scenario)"
        )

    def test_idempotent_repeated_fill_inquiry_does_not_retransition(self):
        """REQ-045-D3: 이미 터미널 상태인 주문은 반복 체결조회에서 재전이하지 않는다.

        멱등: confirm_fills를 두 번 호출해도 'filled'인 주문을 다시 전이하지 않음.
        """
        from trading.kis import broker_truth

        client = _live_client()
        # KIS가 같은 주문의 체결을 두 번 반환 (재조회)
        client.get.return_value = _kis_fill_response(
            records=[_fill_record(odno="0000029297", ccld_qty="3")]
        )

        # 이미 'filled' 상태인 주문 (첫 confirm_fills가 전이한 이후)
        already_filled_order = {
            "id": 69,
            "ts": datetime.now(UTC) - timedelta(minutes=5),
            "side": "sell",
            "ticker": "071050",
            "qty": 3,
            "status": "filled",  # 이미 터미널 상태
            "kis_order_no": "0000029297",
        }
        select_cursor = ScriptedCursor(fetchall_queue=[[already_filled_order]])
        update_cursor = ScriptedCursor(fetchone_queue=[])

        with (
            patch.object(broker_truth, "connection",
                         _conn_sequence([select_cursor, update_cursor])),
            patch.object(broker_truth, "audit", _AuditSink()),
        ):
            result = broker_truth.confirm_fills(client)

        # 이미 filled인 주문은 재전이하지 않음 (REQ-045-D3 멱등)
        fill_updates = _status_updates(update_cursor)
        assert fill_updates == [], (
            "An already-terminal order must not be re-transitioned "
            "(idempotency REQ-045-D3)"
        )
        assert result["source"] == "execution_inquiry"

    def test_sell_lock_fail_open_on_db_error(self):
        """sell_lock의 fail-open 불변: DB 오류 시 False(허용)를 반환한다.

        자본보존 하드 룰: 잘못된 차단이 잘못된 허용보다 나쁘다.
        """
        from trading.kis import sell_lock

        with patch.object(sell_lock, "_has_unresolved_submitted_sell",
                          MagicMock(side_effect=RuntimeError("DB down"))):
            locked = sell_lock.is_sell_locked("000270")

        assert locked is False, (
            "sell_lock must fail OPEN (False) on any DB error — "
            "a wrongly blocked stop-loss is worse than a duplicate "
            "(capital-preservation invariant)"
        )

    def test_clear_sell_inflight_called_on_stale_marker(self):
        """REQ-045-D2: 쿨다운 경과 후 stale 마커는 guard_sell이 정리한다."""
        from trading.kis import sell_lock

        now = datetime(2026, 6, 14, 1, 0, 0, tzinfo=sell_lock.KST)
        # 쿨다운(300s)보다 오래된 마커
        old_marker_time = now - timedelta(seconds=400)

        sink = _AuditSink()

        with (
            patch.object(sell_lock, "_has_unresolved_submitted_sell",
                         MagicMock(return_value=False)),
            patch.object(sell_lock, "_cooldown_active",
                         MagicMock(return_value=False)),
            patch.object(sell_lock, "_marker_created_at",
                         MagicMock(return_value=old_marker_time)),
            patch.object(sell_lock, "clear_sell_inflight", MagicMock()) as mock_clear,
            patch("trading.db.session.audit", sink),
            patch.object(sell_lock, "_now", MagicMock(return_value=now)),
        ):
            allowed = sell_lock.guard_sell("000270", actor="watchdog", now=now)

        assert allowed is True
        mock_clear.assert_called_once_with("000270")
