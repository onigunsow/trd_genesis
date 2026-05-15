"""SPEC-TRADING-024 — shared watcher test fixtures.

The watcher modules share a process-global `TickerThrottle` via
`price_threshold._SHARED_THROTTLE`. Reset it before each test so per-test
behaviour stays deterministic regardless of test order.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_shared_throttle():
    """Reset the module-level shared throttle between tests."""
    from trading.watchers import price_threshold

    price_threshold._SHARED_THROTTLE = None
    yield
    price_threshold._SHARED_THROTTLE = None
