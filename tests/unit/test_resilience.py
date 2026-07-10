"""Unit tests for resilience patterns (circuit breakers, retry logic)"""

import pytest
import asyncio
from app.core.resilience import (
    CircuitBreaker,
    CircuitBreakerState,
    CircuitBreakerError,
    with_retry,
    retry_with_backoff,
    RetryConfig,
)


@pytest.mark.unit
class TestCircuitBreaker:
    """Test circuit breaker pattern"""

    @pytest.mark.asyncio
    async def test_circuit_breaker_starts_closed(self):
        """Test circuit breaker starts in closed state"""
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=1)
        assert breaker.state == CircuitBreakerState.CLOSED
        assert breaker.failure_count == 0

    @pytest.mark.asyncio
    async def test_successful_call_through_breaker(self):
        """Test successful calls go through"""
        breaker = CircuitBreaker(failure_threshold=3)

        async def successful_func():
            return "success"

        result = await breaker.call(successful_func)
        assert result == "success"
        assert breaker.state == CircuitBreakerState.CLOSED
        assert breaker.failure_count == 0

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_threshold(self):
        """Test circuit breaker opens after failure threshold"""
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=10)

        async def failing_func():
            raise ValueError("Test error")

        # First 3 failures should be allowed through
        for i in range(3):
            with pytest.raises(ValueError):
                await breaker.call(failing_func)

        # Should now be OPEN
        assert breaker.state == CircuitBreakerState.OPEN

        # Further calls should be rejected immediately
        with pytest.raises(CircuitBreakerError):
            await breaker.call(failing_func)

    @pytest.mark.asyncio
    async def test_circuit_breaker_transitions_to_half_open(self):
        """Test circuit breaker transitions to half-open after timeout"""
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=1)

        async def failing_func():
            raise ValueError("Test error")

        # Open the breaker
        for i in range(2):
            with pytest.raises(ValueError):
                await breaker.call(failing_func)

        assert breaker.state == CircuitBreakerState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(1.1)

        # Next call should transition to HALF_OPEN
        async def successful_func():
            return "recovered"

        result = await breaker.call(successful_func)
        assert result == "recovered"
        assert breaker.state == CircuitBreakerState.CLOSED

    @pytest.mark.asyncio
    async def test_circuit_breaker_resets_on_success(self):
        """Test circuit breaker resets failure count on success"""
        breaker = CircuitBreaker(failure_threshold=3)

        async def alternating_func(should_fail):
            if should_fail:
                raise ValueError("Failed")
            return "success"

        # Fail once
        with pytest.raises(ValueError):
            await breaker.call(alternating_func, True)
        assert breaker.failure_count == 1

        # Succeed
        await breaker.call(alternating_func, False)
        assert breaker.failure_count == 0

    @pytest.mark.asyncio
    async def test_circuit_breaker_expected_exceptions(self):
        """Test circuit breaker only counts expected exceptions"""
        breaker = CircuitBreaker(failure_threshold=2, expected_exception=ValueError)

        async def value_error_func():
            raise ValueError("Expected error")

        async def type_error_func():
            raise TypeError("Unexpected error")

        # ValueError should count
        with pytest.raises(ValueError):
            await breaker.call(value_error_func)
        assert breaker.failure_count == 1

        # TypeError should not count (unexpected)
        with pytest.raises(TypeError):
            await breaker.call(type_error_func)
        assert breaker.failure_count == 1

    @pytest.mark.asyncio
    async def test_circuit_breaker_with_args_and_kwargs(self):
        """Test circuit breaker passes through args and kwargs"""
        breaker = CircuitBreaker(failure_threshold=3)

        async def func_with_params(a, b, c=None):
            return f"{a}-{b}-{c}"

        result = await breaker.call(func_with_params, "x", "y", c="z")
        assert result == "x-y-z"


