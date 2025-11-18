"""Unit tests for caching layer"""

import pytest
import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch
from services.shared.caching import CacheClient, cache_result


@pytest.mark.unit
class TestCacheClient:
    """Test Redis cache client"""

    @pytest.mark.asyncio
    async def test_cache_initialization(self):
        """Test cache client initialization"""
        mock_redis = AsyncMock()
        cache = CacheClient(mock_redis, namespace="test")

        assert cache.namespace == "test"
        assert cache.redis == mock_redis

    @pytest.mark.asyncio
    async def test_cache_set_and_get(self):
        """Test setting and getting cache values"""
        mock_redis = AsyncMock()
        cache = CacheClient(mock_redis, namespace="test")

        # Mock Redis responses
        mock_redis.setex = AsyncMock(return_value=True)
        mock_redis.get = AsyncMock(return_value=json.dumps({"key": "value"}))

        # Set value
        result = await cache.set("mykey", {"key": "value"}, ttl=3600)
        assert result is True

        # Get value
        value = await cache.get("mykey")
        assert value == {"key": "value"}

    @pytest.mark.asyncio
    async def test_cache_get_miss(self):
        """Test cache miss returns None"""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        cache = CacheClient(mock_redis, namespace="test")

        value = await cache.get("nonexistent")
        assert value is None

    @pytest.mark.asyncio
    async def test_cache_delete(self):
        """Test deleting cache entries"""
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock(return_value=1)

        cache = CacheClient(mock_redis, namespace="test")

        result = await cache.delete("mykey")
        assert result is True

        # Verify key was prefixed with namespace
        mock_redis.delete.assert_called_once()
        called_key = mock_redis.delete.call_args[0][0]
        assert called_key.startswith("test:")

    @pytest.mark.asyncio
    async def test_cache_namespace_isolation(self):
        """Test namespace isolation"""
        mock_redis = AsyncMock()
        cache1 = CacheClient(mock_redis, namespace="service1")
        cache2 = CacheClient(mock_redis, namespace="service2")

        mock_redis.setex = AsyncMock(return_value=True)

        await cache1.set("key", "value1")
        await cache2.set("key", "value2")

        # Should have been called with different prefixed keys
        calls = mock_redis.setex.call_args_list
        assert len(calls) == 2
        key1 = calls[0][0][0]
        key2 = calls[1][0][0]
        assert key1 != key2
        assert "service1" in key1
        assert "service2" in key2

    @pytest.mark.asyncio
    async def test_cache_ttl(self):
        """Test TTL is properly set"""
        mock_redis = AsyncMock()
        cache = CacheClient(mock_redis, namespace="test")

        mock_redis.setex = AsyncMock(return_value=True)

        await cache.set("key", "value", ttl=7200)

        # Verify TTL was passed correctly
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args[0]
        ttl_value = call_args[1]
        assert ttl_value == 7200

    @pytest.mark.asyncio
    async def test_cache_handles_complex_objects(self):
        """Test caching complex objects"""
        mock_redis = AsyncMock()
        cache = CacheClient(mock_redis, namespace="test")

        complex_obj = {
            "users": [
                {"id": 1, "name": "Alice"},
                {"id": 2, "name": "Bob"}
            ],
            "metadata": {
                "total": 2,
                "timestamp": "2025-01-01"
            }
        }

        mock_redis.setex = AsyncMock(return_value=True)
        mock_redis.get = AsyncMock(return_value=json.dumps(complex_obj))

        await cache.set("complex", complex_obj)
        result = await cache.get("complex")

        assert result == complex_obj
        assert len(result["users"]) == 2

    @pytest.mark.asyncio
    async def test_cache_error_handling(self):
        """Test cache gracefully handles errors"""
        mock_redis = AsyncMock()
        cache = CacheClient(mock_redis, namespace="test")

        # Redis error on set
        mock_redis.setex.side_effect = Exception("Redis connection failed")

        result = await cache.set("key", "value")
        assert result is False  # Should return False, not raise

        # Redis error on get
        mock_redis.get.side_effect = Exception("Redis connection failed")

        value = await cache.get("key")
        assert value is None  # Should return None, not raise

    @pytest.mark.asyncio
    async def test_cache_increment(self):
        """Test cache counter increment"""
        mock_redis = AsyncMock()
        cache = CacheClient(mock_redis, namespace="test")

        mock_redis.incr = AsyncMock(return_value=5)

        result = await cache.increment("counter")
        assert result == 5

    @pytest.mark.asyncio
    async def test_cache_decrement(self):
        """Test cache counter decrement"""
        mock_redis = AsyncMock()
        cache = CacheClient(mock_redis, namespace="test")

        mock_redis.decr = AsyncMock(return_value=3)

        result = await cache.decrement("counter")
        assert result == 3

    @pytest.mark.asyncio
    async def test_cache_exists(self):
        """Test checking if key exists"""
        mock_redis = AsyncMock()
        cache = CacheClient(mock_redis, namespace="test")

        mock_redis.exists = AsyncMock(return_value=1)

        exists = await cache.exists("mykey")
        assert exists is True

        mock_redis.exists = AsyncMock(return_value=0)
        exists = await cache.exists("otherkey")
        assert exists is False

    @pytest.mark.asyncio
    async def test_cache_clear_pattern(self):
        """Test clearing cache by pattern"""
        mock_redis = AsyncMock()
        cache = CacheClient(mock_redis, namespace="test")

        mock_redis.keys = AsyncMock(return_value=["test:user:1", "test:user:2"])
        mock_redis.delete = AsyncMock(return_value=2)

        deleted = await cache.clear_pattern("user:*")
        assert deleted == 2


