"""risk.jinja HOLD 재량 재조준 + code_rules_passed 실갱신 (2026-08-15).

실측: risk_reviews 482건 = APPROVE 83% / HOLD 16% / REJECT 0.6%. HOLD 82건 중
67건(82%)이 "단기과열 지정" 한 가지 사유. 그 종목들은 40일 +8.31% 로 APPROVE
(+5.07%)보다 나았다 — 유일한 재량 사유가 수익 진입을 걸러냈고, 단기과열은
코드 가드가 이미 처리한다. 원인: 프롬프트가 HOLD 를 화이트리스트 5개로 묶고
그중 4개는 코드가 강제하므로 모델에겐 과열 라벨 리피트만 남았다.

수정: 과열을 HOLD 사유에서 빼고, 오늘 실측된 손실 원인(늦은 진입 / 20일 버틸
근거 부재 / 손실 청산 재진입)을 재량 사유로 넣는다.

code_rules_passed: 482건 전부 False 하드코딩(죽은 컬럼) → check_pre_order
결과로 갱신하는 record_code_rules_result 배선.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from jinja2 import Environment, FileSystemLoader

_PROMPTS = (
    Path(__file__).resolve().parent.parent.parent / "src" / "trading" / "personas" / "prompts"
)


@pytest.fixture
def rendered() -> str:
    env = Environment(loader=FileSystemLoader(str(_PROMPTS)))
    return env.get_template("risk.jinja").render(
        today="2026-08-15", cycle_kind="intraday", decision_signals=[], assets={},
        cash_pct=50.0, daily_order_count=0, daily_pnl_pct=0.0,
        macro_summary="", micro_summary="",
    )


class TestOverheatNoLongerHoldReason:
    def test_overheat_explicitly_excluded(self, rendered):
        assert "단기과열(단일가매매) 지정은 HOLD 사유가 아니다" in rendered

    def test_overheat_not_in_allowed_list(self, rendered):
        """허용 목록에 '단기과열' 이 남아 있으면 82% 리피트가 재발한다."""
        allowed = rendered.split("HOLD 사유로 인정되는 것")[1].split("###")[0]
        assert "단기과열" not in allowed
        # 매매 자체가 불가한 지정(거래정지 등)은 남긴다
        assert "거래정지" in allowed

    def test_reason_given_code_already_enforces(self, rendered):
        assert "코드가 이미 강제" in rendered


class TestNewDiscretionaryReasons:
    def test_late_entry_is_hold_reason(self, rendered):
        assert "늦은 진입" in rendered
        assert "entry_freshness: late" in rendered

    def test_no_20d_thesis_is_hold_reason(self, rendered):
        assert "20일을 버틸 근거 부재" in rendered

    def test_loss_reentry_is_hold_reason(self, rendered):
        assert "최근 손실 청산 종목 재진입" in rendered
        assert "10거래일 안은 코드가 막는다" in rendered  # 코드 쿨다운과 분업 명시

    def test_approve_default_now_conditional_on_list(self, rendered):
        """'의심스러우면 APPROVE' 는 유지하되 조건이 붙는다 — 재량은 늘고 남발은 막힘."""
        assert "의심의 근거가 위 목록에 없으면" in rendered
        assert "검증자가 아무것도 거르지 않으면 존재 이유가 없다" in rendered


class TestCodeRulesResultRecorded:
    def _conn(self):
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__ = lambda s, *a: None
        conn = MagicMock()
        conn.cursor.return_value = cur
        conn.__enter__ = lambda s: s
        conn.__exit__ = lambda s, *a: None
        return conn, cur

    def test_updates_row_with_passed_and_breaches(self):
        from trading.personas import risk

        conn, cur = self._conn()
        with patch.object(risk, "connection", return_value=conn):
            risk.record_code_rules_result(
                17, passed=False, breaches=["reentry_cooldown: 064350 손실 청산 3일 전"],
            )
        sql, params = cur.execute.call_args.args
        assert "UPDATE risk_reviews" in sql
        assert "code_rules_passed" in sql
        assert params[0] is False
        assert "reentry_cooldown" in params[1]
        assert params[2] == 17

    def test_passed_true_recorded(self):
        from trading.personas import risk

        conn, cur = self._conn()
        with patch.object(risk, "connection", return_value=conn):
            risk.record_code_rules_result(5, passed=True, breaches=[])
        assert cur.execute.call_args.args[1][0] is True

    def test_none_review_id_is_noop(self):
        from trading.personas import risk

        conn, cur = self._conn()
        with patch.object(risk, "connection", return_value=conn):
            risk.record_code_rules_result(None, passed=True, breaches=[])
        assert cur.execute.call_count == 0

    def test_db_error_does_not_raise(self):
        """관측성 갱신이 주문 경로를 막으면 안 된다."""
        from trading.personas import risk

        with patch.object(risk, "connection", side_effect=RuntimeError("db down")):
            risk.record_code_rules_result(1, passed=True, breaches=[])  # no raise

    def test_orchestrator_calls_after_check_pre_order(self):
        """두 시그널 루프 모두 check_pre_order 직후 갱신을 호출해야 한다."""
        src = (Path(__file__).resolve().parent.parent.parent
               / "src" / "trading" / "personas" / "orchestrator.py").read_text(encoding="utf-8")
        assert src.count("risk_persona.record_code_rules_result(") == 2
        # 갱신 호출은 check_pre_order 결과(chk) 를 인자로 받는다
        assert src.count("passed=chk.passed, breaches=list(chk.breaches)") == 2
