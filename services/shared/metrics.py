"""
Prometheus Metrics Configuration for Polyphony

Provides comprehensive metrics for monitoring application health,
performance, and business operations.
"""

from prometheus_client import Counter, Histogram, Gauge, Info
from typing import Callable
from functools import wraps
import time


# ============================================================================
# HTTP Metrics
# ============================================================================

http_requests_total = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status_code', 'service']
)

http_request_duration_seconds = Histogram(
    'http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['method', 'endpoint', 'service'],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0)
)

http_request_size_bytes = Histogram(
    'http_request_size_bytes',
    'HTTP request size in bytes',
    ['method', 'endpoint', 'service']
)

http_response_size_bytes = Histogram(
    'http_response_size_bytes',
    'HTTP response size in bytes',
    ['method', 'endpoint', 'service']
)


# ============================================================================
# Database Metrics
# ============================================================================

db_connections_active = Gauge(
    'db_connections_active',
    'Number of active database connections',
    ['service', 'pool']
)

db_connections_idle = Gauge(
    'db_connections_idle',
    'Number of idle database connections',
    ['service', 'pool']
)

db_query_duration_seconds = Histogram(
    'db_query_duration_seconds',
    'Database query duration in seconds',
    ['service', 'operation'],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
)

db_queries_total = Counter(
    'db_queries_total',
    'Total database queries',
    ['service', 'operation', 'status']
)


# ============================================================================
# Cache Metrics
# ============================================================================

cache_operations_total = Counter(
    'cache_operations_total',
    'Total cache operations',
    ['service', 'operation', 'status']
)

cache_hits_total = Counter(
    'cache_hits_total',
    'Total cache hits',
    ['service', 'cache_key_prefix']
)

cache_misses_total = Counter(
    'cache_misses_total',
    'Total cache misses',
    ['service', 'cache_key_prefix']
)

cache_operation_duration_seconds = Histogram(
    'cache_operation_duration_seconds',
    'Cache operation duration in seconds',
    ['service', 'operation'],
    buckets=(0.0001, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1)
)


# ============================================================================
# LLM/AI Metrics
# ============================================================================

llm_requests_total = Counter(
    'llm_requests_total',
    'Total LLM API requests',
    ['service', 'model', 'status']
)

llm_request_duration_seconds = Histogram(
    'llm_request_duration_seconds',
    'LLM API request duration in seconds',
    ['service', 'model'],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 20.0, 30.0, 60.0)
)

llm_tokens_used_total = Counter(
    'llm_tokens_used_total',
    'Total LLM tokens used',
    ['service', 'model', 'token_type']  # token_type: prompt, completion, total
)

llm_cost_usd_total = Counter(
    'llm_cost_usd_total',
    'Total estimated LLM cost in USD',
    ['service', 'model']
)


# ============================================================================
# Circuit Breaker Metrics
# ============================================================================

circuit_breaker_state = Gauge(
    'circuit_breaker_state',
    'Circuit breaker state (0=closed, 1=open, 2=half_open)',
    ['service', 'circuit_breaker_name']
)

circuit_breaker_failures_total = Counter(
    'circuit_breaker_failures_total',
    'Total circuit breaker failures',
    ['service', 'circuit_breaker_name']
)

circuit_breaker_successes_total = Counter(
    'circuit_breaker_successes_total',
    'Total circuit breaker successes',
    ['service', 'circuit_breaker_name']
)

circuit_breaker_rejections_total = Counter(
    'circuit_breaker_rejections_total',
    'Total circuit breaker rejections (when open)',
    ['service', 'circuit_breaker_name']
)


# ============================================================================
# Business Metrics (Polyphony-specific)
# ============================================================================

scenes_generated_total = Counter(
    'scenes_generated_total',
    'Total scenes generated',
    ['service', 'status']  # status: completed, failed
)

scene_generation_duration_seconds = Histogram(
    'scene_generation_duration_seconds',
    'Scene generation duration in seconds',
    ['service'],
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0)
)

scene_word_count = Histogram(
    'scene_word_count',
    'Word count of generated scenes',
    ['service'],
    buckets=(50, 100, 250, 500, 750, 1000, 1500, 2000, 3000, 5000)
)

