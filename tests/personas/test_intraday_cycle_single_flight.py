"""intraday 사이클 단일 진입 (2026-08-27).

워처의 handle_trigger_event 와 스케줄러의 intraday_adaptive 가 둘 다
run_intraday_cycle() 을 그대로 호출한다. 트리거는 발화 종목으로 좁히지도 않으므로
두 경로가 하는 일이 완전히 같다 — 동시에 돌면 순수 중복이다.

실측(8/27 09:00:02): 동일 프롬프트 121,930바이트가 7ms 간격으로 두 번 발사되어
두 사이클이 각각 012330 매도를 제안했다. 하나는 체결되고 하나는
PHANTOM_SELL_BLOCKED 로 막혔다. 14일간 5건, 전부 09:00~09:30.

event_handler 의 _CYCLE_LOCK 은 트리거끼리만 막아 스케줄 경로와의 충돌이 열려 있었다.
가드는 호출자마다가 아니라 공유 함수인 run_intraday_cycle 안에 있어야 한다.
"""

from __future__ import annotations

import threading
from unittest.mock import patch

from trading.personas import orchestrator


def test_second_entry_is_skipped_while_first_is_in_flight():
    started = threading.Event()
    release = threading.Event()
    bodies: list[str] = []

    def slow_body(today=None):
        bodies.append("ran")
        started.set()
        release.wait(timeout=5)
        return orchestrator.CycleResult(cycle_kind="intraday")

    with patch.object(orchestrator, "_run_intraday_cycle_locked", slow_body):
        t = threading.Thread(target=orchestrator.run_intraday_cycle)
        t.start()
        assert started.wait(timeout=5), "첫 사이클이 시작되지 않았다"

        with patch.object(orchestrator, "audit") as mock_audit:
            res = orchestrator.run_intraday_cycle()

        # 두 번째 진입은 본체를 돌지 않고 빈 결과로 즉시 반환한다
        assert res.cycle_kind == "intraday"
        assert res.decision_run_id is None
        assert res.decisions == []
        mock_audit.assert_called_once()
        assert mock_audit.call_args[0][0] == "CYCLE_SKIPPED_IN_FLIGHT"

        release.set()
        t.join(timeout=5)

    assert bodies == ["ran"], "본체는 한 번만 실행돼야 한다"


def test_lock_is_released_after_normal_completion():
    """락이 새면 이후 모든 사이클이 영구히 막힌다 — 가장 위험한 회귀."""
    with patch.object(orchestrator, "_run_intraday_cycle_locked",
                      return_value=orchestrator.CycleResult(cycle_kind="intraday")):
        orchestrator.run_intraday_cycle()
        orchestrator.run_intraday_cycle()
    assert not orchestrator._INTRADAY_CYCLE_LOCK.locked()


def test_lock_is_released_when_body_raises():
    with patch.object(orchestrator, "_run_intraday_cycle_locked",
                      side_effect=RuntimeError("boom")):
        try:
            orchestrator.run_intraday_cycle()
        except RuntimeError:
            pass
    assert not orchestrator._INTRADAY_CYCLE_LOCK.locked()