@pytest.mark.unit
class TestCacheDecorator:
    """Test cache decorator"""

    @pytest.mark.asyncio
    async def test_cache_decorator_basic(self):
        """Test basic cache decorator usage"""
        mock_redis = AsyncMock()
        mock_cache = CacheClient(mock_redis, namespace="test")

        call_count = 0

        @cache_result(ttl=300, key_prefix="user")
        async def get_user(user_id: str, cache_client=None):
            nonlocal call_count
            call_count += 1
            return {"id": user_id, "name": "Test User"}

        # Mock cache miss then set
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock(return_value=True)

        # First call - cache miss, function executes
        result1 = await get_user("123", cache_client=mock_cache)
        assert result1["id"] == "123"
        assert call_count == 1

        # Mock cache hit
        mock_redis.get = AsyncMock(
            return_value=json.dumps({"id": "123", "name": "Test User"})
        )

        # Second call - cache hit, function not executed
        result2 = await get_user("123", cache_client=mock_cache)
        assert result2["id"] == "123"
        assert call_count == 1  # Still 1, not incremented

    @pytest.mark.asyncio
    async def test_cache_decorator_with_multiple_args(self):
        """Test cache decorator with multiple arguments"""
        mock_redis = AsyncMock()
        mock_cache = CacheClient(mock_redis, namespace="test")

        @cache_result(ttl=300, key_prefix="scene")
        async def get_scene(manuscript_id: str, scene_id: str, cache_client=None):
            return {"manuscript": manuscript_id, "scene": scene_id}

        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock(return_value=True)

        result = await get_scene("ms123", "sc456", cache_client=mock_cache)
        assert result["manuscript"] == "ms123"
        assert result["scene"] == "sc456"

    @pytest.mark.asyncio
    async def test_cache_decorator_without_cache_client(self):
        """Test decorator works without cache client (no-op)"""
        call_count = 0

        @cache_result(ttl=300, key_prefix="test")
        async def func_without_cache(arg):
            nonlocal call_count
            call_count += 1
            return arg

        # Should work without cache_client, just not cache
        result1 = await func_without_cache("value")
        result2 = await func_without_cache("value")

        assert result1 == "value"
        assert result2 == "value"
        assert call_count == 2  # Called both times (no caching)

    @pytest.mark.asyncio
    async def test_cache_decorator_ttl(self):
        """Test decorator respects TTL"""
        mock_redis = AsyncMock()
        mock_cache = CacheClient(mock_redis, namespace="test")

        @cache_result(ttl=1800, key_prefix="test")
        async def cached_func(arg, cache_client=None):
            return arg

        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock(return_value=True)

        await cached_func("value", cache_client=mock_cache)

        # Verify TTL was set correctly
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args[0]
        ttl = call_args[1]
        assert ttl == 1800


