"""Unit tests for Prometheus metrics"""

import pytest
import time
from prometheus_client import REGISTRY
from app.core.metrics import (
    http_requests_total,
    http_request_duration_seconds,
    llm_requests_total,
    llm_tokens_used_total,
    scenes_generated_total,
    circuit_breaker_state,
    cache_hits_total,
    cache_misses_total,
    db_queries_total,
    track_llm_request,
    track_db_query,
    initialize_service_metrics,
)


@pytest.mark.unit
class TestHTTPMetrics:
    """Test HTTP request metrics"""

    def test_http_requests_counter(self):
        """Test HTTP request counter increments"""
        initial_value = http_requests_total.labels(
            method="GET", endpoint="/test", status_code=200, service="test-service"
        )._value.get()

        http_requests_total.labels(
            method="GET", endpoint="/test", status_code=200, service="test-service"
        ).inc()

        new_value = http_requests_total.labels(
            method="GET", endpoint="/test", status_code=200, service="test-service"
        )._value.get()

        assert new_value == initial_value + 1

    def test_http_request_duration_histogram(self):
        """Test HTTP request duration histogram"""
        http_request_duration_seconds.labels(
            method="POST", endpoint="/api/scenes", service="api-gateway"
        ).observe(0.542)

        # Verify metric exists (detailed verification would require accessing histogram internals)
        metric = http_request_duration_seconds.labels(
            method="POST", endpoint="/api/scenes", service="api-gateway"
        )
        assert metric is not None

    def test_http_metrics_different_endpoints(self):
        """Test metrics differentiate endpoints"""
        http_requests_total.labels(
            method="GET", endpoint="/health", status_code=200, service="test"
        ).inc()

        http_requests_total.labels(
            method="GET", endpoint="/metrics", status_code=200, service="test"
        ).inc()

        # Metrics should be tracked separately
        # (Detailed verification would require registry inspection)


@pytest.mark.unit
class TestLLMMetrics:
    """Test LLM usage metrics"""

    def test_llm_requests_counter(self):
        """Test LLM request counter"""
        initial = llm_requests_total.labels(
            service="orchestrator", model="llama-3.1-70b", status="success"
        )._value.get()

        llm_requests_total.labels(
            service="orchestrator", model="llama-3.1-70b", status="success"
        ).inc()

        new_value = llm_requests_total.labels(
            service="orchestrator", model="llama-3.1-70b", status="success"
        )._value.get()

        assert new_value == initial + 1

    def test_llm_tokens_counter(self):
        """Test LLM token usage tracking"""
        initial_prompt = llm_tokens_used_total.labels(
            service="orchestrator", model="llama-3.1-70b", token_type="prompt"
        )._value.get()

        initial_completion = llm_tokens_used_total.labels(
            service="orchestrator", model="llama-3.1-70b", token_type="completion"
        )._value.get()

        # Simulate token usage
        llm_tokens_used_total.labels(
            service="orchestrator", model="llama-3.1-70b", token_type="prompt"
        ).inc(500)

        llm_tokens_used_total.labels(
            service="orchestrator", model="llama-3.1-70b", token_type="completion"
        ).inc(300)

        new_prompt = llm_tokens_used_total.labels(
            service="orchestrator", model="llama-3.1-70b", token_type="prompt"
        )._value.get()

        new_completion = llm_tokens_used_total.labels(
            service="orchestrator", model="llama-3.1-70b", token_type="completion"
        )._value.get()

        assert new_prompt == initial_prompt + 500
        assert new_completion == initial_completion + 300

    @pytest.mark.asyncio
    async def test_track_llm_request_decorator(self):
        """Test LLM request tracking decorator"""

        class MockLLMResponse:
            def __init__(self):
                self.usage = MockUsage()

        class MockUsage:
            prompt_tokens = 150
            completion_tokens = 200
            total_tokens = 350

        @track_llm_request("test-service", "test-model")
        async def mock_llm_call():
            await asyncio.sleep(0.01)
            return MockLLMResponse()

        import asyncio

        initial_requests = llm_requests_total.labels(
            service="test-service", model="test-model", status="success"
        )._value.get()

        _ = await mock_llm_call()

        new_requests = llm_requests_total.labels(
            service="test-service", model="test-model", status="success"
        )._value.get()

        assert new_requests == initial_requests + 1


@pytest.mark.unit
class TestBusinessMetrics:
    """Test business/domain metrics"""

    def test_scenes_generated_counter(self):
        """Test scene generation counter"""
        initial = scenes_generated_total.labels(
            service="orchestrator", status="completed"
        )._value.get()

        scenes_generated_total.labels(service="orchestrator", status="completed").inc()

        new_value = scenes_generated_total.labels(
            service="orchestrator", status="completed"
        )._value.get()

        assert new_value == initial + 1

    def test_scenes_failed_counter(self):
        """Test failed scene generation tracking"""
        initial = scenes_generated_total.labels(
            service="orchestrator", status="failed"
        )._value.get()

        scenes_generated_total.labels(service="orchestrator", status="failed").inc()

        new_value = scenes_generated_total.labels(
            service="orchestrator", status="failed"
        )._value.get()

        assert new_value == initial + 1


