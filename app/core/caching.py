"""
In-process caching utilities.

Was a Redis-backed cache client; one consolidated container makes in-process
state correct (docs/ADR-001 §4), so this is the same CacheClient surface over
cachetools.TTLCache. Async methods are kept so call sites didn't change.
"""

import fnmatch
import hashlib
import json
import time
from functools import wraps
from typing import Any, Callable, Optional

from cachetools import TTLCache


class CacheClient:
    """TTL cache with the old Redis-client convenience surface."""

    def __init__(self, namespace: str = "polyphony", maxsize: int = 4096):
        self.namespace = namespace
        # Per-entry TTLs: store (expires_at, value); the outer TTL is a backstop.
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=24 * 3600)

    def _make_key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    async def get(self, key: str) -> Optional[Any]:
        entry = self._cache.get(self._make_key(key))
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at is not None and time.monotonic() >= expires_at:
            self._cache.pop(self._make_key(key), None)
            return None
        return value

    async def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        try:
            json.dumps(value)  # keep the old JSON-serializable contract
        except (TypeError, ValueError):
            return False
        expires_at = time.monotonic() + ttl if ttl else None
        self._cache[self._make_key(key)] = (expires_at, value)
        return True

    async def delete(self, key: str) -> bool:
        return self._cache.pop(self._make_key(key), None) is not None

    async def exists(self, key: str) -> bool:
        return await self.get(key) is not None

    async def clear_pattern(self, pattern: str) -> int:
        """Delete all keys matching a glob pattern (e.g. "user:*")."""
        full_pattern = self._make_key(pattern)
        matches = [
            k for k in list(self._cache.keys()) if fnmatch.fnmatch(k, full_pattern)
        ]
        for k in matches:
            self._cache.pop(k, None)
        return len(matches)

    async def increment(self, key: str, amount: int = 1) -> Optional[int]:
        current = await self.get(key)
        new_value = (int(current) if current is not None else 0) + amount
        await self.set(key, new_value)
        return new_value

    async def set_if_not_exists(self, key: str, value: Any, ttl: int = 3600) -> bool:
        if await self.exists(key):
            return False
        return await self.set(key, value, ttl=ttl)


def cache_result(
    ttl: int = 3600, key_prefix: str = "", skip_cache_on_error: bool = True
):
    """
    Decorator to cache function results

    Example:
        @cache_result(ttl=300, key_prefix="user")
        async def get_user(user_id: str):
            return await db.get_user(user_id)
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, cache_client: Optional[CacheClient] = None, **kwargs):
            if not cache_client:
                return await func(*args, **kwargs)

            prefix = key_prefix or func.__name__
            args_str = json.dumps({"args": args, "kwargs": kwargs}, sort_keys=True)
            args_hash = hashlib.md5(
                args_str.encode(), usedforsecurity=False
            ).hexdigest()  # nosec B324
            cache_key = f"{prefix}:{args_hash}"

            cached_value = await cache_client.get(cache_key)
            if cached_value is not None:
                return cached_value

            result = await func(*args, **kwargs)
            await cache_client.set(cache_key, result, ttl=ttl)
            return result

        return wrapper

    return decorator


async def get_cached_or_compute(
    cache_client: Optional[CacheClient],
    key: str,
    compute_func: Callable,
    ttl: int = 3600,
    *args,
    **kwargs,
) -> Any:
    """Cache-aside convenience: get from cache or compute and store."""
    if not cache_client:
        return await compute_func(*args, **kwargs)

    cached_value = await cache_client.get(key)
    if cached_value is not None:
        return cached_value

    value = await compute_func(*args, **kwargs)
    await cache_client.set(key, value, ttl=ttl)
    return value
