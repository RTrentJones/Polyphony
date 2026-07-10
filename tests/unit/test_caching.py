"""Unit tests for in-process caching utilities (was Redis; ADR-001 §4)."""

import pytest

from app.core.caching import CacheClient, cache_result, get_cached_or_compute


@pytest.fixture
def cache():
    return CacheClient(namespace="test")


@pytest.mark.unit
class TestCacheClient:
    @pytest.mark.asyncio
    async def test_set_and_get(self, cache):
        assert await cache.set("key", {"a": 1})
        assert await cache.get("key") == {"a": 1}

    @pytest.mark.asyncio
    async def test_get_miss(self, cache):
        assert await cache.get("missing") is None

    @pytest.mark.asyncio
    async def test_delete(self, cache):
        await cache.set("key", "value")
        assert await cache.delete("key") is True
        assert await cache.get("key") is None
        assert await cache.delete("key") is False

    @pytest.mark.asyncio
    async def test_exists(self, cache):
        await cache.set("key", "value")
        assert await cache.exists("key") is True
        assert await cache.exists("other") is False

    @pytest.mark.asyncio
    async def test_namespace_isolation(self):
        a = CacheClient(namespace="a")
        b = CacheClient(namespace="b")
        await a.set("key", "from-a")
        assert await b.get("key") is None

    @pytest.mark.asyncio
    async def test_ttl_expiry(self, cache, monkeypatch):
        import app.core.caching as caching_module

        base = 1000.0
        monkeypatch.setattr(caching_module.time, "monotonic", lambda: base)
        await cache.set("key", "value", ttl=10)
        assert await cache.get("key") == "value"
        monkeypatch.setattr(caching_module.time, "monotonic", lambda: base + 11)
        assert await cache.get("key") is None

    @pytest.mark.asyncio
    async def test_clear_pattern(self, cache):
        await cache.set("user:1", "a")
        await cache.set("user:2", "b")
        await cache.set("scene:1", "c")
        deleted = await cache.clear_pattern("user:*")
        assert deleted == 2
        assert await cache.get("scene:1") == "c"

    @pytest.mark.asyncio
    async def test_increment(self, cache):
        assert await cache.increment("counter") == 1
        assert await cache.increment("counter", 5) == 6

    @pytest.mark.asyncio
    async def test_set_if_not_exists(self, cache):
        assert await cache.set_if_not_exists("key", "first") is True
        assert await cache.set_if_not_exists("key", "second") is False
        assert await cache.get("key") == "first"

    @pytest.mark.asyncio
    async def test_non_serializable_rejected(self, cache):
        assert await cache.set("key", object()) is False


@pytest.mark.unit
class TestCacheDecorator:
    @pytest.mark.asyncio
    async def test_cache_result_caches(self, cache):
        calls = {"n": 0}

        @cache_result(ttl=60)
        async def compute(x: int):
            calls["n"] += 1
            return x * 2

        assert await compute(2, cache_client=cache) == 4
        assert await compute(2, cache_client=cache) == 4
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_no_client_skips_cache(self):
        calls = {"n": 0}

        @cache_result(ttl=60)
        async def compute(x: int):
            calls["n"] += 1
            return x

        await compute(1)
        await compute(1)
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_get_cached_or_compute(self, cache):
        async def compute():
            return "computed"

        assert await get_cached_or_compute(cache, "k", compute) == "computed"

        async def never():
            raise AssertionError("should have been cached")

        assert await get_cached_or_compute(cache, "k", never) == "computed"
