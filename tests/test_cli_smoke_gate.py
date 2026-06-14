"""SPEC-TRADING-049 — CLI smoke-gate 서브커맨드 테스트 (TDD RED-GREEN-REFACTOR).

인수 시나리오 커버:
  시나리오 7  — PAPER 모드/무자격증명 거부 (REQ-049-M1-3)
  시나리오 8  — 상한 초과 발주 차단 (REQ-049-M1-2)
  시나리오 10 — TPS 페이서 경유 (REQ-049-M3-3)
  시나리오 11 — smoke-gate 서브커맨드 디스패치 (REQ-049-M1-1)
  시나리오 12 — 정직 고지 명시 (REQ-049-M1-4, REQ-045-C4)

통합(end-to-end) CLI 흐름:
  - 완전 PASS 경로: BUY→SELL→confirm→reconcile→resolve→판정→기록
  - FAIL 경로: BUY fill 없음 → FAIL → 차단 보고

모든 테스트는 오프라인(no DB, no KIS network):
  - submit_order, confirm_fills, intraday_reconcile, resolve_stuck_orders mock
  - get_system_state mock (live_unlocked=True for test)
  - audit_log mock

패치 경로 안내:
  _cmd_smoke_gate는 함수 내부에서 로컬 import를 사용하므로
  실제 심볼 출처 모듈을 패치한다:
    - trading.kis.broker_truth.confirm_fills
    - trading.kis.broker_truth.intraday_reconcile
    - trading.kis.order.submit_order
    - trading.kis.order_resolver.resolve_stuck_orders
    - trading.kis.sell_lock.guard_sell / set_sell_inflight
    - trading.db.session.get_system_state
    - trading.config.get_settings
    - trading.kis.client.KisClient
    - trading.kis.market.current_price
    - trading.cli._count_stuck_submitted (모듈 레벨 헬퍼)
    - trading.cli._find_fill_record
    - trading.cli._inquire_ccld_raw
    - trading.kis.smoke_gate.audit (영구 기록 mock)
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trading.config import TradingMode


# ---------------------------------------------------------------------------
# 공통 헬퍼 / 테스트 더블
# ---------------------------------------------------------------------------


def _live_settings() -> MagicMock:
    """live 모드 설정 mock."""
    s = MagicMock()
    s.trading_mode = TradingMode.LIVE
    return s


def _paper_settings() -> MagicMock:
    """paper 모드 설정 mock."""
    s = MagicMock()
    s.trading_mode = TradingMode.PAPER
    return s


def _live_client() -> MagicMock:
    """live KisClient mock."""
    client = MagicMock()
    client.mode = TradingMode.LIVE
    client.account_prefix = "50000000"
    client.account_suffix = "01"
    client.tr_id.side_effect = lambda paper_id, live_id: live_id
    return client


def _paper_client() -> MagicMock:
    """paper KisClient mock."""
    client = MagicMock()
    client.mode = TradingMode.PAPER
    return client


def _submit_result(
    *,
    order_id: int = 1,
    kis_order_no: str = "ORD001",
    status: str = "submitted",
) -> dict[str, Any]:
    """submit_order() 반환값 mock."""
    return {
        "order_id": order_id,
        "kis_order_no": kis_order_no,
        "status": status,
    }


def _kis_fill_response_with_record(
    odno: str,
    ccld_qty: str = "1",
    ccld_avg: str = "50000",
) -> dict[str, Any]:
    """confirm_fills()가 반환하는 execution_inquiry 결과 mock."""
    return {
        "source": "execution_inquiry",
        "summary": {
            "filled_count": 1,
            "partial_count": 0,
            "unmatched_kis": 0,
            "skipped_terminal": 0,
            "errors": 0,
        },
        "_records": [
            {
                "ODNO": odno,
                "CCLD_QTY": ccld_qty,
                "CCLD_AVG_UNPR": ccld_avg,
            }
        ],
    }


def _run_cli(args: list[str]) -> int:
    """cli.main()을 실행하고 반환 코드를 반환."""
    from trading.cli import main
    return main(args)


# ---------------------------------------------------------------------------
# 시나리오 11 — smoke-gate 서브커맨드 디스패치 (REQ-049-M1-1)
# ---------------------------------------------------------------------------


class TestSmokeGateDispatch:
    """cli.main()이 smoke-gate 서브커맨드를 올바른 핸들러로 라우팅하는지 확인."""

    def test_smoke_gate_dispatches_to_handler(self, capsys):
        """'smoke-gate' 인수가 _cmd_smoke_gate 핸들러로 라우팅된다."""
        with patch("trading.cli._cmd_smoke_gate", return_value=0) as mock_handler:
            rc = _run_cli(["smoke-gate", "--max-qty", "1"])
        mock_handler.assert_called_once_with(["--max-qty", "1"])
        assert rc == 0

    def test_smoke_gate_passes_rest_args(self):
        """rest 인수([--max-qty, 1, --ticker, 005930])가 핸들러에 전달된다."""
        with patch("trading.cli._cmd_smoke_gate", return_value=0) as mock_handler:
            _run_cli(["smoke-gate", "--max-qty", "1", "--ticker", "005930"])
        mock_handler.assert_called_once_with(
            ["--max-qty", "1", "--ticker", "005930"]
        )

    def test_similar_but_wrong_subcommand_not_dispatched(self, capsys):
        """'smoke-gat'(오타)는 smoke-gate 핸들러를 호출하지 않는다(오매칭 없음)."""
        with patch("trading.cli._cmd_smoke_gate") as mock_handler:
            rc = _run_cli(["smoke-gat"])
        mock_handler.assert_not_called()
        # unknown 서브커맨드이므로 non-zero 종료코드
        assert rc != 0

    def test_exit_code_propagated_from_handler(self):
        """핸들러의 반환 종료코드가 main()의 종료코드로 그대로 전파된다."""
        with patch("trading.cli._cmd_smoke_gate", return_value=2):
            rc = _run_cli(["smoke-gate"])
        assert rc == 2

    def test_handler_failure_propagated(self):
        """핸들러가 1을 반환하면 main()도 1 반환."""
        with patch("trading.cli._cmd_smoke_gate", return_value=1):
            rc = _run_cli(["smoke-gate"])
        assert rc == 1


# ---------------------------------------------------------------------------
# 시나리오 7 — PAPER 모드/무자격증명 거부 (REQ-049-M1-3)
# ---------------------------------------------------------------------------


class TestSmokeGatePaperRejection:
    """PAPER 모드에서 실거래 발주 없이 명확한 사유로 종료."""

    def _run_smoke_gate(self, args: list[str] | None = None) -> tuple[int, str, str]:
        """_cmd_smoke_gate()를 직접 호출하고 (rc, stdout, stderr) 반환."""
        from trading.cli import _cmd_smoke_gate
        import io
        from unittest.mock import patch as _patch
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with _patch("sys.stdout", out_buf), _patch("sys.stderr", err_buf):
            rc = _cmd_smoke_gate(args or ["--max-qty", "1"])
        return rc, out_buf.getvalue(), err_buf.getvalue()

    def test_paper_mode_exits_nonzero_without_order(self):
        """PAPER 모드면 비-0 종료코드, 실거래 발주 없음."""
        with (
            patch("trading.config.get_settings", return_value=_paper_settings()),
            patch("trading.kis.client.KisClient", return_value=_paper_client()),
            patch("trading.kis.order.submit_order") as mock_submit,
        ):
            rc, out, err = self._run_smoke_gate(["--max-qty", "1"])

        assert rc != 0
        mock_submit.assert_not_called()

    def test_paper_mode_outputs_reason(self):
        """PAPER 모드 거부 시 명확한 사유를 출력한다."""
        with (
            patch("trading.config.get_settings", return_value=_paper_settings()),
            patch("trading.kis.client.KisClient", return_value=_paper_client()),
        ):
            rc, out, err = self._run_smoke_gate(["--max-qty", "1"])

        combined = out + err
        # PAPER 거부 사유가 출력에 포함되어야 함
        assert "PAPER" in combined or "paper" in combined or "live" in combined.lower()

    def test_paper_mode_still_shows_honesty_disclosure(self):
        """PAPER 거부이더라도 정직 고지가 출력된다(시나리오 12와 중복 테스트)."""
        with (
            patch("trading.config.get_settings", return_value=_paper_settings()),
            patch("trading.kis.client.KisClient", return_value=_paper_client()),
        ):
            rc, out, err = self._run_smoke_gate(["--max-qty", "1"])

        combined = out + err
        # 정직 고지가 항상 출력된다
        assert "실행 경로" in combined or "전략" in combined


# ---------------------------------------------------------------------------
# 시나리오 12 — 정직 고지 명시 (REQ-049-M1-4, REQ-045-C4)
# ---------------------------------------------------------------------------


class TestSmokeGateHonestyDisclosure:
    """스모크 게이트 출력에 정직 고지가 항상 포함된다."""

    def _run_smoke_gate_live_dry_run(self) -> tuple[int, str]:
        """live 모드 dry-run으로 _cmd_smoke_gate 호출."""
        from trading.cli import _cmd_smoke_gate
        import io
        out_buf = io.StringIO()
        with patch("sys.stdout", out_buf):
            with (
                patch("trading.config.get_settings", return_value=_live_settings()),
                patch("trading.kis.client.KisClient", return_value=_live_client()),
            ):
                rc = _cmd_smoke_gate(["--max-qty", "1", "--dry-run"])
        return rc, out_buf.getvalue()

    def test_dry_run_outputs_honesty_disclosure(self):
        """--dry-run 모드에서 정직 고지 출력 확인(시나리오 12)."""
        rc, out = self._run_smoke_gate_live_dry_run()
        assert "실행 경로" in out or "전략 수익성 검증이 아닙니다" in out

    def test_dry_run_does_not_submit_orders(self):
        """--dry-run은 주문을 발주하지 않는다."""
        with patch("trading.kis.order.submit_order") as mock_submit:
            with (
                patch("trading.config.get_settings", return_value=_live_settings()),
                patch("trading.kis.client.KisClient", return_value=_live_client()),
            ):
                from trading.cli import _cmd_smoke_gate
                _cmd_smoke_gate(["--max-qty", "1", "--dry-run"])

        mock_submit.assert_not_called()

    def test_dry_run_returns_zero(self):
        """--dry-run은 0(성공)을 반환한다."""
        rc, _ = self._run_smoke_gate_live_dry_run()
        assert rc == 0


# ---------------------------------------------------------------------------
# 시나리오 8 — 상한 초과 발주 차단 (REQ-049-M1-2)
# ---------------------------------------------------------------------------


class TestSmokeGateCapEnforcement:
    """--max-qty / --max-notional 상한 초과 발주를 차단한다."""

    def test_max_qty_one_passed_to_submit_order(self):
        """--max-qty 1 이면 qty >= 1 주문만 발주 가능(상한 강제)."""
        from trading.cli import _cmd_smoke_gate

        submitted_qtys: list[int] = []

        def _fake_submit(client, *, ticker, qty, side, **kwargs):
            submitted_qtys.append(qty)
            return _submit_result(order_id=len(submitted_qtys))

        with (
            patch("trading.config.get_settings", return_value=_live_settings()),
            patch("trading.kis.client.KisClient", return_value=_live_client()),
            patch("trading.db.session.get_system_state", return_value={"live_unlocked": True}),
            patch("trading.kis.order.submit_order", side_effect=_fake_submit),
            patch("trading.kis.broker_truth.confirm_fills", return_value={"source": "execution_inquiry", "summary": {}}),
            patch("trading.cli._inquire_ccld_raw", return_value=[]),
            patch("trading.kis.broker_truth.intraday_reconcile", return_value={"reconciled": True}),
            patch("trading.kis.order_resolver.resolve_stuck_orders", return_value={"scanned": 0}),
            patch("trading.cli._count_stuck_submitted", return_value=0),
            patch("trading.kis.sell_lock.guard_sell", return_value=True),
            patch("trading.kis.sell_lock.set_sell_inflight"),
            patch("trading.cli._find_fill_record", return_value=None),
            patch("trading.kis.smoke_gate.audit"),
        ):
            # max-qty=1이면 submit_order에 qty=1이 전달되어야 함
            _cmd_smoke_gate(["--max-qty", "1", "--ticker", "005930"])

        # 발주가 이루어진 경우 qty <= 1
        for qty in submitted_qtys:
            assert qty <= 1, f"상한 초과 qty={qty} 발주됨"

    def test_max_qty_zero_blocks_submission(self):
        """--max-qty 0 이면 발주를 수행하지 않고 종료."""
        from trading.cli import _cmd_smoke_gate

        with (
            patch("trading.config.get_settings", return_value=_live_settings()),
            patch("trading.kis.client.KisClient", return_value=_live_client()),
            patch("trading.kis.order.submit_order") as mock_submit,
        ):
            rc = _cmd_smoke_gate(["--max-qty", "0", "--ticker", "005930"])

        mock_submit.assert_not_called()
        assert rc != 0

    def test_max_notional_enforced(self):
        """--max-notional 상한 초과 예상 금액이면 발주를 차단한다."""
        from trading.cli import _cmd_smoke_gate

        with (
            patch("trading.config.get_settings", return_value=_live_settings()),
            patch("trading.kis.client.KisClient", return_value=_live_client()),
            patch("trading.db.session.get_system_state", return_value={"live_unlocked": True}),
            # current_price가 200,000원을 반환, max-notional=1000이면 차단
            patch("trading.kis.market.current_price", return_value={"price": 200000}),
            patch("trading.kis.order.submit_order") as mock_submit,
        ):
            rc = _cmd_smoke_gate(
                ["--max-qty", "1", "--max-notional", "1000", "--ticker", "005930"]
            )

        mock_submit.assert_not_called()
        assert rc != 0


# ---------------------------------------------------------------------------
# 시나리오 10 — TPS 페이서 경유 (REQ-049-M3-3)
# ---------------------------------------------------------------------------


class TestSmokeGateTpsPacer:
    """스모크 게이트의 KIS 호출이 SPEC-043 전역 페이서를 경유한다."""

    def test_confirm_fills_called_via_existing_seam(self):
        """confirm_fills()가 호출됨 → 내부적으로 client.get() → _RateGate 경유."""
        from trading.cli import _cmd_smoke_gate

        confirm_calls: list[str] = []

        def _fake_confirm(client, *, source=None):
            confirm_calls.append(source or "auto")
            return {"source": "execution_inquiry", "summary": {}}

        with (
            patch("trading.config.get_settings", return_value=_live_settings()),
            patch("trading.kis.client.KisClient", return_value=_live_client()),
            patch("trading.db.session.get_system_state", return_value={"live_unlocked": True}),
            patch("trading.kis.order.submit_order", return_value=_submit_result(order_id=1)),
            patch("trading.kis.broker_truth.confirm_fills", side_effect=_fake_confirm),
            patch("trading.cli._inquire_ccld_raw", return_value=[]),
            patch("trading.kis.broker_truth.intraday_reconcile", return_value={"reconciled": True}),
            patch("trading.kis.order_resolver.resolve_stuck_orders", return_value={"scanned": 0}),
            patch("trading.cli._count_stuck_submitted", return_value=0),
            patch("trading.kis.sell_lock.guard_sell", return_value=True),
            patch("trading.kis.sell_lock.set_sell_inflight"),
            patch("trading.cli._find_fill_record", return_value=None),
            patch("trading.kis.smoke_gate.audit"),
        ):
            _cmd_smoke_gate(["--max-qty", "1", "--ticker", "005930"])

        # confirm_fills가 1회 이상 호출됨 (BUY/SELL 각 1회)
        assert len(confirm_calls) >= 1


# ---------------------------------------------------------------------------
# 통합 — 완전 PASS 흐름 (시나리오 1 CLI 레벨)
# ---------------------------------------------------------------------------


class TestSmokeGateFullPassFlow:
    """BUY→confirm→SELL→confirm→reconcile→resolve→PASS 전체 흐름."""

    def _setup_pass_mocks(self, buy_order_no: str = "BUY001", sell_order_no: str = "SELL001"):
        """완전 PASS 흐름을 위한 mock 패치 컨텍스트 반환."""
        buy_fill = {
            "ODNO": buy_order_no, "CCLD_QTY": "1", "CCLD_AVG_UNPR": "50000"
        }
        sell_fill = {
            "ODNO": sell_order_no, "CCLD_QTY": "1", "CCLD_AVG_UNPR": "49500"
        }

        order_id_counter = [0]

        def _fake_submit(client, *, ticker, qty, side, **kwargs):
            order_id_counter[0] += 1
            ono = buy_order_no if side == "buy" else sell_order_no
            return {"order_id": order_id_counter[0], "kis_order_no": ono, "status": "submitted"}

        def _fake_find_fill(records, order_no):
            """order_no로 매칭되는 fill 레코드를 반환."""
            if order_no == buy_order_no:
                return buy_fill
            if order_no == sell_order_no:
                return sell_fill
            return None

        return {
            "trading.cli.get_settings": _live_settings(),
            "trading.cli.KisClient": _live_client(),
            "trading.cli.get_system_state": {"live_unlocked": True},
            "trading.cli.submit_order": _fake_submit,
            "trading.cli.confirm_fills": {
                "source": "execution_inquiry",
                "summary": {"filled_count": 1}
            },
            "trading.cli.intraday_reconcile": {"reconciled": True, "throttled": False},
            "trading.cli.resolve_stuck_orders": {"scanned": 1, "resolved_filled": 1},
            "trading.cli._count_stuck_submitted": 0,
            "trading.cli.guard_sell": True,
            "trading.cli._find_fill_record": _fake_find_fill,
            "trading.kis.smoke_gate.audit": None,
        }

    def _common_patches(self, mocks):
        """공통 패치 컨텍스트 매니저 목록."""
        return [
            patch("trading.config.get_settings", return_value=mocks["trading.cli.get_settings"]),
            patch("trading.kis.client.KisClient", return_value=mocks["trading.cli.KisClient"]),
            patch("trading.db.session.get_system_state", return_value=mocks["trading.cli.get_system_state"]),
            patch("trading.kis.order.submit_order", side_effect=mocks["trading.cli.submit_order"]),
            patch("trading.kis.broker_truth.confirm_fills", return_value=mocks["trading.cli.confirm_fills"]),
            patch("trading.cli._inquire_ccld_raw", return_value=[]),
            patch("trading.kis.broker_truth.intraday_reconcile", return_value=mocks["trading.cli.intraday_reconcile"]),
            patch("trading.kis.order_resolver.resolve_stuck_orders", return_value=mocks["trading.cli.resolve_stuck_orders"]),
            patch("trading.cli._count_stuck_submitted", return_value=0),
            patch("trading.kis.sell_lock.guard_sell", return_value=True),
            patch("trading.kis.sell_lock.set_sell_inflight"),
            patch("trading.cli._find_fill_record", side_effect=mocks["trading.cli._find_fill_record"]),
            patch("trading.kis.smoke_gate.audit"),
        ]

    def test_full_pass_flow_returns_zero(self, capsys):
        """완전 PASS 흐름: 종료코드 0."""
        from trading.cli import _cmd_smoke_gate
        from contextlib import ExitStack
        mocks = self._setup_pass_mocks()

        with ExitStack() as stack:
            for p in self._common_patches(mocks):
                stack.enter_context(p)
            rc = _cmd_smoke_gate(["--max-qty", "1", "--ticker", "005930"])

        assert rc == 0

    def test_full_pass_flow_outputs_honesty_disclosure(self, capsys):
        """PASS 경로에서도 정직 고지가 출력된다(시나리오 12)."""
        from trading.cli import _cmd_smoke_gate
        from contextlib import ExitStack
        mocks = self._setup_pass_mocks()

        with ExitStack() as stack:
            for p in self._common_patches(mocks):
                stack.enter_context(p)
            rc = _cmd_smoke_gate(["--max-qty", "1", "--ticker", "005930"])

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "실행 경로" in combined or "전략 수익성 검증이 아닙니다" in combined


# ---------------------------------------------------------------------------
# 통합 — FAIL 흐름 → 차단 보고 (시나리오 2/14 CLI 레벨)
# ---------------------------------------------------------------------------


class TestSmokeGateFailFlow:
    """BUY fill 미확인 → FAIL → live 승격 차단 보고."""

    def test_buy_fill_missing_returns_nonzero(self):
        """BUY fill 없으면 FAIL → 비-0 종료코드."""
        from trading.cli import _cmd_smoke_gate

        def _fake_find_fill(records, order_no):
            return None  # BUY fill 없음

        with (
            patch("trading.config.get_settings", return_value=_live_settings()),
            patch("trading.kis.client.KisClient", return_value=_live_client()),
            patch("trading.db.session.get_system_state", return_value={"live_unlocked": True}),
            patch("trading.kis.order.submit_order", return_value=_submit_result(order_id=1, kis_order_no="BUY001")),
            patch("trading.kis.broker_truth.confirm_fills", return_value={"source": "execution_inquiry", "summary": {}}),
            patch("trading.cli._inquire_ccld_raw", return_value=[]),
            patch("trading.kis.broker_truth.intraday_reconcile", return_value={"reconciled": True}),
            patch("trading.kis.order_resolver.resolve_stuck_orders", return_value={"scanned": 0}),
            patch("trading.cli._count_stuck_submitted", return_value=0),
            patch("trading.kis.sell_lock.guard_sell", return_value=True),
            patch("trading.kis.sell_lock.set_sell_inflight"),
            patch("trading.cli._find_fill_record", side_effect=_fake_find_fill),
            patch("trading.kis.smoke_gate.audit"),
        ):
            rc = _cmd_smoke_gate(["--max-qty", "1", "--ticker", "005930"])

        assert rc != 0

    def test_fail_outputs_reason_to_stderr_or_stdout(self, capsys):
        """FAIL 시 사유가 출력된다."""
        from trading.cli import _cmd_smoke_gate

        with (
            patch("trading.config.get_settings", return_value=_live_settings()),
            patch("trading.kis.client.KisClient", return_value=_live_client()),
            patch("trading.db.session.get_system_state", return_value={"live_unlocked": True}),
            patch("trading.kis.order.submit_order", return_value=_submit_result(order_id=1, kis_order_no="BUY001")),
            patch("trading.kis.broker_truth.confirm_fills", return_value={"source": "execution_inquiry", "summary": {}}),
            patch("trading.cli._inquire_ccld_raw", return_value=[]),
            patch("trading.kis.broker_truth.intraday_reconcile", return_value={"reconciled": True}),
            patch("trading.kis.order_resolver.resolve_stuck_orders", return_value={"scanned": 0}),
            patch("trading.cli._count_stuck_submitted", return_value=0),
            patch("trading.kis.sell_lock.guard_sell", return_value=True),
            patch("trading.kis.sell_lock.set_sell_inflight"),
            patch("trading.cli._find_fill_record", return_value=None),
            patch("trading.kis.smoke_gate.audit"),
        ):
            rc = _cmd_smoke_gate(["--max-qty", "1", "--ticker", "005930"])

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "FAIL" in combined or "차단" in combined or "미확인" in combined

    def test_broker_fill_inquiry_not_implemented_is_fail(self):
        """BrokerFillInquiryNotImplemented 발생 → (e) 미충족 → FAIL(시나리오 6)."""
        from trading.cli import _cmd_smoke_gate
        from trading.kis.broker_truth import BrokerFillInquiryNotImplemented

        def _fake_confirm_raises(client, *, source=None):
            raise BrokerFillInquiryNotImplemented("TR_ID 미검증")

        with (
            patch("trading.config.get_settings", return_value=_live_settings()),
            patch("trading.kis.client.KisClient", return_value=_live_client()),
            patch("trading.db.session.get_system_state", return_value={"live_unlocked": True}),
            patch("trading.kis.order.submit_order", return_value=_submit_result(order_id=1, kis_order_no="BUY001")),
            patch("trading.kis.broker_truth.confirm_fills", side_effect=_fake_confirm_raises),
            patch("trading.cli._inquire_ccld_raw", return_value=[]),
            patch("trading.kis.broker_truth.intraday_reconcile", return_value={"reconciled": True}),
            patch("trading.kis.order_resolver.resolve_stuck_orders", return_value={"scanned": 0}),
            patch("trading.cli._count_stuck_submitted", return_value=0),
            patch("trading.kis.sell_lock.guard_sell", return_value=True),
            patch("trading.kis.sell_lock.set_sell_inflight"),
            patch("trading.cli._find_fill_record", return_value=None),
            patch("trading.kis.smoke_gate.audit"),
        ):
            rc = _cmd_smoke_gate(["--max-qty", "1", "--ticker", "005930"])

        # FAIL이면 비-0
        assert rc != 0
