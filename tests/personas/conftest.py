"""tests/personas 공용 픽스처.

2026-08-15: portfolio_gate 가 섹터캡/현금바닥 드롭과 페르소나 조정 사유를
audit 으로 남기기 시작했다(PORTFOLIO_GATE_DROP). audit 은 DB 를 쓰므로 DB 없는
단위 테스트에서는 no-op 으로 둔다. 감사 페이로드를 검증하는 테스트는 개별
patch 로 덮어쓴다.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _audit_noop_in_portfolio_gate():
    with patch("trading.personas.portfolio_gate.audit"):
        yield