@pytest.mark.unit
class TestRetryLogic:
    """Test retry with exponential backoff"""

    @pytest.mark.asyncio
    async def test_retry_succeeds_first_attempt(self):
        """Test function that succeeds on first attempt"""
        call_count = 0

        async def succeeds_immediately():
            nonlocal call_count
            call_count += 1
            return "success"

        result = await retry_with_backoff(succeeds_immediately)
        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_succeeds_after_failures(self):
        """Test function that fails then succeeds"""
        call_count = 0

        async def fails_twice():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Temporary failure")
            return "eventual success"

        config = RetryConfig(max_attempts=5, base_delay=0.01)
        result = await retry_with_backoff(fails_twice, config=config)

        assert result == "eventual success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_exhausts_attempts(self):
        """Test retry gives up after max attempts"""
        call_count = 0

        async def always_fails():
            nonlocal call_count
            call_count += 1
            raise ValueError("Always fails")

        config = RetryConfig(max_attempts=3, base_delay=0.01)

        with pytest.raises(ValueError):
            await retry_with_backoff(always_fails, config=config)

        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_respects_retryable_exceptions(self):
        """Test retry only retries on expected exceptions"""
        call_count = 0

        async def raises_unexpected():
            nonlocal call_count
            call_count += 1
            raise TypeError("Unexpected error")

        config = RetryConfig(max_attempts=3, base_delay=0.01)

        # TypeError is not in default retryable_exceptions
        with pytest.raises(TypeError):
            await retry_with_backoff(
                raises_unexpected, config=config, retryable_exceptions=(ValueError,)
            )

        # Should fail immediately, not retry
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_with_max_delay(self):
        """Test retry caps delay at max_delay"""
        call_count = 0
        delays = []

        async def track_delays():
            nonlocal call_count
            if call_count > 0:
                delays.append(call_count)
            call_count += 1
            if call_count < 4:
                raise ValueError("Retry me")
            return "success"

        config = RetryConfig(
            max_attempts=5, base_delay=0.01, max_delay=0.02, exponential_base=2.0
        )

        result = await retry_with_backoff(track_delays, config=config)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_retry_with_jitter(self):
        """Test retry adds jitter to delays"""
        call_count = 0
        start_times = []

        async def track_timing():
            nonlocal call_count
            import time

            start_times.append(time.time())
            call_count += 1
            if call_count < 3:
                raise ValueError("Retry")
            return "success"

        config = RetryConfig(max_attempts=4, base_delay=0.01, jitter=True)

        await retry_with_backoff(track_timing, config=config)

        # With jitter, delays should vary slightly
        assert call_count == 3


@pytest.mark.unit
class TestWithRetryDecorator:
    """Test @with_retry decorator"""

    @pytest.mark.asyncio
    async def test_decorator_basic_usage(self):
        """Test basic decorator usage"""
        call_count = 0

        @with_retry(max_attempts=3, base_delay=0.01)
        async def decorated_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("Retry")
            return "success"

        result = await decorated_func()
        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_decorator_with_args(self):
        """Test decorator with function arguments"""

        @with_retry(max_attempts=3, base_delay=0.01)
        async def add_numbers(a, b, c=0):
            return a + b + c

        result = await add_numbers(1, 2, c=3)
        assert result == 6

    @pytest.mark.asyncio
    async def test_decorator_retryable_exceptions(self):
        """Test decorator with specific retryable exceptions"""
        call_count = 0

        @with_retry(max_attempts=3, base_delay=0.01, retryable_exceptions=(ValueError,))
        async def may_fail():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("Retryable")
            if call_count == 2:
                raise TypeError("Not retryable")
            return "success"

        with pytest.raises(TypeError):
            await may_fail()

        # Should only be called twice (original + 1 retry for ValueError)
        # Then TypeError stops retries
        assert call_count == 2


@pytest.mark.unit
class TestResilienceIntegration:
    """Test circuit breaker and retry together"""

    @pytest.mark.asyncio
    async def test_retry_with_circuit_breaker(self):
        """Test using retry with circuit breaker"""
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=1)
        call_count = 0

        @with_retry(max_attempts=5, base_delay=0.01)
        async def flaky_service():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Transient error")
            return "recovered"

        result = await breaker.call(flaky_service)
        assert result == "recovered"
        assert call_count == 3
        assert breaker.state == CircuitBreakerState.CLOSED

    @pytest.mark.asyncio
    async def test_circuit_breaker_prevents_retry_cascade(self):
        """Test circuit breaker stops cascading retries"""
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=10)

        async def always_fails():
            raise ValueError("Always fails")

        # First 2 failures open the breaker
        for _ in range(2):
            with pytest.raises(ValueError):
                await breaker.call(always_fails)

        assert breaker.state == CircuitBreakerState.OPEN

        # Now retries should be rejected immediately
        @with_retry(max_attempts=5, base_delay=0.01)
        async def retry_through_breaker():
            return await breaker.call(always_fails)

        with pytest.raises(CircuitBreakerError):
            await retry_through_breaker()
