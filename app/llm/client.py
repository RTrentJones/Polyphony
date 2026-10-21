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
    BadRequestError,
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
from app.core.resilience import CircuitBreaker, CircuitBreakerError

from .errors import (
    ProviderUnavailableError,
    QuotaExhaustedError,
    UserBudgetExhaustedError,
)
from . import spend
from .pacing import backoff_after_429, get_pacer
from .prices import cost_usd
from .providers import (
    GenResult,
    Provider,
    active_provider,
    provider_api_key,
    resolve_model,
)

logger = setup_logging("llm.client")

_MAX_ATTEMPTS = 4
# Transient provider failures: retried per call, then the job pauses (W14-8).
_TRANSIENT = (APIConnectionError, APITimeoutError, InternalServerError)


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
        # The breaker trips on evidence the PROVIDER is unhealthy (connection
        # errors, timeouts, 5xx). A quota 429 is not that — the provider is
        # healthy and saying "not now" — and neither is a request it rejected
        # on its merits (400, e.g. a safety block). Counting those (it used to:
        # expected_exception=Exception) meant four quota pauses opened the
        # breaker, after which jobs FAILED with CircuitBreakerError instead of
        # pausing — breaking "the free tier never loses work" (SPEC-03).
        self._breaker = CircuitBreaker(
            failure_threshold=4,
            recovery_timeout=45,
            expected_exception=Exception,
            name=f"llm_{self.provider.id}",
            ignore_exceptions=(QuotaExhaustedError, BadRequestError),
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
        response_format: Optional[dict] = None,
    ) -> GenResult:
        """Generate a chat completion through pacing/retry/accounting."""
        resolved_model = model or resolve_model(self.provider, fast)
        # Deterministic-eval override: pin temperature when configured so a
        # score delta is attributable to the change under test, not sampling.
        if settings.LLM_TEMPERATURE_OVERRIDE is not None:
            temperature = settings.LLM_TEMPERATURE_OVERRIDE
        start = time.monotonic()
        est_in = sum(len(str(m.get("content") or "")) for m in messages) // 4
        # Check the cap actually sent: reasoning models get headroom on top of
        # the caller's max_tokens (_thinking_guard), and it is spent too.
        _, sent_cap = _thinking_guard(self.provider, resolved_model, max_tokens)
        if user_id is not None:
            await _check_user_budget(user_id, est_in + sent_cap)
        hold = await spend.admit(resolved_model, est_in, sent_cap)
        result = None

        # The circuit breaker wraps the WHOLE retry sequence, not each attempt:
        # one logical generate() = one breaker success/failure. Wrapping each
        # attempt (breaker inside the loop) meant a single call's exhausted
        # retries logged _MAX_ATTEMPTS failures and — with failure_threshold ==
        # _MAX_ATTEMPTS — tripped the breaker in one unlucky call, blackholing
        # every later call for the recovery window (this is exactly what made
        # the eval's whole generation half cascade to CircuitBreakerError). Now
        # the breaker opens only on sustained failure across multiple calls,
        # while transient blips stay absorbed by the per-call retries.
        try:
            result = await self._breaker.call(
                self._generate_with_retries,
                messages,
                resolved_model,
                max_tokens,
                temperature,
                start,
                user_id,
                purpose,
                response_format,
            )
        except CircuitBreakerError as e:
            llm_requests_total.labels(
                service="app", model=resolved_model, status="circuit_open"
            ).inc()
            # An open breaker is a provider outage, not the author's error: a
            # job PAUSES until the recovery window (ProviderUnavailableError is
            # a QuotaExhaustedError) rather than failing and losing the work.
            raise ProviderUnavailableError(
                str(e),
                reset_after=max(5.0, self._breaker.seconds_until_retry()),
            ) from e
        except _TRANSIENT as e:  # outside the breaker, so it still counts these
            raise ProviderUnavailableError(str(e)) from e
        finally:
            if result is None:
                await spend.release(hold)  # no response: nothing was spent
        if await self._record_usage(user_id, purpose, result):  # every response
            await spend.release(hold)  # unrecorded, it stays held (SPEC-81)
        return result

    async def _generate_with_retries(
        self,
        messages: list[dict],
        resolved_model: str,
        max_tokens: int,
        temperature: float,
        start: float,
        user_id: Optional[UUID],
        purpose: str,
        response_format: Optional[dict] = None,
    ) -> GenResult:
        """The paced retry loop; a single logical generation. Raised failures
        here count as ONE breaker failure (see generate())."""
        from .ledger import reserve
        from .tier import effective_rpd

        # Only include response_format when requested — passing null breaks
        # some OpenAI-compatible vendors.
        extra_kwargs = {"response_format": response_format} if response_format else {}
        if purpose:
            # Names the call for whoever serves it: the eval relay files each
            # request under this purpose, which the prompt no longer has to
            # spell out (evals/relay_llm.py). Vendors ignore unknown headers.
            extra_kwargs["extra_headers"] = {"X-Polyphony-Purpose": purpose}
        extra_body, max_tokens = _thinking_guard(
            self.provider, resolved_model, max_tokens
        )
        rpd = effective_rpd(self.provider, resolved_model)
        last_error: Optional[Exception] = None
        waited = 0.0
        for attempt in range(_MAX_ATTEMPTS):
            try:
                if rpd:
                    # Before the call: a reached daily ceiling pauses the job
                    # until the vendor's day rolls over (SPEC-03).
                    await reserve(
                        self.provider.id,
                        resolved_model,
                        rpd,
                        self.provider.rpd_reset_tz,
                    )
                async with get_pacer(self.provider):
                    response = await self._client.chat.completions.create(
                        model=resolved_model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        extra_body=extra_body,
                        **extra_kwargs,
                    )
                return await self._account(response, resolved_model, start)
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
                remaining = settings.LLM_MAX_RETRY_WAIT_SECONDS - waited
                if remaining <= 0:
                    break
                waited += (
                    await backoff_after_429(retry_after, attempt, cap=remaining) or 0.0
                )
            except _TRANSIENT as e:
                last_error = e
                remaining = settings.LLM_MAX_RETRY_WAIT_SECONDS - waited
                if remaining <= 0:
                    break
                waited += await backoff_after_429(None, attempt, cap=remaining) or 0.0

        llm_requests_total.labels(
            service="app", model=resolved_model, status="error"
        ).inc()
        # Classify the give-up: a persistent 429 is QUOTA (the job may pause and
        # resume), distinct from a transient connection/timeout failure. This is
        # what lets the free tier never lose work (docs/BRD.md R7.2).
        if isinstance(last_error, RateLimitError):
            raise QuotaExhaustedError(
                str(last_error), reset_after=_retry_after_seconds(last_error)
            )
        raise last_error if last_error else RuntimeError("LLM generation failed")

    async def _account(
        self,
        response,
        model: str,
        start: float,
    ) -> GenResult:
        latency_ms = int((time.monotonic() - start) * 1000)
        usage = getattr(response, "usage", None)
        tokens_in = getattr(usage, "prompt_tokens", 0) or 0
        tokens_out = getattr(usage, "completion_tokens", 0) or 0
        cost = cost_usd(model, tokens_in, tokens_out)
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
        self, user_id: Optional[UUID], purpose: str, result: GenResult
    ) -> bool:
        # Local import: keep the LLM layer importable without a DB configured.
        from app.core.database import get_async_session
        from app.core.orm_models import APIUsage
        from app.jobs.context import current_job

        try:
            async with get_async_session() as session:
                session.add(
                    APIUsage(
                        user_id=user_id,
                        endpoint=purpose or "llm",
                        tokens_used=result.tokens_in + result.tokens_out,
                        cost_usd=result.cost_usd,
                        model=result.model,
                        job_id=current_job().job_id,
                    )
                )
            return True
        except Exception as e:
            # Accounting must never fail a generation.
            logger.warning(
                f"Failed to record LLM usage: {e}",
                extra_fields={"event": "llm_usage_record_failed"},
            )
            return False


