"""
Health check utilities for production readiness (P1-3 fix)

Provides comprehensive health checks for liveness and readiness probes
used by Kubernetes and load balancers.
"""

from enum import Enum
from typing import Dict, Callable
from datetime import datetime
import asyncio


class HealthStatus(str, Enum):
    """Health check status values"""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class HealthCheck:
    """
    Health check manager for microservices

    Supports both liveness (is service running?) and readiness (can accept traffic?)
    probes for Kubernetes deployments.
    """

    def __init__(self, service_name: str, version: str = "1.0.0"):
        self.service_name = service_name
        self.version = version
        self.startup_time = datetime.utcnow()
        self.checks: Dict[str, Callable] = {}

    def register_check(self, name: str, check_func: Callable):
        """
        Register a health check function

        Args:
            name: Name of the dependency (e.g., "database", "redis")
            check_func: Async function that returns True if healthy, False otherwise
        """
        self.checks[name] = check_func

    def add_check(self, name: str, check_func: Callable):
        """
        Alias for register_check for compatibility with tests

        Args:
            name: Name of the dependency (e.g., "database", "redis")
            check_func: Async function that returns True if healthy, False otherwise
        """
        self.register_check(name, check_func)

    async def liveness(self) -> dict:
        """
        Liveness probe - is the service running?

        This should only fail if the service is completely broken and needs restart.
        Returns 200 if service is alive, 503 if it should be killed.
        """
        return {
            "status": HealthStatus.HEALTHY,
            "service": self.service_name,
            "version": self.version,
            "timestamp": datetime.utcnow().isoformat(),
            "uptime_seconds": (datetime.utcnow() - self.startup_time).total_seconds(),
        }

    async def readiness(self) -> tuple[dict, int]:
        """
        Readiness probe - can the service accept traffic?

        Checks all registered dependencies. If any critical dependency is down,
        returns 503 to stop receiving traffic.

        Returns:
            Tuple of (health_data dict, http_status_code int)
        """
        check_results = {}
        all_healthy = True
        any_unhealthy = False

        # Run all health checks concurrently
        check_tasks = {name: check_func() for name, check_func in self.checks.items()}

        results = await asyncio.gather(*check_tasks.values(), return_exceptions=True)

        for (name, _), result in zip(check_tasks.items(), results):
            if isinstance(result, Exception):
                check_results[name] = {
                    "status": HealthStatus.UNHEALTHY,
                    "error": str(result),
                }
                any_unhealthy = True
                all_healthy = False
            elif result:
                check_results[name] = {"status": HealthStatus.HEALTHY}
            else:
                check_results[name] = {"status": HealthStatus.UNHEALTHY}
                any_unhealthy = True
                all_healthy = False

        # Determine overall status
        if all_healthy:
            overall_status = HealthStatus.HEALTHY
            status_code = 200
        elif any_unhealthy:
            overall_status = HealthStatus.UNHEALTHY
            status_code = 503
        else:
            overall_status = HealthStatus.DEGRADED
            status_code = 200  # Still serve traffic but degraded

        health_data = {
            "status": overall_status,
            "service": self.service_name,
            "version": self.version,
            "timestamp": datetime.utcnow().isoformat(),
            "checks": check_results,
        }

        return health_data, status_code

    async def startup(self) -> tuple[dict, int]:
        """
        Startup probe - has the service finished starting up?

        Some services need time to warm up (load caches, etc.).
        This probe tells Kubernetes when the service is ready.
        """
        # For now, same as readiness
        # Can be extended for services that need warm-up time
        return await self.readiness()


# Helper functions for common health checks


async def check_database(check_db_func: Callable) -> bool:
    """
    Check database connection

    Args:
        check_db_func: Function that checks database connectivity

    Returns:
        True if database is healthy, False otherwise
    """
    try:
        return await check_db_func()
    except Exception:
        return False


async def check_redis(redis_client) -> bool:
    """
    Check Redis connection

    Args:
        redis_client: Redis client instance

    Returns:
        True if Redis is healthy, False otherwise
    """
    try:
        if redis_client:
            await redis_client.ping()
            return True
        return False
    except Exception:
        return False


async def check_http_endpoint(url: str, timeout: float = 5.0) -> bool:
    """
    Check if HTTP endpoint is reachable

    Args:
        url: URL to check
        timeout: Request timeout in seconds

    Returns:
        True if endpoint returns 200, False otherwise
    """
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            return response.status_code == 200
    except Exception:
        return False


# Additional helper functions expected by tests


async def check_database_health(session) -> bool:
    """
    Check database health by executing a simple query

    Args:
        session: Database session

    Returns:
        True if database is healthy, False otherwise
    """
    try:
        # Execute a simple query to verify connection
        from sqlalchemy import text

        await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def check_cache_health(redis_client) -> bool:
    """
    Check cache (Redis) health

    Args:
        redis_client: Redis client instance

    Returns:
        True if cache is healthy, False otherwise
    """
    try:
        if redis_client:
            # Ping Redis to check connectivity
            result = await redis_client.ping()
            return result is True or result == b"PONG" or result == "PONG"
        return False
    except Exception:
        return False


async def check_external_service_health(url: str, client=None) -> bool:
    """
    Check external service health via HTTP

    Args:
        url: URL of the service health endpoint
        client: Optional HTTP client to use

    Returns:
        True if service is healthy, False otherwise
    """
    try:
        import httpx

        if client:
            # Use provided client
            response = await client.get(url, timeout=5.0)
        else:
            # Create new client
            async with httpx.AsyncClient(timeout=5.0) as http_client:
                response = await http_client.get(url)

        return 200 <= response.status_code < 300
    except Exception:
        return False
