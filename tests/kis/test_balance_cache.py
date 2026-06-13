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


def test_fetch_failure_evicts_stale_entry():
    """REQ-043-B2: fetch 실패 시 stale 항목이 캐시에서 제거되어야 한다.

    재현 순서:
    1. 정상 fetch로 캐시를 초기화 (n=1 저장).
    2. TTL 만료 후 실패하는 fetch 호출 → 예외가 전파돼야 한다.
    3. 클록을 최초 항목의 TTL 내로 되돌린 뒤 정상 fetch 호출.
       - 수정 전: stale entry가 _store에 남아 있고, 클록 기준으로는 TTL 내에 있기
         때문에 cached된 낡은 값(n=1)이 반환된다 → 테스트 실패.
       - 수정 후: 실패 시 evict 되므로 신규 fetch가 발생해 n=2가 반환된다.
    """
    clk = FakeClock()
    cache = BalanceCache(ttl=2.0, now=clk.monotonic)
    calls = {"n": 0}

    def good_fetch():
        calls["n"] += 1
        return {"n": calls["n"]}

    def bad_fetch():
        raise RuntimeError("KIS 오류 — 잔고 조회 실패")

    # step 1: t=0 에 정상 캐싱 (entry.ts = 0)
    result = cache.get_or_fetch("k", good_fetch)
    assert result["n"] == 1

    # step 2: t=2.1(TTL 만료)에서 fetch 실패 → 예외 전파
    clk.t = 2.1
    import pytest as _pytest
    with _pytest.raises(RuntimeError, match="잔고 조회 실패"):
        cache.get_or_fetch("k", bad_fetch)

    # step 3: 클록을 실패 직후(t=2.1) 기준으로 TTL 내인 t=3.0 으로 이동.
    # 실패한 bad_fetch 는 _store[key]를 갱신하지 않았으므로:
    #   - 수정 전: 원래 entry(ts=0)가 _store에 그대로 있고, t=3.0 - ts=0 = 3.0 > ttl=2.0
    #             → TTL 만료이므로 새 fetch가 일어남 (이 경로는 버그를 숨김)
    # 실제 버그 시나리오: 실패 직후 t=2.1 에서 바로 호출 (TTL 아직 안 만료가 아닌 상태).
    # t=2.1에서 실패 후 entry(ts=0)는 이미 만료였으므로 그대로 두면 다음 호출도 만료.
    # 버그를 실제로 드러내려면 "실패 후 stale entry의 ts 가 갱신되는" 가상 구현을
    # 테스트해야 한다. 여기서는 직접 _store를 조작해 ts 를 현재로 갱신한 뒤 검증한다.
    # (이 조작은 실제 코드의 버그 없이 동일 효과를 모사)
    # 대신 다음 시나리오를 테스트: force_fresh=False + TTL 만료 없는 상황에서 실패.
    # 즉 TTL 만료 전에 force_fresh=True 로 실패하면 stale이 남는지 확인.
    clk.t = 0.0  # 시간 리셋
    cache2 = BalanceCache(ttl=10.0, now=clk.monotonic)
    calls2 = {"n": 0}

    def good_fetch2():
        calls2["n"] += 1
        return {"n": calls2["n"]}

    def bad_fetch2():
        raise RuntimeError("KIS 오류")

    # TTL=10 내에 정상 캐싱
    cache2.get_or_fetch("k2", good_fetch2)  # n=1 캐싱
    assert calls2["n"] == 1

    # force_fresh=True 로 fetch 실패 → stale(n=1) 이 evict 되어야 함
    clk.t = 0.5  # TTL 내
    with _pytest.raises(RuntimeError, match="KIS 오류"):
        cache2.get_or_fetch("k2", bad_fetch2, force_fresh=True)

    # 수정 전: stale entry(n=1)가 캐시에 남아 있으므로 TTL(10s) 내에 정상 fetch 호출 시
    #          신규 fetch 없이 n=1이 반환됨 → assert 실패.
    # 수정 후: evict 되어 신규 fetch 발생 → n=2 반환.
    clk.t = 1.0  # 여전히 TTL(10s) 내
    result3 = cache2.get_or_fetch("k2", good_fetch2)
    assert result3["n"] == 2, (
        "force_fresh fetch 실패 후 stale 값이 캐시에 남아 있음 — evict 누락"
    )
    assert calls2["n"] == 2
