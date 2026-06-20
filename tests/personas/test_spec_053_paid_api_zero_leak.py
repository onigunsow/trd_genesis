"""SPEC-TRADING-053: 유료 Anthropic API 비용 누수 완전 차단 TDD 테스트.

재현 우선(reproduction-first) — 각 근본원인에 대한 실패 테스트 먼저 작성.

AC-1: strict ON에서 _call_haiku raise + 호출자 catch = 비용 0 스킵
AC-1b: strict ON에서 _llm_text 직접 호출 시 raise (휴면 단위 테스트)
AC-2a: strict ON + cli_personas_enabled=False에서 should_defer_paid_call == True
AC-2b: strict ON에서 _record_cli_failure가 cli_personas_enabled를 끄지 않음 (카운터는 리셋)
AC-2c: strict ON + 워처 stale에서 call_persona 진입점 가드가 messages.create 차단
AC-2d: strict ON에서 is_cli_mode_active()가 워처 stale에도 False를 반환하지 않음
AC-2e: _narrative_text -> call_persona_via_cli -> should_defer_paid_call 경로
AC-3: strict OFF 기본 동작 불변 회귀
AC-8: PAID_CALL 계측 (messages.create 직전 로그 emit)
AC-9: DB 장애 시 fail-closed(last-known ON) / fail-open(콜드스타트/last-known OFF)
E5: strict ON에서도 카운터 리셋 항상 수행
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

BASE = "trading.personas.base"


def _state(**overrides: Any) -> dict[str, Any]:
    """최소 system_state dict 생성."""
    defaults: dict[str, Any] = {
        "cli_personas_enabled": True,
        "cli_only_mode": False,
        "halt_state": False,
        "cli_degraded": False,
        "cli_degraded_since": None,
        "cli_consecutive_failures": 0,
        "cli_degraded_notified_at": None,
        "strict_cost_zero_mode": False,
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# AC-2a (REQ-053-C1) — strict ON + cli_personas_enabled=False에서 should_defer == True
# ---------------------------------------------------------------------------

class TestAC2aShouldDeferStrictON:
    """strict ON이면 cli_personas_enabled/cli_only_mode와 무관하게 True 반환."""

    def test_strict_on_cli_flags_false_returns_true(self) -> None:
        """strict ON + 모든 cli 플래그 False → True (가드 플래그 분리, REQ-053-C1)."""
        state = _state(strict_cost_zero_mode=True, cli_personas_enabled=False, cli_only_mode=False)
        with patch(f"{BASE}.get_system_state", return_value=state):
            from trading.personas.base import should_defer_paid_call
            result = should_defer_paid_call()
        assert result is True, "strict ON에서 cli 플래그와 무관하게 True여야 한다"

    def test_strict_on_cli_enabled_true_returns_true(self) -> None:
        """strict ON + cli_personas_enabled=True여도 True 반환."""
        state = _state(strict_cost_zero_mode=True, cli_personas_enabled=True)
        with patch(f"{BASE}.get_system_state", return_value=state):
            from trading.personas.base import should_defer_paid_call
            result = should_defer_paid_call()
        assert result is True

    def test_strict_off_returns_false(self) -> None:
        """strict OFF → False (REQ-052-C2 불변)."""
        state = _state(strict_cost_zero_mode=False, cli_personas_enabled=True)
        with patch(f"{BASE}.get_system_state", return_value=state):
            from trading.personas.base import should_defer_paid_call
            result = should_defer_paid_call()
        assert result is False, "strict OFF에서는 False여야 한다 (SPEC-016 불변)"


# ---------------------------------------------------------------------------
# AC-9 (REQ-053-C3) — DB 장애 시 fail-closed / 콜드스타트 fail-open
# ---------------------------------------------------------------------------

class TestAC9DBFailureBehavior:
    """get_system_state 예외 시 last-known strict 캐시 기반 분기."""

    def test_coldstart_db_failure_fail_open(self) -> None:
        """콜드스타트(빈 캐시) + DB 예외 → False(fail-open, D-new4)."""
        import trading.personas.base as base_mod
        # 빈 캐시 상태 강제
        original = base_mod._LAST_KNOWN_STRICT  # noqa: SLF001
        base_mod._LAST_KNOWN_STRICT = None  # noqa: SLF001
        try:
            with patch(f"{BASE}.get_system_state", side_effect=Exception("DB down")):
                from trading.personas.base import should_defer_paid_call
                result = should_defer_paid_call()
            assert result is False, "콜드스타트 + DB 장애는 fail-open(False)이어야 한다"
        finally:
            base_mod._LAST_KNOWN_STRICT = original  # noqa: SLF001

    def test_last_known_strict_on_db_failure_fail_closed(self) -> None:
        """last-known strict ON 캐시 + DB 예외 → True(fail-closed)."""
        import trading.personas.base as base_mod
        original = base_mod._LAST_KNOWN_STRICT  # noqa: SLF001
        base_mod._LAST_KNOWN_STRICT = True  # noqa: SLF001
        try:
            with patch(f"{BASE}.get_system_state", side_effect=Exception("DB down")):
                from trading.personas.base import should_defer_paid_call
                result = should_defer_paid_call()
            assert result is True, "last-known strict ON + DB 장애는 fail-closed(True)이어야 한다"
        finally:
            base_mod._LAST_KNOWN_STRICT = original  # noqa: SLF001

    def test_last_known_strict_off_db_failure_fail_open(self) -> None:
        """last-known strict OFF 캐시 + DB 예외 → False(SPEC-016 불변)."""
        import trading.personas.base as base_mod
        original = base_mod._LAST_KNOWN_STRICT  # noqa: SLF001
        base_mod._LAST_KNOWN_STRICT = False  # noqa: SLF001
        try:
            with patch(f"{BASE}.get_system_state", side_effect=Exception("DB down")):
                from trading.personas.base import should_defer_paid_call
                result = should_defer_paid_call()
            assert result is False, "last-known strict OFF + DB 장애는 fail-open이어야 한다"
        finally:
            base_mod._LAST_KNOWN_STRICT = original  # noqa: SLF001

    def test_successful_call_updates_cache(self) -> None:
        """성공적인 get_system_state 호출이 _LAST_KNOWN_STRICT 캐시를 갱신한다."""
        import trading.personas.base as base_mod
        original = base_mod._LAST_KNOWN_STRICT  # noqa: SLF001
        base_mod._LAST_KNOWN_STRICT = None  # noqa: SLF001
        try:
            state = _state(strict_cost_zero_mode=True)
            with patch(f"{BASE}.get_system_state", return_value=state):
                from trading.personas.base import should_defer_paid_call
                should_defer_paid_call()
            assert base_mod._LAST_KNOWN_STRICT is True  # noqa: SLF001
        finally:
            base_mod._LAST_KNOWN_STRICT = original  # noqa: SLF001


# ---------------------------------------------------------------------------
# AC-2b (REQ-053-D1) — strict ON에서 _record_cli_failure가 cli_personas_enabled를 끄지 않음
# E5 — 카운터는 strict 무관하게 임계 도달 시 항상 리셋
# ---------------------------------------------------------------------------

class TestAC2bRecordCliFailureStrictON:
    """strict ON에서 auto-disable 부수효과 생략, 카운터는 항상 리셋."""

    def test_strict_on_no_auto_disable(self) -> None:
        """strict ON + 3연속 실패 → cli_personas_enabled=False 자동전환 미발생 (REQ-053-D1)."""
        from trading.personas.base import _CLI_AUTO_DISABLE_THRESHOLD
        import trading.personas.base as base_mod

        original_count = base_mod._cli_failure_count  # noqa: SLF001
        base_mod._cli_failure_count = _CLI_AUTO_DISABLE_THRESHOLD - 1  # noqa: SLF001

        state = _state(strict_cost_zero_mode=True, cli_personas_enabled=True)
        update_calls: list[dict] = []

        def fake_update(**kwargs: Any) -> None:
            update_calls.append(kwargs)
            state.update(kwargs)

        try:
            with (
                patch(f"{BASE}.get_system_state", return_value=state),
                patch(f"{BASE}.update_system_state", side_effect=fake_update),
                patch(f"{BASE}._persist_cli_degraded"),
                patch(f"{BASE}.maybe_send_cli_degraded_alert"),
                patch(f"{BASE}._log_paid_call"),
            ):
                from trading.personas.base import _record_cli_failure
                _record_cli_failure("decision", "test_failure")

        finally:
            base_mod._cli_failure_count = original_count  # noqa: SLF001

        # cli_personas_enabled=False auto-disable 호출이 없어야 함
        auto_disable_calls = [c for c in update_calls if c.get("cli_personas_enabled") is False]
        assert len(auto_disable_calls) == 0, (
            f"strict ON에서 cli_personas_enabled=False 자동전환이 발생했다: {auto_disable_calls}"
        )

    def test_strict_on_counter_resets_at_threshold(self) -> None:
        """strict ON에서도 카운터는 임계 도달 시 0으로 리셋 (E5, 무한 증가 방지)."""
        from trading.personas.base import _CLI_AUTO_DISABLE_THRESHOLD
        import trading.personas.base as base_mod

        original_count = base_mod._cli_failure_count  # noqa: SLF001
        base_mod._cli_failure_count = _CLI_AUTO_DISABLE_THRESHOLD - 1  # noqa: SLF001

        state = _state(strict_cost_zero_mode=True)

        try:
            with (
                patch(f"{BASE}.get_system_state", return_value=state),
                patch(f"{BASE}.update_system_state"),
                patch(f"{BASE}._persist_cli_degraded"),
                patch(f"{BASE}.maybe_send_cli_degraded_alert"),
                patch(f"{BASE}._log_paid_call"),
            ):
                from trading.personas.base import _record_cli_failure
                _record_cli_failure("decision", "test_failure")

            # 카운터가 0으로 리셋되어야 함
            assert base_mod._cli_failure_count == 0, (  # noqa: SLF001
                f"strict ON에서도 카운터는 임계 도달 시 0으로 리셋되어야 한다. "
                f"현재: {base_mod._cli_failure_count}"  # noqa: SLF001
            )
        finally:
            base_mod._cli_failure_count = original_count  # noqa: SLF001

    def test_strict_off_auto_disable_works(self) -> None:
        """strict OFF에서는 기존 자동전환 동작 불변 (REQ-053-D2 회귀)."""
        from trading.personas.base import _CLI_AUTO_DISABLE_THRESHOLD
        import trading.personas.base as base_mod

        original_count = base_mod._cli_failure_count  # noqa: SLF001
        base_mod._cli_failure_count = _CLI_AUTO_DISABLE_THRESHOLD - 1  # noqa: SLF001

        state = _state(strict_cost_zero_mode=False, cli_personas_enabled=True)
        update_calls: list[dict] = []

        def fake_update(**kwargs: Any) -> None:
            update_calls.append(kwargs)
            state.update(kwargs)

        try:
            with (
                patch(f"{BASE}.get_system_state", return_value=state),
                patch(f"{BASE}.update_system_state", side_effect=fake_update),
                patch(f"{BASE}._persist_cli_degraded"),
                patch(f"{BASE}.maybe_send_cli_degraded_alert"),
                patch(f"{BASE}._log_paid_call"),
                patch("trading.alerts.telegram.system_briefing"),
            ):
                from trading.personas.base import _record_cli_failure
                _record_cli_failure("decision", "test_failure")

        finally:
            base_mod._cli_failure_count = original_count  # noqa: SLF001

        # strict OFF에서는 auto-disable이 발생해야 함
        auto_disable_calls = [c for c in update_calls if c.get("cli_personas_enabled") is False]
        assert len(auto_disable_calls) >= 1, (
            "strict OFF에서는 cli_personas_enabled=False 자동전환이 발생해야 한다"
        )


# ---------------------------------------------------------------------------
# AC-1 (REQ-053-B1/B2) — strict ON에서 is_cli_only_mode True + 데코레이터 raise
# ---------------------------------------------------------------------------

class TestAC1IsCliOnlyModeStrictAware:
    """is_cli_only_mode()가 strict_cost_zero_mode=True일 때 True 반환."""

    def test_strict_on_returns_true(self) -> None:
        """strict ON → is_cli_only_mode() True (REQ-053-B1)."""
        state = _state(strict_cost_zero_mode=True, cli_only_mode=False, cli_personas_enabled=False)
        with patch(f"{BASE}.get_system_state", return_value=state):
            from trading.personas.base import is_cli_only_mode
            result = is_cli_only_mode()
        assert result is True, "strict ON → is_cli_only_mode는 True여야 한다"

    def test_strict_off_cli_flags_active_returns_true(self) -> None:
        """strict OFF지만 cli_personas_enabled=True → True (기존 동작 보존)."""
        state = _state(strict_cost_zero_mode=False, cli_personas_enabled=True)
        with patch(f"{BASE}.get_system_state", return_value=state):
            from trading.personas.base import is_cli_only_mode
            result = is_cli_only_mode()
        assert result is True

    def test_strict_off_all_flags_false_returns_false(self) -> None:
        """strict OFF + 모든 cli 플래그 False → False (기존 동작 보존)."""
        state = _state(strict_cost_zero_mode=False, cli_only_mode=False, cli_personas_enabled=False)
        with patch(f"{BASE}.get_system_state", return_value=state):
            from trading.personas.base import is_cli_only_mode
            result = is_cli_only_mode()
        assert result is False

    def test_cli_personas_enabled_true_still_true_after_strict_extension(self) -> None:
        """cli_personas_enabled=True일 때 기존 동작과 동일(True) — 라이브 DB 상태 호환성."""
        state = _state(strict_cost_zero_mode=False, cli_personas_enabled=True, cli_only_mode=False)
        with patch(f"{BASE}.get_system_state", return_value=state):
            from trading.personas.base import is_cli_only_mode
            result = is_cli_only_mode()
        assert result is True, "cli_personas_enabled=True는 여전히 True여야 한다"


class TestAC1DecoratorRaisesOnStrictON:
    """block_if_cli_only_mode 데코레이터가 strict ON에서 RuntimeError raise."""

    def test_decorator_raises_when_strict_on(self) -> None:
        """strict ON → 데코레이터 RuntimeError raise (REQ-053-B2)."""
        state = _state(strict_cost_zero_mode=True, cli_only_mode=False, cli_personas_enabled=False)
        with patch(f"{BASE}.get_system_state", return_value=state):
            from trading.personas.base import block_if_cli_only_mode

            @block_if_cli_only_mode
            def dummy_api_call() -> str:
                return "paid_result"

            with pytest.raises(RuntimeError):
                dummy_api_call()

    def test_decorator_passes_when_strict_off_all_false(self) -> None:
        """strict OFF + 모든 플래그 False → 데코레이터 통과 (REQ-053-B4 회귀)."""
        state = _state(strict_cost_zero_mode=False, cli_only_mode=False, cli_personas_enabled=False)
        with patch(f"{BASE}.get_system_state", return_value=state):
            from trading.personas.base import block_if_cli_only_mode

            @block_if_cli_only_mode
            def dummy_api_call() -> str:
                return "paid_result"

            result = dummy_api_call()
        assert result == "paid_result"


# ---------------------------------------------------------------------------
# AC-1 (REQ-053-B2) — _call_haiku 호출자 catch가 strict ON raise를 흡수 + pending 보존
# ---------------------------------------------------------------------------

class TestAC1CallHaikuCallerCatch:
    """analyzer.py 호출자 catch(631/637)가 strict ON raise를 흡수하고 pending을 보존."""

    def test_strict_on_call_haiku_blocked_no_messages_create(self) -> None:
        """strict ON → _call_haiku 데코레이터 raise → messages.create 0회 (유료 0)."""
        state = _state(strict_cost_zero_mode=True)
        mock_client = MagicMock()

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch("trading.news.intelligence.analyzer.get_settings",
                  return_value=MagicMock(
                      anthropic=MagicMock(api_key=MagicMock(get_secret_value=lambda: "test"))
                  )),
            patch("trading.news.intelligence.analyzer.Anthropic", return_value=mock_client),
        ):
            from trading.news.intelligence.analyzer import _call_haiku
            with pytest.raises((RuntimeError, Exception)):
                _call_haiku([{"url": "http://test.com", "title": "test", "body": "test"}])

        # messages.create 호출 없음
        mock_client.messages.create.assert_not_called()


# ---------------------------------------------------------------------------
# AC-1b (REQ-053-B1, daily_report 단위) — _llm_text strict ON raise
# ---------------------------------------------------------------------------

class TestAC1bLlmTextStrictON:
    """strict ON → _llm_text 직접 호출 시 데코레이터 raise + messages.create 0회 (휴면)."""

    def test_strict_on_llm_text_raises(self) -> None:
        """strict ON → _llm_text raise (유료 Sonnet 0회, REQ-053-B1)."""
        state = _state(strict_cost_zero_mode=True)
        mock_client = MagicMock()

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch("trading.reports.daily_report.get_settings",
                  return_value=MagicMock(
                      anthropic=MagicMock(api_key=MagicMock(get_secret_value=lambda: "test"))
                  )),
            patch("trading.reports.daily_report.Anthropic", return_value=mock_client),
        ):
            from trading.reports.daily_report import _llm_text
            with pytest.raises((RuntimeError, Exception)):
                _llm_text({"portfolio": [], "positions": []})

        mock_client.messages.create.assert_not_called()


# ---------------------------------------------------------------------------
# AC-2c (REQ-053-G) — call_persona 진입점 가드, strict ON + 워처 stale에서 차단
# ---------------------------------------------------------------------------

class TestAC2cCallPersonaEntryGuard:
    """D-CRIT-1 주 방어: call_persona 진입점에서 strict ON 시 messages.create 도달 전 차단."""

    def _make_mock_client(self) -> MagicMock:
        from tests.conftest import FakeAnthropicMessage
        mock_client = MagicMock()
        msg = FakeAnthropicMessage(text='{"signals": []}')
        mock_client.messages.create.return_value = msg
        return mock_client

    def test_strict_on_call_persona_blocked_before_try(self) -> None:
        """strict ON → call_persona 진입점 가드가 try 블록(263) 이전에 raise.

        messages.create(280/353/408)에 도달하지 않는다.
        raise는 내부 except(393)에 삼켜지지 않으므로 PersonaResult가 반환되지 않는다.
        """
        state = _state(strict_cost_zero_mode=True)
        mock_client = self._make_mock_client()

        from tests.conftest import FakeCursor, FakeConnection
        cursor = FakeCursor(rows=[{"id": 42}])
        conn = FakeConnection(cursor)

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.Anthropic", return_value=mock_client),
            patch(f"{BASE}.connection", return_value=conn),
            patch(f"{BASE}.get_settings",
                  return_value=MagicMock(
                      anthropic=MagicMock(api_key=MagicMock(get_secret_value=lambda: "test"))
                  )),
        ):
            from trading.personas.base import call_persona
            with pytest.raises(RuntimeError):
                call_persona(
                    persona_name="decision",
                    model="claude-sonnet-4-6",
                    cycle_kind="pre_market",
                    system_prompt="test",
                    user_message="test",
                    apply_memory_ops=False,
                )

        # messages.create가 호출되지 않아야 함 (유료 0)
        mock_client.messages.create.assert_not_called()

    def test_strict_off_call_persona_proceeds(self) -> None:
        """strict OFF → call_persona 진입점 가드 통과, 기존 직접-API 경로 보존 (REQ-053-G4 회귀)."""
        state = _state(strict_cost_zero_mode=False)
        mock_client = self._make_mock_client()

        from tests.conftest import FakeCursor, FakeConnection
        cursor = FakeCursor(rows=[{"id": 42}])
        conn = FakeConnection(cursor)

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}.Anthropic", return_value=mock_client),
            patch(f"{BASE}.connection", return_value=conn),
            patch(f"{BASE}.get_settings",
                  return_value=MagicMock(
                      anthropic=MagicMock(api_key=MagicMock(get_secret_value=lambda: "test"))
                  )),
        ):
            from trading.personas.base import call_persona
            result = call_persona(
                persona_name="decision",
                model="claude-sonnet-4-6",
                cycle_kind="pre_market",
                system_prompt="test",
                user_message="test",
                apply_memory_ops=False,
            )

        # strict OFF에서는 messages.create가 호출되어야 함
        mock_client.messages.create.assert_called()
        assert result.persona_run_id == 42

    def test_strict_on_call_persona_raise_not_swallowed_by_internal_except(self) -> None:
        """call_persona 진입점 가드 raise가 내부 except(393)에 삼켜지지 않음 (배치 제약 D3).

        RuntimeError가 call_persona 밖으로 전파되어야 한다.
        내부 except(393)에 삼켜지면 PersonaResult(error=...)가 반환되는데 그래서는 안 된다.
        """
        state = _state(strict_cost_zero_mode=True)

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
        ):
            from trading.personas.base import call_persona
            # RuntimeError가 함수 밖으로 전파되어야 함 (PersonaResult 반환 X)
            with pytest.raises(RuntimeError):
                call_persona(
                    persona_name="decision",
                    model="claude-sonnet-4-6",
                    cycle_kind="pre_market",
                    system_prompt="test",
                    user_message="test",
                    apply_memory_ops=False,
                )


# ---------------------------------------------------------------------------
# AC-2d (REQ-053-D4) — strict ON에서 is_cli_mode_active() 워처 stale에도 True
# ---------------------------------------------------------------------------

class TestAC2dIsCliModeActiveStrictAware:
    """strict ON에서 is_cli_mode_active()가 워처 stale에도 False를 반환하지 않음."""

    def test_strict_on_watcher_stale_returns_true(self) -> None:
        """strict ON + 워처 stale → is_cli_mode_active() True (REQ-053-D4 보조 방어)."""
        state = _state(strict_cost_zero_mode=True, cli_personas_enabled=True)

        with (
            patch("trading.db.session.get_system_state", return_value=state),
            patch(f"{BASE}.get_system_state", return_value=state),
            patch("trading.personas.cli_bridge.is_watcher_alive", return_value=False),
            patch(f"{BASE}._persist_cli_degraded"),
        ):
            from trading.personas.base import is_cli_mode_active
            result = is_cli_mode_active()

        assert result is True, (
            "strict ON + 워처 stale에서 is_cli_mode_active()는 True여야 한다 (REQ-053-D4)"
        )

    def test_strict_off_watcher_stale_returns_false(self) -> None:
        """strict OFF + 워처 stale → is_cli_mode_active() False (기존 동작 보존)."""
        state = _state(strict_cost_zero_mode=False, cli_personas_enabled=True)

        with (
            patch("trading.db.session.get_system_state", return_value=state),
            patch(f"{BASE}.get_system_state", return_value=state),
            patch("trading.personas.cli_bridge.is_watcher_alive", return_value=False),
            patch(f"{BASE}._persist_cli_degraded"),
            patch("trading.alerts.telegram.system_briefing"),
        ):
            from trading.personas.base import is_cli_mode_active
            result = is_cli_mode_active()

        assert result is False, "strict OFF + 워처 stale에서는 기존대로 False여야 한다"


# ---------------------------------------------------------------------------
# AC-8 (REQ-053-F1) — 5개 유료 호출 지점 PAID_CALL 계측
# strict OFF 실제 발동 시 messages.create 직전 PAID_CALL 로그 emit
# ---------------------------------------------------------------------------

class TestAC8PaidCallInstrumentation:
    """messages.create 직전 PAID_CALL 구조화 로그가 emit된다."""

    def test_call_persona_paid_call_log_emitted(self, caplog: pytest.LogCaptureFixture) -> None:
        """call_persona strict OFF 실제 발동 시 PAID_CALL 로그 emit (base.py:280)."""
        import logging
        from tests.conftest import FakeAnthropicMessage, FakeCursor, FakeConnection
        state = _state(strict_cost_zero_mode=False)
        mock_client = MagicMock()
        msg = FakeAnthropicMessage(text='{"signals": []}')
        mock_client.messages.create.return_value = msg

        cursor = FakeCursor(rows=[{"id": 42}])
        conn = FakeConnection(cursor)

        with caplog.at_level(logging.WARNING, logger="trading.personas.base"):
            with (
                patch(f"{BASE}.get_system_state", return_value=state),
                patch(f"{BASE}.Anthropic", return_value=mock_client),
                patch(f"{BASE}.connection", return_value=conn),
                patch(f"{BASE}.get_settings",
                      return_value=MagicMock(
                          anthropic=MagicMock(api_key=MagicMock(get_secret_value=lambda: "test"))
                      )),
            ):
                from trading.personas.base import call_persona
                call_persona(
                    persona_name="decision",
                    model="claude-sonnet-4-6",
                    cycle_kind="pre_market",
                    system_prompt="test",
                    user_message="test",
                    apply_memory_ops=False,
                )

        paid_logs = [r for r in caplog.records if "PAID_CALL" in r.getMessage()]
        assert len(paid_logs) >= 1, (
            "call_persona는 messages.create 직전 PAID_CALL 로그를 emit해야 한다 (REQ-053-F1)"
        )

    def test_strict_on_call_persona_cli_degraded_defer_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """strict ON → call_persona 차단 시 CLI_DEGRADED_DEFER audit/로그 emit (REQ-053-G3)."""
        import logging
        state = _state(strict_cost_zero_mode=True)

        with caplog.at_level(logging.WARNING, logger="trading.personas.base"):
            with patch(f"{BASE}.get_system_state", return_value=state):
                from trading.personas.base import call_persona
                with pytest.raises(RuntimeError):
                    call_persona(
                        persona_name="decision",
                        model="claude-sonnet-4-6",
                        cycle_kind="pre_market",
                        system_prompt="test",
                        user_message="test",
                        apply_memory_ops=False,
                    )

        defer_logs = [r for r in caplog.records if "CLI_DEGRADED_DEFER" in r.getMessage()]
        assert len(defer_logs) >= 1, (
            "strict ON → call_persona 차단 시 CLI_DEGRADED_DEFER 로그가 emit되어야 한다"
        )


# ---------------------------------------------------------------------------
# AC-3 (REQ-053-C2/D2) — strict OFF 기본 동작 불변 회귀
# ---------------------------------------------------------------------------

class TestAC3StrictOffRegression:
    """strict OFF 기본 동작이 SPEC-016과 동일하게 보존된다."""

    def test_strict_off_should_defer_false(self) -> None:
        """strict OFF → should_defer_paid_call() == False."""
        state = _state(strict_cost_zero_mode=False, cli_personas_enabled=True)
        with patch(f"{BASE}.get_system_state", return_value=state):
            from trading.personas.base import should_defer_paid_call
            assert should_defer_paid_call() is False

    def test_cli_only_mode_true_decorator_raises(self) -> None:
        """cli_only_mode=True (strict OFF) → 데코레이터 raise (SPEC-016 계약 보존)."""
        state = _state(strict_cost_zero_mode=False, cli_only_mode=True)
        with patch(f"{BASE}.get_system_state", return_value=state):
            from trading.personas.base import block_if_cli_only_mode

            @block_if_cli_only_mode
            def dummy() -> str:
                return "result"

            with pytest.raises(RuntimeError):
                dummy()

    def test_strict_off_is_cli_only_mode_with_cli_personas_enabled(self) -> None:
        """strict OFF + cli_personas_enabled=True → is_cli_only_mode True (기존 동작)."""
        state = _state(strict_cost_zero_mode=False, cli_personas_enabled=True)
        with patch(f"{BASE}.get_system_state", return_value=state):
            from trading.personas.base import is_cli_only_mode
            assert is_cli_only_mode() is True


# ---------------------------------------------------------------------------
# REQ-053-B6 (scheduler) — strict ON에서 is_cli_only_mode() True → scheduler defer
# ---------------------------------------------------------------------------

class TestB6SchedulerStrictAware:
    """strict ON에서 scheduler.py:204의 is_cli_only_mode() 검사가 True → defer."""

    def test_strict_on_is_cli_only_mode_returns_true_for_scheduler(self) -> None:
        """strict ON → is_cli_only_mode() True (scheduler defer로 이어짐, B6)."""
        state = _state(strict_cost_zero_mode=True, cli_personas_enabled=False, cli_only_mode=False)
        with patch(f"{BASE}.get_system_state", return_value=state):
            from trading.personas.base import is_cli_only_mode
            assert is_cli_only_mode() is True, (
                "strict ON에서 scheduler의 is_cli_only_mode 게이트가 True여야 defer 발동"
            )


# ---------------------------------------------------------------------------
# AC-2e (REQ-053-C1) — call_persona_via_cli의 should_defer_paid_call 게이트 (strict ON)
# ---------------------------------------------------------------------------

class TestAC2eCallPersonaViaCliStrictON:
    """strict ON에서 call_persona_via_cli의 should_defer 게이트가 차단한다."""

    def test_strict_on_call_persona_via_cli_defers(self) -> None:
        """strict ON → call_persona_via_cli should_defer → RuntimeError (AC-2e)."""
        state = _state(strict_cost_zero_mode=True)

        with (
            patch(f"{BASE}.get_system_state", return_value=state),
            patch(f"{BASE}._ensure_cli_imports"),
            patch(f"{BASE}.CLICallError", Exception),
            patch(f"{BASE}.CLITimeoutError", Exception),
            patch(f"{BASE}.call_persona_cli", side_effect=Exception("cli failed")),
            patch(f"{BASE}.build_cli_prompt", return_value="prompt"),
            patch(f"{BASE}.assert_fallback_model"),
            patch(f"{BASE}.audit"),
            patch(f"{BASE}.maybe_send_cli_degraded_alert"),
        ):
            from trading.personas.base import call_persona_via_cli
            with pytest.raises(RuntimeError):
                call_persona_via_cli(
                    persona_name="decision",
                    model="claude-sonnet-4-6",
                    cycle_kind="pre_market",
                    system_prompt="test",
                    user_message="test",
                    apply_memory_ops=False,
                )
