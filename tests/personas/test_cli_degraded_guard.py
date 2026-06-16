"""SPEC-TRADING-052: CLI degraded 감지·조기경고·비용0 강제 TDD 테스트.

재현 우선(reproduction-first): 실제 호스트 claude·wall-clock·텔레그램에 의존 안 함.
- CLI 실패: CLICallError / exit=0 빈출력을 mock 주입
- 쿨다운: now_provider Callable[[], datetime] 주입 (SPEC-031 FakeClock 패턴)
- DB: system_state dict 패치
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 공통 픽스처 / 헬퍼
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(UTC)


def _make_now(dt: datetime) -> Callable[[], datetime]:
    """고정 시각을 반환하는 clock provider."""
    return lambda: dt


BASE = "trading.personas.base"
CB = "trading.risk.circuit_breaker"
TG_ALERT = "trading.alerts.telegram.system_briefing"


def _state(**overrides: Any) -> dict[str, Any]:
    """최소 system_state dict 생성."""
    defaults: dict[str, Any] = {
        "cli_personas_enabled": True,
        "cli_only_mode": False,
        "halt_state": False,
        # SPEC-052 신규 컬럼
        "cli_degraded": False,
        "cli_degraded_since": None,
        "cli_consecutive_failures": 0,
        "cli_degraded_notified_at": None,
        "strict_cost_zero_mode": False,
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# AC-1 (REQ-052-A1): 연속 빈출력 실패 → degraded 영속 마킹
# ---------------------------------------------------------------------------

class TestAC1DegradedPersist:
    """연속 CLI 실패 N회 → system_state.cli_degraded=True latch."""

    def test_consecutive_failures_latch_degraded(self) -> None:
        """CLICallError 3회 연속 → cli_degraded=True가 system_state에 기록된다."""
        from trading.personas.base import _CLI_AUTO_DISABLE_THRESHOLD

        state = _state()
        written_state: dict[str, Any] = {}

        def fake_update(**kwargs: Any) -> None:
            written_state.update(kwargs)
            state.update(kwargs)

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.update_system_state", side_effect=fake_update),
            patch(f"{BASE}._ensure_cli_imports"),
            patch(f"{BASE}.CLICallError", Exception),
            patch(f"{BASE}.CLITimeoutError", Exception),
            patch(f"{BASE}.call_persona_cli", side_effect=Exception("empty output")),
            patch(f"{BASE}.assert_fallback_model"),
            patch(f"{BASE}.call_persona"),
            patch("trading.alerts.telegram.system_briefing"),
            patch(f"{BASE}.audit"),
        ):
            from trading.personas import base
            # 실패를 임계(3)회 발생시킨다
            for _ in range(_CLI_AUTO_DISABLE_THRESHOLD):
                try:
                    base.call_persona_via_cli(
                        persona_name="test",
                        model="test-model",
                        cycle_kind="test",
                        system_prompt="sys",
                        user_message="user",
                    )
                except Exception:
                    pass

        # cli_degraded가 True로 latched되어야 함
        assert written_state.get("cli_degraded") is True, (
            "cli_degraded가 True로 latch되지 않음 — RED (REQ-052-A1)"
        )
        assert "cli_degraded_since" in written_state, (
            "cli_degraded_since 타임스탬프가 기록되지 않음 — RED (REQ-052-A1)"
        )

    def test_consecutive_failures_counter_persisted(self) -> None:
        """연속 실패 횟수가 영속 카운터(cli_consecutive_failures)에 누적된다."""
        state = _state()
        written_state: dict[str, Any] = {}

        def fake_update(**kwargs: Any) -> None:
            written_state.update(kwargs)
            state.update(kwargs)

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.update_system_state", side_effect=fake_update),
            patch(f"{BASE}._ensure_cli_imports"),
            patch(f"{BASE}.CLICallError", Exception),
            patch(f"{BASE}.CLITimeoutError", Exception),
            patch(f"{BASE}.call_persona_cli", side_effect=Exception("fail")),
            patch(f"{BASE}.assert_fallback_model"),
            patch(f"{BASE}.call_persona"),
            patch("trading.alerts.telegram.system_briefing"),
            patch(f"{BASE}.audit"),
        ):
            from trading.personas import base
            try:
                base.call_persona_via_cli(
                    persona_name="test",
                    model="m",
                    cycle_kind="c",
                    system_prompt="s",
                    user_message="u",
                )
            except Exception:
                pass

        # 1회 실패 → 영속 카운터가 1 이상이어야 함
        assert written_state.get("cli_consecutive_failures", 0) >= 1, (
            "cli_consecutive_failures가 증가하지 않음 — RED (REQ-052-A1)"
        )


# ---------------------------------------------------------------------------
# AC-1b (REQ-052-A1b): 워처 stale → degraded 마킹
# ---------------------------------------------------------------------------

class TestAC1bWatcherStaleDegraded:
    """is_cli_mode_active()가 stale 판정 → degraded로 마킹된다."""

    def test_stale_watcher_marks_degraded(self) -> None:
        """워처 heartbeat stale 시 직접경로가 degraded로 마킹된다."""
        state = _state(cli_personas_enabled=True)
        written_state: dict[str, Any] = {}

        def fake_update(**kwargs: Any) -> None:
            written_state.update(kwargs)
            state.update(kwargs)

        # _persist_cli_degraded가 사용하는 get_system_state/update_system_state는
        # base 모듈 레벨에서 import된 것을 사용
        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.update_system_state", side_effect=fake_update),
            patch("trading.db.session.get_system_state", return_value=state),
            patch("trading.db.session.update_system_state", side_effect=fake_update),
            patch("trading.personas.cli_bridge.is_watcher_alive", return_value=False),
            patch("trading.alerts.telegram.system_briefing"),
        ):
            from trading.personas.base import is_cli_mode_active
            result = is_cli_mode_active()

        # stale 판정 후 False 반환
        assert result is False
        # 워처 stale → degraded 마킹되어야 함
        assert written_state.get("cli_degraded") is True, (
            "워처 stale 시 cli_degraded=True가 마킹되지 않음 — RED (REQ-052-A1b)"
        )


# ---------------------------------------------------------------------------
# AC-1c (REQ-052-A5 [HARD]): degraded latch ↔ 자동전환 카운터 독립 (flap 방지)
# ---------------------------------------------------------------------------

class TestAC1cNoflapLatch:
    """[HARD] in-process 카운터가 L564에서 0으로 리셋되어도 cli_degraded=True 유지.

    이것이 REQ-052-A5의 핵심: degraded는 A2 조건(성공/하트비트)에서만 해제된다.
    """

    def test_degraded_latch_survives_inprocess_counter_reset(self) -> None:
        """자동전환 발동(카운터 리셋) 이후에도 cli_degraded=True를 유지한다."""
        from trading.personas import base
        from trading.personas.base import _CLI_AUTO_DISABLE_THRESHOLD

        state = _state()
        written_calls: list[dict[str, Any]] = []

        def fake_update(**kwargs: Any) -> None:
            written_calls.append(dict(kwargs))
            state.update(kwargs)

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.update_system_state", side_effect=fake_update),
            patch(f"{BASE}._ensure_cli_imports"),
            patch(f"{BASE}.CLICallError", Exception),
            patch(f"{BASE}.CLITimeoutError", Exception),
            patch(f"{BASE}.call_persona_cli", side_effect=Exception("empty")),
            patch(f"{BASE}.assert_fallback_model"),
            patch(f"{BASE}.call_persona"),
            patch("trading.alerts.telegram.system_briefing"),
            patch(f"{BASE}.audit"),
        ):
            # 임계만큼 실패 → 자동전환 발동 + in-process 카운터 L564에서 0으로 리셋
            for _ in range(_CLI_AUTO_DISABLE_THRESHOLD):
                try:
                    base.call_persona_via_cli(
                        persona_name="test",
                        model="m",
                        cycle_kind="c",
                        system_prompt="s",
                        user_message="u",
                    )
                except Exception:
                    pass

        # 자동전환 후에도 degraded latch는 True여야 함 (A2 조건 미발생)
        # written_calls 중 cli_degraded=False로 되돌린 call이 없어야 함
        false_resets = [c for c in written_calls if c.get("cli_degraded") is False]
        assert not false_resets, (
            f"cli_degraded가 False로 flap됨 — in-process 카운터 리셋과 독립되지 않음 "
            f"(REQ-052-A5 위반): {false_resets}"
        )
        # 최종 state에서 cli_degraded=True
        assert state.get("cli_degraded") is True, (
            "자동전환 후 cli_degraded가 True가 아님 — RED (REQ-052-A5)"
        )

    def test_degraded_cleared_only_on_success(self) -> None:
        """cli_degraded=True 상태에서 성공 호출 시에만 False로 해제된다."""
        from trading.personas import base

        state = _state(
            cli_degraded=True,
            cli_consecutive_failures=3,
            cli_degraded_since=_utcnow(),
        )
        written_state: dict[str, Any] = {}

        def fake_update(**kwargs: Any) -> None:
            written_state.update(kwargs)
            state.update(kwargs)

        # 성공하는 CLI 응답 mock
        fake_result = {"response_text": "good output", "persona_run_id": 1}

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.update_system_state", side_effect=fake_update),
            patch(f"{BASE}._ensure_cli_imports"),
            patch(f"{BASE}.CLICallError", Exception),
            patch(f"{BASE}.CLITimeoutError", Exception),
            patch(f"{BASE}.call_persona_cli", return_value=fake_result),
            patch(f"{BASE}.assert_fallback_model"),
            patch(f"{BASE}.parse_cli_response", return_value={}),
            patch(f"{BASE}.build_cli_prompt", return_value="prompt"),
            patch("trading.alerts.telegram.system_briefing"),
            patch(f"{BASE}.audit"),
            patch(f"{BASE}.connection") as mock_conn,
        ):
            mock_cur = MagicMock()
            mock_cur.__enter__ = lambda s: s
            mock_cur.__exit__ = MagicMock(return_value=False)
            mock_cur.fetchone.return_value = (42,)
            mock_conn_ctx = MagicMock()
            mock_conn_ctx.__enter__ = lambda s: s
            mock_conn_ctx.__exit__ = MagicMock(return_value=False)
            mock_conn_ctx.cursor.return_value = mock_cur
            mock_conn.return_value = mock_conn_ctx

            try:
                base.call_persona_via_cli(
                    persona_name="test",
                    model="m",
                    cycle_kind="c",
                    system_prompt="s",
                    user_message="u",
                )
            except Exception:
                pass

        # 성공 후 cli_degraded=False 해제되어야 함
        assert written_state.get("cli_degraded") is False, (
            "CLI 성공 후 cli_degraded가 False로 해제되지 않음 — RED (REQ-052-A2/A5)"
        )


# ---------------------------------------------------------------------------
# AC-2 (REQ-052-A2): CLI 성공 복귀 → degraded 해제·카운터 리셋
# ---------------------------------------------------------------------------

class TestAC2DegradedClear:
    """CLI 성공 시 degraded=False, 카운터 0, throttle clock NULL 리셋."""

    def test_success_clears_degraded_and_resets_throttle(self) -> None:
        """성공 호출 후 cli_degraded=False + cli_consecutive_failures=0
        + cli_degraded_notified_at=None이 기록된다."""
        from trading.personas import base

        state = _state(
            cli_degraded=True,
            cli_consecutive_failures=3,
            cli_degraded_since=_utcnow(),
            cli_degraded_notified_at=_utcnow(),
        )
        written_state: dict[str, Any] = {}

        def fake_update(**kwargs: Any) -> None:
            written_state.update(kwargs)
            state.update(kwargs)

        fake_result = {"response_text": "success output", "persona_run_id": 1}

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.update_system_state", side_effect=fake_update),
            patch(f"{BASE}._ensure_cli_imports"),
            patch(f"{BASE}.CLICallError", Exception),
            patch(f"{BASE}.CLITimeoutError", Exception),
            patch(f"{BASE}.call_persona_cli", return_value=fake_result),
            patch(f"{BASE}.parse_cli_response", return_value={}),
            patch(f"{BASE}.build_cli_prompt", return_value="prompt"),
            patch("trading.alerts.telegram.system_briefing"),
            patch(f"{BASE}.audit"),
            patch(f"{BASE}.connection") as mock_conn,
        ):
            mock_cur = MagicMock()
            mock_cur.__enter__ = lambda s: s
            mock_cur.__exit__ = MagicMock(return_value=False)
            mock_cur.fetchone.return_value = (42,)
            mock_conn_ctx = MagicMock()
            mock_conn_ctx.__enter__ = lambda s: s
            mock_conn_ctx.__exit__ = MagicMock(return_value=False)
            mock_conn_ctx.cursor.return_value = mock_cur
            mock_conn.return_value = mock_conn_ctx

            try:
                base.call_persona_via_cli(
                    persona_name="test",
                    model="m",
                    cycle_kind="c",
                    system_prompt="s",
                    user_message="u",
                )
            except Exception:
                pass

        assert written_state.get("cli_degraded") is False, (
            "성공 후 cli_degraded=False 해제 안됨 — RED (REQ-052-A2)"
        )
        assert written_state.get("cli_consecutive_failures") == 0, (
            "성공 후 cli_consecutive_failures=0 리셋 안됨 — RED (REQ-052-A2)"
        )
        assert "cli_degraded_notified_at" in written_state, (
            "성공 후 cli_degraded_notified_at NULL 리셋 안됨 — RED (REQ-052-A2)"
        )
        assert written_state["cli_degraded_notified_at"] is None, (
            "throttle clock이 None으로 리셋되지 않음 — RED (REQ-052-A2)"
        )


# ---------------------------------------------------------------------------
# AC-3 (REQ-052-A4): degraded 영속 DB 실패 graceful (fail-open 보존)
# ---------------------------------------------------------------------------

class TestAC3GracefulDBFailure:
    """update_system_state가 raise → 사이클 wedge 없이 graceful."""

    def test_db_failure_does_not_wedge_cycle(self) -> None:
        """degraded 영속 기록 DB 실패 시 사이클이 중단되지 않고 graceful 처리된다."""
        from trading.personas import base

        def failing_update(**_kwargs: Any) -> None:
            raise RuntimeError("DB connection lost")

        with (
            patch(f"{BASE}.get_system_state", return_value=_state()),
            patch(f"{BASE}.update_system_state", side_effect=failing_update),
            patch(f"{BASE}._ensure_cli_imports"),
            patch(f"{BASE}.CLICallError", Exception),
            patch(f"{BASE}.CLITimeoutError", Exception),
            patch(f"{BASE}.call_persona_cli", side_effect=Exception("fail")),
            patch(f"{BASE}.assert_fallback_model"),
            patch(f"{BASE}.call_persona"),  # 폴백은 성공
            patch("trading.alerts.telegram.system_briefing"),
            patch(f"{BASE}.audit"),
        ):
            # DB 실패가 예외를 사이클로 전파하지 않아야 함
            # call_persona_via_cli는 폴백 결과를 반환하거나 폴백도 실패 시에만 raise
            # 여기서는 DB 실패로 degraded 마킹이 실패해도 폴백은 진행되어야 함
            try:
                base.call_persona_via_cli(
                    persona_name="test",
                    model="m",
                    cycle_kind="c",
                    system_prompt="s",
                    user_message="u",
                )
            except RuntimeError as exc:
                # "DB connection lost"가 직접 전파되면 안 됨
                msg = str(exc)
                assert "DB connection lost" not in msg, (
                    "DB 실패가 사이클을 wedge함 — fail-open 위반 (REQ-052-A4)"
                )
            # 도달하면 OK — 사이클이 DB 실패로 중단되지 않았음


# ---------------------------------------------------------------------------
# AC-4 (REQ-052-B1/B2 — ADR-003): 조기경고 + 쿨다운 throttle
# ---------------------------------------------------------------------------

class TestAC4EarlyWarningThrottle:
    """maybe_send_cli_degraded_alert: 첫 발동 즉시, 쿨다운 내 throttle, 만료 후 재발사."""

    def test_first_alert_fires_immediately(self) -> None:
        """cli_degraded_notified_at=None → 첫 호출에 즉시 텔레그램 발사."""
        from trading.personas.base import maybe_send_cli_degraded_alert

        state = _state(
            cli_degraded=True,
            cli_degraded_notified_at=None,
        )
        now = datetime(2026, 6, 16, 9, 0, 0, tzinfo=UTC)
        written_state: dict[str, Any] = {}

        def fake_update(**kwargs: Any) -> None:
            written_state.update(kwargs)

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.update_system_state", side_effect=fake_update),
            patch("trading.alerts.telegram.system_briefing") as mock_tg,
        ):
            sent = maybe_send_cli_degraded_alert(
                cooldown_seconds=3600,
                now_provider=_make_now(now),
            )

        assert sent is True, "첫 발동에 즉시 True 반환 안됨 — RED (REQ-052-B2)"
        mock_tg.assert_called_once()
        assert written_state.get("cli_degraded_notified_at") == now

    def test_within_cooldown_throttled(self) -> None:
        """쿨다운(1h) 내 재호출 → throttle(미발사)."""
        from trading.personas.base import maybe_send_cli_degraded_alert

        last_sent = datetime(2026, 6, 16, 9, 0, 0, tzinfo=UTC)
        now = last_sent + timedelta(seconds=1799)  # 쿨다운 미만

        state = _state(
            cli_degraded=True,
            cli_degraded_notified_at=last_sent,
        )

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.update_system_state"),
            patch("trading.alerts.telegram.system_briefing") as mock_tg,
        ):
            sent = maybe_send_cli_degraded_alert(
                cooldown_seconds=3600,
                now_provider=_make_now(now),
            )

        assert sent is False, "쿨다운 내 throttle 안됨 — RED (REQ-052-B2)"
        mock_tg.assert_not_called()

    def test_after_cooldown_refires(self) -> None:
        """쿨다운(1h) 경과 후 재발사."""
        from trading.personas.base import maybe_send_cli_degraded_alert

        last_sent = datetime(2026, 6, 16, 9, 0, 0, tzinfo=UTC)
        now = last_sent + timedelta(seconds=3601)  # 쿨다운 초과

        state = _state(
            cli_degraded=True,
            cli_degraded_notified_at=last_sent,
        )
        written_state: dict[str, Any] = {}

        def fake_update(**kwargs: Any) -> None:
            written_state.update(kwargs)

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.update_system_state", side_effect=fake_update),
            patch("trading.alerts.telegram.system_briefing") as mock_tg,
        ):
            sent = maybe_send_cli_degraded_alert(
                cooldown_seconds=3600,
                now_provider=_make_now(now),
            )

        assert sent is True, "쿨다운 경과 후 재발사 안됨 — RED (REQ-052-B2)"
        mock_tg.assert_called_once()

    def test_alert_reset_on_healthy(self) -> None:
        """degraded 해제(성공) 후 throttle clock이 NULL로 리셋된다."""
        from trading.personas import base

        state = _state(
            cli_degraded=True,
            cli_consecutive_failures=3,
            cli_degraded_since=_utcnow(),
            cli_degraded_notified_at=_utcnow(),
        )
        written_state: dict[str, Any] = {}

        def fake_update(**kwargs: Any) -> None:
            written_state.update(kwargs)
            state.update(kwargs)

        fake_result = {"response_text": "ok", "persona_run_id": 1}

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.update_system_state", side_effect=fake_update),
            patch(f"{BASE}._ensure_cli_imports"),
            patch(f"{BASE}.CLICallError", Exception),
            patch(f"{BASE}.CLITimeoutError", Exception),
            patch(f"{BASE}.call_persona_cli", return_value=fake_result),
            patch(f"{BASE}.parse_cli_response", return_value={}),
            patch(f"{BASE}.build_cli_prompt", return_value="p"),
            patch("trading.alerts.telegram.system_briefing"),
            patch(f"{BASE}.audit"),
            patch(f"{BASE}.connection") as mock_conn,
        ):
            mock_cur = MagicMock()
            mock_cur.__enter__ = lambda s: s
            mock_cur.__exit__ = MagicMock(return_value=False)
            mock_cur.fetchone.return_value = (99,)
            mock_conn_ctx = MagicMock()
            mock_conn_ctx.__enter__ = lambda s: s
            mock_conn_ctx.__exit__ = MagicMock(return_value=False)
            mock_conn_ctx.cursor.return_value = mock_cur
            mock_conn.return_value = mock_conn_ctx

            try:
                base.call_persona_via_cli(
                    persona_name="test",
                    model="m",
                    cycle_kind="c",
                    system_prompt="s",
                    user_message="u",
                )
            except Exception:
                pass

        assert written_state.get("cli_degraded_notified_at") is None, (
            "성공 후 throttle clock이 NULL로 리셋 안됨 — RED (REQ-052-B2)"
        )


# ---------------------------------------------------------------------------
# AC-4b (REQ-052-B3 — D7): 무쿨다운 L541 알림 대체 + L557/L558 자동전환 알림 보존
# ---------------------------------------------------------------------------

class TestAC4bAlertReplacement:
    """L541 per-failure 무throttle 알림 → throttled alert 대체.
    L557/L558 자동전환 알림은 그대로 보존.
    """

    def test_per_failure_alert_replaced_by_throttle(self) -> None:
        """연속 실패 3회 동안 'CLI fallback' 알림이 매번 발사되지 않아야 한다.

        기존: 매 실패마다 tg.system_briefing("CLI fallback", ...) 호출
        신규: throttled alert 1회만 (또는 throttle에 따라 제한)
        """
        from trading.personas import base
        from trading.personas.base import _CLI_AUTO_DISABLE_THRESHOLD

        state = _state()
        tg_calls: list[tuple[str, ...]] = []

        def fake_update(**kwargs: Any) -> None:
            state.update(kwargs)

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.update_system_state", side_effect=fake_update),
            patch(f"{BASE}._ensure_cli_imports"),
            patch(f"{BASE}.CLICallError", Exception),
            patch(f"{BASE}.CLITimeoutError", Exception),
            patch(f"{BASE}.call_persona_cli", side_effect=Exception("fail")),
            patch(f"{BASE}.assert_fallback_model"),
            patch(f"{BASE}.call_persona"),
            patch(f"{BASE}.audit"),
            patch("trading.alerts.telegram.system_briefing",
                  side_effect=lambda title, *a, **kw: tg_calls.append((title,))),
        ):
            for _ in range(_CLI_AUTO_DISABLE_THRESHOLD):
                try:
                    base.call_persona_via_cli(
                        persona_name="test",
                        model="m",
                        cycle_kind="c",
                        system_prompt="s",
                        user_message="u",
                    )
                except Exception:
                    pass

        # "CLI fallback" 타이틀 알림이 3번 모두 발사되면 안 됨 (throttle 적용)
        fallback_alerts = [c for c in tg_calls if c[0] == "CLI fallback"]
        assert len(fallback_alerts) < _CLI_AUTO_DISABLE_THRESHOLD, (
            f"per-failure 무throttle 알림이 여전히 {len(fallback_alerts)}회 발사됨 — "
            "throttle로 대체되지 않음 — RED (REQ-052-B3)"
        )

    def test_auto_disabled_alert_preserved(self) -> None:
        """자동전환(cli_personas_enabled=False) 알림(L557)은 throttle 대상이 아니다."""
        from trading.personas import base
        from trading.personas.base import _CLI_AUTO_DISABLE_THRESHOLD

        state = _state()
        tg_calls: list[tuple[str, ...]] = []

        def fake_update(**kwargs: Any) -> None:
            state.update(kwargs)

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.update_system_state", side_effect=fake_update),
            patch(f"{BASE}._ensure_cli_imports"),
            patch(f"{BASE}.CLICallError", Exception),
            patch(f"{BASE}.CLITimeoutError", Exception),
            patch(f"{BASE}.call_persona_cli", side_effect=Exception("fail")),
            patch(f"{BASE}.assert_fallback_model"),
            patch(f"{BASE}.call_persona"),
            patch(f"{BASE}.audit"),
            patch("trading.alerts.telegram.system_briefing",
                  side_effect=lambda title, *a, **kw: tg_calls.append((title,))),
        ):
            for _ in range(_CLI_AUTO_DISABLE_THRESHOLD):
                try:
                    base.call_persona_via_cli(
                        persona_name="test",
                        model="m",
                        cycle_kind="c",
                        system_prompt="s",
                        user_message="u",
                    )
                except Exception:
                    pass

        # "CLI auto-disabled" 알림은 1회 발사되어야 함 (보존)
        auto_disabled = [c for c in tg_calls if c[0] == "CLI auto-disabled"]
        assert len(auto_disabled) >= 1, (
            "CLI auto-disabled 알림이 발사되지 않음 — L557/L558 알림 제거됨 — RED (REQ-052-B3)"
        )


# ---------------------------------------------------------------------------
# AC-5 (REQ-052-C1): strict ON → 유료 폴백 차단·defer
# ---------------------------------------------------------------------------

class TestAC5StrictBlock:
    """strict_cost_zero_mode=True → call_persona 호출 없이 defer."""

    def test_strict_on_blocks_paid_fallback(self) -> None:
        """strict=True + CLI 실패 → call_persona(유료)가 호출되지 않는다."""
        from trading.personas import base

        state = _state(
            cli_degraded=True,
            strict_cost_zero_mode=True,
        )

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.update_system_state"),
            patch(f"{BASE}._ensure_cli_imports"),
            patch(f"{BASE}.CLICallError", Exception),
            patch(f"{BASE}.CLITimeoutError", Exception),
            patch(f"{BASE}.call_persona_cli", side_effect=Exception("fail")),
            patch(f"{BASE}.assert_fallback_model"),
            patch(f"{BASE}.call_persona") as mock_paid,
            patch("trading.alerts.telegram.system_briefing"),
            patch(f"{BASE}.audit"),
        ):
            try:
                base.call_persona_via_cli(
                    persona_name="test",
                    model="m",
                    cycle_kind="c",
                    system_prompt="s",
                    user_message="u",
                )
            except Exception:
                pass

        mock_paid.assert_not_called(), (
            "strict=True에서 유료 call_persona가 호출됨 — RED (REQ-052-C1)"
        )

    def test_strict_on_logs_defer(self) -> None:
        """strict=True defer 시 구조화 로그가 남는다."""
        from trading.personas import base

        state = _state(
            cli_degraded=True,
            strict_cost_zero_mode=True,
        )

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.update_system_state"),
            patch(f"{BASE}._ensure_cli_imports"),
            patch(f"{BASE}.CLICallError", Exception),
            patch(f"{BASE}.CLITimeoutError", Exception),
            patch(f"{BASE}.call_persona_cli", side_effect=Exception("fail")),
            patch(f"{BASE}.assert_fallback_model"),
            patch(f"{BASE}.call_persona"),
            patch("trading.alerts.telegram.system_briefing"),
            patch(f"{BASE}.audit") as mock_audit,
        ):
            try:
                base.call_persona_via_cli(
                    persona_name="test",
                    model="m",
                    cycle_kind="c",
                    system_prompt="s",
                    user_message="u",
                )
            except Exception:
                pass

        # defer 이벤트가 audit 로그에 남아야 함
        audit_events = [str(c) for c in mock_audit.call_args_list]
        assert any("STRICT" in e or "DEFER" in e or "defer" in e.lower()
                   for e in audit_events), (
            "strict defer 시 audit 로그 없음 — RED (REQ-052-C3)"
        )


# ---------------------------------------------------------------------------
# AC-5b (REQ-052-C2 [HARD]): strict OFF → 기존 폴백 동작 불변
# ---------------------------------------------------------------------------

class TestAC5bStrictOffRegression:
    """strict=False(기본값) → 기존 SPEC-016 Haiku 폴백 동작 그대로."""

    def test_strict_off_allows_fallback(self) -> None:
        """strict=False에서 CLI 실패 시 Haiku 폴백이 정상 호출된다."""
        from trading.personas import base

        state = _state(strict_cost_zero_mode=False)
        mock_result = MagicMock()

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.update_system_state"),
            patch(f"{BASE}._ensure_cli_imports"),
            patch(f"{BASE}.CLICallError", Exception),
            patch(f"{BASE}.CLITimeoutError", Exception),
            patch(f"{BASE}.call_persona_cli", side_effect=Exception("fail")),
            patch(f"{BASE}.assert_fallback_model"),
            patch(f"{BASE}.call_persona", return_value=mock_result) as mock_paid,
            patch("trading.alerts.telegram.system_briefing"),
            patch(f"{BASE}.audit"),
        ):
            result = base.call_persona_via_cli(
                persona_name="test",
                model="m",
                cycle_kind="c",
                system_prompt="s",
                user_message="u",
            )

        mock_paid.assert_called_once(), (
            "strict=False에서 Haiku 폴백이 호출되지 않음 — SPEC-016 회귀 (REQ-052-C2)"
        )
        assert result is mock_result


# ---------------------------------------------------------------------------
# AC-6 (REQ-052-A3b + C): 3경로 단일소스 + strict 차단
# ---------------------------------------------------------------------------

class TestAC6ThreePathUnifiedSource:
    """decision.py 직접경로와 뉴스 _call_haiku가 동일 degraded 소스를 참조한다."""

    def test_decision_direct_path_marks_degraded_on_stale(self) -> None:
        """is_cli_mode_active()=False(stale) → decision 직접경로가 degraded 마킹."""
        state = _state(cli_personas_enabled=True)
        written_state: dict[str, Any] = {}

        def fake_update(**kwargs: Any) -> None:
            written_state.update(kwargs)
            state.update(kwargs)

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.update_system_state", side_effect=fake_update),
            patch("trading.db.session.get_system_state", return_value=state),
            patch("trading.db.session.update_system_state", side_effect=fake_update),
            patch("trading.personas.cli_bridge.is_watcher_alive", return_value=False),
            patch("trading.alerts.telegram.system_briefing"),
        ):
            from trading.personas.base import is_cli_mode_active
            is_cli_mode_active()

        assert written_state.get("cli_degraded") is True, (
            "stale watcher → is_cli_mode_active에서 degraded 마킹 안됨 — RED (REQ-052-A1b/A3b)"
        )

    def test_strict_on_blocks_direct_path_in_decision(self) -> None:
        """strict=True + stale watcher → decision.py 직접 call_persona 차단."""
        state = _state(
            cli_personas_enabled=True,
            cli_degraded=True,
            strict_cost_zero_mode=True,
        )

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.update_system_state"),
            patch("trading.personas.cli_bridge.is_watcher_alive", return_value=False),
            patch("trading.alerts.telegram.system_briefing"),
        ):
            from trading.personas.base import is_cli_mode_active, should_defer_paid_call
            cli_active = is_cli_mode_active()

        # strict ON + cli 비활성 → 유료 호출 차단
        assert cli_active is False
        # should_defer_paid_call이 True를 반환해야 함
        with patch(f"{BASE}.get_system_state", return_value=state):
            from trading.personas.base import should_defer_paid_call
            assert should_defer_paid_call() is True, (
                "strict=True 상태에서 should_defer_paid_call()이 True 반환 안됨 — RED (REQ-052-C1)"
            )


# ---------------------------------------------------------------------------
# AC-7 (REQ-052-D1): 세 경로 구조화 로그 동일 스키마
# ---------------------------------------------------------------------------

class TestAC7StructuredLog:
    """폴백/직접/뉴스 경로 — 동일 스키마 구조화 로그."""

    def test_fallback_path_emits_structured_log(self) -> None:
        """폴백 발동 시 persona/path/model/reason 구조화 로그가 남는다."""
        from trading.personas import base

        state = _state(strict_cost_zero_mode=False)
        log_records: list[dict] = []

        original_warning = logging.Logger.warning
        def capture_warning(self: Any, msg: str, *args: Any, **kwargs: Any) -> None:
            msg_str = str(msg)
            if "CLI failed" in msg_str or "PAID_CALL" in msg_str or "fallback" in msg_str.lower():
                log_records.append({"msg": msg, "args": args})
            original_warning(self, msg, *args, **kwargs)

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.update_system_state"),
            patch(f"{BASE}._ensure_cli_imports"),
            patch(f"{BASE}.CLICallError", Exception),
            patch(f"{BASE}.CLITimeoutError", Exception),
            patch(f"{BASE}.call_persona_cli", side_effect=Exception("empty")),
            patch(f"{BASE}.assert_fallback_model"),
            patch(f"{BASE}.call_persona", return_value=MagicMock()),
            patch("trading.alerts.telegram.system_briefing"),
            patch(f"{BASE}.audit") as mock_audit,
            patch.object(logging.Logger, "warning", capture_warning),
        ):
            try:
                base.call_persona_via_cli(
                    persona_name="test_persona",
                    model="m",
                    cycle_kind="c",
                    system_prompt="s",
                    user_message="u",
                )
            except Exception:
                pass

        # audit 또는 log에 persona/path/model/reason 포함 여부 확인
        all_audit_args = " ".join(str(c) for c in mock_audit.call_args_list)
        has_structured = (
            "test_persona" in all_audit_args
            or any("test_persona" in str(r) for r in log_records)
        )
        assert has_structured, (
            "폴백 발동 시 구조화 로그(persona 포함)가 없음 — RED (REQ-052-D1)"
        )


# ---------------------------------------------------------------------------
# maybe_send_cli_degraded_alert 존재 확인 테스트 (최소 smoke)
# ---------------------------------------------------------------------------

class TestMaybeSendCLIDegradedAlertExists:
    """maybe_send_cli_degraded_alert 헬퍼가 base.py에 노출되어 있어야 한다."""

    def test_helper_importable(self) -> None:
        """maybe_send_cli_degraded_alert를 base에서 import할 수 있어야 한다."""
        try:
            from trading.personas.base import maybe_send_cli_degraded_alert  # noqa: F401
        except ImportError as e:
            pytest.fail(f"maybe_send_cli_degraded_alert import 실패 — RED: {e}")

    def test_should_defer_paid_call_importable(self) -> None:
        """should_defer_paid_call을 base에서 import할 수 있어야 한다."""
        try:
            from trading.personas.base import should_defer_paid_call  # noqa: F401
        except ImportError as e:
            pytest.fail(f"should_defer_paid_call import 실패 — RED: {e}")
