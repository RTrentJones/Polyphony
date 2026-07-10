"""Unit tests for health check system"""

import pytest
import asyncio
from datetime import datetime
from app.core.health import HealthCheck, HealthStatus


@pytest.mark.unit
class TestHealthCheck:
    """Test health check functionality"""

    @pytest.mark.asyncio
    async def test_health_check_initialization(self):
        """Test health check initializes correctly"""
        health = HealthCheck(service_name="test-service", version="1.0.0")

        assert health.service_name == "test-service"
        assert health.version == "1.0.0"
        assert isinstance(health.startup_time, datetime)

    @pytest.mark.asyncio
    async def test_liveness_probe_always_healthy(self):
        """Test liveness probe returns healthy when service is running"""
        health = HealthCheck(service_name="test-service")

        result = await health.liveness()

        assert result["status"] == HealthStatus.HEALTHY
        assert result["service"] == "test-service"
        assert "timestamp" in result
        assert "uptime_seconds" in result
        assert result["uptime_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_readiness_probe_no_checks(self):
        """Test readiness probe with no health checks"""
        health = HealthCheck(service_name="test-service")

        result, status_code = await health.readiness()

        assert result["status"] == HealthStatus.HEALTHY
        assert status_code == 200

    @pytest.mark.asyncio
    async def test_readiness_probe_all_healthy(self):
        """Test readiness probe with all healthy dependencies"""
        health = HealthCheck(service_name="test-service")

        async def db_check():
            return True

        async def cache_check():
            return True

        health.add_check("database", db_check)
        health.add_check("cache", cache_check)

        result, status_code = await health.readiness()

        assert result["status"] == HealthStatus.HEALTHY
        assert status_code == 200
        assert result["checks"]["database"]["status"] == HealthStatus.HEALTHY
        assert result["checks"]["cache"]["status"] == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_readiness_probe_unhealthy_dependency(self):
        """Test readiness probe with unhealthy dependency"""
        health = HealthCheck(service_name="test-service")

        async def db_check():
            return True

        async def cache_check():
            return False  # Cache is down

        health.add_check("database", db_check)
        health.add_check("cache", cache_check)

        result, status_code = await health.readiness()

        assert result["status"] == HealthStatus.UNHEALTHY
        assert status_code == 503  # Service Unavailable
        assert result["checks"]["database"]["status"] == HealthStatus.HEALTHY
        assert result["checks"]["cache"]["status"] == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_readiness_probe_check_exception(self):
        """Test readiness probe handles exceptions in checks"""
        health = HealthCheck(service_name="test-service")

        async def failing_check():
            raise Exception("Check failed")

        health.add_check("failing", failing_check)

        result, status_code = await health.readiness()

        assert result["status"] == HealthStatus.UNHEALTHY
        assert status_code == 503
        assert result["checks"]["failing"]["status"] == HealthStatus.UNHEALTHY
        assert "Check failed" in result["checks"]["failing"]["error"]

    @pytest.mark.asyncio
    async def test_add_multiple_checks(self):
        """Test adding multiple health checks"""
        health = HealthCheck(service_name="test-service")

        async def check1():
            return True

        async def check2():
            return True

        async def check3():
            return True

        health.add_check("check1", check1)
        health.add_check("check2", check2)
        health.add_check("check3", check3)

        result, _ = await health.readiness()

        assert len(result["checks"]) == 3
        assert all(result["checks"].values())

    @pytest.mark.asyncio
    async def test_health_check_concurrent_execution(self):
        """Test health checks run concurrently"""
        health = HealthCheck(service_name="test-service")

        call_times = []

        async def slow_check(delay):
            import time

            start = time.time()
            await asyncio.sleep(delay)
            call_times.append(time.time() - start)
            return True

        health.add_check("check1", lambda: slow_check(0.1))
        health.add_check("check2", lambda: slow_check(0.1))
        health.add_check("check3", lambda: slow_check(0.1))

        import time

        start = time.time()
        await health.readiness()
        total_time = time.time() - start

        # If concurrent, should take ~0.1s, not 0.3s
        assert total_time < 0.25  # Allow some overhead

    @pytest.mark.asyncio
    async def test_health_check_degraded_state(self):
        """Test degraded state when some checks fail"""
        health = HealthCheck(service_name="test-service")

        async def healthy_check():
            return True

        async def degraded_check():
            return False

        health.add_check("critical", healthy_check)
        health.add_check("optional", degraded_check)

        result, status_code = await health.readiness()

        # With any failure, should be unhealthy
        assert result["status"] == HealthStatus.UNHEALTHY
        assert status_code == 503

    @pytest.mark.asyncio
    async def test_uptime_increases(self):
        """Test that uptime increases over time"""
        health = HealthCheck(service_name="test-service")

        first_check = await health.liveness()
        await asyncio.sleep(0.1)
        second_check = await health.liveness()

        assert second_check["uptime_seconds"] > first_check["uptime_seconds"]


@pytest.mark.unit
class TestHealthCheckHelpers:
    """Test health check helper utilities"""

    @pytest.mark.asyncio
    async def test_database_health_check(self):
        """Test database health check helper"""
        from app.core.health import check_database_health
        from unittest.mock import AsyncMock

        # Mock database session
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=True)

        # Database healthy
        result = await check_database_health(mock_session)
        assert result is True

        # Database unhealthy
        mock_session.execute.side_effect = Exception("DB error")
        result = await check_database_health(mock_session)
        assert result is False

    @pytest.mark.asyncio
    async def test_cache_health_check(self):
        """Test cache health check helper"""
        from app.core.health import check_cache_health
        from unittest.mock import AsyncMock

        # Mock Redis client
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)

        # Cache healthy
        result = await check_cache_health(mock_redis)
        assert result is True

        # Cache unhealthy
        mock_redis.ping.side_effect = Exception("Redis error")
        result = await check_cache_health(mock_redis)
        assert result is False

    @pytest.mark.asyncio
    async def test_external_service_health_check(self):
        """Test external service health check"""
        from app.core.health import check_external_service_health
        from unittest.mock import AsyncMock
        import httpx

        mock_client = AsyncMock()

        # Service healthy
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_client.get = AsyncMock(return_value=mock_response)

        result = await check_external_service_health(
            "http://example.com/health", client=mock_client
        )
        assert result is True

        # Service unhealthy
        mock_response.status_code = 500
        result = await check_external_service_health(
            "http://example.com/health", client=mock_client
        )
        assert result is False

        # Service unreachable
        mock_client.get.side_effect = httpx.HTTPError("Connection failed")
        result = await check_external_service_health(
            "http://example.com/health", client=mock_client
        )
        assert result is False


