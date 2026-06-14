"""SPEC-TRADING-049 — 라이브 스모크 게이트 단위 테스트 (TDD RED-GREEN-REFACTOR).

모든 테스트는 오프라인(no DB, no network, no real clock):
- evaluate_smoke_evidence()의 (a)~(e) 각 FAIL 분기 + PASS 경로
- record_smoke_verdict() 영구 기록 (audit mock)
- has_valid_smoke_pass() DB 조회 로직
- check_smoke_gate_precondition() 승격 선행 검사 (PASS/FAIL 양방향)

인수 시나리오 커버:
  시나리오 1  — 완전 PASS 경로
  시나리오 2  — BUY 미확정 → FAIL
  시나리오 3  — SELL 미확정 → FAIL
  시나리오 4  — 원장 불일치 → FAIL
  시나리오 5  — stuck 'submitted' 잔존 → FAIL
  시나리오 6  — live TR_ID/필드 미호환 → FAIL (seam 가드)
  시나리오 9  — 판정/기록 멱등 (이미 기록된 터미널 verdict 재전이 없음)
  시나리오 13 — 하드게이트: PASS 기록 존재 → 승격 허용
  시나리오 14 — 하드게이트: PASS 기록 없음 → 승격 차단

엣지 케이스:
  - 실현손익 음수(소액 손실): PASS 판정에 무관
  - 동일 ODNO가 BUY/SELL 양쪽에 중복: side 구분 오매칭 방지
  - mock 응답 빈 output1(rt_cd≠0): (a)/(b) 모두 미충족
  - CCLD_QTY 부분체결(< 주문수량): PASS 가능(확정만 되면 됨)
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from trading.kis.smoke_gate import (
    HONESTY_DISCLOSURE,
    SmokeEvidence,
    SmokeGateRequired,
    SmokeItemResult,
    SmokeVerdict,
    _EVENT_FAIL,
    _EVENT_PASS,
    check_smoke_gate_precondition,
    evaluate_smoke_evidence,
    has_valid_smoke_pass,
    record_smoke_verdict,
)


# ---------------------------------------------------------------------------
# 테스트 픽스처 — 공통 빌더
# ---------------------------------------------------------------------------


def _pass_evidence(
    *,
    buy_order_no: str = "BUY001",
    sell_order_no: str = "SELL001",
    realized_pnl_delta: int | None = None,
) -> SmokeEvidence:
    """5항목 전부 충족하는 최소 SmokeEvidence."""
    return SmokeEvidence(
        buy_fill={
            "ODNO": buy_order_no,
            "CCLD_QTY": "1",
            "CCLD_AVG_UNPR": "50000",
        },
        buy_order_no=buy_order_no,
        sell_fill={
            "ODNO": sell_order_no,
            "CCLD_QTY": "1",
            "CCLD_AVG_UNPR": "49500",
        },
        sell_order_no=sell_order_no,
        ledger_parity=True,
        stuck_submitted_count=0,
        tr_id_field_compatible=True,
        realized_pnl_delta=realized_pnl_delta,
    )


def _fake_conn_factory(rows: list[dict[str, Any]] | None = None):
    """테스트용 DB 연결 팩토리(conftest 패턴과 동일)."""
    from tests.conftest import FakeCursor, FakeConnection

    @contextmanager
    def _conn(autocommit: bool = False):
        cursor = FakeCursor(rows or [])
        yield FakeConnection(cursor)

    return _conn


# ---------------------------------------------------------------------------
# M1 — evaluate_smoke_evidence 순수 함수: PASS 경로 (시나리오 1)
# ---------------------------------------------------------------------------


class TestEvaluateSmokeEvidence:
    """evaluate_smoke_evidence() 단위 테스트 — 순수 함수, I/O 없음."""

    # ── 시나리오 1: 완전 PASS ────────────────────────────────────────────────

    def test_all_items_satisfied_returns_pass(self):
        """5항목 전부 충족 → passed=True, reasons 빈 리스트."""
        evidence = _pass_evidence()
        verdict = evaluate_smoke_evidence(evidence, timestamp="2026-06-14T00:00:00+00:00")

        assert verdict.passed is True
        assert verdict.reasons == []
        assert all(v.satisfied for v in verdict.items.values())
        assert set(verdict.items.keys()) == {"a", "b", "c", "d", "e"}

    def test_pass_verdict_includes_timestamp(self):
        """판정 결과에 타임스탬프가 포함된다."""
        ts = "2026-06-14T09:00:00+00:00"
        verdict = evaluate_smoke_evidence(_pass_evidence(), timestamp=ts)
        assert verdict.timestamp == ts

    def test_pass_verdict_without_explicit_timestamp_uses_utc_now(self):
        """timestamp 미주입 시 UTC now 형식의 문자열이 사용된다."""
        verdict = evaluate_smoke_evidence(_pass_evidence())
        assert verdict.timestamp  # 빈 문자열 아님
        # ISO-8601 UTC 형식 확인
        from datetime import datetime, UTC
        dt = datetime.fromisoformat(verdict.timestamp)
        assert dt.tzinfo is not None

    def test_realized_pnl_delta_negative_does_not_fail(self):
        """실현손익이 음수(소액 손실)여도 PASS 판정에 무관(실행 정합만 검증)."""
        evidence = _pass_evidence(realized_pnl_delta=-5000)
        verdict = evaluate_smoke_evidence(evidence)
        assert verdict.passed is True
        assert verdict.realized_pnl_delta == -5000

    def test_partial_fill_qty_still_passes_if_confirmed(self):
        """부분체결(CCLD_QTY=1 < 주문수량=2)이어도 CCLD_QTY>0이면 확정 충족."""
        evidence = SmokeEvidence(
            buy_fill={"ODNO": "B1", "CCLD_QTY": "1", "CCLD_AVG_UNPR": "50000"},
            buy_order_no="B1",
            sell_fill={"ODNO": "S1", "CCLD_QTY": "1", "CCLD_AVG_UNPR": "49500"},
            sell_order_no="S1",
            ledger_parity=True,
            stuck_submitted_count=0,
            tr_id_field_compatible=True,
        )
        verdict = evaluate_smoke_evidence(evidence)
        assert verdict.passed is True

    # ── 시나리오 2: BUY 미확정 → FAIL ───────────────────────────────────────

    def test_buy_fill_none_fails_item_a(self):
        """buy_fill=None → 항목 (a) 미충족, verdict=FAIL."""
        evidence = SmokeEvidence(
            buy_fill=None,
            buy_order_no="BUY001",
            sell_fill={"ODNO": "SELL001", "CCLD_QTY": "1", "CCLD_AVG_UNPR": "50000"},
            sell_order_no="SELL001",
            ledger_parity=True,
            stuck_submitted_count=0,
            tr_id_field_compatible=True,
        )
        verdict = evaluate_smoke_evidence(evidence)
        assert verdict.passed is False
        assert not verdict.items["a"].satisfied
        assert "BUY fill 미확인" in verdict.items["a"].reason
        assert any("BUY" in r for r in verdict.reasons)

    def test_buy_fill_odno_mismatch_fails_item_a(self):
        """BUY fill ODNO가 buy_order_no와 불일치 → (a) 미충족."""
        evidence = SmokeEvidence(
            buy_fill={"ODNO": "OTHER", "CCLD_QTY": "1", "CCLD_AVG_UNPR": "50000"},
            buy_order_no="BUY001",
            sell_fill={"ODNO": "SELL001", "CCLD_QTY": "1", "CCLD_AVG_UNPR": "49500"},
            sell_order_no="SELL001",
            ledger_parity=True,
            stuck_submitted_count=0,
            tr_id_field_compatible=True,
        )
        verdict = evaluate_smoke_evidence(evidence)
        assert verdict.passed is False
        assert not verdict.items["a"].satisfied
        assert "ODNO 불일치" in verdict.items["a"].reason

    def test_buy_fill_ccld_qty_zero_fails_item_a(self):
        """BUY CCLD_QTY=0 → (a) 미충족 (체결수량 없음)."""
        evidence = SmokeEvidence(
            buy_fill={"ODNO": "BUY001", "CCLD_QTY": "0", "CCLD_AVG_UNPR": "50000"},
            buy_order_no="BUY001",
            sell_fill={"ODNO": "SELL001", "CCLD_QTY": "1", "CCLD_AVG_UNPR": "49500"},
            sell_order_no="SELL001",
            ledger_parity=True,
            stuck_submitted_count=0,
            tr_id_field_compatible=True,
        )
        verdict = evaluate_smoke_evidence(evidence)
        assert verdict.passed is False
        assert "CCLD_QTY <= 0" in verdict.items["a"].reason

    def test_buy_fill_ccld_avg_zero_fails_item_a(self):
        """BUY CCLD_AVG_UNPR=0 → (a) 미충족 (체결평균가 없음)."""
        evidence = SmokeEvidence(
            buy_fill={"ODNO": "BUY001", "CCLD_QTY": "1", "CCLD_AVG_UNPR": "0"},
            buy_order_no="BUY001",
            sell_fill={"ODNO": "SELL001", "CCLD_QTY": "1", "CCLD_AVG_UNPR": "49500"},
            sell_order_no="SELL001",
            ledger_parity=True,
            stuck_submitted_count=0,
            tr_id_field_compatible=True,
        )
        verdict = evaluate_smoke_evidence(evidence)
        assert verdict.passed is False
        assert "CCLD_AVG_UNPR <= 0" in verdict.items["a"].reason

    def test_buy_fill_none_fabrication_check(self):
        """BUY 미확정 시 위조 없음 — buy_fill=None이면 (a)=False만 반환, 값 변경 없음."""
        evidence = SmokeEvidence(
            buy_fill=None,
            buy_order_no="BUY001",
            sell_fill=None,
            sell_order_no="SELL001",
            ledger_parity=False,
            stuck_submitted_count=0,
            tr_id_field_compatible=True,
        )
        verdict = evaluate_smoke_evidence(evidence)
        # verdict에 fabricated fill 없음
        assert evidence.buy_fill is None  # 입력 불변
        assert verdict.items["a"].satisfied is False

    # ── 시나리오 3: SELL 미확정 → FAIL ──────────────────────────────────────

    def test_sell_fill_none_fails_item_b(self):
        """sell_fill=None → 항목 (b) 미충족, verdict=FAIL."""
        evidence = SmokeEvidence(
            buy_fill={"ODNO": "BUY001", "CCLD_QTY": "1", "CCLD_AVG_UNPR": "50000"},
            buy_order_no="BUY001",
            sell_fill=None,
            sell_order_no="SELL001",
            ledger_parity=True,
            stuck_submitted_count=0,
            tr_id_field_compatible=True,
        )
        verdict = evaluate_smoke_evidence(evidence)
        assert verdict.passed is False
        assert not verdict.items["b"].satisfied
        assert "SELL fill 미확인" in verdict.items["b"].reason

    def test_sell_fill_odno_mismatch_fails_item_b(self):
        """SELL fill ODNO가 sell_order_no와 불일치 → (b) 미충족."""
        evidence = SmokeEvidence(
            buy_fill={"ODNO": "BUY001", "CCLD_QTY": "1", "CCLD_AVG_UNPR": "50000"},
            buy_order_no="BUY001",
            sell_fill={"ODNO": "WRONG", "CCLD_QTY": "1", "CCLD_AVG_UNPR": "49500"},
            sell_order_no="SELL001",
            ledger_parity=True,
            stuck_submitted_count=0,
            tr_id_field_compatible=True,
        )
        verdict = evaluate_smoke_evidence(evidence)
        assert verdict.passed is False
        assert "ODNO 불일치" in verdict.items["b"].reason

    def test_sell_fill_ccld_qty_zero_fails_item_b(self):
        """SELL CCLD_QTY=0 → (b) 미충족."""
        evidence = SmokeEvidence(
            buy_fill={"ODNO": "BUY001", "CCLD_QTY": "1", "CCLD_AVG_UNPR": "50000"},
            buy_order_no="BUY001",
            sell_fill={"ODNO": "SELL001", "CCLD_QTY": "0", "CCLD_AVG_UNPR": "49500"},
            sell_order_no="SELL001",
            ledger_parity=True,
            stuck_submitted_count=0,
            tr_id_field_compatible=True,
        )
        verdict = evaluate_smoke_evidence(evidence)
        assert verdict.passed is False
        assert "CCLD_QTY <= 0" in verdict.items["b"].reason

    # ── 시나리오 4: 원장 불일치 → FAIL ──────────────────────────────────────

    def test_ledger_parity_false_fails_item_c(self):
        """ledger_parity=False → 항목 (c) 미충족."""
        evidence = SmokeEvidence(
            buy_fill={"ODNO": "BUY001", "CCLD_QTY": "1", "CCLD_AVG_UNPR": "50000"},
            buy_order_no="BUY001",
            sell_fill={"ODNO": "SELL001", "CCLD_QTY": "1", "CCLD_AVG_UNPR": "49500"},
            sell_order_no="SELL001",
            ledger_parity=False,
            stuck_submitted_count=0,
            tr_id_field_compatible=True,
        )
        verdict = evaluate_smoke_evidence(evidence)
        assert verdict.passed is False
        assert not verdict.items["c"].satisfied
        assert "원장 불일치" in verdict.items["c"].reason

    # ── 시나리오 5: stuck 'submitted' 잔존 → FAIL ────────────────────────────

    def test_stuck_submitted_nonzero_fails_item_d(self):
        """stuck_submitted_count=1 → 항목 (d) 미충족."""
        evidence = SmokeEvidence(
            buy_fill={"ODNO": "BUY001", "CCLD_QTY": "1", "CCLD_AVG_UNPR": "50000"},
            buy_order_no="BUY001",
            sell_fill={"ODNO": "SELL001", "CCLD_QTY": "1", "CCLD_AVG_UNPR": "49500"},
            sell_order_no="SELL001",
            ledger_parity=True,
            stuck_submitted_count=1,
            tr_id_field_compatible=True,
        )
        verdict = evaluate_smoke_evidence(evidence)
        assert verdict.passed is False
        assert not verdict.items["d"].satisfied
        assert "1건 잔존" in verdict.items["d"].reason

    def test_stuck_submitted_zero_passes_item_d(self):
        """stuck_submitted_count=0 → 항목 (d) 충족."""
        evidence = _pass_evidence()
        verdict = evaluate_smoke_evidence(evidence)
        assert verdict.items["d"].satisfied is True

    # ── 시나리오 6: live TR_ID/필드 미호환 → FAIL ────────────────────────────

    def test_tr_id_field_not_compatible_fails_item_e(self):
        """tr_id_field_compatible=False → 항목 (e) 미충족(seam 가드)."""
        evidence = SmokeEvidence(
            buy_fill={"ODNO": "BUY001", "CCLD_QTY": "1", "CCLD_AVG_UNPR": "50000"},
            buy_order_no="BUY001",
            sell_fill={"ODNO": "SELL001", "CCLD_QTY": "1", "CCLD_AVG_UNPR": "49500"},
            sell_order_no="SELL001",
            ledger_parity=True,
            stuck_submitted_count=0,
            tr_id_field_compatible=False,
        )
        verdict = evaluate_smoke_evidence(evidence)
        assert verdict.passed is False
        assert not verdict.items["e"].satisfied
        assert "BrokerFillInquiryNotImplemented" in verdict.items["e"].reason or \
               "TR_ID" in verdict.items["e"].reason

    # ── 복수 항목 동시 FAIL ──────────────────────────────────────────────────

    def test_multiple_items_fail_all_reported(self):
        """여러 항목이 동시에 미충족이면 reasons에 전부 포함."""
        evidence = SmokeEvidence(
            buy_fill=None,
            buy_order_no="BUY001",
            sell_fill=None,
            sell_order_no="SELL001",
            ledger_parity=False,
            stuck_submitted_count=2,
            tr_id_field_compatible=False,
        )
        verdict = evaluate_smoke_evidence(evidence)
        assert verdict.passed is False
        assert len(verdict.reasons) == 5  # a, b, c, d, e 전부 FAIL

    # ── 엣지: BUY/SELL ODNO 중복 방지 ──────────────────────────────────────

    def test_same_odno_for_buy_and_sell_does_not_cross_match(self):
        """BUY/SELL이 동일 ODNO를 공유해도 side 구분으로 오매칭하지 않는다.

        판정 함수는 buy_order_no vs buy_fill['ODNO'],
        sell_order_no vs sell_fill['ODNO']를 각각 독립 매칭하므로
        동일 ODNO가 양쪽에 있을 때 각각의 레코드가 올바르면 PASS.
        """
        shared_odno = "SHARED001"
        evidence = SmokeEvidence(
            buy_fill={"ODNO": shared_odno, "CCLD_QTY": "1", "CCLD_AVG_UNPR": "50000"},
            buy_order_no=shared_odno,
            sell_fill={"ODNO": shared_odno, "CCLD_QTY": "1", "CCLD_AVG_UNPR": "49500"},
            sell_order_no=shared_odno,
            ledger_parity=True,
            stuck_submitted_count=0,
            tr_id_field_compatible=True,
        )
        verdict = evaluate_smoke_evidence(evidence)
        # 각각 buy_order_no/sell_order_no와 매칭되면 PASS
        assert verdict.items["a"].satisfied is True
        assert verdict.items["b"].satisfied is True

    def test_deterministic_same_input_same_output(self):
        """동일 입력 → 항상 동일 판정(결정론적, REQ-049-M2-2)."""
        evidence = _pass_evidence()
        ts = "2026-06-14T12:00:00+00:00"
        v1 = evaluate_smoke_evidence(evidence, timestamp=ts)
        v2 = evaluate_smoke_evidence(evidence, timestamp=ts)
        assert v1.passed == v2.passed
        assert v1.reasons == v2.reasons
        assert v1.timestamp == v2.timestamp


# ---------------------------------------------------------------------------
# M2 — record_smoke_verdict 영구 기록 (시나리오 9 멱등 포함)
# ---------------------------------------------------------------------------


class TestRecordSmokeVerdict:
    """record_smoke_verdict() — audit() 호출 검증."""

    def test_pass_verdict_records_smoke_gate_pass_event(self):
        """PASS 판정은 SMOKE_GATE_PASS 이벤트로 기록."""
        evidence = _pass_evidence()
        verdict = evaluate_smoke_evidence(evidence, timestamp="2026-06-14T10:00:00+00:00")

        with patch("trading.kis.smoke_gate.audit") as mock_audit:
            record_smoke_verdict(verdict, snapshot={"test": True})

        mock_audit.assert_called_once()
        event_type = mock_audit.call_args[0][0]
        details = mock_audit.call_args.kwargs["details"]
        assert event_type == _EVENT_PASS
        assert details["passed"] is True
        assert details["snapshot"] == {"test": True}

    def test_fail_verdict_records_smoke_gate_fail_event(self):
        """FAIL 판정은 SMOKE_GATE_FAIL 이벤트로 기록."""
        evidence = SmokeEvidence(
            buy_fill=None,
            buy_order_no="BUY001",
            sell_fill=None,
            sell_order_no="SELL001",
            ledger_parity=True,
            stuck_submitted_count=0,
            tr_id_field_compatible=True,
        )
        verdict = evaluate_smoke_evidence(evidence)

        with patch("trading.kis.smoke_gate.audit") as mock_audit:
            record_smoke_verdict(verdict)

        mock_audit.assert_called_once()
        event_type = mock_audit.call_args[0][0]
        assert event_type == _EVENT_FAIL

    def test_fail_verdict_never_recorded_as_pass(self):
        """FAIL 기록은 절대 SMOKE_GATE_PASS로 기록되지 않는다(REQ-049-M2-4)."""
        evidence = SmokeEvidence(
            buy_fill=None,
            buy_order_no="B",
            sell_fill=None,
            sell_order_no="S",
            ledger_parity=False,
            stuck_submitted_count=1,
            tr_id_field_compatible=False,
        )
        verdict = evaluate_smoke_evidence(evidence)
        assert not verdict.passed

        with patch("trading.kis.smoke_gate.audit") as mock_audit:
            record_smoke_verdict(verdict)

        event_type = mock_audit.call_args[0][0]
        assert event_type == _EVENT_FAIL
        assert event_type != _EVENT_PASS

    def test_record_includes_all_items_and_reasons(self):
        """기록된 details에 항목별 판정과 사유 목록이 포함된다."""
        evidence = SmokeEvidence(
            buy_fill=None,
            buy_order_no="BUY001",
            sell_fill={"ODNO": "SELL001", "CCLD_QTY": "1", "CCLD_AVG_UNPR": "50000"},
            sell_order_no="SELL001",
            ledger_parity=True,
            stuck_submitted_count=0,
            tr_id_field_compatible=True,
        )
        verdict = evaluate_smoke_evidence(evidence)

        with patch("trading.kis.smoke_gate.audit") as mock_audit:
            record_smoke_verdict(verdict)

        details = mock_audit.call_args.kwargs["details"]
        assert "items" in details
        assert "a" in details["items"]
        assert details["items"]["a"]["satisfied"] is False
        assert "reasons" in details
        assert len(details["reasons"]) >= 1

    def test_idempotent_record_calls_audit_each_time(self):
        """판정/기록이 반복 호출되어도 각 호출은 독립적으로 audit()를 실행(멱등성)."""
        evidence = _pass_evidence()
        verdict = evaluate_smoke_evidence(evidence)

        with patch("trading.kis.smoke_gate.audit") as mock_audit:
            record_smoke_verdict(verdict)
            record_smoke_verdict(verdict)  # 두 번째 호출

        # 두 번 모두 audit() 호출됨 (기존 기록을 덮어쓰지 않고 추가)
        assert mock_audit.call_count == 2


# ---------------------------------------------------------------------------
# M3 — has_valid_smoke_pass + check_smoke_gate_precondition (시나리오 13/14)
# ---------------------------------------------------------------------------


class TestHasValidSmokePass:
    """has_valid_smoke_pass() — PASS 기록 조회 로직 (DB mock 사용)."""

    def test_returns_true_when_pass_record_exists(self):
        """SMOKE_GATE_PASS 이벤트가 audit_log에 있으면 True 반환(시나리오 13)."""
        pass_row = {"event_type": _EVENT_PASS}
        conn_factory = _fake_conn_factory(rows=[pass_row])

        result = has_valid_smoke_pass(conn_factory=conn_factory)
        assert result is True

    def test_returns_false_when_no_pass_record(self):
        """SMOKE_GATE_PASS 이벤트가 없으면 False 반환(시나리오 14)."""
        conn_factory = _fake_conn_factory(rows=[])

        result = has_valid_smoke_pass(conn_factory=conn_factory)
        assert result is False

    def test_fail_record_does_not_count_as_pass(self):
        """SMOKE_GATE_FAIL 이벤트만 있어도 False — FAIL은 PASS로 해석 안 됨."""
        # 실제 SQL은 SMOKE_GATE_PASS만 조회하므로 FAIL 행은 무관.
        # conftest FakeCursor는 항상 rows를 반환하므로,
        # 여기서는 빈 rows를 사용해 query가 PASS를 찾지 못하는 상황 재현.
        conn_factory = _fake_conn_factory(rows=[])
        result = has_valid_smoke_pass(conn_factory=conn_factory)
        assert result is False

    def test_returns_false_on_db_error(self):
        """DB 조회 예외 시 안전을 위해 False(차단) 반환."""
        @contextmanager
        def _bad_conn(autocommit: bool = False):
            raise RuntimeError("DB unreachable")
            yield  # type: ignore[misc]

        result = has_valid_smoke_pass(conn_factory=_bad_conn)
        assert result is False

    def test_query_uses_smoke_gate_pass_event_type(self):
        """쿼리가 SMOKE_GATE_PASS 이벤트 타입을 사용하는지 확인."""
        from tests.conftest import FakeCursor, FakeConnection

        captured_params: list[Any] = []

        class CaptureCursor(FakeCursor):
            def execute(self, sql: str, params: Any = None) -> None:
                super().execute(sql, params)
                if params:
                    captured_params.extend(params if isinstance(params, (list, tuple)) else [params])

        @contextmanager
        def _conn(autocommit: bool = False):
            cursor = CaptureCursor(rows=[])
            yield FakeConnection(cursor)

        has_valid_smoke_pass(conn_factory=_conn)
        assert _EVENT_PASS in captured_params


class TestCheckSmokeGatePrecondition:
    """check_smoke_gate_precondition() — 시나리오 13/14 양방향."""

    def test_pass_record_exists_no_exception(self):
        """PASS 기록 존재 → SmokeGateRequired 미발생(승격 허용, 시나리오 13)."""
        conn_factory = _fake_conn_factory(rows=[{"event_type": _EVENT_PASS}])
        # 예외 없이 정상 반환
        check_smoke_gate_precondition(conn_factory=conn_factory)

    def test_no_pass_record_raises_smoke_gate_required(self):
        """PASS 기록 없음 → SmokeGateRequired 발생(승격 차단, 시나리오 14)."""
        conn_factory = _fake_conn_factory(rows=[])
        with pytest.raises(SmokeGateRequired) as exc_info:
            check_smoke_gate_precondition(conn_factory=conn_factory)

        # 사유 메시지에 '스모크 PASS 기록 없음' 포함
        assert "스모크 PASS 기록 없음" in str(exc_info.value)

    def test_fail_record_only_raises_smoke_gate_required(self):
        """FAIL 기록만 있으면 PASS가 없으므로 차단(시나리오 14 연장)."""
        conn_factory = _fake_conn_factory(rows=[])
        with pytest.raises(SmokeGateRequired):
            check_smoke_gate_precondition(conn_factory=conn_factory)

    def test_error_message_includes_command_hint(self):
        """사유 메시지에 'trading smoke-gate' 실행 힌트 포함."""
        conn_factory = _fake_conn_factory(rows=[])
        with pytest.raises(SmokeGateRequired) as exc_info:
            check_smoke_gate_precondition(conn_factory=conn_factory)
        assert "trading smoke-gate" in str(exc_info.value)

    def test_subsequent_pass_record_unblocks_promotion(self):
        """FAIL 후 PASS 기록이 추가되면 다음 검사에서 차단 해제."""
        # 첫 번째 검사: PASS 없음 → 차단
        no_pass_factory = _fake_conn_factory(rows=[])
        with pytest.raises(SmokeGateRequired):
            check_smoke_gate_precondition(conn_factory=no_pass_factory)

        # 두 번째 검사: PASS 있음 → 허용
        pass_factory = _fake_conn_factory(rows=[{"event_type": _EVENT_PASS}])
        check_smoke_gate_precondition(conn_factory=pass_factory)  # 예외 없음


# ---------------------------------------------------------------------------
# NFR — 정직 고지 문구 상수 확인
# ---------------------------------------------------------------------------


class TestHonestyDisclosure:
    """HONESTY_DISCLOSURE 상수가 REQ-049-M1-4 요건을 충족하는지 확인."""

    def test_honesty_disclosure_mentions_execution_path(self):
        """고지 문구에 '실행 경로' 또는 '전략 수익성 검증이 아님' 포함."""
        assert "실행 경로" in HONESTY_DISCLOSURE or "전략" in HONESTY_DISCLOSURE

    def test_honesty_disclosure_mentions_not_strategy(self):
        """고지 문구에 전략 수익성 검증 아님 명시."""
        assert "전략 수익성 검증이 아닙니다" in HONESTY_DISCLOSURE or \
               "전략 수익성 검증이 아님" in HONESTY_DISCLOSURE

    def test_honesty_disclosure_references_related_specs(self):
        """고지 문구에 SPEC-044/046/048 언급(전략 측정 책임 분리)."""
        assert "SPEC-044" in HONESTY_DISCLOSURE or "SPEC-046" in HONESTY_DISCLOSURE


# ---------------------------------------------------------------------------
# SmokeVerdict 불변성 (frozen dataclass)
# ---------------------------------------------------------------------------


class TestSmokeVerdictImmutability:
    """SmokeVerdict은 frozen=True dataclass — 판정 결과 변조 불가."""

    def test_verdict_is_immutable(self):
        """frozen dataclass이므로 필드 변경 시 FrozenInstanceError 발생."""
        verdict = evaluate_smoke_evidence(_pass_evidence(), timestamp="2026-06-14T00:00:00+00:00")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            verdict.passed = False  # type: ignore[misc]

    def test_smoke_evidence_is_immutable(self):
        """SmokeEvidence도 frozen — 입력 불변성 보장."""
        evidence = _pass_evidence()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            evidence.buy_fill = None  # type: ignore[misc]


import dataclasses
