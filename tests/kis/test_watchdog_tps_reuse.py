"""SPEC-TRADING-043 REQ-043-B2/B4 — watchdog reuses cached balance + safe degrade.

Within one ``poll_position_watchdog`` the watchdog reads ``balance()`` twice
(holdings + concentration-cap denominator). With the transparent read-through
cache those collapse into a single underlying ``inquire-balance`` call, so the
watchdog's TPS contribution per poll halves and TPS-attributable forced skips
drop toward zero. When a balance read genuinely fails the watchdog still degrades
safely (skip poll), proving the cache did not weaken the safety net.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import trading.kis.account as account_mod
import trading.watchers.position_watchdog as wd
from trading.kis.balance_cache import BalanceCache
from trading.kis.client import KisResponse


def _balance_response() -> KisResponse:
    raw: dict[str, Any] = {
        "rt_cd": "0", "msg_cd": "", "msg1": "",
        "output1": [],  # no holdings → loop body skipped, both reads still happen
        "output2": [{
            "dnca_tot_amt": "5000000", "scts_evlu_amt": "0",
            "tot_evlu_amt": "5000000", "nxdy_excc_amt": "5000000",
            "nrcvb_buy_amt": "0", "evlu_pfls_smtl_amt": "0",
        }],
    }
    return KisResponse(status_code=200, rt_cd="0", msg_cd="", msg="OK",
                       output=[], raw=raw)


def _client() -> MagicMock:
    c = MagicMock()
    c.account_prefix = "12345678"
    c.account_suffix = "01"
    c.tr_id = MagicMock(side_effect=lambda paper_id, live_id: paper_id)
    c._account_full = "12345678-01"
    c.get.return_value = _balance_response()
    return c


def test_single_poll_collapses_two_balance_reads_into_one_kis_call():
    """REQ-043-B2: holdings read + portfolio-value read share one inquire-balance."""
    fresh_cache = BalanceCache(ttl=2.0)
    client = _client()
    with (
        patch.object(account_mod, "_CACHE", fresh_cache),
        patch.object(wd, "_build_client", return_value=client),
        patch.object(wd, "_late_cycle_active", return_value=False),
    ):
        metrics = wd.poll_position_watchdog()

    # Two balance() calls in the poll, but only ONE underlying KIS GET.
    assert client.get.call_count == 1
    assert metrics["errors"] == 0
    assert metrics["checked"] == 0  # no holdings to iterate


def test_balance_failure_still_skips_poll_safely():
    """REQ-043-B4: a genuine balance failure degrades safely (skip poll)."""
    fresh_cache = BalanceCache(ttl=2.0)
    client = _client()
    client.get.side_effect = RuntimeError("초당 거래건수 초과")
    with (
        patch.object(account_mod, "_CACHE", fresh_cache),
        patch.object(wd, "_build_client", return_value=client),
    ):
        metrics = wd.poll_position_watchdog()

    assert metrics["errors"] == 1
    assert metrics["checked"] == 0
    assert metrics["stop_exits"] == 0
