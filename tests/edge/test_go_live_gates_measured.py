"""실거래 준비 게이트는 실측 기반이어야 한다 (재현 우선).

2026-08-08~09 확인: ``GO_LIVE_GATES`` 는 하드코딩된 문자열 3개였고, 실제 상태를
한 번도 확인하지 않았다. 그 사이 세 항목이 모두 해소됐는데도 리포트는 계속
"실거래 전 반드시 해소" 라고 보고했다.

  - 일일 손실 한도: "-1.0% 가 빡빡" → 실제 RISK_DAILY_MAX_LOSS 는 -0.025(-2.5%)
    로 이미 권장치였다.
  - 익절 기록: "메모리 dict" → SPEC-TRADING-038 이 position_action_markers
    테이블로 옮겨 재시작에도 살아남는다.
  - .env 권한: "chmod 600 권장" → 이미 0600 이었다.

게이트가 해소된 항목을 계속 경고하면 운영자는 게이트를 무시하게 되고, 그러면
진짜 게이트도 함께 무시된다. 실전 전환 판단을 떠받치는 신호이므로 거짓 양성은
그 자체로 결함이다.

AC-1  게이트는 실측값을 읽어 해소 여부를 스스로 판정한다.
AC-2  한도가 권장치를 만족하면 해소로, 못 미치면 미해소로 판정한다.
AC-3  각 게이트는 판정 근거(실측값)를 함께 싣는다 — 근거 없는 판정은 검증 불가.
AC-4  렌더링은 미해소 건을 숨기지 않는다.
AC-5  검증할 수 없는 항목(키 회전)은 해소로 단정하지 않는다.
"""

from __future__ import annotations

from unittest.mock import patch

from trading.edge import scorecard as sc


class TestGatesAreMeasured:
    def test_evaluate_returns_resolution_state(self):
        """AC-1: 문자열 나열이 아니라 판정 결과여야 한다."""
        gates = sc.evaluate_go_live_gates()
        assert gates, "게이트가 비어 있음"
        for g in gates:
            assert hasattr(g, "resolved")
            assert hasattr(g, "message")
            assert hasattr(g, "evidence")

    def test_evidence_is_present_for_every_gate(self):
        """AC-3: 근거 없는 판정은 검증할 수 없다."""
        for g in sc.evaluate_go_live_gates():
            assert g.evidence, f"{g.key}: 판정 근거 없음"


class TestDailyLossLimitGate:
    def test_resolved_when_limit_meets_recommendation(self):
        """AC-2: -2.5% 는 권장치 충족 — 더 이상 경고하면 안 된다."""
        with patch.object(sc, "_daily_max_loss", return_value=-0.025):
            gate = sc._gate_daily_loss_limit()
        assert gate.resolved is True
        assert "2.5" in gate.evidence

    def test_unresolved_when_limit_too_tight(self):
        """AC-2: 실제로 빡빡하면 여전히 잡아내야 한다 — 무조건 통과는 아니다."""
        with patch.object(sc, "_daily_max_loss", return_value=-0.01):
            gate = sc._gate_daily_loss_limit()
        assert gate.resolved is False


class TestCredentialGate:
    def test_key_rotation_never_auto_resolves(self):
        """AC-5: 코드가 확인할 수 없는 것을 해소로 단정하면 거짓 안심을 준다."""
        gate = sc._gate_credential_rotation()
        assert gate.resolved is False


class TestRenderingKeepsUnresolvedVisible:
    def test_unresolved_gate_appears_in_report(self):
        """AC-4: 미해소 건이 렌더링에서 사라지면 게이트가 무의미해진다."""
        tight = sc.GoLiveGate(
            key="daily_loss_limit",
            message="한도가 너무 빡빡하다",
            resolved=False,
            evidence="실측 -1.00%",
        )
        text = sc.render_go_live_gates([tight])
        assert "한도가 너무 빡빡하다" in text
        assert "실측 -1.00%" in text

    def test_resolved_gate_is_not_reported_as_blocker(self):
        """해소된 항목을 '반드시 해소' 로 계속 보고하면 게이트가 늑대소년이 된다."""
        done = sc.GoLiveGate(
            key="took_profit_persistence",
            message="익절 기록이 메모리에만 존재",
            resolved=True,
            evidence="position_action_markers 테이블 사용",
        )
        text = sc.render_go_live_gates([done])
        assert "익절 기록이 메모리에만 존재" not in text
