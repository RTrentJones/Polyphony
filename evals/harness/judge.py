"""Pluggable LLM judge for the rubric-scored evals (voice fidelity, outline
coherence, prose quality).

Grades with a provider chosen by config (default Gemini today; swap to Anthropic
etc. once that key exists) — ideally NOT the model under test. Returns a 0..1
score + explanation, parsed robustly. Reuses the app's own LLMClient so the
call goes through the same pacing/retry/breaker as production.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from evals.harness.config import EvalConfig

_SYSTEM = (
    "You are a strict, fair literary evaluator. Score the response against the "
    "rubric on a scale from 0.0 (fails the rubric entirely) to 1.0 (fully meets "
    'it). Reply ONLY with JSON: {"score": <float 0..1>, "explanation": <one sentence>}.'
)


def _daily_quota(e: Exception) -> bool:
    """A QuotaExhaustedError for a spent DAILY cap: the ledger's own, or a 429
    naming a per-day limit (Groq "tokens per day", OpenRouter "free-models-per-
    day"). It won't return this run; a per-minute one keeps its retries."""
    from app.llm.errors import DailyRequestLimitError

    pattern = r"(tokens|requests|models)[ -]per[ -]day"
    return isinstance(e, DailyRequestLimitError) or bool(re.search(pattern, str(e)))


def _fast_if_claude(provider_id: str) -> str | None:
    """Claude judges with its fast model (Sonnet), never the Opus default."""
    from app.llm.providers import PROVIDERS

    return PROVIDERS["anthropic"].fast_model if provider_id == "anthropic" else None


class JudgeUnavailable(RuntimeError):
    """Every judge in the chain failed for good (e.g. each vendor retired its
    model). Steps record it and still report their deterministic metrics."""


@dataclass
class Judgment:
    score: float
    explanation: str
    judge_provider: str
    judge_model: str


def _clamp01(x) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


def _parse(text: str) -> tuple[float, str]:
    """Extract {score, explanation} from a judge reply; tolerant of fences/prose."""
    t = (text or "").strip().replace("```json", "").replace("```", "")
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(t[start : end + 1])
            return _clamp01(obj.get("score")), str(obj.get("explanation", ""))[:300]
        except json.JSONDecodeError:
            pass
    # last resort: pull the first float that looks like a score.
    m = re.search(r'score"?\s*[:=]\s*([01](?:\.\d+)?)', t)
    if m:
        return _clamp01(m.group(1)), "recovered score from unstructured reply"
    return 0.0, "unparseable judge reply"


class Judge:
    def __init__(self, cfg: EvalConfig):
        self._cfg = cfg
        self._client = None
        # Who actually produced a judgment (None until one succeeds), and
        # whether the whole chain gave out — the run report records these
        # rather than whichever provider was merely tried last.
        # Every provider that produced a judgment, in order (a mid-run failover
        # can leave earlier steps graded by one judge and later ones by another).
        self.graded_by: list[str] = []
        self.graded_is_self = False
        self.exhausted = False
        self.cost_usd = 0.0  # summed over graded calls: the report's judge_cost_usd
        # A judge at its own URL (EVAL_JUDGE_BASE_URL: a separate relay queue
        # answered by a separate agent) bypasses the provider registry.
        self.endpoint = cfg.judge_base_url
        if self.endpoint:
            self.provider_id = cfg.judge_label or "relay-judge"
            self.fell_back = False
            # The relay ignores the name; a priced-free id passes a ceiling (W12-1).
            self.model_override = cfg.judge_model or "stub-default"
            return
        # Resolved eagerly so the report records the judge that will ACTUALLY
        # grade (post-fallback), not just the requested one.
        self.provider_id, self.fell_back = self._resolve()
        primary = self._cfg.judge_provider.split(",")[0].strip()
        # An explicit EVAL_JUDGE_MODEL names a model for the PRIMARY chain
        # provider only — applying it to a different vendor would 404.
        self.model_override = (
            self._cfg.judge_model
            if (self.provider_id == primary and not self.fell_back)
            else None
        ) or _fast_if_claude(self.provider_id)

    def _resolve(self) -> tuple[str, bool]:
        """Pick the judge provider fail-soft. EVAL_JUDGE_PROVIDER may be a
        comma-separated preference chain ("groq,openrouter"): the first
        provider whose key is set grades. If none in the chain has a key, fall
        back to the app's own provider (self-grading beats no grading — CI
        stays green while judge keys haven't been minted yet)."""
        from app.llm.providers import PROVIDERS, provider_api_key

        chain = [p.strip() for p in self._cfg.judge_provider.split(",") if p.strip()]
        if not chain:
            raise RuntimeError("empty judge provider")
        for name in chain:  # typo protection before any key check
            if PROVIDERS.get(name) is None:
                raise RuntimeError(f"unknown judge provider {name!r}")
        for i, name in enumerate(chain):
            if provider_api_key(PROVIDERS[name]):
                if i > 0:
                    print(
                        f"eval judge: {chain[0]!r} has no key — using the next "
                        f"provider in the chain, {name!r}"
                    )
                # fell_back stays False within the chain: any chain member is a
                # deliberate, non-self judge choice.
                return name, False
        fallback = self._cfg.app_provider
        if fallback not in chain and PROVIDERS.get(fallback):
            print(
                f"eval judge: no key set for any of {chain} — falling back to "
                f"the app provider {fallback!r} (self-grading; scores may show "
                "self-preference bias)"
            )
            return fallback, True
        return chain[0], False

    @property
    def is_self(self) -> bool:
        """True when the EFFECTIVE judge is the model under test's provider."""
        if self.endpoint:
            return self._cfg.judge_is_self
        # ANTHROPIC_USERS routes the eval's generation to Claude (claude=yes).
        claude_gen = self.provider_id == "anthropic" and os.getenv("ANTHROPIC_USERS")
        return self.provider_id == self._cfg.app_provider or bool(claude_gen)

    def _endpoint_provider(self):
        """An OpenAI-compatible provider row for EVAL_JUDGE_BASE_URL. A relay
        takes any key, so EVAL_JUDGE_API_KEY defaults to a placeholder."""
        import dataclasses
        import os

        from app.llm.providers import PROVIDERS

        if not os.getenv("EVAL_JUDGE_API_KEY"):
            os.environ["EVAL_JUDGE_API_KEY"] = "relay"
        return dataclasses.replace(
            PROVIDERS["stub"],
            id=self.provider_id,
            label=f"Eval judge ({self.endpoint})",
            env_key="EVAL_JUDGE_API_KEY",
            base_url=self.endpoint,
            default_model=self.model_override,
            fast_model=self.model_override,
        )

    def _ensure(self):
        if self._client is None and self.endpoint:
            from app.llm.client import LLMClient

            self._client = LLMClient(self._endpoint_provider())
        if self._client is None:
            from app.llm.client import LLMClient
            from app.llm.providers import PROVIDERS

            # raises LLMConfigurationError if the provider's key is absent.
            self._client = LLMClient(PROVIDERS[self.provider_id])
        return self._client

    def _next_in_chain(self) -> bool:
        """Move to the next chain provider with a key (after the current one
        failed for good, e.g. its model was retired). False when none is left."""
        from app.llm.providers import PROVIDERS, provider_api_key

        chain = [p.strip() for p in self._cfg.judge_provider.split(",") if p.strip()]
        if self.provider_id not in chain:
            return False
        for name in chain[chain.index(self.provider_id) + 1 :]:
            if provider_api_key(PROVIDERS[name]):
                print(f"eval judge: {self.provider_id!r} failed — using {name!r}")
                self.provider_id, self._client = name, None
                self.model_override = _fast_if_claude(name)
                return True
        return False

    async def score(self, rubric: str, content: str) -> Judgment:
        prompt = f"RUBRIC:\n{rubric}\n\nRESPONSE TO SCORE:\n{content}\n\nJSON:"
        return await self._chained(self._score_once, prompt)

    async def ask_json(self, prompt: str) -> dict:
        """A plain JSON answer through the same chain, {} when unparseable (W16-1)."""
        return await self._chained(self._json_once, prompt)

    async def _chained(self, once, prompt: str):
        from openai import APIStatusError, NotFoundError

        from app.llm.errors import QuotaExhaustedError

        while True:
            try:
                judgment = await once(prompt)
                if self.provider_id not in self.graded_by:
                    self.graded_by.append(self.provider_id)
                self.graded_is_self = self.graded_is_self or self.is_self
                return judgment
            except (NotFoundError, APIStatusError, QuotaExhaustedError) as e:
                # A vendor retired the judge model (a 404), or this one request
                # is larger than its whole per-minute limit (a 413: Groq's free
                # tier refuses a long outline). Neither is transient: grade
                # with the next provider in the chain instead of failing the
                # whole step it belongs to. So is a spent daily cap (D14).
                if isinstance(e, QuotaExhaustedError) and not _daily_quota(e):
                    raise
                if isinstance(e, APIStatusError) and e.status_code not in (404, 413):
                    raise
                if not self._next_in_chain():
                    self.exhausted = True
                    raise JudgeUnavailable(
                        f"no judge in the chain is available: {e}"
                    ) from e

    async def _json_once(self, prompt: str) -> dict:
        t = (await self._generate(prompt, "Reply ONLY with JSON.")).text or ""
        try:
            obj = json.loads(t[t.find("{") : t.rfind("}") + 1])
        except json.JSONDecodeError:
            return {}
        return obj if isinstance(obj, dict) else {}

    async def _score_once(self, prompt: str) -> Judgment:
        result = await self._generate(prompt, _SYSTEM)
        score, explanation = _parse(result.text)
        return Judgment(
            score=score,
            explanation=explanation,
            judge_provider=self.provider_id,
            judge_model=result.model,
        )

    async def _generate(self, prompt: str, system: str):
        client = self._ensure()
        result = await client.generate(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            # Headroom for reasoning judges (gpt-oss), whose reasoning counts
            # against max_tokens; the JSON reply itself is short.
            max_tokens=1024,
            purpose="eval_judge",
            model=self.model_override,
            # Structured-output mode: llama judges sometimes ignore the
            # reply-only-JSON instruction (outline scores were regex-recovered
            # from prose, losing the explanation). All registry vendors'
            # OpenAI-compat endpoints support json_object.
            response_format={"type": "json_object"},
        )
        self.cost_usd += getattr(result, "cost_usd", None) or 0.0
        return result
