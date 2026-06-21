"""SPEC-TRADING-055 D1: generate_and_send resolver 헬스 격리 테스트.

D1 [CRITICAL]: evaluate_resolver_health() 가 raise 해도 generate_and_send 는
- 리포트 본문을 조립하고
- system_briefing("일일 리포트", ...) 을 반드시 호출(운영자 유일 신호)
- degraded 운영점검 라인을 포함
해야 한다.

Happy-path: evaluate_resolver_health 정상 → summary_line 텍스트가 리포트에 포함.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# 최소 _gather_today 픽스처
# ---------------------------------------------------------------------------

def _min_data() -> dict:
    return {
        "today": "2026-06-21",
        "orders": [],
        "runs": [],
        "risk": [],
        "cost": {},
        "cumulative": {},
        "tool_stats": {},
        "reflection_stats": {},
        "model_breakdown": [],
        "auto_expansion_tickers": [],
        "portfolio": {},
        "intelligence": {},
    }



# ---------------------------------------------------------------------------
# D1 테스트: health eval 이 raise → 리포트 여전히 전송됨
# ---------------------------------------------------------------------------

class TestD1HealthIsolation:
    def test_raising_health_eval_does_not_abort_report(self):
        """evaluate_resolver_health 가 raise → system_briefing 은 반드시 호출."""
        # generate_and_send 내부에서 lazy import 로 evaluate_resolver_health 를 가져오므로
        # 모듈 경로 패치가 필요.
        with (
            patch("trading.reports.daily_report._gather_today", return_value=_min_data()),
            patch("trading.reports.daily_report._narrative_text",
                  side_effect=RuntimeError("cli_only")),
            patch("trading.reports.daily_report._llm_skip_reason", return_value="CLI 전용"),
            patch("trading.reports.daily_report.connection",
                  _fake_connection_ctx()),
            patch("trading.reports.daily_report.system_briefing") as mock_brief,
            # generate_and_send 내 lazy import 경로를 raise 하는 함수로 교체
            patch("trading.ops.resolver_health.evaluate_resolver_health",
                  side_effect=RuntimeError("스키마 불일치 테스트")),
        ):
            from trading.reports.daily_report import generate_and_send
            generate_and_send()

        # system_briefing("일일 리포트", ...) 가 호출돼야 함
        assert mock_brief.called
        call_args = mock_brief.call_args
        assert call_args[0][0] == "일일 리포트"

        # degraded 운영점검 라인 포함 확인
        report_text = call_args[0][1]
        assert "운영점검: 평가 실패" in report_text

    def test_raising_health_eval_returns_text(self):
        """raise 해도 generate_and_send 가 str 을 반환(크래시 없음)."""
        with (
            patch("trading.reports.daily_report._gather_today", return_value=_min_data()),
            patch("trading.reports.daily_report._narrative_text",
                  side_effect=RuntimeError("cli_only")),
            patch("trading.reports.daily_report._llm_skip_reason", return_value="CLI 전용"),
            patch("trading.reports.daily_report.connection", _fake_connection_ctx()),
            patch("trading.reports.daily_report.system_briefing"),
            patch("trading.ops.resolver_health.evaluate_resolver_health",
                  side_effect=RuntimeError("DB 연결 불가")),
        ):
            from trading.reports.daily_report import generate_and_send
            result = generate_and_send()

        assert isinstance(result, str)
        assert "운영점검: 평가 실패" in result


# ---------------------------------------------------------------------------
# Happy-path: summary_line 이 리포트에 포함됨
# ---------------------------------------------------------------------------

class TestHealthLinePresentInReport:
    def test_summary_line_appears_in_report(self):
        """evaluate_resolver_health 정상 → summary_line 텍스트가 리포트 본문에 포함."""
        fake_health = {
            "last_resolver_run": None,
            "resolver_fresh": True,
            "stuck_count": 0,
            "parity": True,
            "parity_detail": {},
            "hard_anomalies": [],
            "soft_notes": [],
            "healthy_hard": True,
        }

        with (
            patch("trading.reports.daily_report._gather_today", return_value=_min_data()),
            patch("trading.reports.daily_report._narrative_text",
                  side_effect=RuntimeError("cli_only")),
            patch("trading.reports.daily_report._llm_skip_reason", return_value="CLI 전용"),
            patch("trading.reports.daily_report.connection", _fake_connection_ctx()),
            patch("trading.reports.daily_report.system_briefing") as mock_brief,
            patch("trading.ops.resolver_health.evaluate_resolver_health",
                  return_value=fake_health),
            patch("trading.ops.resolver_health.maybe_notify_resolver_anomaly",
                  return_value=False),
        ):
            from trading.reports.daily_report import generate_and_send
            result = generate_and_send()

        assert "SPEC-042 운영점검" in result
        report_text = mock_brief.call_args[0][1]
        assert "SPEC-042 운영점검" in report_text


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _fake_connection_ctx():
    """connection() 컨텍스트매니저 패치용."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx(**kw):
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__ = MagicMock(return_value=False)
        conn.__enter__ = lambda s: s
        conn.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cur
        yield conn

    return _ctx
