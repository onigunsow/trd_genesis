"""SPEC-TRADING-043 REQ-043-B2 — read-through balance cache.

A small, process-wide, read-through cache for ``inquire-balance`` reads. Several
independent jobs (reconcile/fill_sync, position_watchdog, tools.executor) poll
the KIS balance within a short window; without coordination they each issue a
duplicate ``inquire-balance`` call and help breach the broker per-second cap.

This cache collapses those into a single underlying fetch within a short TTL,
keyed by trading mode/account so paper and live never share a value. The clock
is injectable for deterministic tests. A ``force_fresh`` read bypasses the cache
(used on the reconcile-after-fill path so post-fill reconciliation never reads
stale holdings).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Few-second TTL: long enough to collapse a burst of concurrent pollers, short
# enough that no exit decision is ever served stale holdings.
DEFAULT_BALANCE_TTL_SECONDS = 2.0


@dataclass
class _Entry:
    value: Any
    ts: float


class BalanceCache:
    """Thread-safe read-through cache with a short TTL and an injectable clock."""

    def __init__(
        self,
        ttl: float = DEFAULT_BALANCE_TTL_SECONDS,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl
        self._now = now
        self._lock = threading.Lock()
        self._store: dict[str, _Entry] = {}

    def get_or_fetch(
        self,
        key: str,
        fetch: Callable[[], Any],
        *,
        force_fresh: bool = False,
    ) -> Any:
        """Return a cached value for ``key`` if fresh, else call ``fetch``.

        ``force_fresh=True`` ignores any cached value, fetches, and refreshes the
        cache so subsequent reads within TTL see the fresh value.

        The lock is held across ``fetch()`` on purpose: that is what collapses a
        burst of concurrent pollers into a single underlying read (the
        thundering-herd dedup REQ-043-B2 targets). All keys therefore serialize
        during a fetch — acceptable because a process serves a single trading
        mode (one key in practice).
        """
        with self._lock:
            now = self._now()
            if not force_fresh:
                entry = self._store.get(key)
                if entry is not None and (now - entry.ts) < self._ttl:
                    return entry.value
            value = fetch()
            self._store[key] = _Entry(value=value, ts=self._now())
            return value


# Process-wide singleton shared by ``account.balance()``.
_CACHE = BalanceCache()
