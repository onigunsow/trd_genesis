"""Tests for JIT merged state cache."""

from __future__ import annotations

import time

from trading.jit.cache import MergedStateCache


class TestMergedStateCache:
    """Test TTL cache behavior."""

    def test_put_and_get(self):
        cache = MergedStateCache(ttl=10)
        cache.put("key1", {"data": "value"})
        assert cache.get("key1") == {"data": "value"}

    def test_get_missing_key(self):
        cache = MergedStateCache(ttl=10)
        assert cache.get("nonexistent") is None

    def test_ttl_expiration(self):
        cache = MergedStateCache(ttl=0.05)  # 50ms TTL
        cache.put("key1", "value")
        assert cache.get("key1") == "value"
        time.sleep(0.06)
        assert cache.get("key1") is None

    def test_invalidate(self):
        cache = MergedStateCache(ttl=10)
        cache.put("key1", "value")
        cache.invalidate("key1")
        assert cache.get("key1") is None

    def test_invalidate_all(self):
        cache = MergedStateCache(ttl=10)
        cache.put("key1", "val1")
        cache.put("key2", "val2")
        cache.invalidate_all()
        assert cache.get("key1") is None
        assert cache.get("key2") is None

    def test_put_after_invalidate(self):
        cache = MergedStateCache(ttl=10)
        cache.put("key1", "old")
        cache.invalidate("key1")
        cache.put("key1", "new")
        assert cache.get("key1") == "new"

    def test_clear(self):
        cache = MergedStateCache(ttl=10)
        cache.put("key1", "val1")
        cache.clear()
        assert cache.get("key1") is None

    def test_stats(self):
        cache = MergedStateCache(ttl=10)
        cache.put("key1", "val1")
        cache.put("key2", "val2")
        cache.invalidate("key2")
        stats = cache.stats()
        assert stats["total_entries"] == 2
        assert stats["valid_entries"] == 1
        assert stats["invalidated"] == 1
        assert stats["ttl_seconds"] == 10
