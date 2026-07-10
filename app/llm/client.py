"""Paced, accounted LLM generation client.

The single entry point every feature uses: `get_llm_client().generate(...)`.
One `openai.AsyncOpenAI` client per provider (parameterized by base_url),
wrapped with the provider pacer, retry + circuit breaker, Prometheus counters,
and per-user usage rows in `api_usage`.
"""

import time
from typing import Optional
from uuid import UUID

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.core.metrics import (
    llm_cost_usd_total,
    llm_request_duration_seconds,
    llm_requests_total,
    llm_tokens_used_total,
)
from app.core.resilience import CircuitBreaker

from .pacing import backoff_after_429, get_pacer
from .providers import (
    GenResult,
    Provider,
    active_provider,
    cost_usd,
    provider_api_key,
    resolve_model,
)

logger = setup_logging("llm.client")

_MAX_ATTEMPTS = 4


class LLMConfigurationError(RuntimeError):
    """The selected provider has no API key configured."""


class LLMClient:
    """Provider-fungible chat-completion client."""

    def __init__(self, provider: Optional[Provider] = None):
        self.provider = provider or active_provider()
        api_key = provider_api_key(self.provider)
        if not api_key:
            raise LLMConfigurationError(
                f"LLM provider '{self.provider.id}' selected but "
                f"{self.provider.env_key} is not set"
            )
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=self.provider.base_url,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_retries=0,  # retries are ours: they must go through the pacer
        )
        self._breaker = CircuitBreaker(
            failure_threshold=4,
            recovery_timeout=45,
            expected_exception=Exception,
            name=f"llm_{self.provider.id}",
        )

    async def generate(
        self,
        messages: list[dict],
        *,
        fast: bool = False,
        model: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        user_id: Optional[UUID] = None,
        purpose: str = "",
    ) -> GenResult:
        """Generate a chat completion through pacing/retry/accounting."""
        resolved_model = model or resolve_model(self.provider, fast)
        start = time.monotonic()
        last_error: Optional[Exception] = None

        for attempt in range(_MAX_ATTEMPTS):
            try:
                async with get_pacer(self.provider):
                    response = await self._breaker.call(
                        self._client.chat.completions.create,
                        model=resolved_model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        extra_body=self.provider.extra_body,
                    )
                return await self._account(
                    response, resolved_model, start, user_id, purpose
                )
            except RateLimitError as e:
                last_error = e
                retry_after = _retry_after_seconds(e)
                logger.warning(
                    f"LLM 429 from {self.provider.id} (attempt {attempt + 1})",
                    extra_fields={
                        "event": "llm_rate_limited",
                        "provider": self.provider.id,
                        "model": resolved_model,
                        "retry_after": retry_after,
                    },
                )
                await backoff_after_429(retry_after, attempt)
            except (APIConnectionError, APITimeoutError, InternalServerError) as e:
                last_error = e
                await backoff_after_429(None, attempt)

        llm_requests_total.labels(
            service="app", model=resolved_model, status="error"
        ).inc()
        raise last_error if last_error else RuntimeError("LLM generation failed")

    async def _account(
        self,
        response,
        model: str,
        start: float,
        user_id: Optional[UUID],
        purpose: str,
    ) -> GenResult:
        latency_ms = int((time.monotonic() - start) * 1000)
        usage = getattr(response, "usage", None)
        tokens_in = getattr(usage, "prompt_tokens", 0) or 0
        tokens_out = getattr(usage, "completion_tokens", 0) or 0
        cost = cost_usd(self.provider, tokens_in, tokens_out)
        text = (response.choices[0].message.content or "").strip()

        llm_requests_total.labels(service="app", model=model, status="success").inc()
        llm_request_duration_seconds.labels(service="app", model=model).observe(
            latency_ms / 1000
        )
        llm_tokens_used_total.labels(  # nosec B106 - metric label
            service="app", model=model, token_type="prompt"
        ).inc(tokens_in)
        llm_tokens_used_total.labels(  # nosec B106 - metric label
            service="app", model=model, token_type="completion"
        ).inc(tokens_out)
        if cost:
            llm_cost_usd_total.labels(service="app", model=model).inc(cost)

        if user_id is not None:
            await self._record_usage(user_id, purpose, tokens_in + tokens_out, cost)

        return GenResult(
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            provider=self.provider.id,
            model=model,
            latency_ms=latency_ms,
            cost_usd=cost,
        )

    async def _record_usage(
        self, user_id: UUID, purpose: str, tokens: int, cost: float
    ) -> None:
        # Local import: keep the LLM layer importable without a DB configured.
        from app.core.database import get_async_session
        from app.core.orm_models import APIUsage

        try:
            async with get_async_session() as session:
                session.add(
                    APIUsage(
                        user_id=user_id,
                        endpoint=purpose or "llm",
                        tokens_used=tokens,
                        cost_usd=cost,
                    )
                )
        except Exception as e:
            # Accounting must never fail a generation.
            logger.warning(
                f"Failed to record LLM usage: {e}",
                extra_fields={"event": "llm_usage_record_failed"},
            )


def _retry_after_seconds(error: RateLimitError) -> Optional[float]:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or {}
    value = headers.get("retry-after") or headers.get("Retry-After")
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """Singleton client for the active provider."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def reset_llm_client() -> None:
    """Test hook."""
    global _client
    _client = None
