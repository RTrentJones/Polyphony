"""
Resilience utilities for external service calls

Provides circuit breakers, retry logic, and fallback mechanisms
for handling failures in distributed systems.
"""

import asyncio
from typing import Callable, Any, TypeVar, Optional
from functools import wraps
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

T = TypeVar('T')


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open"""
    pass


class CircuitBreakerState:
    """States for circuit breaker pattern"""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"      # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if recovered


class CircuitBreaker:
    """
    Circuit breaker pattern implementation

    Prevents cascading failures by detecting failures and stopping
    requests to failing services temporarily.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type = Exception,
        name: str = "circuit_breaker"
    ):
        """
        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before trying again
            expected_exception: Exception type that triggers circuit breaker
            name: Name for logging
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.name = name

        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.state = CircuitBreakerState.CLOSED

    def __call__(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """Decorator to wrap function with circuit breaker"""
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await self.call(func, *args, **kwargs)
        return wrapper

    async def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """Execute function with circuit breaker protection"""
        if self.state == CircuitBreakerState.OPEN:
            if self._should_attempt_reset():
                logger.info(f"Circuit breaker {self.name}: Attempting reset (half-open)")
                self.state = CircuitBreakerState.HALF_OPEN
            else:
                logger.warning(f"Circuit breaker {self.name}: OPEN - rejecting request")
                raise CircuitBreakerError(
                    f"Circuit breaker {self.name} is OPEN. "
                    f"Service is temporarily unavailable."
                )

        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception as e:
            self._on_failure()
            raise

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt recovery"""
        if self.last_failure_time is None:
            return True
        return (datetime.utcnow() - self.last_failure_time).total_seconds() >= self.recovery_timeout

    def _on_success(self):
        """Handle successful call"""
        if self.state == CircuitBreakerState.HALF_OPEN:
            logger.info(f"Circuit breaker {self.name}: Recovery successful - CLOSED")
        self.failure_count = 0
        self.state = CircuitBreakerState.CLOSED

    def _on_failure(self):
        """Handle failed call"""
        self.failure_count += 1
        self.last_failure_time = datetime.utcnow()

        if self.failure_count >= self.failure_threshold:
            logger.error(
                f"Circuit breaker {self.name}: Threshold reached "
                f"({self.failure_count} failures) - OPEN"
            )
            self.state = CircuitBreakerState.OPEN

    def reset(self):
        """Manually reset circuit breaker"""
        logger.info(f"Circuit breaker {self.name}: Manual reset")
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitBreakerState.CLOSED

    @property
    def is_open(self) -> bool:
        """Check if circuit breaker is open"""
        return self.state == CircuitBreakerState.OPEN


class RetryConfig:
    """Configuration for retry behavior"""

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True
    ):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter


async def retry_with_backoff(
    func: Callable[..., T],
    *args,
    config: Optional[RetryConfig] = None,
    retryable_exceptions: tuple = (Exception,),
    **kwargs
) -> T:
    """
    Retry function with exponential backoff

    Args:
        func: Async function to retry
        config: Retry configuration
        retryable_exceptions: Tuple of exceptions that trigger retry
        *args, **kwargs: Arguments to pass to func

    Returns:
        Result from func

    Raises:
        Last exception if all retries fail
    """
    if config is None:
        config = RetryConfig()

    last_exception = None

    for attempt in range(config.max_attempts):
        try:
            return await func(*args, **kwargs)
        except retryable_exceptions as e:
            last_exception = e

            if attempt == config.max_attempts - 1:
                # Last attempt failed
                logger.error(
                    f"All {config.max_attempts} retry attempts failed for {func.__name__}",
                    exc_info=True
                )
                raise

            # Calculate delay with exponential backoff
            delay = min(
                config.base_delay * (config.exponential_base ** attempt),
                config.max_delay
            )

            # Add jitter to prevent thundering herd
            if config.jitter:
                import random
                delay = delay * (0.5 + random.random())  # nosec B311 - Using random for jitter, not security

            logger.warning(
                f"Retry attempt {attempt + 1}/{config.max_attempts} "
                f"for {func.__name__} after {delay:.2f}s delay. Error: {str(e)}"
            )

            await asyncio.sleep(delay)

    # Should never reach here, but satisfy type checker
    raise last_exception


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    retryable_exceptions: tuple = (Exception,)
):
    """
    Decorator to add retry logic to async functions

    Example:
        @with_retry(max_attempts=3, base_delay=2.0)
        async def fetch_data():
            # Will retry up to 3 times with exponential backoff
            return await client.get("/data")
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            config = RetryConfig(max_attempts=max_attempts, base_delay=base_delay)
            return await retry_with_backoff(
                func,
                *args,
                config=config,
                retryable_exceptions=retryable_exceptions,
                **kwargs
            )
        return wrapper
    return decorator


# Timeout utilities
class TimeoutError(Exception):
    """Raised when operation exceeds timeout"""
    pass


async def with_timeout(coro, timeout: float, operation_name: str = "operation"):
    """
    Execute coroutine with timeout

    Args:
        coro: Coroutine to execute
        timeout: Timeout in seconds
        operation_name: Name for error messages

    Raises:
        TimeoutError: If operation exceeds timeout
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"{operation_name} exceeded timeout of {timeout}s"
        )


# Fallback utilities
def fallback_on_error(fallback_value: Any, exceptions: tuple = (Exception,)):
    """
    Decorator to return fallback value on error

    Example:
        @fallback_on_error(fallback_value=[], exceptions=(ValueError,))
        async def get_data():
            # Returns [] if ValueError is raised
            return await fetch_data()
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except exceptions as e:
                logger.warning(
                    f"Function {func.__name__} failed, using fallback value. Error: {str(e)}"
                )
                return fallback_value
        return wrapper
    return decorator
