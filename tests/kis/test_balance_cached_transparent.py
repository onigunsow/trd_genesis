"""SPEC-TRADING-043 REQ-043-B2/B4 — transparent balance caching at account.balance().

``account.balance()`` is the single seam 20+ callers already use. We make the
read-through cache transparent inside it (no caller edits), keyed by mode/account,
with a ``force_fresh=True`` bypass. The reconcile-after-fill path
(``fills.reconcile_from_balance``) uses ``force_fresh=True`` so post-fill
reconciliation never reads stale holdings.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import trading.kis.account as account_mod
from trading.kis.account import balance
from trading.kis.balance_cache import BalanceCache
from trading.kis.client import KisResponse


def _balance_response(total: int) -> KisResponse:
    raw: dict[str, Any] = {
        "rt_cd": "0", "msg_cd": "", "msg1": "",
        "output1": [],
        "output2": [{
            "dnca_tot_amt": "1000", "scts_evlu_amt": "0", "tot_evlu_amt": str(total),
            "nxdy_excc_amt": "1000", "nrcvb_buy_amt": "0", "evlu_pfls_smtl_amt": "0",
        }],
    }
    return KisResponse(status_code=200, rt_cd="0", msg_cd="", msg="OK",
                       output=[], raw=raw)


def _client() -> MagicMock:
    c = MagicMock()
    c.mode = account_mod.__dict__.get("TradingMode", MagicMock())
    c.account_prefix = "12345678"
    c.account_suffix = "01"
    c.tr_id = MagicMock(side_effect=lambda paper_id, live_id: paper_id)
    c.get.return_value = _balance_response(9_000_000)
    c._account_full = "12345678-01"
    return c


def test_three_balance_calls_within_ttl_hit_kis_once():
    """REQ-043-B2: 3 transparent balance() reads in a window → 1 client.get."""
    fresh_cache = BalanceCache(ttl=2.0)
    client = _client()
    with patch.object(account_mod, "_CACHE", fresh_cache):
        balance(client)
        balance(client)
        balance(client)
    assert client.get.call_count == 1


def test_force_fresh_bypasses_cache():
    """force_fresh re-hits KIS even with a warm cache entry."""
    fresh_cache = BalanceCache(ttl=2.0)
    client = _client()
    with patch.object(account_mod, "_CACHE", fresh_cache):
        balance(client)                      # warms cache (1 get)
        balance(client, force_fresh=True)    # bypass (2nd get)
    assert client.get.call_count == 2


def test_paper_and_live_keys_isolated():
    """Distinct accounts must not share a cached balance."""
    fresh_cache = BalanceCache(ttl=2.0)
    with patch.object(account_mod, "_CACHE", fresh_cache):
        c1 = _client()
        c1._account_full = "11111111-01"
        c2 = _client()
        c2._account_full = "22222222-01"
        balance(c1)
        balance(c2)
    assert c1.get.call_count == 1
    assert c2.get.call_count == 1


def test_reconcile_after_fill_forces_fresh():
    """REQ-043-B2: fills.reconcile_from_balance reads with force_fresh=True so a
    warm (stale) cache entry never masks freshly-filled holdings."""
    import trading.kis.fills as fills_mod

    captured = {}

    def fake_balance(client, *, force_fresh=False):
        captured["force_fresh"] = force_fresh
        return {"holdings": []}

    with (
        patch.object(fills_mod, "balance", fake_balance),
        patch.object(fills_mod, "connection") as conn,
    ):
        conn.return_value.__enter__.return_value = MagicMock()
        fills_mod.reconcile_from_balance(MagicMock(), dry_run=True)

    assert captured["force_fresh"] is True