@pytest.mark.unit
class TestCacheIntegration:
    """Test cache integration scenarios"""

    @pytest.mark.asyncio
    async def test_cache_aside_pattern(self):
        """Test cache-aside pattern implementation"""
        mock_redis = AsyncMock()
        cache = CacheClient(mock_redis, namespace="test")

        async def fetch_from_db(key):
            # Simulate database fetch
            await asyncio.sleep(0.01)
            return {"data": f"value_for_{key}"}

        # Cache miss scenario
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock(return_value=True)

        cached_value = await cache.get("item:123")
        if cached_value is None:
            # Cache miss - fetch from DB
            db_value = await fetch_from_db("item:123")
            await cache.set("item:123", db_value, ttl=3600)
            value = db_value
        else:
            value = cached_value

        assert value["data"] == "value_for_item:123"

    @pytest.mark.asyncio
    async def test_cache_stampede_prevention(self):
        """Test preventing cache stampede with set_if_not_exists"""
        mock_redis = AsyncMock()
        cache = CacheClient(mock_redis, namespace="test")

        # Simulate multiple concurrent requests
        mock_redis.exists = AsyncMock(side_effect=[0, 1, 1])  # First missing, then exists
        mock_redis.setex = AsyncMock(return_value=True)
        mock_redis.get = AsyncMock(return_value=json.dumps({"data": "cached"}))

        call_count = 0

        async def expensive_operation():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.1)
            return {"data": "computed"}

        # First request - sets cache
        if not await cache.exists("expensive:key"):
            result = await expensive_operation()
            await cache.set("expensive:key", result)

        # Concurrent requests - use cached value
        for _ in range(5):
            if not await cache.exists("expensive:key"):
                await expensive_operation()
            else:
                await cache.get("expensive:key")

        # Should only compute once
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_cache_invalidation(self):
        """Test cache invalidation strategies"""
        mock_redis = AsyncMock()
        cache = CacheClient(mock_redis, namespace="test")

        mock_redis.delete = AsyncMock(return_value=1)

        # Invalidate single key
        await cache.delete("user:123")

        # Invalidate by pattern
        mock_redis.keys = AsyncMock(return_value=["test:scene:*"])
        mock_redis.delete = AsyncMock(return_value=5)

        deleted = await cache.clear_pattern("scene:*")
        assert deleted == 5

    @pytest.mark.asyncio
    async def test_multi_level_caching(self):
        """Test multi-level caching strategy"""
        # L1: In-memory cache
        l1_cache = {}

        # L2: Redis cache
        mock_redis = AsyncMock()
        l2_cache = CacheClient(mock_redis, namespace="test")

        async def get_with_multilevel_cache(key):
            # Check L1
            if key in l1_cache:
                return l1_cache[key]

            # Check L2
            mock_redis.get = AsyncMock(
                return_value=json.dumps({"data": "from_redis"})
            )
            value = await l2_cache.get(key)

            if value:
                # Populate L1
                l1_cache[key] = value
                return value

            # Cache miss - fetch from source
            value = {"data": "from_source"}
            l1_cache[key] = value
            await l2_cache.set(key, value)
            return value

        result = await get_with_multilevel_cache("test:key")
        assert result is not None
