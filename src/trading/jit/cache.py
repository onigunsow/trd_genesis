"""In-memory TTL cache for merged state — avoids repeated DB queries.

REQ-MERGE-02-2: Cache with 10-second TTL, lazy invalidation.
REQ-MERGE-02-5: Cached read < 1ms, cold merge < 100ms.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

LOG = logging.getLogger(__name__)

# REQ-MERGE-02-2: Configurable TTL (default 10 seconds)
_CACHE_TTL: float = float(os.environ.get("JIT_CACHE_TTL_SECONDS", "10"))


class MergedStateCache:
    """Simple TTL-based cache for merged state dicts.

    Thread-safe for single-writer / multi-reader pattern (GIL-protected).
    Cache key: (snapshot_type, snapshot_id).
    """

    def __init__(self, ttl: float | None = None) -> None:
        self._ttl = ttl if ttl is not None else _CACHE_TTL
        self._store: dict[str, tuple[float, Any]] = {}
        self._invalidated: set[str] = set()

    @property
    def ttl(self) -> float:
        return self._ttl

    def get(self, key: str) -> Any | None:
        """Get cached value if valid (within TTL and not invalidated).

        Returns None on miss (expired, invalidated, or not present).
        """
        if key in self._invalidated:
            self._invalidated.discard(key)
            return None

        entry = self._store.get(key)
        if entry is None:
            return None

        ts, value = entry
        if (time.time() - ts) > self._ttl:
            del self._store[key]
            return None

        return value

    def put(self, key: str, value: Any) -> None:
        """Store a value with current timestamp."""
        self._store[key] = (time.time(), value)
        self._invalidated.discard(key)

    def invalidate(self, key: str) -> None:
        """Mark a key as invalidated (lazy invalidation on next read).

        REQ-MERGE-02-2: Invalidated on new delta event arrival.
        """
        self._invalidated.add(key)

    def invalidate_all(self) -> None:
        """Invalidate all cached entries."""
        self._invalidated.update(self._store.keys())

    def clear(self) -> None:
        """Remove all entries."""
        self._store.clear()
        self._invalidated.clear()

    def stats(self) -> dict[str, Any]:
        """Return cache statistics for observability."""
        now = time.time()
        valid = sum(
            1 for k, (ts, _) in self._store.items()
            if (now - ts) <= self._ttl and k not in self._invalidated
        )
        return {
            "total_entries": len(self._store),
            "valid_entries": valid,
            "invalidated": len(self._invalidated),
            "ttl_seconds": self._ttl,
        }


# Module-level singleton cache instance
_cache = MergedStateCache()


def get_cache() -> MergedStateCache:
    """Return the module-level cache singleton."""
    return _cache