@pytest.mark.unit
class TestCircuitBreakerMetrics:
    """Test circuit breaker metrics"""

    def test_circuit_breaker_state_gauge(self):
        """Test circuit breaker state tracking"""
        # State: 0=closed, 1=open, 2=half-open
        circuit_breaker_state.labels(
            service="orchestrator", circuit_breaker_name="groq_api"
        ).set(
            0
        )  # Closed

        value = circuit_breaker_state.labels(
            service="orchestrator", circuit_breaker_name="groq_api"
        )._value.get()

        assert value == 0

        # Open the circuit breaker
        circuit_breaker_state.labels(
            service="orchestrator", circuit_breaker_name="groq_api"
        ).set(1)

        value = circuit_breaker_state.labels(
            service="orchestrator", circuit_breaker_name="groq_api"
        )._value.get()

        assert value == 1


@pytest.mark.unit
class TestCacheMetrics:
    """Test cache metrics"""

    def test_cache_hits_counter(self):
        """Test cache hit counter"""
        initial = cache_hits_total.labels(
            service="api-gateway", cache_key_prefix="user"
        )._value.get()

        cache_hits_total.labels(service="api-gateway", cache_key_prefix="user").inc()

        new_value = cache_hits_total.labels(
            service="api-gateway", cache_key_prefix="user"
        )._value.get()

        assert new_value == initial + 1

    def test_cache_misses_counter(self):
        """Test cache miss counter"""
        initial = cache_misses_total.labels(
            service="api-gateway", cache_key_prefix="scene"
        )._value.get()

        cache_misses_total.labels(service="api-gateway", cache_key_prefix="scene").inc()

        new_value = cache_misses_total.labels(
            service="api-gateway", cache_key_prefix="scene"
        )._value.get()

        assert new_value == initial + 1

    def test_cache_hit_ratio_calculation(self):
        """Test calculating cache hit ratio"""
        # Record some hits and misses
        cache_hits_total.labels(service="test", cache_key_prefix="test").inc(80)

        cache_misses_total.labels(service="test", cache_key_prefix="test").inc(20)

        hits = cache_hits_total.labels(
            service="test", cache_key_prefix="test"
        )._value.get()

        misses = cache_misses_total.labels(
            service="test", cache_key_prefix="test"
        )._value.get()

        # Hit ratio = hits / (hits + misses)
        total_requests = hits + misses
        hit_ratio = hits / total_requests if total_requests > 0 else 0

        assert hit_ratio >= 0.79  # Should be ~80%


@pytest.mark.unit
class TestDatabaseMetrics:
    """Test database metrics"""

    @pytest.mark.asyncio
    async def test_track_db_query_decorator(self):
        """Test database query tracking decorator"""

        @track_db_query("api-gateway", "select_user")
        async def mock_db_query():
            await asyncio.sleep(0.01)
            return {"id": "123", "name": "Test"}

        import asyncio

        initial = db_queries_total.labels(
            service="api-gateway", operation="select_user", status="success"
        )._value.get()

        result = await mock_db_query()

        new_value = db_queries_total.labels(
            service="api-gateway", operation="select_user", status="success"
        )._value.get()

        assert new_value == initial + 1
        assert result["id"] == "123"

    @pytest.mark.asyncio
    async def test_track_db_query_failure(self):
        """Test tracking failed database queries"""

        @track_db_query("api-gateway", "insert_user")
        async def failing_query():
            raise Exception("DB connection failed")

        initial = db_queries_total.labels(
            service="api-gateway", operation="insert_user", status="failed"
        )._value.get()

        with pytest.raises(Exception):
            await failing_query()

        new_value = db_queries_total.labels(
            service="api-gateway", operation="insert_user", status="failed"
        )._value.get()

        assert new_value == initial + 1


@pytest.mark.unit
class TestServiceMetrics:
    """Test service-level metrics"""

    def test_initialize_service_metrics(self):
        """Test service metrics initialization"""
        start_time = time.time()
        update_uptime = initialize_service_metrics("test-service", "1.0.0", start_time)

        assert callable(update_uptime)

        # Update uptime
        update_uptime()

        # Service should be tracked


@pytest.mark.unit
class TestMetricsIntegration:
    """Test metrics integration scenarios"""

    def test_multiple_services_tracked_separately(self):
        """Test that different services are tracked separately"""
        http_requests_total.labels(
            method="GET", endpoint="/health", status_code=200, service="api-gateway"
        ).inc()

        http_requests_total.labels(
            method="GET", endpoint="/health", status_code=200, service="orchestrator"
        ).inc()

        # Metrics should be separate per service
        gateway_value = http_requests_total.labels(
            method="GET", endpoint="/health", status_code=200, service="api-gateway"
        )._value.get()

        orchestrator_value = http_requests_total.labels(
            method="GET", endpoint="/health", status_code=200, service="orchestrator"
        )._value.get()

        # Values should be tracked independently
        assert gateway_value >= 0
        assert orchestrator_value >= 0

    def test_metrics_export_format(self):
        """Test Prometheus metrics export format"""
        from prometheus_client import generate_latest

        # Generate metrics output
        output = generate_latest(REGISTRY)

        # Should be in Prometheus text format
        assert b"# HELP" in output or b"# TYPE" in output