scene_beats_count = Histogram(
    'scene_beats_count',
    'Number of beats in generated scenes',
    ['service'],
    buckets=(1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
)

dialogue_turns_total = Counter(
    'dialogue_turns_total',
    'Total dialogue turns generated',
    ['service', 'character']
)

manuscripts_created_total = Counter(
    'manuscripts_created_total',
    'Total manuscripts created',
    ['service']
)

characters_created_total = Counter(
    'characters_created_total',
    'Total characters created',
    ['service']
)


# ============================================================================
# User Metrics
# ============================================================================

users_registered_total = Counter(
    'users_registered_total',
    'Total users registered',
    ['service']
)

user_logins_total = Counter(
    'user_logins_total',
    'Total user login attempts',
    ['service', 'status']  # status: success, failed
)

active_users = Gauge(
    'active_users',
    'Number of currently active users',
    ['service']
)


# ============================================================================
# System Metrics
# ============================================================================

service_info = Info(
    'service',
    'Service information'
)

service_uptime_seconds = Gauge(
    'service_uptime_seconds',
    'Service uptime in seconds',
    ['service']
)

background_tasks_active = Gauge(
    'background_tasks_active',
    'Number of active background tasks',
    ['service', 'task_type']
)

background_tasks_completed_total = Counter(
    'background_tasks_completed_total',
    'Total completed background tasks',
    ['service', 'task_type', 'status']
)


# ============================================================================
# Rate Limiting Metrics
# ============================================================================

rate_limit_exceeded_total = Counter(
    'rate_limit_exceeded_total',
    'Total rate limit exceeded events',
    ['service', 'endpoint', 'user_id']
)


# ============================================================================
# Utility Functions
# ============================================================================

def track_request_metrics(service_name: str):
    """
    Decorator to automatically track HTTP request metrics

    Usage:
        @track_request_metrics("api-gateway")
        async def my_endpoint():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            status_code = 500

            try:
                result = await func(*args, **kwargs)
                status_code = getattr(result, 'status_code', 200)
                return result
            finally:
                duration = time.time() - start_time

                # Get endpoint path from function name or kwargs
                endpoint = func.__name__
                method = "GET"  # Default, should be extracted from request

                http_requests_total.labels(
                    method=method,
                    endpoint=endpoint,
                    status_code=status_code,
                    service=service_name
                ).inc()

                http_request_duration_seconds.labels(
                    method=method,
                    endpoint=endpoint,
                    service=service_name
                ).observe(duration)

        return wrapper
    return decorator


def track_llm_request(service_name: str, model: str):
    """
    Decorator to track LLM API requests

    Usage:
        @track_llm_request("orchestrator", "llama-3.1-70b")
        async def call_llm():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            status = "success"

            try:
                result = await func(*args, **kwargs)

                # Extract token usage if available
                if hasattr(result, 'usage'):
                    usage = result.usage
                    llm_tokens_used_total.labels(
                        service=service_name,
                        model=model,
                        token_type="prompt"
                    ).inc(getattr(usage, 'prompt_tokens', 0))

                    llm_tokens_used_total.labels(
                        service=service_name,
                        model=model,
                        token_type="completion"
                    ).inc(getattr(usage, 'completion_tokens', 0))

                    llm_tokens_used_total.labels(
                        service=service_name,
                        model=model,
                        token_type="total"
                    ).inc(getattr(usage, 'total_tokens', 0))

                return result
            except Exception as e:
                status = "failed"
                raise
            finally:
                duration = time.time() - start_time

                llm_requests_total.labels(
                    service=service_name,
                    model=model,
                    status=status
                ).inc()

                llm_request_duration_seconds.labels(
                    service=service_name,
                    model=model
                ).observe(duration)

        return wrapper
    return decorator


def track_db_query(service_name: str, operation: str):
    """
    Decorator to track database queries

    Usage:
        @track_db_query("api-gateway", "select_user")
        async def get_user(user_id: str):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            status = "success"

            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                status = "failed"
                raise
            finally:
                duration = time.time() - start_time

                db_queries_total.labels(
                    service=service_name,
                    operation=operation,
                    status=status
                ).inc()

                db_query_duration_seconds.labels(
                    service=service_name,
                    operation=operation
                ).observe(duration)

        return wrapper
    return decorator


def track_cache_operation(service_name: str, operation: str):
    """
    Decorator to track cache operations

    Usage:
        @track_cache_operation("api-gateway", "get")
        async def get_from_cache(key: str):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            status = "success"

            try:
                result = await func(*args, **kwargs)

                # Track hits/misses for get operations
                if operation == "get":
                    key_prefix = args[0].split(':')[0] if args else "unknown"
                    if result is not None:
                        cache_hits_total.labels(
                            service=service_name,
                            cache_key_prefix=key_prefix
                        ).inc()
                    else:
                        cache_misses_total.labels(
                            service=service_name,
                            cache_key_prefix=key_prefix
                        ).inc()

                return result
            except Exception as e:
                status = "failed"
                raise
            finally:
                duration = time.time() - start_time

                cache_operations_total.labels(
                    service=service_name,
                    operation=operation,
                    status=status
                ).inc()

                cache_operation_duration_seconds.labels(
                    service=service_name,
                    operation=operation
                ).observe(duration)

        return wrapper
    return decorator


def initialize_service_metrics(service_name: str, version: str, start_time: float):
    """
    Initialize service-level metrics

    Args:
        service_name: Name of the service
        version: Service version
        start_time: Service start timestamp
    """
    service_info.info({
        'service': service_name,
        'version': version
    })

    # Update uptime gauge (should be called periodically)
    def update_uptime():
        uptime = time.time() - start_time
        service_uptime_seconds.labels(service=service_name).set(uptime)

    return update_uptime
