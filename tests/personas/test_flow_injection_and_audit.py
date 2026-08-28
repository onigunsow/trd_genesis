"""2026-08-28 로그 감사에서 드러난 4건의 회귀 고정.

각 테스트는 "수정 전이라면 반드시 실패하는" 지점만 잡는다.

  ① resolver 오탐: 계좌 리셋 경계가 orders 집계에 실제로 적용되는가
  ② 유령 지시: 결정 프롬프트가 요구하는 수급 데이터가 실제로 주입되는가
  ③ 라벨 오염: 장전 세션 차단이 EXEC_FAILED 가 아닌 별도 이벤트로 기록되는가
  ④ 사일런트 드롭: 리스크 비승인이 감사 로그에 남는가
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import patch

from trading.kis.ghost_convergence import _orders_net_by_ticker
from trading.kis.order import MarketSessionClosedError
from trading.personas.decision import _candidate_flows, _candidate_tickers
from trading.personas.orchestrator import _audit_risk_blocked, _execute_signal


class _SpyCursor:
    """execute 로 들어온 SQL/params 를 그대로 보관한다."""

    def __init__(self) -> None:
        self.sql: str = ""
        self.params: Any = None

    def execute(self, sql: str, params: Any = None) -> None:
        self.sql, self.params = sql, params

    def fetchall(self) -> list[dict[str, Any]]:
        return []


# ---------------------------------------------------------------------------
# ① 계좌 리셋 경계
# ---------------------------------------------------------------------------


def test_orders_net_applies_reset_boundary() -> None:
    """since 를 주면 ts 필터와 파라미터가 실제 쿼리에 실린다.

    경계가 안 실리면 리셋 이전 이력이 영구 드리프트로 남아 매일 오탐이 뜬다.
    """
    cur = _SpyCursor()
    _orders_net_by_ticker(cur, since=date(2026, 8, 8))

    assert "ts >= %s" in cur.sql
    assert cur.params == (date(2026, 8, 8), date(2026, 8, 8))


def test_orders_net_without_boundary_is_unfiltered() -> None:
    """since 가 없으면 NULL 이 실려 필터가 무력화된다 — 기존 동작 그대로.

    converge_ghost_buys(교정 매도 INSERT) 쓰기 경로가 인자를 안 넘기므로
    이 경로가 깨지면 전 기간 집계가 아니라 부분 집계로 교정이 일어난다.
    """
    cur = _SpyCursor()
    _orders_net_by_ticker(cur)

    assert cur.params == (None, None)


# ---------------------------------------------------------------------------
# ② 수급 주입
# ---------------------------------------------------------------------------


def test_candidate_tickers_merges_candidates_and_holdings() -> None:
    inp = {
        "micro_candidates": {
            "buy": [{"ticker": "032830"}, {"ticker": "018260"}],
            "sell": [{"ticker": "032830"}],  # 중복은 한 번만
        },
        "assets": {"holdings": [{"ticker": "055550"}, {"ticker": "018260"}]},
    }
    assert _candidate_tickers(inp) == ["032830", "018260", "055550"]


def test_candidate_flows_injects_real_numbers_in_eok() -> None:
    """프롬프트가 '반드시 확인하라'는 수급이 숫자로 실제 주입된다."""
    rows = {
        "032830": {
            "foreign_5d": -97_800_000_000,
            "institution_5d": 54_500_000_000,
            "individual_5d": 43_300_000_000,
        }
    }
    with patch("trading.personas.context._flows_5d", side_effect=rows.get):
        out = _candidate_flows({"micro_candidates": {"buy": [{"ticker": "032830"}]}})

    assert out == [{
        "ticker": "032830",
        "foreign_5d_eok": -978.0,
        "institution_5d_eok": 545.0,
        "individual_5d_eok": 433.0,
        "combined_5d_eok": -433.0,
    }]


def test_candidate_flows_survives_lookup_failure() -> None:
    """수급 조회가 터져도 사이클은 계속된다 — 블록만 빠진다."""
    with patch("trading.personas.context._flows_5d", side_effect=RuntimeError("db")):
        assert _candidate_flows(
            {"micro_candidates": {"buy": [{"ticker": "032830"}]}}
        ) == []


def test_decision_prompt_carries_flow_table() -> None:
    """렌더된 프롬프트에 수급 수치가 실제로 박힌다(지시만 있고 데이터 없던 결함)."""
    from trading.personas.base import render_prompt

    prompt = render_prompt("decision.jinja", **{
        "today": "2026-08-29",
        "cycle_kind": "intraday",
        "event_trigger": None,
        "car_context": None,
        "dynamic_thresholds_enabled": False,
        "assets": {},
        "candidate_flows": [{
            "ticker": "032830", "foreign_5d_eok": -978.0,
            "institution_5d_eok": 545.0, "individual_5d_eok": 433.0,
            "combined_5d_eok": -433.0,
        }],
    })

    assert "032830" in prompt
    assert "-978.0" in prompt
    assert "-433.0" in prompt


# ---------------------------------------------------------------------------
# ③ 장전 세션 차단 라벨
# ---------------------------------------------------------------------------


def test_outside_session_is_deferred_not_failed() -> None:
    """정규장 밖 차단은 실패가 아니다 — EXEC_FAILED 로 세면 지표가 오염된다."""
    events: list[str] = []

    def _audit(event: str, **_: Any) -> None:
        events.append(event)

    sig = {"side": "buy", "ticker": "018260", "qty": 1}
    with (
        patch("trading.personas.orchestrator.audit", side_effect=_audit),
        patch(
            "trading.personas.orchestrator.kis_buy",
            side_effect=MarketSessionClosedError("정규장 밖"),
        ),
    ):
        assert _execute_signal(object(), sig, decision_id=1) is None

    assert "EXEC_DEFERRED_OUTSIDE_SESSION" in events
    assert "EXEC_FAILED" not in events


# ---------------------------------------------------------------------------
# ④ 리스크 비승인 감사 기록
# ---------------------------------------------------------------------------


def test_risk_block_is_audited() -> None:
    captured: list[tuple[str, dict[str, Any]]] = []

    def _audit(event: str, *, actor: str = "", details: Any = None) -> None:
        captured.append((event, details or {}))

    with patch("trading.personas.orchestrator.audit", side_effect=_audit):
        _audit_risk_blocked(
            decision_id=3272, ticker="032830", verdict="HOLD",
            cycle_kind="intraday", rationale="수급 근거가 실측과 불일치",
        )

    assert len(captured) == 1
    event, details = captured[0]
    assert event == "ORDER_BLOCKED_RISK"
    assert details["decision_id"] == 3272
    assert details["ticker"] == "032830"
    assert details["verdict"] == "HOLD"
    assert "실측과 불일치" in details["rationale"]


def test_risk_block_audit_failure_does_not_abort_cycle() -> None:
    """감사 한 줄 실패가 남은 시그널 처리를 죽이면 안 된다."""
    with patch("trading.personas.orchestrator.audit", side_effect=RuntimeError("db")):
        _audit_risk_blocked(
            decision_id=1, ticker="005930", verdict="REJECT", cycle_kind="event",
        )  # 예외가 새어 나오면 실패
