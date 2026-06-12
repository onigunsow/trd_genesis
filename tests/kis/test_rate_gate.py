"""SPEC-TRADING-043 Group B — proactive KIS TPS pacing gate (REQ-043-B1/B6).

A process-wide minimum-interval gate serializes concurrent KIS GET callers
beneath the broker per-second cap. The gate accepts an injectable clock + sleep
so it is deterministically testable with a fake clock and a sleep-counter — no
wall-clock, no live broker.
"""

from __future__ import annotations

from trading.kis.client import KIS_MIN_REQUEST_INTERVAL_SECONDS, _RateGate


class FakeClock:
    """Monotonic fake clock; ``sleep`` advances time and counts calls."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, d: float) -> None:
        self.sleeps.append(d)
        self.t += d


def test_first_acquire_does_not_wait():
    """Baseline: a fresh gate grants the first request immediately."""
    clk = FakeClock()
    gate = _RateGate(0.4, now=clk.monotonic, sleep=clk.sleep)
    gate.acquire()
    assert clk.sleeps == []
    assert clk.t == 0.0


def test_n_back_to_back_callers_are_paced_to_cap():
    """REQ-043-B1: without pacing N immediate callers exceed the cap; the gate
    forces an aggregate rate <= cap (1 / min_interval)."""
    min_interval = 0.4
    clk = FakeClock()
    gate = _RateGate(min_interval, now=clk.monotonic, sleep=clk.sleep)

    n = 5
    for _ in range(n):
        gate.acquire()

    # N requests serialized at >= min_interval spacing → total simulated elapsed
    # is at least (N-1) * min_interval. Equivalent: effective rate <= cap.
    assert clk.t >= (n - 1) * min_interval - 1e-9
    # Exactly (N-1) waits (the first is free).
    assert len(clk.sleeps) == n - 1
    for s in clk.sleeps:
        assert abs(s - min_interval) < 1e-9


def test_no_wait_when_caller_already_late():
    """If real time has already moved past the interval, no sleep is incurred."""
    min_interval = 0.4
    clk = FakeClock()
    gate = _RateGate(min_interval, now=clk.monotonic, sleep=clk.sleep)
    gate.acquire()           # grant at t=0
    clk.t = 10.0             # plenty of idle time elapsed
    gate.acquire()           # should not wait
    assert clk.sleeps == []


def test_default_interval_constant_is_sane():
    """REQ-043-B1: the default aggregate cap is ~2.5 req/s (0.4s interval)."""
    assert KIS_MIN_REQUEST_INTERVAL_SECONDS == 0.4