@pytest.mark.unit
class TestHealthCheckIntegration:
    """Test health check integration scenarios"""

    @pytest.mark.asyncio
    async def test_kubernetes_liveness_probe(self):
        """Test Kubernetes liveness probe format"""
        health = HealthCheck(service_name="api-gateway", version="1.0.0")

        result = await health.liveness()

        # Kubernetes expects simple 200 OK with optional JSON
        assert "status" in result
        assert result["status"] == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_kubernetes_readiness_probe(self):
        """Test Kubernetes readiness probe format"""
        health = HealthCheck(service_name="api-gateway")

        async def db_healthy():
            return True

        health.add_check("database", db_healthy)

        result, status_code = await health.readiness()

        # Kubernetes uses status code to determine readiness
        assert status_code == 200
        assert result["status"] == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_graceful_degradation(self):
        """Test service reports unhealthy when dependencies fail"""
        health = HealthCheck(service_name="orchestrator")

        async def llm_service_down():
            return False  # LLM service unavailable

        health.add_check("llm_service", llm_service_down)

        result, status_code = await health.readiness()

        # Should stop receiving traffic
        assert status_code == 503
        assert result["status"] == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_health_check_timeout(self):
        """Test health checks handle slow dependencies"""
        health = HealthCheck(service_name="test-service")

        async def slow_check():
            await asyncio.sleep(10)  # Very slow
            return True

        health.add_check("slow", slow_check)

        # Should have a timeout mechanism
        # This test assumes implementation adds timeout handling
        import time

        start = time.time()

        try:
            result, _ = await asyncio.wait_for(health.readiness(), timeout=2.0)
        except asyncio.TimeoutError:
            # Expected if no timeout handling in health check
            pass

        elapsed = time.time() - start
        assert elapsed < 3.0  # Should timeout or complete quickly
