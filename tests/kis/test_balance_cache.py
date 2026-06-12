"""SPEC-TRADING-043 REQ-043-B2 — read-through balance cache.

Multiple callers (reconcile/fill_sync, position_watchdog, executor) that request
a balance read within a short window reuse a single read-through cached value
rather than issuing duplicate ``inquire-balance`` calls. TTL is small and the
clock is injectable; ``force_fresh`` bypasses the cache.
"""

from __future__ import annotations

from trading.kis.balance_cache import BalanceCache


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t


def test_three_callers_within_ttl_fetch_once():
    """REQ-043-B2: 3 reads within TTL → exactly 1 underlying fetch."""
    clk = FakeClock()
    cache = BalanceCache(ttl=2.0, now=clk.monotonic)
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return {"holdings": [], "n": calls["n"]}

    r1 = cache.get_or_fetch("paper:123", fetch)
    clk.t = 0.5
    r2 = cache.get_or_fetch("paper:123", fetch)
    clk.t = 1.9
    r3 = cache.get_or_fetch("paper:123", fetch)

    assert calls["n"] == 1
    assert r1 is r2 is r3


def test_fresh_read_after_ttl_expiry():
    clk = FakeClock()
    cache = BalanceCache(ttl=2.0, now=clk.monotonic)
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return {"n": calls["n"]}

    cache.get_or_fetch("k", fetch)
    clk.t = 2.0001  # past TTL
    cache.get_or_fetch("k", fetch)
    assert calls["n"] == 2


def test_distinct_keys_are_isolated():
    """Cache is keyed (by mode/account) so paper and live never share a value."""
    clk = FakeClock()
    cache = BalanceCache(ttl=2.0, now=clk.monotonic)
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return {"n": calls["n"]}

    cache.get_or_fetch("paper:123", fetch)
    cache.get_or_fetch("live:999", fetch)
    assert calls["n"] == 2


def test_force_fresh_bypasses_and_refreshes_cache():
    """force_fresh ignores any cached value, fetches, and updates the cache."""
    clk = FakeClock()
    cache = BalanceCache(ttl=2.0, now=clk.monotonic)
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return {"n": calls["n"]}

    cache.get_or_fetch("k", fetch)            # n=1, cached
    forced = cache.get_or_fetch("k", fetch, force_fresh=True)  # n=2, bypass
    assert calls["n"] == 2
    assert forced["n"] == 2
    # A subsequent normal read within TTL now sees the refreshed value (n=2).
    again = cache.get_or_fetch("k", fetch)
    assert calls["n"] == 2
    assert again["n"] == 2
