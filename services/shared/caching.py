"""
Caching utilities for performance optimization (P2-2 fix)

Provides Redis-backed caching with TTL, namespacing, and serialization.
"""

import json
import hashlib
from typing import Any, Optional, Callable
from functools import wraps
import redis.asyncio as redis


class CacheClient:
    """
    Redis cache client with convenience methods

    Handles serialization, TTL, and key namespacing automatically.
    """

    def __init__(self, redis_client: redis.Redis, namespace: str = "polyphony"):
        """
        Args:
            redis_client: Async Redis client
            namespace: Key namespace prefix (default: "polyphony")
        """
        self.redis = redis_client
        self.namespace = namespace

    def _make_key(self, key: str) -> str:
        """Create namespaced cache key"""
        return f"{self.namespace}:{key}"

    async def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found
        """
        try:
            value = await self.redis.get(self._make_key(key))
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            # Cache failures shouldn't break application
            print(f"Cache get error: {e}")
            return None

    async def set(
        self,
        key: str,
        value: Any,
        ttl: int = 3600
    ) -> bool:
        """
        Set value in cache with TTL

        Args:
            key: Cache key
            value: Value to cache (must be JSON serializable)
            ttl: Time-to-live in seconds (default: 1 hour)

        Returns:
            True if successful, False otherwise
        """
        try:
            serialized = json.dumps(value)
            await self.redis.setex(
                self._make_key(key),
                ttl,
                serialized
            )
            return True
        except Exception as e:
            print(f"Cache set error: {e}")
            return False

    async def delete(self, key: str) -> bool:
        """
        Delete key from cache

        Args:
            key: Cache key

        Returns:
            True if key was deleted, False otherwise
        """
        try:
            await self.redis.delete(self._make_key(key))
            return True
        except Exception as e:
            print(f"Cache delete error: {e}")
            return False

    async def exists(self, key: str) -> bool:
        """
        Check if key exists in cache

        Args:
            key: Cache key

        Returns:
            True if key exists, False otherwise
        """
        try:
            return await self.redis.exists(self._make_key(key)) > 0
        except Exception as e:
            print(f"Cache exists error: {e}")
            return False

    async def clear_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching pattern

        Args:
            pattern: Redis pattern (e.g., "user:*")

        Returns:
            Number of keys deleted
        """
        try:
            keys = await self.redis.keys(self._make_key(pattern))
            if keys:
                return await self.redis.delete(*keys)
            return 0
        except Exception as e:
            print(f"Cache clear_pattern error: {e}")
            return 0

    async def increment(self, key: str, amount: int = 1) -> Optional[int]:
        """
        Increment counter value

        Args:
            key: Cache key
            amount: Amount to increment by (default: 1)

        Returns:
            New value or None on error
        """
        try:
            return await self.redis.incrby(self._make_key(key), amount)
        except Exception as e:
            print(f"Cache increment error: {e}")
            return None

    async def set_if_not_exists(
        self,
        key: str,
        value: Any,
        ttl: int = 3600
    ) -> bool:
        """
        Set value only if key doesn't exist

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds

        Returns:
            True if value was set, False if key already existed
        """
        try:
            serialized = json.dumps(value)
            result = await self.redis.set(
                self._make_key(key),
                serialized,
                ex=ttl,
                nx=True  # Only set if not exists
            )
            return result is not None
        except Exception as e:
            print(f"Cache set_if_not_exists error: {e}")
            return False


def cache_result(
    ttl: int = 3600,
    key_prefix: str = "",
    skip_cache_on_error: bool = True
):
    """
    Decorator to cache function results

    Args:
        ttl: Cache TTL in seconds (default: 1 hour)
        key_prefix: Prefix for cache key (default: function name)
        skip_cache_on_error: If True, don't cache if function raises exception

    Example:
        @cache_result(ttl=300, key_prefix="user")
        async def get_user(user_id: str):
            return await db.get_user(user_id)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, cache_client: Optional[CacheClient] = None, **kwargs):
            # If no cache client provided, just call function
            if not cache_client:
                return await func(*args, **kwargs)

            # Generate cache key from function name and arguments
            prefix = key_prefix or func.__name__
            args_str = json.dumps({"args": args, "kwargs": kwargs}, sort_keys=True)
            args_hash = hashlib.md5(args_str.encode()).hexdigest()
            cache_key = f"{prefix}:{args_hash}"

            # Try to get from cache
            cached_value = await cache_client.get(cache_key)
            if cached_value is not None:
                return cached_value

            # Call function
            try:
                result = await func(*args, **kwargs)

                # Cache the result
                await cache_client.set(cache_key, result, ttl=ttl)

                return result
            except Exception as e:
                # Don't cache errors unless explicitly configured
                if not skip_cache_on_error:
                    raise
                raise

        return wrapper
    return decorator


async def get_cached_or_compute(
    cache_client: Optional[CacheClient],
    key: str,
    compute_func: Callable,
    ttl: int = 3600,
    *args,
    **kwargs
) -> Any:
    """
    Get value from cache or compute it

    Convenience function for cache-aside pattern.

    Args:
        cache_client: Cache client (None to skip caching)
        key: Cache key
        compute_func: Function to call if cache miss
        ttl: Cache TTL in seconds
        *args, **kwargs: Arguments for compute_func

    Returns:
        Cached or computed value
    """
    # If no cache client, just compute
    if not cache_client:
        return await compute_func(*args, **kwargs)

    # Try cache
    cached_value = await cache_client.get(key)
    if cached_value is not None:
        return cached_value

    # Cache miss - compute value
    value = await compute_func(*args, **kwargs)

    # Store in cache
    await cache_client.set(key, value, ttl=ttl)

    return value
