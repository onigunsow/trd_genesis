"""SPEC-TRADING-043 Group B — pacing-gate wiring into the KIS client.

REQ-043-B1: ``get()`` (inquiries) passes through the module pacing gate.
REQ-043-B5: ``post()`` (orders) is NOT gated — exit-order submission TRs are
            never delayed by the pacer; trading-mode/live_unlocked gates untouched.
REQ-043-B3: the reactive rate-limit retry remains beneath the pacer.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import trading.kis.client as client_mod
from trading.kis.client import KisClient


def _ok_json() -> dict[str, Any]:
    return {"rt_cd": "0", "msg_cd": "", "msg1": "OK", "output": {}}


def _rate_limited_json() -> dict[str, Any]:
    return {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수 초과", "output": {}}


def _make_client() -> KisClient:
    """Build a KisClient bypassing real settings/credentials."""
    c = KisClient.__new__(KisClient)
    c.mode = client_mod.TradingMode.PAPER
    c.base = "https://example.test"
    c._appkey = "k"
    c._appsecret = "s"
    c._account_full = "12345678-01"
    return c


def _patch_httpx(response_json):
    """Patch httpx.Client so .get/.post return a stubbed response."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = response_json  # a callable returning fresh dicts
    inst = MagicMock()
    inst.get.return_value = resp
    inst.post.return_value = resp
    ctx = MagicMock()
    ctx.__enter__.return_value = inst
    ctx.__exit__.return_value = False
    return patch.object(client_mod.httpx, "Client", return_value=ctx), inst


def test_get_calls_pacing_gate_once():
    c = _make_client()
    patcher, _ = _patch_httpx(lambda: _ok_json())
    with (
        patcher,
        patch.object(c, "_headers", return_value={}),
        patch.object(client_mod._GATE, "acquire") as gate,
    ):
        c.get("/x", tr_id="VTTC")
    gate.assert_called_once()


def test_post_is_not_gated():
    """REQ-043-B5: order submission must never be delayed by the pacer."""
    c = _make_client()
    patcher, _ = _patch_httpx(lambda: _ok_json())
    with (
        patcher,
        patch.object(c, "_headers", return_value={}),
        patch.object(client_mod._GATE, "acquire") as gate,
    ):
        c.post("/order", tr_id="VTTC", body={"a": 1})
    gate.assert_not_called()


def test_reactive_retry_still_present_beneath_pacer():
    """REQ-043-B3: a rate-limited GET still retries (reactive backoff) under the
    pacer, then succeeds — proving the residual-burst safety net is intact."""
    c = _make_client()
    # First response rate-limited, second OK.
    responses = [_rate_limited_json(), _ok_json()]

    def next_json() -> dict[str, Any]:
        return responses.pop(0)

    patcher, inst = _patch_httpx(next_json)
    with (
        patcher,
        patch.object(c, "_headers", return_value={}),
        patch.object(client_mod._GATE, "acquire"),
        # SPEC-051 REQ-051-A5: backoff sleep은 _sleep_fn seam을 통해 수행됨
        patch.object(client_mod, "_sleep_fn") as backoff_sleep,
    ):
        resp = c.get("/x", tr_id="VTTC")

    assert resp.rt_cd == "0"          # eventually succeeds
    assert inst.get.call_count == 2   # one retry occurred
    backoff_sleep.assert_called()     # reactive backoff fired
