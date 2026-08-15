"""tests/risk 공용 픽스처.

2026-08-15: check_pre_order 에 손실 청산 재진입 쿨다운(days_since_loss_exit)이
추가됐다. 이 헬퍼는 orders 를 읽으므로 DB 가 없는 단위 테스트에서 실제 접속을
시도하면 KeyError(POSTGRES_USER)로 죽는다. 기존 테스트들은 쿨다운을 고려하지
않고 쓰였으므로 기본값을 "쿨다운 없음(None)"으로 고정한다 — 쿨다운 자체를
검증하는 테스트는 이 픽스처를 개별 patch 로 덮어쓴다.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _no_reentry_cooldown_by_default():
    with patch("trading.risk.limits.days_since_loss_exit", return_value=None):
        yield
