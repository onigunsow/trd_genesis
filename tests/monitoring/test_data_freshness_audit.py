"""신선도 점검이 감사 기록을 남긴다 (재현 우선).

2026-08-08 사고 조사에서 막힌 지점: KRX 비밀번호 만료로 시장 데이터가 8/3~8/7
5거래일 동안 들어오지 않았는데, 09:00 신선도 점검이 실제로 돌았는지·알림을
보냈는지를 **사후에 확인할 방법이 없었다**. 컨테이너를 재생성하자 도커 로그가
비워졌고, ``check_and_alert`` 는 audit_log 에 아무것도 쓰지 않는다.

관측성 도구가 스스로의 실행 흔적을 남기지 않으면, 고장났을 때 "감시자가 잤는지
감시 대상이 멀쩡했는지" 구분할 수 없다. SPEC-TRADING-063 에서 주문 거부에
대해 같은 교훈을 얻었다 — 그때도 배관은 있었는데 기록이 없었다.

AC-1  점검이 끝나면 audit_log 에 DATA_FRESHNESS_CHECK 행이 남는다.
AC-2  기록에는 테이블별 최신일·stale 여부·알림 발송 여부가 담긴다.
AC-3  감사 기록 실패가 점검·알림 자체를 깨뜨리지 않는다.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from trading.monitoring import data_freshness as df


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event_type: str, **kwargs: Any) -> None:
        self.calls.append((event_type, kwargs))


class TestFreshnessCheckLeavesAuditTrail:
    def test_audit_row_is_written(self):
        """AC-1: 다음 사고 때 '돌았는지' 를 답할 수 있어야 한다."""
        rec = _Recorder()
        with patch.object(df, "audit", rec):
            df.check_and_alert(alert_sender=lambda _c, _m: None)

        assert rec.calls, "DATA_FRESHNESS_CHECK 감사 기록이 없음"
        assert rec.calls[0][0] == "DATA_FRESHNESS_CHECK"

    def test_audit_details_carry_table_state(self):
        """AC-2: '알렸는지'·'무엇이 낡았는지' 까지 담겨야 쓸모가 있다."""
        rec = _Recorder()
        with patch.object(df, "audit", rec):
            df.check_and_alert(alert_sender=lambda _c, _m: None)

        details = rec.calls[0][1]["details"]
        assert "alert_sent" in details
        assert "tables" in details
        assert details["tables"], "테이블별 상태가 비어 있음"
        first = details["tables"][0]
        for key in ("table", "latest", "stale"):
            assert key in first, f"{key} 누락"


class TestAuditFailureIsIsolated:
    def test_audit_failure_does_not_break_check(self):
        """AC-3: 기록 실패가 점검을 죽이면 관측성 추가가 되레 위험이 된다."""
        sent: list[tuple[str, str]] = []

        def _boom(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("db down")

        with patch.object(df, "audit", _boom):
            result = df.check_and_alert(
                alert_sender=lambda c, m: sent.append((c, m))
            )

        assert "entries" in result
        assert "alert_sent" in result