# Groq's free tier limits gpt-oss to ~8K tokens per minute, counting the
# prompt and the requested cap; a larger cap is rejected outright.
_GROQ_FREE_MAX_OUTPUT = 6144
# Headroom for a Gemini flash model's minimal thinking (it cannot be turned off).
_GEMINI_MINIMAL_THINKING = 1024


def _thinking_guard(provider: Provider, model: str, max_tokens: int):
    """Gemini models think, and thinking tokens count against max_tokens, so
    a small cap starves the answer (eval-audit backlog item 6). Flash models
    take the provider's minimal effort plus a little headroom; a non-flash
    Gemini model (Pro) drops the knob and gets more."""
    extra = provider.extra_body
    if provider.id == "groq":
        if "gpt-oss" not in model.lower():
            # Groq rejects reasoning_effort on models that don't support it, so
            # an LLM_MODEL / EVAL_JUDGE_MODEL override must not carry it.
            extra = {k: v for k, v in (extra or {}).items() if k != "reasoning_effort"}
            return (extra or None), max_tokens
        # gpt-oss reasons even at low effort, and reasoning counts against
        # max_tokens: give it headroom so small caps still leave the answer.
        # On the free tier the request (prompt + cap) must also fit Groq's
        # per-minute token limit, so the cap is clamped below it.
        from .tier import get_tier

        cap = max_tokens + 1024
        if get_tier().name == "free":
            cap = min(cap, _GROQ_FREE_MAX_OUTPUT)
        return extra, cap
    if provider.id == "gemini":
        if "flash" in model.lower():
            return extra, max_tokens + _GEMINI_MINIMAL_THINKING
        extra = {k: v for k, v in (extra or {}).items() if k != "reasoning_effort"}
        return (extra or None), max_tokens + 4096
    return extra, max_tokens


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


async def _check_user_budget(user_id: UUID, needed: int) -> None:
    """Pause the caller's job when the author's rolling-24h token budget cannot
    cover this call — its prompt (estimated at ~4 chars/token) plus its answer
    cap — (raises UserBudgetExhaustedError).

    The enqueue preflights estimate a whole job, but a multi-call job (an
    extraction planned to span days, an outline confirmed hours after its
    skeleton) must not spend past the budget between pauses: checking before
    each call bounds any overshoot to one call. Fail-soft like the request
    ledger: an unreadable budget never blocks generation.
    """
    from app.core.budget import remaining_budget_24h
    from app.core.database import get_async_session

    try:
        async with get_async_session() as session:
            remaining = await remaining_budget_24h(session, user_id)
    except Exception:
        return
    if remaining < needed:
        raise UserBudgetExhaustedError(
            f"Today's token budget is spent ({remaining:,} left); this job "
            "resumes as the 24-hour window rolls forward.",
            reset_after=float(settings.QUOTA_PAUSE_SECONDS),
        )
